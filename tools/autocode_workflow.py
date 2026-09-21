"""Opt-in reviewer routing for the existing runner; no provider or state store."""
from __future__ import annotations

import copy
from pathlib import Path

try:
    from . import autocode_support as support, autocode_goals as goals
except ImportError:
    import autocode_support as support
    import autocode_goals as goals

MODE = "glm_first_v1"
FINAL_MODE = "glm_final_audit_v2"
FINAL_APPROVAL = (
    "GLM owns technical planning, implementation, tests, routine fixes and continuation "
    "across approved milestones. Sol runs only for a concrete GLM debugging escalation. "
    "Astra runs only for the final full-task independent audit and any necessary final "
    "audit recheck. Preserve the approved goal, criteria, evidence checks, human approvals, "
    "permissions, models, sessions and limits; no automatic milestone GPT review."
)
APPROVAL_TEXT = (
    "GLM owns substantial implementation, tests and routine fixes. Astra performs "
    "independent milestone validation and completion judgment in one read-only call. "
    "Sol is a targeted escalation only. Existing Sol reviewer references mean the "
    "independent reviewer responsibility, now assigned to Astra; evidence requirements, "
    "scope, permissions, human reviews and execution limits are unchanged."
)


def enabled(state):
    return state.get("settings", {}).get("workflow", {}).get("mode") in (MODE, FINAL_MODE)


def final_only(state):
    return state.get("settings", {}).get("workflow", {}).get("mode") == FINAL_MODE


def guard(state):
    if state.get('settings', {}).get('milestone_checkpoints', {}).get('enabled') and enabled(state):
        raise support.Paused('PAUSED_WORKFLOW_CONFLICT', 'Milestone checkpoints require Terra → Sol → Astra routing')
    if not enabled(state):
        return
    policy = state["settings"]["workflow"]
    event = policy.get("approval_event", {})
    if (event.get("kind") != "workflow_approval" or event.get("actor") != "user"
            or event.get("policy") != (FINAL_APPROVAL if final_only(state) else APPROVAL_TEXT) or event not in state.get("user_events", [])
            or event.get("contract_hash") != state.get("goal_contract", {}).get("hash")):
        raise support.Paused("PAUSED_WORKFLOW_APPROVAL", "Reviewer routing needs approval for this goal revision")


def review_stage(state):
    guard(state)
    if final_only(state):
        return "terra"
    return "astra_checkpoint" if enabled(state) else "sol"


def activate_final(state, *, approval_source):
    if state.get('settings', {}).get('milestone_checkpoints', {}).get('enabled'):
        raise ValueError('Enforced milestone checkpoints cannot use final-only review routing')
    if state.get('active_stage') or state.get('pending_report_repair') or state.get('uncertain_artifacts') or state['status']=='RUNNING':
        raise ValueError('Activate final-only routing only at a reconciled idle checkpoint')
    if not goals.approved(state):
        raise ValueError('An approved goal is required')
    if final_only(state):
        guard(state)
        return
    old=copy.deepcopy(state['settings'].get('workflow'))
    event={'kind':'workflow_approval','actor':'user','at':support.now(),'policy':FINAL_APPROVAL,
           'contract_hash':state['goal_contract']['hash'],'source':approval_source}
    state.setdefault('user_events',[]).append(event)
    state['settings']['workflow']={'mode':FINAL_MODE,'approval_event':event}
    state.setdefault('configuration_changes',[]).append({'at':support.now(),'reason':FINAL_APPROVAL,
        'previous_workflow':old,'previous_next_stage':state.get('next_stage')})
    if state['status'] not in ('TASK_COMPLETE','WAITING_FOR_USER','AWAITING_GOAL_APPROVAL'):
        state['next_stage']='terra'
    # Preserve the approved current task and all already completed reports. GLM
    # consumes the saved review and owns subsequent in-scope technical planning.


def implementation_schema(schema_dir):
    schema=goals.role_schema(support.read(schema_dir/'v2/terra-report.schema.json'),'terra')
    review=goals.role_schema(support.read(schema_dir/'v2/sol-report.schema.json'),'sol')
    schema['properties']['continuation']=goals.obj({
        'action':{'type':'string','enum':['CONTINUE','REQUEST_FINAL_AUDIT','ESCALATE_SOL','WAITING_FOR_USER']},
        'plan':goals.STRINGS,'next_task':goals.INITIAL_TASK,'reason':goals.STRING,'question':goals.STRING,
        'self_assessment':review})
    schema['required'].append('continuation')
    return schema


def dispatch_guard(state, stage, workspace):
    guard(state)
    if not final_only(state):
        return
    if stage not in ('sol','astra_checkpoint'):
        return
    saved=state.get('final_audit_request' if stage=='astra_checkpoint' else 'targeted_consultation') or {}
    if (saved.get('contract_hash')!=state['goal_contract']['hash'] or
            saved.get('task_id')!=state.get('current_task',{}).get('id','') or
            saved.get('source_revision')!=support.snapshot(workspace)['revision']):
        raise support.Paused('PAUSED_STALE_HANDOFF','GPT dispatch lacks a current explicit escalation/final-audit request')
    if stage=='astra_checkpoint' and (not saved.get('evidence_hashes') or
            any(not Path(p).is_file() or support.file_hash(p)!=h for p,h in saved['evidence_hashes'].items())):
        raise support.Paused('PAUSED_STALE_HANDOFF','Final-audit self-check evidence is missing or changed')


def apply_implementation(runner,state,value,record,workspace,run_dir):
    support.validate_schema(value,implementation_schema(runner.SCHEMA_DIR))
    goals.execution_guard(state,value)
    c=value['continuation']; action=c['action']
    if record['source_revision']!=support.snapshot(workspace)['revision']:
        raise support.Paused('PAUSED_STALE_HANDOFF','Implementation handoff is stale')
    if action=='WAITING_FOR_USER':
        raise ValueError('WAITING_FOR_USER needs a material structured user_request')
    state['plan']=c['plan']
    state.setdefault('implementation_handoffs',[]).append({'at':support.now(),'output':record['output'],
        'action':action,'reason':c['reason'],'source_revision':record['source_revision']})
    state.pop('final_audit_request',None)
    if action=='CONTINUE':
        spec=c['next_task']
        decision={'status':'CONTINUE','next_objective':spec['objective'],'affected_paths':spec['affected_paths'],
                  'next_task':{k:v for k,v in spec.items() if k not in ('objective','affected_paths')}}
        goals.assign_task(state,decision,{'revision':record['source_revision']})
        state.update(next_stage='terra',next_action=spec['objective'],affected_paths=spec['affected_paths'])
        state['iteration']+=1
        goals.record_decision(state,decision)
    elif action=='ESCALATE_SOL':
        if not c['question'].strip() or not c['reason'].strip():
            raise ValueError('Sol escalation needs a specific question and evidence of the blocker')
        state['targeted_consultation']={'question':c['question'],'reason':c['reason'],
            'task_id':value['task_id'],'contract_hash':value['contract_hash'],'source_revision':record['source_revision']}
        state['next_stage']='sol'
        state['iteration']+=1
    elif action=='REQUEST_FINAL_AUDIT':
        if value['untested_behavior']:
            raise ValueError('Untested behavior must be addressed or remain explicitly blocked, not final-audit ready')
        # Verify the self-check through the SAME evidence validator, but only on a
        # disposable state. It never becomes independent validation in live state.
        probe=copy.deepcopy(state)
        runner._apply_result(probe,'self_check',c['self_assessment'],record,workspace,run_dir)
        decision={'status':'COMPLETE','contract_hash':value['contract_hash'],'contract_revision':value['contract_revision'],
            'task_id':value['task_id'],'user_request':value['user_request'],
            'acceptance_criteria':[{**a,'status':'verified','evidence':'GLM self-check; not independent'} for a in state['acceptance_criteria']]}
        if not support.completion_ready(probe,decision,support.snapshot(workspace),require_human_reviews=False,require_independent=False):
            raise ValueError('Final audit requires current executed self-check evidence for every approved criterion')
        state['final_audit_request']={**probe['validation'],'requested_at':support.now(),'independent':False}
        state['next_stage']='astra_checkpoint'


FINAL_POLICY = """
FINAL-AUDIT-ONLY WORKFLOW v2 — user-approved override of earlier routing instructions.
GLM (the legacy Terra role) owns technical planning and execution across ALL approved
milestones: inspect, implement, test, fix and self-review. Do not hand each milestone
to Astra. Choose and specify the next in-scope task yourself in continuation.
CONTINUE returns to GLM automatically, with the approved criteria and genuine next
task preserved. Plan substantial batches, not one edit per call. Existing limits
still apply; limits mean paused, not complete, and must not be silently extended.
ESCALATE_SOL requires a specific difficult debugging question, attempts and evidence.
Sol's read-only advice returns directly to GLM; it cannot complete the task.
For unresolved scope/permission/requirements decisions, use structured user_request
and WAITING_FOR_USER. Do not call Astra to re-plan settled goals or expand the task.
REQUEST_FINAL_AUDIT is only for the entire approved task, not the current milestone.
Include a self_assessment with executed checks, criterion_results for EVERY required
criterion and end_to_end_result. This is self-evidence, NOT independent validation.
For ordinary continuation, self_assessment may report NOT_VERIFIED and empty checks;
do not invent checks. All envelopes echo the current contract/hash/task before any
next-task assignment. evidence_refs must be bare file paths, never annotations.
Astra audits the complete task at the end; any final-audit findings go back to GLM.
Astra does not author routine next steps. Historical mandatory Sol/milestone-review
language is overridden only for reviewer routing, never evidence/content quality.
No completion without passing current independent Astra evidence and human reviews.
"""

FINAL_CHECKPOINT = """
This is the FINAL FULL-TASK audit, not a milestone review. Independently inspect the
approved deliverables, real source and executed checks; GLM's self-assessment is not
proof. Return the existing validation+decision schema. If not done, specify concrete
blocking findings and return REWORK/CONTINUE to GLM to plan and repair. Do not propose
new scope or route to Sol: consult_sol must be false. If done, request COMPLETE with
evidence for every criterion. Human review and runner gates still apply.
"""


def activate(state, *, approval_source):
    """Called only by an explicit operator action at an idle, reconciled boundary."""
    if state.get('settings', {}).get('milestone_checkpoints', {}).get('enabled'):
        raise ValueError('Enforced milestone checkpoints require the separate Sol review')
    if state.get("active_stage") or state.get("pending_report_repair") or state.get("uncertain_artifacts"):
        raise ValueError("Finish/reconcile the in-flight stage before changing workflow")
    if state.get("status") == "RUNNING" or not goals.approved(state):
        raise ValueError("Workflow activation requires an idle approved goal")
    if enabled(state):
        guard(state)
        return
    event = {"kind": "workflow_approval", "actor": "user", "at": support.now(),
             "policy": APPROVAL_TEXT, "contract_hash": state["goal_contract"]["hash"],
             "source": approval_source}
    state.setdefault("user_events", []).append(event)
    state["settings"]["workflow"] = {"mode": MODE, "approval_event": event}
    state.setdefault("configuration_changes", []).append({"at": support.now(),
        "reason": APPROVAL_TEXT, "previous_next_stage": state.get("next_stage"),
        "previous_workflow": "astra_terra_sol_astra"})
    if state.get("next_stage") == "sol":
        state["next_stage"] = "astra_checkpoint"


def checkpoint_schema(schema_dir):
    validation = goals.role_schema(support.read(schema_dir / "v2/sol-report.schema.json"), "sol")
    decision = goals.role_schema(support.read(schema_dir / "v2/astra-decision.schema.json"), "astra")
    return goals.obj({"validation": validation, "decision": decision,
        "consult_sol": goals.obj({"requested": {"type": "boolean"}, "question": goals.STRING,
                                  "reason": goals.STRING})})


def rollback(state):
    """Restore legacy routing at a safe checkpoint without restoring old task state."""
    if state.get("active_stage") or state.get("pending_report_repair") or state.get("uncertain_artifacts") or state.get("status") == "RUNNING":
        raise ValueError("Pause and reconcile active work before rolling back routing")
    if not enabled(state):
        return
    old = state["settings"].pop("workflow")
    state.setdefault("configuration_changes", []).append({"at": support.now(),
        "reason": "Explicit rollback to legacy Sol validation routing", "previous_workflow": old})
    if state.get("validation", {}).get("reviewer_role") == "astra":
        state.setdefault("validation_archive", []).append({"reason":"Routing rollback requires Sol revalidation",
            "validation":state.pop("validation")})
        state["human_reviews"] = {}
        state.pop("displayed_review", None)
    if state.get("next_stage") == "astra_checkpoint" or state.get("status") == "TASK_COMPLETE":
        if state.get("status") == "TASK_COMPLETE":
            state.setdefault("completion_archive", []).append({"completed_at":state.pop("completed_at",None),
                "decision":state.pop("final_decision",None)})
        state.update(next_stage="sol",status="PAUSED_WORKFLOW_ROLLBACK",phase="PAUSED_OR_BLOCKED",
            stop_reason="Legacy routing restored; resume the same run for Sol validation")


POLICY = """
GLM-FIRST WORKFLOW v1 (explicit user-approved reviewer-routing override)
GLM is the implementation role (legacy name Terra). Own the substantial assigned
milestone end to end: implementation, tests, self-review and routine fixes. Keep test
failures and local debugging inside that work; do not request GPT approval per edit.
Do not widen the approved task. Stop for real permissions, ambiguity or usage limits.
Astra independently inspects code and executes checks at milestone boundaries in a
single read-only checkpoint that supplies both validation and the next decision.
Sol is NOT an automatic stage. Astra may request one targeted Sol consultation for
a specific unresolved issue. Never remove required independent or human review.
Historical references to Sol's mandatory review name the independent reviewer
responsibility now assigned to Astra, not a waiver of any evidence or quality gate.
The runner, not GLM or a prose verdict, enforces completion. Failed, missing or stale
evidence stays failed/unverified. Optional improvements go into deferred_backlog.
No task restart or discovery for settled requirements. Existing limits still apply.
"""

CHECKPOINT = """
You are ASTRA performing independent validation AND milestone judgment in ONE call.
Read the approved contract, exact changes and relevant source. Terra's summary is not
proof. Run the relevant checks read-only; do not fix files. Return a validation object
with actual command/event evidence for each tested criterion, and a decision object.
Apply the same independent validation rules formerly assigned to Sol. Record unknown
criteria as NOT_VERIFIED. COMPLETE requires passing evidence for EVERY criterion,
current artifact/contract, required human reviews and the complete required flow.
REWORK assigns a coherent GLM correction of blocking findings. CONTINUE assigns the
next substantial approved milestone. Preserve requirements; never invent extra scope.
For consult_sol.requested=true, supply a narrow question and reason and choose
CONTINUE with next_task.kind=validate. This consult preserves the current task. It
does not approve completion or allow implementation to bypass independent review.
The usual path is consult_sol.requested=false with empty reason/question. Do not
escalate routine fixes. For human ambiguity use BLOCKED and matching user_request in
both nested reports. After consultation, inspect the specific findings and affected
behavior rather than repeating unrelated design exploration.
Read this stage's events .jsonl; command evidence uses event:item_N from completed
command_execution events, not conversation call IDs. Never invent test results.
"""


def apply_checkpoint(runner, state, value, record, workspace, run_dir):
    guard(state)
    if not enabled(state) or record.get("role") != "astra":
        raise ValueError("Independent checkpoint must run under the approved Astra role")
    support.validate_schema(value, checkpoint_schema(runner.SCHEMA_DIR))
    current = support.snapshot(workspace)
    if record.get("source_revision") != current["revision"] or record.get("changed_files"):
        raise support.Paused("PAUSED_STALE_VALIDATION", "Checkpoint does not match a read-only current artifact")
    validation, decision, consult = value["validation"], value["decision"], value["consult_sol"]
    if final_only(state):
        dispatch_guard(state,'astra_checkpoint',workspace)
        if consult['requested']:
            raise ValueError('Final audit returns findings to GLM; Sol is only GLM-requested escalation')
    if validation["user_request"] != decision["user_request"]:
        raise ValueError("Validation and decision must agree on the pending user decision")
    if consult["requested"] and (not consult["question"].strip() or not consult["reason"].strip()
            or decision["status"] != "CONTINUE" or decision["next_task"]["kind"] != "validate"
            or decision["user_request"]["kind"] != "none"):
        raise ValueError("Sol escalation requires a concrete question and a non-completion validate decision")
    if not consult["requested"] and (consult["question"] or consult["reason"]):
        raise ValueError("Unused Sol escalation must be empty")
    stages, history = len(state.get("stages", [])), len(state.get("history", []))
    # Reuse existing validators and goal transitions transactionally. These are two
    # logical results from ONE provider call, so persist only its single receipt.
    runner._apply_result(state, "sol", validation, record, workspace, run_dir)
    state["validation"]["reviewer_role"] = "astra"
    state["validation"]["final_audit"] = final_only(state)
    if consult["requested"]:
        goals.execution_guard(state, decision)
        if support.criteria_definition(decision["acceptance_criteria"]) != support.criteria_definition(state["acceptance_criteria"]):
            raise ValueError("Consultation cannot change acceptance criteria")
        state["targeted_consultation"] = {**copy.deepcopy(consult), "task_id": validation["task_id"],
            "source_revision": current["revision"], "contract_hash": validation["contract_hash"]}
        state["next_stage"] = "sol"
        state["iteration"] += 1
        goals.record_decision(state, decision)
    else:
        runner._apply_result(state, "astra_review", decision, record, workspace, run_dir)
        state.pop("targeted_consultation", None)
    state["stages"] = state.get("stages", [])[:stages]
    state["history"] = state.get("history", [])[:history]
    runner.save_record(state, record)
