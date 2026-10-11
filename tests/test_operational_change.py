"""Pure choice-bound timeout policy, including rejection without partial updates."""

import copy
import json
import unittest

import autocode_operational_change as policy


class OperationalChangeTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "settings": {
                "regression": {"test_timeout": 1800},
                "limits": {
                    "tool_timeout_seconds": 1800,
                    "stage_timeout_seconds": 3600,
                    "max_seconds": 7200,
                },
            }
        }
        self.rows = [
            {
                "option": "Use 5400 seconds",
                "changes": {
                    "settings.regression.test_timeout": 5400,
                    "settings.limits.tool_timeout_seconds": 5400,
                    "settings.limits.stage_timeout_seconds": 6000,
                },
            },
            {"option": "Decline", "changes": {}},
        ]
        self.request = {
            "kind": "permission",
            "options": [row["option"] for row in self.rows],
            "proposed_delta": json.dumps({"operational_options": self.rows}),
        }
        self.question = {"why": "Verification exhausted its command allowance"}
        policy.bind(self.state, self.request, self.question)

    def test_selected_option_applies_only_named_settings_and_records_before_after(self):
        receipt = policy.apply(self.state, self.request, self.question, "Use 5400 seconds")
        self.assertEqual(5400, self.state["settings"]["regression"]["test_timeout"])
        self.assertEqual(6000, self.state["settings"]["limits"]["stage_timeout_seconds"])
        self.assertEqual(7200, self.state["settings"]["limits"]["max_seconds"])
        self.assertEqual(
            {"before": {"present": True, "value": 1800}, "after": 5400}, receipt["settings.regression.test_timeout"]
        )
        self.assertIn("settings.regression.test_timeout=5400s", self.question["why"])

    def test_decline_applies_nothing(self):
        before = copy.deepcopy(self.state)
        self.assertEqual({}, policy.apply(self.state, self.request, self.question, "Decline"))
        self.assertEqual(before, self.state)

    def test_free_text_does_not_select_a_change(self):
        for text in ("yes", "1", "Use 5400 seconds except stage timeout", "Decline, actually approve"):
            with self.subTest(text=text):
                before = copy.deepcopy(self.state)
                with self.assertRaises(ValueError):
                    policy.apply(self.state, self.request, self.question, text)
                self.assertEqual(before, self.state)

    def test_changed_or_missing_setting_pin_rejects_the_whole_change(self):
        self.state["settings"]["limits"]["stage_timeout_seconds"] = 4000
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "changed since"):
            policy.apply(self.state, self.request, self.question, "Use 5400 seconds")
        self.assertEqual(before, self.state)

    def test_replaying_applied_change_is_rejected(self):
        policy.apply(self.state, self.request, self.question, "Use 5400 seconds")
        before = copy.deepcopy(self.state)
        with self.assertRaisesRegex(ValueError, "changed since"):
            policy.apply(self.state, self.request, self.question, "Use 5400 seconds")
        self.assertEqual(before, self.state)

    def test_missing_setting_is_pinned_distinctly_from_null(self):
        del self.state["settings"]["regression"]["test_timeout"]
        policy.bind(self.state, self.request, self.question)
        self.state["settings"]["regression"]["test_timeout"] = None
        with self.assertRaisesRegex(ValueError, "changed since"):
            policy.apply(self.state, self.request, self.question, "Use 5400 seconds")

    def test_invalid_proposal_cannot_apply_any_setting(self):
        for path, value in (
            ("settings.limits.max_seconds", 9000),
            ("settings.roles.sol.model", "other"),
            ("settings.regression.test_timeout", 0),
            ("settings.regression.test_timeout", -1),
            ("settings.regression.test_timeout", True),
            ("settings.regression.test_timeout", 1.5),
            ("settings.regression.test_timeout", "5400"),
            ("settings.regression.test_timeout", 10**100),
        ):
            with self.subTest(path=path, value=value):
                rows = copy.deepcopy(self.rows)
                rows[0]["changes"][path] = value
                request = {**self.request, "proposed_delta": json.dumps({"operational_options": rows})}
                before = copy.deepcopy(self.state)
                with self.assertRaises(ValueError):
                    policy.apply(self.state, request, self.question, "Use 5400 seconds")
                self.assertEqual(before, self.state)

    def test_incomplete_or_duplicate_choices_are_rejected(self):
        for rows in ([self.rows[0]], [self.rows[0], self.rows[0]]):
            with self.subTest(rows=rows):
                request = {**self.request, "proposed_delta": json.dumps({"operational_options": rows})}
                with self.assertRaises(ValueError):
                    policy.options(request)

    def test_operational_payload_cannot_be_a_contract_change(self):
        with self.assertRaises(ValueError):
            policy.options({**self.request, "kind": "goal_change"})

    def test_no_decline_option_is_rejected(self):
        request = {
            **self.request,
            "options": [self.rows[0]["option"]],
            "proposed_delta": json.dumps({"operational_options": [self.rows[0]]}),
        }
        with self.assertRaisesRegex(ValueError, "unchanged/decline"):
            policy.options(request)

    def test_legacy_prose_does_not_infer_settings_authority(self):
        before = copy.deepcopy(self.state)
        request = {**self.request, "proposed_delta": "Extend the timeout to 5400 seconds"}
        self.assertIsNone(policy.apply(self.state, request, self.question, "Approve"))
        self.assertEqual(before, self.state)
