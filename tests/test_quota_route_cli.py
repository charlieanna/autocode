"""A role's quota runs out: the CLI asks the person for a model, applies it and records it (#184).

Real CLI processes with the fake Codex provider; its Tester quota is used up only on
gpt-5.6-sol, so a Tester that runs again on that model would stop again. A content-filter
finish that exits 0 runs the fake OpenCode instead (#464). No sleeps, no live models.
"""
import argparse
import contextlib
import io
import json
import re
import shutil
import unittest
from pathlib import Path
from unittest import mock

import autocode as runner
import autocode_quota_route as quota_route
import autocode_resolver_human as human
import autocode_run_actions as run_actions
from autocode_taskrun import TaskRun, TaskRunError

from . import test_subprocess

ADVERTISED_FLAGS = re.compile(r"(--[a-z][a-z0-9-]*)")
OTHER_MODEL = "gpt-6-luna"


class QuotaRouteCliTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def quota_stop(self, *flags, model='gpt-5.6-sol', stage='sol'):
        self.env.update(AUTOCODE_FIXTURE_MODE='no-human', AUTOCODE_FIXTURE_QUOTA_STAGE=stage,
                        AUTOCODE_FIXTURE_QUOTA_MODEL=model)
        self.launch(['Build greeting', '--chat', *flags], 2, answers='CLI\nyes\n')
        self.run_dir, state = self.saved()
        self.args = ['--run-dir', str(self.run_dir)]
        return state

    def view(self):
        return json.loads(self.launch([*self.args, '--status'], 0).stdout)['view']

    def model_stages(self, state):
        return [row for row in state['stages'] if not row.get('runner_owned')]

    @staticmethod
    def model_of(record):
        command = record.get('command') or []
        return command[command.index('--model') + 1] if '--model' in command else None

    def test_quota_stop_asks_for_a_model_and_continues_on_the_named_one(self):
        paused = self.quota_stop()
        published = human.current(paused)
        self.assertEqual('operational_exhaustion', published['scope'])
        self.assertEqual(['route-sol'], [question['id'] for question in published['questions']])
        need = self.view()['needs']
        self.assertEqual({'question_id': 'route-sol', 'role': 'sol', 'job': 'Tester',
                          'current_model': 'gpt-5.6-sol', 'engine': 'codex', 'cause': 'quota',
                          'stopped_model': 'gpt-5.6-sol'}, need['route'])
        self.assertTrue(need['questions'][0]['question'].startswith("Tester's quota is exhausted"))
        surfaces = [paused['stop_reason'], published['request']['decision_needed'], *published['request']['options']]
        flags = {flag for text in surfaces for flag in ADVERTISED_FLAGS.findall(text)}
        self.assertTrue({'--answer', '--resolver-token', '--resume-paused', '--abandon-stage', '--sol-model'} <= flags)
        known = set(ADVERTISED_FLAGS.findall(self.launch(['--help'], 0).stdout))
        self.assertEqual(set(), flags - known, 'a stop must never advertise a flag the CLI does not have')
        state_file = self.run_dir / 'state.json'
        before = state_file.read_bytes()

        # Rejected answers leave the run paused and the request open.
        no_default = 'A model question has no default to delegate'
        for args, message in ((['--answer', 'route-sol=gpt-5.6-terra'], 'Cross-model verification violated'),
                              (['--answer', 'route-sol=openai/gpt-6-luna'], 'Tester uses a bare Codex model name'),
                              (['--answer', 'route-sol=gpt-5.6-sol'], 'The Tester already uses gpt-5.6-sol'),
                              (['--answer', 'route-terra=' + OTHER_MODEL], 'not the model question'),
                              (['--answer', 'route-sol=' + OTHER_MODEL, '--resolver-token', 'stale'],
                               'requires the exact current AutoResolver request and token'),
                              (['--delegate', 'route-sol'], no_default),
                              (['--answer', 'route-sol=' + OTHER_MODEL, '--delegate', 'route-sol'], no_default)):
            with self.subTest(args=args):
                result = self.launch([*self.args, *args], 2)
                self.assertIn(message, result.stderr)
                self.assertEqual(before, state_file.read_bytes())
        result = self.launch([*self.args, '--delegate-all', '--review-token', 'any'], 2)
        self.assertIn('AutoResolver retained the operational request', result.stdout)
        _, still = self.saved()
        self.assertEqual('gpt-5.6-sol', still['settings']['roles']['sol']['model'])
        self.assertEqual(['route-sol'], [q['id'] for q in human.current(still)['questions']])

        result = self.launch([*self.args, '--answer', 'route-sol=' + OTHER_MODEL], 0)
        self.assertIn('Saved; no agent launched', result.stdout)
        _, answered = self.saved()
        self.assertEqual(len(self.model_stages(paused)) + 1, len(self.model_stages(answered)),
                         'the stopped attempt is set aside, not replayed')
        self.assertTrue(self.model_stages(answered)[-1]['abandoned'])
        self.assertEqual('PAUSED_STAGE_ABANDONED', answered['status'])
        self.assertEqual('sol', answered['next_stage'])
        self.assertNotIn('sol', answered['sessions'])
        self.assertIsNone(human.current(answered))
        self.assertEqual('consumed', answered['resolver']['human_escalations'][published['request_id']]['status'])
        view = self.view()
        self.assertEqual({'model': OTHER_MODEL, 'engine': 'codex'}, view['routes']['sol'])
        [assignment] = view['route_assignments']
        self.assertEqual(('sol', 'gpt-5.6-sol', OTHER_MODEL, 'sol', 'answer', 'user_cli', 'PAUSED_BUDGET'),
                         tuple(assignment[key] for key in ('role', 'from', 'to', 'stage', 'via', 'actor',
                                                           'pause_status')))
        self.assertTrue(assignment['at'])
        self.assertTrue(assignment['events'].endswith('validator-01.jsonl'))
        self.assertEqual('resume', view['needs']['kind'])

        self.launch([*self.args, '--resume-paused', '--no-chat'], 0)
        _, done = self.saved()
        self.assertEqual('TASK_COMPLETE', done['status'])
        testers = [row for row in self.model_stages(done) if row['stage'] == 'sol']
        self.assertEqual(['gpt-5.6-sol', OTHER_MODEL], [self.model_of(row) for row in testers])

    def test_resume_with_the_role_model_flag_records_the_assignment(self):
        paused = self.quota_stop()
        attempt = runner.attempt_id(paused['active_stage'])
        self.launch([*self.args, '--abandon-stage', attempt], 0)
        # A model the launch refuses never runs, so it is not recorded as the route.
        result = self.launch([*self.args, '--resume-paused', '--sol-model', 'gpt-5.6-terra', '--no-chat'], 2)
        self.assertIn('Cross-model verification violated', result.stdout + result.stderr)
        self.assertEqual([], self.view()['route_assignments'])
        self.launch([*self.args, '--resume-paused', '--sol-model', OTHER_MODEL, '--no-chat'], 0)
        _, done = self.saved()
        self.assertEqual('TASK_COMPLETE', done['status'])
        [assignment] = self.view()['route_assignments']
        self.assertEqual(('sol', 'gpt-5.6-sol', OTHER_MODEL, 'resume_flag', attempt),
                         tuple(assignment[key] for key in ('role', 'from', 'to', 'via', 'attempt_id')))

    def test_the_role_model_flag_alone_is_refused_while_the_attempt_is_uncertain(self):
        paused = self.quota_stop()
        attempt = runner.attempt_id(paused['active_stage'])
        state_file = self.run_dir / 'state.json'
        before = state_file.read_bytes()
        result = self.launch([*self.args, '--resume-paused', '--sol-model', OTHER_MODEL, '--no-chat'], 2)
        self.assertIn('stopped on quota is still uncertain', result.stderr)
        self.assertIn(f'--abandon-stage {attempt}', result.stderr)
        # The pending request asks route-sol, so the answer the CLI takes there is offered too.
        self.assertIn('--answer route-sol=MODEL --resolver-token TOKEN', result.stderr)
        self.assertEqual(before, state_file.read_bytes())
        # Naming the model and setting the attempt aside in one invocation is the advertised order.
        self.launch([*self.args, '--abandon-stage', attempt, '--sol-model', OTHER_MODEL], 0)
        _, saved = self.saved()
        self.assertEqual(OTHER_MODEL, saved['settings']['roles']['sol']['model'])
        [assignment] = self.view()['route_assignments']
        self.assertEqual(('gpt-5.6-sol', OTHER_MODEL, 'resume_flag', attempt),
                         tuple(assignment[key] for key in ('from', 'to', 'via', 'attempt_id')))

    def test_a_token_stranded_by_a_workspace_edit_still_answers(self):
        paused = self.quota_stop()
        shown = paused['resolver_human_request']['request_token']
        (self.project / 'notes.txt').write_text('edited while paused\n')
        result = self.launch([*self.args, '--answer', 'route-sol=' + OTHER_MODEL, '--resolver-token', shown], 0)
        self.assertIn('Saved; no agent launched', result.stdout)
        _, answered = self.saved()
        self.assertEqual(OTHER_MODEL, answered['settings']['roles']['sol']['model'])
        [assignment] = self.view()['route_assignments']
        self.assertNotEqual(paused['resolver_human_request']['request_id'], assignment['request_id'],
                            'the answer applies to the request re-bound to the edited workspace')

    def test_setting_the_attempt_aside_keeps_the_reasoning_effort(self):
        # gpt-6-sol at high is a rung of the Tester's escalation ladder; a quota stop must not climb it.
        paused = self.quota_stop('--sol-model', 'gpt-6-sol', '--sol-reasoning-effort', 'high', model='gpt-6-sol')
        self.assertEqual({'model': 'gpt-6-sol', 'reasoning_effort': 'high'},
                         {key: paused['settings']['roles']['sol'][key] for key in ('model', 'reasoning_effort')})
        self.launch([*self.args, '--answer', 'route-sol=' + OTHER_MODEL], 0)
        _, answered = self.saved()
        self.assertEqual({'model': OTHER_MODEL, 'reasoning_effort': 'high'},
                         {key: answered['settings']['roles']['sol'][key] for key in ('model', 'reasoning_effort')})
        self.assertEqual([], [event for event in answered.get('reasoning_escalations', []) if event['role'] == 'sol'])

    def test_taskrun_assigns_the_model_and_does_not_pass_the_old_one_back(self):
        self.quota_stop()
        run = TaskRun(self.project, self.run_dir, command=tuple(self.entry), options=('--sol-model', 'gpt-5.6-sol'),
                      env=self.env, timeout=60)
        self.assertEqual('sol', run.status()['needs']['route']['role'])
        with self.assertRaisesRegex(TaskRunError, 'Cross-model verification violated'):
            run.assign_model('sol', 'gpt-5.6-terra')
        view = run.assign_model('sol', OTHER_MODEL)
        self.assertEqual(('--sol-model', OTHER_MODEL), run.options)
        self.assertEqual(OTHER_MODEL, view['routes']['sol']['model'])
        view = run.resume_paused()
        self.assertTrue(view['done'], view['needs'])
        self.assertEqual([OTHER_MODEL], [row['to'] for row in view['route_assignments']])

    def test_a_run_nobody_answers_stays_paused_on_the_same_question(self):
        paused = self.quota_stop()
        for extra in (['--no-chat'], ['--resume-paused', '--no-chat']):
            with self.subTest(extra=extra):
                self.launch([*self.args, *extra], 2)
                _, held = self.saved()
                self.assertEqual(self.model_stages(paused), self.model_stages(held))
                self.assertEqual(paused['active_stage'], held['active_stage'])
                self.assertEqual(['route-sol'], [q['id'] for q in human.current(held)['questions']])
                self.assertEqual([], self.view()['route_assignments'])

    def test_other_stops_ask_no_model_and_still_refuse_answers(self):
        # The same Tester stop with a capacity error instead of a used-up quota: no model is asked for.
        self.env['AUTOCODE_FIXTURE_QUOTA_MESSAGE'] = 'Selected model is at capacity'
        self.quota_stop()
        view = self.view()
        self.assertNotIn('route', view['needs'])
        questions = [q['id'] for q in view['needs']['questions']]
        self.assertNotIn('route-sol', questions)
        result = self.launch([*self.args, '--answer', 'route-sol=' + OTHER_MODEL], 2)
        self.assertIn('Only a quota or content-filter stop is answered with a model', result.stderr)
        result = self.launch([*self.args, '--answer', questions[0] + '=retry please'], 2)
        self.assertIn('Use --resolver-response for this operational request', result.stderr)
        self.assertEqual('gpt-5.6-sol', self.saved()[1]['settings']['roles']['sol']['model'])

    def test_a_used_up_plan_sent_as_a_429_asks_for_a_model(self):
        # Z.AI's used-up plan as OpenCode reported it live (2026-10-05): an HTTP 429 that never says
        # "quota". It paused as a rate limit and the Completion Reviewer's model was never asked for.
        self.env['AUTOCODE_FIXTURE_QUOTA_ERROR'] = json.dumps({'name': 'APIError', 'data': {
            'message': 'Weekly/Monthly Limit Exhausted. Your limit will reset at 2026-10-09 10:51:41',
            'statusCode': 429, 'isRetryable': True}})
        paused = self.quota_stop(stage='astra_review')
        self.assertIn('--answer route-completion=MODEL', paused['stop_reason'])
        need = self.view()['needs']
        self.assertEqual(('route-completion', 'Completion Reviewer', 'quota', 'gpt-5.6-sol'),
                         tuple(need['route'][key] for key in ('question_id', 'job', 'cause', 'stopped_model')))
        self.assertEqual(['route-completion'], [question['id'] for question in need['questions']])
        self.launch([*self.args, '--answer', 'route-completion=' + OTHER_MODEL], 0)
        [assignment] = self.view()['route_assignments']
        self.assertEqual(('completion', 'gpt-5.6-sol', OTHER_MODEL, 'PAUSED_BUDGET'),
                         tuple(assignment[key] for key in ('role', 'from', 'to', 'pause_status')))

    def test_a_content_filter_refusal_names_the_model_and_continues_on_another(self):
        # The same Tester stop, refused by the provider's content filter on gpt-5.6-sol only.
        self.env['AUTOCODE_FIXTURE_QUOTA_MESSAGE'] = "The response was blocked by the provider's content filter"
        paused = self.quota_stop()
        self.assertIn("Tester: the provider's content filter refused the response on gpt-5.6-sol", paused['stop_reason'])
        self.assertIn('--answer route-sol=MODEL', paused['stop_reason'])
        need = self.view()['needs']
        self.assertEqual(('route-sol', 'content_filter', 'gpt-5.6-sol'),
                         tuple(need['route'][key] for key in ('question_id', 'cause', 'stopped_model')))
        self.assertTrue(need['questions'][0]['question'].startswith(
            "Tester's model was refused by its provider's content filter"))
        self.launch([*self.args, '--answer', 'route-sol=' + OTHER_MODEL], 0)
        [assignment] = self.view()['route_assignments']
        self.assertEqual(('gpt-5.6-sol', OTHER_MODEL, 'PAUSED_CONTENT_FILTER'),
                         tuple(assignment[key] for key in ('from', 'to', 'pause_status')))
        self.launch([*self.args, '--resume-paused', '--no-chat'], 0)
        _, done = self.saved()
        self.assertEqual('TASK_COMPLETE', done['status'])
        testers = [row for row in self.model_stages(done) if row['stage'] == 'sol']
        self.assertEqual(['gpt-5.6-sol', OTHER_MODEL], [self.model_of(row) for row in testers],
                         'the refused model is never replayed')

    def answer_in_process(self, model, opencode=None):
        """answer_quota_question on the saved state, as the CLI calls it; (exit code, stderr, saved bytes)."""
        state = json.loads((self.run_dir / 'state.json').read_text())

        class Runner:
            def __getattr__(self, name):
                return getattr(runner, name)
        host = Runner()
        host.opencode = opencode or runner.opencode
        args = argparse.Namespace(answer=['route-sol=' + model], delegate=[], delegate_all=False,
                                  resolver_token=state['resolver_human_request']['request_token'])
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = run_actions.answer_quota_question(host, args, state, self.run_dir, self.project)
        return code, stderr.getvalue()

    def test_a_model_opencode_does_not_list_is_refused(self):
        self.quota_stop()
        before = (self.run_dir / 'state.json').read_bytes()

        class Catalogue:
            CONFIGURED = False

            @staticmethod
            def check_models(roles, workspace=None):
                raise RuntimeError('Models unavailable: ' + ', '.join(r['model'] for r in roles.values()))
        with mock.patch.object(quota_route, 'engine', return_value='opencode'):
            code, stderr = self.answer_in_process('openai/gpt-6-luna', opencode=Catalogue)
        self.assertEqual(2, code)
        self.assertIn('Input rejected: Models unavailable: openai/gpt-6-luna', stderr)
        self.assertEqual(before, (self.run_dir / 'state.json').read_bytes())
        self.assertEqual(['route-sol'], [q['id'] for q in human.current(self.saved()[1])['questions']])

    def test_an_attempt_that_is_no_longer_the_stopped_one_is_not_answered(self):
        paused = self.quota_stop()
        before = (self.run_dir / 'state.json').read_bytes()
        stopped = quota_route.stopped_attempt(paused, failure_status=runner.support.failure_status)
        for attempt in (None, {**stopped, 'active': False}, {**stopped, 'role': 'terra'}):
            with self.subTest(attempt=attempt), mock.patch.object(quota_route, 'stopped_attempt',
                                                                  return_value=attempt):
                code, stderr = self.answer_in_process(OTHER_MODEL)
                self.assertEqual(2, code)
                self.assertIn('no longer current', stderr)
                self.assertEqual(before, (self.run_dir / 'state.json').read_bytes())


class ContentFilterFinishCliTests(unittest.TestCase):
    """An OpenCode Builder whose last step finished ``content-filter`` and exited 0, with no error event (#464).

    The fake OpenCode refuses only the Builder on its first model, so a Builder that ran again on that
    model would stop again. Before #464 this was an uncertain stop that asked for no model."""
    new_run_engine_args = ('--engine', 'opencode')
    refused = 'openai/gpt-5.6-terra'
    other = 'openai/gpt-6-luna'

    def setUp(self):
        test_subprocess.SubprocessFlow.setUp(self)
        provider = self.root / 'fixture-bin' / 'opencode'
        shutil.copy2(Path(test_subprocess.__file__).resolve().parents[1] / 'tools' / 'fake_opencode.py', provider)
        provider.chmod(0o755)
        from .opencode_fixture_cli import entrypoint
        self.entry = entrypoint(self.entry)
        self.env.update(AUTOCODE_FIXTURE_MODE='no-human', AUTOCODE_FIXTURE_CONTENT_FILTER_STAGE='terra',
                        AUTOCODE_FIXTURE_CONTENT_FILTER_MODEL=self.refused)

    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    model_of = staticmethod(QuotaRouteCliTests.model_of)

    def test_the_refused_builder_is_asked_for_another_model_and_continues_on_it(self):
        self.launch(['Build a greeting tool', '--chat', '--terra-model', self.refused], 2, answers='CLI\nyes\n')
        run_dir, paused = self.saved()
        args = ['--run-dir', str(run_dir)]
        published = human.current(paused)
        self.assertEqual(('operational_exhaustion', ['route-terra']),
                         (published['scope'], [question['id'] for question in published['questions']]),
                         paused['stop_reason'])
        self.assertEqual(0, paused['active_stage']['exit_code'])
        self.assertIn(f"Builder: the provider's content filter refused the response on {self.refused} "
                      "(content_filter: OpenCode's last step finished with reason content-filter)", paused['stop_reason'])
        self.assertIn('--answer route-terra=MODEL', paused['stop_reason'])
        view = json.loads(self.launch([*args, '--status'], 0).stdout)['view']
        self.assertEqual(('route-terra', 'content_filter', self.refused),
                         tuple(view['needs']['route'][key] for key in ('question_id', 'cause', 'stopped_model')))
        self.launch([*args, '--answer', f'route-terra={self.other}'], 0)
        [assignment] = json.loads(self.launch([*args, '--status'], 0).stdout)['view']['route_assignments']
        self.assertEqual(('terra', self.refused, self.other, 'PAUSED_CONTENT_FILTER'),
                         tuple(assignment[key] for key in ('role', 'from', 'to', 'pause_status')))
        self.launch([*args, '--resume-paused', '--no-chat'], 0)
        _, done = self.saved()
        self.assertEqual('TASK_COMPLETE', done['status'])
        builders = [row for row in done['stages'] if row['stage'] == 'terra' and not row.get('runner_owned')]
        self.assertEqual([self.refused, self.other], [self.model_of(row) for row in builders],
                         'the refused model is never replayed')


if __name__ == '__main__':
    unittest.main()
