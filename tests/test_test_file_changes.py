"""A rename or a removal the plan approved, where the original test file can no longer be loaded.

Real runs, 2026-11-14: "rename page_count to count_pages everywhere" and "remove the deprecated legacy_total
and its tests" both delivered correct code and stopped for the person, because the original test file
imports the old name and so none of its tests can run against the change.
"""

import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_contract_identity as contracts
import autocode_protected_oracles as guard
import autocode_superseded_correction as correction
import autocode_superseded_tests as superseded
import autocode_test_file_changes as file_changes
import autocode_util as util
import autocode_verify as verify

PAGER = """def page_count(total, size):
    if size <= 0:
        raise ValueError("size must be positive")
    return -(-total // size)


def last_page_size(total, size):
    return 0 if total == 0 else total - (page_count(total, size) - 1) * size
"""
PAGER_TESTS = """import unittest

from pager import last_page_size, page_count

LIMIT = 5


class PagerTests(unittest.TestCase):
    def setUp(self):
        self.size = LIMIT

    def test_exact_multiple(self):
        self.assertEqual(2, page_count(10, self.size))

    def test_partial_last_page(self):
        self.assertEqual(3, page_count(11, self.size))

    def test_last_page_size(self):
        self.assertEqual(1, last_page_size(11, self.size))
"""
RENAMED = PAGER.replace("page_count", "count_pages")
RENAMED_TESTS = PAGER_TESTS.replace("page_count", "count_pages")
FILE = "tests/test_pager.py"
CALLERS = [{"file": FILE, "test": "test_exact_multiple"}, {"file": FILE, "test": "test_partial_last_page"}]

BILLING = """def total(lines):
    return sum(quantity * cents for quantity, cents in lines)


def legacy_total(lines):
    return total(lines) / 100
"""
BILLING_TESTS = """import unittest

from billing import legacy_total, total


class TotalTests(unittest.TestCase):
    def test_total_is_in_cents(self):
        self.assertEqual(1250, total([(2, 500), (1, 250)]))


class LegacyTotalTests(unittest.TestCase):
    def test_legacy_total_is_in_dollars(self):
        self.assertEqual(12.5, legacy_total([(2, 500), (1, 250)]))
"""
REMOVED = BILLING[: BILLING.index("\n\ndef legacy_total")]
# What the Builder of the real run wrote: the old class gone, the import narrowed, a test that the name is gone.
REMOVED_TESTS = """import unittest

from billing import total


class TotalTests(unittest.TestCase):
    def test_total_is_in_cents(self):
        self.assertEqual(1250, total([(2, 500), (1, 250)]))


class RemovalTests(unittest.TestCase):
    def test_ac1_legacy_total_is_removed(self):
        with self.assertRaises(ImportError):
            from billing import legacy_total  # noqa: F401
"""


class SummaryTests(unittest.TestCase):
    def test_a_rename_alters_the_tests_that_call_the_renamed_function(self):
        self.assertEqual(
            {
                "tests": ["test_exact_multiple", "test_last_page_size", "test_partial_last_page"],
                "altered": ["test_exact_multiple", "test_partial_last_page"],
                "intact": True,
            },
            file_changes.summary(PAGER_TESTS, RENAMED_TESTS),
        )

    def test_a_removal_alters_only_the_removed_tests(self):
        found = file_changes.summary(BILLING_TESTS, REMOVED_TESTS)
        self.assertEqual(["test_legacy_total_is_in_dollars"], found["altered"])
        self.assertTrue(found["intact"])

    def test_comments_layout_new_tests_and_new_names_leave_the_file_intact(self):
        changed = (
            RENAMED_TESTS.replace("LIMIT = 5\n", "LIMIT = 5  # items a page\n")
            .replace("import unittest\n", "import unittest\nimport math\n")
            .replace(
                "    def test_last_page_size(self):",
                "    def test_no_items(self):\n        self.assertEqual(0, count_pages(0, 5))\n\n"
                "    def test_last_page_size(self):",
            )
            + "\n\ndef helper():\n    return math.pi\n\n\nclass More(unittest.TestCase):\n    def test_more(self):\n        pass\n"
        )
        self.assertEqual(
            {"altered": ["test_exact_multiple", "test_partial_last_page"], "intact": True},
            {key: file_changes.summary(PAGER_TESTS, changed)[key] for key in ("altered", "intact")},
        )

    def test_anything_a_kept_test_reads_that_changed_is_not_intact(self):
        cases = {
            "a module constant": RENAMED_TESTS.replace("LIMIT = 5", "LIMIT = 6"),
            "a fixture": RENAMED_TESTS.replace("self.size = LIMIT", "self.size = LIMIT + 1"),
            "a class header": RENAMED_TESTS.replace("PagerTests(unittest.TestCase)", "PagerTests(object)"),
            "a new statement": RENAMED_TESTS + "\nlast_page_size = lambda total, size: 1\n",
            "an import of a bound name from elsewhere": RENAMED_TESTS.replace(
                "from pager import last_page_size, count_pages",
                "from pager import count_pages\nfrom fakes import last_page_size",
            ),
            "an alias for a bound name": RENAMED_TESTS.replace(
                "from pager import last_page_size, count_pages",
                "from pager import count_pages\nfrom pager import count_pages as last_page_size",
            ),
            "a star import": RENAMED_TESTS.replace("import unittest\n", "import unittest\nfrom fakes import *\n"),
            "a function over a bound name": RENAMED_TESTS + "\n\ndef last_page_size(total, size):\n    return 1\n",
            "a class over a bound name": RENAMED_TESTS + "\n\nclass LIMIT:\n    pass\n",
        }
        for name, changed in cases.items():
            with self.subTest(name):
                self.assertFalse(file_changes.summary(PAGER_TESTS, changed)["intact"])

    def test_helpers_may_go_but_not_change(self):
        helper = "\n\ndef invoice():\n    return [(2, 500), (1, 250)]\n\n\nclass Fake:\n    pass\n"
        original = BILLING_TESTS + helper
        self.assertTrue(file_changes.summary(original, REMOVED_TESTS + helper)["intact"])
        self.assertTrue(
            file_changes.summary(original, REMOVED_TESTS + "\n\nclass Fake:\n    pass\n")["intact"],
            "a function that is not a test may be removed",
        )
        self.assertFalse(file_changes.summary(original, REMOVED_TESTS + helper.replace("500", "400"))["intact"])
        self.assertFalse(
            file_changes.summary(original, REMOVED_TESTS + "\n\ndef invoice():\n    return [(2, 500), (1, 250)]\n")[
                "intact"
            ],
            "a class with no test is a helper the kept tests may use",
        )

    def test_a_test_moved_or_changed_is_altered(self):
        moved = BILLING_TESTS.replace("class LegacyTotalTests(unittest.TestCase):", "class Other(unittest.TestCase):")
        self.assertEqual(["test_legacy_total_is_in_dollars"], file_changes.summary(BILLING_TESTS, moved)["altered"])
        weakened = REMOVED_TESTS.replace("1250, total", "1251, total")
        self.assertEqual(
            ["test_legacy_total_is_in_dollars", "test_total_is_in_cents"],
            file_changes.summary(BILLING_TESTS, weakened)["altered"],
        )

    def test_a_deleted_file_alters_every_test(self):
        found = file_changes.summary(BILLING_TESTS, None)
        self.assertEqual(found["tests"], found["altered"])
        self.assertTrue(found["intact"])

    def test_a_file_this_cannot_read_has_no_summary(self):
        twice = BILLING_TESTS + "\n\nclass TotalTests(unittest.TestCase):\n    pass\n"
        for original, candidate in ((BILLING_TESTS, "def broken(:\n"), ("def broken(:\n", ""), (twice, REMOVED_TESTS)):
            self.assertIsNone(file_changes.summary(original, candidate))


def unloaded(test_id, passed=()):
    return {
        "exit_code": 1,
        "error": "",
        "timed_out": False,
        "results": {
            "passed": list(passed),
            "failed": [test_id],
            "skipped": [],
            "collection_errors": [test_id],
            "uncollected": [test_id],
            "total": 1 + len(passed),
            "complete": True,
        },
    }


class ExcusedTests(unittest.TestCase):
    changed = [FILE, "tests/test_report.py"]
    structure = {FILE: file_changes.summary(PAGER_TESTS, RENAMED_TESTS)}

    def test_each_runner_names_the_file_it_could_not_load(self):
        for test_id in (
            "unittest.loader._FailedTest.test_pager",
            "unittest.loader._FailedTest.tests.test_pager",
            "unittest.loader.ModuleImportFailure.tests.test_pager",
            "::tests.test_pager",
            "::tests/test_pager.py",
        ):
            with self.subTest(test_id):
                self.assertEqual(FILE, superseded.unloaded_file(test_id, self.changed))
        for test_id in (
            "unittest.loader._FailedTest.test_other",  # not a changed file
            "tests.test_pager.PagerTests.test_exact_multiple",  # a test, not a file
            "tests/test_pager.py::test_exact_multiple",
            "unittest.loader._FailedTest.pager",  # tests/test_pager.py is not the module pager
        ):
            with self.subTest(test_id):
                self.assertIsNone(superseded.unloaded_file(test_id, self.changed))
        # Two changed files with the module's name: which one failed is not known.
        self.assertIsNone(
            superseded.unloaded_file("unittest.loader._FailedTest.test_pager", [FILE, "other/test_pager.py"])
        )

    def test_a_file_that_cannot_load_is_excused_when_the_plan_replaces_every_altered_test(self):
        row = unloaded("unittest.loader._FailedTest.test_pager")
        self.assertEqual(row["results"]["failed"], superseded.excused(row, CALLERS, self.changed, self.structure))

    def test_it_is_not_excused_otherwise(self):
        row = unloaded("unittest.loader._FailedTest.test_pager")
        weakened = file_changes.summary(PAGER_TESTS, RENAMED_TESTS.replace("1, last_page_size", "2, last_page_size"))
        cases = {
            "no comparison of the two files": (CALLERS, None),
            "an altered test the plan does not list": (CALLERS[:1], self.structure),
            "a kept test whose assertion changed": (CALLERS, {FILE: weakened}),
            "the rest of the file changed": (CALLERS, {FILE: {**self.structure[FILE], "intact": False}}),
            "a comparison of another file": (CALLERS, {"tests/test_report.py": self.structure[FILE]}),
        }
        for name, (entries, structure) in cases.items():
            with self.subTest(name):
                self.assertIsNone(superseded.excused(row, entries, self.changed, structure))
        # A file the change left alone, or one that is no changed file, is never excused.
        self.assertIsNone(superseded.excused(row, CALLERS, ["tests/test_report.py"], self.structure))
        other = unloaded("unittest.loader._FailedTest.test_other")
        self.assertIsNone(superseded.excused(other, CALLERS, self.changed, self.structure))


class GateTests(unittest.TestCase):
    """The original-test replay, the completion re-check and the runner's own plan correction, on real
    unittest projects."""

    def project(self, files):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve() / "project"
        self.write({"tests/__init__.py": "", **files})
        git = ["git", "-C", str(self.root)]
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "-qm", "o"], check=True)
        self.run_dir = self.root / ".autocode/runs/renamed"
        command = shlex.join([sys.executable, "-m", "unittest", "discover", "-v"])
        self.state = {"settings": {}, "workspace": str(self.root), "user_events": []}
        guard.reconcile(
            self.state,
            self.state["settings"],
            SimpleNamespace(run_dir=None),
            self.root,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: command,
        )

    def write(self, files):
        for name, text in files.items():
            path = self.root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)

    def approve(self, supersedes, test="test_exact_multiple"):
        row = {"id": "AC1", "criterion": "renamed", "verification_method": "test: " + test, "supersedes": supersedes}
        contract = {
            "task_id": "t",
            "revision": 1,
            "body": {"acceptance_criteria": [row], "open_blocking_questions": []},
        }
        contract["hash"] = util.digest(contract)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": contracts.token(contract)}
        contract.update(approval_status="approved", approval_event=event, origin="planner_draft")
        self.state["goal_contract"] = contract
        self.state["user_events"] = [event]
        self.assertTrue(contracts.approved(self.state))

    def replay(self):
        return guard.replay(self.state, self.root, self.run_dir / "replays", verify.scratch_run, timeout=30)

    def ready(self, result):
        self.state["validation"] = {
            "check_replay": {"verdict": "PASS", "source_revision": result["source_revision"], "protected_tests": result}
        }
        return guard.ready(self.state, result["source_revision"])

    def renamed(self, tests=RENAMED_TESTS):
        self.project({"pager.py": PAGER, FILE: PAGER_TESTS})
        self.write({"pager.py": RENAMED, FILE: tests})

    def test_a_rename_passes_when_the_plan_replaces_the_tests_that_call_the_old_name(self):
        self.renamed()
        self.approve(CALLERS)
        result = self.replay()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(0, result["candidate"]["exit_code"])
        self.assertEqual(["unittest.loader._FailedTest.test_pager"], result["superseded"]["excused_failures"])
        self.assertEqual(["test_exact_multiple", "test_partial_last_page"], result["structure"][FILE]["altered"])
        self.assertTrue(self.ready(result))
        # A later plan that replaces fewer tests cannot reuse the receipt.
        self.approve(CALLERS[:1])
        self.assertFalse(guard.ready(self.state, result["source_revision"]))

    def test_a_rename_is_refused_while_an_altered_test_is_not_listed(self):
        self.renamed()
        self.approve(CALLERS[:1])
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.replay()

    def test_a_kept_test_weakened_under_cover_of_the_rename_is_refused(self):
        # last_page_size is broken and its test, which the plan does not list, is changed to agree.
        self.renamed(RENAMED_TESTS.replace("1, last_page_size", "0, last_page_size"))
        self.write({"pager.py": RENAMED.replace("return 0 if total == 0 else", "return 0 if total else")})
        self.approve(CALLERS)
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.replay()

    def test_a_changed_fixture_under_cover_of_the_rename_is_refused(self):
        # Every test still passes with the change and the plan lists them all; the fixture is not a test.
        self.renamed(RENAMED_TESTS.replace("LIMIT = 5", "LIMIT = 5.0"))
        self.approve([*CALLERS, {"file": FILE, "test": "test_last_page_size"}])
        with self.assertRaises(guard.commands.OriginalGateFailure) as caught:
            self.replay()
        self.assertEqual(0, caught.exception.receipt["candidate"]["exit_code"])
        self.assertFalse(caught.exception.receipt["structure"][FILE]["intact"])

    def test_a_rename_whose_tests_fail_is_refused(self):
        self.renamed(RENAMED_TESTS.replace("2, count_pages", "3, count_pages"))
        self.approve(CALLERS)
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.replay()

    def test_a_removal_passes_when_the_plan_replaces_the_removed_tests(self):
        self.project({"billing.py": BILLING, "tests/test_billing.py": BILLING_TESTS})
        self.write({"billing.py": REMOVED, "tests/test_billing.py": REMOVED_TESTS})
        entry = {"file": "tests/test_billing.py", "test": "test_legacy_total_is_in_dollars"}
        self.approve([entry], "test_ac1_legacy_total_is_removed")
        result = self.replay()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual([{"criterion": "AC1", **entry}], result["superseded"]["tests"])
        self.assertTrue(self.ready(result))

    def test_the_runner_drafts_the_plan_that_lists_the_tests_a_rename_altered(self):
        self.renamed()
        self.approve([])
        with self.assertRaises(guard.commands.OriginalGateFailure) as caught:
            self.replay()
        found = correction.proposal(self.state, caught.exception.receipt, self.root)
        self.assertEqual([{"criterion": "AC1", **entry} for entry in CALLERS], found["tests"])
        self.assertEqual(CALLERS, found["body"]["acceptance_criteria"][0]["supersedes"])
        self.assertIn("can no longer be loaded against the change", found["changes"][0]["replacement"])
        # With that plan approved the same change passes the gate.
        self.approve(found["body"]["acceptance_criteria"][0]["supersedes"])
        self.assertEqual("PASS", self.replay()["verdict"])

    def test_the_runner_drafts_nothing_when_the_rest_of_the_file_changed(self):
        self.renamed(RENAMED_TESTS.replace("LIMIT = 5", "LIMIT = 6"))
        self.approve([])
        with self.assertRaises(guard.commands.OriginalGateFailure) as caught:
            self.replay()
        self.assertIsNone(correction.proposal(self.state, caught.exception.receipt, self.root))


if __name__ == "__main__":
    unittest.main()
