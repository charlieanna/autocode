"""The user's decisions on a saved run (a step of autocode_run_actions.handle).

Answers (--answer, --delegate, --delegate-all, --reject-assumption), plan approval and edits
(--approve-goal, --edit-goal, --goal-file, --feedback, --follow-up), review decisions (--approve-review,
--reject-review, --reconcile-review), --accept-completion, --close-finding and a planning budget change apply
together to a copy of the run, checked against AutoResolver's current request, and are committed
only when every one applies. The invocation then stops, unless --resume-paused asked it to
continue and the decision cleared the human gate (#509).

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
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_answer_output as answer_output
    from . import autocode_approval_provenance as approval_provenance
    from . import autocode_changeset_barrier as changeset_barrier
    from . import autocode_conversation_policy as conversation_policy
    from . import autocode_finding_close as finding_close
    from . import autocode_follow_up as follow_up
    from . import autocode_goal_file as goal_file
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_goals as goals
    from . import autocode_package_writes as package_writes
    from . import autocode_pause_authority as pause_authority
    from . import autocode_pause_command as pause_command
    from . import autocode_planning as planning
    from . import autocode_planning_artifacts as planning_artifacts
    from . import autocode_requirements_chat as requirements_chat
    from . import autocode_requirements_review as requirements_review
    from . import autocode_resolver_human as resolver_human
    from . import autocode_review_rejection as review_rejection
    from . import autocode_run_finder as run_finder
    from . import autocode_run_records as records
    from . import autocode_source_scope as source_scope
    from . import autocode_support as support
    from . import autopilot
    from .autocode_action_resolver import OPERATIONAL_NOT_ANSWER
except ImportError:
    import autocode_acceptance_tests as acceptance
    import autocode_answer_output as answer_output
    import autocode_approval_provenance as approval_provenance
    import autocode_changeset_barrier as changeset_barrier
    import autocode_conversation_policy as conversation_policy
    import autocode_finding_close as finding_close
    import autocode_follow_up as follow_up
    import autocode_goal_file as goal_file
    import autocode_goal_lifecycle as lifecycle
    import autocode_goals as goals
    import autocode_package_writes as package_writes
    import autocode_pause_authority as pause_authority
    import autocode_pause_command as pause_command
    import autocode_planning as planning
    import autocode_planning_artifacts as planning_artifacts
    import autocode_requirements_chat as requirements_chat
    import autocode_requirements_review as requirements_review
    import autocode_resolver_human as resolver_human
    import autocode_review_rejection as review_rejection
    import autocode_run_finder as run_finder
    import autocode_run_records as records
    import autocode_source_scope as source_scope
    import autocode_support as support
    import autopilot
    from autocode_action_resolver import OPERATIONAL_NOT_ANSWER


def resume_dispatch_requested(args, state: RunState):
    """True when this invocation should dispatch after committing a user action (#509).

    Only an explicit --resume-paused continues in-process, and only when the
    committed answer or approval actually cleared the human gate: the run is
    RUNNING with a concrete next stage (or a ready-to-execute plan) and no
    pending question or active stage. Anything else keeps the saved-hint exit.
    """
    if not args.resume_paused or state.get("status") != "RUNNING":
        return False
    if state.get("active_stage") or state.get("user_request") or state.get("pending_questions"):
        return False
    return bool(state.get("next_stage")) or state.get("phase") == "READY_TO_EXECUTE"


def revalidate_on_resume(state: RunState, workspace, *, review_stage):
    """Retire source-stale evidence at an admitted, reconciled explicit resume (#607).

    validation_archive retains the associated regression_proof for audit, alongside
    the old validation. Proof receipts, regression_proofs and the baseline stay intact.
    """
    if (
        state.get("next_stage") not in ("sol", "astra_checkpoint", "astra_review")
        or state.get("status") != "RUNNING"
        or not goals.approved(state)
        or (state.get(resolver_human.PUBLIC) or {}).get("scope") in ("operational_exhaustion", "blocker")
        or any(
            state.get(key)
            for key in (
                "active_stage",
                "active_runner_check",
                "uncertain_artifacts",
                "pending_report_repair",
                "pending_questions",
                "user_request",
                resolver_human.PRIVATE,
            )
        )
    ):
        return False
    validation = state.get("validation") or {}
    proof = state.get("regression_proof") or {}
    revision = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"]
    if not any(
        record.get("source_revision") and record["source_revision"] != revision for record in (validation, proof)
    ):
        return False
    # Resolve routing before retiring anything: workflow approval remains authoritative.
    stage = review_stage(state)
    reason = "Validation is non-current: the workspace revision changed after it ran"
    archived = {"at": records.now(), "reason": reason, "validation": state.pop("validation", {})}
    if proof.get("source_revision") and proof["source_revision"] != revision:
        archived["regression_proof"] = state.pop("regression_proof")
    state.setdefault("validation_archive", []).append(archived)
    for role in ("sol", "astra"):
        old = state.setdefault("sessions", {}).pop(role, None)
        if old:
            state.setdefault("session_rotations", []).append(
                {"role": role, "old_session": old, "at": records.now(), "reason": reason}
            )
    state.update({"next_stage": stage})
    return True


def preflight(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Reject an ineligible approval before recovery can consume a retained report."""
    if (
        getattr(args, "approve_goal", None)
        and not changeset_barrier.held_approval(state)
        and (reason := lifecycle.approval_boundary_refusal(state))
    ):
        try:
            action = conversation_policy.load_action(state, run_dir, args)
        except (ValueError, OSError) as error:
            return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
        if action and action.get("acknowledged"):
            print("Conversation request was already saved; nothing changed and no provider launched.")
            return 0
        return runner.status_command.reject_input(
            runner, state, workspace, run_dir, ValueError("Goal approval refused: " + reason)
        )
    return None


def apply(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Apply the user's decisions together and save them; go on to the build only when --resume-paused asked."""
    # Recheck after settings/checkpoint activation and before an old draft can be approved or displayed.
    # Explicit draft corrections retain their existing ingress instead of being pre-empted by recovery.
    if not any(
        (
            args.feedback is not None,
            args.edit_goal,
            args.goal_file,
            args.answer,
            args.delegate,
            args.delegate_all,
            args.reject_assumption,
        )
    ):
        try:
            from . import autocode_draft_assignment as draft_assignment
        except ImportError:
            import autocode_draft_assignment as draft_assignment
        if draft_assignment.reconcile(runner, state, run_dir, lifecycle.validate_initial_task):
            print("Saved plan's first task needs correction; queued report repair before approval.")
            if args.show_goal or args.approve_goal:
                return 2 if args.approve_goal else 0
    runner.normalize_human_boundary(state, run_dir)
    user_action = any(
        (
            args.show_goal,
            args.answer,
            args.delegate,
            args.delegate_all,
            args.reject_assumption,
            args.approve_goal,
            args.edit_goal,
            args.goal_file,
            args.approve_review,
            args.reject_review,
            args.reconcile_review,
            args.feedback is not None,
            args.follow_up is not None,
            args.accept_completion,
            args.planning_review_call_limit is not None,
            bool(args.close_finding),
        )
    )
    if user_action:
        try:
            conversation_action = conversation_policy.load_action(state, run_dir, args)
        except (ValueError, OSError) as error:
            return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
        if conversation_action and conversation_action.get("acknowledged"):
            print("Conversation request was already saved; nothing changed and no provider launched.")
            return 0
        metadata = runner.intervention_metadata(workspace, run_dir, state)
        if metadata["pending_count"] or metadata["inbox_error"]:
            raise support.Paused(
                "PAUSED_INTERVENTION_PENDING",
                "Queued intervention must be applied before approval, review, or completion",
            )
        candidate = copy.deepcopy(state)
        require_current_inputs = bool(
            args.approve_review or args.reject_review or args.reconcile_review or args.accept_completion
        )
        try:
            if require_current_inputs:
                runner.launch_inputs.guard(candidate, workspace, run_dir)
            if (
                args.approve_goal
                and changeset_barrier.held_approval(candidate)
                and args.approve_goal == goals.token(candidate["goal_contract"])
                and not resolver_human.current(candidate)
            ):
                # The earlier per-run approval consumed its request, but the set
                # never released (or was invalidated). Issue the ordinary exact-plan
                # boundary again; do not bypass AutoResolver authority for retries.
                resolver_human.queue(
                    candidate,
                    "goal_approval",
                    changeset_barrier.reapproval_origin(candidate),
                    status="AWAITING_GOAL_APPROVAL",
                    next_stage=candidate.get("next_stage"),
                )
                runner.normalize_human_boundary(candidate, run_dir)
            published = resolver_human.current(candidate)
            if not published and candidate.get("status") == "TASK_COMPLETE":
                follow_up.refuse_on_finished(args)  # only --follow-up reopens a finished run
            if args.answer or args.delegate:
                if not published or not args.resolver_token:
                    # #909: name the exact command with the live token, refreshed
                    # from the current need — the shown token supersedes stale ones.
                    raise ValueError(
                        "Answers require the current --resolver-token shown by AutoResolver"
                        + pause_command.recovery(candidate.get("needs"), run_dir=run_dir, workspace=workspace)
                    )
                try:
                    resolver_human.require_response(candidate, published["request_id"], args.resolver_token)
                except ValueError:
                    # A saved-state change after display strands the shown token;
                    # the operator still answered this exact request content.
                    fresh = resolver_human.rebind_stale(candidate, None, args.resolver_token)
                    if fresh is None:
                        raise
                    published = fresh
                if published["scope"] in ("blocker", "operational_exhaustion"):
                    raise ValueError(OPERATIONAL_NOT_ANSWER)
            if args.approve_goal and (not published or published["scope"] != "goal_approval"):
                raise ValueError("Goal approval requires a current AutoResolver-issued plan request")
            if args.approve_review and (not published or published["scope"] != "human_review"):
                replay = all(
                    isinstance(candidate.get("human_reviews", {}).get(cid), dict)
                    and candidate["human_reviews"][cid].get("token") == args.review_token
                    and candidate["human_reviews"][cid] in candidate.get("user_events", [])
                    for cid in args.approve_review
                )
                issued = any(
                    entry.get("status") == "consumed"
                    and entry.get("identity", {}).get("proposal", {}).get("scope") == "human_review"
                    and entry["identity"]["proposal"].get("evidence", {}).get("review_token") == args.review_token
                    and set(args.approve_review)
                    <= set(goals.requested_review_criteria(candidate, entry["identity"]["proposal"]["request"]))
                    for entry in cast(dict, candidate.get("resolver", {})).get("human_escalations", {}).values()
                )
                if not (replay and issued):
                    raise ValueError("Artifact acceptance requires a current AutoResolver-issued review request")
            if args.reject_review and (not published or published["scope"] != "human_review"):
                raise ValueError("Artifact rejection requires a current AutoResolver-issued review request")
            if args.planning_review_call_limit is not None:
                planning.set_review_call_limit(candidate, args.planning_review_call_limit)
            for item in args.answer:
                question, sep, response = item.partition("=")
                if not sep:
                    raise ValueError("--answer uses QUESTION_ID=TEXT")
                request = cast(dict, candidate.get("user_request", {}))
                if goals.is_operational_response(request):
                    goals.resolve_permission(candidate, question, response)
                elif (
                    request.get("kind") == "blocker"
                    and response == (request.get("options") or [None])[0]
                    and response.startswith("Reconcile ")
                ):
                    runner.launch_inputs.guard(candidate, workspace, run_dir)
                    require_current_inputs = True
                    lifecycle.resolve_passing_checkpoint(candidate, question, response)
                else:
                    goals.answer(candidate, question, response)
            for question in args.delegate:
                goals.answer(candidate, question, "accept default", delegated=True)
            if args.delegate_all:
                goals.delegate_all(candidate, args.review_token)
            requirements_review.after_answers(candidate)
            requirements_chat.recognize_after_approval(candidate)
            for assumption_id in args.reject_assumption:
                goals.reject_assumption(candidate, assumption_id, args.review_token)
            if args.feedback is not None:
                refusal = pause_authority.feedback_refusal(candidate)
                if refusal:
                    raise ValueError(refusal)
                goals.feedback(candidate, args.feedback)
            requirements_chat.adopt_reply(candidate)
            if args.follow_up is not None:
                follow_up.accept(candidate, args.follow_up, workspace, runner.now())
            if args.edit_goal:
                refusal = pause_authority.correction_refusal(candidate, "An edited goal")
                if refusal:
                    raise ValueError(refusal)
                lifecycle.install_draft(candidate, runner.read_json(args.edit_goal), origin="user_cli_edit")
                planning_artifacts.prepare_user_cli_edit(candidate, run_dir=run_dir)
            if args.goal_file:
                # The person's own edit, as --edit-goal, held to a planner draft's checks too (#720).
                refusal = pause_authority.correction_refusal(candidate, "A plan file")
                if refusal:
                    raise ValueError(refusal)
                plan = goal_file.read(args.goal_file)
                goal_file.check(candidate, plan.body, coverage=planning.traces_coverage(plan.body))
                lifecycle.install_draft(candidate, plan.body, origin="user_cli_edit")
                goal_file.record(candidate, args.goal_file, plan)
                planning_artifacts.prepare_user_cli_edit(candidate, run_dir=run_dir, label="--goal-file")
            if args.approve_goal:
                if not changeset_barrier.held_approval(candidate):
                    getattr(runner, "approve_goal", lifecycle.approve)(candidate, args.approve_goal)
                changeset_barrier.approve(candidate, args.approve_goal, getattr(args, "changeset_token", None))
            for criterion in args.approve_review:
                goals.approve_review(
                    candidate,
                    criterion,
                    args.review_token,
                    source_scope.snapshot(workspace, state, base_snapshot=support.snapshot),
                )
            if args.reject_review:
                refusal = pause_authority.feedback_refusal(candidate)
                if refusal:
                    raise ValueError(refusal)
                review_rejection.reject(
                    candidate,
                    args.reject_review,
                    args.review_token,
                    source_scope.snapshot(workspace, state, base_snapshot=support.snapshot),
                    args.review_note,
                )
            if args.reconcile_review:
                criterion, separator, answer_id = args.reconcile_review.partition("=")
                if not separator or not criterion or not answer_id:
                    raise ValueError("--reconcile-review uses CRITERION_ID=ANSWER_ID")
                goals.reconcile_legacy_review(
                    candidate,
                    criterion,
                    answer_id,
                    args.review_token,
                    source_scope.snapshot(workspace, state, base_snapshot=support.snapshot),
                )
            if args.accept_completion:
                runner.accept_completion(candidate, workspace, run_dir=run_dir)
                # No judging dispatch precedes it, so a package run's check runs here, once its own guards
                # (a finished run, recorded checks, an unapproved goal, missing evidence) have spoken:
                # closing the finding with --close-finding accepts nothing (#711). A refusal discards the
                # candidate, completion included.
                package_writes.refuse(candidate, workspace)
                acceptance.refuse(candidate, workspace, run_dir=run_dir)
            if args.close_finding:
                finding_close.close(
                    candidate,
                    args.close_finding,
                    args.close_reason,
                    current=resolver_human.current,
                    supersede=resolver_human.supersede_operational,
                )
            # Who approved, before the request is consumed so its response copies it (#722).
            approval_provenance.stamp(
                state, candidate, approval_provenance.invocation(sys.argv[1:], claimed_as=args.claimed_as)
            )
            if published and any(
                (args.answer, args.delegate, args.approve_goal, args.approve_review, args.reject_review)
            ):
                runner.finish_human_action(candidate, published)
        except (ValueError, KeyError) as error:
            conversation_policy.reject(run_dir, conversation_action, error)
            return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
        try:
            conversation_policy.acknowledge(state, candidate, conversation_action)
            with conversation_policy.saved_action(state, run_dir, args, conversation_action):
                runner.normalize_human_boundary(candidate, run_dir)
                rendered = lifecycle.present(candidate, run_dir)
                autopilot.publish_handoffs(candidate, run_dir)
                runner.commit_user_action(state, candidate, run_dir, require_current_inputs=require_current_inputs)
        except ValueError as error:
            return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
        print(rendered)
        if args.goal_file:
            print(goal_file.summary(state, args.goal_file))
            # Chat everywhere (#1731): the loaded plan is reviewed, discussed and approved in the run's conversation.
            print(f"Review it and approve it in chat: autocode chat --workspace {workspace} --run-dir {run_dir}")
        if args.answer:
            view = runner.run_view.view(state, run_dir=run_dir, workspace=workspace)
            print(answer_output.summary(view, after_answer=True), file=sys.stderr)
        if resume_dispatch_requested(args, state):
            # --resume-paused asked this invocation to continue: the answer or
            # approval cleared the human gate and the frontier is dispatchable,
            # so fall through to the build loop instead of exiting (#509).
            if revalidate_on_resume(state, workspace, review_stage=runner.workflow.review_stage):
                runner.write_json(state_path, state)
            print("Resumed: dispatching the next stage.", flush=True)
            decided.dispatch = True
            return None
        print(f"Saved; no agent launched by this action. {run_finder.continue_hint(run_dir, state, args.unit)}.")
        return 0
