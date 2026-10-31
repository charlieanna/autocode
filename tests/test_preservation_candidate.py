"""An unchanged Builder may submit the Tester's pinned guards for fresh review."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_coordination_context as coordination
import autocode_retained_work as retained
import autocode_util as util


class GuardCandidateTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.path = self.root / "guard_test.go"
        self.path.write_text("package guard\n")
        archive = self.root / "archive"
        archive.write_bytes(self.path.read_bytes())
        contract = {
            "task_id": "goal",
            "revision": 1,
            "body": {
                "task_kind": "bugfix",
                "open_blocking_questions": [],
                "acceptance_criteria": [
                    {"id": "G1", "criterion": "Preserve boundary", "verification_method": "guard: TestBoundary"}
                ],
            },
            "origin": "agent_plan",
        }
        contract["hash"] = util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        event = {"kind": "goal_approval", "actor": "user_cli", "token": f"r1:{contract['hash']}"}
        contract.update(approval_status="approved", approval_event=event)
        self.state = {
            "workspace": str(self.root),
            "goal_contract": contract,
            "user_events": [event],
            "current_task": {
                "id": "task",
                "kind": "implement",
                "contract_hash": contract["hash"],
                "affected_paths": ["guard_test.go"],
                "acceptance_criteria": ["G1"],
            },
            "stages": [],
            "acceptance_tests": {
                "receipts": {
                    "batch": {
                        "contract_hash": contract["hash"],
                        "case_ids": ["G1"],
                        "source_revision": "rev",
                        "files": {"guard_test.go": {"sha256": util.file_hash(archive), "archive": str(archive)}},
                    }
                }
            },
        }
        snapshot = self.root / "snapshot.json"
        snapshot.write_text(
            json.dumps({"head": "base", "revision": "rev", "files": {"guard_test.go": util.file_hash(self.path)}})
        )
        self.record = {
            "stage": "terra",
            "task_id": "task",
            "changed_files": [],
            "before_ref": str(snapshot),
            "after_ref": str(snapshot),
            "source_revision": "rev",
        }

    def candidate(self, member=True):
        with patch.object(coordination, "context", return_value={"member": "member"} if member else None):
            return retained.fresh_candidate(self.state, self.record, "rev")

    def test_unchanged_pinned_guards_are_only_a_candidate_for_independent_review(self):
        before = copy.deepcopy(self.state)
        result = self.candidate()
        self.assertEqual(
            {"source_revision": "rev", "retained_paths": ["guard_test.go"], "acceptance_guards": True}, result
        )
        self.assertEqual(before, self.state)

    def test_release_of_no_change_hold_uses_same_authenticated_candidate(self):
        import autocode_builder_failure as failure

        output = self.root / "builder.json"
        output.write_text("{}")
        self.record.update(output=str(output.resolve()), exit_code=0)
        self.state["stages"] = [self.record]
        value = failure.evidence(self.state, self.record)
        failure.hold(
            self.state,
            value,
            "PAUSED_BUILDER_CLASSIFICATION",
            "Builder completed an implementation attempt without source changes",
        )
        self.state["status"] = "RUNNING"
        with (
            patch.object(coordination, "context", return_value={"member": "member"}),
            patch.object(failure.source_scope, "snapshot", return_value={"revision": "rev"}),
        ):
            failure.dispatch_guard(self.state, "terra", self.root)
        self.assertNotIn("builder_failure_hold", self.state)
        self.assertNotIn("validation", self.state)

    def test_incomplete_stale_unapproved_product_or_unowned_inputs_are_refused(self):
        controls = {
            "malformed_contract": lambda: self.state["goal_contract"].pop("task_id"),
            "approval": lambda: self.state["user_events"].clear(),
            "contract": lambda: self.state["current_task"].update(contract_hash="other"),
            "task": lambda: self.record.update(task_id="other"),
            "rollback": lambda: self.record.update(rolled_back=True),
            "coverage": lambda: self.state["acceptance_tests"]["receipts"]["batch"].update(case_ids=[]),
            "receipt_contract": lambda: self.state["acceptance_tests"]["receipts"]["batch"].update(
                contract_hash="other"
            ),
            "receipt_source": lambda: self.state["acceptance_tests"]["receipts"]["batch"].update(
                source_revision="other"
            ),
            "current_bytes": lambda: self.path.write_text("changed"),
            "archive_bytes": lambda: (self.root / "archive").write_text("changed"),
            "ownership": lambda: self.state["current_task"].update(affected_paths=["other.go"]),
            "source": lambda: self.record.update(source_revision="other"),
            "snapshot": lambda: (self.root / "snapshot.json").unlink(),
            "product": lambda: self.state["goal_contract"]["body"]["acceptance_criteria"][0].update(
                verification_method="test: TestFix"
            ),
        }
        for label in controls:
            with self.subTest(label=label):
                self.setUp()
                controls[label]()
                self.assertIsNone(self.candidate())
        self.setUp()
        self.assertIsNone(self.candidate(member=False))
