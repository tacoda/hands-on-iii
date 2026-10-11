"""settings/repos.toml: each target repository's convention, as a value.

`resolve` is pure once given the parsed file; only `load` touches the disk.
The factory copies what `resolve` returns onto the job at creation, so a
rework weeks later rebuilds the job's original environment.
"""

import os
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

DEFAULTS = {"forge": "local", "base": "main", "prepare": "", "concurrency": 1,
            "branch_prefix": "factory/", "slug": ""}
FORGES = ("local", "github")


def path() -> Path:
    return Path(os.environ.get("SETTINGS_DIR") or ROOT / "settings") / "repos.toml"


def load() -> dict:
    try:
        return tomllib.loads(path().read_text())
    except FileNotFoundError:
        return {}


def resolve(repo: str, given: dict | None = None) -> dict:
    """The settings for one repository: built-in, then [defaults], then its own table."""
    given = load() if given is None else given
    own = (given.get("repos") or {}).get(str(repo)) or {}
    settings = {**DEFAULTS, **(given.get("defaults") or {}), **own}
    if settings["forge"] not in FORGES:
        raise ValueError(f"{repo}: forge {settings['forge']!r} is not one of {', '.join(FORGES)}")
    if settings["forge"] == "github" and "/" not in str(settings["slug"]):
        raise ValueError(f"{repo}: forge = \"github\" needs slug = \"owner/name\"")
    return settings
