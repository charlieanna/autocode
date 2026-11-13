"""A failing acceptance test whose skip it never reached (#3333)."""

import unittest

import autocode_unreached_skip as unreached

SOURCE = (
    "import pytest\n"  # 1
    "\n"  # 2
    "\n"  # 3
    "@pytest.mark.skipif(False, reason='decorators were evaluated today')\n"  # 4
    "def test_ac1_reads(tmp_path):\n"  # 5
    "    assert reader.read() == 1\n"  # 6
    "    if not tmp_path.exists():\n"  # 7
    "        pytest.skip('no tmp')  # skipped on this host\n"  # 8
    "\n"  # 9
    "\n"  # 10
    "def test_other():\n"  # 11
    "    pytest.skip('another test')\n"  # 12
)


class SkipBelowFailureTests(unittest.TestCase):
    def test_a_skip_below_the_pytest_failure_line_is_named(self):
        tail = "tests/test_reader.py:6: AssertionError\nFAILED tests/test_reader.py::test_ac1_reads"
        self.assertEqual(
            "line 8: pytest.skip('no tmp')  # skipped on this host",
            unreached.skip_below_failure(SOURCE, "tests/test_reader.py", "test_ac1_reads", tail),
        )

    def test_a_unittest_frame_of_another_test_or_file_is_not_this_tests_failure(self):
        for tail in (
            'File "/x/tests/test_reader.py", line 6, in test_other',
            'File "/x/tests/test_elsewhere.py", line 6, in test_ac1_reads',
        ):
            with self.subTest(tail=tail):
                self.assertEqual("", unreached.skip_below_failure(SOURCE, "test_reader.py", "test_ac1_reads", tail))

    def test_a_skip_above_the_failure_a_decorator_or_another_test_is_not_named(self):
        source = "def test_ac1_reads(self):\n    if missing:\n        self.skipTest('x')\n    self.fail('no')\n"
        tail = 'File "/w/test_reader.py", line 4, in test_ac1_reads\n'
        self.assertEqual("", unreached.skip_below_failure(source, "test_reader.py", "test_ac1_reads", tail))
        # Line 6 fails in test_ac1_reads; test_other's skip on line 12 is outside its body.
        trimmed = SOURCE.replace(
            "    if not tmp_path.exists():\n        pytest.skip('no tmp')  # skipped on this host\n", ""
        )
        self.assertEqual(
            "", unreached.skip_below_failure(trimmed, "test_reader.py", "test_ac1_reads", "test_reader.py:6: E")
        )

    def test_raise_skiptest_is_a_skip(self):
        source = "def test_ac1(self):\n    self.fail('no')\n    raise unittest.SkipTest('x')\n"
        tail = 'File "/w/test_a.py", line 2, in test_ac1\n'
        self.assertEqual(
            "line 3: raise unittest.SkipTest('x')", unreached.skip_below_failure(source, "test_a.py", "test_ac1", tail)
        )

    def test_what_cannot_be_read_names_nothing(self):
        tail = "test_a.py:2: E"
        for source, file in (
            ("def test_ac1(:\n", "test_a.py"),  # does not parse
            ("def test_other():\n    pass\n", "test_a.py"),  # no such test
            (SOURCE, "test_a.js"),  # not Python
        ):
            with self.subTest(file=file, source=source[:15]):
                self.assertEqual("", unreached.skip_below_failure(source, file, "test_ac1", tail))
        self.assertEqual("", unreached.skip_below_failure(SOURCE, "test_reader.py", "test_ac1_reads", "no frames"))


if __name__ == "__main__":
    unittest.main()
