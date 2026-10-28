"""A turn is a file, state/turns/<session_id>.json. It is the record, and its
step number is the epoch, as a job file is for the factory (chapter 8)."""

import json
import os
import re
import time
from pathlib import Path

SESSION = re.compile(r"^[A-Za-z0-9_.-]{1,128}$")
STALL_AFTER_S = 600     # well past one streamed step


def folder() -> Path:
    return Path(os.environ.get("PLANNER_TURNS", "state/turns"))


def path(session_id: str) -> Path:
    # A session id names a file, so it never names a path of its own.
    if not SESSION.match(session_id or ""):
        raise ValueError(f"not a session id: {session_id!r}")
    return folder() / f"{session_id}.json"


def read(session_id: str) -> dict | None:
    try:
        return json.loads(path(session_id).read_text())
    except FileNotFoundError:
        return None


def write(turn: dict) -> dict:
    turn["updated"] = time.time()
    target = path(turn["session_id"])
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(turn))
    os.replace(tmp, target)              # a crash leaves the old file or the new one
    return turn


def status(turn: dict | None, now: float | None = None) -> dict:
    """planner::status. It only reports: a blind republish could run a step twice."""
    if turn is None:
        return {"status": "unknown"}
    now = time.time() if now is None else now
    if turn.get("done"):
        return {"status": turn["status"], "step": turn["step"], "result": turn.get("result"),
                "result_error": turn.get("reason"), "cost_usd": turn["cost_usd"]}
    stalled = now - turn.get("updated", now) > STALL_AFTER_S
    return {"status": "stalled" if stalled else "running", "step": turn["step"],
            "cost_usd": turn["cost_usd"]}
