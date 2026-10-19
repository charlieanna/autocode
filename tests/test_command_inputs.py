"""Finite total-time decisions are explicit, exact-command bound public offers."""

import copy
import shlex
import unittest
from unittest import mock

import autocode_args
import autocode_command_inputs as inputs
import autocode_configure
import autocode_recovery_view as recovery
import autocode_run_view as run_view


def checkpoint(**changes):
    return {
        "status": "PAUSED_TIME_LIMIT",
        "workspace": "/tmp/project",
        "run_dir": "/tmp/run",
        "settings": {"limits": {"max_seconds": 2700, "max_cost_usd": 2}},
        "active_seconds": 2800.5,
        **changes,
    }


class CommandInputTests(unittest.TestCase):
    def grant_checkpoint(self):
        return checkpoint(
            status="PAUSED_TIMEOUT_RECOVERY",
            automatic_recoveries_since_resume=3,
            consecutive_timeout_recoveries=3,
            automatic_timeout_recoveries=[{"stage": "astra_challenge"}],
            recovery_grants=[],
        )

    def test_additional_recovery_input_replenishes_only_counted_slots(self):
        state = self.grant_checkpoint()
        before = copy.deepcopy(state)
        view = run_view.view(state)
        self.assertEqual(
            [
                {
                    "id": "grant_recovery",
                    "kind": "integer",
                    "flag": "--grant-recovery",
                    "placeholder": "N",
                    "arg_index": 2,
                    "label": "Additional recoveries",
                    "current": 3,
                    "used": 3,
                    "minimum": 1,
                    "maximum": 3,
                }
            ],
            view["needs"]["command_inputs"],
        )
        self.assertIn("--resume-paused --grant-recovery N --expected-recovery-token -", view["next_command"])
        self.assertEqual(before, state)
        for used in (None, True, 0, -1, 3.5, inputs.MAX_INTEGER + 1, 10**1000):
            bad = copy.deepcopy(state)
            bad["automatic_recoveries_since_resume"] = used
            self.assertIsNone(inputs.project(bad, {"kind": "resume"}, inputs.GRANT_ACTION))
        for action in ("--resume-paused", "--grant-recovery N", inputs.GRANT_ACTION + " --approve-goal -"):
            self.assertIsNone(inputs.project(state, {"kind": "resume", "action": action}, None))
        for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts"):
            bad = copy.deepcopy(state)
            bad[key] = {"fixture": True}
            self.assertIsNone(inputs.project(bad, {"kind": "resume"}, inputs.GRANT_ACTION))
        with mock.patch.object(run_view.operational_information, "projection", return_value={"status": "pending"}):
            self.assertNotIn("command_inputs", run_view.view(state)["needs"])

    def test_recovery_spend_fallbacks_and_grant_history_expire_numeric_offer(self):
        state = self.grant_checkpoint()
        token = recovery.token(state)
        for key, value in (
            ("automatic_recoveries_since_resume", 2),
            ("consecutive_timeout_recoveries", 2),
            ("automatic_timeout_recoveries", []),
            ("no_progress_batches", 4),
            ("recovery_grants", [{"amount": 1, "previous_count": 3, "remaining_count": 2}]),
        ):
            changed = copy.deepcopy(state)
            changed[key] = value
            with self.subTest(key=key), self.assertRaisesRegex(ValueError, "saved pause changed"):
                recovery.require_token(changed, token)
        legacy = copy.deepcopy(state)
        del legacy["automatic_recoveries_since_resume"]
        legacy["no_progress_batches"] = 4
        self.assertEqual(4, run_view.view(legacy)["needs"]["command_inputs"][0]["maximum"])

    def planning_checkpoint(self, **changes):
        return checkpoint(
            status="PAUSED_PLANNING_BUDGET",
            next_stage="astra_challenge",
            planning={"astra_calls": 2, "review_call_limit": 2},
            **changes,
        )

    def test_planning_total_preserves_both_resume_and_held_save_only_shapes(self):
        state = self.planning_checkpoint()
        before = copy.deepcopy(state)
        for action in (None, inputs.PLANNING_ACTION, inputs.PLANNING_SAVE_THEN_RESUME):
            held = action is not None
            review = {"status": "held", "action": action} if held else None
            with mock.patch.object(run_view.operational_information, "projection", return_value=review):
                view = run_view.view(state)
            row = view["needs"]["command_inputs"][0]
            self.assertEqual(
                {
                    "id": "planning_review_call_limit",
                    "kind": "integer",
                    "flag": "--planning-review-call-limit",
                    "placeholder": "N",
                    "arg_index": 1 if held else 2,
                    "label": "Total plan-review calls",
                    "current": 2,
                    "used": 2,
                    "minimum": 3,
                    "maximum": inputs.MAX_INTEGER,
                },
                row,
            )
            self.assertNotIn("default", row)
            words = shlex.split(view["next_command"])
            variable, token = words.pop(0).split("=", 1)
            self.assertEqual(("AUTOCODE_EXPECTED_RECOVERY_TOKEN", view["recovery"]["token"]), (variable, token))
            expected = ([] if held else ["--resume-paused"]) + [
                "--planning-review-call-limit",
                "N",
                "--expected-recovery-token",
                "-",
            ]
            self.assertEqual(expected, words[5:])
            expected[expected.index("N")] = "4"
            with mock.patch.dict("os.environ", {variable: token}):
                args, _ = autocode_args.parse(None, words[1:5] + expected, autocode_configure.DEFAULT_ROLE_MODELS)
            self.assertEqual(4, args.planning_review_call_limit)
            self.assertEqual(not held, args.resume_paused)
            self.assertFalse(args.approve_goal)
        self.assertEqual(before, state)

    def test_planning_offer_rejects_unreconciled_or_malformed_accounting(self):
        need = {"kind": "planning_budget"}
        with mock.patch.object(run_view.operational_information, "projection", return_value={"status": "pending"}):
            pending = run_view.view(self.planning_checkpoint())
        self.assertNotIn("command_inputs", pending["needs"])
        self.assertEqual("--resume-paused", pending["needs"]["action"])
        available = self.planning_checkpoint()
        available["planning"]["review_call_limit"] = 4
        resumed = run_view.view(available)
        self.assertNotIn("command_inputs", resumed["needs"])
        self.assertEqual("--resume-paused", resumed["needs"]["action"])
        for changes in (
            {"next_stage": "terra"},
            {"active_stage": {"stage": "astra_challenge"}},
            {"pending_report_repair": {"attempts": 1}},
            {"uncertain_artifacts": ["artifact"]},
        ):
            state = self.planning_checkpoint()
            state.update(changes)
            self.assertIsNone(inputs.project(state, need, None))
        for current, used in (
            (0, 2),
            (1, 2),
            (True, 2),
            (2.5, 2),
            (inputs.MAX_INTEGER, 2),
            (2, None),
            (2, True),
            (2, -1),
            (2, 2.5),
            (2, inputs.MAX_INTEGER),
        ):
            state = self.planning_checkpoint()
            state["planning"].update(review_call_limit=current, astra_calls=used)
            self.assertIsNone(inputs.project(state, need, None))
        for action in ("--resume-paused", "--planning-review-call-limit N --approve-goal -", "--max-seconds N"):
            self.assertIsNone(inputs.project(self.planning_checkpoint(), {**need, "action": action}, None))
        state = self.planning_checkpoint()
        state["planning"].update(review_call_limit=4, astra_calls=6)
        self.assertEqual(7, inputs.project(state, need, None)[0]["minimum"])

    def test_planning_accounting_is_bound_to_recovery_freshness(self):
        state = self.planning_checkpoint()
        token = recovery.token(state)
        for key in ("astra_calls", "review_call_limit"):
            changed = copy.deepcopy(state)
            changed["planning"][key] += 1
            with self.assertRaisesRegex(ValueError, "saved pause changed"):
                recovery.require_token(changed, token)

    def test_exact_total_time_offer_is_blank_bounded_and_private_token_bound(self):
        state = checkpoint()
        before = copy.deepcopy(state)
        view = run_view.view(state)
        offer = view["needs"]["command_inputs"]
        self.assertEqual(
            [
                {
                    "id": "max_seconds",
                    "kind": "integer",
                    "flag": "--max-seconds",
                    "placeholder": "N",
                    "arg_index": 2,
                    "label": "Total active time (seconds)",
                    "current": 2700,
                    "elapsed": 2800.5,
                    "minimum": 2801,
                    "maximum": inputs.MAX_INTEGER,
                }
            ],
            offer,
        )
        self.assertNotIn("default", offer[0])
        words = shlex.split(view["next_command"])
        variable, token = words.pop(0).split("=", 1)
        self.assertEqual("AUTOCODE_EXPECTED_RECOVERY_TOKEN", variable)
        self.assertEqual(view["recovery"]["token"], token)
        self.assertEqual(["--resume-paused", "--max-seconds", "N", "--expected-recovery-token", "-"], words[5:])
        self.assertNotIn(token, words)
        words[words.index("N")] = "5400"  # Only this explicit person choice supplies the total.
        with mock.patch.dict("os.environ", {variable: token}):
            args, _ = autocode_args.parse(None, words[1:], autocode_configure.DEFAULT_ROLE_MODELS)
        self.assertEqual((5400, token), (args.max_seconds, args.expected_recovery_token))
        self.assertFalse(args.approve_goal)
        self.assertEqual(before, state)

    def test_minimum_exceeds_both_saved_cap_and_consumed_time(self):
        for elapsed, minimum in ((0, 2701), (2699.5, 2701), (2700, 2701), (2800, 2801)):
            with self.subTest(elapsed=elapsed):
                view = run_view.view(checkpoint(active_seconds=elapsed))
                self.assertEqual(minimum, view["needs"]["command_inputs"][0]["minimum"])

    def test_only_exact_finite_total_control_gets_an_input(self):
        need = {"kind": "resume"}
        for action in (
            "--resume-paused",
            "--resume-paused --max-cost-usd N",
            "--resume-paused --max-seconds N --no-chat",
        ):
            self.assertIsNone(inputs.project(checkpoint(), need, action))
        for extra in ({"kind": "answer"}, {"edit_required": True}, {"new_run_required": True}, {"action": "--explain"}):
            self.assertIsNone(inputs.project(checkpoint(), {**need, **extra}, inputs.TOTAL_TIME_ACTION))
        for current, elapsed in (
            (0, 2800),
            (-1, 2800),
            (True, 2800),
            (2700, None),
            (2700, True),
            (2700, -1),
            (2700, float("nan")),
            (2700, float("inf")),
            (inputs.MAX_INTEGER, 2800),
            (2700, inputs.MAX_INTEGER),
            (2700, 10**1000),
        ):
            with self.subTest(current=current, elapsed=elapsed):
                state = checkpoint(settings={"limits": {"max_seconds": current}}, active_seconds=elapsed)
                self.assertIsNone(inputs.project(state, need, inputs.TOTAL_TIME_ACTION))
        terminal = run_view.view(checkpoint(applied_interventions=[{"kind": "stop"}]))
        self.assertIsNone(terminal["next_command"])
        self.assertNotIn("command_inputs", terminal["needs"])

    def test_recovery_token_expires_when_consumed_time_or_other_settings_change(self):
        state = checkpoint()
        token = recovery.token(state)
        for changed in (checkpoint(active_seconds=2801), checkpoint(settings={"limits": {"max_seconds": 3000}})):
            with self.assertRaisesRegex(ValueError, "saved pause changed"):
                recovery.require_token(changed, token)

    def test_ordinary_cli_zero_semantics_are_preserved(self):
        args, _ = autocode_args.parse(
            None, ["--run-dir", "/tmp/run", "--max-seconds", "0"], autocode_configure.DEFAULT_ROLE_MODELS
        )
        self.assertEqual(0, args.max_seconds)


if __name__ == "__main__":
    unittest.main()
