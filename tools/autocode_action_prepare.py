"""Prepare a task invocation before the existing controller admission boundary."""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_design_intake as design_intake
    from . import autocode_dispatch as dispatch
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_interventions as interventions
    from . import autocode_member_stop as member_stop
    from . import autocode_milestones as milestones
    from . import autocode_requirements_chat as requirements_chat
    from . import autocode_resolver_human as resolver_human
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_support as support
    from . import autocode_workflows as workflows
except ImportError:
    import autocode_design_intake as design_intake
    import autocode_dispatch as dispatch
    import autocode_goal_lifecycle as lifecycle
    import autocode_interventions as interventions
    import autocode_member_stop as member_stop
    import autocode_milestones as milestones
    import autocode_requirements_chat as requirements_chat
    import autocode_resolver_human as resolver_human
    import autocode_resolver_runtime as resolver_runtime
    import autocode_support as support
    import autocode_workflows as workflows


def prepare(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Migrate the saved run, apply this invocation's settings and queued input, then --retry-builder."""
    released, issued = decided.released, decided.issued
    if state.get("version", 2) < 3:
        backup = run_dir / "state.pre-v3.json"
        if not backup.exists():
            runner.write_json(backup, state)
        lifecycle.migrate(state, fresh=not args.run_dir)
        runner.write_json(state_path, state)
    if args.workflow:  # after migration: a new run's recognizer is begun there
        try:
            workflows.pin(state, args.workflow)
        except ValueError as error:
            parser.error(str(error))
        runner.write_json(state_path, state)
    if not args.run_dir:
        design_intake.queue(state)
        if requirements_chat.queue_opening(state, resolver_human.queue):
            resolver_human.evaluate(state)
        runner.write_json(state_path, state)
    if args.milestone_checkpoints:
        milestones.activate(state)
        if args.max_milestone_seconds is not None:
            state["settings"]["milestone_checkpoints"]["max_seconds"] = args.max_milestone_seconds
        runner.write_json(state_path, state)
    if milestones.apply_queued_activation(state, run_dir):
        print(
            f"Run: {run_dir}\nMilestone checkpoints enabled at a safe boundary; continuing with independent validation.",
            flush=True,
        )
    try:
        if runner.consume_interventions(state, run_dir, workspace, released=released):
            print(f"{state['status']}: {state['stop_reason']}")
            if args.retry_builder:
                print(
                    "--retry-builder was not applied: queued input was applied first, so it was not checked.",
                    file=sys.stderr,
                )
            return 2
    except interventions.InterventionError as error:
        raise support.Paused("PAUSED_INTERVENTION_ACK", str(error)) from error
    print(f"Run: {run_dir}", flush=True)
    if args.migrate_only:
        print("Migrated to an unapproved draft; saved work retained; no agent launched")
        return 0
    try:
        if args.retry_builder and not dispatch.try_request_retry(state, run_dir, args.retry_builder, issued=issued):
            return 2
    except support.Paused as error:
        if not getattr(error, "quota_worker", None):
            raise
        # A refused member with no question open: its stop, collected again, asks the question again (#541).
        member_stop.ask_again(state, error, at=runner.now())
        resolver_runtime.record_operational_exhaustion(runner, state, run_dir, error)
        runner.write_json(state_path, state)
        print(lifecycle.render(state) + "\nNo provider launched.")
        return 2
