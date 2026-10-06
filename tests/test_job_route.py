"""A workflow job its provider refused or ran out of quota on takes another model (#463), through the CLI.

The job harness of test_job_failure_recovery (copied Git seed, wrapper, TaskRun), with a fake
provider that refuses one model: a ContentFilterError error event, or a quota message, and exit 1.
Every other model writes a valid review. The provider's call log (one line per request, with its
model) and the saved state file are the oracles. No test sleeps or reaches a real provider.
"""
import argparse
import contextlib
import copy
import io
import json
import re
import subprocess
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode as runner
import autocode_interventions as interventions
import autocode_job_failure as job_failure
import autocode_job_route as job_route
import autocode_quota_route as quota_route
import autocode_run_view as run_view
from autocode_taskrun import TaskRun, TaskRunError

from .test_job_failure_recovery import BROKEN, JobHarness, ORIGINAL, PROVIDER

REFUSING = r'''#!/usr/bin/env python3
import json,os,sys,subprocess,uuid
from pathlib import Path
if sys.argv[1:3]==['sandbox','--help']:
 print('--config -- macos linux');raise SystemExit(0)
if sys.argv[1:2]==['sandbox']:
 raise SystemExit(subprocess.call(sys.argv[sys.argv.index('--')+1:]))
if sys.argv[1:]==['login','status']:
 print('Logged in (offline)');raise SystemExit(0)
if sys.argv[1:]==['--version']:
 print('codex-cli fixture');raise SystemExit(0)
sys.stdin.read()
argv=sys.argv
model=next((argv[argv.index(f)+1] for f in ('--model','-m') if f in argv[:-1]),None)
with Path(os.environ['JOB_CALLS']).open('a') as f:f.write('request %s\n'%model)
print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid4())}),flush=True)
if os.environ.get('REFUSE_MODEL') in ('*',model):
 error=({'name':'ContentFilterError','message':'The response was blocked by the content filter'}
        if os.environ.get('REFUSE_KIND','filter')=='filter' else {'message':'subscription usage limit reached'})
 print(json.dumps({'type':'error','error':error}),flush=True);raise SystemExit(1)
result={'verdict':'approve','summary':'Equivalent integer addition','change_under_review':'pr-double.patch','findings':[],'change_patch':'pr-double.patch','tests_run':[],'delivered_tests':[]}
Path(argv[argv.index('-o')+1]).write_text(json.dumps(result))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':5}}),flush=True)
'''
MODEL_FLAG = re.compile(r'--[a-z-]+-model\b')


class JobModelRouteTests(JobHarness):
    def setUp(self):
        super().setUp()
        (self.workspace/'.autocode'/'bin'/'codex').write_text(REFUSING)

    def refused(self, kind='filter', workflow='review', model='gpt-6-sol', mode='exit42'):
        self.env.update(REFUSE_KIND=kind, REFUSE_MODEL=model)
        return self.start(mode, workflow)

    def models(self):
        return [line.split(' ', 1)[1] for line in self.calls.read_text().splitlines()] if self.calls.exists() else []

    def saved(self, run):
        return (run.run_dir/'state.json').read_bytes()

    def cli(self, run, *args):
        return subprocess.run([*run.command, *args, '--workspace', str(self.workspace), '--run-dir', str(run.run_dir)],
                              env={**os.environ, **run.env}, capture_output=True, text=True, timeout=60)

    def rejected(self, run, *args, message):
        before = self.saved(run)
        proc = self.cli(run, *args)
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn('Input rejected', proc.stderr)
        self.assertRegex(proc.stderr, message)
        self.assertEqual(before, self.saved(run), 'a rejected answer changes nothing')

    def stopped(self, run, stage='review_change'):
        view = run.status()
        self.assertEqual(('PAUSED_JOB_FAILURE', 'retry_job', stage),
                         (view['status'], view['needs']['kind'], view['next_stage']), view)
        self.assertFalse(view['needs'].get('questions'))
        return view['needs']

    def test_a_refused_review_names_another_model_and_retries_once_on_it(self):
        run = self.refused()
        need = self.stopped(run)
        route = need['route']
        self.assertEqual({'question_id': 'route-sol', 'role': 'sol', 'job': 'Code Reviewer', 'current_model': 'gpt-6-sol',
                          'engine': 'codex', 'cause': 'content_filter', 'stopped_model': 'gpt-6-sol'},
                         {key: value for key, value in route.items() if key != 'candidates'})
        self.assertIn('candidates', route)
        self.assertIn("Code Reviewer: the provider's content filter refused the response on gpt-6-sol", need['reason'])
        self.assertIn('the same model is likely to refuse it again', need['reason'])
        self.assertIn('--answer route-sol=MODEL --job-retry-token TOKEN', need['reason'])
        self.assertIn('--resume-paused --retry-failed-stage --job-retry-token NEW_TOKEN', need['reason'])
        self.assertNotIn('--abandon-stage', need['reason'])
        self.assertNotRegex(need['reason'], MODEL_FLAG)
        self.assertNotIn(need['job_retry_token'], need['reason'], 'tokens stay placeholders in prose')
        # The exact retry would replay the refused model: the next step the need and the card name is another
        # model, and no card action retries until one is named.
        self.assertEqual('--answer route-sol=MODEL --job-retry-token TOKEN', need['action'])
        view = run.status()
        recovery = view['recovery']
        self.assertIn('content filter refused the Code Reviewer', recovery['what_happened'])
        self.assertIn('name another model for this job', recovery['what_happened'])
        self.assertEqual(['inspect', 'feedback'], [action['kind'] for action in recovery['actions']])
        self.assertEqual('name another model for the Code Reviewer, then retry it', view['progress']['needs_you'])
        old = need['job_retry_token']
        self.rejected(run, '--answer', 'route-sol=gpt-6-luna', message='--job-retry-token TOKEN')
        self.rejected(run, '--answer', 'route-sol=gpt-6-luna', '--job-retry-token', 'jr:wrong', message='token')
        self.rejected(run, '--answer', 'route-sol=gpt-6-sol', '--job-retry-token', old, message='refused gpt-6-sol')
        self.rejected(run, '--answer', 'route-terra=gpt-6-luna', '--job-retry-token', old, message='not the model question')
        self.rejected(run, '--answer', 'route-sol=gpt 6', '--job-retry-token', old, message='without spaces')

        view = run.assign_model('sol', 'gpt-6-luna')
        need = self.stopped(run)
        self.assertNotEqual(old, need['job_retry_token'])
        self.assertEqual('gpt-6-luna', need['route']['current_model'])
        self.assertIn('The Code Reviewer now runs on gpt-6-luna (was gpt-6-sol)', need['reason'])
        [assignment] = view['route_assignments']
        self.assertEqual(('sol', 'gpt-6-sol', 'gpt-6-luna', 'answer', 'PAUSED_CONTENT_FILTER', 'review_change',
                          need['attempt_id'], 'Code Reviewer'),
                         tuple(assignment[key] for key in ('role', 'from', 'to', 'via', 'pause_status', 'stage',
                                                           'attempt_id', 'job')))
        self.assertEqual('gpt-6-luna', run.options[run.options.index('--sol-model') + 1])
        # Once a model is named, the card and the progress line ask only for the retry on it.
        view = run.status()
        self.assertIn('The Code Reviewer now runs on gpt-6-luna; retry it once with the new token.',
                      view['recovery']['what_happened'])
        self.assertNotIn('name another model', view['recovery']['what_happened'])
        self.assertEqual('retry the Code Reviewer on gpt-6-luna', view['progress']['needs_you'])
        self.assertEqual('--resume-paused --retry-failed-stage --job-retry-token TOKEN', view['needs']['action'])
        self.assertEqual([('inspect', None), ('retry_job', need['job_retry_token']), ('feedback', None)],
                         [(action['kind'], action.get('job_retry_token')) for action in view['recovery']['actions']])
        # A second answer never routes back to the refused model, with the new token either.
        self.rejected(run, '--answer', 'route-sol=gpt-6-sol', '--job-retry-token', need['job_retry_token'],
                      message='refused gpt-6-sol')
        with self.assertRaisesRegex(TaskRunError, 'token'):
            run.retry_job(old)
        self.assertEqual(['gpt-6-sol'], self.models())

        view = run.retry_job(need['job_retry_token'])
        self.assertTrue(view['done'], view)
        self.assertEqual(['gpt-6-sol', 'gpt-6-luna'], self.models())
        self.assertEqual(ORIGINAL, (self.workspace/'calc.py').read_text())

    def test_a_model_flag_at_a_stopped_job_is_refused_with_the_job_advice(self):
        run = self.refused()
        need = self.stopped(run)
        before = self.saved(run)
        changed = TaskRun(self.workspace, run.run_dir, command=run.command, options=('--sol-model', 'gpt-6-luna'),
                          env=run.env, timeout=60)
        for attempt in (lambda: changed.retry_job(need['job_retry_token']), changed.resume_paused):
            with self.assertRaises(TaskRunError) as caught:
                attempt()
            message = str(caught.exception)
            self.assertIn('set aside for one exact retry bound to its configuration; --sol-model is not saved', message)
            self.assertIn('--answer route-sol=MODEL --job-retry-token TOKEN', message)
            self.assertNotIn('--abandon-stage', message)
            self.assertEqual(before, self.saved(run))
        self.assertEqual(['gpt-6-sol'], self.models())
        # The shown token still retries exactly (on the refused model: the person's choice).
        self.assertEqual(need['job_retry_token'], self.stopped(run)['job_retry_token'])

    def test_a_quota_stop_names_the_quota_and_takes_a_model_or_an_unchanged_retry(self):
        run = self.refused('quota')
        need = self.stopped(run)
        self.assertEqual(('quota', 'gpt-6-sol'), (need['route']['cause'], need['route']['stopped_model']))
        self.assertIn('Code Reviewer: the provider reported its quota, usage limit or credits used up on gpt-6-sol '
                      '(subscription usage limit reached)', need['reason'])
        self.assertIn('once the quota resets, retry it unchanged with --resume-paused --retry-failed-stage '
                      '--job-retry-token TOKEN', need['reason'])
        # A retry once the quota resets is a way on too: the need and the card keep it beside the answer.
        self.assertEqual('--resume-paused --retry-failed-stage --job-retry-token TOKEN', need['action'])
        recovery = run.status()['recovery']
        self.assertIn('quota', recovery['what_happened'])
        self.assertEqual(['inspect', 'retry_job', 'feedback'], [action['kind'] for action in recovery['actions']])
        view = run.assign_model('sol', 'gpt-6-luna')
        self.assertEqual('PAUSED_BUDGET', view['route_assignments'][0]['pause_status'])
        view = run.retry_job(self.stopped(run)['job_retry_token'])
        self.assertTrue(view['done'], view)
        self.assertEqual(['gpt-6-sol', 'gpt-6-luna'], self.models())

    def test_after_a_quota_reset_the_shown_token_retries_unchanged(self):
        run = self.refused('quota')
        need = self.stopped(run)
        run.env['REFUSE_MODEL'] = ''
        view = run.retry_job(need['job_retry_token'])
        self.assertTrue(view['done'], view)
        self.assertEqual(['gpt-6-sol', 'gpt-6-sol'], self.models())

    def test_a_job_route_without_a_flag_is_named_only_by_its_answer(self):
        for workflow, stage, role, job in (('bugfix', 'investigate_bug', 'investigator', 'Investigator'),
                                           ('design', 'review_design', 'architect', 'Designer')):
            with self.subTest(workflow=workflow):
                self.setUp()  # a fresh workspace and call log for each workflow's run
                # --investigator-model pins the stuck-stage Investigator, never the bug Investigator's route.
                self.options = (*self.options, '--investigator-model', 'gpt-6-stuck')
                run = self.refused(workflow=workflow, model='*')
                need = self.stopped(run, stage)
                self.assertEqual((role, job), (need['route']['role'], need['route']['job']))
                self.assertIn(f'--answer route-{role}=MODEL --job-retry-token TOKEN', need['reason'])
                self.assertNotRegex(need['reason'], MODEL_FLAG)
                self.assertNotIn('--abandon-stage', need['reason'])
                stopped_on = need['route']['stopped_model']
                view = run.assign_model(role, 'gpt-6-nova')
                [assignment] = view['route_assignments']
                self.assertEqual((role, stopped_on, 'gpt-6-nova', stage),
                                 (assignment['role'], assignment['from'], assignment['to'], assignment['stage']))
                self.assertEqual('gpt-6-stuck', run.options[run.options.index('--investigator-model') + 1])
                run.retry_job(self.stopped(run, stage)['job_retry_token'])
                self.assertEqual([stopped_on, 'gpt-6-nova'], self.models())

    def test_a_refused_job_set_aside_after_a_crash_takes_a_model_too(self):
        run = self.refused(mode='abandon')
        attempt = json.loads(self.cli(run, '--status').stdout)['attempt_id']
        # Setting the job aside binds its exact retry to the model it ran on; a model flag in the same
        # invocation would be saved beside it and leave that retry (and the answer) stale: refused.
        before = self.saved(run)
        proc = self.cli(run, '--abandon-stage', attempt, '--resume-paused', '--sol-model', 'gpt-6-luna')
        self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
        self.assertIn('is a workflow job', proc.stderr)
        self.assertIn(f'Set it aside first with --abandon-stage {attempt} alone', proc.stderr)
        self.assertIn('--answer route-sol=MODEL --job-retry-token TOKEN', proc.stderr)
        self.assertEqual(before, self.saved(run), 'nothing is saved')
        proc = self.cli(run, '--abandon-stage', attempt)
        self.assertEqual(0, proc.returncode, proc.stderr)
        view = run.status()
        need = view['needs']
        self.assertEqual(('PAUSED_STAGE_ABANDONED', 'retry_job', 'route-sol'),
                         (view['status'], need['kind'], need['route']['question_id']))
        # Set aside by a person, the stop is the one a direct refusal makes: the runner's launch rules list the
        # configured models that would pass, and the reason keeps the refusal, the model and the provider's words.
        self.assertTrue(need['route']['candidates'], 'the run configures another model that passes the launch rules')
        self.assertNotIn('gpt-6-sol', need['route']['candidates'])
        self.assertIn("Code Reviewer: the provider's content filter refused the response on gpt-6-sol "
                      "(ContentFilterError: The response was blocked by the content filter)", need['reason'])
        self.assertIn(f'Operator abandoned Code Reviewer attempt {attempt}', need['reason'])
        self.assertIn('--answer route-sol=MODEL --job-retry-token TOKEN', need['reason'])
        self.assertIn(need['route']['candidates'][0], need['reason'])
        self.assertEqual(need['reason'], view['stop_reason'])
        self.assertEqual('--answer route-sol=MODEL --job-retry-token TOKEN', need['action'])
        self.assertEqual(['inspect', 'feedback'], [action['kind'] for action in view['recovery']['actions']])
        run.assign_model('sol', 'gpt-6-luna')
        view = run.retry_job(run.status()['needs']['job_retry_token'])
        self.assertTrue(view['done'], view)
        self.assertEqual(['gpt-6-sol', 'gpt-6-luna'], self.models())

    def test_a_model_answer_keeps_the_exact_retry_guarantees(self):
        run = self.refused()
        token = self.stopped(run)['job_retry_token']
        # The answer changes only the job's model: any other setting with it is refused before it is saved,
        # in every form a model answer takes (without its token too, and next to the retry it cannot make).
        answer, only = ('--answer', 'route-sol=gpt-6-luna'), "A stopped job's model answer changes only that job's model"
        for flags, message in (((*answer, '--job-retry-token', token, '--max-stage-seconds', '60'), only),
                               ((*answer, '--job-retry-token', token, '--terra-model', 'gpt-6-nova'), only),
                               ((*answer, '--max-stage-seconds', '60'), only),
                               ((*answer, '--resume-paused', '--retry-failed-stage', '--max-stage-seconds', '60'), only),
                               ((*answer, '--job-retry-token', token, '--resume-paused', '--retry-failed-stage',
                                 '--max-stage-seconds', '60'), "names a stopped job's model on its own")):
            with self.subTest(flags=flags):
                before = self.saved(run)
                proc = self.cli(run, *flags)
                self.assertEqual(2, proc.returncode, proc.stdout + proc.stderr)
                self.assertIn(message, proc.stderr)
                self.assertEqual(before, self.saved(run), 'nothing is saved')
        # A source edit after the stop makes the answer stale, as it does the retry.
        (self.workspace/'calc.py').write_text(BROKEN)
        self.rejected(run, '--answer', 'route-sol=gpt-6-luna', '--job-retry-token', token, message='Source changed')
        # A model the cross-model rule refuses (the Builder's own model for the Code Reviewer).
        (self.workspace/'calc.py').write_text(ORIGINAL)
        builder = json.loads(self.saved(run))['settings']['roles']['terra']['model']
        self.rejected(run, '--answer', f'route-sol={builder}', '--job-retry-token', token,
                      message='Cross-model verification violated')
        # Restored exactly, the answer applies and prints the command that retries once on the named model.
        need = self.stopped(run)
        self.assertEqual(token, need['job_retry_token'])
        proc = self.cli(run, '--answer', 'route-sol=gpt-6-luna', '--job-retry-token', token)
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        new = self.stopped(run)['job_retry_token']
        self.assertIn('PAUSED_JOB_FAILURE: The Code Reviewer now runs on gpt-6-luna (was gpt-6-sol). Retry it with '
                      f'--resume-paused --retry-failed-stage --job-retry-token {new}. Saved; no agent launched.',
                      proc.stdout)
        self.assertNotIn(token, proc.stdout, 'the shown token no longer retries')
        self.assertEqual(['gpt-6-sol'], self.models(), 'no agent launched')
        printed = re.search(r'Retry it with (--resume-paused --retry-failed-stage --job-retry-token \S+)\.', proc.stdout)
        proc = self.cli(run, *printed.group(1).split(), '--no-chat')
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        self.assertTrue(run.status()['done'])
        self.assertEqual(['gpt-6-sol', 'gpt-6-luna'], self.models())

    def test_a_delegated_or_queued_model_answer_is_refused(self):
        run = self.refused()
        token = self.stopped(run)['job_retry_token']
        self.rejected(run, '--answer', 'route-sol=gpt-6-luna', '--delegate', 'route-sol', '--job-retry-token', token,
                      message='A model question has no default to delegate')
        interventions.submit(self.workspace, run.run_dir, request_id='pause-1', kind='pause', text='')
        self.rejected(run, '--answer', 'route-sol=gpt-6-luna', '--job-retry-token', token,
                      message='Apply the queued intervention before answering')
        self.assertEqual(['gpt-6-sol'], self.models())

    def test_after_an_answer_the_route_lists_only_models_it_can_still_take(self):
        run = self.refused()
        listed = self.stopped(run)['route']['candidates']
        self.assertTrue(listed, 'the run configures another model that passes the launch rules')
        run.assign_model('sol', listed[0])
        route = self.stopped(run)['route']
        self.assertEqual(listed[0], route['current_model'])
        # The answer refuses the model already set ('already uses'), so the need no longer offers it.
        self.assertFalse({listed[0], 'gpt-6-sol'} & set(route['candidates']), route)

    def test_a_model_opencode_does_not_list_is_refused_and_changes_nothing(self):
        run = self.refused()
        state = json.loads(self.saved(run))
        before = self.saved(run)

        class Catalogue:
            CONFIGURED = False

            @staticmethod
            def check_models(roles, workspace=None):
                raise RuntimeError('Models unavailable: ' + ', '.join(r['model'] for r in roles.values()))

        class Runner:
            def __getattr__(self, name):
                return getattr(runner, name)
        host = Runner()
        host.opencode = Catalogue
        args = argparse.Namespace(answer=['route-sol=openai/gpt-6-luna'], delegate=[], delegate_all=False,
                                  job_retry_token=state['job_failure']['job_retry_token'], resume_paused=False)
        stderr = io.StringIO()
        with mock.patch.object(quota_route, 'engine', return_value='opencode'), \
                contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            code = job_route.answer(host, args, state, run.run_dir, self.workspace)
        self.assertEqual(2, code)
        self.assertIn('Input rejected: Models unavailable: openai/gpt-6-luna', stderr.getvalue())
        self.assertEqual(before, self.saved(run))

    def test_a_job_stopped_for_another_reason_takes_no_model(self):
        (self.workspace/'.autocode'/'bin'/'codex').write_text(PROVIDER)  # an interrupted attempt, exit 42
        run = self.start('exit42')
        need = self.stopped(run)
        self.assertNotIn('route', need)
        self.rejected(run, '--answer', 'route-sol=gpt-6-luna', '--job-retry-token', need['job_retry_token'],
                      message='did not stop on quota or a content-filter refusal')


ATTEMPT = '001/review-change-01'


def bound_state(settings_sol='gpt-6-sol'):
    """A job stopped by the content filter on gpt-6-sol, as job_failure.recover saves it; settings may have drifted."""
    config = {'engine': 'codex', 'roles': {'sol': {'model': 'gpt-6-sol'}, 'terra': {'model': 'gpt-5.6-terra'}},
              'limits': {'max_stage_seconds': 900}}
    settings = copy.deepcopy(config)
    settings['roles']['sol']['model'] = settings_sol
    route = {'id': 'route-sol', 'route_role': 'sol', 'job': 'Code Reviewer', 'current_model': settings_sol,
             'stopped_model': 'gpt-6-sol', 'cause': 'content_filter'}
    return {'status': 'PAUSED_JOB_FAILURE', 'settings': settings, 'job_retry_authorization': {'token': 'jr:old'},
            'job_failure': {'stage': 'review_change', 'attempt_id': ATTEMPT, 'job_retry_token': 'jr:old',
                            'source_identity': 'source', 'unrestored': [], 'pause_status': 'PAUSED_CONTENT_FILTER',
                            'configuration': job_failure.configuration({'settings': config}), 'route': route}}


class RerouteTests(unittest.TestCase):
    """job_failure.reroute binds the exact retry to the named model and nothing else (pure; source check faked)."""

    def reroute(self, state, to='gpt-6-luna', *, matches=True, runtime=None, **changes):
        assignment = {'role': 'sol', 'attempt_id': ATTEMPT, 'from': state['settings']['roles']['sol']['model'],
                      'to': to, **changes}
        state['settings']['roles']['sol']['model'] = to  # as quota_route.assign applies it
        with mock.patch.object(job_failure.source, 'matches_original', return_value=matches):
            return job_failure.reroute(runtime, state, '/run', '/workspace', assignment)

    def test_the_route_never_lists_the_model_the_job_now_runs_on(self):
        # With the runtime's launch rules the question is asked again against the new configuration.
        runtime = argparse.Namespace(dispatch=argparse.Namespace(enforce_cross_model_verification=lambda state: None))
        state = bound_state()
        state['job_failure']['route'].update(candidates=['gpt-6-luna', 'gpt-5.6-terra'],
                                             recommendation='Configured models that pass: gpt-6-luna, gpt-5.6-terra.')
        self.reroute(state, runtime=runtime)
        route = state['job_failure']['route']
        self.assertEqual(('route-sol', 'sol', 'Code Reviewer', 'gpt-6-luna', 'gpt-6-sol', 'content_filter'),
                         tuple(route[key] for key in ('id', 'route_role', 'job', 'current_model', 'stopped_model',
                                                      'cause')))
        self.assertEqual(['gpt-5.6-terra'], route['candidates'])
        self.assertEqual('Configured models that pass the launch rules for the Code Reviewer: gpt-5.6-terra.',
                         route['recommendation'])
        # Without them (a pure caller), only the model now set is dropped from the saved candidates.
        state = bound_state()
        state['job_failure']['route'].update(candidates=['gpt-6-luna', 'gpt-6-nova'], recommendation='stale')
        self.reroute(state)
        route = state['job_failure']['route']
        self.assertEqual(('gpt-6-luna', ['gpt-6-nova']), (route['current_model'], route['candidates']))
        self.assertNotIn('recommendation', route)

    def test_the_named_model_is_bound_under_a_new_token(self):
        state = bound_state()
        token = self.reroute(state)
        failure = state['job_failure']
        self.assertEqual(token, failure['job_retry_token'])
        self.assertNotEqual('jr:old', token)
        self.assertEqual('gpt-6-luna', failure['configuration']['roles']['sol']['model'])
        self.assertEqual(('gpt-6-luna', 'gpt-6-luna'),
                         (failure['route']['current_model'], failure['route_assignment']['to']))
        self.assertNotIn('job_retry_authorization', state)
        self.assertIn('The Code Reviewer now runs on gpt-6-luna', state['stop_reason'])

    def test_a_route_moved_off_the_bound_model_is_rebound_by_the_answer(self):
        # A --sol-model flag saved at a job stop before #463 left the route on gpt-6-nova; the answer still applies.
        state = bound_state(settings_sol='gpt-6-nova')
        self.reroute(state)
        self.assertEqual(job_failure.configuration(state), state['job_failure']['configuration'])

    def test_anything_else_changed_refuses_and_keeps_the_shown_token(self):
        cases = {
            'another role': ({'role': 'terra'}, True, None, 'does not name the stopped Code Reviewer'),
            'another attempt': ({'attempt_id': '001/review-change-02'}, True, None, 'does not name the stopped'),
            'source': ({}, False, None, 'Source changed since the failed attempt'),
            'limits': ({}, True, ('limits', {'max_stage_seconds': 60}), 'Provider configuration or limits changed'),
            'other route': ({}, True, ('roles', {'sol': {'model': 'x'}, 'terra': {'model': 'gpt-6-nova'}}),
                            'Provider configuration or limits changed'),
        }
        for name, (changes, matches, setting, message) in cases.items():
            with self.subTest(name):
                state = bound_state()
                if setting:
                    state['settings'][setting[0]] = copy.deepcopy(setting[1])
                with self.assertRaisesRegex(ValueError, message):
                    self.reroute(state, matches=matches, **changes)
                failure = state['job_failure']
                self.assertEqual(('jr:old', 'gpt-6-sol'),
                                 (failure['job_retry_token'], failure['configuration']['roles']['sol']['model']))
                self.assertNotIn('route_assignment', failure)
        state = bound_state()
        state['job_failure']['unrestored'] = ['notes.txt']
        with self.assertRaisesRegex(ValueError, 'Unrestored source blocks job retry: notes.txt'):
            self.reroute(state, matches=False)
        state = bound_state()
        state['job_failure']['source_identity'] = None
        with self.assertRaisesRegex(ValueError, 'no saved original source identity'):
            self.reroute(state)


def unrouted_stop(stage='investigate_stuck', *, kind='content_filter', pause_status='PAUSED_CONTENT_FILTER'):
    """A job stopped on its model with no model question saved: the stuck-stage Investigator, or a stop saved
    before #463 (its kind only, no pause_status)."""
    reason = "the provider's content filter refused the response on gpt-6-luna"
    failure = {'stage': stage, 'attempt_id': '002/stopped-01', 'job_retry_token': 'jr:t', 'reason': reason,
               'kind': kind, 'source_identity': 'source', 'configuration': {}, 'archive': 'archive',
               'write_diagnosis': {}, 'unrestored': []}
    if pause_status:
        failure['pause_status'] = pause_status
    return {'status': 'PAUSED_JOB_FAILURE', 'next_stage': stage, 'stop_reason': reason, 'job_failure': failure,
            'settings': {'engine': 'codex', 'roles': {'sol': {'model': 'gpt-6-sol'}}}, 'stages': []}


class JobWithoutModelQuestionTests(unittest.TestCase):
    """A job stop about its model that keeps only the exact retry names its real cause (pure)."""

    def test_a_job_quota_stop_keeps_the_providers_words_in_any_error_shape(self):
        # OpenCode's APIError nests the text under error.data (Z.AI's used-up plan, #521); a flat error has
        # it under error.message. Either way the reason keeps the provider's words, reset time included.
        import autocode_support as support
        import types
        runtime = types.SimpleNamespace(support=support)
        said = "Weekly/Monthly Limit Exhausted. Your limit will reset at 2026-10-09 10:51:41"
        with tempfile.TemporaryDirectory() as temp:
            for name, row in (("apierror", {"type": "error", "error": {"name": "APIError", "data": {
                                  "message": said, "statusCode": 429, "isRetryable": True}}}),
                              ("flat", {"type": "error", "error": {"message": said}})):
                with self.subTest(name):
                    events = Path(temp) / f"{name}.jsonl"
                    events.write_text(json.dumps(row) + "\n")
                    record = {"stage": "review_change", "events": str(events), "exit_code": 1,
                              "launch_route": {"model": "gpt-6-sol"}}
                    kind, reason = job_failure._reason(runtime, record, support.Paused("PAUSED_BUDGET", "x"))
                    self.assertEqual("quota", kind)
                    self.assertIn(f"used up on gpt-6-sol ({said})", reason)

    def test_a_job_resumed_after_a_crash_asks_no_model_for_another_sessions_response(self):
        # Recovered with no runner error (a resume or --abandon-stage after AutoCode stopped), the job checks the
        # saved response's session as the runner would have: another session's quota stop or refusal, after a
        # clean, unsaved or signalled exit, asks no model; the expected session's, or a failed exit's, does.
        import autocode_support as support
        import types
        runtime = types.SimpleNamespace(support=support)
        errors = {"quota": {"type": "error", "error": {"message": "subscription usage limit reached"}},
                  "content_filter": {"type": "turn.failed", "error": {"code": "content_filter", "message": "x"}}}
        with tempfile.TemporaryDirectory() as temp:
            for routed, row in errors.items():
                for thread, expected, code, typed in (("ses_other", "ses_saved", 0, False), (None, None, None, False),
                                                      ("ses_other", "ses_saved", -15, False),
                                                      ("ses_saved", "ses_saved", 0, True),
                                                      ("ses_other", "ses_saved", 1, True)):
                    with self.subTest(routed=routed, thread=thread, expected=expected, code=code):
                        events = Path(temp) / f"{routed}-{thread}-{code}.jsonl"
                        rows = ([{"type": "thread.started", "thread_id": thread}] if thread else []) + [row]
                        events.write_text("".join(json.dumps(r) + "\n" for r in rows))
                        record = {"stage": "review_change", "events": str(events), "exit_code": code,
                                  "supports_sessions": True, "expected_session": expected,
                                  "launch_route": {"model": "gpt-6-sol"}}
                        kind, reason = job_failure._reason(runtime, record, None)
                        if typed:
                            self.assertEqual(routed, kind)
                        else:
                            self.assertEqual("exit", kind)
                            self.assertIn(job_failure.UNEXPECTED_SESSION, reason)

    def test_a_quota_or_refusal_from_an_unexpected_session_asks_no_model(self):
        # When the runner itself stopped on a session it did not expect (#464), the job's stop names that,
        # not a quota stop or a refusal, so recover stores no pause_status and asks no model question.
        import autocode_support as support
        import types
        runtime = types.SimpleNamespace(support=support)
        with tempfile.TemporaryDirectory() as temp:
            for name, row, routed in (
                    ("quota", {"type": "error", "error": {"message": "subscription usage limit reached"}}, "quota"),
                    ("filter", {"type": "turn.failed", "error": {"code": "content_filter", "message": "x"}},
                     "content_filter")):
                with self.subTest(name):
                    events = Path(temp) / f"{name}.jsonl"
                    events.write_text(json.dumps(row) + "\n")
                    record = {"stage": "review_change", "events": str(events), "exit_code": 0,
                              "launch_route": {"model": "gpt-6-sol"}}
                    untrusted = support.Paused("PAUSED_UNCERTAIN_STAGE", "Provider returned a missing or unexpected session ID")
                    kind, reason = job_failure._reason(runtime, record, untrusted)
                    self.assertEqual("exit", kind)
                    self.assertIn("unexpected session", reason)
                    typed = support.Paused(job_failure._ROUTE_STOPS[routed], "typed")
                    self.assertEqual(routed, job_failure._reason(runtime, record, typed)[0])


    def answer(self, state, host=None):
        args = argparse.Namespace(answer=['route-investigator=gpt-6-nova'], delegate=[], delegate_all=False,
                                  job_retry_token='jr:t', resume_paused=False)
        before = copy.deepcopy(state)
        stderr = io.StringIO()
        with contextlib.redirect_stderr(stderr), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(2, job_route.answer(host, args, state, '/run', '/workspace'))
        self.assertEqual(before, state)
        return stderr.getvalue()

    def legacy_exit(self, error):
        """A job stop saved before #463 as kind 'exit' (no pause_status), its provider error archived."""
        temp = tempfile.TemporaryDirectory(prefix='job-route-legacy-')
        self.addCleanup(temp.cleanup)
        archive = Path(temp.name)/'archived-reviewer-01-abc123'
        archive.mkdir()
        events = archive/'reviewer-01.jsonl'
        events.write_text(json.dumps({'type': 'error', 'error': error}) + '\n')
        state = unrouted_stop('review_change', kind='exit', pause_status=None)
        state['job_failure'].update(archive=str(archive), reason='Code Reviewer: provider exited 1 without a terminal '
                                                                 'report; ' + error['message'])
        state['stages'] = [{'stage': 'review_change', 'iteration': 2, 'events': str(events), 'rejected': True}]
        return state

    def test_a_quota_stop_saved_before_jobs_took_a_model_says_quota(self):
        state = self.legacy_exit({'message': 'subscription usage limit reached'})
        message = self.answer(state, runner)  # the runner's provider-error classifier reads the archived events
        self.assertIn('The stopped Code Reviewer stopped on quota, but this stop was saved before a stopped job '
                      'could take another model', message)
        self.assertIn('once the quota resets, retry it unchanged with --resume-paused --retry-failed-stage '
                      '--job-retry-token TOKEN', message)
        self.assertNotIn('did not stop on quota', message)
        # Without a classifier the cause is unknown, and the refusal claims none.
        message = self.answer(state)
        self.assertIn('The stopped Code Reviewer takes no other model: only a job stopped on quota or a '
                      'content-filter refusal does', message)
        self.assertNotIn('did not stop on quota', message)
        # An archived error that is not about the model still says so.
        other = self.legacy_exit({'message': 'connection reset by peer'})
        self.assertIn('did not stop on quota or a content-filter refusal', self.answer(other, runner))

    def test_a_refused_stuck_stage_investigator_keeps_only_its_exact_retry(self):
        state = unrouted_stop()
        view = run_view.view(state)
        self.assertEqual('retry_job', view['needs']['kind'])
        self.assertNotIn('route', view['needs'])
        self.assertEqual("The provider's content filter refused the Investigator's response. The same model is likely "
                         "to refuse it again; this stop keeps only its exact retry.", view['recovery']['what_happened'])
        message = self.answer(state)
        self.assertIn("Input rejected: The stopped Investigator was refused by its provider's content filter, but the "
                      "stuck-stage Investigator's route is rebuilt for every investigation", message)
        self.assertIn('--resume-paused --retry-failed-stage --job-retry-token TOKEN', message)
        self.assertNotIn('did not stop on quota', message)
        quota = unrouted_stop(kind='quota', pause_status='PAUSED_BUDGET')
        self.assertIn('stopped on quota', self.answer(quota))
        self.assertIn('once the quota resets, retry it unchanged with', self.answer(quota))
        self.assertIn('Retry it once the quota resets.', run_view.view(quota)['recovery']['what_happened'])

    def test_a_refusal_saved_before_jobs_took_a_model_says_so(self):
        state = unrouted_stop('review_change', pause_status=None)
        self.assertIn("content filter refused the Code Reviewer's response",
                      run_view.view(state)['recovery']['what_happened'])
        message = self.answer(state)
        self.assertIn("The stopped Code Reviewer was refused by its provider's content filter, but this stop was saved "
                      "before a stopped job could take another model", message)
        self.assertNotIn('did not stop on quota', message)
