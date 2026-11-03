"""Coordinated runs retain the authenticated request, including clarifications."""

import unittest

import autocode_run_view as run_view


class CoordinationRequestTests(unittest.TestCase):
    def late_document(self):
        narrow = {"request_id": "narrow", "text": "Only the example registry. Payloads may stay empty."}
        broad = {"request_id": "broad", "text": "Correction: any non-EPP registry. Do not convert payloads."}
        return {
            "task": "Fix history",
            "request_sources": [],
            "members": [
                {"target": "api", "message_sources": [narrow, broad]},
                {"target": "untouched"},
                {"target": "late", "started": True, "brief": "Fix history"},
            ],
            "conversation": {
                "results": [
                    {
                        **row,
                        "context": {
                            "kind": "coordinated_task",
                            "participants": [{"target": "api"}, {"target": "untouched"}],
                        },
                        "affected_targets": ["api"],
                        "reply": {
                            "text": "Model paraphrase must not become a requirement",
                            "conversation": {"intent": "change"},
                        },
                    }
                    for row in (narrow, broad)
                ]
            },
        }

    def test_late_member_receives_exact_prior_clarifications_with_their_original_scope(self):
        from autocode_coordination_request import brief

        doc = self.late_document()
        text = brief(doc, target="late")
        for source in doc["members"][0]["message_sources"]:
            self.assertEqual(1, text.count(source["text"]))
        self.assertLess(text.index("Only the example"), text.index("Correction:"))
        self.assertIn("applied to api", text)
        self.assertIn("not permission to change another repository or approve a plan", text)
        self.assertNotIn("Model paraphrase", text)
        self.assertNotIn("message_sources", doc["members"][2], "context is not a delivered change receipt")
        self.assertEqual("Fix history", brief(doc, target="untouched"))
        self.assertEqual("Fix history", brief(doc), "global and joint request scope must not be broadened")
        self.assertEqual(
            "Fix history\n\nOnly the example registry. Payloads may stay empty.\n\n"
            "Correction: any non-EPP registry. Do not convert payloads.",
            brief(doc, target="api"),
        )

    def test_late_context_requires_a_completed_change_and_matching_applied_human_text(self):
        from autocode_coordination_request import brief

        for invalid in ("question", "mismatched_text", "missing_receipt", "no_context", "pending"):
            with self.subTest(invalid=invalid):
                doc = self.late_document()
                result = doc["conversation"]["results"][1]
                doc["conversation"]["results"] = [result]
                if invalid == "question":
                    result["reply"]["conversation"]["intent"] = "question"
                elif invalid == "mismatched_text":
                    result["text"] = "Unconfirmed source text"
                elif invalid == "missing_receipt":
                    doc["members"][0]["message_sources"] = []
                elif invalid == "no_context":
                    result.pop("context")
                else:
                    doc["conversation"] = {"active": result, "results": []}
                self.assertEqual("Fix history", brief(doc, target="late"))

    def test_late_context_precedes_member_changes_and_saved_recovery_happens_once(self):
        from autocode_coordination_request import brief, recovery

        doc = self.late_document()
        for member in doc["members"][:2]:
            member["brief"] = brief(doc, target=member["target"])
            member["started"] = True
        late = doc["members"][2]
        late["message_sources"] = [{"request_id": "later", "text": "Keep this member read-only."}]
        expected = brief(doc, target="late")
        self.assertIn("any non-EPP registry", expected)
        self.assertTrue(expected.endswith("Keep this member read-only."))
        self.assertEqual({"kind": "request_feedback", "target": "late", "message": expected}, recovery(doc, {}, []))
        late["brief"] = expected
        self.assertIsNone(recovery(doc, {}, []))
        late["brief"] = "Fix history"
        self.assertEqual("blocked", recovery(doc, {"late": {"approved_contract": {"token": "approved"}}}, [])["kind"])

    def test_joint_request_keeps_applied_messages_once_with_their_member_scope_and_order(self):
        from autocode_coordination_request import joint_brief

        shared = {"request_id": "first", "text": "Preserve both supported routes.\nKeep existing results."}
        later = {"request_id": "second", "text": "Keep the consumer change test-only."}
        doc = {
            "task": "Fix history",
            "request_sources": [{"text": "Keep legacy history."}],
            "members": [
                {"target": "consumer", "message_sources": [later]},
                {"target": "api", "message_sources": [shared]},
                {"target": "client", "message_sources": [shared]},
            ],
            "conversation": {
                "results": [
                    {"request_id": "question", "text": "Could we convert this?", "affected_targets": []},
                    {"request_id": "first", "text": "Not an alternative source of message text"},
                    {"request_id": "second"},
                ],
                "active": {"text": "Unapplied change"},
            },
        }
        self.assertEqual(
            "Fix history\n\nKeep legacy history.\n\n"
            "Applied task message for api, client:\nPreserve both supported routes.\nKeep existing results.\n\n"
            "Applied task message for consumer:\nKeep the consumer change test-only.",
            joint_brief(doc),
        )

    def test_joint_request_keeps_legacy_text_and_unindexed_applied_messages(self):
        from autocode_coordination_request import joint_brief

        self.assertEqual("Fix history", joint_brief({"task": "Fix history"}))
        self.assertEqual(
            "Fix history\n\nApplied task message for api:\nKeep XML.",
            joint_brief(
                {
                    "task": "Fix history",
                    "members": [{"target": "api", "message_sources": [{"request_id": "saved", "text": "Keep XML."}]}],
                }
            ),
        )

    def test_task_messages_extend_only_their_members_without_triggering_legacy_recovery(self):
        from autocode_coordination_request import brief, recovery

        doc = {
            "task": "Fix history",
            "request_sources": [],
            "members": [
                {
                    "target": "api",
                    "started": True,
                    "brief": "Fix history\n\nPreserve XML.",
                    "message_sources": [{"request_id": "message", "text": "Preserve XML."}],
                },
                {"target": "other", "started": True, "brief": "Fix history"},
            ],
        }
        self.assertEqual("Fix history\n\nPreserve XML.", brief(doc, target="api"))
        self.assertEqual("Fix history", brief(doc, target="other"))
        self.assertIsNone(recovery(doc, {}, []))
        self.assertEqual("Fix history", brief(doc))

    def test_saved_unapproved_runs_adopt_sources_and_refresh_each_old_member_brief(self):
        from autocode_coordination_request import recovery

        sources = [{"id": "answer:Q1", "kind": "user_answer", "text": "Keep 2304."}]
        member = {"target": "api", "started": True, "brief": "Fix history"}
        doc = {"task": "Fix history", "members": [member]}
        self.assertEqual({"kind": "sync_request", "sources": sources}, recovery(doc, {}, sources))
        doc["request_sources"] = sources
        self.assertEqual(
            {"kind": "request_feedback", "target": "api", "message": "Fix history\n\nKeep 2304."},
            recovery(doc, {}, sources),
        )
        member["brief"] = "Fix history\n\nKeep 2304."
        self.assertIsNone(recovery(doc, {}, sources))
        member.update(started=False, brief="Fix history")
        self.assertEqual({"kind": "sync_request", "sources": sources}, recovery(doc, {}, sources))
        self.assertIsNone(recovery({"task": "Legacy"}, {}, None))

    def test_request_recovery_never_rewrites_approved_or_active_work(self):
        from autocode_coordination_request import recovery

        doc = {"task": "Fix history", "members": []}
        for extra, views in (
            ({"approved_contracts": {"token": "old"}}, {}),
            ({"joint": {}}, {}),
            ({}, {"api": {"approved_contract": {"token": "old"}}}),
            ({}, {"api": {"usage": {"accounting": {"active_attempts": 1}}}}),
        ):
            with self.subTest(extra=extra, views=views):
                self.assertEqual("blocked", recovery({**doc, **extra}, views, [])["kind"])

    def test_public_request_sources_keep_live_human_clarifications_only(self):
        answer = {"kind": "answer", "actor": "user_cli", "question_id": "Q1", "text": "Result must be 2304."}
        state = {
            "task": "Fix history",
            "answers": {"Q1": answer},
            "user_events": [answer],
            "source_log": [
                {"id": "task:0", "kind": "task", "text": "Fix history"},
                {"id": "answer:Q1", "kind": "user_answer", "text": answer["text"]},
                {"id": "answer:gone", "kind": "user_answer", "text": "Discarded answer"},
                {"id": "model:0", "kind": "assistant", "text": "Invented requirement"},
            ],
        }
        self.assertEqual(state["source_log"][:2], run_view.view(state)["request_sources"])

    def test_member_and_joint_request_preserves_exact_text_and_legacy_tasks(self):
        from autocode_coordination_request import brief

        self.assertEqual("Fix history", brief({"task": "Fix history"}))
        self.assertEqual(
            "Fix history\n\nKeep XML.\nRedact authInfo.",
            brief(
                {
                    "task": "Fix history",
                    "request_sources": [
                        {"id": "task:0", "kind": "task", "text": "Fix history"},
                        {"id": "answer:Q1", "kind": "user_answer", "text": "Keep XML.\nRedact authInfo."},
                    ],
                }
            ),
        )
