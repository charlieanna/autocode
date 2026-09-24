"""Autopilot: deterministic controller for planning, building, review and repair."""
from __future__ import annotations

import copy
import json
from pathlib import Path
try:
    from . import autocode_support as support, autocode_goals as goals
    from . import autocode_workflow as workflow, autocode_milestones as milestones, autocode_escalation as escalation
    from . import autocode_findings as findings_ledger
    from .units import autoplanner as planning_unit
except ImportError:
    import autocode_support as support
    import autocode_goals as goals
    import autocode_workflow as workflow
    import autocode_milestones as milestones
    import autocode_escalation as escalation
    import autocode_findings as findings_ledger
    from units import autoplanner as planning_unit

SKIP = object()


class LoopExit(Exception):
    """Stop orchestration at a deliberate CLI boundary."""

    def __init__(self, code):
        super().__init__(code)
        self.code = code


def drive(state, dispatch, *, apply=None, before=None, after=None, persist=None,
          active=lambda value: value.get('status') == 'RUNNING'):
    """Run the saved state's next stage until its workflow reaches a boundary.

    Units own prompts and schemas; Autopilot owns code-workflow transitions. This function owns the
    common sequence: guard -> select saved stage -> dispatch -> apply -> persist.
    Returning SKIP from a hook restarts from the newly saved state without
    pretending a stage completed.
    """
    while active(state):
        if before and before(state) is SKIP:
            continue
        stage = state.get('next_stage')
        if not isinstance(stage, str) or not stage:
            raise ValueError('Running orchestration has no next stage')
        outcome = dispatch(state, stage)
        if outcome is SKIP:
            continue
        if apply:
            applied = apply(state, stage, outcome)
            if applied is SKIP:
                continue
        if persist:
            persist(state)
        if after:
            after(state, stage, outcome)
    return state

UNITS = ("autoplanner", "autocode", "autoreview", "autoresolver")


def unit_for(stage):
    if stage == "astra_resolve":
        return "autoresolver"
    if stage in ("requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"):
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
    try:
        from . import autocode_goals as goals, autocode_support as support
    except ImportError:
        import autocode_goals as goals
        import autocode_support as support
    from pathlib import Path
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


def dispatch_unit(runtime, state, stage, workspace, run_dir):
    """Call one unit using the runner's durable provider/recovery services."""
    runtime.milestones.dispatch_guard(state, stage)
    runtime.workflow.dispatch_guard(state, stage, workspace)
    unit = unit_module(stage)
    if stage == "orchestrator":
        return unit.dispatch(state, workspace, run_dir)
    state_path = run_dir / "state.json"
    request = unit.prepare(state, stage, state_path, runtime.SCHEMA_DIR)
    runtime.rotate_if_needed(state, request.route_role, run_dir)
    state["pending_context_metrics"] = request.metrics
    if request.metrics["estimated_prompt_tokens"] > request.metrics["soft_budget_tokens"]:
        print("Context soft budget exceeded; preserving complete requirements", flush=True)
    runtime.write_json(state_path, state)
    schema_path = run_dir / "schemas" / f"v3-{stage}.json"
    runtime.write_json(schema_path, runtime.support.model_output_schema(request.schema))
    try:
        value, record = runtime.run_role(role=request.role, prompt=request.prompt,
            sandbox="workspace-write" if request.allow_write else "read-only",
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
        if (runtime.automatically_recover_timed_out_stage(state, run_dir, workspace, error)
                or runtime.automatically_recover_external_directory_denial(state, run_dir, workspace, error)):
            print(f"{stage}: non-terminal attempt archived; continuing from recovery checkpoint", flush=True)
            return SKIP
        raise
    return record



def start_planning(state):
    if state.get("planning"):
        state.setdefault("planning_history", []).append(copy.deepcopy(state["planning"]))
    state["planning"] = {"astra_calls": 0, "reports": {}, "final_token": None}
    state.update(status="RUNNING", phase="PLANNING", next_stage="astra_challenge", pending_questions=[])


def apply_planning(state, stage, value, record):
    support.validate_schema(value, planning_unit.SCHEMAS[stage])
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
        handoff = state.get("requirements_handoff")
        if handoff:
            pending = {question["id"] for question in handoff["report"]["open_questions"]}
            preserved = {question["id"] for question in value["contract"]["open_blocking_questions"]}
            missing = pending - preserved - set(state.get("answers", {}))
            if missing:
                raise ValueError("Planner dropped unresolved requirements questions: " + ", ".join(sorted(missing)))
        goals.install_draft(state, value["contract"], origin="glm_draft")
        if state.get("pending_questions"):
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
        state["next_stage"] = "glm_revise"
    elif stage == "glm_revise":
        concerns = reports["astra_challenge"]["report"]["concerns"]
        planning_unit._coverage(value["responses"], concerns)
        if any(not r["evidence_refs"] for r in value["responses"]):
            raise ValueError("GLM responses must cite investigated evidence")
        goals.install_draft(state, value["contract"], origin=stage)
        state.update(status="RUNNING", phase="PLANNING", next_stage="astra_finalize", pending_questions=[])
    elif stage == "astra_finalize":
        concerns = reports["astra_challenge"]["report"]["concerns"]
        planning_unit._coverage(value["decisions"], concerns)
        unresolved = {d["concern_id"] for d in value["decisions"] if not d["resolved"]}
        if unresolved and not value["contract"]["open_blocking_questions"]:
            raise ValueError("Unresolved planning decisions must return to the user as blocking questions")
        if not value["contract"]["open_blocking_questions"] and "initial_task" not in value["contract"]:
            raise ValueError("Final plan needs an initial_task so approval does not spend another Astra call")
        goals.install_draft(state, value["contract"], origin=stage)
        planning["final_token"] = goals.token(state["goal_contract"])
    reports[stage] = {"report": copy.deepcopy(value), "output": record["output"]}
    state["discovery_summary"] = value["summary"]


def apply_planning_result(state, stage, value, record):
    if planning_unit.is_planning(state, stage):
        apply_planning(state, stage, value, record)
        return
    from pathlib import Path
    schema = support.read(Path(record["schema"])) if record.get("schema") else goals.DISCOVERY_SCHEMA
    support.validate_schema(value, schema)
    legacy = not any(key in schema["properties"]["contract"]["properties"] for key in goals.BRIEF_FIELDS)
    goals.install_draft(state, value["contract"], origin="astra_discovery", allow_legacy=legacy)
    state["discovery_summary"] = value["summary"]


def apply_build_result(runtime, state, value, record, workspace, run_dir):
    support.evidence_hashes(support.implementation_evidence_paths(value["evidence_refs"], record["events"]), workspace, run_dir)
    state.update(implementation={**value, "source_revision": record.get("source_revision"),
                                 "workspace": str(workspace)},
                 changed_files=record["changed_files"], source_snapshot=record["after_ref"],
                 next_stage=workflow.review_stage(state), diff_ref=record.get("diff_ref"))
    if not record["changed_files"]:
        state["no_progress_batches"] = state.get("no_progress_batches", 0) + 1
        escalation.advance(state, "terra", trigger="no_progress",
                           detail="Builder completed a batch without source changes",
                           struggle_id=f"iteration:{record.get('iteration', state.get('iteration', 0))}")
    else:
        state["no_progress_batches"] = 0
    if workflow.final_only(state):
        workflow.apply_implementation(runtime,state,value,record,workspace,run_dir)


def apply_review_result(runtime, state, stage, value, record, workspace, run_dir):
    modern = state.get("version", 2) >= 3
    support.verify_checks(value["checks"], workspace, record["events"],
                          **runtime.check_evidence_options(record))
    refs = [c["evidence_ref"] for c in value["checks"]]
    for check in value["checks"]:
        if not check["evidence_ref"].startswith("event:"):
            receipt_path = Path(check["evidence_ref"])
            receipt_path = receipt_path if receipt_path.is_absolute() else workspace / receipt_path
            refs.append(support.read(receipt_path)["full_output"])
    refs += [p for row in value["criterion_results"] for p in row["evidence_refs"]]
    flow = value.get("end_to_end_result", {})
    refs += flow.get("evidence_refs", [])
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
    ids = [row["id"] for row in value["criterion_results"]]
    known = {c["id"] for c in state["acceptance_criteria"]}
    if len(ids) != len(set(ids)) or not set(ids) <= known:
        raise ValueError("Sol criterion results must use unique approved IDs")
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
    if value["verdict"] == "PASS" and (not value["checks"] or any(c["exit_code"] for c in value["checks"])):
        raise support.Paused("PAUSED_INVALID_OUTPUT", "Sol PASS lacks successful executed checks")
    if state.get("validation"):
        state.setdefault("validation_archive", []).append({
            "reason": "Superseded by another independent validation", "validation": state["validation"]})
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


def queue_resolution(state, decision, record):
    if support.criteria_definition(decision['acceptance_criteria']) != support.criteria_definition(state['acceptance_criteria']):
        raise support.Paused('PAUSED_CRITERIA_CHANGE', 'Repair cannot change approved acceptance criteria')
    # Retain the reviewer's unverified statuses even while diagnosis is pending.
    state['acceptance_criteria'] = copy.deepcopy(decision['acceptance_criteria'])
    pins = dict(state.get('validation', {}).get('evidence_hashes', {}))
    pins[record['output']] = support.file_hash(Path(record['output']))
    state['resolution_request'] = {
        'contract_hash': state['goal_contract']['hash'],
        'task_id': state.get('current_task', {}).get('id'),
        'source_revision': record['source_revision'],
        'review': copy.deepcopy(decision),
        'review_output': record['output'],
        'evidence_hashes': pins,
    }
    state['next_stage'] = 'astra_resolve'


def finish_resolution(state, value, record):
    request = state.pop('resolution_request')
    plan = {'kind': 'repair-plan', 'version': 1,
            'contract_hash': request['contract_hash'], 'source_revision': request['source_revision'],
            'diagnosis': value['diagnosis'], 'evidence': value['evidence'],
            'review_output': request['review_output'], 'evidence_hashes': request['evidence_hashes'],
            'output': record['output'],
            'tasks': [{**copy.deepcopy(state['current_task']), 'depends_on': []}]}
    state['repair_plan'] = plan
    state.setdefault('resolution_history', []).append(copy.deepcopy(plan))


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
    if stage == "astra_resolve":
        unit_module(stage).validate(state, value, record, workspace)
    if stage == "astra_checkpoint":
        workflow.apply_checkpoint(runtime, state, value, record, workspace, run_dir)
        return
    modern = state.get("version", 2) >= 3
    if planning.is_planning(state, stage) or (modern and stage == "astra_discovery"):
        apply_planning_result(state, stage, value, record)
        save_record(state, record)
        return
    if modern:
        goals.execution_guard(state, value)
        for entry in value.get("deferred_backlog", []):
            if entry not in state.setdefault("deferred_backlog", []):
                state["deferred_backlog"].append(entry)
        if value["user_request"]["kind"] != "none":
            if workflow.final_only(state) and not stage.startswith('astra'):
                goals.wait_for_user(state,value['user_request'])
                save_record(state,record)
                return
            if stage.startswith("astra"):
                if value["status"] != "BLOCKED":
                    raise ValueError("Astra must choose BLOCKED when requesting a user decision")
                goals.wait_for_user(state, value["user_request"])
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
        queue_resolution(state, value, record)
        save_record(state, record)
        return
    if stage.startswith("astra"):
        definitions = support.criteria_definition(value["acceptance_criteria"])
        if len({c["id"] for c in definitions}) != len(definitions):
            raise support.Paused("PAUSED_INVALID_OUTPUT", "Duplicate acceptance IDs")
        old = state.get("acceptance_criteria", [])
        if old and definitions != support.criteria_definition(old):
            raise support.Paused("PAUSED_CRITERIA_CHANGE", "Astra proposed a criteria change; previous revision remains authoritative")
        state["acceptance_criteria"] = value["acceptance_criteria"]
        state["criteria_revision"] = support.digest(definitions)
        state["plan"] = value.get("plan", [value["next_objective"]])
        state["affected_paths"] = value.get("affected_paths", [])
        if modern:
            findings_ledger.record_decision(state, value, record)
        if value["status"] in ("COMPLETE", "TASK_COMPLETE"):
            current = support.snapshot(workspace)
            if modern and goals.missing_human_reviews(state):
                if not support.completion_ready(state, value, current, require_human_reviews=False):
                    raise support.Paused("PAUSED_COMPLETION_GATE", "Artifact review requires current passing independent evidence first")
                state.update(status="WAITING_FOR_USER", phase="WAITING_FOR_USER", next_stage="astra_review",
                    user_request={"kind": "human_review", "criteria": goals.missing_human_reviews(state),
                                  "decision_needed": "Review the current artifact and explicitly approve the listed criteria"})
                goals.record_decision(state, value)
                save_record(state, record)
                return
            if not support.completion_ready(state, value, current):
                raise support.Paused("PAUSED_COMPLETION_GATE", "Completion rejected: missing, stale, failed or unverified independent evidence")
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
            # Passing Sol evidence cannot override Astra's rework or unverified criteria.
            if modern and value["status"] == "CONTINUE":
                completion_probe = {**value, "status": "TASK_COMPLETE"}
                if support.completion_ready(state, completion_probe, support.snapshot(workspace)):
                    state.update(status="PAUSED_COMPLETION_REVIEW", phase="PAUSED_OR_BLOCKED", next_stage="astra_review",
                        stop_reason="All required criteria already pass; request completion instead of another implementation batch")
                    state["iteration"] += 1
                    goals.record_decision(state, value)
                    save_record(state, record)
                    return
            if stage in ("astra_review", "astra_resolve"):
                state["iteration"] += 1
            current = support.snapshot(workspace)
            try:
                kind = goals.assign_task(state, value, current) if modern else "implement"
            except support.Paused as error:
                if not error.status.startswith("PAUSED_MILESTONE_"):
                    raise
                milestones.handle_gate(state, error, current)
                goals.record_decision(state, value)
                save_record(state, record)
                return
            validation_verdict = state.get("validation", {}).get("verdict")
            if validation_verdict in ("FAIL", "BLOCKED"):
                escalation.advance(state, "sol" if kind == "validate" else "terra",
                                   trigger="validation_rework",
                                   detail=f"{validation_verdict}: {value['next_objective']}",
                                   struggle_id=f"iteration:{record.get('iteration', state.get('iteration', 0))}")
            state.update(next_action=value["next_objective"], next_stage=workflow.review_stage(state) if kind == "validate" else dispatch.build_stage(state))
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
        if limits["max_reported_tokens"]:
            measured = [r.get("metrics",{}).get("provider_tokens",{}) for r in current.get("stages",[])]
            if any(m.get("input_tokens") is None or m.get("output_tokens") is None for m in measured):
                raise support.Paused("PAUSED_USAGE_UNKNOWN", "Cannot enforce requested token limit with unknown usage")
            if sum(m["input_tokens"]+m["output_tokens"] for m in measured) >= limits["max_reported_tokens"]:
                raise support.Paused("PAUSED_BUDGET", "Saved reported-token limit reached")
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
        print(f"{stage}: saved; next={current['next_stage']}; status={current['status']}", flush=True)
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


def cli():
    try:
        from . import autocode
    except ImportError:
        import autocode
    return autocode.cli()


if __name__ == "__main__":
    raise SystemExit(cli())
