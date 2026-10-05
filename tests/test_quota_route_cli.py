"""A role's quota runs out: the CLI asks the person for a model, applies it and records it (#184).

Real CLI processes with the fake Codex provider; its Tester quota is used up only on
gpt-5.6-sol, so a Tester that runs again on that model would stop again. No sleeps,
no live models.
"""
import json
import re
import unittest

from . import test_subprocess
import autocode as runner
import autocode_resolver_human as human
import autocode_support as support
from autocode_taskrun import TaskRun, TaskRunError

ADVERTISED_FLAGS = re.compile(r"(--[a-z][a-z0-9-]*)")
OTHER_MODEL = "gpt-6-luna"


class QuotaRouteCliTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def quota_stop(self):
        self.env.update(AUTOCODE_FIXTURE_MODE='no-human', AUTOCODE_FIXTURE_QUOTA_STAGE='sol',
                        AUTOCODE_FIXTURE_QUOTA_MODEL='gpt-5.6-sol')
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
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
                          'current_model': 'gpt-5.6-sol', 'engine': 'codex'}, need['route'])
        self.assertTrue(need['questions'][0]['question'].startswith("Tester's quota is exhausted"))
        surfaces = [paused['stop_reason'], published['request']['decision_needed'], *published['request']['options']]
        flags = {flag for text in surfaces for flag in ADVERTISED_FLAGS.findall(text)}
        self.assertTrue({'--answer', '--resolver-token', '--resume-paused', '--abandon-stage', '--sol-model'} <= flags)
        known = set(ADVERTISED_FLAGS.findall(self.launch(['--help'], 0).stdout))
        self.assertEqual(set(), flags - known, 'a stop must never advertise a flag the CLI does not have')
        state_file = self.run_dir / 'state.json'
        before = state_file.read_bytes()

        # Rejected answers leave the run paused and the request open.
        for answer, message in (('route-sol=gpt-5.6-terra', 'Cross-model verification violated'),
                                ('route-sol=openai/gpt-6-luna', 'bare Codex model name'),
                                ('route-terra=' + OTHER_MODEL, 'not the quota question')):
            with self.subTest(answer=answer):
                result = self.launch([*self.args, '--answer', answer], 2)
                self.assertIn(message, result.stderr)
                self.assertEqual(before, state_file.read_bytes())
        result = self.launch([*self.args, '--answer', 'route-sol=' + OTHER_MODEL, '--resolver-token', 'stale'], 2)
        self.assertIn('Input rejected', result.stderr)
        self.launch([*self.args, '--delegate-all', '--review-token', 'any'], 2)
        self.launch([*self.args, '--delegate', 'route-sol'], 2)
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
        self.launch([*self.args, '--resume-paused', '--sol-model', OTHER_MODEL, '--no-chat'], 0)
        _, done = self.saved()
        self.assertEqual('TASK_COMPLETE', done['status'])
        [assignment] = self.view()['route_assignments']
        self.assertEqual(('sol', 'gpt-5.6-sol', OTHER_MODEL, 'resume_flag', attempt),
                         tuple(assignment[key] for key in ('role', 'from', 'to', 'via', 'attempt_id')))

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
        paused = self.quota_stop()
        # Capacity at the same uncertain attempt: the cause is not quota, so no model is asked for.
        human.supersede_operational(paused, 'Fixture republishes another cause')
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(
            runner, paused, self.run_dir, support.Paused('PAUSED_PROVIDER_CAPACITY', 'Model at capacity')))
        runner.write_json(self.run_dir / 'state.json', paused)
        _, capacity = self.saved()
        published = human.current(capacity)
        self.assertNotIn('route-sol', [q['id'] for q in published['questions']])
        self.assertNotIn('route', self.view()['needs'])
        result = self.launch([*self.args, '--answer', 'route-sol=' + OTHER_MODEL], 2)
        self.assertIn('Only a quota stop is answered with a model', result.stderr)
        generic = published['questions'][0]['id']
        result = self.launch([*self.args, '--answer', generic + '=retry please'], 2)
        self.assertIn('Use --resolver-response for this operational request', result.stderr)
        self.assertEqual('gpt-5.6-sol', self.saved()[1]['settings']['roles']['sol']['model'])


if __name__ == '__main__':
    unittest.main()
