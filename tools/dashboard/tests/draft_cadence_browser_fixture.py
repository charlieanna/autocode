"""Real chat HTTP/UI with offline provider callbacks and owned disposable data."""
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import time
from urllib.parse import urlencode

TOOLS = Path(__file__).resolve().parents[2]
sys.path[:0] = [str(TOOLS / 'dashboard'), str(TOOLS)]
from agent_console import Console, Handler, LoopbackHTTPServer
from conversation_transport import ConversationProviderError
from dashboard_setup import conversation_model_fields


def main():
    base = Path(os.environ['AUTOCODE_FIXTURE_ROOT'])
    base.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='draft-cadence-', dir=base) as temporary:
        root = Path(temporary).resolve()
        os.environ['AUTOCODE_HOME'] = str(root / 'home')
        project = root / 'Example project'
        (project / '.git').mkdir(parents=True)
        calls, lock = [], threading.Lock()
        call_path = root / 'provider-calls.json'
        # A 'Slow draft' Planner call stays in flight, after a real launch
        # receipt, until the browser test creates this file.
        gate = root / 'release-slow-draft'

        def record(role, messages):
            human = next(row for row in reversed(messages) if row['role'] == 'user')
            with lock:
                calls.append({'role': role, 'turn': human['logical_turn_id'], 'text': human['text']})
                pending = call_path.with_suffix('.tmp')
                pending.write_text(json.dumps(calls)); pending.replace(call_path)
            return human

        def gatherer(messages, model, workdir):
            record('gatherer', messages)
            return 'Your answer is saved. Tell me about the next requirement.'

        def planner(messages, route, workdir, **delivery):
            human = record('planner', messages)
            with lock:
                attempts = sum(row['role'] == 'planner' and row['turn'] == human['logical_turn_id'] for row in calls)
            if human['text'] == 'Safe draft failure' and attempts == 1:
                delivery['dispatch_observer']('safe_not_dispatched', {})
                raise ConversationProviderError('Fixture worker unavailable before launch', outcome='not_dispatched')
            if human['text'] == 'Ambiguous draft failure':
                raise ConversationProviderError('Fixture lost the provider receipt')
            if human['text'].startswith('Slow draft'):
                delivery['dispatch_observer']('process_starting', {})
                delivery['dispatch_observer']('process_started', {'owned': True, 'pid': None})
                deadline = time.monotonic() + 120
                while not gate.exists() and time.monotonic() < deadline:
                    time.sleep(0.05)
            return json.dumps({'contract_version': 1, 'kind': 'autocode.planner-structured-draft',
                'goal': 'Build a clear chat workspace', 'requirements': ['Keep project conversations and all answers'],
                'milestones': ['Build and verify the chat'], 'parallelism': [], 'unresolved_questions': [],
                'source_revision': {'requirements_revision': sum(row['role'] == 'user' for row in messages),
                                    'logical_turn_id': human['logical_turn_id']},
                'attribution': {'role': 'planner', 'model': route['model'], 'reasoning_effort': route['reasoning_effort']},
                'freshness': {'state': 'fresh', 'updated_at': '2026-10-03T00:00:00+00:00'}})

        # Only this offline fixture supplies the precise pre-launch receipt; an
        # unobserved callback must remain uncertain and must never be retried.
        planner.autocode_dispatch_aware = True

        class OfflineConsole(Console):
            def _probe_conversation_transport(self, executable):
                # Supply only the external version probe; real route and transport
                # admission still run without a host tool or a provider request.
                return {'status': 'ok', 'data': {'version': '1.18.33'}}

            def _json_command(self, command, **kwargs):
                if command[0] != 'registry':
                    raise AssertionError('This fixture must not invoke a task runner')
                return {'registry_version': 1, 'operation': command[1], 'registry_path': str(root/'registry.json'),
                        'runs': [], 'workspaces': []}, None

            def enqueue(self, *args, **kwargs):
                raise ValueError('No implementation or task control is allowed in this fixture')

        catalogue = root / 'models.py'
        models = sorted({route['model'] for route in conversation_model_fields()['conversation_routes'].values()})
        catalogue.write_text('print(' + repr('\n'.join(models)) + ')\n', encoding='utf8')
        console = OfflineConsole([project], root/'no-runner', lambda: False,
            conversation_root=root/'conversations', project_store_root=root/'dashboard',
            catalogue_command=(sys.executable, str(catalogue)),
            conversation_provider=gatherer, conversation_planner=planner)
        documents = {size: console.conversations.create_empty(workspace=str(project), request_id='empty-'+size)
                     for size in ('desktop','tablet','mobile')}
        server = LoopbackHTTPServer(('127.0.0.1',0),Handler)
        server.console = console
        server.hosts = {f'127.0.0.1:{server.server_port}', f'localhost:{server.server_port}'}
        base_url = f'http://127.0.0.1:{server.server_port}/'
        print('FIXTURE='+json.dumps({'urls':{size:base_url+'#'+urlencode({'conversation':doc['id']})
            for size,doc in documents.items()},'calls':str(call_path),'gate':str(gate)}),flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close();console.pool.shutdown(wait=True);console.conversations.close()


if __name__ == '__main__':
    main()
