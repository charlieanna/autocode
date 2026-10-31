"""Failed and interrupted work on a saved run (steps of autocode_run_actions.handle).

gate_failures runs first: a classification retry token authorizes its one saved classification
and goes straight to the build, an inspected Investigator report is offered, and a failed job
either stays stopped, takes another model (autocode_job_route) or is retried with its exact token.
resume runs once the pause's authority is settled: --abandon-stage sets a stage aside, and an
explicit --resume-paused applies its recovery flags (--accept-source-edit, --grant-recovery,
--retry-report, --retry-failed-stage, --diagnose-failed-stage) and reconciles an interrupted
stage. Recovery interprets terminal artifacts only; it never replays a model call.

Like every step of autocode_run_actions.handle, its steps take the invocation's arguments and
``decided`` (what earlier steps established), return an exit code to stop or None to go on, and
call the runner's own functions as runner.X, so tests that patch the runner still reach them.
Only autocode_run_actions imports it.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import sys

try:
    from . import autocode_accepted_source as accepted_source
    from . import autocode_classification_recovery as classification_recovery
    from . import autocode_job_failure as job_failure
    from . import autocode_job_report_recovery as job_report_recovery
    from . import autocode_job_route as job_route
    from . import autocode_recovery_accounting as recovery_accounting
    from . import autocode_recovery_dispatch as recovery_dispatch
    from . import autocode_recovery_grants as recovery_grants
    from . import autocode_resolver_human as resolver_human
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_route_advisory as route_advisory
    from . import autocode_runner_check as runner_check
    from . import autocode_stop as stop
    from . import autocode_support as support
except ImportError:
    import autocode_accepted_source as accepted_source
    import autocode_classification_recovery as classification_recovery
    import autocode_job_failure as job_failure
    import autocode_job_report_recovery as job_report_recovery
    import autocode_job_route as job_route
    import autocode_recovery_accounting as recovery_accounting
    import autocode_recovery_dispatch as recovery_dispatch
    import autocode_recovery_grants as recovery_grants
    import autocode_resolver_human as resolver_human
    import autocode_resolver_runtime as resolver_runtime
    import autocode_route_advisory as route_advisory
    import autocode_runner_check as runner_check
    import autocode_stop as stop
    import autocode_support as support


def reconcile_checkpoint(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Publish an uncertain-stage pause under the run lock; never enter a provider/recovery dispatcher."""
    if getattr(args, "reconcile_only", None) is None:
        return None
    try:
        from . import autocode_checkpoint_reconciliation as reconciliation
    except ImportError:
        import autocode_checkpoint_reconciliation as reconciliation
    if stop.applied_stop(state) is not None or stop.pending_stop(run_dir) is not None:
        parser.error("A stop is pending or already applied; inspect that stop before reconciling the checkpoint")
    active = state.get("active_stage") or {}
    fresh = runner.supervision.observe(active.get("supervision"))
    try:
        changed = reconciliation.decision(state, args.reconcile_only, fresh)
    except ValueError as error:
        parser.error(str(error))  # Refusal must not overwrite a still-running checkpoint.
    state.update(changed)
    runner.write_json(state_path, state)
    print(state["stop_reason"])
    return 0  # The metadata action was saved; the run remains paused, not completed.


def gate_failures(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Settle a classification or job failure before any pause authority is weighed."""
    if getattr(args, "_job_cap_change", False):
        try:
            job_failure.change_total_caps(runner, state, run_dir, workspace, args)
        except ValueError as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        print(
            "Total caps saved; job remains paused. Inspect the fresh retry token before authorizing a retry. No provider launched."
        )
        return 0
    classification_gate = classification_recovery.gate(
        state,
        resume=bool(args.resume_paused),
        retry=bool(args.retry_failed_stage),
        token=args.job_retry_token,
        correction=bool(
            args.edit_goal
            or args.goal_file
            or args.feedback is not None
            or args.follow_up is not None
            or args.accept_source_edit
        ),
    )
    if classification_gate == "authorize":
        try:
            classification_recovery.authorize(
                runner, state, run_dir, workspace, args.job_retry_token, supersede_job=job_failure.supersede
            )
        except (ValueError, support.Paused) as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        # This resumes only the saved classification; generic resume bookkeeping
        # would renew unrelated Resolver/report-repair epochs.
        decided.dispatch = True
        return None
    if classification_gate:
        print(f"Input rejected: {classification_gate}", file=sys.stderr)
        return 2
    retained_report = job_report_recovery.offer(runner, state, run_dir, workspace)
    if (
        not args.abandon_stage
        and not retained_report
        and job_failure.recover(runner, state, run_dir, workspace)
        and not args.retry_failed_stage
    ):
        print(state["stop_reason"])
        return 2
    # A job stopped on quota or a content-filter refusal takes another model with its retry token (#463).
    routed = job_route.answer(runner, args, state, run_dir, workspace)
    if routed is not None:
        return routed
    gate = job_failure.resume_gate(
        state, resume=bool(args.resume_paused), retry=bool(args.retry_failed_stage), token=args.job_retry_token
    )
    if gate == "authorize":
        try:
            job_failure.authorize(runner, state, run_dir, workspace, args.job_retry_token)
        except ValueError as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
    elif gate:
        if gate.startswith("this pause"):
            print(f"Input rejected: {gate}", file=sys.stderr)
        else:
            print(gate)
        return 2
    decided.gate = gate
    decided.retained_report = retained_report


def resume(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Set a stage aside (--abandon-stage), or apply an explicit resume's recovery to the saved run."""
    gate, retained_report, information_admitted = decided.gate, decided.retained_report, decided.information_admitted
    if args.abandon_stage is not None:
        try:
            runner.abandon_stage(state, run_dir, workspace, args.abandon_stage, launch=runner)
        except ValueError as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        print(f"{state['status']}: {state['stop_reason']} No agent launched.")
        return 0
    # Recovery interprets terminal artifacts only. It never replays a model call.
    try:
        if args.resume_paused:
            if state.get("status") in ("PAUSED_VERIFICATION_UNCERTAIN", "PAUSED_STAGE_ABANDONED"):
                runner_check.recover_interrupted(state, run_dir, runner.write_json)
            # Acknowledgement is not a new spending/recovery allowance.
            # Counts, elapsed time, repair attempts and receipts persist;
            # an operator who fixed the cause grants more explicitly
            # with --grant-recovery N.
            if state.get("recovery_context") is None:
                state["recovery_context"] = {}
            if getattr(args, "accept_source_edit", False):
                try:
                    accepted_source.accept_reviewed_source(state, workspace)
                except ValueError as error:
                    print(f"Input rejected: {error}", file=sys.stderr)
                    return 2
            # Validate the displayed grant before resume bookkeeping changes
            # the user events bound into the resolver request's identity.
            if args.grant_recovery is not None:
                # Validate the request as issued, then let the grant's single save carry the
                # accepted settings and the invocation's deferred limit_change events together
                # (#2479): no exit can persist one without the other (#2412), and a refused
                # grant persists neither.
                try:
                    recovery_grants.grant(
                        state,
                        run_dir,
                        args.grant_recovery,
                        previous_settings=getattr(args, "_recovery_grant_settings", None),
                        current_request=resolver_human.current,
                        count=runner.recovery_count(state),
                        supersede=resolver_human.supersede_operational,
                        persist=runner.write_json,
                        maximum=recovery_accounting.MAX_AUTOMATIC_RECOVERIES,
                        pending_limit_events=getattr(args, "_grant_limit_events", None) or (),
                    )
                except ValueError as error:
                    print(f"Input rejected: {error}", file=sys.stderr)
                    return 2
                if run_dir is not None:
                    route_advisory.report_changes(
                        getattr(args, "_recovery_grant_settings", None), state.get("settings")
                    )
            # Reset milestone budget counters when raising the limit
            if args.max_milestone_seconds is not None and not (state.get("progressive") or {}).get("budget"):
                for row in state.get("milestone_progress", {}).values():
                    if isinstance(row, dict):
                        row["seconds"] = 0
                        row["seconds_by_role"] = {}
            # Renew the resolver evaluation epoch without resetting the
            # lifetime diagnostic allowance. Report repair is renewed
            # only after exhaustion-gated retry decisions below. A
            # continuation admitted on corrective information renews
            # neither allowance: information never resets a count (#486).
            if not information_admitted and not getattr(decided, "evidence_reconciled", False):
                resolver_runtime.reset_for_resume(state)
            if args.retry_report:
                try:
                    runner.retry_format_failed_report(state, run_dir, workspace, args.retry_report)
                except ValueError as error:
                    print(f"Input rejected: {error}", file=sys.stderr)
                    return 2
            else:
                authorization = None
                if args.retry_failed_stage and gate == "authorize":
                    pass  # exact job authorization was validated before generic recovery
                elif args.retry_failed_stage:
                    try:
                        authorization = runner.resolver_recovery.authorize_retry(
                            runner,
                            state,
                            run_dir,
                            workspace,
                            previous_settings=getattr(args, "_recovery_grant_settings", None)
                            if args.resolver_model or args.resolver_reasoning_effort
                            else None,
                        ) or runner.authorize_failure_retry(state, run_dir, workspace)
                        args._failure_retry_authorization = authorization
                        print(
                            "Failure retry authorized for the recorded repeated failure; "
                            "one fresh attempt proceeds under existing limits.",
                            flush=True,
                        )
                    except ValueError as error:
                        print(f"Input rejected: {error}", file=sys.stderr)
                        return 2
                elif args.diagnose_failed_stage:
                    try:
                        resolver_runtime.admit_operational_diagnosis(runner, state, run_dir, workspace)
                        print(
                            "Diagnosis admitted for the recorded repeated Builder failure; "
                            "a bounded read-only model diagnosis runs before any retry.",
                            flush=True,
                        )
                    except ValueError as error:
                        print(f"Input rejected: {error}", file=sys.stderr)
                        return 2
                runner.prepare_abandoned_completion_revalidation(state, run_dir, workspace)
                runner.prepare_timeout_archived_completion(state, run_dir, workspace)
                runner.prepare_planning_retry(state, run_dir)
                runner.prepare_exhausted_execution_report_retry(state, run_dir, workspace)
                discarded = runner.archive_stale_report_repair(state, run_dir, workspace)
                if discarded:
                    print(discarded, flush=True)
                # Reconcile the saved repair before holding (#1899): the two calls above archive an
                # exhausted or stale one, so this guard judges only what a resume cannot dispatch, and
                # the retry it tells the operator to authorize is then actually admissible.
                runner.repeated_failure_resume_guard(state, workspace, authorization=authorization)
            # Reset report repair attempts on explicit resume, for whatever
            # repair record is still pending. An exhaustion-gated retry
            # above (which requires and archives the true attempt count)
            # already consumed it if one applied; resetting first would
            # corrupt that archived count and always fail those guards.
            if not information_admitted and not getattr(decided, "evidence_reconciled", False):
                runner.reset_report_repair_for_resume(state)
        runner.reconcile_active(state, run_dir, workspace)
    except runner.ReportRepairQueued:
        pass  # Durable pending repair is dispatched below, not original work.
    except support.Paused as error:
        if retained_report:
            raise  # An inspected report offer is not authority to adopt, archive or replay it.
        fired = recovery_dispatch.recover(runner, state, run_dir, workspace, error)
        if fired == "job_failure":
            print(state["stop_reason"])
            return 2
        if fired == "capacity":
            recovery = cast(dict, state["recovery_context"])
            print(
                f"Provider capacity recovery {recovery['retry_number']}/{runner.MAX_AUTOMATIC_CAPACITY_RECOVERIES}: "
                f"partial work archived; the Plan Reviewer will inspect before the next writer",
                flush=True,
            )
        elif fired is None:
            # Reconciliation above retained the crash-uncertain stage and its
            # evidence without inventing completion. A stop recorded before the
            # crash is still authoritative: consume and apply it exactly once at
            # this saved boundary, before this invocation exits, so no manual
            # recovery is needed to stop the run and no later stage is admitted.
            if stop.pending_stop(run_dir) is not None and runner.consume_interventions(state, run_dir, workspace):
                print(f"{state['status']}: {state['stop_reason']}")
                return 2
            raise
    if state.get("uncertain_artifacts"):
        raise support.Paused(
            "PAUSED_UNCERTAIN_STAGE",
            "Legacy partial stage remains unresolved: " + cast(str, state["uncertain_artifacts"]),
        )
