"""Chat activity uses inspected status and saved attempts, never inferred success."""

import copy
import unittest

import autocode_chat_progress as progress


class ChatProgressTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "status": "RUNNING",
            "active_stage": {"stage": "builder"},
            "current_task": {"milestone_id": "M1"},
            "stages": [
                {"stage": "plan_task", "finished_at": "2026-10-19T08:00:00Z", "exit_code": 0},
                {"stage": "tester", "finished_at": "2026-10-19T08:01:00Z", "exit_code": 0, "rejected": True},
                {"stage": "builder", "finished_at": "2026-10-19T08:02:00Z", "exit_code": 1},
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
            "Recent steps: Planner finished",
            lines[2],
        )
        self.assertNotIn("passed", " ".join(lines))
        self.assertNotIn("not accepted", " ".join(lines))
        self.assertNotIn("stopped", " ".join(lines))
        self.assertEqual(before, (self.state, self.public))

    def test_rejected_and_stopped_attempts_stay_out_of_live_chat_progress(self):
        # While AutoCode is running on its own (needs.kind == "continue"), rejected reports, report repairs
        # and retried attempts are AutoCode's own machinery; they stay in Run details, never in the chat card.
        self.state["active_stage"] = {"stage": "requirements_gather_report_repair"}
        self.state["stages"] = [
            {
                "stage": "requirements_gather",
                "finished_at": "2026-11-11T08:08:12Z",
                "exit_code": 0,
                "rejected": True,
                "rejection_reason": "Requirements R3, R4: source_quote is not in the task or a saved user event. "
                'R3 quotes "Exact-token/core gates are unchanged.". For each, copy the source text',
            }
        ]
        self.public["progress"]["headline"] = "Requirements working"
        lines = progress.project(self.state, self.public)
        self.assertFalse([line for line in lines if "Recent steps:" in line], lines)
        self.assertFalse([line for line in lines if line.startswith("Why")], lines)
        self.state["stages"].append(
            {"stage": "requirements_gather_report_repair", "finished_at": "2026-11-11T08:13:16Z", "exit_code": 0}
        )
        lines = progress.project(self.state, self.public)
        self.assertEqual("Recent steps: Requirements finished", lines[-1])
        self.assertFalse([line for line in lines if line.startswith("Why")], lines)

    def test_internal_report_paths_do_not_leak_into_progress(self):
        self.state["active_stage"] = {"stage": "requirements_gather_report_repair"}
        for reason in (
            "ambiguity_audit.compatibility_and_callers entry R6 is not a file in the workspace",
            "source_trace.requirement_refs is missing an entry",
            "Plan revision reference is stale, malformed or not a Planner revision "
            "[plan_revision_reference:unexpected_reference_fields]",
            "Refreshed handoff dropped R1 without a user-backed omission in ignored_requirements; "
            'previous source_quote: "The request"; candidate user_feedback:feedback-123',
        ):
            with self.subTest(reason=reason):
                self.state["stages"] = [
                    {
                        "stage": "requirements_gather",
                        "finished_at": "2026-11-11T08:08:12Z",
                        "exit_code": 0,
                        "rejected": True,
                        "rejection_reason": reason,
                    }
                ]
                lines = progress.project(self.state, self.public)
                self.assertNotIn(reason, " ".join(lines))
                self.assertNotIn("not accepted", " ".join(lines))

    def test_a_retried_step_reads_as_one_clean_finished_step_without_machinery_chatter(self):
        self.state["active_stage"] = None
        self.public["progress"]["headline"] = "Next: Planner"
        record = {"exit_code": 0, "finished_at": "2026-11-11T08:08:12Z"}
        self.state["stages"] = [
            {**record, "stage": "requirements_gather", "rejected": True},
            {**record, "stage": "requirements_gather_report_repair", "rejected": True},
            {**record, "stage": "requirements_gather_report_repair"},
            {**record, "stage": "requirements_gather"},
            {**record, "stage": "model_preflight", "runner_owned": True},
        ]
        self.assertEqual(
            "Recent steps: Requirements finished",
            progress.project(self.state, self.public)[-1],
        )
        self.state["stages"][3:] = [
            {**record, "stage": "requirements_gather", "exit_code": 1},
            {**record, "stage": "requirements_gather", "rejected": True},
            {**record, "stage": "draft_plan"},
            {**record, "stage": "builder", "exit_code": 1},
            {**record, "stage": "builder"},
        ]
        self.assertEqual(
            "Recent steps: Requirements finished · Planner finished · Builder finished",
            progress.project(self.state, self.public)[-1],
        )

    def test_queued_finished_stopped_and_unknown_activity_are_distinct(self):
        for active, headline, kind, flags, expected in (
            (None, "Next: Tester", "unknown", {}, "Next: Tester"),
            ({"stage": "builder"}, "Builder finished", "stopped", {"finished": True}, "Builder finished"),
            (
                {"stage": "builder"},
                "Builder stopped without saving a report",
                "interrupted",
                {"stopped": True},
                "Builder stopped without saving a report",
            ),
            ({"stage": "builder"}, "Builder working", "unknown", {}, "Builder: activity could not be confirmed."),
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
                "stage": "review_checkpoint",
                "started_at": "2026-10-19T09:01:00Z",
                "finished_at": "2026-10-19T09:02:00Z",
                "exit_code": 0,
            },
            {"stage": "builder", "started_at": "2026-10-19T09:03:00Z"},
            {
                "stage": "internal_unknown",
                "started_at": "2026-10-19T09:03:00Z",
                "finished_at": "2026-10-19T09:04:00Z",
                "exit_code": 0,
            },
        ]
        self.assertEqual("Recent steps: Tester finished", progress.project(self.state, self.public)[-1])

    def test_a_runner_owned_dispatch_with_no_exit_code_finished(self):
        # #1643: the Orchestrator's record carries no exit_code, and read as "attempt stopped" on a clean run.
        dispatch = {"stage": "orchestrator", "finished_at": "2026-10-19T08:03:00Z", "runner_owned": True}
        self.state["stages"] = [dispatch, {"stage": "builder", "finished_at": "2026-10-19T08:04:00Z"}]
        self.assertEqual(
            "Recent steps: Orchestrator finished",
            progress.project(self.state, self.public)[-1],
        )

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
