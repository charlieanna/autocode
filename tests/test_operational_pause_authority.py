"""A protected operational pause is released only by the operator's authority for that pause.

A settings write that does not change the exhausted bound, and a pause intervention, are not
that authority (#379, #486; docs/bugs/2026-10-06-operational-pause-authority.md). One fixture run
stops on quota at the Completion Reviewer and is copied back before each case; every case then
publishes AutoResolver's operational request for one pause and drives the CLI with the fake
provider, as real processes for the reported sequences and in-process for every pause status.
No sleeps, no live models.
"""
import io
import itertools
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from . import test_subprocess
import autocode as runner
import autocode_pause_authority as pause_authority
import autocode_resolver_human as human
import autocode_resolver_runtime as resolver_runtime
import autocode_support as support

# Every pause AutoResolver publishes an operational_exhaustion request for.
STATUSES = ('PAUSED_RESOLVER_OPERATIONAL', 'PAUSED_TIMEOUT_RECOVERY', 'PAUSED_PROVIDER_CAPACITY',
            'PAUSED_PLANNING_BUDGET', 'PAUSED_RATE_LIMIT', 'PAUSED_TIME_LIMIT', 'PAUSED_ITERATION_LIMIT',
            'PAUSED_BUDGET', 'PAUSED_REPORT_REPAIR_LIMIT', 'PAUSED_REPEATED_FAILURE', 'PAUSED_RESOLVER',
            'PAUSED_ORCHESTRATOR_WORKER', 'PAUSED_BUILDER_RETRY_LIMIT', 'PAUSED_MILESTONE_STALLED',
            'PAUSED_MILESTONE_BUDGET', 'PAUSED_MILESTONE_TIME_LIMIT', 'PAUSED_PROVIDER_UNCERTAIN',
            'PAUSED_UNCERTAIN_STAGE', 'PAUSED_WORKSPACE_BUSY', 'PAUSED_NO_PROGRESS', 'PAUSED_CONTENT_FILTER')
# One of each kind of handling, for the variants that are not the reported ones: the planning guard,
# the planning budget (plan feedback acknowledges it), provider stops, reassertable and other bounds.
SAMPLE = ('PAUSED_RESOLVER_OPERATIONAL', 'PAUSED_TIMEOUT_RECOVERY', 'PAUSED_PLANNING_BUDGET', 'PAUSED_RATE_LIMIT',
          'PAUSED_TIME_LIMIT', 'PAUSED_ITERATION_LIMIT', 'PAUSED_NO_PROGRESS', 'PAUSED_CONTENT_FILTER')
# The pauses a budget flag acknowledges: the flag that changes the exhausted bound, with headroom.
ACKNOWLEDGING = {'PAUSED_TIME_LIMIT': ('--max-seconds', '50000'),
                 'PAUSED_ITERATION_LIMIT': ('--max-iterations', '9'),
                 'PAUSED_MILESTONE_TIME_LIMIT': ('--max-milestone-seconds', '50000'),
                 'PAUSED_MILESTONE_BUDGET': ('--max-milestone-seconds', '50000'),
                 'PAUSED_NO_PROGRESS': ('--no-progress-limit', '5')}
STOP_AT_TESTER = {'AUTOCODE_FIXTURE_QUOTA_STAGE': 'sol'}  # the admitted stage records its launch, then stops
FEEDBACK = 'Prefer a shorter greeting'
# Timeout recoveries spent, so --grant-recovery is that pause's own authority.
SPENT_TIMEOUT_RECOVERIES = dict(
    automatic_recoveries_since_resume=3, consecutive_timeout_recoveries=3,
    automatic_timeout_recoveries=[{'stage': 'terra', 'timeout_reason': 'idle watchdog'} for _ in range(3)])


def explicit_bound(kind, limit, **fields):
    """A saved explicit limit the pause exhausted (the realistic cause, set at the stage it pauses)."""
    def edit(state):
        state['settings']['limits'][kind] = limit
        state['settings'].setdefault('budget_origins', {})[kind] = 'user_explicit'
        state.update(fields)
    return edit


class OperationalPauseAuthorityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.flow = flow = test_subprocess.SubprocessFlow('setUp')
        flow.setUp()
        cls.addClassCleanup(flow.doCleanups)
        flow.env.update(AUTOCODE_FIXTURE_MODE='no-human', AUTOCODE_FIXTURE_QUOTA_STAGE='astra_review')
        flow.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        run, paused = flow.saved()
        flow.launch(['--run-dir', str(run), '--abandon-stage', runner.attempt_id(paused['active_stage'])], 0)
        flow.env.pop('AUTOCODE_FIXTURE_QUOTA_STAGE')  # a launched stage would otherwise stop on quota again
        cls.run_dir = run
        copies = tempfile.mkdtemp()
        cls.addClassCleanup(shutil.rmtree, copies, ignore_errors=True)
        cls.template = Path(copies) / 'fixture'
        shutil.copytree(flow.root, cls.template, symlinks=True)
        cls.launches = itertools.count()

    def stopped(self, status, edit=None):
        """A fresh copy of the stopped run, its recoveries spent, at ``status``; the request not yet staged."""
        shutil.rmtree(self.flow.root)
        shutil.copytree(self.template, self.flow.root, symlinks=True)
        state = self.saved()
        state.setdefault('automatic_permission_recoveries', []).extend(
            {'stage': 'terra', 'instruction': 'workspace paths only'} for _ in range(2))
        if edit:
            edit(state)
        state.update(status=status, stop_reason=f'{status} fixture stop')
        return state

    def stage_request(self, state, *, then=None):
        """AutoResolver stages its operational request for the saved pause; the save publishes it."""
        with patch.dict(os.environ, self.flow.env, clear=True):
            self.assertTrue(resolver_runtime.record_operational_exhaustion(
                runner, state, self.run_dir, support.Paused(state['status'], state['stop_reason'])))
            if then:
                then(state)
            runner.write_json(self.run_dir / 'state.json', state)
        return self.saved()

    def checkpoint(self, status='PAUSED_RESOLVER_OPERATIONAL', edit=None):
        """Publish AutoResolver's operational request for ``status`` on a fresh copy of the stopped run."""
        state = self.stage_request(self.stopped(status, edit))
        self.assertEqual(status, self.pause_in_force(state))
        return state

    def saved(self):
        return json.loads((self.run_dir / 'state.json').read_text())

    def pause_in_force(self, state):
        """The pause holding the run: its published operational request's, else its saved status."""
        published = human.current(state)
        if published is None:
            return state['status']
        self.assertEqual('operational_exhaustion', published['scope'])
        entry = state['resolver']['human_escalations'][published['request_id']]
        return entry['identity']['proposal']['origin']['pause_status']

    def invoke(self, *flags, process=False, env=None):
        """Run the CLI on the saved run; return (exit code, stages a provider launched, output)."""
        return self.cli('--workspace', str(self.flow.project), '--run-dir', str(self.run_dir), *flags,
                        '--no-chat', process=process, env=env)

    def cli(self, *argv, process=False, env=None):
        probe = self.flow.root / f'launch-{next(self.launches)}.jsonl'
        environment = {**self.flow.env, **(env or {}), 'AUTOCODE_REGISTRY_LAUNCH_PROBE': str(probe)}
        if process:
            result = subprocess.run([*self.flow.entry, *argv], cwd=self.flow.root, env=environment,
                                    capture_output=True, text=True, timeout=240)
            code, output = result.returncode, result.stdout + result.stderr
        else:
            stream = io.StringIO()
            with patch.dict(os.environ, environment, clear=True), patch.object(sys, 'argv', ['autocode', *argv]), \
                    redirect_stdout(stream), redirect_stderr(stream):
                try:
                    code = runner.main()
                except SystemExit as error:
                    code = error.code
            output = stream.getvalue()
        stages = [json.loads(line)['stage'] for line in probe.read_text().splitlines()] if probe.exists() else []
        return code, stages, output

    def answer(self, state, **options):
        published = human.current(state)
        code, stages, output = self.invoke(
            '--resolver-request', published['request_id'], '--resolver-token', published['request_token'],
            '--resolver-response', 'provide_information', '--resolver-message', 'The temporary directory is valid',
            **options)
        self.assertEqual((0, []), (code, stages), output)

    def submit_pause(self, request_id='req-pause', **options):
        self.submit('pause', request_id, **options)

    def submit(self, kind, request_id, text='', **options):
        code, _, output = self.cli('intervention', 'submit', '--workspace', str(self.flow.project),
                                   '--run-dir', str(self.run_dir), '--request-id', request_id, '--kind', kind,
                                   '--text', text, **options)
        self.assertEqual(0, code, output)

    def assert_held(self, result, status='PAUSED_RESOLVER_OPERATIONAL', *, settled=True):
        """No provider launched and the run did not resume; ``settled``: that pause is in force again."""
        code, stages, output = result
        self.assertEqual([], stages, f'no provider may launch past {status}:\n{output}')
        self.assertEqual(2, code, output)
        state = self.saved()
        self.assertNotEqual('RUNNING', state['status'], output)
        if settled:
            self.assertEqual(status, self.pause_in_force(state), output)

    def assert_resumes(self, result):
        code, stages, output = result
        self.assertEqual(['sol'], stages, f'changing the exhausted bound must admit the next stage:\n{output}')

    # (a) The reported sequences, as real processes: a budget flag for another bound on --resume-paused.
    def test_unrelated_limit_on_resume_never_releases_an_unanswered_operational_pause(self):
        self.checkpoint()
        self.assert_held(self.invoke('--resume-paused', '--max-stage-seconds', '1200', process=True))
        self.assertEqual(1200, self.saved()['settings']['limits']['stage_timeout_seconds'],
                         'the unrelated change itself is kept')
        self.assert_held(self.invoke('--resume-paused', process=True))

    def test_unrelated_limit_on_resume_never_releases_an_answered_operational_pause(self):
        self.answer(self.checkpoint(), process=True)
        self.assert_held(self.invoke('--resume-paused', '--max-stage-seconds', '1200', process=True))
        self.assert_held(self.invoke('--resume-paused', process=True))

    def test_restating_a_default_limit_never_releases_the_pause(self):
        # The explicit flag turns the default into a user-explicit limit: a settings change, not authority.
        self.checkpoint()
        self.assert_held(self.invoke('--resume-paused', '--max-stage-seconds', '3600', process=True))

    # (b) The reported sequence, as real processes: a pause intervention after an answered request.
    def resume_after_answer(self, status, *, pause, process=False):
        """Answer the request; with ``pause``, submit a pause intervention and invoke plainly; then resume."""
        self.answer(self.checkpoint(status), process=process)
        if pause:
            self.submit_pause(process=process)
            self.assert_held(self.invoke(process=process), status, settled=False)
        return self.invoke('--resume-paused', process=process, env=STOP_AT_TESTER)

    def assert_intervention_adds_nothing(self, status, process=False):
        """Resuming the intervention decides exactly as resuming the answered pause would without it."""
        _, without, _ = self.resume_after_answer(status, pause=False, process=process)
        result = self.resume_after_answer(status, pause=True, process=process)
        self.assertEqual(without, result[1], f'the pause intervention changed what {status} admits:\n{result[2]}')
        if not without:
            self.assert_held(result, status)

    def test_pause_intervention_never_releases_an_answered_operational_pause(self):
        self.assert_intervention_adds_nothing('PAUSED_RESOLVER_OPERATIONAL', process=True)

    def test_pause_intervention_never_releases_an_unanswered_operational_pause(self):
        self.checkpoint()
        self.submit_pause(process=True)
        self.assert_held(self.invoke(process=True), settled=False)
        self.assertEqual('PAUSED_INTERVENTION', self.saved()['status'], 'the queued pause is applied, never stranded')
        # Resuming the intervention returns to the operational pause and asks its request again.
        self.assert_held(self.invoke('--resume-paused', process=True))
        self.answer(self.saved(), process=True)

    # Every pause status, in-process.
    def test_no_unrelated_limit_releases_any_operational_pause(self):
        for status, answered in itertools.product(STATUSES, (False, True)):
            with self.subTest(status=status, answered=answered):
                state = self.checkpoint(status)
                if answered:
                    self.answer(state)
                self.assert_held(self.invoke('--resume-paused', '--max-stage-seconds', '1200'), status)

    def test_no_pause_intervention_releases_any_answered_operational_pause(self):
        for status in STATUSES:
            with self.subTest(status=status):
                self.assert_intervention_adds_nothing(status)

    def stranded(self, status, saved_status):
        """A run an earlier version left with its operational request queued under a moved frontier.

        It staged the request while a pause intervention was pending, so its receipt named that inbox
        and could never be published; here the frontier moves by accounted stage time instead.
        """
        def account(state):
            state['active_seconds'] = state.get('active_seconds', 0) + 1
        state = self.stage_request(self.stopped(status), then=account)
        self.assertEqual(('RESOLVER_PENDING', None), (state['status'], human.current(state)))
        if saved_status != 'RESOLVER_PENDING':
            state['status'] = saved_status
            (self.run_dir / 'state.json').write_text(json.dumps(state))
        return state

    def test_a_stranded_operational_request_is_asked_again_never_resumed(self):
        for command in (('--resume-paused',), ()):
            with self.subTest(command=command):
                self.stranded('PAUSED_RESOLVER_OPERATIONAL', 'RESOLVER_PENDING')
                self.assert_held(self.invoke(*command))
                self.assertEqual('WAITING_FOR_USER', self.saved()['status'], 'the request is published again')

    def test_a_save_that_defers_a_queued_request_launches_nothing(self):
        # Defense in depth at stage admission: the stage's own save before launch deferred the queued request.
        self.stranded('PAUSED_RESOLVER_OPERATIONAL', 'RUNNING')
        self.assert_held(self.invoke('--resume-paused'), settled=False)

    # Review of the fix: every other input that replaced the pause, or arrived with a settings write.
    def stacked_pauses(self, status, *, answered, process=False):
        """Two pause interventions, each applied by an invocation, then a resume."""
        state = self.checkpoint(status)
        if answered:
            self.answer(state, process=process)
        # Unanswered, as reported: a settings write on the first resume applies the first pause.
        first = () if answered else ('--resume-paused', '--max-stage-seconds', '1200')
        self.submit_pause('req-pause-1', process=process)
        self.assert_held(self.invoke(*first, process=process), status, settled=False)
        self.submit_pause('req-pause-2', process=process)
        self.assert_held(self.invoke(process=process), status, settled=False)
        self.assertEqual(status, self.saved()['pause_intent']['held_pause']['status'],
                         'the second pause keeps the pause the first one interrupted')
        return self.invoke('--resume-paused', process=process, env=STOP_AT_TESTER)

    def test_a_second_pause_intervention_never_releases_an_operational_pause(self):
        self.assert_held(self.stacked_pauses('PAUSED_RESOLVER_OPERATIONAL', answered=True, process=True))
        cases = [*((status, True) for status in STATUSES), *((status, False) for status in SAMPLE)]
        for status, answered in cases:
            with self.subTest(status=status, answered=answered):
                self.assert_held(self.stacked_pauses(status, answered=answered), status)
                if not answered:  # asked again from its own cause: the earlier advice is not repeated
                    self.assertEqual(1, self.saved()['stop_reason'].count('AutoResolver could not resolve'),
                                     self.saved()['stop_reason'])

    def queued_feedback(self, status, variant, *, process=False):
        """Feedback queued at the operational pause, an invocation that applies it, then a resume."""
        state = self.checkpoint(status)
        if variant == 'answered':
            self.answer(state, process=process)
        self.submit('feedback', 'req-feedback', FEEDBACK, process=process)
        flags = ('--max-stage-seconds', '1200') if variant == 'settings write' else ()
        self.assert_held(self.invoke('--resume-paused', *flags, process=process), status, settled=False)
        self.assertEqual(FEEDBACK, self.saved()['brief_feedback'][-1]['text'], 'the feedback itself is applied')
        return self.invoke('--resume-paused', process=process, env=STOP_AT_TESTER)

    def test_queued_feedback_never_releases_an_operational_pause(self):
        # Only plan feedback acknowledges a pause: the exhausted plan-review budget.
        self.assert_held(self.queued_feedback('PAUSED_RATE_LIMIT', 'settings write', process=True), 'PAUSED_RATE_LIMIT')
        for status, variant in itertools.product(SAMPLE, ('settings write', 'unanswered', 'answered')):
            with self.subTest(status=status, variant=variant):
                result = self.queued_feedback(status, variant)
                if status == 'PAUSED_PLANNING_BUDGET':
                    self.assertTrue(result[1], f'plan feedback acknowledges the planning budget:\n{result[2]}')
                else:
                    self.assert_held(result, status)

    def test_brief_feedback_never_releases_an_operational_pause(self):
        for status in STATUSES:
            with self.subTest(status=status):
                self.checkpoint(status)
                code, stages, output = self.invoke('--feedback', FEEDBACK)
                if status == 'PAUSED_PLANNING_BUDGET':  # plan feedback is that pause's own authority
                    self.assertEqual(0, code, output)
                    continue
                self.assertEqual((2, []), (code, stages), output)
                self.assertIn('does not acknowledge', output)
                self.assert_held(self.invoke(env=STOP_AT_TESTER), status)

    def test_enabling_joint_planning_never_releases_an_operational_pause(self):
        # Enabling it is a settings write (an unanswered request is refused earlier, by configure).
        unattended = [sys.executable, str(Path(runner.__file__).with_name('autocode_unattended.py'))]
        cases = [*((status, False) for status in STATUSES), ('PAUSED_RESOLVER_OPERATIONAL', True)]
        for status, paused in cases:
            with self.subTest(status=status, after_pause_intervention=paused):
                self.answer(self.checkpoint(status))
                if paused:
                    self.submit_pause()
                    self.assert_held(self.invoke(), status, settled=False)
                if status == 'PAUSED_RATE_LIMIT' and not paused:  # the reported unattended call, as a process
                    probe = self.flow.root / f'launch-{next(self.launches)}.jsonl'
                    result = subprocess.run(
                        [*unattended, '--workspace', str(self.flow.project), '--run-dir', str(self.run_dir),
                         '--joint-planning'], cwd=self.flow.root, capture_output=True, text=True, timeout=240,
                        env={**self.flow.env, **STOP_AT_TESTER, 'AUTOCODE_REGISTRY_LAUNCH_PROBE': str(probe)})
                    self.assertFalse(probe.exists(), result.stdout + result.stderr)
                else:
                    self.assert_held(self.invoke('--joint-planning', env=STOP_AT_TESTER), status, settled=not paused)
                saved = self.saved()
                self.assertTrue(saved['settings']['joint_planning'], 'the setting itself is kept')
                self.assertEqual('requirements_gather', saved['next_stage'])
                self.assert_held(self.invoke('--resume-paused', env=STOP_AT_TESTER), status)

    def test_a_requested_pause_never_releases_an_operational_pause(self):
        # The run-local pause-requested file, with and without a settings write, answered or not.
        flags = ((), ('--max-stage-seconds', '1200'))
        for answered, extra in itertools.product((False, True), flags):
            with self.subTest(answered=answered, flags=extra):
                state = self.checkpoint()
                if answered:
                    self.answer(state)
                pause = self.run_dir / 'pause-requested'
                pause.write_text('operator')
                code, stages, output = self.invoke('--resume-paused', *extra, env=STOP_AT_TESTER)
                self.assert_held((code, stages, output), settled=False)
                self.assertIn('pause-requested', output)
                pause.unlink()
                self.assert_held(self.invoke('--resume-paused', env=STOP_AT_TESTER))

    def test_queued_milestone_checkpoints_never_release_an_operational_pause(self):
        for status, answered in itertools.product(('PAUSED_RESOLVER_OPERATIONAL', 'PAUSED_TIME_LIMIT'), (False, True)):
            with self.subTest(status=status, answered=answered):
                state = self.checkpoint(status)
                if answered:
                    self.answer(state)
                code, _, output = self.cli('--workspace', str(self.flow.project), '--run-dir', str(self.run_dir),
                                           '--request-milestone-checkpoints')
                self.assertEqual(0, code, output)
                self.assert_held(self.invoke('--resume-paused', env=STOP_AT_TESTER), status)
                self.assertTrue(self.saved()['settings']['milestone_checkpoints'],
                                'the next invocation applies checkpoints without starting a provider')
                self.assertFalse((self.run_dir / 'pause-requested').exists())
                self.assert_held(self.invoke('--resume-paused', env=STOP_AT_TESTER), status)

    def test_authority_given_while_a_pause_is_queued_survives_the_intervention(self):
        # The acknowledgement is applied first; the queued pause then pauses a released run, and
        # resuming that pause continues without acknowledging the bound again.
        cases = [(status, ('--resume-paused', *flag), None, answered)
                 for (status, flag), answered in itertools.product(ACKNOWLEDGING.items(), (False, True))]
        # A grant checks the answered request it names (an unanswered one is stale while input is queued).
        cases.append(('PAUSED_TIMEOUT_RECOVERY', ('--resume-paused', '--grant-recovery', '1'),
                      lambda state: state.update(SPENT_TIMEOUT_RECOVERIES), True))
        for status, command, edit, answered in cases:
            with self.subTest(status=status, command=command, answered=answered):
                state = self.checkpoint(status, edit)
                if answered:
                    self.answer(state)
                self.submit_pause()
                self.assert_held(self.invoke(*command, env=STOP_AT_TESTER), status, settled=False)
                self.assertEqual('PAUSED_INTERVENTION', self.saved()['status'])
                self.assertNotIn('held_pause', self.saved()['pause_intent'])
                self.assert_resumes(self.invoke('--resume-paused', env=STOP_AT_TESTER))

    # Controls: changing the exhausted bound itself is the operator's authority and still resumes in one
    # command, answered or not, and after a pause intervention (#301, #378, #394).
    def test_changing_the_exhausted_bound_still_resumes_every_bounded_pause(self):
        for (status, flag), variant in itertools.product(ACKNOWLEDGING.items(), ('unanswered', 'answered', 'paused')):
            with self.subTest(status=status, variant=variant):
                state = self.checkpoint(status)
                if variant != 'unanswered':
                    self.answer(state)
                if variant == 'paused':
                    self.submit_pause()
                    self.assert_held(self.invoke(), status, settled=False)
                self.assert_resumes(self.invoke('--resume-paused', *flag, env=STOP_AT_TESTER))

    def test_raising_a_realistically_exhausted_bound_resumes(self):
        cases = (('PAUSED_ITERATION_LIMIT', explicit_bound('iteration_ceiling', 0), ('--max-iterations', '5')),
                 ('PAUSED_NO_PROGRESS', explicit_bound('no_progress_batches', 2, no_progress_batches=2,
                                                       next_stage='terra'), ('--no-progress-limit', '5')))
        for (status, edit, flag), answered in itertools.product(cases, (False, True)):
            with self.subTest(status=status, answered=answered):
                state = self.checkpoint(status, edit)
                if answered:
                    self.answer(state)
                code, stages, output = self.invoke('--resume-paused', *flag, env=STOP_AT_TESTER)
                self.assertTrue(stages, f'raising the exhausted bound must admit the next stage:\n{output}')


class HeldPauseTests(unittest.TestCase):
    """The pure records behind the CLI tests above."""

    def test_the_tested_statuses_are_every_operational_pause(self):
        self.assertEqual(sorted(STATUSES), sorted(pause_authority.OPERATIONAL_PAUSES))

    def test_held_cause_drops_the_advice_its_request_appended(self):
        def request(discovered, pause='PAUSED_RATE_LIMIT'):
            return {'identity': {'proposal': {'scope': 'operational_exhaustion', 'origin': {'pause_status': pause},
                                              'request': {'discovered': discovered}}}}
        state = {'stop_reason': 'Rate limited. AutoResolver could not resolve it. Provide information.',
                 'resolver': {'human_escalations': {'a': request('Rate limited'), 'b': request('Other', 'PAUSED_TIME_LIMIT'),
                                                    'c': request('Rate')}}}
        self.assertEqual('Rate limited', pause_authority.held_cause(state, 'PAUSED_RATE_LIMIT'))
        self.assertEqual(state['stop_reason'], pause_authority.held_cause(state, 'PAUSED_TIME_LIMIT'))
        state['stop_reason'] = 'AutoResolver received the human response; no execution was authorized.'
        self.assertEqual(state['stop_reason'], pause_authority.held_cause(state, 'PAUSED_RATE_LIMIT'))

    def test_a_later_intervention_keeps_the_pause_the_first_one_interrupted(self):
        import autocode_stop as stop
        held = {'status': 'PAUSED_RATE_LIMIT', 'stop_reason': 'Rate limited'}
        state = {'status': stop.STOP_STATUS, 'pause_intent': {'acknowledged_at': None, 'held_pause': held}}
        self.assertEqual(held, stop.interrupted_pause(state))
        state['pause_intent']['acknowledged_at'] = '2026-10-06T00:00:00+00:00'
        self.assertIsNone(stop.interrupted_pause(state))
        self.assertIsNone(stop.interrupted_pause({'status': 'RUNNING'}))
        self.assertEqual('PAUSED_TIME_LIMIT', stop.interrupted_pause({'status': 'PAUSED_TIME_LIMIT'})['status'])


if __name__ == '__main__':
    unittest.main()
