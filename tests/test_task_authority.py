"""The original request stays binding in rendered bug-fix review prompts."""

import json
import unittest
from unittest.mock import patch

import autocode_task_authority as authority
from units import autoreview
from units.common import ModelRequest


class ReviewPromptTests(unittest.TestCase):
    STAGES = ("sol", "astra_review", "astra_checkpoint")
    TASK = "Fix duplicate renewals and preserve public state after partial cancellation."

    def upstream(self):
        packet = {"task": self.TASK, "saved_answers": {"Q1": "Keep callback behavior."}}
        return ModelRequest(
            "sol",
            "sol",
            "Review independently.\nCURRENT HANDOFF DATA\n" + json.dumps(packet),
            {"source": "unchanged"},
            {"properties": {}, "required": []},
            False,
        )

    def render(self, stage, outcome):
        upstream = self.upstream()
        with patch.object(autoreview, "execution_request", return_value=upstream):
            request = autoreview.prepare(
                {"settings": {}, "investigation": {"outcome": outcome}},
                stage,
                "/run/state.json",
                None,
            )
        return upstream, request

    def test_every_judging_route_retains_human_task_and_adds_coverage_policy(self):
        for stage in self.STAGES:
            with self.subTest(stage=stage):
                upstream, request = self.render(stage, "reproduced")
                instruction, packet = request.prompt.split("\nCURRENT HANDOFF DATA\n", 1)
                self.assertEqual(1, instruction.count("HUMAN TASK COVERAGE:"))
                self.assertIn(authority.INSTRUCTION, instruction)
                self.assertEqual(
                    json.loads(upstream.prompt.split("\nCURRENT HANDOFF DATA\n", 1)[1]), json.loads(packet)
                )
                self.assertEqual(upstream.role, request.role)
                self.assertEqual(upstream.route_role, request.route_role)
                self.assertEqual(upstream.allow_write, request.allow_write)
                self.assertEqual([], request.schema["required"])
                self.assertEqual("unchanged", request.metrics["source"])
                self.assertEqual((len(request.prompt.encode()) + 3) // 4, request.metrics["estimated_prompt_tokens"])

    def test_unreproduced_jobs_do_not_receive_bug_coverage_policy(self):
        for stage in self.STAGES:
            for outcome in (None, "not_reproduced", "needs_information"):
                with self.subTest(stage=stage, outcome=outcome):
                    _, request = self.render(stage, outcome)
                    self.assertNotIn("HUMAN TASK COVERAGE:", request.prompt)

    def test_policy_distinguishes_assertions_checkpoint_and_final_obligations(self):
        policy = authority.instruction({"outcome": "reproduced"})
        self.assertIn("A named test proves its actual assertions", policy)
        self.assertIn("pending later work is not a defect", policy)
        self.assertIn("Final completion requires every applicable original obligation", policy)
        self.assertIn("existing blocker/re-approval", policy)
        self.assertEqual("", authority.instruction(None))

    def test_completion_retry_does_not_waive_omitted_human_obligation(self):
        self.assertIn("original human obligation omitted", autoreview.SEND_BACK_NOTE)
        self.assertIn("contract correction", autoreview.SEND_BACK_NOTE)
