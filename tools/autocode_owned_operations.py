"""Invocation-local operations owned by the existing action families.

Selection permits only the named owner to inspect its ordinary contract. It
grants no recovery, stage, allowance or result authority and is never saved in
run state or accepted from a provider/client payload.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState


@dataclass(frozen=True)
class Operation:
    name: str
    apply: Callable
    validate: Callable
    request_bound: bool = True
    preserve_request: bool = False
    actions: tuple[str, ...] = ()
    frontier_bound: bool = True
    observes: Callable | None = None


_OPERATIONS: dict[str, Operation] = {}


def register(*operations):
    """Static code ownership; saved data cannot install or replace a callback."""
    names = [operation.name for operation in operations]
    if any(not isinstance(name, str) or not name for name in names) or len(set(names)) != len(names):
        raise ValueError("An owned operation needs one unique handler and frontier validator")
    for operation in operations:
        if not callable(operation.apply) or not callable(operation.validate) or operation.name in _OPERATIONS:
            raise ValueError("An owned operation needs one unique handler and frontier validator")
    for operation in operations:
        _OPERATIONS[operation.name] = operation
    return {operation.name: operation for operation in operations}


def observation_available(state: RunState | None):
    """Each recovery owner declares its own evidence offer; selection grants no work."""
    return state is not None and any(op.observes(state) for op in _OPERATIONS.values() if op.observes)


def resolve(name):
    return _OPERATIONS.get(name) if isinstance(name, str) else None


def for_actions(operation, *actions):
    return replace(operation, actions=tuple(actions))


def authentic(operation):
    registered = resolve(operation.name)
    return registered is not None and replace(operation, actions=registered.actions) == registered


def no_work(*_args, **_kwargs):
    """A selector or metadata-only exit never authorizes stage work."""
    return False


def completed(decided, name):
    """Only this invocation can skip a handler it already applied."""
    return name in decided.completed_operations


def user_actions(args) -> dict[str, bool]:
    """Each user action flag and whether this invocation gives it; at most one may be given."""
    return {
        "--status": bool(args.status),
        "--explain": bool(args.explain),
        "--dry-run": bool(args.dry_run),
        "--migrate-only": bool(args.migrate_only),
        "--reconcile-only": getattr(args, "reconcile_only", None) is not None,
        "--show-goal": bool(args.show_goal),
        "--answer/--delegate": bool(args.answer or args.delegate),
        "--delegate-all": bool(args.delegate_all),
        "--reject-assumption": bool(args.reject_assumption),
        "--approve-goal": bool(args.approve_goal),
        "--edit-goal": bool(args.edit_goal),
        "--goal-file": bool(args.goal_file),
        "--approve-review": bool(args.approve_review),
        "--reject-review": bool(args.reject_review),
        "--reconcile-review": bool(args.reconcile_review),
        "--feedback": args.feedback is not None,
        "--report-discrepancy": getattr(args, "report_discrepancy", None) is not None,
        "--stage-model": getattr(args, "stage_model", None) is not None,
        "--follow-up": args.follow_up is not None,
        "--accept-completion": bool(args.accept_completion),
        "--abandon-stage": args.abandon_stage is not None,
        "--export-repair": bool(args.export_repair),
        "--submit-repair": args.submit_repair is not None,
        "--close-finding": bool(args.close_finding),
        "--request-milestone-checkpoints": bool(args.request_milestone_checkpoints),
        "--planning-review-call-limit": args.planning_review_call_limit is not None,
        "--bind-dependency": bool(args.bind_dependency),
        "--receive-dependency": bool(args.receive_dependency),
    }


def only_action(args, action):
    """Use the parser's exact action inventory when an owner declares metadata."""
    selected = [name for name, present in user_actions(args).items() if present]
    return selected == [action]
