"""A turn never writes under this repository's state/.

Imports nothing from the ladder worker, so it runs alone:

    python3 rules/record-is-read-only.py 'cat state/audit/audit.jsonl'

The ladder passes the call's function_id and the turn's root in `context`.
"""

import sys
from pathlib import Path

STATE = Path(__file__).resolve().parents[1] / "state"
SHELL = ("shell::exec", "shell::exec_bg")


def lands_in_state(path: str, root: str) -> bool:
    given = Path(path).expanduser()
    full = (given if given.is_absolute() else Path(root or ".") / given).resolve()
    return full == STATE.resolve() or STATE.resolve() in full.parents


def check(path: str, content: str, context: dict) -> list[dict]:
    function_id = str(context.get("function_id") or "")
    if function_id in SHELL:
        # A string match, not a parse: it also refuses a target repository's
        # own state/, and a path built at runtime passes it.
        for number, line in enumerate(content.splitlines(), 1):
            if "state/" in line:
                return [{"line": number, "why": "the command names state/"}]
        return []
    if function_id.startswith("coder::") and path and lands_in_state(path, context.get("root", "")):
        return [{"line": 1, "why": f"{path} is under {STATE}"}]
    return []


if __name__ == "__main__":
    found = 0
    for command in sys.argv[1:]:
        for finding in check("", command, {"function_id": "shell::exec"}):
            print(f"{command}: {finding['why']}")
            found += 1
    sys.exit(1 if found else 0)
