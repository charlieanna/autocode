"""The tests workflow's two depths, its nightly issue and its shard timings (CI tooling)."""

import json
import tempfile
import unittest
from pathlib import Path

import ci_depth
import ci_nightly_issue
import suite_durations

BASE = "a" * 40


class DepthTests(unittest.TestCase):
    def test_pull_requests_merge_groups_and_master_pushes_take_the_sharded_gate(self):
        for event, key in (("pull_request", "pr_base"), ("merge_group", "group_base"), ("push", "push_base")):
            with self.subTest(event=event):
                outputs = ci_depth.depth(event, **{key: BASE}, suite_shards=3)
                self.assertEqual("gate", outputs["mode"])
                self.assertEqual(BASE, outputs["base"])
                self.assertEqual([1, 2, 3], json.loads(outputs["suite_shards"]))
                self.assertEqual([1], json.loads(outputs["scenario_shards"]))

    def test_the_gate_reads_only_its_own_events_base(self):
        outputs = ci_depth.depth("push", pr_base=BASE, group_base=BASE, push_base="")
        self.assertEqual(("full", ""), (outputs["mode"], outputs["base"]))

    def test_nightly_manual_and_new_branch_pushes_run_everything(self):
        for event, push_base in (("schedule", ""), ("workflow_dispatch", ""), ("push", "0" * 40)):
            with self.subTest(event=event):
                outputs = ci_depth.depth(event, push_base=push_base, nightly_suite_shards=5, scenario_shards=4)
                self.assertEqual(("full", ""), (outputs["mode"], outputs["base"]))
                self.assertEqual([1, 2, 3, 4, 5], json.loads(outputs["suite_shards"]))
                self.assertEqual([1, 2, 3, 4], json.loads(outputs["scenario_shards"]))

    def test_a_pull_request_without_its_pinned_base_or_a_zero_shard_count_is_refused(self):
        with self.assertRaises(ValueError):
            ci_depth.depth("pull_request", pr_base="main")
        with self.assertRaises(ValueError):
            ci_depth.depth("schedule", scenario_shards=0)

    def test_outputs_are_appended_for_the_workflow(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "outputs"
            output.write_text("existing=value\n")
            self.assertEqual(0, ci_depth.main(["--event", "push", "--push-base", BASE, "--github-output", str(output)]))
            lines = output.read_text().splitlines()
            self.assertEqual("existing=value", lines[0])
            self.assertIn("mode=gate", lines)
            self.assertIn(f"base={BASE}", lines)
            self.assertEqual(2, ci_depth.main(["--event", "pull_request"]))


class NightlyIssueTests(unittest.TestCase):
    URL = "https://example.test/run/1"

    def test_a_red_night_opens_one_issue_or_comments_on_the_open_one(self):
        (create,) = ci_nightly_issue.plan("failure", None, self.URL, "abc")
        self.assertEqual(["issue", "create", "--title", ci_nightly_issue.TITLE], create[:4])
        self.assertIn(self.URL, create[create.index("--body") + 1])
        (comment,) = ci_nightly_issue.plan("cancelled", 12, self.URL, "abc")
        self.assertEqual(["issue", "comment", "12"], comment[:3])

    def test_a_green_night_closes_the_open_issue_and_otherwise_does_nothing(self):
        comment, close = ci_nightly_issue.plan("success", 12, self.URL, "abc")
        self.assertEqual(["issue", "comment", "12"], comment[:3])
        self.assertEqual(["issue", "close", "12"], close)
        self.assertEqual([], ci_nightly_issue.plan("success", None, self.URL, "abc"))


class SuiteDurationsTests(unittest.TestCase):
    def test_finished_rows_of_a_ci_log_become_durations(self):
        log = (
            "2026-11-23T16:20:01.1Z ok     12.5s  tests.test_alpha (5 tests)\n"
            "2026-11-23T16:20:02.1Z FAIL  300.0s  tests.test_beta (1 tests)\n"
            "ok    81.3s  scenarios.catalog.journey.test_cli.CLI.test_one (1 tests)\n"
            "Ran 2 tests in 0.001s\n"
        )
        self.assertEqual(
            {
                "tests.test_alpha": 12.5,
                "tests.test_beta": 300.0,
                "scenarios.catalog.journey.test_cli.CLI.test_one": 81.3,
            },
            suite_durations.parse(log),
        )


if __name__ == "__main__":
    unittest.main()
