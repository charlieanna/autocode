"""Every flag of the main parser is classified as a decision or a start flag, exactly once."""

import unittest

import autocode
import autocode_args
import autocode_decision_flags as flags


def parser_flags() -> set[str]:
    parser = autocode_args.build_parser(None, autocode.opencode.DEFAULT_MODELS)
    return {option for action in parser._actions for option in action.option_strings if option.startswith("--")}


class DecisionFlagTests(unittest.TestCase):
    def test_every_parser_flag_is_classified_exactly_once(self):
        classified = set(flags.DECISION_FLAGS) | set(flags.START_FLAGS)
        unclassified = sorted(parser_flags() - classified)
        self.assertEqual(
            [],
            unclassified,
            "classify these flags in tools/autocode_decision_flags.py: a decision the operator owns goes in "
            "DECISION_FLAGS, setup/routing/budget goes in START_FLAGS",
        )
        self.assertEqual([], sorted(classified - parser_flags()), "classified flags the parser no longer accepts")
        both = set(flags.DECISION_FLAGS) & set(flags.START_FLAGS)
        self.assertEqual(set(), both)
        for name in ("DECISION_FLAGS", "START_FLAGS"):
            with self.subTest(name=name):
                values = getattr(flags, name)
                self.assertEqual(len(values), len(set(values)), "a flag is listed twice")

    def test_policy_flags_are_decisions_and_budgets_are_not(self):
        for flag in ("--skip-requirements", "--skip-to-build", "--allow-uncontained-tools", "--accept-source-edit"):
            self.assertIn(flag, flags.DECISION_FLAGS)
        self.assertIn("--planning-review-call-limit", flags.DECISION_FLAGS)
        for flag in ("--max-seconds", "--unlimited-iterations", "--no-progress-limit", "--terra-model"):
            self.assertIn(flag, flags.START_FLAGS)


if __name__ == "__main__":
    unittest.main()
