"""Cost with its source, and the job budget in settings/pricing.yaml.

A cost of $0.00 that means "unknown" is the trap: a total built on it
understates spend, and a ceiling that reads it as safe never fires.
"""

import pytest

import actions
import cost
import jobs

PRICING = {"job_max_usd": 1.00,
           "models": {"claude-sonnet-5": {"input_per_million": 3.0, "output_per_million": 15.0}}}
SPEC = "specs/document-the-ports.md"


# cost_source

def test_the_routers_figure_comes_first():
    got = cost.turn_cost({"context": {"session_cost_usd": 0.0071}},
                         {"input_tokens": 9, "output_tokens": 9}, "claude-sonnet-5", PRICING)
    assert got == {"usd": 0.0071, "source": "router", "model": "claude-sonnet-5"}


def test_the_routers_figure_is_read_from_the_events_context():
    """The harness worker puts session_cost_usd under context; a top-level read found nothing,
    so every turn recorded unpriced."""
    assert cost.router_figure({"session_cost_usd": 0.5}) is None
    assert cost.router_figure({"context": {"session_cost_usd": 0.5}}) == 0.5


def test_the_metrics_total_is_a_router_figure_when_the_event_has_none():
    got = cost.turn_cost({}, {"input_tokens": 9, "output_tokens": 9, "cost_usd": 0.02},
                         "claude-sonnet-5", PRICING)
    assert got["source"] == "router" and got["usd"] == 0.02


def test_a_router_zero_is_not_a_figure():
    """Omar's trap: a model the router cannot price adds 0.0, which reads as free."""
    got = cost.turn_cost({"context": {"session_cost_usd": 0.0}}, {"cost_usd": 0.0},
                         "deepseek-flash", PRICING)
    assert got["source"] == "unpriced" and got["usd"] is None


def test_a_router_zero_falls_through_to_the_table():
    """An unpriced model with a table price is priced from the table, not at $0.00."""
    got = cost.turn_cost({"context": {"session_cost_usd": 0.0}},
                         {"input_tokens": 1000, "output_tokens": 0, "cost_usd": 0.0},
                         "claude-sonnet-5", PRICING)
    assert got["source"] == "table" and got["usd"] == pytest.approx(0.003)


def test_the_table_prices_input_and_output_tokens_per_million():
    got = cost.turn_cost({}, {"input_tokens": 100_000, "output_tokens": 10_000},
                         "claude-sonnet-5", PRICING)
    assert got["source"] == "table"
    assert got["usd"] == pytest.approx(0.3 + 0.15)


def test_tokens_come_from_the_metrics_totals_not_the_last_steps_usage():
    """context.usage is the last step only; pricing it undercounts every longer turn."""
    event = {"context": {"usage": {"input_tokens": 10, "output_tokens": 1}}}
    got = cost.turn_cost(event, {"input_tokens": 1000, "output_tokens": 0}, "claude-sonnet-5", PRICING)
    assert got["tokens"] == [1000, 0]
    assert cost.turn_cost(event, {}, "claude-sonnet-5", PRICING)["source"] == "unpriced"


def test_no_figure_and_no_price_is_unpriced_never_zero():
    """A missing figure turned into 0.0 is the gap ghola has today."""
    got = cost.turn_cost({}, {"input_tokens": 5, "output_tokens": 5}, "unknown-model", PRICING)
    assert got["source"] == "unpriced" and got["usd"] is None
    assert cost.turn_cost({}, {}, "claude-sonnet-5", PRICING)["source"] == "unpriced"


def test_a_job_reports_its_weakest_source():
    """Four priced turns and one unpriced turn make an unpriced job, and a lower-bound total."""
    turns = [{"usd": 0.1, "source": "router"}] * 4 + [{"usd": None, "source": "unpriced"}]
    assert cost.job_cost(turns) == {"usd": pytest.approx(0.4), "source": "unpriced", "turns": 5}
    assert cost.job_cost([{"usd": 0.1, "source": "router"},
                          {"usd": 0.2, "source": "table"}])["source"] == "table"


# the budget

def test_under_the_budget_runs():
    assert cost.over_budget([{"usd": 0.99, "source": "router"}], PRICING) == ""


def test_at_the_budget_is_over():
    """At or over, not only over: the next stage would spend past it."""
    why = cost.over_budget([{"usd": 0.5, "source": "router"}, {"usd": 0.5, "source": "table"}], PRICING)
    assert why.startswith("over budget") and "job_max_usd $1.00" in why


def test_an_unpriced_turn_counts_as_over():
    """A ceiling that reads $0.00 as safe is no ceiling."""
    why = cost.over_budget([{"usd": None, "source": "unpriced", "model": "deepseek-flash"}], PRICING)
    assert "deepseek-flash" in why and "have no price" in why


def test_pricing_the_model_later_prices_a_turn_whose_tokens_were_kept():
    """Otherwise a job blocked for an unpriced model could never run again."""
    turn = cost.turn_cost({}, {"input_tokens": 1000, "output_tokens": 100}, "deepseek-flash", PRICING)
    assert turn["source"] == "unpriced"
    priced = {**PRICING, "models": {"deepseek-flash": {"input_per_million": 0.28,
                                                       "output_per_million": 0.42}}}
    assert cost.over_budget([turn], priced) == ""


def test_no_job_max_usd_is_no_ceiling():
    """An empty settings/ is a working configuration."""
    assert cost.over_budget([{"usd": None, "source": "unpriced"}], {}) == ""


# the factory

@pytest.fixture
def budgeted(factory_main, settings_dir):
    (settings_dir / "phases.yaml").write_text("phases:\n  plan: {}\n  run: {}\n")
    (settings_dir / "pricing.yaml").write_text(
        "job_max_usd: 1.00\nmodels:\n  claude-sonnet-5: {input_per_million: 3, output_per_million: 15}\n")
    factory_main.worker.answers["audit::append"] = {}
    return factory_main


def appended(factory_main, kind):
    return [p for fid, p in factory_main.worker.calls if fid == "audit::append" and p["kind"] == kind]


def test_a_job_over_budget_moves_to_blocked_with_the_reason_before_the_stage(budgeted):
    """The check is before every stage, so nothing more is spent; a person decides."""
    job = {**jobs.new(SPEC, "/code/scratch", "plan"),
           "costs": [{"usd": 1.25, "source": "router", "session_id": "s_x_plan_1"}]}
    jobs.write(job)
    got = budgeted.step({"job_id": job["id"], "stage": "plan", "epoch": 0})
    after = jobs.read(job["id"])
    assert got["moved"] == "blocked" and after["stage"] == "blocked"
    assert after["history"][-1]["why"].startswith("over budget")
    assert after["question"].startswith("over budget")
    assert not [c for c, _ in budgeted.worker.calls if c == "harness::send"]
    entered, = appended(budgeted, "stage.entered")
    assert entered["detail"]["stage"] == "blocked" and entered["detail"]["epoch"] == 1
    assert entered["detail"]["reason"].startswith("over budget")


def test_an_unpriced_turn_blocks_the_next_stage(budgeted):
    job = {**jobs.new(SPEC, "/code/scratch", "run"),
           "costs": [{"usd": None, "source": "unpriced", "model": "deepseek-flash"}]}
    jobs.write(job)
    budgeted.step({"job_id": job["id"], "stage": "run", "epoch": 0})
    assert jobs.read(job["id"])["stage"] == "blocked"


def test_a_completed_turn_writes_its_cost_and_source_into_the_job(budgeted):
    """The record of spend the budget sums, one entry per turn."""
    job = {**jobs.new(SPEC, "/code/scratch", "plan"), "session_id": "s_x_plan_1"}
    job["session_id"] = jobs.session_id(job, "plan", 1)
    jobs.write(job)
    budgeted.worker.answers["harness::metrics"] = {
        "totals": {"input_tokens": 1000, "output_tokens": 0, "cost_usd": 0.0}}
    budgeted.turn_completed({"session_id": job["session_id"], "status": "failed", "terminal": True,
                             "reason": "x", "context": {"session_cost_usd": 0.0}})
    after = jobs.read(job["id"])
    assert ("harness::metrics", {"root_session_id": job["session_id"]}) in budgeted.worker.calls
    spent, = after["costs"]
    assert spent["source"] == "table" and spent["usd"] == pytest.approx(0.003)
    assert spent["session_id"] == job["session_id"]
    recorded, = appended(budgeted, "turn.cost")
    assert recorded["detail"]["source"] == "table"


def test_every_real_transition_is_recorded_with_its_new_epoch(budgeted):
    job = {**jobs.new(SPEC, "/code/scratch", "plan"), "session_id": None}
    jobs.write(job)
    budgeted.advance(job, {"failed": "the turn ended"})
    entered, = appended(budgeted, "stage.entered")
    assert entered["subject"] == job["id"]
    assert entered["detail"] == {"stage": "failed", "epoch": 1, "from": "plan",
                                "why": "plan failed: the turn ended"}


def test_a_stage_that_stays_put_records_nothing(budgeted):
    """A transition to the same stage at the same epoch is not one."""
    job = {**jobs.new(SPEC, "/code/scratch", "blocked")}
    jobs.write(job)
    budgeted.advance(job, {})
    assert appended(budgeted, "stage.entered") == []
