"""The dated price table (tools/autocode_prices.json): what a model's tokens cost, per million.

Each row names a model the way a stage records it (`provider/model` for OpenCode and Kilo routes),
its input, output and, where the provider publishes one, cached-input price in USD per million
tokens, the page it came from and the date it was read. Rows change by pull request only.

A row may keep the rates it replaced in `history`: each entry has the same fields plus `until`,
the UTC date from which it no longer applies, in ascending order. A stage is priced at the entry
in force on the UTC date it finished, so a tariff update never rewrites what an earlier stage
cost (#1867 S1a). Changing a rate therefore moves the old one into `history` with `until` set to
the new rate's first day; editing a rate in place reprices every earlier stage.

A model id is matched as recorded, without an OpenCode 2.x `#effort` suffix; a bare id with no row of
its own (the Codex engine's `gpt-5.6-sol`) is priced as `openai/<id>`, the only provider that engine
runs. A model without a row has no rate: its cost is unknown, never zero.

These are current list prices for a person's spending cap (#721). The frozen comparison rates in
autocode_usage.REFERENCE_PRICES stay separate, so evidence reports and campaign comparisons bound
to them do not move when this table is updated. Imports only the standard library.
"""

from __future__ import annotations

import datetime as dt
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
        models[model] = {**_rates(row, model), "note": row.get("note", "")}
        if "history" in row:
            models[model]["history"] = _history(row["history"], model)
    return {"as_of": document["as_of"], "currency": "USD", "models": models}


def _rates(row, where):
    for key in ("source", "retrieved"):
        if not isinstance(row.get(key), str) or not row[key]:
            raise ValueError(f"{where}: {key} is required")
    prices = {key: _rate(row[key], f"{where}.{key}") for key in ("input", "output")}
    if row.get("cached_input") is not None:
        prices["cached_input"] = _rate(row["cached_input"], f"{where}.cached_input")
    return {**prices, "source": row["source"], "retrieved": row["retrieved"]}


def _history(entries, model):
    if not isinstance(entries, list):
        raise ValueError(f"{model}.history must be a list")
    parsed, last = [], None
    for index, entry in enumerate(entries):
        where = f"{model}.history[{index}]"
        until = entry.get("until") if isinstance(entry, dict) else None
        if _day(until) != until or (last is not None and until <= last):
            raise ValueError(f"{where}.until must be a YYYY-MM-DD date after the previous entry's")
        parsed.append({**_rates(entry, where), "until": until})
        last = until
    return parsed


def _day(value):
    """The UTC calendar date of an ISO date or timestamp, as YYYY-MM-DD; None when it is not one."""
    if not isinstance(value, str) or not value:
        return None
    try:
        moment = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is not None:
        moment = moment.astimezone(dt.UTC)
    return moment.date().isoformat()


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


def rate(model, at=None):
    """The rates for `model` (input, output, optional cached_input, source, retrieved), or None.

    With `at`, an ISO timestamp, the entry in force on that UTC date: a `history` entry, which
    carries its `until`, while the date is before it; otherwise the current row.
    """
    found = key(model)
    if not found:
        return None
    row = dict(table()["models"][found])
    history = row.pop("history", [])
    day = _day(at)
    for entry in history if day else ():
        if day < entry["until"]:
            return {**entry, "note": row.get("note", "")}
    return row


def _count(value):
    return value if type(value) is int and value >= 0 else None


def estimate(model, tokens, at=None):
    """USD for one stage's tokens at the table's rates, or None without a rate or token counts.

    `at` is when the stage finished: it is priced at the rate in force then (`rate`).

    Input and output counts are inclusive: input includes cache reads and writes, output includes
    reasoning. Cache reads are priced at the cached-input rate when the row has one and the read
    count is known and within the input count; otherwise every input token is priced at the input
    rate. Cache writes are priced at the input rate, and a per-request long-context premium is not
    visible in a stage's totals, so this is an estimate that can run below the provider's bill.
    """
    row = rate(model, at)
    tokens = tokens if isinstance(tokens, dict) else {}
    inp, out = _count(tokens.get("input_tokens")), _count(tokens.get("output_tokens"))
    if row is None or inp is None or out is None:
        return None
    cached = _count(tokens.get("cached_input_tokens"))
    if "cached_input" in row and cached is not None and cached <= inp:
        return ((inp - cached) * row["input"] + cached * row["cached_input"] + out * row["output"]) / 1e6
    return (inp * row["input"] + out * row["output"]) / 1e6
