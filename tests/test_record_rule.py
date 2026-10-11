"""rules/record-is-read-only: a turn never writes under this repository's state/,
and every refusal the ladder makes lands in the record."""

import importlib.util
from pathlib import Path

import ladder
import rules

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("record_read_only", ROOT / "rules" / "record-is-read-only.py")
predicate = importlib.util.module_from_spec(spec)
spec.loader.exec_module(predicate)


def ctx(function_id, root=""):
    return {"rule": "record-is-read-only", "function_id": function_id, "root": root}


def loaded():
    rule, = [r for r in rules.load() if r.id == "record-is-read-only"]
    return rule


def test_the_rule_is_team_layer_rung_three_refuse_with_no_escape():
    """An escape would let the turn that rewrote the record also excuse itself."""
    rule = loaded()
    assert (rule.layer, rule.rungs, rule.policy, rule.escape) == ("team", (3,), "refuse", "")
    assert rule.script.endswith("rules/record-is-read-only.py")
    assert rule.refused == []


def test_a_coder_write_into_the_record_is_refused():
    """The plain case: a turn editing state/audit/audit.jsonl."""
    path = str(ROOT / "state" / "audit" / "audit.jsonl")
    assert predicate.check(path, "{}", ctx("coder::update-file"))


def test_a_relative_path_is_resolved_against_the_turns_root():
    """state/jobs/x.json from this repo's root lands in the record's folder;
    the same text from a worktree lands in the worktree."""
    assert predicate.check("state/jobs/x.json", "", ctx("coder::create-file", str(ROOT)))
    assert predicate.check("../state/x", "", ctx("coder::create-file", str(ROOT / "rules")))
    assert not predicate.check("state/x.json", "", ctx("coder::create-file", "/tmp/worktree"))


def test_a_coder_read_of_state_is_refused_too():
    """coder::* means every coder call: a turn has no reason to read the record."""
    assert predicate.check(str(ROOT / "state" / "audit" / "audit.jsonl"), "",
                           ctx("coder::read-file"))


def test_a_shell_command_naming_state_is_refused_by_string_match():
    """Rung 3 does not parse the shell, so the text is all it can read."""
    assert predicate.check("", "sed -i s/a/b/ state/audit/audit.jsonl", ctx("shell::exec"))
    assert not predicate.check("", "git status", ctx("shell::exec"))


def test_a_path_built_at_runtime_passes_which_is_the_rules_stated_limit():
    """The anchor covers this case, not the rule. The test pins the limit so nobody claims more."""
    assert not predicate.check("", 'd=sta; d="${d}te"; echo x >> "$d/audit/audit.jsonl"',
                               ctx("shell::exec"))


def test_other_functions_are_not_this_rules_business():
    assert not predicate.check(str(ROOT / "state" / "x"), "", ctx("worktree::create"))


def test_decide_refuses_a_shell_call_into_state_and_names_the_rule():
    """Through the ladder's own decide, with the context the gate passes."""
    write = {"function_id": "shell::exec", "path": "", "content": "rm state/audit/audit.jsonl",
             "root": str(ROOT)}
    got = ladder.decide([loaded()], write, rung=3)
    assert got["decision"] == "deny"
    assert got["annotations"] == {"ladder.rule": "record-is-read-only", "ladder.rung": 3}
    assert "can rewrite what the record says it did" in got["reason"]


def audited(worker):
    return [p for fid, p in worker.calls if fid == "audit::append"]


def test_the_gate_records_its_refusal_without_the_content(ladder_main):
    """The harness worker skips post-trigger for a denied call, so the gate is the recorder.
    The content is where a credential would hide, so it never reaches the record."""
    key = "sk-ant-" + "x" * 16
    payload = {"session_id": "s_0123abcd_run_1", "step": 2,
               "metadata": {"fs_scope": {"root": ""}, "job_id": "0123abcd" * 4},
               "call": {"id": "c9", "function_id": "coder::create-file",
                        "arguments": {"path": "a.py", "content": f"KEY = '{key}'"}}}
    got = ladder_main.gate(payload)
    assert got["decision"] == "deny"
    entry, = audited(ladder_main.worker)
    assert entry["kind"] == "ladder.refused" and entry["actor"] == "no-secrets"
    assert entry["subject"] == "0123abcd" * 4
    assert entry["detail"]["function_call_id"] == "c9" and entry["detail"]["rung"] == 3
    assert key not in str(entry)


def test_a_continue_is_not_recorded(ladder_main):
    """Only refusals: a record of every passing call drowns them."""
    ladder_main.gate({"metadata": {"fs_scope": {"root": ""}},
                      "call": {"id": "c1", "function_id": "coder::read-file",
                               "arguments": {"path": "README.md"}}})
    assert audited(ladder_main.worker) == []


def test_the_gate_still_denies_when_the_record_cannot_be_written(ladder_main, capsys):
    """A recorder that fails must never turn a refusal into a pass, and must say it failed."""
    def down(payload):
        raise ConnectionError("audit is down")
    ladder_main.worker.answers["audit::append"] = down
    got = ladder_main.gate({"metadata": {"fs_scope": {"root": ""}},
                            "call": {"id": "c1", "function_id": "shell::exec",
                                     "arguments": {"command": "cat state/audit/audit.jsonl"}}})
    assert got["decision"] == "deny"
    assert "refusal not recorded" in capsys.readouterr().out


def test_a_rung_four_refusal_is_recorded_with_its_rung(ladder_main):
    """Chapter 9's delivery gate refuses through ladder::evaluate, and that lands as ladder.refused too."""
    got = ladder_main.evaluate({"repo": "", "path": "a.py", "rung": 4,
                                "content": "KEY = 'sk-ant-" + "y" * 16 + "'"})
    assert got["decision"] == "deny"
    entry, = audited(ladder_main.worker)
    assert entry["kind"] == "ladder.refused" and entry["detail"]["rung"] == 4
    assert entry["detail"]["path"] == "a.py"
