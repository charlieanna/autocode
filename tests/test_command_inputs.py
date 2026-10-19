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
