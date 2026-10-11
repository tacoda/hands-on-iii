import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import turn
from turn import ASKS, oversight

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "turn.py"


@pytest.fixture(autouse=True)
def real_settings(monkeypatch):
    """The invariant must read the repo's own settings/oversight.yaml, the file the next turn uses."""
    monkeypatch.delenv("SETTINGS_DIR", raising=False)


def test_ask_never_becomes_allow():
    for level in ASKS:
        assert oversight(level)[1] in ("manual", "auto"), level


def test_no_level_and_a_typo_both_resolve_to_supervised():
    """A turn that dies on `oversight: paranoid` is worse than a supervised turn that says so."""
    assert oversight() == ("supervised", "full")
    assert oversight("paranoid") == ("supervised", "full")


def test_every_level_maps_to_a_mode_the_gate_knows():
    """approval::set-mode rejects an unknown mode, and the script would then run on the default."""
    for level in ("manual", "attended", "supervised", "dark"):
        assert oversight(level)[1] in ("manual", "auto", "full"), level
    assert oversight("manual") == ("manual", "manual")
    assert oversight("attended") == ("attended", "auto")
    assert oversight("dark") == ("dark", "full")


def test_a_deleted_settings_file_resolves_to_supervised(settings_dir):
    """Deleting oversight.yaml must give the documented default, not an error or `manual`."""
    assert oversight() == ("supervised", "full")
    assert oversight("attended") == ("supervised", "full")


def test_a_level_is_matched_without_case_or_spaces():
    """`--oversight Attended ` resolving to supervised would drop the person it asked for."""
    assert oversight(" Attended ") == ("attended", "auto")


def completed(code, stderr=""):
    return lambda *a, **k: subprocess.CompletedProcess(a, code, "", stderr)


def test_set_mode_sends_the_session_and_mode_to_approval_set_mode(monkeypatch, capsys):
    """A mode sent to the wrong function or session leaves the turn on the deployment default."""
    seen = []
    monkeypatch.setattr(turn.subprocess, "run",
                        lambda argv, **k: seen.append(argv) or subprocess.CompletedProcess(argv, 0))
    turn.set_mode("s_1", "attended", "auto")
    argv, = seen
    assert argv[:4] == ["iii", "trigger", "approval::set-mode", "--json"]
    assert json.loads(argv[4]) == {"session_id": "s_1", "mode": "auto"}
    assert "oversight: attended (auto) on s_1" in capsys.readouterr().err


@pytest.mark.parametrize("level", sorted(ASKS))
def test_a_failed_set_mode_at_a_level_that_asks_exits(monkeypatch, capsys, level):
    """Falling back to `full` would start a run nobody watched under a level that promised a person."""
    monkeypatch.setattr(turn.subprocess, "run", completed(1, "engine down"))
    with pytest.raises(SystemExit) as stopped:
        turn.set_mode("s_1", level, "auto")
    assert stopped.value.code == 1
    assert "oversight: set-mode failed: engine down" in capsys.readouterr().err


@pytest.mark.parametrize("level", ["supervised", "dark"])
def test_a_failed_set_mode_at_a_level_that_does_not_ask_goes_ahead(monkeypatch, capsys, level):
    """The deployment default is already `full`, so stopping here would block a turn for nothing."""
    monkeypatch.setattr(turn.subprocess, "run", completed(1, "engine down"))
    turn.set_mode("s_1", level, "full")
    assert "set-mode failed" in capsys.readouterr().err


def test_a_missing_iii_cli_counts_as_a_failed_set_mode(monkeypatch):
    """A traceback instead of the exit would print nothing useful and still look like a crash."""
    def missing(*a, **k):
        raise FileNotFoundError("iii")
    monkeypatch.setattr(turn.subprocess, "run", missing)
    with pytest.raises(SystemExit):
        turn.set_mode("s_1", "manual", "manual")


def script(fake_iii, *args, fail=False):
    env = {**os.environ, "PATH": f"{fake_iii['bin']}{os.pathsep}{os.environ['PATH']}",
           "III_LOG": str(fake_iii["log"])}
    env.pop("SETTINGS_DIR", None)
    env.pop("III_FAIL", None)
    if fail:
        env["III_FAIL"] = "1"
    return subprocess.run([sys.executable, str(SCRIPT), "--phase", "plan", "--prompt", "p", *args],
                          env=env, capture_output=True, text=True)


def test_the_send_carries_the_level_and_the_mode_is_set_for_its_session(fake_iii):
    """Chapter 7's ladder reads the level from metadata; a mode on another session guards nothing."""
    out = script(fake_iii, "--oversight", "attended")
    assert out.returncode == 0, out.stderr
    send = json.loads(out.stdout)
    assert send["options"]["metadata"]["oversight"] == "attended"
    logged = fake_iii["log"].read_text()
    assert logged.startswith("trigger approval::set-mode --json")
    assert json.loads(logged.split("--json ", 1)[1]) == {"session_id": send["session_id"],
                                                         "mode": "auto"}
    assert f"oversight: attended (auto) on {send['session_id']}" in out.stderr


def test_a_failed_set_mode_at_attended_prints_no_send(fake_iii):
    """A printed send would go straight into harness::send and run with nobody watching."""
    out = script(fake_iii, "--oversight", "attended", fail=True)
    assert out.returncode == 1
    assert out.stdout == ""
    assert "oversight: set-mode failed: engine down" in out.stderr


def test_a_failed_set_mode_at_supervised_still_prints_the_send(fake_iii):
    """The level is printed either way, so the operator sees what the turn ran on."""
    out = script(fake_iii, fail=True)
    assert out.returncode == 0
    assert json.loads(out.stdout)["options"]["metadata"]["oversight"] == "supervised"
    assert "oversight: supervised (full)" in out.stderr


CONFIG = Path(__file__).resolve().parents[1] / "config"


def test_the_gate_config_holds_only_keys_approval_gate_accepts():
    """approval-gate 1.0.14 rejects unknown keys (the old always_allow_seed and pending_timeout_ms)
    and aborts startup, and an object rule fails its schema so the gate falls back to `manual`."""
    import yaml
    value = yaml.safe_load((CONFIG / "approval-gate.yaml").read_text())["value"]
    assert set(value) <= {"default_mode", "rules", "grant_reask_limit"}, sorted(value)
    assert value["rules"] and all(isinstance(rule, str) for rule in value["rules"])


def test_the_harness_sweep_runs_every_minute():
    """The stock sweep runs once a day, so an unanswered hold would outlive its 30-minute deadline."""
    import yaml
    value = yaml.safe_load((CONFIG / "harness.yaml").read_text())["value"]
    assert value["sweep_expression"] == "0 * * * * *"
    assert value["default_pending_timeout_ms"] == 1800000
