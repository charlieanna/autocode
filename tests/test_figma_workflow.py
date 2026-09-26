"""Figma pipeline/handoff checks use local fake providers, never the user's Figma."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import argparse
import contextlib
import copy
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from tools import autocode_ui as ui, autocode_figma as figma, autocode as runner, autocode_support as support
from tests import test_planning, test_subprocess

URL = 'https://www.figma.com/design/Example123/Task?node-id=1-2'


class FigmaWorkflow(unittest.TestCase):
    def test_execution_prompt_scopes_figma_to_current_task(self):
        settings = {'figma_file': URL}
        scoped = figma.instructions(settings, stage='terra', current_task={'objective': 'Repair benchmark tests'})
        self.assertIn('bounded test, parser, or harness repair', scoped)
        self.assertIn('If the repair affects presentation', scoped)
        self.assertNotIn('before planning, implementation or validation', scoped)
        self.assertIn('preserve all required final visual checks', scoped)
        for stage in ('sol', 'astra_review', 'astra_checkpoint'):
            self.assertIn('Scope design work to current_task',
                          figma.instructions(settings, stage=stage, current_task={'id': 'bounded'}))
        self.assertIn('before planning, implementation or validation', figma.instructions(settings))
        self.assertEqual('', figma.instructions({}, stage='terra', current_task={'id': 'bounded'}))

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.calls = []

    def run_ui(self, outcomes=('PASS',), **options):
        run = self.root / ('run-' + str(len(list(self.root.iterdir()))))
        args = ['Design a dashboard', '--workspace', str(self.root), '--run-dir', str(run)]
        plan_outcomes = options.pop('plan_outcomes', ('ACCEPT',))
        if options.pop('build', False):
            ui.workspaces.git(self.root, 'init', '-q')
            ui.workspaces.git(self.root, '-c', 'user.name=Test', '-c', 'user.email=t@example.test',
                              'commit', '--allow-empty', '-qm', 'base')
            args += ['--build', '--no-chat']
        for flag, value in options.items():
            args.append('--' + flag.replace('_', '-'))
            if value is not True:
                args.append(str(value))
        def stage(name, model, text, workspace, directory, schema=None, dry_run=False, reasoning_effort=None):
            self.calls.append((name, text))
            path = directory / (name + ('.json' if schema else '.md'))
            if not schema:
                path.write_text('The complete dashboard brief')
                return path, path.read_text()
            round_number = int(name[-2:]) if name[-2:].isdigit() else 0
            status = ('PASS' if name == 'plan-review' else
                      plan_outcomes[min(round_number, len(plan_outcomes)-1)] if name.startswith('plan-finalization') else
                      'COMPLETE' if name.startswith('builder') else
                      outcomes[min(round_number, len(outcomes)-1)] if name.startswith('validator') else 'ACCEPT')
            report = {'status': status, 'summary': 'Inspected requirements and canvas',
                      'evidence': ['Brief sections and Node 1:2 screenshot audit'],
                      'required_changes': ['Repair missing mobile layout'] if status == 'FAIL' else []}
            if name.startswith(('builder', 'validator', 'decision')):
                report['figma_file'] = URL
            path.write_text(json.dumps(report))
            return path, report
        with patch.object(ui, 'run_stage', side_effect=stage), patch.object(ui, 'subprocess') as process, contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            build = process.run
            build.return_value.returncode = 0
            code = ui.cli(args)
        return code, run, build

    def test_build_receives_only_accepted_artifact_and_chatgpt_models(self):
        code, root, build = self.run_ui(build=True)
        self.assertEqual(0, code)
        self.assertEqual(URL, figma.load_handoff(root)['figma_file'])
        command = build.call_args.args[0]
        self.assertEqual(str(root), command[command.index('--ui-run') + 1])
        self.assertEqual('codex', command[command.index('--engine') + 1])
        self.assertIn('--no-chat', command)
        self.assertEqual('gpt-5.6-terra', command[command.index('--terra-model')+1])
        self.assertEqual('gpt-5.6-sol', command[command.index('--astra-model')+1])
        self.assertEqual('high', command[command.index('--astra-reasoning-effort')+1])
        state = json.loads((root / 'state.json').read_text())
        self.assertEqual(7, len(state['stages']))
        self.assertEqual(URL, state['figma_file'])
        self.assertEqual('gpt-5.6-sol', state['models']['planner'])
        self.assertEqual('gpt-5.6-sol', state['models']['astra'])
        self.assertEqual('high', state['reasoning_efforts']['astra'])
        self.assertEqual(['requirements_planner', 'plan_reviewer', 'requirements_revision', 'plan_finalizer',
                          'builder', 'validator', 'decision_owner'],
                         [stage['stage'] for stage in state['stages']])

    def test_ui_and_code_use_the_same_orchestration_driver(self):
        self.assertIs(runner.orchestrator.drive, ui.orchestrator.drive)

    def test_plan_stage_accepts_report_without_figma_file(self):
        schema = ui.plan_schema(['PASS'])
        report = {'status': 'PASS', 'summary': 'Ready to build',
                  'evidence': ['Reviewed the brief'], 'required_changes': []}

        def fake_provider(command, **_):
            Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps(report))
            return subprocess.CompletedProcess(command, 0)

        with patch.object(ui.figma, 'require_chatgpt'), patch.object(ui.support, 'local_settings', return_value={}), \
             patch.object(ui.subprocess, 'run', side_effect=fake_provider):
            path, result = ui.run_stage('plan-review', 'gpt-5.6-sol', 'Review the brief',
                                        self.root, self.root, schema)
        self.assertEqual(report, result)
        self.assertEqual(report, json.loads(path.read_text()))

    def test_written_brief_survives_final_completion_message(self):
        brief = '# Dashboard brief\n\n' + 'Specific acceptance check.\n' * 30

        def fake_provider(command, **_):
            capture = Path(command[command.index('--output-last-message') + 1])
            (self.root / 'requirements-revision-00.md').write_text(brief)
            capture.write_text('Completed the brief in requirements-revision-00.md')
            return subprocess.CompletedProcess(command, 0)

        with patch.object(ui.figma, 'require_chatgpt'), patch.object(ui.support, 'local_settings', return_value={}), \
             patch.object(ui.subprocess, 'run', side_effect=fake_provider):
            path, result = ui.run_stage('requirements-revision-00', 'gpt-5.6-sol',
                                        'Write the full brief', self.root, self.root)
        self.assertEqual(brief, result)
        self.assertEqual(brief, path.read_text())
        self.assertIn('Completed the brief', (self.root / 'requirements-revision-00.last-message.md').read_text())

    def test_new_run_can_continue_from_accepted_plan(self):
        source = self.root / 'source'
        target = self.root / 'target'
        source.mkdir(); target.mkdir()
        outputs = {}
        for key, name in [('requirements_draft', 'requirements-draft.md'),
                          ('plan_reviewer', 'plan-review.json'),
                          ('brief', 'requirements-revision-00.md'),
                          ('plan_finalizer', 'plan-finalization-00.json')]:
            path = source / name
            path.write_text(key)
            outputs[key] = str(path)
        finalizer = {'status': 'ACCEPT', 'required_changes': [], 'evidence': ['Checked']}
        (source / 'state.json').write_text(json.dumps({
            'figma_file': URL, 'outputs': outputs, 'planning_iteration': 0,
            'reports': {'plan_reviewer': {'status': 'PASS'}, 'plan_finalizer': finalizer}}))
        state = {'figma_file': URL, 'outputs': {}, 'reports': {}, 'next_stage': 'requirements_planner'}
        ui.seed_accepted_plan(state, source, target)
        self.assertEqual('builder', state['next_stage'])
        self.assertEqual(finalizer, state['reports']['plan_finalizer'])
        self.assertTrue((target / 'requirements-revision-00.md').is_file())
        self.assertEqual(target / 'requirements-revision-00.md', Path(state['outputs']['brief']))

    def test_plan_stage_requests_high_reasoning_for_sol(self):
        schema = ui.plan_schema(['PASS'])
        report = {'status': 'PASS', 'summary': 'Ready to build',
                  'evidence': ['Reviewed the brief'], 'required_changes': []}

        def fake_provider(command, **_):
            self.assertEqual('gpt-5.6-sol', command[command.index('--model') + 1])
            self.assertIn('model_reasoning_effort="high"', command)
            Path(command[command.index('--output-last-message') + 1]).write_text(json.dumps(report))
            return subprocess.CompletedProcess(command, 0)

        with patch.object(ui.figma, 'require_chatgpt'), patch.object(ui.support, 'local_settings', return_value={}), \
             patch.object(ui.subprocess, 'run', side_effect=fake_provider):
            ui.run_stage('plan-review', ui.DEFAULT_MODELS['astra'], 'Review the brief',
                         self.root, self.root, schema, reasoning_effort='high')

    def test_astra_accept_cannot_override_failed_sol(self):
        code, root, build = self.run_ui(('FAIL',), max_reworks=0, build=True)
        self.assertEqual(3, code)
        self.assertFalse((root / 'handoff.json').exists())
        build.assert_not_called()

    def test_rework_receives_previous_findings_and_decision(self):
        code, root, _ = self.run_ui(('FAIL', 'PASS'))
        self.assertEqual(0, code)
        rework = next(text for name, text in self.calls if name == 'builder-01')
        self.assertIn('validator-00.json', rework)
        self.assertIn('decision-00.json', rework)
        self.assertIn(URL, rework)
        self.assertEqual('validator-01.json', figma.load_handoff(root)['artifacts']['validator']['path'])

    def test_plan_rework_returns_to_requirements_planner_before_figma_build(self):
        code, root, _ = self.run_ui(plan_outcomes=('REWORK', 'ACCEPT'))
        self.assertEqual(0, code)
        state = json.loads((root / 'state.json').read_text())
        self.assertEqual(1, state['planning_iteration'])
        revision = next(text for name, text in self.calls if name == 'requirements-revision-01')
        self.assertIn('plan-finalization-00.json', revision)
        stages = [stage['stage'] for stage in state['stages']]
        self.assertEqual(2, stages.count('requirements_revision'))
        self.assertLess(stages.index('plan_finalizer'), stages.index('builder'))

    def test_unlimited_reworks_reach_independent_acceptance_beyond_default_limits(self):
        code, root, _ = self.run_ui(('FAIL', 'FAIL', 'FAIL', 'PASS'),
                                    plan_outcomes=('REWORK', 'REWORK', 'REWORK', 'ACCEPT'),
                                    max_reworks='none', max_plan_reworks='none')
        self.assertEqual(0, code)
        state = json.loads((root / 'state.json').read_text())
        self.assertEqual({'max_reworks': None, 'max_plan_reworks': None}, state['limits'])
        self.assertEqual((3, 3), (state['iteration'], state['planning_iteration']))
        handoff = figma.load_handoff(root)
        self.assertEqual('validator-03.json', handoff['artifacts']['validator']['path'])
        self.assertEqual('plan-finalization-03.json', handoff['artifacts']['plan_finalizer']['path'])

    def test_numeric_rework_limits_still_stop_without_an_accepted_handoff(self):
        for limit in (0, 1, 3):
            with self.subTest(limit=limit):
                code, root, _ = self.run_ui(('FAIL',), max_reworks=limit)
                state = json.loads((root / 'state.json').read_text())
                self.assertEqual((3, 'REWORK_REQUIRED', limit), (code, state['status'], state['iteration']))
                self.assertFalse((root / 'handoff.json').exists())
                code, root, _ = self.run_ui(plan_outcomes=('REWORK',), max_plan_reworks=limit)
                state = json.loads((root / 'state.json').read_text())
                self.assertEqual((3, 'PLAN_REWORK_REQUIRED', limit),
                                 (code, state['status'], state['planning_iteration']))
                self.assertNotIn('builder', state['outputs'])
                self.assertFalse((root / 'handoff.json').exists())

    def test_invalid_rework_limits_are_rejected_before_creating_a_run(self):
        for flag in ('--max-reworks', '--max-plan-reworks'):
            for value in ('-1', '1.5', 'unlimited'):
                with self.subTest(flag=flag, value=value), contextlib.redirect_stderr(io.StringIO()):
                    with self.assertRaises(SystemExit) as error:
                        ui.cli(['Design a dashboard', '--workspace', str(self.root), flag, value])
                    self.assertEqual(2, error.exception.code)
                    self.assertEqual([], list(self.root.iterdir()))

    def test_modified_report_and_incomplete_run_cannot_be_imported(self):
        _, root, _ = self.run_ui()
        (root / 'validator-00.json').write_text('{}')
        with self.assertRaisesRegex(ValueError, 'changed after acceptance'):
            figma.load_handoff(root)
        state = json.loads((root / 'state.json').read_text()); state['status'] = 'DRY_RUN'
        (root / 'state.json').write_text(json.dumps(state))
        with self.assertRaisesRegex(ValueError, 'completed, accepted'):
            figma.load_handoff(root)

    def test_handoff_requires_accepted_plan_and_keeps_version_one_compatibility(self):
        _, root, _ = self.run_ui()
        handoff = json.loads((root / 'handoff.json').read_text())
        final = root / handoff['artifacts']['plan_finalizer']['path']
        report = json.loads(final.read_text()); report['status'] = 'REWORK'; report['required_changes'] = ['Clarify mobile']
        final.write_text(json.dumps(report))
        handoff['artifacts']['plan_finalizer']['sha256'] = support.file_hash(final)
        (root / 'handoff.json').write_text(json.dumps(handoff))
        with self.assertRaisesRegex(ValueError, 'accepted requirements plan'):
            figma.load_handoff(root)

        _, legacy_root, _ = self.run_ui()
        current = json.loads((legacy_root / 'handoff.json').read_text())
        current['version'] = 1
        current['artifacts'] = {old: current['artifacts'][new] for old, new in
                                {'brief': 'brief', 'terra': 'builder', 'sol': 'validator',
                                 'astra': 'decision_owner'}.items()}
        (legacy_root / 'handoff.json').write_text(json.dumps(current))
        self.assertEqual(URL, figma.load_handoff(legacy_root)['figma_file'])

    def test_dry_run_uses_real_cli_without_starting_providers_or_emitting_handoff(self):
        root = self.root / 'dry'
        result = subprocess.run([sys.executable, str(Path(runner.__file__)), 'ui', 'Design a tracker',
                                 '--workspace', str(self.root), '--run-dir', str(root), '--dry-run', '--build'],
                                capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)  # Build needs a Git project even in preview.
        result = subprocess.run([sys.executable, str(Path(runner.__file__)), 'ui', 'Design a tracker',
                                 '--workspace', str(self.root), '--run-dir', str(root), '--dry-run'],
                                capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('DRY_RUN', json.loads((root / 'state.json').read_text())['status'])
        self.assertFalse((root / 'handoff.json').exists())
        self.assertEqual(7, len(list(root.glob('*.prompt.md'))))

    def test_figma_policy_uses_independent_review_without_forging_human_approval(self):
        text = figma.instructions({'figma_file': URL})
        self.assertIn('human_review=false', text)
        self.assertIn('Do not invent a human approval event', text)
        self.assertIn('every required screen', text)
        self.assertIn('human visual review criterion', figma.instructions({'figma_file': URL, 'figma_review': 'human'}))

    def test_urls_are_restricted_to_editable_figma_files(self):
        self.assertEqual(URL, figma.design_url(URL))
        for value in ['https://figma.com.evil.test/design/abc', 'http://www.figma.com/design/abc',
                      'https://www.figma.com/make/abc', 'https://user@www.figma.com/design/abc']:
            with self.assertRaises(ValueError):
                figma.design_url(value)

    def test_ui_alias_and_figma_cli_dry_run_select_codex(self):
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        result = subprocess.run([sys.executable, str(Path(runner.__file__)), 'Build tracker', '--workspace',
                                 str(self.root), '--figma-file', URL, '--dry-run'], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual('codex', json.loads(result.stdout)['engine'])
        self.assertFalse((self.root / '.autocode').exists())

    def test_figma_implementation_pins_openai_and_rejects_other_auth(self):
        args = test_planning.PlanningTests().configure_args(engine='codex', figma_file=URL)
        state = {'workspace': str(self.root), 'iteration': 0}
        with patch.object(support, 'local_settings', return_value={'auth_mode': 'ChatGPT', 'model_provider': 'ZAI'}):
            settings = runner.configure(args, state)
        self.assertEqual({'openai'}, {role['provider'] for role in settings['roles'].values()})
        self.assertEqual('automatic', settings['figma_review'])
        for invalid in ({'auth_mode': 'unknown'}, {'auth_mode': 'ChatGPT', 'environment_auth_present': True},
                        {'auth_mode': 'ChatGPT', 'environment_base_url_present': True}):
            with patch.object(support, 'local_settings', return_value=invalid), self.assertRaisesRegex(ValueError, 'ChatGPT login'):
                runner.configure(args, state)

    def test_unborn_build_project_does_not_start_design(self):
        ui.workspaces.git(self.root, 'init', '-q')
        with patch.object(ui, 'run_stage') as stage, contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            ui.cli(['Design an app', '--workspace', str(self.root), '--build'])
        stage.assert_not_called()
        self.assertFalse((self.root / '.autocode-ui').exists())

    def test_accepted_handoff_completes_in_an_isolated_cli_task_with_fake_provider(self):
        _, design, _ = self.run_ui()
        flow = test_subprocess.SubprocessFlow(); flow.setUp()
        self.addCleanup(flow.doCleanups)
        env = {**flow.env, 'AUTOCODE_FIXTURE_MODE': 'no-human', 'CODEX_HOME': str(flow.root / 'codex-config')}
        for key in ('OPENAI_API_KEY', 'CODEX_API_KEY', 'OPENAI_BASE_URL'):
            env.pop(key, None)
        result = subprocess.run([*flow.entry, '--workspace', str(flow.project), '--ui-run', str(design), '--chat'],
                                input='CLI\nyes\n', capture_output=True, text=True, env=env, cwd=flow.root, timeout=45)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        path = next(flow.project.glob('.autocode/worktrees/*/.autocode/runs/*/state.json'))
        state = json.loads(path.read_text())
        self.assertEqual('TASK_COMPLETE', state['status'])
        self.assertEqual(URL, state['settings']['figma_file'])
        self.assertEqual(str(design), state['ui_run'])
        self.assertIn('The complete dashboard brief', state['task'])
        self.assertEqual('codex', state['settings']['engine'])
        self.assertEqual({'openai'}, {role['provider'] for role in state['settings']['roles'].values()})
        self.assertTrue((Path(state['workspace']) / 'greet.py').exists())
        self.assertFalse((flow.project / 'greet.py').exists())
        for stage in state['stages']:
            self.assertIn(URL, Path(stage['prompt']).read_text())


if __name__ == '__main__':
    unittest.main()
