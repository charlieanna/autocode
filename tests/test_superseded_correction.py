"""A fix the original gate refused for an unlisted in-place test change goes back to plan review (#2748)."""

import copy
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_contract_identity as contracts
import autocode_goal_lifecycle as lifecycle
import autocode_protected_commands as commands
import autocode_protected_oracles as guard
import autocode_superseded_correction as correction
import autocode_util as util
import autocode_verify as verify

SHIPPING = "def shipping_fee(cents):\n    return 0 if cents > 5000 else 499\n"
FIXED = "def shipping_fee(cents):\n    return 0 if cents >= 5000 else 499\n"
# The mixed case: one assertion among several pins the bug (live GLM runs, tmp-superseded-mixed, 2026-11-05).
ORIGINAL = """import unittest
from shipping import shipping_fee
class Shipping(unittest.TestCase):
    def test_fee_schedule(self):
        self.assertEqual(shipping_fee(1200), 499)
        self.assertEqual(shipping_fee(5000), 499)
        self.assertEqual(shipping_fee(7500), 0)
    def test_small_order_pays_fee(self):
        self.assertEqual(shipping_fee(1000), 499)
"""
UPDATED = ORIGINAL.replace("shipping_fee(5000), 499", "shipping_fee(5000), 0") + (
    "    def test_fifty_dollar_order_ships_free(self):\n        self.assertEqual(shipping_fee(5000), 0)\n"
)
ENTRY = {"file": "tests/test_shipping.py", "test": "test_fee_schedule"}
FAILED = "tests.test_shipping.Shipping.test_fee_schedule"


class Correction(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / "project"
        (self.project / "tests").mkdir(parents=True)
        (self.project / "shipping.py").write_text(SHIPPING)
        (self.project / "tests" / "__init__.py").write_text("")
        (self.project / "tests" / "test_shipping.py").write_text(ORIGINAL)
        (self.project / "tests" / "test_other.py").write_text(
            "import unittest\nfrom shipping import shipping_fee\nclass Other(unittest.TestCase):\n"
            "    def test_large_order_ships_free(self):\n        self.assertEqual(shipping_fee(9000), 0)\n"
        )
        git = ["git", "-C", str(self.project)]
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "-qm", "o"], check=True)
        self.run_dir = self.project / ".autocode/runs/correction"
        command = shlex.join([sys.executable, "-m", "unittest", "discover", "-v"])
        self.state = {"settings": {}, "workspace": str(self.project), "user_events": []}
        guard.reconcile(
            self.state,
            self.state["settings"],
            SimpleNamespace(run_dir=None),
            self.project,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: command,
        )

    def approve(self, rows):
        body = {"acceptance_criteria": rows, "open_blocking_questions": []}
        contract = {"task_id": "t", "revision": 1, "body": body}
        contract["hash"] = util.digest(contract)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": contracts.token(contract)}
        contract.update(approval_status="approved", approval_event=event, origin="glm_draft")
        self.state["goal_contract"] = contract
        self.state["user_events"] = [event]

    def criterion(self, cid, test, **extra):
        return {"id": cid, "criterion": "A $50 order ships free", "verification_method": f"test: {test}", **extra}

    def refused(self):
        with self.assertRaises(commands.OriginalGateFailure) as caught:
            guard.replay(self.state, self.project, self.run_dir / "replays", verify.scratch_run, timeout=30)
        return caught.exception

    def fix(self, test=UPDATED, code=FIXED):
        (self.project / "shipping.py").write_text(code)
        (self.project / "tests" / "test_shipping.py").write_text(test)

    def test_an_unlisted_in_place_change_becomes_a_supersedes_entry_the_gate_then_accepts(self):
        self.approve([self.criterion("AC1", "test_fifty_dollar_order_ships_free")])
        self.fix()
        error = self.refused()
        self.assertEqual([FAILED], error.receipt["original"]["results"]["failed"])
        found = correction.proposal(self.state, error.receipt, self.project)
        self.assertEqual([{"criterion": "AC1", **ENTRY}], found["tests"])
        self.assertEqual([ENTRY], found["body"]["acceptance_criteria"][0]["supersedes"])
        self.assertEqual(["AC1"], [row["item"] for row in found["changes"]])
        self.assertEqual("runner_proof", found["changes"][0]["basis"])
        self.assertIn("tests/test_shipping.py::test_fee_schedule", found["changes"][0]["replacement"])
        # The approved plan itself is unchanged until plan review and approval.
        self.assertNotIn("supersedes", self.state["goal_contract"]["body"]["acceptance_criteria"][0])
        # Once a plan with the entry is approved, the same change passes the gate, excusing only that test.
        self.approve(found["body"]["acceptance_criteria"])
        result = guard.replay(self.state, self.project, self.run_dir / "replays", verify.scratch_run, timeout=30)
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual([FAILED], result["superseded"]["excused_failures"])

    def test_the_entry_goes_on_the_criterion_whose_test_sits_beside_the_old_one(self):
        self.approve(
            [
                self.criterion("AC1", "test_large_order_ships_free"),
                self.criterion("AC2", "test_fifty_dollar_order_ships_free"),
            ]
        )
        self.fix()
        found = correction.proposal(self.state, self.refused().receipt, self.project)
        self.assertEqual(["AC2"], [row["criterion"] for row in found["tests"]])
        self.assertNotIn("supersedes", found["body"]["acceptance_criteria"][0])

    def test_other_refusals_keep_the_pause(self):
        self.approve([self.criterion("AC1", "test_fifty_dollar_order_ships_free")])
        # A failing candidate suite.
        self.fix(test=UPDATED.replace("shipping_fee(7500), 0", "shipping_fee(7500), 3"))
        self.assertIsNone(correction.proposal(self.state, self.refused().receipt, self.project))
        self.fix()
        receipt = self.refused().receipt
        # Another failing original test, in a file the change did not edit, is not a replaced test.
        other = copy.deepcopy(receipt)
        other["original"]["results"]["failed"].append("tests.test_other.Other.test_large_order_ships_free")
        self.assertIsNone(correction.proposal(self.state, other, self.project))
        # A crashed or incomplete original run.
        other = copy.deepcopy(receipt)
        other["original"]["results"]["complete"] = False
        self.assertIsNone(correction.proposal(self.state, other, self.project))
        # A plan with no test: criterion has no regression proof to carry the entry.
        self.approve([{"id": "AC1", "criterion": "x", "verification_method": "guard: test_fee_schedule"}])
        self.assertIsNone(correction.proposal(self.state, receipt, self.project))
        # An unapproved plan.
        self.approve([self.criterion("AC1", "test_fifty_dollar_order_ships_free")])
        self.state["user_events"] = []
        self.assertIsNone(correction.proposal(self.state, receipt, self.project))

    def test_a_test_is_sent_back_once(self):
        self.approve([self.criterion("AC1", "test_fifty_dollar_order_ships_free")])
        self.fix()
        error = self.refused()
        self.state[correction.KEY] = [{"contract_revision": 1, "tests": [{"criterion": "AC1", **ENTRY}]}]
        self.assertIsNone(correction.proposal(self.state, error.receipt, self.project))

    def test_send_back_applies_only_to_the_testers_gate_refusal(self):
        self.approve([self.criterion("AC1", "test_fifty_dollar_order_ships_free")])
        self.fix()
        error = self.refused()
        before = dict(self.state)
        self.assertFalse(correction.send_back(self.state, {"stage": "terra"}, error))
        self.assertFalse(correction.send_back(self.state, {"stage": "sol"}, ValueError("other")))
        self.assertEqual(before, self.state)

    def test_the_runner_built_draft_is_an_unauthored_draft(self):
        self.assertIn(correction.ORIGIN, lifecycle.UNAUTHORED_DRAFTS)


if __name__ == "__main__":
    unittest.main()
