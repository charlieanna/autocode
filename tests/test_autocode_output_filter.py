import unittest

from autocode_output_filter import compact_output


class FilterTests(unittest.TestCase):
    def test_raw_for_unknown_interrupted_and_malformed_runner_output(self):
        text = 'test_a (tests.A) ... ok\n' * 10 + 'Traceback\nTraceback\nAssertionError\nAssertionError\n'
        for command, code in ((['python', 'custom.py'], 1), (['python', '-m', 'unittest'], -15), (['python', '-m', 'unittest'], 1)):
            with self.subTest(command=command, code=code):
                self.assertEqual(compact_output(text, command=command, exit_code=code)['content'], text)

    def test_passing_progress_only_collapsed_for_completed_verified_command(self):
        text = '........................................\n----------------------------------------------------------------------\nRan 40 tests in 0.001s\n\nOK\n'
        result = compact_output(text, command=['python3.12', '-m', 'unittest'], exit_code=0)
        self.assertEqual(result['omitted_sections'], [{'start_line': 1, 'end_line': 1, 'kind': 'passing-tests-or-progress'}])
        self.assertTrue(result['content'].endswith('Ran 40 tests in 0.001s\n\nOK\n'))
        self.assertEqual(compact_output(text, command=['python', '-m', 'unittest'], exit_code=0, enabled=False)['content'], text)

    def test_repeated_multiline_diagnostic_remains_verbatim(self):
        details = '======================================================================\nFAIL: case\nTraceback (most recent call last):\n  frame\nAssertionError: same\n' * 2
        text = 'test_ok (tests.A) ... ok\n' * 20 + details + 'Ran 22 tests in 0.01s\n\nFAILED (failures=2)\n'
        shown = compact_output(text, command=['python', '-m', 'unittest'], exit_code=1)['content']
        self.assertIn(details, shown)

    def test_unicode_message_separator_does_not_shift_exact_omission_ranges(self):
        passing = ''.join(f'test_{n} (suite.Case) ... ok\n' for n in range(3))
        text = 'note\u2028same physical line\n' + passing + '\nRan 3 tests in 0.001s\n\nOK\n'
        shown = compact_output(text, command=['python', '-m', 'unittest'], exit_code=0)
        omitted = shown['omitted_sections'][0]
        original = text.encode().splitlines(keepends=True)
        self.assertEqual(b''.join(original[omitted['start_line']-1:omitted['end_line']]), passing.encode())
        self.assertIn('note\u2028same physical line\n', shown['content'])
