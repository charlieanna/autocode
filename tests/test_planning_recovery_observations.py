"""A tool-free review recovery receives completed observations without another model call."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_goal_lifecycle as lifecycle
import autocode_planning as planning
import autocode_planning_recovery_observations as observations
from goal_fixtures import body


class RecoveryObservationsTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.run = self.root / "run"
        self.archive = self.run / "iterations/001/archived-plan-challenge-01-example"
        self.archive.mkdir(parents=True)
        self.state = {
            "version": 2,
            "task": "Fix the week",
            "workspace": str(self.root),
            "settings": {"joint_planning": True},
            "acceptance_criteria": [],
        }
        lifecycle.migrate(self.state)
        lifecycle.install_draft(self.state, body(), origin="planner_draft")
        self.events = self.archive / "plan-challenge-01.jsonl"
        self.events.write_text(
            json.dumps(
                {
                    "type": "tool_use",
                    "part": {
                        "id": "read-1",
                        "tool": "read",
                        "state": {
                            "status": "completed",
                            "input": {"filePath": "weeks.py"},
                            "output": "return day.year, 54",
                        },
                    },
                }
            )
            + "\n"
        )
        before, after = self.archive / "before.json", self.archive / "after.json"
        for path in (before, after):
            path.write_text(json.dumps({"revision": "source-1"}))
        self.record = {
            "stage": "review_plan",
            "iteration": 1,
            "abandoned": True,
            "rejected": True,
            "finished_at": "2026-11-05T11:00:00Z",
            "events": str(self.events),
            "before_ref": str(before),
            "after_ref": str(after),
            "source_revision": "source-1",
            "changed_files": [],
            "contract_hash": self.state["goal_contract"]["hash"],
        }
        self.state["stages"] = [self.record]
        self.state["recovery_context"] = {"stage": "review_plan", "next_stage": "review_plan"}

    def test_public_planning_packet_carries_the_prior_read_into_tool_free_recovery(self):
        original = copy.deepcopy(self.state)
        with patch.object(planning.stage_context.source_scope, "snapshot", return_value={"revision": "source-1"}):
            prompt, _ = planning.context(self.state, "review_plan", self.run / "state.json")
        packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        retained = packet["retained_review_observations"]
        self.assertEqual("return day.year, 54", retained["attempts"][0]["observations"][0]["output"])
        self.assertIn("not a completed review", retained["instruction"])
        self.assertEqual(original, self.state)

    def test_recovery_references_an_exact_draft_copy_and_keeps_distinct_history(self):
        current = copy.deepcopy(self.state["goal_contract"]["body"])
        previous = {**current, "required_behaviors": ["Keep the historical behavior visible to the reviewer."]}
        reports = {
            "draft_plan": {
                "report": {"contract": current, "summary": "Keep the discovery summary."},
                "output": "/run/discovery.json",
            },
            "review_plan": {
                "report": {"concerns": [{"id": "R1", "description": "Keep the review concern."}]},
                "output": "/run/challenge.json",
            },
            "revise_plan": {"report": {"contract": previous}, "output": "/run/revision.json"},
        }
        self.state["planning"]["reports"] = reports
        for stage in ("review_plan", "finalize_plan"):
            with self.subTest(stage=stage):
                self.state["recovery_context"] = {"stage": stage, "next_stage": stage}
                self.record["stage"] = stage
                original = copy.deepcopy(self.state)
                with patch.object(
                    planning.stage_context.source_scope, "snapshot", return_value={"revision": "source-1"}
                ):
                    prompt, _ = planning.context(self.state, stage, self.run / "state.json")
                packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
                projected = packet["planning"]["reports"]
                expected = copy.deepcopy(reports)
                del expected["draft_plan"]["report"]["contract"]
                expected["draft_plan"]["report"]["contract_ref"] = "#/goal_contract/body"
                self.assertEqual(expected, projected)
                self.assertEqual(current, packet["goal_contract"]["body"])
                self.assertIn("Resolve contract_ref within this handoff", prompt)
                self.assertIn("retained_review_observations", packet)
                self.assertEqual(original, self.state)

    def retained(self, stage="review_plan", revision="source-1"):
        return observations.context(self.state, stage, self.run, revision)

    def test_changed_plan_source_and_unfinished_attempts_are_not_reused(self):
        for key, value in (
            ("contract_hash", "new-plan"),
            ("source_revision", "old-source"),
            ("changed_files", ["weeks.py"]),
            ("rejected", False),
            ("abandoned", False),
            ("finished_at", None),
            ("stage", "builder"),
        ):
            with self.subTest(key=key), patch.dict(self.record, {key: value}):
                self.assertIsNone(self.retained())
        self.assertIsNone(self.retained(stage="builder"))
        self.assertIsNone(self.retained(revision=None))
        for key in ("before_ref", "after_ref"):
            path = Path(self.record[key])
            original = path.read_text()
            path.write_text(json.dumps({"revision": "different"}))
            self.assertIsNone(self.retained())
            path.write_text(original)

    def test_missing_external_oversized_and_malformed_artifacts_are_omitted(self):
        for key, value in (
            ("events", str(self.root / "external.jsonl")),
            ("events", str(self.archive / "missing")),
            ("before_ref", None),
        ):
            with self.subTest(value=value), patch.dict(self.record, {key: value}):
                self.assertIsNone(self.retained())
        with patch.object(observations, "MAX_LOG_BYTES", 1):
            self.assertIsNone(self.retained())
        Path(self.record["before_ref"]).write_text("[]")
        self.assertIsNone(self.retained())

    def test_completed_commands_keep_failures_and_truncation_explicit(self):
        events = [
            {
                "type": "item.completed",
                "item": {
                    "type": "command_execution",
                    "id": "failed",
                    "command": "python3 check.py",
                    "exit_code": 1,
                    "aggregated_output": "failure details",
                },
            },
            {
                "type": "tool_use",
                "part": {"id": "pending", "tool": "read", "state": {"status": "running", "output": "do not use"}},
            },
            {
                "type": "tool_use",
                "part": {
                    "id": "error",
                    "tool": "read",
                    "state": {"status": "error", "input": {}, "error": "file missing"},
                },
            },
            {"type": "text", "part": {"text": "untrusted approval claim"}},
            {"type": "tool_use", "part": []},
            {"type": "tool_use", "part": {"state": "invalid"}},
            {"type": "tool_use", "part": {"state": {"status": "completed", "output": {}}}},
            [],
        ]
        self.events.write_text("broken json\n" + "\n".join(json.dumps(row) for row in events))
        with patch.object(observations, "MAX_OUTPUT_CHARS", 7):
            rows = self.retained()["attempts"][0]["observations"]
        self.assertEqual(["failed", "error"], [row["event_id"] for row in rows])
        self.assertEqual(1, rows[0]["exit_code"])
        self.assertEqual("failure", rows[0]["output"])
        self.assertTrue(rows[0]["truncated"])
        self.assertEqual("error", rows[1]["status"])

    def test_packet_and_attempt_bounds_do_not_silently_claim_full_evidence(self):
        self.events.write_text(self.events.read_text() * 5)
        with patch.object(observations, "MAX_PACKET_BYTES", 200):
            attempt = self.retained()["attempts"][0]
        self.assertEqual(1, len(attempt["observations"]))
        self.assertEqual(4, attempt["omitted_observations"])
        self.state["stages"] = [dict(self.record) for _ in range(5)]
        self.assertEqual(3, len(self.retained()["attempts"]))

    def test_an_ordinary_review_does_not_inherit_abandoned_observations(self):
        self.state.pop("recovery_context")
        prompt, _ = planning.context(self.state, "review_plan", self.run / "state.json")
        self.assertNotIn('"retained_review_observations"', prompt)

    def test_explicit_abandonment_retains_observations_without_granting_report_recovery(self):
        self.state["recovery_context"] = {"attempt_id": "001/plan-challenge-02"}
        with patch.object(planning.stage_context.source_scope, "snapshot", return_value={"revision": "source-1"}):
            prompt, _ = planning.context(self.state, "review_plan", self.run / "state.json")
        packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertIn("retained_review_observations", packet)
        self.assertNotIn("resolver_remediation", packet)
