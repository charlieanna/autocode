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
    from . import autocode_interaction_timing as interaction_timing
except ImportError:
    import autocode_acceptance_recovery as acceptance_recovery
    import autocode_interaction_timing as interaction_timing

try:
    from . import autocode_acceptance_tests as acceptance_tests
    from . import autocode_accepted_source as accepted_source
    from . import autocode_adaptive_planning as adaptive_planning
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
    from . import autocode_diagnosis_refresh as diagnosis_refresh
    from . import autocode_efficiency, autocode_usage
    from . import autocode_effort_question as effort_question
    from . import autocode_escalation_view as escalation_view
    from . import autocode_finding_rescope as finding_rescope
    from . import autocode_goals as goals
    from . import autocode_liveness as liveness_policy
    from . import autocode_member_stop as member_stop
    from . import autocode_operational_information as operational_information
    from . import autocode_output_policy as output_policy
    from . import autocode_package_scope as package_scope
    from . import autocode_pause_authority as pause_authority
    from . import autocode_pause_category as pause_category
    from . import autocode_pause_command as pause_command
    from . import autocode_plan_file as plan_file
    from . import autocode_plan_file_origin as plan_file_origin
    from . import autocode_plan_retention as plan_retention
    from . import autocode_planning_review_usage as review_usage
    from . import autocode_progressive_plan as progressive_rules
    from . import autocode_quota_route as quota_route
    from . import autocode_recovery_limits as recovery_limits
    from . import autocode_recovery_progress as recovery_progress
    from . import autocode_recovery_view as recovery_view
    from . import autocode_report_retry as report_retry
    from . import autocode_request_usage as request_usage
    from . import autocode_resolver_recommendation as recommendation
    from . import autocode_risk_disclosure as risk_disclosure
    from . import autocode_role_transport as role_transport
    from . import autocode_route_ladder as route_ladder
    from . import autocode_stage_policy as stage_policy
    from . import autocode_stop_explanations as stop_explanations
    from . import autocode_task_input as task_input
    from . import autocode_tool_containment as tool_containment
    from . import autocode_trace_coverage as trace_coverage
    from . import autocode_transport_recovery as transport_recovery
    from . import autocode_verification_view as verification_view
except ImportError:
    import autocode_acceptance_tests as acceptance_tests
    import autocode_accepted_source as accepted_source
    import autocode_adaptive_planning as adaptive_planning
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
    import autocode_diagnosis_refresh as diagnosis_refresh
    import autocode_efficiency
    import autocode_effort_question as effort_question
    import autocode_escalation_view as escalation_view
    import autocode_finding_rescope as finding_rescope
    import autocode_goals as goals
    import autocode_liveness as liveness_policy
    import autocode_member_stop as member_stop
    import autocode_operational_information as operational_information
    import autocode_output_policy as output_policy
    import autocode_package_scope as package_scope
    import autocode_pause_authority as pause_authority
    import autocode_pause_category as pause_category
    import autocode_pause_command as pause_command
    import autocode_plan_file as plan_file
    import autocode_plan_file_origin as plan_file_origin
    import autocode_plan_retention as plan_retention
    import autocode_planning_review_usage as review_usage
    import autocode_progressive_plan as progressive_rules
    import autocode_quota_route as quota_route
    import autocode_recovery_limits as recovery_limits
    import autocode_recovery_progress as recovery_progress
    import autocode_recovery_view as recovery_view
    import autocode_report_retry as report_retry
    import autocode_request_usage as request_usage
    import autocode_resolver_recommendation as recommendation
    import autocode_risk_disclosure as risk_disclosure
    import autocode_role_transport as role_transport
    import autocode_route_ladder as route_ladder
    import autocode_stage_policy as stage_policy
    import autocode_stop_explanations as stop_explanations
    import autocode_task_input as task_input
    import autocode_tool_containment as tool_containment
    import autocode_trace_coverage as trace_coverage
    import autocode_transport_recovery as transport_recovery
    import autocode_usage
    import autocode_verification_view as verification_view

SCHEMA = 2
COMPLETE = ("TASK_COMPLETE", "COMPLETE")
# Statuses where relaunching the run, with no user input, continues the work.
CONTINUE = ("RUNNING", "DISCOVERING", "WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL")
QUESTION_FIELDS = ("id", "question", "why", "options", "proposed_default")


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


def view(
    state: RunState,
    *,
    completion_current=None,
    visual_acceptance=None,
    stale_report_repair=False,
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
) -> dict:
    """Caller supplies fresh completion, evidence, repair, supervision and design-input inspections."""
    try:
        from . import autocode_attention as attention
        from . import autocode_changeset_store as changeset_store
        from . import autocode_coordination_store as coordination_store
        from . import autocode_related_profile as related_profile
        from . import autocode_replay_view as replay_view
        from . import autocode_scope_question as scope_question
        from . import autocode_worker_view as worker_view
    except ImportError:
        import autocode_attention as attention
        import autocode_changeset_store as changeset_store
        import autocode_coordination_store as coordination_store
        import autocode_related_profile as related_profile
        import autocode_replay_view as replay_view
        import autocode_scope_question as scope_question
        import autocode_worker_view as worker_view
    replay = replay_view.completion_policy(state)
    replay_progress, invalid_replay, simulated = replay["progress"], replay["invalid"], replay["simulated"]
    if simulated or invalid_replay:
        completion_current = False
    status = state.get("status", "")
    next_action = action(state, run_dir=run_dir, workspace=workspace, stale_report_repair=stale_report_repair)
    try:
        from . import autocode_checkpoint_reconciliation as reconciliation
    except ImportError:
        import autocode_checkpoint_reconciliation as reconciliation
    reconcile = reconciliation.offer(state, liveness, controller=controller)
    if reconcile is not None:
        next_action = {
            "needs": reconcile,
            "pause_category": None,
            "pause_category_label": None,
            "next_command": pause_command.command(
                {**reconcile, "kind": "resume"},
                run_dir=run_dir or state.get("run_dir"),
                workspace=workspace or state.get("workspace"),
            ),
        }
    need = next_action["needs"]
    task = state.get("current_task") or {}
    active = state.get("active_stage")
    supervision = active.get("supervision") if isinstance(active, dict) else None
    check = state.get("active_runner_check")
    check_supervision = check.get("supervision") if isinstance(check, dict) else None
    # Saved stage records, then the running stage, so the running one is read first; a corrupt list reads empty.
    stages = state.get("stages")
    launches = [*(stages if isinstance(stages, list) else []), active]
    plan = approved_contract(state)
    result = {
        "replay_source": None if invalid_replay else replay_view.project(state, run_dir=run_dir),
        "replay_progress": replay_progress,
        "replay_result": deepcopy(state.get("replay_result")),
        "runner_check": {
            **{
                key: deepcopy(check.get(key))
                for key in ("stage", "summary", "started_at", "updated_at", "command", "output", "supervision")
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
        "next_command": next_action["next_command"],
        "liveness": liveness_policy.classify(supervision, liveness),
        "done": status in COMPLETE and not simulated and not invalid_replay,
        "needs": need,
        "recovery": recovery_view.project(state, need),
        "verification": verification_view.project(state, accepted_human_ids=accepted_reviews(state)),
        "review_result": None,  # status may add a read-time inspection; evidence remains authenticated recorded facts
        "code_checkpoints": code_checkpoints.project(state, run_dir=run_dir, workspace=workspace),
        # Which saved checkpoints built each approved criterion's milestones (#730): attribution, not proof.
        "changes_by_criterion": criterion_changes.attribute((plan or {}).get("body"), state.get("code_checkpoints")),
        # The run's development server as the caller inspected it (autocode_dev_server.inspect, #730).
        "dev_server": deepcopy(dev_server),
        "phase": state.get("phase"),
        "next_stage": state.get("next_stage"),
        "iteration": state.get("iteration"),
        "stop_reason": state.get("stop_reason"),
        # Plain-English explanation of this stop (#715), added never renamed.
        "explanation": stop_explanations.explain(
            status,
            stop_reason=state.get("stop_reason") or "",
            needs=need,
            held_cause=pause_authority.answered_cause(state),
            ready=(controller or {}).get("kind") == "ready",
        ),
        "current_task": {key: task.get(key) for key in ("id", "objective", "milestone_id")} if task else None,
        # The kind of job recognized from the request (autocode_workflows.WORKFLOWS);
        # None until the first stage has run, and for runs that predate recognition.
        "workflow": (state.get("workflow") or {}).get("kind"),
        # "model" when the recognizer decided it, "user" when --workflow named it; the reason it gave.
        "workflow_source": (state.get("workflow") or {}).get("source"),
        "workflow_reason": (state.get("workflow") or {}).get("reason"),
        # How much planning the run chose and why (autocode_adaptive_planning.view, #697): whether recognition
        # skipped Requirements as clear, the plan's size, the review allowance and an early approval.
        "planning_depth": adaptive_planning.view(state),
        # Which planning path a bug-fix run took and why (autocode_stage_policy.view, #2021): the compact path's
        # omitted Planner draft and what covers it, or the full path's reasons; null when the run never decided.
        "stage_policy": stage_policy.view(state),
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
        # Tokens and cost so far, by role (autocode_usage.summary): reported, estimated and unknown kept apart.
        "usage": autocode_usage.summary(state),
        # Spend against the person's money cap at the dated price table (autocode_cost, #721).
        "cost": cost.view(state),
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
        "route_assignments": quota_route.assignments(state),
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
        # Status inspection supplies a fresh offer; saved state alone grants no adoption.
        "job_report_recovery": None,
        # The pause a client may request while the run works (autocode_pause_offer, #1598); status supplies it.
        "pause": None,
    }
    # Work that belongs to another repository, and what the person left out of this run (#2066).
    result["scope"] = scope_question.view(
        state, (state.get("requirements_handoff") or {}).get("dropped_external_parts")
    )
    result["related_profile"] = related_profile.project(state)
    result["coordination"] = (
        coordination_store.offer(
            run_dir,
            eligible=result["workflow"] == "bugfix"
            and not (state.get("settings") or {}).get("coordination_member")
            and result["scope"]["spans_repositories"]
            and not result["scope"]["decision"],
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
        except (KeyError, TypeError, ValueError):
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
                      (autocode_check_replay): verdict, source_revision and one row per command, else None
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
    replay = validation.get("check_replay")
    investigation = state.get("investigation")
    if not isinstance(investigation, dict):
        investigation = {}
    moves = finding_rescope.history(state.get("findings_ledger"))
    return {
        "created_at": state.get("created_at"),
        **({"interaction_timing": interaction_timing.project(state)} if state.get("interaction_timing") else {}),
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
                    )
                }
                for row in replay.get("checks") or []
                if isinstance(row, dict)
            ],
        }
        if isinstance(replay, dict)
        else None,
        "proof_stability": deepcopy(proof.get("proof_stability")) if isinstance(proof, dict) else None,
        "diff_coverage": deepcopy(proof.get("diff_coverage")) if isinstance(proof, dict) else None,
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
            "summary": "Analyst answer recorded; code correctness is not established by an answer",
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


def action(state: RunState, *, run_dir=None, workspace=None, stale_report_repair=False) -> dict:
    """What this run asks of a person: the need, its category, and the command that answers it.

    ``view`` composes those three from here, so a caller that only needs the next
    action never re-derives it; ``autocode runs`` pays for this, not for the view's
    evidence and usage accounting. Leaving ``stale_report_repair`` false keeps an
    uncertain attempt's abandon step in the need instead of promising a resume that
    would discard it.
    """
    try:
        from . import autocode_changeset_barrier as changeset_barrier
    except ImportError:
        import autocode_changeset_barrier as changeset_barrier
    if changeset_barrier.held_approval(state):
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
                held_need, run_dir=run_dir or state.get("run_dir"), workspace=workspace or state.get("workspace")
            ),
        }
    status = state.get("status", "")
    need = needs(state, stale_report_repair=stale_report_repair)
    category = pause_category.category(status, need)
    terminal = any(row.get("kind") == "stop" for row in state.get("applied_interventions", []) if isinstance(row, dict))
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
            run_dir=run_dir or state.get("run_dir"),
            workspace=workspace or state.get("workspace"),
            recovery_token=token,
        )
        inputs = [{**inputs[0], "command": named_raise}] if named_raise and token else None
    if inputs and need is not None:
        need = {**need, "command_inputs": inputs}
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
        "next_command": None
        if not category and not ready_boundary
        else pause_command.command(
            need,
            run_dir=run_dir or state.get("run_dir"),
            workspace=workspace or state.get("workspace"),
            resume_flags=resume_flags,
            recovery_token=token if inputs and not separate else None,
            terminal=terminal,
        ),
    }


def needs(state: RunState, *, stale_report_repair=False) -> dict | None:
    """What must happen next for the run to progress, or None when it is complete.

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
                                                     alternatives do (the Tester's pause: --feedback
                                                     revises the plan, a resume reruns the Tester);
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
    recover_source missing original identity       inspect archive and source before a new run
    dependency    another run's accepted delivery   autocode wait --run-dir PRODUCER — returns when the
                                                     producer needs a person or ends; the delivery and
                                                     the resume are the dependency worker's, not a
                                                     person's command on this run
    continue      nothing; relaunch to proceed      the same command with --run-dir;
                                                     a core-classified uncertain attempt may name
                                                     --abandon-stage ATTEMPT as deterministic recovery
    """
    status = state.get("status", "")
    if status in COMPLETE:
        return None
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
    if (
        status == "PAUSED_BUILDER_CLASSIFICATION"
        and not (state.get("builder_failure_hold") or {}).get("classification_retry")
        and "(The Investigator could not finish:" in (state.get("stop_reason") or "")
    ):
        return {
            "kind": "resume",
            "reason": state.get("stop_reason"),
            "edit_required": True,
            "action": "--edit-goal FILE",
            "feedback_action": None,
            "recovery_hint": "This older checkpoint did not retain its failed classification request. Review and change the approved plan, or start a new run; its discarded Resolver proposal cannot be reconstructed by resume.",
        }
    failure = state.get("job_failure") or {}
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
    issued = state.get("resolver_human_request")
    if recovery_progress.routine_resume(state, issued):
        return {
            "kind": "continue",
            "action": "--resume-paused",
        }
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
        answer = {
            "kind": "answer",
            "request_kind": request.get("kind"),
            "questions": [{field: question.get(field) for field in QUESTION_FIELDS} for question in questions],
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
        if route:
            answer["route"] = _route(route)
        elif answer.get("resolver_scope") in ("blocker", "operational_exhaustion"):
            # Not a requirements question: the CLI refuses --answer here and takes the
            # operational response (#675). The route question above is the one exception.
            answer["action"] = recovery_limits.RESPONSE_COMMAND
            # A recovery novelty hold also admits one explicit scoped retry after inspection (#1012).
            if answer.get("pause_origin") == "PAUSED_NO_PROGRESS" and (retry := recovery_limits.scoped_retry(state)):
                answer["retry_action"] = retry
        return answer
    if status == "AWAITING_GOAL_APPROVAL":
        if (state.get("goal_contract") or {}).get("approval_status") == "draft" and diagnosis_refresh.conflicts(state):
            return {
                "kind": "resume",
                "action": "--resume-paused",
                "reason": "Refresh the conflicting accepted diagnosis before plan approval",
            }
        # The approval token is saved when the CLI displays the plan; until then, relaunch to display it.
        return (
            {"kind": "approve_plan", "token": state["displayed_goal"]}
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
                "questions": [{field: asked.get(field) for field in QUESTION_FIELDS}],
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
                "questions": [{field: asked.get(field) for field in QUESTION_FIELDS}],
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
        need = {"kind": "resume", "reason": state.get("stop_reason") or status}
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
            attempt = f"{active['iteration']:03d}/{Path(active['output']).stem}"
            need["abandon_stage"] = attempt
            need["action"] = f"--abandon-stage {attempt} then --resume-paused"
            if not stale_report_repair:
                # The core has classified this exact retained response as uncertain
                # and already requires it to be archived before any further work.
                need["kind"] = "continue"
                return need
        if stale_report_repair:
            need["action"] = "--resume-paused"
            return need
        if status == "PAUSED_ACCEPTANCE_TESTS" and "action" not in need:
            # The command revises the plan; a resume reruns the same Tester on the same plan (#1869).
            need["action"] = "--feedback 'WHAT TO CHANGE'"
            need["guidance"] = (
                "--feedback MESSAGE revises the plan; --resume-paused reruns the Tester unchanged, "
                "which helps only after you changed the code."
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
        # A recovery novelty hold before its request is published (RESOLVER_PENDING) re-holds on a
        # plain resume; its one admissible continuation is the explicit scoped retry (#1770).
        retry = recovery_limits.scoped_retry(state)
        if retry:
            need["retry_action"] = retry
            need.setdefault("action", retry)
        elif blocked := recovery_limits.scoped_retry_unavailable(state):
            # The hold admits no retry yet; say what must happen first instead of implying a resume will do.
            need["retry_unavailable"] = blocked
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
        if (
            status == "PAUSED_REPEATED_FAILURE"
            and (pending.get("original") or {}).get("stage") == "sol"
            and not any(state.get(key) for key in ("active_stage", "active_runner_check", "uncertain_artifacts"))
            and pending.get("error") in report_retry.RETRYABLE_ERRORS
            and report_retry.bounded_failure(
                state, (state.get("settings") or {}).get("report_repair", {}).get("max_attempts", 0)
            )
            and isinstance(rejected.get("iteration"), int)
            and rejected.get("output")
        ):
            need["retry_report_attempt"] = f"{rejected['iteration']:03d}/{Path(rejected['output']).stem}"
            need["action"] = f"--resume-paused --retry-report {need['retry_report_attempt']}"
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
        if "action" not in need and pause_authority.requested_only(state):
            need["confirmation_required"] = False
        return need
    return {"kind": "continue"}
