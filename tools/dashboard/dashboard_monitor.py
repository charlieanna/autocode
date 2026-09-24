"""Read-only status evidence. Never starts providers or changes runner state."""
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shlex
import subprocess
import threading
import time


def mapping(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return value if isinstance(value, list) else []


def stamp(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


_lock = threading.Lock()
_table = None
_checked = 0
_PROCESS_TABLE_UNSET = object()


def process_table():
    global _table, _checked
    with _lock:
        if time.monotonic() - _checked < 3:
            return _table
        try:
            result = subprocess.run(['ps', '-axo', 'pid=,ppid=,lstart=,etime=,stat=,command='],
                                    capture_output=True, text=True, timeout=2,
                                    env={**os.environ, 'LC_ALL': 'C'})
            if result.returncode:
                raise OSError('Process inspection unavailable')
            table = {}
            for line in result.stdout.splitlines():
                fields = line.split(None, 9)
                if len(fields) != 10:
                    continue
                try:
                    table[int(fields[0])] = {'parent': int(fields[1]), 'started': ' '.join(fields[2:7]),
                                            'elapsed': fields[7], 'state': fields[8], 'command': fields[9]}
                except ValueError:
                    continue
            _table = table
        except (OSError, subprocess.TimeoutExpired):
            _table = None
        _checked = time.monotonic()
        return _table


def worker_status(active, run, table):
    pid = active.get('pid')
    if type(pid) is not int or pid <= 0:
        return {'state': 'none', 'label': 'No worker recorded for this step'}
    if table is None:
        return {'state': 'unknown', 'label': 'Worker could not be checked', 'pid': pid}
    worker = table.get(pid)
    if not worker or worker['state'].startswith('Z'):
        return {'state': 'exited', 'label': 'Recorded worker has exited', 'pid': pid}
    saved = next((p for p in rows(active.get('processes')) if mapping(p).get('pid') == pid), {})
    if saved.get('started'):
        verified = saved['started'] == worker['started']
    else:
        # Older runners have no start identity: require a provider command and
        # an exact run-local path argument. A matching PID alone is insufficient.
        try:
            args = shlex.split(worker['command'])
            provider = any(Path(arg).name in ('codex', 'opencode') for arg in args[:3])
            verified = provider and any(arg == str(run) or arg.startswith(str(run) + '/') for arg in args)
        except ValueError:
            verified = False
    if not verified:
        return {'state': 'unknown', 'label': 'Worker identity could not be verified', 'pid': pid}
    return {'state': 'alive', 'label': 'Worker verified alive', 'pid': pid, 'elapsed': worker['elapsed']}


def local_file(run, raw):
    if not isinstance(raw, str) or not raw:
        return None
    try:
        path = Path(raw)
        path = (path if path.is_absolute() else run / path).resolve(strict=True)
        path.relative_to(run.resolve())
        return path if path.is_file() else None
    except (OSError, ValueError, RuntimeError):
        return None


def activity_log(path):
    """Only safe tool metadata; no model prose, reasoning or raw command/output."""
    if path is None:
        return []
    try:
        with path.open('rb') as stream:
            size = stream.seek(0, 2)
            stream.seek(max(0, size - 131072))
            if size > 131072:
                stream.readline()
            content = stream.read(131072).decode('utf8', errors='replace')
    except OSError:
        return []
    found = {}
    for line in content.splitlines():
        try:
            event = mapping(json.loads(line))
        except ValueError:
            continue
        item = mapping(event.get('item'))
        kind, ident = item.get('type'), item.get('id')
        if kind in ('command_execution', 'file_change'):
            entry = {'label': 'Terminal command' if kind == 'command_execution' else 'File changes',
                     'status': item.get('status') or event.get('type'), 'exit_code': item.get('exit_code')}
            output = item.get('aggregated_output')
        elif event.get('type') == 'tool_use':
            part = mapping(event.get('part')); tool = part.get('tool'); info = mapping(part.get('state'))
            if tool not in ('bash', 'read', 'grep', 'glob', 'edit', 'write', 'apply_patch'):
                continue
            ident = part.get('id')
            name = Path(str(mapping(info.get('input')).get('filePath') or '')).name
            name = name if re.fullmatch(r'[A-Za-z0-9_.-]{1,100}', name) else ''
            entry = {'label': 'Terminal command' if tool == 'bash' else tool.title() + ': ' + (name or 'workspace files'),
                     'status': info.get('status'), 'exit_code': mapping(info.get('metadata')).get('exit')}
            output = info.get('output') if tool == 'bash' else None
        else:
            continue
        if isinstance(ident, str):
            # Status is also an allow-list, not arbitrary provider text.
            entry['status'] = entry['status'] if entry['status'] in ('running', 'pending', 'completed', 'failed', 'error', 'in_progress', 'item.started', 'item.completed') else 'recorded'
            entry['exit_code'] = entry['exit_code'] if type(entry['exit_code']) is int else None
            # Extract numeric test totals only; never expose raw logs, commands,
            # failing-test names, source snippets, secrets, or model reasoning.
            if isinstance(output, str):
                totals = re.findall(r'(?m)^\s*(\d+ runs, \d+ assertions, \d+ failures, \d+ errors, \d+ skips|\d+ examples, \d+ failures(?:, \d+ pending)?|Ran \d+ tests? in [\d.]+s)\s*$', output)
                if totals:
                    entry['test_summary'] = ' · '.join(dict.fromkeys(totals[-3:]))
            found[ident] = entry
    return list(found.values())[-6:][::-1]


def batch_summary(raw):
    batch = mapping(raw)
    if not batch:
        return None
    result = {key: batch.get(key) for key in ('id', 'status') if isinstance(batch.get(key), str)}
    result['workers'] = [{key: worker.get(key) for key in ('milestone_id', 'status', 'workspace', 'run_dir')
                          if isinstance(worker.get(key), str)}
                         for worker in rows(batch.get('workers')) if isinstance(worker, dict)]
    return result


def snapshot(state, run, detailed=False, process_snapshot=_PROCESS_TABLE_UNSET):
    settings = mapping(state.get('settings')); active = mapping(state.get('active_stage'))
    stages = [s for s in rows(state.get('stages')) if isinstance(s, dict)]
    ongoing = bool(active.get('stage') and not active.get('finished_at') and active.get('exit_code') is None)
    if ongoing and type(active.get('pid')) is int:
        table = process_table() if process_snapshot is _PROCESS_TABLE_UNSET else process_snapshot
        live = worker_status(active, run, table)
    else:
        live = worker_status(active, run, None) if ongoing else {'state': 'none', 'label': 'No active worker recorded'}
    path = local_file(run, active.get('events') or next((s.get('events') for s in reversed(stages) if s.get('events')), None))
    def modified(file):
        try:
            return stamp(file.stat().st_mtime) if file else None
        except OSError:
            return None
    roles = {role: {key: value.get(key) for key in ('model', 'reasoning_effort') if isinstance(value.get(key), str)}
             for role, value in mapping(settings.get('roles')).items()
             if role in ('glm', 'astra', 'terra', 'sol', 'completion') and isinstance(value, dict)}
    result = {'checked_at': stamp(time.time()), 'live': live, 'checkpoint_updated': modified(run / 'state.json'),
              'log_updated': modified(path), 'workflow_mode': mapping(settings.get('workflow')).get('mode'),
              'roles': roles, 'iteration_limit': mapping(settings.get('limits')).get('iteration_ceiling'),
              'limits_known': 'iteration_ceiling' in mapping(settings.get('limits')),
              'objective': mapping(state.get('current_task')).get('objective') or state.get('next_action'),
              'active_role': active.get('route_role') or active.get('role'), 'next_stage': state.get('next_stage')}
    result['orchestration'] = {key: value for key, value in mapping(settings.get('orchestration')).items()
                               if key in ('enabled', 'max_parallel')}
    result['orchestration_batch'] = batch_summary(state.get('orchestration_batch'))
    if detailed:
        result['orchestration_history'] = [batch_summary(batch) for batch in rows(state.get('orchestration_history'))
                                           if isinstance(batch, dict) and batch]
        result['findings'] = [{key: row.get(key) for key in ('severity', 'finding')}
                              for row in rows(state.get('unresolved_findings')) if isinstance(row, dict)]
        result['validation_verdict'] = mapping(state.get('validation')).get('verdict')
        result['activity'] = activity_log(path)
        result['history'] = [{k: s.get(k) for k in ('stage', 'role', 'iteration', 'finished_at', 'exit_code', 'rejected', 'interrupted', 'timed_out', 'runner_owned')} for s in stages[-6:]][::-1]
    return result
