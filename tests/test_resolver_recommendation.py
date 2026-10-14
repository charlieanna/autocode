"""Resolver recommendations can change wording, never acceptance authority."""

import copy
import unittest

import autocode_resolver_recommendation as recommendation
import autocode_util as util


def body():
    return {
        "intended_outcome": "Print the agreed total row",
        "acceptance_criteria": [
            {
                "id": "AC1",
                "criterion": "Print a total row",
                "verification_method": "python -m unittest test_total",
                "human_review": True,
                "sources": [{"id": "source-1", "quote": "Show the total"}],
            },
            {
                "id": "AC3",
                "criterion": "Preserve column alignment",
                "verification_method": "Compare output bytes",
                "human_review": False,
                "source_quote": "Keep the columns aligned",
            },
        ],
        "permission_boundaries": ["Only edit report.py"],
        "constraints": ["Standard library only"],
        "milestones": [{"id": "M1", "acceptance_criteria": ["AC1", "AC3"]}],
        "source_manifest": {"hash": "original", "records": ["source-1"]},
    }


def proposed(cid="AC1", criterion="Print the final total row"):
    return {"acceptance_criteria": [{"id": cid, "criterion": criterion}]}


class ResolverRecommendationTests(unittest.TestCase):
    def test_only_criterion_text_changes_and_all_nested_inputs_remain_independent(self):
        original, patch = body(), proposed()
        before, proposal_before = copy.deepcopy(original), copy.deepcopy(patch)
        revised = recommendation.compose(original, patch)
        expected = copy.deepcopy(original)
        expected["acceptance_criteria"][0]["criterion"] = "Print the final total row"
        self.assertEqual(expected, revised)
        revised["acceptance_criteria"][0]["sources"][0]["quote"] = "changed"
        revised["permission_boundaries"].append("New permission")
        revised["source_manifest"]["records"].append("changed")
        self.assertEqual(before, original)
        self.assertEqual(proposal_before, patch)
        self.assertEqual("python -m unittest test_total", revised["acceptance_criteria"][0]["verification_method"])
        self.assertTrue(revised["acceptance_criteria"][0]["human_review"])

    def test_exact_output_correction_preserves_ten_to_twelve_embedded_spaces(self):
        original = body()
        original["acceptance_criteria"][0]["criterion"] = "Print exactly 'total" + " " * 10 + "42'."
        corrected = "Print exactly 'total" + " " * 12 + "42'."
        revised = recommendation.compose(original, proposed(criterion=corrected))
        self.assertEqual(corrected, revised["acceptance_criteria"][0]["criterion"])
        self.assertIn("total" + " " * 10 + "42", original["acceptance_criteria"][0]["criterion"])

    def test_unknown_and_duplicate_ids_are_rejected_without_partial_changes(self):
        patches = [
            proposed("unknown"),
            {"acceptance_criteria": [proposed()["acceptance_criteria"][0], proposed()["acceptance_criteria"][0]]},
            {"acceptance_criteria": [proposed()["acceptance_criteria"][0], {"id": "unknown", "criterion": "Changed"}]},
        ]
        for patch in patches:
            original = body()
            before = copy.deepcopy(original)
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                recommendation.compose(original, patch)
            self.assertEqual(before, original)

    def test_permissions_sources_human_review_and_proof_changes_cannot_hide_in_a_patch(self):
        for name, value in (
            ("permission_boundaries", []),
            ("sources", []),
            ("human_review", False),
            ("verification_method", "Skip tests"),
            ("id_alias", "AC3"),
        ):
            for top_level in (True, False):
                patch = proposed()
                target = patch if top_level else patch["acceptance_criteria"][0]
                target[name] = value
                with self.subTest(name=name, top_level=top_level), self.assertRaises(ValueError):
                    recommendation.compose(body(), patch)

    def test_empty_malformed_and_no_op_proposals_fail_closed(self):
        patches = [
            None,
            [],
            {},
            {"acceptance_criteria": []},
            {"acceptance_criteria": {}},
            {"acceptance_criteria": [None]},
            {"acceptance_criteria": [{"id": "AC1"}]},
            proposed(criterion="Print a total row"),
            proposed(criterion="  Print a total row\n"),
            proposed(criterion=""),
            proposed(criterion=" \n\t"),
            proposed(criterion=True),
            proposed(criterion={"text": "Changed"}),
            proposed(cid=""),
            proposed(cid=1),
            proposed(cid=" AC1 "),
        ]
        for patch in patches:
            with self.subTest(patch=patch), self.assertRaises(ValueError):
                recommendation.compose(body(), patch)

    def test_unusable_original_checklists_cannot_be_implicitly_repaired(self):
        originals = [None, {}, {"acceptance_criteria": []}, {"acceptance_criteria": ["AC1"]}]
        duplicate = body()
        duplicate["acceptance_criteria"].append(copy.deepcopy(duplicate["acceptance_criteria"][0]))
        originals.append(duplicate)
        for text in (None, "", 7):
            invalid = body()
            invalid["acceptance_criteria"][0]["criterion"] = text
            originals.append(invalid)
        for original in originals:
            with self.subTest(original=original), self.assertRaises(ValueError):
                recommendation.compose(original, proposed())

    def test_limits_allow_the_boundary_and_reject_oversized_recommendations(self):
        original = body()
        original["acceptance_criteria"][0]["id"] = "a" * recommendation.MAX_ID_CHARS
        text = "b" * recommendation.MAX_CRITERION_CHARS
        result = recommendation.compose(original, proposed("a" * recommendation.MAX_ID_CHARS, text))
        self.assertEqual(text, result["acceptance_criteria"][0]["criterion"])
        for patch in (
            proposed("a" * (recommendation.MAX_ID_CHARS + 1)),
            proposed(criterion="b" * (recommendation.MAX_CRITERION_CHARS + 1)),
            {
                "acceptance_criteria": [
                    {"id": str(i), "criterion": "new"} for i in range(recommendation.MAX_CORRECTIONS + 1)
                ]
            },
        ):
            with self.subTest(patch_size=len(str(patch))), self.assertRaises(ValueError):
                recommendation.compose(body(), patch)

    def test_only_the_explicit_acceptance_phrase_is_recognized(self):
        for text in (recommendation.ACCEPTANCE, " accept recommended revision ", "\nACCEPT RECOMMENDED REVISION\t"):
            with self.subTest(text=text):
                self.assertTrue(recommendation.accepts(text))
        for text in (
            None,
            True,
            [recommendation.ACCEPTANCE],
            "yes",
            "approve",
            "accept",
            "",
            "Do not Accept recommended revision",
            "Accept recommended revision please",
            "Accept recommended revision.",
            "Accept  recommended revision",
            "Accept\nrecommended revision",
        ):
            with self.subTest(text=text):
                self.assertFalse(recommendation.accepts(text))

    def test_nullable_schema_is_strict_for_nonnull_recommendations(self):
        for value in (None, proposed()):
            util.validate_schema(value, recommendation.SCHEMA)
        for value in ({}, {"acceptance_criteria": []}, {**proposed(), "permission_boundaries": []}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                util.validate_schema(value, recommendation.SCHEMA)
        self.assertEqual(
            {"id", "criterion"}, set(recommendation.SCHEMA["properties"]["acceptance_criteria"]["items"]["properties"])
        )


if __name__ == "__main__":
    unittest.main()
