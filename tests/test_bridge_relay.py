"""The bridge relay: gates become channel messages once, and a reply becomes one action."""

import unittest

import autocode_bridge_relay as relay

WORKSPACE = "/work/project"
RUN = "/work/project/.autocode/runs/r1"


def answer_view(**need):
    return {
        "status": "WAITING_FOR_USER",
        "needs": {
            "kind": "answer",
            "resolver_request_id": "req-1",
            "resolver_token": "t1",
            "questions": [
                {
                    "id": "q-1",
                    "question": "Which database?",
                    "why": "It sets the migration path",
                    "options": ["sqlite", "postgres"],
                    "proposed_default": "sqlite",
                },
                {"id": "q-2", "question": "Any seed data?", "why": "It shapes the checks"},
            ],
            **need,
        },
    }


def plan_view(token="r1:abc", *, complete=True):
    plan = {
        "revision": 1,
        "token": token,
        "acceptance_criteria": [
            {"id": "c1", "criterion": "greets by name", "verification_method": "unittest", "human_review": False},
            {"id": "c2", "criterion": "no stderr output", "verification_method": "human review", "human_review": True},
        ]
        if complete
        else [{"id": "c1"}],
        "constraints": ["keep the public API"],
        "permission_boundaries": ["read-only outside src/"],
    }
    return {
        "status": "AWAITING_GOAL_APPROVAL",
        "needs": {"kind": "approve_plan", "token": token},
        "displayed_plan": plan,
    }


class RelayTests(unittest.TestCase):
    def test_questions_relay_once_with_ids_and_options(self):
        record = {"gate": {}}
        first = relay.relay(answer_view(), record)
        self.assertEqual(1, len(first.messages))
        self.assertIn("[q-1]", first.messages[0])
        self.assertIn("sqlite, postgres", first.messages[0])
        self.assertIn("ID: your answer", first.messages[0])
        self.assertEqual({"key": "answer:req-1::q-1,q-2", "request_id": "req-1"}, first.gate)
        again = relay.relay(answer_view(), {"gate": first.gate})
        self.assertEqual([], again.messages)  # an unchanged stop is relayed once

    def test_questions_without_an_issued_request_ask_for_an_advance_not_a_person(self):
        view = answer_view(resolver_token=None)
        result = relay.relay(view, {"gate": {}})
        self.assertEqual([], result.messages)
        self.assertTrue(result.publish_questions)

    def test_a_complete_plan_is_relayed_with_its_token_and_terms(self):
        result = relay.relay(plan_view(), {"gate": {}})
        self.assertEqual(1, len(result.messages))
        self.assertIn("greets by name", result.messages[0])
        self.assertIn("keep the public API", result.messages[0])
        self.assertEqual({"key": "approve_plan:r1:abc", "token": "r1:abc"}, result.gate)

    def test_an_incomplete_plan_is_never_offered_for_approval(self):
        result = relay.relay(plan_view(complete=False), {"gate": {}})
        self.assertIn("does not carry the complete plan", result.messages[0])
        self.assertIn("cannot show", result.messages[0])
        self.assertIsNone(result.gate.get("token"))
        self.assertFalse(result.gate.get("showable"))

    def test_a_pause_relays_its_reason_and_the_resume_word(self):
        view = {"status": "PAUSED_TIME_LIMIT", "needs": {"kind": "resume", "reason": "the run used its active time"}}
        result = relay.relay(view, {"gate": {}})
        self.assertIn("the run used its active time", result.messages[0])
        self.assertIn("`resume`", result.messages[0])
        self.assertEqual("resume", relay.interpret({"gate": result.gate}, view, "resume").kind)

    def test_completion_relays_once(self):
        view = {"status": "COMPLETE", "done": True, "evidence": {"outcome": "delivered"}}
        result = relay.relay(view, {"gate": {}})
        self.assertIn("Task complete", result.messages[0])
        self.assertIn("delivered", result.messages[0])
        self.assertEqual([], relay.relay(view, {"gate": result.gate}).messages)

    def test_a_route_stop_names_the_role_and_candidates(self):
        view = {
            "status": "PAUSED_QUOTA",
            "needs": {
                "kind": "answer",
                "resolver_token": "t2",
                "route": {
                    "question_id": "route-builder",
                    "role": "builder",
                    "cause": "quota",
                    "stopped_model": "glm-5.3",
                    "candidates": ["glm-5.4", "gpt-6-terra"],
                },
            },
        }
        result = relay.relay(view, {"gate": {}})
        self.assertIn("builder", result.messages[0])
        self.assertIn("`glm-5.4`", result.messages[0])
        self.assertEqual("model", relay.interpret({"gate": result.gate}, view, "model glm-5.4").kind)
        self.assertEqual("none", relay.interpret({"gate": result.gate}, view, "anything").kind)


class InterpretTests(unittest.TestCase):
    def test_a_single_question_takes_the_whole_reply(self):
        view = answer_view()
        del view["needs"]["questions"][1]
        action = relay.interpret({"gate": {}}, view, "sqlite")
        self.assertEqual({"q-1": "sqlite"}, action.answers)

    def test_an_option_question_refuses_anything_but_its_options(self):
        view = answer_view()
        del view["needs"]["questions"][1]
        action = relay.interpret({"gate": {}}, view, "mysql")
        self.assertEqual("none", action.kind)
        self.assertIn("sqlite, postgres", action.text)

    def test_multiple_questions_take_id_lines_and_name_missing_or_unknown_ids(self):
        view = answer_view()
        action = relay.interpret({"gate": {}}, view, "q-1: postgres\nq-2: none needed")
        self.assertEqual({"q-1": "postgres", "q-2": "none needed"}, action.answers)
        self.assertIn("Missing", relay.interpret({"gate": {}}, view, "q-1: sqlite").text)
        self.assertIn("No open question", relay.interpret({"gate": {}}, view, "q-9: x").text)

    def test_free_text_never_approves_a_plan(self):
        view = plan_view()
        gate = {"token": "r1:abc"}
        self.assertEqual("approve", relay.interpret({"gate": gate}, view, "approve").kind)
        self.assertEqual("approve", relay.interpret({"gate": gate}, view, "LGTM").kind)
        for text in ("yes please", "approved!", "looks good, ship it"):
            action = relay.interpret({"gate": gate}, view, text)
            self.assertEqual("feedback", action.kind, text)

    def test_an_approval_carries_the_token_the_person_was_shown(self):
        action = relay.interpret({"gate": {"token": "r1:abc"}}, plan_view(), "approve")
        self.assertEqual("r1:abc", action.shown_token)

    def test_a_finished_run_takes_any_text_as_a_follow_up(self):
        view = {"status": "COMPLETE", "done": True}
        action = relay.interpret({"gate": {"key": "done"}}, view, "also add a French greeting")
        self.assertEqual("follow_up", action.kind)
        self.assertEqual("also add a French greeting", action.text)

    def test_a_blocker_takes_text_as_information_for_the_resolver(self):
        view = answer_view(resolver_scope="blocker", reason="the API key was rejected")
        self.assertEqual("operational", relay.interpret({"gate": {}}, view, "the key lives in the vault").kind)

    def test_a_pause_without_an_edit_takes_only_the_resume_word(self):
        view = {"status": "PAUSED_NO_PROGRESS", "needs": {"kind": "resume", "reason": "stuck"}}
        self.assertEqual("resume", relay.interpret({"gate": {}}, view, "continue").kind)
        self.assertEqual("none", relay.interpret({"gate": {}}, view, "try harder").kind)

    def test_an_edit_required_pause_takes_text_as_feedback(self):
        view = {
            "status": "PAUSED_GOAL_UNAPPROVED",
            "needs": {"kind": "resume", "reason": "the plan conflicts", "edit_required": True},
        }
        self.assertEqual("feedback", relay.interpret({"gate": {}}, view, "drop the migration").kind)

    def test_a_review_gate_takes_the_approve_word_or_feedback(self):
        view = {
            "status": "AWAITING_REVIEW",
            "needs": {"kind": "review", "token": "rev1", "criteria": ["c1", "c2"]},
            "approved_contract": {
                "body": {
                    "acceptance_criteria": [
                        {"id": "c1", "criterion": "greets by name"},
                        {"id": "c2", "criterion": "no stderr output"},
                    ]
                }
            },
        }
        action = relay.interpret({"gate": {"token": "rev1"}}, view, "approve")
        self.assertEqual("review", action.kind)
        self.assertEqual("rev1", action.shown_token)
        self.assertEqual("feedback", relay.interpret({"gate": {"token": "rev1"}}, view, "c2 is wrong").kind)


def changeset_summary(set_token=None):
    other = {
        "role": "member",
        "target": "greet-history",
        "workspace": "/work/history",
        "run_dir": "/work/history/.autocode/runs/m1",
        "status": "AWAITING_GOAL_APPROVAL" if set_token else "WAITING_FOR_USER",
        "plan": plan_view("m1:def")["displayed_plan"] if set_token else None,
    }
    command = ["autocode", "changeset", "approve", "--run-dir", RUN, "--set-token", set_token] if set_token else None
    return {
        "changeset": "changeset-1",
        "runs": [{"role": "source", "target": "project", "workspace": WORKSPACE, "run_dir": RUN}, other],
        "approval": {
            "set_token": set_token,
            "waiting_on": [] if set_token else ["greet-history"],
            "next_command": command,
        },
        "verification": {
            "waiting_on": ["project", "greet-history"],
            "done": False,
            "merge_order": [],
            "next_command": None,
        },
    }


class ChangesetRelayTests(unittest.TestCase):
    def view(self):
        return {**plan_view(), "run_dir": RUN, "scope": {"linked": [{"target": "greet-history"}]}}

    def test_a_plan_gate_in_a_change_set_offers_the_set_approval_once_it_is_named(self):
        waiting = relay.relay(self.view(), {"gate": {}}, changeset_summary())
        self.assertEqual(2, len(waiting.messages))
        self.assertIn("waiting on: greet-history", waiting.messages[1])
        self.assertIsNone(waiting.gate["set_token"])
        ready = relay.relay(self.view(), {"gate": waiting.gate}, changeset_summary("set-1"))
        self.assertNotEqual(waiting.gate["key"], ready.gate["key"], "the set becoming approvable is a new gate")
        self.assertEqual(("r1:abc", "set-1"), (ready.gate["token"], ready.gate["set_token"]))
        self.assertIn("Reply `approve set`", ready.messages[1])
        self.assertIn("**greet-history's plan**", ready.messages[1])
        self.assertIn("greets by name", ready.messages[1], "the other run's plan is shown before it is approved")
        self.assertEqual([], relay.relay(self.view(), {"gate": ready.gate}, changeset_summary("set-1")).messages)

    def test_only_the_set_reply_takes_the_set_approval_with_the_shown_token(self):
        gate = {"token": "r1:abc", "set_token": "set-1"}
        action = relay.interpret({"gate": gate}, self.view(), "  Approve   SET ")
        self.assertEqual(("approve_set", "set-1"), (action.kind, action.shown_token))
        self.assertEqual("approve", relay.interpret({"gate": gate}, self.view(), "approve").kind)
        self.assertEqual("feedback", relay.interpret({"gate": gate}, self.view(), "approve the set please").kind)

    def test_a_set_that_cannot_be_read_for_one_poll_does_not_relay_the_plan_again(self):
        for summary in (changeset_summary(), changeset_summary("set-1")):
            shown = relay.relay(self.view(), {"gate": {}}, summary)
            unread = relay.relay(self.view(), {"gate": shown.gate}, None)
            self.assertEqual([], unread.messages, "a transient changeset status failure is not a new gate")
            # with no messages the bridge keeps the shown gate, set token included
            self.assertEqual([], relay.relay(self.view(), {"gate": shown.gate}, summary).messages)
        replanned = {**plan_view("r1:new"), "run_dir": RUN, "scope": self.view()["scope"]}
        self.assertEqual(1, len(relay.relay(replanned, {"gate": shown.gate}, None).messages), "a new plan is relayed")


if __name__ == "__main__":
    unittest.main()
