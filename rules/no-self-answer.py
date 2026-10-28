"""A turn never answers a hold.

Imports nothing from the ladder worker, so it runs alone:

    python3 rules/no-self-answer.py some_file.sh
"""

import re
import sys

PATTERNS = [re.compile(p) for p in (
    r"\b(approval|ladder|harness::function)::resolve\b",   # the engine, directly
    r":3111\b", r"/api/holds",                              # the API, around Caddy
)]


def check(path: str, content: str, context: dict) -> list[dict]:
    return [{"line": n, "why": "a turn may not answer a hold"}
            for n, line in enumerate(content.splitlines(), 1)
            if any(p.search(line) for p in PATTERNS)]


if __name__ == "__main__":
    found = 0
    for name in sys.argv[1:]:
        with open(name) as handle:
            for finding in check(name, handle.read(), {}):
                print(f"{name}:{finding['line']}: {finding['why']}")
                found += 1
    sys.exit(1 if found else 0)
