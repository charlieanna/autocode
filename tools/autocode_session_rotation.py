"""The one reported-input bound behind role-session rotation (#1939) and report-repair fresh starts (#4094).

A resumed provider request carries the whole conversation, so a session's reported input grows with
every attempt that resumes it: the original handoff and report, then each repair prompt, rejected
report and refusal. ``rotation_after_input_tokens`` is the run's bound on that growth.
``autocode.rotate_if_needed`` drops a role's saved session once its latest stage record reports that
much input, and a tools-off report launch (``autocode_format_correction``) starts fresh instead of
resuming a session that has reached the same bound. Both read this one threshold and one measurement
of a record's reported input, so a repeated report repair cannot overflow a context an ordinary
dispatch would already have rotated.

The measurement is the provider's own cumulative reported input (``metrics.provider_tokens.input_tokens``):
not a context-window measurement, and not a guess from the new prompt's bytes, which stay bounded
while the resumed conversation grows. A repair resumes one pinned thread, so its bound reads the
largest input reported on that thread — a fresh repair's own small request on another thread does not
shrink the conversation the next resume would carry. Pure functions over the run state; imports
nothing from the runner or the controller.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_names as names
except ImportError:
    import autocode_names as names


def _reported_input(row) -> int | None:
    metrics = row.get("metrics") if isinstance(row, dict) else None
    tokens = metrics.get("provider_tokens") if isinstance(metrics, dict) else None
    value = tokens.get("input_tokens") if isinstance(tokens, dict) else None
    return value if type(value) is int and value > 0 else None


def _route(row) -> str:
    return row.get("route_role", row.get("role", names.stage_route(row.get("stage"))))


def _threads(row) -> set[str]:
    """The session ids a stage record's own evidence names: the thread it saved, the one it was asked
    to resume, and the sessions its provider events reported."""
    sessions = {row[key] for key in ("thread_id", "expected_session") if isinstance(row.get(key), str) and row[key]}
    metrics = row.get("metrics")
    reported = metrics.get("provider_sessions") if isinstance(metrics, dict) else None
    if isinstance(reported, list):
        sessions.update(value for value in reported if isinstance(value, str) and value)
    return sessions


def reported_input(state: RunState, role) -> int | None:
    """The role's latest stage record's cumulative reported input, or None when it reports none."""
    latest = next(
        (row for row in reversed(state.get("stages", [])) if isinstance(row, dict) and _route(row) == role),
        None,
    )
    return _reported_input(latest)


def session_input(state: RunState, thread: str) -> int | None:
    """The largest input reported on ``thread``: the conversation a resume of it would carry."""
    sizes = [
        size
        for row in state.get("stages", [])
        if isinstance(row, dict) and thread in _threads(row)
        if (size := _reported_input(row)) is not None
    ]
    return max(sizes, default=None)


def _reached(state: RunState, tokens: int | None) -> bool:
    limit = (state.get("settings") or {}).get("rotation_after_input_tokens")
    return bool(limit and tokens and tokens >= limit)


def reached(state: RunState, role) -> bool:
    """Whether the role's latest reported input has reached the rotation threshold (#1939)."""
    return _reached(state, reported_input(state, role))


def session_reached(state: RunState, thread: str) -> bool:
    """Whether resuming ``thread`` would carry reported input at or past the same threshold (#4094)."""
    return _reached(state, session_input(state, thread))
