"""The run's stage pipeline in one list, for a client's always-visible strip (``pipeline``).

Requirements, Planner, Plan Reviewer, Builder, Tester and Completion Reviewer (``autocode_roles.PIPELINE``),
each with where it stands, the model and reasoning level of its route and what it has cost so far. Everything
is read from records the status view already carries: ``routes``, ``stage_decisions`` and ``run_metrics``,
plus the saved running and next stage. A client renders the rows as they are and keeps no table of job names
of its own (the VS Code client may not import ``autocode_roles``); the terminal chat's pipeline (#3070) reads
the same records.

A display copy: it decides nothing, grants nothing and is never evidence that a stage passed. ``ran`` means the
job launched at least once, not that its work was accepted.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_roles as roles
except ImportError:
    import autocode_roles as roles

# Where a job stands: working now, runs next (the run waits or is between stages), launched at least once,
# passed over by a saved decision, not reached yet, or never reached by a run that has ended.
STATES = ("working", "next", "ran", "skipped", "pending", "not_run")


def _mapping(value) -> dict:
    return value if isinstance(value, dict) else {}


def _rows(value) -> list[dict]:
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def _text(value) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _money(value) -> float | None:
    return float(value) if type(value) in (int, float) and value >= 0 else None


def _job(stage, state: RunState) -> str | None:
    return roles.job_for_stage(stage, state) if isinstance(stage, str) and stage else None


def project(state: RunState, view: dict) -> list[dict]:
    """One row per pipeline job, in order: ``job``, ``label``, ``state``, ``model``, ``reasoning_effort``,
    ``spent_usd`` and ``reason`` (why a skipped job was skipped). ``view`` is the status view built so far; it
    must already hold ``done``, ``routes``, ``stage_decisions`` and ``run_metrics``."""
    routes = _mapping(view.get("routes"))
    decisions = _rows(view.get("stage_decisions"))
    ran = {row.get("job") for row in decisions if row.get("decision") == "run"}
    skipped: dict[str, str | None] = {}
    levels: dict[str, str] = {}
    for row in decisions:
        job = row.get("job")
        if not isinstance(job, str):
            continue
        if row.get("decision") == "skip" and job not in ran:
            skipped.setdefault(job, _text(row.get("reason")))
        level = _text(_mapping(row.get("effort_choice")).get("level"))
        if row.get("decision") == "run" and level:
            levels[job] = level  # the level it last ran at, which a rule may have moved from the saved one
    spent = {
        row.get("job"): _money(row.get("spent_usd")) for row in _rows(_mapping(view.get("run_metrics")).get("by_job"))
    }
    done = view.get("done") is True
    active = _mapping(state.get("active_stage"))
    working = None if done else _job(active.get("stage"), state)
    following = None if done or working else _job(state.get("next_stage"), state)
    pipeline = []
    for job in roles.PIPELINE:
        route = _mapping(routes.get(roles.route_for_job(job, routes) or ""))
        label = roles.SCREEN[job]
        if job == working:
            where = "working"
        elif job == following:
            where = "next"
        elif job in ran:
            where = "ran"
        elif job in skipped:
            where = "skipped"
        else:
            where = "not_run" if done else "pending"
        pipeline.append(
            {
                "job": job,
                "label": label,
                "state": where,
                "model": _text(route.get("model")),
                "reasoning_effort": levels.get(job) or _text(route.get("reasoning_effort")),
                "spent_usd": spent.get(label),
                "reason": skipped.get(job) if where == "skipped" else None,
            }
        )
    return pipeline
