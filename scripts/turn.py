"""Print one harness::send payload as JSON. It sends nothing:

    iii trigger harness::send --json "$(python3 scripts/turn.py --prompt 'say hello')"
"""

import argparse
import json
import uuid

import settings

# Only a name until chapter 4 gives phases their own settings.
PHASE = "chat"

def payload(prompt: str, phase: str) -> dict:
    values = {key: value for key, (value, _) in settings.effective().items()}
    job = uuid.uuid4().hex[:8]
    return {
        "session_id": f"s_{job}_{phase}_1",
        "message": prompt,
        "provider": "anthropic",
        "model": values["model"],
        "options": {
            "max_turns": values["max_turns"],
            "thinking_level": values["thinking_level"],
            "functions": {"allow": []},
            "metadata": {"job_id": job, "phase": phase},
        },
    }

def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prompt", required=True)
    print(json.dumps(payload(parser.parse_args().prompt, PHASE)))

if __name__ == "__main__":
    main()
