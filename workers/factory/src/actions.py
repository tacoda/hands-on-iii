"""What a stage does. At tag ch08 every stage but a `turn` is a stub.

`run_stage` is pure once given the graph, and `from_turn` is pure. `start_turn` talks to the engine only
through the `call(function_id, payload)` it is given, so a test passes a fake.
"""

import sys
from pathlib import Path

import graph as stage_graph
import jobs

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "scripts"))

import charter  # noqa: E402  scripts/, the same charter chapter 5 builds
import turn     # noqa: E402  scripts/, the same send chapter 3 builds

GATE = "ladder::gate"
ENDED = {"completed", "cancelled", "failed"}


def run_stage(job: dict, graph: dict | None = None) -> dict:
    stages = (graph or stage_graph.load())["stages"]
    action = (stages.get(job["stage"]) or {}).get("action", "stub")
    if action == "watch":
        # No forge until chapter 9, so a person can never have acted yet.
        return {"outcome": ""}
    if action != "stub":
        return {"failed": f"no action {action!r} at this tag"}
    # Run it's break: {"stub_refuse": "commit"} makes that stub refuse every time.
    if (job.get("options") or {}).get("stub_refuse") == job["stage"]:
        return {"refused": True, "reason": f"the {job['stage']} stub refuses"}
    return {"stub": job["stage"]}


def from_turn(event: dict) -> dict:
    if event.get("status") == "completed":
        return {"ok": True, "result": event.get("result")}
    return {"failed": event.get("reason") or event.get("status") or "the turn ended"}


def ladder_registered(call) -> bool:
    # on_error: fail_closed covers a hook that runs and fails, not one that is
    # gone: a ladder that is down has no binding, and the call runs unguarded.
    try:
        info = call("engine::functions::info", {"function_id": GATE})
    except Exception:
        return False
    return bool(info) and GATE in str(info) and "function_not_found" not in str(info)


def brief(job: dict) -> str:
    spec = Path(job["spec"])
    text = (spec if spec.is_absolute() else ROOT / spec).read_text()
    return f"Phase: {job['stage']}. Job: {job['id']}.\n\n{text}"


def build_send(job: dict, session: str, call) -> tuple[dict, str, str]:
    repo = Path(job["repo"])
    workspace = str(repo if repo.is_absolute() else (ROOT / repo).resolve())
    send = turn.send_options(job["stage"], workspace)
    built = charter.for_repo(Path(workspace))
    if built.pieces:
        send["options"]["system_prompt"] = built.text()
    meta = send["options"]["metadata"]
    meta |= {"job_id": job["id"], "charter_pieces": len(built.pieces)}
    taken = (call("ladder::list", {"repo": workspace}) or {}).get("withheld")
    if taken is None:
        raise RuntimeError("ladder::list did not answer, so the repo's withheld functions are unknown")
    deny = send["options"]["functions"].setdefault("deny", [])
    deny += [fid for fid in taken if fid not in deny]
    level, mode = turn.oversight()
    meta["oversight"] = level
    # The key is the session id, so a duplicate stage message gets the
    # original turn back with deduplicated: true and starts nothing.
    return ({"session_id": session, "idempotency_key": session,
             "message": brief(job), **send}, level, mode)


def start_turn(job: dict, call) -> dict:
    if not ladder_registered(call):
        # Raise before writing anything: the queue retries, then dead-letters,
        # and no turn starts with no rule in front of it.
        raise RuntimeError(f"{GATE} is not registered, so no rule would refuse a call. "
                           "Start the ladder, then redrive factory.stage.")
    session = job.get("session_id")
    if not session:
        # Every move clears session_id, so one already here belongs to this epoch.
        n = (job.get("turns") or {}).get(job["stage"], 0) + 1
        session = jobs.session_id(job, job["stage"], n)
        job = {**job, "session_id": session, "turns": {**(job.get("turns") or {}),
                                                        job["stage"]: n}}
        jobs.write(job)                  # the record first, then the send
    send, level, mode = build_send(job, session, call)
    # A mode set after the send does not reach the calls the turn already made.
    call("approval::set-mode", {"session_id": session, "mode": mode})
    answer = call("harness::send", send) or {}
    return {"started": session, "oversight": level,
            "deduplicated": bool(answer.get("deduplicated"))}
