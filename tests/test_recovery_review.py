"""Failure inspection reaches review before proof, without changing proof authority."""

import copy
import unittest
from pathlib import Path
from unittest import mock

import autocode_regression as regression


def inspecting():
    recovery = {
        "stage": "terra",
        "role": "terra",
        "next_stage": "astra_review",
        "source_revision": "partial",
        "task_id": "T1",
        "attempt_id": "001/builder-01",
    }
    return {
        "status": "RUNNING",
        "next_stage": "astra_review",
        "goal_contract": {"hash": "approved", "revision": 1},
        "current_task": {"id": "T1", "kind": "implement", "contract_hash": "approved", "contract_revision": 1},
        "recovery_context": recovery,
        "automatic_timeout_recoveries": [copy.deepcopy(recovery)],
        "stages": [
            {
                "stage": "terra",
                "role": "terra",
                "iteration": 1,
                "output": "/run/archived-builder-01/builder-01.json",
                "source_revision": "partial",
                "task_id": "T1",
                "contract_hash": "approved",
                "contract_revision": 1,
                "timed_out": True,
                "abandoned": True,
                "automatic_recovery": True,
                "rejected": True,
            },
            {"stage": "recovery_observation", "runner_owned": True},
        ],
    }


class RecoveryReviewTests(unittest.TestCase):
    def review(self, state, *, stage="astra_review", revision="partial"):
        with (
            mock.patch.object(regression.schedule, "guard"),
            mock.patch.object(regression.runner_check, "clear"),
            mock.patch.object(regression.package_writes, "check"),
            mock.patch.object(regression.acceptance, "is_blind", return_value=False),
            mock.patch.object(regression, "required", return_value=True),
            mock.patch.object(regression, "record_unmeasured"),
            mock.patch.object(regression, "cases", return_value=[]),
            mock.patch.object(regression.source_scope, "snapshot", return_value={"revision": revision}),
            mock.patch.object(regression, "prove") as prove,
        ):
            regression.before_review(state, stage, Path("/workspace"), Path("/run"))
        return prove.call_count

    def test_bound_failed_builder_is_inspected_before_proof_even_with_an_old_implementation(self):
        for retained in (None, {"source_revision": "earlier", "summary": "Previously accepted Builder report"}):
            state = inspecting()
            if retained:
                state["implementation"] = retained
            before = copy.deepcopy(state)
            with self.subTest(retained=retained):
                self.assertEqual(0, self.review(state))
                self.assertEqual(before, state, "Deferral must grant no proof or report credit")

    def test_tester_and_checkpoint_still_execute_proof(self):
        for stage in ("sol", "astra_checkpoint"):
            with self.subTest(stage=stage):
                self.assertEqual(1, self.review(inspecting(), stage=stage))

    def test_changed_source_or_stale_recovery_binding_keeps_normal_proof(self):
        self.assertEqual(1, self.review(inspecting(), revision="changed-again"))
        changes = (
            ("status", "PAUSED_PROVIDER_TIMEOUT"),
            ("next_stage", "sol"),
            ("active_stage", {"stage": "terra"}),
            ("uncertain_artifacts", ["attempt"]),
            ("pending_report_repair", {"original": "attempt"}),
            ("recovery_context", {}),
            ("automatic_timeout_recoveries", []),
            ("stages", []),
        )
        for key, value in changes:
            state = inspecting()
            state[key] = value
            with self.subTest(field=key):
                self.assertEqual(1, self.review(state))
        for field, value in (
            ("stage", "sol"),
            ("role", "astra"),
            ("next_stage", "sol"),
            ("source_revision", "stale"),
            ("task_id", "another-task"),
            ("attempt_id", "002/builder-02"),
        ):
            state = inspecting()
            state["recovery_context"][field] = value
            state["automatic_timeout_recoveries"] = [copy.deepcopy(state["recovery_context"])]
            with self.subTest(recovery=field):
                self.assertEqual(1, self.review(state))

    def test_current_contract_task_and_archived_attempt_must_still_match(self):
        for field, value in (
            ("kind", "validate"),
            ("id", "another-task"),
            ("contract_hash", "another-contract"),
            ("contract_revision", 2),
        ):
            state = inspecting()
            state["current_task"][field] = value
            with self.subTest(task=field):
                self.assertEqual(1, self.review(state))
        for field, value in (
            ("stage", "terra_report_repair"),
            ("role", "sol"),
            ("timed_out", False),
            ("abandoned", False),
            ("automatic_recovery", False),
            ("rejected", False),
            ("report_only", True),
            ("source_revision", "earlier"),
            ("task_id", "another-task"),
            ("contract_hash", "another-contract"),
            ("contract_revision", 2),
            ("iteration", 2),
            ("output", ""),
        ):
            state = inspecting()
            state["stages"][0][field] = value
            with self.subTest(record=field):
                self.assertEqual(1, self.review(state))
        state = inspecting()
        state["stages"].append({"stage": "terra", "exit_code": 0, "source_revision": "partial"})
        self.assertEqual(1, self.review(state), "A later attempt cannot inherit the old timeout deferral")
        state = inspecting()
        state["automatic_timeout_recoveries"][-1]["attempt_id"] = "another-attempt"
        self.assertEqual(1, self.review(state))


if __name__ == "__main__":
    unittest.main()
