"""Real CLI unit boundaries with isolated workspaces and offline providers."""
import copy
import json
from pathlib import Path
import sys
import unittest

from . import test_dispatch, test_subprocess, test_planning, test_goals
from . import autopilot as orchestrator
from . import autocode_support as support
from . import autocode as runner


class UnitFlow(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    fixture = test_dispatch.DispatchCliTests.fixture
    new_run_engine_args = ('--engine', 'codex')

    def select(self, name):
        self.entry = [sys.executable, str(Path(__file__).with_name(name + '.py'))]

    def test_separate_units_plan_parallel_build_review_dependency_and_complete(self):
        self.fixture()
        self.select('autoplanner')
        self.launch(['Produce two outputs and combine', '--max-parallel-builders', '2', '--chat'], 0, answers='yes\n')
        run, state = self.saved()
        token = state['goal_contract']['hash']
        self.assertEqual(['astra_discovery'], [r['stage'] for r in state['stages']])
        self.assertEqual('autocode', orchestrator.pending_unit(state))
        self.assertFalse((self.project / 'a.txt').exists())
        self.assertEqual('approved-plan', state['unit_handoffs']['autoplanner']['kind'])

        self.select('autocode_build')
        self.launch(['--run-dir', str(run), '--no-chat'], 0)
        state = self.saved()[1]
        self.assertEqual('sol', state['next_stage'])
        self.assertTrue((self.project / 'a.txt').exists())
        self.assertTrue((self.project / 'b.txt').exists())
        self.assertFalse((self.project / 'combined.txt').exists())
        candidate = json.loads(Path(state['unit_handoffs']['autocode']['path']).read_text())
        self.assertEqual(token, candidate['contract_hash'])
        self.assertEqual(state['implementation']['source_revision'], candidate['source_revision'])
        self.assertEqual(['M1', 'M2'], [w['milestone_id'] for w in state['orchestration_history'][0]['workers']])

        self.select('autoreview')
        self.launch(['--run-dir', str(run), '--no-chat'], 0)
        state = self.saved()[1]
        self.assertEqual('orchestrator', state['next_stage'])
        self.assertFalse((self.project / 'combined.txt').exists())
        self.assertEqual('review-result', state['unit_handoffs']['autoreview']['kind'])

        self.select('autocode_build')
        self.launch(['--run-dir', str(run), '--no-chat'], 0)
        self.assertEqual('sol', self.saved()[1]['next_stage'])
        self.select('autoreview')
        self.launch(['--run-dir', str(run), '--no-chat'], 0)
        state = self.saved()[1]
        self.assertEqual('TASK_COMPLETE', state['status'])
        self.assertEqual(token, state['goal_contract']['hash'])
        self.assertEqual({'autoplanner', 'autocode', 'autoreview'}, set(state['unit_handoffs']))
        for handoff in state['unit_handoffs'].values():
            self.assertEqual(handoff['hash'], support.digest(json.loads(Path(handoff['path']).read_text())))

    def test_build_cannot_create_or_approve_an_unplanned_task(self):
        self.select('autocode_build')
        result = self.launch(['Build anything', '--no-chat'], 2)
        self.assertIn('require an existing --run-dir', result.stderr)
        self.assertFalse((self.project / '.autocode/runs').exists())

    def test_unit_entrypoint_cannot_be_overridden(self):
        self.select('autoplanner')
        result = self.launch(['Idea', '--unit', 'autocode'], 2)
        self.assertIn('runs only autoplanner', result.stderr)


class JointPlannerUnit(unittest.TestCase):
    setUp = UnitFlow.setUp
    launch = UnitFlow.launch
    saved = UnitFlow.saved
    select = UnitFlow.select
    new_run_engine_args = ()
    prepare = test_planning.JointFlow.prepare

    def test_joint_planner_stops_after_approval_without_a_builder(self):
        self.prepare()
        self.select('autoplanner')
        self.launch(['Build greeting', '--chat'], 0, answers='CLI\nyes\n')
        run, state = self.saved()
        self.assertEqual('approved', state['goal_contract']['approval_status'])
        self.assertFalse(any(r['stage'] in ('terra', 'sol') for r in state['stages']))
        self.assertTrue(all(orchestrator.unit_for(r['stage']) == 'autoplanner' for r in state['stages']))
        self.assertEqual('autocode', orchestrator.pending_unit(state))
        plan = json.loads(Path(state['unit_handoffs']['autoplanner']['path']).read_text())
        self.assertEqual(state['goal_contract'], plan['contract'])


class ResolverFlow(unittest.TestCase):
    setUp = UnitFlow.setUp
    launch = UnitFlow.launch
    saved = UnitFlow.saved
    select = UnitFlow.select
    new_run_engine_args = ('--engine', 'codex')

    def test_autopilot_controls_all_four_units_through_repair_and_completion(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'rework'
        self.select('autopilot')
        self.launch(['Build greeting', '--chat'], 0, answers='CLI\nyes\n')
        run, state = self.saved()
        self.assertEqual('TASK_COMPLETE', state['status'])
        stages = [r['stage'] for r in state['stages']]
        self.assertEqual(['astra_discovery', 'astra_discovery', 'astra_plan', 'terra',
                          'sol', 'astra_review', 'astra_resolve', 'terra', 'sol', 'astra_review'], stages)
        self.assertEqual(set(orchestrator.UNITS), set(state['unit_handoffs']))
        self.assertEqual('PASS', state['validation']['verdict'])
        self.assertEqual(1, len(state['resolution_history']))
        self.assertEqual(1, sum(e['kind'] == 'goal_approval' for e in state['user_events']))

    def rejected_build(self):
        self.env['AUTOCODE_FIXTURE_MODE'] = 'rework'
        self.select('autoplanner')
        self.launch(['Build greeting', '--chat'], 0, answers='CLI\nyes\n')
        run, _ = self.saved()
        args = ['--run-dir', str(run), '--no-chat']
        self.select('autocode_build')
        self.launch(args, 0)
        self.select('autoreview')
        self.launch(args, 0)
        self.assertEqual('astra_resolve', self.saved()[1]['next_stage'])
        return run, args

    def test_resolution_is_read_only_and_hands_back_to_build_and_independent_review(self):
        run, args = self.rejected_build()
        before = support.snapshot(self.project)['revision']
        original = self.saved()[1]
        self.select('autoresolver')
        self.launch(args, 0)
        state = self.saved()[1]
        self.assertEqual(before, support.snapshot(self.project)['revision'])
        self.assertEqual('autocode', orchestrator.pending_unit(state))
        self.assertEqual(original['goal_contract'], state['goal_contract'])
        self.assertEqual('FAIL', state['validation']['verdict'])
        self.assertNotEqual(state['sessions']['resolver'], state['sessions']['astra'])
        self.assertNotEqual(state['sessions']['resolver'], state['sessions']['completion'])
        artifact = state['unit_handoffs']['autoresolver']
        plan = json.loads(Path(artifact['path']).read_text())
        self.assertEqual('repair-plan', plan['kind'])
        self.assertEqual(1, len(plan['tasks']))
        self.assertEqual([], plan['tasks'][0]['depends_on'])
        self.assertEqual(state['current_task'], {k: v for k, v in plan['tasks'][0].items() if k != 'depends_on'})
        self.assertTrue(plan['tasks'][0]['validation_plan'])
        self.assertEqual(artifact['hash'], support.digest(plan))
        self.select('autocode_build')
        self.launch(args, 0)
        self.select('autoreview')
        self.launch(args, 0)
        state = self.saved()[1]
        self.assertEqual('TASK_COMPLETE', state['status'])
        self.assertEqual(2, sum(r['stage'] == 'sol' for r in state['stages']))

    def test_changed_source_pauses_resolver_without_launching_a_writer(self):
        run, args = self.rejected_build()
        (self.project / 'greet.py').write_text('# Changed after review\n')
        count = len(self.saved()[1]['stages'])
        self.select('autoresolver')
        self.launch(args, 2)
        state = self.saved()[1]
        self.assertEqual('PAUSED_STALE_HANDOFF', state['status'])
        self.assertEqual(count, len(state['stages']))

    def test_changed_evidence_pauses_resolution(self):
        run, args = self.rejected_build()
        evidence = next(iter(self.saved()[1]['resolution_request']['evidence_hashes']))
        Path(evidence).write_text('changed evidence')
        self.select('autoresolver')
        self.launch(args, 2)
        self.assertEqual('PAUSED_STALE_HANDOFF', self.saved()[1]['status'])


class UnitRouting(unittest.TestCase):
    def test_pending_repair_is_owned_by_its_original_unit(self):
        state = {'next_stage': 'terra', 'pending_report_repair': {'original': {'stage': 'sol'}}}
        self.assertEqual('autoreview', orchestrator.pending_unit(state))
        with self.assertRaisesRegex(ValueError, 'No unit owns'):
            orchestrator.unit_for('unknown-stage')


class ResolverSafety(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp
    draft = test_goals.GoalTests.draft
    approve = test_goals.GoalTests.approve
    decision = test_goals.GoalTests.decision

    def ready(self):
        self.approve()
        runner.apply_result(self.state, 'astra_plan', self.decision(), {'output': 'plan'}, self.root, self.run)
        value = self.decision('REWORK')
        output = self.run / 'review.json'
        output.write_text(json.dumps(value))
        record = {'output': str(output), 'source_revision': support.snapshot(self.root)['revision']}
        runner.apply_result(self.state, 'astra_review', value, record, self.root, self.run)
        value['diagnosis'] = 'Missing input validation'
        return value, record

    def rejected(self, value, record):
        before = copy.deepcopy(self.state)
        with self.assertRaises((ValueError, support.Paused)):
            runner.apply_result(self.state, 'astra_resolve', value, record, self.root, self.run)
        self.assertEqual(before, self.state)

    def test_cannot_self_approve_completion(self):
        value, record = self.ready()
        value['status'] = 'COMPLETE'
        self.rejected(value, record)

    def test_cannot_change_approved_criteria(self):
        value, record = self.ready()
        value['acceptance_criteria'][0]['description'] = 'Changed scope'
        # Change the actual criterion definition key, not a report-only field.
        value['acceptance_criteria'][0]['id'] = 'UNAPPROVED'
        self.rejected(value, record)

    def test_requires_diagnosis(self):
        value, record = self.ready()
        value['diagnosis'] = ''
        self.rejected(value, record)

    def test_source_writes_are_rejected(self):
        value, record = self.ready()
        record['changed_files'] = ['greet.py']
        self.rejected(value, record)


class UnitHandoffs(unittest.TestCase):
    setUp = test_goals.GoalTests.setUp
    draft = test_goals.GoalTests.draft
    approve = test_goals.GoalTests.approve

    def test_handoffs_are_immutable_and_cannot_authorize_a_draft(self):
        self.approve()
        exports = orchestrator.publish_handoffs(self.state, self.run)
        path = Path(exports['autoplanner']['path'])
        plan = json.loads(path.read_text())
        self.assertEqual(self.state['goal_contract'], plan['contract'])
        path.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'handoff was modified'):
            orchestrator.publish_handoffs(self.state, self.run)
        self.state['goal_contract']['approval_status'] = 'draft'
        self.assertEqual({}, orchestrator.publish_handoffs(self.state, self.run))

    def test_handoff_publication_is_idempotent(self):
        self.approve()
        exports = orchestrator.publish_handoffs(self.state, self.run)
        path = Path(exports['autoplanner']['path'])
        timestamp = path.stat().st_mtime_ns
        self.assertEqual(exports, orchestrator.publish_handoffs(self.state, self.run))
        self.assertEqual(timestamp, path.stat().st_mtime_ns)


if __name__ == '__main__':
    unittest.main()
