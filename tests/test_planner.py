"""Chapter 14: the planner, a harness worker of your own for the plan phase.

The model is a stand-in, so no test here sends a paid turn."""

import types

import anthropic
import httpx
import pytest

import improve
import pricing
import tools
import turns
from conftest import ROOT

HOOKS = {"observers": [
    {"function_id": "harness::hook::on-pre-call", "target": {"function_id": "ladder::gate"},
     "args": {"priority": 60}},
    {"function_id": "harness::hook::on-pre-call", "target": {"function_id": "approval::gate"}, "args": {}},
    {"function_id": "harness::hook::on-pre-call", "target": {"function_id": "ade::stamp"},
     "args": {"functions": ["ade::working-directory::propose"]}},
    {"function_id": "harness::hook::on-pre-call", "target": {"function_id": "console::only"},
     "args": {"sessions": ["console-*"]}},
    {"function_id": "queue::on-message", "target": {"function_id": "factory::step"}, "args": {}},
]}
INFO = {"description": "Read one file.",
        "request_schema": {"type": "object", "properties": {"path": {"type": "string"}}}}
SEND = {"session_id": "s_1234abcd_plan_1", "message": "Plan the change.", "model": "claude-opus-5",
        "options": {"max_turns": 3, "thinking_level": "high", "max_cost_usd": 1.0,
                    "functions": {"allow": ["coder::read-file", "coder::search"],
                                  "deny": ["coder::search"]},
                    "metadata": {"phase": "plan", "oversight": "supervised",
                                 "fs_scope": {"root": "/tmp/repo"}}}}


def block(kind, **fields):
    return types.SimpleNamespace(type=kind, model_dump=lambda **_: {"type": kind, **fields}, **fields)


def reply(stop, *content, usage=None):
    return types.SimpleNamespace(stop_reason=stop, content=list(content),
                                 usage=usage or {"input_tokens": 1000, "output_tokens": 100})


@pytest.fixture
def planner(planner_main):
    planner_main.worker.answers.update({
        "engine::observers::list": HOOKS,
        "engine::functions::info": INFO,
        "ladder::gate": {"decision": "continue"},
        "approval::gate": {"decision": "continue"},
        "coder::read-file": {"content": "# README"},
    })
    return planner_main


def started(planner):
    assert planner.start(SEND)["accepted"]
    return turns.read(SEND["session_id"])


def published(planner):
    return [p["data"] for f, p in planner.worker.calls if f == "iii::durable::publish"]


def emitted(planner):
    return [(f, p) for f, p in planner.worker.calls
            if f in ("planner::turn-completed", "planner::tool-called")]


# ------------------------------------------------------------- tool names

def test_a_function_id_becomes_a_valid_tool_name_and_comes_back():
    """The API refuses colons in a tool name, so every call would fail with BadRequestError."""
    name = tools.tool_name("coder::read-file")
    assert name == "coder__read-file" and tools.TOOL_NAME.match(name)
    assert tools.function_id(name) == "coder::read-file"


@pytest.mark.parametrize("fid", ["a_::b", "coder::*", "x::y.z"])
def test_an_id_that_does_not_survive_the_mapping_is_refused_at_start(fid):
    """A grant error should never be the model's to find."""
    with pytest.raises(ValueError):
        tools.tool_list([fid], lambda *_: INFO)


def test_the_tool_list_reads_the_engine_s_request_schema():
    got, = tools.tool_list(["coder::read-file"], lambda *_: INFO)
    assert got == {"name": "coder__read-file", "description": "Read one file.",
                   "input_schema": INFO["request_schema"]}


def test_the_gates_are_the_hook_s_observers_in_priority_order():
    """approval::gate at priority 0 runs before ladder::gate at 60, as on the stock hook."""
    gates = tools.gates(lambda *_: HOOKS, "s_1234abcd_plan_1")
    assert [g["id"] for g in gates] == ["ade::stamp", "approval::gate", "ladder::gate"]
    assert tools.consulted(gates, "coder::read-file") == ["approval::gate", "ladder::gate"]


def test_a_gate_is_consulted_only_where_its_globs_match():
    """The hook skips an observer whose `functions` or `sessions` globs miss, so the planner must too."""
    gates = tools.gates(lambda *_: HOOKS, "console-7")
    assert tools.consulted(gates, "ade::working-directory::propose") == [
        "ade::stamp", "approval::gate", "console::only", "ladder::gate"]
    assert tools.consulted(gates, "coder::search") == ["approval::gate", "console::only", "ladder::gate"]


# ------------------------------------------------------------- a turn

def test_a_turn_starts_from_a_send_and_publishes_step_zero(planner):
    turn = started(planner)
    assert turn["allow"] == ["coder::read-file"]          # allow minus deny
    assert [g["id"] for g in turn["gates"]] == ["ade::stamp", "approval::gate", "ladder::gate"]
    assert turn["metadata"]["oversight"] == "dark"        # an ask becomes a deny
    assert turn["max_steps"] == 3 and turn["effort"] == "high"
    assert published(planner) == [{"session_id": SEND["session_id"], "step": 0}]


def test_a_second_send_with_the_same_session_starts_nothing(planner):
    started(planner)
    assert planner.start(SEND)["deduplicated"] is True
    assert len(published(planner)) == 1


def test_no_turn_starts_without_the_ladder_on_the_hook(planner):
    planner.worker.answers["engine::observers::list"] = {"observers": []}
    with pytest.raises(RuntimeError, match="ladder::gate"):
        planner.start(SEND)
    assert turns.read(SEND["session_id"]) is None


def test_a_step_runs_the_tool_through_both_gates_then_publishes_the_next(planner, monkeypatch):
    started(planner)
    use = block("tool_use", id="tu_1", name="coder__read-file", input={"path": "README.md"})
    monkeypatch.setattr(planner, "model_call", lambda turn: reply("tool_use", use))
    assert planner.step({"session_id": SEND["session_id"], "step": 0}) == {"step": 1}
    asked = [f for f, _ in planner.worker.calls]
    assert asked.index("approval::gate") < asked.index("ladder::gate") < asked.index("coder::read-file")
    turn = turns.read(SEND["session_id"])
    assert turn["messages"][-1]["content"][0]["is_error"] is False
    assert published(planner)[-1] == {"session_id": SEND["session_id"], "step": 1}
    hook = dict(planner.worker.calls)["approval::gate"]
    assert hook["turn_id"] == SEND["session_id"]   # approval::gate refuses a hook without one
    called, = [e for f, e in emitted(planner) if f == "planner::tool-called"]
    assert called["result"] == {"function_id": "coder::read-file", "is_error": False}


def test_a_stale_step_is_skipped(planner):
    started(planner)
    assert planner.step({"session_id": SEND["session_id"], "step": 5}) == {"skipped": "a stale step"}


def test_a_denied_call_never_reaches_its_target(planner, monkeypatch):
    started(planner)
    planner.worker.answers["ladder::gate"] = {"decision": "deny", "reason": "no-secrets: a key"}
    use = block("tool_use", id="tu_1", name="coder__read-file", input={"path": ".env"})
    monkeypatch.setattr(planner, "model_call", lambda turn: reply("tool_use", use))
    planner.step({"session_id": SEND["session_id"], "step": 0})
    assert "coder::read-file" not in [f for f, _ in planner.worker.calls]
    result = turns.read(SEND["session_id"])["messages"][-1]["content"][0]
    assert result["is_error"] and "no-secrets: a key" in result["content"]


def test_a_gate_that_fails_denies_the_call_and_names_itself(planner, monkeypatch):
    """Fail closed, but say who failed."""
    started(planner)
    def down(_):
        raise RuntimeError("timeout")
    planner.worker.answers["ladder::gate"] = down
    use = block("tool_use", id="tu_1", name="coder__read-file", input={"path": "a"})
    monkeypatch.setattr(planner, "model_call", lambda turn: reply("tool_use", use))
    planner.step({"session_id": SEND["session_id"], "step": 0})
    result = turns.read(SEND["session_id"])["messages"][-1]["content"][0]
    assert result["is_error"] and "ladder::gate failed" in result["content"]


def test_a_tool_outside_the_grant_is_refused_before_any_gate(planner, monkeypatch):
    """The model can name a tool it was never shown."""
    started(planner)
    use = block("tool_use", id="tu_1", name="coder__search", input={})
    monkeypatch.setattr(planner, "model_call", lambda turn: reply("tool_use", use))
    planner.step({"session_id": SEND["session_id"], "step": 0})
    assert not {"ladder::gate", "coder::search"} & {f for f, _ in planner.worker.calls}


def test_the_end_is_announced_once_with_the_cost_the_factory_reads(planner, monkeypatch):
    started(planner)
    monkeypatch.setattr(planner, "model_call",
                        lambda turn: reply("end_turn", block("text", text="The plan.")))
    assert planner.step({"session_id": SEND["session_id"], "step": 0}) == {"finished": "completed"}
    event, = [e for f, e in emitted(planner) if f == "planner::turn-completed"]
    assert event["terminal"] and event["result"] == "The plan."
    assert event["context"]["session_cost_usd"] == pytest.approx(0.0075)
    planner.finish(turns.read(SEND["session_id"]))             # a redelivered step
    assert len([f for f, _ in emitted(planner) if f == "planner::turn-completed"]) == 1


def test_a_finish_that_crashed_before_announcing_announces_again(planner):
    """The outbox: done is written first, so a redelivered step still announces."""
    turn = started(planner)
    turns.write({**turn, "done": True, "status": "completed", "result": "x", "reason": None})
    planner.step({"session_id": SEND["session_id"], "step": 0})
    assert [f for f, _ in emitted(planner)] == ["planner::turn-completed"]


def test_the_step_limit_stops_the_turn(planner):
    turn = started(planner)
    turns.write({**turn, "step": 3})
    planner.step({"session_id": SEND["session_id"], "step": 3})
    assert turns.read(SEND["session_id"])["reason"] == "Stopped after 3 steps."


def test_the_cost_check_stops_the_turn_after_the_call(planner, monkeypatch):
    started(planner)
    use = block("tool_use", id="tu_1", name="coder__read-file", input={"path": "a"})
    huge = {"input_tokens": 400_000, "output_tokens": 0}
    monkeypatch.setattr(planner, "model_call", lambda turn: reply("tool_use", use, usage=huge))
    planner.step({"session_id": SEND["session_id"], "step": 0})
    assert turns.read(SEND["session_id"])["reason"] == "The turn reached max_cost_usd."


def test_a_refusal_fails_the_turn_by_name_and_is_not_retried(planner, monkeypatch):
    started(planner)
    monkeypatch.setattr(planner, "model_call", lambda turn: reply("refusal"))
    planner.step({"session_id": SEND["session_id"], "step": 0})
    assert "refusal" in turns.read(SEND["session_id"])["reason"]


def api_error(cls, status):
    request = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("x", response=httpx.Response(status, request=request), body=None)


def test_a_rate_limit_raises_so_the_queue_retries(planner, monkeypatch):
    started(planner)
    def limited(turn):
        raise api_error(anthropic.RateLimitError, 429)
    monkeypatch.setattr(planner, "model_call", limited)
    with pytest.raises(anthropic.RateLimitError):
        planner.step({"session_id": SEND["session_id"], "step": 0})
    assert turns.read(SEND["session_id"])["done"] is False


def test_a_wrong_key_fails_the_turn_at_once(planner, monkeypatch):
    started(planner)
    def wrong(turn):
        raise api_error(anthropic.AuthenticationError, 401)
    monkeypatch.setattr(planner, "model_call", wrong)
    planner.step({"session_id": SEND["session_id"], "step": 0})
    assert turns.read(SEND["session_id"])["reason"].startswith("AuthenticationError")


def test_the_client_leaves_retries_to_the_queue(planner):
    """Two retry layers multiply: nine paid attempts for one rate-limited step."""
    assert planner.client.max_retries == 0


def test_a_stalled_turn_is_reported_and_nothing_else(planner):
    turn = started(planner)
    assert turns.status(turn, now=turn["updated"] + 601)["status"] == "stalled"
    assert turns.status(turn, now=turn["updated"] + 10)["status"] == "running"


# ------------------------------------------------------------- price and wiring

def test_an_unlisted_model_costs_infinity_so_the_check_stops_it():
    assert pricing.cost("claude-unlisted", {"input_tokens": 1}, {"models": {}}) == float("inf")


def test_a_cache_read_costs_a_tenth_of_input():
    prices = {"models": {"m": {"input_per_million": 10.0, "output_per_million": 0}}}
    assert pricing.cost("m", {"cache_read_input_tokens": 1_000_000}, prices) == pytest.approx(1.0)


def test_the_planner_registers_under_its_compose_key(planner):
    assert planner.worker.options == {"worker_name": "planner"}
    assert set(planner.worker.functions) == {"planner::turn", "planner::step", "planner::status",
                                             "planner::turn-completed", "planner::tool-called"}


def test_every_planner_function_is_reached(planner, factory_main):
    """Observed or called: the wiring check counts the planner from chapter 14."""
    functions = [{"function_id": f} for f in planner.worker.functions]
    observers = [{"function_id": o["source"]["function_id"], "target": o["target"]}
                 for o in planner.worker.observers + factory_main.worker.observers]
    observers.append({"function_id": "planner::tool-called", "target": "audit::record_call"})
    assert "planner::tool-called" in (ROOT / "workers/audit/src/index.ts").read_text()
    got = improve.unwired(functions, observers, improve.called_ids(ROOT))
    assert got == []


def test_the_plan_phase_goes_to_the_planner(factory_main):
    import actions
    assert actions.harness_of("plan") == "planner"
    assert actions.harness_of("run") == "harness"


def test_every_request_caches_the_transcript(planner, monkeypatch):
    """Each step resends the whole conversation, so an uncached step pays for all of it again."""
    seen = {}
    class Stream:
        def __enter__(self): return self
        def __exit__(self, *exc): return False
        def get_final_message(self): return "reply"
    monkeypatch.setattr(planner.client.messages, "stream", lambda **kw: seen.update(kw) or Stream())
    planner.model_call({"model": "m", "max_output_tokens": 1, "effort": "high",
                        "system": "s", "tools": [], "messages": []})
    assert seen["cache_control"] == {"type": "ephemeral"}


def test_every_tool_call_carries_the_turn_s_root(planner, monkeypatch):
    """Without the stamp, coder:: reads the factory's own directory, not the target."""
    started(planner)
    use = block("tool_use", id="tu_1", name="coder__read-file",
                input={"path": "README.md", "fs_scope": {"root": "/"}})
    monkeypatch.setattr(planner, "model_call", lambda turn: reply("tool_use", use))
    planner.step({"session_id": SEND["session_id"], "step": 0})
    sent = dict(planner.worker.calls)["coder::read-file"]
    assert sent["fs_scope"] == {"root": "/tmp/repo", "boundary": "workspace"}


def test_a_turn_with_no_root_refuses_every_call(planner, monkeypatch):
    started(planner)
    turn = turns.read(SEND["session_id"])
    turn["metadata"].pop("fs_scope")
    turns.write(turn)
    use = block("tool_use", id="tu_1", name="coder__read-file", input={"path": "README.md"})
    monkeypatch.setattr(planner, "model_call", lambda turn: reply("tool_use", use))
    planner.step({"session_id": SEND["session_id"], "step": 0})
    assert "coder::read-file" not in {f for f, _ in planner.worker.calls}
