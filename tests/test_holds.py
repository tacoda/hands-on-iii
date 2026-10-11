import holds

TIMEOUT = 60_000


def test_only_a_hold_past_the_timeout_is_stale_and_the_oldest_comes_first():
    """A timer that denies a fresh hold takes the decision away from the person it waits for."""
    now = 1_000_000
    found = holds.stale([{"id": "fresh", "held_at": now - 1_000},
                         {"id": "newer", "held_at": now - 90_000},
                         {"id": "older", "held_at": now - 600_000},
                         {"id": "broken", "held_at": None}], now, TIMEOUT, "held_at")
    assert [h["id"] for h in found] == ["older", "newer"]


def test_the_timeout_comes_from_the_harness_config_and_falls_back_to_thirty_minutes():
    """One setting bounds every wait: chapter 6's default_pending_timeout_ms."""
    assert holds.timeout_of({"id": "harness", "value": {"default_pending_timeout_ms": TIMEOUT}}) == TIMEOUT
    assert holds.timeout_of({"value": {"default_pending_timeout_ms": "nonsense"}}) == 1_800_000
    assert holds.timeout_of(None) == 1_800_000


def test_a_remembered_ladder_hold_is_found_again_and_forgotten(monkeypatch, tmp_path):
    """The harness worker lists no hook holds, so a hold the ladder does not write down never times out."""
    monkeypatch.setenv("LADDER_HOLDS", str(tmp_path / "holds"))
    hook = {"session_id": "s_1", "turn_id": "t_1",
            "call": {"id": "c_1", "function_id": "coder::create-file"}}
    hold = holds.remember(hook, at_ms=5)
    assert holds.recorded() == [hold]
    holds.forget(hold)
    assert holds.recorded() == []


def test_a_hold_without_its_turn_is_not_written():
    """harness::function::resolve needs the turn id; a hold without one could never be denied."""
    assert holds.remember({"session_id": "s_1", "call": {"id": "c_1"}})["turn_id"] == ""


def test_both_denials_carry_a_reason_the_model_can_act_on():
    """The model reads the reason as its next instruction, so it must say what happened."""
    ladder = holds.deny_ladder_hold({"session_id": "s", "turn_id": "t", "function_call_id": "c"}, TIMEOUT)
    assert ladder["action"] == "deliver" and ladder["is_error"] is True
    assert "timed out" in ladder["content"][0]["text"]
    gate = holds.deny_gate_hold({"session_id": "s", "function_call_id": "c"}, TIMEOUT)
    assert gate["decision"] == "deny" and "timed out" in gate["reason"]
