// The record's chain: making an entry, the bytes it is hashed over, and
// checking a chain. Pure, so a test builds and verifies a chain in memory.
import { createHash } from "node:crypto";

export type Entry = {
  v: number;
  seq: number;
  prev: string;
  kind: string;
  at: number;
  actor: string;
  subject: string;
  detail: Record<string, unknown>;
  hash: string;
};

export type Verified =
  | { ok: true; through: number }
  | { ok: false; through: number; problem: string };

// scripts/verify_audit.py rebuilds these bytes with json.dumps(sort_keys=True,
// separators=(",", ":"), ensure_ascii=False). JSON.stringify differs from it in
// one place, a small fraction: 0.00005 is "0.00005" here and "5e-05" there.
function number(n: number): string {
  if (!Number.isFinite(n)) return "null";
  if (Number.isInteger(n) || Math.abs(n) >= 1e-4) return JSON.stringify(n);
  const [mantissa, exponent] = n.toExponential().split("e");
  const sign = exponent.startsWith("-") ? "-" : "+";
  return `${mantissa}e${sign}${exponent.replace(/^[-+]/, "").padStart(2, "0")}`;
}

export function canonical(value: unknown): string {
  if (value === null || value === undefined) return "null";
  if (typeof value === "number") return number(value);
  if (typeof value === "string" || typeof value === "boolean") return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  const object = value as Record<string, unknown>;
  const keys = Object.keys(object).filter((k) => object[k] !== undefined).sort();
  return `{${keys.map((k) => `${JSON.stringify(k)}:${canonical(object[k])}`).join(",")}}`;
}

export const GENESIS = "0".repeat(64);
const digest = (body: object) => createHash("sha256").update(canonical(body)).digest("hex");
export function next(tail: Entry | null, kind: string, actor: string,
                     subject: string, detail: Record<string, unknown>): Entry {
  const body = { v: 1, seq: tail ? tail.seq + 1 : 0, prev: tail ? tail.hash : GENESIS,
                 kind, at: Date.now(), actor, subject, detail };
  return { ...body, hash: digest(body) };   // the hash covers prev
}

// The first break by number, not a boolean: an audit needs to know where
// trust ends. The same checks, in the same order, as verify_audit.py.
export function verify(lines: string[]): Verified {
  let prev = GENESIS;
  for (const [i, line] of lines.entries()) {
    const { hash: claimed, ...body } = JSON.parse(line) as Entry;  // a torn line throws
    const why = body.seq !== i ? "is out of order"
      : body.prev !== prev ? "does not follow the entry before it"
      : digest(body) !== claimed ? "does not match its hash, so it was edited" : "";
    if (why) return { ok: false, through: i - 1, problem: `entry ${i} ${why}` };
    prev = claimed;
  }
  return { ok: true, through: lines.length - 1 };
}
