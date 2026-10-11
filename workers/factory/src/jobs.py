"""Job records: one JSON file per job under state/jobs/, written atomically.

The factory is the only process that writes here. `cat state/jobs/<id>.json`
reads a record with no engine running. `moved` and `session_id` are pure;
`write`, `read`, `all_jobs`, and `parse_session` touch the folder.
"""

import json
import os
import re
import tempfile
import time
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
JOB_ID = re.compile(r"^[0-9a-f]{32}$")
SESSION = re.compile(r"^s_([0-9a-f]{8})_([a-z][a-z0-9-]*)_([1-9][0-9]*)$")


def folder() -> Path:
    return Path(os.environ.get("FACTORY_JOBS") or ROOT / "state" / "jobs")


def new(spec: str, repo: str, first: str, **options) -> dict:
    now = time.time()
    return {"id": uuid.uuid4().hex, "spec": spec, "repo": repo, "stage": first,
            "epoch": 0, "revisions": 0, "session_id": None, "turns": {},
            "history": [], "options": options, "created": now, "updated": now}


def moved(job: dict, to: str, why: str, revision: bool = False) -> dict:
    epoch = job["epoch"] + 1
    now = time.time()
    entry = {"from": job["stage"], "to": to, "why": why, "epoch": epoch, "at": now}
    # Clearing session_id is what makes a second delivery of the same
    # completion stale: nothing matches it any more.
    return {**job, "stage": to, "epoch": epoch, "session_id": None, "updated": now,
            "revisions": job.get("revisions", 0) + (1 if revision else 0),
            "history": [*job.get("history", []), entry]}


def session_id(job: dict, phase: str, n: int) -> str:
    return f"s_{job['id'][:8]}_{phase}_{n}"


def write(job: dict) -> None:
    where = folder()
    where.mkdir(parents=True, exist_ok=True)
    # Same directory, so os.replace is a rename on one filesystem: atomic.
    with tempfile.NamedTemporaryFile("w", dir=where, suffix=".tmp", delete=False) as tmp:
        json.dump(job, tmp, indent=2, sort_keys=True)
        tmp.flush()
        # Without fsync a power cut can leave the rename pointing at an empty file.
        os.fsync(tmp.fileno())
    os.replace(tmp.name, where / f"{job['id']}.json")


def read(job_id) -> dict | None:
    # A job id arrives from a queue message, so it never names a path of its own.
    if not isinstance(job_id, str) or not JOB_ID.match(job_id):
        return None
    try:
        return json.loads((folder() / f"{job_id}.json").read_text())
    except FileNotFoundError:
        return None


def all_jobs() -> list[dict]:
    if not folder().is_dir():
        return []
    found = [read(p.stem) for p in folder().glob("*.json")]
    return sorted((j for j in found if j), key=lambda j: j["created"], reverse=True)


def parse_session(session_id) -> str | None:
    """The job a factory session belongs to, or None for anyone else's session.

    scripts/turn.py mints the same shape with a random prefix, so a session
    that parses but names no job here is someone else's too.
    """
    match = SESSION.match(session_id) if isinstance(session_id, str) else None
    if not match or not folder().is_dir():
        return None
    owners = [p.stem for p in folder().glob(f"{match.group(1)}*.json") if JOB_ID.match(p.stem)]
    return owners[0] if len(owners) == 1 else None
