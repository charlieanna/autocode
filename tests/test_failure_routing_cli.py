"""Cause-aware Builder routing through real public CLI and provider receipts.

No private state mutation, policy mocks, or model spend. Set BUILD_AUDIT_ARTIFACTS
to retain every first failure, CLI response, source tree and provider handoff.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import unittest

from tests import test_build_blackbox as build_fixture


class FailureRoutingCLI(unittest.TestCase):
    # Reuse only hermetic process setup, not the state-based assertions or tests.
    command = build_fixture.BuildBlackbox.command

    def setUp(self):
        build_fixture.BuildBlackbox.setUp(self)
        shutil.copy2(Path(__file__).with_name('failure_routing_provider.py'), self.root / 'bin/codex')
        (self.root / 'bin/codex').chmod(0o755)
        self.env.update(FAILURE_ROUTING_PROVIDER=str(self.source / 'blackbox_build_provider.py'),
                        FAILURE_ROUTING_TRACE=str(self.root / 'routing.jsonl'))
        for key in ('BUILD_AUDIT_LIVE_CODEX', 'REVIEW_AUDIT_LIVE_CODEX'):
            self.env.pop(key, None)

    def call(self, unit, args, codes=(0,)):
        command = self.command(unit, args)
        self.counter += 1
        artifact = self.root / f'cli-{self.counter}.json'
        try:
            result = subprocess.run(command, cwd=self.root, env=self.env, capture_output=True,
                                    text=True, timeout=180)
        except subprocess.TimeoutExpired as error:
            text = lambda value: value.decode(errors='replace') if isinstance(value, bytes) else value or ''
            artifact.write_text(json.dumps(dict(command=command, timed_out=True,
                stdout=text(error.stdout), stderr=text(error.stderr)), indent=2))
            raise
        artifact.write_text(json.dumps(dict(
            command=command, returncode=result.returncode, stdout=result.stdout, stderr=result.stderr), indent=2))
        self.assertIn(result.returncode, codes, result.stdout + result.stderr)
        return result

    def status(self):
        return json.loads(self.call('autocode', ['--run-dir', str(self.run), '--status', '--inspect-evidence']).stdout)

    def trace(self, stage=None, event='call'):
        path = self.root / 'routing.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
        return [row for row in rows if row['event'] == event and (stage is None or row['stage'] == stage)]

    def call_order(self, stage=None):
        return [(row['stage'], row['model'], Path(row['argv'][row['argv'].index('-o') + 1]).name)
                for row in self.trace(stage)]

    def seed(self, *, existing=None, goal='Deliver a CLI that prints hello', check=None,
             payload="print('hello')\n", objective='CLI prints hello', complete=True,
             deliverable='main.py', criterion=None, verification=None):
        if existing is not None:
            (self.project / 'main.py').write_text(existing)
            subprocess.run(['git', 'add', 'main.py'], cwd=self.project, check=True, capture_output=True)
            subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=f@example.test',
                            'commit', '-qm', 'Seed original implementation'], cwd=self.project,
                           check=True, capture_output=True)
        check = check or "import subprocess,sys; assert subprocess.check_output([sys.executable,'main.py'],text=True) == 'hello\\n'"
        spec = build_fixture.plan([([], objective, [deliverable])],
                    {'M1': {deliverable: payload}}, {'M1': check}, goal)
        spec['complete_product'] = complete
        if criterion is not None:
            spec['contract']['acceptance_criteria'][0]['criterion'] = criterion
        if verification is not None:
            spec['contract']['acceptance_criteria'][0]['verification_method'] = verification
        (self.root / 'plan.json').write_text(json.dumps(spec))
        self.call('autoplanner', [goal, '--engine', 'codex', '--in-place',
            '--terra-model', 'gpt-6-luna', '--sol-model', 'gpt-5.6-sol',
            '--completion-model', 'gpt-5.6-sol', '--builder-strong-model', 'gpt-5.4',
            '--max-parallel-builders', '1', '--no-chat'], (2,))
        self.run = next((self.project / '.autocode/runs').iterdir())
        initial = self.status()
        self.approved_token = initial['contract_token']
        self.call('autoplanner', ['--run-dir', str(self.run), '--approve-goal',
                                  self.approved_token, '--no-chat'])
        self.initial_routes = self.status()['view']['routes']
        self.assertEqual([], self.trace('terra'))

    def drive(self, *extra, codes=(0, 2)):
        return self.call('autocode', ['--run-dir', str(self.run), '--no-chat', *extra], codes)

    def assert_no_strong(self):
        self.assertTrue(all(row['model'] == 'gpt-6-luna' for row in self.trace('terra')), self.trace('terra'))
        routes = self.status()['view']['routes']
        self.assertEqual(self.initial_routes, {role: routes[role] for role in self.initial_routes},
                         'Nonexecution causes must not escalate Builder or mutate its independent checker routes')

    def assert_bound_read_only(self):
        calls = [row for row in self.trace('investigate_stuck') if row['handoff'].get('builder_failure')]
        self.assertEqual(1, len(calls), self.call_order('investigate_stuck'))
        argv = calls[0]['argv']
        self.assertEqual('read-only', argv[argv.index('--sandbox') + 1])
        packet = calls[0]['handoff']['builder_failure']
        result = self.trace('investigate_stuck', 'diagnosis')[0]['report']
        self.assertEqual(packet['failure_id'], result['failure_id'])
        self.assertTrue(result['evidence_refs'])
        self.assertTrue(set(result['evidence_refs']) <= set(packet['evidence_refs']))
        self.assertEqual(calls[0]['handoff']['current_task']['id'], packet['binding']['task_id'])
        self.assertTrue(packet['binding']['source_revision'])
        self.assertEqual(self.approved_token, self.status()['contract_token'])

    def wait_for(self, predicate, process, label):
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline and process.poll() is None:
            value = predicate()
            if value:
                return value
            time.sleep(.02)
        self.fail(f'{label} was not reached; controller exit={process.poll()}')

    def test_uncertain_no_source_change_investigates_before_spending_and_refuses(self):
        self.seed()
        self.env.update(BUILD_AUDIT_FAULT='no_change', FAILURE_ROUTING_DIAGNOSIS='unknown')
        self.drive(codes=(2,))
        status = self.status()
        self.assertEqual('PAUSED_BUILDER_CLASSIFICATION', status['status'])
        self.assertIn('without source changes', status['view']['stop_reason'])
        self.assertEqual(['terra', 'investigate_stuck'],
                         [row['stage'] for row in self.trace() if row['stage'] in ('terra', 'investigate_stuck')])
        self.assert_bound_read_only()
        self.assert_no_strong()
        self.assertFalse((self.project / 'main.py').exists())
        before = self.call_order()
        self.drive('--resume-paused', codes=(2,))
        self.assertEqual(before, self.call_order(), 'Resume must not renew an investigation or Builder allowance')

    def test_operational_provider_exit_retains_first_cause_without_strong_builder(self):
        self.seed()
        self.env['BUILD_AUDIT_FAULT'] = 'crash'
        self.drive(codes=(2,))
        status = self.status()
        self.assertEqual('WAITING_FOR_USER', status['status'])
        self.assertIn(status['human_escalation']['scope'], ('operational_recovery', 'operational_exhaustion'))
        self.assertNotEqual('TASK_COMPLETE', status['status'])
        self.assertEqual(1, len(self.trace('terra')))
        self.assertEqual([], self.trace('investigate_stuck'))
        self.assert_no_strong()
        self.assertFalse((self.project / 'main.py').exists())
        self.assertIn('terra exited 9', status['view']['stop_reason'])
        before = self.call_order('terra')
        self.drive('--resume-paused', codes=(2,))
        self.assertEqual(before, self.call_order('terra'))
        self.assert_no_strong()

    def test_permission_request_never_self_grants_or_escalates(self):
        self.seed()
        self.env['BUILD_AUDIT_FAULT'] = 'permission'
        self.drive(codes=(2,))
        status = self.status()
        self.assertEqual('WAITING_FOR_USER', status['status'])
        self.assertTrue(status['human_request_authorized'])
        self.assertEqual('permission', status['human_escalation']['scope'])
        self.assertEqual('permission', status['user_request']['kind'])
        self.assertEqual('Required external access is unavailable', status['user_request']['discovered'])
        self.assertEqual('Replan or stop?', status['pending_questions'][0]['question'])
        self.assertEqual(1, len(self.trace('terra')))
        self.assert_no_strong()
        self.assertFalse((self.project / 'main.py').exists())
        before = self.call_order()
        self.drive('--resume-paused', codes=(2,))
        resumed = self.status()
        self.assertEqual(before, self.call_order())
        self.assertEqual(status['pending_questions'], resumed['pending_questions'])
        self.assertEqual(status['human_escalation'], resumed['human_escalation'])
        self.assertEqual(self.approved_token, resumed['contract_token'])
        self.assert_no_strong()

    def test_stale_diagnosis_cannot_authorize_an_execution_attempt(self):
        self.seed()
        self.env.update(BUILD_AUDIT_FAULT='no_change', FAILURE_ROUTING_DIAGNOSIS='stale')
        self.drive(codes=(2,))
        status = self.status()
        self.assertEqual('PAUSED_BUILDER_CLASSIFICATION', status['status'])
        self.assertIn('same pinned failure identity', status['view']['stop_reason'])
        draft = self.trace('investigate_stuck', 'diagnosis')[0]['report']
        packet = self.trace('investigate_stuck')[0]['handoff']['builder_failure']
        self.assertEqual(('execution', 'retry'), (draft['failure_class'], draft['recommendation']))
        self.assertNotEqual(packet['failure_id'], draft['failure_id'])
        self.assertTrue(set(draft['evidence_refs']) <= set(packet['evidence_refs']))
        self.assertTrue(draft['probe'])
        self.assertEqual(1, len(self.trace('terra')))
        self.assert_no_strong()
        self.assertFalse((self.project / 'main.py').exists())
        before = self.call_order()
        self.drive('--resume-paused', codes=(2,))
        self.assertEqual(before, self.call_order(), 'A stale classification must not mint another incident')

    def test_fulfilled_wrong_plan_requires_fresh_reassessment_not_stronger_builder(self):
        goodbye = "print('goodbye')\n"
        self.seed(existing=goodbye, payload=goodbye, objective='CLI prints goodbye', complete=False,
            check="import subprocess,sys; assert subprocess.check_output([sys.executable,'main.py'],text=True) == 'goodbye\\n'")
        self.env['FAILURE_ROUTING_DIAGNOSIS'] = 'plan'
        self.drive(codes=(2,))
        status = self.status()
        calls = self.trace()
        stages = [row['stage'] for row in calls]
        self.assertLess(stages.index('investigate_stuck'), stages.index('astra_review'))
        self.assertEqual(1, len(self.trace('terra')), 'An unchanged CONTINUE is not an authorized replan')
        self.assert_no_strong()
        self.assert_bound_read_only()
        self.assertEqual(self.approved_token, status['contract_token'])
        self.assertNotEqual('TASK_COMPLETE', status['status'])
        self.assertEqual(goodbye, (self.project / 'main.py').read_text())
        self.assertIn('REWORK', json.dumps(status))
        self.assertIn('changed', json.dumps(status).lower())

    def test_unchanged_rework_before_independent_validation_is_not_a_replan(self):
        goodbye = "print('goodbye')\n"
        self.seed(existing=goodbye, payload=goodbye, objective='CLI prints goodbye', complete=False,
            check="import subprocess,sys; assert subprocess.check_output([sys.executable,'main.py'],text=True) == 'goodbye\\n'")
        self.env.update(FAILURE_ROUTING_DIAGNOSIS='plan', FAILURE_ROUTING_REWORK='unchanged')
        self.drive(codes=(2,))
        status = self.status()
        calls = self.trace()
        reviewer = next(row for row in calls if row['stage'] == 'astra_review')
        self.assertFalse(reviewer['handoff'].get('validation'), 'Control must reach REWORK before any independent validation')
        stages = [row['stage'] for row in calls]
        self.assertLess(stages.index('investigate_stuck'), stages.index('astra_review'))
        if 'sol' in stages:
            self.assertLess(stages.index('astra_review'), stages.index('sol'))
        unchanged = self.trace('astra_review', 'unchanged_rework')[0]['report']
        self.assertEqual('REWORK', unchanged['status'])
        self.assertEqual(reviewer['handoff']['current_task']['objective'], unchanged['next_objective'])
        self.assertEqual(reviewer['handoff']['current_task']['requirements'], unchanged['next_task']['requirements'])
        self.assertEqual(reviewer['handoff']['current_task']['validation_plan'], unchanged['next_task']['validation_plan'])
        self.assertTrue(unchanged['evidence'])
        self.assertEqual(1, len(self.trace('terra')), 'Nonempty evidence is not a materially changed approach')
        self.assert_no_strong()
        self.assertNotEqual('TASK_COMPLETE', status['status'])
        self.assertEqual(self.approved_token, status['contract_token'])
        self.assertIn('changed approach', json.dumps(status).lower())
        self.assertEqual(goodbye, (self.project / 'main.py').read_text())

    def test_execution_diagnosis_needing_user_retains_operator_pause(self):
        self.seed()
        self.env.update(BUILD_AUDIT_FAULT='no_change', FAILURE_ROUTING_DIAGNOSIS='execution',
                        FAILURE_ROUTING_NEEDS_USER='1')
        self.drive(codes=(2,))
        status = self.status()
        self.assertNotEqual('TASK_COMPLETE', status['status'])
        self.assertIn('Should this run stop or continue', json.dumps(status))
        self.assert_bound_read_only()
        self.assertEqual(1, len(self.trace('terra')))
        self.assert_no_strong()
        self.assertFalse((self.project / 'main.py').exists())
        diagnosis = self.trace('investigate_stuck', 'diagnosis')[0]['report']
        self.assertEqual(('execution', 'pause', 'needs_user'),
            (diagnosis['failure_class'], diagnosis['recommendation'], diagnosis['cause']))
        before = self.call_order()
        self.drive('--resume-paused', codes=(2,))
        self.assertEqual(before, self.call_order())

    def test_wrong_approach_changed_rework_keeps_approval_and_runs_fresh_checks(self):
        goodbye = "print('goodbye')\n"
        self.seed(existing=goodbye, payload=goodbye, objective='CLI prints goodbye', complete=False,
            criterion='CLI prints hello to fulfill the original goal',
            verification="import subprocess,sys; assert subprocess.check_output([sys.executable,'main.py'],text=True) == 'hello\\n'",
            check="import subprocess,sys; assert subprocess.check_output([sys.executable,'main.py'],text=True) == 'goodbye\\n'")
        original = subprocess.run([sys.executable, 'main.py'], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(0, original.returncode)
        self.assertEqual('goodbye\n', original.stdout, 'The initial plan letter must demonstrably miss the original hello goal')
        self.env.update(FAILURE_ROUTING_DIAGNOSIS='plan', FAILURE_ROUTING_REWORK='correct')
        self.drive(codes=(0,))
        status = self.status()
        self.assertEqual('TASK_COMPLETE', status['status'])
        self.assertTrue(status['completion_current'])
        self.assertEqual(self.approved_token, status['contract_token'])
        self.assertEqual(2, len(self.trace('terra')))
        self.assert_no_strong()
        self.assert_bound_read_only()
        calls = self.trace()
        stages = [row['stage'] for row in calls]
        self.assertLess(stages.index('investigate_stuck'), stages.index('astra_review'))
        self.assertLess(stages.index('astra_review'), stages.index('astra_resolve'))
        self.assertLess(stages.index('astra_resolve'), stages.index('sol'))
        first, corrected = self.trace('terra')
        self.assertNotEqual(first['handoff']['current_task']['id'], corrected['handoff']['current_task']['id'])
        self.assertNotEqual(first['handoff']['current_task']['requirements'], corrected['handoff']['current_task']['requirements'])
        self.assertNotEqual(first['handoff']['current_task']['validation_plan'], corrected['handoff']['current_task']['validation_plan'])
        for tester in self.trace('sol'):
            self.assertIn('hello', tester['handoff']['current_task']['validation_plan'][0])
        oracle = subprocess.run([sys.executable, 'main.py'], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(0, oracle.returncode)
        self.assertEqual('hello\n', oracle.stdout)

    def test_ignored_approved_document_uses_source_snapshot_without_builder_escalation(self):
        (self.project / '.gitignore').write_text('docs/\n')
        subprocess.run(['git', 'add', '.gitignore'], cwd=self.project, check=True, capture_output=True)
        subprocess.run(['git', '-c', 'user.name=Fixture', '-c', 'user.email=f@example.test',
                        'commit', '-qm', 'Ignore delivered documentation'], cwd=self.project,
                       check=True, capture_output=True)
        document = '# Deployment checklist\n- Check the service.\n'
        self.seed(goal='Deliver the specified deployment checklist', objective='Write the deployment checklist',
            deliverable='docs/checklist.md', payload=document,
            check="from pathlib import Path; assert Path('docs/checklist.md').read_text() == " + repr(document))
        index = (self.project / '.git/index').read_bytes()
        self.drive(codes=(0,))
        status = self.status()
        self.assertEqual('TASK_COMPLETE', status['status'])
        self.assertTrue(status['completion_current'])
        self.assertEqual(1, len(self.trace('terra')))
        self.assertEqual([], self.trace('investigate_stuck'))
        self.assert_no_strong()
        self.assertEqual(document, (self.project / 'docs/checklist.md').read_text())
        self.assertEqual('docs/\n', (self.project / '.gitignore').read_text())
        self.assertEqual(index, (self.project / '.git/index').read_bytes())
        self.assertEqual(0, subprocess.run(['git', 'check-ignore', '-q', 'docs/checklist.md'],
                                         cwd=self.project).returncode)
        (self.project / 'docs/checklist.md').write_text('# Changed after completion\n')
        self.assertFalse(self.status()['completion_current'])

    def test_real_independent_failure_keeps_bounded_execution_recovery(self):
        self.seed(payload="print('wrong')\n")
        self.env['BUILD_AUDIT_FAULT'] = 'validation_fails'
        self.drive('--max-iterations', '6', codes=(2,))
        status = self.status()
        builders = self.trace('terra')
        self.assertEqual(['gpt-6-luna', 'gpt-6-luna'], [row['model'] for row in builders])
        self.assertEqual('RESOLVER_PENDING', status['status'])
        self.assertIn('No causal progress for the same incident', status['view']['stop_reason'])
        before = self.call_order()
        self.drive('--resume-paused', codes=(2,))
        self.assertEqual(before, self.call_order(), 'An unchanged incident cannot mint an automatic retry')
        # Existing scoped operational admission authorizes diagnosis first, then
        # the retained correction. Neither flag increases the Builder lane cap.
        self.drive('--resume-paused', '--retry-failed-stage', codes=(2,))
        self.assertEqual(2, len(self.trace('terra')), self.call_order('terra'))
        self.drive('--resume-paused', '--retry-failed-stage', codes=(2,))
        status = self.status()
        self.assertEqual(['gpt-6-luna', 'gpt-6-luna', 'gpt-5.4'],
                         [row['model'] for row in self.trace('terra')])
        self.assertNotEqual('TASK_COMPLETE', status['status'])
        self.assertEqual(self.approved_token, status['contract_token'])
        self.assertGreaterEqual(len(self.trace('sol')), 2)
        final_builders = self.call_order('terra')
        final_source = (self.project / 'main.py').read_text()
        self.drive('--resume-paused', codes=(2,))
        self.assertEqual(final_builders, self.call_order('terra'), 'A retained pause must not renew the spent strong slot')
        self.assertEqual(final_source, (self.project / 'main.py').read_text())
        checked = subprocess.run([sys.executable, 'main.py'], cwd=self.project, capture_output=True, text=True)
        self.assertEqual(0, checked.returncode)
        self.assertEqual('wrong\n', checked.stdout)
        receipts = []
        for path in self.run.glob('iterations/**/*.jsonl'):
            for line in path.read_text().splitlines():
                event = json.loads(line)
                item = event.get('item') or {}
                if item.get('type') == 'command_execution' and item.get('exit_code') == 1:
                    receipts.append(item)
        self.assertTrue(any('AssertionError' in row.get('aggregated_output', '') for row in receipts), receipts)

    def test_source_edit_during_investigation_rejects_stale_classification(self):
        self.seed()
        self.env.update(BUILD_AUDIT_FAULT='no_change', FAILURE_ROUTING_DIAGNOSIS='execution',
                        FAILURE_ROUTING_HOLD='1')
        process = subprocess.Popen(self.command('autocode', ['--run-dir', str(self.run), '--no-chat']),
            cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.wait_for(lambda: (self.root / 'investigator-ready').exists(), process, 'Investigator source barrier')
            user_source = "print('user change must survive')\n"
            (self.project / 'main.py').write_text(user_source)
            (self.root / 'release-investigator').touch()
            stdout, stderr = process.communicate(timeout=45)
            (self.root / 'stale-source-cli.log').write_text(stdout + stderr)
            self.assertEqual(2, process.returncode, stdout + stderr)
        finally:
            if process.poll() is None:
                process.kill()
            if not (self.root / 'stale-source-cli.log').exists():
                stdout, stderr = process.communicate(timeout=10)
                (self.root / 'stale-source-cli.log').write_text(stdout + stderr)
        status = self.status()
        self.assertNotEqual('TASK_COMPLETE', status['status'])
        self.assertEqual('PAUSED_STALE_VALIDATION', status['status'])
        self.assertEqual('Repository changed during read-only review; preserve result and revalidate',
                         status['view']['stop_reason'])
        self.assertEqual(1, len(self.trace('terra')))
        self.assertEqual(1, len(self.trace('investigate_stuck')))
        self.assert_no_strong()
        self.assertEqual(user_source, (self.project / 'main.py').read_text())

    def test_crash_resume_retains_pending_investigation_without_replay_or_renewal(self):
        self.seed()
        hook = self.root / 'hook'
        hook.mkdir()
        shutil.copy2(Path(__file__).with_name('failure_routing_sitecustomize.py'), hook / 'sitecustomize.py')
        boundary = self.root / 'published-investigator'
        self.env.update(BUILD_AUDIT_FAULT='no_change', FAILURE_ROUTING_DIAGNOSIS='unknown',
            FAILURE_ROUTING_BOUNDARY=str(boundary), PYTHONPATH=str(hook))
        process = subprocess.Popen(self.command('autocode', ['--run-dir', str(self.run), '--no-chat']),
            cwd=self.root, env=self.env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            self.wait_for(boundary.exists, process, 'Saved Investigator attempt publication')
            self.assertEqual(process.pid, int(boundary.read_text()), 'Barrier must hold this test-owned controller')
            def stopped():
                for path in self.run.glob('iterations/**/stuck-investigation-01.supervision.json'):
                    receipt = json.loads(path.read_text())
                    if (receipt.get('phase') == 'stopped'
                            and receipt.get('cause') in ('provider_stopped', 'controller_finished')
                            and not receipt.get('cleanup_error')):
                        return receipt
            receipt = self.wait_for(stopped, process, 'Normal Investigator provider supervision receipt')
            self.assertTrue(receipt['provider']['birth_identity'])
            self.assertEqual(self.trace('investigate_stuck')[0]['pid'], receipt['provider']['pid'])
            (self.root / 'observed-investigator-supervision.json').write_text(json.dumps(receipt, indent=2))
            process.kill()
            stdout, stderr = process.communicate(timeout=10)
            (self.root / 'crashed-investigator-cli.log').write_text(stdout + stderr)
        finally:
            if process.poll() is None:
                process.kill()
            if not (self.root / 'crashed-investigator-cli.log').exists():
                stdout, stderr = process.communicate(timeout=10)
                (self.root / 'crashed-investigator-cli.log').write_text(stdout + stderr)
        self.env.pop('FAILURE_ROUTING_BOUNDARY')
        self.drive('--resume-paused', codes=(2,))
        status = self.status()
        self.assertEqual('WAITING_FOR_USER', status['status'])
        self.assertIn(status['human_escalation']['scope'], ('operational_recovery', 'operational_exhaustion'))
        self.assertIn('lacks a verified uninterrupted result', status['view']['stop_reason'])
        self.assertEqual(1, len(self.trace('terra')))
        self.assert_bound_read_only()
        before = self.call_order()
        self.drive('--resume-paused', codes=(2,))
        self.assertEqual(before, self.call_order())
        self.assert_no_strong()
        self.assertFalse((self.project / 'main.py').exists())


if __name__ == '__main__':
    unittest.main()
