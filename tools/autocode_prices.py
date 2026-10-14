"""The dated price table (tools/autocode_prices.json): what a model's tokens cost, per million.

Each row names a model the way a stage records it (`provider/model` for OpenCode and Kilo routes),
its input, output and, where the provider publishes one, cached-input price in USD per million
tokens, the page it came from and the date it was read. Rows change by pull request only.

A model id is matched as recorded, without an OpenCode 2.x `#effort` suffix; a bare id with no row of
its own (the Codex engine's `gpt-5.6-sol`) is priced as `openai/<id>`, the only provider that engine
runs. A model without a row has no rate: its cost is unknown, never zero.

These are current list prices for a person's spending cap (#721). The frozen comparison rates in
autocode_usage.REFERENCE_PRICES stay separate, so evidence reports and campaign comparisons bound
to them do not move when this table is updated. Imports only the standard library.
"""

from __future__ import annotations

import json
import math
from functools import lru_cache
from pathlib import Path

PATH = Path(__file__).with_name("autocode_prices.json")
RATES = ("input", "output", "cached_input")


def _rate(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
        raise ValueError(f"{where} must be a finite nonnegative number")
    return float(value)


def parse(document):
    """The validated table from a decoded price file; ValueError names the first bad field."""
    if not isinstance(document, dict) or not isinstance(document.get("models"), dict):
        raise ValueError("the price table needs a models object")
    for key in ("as_of", "currency"):
        if not isinstance(document.get(key), str) or not document[key]:
            raise ValueError(f"the price table needs {key}")
    if document["currency"] != "USD":
        raise ValueError("prices are in USD")
    models = {}
    for model, row in document["models"].items():
        if not isinstance(model, str) or not model or model != model.strip() or not isinstance(row, dict):
            raise ValueError(f"bad price row {model!r}")
        for key in ("source", "retrieved"):
            if not isinstance(row.get(key), str) or not row[key]:
                raise ValueError(f"{model}: {key} is required")
        prices = {key: _rate(row[key], f"{model}.{key}") for key in ("input", "output")}
        if row.get("cached_input") is not None:
            prices["cached_input"] = _rate(row["cached_input"], f"{model}.cached_input")
        models[model] = {**prices, "source": row["source"], "retrieved": row["retrieved"], "note": row.get("note", "")}
    return {"as_of": document["as_of"], "currency": "USD", "models": models}


@lru_cache(maxsize=1)
def table():
    """The shipped table, read once per process."""
    return parse(json.loads(PATH.read_text(encoding="utf-8")))


def key(model):
    """The table row a recorded model id is priced by, or None when it has no rate."""
    if not isinstance(model, str) or not model:
        return None
    model = model.rsplit("#", 1)[0]
    models = table()["models"]
    if model in models:
        return model
    if "/" not in model and "openai/" + model in models:
        return "openai/" + model
    return None


def rate(model):
    """The row for `model` (input, output, optional cached_input, source, retrieved), or None."""
    found = key(model)
    return dict(table()["models"][found]) if found else None


def _count(value):
    return value if type(value) is int and value >= 0 else None


def estimate(model, tokens):
    """USD for one stage's tokens at the table's rates, or None without a rate or token counts.

    Input and output counts are inclusive: input includes cache reads and writes, output includes
    reasoning. Cache reads are priced at the cached-input rate when the row has one and the read
    count is known and within the input count; otherwise every input token is priced at the input
    rate. Cache writes are priced at the input rate, and a per-request long-context premium is not
    visible in a stage's totals, so this is an estimate that can run below the provider's bill.
    """
    row = rate(model)
    tokens = tokens if isinstance(tokens, dict) else {}
    inp, out = _count(tokens.get("input_tokens")), _count(tokens.get("output_tokens"))
    if row is None or inp is None or out is None:
        return None
    cached = _count(tokens.get("cached_input_tokens"))
    if "cached_input" in row and cached is not None and cached <= inp:
        return ((inp - cached) * row["input"] + cached * row["cached_input"] + out * row["output"]) / 1e6
    return (inp * row["input"] + out * row["output"]) / 1e6
