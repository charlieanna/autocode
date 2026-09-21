"""Disposable unified dashboard fixture. Never invokes a provider.

Run from anywhere. Create/remove PRINTED_ROOT/offline to test failed task reads.
The real workspace/run is never loaded; all controls use an unavailable adapter.
"""
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console, Handler, ThreadingHTTPServer


def main():
    with tempfile.TemporaryDirectory(prefix='autocode-unified-browser-') as temporary:
        root = Path(temporary)
        os.environ['AUTOCODE_HOME'] = str(root / 'home')
        workspace = root / 'Example project'; (workspace / '.git').mkdir(parents=True)
        run = workspace / '.autocode/runs/existing-task'; run.mkdir(parents=True)
        state = {'task': 'Finish the existing course map without changing lessons', 'status': 'RUNNING',
                 'phase': 'EXECUTING', 'iteration': 28, 'active_stage': {'stage': 'terra', 'role': 'terra'},
                 'settings': {'roles': {'astra': {'model': 'gpt-6-astra', 'reasoning_effort': 'high'},
                                         'terra': {'model': 'zai-coding-plan/glm-5.3', 'reasoning_effort': 'max'},
                                         'sol': {'model': 'gpt-5.6-sol', 'reasoning_effort': 'high'}}},
                 'current_task': {'objective': 'Close the remaining graph and trie mappings, preserving every authored teaching source. Validate prerequisites and record uncertainties.'},
                 'unresolved_findings': [{'severity': 'medium', 'finding': 'Trie evidence still needs route-specific verification.'}],
                 'acceptance_criteria': [{'id': 'C1', 'criterion': 'All records reconciled'}]}
        (run / 'state.json').write_text(json.dumps(state))
        (workspace / 'coverage.json').write_text(json.dumps({'groups': {'trees': {'done': 40, 'total': 80}, 'tries': {'done': 12, 'total': 30}}}))
        (workspace / '.autocode/dashboard.json').write_text(json.dumps({'version': 1, 'metrics': [{
            'id': 'mapping', 'label': 'Course-mapping coverage', 'runs': [run.name], 'source': 'coverage.json',
            'description': 'Metadata records dispositioned — not course completion or learner mastery.',
            'groups_pointer': '/groups', 'completed_field': 'done', 'total_field': 'total'}]}))
        class FixtureConsole(Console):
            def _json_command(self, *args, **kwargs):
                operation = args[0][1]
                return {'registry_version': 1, 'operation': operation, 'registry_path': str(root / 'registry.json'), 'runs': [], 'workspaces': []}, None
            def _intervention_view(self, *args, **kwargs):
                return {'mode': 'unavailable', 'error': 'Read-only browser fixture'}
            def task_view(self, workspace, run):
                if (root / 'offline').exists():
                    raise AttributeError('Simulated detail failure')
                return super().task_view(workspace, run)
            def enqueue(self, *args, **kwargs):
                raise ValueError('No execution in this fixture')
        console = FixtureConsole([workspace], root / 'no-runner', lambda: False,
                                 conversation_root=root / 'conversations', project_store_root=root / 'dashboard')
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler); server.console = console
        server.hosts = {f'127.0.0.1:{server.server_port}', f'localhost:{server.server_port}'}
        print('ROOT=' + str(root), flush=True)
        print(f'http://127.0.0.1:{server.server_port}/#' + urlencode({'task': str(workspace), 'run': str(run)}), flush=True)
        try:
            server.serve_forever()
        finally:
            server.server_close(); console.pool.shutdown(wait=True)
            if console._conversation_store is not None:
                console._conversation_store.close()


if __name__ == '__main__':
    main()
