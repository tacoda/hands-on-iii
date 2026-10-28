// The shapes the factory's routes return (workers/factory/src/web.py).
export type Hold = {
  kind: "gate" | "ladder";
  session_id: string;
  function_call_id: string;
  function_id: string;
  arguments_excerpt: string;
  rule_id?: string;          // ladder holds only
  reason?: string;           // ladder holds only
};

export type Job = {
  id: string;
  stage: string;
  epoch: number;
  stuck: boolean;
  reason: string;
  spec: string;
  session_id: string | null;
};

export type Entry = { kind: string; actor: string; detail?: unknown; at?: string };
