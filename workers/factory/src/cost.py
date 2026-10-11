"""What a turn cost, where the figure came from, and the job's budget. Pure.

Every cost carries its source: `router`, `table`, or `unpriced`. A router that
has no price for a model reports the turn at zero, and $0.00 reads as free
when it means unknown. So a zero from the router is not a figure, and a turn
with no figure and no table price is `unpriced`, never 0.

settings/pricing.yaml is tier 2, because a price is a value, not logic:

    job_max_usd: 5.00
    models:
      claude-sonnet-5: {input_per_million: 2.00, output_per_million: 10.00}
"""

SOURCES = ("unpriced", "table", "router")   # weakest first


def _count(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def router_figure(event: dict, totals: dict | None = None) -> float | None:
    # The turn-completed event carries the router's running session total in
    # context.session_cost_usd, and harness::metrics the same total for the
    # tree. An unpriced step adds 0.0, so a zero is no figure at all.
    context = event.get("context") if isinstance(event.get("context"), dict) else {}
    for value in (context.get("session_cost_usd"), (totals or {}).get("cost_usd")):
        if _count(value) and value > 0:
            return float(value)
    return None


def tokens(totals: dict | None) -> tuple[int, int] | None:
    # From harness::metrics totals, never context.usage: usage is the last
    # step only, and a table price on it would undercount every longer turn.
    totals = totals if isinstance(totals, dict) else {}
    given = [totals.get(k) for k in ("input_tokens", "output_tokens")]
    if not all(isinstance(n, int) and not isinstance(n, bool) for n in given):
        return None
    return given[0], given[1]


def table_price(model: str, counted: tuple[int, int] | None, pricing: dict) -> float | None:
    rates = ((pricing.get("models") or {}).get(model)) if model else None
    if not isinstance(rates, dict) or counted is None:
        return None
    try:
        per_in, per_out = (float(rates[k]) for k in ("input_per_million", "output_per_million"))
    except (KeyError, TypeError, ValueError):
        return None
    return (counted[0] * per_in + counted[1] * per_out) / 1_000_000


def turn_cost(event: dict, totals: dict | None, model: str, pricing: dict) -> dict:
    """The router's figure first, then the table, otherwise unpriced. `totals`
    is harness::metrics {root_session_id} -> totals for the turn's session."""
    figure = router_figure(event, totals)
    if figure is not None:
        return {"usd": round(figure, 6), "source": "router", "model": model}
    counted = tokens(totals)
    # The counts are kept, so a model priced later prices this turn too.
    kept = {"tokens": list(counted)} if counted else {}
    priced = table_price(model, counted, pricing)
    if priced is not None:
        return {"usd": round(priced, 6), "source": "table", "model": model, **kept}
    return {"usd": None, "source": "unpriced", "model": model, **kept}


def repriced(costs: list[dict], pricing: dict) -> list[dict]:
    """Unpriced turns priced again from today's table, where their token counts
    were kept. A turn with no counts stays unpriced whatever the table says."""
    out = []
    for c in costs:
        counted = c.get("tokens")
        if c.get("source") == "unpriced" and isinstance(counted, list) and len(counted) == 2:
            priced = table_price(str(c.get("model") or ""), (counted[0], counted[1]), pricing)
            if priced is not None:
                c = {**c, "usd": round(priced, 6), "source": "table"}
        out.append(c)
    return out


def job_cost(costs: list[dict]) -> dict:
    """The sum of the priced turns, and the weakest source among them. One
    unpriced turn makes the job unpriced, and the sum a lower bound."""
    if not costs:
        return {"usd": 0.0, "source": "router", "turns": 0}
    total = sum(c["usd"] for c in costs if isinstance(c.get("usd"), (int, float)))
    weakest = min((c.get("source") or "unpriced" for c in costs),
                  key=lambda s: SOURCES.index(s) if s in SOURCES else 0)
    return {"usd": round(total, 6), "source": weakest, "turns": len(costs)}


def over_budget(costs: list[dict], pricing: dict) -> str:
    """Why the job may not start another stage, or "". At or over job_max_usd
    is over, and an unpriced turn counts as over: a ceiling that reads $0.00 as
    safe is no ceiling. No job_max_usd in settings means no ceiling."""
    limit = pricing.get("job_max_usd")
    if limit is None:
        return ""
    limit = float(limit)
    costs = repriced(costs, pricing)
    unpriced = [c for c in costs if c.get("source") == "unpriced"]
    if unpriced:
        names = ", ".join(sorted({str(c.get("model") or "an unknown model") for c in unpriced}))
        return (f"over budget: {len(unpriced)} turn(s) on {names} have no price, so the job's "
                f"spend against job_max_usd ${limit:.2f} is unknown. Price the model in "
                "settings/pricing.yaml, then answer the job")
    spent = job_cost(costs)["usd"]
    if spent >= limit:
        return (f"over budget: the job has spent ${spent:.4f} ({job_cost(costs)['source']}), "
                f"at or over job_max_usd ${limit:.2f}. Raise it in settings/pricing.yaml, "
                "then answer the job")
    return ""
