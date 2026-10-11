"""The Python verifier and the TypeScript writer hash the same bytes.

scripts/verify_audit.py rebuilds an entry's canonical form with json.dumps;
workers/audit/src/chain.ts builds it by hand. If the two differ by one byte,
every entry the worker writes fails verification. Both sides check one fixture,
tests/fixtures/audit/, and workers/audit/src/chain.test.ts checks it from Node.
"""

import hashlib
import json
import shutil
import subprocess
from pathlib import Path

import pytest

import verify_audit

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "audit"
AUDIT = ROOT / "workers" / "audit"


def test_the_fixture_entry_canonicalises_to_the_bytes_chain_ts_produces():
    """A canonical form that drifted from the worker's would fail every real entry."""
    entry = json.loads((FIXTURE / "entry.json").read_text())
    got = verify_audit.canonical(entry)
    assert got == (FIXTURE / "canonical.txt").read_text()
    assert hashlib.sha256(got.encode()).hexdigest() == (FIXTURE / "hash.txt").read_text()


def test_canonical_sorts_keys_at_every_depth_and_writes_no_spaces():
    """Insertion order would otherwise give one entry two hashes."""
    assert verify_audit.canonical({"b": 1, "a": {"d": [2, {"z": 1, "y": 2}], "c": "x"}}) == \
        '{"a":{"c":"x","d":[2,{"y":2,"z":1}]},"b":1}'


def test_non_ascii_text_is_written_as_itself():
    """ensure_ascii would write \\u00ed where JSON.stringify writes í."""
    assert verify_audit.canonical({"why": "línea"}) == '{"why":"línea"}'


@pytest.mark.skipif(not (AUDIT / "node_modules" / ".bin" / "tsx").exists() or not shutil.which("node"),
                    reason="needs `npm ci` in workers/audit")
def test_node_and_python_agree_on_a_fresh_entry_byte_for_byte():
    """The fixture pins one entry; this one is made by chain.ts's own next()."""
    script = ("import { next, canonical } from './src/chain.ts';"
              "const e = next(null, 'turn.cost', 'factory', 'job', "
              "{ usd: 0.00005, tokens: [1200, 80], why: 'línea \"q\"\\n', nested: { b: 1, a: 2 } });"
              "process.stdout.write(canonical(e));")
    done = subprocess.run(["node", "--import", "tsx", "--input-type=module", "-e", script],
                          cwd=AUDIT, capture_output=True, text=True, timeout=60, check=True)
    line = done.stdout
    body = json.loads(line)
    assert verify_audit.canonical(body) == line
    claimed = body.pop("hash")
    assert hashlib.sha256(verify_audit.canonical(body).encode()).hexdigest() == claimed
