"""The packet must not restate material it already carries in full elsewhere."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

import autocode_context as context


class ValidationReplayTests(unittest.TestCase):
    def test_bulky_replay_archived_with_verdict_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'state.json'
            replay = {'verdict': 'PASS', 'source_revision': 'r1',
                      'checks': [{'command': 'x' * 300, 'output': 'scratch.log', 'output_sha256': 'h'}
                                 for _ in range(8)]}
            base = {'validation': {'verdict': 'PASS', 'checks': [{'command': 'x', 'exit_code': 0}],
                                   'check_replay': replay}}
            original = copy.deepcopy(base)
            smaller, moved = context.compact(base, path)
            self.assertEqual(original, base)
            self.assertEqual(['validation_check_replay'], moved)
            self.assertEqual({'verdict': 'PASS', 'source_revision': 'r1'},
                             smaller['validation']['check_replay'])
            self.assertEqual(base['validation']['checks'], smaller['validation']['checks'])
            archived = json.loads(Path(smaller['context_artifact']['path']).read_text())
            self.assertEqual(replay, archived['validation_check_replay'])
            self.assertEqual(smaller, context.compact(base, path)[0])

    def test_small_replay_stays_inline(self):
        base = {'validation': {'check_replay': {'verdict': 'PASS', 'checks': [{'command': 'x'}]}}}
        self.assertEqual((base, []), context.compact(base, '/nonexistent/state.json'))


class ContractCriteriaTests(unittest.TestCase):
    def test_restated_criteria_dropped_from_contract_body(self):
        base = {'acceptance_criteria': [{'id': 'AC1', 'criterion': 'exact'}, {'id': 'AC2', 'criterion': 'other'}],
                'goal_contract': {'revision': 3,
                                  'body': {'acceptance_criteria': [{'criterion': 'exact'},
                                                                   {'criterion': 'other'}],
                                           'constraints': ['keep']}}}
        original = copy.deepcopy(base)
        smaller, moved = context.compact(base, '/nonexistent/state.json')
        self.assertEqual(original, base)
        self.assertEqual([], moved)
        self.assertNotIn('acceptance_criteria', smaller['goal_contract']['body'])
        self.assertEqual(base['acceptance_criteria'], smaller['acceptance_criteria'])
        self.assertEqual(['keep'], smaller['goal_contract']['body']['constraints'])

    def test_diverged_contract_criteria_stay(self):
        base = {'acceptance_criteria': [{'id': 'AC1', 'criterion': 'revised'}],
                'goal_contract': {'body': {'acceptance_criteria': [{'criterion': 'approved'}]}}}
        smaller, _ = context.compact(base, '/nonexistent/state.json')
        self.assertEqual([{'criterion': 'approved'}], smaller['goal_contract']['body']['acceptance_criteria'])

    def test_contract_without_criteria_list_untouched(self):
        base = {'acceptance_criteria': [{'id': 'AC1', 'criterion': 'exact'}],
                'goal_contract': {'body': 'exact goal'}}
        smaller, moved = context.compact(base, '/nonexistent/state.json')
        self.assertEqual(base, smaller)
        self.assertEqual([], moved)


if __name__ == '__main__':
    unittest.main()
