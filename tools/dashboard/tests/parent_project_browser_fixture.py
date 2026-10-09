"""Disposable parent/worktree browser fixture; only the external runner is controlled.

Production Console, HTTP handlers, projections, stores and action validation run.
The test supplies one initial fixture checkpoint; it never reads a runner state
body or launches a controller/model. Recorded output contains public API inputs
and the external runner's status/inspect/submit arguments only.
"""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
from urllib.parse import parse_qs, urlsplit

SOURCE = Path(os.environ['AUTOCODE_TEST_SOURCE_ROOT']).resolve(strict=True)
sys.path[:0] = [str(SOURCE / 'tools/dashboard'), str(SOURCE / 'tools')]
from agent_console import Console, Handler, LoopbackHTTPServer
from dashboard_delete import git
from autocode_workspaces import create as create_worktree

root = Path(os.environ['AUTOCODE_FIXTURE_ROOT']).resolve()
root.mkdir(parents=True, exist_ok=False)
os.environ['AUTOCODE_HOME'] = str(root / 'registry')
project = root / 'ParentProject'
project.mkdir()
git(project, 'init', '-q')
(project / 'keep.txt').write_text('Keep the parent and its managed worktree.\n')
git(project, 'add', 'keep.txt')
git(project, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid',
    'commit', '-qm', 'fixture')
meta = create_worktree(project, 'Parent project identity browser fixture')
workspace = Path(meta['workspace'])
run = workspace / '.autocode/runs/parent-identity'
run.mkdir(parents=True)
# Capture only this original fixture process at creation. The unchanged monitor
# must verify its real birth; a made-up "alive" projection would bypass the UI
# capability boundary that this browser case needs to reach.
birth_result = subprocess.run(['ps', '-p', str(os.getpid()), '-o', 'lstart='],
                              capture_output=True, text=True, timeout=2,
                              env={**os.environ, 'LC_ALL': 'C'})
if birth_result.returncode or len(birth_result.stdout.split()) != 5:
    raise RuntimeError('Cannot identify the original owned fixture process')
birth = ' '.join(birth_result.stdout.split())
state = {
    'workspace': str(workspace), 'project_workspace': str(project),
    'task_branch': meta['branch'], 'run_dir': str(run),
    'task_id': 'fixture-parent-identity', 'task': 'Parent identity browser fixture',
    'status': 'RUNNING', 'phase': 'EXECUTING', 'iteration': 1,
    # The only live process is this disposable fixture, never a task controller.
    'active_stage': {'stage': 'terra', 'pid': os.getpid(),
                     'processes': [{'pid': os.getpid(), 'started': birth}]},
    'pending_questions': [], 'settings': {'engine': 'opencode', 'joint_planning': True},
}
(run / 'state.json').write_text(json.dumps(state))
request_log = root / 'public-requests.json'
request_log.write_text('[]\n')
records = []
record_lock = threading.RLock()


def record(kind, value):
    with record_lock:
        records.append({'kind': kind, **copy.deepcopy(value)})
        temporary = request_log.with_suffix('.tmp')
        temporary.write_text(json.dumps(records, sort_keys=True) + '\n')
        temporary.replace(request_log)


def forbid_provider(*args, **kwargs):
    record('unexpected_provider', {})
    raise AssertionError('This fixture does not permit model calls')


class FixtureConsole(Console):
    """Replace only the external CLI/capability seam, retaining its caller guards."""
    def _run_json_command(self, args, timeout):
        record('runner', {'argv': args, 'timeout': timeout})
        if args[:1] == ['registry']:
            return {'registry_version': 1, 'operation': args[1],
                    'registry_path': str(root / 'registry.json'),
                    'runs': [], 'workspaces': []}, None
        if '--workspace' not in args or '--run-dir' not in args:
            raise AssertionError('Unexpected external fixture command: ' + repr(args))
        if (args[args.index('--workspace') + 1] != str(workspace)
                or args[args.index('--run-dir') + 1] != str(run)):
            raise AssertionError('External runner was retargeted away from the execution worktree')
        with record_lock:
            if '--status' in args:
                return {**copy.deepcopy(state), 'interventions': {
                    'inspector_capability': {'supported': True, 'version': 1},
                    'runner_capability': {'supported': True, 'version': 1, 'supports_stop': True},
                    'applied_receipts': [], 'blocked_conditions': [],
                }}, None
            if args[:2] == ['intervention', 'inspect']:
                return {'version': 1, 'operation': 'inspect',
                        'requests': copy.deepcopy(self.fixture_inbox)}, None
            if args[:2] == ['intervention', 'submit']:
                kind = args[args.index('--kind') + 1]
                ident = args[args.index('--request-id') + 1]
                if kind != 'stop' or '--text' in args:
                    raise AssertionError('Only an empty Stop request is allowed')
                prior = next((row for row in self.fixture_inbox if row['id'] == ident), None)
                duplicate = prior is not None
                if prior is None:
                    prior = {'id': ident, 'kind': 'stop', 'text': '',
                             'order': len(self.fixture_inbox) + 1,
                             'submitted_at': '2026-10-09T00:00:00Z'}
                    self.fixture_inbox.append(prior)
                return {'version': 1, 'operation': 'submit', 'accepted': True,
                        'receipt': copy.deepcopy(prior), 'idempotent': duplicate}, None
        raise AssertionError('Unexpected external fixture command: ' + repr(args))

    def model_catalogue(self, refresh=False):
        return {'models': [], 'error': 'Offline identity fixture: no model requests'}

    def mutate(self, data):
        record('api_action', {'body': data})
        return super().mutate(data)

    def project_action(self, data):
        record('api_projects', {'body': data})
        return super().project_action(data)

    def conversation_create(self, data):
        record('api_conversations', {'body': data})
        return super().conversation_create(data)


class RecordingHandler(Handler):
    def do_POST(self):
        record('api_post_route', {'path': self.path})
        super().do_POST()

    def do_GET(self):
        url = urlsplit(self.path)
        if url.path == '/api/run':
            record('api_run', {'query': parse_qs(url.query)})
        super().do_GET()


runner = root / 'never-launch-controller.py'
runner.write_text('raise SystemExit("No task controller is permitted in this fixture")\n')
console = FixtureConsole([project, workspace], runner, lambda: False,
                         conversation_root=root / 'dashboard/conversations',
                         project_store_root=root / 'dashboard',
                         conversation_provider=forbid_provider,
                         conversation_planner=forbid_provider)
console.fixture_inbox = []
# A linked parent attachment deliberately survives a child-only project removal.
# This is known fixture input, not an inspection of another task's private data.
doc = console.conversation_create({'empty': True, 'workspace': str(project),
                                   'request_id': 'fixture-linked-parent'})
console.conversations.update(doc['id'], title=state['task'], attachment={
    'status': 'linked', 'project_workspace': str(project),
    'workspace': str(workspace), 'run': str(run),
})
server = LoopbackHTTPServer(('127.0.0.1', 0), RecordingHandler)
server.console = console
server.hosts = {'127.0.0.1:' + str(server.server_port)}
print('FIXTURE=' + json.dumps({
    'url': 'http://127.0.0.1:' + str(server.server_port),
    'project': str(project), 'workspace': str(workspace), 'run': str(run),
    'conversation': doc['id'], 'request_log': str(request_log),
    'parent_marker': str(project / 'keep.txt'), 'child_marker': str(workspace / 'keep.txt'),
}), flush=True)
try:
    server.serve_forever()
finally:
    server.server_close()
    console.pool.shutdown(wait=True)
    console.conversations.close(wait=True)
