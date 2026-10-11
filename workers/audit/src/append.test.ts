import { test } from "node:test";
import assert from "node:assert/strict";
import { execFile } from "node:child_process";
import { mkdtempSync, readFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { promisify } from "node:util";

import { verify } from "./chain.js";

const run = promisify(execFile);
const INDEX = new URL("./index.ts", import.meta.url).href;

// Twenty appends at once from one process, so they also interleave at every await.
const CALLER = `const { appended } = await import(${JSON.stringify(INDEX)});
await Promise.all(Array.from({ length: 20 }, (_, i) =>
  appended({ kind: "call.completed", actor: process.env.CALLER, subject: "job", detail: { i } })));`;

test("forty concurrent appends from two callers still verify as one chain", async () => {
  // Remove the lock, or read the tail outside it, and this breaks at the first overlap,
  // the way ghola's first real record did.
  const dir = mkdtempSync(join(tmpdir(), "audit-"));
  const caller = (name: string) => run(process.execPath, ["--import", "tsx", "--input-type=module",
    "-e", CALLER], { env: { ...process.env, AUDIT_DIR: dir, CALLER: name } });
  await Promise.all([caller("one"), caller("two")]);
  const lines = readFileSync(join(dir, "audit.jsonl"), "utf8").split("\n").filter(Boolean);
  assert.equal(lines.length, 40);
  assert.deepEqual(verify(lines), { ok: true, through: 39 });
});

test("an entry with no actor is refused", async () => {
  // Owned: an entry that names nobody answers nothing about who decided.
  process.env.AUDIT_DIR = mkdtempSync(join(tmpdir(), "audit-"));
  const { appended } = await import("./index.js");
  await assert.rejects(appended({ kind: "stage.entered" }), /an actor/);
});

test("a hold the ladder's timer denied is recorded as a timeout, not a person's answer", async () => {
  // Owned: a record that credits a person with the timer's deny misstates who decided.
  process.env.AUDIT_DIR = mkdtempSync(join(tmpdir(), "audit-"));
  const { recordCall, LOG } = await import("./index.js");
  await recordCall({ session_id: "s_1", function_call_id: "c_1", function_id: "coder::create-file",
                     outcome: "deny", reason: "pending call timed out: nobody answered it within 1 minutes." });
  const last = JSON.parse(readFileSync(LOG, "utf8").trim().split("\n").pop()!);
  assert.equal(last.kind, "approval.resolved");
  assert.equal(last.actor, "ladder::expire_holds");
  assert.equal(last.detail.outcome, "timeout");
});
