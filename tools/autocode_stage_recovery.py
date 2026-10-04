"""Automatic recovery of a stage that stopped: timeouts, capacity and rate limits, denied external
directories, report-repair timeouts, abandoned attempts, and the retries that follow.

It uses the run's saved records through autocode_run_records (as records.X, so one patch there
reaches every caller) and imports nothing from autocode.py, which re-exports these names.
"""
from __future__ import annotations

import copy
import time
from pathlib import Path

try:
    from . import autocode_job_failure as job_failure
    from . import autocode_escalation as escalation
    from . import autocode_failures as failures
    from . import autocode_interventions as interventions
    from . import autocode_planning as planning
    from . import autocode_process as processes
    from . import autocode_resolver_human as resolver_human
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_reviewer_fallback as reviewer_fallback
    from . import autocode_support as support
    from . import autocode_workflow as workflow
    from . import autocode_run_records as records
    from . import autocode_validation_recovery as validation_recovery
    from . import autocode_permission_recovery as permission_recovery
except ImportError:
    import autocode_job_failure as job_failure
    import autocode_escalation as escalation
    import autocode_failures as failures
    import autocode_interventions as interventions
    import autocode_planning as planning
    import autocode_process as processes
    import autocode_resolver_human as resolver_human
    import autocode_resolver_runtime as resolver_runtime
    import autocode_reviewer_fallback as reviewer_fallback
    import autocode_support as support
    import autocode_workflow as workflow
    import autocode_run_records as records
    import autocode_validation_recovery as validation_recovery
    import autocode_permission_recovery as permission_recovery


def recover_legacy_report_repair(state, run_dir, workspace):
    """Upgrade one pre-report-repair checkpoint at an explicit resume boundary.

    Older checkpoints could reject a fully completed Builder response for a bad
    evidence citation while their saved settings disabled the already-existing
    report-only repair path.  That is a known terminal artifact, not an
    uncertain provider request: re-open it only when its source, contract and
    archived evidence still match exactly.  Other invalid-output pauses remain
    paused for an operator.
    """
    if state.get("pending_report_repair") or state.get("status") != "PAUSED_INVALID_OUTPUT":
        return False
    record = (state.get("stages") or [])[-1] if state.get("stages") else None
    reason = record.get("rejection_reason", "") if isinstance(record, dict) else ""
    if not (record and record.get("rejected")
            and reason.startswith("Implementation evidence references a missing executed event:")
            and record.get("exit_code") == 0 and record.get("source_revision")
            and not record.get("timed_out") and not record.get("interrupted")):
        return False
    required = ("events", "before_ref", "after_ref", "schema")
    if (support.snapshot(workspace)["revision"] != record["source_revision"]
            or record.get("contract_hash") != (state.get("goal_contract") or {}).get("hash")
            or any(not record.get(key) or not Path(record[key]).is_file() for key in required)
            or not records.stage_completed(state, record)):
        return False
    if not records.repair_limit(state):
        return False
    pending = {"original": copy.deepcopy(record), "attempts": 0,
               "contract_hash": (state.get("goal_contract") or {}).get("hash"),
               "pins": {record[key]: support.file_hash(record[key]) for key in required},
               "error": reason}
    state["pending_report_repair"] = pending
    state.update(status="RUNNING", phase="REPORT_REPAIR")
    state.pop("stop_reason", None)
    state.setdefault("reconciliation_notes", []).append({
        "at": records.now(), "stage": record["stage"], "iteration": record["iteration"],
        "reason": "Migrated legacy rejected evidence citation to bounded report-only repair"})
    records.write_json(run_dir / "state.json", state)
    return True


def abandon_stage(state, run_dir, workspace, selected):
    """Explicitly discard an uncertain response, retaining its edits and evidence."""
    record = state.get("active_stage")
    if not record or selected != records.attempt_id(record):
        raise ValueError("--abandon-stage must match the active attempt_id shown by --status")
    records.assert_stage_stopped(record)
    if job_failure.recover(records, state, run_dir, workspace, abandoned=True):
        return
    record["metrics"] = support.event_metrics(record["events"])
    records.account_stage(state, record)
    before = records.read_json(Path(record["before_ref"]))
    after = support.snapshot(workspace)
    after_path = Path(record["output"]).with_suffix(".after.json")
    records.write_json(after_path, after)
    record.update(after_ref=str(after_path), source_revision=after["revision"],
                  changed_files=support.changed_paths(before, after), abandoned=True)
    resolver_human.supersede_operational(state, 'Operator explicitly abandoned the selected uncertain attempt')
    originals = records.archive_rejected_stage(state, run_dir, record, "Operator abandoned uncertain response; workspace edits retained")
    if record.get("report_only") and state.get("pending_report_repair"):
        state.setdefault("report_repair_archive", []).append({
            "reason": "Report repair attempt abandoned", "repair": state.pop("pending_report_repair")})
    escalation.advance(state, record.get("route_role", record["role"]),
                       trigger="abandoned_attempt", detail="Operator abandoned an uncertain response")
    state["sessions"].pop(record.get("route_role", record["role"]), None)
    if state.get("validation"):
        state.setdefault("validation_archive", []).append({
            "reason": "Uncertain stage abandoned", "validation": state.pop("validation")})
    state["human_reviews"] = {}
    state.pop("displayed_review", None)
    state.setdefault("user_events", []).append({"kind": "stage_abandoned", "at": records.now(),
        "actor": "user_cli", "attempt_id": selected, "changed_files": record["changed_files"]})
    state["recovery_context"] = {"attempt_id": selected, "role": record["role"],
        "source_revision": after["revision"], "changed_files": record["changed_files"],
        "events": record["events"], "source_snapshot": record["after_ref"],
        "instruction": "This response was abandoned. Inspect partial work before assigning a task or validation; its report is not evidence of success."}
    # Report repair is an internal transport stage. A fresh attempt must route
    # to the owning workflow stage, never to the unowned *_report_repair name.
    retry_stage = record.get("original_stage") or record["stage"].removesuffix("_report_repair")
    # Abandonment invalidated validation; a completion retry must obtain it again.
    # Builder and Validator abandonment re-dispatches the same role to inspect
    # partial work and retry. Only completion (astra_review) routes to a fresh
    # review stage.
    next_stage = (workflow.review_stage(state) if retry_stage == "astra_review" else
                  retry_stage if record["role"] == "astra" or record.get("planning") else
                  retry_stage if record["role"] in ("terra", "sol") else
                  "astra_review")
    recovery_role = ("Requirements" if planning.is_planning(state, next_stage) else
                     "Tester" if next_stage == "sol" else
                     "Builder" if next_stage == "terra" else "Plan Reviewer")
    state.update(status="PAUSED_STAGE_ABANDONED", phase="PAUSED_OR_BLOCKED", next_stage=next_stage,
                 stop_reason=f"Partial work retained. Resume explicitly for {recovery_role} to inspect it and choose the next step.")
    records.write_json(run_dir / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)


MAX_AUTOMATIC_CAPACITY_RECOVERIES = 3


def automatically_recover_report_repair_timeout(state, run_dir, workspace, error):
    """Retire only a proven stopped repair; never replay its completed owner."""
    record = state.get('active_stage') or {}
    if not record.get('report_only') or not record.get('timed_out'):
        return False
    if (run_dir / 'pause-requested').exists():
        raise support.Paused('PAUSED_REQUESTED', 'Pause requested; timed-out report repair retained')
    if interventions.pending(run_dir):
        raise support.Paused('PAUSED_INTERVENTION_PENDING', 'Intervention pending; report repair retained')

    def stop(reason):
        raise support.Paused('PAUSED_RESOLVER_OPERATIONAL',
            'AutoResolver cannot safely retry the timed-out report repair: ' + reason)

    pending = state.get('pending_report_repair') or {}
    original = pending.get('original') or {}
    request = state.get('user_request') or (state.get('agent_request') or {}).get('request')
    if (state.get('uncertain_artifacts') or state.get('pending_questions')
            or (request and request.get('kind') != 'none')
            or (state.get('pause_intent') and not state['pause_intent'].get('acknowledged_at'))):
        stop('an unresolved question, pause or intervention owns this boundary')
    try:
        if (not record.get('finished_at') or type(record.get('exit_code')) is not int
                or not record.get('processes') or record.get('interrupted')):
            stop('durable stopped-process evidence is missing')
        records.assert_stage_stopped(record)
        if not Path(record['events']).is_file():
            stop('the repair event log is missing')
        if (any(event.get('type') in ('turn.completed', 'turn.failed')
                for event in support.events(record['events']))
                or (not records.stage_supports_sessions(state, record) and Path(record['output']).is_file())):
            stop('a terminal response raced the timeout; retain it for reconciliation')
        before = records.read_json(Path(record['before_ref']))
        after = support.snapshot(workspace)
        pins = pending.get('pins') or {}
        required = ('events', 'before_ref', 'after_ref', 'schema')
        if (not original.get('source_revision') or before['revision'] != after['revision']
                or after['revision'] != original['source_revision']
                or state.get('next_stage') != original.get('stage')
                or record.get('original_stage') != original.get('stage')
                or record.get('stage') != original.get('stage', '') + '_report_repair'
                or record.get('iteration') != original.get('iteration')
                or record.get('role') != original.get('role')
                or not Path(record.get('schema', '')).is_file()
                or support.file_hash(record['schema']) != support.file_hash(original['schema'])
                or record.get('contract_hash') != original.get('contract_hash')
                or record.get('task_id') != original.get('task_id')
                or (original.get('task_id') is not None
                    and original['task_id'] != (state.get('current_task') or {}).get('id'))
                or record.get('criteria_revision') != original.get('criteria_revision')
                or (original.get('criteria_revision') is not None
                    and original['criteria_revision'] != state.get('criteria_revision'))
                or (state.get('goal_contract') or {}).get('hash') != pending.get('contract_hash')
                or any(not original.get(key) or original[key] not in pins for key in required)
                or any(not Path(p).is_file() or support.file_hash(p) != h for p, h in pins.items())
                or original.get('exit_code') != 0 or original.get('timed_out')
                or not records.stage_completed(state, original)):
            stop('source, original evidence or stage bindings changed')
        attempts = pending.get('attempts')
        if type(attempts) is not int or not 1 <= attempts < records.repair_limit(state):
            stop('bounded report-only repair allowance exhausted or invalid; consumed attempts stay charged')
    except (KeyError, TypeError, ValueError, OSError, processes.ProcessError) as invalid:
        stop(str(invalid))
    except support.Paused as blocked:
        if blocked.status == 'PAUSED_RESOLVER_OPERATIONAL':
            raise
        stop(str(blocked))

    # Persist the archive and resolver receipt together. No implementation/session,
    # validation, original pins or pending attempt counters are reset here.
    with interventions.admission(run_dir):
        record['metrics'] = support.event_metrics(record['events'])
        records.account_stage(state, record)
        after_path = Path(record['output']).with_suffix('.after.json')
        records.write_json(after_path, after)
        record.update(after_ref=str(after_path), source_revision=after['revision'],
                      changed_files=[], abandoned=True, automatic_recovery=True)
        originals = records.archive_rejected_stage(state, run_dir, record,
            'AutoResolver retained stopped nonterminal report repair; retry remaining report-only allowance')
        resolver_runtime._operational_receipt(state, run_dir, 'retry',
            'Retry only the remaining bounded report repair; preserve the completed original and charged attempts.',
            {'origin_output': record['output'], 'events': record['events'],
             'pins': copy.deepcopy(pending['pins']), 'source_revision': after['revision'],
             'next_stage': original['stage'], 'repair_attempts_used': attempts,
             'timeout_kind': record.get('timeout_kind'), 'timeout_reason': str(error)})
        state.update(status='RUNNING', phase='REPORT_REPAIR')
        state.pop('stop_reason', None)
        records.write_json(run_dir / 'state.json', state)
        for artifact in originals:
            artifact.unlink(missing_ok=True)
    return True


def automatically_recover_capacity_stage(state, run_dir, workspace, error):
    """Archive a confirmed model-capacity failure for bounded recovery.

    The next stage is a Plan Reviewer recovery review, so partial work is inspected
    before another writer runs. Repeated capacity failures stop after two
    recoveries and require an explicit resume.
    """
    record = state.get("active_stage")
    if job_failure.owner(record or {}):
        return job_failure.recover(records, state, run_dir, workspace, error)
    if (error.status != "PAUSED_PROVIDER_CAPACITY" or not record
            or (run_dir / "pause-requested").exists()):
        return False
    events = support.events(Path(record.get("events", "")))
    if (not any(event.get("type") == "turn.failed" for event in events)
            or any(event.get("type") == "turn.completed" for event in events)
            or not record.get("before_ref") or not Path(record["before_ref"]).is_file()):
        return False
    try:
        records.assert_stage_stopped(record)
    except support.Paused:
        return False

    recovered = state.get("automatic_capacity_recoveries", [])
    if len(recovered) >= MAX_AUTOMATIC_CAPACITY_RECOVERIES:
        raise support.Paused(
            "PAUSED_PROVIDER_CAPACITY",
            f"Model capacity retry limit reached ({MAX_AUTOMATIC_CAPACITY_RECOVERIES}); "
            "AutoResolver exhausted its bounded capacity recovery. Failed attempts and partial "
            "work remain saved; the task is incomplete and no further calls will launch.")

    before = records.read_json(Path(record["before_ref"]))
    after = support.snapshot(workspace)
    record["metrics"] = support.event_metrics(record["events"])
    records.account_stage(state, record)
    after_path = Path(record["output"]).with_suffix(".after.json")
    records.write_json(after_path, after)
    reason = "Provider reported temporary model capacity exhaustion; partial work retained for review"
    record.update(after_ref=str(after_path), source_revision=after["revision"],
                  changed_files=support.changed_paths(before, after), abandoned=True,
                  automatic_recovery=True, rejection_reason=reason)
    originals = records.archive_rejected_stage(state, run_dir, record, reason)
    state["sessions"].pop(record.get("route_role", record["role"]), None)
    state["human_reviews"] = {}
    state.pop("displayed_review", None)

    next_stage, phase = records.timeout_recovery_route(state, record)
    retry_number = len(recovered) + 1
    recovery = {"at": records.now(), "attempt_id": records.attempt_id(record), "role": record["role"],
                "stage": record["stage"], "source_revision": after["revision"],
                "changed_files": record["changed_files"], "events": record["events"],
                "source_snapshot": record["after_ref"], "next_stage": next_stage,
                "retry_number": retry_number,
                "capacity_error": support.terminal_failure_reason(record["events"])
                    or "Selected model is at capacity",
                "instruction": "The provider reported that the selected model was at capacity. Its workers "
                    "stopped and the partial diff and log were archived. Inspect that work before assigning "
                    "another writer; do not treat this attempt as a completed report. Capacity retries are "
                    "bounded and use a fresh provider request after this review."}
    state.setdefault("automatic_capacity_recoveries", []).append(recovery)
    state.setdefault("user_events", []).append({"kind": "automatic_capacity_recovery", "actor": "runner",
        "at": recovery["at"], "attempt_id": recovery["attempt_id"], "retry_number": retry_number,
        "next_stage": next_stage, "changed_files": record["changed_files"]})
    state["recovery_context"] = recovery
    resolver_runtime.observe_operational_recovery(records, state, run_dir, workspace, recovery)
    state.update(status="RUNNING", phase=phase, next_stage=next_stage)
    state.pop("stop_reason", None)
    records.write_json(run_dir / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    time.sleep(2 ** (retry_number - 1))
    return True


def automatically_recover_truncated_review(state, run_dir, workspace, error):
    """Queue a bounded report-only repair for a truncated read-only review.

    Only Validator and Completion Owner reports qualify. Their original provider
    events and usage remain archived; a repair may summarize completed evidence,
    but it cannot run checks or alter the source. Every truncated repair attempt
    consumes the existing report-repair allowance.
    """
    record = state.get('active_stage') or {}
    original_stage = record.get('original_stage') or record.get('stage')
    event_path = Path(record.get('events', ''))
    reason = support.terminal_failure_reason(event_path) if event_path.is_file() else None
    if (error.status not in ('PAUSED_PROVIDER_UNCERTAIN', 'PAUSED_UNCERTAIN_STAGE')
            or original_stage not in ('sol', 'astra_review', 'astra_checkpoint')
            or not reason or 'output token limit' not in reason.lower()
            or record.get('timed_out') or record.get('interrupted') or record.get('cleanup_error')
            or record.get('exit_code') != 0 or not record.get('before_ref')
            or not Path(record['before_ref']).is_file() or not record.get('schema')
            or not Path(record['schema']).is_file() or state.get('pause_intent')
            or (Path(run_dir) / 'pause-requested').exists() or interventions.pending(run_dir)):
        return False
    if any(row.get('type') == 'turn.completed' for row in support.events(event_path)):
        return False
    try:
        worker_check = record
        if record.get('exit_code') is not None and record.get('pid'):
            # wait_for_stage already reaped this exact parent. Its last process
            # snapshot still contains that PID; check any recorded descendants.
            worker_check = copy.deepcopy(record)
            worker_check['processes'] = [row for row in record.get('processes', [])
                                         if row.get('pid') != record.get('pid')]
        records.assert_stage_stopped(worker_check)
    except support.Paused:
        return False
    before = records.read_json(Path(record['before_ref']))
    after = support.snapshot(workspace)
    if before.get('revision') != after.get('revision'):
        return False
    pending = state.get('pending_report_repair')
    if pending and (pending.get('original', {}).get('stage') != original_stage
                    or pending.get('attempts', 0) >= records.repair_limit(state)):
        if pending.get('original', {}).get('stage') == original_stage:
            raise support.Paused('PAUSED_REPORT_REPAIR_LIMIT',
                'Truncated report-only attempts exhausted the bounded repair allowance; original review evidence is preserved')
        return False
    if not pending and not records.repair_limit(state):
        return False

    record['metrics'] = support.event_metrics(event_path)
    records.account_stage(state, record)
    after_path = Path(record['output']).with_suffix('.after.json')
    records.write_json(after_path, after)
    record.update(after_ref=str(after_path), source_revision=after['revision'], changed_files=[],
                  abandoned=True, automatic_recovery=True, truncated_output=True,
                  rejection_reason=reason)
    originals = records.archive_rejected_stage(state, run_dir, record, reason)
    paths = {record[key]: support.file_hash(record[key]) for key in ('events', 'before_ref', 'after_ref', 'schema')
             if record.get(key) and Path(record[key]).is_file()}
    if pending:
        pending['latest_rejected'] = copy.deepcopy(record)
        pending['error'] = reason
        pending.setdefault('pins', {}).update(paths)
    else:
        state['pending_report_repair'] = {
            'original': copy.deepcopy(record), 'attempts': 0,
            'contract_hash': (state.get('goal_contract') or {}).get('hash'),
            'pins': paths, 'error': reason, 'truncated_output': True}
    state.setdefault('user_events', []).append({
        'kind': 'truncated_review_report_repair', 'actor': 'runner', 'at': records.now(),
        'attempt_id': records.attempt_id(record), 'stage': original_stage,
        'repair_attempts_used': (state.get('pending_report_repair') or {}).get('attempts', 0)})
    state.update(status='RUNNING', phase='REPORT_REPAIR', next_stage=original_stage)
    state.pop('stop_reason', None)
    records.write_json(Path(run_dir) / 'state.json', state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    return True


def reconcile_rate_limited_stage(state, run_dir, workspace):
    """AutoResolver retires a proven stopped rate-limit attempt without replay."""
    record = state.get('active_stage') or {}
    if job_failure.owner(record or {}):
        return job_failure.recover(records, state, run_dir, workspace)
    event_path = Path(record.get('events', ''))
    if (not event_path.is_file() or support.failure_status(event_path) != 'PAUSED_RATE_LIMIT'
            or any(event.get('type') == 'turn.completed' for event in support.events(event_path))
            or not record.get('before_ref') or not Path(record['before_ref']).is_file()):
        return False
    records.assert_stage_stopped(record)
    before = records.read_json(Path(record['before_ref']))
    after = support.snapshot(workspace)
    if support.changed_paths(before, after):
        raise support.Paused('PAUSED_PROVIDER_UNCERTAIN',
            'Rate-limited attempt changed source; AutoResolver retained it for reconciliation and will not replay it')
    record['metrics'] = support.event_metrics(event_path)
    records.account_stage(state, record)
    after_path = Path(record['output']).with_suffix('.after.json')
    records.write_json(after_path, after)
    record.update(after_ref=str(after_path), source_revision=after['revision'], changed_files=[], abandoned=True)
    originals = records.archive_rejected_stage(state, run_dir, record,
        'AutoResolver archived a stopped rate-limited response; no automatic replay')
    route = record.get('route_role') or record.get('role')
    if route:
        state.setdefault('sessions', {}).pop(route, None)
    state['recovery_context'] = {'kind': 'provider_rate_limit', 'stage': record.get('original_stage') or record.get('stage'),
        'events': record['events'], 'source_revision': after['revision'], 'instruction':
        'Provider rate limit was observed. Preserve this attempt and do not retry or change billing routes automatically.'}
    state.update(status='PAUSED_RATE_LIMIT', phase='PAUSED_OR_BLOCKED', next_stage=record.get('original_stage') or record.get('stage'),
                 stop_reason='Provider rate limit retained by AutoResolver; human assistance is required and no replay was authorized')
    records.write_json(Path(run_dir) / 'state.json', state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    return True


def automatically_recover_timed_out_stage(state, run_dir, workspace, error):
    """Archive one fully stopped, non-terminal timeout and continue safely.

    This is deliberately *not* a replay: the original request remains archived,
    its role session is discarded, and a later loop iteration creates a new
    attempt with recovery_context. A completed provider turn, live worker or
    requested pause remains an explicit paused checkpoint. Repeated recoveries
    consume the existing no-progress budget before another provider is launched.
    """
    record = state.get("active_stage")
    if job_failure.owner(record or {}):
        return job_failure.recover(records, state, run_dir, workspace, error)
    if record and record.get('report_only'):
        return automatically_recover_report_repair_timeout(state, run_dir, workspace, error)
    # Startup reconciliation classifies a non-terminal saved log as uncertain;
    # the durable timeout record still proves why the stopped request ended.
    if (error.status not in ("PAUSED_PROVIDER_TIMEOUT", "PAUSED_PROVIDER_UNCERTAIN")
            or not record or not record.get("timed_out")
            or (run_dir / "pause-requested").exists()):
        return False
    events = support.events(Path(record["events"]))
    # A terminal event may have raced the timeout. Preserve it for explicit
    # reconciliation rather than discarding a potentially valid report.
    if any(event.get("type") == "turn.completed" for event in events):
        return False
    if not record.get("before_ref") or not Path(record["before_ref"]).is_file():
        return False
    try:
        records.assert_stage_stopped(record)
    except support.Paused:
        return False

    before = records.read_json(Path(record["before_ref"]))
    after = support.snapshot(workspace)
    record["metrics"] = support.event_metrics(record["events"])
    records.account_stage(state, record)
    after_path = Path(record["output"]).with_suffix(".after.json")
    records.write_json(after_path, after)
    record.update(after_ref=str(after_path), source_revision=after["revision"],
                  changed_files=support.changed_paths(before, after), abandoned=True,
                  automatic_recovery=True,
                  rejection_reason="Timed-out non-terminal provider request automatically archived; partial work retained")
    failures.record(state, record, error, records.now())
    originals = records.archive_rejected_stage(state, run_dir, record, record["rejection_reason"])
    state["sessions"].pop(record.get("route_role", record["role"]), None)
    if state.get("validation"):
        state.setdefault("validation_archive", []).append({
            "reason": "Timed-out implementation automatically archived", "validation": state.pop("validation")})
    state["human_reviews"] = {}
    state.pop("displayed_review", None)

    next_stage, phase = records.timeout_recovery_route(state, record)
    # Final-audit-only runs keep the Builder in charge of implementation. Other routing
    # modes retain the established Plan Reviewer recovery review before another writer.
    semantic_stage = record.get('original_stage') or record['stage']
    recovery = {"at": records.now(), "attempt_id": records.attempt_id(record), "role": record["role"],
                "stage": record["stage"], "source_revision": after["revision"],
                "task_id": record.get("task_id", (state.get("current_task") or {}).get("id")),
                "execution_limits": {key: record.get(key, state.get("settings", {}).get("limits", {}).get(key))
                    for key in ("stage_timeout_seconds", "idle_timeout_seconds", "tool_timeout_seconds")},
                "timeout_kind": record.get("timeout_kind", "stage"), "timeout_reason": record.get("timeout_reason", str(error)),
                "changed_files": record["changed_files"], "events": record["events"],
                "source_snapshot": record["after_ref"], "next_stage": next_stage,
                "instruction": "This timed-out request was archived after its workers stopped. Inspect retained "
                    "partial work and evidence before continuing; do not treat it as a completed report. "
                    "Use timeout_kind and execution_limits to diagnose the failed boundary. Before another writer, "
                    "change the execution plan: split long tool work into bounded calls, reuse valid completed "
                    "checks, or fix the identified stall. Preserve all acceptance checks; do not replay the same "
                    "task under unchanged limits. Do not extend limits or reset budgets without authorization."}
    records.count_automatic_recovery(state)
    state.setdefault("automatic_timeout_recoveries", []).append(recovery)
    state.setdefault("user_events", []).append({"kind": "automatic_timeout_recovery", "actor": "runner",
                                                   "at": recovery["at"], "attempt_id": recovery["attempt_id"],
                                                   "next_stage": next_stage, "changed_files": record["changed_files"]})
    state["recovery_context"] = recovery
    resolver_runtime.observe_operational_recovery(records, state, run_dir, workspace, recovery)
    if record.get('reviewer_fallback_grant'):
        reviewer_fallback.record_failed(state, record['reviewer_fallback_grant'], record)
    elif planning.is_planning(state, semantic_stage):
        reviewer_fallback.reserve(state, run_dir, workspace, semantic_stage)
    if semantic_stage == 'terra':
        state["no_progress_batches"] = state.get("no_progress_batches", 0) + 1
    state["consecutive_timeout_recoveries"] = state.get("consecutive_timeout_recoveries", 0) + 1
    state.update(status="RUNNING", phase=phase, next_stage=next_stage)
    state.pop("stop_reason", None)
    records.write_json(run_dir / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    return True


def automatically_recover_external_directory_denial(state, run_dir, workspace, error):
    """Recover the narrow, deterministic OpenCode external-directory denial.

    The native tool emits this denial before the agent can return a terminal
    turn.  It is unlike a provider timeout: the raw event log identifies the
    exact denied capability, the worker has stopped, and the next prompt can
    state the workspace-only alternative.  All other non-terminal responses
    stay uncertain and require reconciliation.
    """
    record = state.get("active_stage")
    if job_failure.owner(record or {}):
        return job_failure.recover(records, state, run_dir, workspace, error)
    if not record or record.get("timed_out") or (run_dir / "pause-requested").exists():
        return False
    event_path = Path(record.get("events", ""))
    if not event_path.is_file():
        return False
    raw = event_path.read_text(errors="replace")
    events = support.events(event_path)
    if ("permission requested: external_directory" not in raw
            or any(event.get("type") == "turn.completed" for event in events)
            or not record.get("before_ref") or not Path(record["before_ref"]).is_file()):
        return False
    try:
        records.assert_stage_stopped(record)
    except support.Paused:
        return False
    denied = permission_recovery.operation(raw)
    try:
        diagnostics = permission_recovery.prepare(workspace, record.get('original_stage') or record['stage'],
                       denied, state.get('automatic_permission_recoveries', []), state.get('stages', []),
                       run_dir=run_dir)
    except (OSError, ValueError) as error:
        raise support.Paused('PAUSED_PROVIDER_UNCERTAIN',
            f"Cannot provision workspace-contained permission diagnostics: {error}. "
            "The stopped attempt and partial work remain for reconciliation; no retry was launched.") from error
    before = records.read_json(Path(record["before_ref"]))
    after = support.snapshot(workspace)
    record["metrics"] = support.event_metrics(event_path)
    records.account_stage(state, record)
    after_path = Path(record["output"]).with_suffix(".after.json")
    records.write_json(after_path, after)
    record.update(after_ref=str(after_path), source_revision=after["revision"],
                  changed_files=support.changed_paths(before, after), abandoned=True,
                  automatic_recovery=True,
                  rejection_reason="OpenCode denied external_directory before a terminal turn; partial work retained")
    failures.record(state, record, error, records.now())
    originals = records.archive_rejected_stage(state, run_dir, record, record["rejection_reason"])
    state["sessions"].pop(record.get("route_role", record["role"]), None)
    next_stage = validation_recovery.permission_retry(state, record, review_stage=workflow.review_stage(state))
    recovery = {"at": records.now(), "attempt_id": records.attempt_id(record), "role": record["role"],
                "stage": record["stage"], "source_revision": after["revision"],
                "changed_files": record["changed_files"], "events": record["events"],
                "source_snapshot": record["after_ref"], "next_stage": next_stage,
                "instruction": f"The prior request was stopped by OpenCode's external_directory permission. "
                    f"The exact workspace root is {workspace}. Use source-relative shell paths there; "
                    "derive absolute file-tool paths from that exact root, never from a guessed run name. "
                    "A mistyped project path is still outside the allowed workspace: inspect the requested "
                    "path and correct it rather than repeating it or requesting broader permissions. "
                    "Use only workspace-contained evidence paths; do not use /tmp, default mktemp paths, "
                    "nohup, or detached processes. Inspect retained work and start a fresh request."}
    recovery.update(diagnostics)
    recovery['instruction'] += (f" The denied operation is {recovery['denied_operation']}. "
        f"Use the existing diagnostic_directory {recovery['diagnostic_directory']} for scratch files; "
        "for mktemp, supply an explicit template below that directory. Diagnostic success alone is "
        "not task completion; return through the normal independent verification gates.")
    records.count_automatic_recovery(state)
    state.setdefault("automatic_permission_recoveries", []).append(recovery)
    state.setdefault("user_events", []).append({"kind": "automatic_permission_recovery", "actor": "runner",
                                                   "at": recovery["at"], "attempt_id": recovery["attempt_id"],
                                                   "next_stage": next_stage, "changed_files": record["changed_files"]})
    state["recovery_context"] = recovery
    if (record.get('original_stage') or record['stage']) == 'terra':
        state["no_progress_batches"] = state.get("no_progress_batches", 0) + 1
    resolver_runtime.observe_operational_recovery(records, state, run_dir, workspace, recovery)
    state.update(status="RUNNING", phase="PLANNING" if planning.is_planning(state, next_stage) else "EXECUTING",
                 next_stage=next_stage)
    state.pop("stop_reason", None)
    message = permission_recovery.hold_message(recovery) if recovery['repeat_count'] >= 2 else None
    if message:
        state.update(status='PAUSED_REPEATED_FAILURE', phase='PAUSED_OR_BLOCKED', stop_reason=message)
    records.write_json(run_dir / "state.json", state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    if message:
        raise support.Paused('PAUSED_REPEATED_FAILURE', message)
    return True


def prepare_planning_retry(state, run_dir):
    """Explicitly retry an exhausted planning report; retain rejected evidence."""
    # Older runs may already have archived a repair under its internal stage
    # name. Recover only the exact abandoned attempt on an explicit resume.
    if (state.get('status') == 'PAUSED_INVALID_OUTPUT' and not state.get('active_stage')
            and not state.get('pending_report_repair')
            and str(state.get('next_stage', '')).endswith('_report_repair')):
        selected = (state.get('recovery_context') or {}).get('attempt_id')
        archived = next((row for row in reversed(state.get('stages', []))
                         if row.get('abandoned') and records.attempt_id(row) == selected), None)
        if archived and archived.get('stage') == state['next_stage']:
            owner = archived.get('original_stage') or archived['stage'].removesuffix('_report_repair')
            if planning.is_planning(state, owner):
                state['next_stage'] = owner
                state.setdefault('reconciliation_notes', []).append({
                    'at': records.now(), 'stage': owner,
                    'reason': 'Explicit resume routed an archived report repair to its planning owner'})
                records.write_json(run_dir / 'state.json', state)
    # The caller checks unchanged repeated failures before reaching this point.
    # After the cause changes, planning needs the same explicit fresh attempt
    # path as ordinary invalid output, retaining the exhausted repair artifacts.
    if state.get('status') not in ('PAUSED_INVALID_OUTPUT', 'PAUSED_REPEATED_FAILURE', 'PAUSED_REPORT_REPAIR_LIMIT'):
        return False
    pending = state.get('pending_report_repair')
    active = state.get('active_stage')
    stage = (pending or {}).get('original', {}).get('stage', state.get('next_stage'))
    if stage != state.get('next_stage') or not (stage == 'astra_discovery' or planning.is_planning(state, stage)):
        return False
    if active:
        if not (active.get('rejected') and active.get('exit_code') == 0
                and not active.get('timed_out') and not active.get('interrupted')):
            return False
        records.assert_stage_stopped(active)
        if not records.stage_completed(state, active):
            return False
        # Repair checkpoints from the old copy/owner bug retain the archived
        # record as active. It is already rejected: never archive/apply it again.
        if not any(row.get('output') == active.get('output') for row in state.get('stages', [])):
            state.setdefault('stages', []).append(copy.deepcopy(active))
        state.pop('active_stage', None)
    if pending:
        state.setdefault('report_repair_archive', []).append({
            'at': records.now(), 'reason': 'Explicit fresh planning retry after rejected output',
            'repair': state.pop('pending_report_repair')})
    state.setdefault('reconciliation_notes', []).append({
        'at': records.now(), 'stage': stage, 'reason': 'Explicit fresh planning retry; rejected reports retained'})
    # Keep the pause until the normal explicit-resume checks have succeeded.
    records.write_json(run_dir / 'state.json', state)
    return True


def prepare_exhausted_execution_report_retry(state, run_dir, workspace=None, *, allow_repeated=False):
    """Allow an explicit fresh execution report after bounded repairs fail."""
    if state.get('status') not in ('PAUSED_REPORT_REPAIR_LIMIT', 'PAUSED_INVALID_OUTPUT', 'PAUSED_REPEATED_FAILURE') or state.get('active_stage'):
        return False
    pending = state.get('pending_report_repair')
    original = pending.get('original') if isinstance(pending, dict) else None
    if not isinstance(original, dict):
        return False
    stage = original.get('stage')
    reroute_abandoned_sol = (
        stage == 'sol' and state.get('next_stage') == 'astra_review'
        and not state.get('validation')
        and (state.get('recovery_context') or {}).get('role') == 'sol'
        and any(record.get('stage') == 'sol_report_repair' and record.get('abandoned')
                and records.attempt_id(record) == state['recovery_context'].get('attempt_id')
                for record in state.get('stages', [])))
    if ((stage != state.get('next_stage') and not reroute_abandoned_sol) or stage == 'astra_discovery'
            or planning.is_planning(state, stage)
            or pending.get('attempts') != records.repair_limit(state)):
        return False
    repeated = failures.repeated(state, original)
    if repeated and not allow_repeated and (workspace is None or support.snapshot(workspace)['revision'] == original.get('source_revision')):
        message = ("The same stage failed the same way at this source "
                   f"{repeated.get('streak', repeated['count'])} consecutive times. Inspect failure_history and saved output; "
                   "change the cause before another execution request.")
        state.update(status='PAUSED_REPEATED_FAILURE', phase='PAUSED_OR_BLOCKED', stop_reason=message, paused_at=records.now())
        records.write_json(run_dir / 'state.json', state)
        raise support.Paused('PAUSED_REPEATED_FAILURE', message)
    if reroute_abandoned_sol:
        state['next_stage'] = 'sol'
    state.setdefault('report_repair_archive', []).append({
        'at': records.now(), 'reason': 'Explicit fresh execution retry after exhausted report repairs',
        'repair': state.pop('pending_report_repair')})
    role = original.get('route_role') or original.get('role')
    old = state.setdefault('sessions', {}).pop(role, None) if role else None
    if old:
        state.setdefault('session_rotations', []).append({
            'role': role, 'old_session': old, 'at': records.now(),
            'reason': 'Explicit fresh execution retry after exhausted report repairs'})
    state.setdefault('reconciliation_notes', []).append({
        'at': records.now(), 'stage': stage, 'iteration': original.get('iteration'),
        'reason': 'Explicit fresh execution retry; rejected reports retained'})
    records.write_json(run_dir / 'state.json', state)
    return True


def stale_report_repair(state, workspace):
    """(repaired source, current source) when only the source moved under a queued report repair, else None.

    A repair restates the original report for the source that attempt ran on, so after an operator
    edit it can never run (issue #302). A changed goal or changed pinned evidence is not this case.
    Neither is a run waiting on a person: an AutoResolver operational request, even one a source edit
    made stale, is answered or withdrawn only through its own actions, never as a side effect of
    resuming (archiving would also change the frontier the request is bound to)."""
    pending = state.get('pending_report_repair')
    status = str(state.get('status') or '')
    if (not isinstance(pending, dict) or any(state.get(key) for key in ('active_stage', 'uncertain_artifacts'))
            or not (status == 'RUNNING' or status.startswith('PAUSED_'))
            or (state.get(resolver_human.PUBLIC) or {}).get('scope') in ('operational_exhaustion', 'blocker')
            or state.get(resolver_human.PRIVATE)):
        return None
    original = pending.get('original') or {}
    checked = original.get('source_revision')
    if (not checked or original.get('stage') != state.get('next_stage')
            or (state.get('goal_contract') or {}).get('hash') != pending.get('contract_hash')):
        return None
    revision = support.snapshot(workspace)['revision']
    if (checked == revision
            or any(not Path(p).is_file() or support.file_hash(p) != h for p, h in pending.get('pins', {}).items())):
        return None
    return checked, revision


def archive_stale_report_repair(state, run_dir, workspace):
    """On an explicit resume, archive (never delete) a stale report repair so the same stage starts afresh.

    The role's saved session judged the old source, so it is rotated like an exhausted repair's
    (prepare_exhausted_execution_report_retry). Returns the message to show, or None when
    stale_report_repair does not apply."""
    stale = stale_report_repair(state, workspace)
    if not stale:
        return None
    checked, revision = stale
    original = state['pending_report_repair']['original']
    stage = original['stage']
    message = (f"Discarded stale {stage} report repair from source {checked[:12]}; the workspace is now at "
               f"{revision[:12]}. A fresh {stage} attempt runs on the current source.")
    reason = 'Source changed while paused; the repair no longer applies'
    state.setdefault('report_repair_archive', []).append({
        'at': records.now(), 'reason': reason, 'repair': state.pop('pending_report_repair')})
    role = original.get('route_role') or original.get('role')
    old = state.setdefault('sessions', {}).pop(role, None) if role else None
    if old:
        state.setdefault('session_rotations', []).append(
            {'role': role, 'old_session': old, 'at': records.now(), 'reason': reason})
    state.setdefault('reconciliation_notes', []).append({
        'at': records.now(), 'stage': stage, 'iteration': original.get('iteration'), 'reason': message})
    records.write_json(Path(run_dir) / 'state.json', state)
    return message


def retry_format_failed_report(state, run_dir, workspace, selected):
    """Explicitly request fresh evidence after a bounded report rejection."""
    pending = state.get('pending_report_repair') or {}
    original = pending.get('original') or {}
    repair = next((row for row in reversed(state.get('stages', []))
                   if row.get('report_only') and row.get('rejected')
                   and row.get('original_stage') == original.get('stage')), None)
    if (state.get('status') != 'PAUSED_REPEATED_FAILURE'
            or pending.get('error') not in (
                'OpenCode final message is not a JSON report; inspect the saved raw events',
                'Check is not supported by an exact executed Validator event')
            or original.get('stage') != 'sol'
            or not repair or selected != records.attempt_id(repair)
            or repair.get('original_stage') != original.get('stage')
            or repair.get('source_revision') != original.get('source_revision')
            or not repair.get('schema') or not original.get('schema')
            or not Path(repair['schema']).is_file() or not Path(original['schema']).is_file()
            or support.file_hash(repair['schema']) != support.file_hash(original['schema'])
            or pending.get('attempts') != records.repair_limit(state)):
        raise ValueError('--retry-report must match the exhausted rejected report-only attempt')
    if (support.snapshot(workspace)['revision'] != original['source_revision']
            or (state.get('goal_contract') or {}).get('hash') != pending.get('contract_hash')
            or any(not Path(p).is_file() or support.file_hash(p) != h
                   for p, h in pending.get('pins', {}).items())):
        raise ValueError('Saved report inputs changed; reconcile them before retrying')
    if not prepare_exhausted_execution_report_retry(
            state, run_dir, workspace, allow_repeated=True):
        raise ValueError('Saved stage cannot be retried as a fresh execution report')
    state.setdefault('user_events', []).append({
        'kind': 'report_retry_after_format_fix', 'actor': 'user_cli', 'at': records.now(),
        'attempt_id': selected, 'source_revision': original['source_revision']})
    records.write_json(run_dir / 'state.json', state)


def prepare_abandoned_completion_revalidation(state, run_dir, workspace):
    """Repair old completion-abandonment routing on explicit resume only."""
    if (state.get('status') not in ('PAUSED_STAGE_ABANDONED', 'PAUSED_INVALID_OUTPUT', 'PAUSED_REPEATED_FAILURE')
            or state.get('next_stage') != 'astra_review' or state.get('validation')
            or any(state.get(key) for key in ('active_stage', 'pending_report_repair', 'uncertain_artifacts'))):
        return False
    recovery = state.get('recovery_context') or {}
    selected = recovery.get('attempt_id')
    stages = state.get('stages', [])
    index = next((i for i in range(len(stages) - 1, -1, -1)
                  if stages[i].get('abandoned') and records.attempt_id(stages[i]) == selected), None)
    if index is None:
        return False
    abandoned = stages[index]
    owner = abandoned.get('original_stage') or abandoned['stage'].removesuffix('_report_repair')
    if (owner != 'astra_review'
            or abandoned.get('contract_hash') != (state.get('goal_contract') or {}).get('hash')
            or abandoned.get('task_id') != (state.get('current_task') or {}).get('id')
            or not any(e.get('kind') == 'stage_abandoned' and e.get('attempt_id') == selected
                       for e in state.get('user_events', []))
            or not any(v.get('reason') == 'Uncertain stage abandoned' for v in state.get('validation_archive', []))):
        return False
    revision = support.snapshot(workspace)['revision']
    if abandoned.get('source_revision') != revision or recovery.get('source_revision') != revision:
        return False
    # Only failed completion requests (and their runner-owned repair routing)
    # may follow this boundary. Never reinterpret later accepted work.
    retries = [r for r in stages[index + 1:]
               if not (r.get('runner_owned') and r.get('stage') == 'resolver'
                       and (r.get('decision') or {}).get('action') in ('retry', 'hold'))]
    if any(not r.get('rejected') or r.get('abandoned') or r.get('source_revision') != revision
           or (r.get('original_stage') or r.get('stage')) != 'astra_review' for r in retries):
        return False
    missing = 'Completion rejected: missing, stale, failed or unverified independent evidence'
    if retries:
        last = retries[-1]
        failure = (state.get('failure_history') or {}).get(last.get('failure_key'), {})
        if (failure.get('identity') != {'stage': 'astra_review', 'artifact_hash': revision,
                                       'error_class': 'PAUSED_COMPLETION_GATE'}
                or not str(last.get('rejection_reason') or '').startswith(missing)
                or any(not str(r.get('rejection_reason') or '').startswith(missing) and r.get('rejection_reason') not in (
                       'OpenCode final message is not a JSON report; inspect the saved raw events') for r in retries)):
            return False
        attempts = {r.get('failure_attempt') for r in retries if str(r.get('rejection_reason') or '').startswith(missing)}
        if not failure.get('attempts') or not set(failure['attempts']) <= attempts:
            return False
    elif state['status'] != 'PAUSED_STAGE_ABANDONED':
        return False
    stage = workflow.review_stage(state)
    if failures.repeated(state, {'stage': stage, 'source_revision': revision}):
        return False
    state.setdefault('reconciliation_notes', []).append({
        'at': records.now(), 'attempt_id': selected, 'previous_status': state['status'], 'stage': stage,
        'reason': 'Explicit resume requires fresh validation after abandoned completion'})
    resolver_human.supersede_operational(state, 'Scoped completion-abandonment recovery was proven')
    state.update(status='PAUSED_STAGE_ABANDONED', phase='PAUSED_OR_BLOCKED', next_stage=stage,
                 stop_reason='Completion abandonment invalidated validation. Resume explicitly for fresh review.')
    records.write_json(run_dir / 'state.json', state)
    return True


def authorize_failure_retry(state, run_dir, workspace):
    """Authorize one fresh attempt for an inspected, unchanged repeated failure.

    The durable record is audit only. The returned exception is consumed in this
    invocation; loading the checkpoint cannot grant another execution attempt.
    """
    issued = resolver_human.current(state)
    issued_cause = (state.get('resolver', {}).get('human_escalations', {}).get(issued['request_id'], {})
                    .get('identity', {}).get('proposal', {}).get('origin', {}).get('pause_status')) if issued else None
    if (state.get('status') != 'PAUSED_REPEATED_FAILURE'
            and not (issued and issued['scope'] == 'operational_exhaustion' and issued_cause == 'PAUSED_REPEATED_FAILURE')):
        raise ValueError('--retry-failed-stage requires a run paused for repeated failure')
    if any(state.get(key) for key in ('active_stage', 'uncertain_artifacts', 'pending_report_repair')):
        raise ValueError('Reconcile the active attempt or pending report repair before authorizing a retry')
    record = next(
        (row for row in reversed(state.get('stages', [])) if row.get('failure_key')), None)
    repeated = failures.repeated(state, record) if record else None
    if not repeated:
        raise ValueError('No unchanged repeated failure to authorize; fix the cause, then resume')
    selected = record.get('failure_key')
    revision = support.snapshot(workspace)['revision']
    identity = repeated['identity']
    if ((state.get('failure_history') or {}).get(selected) is not repeated
            or failures.key(identity) != selected
            or identity['artifact_hash'] != revision or record.get('source_revision') != revision
            or identity['stage'] != (record.get('original_stage') or record.get('stage'))
            or identity['stage'] != state.get('next_stage')):
        raise ValueError('Retry authorization requires the exact current source, stage and failure identity')
    authorization = {'at': records.now(), 'failure_key': selected, 'identity': copy.deepcopy(identity),
                     'count': repeated['count'], 'source_revision': revision}
    resolver_human.supersede_operational(state, 'Operator explicitly authorized one scoped failure retry')
    state.setdefault('failure_retry_authorizations', []).append(authorization)
    state.setdefault('user_events', []).append({
        'kind': 'failure_retry_authorized', 'at': records.now(), 'failure_key': selected,
        'stage': repeated['identity'].get('stage'), 'count': repeated['count']})
    records.write_json(run_dir / 'state.json', state)
    return copy.deepcopy(authorization)
