"""Two helpers for the live tests: send one turn the way an operator does, and
wait for it. Both go through the iii CLI, so they need a running engine."""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DONE = {"completed", "failed", "cancelled"}


def iii(function_id: str, body: dict, timeout_ms: int = 30000) -> dict:
    done = subprocess.run(["iii", "trigger", function_id, "--json", json.dumps(body),
                           "--timeout-ms", str(timeout_ms)],
                          capture_output=True, text=True, timeout=timeout_ms / 1000 + 10)
    assert done.returncode == 0, f"{function_id} failed: {done.stderr.strip()}"
    return json.loads(done.stdout or "null")


def turn(prompt: str, repo: Path, phase: str = "run") -> str:
    """Print the send with scripts/turn.py, as an operator does, then send it."""
    built = subprocess.run([sys.executable, str(ROOT / "scripts" / "turn.py"), "--phase", phase,
                            "--repo", str(repo), "--prompt", prompt],
                           capture_output=True, text=True, cwd=ROOT)
    assert built.returncode == 0, built.stderr
    send = json.loads(built.stdout)
    iii("harness::send", send)
    return send["session_id"]


def status_of(answer) -> str:
    if not isinstance(answer, dict):
        return ""
    return str(answer.get("status") or (answer.get("turn") or {}).get("status") or "")


def wait_for_completed(session_id: str, seconds: int = 180) -> str:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        status = status_of(iii("harness::status", {"session_id": session_id}))
        if status in DONE:
            return status
        time.sleep(3)
    raise AssertionError(f"{session_id} did not finish in {seconds} s")
