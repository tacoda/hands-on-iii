// What one rule says and where it is carried, from ladder::list's answer.
// Pure, so the test needs no engine.

export type Listed = {
  id: string;
  description?: string;
  why?: string;
  policy?: string;
  escape?: string;
  rungs?: number[];
  predicate?: string;
  refused?: string[];
};

export type Explained =
  | { error: string }
  | {
      id: string;
      policy: string;
      says: string;
      carried_at: number[] | "prose only";
      predicate: string;
      escape: string;
      refused: string[];
    };

export function explain(rules: Listed[] = [], id: string): Explained {
  const rule = rules.find((r) => r.id === id);
  if (!rule) {
    return { error: `no rule ${JSON.stringify(id)} is loaded. Run ladder::list to see the rules.` };
  }
  // Rung 0 asks and enforces nothing, so it does not count as carried.
  const carried = (rule.rungs ?? []).filter((rung) => rung > 0);
  return {
    id: rule.id,
    policy: rule.policy ?? "",
    says: [rule.description, rule.why].filter(Boolean).join(". "),
    carried_at: carried.length ? carried : "prose only",
    predicate: rule.predicate ?? "",
    escape: rule.escape ?? "",
    refused: rule.refused ?? [],
  };
}
