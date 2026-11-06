"""A spec-first wave built one milestone at a time is proved against its own tests only.

A live medium run (2026-11-06) wrote pricing's and stock's acceptance tests together, then built
pricing alone. Its regression proof ran tests/test_stock.py too, which cannot pass before stock is
built, and refused pricing. The proof goes through regression.prove with real unittest runs.
"""

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_pending_tests as pending_tests
import autocode_regression as regression
import autocode_verify as verify

from tests.test_verify import Project

PRICING = "def total(items):\n    return sum(q * p for q, p in items)\n"
PRICING_TEST = (
    "import unittest\nfrom pricing import total\n\n\nclass Pricing(unittest.TestCase):\n"
    "    def test_c1_total(self):\n        self.assertEqual(7, total([(1, 3), (2, 2)]))\n"
)
STOCK_TEST = (
    "import unittest\nfrom stock import ship\n\n\nclass Stock(unittest.TestCase):\n"
    "    def test_c2_ship(self):\n        self.assertEqual(1, ship(2, 1))\n"
)


def criterion(cid, name):
    return {"id": cid, "criterion": cid, "verification_method": f"test: {name}"}


class WaveBuiltSeriallyTests(unittest.TestCase):
    def setUp(self):
        self.project = Project({"README.md": "A new project.\n"})
        self.addCleanup(self.project.close)
        self.run_dir = Path(tempfile.mkdtemp(prefix="pending-tests-"))
        self.addCleanup(shutil.rmtree, self.run_dir, True)
        # The Tester wrote both milestones' tests before the Builder took pricing alone.
        self.project.write({"test_pricing.py": PRICING_TEST, "test_stock.py": STOCK_TEST, "pricing.py": PRICING})

    def state(self, milestone="M1", files=None):
        pinned = files or {"test_pricing.py": PRICING_TEST, "test_stock.py": STOCK_TEST}
        return {
            "base_commit": self.project.base,
            "settings": {},
            "iteration": 1,
            "stages": [],
            "history": [],
            "goal_contract": {
                "hash": "contract",
                "body": {
                    "task_kind": "build",
                    "acceptance_criteria": [criterion("C1", "test_c1_total"), criterion("C2", "test_c2_ship")],
                    "milestones": [
                        {"id": "M1", "acceptance_criteria": ["C1"]},
                        {"id": "M2", "acceptance_criteria": ["C2"]},
                    ],
                },
            },
            "current_task": {"id": "task", "milestone_id": milestone, "contract_hash": "contract"},
            "acceptance_tests": {
                "receipts": {
                    "wave": {
                        "case_ids": ["C1", "C2"],
                        "files": {
                            path: {"sha256": hashlib.sha256(text.encode()).hexdigest()} for path, text in pinned.items()
                        },
                    }
                }
            },
        }

    def test_the_first_member_is_proved_without_its_wave_mates_unbuilt_tests(self):
        proof = regression.prove(self.state(), self.project.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], str(proof["failures"]) + str(proof["unverified"]))
        self.assertEqual(["test_pricing.Pricing.test_c1_total"], proof["fail_to_pass"], proof)
        self.assertIn(pending_tests.NOTE + "test_stock.py", proof["notes"])

    def test_the_final_proof_still_runs_every_test(self):
        proof = regression.prove(self.state(), self.project.root, self.run_dir, all_due=True)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("test_stock", " ".join(proof["failures"]))

    def test_only_unchanged_files_that_hold_no_due_test_are_withheld(self):
        due = [{"id": "C1", "test_name": "test_c1_total"}]
        state = self.state()
        self.assertEqual(["test_stock.py"], pending_tests.withheld(state, self.project.root, due))
        # A file that also holds a due case's test stays in the proof.
        both = STOCK_TEST + "\n\ndef test_c1_total():\n    pass\n"
        self.project.write({"test_stock.py": both})
        self.assertEqual([], pending_tests.withheld(self.state(files={"test_stock.py": both}), self.project.root, due))
        # A file the Builder changed since the receipt pinned it stays too.
        self.project.write({"test_stock.py": STOCK_TEST + "# edited\n"})
        self.assertEqual([], pending_tests.withheld(state, self.project.root, due))
        # A due case without a test name could live in any file: nothing is withheld.
        self.project.write({"test_stock.py": STOCK_TEST})
        self.assertEqual([], pending_tests.withheld(state, self.project.root, [{"id": "C1"}]))
        self.assertEqual([], pending_tests.withheld(state, self.project.root, []))


if __name__ == "__main__":
    unittest.main()
