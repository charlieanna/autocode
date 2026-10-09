"""Durable, bounded task updates for the terminal and dashboard conversation."""
import datetime as dt
import hashlib
import json
try:
    from . import autocode_resolver_human as human
    from . import autocode_roles as roles
except ImportError:
    import autocode_resolver_human as human
    import autocode_roles as roles

# Re-exported for callers that still ask for the table; names live in autocode_roles.
ROLES = {stage: roles.screen_name(stage) for stage in roles.STAGE_JOB}


def role_name(stage, state=None):
    """Screen name for a stage code name: 'terra' -> 'Builder'.

    Report repair stays the same job. Pass ``state`` so a stage that does more than
    one job can name the job being done (reviewer routing's astra_checkpoint is
    Tester, not the Plan Reviewer's plan job).
    """
    return roles.screen_name(stage, state)


def _model(active):
    route = active.get('launch_route') or {}
    if route.get('model'):
        return str(route['model'])
    command = active.get('command')
    if not isinstance(command, list):
        return None
    for flag in ('--model', '-m'):
        if flag in command and command.index(flag) + 1 < len(command):
            return str(command[command.index(flag) + 1])
    return None


def record(state, *, timestamp=None):
    if not all(key in state for key in ('task', 'workspace', 'status')):
        return None
    timestamp = timestamp if timestamp is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    check = state.get('active_runner_check') or {}
    active = state.get('active_stage') or check
    stage = active.get('stage') or state.get('next_stage') or ''
    role = role_name(stage, state) or 'Runner'
    task = state.get('current_task') or {}
    activity = active.get('activity') or {}
    batch = state.get('orchestration_batch') or {}
    workers = [(w.get('milestone_id'), w.get('status')) for w in batch.get('workers', [])]
    public = human.current(state)
    request = public['request'] if public else {}
    questions = public['questions'] if public else []
    status = state['status']
    signature = hashlib.sha256(json.dumps([status, stage, state.get('iteration'), task.get('id'),
        task.get('objective'), bool(active), active.get('started_at'), active.get('name'), workers, state.get('stop_reason'), request,
        questions, public.get('request_id') if public else None, check.get('summary')], sort_keys=True).encode()).hexdigest()
    previous = state.get('progress_checkpoint') or {}
    changed = previous.get('signature') != signature
    heartbeat = status == 'RUNNING' and (bool(active) or batch.get('status') == 'BUILDING') and timestamp - previous.get('at', 0) >= 60
    if not changed and not heartbeat:
        return None
    if status in ('TASK_COMPLETE', 'COMPLETE'):
        text = 'Task complete. Acceptance checks and required reviews are recorded.'
    elif status == 'WAITING_FOR_DEPENDENCY':
        text = state.get('stop_reason') or 'Waiting for prerequisite delivery. No user action needed.'
    elif status == 'DRY_RUN':
        text = 'Dry run complete. No providers were launched.'
    elif status.endswith('REWORK_REQUIRED'):
        text = 'Design rework limit reached. Inspect the saved review before continuing.'
    elif status == 'RESOLVER_PENDING' or (status in ('WAITING_FOR_USER', 'AWAITING_GOAL_APPROVAL') and not public):
        role = 'AutoResolver'
        text = 'AutoResolver is evaluating an internal decision; no human request has been authorized.'
    elif status.startswith('PAUSED') or status in ('WAITING_FOR_USER', 'AWAITING_GOAL_APPROVAL', 'BLOCKED'):
        detail = (request.get('question') or request.get('decision_needed') or state.get('error') or state.get('stop_reason')
                  or '; '.join(q.get('question', '') for q in questions)
                  or ('Review the proposed brief.' if status == 'AWAITING_GOAL_APPROVAL'
                      else 'Inspect the saved checkpoint.'))
        text = f'{status.replace("_", " ").title()}: {detail}'
        last = (state.get('stages') or [{}])[-1]
        operational_hold = (last.get('runner_owned') and last.get('decision', {}).get('action') == 'hold'
                            and last.get('receipt', {}).get('scope') == 'operational_diagnostic'
                            and not state.get('pending_questions') and request.get('kind', 'none') == 'none')
        if public:
            role = 'AutoResolver'
            text = 'AutoResolver: ' + detail
            text += (' Review the proposed plan and use its exact approval token.'
                     if public['scope'] == 'goal_approval' else ' Respond to the issued AutoResolver request.')
        elif operational_hold:
            text += ' AutoResolver must evaluate human escalation before any request is shown.'
    elif check:
        role = 'Runner'
        text = check['summary'] + '. This check runs locally before the Validator; no model is active.'
    else:
        shown = _model(active) if active else None
        text = f'{role}{" started" if active else " queued"}: ' + (task.get('objective') or 'Working on the task.')
        if shown:
            text += f' [{shown}]'
        if activity:
            text += f' Activity: {activity.get("activity", "waiting for provider").replace("_", " ")}.'
            if activity.get('elapsed_seconds') is not None:
                text += f' Stage elapsed: {activity["elapsed_seconds"]:g}s.'
        if workers:
            text += ' Builders: ' + '; '.join(f'{mid}: {saved}' for mid, saved in workers) + '.'
        if stage.endswith('_report_repair'):
            text += ' Repairing the saved report format.'
    messages = state.setdefault('progress_messages', [])
    # Heartbeats replace the current heartbeat, avoiding a message every minute in history.
    if not changed and messages and messages[-1].get('kind') == 'heartbeat':
        messages.pop()
    sequence = state.get('progress_sequence', 0) + 1
    entry = {'id': f'progress-{sequence}', 'role': 'assistant', 'speaker': role,
             'created_at': dt.datetime.fromtimestamp(timestamp, dt.timezone.utc).isoformat(),
             'text': text, 'kind': 'transition' if changed else 'heartbeat'}
    messages.append(entry)
    state['progress_messages'] = messages[-100:]
    state['progress_sequence'] = sequence
    state['progress_checkpoint'] = {'signature': signature, 'at': timestamp}
    return entry


def persist(path, state):
    """Publish an update only after its checkpoint is durably saved."""
    import sys
    try:
        from . import autocode_util as util, autocode_checkpoints as checkpoints, autocode_activity_log as activity_log
        from . import autocode_usage as token_usage
    except ImportError:
        import autocode_util as util
        import autocode_checkpoints as checkpoints
        import autocode_activity_log as activity_log
        import autocode_usage as token_usage
    try:
        from . import autocode_code_checkpoints as code_checkpoints
    except ImportError:
        import autocode_code_checkpoints as code_checkpoints
    code_checkpoints.update(state, util.Path(path).parent)
    checkpoints.update(state)
    entry = record(state)
    util.atomic_json(path, state)
    activity_log.record(path, state)
    token_usage.record(path, state)
    if entry:
        print(entry['text'], file=sys.stderr, flush=True)
