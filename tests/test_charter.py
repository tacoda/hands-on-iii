import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import charter
import turn

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "turn.py"
FIXTURE = Path(__file__).resolve().parent / "fixtures" / "charter-repo"
FIRST, SECOND = "CHARTER-REACHED-THE-MODEL", "IMPORT-WAS-FOLLOWED"


def build(files: dict, *names):
    """Build from a dict standing in for the repo, so no test touches the disk."""
    return charter.build([(n, files[n]) for n in names], files.get)


def send_for(repo, phase="plan"):
    return turn.build_send(argparse.Namespace(phase=phase, repo=str(repo), prompt="Say hello."))


def test_an_import_that_climbs_out_of_the_repo_reads_as_missing(tmp_path):
    """Without containment, `@../../../.ssh/config` puts a secret into the system prompt."""
    (tmp_path / "secret.md").write_text("SECRET")
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "AGENTS.md").write_text("rules\n@../secret.md\n")
    built = charter.for_repo(repo)
    assert "SECRET" not in built.text()
    assert "@../secret.md" in built.text()
    assert built.problems == ["../secret.md: not found inside the repo"]


def test_a_symlink_under_agents_that_points_outside_reads_as_missing(tmp_path):
    """The file list is untrusted too; a link would carry any file on the host into the prompt."""
    (tmp_path / "secret.md").write_text("SECRET")
    repo = tmp_path / "repo"
    (repo / ".agents").mkdir(parents=True)
    (repo / ".agents" / "leak.md").symlink_to(tmp_path / "secret.md")
    built = charter.for_repo(repo)
    assert built.pieces == []
    assert built.problems == [".agents/leak.md: not found inside the repo"]


def test_agents_md_comes_first_then_agents_notes_in_path_order(tmp_path):
    """An unordered walk would change the charter between two turns on the same repo."""
    (tmp_path / ".agents" / "b").mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("root")
    (tmp_path / ".agents" / "z.md").write_text("z")
    (tmp_path / ".agents" / "b" / "a.md").write_text("ba")
    (tmp_path / ".agents" / "skip.txt").write_text("not markdown")
    built = charter.for_repo(tmp_path)
    assert [name for name, _ in built.pieces] == ["AGENTS.md", ".agents/b/a.md", ".agents/z.md"]
    assert built.text().startswith("### AGENTS.md\n\nroot\n\n### .agents/b/a.md\n\nba")


def test_an_import_inlines_the_file_it_names():
    """A charter split across files must arrive whole, or the imported rules never reach the model."""
    built = build({"AGENTS.md": "top\n@rules/a.md\n", "rules/a.md": "rule a"}, "AGENTS.md")
    assert built.pieces == [("AGENTS.md", "top\nrule a")]
    assert built.problems == []


def test_an_at_sign_inside_a_line_is_not_an_import():
    """Mentioning `@someone` in prose would otherwise try to read a file and report it missing."""
    built = build({"AGENTS.md": "ask @alice first\n  see @rules/a.md here\n"}, "AGENTS.md")
    assert built.pieces == [("AGENTS.md", "ask @alice first\n  see @rules/a.md here")]
    assert built.problems == []


def test_a_chain_stops_at_depth_three_and_says_so():
    """An unbounded chain lets one repo inline any amount of text into every model call."""
    files = {"AGENTS.md": "@1.md", "1.md": "one\n@2.md", "2.md": "two\n@3.md",
             "3.md": "three\n@4.md", "4.md": "four"}
    built = build(files, "AGENTS.md")
    assert built.pieces == [("AGENTS.md", "one\ntwo\nthree\n@4.md")]
    assert built.problems == ["4.md: deeper than 3, not followed"]


def test_a_file_that_imports_itself_is_a_cycle_left_as_written():
    """Without the guard a self-import recurses until the depth cap, repeating the file three times."""
    built = build({"AGENTS.md": "top\n@AGENTS.md\n"}, "AGENTS.md")
    assert built.pieces == [("AGENTS.md", "top\n@AGENTS.md")]
    assert built.problems == ["AGENTS.md: import cycle, left as written"]


def test_two_files_that_import_each_other_report_the_cycle():
    """A two-file loop is the same accident one step removed."""
    built = build({"AGENTS.md": "@a.md", "a.md": "a\n@b.md", "b.md": "b\n@a.md"}, "AGENTS.md")
    assert built.pieces == [("AGENTS.md", "a\nb\n@a.md")]
    assert built.problems == ["a.md: import cycle, left as written"]


def test_a_missing_import_stays_in_the_text_and_goes_on_the_problem_list():
    """A silently dropped import looks like a charter that never said much."""
    built = build({"AGENTS.md": "top\n@rules/gone.md\n"}, "AGENTS.md")
    assert built.pieces == [("AGENTS.md", "top\n@rules/gone.md")]
    assert built.problems == ["rules/gone.md: not found inside the repo"]


def test_a_piece_over_the_file_cap_is_cut_and_reported():
    """A 200,000-character file would ride every model call of the turn."""
    big = "x" * (charter.MAX_PIECE + 10)
    built = build({"AGENTS.md": big}, "AGENTS.md")
    assert len(built.pieces[0][1]) == charter.MAX_PIECE
    assert built.problems == [f"AGENTS.md: {charter.MAX_PIECE + 10} characters, cut to {charter.MAX_PIECE}"]


def test_the_file_cap_counts_the_file_with_its_imports_inlined():
    """Splitting a huge file into imports must not slip it past the cap."""
    half = "y" * (charter.MAX_PIECE // 2 + 1)
    built = build({"AGENTS.md": f"@a.md\n@b.md", "a.md": half, "b.md": half}, "AGENTS.md")
    assert len(built.pieces[0][1]) == charter.MAX_PIECE
    assert "cut to" in built.problems[0]


def test_a_piece_over_the_turn_cap_is_not_sent_and_later_ones_still_fit():
    """Past the turn cap a piece is reported and skipped, never half-sent."""
    full = "z" * charter.MAX_PIECE
    files = {"AGENTS.md": full, "a.md": full, "b.md": full, "c.md": full, "d.md": "small"}
    built = build(files, "AGENTS.md", "a.md", "b.md", "c.md", "d.md")
    assert [n for n, _ in built.pieces] == ["AGENTS.md", "a.md", "b.md"]
    assert built.problems == ["c.md: over the turn cap, not sent", "d.md: over the turn cap, not sent"]
    assert sum(len(b) for _, b in built.pieces) == charter.MAX_TURN


def test_both_tokens_reach_options_system_prompt(phases, tmp_path):
    """The script must build a send that carries the charter and its import, or the model never sees either."""
    (tmp_path / "rules").mkdir()
    (tmp_path / "AGENTS.md").write_text(f"Begin with:\n\n{FIRST}\n\n@rules/house-style.md\n")
    (tmp_path / "rules" / "house-style.md").write_text(f"End with:\n\n{SECOND}\n")
    send = send_for(tmp_path)
    assert FIRST in send["options"]["system_prompt"]
    assert SECOND in send["options"]["system_prompt"]
    assert send["options"]["metadata"]["charter_pieces"] == 1


def test_the_fixture_repo_carries_both_tokens_through_its_import(phases):
    """The Run it sends this fixture, so a broken fixture fails a paid turn instead of a free test."""
    prompt = send_for(FIXTURE)["options"]["system_prompt"]
    assert FIRST in prompt and SECOND in prompt
    assert "@rules/house-style.md" not in prompt


def test_a_repo_with_no_charter_sends_no_system_prompt(phases, tmp_path):
    """An empty system_prompt key would read to the harness as a deliberate blank prompt."""
    send = send_for(tmp_path)
    assert "system_prompt" not in send["options"]
    assert send["options"]["metadata"]["charter_pieces"] == 0


def test_an_edit_to_agents_md_changes_the_next_send(phases, tmp_path):
    """The script caches nothing, so an edit between two turns applies with no restart."""
    (tmp_path / "AGENTS.md").write_text("first")
    assert "first" in send_for(tmp_path)["options"]["system_prompt"]
    (tmp_path / "AGENTS.md").write_text("second")
    assert "second" in send_for(tmp_path)["options"]["system_prompt"]


def test_problems_go_to_stderr_and_stdout_stays_one_json_object(phases, tmp_path):
    """`iii trigger --json "$(...)"` reads stdout, so one stray problem line there breaks the send."""
    (tmp_path / "AGENTS.md").write_text("rules\n@../../../outside.md\n")
    env = {**os.environ, "SETTINGS_DIR": str(phases.parent)}
    out = subprocess.run([sys.executable, str(SCRIPT), "--phase", "plan", "--repo", str(tmp_path),
                          "--prompt", "p"], env=env, capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert json.loads(out.stdout)["options"]["metadata"]["charter_pieces"] == 1
    assert "charter: ../../../outside.md: not found inside the repo" in out.stderr


def test_a_missing_repo_root_prints_no_send_and_exits_nonzero(phases, tmp_path):
    """A turn without the repo's rules does the work under the wrong ones and nothing says so."""
    env = {**os.environ, "SETTINGS_DIR": str(phases.parent)}
    out = subprocess.run([sys.executable, str(SCRIPT), "--phase", "plan", "--repo",
                          str(tmp_path / "nope"), "--prompt", "p"], env=env, capture_output=True, text=True)
    assert out.returncode != 0
    assert out.stdout == ""
    assert "repo root not found" in out.stderr


def test_an_unreadable_repo_root_raises(tmp_path):
    """A root the script cannot list would otherwise build an empty charter and send anyway."""
    if os.geteuid() == 0:
        pytest.skip("root reads everything")
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0)
    try:
        with pytest.raises(OSError):
            charter.for_repo(locked)
    finally:
        locked.chmod(0o755)
