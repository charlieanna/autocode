"""Resolver recommendation schema, display, and transactional response surfaces."""

import copy
import io
import json
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_action_resolver as action
import autocode_goal_lifecycle as lifecycle
import autocode_resolver_human as human
import autocode_resolver_recommendation as recommendation
import autocode_role_schema as role_schema
import autocode_run_view as run_view
import autocode_util as util
from units import autoresolver


class ResolverRecommendationSurfaceTests(unittest.TestCase):
    def request(self, revision=None):
        return {
            "kind": "contradiction",
            "discovered": "The criterion's total-row spacing disagrees with the reference",
            "impact": "The approved plan needs a wording correction",
            "decision_needed": "Accept the corrected total row?",
            "options": ["Explain the difference", "Leave paused"],
            "proposed_delta": "Correct the spacing",
            "recommended_revision": revision,
        }

    def test_resolver_schema_is_optional_for_saved_reports_nullable_and_strict_on_wire(self):
        original = role_schema.role_schema(
            {
                "type": "object",
                "properties": {"status": {"type": "string", "enum": ["CONTINUE"]}},
                "required": ["status"],
                "additionalProperties": False,
            },
            "astra",
        )
        before = copy.deepcopy(original)
        source = SimpleNamespace(schema=original, metrics={}, prompt="Prelude\nCURRENT HANDOFF DATA\n{}")
        state = {
            "workspace": "/not-read",
            "settings": {"roles": {"astra": {"model": "fixture"}}},
            "resolution_request": {"source_revision": "source"},
        }
        with patch.object(autoresolver, "guard"), patch.object(autoresolver, "execution_request", return_value=source):
            request = autoresolver.prepare(state, "astra_resolve", Path("state.json"), Path("schemas"))
        saved = request.schema["properties"]["user_request"]
        self.assertEqual(before, original)
        self.assertNotIn("recommended_revision", role_schema.USER_REQUEST["properties"])
        self.assertNotIn("recommended_revision", saved["required"])
        legacy = self.request()
        del legacy["recommended_revision"]
        util.validate_schema(legacy, saved)
        util.validate_schema(self.request(), saved)
        wire = util.model_output_schema(request.schema)["properties"]["user_request"]
        self.assertIn("recommended_revision", wire["required"])
        util.validate_schema(self.request(), wire)
        nonnull = self.request({"acceptance_criteria": [{"id": "C1", "criterion": "Correct the total row"}]})
        util.validate_schema(nonnull, wire)
        with self.assertRaises(ValueError):
            util.validate_schema(legacy, wire)
        with self.assertRaises(ValueError):
            util.validate_schema(nonnull, role_schema.USER_REQUEST)

    def test_display_preserves_exact_rows_and_status_copies_the_recommendation(self):
        old = "Print exactly 'total" + " " * 10 + "42'."
        new = "Print exactly 'total" + " " * 12 + "42'."
        revision = {"acceptance_criteria": [{"id": "C1", "criterion": new}]}
        state = {
            "status": "RUNNING",
            "goal_contract": {
                "body": {
                    "acceptance_criteria": [
                        {
                            "id": "C1",
                            "criterion": old,
                            "verification_method": "Compare exact output",
                            "human_review": True,
                        },
                    ]
                }
            },
        }
        original = copy.deepcopy(state["goal_contract"])
        request = self.request(revision)
        request_before = copy.deepcopy(request)
        proposal = human.queue(state, "blocker", {"stage": "astra_resolve"}, request=request)
        text = proposal["request"]["decision_needed"]
        self.assertIn("C1 current: " + json.dumps(old), text)
        self.assertIn("C1 proposed: " + json.dumps(new), text)
        self.assertIn("still needs review and its own approval token", text)
        self.assertEqual(recommendation.ACCEPTANCE, proposal["request"]["options"][0])
        self.assertEqual(original, state["goal_contract"])
        self.assertEqual(request_before, request)
        state.update(
            status="WAITING_FOR_USER",
            user_request=copy.deepcopy(proposal["request"]),
            pending_questions=[{"id": "Q1", "question": text}],
            resolver_human_request={
                "request_id": "R1",
                "request_token": "token",
                "scope": "blocker",
                "request": copy.deepcopy(proposal["request"]),
            },
            resolver={"human_escalations": {"R1": {"status": "pending", "identity": {"proposal": proposal}}}},
        )
        need = run_view.needs(state)
        self.assertEqual(revision, need["recommended_revision"])
        self.assertEqual(recommendation.ACCEPTANCE, need["recommended_revision_response"])
        need["recommended_revision"]["acceptance_criteria"][0]["criterion"] = "Changed in a reader"
        self.assertEqual(new, state["user_request"]["recommended_revision"]["acceptance_criteria"][0]["criterion"])
        state["resolver"]["human_escalations"]["R1"]["status"] = "consumed"
        self.assertNotIn("recommended_revision", run_view.needs(state))

    def test_other_stages_and_ambiguous_questions_cannot_publish_recommendations(self):
        revision = {"acceptance_criteria": [{"id": "C1", "criterion": "Corrected wording"}]}
        initial = {
            "status": "RUNNING",
            "goal_contract": {
                "body": {
                    "acceptance_criteria": [
                        {"id": "C1", "criterion": "Original wording"},
                    ]
                }
            },
        }
        for scope, stage, questions in (
            ("blocker", "astra_review", []),
            ("permission", "astra_resolve", []),
            ("blocker", "astra_resolve", [{"id": "Q1"}, {"id": "Q2"}]),
        ):
            state = copy.deepcopy(initial)
            with self.subTest(scope=scope, stage=stage, questions=questions), self.assertRaises(ValueError):
                human.queue(state, scope, {"stage": stage}, request=self.request(revision), questions=questions)
            self.assertEqual(initial, state)

    def test_canonical_acceptance_never_rebinds_a_stale_request(self):
        state = {"status": "WAITING_FOR_USER", "resolver": {}}
        before = copy.deepcopy(state)
        args = SimpleNamespace(
            resolver_request="R1",
            resolver_token="old",
            resolver_response="provide_information",
            resolver_message=" ACCEPT RECOMMENDED REVISION ",
        )
        runner = SimpleNamespace(
            status_command=SimpleNamespace(reject_input=Mock(return_value=2)), commit_user_action=Mock()
        )
        with (
            patch.object(action, "answer_quota_question", return_value=None),
            patch.object(human, "respond_operational", side_effect=ValueError("stale")),
            patch.object(human, "rebind_stale") as rebind,
        ):
            code = action.answer(runner, args, None, state, None, None, None, {})
        self.assertEqual(2, code)
        rebind.assert_not_called()
        runner.commit_user_action.assert_not_called()
        self.assertEqual(before, state)

    def test_chat_discards_a_partially_applied_candidate_when_installation_fails(self):
        state = {"status": "WAITING_FOR_USER", "goal_contract": {"body": {"criterion": "original"}}}
        before = copy.deepcopy(state)
        published = {"request_id": "R1", "request_token": "token", "request": self.request()}

        def respond(candidate, *_):
            candidate["response"] = "candidate only"

        def apply(candidate, **_):
            candidate["goal_contract"]["body"]["criterion"] = "candidate correction"
            raise ValueError("Plan source guard refused this correction")

        runner = SimpleNamespace(
            lifecycle=SimpleNamespace(apply_resolver_recommendation=apply), commit_user_action=Mock()
        )
        output = io.StringIO()
        with (
            patch("builtins.input", return_value=recommendation.ACCEPTANCE),
            patch.object(human, "respond_operational", side_effect=respond),
            patch.object(human, "review_operational_response") as hold,
            redirect_stdout(output),
        ):
            self.assertFalse(action.chat(runner, state, None, published))
        runner.commit_user_action.assert_not_called()
        hold.assert_not_called()
        self.assertEqual(before, state)
        self.assertIn("The response was not saved", output.getvalue())

    def test_chat_acceptance_without_a_recommendation_does_not_consume_the_request(self):
        state = {"status": "WAITING_FOR_USER", "resolver": {}}
        before = copy.deepcopy(state)
        published = {"request_id": "R1", "request_token": "token", "request": self.request()}

        def respond(candidate, *_):
            candidate["resolver"] = {
                "pending_human_response": {
                    "request_id": "R1",
                    "action": "provide_information",
                    "text": recommendation.ACCEPTANCE,
                },
                "human_escalations": {
                    "R1": {
                        "identity": {
                            "proposal": {
                                "scope": "blocker",
                                "origin": {"stage": "astra_resolve"},
                                "request": self.request(),
                            }
                        }
                    }
                },
            }

        runner = SimpleNamespace(lifecycle=lifecycle, commit_user_action=Mock())
        output = io.StringIO()
        with (
            patch("builtins.input", return_value=recommendation.ACCEPTANCE),
            patch.object(human, "respond_operational", side_effect=respond),
            patch.object(human, "review_operational_response") as hold,
            redirect_stdout(output),
        ):
            self.assertFalse(action.chat(runner, state, None, published))
        runner.commit_user_action.assert_not_called()
        hold.assert_not_called()
        self.assertEqual(before, state)
        self.assertIn("requires a current Resolver criterion recommendation", output.getvalue())

    def test_native_chat_propagates_an_uncertain_commit_without_claiming_rollback(self):
        for replaced, durable in ((False, False), (True, False), (True, True)):
            with self.subTest(replaced=replaced, durable=durable):
                state = {"status": "WAITING_FOR_USER"}
                published = {"request_id": "R1", "request_token": "token", "request": self.request()}
                native, persist = object(), Mock()
                attempted = {}

                def commit(original, candidate, run_dir, **options):
                    attempted.update(options)
                    if replaced:
                        original.clear()
                        original.update(candidate)
                    raise util.JSONCommitUncertain(
                        OSError("directory fsync uncertain"), candidate=candidate, replaced=replaced, durable=durable
                    )

                runner = SimpleNamespace(commit_user_action=commit)
                output = io.StringIO()
                with (
                    patch("builtins.input", return_value="Keep the saved work"),
                    patch.object(human, "respond_operational"),
                    patch.object(action, "review_response", return_value=False),
                    redirect_stdout(output),
                    self.assertRaises(util.JSONCommitUncertain) as raised,
                ):
                    action.chat(runner, state, Path("unused"), published, persist=persist, native=native)
                self.assertIs(attempted["native"], native)
                self.assertIs(attempted["persist"], persist)
                self.assertEqual((replaced, durable), (raised.exception.replaced, raised.exception.durable))
                self.assertNotIn("The response was not saved", output.getvalue())


if __name__ == "__main__":
    unittest.main()
