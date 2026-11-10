"""Real Go collection of a probe command, using scripted answers."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_verify as verify

GO = shutil.which("go")
SOURCE = "package pager\n\nfunc PageCount(total, size int) int { return total / size }\n"
TEST = """package pager
import (
    "flag"
    "testing"
)
var jsonArgument = flag.String("json", "", "literal test program argument")
func Test_t1_truncationProbe(t *testing.T) {
    if PageCount(5, 2) != 2 { t.Fatal("expected the reported truncation bug") }
}
func Test_skipProbe(t *testing.T) {
    t.Skip("reproduction prerequisite is unavailable")
}
func Test_jsonArgument(t *testing.T) {
    if *jsonArgument != "false" { t.Fatalf("json argument changed: %q", *jsonArgument) }
}
func Test_argumentAfterDelimiter(t *testing.T) {
    if args := flag.Args(); len(args) != 1 || args[0] != "-json=false" {
        t.Fatalf("literal test arguments changed: %q", args)
    }
}
"""


@unittest.skipUnless(GO, "needs a Go toolchain")
class GoReproductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "GOCACHE" in os.environ:
            cache = os.environ["GOCACHE"]
            if not Path(cache).is_absolute():
                raise RuntimeError("An explicitly supplied GOCACHE must be an absolute directory")
            cls.cache_path = cache
        else:
            cls.cache_directory = tempfile.TemporaryDirectory(prefix="autocode-go-probe-cache-")
            cls.addClassCleanup(cls.cache_directory.cleanup)
            cls.cache_path = cls.cache_directory.name

    def setUp(self):
        if artifacts := os.environ.get("BUILD_AUDIT_ARTIFACTS"):
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=artifacts)).resolve()
        else:
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            self.root = Path(directory.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        for name, text in {
            "go.mod": "module example.test/pager\n\ngo 1.20\n",
            "pager.go": SOURCE,
            "pager_test.go": TEST,
            ".gitignore": ".autocode/\n",
        }.items():
            (self.workspace / name).write_text(text)
        for args in (
            ("init", "-q"),
            ("add", "."),
            ("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "buggy pager"),
        ):
            subprocess.run(["/usr/bin/git", *args], cwd=self.workspace, check=True, capture_output=True)
        self.environment = {
            **os.environ,
            "GOCACHE": self.cache_path,
            "GOPROXY": "off",
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "local",
            "GOWORK": "off",
            "GOFLAGS": "",
            "AUTOCODE_HOME": str(self.root / "registry"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.patch_environment = mock.patch.dict(os.environ, self.environment)
        self.patch_environment.start()
        self.addCleanup(self.patch_environment.stop)

    def command(self, selector, *flags, executable=GO):
        # The executable is absolute, matching the live command that bypassed
        # zero-collection rejection. Quoted selectors retain their exact argv.
        return shlex.join([executable, "test", ".", "-run", selector, "-count=1", "-timeout=20s", *flags])

    def scratch(self, command):
        run_dir = self.workspace / ".autocode/probe"
        run_dir.mkdir(parents=True)
        receipt = verify.scratch_run(self.workspace, run_dir, command=command, timeout=30)
        (self.root / "scratch-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        return receipt

    def test_absolute_go_empty_selector_is_rejected_despite_native_zero(self):
        receipt = self.scratch(self.command("^TestMissing$"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertFalse(receipt["timed_out"], receipt)
        self.assertIsInstance(receipt["results"], dict, receipt)
        self.assertEqual(0, receipt["results"]["total"], receipt)
        self.assertIn("zero tests or incomplete per-test results", receipt["error"])
        self.assertIn("-json", shlex.split(receipt["command"]))

    def test_absolute_go_named_probe_has_complete_native_identity(self):
        receipt = self.scratch(self.command("^Test_t1_truncationProbe$"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertFalse(receipt["timed_out"], receipt)
        self.assertEqual("", receipt["error"], receipt)
        self.assertEqual(["example.test/pager::Test_t1_truncationProbe"], receipt["results"]["passed"])
        self.assertEqual(1, receipt["results"]["total"])
        self.assertTrue(receipt["results"]["complete"])

    def test_plain_go_all_skipped_probe_is_rejected_with_complete_named_results(self):
        # The untouched runner already recognizes this literal command. The
        # regression isolates skipped-only acceptance from absolute Go parsing.
        receipt = self.scratch(self.command("Test_skipProbe", executable="go"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertFalse(receipt["timed_out"], receipt)
        self.assertEqual(["example.test/pager::Test_skipProbe"], receipt["results"]["skipped"])
        self.assertEqual([], receipt["results"]["passed"])
        self.assertEqual([], receipt["results"]["failed"])
        self.assertEqual(1, receipt["results"]["total"])
        self.assertTrue(receipt["results"]["complete"])
        self.assertIn("only skipped tests", receipt["error"])

    def test_mixed_real_pass_and_skip_probe_keeps_complete_native_collection(self):
        receipt = self.scratch(self.command("^(Test_t1_truncationProbe|Test_skipProbe)$"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertFalse(receipt["timed_out"], receipt)
        self.assertEqual("", receipt["error"], receipt)
        self.assertEqual(["example.test/pager::Test_t1_truncationProbe"], receipt["results"]["passed"])
        self.assertEqual(["example.test/pager::Test_skipProbe"], receipt["results"]["skipped"])
        self.assertEqual(2, receipt["results"]["total"])
        self.assertTrue(receipt["results"]["complete"])

    def test_disabled_json_does_not_become_collection_proof(self):
        receipt = self.scratch(self.command("^Test_t1_truncationProbe$", "-json=false"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertIsNone(receipt["results"], receipt)
        self.assertIn("zero tests or incomplete per-test results", receipt["error"])
        self.assertIn("-json=false", shlex.split(receipt["command"]))

    def test_leading_and_later_enabled_reporters_keep_real_collection(self):
        for position in ("leading", "later"):
            with self.subTest(position=position):
                words = shlex.split(self.command("^Test_t1_truncationProbe$"))
                if position == "leading":
                    words.insert(2, "-json=true")
                else:
                    words.append("-json=true")
                # Each receipt has its own output root; never parse an earlier log.
                run_dir = self.workspace / ".autocode" / position
                run_dir.mkdir(parents=True)
                receipt = verify.scratch_run(self.workspace, run_dir, command=shlex.join(words), timeout=30)
                self.assertEqual(0, receipt["exit_code"], receipt)
                self.assertEqual("", receipt["error"], receipt)
                self.assertEqual(["example.test/pager::Test_t1_truncationProbe"], receipt["results"]["passed"])
                self.assertEqual(
                    words if position == "leading" else [*words[:2], "-json", *words[2:]],
                    shlex.split(receipt["command"]),
                )

    def test_json_named_test_program_argument_does_not_disable_outer_reporter(self):
        receipt = self.scratch(self.command("^Test_jsonArgument$", "-args", "-json=false"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertEqual("", receipt["error"], receipt)
        self.assertEqual(["example.test/pager::Test_jsonArgument"], receipt["results"]["passed"])

    def test_test_program_delimiter_keeps_literal_arguments_and_real_collection(self):
        receipt = self.scratch(self.command("^Test_argumentAfterDelimiter$", "-args", "--", "-json=false"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertEqual("", receipt["error"], receipt)
        self.assertEqual(["example.test/pager::Test_argumentAfterDelimiter"], receipt["results"]["passed"])

    def test_malformed_json_flag_remains_an_authentic_native_failure(self):
        receipt = self.scratch(self.command("^Test_t1_truncationProbe$", "-json=invalid"))
        self.assertNotEqual(0, receipt["exit_code"], receipt)
        self.assertIsNone(receipt["results"], receipt)

    def test_plain_assertion_probe_remains_valid_without_test_collection(self):
        command = shlex.join(
            [
                sys.executable,
                "-B",
                "-c",
                "from pathlib import Path; assert 'return total / size' in Path('pager.go').read_text()",
            ]
        )
        receipt = self.scratch(command)
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertEqual("", receipt["error"], receipt)
        self.assertIsNone(receipt["results"])


if __name__ == "__main__":
    unittest.main()
