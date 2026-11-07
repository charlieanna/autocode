"""Answers to AutoResolver's operational requests (a step of autocode_run_actions.handle).

Two answers apply here, and each saves the run without launching anything: --answer
route-ROLE=MODEL to a build stage's quota question (answer_quota_question, #184), and
--resolver-response to a published operational request, which AutoResolver re-evaluates once at
the next explicit resume (#486). Any other --answer to an operational request is refused with the
form it takes (OPERATIONAL_NOT_ANSWER, #675).

Like every step of autocode_run_actions.handle, its steps take the invocation's arguments and
``decided`` (what earlier steps established), return an exit code to stop or None to go on, and
call the runner's own functions as runner.X, so tests that patch the runner still reach them.
The CLI also uses the shared transactional response path for operational chat.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import copy

try:
    from . import autocode_conversation_policy as conversation_policy
    from . import autocode_dispatch as dispatch
    from . import autocode_effort_question as effort_question
    from . import autocode_follow_up as follow_up
    from . import autocode_interventions as interventions
    from . import autocode_member_stop as member_stop
    from . import autocode_operational_information as operational_information
    from . import autocode_quota_route as quota_route
    from . import autocode_recovery_limits as recovery_limits
    from . import autocode_resolver_human as resolver_human
    from . import autocode_resolver_recommendation as recommendation
    from . import autocode_role_transport as role_transport
    from . import autocode_support as support
    from . import autocode_util as util
    from . import autocode_worker_quota as worker_quota
except ImportError:
    import autocode_conversation_policy as conversation_policy
    import autocode_dispatch as dispatch
    import autocode_effort_question as effort_question
    import autocode_follow_up as follow_up
    import autocode_interventions as interventions
    import autocode_member_stop as member_stop
    import autocode_operational_information as operational_information
    import autocode_quota_route as quota_route
    import autocode_recovery_limits as recovery_limits
    import autocode_resolver_human as resolver_human
    import autocode_resolver_recommendation as recommendation
    import autocode_role_transport as role_transport
    import autocode_support as support
    import autocode_util as util
    import autocode_worker_quota as worker_quota


# An --answer to a published blocker or operational request. The refusal names the form the
# request takes, so the person is not sent to look for it (#675).
OPERATIONAL_NOT_ANSWER = (
    "Use --resolver-response for this operational request; it is not a requirements "
    "answer. The form is: " + recovery_limits.RESPONSE_COMMAND
)


def answer(runner, args, parser, state: RunState, state_path, run_dir, workspace, decided):
    """Apply a model answer to a quota stop or a --resolver-response, saving without launching anything."""
    fitted = answer_effort_question(runner, args, state, run_dir, workspace)
    if fitted is not None:
        return fitted
    routed = answer_quota_question(runner, args, state, run_dir, workspace)
    if routed is not None:
        return routed
    crossed = answer_cross_model_question(runner, args, state, run_dir, workspace)
    if crossed is not None:
        return crossed
    if args.resolver_response:
        answered = ((state.get("resolver") or {}).get("human_escalations") or {}).get(args.resolver_request) or {}
        replay = isinstance(answered, dict) and answered.get("status") == "consumed"
        candidate = copy.deepcopy(state)
        try:
            resolver_human.respond_operational(
                candidate, args.resolver_request, args.resolver_token, args.resolver_response, args.resolver_message
            )
        except ValueError as error:
            if args.resolver_response == "provide_information" and recommendation.accepts(args.resolver_message):
                return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
            # A saved-state change after display strands the shown token; the
            # operator's decision still applies to the identical pending
            # request, re-bound to the current state in this same invocation.
            current = resolver_human.current(state)
            if current and current["scope"] not in ("blocker", "operational_exhaustion"):
                return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
            fresh = resolver_human.rebind_stale(candidate, args.resolver_request, args.resolver_token)
            if fresh is None:
                return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
            try:
                resolver_human.respond_operational(
                    candidate,
                    fresh["request_id"],
                    fresh["request_token"],
                    args.resolver_response,
                    args.resolver_message,
                )
            except ValueError as error:
                return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
        if replay:
            # The same response sent again (respond_operational refuses a different one): nothing changes.
            live = resolver_human.current(state)
            review = operational_information.projection(state) or {}
            step = member_stop.next_step(state)
            print(
                f"AutoResolver already received this response to request {args.resolver_request[:12]}; nothing "
                "changed and no provider launched."
                + (
                    " " + step
                    if step
                    else " It re-evaluates the response once at the next autocode resume."
                    if review.get("request_id") == args.resolver_request and review.get("status") == "pending"
                    else ""
                )
                + (
                    f" A newer AutoResolver request is waiting: answer request {live['request_id']} with its own token."
                    if live and live["request_id"] != args.resolver_request
                    else ""
                )
            )
            return 0
        try:
            installed = review_response(runner, candidate, run_dir)
        except (ValueError, OSError) as error:
            return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
        scheduled = (operational_information.projection(candidate) or {}).get("status") == "pending"
        runner.commit_user_action(state, candidate, run_dir)
        if installed:
            print(installed)
            print(
                "The recommended correction is saved as a draft. Continue to review it, then approve the new plan token. No provider launched."
            )
            return 0
        step = member_stop.next_step(candidate)  # a member's model stop names its one control (#541)
        print(
            "AutoResolver received the response. Work, approvals and budgets remain unchanged; no provider launched."
            + (
                " " + step
                if step
                else " It re-evaluates the response once at the next autocode resume."
                if scheduled
                else ""
            )
        )
        return 0


def review_response(runner, candidate, run_dir):
    """Apply an explicitly accepted correction, or retain ordinary information."""
    if runner.lifecycle.apply_resolver_recommendation(candidate, run_dir=run_dir):
        runner.normalize_human_boundary(candidate, run_dir)
        return runner.lifecycle.present(candidate, run_dir)
    resolver_human.review_operational_response(candidate)
    return False


def chat(runner, state: RunState, run_dir, published, *, persist=None, native=None):
    """Collect one operational response through the same transactional boundary as flags."""
    print("AutoResolver: " + published["request"]["decision_needed"])
    print("No retry, approval, permission or budget increase is implied by a response.")
    try:
        reply = input("Corrective information, or /pause: ").strip()
        if not reply:
            return False
        candidate = copy.deepcopy(state)
        resolver_human.respond_operational(
            candidate,
            published["request_id"],
            published["request_token"],
            "leave_paused" if reply == "/pause" else "provide_information",
            "" if reply == "/pause" else reply,
        )
        installed = review_response(runner, candidate, run_dir)
        options = {}
        if persist is not None:
            options["persist"] = persist
        if native is not None:
            options["native"] = native
        runner.commit_user_action(state, candidate, run_dir, **options)
        if installed:
            print(installed)
            print(
                "The recommended correction is saved as a draft; review and approve the new plan token before building."
            )
    except (EOFError, KeyboardInterrupt):
        pass
    except util.JSONCommitUncertain:
        # Replacement may already be visible. The original owner must retain
        # the uncertain commit; a conversational error cannot imply rollback.
        raise
    except (ValueError, OSError) as error:
        print("The response was not saved: " + str(error))
    return False


def answer_quota_question(runner, args, state: RunState, run_dir, workspace):
    """--answer route-ROLE=MODEL at a quota stop (#184); None when this invocation answers something else.

    The one operational question answered with --answer. The model must pass the launch rules
    (engine format, availability, cross-model); a rejection leaves the run paused and the
    request open. An accepted model sets the stopped attempt aside exactly as --abandon-stage
    does, then applies and records the route; --resume-paused continues on a fresh session.
    """
    if not (args.answer or args.delegate):
        return None
    if state.get("status") == quota_route.CROSS_MODEL_STATUS:
        return None  # a cross-model route question (answer_cross_model_question)
    candidate = copy.deepcopy(state)
    published = resolver_human.current(candidate)
    if not any(item.partition("=")[0].startswith(quota_route.PREFIX) for item in [*args.answer, *args.delegate]):
        # Refuse every other answer to an operational request here, before an uncertain attempt is
        # reconciled: reconciling it again would retire the request the person is reading.
        if published and published["scope"] in ("blocker", "operational_exhaustion"):
            return runner.status_command.reject_input(runner, state, workspace, run_dir, OPERATIONAL_NOT_ANSWER)
        return None
    try:
        conversation_action = conversation_policy.load_action(state, run_dir, args)
    except (ValueError, OSError) as error:
        return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
    if conversation_action and conversation_action.get("acknowledged"):
        print("Conversation request was already saved; nothing changed and no provider launched.")
        return 0
    if published and published["scope"] != "operational_exhaustion":
        return None  # not a model question; the ordinary answer path decides
    try:
        if not published:
            raise ValueError(
                follow_up.ANSWER_FINISHED
                if state.get("status") == "TASK_COMPLETE"
                else resolver_human.stale_request_message(state)
            )
        resolver_human.require_response(candidate, published["request_id"], args.resolver_token)
    except ValueError as error:
        # A saved-state change after display strands the shown token; the person still
        # answered this exact request, re-bound to the current state in this invocation.
        fresh = resolver_human.rebind_stale(candidate, None, args.resolver_token) if args.resolver_token else None
        if fresh is None:
            conversation_policy.reject(run_dir, conversation_action, error)
            return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
        published = fresh
    if published["scope"] != "operational_exhaustion":
        return None
    try:
        proposal = candidate["resolver"]["human_escalations"][published["request_id"]]["identity"]["proposal"]
        if args.delegate or args.delegate_all:
            raise ValueError("A model question has no default to delegate; name the model yourself")
        asked, model = quota_route.parse_answer(args.answer, published["questions"], proposal["origin"])
        role = asked["route_role"]
        parallel = worker_quota.current(candidate, proposal["origin"])
        # A batch member ran on its own model; a sibling's earlier answer may have moved the route (#465).
        ran_on = parallel[1].get("model") if parallel else None
        quota_route.validate(
            candidate,
            role,
            model,
            configured_tool=getattr(runner.opencode, "CONFIGURED", False),
            cross_check=dispatch.enforce_cross_model_verification,
            job=asked.get("job"),
            current=ran_on,
        )
        if quota_route.engine(candidate["settings"], role) == "opencode":
            try:
                facade = role_transport.adapter(candidate["settings"], role, runner.opencode, workspace)
                facade.check_models({role: {"model": model}}, workspace)
            except RuntimeError as error:
                raise ValueError(str(error)) from None
        if interventions.pending(run_dir):
            raise ValueError("Apply the queued intervention before answering")
        if proposal["origin"].get("quota_worker") and not parallel:
            raise ValueError("The quota-stopped Builder is no longer current; inspect the batch before retrying")
        attempt: dict | None
        if parallel:
            row, stopped_worker = parallel
            worker_quota.validate_model(model, stopped_worker, dispatch._model_family)
            attempt = {**stopped_worker, "stage": "terra", "pause_status": row["status"]}
        else:
            attempt = quota_route.stopped_attempt(candidate, failure_status=support.failure_status)
            if not attempt or not attempt["active"] or attempt["role"] != role:
                raise ValueError(
                    "The quota-stopped attempt is no longer current; run with --no-chat to see the request"
                )
    except (ValueError, KeyError) as error:
        conversation_policy.reject(run_dir, conversation_action, error)
        return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
    try:
        with conversation_policy.saved_action(state, run_dir, args, conversation_action):
            if conversation_action:
                # abandon_stage durably saves the candidate before returning. Include the
                # route and its receipt in that first save so a crash cannot consume the
                # model question without acknowledging its exact conversation request.
                record = quota_route.assign(
                    candidate,
                    role,
                    model,
                    at=runner.now(),
                    via="answer",
                    attempt=attempt,
                    request_id=published["request_id"],
                    current=ran_on,
                )
                route_source = candidate["user_events"][-1]
                candidate["pending_questions"] = []
                candidate.pop("user_request", None)
                runner.finish_human_action(candidate, published)
                conversation_policy.acknowledge(state, candidate, conversation_action, source_kind="route_assignment")
            if parallel:
                worker_quota.assign_child(row, stopped_worker, model, abandon=runner.abandon_stage)
                # The child attempt is archived; the parent has no unanswered
                # decision and remains paused until an explicit resume.
                runner.run_status.pause(candidate, "PAUSED_STAGE_ABANDONED")
            else:
                runner.abandon_stage(candidate, run_dir, workspace, attempt["attempt_id"])
                attempt = quota_route.stopped_attempt(candidate, failure_status=support.failure_status) or attempt
                if conversation_action:
                    # Abandonment archives the attempt's evidence. Keep the ordinary audit
                    # pointed at that retained copy instead of its removed original path.
                    route_source["events"] = attempt.get("events")
                    candidate["resolver"]["human_escalations"][published["request_id"]]["response"]["event"] = (
                        copy.deepcopy(route_source)
                    )
            if not conversation_action:
                record = quota_route.assign(
                    candidate,
                    role,
                    model,
                    at=runner.now(),
                    via="answer",
                    attempt=attempt,
                    request_id=published["request_id"],
                    current=ran_on,
                )
                # Consume the answered routing question before finalization can
                # mistake it for a remaining requirements clarification.
                candidate["pending_questions"] = []
                candidate.pop("user_request", None)
                runner.finish_human_action(candidate, published)
            candidate["pending_questions"] = []
            candidate.pop("user_request", None)
            candidate["stop_reason"] = (
                f"The {record['job']} now runs on {model} (was {record['from']}). The stopped "
                "attempt was set aside without replay; its partial work is retained. "
                "Continue with --resume-paused."
            )
            runner.commit_user_action(state, candidate, run_dir)
    except (ValueError, KeyError) as error:
        return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
    print(f"{state['status']}: {state['stop_reason']} Saved; no agent launched.")
    return 0


def answer_cross_model_question(runner, args, state: RunState, run_dir, workspace):
    """--answer route-ROLE=MODEL at PAUSED_CROSS_MODEL; None when this invocation answers something else.

    The cross-model stop has no attempt or issued token: the question is the
    verifier route that would grade its own work. Naming a model that passes the
    launch and independence rules saves that route; --resume-paused then continues
    in this invocation, otherwise the person resumes explicitly.
    """
    if state.get("status") != quota_route.CROSS_MODEL_STATUS:
        return None
    if not (args.answer or args.delegate):
        return None
    if not any(item.partition("=")[0].startswith(quota_route.PREFIX) for item in [*args.answer, *args.delegate]):
        return None
    candidate = copy.deepcopy(state)
    try:
        asked = quota_route.cross_model_question(candidate)
        if not asked:
            raise ValueError("This run's routes no longer break cross-model verification; --resume-paused continues")
        if args.delegate or args.delegate_all:
            raise ValueError("A model question has no default to delegate; name the model yourself")
        asked, model = quota_route.parse_answer(args.answer, [asked], {"pause_status": quota_route.CROSS_MODEL_STATUS})
        role = asked["route_role"]
        quota_route.validate(
            candidate,
            role,
            model,
            configured_tool=getattr(runner.opencode, "CONFIGURED", False),
            cross_check=quota_route.cross_model_check(role),
            job=asked.get("job"),
        )
        if quota_route.engine(candidate["settings"], role) == "opencode":
            try:
                facade = role_transport.adapter(candidate["settings"], role, runner.opencode, workspace)
                facade.check_models({role: {"model": model}}, workspace)
            except RuntimeError as error:
                raise ValueError(str(error)) from None
        if runner.interventions.pending(run_dir):
            raise ValueError("Apply the queued intervention before answering")
        record = quota_route.assign(
            candidate,
            role,
            model,
            at=runner.now(),
            via="answer",
            attempt={"pause_status": quota_route.CROSS_MODEL_STATUS, "role": role},
        )
        candidate["stop_reason"] = (
            f"The {record['job']} now runs on {model} (was {record['from']}). Continue with --resume-paused."
        )
        runner.commit_user_action(state, candidate, run_dir)
    except (ValueError, KeyError) as error:
        return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
    print(f"{state['status']}: {state['stop_reason']} Saved; no agent launched.")
    return 0


def answer_effort_question(runner, args, state: RunState, run_dir, workspace):
    """--answer effort-ROLE=EFFORT at PAUSED_EFFORT_UNSUPPORTED (#2057); None when this invocation answers
    something else.

    Like the cross-model stop it has no attempt or issued token: the question is the saved route
    whose person-named level its model does not declare. A declared level is saved as the person's
    own choice; the person then continues with --resume-paused, as after a cross-model answer.
    """
    if state.get("status") != effort_question.STATUS or not (args.answer or args.delegate):
        return None
    if not any(item.partition("=")[0].startswith(effort_question.PREFIX) for item in [*args.answer, *args.delegate]):
        return None
    candidate = copy.deepcopy(state)
    try:
        asked = effort_question.question(candidate)
        if not asked:
            raise ValueError("Every named effort is one its model declares now; --resume-paused continues")
        if args.delegate or args.delegate_all:
            raise ValueError("An effort question has no default to delegate; choose the level yourself")
        effort = effort_question.parse_answer(args.answer, asked)
        if runner.interventions.pending(run_dir):
            raise ValueError("Apply the queued intervention before answering")
        record = effort_question.assign(candidate, asked, effort, at=runner.now())
        following = effort_question.question(candidate)
        candidate["stop_reason"] = (
            f"The {record['job']} now runs at {effort} on {record['model']} (was {record['from']}). "
            + (f"Next: {following['question']}." if following else "Continue with --resume-paused.")
        )
        runner.commit_user_action(state, candidate, run_dir)
    except (ValueError, KeyError) as error:
        return runner.status_command.reject_input(runner, state, workspace, run_dir, error)
    print(f"{state['status']}: {state['stop_reason']} Saved; no agent launched.")
    return 0
