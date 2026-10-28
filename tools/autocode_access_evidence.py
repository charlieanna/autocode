"""Reuse a real call as access evidence, bound to its launch transport and effort.

This proves only route access, never task correctness or tool capability. Legacy
calls without a launch binding remain unknown; current settings cannot backfill it.
"""

from __future__ import annotations

import json
from pathlib import Path

try:
    from . import autocode_role_transport as role_transport
    from .autocode_util import digest
except ImportError:
    import autocode_role_transport as role_transport
    from autocode_util import digest


def launch_binding(settings, role, adapter, model, effort) -> dict | None:
    config = getattr(adapter, "_config", None)
    provider = getattr(adapter, "NAME", None)
    if not isinstance(settings, dict) or not isinstance(config, dict) or not isinstance(config.get("auth"), dict):
        return None
    if not isinstance(model, str) or not model or not isinstance(provider, str):
        return None
    selected = role_transport.entry(settings, role)
    transport = (
        selected["identity"]
        if selected
        else settings.get("transport_identities", {}).get("opencode", settings.get("transport_identity"))
    )
    if not role_transport.valid_identity(transport, provider):
        return None
    identity = {"provider": provider, "transport_sha256": digest(transport)}
    if effort is not None:
        identity["reasoning_effort"] = effort
    return {"schema": 1, "identity": identity, "model": model}


def _completed_events(record, run_dir) -> bool:
    """Use actual local terminal events, never a report's claim of success."""
    try:
        root = Path(run_dir).resolve()
        for key in ("output", "events"):
            name = record.get(key)
            if not isinstance(name, str) or not name:
                return False
            path = Path(name)
            if (
                not path.is_absolute()
                or not path.is_file()
                or path.is_symlink()
                or not path.resolve().is_relative_to(root)
            ):
                return False
        terminal = False
        with Path(record["events"]).open() as source:
            for line in source:
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict) or row.get("type") in {"error", "turn.failed"}:
                    return False
                if row.get("type") == "turn.completed" or (
                    row.get("type") == "step_finish" and (row.get("part") or {}).get("reason") == "stop"
                ):
                    terminal = True
        return terminal
    except (OSError, ValueError, TypeError, AttributeError):
        return False


def passed(records, identity, run_dir) -> dict[str, dict]:
    """Only clean, charged real calls under the exact requested identity qualify.

    An accepted report-only repair is also a real request to that route. Its own
    launch binding and terminal events prove access; the rejected original does
    not. This grants no task correctness, tool capability or execution evidence.
    """
    if not isinstance(identity, dict) or not isinstance(records, list):
        return {}
    outcomes: dict[str, dict] = {}
    for record in records:
        if not isinstance(record, dict) or record.get("engine") != "opencode":
            continue
        binding = record.get("access_preflight_identity")
        if not isinstance(binding, dict) or set(binding) != {"schema", "identity", "model"}:
            continue
        if type(binding["schema"]) is not int or binding["schema"] != 1 or binding["identity"] != identity:
            continue
        model = binding["model"]
        route = record.get("launch_route") or {}
        if not isinstance(model, str) or not model or not isinstance(route, dict) or route.get("model") != model:
            continue
        if record.get("provider") != identity.get("provider") or record.get("reasoning_effort") != identity.get(
            "reasoning_effort"
        ):
            continue
        if route.get("reasoning_effort") != identity.get("reasoning_effort"):
            continue
        # Records are chronological. A later unsuccessful attempt on the same
        # bound route invalidates older access evidence, without granting a retry.
        outcomes.pop(model, None)
        if record.get("accounted") is not True or type(record.get("exit_code")) is not int or record["exit_code"] != 0:
            continue
        if not isinstance(record.get("finished_at"), str) or not record["finished_at"]:
            continue
        if any(
            record.get(key) for key in ("timed_out", "interrupted", "rejected", "dry_run", "replay", "cleanup_error")
        ):
            continue
        if not _completed_events(record, run_dir):
            continue
        outcomes[model] = {
            "status": "ok",
            "source": "completed_stage",
            "stage": record.get("stage"),
            "evidence": record["events"],
        }
    return outcomes
