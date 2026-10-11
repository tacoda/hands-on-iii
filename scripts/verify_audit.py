"""Verify the record by reading state/audit/ directly. No engine, no worker.

    python3 scripts/verify_audit.py
    python3 scripts/verify_audit.py --anchor <hash from a request's record head line>

Asking the writer whether its own writing is intact is the wrong shape for an
audit, so this reads the file itself. It exits non-zero on a break, or when an
anchor is not in the verified chain, so it works as a CI step.

It detects accidental edits and any edit that does not rebuild the chain.
Anyone who can write state/audit/ can rebuild every later hash, which is what
--anchor is for: a copy of the head the forge already holds.
"""

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GENESIS = "0" * 64


def folder() -> Path:
    return Path(os.environ.get("AUDIT_DIR") or ROOT / "state" / "audit")


def canonical(body: dict) -> str:
    # Must match workers/audit/src/chain.ts byte for byte, or every entry fails.
    # tests/test_audit_canonical.py holds both sides to one fixture.
    return json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def verify(lines: list[str]) -> dict:
    prev = "0" * 64
    for i, line in enumerate(lines):
        body = json.loads(line)                     # a torn line raises here
        claimed = body.pop("hash")
        digest = hashlib.sha256(canonical(body).encode()).hexdigest()
        why = ("is out of order" if body["seq"] != i
               else "does not follow the entry before it" if body["prev"] != prev
               else "does not match its hash, so it was edited" if digest != claimed else "")
        if why:
            return {"ok": False, "through": i - 1, "problem": f"entry {i} {why}"}
        prev = claimed
    return {"ok": True, "through": len(lines) - 1}


def anchored(lines: list[str], through: int, anchor: str) -> bool:
    """Whether the verified part of the chain holds this hash. An empty record's
    head is genesis, which every chain starts from."""
    if anchor == GENESIS:
        return True
    return any(json.loads(line).get("hash") == anchor for line in lines[:through + 1])


def read_lines(path: Path) -> list[str]:
    if not path.is_file():
        return []
    return [line for line in path.read_text(encoding="utf-8").split("\n") if line.strip()]


def report(lines: list[str], anchor: str = "") -> tuple[int, str]:
    """The exit code and the line to print. Pure, so a test needs no files."""
    try:
        result = verify(lines)
    except (ValueError, KeyError, TypeError) as error:
        torn = next((i for i, line in enumerate(lines) if not parses(line)), None)
        where = f"entry {torn}" if torn is not None else "an entry"
        return 1, (f"{len(lines)} entries, broken: {where} cannot be read ({error}). "
                   "Repair that line by hand; the worker will not append past it.")
    if not result["ok"]:
        return 1, (f"{len(lines)} entries, broken: {result['problem']}. "
                   f"Verified through entry {result['through']}.")
    said = f"{len(lines)} entries, intact, verified through entry {result['through']}"
    if anchor:
        if not anchored(lines, result["through"], anchor):
            return 1, (f"{said}, but the chain does not contain the anchor {anchor}. "
                       "Something rebuilt the record after that request opened.")
        said += f", and it contains the anchor {anchor[:12]}"
    return 0, said + "."


def parses(line: str) -> bool:
    try:
        return isinstance(json.loads(line), dict)
    except ValueError:
        return False


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--anchor", default="", help="a record head hash from a request body")
    parser.add_argument("--dir", default="", help="the record's folder (default state/audit/)")
    args = parser.parse_args(argv)
    code, said = report(read_lines(Path(args.dir or folder()) / "audit.jsonl"),
                        args.anchor.strip())
    print(said)
    return code


if __name__ == "__main__":
    sys.exit(main())
