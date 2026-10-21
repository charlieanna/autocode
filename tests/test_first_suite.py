"""First-project proofs and preservation guards (#526, #1783)."""

import shutil
import sys
import unittest

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
        self.assertIn(
            "The base suite could not run; preservation of existing behavior is unproven", result["unverified"]
        )

    def test_bugfix_still_requires_a_working_baseline(self):
        _, result = self.prove(new_behavior=False)
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertIn(
            "The base suite could not run; preservation of existing behavior is unproven", result["unverified"]
        )

    def test_preservation_only_does_not_gain_a_new_behavior_exception(self):
        _, result = self.prove(preserve_only=True)
        self.assertNotEqual(verify.PASS, result["verdict"], result)


class FirstPythonSuiteTests(unittest.TestCase):
    def prove(self, seed):
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
