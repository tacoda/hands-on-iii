"""Chapter 12: the grader, the improve lane, accept, and the wiring check. No engine."""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

import improve

ROOT = Path(__file__).resolve().parents[1]
JOB_A, JOB_B = "a48bf8ec" + "0" * 24, "b1c2d3e4" + "1" * 24
RULES = [{"id": "no-secrets", "rungs": [3, 4]}, {"id": "house-style", "rungs": [0]},
         {"id": "record-is-read-only", "rungs": [3]}]


def entry(kind, actor, subject="", **detail):
    return {"kind": kind, "actor": actor, "subject": subject, "detail": detail}


class Engine:
    """call(function_id, payload) for the improve lane; every answer is canned."""

    def __init__(self, **answers):
        self.answers = {"audit::read": {"entries": []}, "ladder::list": {"rules": [], "withheld": []},
                        "engine::functions::info": {"function_id": "ladder::gate"},
                        "approval::set-mode": {"ok": True},
                        "harness::send": {"accepted": True}} | answers
        self.calls = []

    def __call__(self, function_id, payload):
        self.calls.append((function_id, payload))
        answer = self.answers.get(function_id)
        if isinstance(answer, Exception):
            raise answer
        return answer

    def sent(self):
        return [p for f, p in self.calls if f == "harness::send"]


# ------------------------------------------------------------- the grader

def test_the_grader_passes_a_concern_that_names_a_place():
    """A grader that misread the reply would fail the prompt the contract wants."""
    got = improve.grade({"output": "VERDICT: concerns\n\n- app.py:9 money in a float",
                         "arguments": {"contract": "verdict", "equals": "concerns"}})
    assert got["passed"] is True and got["score"] == 1.0


def test_the_grader_grades_the_downgraded_verdict_not_the_claim():
    """Grading the raw claim rewards the exact behavior the contract exists to catch."""
    got = improve.grade({"payload": {"output": "VERDICT: concerns\n\nthe money feels off",
                                     "arguments": {"contract": "verdict", "equals": "concerns"}}})
    assert got["passed"] is False and got["details"]["value"] == "unreadable"


def test_the_grader_reads_a_reply_given_as_blocks():
    """An evaluator that understood one shape would fail a correct answer, and that looks like evidence."""
    output = {"content": [{"type": "thinking", "text": "VERDICT: pass"},
                          {"type": "text", "text": "VERDICT: concerns\n- app.py:9 float"}]}
    got = improve.grade({"output": output, "arguments": {"contract": "verdict", "equals": "concerns"}})
    assert got["passed"] is True


def test_a_grader_that_cannot_run_fails_the_case():
    """A green tick on a case nobody graded is worse than no case."""
    for arguments in ({}, {"contract": "nonsense", "equals": "pass"}):
        got = improve.grade({"output": "VERDICT: pass", "arguments": arguments})
        assert got["passed"] is False and got["score"] == 0.0 and got["reason"]


def test_the_grader_answers_the_same_on_a_second_delivery():
    """Delivery is at-least-once, so a grader that varied would make the report random."""
    payload = {"output": "VERDICT: blocker\n- app.py:9", "arguments": {"contract": "verdict",
                                                                         "equals": "concerns"}}
    assert improve.grade(payload) == improve.grade(payload)


# ------------------------------------------------------------- the evidence

def test_trouble_finds_revisions_refusals_per_rule_and_rung_and_holds():
    """A lane that read only failures would miss the jobs that landed at a cost."""
    jobs = [{"id": JOB_A, "revisions": 2}, {"id": JOB_B, "revisions": 0}]
    entries = [entry("ladder.refused", "no-secrets", JOB_A, rung=3),
               entry("ladder.refused", "no-secrets", JOB_B, rung=3),
               entry("ladder.refused", "no-secrets", JOB_A, rung=4),
               entry("approval.held", "approval-gate", JOB_B, function_id="shell::exec",
                     function_call_id="c1"),
               entry("approval.resolved", "a person", JOB_B, function_call_id="c1", outcome="allow"),
               entry("stage.entered", "factory", JOB_A, stage="run")]
    found = improve.trouble(jobs, entries, [])
    by = {(s["kind"], s["detail"].get("rung")): s for s in found}
    assert by[("revision", None)]["jobs"] == [JOB_A] and by[("revision", None)]["count"] == 2
    assert by[("refusal", "3")]["count"] == 2 and by[("refusal", "3")]["jobs"] == sorted([JOB_A, JOB_B])
    assert by[("refusal", "4")]["count"] == 1
    held = by[("held", None)]
    assert held["detail"] == {"function_id": "shell::exec", "outcomes": {"allow": 1}}
    assert all(s["argues"] for s in found)       # a count alone is not an argument


def test_a_silent_rule_splits_into_never_fired_and_unobservable():
    """One never-fired signal argued for deleting rung-0 rules that refuse nothing by design."""
    jobs = [{"id": JOB_A, "revisions": 0}]
    entries = [entry("ladder.refused", "no-secrets", JOB_A, rung=3)]
    found = {s["kind"]: s for s in improve.trouble(jobs, entries, RULES)}
    assert found["never-fired"]["detail"]["rules"] == ["record-is-read-only"]
    assert found["unobservable"]["detail"]["rules"] == ["house-style"]
    assert "silence is not evidence" in found["unobservable"]["argues"]


def test_a_rule_with_no_carried_rung_counts_as_prose():
    """max() of an empty rung list would crash the whole read."""
    found = improve.trouble([{"id": JOB_A}], [], [{"id": "loose", "rungs": []}])
    assert [s["kind"] for s in found] == ["unobservable"]


def test_silence_over_no_jobs_is_no_signal():
    """A fresh clone would otherwise pay for a turn that argues about rules nobody tried."""
    assert improve.trouble([], [], RULES) == []


def test_a_clean_record_runs_no_turn(tmp_path):
    """A turn handed no evidence produces proposals anyway."""
    (tmp_path / "repo").mkdir()
    engine = Engine(**{"ladder::list": {"rules": RULES, "withheld": []}})
    got = improve.start(engine, tmp_path, str(tmp_path / "repo"), [])
    assert "skipped" in got and engine.sent() == []
    assert not (tmp_path / "state").exists()


def test_an_unread_record_is_not_a_clean_one(tmp_path):
    """An audit worker that is down would read as zero refusals."""
    (tmp_path / "repo").mkdir()
    engine = Engine(**{"audit::read": RuntimeError("no worker")})
    with pytest.raises(RuntimeError, match="audit::read did not answer"):
        improve.start(engine, tmp_path, str(tmp_path / "repo"), [{"id": JOB_A, "revisions": 1}])
    assert engine.sent() == []


def test_trouble_sends_one_turn_with_the_evidence_and_a_ladder_check(tmp_path):
    """No improve turn starts with no rule in front of it, and its brief names the jobs."""
    (tmp_path / "repo").mkdir()
    engine = Engine()
    got = improve.start(engine, tmp_path, str(tmp_path / "repo"), [{"id": JOB_A, "revisions": 1}])
    [send] = engine.sent()
    assert send["session_id"] == got["session_id"] == f"s_{got['run_id']}_improve_1"
    assert send["idempotency_key"] == send["session_id"]
    assert "a48bf8ec" in send["message"] and "revision" in send["message"]
    assert send["options"]["metadata"]["phase"] == "improve"
    order = [f for f, _ in engine.calls]
    assert order.index("engine::functions::info") < order.index("harness::send")
    run = improve.read(tmp_path, got["run_id"])
    assert run["state"] == "running" and run["jobs"] == [JOB_A]


def test_no_improve_turn_without_the_ladder(tmp_path):
    """A dead ladder has no binding, so the harness worker would run every call."""
    (tmp_path / "repo").mkdir()
    engine = Engine(**{"engine::functions::info": {"error": "function_not_found"}})
    with pytest.raises(RuntimeError, match="ladder::gate"):
        improve.start(engine, tmp_path, str(tmp_path / "repo"), [{"id": JOB_A, "revisions": 1}])
    assert engine.sent() == []


# ------------------------------------------------------------- proposals

TRACED = """## Require Decimal for money

- lane: charter
- kind: rule
- target: AGENTS.md
- action: add
- evidence: job a48bf8ec, revision
- why: the run phase wrote a float total and the gate refused it.
  A rule the run turn reads would have stopped it.

Money is a Decimal, never a float.
"""
UNTRACED = """## Add a linter

- lane: charter
- kind: hook
- target: .agents/hooks
- action: add
- why: linters are good.
"""
WRONG_JOB = UNTRACED.replace("- why", "- evidence: job ffffffff\n- why")


def test_a_proposal_with_no_evidence_is_dropped_and_the_reason_kept():
    """A lane that silently discards half its output looks like a lane that found less."""
    staged, dropped = improve.parse(TRACED + UNTRACED + WRONG_JOB, [JOB_A], ["revision"])
    assert [p["title"] for p in staged] == ["Require Decimal for money"]
    assert len(dropped) == 2 and all("no evidence" in d for d in dropped)


def test_a_staged_proposal_keeps_its_fields_and_what_it_traced_to():
    """A why cut at its first line loses the half that says what the change would have changed."""
    [p], _ = improve.parse(TRACED, [JOB_A], ["revision"])
    assert (p["lane"], p["kind"], p["action"], p["target"]) == ("charter", "rule", "add", "AGENTS.md")
    assert p["why"].endswith("would have stopped it.")
    assert p["traced"] == [JOB_A, "revision"]
    assert p["body"] == "Money is a Decimal, never a float."


def test_a_proposal_with_an_unknown_lane_or_action_is_dropped():
    """The parser never repairs a proposal into one the turn did not write."""
    bad = TRACED.replace("lane: charter", "lane: everywhere").replace("action: add", "action: rewrite")
    staged, [why] = improve.parse(bad, [JOB_A], ["revision"])
    assert staged == [] and "lane" in why and "action" in why


def staged_run(root, text=TRACED):
    engine = Engine()
    (root / "repo").mkdir(exist_ok=True)
    run = improve.start(engine, root, str(root / "repo"), [{"id": JOB_A, "revisions": 1}])
    done = improve.completed(root, {"session_id": run["session_id"], "status": "completed",
                                    "result": {"content": [{"type": "text", "text": text}]}})
    return run["run_id"], done


def test_a_completed_improve_turn_stages_what_traces(tmp_path):
    """The run file is where a person reads what the lane found and what it dropped."""
    run_id, done = staged_run(tmp_path, TRACED + UNTRACED)
    assert done == {"run_id": run_id, "state": "staged", "staged": 1, "dropped": 1}
    listed = improve.listed(tmp_path)["runs"]
    assert listed[0]["proposals"][0]["evidence"] == "job a48bf8ec, revision"
    assert improve.completed(tmp_path, {"session_id": f"s_{run_id}_improve_1",
                                        "status": "completed"}) == {"skipped": "a stale completion"}


def test_another_session_is_not_an_improve_run(tmp_path):
    """Every turn on the engine reaches turn_completed, so the lane must say "not mine"."""
    assert improve.completed(tmp_path, {"session_id": "s_0123abcd_plan_1"}) == {}
    assert improve.completed(tmp_path, {"session_id": "s_0123abcd_improve_1"}) == {}


# ------------------------------------------------------------- accept

def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*")
            if p.is_file() and "state" not in p.relative_to(root).parts}


def test_accept_writes_one_spec_and_nothing_else(tmp_path):
    """A factory that can edit its own gates has no gates."""
    run_id, _ = staged_run(tmp_path)
    (tmp_path / "AGENTS.md").write_text("# charter\n")
    before = tree(tmp_path)
    got = improve.accept(tmp_path, run_id, 0)
    after = tree(tmp_path)
    assert got == {"spec": str(tmp_path / "specs" / "require-decimal-for-money.md"),
                   "next": "factory::submit"}
    assert set(after) - set(before) == {"specs/require-decimal-for-money.md"}
    assert {k: v for k, v in after.items() if k in before} == before
    spec = Path(got["spec"]).read_text()
    assert spec.startswith("# Require Decimal for money\n") and "a48bf8ec" in spec


def test_a_double_accept_returns_the_first_spec(tmp_path):
    """A double click must not open two jobs for one decision."""
    run_id, _ = staged_run(tmp_path)
    first = improve.accept(tmp_path, run_id, 0)
    again = improve.accept(tmp_path, run_id, 0)
    assert again == {"already": first["spec"]}
    assert [p.name for p in (tmp_path / "specs").iterdir()] == [Path(first["spec"]).name]


def test_accept_never_overwrites_a_spec_with_the_same_name(tmp_path):
    """Somebody may already have submitted the spec that holds this name."""
    run_id, _ = staged_run(tmp_path)
    (tmp_path / "specs").mkdir()
    (tmp_path / "specs" / "require-decimal-for-money.md").write_text("mine\n")
    got = improve.accept(tmp_path, run_id, 0)
    assert got["spec"].endswith(f"require-decimal-for-money-{run_id}.md")
    assert (tmp_path / "specs" / "require-decimal-for-money.md").read_text() == "mine\n"


@pytest.mark.parametrize("run_id, index", [("../../etc", 0), ("deadbeef", 0), (None, 0)])
def test_accept_refuses_a_run_that_is_not_there(tmp_path, run_id, index):
    """A run id from a payload never names a path of its own."""
    with pytest.raises(ValueError):
        improve.accept(tmp_path, run_id, index)


def test_accept_refuses_an_index_out_of_range(tmp_path):
    run_id, _ = staged_run(tmp_path)
    for index in (1, -1, "0", True):
        with pytest.raises(ValueError):
            improve.accept(tmp_path, run_id, index)


# ------------------------------------------------------------- the wiring

def registered(factory_main, ladder_main):
    """What the four processes would register: the two Python workers as
    imported, and the two TypeScript ones read from their sources."""
    workers = [factory_main.worker, ladder_main.worker]
    functions = [{"function_id": f} for w in workers for f in w.functions]
    bound = [{"function_id": t["function_id"]} for w in workers for t in w.triggers]
    for path in ("workers/audit/src/index.ts", "workers/ladder-explain-ts/src/index.ts"):
        text = (ROOT / path).read_text()
        functions += [{"function_id": f} for f in re.findall(r'registerFunction\("([^"]+)"', text)]
        bound += [{"function_id": f} for f in re.findall(r'function_id: "([^"]+)",\s*\n?\s*config',
                                                          text)]
    return functions, bound


def test_every_function_the_three_workers_register_is_reached(factory_main, ladder_main):
    """A mechanism tested and connected to nothing passes every unit test."""
    functions, bound = registered(factory_main, ladder_main)
    assert {"factory::grade", "audit::read", "ladder::explain"} <= {f["function_id"] for f in functions}
    got = improve.report(functions, bound, improve.called_ids(ROOT), RULES)
    assert got == {"ok": True, "unwired": [], "problems": [],
                   "functions": len(functions), "bound": len(bound)}


def test_an_unbound_function_fails_the_wiring_and_is_named(factory_main, ladder_main):
    """Run it's break: delete the turn-completed binding and every unit test still passes."""
    functions, bound = registered(factory_main, ladder_main)
    bound = [t for t in bound if t["function_id"] != "factory::turn_completed"]
    got = improve.report(functions, bound, improve.called_ids(ROOT), [])
    assert got["ok"] is False and got["unwired"] == ["factory::turn_completed"]


def test_a_missing_ladder_gate_fails_the_wiring(factory_main, ladder_main):
    """A dead ladder has no binding, so the harness worker calls no hook and every call runs."""
    functions, bound = registered(factory_main, ladder_main)
    functions = [f for f in functions if f["function_id"] != "ladder::gate"]
    got = improve.report(functions, bound, improve.called_ids(ROOT), [])
    assert got["ok"] is False and "ladder::gate is not registered" in got["problems"]


def test_a_rule_claiming_an_uncarried_rung_fails_the_wiring(factory_main, ladder_main):
    """Never claim a rung you do not carry, applied to the check itself."""
    functions, bound = registered(factory_main, ladder_main)
    rules = [{"id": "ci-only", "rungs": [], "refused": ["ci-only: rung 5 refused: rung 5 is CI"]}]
    got = improve.report(functions, bound, improve.called_ids(ROOT), rules)
    assert got["ok"] is False and got["problems"] == ["ci-only: rung 5 refused: rung 5 is CI"]


def test_a_name_found_only_in_tests_or_a_registration_is_no_caller(tmp_path):
    """A test is not a caller, and registering a function is not calling it."""
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text('call("factory::orphan", {})\n')
    (tmp_path / "workers" / "x").mkdir(parents=True)
    (tmp_path / "workers" / "x" / "main.py").write_text(
        'worker.register_function("factory::orphan", orphan,\n    description="x")\n'
        'worker.register_trigger({"type": "cron", "function_id": "factory::orphan",\n'
        '                         "config": {"expression": "0 * * * * *"}})\n'
        'print("factory: cron -> factory::orphan")\n'
        'call("audit::summary", {"by": []})\n')
    called = improve.called_ids(tmp_path)
    assert "factory::orphan" not in called and "audit::summary" in called
    got = improve.report([{"function_id": "ladder::gate"}, {"function_id": "factory::orphan"}],
                         [{"function_id": "ladder::gate"}], called, [])
    assert got["unwired"] == ["factory::orphan"]


def test_the_wiring_asks_the_engine_and_fails_on_no_answer(tmp_path):
    """An engine that answered nothing would otherwise report every function wired."""
    engine = Engine(**{"engine::functions::list": RuntimeError("down")})
    got = improve.wiring(engine, tmp_path)
    assert got["ok"] is False and got["problems"][0].startswith("engine::functions::list")
    engine = Engine(**{"engine::functions::list": {"functions": [{"function_id": "ladder::gate",
                                                                  "worker_name": "ladder"}]},
                       "engine::registered-triggers::list": {"triggers": [
                           {"function_id": "ladder::gate", "trigger_type": "harness::hook::pre-trigger"}]}})
    assert improve.wiring(engine, tmp_path)["ok"] is True


# ------------------------------------------------------------- the eval case

def test_compose_pins_the_eval_worker_exactly():
    """An experimental worker on a floating version changes its reports under you."""
    compose = yaml.safe_load((ROOT / "worker-compose.yaml").read_text())["containers"]
    assert compose["eval"] == {"worker": "package://eval", "version": "0.2.19"}


def test_every_eval_case_names_its_grader_and_changes_one_dimension():
    """An A/B that changes two things says something moved and never which."""
    cases = sorted((ROOT / "evals").glob("*.json"))
    assert cases
    for case in cases:
        data = json.loads(case.read_text())
        assert data["dimension"] == "prompt"
        assert data["evaluator"]["function_id"] == "factory::grade"
        assert "prompt" not in data["control"] and "prompt" not in data["treatment"]
        assert set(data["evaluator"]["arguments"]) == {"contract", "equals"}


@pytest.mark.skipif(not shutil.which("jq"), reason="needs jq")
def test_the_eval_script_fills_both_prompts_from_head_and_the_working_copy():
    """Both halves from one file would make every eval a tie."""
    done = subprocess.run([str(ROOT / "scripts" / "eval.sh"), "--print",
                           "evals/review-names-a-place.json"],
                          cwd=ROOT, capture_output=True, text=True, check=True)
    request = json.loads(done.stdout)
    head = subprocess.run(["git", "show", "HEAD:prompts/review.md"], cwd=ROOT,
                          capture_output=True, text=True, check=True).stdout
    case = json.loads((ROOT / "evals" / "review-names-a-place.json").read_text())
    assert request["control"]["prompt"] == head + case["input"]
    assert request["treatment"]["prompt"] == (ROOT / "prompts" / "review.md").read_text() + case["input"]
    assert "input" not in request


def test_the_case_fails_a_review_that_names_no_place():
    """A case is worth keeping only if it can fail."""
    case = json.loads((ROOT / "evals" / "review-names-a-place.json").read_text())
    args = case["evaluator"]["arguments"]
    assert improve.grade({"output": "VERDICT: concerns\n- app.py:9 a float", "arguments": args})["passed"]
    assert not improve.grade({"output": "VERDICT: concerns\nmoney is off", "arguments": args})["passed"]
