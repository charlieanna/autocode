"""Read-only, exact-job time changes; saving a limit never retries the job."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_budget_state as budget_state
    from . import autocode_pause_command as pause_command
except ImportError:
    import autocode_budget_state as budget_state
    import autocode_pause_command as pause_command


def offers(state: RunState, need, *, run_dir, workspace):
    approval = bool(
        need
        and need.get("kind") == "approve_plan"
        and isinstance(need.get("token"), str)
        and need["token"]
        and state.get("status") == "AWAITING_GOAL_APPROVAL"
        and state.get("displayed_goal") == need["token"]
    )
    if approval and not any(
        state.get(key)
        for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts")
    ):
        limits = (state.get("settings") or {}).get("limits") or {}
        current = limits.get("max_seconds")
        if type(current) is int and current > 0:
            used = budget_state.active_seconds(state)
            if type(used) in (int, float) and math.isfinite(used) and 0 <= used < 9007199254740991:
                minimum = math.floor(max(current, used)) + 1
                return [
                    {
                        "id": "max_seconds",
                        "kind": "integer",
                        "flag": "--max-seconds",
                        "placeholder": "N",
                        "arg_index": 2,
                        "label": "Total active time (seconds)",
                        "current": current,
                        "used": used,
                        "minimum": minimum,
                        "maximum": 9007199254740991,
                        "command": pause_command.command(
                            {
                                "kind": "continue",
                                "token": need["token"],
                                "action": f"--extend-plan-time-limit --max-seconds N --expected-goal-token {need['token']}",
                            },
                            run_dir=run_dir,
                            workspace=workspace,
                            for_client=True,
                        ),
                        "binding": need["token"],
                        "purpose": "approval",
                    }
                ]
    if (
        not need
        or need.get("kind") != "retry_job"
        or not isinstance(need.get("job_retry_token"), str)
        or not need["job_retry_token"]
    ):
        return []
    if any(
        state.get(key)
        for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts")
    ):
        return []
    limits = (state.get("settings") or {}).get("limits") or {}
    failure = state.get("job_failure") or {}
    recovery = state.get("recovery_context") or {}
    rows = []

    def add(identity, flag, label, current, used, maximum):
        if not (type(current) is int and 0 < current < maximum):
            return
        if not (type(used) in (int, float) and 0 <= used < maximum and math.isfinite(used)):
            return
        minimum = math.floor(max(current, used)) + 1
        command = pause_command.command(
            {**need, "action": f"{flag} N --job-retry-token -"},
            run_dir=run_dir,
            workspace=workspace,
            for_client=True,
        )
        rows.append(
            {
                "id": identity,
                "kind": "integer",
                "flag": flag,
                "placeholder": "N",
                "arg_index": 1,
                "label": label,
                "current": current,
                "used": used,
                "minimum": minimum,
                "maximum": maximum,
                "command": command,
            }
        )

    total = limits.get("max_seconds")
    if total:
        add(
            "max_seconds",
            "--max-seconds",
            "Total active time (seconds)",
            total,
            budget_state.active_seconds(state),
            9007199254740991,
        )
    stage = limits.get("stage_timeout_seconds")
    if (
        failure.get("kind") == "timeout"
        and recovery.get("timeout_kind") == "stage"
        and recovery.get("attempt_id") == failure.get("attempt_id")
        and recovery.get("stage") == failure.get("stage")
        and type(stage) is int
        and stage > 0
    ):
        add("max_stage_seconds", "--max-stage-seconds", "Time per step (seconds)", stage, stage, 2147483647)
    return rows
