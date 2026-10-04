"""Pause stopped workflow jobs under their owner, with one exact explicit retry.

Runtime services are passed by callers so this policy never imports a controller
or a cycle member. This module alone writes state.job_failure and
state.job_retry_authorization; run_view reads the former and provider admission
consumes the latter. The archive retains fragments, usage and source captures.
"""
from __future__ import annotations
import copy
import json
import re
from pathlib import Path
try:
    from . import autocode_jobs as jobs, autocode_job_source as source, autocode_util as util
except ImportError:
    import autocode_jobs as jobs
    import autocode_job_source as source
    import autocode_util as util

ROLE_NAMES = {'review_change': 'Reviewer', 'review_design': 'Architect', 'check_design': 'Architect',
              'answer_question': 'Analyst', 'investigate_bug': 'Investigator', 'investigate_stuck': 'Investigator'}
RETRY_ACTION = '--resume-paused --retry-failed-stage --job-retry-token TOKEN'


def owner(record):
    stage = record.get('stage')
    return stage if stage in jobs.STAGES and not record.get('report_only') else None


def configuration(state):
    settings = state.get('settings') or {}
    return copy.deepcopy({k: settings.get(k) for k in ('engine', 'provider', 'roles', 'limits',
                         'transport_identity', 'transport_identities', 'headroom', 'output_transport', 'test_command', 'regression_command')})


def _reason(runtime, record, error):
    role = ROLE_NAMES[owner(record)]
    if record.get('timed_out'):
        return 'timeout', role + ': ' + (record.get('timeout_reason') or str(error))
    path = Path(record.get('events', ''))
    raw = path.read_text(errors='replace') if path.is_file() else ''
    status = runtime.support.failure_status(path)
    diagnostic = raw.strip()
    try:
        event = json.loads(diagnostic)
        if isinstance(event, dict) and event.get('type') in ('error', 'turn.failed'):
            diagnostic = (event.get('error') or {}).get('message', diagnostic)
    except (ValueError, TypeError, AttributeError):
        pass
    if re.fullmatch(r'(?:(?:Error|SQLiteError|SQLITE_BUSY|SQLITE_LOCKED):\s*)?database(?: table)? is locked\.?', diagnostic, re.I):
        return 'startup_lock', role + ': provider database is locked'
    if 'external_directory' in raw:
        return 'external_directory', role + ': external-directory permission denied'
    if status == 'PAUSED_RATE_LIMIT':
        return 'rate_limit', role + ': provider rate limit'
    if status == 'PAUSED_PROVIDER_CAPACITY':
        return 'capacity', role + ': provider capacity failure'
    return 'exit', f"{role}: provider exited {record.get('exit_code')} without a terminal report; {error or ''}"


def recover(runtime, state, run_dir, workspace, error=None, *, abandoned=False):
    record = state.get('active_stage') or {}
    stage = owner(record)
    if getattr(error, 'status', None) in ('PAUSED_PROCESS_CLEANUP', 'PAUSED_WORKSPACE_BUSY'):
        return False
    if not stage or (not abandoned and runtime.stage_completed(state, record)):
        return False
    if not record.get('pid') and not record.get('finished_at'):
        return False
    try:
        runtime.assert_stage_stopped(record)
    except util.Paused:
        return False
    kind, reason = _reason(runtime, record, error)
    original_attempt = runtime.attempt_id(record)
    if abandoned:
        reason = f'Operator abandoned {ROLE_NAMES[stage]} attempt {original_attempt}; inspect its retained work before an explicit fresh retry.'
    restoration = source.restore(workspace, record)
    if restoration['unrestored']:
        reason += '; unrestored source: ' + ', '.join(restoration['unrestored'])
    record['metrics'] = runtime.support.event_metrics(record['events'])
    runtime.account_stage(state, record)
    record.update(job_failure_kind=kind, write_diagnosis=restoration, abandoned=abandoned)
    config = record.get('job_configuration') or configuration(state)
    source_identity = (record.get('job_source') or {}).get('before_identity')
    token = 'jr:' + util.digest({'run': str(run_dir), 'attempt': original_attempt,
                    'started_at': record.get('started_at'), 'source': source_identity, 'configuration': config})
    originals = runtime.archive_rejected_stage(state, run_dir, record, reason)
    state.setdefault('sessions', {}).pop(record.get('route_role') or record.get('role'), None)
    state['job_failure'] = {'stage': stage, 'attempt_id': original_attempt, 'job_retry_token': token,
                           'reason': reason, 'kind': kind, 'source_identity': source_identity,
                           'configuration': config, 'archive': str(Path(record['events']).parent),
                           'write_diagnosis': restoration, 'unrestored': restoration['unrestored']}
    state.pop('job_retry_authorization', None)
    state['recovery_context'] = {'kind': 'job_failure', 'stage': stage, 'attempt_id': original_attempt,
                                'events': record['events'], 'reason': reason, 'write_diagnosis': restoration}
    state.update(status='PAUSED_STAGE_ABANDONED' if abandoned else 'PAUSED_JOB_FAILURE',
                 phase='PAUSED_OR_BLOCKED', next_stage=stage, stop_reason=reason, pending_questions=[])
    state.pop('user_request', None)
    runtime.write_json(Path(run_dir) / 'state.json', state)
    for artifact in originals:
        artifact.unlink(missing_ok=True)
    return True


def authorize(runtime, state, run_dir, workspace, token):
    failure = state.get('job_failure') or {}
    if state.get('status') not in ('PAUSED_JOB_FAILURE', 'PAUSED_STAGE_ABANDONED') or not failure:
        raise ValueError('Exact job retry requires a paused failed workflow job')
    if not token or token != failure.get('job_retry_token'):
        raise ValueError('Job retry token does not match the current failed attempt')
    if not failure.get('source_identity'):
        raise ValueError('Exact retry is unavailable: this attempt has no saved original source identity. '
                         'Inspect the archived attempt and current changes before starting a new run.')
    # The restoration diagnosis describes the failed attempt. Missing capture
    # files or a later exact manual restoration must not make it a permanent
    # veto: recheck the saved full identity, then check it again at admission.
    if not source.matches_original(workspace, failure['source_identity']):
        if failure.get('unrestored'):
            raise ValueError('Unrestored source blocks job retry: ' + ', '.join(failure['unrestored']))
        raise ValueError('Source changed since the failed attempt; retry is stale')
    if configuration(state) != failure['configuration']:
        raise ValueError('Provider configuration or limits changed; retry is stale')
    state['job_retry_authorization'] = {'token': token, 'stage': failure['stage'], 'consumed': False}
    state.setdefault('user_events', []).append({'kind': 'job_retry_authorized', 'actor': 'user_cli',
        'at': util.now(), 'attempt_id': failure['attempt_id'], 'token': token})
    state.update(status='RUNNING', phase='EXECUTING', next_stage=failure['stage'])
    state.pop('stop_reason', None)
    runtime.write_json(Path(run_dir) / 'state.json', state)


def admit(state, record, workspace):
    if not owner(record):
        return
    record['job_configuration'] = configuration(state)
    if (record.get('job_source') or {}).get('before_identity') != util.digest(source.identity(workspace)):
        raise util.Paused('PAUSED_STALE_VALIDATION', 'Source changed before workflow-job admission')
    if not state.get('job_failure'):
        return
    failure = state['job_failure']
    auth = state.get('job_retry_authorization') or {}
    capture = record.get('job_source') or {}
    if (auth.get('consumed') or auth.get('token') != failure['job_retry_token']
            or auth.get('stage') != owner(record) or configuration(state) != failure['configuration']
            or capture.get('before_identity') != failure['source_identity']):
        raise util.Paused('PAUSED_JOB_FAILURE', 'Exact job retry admission changed; no provider launched')
    auth['consumed'] = True  # persisted with active_stage immediately before Popen
    record['job_retry_token'] = auth['token']


def completed(state, stage):
    if stage == (state.get('job_failure') or {}).get('stage'):
        state.pop('job_failure', None)
        state.pop('job_retry_authorization', None)
