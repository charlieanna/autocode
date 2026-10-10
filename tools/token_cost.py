"""Token counts and their estimated API-equivalent cost, for the live-trial scoring tools.

score_autocode_run re-exports these. live_token_sampler imports them from here rather than from
score_autocode_run, which imports the sampler back for its per-step summary.
"""

from __future__ import annotations

try:
    from .autocode_usage import REFERENCE_PRICES
except ImportError:
    from autocode_usage import REFERENCE_PRICES


def token_count(value):
    return value if type(value) is int and value >= 0 else None


def known_sum(values):
    values = list(values)
    return sum(values) if values and all(v is not None for v in values) else None


def normalized_tokens(tokens: dict) -> dict:
    """Runner totals include cached input and reasoning; raw OpenCode does not.

    Match providers/opencode.py normalization, including cache writes. Missing
    fields cannot be inferred to be zero, and explicit zero must survive.
    """
    tokens = tokens if isinstance(tokens, dict) else {}
    if any(k in tokens for k in ("input_tokens", "output_tokens", "cached_input_tokens", "reasoning_output_tokens")):
        result = {
            k: token_count(tokens.get(k))
            for k in ("input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens")
        }
        for subset, total in (("cached_input_tokens", "input_tokens"), ("reasoning_output_tokens", "output_tokens")):
            if tokens.get(subset) is not None and (
                result[subset] is None or (result[total] is not None and result[subset] > result[total])
            ):
                result[total] = None
        return result
    cache = tokens.get("cache")
    if not isinstance(cache, dict):
        cache = {}
    cached, written = token_count(cache.get("read")), token_count(cache.get("write"))
    reasoning = token_count(tokens.get("reasoning"))
    return {
        "input_tokens": known_sum([token_count(tokens.get("input")), cached, written]),
        "cached_input_tokens": cached,
        "output_tokens": known_sum([token_count(tokens.get("output")), reasoning]),
        "reasoning_output_tokens": reasoning,
    }


def estimate_cost(model: str, tokens: dict) -> float | None:
    prices = REFERENCE_PRICES.get(model)
    usage = normalized_tokens(tokens)
    inp, out = usage["input_tokens"], usage["output_tokens"]
    if prices is None or inp is None or out is None:
        return None
    return (inp * prices["input"] + out * prices["output"]) / 1e6


def recorded_model(record: dict) -> str:
    """The launch command wins; mutable current role settings are not history."""
    command = record.get("command")
    if isinstance(command, list):
        for i, arg in enumerate(command):
            if arg in ("--model", "-m") and i + 1 < len(command):
                value = command[i + 1]
                if isinstance(value, str) and value and not value.startswith("-"):
                    return value
            if isinstance(arg, str) and arg.startswith("--model="):
                return arg.partition("=")[2]
    model = record.get("model")
    return model if isinstance(model, str) else ""


def money(value) -> str:
    return "unknown" if value is None else f"${value:.6f}"


def count_text(value) -> str:
    return "unknown" if value is None else str(value)
