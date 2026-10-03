"""Reprice OpenCode request usage at a supplied, frozen API rate card.

This is a comparison estimate, never an invoice or a provider's subscription
price. OpenCode reports fresh input, cache reads and cache writes separately;
visible output and reasoning are separate too. Replayed finish events are one
request, including when later stages reuse a session. Unknown usage/rates remain
unknown rather than turning into zero-cost calls.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .driver import model_routes

FIELDS = ("input", "cache_read", "cache_write", "output")


def request_cost(tokens: dict, model: str, card: dict) -> float:
    rates = card["rates_per_million"].get(model)
    if rates is None:
        raise ValueError(f"no API rates for {model}")
    if any(type(tokens.get(key)) is not int or tokens[key] < 0 for key in FIELDS):
        raise ValueError("missing or invalid request token usage")
    policy = card.get("long_context") or {}
    long = (model in policy.get("models", []) and
            sum(tokens[key] for key in FIELDS[:3]) > policy["input_threshold"])
    result = 0.0
    for key in FIELDS:
        if not tokens[key]:
            continue
        rate = rates.get(key)
        if not isinstance(rate, (int, float)) or isinstance(rate, bool) or rate < 0:
            raise ValueError(f"unknown {key} API rate for {model}")
        multiplier = (policy["output_multiplier"] if key == "output" else policy["input_multiplier"]) if long else 1
        result += tokens[key] * rate * multiplier / 1_000_000
    return result


def estimate(state: dict, card: dict) -> dict:
    """Read saved events after a run stops; include rejected, repair and active calls."""
    requests, issues = {}, []
    stages = [*(state.get("stages") or [])]
    if state.get("active_stage"):
        stages.append(state["active_stage"])
    for stage in stages:
        if stage.get("runner_owned") or stage.get("stage") == "orchestrator":
            continue
        name = stage.get("stage") or "?"
        model = model_routes({"stages": [stage]})[0]["model"]
        path = Path(stage.get("events") or "")
        if stage.get("engine") != "opencode" or not path.is_file() or not model:
            issues.append(f"{name}: missing OpenCode events or model")
            continue
        try:
            events = []
            for line in path.read_text().splitlines():
                if not line.strip():
                    continue
                notice = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", line).strip()
                if notice.startswith("! permission requested:"):
                    continue  # OpenCode emits this known non-JSON UI notice in its transport stream.
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError("event is not an object")
                events.append(row)
        except (OSError, ValueError) as error:
            issues.append(f"{name}: cannot read events: {error}")
            continue
        sessions = {row.get("sessionID") for row in events if isinstance(row.get("sessionID"), str)}
        if len(sessions) != 1:
            issues.append(f"{name}: missing or mixed sessions")
            continue
        parts = {}
        for row in events:
            part = row.get("part")
            phase = row.get("type") in ("step_start", "step_finish")
            if not isinstance(part, dict):
                if phase:
                    issues.append(f"{name}: request phase without a part")
                continue
            if isinstance(part.get("id"), str) and part["id"] and part.get("sessionID") == row.get("sessionID"):
                parts[part["id"]] = row
            elif phase:
                issues.append(f"{name}: request phase without a matching session and part ID")
        phases = [row for row in parts.values() if row.get("type") in ("step_start", "step_finish")]
        if phases and phases[-1]["type"] == "step_start":
            issues.append(f"{name}: unfinished request with no final usage")
        finishes = {id_: row["part"] for id_, row in parts.items() if row.get("type") == "step_finish"}
        if not finishes:
            issues.append(f"{name}: no request usage finishes")
        for part_id, part in finishes.items():
            usage = part.get("tokens") or {}
            if not isinstance(usage, dict) or not isinstance(usage.get("cache") or {}, dict):
                issues.append(f"{name}/{part_id}: invalid token usage object")
                continue
            cache = usage.get("cache") or {}
            visible, reasoning = usage.get("output"), usage.get("reasoning")
            tokens = {"input": usage.get("input"), "cache_read": cache.get("read"),
                      "cache_write": cache.get("write"),
                      "output": visible + reasoning if (type(visible) is int and type(reasoning) is int
                                                         and visible >= 0 and reasoning >= 0) else None}
            try:
                cost = request_cost(tokens, model, card)
            except (KeyError, ValueError) as error:
                issues.append(f"{name}/{part_id}: {error}")
                continue
            key = (next(iter(sessions)), part_id)
            row = {"model": model, "tokens": tokens, "usd": cost}
            if key in requests and requests[key] != row:
                issues.append(f"{name}/{part_id}: conflicting replayed request usage")
                continue
            requests[key] = row
    known = sum(row["usd"] for row in requests.values())
    return {"usd": round(known, 8) if not issues else None, "known_usd": round(known, 8),
            "requests": len(requests), "complete": not issues and bool(requests), "issues": issues,
            "by_model": {model: round(sum(row["usd"] for row in requests.values() if row["model"] == model), 8)
                         for model in sorted({row["model"] for row in requests.values()})}}
