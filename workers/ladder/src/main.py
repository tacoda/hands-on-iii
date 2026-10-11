"""The ladder worker: rungs 2 and 3 in front of every tool call, and rung 4
for the factory's delivery gate through ladder::evaluate.

Compose starts it (worker-compose.yaml) and injects III_URL. The decisions are
in ladder.py and the rule files in rules.py; this file is the wiring. It serves
ladder::gate, ladder::evaluate, and ladder::list. ladder::explain is served by
workers/ladder-explain-ts, because one function id has one owner.

A hold from ladder::gate never enters approval-gate's inbox, and no stock
worker expires it or the gate's holds. ladder::expire_holds, on a cron trigger,
denies any hold older than the harness worker's default_pending_timeout_ms.
Settle it with harness::function::resolve: action execute allows the call, and
action deliver with is_error true and a content denies it.
"""

import os
import threading

from iii import InitOptions, register_worker

import holds
import ladder
import rules


def gate(payload: dict) -> dict:
    meta = payload.get("metadata") or {}
    repo = str((meta.get("fs_scope") or {}).get("root") or "")
    write = ladder.write_of(payload.get("call") or {})
    try:
        answer = (ladder.repo_permissions(repo, write)                      # rung 2
                  or ladder.decide(rules.load(repo), write, rung=3, run=ladder.run))
    except Exception as exc:
        return {"decision": "deny", "reason": f"The ladder failed ({type(exc).__name__}) "
                "before judging this call. No rule refused it. Stop and report the failure."}
    if answer.get("policy") != "ask":
        return answer                                    # continue, or a refusal
    if meta.get("oversight") != "dark":
        holds.remember(payload)                          # so the timer can find it
        return {"decision": "hold"}                      # parked for a person
    return {"decision": "deny", "reason": answer["reason"] + " Oversight is dark, so "
            "nobody can answer. Leave this unchanged and say why in your reply."}


def evaluate(payload: dict) -> dict:
    # The same decide as the hook, so the two cannot answer differently.
    write = {"function_id": "", "path": str(payload.get("path") or ""),
             "content": str(payload.get("content") or "")}
    repo = str(payload.get("repo") or "")
    return ladder.decide(rules.load(repo), write, rung=int(payload.get("rung", 3)),
                         run=ladder.run)


def described(rule: rules.Rule) -> dict:
    return {"id": rule.id, "layer": rule.layer, "description": rule.description,
            "why": rule.why, "policy": rule.policy, "escape": rule.escape,
            "paths": list(rule.paths), "claimed": list(rule.claimed),
            "rungs": list(rule.rungs),
            "predicate": os.path.basename(rule.script) if rule.script else "",
            "refused": list(rule.refused), "source": rule.source}


def list_rules(payload: dict) -> dict:
    repo = str((payload or {}).get("repo") or "")
    permissions = ladder.read_permissions(repo)
    return {"rules": [described(r) for r in rules.load(repo)],
            "withheld": ladder.withheld(permissions),
            "problems": ladder.unmapped(permissions)}


def call(function_id: str, payload: dict) -> dict:
    answer = worker.trigger({"function_id": function_id, "payload": payload, "timeout_ms": 10000})
    answer = answer.get("payload", answer) if isinstance(answer, dict) else {}
    return answer if isinstance(answer, dict) else {}


def expire_holds(payload: dict | None = None) -> dict:
    """The timer no stock worker has. Every ladder hold and every gate hold
    older than default_pending_timeout_ms is denied with a reason the model
    reads, and its turn resumes."""
    try:
        timeout = holds.timeout_of(call("configuration::get", {"id": "harness"}))
    except Exception:
        timeout = holds.DEFAULT_TIMEOUT_MS
    now, expired = holds.now_ms(), []
    for hold in holds.stale(holds.recorded(), now, timeout, "held_at"):
        try:
            call("harness::function::resolve", holds.deny_ladder_hold(hold, timeout))
            expired.append(hold["function_call_id"])
            holds.forget(hold)
        except Exception as exc:
            # A person already answered it, or the engine is busy. Retry until twice the timeout.
            if now - hold["held_at"] >= 2 * timeout:
                holds.forget(hold)
            print(f"ladder: hold {hold['function_call_id']} not expired: {exc}", flush=True)
    try:
        pending = call("approval::list-pending", {}).get("pending") or []
    except Exception:
        pending = []                     # no approval gate running
    for held in holds.stale(pending, now, timeout, "pending_at"):
        try:
            call("approval::resolve", holds.deny_gate_hold(held, timeout))
            expired.append(held["function_call_id"])
        except Exception as exc:
            print(f"ladder: gate hold {held['function_call_id']} not expired: {exc}", flush=True)
    return {"timeout_ms": timeout, "expired": expired}

PRE_TRIGGER = "harness::hook::pre-trigger"  # hyphens; tests/test_hooks.py asserts it
worker = register_worker(os.environ["III_URL"], InitOptions(worker_name="ladder"))
worker.register_function("ladder::gate", gate)
worker.register_function("ladder::evaluate", evaluate)
worker.register_function("ladder::list", list_rules)
worker.register_function("ladder::expire_holds", expire_holds)
worker.register_trigger({"type": "cron", "function_id": "ladder::expire_holds",
                         "config": {"expression": "0 * * * * *"}})
worker.register_trigger({
    "type": PRE_TRIGGER,
    "function_id": "ladder::gate",
    "config": {"priority": 60, "timeout_ms": 15000, "on_error": "fail_closed"},
})

if __name__ == "__main__":
    loaded = rules.load()
    print(f"ladder: {PRE_TRIGGER} -> ladder::gate, {len(loaded)} team rules")
    for rule in loaded:
        for refusal in rule.refused:
            print(f"ladder: refused {refusal}")
    # The SDK thread happens to be non-daemon; the worker should not rely on that.
    threading.Event().wait()
