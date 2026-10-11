import json
import os
from pathlib import Path

import pytest
import yaml

import actions
import graph
import jobs
from graph import DEFAULT, next_stage

ROOT = Path(__file__).resolve().parents[1]
SUBMIT = {"spec": "specs/document-the-ports.md", "repo": "tests/fixtures/sample-repo"}
TURN_PLAN = ("first: plan\nstages:\n  plan: {action: turn, next: run}\n"
             "  run: {action: stub, next: landed}\n")
# Chapter 8's all-stub graph, so these tests still walk a job with no engine.
STUBS = """first: plan
stages:
  plan:    {action: stub, next: run}
  run:     {action: stub, next: commit}
  commit:  {action: stub, next: publish, on_refusal: {goto: run, max: 2}}
  publish: {action: stub, next: waiting}
  waiting: {action: watch, on_merge: landed, on_close: closed, on_comment: rework}
  rework:  {action: stub, next: run}
"""


@pytest.fixture
def stubs(settings_dir):
    (settings_dir / "pipeline.yaml").write_text(STUBS)
    return settings_dir


def published(factory_main):
    return [p["data"] for fid, p in factory_main.worker.calls if fid == "iii::durable::publish"]


def drain(factory_main):
    """Deliver every published message once, in order, as the queue would."""
    done = 0
    while done < len(published(factory_main)):
        factory_main.step(published(factory_main)[done])
        done += 1
    return published(factory_main)


def sends(factory_main):
    return [p for fid, p in factory_main.worker.calls if fid == "harness::send"]


# The graph is a value

def test_a_refusal_with_no_revisions_left_fails_the_job():
    """A refusal loop with no bound would send commit back to run forever."""
    assert next_stage({"stage": "commit", "revisions": 2}, DEFAULT, {"refused": True})[0] == "failed"


@pytest.mark.parametrize("job, result, expected", [
    ({"stage": "plan"}, {}, "run"),
    ({"stage": "commit", "revisions": 0}, {"refused": True}, "run"),
    ({"stage": "commit", "revisions": 1}, {"refused": True}, "run"),
    ({"stage": "run"}, {"refused": True}, "failed"),
    ({"stage": "waiting"}, {}, "waiting"),
    ({"stage": "waiting"}, {"outcome": "merge"}, "landed"),
    ({"stage": "waiting"}, {"outcome": "close"}, "closed"),
    ({"stage": "waiting"}, {"outcome": "comment"}, "rework"),
    ({"stage": "rework"}, {}, "run"),
    ({"stage": "nowhere"}, {}, "failed"),
    ({"stage": "plan"}, {"failed": "the turn failed"}, "failed"),
])
def test_next_stage_answers_every_branch_from_two_dicts(job, result, expected):
    """A branch checked only by a paid run is a branch nobody checks."""
    assert next_stage(job, DEFAULT, result)[0] == expected


def test_next_stage_reads_nothing_and_changes_nothing():
    """A next_stage that mutated the job would move it before the record was written."""
    job, result = {"stage": "commit", "revisions": 1}, {"refused": True}
    before = json.dumps([job, result, DEFAULT], sort_keys=True)
    next_stage(job, DEFAULT, result)
    assert json.dumps([job, result, DEFAULT], sort_keys=True) == before


def test_the_shipped_pipeline_is_the_default(settings_dir):
    """An empty settings/ must run the same graph the repo ships."""
    shipped = yaml.safe_load((ROOT / "settings" / "pipeline.yaml").read_text())
    assert shipped == DEFAULT
    assert graph.load() == DEFAULT  # settings_dir is empty


# Job records are files

def test_a_write_is_atomic_and_a_crash_keeps_the_old_record(tmp_path, monkeypatch):
    """A half-written record would leave a job nobody can read."""
    monkeypatch.setenv("FACTORY_JOBS", str(tmp_path))
    job = jobs.new("s.md", "r", "plan")
    jobs.write(job)
    assert list(tmp_path.glob("*.tmp")) == []

    def crash(src, dst):
        raise OSError("power cut")
    monkeypatch.setattr(jobs.os, "replace", crash)
    with pytest.raises(OSError):
        jobs.write(jobs.moved(job, "run", "plan finished"))
    assert jobs.read(job["id"]) == job
    assert len(list(tmp_path.glob("*.tmp"))) == 1


def test_a_write_fsyncs_before_it_renames(tmp_path, monkeypatch):
    """A rename before the bytes reach disk can leave an empty record after a power cut."""
    monkeypatch.setenv("FACTORY_JOBS", str(tmp_path))
    order = []
    real_fsync, real_replace = os.fsync, os.replace
    monkeypatch.setattr(jobs.os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd)))
    monkeypatch.setattr(jobs.os, "replace", lambda a, b: (order.append("replace"),
                                                          real_replace(a, b)))
    jobs.write(jobs.new("s.md", "r", "plan"))
    assert order == ["fsync", "replace"]


def test_a_move_raises_the_epoch_clears_the_session_and_records_why():
    """Without the epoch, a loop back to run looks like the old run message."""
    job = {**jobs.new("s.md", "r", "run"), "session_id": "s_abcdef12_run_1"}
    moved = jobs.moved(job, "commit", "run finished")
    assert (moved["stage"], moved["epoch"], moved["session_id"]) == ("commit", 1, None)
    assert moved["history"][-1] | {"at": 0} == {"from": "run", "to": "commit",
                                                "why": "run finished", "epoch": 1, "at": 0}
    assert job["epoch"] == 0 and job["history"] == []


def test_a_job_id_from_a_message_never_names_a_path(tmp_path, monkeypatch):
    """A queue message is input, and ../ in it would read outside state/jobs/."""
    monkeypatch.setenv("FACTORY_JOBS", str(tmp_path))
    assert jobs.read("../../etc/passwd") is None
    assert jobs.read(None) is None


# Submit

@pytest.mark.parametrize("payload, said", [
    ({"repo": SUBMIT["repo"]}, "spec"),
    ({"spec": SUBMIT["spec"]}, "repo"),
    ({**SUBMIT, "spec": 3}, "spec"),
    ({**SUBMIT, "spec": "specs/no-such-spec.md"}, "not a file"),
    ({**SUBMIT, "repo": "tests/fixtures/no-such-repo"}, "not a directory"),
    ({**SUBMIT, "branch": "main"}, "unknown keys"),
    ({**SUBMIT, "stub_refuse": 1}, "stub_refuse"),
    ("specs/document-the-ports.md", "factory::submit takes"),
])
def test_submit_refuses_a_bad_payload_before_writing_a_record(factory_main, settings_dir,
                                                              payload, said):
    """A record for a spec that does not exist is a job that fails on its first paid turn."""
    with pytest.raises(ValueError, match=said):
        factory_main.submit(payload)
    assert jobs.all_jobs() == [] and published(factory_main) == []


def test_submit_ignores_the_keys_the_engine_adds(factory_main, settings_dir):
    """The engine adds _caller_worker_id to a call made with iii trigger. A check that
    refused it refused every submit the book prints."""
    answer = factory_main.submit({**SUBMIT, "_caller_worker_id": "cli"})
    assert jobs.read(answer["job_id"]) is not None


def test_submit_writes_epoch_zero_at_first_and_publishes_one_message(factory_main, stubs):
    """The message is the three keys and nothing else, so it can never disagree with the file."""
    answer = factory_main.submit(SUBMIT)
    job = jobs.read(answer["job_id"])
    assert (job["stage"], job["epoch"], job["history"]) == ("plan", 0, [])
    assert published(factory_main) == [{"job_id": job["id"], "stage": "plan", "epoch": 0}]


# One message per transition

def test_stub_job_walks_to_waiting_with_four_history_entries(factory_main, stubs):
    """Run it's first step: no forge, so the job stops at waiting."""
    job_id = factory_main.submit(SUBMIT)["job_id"]
    drain(factory_main)
    job = jobs.read(job_id)
    assert job["stage"] == "waiting" and len(job["history"]) == 4


def test_the_epoch_guard_drops_a_stale_redelivery(factory_main, stubs):
    """A duplicate run message would open a second commit and a second pull request."""
    job_id = factory_main.submit(SUBMIT)["job_id"]
    drain(factory_main)
    before = jobs.read(job_id)
    count = len(published(factory_main))
    got = factory_main.step({"job_id": job_id, "stage": "run", "epoch": 1})
    assert "skipped" in got
    assert jobs.read(job_id) == before and len(published(factory_main)) == count


def test_a_loop_back_to_the_same_stage_gets_a_new_epoch(factory_main, stubs):
    """A stage name alone cannot tell the first run from the third."""
    job_id = factory_main.submit({**SUBMIT, "stub_refuse": "commit"})["job_id"]
    drain(factory_main)
    job = jobs.read(job_id)
    assert job["stage"] == "failed"
    assert job["history"][-1]["why"].startswith("commit refused 3 times")
    assert [h["epoch"] for h in job["history"] if h["to"] == "run"] == [1, 3, 5]
    for epoch in (1, 3):
        assert "skipped" in factory_main.step({"job_id": job_id, "stage": "run", "epoch": epoch})
    assert jobs.read(job_id) == job


def test_a_transition_to_the_same_stage_is_not_one(factory_main, stubs):
    """ghola's first reconciler spun 2,020 history entries in 75 seconds without this."""
    job_id = factory_main.submit(SUBMIT)["job_id"]
    drain(factory_main)
    before, count = jobs.read(job_id), len(published(factory_main))
    got = factory_main.step({"job_id": job_id, "stage": "waiting", "epoch": before["epoch"]})
    assert got["stayed"] == "waiting"
    assert jobs.read(job_id) == before and len(published(factory_main)) == count


def test_a_terminal_stage_publishes_nothing(factory_main, stubs):
    """A message for failed would wake a consumer with nothing to do, forever."""
    factory_main.submit({**SUBMIT, "stub_refuse": "commit"})
    assert all(m["stage"] not in graph.TERMINAL for m in drain(factory_main))


# Starting a turn, and hearing it finish

@pytest.fixture
def turn_job(factory_main, phases):
    (phases.parent / "pipeline.yaml").write_text(TURN_PLAN)
    job_id = factory_main.submit(SUBMIT)["job_id"]
    return job_id


def test_a_turn_stage_sends_with_the_session_id_as_idempotency_key(factory_main, turn_job):
    """Without the key, a redelivered stage message starts a second paid turn."""
    factory_main.step({"job_id": turn_job, "stage": "plan", "epoch": 0})
    [send] = sends(factory_main)
    assert send["session_id"] == f"s_{turn_job[:8]}_plan_1"
    assert send["idempotency_key"] == send["session_id"]
    assert send["options"]["metadata"]["job_id"] == turn_job
    assert send["options"]["metadata"]["phase"] == "plan"
    assert jobs.read(turn_job)["session_id"] == send["session_id"]


def test_a_duplicate_stage_message_resends_the_same_key(factory_main, turn_job):
    """A second session id for the same epoch would be a second turn the key cannot stop."""
    for _ in range(2):
        factory_main.step({"job_id": turn_job, "stage": "plan", "epoch": 0})
    first, second = sends(factory_main)
    assert first["idempotency_key"] == second["idempotency_key"] == first["session_id"]
    assert jobs.read(turn_job)["turns"] == {"plan": 1}


def test_mode_is_set_before_the_send(factory_main, turn_job):
    """A mode set after the send does not reach the calls the turn already made."""
    factory_main.step({"job_id": turn_job, "stage": "plan", "epoch": 0})
    order = [fid for fid, _ in factory_main.worker.calls]
    assert order.index("approval::set-mode") < order.index("harness::send")


def test_the_turn_completion_moves_the_job(factory_main, turn_job):
    """A turn stage waits on the event, not on the step."""
    factory_main.step({"job_id": turn_job, "stage": "plan", "epoch": 0})
    session = jobs.read(turn_job)["session_id"]
    got = factory_main.turn_completed({"session_id": session, "turn_id": "t_1", "terminal": True,
                                       "status": "completed", "result": "a plan"})
    assert got["moved"] == "run"
    assert jobs.read(turn_job)["session_id"] is None


def test_a_stale_completion_is_ignored(factory_main, turn_job):
    """A late completion from an earlier revision would move the job a second time."""
    factory_main.step({"job_id": turn_job, "stage": "plan", "epoch": 0})
    job = jobs.read(turn_job)
    stale = {"session_id": f"s_{turn_job[:8]}_plan_7", "status": "completed", "terminal": True}
    assert "skipped" in factory_main.turn_completed(stale)
    assert jobs.read(turn_job) == job
    event = {"session_id": job["session_id"], "status": "completed", "terminal": True}
    factory_main.turn_completed(event)
    moved = jobs.read(turn_job)
    assert "skipped" in factory_main.turn_completed(event)  # the same event, delivered twice
    assert jobs.read(turn_job) == moved


def test_a_non_terminal_completion_is_ignored(factory_main, turn_job):
    """A completion that is not terminal would advance a job whose turn is still running."""
    factory_main.step({"job_id": turn_job, "stage": "plan", "epoch": 0})
    job = jobs.read(turn_job)
    event = {"session_id": job["session_id"], "turn_id": "t_1", "terminal": False,
             "status": "completed", "result": "a step"}
    assert factory_main.turn_completed(event) == {}
    assert factory_main.turn_completed({k: v for k, v in event.items() if k != "terminal"}) == {}
    assert jobs.read(turn_job) == job


def test_another_workers_session_is_not_ours(factory_main, turn_job):
    """Every turn on the engine reaches the handler, because the binding has no filter."""
    assert factory_main.turn_completed({"session_id": "s_0123abcd_plan_1", "terminal": True}) == {}
    assert factory_main.turn_completed({"session_id": "s_8c1f2b3a-uuid", "terminal": True}) == {}
    assert factory_main.turn_completed({"terminal": True}) == {}


def test_a_failed_turn_fails_the_job(factory_main, turn_job):
    """A failed turn read as a finished one would carry no plan into run."""
    factory_main.step({"job_id": turn_job, "stage": "plan", "epoch": 0})
    session = jobs.read(turn_job)["session_id"]
    factory_main.turn_completed({"session_id": session, "status": "failed", "reason": "boom",
                                 "terminal": True})
    assert jobs.read(turn_job)["stage"] == "failed"


# The ladder presence check

def test_no_send_without_the_ladder(factory_main, turn_job):
    """A ladder that is down has no binding, so the harness worker would call no hook."""
    def missing(payload):
        raise RuntimeError("function_not_found: ladder::gate")
    factory_main.worker.answers["engine::functions::info"] = missing
    before = jobs.read(turn_job)
    with pytest.raises(RuntimeError, match="ladder::gate"):
        factory_main.step({"job_id": turn_job, "stage": "plan", "epoch": 0})
    assert sends(factory_main) == []
    assert jobs.read(turn_job) == before  # raised before it wrote anything


def test_the_ladder_check_runs_before_every_send(factory_main, turn_job):
    """A check made once at boot would miss a ladder that stopped later."""
    for _ in range(2):
        factory_main.step({"job_id": turn_job, "stage": "plan", "epoch": 0})
    infos = [p for fid, p in factory_main.worker.calls if fid == "engine::functions::info"]
    assert infos == [{"function_id": "ladder::gate"}] * 2


@pytest.mark.parametrize("answer", [None, {}, {"error": "function_not_found"}])
def test_an_empty_info_answer_counts_as_missing(factory_main, answer):
    """Treating a non-answer as present is the fail-open this check exists to stop."""
    factory_main.worker.answers["engine::functions::info"] = answer
    assert not actions.ladder_registered(factory_main.call)


def test_tick_reports_a_missing_ladder_first(factory_main, settings_dir):
    """A tick that said nothing about the ladder would hide every unguarded turn."""
    factory_main.worker.answers["engine::functions::info"] = None
    report = factory_main.tick({})
    assert next(iter(report)) == "ladder" and report["ladder"] == "missing"


# A clock for what the queue cannot see

def test_tick_polls_waiting_jobs_through_the_queue_with_their_epoch(factory_main, stubs):
    """A poll that skipped the queue would skip the epoch guard too."""
    job_id = factory_main.submit(SUBMIT)["job_id"]
    drain(factory_main)
    epoch = jobs.read(job_id)["epoch"]
    report = factory_main.tick({})
    assert report["ladder"] == "ok" and report["polled"] == [job_id]
    assert published(factory_main)[-1] == {"job_id": job_id, "stage": "waiting", "epoch": epoch}


def test_tick_recovers_a_completion_missed_while_the_factory_was_down(factory_main, turn_job):
    """The engine drops a disconnected worker's bindings, so the event reached nobody."""
    factory_main.step({"job_id": turn_job, "stage": "plan", "epoch": 0})
    factory_main.worker.answers["harness::status"] = {"status": "completed", "result": "a plan"}
    assert factory_main.tick({})["recovered"] == [turn_job]
    assert jobs.read(turn_job)["stage"] == "run"


def test_tick_reports_a_stuck_job_and_moves_nothing(factory_main, stubs):
    """Republishing a stuck job blindly would rerun its stage, the duplicate the guard stops."""
    job_id = factory_main.submit(SUBMIT)["job_id"]  # published, never delivered
    old = {**jobs.read(job_id), "updated": 0}
    jobs.write(old)
    count = len(published(factory_main))
    assert factory_main.tick({})["stuck"] == [job_id]
    assert jobs.read(job_id) == old and len(published(factory_main)) == count
    assert factory_main.listed({})["jobs"][0]["stuck"] is True


# The wiring

def test_the_factory_registers_as_factory(factory_main):
    """Compose waits out its timeout for a container whose worker uses another name."""
    assert factory_main.worker.options == {"worker_name": "factory"}
    assert set(factory_main.worker.functions) == {
        "factory::submit", "factory::step", "factory::turn_completed",
        "factory::tick", "factory::jobs", "factory::answer", "factory::verdict_guard"}


def test_the_bindings_are_the_ones_the_chapters_name(factory_main):
    """Without the subscriber, every job writes its first record and stops."""
    bound = {t["type"]: t for t in factory_main.worker.triggers}
    assert bound["durable:subscriber"] == {"type": "durable:subscriber",
                                           "function_id": "factory::step",
                                           "config": {"topic": "factory.stage"}}
    assert bound["harness::turn-completed"]["function_id"] == "factory::turn_completed"
    assert bound["harness::turn-completed"]["config"] == {}  # a session_id filter heard nothing
    assert bound["cron"]["function_id"] == "factory::tick"
    assert len(bound["cron"]["config"]["expression"].split()) == 6  # seconds first


def test_compose_pins_queue_and_cron_and_persists_the_queue():
    """An in_memory queue loses every unfinished message on restart, and an adapter block
    that names builtin without store_method runs in_memory."""
    compose = yaml.safe_load((ROOT / "worker-compose.yaml").read_text())["containers"]
    assert (compose["queue"]["worker"], compose["queue"]["version"]) == ("package://queue", "0.21.6")
    assert (compose["cron"]["worker"], compose["cron"]["version"]) == ("package://cron", "0.21.10")
    factory = compose["factory"]
    assert factory["worker"] == "path://./workers/factory"
    assert set(factory["start_after"]) == {"harness", "queue", "state"}
    assert factory["scripts"]["run"] == ".venv/bin/python workers/factory/src/main.py"
    value = yaml.safe_load((ROOT / "config" / "queue.yaml").read_text())["value"]
    assert value["adapter"]["name"] == "builtin"
    assert value["adapter"]["config"]["store_method"] == "file_based"
    assert value["adapter"]["config"]["file_path"].startswith("./data/")
    # The worker writes each defined queue into this file, so the file carries them.
    assert "harness-turn" in value["queue_configs"]
