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
