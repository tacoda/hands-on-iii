"""The one loader for settings/. Every script in scripts/ imports it.

A value comes from a built-in default unless settings/phases.yaml overrides it,
and `effective()` says which, so no default is a magic number.
"""

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

DEFAULTS = {"model": "claude-sonnet-5", "thinking_level": "medium", "max_turns": 50}

def declared() -> dict:
    try:
        return (yaml.safe_load(path().read_text()) or {}).get("defaults") or {}
    except FileNotFoundError:
        return {}

def effective() -> dict[str, tuple[object, str]]:
    given = declared()
    return {key: (given.get(key, default), "settings" if key in given else "built-in")
            for key, default in DEFAULTS.items()}

def path() -> Path:
    # The harness container sets SETTINGS_DIR, so its pre_run checks the file the
    # project runs on, whatever directory the check was started from.
    return Path(os.environ.get("SETTINGS_DIR") or ROOT / "settings") / "phases.yaml"

def check() -> None:
    # A typo like `max_turn` would otherwise run on the default without a word.
    unknown = sorted(set(declared()) - set(DEFAULTS))
    if unknown:
        raise ValueError(f"unknown keys {unknown}")
