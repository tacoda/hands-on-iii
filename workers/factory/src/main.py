"""The factory worker: moves a job through settings/pipeline.yaml.

Compose starts it after harness, queue, and state, and injects III_URL. It
starts turns and never runs one. Every transition is one durable message on
factory.stage, {job_id, stage, epoch} and nothing else; the record under
state/jobs/ holds the rest. The graph is graph.py, the records jobs.py, and
the stages actions.py; this file is the wiring.
"""

import os
import threading
import time
from pathlib import Path

from iii import InitOptions, register_worker

import actions
import graph
import jobs
import repos

ROOT = Path(__file__).resolve().parents[3]
TOPIC = "factory.stage"
# Six fields, seconds first: second zero of every minute.
TICK = "0 * * * * *"
STUCK_AFTER = 3600
TURN_COMPLETED = "harness::turn-completed"  # hyphen: what the harness worker delivers on


def call(function_id: str, payload: dict, timeout_ms: int = 30000):
    return worker.trigger({"function_id": function_id, "payload": payload,
                           "timeout_ms": timeout_ms})


def enqueue(job_id: str, stage: str, epoch: int) -> None:
    call("iii::durable::publish",
         {"topic": TOPIC, "data": {"job_id": job_id, "stage": stage, "epoch": epoch}})


def validate(payload: dict) -> dict:
    """The submit payload, checked before any record exists."""
    if not isinstance(payload, dict):
        raise ValueError("factory::submit takes {\"spec\": ..., \"repo\": ...}")
    # The engine adds underscore keys, such as _caller_worker_id, to every call.
    unknown = sorted(k for k in set(payload) - {"spec", "repo", "stub_refuse"}
                     if not k.startswith("_"))
    if unknown:
        raise ValueError(f"unknown keys {unknown}; known: repo, spec, stub_refuse")
    given = {}
    for key in ("spec", "repo"):
        value = payload.get(key)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{key} must be a non-empty path")
        given[key] = value.strip()
    spec, repo = (Path(given[k]) for k in ("spec", "repo"))
    if not (spec if spec.is_absolute() else ROOT / spec).is_file():
        raise ValueError(f"spec {given['spec']} is not a file")
    if not (repo if repo.is_absolute() else ROOT / repo).is_dir():
        raise ValueError(f"repo {given['repo']} is not a directory")
    refuse = payload.get("stub_refuse")
    if refuse is not None and not isinstance(refuse, str):
        raise ValueError("stub_refuse must name a stage")
    return given | ({"stub_refuse": refuse} if refuse else {})


def title(spec: Path) -> str:
    return next((line for line in spec.read_text().splitlines() if line.strip()), "")


def submit(payload: dict) -> dict:
    given = validate(payload)
    first = graph.load()["first"]
    options = {"stub_refuse": given["stub_refuse"]} if "stub_refuse" in given else {}
    spec, repo = (Path(given[k]) for k in ("spec", "repo"))
    # settings/repos.toml is keyed by absolute path, and worktree::create needs one.
    repo = str(repo if repo.is_absolute() else (ROOT / repo).resolve())
    # A copy, so a rework weeks later rebuilds this job's environment, not
    # the one the file describes by then.
    settings = repos.resolve(repo)
    job = jobs.new(given["spec"], repo, first, **options)
    job |= {"settings": settings, "title": title(spec if spec.is_absolute() else ROOT / spec)}
    jobs.write(job)
    enqueue(job["id"], first, 0)
    return {"job_id": job["id"], "stage": first, "epoch": 0}


def step(message: dict) -> dict:
    job = jobs.read(message["job_id"])
    if job is None or (job["stage"], job["epoch"]) != (message["stage"], message["epoch"]):
        return {"skipped": "the job is already past this message"}
    if (graph.load()["stages"].get(job["stage"]) or {}).get("action") == "turn":
        return actions.start_turn(job, call)  # the turn's end moves the job
    return advance(job, actions.run_stage(job, worker=worker))

def advance(job: dict, result: dict) -> dict:
    job = {**job, **(result.get("record") or {})}   # what the stage made, e.g. its worktree
    to, why = graph.next_stage(job, graph.load(), result)
    if to == job["stage"]:
        return {"stayed": to, "why": why}
    refused = bool(result.get("refused"))
    # A refusal that fails the job starts no revision, so it is not counted as one.
    moved = jobs.moved(job, to, why, revision=refused and to not in graph.TERMINAL)
    moved.pop("brief", None)
    if refused:
        # Kept to compare with the next refusal, and sent as the next brief.
        refusal = str(result.get("refusal") or "")
        moved |= {"last_refusal": refusal, "brief": refusal}
    jobs.write(moved)                    # epoch + 1, session_id cleared
    if to in graph.TERMINAL:
        # Only a terminal stage gives the claim back; waiting still holds it.
        return {"moved": to, "why": why, "teardown": actions.teardown(worker, moved)["did"]}
    enqueue(job["id"], to, moved["epoch"])
    return {"moved": to, "why": why}


def turn_completed(event: dict) -> dict:
    if not event.get("terminal"):
        return {}                        # the turn has not ended
    job_id = jobs.parse_session(event.get("session_id"))
    if job_id is None:
        return {}                        # another worker's turn on this engine
    job = jobs.read(job_id)
    if job is None or job.get("session_id") != event["session_id"]:
        return {"skipped": "a stale completion"}
    return advance(job, actions.from_turn(event))


def tick(payload: dict | None = None) -> dict:
    """The reconciler: what the queue cannot see. It moves a job only along a
    path that already has a guard, and only reports a stuck one."""
    report = {"ladder": "ok" if actions.ladder_registered(call) else "missing",
              "polled": [], "recovered": [], "stuck": []}
    stages, now = graph.load()["stages"], time.time()
    for job in jobs.all_jobs():
        stage = stages.get(job["stage"]) or {}
        if stage.get("action") == "watch":
            # Through the queue, so the epoch guard and the retries apply.
            enqueue(job["id"], job["stage"], job["epoch"])
            report["polled"].append(job["id"])
        elif stage.get("action") == "turn" and job.get("session_id"):
            try:
                status = call("harness::status", {"session_id": job["session_id"]}) or {}
            except Exception:
                status = {}              # one unanswered status must not stop the rest
            if status.get("status") in actions.ENDED:
                event = {"session_id": job["session_id"], "status": status["status"],
                         "terminal": True,
                         "result": status.get("result"), "reason": status.get("result_error")}
                turn_completed(event)    # a missed completion, one minute late
                report["recovered"].append(job["id"])
        elif job["stage"] not in graph.TERMINAL and now - job["updated"] > STUCK_AFTER:
            report["stuck"].append(job["id"])  # a person decides; republishing would rerun it
    return report


def listed(payload: dict | None = None) -> dict:
    now = time.time()
    rows = [{"id": j["id"], "stage": j["stage"], "epoch": j["epoch"],
             "revisions": j.get("revisions", 0), "steps": len(j.get("history", [])),
             "session_id": j.get("session_id"), "spec": j["spec"],
             "stuck": j["stage"] not in graph.TERMINAL and now - j["updated"] > STUCK_AFTER}
            for j in jobs.all_jobs()]
    return {"jobs": sorted(rows, key=lambda r: not r["stuck"])}  # stuck first, then newest


worker = register_worker(os.environ["III_URL"], InitOptions(worker_name="factory"))
worker.register_function("factory::submit", submit,
                         description="A spec and a repo. Writes the job and returns its id.")
worker.register_function("factory::step", step,
                         description="Internal: one stage of one job. Bound to factory.stage.")
worker.register_function("factory::turn_completed", turn_completed,
                         description="Internal: a turn ended. Bound to harness::turn-completed.")
worker.register_function("factory::tick", tick,
                         description="The reconciler. Bound to cron, once a minute.")
worker.register_function("factory::jobs", listed,
                         description="Every job, stuck ones first, then newest first.")
# Without this binding nothing consumes factory.stage, and every job writes its
# first record and stops, which looks exactly like a job still working.
# The queue worker's canonical key is `queue`; `topic` is its alias.
worker.register_trigger({"type": "durable:subscriber", "function_id": "factory::step",
                         "config": {"topic": TOPIC}})
# Empty config: a session_id filter matches one id exactly, and the factory mints
# a new id for every turn, so one binding hears them all and the handler filters.
worker.register_trigger({"type": TURN_COMPLETED, "function_id": "factory::turn_completed",
                         "config": {}})
worker.register_trigger({"type": "cron", "function_id": "factory::tick",
                         "config": {"expression": TICK}})

if __name__ == "__main__":
    print(f"factory: {TOPIC} -> factory::step, {TURN_COMPLETED} -> factory::turn_completed")
    print(f"factory: {TICK} -> factory::tick, jobs in {jobs.folder()}")
    threading.Event().wait()
