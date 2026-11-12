"""A report-only repair of one Tester execution does not launch the protected-test pair again (#3040).

The protected gate runs the original suite twice: on the candidate, and with the original tests overlaid.
When the Tester's report is rejected for one citation and repaired, the same Tester execution still owns
that proof. These tests drive ``autocode_check_replay.replay`` (the runner's clean replay, as the report
and its repair reach it) on a real unittest project whose approved plan supersedes one original test.
"""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_check_replay as check_replay
import autocode_contract_identity as contracts
import autocode_protected_oracles as guard
import autocode_util as util
import autocode_verify as verify

from .test_superseded_tests import ENTRY, FIXED, ORIGINAL, SHIPPING, UPDATED

EXCUSED = ["tests.test_shipping.Shipping.test_fifty_dollar_order_pays_standard_fee"]


class ProtectedPairReuse(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / "project"
        (self.project / "tests").mkdir(parents=True)
        (self.project / "shipping.py").write_text(SHIPPING)
        (self.project / "tests" / "__init__.py").write_text("")
        (self.project / "tests" / "test_shipping.py").write_text(ORIGINAL)
        git = ["git", "-C", str(self.project)]
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "-qm", "o"], check=True)
        self.run_dir = self.project / ".autocode/runs/pair"
        self.command = shlex.join([sys.executable, "-m", "unittest", "discover", "-v"])
        self.state = {"settings": {}, "workspace": str(self.project), "user_events": []}
        guard.reconcile(
            self.state,
            self.state["settings"],
            SimpleNamespace(run_dir=None),
            self.project,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: self.command,
        )
        self.approve([ENTRY])
        (self.project / "shipping.py").write_text(FIXED)
        (self.project / "tests" / "test_shipping.py").write_text(UPDATED)
        self.events = self.run_dir / "iterations" / "tester-01.events.jsonl"
        self.events.parent.mkdir(parents=True)
        self.events.write_text('{"type": "tester execution"}\n')
        self.record = {
            "stage": "tester",
            "events": str(self.events),
            "output": str(self.run_dir / "iterations" / "tester-01.json"),
            "task_id": "T1",
            "source_revision": util.snapshot(self.project)["revision"],
        }
        self.context = {"reuse_supported": True, "environment": "same"}
        self.launches = []

    def approve(self, supersedes, criterion="AC1"):
        row = {"id": criterion, "criterion": "A $50 order ships free", "verification_method": "test: fee"}
        if supersedes is not None:
            row["supersedes"] = supersedes
        body = {"acceptance_criteria": [row], "open_blocking_questions": []}
        contract = {"task_id": "t", "revision": 1, "body": body}
        contract["hash"] = util.digest(contract)
        event = {"kind": "goal_approval", "actor": "user_cli", "token": contracts.token(contract)}
        contract.update(approval_status="approved", approval_event=event, origin="planner_draft")
        self.state["goal_contract"] = contract
        self.state["user_events"] = [event]
        self.assertTrue(contracts.approved(self.state))

    def scratch_run(self, workspace, directory, **kwargs):
        # The pair runs the bound suite narrowed to the changed test module (replayed_command).
        pair = guard.replay_command(self.command, ["tests/test_shipping.py"], self.project)
        if kwargs.get("command") == pair:
            self.launches.append("original" if kwargs.get("files") else "candidate")
        return verify.scratch_run(workspace, directory, **kwargs)

    def identity(self, workspace, **_kwargs):
        # The complete runtime identity has its own tests (test_verification_schedule); this one lets a
        # test change one dimension of it. Its source revision is the real one.
        return {"source_revision": util.snapshot(workspace)["revision"], **self.context}

    def replay(self):
        return check_replay.replay(
            [],
            self.project,
            self.run_dir,
            self.record,
            self.scratch_run,
            timeout=60,
            approved_state=self.state,
            execution_identity=self.identity,
        )

    def repair(self):
        """What the report repair changes: only the report's output path, never the Tester execution."""
        self.record["output"] = str(self.run_dir / "iterations" / "tester-01-repair-01.json")
        return self.replay()

    def completed(self):
        first = self.replay()
        self.assertEqual(["candidate", "original"], self.launches)
        protected = first["protected_tests"]
        self.assertEqual("PASS", protected["verdict"])
        self.assertEqual(1, protected["original"]["exit_code"])
        self.assertEqual(EXCUSED, protected["superseded"]["excused_failures"])
        return first

    def test_report_only_repair_reuses_the_completed_protected_pair(self):
        first = self.completed()
        second = self.repair()
        self.assertEqual(["candidate", "original"], self.launches, "the repair launched the pair again")
        protected, before = second["protected_tests"], first["protected_tests"]
        self.assertEqual("PASS", protected["verdict"])
        for name in ("candidate", "original"):
            self.assertEqual("reuse", protected[name]["scheduling"]["action"])
            self.assertEqual(before[name]["output"], protected[name]["output"])
            self.assertEqual(before[name]["output_sha256"], util.file_hash(protected[name]["output"]))
            self.assertEqual(before[name]["scheduling"]["attempt_id"], protected[name]["scheduling"]["attempt_id"])
        # The approved supersedes list still decides the original run's nonzero exit, afresh.
        self.assertEqual(1, protected["original"]["exit_code"])
        self.assertEqual(before["superseded"], protected["superseded"])
        # Reused and executed evidence are counted once each, with the original command seconds.
        self.assertEqual(2, first["scheduling"]["executed_count"])
        self.assertEqual(0, first["scheduling"]["reused_count"])
        self.assertEqual(0, second["scheduling"]["executed_count"])
        self.assertEqual(2, second["scheduling"]["reused_count"])
        self.assertEqual(
            sum(before[name]["duration_seconds"] for name in ("candidate", "original")),
            second["scheduling"]["avoided_seconds"],
        )
        # The old receipt and citations stay intact; the repair has its own protected receipt.
        self.assertNotEqual(before["receipt"], protected["receipt"])
        self.assertEqual(before["receipt_sha256"], util.file_hash(before["receipt"]))
        pins = check_replay.evidence_pins(second)
        self.assertEqual(
            protected["original"]["scheduling"]["receipt_sha256"], pins[protected["original"]["scheduling"]["receipt"]]
        )
        self.assertEqual(
            protected["original"]["supervision_sha256"], pins[protected["original"]["supervision"]["receipt"]]
        )
        # Completion accepts the repaired report's proof under the same rules as a fresh one.
        self.state["validation"] = {
            "check_replay": {
                "verdict": "PASS",
                "source_revision": protected["source_revision"],
                "protected_tests": protected,
            }
        }
        self.assertTrue(guard.ready(self.state, protected["source_revision"]))

    def test_a_new_tester_execution_runs_the_pair_fresh(self):
        self.completed()
        self.events.write_text('{"type": "a later Tester attempt"}\n')
        self.repair()
        self.assertEqual(["candidate", "original"] * 2, self.launches)

    def test_changed_supersedes_decision_runs_the_pair_fresh(self):
        self.completed()
        self.approve([ENTRY], criterion="AC2")
        result = self.repair()
        self.assertEqual(["candidate", "original"] * 2, self.launches)
        self.assertEqual("execute", result["protected_tests"]["original"]["scheduling"]["action"])

    def test_without_the_supersedes_entry_the_gate_refuses_instead_of_reusing(self):
        self.completed()
        self.approve(None)
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.repair()
        self.assertEqual(["candidate", "original"] * 2, self.launches)

    def test_changed_source_or_execution_context_runs_the_pair_fresh(self):
        self.completed()
        self.context["environment"] = "changed"
        self.repair()
        self.assertEqual(["candidate", "original"] * 2, self.launches)
        (self.project / "shipping.py").write_text(FIXED + "\n# changed\n")
        self.record["source_revision"] = util.snapshot(self.project)["revision"]
        self.repair()
        self.assertEqual(["candidate", "original"] * 3, self.launches)

    def test_unsupported_runtime_never_reuses(self):
        self.context["reuse_supported"] = False
        self.completed()
        result = self.repair()
        self.assertEqual(["candidate", "original"] * 2, self.launches)
        self.assertEqual(
            "execution_identity_not_cacheable", result["protected_tests"]["candidate"]["scheduling"]["reason"]
        )

    def test_altered_output_or_supervision_is_never_reused(self):
        first = self.completed()["protected_tests"]
        Path(first["original"]["output"]).write_text("forged\n")
        result = self.repair()["protected_tests"]
        self.assertEqual(["candidate", "original", "original"], self.launches)
        self.assertEqual("reuse", result["candidate"]["scheduling"]["action"])
        self.assertEqual("failed_partial_or_missing_output", result["original"]["scheduling"]["reason"])
        supervision = Path(result["candidate"]["supervision"]["receipt"])
        value = json.loads(supervision.read_text())
        supervision.write_text(json.dumps({**value, "cleanup_error": "uncertain"}))
        self.repair()
        self.assertEqual(["candidate", "original", "original", "candidate"], self.launches)

    def test_altered_scheduling_receipt_is_never_reused(self):
        first = self.completed()["protected_tests"]
        receipt = Path(first["candidate"]["scheduling"]["receipt"])
        saved = json.loads(receipt.read_text())
        receipt.write_text(json.dumps({**saved, "finished_at": "2026-01-01T00:00:00Z"}))
        result = self.repair()["protected_tests"]
        self.assertEqual(["candidate", "original", "candidate"], self.launches)
        self.assertEqual("missing_or_tampered_receipt", result["candidate"]["scheduling"]["reason"])

    def test_another_failing_original_test_is_never_reused_as_a_pass(self):
        broken = "def shipping_fee(cents):\n    return 0 if cents >= 5000 else (1 if cents == 1000 else 499)\n"
        (self.project / "shipping.py").write_text(broken)
        (self.project / "tests" / "test_shipping.py").write_text(
            UPDATED.replace("shipping_fee(1000), 499", "shipping_fee(1000), 1")
        )
        self.record["source_revision"] = util.snapshot(self.project)["revision"]
        for attempt in (1, 2):
            with self.assertRaisesRegex(ValueError, "test_small_order_pays_fee"):
                self.repair() if attempt == 2 else self.replay()
        self.assertEqual(["candidate", "original", "original"], self.launches)

    def test_a_builder_record_keeps_the_pair_unscheduled(self):
        self.record["stage"] = "builder"
        first = self.replay()
        self.repair()
        self.assertEqual(["candidate", "original"] * 2, self.launches)
        self.assertNotIn("scheduling", first["protected_tests"]["candidate"])


if __name__ == "__main__":
    unittest.main()
