"""Actual dashboard over disposable recovery states; no provider or real runner.

Only the CLI status/execute seam is supplied. Public projection, HTTP action
validation and browser code are production code. CLI execution is separately
covered by test_builder_recovery against the real runner and a fake provider.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console, Handler, LoopbackHTTPServer
from unified_browser_fixture import base_state, FIXTURE_NOW, fixture_base_directory
import autocode_run_view as run_view
import autocode_recovery_view as recovery


def main():
    with tempfile.TemporaryDirectory(prefix='recovery-', dir=fixture_base_directory()) as directory:
        root = Path(directory).resolve()
        os.environ['AUTOCODE_HOME'] = str(root / 'home')
        workspace = root / 'Recovery project'
        (workspace / '.git').mkdir(parents=True)
        (workspace / 'kept.txt').write_text('Existing project work stays intact.\n')
        runs = workspace / '.autocode/runs'
        names = []
        for viewport in ('desktop','tablet','mobile'):
            for kind in ('builder','interruption','unknown','stopped','internal','stale','source'):
                name = kind+'-'+viewport
                names.append(name)
                run = runs / name
                run.mkdir(parents=True)
                state = base_state(workspace, 'Inspect '+kind+' recovery', status='PAUSED_REQUESTED')
                state.update(run_dir=str(run), task_id=name, next_stage='terra')
                state.pop('active_stage', None)
                state['settings'].pop('workflow', None)
                state['settings']['builder_retry'] = {'enabled': True}
                state['current_task'].update(id='fixture-task', milestone_id='M1', kind='implement')
                state['_fixture_monitor']['live'] = {'state':'none','label':'No worker in this disposable fixture'}
                if kind == 'builder':
                    state['status'] = 'PAUSED_BUILDER_RETRY_LIMIT'
                    state['builder_retries'] = {recovery.builder_policy.key(state): {'action':'pause','failures':['one','two']}}
                    state['failure_history'] = {'first': {'identity': {'stage':'terra','artifact_hash':'source-one'}, 'count':2,'attempts':['1','2'],'last_error':'Missing accessible label','last_seen':'2026-10-01'},
                                                'second': {'identity': {'stage':'terra','artifact_hash':'source-two'}, 'count':1,'attempts':['3'],'last_error':'Focus left the chat','last_seen':'2026-10-02'}}
                elif kind == 'interruption':
                    state['status'] = 'PAUSED_PROVIDER_UNCERTAIN'
                    state['active_stage'] = {'iteration':31,'stage':'terra','output':str(run/'builder-01.json'),'started_at':FIXTURE_NOW}
                elif kind == 'unknown':
                    state['status'] = 'PAUSED_FUTURE_REASON'
                    state['stop_reason'] = 'A newly introduced pause needs inspection.'
                elif kind == 'stale':
                    state['status'] = 'RUNNING'
                    state['active_stage'] = {'stage':'terra','iteration':31,'output':str(run/'builder-01.json')}
                    state['_fixture_monitor']['live'] = {'state':'exited','label':'Recorded worker exited'}
                elif kind == 'source':
                    state['status'] = 'PAUSED_STAGE_ABANDONED'
                    state['next_stage'] = 'investigate_bug'
                    state['job_failure'] = {'reason': 'Provider stopped', 'stage': 'investigate_bug',
                        'attempt_id': '001/bug-investigation-01', 'job_retry_token': 'historical-token',
                        'archive': str(run / 'archive'), 'source_identity': None,
                        'write_diagnosis': {}, 'unrestored': ['original source capture']}
                elif kind == 'internal':
                    state['status'] = 'WAITING_FOR_USER'
                    state['pending_questions'] = [{'id':'internal-question','question':'Unpublished internal question?'}]
                else:
                    state['status'] = 'PAUSED_INTERVENTION'
                    state['applied_interventions'] = [{'id':'fixture-stop','kind':'stop','applied_at':FIXTURE_NOW}]
                (run/'state.json').write_text(json.dumps(state))

        class FixtureConsole(Console):
            def state(self, run):
                run = Path(run).resolve()
                if run.parent != runs:
                    raise ValueError('Only this disposable fixture is available')
                return json.loads((run/'state.json').read_text())

            def _json_command(self, command, **kwargs):
                if '--status' in command:
                    run = Path(command[command.index('--run-dir')+1])
                    state = self.state(run)
                    stop = next((row for row in state.get('applied_interventions',[]) if row.get('kind')=='stop'), None)
                    return {'status':state['status'], 'stale':run.name.startswith('stale-'), 'active_stage':state.get('active_stage'), 'view':run_view.view(state),
                            'interventions':{'inspector_capability':{'supported':True,'version':1},
                                             'runner_capability':{'supported':True,'version':1,'supports_stop':True},
                                             'applied_receipts':[],'blocked_conditions':[], 'stop_intent':stop}}, None
                if command[:2] == ['intervention','inspect']:
                    return {'version':1,'operation':'inspect','requests':[]}, None
                return {'registry_version':1,'operation':command[1], 'registry_path':str(root/'registry.json'),'runs':[],'workspaces':[]}, None

            def task_view(self, workspace, run):
                result = super().task_view(workspace, run)
                result['monitor'] = self.state(run)['_fixture_monitor']
                return result

            def enqueue(self, workspace, run, label, extra, *args, **kwargs):
                state = self.state(run)
                if '--expected-recovery-token' not in extra:
                    raise ValueError('This fixture accepts only inspected recovery actions')
                recovery.require_token(state, extra[extra.index('--expected-recovery-token')+1])
                if '--abandon-stage' in extra:
                    state.pop('active_stage', None)
                    state['status'] = 'PAUSED_INTERVENTION'
                    state['stages'].append({'stage':'terra','iteration':31,'finished_at':FIXTURE_NOW,'abandoned':True})
                elif '--retry-builder' in extra:
                    state['iteration'] += 1
                    state['builder_retries'][recovery.builder_policy.key(state)]['failures'].append('retained-new-failure')
                    state['stages'].append({'stage':'terra','iteration':state['iteration'],'finished_at':FIXTURE_NOW,'exit_code':1})
                else:
                    state['status'] = 'RUNNING'
                (run/'state.json').write_text(json.dumps(state))
                record = {'id':str(time.time_ns()),'label':label,'command':['fixture-cli',*extra],'status':'finished','exit_status':0,
                          'queued_at':time.time(),'started_at':time.time(),'finished_at':time.time(),'stdout':'Disposable fixture command recorded.','stderr':''}
                self.actions.setdefault(str(run),[]).append(record)
                self.status_cache.pop(str(run),None)
                return record

        console = FixtureConsole([workspace],root/'no-runner',lambda:False,
            conversation_root=root/'conversations', project_store_root=root/'dashboard',
            catalogue_command=(sys.executable,'-c',"print('openai/gpt-6-astra\\nopenai/gpt-5.6-terra\\nopenai/gpt-5.6-sol')"))
        server=LoopbackHTTPServer(('127.0.0.1',0),Handler)
        server.console=console
        server.hosts={f'127.0.0.1:{server.server_port}',f'localhost:{server.server_port}'}
        base=f'http://127.0.0.1:{server.server_port}/'
        print('FIXTURE='+json.dumps({'base_url':base,'workspace':str(workspace),'scenarios':{name:base+'#'+urlencode({'task':str(workspace),'run':str(runs/name)}) for name in names}}),flush=True)
        try: server.serve_forever()
        finally: server.server_close();console.pool.shutdown(wait=True)


if __name__ == '__main__':
    main()
