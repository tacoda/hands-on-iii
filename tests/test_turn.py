import json
import os
import re
import subprocess
import sys
from pathlib import Path

import turn

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "turn.py"
SESSION = re.compile(r"^s_([0-9a-f]{8})_([a-z]+)_1$")


def test_the_payload_has_the_shape_harness_send_takes(settings_dir):
    """A misplaced key is ignored by the harness, so the turn runs on its own defaults."""
    got = turn.payload("say hello", "chat")
    assert set(got) == {"session_id", "message", "provider", "model", "options"}
    assert got["message"] == "say hello"
    assert got["provider"] == "anthropic"
    assert got["model"] == "claude-sonnet-5"
    assert set(got["options"]) == {"max_turns", "thinking_level", "functions", "metadata"}
    assert got["options"]["max_turns"] == 50
    assert got["options"]["thinking_level"] == "medium"


def test_the_grant_is_explicit_and_empty(settings_dir):
    """Any non-empty allow would let a chat turn call tools; empty denies every call, on purpose."""
    assert turn.payload("p", "chat")["options"]["functions"] == {"allow": []}


def test_the_session_id_follows_the_job_phase_count_convention(settings_dir):
    """The completion event carries no metadata, so the id must encode job, phase, and count."""
    got = turn.payload("p", "chat")
    match = SESSION.match(got["session_id"])
    assert match, got["session_id"]
    job, phase = match.groups()
    assert got["options"]["metadata"] == {"job_id": job, "phase": phase}
    assert phase == "chat"


def test_every_call_gets_a_fresh_session(settings_dir):
    """A fixed id inherits the last run's transcript and failed state, or folds into a running turn."""
    ids = {turn.payload("p", "chat")["session_id"] for _ in range(50)}
    assert len(ids) == 50


def test_an_edit_to_settings_changes_the_next_payload(settings_dir):
    """The script caches nothing, so an edit applies to the next turn with no restart."""
    assert turn.payload("p", "chat")["options"]["max_turns"] == 50
    (settings_dir / "phases.yaml").write_text("defaults:\n  max_turns: 20\n")
    assert turn.payload("p", "chat")["options"]["max_turns"] == 20


def test_the_script_prints_one_json_object_and_nothing_else(settings_dir):
    """Its stdout goes straight into `iii trigger --json "$(...)"`, so any extra text breaks the send."""
    env = {**os.environ, "SETTINGS_DIR": str(settings_dir)}
    out = subprocess.run([sys.executable, str(SCRIPT), "--prompt", "say hello"],
                         env=env, capture_output=True, text=True, check=True)
    assert json.loads(out.stdout)["message"] == "say hello"
