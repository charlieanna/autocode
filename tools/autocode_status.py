"""Durable, bounded task updates for the terminal and dashboard conversation."""
import datetime as dt
import hashlib
import json

ROLES = {'astra_resolve': 'Autoresolver', 'terra': 'Builder', 'sol': 'Validator', 'astra_review': 'Completion Owner',
         'astra_checkpoint': 'Completion Owner', 'astra_discovery': 'Requirements Planner',
         'astra_plan': 'Plan Reviewer', 'astra_challenge': 'Plan Reviewer',
         'astra_finalize': 'Plan Reviewer', 'glm_revise': 'Requirements Planner', 'orchestrator': 'Orchestrator', 'builder': 'Builder', 'validator': 'Validator',
         'decision_owner': 'Completion Owner', 'requirements_planner': 'Requirements Planner',
         'requirements_revision': 'Requirements Planner', 'plan_reviewer': 'Plan Reviewer',
         'plan_finalizer': 'Plan Reviewer'}


def record(state, *, timestamp=None):
    if not all(key in state for key in ('task', 'workspace', 'status')):
        return None
    timestamp = timestamp if timestamp is not None else dt.datetime.now(dt.timezone.utc).timestamp()
    active = state.get('active_stage') or {}
    stage = active.get('stage') or state.get('next_stage') or ''
    role = ROLES.get(stage.removesuffix('_report_repair'), stage.replace('_', ' ').title() or 'Runner')
    task = state.get('current_task') or {}
    activity = active.get('activity') or {}
    batch = state.get('orchestration_batch') or {}
    workers = [(w.get('milestone_id'), w.get('status')) for w in batch.get('workers', [])]
    request = state.get('user_request') or {}
    status = state['status']
    signature = hashlib.sha256(json.dumps([status, stage, state.get('iteration'), task.get('id'),
        task.get('objective'), bool(active), active.get('started_at'), active.get('name'), workers, state.get('stop_reason'), request,
        state.get('pending_questions')], sort_keys=True).encode()).hexdigest()
    previous = state.get('progress_checkpoint') or {}
    changed = previous.get('signature') != signature
    heartbeat = status == 'RUNNING' and (bool(active) or batch.get('status') == 'BUILDING') and timestamp - previous.get('at', 0) >= 60
    if not changed and not heartbeat:
        return None
    if status in ('TASK_COMPLETE', 'COMPLETE'):
        text = 'Task complete. Acceptance checks and required reviews are recorded.'
    elif status == 'DRY_RUN':
        text = 'Dry run complete. No providers were launched.'
    elif status.endswith('REWORK_REQUIRED'):
        text = 'Design rework limit reached. Inspect the saved review before continuing.'
    elif status.startswith('PAUSED') or status in ('WAITING_FOR_USER', 'AWAITING_GOAL_APPROVAL', 'BLOCKED'):
        detail = (request.get('question') or request.get('decision_needed') or state.get('error') or state.get('stop_reason')
                  or '; '.join(q.get('question', '') for q in state.get('pending_questions', []))
                  or ('Review the proposed brief.' if status == 'AWAITING_GOAL_APPROVAL'
                      else 'Inspect the saved checkpoint.'))
        text = f'{status.replace("_", " ").title()}: {detail}'
        text += (' Next: answer the pending request in this task.' if status == 'WAITING_FOR_USER' else
                 ' Next: review the brief and approve or give feedback.' if status == 'AWAITING_GOAL_APPROVAL' else
                 ' Next: resolve the recorded blocker, then explicitly resume this run.')
    else:
        text = f'{role}{" started" if active else " queued"}: ' + (task.get('objective') or 'Working on the task.')
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
        from . import autocode_support as support, autocode_checkpoints as checkpoints
    except ImportError:
        import autocode_support as support
        import autocode_checkpoints as checkpoints
    checkpoints.update(state)
    entry = record(state)
    support.atomic_json(path, state)
    if entry:
        print(entry['text'], file=sys.stderr, flush=True)
