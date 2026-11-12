"""AutoCode's own retries of a stage whose answers keep being rejected for different reasons.

A rejected answer goes to the Investigator once per stage and source (autocode_stuck_job). When the
retry it recommends is rejected again with another error, the run used to pause and ask the person to
resume: a question about AutoCode's own retries, which a person is never asked (AGENTS.md). And since
the exact-repeat check (autocode_failures.stalled) restarts on every new error, a stage alternating
between two rejected answers never counted as stuck, however often it was resumed.

So AutoCode decides itself, bounded by the rejected attempts at one source whatever their errors
(``autocode_failures.episode_rejections``, ``LIMIT``):

- below ``LIMIT``, after an investigation that recommended a retry, it runs the stage again (``admit``)
  and records that on the failure's ledger entry (``automatic_retries``), where the status view's
  recoveries show it;
- at ``LIMIT`` the Investigator looks once more (``second_look``), now at the whole run of rejections;
- past that, or when the run's investigation budget is spent, the run stops with what happened
  (``stop_reason``) instead of offering another retry.

Only ``PAUSED_INVALID_OUTPUT`` is retried here. An exact repeat becomes ``PAUSED_REPEATED_FAILURE``, which
keeps its own rules, and an Investigator that said the person must answer keeps that question.
Pure functions over the run state; imports nothing above the domain layer.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_failures as failures
    from . import autocode_roles as roles
    from . import autocode_run_status as run_status
except ImportError:
    import autocode_failures as failures
    import autocode_roles as roles
    import autocode_run_status as run_status

STATUS = "PAUSED_INVALID_OUTPUT"
LIMIT = failures.EPISODE_LIMIT


def _retried(seen: list[dict]) -> bool:
    """Every investigation of this problem recommended a retry, and none is still running."""
    return bool(seen) and all(entry.get("outcome") == "retried" for entry in seen)


def second_look(state: RunState, stage: str, status: str, seen: list[dict]) -> bool:
    """The Investigator's one more look at a problem it already retried, once the stage reached ``LIMIT``."""
    return (
        status == STATUS
        and len(seen) == 1
        and _retried(seen)
        and len(failures.episode_rejections(state, stage)) >= LIMIT
    )


def admit(state: RunState, stage: str, status: str, seen: list[dict], *, at: str) -> bool:
    """Run ``stage`` again after another rejection, below ``LIMIT``; True when the run is running again."""
    if status != STATUS or state.get("active_stage") or not _retried(seen):
        return False
    rows = failures.episode_rejections(state, stage)
    entry = (state.get("failure_history") or {}).get(rows[0].get("failure_key")) if rows else None
    if not entry or len(rows) >= LIMIT:
        return False
    entry.setdefault("automatic_retries", []).append({"at": at, "attempt": len(rows) + 1, "limit": LIMIT})
    if state.get("pending_report_repair"):
        # Spent with the rejected attempt; the before-stage hook would otherwise replay it.
        state.setdefault("report_repair_archive", []).append(
            {
                "at": at,
                "reason": "Superseded by AutoCode's own retry of the stage",
                "repair": state.pop("pending_report_repair"),
            }
        )
    state.pop("paused_at", None)
    run_status.running(state, "PLANNING" if stage in roles.ALL_PLANNING_STAGES else "EXECUTING", next_stage=stage)
    return True


def stop_reason(state: RunState, stage: str, status: str, seen: list[dict], reason: str) -> str:
    """The pause reason once AutoCode stopped retrying: what it tried, without offering another retry."""
    rows = failures.episode_rejections(state, stage) if status == STATUS and seen else []
    if len(rows) < LIMIT:
        return reason
    errors = list(
        dict.fromkeys(str(row.get("rejection_reason") or "").strip() for row in rows if row.get("rejection_reason"))
    )
    looked = "twice" if len(seen) > 1 else "once"
    told = f" The errors were: {' | '.join(errors[:3])}." if len(errors) > 1 else ""
    return reason.replace(failures.REJECTED_RESUME, "").rstrip() + (
        f" AutoCode ran the {roles.screen_name(stage, state)} {len(rows)} times at this source and asked the "
        f"Investigator {looked}; every answer was rejected, so it stopped instead of trying again.{told}"
    )
