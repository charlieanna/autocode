"""Explicit, choice-bound timeout changes; no authority is inferred from prose."""

from __future__ import annotations

import copy
import json

PATHS = frozenset(
    {
        "settings.regression.test_timeout",
        "settings.limits.tool_timeout_seconds",
        "settings.limits.stage_timeout_seconds",
    }
)


def options(request):
    """Read the optional payload carried by the existing delta string."""
    delta = request.get("proposed_delta", "")
    if not isinstance(delta, str) or not delta.lstrip().startswith("{"):
        return None
    try:
        payload = json.loads(delta)
    except ValueError as error:
        raise ValueError("Operational changes need valid JSON in proposed_delta") from error
    if not isinstance(payload, dict) or "operational_options" not in payload:
        return None
    if request.get("kind") != "permission" or set(payload) != {"operational_options"}:
        raise ValueError("Operational changes require a permission request with operational_options only")
    rows = payload["operational_options"]
    offered = request.get("options", [])
    if not isinstance(rows, list) or not rows or not isinstance(offered, list):
        raise ValueError("Operational changes need explicit offered choices")
    names = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != {"option", "changes"}:
            raise ValueError("Each operational option needs only option and changes")
        name, changes = row["option"], row["changes"]
        if not isinstance(name, str) or not name.strip() or not isinstance(changes, dict):
            raise ValueError("Operational choices need nonempty text and a settings map")
        names.append(name)
        for path, value in changes.items():
            if path not in PATHS or type(value) is not int or not 0 < value <= 2_147_483_647:
                raise ValueError("Operational changes support only positive, finite suite/tool/stage timeouts")
    if names != offered or len(names) != len(set(names)):
        raise ValueError("Operational options must match every offered choice exactly, in order")
    if not any(not row["changes"] for row in rows) or not any(row["changes"] for row in rows):
        raise ValueError("Operational choices need both a concrete change and an unchanged/decline option")
    return rows


def _value(settings, path):
    _, group, key = path.split(".")
    values = settings.get(group, {})
    if not isinstance(values, dict):
        raise ValueError("Operational timeout settings must be objects")
    return {"present": key in values, "value": copy.deepcopy(values.get(key))}


def bind(state, request, question):
    """Pin precisely the settings offered by this question before publication."""
    rows = options(request)
    if rows is None:
        return
    paths = {path for row in rows for path in row["changes"]}
    question["operational_settings"] = {path: _value(state.get("settings", {}), path) for path in sorted(paths)}
    question["operational_options"] = copy.deepcopy(rows)
    descriptions = [
        row["option"]
        + ": "
        + (
            ", ".join(f"{path}={value}s" for path, value in row["changes"].items())
            or "leave timeout settings unchanged"
        )
        for row in rows
    ]
    question["why"] += "\nExact operational effects: " + "; ".join(descriptions)


def apply(state, request, question, text):
    """Apply only an exact selected option, after validating all input pins."""
    rows = options(request)
    if rows is None:
        return None
    selected = next((row for row in rows if row["option"] == text), None)
    if selected is None:
        raise ValueError("Choose an operational option exactly as displayed; free-text consent changes no settings")
    expected = question.get("operational_settings")
    paths = {path for row in rows for path in row["changes"]}
    settings = state.get("settings", {})
    if (
        not isinstance(expected, dict)
        or set(expected) != paths
        or any(_value(settings, path) != expected[path] for path in paths)
    ):
        raise ValueError(
            "Operational timeout settings changed since this question was issued; request a fresh decision"
        )
    updated = copy.deepcopy(settings)
    receipt = {}
    for path, value in selected["changes"].items():
        _, group, key = path.split(".")
        updated.setdefault(group, {})[key] = value
        if group == "limits":
            updated.setdefault("budget_origins", {})[key] = "user_explicit"
        receipt[path] = {"before": expected[path], "after": value}
    state["settings"] = updated
    return receipt


def update_overrides(argv, changes):
    """Keep a TaskRun client's existing timeout flags aligned after consent."""
    flags = {
        "--max-tool-seconds": "settings.limits.tool_timeout_seconds",
        "--max-stage-seconds": "settings.limits.stage_timeout_seconds",
    }
    result = list(argv)
    for index, argument in enumerate(result):
        flag, separator, _ = argument.partition("=")
        path = flags.get(flag)
        if path not in changes:
            continue
        if separator:
            result[index] = flag + "=" + str(changes[path])
        elif index + 1 < len(result):
            result[index + 1] = str(changes[path])
    return tuple(result)
