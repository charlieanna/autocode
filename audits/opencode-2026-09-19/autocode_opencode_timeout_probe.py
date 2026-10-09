"""Use real OpenCode with a loopback-only fake model to inspect timeout cleanup."""
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from autocode_opencode_audit_probes import fixture, oc, runner, support

root, workspace, env = fixture()
run = workspace / '.autocode/runs/timeout-probe'
run.mkdir(parents=True)
worker = workspace / 'audit_worker.py'
worker.write_text("from pathlib import Path\nimport os, time\nPath('worker-started.json').write_text(str(os.getpid()))\ntime.sleep(12)\nPath('worker-finished.txt').write_text('Wrote after timeout')\n")
subprocess.run(['git', '-C', str(workspace), 'add', 'audit_worker.py'], check=True)
subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=Audit', '-c',
                'user.email=audit@example.test', 'commit', '-qm', 'Fixture'], check=True)
requests = []


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        tool_names = [item.get('function', {}).get('name') for item in body.get('tools', [])]
        requests.append({'path': self.path, 'tool_names': tool_names})
        has_result = any(item.get('role') == 'tool' for item in body.get('messages', []))
        if 'bash' in tool_names and not has_result:
            delta = {'role': 'assistant', 'tool_calls': [{'index': 0, 'id': 'call_audit_timeout',
                'type': 'function', 'function': {'name': 'bash', 'arguments': json.dumps({
                    'command': 'python3 audit_worker.py', 'timeout': 30000,
                    'description': 'Run bounded timeout cleanup fixture'})}}]}
            finish = 'tool_calls'
        else:
            delta = {'role': 'assistant', 'content': '{"summary":"Local audit fixture"}'}
            finish = 'stop'
        self.send_response(200)
        self.send_header('Content-Type', 'text/event-stream')
        self.send_header('Connection', 'close')
        self.end_headers()
        for item in [
            {'id': 'chatcmpl-audit', 'object': 'chat.completion.chunk', 'created': int(time.time()),
             'model': 'audit-model', 'choices': [{'index': 0, 'delta': delta, 'finish_reason': None}]},
            {'id': 'chatcmpl-audit', 'object': 'chat.completion.chunk', 'created': int(time.time()),
             'model': 'audit-model', 'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}],
             'usage': {'prompt_tokens': 20, 'completion_tokens': 10, 'total_tokens': 30}},
        ]:
            self.wfile.write(('data: ' + json.dumps(item) + '\n\n').encode())
        self.wfile.write(b'data: [DONE]\n\n')
        self.wfile.flush()


server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
threading.Thread(target=server.serve_forever, daemon=True).start()
env['OPENCODE_CONFIG_CONTENT'] = json.dumps({'provider': {'audit-fixture': {
    'npm': '@ai-sdk/openai-compatible', 'name': 'Loopback-only audit',
    'options': {'baseURL': f'http://127.0.0.1:{server.server_port}/v1', 'apiKey': 'audit-placeholder'},
    'models': {'audit-model': {'name': 'Audit model', 'limit': {'context': 32000, 'output': 4096}}}}}})
state = {'version': 2, 'workspace': str(workspace), 'task': 'Timeout fixture', 'status': 'RUNNING',
         'iteration': 1, 'sessions': {}, 'stages': [], 'history': [], 'next_stage': 'terra',
         'settings': {'engine': 'opencode', 'headroom': {'enabled': False},
                      'limits': {'stage_timeout_seconds': 8},
                      'roles': {'terra': {'model': 'audit-fixture/audit-model', 'reasoning_effort': None}}}}
result = {'fixture': str(root), 'timeout_seconds': 8}
print('Fixture:', root, flush=True)
try:
    with patch.dict(os.environ, env, clear=True), support.workspace_lock(workspace):
        try:
            runner.run_role(role='terra', prompt='Execute the bounded audit fixture.\nCURRENT HANDOFF DATA\n{}',
                            sandbox='workspace-write', workspace=workspace, run_dir=run, state=state,
                            schema=runner.SCHEMA_DIR / 'v2/terra-report.schema.json',
                            model='audit-fixture/audit-model', allow_write=True, dry_run=False)
        except (RuntimeError, ValueError) as error:
            result['runner_status'] = getattr(error, 'status', type(error).__name__)
            result['runner_error'] = str(error)
    result['worker_started_before_pause'] = (workspace / 'worker-started.json').exists()
    result['worker_finished_at_pause'] = (workspace / 'worker-finished.txt').exists()
    if result['worker_started_before_pause']:
        pid = int((workspace / 'worker-started.json').read_text())
        try:
            os.kill(pid, 0)
            result['worker_alive_after_pause'] = True
        except ProcessLookupError:
            result['worker_alive_after_pause'] = False
        deadline = time.monotonic() + 14
        while not (workspace / 'worker-finished.txt').exists() and time.monotonic() < deadline:
            time.sleep(.2)
    result['worker_wrote_after_pause'] = (workspace / 'worker-finished.txt').exists() and not result['worker_finished_at_pause']
    with support.workspace_lock(workspace):
        result['workspace_lock_reacquired'] = True
finally:
    server.shutdown()
    server.server_close()
result['requests'] = requests
result['active_stage'] = state.get('active_stage')
(root / 'result.json').write_text(json.dumps(result, indent=2) + '\n')
Path('/private/tmp/autocode-opencode-timeout-result.json').write_text(json.dumps(result, indent=2) + '\n')
print(json.dumps(result, indent=2))
