"""A credential never reaches a file.

Imports nothing from the ladder worker, so it runs alone:

    python3 rules/no-secrets.py some_file.py
"""

import re
import sys

# Narrow on purpose: a pattern that fires on the word "key" fires on honest
# code too, and a rule people switch off is worse than no rule.
PATTERNS = (
    (re.compile(r"sk-ant-[A-Za-z0-9_-]{8,}"), "an Anthropic key"),
    (re.compile(r"ghp_[A-Za-z0-9]{20,}"), "a GitHub token"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
)

# Reading a key from the environment is the correct pattern. Firing on it
# would teach the model to avoid it.
FROM_ENV = re.compile(r"os\.environ|getenv|process\.env|\$\{?[A-Z_]+\}?")


def check(path: str, content: str, context: dict) -> list[dict]:
    findings = []
    for number, line in enumerate(content.splitlines(), 1):
        if FROM_ENV.search(line):
            continue
        for pattern, what in PATTERNS:
            if pattern.search(line):
                findings.append({"line": number, "why": f"{what} in source"})
                break
    return findings


if __name__ == "__main__":
    found = 0
    for name in sys.argv[1:]:
        with open(name) as handle:
            for finding in check(name, handle.read(), {}):
                print(f"{name}:{finding['line']}: {finding['why']}")
                found += 1
    sys.exit(1 if found else 0)
