import pytest
import yaml

import settings


def test_an_empty_settings_directory_runs_on_the_built_in_defaults(settings_dir):
    """Empty settings/ is a working configuration, not an error."""
    assert settings.effective() == {
        "model": ("claude-sonnet-5", "built-in"),
        "thinking_level": ("medium", "built-in"),
        "max_turns": (50, "built-in"),
        "provider": ("anthropic", "built-in"),
        "functions": ({"allow": []}, "built-in"),
        "max_cost_usd": (None, "built-in"),
        "max_total_tokens": (None, "built-in"),
        "max_output_tokens": (None, "built-in"),
    }


def test_an_empty_file_is_the_same_as_no_file(settings_dir):
    """A blank phases.yaml parses to None; it must not crash the loader."""
    (settings_dir / "phases.yaml").write_text("")
    assert settings.declared() == {}


def test_a_key_under_defaults_overrides_only_that_key_and_says_so(settings_dir):
    """Provenance: a value set in settings/ is reported as `settings`, the rest stay built-in."""
    (settings_dir / "phases.yaml").write_text("defaults:\n  max_turns: 20\n")
    got = settings.effective()
    assert got["max_turns"] == (20, "settings")
    assert got["model"] == ("claude-sonnet-5", "built-in")


def test_a_yaml_error_raises_instead_of_reading_as_empty(settings_dir):
    """A broken file read as empty looks like agreement with the built-ins."""
    (settings_dir / "phases.yaml").write_text("defaults\n  max_turns: 20\n")
    with pytest.raises(yaml.YAMLError):
        settings.effective()


def test_check_refuses_an_unknown_key(settings_dir):
    """`max_turn: 20` would otherwise run silently on the default of 50."""
    (settings_dir / "phases.yaml").write_text("defaults:\n  max_turn: 20\n")
    with pytest.raises(ValueError, match=r"unknown keys \['max_turn'\]"):
        settings.check()


def test_check_accepts_every_built_in_key(settings_dir):
    """The check must not refuse the keys it exists to protect."""
    (settings_dir / "phases.yaml").write_text(
        "defaults:\n  model: m\n  thinking_level: low\n  max_turns: 20\n")
    settings.check()


def test_settings_dir_names_where_the_loader_reads(settings_dir):
    """The harness container's pre_run must check the directory compose points it at."""
    assert settings.path() == settings_dir / "phases.yaml"
