import { test } from "node:test";
import assert from "node:assert/strict";
import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";

import { canonical, GENESIS, next, verify, type Entry } from "./chain.js";

const FIXTURE = new URL("../../../tests/fixtures/audit/", import.meta.url);
const line = (e: Entry) => canonical(e);

function chain(n: number): Entry[] {
  const out: Entry[] = [];
  for (let i = 0; i < n; i++) {
    out.push(next(out.at(-1) ?? null, "stage.entered", "factory", "job-1", { epoch: i }));
  }
  return out;
}

test("each entry points back at the one before it, and the first at genesis", () => {
  // A chain whose first prev were random could not be told from a truncated one.
  const entries = chain(3);
  assert.equal(entries[0].prev, GENESIS);
  assert.equal(entries[1].prev, entries[0].hash);
  assert.deepEqual(entries.map((e) => e.seq), [0, 1, 2]);
  assert.deepEqual(verify(entries.map(line)), { ok: true, through: 2 });
});

test("the hash covers prev, so relinking a deleted entry's neighbour is caught", () => {
  // With prev outside the hash, deleting entry 1 and fixing entry 2's prev would pass.
  const [a, , c] = chain(3);
  const relinked = { ...c, seq: 1, prev: a.hash };
  const got = verify([line(a), line(relinked)]);
  assert.equal(got.ok, false);
  assert.match((got as any).problem, /^entry 1 does not match its hash/);
});

test("a one-field edit is caught by entry number", () => {
  // A boolean answer would hide where trust ends.
  const entries = chain(5);
  entries[3] = { ...entries[3], actor: "factorx" };
  assert.deepEqual(verify(entries.map(line)),
                   { ok: false, through: 2, problem: "entry 3 does not match its hash, so it was edited" });
});

test("verify reports the first break, not the last", () => {
  // Rehashing the edited entry moves the break one entry later, never past it.
  const entries = chain(5);
  const { hash: _, ...body } = { ...entries[1], actor: "someone" };
  entries[1] = { ...body, hash: createHash("sha256").update(canonical(body)).digest("hex") };
  entries[3] = { ...entries[3], actor: "edited too" };
  assert.deepEqual(verify(entries.map(line)), {
    ok: false, through: 1, problem: "entry 2 does not follow the entry before it" });
});

test("an entry out of order is named before its hash is checked", () => {
  const entries = chain(2);
  assert.deepEqual(verify([line(entries[1])]),
                   { ok: false, through: -1, problem: "entry 0 is out of order" });
});

test("canonical sorts keys at every depth, so insertion order never changes a hash", () => {
  // JSON.stringify keeps insertion order: the same entry would hash two ways.
  assert.equal(canonical({ b: 1, a: { d: [2, { z: 1, y: 2 }], c: "x" } }),
               '{"a":{"c":"x","d":[2,{"y":2,"z":1}]},"b":1}');
});

test("canonical matches the Python verifier's bytes for the shared fixture", () => {
  // tests/test_audit_canonical.py checks the same two files from the Python side.
  const entry = JSON.parse(readFileSync(new URL("entry.json", FIXTURE), "utf8"));
  const expected = readFileSync(new URL("canonical.txt", FIXTURE), "utf8");
  assert.equal(canonical(entry), expected);
  assert.equal(createHash("sha256").update(canonical(entry)).digest("hex"),
               readFileSync(new URL("hash.txt", FIXTURE), "utf8"));
});

test("a small fraction is written the way Python's json.dumps writes it", () => {
  // JSON.stringify gives 0.00005; Python gives 5e-05, and every such entry would fail verify.
  assert.equal(canonical(0.00005), "5e-05");
  assert.equal(canonical(1.5e-7), "1.5e-07");
  assert.equal(canonical(0.0071), "0.0071");
  assert.equal(canonical(12), "12");
});
