"""Print one harness::send payload as JSON. It sends nothing:

    iii trigger harness::send --json "$(python3 scripts/turn.py --phase plan --prompt 'say hello')"

It does set the session's approval-gate mode first, because a mode set after
the send does not reach the calls the turn has already made. And it prints
nothing unless ladder::gate is registered: a dead ladder has no binding, so the
harness worker would run every call with no rule in front of it.
"""

import argparse
import json
import subprocess
import sys
import uuid
from pathlib import Path

import charter
import settings

REPO = "tests/fixtures/sample-repo"
GATE = "ladder::gate"

def send_options(phase: str, workspace: str) -> dict:
    # Without a root the harness worker scopes a new session to its own working
    # directory, so a run grant would edit this repository instead of the target.
    # It reads the root only from metadata.fs_scope.root; options.fs_scope is dropped.
    if not workspace:
        raise ValueError("refusing to send without fs_scope.root")
    values = settings.for_phase(phase)
    options = {key: values[key] for key in ("max_turns", "thinking_level", "functions")}
    options |= {key: values[key] for key in ("max_cost_usd", "max_total_tokens", "max_output_tokens")
                if values[key] is not None}
    options["metadata"] = {"phase": phase, "fs_scope": {"root": workspace}}
    # harness::send reads provider and model at the top level, not in options.
    return {"provider": values["provider"], "model": values["model"], "options": options}

def payload(prompt: str, phase: str, workspace: str) -> dict:
    send = send_options(phase, workspace)
    job = uuid.uuid4().hex[:8]
    send["options"]["metadata"]["job_id"] = job
    return {"session_id": f"s_{job}_{phase}_1", "message": prompt, **send}

def build_send(args) -> dict:
    # The shell worker does not run from this directory, so a relative root
    # would name some other place.
    workspace = str(Path(args.repo).resolve()) if args.repo else ""
    send = payload(args.prompt, args.phase, workspace)
    built = charter.for_repo(Path(args.repo))
    for problem in built.problems:
        print(f"charter: {problem}", file=sys.stderr)  # stdout is the JSON
    if built.pieces:
        send["options"]["system_prompt"] = built.text()
    send["options"]["metadata"]["charter_pieces"] = len(built.pieces)
    return send

ASKS = {"manual", "attended"}  # levels that promise a person will answer


def oversight(asked=None) -> tuple[str, str]:
    given = settings("oversight")
    levels = given.get("levels") or {"supervised": "full"}
    level = str(asked or given.get("default") or "").strip().lower()
    if level not in levels:
        level = "supervised"
    return level, levels.get(level, "full")


def set_mode(session_id: str, level: str, mode: str) -> None:
    body = json.dumps({"session_id": session_id, "mode": mode})
    try:
        done = subprocess.run(["iii", "trigger", "approval::set-mode", "--json", body],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        # No CLI on PATH or no engine answering is a failed set-mode, not a crash.
        done = subprocess.CompletedProcess([], 1, "", str(error))
    print(f"oversight: {level} ({mode}) on {session_id}", file=sys.stderr)
    if done.returncode != 0:
        print(f"oversight: set-mode failed: {done.stderr.strip()}", file=sys.stderr)
        if level in ASKS:
            sys.exit(1)


def ask_engine(function_id: str, body: dict) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["iii", "trigger", function_id, "--json", json.dumps(body)],
                              capture_output=True, text=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as error:
        return subprocess.CompletedProcess([], 1, "", str(error))


def ladder_registered() -> bool:
    # on_error: fail_closed covers a hook that runs and fails, not one that is gone.
    done = ask_engine("engine::functions::info", {"function_id": GATE})
    said = done.stdout + done.stderr
    return done.returncode == 0 and GATE in done.stdout and "function_not_found" not in said


def withheld(root: str) -> list[str] | None:
    done = ask_engine("ladder::list", {"repo": root})
    try:
        return list(json.loads(done.stdout)["withheld"]) if done.returncode == 0 else None
    except (ValueError, KeyError, TypeError):
        return None


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--phase", required=True)
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--oversight", help="manual, attended, supervised, or dark")
    args = parser.parse_args()
    if not ladder_registered():
        sys.exit(f"{GATE} is not registered, so no rule would refuse a call. "
                 "Start it: iii trigger compose::restart worker=ladder")
    # Fail closed: with no repo there are no rules, so print no send at all.
    try:
        send = build_send(args)
    except (ValueError, OSError) as error:
        sys.exit(str(error))
    # Rung 1: what the repo's own settings deny outright is withheld, from the
    # ladder's one list, never from a second copy kept in phases.yaml.
    taken = withheld(send["options"]["metadata"]["fs_scope"]["root"])
    if taken is None:
        sys.exit("ladder::list did not answer, so the repo's withheld functions are unknown")
    deny = send["options"]["functions"].setdefault("deny", [])
    deny += [fid for fid in taken if fid not in deny]
    level, mode = oversight(args.oversight)
    # metadata arrives verbatim at every hook, so chapter 7's ladder reads the
    # level here and keeps no session state.
    send["options"]["metadata"]["oversight"] = level
    set_mode(send["session_id"], level, mode)
    print(json.dumps(send))

if __name__ == "__main__":
    main()
