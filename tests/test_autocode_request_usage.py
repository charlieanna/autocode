import unittest
from autocode_request_usage import measurement, view


class RequestUsageTests(unittest.TestCase):
    def step(self, identity, tokens):
        return {'type': 'step_finish', 'sessionID': 's', 'part': {'id': identity, 'tokens': tokens}}

    def test_individual_context_includes_cache_and_is_not_cumulative(self):
        rows = [self.step('1', {'input': 10, 'output': 5, 'cache': {'read': 100, 'write': 20}}),
                self.step('2', {'input': 8, 'output': 2, 'cache': {'read': 150, 'write': 0}})]
        result = measurement(rows + [rows[-1]])
        self.assertEqual(result['peak_input_tokens'], 158)
        self.assertEqual(result['last']['input_tokens'], 158)
        self.assertEqual(result['observed_requests'], 2)
        self.assertTrue(result['complete'])

    def test_missing_counters_and_native_turn_totals_are_unavailable(self):
        for rows in ([], [{'type': 'turn.completed', 'usage': {'input_tokens': 900}}], [self.step('x', {})]):
            result = measurement(rows)
            self.assertIsNone(result['peak_input_tokens'])
            self.assertIsNone(result['last'])
            self.assertFalse(result['complete'])
        result = measurement([self.step('a', {'input': 2, 'cache': {'read': 3, 'write': 0}}), self.step('b', {})])
        self.assertIsNone(result['last'])
        self.assertEqual(result['missing_usage_requests'], 1)
        self.assertEqual(view({'stages': [{'metrics': {'request_context': result}}]})['unavailable_stages'], 1)

    def test_started_request_without_final_usage_keeps_measurement_incomplete(self):
        result = measurement([self.step('a', {'input': 2, 'cache': {'read': 3, 'write': 0}}), {'type': 'step_start'}])
        self.assertEqual(result['peak_input_tokens'], 5)
        self.assertFalse(result['complete'])
        self.assertTrue(result['unfinished_request'])

    def test_bad_identity_or_conflicting_replay_is_unavailable(self):
        good = self.step('a', {'input': 2, 'cache': {'read': 3, 'write': 0}})
        for bad in (self.step({'invalid': 'id'}, {}), self.step('a', {'input': 100, 'cache': {'read': 3, 'write': 0}})):
            result = measurement([good, bad])
            self.assertFalse(result['complete'])
            self.assertEqual(result['missing_usage_requests'], 1)
