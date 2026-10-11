// ladder::explain, in TypeScript. It owns no rules: it asks the Python
// ladder's ladder::list by function id, so neither side knows the other's language.
import { registerWorker } from "iii-sdk";

import { explain } from "./explain.js";

const iii = registerWorker(process.env.III_URL!, { workerName: "ladder-explain-ts" });

iii.registerFunction("ladder::explain", async ({ id, repo = "" }) => {
  const { rules }: any = await iii.trigger({ function_id: "ladder::list", payload: { repo } });
  return explain(rules, String(id ?? ""));
});

console.log("ladder-explain-ts: serving ladder::explain");
