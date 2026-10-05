"""What the plan approval stop tells a person (issue #381).

At ``AWAITING_GOAL_APPROVAL`` the brief says which plan revision is waiting and what
approving it authorizes, that its token is a SHA-256 lock on that exact plan, the limits
the run works within, and the commands that approve or revise it. The ``Approval token:``
line itself stays where and as it was: tools parse it.

Pure functions over saved values. Nothing from AutoCode is imported, so the brief
renderer (autocode_goal_lifecycle) uses them without adding to an import cycle.
"""
from __future__ import annotations

import shlex


def intro(revision: int) -> str:
    """Under the brief header, above the token."""
    return (f"Plan revision {revision} (r{revision}) waits for your approval. Approving it authorizes "
            "implementation of exactly this plan: AutoCode may then change files in the workspace to build it.")


def token_note() -> str:
    """Under the ``Approval token:`` line."""
    return ("  The token is a SHA-256 lock on this exact plan: revising the plan changes the token, "
            "and an old token approves nothing.")


def actions(token: str, settings: dict, iteration: int, run_dir=None) -> list[str]:
    """Last: the limits in effect, then how to approve this plan or ask for a change."""
    command = "autocode" + (f" --run-dir {shlex.quote(str(run_dir))}" if run_dir else "")
    return [limits(settings, iteration),
            f"To approve this plan: {command} --approve-goal {shlex.quote(token)}",
            f"To change it instead: {command} --feedback 'WHAT TO CHANGE' (the revised plan gets a new token)"]


def limits(settings: dict, iteration: int) -> str:
    """The saved run limits as one line. A missing or zero time limit is no limit."""
    saved = settings.get("limits") or {}
    run, stage, ceiling = saved.get("max_seconds"), saved.get("stage_timeout_seconds"), saved.get("iteration_ceiling")
    orchestration = settings.get("orchestration") or {}
    builders = orchestration.get("max_parallel") if orchestration.get("enabled") is True else 1
    parts = [f"{duration(run)} of active time for the run" if run else "no time limit for the run",
             f"{duration(stage)} per stage" if stage else "no per-stage time limit",
             "no iteration ceiling" if ceiling is None else f"stops after iteration {ceiling} (now at {iteration})",
             f"up to {builders} Builders at once" if type(builders) is int and builders > 1
             else "one Builder at a time"]
    return "Limits in effect: " + ", ".join(parts) + "."


def field_lines(value, indent=2) -> list[str]:
    """Show structured declarations without implying missing values were measured."""
    prefix = " " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            lines.append(prefix + key.replace("_", " ").capitalize() + ":")
            lines.extend(field_lines(item, indent + 2))
        return lines or [prefix + "(none declared)"]
    if isinstance(value, list):
        return [line for item in value for line in field_lines(item, indent + 2)] or [prefix + "(none declared)"]
    text = "(unmeasured)" if value is None else "yes" if value is True else "no" if value is False else str(value)
    return [prefix + text]


def duration(seconds) -> str:
    if type(seconds) is int and seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if type(seconds) is int and seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"
