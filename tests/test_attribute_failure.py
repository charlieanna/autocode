"""A red suite names whose failure it is (#2603)."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import attribute_failure
import run_suite

from .ci_failure_fixture import failure_output, ref_failure_output


class FirstSkipTests(unittest.TestCase):
    def test_the_first_skip_reason_is_named_and_absent_when_nothing_skipped(self):
        self.assertEqual("needs Linux", run_suite.first_skip("... skipped 'needs Linux' ... skipped 'other'"))
        self.assertEqual("", run_suite.first_skip("Ran 3 tests\n\nOK"))
        self.assertEqual("", run_suite.first_skip(""))


class ReportTests(unittest.TestCase):
    def test_the_table_says_which_modules_are_this_branchs_to_fix(self):
        text = attribute_failure.report([("tests.a", "pre-existing"), ("tests.b", "introduced")])
        self.assertIn("| `tests.a` | fails | pre-existing |", text)
        self.assertIn("| `tests.b` | passes | introduced |", text)
        self.assertIn("1 introduced", text)
        self.assertIn("`tests.b`", text)

    def test_reproduced_failures_do_not_exonerate_unexamined_changes(self):
        text = attribute_failure.report([("tests.a", "pre-existing")])
        self.assertIn("observed head failures", text)
        self.assertNotIn("Nothing here is this branch's to fix", text)

    def test_unknown_and_different_failures_are_not_reported_as_a_passing_base(self):
        text = attribute_failure.report(
            [("tests.a", "unresolved (base also fails)"), ("tests.b", "introduced (different failures)")]
        )
        self.assertIn("| `tests.a` | fails | unresolved", text)
        self.assertIn("| `tests.b` | fails | introduced", text)


class ClassifyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        (self.base / "tests").mkdir()

    def module(self, name, body):
        (self.base / "tests" / f"{name}.py").write_text(body)

    def test_a_module_the_base_does_not_have_cannot_be_the_bases_failure(self):
        self.assertEqual(attribute_failure.NEW_MODULE, attribute_failure.classify("tests.absent", self.base, 60))

    def test_a_red_module_without_head_failure_evidence_stays_unresolved(self):
        self.module(
            "broken",
            "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.fail('already red')\n",
        )
        self.assertEqual(attribute_failure.UNRESOLVED, attribute_failure.classify("tests.broken", self.base, 60))

    def test_only_matching_head_failures_are_classified_as_pre_existing(self):
        self.module("value", "# fixture exists\n")
        head = attribute_failure.failure_signatures.signatures(failure_output("/head"), Path("/head"))
        result = type("Probe", (), {"returncode": 1, "stdout": "", "stderr": failure_output(str(self.base))})()
        with mock.patch.object(attribute_failure.subprocess, "run", return_value=result):
            self.assertEqual(
                attribute_failure.PRE_EXISTING,
                attribute_failure.classify("tests.value", self.base, 60, head_failures=head),
            )
            head += attribute_failure.failure_signatures.signatures(
                failure_output("/head", method="test_other"), Path("/head")
            )
            self.assertEqual(
                attribute_failure.DIFFERENT,
                attribute_failure.classify("tests.value", self.base, 60, head_failures=head),
            )

    def test_a_changed_assertion_is_not_hidden_by_the_same_failing_test_name(self):
        self.module("value", "# fixture exists\n")
        head = attribute_failure.failure_signatures.signatures(
            failure_output("/head", message="new failure"), Path("/head")
        )
        result = type("Probe", (), {"returncode": 1, "stdout": "", "stderr": failure_output(str(self.base))})()
        with mock.patch.object(attribute_failure.subprocess, "run", return_value=result):
            self.assertEqual(
                attribute_failure.DIFFERENT,
                attribute_failure.classify("tests.value", self.base, 60, head_failures=head),
            )

    def test_a_fresh_generated_start_ref_is_the_same_pre_existing_failure(self):
        self.module("value", "# fixture exists\n")
        prefix = "refs/autocode/partial-work/start/"
        head = attribute_failure.failure_signatures.signatures(
            ref_failure_output("/head", prefix + "a" * 40), Path("/head")
        )
        output = ref_failure_output(str(self.base), prefix + "b" * 40)
        probe = type("Probe", (), {"returncode": 1, "stdout": "", "stderr": output})()
        with mock.patch.object(attribute_failure.subprocess, "run", return_value=probe):
            self.assertEqual(
                attribute_failure.PRE_EXISTING,
                attribute_failure.classify("tests.value", self.base, 60, head_failures=head),
            )
            probe.stderr = output.replace("self.assertTrue(allowed, ref)", "self.assertTrue(verified, ref)")
            self.assertEqual(
                attribute_failure.DIFFERENT,
                attribute_failure.classify("tests.value", self.base, 60, head_failures=head),
            )

    def test_incomplete_base_output_cannot_exonerate_matching_failures(self):
        self.module("value", "# fixture exists\n")
        output = failure_output(str(self.base)).split("Ran 1 test")[0]
        head = attribute_failure.failure_signatures.signatures(failure_output("/head"), Path("/head"))
        result = type("Probe", (), {"returncode": 1, "stdout": "", "stderr": output})()
        with mock.patch.object(attribute_failure.subprocess, "run", return_value=result):
            self.assertEqual(
                attribute_failure.INCOMPLETE,
                attribute_failure.classify("tests.value", self.base, 60, head_failures=head),
            )

    def test_base_setup_failure_and_timeout_are_unresolved(self):
        import subprocess

        self.module("value", "# fixture exists\n")
        for response in (
            type("Probe", (), {"returncode": 1, "stdout": "", "stderr": "could not start Python"})(),
            subprocess.TimeoutExpired("python", 60),
        ):
            with (
                self.subTest(response=response),
                mock.patch.object(
                    attribute_failure.subprocess,
                    "run",
                    **({"side_effect": response} if isinstance(response, Exception) else {"return_value": response}),
                ),
            ):
                self.assertIn("unresolved", attribute_failure.classify("tests.value", self.base, 60))

    def test_a_module_that_passes_on_the_base_was_introduced_here(self):
        self.module("fine", "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n")
        self.assertEqual(attribute_failure.INTRODUCED, attribute_failure.classify("tests.fine", self.base, 60))

    @unittest.skipUnless(os.name == "posix", "base-attribution workflow uses a POSIX tool fixture")
    def test_base_probe_keeps_test_configuration_and_path_without_comment_credentials(self):
        binary = self.base / "fixture-bin"
        binary.mkdir()
        executable = binary / "attribution-fixture-tool"
        executable.write_text("#!/bin/sh\nexit 0\n")
        executable.chmod(0o755)
        self.module(
            "environment",
            "import os,subprocess,unittest\nclass T(unittest.TestCase):\n"
            "    def test_environment(self):\n"
            "        self.assertEqual('present', os.environ.get('CI_ATTRIBUTION_FIXTURE'))\n"
            "        self.assertNotIn('GH_TOKEN', os.environ)\n"
            "        self.assertNotIn('GITHUB_TOKEN', os.environ)\n"
            "        subprocess.run(['attribution-fixture-tool'], check=True)\n",
        )
        with mock.patch.dict(
            os.environ,
            {
                "PATH": str(binary) + os.pathsep + os.environ["PATH"],
                "CI_ATTRIBUTION_FIXTURE": "present",
                "GH_TOKEN": "fixture-comment-token",
                "GITHUB_TOKEN": "fixture-comment-token",
            },
        ):
            self.assertEqual(
                attribute_failure.INTRODUCED, attribute_failure.classify("tests.environment", self.base, 60)
            )

    def test_the_module_sees_the_base_checkout_not_this_branch(self):
        # The verdict must come from the base's own copy: the same module name with a
        # different result there is what makes the two verdicts mean anything.
        self.module(
            "moved", "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        self.fail()\n"
        )
        self.assertEqual(attribute_failure.UNRESOLVED, attribute_failure.classify("tests.moved", self.base, 60))
        (self.base / "tests" / "moved.py").write_text(
            "import unittest\nclass T(unittest.TestCase):\n    def test_x(self):\n        pass\n"
        )
        self.assertEqual(attribute_failure.INTRODUCED, attribute_failure.classify("tests.moved", self.base, 60))


class FailedOutTests(unittest.TestCase):
    def test_the_failed_module_list_is_written_for_the_attribution_pass(self):
        import json
        from unittest import mock

        with tempfile.TemporaryDirectory() as scratch:
            out = Path(scratch) / "failed.json"
            red = {"module": "tests.red", "ok": False, "tests": 1, "seconds": 0.0, "output": "FAIL"}
            green = {"module": "tests.green", "ok": True, "tests": 1, "seconds": 0.0, "output": "OK"}
            with mock.patch.object(run_suite, "run_module", side_effect=[red, green]):
                run_suite.run_parallel(["tests.red", "tests.green"], 1, 0, failed_out=out)
            self.assertEqual(["tests.red"], [row["module"] for row in json.loads(out.read_text())["failures"]])

    def test_serial_runs_also_write_the_head_failure_report(self):
        import contextlib
        import io
        import json

        class Broken(unittest.TestCase):
            def runTest(self):
                self.fail("fixture failure")

        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / "failed.json"
            with (
                mock.patch.object(run_suite, "compile_runtime"),
                mock.patch.object(run_suite, "discover", return_value=unittest.TestSuite([Broken()])),
                mock.patch.object(run_suite, "load_exclusions", return_value={}),
                mock.patch.object(run_suite, "test_modules", return_value=[__name__]),
                contextlib.redirect_stdout(io.StringIO()),
                contextlib.redirect_stderr(io.StringIO()),
            ):
                self.assertEqual(1, run_suite.main(["--jobs", "1", "--failed-out", str(output)]))
            self.assertEqual(__name__, json.loads(output.read_text())["failures"][0]["module"])


class AttributionCliTests(unittest.TestCase):
    def test_timed_out_head_is_not_exonerated_by_matching_partial_failures(self):
        import contextlib
        import io
        import json

        with tempfile.TemporaryDirectory() as temporary:
            failed = Path(temporary) / "failed.json"
            failed.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "failures": [{"module": "tests.test_value", "failures": [{"test": "old"}], "timed_out": True}],
                    }
                )
            )
            with (
                mock.patch.object(attribute_failure.subprocess, "run"),
                mock.patch.object(
                    attribute_failure, "classify", return_value=attribute_failure.PRE_EXISTING
                ) as classify,
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                self.assertEqual(0, attribute_failure.main(["--base", "base", "--failed", str(failed)]))
            classify.assert_not_called()
            self.assertIn("unresolved (head incomplete)", output.getvalue())

    def test_current_failure_report_detects_a_new_failure_beside_an_existing_one(self):
        import contextlib
        import io
        import json

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "tests").mkdir()
            (root / "tests/__init__.py").touch()
            (root / "tests/test_value.py").write_text(
                "import unittest, subprocess\nclass ValueTests(unittest.TestCase):\n"
                "    def test_value(self):\n"
                "        subprocess.run(['git', 'config', 'attribution.probe', 'touched'], check=True)\n"
                "        self.fail('old failure')\n"
            )
            for args in (
                ["init", "-q"],
                ["add", "."],
                ["-c", "user.name=Fixture", "-c", "user.email=fixture@example.test", "commit", "-qm", "base"],
            ):
                subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)
            base = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
            original_config = (root / ".git/config").read_bytes()
            failed = root / "head-failed.json"
            failed.write_text(
                json.dumps(
                    {
                        "version": 1,
                        "failures": [
                            {
                                "module": "tests.test_value",
                                "failures": [
                                    {"test": "tests.test_value.ValueTests.test_new", "kind": "FAIL", "signature": "new"}
                                ],
                            }
                        ],
                    }
                )
            )
            with contextlib.chdir(root), contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(0, attribute_failure.main(["--base", base, "--failed", str(failed)]))
            self.assertEqual(original_config, (root / ".git/config").read_bytes())
            self.assertIn("introduced (different failures)", output.getvalue())


if __name__ == "__main__":
    unittest.main()
