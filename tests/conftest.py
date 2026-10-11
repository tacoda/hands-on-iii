import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))


@pytest.fixture
def settings_dir(tmp_path, monkeypatch):
    """An empty settings/ for one test, so the repo's own files never leak in."""
    monkeypatch.setenv("SETTINGS_DIR", str(tmp_path))
    return tmp_path


@pytest.fixture
def phases(settings_dir):
    """A small phases.yaml in the test's own settings/, so a test never reads the repo's file."""
    (settings_dir / "phases.yaml").write_text(
        "grants:\n  discovery: [engine::functions::list]\n"
        "phases:\n  plan: {}\n")
    return settings_dir / "phases.yaml"


@pytest.fixture
def fake_iii(tmp_path):
    """A stand-in `iii` on PATH that logs its argv, so no test ever reaches an engine."""
    bin_dir = tmp_path / "fake-bin"
    bin_dir.mkdir()
    log = tmp_path / "iii.log"
    script = bin_dir / "iii"
    script.write_text('#!/bin/sh\necho "$@" >> "$III_LOG"\n'
                      'if [ -n "$III_FAIL" ]; then echo "engine down" >&2; exit 1; fi\n')
    script.chmod(0o755)
    return {"bin": bin_dir, "log": log}
