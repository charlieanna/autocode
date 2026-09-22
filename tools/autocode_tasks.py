"""Persistent sequential lanes with safe parallelism across isolated worktrees."""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

try:
    from . import autocode_support as support, autocode_workspaces as workspaces
except ImportError:
    import autocode_support as support
    import autocode_workspaces as workspaces


TERMINAL_CODE = {'TASK_COMPLETE'}
WAITING_CODE = {'WAITING_FOR_USER', 'AWAITING_GOAL_APPROVAL'}
TERMINAL_UI = {'COMPLETE'}


def load_manifest(path):
    source = Path(path).resolve()
    value = json.loads(source.read_text())
    if value.get('version') != 1 or not isinstance(value.get('name'), str) or not value['name'].strip():
        raise ValueError('Task flow needs version 1 and a nonempty name')
    lanes = value.get('lanes')
    if not isinstance(lanes, list) or not lanes:
        raise ValueError('Task flow needs at least one lane')
    lane_ids = []
    for lane in lanes:
        if (set(lane) != {'id', 'tasks'} or not isinstance(lane['id'], str)
                or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', lane['id'])):
            raise ValueError('Every lane needs only id and tasks')
        lane_ids.append(lane['id'])
        if not isinstance(lane['tasks'], list) or not lane['tasks']:
            raise ValueError(f'Lane {lane["id"]} needs at least one task')
        task_ids = []
        for task in lane['tasks']:
            allowed = {'id', 'mode', 'task', 'ui_from', 'figma_file', 'engine'}
            if (set(task) - allowed or not all(isinstance(task.get(key), str) and task[key].strip()
                                                for key in ('id', 'mode', 'task'))
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,63}', task['id'])):
                raise ValueError('Each task needs nonempty id, mode and task fields')
            if task['mode'] not in ('ui', 'code'):
                raise ValueError('Task mode must be ui or code')
            if task.get('engine') not in (None, 'codex', 'opencode') or (task.get('engine') and task['mode'] != 'code'):
                raise ValueError('engine must be codex or opencode and is available only for code tasks')
            if task.get('ui_from') and task['mode'] != 'code':
                raise ValueError('ui_from is available only for code tasks')
            task_ids.append(task['id'])
        if len(task_ids) != len(set(task_ids)):
            raise ValueError(f'Lane {lane["id"]} has duplicate task IDs')
        positions = {task['id']: index for index, task in enumerate(lane['tasks'])}
        for index, task in enumerate(lane['tasks']):
            if task.get('ui_from') not in (None, '') and positions.get(task['ui_from'], index) >= index:
                raise ValueError('ui_from must name an earlier task in the same lane')
    if len(lane_ids) != len(set(lane_ids)):
        raise ValueError('Lane IDs must be unique')
    return source, value


def flow_key(source, manifest):
    raw = source.read_bytes()
    slug = re.sub('[^a-z0-9]+', '-', manifest['name'].lower()).strip('-')[:36] or 'tasks'
    return slug + '-' + hashlib.sha256(raw).hexdigest()[:10]


def new_state(source, manifest, project):
    return {'version': 1, 'name': manifest['name'], 'manifest': str(source),
            'manifest_sha256': support.file_hash(source), 'project_workspace': str(project),
            'created_at': support.now(), 'status': 'RUNNING',
            'lanes': {lane['id']: {'workspace': None, 'tasks': {
                task['id']: {'status': 'PENDING', 'mode': task['mode'], 'task': task['task']}
                for task in lane['tasks']}} for lane in manifest['lanes']}}


def refresh_task(record):
    run = record.get('run_dir')
    if not run:
        return
    path = Path(run) / 'state.json'
    if not path.is_file():
        return
    saved = json.loads(path.read_text())
    status = saved.get('status')
    record['run_status'] = status
    if status in (TERMINAL_UI if record['mode'] == 'ui' else TERMINAL_CODE):
        record.update(status='COMPLETE', finished_at=support.now())
        if record['mode'] == 'ui':
            record['figma_file'] = saved.get('figma_file')
    elif status in WAITING_CODE:
        record['status'] = 'WAITING'
    elif status not in ('RUNNING', None):
        record['status'] = 'PAUSED'


def ready(manifest, state):
    rows = []
    for lane in manifest['lanes']:
        saved = state['lanes'][lane['id']]
        for index, task in enumerate(lane['tasks']):
            record = saved['tasks'][task['id']]
            refresh_task(record)
            if record['status'] == 'PENDING' and all(
                    saved['tasks'][prior['id']]['status'] == 'COMPLETE' for prior in lane['tasks'][:index]):
                rows.append((lane, task, record))
            if record['status'] != 'COMPLETE':
                break
    return rows


def launch_task(project, flow_dir, lane, task, record, lane_state):
    workspace = Path(lane_state['workspace'])
    runner = Path(__file__).with_name('autocode.py')
    if task['mode'] == 'ui':
        run = flow_dir / lane['id'] / task['id'] / 'ui'
        command = [sys.executable, str(runner), 'ui', task['task'], '--workspace', str(workspace),
                   '--run-dir', str(run)]
        if task.get('figma_file'):
            command += ['--figma-file', task['figma_file']]
    else:
        command = [sys.executable, str(runner), task['task'], '--workspace', str(workspace),
                   '--in-place', '--no-chat']
        if task.get('engine'):
            command += ['--engine', task['engine']]
        if task.get('ui_from'):
            source = lane_state['tasks'][task['ui_from']]
            if source.get('status') != 'COMPLETE' or source.get('mode') != 'ui':
                raise ValueError(f'{task["id"]} needs completed UI task {task["ui_from"]}')
            command += ['--ui-run', source['run_dir']]
    record.update(status='RUNNING', started_at=support.now(), command=command)
    before = set(workspace.glob('.autocode/runs/*'))
    result = subprocess.run(command, cwd=workspace, capture_output=True, text=True)
    artifact = flow_dir / lane['id'] / task['id']
    artifact.mkdir(parents=True, exist_ok=True)
    (artifact / 'stdout.log').write_text(result.stdout)
    (artifact / 'stderr.log').write_text(result.stderr)
    if task['mode'] == 'ui':
        record['run_dir'] = str(run)
    else:
        created = set(workspace.glob('.autocode/runs/*')) - before
        if len(created) == 1:
            record['run_dir'] = str(created.pop().resolve())
    record.update(exit_code=result.returncode, finished_at=support.now())
    refresh_task(record)
    if record['status'] == 'RUNNING':
        record['status'] = 'FAILED' if result.returncode not in (0, 2) or not record.get('run_dir') else 'WAITING'
    return record


def summarize(manifest, state, state_path):
    lanes = []
    for lane in manifest['lanes']:
        saved = state['lanes'][lane['id']]
        tasks = [{**task, **saved['tasks'][task['id']]} for task in lane['tasks']]
        lanes.append({'id': lane['id'], 'workspace': saved['workspace'], 'tasks': tasks})
    statuses = [task['status'] for lane in lanes for task in lane['tasks']]
    state['status'] = ('COMPLETE' if all(value == 'COMPLETE' for value in statuses) else
                       'BLOCKED' if any(value == 'FAILED' for value in statuses) else
                       'WAITING' if any(value in ('WAITING', 'PAUSED') for value in statuses) else 'RUNNING')
    return {'flow': state['name'], 'status': state['status'], 'state_file': str(state_path), 'lanes': lanes}


def execute_flow(args, source, manifest, project, flow_dir, state_path):
    state = json.loads(state_path.read_text()) if state_path.is_file() else new_state(source, manifest, project)
    if state['manifest_sha256'] != support.file_hash(source) or state['project_workspace'] != str(project):
        raise ValueError('Task flow manifest or project differs from its saved checkpoint')
    while True:
        rows = ready(manifest, state)
        if not rows:
            break
        for lane, _task, _record in rows:
            lane_state = state['lanes'][lane['id']]
            if not lane_state['workspace']:
                lane_state['workspace'] = workspaces.create(
                    project, f'{manifest["name"]} {lane["id"]}')['workspace']
        support.atomic_json(state_path, state)
        with ThreadPoolExecutor(max_workers=args.max_parallel) as pool:
            futures = {pool.submit(launch_task, project, flow_dir, lane, task, record,
                                   state['lanes'][lane['id']]): (lane, task)
                       for lane, task, record in rows[:args.max_parallel]}
            for future in as_completed(futures):
                lane, task = futures[future]
                try:
                    future.result()
                except Exception as error:
                    state['lanes'][lane['id']]['tasks'][task['id']].update(
                        status='FAILED', error=str(error), finished_at=support.now())
                support.atomic_json(state_path, state)
    result = summarize(manifest, state, state_path)
    support.atomic_json(state_path, state)
    print(json.dumps(result, indent=2))
    return 0 if state['status'] == 'COMPLETE' else 2


def cli(argv=None):
    parser = argparse.ArgumentParser(description='Run sequential Autocode task lanes in parallel worktrees')
    parser.add_argument('manifest', type=Path)
    parser.add_argument('--workspace', type=Path, default=Path.cwd())
    parser.add_argument('--max-parallel', type=int, default=2)
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    if args.max_parallel < 1:
        parser.error('--max-parallel must be positive')
    try:
        source, manifest = load_manifest(args.manifest)
        project = args.workspace.resolve()
        if Path(workspaces.git(project, 'rev-parse', '--show-toplevel')).resolve() != project:
            raise ValueError('Select the root of a Git checkout')
        workspaces.git(project, 'rev-parse', '--verify', 'HEAD')
    except (OSError, ValueError, json.JSONDecodeError) as error:
        parser.error(str(error))
    key = flow_key(source, manifest)
    flow_dir = project / '.autocode/task-flows' / key
    state_path = flow_dir / 'state.json'
    if args.dry_run:
        preview = new_state(source, manifest, project)
        print(json.dumps(summarize(manifest, preview, state_path), indent=2))
        return 0
    flow_dir.mkdir(parents=True, exist_ok=True)
    try:
        with support.workspace_lock(flow_dir):
            return execute_flow(args, source, manifest, project, flow_dir, state_path)
    except (support.Paused, ValueError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(cli())
