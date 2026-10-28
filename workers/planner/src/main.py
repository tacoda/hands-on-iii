"""The planner: a harness worker of your own, for the plan phase only.

It runs one turn as a chain of queue steps. Each step makes one model call
straight to the Anthropic API, runs the tool calls the reply asks for through
the gates observing the stock harness worker's on-pre-call hook, and publishes
the next step. The turn file is the record; the step number is its epoch.

It publishes two observable functions: planner::turn-completed, which the
factory observes, and planner::tool-called, which the audit worker observes.
Chapter 14 lists what the stock harness worker does that this one does not.
"""

import json
import os
import sys
import threading

import anthropic
from iii import CallAction, InitOptions, register_worker

import pricing
import tools
import turns

TOPIC = "planner.step"
DEFAULT_SYSTEM = "You plan a change to a repository. Read what you need, then answer with the plan."
# The queue retries these with backoff. Anything else that a retry cannot fix fails the turn.
RETRYABLE = (anthropic.RateLimitError, anthropic.InternalServerError, anthropic.APIConnectionError)
FINAL = (anthropic.AuthenticationError, anthropic.NotFoundError, anthropic.BadRequestError)

# Check the key at start, not at the first turn, so compose shows the failure.
if not os.environ.get("ANTHROPIC_API_KEY"):
    sys.exit("planner: ANTHROPIC_API_KEY is not set (.env.planner)")

client = anthropic.Anthropic(max_retries=0)    # ANTHROPIC_API_KEY; the queue owns retries
if __name__ == "__main__":
    # A failed start must end the process. The SDK's threads would keep it alive,
    # and compose would go on reporting the worker ready.
    sys.excepthook = lambda *exc: (sys.__excepthook__(*exc), os._exit(1))

worker = register_worker(os.environ["III_URL"], InitOptions(worker_name="planner"))


def call(function_id: str, payload: dict, timeout_ms: int = 30000):
    return worker.call({"function_id": function_id, "payload": payload,
                           "timeout_ms": timeout_ms})


def publish(topic: str, data: dict) -> None:
    call("iii::durable::publish", {"topic": topic, "data": data})


# ------------------------------------------------------------ start a turn

def start(send: dict) -> dict:
    """planner::turn. Takes the payload the factory would send to harness::send."""
    session_id = send["session_id"]
    existing = turns.read(session_id)
    if existing is not None:
        # A duplicate stage message starts nothing, the same as harness::send.
        return {"session_id": session_id, "accepted": True, "deduplicated": True}
    options = send.get("options") or {}
    gates = tools.gates(call, session_id)
    if tools.GATE not in [g["id"] for g in gates]:
        raise RuntimeError(f"{tools.GATE} does not observe {tools.PRE_CALL}, so no rule "
                           "would refuse a call. Start the ladder, then redrive factory.stage.")
    allow = tools.allowed(options.get("functions") or {})
    # A held call needs machinery this harness lacks, so an `ask` becomes a deny.
    metadata = {**(options.get("metadata") or {}), "oversight": "dark", "harness": "planner"}
    turn = {"session_id": session_id, "model": send["model"],
            "system": options.get("system_prompt") or DEFAULT_SYSTEM,
            "tools": tools.tool_list(allow, call), "allow": allow, "gates": gates,
            "metadata": metadata, "effort": options.get("thinking_level") or "high",
            "max_output_tokens": options.get("max_output_tokens") or 16000,
            "max_steps": options.get("max_turns") or 50,
            "max_cost_usd": options.get("max_cost_usd") or float("inf"),
            "messages": [{"role": "user", "content": send["message"]}],
            "step": 0, "cost_usd": 0.0, "done": False, "announced": False}
    turns.write(turn)                                            # the record first
    publish(TOPIC, {"session_id": session_id, "step": 0})
    return {"session_id": session_id, "accepted": True}


# ------------------------------------------------------------ one step

def step(msg: dict) -> dict:
    turn = turns.read(msg["session_id"])
    if turn is None or turn["step"] != msg["step"]:
        return {"skipped": "a stale step"}                       # chapter 8's guard
    if turn["done"]:
        return finish(turn)                                      # announce, if it never did
    if turn["step"] >= turn["max_steps"]:
        return finish(turn, "failed", reason=f"Stopped after {turn['max_steps']} steps.")
    try:
        response = model_call(turn)
    except RETRYABLE:
        raise                                                    # the queue retries, then dead-letters
    except FINAL as exc:
        return finish(turn, "failed", reason=f"{type(exc).__name__}: {exc}")
    turn["cost_usd"] += pricing.cost(turn["model"], response.usage)
    turn["messages"].append({"role": "assistant", "content": blocks(response.content)})
    if response.stop_reason != "tool_use":
        return finish(turn, *outcome(response))
    results = [run_tool(turn, b) for b in response.content if b.type == "tool_use"]
    turn["messages"].append({"role": "user", "content": results})
    if turn["cost_usd"] >= turn["max_cost_usd"]:
        return finish(turn, "failed", reason="The turn reached max_cost_usd.")
    turn["step"] += 1
    turns.write(turn)                                            # the record first
    publish(TOPIC, {"session_id": turn["session_id"], "step": turn["step"]})
    return {"step": turn["step"]}


def model_call(turn: dict):
    with client.messages.stream(
            model=turn["model"], max_tokens=turn["max_output_tokens"],
            output_config={"effort": turn["effort"]},
            # Caches the request through its last block, so the next step pays a cache read.
            cache_control={"type": "ephemeral"},
            system=turn["system"], tools=turn["tools"], messages=turn["messages"]) as stream:
        return stream.get_final_message()


def blocks(content) -> list[dict]:
    # As the API returned them, thinking blocks included: a tool loop sends them back unchanged.
    return [b.model_dump(mode="json", exclude_none=True) for b in content]


def outcome(response) -> tuple[str, ...]:
    text = "".join(b.text for b in response.content if b.type == "text")
    if response.stop_reason == "end_turn":
        return ("completed", text)
    # max_tokens, refusal, or a reason this loop does not know. None of them is retried.
    return ("failed", None, f"The turn stopped on {response.stop_reason}.")


# ------------------------------------------------------------ the gates

def run_tool(turn: dict, block) -> dict:
    fid = tools.function_id(block.name)
    if fid not in turn["allow"]:
        return refused(block, "the grant", {"reason": f"{fid} is not in this phase's grant."})
    # approval::gate refuses a payload with no turn_id, and a refusal here fails closed.
    hook = {"point": "pre_call", "call": {"id": block.id, "function_id": fid, "arguments": block.input},
            "metadata": turn["metadata"], "session_id": turn["session_id"],
            "turn_id": turn["session_id"], "step": turn["step"], "depth": 0}
    for gate in tools.consulted(turn["gates"], fid):
        answer = consult(gate, hook)
        if answer.get("decision") != "continue":
            return refused(block, gate, answer)
    root = (turn["metadata"].get("fs_scope") or {}).get("root")
    if not root:
        return refused(block, "the scope", {"reason": "this turn has no fs_scope.root."})
    # The harness worker's stamp: the turn's root, never one the model wrote.
    arguments = {k: v for k, v in block.input.items() if k != "fs_scope"}
    arguments["fs_scope"] = {"root": root, "boundary": "workspace"}
    try:
        result, is_error = json.dumps(call(fid, arguments)), False
    except Exception as exc:
        result, is_error = f"{fid} failed: {exc}", True
    announce_call(hook, is_error)
    return {"type": "tool_result", "tool_use_id": block.id, "is_error": is_error,
            "content": result}


def consult(gate: str, hook: dict) -> dict:
    """Fail closed, and say who failed: a gate that raises, times out, or is gone denies."""
    try:
        answer = call(gate, hook, timeout_ms=15000)
    except Exception as exc:
        return {"decision": "deny", "reason": f"The gate {gate} failed ({type(exc).__name__}: "
                                              f"{exc}) before judging this call. Stop and report the failure."}
    return answer if isinstance(answer, dict) else {"decision": "deny",
                                                    "reason": f"The gate {gate} gave no answer."}


def refused(block, by: str, answer: dict) -> dict:
    # A hold is a deny here: this harness has no way to park a call.
    reason = answer.get("reason") or f"{by} did not allow this call."
    return {"type": "tool_result", "tool_use_id": block.id, "is_error": True,
            "content": f"Refused by {by}: {reason}"}


# ------------------------------------------------------------ the end, and the calls

def the_event(event: dict) -> dict:
    return event                         # what the event's observers observe


# Private: only this worker calls them, and everyone else observes them.
COMPLETED = "planner::turn-completed"
worker.register_function(COMPLETED, the_event, private=True,
                         description="One planner turn ended. Observe it; do not call it.")
TOOL_CALLED = "planner::tool-called"
worker.register_function(TOOL_CALLED, the_event, private=True,
                         description="One planner tool call ran, in the post-call hook's shape. Observe it.")


def emit(function_id: str, event: dict) -> None:
    # Void does not wait: a stopped observer misses the event, and planner::status covers that.
    worker.call({"function_id": function_id, "payload": event, "action": CallAction.Void()})


def announce_call(hook: dict, is_error: bool) -> None:
    emit(TOOL_CALLED, {**hook, "result": {"function_id": hook["call"]["function_id"],
                                          "is_error": is_error}})


def finish(turn: dict, status: str | None = None, result=None, reason: str | None = None) -> dict:
    """An outbox: mark done, announce, then mark announced. A crash between the
    writes announces again, and the factory ignores a stale completion."""
    if not turn["done"]:
        turn.update({"done": True, "status": status, "result": result, "reason": reason})
        turns.write(turn)
    if not turn["announced"]:
        event = {"session_id": turn["session_id"], "turn_id": turn["session_id"],
                 "status": turn["status"], "terminal": True,
                 # The field chapter 11's turn.cost entry reads from a completion event.
                 "context": {"session_cost_usd": turn["cost_usd"]}}
        event |= {"result": turn["result"]} if turn["status"] == "completed" else {"reason": turn["reason"]}
        emit(COMPLETED, event)
        turn["announced"] = True
        turns.write(turn)
    return {"finished": turn["status"]}


def status(payload: dict) -> dict:
    return turns.status(turns.read(str((payload or {}).get("session_id") or "")))


worker.register_function("planner::turn", start,
                         description="Start one turn from a harness::send payload. Returns at once.")
worker.register_function("planner::step", step,
                         description="Internal: one step of a turn. Bound to planner.step.")
worker.register_function("planner::status", status,
                         description="{session_id}: a turn's status, step, and cost. Reports only.")
# A fixed id: a restarted planner takes over the queue it left, unacked steps included.
steps = worker.observe({"function_id": "queue::on-message", "args": {"topic": TOPIC}},
                       "planner::step", {"id": "planner-step"})
steps.ready(timeout=60)

if __name__ == "__main__":
    print(f"planner: {TOPIC} -> planner::step, turns in {turns.folder()}")
    threading.Event().wait()
