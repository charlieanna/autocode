"""The authority that releases the pause holding a saved run (steps of autocode_run_actions.handle).

A pause is released only by its own authority (#486): an explicit recovery flag for the bound it
exhausted (explicit_recovery_requested), an acknowledged planning or bound extension, a specific
recovery already recognized, or corrective information AutoResolver admits once at an explicit
resume. settle weighs that authority before queued input is applied, and asks AutoResolver's
operational request again for a pause that has none. reconsider lets AutoResolver reconsider a
published request and records whether this invocation released the held pause. Input queued
under an operational pause is applied under that pause (apply_queued_input, hold_for_input),
never as its authority.

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
    from . import autocode_acceptance_recovery as acceptance_recovery
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_goals as goals
    from . import autocode_interventions as interventions
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_member_stop as member_stop
    from . import autocode_milestones as milestones
    from . import autocode_operational_information as operational_information
    from . import autocode_pause_authority as pause_authority
    from . import autocode_planning as planning
    from . import autocode_planning_review_usage as review_usage
    from . import autocode_progressive_state as progressive
    from . import autocode_recovery_progress as recovery_progress
    from . import autocode_resolver_human as resolver_human
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_result_application as result_application
    from . import autocode_support as support
    from . import autocode_verify as verify
except ImportError:
    import autocode_acceptance_recovery as acceptance_recovery
    import autocode_goal_lifecycle as lifecycle
    import autocode_goals as goals
    import autocode_interventions as interventions
    import autocode_launch_inputs as launch_inputs
    import autocode_member_stop as member_stop
    import autocode_milestones as milestones
    import autocode_operational_information as operational_information
    import autocode_pause_authority as pause_authority
    import autocode_planning as planning
    import autocode_planning_review_usage as review_usage
    import autocode_progressive_state as progressive
    import autocode_recovery_progress as recovery_progress
    import autocode_resolver_human as resolver_human
    import autocode_resolver_runtime as resolver_runtime
    import autocode_result_application as result_application
    import autocode_support as support
    import autocode_verify as verify


def prepare_actions(runner, args, state: RunState, factory):
    """Admit the original native action before any saved-run action step."""
    native = getattr(runner, "native_calls", None)
    if native is not None:
        native.require_supported_action(args, state)
    return factory()


def explicit_recovery_requested(args, state: RunState, *, checked=False):
    """Whether this invocation carries a scoped operator recovery action for the pause holding ``state``.

    An explicit change to the bound that pause exhausted is one (#301): it must not be held behind an
    unchanged operational frontier. A budget flag for any other bound is only a settings write, never
    authority to release the pause (#379, #486). ``state`` is required so that no caller counts a
    budget flag without naming the pause; None counts none. ``checked``: only the actions handle()
    checks before it applies queued input. --retry-builder is checked after that input
    (dispatch.try_request_retry), so until then a milestone it would refuse is no authority.
    """
    budget_flags = getattr(args, "_explicit_budget_flags", None) or set()
    return any(
        (
            getattr(args, "retry_builder", None) and not checked,
            getattr(args, "retry_failed_stage", False),
            getattr(args, "retry_report", None),
            getattr(args, "abandon_stage", None),
            getattr(args, "diagnose_failed_stage", False),
            getattr(args, "grant_recovery", None) is not None,
            getattr(args, "accept_source_edit", False),
            bool(budget_flags)
            and state is not None
            and pause_authority.changes_held_bound(budget_flags, pause_authority.held_origin(state)),
        )
    )


def apply_queued_input(runner, state: RunState, run_dir, workspace):
    """Apply queued interventions under the pause holding the run; 2 when that stopped this invocation, else None."""
    pause = state["status"]
    try:
        if runner.consume_interventions(state, run_dir, workspace):
            print(f"{state['status']}: {state['stop_reason']}")
            return 2
    except interventions.InterventionError as error:
        print(f"{pause}: queued input could not be applied ({error}); the pause stays in force.")
        return 2
    return None


def hold_for_input(runner, state: RunState, state_path, run_dir, workspace):
    """Apply input accepted while an operational pause held the run, under that pause; return 2.

    A request asked while input is pending could never be published (its binding names that input),
    and continuing past it would release the pause without its own authority (#486 review). So the
    input is applied here, at the saved boundary, and no provider starts: a queued pause or feedback
    keeps the held pause (autocode_stop), a stop ends the run, and queued milestone checkpoints are
    enabled. The request is then asked again. An operator's own pause-requested file keeps the run
    at its pause, unasked, until it is removed.
    """
    pause = state["status"]
    if apply_queued_input(runner, state, run_dir, workspace) is not None:
        return 2
    try:
        if milestones.apply_queued_activation(state, run_dir):
            print("Milestone checkpoints enabled at this boundary; no provider launched.", flush=True)
    except ValueError as error:
        print(f"Milestone checkpoints stay queued: {error}", file=sys.stderr)
    cause = pause_authority.held_cause(state, pause) or "Operational recovery stopped"
    if resolver_runtime.record_operational_exhaustion(runner, state, run_dir, support.Paused(pause, cause)):
        runner.write_json(state_path, state)
        if resolver_human.current(state):
            print(lifecycle.render(state))
            return 2
    runner.write_json(state_path, state)
    print(f"{state['status']}: {state.get('stop_reason', 'Operational recovery stopped')}")
    if (run_dir / "pause-requested").exists():
        print(
            f"A requested pause ({run_dir / 'pause-requested'}) keeps the run at {pause}; remove that file "
            "and AutoResolver asks its operational request again."
        )
    return 2


def settle(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Weigh the authority this invocation gives the pause holding the run, before queued input applies."""
    explicit_run_seconds = getattr(args, "max_seconds", None)
    explicit_slice_seconds = getattr(args, "max_milestone_seconds", None)
    if explicit_run_seconds is not None or explicit_slice_seconds is not None:
        progressive.set_explicit_limits(state, run_seconds=explicit_run_seconds, slice_seconds=explicit_slice_seconds)
    decision_action = any(
        (
            args.answer,
            args.delegate,
            args.approve_goal,
            args.edit_goal,
            args.goal_file,
            args.approve_review,
            args.reject_review,
            args.reconcile_review,
            args.feedback is not None,
            args.follow_up is not None,
            args.show_goal,
            args.accept_completion,
            args.resolver_response,
            args.planning_review_call_limit is not None,
            bool(args.close_finding),
        )
    )
    if (
        args.feedback is None
        and not args.edit_goal
        and not args.goal_file
        and args.follow_up is None
        and (hold := resolver_human.permission_hold(state))
    ):
        print(f"{state['status']}: {hold['reason']}")
        return 2
    if (
        args.resume_paused
        and not decision_action
        and not explicit_recovery_requested(args, state)
        and recovery_progress.reconcile(
            state,
            issued=resolver_human.current(state),
            approved=goals.approved(state),
            supersede=resolver_human.supersede_operational,
            now=runner.now,
        )
    ):
        runner.write_json(state_path, state)
    active = state.get("active_stage") or {}
    if not decision_action and active and support.failure_status(active.get("events", "")) == "PAUSED_RATE_LIMIT":
        prior = resolver_human.current(state)
        if runner.reconcile_rate_limited_stage(state, run_dir, workspace):
            if prior:
                old = cast(dict, state.get("resolver", {})).get("human_escalations", {}).get(prior["request_id"])
                if old and old.get("status") == "pending":
                    old.update(
                        status="superseded",
                        superseded_at=runner.now(),
                        superseded_reason="AutoResolver reconciled the retained rate-limit attempt",
                    )
                state.pop(resolver_human.PUBLIC, None)
                state.pop("user_request", None)
                state["pending_questions"] = []
            rate_error = support.Paused("PAUSED_RATE_LIMIT", state["stop_reason"])
            resolver_runtime.record_operational_exhaustion(runner, state, run_dir, rate_error)
            runner.write_json(state_path, state)
            print(lifecycle.render(state))
            return 2
    specific_recovery = False
    if (
        args.resume_paused
        and not decision_action
        and not explicit_recovery_requested(args, state)
        and not any(resolver_human.pending_interruptions(run_dir).values())
        and acceptance_recovery.can_resume(state)
    ):

        def restore_acceptance(candidate: RunState):
            clean_run, _ = launch_inputs.runners(candidate, workspace, run_dir, verify.scratch_run)

            def check_tests(tests):
                acceptance_recovery.require_boundary(candidate, getattr(args, "expected_goal_token", None))
                return clean_run(
                    workspace,
                    run_dir / "check-replay" / "acceptance-recovery",
                    tests=tests,
                    timeout=verify.DEFAULT_TIMEOUT,
                )

            if not acceptance_recovery.reconcile(
                candidate,
                workspace,
                at=runner.now(),
                run_tests=check_tests,
            ):
                raise ValueError("The inherited-test correction no longer has exact recovery provenance")

        try:
            candidate = result_application.prepare(
                state, run_dir, acceptance_recovery.STAGE, restore_acceptance, persist=runner.write_json
            )
        except ValueError as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        cast(dict, state).clear()
        state.update(candidate)
        runner.write_json(state_path, state)
        # Continue through ordinary settings, queued-input and Build admission.
        # Reconciliation grants no Resolver or report-repair allowance.
        decided.evidence_reconciled = True
        specific_recovery = True
    if (
        args.resume_paused
        and not decision_action
        and review_usage.can_resume(state)
        and review_usage.reconcile(state, at=runner.now())
    ):
        resolver_human.supersede_operational(state, "Inherited review accounting was corrected under the saved limit")
        state.update({"status": "RUNNING", "phase": "PLANNING"})
        state.pop("stop_reason", None)
        runner.write_json(state_path, state)
        specific_recovery = True
    issued = resolver_human.current(state)
    if args.resume_paused and issued and issued["scope"] == "operational_exhaustion":
        cause = state["resolver"]["human_escalations"][issued["request_id"]]["identity"]["proposal"]["origin"].get(
            "pause_status"
        )
        if cause == "PAUSED_REPEATED_FAILURE":
            published_status = state["status"]
            state["status"] = cause
            specific_recovery = runner.prepare_abandoned_completion_revalidation(state, run_dir, workspace)
            if not specific_recovery:
                state["status"] = published_status
    # A source-only stale repair is already a recognized recovery. Do not let
    # an answered operational request hide it or publish the same request again.
    if (
        args.resume_paused
        and not decision_action
        and not explicit_recovery_requested(args, state)
        and runner.stale_report_repair(state, workspace)
    ):
        specific_recovery = True
    acknowledged_planning_extension = (
        args.resume_paused
        and state.get("status") == "PAUSED_PLANNING_BUDGET"
        and bool(state.get("user_events"))
        and state["user_events"][-1].get("kind") == "planning_budget_change"
        and state["user_events"][-1].get("limit") == planning.review_call_limit(state)
        and state["user_events"][-1].get("calls_used") == cast(dict, state.get("planning", {})).get("astra_calls")
    )
    marker: dict = state.get("_authorized_bound_change", {})
    current_request = resolver_human.current(state)
    current_pause = (
        cast(dict, state.get("resolver", {}))
        .get("human_escalations", {})
        .get(current_request["request_id"], {})
        .get("identity", {})
        .get("proposal", {})
        .get("origin", {})
        .get("pause_status")
        if current_request
        else state.get("status")
    )
    acknowledged_bound_change = args.resume_paused and bool(marker) and marker.get("pause_status") == current_pause
    if acknowledged_bound_change:
        resolver_human.supersede_operational(state, "Delegated finite bound is ready for bounded AutoResolver recovery")
        state["status"] = marker["pause_status"]
        state.pop("_authorized_bound_change", None)
        runner.write_json(state_path, state)
    # Corrective information is re-evaluated once by AutoResolver at an explicit resume (#486).
    review = None
    if (
        not decision_action
        and not specific_recovery
        and not acknowledged_bound_change
        and not explicit_recovery_requested(args, state)
    ):
        # Queued interventions are bound into the frontier the information was sent for: apply them under
        # the pause first (they are held, autocode_stop), never retire the information for them (#486 review).
        if (
            args.resume_paused
            and (operational_information.projection(state) or {}).get("status") == "pending"
            and resolver_human.pending_interruptions(run_dir)["pending"]
            and apply_queued_input(runner, state, run_dir, workspace) is not None
        ):
            return 2
        already_held = (operational_information.projection(state) or {}).get("status") == "held"
        review = operational_information.reevaluate(runner, state, run_dir, workspace, resume=args.resume_paused)
        if review is not None:
            print(review.message, flush=True)
            if review.action in ("hold", "pending"):
                # The held view offers plain resume while a pending repair blocks scoped retry.
                # Reconcile only a spent terminal planning report on that second explicit action;
                # retain the pause and counts and return before any provider admission (#2191).
                pending = state.get("pending_report_repair") or {}
                original = pending.get("original") or {}
                if (
                    args.resume_paused
                    and already_held
                    and review.action == "hold"
                    and state.get("status") == "PAUSED_REPEATED_FAILURE"
                    and pending.get("attempts", 0) >= runner.repair_limit(state)
                    and original.get("rejected")
                    and original.get("exit_code") == 0
                    and not original.get("timed_out")
                    and not original.get("interrupted")
                    and not any(
                        state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts")
                    )
                    and runner.prepare_planning_retry(state, run_dir)
                ):
                    print("Spent planning repair reconciled; inspect the fresh retry action. No provider launched.")
                return 2
    information_admitted = review is not None and review.action == "continue"
    if (
        not decision_action
        and not specific_recovery
        and not acknowledged_bound_change
        and not information_admitted
        and not any(
            (
                args.retry_builder,
                args.retry_failed_stage,
                args.retry_report,
                args.abandon_stage,
                args.grant_recovery is not None,
            )
        )
        and resolver_human.response_holds_current_frontier(state)
    ):
        retired = operational_information.retired(state)
        if retired:
            # Retired with the run still at the answered frontier (only its records or evidence
            # changed): holding on the consumed request is #486 again. Ask again; launch nothing.
            state.pop("user_request", None)
            state["pending_questions"] = []
            if resolver_runtime.record_operational_exhaustion(
                runner, state, run_dir, member_stop.restore(state, support.Paused(state["status"], retired))
            ):
                runner.write_json(state_path, state)
                if resolver_human.current(state):
                    print(lifecycle.render(state))
                    return 2
        step = member_stop.next_step(state)  # a member's model stop names its one control (#541)
        # A blocker's answer is retained, never applied to the contract: say what applies it (#675).
        print(
            "AutoResolver retained the human guidance. No new execution allowance or changed cause was established; "
            "the run remains paused without repeating the same request."
            + (" " + step if step else "")
            + resolver_human.contract_decision_advice(resolver_human.held_response_scope(state))
        )
        return 2
    default_budget_kind = {
        "PAUSED_ITERATION_LIMIT": "iteration_ceiling",
        "PAUSED_TIME_LIMIT": "max_seconds",
        "PAUSED_MILESTONE_TIME_LIMIT": "milestone_max_seconds",
    }.get(cast(str, state.get("status")))
    if (
        not decision_action
        and not resolver_human.current(state)
        and not state.get(resolver_human.PRIVATE)
        and default_budget_kind
        and runner.recover_default_budget(state, run_dir, workspace, default_budget_kind)
    ):
        state.update({"status": "RUNNING", "phase": "EXECUTING"})
        state.pop("stop_reason", None)
        runner.write_json(state_path, state)
    # The pause holding the run's own authority, given by this invocation (reconsideration adds one below):
    # what is checked before queued input is applied, and --retry-builder, which is checked after it.
    checked_authority = (
        specific_recovery
        or information_admitted
        or acknowledged_planning_extension
        or acknowledged_bound_change
        or explicit_recovery_requested(args, state, checked=True)
    )
    acknowledged = checked_authority or explicit_recovery_requested(args, state)
    unacknowledged = not decision_action and not acknowledged
    # Input queued after an operational request was shown leaves that request unanswerable (its
    # binding names the inbox): withdraw it so the input is applied under the pause, then ask again.
    if (
        unacknowledged
        and (state.get(resolver_human.PUBLIC) or {}).get("scope") == "operational_exhaustion"
        and not resolver_human.current(state)
        and any(resolver_human.pending_interruptions(run_dir).values())
    ):
        resolver_human.supersede_operational(state, "Input queued after this request was shown is applied first")
    if (
        unacknowledged
        and state.get("status") != "RUNNING"
        and str(state.get("status", "")).startswith("PAUSED_")
        and not resolver_human.current(state)
        and not state.get(resolver_human.PRIVATE)
    ):
        # Unbound legacy fields are not authority and must not suppress
        # the resolver's current, evidenced escalation for this pause.
        state.pop("user_request", None)
        state["pending_questions"] = []
        # A parallel member's stop is asked as that member's route question again, never a generic one (#541).
        member_error = member_stop.restore(
            state,
            support.Paused(
                state["status"], pause_authority.held_cause(state, state["status"]) or "Operational recovery stopped"
            ),
            detail=False,
        )
        if resolver_runtime.record_operational_exhaustion(runner, state, run_dir, member_error):
            runner.write_json(state_path, state)
            if resolver_human.current(state):
                print(lifecycle.render(state))
                return 2
        elif pause_authority.operational(state["status"]) and any(
            resolver_human.pending_interruptions(run_dir).values()
        ):
            return hold_for_input(runner, state, state_path, run_dir, workspace)
    decided.decision_action = decision_action
    decided.information_admitted = information_admitted
    decided.checked_authority = checked_authority
    decided.issued = issued


def reconsider(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Let AutoResolver reconsider its published operational request, and record whether the pause is released."""
    decision_action, information_admitted = decided.decision_action, decided.information_admitted
    checked_authority = decided.checked_authority
    reconsidered = False
    if (
        not decision_action
        and not explicit_recovery_requested(args, state)
        and not (args.chat and state.get("status") == "WAITING_FOR_USER" and resolver_human.current(state))
        and (state.get(resolver_human.PUBLIC) or {}).get("scope") == "operational_exhaustion"
    ):
        if not resolver_runtime.reconsider_operational_request(runner, state, run_dir, workspace):
            if state.get("stop_reason"):
                print(state["stop_reason"])
            print("AutoResolver retained the operational request; no unchanged, permitted recovery credit was proven.")
            return 2
        reconsidered = True
    # Authority this invocation gave for the pause holding the run: a pause or feedback applied
    # below then pauses a released run and holds nothing (autocode_stop, #486 review). Not an
    # unchecked --retry-builder: input applied below ends this invocation before it is checked.
    decided.released = bool(args.resume_paused and (checked_authority or reconsidered))
    if (
        not decision_action
        and args.grant_recovery is None
        # Abandonment validates the exact stopped attempt in the next step;
        # republishing here would make that named control unreachable.
        and args.abandon_stage is None
        and not information_admitted
        and state.get("status") in ("PAUSED_RESOLVER_OPERATIONAL", "PAUSED_TIMEOUT_RECOVERY")
        and planning.is_planning(state, state.get("next_stage"))
    ):
        resolver_runtime.record_operational_exhaustion(
            runner,
            state,
            run_dir,
            support.Paused(
                state["status"], pause_authority.held_cause(state, state["status"]) or "Operational recovery exhausted"
            ),
        )
        runner.write_json(state_path, state)
        print(lifecycle.render(state))
        return 2
