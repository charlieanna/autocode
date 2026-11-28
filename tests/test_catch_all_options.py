"""A Requirements question's catch-all option is removed by the runner, never sent to the person (#4223)."""

import unittest

import autocode_catch_all_options as catch_all


class CatchAllOptionTests(unittest.TestCase):
    def test_an_option_that_only_invites_free_text_is_a_catch_all(self):
        for option in (
            "Something else, which you describe in your own words.",  # run 20261127-035250-add-dark-mode
            "Something else",
            "Anything else (tell us what you want)",
            "Other (please specify)",
            "Other: describe it below",
            "None of these, in your own words",
            "A different approach, which you explain",
            "**Something else**: say what you mean",
        ):
            with self.subTest(option=option):
                self.assertTrue(catch_all.is_catch_all(option))

    def test_an_option_that_names_a_reading_stays(self):
        for option in (
            "Other users keep their own lists",
            "None of these files change: the command only reads them",
            "Something else on the screen changes colour: the header",
            "A different approach: a web page with a dark theme",
            "Other",  # a category among Work, Personal, Other
            "None of the above",  # "change nothing" can be the answer
            "Colored list output for dark terminals",
            "",
            None,
        ):
            with self.subTest(option=option):
                self.assertFalse(catch_all.is_catch_all(option))

    def test_the_catch_all_is_removed_and_recorded_and_other_questions_are_untouched(self):
        padded = {
            "id": "Q1",
            "question": "What should 'dark mode' mean for this todo tool?",
            "options": [
                "Colored list output for dark terminals",
                "A per-run switch: python todo.py list --dark",
                "Something else, which you describe in your own words.",
            ],
            "proposed_default": "",
        }
        plain = {"id": "Q2", "question": "What should done 99 print?", "options": ["An error", "Nothing"]}
        kept, dropped = catch_all.without_catch_all([padded, plain])
        self.assertEqual(padded["options"][:2], kept[0]["options"])
        self.assertIs(plain, kept[1])
        self.assertEqual([{"question": "Q1", "option": padded["options"][2]}], dropped)
        self.assertEqual(3, len(padded["options"]), "the report's own question is not changed in place")

    def test_a_default_naming_the_catch_all_is_cleared_and_a_question_left_with_none_takes_free_text(self):
        only = {"id": "Q1", "question": "Which?", "options": ["Something else"], "proposed_default": "Something else"}
        kept, dropped = catch_all.without_catch_all([only])
        self.assertEqual(([], ""), (kept[0]["options"], kept[0]["proposed_default"]))
        self.assertEqual(1, len(dropped))
        self.assertEqual(([], []), catch_all.without_catch_all([]))


if __name__ == "__main__":
    unittest.main()
