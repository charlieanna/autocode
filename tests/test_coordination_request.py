"""Coordinated runs retain the authenticated request, including clarifications."""

import unittest

import autocode_run_view as run_view


class CoordinationRequestTests(unittest.TestCase):
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
        for extra, views in (({"approved_contracts": {"token": "old"}}, {}),
                             ({"joint": {}}, {}),
                             ({}, {"api": {"approved_contract": {"token": "old"}}}),
                             ({}, {"api": {"usage": {"accounting": {"active_attempts": 1}}}})):
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
            brief({"task": "Fix history", "request_sources": [
                {"id": "task:0", "kind": "task", "text": "Fix history"},
                {"id": "answer:Q1", "kind": "user_answer", "text": "Keep XML.\nRedact authInfo."},
            ]}),
        )
