"""The one loader for settings/. Every script in scripts/ imports it.

A value comes from a built-in default unless settings/phases.yaml overrides it,
and `effective()` says which, so no default is a magic number.
"""

import os
import sys
import types
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

DEFAULTS = {"model": "claude-sonnet-5", "thinking_level": "medium", "max_turns": 50,
            "provider": "anthropic", "functions": {"allow": []},
            "max_cost_usd": None, "max_total_tokens": None,
            "max_output_tokens": None}

def load() -> dict:
    try:
        return yaml.safe_load(path().read_text()) or {}
    except FileNotFoundError:
        return {}

def read(name: str) -> dict:
    # A missing file is the built-in default, so deleting one never stops a turn.
    try:
        return yaml.safe_load((path().parent / f"{name}.yaml").read_text()) or {}
    except FileNotFoundError:
        return {}

def declared() -> dict:
    return load().get("defaults") or {}

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

def expand(names: list[str], grants: dict) -> list[str]:
    # The harness worker takes flat lists of function ids, so group names stop here.
    ids: list[str] = []
    for name in names:
        if name == "*" or "::" in name:
            found = [name]
        elif name in grants:
            found = grants[name]
        else:
            # Granting nothing for a misspelt group would look like a working phase.
            raise ValueError(f"unknown grant group {name!r}; known: {sorted(grants)}")
        ids += [fid for fid in found if fid not in ids]
    return ids

def for_phase(phase: str, given: dict | None = None) -> dict:
    given = load() if given is None else given
    phases = given.get("phases") or {}
    if phase not in phases:
        raise ValueError(f"unknown phase {phase!r}; known: {sorted(phases)}")
    # One level deep. A phase's `functions` replaces the default whole.
    settings = {**DEFAULTS, **(given.get("defaults") or {}), **phases[phase]}
    fn, grants = settings["functions"], given.get("grants") or {}
    settings["functions"] = {k: expand(fn.get(k) or [], grants)
                             for k in ("allow", "deny")}
    return settings

class _Settings(types.ModuleType):
    # turn.py reads a named file as `settings("oversight")` and a phase as
    # `settings.for_phase(...)`, so the one module answers both spellings.
    def __call__(self, name: str) -> dict:
        return read(name)

sys.modules[__name__].__class__ = _Settings
