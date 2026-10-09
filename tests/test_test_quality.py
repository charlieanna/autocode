"""Reject inert suites without claiming that syntax proves behavioral coverage."""
from pathlib import Path
import tempfile
import unittest

import autocode_test_quality as quality


class QualityTests(unittest.TestCase):
    def check(self, text):
        with tempfile.TemporaryDirectory() as workspace:
            Path(workspace, "test_app.py").write_text(text)
            quality.require_behavioral_tests(workspace, ["python3 -m unittest test_app.py"])

    def test_empty_and_literal_true_test_suites_are_rejected(self):
        for statement in ("pass", "assert True", "return None", '"only a docstring"'):
            with self.subTest(statement=statement), self.assertRaisesRegex(ValueError, "vacuous test bodies"):
                self.check("import unittest\nclass TestApp(unittest.TestCase):\n    def test_app(self):\n        " + statement + "\n")

    def test_assertions_calls_and_fixtures_are_not_mistaken_for_empty_tests(self):
        for text in (
            "def test_app():\n    assert app() == 3\n",
            "def test_app():\n    helper_that_asserts()\n",
            "class TestApp:\n    def setUp(self):\n        self.assertEqual(app(), 3)\n    def test_app(self):\n        pass\n",
            "def test_empty():\n    pass\ndef test_app():\n    assert app() == 3\n"):
            with self.subTest(text=text):
                self.check(text)
