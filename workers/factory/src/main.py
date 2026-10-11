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
import contracts
import document
import graph
import jobs
import repos

ROOT = Path(__file__).resolve().parents[3]
TOPIC = "factory.stage"
# Six fields, seconds first: second zero of every minute.
TICK = "0 * * * * *"
STUCK_AFTER = 3600
TURN_COMPLETED = "harness::turn-completed"  # hyphen: what the harness worker delivers on
POST_GENERATE = "harness::hook::post-generate"


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
    stage = graph.load()["stages"].get(job["stage"]) or {}
    if stage.get("action") == "turn":
        started = actions.start_turn(job, call, worker, stage)  # the turn's end moves the job
        if started.get("failed"):
            return advance(jobs.read(job["id"]) or job, started)
        return started
    return advance(job, actions.run_stage(job, worker=worker))

def advance(job: dict, result: dict) -> dict:
    job = {**job, **(result.get("record") or {})}   # what the stage made, e.g. its worktree
    to, why = graph.next_stage(job, graph.load(), result)
    if to == job["stage"]:
        return {"stayed": to, "why": why}
    refused = bool(result.get("refused"))
    # A refusal that fails the job starts no revision, so it is not counted as one.
    moved = jobs.moved(job, to, why, revision=refused and to not in graph.TERMINAL)
    if "brief" not in (result.get("record") or {}):
        moved.pop("brief", None)         # a brief belongs to the one move that set it
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
    stage = graph.load()["stages"].get(job["stage"]) or {}
    return advance(job, actions.finish_turn(worker, job, stage, actions.from_turn(event)))


def answer(payload: dict) -> dict:
    """A person's reply to a blocked job: into the document, then back to run."""
    payload = payload if isinstance(payload, dict) else {}
    text = payload.get("text")
    if not isinstance(text, str) or not text.strip():
        raise ValueError("factory::answer takes {\"job_id\": ..., \"text\": ...}")
    job = jobs.read(payload.get("job_id"))
    if job is None:
        raise ValueError(f"no job {payload.get('job_id')!r}")
    if (graph.load()["stages"].get(job["stage"]) or {}).get("action") != "hold":
        raise ValueError(f"job {job['id'][:8]} is at {job['stage']}, not blocked")
    # The working copy, never specs/: the checks read the answer from here.
    written = document.put(document.load(job), "answer",
                           f"{job.get('question') or 'The job stopped.'}\n\n"
                           f"Answer: {text.strip()}")
    document.save(job, written)
    return advance({**job, "question": None}, {"answered": text.strip()})


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
        elif stage.get("action") == "hold":
            continue                     # blocked: a person owes it an answer, not a retry
        elif job["stage"] not in graph.TERMINAL and now - job["updated"] > STUCK_AFTER:
            report["stuck"].append(job["id"])  # a person decides; republishing would rerun it
    return report


def listed(payload: dict | None = None) -> dict:
    now = time.time()
    rows = [{"id": j["id"], "stage": j["stage"], "epoch": j["epoch"],
             "revisions": j.get("revisions", 0), "steps": len(j.get("history", [])),
             "session_id": j.get("session_id"), "spec": j["spec"],
             "reason": (j.get("history") or [{}])[-1].get("why", ""),
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
worker.register_function("factory::answer", answer,
                         description="Answer a blocked job: {job_id, text}. Moves it back to run.")
worker.register_function("factory::verdict_guard", contracts.verdict_guard,
                         description="Internal: grades prove and review. Bound to post-generate.")
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
# post-generate observes and cannot refuse, which suits a guard that grades.
worker.register_trigger({"type": POST_GENERATE, "function_id": "factory::verdict_guard",
                         "config": {"timeout_ms": 5000, "on_error": "fail_open"}})

if __name__ == "__main__":
    print(f"factory: {TOPIC} -> factory::step, {TURN_COMPLETED} -> factory::turn_completed")
    print(f"factory: {TICK} -> factory::tick, jobs in {jobs.folder()}")
    print(f"factory: {POST_GENERATE} -> factory::verdict_guard")
    threading.Event().wait()
