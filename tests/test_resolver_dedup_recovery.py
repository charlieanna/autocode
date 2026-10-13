"""Duplicate reviews retain Resolver questions without buying another diagnosis."""

import copy
import unittest
from unittest.mock import patch

import autocode as runner
import autocode_resolver_human as human
import autocode_support as support
import autocode_util as util
import autopilot
from goal_fixtures import envelope

from tests import test_resolver_producers as producers


class ResolverDedupRecoveryTests(unittest.TestCase):
    def setUp(self):
        # Compose the established producer fixture without inheriting its tests.
        self.fixture = producers.ResolverProducerTests(methodName="runTest")
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.ready()
        self.fixture.state["settings"]["regression"] = {"regression_command": "old regression"}

    def diagnose(self):
        self.fixture.apply("astra_review", self.fixture.decision())
        diagnosis = {**self.fixture.decision(), "diagnosis": "The regression command needs corrective information"}
        record = self.fixture.apply("astra_resolve", diagnosis)
        self.assertEqual("escalate", human.evaluate(self.fixture.state))
        request = human.current(self.fixture.state)
        self.assertIsNotNone(request)
        return record, request

    def diagnosis_count(self):
        return sum(row.get("stage") == "astra_resolve" for row in self.fixture.state["stages"])

    def review_decision(self, status="BLOCKED"):
        value = self.fixture.decision(status)
        if status == "REWORK":
            value["user_request"] = envelope(self.fixture.state)["user_request"]
        return value

    def provide_information(self, request):
        event = human.respond_operational(
            self.fixture.state,
            request["request_id"],
            request["request_token"],
            "provide_information",
            "Use the existing approved regression command; keep the source unchanged",
        )
        resolution = human.review_operational_response(self.fixture.state)
        self.assertEqual("hold", resolution["action"])
        self.assertEqual(event, resolution["response"])
        self.assertIsNone(human.current(self.fixture.state))
        return event

    def apply_at_source(self, stage, value, snapshot):
        record = self.fixture.record(stage, value)
        record["source_revision"] = snapshot["revision"]
        autopilot.apply_result(runner, self.fixture.state, stage, value, record, self.fixture.root, self.fixture.root)
        return record

    def test_three_duplicate_reviews_preserve_the_same_answerable_question_and_token(self):
        _, pending = self.diagnose()
        ledger = copy.deepcopy(self.fixture.state["resolver"]["human_escalations"])
        for status in ("BLOCKED", "REWORK"):
            for repetition in range(3):
                with self.subTest(status=status, repetition=repetition):
                    reviewed = self.fixture.apply("astra_review", self.review_decision(status))
                    self.assertFalse(reviewed.get("rejected"))
                    self.assertEqual(reviewed["output"], self.fixture.state["stages"][-1]["output"])
                    self.assertIsNone(human.current(self.fixture.state))
                    self.assertEqual("escalate", human.evaluate(self.fixture.state))
                    self.assertEqual(pending, human.current(self.fixture.state))
                    self.assertEqual(ledger, self.fixture.state["resolver"]["human_escalations"])
                    self.assertEqual(1, self.diagnosis_count())
                    self.assertEqual("astra_review", self.fixture.state["next_stage"])

    def test_corrective_information_then_duplicate_reviews_do_not_rearm_the_answered_request(self):
        _, pending = self.diagnose()
        event = self.provide_information(pending)
        for status in ("BLOCKED", "REWORK"):
            for repetition in range(3):
                with self.subTest(status=status, repetition=repetition):
                    reviewed = self.fixture.apply("astra_review", self.review_decision(status))
                    self.assertFalse(reviewed.get("rejected"))
                    self.assertEqual(reviewed["output"], self.fixture.state["stages"][-1]["output"])
                    self.assertEqual("defer", human.evaluate(self.fixture.state))
                    self.assertIsNone(human.current(self.fixture.state))
                    self.assertEqual("RESOLVER_PENDING", human.projection(self.fixture.state)["status"])
                    self.assertEqual("astra_review", self.fixture.state["next_stage"])
                    self.assertEqual(1, self.diagnosis_count())
                    self.assertEqual([event], self.fixture.state["resolver"]["human_responses"])
                    self.assertEqual(
                        "consumed", self.fixture.state["resolver"]["human_escalations"][pending["request_id"]]["status"]
                    )

    def test_duplicate_rework_cannot_change_approved_criteria_or_replace_the_pending_question(self):
        _, pending = self.diagnose()
        value = self.review_decision("REWORK")
        value["acceptance_criteria"][0]["criterion"] = "A different unapproved requirement"
        before = copy.deepcopy(self.fixture.state)
        record = self.fixture.record("astra_review", value)
        with self.assertRaisesRegex(support.Paused, "change approved acceptance criteria"):
            autopilot.apply_result(
                runner, self.fixture.state, "astra_review", value, record, self.fixture.root, self.fixture.root
            )
        self.assertEqual(before, self.fixture.state)
        self.assertNotIn(record["output"], [row["output"] for row in self.fixture.state["stages"]])
        self.assertEqual(pending, human.current(self.fixture.state))
        self.assertEqual(1, self.diagnosis_count())

    def test_actionable_settings_or_source_change_admits_one_fresh_diagnosis(self):
        original = copy.deepcopy(self.fixture.state)
        for change in ("settings", "source"):
            with self.subTest(change=change):
                self.fixture.state = copy.deepcopy(original)
                self.diagnose()
                if change == "settings":
                    self.fixture.state["settings"]["regression"]["regression_command"] = "new regression"
                    snapshot = support.snapshot(self.fixture.root)
                else:
                    source = self.fixture.root / "greet.py"
                    source.write_text('def greet(name):\n    return f"Hello, {name}!"\n')
                    snapshot = {"head": "fixture-head", "files": {"greet.py": util.file_hash(source)}}
                    snapshot["revision"] = util.digest(snapshot)
                with (
                    patch.object(support, "snapshot", return_value=snapshot),
                    patch.object(util, "snapshot", return_value=snapshot),
                ):
                    reviewed = self.apply_at_source("astra_review", self.fixture.decision(), snapshot)
                    self.assertFalse(reviewed.get("rejected"))
                    self.assertEqual("astra_resolve", self.fixture.state["next_stage"])
                    fresh = {
                        **self.fixture.decision(),
                        "diagnosis": "The changed setting or source was investigated once",
                    }
                    self.apply_at_source("astra_resolve", fresh, snapshot)
                    self.assertEqual("escalate", human.evaluate(self.fixture.state))
                    pending = human.current(self.fixture.state)
                    self.assertIsNotNone(pending)
                    self.assertEqual(2, self.diagnosis_count())
                    self.apply_at_source("astra_review", self.fixture.decision(), snapshot)
                    self.assertEqual("escalate", human.evaluate(self.fixture.state))
                    self.assertEqual(pending, human.current(self.fixture.state))
                    self.assertEqual(2, self.diagnosis_count())
                    self.assertEqual("astra_review", self.fixture.state["next_stage"])

    def test_consumed_or_forged_public_marker_cannot_authorize_a_pending_question(self):
        _, pending = self.diagnose()
        self.provide_information(pending)
        answered = copy.deepcopy(self.fixture.state)
        for marker in (pending, {**pending, "request_id": "forged-request", "request_token": "forged-token"}):
            with self.subTest(marker=marker["request_id"]):
                self.fixture.state = copy.deepcopy(answered)
                # Fault injection replays a marker, not a user response or request receipt.
                self.fixture.state[human.PUBLIC] = copy.deepcopy(marker)
                self.assertIsNone(human.current(self.fixture.state))
                reviewed = self.fixture.apply("astra_review", self.fixture.decision())
                self.assertFalse(reviewed.get("rejected"))
                self.assertEqual("defer", human.evaluate(self.fixture.state))
                self.assertIsNone(human.current(self.fixture.state))
                self.assertFalse(human.projection(self.fixture.state)["human_request_authorized"])
                self.assertEqual(1, self.diagnosis_count())
                self.assertEqual(
                    answered["resolver"]["human_escalations"], self.fixture.state["resolver"]["human_escalations"]
                )

    def test_pending_marker_with_a_forged_token_or_receipt_does_not_publish_controls(self):
        self.diagnose()
        original = copy.deepcopy(self.fixture.state)
        for field in ("request_token", "receipt_hash"):
            with self.subTest(field=field):
                self.fixture.state = copy.deepcopy(original)
                self.fixture.state[human.PUBLIC][field] = "forged"
                self.assertIsNone(human.current(self.fixture.state))
                reviewed = self.fixture.apply("astra_review", self.fixture.decision())
                self.assertFalse(reviewed.get("rejected"))
                self.assertEqual("defer", human.evaluate(self.fixture.state))
                self.assertIsNone(human.current(self.fixture.state))
                self.assertFalse(human.projection(self.fixture.state)["human_request_authorized"])
                self.assertEqual(1, self.diagnosis_count())
                self.assertEqual(
                    original["resolver"]["human_escalations"], self.fixture.state["resolver"]["human_escalations"]
                )

    def test_consumed_private_proposal_replay_cannot_republish_the_answered_request(self):
        _, pending = self.diagnose()
        proposal = copy.deepcopy(
            self.fixture.state["resolver"]["human_escalations"][pending["request_id"]]["identity"]["proposal"]
        )
        self.provide_information(pending)
        self.fixture.state[human.PRIVATE] = proposal
        reviewed = self.fixture.apply("astra_review", self.fixture.decision())
        self.assertFalse(reviewed.get("rejected"))
        self.assertEqual("consumed", human.evaluate(self.fixture.state))
        self.assertIsNone(human.current(self.fixture.state))
        self.assertFalse(human.projection(self.fixture.state)["human_request_authorized"])
        self.assertEqual(1, self.diagnosis_count())

    def test_forged_private_marker_fails_closed_without_creating_a_question(self):
        _, pending = self.diagnose()
        self.provide_information(pending)
        self.fixture.state[human.PRIVATE] = {"issuer": "resolver"}
        before = copy.deepcopy(self.fixture.state)
        with self.assertRaisesRegex(ValueError, "invalid proposal"):
            self.fixture.apply("astra_review", self.fixture.decision())
        self.assertEqual(before, self.fixture.state)
        self.assertIsNone(human.current(self.fixture.state))
        self.assertEqual(1, self.diagnosis_count())


if __name__ == "__main__":
    unittest.main()
