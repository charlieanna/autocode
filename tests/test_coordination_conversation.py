"""Task conversation decisions retain exact authority and public evidence."""

import copy
import importlib
import unittest

import autocode_coordination_store as store
import autocode_util as util


def policy():
    return importlib.import_module("autocode_coordination_conversation")


def document():
    return {
        "id": "task-id",
        "anchor": "/source/.autocode/runs/task",
        "phase": "fixing",
        "members": [
            {"target": "api", "workspace": "/api", "run_dir": "/api/run", "started": True},
            {"target": "gateway", "workspace": "/gateway", "run_dir": "/gateway/run", "started": True},
            {"target": "history", "workspace": "/history", "run_dir": "/history/run", "started": True},
        ],
        "dependencies": {"gateway": ["api"], "history": ["gateway"], "api": []},
    }


def paused(*receipts, held=None):
    return {
        "status": "PAUSED_INTERVENTION",
        "needs": {"kind": "resume"},
        "recovery": {"cause": "PAUSED_INTERVENTION"},
        "interventions": {
            "applied_receipts": list(receipts),
            "pending_ids": [],
            "pending_count": 0,
            "pause_intent": {"request_ids": [receipts[-1]["id"]] if receipts else [], "held_pause": held},
        },
    }


class ConversationPolicyTests(unittest.TestCase):
    def test_saved_owned_pause_can_recover_its_known_admission_boundary_only(self):
        own = {"id": "owned", "kind": "pause"}
        held = {
            "status": "PAUSED_INTERVENTION_PENDING",
            "stop_reason": "Queued intervention must be applied before this action; explicitly continue.",
        }
        self.assertTrue(policy().resumable(paused(own, held=held), {"owned"}))
        self.assertFalse(policy().resumable(paused({"id": "person", "kind": "pause"}, own, held=held), {"owned"}))
        self.assertFalse(policy().resumable(paused(own, held={**held, "stop_reason": "Unreadable inbox"}), {"owned"}))
        self.assertFalse(policy().resumable(paused(own, held={**held, "status": "PAUSED_COST_LIMIT"}), {"owned"}))
        waiting = paused(own, held=held)
        waiting["interventions"].update(pending_count=1, pending_ids=["new"])
        self.assertFalse(policy().resumable(waiting, {"owned"}))

    def test_request_identity_is_stable_and_binds_task_request_target_and_operation(self):
        record, message = document(), {"id": "question-1", "text": "What do you think?"}
        original = policy().request_id(record, message, "api", "pause")
        self.assertEqual(original, policy().request_id(copy.deepcopy(record), message, "api", "pause"))
        self.assertLessEqual(len(original), 128)
        alternatives = [
            policy().request_id({**record, "id": "other"}, message, "api", "pause"),
            policy().request_id(record, {**message, "id": "question-2"}, "api", "pause"),
            policy().request_id(record, message, "gateway", "pause"),
            policy().request_id(record, message, "api", "feedback"),
        ]
        self.assertEqual(5, len(set([original, *alternatives])))

    def test_only_owned_unresumed_pause_and_feedback_can_be_resumed(self):
        own = {"id": "owned", "kind": "pause"}
        self.assertTrue(policy().resumable(paused(own), {"owned"}))
        self.assertTrue(policy().resumable(paused({**own, "kind": "feedback"}), {"owned"}))
        prior = {"id": "old-user", "kind": "pause", "resumed_at": "yesterday"}
        self.assertTrue(policy().resumable(paused(prior, own), {"owned"}))
        self.assertFalse(policy().resumable(paused({"id": "user", "kind": "pause"}, own), {"owned"}))
        self.assertFalse(policy().resumable(paused({"id": "user", "kind": "feedback"}, own), {"owned"}))

    def test_stop_operational_hold_and_unknown_pending_receipts_never_resume(self):
        own = {"id": "owned", "kind": "pause"}
        cases = [
            paused({"id": "owned", "kind": "stop"}),
            paused(own, held={"status": "PAUSED_COST_LIMIT"}),
            paused(),
            {**paused(own), "status": "PAUSED_COST_LIMIT"},
            {**paused(own), "needs": {"kind": "answer", "resolver_scope": "operational_exhaustion"}},
            {**paused(own), "liveness": {"kind": "supervised"}},
        ]
        for key, value in (
            ("pending_ids", ["incoming"]),
            ("pending_count", 1),
            ("inbox_error", {"code": "bad"}),
            ("stop_intent", {"request_ids": ["owned"]}),
        ):
            current = paused(own)
            current["interventions"][key] = value
            cases.append(current)
        for case in cases:
            with self.subTest(case=case):
                self.assertFalse(policy().resumable(case, {"owned"}))

    def test_context_contains_only_current_public_member_plans_in_authorized_order(self):
        record = document()
        public = {
            "api": {
                "status": "AWAITING_GOAL_APPROVAL",
                "displayed_plan": {"token": "a", "goal": "Fix API"},
                "next_command": "do-not-copy",
                "workspace": "/private/path",
            },
            "gateway": {"status": "PAUSED_INTERVENTION", "displayed_plan": {"token": "g"}},
            "history": {"status": "RUNNING", "displayed_plan": None},
            "unrelated": {"status": "RUNNING"},
        }
        result = policy().context(record, public)
        self.assertEqual("coordinated_task", result["kind"])
        self.assertEqual(["api", "gateway", "history"], [row["target"] for row in result["participants"]])
        self.assertEqual({"target", "status", "plan"}, set(result["participants"][0]))
        self.assertIsNone(result["participants"][-1]["plan"])
        result["participants"][0]["plan"]["token"] = "edited"
        self.assertEqual("a", public["api"]["displayed_plan"]["token"])

    def test_missing_started_member_status_is_not_fabricated(self):
        with self.assertRaisesRegex(store.CoordinationError, "public status"):
            policy().context(document(), {})
        record = document()
        for member in record["members"]:
            member["started"] = False
        result = policy().context(record, {})
        self.assertEqual({"STARTING"}, {row["status"] for row in result["participants"]})

    def test_large_public_plans_are_projected_without_dropping_participants_or_requirements(self):
        from tests.test_message_context import large_context

        supplied = large_context()
        record = {"members": [{"target": row["target"], "started": True} for row in supplied["participants"]]}
        views = {
            row["target"]: {"status": row["status"], "displayed_plan": row["plan"]} for row in supplied["participants"]
        }
        before = copy.deepcopy(views)
        result = policy().context(record, views)
        self.assertEqual(["gateway", "reader"], [row["target"] for row in result["participants"]])
        plan = result["participants"][0]["plan"]
        self.assertEqual(supplied["participants"][0]["plan"]["constraints"], plan["constraints"])
        self.assertEqual("r3:exact-plan", plan["token"])
        self.assertEqual("requirements_and_checks", plan["conversation_projection"]["kind"])
        self.assertEqual(before, views)

    def test_affected_targets_include_transitive_dependents_not_unrelated_prerequisites(self):
        record = document()
        self.assertEqual(["gateway", "history"], policy().affected(record, ["gateway"]))
        self.assertEqual(["api", "gateway", "history"], policy().affected(record, ["api"]))
        self.assertEqual(["history"], policy().affected(record, ["history"]))
        record["dependencies"]["api"] = ["history"]
        self.assertEqual(["api", "gateway", "history"], policy().affected(record, ["api"]))
        for invalid in ([], ["outside"], ["api", "api"], "api"):
            with self.subTest(invalid=invalid), self.assertRaises(store.CoordinationError):
                policy().affected(record, invalid)

    def test_reply_matches_exact_request_context_and_validated_routing(self):
        context = {"kind": "coordinated_task", "participants": [{"target": "api", "status": "READY", "plan": None}]}
        active = {"source_request_id": "request", "context": context}
        metadata = {
            "request_id": "request",
            "context_sha256": util.digest(context),
            "intent": "question",
            "affected_targets": [],
        }
        answer = {"id": "reply", "role": "assistant", "text": "It needs investigation.", "conversation": metadata}
        source = {"chat": {"messages": [{**answer, "conversation": {**metadata, "request_id": "older"}}, answer]}}
        self.assertEqual(answer, policy().reply(active, source))
        self.assertIsNone(policy().reply(active, {"chat": {"messages": []}}))
        for mutation in (
            {"context_sha256": "stale"},
            {"intent": "question", "affected_targets": ["api"]},
            {"intent": "change", "affected_targets": []},
            {"intent": "change", "affected_targets": ["outside"]},
            {"intent": "approve", "affected_targets": []},
        ):
            with self.subTest(mutation=mutation), self.assertRaises(store.CoordinationError):
                policy().reply(active, {"chat": {"messages": [{**answer, "conversation": {**metadata, **mutation}}]}})
        with self.assertRaisesRegex(store.CoordinationError, "multiple"):
            policy().reply(active, {"chat": {"messages": [answer, answer]}})

    def test_unrelated_historical_or_user_display_rows_do_not_count_as_replies(self):
        active = {"source_request_id": "request", "context": {}}
        source = {
            "chat": {
                "messages": [
                    {"role": "user", "conversation": {"request_id": "request"}},
                    {"role": "assistant", "text": "Old resolver display"},
                ]
            }
        }
        self.assertIsNone(policy().reply(active, source))


class ConversationStepTests(unittest.TestCase):
    def setUp(self):
        self.record = document()
        self.messages = [{"id": "question-1", "text": "Could we reuse the converter?"}]
        self.views = {
            row["target"]: {
                "status": "AWAITING_GOAL_APPROVAL",
                "needs": {"kind": "approve_plan"},
                "displayed_plan": {"token": row["target"]},
            }
            for row in self.record["members"]
        }
        self.source = {
            "status": "WAITING_FOR_USER",
            "needs": {"kind": "answer", "resolver_scope": "clarification"},
            "interventions": {"runner_capability": {"supports_message_context": True}},
        }

    def step(self):
        return policy().step(self.record, self.messages, self.views, self.source)

    def activate(self):
        active = {
            "id": self.messages[0]["id"],
            "source_request_id": "source-message",
            "context": policy().context(self.record, self.views),
        }
        self.record["conversation"] = {"active": active, "results": []}
        return active

    def test_idle_message_begins_before_unrelated_planning_and_records_no_approval(self):
        before = copy.deepcopy(self.record)
        self.assertEqual({"kind": "conversation", "operation": "begin", "request_id": "question-1"}, self.step())
        self.assertEqual(before, self.record)

    def test_active_member_must_pause_before_requirements_runs(self):
        self.views["api"].update(liveness={"kind": "supervised"}, pause={"next_command": ["named-pause"]})
        selected = self.step()
        self.assertEqual(
            ("conversation", "pause", "api"), (selected["kind"], selected["operation"], selected["target"])
        )
        self.views["api"]["pause"]["requested"] = True
        self.assertEqual("watch", self.step()["kind"])
        self.views["api"]["pause"] = None
        self.assertEqual("watch", self.step()["kind"])

    def test_joint_worker_also_must_settle_and_missing_member_status_blocks(self):
        self.views["Joint regression"] = {"liveness": {"kind": "supervised"}}
        self.assertEqual("watch", self.step()["kind"])
        self.views.pop("Joint regression")
        self.views.pop("gateway")
        self.assertEqual("blocked", self.step()["kind"])

    def test_unconsumed_owned_pause_is_consumed_without_replaying_unknown_input(self):
        owned = policy().request_id(self.record, self.messages[0], "api", "pause")
        self.views["api"]["interventions"] = {"pending_ids": [owned], "pending_count": 1}
        self.assertEqual("consume", self.step()["operation"])
        self.views["api"]["interventions"]["pending_ids"].append("external-feedback")
        self.assertEqual("begin", self.step()["operation"])

    def test_independent_pending_hold_allows_assessment_but_never_child_dispatch(self):
        self.views["api"]["interventions"] = {"pending_ids": ["external-pause"], "pending_count": 1}
        original = copy.deepcopy(self.views)
        self.assertEqual("begin", self.step()["operation"])
        active = self.activate()
        self.assertEqual("submit", self.step()["operation"])
        self.source["interventions"]["pending_ids"] = [active["source_request_id"]]
        self.assertEqual("source_advance", self.step()["operation"])
        answer = {
            "role": "assistant",
            "text": "The gateway plan needs revision.",
            "conversation": {
                "request_id": active["source_request_id"],
                "context_sha256": util.digest(active["context"]),
                "intent": "change",
                "affected_targets": ["gateway"],
            },
        }
        self.source["chat"] = {"messages": [answer]}
        self.assertEqual("capture", self.step()["operation"])
        active["reply"] = answer
        self.assertEqual("blocked", self.step()["kind"])
        active.update(invalidated=True, targets=["gateway", "history"], delivered=[])
        self.assertEqual("blocked", self.step()["kind"])
        self.assertEqual(original, self.views)

    def test_question_can_finish_with_independent_pending_hold_but_hold_still_blocks_work(self):
        self.views["api"]["interventions"] = {"pending_ids": ["external-pause"], "pending_count": 1}
        active = self.activate()
        active["reply"] = {"conversation": {"intent": "question", "affected_targets": []}}
        self.assertEqual("finish", self.step()["operation"])
        self.record["conversation"] = {"results": [{"request_id": self.messages[0]["id"]}]}
        self.assertEqual("blocked", self.step()["kind"])

    def test_independent_hold_never_hides_an_active_later_participant(self):
        self.views["api"]["interventions"] = {"pending_ids": ["external-pause"], "pending_count": 1}
        self.views["history"]["liveness"] = {"kind": "supervised"}
        self.assertEqual({"kind": "watch", "target": "history"}, self.step())

    def test_unreadable_intervention_receipt_still_blocks_assessment(self):
        for intervention in ({"pending_count": 1}, {"inbox_error": "unreadable"}):
            with self.subTest(intervention=intervention):
                self.views["api"]["interventions"] = intervention
                self.assertEqual("blocked", self.step()["kind"])

    def test_captured_answer_is_projected_once_without_finishing_routing(self):
        active = self.activate()
        self.assertEqual([], policy().results(self.record, self.messages))
        active.update(
            reply={
                "id": "answer",
                "text": "Revise the gateway plan.",
                "conversation": {"intent": "change", "affected_targets": ["gateway"]},
            },
            answered_at="captured-time",
        )
        before = copy.deepcopy(self.record)
        result = policy().results(self.record, self.messages)
        self.assertEqual(
            [
                {
                    "request_id": "question-1",
                    "text": self.messages[0]["text"],
                    "reply": active["reply"],
                    "context": active["context"],
                    "affected_targets": ["gateway", "history"],
                    "answered_at": "captured-time",
                }
            ],
            result,
        )
        self.assertEqual(before, self.record)
        self.assertEqual([], policy().results(self.record, []))
        self.record["conversation"]["results"] = copy.deepcopy(result)
        self.assertEqual(result, policy().results(self.record, self.messages))
        result[0]["reply"]["text"] = "edited display"
        self.assertEqual("Revise the gateway plan.", active["reply"]["text"])

    def test_submission_then_source_advancement_reconcile_from_public_receipts(self):
        self.activate()
        self.assertEqual("submit", self.step()["operation"])
        self.source["interventions"]["pending_ids"] = ["source-message"]
        self.source["interventions"]["pending_count"] = 1
        self.assertEqual("source_advance", self.step()["operation"])
        self.source["interventions"]["pending_ids"] = []
        self.source["interventions"]["pending_count"] = 0
        self.source["interventions"]["applied_receipts"] = [{"id": "source-message", "kind": "message"}]
        self.source["status"] = "RUNNING"
        self.source["needs"] = {"kind": "continue"}
        self.assertEqual("source_advance", self.step()["operation"])

    def test_source_operational_hold_or_user_stop_is_not_bypassed(self):
        self.activate()
        for status, needs in [
            ("PAUSED_COST_LIMIT", {"kind": "resume"}),
            ("WAITING_FOR_USER", {"kind": "answer", "resolver_scope": "operational_exhaustion"}),
        ]:
            with self.subTest(status=status):
                self.source.update(status=status, needs=needs)
                self.assertEqual("blocked", self.step()["kind"])
        self.source.update(status="RUNNING", needs={"kind": "continue"})
        self.source["interventions"]["applied_receipts"] = [{"id": "stop", "kind": "stop"}]
        self.assertEqual("blocked", self.step()["kind"])

    def test_legacy_source_can_only_refresh_capability_at_an_idle_clarification_gate(self):
        active = self.activate()
        self.source["interventions"]["runner_capability"] = {}
        self.assertEqual("prepare_source", self.step()["operation"])
        active["prepared_source"] = True
        self.assertEqual("blocked", self.step()["kind"])
        self.source.update(status="RUNNING", needs={"kind": "continue"})
        self.assertEqual("blocked", self.step()["kind"])

    def test_matching_answer_is_captured_but_changed_member_context_is_refused(self):
        active = self.activate()
        self.source["chat"] = {
            "messages": [
                {
                    "role": "assistant",
                    "text": "A possible approach.",
                    "conversation": {
                        "request_id": active["source_request_id"],
                        "context_sha256": util.digest(active["context"]),
                        "intent": "question",
                        "affected_targets": [],
                    },
                }
            ]
        }
        self.assertEqual("capture", self.step()["operation"])
        self.views["gateway"]["displayed_plan"]["token"] = "changed-externally"
        self.assertEqual("blocked", self.step()["kind"])

    def test_question_finishes_without_invalidating_or_applying_feedback(self):
        active = self.activate()
        active["reply"] = {
            "text": "A possible approach.",
            "conversation": {"intent": "question", "affected_targets": []},
        }
        self.assertEqual("finish", self.step()["operation"])

    def test_change_invalidates_then_delivers_original_text_to_each_affected_target(self):
        active = self.activate()
        active["reply"] = {
            "text": "Gateway must change.",
            "conversation": {"intent": "change", "affected_targets": ["gateway"]},
        }
        self.assertEqual("invalidate", self.step()["operation"])
        active.update(invalidated=True, targets=["gateway", "history"], delivered=[])
        self.assertEqual(("feedback", "gateway"), (self.step()["operation"], self.step()["target"]))
        feedback_id = policy().request_id(self.record, self.messages[0], "gateway", "feedback")
        self.views["gateway"]["interventions"] = {"applied_receipts": [{"id": feedback_id, "kind": "feedback"}]}
        self.assertEqual("delivered", self.step()["operation"])
        active["delivered"].append("gateway")
        self.assertEqual("history", self.step()["target"])
        self.record["members"][2]["started"] = False
        self.views.pop("history")
        self.assertEqual("unstarted", self.step()["operation"])
        active["delivered"].append("history")
        self.assertEqual("finish", self.step()["operation"])

    def test_second_message_is_answered_before_resuming_owned_work(self):
        owned = policy().request_id(self.record, self.messages[0], "api", "pause")
        self.views["api"] = paused({"id": owned, "kind": "pause"})
        self.record["conversation"] = {"results": [{"request_id": "question-1"}]}
        self.messages.append({"id": "question-2", "text": "One more question"})
        self.assertEqual(("begin", "question-2"), (self.step()["operation"], self.step()["request_id"]))
        self.record["conversation"]["results"].append({"request_id": "question-2"})
        self.assertEqual(("resume", "api"), (self.step()["operation"], self.step()["target"]))
        self.views["api"]["interventions"]["applied_receipts"][0]["resumed_at"] = "now"
        self.assertIsNone(self.step())


if __name__ == "__main__":
    unittest.main()
