"""The assumption ledger: who must confirm each Requirements or Planner assumption (#3603)."""

import unittest
from unittest import mock

import autocode_assumption_ledger as ledger
import autocode_code_citations as code_citations


def row(aid="A1", **extra):
    return {
        "id": aid,
        "text": "Errors exit with status 2",
        "kind": "inferable",
        "category": "behavior",
        "convention_ref": "cli.py:10",
        "rationale": "The existing CLI does",
        "supports": [],
        **extra,
    }


def read_only(*paths):
    """A verify that accepts citations of exactly these files."""
    return lambda ref: None if ref.split(":", 1)[0] in paths else f"{ref} was never read"


class UnconfirmedTests(unittest.TestCase):
    def test_behavior_is_never_inferred_from_code_whatever_its_kind(self):
        for kind in ("inferable", "discoverable", "decision"):
            with self.subTest(kind=kind):
                rows = ledger.unconfirmed([row(kind=kind)], confirmed_ids=[], verify=read_only("cli.py"))
                self.assertEqual(["A1"], [r["id"] for r in rows])
                self.assertIn("behavior choice is the person's", rows[0]["reason"])

    def test_every_person_category_needs_the_person(self):
        for category in (
            "behavior",
            "other",
            "cost",
            "quota",
            "permission",
            "external_side_effect",
            "requested_outcome",
        ):
            with self.subTest(category=category):
                rows = ledger.unconfirmed([row(category=category)], confirmed_ids=(), verify=read_only("cli.py"))
                self.assertEqual(["A1"], [r["id"] for r in rows])

    def test_an_unknown_category_is_the_persons(self):
        rows = ledger.unconfirmed([row(category="invented")], confirmed_ids=(), verify=read_only("cli.py"))
        self.assertIn("requested_outcome", rows[0]["reason"])

    def test_the_persons_confirmation_or_saved_answer_settles_a_behavior(self):
        self.assertEqual([], ledger.unconfirmed([row()], confirmed_ids={"A1"}, verify=read_only()))
        cited = row(convention_ref="Q1")
        self.assertEqual(
            [], ledger.unconfirmed([cited], confirmed_ids=(), verify=read_only(), cites_person=lambda ref: ref == "Q1")
        )
        self.assertEqual(
            ["A1"],
            [
                r["id"]
                for r in ledger.unconfirmed([cited], confirmed_ids=(), verify=read_only(), cites_person=lambda r: False)
            ],
        )

    def test_a_convention_stands_only_on_a_citation_that_was_read(self):
        technical = row(category="technical", text="Tests use unittest", convention_ref="tests/test_cli.py:1")
        self.assertEqual([], ledger.unconfirmed([technical], confirmed_ids=(), verify=read_only("tests/test_cli.py")))
        rows = ledger.unconfirmed([technical], confirmed_ids=(), verify=read_only("cli.py"))
        self.assertEqual(["A1"], [r["id"] for r in rows])
        self.assertIn("tests/test_cli.py:1 was never read", rows[0]["reason"])
        # The person's own answer settles it too, so a choice they made is not confirmed twice.
        cited = {**technical, "convention_ref": "Q1"}
        self.assertEqual(
            [], ledger.unconfirmed([cited], confirmed_ids=(), verify=read_only(), cites_person=lambda r: True)
        )

    def test_the_persons_confirmation_also_settles_a_convention(self):
        technical = row(category="technical", convention_ref="")
        self.assertEqual([], ledger.unconfirmed([technical], confirmed_ids=["A1"], verify=read_only()))

    def test_a_legacy_string_needs_the_person_and_rows_keep_their_order(self):
        rows = ledger.unconfirmed(
            ["Use a CLI", row("A2"), row("A3", category="technical", convention_ref="x.py:1")],
            confirmed_ids=(None, ""),
            verify=read_only("x.py"),
        )
        self.assertEqual([None, "A2"], [r["id"] for r in rows])
        self.assertTrue(rows[0]["legacy"])
        self.assertEqual("Use a CLI", rows[0]["text"])

    def test_returns_normalized_copies(self):
        raw = row()
        result = ledger.unconfirmed([raw], confirmed_ids=(), verify=read_only())[0]
        self.assertEqual(
            {"id", "text", "kind", "category", "convention_ref", "rationale", "supports", "legacy", "reason"},
            set(result),
        )
        self.assertNotIn("reason", raw)


class BindingTests(unittest.TestCase):
    def test_verifier_binds_the_one_citation_rule_to_the_stage(self):
        state = {"workspace": "/w"}
        with mock.patch.object(code_citations, "verify_citation", create=True, return_value="nope") as verify:
            self.assertEqual("nope", ledger.verifier(state, ("draft_plan",))("x.py:1"))
        verify.assert_called_once_with(state, ("draft_plan",), "x.py:1", field="convention_ref")

    def test_cites_person_accepts_saved_answers_and_authenticated_feedback_only(self):
        feedback = {"id": "feedback-1", "text": "Exit 2"}
        state = {"answers": {"Q1": {"text": "yes"}}, "brief_feedback": [feedback], "user_events": [feedback]}
        cites = ledger.cites_person(state)
        self.assertTrue(cites("Q1"))
        self.assertTrue(cites("feedback-1"))
        self.assertFalse(cites("Q9"))
        self.assertFalse(cites("task"))
        state["user_events"] = []
        self.assertFalse(cites("feedback-1"))


if __name__ == "__main__":
    unittest.main()
