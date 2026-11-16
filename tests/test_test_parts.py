"""Tests written inside a code file (autocode_test_parts): which parts of a file are tests, putting the
change's ones on the original code, and the regression proof and plan check that read them."""

from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_goal_lifecycle as lifecycle
import autocode_regression as regression
import autocode_test_parts as test_parts
import autocode_verify as verify

from . import test_plan_test_suite as plan_tests
from .test_plan_command_roles import contract, project, run_state

TEXTUTIL = '''"""Small text helpers. The examples in the docstrings are the tests."""

import re


def slugify(text):
    """A URL slug for ``text``.

    >>> slugify("Hello World")
    'hello-world'
    """
    return re.sub(r"\\s", "-", text.strip().lower())
'''
BUGGY = 're.sub(r"\\s", "-", text.strip().lower())'
RIGHT = 're.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")'
EXAMPLE = """    'hello-world'
    >>> slugify("Hello,  World!")
    'hello-world'
    \"\"\""""
FIX_ONLY = TEXTUTIL.replace(BUGGY, RIGHT)
EXAMPLE_ONLY = TEXTUTIL.replace('    \'hello-world\'\n    """', EXAMPLE)
FIXED = EXAMPLE_ONLY.replace(BUGGY, RIGHT)
PASSING = FIX_ONLY.replace(
    '    \'hello-world\'\n    """', "    'hello-world'\n    >>> slugify('a b')\n    'a-b'\n    \"\"\""
)
NEW_FUNCTION = (
    TEXTUTIL
    + '\n\ndef words(text):\n    """The words of ``text``.\n\n    >>> words("a b")\n    [\'a\', \'b\']\n    """\n'
    "    return text.split()\n"
)
DOCTEST = "python -m doctest textutil.py"
FLIP = "run `{}`: it fails on the original code and passes with the fix"
NO_TEST = "No regression test was added or changed, so the bug is not shown to be reproduced"
ALSO_PASS = "The regression tests also pass on the unfixed base code, so they do not reproduce the bug"

LIB = """//! Pages.

/// How many pages `items` fill at `size` a page.
///
/// ```
/// assert_eq!(pager::page_count(10, 5), 2);
/// ```
pub fn page_count(items: u32, size: u32) -> u32 {
    items / size
}
"""
LIB_FIXED = LIB.replace("/// ```\npub", "/// assert_eq!(pager::page_count(11, 5), 3);\n/// ```\npub").replace(
    "items / size", "(items + size - 1) / size"
)
CARGO = '[package]\nname = "pager"\nversion = "0.1.0"\nedition = "2021"\n\n[dependencies]\n'
USAGE = "Using textutil\n==============\n\n    >>> from textutil import slugify\n    >>> slugify('Hello World')\n    'hello-world'\n"
USAGE_FIXED = USAGE + "    >>> slugify('Hello,  World!')\n    'hello-world'\n"


class PartsTests(unittest.TestCase):
    """Which parts of a file are tests, by the language's own syntax."""

    def test_a_docstring_with_an_example_holds_tests(self):
        self.assertTrue(test_parts.holds_tests("textutil.py", TEXTUTIL.encode()))
        self.assertFalse(test_parts.holds_tests("textutil.py", TEXTUTIL.replace(">>>", "Example:").encode()))
        # A ">>>" in code is no example; a file that does not parse holds none.
        self.assertFalse(test_parts.holds_tests("prompt.py", b'PROMPT = ">>> "\n'))
        self.assertFalse(test_parts.holds_tests("broken.py", b'def f(:\n    """>>> f()"""\n'))

    def test_a_rust_doc_comment_with_a_code_block_holds_tests(self):
        self.assertTrue(test_parts.holds_tests("src/lib.rs", LIB.encode()))
        self.assertFalse(test_parts.holds_tests("src/lib.rs", LIB.replace("```", "").encode()))
        # A doc comment inside a string or a block comment is none.
        quoted = b'pub const DOC: &str = "\n/// ```\n/// x\n/// ```\n";\n/* /// ``` */\n'
        self.assertFalse(test_parts.holds_tests("src/lib.rs", quoted))

    def test_a_document_of_examples_holds_tests(self):
        self.assertTrue(test_parts.holds_tests("docs/usage.txt", USAGE.encode()))
        self.assertFalse(test_parts.holds_tests("README.md", b"# textutil\n\nRun `python -m doctest`.\n"))
        self.assertFalse(test_parts.holds_tests("main.go", b"// >>> nothing\n"))

    def test_only_a_change_to_the_tests_is_a_change_of_them(self):
        self.assertTrue(test_parts.changed("textutil.py", TEXTUTIL.encode(), EXAMPLE_ONLY.encode()))
        self.assertFalse(test_parts.changed("textutil.py", TEXTUTIL.encode(), FIX_ONLY.encode()))
        self.assertTrue(test_parts.changed("textutil.py", None, TEXTUTIL.encode()))
        self.assertFalse(test_parts.changed("plain.py", None, b"x = 1\n"))
        self.assertTrue(test_parts.changed("src/lib.rs", LIB.encode(), LIB_FIXED.encode()))
        self.assertFalse(test_parts.changed("src/lib.rs", LIB.encode(), LIB.replace("items / size", "0").encode()))


class SpliceTests(unittest.TestCase):
    """The original program with the change's tests."""

    def test_the_example_goes_onto_the_original_code_and_the_fix_does_not(self):
        spliced = test_parts.splice("textutil.py", TEXTUTIL.encode(), FIXED.encode())
        self.assertEqual(EXAMPLE_ONLY.encode(), spliced)

    def test_a_new_functions_examples_go_into_doctests_own_table(self):
        spliced = test_parts.splice("textutil.py", TEXTUTIL.encode(), NEW_FUNCTION.encode()).decode()
        self.assertTrue(spliced.startswith(TEXTUTIL))
        self.assertIn("__test__ = {", spliced)
        self.assertIn("'words':", spliced)
        self.assertNotIn("def words", spliced)
        namespace: dict = {}
        exec(compile(spliced, "textutil.py", "exec"), namespace)  # noqa: S102 - the test's own fixture
        self.assertIn(">>> words", namespace["__test__"]["words"])

    def test_a_docstring_that_shares_its_line_with_code_is_not_put_on_the_original(self):
        base = b'def f(): ">>> f()\\n1"; return 1\n'
        candidate = b'def f(): ">>> f()\\n2"; return 2\n'
        with self.assertRaisesRegex(ValueError, "could not be put on the original code"):
            test_parts.splice("f.py", base, candidate)

    def test_a_rust_doc_example_goes_onto_the_original_code(self):
        spliced = test_parts.splice("src/lib.rs", LIB.encode(), LIB_FIXED.encode()).decode()
        self.assertIn("page_count(11, 5), 3", spliced)
        self.assertIn("items / size", spliced)

    def test_a_document_is_taken_whole(self):
        self.assertEqual(
            USAGE_FIXED.encode(), test_parts.splice("docs/usage.txt", USAGE.encode(), USAGE_FIXED.encode())
        )


class PartitionTests(unittest.TestCase):
    def setUp(self):
        made = project(self, {"textutil.py": TEXTUTIL, "src/lib.rs": LIB, "tests/test_x.py": "x = 1\n"}, ())
        self.made = made

    def test_a_file_whose_examples_changed_is_a_changed_test_and_stays_code(self):
        self.made.write({"textutil.py": FIXED})
        changes = {"textutil.py": "modified"}
        self.assertEqual(
            (["textutil.py"], ["textutil.py"]),
            test_parts.partition(self.made.root, self.made.base, [], ["textutil.py"], changes),
        )
        # Its examples alone are no change of the program.
        self.made.write({"textutil.py": EXAMPLE_ONLY})
        self.assertEqual(
            (["textutil.py"], []), test_parts.partition(self.made.root, self.made.base, [], ["textutil.py"], changes)
        )

    def test_beside_a_changed_test_file_a_doctest_is_left_to_the_suite_and_a_rust_example_is_not(self):
        self.made.write({"textutil.py": FIXED, "src/lib.rs": LIB_FIXED, "tests/test_x.py": "x = 2\n"})
        changes = dict.fromkeys(("textutil.py", "src/lib.rs", "tests/test_x.py"), "modified")
        runnable, sources = test_parts.partition(
            self.made.root, self.made.base, ["tests/test_x.py"], ["textutil.py", "src/lib.rs"], changes
        )
        self.assertEqual(["tests/test_x.py", "src/lib.rs"], runnable)
        self.assertEqual(["textutil.py", "src/lib.rs"], sources)


class ProofTests(unittest.TestCase):
    """Real proofs: the change's examples must fail on the original code and pass with the change."""

    def prove(self, seed, change, command=DOCTEST):
        made = project(self, seed, ())
        made.write(change)
        state = run_state(made, contract(FLIP.format(command)))
        path = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
        with (
            mock.patch.dict(os.environ, {"PATH": path}),
            mock.patch.object(verify.diff_coverage, "collect", return_value=None),
        ):
            self.run_dir = made.root.parent / "run"
            return regression.prove(state, made.root, self.run_dir)

    def verdict(self, proof):
        return proof["verdict"], proof["failures"]

    def test_a_fix_with_its_example_in_the_docstring_is_proven(self):
        proof = self.prove({"textutil.py": TEXTUTIL}, {"textutil.py": FIXED})
        self.assertEqual(("PASS", []), self.verdict(proof))
        receipts = list(self.run_dir.rglob("test-parts/receipt.json"))
        self.assertEqual(1, len(receipts))
        self.assertIn("textutil.py", receipts[0].read_text())
        self.assertIn("textutil.py", proof["source_files"])

    def test_so_is_it_with_pytest_running_the_doctests(self):
        command = f"{sys.executable} -m pytest -q -p no:cacheprovider --doctest-modules textutil.py"
        self.assertEqual(
            ("PASS", []), self.verdict(self.prove({"textutil.py": TEXTUTIL}, {"textutil.py": FIXED}, command))
        )

    def test_so_is_a_new_function_with_its_doctest(self):
        self.assertEqual(
            ("PASS", []), self.verdict(self.prove({"textutil.py": TEXTUTIL}, {"textutil.py": NEW_FUNCTION}))
        )

    def test_so_is_a_fix_whose_example_is_in_a_document(self):
        seed = {"textutil.py": TEXTUTIL, "docs/usage.txt": USAGE}
        change = {"textutil.py": FIX_ONLY, "docs/usage.txt": USAGE_FIXED}
        self.assertEqual(("PASS", []), self.verdict(self.prove(seed, change, "python -m doctest docs/usage.txt")))

    @unittest.skipUnless(shutil.which("cargo"), "the project's suite is cargo test")
    def test_so_is_a_rust_fix_with_its_doc_example(self):
        seed = {"Cargo.toml": CARGO, "src/lib.rs": LIB}
        proof = self.prove(seed, {"src/lib.rs": LIB_FIXED}, "cargo test --offline")
        self.assertEqual(("PASS", []), self.verdict(proof))

    def test_a_fix_alone_has_no_test(self):
        self.assertEqual(
            ("FAIL", [NO_TEST]), self.verdict(self.prove({"textutil.py": TEXTUTIL}, {"textutil.py": FIX_ONLY}))
        )

    def test_an_example_that_passes_on_the_original_code_shows_nothing(self):
        proof = self.prove({"textutil.py": TEXTUTIL}, {"textutil.py": PASSING})
        self.assertEqual(("FAIL", [ALSO_PASS]), self.verdict(proof))

    def test_an_example_without_its_fix_is_no_fix(self):
        proof = self.prove({"textutil.py": TEXTUTIL}, {"textutil.py": EXAMPLE_ONLY})
        self.assertEqual("FAIL", proof["verdict"])
        self.assertIn("Only test files changed; a fix must change product code", proof["failures"])


class PlanCheckTests(unittest.TestCase):
    """A project whose only tests are doctests has tests."""

    def check(self, files, *paths, guard=None):
        made = project(self, files, ())
        run_dir = Path(tempfile.mkdtemp(prefix="plan-check-"))
        self.addCleanup(shutil.rmtree, run_dir, True)
        state = {**plan_tests.CheckTests.state(self, made), "base_commit": made.base, "run_dir": str(run_dir)}
        body = plan_tests.plan(*paths)
        body["milestones"][0]["affected_paths"] = list(paths)
        body["acceptance_criteria"][0]["verification_method"] = FLIP.format(DOCTEST)
        if guard:
            criterion = {**body["acceptance_criteria"][0], "id": "AC2", "verification_method": f"guard: run `{guard}`"}
            body["acceptance_criteria"].append(criterion)
            body["milestones"][0]["acceptance_criteria"].append("AC2")
        lifecycle.validate_body(state, body, ready=True)

    def test_a_plan_that_changes_a_file_of_doctests_is_accepted(self):
        self.check({"textutil.py": TEXTUTIL}, "textutil.py")

    def test_a_project_with_no_tests_at_all_is_still_refused(self):
        with self.assertRaisesRegex(ValueError, "plans no test, and the project has no tests yet"):
            self.check({"textutil.py": TEXTUTIL.replace(">>>", "Example:")}, "textutil.py")

    def test_a_guard_that_runs_the_files_examples_runs_existing_tests(self):
        # The real run's first draft was refused with "AC3 `python -m doctest textutil.py` names no existing
        # test file" and repaired by its Planner (survey section 55).
        self.check({"textutil.py": TEXTUTIL}, "textutil.py", guard=DOCTEST)

    def test_a_guard_that_names_a_file_without_examples_is_still_refused(self):
        files = {"textutil.py": TEXTUTIL, "plain.py": "X = 1\n"}
        with self.assertRaisesRegex(ValueError, "names no existing test file"):
            self.check(files, "textutil.py", guard="python -m doctest plain.py")


REFACTORED = TEXTUTIL.replace(BUGGY, 're.sub(r"\\s", "-", text.lower().strip())')


class GuardProofTests(unittest.TestCase):
    """A change that alters no behavior, guarded by the examples the file already has."""

    def guarded(self, change):
        made = project(self, {"textutil.py": TEXTUTIL}, ())
        made.write(change)
        state = run_state(made, contract(f"guard: run `{DOCTEST}`", kind="build"))
        path = os.path.dirname(sys.executable) + os.pathsep + os.environ.get("PATH", "")
        with (
            mock.patch.dict(os.environ, {"PATH": path}),
            mock.patch.object(verify.diff_coverage, "collect", return_value=None),
        ):
            return regression.prove(state, made.root, made.root.parent / "run")

    def test_a_refactor_is_guarded_by_the_examples(self):
        proof = self.guarded({"textutil.py": REFACTORED})
        self.assertEqual(("PASS", []), (proof["verdict"], proof["unverified"]), proof["failures"])
        checks = proof["checks"]
        self.assertEqual(
            (0, 0),
            (checks["guard_command_on_base:AC1"]["exit_code"], checks["guard_command_on_candidate:AC1"]["exit_code"]),
        )

    def test_a_refactor_that_breaks_an_example_is_refused(self):
        proof = self.guarded({"textutil.py": TEXTUTIL.replace(BUGGY, "text")})
        self.assertEqual("FAIL", proof["verdict"])


if __name__ == "__main__":
    unittest.main()
