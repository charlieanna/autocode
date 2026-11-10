"""A format-only report repair runs below the stage's saved reasoning level (#2868). Pure functions."""

import unittest

GLM = "zai-coding-plan/glm-5.3"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"


def state(levels=None, **settings):
    return {
        "settings": {
            "roles": {"planner": {"model": GLM, "reasoning_effort": "high"}},
            "declared_variants": {GLM: levels if levels is not None else ["high", "low", "max"]},
            **settings,
        }
    }


def key(choice):
    return choice["level"], choice["ceiling"], choice["lowered"], choice["reason"]


class OutputLimitRecoveryTests(unittest.TestCase):
    """A format repair that hit the output limit recovers automatically (#2836)."""

    def test_format_repair_output_limit_does_not_ask_the_person(self):
        from autocode_resolver_runtime import _is_format_repair

        state = {"active_stage": {"report_only": True}, "stages": []}
        self.assertTrue(_is_format_repair(state), "an active format repair is recognized")

        state = {"active_stage": {}, "stages": [{"stage": "requirements_gather_report_repair", "report_only": True}]}
        self.assertTrue(_is_format_repair(state), "a saved format repair is recognized")

        state = {"active_stage": {}, "stages": [{"stage": "builder", "report_only": False}]}
        self.assertFalse(_is_format_repair(state), "a Builder stage is not a format repair")

        state = {"active_stage": {}, "stages": []}
        self.assertFalse(_is_format_repair(state), "no stage is not a format repair")


class GroundTests(unittest.TestCase):
    """The deterministic quote fix for dropped-markup mismatches (#2835)."""

    def test_a_quote_that_dropped_backticks_resolves_to_the_source_span(self):
        from autocode_requirement_quotes import ground

        sources = ["The brief says `source_ref.kind == 'reference_solution'`; next sentence."]
        quote = "`source_ref.kind == 'reference_solution';"
        result = ground(quote, sources)
        self.assertIsNotNone(result, "the source span is found despite dropped markup")
        self.assertIn("source_ref.kind", result)
        self.assertIn("reference_solution", result)

    def test_a_quote_with_exact_match_still_works(self):
        from autocode_requirement_quotes import ground

        sources = ["The brief says `source_ref.kind == 'reference_solution'`; next sentence."]
        result = ground("`source_ref.kind == 'reference_solution'`", sources)
        self.assertEqual("`source_ref.kind == 'reference_solution'`", result)

    def test_an_unmatched_quote_still_returns_none(self):
        from autocode_requirement_quotes import ground

        sources = ["Something entirely different."]
        self.assertIsNone(ground("No such text anywhere", sources))

    def test_a_quote_that_dropped_quotes_resolves(self):
        from autocode_requirement_quotes import ground

        sources = ["The task says 'use Python standard library' and nothing else."]
        result = ground("use Python standard library", sources)
        self.assertIsNotNone(result)
        self.assertIn("use Python standard library", result)
