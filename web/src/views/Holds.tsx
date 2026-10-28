import { useState } from "react";
import { api } from "../api";
import { usePoll } from "../poll";
import type { Hold } from "../types";

export function Holds() {
  const { data, error, reload } = usePoll<{ holds: Hold[] }>("/holds");
  const problem = error && <p role="alert">Cannot load holds: {error}. The list may be stale.</p>;
  if (!data) return problem || <p>Loading</p>;
  if (data.holds.length === 0) return <>{problem}<p>No held calls.</p></>;
  return <>{problem}<ul>{data.holds.map((h) => <HoldRow key={h.function_call_id} hold={h} done={reload} />)}</ul></>;
}

function HoldRow({ hold, done }: { hold: Hold; done: () => void }) {
  const [reason, setReason] = useState("");
  const [error, setError] = useState<string | null>(null);
  const answer = (decision: "allow" | "deny") =>
    api<{ resolved: boolean }>("/holds/resolve", { ...hold, decision, reason }).then((r) => {
      if (!r.resolved) alert("This hold was settled before your answer arrived. The job's history shows how.");
      done();
    }, (e) => setError(e.message));
  return (
    <li>
      <code>{hold.function_id}</code> held by the {hold.kind}, job {hold.session_id.slice(2, 10)}
      {hold.kind === "ladder" && <p>{hold.rule_id}: {hold.reason}</p>}
      <pre>{hold.arguments_excerpt}</pre>
      <textarea value={reason} onChange={(e) => setReason(e.target.value)}
                placeholder="What should the agent do instead?" />
      <button onClick={() => answer("allow")}>Allow</button>
      <button disabled={!reason.trim()} onClick={() => answer("deny")}>Deny</button>
      {error && <p role="alert">{error}</p>}
    </li>
  );
}
