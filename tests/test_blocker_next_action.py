"""A blocker AutoResolver asks a person names its response, and a contract decision its path (#675).

A live run (feature-timesheet-by-project, 2026-10-07) proved a contradiction in the approved
criteria and asked the person to approve a correction. The status view said `answer`, the CLI
refused --answer, --resolver-response was accepted and then every resume held without naming
--edit-goal, the one path that applied the decision. The view logic is tested without processes;
the CLI path with real processes and the fake provider, no sleeps.
"""

import copy
import json
import shlex
import unittest

import autocode as runner
import autocode_client_actions as actions
import autocode_pause_authority as pause_authority
import autocode_pause_command as pause_command
import autocode_recovery_limits as recovery_limits
import autocode_resolver_human as human
import autocode_run_view as run_view

from . import test_resolver_human, test_subprocess

COMMAND = recovery_limits.RESPONSE_COMMAND
ADVICE = recovery_limits.CONTRACT_DECISION_ADVICE


def blocker_request():
    return {
        "kind": "blocker",
        "decision_needed": "Approve correcting the AC1 total-row string from ten spaces to twelve?",
        "impact": "Two acceptance tests fail; no implementation can satisfy both the string and the layout rule",
        "options": ["Approve the correction", "Keep the string and revise the layout requirement"],
        "discovered": "The approved AC1 string contradicts the approved unchanged renderer",
        "proposed_delta": "",
    }


def publish_blocker(state):
    """Publish a Resolver diagnosis that asks a person, as the live run did."""
    human.queue(
        state,
        "blocker",
        {"stage": "astra_resolve"},
        request=blocker_request(),
        evidence={"diagnosis": "Proven contract contradiction", "output": "iterations/001/resolver-01.json"},
    )
    state["stages"].append(
        {"stage": "astra_resolve", "output": "iterations/001/resolver-01.json", "exit_code": 0, "changed_files": []}
    )
    assert human.evaluate(state) == "escalate"
    return human.current(state)


class BlockerNeedTests(unittest.TestCase):
    setUp = test_resolver_human.ResolverHumanTests.setUp
    contract = test_resolver_human.ResolverHumanTests.contract
    queue_question = test_resolver_human.ResolverHumanTests.queue_question
    request = test_resolver_human.ResolverHumanTests.request
    publish_operational = test_resolver_human.ResolverHumanTests.publish_operational

    def needs(self):
        return run_view.view(self.state)["needs"]

    def test_a_blocker_names_the_response_as_its_action(self):
        self.state["status"] = "PAUSED_RESOLVER"
        publish_blocker(self.state)
        need = self.needs()
        self.assertEqual(("answer", "blocker", COMMAND), (need["kind"], need["resolver_scope"], need["action"]))
        self.assertTrue(need["resolver_request_id"] and need["resolver_token"])

    def test_an_operational_request_names_the_same_response(self):
        self.contract()
        self.publish_operational()
        need = self.needs()
        self.assertEqual(
            ("answer", "operational_exhaustion", COMMAND), (need["kind"], need["resolver_scope"], need["action"])
        )

    def publish_no_progress(self, *, held=True):
        """A recovery novelty hold published as an operational request, as after an abandoned repair (#1012)."""
        self.contract()
        self.state.update(status="PAUSED_NO_PROGRESS", next_stage="astra_resolve")
        if held:
            self.state["resolution_request"] = {"novelty_hold": {"stage": "astra_resolve", "incident_id": "incident"}}
        self.state["resolver"] = {
            "operational_diagnostics": ["op-receipt"],
            "operational_receipts": {
                "op-receipt": {
                    "stage": "resolver",
                    "runner_owned": True,
                    "decision": {"action": "hold"},
                    "receipt": {"evaluation_binding": human._binding(self.state)},
                }
            },
        }
        human.queue(
            self.state,
            "operational_exhaustion",
            {"stage": "astra_resolve", "pause_status": "PAUSED_NO_PROGRESS"},
            request=self.request(),
            evidence={"resolver_receipt_id": "op-receipt"},
        )
        self.assertEqual("escalate", human.evaluate(self.state))

    def test_a_novelty_hold_also_names_its_one_scoped_retry(self):
        self.publish_no_progress()
        need = self.needs()
        self.assertEqual(("PAUSED_NO_PROGRESS", COMMAND), (need["pause_origin"], need["action"]))
        self.assertEqual("--resume-paused --retry-failed-stage", need["retry_action"])

    def test_no_scoped_retry_without_a_hold_or_with_an_unsettled_attempt(self):
        self.publish_no_progress(held=False)
        self.assertNotIn("retry_action", self.needs())
        self.publish_no_progress()
        self.state["uncertain_artifacts"] = ["iterations/002/builder-01.json"]
        self.assertNotIn("retry_action", self.needs())

    def card_actions(self):
        return {row["id"]: row for row in run_view.view(self.state)["recovery"]["actions"]}

    def test_the_recovery_card_offers_the_held_steps_scoped_retry(self):
        # The card runs the same flags needs.retry_action names, so a client never maps kinds (#730).
        self.publish_no_progress()
        actions = self.card_actions()
        self.assertIn("decision", actions)
        retry = actions["retry_failed_stage"]
        self.assertEqual(self.needs()["retry_action"], " ".join(retry["args"]))
        self.assertEqual(["--resume-paused", "--retry-failed-stage"], retry["args"])

    def test_the_card_offers_no_scoped_retry_without_a_hold_or_with_an_unsettled_attempt(self):
        self.publish_no_progress(held=False)
        self.assertNotIn("retry_failed_stage", self.card_actions())
        self.publish_no_progress()
        self.state["uncertain_artifacts"] = ["iterations/002/builder-01.json"]
        self.assertNotIn("retry_failed_stage", self.card_actions())

    def hold_before_publication(self):
        """The #1770 live hold: the novelty hold is queued, but no request is published yet."""
        self.contract()
        self.state.update(status="PAUSED_NO_PROGRESS", next_stage="astra_resolve")
        self.state["resolution_request"] = {"novelty_hold": {"stage": "astra_resolve", "incident_id": "incident"}}
        human.queue(
            self.state,
            "operational_exhaustion",
            {"stage": "astra_resolve", "pause_status": "PAUSED_NO_PROGRESS"},
            request=self.request(),
            evidence={"resolver_receipt_id": "op-receipt"},
        )
        self.assertEqual("RESOLVER_PENDING", self.state["status"])
        self.assertIsNone(human.current(self.state))

    def test_an_unpublished_novelty_hold_names_the_scoped_retry_not_a_plain_resume(self):
        # The view used to offer --resume-paused here, which only re-held the run (#1770).
        self.hold_before_publication()
        view = run_view.view(self.state)
        need = view["needs"]
        self.assertEqual("resume", need["kind"])
        self.assertEqual(recovery_limits.SCOPED_RETRY, need["action"])
        self.assertEqual(recovery_limits.SCOPED_RETRY, need["retry_action"])
        self.assertIn("--retry-failed-stage", view["next_command"])
        retry = {row["id"]: row for row in view["recovery"]["actions"]}["retry_failed_stage"]
        self.assertEqual(["--resume-paused", "--retry-failed-stage"], retry["args"])
        self.assertIn("one more attempt", retry["effect"])

    def test_an_unpublished_hold_that_cannot_take_the_retry_says_what_comes_first(self):
        # Each refusal is the CLI's own reason (autocode_resolver_recovery.authorize_retry).
        cases = {
            "uncertain attempt": (
                lambda: self.state.update(uncertain_artifacts=["iterations/002/resolver-02.json"]),
                recovery_limits.UNSETTLED_REFUSAL,
            ),
            "different pending decision": (
                lambda: self.state[human.PRIVATE].update(scope="blocker"),
                "A different pending decision cannot grant a recovery retry",
            ),
        }
        for name, (change, reason) in cases.items():
            with self.subTest(name):
                self.setUp()  # a fresh run per case
                self.hold_before_publication()
                change()
                need = self.needs()
                self.assertNotIn("retry_action", need)
                self.assertNotEqual(recovery_limits.SCOPED_RETRY, need.get("action"))
                self.assertEqual(reason, need["retry_unavailable"])
                self.assertNotIn("retry_failed_stage", self.card_actions())
        self.hold_before_publication()
        self.state["resolution_request"] = {}  # no novelty hold: nothing to retry or explain
        need = self.needs()
        self.assertNotIn("retry_action", need)
        self.assertNotIn("retry_unavailable", need)

    def test_the_view_and_the_cli_share_one_admission_rule(self):
        # A published request of another kind refuses in both, with the same reason.
        self.publish_no_progress()
        public = human.current(self.state)
        self.assertIsNone(recovery_limits.scoped_retry_refusal(self.state, public))
        public = {**public, "scope": "blocker"}
        self.assertEqual(
            "A different human decision cannot grant a recovery retry",
            recovery_limits.scoped_retry_refusal(self.state, public),
        )

    def test_a_requirements_question_keeps_its_answer_path(self):
        self.queue_question()
        self.assertEqual("escalate", human.evaluate(self.state))
        need = self.needs()
        self.assertEqual(("answer", "clarification"), (need["kind"], need["resolver_scope"]))
        self.assertNotIn("action", need)

    def test_information_on_a_blocker_names_the_contract_path_and_holds(self):
        self.state["status"] = "PAUSED_RESOLVER"
        public = publish_blocker(self.state)
        contract = copy.deepcopy(self.state["goal_contract"])
        human.respond_operational(
            self.state,
            public["request_id"],
            public["request_token"],
            "provide_information",
            "Approved: correct AC1 to twelve spaces",
        )
        self.assertEqual("PAUSED_RESOLVER", self.state["status"])
        self.assertTrue(self.state["stop_reason"].startswith(human.RESPONSE_RECEIVED))
        self.assertIn(ADVICE, self.state["stop_reason"])
        self.assertIn("--edit-goal body.json", self.state["stop_reason"])
        self.assertEqual(contract, self.state["goal_contract"], "information never edits the contract")
        need = self.needs()
        self.assertEqual("resume", need["kind"])
        self.assertIn("--edit-goal body.json", need["reason"])
        human.review_operational_response(self.state)
        self.assertEqual("blocker", human.held_response_scope(self.state))

    def test_leaving_a_blocker_paused_asks_for_nothing(self):
        self.state["status"] = "PAUSED_RESOLVER"
        public = publish_blocker(self.state)
        human.respond_operational(self.state, public["request_id"], public["request_token"], "leave_paused")
        self.assertEqual(human.RESPONSE_RECEIVED, self.state["stop_reason"])

    def test_operational_information_is_not_given_the_contract_path(self):
        # An exhausted operational recovery is re-evaluated (#486); a contract edit is not its path.
        self.contract()
        public = self.publish_operational()
        human.respond_operational(
            self.state,
            public["request_id"],
            public["request_token"],
            "provide_information",
            "The provider incident has been investigated",
        )
        self.assertEqual(human.RESPONSE_RECEIVED, self.state["stop_reason"])
        human.review_operational_response(self.state)
        self.assertEqual("operational_exhaustion", human.held_response_scope(self.state))
        self.assertEqual("", human.contract_decision_advice("operational_exhaustion"))

    def test_no_held_response_has_no_scope(self):
        self.assertIsNone(human.held_response_scope(self.state))
        self.assertIsNone(human.held_response_scope({"resolver": {"human_response_frontier": {"request_id": "x"}}}))

    def answer_blocker_with_information(self):
        """A blocker answered with information at an approved contract, its response reviewed."""
        self.state["status"] = "PAUSED_RESOLVER"
        self.contract()
        # The decision revises an approved contract, as the live member run's did.
        self.state["goal_contract"].update(approval_status="approved", approval_event={"kind": "goal_approval"})
        public = publish_blocker(self.state)
        human.respond_operational(
            self.state,
            public["request_id"],
            public["request_token"],
            "provide_information",
            "Finish this member's local gates; keep joint proof as the whole-set gate.",
        )
        human.review_operational_response(self.state)
        return public

    def view(self):
        return run_view.view(
            self.state, workspace=self.state["workspace"], run_dir=self.state["run_dir"]
        )

    def test_an_answered_blocker_offers_its_plan_feedback_and_consistent_edit_path(self):
        # The live dead end (#2434): after the informational reply the view named only
        # --resume-paused (chat.input null) and the CLI refused the advised --edit-goal.
        self.answer_blocker_with_information()
        view = self.view()
        self.assertEqual("resume", view["needs"]["kind"])
        self.assertIn("plan feedback (--feedback)", view["needs"]["reason"])
        offered = view["chat"]["input"]
        self.assertEqual("feedback", offered["kind"])
        self.assertEqual(["--feedback", "MESSAGE"], shlex.split(offered["next_command"])[-2:])
        self.assertIsNone(pause_authority.feedback_refusal(self.state))
        self.assertIsNone(pause_authority.correction_refusal(self.state, "An edited goal"))

    def test_information_stays_informational_until_the_person_sends_the_correction(self):
        self.state["status"] = "PAUSED_RESOLVER"
        self.contract()
        self.state["goal_contract"].update(approval_status="approved", approval_event={"kind": "goal_approval"})
        before = copy.deepcopy(self.state["goal_contract"])
        public = publish_blocker(self.state)
        human.respond_operational(
            self.state,
            public["request_id"],
            public["request_token"],
            "provide_information",
            "Finish this member's local gates; keep joint proof as the whole-set gate.",
        )
        human.review_operational_response(self.state)
        self.assertEqual(before, self.state["goal_contract"], "answering never edits the contract")
        self.assertEqual("PAUSED_RESOLVER", self.state["status"])
        # The offer disappears once the decision was taken: the draft awaits its own approval.
        self.state["goal_contract"].update(approval_status="draft", approval_event=None)
        self.assertIsNone(self.view()["chat"]["input"])
        self.assertIsNotNone(pause_authority.feedback_refusal(self.state))


class AnsweredBlockerChatTests(unittest.TestCase):
    """The chat's recovery at the answered blocker: type the correction, then approve the revised
    plan it produces — through the actions the views name, never a raw goal file (#2434)."""

    setUp = test_resolver_human.ResolverHumanTests.setUp
    contract = test_resolver_human.ResolverHumanTests.contract

    def answered_view(self):
        state = self.state
        state["status"] = "PAUSED_RESOLVER"
        self.contract()
        state["goal_contract"].update(approval_status="approved", approval_event={"kind": "goal_approval"})
        public = publish_blocker(state)
        human.respond_operational(
            state, public["request_id"], public["request_token"], "provide_information", "keep joint proof whole-set"
        )
        human.review_operational_response(state)
        return run_view.view(state, workspace=state["workspace"], run_dir=state["run_dir"])

    def revised_plan_view(self):
        """The fresh draft's approval view, as the core would publish it after re-planning: the
        member's flow is local, the coordinator-owned joint proof stays a whole-set obligation
        outside the member's milestones, and the approval screen shows both (#2434)."""
        plan = {
            "revision": 2,
            "hash": "h4",
            "token": "r2:h4",
            "acceptance_criteria": [
                {
                    "id": "AC5",
                    "criterion": "Given every member repository composed by the coordinator, checkout shows "
                    "'$110.00' in the coordinator's joint check; local tax tests never discharge it",
                    "verification_method": "test: joint_integration_by_coordinator",
                    "human_review": False,
                }
            ],
            "constraints": [
                "Preserve every original test file byte-for-byte",
                "The coordinator's joint integration check owns the whole-set proof of AC5",
            ],
            "permission_boundaries": ["This repository only"],
            "milestones": [
                {"id": "M1", "objective": "Finish the member's local gates", "acceptance_criteria": ["AC1", "AC6"]}
            ],
            "end_to_end_flow": [
                "Finish this member's local gates",
                "The coordinator's separate joint check discharges AC5 over every member's actual code",
            ],
        }
        command = pause_command.command(
            {"kind": "approve_plan", "token": "r2:h4"}, run_dir=self.state["run_dir"], workspace=self.state["workspace"]
        )
        return {
            "status": "AWAITING_GOAL_APPROVAL",
            "workspace": self.state["workspace"],
            "run_dir": self.state["run_dir"],
            "needs": {"kind": "approve_plan", "token": "r2:h4"},
            "next_command": command,
            "displayed_plan": plan,
            "chat": {"context": "fresh", "input": None, "messages": []},
        }

    def test_the_typed_correction_opens_fresh_approval_of_the_revised_plan(self):
        """Client dispatch and render only: the canned second view stands in for the core's
        re-planning (which tests.test_goals and the CLI module drive through the real pipeline)."""
        from .test_chat_client import DONE, ScriptedChat

        correction = (
            "Keep this member's flow local: finish its own gates and retain the coordinator-owned "
            "joint proof as the whole-set gate with the original tests unchanged."
        )
        client = ScriptedChat(self.answered_view(), self.revised_plan_view(), DONE, replies=[correction, "y"])
        self.assertEqual(0, client.converse())
        # The approve action keeps its token in the private env channel, never argv.
        self.assertEqual([["--feedback", correction], ["--approve-goal", "-"]], client.taken)
        # The approval screen shows the completion order, not only criteria summaries (#2434).
        self.assertIn("Milestone M1 (proves AC1, AC6)", client.text())
        self.assertIn("Steps in order", client.text())
        self.assertIn("The coordinator's separate joint check discharges AC5", client.text())

    def test_an_offer_augmented_beyond_what_the_view_named_is_refused(self):
        from .test_chat_client import ScriptedChat

        view = self.answered_view()
        # A command carrying more than the view named (here an extra resume) is not the offer.
        view["chat"]["input"]["next_command"] = view["chat"]["input"]["next_command"].replace(
            "--feedback", "--resume-paused --feedback"
        )
        client = ScriptedChat(view, replies=["whatever"])
        with self.assertRaises(actions.ClientGap):
            client.send("whatever", view["chat"]["context"])

    def test_an_offer_from_a_conversation_that_changed_is_refused(self):
        from .test_chat_client import ScriptedChat

        view = self.answered_view()
        client = ScriptedChat(view, replies=["whatever"])
        with self.assertRaises(actions.ClientGap) as caught:
            client.send("whatever", "a-context-the-person-no-longer-sees")
        self.assertIn("conversation changed", str(caught.exception))
        self.assertEqual([], client.taken)


class BlockerNextActionCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def blocker_checkpoint(self):
        """A built run whose Resolver diagnosis asks a person, saved as the CLI would find it."""
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"] = "astra_review"
        self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
        run, paused = self.saved()
        self.launch(["--run-dir", str(run), "--abandon-stage", runner.attempt_id(paused["active_stage"])], 0)
        self.run, stopped = self.saved()
        stopped.update(
            status="PAUSED_RESOLVER",
            phase="PAUSED_OR_BLOCKED",
            stop_reason="AutoResolver could not resolve the contradiction",
        )
        publish_blocker(stopped)
        runner.write_json(self.run / "state.json", stopped)
        return self.status()

    def status(self):
        return json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]

    def test_the_view_the_refusal_and_the_hold_all_name_the_way_forward(self):
        view = self.blocker_checkpoint()
        need = view["needs"]
        self.assertEqual(("answer", "blocker", COMMAND), (need["kind"], need["resolver_scope"], need["action"]))
        question = need["questions"][0]["id"]
        state_file = self.run / "state.json"
        before = state_file.read_bytes()
        result = self.launch(
            [
                "--run-dir",
                str(self.run),
                "--resolver-token",
                need["resolver_token"],
                "--answer",
                question + "=Approve the correction",
            ],
            2,
        )
        self.assertIn("Use --resolver-response", result.stderr)
        self.assertIn(COMMAND, result.stderr)
        self.assertEqual(before, state_file.read_bytes(), "a refused answer changes nothing")
        self.launch(
            [
                "--run-dir",
                str(self.run),
                "--resolver-request",
                need["resolver_request_id"],
                "--resolver-token",
                need["resolver_token"],
                "--resolver-response",
                "provide_information",
                "--resolver-message",
                "Approved: correct AC1 to twelve spaces",
                "--no-chat",
            ],
            0,
        )
        held = self.status()
        self.assertEqual(("PAUSED_RESOLVER", "resume"), (held["status"], held["needs"]["kind"]))
        self.assertIn("--edit-goal body.json", held["needs"]["reason"])
        probe = self.root / "launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        result = self.launch(["--run-dir", str(self.run), "--resume-paused", "--no-chat"], 2)
        self.assertIn("retained the human guidance", result.stdout)
        self.assertIn("--edit-goal body.json", result.stdout)
        self.assertIn("--approve-goal", result.stdout)
        self.assertFalse(probe.exists(), "information launches no provider")


    def answer_the_blocker_with_information(self, message):
        """Answer the checkpoint's blocker with information through the public CLI, exactly as
        the terminal chat's resolver-reply offer does."""
        need = self.blocker_checkpoint()["needs"]
        self.launch(
            [
                "--run-dir",
                str(self.run),
                "--resolver-request",
                need["resolver_request_id"],
                "--resolver-token",
                need["resolver_token"],
                "--resolver-response",
                "provide_information",
                "--resolver-message",
                message,
                "--no-chat",
            ],
            0,
        )

    def test_the_answered_blocker_takes_its_decision_through_named_plan_feedback(self):
        # The live dead end (#2434): after the informational reply the view named only
        # --resume-paused and the CLI refused every correction. The answered blocker's own
        # authority is now the person's explicit plan feedback: an unapproved revised plan that
        # the old approval cannot release, with no provider launched.
        self.answer_the_blocker_with_information(
            "Finish this member's local gates; keep joint proof as the whole-set gate"
        )
        held = self.status()
        self.assertEqual(("PAUSED_RESOLVER", "resume"), (held["status"], held["needs"]["kind"]))
        self.assertIn("--edit-goal body.json", held["needs"]["reason"])
        self.assertIn("plan feedback (--feedback)", held["needs"]["reason"])
        offered = (held.get("chat") or {}).get("input")
        self.assertEqual("feedback", offered["kind"])
        self.assertEqual(["--feedback", "MESSAGE"], shlex.split(offered["next_command"])[-2:])
        old_token = held["approved_contract"]["token"]
        probe = self.root / "launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        message = "Keep the member flow local; retain the mandatory actual-code joint proof as the whole-set gate."
        self.launch(["--run-dir", str(self.run), "--feedback", message, "--no-chat"], 0)
        self.assertFalse(probe.exists(), "the correction launches no provider")
        revised = self.status()
        self.assertIsNone(revised.get("approved_contract"), "the revised plan awaits fresh approval")
        self.assertIsNone((revised.get("chat") or {}).get("input"), "the taken decision closes the offer")
        self.launch(["--run-dir", str(self.run), "--approve-goal", old_token, "--no-chat"], 2)
        self.launch(["--run-dir", str(self.run), "--feedback", "one correction per decision", "--no-chat"], 2)

    def test_the_advised_edit_goal_is_accepted_at_the_answered_blocker(self):
        # The stop reason names --edit-goal; the CLI used to refuse it here (#2434). It stays
        # the person-only path it describes, consistent with the same pause authority.
        self.answer_the_blocker_with_information("Revise the contract")
        stopped = self.saved()[1]
        goal_file = self.root / "body.json"
        goal_file.write_text(json.dumps(stopped["goal_contract"]["body"]))
        self.launch(["--run-dir", str(self.run), "--edit-goal", str(goal_file), "--no-chat"], 0)
        revised = self.status()
        self.assertIsNone(revised.get("approved_contract"))
        self.assertGreater(revised["displayed_plan"]["revision"], stopped["goal_contract"]["revision"])


if __name__ == "__main__":
    unittest.main()
