"""Autopilot: deterministic controller for planning, building, review and repair."""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path
try:
    from . import autocode_support as support, autocode_goals as goals, autocode_jobs as jobs
    from . import autocode_stuck_job as stuck
    from . import autocode_planning_artifacts as planning_artifacts, autocode_planning_graph as planning_graph
    from . import autocode_workflow as workflow, autocode_milestones as milestones, autocode_escalation as escalation
    from . import autocode_findings as findings_ledger, autocode_builder_policy as builder_policy
    from . import autocode_resolver_human as human, autocode_failures as failures
    from .units import autoplanner as planning_unit
    from . import autocode_regression as regression
except ImportError:
    import autocode_regression as regression
    import autocode_support as support, autocode_jobs as jobs
    import autocode_stuck_job as stuck
    import autocode_goals as goals
    import autocode_planning_artifacts as planning_artifacts
    import autocode_planning_graph as planning_graph
    import autocode_workflow as workflow
    import autocode_milestones as milestones
    import autocode_escalation as escalation
    import autocode_findings as findings_ledger
    import autocode_builder_policy as builder_policy
    import autocode_resolver_human as human
    import autocode_failures as failures
    from units import autoplanner as planning_unit

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


def dispatch_unit(runtime, state, stage, workspace, run_dir):
    """Call one unit using the runner's durable provider/recovery services."""
    if stage == 'terra':
        builder_policy.guard(state)
    runtime.milestones.dispatch_guard(state, stage)
    runtime.workflow.dispatch_guard(state, stage, workspace)
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
    saved_review_limit = state.get('settings', {}).get('planning_review_call_limit')
    if type(saved_review_limit) is int and saved_review_limit == 0:
        state['planning'].update(review_call_limit=0, review_call_limit_origin='user_explicit')
    state.pop(human.PRIVATE, None)
    state.pop(human.PUBLIC, None)
    state.pop("user_request", None)
    next_stage = "plan_review" if state.get("settings", {}).get("planning_flow") == "v2" else "astra_challenge"
    state.update(status="RUNNING", phase="PLANNING", next_stage=next_stage, pending_questions=[])


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
        raw, _, line_text = str(ref).partition(":")
        target = (root / raw).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError(f"{field} entry {ref} is not a file in the workspace")
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
    goals.check_requirement_trace(state, value, value["contract"])
    if origin in ("glm_draft", "glm_revise"):
        _check_code_refs(state, value.get("code_refs") or [])
    goals.install_draft(state, value["contract"], origin=origin, changes=value.get("contract_changes") or [], record=record)


# Only a technical fact, or one with no policy weight, can be read from the
# workspace. Cost, quota, permission, side-effect and requested-outcome choices
# (and any question without a category) always stay with the user.
RESOLVABLE_CATEGORIES = frozenset({"technical", "other"})


def _stage_questions(stage, value):
    return value["open_questions"] if stage == "requirements_gather" else value["contract"]["open_blocking_questions"]


def _check_resolution_refs(state, refs):
    """Strict: unlike _check_code_refs, an absent or empty workspace is not a pass."""
    root = Path(state.get("workspace") or "")
    if not state.get("workspace") or not root.is_dir():
        raise ValueError("A machine resolution needs an inspectable workspace")
    if not refs:
        raise ValueError("A machine resolution must cite the workspace source it was read from")
    for ref in refs:
        target = (root / str(ref).partition(":")[0]).resolve()
        if not target.is_relative_to(root.resolve()) or not target.is_file():
            raise ValueError(f"machine_resolutions source_refs entry {ref} is not a file in the workspace")
    _check_code_refs(state, refs, "machine_resolutions.source_refs")


def _clarify_discoverable(state, stage, value, record):
    """Keep discoverable questions away from the user (issue #62, rules E1-E3).

    The first report in a clarification episode that asks a discoverable
    question is not installed: the runner spends the episode's single
    investigation pass by re-running the same stage with an explicit packet
    (the questions, the report that raised them, and its hash). That pass must
    resolve each question from cited workspace source, reclassify it as a
    decision, or record an access blocker. Whatever is still discoverable after
    the pass, or once the budget is spent, becomes a decision question the
    runner labels as such; there is no second pass and no fabricated answer.

    Returns (value, deferred); deferred means the pass was queued.
    """
    if stage == "astra_finalize":
        # The final reviewer stage gets no investigation pass; it still cannot
        # show the user a question labelled as a workspace fact.
        value = copy.deepcopy(value)
        _label_unresolved(state, value["contract"]["open_blocking_questions"])
        return value, False
    if stage not in planning_unit.INVESTIGATION_STAGES:
        return value, False
    request = state.get("investigation_request")
    if request and request.get("stage") != stage:
        request = None
    raised_hash = support.digest(value)
    value = copy.deepcopy(value)
    questions = _stage_questions(stage, value)
    by_id = {question["id"]: question for question in questions}
    resolutions = value.get("machine_resolutions") or []
    if resolutions and not request:
        raise ValueError("machine_resolutions are accepted only in the investigation pass for outstanding "
                         "discoverable questions")
    accepted, resolved = [], set()
    for row in resolutions:
        question_id = row["question_id"]
        original = next((q for q in request["questions"] if q["id"] == question_id), None)
        if question_id in resolved:
            raise ValueError(f"Duplicate machine_resolution for {question_id}")
        resolved.add(question_id)
        if original is None:
            raise ValueError(f"machine_resolution {question_id} does not name an outstanding discoverable question")
        if question_id in state.get("answers", {}):
            raise ValueError(f"Question {question_id} already has a saved user answer")
        category = original.get("category", "requested_outcome")
        if category not in RESOLVABLE_CATEGORIES:
            raise ValueError(f"Question {question_id} is a {category} choice; only a technical fact can be "
                             "resolved from the workspace")
        if row["handoff_hash"] != request["handoff_hash"]:
            raise ValueError(f"machine_resolution {question_id} is bound to a different report")
        if not row["resolution"].strip():
            raise ValueError(f"machine_resolution {question_id} needs the resolved fact")
        _check_resolution_refs(state, row["source_refs"])
        if question_id in by_id:
            raise ValueError(f"Question {question_id} was resolved from the workspace and must not also be asked")
        accepted.append({**copy.deepcopy(row), "stage": stage, "episode_id": request["episode_id"],
                         "requirements_handoff": goals.handoff_ref(state), "at": support.now()})
    for row in value.get("access_blockers") or []:
        question = by_id.get(row["question_id"])
        if not row["reason"].strip() or question is None or question.get("kind", "decision") != "decision":
            raise ValueError("An access_blocker must name a question that stays open as kind=decision, with a reason")
    if request:
        # Every question the held-back report asked must survive the pass, not
        # just the discoverable ones: the original report was never installed,
        # so no later gate could recover a decision dropped here.
        asked = set(request.get("all_question_ids") or request["question_ids"])
        dropped = asked - resolved - set(by_id) - set(state.get("answers", {}))
        if dropped:
            raise ValueError("Investigation dropped questions without a machine_resolution: "
                             + ", ".join(sorted(dropped)))
        state.setdefault("machine_resolutions", []).extend(accepted)
        state.pop("investigation_request")
    else:
        discoverable = [question for question in questions if question.get("kind") == "discoverable"]
        episode = goals.clarification_episode(state) if discoverable else None
        if episode and not episode["investigation_used"]:
            episode.update(investigation_used=True, used_at=support.now(), used_stage=stage)
            state["investigation_request"] = {
                "stage": stage, "episode_id": episode["id"], "handoff_hash": raised_hash,
                "question_ids": [question["id"] for question in discoverable],
                "all_question_ids": [question["id"] for question in questions],
                "questions": copy.deepcopy(discoverable), "prior_output": record.get("output"),
                "prior_report": copy.deepcopy(value), "created_at": support.now()}
            state.update(status="RUNNING", phase="PLANNING", next_stage=stage)
            return value, True
    _label_unresolved(state, questions)
    return value, False


def _label_unresolved(state, questions):
    """Runner-authored, visible relabelling; never a fabricated answer."""
    for index, question in enumerate(questions):
        if question.get("kind") == "discoverable":
            questions[index] = {**question, "kind": "decision", "why": (
                "Not determinable from the workspace within this clarification episode's single "
                "investigation pass. " + question["why"])}
            goals.clarification_episode(state).setdefault("converted_to_decision", []).append(question["id"])


def _surface_obligations(obligations, questions, where):
    """An unresolved obligation returns to the user as a decision question under
    its own id. The three-question cap limits presentation per round, not the
    obligation: with the cap full it waits for the next round."""
    ids = {ob["id"] for ob in obligations}
    for question in questions:
        if question["id"] in ids and question.get("kind", "decision") != "decision":
            raise ValueError(f"Obligation {question['id']} must be asked as a kind=decision question")
    missing = sorted(ids - {question["id"] for question in questions})
    if missing and len(questions) < 3:
        raise ValueError(f"{where}: unresolved obligations must return to the user as decision questions "
                         "under their own ids: " + ", ".join(missing))


def _apply_obligations(state, stage, value):
    """Rule E8 (issue #62): rejected assumptions become runner-owned obligations.

    A remediation obligation is discharged only by a Plan Reviewer decision on
    astra_challenge or astra_finalize, bound to the hash of the exact remediation
    record it judged; a repaired record resets it to pending review. A human
    decision obligation, or any obligation still open at final review, goes back
    to the user as a question under its own id. Agent output alone never
    discharges an obligation. At finalize, the report's own decisions are applied
    before the gate is evaluated.
    """
    obligations = {ob["id"]: ob for ob in goals.open_obligations(state)}
    if stage == "requirements_gather":
        # Resolving how to proceed without an assumption is not permission to
        # restore it; there is no reinstatement path, so history always applies.
        rejected = {ob.get("assumption_id") for ob in state.get("deferred_obligations", [])}
        reused = sorted(rejected & {row.get("id") for row in value.get("proposed_assumptions") or []
                                    if isinstance(row, dict)})
        if reused:
            raise ValueError("A rejected assumption cannot reappear: " + ", ".join(reused))
        return
    if stage in ("astra_discovery", "glm_revise"):
        trace = {row.get("requirement_id"): row.get("disposition") for row in value.get("requirement_trace") or []}
        episode_id = goals.clarification_episode(state)["id"]
        seen = set()
        for record in value.get("remediation_records") or []:
            obligation_id = record["obligation_id"]
            obligation = obligations.get(obligation_id)
            if obligation_id in seen:
                raise ValueError(f"Duplicate remediation record for {obligation_id}")
            seen.add(obligation_id)
            if obligation is None or obligation["kind"] != "remediation":
                raise ValueError(f"remediation_records {obligation_id} does not name an open remediation obligation")
            if record["assumption_id"] != obligation["assumption_id"]:
                raise ValueError(f"remediation_records {obligation_id} names a different assumption")
            if not record["approach"].strip() or not record["evidence_refs"]:
                raise ValueError(f"remediation_records {obligation_id} needs an approach and evidence")
            _check_code_refs(state, record["evidence_refs"], "remediation_records.evidence_refs")
            covered = record["covered_requirements"]
            if len(covered) != len(set(covered)) or set(covered) != set(obligation.get("supports") or []):
                raise ValueError(f"remediation_records {obligation_id} must cover exactly the requirements "
                                 "the rejected assumption supported")
            uncovered = sorted(rid for rid in covered if trace.get(rid) != "covered")
            if uncovered:
                raise ValueError(f"remediation_records {obligation_id} claims requirements the trace does not "
                                 "cover: " + ", ".join(uncovered))
            if record["episode_id"] != episode_id:
                raise ValueError(f"remediation_records {obligation_id} is bound to a previous clarification episode")
            obligation.update(remediation=copy.deepcopy(record), remediation_hash=support.digest(record),
                              status="pending_review")
        if stage == "astra_discovery":
            human = [ob for ob in obligations.values() if ob["kind"] == "human_decision"]
            if human:
                contract = value["contract"]
                if (contract.get("milestones") or contract.get("technical_approach")
                        or contract.get("initial_task", {}).get("kind") in ("implement", "validate")):
                    raise ValueError("A rejected assumption with policy weight needs the user's decision first; "
                                     "return a clarification-only draft")
                _surface_obligations(human, contract["open_blocking_questions"], "Discovery")
        return
    if stage not in ("astra_challenge", "astra_finalize"):
        return
    report_hash = support.digest(value)
    pending = {oid: ob for oid, ob in obligations.items() if ob["status"] == "pending_review"}
    decided = set()
    for decision in value.get("obligation_decisions") or []:
        obligation_id = decision["obligation_id"]
        obligation = pending.get(obligation_id)
        if obligation_id in decided:
            raise ValueError(f"Duplicate obligation decision for {obligation_id}")
        decided.add(obligation_id)
        if obligation is None:
            raise ValueError(f"obligation_decisions {obligation_id} does not name a remediation awaiting review")
        if decision["remediation_hash"] != obligation["remediation_hash"]:
            raise ValueError(f"Decision for {obligation_id} refers to a superseded remediation")
        if (obligation.get("remediation") or {}).get("episode_id") != goals.clarification_episode(state)["id"]:
            raise ValueError(f"Remediation {obligation_id} was proposed in a previous clarification episode; "
                             "it must be proposed again")
        if decision["resolved"]:
            if not decision["rationale"].strip() or not decision["evidence_refs"]:
                raise ValueError(f"Accepting remediation {obligation_id} needs a rationale and evidence")
            obligation.update(status="resolved", resolved_by=f"{stage}:{report_hash}:{obligation_id}",
                              judged_hash=obligation["remediation_hash"], resolved_at=support.now())
        else:
            if stage == "astra_challenge" and not any(
                    concern.get("blocking") and obligation_id in (concern.get("evidence_refs") or [])
                    for concern in value["concerns"]):
                raise ValueError(f"Rejecting remediation {obligation_id} needs a blocking concern citing it")
            obligation["status"] = "open"
    missing = sorted(set(pending) - decided)
    if missing:
        raise ValueError("Every remediation awaiting review needs one obligation_decisions entry: "
                         + ", ".join(missing))
    if stage == "astra_finalize":
        remaining = goals.open_obligations(state)
        if remaining:
            contract = value["contract"]
            if contract.get("initial_task", {}).get("kind") in ("implement", "validate"):
                raise ValueError("Unresolved obligations block an executable initial_task")
            _surface_obligations(remaining, contract["open_blocking_questions"], "Final review")


def apply_planning(state, stage, value, record, *, run_dir=None):
    # Older saved reports predate explicit, user-backed conflict resolutions.
    # An absent list supplies no authority to resolve any conflict.
    if "conflict_resolutions" in planning_unit.SCHEMAS[stage]["properties"]:
        value = {"conflict_resolutions": [], **value}
    if stage in planning_unit.V2_STAGES:
        support.validate_schema(value, planning_unit.SCHEMAS[stage])
        prepared = planning_artifacts.prepare(state, stage, value, origin=stage,
                                              run_dir=run_dir, record=False)
        if stage == "requirements":
            goals.apply_requirements(state, value["requirements"],
                                     artifact_sha256=prepared["artifact"]["sha256"], record=record)
            planning = state.setdefault("planning", {"astra_calls": 0, "reports": {}, "final_token": None})
        else:
            planning = state.setdefault("planning", {"astra_calls": 0, "reports": {}, "final_token": None})
            reports = planning["reports"]
            if stage == "plan":
                derived = planning_graph.validate(value["contract"])
                goals.install_draft(state, value["contract"], origin="plan", record=record)
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
                goals.install_draft(state, value["contract"], origin="plan_revise", record=record)
                planning["derived_graph"] = derived
            elif stage == "plan_finalize":
                planning_unit._coverage(value["decisions"], reports["plan_review"]["report"]["concerns"])
                unresolved = {row["concern_id"] for row in value["decisions"] if not row["resolved"]}
                if unresolved and not value["contract"]["open_blocking_questions"]:
                    raise ValueError("Unresolved planning decisions must return as blocking questions")
                if not value["contract"]["open_blocking_questions"] and "initial_task" not in value["contract"]:
                    raise ValueError("Final plan needs an initial_task before approval")
                derived = planning_graph.validate(value["contract"])
                goals.install_draft(state, value["contract"], origin="plan_finalize", record=record)
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
    support.validate_schema(checked, planning_unit.SCHEMAS[stage])
    if stage == planning_unit.RECOGNIZE:
        return planning_unit.recognize(state, value, record)
    value, deferred = _clarify_discoverable(state, stage, value, record)
    if deferred:
        return
    _apply_obligations(state, stage, value)
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
        handoff = state.get("requirements_handoff")
        if handoff:
            pending = {question["id"] for question in handoff["report"]["open_questions"]}
            preserved = {question["id"] for question in value["contract"]["open_blocking_questions"]}
            # A fact settled from the workspace stays settled while the handoff
            # that asked it is unchanged; answering another question renews the
            # episode but not the handoff. A refreshed handoff must settle it again.
            current = goals.handoff_ref(state)
            resolved = {row["question_id"] for row in state.get("machine_resolutions", [])
                        if row.get("requirements_handoff") == current}
            missing = pending - preserved - set(state.get("answers", {})) - resolved
            if missing:
                raise ValueError("Planner dropped unresolved requirements questions: " + ", ".join(sorted(missing)))
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
        state["next_stage"] = "glm_revise"
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
        state.update(status="RUNNING", phase="PLANNING", next_stage="astra_finalize", pending_questions=[])
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
    goals.install_draft(state, value["contract"], origin="astra_discovery", allow_legacy=legacy, record=record)
    state["discovery_summary"] = value["summary"]


def assert_within_assignment(state, record):
    """A serial Builder gets the same ownership gate as a parallel worktree.

    The declared affected_paths are the assignment boundary. Evidence is the
    actual tree delta, never the report's changed_files list. Tasks without
    explicit ownership (legacy contracts, [] for serial dispatch) are unbounded.
    """
    owned = (state.get("current_task") or {}).get("affected_paths") or []
    if not owned or not state.get("goal_contract"):
        return
    try:
        from . import autocode_dispatch as dispatch
    except ImportError:
        import autocode_dispatch as dispatch
    outside = sorted(name for name in record.get("changed_files", [])
                     if not any(dispatch.contains(root, name) for root in owned))
    if outside:
        raise support.Paused("PAUSED_ASSIGNMENT_SCOPE",
                             "Builder changed files outside the assigned paths; edits retained for inspection: "
                             + ", ".join(outside))


def retained_validated_candidate(state, value, record, workspace):
    """Recognize preserved work only to route it through fresh validation."""
    if record.get('changed_files') or not isinstance(value.get('changed_files'), list):
        return None
    declared = set(value['changed_files'])
    affected = set((state.get('current_task') or {}).get('affected_paths') or [])
    if (not declared or not affected or not declared <= affected
            or not value.get('commands_run') or not value.get('evidence_refs')
            or any(not (Path(workspace) / path).is_file() for path in declared)):
        return None
    revision = support.snapshot(workspace)['revision']
    criteria = {row['id'] for row in (state.get('goal_contract') or {}).get('body', {}).get('acceptance_criteria', [])}
    if not criteria or (state.get('current_task') or {}).get('source_revision') != revision:
        return None
    for archived in reversed(state.get('validation_archive', [])):
        validation = archived.get('validation') or {}
        results = {row.get('id'): row.get('status') for row in validation.get('criterion_results', [])}
        if (validation.get('verdict') == 'PASS' and validation.get('source_revision') == revision
                and criteria <= {cid for cid, status in results.items() if status == 'PASS'}):
            return {'source_revision': revision, 'validation_output': validation.get('output'),
                    'criteria': sorted(criteria), 'declared_paths': sorted(declared)}
    return None


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
    if value["verdict"] == "PASS" and (not value["checks"] or any(c["exit_code"] for c in value["checks"])):
        raise support.Paused("PAUSED_INVALID_OUTPUT", "Validator PASS lacks successful executed checks")
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
        raise support.Paused('PAUSED_RESOLVER', 'This task and source already received a resolver diagnosis; '
                             'the existing blocker must be reconciled before another diagnosis')
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
    state.setdefault('resolution_history', []).append(copy.deepcopy(plan))


def apply_diagnosis_result(runtime, state, value, record, workspace, run_dir):
    """Validate a model's bounded diagnosis and, only if accepted, retry its
    original failed stage. A rejected or escalated diagnosis pauses; it never
    grants a second chance beyond the two-evaluation budget already spent
    across admission and this completion.
    """
    unit_module('astra_diagnose').validate_diagnosis(state, value, record, workspace)
    runtime.resolver_runtime.finish_operational_diagnosis(state, run_dir, value['recommendation'])


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
                    goals.wait_for_user(state, request, origin=origin,
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
                    goals.wait_for_user(state, request, origin=origin, evidence=evidence,
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
        # The reviewer's findings are authoritative state; record them before the
        # resolver diagnosis consumes the rejection. The decision itself is recorded
        # when the resolver's bounded repair is applied, so it is not duplicated.
        findings_ledger.record_decision(state, value, record)
        queue_resolution(state, value, record)
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
        if value["status"] in ("COMPLETE", "TASK_COMPLETE"):
            current = support.snapshot(workspace)
            if modern and findings_ledger.blocking_entries(state):
                raise support.Paused("PAUSED_COMPLETION_GATE", "Completion rejected: the findings ledger still lists "
                                     "open blocking findings; resolve or retract each one with evidence")
            if modern and goals.missing_human_reviews(state):
                if not support.completion_ready(state, value, current, require_human_reviews=False):
                    raise support.Paused("PAUSED_COMPLETION_GATE", "Artifact review requires current passing independent evidence first")
                goals.wait_for_user(state,
                    {"kind": "human_review", "criteria": goals.missing_human_reviews(state),
                     "decision_needed": "Review the current artifact and explicitly approve the listed criteria",
                     "impact": "Completion requires the declared human acceptance of this validated artifact",
                     "options": [], "discovered": "Independent evidence passed; human review remains", "proposed_delta": ""},
                    origin={'stage': stage, 'output': record['output'], 'source_revision': current['revision']},
                    next_stage='astra_review')
                goals.record_decision(state, value)
                save_record(state, record)
                return
            if not support.completion_ready(state, value, current):
                if not regression.complete(state, current["revision"]):
                    proof = state.get("regression_proof") or {}
                    reasons = "; ".join((proof.get("failures") or []) + (proof.get("unverified") or [])) or \
                        "no proof exists for the current source"
                    raise support.Paused("PAUSED_COMPLETION_GATE", "Completion rejected: this change has no passing "
                                         f"regression proof for the current source ({reasons})")
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
            # Passing Validator evidence cannot override the Plan Reviewer's rework or unverified criteria.
            if modern and value["status"] == "CONTINUE":
                completion_probe = {**value, "status": "TASK_COMPLETE"}
                probe_snapshot = support.snapshot(workspace)
                if support.completion_ready(state, completion_probe, probe_snapshot):
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
                kind = goals.assign_task(state, value, current) if modern else "implement"
            except support.Paused as error:
                if not error.status.startswith("PAUSED_MILESTONE_"):
                    raise
                milestones.handle_gate(state, error, current, origin={'stage': stage, 'output': record['output']})
                goals.record_decision(state, value)
                save_record(state, record)
                return
            validation_verdict = state.get("validation", {}).get("verdict")
            if validation_verdict in ("FAIL", "BLOCKED") and not (kind == 'implement' and builder_policy.enabled(state)):
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
        support.enforce_reported_token_limit(current)
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
