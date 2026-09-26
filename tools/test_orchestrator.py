"""The common driver owns stage selection, persistence and skip semantics."""
import unittest

from tools import autocode_orchestrator as orchestrator


class SharedOrchestrator(unittest.TestCase):
    def test_drives_saved_transitions_for_any_target(self):
        state = {'status': 'RUNNING', 'next_stage': 'astra', 'seen': []}
        persisted = []

        def dispatch(current, stage):
            current['seen'].append(stage)
            return stage.upper()

        def apply(current, stage, result):
            self.assertEqual(stage.upper(), result)
            if stage == 'astra':
                current['next_stage'] = 'terra'
            else:
                current.update(status='COMPLETE', next_stage=None)

        orchestrator.drive(state, dispatch, apply=apply,
                           persist=lambda current: persisted.append((current['status'], current['next_stage'])))
        self.assertEqual(['astra', 'terra'], state['seen'])
        self.assertEqual([('RUNNING', 'terra'), ('COMPLETE', None)], persisted)

    def test_skip_restarts_from_updated_checkpoint_without_applying(self):
        state = {'status': 'RUNNING', 'next_stage': 'terra'}
        calls = []

        def before(current):
            calls.append('before')
            if len(calls) == 1:
                current['next_stage'] = 'sol'
                return orchestrator.SKIP

        def dispatch(current, stage):
            calls.append(stage)
            current.update(status='COMPLETE', next_stage=None)
            return 'done'

        orchestrator.drive(state, dispatch, before=before)
        self.assertEqual(['before', 'before', 'sol'], calls)

    def test_running_state_requires_a_saved_next_stage(self):
        with self.assertRaisesRegex(ValueError, 'no next stage'):
            orchestrator.drive({'status': 'RUNNING'}, lambda *_: None)


if __name__ == '__main__':
    unittest.main()
