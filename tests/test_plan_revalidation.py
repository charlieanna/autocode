"""An approved plan correction can recheck a tested commit without inventing a source edit."""

import copy
import shlex
import sys
import unittest
from pathlib import Path

import autocode_check_replay as replay
import autocode_retained_work as retained
import autocode_util as util
import autocode_verification_schedule as schedule
import autocode_verify as verify
import autopilot

from .test_verify import Project


class PlanRevalidationTests(unittest.TestCase):
    def setUp(self):
        project = Project(
            {
                ".gitignore": ".autocode/\n__pycache__/\n",
                "value.py": "def answer():\n    return 42\n",
                "test_value.py": (
                    "import unittest\nfrom value import answer\n"
                    "class ValueTests(unittest.TestCase):\n"
                    "    def test_answer(self):\n        self.assertEqual(42, answer())\n"
                ),
            }
        )
        self.addCleanup(project.close)
        self.workspace = project.root
        self.run = project.root / ".autocode" / "runs" / "correction"
        self.command = shlex.join([sys.executable, "-m", "unittest", "discover", "-v"])
        source = util.snapshot(self.workspace)
        self.revision = source["revision"]
        self.state = {
            "workspace": str(self.workspace),
            "run_dir": str(self.run),
            "settings": {},
            "user_events": [],
            "contract_history": [],
            "stages": [],
            "turns": [],
        }
        for revision, summary in ((1, "Check the answer"), (2, "Correct plan wording; keep the tested source")):
            contract = {
                "task_id": "goal",
                "revision": revision,
                "body": {
                    "summary": summary,
                    "open_blocking_questions": [],
                    "permission_boundaries": ["Only this project"],
                    "constraints": ["Preserve existing tests"],
                    "acceptance_criteria": [
                        {
                            "id": "C1",
                            "criterion": "Answer is 42",
                            "human_review": False,
                            "verification_method": "suite: run `" + self.command + "`",
                        }
                    ],
                },
            }
            contract["hash"] = util.digest(contract)
            event = {"kind": "goal_approval", "actor": "user_cli", "token": f"r{revision}:{contract['hash']}"}
            contract.update(approval_status="approved", approval_event=event)
            self.state["user_events"].append(event)
            self.state["contract_history"].append(contract)
        self.state["goal_contract"] = self.state["contract_history"].pop()
        previous = {
            "id": "initial",
            "kind": "implement",
            "milestone_id": "M1",
            "affected_paths": ["value.py", "test_value.py"],
            "acceptance_criteria": ["C1"],
            "source_revision": self.revision,
            "contract_hash": self.state["contract_history"][0]["hash"],
        }
        self.state["task_archive"] = [previous]
        self.state["current_task"] = {
            **copy.deepcopy(previous),
            "id": "corrected",
            "contract_hash": self.state["goal_contract"]["hash"],
        }
        identity = {
            "execution": {"source_revision": self.revision},
            "command": self.command,
            "obligation": {"task_id": "initial", "contract": copy.deepcopy(self.state["contract_history"][0])},
        }
        receipt = schedule.run(
            self.run / "check-replay" / "obligations",
            identity,
            lambda out: verify.scratch_run(self.workspace, out, command=self.command, check_kind="full_suite"),
            reuse_allowed=False,
            reason="independent_clean_replay",
            current_identity=lambda: identity,
        )
        checked = {"verdict": "PASS", "source_revision": self.revision, "checks": [receipt]}
        self.state["validation_archive"] = [
            {
                "validation": {
                    "verdict": "PASS",
                    "source_revision": self.revision,
                    "check_replay": checked,
                    "criterion_results": [{"id": "C1", "status": "PASS"}],
                    "evidence_hashes": replay.evidence_pins(checked),
                },
            }
        ]
        self.value = {"changed_files": [], "commands_run": [], "evidence_refs": [], "summary": "No source edit needed"}
        self.record = {
            "stage": "builder",
            "task_id": "corrected",
            "changed_files": [],
            "source_revision": self.revision,
            "output": str(self.run / "builder.json"),
            "after_ref": str(self.run / "after.json"),
        }
        util.atomic_json(self.record["output"], self.value)
        util.atomic_json(self.record["after_ref"], source)
        self.receipt = receipt

    def candidate(self):
        return retained.validated_candidate(
            self.state,
            self.value,
            self.record,
            self.workspace,
            util.snapshot(self.workspace)["revision"],
        )

    def test_plan_only_correction_recovers_unchanged_commit_for_fresh_tester(self):
        self.state["stages"].append(self.record)
        self.state["no_progress_reports"] = [self.value]
        self.state["no_progress_batches"] = 3
        original = Path(self.receipt["scheduling"]["receipt"]).read_bytes()
        self.assertTrue(autopilot.recover_retained_candidate(self.state, self.workspace))
        self.assertEqual("tester", self.state["next_stage"])
        self.assertEqual(0, self.state["no_progress_batches"])
        handoff = self.state["retained_candidate_handoffs"][-1]
        self.assertEqual(self.revision, handoff["source_revision"])
        self.assertTrue(handoff["fresh_verification_required"])
        self.assertEqual([self.command], handoff["checks_to_run"])
        self.assertEqual(original, Path(self.receipt["scheduling"]["receipt"]).read_bytes())
        self.assertNotIn("validation", self.state)

    def test_source_drift_or_missing_approval_cannot_borrow_old_tested_commit(self):
        baseline = copy.deepcopy(self.state)
        self.state["user_events"].pop(0)
        self.assertIsNone(self.candidate())
        self.state = baseline
        (self.workspace / "value.py").write_text("def answer():\n    return 0\n")
        self.assertIsNone(self.candidate())

    def test_plan_reordering_does_not_change_scope_but_a_different_scope_does(self):
        self.state["current_task"]["affected_paths"].reverse()
        self.assertIsNotNone(self.candidate())
        self.state["current_task"]["affected_paths"].append("elsewhere.py")
        self.assertIsNone(self.candidate())

    def test_corrected_checks_require_fresh_verification_and_never_promote_the_old_pass(self):
        contract = self.state["goal_contract"]
        contract["body"]["acceptance_criteria"][0]["verification_method"] = "suite: run `python3 -m unittest test_new`"
        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        contract["approval_event"]["token"] = f"r2:{contract['hash']}"
        self.state["current_task"]["contract_hash"] = contract["hash"]
        result = self.candidate()
        self.assertIsNotNone(result)
        self.assertTrue(result["fresh_verification_required"])
        self.assertEqual(["python3 -m unittest test_new"], result["checks_to_run"])
        self.assertNotIn("verdict", result)

    def test_changed_command_output_or_missing_immutable_receipt_refuses_reconciliation(self):
        output = Path(self.receipt["output"])
        original = output.read_bytes()
        output.write_text("tampered success\n")
        self.assertIsNone(self.candidate())
        output.write_bytes(original)
        self.state["validation_archive"][0]["validation"]["evidence_hashes"].clear()
        self.assertIsNone(self.candidate())

    def test_compile_check_is_retained_but_cannot_replace_named_test_receipts(self):
        identity = {
            "execution": {"source_revision": self.revision},
            "command": shlex.join([sys.executable, "-c", "print('compile ready')"]),
            "check_kind": "compile",
            "obligation": {"task_id": "initial", "contract": self.state["contract_history"][0]},
        }
        compiled = schedule.run(
            self.run / "check-replay" / "obligations",
            identity,
            lambda out: verify.scratch_run(
                self.workspace,
                out,
                command=identity["command"],
                check_kind="compile",
            ),
            reuse_allowed=False,
            reason="approved_execution",
            current_identity=lambda: identity,
        )
        validation = self.state["validation_archive"][0]["validation"]
        validation["check_replay"]["checks"].append(compiled)
        validation["evidence_hashes"].update(replay.evidence_pins(validation["check_replay"]))
        self.assertIsNotNone(self.candidate())
        validation["check_replay"]["checks"] = [compiled]
        self.assertIsNone(self.candidate())
