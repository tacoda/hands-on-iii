import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

import turn

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "turn.py"
SESSION = re.compile(r"^s_([0-9a-f]{8})_([a-z]+)_1$")
ROOT = "/work/sample-repo"


@pytest.fixture(autouse=True)
def no_engine(fake_iii, monkeypatch):
    """turn.py calls `iii trigger approval::set-mode`, so every run here meets the fake."""
    monkeypatch.setenv("PATH", f"{fake_iii['bin']}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("III_LOG", str(fake_iii["log"]))
    monkeypatch.delenv("III_FAIL", raising=False)


def run(settings_dir, *args):
    env = {**os.environ, "SETTINGS_DIR": str(settings_dir)}
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                          capture_output=True, text=True)


def test_the_payload_has_the_shape_harness_send_takes(phases):
    """A misplaced key is ignored by the harness, so the turn runs on its own defaults."""
    got = turn.payload("say hello", "plan", ROOT)
    assert set(got) == {"session_id", "message", "provider", "model", "options"}
    assert got["message"] == "say hello"
    assert got["provider"] == "anthropic"
    assert got["model"] == "claude-sonnet-5"
    assert set(got["options"]) == {"max_turns", "thinking_level", "functions", "metadata"}
    assert got["options"]["max_turns"] == 50
    assert got["options"]["thinking_level"] == "medium"


def test_the_grant_is_explicit_even_when_the_phase_sets_none(phases):
    """An omitted or empty grant denies every call on a new session; the send states it anyway."""
    assert turn.payload("p", "plan", ROOT)["options"]["functions"] == {"allow": [], "deny": []}


def test_the_session_id_follows_the_job_phase_count_convention(phases):
    """The completion event carries no metadata, so the id must encode job, phase, and count."""
    got = turn.payload("p", "plan", ROOT)
    match = SESSION.match(got["session_id"])
    assert match, got["session_id"]
    job, phase = match.groups()
    assert got["options"]["metadata"]["job_id"] == job
    assert phase == "plan"


def test_every_call_gets_a_fresh_session(phases):
    """A fixed id inherits the last run's transcript and failed state, or folds into a running turn."""
    ids = {turn.payload("p", "plan", ROOT)["session_id"] for _ in range(50)}
    assert len(ids) == 50


def test_an_edit_to_settings_changes_the_next_payload(phases):
    """The script caches nothing, so an edit applies to the next turn with no restart."""
    assert turn.payload("p", "plan", ROOT)["options"]["max_turns"] == 50
    phases.write_text("defaults:\n  max_turns: 20\nphases:\n  plan: {}\n")
    assert turn.payload("p", "plan", ROOT)["options"]["max_turns"] == 20


def test_every_send_carries_its_filesystem_root_and_phase(phases):
    """Without metadata.fs_scope.root the turn is scoped to the harness worker's directory, this repo."""
    meta = turn.send_options("plan", ROOT)["options"]["metadata"]
    assert meta == {"phase": "plan", "fs_scope": {"root": ROOT}}


def test_an_empty_workspace_is_refused(phases):
    """A send with no root would run a run grant against the harness instead of the target."""
    with pytest.raises(ValueError, match="refusing to send without fs_scope.root"):
        turn.send_options("plan", "")


def test_provider_and_model_ride_at_the_top_level_not_in_options(settings_dir):
    """harness::send reads them at the top level; inside options they are ignored."""
    (settings_dir / "phases.yaml").write_text(
        "phases:\n  run: {provider: deepseek, model: deepseek-flash}\n")
    got = turn.send_options("run", ROOT)
    assert (got["provider"], got["model"]) == ("deepseek", "deepseek-flash")
    assert not {"provider", "model"} & set(got["options"])


def test_a_spend_ceiling_passes_through_only_when_the_phase_sets_it(settings_dir):
    """A null ceiling sent as a key would read to the harness worker as a value."""
    (settings_dir / "phases.yaml").write_text(
        "phases:\n  plan: {max_cost_usd: 1.00, max_total_tokens: 500000}\n  run: {}\n")
    assert turn.send_options("plan", ROOT)["options"]["max_cost_usd"] == 1.00
    assert turn.send_options("plan", ROOT)["options"]["max_total_tokens"] == 500000
    assert not {"max_cost_usd", "max_total_tokens"} & set(turn.send_options("run", ROOT)["options"])


def test_the_script_prints_one_json_object_and_nothing_else(phases):
    """Its stdout goes straight into `iii trigger --json "$(...)"`, so any extra text breaks the send."""
    out = run(phases.parent, "--phase", "plan", "--prompt", "say hello")
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["message"] == "say hello"


def test_the_script_sends_an_absolute_root_defaulting_to_the_sample_repo(phases):
    """The shell worker does not share this shell's directory, so a relative root names the wrong place."""
    out = run(phases.parent, "--phase", "plan", "--prompt", "p")
    root = json.loads(out.stdout)["options"]["metadata"]["fs_scope"]["root"]
    assert Path(root).is_absolute()
    assert root.endswith("tests/fixtures/sample-repo")


def test_an_unknown_phase_prints_no_payload_and_names_the_known_ones(phases):
    """`--phase reveiw` on the bare defaults would run a turn and look like it worked."""
    out = run(phases.parent, "--phase", "reveiw", "--prompt", "p")
    assert out.returncode != 0
    assert out.stdout == ""
    assert "unknown phase 'reveiw'; known: ['plan']" in out.stderr


def test_the_root_rides_only_in_metadata_never_as_options_fs_scope(phases):
    """The harness worker drops options.fs_scope silently, so a root there leaves the turn unscoped."""
    assert "fs_scope" not in turn.send_options("plan", ROOT)["options"]
