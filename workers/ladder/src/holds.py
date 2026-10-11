"""Holds that wait too long. Neither the approval gate nor the harness worker
expires a held call: both mark it held_by, and the harness worker's sweep skips
held calls. So the ladder keeps the timer, for its own holds and the gate's.

stale(), timeout_of(), and the two request builders are pure. remember(),
recorded(), and forget() touch one file per ladder hold under state/holds/,
because the harness worker lists no hook holds and a restart must not lose one.
"""

import json
import os
import time
from pathlib import Path

DEFAULT_TIMEOUT_MS = 1_800_000          # the harness worker's own default


def folder() -> Path:
    return Path(os.environ.get("LADDER_HOLDS", "state/holds"))


def now_ms() -> int:
    return int(time.time() * 1000)


def remember(payload: dict, at_ms: int | None = None) -> dict:
    """Record a ladder hold so the timer can find it later."""
    call = payload.get("call") or {}
    hold = {"session_id": str(payload.get("session_id") or ""),
            "turn_id": str(payload.get("turn_id") or ""),
            "job_id": str((payload.get("metadata") or {}).get("job_id") or ""),
            "function_call_id": str(call.get("id") or ""),
            "function_id": str(call.get("function_id") or ""),
            "held_at": now_ms() if at_ms is None else at_ms}
    if hold["session_id"] and hold["turn_id"] and hold["function_call_id"]:
        folder().mkdir(parents=True, exist_ok=True)
        path = folder() / f"{hold['function_call_id']}.json"
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(hold))
        os.replace(tmp, path)            # a crash leaves the old file or the new one
    return hold


def recorded() -> list[dict]:
    found = []
    for path in sorted(folder().glob("*.json")) if folder().is_dir() else []:
        try:
            found.append(json.loads(path.read_text()))
        except (OSError, ValueError):
            continue                     # a torn file is not a hold
    return found


def forget(hold: dict) -> None:
    (folder() / f"{hold.get('function_call_id')}.json").unlink(missing_ok=True)


def timeout_of(answer) -> int:
    """default_pending_timeout_ms from config/harness.yaml, so one setting
    bounds every wait. A missing or broken value falls back to 30 minutes."""
    entry = answer if isinstance(answer, dict) else {}
    value = entry.get("value", entry)
    try:
        timeout = int((value if isinstance(value, dict) else {}).get("default_pending_timeout_ms") or 0)
    except (TypeError, ValueError):
        timeout = 0
    return timeout if timeout > 0 else DEFAULT_TIMEOUT_MS


def stale(holds: list, now: int, timeout_ms: int, key: str) -> list[dict]:
    """The holds at or past the timeout, oldest first."""
    old = [h for h in holds if isinstance(h, dict)
           and isinstance(h.get(key), (int, float)) and now - h[key] >= timeout_ms]
    return sorted(old, key=lambda h: h[key])


def reason(timeout_ms: int) -> str:
    minutes = max(1, round(timeout_ms / 60000))
    return (f"pending call timed out: nobody answered it within {minutes} minutes. "
            "Do not retry this call; say in your reply that it waited for a person.")


def deny_ladder_hold(hold: dict, timeout_ms: int) -> dict:
    """harness::function::resolve: deliver an error in place of the result."""
    return {"session_id": hold["session_id"], "turn_id": hold["turn_id"],
            "function_call_id": hold["function_call_id"], "action": "deliver",
            "is_error": True, "content": [{"type": "text", "text": reason(timeout_ms)}]}


def deny_gate_hold(pending: dict, timeout_ms: int) -> dict:
    """approval::resolve: a deny, with the reason the model will read."""
    return {"session_id": pending["session_id"], "function_call_id": pending["function_call_id"],
            "decision": "deny", "reason": reason(timeout_ms)}
