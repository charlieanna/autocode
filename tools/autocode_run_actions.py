"""What one autocode invocation does to a saved run before it builds.

It applies dependency deliveries, operational-recovery decisions, AutoResolver responses, an
abandoned stage, a resumed pause, state migrations, the user's answers, approvals and feedback, and
stops for runs waiting on the user. handle() returns an exit code when the invocation ends here, or
None to go on to the build loop.

It is given the runner module (autocode) and calls the runner's own functions as runner.X, the way
autopilot.apply_result does, so tests that patch the runner still reach this code and the provider
selected for this invocation (runner.opencode) is the one used. It never imports autocode.
"""
from __future__ import annotations

import copy
import sys

try:
    from . import autocode_job_failure as job_failure, autocode_design_revision as design_revision, autocode_design_intake as design_intake
    from . import autopilot
    from . import autocode_dependency as dependency
    from . import autocode_conversation_ingress as conversation_ingress
    from . import autocode_dispatch as dispatch
    from . import autocode_finding_close as finding_close
    from . import autocode_follow_up as follow_up
    from . import autocode_goals as goals
    from . import autocode_interventions as interventions
    from . import autocode_jobs as jobs
    from . import autocode_goal_lifecycle as lifecycle
    from . import autocode_milestones as milestones
    from . import autocode_planning as planning
    from . import autocode_planning_artifacts as planning_artifacts
    from . import autocode_progressive_state as progressive
    from . import autocode_quota_route as quota_route
    from . import autocode_resolver_human as resolver_human
    from . import autocode_recovery_progress as recovery_progress
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_run_finder as run_finder
    from . import autocode_stop as stop
    from . import autocode_support as support
    from . import autocode_workflows as workflows
    from . import autocode_worktrees as worktrees
except ImportError:
    import autocode_job_failure as job_failure, autocode_design_revision as design_revision, autocode_design_intake as design_intake
    import autopilot
    import autocode_dependency as dependency
    import autocode_conversation_ingress as conversation_ingress
    import autocode_dispatch as dispatch
    import autocode_finding_close as finding_close
    import autocode_follow_up as follow_up
    import autocode_goals as goals
    import autocode_interventions as interventions
    import autocode_jobs as jobs
    import autocode_goal_lifecycle as lifecycle
    import autocode_milestones as milestones
    import autocode_planning as planning
    import autocode_planning_artifacts as planning_artifacts
    import autocode_progressive_state as progressive
    import autocode_quota_route as quota_route
    import autocode_resolver_human as resolver_human
    import autocode_recovery_progress as recovery_progress
    import autocode_resolver_runtime as resolver_runtime
    import autocode_run_finder as run_finder
    import autocode_stop as stop
    import autocode_support as support
    import autocode_workflows as workflows
    import autocode_worktrees as worktrees


def explicit_recovery_requested(args):
    """Whether this invocation carries a scoped operator recovery action."""
    # An explicit bound change is a recovery action (#301): it must not be held
    # behind an unchanged operational frontier.
    budget_flags = getattr(args, '_explicit_budget_flags', None) or set()
    return any((getattr(args, 'retry_builder', None),
                getattr(args, 'retry_failed_stage', False),
                getattr(args, 'retry_report', None),
                getattr(args, 'abandon_stage', None),
                getattr(args, 'diagnose_failed_stage', False),
                getattr(args, 'grant_recovery', None) is not None,
                bool(budget_flags)))


def handle(runner, args, parser, state, state_path, run_dir, workspace):
    """Apply this invocation's action to the saved run; return an exit code to stop, or None to build."""
    args._failure_retry_authorization = None  # Invocation-local; saved history is audit data, not credit.
    # An applied durable stop is terminal: no recovery, user action, answer,
    # approval, feedback or resume may relaunch a stopped run or complete it.
    if stop.applied_stop(state) is not None:
        if stop.assert_stopped(state):
            runner.write_json(state_path, state)
        print(f"{state['status']}: {state['stop_reason']}")
        return 2
    if args.revise_figma_manifest:
        try:
            metadata = runner.intervention_metadata(workspace, run_dir, state)
            if metadata["pending_count"] or metadata["inbox_error"]:
                raise ValueError("Apply queued interventions before revising design references")
            design_revision.apply(state, design_revision.manifest.load(args.revise_figma_manifest),
                                  args.expected_design_hash, args.design_change_reason, workspace)
        except (ValueError, OSError) as error:
            print(f'Input rejected: {error}', file=sys.stderr)
            return 2
        runner.write_json(state_path, state)
        print(state['stop_reason'])
        return 0
    try:
        conversation_ingress.require_expected_goal(state, getattr(args, 'expected_goal_token', None),
                                                   token_for=goals.token, is_approved=goals.approved)
        if conversation_ingress.ingest(state, run_dir, workspace, getattr(args, 'conversation_handoff', None),
                                       existing_run=bool(args.run_dir)):
            runner.write_json(state_path, state)
    except ValueError as error:
        print(f'Input rejected: {error}', file=sys.stderr)
        return 2
    dependency_result = dependency.apply(args, state, run_dir, resolver_human.current(state), runner.write_json,
                                         lambda: support.snapshot(workspace)["revision"])
    if dependency_result is not None:
        return dependency_result
    if not args.abandon_stage and job_failure.recover(runner, state, run_dir, workspace):
        if not args.retry_failed_stage:
            print(state['stop_reason'])
            return 2
    if state.get('job_failure') and (state.get('status') in ('PAUSED_JOB_FAILURE', 'PAUSED_STAGE_ABANDONED') or args.job_retry_token):
        if not (args.resume_paused and args.retry_failed_stage):
            print(state.get('stop_reason', 'Inspect the retained workflow-job attempt before retrying.'))
            return 2
        try:
            job_failure.authorize(runner, state, run_dir, workspace, args.job_retry_token)
        except ValueError as error:
            print(f'Input rejected: {error}', file=sys.stderr)
            return 2
    explicit_run_seconds = getattr(args, "max_seconds", None)
    explicit_slice_seconds = getattr(args, "max_milestone_seconds", None)
    if explicit_run_seconds is not None or explicit_slice_seconds is not None:
        progressive.set_explicit_limits(state, run_seconds=explicit_run_seconds,
                                        slice_seconds=explicit_slice_seconds)
    decision_action = any((args.answer, args.delegate, args.approve_goal, args.edit_goal,
                           args.approve_review, args.reconcile_review, args.feedback is not None, args.follow_up is not None,
                           args.show_goal, args.accept_completion, args.resolver_response,
                           args.planning_review_call_limit is not None, bool(args.close_finding)))
    if (args.resume_paused and not decision_action and not explicit_recovery_requested(args)
            and recovery_progress.reconcile(state, issued=resolver_human.current(state),
                approved=goals.approved(state), supersede=resolver_human.supersede_operational,
                now=runner.now)):
        runner.write_json(state_path, state)
    active = state.get('active_stage') or {}
    if (not decision_action and active and support.failure_status(active.get('events', '')) == 'PAUSED_RATE_LIMIT'):
        prior = resolver_human.current(state)
        if runner.reconcile_rate_limited_stage(state, run_dir, workspace):
            if prior:
                old = state.get('resolver', {}).get('human_escalations', {}).get(prior['request_id'])
                if old and old.get('status') == 'pending':
                    old.update(status='superseded', superseded_at=runner.now(),
                               superseded_reason='AutoResolver reconciled the retained rate-limit attempt')
                state.pop(resolver_human.PUBLIC, None)
                state.pop('user_request', None)
                state['pending_questions'] = []
            error = support.Paused('PAUSED_RATE_LIMIT', state['stop_reason'])
            resolver_runtime.record_operational_exhaustion(runner, state, run_dir, error)
            runner.write_json(state_path, state)
            print(lifecycle.render(state))
            return 2
    specific_recovery = False
    issued = resolver_human.current(state)
    if args.resume_paused and issued and issued['scope'] == 'operational_exhaustion':
        cause = state['resolver']['human_escalations'][issued['request_id']]['identity']['proposal']['origin'].get('pause_status')
        if cause == 'PAUSED_REPEATED_FAILURE':
            published_status = state['status']
            state['status'] = cause
            specific_recovery = runner.prepare_abandoned_completion_revalidation(state, run_dir, workspace)
            if not specific_recovery:
                state['status'] = published_status
    acknowledged_planning_extension = (args.resume_paused and state.get('status') == 'PAUSED_PLANNING_BUDGET'
        and bool(state.get('user_events')) and state['user_events'][-1].get('kind') == 'planning_budget_change'
        and state['user_events'][-1].get('limit') == planning.review_call_limit(state)
        and state['user_events'][-1].get('calls_used') == state.get('planning', {}).get('astra_calls'))
    marker = state.get('_authorized_bound_change', {})
    current_request = resolver_human.current(state)
    current_pause = (state.get('resolver', {}).get('human_escalations', {}).get(
        current_request['request_id'], {}).get('identity', {}).get('proposal', {}).get('origin', {}).get('pause_status')
        if current_request else state.get('status'))
    acknowledged_bound_change = (args.resume_paused and bool(marker)
                                 and marker.get('pause_status') == current_pause)
    if acknowledged_bound_change:
        resolver_human.supersede_operational(state, 'Delegated finite bound is ready for bounded AutoResolver recovery')
        state['status'] = marker['pause_status']
        state.pop('_authorized_bound_change', None)
        runner.write_json(state_path, state)
    if (not decision_action and not specific_recovery and not acknowledged_bound_change
            and not any((args.retry_builder, args.retry_failed_stage, args.retry_report,
                         args.abandon_stage, args.grant_recovery is not None))
            and resolver_human.response_holds_current_frontier(state)):
        print('AutoResolver retained the human guidance. No new execution allowance or changed cause was established; '
              'the run remains paused without repeating the same request.')
        return 2
    default_budget_kind = {'PAUSED_ITERATION_LIMIT': 'iteration_ceiling',
                           'PAUSED_TIME_LIMIT': 'max_seconds',
                           'PAUSED_MILESTONE_TIME_LIMIT': 'milestone_max_seconds'}.get(state.get('status'))
    if (not decision_action and not resolver_human.current(state) and not state.get(resolver_human.PRIVATE)
            and default_budget_kind and runner.recover_default_budget(state, run_dir, workspace, default_budget_kind)):
        state.update(status='RUNNING', phase='EXECUTING')
        state.pop('stop_reason', None)
        runner.write_json(state_path, state)
    if (not decision_action and not explicit_recovery_requested(args)
            and state.get('status') != 'RUNNING'
            and not acknowledged_planning_extension and not acknowledged_bound_change
            and str(state.get('status', '')).startswith('PAUSED_')
            and not resolver_human.current(state) and not state.get(resolver_human.PRIVATE)):
        # Unbound legacy fields are not authority and must not suppress
        # the resolver's current, evidenced escalation for this pause.
        state.pop('user_request', None)
        state['pending_questions'] = []
        error = support.Paused(state['status'], state.get('stop_reason', 'Operational recovery stopped'))
        if resolver_runtime.record_operational_exhaustion(runner, state, run_dir, error):
            runner.write_json(state_path, state)
            if resolver_human.current(state):
                print(lifecycle.render(state))
                return 2
    routed = answer_quota_question(runner, args, state, run_dir, workspace)
    if routed is not None:
        return routed
    if args.resolver_response:
        candidate = copy.deepcopy(state)
        try:
            resolver_human.respond_operational(candidate, args.resolver_request, args.resolver_token,
                                               args.resolver_response, args.resolver_message)
        except ValueError as error:
            # A saved-state change after display strands the shown token; the
            # operator's decision still applies to the identical pending
            # request, re-bound to the current state in this same invocation.
            fresh = resolver_human.rebind_stale(candidate, args.resolver_request, args.resolver_token)
            if fresh is None:
                print(f'Input rejected: {error}', file=sys.stderr)
                return 2
            resolver_human.respond_operational(candidate, fresh['request_id'], fresh['request_token'],
                                               args.resolver_response, args.resolver_message)
        resolver_human.review_operational_response(candidate)
        runner.commit_user_action(state, candidate, run_dir)
        print('AutoResolver received the response. Work, approvals and budgets remain unchanged; no provider launched.')
        return 0
    if (not decision_action and not explicit_recovery_requested(args)
            and not (args.chat and state.get('status') == 'WAITING_FOR_USER'
                     and resolver_human.current(state))
            and (state.get(resolver_human.PUBLIC) or {}).get('scope') == 'operational_exhaustion'):
        if not resolver_runtime.reconsider_operational_request(
                runner, state, run_dir, workspace):
            if state.get('stop_reason'):
                print(state['stop_reason'])
            print('AutoResolver retained the operational request; no unchanged, permitted recovery credit was proven.')
            return 2
    if (not decision_action and args.grant_recovery is None
            and state.get('status') in ('PAUSED_RESOLVER_OPERATIONAL', 'PAUSED_TIMEOUT_RECOVERY')
            and planning.is_planning(state, state.get('next_stage'))):
        resolver_runtime.record_operational_exhaustion(runner, state, run_dir,
            support.Paused(state['status'], state.get('stop_reason', 'Operational recovery exhausted')))
        runner.write_json(state_path, state)
        print(lifecycle.render(state))
        return 2
    if args.abandon_stage is not None:
        try:
            runner.abandon_stage(state, run_dir, workspace, args.abandon_stage)
        except ValueError as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        print(f"{state['status']}: {state['stop_reason']} No agent launched.")
        return 0
    # Recovery interprets terminal artifacts only. It never replays a model call.
    try:
        if args.resume_paused:
            # Acknowledgement is not a new spending/recovery allowance.
            # Counts, elapsed time, repair attempts and receipts persist;
            # an operator who fixed the cause grants more explicitly
            # with --grant-recovery N.
            if state.get("recovery_context") is None:
                state["recovery_context"] = {}
            # Validate the displayed grant before resume bookkeeping changes
            # the user events bound into the resolver request's identity.
            if args.grant_recovery is not None:
                try:
                    runner.grant_recovery_allowance(state, run_dir, args.grant_recovery,
                        previous_settings=getattr(args, "_recovery_grant_settings", None))
                except ValueError as error:
                    print(f"Input rejected: {error}", file=sys.stderr)
                    return 2
            # Reset milestone budget counters when raising the limit
            if args.max_milestone_seconds is not None and not (state.get("progressive") or {}).get("budget"):
                for row in state.get("milestone_progress", {}).values():
                    if isinstance(row, dict):
                        row["seconds"] = 0
                        row["seconds_by_role"] = {}
            # Renew the resolver evaluation epoch without resetting the
            # lifetime diagnostic allowance. Report repair is renewed
            # only after exhaustion-gated retry decisions below.
            resolver_runtime.reset_for_resume(state)
            if args.retry_report:
                try:
                    runner.retry_format_failed_report(state, run_dir, workspace, args.retry_report)
                except ValueError as error:
                    print(f"Input rejected: {error}", file=sys.stderr)
                    return 2
            else:
                authorization = None
                if args.retry_failed_stage and state.get("job_failure"):
                    pass  # exact job authorization was validated before generic recovery
                elif args.retry_failed_stage:
                    try:
                        authorization = (runner.resolver_recovery.authorize_retry(runner, state, run_dir, workspace)
                                         or runner.authorize_failure_retry(state, run_dir, workspace))
                        args._failure_retry_authorization = authorization
                        print("Failure retry authorized for the recorded repeated failure; "
                              "one fresh attempt proceeds under existing limits.", flush=True)
                    except ValueError as error:
                        print(f"Input rejected: {error}", file=sys.stderr)
                        return 2
                elif args.diagnose_failed_stage:
                    try:
                        resolver_runtime.admit_operational_diagnosis(runner, state, run_dir, workspace)
                        print("Diagnosis admitted for the recorded repeated Builder failure; "
                              "a bounded read-only model diagnosis runs before any retry.", flush=True)
                    except ValueError as error:
                        print(f"Input rejected: {error}", file=sys.stderr)
                        return 2
                runner.prepare_abandoned_completion_revalidation(state, run_dir, workspace)
                runner.repeated_failure_resume_guard(state, workspace, authorization=authorization)
                runner.prepare_planning_retry(state, run_dir)
                runner.prepare_exhausted_execution_report_retry(state, run_dir, workspace)
                discarded = runner.archive_stale_report_repair(state, run_dir, workspace)
                if discarded:
                    print(discarded, flush=True)
            # Reset report repair attempts on explicit resume, for whatever
            # repair record is still pending. An exhaustion-gated retry
            # above (which requires and archives the true attempt count)
            # already consumed it if one applied; resetting first would
            # corrupt that archived count and always fail those guards.
            runner.reset_report_repair_for_resume(state)
        runner.reconcile_active(state, run_dir, workspace)
    except runner.ReportRepairQueued:
        pass  # Durable pending repair is dispatched below, not original work.
    except support.Paused as error:
        if job_failure.recover(runner, state, run_dir, workspace, error):
            print(state["stop_reason"])
            return 2
        capacity_recovered = runner.automatically_recover_capacity_stage(state, run_dir, workspace, error)
        if capacity_recovered:
            recovery = state["recovery_context"]
            print(f"Provider capacity recovery {recovery['retry_number']}/{runner.MAX_AUTOMATIC_CAPACITY_RECOVERIES}: "
                  f"partial work archived; the Plan Reviewer will inspect before the next writer", flush=True)
        elif not (runner.automatically_recover_timed_out_stage(state, run_dir, workspace, error)
                  or runner.automatically_recover_external_directory_denial(state, run_dir, workspace, error)):
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
        raise support.Paused("PAUSED_UNCERTAIN_STAGE", "Legacy partial stage remains unresolved: " + state["uncertain_artifacts"])
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
        runner.write_json(state_path, state)
    if args.milestone_checkpoints:
        milestones.activate(state)
        if args.max_milestone_seconds is not None:
            state['settings']['milestone_checkpoints']['max_seconds'] = args.max_milestone_seconds
        runner.write_json(state_path, state)
    if milestones.apply_queued_activation(state, run_dir):
        print(f"Run: {run_dir}\nMilestone checkpoints enabled at a safe boundary; continuing with independent validation.", flush=True)
    try:
        if runner.consume_interventions(state, run_dir, workspace):
            print(f"{state['status']}: {state['stop_reason']}")
            return 2
    except interventions.InterventionError as error:
        raise support.Paused("PAUSED_INTERVENTION_ACK", str(error)) from error
    print(f"Run: {run_dir}", flush=True)
    if args.migrate_only:
        print("Migrated to an unapproved draft; saved work retained; no agent launched")
        return 0
    if args.retry_builder and not dispatch.try_request_retry(state, run_dir, args.retry_builder, issued=issued):
        return 2
    runner.normalize_human_boundary(state, run_dir)
    user_action = any((args.show_goal, args.answer, args.delegate, args.delegate_all, args.reject_assumption,
                       args.approve_goal, args.edit_goal,
                       args.approve_review, args.reconcile_review,
                       args.feedback is not None, args.follow_up is not None, args.accept_completion,
                       args.planning_review_call_limit is not None, bool(args.close_finding)))
    if user_action:
        metadata = runner.intervention_metadata(workspace, run_dir, state)
        if metadata["pending_count"] or metadata["inbox_error"]:
            raise support.Paused("PAUSED_INTERVENTION_PENDING",
                                 "Queued intervention must be applied before approval, review, or completion")
        candidate = copy.deepcopy(state)
        try:
            published = resolver_human.current(candidate)
            if args.answer or args.delegate:
                if not published or not args.resolver_token:
                    raise ValueError('Answers require the current --resolver-token shown by AutoResolver')
                try:
                    resolver_human.require_response(candidate, published['request_id'], args.resolver_token)
                except ValueError:
                    # A saved-state change after display strands the shown token;
                    # the operator still answered this exact request content.
                    fresh = resolver_human.rebind_stale(candidate, None, args.resolver_token)
                    if fresh is None:
                        raise
                    published = fresh
                if published['scope'] in ('blocker', 'operational_exhaustion'):
                    raise ValueError('Use --resolver-response for this operational request; it is not a requirements answer')
            if args.approve_goal and (not published or published['scope'] != 'goal_approval'):
                raise ValueError('Goal approval requires a current AutoResolver-issued plan request')
            if args.approve_review and (not published or published['scope'] != 'human_review'):
                replay = all(isinstance(candidate.get('human_reviews', {}).get(cid), dict)
                             and candidate['human_reviews'][cid].get('token') == args.review_token
                             and candidate['human_reviews'][cid] in candidate.get('user_events', [])
                             for cid in args.approve_review)
                issued = any(entry.get('status') == 'consumed'
                             and entry.get('identity', {}).get('proposal', {}).get('scope') == 'human_review'
                             and entry['identity']['proposal'].get('evidence', {}).get('review_token') == args.review_token
                             and set(args.approve_review) <= set(goals.requested_review_criteria(
                                 candidate, entry['identity']['proposal']['request']))
                             for entry in candidate.get('resolver', {}).get('human_escalations', {}).values())
                if not (replay and issued):
                    raise ValueError('Artifact acceptance requires a current AutoResolver-issued review request')
            if args.planning_review_call_limit is not None:
                planning.set_review_call_limit(candidate, args.planning_review_call_limit)
            for item in args.answer:
                question, sep, response = item.partition("=")
                if not sep:
                    raise ValueError("--answer uses QUESTION_ID=TEXT")
                request = candidate.get("user_request", {})
                if goals.is_operational_response(request):
                    goals.resolve_permission(candidate, question, response)
                elif (request.get("kind") == "blocker" and response == (request.get("options") or [None])[0]
                      and response.startswith("Reconcile ")):
                    lifecycle.resolve_passing_checkpoint(candidate, question, response)
                else:
                    goals.answer(candidate, question, response)
            for question in args.delegate:
                goals.answer(candidate, question, "accept default", delegated=True)
            if args.delegate_all:
                goals.delegate_all(candidate, args.review_token)
            for assumption_id in args.reject_assumption:
                goals.reject_assumption(candidate, assumption_id, args.review_token)
            if args.feedback is not None:
                goals.feedback(candidate, args.feedback)
            if args.follow_up is not None:
                follow_up.accept(candidate, args.follow_up, workspace, runner.now())
            if args.edit_goal:
                lifecycle.install_draft(candidate, runner.read_json(args.edit_goal), origin="user_cli_edit")
                planning_artifacts.prepare_user_cli_edit(candidate, run_dir=run_dir)
            if args.approve_goal:
                lifecycle.approve(candidate, args.approve_goal)
            for criterion in args.approve_review:
                goals.approve_review(candidate, criterion, args.review_token, support.snapshot(workspace))
            if args.reconcile_review:
                criterion, separator, answer_id = args.reconcile_review.partition("=")
                if not separator or not criterion or not answer_id:
                    raise ValueError("--reconcile-review uses CRITERION_ID=ANSWER_ID")
                goals.reconcile_legacy_review(candidate, criterion, answer_id,
                                              args.review_token, support.snapshot(workspace))
            if args.accept_completion:
                runner.accept_completion(candidate, workspace)
            if args.close_finding:
                finding_close.close(candidate, args.close_finding, args.close_reason,
                                    current=resolver_human.current, supersede=resolver_human.supersede_operational)
            if published and any((args.answer, args.delegate, args.approve_goal, args.approve_review)):
                runner.finish_human_action(candidate, published)
        except (ValueError, KeyError) as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        runner.normalize_human_boundary(candidate, run_dir)
        rendered = lifecycle.present(candidate, run_dir)
        autopilot.publish_handoffs(candidate, run_dir)
        runner.commit_user_action(state, candidate, run_dir)
        print(rendered)
        print(f"Saved; no agent launched by this action. {run_finder.continue_hint(run_dir, state, args.unit)}.")
        return 0
    if state["status"] == "TASK_COMPLETE":
        runner.recheck_completion(state, workspace)
        if state["status"] != "TASK_COMPLETE":
            runner.write_json(state_path, state)
    if state["status"] == "TASK_COMPLETE":
        print(jobs.render(state, goals.render_completion) + worktrees.deliver(state, workspace))
        return 0
    if state["status"] in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"):
        if args.chat:
            if not runner.chat_checkpoint(state, run_dir):
                runner.write_json(state_path, state)
                return 2
            runner.write_json(state_path, state)
        else:
            rendered = lifecycle.present(state, run_dir)
            runner.write_json(state_path, state)
            print(rendered)
            return 2
    if state['status'] == 'PAUSED_PLANNING_BUDGET' and not user_action:
        if (runner.recover_default_budget(state, run_dir, workspace, 'planning_review_call_limit')
                or resolver_runtime.operational_boundary(runner, state, run_dir, workspace)):
            state.update(status='RUNNING', phase='PLANNING')
            state.pop('stop_reason', None)
            runner.write_json(state_path, state)
    if state["status"] != "RUNNING":
        # A pre-v0.5.4 terminal response with one missing event
        # citation can be repaired without implementation replay.  It
        # is deliberately gated on an explicit resume and exact pins.
        if args.resume_paused and runner.recover_legacy_report_repair(state, run_dir, workspace):
            pass
        elif not args.resume_paused:
            print(f"{state['status']}: {state.get('stop_reason','explicit resume required')}")
            return 2
        else:
            resumed_at = runner.now()
            if state.get("pause_intent") and not state["pause_intent"].get("acknowledged_at"):
                state["pause_intent"]["acknowledged_at"] = resumed_at
            for receipt in state.get("applied_interventions", []):
                # Pause receipts acknowledge their resume; a stop receipt is
                # terminal and never receives a resumed_at stamp.
                if (isinstance(receipt, dict) and not receipt.get("resumed_at")
                        and not stop.is_stop_receipt(receipt)):
                    receipt["resumed_at"] = resumed_at
            state.update(status="RUNNING", phase="PLANNING" if planning.is_planning(state, state["next_stage"])
                         else "DISCOVERING" if state["next_stage"] == "astra_discovery" else "READY_TO_EXECUTE")
            state.pop('stop_reason', None)
    if state.get("pending_questions"):
        raise support.Paused("PAUSED_UNANSWERED_QUESTION", "Pending questions cannot be bypassed by resume")
    if runner.migrate_opencode_roles(state, run_dir, workspace):
        print("Saved roles now use OpenCode; previous sessions archived and task progress retained.", flush=True)
    try:
        conversation_ingress.require_expected_goal(state, getattr(args, 'expected_goal_token', None),
                                                   token_for=goals.token, is_approved=goals.approved)
    except ValueError as error:
        print(f'Input rejected: {error}', file=sys.stderr)
        return 2
    if state["settings"].get("engine") == "opencode":
        runner.opencode.check_models({r: config for r, config in state["settings"]["roles"].items()
                               if planning.engine_for(state["settings"], r) == "opencode"}, workspace)
    if conversation_ingress.record_build_start(state, getattr(args, 'expected_goal_token', None),
                                               token_for=goals.token, is_approved=goals.approved):
        runner.write_json(state_path, state)
    return None


def answer_quota_question(runner, args, state, run_dir, workspace):
    """--answer route-ROLE=MODEL at a quota stop (#184); None when this invocation answers something else.

    The one operational question answered with --answer. The model must pass the launch rules
    (engine format, availability, cross-model); a rejection leaves the run paused and the
    request open. An accepted model sets the stopped attempt aside exactly as --abandon-stage
    does, then applies and records the route; --resume-paused continues on a fresh session.
    """
    if not (args.answer or args.delegate):
        return None
    candidate = copy.deepcopy(state)
    published = resolver_human.current(candidate)
    if not any(item.partition('=')[0].startswith(quota_route.PREFIX) for item in [*args.answer, *args.delegate]):
        # Refuse every other answer to an operational request here, before an uncertain attempt is
        # reconciled: reconciling it again would retire the request the person is reading.
        if published and published['scope'] in ('blocker', 'operational_exhaustion'):
            print('Input rejected: Use --resolver-response for this operational request; '
                  'it is not a requirements answer', file=sys.stderr)
            return 2
        return None
    if published and published['scope'] != 'operational_exhaustion':
        return None  # not a quota question; the ordinary answer path decides
    try:
        if not published:
            raise ValueError(resolver_human.stale_request_message(state))
        resolver_human.require_response(candidate, published['request_id'], args.resolver_token)
    except ValueError as error:
        # A saved-state change after display strands the shown token; the person still
        # answered this exact request, re-bound to the current state in this invocation.
        fresh = resolver_human.rebind_stale(candidate, None, args.resolver_token) if args.resolver_token else None
        if fresh is None:
            print(f'Input rejected: {error}', file=sys.stderr)
            return 2
        published = fresh
    if published['scope'] != 'operational_exhaustion':
        return None
    try:
        proposal = candidate['resolver']['human_escalations'][published['request_id']]['identity']['proposal']
        if args.delegate or args.delegate_all:
            raise ValueError('A quota question has no default to delegate; name the model yourself')
        asked, model = quota_route.parse_answer(args.answer, published['questions'], proposal['origin'])
        role = asked['route_role']
        quota_route.validate(candidate, role, model, configured_tool=getattr(runner.opencode, 'CONFIGURED', False),
                             cross_check=dispatch.enforce_cross_model_verification, job=asked.get('job'))
        if quota_route.engine(candidate['settings'], role) == 'opencode':
            try:
                runner.opencode.check_models({role: {'model': model}}, workspace)
            except RuntimeError as error:
                raise ValueError(str(error)) from None
        if interventions.pending(run_dir):
            raise ValueError('Apply the queued intervention before answering')
        attempt = quota_route.stopped_attempt(candidate, failure_status=support.failure_status)
        if not attempt or not attempt['active'] or attempt['role'] != role:
            raise ValueError('The quota-stopped attempt is no longer current; run with --no-chat to see the request')
        runner.abandon_stage(candidate, run_dir, workspace, attempt['attempt_id'])
        attempt = quota_route.stopped_attempt(candidate, failure_status=support.failure_status) or attempt  # archived
        record = quota_route.assign(candidate, role, model, at=runner.now(), via='answer', attempt=attempt,
                                    request_id=published['request_id'])
        runner.finish_human_action(candidate, published)
    except (ValueError, KeyError) as error:
        print(f'Input rejected: {error}', file=sys.stderr)
        return 2
    candidate['pending_questions'] = []
    candidate.pop('user_request', None)
    candidate['stop_reason'] = (f"The {record['job']} now runs on {model} (was {record['from']}). The stopped "
                                "attempt was set aside without replay; its partial work is retained. "
                                "Continue with --resume-paused.")
    runner.commit_user_action(state, candidate, run_dir)
    print(f"{state['status']}: {state['stop_reason']} Saved; no agent launched.")
    return 0
