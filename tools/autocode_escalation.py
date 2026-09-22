"""Durable, evidence-triggered model/reasoning escalation for workflow roles."""
from __future__ import annotations

import datetime as dt


LADDERS = {
    "astra": (
        ("openai/gpt-5.6-sol", "high", "Sol High"),
        ("openai/gpt-5.6-sol", "xhigh", "Sol XHigh"),
        ("openai/gpt-6-astra", "high", "Astra High"),
    ),
    "terra": (
        ("openai/gpt-5.6-terra", "medium", "Terra Medium"),
        ("openai/gpt-5.6-terra", "high", "Terra High"),
        ("openai/gpt-5.6-terra", "xhigh", "Terra XHigh"),
        ("openai/gpt-5.6-terra", "max", "Terra Max"),
    ),
    "sol": (
        ("openai/gpt-5.6-sol", "high", "Sol High"),
        ("openai/gpt-5.6-sol", "xhigh", "Sol XHigh"),
        ("openai/gpt-6-astra", "high", "Astra High"),
    ),
    "completion": (
        ("openai/gpt-5.6-sol", "medium", "Sol Medium"),
        ("openai/gpt-5.6-sol", "high", "Sol High"),
        ("openai/gpt-6-astra", "high", "Astra High"),
    ),
}


def _normalized_model(model):
    if not isinstance(model, str):
        return model
    return model if "/" in model else f"openai/{model}"


def rung(role, config):
    """Return the exact configured rung, or None for an explicit custom route."""
    model = _normalized_model(config.get("model"))
    effort = config.get("reasoning_effort")
    return next((index for index, (candidate, reasoning, _label) in enumerate(LADDERS.get(role, ()))
                 if candidate == model and reasoning == effort), None)


def profile(role, config):
    index = rung(role, config)
    return LADDERS[role][index][2] if index is not None else None


def advance(state, role, *, trigger, detail="", struggle_id=None):
    """Advance one rung for the next request and rotate the prior role session.

    Custom providers/models are deliberately left alone. Reaching the final rung is
    also stable: struggle remains recorded by the rejected report or validation,
    without silently inventing another provider route.
    """
    roles = state.get("settings", {}).get("roles", {})
    if struggle_id is not None and any(event.get("role") == role and event.get("struggle_id") == struggle_id
                                      for event in state.get("reasoning_escalations", [])):
        return None
    config = roles.get(role)
    if not isinstance(config, dict) or config.get("provider") not in (None, "openai"):
        return None
    index = rung(role, config)
    ladder = LADDERS.get(role, ())
    if index is None or index + 1 >= len(ladder):
        return None
    model, effort, label = ladder[index + 1]
    previous = {"model": config.get("model"), "reasoning_effort": config.get("reasoning_effort"),
                "profile": ladder[index][2]}
    engine = config.get("engine", state.get("settings", {}).get("engine", "codex"))
    config.update(model=model if engine == "opencode" else model.split("/", 1)[1],
                  reasoning_effort=effort)
    old_session = state.setdefault("sessions", {}).pop(role, None)
    event = {"at": dt.datetime.now(dt.timezone.utc).isoformat(), "role": role,
             "trigger": trigger, "detail": str(detail), "previous": previous,
             "selected": {"model": config["model"], "reasoning_effort": effort, "profile": label}}
    if struggle_id is not None:
        event["struggle_id"] = struggle_id
    state.setdefault("reasoning_escalations", []).append(event)
    if old_session:
        state.setdefault("session_rotations", []).append({"role": role, "old_session": old_session,
            "at": event["at"], "reason": f"Automatic escalation: {previous['profile']} → {label}"})
    return event
