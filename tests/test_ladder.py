import json

import pytest
import yaml

import ladder
import rules

KEY = "KEY = 'sk-ant-" + "x" * 16 + "'"
LEVELS = list(yaml.safe_load((rules.ROOT / "settings" / "oversight.yaml").read_text())["levels"])
ASKED = {"decision": "hold", "policy": "ask", "reason": "The rule asks a person."}


def write(content="", path="settings.py", function_id="coder::create-file"):
    return {"function_id": function_id, "path": path, "content": content}


def hook(oversight=None, root=""):
    meta = {"fs_scope": {"root": root}}
    if oversight is not None:
        meta["oversight"] = oversight
    return {"call": {"id": "c1", "function_id": "coder::create-file",
                     "arguments": {"files": [{"path": "a.py", "content": "x"}]}},
            "metadata": meta, "step": 3}


def shipped():
    return [r for r in rules.load() if r.id == "no-secrets"]


@pytest.mark.parametrize("level", LEVELS + [None, "drak"])
def test_ask_never_becomes_allow(ladder_main, monkeypatch, level):
    """Chapter 6's first invariant, for a rule's own ask: no level, typo, or gap lets the call run."""
    monkeypatch.setattr(ladder_main.ladder, "repo_permissions", lambda repo, w: ASKED)
    got = ladder_main.gate(hook(level))
    assert got["decision"] in ("hold", "deny"), f"oversight {level!r} let an ask through"
    if level != "dark":
        assert got == {"decision": "hold"}, f"oversight {level!r} did not hold"


def test_dark_refuses(ladder_main, monkeypatch):
    """At dark nobody answers a hold, so the call must refuse and say why."""
    monkeypatch.setattr(ladder_main.ladder, "repo_permissions", lambda repo, w: ASKED)
    got = ladder_main.gate(hook("dark"))
    assert got["decision"] == "deny", "dark let an ask through"
    assert got["reason"].startswith("The rule asks a person.")
    assert "Oversight is dark" in got["reason"]


def test_decide_refuses_a_literal_key_in_the_rules_own_words():
    """A refusal that says only "denied" teaches the model to retry instead of adapt."""
    got = ladder.decide(shipped(), write(KEY), rung=3)
    assert got["decision"] == "deny"
    assert got["policy"] == "refuse"
    assert "A credential never reaches a file (no-secrets)" in got["reason"]
    assert "settings.py line 1" in got["reason"]
    assert "rotation" in got["reason"]
    assert "secret-ok" in got["reason"]


def test_decide_lets_an_environment_read_through():
    """Reading the key from the environment is the pattern the rule wants."""
    assert ladder.decide(shipped(), write("KEY = os.environ['K']"), rung=3) == {"decision": "continue"}


def test_the_escape_token_lets_the_write_through():
    """A sanctioned exception that leaves a trace beats an unsanctioned one."""
    got = ladder.decide(shipped(), write(KEY + "  # secret-ok: test fixture"), rung=3)
    assert got == {"decision": "continue"}


def test_a_read_is_never_refused():
    """Refusing reads under a rule about writes is how a ladder gets disabled."""
    assert ladder.decide(shipped(), write("", path=""), rung=3) == {"decision": "continue"}


def test_a_rule_not_carried_at_the_rung_asked_is_skipped():
    """A rule that claims only rung 3 must not be borrowed by the delivery gate at rung 4."""
    only_three = [rules.Rule(**{**vars(r), "rungs": (3,)}) for r in shipped()]
    assert ladder.decide(only_three, write(KEY), rung=4) == {"decision": "continue"}


def test_the_shipped_secret_rule_refuses_at_rung_4():
    """A key that reached a file by a route rung 3 missed must still stop at delivery."""
    assert ladder.decide(shipped(), write(KEY), rung=4)["decision"] == "deny"


def test_a_warn_finding_allows_the_call_and_says_so():
    """warn is counted in chapter 11, so the finding must survive the allow."""
    noisy = rules.Rule(id="w", layer="team", policy="warn", rungs=(3,), script="w.py")
    got = ladder.decide([noisy], write("x"), rung=3, run=lambda r, w: [{"line": 1, "why": "y"}])
    assert got["decision"] == "continue"
    assert got["annotations"]["ladder.warned"] == [{"rule": "w", "line": 1, "why": "y"}]


def test_a_shell_command_is_read_as_content():
    """A heredoc through the shell never touches an editing tool, so the command text is the write."""
    got = ladder.write_of({"function_id": "shell::exec",
                           "arguments": {"command": f"cat > leak.py <<EOF\n{KEY}\nEOF"}})
    assert ladder.decide(shipped(), got, rung=3)["decision"] == "deny"


def test_a_batched_create_reads_every_file():
    """Reading only the first file of a batch would wave the second one through."""
    got = ladder.write_of({"function_id": "coder::create-file", "arguments": {
        "files": [{"path": "a.py", "content": "ok"}, {"path": "b.py", "content": KEY}]}})
    assert ladder.decide(shipped(), got, rung=3)["decision"] == "deny"


def test_a_crashing_predicate_fails_closed_and_says_the_ladder_failed(ladder_main, monkeypatch):
    """A crash borrowing a rule's words tells the model the repo forbade a write it did not."""
    def broken(rule, w):
        raise ZeroDivisionError("division by zero")
    monkeypatch.setattr(ladder_main.ladder, "run", broken)
    payload = hook()
    payload["call"]["arguments"] = {"path": "a.py", "content": KEY}
    got = ladder_main.gate(payload)
    assert got["decision"] == "deny"
    assert got["reason"].startswith("The ladder failed (ZeroDivisionError)")
    assert "No rule refused it" in got["reason"]
    assert "credential" not in got["reason"]


def test_a_failing_rule_is_named_when_it_refuses(ladder_main):
    """The model has to know which rule to satisfy, so the refusal names it."""
    payload = hook()
    payload["call"]["arguments"] = {"path": "a.py", "content": KEY}
    got = ladder_main.gate(payload)
    assert got["decision"] == "deny"
    assert "(no-secrets)" in got["reason"]
    assert got["annotations"] == {"ladder.rule": "no-secrets", "ladder.rung": 3}


def settings_json(tmp_path, permissions):
    (tmp_path / ".agents").mkdir()
    (tmp_path / ".agents" / "settings.json").write_text(json.dumps({"permissions": permissions}))
    return str(tmp_path)


def test_rung_2_refuses_a_denied_command_and_not_a_compound_one(tmp_path):
    """The matcher does not parse the shell; guessing at shell semantics would be another rule."""
    repo = settings_json(tmp_path, {"deny": ["Bash", "Bash(php *)"]})
    php = ladder.write_of({"function_id": "shell::exec", "arguments": {"command": "php artisan migrate"}})
    both = ladder.write_of({"function_id": "shell::exec",
                            "arguments": {"command": "python3 -m pytest && php artisan migrate"}})
    got = ladder.repo_permissions(repo, php)
    assert got["decision"] == "deny"
    assert "`Bash(php *)`" in got["reason"]
    assert ladder.repo_permissions(repo, both) is None


def test_rung_2_asks_for_a_write_under_an_ask_entry(tmp_path):
    """`ask` entries hold the call for a person; they never refuse or allow on their own."""
    repo = settings_json(tmp_path, {"ask": ["Write(prod/**)"]})
    got = ladder.repo_permissions(repo, write("x", path="prod/config/app.yaml"))
    assert got["policy"] == "ask"
    assert ladder.repo_permissions(repo, write("x", path="dev/app.yaml")) is None


def test_a_bare_deny_is_withheld_at_rung_1_and_allow_is_ignored():
    """A repo must not widen its own grant by writing a file, and a whole denied tool is never granted."""
    got = ladder.withheld({"deny": ["Bash", "Bash(php *)"], "allow": ["coder::*"]})
    assert "shell::exec" in got and "shell::exec_bg" in got
    assert not any(f.startswith("coder::") for f in got)


def test_list_reports_rungs_withheld_and_refusals(ladder_main, tmp_path):
    """`ladder::list` is what an operator reads after a rule edit, and what explain is built on."""
    repo = settings_json(tmp_path, {"deny": ["Bash"]})
    got = ladder_main.list_rules({"repo": repo})
    secrets, = [r for r in got["rules"] if r["id"] == "no-secrets"]
    assert secrets["rungs"] == [3, 4]
    assert secrets["predicate"] == "no-secrets.py"
    assert "shell::exec" in got["withheld"]


def test_evaluate_and_the_hook_give_one_answer(ladder_main):
    """Two implementations of one rule disagree, and the agent finds the seam first."""
    got = ladder_main.evaluate({"repo": "", "path": "a.py", "content": KEY, "rung": 3})
    assert got["decision"] == "deny"
    assert "(no-secrets)" in got["reason"]
