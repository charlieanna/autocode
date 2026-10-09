"""Read-only diagnosis and bounded repair planning; never writes application code.

Owns three stages: ``astra_resolve`` (why did a reviewed build fail, and what bounded
rework fixes it), the bug-fix workflow's ``investigate_bug`` (autocode_bug_job:
reproduce a reported misbehavior and diagnose it before any fix exists), and the
discuss workflow's ``answer_question`` (autocode_discuss_job: answer a question or
weigh a tradeoff from the repository, building nothing), and ``investigate_stuck``
(autocode_stuck_job: why a stage stopped converging, before the run pauses)."""
import copy
import json
from pathlib import Path
try:
    from .. import autocode_util as util, autocode_source_scope as source_scope, autocode_goals as goals, autocode_bug_job as bug_job
    from .. import autocode_goal_lifecycle as lifecycle
    from .. import autocode_job_report_recovery as job_report_recovery
    from .. import autocode_bug_questions as bug_questions, autocode_resolver_human as human
    from .. import autocode_discuss_job as discuss_job, autocode_stuck_job as stuck_job, autocode_failures as failures
    from .. import autocode_providers, autocode_verify as verify, autocode_verification_plan as verification_plan, autocode_launch_inputs as launch_inputs
    from .. import autocode_investigation_workspace as investigation_workspace, autocode_recovery_novelty as novelty, autocode_resolver_recovery as resolver_recovery
except ImportError:
    import autocode_verify as verify
    import autocode_verification_plan as verification_plan
    import autocode_launch_inputs as launch_inputs
    import autocode_util as util
    import autocode_source_scope as source_scope
    import autocode_goals as goals
    import autocode_goal_lifecycle as lifecycle
    import autocode_job_report_recovery as job_report_recovery
    import autocode_bug_questions as bug_questions, autocode_resolver_human as human
    import autocode_bug_job as bug_job
    import autocode_discuss_job as discuss_job
    import autocode_stuck_job as stuck_job
    import autocode_providers
    import autocode_investigation_workspace as investigation_workspace, autocode_recovery_novelty as novelty, autocode_resolver_recovery as resolver_recovery
    import autocode_failures as failures
from . import autoplanner
from .common import ModelRequest, capped_route, execution_request

PROBE_TIMEOUT = 120  # seconds per discuss probe; a probe checks one claim, it is not a test suite


def prepare_answer(state):
    """The Analyst inherits the Plan Reviewer's model (the Resolver's when there is none) on its
    own route and session, effort capped at medium, with the same scratch-copy rule as the
    Investigator. Like the Architect: GPT-6 Astra stays reserved for the Resolver (docs/models.md)."""
    state["phase"] = "INVESTIGATING"
    roles = state["settings"]["roles"]
    roles.setdefault("analyst", capped_route(roles.get("plan_reviewer") or roles["astra"]))
    prompt, metrics = discuss_job.prompt(
        state, autoplanner.workspace_inventory(state["workspace"], state["task"]),
        state["settings"].get("context_soft_tokens", 10000),
        autoplanner.engine_for(state["settings"], "analyst"))
    return ModelRequest("astra", "analyst", prompt, metrics, discuss_job.SCHEMA, True)


def prepare_investigation(state):
    """The Investigator inherits the Plan Reviewer's model (the Resolver's when there is none) on
    its own route and session, so its reproduction context never leaks into later planning or
    review. It may write, but only to a scratch copy of its own; the runner rejects any change
    to the workspace itself. GPT-6 Astra stays reserved for the Resolver (docs/models.md)."""
    state["phase"] = "INVESTIGATING"
    roles = state["settings"]["roles"]
    roles.setdefault("investigator", copy.deepcopy(roles.get("plan_reviewer") or roles["astra"]))
    scratch = investigation_workspace.prepare(state['workspace'])
    prompt, metrics = bug_job.prompt(
        state, autoplanner.workspace_inventory(state["workspace"], state["task"]),
        state["settings"].get("context_soft_tokens", 10000),
        autoplanner.engine_for(state["settings"], "investigator"), scratch_workspace=str(scratch),
        python_executable=verify.python_for(state['workspace']))
    return ModelRequest("astra", "investigator", prompt, metrics, bug_job.SCHEMA, True)


def apply_job(stage, state, value, record, workspace, *, run_dir=None):
    """Autopilot hands a job stage's validated report here. The Analyst's answer completes
    the run. For the Investigator, a small reproduced bug becomes one Builder task at once
    while that short path is enabled (bug_job.SMALL_CORRECTION_ENABLED); anything else
    continues where bug_job.apply sent it."""
    clean_run, _ = launch_inputs.runners(state, workspace, run_dir or Path(record.get("output") or workspace).parent.parent, verify.scratch_run)
    if stage == discuss_job.STAGE:
        # The runner, not the Analyst, runs each claim's probe, in a scratch copy of the code.
        return discuss_job.apply(state, value, record, workspace, run_probe=lambda command: clean_run(
            workspace, Path(record.get("output") or workspace).parent / "answer-probes", command=command,
            timeout=PROBE_TIMEOUT))
    if stage == stuck_job.STAGE:
        # The runner shows the diagnosed cause: the probe runs in a scratch tree holding only the cited files.
        def probe(command, files):
            if (state.get('stuck_investigation') or {}).get('mode') == 'builder_failure':
                try:
                    from .. import autocode_builder_failure as builder_failure
                except ImportError:
                    import autocode_builder_failure as builder_failure
                command = builder_failure.readonly_probe(command)
            return clean_run(workspace, Path(record.get('output') or workspace).parent / 'investigation-probe',
                             command=command, files=files, timeout=PROBE_TIMEOUT)
        return stuck_job.apply(state, value, record, workspace, run_probe=probe)
    # The runner, not the Investigator, shows the bug: its probe must exit 0 on the code as it is.
    guard = job_report_recovery.BEFORE_WRITE.get()
    if guard:
        guard()
    def run_probe(command):
        result = clean_run(workspace, Path(record.get("output") or workspace).parent / "investigation-probe",
                           command=command, timeout=PROBE_TIMEOUT)
        if guard:
            guard()  # No note or result is published if inspected bytes/source changed during the probe.
        return result
    bug_job.apply(state, value, record, workspace, run_probe=run_probe)
    if bug_questions.has_questions(state):
        human.queue(state, 'clarification', {'stage': bug_job.STAGE, 'output': record.get('output'),
                    'source_revision': record.get('source_revision')}, questions=bug_questions.questions(state),
                    evidence=bug_questions.evidence(state), phase='INVESTIGATING', next_stage=bug_job.STAGE)
        return
    if bug_job.small_correction(state):
        start_small_correction(state, workspace)


def wait_on_existing_diagnosis(state, decision, record, request, *, source_stage):
    """The Completion Owner blocks again on a task and source the AutoResolver already diagnosed.

    One diagnosis per task and source stays the rule. Refusing the second one by raising made the
    runner treat a valid review as invalid output: report repair could never fix it, and a run whose
    code was done could not move (live greenfield-greeting-cli and port-policy-go, 2026-09-29, after
    the user had answered the diagnosis's question and the source was unchanged). The review is
    accepted instead, and the run waits for the user with the existing diagnosis attached: the
    source is unchanged, so only a person, a changed setting or new code can move it. While the
    diagnosis's own question is still unanswered, a second one is not asked; the caller refuses.
    """
    diagnosis = ""
    output = request["diagnosis_output"]
    try:
        diagnosis = str(json.loads(Path(output).read_text()).get("diagnosis") or "")
    except (OSError, ValueError, AttributeError):
        pass
    asked = decision.get("user_request") or {}
    if (asked.get("kind", "none") == "none" or not str(asked.get("decision_needed", "")).strip()
            or not str(asked.get("impact", "")).strip()):
        asked = {"kind": "blocker", "discovered": "The Completion Owner still does not accept this source after "
                                                  "the AutoResolver's diagnosis, and the source has not changed.",
                 "impact": "Another automatic round would review the same source and reach the same result.",
                 "decision_needed": "How should the blocker in the existing diagnosis be resolved?",
                 "options": ["Change the setting or source the diagnosis names, then resume",
                             "Give feedback that changes the plan", "Leave the run paused"],
                 "proposed_delta": ""}
    evidence = {"diagnosis": diagnosis, "output": output, "repeated_on_unchanged_source": True,
                "hashes": dict(request.get("evidence_hashes") or {})}
    if Path(output).is_file():
        evidence["hashes"][output] = util.file_hash(Path(output))
    origin = {"stage": source_stage, **{key: record[key] for key in ("output", "source_revision", "task_id")
                                        if key in record}}
    lifecycle.wait_for_user(state, asked, origin=origin, evidence=evidence, next_stage="astra_review")


def start_small_correction(state, workspace):
    """Install the diagnosis as a one-task contract, approve it under the recorded policy
    (never as the user), and assign the Builder task exactly as a user approval would."""
    try:
        from .. import autocode_dispatch as dispatch
    except ImportError:
        import autocode_dispatch as dispatch
    body = bug_job.correction_contract(state)
    # Approved here under the recorded policy, so no approval request is queued for the user.
    lifecycle.install_draft(state, body, origin=bug_job.ORIGIN, queue_human=False)
    lifecycle.validate_body(state, body, ready=True)
    contract = state["goal_contract"]
    event = {"kind": "goal_approval", "actor": "workflow_policy", "policy": bug_job.SMALL_FIX_POLICY,
             "at": util.now(), "token": goals.token(contract)}
    state.setdefault("user_events", []).append(event)
    contract.update(approval_status="approved", approval_event=event)
    state.update(phase="READY_TO_EXECUTE", status="RUNNING", pending_questions=[])
    decision = goals.initial_decision(body)
    lifecycle.assign_task(state, decision, source_scope.snapshot(Path(workspace), state))
    state.update(next_action=decision["next_objective"], affected_paths=decision["affected_paths"],
                 next_stage=dispatch.build_stage(state))
    goals.record_decision(state, decision)
    if not goals.approved(state):
        raise ValueError("The small-correction contract did not pass the approval check")

def guard(state, workspace):
    goals.execution_guard(state)
    request = state.get('resolution_request') or {}
    output = request.get('source_output', request.get('review_output'))
    if (not state.get('current_task', {}).get('id')
            or request.get('contract_hash') != state['goal_contract']['hash']
            or request.get('task_id') != state.get('current_task', {}).get('id')
            or not output or output not in request.get('evidence_hashes', {})
            or request.get('source_revision') != source_scope.snapshot(workspace, state)['revision']):
        raise util.Paused('PAUSED_STALE_HANDOFF', 'Repair diagnosis needs the current reviewed source and task')
    if request.get('diagnosis_output'):
        raise util.Paused('PAUSED_RESOLVER', 'The saved blocker already has a diagnosis; reconcile it before another resolver call')
    if failures.repeated(state, {'stage': 'astra_resolve', 'source_revision': request['source_revision']}):
        raise util.Paused('PAUSED_REPEATED_FAILURE', 'Resolver failure limit reached for this source; no additional diagnosis authorized')
    for path, digest in request.get('evidence_hashes', {}).items():
        if not Path(path).is_file() or util.file_hash(path) != digest:
            raise util.Paused('PAUSED_STALE_HANDOFF', 'Repair evidence changed; review again before resolving')


def prepare_stuck(state, state_path):
    """A fresh route and session every time, on a model different from the stuck stage's
    (stuck_job.route, or the pinned --investigator-model), so it does not inherit its reasoning."""
    if state['stuck_investigation'].get('mode') == 'builder_failure':
        try:
            from .. import autocode_builder_failure as builder_failure
        except ImportError:
            import autocode_builder_failure as builder_failure
        builder_failure.guard(state, state['stuck_investigation'], state['workspace'])
    roles = state["settings"]["roles"]
    stuck_route = autoplanner.route_for(state, state["stuck_investigation"]["stage"])
    roles[stuck_job.ROUTE] = (stuck_job.pinned_route(state["settings"])
                              or stuck_job.route(roles, (roles.get(stuck_route) or {}).get("model", ""),
                                                 state["settings"].get("provider")))
    state.setdefault("sessions", {}).pop(stuck_job.ROUTE, None)
    identities = state["settings"].setdefault("transport_identities", {})
    if roles[stuck_job.ROUTE].get("engine") == "opencode" and "opencode" not in identities:
        # A Codex run's first OpenCode stage (a pinned --investigator-model): record the transport
        # the way run creation does, so the per-stage billing and drift checks cover it.
        tool = autocode_providers.resolve(state["settings"].get("provider") or "opencode")
        identities["opencode"] = tool.local_settings(Path(state["workspace"]))
    prompt, metrics = stuck_job.prompt(
        state, state_path, autoplanner.workspace_inventory(state["workspace"], state["task"]),
        state["settings"].get("context_soft_tokens", 10000), autoplanner.engine_for(state["settings"], stuck_job.ROUTE))
    return ModelRequest("astra", stuck_job.ROUTE, prompt, metrics, stuck_job.schema(state), False)


def prepare(state, stage, state_path, schema_dir):
    if stage == stuck_job.STAGE:
        return prepare_stuck(state, state_path)
    if stage == bug_job.STAGE:
        return prepare_investigation(state)
    if stage == discuss_job.STAGE:
        return prepare_answer(state)
    if stage == 'astra_diagnose':
        return prepare_diagnosis(state, stage, state_path, schema_dir)
    if stage != 'astra_resolve':
        raise ValueError(f'Autoresolver cannot run {stage}')
    guard(state, Path(state['workspace']))
    # Inherit the planner model, not its conversation. A dedicated route keeps
    # resolver sessions separate from planning, completion and implementation.
    state['settings']['roles'].setdefault('resolver', copy.deepcopy(state['settings']['roles']['astra']))
    request = execution_request(state, 'astra_review', state_path, schema_dir)
    instruction, payload = request.prompt.split('CURRENT HANDOFF DATA\n', 1)
    data = json.loads(payload)
    data.update(stage=stage, resolution_request=state['resolution_request'],
                execution_engine=state['settings']['roles']['resolver'].get('engine', state['settings'].get('engine', 'codex')))
    schema = copy.deepcopy(request.schema)
    schema['properties']['status']['enum'] = ['REWORK', 'BLOCKED']
    schema['properties']['diagnosis'] = goals.STRING
    schema['properties']['recovery_change'] = copy.deepcopy(novelty.CHANGE_SCHEMA)
    schema['required'].append('diagnosis')
    prompt = ('You are AUTORESOLVER, a read-only failure diagnostician, not a Builder or completion owner. '
              'Inspect the source report, rejected build, review findings and exact evidence. '
              'When resolution_request.provenance is source_report_not_accepted_review, its source_report '
              'is a Builder or Validator proposal, not accepted independent validation. Do not promote '
              'its checks, criterion statuses, or claims into accepted review evidence. Return a nonempty diagnosis '
              'and one bounded REWORK next_task with defect evidence and concrete validation_plan retests. '
              + verification_plan.EXPECTED_FAILURE_RULE + ' ' + verification_plan.GIT_STATUS_RULE + ' '
              + verification_plan.SHELL_SYNTAX_RULE + ' '
              'Use kind=implement for a source correction, or kind=validate when the remaining defect is '
              'missing or invalid independent verification of unchanged work. A validate task dispatches '
              'the Validator; it neither authorizes source edits nor accepts prior evidence as current. '
              'The runner exports this task as a one-node repair DAG. Preserve the whole integrated batch. '
              'Return the complete unchanged acceptance_criteria list from the handoff; select the repair subset only in next_task.acceptance_criteria. '
              'Criterion statuses and evidence remain owned by the reviewer, not the resolver. '
              'Do not approve work, change requirements, weaken tests, or modify source. '
              'Resolve ordinary technical blockers within the approved scope before asking. If no safe '
              'bounded repair is available, explain the investigated evidence and remaining smallest '
              'decision in BLOCKED with a structured user_request. If scope or permission must change, '
              'return BLOCKED; never grant it yourself. This only proposes a human question, not execution authority.\n'
              + novelty.INSTRUCTION + instruction + '\nResolver constraint overrides completion choices: only REWORK or BLOCKED; '
              'validation-only repairs use REWORK with next_task.kind=validate.\n'
              + 'CURRENT HANDOFF DATA\n' + json.dumps(data, indent=2))
    metrics = {**request.metrics, 'estimated_prompt_tokens': (len(prompt) + 3) // 4}
    return ModelRequest('astra', 'resolver', prompt, metrics, schema, False)


def validate(state, value, record, workspace):
    guard(state, workspace)
    if record.get('changed_files') or record['source_revision'] != state['resolution_request']['source_revision']:
        raise util.Paused('PAUSED_STALE_HANDOFF', 'Resolver must leave the reviewed source unchanged')
    if record.get('rejected') or record.get('exit_code', 0) != 0 or not Path(record.get('output') or '').is_file():
        raise util.Paused('PAUSED_STALE_HANDOFF', 'Resolver diagnosis requires its saved successful read-only output')
    if value.get('status') not in ('REWORK', 'BLOCKED') or not value.get('diagnosis', '').strip():
        raise ValueError('Resolver requires a diagnosis and a REWORK or BLOCKED decision')
    if value['status'] == 'REWORK' and (not value.get('evidence')
            or value.get('next_task', {}).get('kind') not in ('implement', 'validate')):
        raise ValueError('Resolver must supply an evidence-backed implementation or validation repair')
    resolver_recovery.validate_decision(state, value, record)


# Operational diagnosis: a genuinely separate stage and schema from astra_resolve.
# It is not bound to a reviewer REWORK verdict, never touches acceptance_criteria,
# and its output can only recommend "retry" (with guidance) or "escalate" -- never
# an implementation task, a contract change, or a completion claim. The runner
# revalidates the recommendation against the same bounded policy used to admit
# the diagnosis before any retry is authorized (see autocode_resolver_runtime).
DIAGNOSIS_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["diagnosis", "recommendation"],
    "properties": {
        "diagnosis": goals.STRING,
        "recovery_change": copy.deepcopy(novelty.CHANGE_SCHEMA),
        "recommendation": {
            "type": "object", "additionalProperties": False,
            "required": ["action", "rationale"],
            "properties": {
                "action": {"type": "string", "enum": ["retry", "escalate"]},
                "rationale": goals.STRING,
                "guidance": goals.STRING,
                "evidence_refs": {"type": "array", "items": goals.STRING},
            },
        },
    },
}


def diagnosis_guard(state, workspace):
    goals.execution_guard(state)
    request = state.get('diagnosis_request') or {}
    if (request.get('contract_hash') != state['goal_contract']['hash']
            or request.get('source_revision') != source_scope.snapshot(workspace, state)['revision']):
        raise util.Paused('PAUSED_STALE_HANDOFF', 'Diagnosis needs the current source and approved contract')
    for path, digest in request.get('evidence_hashes', {}).items():
        if not Path(path).is_file() or util.file_hash(path) != digest:
            raise util.Paused('PAUSED_STALE_HANDOFF', 'Diagnosis evidence changed; reconcile before diagnosing')


def prepare_diagnosis(state, stage, state_path, schema_dir):
    if stage != 'astra_diagnose':
        raise ValueError(f'Autoresolver cannot run {stage}')
    diagnosis_guard(state, Path(state['workspace']))
    state['settings']['roles'].setdefault('resolver', copy.deepcopy(state['settings']['roles']['astra']))
    # Reuse the Plan Reviewer's handoff context (current source, task, evidence
    # inventory) for input only; the output schema below is unrelated to and far
    # narrower than the reviewer decision schema that context call would imply.
    request = execution_request(state, 'astra_review', state_path, schema_dir)
    instruction, payload = request.prompt.split('CURRENT HANDOFF DATA\n', 1)
    data = json.loads(payload)
    data.update(stage=stage, diagnosis_request=state['diagnosis_request'],
                execution_engine=state['settings']['roles']['resolver'].get('engine', state['settings'].get('engine', 'codex')))
    prompt = ('You are AUTORESOLVER, a read-only failure diagnostician, not a Builder or completion owner. '
              'A stage has failed the same way repeatedly (see diagnosis_request.repeated_count) and the runner has '
              'stopped its own deterministic recovery for it. '
              'Inspect diagnosis_request (the repeated failure identity, its evidence, and how many times it '
              'recurred) plus the current handoff data. Return a nonempty diagnosis explaining the likely cause. '
              'Recommend "retry" only when you can name a concrete, different action or guidance the next '
              'Builder attempt should follow; recommend "escalate" whenever the cause is unclear, out of scope, '
              'or needs a human decision -- never guess. You cannot approve work, change requirements, weaken '
              'tests, modify source, dispatch a task, or claim completion yourself; this recommendation is '
              'advisory only, and the runner independently validates and bounds it before any retry proceeds.\n'
              + novelty.INSTRUCTION + instruction + '\nDiagnosis constraint overrides completion choices: return diagnosis, recommendation and any bounded recovery_change. '
              'The Builder receives your diagnosis and recommendation; put the concrete steps it should follow in '
              'recommendation.guidance. This incident is the failed stage, not a check command, so a recovery_change '
              'the incident packet cannot attest reaches the Builder only as unattested advice.\n'
              + 'CURRENT HANDOFF DATA\n' + json.dumps(data, indent=2))
    metrics = {**request.metrics, 'estimated_prompt_tokens': (len(prompt) + 3) // 4}
    return ModelRequest('astra', 'resolver', prompt, metrics, DIAGNOSIS_SCHEMA, False)


def validate_diagnosis(state, value, record, workspace):
    diagnosis_guard(state, workspace)
    if record.get('changed_files') or record.get('source_revision') != state['diagnosis_request']['source_revision']:
        raise util.Paused('PAUSED_STALE_HANDOFF', 'Diagnosis must leave the reviewed source unchanged')
    if not value.get('diagnosis', '').strip():
        raise ValueError('Diagnosis requires a nonempty explanation')
    recommendation = value.get('recommendation') or {}
    if recommendation.get('action') not in ('retry', 'escalate') or not recommendation.get('rationale', '').strip():
        raise ValueError('Diagnosis recommendation requires a rationale and a retry-or-escalate action')


def preserve_review_criteria(state, value):
    """A focused diagnosis may omit or restate criteria, but cannot add or verify them.

    The approved wording stays. A one-character restatement used to reject the
    whole report, so a BLOCKED question about the proof environment never
    reached the operator (issue 624).
    """
    authoritative = state['acceptance_criteria']
    by_id = {row['id']: row for row in authoritative}
    seen = set()
    for row in value['acceptance_criteria']:
        cid = row['id']
        if cid in seen:
            raise util.Paused('PAUSED_INVALID_OUTPUT', 'Duplicate acceptance IDs')
        seen.add(cid)
        if cid not in by_id:
            raise util.Paused('PAUSED_CRITERIA_CHANGE', 'Repair cannot change approved acceptance criteria')
    # Preserve the last review's order, statuses and evidence, including omitted
    # criteria. Diagnosis supplies repair instructions, not a new review verdict.
    return {**value, 'acceptance_criteria': copy.deepcopy(authoritative)}
