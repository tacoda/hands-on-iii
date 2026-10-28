"""Dollars for one reply, from its usage and settings/pricing.yaml.

A cache read and a cache write are priced from the model's input rate, with
the multipliers the provider documents. A model the table does not list costs
infinity, so the cost check stops the turn instead of reading it as free.
"""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[3]
CACHE_READ = 0.10       # of the input rate
CACHE_WRITE = 1.25


def table() -> dict:
    return yaml.safe_load((ROOT / "settings" / "pricing.yaml").read_text()) or {}


def cost(model: str, usage, prices: dict | None = None) -> float:
    rates = ((prices if prices is not None else table()).get("models") or {}).get(model)
    if not rates:
        return float("inf")
    get = (lambda k: usage.get(k) or 0) if isinstance(usage, dict) else (lambda k: getattr(usage, k, 0) or 0)
    per_in, per_out = rates["input_per_million"], rates["output_per_million"]
    return (get("input_tokens") * per_in + get("output_tokens") * per_out
            + get("cache_read_input_tokens") * per_in * CACHE_READ
            + get("cache_creation_input_tokens") * per_in * CACHE_WRITE) / 1_000_000
