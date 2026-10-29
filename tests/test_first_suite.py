"""First-project proofs and preservation guards (#526, #1783)."""

import shutil
import sys
import unittest

import autocode_first_suite as first_suite
import autocode_verify as verify

from tests.test_verify import Project


@unittest.skipUnless(shutil.which("node"), "Node is required for native suite proof")
class FirstNodeSuiteTests(unittest.TestCase):
    def prove(self, seed=None, *, new_behavior=True, top_level_import=False, broken=False, preserve_only=False):
        project = Project(seed or {"README.md": "A new project.\n"})
        self.addCleanup(project.close)
        load = "const {greet}=require('./greet.cjs');"
        project.write(
            {
                "greet.cjs": "exports.greet=name=>'" + ("wrong " if broken else "hello ") + "'+name;\n",
                "greet.test.cjs": "const {test}=require('node:test');"
                "const assert=require('node:assert/strict');"
                + (load if top_level_import else "")
                + "test('hello',()=>{"
                + ("" if top_level_import else load)
                + "assert.equal(greet('x'),'hello x');});\n",
            }
        )
        command = "node --test greet.test.cjs"
        framework = verify.command_framework(command)
        base = verify.baseline(
            project.root, project.base, project.evidence, framework=framework, suite_command=command, timeout=20
        )
        result = verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=command,
            regression_command=command,
            base_suite=base,
            new_behavior=new_behavior,
            preserve_only=preserve_only,
            timeout=20,
        )
        return base, result

    def test_first_suite_has_named_fail_to_pass_without_a_preexisting_suite(self):
        base, result = self.prove()
        self.assertEqual("broken", base["health"])
        self.assertIsNone(base["receipt"]["results"])
        self.assertFalse(base["receipt"]["timed_out"])
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["greet.test.cjs::hello"], result["fail_to_pass"])
        self.assertFalse(result["not_run_on_base"])
        self.assertIn("documentation-only", " ".join(result["notes"]))

    def test_new_module_import_can_establish_new_behavior_without_old_suite(self):
        _, result = self.prove(top_level_import=True)
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["greet.test.cjs::hello"], result["not_run_on_base"])

    def test_broken_candidate_is_still_rejected(self):
        _, result = self.prove(broken=True)
        self.assertEqual(verify.FAIL, result["verdict"], result)

    def test_unknown_existing_source_cannot_claim_an_empty_baseline(self):
        _, result = self.prove({"README.md": "Existing project.\n", "legacy": "old behavior\n"})
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertIn(first_suite.UNTESTED_BASE, result["unverified"])

    def test_bugfix_still_requires_a_working_baseline(self):
        _, result = self.prove(new_behavior=False)
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertIn(first_suite.UNTESTED_BASE, result["unverified"])

    def test_preservation_only_does_not_gain_a_new_behavior_exception(self):
        _, result = self.prove(preserve_only=True)
        self.assertNotEqual(verify.PASS, result["verdict"], result)


class FirstPythonSuiteTests(unittest.TestCase):
    def prove(self, seed, extra=None):
        project = Project(seed)
        self.addCleanup(project.close)
        # The run's original checkout: a task worktree proves against it, so the
        # candidate cannot have hidden ignored code there.
        original = Project(seed)
        self.addCleanup(original.close)
        project.write(
            {
                "greet.py": "def greet(name):\n    return 'Hello, ' + name + '!'\n",
                "tests/test_greet.py": "import unittest\nfrom greet import greet\n\n\n"
                "class Greet(unittest.TestCase):\n    def test_greet(self):\n"
                "        self.assertEqual('Hello, bob!', greet('bob'))\n",
                **(extra or {}),
            }
        )
        command = f"{sys.executable} -m unittest discover -s tests -v"
        framework = verify.command_framework(command)
        base = verify.baseline(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=command,
            timeout=60,
            dependencies_from=original.root,
        )
        return verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=command,
            regression_command=command,
            base_suite=base,
            new_behavior=True,
            timeout=60,
            dependencies_from=original.root,
        )

    def test_a_manifest_only_base_has_no_behavior_to_preserve(self):
        # #1783: the live first-project base held README.md, pyproject.toml and an
        # empty tests/__init__.py. None is executable source, so the suite had
        # nothing to run on base and the manifest holds no behavior to preserve.
        result = self.prove(
            {
                "README.md": "A new project.\n",
                "pyproject.toml": "[project]\nname = 'greet'\nversion = '0.1.0'\n",
                "tests/__init__.py": "",
            }
        )
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertTrue(any("no behavior to preserve" in note for note in result["notes"]), result["notes"])

    def test_a_data_only_base_has_no_behavior_to_preserve(self):
        # #2036: two live greenfield chats shipped the input their CLI reads beside the README. A text
        # or data file is read, never run: with no code or test file in the base there is nothing to preserve.
        for name, text in (
            ("sample.txt", "the quick brown fox\nthe lazy dog\n"),
            ("contacts.json", '[{"name": "Ana"}]\n'),
        ):
            with self.subTest(name):
                result = self.prove({"README.md": "A new project.\n", name: text})
                self.assertEqual(verify.PASS, result["verdict"], result)
                self.assertTrue(
                    any("no existing behavior to preserve" in note for note in result["notes"]), result["notes"]
                )

    def test_a_stray_extensionless_note_has_no_behavior_to_preserve(self):
        # #2259: a first task's base held only a one-line file named "a". The base suite collected no
        # tests, so the proof waited on passing evidence nobody could supply and the run dead-ended.
        result = self.prove({"a": "hello\n"})
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertFalse(result["unverified"], result)

    def test_an_extensionless_file_that_can_run_still_requires_preservation(self):
        for seed in (
            {"a": "hello\n", "Makefile": "test:\n\techo hi\n"},
            {"run": "#!/bin/sh\necho hi\n"},
            {"a": "hello\n", "legacy.py": "VALUE = 1\n"},
        ):
            with self.subTest(sorted(seed)):
                result = self.prove(seed)
                self.assertEqual(verify.UNVERIFIED, result["verdict"], result)

    def test_a_plain_note_is_judged_by_content(self):
        self.assertTrue(first_suite.plain_note("a", b"hello\n"))
        self.assertTrue(first_suite.plain_note("docs/NOTES", b"todo\n"))
        for path, content in (
            ("run", b"#!/usr/bin/env python3\nprint(1)\n"),
            ("Dockerfile", b"FROM scratch\n"),
            ("blob", b"\x00\x01"),
            ("big", b"x" * (first_suite.NOTE_LIMIT + 1)),
            ("notes.sh", b"echo hi\n"),
            (".envrc", b"export A=1\n"),
            ("a", None),
        ):
            with self.subTest(path):
                self.assertFalse(first_suite.plain_note(path, content))
        read = {"a": b"hello\n"}.get
        self.assertTrue(first_suite.inert_base_file("a", 6, separate=True, source=False, read=read))
        self.assertFalse(first_suite.inert_base_file("a", 6, separate=True, source=True, read=read))
        self.assertFalse(first_suite.inert_base_file("a", 6, separate=False, source=False, read=read))
        self.assertFalse(first_suite.inert_base_file("a", 6, separate=True, source=False))

    def test_a_data_file_beside_existing_source_still_requires_preservation(self):
        result = self.prove(
            {"README.md": "An existing project.\n", "sample.txt": "the fox\n", "legacy.py": "VALUE = 1\n"}
        )
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertTrue(
            any("preservation of existing behavior is unproven" in row for row in result["unverified"]),
            result["unverified"],
        )

    def test_only_formats_nothing_runs_count_as_data(self):
        for path in ("sample.txt", "data/rows.csv", "fixtures/contacts.JSON", "notes.rst", "config.yaml"):
            with self.subTest(path):
                self.assertTrue(first_suite.data_file(path))
        for path in ("run.sh", "tool.py", "page.html", "index.js", "Makefile", "data.sql", "template.j2", "noext"):
            with self.subTest(path):
                self.assertFalse(first_suite.data_file(path))
        self.assertTrue(first_suite.inert_base_file("sample.txt", 10, separate=True, source=False))
        self.assertFalse(first_suite.inert_base_file("sample.txt", 10, separate=True, source=True))
        self.assertFalse(first_suite.inert_base_file("sample.txt", 10, separate=False, source=False))

    def test_untested_code_is_kept_by_the_change_tests_that_pass_on_it(self):
        # #2259: an original program with no tests. The change's test of its existing behavior passes on the
        # original code and on the change, so it stands in for the suite the base lacked.
        legacy = "tests/test_legacy.py"
        check = "import unittest\nimport legacy\n\n\nclass Legacy(unittest.TestCase):\n    def test_value(self):\n"
        result = self.prove(
            {"legacy.py": "VALUE = 1\n"}, {legacy: check + "        self.assertEqual(1, legacy.VALUE)\n"}
        )
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertIn("test_legacy.Legacy.test_value", " ".join(result["pass_to_pass"]))
        self.assertIn(first_suite.UNTESTED_BASE_KEPT, result["notes"])

    def test_untested_code_without_a_kept_test_tells_the_builder_what_to_add(self):
        result = self.prove({"legacy.py": "VALUE = 1\n"})
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertIn(first_suite.UNTESTED_BASE, result["unverified"])

    def test_a_manifest_beside_existing_source_still_requires_preservation(self):
        result = self.prove(
            {
                "README.md": "An existing project.\n",
                "pyproject.toml": "[project]\nname = 'greet'\nversion = '0.1.0'\n",
                "legacy.py": "VALUE = 1\n",
                "tests/__init__.py": "",
            }
        )
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertIn(
            "The base suite provided no complete passing-test evidence; preservation of existing behavior is unproven",
            result["unverified"],
        )


if __name__ == "__main__":
    unittest.main()
