"""Links among a project's tests that are data, not code: a task can start, is proven, and the gate keeps them.

A project whose tests hold a link to a directory (``tests/data/current -> v2``), or a link a test of link
handling needs dangling, absolute or looping, could start no task at all: ``autocode: Protected test link
target is not in the source snapshot: tests/data/v2`` (exit 2, before any model call). Such a link is bound as
its text. The regression proof refused the absolute, outside and looping ones for the suite's definition
("does not bind an in-tree input"), and gave a link the change adds no role ("ownership is ambiguous"). A test
file that is itself a link is still held to a snapshot file (tests.test_protected_test_links).

These cases bind real projects, run real regression proofs and the real original-test gate; the last starts a
run through the CLI.
tools/autocode_protected_paths.py (collect), tools/autocode_protected_store.py (_links),
tools/autocode_suite_definition_inputs.py (linked_inputs), tools/autocode_python_test_roles.py (_classify)
"""

import json
import os
import shlex
import sys
import unittest

import autocode_protected_oracles as guard
import autocode_protected_store as store
import autocode_taskrun as taskrun
import autocode_util as util
import autocode_verify as verify

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS
from .test_verify import Project, git

REPORT = "def render(rows):\n    return ''.join(f'{name}: {count}\\n' for name, count in rows)\n"
PADDED = REPORT.replace("{name}: {count}", "{name:<6}{count:>4}")
TESTS = (
    "import unittest\nfrom pathlib import Path\n\nfrom report import render\n\n"
    "DATA = Path(__file__).with_name('data')\n\n\n"
    "class ReportTests(unittest.TestCase):\n    def test_report_matches_the_current_sample(self):\n"
    "        self.assertEqual((DATA / 'current' / 'report.txt').read_text(), render([('pen', 12)]))\n\n"
    "    def test_no_rows_is_an_empty_report(self):\n        self.assertEqual('', render([]))\n"
)
FILES = {
    "report.py": REPORT,
    "samples/a.txt": "a\n",
    "tests/__init__.py": "",
    "tests/test_report.py": TESTS,
    "tests/data/v2/report.txt": "pen: 12\n",
}
# The links a real project's tests hold: a fixture directory, and what a test of link handling reads.
LINKS = {
    "tests/data/current": "v2",
    "tests/data/missing": "nowhere",
    "tests/data/host": "/etc/hostname",
    "tests/data/up": "../../../outside",
    "tests/data/loop-a": "loop-b",
    "tests/data/loop-b": "loop-a",
    "tests/data/through": "current/report.txt",
    "tests/shared": "../samples",
}
IDENTITY = ("-c", "user.name=t", "-c", "user.email=t@example.test")


def link(root, links=LINKS):
    for name, target in links.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(target)


class DataLinkTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(FILES)
        self.addCleanup(self.project.close)
        self.root = self.project.root
        link(self.root)
        git(self.root, "add", ".")
        git(self.root, *IDENTITY, "commit", "-qm", "links")
        self.command = shlex.join([sys.executable, "-B", "-m", "unittest", "discover", "-v"])
        self.record = guard.retain(
            self.root, self.project.evidence, guard.inventory(self.root, verify.is_test_path), self.command
        )
        self.state = {"settings": {"protected_tests": self.record}}

    def replay(self):
        return guard.replay(self.state, self.root, self.project.evidence / "replays", verify.scratch_run, timeout=30)

    def rejected_receipt(self):
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.replay()
        receipts = list((self.project.evidence / "replays").glob("protected-tests/*/receipt.json"))
        self.assertEqual(1, len(receipts))
        receipt = json.loads(receipts[0].read_text())
        self.assertEqual(0, receipt["candidate"]["exit_code"], receipt["candidate"])
        self.assertEqual(1, receipt["original"]["exit_code"], receipt["original"])
        return receipt

    def test_a_data_link_is_bound_as_its_text(self):
        files = self.record["files"]
        self.assertEqual({name: {"symlink": target} for name, target in LINKS.items()}, {n: files[n] for n in LINKS})
        # A linked directory's files are eligible under their own names; one outside the tests is not a test.
        self.assertIn("sha256", files["tests/data/v2/report.txt"])
        self.assertNotIn("samples/a.txt", files)
        self.assertEqual(2, self.record["version"])
        self.assertEqual(self.record, guard.verify_binding(self.record))
        with store.opened(self.record, self.root) as retained:
            for name, target in LINKS.items():
                self.assertEqual(target, os.readlink(retained / name))
            self.assertEqual("pen: 12\n", (retained / "tests/data/current/report.txt").read_text())

    def test_the_original_tests_run_with_the_original_links(self):
        (self.root / "tests/test_report.py").write_text(
            TESTS
            + "\n    def test_two_rows(self):\n        self.assertEqual(2, render([('a', 1), ('b', 2)]).count('\\n'))\n"
        )
        result = self.replay()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(["tests/test_report.py"], result["changed_tests"])
        self.assertEqual(0, result["original"]["exit_code"], result["original"])
        self.assertEqual(LINKS, {name: os.readlink(self.root / name) for name in LINKS})

    def test_a_retargeted_directory_link_cannot_replace_the_expected_output(self):
        (self.root / "report.py").write_text(PADDED)
        (self.root / "tests/data/v3").mkdir()
        (self.root / "tests/data/v3/report.txt").write_text("pen     12\n")
        current = self.root / "tests/data/current"
        current.unlink()
        current.symlink_to("v3")
        receipt = self.rejected_receipt()
        self.assertIn("tests/data/current", receipt["changed_tests"])
        self.assertIn("test_report_matches_the_current_sample", receipt["original"]["tail"])
        self.assertEqual("v3", os.readlink(current))

    def test_a_directory_put_in_place_of_the_link_gives_way_to_the_original_link(self):
        (self.root / "report.py").write_text(PADDED)
        current = self.root / "tests/data/current"
        current.unlink()
        current.mkdir()
        (current / "report.txt").write_text("pen     12\n")
        receipt = self.rejected_receipt()
        self.assertIn("tests/data/current", receipt["changed_tests"])
        self.assertEqual("pen     12\n", (current / "report.txt").read_text())
        self.assertEqual("pen: 12\n", (self.root / "tests/data/v2/report.txt").read_text())

    def test_an_unbound_source_link_beside_the_bound_data_links_is_still_refused(self):
        (self.root / "tests/test_report.py").write_text(
            TESTS + "\n    def test_one_more(self):\n        self.assertEqual(1, len(render([('a', 1)]))\n"
        )
        (self.root / "tests/data/extra").symlink_to("/etc/hostname")
        with self.assertRaises(util.PinnedSourceLinkRefusal) as held:
            self.replay()
        self.assertEqual("protected", held.exception.policy)

    def test_code_that_is_a_link_to_no_file_of_the_repository_is_still_refused(self):
        for links in (
            {"tests/test_host.py": "/etc/hostname"},
            {"tests/test_up.py": "../../outside.py"},
            {"tests/test_gone.py": "nowhere.py"},
            {"tests/helpers.py": "data"},
            {"tests/test_a.py": "test_b.py", "tests/test_b.py": "test_a.py"},
        ):
            with self.subTest(links=links):
                link(self.root, links)
                with self.assertRaises(ValueError):
                    guard.inventory(self.root, verify.is_test_path)
                for name in links:
                    (self.root / name).unlink()

    def test_a_record_without_the_file_a_code_link_leads_to_is_still_refused(self):
        (self.root / "tests/test_alias.py").symlink_to("test_report.py")
        files = guard.inventory(self.root, verify.is_test_path)
        self.assertEqual({"symlink": "test_report.py"}, files["tests/test_alias.py"])
        del files["tests/test_report.py"]
        with self.assertRaisesRegex(ValueError, "source snapshot"):
            guard.retain(self.root, self.project.evidence / "partial", files, self.command)


# Python 3.11 has no quote of the string's own kind inside an f-string's braces.
DASHED = REPORT.replace("{count}", '{"-" if count is None else count}')
DASH_TEST = (
    "\n    def test_a_missing_count_is_a_dash(self):\n        self.assertEqual('pen: -\\n', render([('pen', None)]))\n"
)
ALIAS_TEST = DASH_TEST + (
    "        self.assertEqual((DATA / 'alias' / 'report.txt').read_text(), render([('pen', 12)]))\n"
)
NEW_TEST = "tests.test_report.ReportTests.test_a_missing_count_is_a_dash"


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(FILES)
        self.addCleanup(self.project.close)
        self.root = self.project.root
        link(self.root)
        git(self.root, "add", ".")
        git(self.root, *IDENTITY, "commit", "-qm", "links")
        self.project.base = git(self.root, "rev-parse", "HEAD")

    def test_a_change_is_proven_in_a_project_whose_fixtures_are_links(self):
        self.project.write({"report.py": DASHED, "tests/test_report.py": TESTS + DASH_TEST})
        result = self.project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([NEW_TEST], result["fail_to_pass"])
        self.assertEqual(2, len(result["pass_to_pass"]))

    def test_a_link_the_change_adds_as_a_fixture_is_one_of_its_test_files(self):
        self.project.write({"report.py": DASHED, "tests/test_report.py": TESTS + ALIAS_TEST})
        link(self.root, {"tests/data/alias": "v2", "tests/data/lost": "nowhere", "tests/data/root": "/"})
        result = self.project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual([NEW_TEST], result["fail_to_pass"])

    def test_a_test_file_the_change_adds_as_a_link_to_nothing_is_not_proven(self):
        self.project.write({"report.py": DASHED, "tests/test_report.py": TESTS + DASH_TEST})
        link(self.root, {"tests/test_ghost.py": "nowhere.py"})
        result = self.project.verify()
        self.assertEqual(verify.UNVERIFIED, result["verdict"], result)
        self.assertIn("ownership is ambiguous", " ".join(result["unverified"]))


class StartTests(unittest.TestCase):
    def test_a_task_starts_in_a_project_whose_tests_hold_data_links(self):
        workspace, env = fixture_workspace(self)
        for name, text in FILES.items():
            path = workspace / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text)
        link(workspace)
        git(workspace, "add", ".")
        git(workspace, *IDENTITY, "commit", "-qm", "links")
        run = taskrun.TaskRun.start(workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=300)
        planned = run.advance_until_input()
        self.assertEqual("approve_plan", planned["needs"]["kind"], planned)
        self.assertEqual(LINKS, {name: os.readlink(workspace / name) for name in LINKS})


if __name__ == "__main__":
    unittest.main()
