"""Chat activity uses inspected status and saved attempts, never inferred success."""

import copy
import unittest

import autocode_chat_progress as progress


class ChatProgressTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "status": "RUNNING",
            "active_stage": {"stage": "terra"},
            "current_task": {"milestone_id": "M1"},
            "stages": [
                {"stage": "astra_plan", "finished_at": "2026-10-19T08:00:00Z", "exit_code": 0},
                {"stage": "sol", "finished_at": "2026-10-19T08:01:00Z", "exit_code": 0, "rejected": True},
                {"stage": "terra", "finished_at": "2026-10-19T08:02:00Z", "exit_code": 1},
            ],
        }
        self.public = {
            "status": "RUNNING",
            "needs": {"kind": "continue"},
            "liveness": {"kind": "supervised"},
            "progress": {
                "headline": "Builder working",
                "stage": "Builder",
                "tasks": {"total": 2, "label": "0 of 2 tasks done"},
                "requirements": {"total": 3, "label": "1 of 3 requirements checked (1 from earlier checks), 1 failed"},
            },
        }

    def test_working_builder_and_finished_attempts_never_imply_passed_checks(self):
        before = copy.deepcopy((self.state, self.public))
        lines = progress.project(self.state, self.public)
        self.assertEqual("Builder working · task M1", lines[0])
        self.assertEqual("0 of 2 tasks done · 1 of 3 requirements checked (1 from earlier checks), 1 failed", lines[1])
        self.assertEqual(
            "Recent steps: Planner attempt finished · Tester attempt needs correction · Builder attempt stopped",
            lines[2],
        )
        self.assertNotIn("passed", " ".join(lines))
        self.assertEqual(before, (self.state, self.public))

    def test_queued_finished_stopped_and_unknown_activity_are_distinct(self):
        for active, headline, kind, flags, expected in (
            (None, "Next: Tester", "unknown", {}, "Next: Tester"),
            ({"stage": "terra"}, "Builder finished", "stopped", {"finished": True}, "Builder finished"),
            (
                {"stage": "terra"},
                "Builder stopped without saving a report",
                "interrupted",
                {"stopped": True},
                "Builder stopped without saving a report",
            ),
            ({"stage": "terra"}, "Builder working", "unknown", {}, "Builder: activity could not be confirmed."),
        ):
            with self.subTest(headline=headline, kind=kind):
                self.state["active_stage"] = active
                self.public["progress"]["headline"] = headline
                self.public["liveness"]["kind"] = kind
                self.assertEqual(expected, progress.project(self.state, self.public, **flags)[0])

    def test_recent_attempts_follow_the_current_turn_and_canonical_routing(self):
        self.state["turns"] = [{"at": "2026-10-19T09:00:00Z"}]
        self.state["settings"] = {"workflow": {"mode": "glm_first_v1"}}
        self.state["stages"] += [
            {
                "stage": "astra_checkpoint",
                "started_at": "2026-10-19T09:01:00Z",
                "finished_at": "2026-10-19T09:02:00Z",
                "exit_code": 0,
            },
            {"stage": "terra", "started_at": "2026-10-19T09:03:00Z"},
            {
                "stage": "internal_unknown",
                "started_at": "2026-10-19T09:03:00Z",
                "finished_at": "2026-10-19T09:04:00Z",
                "exit_code": 0,
            },
        ]
        self.assertEqual("Recent steps: Tester attempt finished", progress.project(self.state, self.public)[-1])

    def test_waits_and_pauses_never_show_active_work(self):
        for status, need in (("WAITING_FOR_USER", "answer"), ("PAUSED_REQUESTED", "resume"), ("TASK_COMPLETE", "done")):
            with self.subTest(status=status):
                self.public.update(status=status, needs={"kind": need})
                self.assertEqual([], progress.project(self.state, self.public))

    def test_local_check_names_the_check_instead_of_the_queued_model(self):
        self.state["active_stage"] = None
        self.public["progress"]["headline"] = "Next: Tester"
        self.public["runner_check"] = {"summary": "Checking the completed report", "liveness": {"kind": "supervised"}}
        self.assertEqual("Checking the completed report", progress.project(self.state, self.public)[0])
        self.public["runner_check"]["liveness"]["kind"] = "unknown"
        self.assertEqual("Local check: activity could not be confirmed.", progress.project(self.state, self.public)[0])
