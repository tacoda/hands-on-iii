def test_the_pre_call_hook_is_spelled_with_hyphens(ladder_main):
    """`on_pre_call` names no function, so its observer never goes active and the ladder enforces nothing."""
    assert "on-pre-call" in ladder_main.PRE_CALL, f"{ladder_main.PRE_CALL!r} must spell on-pre-call"
    assert ladder_main.PRE_CALL == "harness::hook::on-pre-call"


def test_the_gate_is_bound_fail_closed_after_the_approval_gate_and_unfiltered(ladder_main):
    """A functions filter would let a rule about content miss calls; a tie with approval::gate is luck."""
    binding, = [o for o in ladder_main.worker.observers if o["source"]["function_id"] == ladder_main.PRE_CALL]
    assert binding["target"] == "ladder::gate"
    assert binding["source"]["args"] == {"priority": 60, "timeout_ms": 15000, "on_error": "fail_closed"}
    assert "functions" not in binding["source"]["args"]


def test_the_worker_registers_under_its_compose_key(ladder_main):
    """Compose waits for a worker named like its container key; a name that differs fails at its deadline."""
    assert ladder_main.worker.options == {"worker_name": "ladder"}
    assert set(ladder_main.worker.functions) == {"ladder::gate", "ladder::evaluate", "ladder::list",
                                                 "ladder::expire_holds", "ladder::holds",
                                                 "ladder::resolve"}


def test_the_hold_timer_runs_every_minute(ladder_main):
    """No stock worker expires a hold, so an unobserved timer leaves every held call waiting forever."""
    timer, = [o for o in ladder_main.worker.observers if o["source"]["function_id"] == "cron::on-tick"]
    assert timer["target"] == "ladder::expire_holds"
    assert timer["source"]["args"] == {"expression": "0 * * * * *"}
    assert timer["source"]["capture"] == ["input"]  # the tick, as the payload's input
