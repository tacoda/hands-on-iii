def test_the_pre_trigger_hook_type_is_spelled_with_a_hyphen(ladder_main):
    """`pre_trigger` registers without error and never fires: a ladder that looks wired and enforces nothing."""
    assert "pre-trigger" in ladder_main.PRE_TRIGGER, f"{ladder_main.PRE_TRIGGER!r} must spell pre-trigger"
    assert ladder_main.PRE_TRIGGER == "harness::hook::pre-trigger"


def test_the_gate_is_bound_fail_closed_after_the_approval_gate_and_unfiltered(ladder_main):
    """A functions filter would let a rule about content miss calls; a tie with approval::gate is luck."""
    binding, = [t for t in ladder_main.worker.triggers if t["type"] == ladder_main.PRE_TRIGGER]
    assert binding["type"] == ladder_main.PRE_TRIGGER
    assert binding["function_id"] == "ladder::gate"
    assert binding["config"] == {"priority": 60, "timeout_ms": 15000, "on_error": "fail_closed"}
    assert "functions" not in binding["config"]


def test_the_worker_registers_under_its_compose_key(ladder_main):
    """Compose waits for a worker named like its container key; a name that differs fails at its deadline."""
    assert ladder_main.worker.options == {"worker_name": "ladder"}
    assert set(ladder_main.worker.functions) == {"ladder::gate", "ladder::evaluate", "ladder::list",
                                                 "ladder::expire_holds"}


def test_the_hold_timer_runs_every_minute(ladder_main):
    """No stock worker expires a hold, so an unbound timer leaves every held call waiting forever."""
    timer, = [t for t in ladder_main.worker.triggers if t["type"] == "cron"]
    assert timer["function_id"] == "ladder::expire_holds"
    assert timer["config"] == {"expression": "0 * * * * *"}
