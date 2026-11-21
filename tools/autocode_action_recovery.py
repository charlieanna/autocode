"""Failed and interrupted work on a saved run (steps of autocode_run_actions.handle).

gate_failures runs first: an approval-boundary time change saves only the larger limit, a
classification retry token authorizes its one saved classification and goes straight to the
build, an inspected Investigator report is offered, and a failed job either stays stopped,
takes another model (autocode_job_route) or is retried with its exact token.
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

import copy
import sys

try:
    from . import autocode_accepted_source as accepted_source
    from . import autocode_action_retained_recovery as retained_recovery
    from . import autocode_classification_recovery as classification_recovery
    from . import autocode_interrupted_review_recovery as interrupted_review
    from . import autocode_job_failure as job_failure
    from . import autocode_job_route as job_route
    from . import autocode_limit_events as limit_events
    from . import autocode_model_listing_failure as model_listing_failure
    from . import autocode_operational_admission as operational_admission
    from . import autocode_operational_outcome as owner_outcome
    from . import autocode_owned_operations as owned_operations
    from . import autocode_plan_time as plan_time
    from . import autocode_recovery_accounting as recovery_accounting
    from . import autocode_recovery_dispatch as recovery_dispatch
    from . import autocode_recovery_grants as recovery_grants
    from . import autocode_report_correction as report_correction
    from . import autocode_resolver_human as resolver_human
    from . import autocode_resolver_recovery as resolver_recovery
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_route_advisory as route_advisory
    from . import autocode_run_status as run_status
    from . import autocode_runner_check as runner_check
    from . import autocode_source_reconciliation as source_reconciliation
    from . import autocode_stage_recovery as stage_recovery
    from . import autocode_stage_stalls as stage_stalls
    from . import autocode_stop as stop
    from . import autocode_support as support
    from . import autocode_verification_failure as verification_failure
except ImportError:
    import autocode_accepted_source as accepted_source
    import autocode_action_retained_recovery as retained_recovery
    import autocode_classification_recovery as classification_recovery
    import autocode_interrupted_review_recovery as interrupted_review
    import autocode_job_failure as job_failure
    import autocode_job_route as job_route
    import autocode_limit_events as limit_events
    import autocode_model_listing_failure as model_listing_failure
    import autocode_operational_admission as operational_admission
    import autocode_operational_outcome as owner_outcome
    import autocode_owned_operations as owned_operations
    import autocode_plan_time as plan_time
    import autocode_recovery_accounting as recovery_accounting
    import autocode_recovery_dispatch as recovery_dispatch
    import autocode_recovery_grants as recovery_grants
    import autocode_report_correction as report_correction
    import autocode_resolver_human as resolver_human
    import autocode_resolver_recovery as resolver_recovery
    import autocode_resolver_runtime as resolver_runtime
    import autocode_route_advisory as route_advisory
    import autocode_run_status as run_status
    import autocode_runner_check as runner_check
    import autocode_source_reconciliation as source_reconciliation
    import autocode_stage_recovery as stage_recovery
    import autocode_stage_stalls as stage_stalls
    import autocode_stop as stop
    import autocode_support as support
    import autocode_verification_failure as verification_failure


def reconcile_checkpoint(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Settle a proven read-only interruption or retain the uncertainty; never launch a provider."""
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
    if interrupted_review.settle(runner, state, run_dir, workspace, fresh):
        print("Stopped read-only attempt archived; the run can continue under its saved limits and model choices.")
        return 0
    state.update(changed)
    resolver_runtime.record_operational_exhaustion(
        runner, state, run_dir, support.Paused(state["status"], state["stop_reason"])
    )
    runner.write_json(state_path, state)
    print(state["stop_reason"])
    return 0  # The metadata action was saved; the run remains paused, not completed.


def gate_failures(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Settle a classification or job failure before any pause authority is weighed."""
    if getattr(args, "_approval_time_change", False):
        try:
            plan_time.change(runner, state, state_path, args)
        except ValueError as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        print("Total active-time limit saved. Plan approval is still pending; no provider launched.")
        return 0
    if getattr(args, "_job_cap_change", False):
        try:
            job_failure.change_total_caps(runner, state, run_dir, workspace, args)
        except ValueError as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        print(
            "Limits saved; job remains paused. Inspect the fresh retry token before authorizing a retry. No provider launched."
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
    if (
        not args.abandon_stage
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
    elif gate == "automatic":
        # A stopped job with nothing for a person to decide: its one automatic retry, or a recorded end.
        if not job_failure.settle(runner, state, run_dir, workspace):
            print(state.get("stop_reason"))
            return 2
        gate = "authorize"  # Authorized as the exact retry is; the retry operation adds nothing.
    elif gate:
        if gate.startswith("this pause"):
            print(f"Input rejected: {gate}", file=sys.stderr)
        else:
            print(gate)
        return 2
    decided.gate = gate


def operations(args, state: RunState | None = None):
    """Only this family's explicit reset-free handlers, in their ordinary order."""
    if getattr(args, "abandon_stage", None) is not None:
        return (OPERATIONS["abandon"],)
    selected = []
    if getattr(args, "resume_paused", False):
        if getattr(args, "grant_recovery", None) is not None:
            selected.append(OPERATIONS["grant"])
        if getattr(args, "retry_report", None):
            selected.append(OPERATIONS["report"])
        elif getattr(args, "retry_failed_stage", False):
            selected.append(
                OPERATIONS[
                    "resolver_retry" if state is not None and resolver_recovery.retry_hold(state) else "failure_retry"
                ]
            )
        if not selected and getattr(args, "_explicit_budget_flags", None):
            selected.append(OPERATIONS["bound"])
    # Observing retained evidence grants no authority itself. The owner must
    # prove its existing credit and pass final admission on plain continuation
    # too; an operator's resume flag adds nothing to that proof.
    if (
        not selected
        and owned_operations.observation_available(state)
        and not any(owned_operations.user_actions(args).values())
        and not any(
            (
                args.retry_builder,
                args.diagnose_failed_stage,
                args.accept_source_edit,
                args.grant_recovery is not None,
                args.retry_report,
                args.retry_failed_stage,
                getattr(args, "_explicit_budget_flags", None),
            )
        )
    ):
        selected.append(OPERATIONS["observe"])
    return tuple(selected)


def observe(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Only the existing read-only recovery owners may establish a plain resume."""
    handled, code = retained_recovery.settle(runner, args, parser, state, state_path, run_dir, workspace, decided)
    if handled:
        return code
    if resolver_runtime.reconsider_operational_request(runner, state, run_dir, workspace):
        decided.operational_selection = "planning_reserve"
        proof = resolver_runtime.validate_operational_grant(state, state.get("next_stage"), workspace)
        operational_admission.checked(runner, state, run_dir, decided, "planning_reserve", proof)
        return None
    origin = operational_admission.original_cause(state, decided)
    origin = origin if state.get("status") in ("WAITING_FOR_USER", "RESOLVER_PENDING") else None
    proof = stage_recovery.abandoned_completion_evidence(state, workspace, origin=origin)
    if proof and stage_recovery.prepare_abandoned_completion_revalidation(state, run_dir, workspace, origin=origin):
        decided.operational_selection = "archive_completion"
        operational_admission.checked(runner, state, run_dir, decided, "archive_completion", proof)
        return None
    proof = stage_recovery.timeout_completion_evidence(state, workspace, origin=origin)
    if proof and stage_recovery.prepare_timeout_archived_completion(state, run_dir, workspace, origin=origin):
        decided.operational_selection = "archive_timeout"
        operational_admission.checked(runner, state, run_dir, decided, "archive_timeout", proof)
        return None
    return operational_admission.refuse()


def abandon(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    original = copy.deepcopy(state.get("active_stage"))
    try:
        runner.abandon_stage(state, run_dir, workspace, args.abandon_stage, launch=runner)
        operational_admission.abandoned(runner, state, run_dir, decided, original)
    except ValueError as error:
        print(f"Input rejected: {error}", file=sys.stderr)
        return 2
    print(f"{state['status']}: {state['stop_reason']} No agent launched.")
    return 0


def grant(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    if owned_operations.completed(decided, "grant"):
        return None
    # Its single save validates the request as issued and commits the selected
    # settings/deferred events together, before any resume bookkeeping.
    try:
        granted = recovery_grants.grant(
            state,
            run_dir,
            args.grant_recovery,
            previous_settings=getattr(args, "_recovery_grant_settings", None),
            current_request=lambda saved: resolver_human.current(saved) or resolver_human.recovery_request(saved),
            queued_request=resolver_human.queued_operational,
            count=runner.recovery_count(state),
            supersede=resolver_human.supersede_operational,
            persist=runner.write_json,
            maximum=recovery_accounting.MAX_AUTOMATIC_RECOVERIES,
            pending_limit_events=getattr(args, "_grant_limit_events", None) or (),
        )
        operational_admission.checked(runner, state, run_dir, decided, "grant", granted)
    except ValueError as error:
        print(f"Input rejected: {error}", file=sys.stderr)
        return 2
    if run_dir is not None:
        route_advisory.report_changes(getattr(args, "_recovery_grant_settings", None), state.get("settings"))
    return None


def retry_report(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    if owned_operations.completed(decided, "report"):
        return None
    try:
        outcome = runner.retry_format_failed_report(state, run_dir, workspace, args.retry_report)
        operational_admission.checked(runner, state, run_dir, decided, "report", outcome)
    except ValueError as error:
        print(f"Input rejected: {error}", file=sys.stderr)
        return 2
    return None


def retry_failure(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    if owned_operations.completed(decided, "failure_retry") or owned_operations.completed(decided, "resolver_retry"):
        return None
    if decided.gate == "authorize":
        return None  # The exact job owner already authorized its own retry.
    try:
        if (
            getattr(decided, "operational_selection", None) == "failure_retry"
            or stage_stalls.exhausted_unlaunchable_pending(state)
        ) and not runner.prepare_planning_retry(state, run_dir):
            runner.prepare_exhausted_execution_report_retry(state, run_dir, workspace, allow_repeated=True)
        authorization = runner.resolver_recovery.authorize_retry(
            runner,
            state,
            run_dir,
            workspace,
            previous_settings=getattr(args, "_recovery_grant_settings", None)
            if args.resolver_model or args.resolver_reasoning_effort
            else None,
        )
        selection = "resolver_retry" if authorization is not None else "failure_retry"
        if authorization is None:
            authorization = runner.authorize_failure_retry(state, run_dir, workspace)
        if decided.operational_selection == "failure_retry":
            decided.operational_selection = selection
        args._failure_retry_authorization = authorization
        decided.failure_retry_authorization = authorization
        operational_admission.checked(runner, state, run_dir, decided, selection, authorization)
        print(
            "Failure retry authorized for the recorded repeated failure; one fresh attempt proceeds under existing limits.",
            flush=True,
        )
    except ValueError as error:
        print(f"Input rejected: {error}", file=sys.stderr)
        return 2
    return None


def acknowledge_bound(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """The locked setup already committed its validated limit and owner event."""
    operational_admission.checked(runner, state, run_dir, decided, "bound", {"settings": state.get("settings")})
    return None


def resume(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Set a stage aside (--abandon-stage), or apply an explicit resume's recovery to the saved run."""
    information_admitted = decided.information_admitted
    if blocked := model_listing_failure.blocked(state):
        before = tuple(state.get(key) for key in ("status", "phase", "stop_reason"))
        run_status.pause(state, "PAUSED_TRANSPORT_UNVERIFIED", blocked["summary"])
        if before != tuple(state.get(key) for key in ("status", "phase", "stop_reason")):
            runner.write_json(state_path, state)
        print(
            "AutoCode is blocked on unconfirmed model-listing descendant cleanup. No stage was started.",
            file=sys.stderr,
        )
        return 2
    if blocked := verification_failure.blocked(state):
        before = tuple(state.get(key) for key in ("status", "phase", "stop_reason"))
        run_status.pause(state, "PAUSED_VERIFICATION_PREPARATION", blocked["summary"])
        if before != tuple(state.get(key) for key in ("status", "phase", "stop_reason")):
            runner.write_json(state_path, state)
        print(
            "AutoCode is blocked on unconfirmed verification preparation source binding, cleanup or ownership. No stage was started.",
            file=sys.stderr,
        )
        return 2
    if getattr(decided, "operational_incident", None) is None:
        refused = operational_admission.guard(runner, args, parser, state, state_path, run_dir, workspace, decided)
        if refused is not None:
            return refused
    if getattr(decided, "operational_selection", None) in ("planning_input", "planning_reserve"):
        # Its existing owner-input handler runs later. A requested resume must
        # not reset internal recovery/report allowances before that commit.
        return None
    if args.abandon_stage is not None:
        return abandon(runner, args, parser, state, state_path, run_dir, workspace, decided)
    # Recovery interprets terminal artifacts only. It never replays a model call.
    prior_next_action = state.get("next_action") if isinstance(state.get("next_action"), str) else None
    try:
        if args.resume_paused:
            if source_reconciliation.prepare(runner, state, workspace):
                decided.evidence_reconciled = True
                args._resumed_pause = None  # Runner recovery, not a person's resume or an allowance renewal.
                runner.write_json(state_path, state)
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
            if (
                args.grant_recovery is not None
                and (code := grant(runner, args, parser, state, state_path, run_dir, workspace, decided)) is not None
            ):
                return code
            paused_before = state.get("status")
            # One decision (autocode_report_correction): a saved report the current validator accepts is
            # applied; one it authenticates but now rejects only for its contents gets a report-only
            # correction. Either way no baseline, proof or fresh attempt; otherwise today's path below.
            correction = (
                report_correction.resume(runner, state, run_dir, workspace)
                if not args.retry_report and getattr(decided, "operational_incident", None) is None
                else None
            )
            revalidated_report = correction is not None
            if revalidated_report:
                # A correction is still the person's ordinary resume: it renews the Resolver's evaluation
                # epoch as today's retry would; its report-repair allowance was already renewed by the runner.
                decided.evidence_reconciled = decided.evidence_reconciled or correction == "accepted"
                if state.get("status") == paused_before and paused_before in (
                    "PAUSED_REPEATED_FAILURE",
                    "PAUSED_INVALID_OUTPUT",
                    "PAUSED_REPORT_REPAIR_LIMIT",
                    "PAUSED_REPORT_REPAIR_INPUT",
                ):
                    stop.acknowledge_pause(state, runner.now())
                    run_status.running(
                        state,
                        "PLANNING"
                        if runner.planning.is_planning(state, state["next_stage"])
                        else "DISCOVERING"
                        if state["next_stage"] == "draft_plan"
                        else "EXECUTING",
                        next_stage=state.get("next_stage"),
                    )
                    runner.write_json(state_path, state)
                print(
                    "Pinned report revalidated under current checks; saved stage output accepted without launching a retry."
                    if correction == "accepted"
                    else "Saved report authenticated but rejected by the current checks; a report-only correction "
                    "runs with the original execution evidence, without a new baseline, proof or attempt.",
                    flush=True,
                )
            # Renew the resolver evaluation epoch without resetting the
            # lifetime diagnostic allowance. Report repair is renewed
            # only after exhaustion-gated retry decisions below. A
            # continuation admitted on corrective information renews
            # neither allowance: information never resets a count (#486).
            if (
                not information_admitted
                and not getattr(decided, "evidence_reconciled", False)
                and not getattr(decided, "operational_incident", None)
            ):
                resolver_runtime.reset_for_resume(state)
            if args.retry_report:
                if (
                    code := retry_report(runner, args, parser, state, state_path, run_dir, workspace, decided)
                ) is not None:
                    return code
            elif not revalidated_report:
                authorization = None
                if args.retry_failed_stage:
                    if (
                        code := retry_failure(runner, args, parser, state, state_path, run_dir, workspace, decided)
                    ) is not None:
                        return code
                    authorization = getattr(args, "_failure_retry_authorization", None)
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
                if not (hasattr(decided, "completed_operations") and owned_operations.completed(decided, "observe")):
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
                decided.failure_retry_consumed = bool(
                    isinstance(authorization, dict)
                    and authorization.get("consumed") is True
                    and authorization is getattr(decided, "failure_retry_authorization", None)
                )
            # Reset report repair attempts on explicit resume, for whatever
            # repair record is still pending. An exhaustion-gated retry
            # above (which requires and archives the true attempt count)
            # already consumed it if one applied; resetting first would
            # corrupt that archived count and always fail those guards.
            if (
                not information_admitted
                and not getattr(decided, "evidence_reconciled", False)
                and not getattr(decided, "operational_incident", None)
            ):
                runner.reset_report_repair_for_resume(state)
        if args.resume_paused and state.get("no_progress_reports"):
            runner.autopilot.recover_retained_candidate(state, workspace, bound_only=True)
        if (
            args.resume_paused
            and state.get("builder_failure_hold")
            and (
                (
                    hasattr(runner.autopilot.changed_code_rework, "reconcile_classified_hold")
                    and runner.autopilot.changed_code_rework.reconcile_classified_hold(
                        state,
                        workspace,
                        route=runner.autopilot.route_builder_failure,
                        prior_next_action=prior_next_action,
                    )
                )
                or (
                    hasattr(runner.autopilot.builder_failure, "reconcile_routable_hold")
                    and runner.autopilot.builder_failure.reconcile_routable_hold(
                        state,
                        workspace,
                        route_failure=runner.autopilot.route_builder_failure,
                        diagnose_rework=runner.autopilot.changed_code_rework.diagnosed,
                        apply_resolver_continuation=lambda current, diag, rev: (
                            hasattr(runner.autopilot, "apply_saved_resolver_continuation")
                            and runner.autopilot.apply_saved_resolver_continuation(
                                runner, current, diag, rev, workspace, run_dir
                            )
                        ),
                        prior_next_action=prior_next_action,
                        enabled=runner.autopilot.stuck.enabled(state),
                    )
                )
            )
        ):
            stop.acknowledge_pause(state, runner.now())
            runner.write_json(state_path, state)
        runner.reconcile_active(state, run_dir, workspace)
    except runner.ReportRepairQueued:
        pass  # Durable pending repair is dispatched below, not original work.
    except support.Paused as error:
        fired = (
            None
            if getattr(decided, "operational_incident", None)
            else recovery_dispatch.recover(runner, state, run_dir, workspace, error)
        )
        if fired == "job_failure" and state.get("status") != "RUNNING":
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
    decided.resume_initialization = bool(args.resume_paused)
    if getattr(decided, "operational_selection", None) == "bound":
        operational_admission.checked(runner, state, run_dir, decided, "bound", {"settings": state.get("settings")})
    if getattr(decided, "operational_incident", None):
        decided.operational_resume_deferred = bool(args.resume_paused and decided.operational_selection != "abandon")
        # --retry-builder is validated by prepare after queued input is applied.
        # Reaching that metadata handler grants no dispatch or resume allowance.
        if getattr(decided, "operational_selection", None) == "builder":
            return None
        if not operational_admission.ready(state, decided):
            return operational_admission.refuse()


def hold_newer_pause(runner, args, state: RunState, state_path):
    """An earlier resume cannot release a pause accepted during its reconciliation."""
    if getattr(args, "resume_paused", False) and stop.hold_newer_pause(
        state, getattr(args, "_resume_pause_frontier", stop.pause_frontier(state))
    ):
        runner.write_json(state_path, state)
        print(f"{state['status']}: {state['stop_reason']}")
        return 2
    return None


def validate_grant(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    proof = owner_outcome.proof(state, observed)
    facts = recovery_grants.committed_outcome(state, proof)
    return owner_outcome.answer(
        state,
        proof,
        facts,
        fields=("recovery_grants", "automatic_recoveries_since_resume", "consecutive_timeout_recoveries"),
        members=(("user_events", facts["event"]),),
    )


def validate_bound(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    # This is evidence of the specific settings transition already validated by
    # locked CLI setup, not a second eligibility rule for a pause status.
    disposition = operational_admission.disposition
    evidence = disposition.original_detail(state, row).get("evidence") or {}
    original = evidence.get("budget") or {}
    current = disposition.budget(state, evidence.get("status"))
    if not (
        current.get("kind")
        and current.get("kind") == original.get("kind")
        and current.get("origin") == "user_explicit"
        and current.get("limit") != original.get("limit")
    ):
        return None
    from_events = disposition.later_events(state, row)
    if from_events is None:
        return None
    event = next(
        (
            event
            for event in reversed(from_events)
            if isinstance(event, dict)
            and event.get("kind") == limit_events.KIND
            and event.get("actor") == "user_cli"
            and event.get("limit") == current["kind"]
            and event.get("value") == current.get("limit")
            and event.get("previous") == original.get("limit")
        ),
        None,
    )
    if current["kind"] in limit_events.LIMIT_FLAGS and event is None:
        return None
    facts = {"bound": current, "event": event}
    return owner_outcome.answer(
        state,
        owner_outcome.proof(state, observed),
        facts,
        fields=("settings",),
        members=(("user_events", event),) if event else (),
    )


def validate_abandon(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    proof = owner_outcome.proof(state, observed)
    if not isinstance(proof, dict):
        return None
    operational_admission._validate_abandoned_archive(proof)
    original = operational_admission.disposition.original_detail(state, row)
    evidence = original.get("evidence") or {}
    if (
        original.get("evaluation_binding", {}).get("active_stage") != support.digest(proof.get("original"))
        or (proof.get("original") or {}).get("stage") != evidence.get("stage")
        or operational_admission.disposition.budget(state, evidence.get("status")) != evidence.get("budget")
    ):
        return None
    record = proof["record"]
    event = next(
        (
            event
            for event in reversed(state.get("user_events") or [])
            if isinstance(event, dict)
            and event.get("kind") == "stage_abandoned"
            and event.get("actor") == "user_cli"
            and event.get("attempt_id") == proof.get("selected")
            and event.get("changed_files") == record.get("changed_files")
        ),
        None,
    )
    if state.get("active_stage") or event is None or record not in (state.get("stages") or []):
        return None
    return owner_outcome.answer(
        state,
        proof,
        {"record": record, "event": event},
        fields=(
            "active_stage",
            "recovery_context",
            "settings",
            "failure_history",
            "recovery_grants",
            "planning_history",
            "report_repair_archive",
            "active_seconds",
            "milestone_active_seconds",
            "automatic_recoveries_since_resume",
            "consecutive_timeout_recoveries",
            "automatic_timeout_recoveries",
            "automatic_capacity_recoveries",
            "automatic_permission_recoveries",
            "no_progress_batches",
        ),
        members=(("stages", record), ("user_events", event)),
    )


def validate_reserve(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    proof = owner_outcome.proof(state, observed)
    current = resolver_runtime.validate_operational_grant(state, state.get("next_stage"), workspace)
    if proof != current:
        return None
    return owner_outcome.answer(state, proof, {"grant": current}, fields=("planning",))


def validate_archive(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    proof = owner_outcome.proof(state, observed)
    stage_recovery.validate_abandoned_completion_evidence(state, workspace, proof)
    return owner_outcome.answer(
        state,
        proof,
        {"archive": proof},
        fields=("current_task", "goal_contract", "failure_history", "recovery_context", "validation_archive"),
    )


def validate_timeout_archive(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    proof = owner_outcome.proof(state, observed)
    stage_recovery.validate_timeout_completion_evidence(state, workspace, proof)
    return owner_outcome.answer(
        state,
        proof,
        {"archive": proof},
        fields=(
            "current_task",
            "goal_contract",
            "failure_history",
            "recovery_context",
            "validation_archive",
            "validation",
        ),
    )


def validate_report(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    proof = owner_outcome.proof(state, observed)
    stage_recovery.validate_report_retry_outcome(state, workspace, proof)
    return owner_outcome.answer(
        state,
        proof,
        {"report": proof},
        fields=("pending_report_repair", "report_repair_archive", "failure_history"),
        members=(("user_events", proof["event"]),),
    )


def validate_failure_retry(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    authorization = decided.failure_retry_authorization
    proof = owner_outcome.proof(state, observed)
    if (
        not isinstance(authorization, dict)
        or args is not None
        and authorization is not getattr(args, "_failure_retry_authorization", None)
        or not isinstance(proof, dict)
        or {key: value for key, value in authorization.items() if key != "consumed"}
        != {key: value for key, value in proof.items() if key != "consumed"}
        or (
            authorization.get("consumed") is True
            if not final
            else (not decided.failure_retry_consumed or authorization.get("consumed") is not True)
        )
    ):
        return None
    try:
        stage_recovery.validate_failure_retry_authorization(state, workspace, authorization)
    except (ValueError, OSError) as error:
        print(f"Input rejected: {error}", file=sys.stderr)
        return None
    durable = {key: value for key, value in authorization.items() if key != "consumed"}
    return owner_outcome.answer(
        state,
        {**durable, "consumed": False},
        {"authorization": durable},
        fields=("failure_history", "failure_retry_authorizations"),
    )


def validate_resolver_retry(runner, args, state: RunState, run_dir, workspace, decided, row, observed, *, final):
    authorization = decided.failure_retry_authorization
    proof = owner_outcome.proof(state, observed)
    if (
        not isinstance(authorization, dict)
        or authorization != proof
        or args is not None
        and authorization is not getattr(args, "_failure_retry_authorization", None)
    ):
        return None
    facts = runner.resolver_recovery.retry_authorization_outcome(state, workspace, run_dir, authorization)
    return owner_outcome.answer(
        state,
        proof,
        facts,
        fields=("failure_retry_authorizations", "failure_history"),
        members=(("user_events", facts["event"]),),
    )


OPERATIONS = owned_operations.register(
    owned_operations.Operation("abandon", abandon, validate_abandon, request_bound=False, actions=("--abandon-stage",)),
    owned_operations.Operation("grant", grant, validate_grant, preserve_request=True),
    owned_operations.Operation("report", retry_report, validate_report),
    owned_operations.Operation("failure_retry", retry_failure, validate_failure_retry),
    owned_operations.Operation("resolver_retry", retry_failure, validate_resolver_retry, preserve_request=True),
    owned_operations.Operation("bound", acknowledge_bound, validate_bound, request_bound=False, frontier_bound=False),
    owned_operations.Operation(
        "observe", observe, owned_operations.no_work, request_bound=False, preserve_request=True
    ),
    owned_operations.Operation(
        "planning_reserve", observe, validate_reserve, observes=resolver_runtime.reconsideration_request
    ),
    owned_operations.Operation(
        "archive_completion",
        observe,
        validate_archive,
        observes=lambda state: (
            (state.get("recovery_context") or {}).get("attempt_id") and state.get("next_stage") == "review_completion"
        ),
    ),
    owned_operations.Operation("archive_timeout", observe, validate_timeout_archive),
)
