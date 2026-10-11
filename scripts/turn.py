"""Print one harness::send payload as JSON. It sends nothing:

    iii trigger harness::send --json "$(python3 scripts/turn.py --phase plan --prompt 'say hello')"
"""

import argparse
import json
import sys
import uuid
from pathlib import Path

import settings

REPO = "tests/fixtures/sample-repo"

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

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--phase", required=True)
    parser.add_argument("--repo", default=REPO)
    parser.add_argument("--prompt", required=True)
    args = parser.parse_args()
    # The shell worker does not run from this directory, so a relative root
    # would name some other place.
    workspace = str(Path(args.repo).resolve()) if args.repo else ""
    try:
        print(json.dumps(payload(args.prompt, args.phase, workspace)))
    except ValueError as error:
        sys.exit(str(error))

if __name__ == "__main__":
    main()
