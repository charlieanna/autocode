"""Each requirement's saved changes after parallel Builders, through the public CLI (#730).

Drives parallel-diamond end to end with the scripted model: milestone A, then B and C as one
parallel batch, then D. The batch is integrated as one saved step, and each of its requirements
must still be attributed that step with only its own milestone's files. Before #730's acceptance
test, an integrated batch recorded no code checkpoint, so B's and C's requirements showed no
change at all. This module is in tests/suite_slow.json; the pure rules are in
test_code_checkpoints, test_criterion_changes and test_review_diff.
"""

import tempfile
import unittest
from pathlib import Path

import autocode_review_diff as review_diff
import autocode_review_presentation as presentation

FILES = {
    "C_A": ["contract/schema.json", "dependency_trace.json"],
    "C_B": ["server/__init__.py", "server/handler.py", "server/test_handler.py"],
    "C_C": ["client/__init__.py", "client/fetch.py", "client/test_fetch.py"],
    "C_D": ["integration/__init__.py", "integration/check.py", "integration/test_check.py"],
}


class ParallelCriterionChanges(unittest.TestCase):
    def setUp(self):
        from scenarios import run  # noqa: F401, I001 - initialize the harness before importing it
        from harness import catalog
        from harness.driver import Driver, default_autocode, fake_setup
        from harness.project import materialize, without_maintenance

        results = Path(__file__).resolve().parents[1] / ".scenario-runs"
        results.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(prefix="parallel-criteria-", dir=results, ignore_cleanup_errors=True)
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name).resolve()
        self.scenario = catalog.load("parallel-diamond")
        for name in ("config", "codex-home"):
            (root / name).mkdir()
        self.project = without_maintenance(materialize(self.scenario.seed, root / "project"))
        flags, env = fake_setup(self.scenario, root, self.scenario.reference)
        env.update(XDG_CONFIG_HOME=str(root / "config"), CODEX_HOME=str(root / "codex-home"))
        self.driver = Driver(
            self.project, root, flags, env, autocode=default_autocode(), max_steps=30, timeout_seconds=300
        )

    def test_each_requirement_shows_its_own_reference_files_including_the_parallel_batch(self):
        view = self.driver.drive(self.scenario.brief)
        self.assertTrue(view["done"], (view.get("status"), view.get("stop_reason")))
        batches = [row for row in self.driver.state().get("orchestration_history", []) if row["status"] == "INTEGRATED"]
        self.assertEqual([["B", "C"]], [[row["milestone_id"] for row in batch["workers"]] for batch in batches])

        attribution = {row["id"]: row for row in view["changes_by_criterion"]["criteria"]}
        self.assertEqual(FILES, {cid: row["files"] for cid, row in attribution.items()})
        self.assertFalse(any(row["overlapping"] for row in attribution.values()))
        self.assertEqual([], view["changes_by_criterion"]["unattributed"])
        self.assertEqual(attribution["C_B"]["checkpoints"], attribution["C_C"]["checkpoints"])
        [batch] = [row for row in view["code_checkpoints"]["rows"] if row["members"]]
        self.assertEqual(
            [
                {"milestone_id": "B", "changed_files": FILES["C_B"]},
                {"milestone_id": "C", "changed_files": FILES["C_C"]},
            ],
            batch["members"],
        )

        shown = review_diff.criterion_diffs(self.project, view)
        self.assertEqual("available", shown["availability"], shown)
        for criterion in shown["criteria"]:
            with self.subTest(criterion["id"]):
                diffs = {
                    file["path"]: file["text"]
                    for ident in criterion["checkpoints"]
                    for file in shown["steps"][ident]["files"]
                }
                for name in FILES[criterion["id"]]:
                    added = [line for line in (self.scenario.reference / name).read_text().splitlines() if line]
                    self.assertTrue(added)
                    for line in added:
                        self.assertIn("+" + line, diffs[name].splitlines())
                lines = presentation.render_review({"id": criterion["id"], "criterion_diffs": shown}).splitlines()
                for cid, names in FILES.items():
                    for name in names:
                        (self.assertIn if cid == criterion["id"] else self.assertNotIn)("    " + name, lines)


if __name__ == "__main__":
    unittest.main()
