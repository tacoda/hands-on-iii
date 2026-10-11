import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "workers" / "ladder" / "src"))


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
    """turn.py asks the engine for ladder::gate before every send, so every test meets the fake."""
    if request.node.get_closest_marker("live"):
        return  # a live test must reach the real engine, or the fake accepts its send
    monkeypatch.setenv("PATH", f"{fake_iii['bin']}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("III_LOG", str(fake_iii["log"]))
    monkeypatch.setenv("LADDER_HOLDS", str(tmp_path / "holds"))
    for name in ("III_FAIL", "III_NO_LADDER", "III_LADDER_LIST"):
        monkeypatch.delenv(name, raising=False)


class FakeWorker:
    """Records what main.py registers, so the wiring is testable with no engine."""

    def __init__(self, address, options):
        self.address, self.options = address, options
        self.functions, self.triggers = {}, []

    def register_function(self, function_id, handler, **kwargs):
        self.functions[function_id] = handler

    def register_trigger(self, trigger):
        self.triggers.append(trigger)


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
