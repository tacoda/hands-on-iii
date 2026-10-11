import importlib.util
from pathlib import Path

import rules

ROOT = Path(__file__).resolve().parents[1]
HEAD = "---\nid: r\ndescription: d\nwhy: w\n{extra}\n---\nbody\n"


def rule(extra, script="r.py"):
    return rules.parse(HEAD.format(extra=extra), "r", "team", script, "/x/r.md")


def predicate():
    spec = importlib.util.spec_from_file_location("no_secrets", ROOT / "rules" / "no-secrets.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.check


def test_the_shipped_rule_loads_at_rung_3_with_its_predicate():
    """A rule that loaded as prose would look enforced on the page and refuse nothing."""
    loaded, = [r for r in rules.load() if r.id == "no-secrets"]
    assert loaded.rungs == (3,)
    assert loaded.refused == []
    assert loaded.script.endswith("rules/no-secrets.py")
    assert (loaded.layer, loaded.policy, loaded.escape) == ("team", "refuse", "secret-ok")
    assert loaded.description == "A credential never reaches a file"


def test_the_predicate_refuses_a_literal_key():
    """The whole point of the rule: a key written into a file."""
    found = predicate()("settings.py", "KEY = 'sk-ant-" + "x" * 16 + "'", {})
    assert found == [{"line": 1, "why": "an Anthropic key in source"}]


def test_the_predicate_lets_an_environment_read_through():
    """Firing on the correct pattern teaches the model to avoid it."""
    check = predicate()
    assert check("a.py", "KEY = os.environ['ANTHROPIC_API_KEY']", {}) == []
    assert check("a.py", "# the api key lives in .env", {}) == []


def test_the_predicate_names_the_line_and_catches_a_token_and_a_private_key():
    """A finding with no line number cannot be fixed by the model that wrote it."""
    found = predicate()("a", "ok\n-----BEGIN RSA PRIVATE KEY-----\nghp_" + "a" * 36, {})
    assert [f["line"] for f in found] == [2, 3]


def test_a_mechanical_rung_with_no_script_is_refused_as_prose():
    """Renaming the .py away must show in ladder::list, not leave rung 3 looking carried."""
    got = rule("rung: [3]\npolicy: refuse", script="")
    assert got.rungs == ()
    assert any("rung 3 refused" in r and "prose only" in r for r in got.refused)


def test_ask_is_refused_anywhere_but_rung_3():
    """Only pre-trigger can park a call for a person; ask elsewhere would quietly pass."""
    got = rules.parse(HEAD.format(extra="rung: [0, 3, 4]\npolicy: ask"), "r", "team", "r.py")
    assert 3 in got.rungs
    assert 4 not in got.rungs


def test_rung_4_is_refused_before_chapter_9():
    """Nothing carries rung 4 until the delivery gate exists, so a claim of it is a lie."""
    got = rule("rung: [3, 4]\npolicy: refuse")
    assert got.rungs == (3,)
    assert any("rung 4 refused" in r and "chapter 9" in r for r in got.refused)


def test_an_unknown_policy_carries_nothing():
    """A typo like `refuze` must not decide which way the gate goes."""
    got = rule("rung: [3]\npolicy: refuze")
    assert got.rungs == ()
    assert "refuze" in got.refused[0]


def test_one_bad_file_never_stops_the_others(tmp_path):
    """A broken project rule must not take the team's rules down with it."""
    (tmp_path / "broken.md").write_text("---\nrung: [3\n---\n")
    (tmp_path / "fine.md").write_text(HEAD.format(extra="rung: [3]"))
    (tmp_path / "fine.py").write_text("def check(path, content, context):\n    return []\n")
    loaded = {r.id: r for r in rules.from_dir(tmp_path, "project")}
    assert loaded["broken"].rungs == ()
    assert loaded["broken"].refused
    assert loaded["r"].rungs == (3,)


def test_project_rules_come_from_the_target_repos_agents_rules(tmp_path):
    """The project layer must be read from the repo the turn edits, not this one."""
    (tmp_path / ".agents" / "rules").mkdir(parents=True)
    (tmp_path / ".agents" / "rules" / "local.md").write_text("---\nid: local\n---\n")
    layers = {r.id: r.layer for r in rules.load(str(tmp_path))}
    assert layers["local"] == "project"
    assert layers["no-secrets"] == "team"
