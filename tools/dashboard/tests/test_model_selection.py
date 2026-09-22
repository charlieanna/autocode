import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console, Handler, ModelCatalogue, ThreadingHTTPServer, saved_models


class ModelSelectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        isolation = patch.dict(os.environ, {'AUTOCODE_HOME': str(self.root / 'registry-home')})
        isolation.start()
        self.addCleanup(isolation.stop)
        self.workspace = self.root / 'project'
        (self.workspace / '.git').mkdir(parents=True)
        self.run = self.workspace / '.autocode/runs/run'
        self.run.mkdir(parents=True)
        self.runner = self.root / 'runner.py'
        self.runner.write_text('import sys\n')
        self.catalogue = self.root / 'catalogue.py'
        self.catalogue.write_text("import sys\nprint('openai/gpt-6-astra')\nprint('zai-coding-plan/glm-5.3')\nprint('zai-coding-plan/glm-5.3-flash')\nprint('openai/gpt-5.6-terra')\nprint('openai/gpt-6-astra')\nprint('not a model')\n")
        self.console = Console([], self.runner, lambda: None, catalogue_command=(sys.executable, str(self.catalogue)))
        self.addCleanup(self.console.pool.shutdown, wait=True)

    def wait(self):
        for _ in range(100):
            actions = self.console.action_log(self.workspace)
            if actions and actions[-1]['status'] not in ('queued', 'running'):
                return actions[-1]
            time.sleep(.01)
        self.fail('timed out')

    def test_catalogue_parses_deduplicates_caches_and_single_flights(self):
        calls = self.root / 'catalogue-calls'
        self.catalogue.write_text('import time\nfrom pathlib import Path\n'
                                  f'with Path({str(calls)!r}).open("a") as stream: stream.write("called\\n")\n'
                                  'time.sleep(.05)\n'
                                  'print("openai/gpt-6-astra\\nopenai/gpt-6-astra\\nzai-coding-plan/glm-5.3\\ninvalid value")\n')
        catalogue = ModelCatalogue((sys.executable, str(self.catalogue)), ttl=300)
        results = []
        threads = [threading.Thread(target=lambda: results.append(catalogue.fetch())) for _ in range(4)]
        for thread in threads: thread.start()
        for thread in threads:
            thread.join(timeout=2)
            self.assertFalse(thread.is_alive())
        self.assertEqual(4, len(results))
        self.assertTrue(all(result['usable'] for result in results))
        self.assertEqual(['openai/gpt-6-astra', 'zai-coding-plan/glm-5.3'], catalogue.fetch()['models'])
        self.assertEqual(['called'], calls.read_text().splitlines())
        self.assertTrue(catalogue.fetch(refresh=True)['usable'])
        self.assertEqual(['called', 'called'], calls.read_text().splitlines())

    def test_catalogue_failure_timeout_and_oversized_output_are_honest(self):
        scripts = [
            ('nonzero', 'import sys\nprint("broken", file=sys.stderr)\nraise SystemExit(1)\n'),
            ('empty', ''),
            ('malformed', 'print("not a provider/model identifier")\n'),
            ('stdout overflow', 'print("x" * 1048576)\n'),
            ('stderr overflow', 'import sys\nprint("x" * 1048576, file=sys.stderr)\n'),
        ]
        for name, script in scripts:
            with self.subTest(name=name):
                self.catalogue.write_text(script)
                catalogue = ModelCatalogue((sys.executable, str(self.catalogue)), output_limit=1024)
                status = catalogue.fetch()
                self.assertFalse(status['usable'])
                self.assertTrue(status['error'])
        missing = ModelCatalogue((str(self.root / 'missing-command'),)).fetch()
        self.assertFalse(missing['usable'])
        self.assertTrue(missing['error'])
        self.catalogue.write_text('import time\ntime.sleep(2)\n')
        started = time.monotonic()
        status = ModelCatalogue((sys.executable, str(self.catalogue)), timeout=.15).fetch()
        self.assertIn('timed out', status['error'])
        self.assertLess(time.monotonic() - started, 1.5)

    def test_catalogue_timeout_also_closes_inherited_child_pipes(self):
        self.catalogue.write_text('import subprocess,sys,time\n'
                                  'subprocess.Popen([sys.executable,"-c","import time; time.sleep(5)"])\n'
                                  'time.sleep(5)\n')
        started = time.monotonic()
        status = ModelCatalogue((sys.executable, str(self.catalogue)), timeout=.15).fetch()
        self.assertIn('timed out', status['error'])
        self.assertLess(time.monotonic() - started, 1.5)

    def test_failed_expired_catalogue_cannot_authorize_old_model_choices(self):
        catalogue = self.console.catalogue
        catalogue.ttl = 0
        initial = catalogue.fetch()
        self.assertTrue(initial['usable'])
        self.catalogue.write_text('raise SystemExit(7)\n')
        failed = catalogue.fetch()
        self.assertFalse(failed['usable'])
        self.assertTrue(failed['error'])
        self.assertEqual(initial['models'], failed['models'])
        with self.assertRaisesRegex(ValueError, 'catalogue'):
            self.console.create({'project': str(self.workspace), 'goal': 'blocked', 'glm_model': 'zai-coding-plan/glm-5.3'})
        self.assertEqual([], self.console.action_log(self.workspace))
        self.assertIn('default', self.console.create({'project': str(self.workspace), 'goal': 'default'})['command'])
        self.wait()

    def test_joint_arguments_are_explicit_only_and_invalid_routes_do_not_launch(self):
        default = self.console.create({'project': str(self.workspace), 'goal': 'default', 'engine': 'opencode'})
        prefix = [sys.executable, str(self.runner.resolve()), '--workspace', str(self.workspace.resolve())]
        joint = ['--engine', 'opencode', '--joint-planning', '--no-chat']
        self.assertEqual(prefix + ['default'] + joint, default['command'])
        self.wait()
        for role, model in [('glm', 'zai-coding-plan/glm-5.3'), ('astra', 'gpt-6-astra'), ('terra', 'zai-coding-plan/glm-5.3-flash'), ('sol', 'gpt-5.6-sol'), ('completion', 'gpt-5.6-sol')]:
            each = self.console.create({'project': str(self.workspace), 'goal': role, 'engine': 'opencode', role+'_model': model})
            self.assertEqual(prefix + [role] + joint + ['--'+role+'-model', model], each['command'])
            self.wait()
        mixed = self.console.create({'project': str(self.workspace), 'goal': 'mixed', 'engine': 'opencode', 'astra_model': 'gpt-5.6-sol', 'sol_model': 'gpt-5.6-terra'})
        self.assertEqual(prefix + ['mixed'] + joint + ['--astra-model', 'gpt-5.6-sol', '--sol-model', 'gpt-5.6-terra'], mixed['command'])
        self.wait()
        all_roles = self.console.create({'project': str(self.workspace), 'goal': 'all', 'engine': 'opencode', 'glm_model': 'zai-coding-plan/glm-5.3-flash', 'astra_model': 'gpt-6-astra', 'terra_model': 'zai-coding-plan/glm-5.3', 'sol_model': 'gpt-5.6-sol'})
        self.assertEqual(prefix + ['all'] + joint + ['--glm-model', 'zai-coding-plan/glm-5.3-flash', '--astra-model', 'gpt-6-astra', '--terra-model', 'zai-coding-plan/glm-5.3', '--sol-model', 'gpt-5.6-sol'], all_roles['command'])
        self.wait()
        launches = len(self.console.action_log(self.workspace))
        for role in ('glm', 'terra'):
            for value in ('bad model', 'zai-coding-plan/not-listed', 'glm-5.3'):
                with self.subTest(role=role, value=value), self.assertRaisesRegex(ValueError, 'provider/model|catalogue'):
                    self.console.create({'project': str(self.workspace), 'goal': 'reject', 'engine': 'opencode', role+'_model': value})
        for role in ('astra', 'sol'):
            for value in ('openai/unlisted', 'zai-coding-plan/unlisted', 'glm-5.3', 'unknown'):
                with self.subTest(role=role, value=value), self.assertRaisesRegex(ValueError, 'provider/model|catalogue'):
                    self.console.create({'project': str(self.workspace), 'goal': 'reject', 'engine': 'opencode', role+'_model': value})
        for role in ('glm', 'astra', 'terra', 'sol'):
            for value in [None, ['not', 'a', 'string'], {}, False, 42]:
                with self.assertRaisesRegex(ValueError, 'must be strings'):
                    self.console.create({'project': str(self.workspace), 'goal': 'reject', 'engine': 'opencode', role+'_model': value})
        self.assertEqual(launches, len(self.console.action_log(self.workspace)))

    def test_every_role_uses_actual_opencode_catalogue_for_all_providers(self):
        values = ['openai/gpt-5.6-terra', 'openai/new-model', 'another-provider/model-v1', 'local/model:32b']
        with patch.object(self.console.catalogue, 'fetch', return_value={'usable':True,'models':values}) as fetch:
            for role in ('glm', 'astra', 'terra', 'sol'):
                for model in values:
                    action = self.console.create({'project': str(self.workspace), 'goal': 'mixed subscriptions',
                                                  role+'_model': model})
                    self.assertEqual(['--engine', 'opencode', '--joint-planning', '--no-chat', '--'+role+'-model', model],
                                     action['command'][-6:])
                    self.wait()
            self.assertEqual(4 * len(values), fetch.call_count)
        with self.assertRaisesRegex(ValueError, 'catalogue'):
            self.console.create({'project': str(self.workspace), 'goal': 'not a known Codex choice',
                                 'terra_model': 'openai/unlisted-model'})
        with self.assertRaisesRegex(ValueError, 'provider/model'):
            self.console.create({'project': str(self.workspace), 'goal': 'wrong transport', 'terra_model': 'gpt-5.6-terra'})
        with patch.object(self.console.catalogue, 'fetch', return_value={'usable':False,'error':'offline','models':values}):
            with self.assertRaisesRegex(ValueError, 'offline'):
                self.console.create({'project': str(self.workspace), 'goal': 'catalogue unavailable', 'terra_model': values[0]})

    def test_fake_runner_records_explicit_and_inherited_role_settings(self):
        self.runner.write_text("""import json,sys
from pathlib import Path
args=sys.argv[1:]
workspace=Path(args[args.index('--workspace')+1])
assert '--joint-planning' in args and '--no-chat' in args
models={'glm':'zai-coding-plan/glm-5.3','astra':'gpt-6-astra','terra':'zai-coding-plan/glm-5.3','sol':'gpt-5.6-sol','completion':'gpt-5.6-sol'}
for role in models:
 if '--'+role+'-model' in args: models[role]=args[args.index('--'+role+'-model')+1]
run=workspace/'.autocode/runs/created'
run.mkdir(parents=True,exist_ok=True)
(run/'state.json').write_text(json.dumps({'task':'created','settings':{'engine':'opencode','joint_planning':True,'roles':{role:{'model':model,'engine':'codex' if role in ('astra','sol','completion') else 'opencode'} for role,model in models.items()}}}))
""")
        self.console.create({'project': str(self.workspace), 'goal': 'persisted', 'engine': 'opencode', 'terra_model': 'zai-coding-plan/glm-5.3-flash'})
        self.assertEqual(0, self.wait()['exit_status'])
        run = self.workspace / '.autocode/runs/created'
        reopened = Console([self.workspace], self.runner, lambda: None, catalogue_command=(sys.executable, str(self.catalogue)))
        self.addCleanup(reopened.pool.shutdown, wait=True)
        saved = reopened.view(self.workspace, run)['model_settings']
        self.assertTrue(saved['joint_planning'])
        self.assertEqual({'glm': 'zai-coding-plan/glm-5.3', 'astra': 'gpt-6-astra', 'terra': 'zai-coding-plan/glm-5.3-flash', 'sol': 'gpt-5.6-sol', 'completion': 'gpt-5.6-sol'}, saved['roles'])
        self.assertEqual({'glm': 'opencode', 'astra': 'codex', 'terra': 'opencode', 'sol': 'codex', 'completion': 'codex'}, saved['role_engines'])

    def test_default_creation_survives_catalogue_failure_but_override_does_not(self):
        self.catalogue.write_text('raise SystemExit(2)\n')
        action = self.console.create({'project': str(self.workspace), 'goal': 'still works', 'engine': 'opencode'})
        self.assertIn('still works', action['command'])
        self.wait()
        with patch.object(self.console.catalogue, 'fetch', side_effect=AssertionError('Codex choices do not need the OpenCode catalogue')):
            self.console.create({'project': str(self.workspace), 'goal': 'Codex role works', 'astra_model': 'gpt-5.6-sol', 'sol_model': 'gpt-6-astra'})
            self.wait()
        with self.assertRaisesRegex(ValueError, 'catalogue'):
            self.console.create({'project': str(self.workspace), 'goal': 'blocked', 'engine': 'opencode', 'glm_model': 'zai-coding-plan/glm-5.3'})

    def test_saved_effective_models_are_projected_without_inference(self):
        state = {'task': 'saved', 'settings': {'engine': 'opencode', 'roles': {'astra': {'model': 'openai/gpt-6-astra', 'reasoning_effort': 'xhigh'}, 'terra': {'model': 'openai/gpt-5.6-terra'}}}}
        (self.run / 'state.json').write_text(json.dumps(state))
        settings = self.console.view(self.workspace, self.run)['model_settings']
        self.assertEqual('opencode', settings['engine'])
        self.assertEqual('openai/gpt-6-astra', settings['roles']['astra'])
        self.assertIsNone(settings['roles']['sol'])
        self.assertNotIn('glm', settings['roles'])
        self.assertFalse(settings['joint_planning'])
        self.assertEqual({'astra': 'opencode', 'terra': 'opencode', 'sol': None}, settings['role_engines'])
        self.assertEqual({'astra': 'xhigh', 'terra': None, 'sol': None}, settings['role_efforts'])
        self.assertEqual({'astra': None, 'terra': None, 'sol': None}, saved_models({})['role_engines'])
        old_joint = saved_models({'settings': {'engine': 'opencode', 'joint_planning': True, 'roles': {'astra': {'model': 'gpt-6-astra', 'engine': 'codex'}, 'sol': {'model': 'zai-coding-plan/glm-5.3', 'engine': 'opencode'}, 'glm': {'model': 'zai-coding-plan/glm-5.3'}}}})
        self.assertEqual('opencode', old_joint['role_engines']['sol'])
        self.assertEqual('opencode', old_joint['role_engines']['glm'])

    def test_legacy_codex_creation_has_explicit_engine_and_never_enables_joint(self):
        action = self.console.create({'project': str(self.workspace), 'goal': 'legacy', 'engine': 'codex'})
        self.assertEqual([sys.executable, str(self.runner.resolve()), '--workspace', str(self.workspace.resolve()), 'legacy', '--engine', 'codex', '--no-chat', '--astra-model', 'gpt-5.6-sol', '--terra-model', 'gpt-5.6-terra', '--sol-model', 'gpt-5.6-sol', '--completion-model', 'gpt-5.6-sol', '--astra-reasoning-effort', 'high', '--terra-reasoning-effort', 'medium', '--sol-reasoning-effort', 'high', '--completion-reasoning-effort', 'medium'], action['command'])
        self.assertNotIn('--joint-planning', action['command'])
        self.wait()
        with self.assertRaisesRegex(ValueError, 'joint-planning'):
            self.console.create({'project': str(self.workspace), 'goal': 'reject', 'engine': 'codex', 'glm_model': 'zai-coding-plan/glm-5.3'})

    def test_catalogue_retry_requires_same_origin_and_polling_does_not_invoke_it(self):
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.console = self.console
        authority = '127.0.0.1:' + str(server.server_port)
        server.hosts = {authority}
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, headers):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port)
            connection.request(method, path, '{}', headers)
            response = connection.getresponse(); status = response.status; response.read(); connection.close(); return status
        try:
            with patch.object(self.console.catalogue, 'fetch', wraps=self.console.catalogue.fetch) as fetch:
                self.assertEqual(200, request('GET', '/api/runs', {'Host': authority}))
                self.assertEqual(0, fetch.call_count)
                self.assertEqual(403, request('POST', '/api/models/refresh', {'Host': 'evil.example', 'Content-Type': 'application/json'}))
                self.assertEqual(202, request('POST', '/api/models/refresh', {'Host': authority, 'Origin': 'http://' + authority, 'Content-Type': 'application/json'}))
                self.assertEqual(1, fetch.call_count)
        finally:
            server.shutdown(); server.server_close()

    def test_served_form_has_prominent_isolated_role_selectors_and_retention_logic(self):
        from agent_console import APP, INDEX
        self.assertIn('Requirements planner <small>Drafts the requirements plan and applies reviewer feedback</small>', INDEX)
        self.assertIn('Plan reviewer <small>Sol High challenges and finalizes the requirements plan', INDEX)
        self.assertIn('Builder <small>Medium: bounded coding', INDEX)
        self.assertIn('Independent verifier <small>A separate Sol session checks the implementation and evidence</small>', INDEX)
        self.assertIn('Completion owner <small>Sol Medium decides complete or rework', INDEX)
        self.assertIn('Default · GLM-5.3', INDEX)
        self.assertIn('id="astra-reasoning-effort"', INDEX)
        self.assertIn('id="terra-reasoning-effort"', INDEX)
        self.assertIn('id="sol-reasoning-effort"', INDEX)
        self.assertIn('id="completion-reasoning-effort"', INDEX)
        self.assertIn('id="create-error"', INDEX)
        self.assertIn("['glm','astra','terra','sol','completion'].map", APP)
        self.assertIn("models[role+'_reasoning_effort']", APP)
        self.assertIn("action:'set_reasoning'", APP)
        self.assertIn('id="task-reasoning-form"', INDEX)
        self.assertIn("conversationPayload(text,models,conversationRequest.id)", APP)
        self.assertIn('No project needed yet.', INDEX)
        self.assertIn("Unavailable selection: ", APP)
        self.assertIn("loadModels(true)", APP)


if __name__ == '__main__':
    unittest.main()
