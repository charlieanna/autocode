"""A bug-fix test that uses a seam the fix adds cannot fail on the unfixed code (#299).

etcd #22498: the fix fsynced a directory through a package variable it added, and the
tests spied on that variable. On the unfixed code they did not compile, so the proof failed
with a generic "write the test against behavior that exists before the fix". The proof must
stay non-passing, but name the seam and the way out: a test through APIs that exist before
the fix that drives the real failure path. These fixtures run real unittest and Go suites in
scratch Git worktrees; no provider is launched.
"""
from __future__ import annotations

import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_proof_seam as proof_seam
import autocode_regression as regression
import autocode_verify as verify

from tests.test_verify import Project

STORE = '''import os


def save(directory, name, data):
    """Write data to directory/name through a temporary file and a rename."""
    temporary = os.path.join(directory, name + ".tmp")
    with open(temporary, "w") as handle:
        handle.write(data)
    try:
        os.rename(temporary, os.path.join(directory, name))
    except OSError:
        pass
'''
# The fix stops swallowing the failed rename, and adds replace_file, a seam a test can patch.
FIXED_STORE = '''import os

replace_file = os.rename


def save(directory, name, data):
    """Write data to directory/name through a temporary file and a rename."""
    temporary = os.path.join(directory, name + ".tmp")
    with open(temporary, "w") as handle:
        handle.write(data)
    replace_file(temporary, os.path.join(directory, name))
'''
TESTS = '''import os
import tempfile
import unittest
from unittest import mock

import store


class SaveTests(unittest.TestCase):
    def test_saves(self):
        with tempfile.TemporaryDirectory() as directory:
            store.save(directory, "a", "x")
            with open(os.path.join(directory, "a")) as handle:
                self.assertEqual("x", handle.read())
'''
SEAM_TEST = TESTS.replace("import store\n", "import store\nfrom store import replace_file\n") + '''
    def test_t1_rename_failure_is_raised(self):
        with mock.patch.object(store, "replace_file", side_effect=OSError("disk full")) as spy, \\
                tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                store.save(directory, "a", "x")
        spy.assert_called_once()
        self.assertIs(os.rename, replace_file)
'''
# The same seam, reached only at run time: nothing imports it, so the module loads on the unfixed code and
# the test errors there ("does not have the attribute") instead of failing on the bug.
RUNTIME_SEAM_TEST = TESTS + '''
    def test_t1_rename_failure_is_raised(self):
        with mock.patch.object(store, "replace_file", side_effect=OSError("disk full")) as spy, \\
                tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(OSError):
                store.save(directory, "a", "x")
        spy.assert_called_once()
'''
# The same failure, reached through save() as it exists before the fix: a directory is in the way.
BEHAVIOR_TEST = TESTS + '''
    def test_t1_rename_failure_is_raised(self):
        with tempfile.TemporaryDirectory() as directory:
            os.makedirs(os.path.join(directory, "a", "kept"))
            with self.assertRaises(OSError):
                store.save(directory, "a", "x")
'''
CASE = {"id": "T1", "criterion": "Given a directory where the file goes; when save runs; then it raises OSError",
        "verification_method": "test: test_t1_rename_failure_is_raised"}


def bugfix_state(project):
    return {"base_commit": project.base, "settings": {}, "iteration": 1, "stages": [], "history": [],
            "goal_contract": {"body": {"task_kind": "bugfix", "acceptance_criteria": [CASE],
                                       "milestones": [{"id": "M1"}]}}}


class PythonSeamProofTests(unittest.TestCase):
    def prove(self, files, seed=None):
        project = Project(seed or {"store.py": STORE, "test_store.py": TESTS})
        self.addCleanup(project.close)
        project.write(files)
        run_dir = Path(tempfile.mkdtemp(prefix="seam-proof-"))
        self.addCleanup(shutil.rmtree, run_dir, True)
        return regression.prove(bugfix_state(project), project.root, run_dir)

    def test_a_test_that_imports_the_fixs_seam_fails_and_says_why(self):
        proof = self.prove({"store.py": FIXED_STORE, "test_store.py": SEAM_TEST})
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertEqual([], proof["fail_to_pass"])
        reason = next(failure for failure in proof["failures"] if "only fail to import or collect" in failure)
        self.assertIn("because they use replace_file, which only the fix adds", reason)
        self.assertIn("drive the real failure path through its existing public APIs", reason)
        self.assertIn("A log line or message alone does not prove the behavior", reason)
        self.assertIn("instrumentation-only base patch", reason)
        self.assertNotIn("Write the regression test against behavior that exists before the fix", reason)
        # Genuine application errors remain eligible even when a changed name appears in the trace.
        self.assertNotIn("cannot pass this proof", reason)
        self.assertIn("only because replace_file is missing there is not a reproduction, even if it reaches "
                      "replace_file at run time", reason)

    def test_runtime_mock_preparation_is_not_a_reproduction(self):
        proof = self.prove({"store.py": FIXED_STORE, "test_store.py": RUNTIME_SEAM_TEST})
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertEqual([], proof["fail_to_pass"])
        self.assertTrue(any("test_t1_rename_failure_is_raised" in reason and "prepare its mock" in reason
                            and "replace_file" in reason for reason in proof["failures"]), proof)

    def test_broken_product_cannot_pass_by_testing_an_unused_mock(self):
        broken = STORE.replace("import os", "import os\nreplace_file = os.rename")
        mock_only = TESTS + '''
    def test_t1_rename_failure_is_raised(self):
        with mock.patch.object(store, "replace_file", side_effect=OSError("injected")) as spy:
            with self.assertRaises(OSError):
                store.replace_file("unused", "unused")
        spy.assert_called_once()
'''
        proof = self.prove({"store.py": broken, "test_store.py": mock_only})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual([], proof["fail_to_pass"])

    def test_runtime_import_is_not_a_bug_reproduction(self):
        runtime_import = SEAM_TEST.replace("from store import replace_file\n", "").replace(
            "        with mock.patch.object", "        from store import replace_file\n        with mock.patch.object")
        proof = self.prove({"store.py": FIXED_STORE, "test_store.py": runtime_import})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual([], proof["fail_to_pass"])
        self.assertTrue(any("could not import its dependency" in reason for reason in proof["failures"]), proof)

    def test_genuine_product_attribute_error_still_proves_a_fix(self):
        existing_source = "def stable():\n    return 9\n"
        existing_tests = ("import unittest\nimport store\nclass Existing(unittest.TestCase):\n"
                          "    def test_stable(self): self.assertEqual(9, store.stable())\n")
        seed = {"store.py": existing_source + "def read():\n    return None.missing\n",
                "test_store.py": existing_tests}
        tests = existing_tests + "class ReadTests(unittest.TestCase):\n" \
                "    def test_t1_rename_failure_is_raised(self):\n        self.assertEqual(1, store.read())\n"
        proof = self.prove({"store.py": existing_source + "def read():\n    return 1\n",
                            "test_store.py": tests}, seed)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test_store.ReadTests.test_t1_rename_failure_is_raised"], proof["fail_to_pass"])
        self.assertIn("test_store.Existing.test_stable", proof["pass_to_pass"])

    def test_public_attribute_assertion_without_product_trace_is_still_proof(self):
        existing_tests = ("import unittest\nimport store\nclass Existing(unittest.TestCase):\n"
                          "    def test_kind(self): self.assertEqual('value', store.Value().kind)\n")
        seed = {"store.py": "class Value:\n    kind = 'value'\n", "test_store.py": existing_tests}
        tests = existing_tests + "class ReadTests(unittest.TestCase):\n" \
                "    def test_t1_rename_failure_is_raised(self):\n        self.assertEqual(1, store.Value().value)\n"
        proof = self.prove({"store.py": "class Value:\n    kind = 'value'\n    value = 1\n",
                            "test_store.py": tests}, seed)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test_store.ReadTests.test_t1_rename_failure_is_raised"], proof["fail_to_pass"])
        self.assertIn("test_store.Existing.test_kind", proof["pass_to_pass"])

    def test_runtime_setup_failure_cannot_borrow_another_cases_behavior_proof(self):
        # One setup-only T1 and one actual behavior test cannot satisfy T1.
        tests = RUNTIME_SEAM_TEST + "    def test_other" + BEHAVIOR_TEST.split("    def test_t1", 1)[1]
        proof = self.prove({"store.py": FIXED_STORE, "test_store.py": tests})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual(["test_store.SaveTests.test_other_rename_failure_is_raised"], proof["fail_to_pass"])
        self.assertTrue(any("T1" in reason for reason in proof["failures"]), proof)
        self.assertTrue(any("test_t1_rename_failure_is_raised" in note and "prepare its mock" in note
                            for note in proof["notes"]), proof)

    def test_feature_proof_still_allows_a_new_mockable_api(self):
        project = Project({"store.py": STORE, "test_store.py": TESTS})
        self.addCleanup(project.close)
        project.write({"store.py": FIXED_STORE, "test_store.py": RUNTIME_SEAM_TEST})
        proof = project.verify(new_behavior=True)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual(["test_store.SaveTests.test_t1_rename_failure_is_raised"], proof["fail_to_pass"])

    def test_a_test_that_reads_the_seam_while_loading_is_unverified_and_names_it(self):
        # An AttributeError while loading the module stops unittest before it reports any test.
        loads_seam = RUNTIME_SEAM_TEST.replace("import store\n", "import store\nORIGINAL = store.replace_file\n")
        proof = self.prove({"store.py": FIXED_STORE, "test_store.py": loads_seam})
        self.assertEqual(verify.UNVERIFIED, proof["verdict"])
        reason = next((reason for reason in proof["unverified"] if "replace_file" in reason), None)
        self.assertIsNotNone(reason, proof["unverified"])
        self.assertIn("reported no test results", reason)
        self.assertIn("because they use replace_file, which only the fix adds", reason)

    def test_a_behavior_test_through_the_existing_api_is_not_flagged(self):
        proof = self.prove({"store.py": FIXED_STORE, "test_store.py": BEHAVIOR_TEST})
        self.assertFalse(any("replace_file" in reason for reason in proof["review_reasons"]),
                         proof["review_reasons"])

    def test_the_same_fix_is_proven_by_a_test_through_the_existing_api(self):
        proof = self.prove({"store.py": FIXED_STORE, "test_store.py": BEHAVIOR_TEST})
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(["test_store.SaveTests.test_t1_rename_failure_is_raised"], proof["fail_to_pass"])
        self.assertEqual({"T1": ["test_store.SaveTests.test_t1_rename_failure_is_raised"]}, proof["case_tests"])

    def test_a_seam_test_beside_a_real_reproduction_is_named_as_not_counted(self):
        seam_only = SEAM_TEST.replace("test_t1_rename_failure_is_raised", "test_rename_is_wired")
        proof = self.prove({"store.py": FIXED_STORE, "test_store.py": BEHAVIOR_TEST, "test_seam.py": seam_only})
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertTrue(any("Not counted as proof" in note and "replace_file" in note for note in proof["notes"]),
                        proof["notes"])

    def test_a_collection_error_with_no_seam_keeps_the_generic_reason(self):
        broken = STORE.replace("import os\n", "import os\nimport legacy_backend\n")  # the fix removes it
        proof = self.prove({"store.py": FIXED_STORE, "test_store.py": BEHAVIOR_TEST},
                           seed={"store.py": broken, "test_store.py": TESTS})
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertTrue(any("Write the regression test against behavior that exists before the fix" in failure
                            for failure in proof["failures"]), proof["failures"])
        self.assertFalse(any("which only the fix adds" in failure for failure in proof["failures"]))


@unittest.skipUnless(verify._python_can_import(sys.executable, "pytest"), "pytest is not installed")
class PytestSeamProofTests(unittest.TestCase):
    def test_real_junit_missing_mock_target_is_not_proof(self):
        project = Project({"store.py": STORE, "test_store.py": TESTS,
                           "pytest.ini": "[pytest]\naddopts = --confcutdir=.\n"})
        self.addCleanup(project.close)
        project.write({"store.py": FIXED_STORE, "test_store.py": RUNTIME_SEAM_TEST})
        framework = verify.detect_framework(project.root, python=sys.executable)
        baseline = verify.baseline(project.root, project.base, project.evidence, framework=framework,
                                   suite_command=framework.suite, timeout=30)
        proof = verify.verify(project.root, project.base, project.evidence, framework=framework,
                              base_suite=baseline, timeout=30)
        self.assertEqual("pytest", framework.name)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertEqual([], proof["fail_to_pass"])
        self.assertTrue(any("prepare its mock" in reason for reason in proof["failures"]), proof)


GO_STORE = '''package store

import (
	"os"
	"path/filepath"
)

// Save writes data to dir/name through a temporary file and a rename.
func Save(dir, name string, data []byte) error {
	tmp := filepath.Join(dir, name+".tmp")
	if err := os.WriteFile(tmp, data, 0o644); err != nil {
		return err
	}
	os.Rename(tmp, filepath.Join(dir, name))
	return nil
}
'''
GO_FIXED = GO_STORE.replace("// Save", "var renameFile = os.Rename\n\n// Save").replace(
    "\tos.Rename(tmp, filepath.Join(dir, name))\n\treturn nil\n",
    "\treturn renameFile(tmp, filepath.Join(dir, name))\n")
GO_TESTS = '''package store

import (
	%s"os"
	"path/filepath"
	"testing"
)

func TestSaveWrites(t *testing.T) {
	dir := t.TempDir()
	if err := Save(dir, "a", []byte("x")); err != nil {
		t.Fatal(err)
	}
	if got, _ := os.ReadFile(filepath.Join(dir, "a")); string(got) != "x" {
		t.Fatalf("read %%q", got)
	}
}
'''
GO_SEAM_TEST = GO_TESTS % '"errors"\n\t' + '''
func Test_t1_rename_failure_is_returned(t *testing.T) {
	renameFile = func(string, string) error { return errors.New("disk full") }
	defer func() { renameFile = os.Rename }()
	if err := Save(t.TempDir(), "a", []byte("x")); err == nil {
		t.Fatal("Save returned nil although the rename failed")
	}
}
'''
GO_BEHAVIOR_TEST = GO_TESTS % "" + '''
func Test_t1_rename_failure_is_returned(t *testing.T) {
	dir := t.TempDir()
	if err := os.MkdirAll(filepath.Join(dir, "a", "kept"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := Save(dir, "a", []byte("x")); err == nil {
		t.Fatal("Save returned nil although the rename failed")
	}
}
'''
GO_SEED = {"go.mod": "module store\n\ngo 1.21\n", "store.go": GO_STORE, "store_test.go": GO_TESTS % ""}


@unittest.skipUnless(shutil.which("go"), "needs a Go toolchain")
class GoSeamProofTests(unittest.TestCase):
    def verify(self, test):
        project = Project(GO_SEED)
        self.addCleanup(project.close)
        project.write({"store.go": GO_FIXED, "store_test.go": test})
        return project.verify()

    def test_a_go_test_that_sets_the_fixs_package_variable_fails_and_says_why(self):
        result = self.verify(GO_SEAM_TEST)
        self.assertEqual(verify.FAIL, result["verdict"])
        self.assertEqual([], result["fail_to_pass"])
        self.assertTrue(any("store::[build failed]" in failure and "use renameFile, which only the fix adds" in failure
                            for failure in result["failures"]), result["failures"])

    def test_the_same_go_fix_is_proven_through_the_existing_api(self):
        result = self.verify(GO_BEHAVIOR_TEST)
        self.assertEqual(verify.PASS, result["verdict"], result["failures"] + result["unverified"])
        self.assertEqual(["store::Test_t1_rename_failure_is_returned"], result["fail_to_pass"])


class SeamNameTests(unittest.TestCase):
    def test_missing_names_are_read_from_compiler_and_import_errors(self):
        output = "\n".join((
            '{"Action":"build-output","Output":"./store_test.go:18:2: undefined: renameFile\\n"}',
            "./x_test.go:3: undefined: store.Hook",
            "ImportError: cannot import name 'replace_file' from 'store' (/tmp/store.py)",
            "AttributeError: module 'store' has no attribute 'flush_dir'",
            "ModuleNotFoundError: No module named 'store.sync'",
            "AttributeError: <module 'store' from '/tmp/store.py'> does not have the attribute 'fsync_dir'",
            "SyntaxError: invalid syntax",
        ))
        self.assertEqual({"renameFile", "Hook", "replace_file", "flush_dir", "sync", "fsync_dir"},
                         proof_seam.missing_names(output))

    def test_added_names_come_from_the_changed_lines_even_when_a_comment_already_used_the_word(self):
        before = "// rename the file\nfunc Save() {\n\tos.Rename(a, b)\n}\n"
        after = "// rename the file\nvar rename = os.Rename\nfunc Save() {\n\trename(a, b)\n}\n"
        added = proof_seam.added_names("store.go", before, after)
        self.assertIn("rename", added)
        self.assertNotIn("Rename", added)  # also on a removed line
        self.assertIn("sync", proof_seam.added_names("store/sync.py", "", "def flush():\n    pass\n"))
        self.assertIn("hooks", proof_seam.added_names("store/hooks/__init__.py", "", "X = 1\n"))

    def test_the_seam_is_missing_added_and_used_by_the_tests(self):
        output = "undefined: renameFile\nundefined: helper\n"
        self.assertEqual(["renameFile"], proof_seam.used(output, {"renameFile", "Save"}, {"renameFile", "helper"}))
        self.assertEqual([], proof_seam.used(output, {"renameFile"}, {"Save"}))


if __name__ == "__main__":
    unittest.main()
