"""Each saved person event is said once in a planning stage's handoff."""

import copy
import json
import unittest

import autocode_packet_events as packet_events

REVIEW = {"id": "requirements-review-1", "question": "Here's what I understand. " + "Outcome text. " * 200}
FEEDBACK = {
    "kind": "brief_feedback",
    "id": "feedback-1",
    "actor": "user_cli",
    "at": "2026-11-05T10:27:44Z",
    "contract_token": "",
    "assistant_reply": "Earlier reply. " * 50,
    "text": "A web app for authors.",
}
GATE = {
    "kind": "gate_message",
    "id": "gate-message-1",
    "actor": "user_cli",
    "gate_kind": "requirements",
    "gate_token": "requirements-review-1",
    "intent": "confirm",
    "text": "yes",
    "questions": [REVIEW],
    "reply_after": "You approved it.",
}
ANSWER = {
    "kind": "answer",
    "question_id": "requirements-review-1",
    "actor": "user_cli",
    "question": REVIEW,
    "text": "plan",
}


class ReferenceRepeatsTests(unittest.TestCase):
    def test_exact_copies_keep_their_identity_and_point_to_the_full_record(self):
        # As in the live run of 2026-11-05: three requirements replies and a "yes" to the understanding.
        events = [FEEDBACK, GATE, ANSWER]
        shown = packet_events.reference_repeats(events, [FEEDBACK], {"requirements-review-1": ANSWER})
        self.assertEqual(
            [
                {
                    "kind": "brief_feedback",
                    "id": "feedback-1",
                    "actor": "user_cli",
                    "at": "2026-11-05T10:27:44Z",
                    "contract_token": "",
                    "same_as": "brief_feedback",
                },
                {
                    **{key: value for key, value in GATE.items() if key != "questions"},
                    "questions_same_as": "saved_answers",
                },
                {
                    "kind": "answer",
                    "question_id": "requirements-review-1",
                    "actor": "user_cli",
                    "same_as": "saved_answers.requirements-review-1",
                },
            ],
            shown,
        )
        self.assertLess(len(json.dumps(shown)) * 5, len(json.dumps(events)))

    def test_anything_that_is_not_an_exact_copy_is_kept_whole(self):
        changed = {**FEEDBACK, "text": "A different reply."}
        other_gate = {**GATE, "questions": [{"id": "Q9", "question": "Not saved"}]}
        other_answer = {**ANSWER, "text": "something else"}
        events = [changed, other_gate, other_answer, {"kind": "technical_decision", "id": "t1"}, "odd"]
        before = copy.deepcopy(events)
        shown = packet_events.reference_repeats(events, [FEEDBACK], {"requirements-review-1": ANSWER})
        self.assertEqual(before, shown)
        self.assertEqual(before, events)  # the saved events are not changed
        self.assertEqual(before[:4], packet_events.reference_repeats(before[:4], None, None))


if __name__ == "__main__":
    unittest.main()
