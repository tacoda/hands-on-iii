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
