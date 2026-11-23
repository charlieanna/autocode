"""Pure binding of an accepted repair's concern-answer claims to its accepted change list (#3908).

The CLI regression (tests.test_repaired_claim_binding) proves the wiring through the real
acceptance path; these cover the mechanism's axes: item kinds, answer kinds, mention fields,
word boundaries, kept rows, idempotent revalidation and unreadable change lists.
"""

import copy
import unittest

import autocode_repair_claim_binding as binding


def change(item):
    return {
        "item": item,
        "change": "reworded",
        "basis": "agent_proposed",
        "answer_id": "",
        "replacement": "replacement",
        "example_correction": None,
    }


def response(concern_id, text, change_text=""):
    return {
        "concern_id": concern_id,
        "response": text,
        "evidence_refs": ["task"],
        "change": change_text,
        "acceptance_test": "check it",
    }


def decision(concern_id, text, rationale=""):
    return {
        "concern_id": concern_id,
        "decision": text,
        "rationale": rationale,
        "acceptance_test": "check it",
        "resolved": True,
    }


def report(changes, *, responses=(), decisions=(), summary="Revision summary"):
    return {
        "summary": summary,
        "contract_changes": list(changes),
        "responses": list(responses),
        "decisions": list(decisions),
    }


class ClaimBindingTests(unittest.TestCase):
    def test_dropped_criterion_row_marks_the_response_and_summary(self):
        original = report([change("AC28"), change("AC7")])
        repaired = report(
            [change("AC7")],
            responses=[response("C6", "This revision re-emitted the complete AC28 change entry")],
            summary="Re-emitted the complete AC28 change entry",
        )
        bound, claims = binding.bind(original, repaired)
        self.assertIn("AC28", bound["responses"][0]["response"])
        self.assertIn("unverified", bound["responses"][0]["response"])
        self.assertIn(binding.MARKER, bound["responses"][0]["response"])
        self.assertIn("unverified", bound["summary"])
        self.assertEqual(
            [
                {"item": "AC28", "concern_id": "C6", "where": ["response"]},
                {"item": "AC28", "concern_id": "", "where": ["summary"]},
            ],
            claims,
        )
        self.assertEqual([change("AC7")], bound["contract_changes"], "the accepted list itself is untouched")

    def test_dropped_protected_text_row_is_bound_the_same_way(self):
        text = "M1 adds only greet.py and test_greet.py"
        original = report([change(text)])
        repaired = report([], responses=[response("R1", f"Reworded the constraint: {text}")])
        bound, claims = binding.bind(original, repaired)
        self.assertIn("unverified", bound["responses"][0]["response"])
        self.assertEqual([text], [row["item"] for row in claims])

    def test_finalize_decisions_are_bound_the_same_way(self):
        original = report([change("AC28")])
        repaired = report(
            [],
            decisions=[decision("C6", "The complete AC28 change entry was re-emitted", "AC28 is whole")],
        )
        bound, claims = binding.bind(original, repaired)
        self.assertIn("unverified", bound["decisions"][0]["decision"])
        self.assertEqual(
            [{"item": "AC28", "concern_id": "C6", "where": ["decision", "rationale"]}],
            claims,
        )

    def test_a_mention_only_in_the_change_field_still_marks_the_row(self):
        original = report([change("AC28")])
        repaired = report(
            [],
            responses=[response("C6", "See the declared change", "Re-emitted the complete AC28 change entry")],
        )
        bound, claims = binding.bind(original, repaired)
        self.assertIn("unverified", bound["responses"][0]["response"])
        self.assertEqual([{"item": "AC28", "concern_id": "C6", "where": ["change"]}], claims)

    def test_item_ids_match_on_word_boundaries(self):
        original = report([change("AC1")])
        repaired = report([], responses=[response("C6", "AC12 and AC10 stay as written")])
        bound, claims = binding.bind(original, repaired)
        self.assertIs(bound, repaired, "AC1 inside AC12/AC10 is not a mention of AC1")
        self.assertEqual([], claims)

    def test_kept_rows_leave_the_report_untouched(self):
        original = report([change("AC28")])
        repaired = report([change("AC28")], responses=[response("C6", "Re-emitted AC28")])
        bound, claims = binding.bind(original, repaired)
        self.assertIs(bound, repaired)
        self.assertEqual([], claims)

    def test_answers_that_never_mention_a_dropped_row_stay_verbatim(self):
        original = report([change("AC28")])
        repaired = report([], responses=[response("C6", "The greeting contract is unchanged")])
        bound, claims = binding.bind(original, repaired)
        self.assertIs(bound, repaired)
        self.assertEqual([], claims)

    def test_binding_is_idempotent_for_revalidation(self):
        original = report([change("AC28")])
        repaired = report(
            [],
            responses=[response("C6", "Re-emitted the complete AC28 change entry")],
            summary="Re-emitted AC28",
        )
        once, claims = binding.bind(original, repaired)
        self.assertTrue(claims)
        twice, again = binding.bind(original, once)
        self.assertIs(twice, once, "revalidation reads the saved annotated bytes and changes nothing")
        self.assertEqual([], again)

    def test_multiple_dropped_items_share_one_note_per_row(self):
        original = report([change("AC28"), change("AC7"), change("AC14")])
        repaired = report(
            [change("AC14")],
            responses=[response("C6", "Re-emitted AC28 and AC7")],
        )
        bound, claims = binding.bind(original, repaired)
        response_text = bound["responses"][0]["response"]
        self.assertEqual(1, response_text.count(binding.MARKER))
        self.assertIn("AC28, AC7", response_text)
        self.assertEqual({"AC28", "AC7"}, {row["item"] for row in claims})

    def test_unreadable_change_lists_bind_nothing(self):
        repaired = report([], responses=[response("C6", "Re-emitted AC28")])
        for original in (None, {}, {"contract_changes": "not a list"}, report([{"item": 28}])):
            with self.subTest(original=original):
                bound, claims = binding.bind(original, repaired)
                self.assertIs(bound, repaired)
                self.assertEqual([], claims)
        bound, claims = binding.bind(report([change("AC28")]), {"summary": "Re-emitted AC28"})
        self.assertEqual([], claims, "a repair with no readable change list is left to the schema gate")

    def test_input_reports_are_not_mutated(self):
        original = report([change("AC28")])
        repaired = report([], responses=[response("C6", "Re-emitted AC28")], summary="Re-emitted AC28")
        before = copy.deepcopy(repaired)
        binding.bind(original, repaired)
        self.assertEqual(before, repaired)

    def test_a_declined_suggestion_keeps_its_words_and_gets_the_factual_note(self):
        original = report([change("AC28")])
        repaired = report([], responses=[response("C6", "AC28 stays as written; the suggestion was declined")])
        bound, claims = binding.bind(original, repaired)
        self.assertTrue(bound["responses"][0]["response"].startswith("AC28 stays as written"))
        self.assertIn("unverified", bound["responses"][0]["response"])
        self.assertEqual(["AC28"], [row["item"] for row in claims])

    def test_a_summary_only_report_is_bound(self):
        original = report([change("AC28")])
        repaired = {"summary": "Re-emitted the complete AC28 change entry", "contract_changes": []}
        bound, claims = binding.bind(original, repaired)
        self.assertIn("unverified", bound["summary"])
        self.assertEqual([{"item": "AC28", "concern_id": "", "where": ["summary"]}], claims)


if __name__ == "__main__":
    unittest.main()
