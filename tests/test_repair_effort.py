"""A format-only report repair runs below the stage's saved reasoning level (#2868). Pure functions."""

import copy
import unittest

import autocode_repair_effort as repair_effort

GLM = "zai-coding-plan/glm-5.3"
MIMO = "xiaomi-token-plan-sgp/mimo-v2.6-pro"


def state(levels=None, **settings):
    return {
        "settings": {
            "roles": {"glm": {"model": GLM, "reasoning_effort": "high"}},
            "declared_variants": {GLM: levels if levels is not None else ["high", "low", "max"]},
            **settings,
        }
    }


def key(choice):
    return choice["level"], choice["ceiling"], choice["lowered"], choice["reason"]


class GroundTests(unittest.TestCase):
    """The deterministic quote fix for dropped-markup mismatches (#2835)."""

    def test_a_quote_that_dropped_backticks_resolves_to_the_source_span(self):
        from autocode_requirement_quotes import ground

        sources = ["The brief says `source_ref.kind == 'reference_solution'`; next sentence."]
        # The quote dropped one backtick.
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


class DecideTests(unittest.TestCase):
    def test_a_repair_takes_the_highest_offered_level_below_the_configured_one(self):
        # GLM 5.3 offers low, high and max: the live incident's repair runs low, not high.
        self.assertEqual(
            ("low", "high", True, "format_repair"),
            key(repair_effort.decide(state(), "high", model=GLM)),
        )
        self.assertEqual(
            ("medium", "high", True, "format_repair"),
            key(repair_effort.decide(state(["high", "low", "medium"]), "high", model=GLM)),
        )
        self.assertEqual(
            ("high", "max", True, "format_repair"),
            key(repair_effort.decide(state(), "max", model=GLM)),
        )

    def test_a_model_with_no_lower_level_keeps_the_configured_one_and_says_why(self):
        for levels in (["high"], ["high", "max"]):
            choice = repair_effort.decide(state(levels), "high", model=GLM)
            self.assertEqual(("high", "high", False, "no_lower_level"), key(choice))
            self.assertIn(GLM, choice["text"])
        self.assertEqual(
            ("low", "low", False, "no_lower_level"),
            key(repair_effort.decide(state(["low", "high"]), "low", model=GLM)),
        )

    def test_unknown_levels_keep_the_configured_level(self):
        choice = repair_effort.decide(state(declared_variants={}), "high", model=MIMO)
        self.assertEqual(("high", "high", False, "levels_unknown"), key(choice))
        self.assertEqual(
            ("high", "high", False, "levels_unknown"), key(repair_effort.decide(state(), "high", model=None))
        )

    def test_a_probe_that_passed_the_lower_level_counts_beside_the_listing(self):
        settings = state(["high", "max"])["settings"]
        settings["certified_variants"] = {GLM: ["medium"]}
        self.assertEqual(
            ("medium", "high", True, "format_repair"),
            key(repair_effort.decide({"settings": settings}, "high", model=GLM)),
        )

    def test_no_level_or_an_off_scale_level_is_nothing_to_bound(self):
        self.assertIsNone(repair_effort.decide(state(), None, model=GLM))
        self.assertIsNone(repair_effort.decide(state(), "", model=GLM))
        self.assertIsNone(repair_effort.decide(state(), "thinking", model=GLM))

    def test_a_typed_level_is_bounded_too_the_person_never_chose_the_repairs_level(self):
        settings = state()["settings"]
        settings["effort_origins"] = {"glm": "user_explicit"}
        self.assertEqual(
            ("low", "high", True, "format_repair"),
            key(repair_effort.decide({"settings": settings}, "high", model=GLM)),
        )

    def test_the_saved_route_and_listings_are_never_rewritten(self):
        run = state()
        before = copy.deepcopy(run)
        repair_effort.decide(run, "high", model=GLM)
        self.assertEqual(before, run)


if __name__ == "__main__":
    unittest.main()
