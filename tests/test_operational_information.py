"""Corrective information on an operational pause is re-evaluated once by AutoResolver (#486).

Before #486 an accepted `--resolver-response provide_information` was retained and never
evaluated: every resume repeated the same hold. Real CLI processes with the fake provider;
no sleeps, no live models. A launch is observed through the registry launch probe. The control
table itself is tested without processes in test_operational_information_controls.
"""
import json
import subprocess
import unittest

from . import test_subprocess
import autocode as runner
import autocode_support as support


class OperationalInformationCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ('--engine', 'codex')

    def checkpoint(self, status, reason, edit=None, **fields):
        """A run AutoResolver stopped with a published operational_exhaustion request."""
        self.env['AUTOCODE_FIXTURE_MODE'] = 'no-human'
        self.env['AUTOCODE_FIXTURE_QUOTA_STAGE'] = 'astra_review'
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        run, paused = self.saved()
        self.launch(['--run-dir', str(run), '--abandon-stage', runner.attempt_id(paused['active_stage'])], 0)
        self.run, stopped = self.saved()
        if edit:
            edit(stopped)
        stopped.update(status=status, stop_reason=reason, **fields)
        self.assertTrue(runner.resolver_runtime.record_operational_exhaustion(
            runner, stopped, self.run, support.Paused(status, reason)))
        runner.write_json(self.run / 'state.json', stopped)
        view = self.status()
        self.assertEqual('operational_exhaustion', view['needs']['resolver_scope'])
        return view

    def issue_checkpoint(self, edit=None, **fields):
        """#486's run: two operational recoveries, then PAUSED_RESOLVER_OPERATIONAL."""
        recoveries = [{'stage': 'terra', 'instruction': 'workspace paths only'} for _ in range(2)]
        return self.checkpoint('PAUSED_RESOLVER_OPERATIONAL', 'AutoResolver exhausted its operational recoveries',
                               edit, automatic_permission_recoveries=recoveries, **fields)

    def status(self):
        return json.loads(self.launch(['--run-dir', str(self.run), '--status'], 0).stdout)['view']

    def inform(self, view, text, *, token=None, expected=0):
        return self.launch(['--run-dir', str(self.run), '--resolver-request', view['needs']['resolver_request_id'],
                            '--resolver-token', token or view['needs']['resolver_token'],
                            '--resolver-response', 'provide_information', '--resolver-message', text,
                            '--no-chat'], expected)

    def invoke(self, *args, env=None):
        """One CLI process; returns (exit code, whether any provider launched, output)."""
        probe = self.root / f'launch-{len(list(self.root.glob("launch-*")))}.jsonl'
        result = subprocess.run([*self.entry, '--workspace', str(self.project), '--run-dir', str(self.run),
                                 *args, '--no-chat'], cwd=self.root,
                                env={**self.env, 'AUTOCODE_REGISTRY_LAUNCH_PROBE': str(probe), **(env or {})},
                                capture_output=True, text=True, timeout=240)
        return result.returncode, probe.exists(), result.stdout + result.stderr

    def resume(self, *flags, env=None):
        return self.invoke('--resume-paused', *flags, env=env)

    def evaluations(self):
        return sorted((self.run / 'resolver').glob('information-*.json'))

    def test_accepted_information_is_reevaluated_exactly_once(self):
        def explicit_cap(state):
            state['settings']['limits']['max_seconds'] = 7200
            state['settings'].setdefault('budget_origins', {})['max_seconds'] = 'user_explicit'
            state['active_seconds'] = 7300
        view = self.checkpoint('PAUSED_TIME_LIMIT', 'Saved active-time limit reached at stage boundary', explicit_cap)
        result = self.inform(view, 'The operator inspected the run; the approved scope is unchanged')
        self.assertIn('re-evaluates the response once at the next autocode resume', result.stdout)
        view = self.status()
        self.assertEqual('pending', view['information_review']['status'])
        self.assertEqual('--resume-paused', view['needs']['action'])
        # Only an explicit resume consumes it; a plain relaunch neither evaluates nor launches.
        code, launched, output = self.invoke()
        self.assertEqual((2, False), (code, launched), output)
        self.assertIn('re-evaluates it once', output)
        self.assertEqual('pending', self.status()['information_review']['status'])
        self.assertEqual([], self.evaluations())
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        first = self.status()['information_review']
        self.assertEqual(('held', 'hold'), (first['status'], first['decision']))
        # Repeated resumes and a fresh process are the same decision, never a second evaluation.
        for args in (('--resume-paused',), ('resume',), ('--resume-paused',)):
            code, launched, output = self.invoke(*args)
            self.assertEqual((2, False), (code, launched), output)
            self.assertIn(first['reason'], output)
            self.assertEqual(first, self.status()['information_review'])
        self.assertEqual(1, len(self.evaluations()))

    def test_stale_or_replayed_information_launches_nothing(self):
        view = self.issue_checkpoint()
        state_file = self.run / 'state.json'
        before = state_file.read_bytes()
        forged = view['needs']['resolver_token'][:-4] + 'beef'
        self.inform(view, 'The temporary directory is valid', token=forged, expected=2)
        self.assertEqual(before, state_file.read_bytes())
        self.inform(view, 'The temporary directory is valid')
        scheduled = self.status()['information_review']
        self.assertEqual('pending', scheduled['status'])
        # A replay of the same response changes nothing; a different one is refused.
        self.inform(view, 'The temporary directory is valid')
        self.assertEqual(scheduled, self.status()['information_review'])
        self.inform(view, 'Launch the Builder now', expected=2)
        # Stale: the source changed after the response, so it is not evaluated against it.
        (self.project / 'greet.py').write_text('# edited while paused\n')
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        self.assertIn('did not re-evaluate', output)
        self.assertEqual('stale', self.status()['information_review']['status'])
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        self.assertEqual([], self.evaluations())

    def test_information_bound_to_altered_evidence_launches_nothing(self):
        answered = self.issue_checkpoint()
        self.inform(answered, 'The temporary directory is valid')
        for receipt in (self.run / 'resolver').glob('*.json'):
            receipt.write_text(receipt.read_text() + '\n')
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        self.assertIn('evidence changed', output)
        # Only the records changed, not the run: holding on the answered request would be #486
        # again, so AutoResolver asks again for this frontier, launching nothing.
        view = self.status()
        self.assertEqual('answer', view['needs']['kind'], view['needs'])
        self.assertNotEqual(answered['needs']['resolver_request_id'], view['needs']['resolver_request_id'])
        self.assertIn("the request's recorded evidence changed", view['stop_reason'])
        self.assertEqual('stale', self.saved()[1]['resolver']['information_reviews'][
            answered['needs']['resolver_request_id']]['status'])
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        self.assertEqual(view['needs'], self.status()['needs'])
        self.assertEqual([], self.evaluations())
        # The fresh request takes information like any other and schedules its own one evaluation.
        self.inform(view, 'The temporary directory is valid')
        review = self.status()['information_review']
        self.assertEqual((view['needs']['resolver_request_id'], 'pending'), (review['request_id'], review['status']))

    def assert_held_for_a_grant_once(self, checkpoint):
        self.inform(checkpoint, 'The provider was slow; it is healthy again')
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        view = self.status()
        review = view['information_review']
        self.assertEqual(('held', 'hold'), (review['status'], review['decision']))
        self.assertEqual(('resume', '--resume-paused --grant-recovery N'),
                         (view['needs']['kind'], view['needs']['action']))
        self.assertIn('--grant-recovery N', view['stop_reason'])
        # Not repeated: the next resume restates the decision; nothing is evaluated or asked again.
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        self.assertIn(view['stop_reason'], output)
        again = self.status()
        self.assertEqual((view['stop_reason'], review, 'resume'),
                         (again['stop_reason'], again['information_review'], again['needs']['kind']))
        self.assertEqual(1, len(self.evaluations()))
        # The control it names is accepted here and moves the run.
        code, launched, output = self.resume('--grant-recovery', '1')
        self.assertNotIn('Input rejected', output)
        self.assertTrue(launched, output)

    def test_a_decision_without_allowance_stays_paused_with_one_actionable_status(self):
        self.assert_held_for_a_grant_once(self.checkpoint(
            'PAUSED_TIMEOUT_RECOVERY', 'Automatic recovery budget exhausted; no further provider will launch.',
            automatic_recoveries_since_resume=3, consecutive_timeout_recoveries=3,
            automatic_timeout_recoveries=[{'stage': 'terra', 'timeout_reason': 'idle watchdog'} for _ in range(3)]))

    def test_a_spent_allowance_behind_another_stop_names_the_grant_it_accepts(self):
        # AutoResolver runs the admission guard itself and finds the spent automatic-recovery allowance.
        self.assert_held_for_a_grant_once(self.issue_checkpoint(automatic_recoveries_since_resume=3))

    def test_a_spent_report_repair_allowance_is_held_for_the_operator(self):
        def spent_repair(state):
            state['settings']['report_repair'] = {'max_attempts': 2}
            state['pending_report_repair'] = {
                'attempts': 2, 'contract_hash': (state.get('goal_contract') or {}).get('hash'), 'pins': {},
                'original': {'stage': 'terra', 'role': 'terra', 'iteration': state.get('iteration', 1)}}
            state.update(next_stage='terra')
            state.setdefault('sessions', {})['terra'] = 'spent-builder-session'
        view = self.checkpoint('PAUSED_REPORT_REPAIR_LIMIT', 'Bounded report-only repair attempts exhausted', spent_repair)
        self.env.pop('AUTOCODE_FIXTURE_QUOTA_STAGE')
        _, before = self.saved()
        self.inform(view, 'The workspace is fine now')
        # A report that failed validation twice is not a cause outside the run: information cannot
        # buy the fresh Builder attempt the spent repair allowance stopped.
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        view = self.status()
        self.assertEqual(('PAUSED_REPORT_REPAIR_LIMIT', 'held'), (view['status'], view['information_review']['status']))
        self.assertIn('report-only repairs for this attempt are spent', view['stop_reason'])
        _, after = self.saved()
        for key in ('pending_report_repair', 'report_repair_archive', 'sessions'):
            self.assertEqual(before.get(key), after.get(key), key)

    def test_a_held_content_filter_stop_names_a_model_change(self):
        # The content filter refused the Tester's model; it would likely refuse again (#464/#465).
        self.env.update(AUTOCODE_FIXTURE_MODE='no-human', AUTOCODE_FIXTURE_QUOTA_STAGE='sol',
                        AUTOCODE_FIXTURE_QUOTA_MODEL='gpt-5.6-sol',
                        AUTOCODE_FIXTURE_QUOTA_MESSAGE="The response was blocked by the provider's content filter")
        self.launch(['Build greeting', '--chat'], 2, answers='CLI\nyes\n')
        self.run, stopped = self.saved()
        attempt = runner.attempt_id(stopped['active_stage'])
        self.inform(self.status(), 'The refusal was transient')
        code, launched, output = self.resume()
        self.assertEqual((2, False), (code, launched), output)
        view = self.status()
        self.assertEqual('held', view['information_review']['status'])
        self.assertIn(f'--abandon-stage {attempt} ', view['stop_reason'])
        self.assertIn(', then autocode resume --sol-model MODEL', view['stop_reason'])
        self.assertEqual(f'--abandon-stage {attempt} then --resume-paused --sol-model MODEL', view['needs']['action'])
        # The commands it names are accepted and run the Tester on another model.
        self.assertEqual(0, self.invoke('--abandon-stage', attempt)[0])
        code, launched, output = self.invoke('resume', '--sol-model', 'gpt-6-luna')
        self.assertTrue(launched, output)
        self.assertIn('Tester: started; model=gpt-6-luna', output)
        self.assertNotIn('Tester: started; model=gpt-5.6-sol', output)

    def test_an_explicit_control_leaves_pending_information_unevaluated(self):
        self.inform(self.checkpoint(
            'PAUSED_TIMEOUT_RECOVERY', 'Automatic recovery budget exhausted; no further provider will launch.',
            automatic_recoveries_since_resume=3, consecutive_timeout_recoveries=3,
            automatic_timeout_recoveries=[{'stage': 'terra', 'timeout_reason': 'idle watchdog'} for _ in range(3)]),
            'The provider was slow; it is healthy again')
        self.env.pop('AUTOCODE_FIXTURE_QUOTA_STAGE')
        code, launched, output = self.resume('--grant-recovery', '1')
        self.assertTrue(launched, output)
        self.assertEqual('TASK_COMPLETE', self.status()['status'], output)
        self.assertEqual([], self.evaluations())
        # The finished run is reported as finished; the unused information neither holds nor runs it.
        for args in ((), ('--resume-paused',)):
            code, launched, output = self.invoke(*args)
            self.assertEqual((0, False), (code, launched), output)
            self.assertNotIn('re-evaluate', output)
        self.assertEqual([], self.evaluations())
        # The view does not call it pending: no resume will ever evaluate it.
        review = self.status()['information_review']
        self.assertEqual(('superseded', None), (review['status'], review['action']))

    def test_an_admitted_decision_continues_through_the_guarded_admission_path(self):
        def earlier_incident(state):
            state.setdefault('resolver', {})['attempts'] = {'earlier-incident': 3}
        self.inform(self.issue_checkpoint(earlier_incident), 'The workspace temporary directory and interpreter are valid')
        _, answered = self.saved()
        # AutoResolver admits a continuation, but the normal admission checks still run before a
        # launch: a changed local transport stops it with no provider call.
        code, launched, output = self.resume(env={'OPENAI_BASE_URL': 'https://example.invalid'})
        self.assertEqual((2, False), (code, launched), output)
        self.assertIn('continues through the normal admission checks', output)
        view = self.status()
        self.assertEqual('PAUSED_TRANSPORT_CHANGED', view['status'])
        self.assertEqual(('admitted', 'continue'), (view['information_review']['status'],
                                                    view['information_review']['decision']))
        # Admitting information renewed no allowance: the per-incident AutoResolver attempts an
        # operator's own resume would clear still stand.
        _, admitted = self.saved()
        self.assertEqual({'earlier-incident': 3}, admitted['resolver']['attempts'])
        self.assertFalse([event for event in admitted.get('user_events', [])
                          if event.get('kind') in ('resolver_resume_epoch', 'report_repair_resume_epoch')])
        # With the transport restored the run continues on that one decision, never evaluated again.
        self.env.pop('AUTOCODE_FIXTURE_QUOTA_STAGE')
        code, launched, output = self.resume()
        self.assertTrue(launched, output)
        _, resumed = self.saved()
        ran = [row['stage'] for row in resumed['stages'][len(answered['stages']):] if not row.get('runner_owned')]
        self.assertEqual('sol', ran[0], ran)
        self.assertEqual(1, len(self.evaluations()))
        # Information granted nothing: recovery history, counts, limits and approval are those of the stop.
        for key in ('automatic_permission_recoveries', 'automatic_timeout_recoveries',
                    'automatic_recoveries_since_resume', 'goal_contract'):
            self.assertEqual(answered.get(key), resumed.get(key), key)
        self.assertEqual(answered['settings']['limits'], resumed['settings']['limits'])
        self.assertFalse(resumed.get('recovery_grants'))


if __name__ == '__main__':
    unittest.main()
