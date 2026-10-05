"""Autopilot: deterministic controller for planning, building, review and repair."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
try:
    from . import autocode_support as support, autocode_completion as completion_gate, autocode_goals as goals, autocode_goal_lifecycle as lifecycle, autocode_jobs as jobs
    from . import autocode_stuck_job as stuck
    from . import autocode_planning_artifacts as planning_artifacts, autocode_planning_graph as planning_graph
    from . import autocode_workflow as workflow, autocode_milestones as milestones, autocode_escalation as escalation
    from . import autocode_findings as findings_ledger, autocode_builder_policy as builder_policy
    from . import autocode_resolver_human as human, autocode_failures as failures, autocode_assignment as assignment, autocode_status
    from . import autocode_retained_work as retained_work, autocode_provider_recovery as provider_recovery, autocode_rework_policy as rework_policy, autocode_resolver_recovery as resolver_recovery
    from . import autocode_planning_clarification as clarification
    from . import autocode_progressive_state as progressive_state, autocode_design_coverage as design_coverage, autocode_efficiency as efficiency, autocode_visual_runtime as visual_runtime
    from .units import autoplanner as planning_unit, common as units_common
    from . import autocode_regression as regression, autocode_verify as verify, autocode_check_replay as check_replay, autocode_check_refs as check_refs
    from . import autocode_validation_rounds as validation_rounds
except ImportError:
    import autocode_regression as regression, autocode_verify as verify, autocode_check_replay as check_replay, autocode_check_refs as check_refs
    import autocode_validation_rounds as validation_rounds
    import autocode_support as support, autocode_completion as completion_gate, autocode_jobs as jobs
    import autocode_stuck_job as stuck, autocode_goals as goals, autocode_goal_lifecycle as lifecycle
    import autocode_planning_artifacts as planning_artifacts, autocode_planning_graph as planning_graph
    import autocode_workflow as workflow
    import autocode_milestones as milestones
    import autocode_escalation as escalation
    import autocode_findings as findings_ledger
    import autocode_builder_policy as builder_policy
    import autocode_resolver_human as human
    import autocode_failures as failures, autocode_assignment as assignment, autocode_status
    import autocode_retained_work as retained_work, autocode_provider_recovery as provider_recovery, autocode_rework_policy as rework_policy, autocode_resolver_recovery as resolver_recovery
    import autocode_planning_clarification as clarification
    import autocode_progressive_state as progressive_state, autocode_design_coverage as design_coverage, autocode_efficiency as efficiency, autocode_visual_runtime as visual_runtime
    from units import autoplanner as planning_unit, common as units_common

SKIP = object()


class LoopExit(Exception):
    """Stop orchestration at a deliberate CLI boundary."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def drive(state, dispatch, *, apply=None, before=None, after=None, persist=None,
          active=lambda value: value.get('status') == 'RUNNING', investigate=False):
    """Run the saved state's next stage until its workflow reaches a boundary.

    Units own prompts and schemas; Autopilot owns code-workflow transitions. The loop itself
    (guard -> select saved stage -> dispatch -> apply -> persist) is autocode_stuck_job.drive;
    SKIP from a hook restarts from the newly saved state without pretending a stage completed.
    With ``investigate``, a stage that stops converging goes to the Investigator before a pause.
    """
    return stuck.drive(state, dispatch, apply=apply, before=before, after=after, persist=persist,
                       active=active, skip=SKIP, paused=support.Paused, investigate=investigate)


def prepare_request(state, stage, state_path, schema_dir):
    """A unit's model request for ``stage``, carrying the Investigator's guidance when in force."""
    return stuck.with_guidance(state, stage, unit_module(stage).prepare(state, stage, state_path, schema_dir))

UNITS = ("autoplanner", "autocode", "autoreview", "autoresolver")


def unit_for(stage):
    if stage in jobs.UNIT:
        return jobs.UNIT[stage]
    if stage in ("astra_resolve", "astra_diagnose"):
        return "autoresolver"
    if stage in (planning_unit.RECOGNIZE, "requirements_gather", "astra_discovery", "astra_challenge", "glm_revise",
                 "astra_finalize", "requirements", "plan", "plan_review", "plan_revise", "plan_finalize"):
        return "autoplanner"
    if stage in ("astra_plan", "orchestrator", "terra"):
        return "autocode"
    if stage in ("sol", "astra_review", "astra_checkpoint"):
        return "autoreview"
    raise ValueError(f"No unit owns stage {stage!r}")


def unit_module(stage):
    try:
        from .units import autoplanner, autocode, autoreview, autoresolver
    except ImportError:
        from units import autoplanner, autocode, autoreview, autoresolver
    return dict(zip(UNITS, (autoplanner, autocode, autoreview, autoresolver)))[unit_for(stage)]


def pending_unit(state):
    pending = state.get("pending_report_repair") or {}
    stage = (pending.get("original") or {}).get("stage") or state.get("next_stage")
    return unit_for(stage) if stage else None


def publish_handoffs(state, run_dir):
    """Export versioned unit outputs; saved state and checked evidence stay authoritative."""
    outputs = {}
    if goals.approved(state):
        contract = state["goal_contract"]
        common = {"version": 1, "contract_hash": contract["hash"], "contract_revision": contract["revision"]}
        outputs["autoplanner"] = {**common, "kind": "approved-plan", "contract": contract,
                                  "approval_token": goals.token(contract)}
        implementation = state.get("implementation") or {}
        if implementation.get("contract_hash") == contract["hash"] and implementation.get("source_revision"):
            outputs["autocode"] = {**common, "kind": "build-candidate", "implementation": implementation,
                                   "source_revision": implementation["source_revision"],
                                   "task_id": implementation.get("task_id"), "diff_ref": state.get("diff_ref")}
        validation = state.get("validation") or {}
        repair = state.get("repair_plan") or {}
        if repair.get("contract_hash") == contract["hash"]:
            outputs["autoresolver"] = {**repair, 'contract_revision': contract['revision']}
        if validation.get("contract_hash") == contract["hash"] and validation.get("source_revision"):
            outputs["autoreview"] = {**common, "kind": "review-result", "validation": validation,
                                     "source_revision": validation["source_revision"],
                                     "task_id": validation.get("task_id")}
    handoffs = {}
    for unit, value in outputs.items():
        digest = support.digest(value)
        path = Path(run_dir) / "handoffs" / unit / (digest + ".json")
        if not path.exists():
            support.atomic_json(path, value)
        elif support.read(path) != value:
            raise ValueError(f"Unit handoff was modified: {path}")
        handoffs[unit] = {"path": str(path), "hash": digest, "kind": value["kind"],
                          "contract_hash": value["contract_hash"]}
    state["unit_handoffs"] = handoffs
    return handoffs


def admit_validation(runtime, state, stage, workspace, run_dir):
    """Ask the user instead of launching another validation-only round that cannot close its blockers."""
    blocking = findings_ledger.blocking_entries(state) if stage == "sol" else []
    stop = blocking and validation_rounds.admit(state, blocking, support.snapshot(workspace)["revision"])
    if not stop:
        return
    error = support.Paused(validation_rounds.STATUS, stop["reason"])
    state.update(status=error.status, phase="PAUSED_OR_BLOCKED", stop_reason=stop["reason"], paused_at=support.now())
    runtime.resolver_runtime.record_operational_exhaustion(runtime, state, run_dir, error, request=stop["request"])
    runtime.write_json(Path(run_dir) / "state.json", state)
    print(f"{error.status}: {stop['reason']}\n{lifecycle.render(state)}", flush=True)
    raise LoopExit(2)


def dispatch_unit(runtime, state, stage, workspace, run_dir):
    """Call one unit using the runner's durable provider/recovery services."""
    progressive_state.guard_dispatch(state, stage)
    if stage == 'terra':
        builder_policy.guard(state)
    runtime.milestones.dispatch_guard(state, stage)
    runtime.workflow.dispatch_guard(state, stage, workspace)
    admit_validation(runtime, state, stage, workspace, run_dir)
    unit = unit_module(stage)
    if stage == "orchestrator":
        return unit.dispatch(state, workspace, run_dir)
    regression.before_review(state, stage, workspace, run_dir)
    state_path = run_dir / "state.json"
    request = prepare_request(state, stage, state_path, runtime.SCHEMA_DIR)
    runtime.rotate_if_needed(state, request.route_role, run_dir)
    state["pending_context_metrics"] = request.metrics
    if request.metrics["estimated_prompt_tokens"] > request.metrics["soft_budget_tokens"]:
        print("Context soft budget exceeded; preserving complete requirements", flush=True)
    runtime.write_json(state_path, state)
    schema_path = run_dir / "schemas" / f"v3-{stage}.json"
    runtime.write_json(schema_path, runtime.support.model_output_schema(request.schema))
    try:
        value, record = runtime.run_role(role=request.role, prompt=request.prompt,
            sandbox=units_common.launch_sandbox(stage, request.allow_write),
            workspace=workspace, run_dir=run_dir, state=state, schema=schema_path,
            model=state["settings"]["roles"][request.route_role]["model"],
            allow_write=request.allow_write, dry_run=False)
        record["unit"] = unit_for(stage)
        runtime.account_stage(state, record)
        try:
            runtime.commit_stage_result(state, stage, value, record, workspace, run_dir)
        except (ValueError, KeyError, runtime.support.Paused) as error:
            runtime.reject_completed_stage(state, run_dir, record, error)
    except runtime.ReportRepairQueued:
        return SKIP
    except runtime.support.Paused as error:
        if provider_recovery.recover_dispatch(runtime, state, run_dir, workspace, error):
            print(f"{stage}: non-terminal attempt archived; continuing from recovery checkpoint", flush=True)
            return SKIP
        raise
    return record


def _check_code_refs(state, refs, field="code_refs"):
    if not state.get("workspace"):
        return
    root = Path(state.get("workspace") or "")
    if not root.is_dir():
        return
    files = planning_unit.workspace_inventory(root, state.get("task", ""))["files"]
    if not files:
        return
    if not refs:
        raise ValueError(f"Report must cite existing source in {field}")
    for ref in refs:
        raw, line_text = planning_unit.split_code_ref(root, str(ref))
        target = planning_unit.cited_file(root, raw, field, ref)
        if line_text:
            citation = re.fullmatch(r"(\d+)(?:-(\d+))?(?:\s+.*)?", line_text)
            if not citation:
                raise ValueError(f"{field} entry {ref} has no line number")
            line = int(citation[1])
            end = int(citation[2] or citation[1])
            count = len(target.read_text(errors="replace").splitlines())
            if line < 1 or end < line or end > max(count, 1):
                raise ValueError(f"{field} entry {ref} points past the end of the file")


def _bind_plan(state, value, origin, record):
    if "contract" not in value:
        return
    if (origin == "glm_draft" and value["contract"].get("open_blocking_questions")
            and (value["contract"].get("milestones") or value["contract"].get("technical_approach")
                 or value["contract"].get("initial_task", {}).get("kind") in ("implement", "validate"))):
        raise ValueError("Unresolved blocking questions require a clarification-only draft: "
                         "technical_approach=[] and milestones=[]; no executable initial_task")
    goals.check_requirement_trace(state, value, value["contract"], coverage=planning_unit.traces_coverage(value["contract"]))
    if origin in ("glm_draft", "glm_revise"):
        _check_code_refs(state, value.get("code_refs") or [])
    progressive_state.accept_proposal(state, value, origin=origin)
    lifecycle.install_draft(state, value["contract"], origin=origin, changes=value.get("contract_changes") or [], record=record)


def apply_planning(state, stage, value, record, *, run_dir=None):
    if progressive_state.revision_pending(state):
        result = progressive_state.apply_revision(state, stage, value, record,
            product_findings=findings_ledger.blocking_entries(state))
        if isinstance(result, dict) and "material_request" in result:
            request = result["material_request"]
            human.queue(state, request["kind"], {"stage": stage}, request=request,
                        evidence=result["evidence"], next_stage=result["next_stage"])
        elif isinstance(result, dict):
            first = result["initial_task"]
            decision = {"status": "CONTINUE", "next_task": {key: entry for key, entry in first.items()
                        if key not in ("objective", "affected_paths")}, "next_objective": first["objective"],
                        "affected_paths": first["affected_paths"], "evidence": []}
            kind = lifecycle.assign_task(state, decision, support.snapshot(Path(state["workspace"])))
            state.update(next_stage="sol" if kind == "validate" else "terra", phase="EXECUTING")
        return
    # Older saved reports predate explicit, user-backed conflict resolutions.
    # An absent list supplies no authority to resolve any conflict.
    if "conflict_resolutions" in planning_unit.SCHEMAS[stage]["properties"]:
        value = {"conflict_resolutions": [], **value}
    if stage in planning_unit.V2_STAGES:
        support.validate_schema(value, planning_unit.SCHEMAS[stage])
        progressive_state.accept_proposal(state, value, origin=stage)
        prepared = planning_artifacts.prepare(state, stage, value, origin=stage,
                                              run_dir=run_dir, record=False)
        if stage == "requirements":
            lifecycle.apply_requirements(state, value["requirements"],
                                     artifact_sha256=prepared["artifact"]["sha256"], record=record)
            planning = state.setdefault("planning", {"astra_calls": 0, "reports": {}, "final_token": None})
        else:
            planning = state.setdefault("planning", {"astra_calls": 0, "reports": {}, "final_token": None})
            reports = planning["reports"]
            if stage == "plan":
                derived = planning_graph.validate(value["contract"])
                lifecycle.install_draft(state, value["contract"], origin="plan", record=record)
                planning["derived_graph"] = derived
            elif stage == "plan_review":
                concerns = value["concerns"]
                ids = [concern["id"] for concern in concerns]
                if len(ids) != len(set(ids)) or any(not item.strip() for item in ids):
                    raise ValueError("Concern IDs must be nonempty and unique")
                if any(not concern[key].strip() for concern in concerns
                       for key in ("concern", "requested_change", "acceptance_test")):
                    raise ValueError("Each concern needs a concrete change and acceptance test")
                state["next_stage"] = "plan_revise"
            elif stage == "plan_revise":
                planning_unit._coverage(value["responses"], reports["plan_review"]["report"]["concerns"])
                if any(not response["evidence_refs"] for response in value["responses"]):
                    raise ValueError("Planner responses must cite investigated evidence")
                derived = planning_graph.validate(value["contract"])
                lifecycle.install_draft(state, value["contract"], origin="plan_revise", record=record)
                planning["derived_graph"] = derived
            elif stage == "plan_finalize":
                planning_unit._coverage(value["decisions"], reports["plan_review"]["report"]["concerns"])
                unresolved = {row["concern_id"] for row in value["decisions"] if not row["resolved"]}
                if unresolved and not value["contract"]["open_blocking_questions"]:
                    raise ValueError("Unresolved planning decisions must return as blocking questions")
                if not value["contract"]["open_blocking_questions"] and "initial_task" not in value["contract"]:
                    raise ValueError("Final plan needs an initial_task before approval")
                derived = planning_graph.validate(value["contract"])
                lifecycle.install_draft(state, value["contract"], origin="plan_finalize", record=record)
                planning["derived_graph"] = derived
                planning["final_token"] = goals.token(state["goal_contract"])
                planning_artifacts.prepare_final_outputs(state, prepared)
        planning["reports"][stage] = {"report": copy.deepcopy(value), "output": record["output"],
                                       "artifact": {"artifact": copy.deepcopy(prepared["artifact"]),
                                                    "delta": copy.deepcopy(prepared["delta"])}}
        state["discovery_summary"] = value["summary"]
        planning_artifacts.record_prepared(state, prepared)
        return
    checked = value
    if stage == "requirements_gather" and isinstance(value.get("proposed_assumptions"), list):
        # A pre-structured report's plain-string assumptions stay readable as legacy.
        checked = {**value, "proposed_assumptions": [row for row in value["proposed_assumptions"]
                                                     if not isinstance(row, str)]}
    support.validate_schema(checked, planning_unit.schema_for(state, stage))
    if stage == planning_unit.RECOGNIZE:
        return planning_unit.recognize(state, value, record)
    if stage == "astra_discovery" and planning_unit.rerun_requirements(state, value):
        return  # the draft is discarded before any of its checks: Requirements runs next
    value, deferred = clarification.clarify_discoverable(
        state, stage, value, record, investigation_stages=planning_unit.INVESTIGATION_STAGES,
        check_code_refs=_check_code_refs)
    if deferred:
        return
    clarification.apply_obligations(state, stage, value, check_code_refs=_check_code_refs)
    if stage == "requirements_gather":
        if not value["intended_outcome"].strip() or not value["required_behaviors"] or not value["acceptance_tests"]:
            raise ValueError("Requirements handoff needs an outcome, behaviors, and acceptance tests")
        questions = value["open_questions"]
        ids = [question["id"] for question in questions]
        if len(ids) != len(set(ids)) or any(not question_id.strip() for question_id in ids):
            raise ValueError("Requirements question IDs must be nonempty and unique")
        previous = state.get("requirements_handoff")
        if previous:
            state.setdefault("requirements_history", []).append(copy.deepcopy(previous))
        goals.check_requirement_handoff(state, value)
        input_refs = {"task", *state.get("answers", {})}
        input_refs.update(event["id"] for event in state.get("user_events", []) if event.get("id"))
        # External design/spec references may accompany local inspection evidence;
        # they are not filesystem paths and do not satisfy the source-inspection gate.
        local_refs = [ref for ref in value["source_refs"]
                      if ref not in input_refs and not ref.startswith(("https://", "http://"))]
        _check_code_refs(state, local_refs, "source_refs")
        state["requirements_handoff"] = {"report": copy.deepcopy(value), "output": record["output"]}
        state.update(status="RUNNING", phase="PLANNING", next_stage="astra_discovery",
                     discovery_summary=value["summary"])
        return
    # New joint plans must state every dependency; older saved contracts remain readable.
    if ("plan_reviewer" in state.get("settings", {}).get("roles", {})
            and "contract" in value
            and any("depends_on" not in row for row in value["contract"].get("milestones", []))):
        raise ValueError("Every planned milestone must declare depends_on (use [] for independent work)")
    if stage == "astra_discovery":
        clarification.check_handoff_questions(state, value)
        _bind_plan(state, value, "glm_draft", record)
        if human.internal_questions(state):
            state["discovery_summary"] = value["summary"]
            return
        # install_draft starts the bounded cycle once clarification is complete.
    planning = state["planning"]
    reports = planning["reports"]
    if stage == "astra_challenge":
        concerns = value["concerns"]
        ids = [c["id"] for c in concerns]
        if len(ids) != len(set(ids)) or any(not x.strip() for x in ids):
            raise ValueError("Concern IDs must be nonempty and unique")
        if any(not c[k].strip() for c in concerns for k in ("concern", "requested_change", "acceptance_test")):
            raise ValueError("Each concern needs a concrete change and acceptance test")
        planning_unit.after_challenge(state, value, record)
    elif stage == "glm_revise":
        concerns = reports["astra_challenge"]["report"]["concerns"]
        planning_unit._coverage(value["responses"], concerns)
        if any(not r["evidence_refs"] for r in value["responses"]):
            raise ValueError("Planner responses must cite investigated evidence")
        _bind_plan(state, value, stage, record)
        if human.internal_questions(state):
            reports[stage] = {"report": copy.deepcopy(value), "output": record["output"]}
            state["discovery_summary"] = value["summary"]
            return
        state.update(status="RUNNING", phase="PLANNING", next_stage=planning_unit.after_revise(state), pending_questions=[])
    elif stage == "astra_finalize":
        concerns = reports["astra_challenge"]["report"]["concerns"]
        planning_unit._coverage(value["decisions"], concerns)
        unresolved = {d["concern_id"] for d in value["decisions"] if not d["resolved"]}
        if unresolved and not value["contract"]["open_blocking_questions"]:
            raise ValueError("Unresolved planning decisions must return to the user as blocking questions")
        if not value["contract"]["open_blocking_questions"] and "initial_task" not in value["contract"]:
            raise ValueError("Final plan needs an initial_task so approval does not spend another Plan Reviewer call")
        _bind_plan(state, value, stage, record)
        planning["final_token"] = goals.token(state["goal_contract"])
    reports[stage] = {"report": copy.deepcopy(value), "output": record["output"]}
    state["discovery_summary"] = value["summary"]


def apply_planning_result(state, stage, value, record, *, run_dir=None):
    if planning_unit.is_planning(state, stage):
        apply_planning(state, stage, value, record, run_dir=run_dir)
        return
    schema = support.read(Path(record["schema"])) if record.get("schema") else goals.DISCOVERY_SCHEMA
    support.validate_schema(value, schema)
    legacy = not any(key in schema["properties"]["contract"]["properties"] for key in goals.BRIEF_FIELDS)
    lifecycle.install_draft(state, value["contract"], origin="astra_discovery", allow_legacy=legacy, record=record)
    state["discovery_summary"] = value["summary"]


def assert_within_assignment(state, record):
    """A serial Builder gets the same ownership gate as a parallel worktree.

    The declared affected_paths are the assignment boundary. Evidence is the retained
    tree delta since the task's first Builder attempt began (autocode_assignment), so
    edits an earlier attempt left behind count; never the report's changed_files list.
    Tasks without explicit ownership (legacy contracts, [] for serial dispatch) are unbounded.
    """
    owned = (state.get("current_task") or {}).get("affected_paths") or []
    if not owned or not state.get("goal_contract"):
        return
    outside = assignment.outside(owned, state.get("stages", []), record, workspace=state.get("workspace"))
    if outside is None:
        raise support.Paused("PAUSED_ASSIGNMENT_SCOPE",
                             "The assignment's starting snapshot is missing; edits retained for inspection")
    if outside:  # files the assignment created are removed, so a retry is not refused for them
        raise support.Paused("PAUSED_ASSIGNMENT_SCOPE", assignment.undo_created(outside, state.get("stages", []), record, state.get("workspace")))


def retained_validated_candidate(state, value, record, workspace):
    """Compatibility entry point for previously validated retained work."""
    return retained_work.validated_candidate(state, value, record, workspace,
                                             support.snapshot(workspace)['revision'])


def recover_retained_candidate(state, workspace):
    """Reconsider an already-saved no-progress report without another provider call."""
    reports = state.get('no_progress_reports') or []
    if (state.get('active_stage') or state.get('uncertain_artifacts') or not reports
            or (state.get('current_task') or {}).get('kind') != 'implement'):
        return False
    record = next((row for row in reversed(state.get('stages', []))
                   if (row.get('original_stage') or row.get('stage')) == 'terra'
                   and not row.get('runner_owned') and not row.get('changed_files')
                   and row.get('source_revision') == support.snapshot(workspace)['revision']), None)
    if not record:
        return False
    value = reports[-1]
    evidence = retained_validated_candidate(state, value, record, workspace)
    if not evidence:
        return False
    state['implementation'] = {**copy.deepcopy(value), 'source_revision': evidence['source_revision'],
                               'workspace': str(workspace)}
    state['changed_files'] = []
    state['source_snapshot'] = record.get('after_ref')
    state['diff_ref'] = record.get('diff_ref')
    state['next_stage'] = workflow.review_stage(state)
    state['no_progress_batches'] = 0
    state.setdefault('retained_candidate_handoffs', []).append({
        'at': support.now(), 'task_id': state['current_task']['id'],
        'builder_output': record['output'], 'reconsidered': True, **evidence})
    return True


def apply_build_result(runtime, state, value, record, workspace, run_dir):
    assert_within_assignment(state, record)
    support.evidence_hashes(support.implementation_evidence_paths(value["evidence_refs"], record["events"]), workspace, run_dir)
    state.update(implementation={**value, "source_revision": record.get("source_revision"),
                                 "workspace": str(workspace)},
                 changed_files=record["changed_files"], source_snapshot=record["after_ref"],
                 next_stage=workflow.review_stage(state), diff_ref=record.get("diff_ref"))
    if not record["changed_files"]:
        retained = retained_validated_candidate(state, value, record, workspace)
        if retained:
            state['no_progress_batches'] = 0
            state.setdefault('retained_candidate_handoffs', []).append({
                'at': support.now(), 'task_id': (state.get('current_task') or {}).get('id'),
                'builder_output': record['output'], **retained})
            state['next_stage'] = workflow.review_stage(state)
            return
        fresh = retained_work.fresh_candidate(state, record, support.snapshot(workspace)['revision'])
        if fresh:
            # The runner's snapshots, rather than this attempt's empty report,
            # identify the files left by an earlier Builder. They are a candidate
            # for independent review, never proof that the work passes.
            state['changed_files'] = fresh['retained_paths']
            state['implementation']['changed_files'] = fresh['retained_paths']
            state['no_progress_batches'] = 0
            state.setdefault('retained_candidate_handoffs', []).append({
                'at': support.now(), 'task_id': (state.get('current_task') or {}).get('id'),
                'builder_output': record['output'], 'previously_validated': False,
                'reported_changed_files': value.get('changed_files'), **fresh})
            state['next_stage'] = workflow.review_stage(state)
            return
        state["no_progress_batches"] = state.get("no_progress_batches", 0) + 1
        if state.get('current_task', {}).get('kind') == 'implement':
            state.setdefault('no_progress_reports', []).append(state.pop('implementation'))
            state.setdefault('unit_handoffs', {}).pop('autocode', None)
            state['next_stage'] = 'terra'
            builder_policy.failure(state, record['output'], 'Builder completed an implementation attempt without source changes')
            return
        escalation.advance(state, "terra", trigger="no_progress",
                           detail="Builder completed a batch without source changes",
                           struggle_id=f"iteration:{record.get('iteration', state.get('iteration', 0))}")
    else:
        state["no_progress_batches"] = 0
    if workflow.final_only(state):
        workflow.apply_implementation(runtime,state,value,record,workspace,run_dir)


def apply_review_result(runtime, state, stage, value, record, workspace, run_dir):
    modern = state.get("version", 2) >= 3
    progressive_state.require_reported_checks(state, value["checks"])
    support.verify_checks(value["checks"], workspace, record["events"], **runtime.check_evidence_options(record))
    check_refs.resolve(value)  # check:<n> evidence names a check whose event the runner just attached
    refs = [c["evidence_ref"] for c in value["checks"]]
    for check in value["checks"]:
        if not check["evidence_ref"].startswith("event:"):
            receipt_path = Path(check["evidence_ref"])
            receipt_path = receipt_path if receipt_path.is_absolute() else workspace / receipt_path
            refs.append(support.read(receipt_path)["full_output"])
    refs += [p for row in value["criterion_results"] for p in row["evidence_refs"]] + design_coverage.report_refs(state, value, stage=stage)
    flow = value.get("end_to_end_result", {})
    refs += flow.get("evidence_refs", [])
    technical = flow.get("technical_result") or {}
    refs += technical.get("evidence_refs", [])
    members = state.get("current_task", {}).get("milestone_ids", [])
    if members:
        results = value.get("milestone_results", [])
        ids = [r["milestone_id"] for r in results]
        if len(ids) != len(set(ids)) or set(ids) != set(members):
            raise ValueError("Combined validation must report every batch milestone exactly once")
        for result in results:
            if (value["verdict"] == "PASS" and result["status"] != "PASS"
                    or result["status"] == "PASS" and (not result["summary"].strip() or not result["evidence_refs"])):
                raise ValueError("Combined PASS needs passing evidence for every milestone outcome")
            refs += result["evidence_refs"]
    if flow.get("status") == "PASS" and (not flow.get("summary", "").strip() or not flow.get("evidence_refs")):
        raise ValueError("End-to-end PASS requires a check description and evidence")
    if ((flow.get("status") == "PASS" and not goals.flow_awaits_only(flow, set()))
            or (technical.get("status") == "FAIL" and flow.get("status") != "FAIL")):
        raise ValueError("End-to-end result conflicts with technical proof or pending human criteria")
    ids = [row["id"] for row in value["criterion_results"]]
    known = {c["id"] for c in state["acceptance_criteria"]}
    if len(ids) != len(set(ids)) or not set(ids) <= known:
        raise ValueError("Validator criterion results must use unique approved IDs")
    for ref in refs:
        if ref.startswith("event:"):
            event_id = ref.split(":", 1)[1]
            matches = [e for e in support.events(record["events"]) if e.get("type") == "item.completed"
                       and e.get("item", {}).get("id") == event_id
                       and e["item"].get("type") == "command_execution"]
            if len(matches) != 1:
                raise ValueError(f"Criterion evidence references a missing executed event: {event_id}")
    refs = [record["events"] if p.startswith("event:") else p for p in refs]
    pins = support.evidence_hashes(refs, workspace, run_dir) if refs else {}
    validation = {**value, "evidence_hashes": pins, "criteria_revision": state["criteria_revision"],
                  "source_revision": record["source_revision"], "output": record["output"],
                  "reviewer_role": record.get("role", stage)}
    human_pending = modern and any(goals.human_only_pending_validation(state, value, c["id"])
                                  for c in state["goal_contract"]["body"]["acceptance_criteria"] if c["human_review"])
    progressive_pass = progressive_state.enabled(state) and any(
        row["status"] == "PASS" for row in value.get("criterion_results", []))
    if (value["verdict"] == "PASS" or human_pending or progressive_pass) and (not value["checks"] or any(c["exit_code"] for c in value["checks"])):
        raise ValueError("Technically passing validation lacks successful executed checks: list each check you ran, with its exit code")
    validation["check_replay"] = (check_replay.replay(value["checks"], workspace, run_dir, record, verify.scratch_run,
                                                   approved_state=state, progressive_context=progressive_state.context(state),
                                                   execution_identity=verify.execution_identity)
                                  if value["verdict"] == "PASS" or human_pending or progressive_pass else None)
    efficiency.observe_replay(state, validation["check_replay"], attempt_id=record.get("events") or record["output"])
    validation["evidence_hashes"].update(check_replay.evidence_pins(validation["check_replay"]))
    if stage == 'sol' and (record.get('visual_runtime') or visual_runtime.requested(state)):
        visual_receipt = visual_runtime.accept_review(record.get('visual_runtime'), state, record, run_dir=run_dir,
                                                       current_snapshot=support.snapshot(workspace), accepted_validation=validation)
        if visual_receipt:
            validation['evidence_hashes'].update(visual_receipt['evidence_hashes'])
    if progressive_state.enabled(state):
        progressive_state.check_result_binding(state, record, support.snapshot(workspace))
        progressive_state.assert_product_claims(state, support.snapshot(workspace), validation)
    if state.get("validation"):
        state.setdefault("validation_archive", []).append({
            "reason": "Superseded by another independent validation", "validation": state["validation"]})
    # A PASS or FAIL from the Validator is evidence. It cannot withdraw an open Plan Reviewer
    # correction or retarget the workflow at a new review of the old candidate.
    correction_open = bool(state.get("resolution_request")) and state.get("next_stage") == "astra_resolve"
    if correction_open:
        state.setdefault("validation_archive", []).append({
            "reason": "Stored during an open correction; not applied to the current candidate",
            "validation": validation})
    else:
        state.update(validation=validation, unresolved_findings=value["findings"], next_stage="astra_review")
    findings_ledger.record_validation(state, value, record)
    milestones.observe_validation(state, support.snapshot(workspace))
    if modern:
        state["human_reviews"] = {}
        state.pop("displayed_review", None)
    if stage == 'sol' and workflow.final_only(state) and record.get('role') == 'sol':
        workflow.dispatch_guard(state,'sol',workspace)
        state.setdefault('consultation_reports',[]).append({
            'question':state.pop('targeted_consultation'),'report':state.pop('validation')})
        state['next_stage']='terra'


def queue_resolution(state, decision, record, *, source_stage='astra_review', source_report=False):
    goals.execution_guard(state, decision)
    task = state.get('current_task') or {}
    revision = record.get('source_revision')
    if (not task.get('id') or task.get('contract_hash') != state['goal_contract']['hash']
            or task.get('contract_revision') != state['goal_contract']['revision']
            or not revision or revision != support.snapshot(Path(state['workspace']))['revision']
            or not record.get('output') or not Path(record['output']).is_file()
            or record.get('rejected') or record.get('exit_code', 0) != 0
            or (source_stage != 'terra' and record.get('changed_files'))
            or record.get('task_id', task['id']) != task['id']):
        raise support.Paused('PAUSED_STALE_HANDOFF',
                             'AutoResolver diagnosis requires a current approved task, source and saved source report')
    previous = state.get('resolution_request') or {}
    if (previous.get('task_id') == task['id'] and previous.get('source_revision') == revision
            and previous.get('diagnosis_output')):
        if human.PRIVATE in state or human.PUBLIC in state:
            raise support.Paused('PAUSED_RESOLVER', 'This task and source already received a resolver diagnosis; '
                                 'the existing blocker must be reconciled before another diagnosis')
        return unit_module('astra_resolve').wait_on_existing_diagnosis(state, decision, record, previous,
                                                                       source_stage=source_stage)
    resolved = [row for row in state.get('stages', []) if row.get('stage') == 'astra_resolve'
                and row.get('source_revision') == revision and not row.get('rejected')]
    if (len(resolved) >= failures.REPEAT_THRESHOLD
            or failures.repeated(state, {'stage': 'astra_resolve', 'source_revision': revision})
            or failures.repeated(state, {**record, 'stage': source_stage})):
        raise support.Paused('PAUSED_REPEATED_FAILURE', 'Read-only diagnosis exhausted the existing repeated-failure limit for this source')
    if not source_report:
        if support.criteria_definition(decision['acceptance_criteria']) != support.criteria_definition(state['acceptance_criteria']):
            raise support.Paused('PAUSED_CRITERIA_CHANGE', 'Repair cannot change approved acceptance criteria')
        # Retain the reviewer's unverified statuses even while diagnosis is pending.
        state['acceptance_criteria'] = copy.deepcopy(decision['acceptance_criteria'])
    validation = state.get('validation') or {}
    pins = dict(validation.get('evidence_hashes', {})) if (
        validation.get('source_revision') == revision and validation.get('task_id') == task['id']
        and validation.get('contract_hash') == state['goal_contract']['hash']) else {}
    pins[record['output']] = support.file_hash(Path(record['output']))
    if record.get('events') and Path(record['events']).is_file():
        pins[record['events']] = support.file_hash(Path(record['events']))
    if any(not Path(path).is_file() or support.file_hash(Path(path)) != digest for path, digest in pins.items()):
        raise support.Paused('PAUSED_STALE_HANDOFF', 'Diagnosis evidence changed before the resolver handoff')
    state['resolution_request'] = {
        'contract_hash': state['goal_contract']['hash'],
        'task_id': task['id'], 'source_revision': revision,
        'source_stage': source_stage, 'source_output': record['output'],
        'provenance': 'source_report_not_accepted_review' if source_report else 'completion_review_decision',
        **({'source_report': copy.deepcopy(decision)} if source_report else
           {'review': copy.deepcopy(decision), 'review_output': record['output']}),
        'evidence_hashes': pins,
    }
    state.pop(human.PRIVATE, None)
    state.pop(human.PUBLIC, None)
    state.pop('user_request', None)
    state.update(status='RUNNING', phase='RESOLVING', next_stage='astra_resolve', pending_questions=[])
    resolver_recovery.prepare_resolution(state, decision, record)


def finish_resolution(state, value, record):
    request = state.pop('resolution_request')
    plan = {'kind': 'repair-plan', 'version': 1,
            'contract_hash': request['contract_hash'], 'source_revision': request['source_revision'],
            'diagnosis': value['diagnosis'], 'evidence': value['evidence'],
            'source_output': request.get('source_output', request.get('review_output')),
            'source_stage': request.get('source_stage', 'astra_review'),
            'provenance': request.get('provenance', 'completion_review_decision'),
            **({'review_output': request['review_output']} if 'review_output' in request else {}),
            'evidence_hashes': request['evidence_hashes'],
            'output': record['output'],
            'tasks': [{**copy.deepcopy(state['current_task']), 'depends_on': []}]}
    state['repair_plan'] = plan
    resolver_recovery.finish_resolution_packet(state, request, plan)
    state.setdefault('resolution_history', []).append(copy.deepcopy(plan))


def apply_diagnosis_result(runtime, state, value, record, workspace, run_dir):
    """Validate a model's bounded diagnosis and, only if accepted, retry its
    original failed stage. A rejected or escalated diagnosis pauses; it never
    grants a second chance beyond the two-evaluation budget already spent
    across admission and this completion.
    """
    unit_module('astra_diagnose').validate_diagnosis(state, value, record, workspace)
    runtime.resolver_runtime.finish_operational_diagnosis(state, run_dir, value['recommendation'],
                                                        recovery_change=value.get('recovery_change'),
                                                        diagnosis=value['diagnosis'])


def apply_result(runtime, state, stage, value, record, workspace, run_dir):
    """Commit a unit result only after every transition and evidence gate succeeds."""
    candidate = copy.deepcopy(state)
    _apply_result(runtime, candidate, stage, value, record, workspace, run_dir)
    state.clear()
    state.update(candidate)


def _apply_result(runtime, state, stage, value, record, workspace, run_dir):
    """Autopilot alone interprets unit results and advances the workflow."""
    support, goals, planning = runtime.support, runtime.goals, runtime.planning
    workflow, milestones, escalation = runtime.workflow, runtime.milestones, runtime.escalation
    dispatch, save_record, now = runtime.dispatch, runtime.save_record, runtime.now
    if stage in jobs.UNIT:
        unit_module(stage).apply_job(stage, state, value, record, workspace)
        return save_record(state, record)
    if stage == "astra_resolve":
        unit_module(stage).validate(state, value, record, workspace)
        value = unit_module(stage).preserve_review_criteria(state, value)
    if stage == "astra_diagnose":
        # A bounded diagnosis+recommendation object, not a reviewer decision:
        # it carries no acceptance_criteria/status and must never reach the
        # shared astra*-report handling below.
        apply_diagnosis_result(runtime, state, value, record, workspace, run_dir)
        save_record(state, record)
        return
    if stage == "astra_checkpoint":
        workflow.apply_checkpoint(runtime, state, value, record, workspace, run_dir)
        return
    modern = state.get("version", 2) >= 3
    if planning.is_planning(state, stage) or (modern and stage == "astra_discovery"):
        apply_planning_result(state, stage, value, record, run_dir=run_dir)
        save_record(state, record)
        return
    if modern:
        if progressive_state.enabled(state):
            progressive_state.check_result_binding(state, record, support.snapshot(workspace))
        goals.execution_guard(state, value)
        for entry in value.get("deferred_backlog", []):
            if entry not in state.setdefault("deferred_backlog", []):
                state["deferred_backlog"].append(entry)
        if value["user_request"]["kind"] != "none":
            request = value['user_request']
            origin = {'stage': stage, **{key: record[key] for key in
                      ('output', 'source_revision', 'task_id') if key in record}}
            if workflow.final_only(state) and not stage.startswith('astra'):
                if stage == 'terra':
                    assert_within_assignment(state, record)
                if request['kind'] in ('blocker', 'clarification'):
                    queue_resolution(state, value, record, source_stage=stage, source_report=True)
                else:
                    lifecycle.wait_for_user(state, request, origin=origin,
                                        evidence={'provenance': 'source_report_not_accepted_review'})
                save_record(state,record)
                return
            if stage.startswith("astra"):
                if value["status"] != "BLOCKED":
                    raise ValueError("The Plan Reviewer must choose BLOCKED when requesting a user decision")
                # Record the defects already identified, then pause. The early return
                # below never reaches the normal review path.
                if stage != 'astra_resolve':
                    definitions = support.criteria_definition(value['acceptance_criteria'])
                    if (len({row['id'] for row in definitions}) != len(definitions)
                            or definitions != support.criteria_definition(state['acceptance_criteria'])):
                        raise support.Paused('PAUSED_CRITERIA_CHANGE', 'A blocked decision cannot change approved criteria')
                    findings_ledger.record_decision(state, value, record)
                if stage != 'astra_resolve' and request['kind'] in ('blocker', 'clarification'):
                    queue_resolution(state, value, record, source_stage=stage)
                else:
                    evidence = {}
                    if stage == 'astra_resolve':
                        evidence = {'diagnosis': value['diagnosis'], 'output': record['output'],
                                    'hashes': dict(state['resolution_request']['evidence_hashes'])}
                        evidence['hashes'][record['output']] = support.file_hash(Path(record['output']))
                        state['resolution_request']['diagnosis_output'] = record['output']
                    lifecycle.wait_for_user(state, request, origin=origin, evidence=evidence,
                                        next_stage='astra_review')
                goals.record_decision(state, value)
                state.pop("agent_request", None)
                save_record(state, record)
                return
            state["agent_request"] = {"role": stage, "request": copy.deepcopy(value["user_request"])}
            if stage == "terra":
                state.update(implementation={**value, "source_revision": record.get("source_revision"),
                                             "workspace": str(workspace)},
                             changed_files=record.get("changed_files", []), source_snapshot=record.get("after_ref"),
                             diff_ref=record.get("diff_ref"), next_stage="astra_review")
                save_record(state, record)
                return
    if (modern and stage == "astra_review" and value.get("status") == "REWORK"
            and not workflow.enabled(state)):
        # Keep the review authoritative whether its correction is assigned or diagnosed.
        findings_ledger.record_decision(state, value, record)
        if not rework_policy.route(runtime, state, value, record, queue_resolution, builder_policy, run_dir=run_dir):
            resolver_recovery.route_known_change(runtime, state, value, record, run_dir=run_dir, retry_policy=builder_policy)
        save_record(state, record)
        return
    if stage.startswith("astra"):
        definitions = support.criteria_definition(value["acceptance_criteria"])
        if len({c["id"] for c in definitions}) != len(definitions):
            raise support.Paused("PAUSED_INVALID_OUTPUT", "Duplicate acceptance IDs")
        old = state.get("acceptance_criteria", [])
        if old and definitions != support.criteria_definition(old):
            raise support.Paused("PAUSED_CRITERIA_CHANGE", "The Plan Reviewer proposed a criteria change; previous revision remains authoritative")
        state["acceptance_criteria"] = value["acceptance_criteria"]
        state["criteria_revision"] = support.digest(definitions)
        state["plan"] = value.get("plan", [value["next_objective"]])
        state["affected_paths"] = value.get("affected_paths", [])
        if modern and stage != "astra_resolve":
            # Only the reviewers reconcile findings. The resolver diagnoses them.
            findings_ledger.record_decision(state, value, record)
        if modern and stage == "astra_review":  # only the Validator can close its own open blockers
            value = findings_ledger.recheck_by_validator(state, value, record.get("source_revision"))
        if stage == "astra_review" and value.get("progressive_checkpoint") is True:
            if value["status"] != "CONTINUE" or not progressive_state.enabled(state):
                raise ValueError("progressive checkpoint requires explicit CONTINUE within an approved progressive run")
            if value["next_task"]["kind"] != "none":
                raise ValueError("slice checkpoint cannot manufacture a next assignment before independent slice review")
            proven = {row["id"] for row in (state.get("validation") or {}).get("criterion_results", []) if row["status"] == "PASS"}
            if any(row["status"] == "verified" and row["id"] not in proven for row in value["acceptance_criteria"]):
                raise ValueError("slice checkpoint cannot claim verified original criteria without the Validator's current product proof")
            progressive_state.checkpoint(state, support.snapshot(workspace), record,
                                         product_findings=findings_ledger.blocking_entries(state))
            goals.record_decision(state, value)
            save_record(state, record)
            return
        current = support.snapshot(workspace)
        request = (completion_gate.artifact_review_request(state, value, current)
                   if modern and stage in ("astra_review", "astra_checkpoint") else None)
        if request:
            lifecycle.wait_for_user(state, request,
                origin={'stage': stage, 'output': record['output'], 'source_revision': current['revision']},
                next_stage='astra_review')
            goals.record_decision(state, value)
            save_record(state, record)
            return
        if value["status"] in ("COMPLETE", "TASK_COMPLETE"):
            visual_runtime.require_completion(state, current_snapshot=current)
            progressive_state.prepare_completion(state, current, record,
                                                  product_findings=findings_ledger.blocking_entries(state))
            if modern and findings_ledger.blocking_entries(state):
                raise support.Paused("PAUSED_COMPLETION_GATE", "Completion rejected: the findings ledger still lists "
                                     "open blocking findings; resolve or retract each one with evidence")
            if modern and goals.missing_human_reviews(state):
                raise support.Paused("PAUSED_COMPLETION_GATE", "Artifact review requires current passing independent evidence first")
            if not completion_gate.completion_ready(state, value, current):
                if not regression.complete(state, current["revision"]):
                    raise support.Paused("PAUSED_COMPLETION_GATE", regression.rejection(state))
                raise support.Paused("PAUSED_COMPLETION_GATE", completion_gate.rejection(state))
            state.update(status="TASK_COMPLETE", completed_at=now(), final_decision=value, next_stage=None)
            if milestones.enabled(state):
                milestones.accept(state, current)
            if modern:
                state["phase"] = "COMPLETE"
        elif value["status"] == "BLOCKED":
            if modern:
                raise support.Paused("PAUSED_INVALID_OUTPUT", "BLOCKED requires a structured user_request")
            state.update(status="BLOCKED_HUMAN", stop_reason=value["blocker"], next_stage="astra_review")
        elif value["status"] == "VALIDATE":
            if stage == "astra_review":
                state["iteration"] += 1
            state.update(next_stage=workflow.review_stage(state))
        else:
            if not value["next_objective"].strip():
                raise support.Paused("PAUSED_INVALID_OUTPUT", "CONTINUE requires an action")
            # Passing Validator evidence cannot override the Plan Reviewer's rework or unverified criteria.
            if modern and value["status"] == "CONTINUE":
                completion_probe = {**value, "status": "TASK_COMPLETE"}
                probe_snapshot = support.snapshot(workspace)
                if visual_runtime.completion_allowed(state, current_snapshot=probe_snapshot) and completion_gate.completion_ready(state, completion_probe, probe_snapshot):
                    state.update(next_stage="astra_review", **unit_module("astra_review").completion_review(state, probe_snapshot))
                    state["iteration"] += 1
                    goals.record_decision(state, value)
                    save_record(state, record)
                    return
            if stage in ("astra_review", "astra_resolve"):
                state["iteration"] += 1
            if (stage == 'astra_resolve' and builder_policy.enabled(state)
                    and value.get('next_task', {}).get('kind') == 'implement'):
                request = state['resolution_request']
                action = builder_policy.failure(state, request.get('source_output', request.get('review_output')), value['diagnosis'])
                if action == 'pause':
                    # Exhaustion precedes assignment/replan gates and cannot be
                    # converted into another completion-owner/model round trip.
                    state['next_stage'] = 'terra'
                    goals.record_decision(state, value)
                    save_record(state, record)
                    return
            current = support.snapshot(workspace)
            try:
                kind = lifecycle.assign_task(state, value, current) if modern else "implement"
            except support.Paused as error:
                if not error.status.startswith("PAUSED_MILESTONE_"):
                    raise
                milestones.handle_gate(state, error, current, origin={'stage': stage, 'output': record['output']}, ask_user=lifecycle.wait_for_user)
                goals.record_decision(state, value)
                save_record(state, record)
                return
            validation_verdict = state.get("validation", {}).get("verdict")
            if validation_verdict in ("FAIL", "BLOCKED") and not (kind == 'implement' and builder_policy.enabled(state)):
                escalation.advance(state, "sol" if kind == "validate" else "terra",
                                   trigger="validation_rework",
                                   detail=f"{validation_verdict}: {value['next_objective']}",
                                   struggle_id=f"iteration:{record.get('iteration', state.get('iteration', 0))}")
            state.update(next_action=value["next_objective"], next_stage=workflow.review_stage(state) if kind == "validate" else
                         "terra" if progressive_state.enabled(state) else dispatch.build_stage(state))
            if stage == "astra_resolve":
                finish_resolution(state, value, record)
        if modern:
            goals.record_decision(state, value)
            state.pop("agent_request", None)
    elif stage == "terra":
        apply_build_result(runtime, state, value, record, workspace, run_dir)
    else:
        # Builder self-checks share evidence validation, but are not independently
        # dispatchable review stages (the final-audit workflow uses a copy).
        if stage != "self_check":
            unit_for(stage)
        apply_review_result(runtime, state, stage, value, record, workspace, run_dir)
    save_record(state, record)
    state.pop("stop_reason", None) if state["status"] == "RUNNING" else None


def run(runtime, state, workspace, run_dir, args):
    """Own stage admission, unit handoffs, recovery and approval checkpoints."""
    state_path = run_dir / "state.json"
    support, planning = runtime.support, runtime.planning
    milestones, workflow, interventions = runtime.milestones, runtime.workflow, runtime.interventions
    opencode = runtime.opencode
    consume_interventions = runtime.consume_interventions
    write_json, now = runtime.write_json, runtime.now
    timeout_recovery_guard = runtime.timeout_recovery_guard
    iteration_limit_reached = runtime.iteration_limit_reached
    check_joint_transports = runtime.check_joint_transports
    execute_report_repair, ReportRepairQueued = runtime.execute_report_repair, runtime.ReportRepairQueued
    chat_checkpoint = runtime.chat_checkpoint
    def before_code_stage(current):
        try:
            if consume_interventions(current, run_dir, workspace):
                print(f"{current['status']}: {current['stop_reason']}")
                raise LoopExit(2)
        except interventions.InterventionError as error:
            raise support.Paused("PAUSED_INTERVENTION_ACK", str(error)) from error
        if args.unit and pending_unit(current) != args.unit:
            publish_handoffs(current, run_dir)
            write_json(state_path, current)
            print(f"{args.unit}: handoff ready; next unit={pending_unit(current)}", flush=True)
            raise LoopExit(0)
        if milestones.apply_queued_activation(current, run_dir):
            print("Milestone checkpoints enabled at a safe boundary; continuing with independent validation.", flush=True)
        workflow.guard(current)
        if current.get("next_stage") in ("terra", "sol", "orchestrator", "completion"):
            try:
                from . import autocode_dispatch as dispatch
            except ImportError:
                import autocode_dispatch as dispatch
            dispatch.enforce_cross_model_verification(current)
        repairing_before_upgrade = (args.resume_paused and current.get('pending_report_repair')
                                    and milestones.owns_pause(run_dir))
        if (run_dir / "pause-requested").exists() and not repairing_before_upgrade:
            raise support.Paused("PAUSED_REQUESTED", "Pause requested; previous stage saved")
        limits = current["settings"]["limits"]
        timeout_recovery_guard(current)
        if iteration_limit_reached(current["iteration"], limits["iteration_ceiling"]):
            raise support.Paused("PAUSED_ITERATION_LIMIT", "Saved iteration ceiling reached")
        if limits["max_seconds"] and current.get("active_seconds",0) >= limits["max_seconds"]:
            raise support.Paused("PAUSED_TIME_LIMIT", "Saved active-time limit reached at stage boundary")
        if (not repairing_before_upgrade and (not milestones.enabled(current) or current.get('next_stage') in ('terra', 'orchestrator')) and limits["no_progress_batches"]
                and current.get("no_progress_batches",0) >= limits["no_progress_batches"]):
            raise support.Paused("PAUSED_NO_PROGRESS", "Repeated unchanged implementation batches require review")
        # Do not silently change auth/provider when local config changes.
        using_opencode = current["settings"].get("engine") == "opencode"
        if planning.enabled(current):
            check_joint_transports(current, workspace)
        current_settings = opencode.local_settings(workspace) if using_opencode else support.local_settings()
        drifted = (opencode.transport_drift(current_settings, current["settings"]["transport_identity"]) if using_opencode else
                   support.transport_drift(current_settings, current["settings"]["transport_identity"], current["settings"]["roles"]))
        if drifted:
            raise support.Paused("PAUSED_TRANSPORT_CHANGED", "Local model/auth/provider settings differ from checkpoint")
        if using_opencode and current["settings"]["transport_identity"].get("identity_version", 1) < 2:
            current.setdefault("configuration_changes", []).append({"at": now(),
                "reason": "Expanded OpenCode configuration identity; all previously recorded inputs match"})
            current["settings"]["transport_identity"] = current_settings
        if current.get('pending_report_repair'):
            try:
                execute_report_repair(current, run_dir, workspace)
            except ReportRepairQueued:
                pass
            return SKIP

    def dispatch_code_stage(current, stage):
        return dispatch_unit(runtime, current, stage, workspace, run_dir)

    def persist_code_stage(current):
        publish_handoffs(current, run_dir)
        write_json(state_path, current)

    def after_code_stage(current, stage, _record):
        print(f"{autocode_status.role_name(stage, current)}: saved; next={autocode_status.role_name(current['next_stage'], current) or 'none'}; status={current['status']}", flush=True)
        if milestones.enabled(current):
            print(milestones.status_line(current), flush=True)
        try:
            if consume_interventions(current, run_dir, workspace):
                print(f"{current['status']}: {current['stop_reason']}")
                raise LoopExit(2)
        except interventions.InterventionError as error:
            raise support.Paused("PAUSED_INTERVENTION_ACK", str(error)) from error
        if args.chat and current["status"] in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"):
            if not chat_checkpoint(current, run_dir):
                write_json(state_path, current)
                raise LoopExit(2)
            write_json(state_path, current)
        if args.pause_after_stage and current["status"] == "RUNNING":
            raise support.Paused("PAUSED_REQUESTED", "--pause-after-stage checkpoint reached")
    try:
        drive(state, dispatch_code_stage, before=before_code_stage,
                           persist=persist_code_stage,
                           after=after_code_stage)
    except LoopExit as stopped:
        return stopped.code
    return None


if __name__ == "__main__":
    # The dashboard and the macOS app run `python tools/autopilot.py ...`. The CLI lives in autocode.py,
    # the layer above this controller, so only this script entry imports it; importing autopilot does not.
    try:
        from . import autocode
    except ImportError:
        import autocode
    raise SystemExit(autocode.cli())
