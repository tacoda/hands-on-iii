"""The stage graph: where a job goes next, as a pure function of two dicts.

settings/pipeline.yaml overrides DEFAULT, so an empty settings/ still runs.
Only `load` touches the disk.
"""

import os
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]

DEFAULT = {
    "first": "plan",
    "stages": {
        "plan":    {"action": "stub", "next": "run"},
        "run":     {"action": "stub", "next": "commit"},
        "commit":  {"action": "stub", "next": "publish",
                    "on_refusal": {"goto": "run", "max": 2}},
        "publish": {"action": "stub", "next": "waiting"},
        "waiting": {"action": "watch", "on_merge": "landed", "on_close": "closed",
                    "on_comment": "rework"},
        "rework":  {"action": "stub", "next": "run"},
    },
}

TERMINAL = frozenset({"landed", "closed", "failed"})


def path() -> Path:
    return Path(os.environ.get("SETTINGS_DIR") or ROOT / "settings") / "pipeline.yaml"


def load() -> dict:
    # Read on every call, so an edit to pipeline.yaml applies to the next step
    # without a restart.
    try:
        given = yaml.safe_load(path().read_text()) or {}
    except FileNotFoundError:
        return DEFAULT
    return {"first": given.get("first") or DEFAULT["first"],
            "stages": given.get("stages") or DEFAULT["stages"]}


def next_stage(job: dict, graph: dict, result: dict) -> tuple[str, str]:
    if job["stage"] not in graph["stages"]:
        return "failed", f"unknown stage {job['stage']!r}"
    if result.get("failed"):
        return "failed", f"{job['stage']} failed: {result['failed']}"
    stage = graph["stages"][job["stage"]]
    if "on_merge" in stage:
        target = stage.get(f"on_{result.get('outcome', '')}")
        return (target, "the person acted") if target else (job["stage"], "the job waits")
    if result.get("refused"):
        rule, revisions = stage.get("on_refusal") or {}, job.get("revisions", 0)
        if not rule or revisions >= rule.get("max", 0):
            return "failed", f"refused {revisions + 1} times"
        return rule["goto"], "refused; the refusal is the next brief"
    return stage["next"], f"{job['stage']} finished"
