"""The read side of the task-run interface: a stable summary of one run.

Programs that drive AutoCode (the scenario harness, multi-task coordination)
read this instead of the ~140 keys of state.json, which stay private to the
runner. `autocode --run-dir RUN --status` prints it under "view".

This is a contract (see docs/task-run.md). Add fields; never rename or remove
one, and bump SCHEMA if a meaning changes. It is a read-only projection of saved
state and explicitly named usage logs, never sibling/private state discovery.
It imports nothing from the runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

from copy import deepcopy
from pathlib import Path

try:
    from . import autocode_acceptance_recovery as acceptance_recovery
    from . import autocode_brief_obligations as brief_obligations
    from . import autocode_current_behavior as current_behavior
    from . import autocode_interaction_timing as interaction_timing
    from . import autocode_internal_failure_view as operational_block
    from . import autocode_plan_only as plan_only
    from . import autocode_scope_pause as scope_pause
    from . import autocode_stop as stop
except ImportError:
    import autocode_acceptance_recovery as acceptance_recovery
    import autocode_brief_obligations as brief_obligations
    import autocode_current_behavior as current_behavior
    import autocode_interaction_timing as interaction_timing
    import autocode_internal_failure_view as operational_block
    import autocode_plan_only as plan_only
    import autocode_scope_pause as scope_pause
    import autocode_stop as stop

try:
    from . import autocode_acceptance_retry_next as retry_next
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_accepted_source as accepted_source
    from . import autocode_adaptive_planning as adaptive_planning
    from . import autocode_answer_options as answer_options
    from . import autocode_approval_provenance as approval_provenance
    from . import autocode_chat_view as chat_view
    from . import autocode_classification_recovery as classification_recovery
    from . import autocode_code_checkpoints as code_checkpoints
    from . import autocode_command_inputs as command_inputs
    from . import autocode_component_plan as component_plan
    from . import autocode_containment_policy as containment_policy
    from . import autocode_contract_identity as contract_identity
    from . import autocode_conversation_projection as conversation_projection
    from . import autocode_cost as cost
    from . import autocode_criterion_changes as criterion_changes
    from . import autocode_design_coverage as design_coverage
    from . import autocode_diff_coverage as diff_coverage
    from . import autocode_efficiency, autocode_run_metrics, autocode_usage
    from . import autocode_effort_question as effort_question
    from . import autocode_escalation_view as escalation_view
    from . import autocode_finding_rescope as finding_rescope
    from . import autocode_goals as goals
    from . import autocode_implementation_discrepancy as implementation_discrepancy
    from . import autocode_job_end as job_end
    from . import autocode_job_time_view as job_time_view
    from . import autocode_liveness as liveness_policy
    from . import autocode_member_stop as member_stop
    from . import autocode_no_progress_recovery as no_progress_recovery
    from . import autocode_operational_information as operational_information
    from . import autocode_output_policy as output_policy
    from . import autocode_package_scope as package_scope
    from . import autocode_pause_authority as pause_authority
    from . import autocode_pause_category as pause_category
    from . import autocode_pause_command as pause_command
    from . import autocode_person_text as person_text
    from . import autocode_plan_file as plan_file
    from . import autocode_plan_file_origin as plan_file_origin
    from . import autocode_plan_retention as plan_retention
    from . import autocode_plan_review_view as plan_review_view
    from . import autocode_planning_calls as planning_calls
    from . import autocode_planning_review_usage as review_usage
    from . import autocode_progressive_plan as progressive_rules
    from . import autocode_project_notes_lifecycle as project_notes
    from . import autocode_python_test_roles as test_roles
    from . import autocode_quota_route as quota_route
    from . import autocode_recovery_exhaustion as recovery_exhaustion
    from . import autocode_recovery_history as recovery_history
    from . import autocode_recovery_limits as recovery_limits
    from . import autocode_recovery_progress as recovery_progress
    from . import autocode_recovery_view as recovery_view
    from . import autocode_report_repair_view as report_repair_view
    from . import autocode_report_retry as report_retry
    from . import autocode_report_retry_admission as report_retry_admission
    from . import autocode_request_usage as request_usage
    from . import autocode_requirements_review as requirements_review
    from . import autocode_resolver_human as resolver_human
    from . import autocode_resolver_recommendation as recommendation
    from . import autocode_risk_disclosure as risk_disclosure
    from . import autocode_role_transport as role_transport
    from . import autocode_roles as roles
    from . import autocode_route_ladder as route_ladder
    from . import autocode_run_stage_models as run_stage_models
    from . import autocode_stage_decisions as stage_decisions
    from . import autocode_stage_pipeline as stage_pipeline
    from . import autocode_stage_policy as stage_policy
    from . import autocode_status_label as status_label
    from . import autocode_status_observation as status_observation
    from . import autocode_stop_explanations as stop_explanations
    from . import autocode_task_input as task_input
    from . import autocode_tool_containment as tool_containment
    from . import autocode_trace_coverage as trace_coverage
    from . import autocode_transport_recovery as transport_recovery
    from . import autocode_validation_exhaustion_recovery as validation_recovery
    from . import autocode_verification_config as verification_config
    from . import autocode_verification_view as verification_view
    from . import autocode_workflows as workflows
    from . import autocode_worktree_choice as worktree_choice
except ImportError:
    import autocode_acceptance_retry_next as retry_next
    import autocode_acceptance_tests as acceptance_tests
    import autocode_accepted_source as accepted_source
    import autocode_adaptive_planning as adaptive_planning
    import autocode_answer_options as answer_options
    import autocode_approval_provenance as approval_provenance
    import autocode_chat_view as chat_view
    import autocode_classification_recovery as classification_recovery
    import autocode_code_checkpoints as code_checkpoints
    import autocode_command_inputs as command_inputs
    import autocode_component_plan as component_plan
    import autocode_containment_policy as containment_policy
    import autocode_contract_identity as contract_identity
    import autocode_conversation_projection as conversation_projection
    import autocode_cost as cost
    import autocode_criterion_changes as criterion_changes
    import autocode_design_coverage as design_coverage
    import autocode_diff_coverage as diff_coverage
    import autocode_efficiency
    import autocode_effort_question as effort_question
    import autocode_escalation_view as escalation_view
    import autocode_finding_rescope as finding_rescope
    import autocode_goals as goals
    import autocode_implementation_discrepancy as implementation_discrepancy
    import autocode_job_end as job_end
    import autocode_job_time_view as job_time_view
    import autocode_liveness as liveness_policy
    import autocode_member_stop as member_stop
    import autocode_no_progress_recovery as no_progress_recovery
    import autocode_operational_information as operational_information
    import autocode_output_policy as output_policy
    import autocode_package_scope as package_scope
    import autocode_pause_authority as pause_authority
    import autocode_pause_category as pause_category
    import autocode_pause_command as pause_command
    import autocode_person_text as person_text
    import autocode_plan_file as plan_file
    import autocode_plan_file_origin as plan_file_origin
    import autocode_plan_retention as plan_retention
    import autocode_plan_review_view as plan_review_view
    import autocode_planning_calls as planning_calls
    import autocode_planning_review_usage as review_usage
    import autocode_progressive_plan as progressive_rules
    import autocode_project_notes_lifecycle as project_notes
    import autocode_python_test_roles as test_roles
    import autocode_quota_route as quota_route
    import autocode_recovery_exhaustion as recovery_exhaustion
    import autocode_recovery_history as recovery_history
    import autocode_recovery_limits as recovery_limits
    import autocode_recovery_progress as recovery_progress
    import autocode_recovery_view as recovery_view
    import autocode_report_repair_view as report_repair_view
    import autocode_report_retry as report_retry
    import autocode_report_retry_admission as report_retry_admission
    import autocode_request_usage as request_usage
    import autocode_requirements_review as requirements_review
    import autocode_resolver_human as resolver_human
    import autocode_resolver_recommendation as recommendation
    import autocode_risk_disclosure as risk_disclosure
    import autocode_role_transport as role_transport
    import autocode_roles as roles
    import autocode_route_ladder as route_ladder
    import autocode_run_metrics
    import autocode_run_stage_models as run_stage_models
    import autocode_stage_decisions as stage_decisions
    import autocode_stage_pipeline as stage_pipeline
    import autocode_stage_policy as stage_policy
    import autocode_status_label as status_label
    import autocode_status_observation as status_observation
    import autocode_stop_explanations as stop_explanations
    import autocode_task_input as task_input
    import autocode_tool_containment as tool_containment
    import autocode_trace_coverage as trace_coverage
    import autocode_transport_recovery as transport_recovery
    import autocode_usage
    import autocode_validation_exhaustion_recovery as validation_recovery
    import autocode_verification_config as verification_config
    import autocode_verification_view as verification_view
    import autocode_workflows as workflows
    import autocode_worktree_choice as worktree_choice

SCHEMA = 2
COMPLETE = ("TASK_COMPLETE", "COMPLETE")
# Statuses where relaunching the run, with no user input, continues the work.
CONTINUE = ("RUNNING", "DISCOVERING", "WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL")
QUESTION_FIELDS = answer_options.QUESTION_FIELDS
_UNSET = object()  # a caller that computed no retained-retry answer: compute it from the run's workspace
_question_view = answer_options.question_view


def requirements_view(state: RunState) -> dict | None:
    """Additive projection of the current turn's Requirements handoff (#3650); None when none exists."""
    handoff = requirements_review.current_turn_handoff(state)
    report = handoff.get("report") if isinstance(handoff, dict) else None
    if not isinstance(report, dict):
        return None
    rejected = {
        ob.get("assumption_id")
        for ob in state.get("deferred_obligations", [])
        if isinstance(ob, dict) and ob.get("status") != "resolved"
    }
    confirmed_ids = requirements_review.confirmed_assumption_ids(state)
    unconfirmed_ids = {
        row.get("id") for row in requirements_review.unconfirmed_assumptions(state) if isinstance(row, dict)
    }
    skip_confirmed = (handoff.get("confirmed_assumptions") or {}).get("source") == requirements_review.CONFIRMED_BY_SKIP
    standing = requirements_review.assumption_standing(state)
    proposed_assumptions = []
    for raw in report.get("proposed_assumptions") or []:
        try:
            row = goals.normalize_assumption(raw)
        except ValueError:
            continue
        if row["legacy"] or not row["id"] or row["id"] in rejected:
            continue
        proposed_assumptions.append(
            {
                "id": row["id"],
                "text": row["text"],
                "category": row["category"],
                "convention_ref": row["convention_ref"],
                "rationale": row["rationale"],
                "confirmed": bool(skip_confirmed or row["id"] in confirmed_ids or row["id"] not in unconfirmed_ids),
                # Where `confirmed` comes from (#3898): the person, an inferred convention, a whole approval or a skip.
                "standing": standing.get(row["id"], requirements_review.NEEDS_CONFIRMATION),
            }
        )
    requirements = [
        {
            "id": str(item.get("id") or ""),
            "text": str(item.get("text") or ""),
            "source_quote": str(item.get("source_quote") or ""),
        }
        for item in report.get("requirements") or []
        if isinstance(item, dict)
    ]
    tests = [str(item) for item in report.get("acceptance_tests") or [] if isinstance(item, str)]
    conflicts: list[dict | str] = []
    for item in report.get("conflicts") or []:
        if isinstance(item, dict):
            conflicts.append(
                {
                    "requirement_ids": [str(rid) for rid in item.get("requirement_ids") or []],
                    "description": str(item.get("description") or ""),
                }
            )
        elif isinstance(item, str):
            conflicts.append(item)
    return {
        "handoff_ref": goals.s.digest(report)[:12],
        "intended_outcome": str(report.get("intended_outcome") or ""),
        "requirements": requirements,
        "proposed_assumptions": proposed_assumptions,
        "acceptance_tests": tests,
        "constraints": [item for item in report.get("constraints") or [] if isinstance(item, str)],
        "conflicts": conflicts,
        # What Requirements named in this report, before the person confirms it (#3630): the
        # workflow field reports the job only once it starts, and the model probe grades both.
        "job": report.get("job") if report.get("job") in (*workflows.WORKFLOWS, "none") else None,
        "clarity": report.get("clarity") if report.get("clarity") in adaptive_planning.CLARITY else None,
    }


def accepted_reviews(state: RunState) -> list:
    """The human-review criteria whose review receipt is valid for the current validation, in plan
    order: the plain view reports them accepted, as --inspect-evidence already did (#2342). Legacy or
    partial plans carry no review binding to show."""
    contract = state.get("goal_contract") or {}
    try:
        criteria = contract["body"]["acceptance_criteria"]
        missing = set(goals.missing_human_reviews(state))
        return [row["id"] for row in criteria if row.get("human_review") and row["id"] not in missing]
    except (KeyError, TypeError, AttributeError, ValueError):
        return []


@status_observation.budgeted
def view(
    state: RunState,
    *,
    completion_current=None,
    visual_acceptance=None,
    stale_report_repair=False,
    report_repair_revision=None,
    report_repair_source_unavailable=False,
    liveness=None,
    runner_check_liveness=None,
    verification_obligation=None,
    conversation_journal=None,
    design_input_preview=None,
    controller=None,
    run_dir=None,
    workspace=None,
    dev_server=None,
    observation=None,
    execution_inspection=None,
    stop_pending=False,
    human_projection=None,
) -> dict:
    """Caller supplies fresh completion, evidence, repair, supervision and design-input inspections."""
    try:
        from . import autocode_attention as attention
        from . import autocode_changeset_store as changeset_store
        from . import autocode_controller_owner as controller_owner
        from . import autocode_coordination_admission as coordination_admission
        from . import autocode_coordination_store as coordination_store
        from . import autocode_related_profile as related_profile
        from . import autocode_replay_view as replay_view
        from . import autocode_scope_question as scope_question
        from . import autocode_worker_view as worker_view
    except ImportError:
        import autocode_attention as attention
        import autocode_changeset_store as changeset_store
        import autocode_controller_owner as controller_owner
        import autocode_coordination_admission as coordination_admission
        import autocode_coordination_store as coordination_store
        import autocode_related_profile as related_profile
        import autocode_replay_view as replay_view
        import autocode_scope_question as scope_question
        import autocode_worker_view as worker_view
    saved_state = state
    human_projection = resolver_human.projection(state) if human_projection is None else human_projection
    state = resolver_human.decision_state(state, human_projection)
    replay = replay_view.completion_policy(state)
    replay_progress, invalid_replay, simulated = replay["progress"], replay["invalid"], replay["simulated"]
    if simulated or invalid_replay:
        completion_current = False
    status = state.get("status", "")
    # What the next resume of a retained acceptance verification retry does (#3927): one answer, computed
    # once with the view's workspace, read by needs, next_command, the recovery card, chat and progress.
    retained = status_observation.retained_retry(saved_state, workspace or state.get("workspace"))
    next_action = action(
        saved_state,
        run_dir=run_dir,
        workspace=workspace,
        stale_report_repair=stale_report_repair,
        human_projection=human_projection,
        retained=retained,
    )
    stop_reason = (
        recovery_exhaustion.NOTICE
        if recovery_exhaustion.pending(saved_state)
        else job_end.NOTICE
        if job_end.pending(saved_state)
        else verification_config.NOTICE
        if verification_config.pending(saved_state)
        else classification_recovery.NOTICE
        if classification_recovery.pending(saved_state)
        else validation_recovery.notice(saved_state)
        if validation_recovery.pending(saved_state)
        else no_progress_recovery.notice(saved_state)
        if no_progress_recovery.pending_legacy(saved_state)
        else state.get("stop_reason")
    )
    try:
        from . import autocode_checkpoint_reconciliation as reconciliation
    except ImportError:
        import autocode_checkpoint_reconciliation as reconciliation
    reconcile = (
        None
        if (operational_block.presentation(saved_state, include_hold=True)) or recovery_exhaustion.pending(saved_state)
        else reconciliation.offer(
            saved_state, liveness, controller=controller, stop_pending=stop_pending, workspace=workspace
        )
    )
    if reconcile is not None:
        next_action = {
            "needs": reconcile,
            "pause_category": None,
            "pause_category_label": None,
            "next_command": pause_command.command(
                {**reconcile, "kind": "resume"},
                for_client=True,
                run_dir=run_dir or state.get("run_dir"),
                workspace=workspace or state.get("workspace"),
            ),
        }
    elif not operational_block.presentation(saved_state, include_hold=True):
        try:
            from . import autocode_source_reconciliation as source_reconciliation
        except ImportError:
            import autocode_source_reconciliation as source_reconciliation
        source_resume = source_reconciliation.offer(
            saved_state,
            workspace or state.get("workspace"),
            liveness,
            controller=controller,
            stop_pending=stop_pending,
        )
        if source_resume is not None:
            next_action = {
                "needs": source_resume,
                "pause_category": None,
                "pause_category_label": None,
                "next_command": pause_command.command(
                    source_resume,
                    for_client=True,
                    run_dir=run_dir or state.get("run_dir"),
                    workspace=workspace or state.get("workspace"),
                    recovery_token=recovery_view.token(saved_state),
                ),
            }
        else:
            catalog_resume = status_observation.catalog_offer(
                saved_state, controller=controller, stop_pending=stop_pending
            )
            if catalog_resume is not None:
                next_action = {
                    "needs": catalog_resume,
                    "pause_category": None,
                    "pause_category_label": None,
                    "next_command": pause_command.command(
                        catalog_resume,
                        for_client=True,
                        run_dir=run_dir or state.get("run_dir"),
                        workspace=workspace or state.get("workspace"),
                    ),
                }
    continuing = controller_owner.continuing(saved_state, controller)
    if continuing:
        # The live controller will save its own next state; an action taken now is refused by the run lock.
        next_action = {"needs": None, "pause_category": None, "pause_category_label": None, "next_command": None}
    need = next_action["needs"]
    task = state.get("current_task") or {}
    active = state.get("active_stage")
    supervision = active.get("supervision") if isinstance(active, dict) else None
    stage_liveness = liveness_policy.classify(supervision, liveness)
    check = state.get("active_runner_check")
    check_supervision = check.get("supervision") if isinstance(check, dict) else None
    # Saved stage records, then the running stage, so the running one is read first; a corrupt list reads empty.
    stages = state.get("stages")
    launches = [*(stages if isinstance(stages, list) else []), active]
    plan = approved_contract(state)
    # Tokens and cost so far, by role (autocode_usage.summary), with the live accounting the cost reads.
    usage_summary = autocode_usage.summary(state)
    recovery = None if continuing else recovery_view.project(saved_state, need, retained=retained)
    result = {
        "replay_source": None if invalid_replay else replay_view.project(state, run_dir=run_dir),
        "replay_progress": replay_progress,
        "replay_result": deepcopy(state.get("replay_result")),
        "runner_check": {
            **{
                key: deepcopy(check.get(key))
                for key in ("stage", "summary", "started_at", "updated_at", "command", "output", "supervision")
            },
            **{
                key: deepcopy(check[key])
                for key in ("phase", "current", "total", "elapsed_seconds")
                if check.get(key) is not None
            },
            "liveness": liveness_policy.classify(check_supervision, runner_check_liveness),
        }
        if check
        else None,
        "dependency": state.get("dependency_wait"),
        "verification_obligation": deepcopy(verification_obligation),
        "schema": SCHEMA,
        # The run this view describes. A client's own handle may name the folder
        # it selected before AutoCode bootstrapped a child project there; the
        # effective location is what named commands carry.
        "workspace": str(workspace) if workspace is not None else state.get("workspace"),
        "run_dir": str(run_dir) if run_dir is not None else None,
        # Where this chat changes files: its own worktree and branch, or the person's folder (autocode_worktree_choice).
        "worktree": worktree_choice.where(state, workspace),
        "controller": deepcopy(controller),
        "observation": deepcopy(observation),
        "execution": worker_view.execution(
            state.get("execution"), observation=observation, inspection=execution_inspection
        ),
        # Saved launch identity, never inferred from current installed resources.
        "prompts_hash": state.get("prompts_hash"),
        "task": state.get("task"),
        "task_sha256": task_input.status_digest(state.get("task")),
        # Controller that last saved the checkpoint; legacy and unversioned code stay unknown.
        "runner": deepcopy(state.get("runner")) if isinstance(state.get("runner"), dict) else None,
        "interaction_timing": interaction_timing.project(state),
        "pause_category": next_action["pause_category"],
        "pause_category_label": next_action["pause_category_label"],
        "status": status,
        # The status in a person's words, as the run list words it (autocode_status_label).
        "status_label": status_label.label(status),
        "next_command": next_action["next_command"],
        "liveness": stage_liveness,
        "done": status in COMPLETE and not simulated and not invalid_replay,
        "needs": need,
        "recovery": recovery,
        # What went wrong and what AutoCode did about it, oldest first (autocode_recovery_history): rejected
        # answers, rewritten reports, Builder send-backs, Investigator and Resolver calls, as plain sentences.
        "recoveries": recovery_history.project(state),
        "verification": verification_view.project(state, accepted_human_ids=accepted_reviews(state)),
        "review_result": None,  # status may add a read-time inspection; evidence remains authenticated recorded facts
        "code_checkpoints": code_checkpoints.project(state, run_dir=run_dir, workspace=workspace),
        # Which saved checkpoints built each approved criterion's milestones (#730): attribution, not proof.
        "changes_by_criterion": criterion_changes.attribute((plan or {}).get("body"), state.get("code_checkpoints")),
        # The run's development server as the caller inspected it (autocode_dev_server.inspect, #730).
        "dev_server": deepcopy(dev_server),
        "phase": state.get("phase"),
        "next_stage": state.get("next_stage"),
        # The next stage's job name on screen (autocode_roles): 'draft_plan' -> 'Planner'.
        "next_stage_label": roles.screen_name(str(state.get("next_stage") or ""), state) or None,
        "iteration": state.get("iteration"),
        "stop_reason": stop_reason,
        # Plain-English explanation of this stop (#715), added never renamed.
        "explanation": stop_explanations.explain(
            status,
            stop_reason=stop_reason or "",
            needs=need,
            held_cause=pause_authority.answered_cause(saved_state),
            ready=(controller or {}).get("kind") == "ready",
            internal_failure=operational_block.presentation(saved_state, include_hold=True),
            recovery=recovery,
        ),
        "current_task": {key: task.get(key) for key in ("id", "objective", "milestone_id")} if task else None,
        # The kind of job named for the request (autocode_workflows.WORKFLOWS);
        # None until it is named, and for runs that predate naming it.
        "workflow": (state.get("workflow") or {}).get("kind"),
        # "model" when Requirements (or an earlier runtime's recognizer) named it, "user" when --workflow
        # named it, "default" on a flow without Requirements, where every task is a build; the reason.
        "workflow_source": (state.get("workflow") or {}).get("source"),
        "workflow_reason": (state.get("workflow") or {}).get("reason"),
        # How much planning the run chose and why (autocode_adaptive_planning.view, #697): whether recognition
        # skipped Requirements as clear, the plan's size, the review allowance and an early approval.
        "planning_depth": adaptive_planning.view(state),
        # The Plan Reviewer's latest accepted verdict, its blocking concern ids and its concerns
        # (autocode_plan_review_view); null before a review. Display only.
        "plan_review": plan_review_view.project(state),
        # Which planning path a bug-fix run took and why (autocode_stage_policy.view, #2021): the compact path's
        # omitted Planner draft and what covers it, or the full path's reasons; null when the run never decided.
        "stage_policy": stage_policy.view(state),
        # Every stage the run launched and every stage a saved decision skipped, with the reason and what covers
        # it (autocode_stage_decisions.view): one place for why a stage ran or did not; it grants nothing.
        "stage_decisions": stage_decisions.view(state, liveness=stage_liveness),
        # Which request of the conversation the run is on: 1, then one more per --follow-up
        # (autocode_follow_up).
        "turn": len(state.get("turns") or []) + 1,
        "evidence": evidence(state),
        # Who approved, edited the plan, resumed a pause or accepted completion (#722).
        "approvals": approval_provenance.view(state),
        # Canonical publication metadata is historical until the status interface
        # authenticates its files and current completion; no state mutation here.
        "evidence_report": evidence_report(state),
        "acceptance_tests": acceptance_tests.view(state),  # spec-first Tester files and results (#1200)
        # What the next resume of a retained acceptance verification retry does (autocode_acceptance_retry_next):
        # {next: recheck|tester|exhausted|blocked, rechecks, allowed}; allowed is null for an unbounded
        # runner-not-started retry or a blocked report, and the field is null without a retained retry.
        "acceptance_verification_retry": retry_next.public(retained),
        # Tokens and cost so far, by role (autocode_usage.summary): reported, estimated and unknown kept apart.
        "usage": usage_summary,
        # Spend against the person's money cap at the dated price table (autocode_cost, #721), and what
        # the running stage has spent so far (`running_usd`), from the live accounting above.
        "cost": cost.view(state, accounting=usage_summary["accounting"]),
        # Planning's model calls since the person's last answer or feedback, against the cap on them
        # (autocode_planning_calls, owner decision 2026-11-12): `cap` is null when the run has none.
        "planning_calls": planning_calls.view(state),
        # How the struggles behind the run's escalations and Builder retries ended, as the runner
        # stamped them (autocode_route_ladder): `passed` once the run completed on work a validation
        # passed or moved on from it, `paused` at a retry-limit pause, `open` while unresolved.
        "escalation_outcomes": route_ladder.outcome_summary(state),
        # Each escalation (a role's effort climb, the Builder's stronger attempt) with the role and stage it was
        # for, the route it climbed from and to, what its escalated attempts cost and how it ended
        # (autocode_escalation_view, #612): passed, failed_again, paused or running.
        "escalations": escalation_view.rows(state),
        "request_context": request_usage.view(state),
        "output_transport": output_policy.view(state),
        # Runner-owned assignment provenance, never a model diagnosis or completion proof.
        "direct_rework_assignments": deepcopy(state.get("direct_rework_assignments", [])),
        # The route each role's next launch uses (autocode_role_route): its model, engine, primary
        # job, reasoning level and the tool that runs it, plus the account its model bills and why
        # the route is what it is; and every model a person named for a role after its quota ran
        # out (autocode_quota_route): role, from, to, stage, at, via. Actual models remain on
        # usage.accounting.attempts.
        "routes": quota_route.routes(state),
        # The provider those OpenCode-engine routes launch through ("opencode", "claude", a configured
        # tool's name); None when every role runs on Codex (#717).
        "provider": quota_route.provider(state),
        # The selected source receipt is a read-only binding for cross-run citation checks.
        "source_catalog_receipt": state.get("source_catalog_receipt")
        if state.get("source_catalog_receipt") is None or isinstance(state.get("source_catalog_receipt"), str)
        else "invalid",
        "route_assignments": quota_route.assignments(state),
        # Whether a person may change this started run's stage models now, why not, and the read-only
        # list whose `set` arrays change one between stages (autocode_run_stage_models); None before routes.
        "stage_models": run_stage_models.offer(state, run_dir=run_dir, liveness=stage_liveness),
        # At a pause the person requested while the approved plan is being built: the named action that reports
        # where the code departs from that plan without revising it, and the reports kept for the reviewers
        # (autocode_implementation_discrepancy, #3045). None when there is neither.
        "implementation_discrepancy": implementation_discrepancy.project(state, run_dir=run_dir, workspace=workspace),
        # What earlier runs learned about this repository and this run was given as advisory context
        # (notes), what this run's Investigator learned (learned), each with its receipt, and the list
        # whose argument arrays retract or correct one (autocode_project_notes_lifecycle, #2943).
        "project_notes": project_notes.view(state),
        # AutoResolver's one re-evaluation of corrective information sent to an operational request
        # (autocode_operational_information, #486): status pending, held, admitted, stale or superseded
        # (the run left the pause unevaluated); the decision, its reason and, when held, the exact next
        # action. None when no response is current.
        "information_review": operational_information.projection(state),
        # Built-in OpenCode non-planning stages: "contained" (kernel tool boundary) or
        # "uncontained_user_accepted" (--allow-uncontained-tools); None when no stage uses it (#413).
        "tool_containment": containment_policy.mode(state.get("settings")),
        # The kernel boundary the latest contained launch ran in, from its own stage record (the running
        # stage first): backend ("seatbelt" or "bubblewrap"), backend_version, stage, iteration and
        # started_at. None until a stage launches contained (#709).
        "tool_containment_backend": tool_containment.recorded_backend(launches),
        # The monorepo package the run works in (--package, autocode_package_scope); None for the whole repository.
        "package": package_scope.of(state.get("settings")),
        # Retired with the bug-fix Investigator, the only stage that offered report recovery (2026-11-10);
        # always null, kept because the status view never drops a field.
        "job_report_recovery": None,
        "report_repair": report_repair_view.project(
            state,
            stale=stale_report_repair,
            current_revision=report_repair_revision,
            source_unavailable=report_repair_source_unavailable,
        ),
        # The pause a client may request while the run works (autocode_pause_offer, #1598); status supplies it.
        "pause": None,
        # Another run holds the folder this ready run's next step needs (autocode_checkout_wait); status supplies it.
        "checkout_wait": None,
    }
    # Work that belongs to another repository, and what the person left out of this run (#2066).
    result["scope"] = scope_question.view(
        state, (state.get("requirements_handoff") or {}).get("dropped_external_parts")
    )
    result["request_sources"] = deepcopy(brief_obligations.sources(state))
    result["related_profile"] = related_profile.project(state)
    result["coordination"] = (
        coordination_store.offer(
            run_dir,
            eligible=result["workflow"] == "bugfix"
            and not (state.get("settings") or {}).get("coordination_member")
            and result["scope"]["spans_repositories"]
            and not result["scope"]["decision"]
            and coordination_admission.pending_scope(result),
        )
        if run_dir and result["execution"]["mode"] == "owned"
        else None
    )
    if run_dir is not None:  # the other runs of this run's change set (autocode_changeset)
        result["scope"]["linked"] = changeset_store.linked_for(run_dir)
    # The requirements the current plan proves only on a stand-in for what the user sees, because code
    # outside this checkout renders it (#2068): requirement_id, the criteria it cites (evidence) and
    # proxy_reason, from the trace that produced the current contract; [] when none or no plan yet.
    result["proxied_requirements"] = trace_coverage.proxied_rows(plan_retention.validated_trace(state))
    result["requirements"] = requirements_view(state)
    result["chat"] = chat_view.project(state, result, run_dir=run_dir, workspace=workspace)
    result["attention"] = attention.project(
        state, result, workspace=workspace, run_dir=run_dir, context=result["chat"]["context"]
    )
    if simulated or invalid_replay:
        result["completion_current"] = False
    result["efficiency"] = autocode_efficiency.summary(
        state,
        accounting=result["usage"]["accounting"],
        completion_current=completion_current,
        visual_acceptance=visual_acceptance,
    )
    # Where the spend went, what was spent again, and what the checking stages produced,
    # read from the cost, usage, efficiency, recoveries and evidence above (display only).
    result["run_metrics"] = autocode_run_metrics.project(result)
    # The six pipeline jobs, each with where it stands, its route's model and level and its spend, read from
    # the routes, stage decisions and run metrics above, so a client shows the pipeline without a job table.
    result["pipeline"] = stage_pipeline.project(state, result)
    contract = state.get("goal_contract") or {}
    if isinstance(contract, dict) and isinstance(contract.get("body"), dict):
        try:
            if (
                type(contract.get("revision")) is int
                and contract["revision"] > 0
                and state.get("displayed_goal") == contract_identity.token(contract)
                and contract_identity.sealed(contract)
            ):
                # Approval consumers need actual fields, not model-authored display text. The
                # original fields keep their unconditional shape (null when absent), because the
                # status view only grows additively. Milestones and the end-to-end flow are the
                # completion order the person is approving — criteria summaries alone hid a
                # coordinator-owned joint-proof milestone inside a member plan (#2434) — added
                # only where the contract body carries them: legacy bodies predate both fields
                # (LEGACY_BODY_SCHEMA omits them) and stay showable and approvable unchanged.
                result["displayed_plan"] = {
                    "revision": contract["revision"],
                    "hash": contract["hash"],
                    "token": state["displayed_goal"],
                    "review_summary_markdown": plan_file.summary_markdown(
                        contract["body"],
                        revision=contract["revision"],
                        status="approved" if contract_identity.approved(state) else "not approved",
                    ),
                    "review_markdown": plan_file.review_markdown(
                        contract["body"], observed_behavior=(current_behavior.evidence(state) or {}).get("text", "")
                    ),
                    **{
                        key: deepcopy(contract["body"].get(key))
                        for key in (
                            "acceptance_criteria",
                            "constraints",
                            "permission_boundaries",
                            "git_delivery",
                            "scope_exclusions",
                        )
                    },
                    **(
                        {
                            key: deepcopy(contract["body"][key])
                            for key in ("milestones", "end_to_end_flow")
                            if key in contract["body"]
                        }
                    ),
                }
        except (AttributeError, KeyError, TypeError, ValueError):
            pass  # Missing, stale or unsealed plans cannot supply approval authority.
        approved = plan
        if approved is not None:
            result["approved_contract"] = approved
    design = design_coverage.projection(state)
    if design is not None:
        result["design"] = design
    if design_input_preview is not None:
        # Verified new-run dry-run input: never persisted as settings or model evidence.
        result["design_input_preview"] = deepcopy(design_input_preview)
    if state.get("task_preflight"):
        result["task_preflight"] = {
            key: deepcopy(state["task_preflight"].get(key))
            for key in (
                "kind",
                "execution_context",
                "execution_identity",
                "phase",
                "status",
                "checked_at",
                "manifest_hash",
                "binding",
                "errors",
                "receipt",
                "receipt_sha256",
                "checks",
                "design",
            )
        }
    result["conversation"] = conversation_projection.project(
        state,
        conversation_journal,
        displayed_plan=result.get("displayed_plan"),
        needs=need,
        public_status={
            key: result.get(key)
            for key in ("status", "done", "phase", "stop_reason", "next_command", "recovery", "liveness")
        },
        review_findings=result["evidence"].get("findings"),
        review_scope_moves=result["evidence"].get("finding_scope_moves"),
    )
    if isinstance(result["conversation"], dict) and result["conversation"].get("availability") == "available":
        result["conversation"]["run_dir"] = str(run_dir) if run_dir is not None else None
        result["conversation"]["workspace"] = str(workspace) if workspace is not None else None
    projection = progressive(state)
    if projection is not None:
        result["progressive"] = projection
    return result


def approved_contract(state: RunState) -> dict | None:
    """The plan in force: the approved contract, as long as that approval still holds.

    Approved by the user, or, for a bug fix's small correction, under the workflow policy
    the user agreed to (autocode_workflows.POLICY_ORIGINS); the view does not say which.
    None while there is no approval, after a new draft revision replaces the approved one,
    and while the contract itself records an open blocking question (approval refuses one);
    a question the run asks after approval does not remove it (the runner's own approval
    check, contract_identity.approved). Until a --follow-up drafts its own plan, and for a
    follow-up answered by a review, design or discussion, it is the earlier request's plan.
    A program coordinating several runs reads the child's approved criteria here, never
    state.json.

    ``goal_hash`` is the plan's goal hash, what `autocode plan --check FILE` prints for a file
    holding exactly this plan; ``plan_file`` names the plan file it was loaded from with
    --goal-file, or is None (autocode_plan_file_origin.citation, which never fails on a
    malformed record, so such a record cannot take the approved plan out of the view).
    """
    contract = state.get("goal_contract") or {}
    try:
        if not contract_identity.approved(state):
            return None
        approved = {
            "revision": contract["revision"],
            "hash": contract["hash"],
            "token": contract_identity.token(contract),
            "task_id": contract.get("task_id"),
            "approved_at": (contract.get("approval_event") or {}).get("at"),
            # "skip_to_build" when the person chose approval when starting the run, else "person" (#1731)
            "approval_source": (contract.get("approval_event") or {}).get("source") or "person",
            "body": deepcopy(contract["body"]),
            "goal_hash": plan_file.goal_hash(contract["body"]),
        }
    except (KeyError, TypeError, AttributeError, ValueError):
        return None
    approved["plan_file"] = plan_file_origin.citation(state)
    return approved


def progressive(state: RunState) -> dict | None:
    """Read-only, additive projection of the version-one progressive ledger.

    Plan/candidate envelopes contain {proposal, plan_hash}; active contains
    {definition, plan_hash, artifact, review}. The history list contains
    demonstrated checkpoints, never current product proof. budget, when
    present, is the budget policy ledger and is copied without invented limits.
    Status describes saved activation, not permission to dispatch: this reader
    does not authenticate artifact files, source snapshots or replay receipts.
    Until a current-proof reader is defined by the evidence owner, no saved
    boolean, prior PASS, empty future map or COMPLETE status establishes proof.
    """
    record = state.get("progressive")
    record = record if isinstance(record, dict) else {}
    contract = state.get("goal_contract") or {}
    body = contract.get("body") or {}
    disclosure = [
        line
        for field in ("constraints", "technical_approach")
        for line in body.get(field) or []
        if isinstance(line, str)
        and line.startswith(
            (
                progressive_rules.DISCLOSURE_DELEGATION,
                progressive_rules.DISCLOSURE_SLICE,
                progressive_rules.DISCLOSURE_OUTSTANDING,
            )
        )
    ]
    if not record and not disclosure:
        return None

    candidate = record.get("candidate")
    candidate = candidate if isinstance(candidate, dict) else {}
    plan = record.get("plan")
    plan = plan if isinstance(plan, dict) else {}
    proposal = candidate.get("proposal") or plan.get("proposal") or {}
    proposal = proposal if isinstance(proposal, dict) else {}
    active = record.get("active")
    active = active if isinstance(active, dict) else {}
    definition = active.get("definition")
    definition = definition if isinstance(definition, dict) else None
    approved = False
    reason = None
    try:
        progressive_rules.require_delegation(
            record,
            contract_token=contract_identity.token(contract),
            contract_body=body,
            contract_approved=contract_identity.approved(state),
            contract_sealed=contract_identity.sealed(contract),
        )
        approved = True
    except (ValueError, TypeError, KeyError, AttributeError) as error:
        reason = str(error)
    if record.get("suspended") or (record.get("delegation") and not approved):
        status = "suspended"
    elif not approved:
        status = "proposed" if candidate else "unapproved"
    elif (
        definition
        and definition.get("tentative") is False
        and active.get("plan_hash")
        and active.get("plan_hash") == plan.get("plan_hash")
        and definition == ((plan.get("proposal") or {}).get("slices") or [None])[0]
        and active.get("artifact")
        and active.get("review")
    ):
        status = "active"
    else:
        status = "needs_activation"

    slices = [row for row in proposal.get("slices") or [] if isinstance(row, dict)]
    criteria = [
        {"id": row.get("id"), "criterion": row.get("criterion") or row.get("text")}
        for row in body.get("acceptance_criteria") or []
        if isinstance(row, dict)
    ]
    result = {
        "status": status,
        "delegation_approved": approved,
        "authority_issue": reason,
        "disclosure": disclosure,
        "plan_hash": plan.get("plan_hash") or candidate.get("plan_hash"),
        "demonstrated_slices": record.get("history") or [],
        "checkpoints": record.get("history") or [],
        "retirements": record.get("retirements") or [],
        "active_slice": definition,
        "tentative_next_work": [row for row in slices if row.get("tentative") is True],
        "required_checks": record.get("required_checks") or [],
        # Every product criterion remains unproven here, including those mapped
        # to a green slice. The narrower map deferrals remain separately visible.
        "outstanding_product_criteria": criteria,
        "outstanding_criteria": record.get("outstanding_criteria", proposal.get("outstanding_criteria", [])),
        "current_whole_product_proof": {
            "verified": False,
            "status": "not_established",
            "reason": "Historical slice/checkpoint evidence is not authenticated current whole-product proof.",
        },
    }
    if "budget" in record:
        result["allowance_usage"] = record["budget"]
    return deepcopy(result)


def evidence_report(state: RunState):
    try:
        from .autocode_evidence_export import metadata
    except ImportError:
        from autocode_evidence_export import metadata
    return metadata(state)


def evidence(state: RunState) -> dict:
    """What the run agreed to deliver and what supports it, for reports outside the runner.

    outcome           the approved contract's intended outcome, or None
    base_commit       the revision the run started from
    acceptance        one row per criterion: its latest recorded outcome and evidence;
                      validator_status is the latest saved validation's result for that
                      criterion (FAIL, PASS or NOT_VERIFIED), None when that validation
                      has no row — a failed criterion must be distinguishable from an
                      unchecked one
    validator_source_revision  the source revision that validation checked, or None.
                      The view does not read the workspace: after rework, validator_status
                      still reports that validation until a newer one replaces it. Compare
                      this revision to the workspace before treating the status as current.
    findings          the findings ledger: id, status, severity, finding, source, blocking, evidence,
                      scope (sol: Tester, astra: Completion Reviewer, runner: a failed runner lifecycle
                      observation, #451, or, in a --package run, the files changed outside the package,
                      one blocking row naming each path, autocode_package_writes, #711)
    finding_scope_moves  present once an approved revision moved criteria that open findings cite:
                      one row per finding it re-attributed (autocode_finding_rescope): finding, from,
                      to (every resulting row's id, milestone_id, criteria), contract_token, at
    regression_proof  for bug fixes, the runner's own fail-before/pass-after proof, else None;
                      case_tests maps each English test case to the tests that prove it; unstable
                      lists the proving tests whose repeated runs disagreed, else None
    test_cases        a reproduced bug's regression tests in plain English (id, given, when, then), else []
    check_replay      the current validation's checks as the runner itself re-ran them in a clean copy
                      (autocode_check_replay): verdict, source_revision and one row per command, else None.
                      A planned check's row carries base_attribution when its failure was compared with
                      the base revision (autocode_base_attribution): kind base or change and the reason
    proof_stability   the proving tests' repeats (autocode_proof_stability): repeats, unstable (test, side,
                      kind, runs), unrepeated, next_step when one is unstable, note; else None
    diff_coverage     the proving tests' line coverage of changed product hunks (autocode_diff_coverage):
                      collected, tool, note, files, uncovered, unmeasured; else None. Report only.
    mutation_check    small mutations of the changed product lines run against the proving tests once the
                      proof's verdict was final and a pass (autocode_mutation_check): collected, note,
                      limit_per_file, budget_seconds, files (path, notes, mutants: line, operator, before,
                      after, outcome killed, survived, timeout or not_run, survived_tests) and, when
                      collected, survivors: keyed by the same test-case ids as regression_proof.case_tests
                      (a diagnosis's T1; a planned build's criterion id), each case's proving tests that passed with a product
                      line changed (test, path, line, operator, before, after, text), {} when none did. A
                      proof that did not pass, even one refused before any test ran, has the note "not run:
                      the proof did not pass"; None when a passing proof had no due test case, or there is
                      no proof. Report only: a survivor never changes the verdict or completion
    unverified_risk_claims  durability or concurrency promises in the person's own words that no runner
                      protocol proves (autocode_risk_disclosure): kind, source_id, quote, reason; [] when none.
                      Disclosure only: a PASS never verifies them
    """
    contract = (state.get("goal_contract") or {}).get("body") or {}
    criteria = contract.get("acceptance_criteria") or state.get("acceptance_criteria") or []
    decision = state.get("last_decision")
    if not isinstance(decision, dict):
        decision = {}
    report = decision.get("report")
    if not isinstance(report, dict):
        report = decision
    outcomes = {row.get("id"): row for row in report.get("acceptance_criteria") or [] if isinstance(row, dict)}
    reviewed = state.get("human_reviews")
    if not isinstance(reviewed, dict):
        reviewed = {}
    validation = state.get("validation")
    if not isinstance(validation, dict):
        validation = {}
    validated = {
        row.get("id"): row.get("status") for row in validation.get("criterion_results") or [] if isinstance(row, dict)
    }
    acceptance = []
    for item in criteria:
        item = item if isinstance(item, dict) else {"criterion": str(item)}
        outcome = outcomes.get(item.get("id")) or {}
        acceptance.append(
            {
                "id": item.get("id"),
                "criterion": item.get("criterion") or item.get("text"),
                "status": outcome.get("status"),
                "evidence": outcome.get("evidence"),
                "validator_status": validated.get(item.get("id")),
                "human_reviewed": item.get("id") in reviewed,
            }
        )
    proof = state.get("regression_proof")
    correction = test_roles.latest_correction(state.get("user_events"))
    replay = validation.get("check_replay")
    investigation = state.get("investigation")
    if not isinstance(investigation, dict):
        investigation = {}
    moves = finding_rescope.history(state.get("findings_ledger"))
    return {
        "created_at": state.get("created_at"),
        **({"interaction_timing": interaction_timing.project(state)} if interaction_timing.has_record(state) else {}),
        "outcome": contract.get("intended_outcome"),
        "workflow_result": workflow_result(state),
        "base_commit": state.get("base_commit"),
        "runner": deepcopy(state.get("runner")) if isinstance(state.get("runner"), dict) else None,
        **(
            {"protected_tests": deepcopy(state["settings"]["protected_tests"])}
            if state.get("settings", {}).get("protected_tests")
            else {}
        ),
        "acceptance": acceptance,
        "validator_source_revision": validation.get("source_revision"),
        "findings": [
            {
                **{
                    key: row.get(key)
                    for key in ("id", "status", "severity", "finding", "source", "blocking", "evidence")
                },
                **({"scope": deepcopy(row["scope"])} if "scope" in row else {}),
            }
            for row in state.get("findings_ledger") or []
            if isinstance(row, dict)
        ],
        **({"finding_scope_moves": moves} if moves else {}),
        "regression_proof": {
            **{
                key: proof.get(key)
                for key in (
                    "verdict",
                    "fail_to_pass",
                    "failures",
                    "unverified",
                    "commands",
                    "source_revision",
                    "case_tests",
                    "follow_up_baseline",
                    "unstable",
                    "changed_code_evidence",
                )
                if key != "changed_code_evidence" or proof.get(key) is not None
            },
            **({"role_correction": correction} if correction else {}),
        }
        if isinstance(proof, dict)
        else None,
        "test_cases": [
            {key: case.get(key) for key in ("id", "given", "when", "then")}
            for case in investigation.get("test_cases") or []
            if isinstance(case, dict)
        ]
        if investigation.get("outcome") == "reproduced"
        else [],
        "check_replay": {
            "protected_tests": deepcopy(replay.get("protected_tests")),
            "brief_acceptance": deepcopy(replay.get("brief_acceptance")),
            "risk_acceptance": deepcopy(replay.get("risk_acceptance")),
            "verdict": replay.get("verdict"),
            "source_revision": replay.get("source_revision"),
            "scheduling": deepcopy(replay.get("scheduling")),
            "checks": [
                {
                    key: deepcopy(row.get(key))
                    for key in (
                        "command",
                        "evidence_ref",
                        "exit_code",
                        "timed_out",
                        "output",
                        "output_sha256",
                        "duration_seconds",
                        "error",
                        "tail",
                        "purpose",
                        "scheduling",
                        "results",
                        "base_attribution",
                    )
                }
                for row in replay.get("checks") or []
                if isinstance(row, dict)
            ],
        }
        if isinstance(replay, dict)
        else None,
        "proof_stability": deepcopy(proof.get("proof_stability")) if isinstance(proof, dict) else None,
        "diff_coverage": deepcopy(diff_coverage.bounded(proof.get("diff_coverage")))
        if isinstance(proof, dict)
        else None,
        "mutation_check": deepcopy(
            proof.get("mutation_check")
            if isinstance(proof, dict)
            else (state.get("review") or {}).get("mutation_check")
            if (state.get("workflow") or {}).get("kind") == "review"
            else None
        ),
        "unverified_risk_claims": risk_disclosure.view(state),
    }


def workflow_result(state: RunState):
    """Bounded job outcome facts; job completion is separate from code checks."""
    kind = (state.get("workflow") or {}).get("kind")
    if kind == "review" and state.get("review"):
        row = state["review"]
        return {
            "outcome": row.get("verdict"),
            "artifact": row.get("report_path"),
            "artifact_sha256": row.get("report_sha256"),
            **({"source_revision": row["source_revision"]} if row.get("source_revision") else {}),
            "summary": f"{row.get('blocking', 0)} blocking, {row.get('advisory', 0)} advisory findings"
            + (
                f"; {row.get('pre_existing', 0)} pre-existing observations, "
                f"{row.get('inconclusive', 0)} blockers with inconclusive attribution"
                if row.get("pre_existing") or row.get("inconclusive")
                else ""
            )
            + (
                f"; submitted verdict: {row.get('submitted_verdict')}; {row['verdict_adjustment']}"
                if row.get("verdict_adjustment")
                else ""
            ),
            **(
                {
                    "review_attribution": {
                        key: deepcopy(row.get(key))
                        for key in ("pre_existing", "inconclusive", "submitted_verdict", "verdict_adjustment")
                    }
                }
                if "pre_existing" in row
                else {}
            ),
            **(
                {"mutation_check": deepcopy(row["mutation_check"])}
                if isinstance(row.get("mutation_check"), dict)
                else {}
            ),
        }
    if kind == "design" and (state.get("design_review") or {}).get("mode") == "review":
        row = state["design_review"]
        return {
            "outcome": row.get("verdict"),
            "artifact": row.get("report_path"),
            "artifact_sha256": row.get("report_sha256"),
            "summary": f"Design revision {row.get('revision')}; {row.get('blocking', 0)} blocking concerns",
        }
    if kind == "discuss" and state.get("answer"):
        row = state["answer"]
        return {
            "outcome": "answered",
            "artifact": row.get("note_path") or row.get("output"),
            "artifact_sha256": None,
            "summary": (
                "Requirements answered in the conversation"
                if row.get("answered_by") == "requirements_gather"
                else "Analyst answer recorded"  # saved by an earlier runtime's Analyst
            )
            + "; code correctness is not established by an answer",
        }
    return None


def _route(question: dict) -> dict:
    """``needs.route``: a role's model question after a quota stop or a content-filter refusal (#184, #463)."""
    route = {
        "question_id": question["id"],
        "role": question["route_role"],
        "job": question.get("job"),
        "current_model": question.get("current_model"),
        "engine": question.get("engine"),
        "cause": question.get("cause", "quota"),
        "stopped_model": question.get("stopped_model"),
    }
    if "candidates" in question:
        route["candidates"] = list(question["candidates"])
    if isinstance(question.get("lower_effort"), dict):
        # A time-limit stop on a model with a lighter declared variant (#1902): {model, from, to, answer}.
        route["lower_effort"] = dict(question["lower_effort"])
    return route


@status_observation.budgeted
def action(
    state: RunState,
    *,
    run_dir=None,
    workspace=None,
    stale_report_repair=False,
    human_projection=None,
    retained=_UNSET,
) -> dict:
    """What this run asks of a person: the need, its category, and the command that answers it.

    ``view`` composes those three from here, so a caller that only needs the next
    action never re-derives it; ``autocode runs`` pays for this, not for the view's
    evidence and usage accounting. Leaving ``stale_report_repair`` false keeps an
    uncertain attempt's abandon step in the need instead of promising a resume that
    would discard it. ``retained`` is the retained acceptance retry's answer
    (autocode_acceptance_retry_next.decide), computed here when the caller has none.
    """
    if retained is _UNSET:
        retained = status_observation.retained_retry(state, workspace or state.get("workspace"))
    try:
        from . import autocode_changeset_barrier as changeset_barrier
    except ImportError:
        import autocode_changeset_barrier as changeset_barrier
    human_projection = resolver_human.projection(state) if human_projection is None else human_projection
    display_state = resolver_human.decision_state(state, human_projection)
    terminal = stop.applied_stop(state) is not None
    if terminal:
        category = pause_category.category(state.get("status"), {"reason": stop.stop_reason(state)})
        return {
            "needs": None,
            "pause_category": category,
            "pause_category_label": pause_category.LABELS.get(category) if category else None,
            "next_command": None,
        }
    if operational_block.presentation(state, include_hold=True):
        return {"needs": None, "pause_category": "autocode", "pause_category_label": "AutoCode", "next_command": None}
    if changeset_barrier.held_approval(display_state):
        held_need = {
            "kind": "approve_plan",
            "token": contract_identity.token(state["goal_contract"]),
            "reason": changeset_barrier.refusal(state),
            "changeset_held": True,
        }
        return {
            "needs": held_need,
            "pause_category": "your_decision",
            "pause_category_label": "Your decision",
            "next_command": pause_command.command(
                held_need,
                for_client=True,
                run_dir=run_dir or state.get("run_dir"),
                workspace=workspace or state.get("workspace"),
            ),
        }
    status = display_state.get("status", "")
    need = needs(state, stale_report_repair=stale_report_repair, human_projection=human_projection, retained=retained)
    category = pause_category.category(status, need)
    # This status also names novelty/source holds; only its bound hold needs a larger limit.
    resume_flags = (
        None
        if status == "PAUSED_NO_PROGRESS" and not recovery_limits.no_progress_bound_holds(state)
        else operational_information.resume_flags(state, (need or {}).get("pause_origin") or status)
    )
    inputs: list | None = None if terminal or not category else command_inputs.project(state, need, resume_flags)
    # A bound row whose raise is not next_command names its own command (#2044). Pending information is
    # reviewed first, as for the attached offers, so it is offered once that review has held.
    separate = bool(inputs and "command" in inputs[0])
    if separate and (operational_information.projection(state) or {}).get("status") == "pending":
        inputs, separate = None, False
    token = recovery_view.token(state) if inputs else None
    if separate and inputs:
        named_raise = pause_command.command(
            {"kind": "resume", "action": command_inputs.raise_action(inputs[0])},
            for_client=True,
            run_dir=run_dir or state.get("run_dir"),
            workspace=workspace or state.get("workspace"),
            recovery_token=token,
        )
        inputs = [{**inputs[0], "command": named_raise}] if named_raise and token else None
    if inputs and need is not None:
        need = {**need, "command_inputs": inputs}
    # At a limit AutoResolver could not settle, only a larger total continues the run: a written reply can't
    # lift it (#2607). The raise is then the run's next action, not AutoResolver's information reply, which a
    # client's generic card would otherwise take (live run, 2026-11-18: the chat said a reply can't lift the
    # cost cap while `next_command` was that reply).
    raise_next = (
        inputs[0]["command"]
        if separate and inputs and need is not None and need.get("resolver_scope") == "operational_exhaustion"
        else None
    )
    if raise_next and need is not None and inputs:
        # The typed slot is then next_command's own, as for any named raise: the row names no second command.
        need = {**need, "command_inputs": [{key: value for key, value in inputs[0].items() if key != "command"}]}
    job_times = (
        job_time_view.offers(
            state, need, run_dir=run_dir or state.get("run_dir"), workspace=workspace or state.get("workspace")
        )
        if not terminal
        else []
    )
    if job_times and need is not None:  # offers() is empty without a need
        need = {**need, "job_time_controls": job_times}
    ready_boundary = (
        (need or {}).get("kind") == "continue"
        and state.get("active_stage") is None
        and state.get("active_runner_check") is None
    )
    if (
        need is not None
        and need.get("kind") == "retry_job"
        and isinstance(need.get("route"), dict)
        and need.get("job_retry_token")
    ):
        # The model question a stopped job keeps (#463) is named as a command too, not only in the reason's
        # prose: a client takes a typed model through `route.command`, the core issues a new retry token
        # for that model, and the retry on it is then `next_command` as usual (#2005).
        answer = f"--answer {need['route']['question_id']}=MODEL --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)"
        named = pause_command.command(
            {**need, "action": answer},
            for_client=True,
            run_dir=run_dir or state.get("run_dir"),
            workspace=workspace or state.get("workspace"),
            resume_flags=resume_flags,
            terminal=terminal,
        )
        need = {**need, "route": {**need["route"], "command": named}}
    return {
        "needs": need,
        "pause_category": category,
        "pause_category_label": pause_category.LABELS.get(category) if category else None,
        "next_command": raise_next
        or (
            None
            if not category and not ready_boundary
            else pause_command.command(
                need,
                for_client=True,
                run_dir=run_dir or state.get("run_dir"),
                workspace=workspace or state.get("workspace"),
                resume_flags=resume_flags,
                recovery_token=token if inputs and not separate else None,
                terminal=terminal,
            )
        ),
    }


def _person_facing(need: dict) -> dict:
    """``need`` with a person-only Resolver request's question text free of CLI machinery (#3145).

    The request AutoResolver saves carries operator advice (flags, a token's environment variable,
    `autocode resume`) after its sentence for the person; the saved request and ``stop_reason`` keep
    it for the terminal. A client shows these questions to a person and takes only the actions the
    view names, so the view's questions keep the person's sentences only."""
    if need.get("resolver_scope") in ("blocker", "operational_exhaustion"):
        need["questions"] = [person_text.question(row) for row in need.get("questions") or []]
    return need


@status_observation.budgeted
def needs(state: RunState, *, stale_report_repair=False, human_projection=None, retained=_UNSET) -> dict | None:
    """What must happen next, or None when complete, an internal failure offers no person action, or the
    Tester's retained report has no re-check left (autocode_acceptance_retry_next; ``retained`` is its answer).

    kind          what it asks for                  answered with
    review        human acceptance of criteria      --approve-review CRITERION --review-token - (reads AUTOCODE_REVIEW_TOKEN)
    answer        answers to pending questions      --answer QUESTION_ID=TEXT (plus --resolver-token
                                                     when the view carries one); with `route` set, a
                                                     role's quota ran out: --answer route-ROLE=MODEL;
                                                     with `resolver_scope` blocker or
                                                     operational_exhaustion and no `route`, AutoResolver
                                                     is asking a person and refuses --answer: `action`
                                                     is the response (--resolver-request ID
                                                     --resolver-token - (reads AUTOCODE_RESOLVER_TOKEN) --resolver-response
                                                     provide_information --resolver-message TEXT); a
                                                     decision that changes the approved contract then
                                                     takes --edit-goal body.json and --approve-goal; a
                                                     held repeated incident (pause_origin
                                                     PAUSED_NO_PROGRESS) also names `retry_action`,
                                                     --resume-paused --retry-failed-stage
    approve_plan  approval of the displayed plan    --approve-goal - (reads AUTOCODE_APPROVE_GOAL_TOKEN)
    planning_budget  more planning review calls     --feedback TEXT or --planning-review-call-limit N;
                                                     after AutoResolver held corrective information,
                                                     `action` is the control it requires
    resume        a person to inspect a pause       --resume-paused, after resolving stop_reason;
                                                     `new_run_required` instead preserves this run
                                                     and requires a fresh run with valid caller inputs;
                                                     `edit_required` instead prescribes --edit-goal FILE
                                                     and `feedback_action` when offered, then fresh approval;
                                                     `guidance`, when set, says in prose what the
                                                     alternatives do (the Tester's pause: resume
                                                     continues acceptance verification);
                                                     `action`, when set, is the one command that
                                                     continues (a source-only PAUSED_STALE_HANDOFF:
                                                     --resume-paused --accept-source-edit; a PAUSED_NO_PROGRESS its unchanged-
                                                     batch limit caused: --resume-paused --no-progress-
                                                     limit N, N above `no_progress_batches`, the
                                                     retained count, or 0; after AutoResolver held
                                                     corrective information, the control it requires,
                                                     view.information_review; at a parallel Builder
                                                     member's quota or content-filter stop answered
                                                     without a model, --resume-paused --retry-builder M;
                                                     a recovery novelty hold, also before its request is
                                                     published: --resume-paused --retry-failed-stage,
                                                     named again as `retry_action`; a PAUSED_REPEATED_FAILURE
                                                     whose active attempt, uncertain artifacts or pending
                                                     report repair its retry gate will not authorize over:
                                                     --resume-paused, with that CLI refusal as
                                                     `retry_unavailable` and never the refused flag (#1897))
    retry_job     a person to inspect a stopped job  --resume-paused --retry-failed-stage --job-retry-token
                                                     TOKEN; with `route` set (quota or a content-filter
                                                     refusal), --answer route-ROLE=MODEL --job-retry-token
                                                     TOKEN first names another model and issues a new token
                                                     (after a refusal, `action` is that answer until a
                                                     model is named)
    recover_source an investigation's own job    inspect archive and source before a new run;
                   missing original identity       every other job pause without it ends at resume
                                                     (a run saved at a stage this runtime no longer
                                                     runs is a `resume` with `new_run_required`)
    dependency    another run's accepted delivery   autocode wait --run-dir PRODUCER — returns when the
                                                     producer needs a person or ends; the delivery and
                                                     the resume are the dependency worker's, not a
                                                     person's command on this run
    continue      nothing; relaunch to proceed      the same command with --run-dir;
                                                     a core-classified uncertain attempt may name
                                                     --abandon-stage ATTEMPT as deterministic recovery
    """
    human_projection = resolver_human.projection(state) if human_projection is None else human_projection
    refreshing_decision = resolver_human.needs_publication(state, human_projection)
    status = state.get("status", "")
    if status == "PAUSED_SOURCE_UNAVAILABLE":
        # Only a fresh source/supervision observation in view() may name its
        # bound continuation. Missing source is machinery, never a person question.
        return None
    if (
        stop.applied_stop(state) is not None
        or status in COMPLETE
        or (operational_block.presentation(state, include_hold=True))
    ):
        return None
    if recovery_exhaustion.pending(state):
        # A spent automatic recovery allowance is AutoCode's to close, not a grant for a person:
        # the next resume records the run's outcome (autocode_recovery_exhaustion.settle).
        return {"kind": "continue", "action": "--resume-paused"}
    if verification_config.pending(state):
        # The Resolver's verification-command correction is AutoCode's to settle, not a command for a
        # person to type: the next resume drops the explicit commands or records the run's end.
        return {"kind": "continue", "action": "--resume-paused"}
    if retired := workflows.retired_stage(state):
        # Saved at a stage this runtime no longer runs (owner decision 2026-11-10): nothing resumes it.
        return {
            "kind": "resume",
            "reason": retired,
            "new_run_required": True,
            "edit_required": False,
            "action": "fresh_run",
            "feedback_action": None,
            "recovery_hint": retired,
        }
    if scope_pause.can_resume(state):
        return {
            "kind": "resume",
            "action": "--resume-paused",
            "confirmation_required": False,
            "reason": "Resume the boundary pause to restore its retained repository-scope question.",
        }
    if status == "PAUSED_COMPONENT_PLAN":
        return {
            "kind": "resume",
            "reason": state.get("stop_reason"),
            **component_plan.recovery(
                state.get("task", ""), (state.get("settings") or {}).get("regression", {}).get("test_root")
            ),
        }
    classification_need = classification_recovery.offer(state)
    if classification_need:
        return classification_need
    if classification_recovery.pending(state):
        # A spent classification is AutoCode's to close, not a plan edit for a person: the next
        # resume records the run's outcome (autocode_classification_recovery.conclude).
        return {"kind": "continue", "action": "--resume-paused"}
    failure = state.get("job_failure") or {}
    if job_end.pending(state):
        # A stopped job with nothing for a person to decide: the next resume retries it once or
        # records the run's end (autocode_job_failure.settle), never a retry button for a person.
        return {"kind": "continue", "action": "--resume-paused"}
    if quota_route.job_pause_current(state) and failure:
        known_source = bool(failure.get("source_identity"))
        need = {
            "kind": "retry_job" if known_source else "recover_source",
            "reason": (str(state.get("stop_reason") or "") + ". " + failure["reason"])
            if status in quota_route.JOB_PREFLIGHT_PAUSES
            else failure["reason"],
            "stage": failure["stage"],
            "attempt_id": failure["attempt_id"],
            "job_retry_token": failure["job_retry_token"],
            "archive": failure["archive"],
            "source_identity": failure["source_identity"],
            "write_diagnosis": deepcopy(failure["write_diagnosis"]),
            "unrestored": list(failure["unrestored"]),
            "action": "--resume-paused --retry-failed-stage --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)"
            if known_source
            else None,
            "recovery_hint": (
                "Exact retry rechecks the saved original source identity, including file modes and Git HEAD. "
                "Restore that exact source before retrying; the archived restoration diagnosis is retained."
                if known_source
                else "Exact retry is unavailable because this attempt has no saved original source identity. "
                "Inspect the archived attempt and current changes before starting a new run. "
                "The current checkout cannot establish the missing original identity."
            ),
        }
        if known_source:
            try:
                from . import autocode_job_retry_admission as retry_admission
            except ImportError:
                import autocode_job_retry_admission as retry_admission
            try:
                retry_admission.validate_retry(state, state.get("workspace"), failure.get("job_retry_token"))
            except (ValueError, OSError, KeyError, TypeError) as error:
                need.update(kind="recover_source", action=None, resume_unavailable=str(error))
        # A job stopped on quota or by its provider's content filter (#463) keeps its model question on
        # the failure: --answer route-ROLE=MODEL --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN) names another model and issues a
        # new token for the exact retry. Same shape as the answer need's ``route``.
        route = quota_route.job_question(state)
        if known_source and isinstance(route, dict):
            need["route"] = _route(route)
            # Until a person names another model (job_failure.route_assignment), the exact retry of a
            # refused job would replay the refused model: the next step is the answer. job_retry_token
            # stays, since the CLI still accepts it. A quota stop keeps the retry (the quota resets).
            if need["route"]["cause"] in (
                "content_filter",
                "output_limit",
                "authentication",
                "timeout",
            ) and not isinstance(failure.get("route_assignment"), dict):
                # A timeout whose model declares a lighter variant names that answer itself (#1902).
                value = (need["route"].get("lower_effort") or {}).get("answer") or "MODEL"
                need["action"] = (
                    f"--answer {need['route']['question_id']}={value} --job-retry-token - (reads AUTOCODE_JOB_RETRY_TOKEN)"
                )
        return need
    if status == "WAITING_FOR_DEPENDENCY":
        return {
            "kind": "dependency",
            "reason": state.get("stop_reason"),
            "producer_run": (state.get("dependency_wait") or {}).get("producer_run"),
            "producer_workspace": (state.get("dependency_wait") or {}).get("producer_workspace"),
        }
    if (
        recovery_progress.pending(state)
        or validation_recovery.pending(state)
        or no_progress_recovery.pending_legacy(state)
    ):
        return {
            "kind": "continue",
            "action": "--resume-paused",
        }
    # A proposal or expired publication has no answer authority. The writer must
    # re-evaluate and publish the current decision before a client offers it.
    if human_projection.get("status") == "RESOLVER_PENDING" and refreshing_decision:
        return {"kind": "continue", "action": "--no-chat"}
    # Recovery owners inspect the saved checkpoint above. Only decision display
    # uses this projection; it cannot erase retained evidence or authorize it.
    state = resolver_human.decision_state(state, human_projection)
    status = state.get("status", "")
    questions = state.get("pending_questions") or []
    reviews = [question for question in questions if question.get("review_criteria")]
    if reviews:
        return {
            "kind": "review",
            "criteria": list(reviews[0]["review_criteria"]),
            "token": reviews[0].get("review_token"),
            "question": reviews[0].get("question"),
        }
    request = state.get("user_request") or {}
    if request.get("kind") == "human_review":
        # A completion-time human review (autopilot.apply_review_result, after validation
        # passes with unreviewed criteria) is signaled directly on user_request with no
        # pending_questions entry at all; the token is the one already displayed for this
        # validated artifact.
        return {
            "kind": "review",
            "criteria": list(request.get("criteria") or []),
            "token": state.get("displayed_review"),
            "question": request.get("decision_needed"),
        }
    if questions:
        answer: dict = {
            "kind": "answer",
            "request_kind": request.get("kind"),
        }
        if request.get("kind") == "permission" and len(questions) == 1 and "operational_options" in questions[0]:
            answer["operational_options"] = deepcopy(questions[0]["operational_options"])
        # Answers to a published AutoResolver request must carry its token
        # (autocode.py: require --resolver-token). Surface the current one so
        # drivers can serve this gate from the view alone; a stale or consumed
        # request carries no token and the CLI re-verifies freshness anyway.
        # ``resolver_scope`` says what kind of request it is: "operational_exhaustion"
        # and "blocker" mean AutoResolver could not continue safely and is asking a
        # person, not asking a requirements question a default could answer.
        published = state.get("resolver_human_request")
        entry = ((state.get("resolver") or {}).get("human_escalations") or {}).get((published or {}).get("request_id"))
        if isinstance(published, dict) and isinstance(entry, dict) and entry.get("status") == "pending":
            answer["resolver_request_id"] = published.get("request_id")
            answer["resolver_token"] = published.get("request_token")
            answer["resolver_scope"] = published.get("scope")
            if answer["resolver_scope"] in ("blocker", "operational_exhaustion"):
                origin = ((entry.get("identity") or {}).get("proposal") or {}).get("origin") or {}
                answer["pause_origin"] = origin.get("pause_status")
                revision = published.get("request", {}).get("recommended_revision")
                if revision is not None:
                    answer["recommended_revision"] = deepcopy(revision)
                    answer["recommended_revision_response"] = recommendation.ACCEPTANCE
        # A quota stop or a content-filter refusal asks for a model (#184): answer --answer
        # route-ROLE=MODEL. It has no default and is a person's decision, never a delegable
        # requirements answer. ``cause`` is "quota", "content_filter", "output_limit" or
        # "cross_model"; ``stopped_model`` is the
        # model that stopped; ``candidates`` (refusal and cross-model) the configured models that would pass.
        route = next(
            (
                q
                for q in questions
                if q.get("category") == quota_route.CATEGORY and quota_route.asked_route(questions, q.get("id"))
            ),
            None,
        )
        answer["questions"] = [
            _question_view(
                question,
                request_kind=request.get("kind"),
                resolver_scope=answer.get("resolver_scope"),
                route=bool(route and question is route),
            )
            for question in questions
        ]
        if route:
            answer["route"] = _route(route)
        elif answer.get("resolver_scope") in ("blocker", "operational_exhaustion"):
            # Not a requirements question: the CLI refuses --answer here and takes the
            # operational response (#675). The route question above is the one exception.
            # A verification-command correction never reaches here: AutoCode settles it (pending above).
            answer["action"] = recovery_limits.RESPONSE_COMMAND
            # A recovery novelty hold also admits one explicit scoped retry after inspection (#1012).
            if answer.get("pause_origin") == "PAUSED_NO_PROGRESS" and (retry := recovery_limits.scoped_retry(state)):
                answer["retry_action"] = retry
        return _person_facing(answer)
    if status == "AWAITING_GOAL_APPROVAL":
        # The approval token is saved when the CLI displays the plan; until then, relaunch to display it.
        return (
            plan_only.describe_approval(state, {"kind": "approve_plan", "token": state["displayed_goal"]})
            if state.get("displayed_goal")
            else {"kind": "continue"}
        )
    if status == quota_route.CROSS_MODEL_STATUS:
        # Same model question as a quota stop, derived from the saved routes rather
        # than an attempt: name the verifier's next model so it stops grading its
        # own work. --answer route-ROLE=MODEL applies it; --resume-paused continues.
        asked = quota_route.cross_model_question(state)
        if asked:
            return {
                "kind": "answer",
                "reason": state.get("stop_reason") or status,
                "questions": [_question_view(asked, route=True)],
                "route": _route(asked),
            }
    if status == effort_question.STATUS:
        # A level a person named that the model does not declare (#2057): the question is derived
        # from the saved routes, its options are the declared levels, and --answer effort-ROLE=EFFORT
        # saves one; --resume-paused continues. Once answered, the plain resume below applies.
        asked = effort_question.question(state)
        if asked:
            return {
                "kind": "answer",
                "reason": state.get("stop_reason") or status,
                "questions": [_question_view(asked, effort=True)],
                "effort": effort_question.need(asked),
            }
    if status == "PAUSED_PERMISSION_LOOP":
        disposition = (state.get("resolver") or {}).get("human_disposition") or {}
        return {
            "kind": "resume",
            "reason": state.get("stop_reason"),
            "action": "--feedback 'WHAT CHANGED'",
            "permission_decisions": disposition.get("count"),
            "permission_decision_limit": disposition.get("limit"),
        }
    if status == "PAUSED_PLANNING_BUDGET":
        if acceptance_recovery.can_resume(state):
            return {
                "kind": "resume",
                "action": "--resume-paused",
                "reason": "The runner reopened this approved plan for unchanged tests delivered in the prior turn. "
                "Resume rechecks the tests and restores that exact approved plan, retaining all review charges "
                "and its saved allowance; no new plan is approved.",
            }
        if review_usage.can_resume(state):
            return {
                "kind": "resume",
                "action": "--resume-paused",
                "reason": "An inherited Plan Reviewer charge was counted twice. Resume rechecks the receipts and "
                "continues under the unchanged review limit; no plan approval is implied.",
            }
        need = {"kind": "planning_budget", "reason": state.get("stop_reason")}
        # AutoResolver evaluated corrective information and held: name the control it requires (#486).
        review = operational_information.projection(state) or {}
        if review.get("status") == "pending":
            # Information grants no allowance. Evaluate it before offering a
            # finite numeric choice, just as at the total-time checkpoint.
            need["action"] = "--resume-paused"
        elif command_inputs.has_planning_allowance(state):
            need["action"] = "--resume-paused"
        elif review.get("status") == "held" and review.get("action"):
            need["action"] = review["action"]
        return need
    if status.startswith(("PAUSED_", "BLOCKED_")) or status not in CONTINUE:
        native_need = recovery_view.native_cleanup_need(state)
        if native_need is not None:
            return native_need
        try:
            from . import autocode_protected_commands as protected_commands
        except ImportError:
            import autocode_protected_commands as protected_commands
        if protected_offer := protected_commands.offer(state):
            return protected_offer
        if retained is _UNSET:
            retained = status_observation.retained_retry(state, state.get("workspace"))
        if retry_next.exhausted_pause(state, retained):
            # The retained report is spent or cannot be authenticated: plain resume would only re-pause.
            # The recovery card preserves the evidence and explains the hold.
            return None
        need = {"kind": "resume", "reason": state.get("stop_reason") or status}
        if status == "PAUSED_STALE_HANDOFF":
            stale_checks = (state.get("resolution_request") or {}).get("stale_checks")
            if isinstance(stale_checks, list) and all(isinstance(name, str) for name in stale_checks):
                need["stale_checks"] = list(stale_checks)
        # An uncertain attempt must be set aside before a resume can continue (#340).
        active = state.get("active_stage") or {}
        if (
            status == "PAUSED_CONTROLLER_EXIT"
            and active.get("accounted") is True
            and type(active.get("exit_code")) is int
            and active["exit_code"] == 0
            and active.get("finished_at")
        ):
            # The owner projection freshly confirmed stopped supervision and a
            # retained report. Resume rechecks it; abandonment would discard it.
            need["action"] = "--resume-paused"
            return need
        # An inventory refusal leaves a retained result available for the usual
        # reconciliation checks. It is not authority to discard or replay it.
        if status != "PAUSED_SOURCE_UNAVAILABLE" and active.get("output") and isinstance(active.get("iteration"), int):
            try:
                from . import autocode_process as processes
            except ImportError:
                import autocode_process as processes
            if refusal := processes.stage_stop_refusal(active):
                need["resume_unavailable"] = refusal
                return need
            attempt = f"{active['iteration']:03d}/{Path(active['output']).stem}"
            need["abandon_stage"] = attempt
            need["action"] = f"--abandon-stage {attempt} then --resume-paused"
            held = operational_information.projection(state) or {}
            if (
                not stale_report_repair
                and not (active.get("finished_at") and active.get("exit_code") == 0)
                and not (held.get("status") == "held" and held.get("action"))
            ):
                # The core has classified this exact retained response as uncertain
                # and already requires it to be archived before any further work.
                # A cleanly finished attempt holds a result that archiving would
                # discard (a verification hold or stale source check stands on it),
                # and a held information review names a person's change (another
                # model after a content-filter refusal), so those pauses stay a resume.
                need["kind"] = "continue"
                return need
        if stale_report_repair:
            need["action"] = "--resume-paused"
            return need
        if status == "PAUSED_ACCEPTANCE_TESTS" and "action" not in need:
            # The named command is what a yes takes, so it is the resume. A completed writer's
            # execution-only failure can retain its report for fresh local verification (#3511).
            # A plan change is the person's words, offered as chat.input's feedback (#1869). Naming
            # `--feedback 'WHAT TO CHANGE'` here sent that placeholder to Requirements as the plan feedback
            # when a person answered "y" at the chat's Continue prompt (live Kilo run, 2026-11-12).
            need["action"] = "--resume-paused"
            need["guidance"] = (
                "Resume continues acceptance verification on the same plan. A retained report is reused "
                "when its plan, source and evidence still match; otherwise the Tester prepares fresh tests."
            )
        if "action" not in need and status == "PAUSED_TRANSPORT_CHANGED":
            try:
                immutable = role_transport.enabled(deepcopy(state.get("settings")))
            except (ValueError, TypeError, AttributeError):
                immutable = True  # A malformed binding cannot grant acceptance.
            if transport_offer := transport_recovery.offer(state, immutable_role_transports=immutable):
                need.update(transport_offer)
        if "action" not in need:
            # A source-only stale repair names the command that accepts the edit.
            # Other stale pauses do not.
            action = accepted_source.resume_action(state)
            if action:
                need["action"] = action
        if status == "PAUSED_NO_PROGRESS" and "action" not in need and recovery_limits.no_progress_bound_holds(state):
            # A plain resume holds here, also after a consumed response; only a bound
            # that admits the retained count acknowledges it (#448). Information pending
            # review cannot release this bound either, so this command comes first (#486).
            need["action"] = "--resume-paused --no-progress-limit N"
            need["no_progress_batches"] = state.get("no_progress_batches", 0)
        if status == planning_calls.STATUS and "action" not in need:
            # A plain resume holds here: only a cap above the calls counted, or 0, continues, as at the cost cap.
            need["action"] = f"--resume-paused {planning_calls.FLAG} N"
        # A recovery novelty hold before its request is published (RESOLVER_PENDING) re-holds on a
        # plain resume; its one admissible continuation is the explicit scoped retry (#1770).
        retry = recovery_limits.scoped_retry(state)
        if retry:
            need["retry_action"] = retry
            need.setdefault("action", retry)
        elif blocked := recovery_limits.scoped_retry_unavailable(state):
            # The hold admits no retry yet; say what must happen first instead of implying a resume will do.
            need["retry_unavailable"] = blocked
        if status == "PAUSED_REPEATED_FAILURE" and not recovery_limits.repeated_failure_retry_refusal(state):
            try:
                from . import autocode_failure_retry_admission as failure_retry_admission
            except ImportError:
                import autocode_failure_retry_admission as failure_retry_admission
            try:
                failure_retry_admission.failure_retry_frontier(state, state.get("workspace"))
            except (ValueError, OSError, KeyError, TypeError) as error:
                need["retry_unavailable"] = str(error)
                need.pop("retry_action", None)
                if need.get("action") == retry:
                    need.pop("action", None)
        # A parallel member's model stop whose request a person answered continues only by its own
        # control (#541): --resume-paused --retry-builder M.
        member = member_stop.action(state) if "action" not in need else None
        if member:
            need["action"] = member
        # AutoResolver evaluated corrective information and held: name the control it requires (#486).
        review = operational_information.projection(state) or {}
        if review.get("status") == "pending" and "action" not in need:
            need["action"] = "--resume-paused"
        elif review.get("action") and (review["status"] == "held" or "action" not in need):
            need["action"] = review["action"]
        pending = state.get("pending_report_repair") or {}
        rejected = report_retry.rejected_attempt(state) or {}
        resolved_already = bool(state.get("resolve_repair_request")) or any(
            isinstance(row, dict)
            and row.get("kind") == "report_repair"
            and row.get("original_output") == (pending.get("original") or {}).get("output")
            for row in state.get("resolve_repair_history") or ()
        )
        if (
            status == "PAUSED_REPEATED_FAILURE"
            and not resolved_already
            and (pending.get("original") or {}).get("stage") == "tester"
            and not any(state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts"))
            and report_retry.retryable(pending.get("error"))
            and report_retry.bounded_failure(
                state, (state.get("settings") or {}).get("report_repair", {}).get("max_attempts", 0)
            )
            and isinstance(rejected.get("iteration"), int)
            and rejected.get("output")
        ):
            selected = f"{rejected['iteration']:03d}/{Path(rejected['output']).stem}"
            try:
                report_retry_admission.validate(state, state.get("workspace"), selected)
            except (OSError, RuntimeError, ValueError, TypeError, KeyError):
                pass
            else:
                need["retry_report_attempt"] = selected
                need["action"] = f"--resume-paused --retry-report {selected}"
        # The CLI refuses --retry-failed-stage while the attempt or report repair behind this pause is
        # unreconciled, so naming it here would be a command the person is then rejected for (#1897).
        # Say why the retry waits, as a novelty hold does, and name the resume the CLI does take.
        if (
            status == "PAUSED_REPEATED_FAILURE"
            and "action" not in need
            and (blocked := recovery_limits.repeated_failure_retry_refusal(state))
        ):
            need["retry_unavailable"] = blocked
            need["action"] = "--resume-paused"
        if status == "PAUSED_BUILDER_RETRY_LIMIT" and "action" not in need:
            # The same stopped lane and approval checks that guard the executor also
            # decide whether the view may name its retry. A later launch rechecks them.
            import shlex

            try:
                from . import autocode_builder_recovery as builder_recovery
                from . import autocode_legacy_process as legacy_process
                from . import autocode_util as util
            except ImportError:
                import autocode_builder_recovery as builder_recovery
                import autocode_legacy_process as legacy_process
                import autocode_util as util

            task = state.get("current_task") or {}
            members = task.get("milestone_ids") or [task.get("milestone_id") or task.get("id")]
            try:
                goals.execution_guard(state)
                builder_recovery.serial_retry_frontier(state, members)
                if any(not isinstance(mid, str) or not mid for mid in members):
                    raise ValueError("The saved milestone identities are unavailable")
                if not state.get("run_dir") or not state.get("workspace"):
                    raise ValueError("The saved run or workspace location is unavailable")
                legacy_process.assert_no_legacy_process(Path(state["run_dir"]), Path(state["workspace"]))
            except (util.Paused, ValueError, KeyError, TypeError, OSError) as error:
                need["resume_unavailable"] = str(error)
            else:
                need["action"] = shlex.join(
                    ["--resume-paused", *(part for mid in members for part in ("--retry-builder", mid))]
                )
        if (
            status == "PAUSED_RESOLVER"
            and "action" not in need
            and resolver_human.held_response_scope(state) == "blocker"
            and resolver_human.response_holds_current_frontier(state)
            and not operational_information.retired(state)
        ):
            need["resume_unavailable"] = (
                "The saved blocker remains unresolved. A plain resume cannot continue at this same frontier."
            )
        if "action" not in need and pause_authority.requested_only(state):
            need["confirmation_required"] = False
        action = need.get("action")
        if state.get("next_stage") == "orchestrator" and isinstance(action, str):
            words = action.split()
            selected_members = [words[i + 1] for i, word in enumerate(words[:-1]) if word == "--retry-builder"]
            if selected_members and not set(selected_members) <= set(recovery_view._parallel_members(state)):
                need.pop("action", None)
                need["parallel_retry_unavailable"] = (
                    "The selected Builder retry is no longer admissible. Review the available member actions."
                )
        return need
    return {"kind": "continue"}
