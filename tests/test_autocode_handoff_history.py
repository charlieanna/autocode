"""Review prompts shorten discussion an approved contract already settled, and nothing else."""
import copy
import json
from pathlib import Path
import tempfile
import unittest

import autocode_context as context
import autocode_handoff_history as history

APPROVED = "2026-09-28T21:27:11.890219+00:00"
BEFORE, AFTER = "2026-09-27T10:00:00+00:00", "2026-09-29T08:00:00+00:00"


def question(qid, text):
    return {"id": qid, "question": text, "why": "Why this matters: " + "context " * 60,
            "options": ["Option 1: keep it", "Option 2: replace it"], "proposed_default": "Option 1: keep it",
            "category": "scope", "delegable": True, "kind": "blocking"}


def packet(stage="sol", status="approved"):
    return {"stage": stage, "state_file": "/runs/r/state.json",
            "goal_contract": {"revision": 9, "hash": "h", "approval_status": status,
                              "approval_event": {"kind": "goal_approval", "at": APPROVED}, "body": {}},
            "brief_feedback": [
                {"kind": "brief_feedback", "id": "feedback-early", "actor": "user_cli", "at": BEFORE,
                 "text": "Never deploy to production. " + "Keep the export format byte-identical. " * 40,
                 "retained_work": {"stages": 16, "notes": "kept " * 200}},
                {"kind": "brief_feedback", "id": "feedback-late", "actor": "user_cli", "at": AFTER,
                 "text": "Also keep the old flag working. " * 20}],
            "saved_answers": {
                "Q1": {"kind": "answer", "actor": "user_cli", "at": BEFORE, "question_id": "Q1",
                       "question": question("Q1", "May the build delete the legacy cache?"),
                       "text": "No. Do not delete it; move it under .old/ instead.", "contract_token": "r3:x",
                       "starts_episode": False},
                "Q2": {"kind": "delegated", "actor": "user_cli", "at": AFTER, "question_id": "Q2",
                       "question": question("Q2", "Which port?"), "text": "Option 1: keep it",
                       "contract_token": "r9:y", "starts_episode": False},
                "P1": {"kind": "permission_answer", "actor": "user_cli", "at": BEFORE, "question_id": "P1",
                       "question": question("P1", "May the build install pytest?"), "text": "Yes, only in .venv.",
                       "request": {"kind": "permission", "decision_needed": "Install pytest " * 30},
                       "contract_token": "r8:z"}}}


class CondenseTests(unittest.TestCase):
    def test_review_stages_shorten_settled_records_and_move_them_whole(self):
        for stage in history.STAGES:
            with self.subTest(stage=stage):
                original = packet(stage)
                before = copy.deepcopy(original)
                small, moved = history.condense(original)
                self.assertEqual(before, original)
                self.assertLess(len(json.dumps(small)), len(json.dumps(original)))
                early = small["brief_feedback"][0]
                self.assertEqual(("feedback-early", BEFORE, "user_cli"), (early["id"], early["at"], early["actor"]))
                self.assertTrue(early["excerpt"].startswith("Never deploy to production."))
                self.assertEqual(len(original["brief_feedback"][0]["text"]), early["chars"])
                self.assertNotIn("retained_work", early)
                self.assertEqual({"question": "May the build delete the legacy cache?",
                                  "answer": "No. Do not delete it; move it under .old/ instead.", "kind": "answer",
                                  "at": BEFORE, "options": ["Option 1: keep it", "Option 2: replace it"]},
                                 small["saved_answers"]["Q1"])
                self.assertEqual(original["brief_feedback"][1], small["brief_feedback"][1])
                self.assertEqual(original["saved_answers"]["Q2"], small["saved_answers"]["Q2"])
                self.assertEqual(original["saved_answers"]["P1"], small["saved_answers"]["P1"])
                self.assertEqual({"brief_feedback": [original["brief_feedback"][0]],
                                  "saved_answers": {"Q1": original["saved_answers"]["Q1"]}}, moved)
                self.assertIn("context_artifact", small["settled_history_note"])
                self.assertEqual((small, {}), history.condense(small))

    def test_other_stages_and_unsettled_contracts_keep_everything(self):
        cases = [packet(stage) for stage in ("terra", "astra_discovery", "astra_plan", "astra_resolve")]
        cases.append(packet(status="draft"))
        unapproved = packet()
        unapproved["goal_contract"].pop("approval_event")
        cases.append(unapproved)
        naive = packet()
        naive["goal_contract"]["approval_event"]["at"] = "2026-09-28T21:27:11"
        cases.append(naive)
        undated = packet()
        for record in [*undated["brief_feedback"], *undated["saved_answers"].values()]:
            record.pop("at")
        cases.append(undated)
        cases.append({"stage": "sol", "goal_contract": None, "saved_answers": {}, "brief_feedback": []})
        for original in cases:
            with self.subTest(stage=original["stage"]):
                self.assertEqual((original, {}), history.condense(original))

    def test_short_settled_records_are_left_alone_when_shortening_would_not_shrink_the_prompt(self):
        original = packet()
        original["brief_feedback"] = [{"id": "f1", "at": BEFORE, "actor": "user_cli", "text": "No auth."}]
        original["saved_answers"] = {"Q1": {"at": BEFORE, "kind": "answer", "question": "Port?", "text": "80"}}
        self.assertEqual((original, {}), history.condense(original))

    def test_compaction_keeps_the_full_records_retrievable_and_hash_pinned(self):
        original = packet("astra_review")
        with tempfile.TemporaryDirectory() as tmp:
            state_path = Path(tmp) / "state.json"
            small, moved = context.compact(original, state_path)
            self.assertEqual(["brief_feedback", "saved_answers"], sorted(moved))
            artifact = small["context_artifact"]
            saved = json.loads(Path(artifact["path"]).read_text())
            self.assertEqual([original["brief_feedback"][0]], saved["brief_feedback"])
            self.assertEqual({"Q1": original["saved_answers"]["Q1"]}, saved["saved_answers"])
            self.assertEqual({"total": 1}, artifact["fields"]["saved_answers"])
            self.assertEqual(small, context.compact(original, state_path)[0])

    def test_a_short_settled_history_never_grows_the_final_compacted_packet(self):
        # The context_artifact index costs more than a small settled history saves.
        # The final packet, index included, must stay exactly what a run without
        # settled-history shortening would produce.
        for stage in history.STAGES:
            with self.subTest(stage=stage), tempfile.TemporaryDirectory() as tmp:
                original = packet(stage)
                original["brief_feedback"] = [{"kind": "brief_feedback", "id": "f1", "actor": "user_cli",
                                               "at": BEFORE, "text": "x" * 800}]
                original["saved_answers"] = {}
                small, moved = context.compact(original, Path(tmp) / "state.json")
                self.assertLessEqual(len(json.dumps(small)), len(json.dumps(original)))
                self.assertEqual([], moved)
                self.assertNotIn("context_artifact", small)
                self.assertEqual(original["brief_feedback"], small["brief_feedback"])


if __name__ == "__main__":
    unittest.main()
