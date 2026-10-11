import { test } from "node:test";
import assert from "node:assert/strict";

import { explain } from "./explain.js";

const secrets = {
  id: "no-secrets",
  description: "A credential never reaches a file",
  why: "A leaked key costs a rotation.",
  policy: "refuse",
  escape: "secret-ok",
  rungs: [3],
  predicate: "no-secrets.py",
  refused: [],
};

test("a carried rule names its rungs, policy, words, and predicate", () => {
  // An explain that dropped the rung would hide which mechanism enforces the rule.
  assert.deepEqual(explain([secrets], "no-secrets"), {
    id: "no-secrets",
    policy: "refuse",
    says: "A credential never reaches a file. A leaked key costs a rotation.",
    carried_at: [3],
    predicate: "no-secrets.py",
    escape: "secret-ok",
    refused: [],
  });
});

test("a rule with no predicate reads prose only and keeps the refused claim", () => {
  // Renaming the .py away must show here, not leave rung 3 looking carried.
  const refused = ["no-secrets: rung 3 refused: no no-secrets.py sits beside no-secrets.md"];
  const got = explain([{ ...secrets, rungs: [], predicate: "", refused }], "no-secrets");
  assert.ok(!("error" in got));
  assert.equal(got.carried_at, "prose only");
  assert.match(got.refused[0], /rung 3/);
});

test("an unknown id says to run ladder::list", () => {
  // An empty answer would read like a rule that says nothing.
  const got = explain([secrets], "no-secret");
  assert.ok("error" in got);
  assert.match(got.error, /ladder::list/);
});
