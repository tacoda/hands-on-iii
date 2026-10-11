// The audit worker: the record's one writer. It decides nothing. The ladder,
// the factory, the approval gate, and the turns' own calls report to it, and
// it chains, fsyncs, and answers questions. The chain itself is chain.ts.
import { mkdir, open, readFile } from "node:fs/promises";
import { fileURLToPath, pathToFileURL } from "node:url";
import lockfile from "proper-lockfile";
import { registerWorker } from "iii-sdk";

import { canonical, next, verify, type Entry } from "./chain.js";

// Plain files, not the state worker: a record must survive the thing it records.
const DIR = process.env.AUDIT_DIR || fileURLToPath(new URL("../../../state/audit", import.meta.url));
const LOG = `${DIR}/audit.jsonl`;

async function lines(): Promise<string[]> {
  try {
    return (await readFile(LOG, "utf8")).split("\n").filter((line) => line.trim());
  } catch (error: any) {
    if (error?.code === "ENOENT") return [];
    throw error;
  }
}

// From disk on every append, never from memory: a second writer would make a
// remembered tail wrong. A torn last line throws, and every append fails until
// a person repairs it, rather than chaining past a line nobody can read.
async function tail(): Promise<Entry | null> {
  const all = await lines();
  return all.length ? (JSON.parse(all[all.length - 1]) as Entry) : null;
}

async function append(kind: string, actor: string, subject: string,
                      detail: Record<string, unknown>): Promise<Entry> {
  const release = await lockfile.lock(DIR, { retries: { retries: 200, minTimeout: 5, maxTimeout: 50 } });
  try {
    const entry = next(await tail(), kind, actor, subject, detail);  // tail read inside the lock
    const file = await open(LOG, "a");
    try { await file.appendFile(canonical(entry) + "\n"); await file.sync(); }
    finally { await file.close(); }
    return entry;
  } finally { await release(); }
}

async function appended(input: any): Promise<Entry> {
  const { kind, actor, subject = "", detail = {} } = input ?? {};
  if (!kind || !actor) {
    throw new Error("audit::append needs a kind and an actor: an entry nobody owns is not owned");
  }
  await mkdir(DIR, { recursive: true });
  return append(String(kind), String(actor), String(subject), detail);
}

async function read(input: any) {
  const { kind, subject } = input ?? {};
  const entries = (await lines()).map((line) => JSON.parse(line) as Entry);
  return { entries: entries.filter((e) => (!kind || e.kind === kind)
                                           && (!subject || e.subject === subject)) };
}

async function checked() {
  const all = await lines();
  try {
    return verify(all);
  } catch (error) {
    return { ok: false, through: -1, problem: `a line is not JSON, so the record is torn: ${error}` };
  }
}

// Both questions from one file: whether to trust it, and what happened.
async function summary(input: any) {
  const by: string[] = Array.isArray(input?.by) ? input.by.map(String) : ["kind", "actor"];
  const all = await lines();
  const check = await checked();
  const entries = check.ok ? all.map((line) => JSON.parse(line) as any) : [];
  const counts: Record<string, Record<string, number>> = {};
  for (const field of by) {
    counts[field] = {};
    for (const e of entries) {
      const value = String(e[field] ?? e.detail?.[field] ?? "");
      counts[field][value] = (counts[field][value] ?? 0) + 1;
    }
  }
  const last = check.ok && entries.length ? entries[entries.length - 1].hash : null;
  return { entries: all.length, verified: check.ok, through: check.through,
           ...(check.ok ? {} : { problem: (check as any).problem }), head: last, by: counts };
}

// What a call was about, never what it carried: arguments hold file contents
// and sometimes credentials.
function called(input: any): Record<string, unknown> {
  const meta = input?.metadata ?? {};
  const args = input?.call?.arguments ?? {};
  const about = String(args.path ?? args.command ?? "").slice(0, 200);
  return { function_call_id: input?.call?.id ?? input?.result?.function_call_id ?? "",
           session_id: input?.session_id ?? "", turn_id: input?.turn_id ?? "",
           is_error: Boolean(input?.result?.is_error),
           allowed_by: `the ${meta.phase ?? "unknown"} phase grant, and the ladder passed it`,
           ...(about ? { about } : {}) };
}

const subjectOf = (input: any) =>
  String(input?.metadata?.job_id ?? input?.session_metadata?.job_id ?? input?.session_id ?? "");

// The approval gate forgets a hold once a person answers it, so these two
// entries are the only place the answer survives.
async function held(input: any) {
  const resolved = input?.outcome !== undefined;
  // The gate passes no resolver, so the ladder's timer is known by its reason.
  const timedOut = resolved && String(input?.reason ?? "").startsWith("pending call timed out");
  const detail: Record<string, unknown> = {
    function_call_id: input?.function_call_id ?? "", function_id: input?.function_id ?? "",
    session_id: input?.session_id ?? "" };
  if (resolved) {
    detail.outcome = timedOut ? "timeout" : input.outcome;
    if (input.reason) detail.reason = String(input.reason).slice(0, 400);
    if (input.outcome === "allow") detail.allowed_by = "a person released the hold";
  } else {
    detail.expires_at = input?.expires_at ?? null;
  }
  const actor = timedOut ? "ladder::expire_holds" : resolved
    ? String(input.resolved_by ?? (input.outcome === "timeout" ? "approval-gate" : "a person"))
    : "approval-gate";
  await append(resolved ? "approval.resolved" : "approval.held", actor, subjectOf(input), detail)
    .catch((e) => console.error(`audit: hold not recorded: ${e}`));
  return {};
}

const WORTH_RECORDING = ["coder::update-file", "shell::exec", "worktree::", "github::"];

async function recordCall(input: any) {
  if (input?.outcome !== undefined || input?.pending_at !== undefined) return held(input);
  const fn = String(input?.result?.function_id ?? "");
  if (!WORTH_RECORDING.some((p) => fn.startsWith(p))) return { decision: "continue" };
  await append(fn.startsWith("github::") ? "published" : "call.completed", fn,
               subjectOf(input), called(input))
    .catch((e) => console.error(`audit: call not recorded: ${e}`));  // open, never silent
  return { decision: "continue" };
}

// Exported for the tests, which start no engine.
export { append, appended, recordCall, summary, LOG };

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  await mkdir(DIR, { recursive: true });
  const iii = registerWorker(process.env.III_URL!, { workerName: "audit" });
  iii.registerFunction("audit::append", appended);
  iii.registerFunction("audit::read", read);
  iii.registerFunction("audit::verify", checked);
  iii.registerFunction("audit::summary", summary);
  iii.registerFunction("audit::record_call", recordCall);
  // The harness worker skips post-trigger for a denied call, so refusals come
  // from ladder::gate calling audit::append, not from here.
  iii.registerTrigger({ type: "harness::hook::post-trigger", function_id: "audit::record_call",
                        config: { timeout_ms: 5000, on_error: "fail_open" } });
  iii.registerTrigger({ type: "approval::pending-created", function_id: "audit::record_call",
                        config: {} });
  iii.registerTrigger({ type: "approval::pending-resolved", function_id: "audit::record_call",
                        config: {} });
  console.log(`audit: one writer for ${LOG}`);
}
