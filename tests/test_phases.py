from fnmatch import fnmatchcase
from pathlib import Path

import pytest
import yaml

import settings

FILE = Path(__file__).resolve().parents[1] / "settings" / "phases.yaml"
REAL = yaml.safe_load(FILE.read_text())
EDITING = REAL["grants"]["editing"]
DISCOVERY = REAL["grants"]["discovery"]
CHECKS = ("plan", "prove", "review")


def dispatched(fid: str, grant: dict) -> bool:
    """The harness worker's policy: an allow-glob match and no deny-glob match."""
    return (any(fnmatchcase(fid, g) for g in grant["allow"])
            and not any(fnmatchcase(fid, g) for g in grant["deny"]))


@pytest.mark.parametrize("phase", CHECKS)
def test_no_check_phase_in_the_real_file_holds_an_editor(phase):
    """A review that can edit fixes the typo it should report, then calls the diff clean."""
    grant = settings.for_phase(phase, REAL)["functions"]
    assert not set(EDITING) & set(grant["allow"])
    assert not any(dispatched(fid, grant) for fid in EDITING)


def test_the_plan_phase_holds_no_editor_and_can_still_read():
    """A plan reads before it decides, so the plan names real files, but it never edits."""
    grant = settings.for_phase("plan", REAL)["functions"]
    assert dispatched("coder::read-file", grant)
    assert not any(fid.startswith("shell::") for fid in grant["allow"])
    assert not any(dispatched(fid, grant) for fid in EDITING)


def test_prove_keeps_its_deny_on_the_editors():
    """Prove allows `coder::*` by glob; only its deny takes the editors back."""
    assert settings.for_phase("prove", REAL)["functions"]["deny"] == EDITING


def test_deny_beats_allow_when_a_glob_grants_an_editor():
    """`coder::*` matches every editor; a deny that lost to it would hand prove an editor."""
    grant = settings.for_phase("prove", REAL)["functions"]
    assert any(fnmatchcase("coder::update-file", g) for g in grant["allow"])
    assert not dispatched("coder::update-file", grant)
    assert dispatched("coder::read-file", grant)


@pytest.mark.parametrize("phase", sorted(REAL["phases"]))
def test_every_phase_that_holds_a_tool_also_holds_discovery(phase):
    """Tools without engine::functions::list are tools the model cannot find, so the turn holds nothing."""
    allow = settings.for_phase(phase, REAL)["functions"]["allow"]
    assert allow
    assert set(DISCOVERY) <= set(allow)


def test_provider_and_model_are_set_per_phase():
    """Plan spends the strong model once; run moves to DeepSeek; neither leaks into the other."""
    plan, run = settings.for_phase("plan", REAL), settings.for_phase("run", REAL)
    assert (plan["provider"], plan["model"]) == ("anthropic", "claude-opus-5")
    assert (run["provider"], run["model"]) == ("deepseek", "deepseek-flash")
    review = settings.for_phase("review", REAL)
    assert (review["provider"], review["model"]) == ("openai", "gpt-5.2")


def test_a_phase_functions_replaces_the_default_whole_never_merged():
    """Moving `editing` into defaults must not hand every check phase an editor."""
    given = {**REAL, "defaults": {**REAL["defaults"],
                                  "functions": {"allow": ["discovery", "read-only", "editing"]}}}
    for phase in CHECKS:
        assert not set(EDITING) & set(settings.for_phase(phase, given)["functions"]["allow"])


def test_a_phase_without_functions_inherits_the_default_grant_whole():
    """The merge is one level deep: no `functions` key means the default list, not an empty one."""
    given = {"grants": {"g": ["a::b"]}, "defaults": {"functions": {"allow": ["g"]}},
             "phases": {"p": {"max_turns": 5}}}
    got = settings.for_phase("p", given)
    assert got["functions"] == {"allow": ["a::b"], "deny": []}
    assert got["max_turns"] == 5


def test_the_merge_is_one_level_deep_for_scalars_too():
    """A phase that sets max_turns keeps the default model."""
    given = {"defaults": {"model": "m"}, "phases": {"p": {"max_turns": 5}}}
    got = settings.for_phase("p", given)
    assert (got["model"], got["max_turns"]) == ("m", 5)


def test_the_built_in_grant_is_empty():
    """With no grant anywhere, the empty allow denies every call; it never falls back to default_functions."""
    assert settings.for_phase("p", {"phases": {"p": {}}})["functions"] == {"allow": [], "deny": []}


def test_ids_globs_and_a_bare_star_pass_through_unchanged():
    """Only bare names are groups; anything with `::` is the harness worker's to match."""
    assert settings.expand(["*", "coder::*", "shell::exec"], {}) == ["*", "coder::*", "shell::exec"]


def test_an_unknown_group_raises_instead_of_granting_nothing():
    """A typo like `read-onyl` would otherwise send a phase that can do nothing, in silence."""
    with pytest.raises(ValueError, match="unknown grant group 'read-onyl'"):
        settings.for_phase("plan", {"phases": {"plan": {"functions": {"allow": ["read-onyl"]}}}})


def test_an_unknown_phase_raises_and_names_the_known_ones():
    """`reveiw` on the bare defaults would run and look like it worked."""
    with pytest.raises(ValueError, match=r"unknown phase 'reveiw'; known: \['plan', 'prove', 'review', 'run'\]"):
        settings.for_phase("reveiw", REAL)


def test_for_phase_reads_the_file_fresh_on_every_call(settings_dir):
    """An edit to phases.yaml changes the next turn with no restart."""
    (settings_dir / "phases.yaml").write_text("phases:\n  p: {max_turns: 5}\n")
    assert settings.for_phase("p")["max_turns"] == 5
    (settings_dir / "phases.yaml").write_text("phases:\n  p: {max_turns: 6}\n")
    assert settings.for_phase("p")["max_turns"] == 6


def test_the_real_file_passes_the_pre_run_check(monkeypatch):
    """`check()` must accept the keys the defaults block now carries, or compose never starts."""
    monkeypatch.setenv("SETTINGS_DIR", str(FILE.parent))
    settings.check()
