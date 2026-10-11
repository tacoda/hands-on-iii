"""Chapter 10's stages: prompts, entry and exit checks, checks that cannot
repair, the routes to `blocked`, factory::answer, and rework on comments.

Real git in a temporary repository; every engine, harness, and forge call is a
stand-in, so no test sends a turn or reaches a network.
"""

import string
from pathlib import Path

import pytest
import yaml

import actions
import document
import forge
import graph
import jobs
from conftest import Shell, git

ROOT = Path(__file__).resolve().parents[1]
SPEC = "specs/document-the-ports.md"
PHASES = "phases:\n  plan: {}\n  run: {}\n  prove: {}\n  review: {}\n"


@pytest.fixture
def flow(factory_main, settings_dir, git_repo):
    """The shipped graph (settings/ has no pipeline.yaml), four phases, and the git_repo as the tree."""
    (settings_dir / "phases.yaml").write_text(PHASES)
    factory_main.worker.answers |= {
        "shell::exec": Shell(), "ladder::evaluate": {"decision": "continue"},
        "worktree::claim": {}, "github::pr::comment": {},
    }
    return factory_main


def at(stage, git_repo, sections=(), **extra):
    """A job at `stage`, with its document holding `sections`, written to the job store."""
    job = {**jobs.new(SPEC, str(git_repo), stage), "path": str(git_repo), "worktree_id": "wt_1",
           "branch": "factory/document-the-ports-0123abcd", "settings": {"forge": "local",
                                                                         "base": "main"}, **extra}
    jobs.write(job)
    text = document.load(job)
    for name, body in sections:
        text = document.put(text, name, body)
    document.save(job, text)
    return job


def sends(factory_main):
    return [p for fid, p in factory_main.worker.calls if fid == "harness::send"]


def published(factory_main):
    return [p["data"] for fid, p in factory_main.worker.calls if fid == "iii::durable::publish"]


def start(factory_main, job):
    return factory_main.step({"job_id": job["id"], "stage": job["stage"], "epoch": job["epoch"]})


def finish(factory_main, job_id, text):
    job = jobs.read(job_id)
    return factory_main.turn_completed({"session_id": job["session_id"], "status": "completed",
                                        "terminal": True, "result": text})


# Prompts

@pytest.mark.parametrize("phase", ["plan", "run", "prove", "review"])
def test_each_phase_has_a_prompt_with_only_fields_something_fills(phase):
    """ghola's review prompt carried a $diff that nothing wrote, so its heading rendered empty."""
    text = (ROOT / "prompts" / f"{phase}.md").read_text()
    names = {m.group("named") or m.group("braced")
             for m in string.Template.pattern.finditer(text) if m.group("named") or m.group("braced")}
    assert names <= {"document", "base"}
    assert "document" in names


def test_render_is_plain_substitution_and_leaves_unknown_names_alone():
    """A spec that mentions $PATH must not break the turn."""
    assert actions.render("Run $cmd in $PATH. $document", {"document": "d"}) == "Run $cmd in $PATH. d"


def test_the_review_prompt_tells_the_reviewer_to_diff_the_gates_base(flow, git_repo):
    """The reviewer reads the same change the delivery gate judges, or it reviews another one."""
    job = at("review", git_repo, [("work", "Added it.")])
    start(flow, job)
    [send] = sends(flow)
    assert "git diff main" in send["message"] and "Added it." in send["message"]
    assert send["options"]["metadata"]["phase"] == "review"


def test_the_run_prompt_offers_the_interrupt_hatch_and_names_what_counts():
    """A hatch with no limits turns every naming choice into a stopped job."""
    text = (ROOT / "prompts" / "run.md").read_text()
    assert "`INTERRUPT:`" in text and "opening line" in text
    for blocked in ("contradict", "credential", "destroy data"):
        assert blocked in text


def test_a_prompt_edit_needs_no_restart(flow, git_repo, monkeypatch, tmp_path):
    """The prompt is read at send time, so Run it's one-line edit reaches the next turn."""
    monkeypatch.setattr(actions, "prompts", lambda: tmp_path)
    (tmp_path / "plan.md").write_text("Plan this.\n$document\nAnswer VERDICT: blocker.\n")
    start(flow, at("plan", git_repo))
    assert sends(flow)[0]["message"].startswith("Plan this.\n<!-- factory:spec -->\n# Document the ports")


def test_a_stage_can_name_its_own_oversight_level(flow, settings_dir, git_repo):
    """Chapter 6's stages: block, read before each phase turn."""
    (settings_dir / "oversight.yaml").write_text(
        "default: supervised\nlevels: {attended: auto, supervised: full}\nstages: {plan: attended}\n")
    start(flow, at("plan", git_repo))
    [mode] = [p for fid, p in flow.worker.calls if fid == "approval::set-mode"]
    assert mode["mode"] == "auto"
    assert sends(flow)[0]["options"]["metadata"]["oversight"] == "attended"


# Entry and exit checks

def test_a_stage_without_its_inputs_fails_before_a_turn(flow, git_repo):
    """prove with no work would pay a turn to check nothing."""
    job = at("prove", git_repo)
    start(flow, job)
    assert sends(flow) == []
    after = jobs.read(job["id"])
    assert after["stage"] == "failed" and "needs work" in after["history"][-1]["why"]


def test_run_requires_the_spec_not_the_plan(flow, git_repo):
    """A rework skips plan, so a run that required one could never start twice."""
    start(flow, at("run", git_repo))
    assert len(sends(flow)) == 1


def test_a_turn_that_leaves_its_section_empty_fails_the_stage(flow, git_repo):
    """An empty section is not a section."""
    job = at("plan", git_repo)
    start(flow, job)
    finish(flow, job["id"], "   ")
    after = jobs.read(job["id"])
    assert after["stage"] == "failed" and "produced no plan" in after["history"][-1]["why"]


def test_each_phase_writes_its_section_and_the_document_is_the_request_body(flow, git_repo):
    """ghola's first real job opened a pull request with an empty body."""
    job = at("plan", git_repo)
    start(flow, job)
    finish(flow, job["id"], "Edit README.md only.")
    text = document.load(job)
    assert document.sections(text)["plan"] == "Edit README.md only."
    head = "ab" * 32
    assert actions.request_body(jobs.read(job["id"]), head) == f"{text.rstrip()}\n\nrecord head: {head}\n"
    assert jobs.read(job["id"])["stage"] == "run"


def test_a_downgrade_is_written_beside_the_claim(flow, git_repo):
    """The request must say what the turn claimed and what that claim was worth."""
    job = at("prove", git_repo, [("work", "Added it.")])
    start(flow, job)
    finish(flow, job["id"], "PROVEN: yes\nEverything works.")
    after = jobs.read(job["id"])
    proof = document.sections(document.load(job))["proof"]
    assert "Contract `proven`: `unproven`. The turn answered `yes`" in proof
    assert after["contracts"]["proven"]["value"] == "unproven"
    assert after["stage"] == "review"                    # a contract never fails a job


# Checks cannot repair

def test_prove_and_review_hold_no_editing_function(settings_dir):
    """A check that fixes what it finds reports on a tree nobody else has seen."""
    import settings
    shipped = yaml.safe_load((ROOT / "settings" / "phases.yaml").read_text())
    for phase in ("prove", "review"):
        got = settings.for_phase(phase, shipped)["functions"]
        editors = {"coder::create-file", "coder::update-file", "coder::delete-file", "coder::move"}
        assert not editors & (set(got["allow"]) - set(got["deny"]))
        assert "shell::exec" in got["allow"]


def test_a_check_is_reset_to_the_runs_work_after_it_ends(flow, git_repo):
    """prove holds shell::exec, and a test run can rewrite a snapshot file."""
    (git_repo / ".gitignore").write_text("build/\n")
    (git_repo / "README.md").write_text("# target\n\n## Ports\n")      # the run's work
    (git_repo / "notes.md").write_text("new from run\n")
    job = at("prove", git_repo, [("work", "Added it.")])
    start(flow, job)
    assert jobs.read(job["id"])["snapshot"]
    (git_repo / "README.md").write_text("rewritten by the check\n")
    (git_repo / "notes.md").unlink()
    (git_repo / "made-by-check.txt").write_text("x\n")
    (git_repo / "build").mkdir()
    (git_repo / "build" / "out.o").write_text("ignored\n")
    finish(flow, job["id"], "PROVEN: no\nThe section is missing.")
    assert (git_repo / "README.md").read_text() == "# target\n\n## Ports\n"
    assert (git_repo / "notes.md").read_text() == "new from run\n"
    assert not (git_repo / "made-by-check.txt").exists()
    assert (git_repo / "build" / "out.o").exists()           # ignored files stay
    assert jobs.read(job["id"])["snapshot"] is None


def test_a_check_whose_snapshot_fails_never_starts(flow, git_repo):
    """A check you cannot undo is a check that can repair."""
    flow.worker.answers["shell::exec"] = {"exit_code": 128, "stdout": "", "stderr": "not a repo"}
    job = at("review", git_repo, [("work", "Added it.")])
    start(flow, job)
    assert sends(flow) == []
    assert "could not snapshot" in jobs.read(job["id"])["history"][-1]["why"]


def test_a_redelivered_check_message_does_not_snapshot_twice(flow, git_repo):
    """A second snapshot mid-turn would capture the check's own edits as the run's work."""
    job = at("prove", git_repo, [("work", "Added it.")])
    start(flow, job)
    shell = flow.worker.answers["shell::exec"]
    before = len(shell.ran)
    start(flow, job)
    assert len(shell.ran) == before and len(sends(flow)) == 2


# The routes to blocked

def test_a_readable_blocker_sends_the_job_to_blocked_not_commit(flow, git_repo):
    """A verdict that names a place holds the work before anything leaves the machine."""
    job = at("review", git_repo, [("work", "Added it.")])
    start(flow, job)
    finish(flow, job["id"], "VERDICT: blocker\n\n- README.md:1 wrong title.\n")
    after = jobs.read(job["id"])
    assert after["stage"] == "blocked"
    assert "README.md:1" in after["history"][-1]["why"]
    assert flow.listed({})["jobs"][0]["reason"] == after["history"][-1]["why"]
    assert all(message["stage"] != "commit" for message in published(flow))
    assert git(git_repo, "rev-list", "--count", "main..HEAD").strip() == "0"
    assert not (git_repo / "requests").exists()


@pytest.mark.parametrize("text", ["VERDICT: blocker\nThis is bad.\n", "VERDICT: concerns\n- a.py:3 x\n",
                                  "VERDICT: pass\n", "no verdict at all"])
def test_every_other_verdict_goes_on_to_commit(text):
    """A blocker with no place cannot be fixed by a run turn that cannot find it."""
    import contracts
    answer = contracts.read(text, "verdict")
    result = {"ok": True, **({"blocker": "x"} if answer["value"] == "blocker" else {})}
    assert graph.next_stage({"stage": "review"}, graph.DEFAULT, result)[0] == "commit"


def test_every_prove_answer_goes_on_to_review():
    """A contract reports; only the blocker edge changes the route."""
    assert graph.next_stage({"stage": "prove"}, graph.DEFAULT, {"ok": True})[0] == "review"
    assert graph.next_stage({"stage": "prove"}, graph.DEFAULT, {"blocker": "a.py:1"})[0] == "review"


def test_an_interrupt_from_run_blocks_the_job_and_nothing_retries_it(flow, git_repo):
    """Asking the same question twice is not asking."""
    job = at("run", git_repo)
    start(flow, job)
    finish(flow, job["id"], "INTERRUPT: there is no mail credential. Drop the email?")
    after = jobs.read(job["id"])
    assert after["stage"] == "blocked" and after["question"].startswith("there is no mail")
    assert "work" not in document.sections(document.load(job))
    count = len(sends(flow))
    flow.step(published(flow)[-1])                            # the blocked message, delivered
    report = flow.tick()
    assert len(sends(flow)) == count and job["id"] not in report["stuck"] + report["polled"]
    assert jobs.read(job["id"])["stage"] == "blocked"


def test_an_interrupt_after_the_first_line_is_work_not_a_question(flow, git_repo):
    """Only the opening line counts, or "no INTERRUPT: needed" would stop a job."""
    job = at("run", git_repo)
    start(flow, job)
    finish(flow, job["id"], "Added the section. No INTERRUPT: needed.")
    assert jobs.read(job["id"])["stage"] == "prove"


# factory::answer

def test_answer_writes_the_document_and_moves_the_job_back_to_run(flow, git_repo):
    """The checks read the answer, or they grade correct work against a withdrawn requirement."""
    job = at("run", git_repo)
    start(flow, job)
    finish(flow, job["id"], "INTERRUPT: no mail credential. Drop the email?")
    blocked = jobs.read(job["id"])
    got = flow.answer({"job_id": job["id"], "text": "drop the email"})
    after = jobs.read(job["id"])
    assert got["moved"] == "run" and after["epoch"] == blocked["epoch"] + 1
    answer = document.sections(document.load(job))["answer"]
    assert answer == "no mail credential. Drop the email?\n\nAnswer: drop the email"
    assert published(flow)[-1] == {"job_id": job["id"], "stage": "run", "epoch": after["epoch"]}
    flow.step(published(flow)[-1])
    rerun = sends(flow)[-1]
    assert rerun["session_id"] == f"s_{job['id'][:8]}_run_2"     # D1: the next session id
    assert "Answer: drop the email" in rerun["message"]
    assert (ROOT / SPEC).read_text().count("email") == 0         # the spec of record is untouched


def test_answer_refuses_a_job_that_is_not_blocked(flow, git_repo):
    """An answer to a running job would move it out from under its turn."""
    job = at("run", git_repo)
    with pytest.raises(ValueError, match="not blocked"):
        flow.answer({"job_id": job["id"], "text": "go"})
    with pytest.raises(ValueError):
        flow.answer({"job_id": job["id"], "text": "  "})
    with pytest.raises(ValueError, match="no job"):
        flow.answer({"job_id": "f" * 32, "text": "go"})


def test_a_stale_run_message_after_the_answer_does_nothing(flow, git_repo):
    """D2: the loop back to run has a new epoch, so the first pass's message is old."""
    job = at("run", git_repo)
    start(flow, job)
    finish(flow, job["id"], "INTERRUPT: which port?")
    flow.answer({"job_id": job["id"], "text": "3113"})
    assert "skipped" in flow.step({"job_id": job["id"], "stage": "run", "epoch": job["epoch"]})


# Rework on comments

@pytest.mark.parametrize("pr, outcome", [
    ({"merged": True, "state": "closed"}, "merge"),
    ({"state": "MERGED"}, "merge"),
    ({"state": "closed", "comments": [{"id": "1", "body": "fix it"}]}, "close"),
    ({"state": "open", "comments": [{"id": "1", "body": "fix it"}]}, "comment"),
    ({"state": "open", "comments": []}, ""),
    ({"state": "open"}, ""),
])
def test_derive_outcome_checks_merged_then_closed_then_comments(pr, outcome):
    """A merged request that also reads closed must land, and a closed one must not rework."""
    assert actions.derive_outcome({}, pr)["outcome"] == outcome


def test_derive_outcome_reads_the_newest_comment_that_is_not_the_factorys():
    """Without the marker, the factory's own reply reads as feedback and the job reworks forever."""
    pr = {"state": "open", "comments": [
        {"id": "1", "body": "old ask"}, {"id": "2", "body": "rename the heading"},
        {"id": "3", "body": f"{actions.MARKER}\nThe factory read this comment."}]}
    assert actions.derive_outcome({}, pr) == {"outcome": "comment", "brief": "rename the heading",
                                              "comment_id": "2"}


def test_derive_outcome_stops_at_an_answered_comment():
    """Scanning past it would bring back an older comment after two rounds of rework."""
    pr = {"state": "open", "comments": [
        {"id": "1", "body": "first ask, already reworked"},
        {"id": "2", "body": "second ask, already reworked"},
        {"id": "3", "body": f"{actions.MARKER} read it"}]}
    assert actions.derive_outcome({"answered_comment": "2"}, pr) == {"outcome": ""}


def test_derive_outcome_is_pure():
    """Two dicts in, one dict out, so every branch has a test that needs no pull request."""
    job, pr = {"answered_comment": "1"}, {"state": "open", "comments": [{"id": "2", "body": "b"}]}
    before = repr((job, pr))
    actions.derive_outcome(job, pr)
    assert repr((job, pr)) == before


def test_a_local_request_reads_its_comments_from_the_file():
    """Chapter 9's local forge had nowhere for a person to ask for a change."""
    text = forge.request_text({"branch": "b", "title": "t", "repo": "/r"}, "body")
    assert "`### comment`" in text
    text += "\n### comment\nRename the heading.\n\n### comment\n\n### comment\nAnd the port.\n"
    got = forge.local_comments([{"ok": True, "value": {"content": text}}])
    assert got == [{"id": "comment-1", "body": "Rename the heading."},
                   {"id": "comment-3", "body": "And the port."}]
    assert forge.local_comments([{"ok": False, "error": "gone"}]) == []


def test_watch_records_a_comment_for_rework(worker, git_repo):
    """The comment must survive the move, or rework has nothing to work from."""
    job = {"id": "0123abcd" + "0" * 24, "repo": str(git_repo), "branch": "b",
           "request": {"id": "r"}, "settings": {"forge": "local"}}
    worker.answers |= {"coder::read-file": {"content": "status: open\n\n### comment\nRename it.\n"},
                       "shell::exec": {"exit_code": 1, "stdout": "", "stderr": ""}}
    assert actions.watch(worker, job, {}) == {
        "outcome": "comment", "record": {"comment": {"id": "comment-1", "body": "Rename it."}}}


def test_rework_replies_with_the_marker_and_the_comment_is_the_whole_brief(worker):
    """A brief that kept the spec beside the complaint invites the turn to solve the wrong one."""
    worker.answers |= {"worktree::claim": {}, "github::pr::comment": {}}
    job = {"id": "0123abcd" + "0" * 24, "repo": "/r", "worktree_id": "wt_1", "path": "/r/wt",
           "branch": "b", "request": {"id": 7},
           "settings": {"forge": "github", "slug": "you/repo"},
           "comment": {"id": "42", "body": "Rename the heading."}}
    got = actions.rework(worker, job, job["settings"])
    assert got["record"]["brief"] == "Rename the heading."
    assert got["record"]["answered_comment"] == "42" and got["record"]["comment"] is None
    [reply] = [p for fid, p in worker.calls if fid == "github::pr::comment"]
    assert actions.MARKER in reply["body"] and reply["number"] == 7
    assert not any(fid == "worktree::create" for fid, _ in worker.calls)   # the same tree
    assert actions.rework(worker, {**job, "comment": None}, {})["ok"] is False


def test_a_rework_walks_back_to_run_with_two_epochs_and_the_next_session_ids(flow, git_repo):
    """D1 and D2: the chapter's s_<job8>_run_3 and s_<job8>_prove_2, and no stale run message acts."""
    (git_repo / "README.md").write_text("# target\n\n## Ports\n")
    git(git_repo, "commit", "-qam", "Document the ports")   # published, not merged
    job = at("waiting", git_repo, [("plan", "p"), ("work", "w")],
             turns={"plan": 1, "run": 2, "prove": 1, "review": 1},
             request={"id": "r", "url": "r"})
    flow.worker.answers |= {
        "coder::read-file": {"content": "status: open\n\n### comment\nRename the heading.\n"}}
    start(flow, job)                                   # waiting: the poll finds the comment
    reworking = jobs.read(job["id"])
    assert reworking["stage"] == "rework" and reworking["comment"]["body"] == "Rename the heading."
    flow.step(published(flow)[-1])                     # rework
    at_run = jobs.read(job["id"])
    assert (at_run["stage"], at_run["epoch"]) == ("run", job["epoch"] + 2)
    assert at_run["brief"] == "Rename the heading." and at_run["answered_comment"] == "comment-1"
    assert "skipped" in flow.step({"job_id": job["id"], "stage": "run", "epoch": job["epoch"]})
    flow.step(published(flow)[-1])
    run = sends(flow)[-1]
    assert run["session_id"] == f"s_{job['id'][:8]}_run_3" and run["message"] == "Rename the heading."
    finish(flow, job["id"], "Renamed it.")
    assert "brief" not in jobs.read(job["id"])
    flow.step(published(flow)[-1])
    assert sends(flow)[-1]["session_id"] == f"s_{job['id'][:8]}_prove_2"
    # The next poll: the answered comment is the newest, so the job waits.
    assert actions.watch(flow.worker, {**jobs.read(job["id"]), "stage": "waiting"}, {}) == \
        {"outcome": ""}


# The graph

def test_the_shipped_graph_has_the_chapters_stage_keys():
    """A check stage without reset_worktree can leave its own edits in the request."""
    stages = yaml.safe_load((ROOT / "settings" / "pipeline.yaml").read_text())["stages"]
    assert stages["prove"] == {"action": "turn", "phase": "prove", "requires": ["work"],
                               "produces": ["proof"], "contract": "proven",
                               "reset_worktree": True, "next": "review"}
    assert stages["review"] == {"action": "turn", "phase": "review", "requires": ["work"],
                                "produces": ["review"], "contract": "verdict",
                                "reset_worktree": True, "on_blocker": "blocked", "next": "commit"}
    assert stages["run"]["requires"] == ["spec"] and stages["run"]["on_interrupt"] == "blocked"
    assert stages["rework"]["next"] == "run" and stages["blocked"]["on_answer"] == "run"
    order, at_stage = [], "prepare"
    while at_stage in stages and at_stage not in order:
        order.append(at_stage)
        at_stage = stages[at_stage].get("next", "")
    assert order == ["prepare", "plan", "run", "prove", "review", "commit", "publish", "waiting"]


def test_only_an_answer_leads_out_of_blocked():
    """A blocked job has not failed; it waits for a person, and a poll must not move it."""
    assert graph.next_stage({"stage": "blocked"}, graph.DEFAULT, {})[0] == "blocked"
    assert graph.next_stage({"stage": "blocked"}, graph.DEFAULT, {"outcome": "merge"})[0] == "blocked"
    assert graph.next_stage({"stage": "blocked"}, graph.DEFAULT, {"answered": "go"})[0] == "run"


def test_the_guard_is_bound_to_post_generate(factory_main):
    """An underscore spelling registers without error and never fires."""
    bound = {t["type"]: t for t in factory_main.worker.triggers}
    assert bound["harness::hook::post-generate"]["function_id"] == "factory::verdict_guard"
    assert bound["harness::hook::post-generate"]["config"]["on_error"] == "fail_open"
