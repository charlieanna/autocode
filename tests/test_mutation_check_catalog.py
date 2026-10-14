"""The mutation check (#699) through real proofs on catalog scenarios: a weak proving test's survivor is
reported beside its case, a discriminating one leaves none, and the verdict never moves.

Each variant is laid over its committed seed, as the fake harness does, and proven through the public
entry point (regression.prove) with real unittest runs.
"""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_mutation_check as mutation_check
import autocode_regression as regression
import autocode_verify as verify

CATALOG = Path(__file__).resolve().parents[1] / "scenarios" / "catalog"
PAGES = "partial_last_page_is_counted"
WEEKS = "reported_new_year_week_is_one_row"


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


class CatalogProofTests(unittest.TestCase):
    def prove(self, scenario, variant, *case_ids):
        temporary = tempfile.TemporaryDirectory(prefix="mutation-catalog-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "project"
        shutil.copytree(CATALOG / scenario / "seed", root)
        git(root, "init", "-q")
        git(root, "config", "maintenance.auto", "false")
        git(root, "config", "gc.auto", "0")
        git(root, "add", "-A")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        overlay = CATALOG / scenario / variant
        for path in overlay.rglob("*"):
            if path.is_file() and "__pycache__" not in path.parts:
                target = root / path.relative_to(overlay)
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(path, target)
        product = {
            path: (root / path).read_bytes()
            for path in ("pager/__init__.py", "timesheet/weeks.py")
            if (root / path).is_file()
        }
        state = {
            "base_commit": git(root, "rev-parse", "HEAD"),
            "settings": {},
            "iteration": 1,
            "stages": [],
            "history": [],
            "goal_contract": {"body": {"task_kind": "bugfix", "acceptance_criteria": [], "milestones": [{"id": "M1"}]}},
            "investigation": {
                "outcome": "reproduced",
                "test_cases": [{"id": case, "given": "g", "when": "w", "then": "t"} for case in case_ids],
            },
        }
        proof = regression.prove(state, root, Path(temporary.name) / "run")
        self.assertEqual(product, {path: (root / path).read_bytes() for path in product})
        saved = json.loads(Path(proof["path"]).read_text())
        self.assertEqual(proof["mutation_check"], saved["mutation_check"])
        self.assertFalse([label for label in saved["checks"] if "mutant" in label or "unmutated" in label])
        return proof

    def survivors(self, proof):
        return {
            case: [(row["path"], row["line"], row["operator"], row["before"], row["after"]) for row in rows]
            for case, rows in proof["mutation_check"]["survivors"].items()
        }

    def test_the_bugfix_trivial_reference_leaves_no_survivor(self):
        proof = self.prove("bugfix-trivial", "reference", PAGES)
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual({}, proof["mutation_check"]["survivors"])
        self.assertEqual(["killed"], [row["outcome"] for row in proof["mutation_check"]["files"][0]["mutants"]])

    def test_a_partial_page_test_that_passes_on_the_wrong_fix_reports_its_off_by_one(self):
        proof = self.prove("bugfix-trivial", "broken/lone-partial-page", PAGES)
        # Report only: the proof still passes, as it did before the mutation check existed.
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(
            {PAGES: ["tests.test_pager.PagerTests.test_partial_last_page_is_counted"]}, proof["case_tests"]
        )
        self.assertEqual({PAGES: [("pager/__init__.py", 9, "swap_bound", ">", ">=")]}, self.survivors(proof))
        self.assertEqual(
            "test_partial_last_page_is_counted passes with pager/__init__.py:9 changed (`>` -> `>=`)",
            proof["mutation_check"]["survivors"][PAGES][0]["text"],
        )

    def test_the_iso_weeks_reference_leaves_no_survivor(self):
        proof = self.prove("bugfix-iso-weeks", "reference", WEEKS)
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual({}, proof["mutation_check"]["survivors"])

    def test_the_special_cased_new_year_reports_the_bound_its_test_does_not_pin(self):
        proof = self.prove("bugfix-iso-weeks", "broken/special-case", WEEKS)
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertIn(("timesheet/weeks.py", 14, "swap_bound", ">", ">="), self.survivors(proof)[WEEKS])

    def test_a_proof_that_fails_after_its_mutants_ran_reports_none(self):
        # The second case has no test of its own: verify() passes and runs the mutants, check_cases fails it.
        proof = self.prove("bugfix-trivial", "broken/lone-partial-page", PAGES, "zero_items_zero_pages")
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertEqual(mutation_check.record(mutation_check.NOT_PASSED), proof["mutation_check"])


if __name__ == "__main__":
    unittest.main()
