"""A first Go suite on a base that has no Go project (#685)."""
import shutil
import unittest
from pathlib import Path

import autocode_first_suite as first_suite
import autocode_verify as verify
from tests.test_verify import Project


CS = "namespace Demo { public class Policy {} }\n"
GO_MOD = "module policy\n\ngo 1.21\n"
POLICY = "package policy\nfunc Days() int { return 30 }\n"
TEST_OK = ("package policy\nimport \"testing\"\nfunc TestDays(t *testing.T) {\n"
           "\tif Days() != 30 {\n\t\tt.Fatal(Days())\n\t}\n}\n")
TEST_BAD = TEST_OK.replace("!= 30", "!= 99")
NOTE = "no Go project"


def _passed():
    return {"exit_code": 0, "timed_out": False, "results_expected": True,
            "results": {"passed": ["policy::TestDays"], "failed": [], "skipped": [],
                        "complete": True, "total": 1, "collection_errors": [], "uncollected": []}}


class AbsentGoSuiteTests(unittest.TestCase):
    def test_inventory_is_go_module_workspace_or_source(self):
        self.assertFalse(first_suite.contains_go_project(["reference/Policy.cs", "README.md"]))
        self.assertTrue(first_suite.contains_go_project(["reference/Policy.cs", "go.mod"]))
        self.assertTrue(first_suite.contains_go_project(["nested/go.work"]))
        self.assertTrue(first_suite.contains_go_project(["old.go"]))

    def test_only_the_no_package_report_is_an_absent_suite(self):
        base = {"exit_code": 1, "timed_out": False, "results_expected": True, "results": None,
                "tail": "go: warning: \"./...\" matched no packages\nno packages to test\n"}
        self.assertTrue(first_suite.absent_go_suite(base, _passed(), no_go_project=True))
        self.assertFalse(first_suite.absent_go_suite(base, _passed(), no_go_project=False))
        built = {**base, "results": {"passed": [], "failed": ["policy::[build failed]"], "skipped": []},
                 "tail": "FAIL\tpolicy [build failed]\n"}
        self.assertFalse(first_suite.absent_go_suite(built, _passed(), no_go_project=True))
        pattern = {**base, "results": {"passed": [], "failed": ["./...::[build failed]"], "skipped": []},
                   "tail": "pattern ./...: directory prefix . does not contain main module\n"}
        self.assertTrue(first_suite.absent_go_suite(pattern, _passed(), no_go_project=True))


@unittest.skipUnless(shutil.which("go"), "Go is required for native suite proof")
class FirstGoSuiteTests(unittest.TestCase):
    def prove(self, seed, files, *, new_behavior=True, parent_module=False):
        project = Project(seed)
        self.addCleanup(project.close)
        if parent_module:
            # The proof trees sit under the run directory. A go.mod above them is how a
            # candidate's new module makes `go test` on the base say "matched no packages".
            (Path(project.temp.name) / "go.mod").write_text("module parent\n\ngo 1.21\n")
        project.write(files)
        framework = verify.detect_framework(project.root)
        self.assertIsNotNone(framework)
        self.assertEqual("go", framework.name)
        command = framework.suite
        base = verify.baseline(project.root, project.base, project.evidence,
                               framework=framework, suite_command=command, timeout=60)
        result = verify.verify(project.root, project.base, project.evidence,
                               framework=framework, suite_command=command, base_suite=base,
                               new_behavior=new_behavior, timeout=60)
        return base, result

    def test_a_code_only_base_inside_a_new_module_is_not_a_broken_suite(self):
        base, result = self.prove(
            {"reference/Policy.cs": CS},
            {"go.mod": GO_MOD, "policy.go": POLICY, "policy_test.go": TEST_OK},
            parent_module=True)
        self.assertEqual("broken", base["health"])
        self.assertIn("matched no packages", base["receipt"]["tail"])
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertIn("policy::TestDays", result["fail_to_pass"] + result["not_run_on_base"])
        self.assertTrue(any(NOTE in note for note in result["notes"]), result["notes"])

    def test_a_code_only_base_outside_a_module_is_not_a_broken_suite(self):
        base, result = self.prove(
            {"reference/Policy.cs": CS},
            {"go.mod": GO_MOD, "policy.go": POLICY, "policy_test.go": TEST_OK})
        self.assertEqual("broken", base["health"])
        self.assertIn("does not contain main module", base["receipt"]["tail"])
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertTrue(any(NOTE in note for note in result["notes"]), result["notes"])

    def test_existing_go_source_keeps_the_base_suite_rule(self):
        _, result = self.prove(
            {"reference/Policy.cs": CS, "old.go": "package old\n"},
            {"go.mod": GO_MOD, "policy.go": POLICY, "policy_test.go": TEST_OK})
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertFalse(any(NOTE in note for note in result["notes"]), result["notes"])

    def test_an_existing_module_with_no_packages_is_still_unverified(self):
        _, result = self.prove(
            {"go.mod": GO_MOD},
            {"policy.go": POLICY, "policy_test.go": TEST_OK})
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertFalse(any(NOTE in note for note in result["notes"]), result)

    def test_a_base_package_that_fails_to_build_is_not_excused(self):
        _, result = self.prove(
            {"go.mod": GO_MOD, "policy.go": "package policy\nfunc Days( {\n"},
            {"policy.go": POLICY, "policy_test.go": TEST_OK})
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertFalse(any(NOTE in note for note in result["notes"]), result)

    def test_a_broken_candidate_is_still_rejected(self):
        _, result = self.prove(
            {"reference/Policy.cs": CS},
            {"go.mod": GO_MOD, "policy.go": POLICY, "policy_test.go": TEST_BAD},
            parent_module=True)
        self.assertEqual(verify.FAIL, result["verdict"], result)

    def test_a_bugfix_still_requires_a_working_baseline(self):
        _, result = self.prove(
            {"reference/Policy.cs": CS},
            {"go.mod": GO_MOD, "policy.go": POLICY, "policy_test.go": TEST_OK},
            new_behavior=False, parent_module=True)
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertFalse(any(NOTE in note for note in result["notes"]), result["notes"])

    def test_a_link_in_the_base_is_not_treated_as_absent(self):
        project = Project({"reference/Policy.cs": CS})
        self.addCleanup(project.close)
        (project.root / "alias").symlink_to("reference/Policy.cs")
        verify._git(project.root, "add", "-A")
        verify._git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "link")
        project.base = verify._git(project.root, "rev-parse", "HEAD").strip()
        project.write({"go.mod": GO_MOD, "policy.go": POLICY, "policy_test.go": TEST_OK})
        result = project.verify(new_behavior=True)
        self.assertNotEqual(verify.PASS, result["verdict"], result)
        self.assertFalse(any(NOTE in note for note in result["notes"]), result["notes"])


if __name__ == "__main__":
    unittest.main()
