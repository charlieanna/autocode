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
    from . import autocode_operational_admission as operational_admission
    from . import autocode_operational_outcome as owner_outcome
    from . import autocode_owned_operations as owned_operations
    from . import autocode_requirements_chat as requirements_chat
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
    import autocode_operational_admission as operational_admission
    import autocode_operational_outcome as owner_outcome
    import autocode_owned_operations as owned_operations
    import autocode_requirements_chat as requirements_chat
    import autocode_resolver_runtime as resolver_runtime
    import autocode_support as support
    import autocode_workflows as workflows


def operation(args):
    """The existing selected Builder retry; its owner still checks the batch."""
    if getattr(args, "retry_builder", None) and not any(owned_operations.user_actions(args).values()):
        return OPERATIONS["builder"]
    if owned_operations.only_action(args, "--migrate-only"):
        return OPERATIONS["metadata"]
    return None


def retry_builder(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Apply only the exact stopped Builder selection, after queued input."""
    try:
        if not dispatch.try_request_retry(state, run_dir, args.retry_builder, issued=decided.issued):
            return 2
        operational_admission.checked(
            runner,
            state,
            run_dir,
            decided,
            "builder",
            {"milestone": args.retry_builder, "batch": dispatch.retry_evidence(state, args.retry_builder)},
        )
    except support.Paused as error:
        if not getattr(error, "quota_worker", None):
            raise
        member_stop.ask_again(state, error, at=runner.now())
        resolver_runtime.record_operational_exhaustion(runner, state, run_dir, error)
        runner.write_json(state_path, state)
        print(lifecycle.render(state) + "\nNo provider launched.")
        return 2
    return None


def prepare(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Migrate the saved run, apply this invocation's settings and queued input, then --retry-builder."""
    released = decided.released
    if state.get("version", 2) < 3:
        backup = run_dir / "state.pre-v3.json"
        if not backup.exists():
            runner.write_json(backup, state)
        lifecycle.migrate(state, fresh=not args.run_dir)
        runner.write_json(state_path, state)
    if args.workflow:  # after migration: a new run's first stage is begun there
        try:
            workflows.pin(state, args.workflow)
        except ValueError as error:
            parser.error(str(error))
        runner.write_json(state_path, state)
    if not args.run_dir:
        design_intake.queue(state)
        requirements_chat.gather_first(state)
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
            print(
                f"{state['status']}: {state.get('stop_reason') or 'Queued input saved; current checkpoint retained.'}"
            )
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
    if args.retry_builder and not owned_operations.completed(decided, "builder"):
        return retry_builder(runner, args, parser, state, state_path, run_dir, workspace, decided)


def validate_builder(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    proof = owner_outcome.proof(state, observed)
    if not isinstance(proof, dict) or not isinstance(proof.get("milestone"), list):
        return None
    selected, evidence = proof["milestone"], proof.get("batch")
    if not selected or not isinstance(evidence, dict):
        return None
    dispatch.validate_retry_evidence(state, selected, evidence)
    return owner_outcome.answer(
        state,
        proof,
        {"selected": selected, "evidence": evidence},
        fields=("builder_retries", "builder_retry_decisions", "resolution_request", "orchestration_batch"),
        members=(("user_events", evidence["event"]),),
    )


OPERATIONS = owned_operations.register(
    owned_operations.Operation("builder", retry_builder, validate_builder),
    owned_operations.Operation(
        "metadata",
        prepare,
        owned_operations.no_work,
        request_bound=False,
        actions=("--migrate-only",),
        frontier_bound=False,
    ),
)
