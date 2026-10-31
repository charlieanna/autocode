"""A checkpoint left by a stopped Completion Review is re-opened, never re-litigated.

Two idle-stopped Completion Reviews on a live GLM/MiMo run archived the Tester's current evidence and
then refused every valid `COMPLETE` with a message blaming a regression proof that had not failed. These
fixtures assemble that durable checkpoint around the runner's own authenticated proof shape; no provider
and no mutation run is launched.
"""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_changed_code_evidence as policy
import autocode_regression as regression
import autocode_run_records as records
import autocode_source_scope as source_scope
import autocode_stage_recovery as recovery
import autocode_util as util

from tests.test_command_receipt import guarded_command_receipt

CASE = {
    "id": "T1",
    "criterion": "Given a negative number; when value runs; then it raises ValueError",
    "verification_method": "test: test_t1_negative_raises",
}
SOURCE = "def value(n):\n    return n + 1\n"


class TimeoutArchivedCompletionTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="timeout-archived-completion-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        (self.workspace / "calc.py").write_text(SOURCE)
        self.git("init", "-q")
        self.git("add", "calc.py")
        self.git("-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.run_dir = self.root / "run"
        self.proof_dir = self.run_dir / "proof-01"
        self.proof_dir.mkdir(parents=True)

    def git(self, *args):
        subprocess.run(["git", "-C", str(self.workspace), *args], check=True, capture_output=True)

    def head(self):
        return subprocess.check_output(["git", "-C", str(self.workspace), "rev-parse", "HEAD"], text=True).strip()

    def receipt(self, name):
        path = self.proof_dir / (name + ".log")
        path.write_text("Complete synthetic command capture " + name + "\n")
        return guarded_command_receipt(
            {
                "command": "python3 -m unittest",
                "output": str(path),
                "output_sha256": util.file_hash(path),
                "exit_code": 0,
                "timed_out": False,
                "interrupted": False,
            }
        )

    def state(self):
        return {
            "workspace": str(self.workspace),
            "base_commit": self.head(),
            "status": "PAUSED_COMPLETION_GATE",
            "phase": "PAUSED_OR_BLOCKED",
            "next_stage": "astra_review",
            "iteration": 1,
            "settings": {},
            "stages": [],
            "history": [],
            "user_events": [],
            "criteria_revision": 1,
            "goal_contract": {
                "hash": "approved-goal",
                "revision": 3,
                "body": {"task_kind": "bugfix", "acceptance_criteria": [CASE]},
            },
            "current_task": {"id": "task-1", "milestone_id": "M1"},
        }

    def prove(self, state):
        """A PASS proof for this source with one line only an independent reason can discharge."""
        revision = source_scope.snapshot(self.workspace, state)["revision"]
        product = self.workspace / "calc.py"
        inventory = [{"path": "calc.py", "sha256": util.file_hash(product), "lines": [2]}]
        common = {"source_revision": revision, "proof_context": "proof-context"}
        coverage = {**common, "files": [{**inventory[0], "executed_lines": [2]}]}
        coverage_receipt, mutant_receipt = self.receipt("coverage"), self.receipt("mutant")
        mutation = {
            **common,
            "files": [
                {
                    "path": "calc.py",
                    "sha256": util.file_hash(product),
                    "mutants": [
                        {"line": 2, "outcome": "survived", "failed_tests": [], "evidence": [mutant_receipt["output"]]}
                    ],
                }
            ],
        }
        report = self.proof_dir / "coverage.json"
        report.write_text(json.dumps({"files": {"calc.py": {"executed_lines": [2], "missing_lines": []}}}))
        assessment = policy.assess(inventory, coverage, mutation, **common)
        self.assertEqual("reason_required", assessment["obligations"][0]["status"], assessment)
        result = {
            "source_revision": revision,
            "verdict": "PASS",
            "case_scope": ["T1"],
            "case_tests": {"T1": "test_t1_negative_raises"},
            "checks": {"candidate": self.receipt("candidate")},
            "changed_code_inputs": {"inventory": inventory, "coverage": coverage, "mutation": mutation},
            "changed_code_evidence": assessment,
            "changed_code_receipts": [coverage_receipt, mutant_receipt],
            "diff_coverage": {
                "collected": True,
                "report_path": str(report),
                "report_sha256": util.file_hash(report),
                "command_outputs": [coverage_receipt["output"]],
            },
        }
        path = self.proof_dir / "verification.json"
        util.atomic_json(path, result)
        state["regression_proof"] = dict(result, path=str(path), receipt_sha256=util.file_hash(path))
        return revision, assessment

    def archived_validation(self, state, revision, assessment):
        review = self.proof_dir / "tester-review.json"
        review.write_text("Independently inspected the surviving mutant.\n")
        gap = assessment["obligations"][0]
        return {
            "verdict": "PASS",
            "reviewer_role": "sol",
            "source_revision": revision,
            "criteria_revision": state["criteria_revision"],
            "contract_revision": 3,
            "contract_hash": "approved-goal",
            "task_id": "task-1",
            "changed_code_decisions": [
                {
                    **{key: gap[key] for key in ("gap_id", "path", "sha256", "line")},
                    "source_revision": revision,
                    "proof_context": assessment["proof_context"],
                    "accepted": True,
                    "reason": "The reviewer inspected every public entry point; no call reaches this branch.",
                    "evidence": [str(review)],
                }
            ],
            "evidence_hashes": {str(review): util.file_hash(review)},
            "criterion_results": [{"id": "T1", "status": "PASS", "evidence_refs": [str(review)]}],
        }

    def checkpoint(self):
        """The saved run as the stopped-completion-review recovery left it."""
        state = self.state()
        revision, assessment = self.prove(state)
        validation = self.archived_validation(state, revision, assessment)
        stopped = {
            "stage": "astra_review",
            "role": "astra",
            "iteration": 1,
            "output": str(self.proof_dir / "completion-review-01.json"),
            "events": str(self.proof_dir / "completion-review-01.jsonl"),
            "source_revision": revision,
            "changed_files": [],
            "task_id": "task-1",
            "contract_hash": "approved-goal",
            "timed_out": True,
            "rejected": True,
            "abandoned": True,
            "automatic_recovery": True,
            "rejection_reason": "Timed-out non-terminal provider request automatically archived; partial work retained",
        }
        context = {
            "attempt_id": records.attempt_id(stopped),
            "role": "astra",
            "stage": "astra_review",
            "source_revision": revision,
            "task_id": "task-1",
            "changed_files": [],
            "next_stage": "astra_review",
        }
        state.update(
            {
                "stages": [
                    stopped,
                    {
                        "stage": "astra_review",
                        "role": "astra",
                        "iteration": 1,
                        "output": str(self.proof_dir / "completion-review-02.json"),
                        "source_revision": revision,
                        "task_id": "task-1",
                        "rejected": True,
                        "rejection_reason": regression.rejection(state),
                    },
                ],
                "recovery_context": context,
                "automatic_timeout_recoveries": [dict(context, at=util.now())],
                "user_events": [
                    {
                        "kind": "automatic_timeout_recovery",
                        "actor": "runner",
                        "attempt_id": context["attempt_id"],
                        "changed_files": [],
                    }
                ],
                "validation_archive": [{"reason": recovery.TIMEOUT_ARCHIVED_VALIDATION, "validation": validation}],
            }
        )
        return state, revision, validation

    def test_resume_restores_the_retired_evidence_so_a_proven_change_can_be_certified(self):
        state, revision, validation = self.checkpoint()
        self.assertFalse(regression.complete(state, revision, all_due=True), "the gate still lacks its evidence")
        self.assertTrue(regression.complete(state, revision, all_due=True, require_changed_code=False))

        self.assertTrue(recovery.prepare_timeout_archived_completion(state, self.run_dir, self.workspace))

        saved = util.read(self.run_dir / "state.json")
        self.assertEqual(validation, saved["validation"])
        self.assertEqual([], saved["validation_archive"])
        self.assertEqual("PAUSED_COMPLETION_GATE", saved["status"])
        self.assertEqual("astra_review", saved["next_stage"])
        self.assertEqual(2, len(saved["stages"]), "no attempt is added or erased")
        self.assertEqual(
            "Restored the current pinned validation that a stopped completion review had retired",
            saved["reconciliation_notes"][-1]["reason"],
        )
        self.assertTrue(regression.complete(saved, revision, all_due=True), "the restored evidence closes the gate")

    def test_the_repair_is_idempotent(self):
        state, _, _ = self.checkpoint()
        self.assertTrue(recovery.prepare_timeout_archived_completion(state, self.run_dir, self.workspace))
        saved = copy.deepcopy(state)
        self.assertFalse(recovery.prepare_timeout_archived_completion(state, self.run_dir, self.workspace))
        self.assertEqual(saved, state, "a second resume of the same checkpoint changes nothing")

    def test_a_checkpoint_that_does_not_match_the_retired_evidence_exactly_is_left_paused(self):
        def archived(state):
            return state["validation_archive"][0]["validation"]

        breakings = {
            "run is not paused": lambda s: s.update(status="RUNNING"),
            "next stage is not the completion review": lambda s: s.update(next_stage="sol"),
            "validation is already present": lambda s: s.update(validation=copy.deepcopy(archived(s))),
            "recovery names another stage": lambda s: s["recovery_context"].update(stage="terra"),
            "recovery names another role": lambda s: s["recovery_context"].update(role="terra"),
            "the timed-out attempt wrote files": lambda s: s["recovery_context"].update(changed_files=["calc.py"]),
            "another task is current": lambda s: s.update(current_task={"id": "another-task"}),
            "a stage is still in flight": lambda s: s.update(active_stage={"stage": "astra_review"}),
            "a check is still in flight": lambda s: s.update(active_runner_check={"stage": "regression_proof"}),
            "a report repair is pending": lambda s: s.update(pending_report_repair={"attempts": 1}),
            "an artifact is uncertain": lambda s: s.update(uncertain_artifacts=["partial"]),
            "a question owns the boundary": lambda s: s.update(pending_questions=[{"id": "Q1"}]),
            "a request owns the boundary": lambda s: s.update(user_request={"kind": "human_review"}),
            "no timed-out attempt is recorded": lambda s: s.update(automatic_timeout_recoveries=[]),
            "no recovery event is recorded": lambda s: s.update(user_events=[]),
            "two reports could match": lambda s: s["validation_archive"].append(
                {"reason": recovery.TIMEOUT_ARCHIVED_VALIDATION, "validation": copy.deepcopy(archived(s))}
            ),
            "no report was retired": lambda s: s.update(validation_archive=[]),
            "the source moved after the review": lambda s: (self.workspace / "calc.py").write_text(
                "def value(n):\n    return n + 2\n"
            ),
            "the goal changed after the review": lambda s: archived(s).update(contract_hash="another-goal"),
            "the goal revision changed": lambda s: archived(s).update(contract_revision=9),
            "the criteria set changed": lambda s: s.update(criteria_revision=9),
            "the review belongs to another task": lambda s: archived(s).update(task_id="another-task"),
            "a pinned evidence file changed": lambda s: [
                Path(path).write_text("Changed after the Tester pinned it.\n")
                for path in archived(s)["evidence_hashes"]
            ],
            "later work was accepted": lambda s: s["stages"].append(
                {
                    "stage": "terra",
                    "role": "terra",
                    "iteration": 2,
                    "source_revision": s["recovery_context"]["source_revision"],
                }
            ),
            "the runner proof does not pass": lambda s: s.pop("regression_proof"),
        }
        for name, break_ in breakings.items():
            with self.subTest(binding=name):
                self.setUp()  # each refusal gets its own workspace and proof
                state, _, _ = self.checkpoint()
                break_(state)
                untouched = copy.deepcopy(state)
                self.assertFalse(recovery.prepare_timeout_archived_completion(state, self.run_dir, self.workspace))
                self.assertEqual(untouched, state, "a refused repair changes nothing about the checkpoint")
                self.assertFalse((self.run_dir / "state.json").exists(), "a refused repair saves nothing")
