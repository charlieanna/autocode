"""A protected operational pause is released only by the operator's authority for that pause.

A settings write that does not change the exhausted bound, a pause intervention and an edited goal
are not that authority (#379, #486; docs/bugs/2026-10-06-operational-pause-authority.md). One fixture
run stops on quota at the Completion Reviewer and is copied back before each case; every case then
publishes AutoResolver's operational request for one pause and drives the CLI with the fake
provider, as real processes for the reported sequences and in-process for every pause status.
An answer or approval with --resume-paused, which dispatches the next stage once it clears a human
gate (#509), clears none at an operational pause, and AutoResolver's one re-evaluation of corrective
information (#486) decides afterwards exactly as it would without it. No sleeps, no live models.
"""
import copy
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

import autocode as runner
import autocode_goals as goals
import autocode_operational_information as operational_information
import autocode_pause_authority as pause_authority
import autocode_resolver_human as human
import autocode_resolver_runtime as resolver_runtime
import autocode_run_view as run_view
import autocode_support as support

from . import test_subprocess

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

    def view(self):
        """The run's status view (autocode_run_view, the public contract)."""
        return run_view.view(self.saved())

    def pause_in_force(self, state):
        """The pause holding the run: its published operational request's, else its saved status."""
        published = human.current(state)
        if published is None:
            return state['status']
        self.assertEqual('operational_exhaustion', published['scope'])
        return pause_authority.held_origin(state)['pause_status']

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
    def resume_after_answer(self, status, *, pause, process=False, applied_by=()):
        """Answer the request; with ``pause``, submit a pause intervention and apply it with the command
        ``applied_by`` (a plain invocation, or --resume-paused itself); then resume."""
        self.answer(self.checkpoint(status), process=process)
        if pause:
            self.submit_pause(process=process)
            self.assert_held(self.invoke(*applied_by, process=process, env=STOP_AT_TESTER), status, settled=False)
            self.assertIn(f'{status} still holds the run', self.view()['stop_reason'])
        return self.invoke('--resume-paused', process=process, env=STOP_AT_TESTER)

    _after_answer = {}

    def admitted_after_answer(self, status):
        """What --resume-paused admits after the answered request alone, with nothing else sent.

        AutoResolver re-evaluates the corrective information once at that resume (#486, #581): a stop
        caused outside the run continues, and one only an operator control releases is held.
        """
        return self.after_answer(status)[0]

    def after_answer(self, status):
        """(stages admitted, information review decided) by --resume-paused after the answered request alone.

        Worked out once per status on a fresh copy of the fixture; the run a caller is checking is put back.
        """
        if status not in self._after_answer:
            kept = Path(tempfile.mkdtemp()) / 'kept'
            shutil.copytree(self.flow.root, kept, symlinks=True)
            try:
                stages = self.resume_after_answer(status, pause=False)[1]
                self._after_answer[status] = (stages, self.information_review())
            finally:
                shutil.rmtree(self.flow.root)
                shutil.copytree(kept, self.flow.root, symlinks=True)
                shutil.rmtree(kept.parent, ignore_errors=True)
        return self._after_answer[status]

    def information_review(self):
        """The status view's information review (#581): what it decided, without its timestamps."""
        review = operational_information.projection(self.saved()) or {}
        return review.get('status'), review.get('decision')

    def assert_adds_nothing(self, result, status, *, answered=True):
        """``result`` admitted no more than the answered pause alone does (nothing, when unanswered)."""
        without = self.admitted_after_answer(status) if answered else []
        self.assertEqual(without, result[1], f'the input changed what {status} admits:\n{result[2]}')
        if not without:
            self.assert_held(result, status)

    def assert_intervention_adds_nothing(self, status, process=False, applied_by=()):
        """Resuming the intervention decides exactly as resuming the answered pause would without it."""
        self.assert_adds_nothing(self.resume_after_answer(status, pause=True, process=process, applied_by=applied_by),
                                 status)

    def test_pause_intervention_never_releases_an_answered_operational_pause(self):
        # At a pause the answered request alone holds (an operator-only control): master launched the Tester.
        self.assert_intervention_adds_nothing('PAUSED_TIMEOUT_RECOVERY', process=True)
        # Applied by --resume-paused itself, at a stop the answer alone continues: the queued pause was bound
        # into nothing the information was sent for, so it neither retires that answer nor stands in for it.
        self.assertEqual(['sol'], self.admitted_after_answer('PAUSED_RATE_LIMIT'))
        self.assert_intervention_adds_nothing('PAUSED_RATE_LIMIT', process=True, applied_by=('--resume-paused',))

    def test_pause_intervention_never_releases_an_unanswered_operational_pause(self):
        self.checkpoint()
        self.submit_pause(process=True)
        self.assert_held(self.invoke(process=True), settled=False)
        self.assertEqual('PAUSED_INTERVENTION', self.view()['status'], 'the queued pause is applied, never stranded')
        # Resuming the intervention returns to the operational pause and asks its request again.
        self.assert_held(self.invoke('--resume-paused', process=True))
        self.answer(self.saved(), process=True)

    def test_a_pause_queued_over_an_unanswered_request_leaves_its_own_recovery_usable(self):
        # Before, the plain invocation left no request to answer and --grant-recovery was refused
        # ("requires a run paused for exhausted timeout recovery"); only an unrelated settings write moved it.
        self.checkpoint('PAUSED_TIMEOUT_RECOVERY', lambda state: state.update(SPENT_TIMEOUT_RECOVERIES))
        self.submit_pause()
        self.assert_held(self.invoke(), 'PAUSED_TIMEOUT_RECOVERY', settled=False)
        self.assertEqual('PAUSED_INTERVENTION', self.view()['status'], 'the queued pause is applied')
        self.assert_held(self.invoke('--resume-paused'), 'PAUSED_TIMEOUT_RECOVERY')
        self.assertIsNotNone(human.current(self.saved()), 'its request is asked again')
        self.assert_resumes(self.invoke('--resume-paused', '--grant-recovery', '1', env=STOP_AT_TESTER))

    # Every pause status, in-process.
    def test_no_unrelated_limit_releases_any_operational_pause(self):
        for status, answered in itertools.product(STATUSES, (False, True)):
            with self.subTest(status=status, answered=answered):
                state = self.checkpoint(status)
                if answered:
                    self.answer(state)
                self.assert_held(self.invoke('--resume-paused', '--max-stage-seconds', '1200'), status)

    def test_no_pause_intervention_releases_any_answered_operational_pause(self):
        for status, applied_by in itertools.product(STATUSES, ((), ('--resume-paused',))):
            with self.subTest(status=status, applied_by=applied_by):
                self.assert_intervention_adds_nothing(status, applied_by=applied_by)

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
                self.assertEqual('WAITING_FOR_USER', self.view()['status'], 'the request is published again')

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
        self.assertIn(f'{status} still holds the run', self.view()['stop_reason'],
                      'the second pause keeps the pause the first one interrupted')
        return self.invoke('--resume-paused', process=process, env=STOP_AT_TESTER)

    def test_a_second_pause_intervention_never_releases_an_operational_pause(self):
        # As a process at a pause the answered request alone does not release (an operator-only control).
        self.assert_held(self.stacked_pauses('PAUSED_TIMEOUT_RECOVERY', answered=True, process=True),
                         'PAUSED_TIMEOUT_RECOVERY')
        cases = [*((status, True) for status in STATUSES), *((status, False) for status in SAMPLE)]
        for status, answered in cases:
            with self.subTest(status=status, answered=answered):
                self.assert_adds_nothing(self.stacked_pauses(status, answered=answered), status, answered=answered)
                if not answered:  # asked again from its own cause: the earlier advice is not repeated
                    self.assertEqual(1, self.view()['stop_reason'].count('AutoResolver could not resolve'),
                                     self.view()['stop_reason'])

    def queued_feedback(self, status, variant, *, process=False):
        """Feedback queued at the operational pause, an invocation that applies it, then a resume."""
        state = self.checkpoint(status)
        if variant == 'answered':
            self.answer(state, process=process)
        self.submit('feedback', 'req-feedback', FEEDBACK, process=process)
        flags = ('--max-stage-seconds', '1200') if variant == 'settings write' else ()
        self.assert_held(self.invoke('--resume-paused', *flags, process=process), status, settled=False)
        self.assertTrue(self.view()['stop_reason'].startswith('Queued feedback was applied'),
                        'the feedback itself is applied')
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
                view = self.view()
                self.assertEqual('PAUSED_INTERVENTION', view['status'])
                self.assertNotIn('still holds the run', view['stop_reason'], 'the acknowledged pause is not held')
                self.assert_resumes(self.invoke('--resume-paused', env=STOP_AT_TESTER))

    def test_a_builder_retry_is_no_authority_until_it_is_checked(self):
        # --retry-builder is checked only after queued input is applied. Counted as the held pause's own
        # authority before that, a milestone it then refused (here one that does not exist) had the input
        # applied to a released run, and the next plain --resume-paused launched past the pause.
        def queued_then_retry(status, kind, *, answered, process=False):
            state = self.checkpoint(status)
            if answered:
                self.answer(state, process=process)
            self.submit(kind, f'req-{kind}', FEEDBACK if kind == 'feedback' else '', process=process)
            code, stages, output = self.invoke('--resume-paused', '--retry-builder', 'M-unknown',
                                               process=process, env=STOP_AT_TESTER)
            self.assert_held((code, stages, output), status, settled=False)
            self.assertIn('--retry-builder was not applied', output)
            return self.invoke('--resume-paused', process=process, env=STOP_AT_TESTER)
        self.assert_held(queued_then_retry('PAUSED_TIMEOUT_RECOVERY', 'feedback', answered=False, process=True),
                         'PAUSED_TIMEOUT_RECOVERY')
        for status, (kind, answered) in itertools.product(STATUSES, (('pause', True), ('feedback', False))):
            with self.subTest(status=status, kind=kind, answered=answered):
                result = queued_then_retry(status, kind, answered=answered)
                if kind == 'feedback' and status == 'PAUSED_PLANNING_BUDGET':  # plan feedback is its own authority
                    self.assertTrue(result[1], result[2])
                else:
                    self.assert_adds_nothing(result, status, answered=answered)

    def test_a_settings_write_beside_other_input_leaves_the_request_asked(self):
        # The settings write withdraws the request whose binding it changed, and load_locked asks it again
        # under the new settings. An input the CLI then refuses, or one that only shows the goal, must not
        # leave the pause with no request: each refusal names that request as the way on.
        for status in SAMPLE:
            commands = [('--show-goal',), ('--approve-goal', None)]
            if status != 'PAUSED_PLANNING_BUDGET':  # plan feedback is that pause's own authority
                commands.append(('--feedback', FEEDBACK))
            for command in commands:
                with self.subTest(status=status, command=command[0]):
                    state = self.checkpoint(status)
                    argv = [self.approval_token(state) if arg is None else arg for arg in command]
                    code, stages, output = self.invoke(*argv, '--max-stage-seconds', '1200', env=STOP_AT_TESTER)
                    self.assertEqual([], stages, output)
                    self.assertEqual('operational_exhaustion', (self.view()['needs'] or {}).get('resolver_scope'),
                                     f'the request is asked again under the new settings:\n{output}')
                    self.assertEqual(status, self.pause_in_force(self.saved()), output)

    # Composition with #509 (#586): --resume-paused with an answer or approval dispatches the next stage
    # in the same invocation once it clears a human gate. At an operational pause none clears it.
    def approval_token(self, state):
        return state.get('displayed_goal') or goals.token(state['goal_contract'])

    def edited_goal(self, state):
        body = copy.deepcopy(state['goal_contract']['body'])
        body['constraints'] = [*body.get('constraints', []), 'Keep the greeting on one line']
        path = self.flow.root / 'edited-goal.json'
        path.write_text(json.dumps(body))
        return str(path)

    def test_an_answer_or_approval_with_resume_dispatches_nothing_at_an_operational_pause(self):
        # Neither clears a human gate there, so #586's dispatch is never reached: each is refused when it is
        # checked, sent with the token AutoResolver showed for the operational request.
        approval_refusal = 'Goal approval requires a current AutoResolver-issued plan request'
        cases = [*((status, True) for status in STATUSES), *((status, False) for status in SAMPLE)]
        for status, answered in cases:
            with self.subTest(status=status, answered=answered):
                state = self.checkpoint(status)
                token = human.current(state)['request_token']
                if answered:
                    self.answer(state)
                    state = self.saved()
                answer_refusal = ('Answers require the current --resolver-token shown by AutoResolver' if answered
                                  else 'Use --resolver-response for this operational request; it is not a requirements answer')
                for command, refusal in ((('--answer', 'Q1=CLI', '--resolver-token', token), answer_refusal),
                                         (('--approve-goal', self.approval_token(state)), approval_refusal)):
                    result = self.invoke(*command, '--resume-paused', env=STOP_AT_TESTER)
                    self.assert_held(result, status)
                    self.assertIn(f'Input rejected: {refusal}', result[2])
                    self.assertNotIn('Resumed: dispatching the next stage.', result[2])
                # Neither input used up the pause's own rule: AutoResolver's one re-evaluation of the
                # answered request's information then holds or continues exactly as it does without them.
                self.assert_adds_nothing(self.invoke('--resume-paused', env=STOP_AT_TESTER), status, answered=answered)
                if answered:
                    self.assertEqual(self.after_answer(status)[1], self.information_review())

    def test_the_information_review_still_decides_every_answered_pause_as_581_does(self):
        # The baseline every input above is compared with: --resume-paused after the answered request alone.
        # #581's one re-evaluation continues a stop caused outside the run (INFORMATION_CAUSES) and holds
        # one only an operator control releases, at every pause; holding inputs never changes that.
        for status in STATUSES:
            with self.subTest(status=status):
                expected = ((['sol'], ('admitted', 'continue')) if status in operational_information.INFORMATION_CAUSES
                            else ([], ('held', 'hold')))
                self.assertEqual(expected, self.after_answer(status))

    def test_an_edited_goal_never_releases_an_operational_pause(self):
        # The edit installed a draft for approval in place of the pause, and --approve-goal TOKEN
        # --resume-paused then dispatched the Planner, Builder and Tester in one invocation.
        def edit_then_approve(status, variant, process=False):
            state = self.checkpoint(status)
            if variant != 'unanswered':
                self.answer(state, process=process)
            if variant == 'paused':
                self.submit_pause(process=process)
                self.assert_held(self.invoke(process=process), status, settled=False)
            code, stages, output = self.invoke('--edit-goal', self.edited_goal(state), process=process)
            if status == 'PAUSED_PLANNING_BUDGET':  # a correction is that pause's own authority, like plan feedback
                self.assertEqual((0, 'AWAITING_GOAL_APPROVAL'), (code, self.view()['status']), output)
                return
            self.assertEqual((2, []), (code, stages), output)
            self.assertIn('does not acknowledge', output)
            self.assertEqual(state['goal_contract'], self.saved()['goal_contract'], 'the goal is unchanged')
            self.assert_held(self.invoke('--approve-goal', self.approval_token(self.saved()), '--resume-paused',
                                         process=process, env=STOP_AT_TESTER), status)
        with self.subTest(status='PAUSED_RESOLVER_OPERATIONAL', variant='answered', process=True):
            edit_then_approve('PAUSED_RESOLVER_OPERATIONAL', 'answered', process=True)
        cases = [*((status, 'answered') for status in STATUSES), *((status, 'unanswered') for status in SAMPLE),
                 ('PAUSED_RATE_LIMIT', 'paused')]
        for status, variant in cases:
            with self.subTest(status=status, variant=variant):
                edit_then_approve(status, variant)

    def test_an_approval_with_resume_still_dispatches_past_an_ordinary_pause(self):
        # #509 where no operational pause holds the run: the edited goal is asked for approval, and
        # approving it with --resume-paused dispatches the Planner in the same invocation.
        for status in ('PAUSED_STAGE_ABANDONED', 'PAUSED_PLANNING_BUDGET'):
            with self.subTest(status=status):
                state = self.checkpoint(status) if status == 'PAUSED_PLANNING_BUDGET' else self.stopped(status)
                runner.write_json(self.run_dir / 'state.json', state)
                code, _, output = self.invoke('--edit-goal', self.edited_goal(state))
                self.assertEqual((0, 'AWAITING_GOAL_APPROVAL'), (code, self.view()['status']), output)
                self.invoke('--show-goal')
                code, stages, output = self.invoke('--approve-goal', self.approval_token(self.saved()),
                                                   '--resume-paused', env=STOP_AT_TESTER)
                self.assertEqual('astra_plan', (stages or [None])[0], output)
                self.assertIn('Resumed: dispatching the next stage.', output)

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


class OrdinaryGateTests(unittest.TestCase):
    """#509 (#586) unchanged at the human gates of a new run, where no operational pause holds it."""

    def test_an_answer_and_an_approval_with_resume_dispatch_the_next_stage(self):
        flow = test_subprocess.SubprocessFlow('setUp')
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        probe = flow.root / 'launches.jsonl'
        flow.env.update(STOP_AT_TESTER, AUTOCODE_REGISTRY_LAUNCH_PROBE=str(probe))

        def launched():
            stages = [json.loads(line)['stage'] for line in probe.read_text().splitlines()] if probe.exists() else []
            probe.unlink(missing_ok=True)
            return stages
        flow.launch(['Build greeting', '--no-chat'], 2)
        run, state = flow.saved()
        self.assertEqual('WAITING_FOR_USER', state['status'])
        launched()
        result = flow.launch(['--run-dir', str(run), '--answer', 'Q1=CLI', '--resume-paused', '--no-chat'], 2)
        self.assertIn('Resumed: dispatching the next stage.', result.stdout)
        self.assertEqual(['astra_discovery'], launched(), result.stdout)
        _, state = flow.saved()
        self.assertEqual('AWAITING_GOAL_APPROVAL', state['status'])
        result = flow.launch(['--run-dir', str(run), '--approve-goal', state['displayed_goal'], '--resume-paused',
                              '--no-chat'], 2)
        self.assertIn('Resumed: dispatching the next stage.', result.stdout)
        self.assertEqual('astra_plan', (launched() or [None])[0], result.stdout)


class StatusCoverageTests(unittest.TestCase):
    """The pure records behind these tests are in tests.test_pause_authority, a fast module."""

    def test_the_tested_statuses_are_every_operational_pause(self):
        self.assertEqual(sorted(STATUSES), sorted(pause_authority.OPERATIONAL_PAUSES))


if __name__ == '__main__':
    unittest.main()
