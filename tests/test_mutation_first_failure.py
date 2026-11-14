"""A detected mutation stops redundant tests without admitting incomplete proof."""

import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_mutation_check as mutation
import autocode_mutation_results as mutation_results
import autocode_verify as verify

from tests import test_changed_code_measurement as fixtures

TESTS = fixtures.TESTS


class ExecutionTests(unittest.TestCase):
    def test_only_a_killed_mutant_stops_before_later_selected_tests(self):
        for framework, discover in (("unittest", False), ("unittest", True), ("pytest", False)):
            with self.subTest(framework=framework, discover=discover), tempfile.TemporaryDirectory() as folder:
                tree = Path(folder)
                original = "def value():\n    return True\ndef unused():\n    return True\n"
                (tree / "app.py").write_text(original)
                (tree / "test_value.py").write_text(
                    "import unittest\nfrom pathlib import Path\nfrom app import value\n"
                    "class ValueTests(unittest.TestCase):\n"
                    "    def test_a_value(self):\n        self.assertTrue(value())\n"
                    "    def test_z_later(self):\n        Path('later').write_text('ran')\n"
                )
                selected = [
                    "test_value.ValueTests.test_a_value",
                    "test_value.ValueTests.test_z_later",
                ]
                if framework == "pytest":
                    selected = [name.rsplit(".", 1)[0] + "::" + name.rsplit(".", 1)[1] for name in selected]
                calls = []

                def run(command, label, timeout):
                    marker = tree / "later"
                    marker.unlink(missing_ok=True)
                    env = dict(os.environ)
                    env.pop("PYTHONDONTWRITEBYTECODE", None)
                    xml = tree / (label + ".xml")
                    args = shlex.split(command)
                    if framework == "pytest":
                        args.append("--junitxml=" + str(xml))
                    result = subprocess.run(args, cwd=tree, env=env, capture_output=True, text=True, timeout=timeout)
                    output = result.stdout + result.stderr
                    capture = tree / (label + ".log")
                    capture.write_text(output)
                    results = verify.per_test_results(
                        SimpleNamespace(name=framework, per_test=True, suite=command),
                        {"command": command, "output": str(capture)},
                        xml,
                        tree=tree,
                    )
                    calls.append((label, marker.exists(), result.returncode, results))
                    return {
                        "command": shlex.join(args),
                        "output": str(capture),
                        "exit_code": result.returncode,
                        "timed_out": False,
                        "duration_seconds": 0.1,
                        "results": results,
                    }

                check = mutation.collect_selected_tests(
                    framework,
                    shlex.join(
                        [sys.executable, "-m", framework, *(["discover"] if discover else []), "-v"]
                        + ([] if discover else ["test_value.py"])
                    )
                    + (" --maxfail=0" if framework == "pytest" else ""),
                    selected,
                    tree,
                    tree / "evidence",
                    [("app.py", "modified")],
                    lambda files: "diff --git a/app.py b/app.py\n+++ b/app.py\n@@ -2,3 +2,3 @@\n",
                    run,
                    enforcement=True,
                )
                self.assertTrue(check["collected"], check)
                self.assertTrue(calls[0][1], "the unchanged reference runs every selected test")
                killed = [call for call in calls[1:] if call[2] != 0]
                self.assertTrue(killed)
                self.assertTrue(all(not call[1] for call in killed), "later tests must not run after a kill")
                survivors = [call for call in calls[1:] if call[2] == 0]
                self.assertTrue(survivors)
                self.assertTrue(all(call[1] for call in survivors), "survivors run every selected test")
                self.assertEqual(
                    {"killed", "survived"}, {row["outcome"] for file in check["files"] for row in file["mutants"]}
                )
                self.assertEqual(original, (tree / "app.py").read_text())

    def test_positional_options_and_unrecognized_commands_do_not_authorize_early_stop(self):
        command = "python -m unittest -v -- -f"
        self.assertEqual(command, mutation_results.stop_command(command))
        for candidate in (command, "make test", "python -m pytest -k --maxfail=1", "python -m unittest -v -k -f"):
            self.assertFalse(mutation_results.stops_after_failure(candidate), candidate)


class EvidenceTests(unittest.TestCase):
    setUp = fixtures.MeasurementTests.setUp
    receipt = fixtures.MeasurementTests.receipt
    mutation = fixtures.MeasurementTests.mutation
    normalize_mutation = fixtures.MeasurementTests.normalize_mutation

    def early_failure(self):
        report, receipts = self.mutation()
        extra = "tests.test_value.ValueTests.test_later"
        report["selected_tests"] = TESTS + [extra]
        reference = receipts[0]["results"]
        reference.update(passed=TESTS + [extra], total=len(TESTS) + 1)
        report["execution_policy"] = "stop_after_selected_failure_v1"
        receipts[1]["command"] = "python -m unittest -f -v tests.test_value"
        return report, receipts

    def test_owned_selected_failure_counts_after_complete_reference(self):
        report, receipts = self.early_failure()
        row = self.normalize_mutation(report, receipts)["files"][0]["mutants"][0]
        self.assertEqual("killed", row["outcome"])
        self.assertEqual(TESTS, row["failed_tests"])

    def test_early_stop_never_supplies_missing_baseline_or_survivor_evidence(self):
        for change in (
            lambda report, receipts: report.pop("execution_policy"),
            lambda report, receipts: report.update(execution_policy="unknown"),
            lambda report, receipts: receipts[1].update(command="python -m unittest tests.test_value"),
            lambda report, receipts: receipts[0]["results"].update(passed=TESTS, total=len(TESTS)),
            lambda report, receipts: receipts[1]["results"].update(setup_errors=TESTS),
            lambda report, receipts: receipts[1]["results"].update(collection_errors=TESTS),
            lambda report, receipts: receipts[1]["results"].update(complete=False),
            lambda report, receipts: receipts[1]["results"].update(skipped=["skipped.test"], total=2),
            lambda report, receipts: receipts[1]["results"].update(passed=TESTS, total=2),
            lambda report, receipts: receipts[1]["results"].update(failed=["unknown.test"]),
            lambda report, receipts: receipts[1].update(timed_out=True),
            lambda report, receipts: receipts[1].pop("supervision"),
            lambda report, receipts: Path(receipts[1]["output"]).write_text("tampered"),
        ):
            with self.subTest(change=change):
                report, receipts = self.early_failure()
                change(report, receipts)
                self.assertEqual(
                    "not_run", self.normalize_mutation(report, receipts)["files"][0]["mutants"][0]["outcome"]
                )
        report, receipts = self.early_failure()
        receipts[1]["results"].update(passed=TESTS, failed=[])
        receipts[1]["exit_code"] = 0
        report["files"][0]["mutants"][0].update(outcome="survived", failed_tests=[])
        self.assertEqual("not_run", self.normalize_mutation(report, receipts)["files"][0]["mutants"][0]["outcome"])
