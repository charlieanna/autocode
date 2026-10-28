"""Which pause holds a run, and what its own authority is: autocode_pause_authority and the records
autocode_stop keeps for a pause an intervention interrupted (#379, #486).

Pure functions over saved state, no processes. The CLI behaviour they decide is driven end to end in
tests.test_operational_pause_authority (a slow module); docs/bugs/2026-10-06-operational-pause-authority.md.
"""

import copy
import unittest

import autocode_pause_authority as pause_authority
import autocode_stop as stop


class ChangesHeldBoundTests(unittest.TestCase):
    def test_only_the_flag_for_the_exhausted_bound_changes_it(self):
        cases = (
            ("PAUSED_TIME_LIMIT", {}, "max_seconds"),
            ("PAUSED_COST_LIMIT", {}, "max_cost_usd"),
            ("PAUSED_COST_LIMIT", {"kind": "max_cost_usd"}, "max_cost_usd"),
            ("PAUSED_ITERATION_LIMIT", {}, "max_iterations"),
            ("PAUSED_ITERATION_LIMIT", {}, "unlimited_iterations"),
            ("PAUSED_MILESTONE_TIME_LIMIT", {}, "max_milestone_seconds"),
            # Neither records a bound kind in its request: the pause names it (run_setup.load_locked).
            ("PAUSED_MILESTONE_BUDGET", {}, "max_milestone_seconds"),
            ("PAUSED_MILESTONE_STALLED", {}, "max_milestone_replans"),
            ("PAUSED_NO_PROGRESS", {}, "no_progress_limit"),
            # A request after recovery burn-out names the bound it exhausted (#301).
            ("PAUSED_RESOLVER_OPERATIONAL", {"kind": "iteration_ceiling"}, "max_iterations"),
        )
        for pause, budget, flag in cases:
            with self.subTest(pause=pause, flag=flag):
                origin = {"pause_status": pause, **({"budget": budget} if budget else {})}
                self.assertTrue(pause_authority.changes_held_bound({flag}, origin))
                for other in (
                    "max_stage_seconds",
                    "max_idle_seconds",
                    "max_tool_seconds",
                    *({"max_seconds", "max_cost_usd"} - {flag}),
                ):
                    self.assertFalse(pause_authority.changes_held_bound({other}, origin), other)
                self.assertFalse(pause_authority.changes_held_bound(set(), origin))
        for pause in ("PAUSED_RATE_LIMIT", "PAUSED_TIMEOUT_RECOVERY", "PAUSED_RESOLVER_OPERATIONAL"):
            flags = {flag for flags in pause_authority.BUDGET_FLAGS.values() for flag in flags}
            self.assertFalse(pause_authority.changes_held_bound(flags, {"pause_status": pause}), pause)


class HeldPauseTests(unittest.TestCase):
    """The records behind the held pause: its cause, whether feedback is its authority, what an intervention keeps."""

    def test_held_cause_drops_the_advice_its_request_appended(self):
        def request(discovered, pause="PAUSED_RATE_LIMIT"):
            return {
                "identity": {
                    "proposal": {
                        "scope": "operational_exhaustion",
                        "origin": {"pause_status": pause},
                        "request": {"discovered": discovered},
                    }
                }
            }

        state = {
            "stop_reason": "Rate limited. AutoResolver could not resolve it. Provide information.",
            "resolver": {
                "human_escalations": {
                    "a": request("Rate limited"),
                    "b": request("Other", "PAUSED_TIME_LIMIT"),
                    "c": request("Rate"),
                }
            },
        }
        self.assertEqual("Rate limited", pause_authority.held_cause(state, "PAUSED_RATE_LIMIT"))
        self.assertEqual(state["stop_reason"], pause_authority.held_cause(state, "PAUSED_TIME_LIMIT"))
        state["stop_reason"] = "AutoResolver received the human response; no execution was authorized."
        self.assertEqual(state["stop_reason"], pause_authority.held_cause(state, "PAUSED_RATE_LIMIT"))

    def test_an_answered_request_still_names_the_cause_the_pause_holds_for(self):
        """respond_operational replaces the stop reason with its receipt note; the cause survives in the request."""
        cause = "Completed requirements_gather output was rejected ($: missing assistant_reply); attempt archived"
        escalation = {
            "status": "consumed",
            "identity": {
                "proposal": {
                    "scope": "operational_exhaustion",
                    "origin": {"pause_status": "PAUSED_REPEATED_FAILURE"},
                    "request": {"discovered": cause},
                }
            },
        }
        state = {
            "status": "PAUSED_REPEATED_FAILURE",
            "stop_reason": "AutoResolver received the human response; no execution was authorized.",
            "resolver": {
                "human_escalations": {"req": escalation},
                "human_response_frontier": {"request_id": "req", "pause_status": "PAUSED_REPEATED_FAILURE"},
            },
        }
        self.assertEqual(cause, pause_authority.answered_cause(state))
        # Before the answer the stop reason itself starts with the cause: nothing to add.
        state["stop_reason"] = cause + ". Provide corrective information or leave the run paused."
        self.assertEqual("", pause_authority.answered_cause(state))
        # A frontier at another pause is not this pause's cause.
        state["stop_reason"] = "AutoResolver received the human response; no execution was authorized."
        state["resolver"]["human_response_frontier"]["pause_status"] = "PAUSED_RATE_LIMIT"
        self.assertEqual("", pause_authority.answered_cause(state))

    def test_feedback_acknowledges_only_a_pause_that_offers_it(self):
        # The planning budget, and the validation-only stop whose request names --feedback (tests.test_rework_cli).
        def asked(request, at, pause="PAUSED_RESOLVER", status="pending"):
            return {
                "status": status,
                "issued_at": at,
                "identity": {
                    "proposal": {
                        "scope": "operational_exhaustion",
                        "origin": {"pause_status": pause},
                        "request": request,
                    }
                },
            }

        stalled = {"kind": "blocker", "discovered": "stalled", "finding_ids": ["F1"]}
        live = {
            "status": "WAITING_FOR_USER",
            "resolver_human_request": {"scope": "operational_exhaustion", "request_id": "b"},
            "resolver": {
                "human_escalations": {
                    "a": asked({"discovered": "older"}, "2026-10-06T01:00:00"),
                    "b": asked(stalled, "2026-10-06T02:00:00"),
                }
            },
        }
        self.assertTrue(pause_authority.feedback_acknowledges(live, "PAUSED_RESOLVER"))
        self.assertIsNone(pause_authority.feedback_refusal(live))
        withdrawn = {"status": "PAUSED_RESOLVER", "resolver": live["resolver"]}  # queued input applied under it
        self.assertTrue(pause_authority.feedback_acknowledges(withdrawn, "PAUSED_RESOLVER"))
        newer = {
            "status": "PAUSED_RESOLVER",
            "resolver": {
                "human_escalations": {
                    **live["resolver"]["human_escalations"],
                    "c": asked({"discovered": "other"}, "2026-10-06T03:00:00"),
                }
            },
        }
        self.assertFalse(pause_authority.feedback_acknowledges(newer, "PAUSED_RESOLVER"))
        self.assertIn("does not acknowledge PAUSED_RESOLVER", pause_authority.feedback_refusal(newer))
        self.assertTrue(pause_authority.feedback_acknowledges({}, "PAUSED_PLANNING_BUDGET"))
        self.assertIsNone(pause_authority.feedback_refusal({"status": "PAUSED_INVALID_OUTPUT"}))

    def test_queued_feedback_holds_only_a_pause_it_does_not_acknowledge(self):
        for held, holds in (
            ({"status": "PAUSED_RATE_LIMIT", "feedback": False}, True),
            ({"status": "PAUSED_RESOLVER", "feedback": True}, False),
            ({"status": "PAUSED_PLANNING_BUDGET"}, False),
            ({"status": "PAUSED_INVALID_OUTPUT", "feedback": False}, False),
        ):
            with self.subTest(held=held):
                state = {"status": stop.STOP_STATUS, "next_stage": "astra_discovery"}
                stop.boundary_effects(state, [{"id": "f", "kind": "feedback"}], lambda: "now", held)
                self.assertEqual(holds, "held_pause" in (state.get("pause_intent") or {}))
        # A pause in the same batch holds whatever the feedback would acknowledge (docs/interventions.md).
        state = {"status": stop.STOP_STATUS, "next_stage": "sol"}
        stop.boundary_effects(
            state,
            [{"id": "p", "kind": "pause"}, {"id": "f", "kind": "feedback"}],
            lambda: "now",
            {"status": "PAUSED_RESOLVER", "feedback": True},
        )
        self.assertEqual(
            ("PAUSED_RESOLVER", ["p"]),
            (state["pause_intent"]["held_pause"]["status"], state["pause_intent"]["request_ids"]),
        )

    def test_the_stop_reason_promises_only_what_the_held_pause_requires(self):
        # Any pause is returned to, but only an operational one waits for its own authority: a plain
        # --resume-paused releases an abandoned stage at once (#486 review).
        for held, operational in (("PAUSED_RATE_LIMIT", True), ("PAUSED_STAGE_ABANDONED", False)):
            with self.subTest(held=held):
                state = {"status": held, "next_stage": "sol"}
                stop.boundary_effects(
                    state,
                    [{"id": "p", "kind": "pause"}],
                    lambda: "now",
                    {"status": held, "stop_reason": "earlier stop"},
                )
                self.assertEqual(held, state["pause_intent"]["held_pause"]["status"])
                reason = state["stop_reason"]
                self.assertTrue(reason.startswith("Queued pause was applied"), reason)
                self.assertIn(held, reason)
                self.assertIn("--resume-paused returns to", reason)
                self.assertEqual(operational, "only its own authority releases it" in reason, reason)

    def test_a_later_intervention_keeps_the_pause_the_first_one_interrupted(self):
        held = {"status": "PAUSED_RATE_LIMIT", "stop_reason": "Rate limited"}
        state = {"status": stop.STOP_STATUS, "pause_intent": {"acknowledged_at": None, "held_pause": held}}
        self.assertEqual(held, stop.interrupted_pause(state))
        state["pause_intent"]["acknowledged_at"] = "2026-10-06T00:00:00+00:00"
        self.assertIsNone(stop.interrupted_pause(state))
        self.assertIsNone(stop.interrupted_pause({"status": "RUNNING"}))
        self.assertEqual("PAUSED_TIME_LIMIT", stop.interrupted_pause({"status": "PAUSED_TIME_LIMIT"})["status"])
        self.assertEqual(stop.STOP_STATUS, pause_authority.INTERVENTION_STATUS)

    def test_a_correction_is_refused_at_the_pause_an_intervention_interrupted(self):
        held = {"status": "PAUSED_RATE_LIMIT", "stop_reason": "Rate limited"}
        state = {
            "status": pause_authority.INTERVENTION_STATUS,
            "pause_intent": {"acknowledged_at": None, "held_pause": held},
        }
        self.assertIn(
            "An edited goal does not acknowledge PAUSED_RATE_LIMIT",
            pause_authority.correction_refusal(state, "An edited goal"),
        )
        self.assertIn("Queued feedback is applied under the pause", pause_authority.feedback_refusal(state))
        state["pause_intent"]["acknowledged_at"] = "2026-10-07T00:00:00+00:00"  # resumed: nothing interrupted
        self.assertIsNone(pause_authority.correction_refusal(state, "An edited goal"))
        for status in ("PAUSED_PLANNING_BUDGET", "PAUSED_STAGE_ABANDONED", "AWAITING_GOAL_APPROVAL"):
            self.assertIsNone(pause_authority.correction_refusal({"status": status}, "An edited goal"), status)


class PlanningReconsiderationTests(unittest.TestCase):
    def test_only_abandonment_defers_exhaustion_to_its_existing_handler(self):
        from pathlib import Path
        from types import SimpleNamespace
        from unittest.mock import Mock, patch

        import autocode as runner
        import autocode_action_authority as authority
        import autocode_args

        parser = autocode_args.build_parser(None, runner.DEFAULT_ROLE_MODELS)
        for pause in ("PAUSED_RESOLVER_OPERATIONAL", "PAUSED_TIMEOUT_RECOVERY"):
            for flags in (
                (),
                ("--retry-builder", "M1"),
                ("--retry-failed-stage",),
                ("--retry-report", "001/other"),
                ("--diagnose-failed-stage",),
                ("--max-stage-seconds", "900"),
                ("--abandon-stage", "001/plan-review-01"),
            ):
                with self.subTest(pause=pause, flags=flags):
                    state = {"status": pause, "next_stage": "astra_challenge", "settings": {"joint_planning": True}}
                    args = parser.parse_args(flags)
                    decided = SimpleNamespace(decision_action=False, information_admitted=False, checked_authority=True)
                    fake_runner = Mock()
                    with (
                        patch.object(authority.resolver_runtime, "record_operational_exhaustion") as publish,
                        patch.object(authority.lifecycle, "render", return_value="Planning recovery held"),
                    ):
                        result = authority.reconsider(
                            fake_runner,
                            args,
                            parser,
                            state,
                            Path("/unused/state.json"),
                            Path("/unused"),
                            Path("/workspace"),
                            decided,
                        )
                    if "--abandon-stage" in flags:
                        self.assertIsNone(result)
                        publish.assert_not_called()
                        fake_runner.write_json.assert_not_called()
                    else:
                        self.assertEqual(2, result)
                        publish.assert_called_once()


def answered_blocker_state():
    """The #2434 live shape: a semantic blocker answered with information, holding its pause for
    the contract decision it asked about. The authenticated pieces are the consumed entry with its
    ask-time binding, the response that consumed it, and the frontier holding the current pause."""
    contract = {"task_id": "t1", "revision": 3, "hash": "h3", "approval_status": "approved"}
    response = {
        "request_id": "req-1",
        "request_token": "tok",
        "action": "provide_information",
        "text": "Finish the member's local gates; keep joint proof as the whole-set gate",
        "actor": "user_cli",
        "at": "2026-10-28T20:45:00+00:00",
    }
    return {
        "status": "PAUSED_RESOLVER",
        "goal_contract": contract,
        "resolver_human_request": None,
        "resolver_human_proposal": None,
        "resolver": {
            "human_escalations": {
                "req-1": {
                    "status": "consumed",
                    "identity": {
                        "issuer": "resolver",
                        "binding": {"contract": {"hash": "h3", "revision": 3, "approval_status": "approved"}},
                        "proposal": {
                            "scope": "blocker",
                            "origin": {},
                            "previous_status": "PAUSED_RESOLVER",
                            "request": {"kind": "blocker", "options": ["Provide evidence", "Revise the contract"]},
                        },
                    },
                    "response": response,
                }
            },
            "human_response_frontier": {
                "request_id": "req-1",
                "pause_status": "PAUSED_RESOLVER",
                "binding": {"contract": {"hash": "h3", "revision": 3, "approval_status": "approved"}},
            },
            "pending_human_response": None,
        },
    }


class AnsweredBlockerDecisionTests(unittest.TestCase):
    """The hold an answered semantic blocker keeps is its contract decision's own authority (#2434)."""

    def test_the_answered_blocker_admits_its_correction(self):
        state = answered_blocker_state()
        self.assertTrue(pause_authority.answered_blocker_decision(state))
        self.assertTrue(pause_authority.feedback_acknowledges(state, "PAUSED_RESOLVER"))
        self.assertIsNone(pause_authority.feedback_refusal(state))
        self.assertIsNone(pause_authority.correction_refusal(state, "An edited goal"))
        self.assertIsNone(pause_authority.correction_refusal(state, "A plan file"))

    def test_the_pending_response_window_needs_the_consumed_entrys_exact_record(self):
        state = answered_blocker_state()
        response = state["resolver"]["human_escalations"]["req-1"]["response"]
        state["resolver"].pop("human_response_frontier")
        state["resolver"]["pending_human_response"] = copy.deepcopy(response)
        self.assertTrue(pause_authority.answered_blocker_decision(state))
        stale = {**response, "text": "an older reply naming the same request"}
        state["resolver"]["pending_human_response"] = stale
        self.assertFalse(pause_authority.answered_blocker_decision(state))
        state["resolver"]["pending_human_response"] = {**response, "request_id": "req-2"}
        self.assertFalse(pause_authority.answered_blocker_decision(state))

    def test_only_the_current_authenticated_answer_qualifies(self):
        def break_it(name):
            state = answered_blocker_state()
            entry = state["resolver"]["human_escalations"]["req-1"]
            if name == "request still pending":
                entry["status"] = "pending"
            elif name == "operational scope":
                entry["identity"]["proposal"]["scope"] = "operational_exhaustion"
            elif name == "leave_paused reply":
                entry["response"] = {**entry["response"], "action": "leave_paused"}
                state["resolver"]["pending_human_response"] = entry["response"]
                state["resolver"].pop("human_response_frontier")
            elif name == "response names another request":
                entry["response"] = {**entry["response"], "request_id": "req-2"}
            elif name == "response actor is not the person":
                entry["response"] = {**entry["response"], "actor": "resolver"}
            elif name == "a live published request":
                state["resolver_human_request"] = {"request_id": "req-9", "scope": "blocker"}
            elif name == "a queued proposal":
                state["resolver_human_proposal"] = {
                    "scope": "operational_exhaustion",
                    "origin": {"stage": "astra_resolve", "pause_status": "PAUSED_RESOLVER"},
                }
            elif name == "the decision was already taken":
                state["goal_contract"]["approval_status"] = "draft"
            elif name == "the contract changed since the ask":
                state["goal_contract"] = {
                    **state["goal_contract"],
                    "revision": 4,
                    "hash": "h4",
                    "approval_status": "approved",
                }
            elif name == "the frontier holds another pause":
                state["resolver"]["human_response_frontier"]["pause_status"] = "PAUSED_COMPLETION_GATE"
            elif name == "another pause holds the run":
                # A pause intervention interrupted the hold: its recorded intent governs (#379).
                state["pause_intent"] = {"held_pause": {"status": "PAUSED_RESOLVER"}}
                state["status"] = "PAUSED_INTERVENTION"
            elif name == "a bound pause held the blocker":
                entry["identity"]["proposal"]["previous_status"] = "PAUSED_TIME_LIMIT"
                state["status"] = "PAUSED_TIME_LIMIT"
                state["resolver"]["human_response_frontier"]["pause_status"] = "PAUSED_TIME_LIMIT"
            elif name == "a refusal pause held the blocker":
                entry["identity"]["proposal"]["previous_status"] = "PAUSED_PROVIDER_UNCERTAIN"
                state["status"] = "PAUSED_PROVIDER_UNCERTAIN"
                state["resolver"]["human_response_frontier"]["pause_status"] = "PAUSED_PROVIDER_UNCERTAIN"
            elif name == "no binding recorded at the ask":
                entry["identity"]["binding"] = {}
            else:
                raise AssertionError(name)
            return state

        for name in (
            "request still pending",
            "operational scope",
            "leave_paused reply",
            "response names another request",
            "response actor is not the person",
            "a live published request",
            "a queued proposal",
            "the decision was already taken",
            "the contract changed since the ask",
            "the frontier holds another pause",
            "another pause holds the run",
            "a bound pause held the blocker",
            "a refusal pause held the blocker",
            "no binding recorded at the ask",
        ):
            with self.subTest(name=name):
                state = break_it(name)
                self.assertFalse(pause_authority.answered_blocker_decision(state))
                self.assertFalse(pause_authority.feedback_acknowledges(state, state["status"]))
                refusal = pause_authority.feedback_refusal(state)
                if state["status"] not in ("PAUSED_TIME_LIMIT", "PAUSED_PROVIDER_UNCERTAIN"):
                    self.assertIsNotNone(refusal)
                self.assertIsNotNone(pause_authority.correction_refusal(state, "An edited goal"))

    def test_every_other_pause_a_blocker_can_arise_at_keeps_its_own_authority(self):
        """The decision's exception is the Resolver's own pause, an allow-list entry; protected
        operational holds admit no correction through an answered blocker's ledger record."""
        protected = (
            "PAUSED_BUDGET",
            "PAUSED_OUTPUT_CAP",
            "PAUSED_CONTEXT_WINDOW",
            "PAUSED_REPORT_REPAIR_LIMIT",
            "PAUSED_RATE_LIMIT",
            "PAUSED_REPEATED_FAILURE",
            "PAUSED_RESOLVER_OPERATIONAL",
            "PAUSED_BUILDER_RETRY_LIMIT",
            "PAUSED_TIME_LIMIT",
            "PAUSED_PROVIDER_UNCERTAIN",
            "PAUSED_UNCERTAIN_STAGE",
        )
        for pause in protected:
            with self.subTest(pause=pause):
                state = answered_blocker_state()
                entry = state["resolver"]["human_escalations"]["req-1"]
                entry["identity"]["proposal"]["previous_status"] = pause
                entry["identity"]["proposal"]["origin"] = {"pause_status": pause}
                state["status"] = pause
                state["resolver"]["human_response_frontier"]["pause_status"] = pause
                self.assertFalse(pause_authority.answered_blocker_decision(state))
                self.assertFalse(pause_authority.feedback_acknowledges(state, pause))
                self.assertIn("does not acknowledge", pause_authority.feedback_refusal(state))
                self.assertIn("does not acknowledge", pause_authority.correction_refusal(state, "An edited goal"))

    def test_the_planning_budget_pause_keeps_its_own_feedback_authority(self):
        # Pre-existing, separately admitted (#1294); the answered-blocker exception neither
        # widens nor narrows it.
        self.assertTrue(pause_authority.feedback_acknowledges({"status": "PAUSED_PLANNING_BUDGET"}, "PAUSED_PLANNING_BUDGET"))
        state = answered_blocker_state()
        state["status"] = "PAUSED_PLANNING_BUDGET"
        self.assertFalse(pause_authority.answered_blocker_decision(state))

    def test_an_operational_answer_keeps_its_information_path_only(self):
        state = answered_blocker_state()
        entry = state["resolver"]["human_escalations"]["req-1"]
        entry["identity"]["proposal"]["scope"] = "operational_exhaustion"
        entry["identity"]["proposal"]["previous_status"] = "PAUSED_REPEATED_FAILURE"
        entry["identity"]["proposal"]["origin"] = {"pause_status": "PAUSED_REPEATED_FAILURE"}
        state["status"] = "PAUSED_REPEATED_FAILURE"
        state["resolver"]["human_response_frontier"]["pause_status"] = "PAUSED_REPEATED_FAILURE"
        self.assertFalse(pause_authority.answered_blocker_decision(state))
        self.assertIn("does not acknowledge", pause_authority.feedback_refusal(state))
        self.assertIn("does not acknowledge", pause_authority.correction_refusal(state, "An edited goal"))


if __name__ == "__main__":
    unittest.main()
