import os
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "config.py"


def run(settings_dir, *args):
    env = {**os.environ, "SETTINGS_DIR": str(settings_dir)}
    return subprocess.run([sys.executable, str(SCRIPT), *args], env=env,
                          capture_output=True, text=True)


def test_config_prints_each_value_with_where_it_came_from(settings_dir):
    """The output is how an operator finds the file that set a number."""
    (settings_dir / "phases.yaml").write_text("defaults:\n  max_turns: 20\n")
    out = run(settings_dir)
    assert out.returncode == 0
    assert out.stdout.splitlines() == [
        "  model            claude-sonnet-5      [built-in]",
        "  thinking_level   medium               [built-in]",
        "  max_turns        20                   [settings]",
        "  provider         anthropic            [built-in]",
        "  functions        {'allow': []}        [built-in]",
        "  max_cost_usd     None                 [built-in]",
        "  max_total_tokens None                 [built-in]",
        "  max_output_tokens None                 [built-in]",
    ]


def test_check_passes_on_an_empty_settings_directory(settings_dir):
    """A fresh checkout must come up: pre_run fails the harness container on a non-zero exit."""
    out = run(settings_dir, "--check")
    assert out.returncode == 0
    assert out.stdout == ""


def test_check_exits_non_zero_and_names_the_unknown_key(settings_dir):
    """The pre_run check is what stops `up` before a turn runs on a typo."""
    (settings_dir / "phases.yaml").write_text("defaults:\n  max_turn: 20\n")
    out = run(settings_dir, "--check")
    assert out.returncode != 0
    assert "unknown keys ['max_turn']" in out.stderr


def test_a_yaml_error_fails_the_script_instead_of_printing_defaults(settings_dir):
    """Three [built-in] lines after a syntax error would look correct and be wrong."""
    (settings_dir / "phases.yaml").write_text("defaults\n  max_turns: 20\n")
    out = run(settings_dir)
    assert out.returncode != 0
    assert "[built-in]" not in out.stdout
