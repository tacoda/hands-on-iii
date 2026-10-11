import importlib.util
import os
import subprocess
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "workers" / "ladder" / "src"))
sys.path.insert(0, str(ROOT / "workers" / "factory" / "src"))


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    """An empty settings/ for one test, so the repo's own files never leak in."""
    monkeypatch.setenv("SETTINGS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def phases(settings_dir):
    """A small phases.yaml in the test's own settings/, so a test never reads the repo's file."""
    (settings_dir / "phases.yaml").write_text(
        "grants:\n  discovery: [engine::functions::list]\n"
        "phases:\n  plan: {}\n")
    return settings_dir / "phases.yaml"


@pytest.fixture
def fake_iii(tmp_path):
    """A stand-in `iii` on PATH that logs its argv, so no test ever reaches an engine."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    log = tmp_path / "iii.log"
    script = bin_dir / "iii"
    # It answers the two ladder questions turn.py asks before a send: the gate
    # is registered unless III_NO_LADDER is set, and ladder::list prints
    # III_LADDER_LIST. III_FAIL fails approval::set-mode only.
    script.write_text(
        '#!/bin/sh\necho "$@" >> "$III_LOG"\n'
        'case "$2" in\n'
        '  engine::functions::info)\n'
        '    if [ -n "$III_NO_LADDER" ]; then echo "function_not_found: ladder::gate" >&2; exit 1; fi\n'
        '    echo \'{"function_id": "ladder::gate", "worker_name": "ladder"}\' ;;\n'
        '  ladder::list)\n'
        '    if [ -n "$III_LADDER_LIST" ]; then printf "%s\\n" "$III_LADDER_LIST"\n'
        '    else echo \'{"rules": [], "withheld": []}\'; fi ;;\n'
        '  *)\n'
        '    if [ -n "$III_FAIL" ]; then echo "engine down" >&2; exit 1; fi ;;\n'
        'esac\n')
    script.chmod(0o755)
    return {"bin": bin_dir, "log": log}


@pytest.fixture(autouse=True)
def no_real_engine(request, fake_iii, monkeypatch, tmp_path):
    """turn.py asks the engine for ladder::gate before every send, so every test meets the fake.
    And a stage that writes a job record writes it here, never into the checkout's state/."""
    if request.node.get_closest_marker("live"):
        return  # a live test must reach the real engine, or the fake accepts its send
    monkeypatch.setenv("PATH", f"{fake_iii['bin']}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FACTORY_JOBS", str(tmp_path / "jobs"))
    monkeypatch.setenv("III_LOG", str(fake_iii["log"]))
    monkeypatch.setenv("LADDER_HOLDS", str(tmp_path / "holds"))
    for name in ("III_FAIL", "III_NO_LADDER", "III_LADDER_LIST"):
        monkeypatch.delenv(name, raising=False)


class FakeWorker:
    """Records what main.py registers, so the wiring is testable with no engine."""

    def __init__(self, address, options):
        self.address, self.options = address, options
        self.functions, self.triggers = {}, []
        self.calls, self.answers = [], {}

    def register_function(self, function_id, handler, **kwargs):
        self.functions[function_id] = handler

    def register_trigger(self, trigger):
        self.triggers.append(trigger)

    def trigger(self, request):
        # Every engine call a worker makes lands here; a test sets `answers`.
        self.calls.append((request["function_id"], request.get("payload")))
        answer = self.answers.get(request["function_id"])
        return answer(request.get("payload")) if callable(answer) else answer


@pytest.fixture
def ladder_main(monkeypatch):
    """workers/ladder/src/main.py, imported against a stand-in `iii` module.

    main.py registers at import, as chapter 7 prints it, so the real SDK would
    try to connect to an engine."""
    fake = types.ModuleType("iii")
    fake.InitOptions = lambda **kwargs: kwargs
    fake.register_worker = FakeWorker
    monkeypatch.setitem(sys.modules, "iii", fake)
    monkeypatch.setenv("III_URL", "ws://127.0.0.1:49134")
    path = ROOT / "workers" / "ladder" / "src" / "main.py"
    spec = importlib.util.spec_from_file_location("ladder_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def factory_main(monkeypatch, tmp_path):
    """workers/factory/src/main.py against a stand-in `iii`, with jobs in a temporary folder."""
    fake = types.ModuleType("iii")
    fake.InitOptions = lambda **kwargs: kwargs
    fake.register_worker = FakeWorker
    monkeypatch.setitem(sys.modules, "iii", fake)
    monkeypatch.setenv("III_URL", "ws://127.0.0.1:49134")
    monkeypatch.setenv("FACTORY_JOBS", str(tmp_path / "jobs"))
    path = ROOT / "workers" / "factory" / "src" / "main.py"
    spec = importlib.util.spec_from_file_location("factory_main", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.worker.answers.update({
        "engine::functions::info": {"function_id": "ladder::gate", "worker_name": "ladder"},
        "ladder::list": {"rules": [], "withheld": []},
        "approval::set-mode": {"ok": True},
        "harness::send": lambda p: {"session_id": p["session_id"], "turn_id": "t_1",
                                    "accepted": True},
    })
    return module


def pytest_configure(config):
    config.addinivalue_line("markers", "live: needs a running engine and sends paid turns")


def pytest_collection_modifyitems(config, items):
    # Opt in with `-m live`. Skipping only when no engine answers would send a
    # paid turn to any engine that happens to be up, including another project's.
    if "live" in (config.option.markexpr or ""):
        return
    skip = pytest.mark.skip(reason="live: sends a paid turn; run with -m live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip)


def git(cwd, *args) -> str:
    done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True)
    return done.stdout


class Shell:
    """shell::exec for real git in a temporary repo, with the remote mocked.

    `remote_head` is what ls-remote reports for the branch; `push_fails` makes
    push exit 1. Nothing here ever reaches a network."""

    def __init__(self):
        self.remote_head, self.push_fails, self.ran = "", False, []

    def __call__(self, payload):
        args = [payload["command"], *payload.get("args", [])]
        self.ran.append(args)
        if args[:2] == ["git", "ls-remote"]:
            line = f"{self.remote_head}\trefs/heads/x\n" if self.remote_head else ""
            return {"exit_code": 0, "stdout": line, "stderr": ""}
        if args[:2] == ["git", "push"]:
            if self.push_fails:
                return {"exit_code": 1, "stdout": "", "stderr": "fatal: could not read from remote"}
            return {"exit_code": 0, "stdout": "", "stderr": ""}
        done = subprocess.run(args, cwd=payload.get("cwd"), capture_output=True, text=True)
        return {"exit_code": done.returncode, "stdout": done.stdout, "stderr": done.stderr}


@pytest.fixture
def worker():
    """A bare stand-in worker for the stage actions, answering nothing until told."""
    return FakeWorker("ws://127.0.0.1:49134", {"worker_name": "test"})


@pytest.fixture
def git_repo(tmp_path):
    """A target repo with main at one commit and the job's branch checked out."""
    repo = tmp_path / "target"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.email", "factory@example.com")
    git(repo, "config", "user.name", "Factory Test")
    git(repo, "config", "commit.gpgsign", "false")
    (repo / "README.md").write_text("# target\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "start")
    git(repo, "checkout", "-q", "-b", "factory/document-the-ports-0123abcd")
    return repo
