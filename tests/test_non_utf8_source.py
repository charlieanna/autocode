"""A source file that is not UTF-8 is proved like any other (autocode_diff_coverage.product_diff).

A real bug fix (Claude models, 2026-11-14) in a Python project whose ``greetings.py`` is Latin-1, declared
as PEP 263 asks, stopped after the Builder with ``PAUSED_INVALID_OUTPUT`` and "'utf-8' codec can't decode
byte 0xe1 in position 151: invalid continuation byte": the proof read the product's diff as UTF-8 text to
find its changed lines. The person was told a stage "returned output the runner could not accept as a
report" and offered a resume, which stops at the same place. With that fixed the run completes, and the
change the person is shown said "Binary file changed." for the Latin-1 file. The proof cases run a real
regression proof with pytest in a Git project, with no provider.
"""

from __future__ import annotations

import unittest

import autocode_checkpoint_cli as checkpoint_cli
import autocode_diff_coverage as diff_coverage
import autocode_verify as verify

from . import test_verify
from .test_verify import Project

LATIN1 = (
    "# -*- coding: latin-1 -*-\n"
    '"""Greetings, in the languages of the customers."""\n\n'
    "GREETINGS = {\n"
    '    "es": "¡Hola, señor!",\n'
    '    "de": "Grüß Gott!",\n'
    "}\n\n\n"
    "def greet(language):\n"
    "    return GREETINGS[language]\n"
)
FIXED = LATIN1.replace('    "de"', '    "pt": "Olá, senhor!",\n    "de"').replace(
    "GREETINGS[language]", 'GREETINGS.get(language, "Hello!")'
)
TEST = 'from greetings import greet\n\n\ndef test_german():\n    assert greet("de") == "Gr\\u00fc\\u00df Gott!"\n'
REGRESSION = (
    TEST
    + '\n\ndef test_portuguese():\n    assert greet("pt") == "Ol\\u00e1, senhor!"\n'
    + '\n\ndef test_an_unknown_language_falls_back():\n    assert greet("xx") == "Hello!"\n'
)
PYPROJECT = '[project]\nname = "greetings"\nversion = "0.1.0"\n\n[tool.pytest.ini_options]\npythonpath = ["."]\n'


class Latin1Project(unittest.TestCase):
    def setUp(self):
        self.project = Project({"pyproject.toml": PYPROJECT, "tests/test_greetings.py": TEST})
        self.addCleanup(self.project.close)
        root = self.project.root
        (root / "greetings.py").write_bytes(LATIN1.encode("latin-1"))
        test_verify.git(root, "add", "-A")
        test_verify.git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "latin-1")
        self.project.base = test_verify.git(root, "rev-parse", "HEAD")

    def change(self, source=FIXED, tests=REGRESSION):
        (self.project.root / "greetings.py").write_bytes(source.encode("latin-1"))
        self.project.write({"tests/test_greetings.py": tests})


class DiffTests(Latin1Project):
    def test_the_changed_lines_of_a_latin1_file_are_found(self):
        self.change()
        text = diff_coverage.product_diff(self.project.root, self.project.base, [("greetings.py", "modified")])
        (changed,) = diff_coverage.hunks(text)
        self.assertEqual("greetings.py", changed["path"])
        self.assertEqual([(6, 6), (12, 12)], [(hunk["start"], hunk["end"]) for hunk in changed["hunks"]])

    def test_a_new_latin1_file_is_all_changed_lines(self):
        (self.project.root / "extra.py").write_bytes('# -*- coding: latin-1 -*-\nNAME = "José"\n'.encode("latin-1"))
        text = diff_coverage.product_diff(self.project.root, self.project.base, [("extra.py", "added")])
        (changed,) = diff_coverage.hunks(text)
        self.assertEqual([(1, 2)], [(hunk["start"], hunk["end"]) for hunk in changed["hunks"]])


class ShownChangeTests(Latin1Project):
    """What a person is shown of the change (the status view's ``code_checkpoints`` diffs)."""

    def test_the_changed_lines_are_shown_and_not_called_a_binary_file(self):
        self.change()
        state = {"workspace": str(self.project.root)}
        entry = {"greetings.py": "file:x"}
        patch = checkpoint_cli.patch_text(state, self.project.base, ["greetings.py"], entry, entry)
        self.assertNotIn("Binary file changed", patch)
        self.assertIn("Not UTF-8 text", patch)
        self.assertIn('+    "pt": "Ol\ufffd, senhor!",\n', patch)
        self.assertIn("-    return GREETINGS[language]\n", patch)

    def test_a_file_with_a_nul_byte_is_still_a_binary_file(self):
        (self.project.root / "greetings.py").write_bytes(b"\x00\xe1\x00")
        state = {"workspace": str(self.project.root)}
        entry = {"greetings.py": "file:x"}
        patch = checkpoint_cli.patch_text(state, self.project.base, ["greetings.py"], entry, entry)
        self.assertIn("Binary or large file changed", patch)


class ProofTests(Latin1Project):
    def test_a_fix_in_a_latin1_file_is_proved(self):
        self.change()
        result = self.project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["greetings.py"], result["source_files"])
        # The file is still Latin-1: the proof read it, it did not rewrite it.
        self.assertEqual(FIXED.encode("latin-1"), (self.project.root / "greetings.py").read_bytes())

    def test_a_change_that_leaves_its_test_failing_is_still_refused(self):
        self.change(source=LATIN1.replace("GREETINGS[language]", "GREETINGS[language] or None"))
        result = self.project.verify()
        self.assertEqual(verify.FAIL, result["verdict"], result)


if __name__ == "__main__":
    unittest.main()
