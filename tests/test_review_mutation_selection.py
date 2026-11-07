"""Selected-test review mutations: baseline authority, bounded runs and safe cleanup (#699)."""

import os
import tempfile
import unittest
from pathlib import Path

import autocode_command_receipt as command_receipt
import autocode_mutation_check as mutation_check
import autocode_python_tests as python_tests

INSIDE = "tests.test_window.WindowTests.test_includes_lower_bound"
OUTSIDE = "tests.test_window.WindowTests.test_excludes_upper_bound"
SELECTED = [INSIDE, OUTSIDE]
COMMAND = "/usr/bin/python3 -m unittest discover -s tests -v"
PRODUCT = (
    "def in_window(n, lo, hi):\n"
    '    """Half-open window."""\n'
    "    if lo > hi:\n"
    '        raise ValueError("empty")\n'
    "    return lo <= n < hi\n"
)


def receipt(passed=SELECTED, *, complete=True, failed=(), skipped=(), exit_code=0, duration=0.5):
    return {
        "exit_code": exit_code,
        "timed_out": False,
        "duration_seconds": duration,
        "results": {
            "complete": complete,
            "passed": list(passed),
            "failed": list(failed),
            "skipped": list(skipped),
        },
    }


class Clock:
    def __init__(self, now=0.0):
        self.now = now

    def __call__(self):
        return self.now


class StubRun:
    """Returns recorded test outcomes and observes the actual product bytes at each invocation."""

    def __init__(self, tree, *, selected=SELECTED, respond=None):
        self.tree = tree
        self.selected = list(selected)
        self.respond = respond
        self.calls = []

    def __call__(self, command, label, timeout):
        self.calls.append(
            {
                "command": command,
                "label": label,
                "timeout": timeout,
                "source": (self.tree / "app.py").read_bytes(),
            }
        )
        if self.respond is not None:
            value = self.respond(label, timeout)
            if value is not None:
                return value
        return receipt(self.selected)


class SelectedReviewMutationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="review-mutation-selection-")
        self.addCleanup(temporary.cleanup)
        self.tree = Path(temporary.name) / "tree"
        self.tree.mkdir()
        self.target = self.tree / "app.py"
        self.target.write_text(PRODUCT)
        os.utime(self.target, ns=(1_577_836_800_000_000_000, 1_577_836_800_123_456_789))
        self.original = self.target.read_bytes()
        self.original_mtime = self.target.stat().st_mtime_ns
        self.evidence = Path(temporary.name) / "evidence"
        self.clock = Clock()

    @staticmethod
    def diff(files):
        return "".join(
            f"diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n@@ -2,0 +3,3 @@\n" for path, _ in files
        )

    def collect(self, run, *, selected=SELECTED, framework="unittest", command=COMMAND, **options):
        return mutation_check.collect_selected_tests(
            framework,
            command,
            selected,
            self.tree,
            self.evidence,
            options.pop("files", [("app.py", "modified")]),
            options.pop("diff_text", self.diff),
            run,
            clock=self.clock,
            **options,
        )

    @staticmethod
    def mutants(check):
        return [mutant for row in check["files"] for mutant in row["mutants"]]

    def assert_restored(self):
        self.assertEqual(self.original, self.target.read_bytes())
        self.assertEqual(self.original_mtime, self.target.stat().st_mtime_ns)

    def test_review_selected_ids_need_no_matching_case_or_fabricated_proof(self):
        run = StubRun(self.tree)
        check = self.collect(run)
        self.assertTrue(check["collected"], check)
        self.assertEqual({"collected", "note", "limit_per_file", "budget_seconds", "files"}, set(check))
        self.assertEqual((6, 900), (check["limit_per_file"], check["budget_seconds"]))
        self.assertEqual("unmutated", run.calls[0]["label"])
        self.assertEqual(self.original, run.calls[0]["source"])
        self.assertTrue(all(call["source"] != self.original for call in run.calls[1:]))
        self.assertTrue(self.mutants(check))
        for row in self.mutants(check):
            self.assertCountEqual(SELECTED, row["survived_tests"])
        self.assert_restored()

    def test_every_unittest_invocation_selects_only_the_reviewed_ids(self):
        run = StubRun(self.tree)
        self.collect(run, command="env LANG=C " + COMMAND)
        caches = []
        for call in run.calls:
            invocation = python_tests.parse(call["command"])
            self.assertIsNotNone(invocation)
            self.assertEqual("unittest", invocation.kind)
            # Discovery keeps its import root and options; each reviewed ID is one -k selector (63bae126).
            arguments = list(invocation.arguments)
            selectors = [arguments[index + 1] for index, word in enumerate(arguments) if word == "-k"]
            options = [word for index, word in enumerate(arguments) if word != "-k" and arguments[index - 1] != "-k"]
            self.assertEqual(["discover", "-s", "tests", "-v"], options)
            self.assertCountEqual(SELECTED, selectors)
            self.assertEqual(("env", "LANG=C"), invocation.prefix[:2])
            caches.extend(word for word in invocation.prefix if word.startswith("PYTHONPYCACHEPREFIX="))
        self.assertEqual(len(run.calls), len(set(caches)))
        self.assertGreater(len(run.calls), 1)

    def test_pytest_judges_selected_node_ids_without_a_case_name_convention(self):
        selected = [
            "tests/test_window.py::TestWindow::test_lower_bound[closed]",
            "tests/test_window.py::TestWindow::test_upper_bound[open]",
        ]
        run = StubRun(self.tree, selected=selected)
        check = self.collect(run, selected=selected, framework="pytest", command="python3 -m pytest tests -q")
        self.assertTrue(check["collected"], check)
        for call in run.calls:
            invocation = python_tests.parse(call["command"])
            self.assertEqual("pytest", invocation.kind)
            self.assertTrue(
                "-k" in invocation.arguments or all(test in invocation.arguments for test in selected),
                "A whole-suite fallback cannot replace selected-test execution",
            )
        for row in self.mutants(check):
            self.assertCountEqual(selected, row["survived_tests"])

    def test_baseline_must_completely_and_positively_report_every_selected_id(self):
        bad = {
            "incomplete": receipt(complete=False),
            "selected_failure": receipt([INSIDE], failed=[OUTSIDE], exit_code=1),
            "exit_nonzero": receipt(exit_code=1),
            "skips_only": receipt([], skipped=SELECTED),
            "one_omitted": receipt([INSIDE]),
            "one_skipped": receipt([INSIDE], skipped=[OUTSIDE]),
            "different_id": receipt(["tests.test_window.WindowTests.test_other"]),
            "unreported": {"exit_code": 0, "timed_out": False, "results": None},
            "timeout": {**receipt(), "timed_out": True},
            "error": {**receipt(), "error": "fixture command unavailable"},
        }
        for name, baseline in bad.items():
            with self.subTest(baseline=name):
                run = StubRun(self.tree, respond=lambda label, timeout, value=baseline: value)
                check = self.collect(run)
                self.assertEqual(["unmutated"], [call["label"] for call in run.calls])
                self.assertFalse(check["collected"], check)
                self.assertTrue(check["note"])
                self.assertFalse(self.mutants(check))
                self.assert_restored()

    def test_mutants_keep_the_existing_judge_outcomes_for_selected_tests(self):
        outcomes = {
            "mutant-01": receipt([INSIDE], failed=[OUTSIDE], exit_code=1),
            "mutant-02": receipt(),
            "mutant-03": {"exit_code": None, "timed_out": True, "results": None},
            "mutant-04": {"error": "fixture receipt missing"},
            "mutant-05": receipt([INSIDE]),
            "mutant-06": {"exit_code": 7, "timed_out": False, "results": None},
        }
        run = StubRun(self.tree, respond=lambda label, timeout: outcomes.get(label))
        check = self.collect(run)
        self.assertEqual(
            ["killed", "survived", "timeout", "not_run", "not_run", "killed"],
            [row["outcome"] for row in self.mutants(check)],
        )
        self.assertCountEqual(SELECTED, self.mutants(check)[1]["survived_tests"])
        self.assert_restored()

    def test_unsupported_or_unselectable_commands_never_fall_back_to_the_whole_suite(self):
        for options in (
            {"framework": "go", "command": "go test ./..."},
            {"command": COMMAND + " | tee results.log"},
            {"selected": []},
        ):
            with self.subTest(options=options):
                run = StubRun(self.tree)
                check = self.collect(run, **options)
                self.assertEqual([], run.calls)
                self.assertFalse(check["collected"])
                self.assertTrue(check["note"])
                self.assert_restored()

    def test_no_eligible_changed_mutants_runs_no_baseline(self):
        self.target.write_text("value = 1\n")
        run = StubRun(self.tree)
        check = self.collect(run)
        self.assertEqual([], run.calls)
        self.assertFalse(check["collected"])
        self.assertIn("mutation site", check["note"])
        self.assertEqual(b"value = 1\n", self.target.read_bytes())

    def test_each_changed_file_keeps_the_six_mutant_bound(self):
        other = self.tree / "other.py"
        other.write_text(PRODUCT)
        run = StubRun(self.tree)
        check = self.collect(run, files=[("app.py", "modified"), ("other.py", "modified")])
        self.assertTrue(check["collected"], check)
        self.assertEqual(["app.py", "other.py"], [row["path"] for row in check["files"]])
        self.assertEqual([6, 6], [len(row["mutants"]) for row in check["files"]])
        self.assertEqual(self.original, other.read_bytes())
        self.assert_restored()

    def test_expired_absolute_deadline_admits_no_command(self):
        self.clock.now = 100
        run = StubRun(self.tree)
        check = self.collect(run, deadline=99)
        self.assertEqual([], run.calls)
        self.assertTrue(check["note"])
        self.assertEqual(900, check["budget_seconds"])
        self.assert_restored()

    def test_external_deadline_charges_preparation_and_unmutated_time(self):
        self.clock.now = 100

        def prepare(files):
            self.clock.now += 30
            return self.diff(files)

        def baseline(label, timeout):
            self.assertEqual("unmutated", label)
            self.clock.now += 20
            return receipt(duration=20)

        run = StubRun(self.tree, respond=baseline)
        check = self.collect(run, deadline=200, diff_text=prepare)
        self.assertEqual([("unmutated", 70)], [(call["label"], call["timeout"]) for call in run.calls])
        self.assertTrue(all(row["outcome"] == "not_run" for row in self.mutants(check)))
        self.assertTrue(check["note"])
        self.assertEqual(900, check["budget_seconds"])
        self.assert_restored()

    def test_a_later_external_deadline_cannot_extend_the_normal_budget(self):
        self.clock.now = 100

        def respond(label, timeout):
            if label == "unmutated":
                self.clock.now += 900
                return receipt(duration=900)
            return None

        run = StubRun(self.tree, respond=respond)
        check = self.collect(run, deadline=5000)
        self.assertEqual(900, run.calls[0]["timeout"])
        self.assertEqual(["unmutated"], [call["label"] for call in run.calls])
        self.assertTrue(all(row["outcome"] == "not_run" for row in self.mutants(check)))
        self.assert_restored()

    def test_mutant_timeout_and_exhausted_shared_budget_are_distinguished(self):
        self.clock.now = 20

        def respond(label, timeout):
            if label == "unmutated":
                self.clock.now += 20
                return receipt(duration=10)
            self.clock.now += timeout
            return {"exit_code": None, "timed_out": True, "results": None}

        run = StubRun(self.tree, respond=respond)
        check = self.collect(run, deadline=200)
        self.assertEqual([180, 100, 60], [call["timeout"] for call in run.calls])
        self.assertEqual(["timeout", *(["not_run"] * 5)], [row["outcome"] for row in self.mutants(check)])
        self.assertTrue(check["note"])
        self.assert_restored()

    def test_ordinary_runner_and_planning_exceptions_only_add_report_notes(self):
        for phase in ("planning", "unmutated", "mutant-02"):
            with self.subTest(phase=phase):

                def respond(label, timeout):
                    if label == phase:
                        raise OSError("fixture log unavailable")
                    return None

                def prepare(files):
                    if phase == "planning":
                        raise RuntimeError("fixture diff unavailable")
                    return self.diff(files)

                run = StubRun(self.tree, respond=respond)
                check = self.collect(run, diff_text=prepare)
                self.assertFalse(check["collected"])
                notes = [check["note"] or "", *(note for row in check["files"] for note in row["notes"])]
                self.assertTrue(any("fixture" in note for note in notes), check)
                self.assert_restored()

    def test_unknown_preparation_ownership_is_not_reduced_to_a_file_note(self):
        error = command_receipt.OwnershipUncertain("fixture preparation still owns its checkout")

        def prepare(files):
            raise error

        run = StubRun(self.tree)
        with self.assertRaises(command_receipt.OwnershipUncertain) as caught:
            self.collect(run, diff_text=prepare)
        self.assertIs(error, caught.exception)
        self.assertEqual([], run.calls)
        self.assert_restored()

    def test_unknown_unmutated_ownership_propagates_without_admitting_mutants(self):
        error = command_receipt.OwnershipUncertain("fixture owner remains live")

        def uncertain(label, timeout):
            raise error

        run = StubRun(self.tree, respond=uncertain)
        with self.assertRaises(command_receipt.OwnershipUncertain) as caught:
            self.collect(run)
        self.assertIs(error, caught.exception)
        self.assertEqual(["unmutated"], [call["label"] for call in run.calls])
        self.assert_restored()

    def test_unknown_mutant_ownership_propagates_and_keeps_its_tree_untouched(self):
        error = command_receipt.OwnershipUncertain("fixture mutant still owns the checkout")

        def uncertain(label, timeout):
            if label == "mutant-01":
                raise error
            return None

        run = StubRun(self.tree, respond=uncertain)
        with self.assertRaises(command_receipt.OwnershipUncertain) as caught:
            self.collect(run)
        self.assertIs(error, caught.exception)
        self.assertEqual(["unmutated", "mutant-01"], [call["label"] for call in run.calls])
        self.assertNotEqual(self.original, self.target.read_bytes())
        self.assertEqual(run.calls[-1]["source"], self.target.read_bytes())


if __name__ == "__main__":
    unittest.main()
