"""A workflow job its provider refused or ran out of quota on takes another model (#463), through the CLI.

The job harness of test_job_failure_recovery (copied Git seed, wrapper, TaskRun), with a fake
provider that refuses one model: a ContentFilterError error event, or a quota message, and exit 1.
Every other model writes a valid review. The provider's call log (one line per request, with its
model) and the saved state file are the oracles. No test sleeps or reaches a real provider.
"""
import json
import re
import subprocess
import os

from autocode_taskrun import TaskRun, TaskRunError

from .test_job_failure_recovery import JobHarness, ORIGINAL, PROVIDER

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
        recovery = run.status()['recovery']
        self.assertIn('content filter refused the Code Reviewer', recovery['what_happened'])
        self.assertEqual(['inspect', 'retry_job', 'feedback'], [action['kind'] for action in recovery['actions']])
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
        self.assertIn('quota', run.status()['recovery']['what_happened'])
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
                run.retry_job(self.stopped(run, stage)['job_retry_token'])
                self.assertEqual([stopped_on, 'gpt-6-nova'], self.models())

    def test_a_refused_job_set_aside_after_a_crash_takes_a_model_too(self):
        run = self.refused(mode='abandon')
        attempt = json.loads(self.cli(run, '--status').stdout)['attempt_id']
        proc = self.cli(run, '--abandon-stage', attempt)
        self.assertEqual(0, proc.returncode, proc.stderr)
        view = run.status()
        self.assertEqual(('PAUSED_STAGE_ABANDONED', 'retry_job', 'route-sol'),
                         (view['status'], view['needs']['kind'], view['needs']['route']['question_id']))
        run.assign_model('sol', 'gpt-6-luna')
        view = run.retry_job(run.status()['needs']['job_retry_token'])
        self.assertTrue(view['done'], view)
        self.assertEqual(['gpt-6-sol', 'gpt-6-luna'], self.models())

    def test_a_job_stopped_for_another_reason_takes_no_model(self):
        (self.workspace/'.autocode'/'bin'/'codex').write_text(PROVIDER)  # an interrupted attempt, exit 42
        run = self.start('exit42')
        need = self.stopped(run)
        self.assertNotIn('route', need)
        self.rejected(run, '--answer', 'route-sol=gpt-6-luna', '--job-retry-token', need['job_retry_token'],
                      message='did not stop on quota or a content-filter refusal')
