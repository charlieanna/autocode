"""Structural requirement boundaries observed in a native program brief (#919)."""

import json
import unittest

from autocode_goals import check_requirement_handoff
from autocode_requirement_cues import cue_sentences


def report(*quotes):
    return {
        "requirements": [{"id": f"R{i}", "text": quote, "source_quote": quote} for i, quote in enumerate(quotes)],
        "ignored_statements": [],
    }


class RequirementBlockTests(unittest.TestCase):
    def test_inherited_instruction_is_separate_from_verification_metadata(self):
        definition = (
            '- B1: Calling banner.render.render("Nova") returns the string ready:Nova. '
            "(verify: python3 -c \"from banner.render import render; assert render('Nova') == 'ready:Nova'\"; "
            "human_review: false)"
        )
        inherited = (
            "Inherited requirements: keep each as an acceptance criterion of your plan with exactly this id (B1)."
        )
        task = definition + "\n\n" + inherited
        self.assertEqual([inherited], cue_sentences(task))
        check_requirement_handoff({"task": task}, report(definition, inherited))
        with self.assertRaisesRegex(ValueError, "Inherited requirements"):
            check_requirement_handoff({"task": task}, report(definition))

    def test_each_adjacent_list_obligation_needs_coverage(self):
        for first, second in (
            ("- You must keep exit codes", "- You must preserve tests"),
            ("* You must keep exit codes", "+ You must preserve tests"),
            ("1. You must keep exit codes", "2) You must preserve tests"),
        ):
            with self.subTest(first=first):
                task = first + "\n" + second
                expected_first = first.removeprefix("1. ")
                self.assertEqual([expected_first, second], cue_sentences(task))
                check_requirement_handoff({"task": task}, report(first, second))
                for quoted in (first, second):
                    with self.assertRaisesRegex(ValueError, "neither quoted nor explicitly ignored"):
                        check_requirement_handoff({"task": task}, report(quoted))

    def test_substantive_plain_heading_is_enforced_separately(self):
        heading = "Workstreams already merged on the integration branch (use their results; do not redo them):"
        item = "- skeleton: Implement skeleton/health.py and independent tests under skeleton/."
        task = heading + "\n" + item
        self.assertEqual([heading], cue_sentences(task))
        with self.assertRaisesRegex(ValueError, "do not redo them"):
            check_requirement_handoff({"task": task}, report(item))
        check_requirement_handoff({"task": task}, report(heading, item))

    def test_wrapped_prose_and_list_continuations_stay_together(self):
        for task in (
            "Builder must preserve:\nall protected tests and their inclusion.",
            "- Builder must preserve\n  all protected tests and their inclusion.",
            "Use only version 3.14\nand the -I flag when invoking Python.",
        ):
            with self.subTest(task=task):
                self.assertEqual([task], cue_sentences(task))
                check_requirement_handoff({"task": task}, report(task))

    def test_blank_paragraphs_need_independent_coverage(self):
        first, second = "You must preserve tests", "You must preserve source"
        task = first + "\r\n \t\r\n" + second
        self.assertEqual([first, second], cue_sentences(task))
        for quoted in (first, second):
            with self.assertRaisesRegex(ValueError, "neither quoted nor explicitly ignored"):
                check_requirement_handoff({"task": task}, report(quoted))

    def test_constraints_do_not_substitute_for_trace_quotes(self):
        boundaries = [
            "Use only Python standard library.",
            "Do not merge branches or commit source; the program controller owns Git integration.",
            "Do not modify, delete, skip, disable, or exclude test_seed_health.py or seed_health.py; "
            "preserve inclusion of protected tests in the whole-project test suite.",
            "Do not start nested models or read credentials and authentication files.",
        ]
        inherited = (
            "Inherited requirements: keep each as an acceptance criterion of your plan with exactly this id (B1)."
        )
        task = "Shared constraints:\n" + "\n".join("- " + text for text in boundaries) + "\n\n" + inherited
        value = {**report(inherited), "constraints": boundaries}
        with self.assertRaises(ValueError) as caught:
            check_requirement_handoff({"task": task}, value)
        missing = json.loads(str(caught.exception).split(": ", 1)[1])
        self.assertEqual(["- " + text for text in boundaries], missing)
        check_requirement_handoff({"task": task}, report(inherited, *boundaries))


if __name__ == "__main__":
    unittest.main()
