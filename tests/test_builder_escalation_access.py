"""A stronger Builder route AutoCode chose, and its account cannot use.

The Builder lane escalates to its stronger route on its own. When that route's access check
fails (an expired login, no credit), the route was never the person's choice, so AutoCode
puts the Builder and its checkers back, records why, and ends the lane as it does for a
stronger model the provider does not offer. A route a person named keeps its access pause.
Undoing the escalation, or the escalation itself, leaves a held repair's binding intact.
"""

import copy
import json
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_builder_policy as policy
import autocode_model_preflight as preflight
import autocode_resolver_recovery as recovery
import autocode_stop as stop
import autocode_support as support

STRONG = policy.DEFAULTS["strong_model"]
REJECTED = [
    {
        "type": "error",
        "sessionID": "ses_auth",
        "error": {"name": "APIError", "data": {"statusCode": 401, "message": "token expired"}},
    }
]
OK_EVENTS = [
    {"type": "step_start", "sessionID": "ses_probe", "part": {"id": "p1", "sessionID": "ses_probe"}},
    {"type": "step_finish", "sessionID": "ses_probe", "part": {"id": "p1", "sessionID": "ses_probe", "reason": "stop"}},
]


def run_state(origin="runner", strong=STRONG):
    retry = {**policy.DEFAULTS, "strong_model": strong}
    if origin is None:
        retry.pop("strong_origin")  # a run saved before strong_origin existed
    else:
        retry["strong_origin"] = origin
    return {
        "settings": {
            "engine": "opencode",
            "builder_retry": retry,
            "roles": {
                "builder": {"engine": "opencode", "model": "plan/mimo", "reasoning_effort": "high"},
                "tester": {"engine": "opencode", "model": STRONG, "reasoning_effort": "high"},
                "task_planner": {"engine": "opencode", "model": "plan/glm", "reasoning_effort": "high"},
            },
        },
        "goal_contract": {"hash": "approved", "revision": 3},
        "current_task": {"id": "task1", "milestone_id": "M1"},
        "status": "RUNNING",
        "sessions": {"builder": "builder-session", "tester": "tester-session"},
    }


def escalated(origin="runner", strong=STRONG):
    state = run_state(origin, strong)
    assert policy.failure(state, "e1", "failure") == "retry"
    assert policy.failure(state, "e2", "failure") == "escalate"
    state["sessions"] = {"builder": "strong-session", "tester": "checker-session"}
    return state


def roles(state):
    return {role: (route["model"], route["reasoning_effort"]) for role, route in state["settings"]["roles"].items()}


class Origin(unittest.TestCase):
    def test_a_new_run_records_whose_stronger_route_it_is(self):
        self.assertEqual("runner", policy.configured()["strong_origin"])
        self.assertEqual("user_explicit", policy.configured("openai/gpt-6-luna")["strong_origin"])


class EscalationRoutes(unittest.TestCase):
    def test_the_lane_lists_its_route_before_and_after_escalating(self):
        self.assertEqual([], policy.escalation_routes(run_state()))
        state = escalated()
        self.assertEqual(
            [
                {"builder": ("plan/mimo", "high"), "tester": (STRONG, "high")},
                {"builder": (STRONG, "xhigh"), "tester": (policy.DEFAULTS["checker_model"], "high")},
            ],
            policy.escalation_routes(state),
        )

    def test_another_milestone_and_malformed_rows_add_nothing(self):
        state = escalated()
        state["builder_retry_decisions"].append({"action": "escalate", "milestone_key": "other"})
        state["builder_retry_decisions"].append("malformed")
        self.assertEqual(2, len(policy.escalation_routes(state)))
        state["current_task"] = {"id": "task2", "milestone_id": "M2"}
        self.assertEqual([], policy.escalation_routes(state))


class Withdraw(unittest.TestCase):
    def withdraw(self, state, model=STRONG):
        return policy.withdraw_unreachable(state, model, detail="HTTP 401", evidence="/run/probe")

    def test_autocodes_unusable_stronger_route_is_put_back_and_the_lane_ends_without_a_person(self):
        state = escalated()
        before = run_state()
        self.assertTrue(self.withdraw(state))
        self.assertEqual(roles(before), roles(state))
        self.assertEqual({}, state["sessions"])
        lane = state["builder_retries"][policy.key(state)]
        self.assertEqual("pause", lane["action"])
        self.assertEqual({"model": STRONG, "detail": "HTTP 401", "evidence": "/run/probe"}, lane["strong_unreachable"])
        decision = state["builder_retry_decisions"][-1]
        self.assertEqual(
            ("pause", "plan/mimo", "high"),
            (decision["action"], decision["selected_model"], decision["selected_effort"]),
        )
        self.assertIn(STRONG, decision["reason"])
        self.assertEqual(stop.STOP_STATUS, state["status"])
        outcome = stop.outcome(state)
        self.assertEqual("could_not_finish", outcome["result"])
        self.assertIn("cannot use " + STRONG, outcome["reason"])
        self.assertIn("/run/probe", outcome["reason"])
        # The lane stays ended: a later failure never offers the unusable route again.
        self.assertEqual("pause", policy.failure(state, "e3", "failure"))
        self.assertEqual(roles(before), roles(state))

    def test_the_lane_decides_from_a_recorded_unusable_route_as_from_an_unoffered_one(self):
        state = escalated()
        lane = state["builder_retries"][policy.key(state)]
        lane["strong_unreachable"] = {"model": STRONG, "detail": "HTTP 401", "evidence": "/run/probe"}
        action, checkers, reason = policy._escalate(state, lane, state["settings"]["builder_retry"], {})
        self.assertEqual(("pause", {}), (action, checkers))
        self.assertIn("cannot use " + STRONG, reason)

    def test_a_route_a_person_named_or_outside_the_lane_is_left_alone(self):
        cases = {
            "a person named the stronger route": (escalated("user_explicit"), STRONG),
            "an unfamiliar origin": (escalated("someone"), STRONG),
            "a legacy run on a non-default stronger route": (escalated(None, "openai/gpt-6-luna"), "openai/gpt-6-luna"),
            "the probed model is not the Builder's": (escalated(), "plan/glm"),
            "the lane has not escalated": (run_state(), "plan/mimo"),
        }
        for name, (state, model) in cases.items():
            with self.subTest(name):
                before = copy.deepcopy(state)
                self.assertFalse(self.withdraw(state, model))
                self.assertEqual(before, state)

    def test_a_legacy_run_on_the_default_stronger_route_is_autocodes_choice(self):
        state = escalated(None)
        self.assertTrue(self.withdraw(state))
        self.assertEqual("plan/mimo", state["settings"]["roles"]["builder"]["model"])

    def test_a_builder_whose_own_route_was_the_stronger_model_is_left_alone(self):
        state = run_state()
        state["settings"]["roles"]["builder"]["model"] = STRONG
        state["builder_retries"] = {
            policy.key(state): {"action": "escalate", "failures": ["e"], "initial_route": {"model": STRONG}}
        }
        before = copy.deepcopy(state)
        self.assertFalse(self.withdraw(state))
        self.assertEqual(before, state)

    def test_a_run_without_the_lane_is_left_alone(self):
        state = escalated()
        state["settings"]["builder_retry"]["enabled"] = False
        before = copy.deepcopy(state)
        self.assertFalse(self.withdraw(state))
        self.assertEqual(before, state)


class HeldRepair(unittest.TestCase):
    """A repair bound on one of the lane's own routes still holds on another."""

    def test_the_binding_holds_across_the_escalation_and_its_undoing(self):
        state = escalated()
        on_strong = recovery._binding(state, "rev")
        undone = copy.deepcopy(state)
        undone["settings"]["roles"]["builder"].update(model="plan/mimo", reasoning_effort="high")
        undone["settings"]["roles"]["tester"].update(model=STRONG, reasoning_effort="high")
        self.assertNotEqual(on_strong, recovery._binding(undone, "rev"))
        self.assertTrue(recovery._holds(on_strong, undone, "rev"))
        self.assertTrue(recovery._holds(recovery._binding(undone, "rev"), state, "rev"))

    def test_a_route_outside_the_lane_still_stales_the_binding(self):
        state = escalated()
        saved = recovery._binding(state, "rev")
        for terra, sol in (("plan/other", "high"), ("plan/mimo", "xhigh")):
            with self.subTest(builder=terra, tester=sol):
                moved = copy.deepcopy(state)
                moved["settings"]["roles"]["builder"].update(model=terra, reasoning_effort="high")
                moved["settings"]["roles"]["tester"].update(model=STRONG, reasoning_effort=sol)
                self.assertFalse(recovery._holds(saved, moved, "rev"))
        # Half the lane's route (the Builder put back, its checker not) is not one of its routes.
        half = copy.deepcopy(state)
        half["settings"]["roles"]["builder"].update(model="plan/mimo", reasoning_effort="high")
        self.assertFalse(recovery._holds(saved, half, "rev"))


class Preflight(unittest.TestCase):
    """The access check before a stage, with the lane on AutoCode's stronger route."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.adapter = types.SimpleNamespace(
            NAME="fixture",
            _config={"auth": {"command": ["kilo", "auth", "list"], "routes": []}},
            check_subscription_routes=lambda roles, workspace=None, env=None: None,
        )
        self.saves = 0

    def check(self, state, events):
        state["settings"]["transport_identities"] = {
            "opencode": {
                "engine": "fixture",
                "identity_version": 1,
                "executable": "/fixture/provider",
                "version": "1.0",
                "config_path": "/fixture/provider.toml",
                "config_sha256": "a" * 64,
            }
        }
        state["next_stage"] = "builder"

        def execute(**kwargs):
            kwargs["directory"].mkdir(parents=True, exist_ok=True)
            rows = events[kwargs["model"]]
            (kwargs["directory"] / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            return {
                "exit_code": 0,
                "timed_out": False,
                "report": None,
                "rows": rows,
                "report_error": None,
                "metrics": {},
            }

        def checkpoint():
            self.saves += 1

        runner = types.SimpleNamespace(opencode=self.adapter)
        with patch.object(preflight.transport, "execute", side_effect=execute):
            return preflight.check(runner, state, self.run_dir, self.root, checkpoint=checkpoint)

    def events(self):
        return {
            STRONG: REJECTED,
            "plan/mimo": OK_EVENTS,
            "plan/glm": OK_EVENTS,
            policy.DEFAULTS["checker_model"]: OK_EVENTS,
        }

    def test_autocodes_stronger_route_failing_its_login_ends_the_lane_on_the_builders_route(self):
        state = escalated()
        with self.assertRaises(support.Paused) as caught:
            self.check(state, self.events())
        self.assertEqual(stop.STOP_STATUS, caught.exception.status)
        self.assertNotEqual("PAUSED_AUTH", state["status"])
        self.assertIn("cannot use " + STRONG, str(caught.exception))
        self.assertEqual(roles(run_state()), roles(state))
        self.assertGreater(self.saves, 0)

    def test_a_stronger_route_a_person_named_keeps_its_access_pause(self):
        state = escalated("user_explicit")
        with self.assertRaises(support.Paused) as caught:
            self.check(state, self.events())
        self.assertEqual("PAUSED_AUTH", caught.exception.status)
        self.assertEqual(STRONG, state["settings"]["roles"]["builder"]["model"])


if __name__ == "__main__":
    unittest.main()
