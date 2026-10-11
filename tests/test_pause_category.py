"""Pause causes are explicit, exhaustive, and independent of run controllers."""

from __future__ import annotations

import unittest
from copy import deepcopy
from types import MappingProxyType

import autocode_pause_category as pauses

from tests.test_stop_explanations import named_pause_states


class PauseCategoryTests(unittest.TestCase):
    def test_categories_have_the_four_stable_values_and_readable_labels(self):
        self.assertEqual(
            {
                "your_decision": "Your decision",
                "your_environment": "Your environment",
                "model_or_provider": "Model or provider",
                "autocode": "AutoCode",
            },
            dict(pauses.LABELS),
        )
        self.assertEqual(set(pauses.LABELS), set(pauses.STATUS_GROUPS))

    def test_every_inventoried_pause_has_exactly_one_explicit_group(self):
        groups = list(pauses.STATUS_GROUPS.values())
        explicit = set().union(*groups)
        self.assertEqual(named_pause_states(), explicit)
        self.assertEqual(sum(len(group) for group in groups), len(explicit))
        for cause, group in pauses.STATUS_GROUPS.items():
            for status in group:
                with self.subTest(status=status):
                    self.assertEqual(cause, pauses.category(status))

    def test_cause_examples_distinguish_host_provider_and_autocode_guards(self):
        for status, cause in (
            ("PAUSED_REQUESTED", "your_decision"),
            ("PAUSED_PERMISSION", "your_decision"),
            ("PAUSED_AUTH", "your_environment"),
            ("PAUSED_WORKSPACE_BUSY", "your_environment"),
            ("PAUSED_TOOL_CONTAINMENT", "your_environment"),
            ("PAUSED_PROVIDER_CAPACITY", "model_or_provider"),
            ("PAUSED_INVALID_OUTPUT", "model_or_provider"),
            ("PAUSED_CONTENT_FILTER", "model_or_provider"),
            ("PAUSED_BUDGET", "autocode"),
            ("PAUSED_STAGE_TIMEOUT", "autocode"),
            ("PAUSED_REPORT_REPAIR_INPUT", "autocode"),
            ("PAUSED_STALE_HANDOFF", "autocode"),
            ("PAUSED_COMPLETION_GATE", "autocode"),
            ("RESOLVER_PENDING", "autocode"),
        ):
            with self.subTest(status=status):
                self.assertEqual(cause, pauses.category(status))

    def test_active_or_complete_status_is_not_a_pause_even_with_an_action(self):
        for status in (None, "", "BUILDING", "RUNNING", "COMPLETE", "TASK_COMPLETE", "COMPLETE_WITH_RISKS"):
            with self.subTest(status=status):
                self.assertIsNone(pauses.category(status, {"kind": "review", "token": "review-current"}))

    def test_unknown_pause_uses_the_safe_autocode_fallback(self):
        for status in ("PAUSED", "PAUSED_UNRECOGNIZED_CAUSE"):
            with self.subTest(status=status):
                self.assertEqual("autocode", pauses.category(status))
                self.assertEqual("autocode", pauses.category(status, {"kind": "approve_plan", "token": "current"}))

    def test_current_normal_question_can_be_the_actionable_human_decision(self):
        need = {"kind": "answer", "resolver_token": "current", "questions": [{"id": "timezone"}]}
        self.assertEqual("your_decision", pauses.category("RESOLVER_PENDING", need))
        self.assertEqual("your_decision", pauses.category("PAUSED_PLANNING_BUDGET", need))

    def test_orphan_or_tokenless_questions_do_not_override_the_pause_cause(self):
        for need in (
            {"kind": "answer", "questions": [{"id": "timezone"}]},
            {"kind": "answer", "resolver_token": "", "questions": [{"id": "timezone"}]},
            {"kind": "answer", "resolver_token": "current", "questions": []},
            {"kind": "answer", "resolver_token": "current", "questions": [{"id": None}]},
        ):
            with self.subTest(need=need):
                self.assertEqual("autocode", pauses.category("RESOLVER_PENDING", need))

    def test_current_review_and_plan_approval_can_be_the_human_decision(self):
        for kind in ("review", "approve_plan"):
            with self.subTest(kind=kind):
                self.assertEqual(
                    "your_decision", pauses.category("PAUSED_COMPLETION_GATE", {"kind": kind, "token": "current"})
                )
                self.assertEqual("autocode", pauses.category("PAUSED_COMPLETION_GATE", {"kind": kind}))

    def test_repeated_completion_continue_preserves_cause_until_a_current_review(self):
        status = "PAUSED_COMPLETION_REVIEW"
        # Repeated CONTINUE despite passing evidence is a model convergence stop.
        self.assertEqual("model_or_provider", pauses.category(status))
        for need in ({"kind": "resume"}, {"kind": "review"}, {"kind": "review", "token": " "}):
            with self.subTest(need=need):
                self.assertEqual("model_or_provider", pauses.category(status, need))
        self.assertEqual("your_decision", pauses.category(status, {"kind": "review", "token": "current"}))

    def test_model_route_answers_preserve_the_provider_cause(self):
        for cause in ("quota", "content_filter", "output_limit"):
            for kind in ("answer", "retry_job"):
                for status in ("PAUSED_BUDGET", "WAITING_FOR_USER", "PAUSED_JOB_FAILURE"):
                    with self.subTest(cause=cause, kind=kind, status=status):
                        need = {
                            "kind": kind,
                            "resolver_token": "current",
                            "questions": [{"id": "route-builder"}],
                            "route": {"cause": cause, "question_id": "route-builder"},
                        }
                        self.assertEqual("model_or_provider", pauses.category(status, need))

    def test_operational_answers_use_the_projected_typed_pause_origin(self):
        for scope in ("blocker", "operational_exhaustion"):
            for origin, expected in (
                ("PAUSED_PROVIDER_CAPACITY", "model_or_provider"),
                ("PAUSED_AUTH", "your_environment"),
                ("PAUSED_TOOL_CONTAINMENT", "your_environment"),
                ("PAUSED_TIMEOUT_RECOVERY", "autocode"),
            ):
                with self.subTest(scope=scope, origin=origin):
                    need = {
                        "kind": "answer",
                        "resolver_scope": scope,
                        "pause_origin": origin,
                        "resolver_token": "current",
                    }
                    self.assertEqual(expected, pauses.category("WAITING_FOR_USER", need))

    def test_operational_answer_without_a_valid_origin_falls_back_conservatively(self):
        for origin in (None, "", "PAUSED_UNKNOWN_ORIGIN", "RUNNING", "WAITING_FOR_USER"):
            with self.subTest(origin=origin):
                need = {"kind": "answer", "resolver_scope": "operational_exhaustion", "pause_origin": origin}
                self.assertEqual("autocode", pauses.category("WAITING_FOR_USER", need))
                self.assertEqual("model_or_provider", pauses.category("PAUSED_PROVIDER_CAPACITY", need))
                self.assertEqual("your_environment", pauses.category("PAUSED_AUTH", need))

    def test_an_operational_action_does_not_become_a_normal_answer(self):
        need = {
            "kind": "answer",
            "resolver_token": "current",
            "questions": [{"id": "capacity"}],
            "action": "--resume-paused",
        }
        self.assertEqual("model_or_provider", pauses.category("PAUSED_PROVIDER_CAPACITY", need))

    def test_malformed_optional_projection_values_do_not_hide_a_guard(self):
        for need in (
            None,
            [],
            {"kind": {"answer": True}},
            {
                "kind": "answer",
                "resolver_scope": {},
                "route": {"cause": {}},
                "questions": [{"id": "orphan"}],
            },
        ):
            with self.subTest(need=need):
                self.assertEqual("autocode", pauses.category("PAUSED_COMPLETION_GATE", need))

    def test_inputs_are_not_changed_and_read_only_projections_are_accepted(self):
        need = {"kind": "answer", "resolver_token": "current", "questions": [{"id": "timezone"}]}
        before = deepcopy(need)
        self.assertEqual("your_decision", pauses.category("RESOLVER_PENDING", MappingProxyType(need)))
        self.assertEqual(before, need)


if __name__ == "__main__":
    unittest.main()
