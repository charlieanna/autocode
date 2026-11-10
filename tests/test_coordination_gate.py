"""A complete coordinated plan is interpreted before its exact approval action."""

import copy
import importlib
import os
import unittest
from unittest.mock import Mock, patch

import autocode_coordination_conversation as policy
import autocode_coordination_store as store
import autocode_message_context as message_context
import autocode_run_messages as messages
import autocode_util as util
from units import autoplanner as planner

from tests.test_run_messages import receipt, response, working


def approval_context():
    return {
        "kind": "coordinated_plan",
        "participants": [{"target": "api", "status": "AWAITING_GOAL_APPROVAL", "plan": {"token": "r1:api"}}],
        "approval": {"identity": "a" * 64, "publication": "Draft PRs only."},
    }


def gate():
    return importlib.import_module("autocode_coordination_gate")


def packet():
    return {
        "id": "set-1",
        "anchor": "/intake/.autocode/runs/task",
        "revision": 3,
        "source_catalog": {"source_snapshot": "source-1"},
        "members": [{"target": "api", "base": "base-1", "role": "member", "workspace": "/api"}],
        "needs": {
            "kind": "approval",
            "summary": {
                "runs": [
                    {
                        "target": "api",
                        "role": "member",
                        "plan": {"token": "r1:api", "constraints": ["new history only"]},
                    },
                    {
                        "target": "Joint regression",
                        "role": "joint",
                        "plan": {"token": "r1:joint", "checks": ["end to end"]},
                    },
                ]
            },
            "joint_plan": {"token": "r1:joint", "checks": ["end to end"]},
        },
        "approval": {
            "token": "display-token",
            "basis": "b" * 64,
            "publication": "Draft PRs after verification; no merge.",
            "next_command": ["do not copy commands"],
        },
    }


class CoordinatedGateTests(unittest.TestCase):
    def test_joint_only_correction_rebuilds_joint_plan_without_revising_member_plans(self):
        import autocode_coordination_request as requests

        document = {"members": [{"target": "api"}], "joint": {"run_dir": "/joint"}}
        self.assertEqual([], policy.affected(document, ["Joint regression"]))
        self.assertEqual(["api"], policy.affected(document, ["api", "Joint regression"]))
        with self.assertRaises(store.CoordinationError):
            policy.affected(document, ["unknown"])
        document.update(
            task="Fix history",
            conversation={
                "results": [
                    {
                        "request_id": "m1",
                        "text": "Add the failure case to the joint proof.",
                        "reply": {"conversation": {"intent": "change", "affected_targets": ["Joint regression"]}},
                    }
                ]
            },
        )
        self.assertIn("Add the failure case to the joint proof.", requests.joint_brief(document))

    def test_public_message_cli_dispatches_a_bound_plan_reply(self):
        import autocode_coordinate as coordinator

        with (
            patch.dict(os.environ),
            patch.object(coordinator.gate_chat, "submit", return_value={"accepted": True}) as submit,
        ):
            result = coordinator.cli(
                [
                    "message",
                    "--run-dir",
                    "/intake/.autocode/runs/task",
                    "--request-id",
                    "once",
                    "--text",
                    "yes coplete",
                    "--plan-token",
                    "displayed",
                    "--json",
                ]
            )
        self.assertEqual(0, result)
        self.assertEqual(("once", "yes coplete", "displayed"), submit.call_args.args[1:])
        self.assertIs(coordinator.status, submit.call_args.kwargs["read_status"])

    def test_coordinated_approval_uses_the_reviewer_even_for_an_explicit_workflow_source(self):
        state = working()
        state["settings"].pop("spec_first", None)
        messages.receive(state, {**receipt(text="yes coplete"), "context": approval_context()})
        self.assertTrue(messages.activate(state))
        self.assertEqual("finalize_plan", state["next_stage"])

    def test_explicit_workflow_model_schema_preserves_the_required_conversation_answer(self):
        state = working()
        state["settings"].pop("spec_first", None)
        messages.receive(state, {**receipt(text="yes coplete"), "context": approval_context()})
        messages.activate(state)
        offered = planner._model_report_schema(planner.schema_for(state, "finalize_plan"), state)
        self.assertIn("assistant_reply", offered["properties"])
        self.assertIn("assistant_reply", offered["required"])
        self.assertEqual(["confirm", "question", "change", "decline"], offered["properties"]["intent"]["enum"])

    def test_approval_context_uses_saved_reviewer_and_restores_the_original_checkpoint(self):
        state = working()
        original = copy.deepcopy(state)
        event = messages.receive(state, {**receipt(text="yes coplete"), "context": approval_context()})
        self.assertTrue(messages.activate(state))
        self.assertEqual("finalize_plan", state["next_stage"])
        prompt, _ = planner.context(state, "finalize_plan", "/saved/state.json")
        self.assertIn("Plan Reviewer", prompt)
        schema = planner.schema_for(state, "finalize_plan")
        self.assertIn("confirm", schema["properties"]["intent"]["enum"])
        self.assertIn("decline", schema["properties"]["intent"]["enum"])
        before_charge = copy.deepcopy(state["planning"])
        planner.charge(state, "finalize_plan")
        self.assertEqual(before_charge, state["planning"])
        accepted = messages.accept(state, {**response(event, "confirm"), "affected_targets": []}, {})
        self.assertEqual("confirm", accepted["intent"])
        for key in original.keys() - {"user_events"}:
            self.assertEqual(original[key], state[key], key)

    def test_plan_context_is_bounded_and_requires_a_complete_identity(self):
        context = approval_context()
        self.assertEqual(context, message_context.checked(context))
        for mutate in (
            lambda c: c["approval"].update(identity=""),
            lambda c: c["approval"].update(next_command=["unsafe"]),
            lambda c: c["participants"][0].update(plan=None),
        ):
            bad = copy.deepcopy(context)
            mutate(bad)
            with self.assertRaises(ValueError):
                message_context.checked(bad)

    def test_snapshot_keeps_every_plan_and_publication_but_not_commands_or_revision(self):
        shown = packet()
        before = copy.deepcopy(shown)
        snapshot = gate().snapshot(shown)
        self.assertEqual("coordinated_plan", snapshot["kind"])
        self.assertEqual(["api", "Joint regression"], [p["target"] for p in snapshot["participants"]])
        self.assertEqual(shown["approval"]["publication"], snapshot["approval"]["publication"])
        self.assertNotIn("next_command", str(snapshot))
        self.assertEqual(before, shown)
        changed = copy.deepcopy(shown)
        changed["revision"] += 1
        changed["approval"]["token"] = "later-token"
        self.assertEqual(snapshot, gate().snapshot(changed))
        for mutate in (
            lambda p: p["needs"]["summary"]["runs"][0]["plan"].update(token="r2:api"),
            lambda p: p["approval"].update(publication="Publish immediately"),
            lambda p: p["approval"].update(basis="c" * 64),
            lambda p: p["members"][0].update(base="different-base"),
        ):
            changed = copy.deepcopy(shown)
            mutate(changed)
            self.assertNotEqual(snapshot, gate().snapshot(changed))
        changed = copy.deepcopy(shown)
        changed["needs"]["joint_plan"].update(checks=["weaker"])
        with self.assertRaises(ValueError):
            gate().snapshot(changed)

    def test_snapshot_refuses_missing_identity_or_missing_member_plan(self):
        for mutate in (
            lambda p: p["approval"].pop("basis"),
            lambda p: p["approval"].update(basis="malformed"),
            lambda p: p["needs"]["summary"]["runs"].pop(0),
            lambda p: p["needs"]["summary"]["runs"][0].update(role="joint"),
        ):
            shown = packet()
            mutate(shown)
            with self.assertRaises(ValueError):
                gate().snapshot(shown)

    def test_plan_reply_is_reconciled_by_request_and_context_and_never_invalidates_on_confirmation(self):
        context = approval_context()
        active = {"source_request_id": "r1", "context": context}
        reply = {
            "role": "assistant",
            "text": "Approved as shown.",
            "conversation": {
                "request_id": "r1",
                "context_sha256": util.digest(context),
                "intent": "confirm",
                "affected_targets": [],
            },
        }
        self.assertEqual(reply, policy.reply(active, {"chat": {"messages": [reply]}}))
        active["reply"] = reply
        active["id"] = "m1"
        doc = {
            "id": "set1",
            "anchor": "/anchor",
            "members": [{"target": "api", "started": True}],
            "conversation": {"active": active},
        }
        views = {"api": {"status": "AWAITING_GOAL_APPROVAL", "displayed_plan": {"token": "r1:api"}}}
        self.assertTrue(policy.context_matches(doc, views, context))
        self.assertEqual({"kind": "conversation", "operation": "finish"}, policy.step(doc, [{"id": "m1"}], views, {}))
        views["api"]["displayed_plan"]["token"] = "r2:api"
        self.assertFalse(policy.context_matches(doc, views, context))

    def test_submission_binds_the_displayed_token_and_retries_without_rereading_a_changed_gate(self):
        shown = packet()
        read = Mock(return_value=shown)
        with patch.object(gate().inbox, "read", return_value=[]), patch.object(gate().inbox, "submit") as submit:
            gate().submit("/anchor", "once", "yes coplete", "display-token", read_status=read)
            supplied = submit.call_args.kwargs
            self.assertEqual("display-token", supplied["plan_token"])
            self.assertEqual(gate().snapshot(shown), supplied["plan_context"])
            read.assert_called_once_with("/anchor")
        read.reset_mock()
        old = {"id": "once", **supplied}
        with patch.object(gate().inbox, "read", return_value=[old]), patch.object(gate().inbox, "submit"):
            gate().submit("/anchor", "once", "yes coplete", "display-token", read_status=read)
            read.assert_not_called()
        with patch.object(gate().inbox, "read", return_value=[]), patch.object(gate().inbox, "submit") as submit:
            with self.assertRaises(store.CoordinationError):
                gate().submit("/anchor", "once", "yes", "stale", read_status=read)
            submit.assert_not_called()

    def test_only_latest_confirmed_exact_packet_can_offer_an_approval_decision(self):
        shown = packet()
        context = gate().snapshot(shown)
        request = {"id": "reply-1", "text": "yes coplete", "plan_context": context}
        result = {
            "request_id": "reply-1",
            "context": context,
            "reply": {"text": "You approved these plans.", "conversation": {"intent": "confirm"}},
        }
        self.assertEqual({"message_id": "reply-1", "intent": "confirm"}, gate().decision(shown, [request], [result]))
        for mutate in (
            lambda p, req, res: p["approval"].update(publication="Merge automatically"),
            lambda p, req, res: res[0]["reply"]["conversation"].update(intent="change"),
            lambda p, req, res: req.append({"id": "later", "text": "wait"}),
            lambda p, req, res: p.pop("approval"),
            lambda p, req, res: res[0].update(context={}),
        ):
            p, req, res = copy.deepcopy((shown, [request], [result]))
            mutate(p, req, res)
            self.assertIsNone(gate().decision(p, req, res))


if __name__ == "__main__":
    unittest.main()
