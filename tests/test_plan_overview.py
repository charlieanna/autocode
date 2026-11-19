"""The same concise canonical plan leads files and the public editor projection (#3833)."""

import copy
import unittest

import autocode_plan_file as plan_file
import autocode_run_view as run_view
import autocode_util as util

from tests.test_plan_file import body


class PlanOverviewTests(unittest.TestCase):
    def test_scope_counts_use_singular_and_plural_without_predicting_effort(self):
        for count, expected in (
            (0, "0 stages; 0 acceptance criteria"),
            (1, "1 stage; 1 acceptance criterion"),
            (2, "2 stages; 2 acceptance criteria"),
        ):
            with self.subTest(count=count):
                original = {"milestones": [{"objective": "Work"}] * count, "acceptance_criteria": [{}] * count}
                self.assertIn(expected, plan_file.summary_markdown(original, revision=1, status="not approved"))

    def test_format_two_keeps_its_original_bytes_after_scope_wording_improves(self):
        import hashlib

        original = body()
        exported = plan_file.document(
            {
                "autocode_plan": 2,
                "goal_hash": util.digest(original),
                "revision": 3,
                "approval_status": "approved",
                "contract": original,
            }
        )
        self.assertEqual(
            "acd471aad48f0fbefa4b1463e9ef603fb73852d430c8f4a5a91743a08314a54d",
            hashlib.sha256(exported.encode()).hexdigest(),
        )
        self.assertEqual(2, plan_file.read(exported).format)
        self.assertEqual(exported, plan_file.rendered(exported))
        single = {"milestones": [{"objective": "Work"}], "acceptance_criteria": [{}]}
        legacy = plan_file.document(
            {
                "autocode_plan": 2,
                "goal_hash": util.digest(single),
                "revision": 1,
                "approval_status": "not approved",
                "contract": single,
            }
        )
        self.assertIn("1 stages; 1 acceptance criteria", legacy)
        self.assertEqual(legacy, plan_file.rendered(legacy))

    def test_export_leads_with_the_approach_before_contract_and_acceptance_evidence(self):
        for count in (1, 100):
            original = body()
            original["acceptance_criteria"] *= count
            exported = plan_file.export(original, revision=3, approved=False)
            self.assertTrue(exported.startswith("# Plan\n"))
            lead, _ = exported.split("<details>", 1)
            self.assertIn("Compute the window in store time", lead)
            self.assertIn("Refund rules", lead)
            self.assertIn("not approved", lead)
            self.assertIn("not estimated", lead)
            self.assertNotIn("source_trace", lead)
            self.assertNotIn("test_window_ends_after", lead)
            self.assertNotIn("--render", lead)
            parsed = plan_file.read(exported)
            self.assertEqual(original, parsed.body)
            self.assertEqual(util.digest(original), parsed.goal_hash)
            self.assertIsNone(parsed.stale_line)

    def test_overview_is_bounded_literal_redacted_and_does_not_invent_estimates(self):
        original = body()
        original["technical_approach"] = ["Keep tests/__init__.py <literal> " + "large " * 300] * 30
        original["milestones"] *= 30
        original["scope_exclusions"] *= 30
        original["accepted_assumptions"] *= 30
        original["open_blocking_questions"] = [{"question": "Which region?"}] * 30
        original["intended_outcome"] = "Use ghp_" + "a1B2" * 9
        before = copy.deepcopy(original)
        summary = plan_file.summary_markdown(original, revision=2, status="not approved")
        self.assertLess(len(summary), 3800)
        self.assertIn(r"tests/\_\_init\_\_\.py", summary)
        self.assertIn(r"\<literal\>", summary)
        self.assertIn("more", summary)
        self.assertIn("Which region?", summary)
        self.assertIn("not estimated", summary)
        self.assertNotIn("ghp_", summary)
        self.assertNotIn("$0", summary)
        self.assertEqual(before, original)

    def test_missing_sections_and_no_open_questions_are_explicit(self):
        summary = plan_file.summary_markdown({}, revision=1, status="not approved")
        self.assertIn("No technical approach recorded", summary)
        self.assertIn("No work stages recorded", summary)
        self.assertIn("No blocking questions recorded", summary)
        self.assertIn("not estimated", summary)

    def test_summary_and_full_review_share_the_exact_sealed_public_identity(self):
        original = body()
        contract = {"task_id": "t", "revision": 2, "body": original}
        contract["hash"] = util.digest(contract)
        token = "r2:" + contract["hash"]
        state = {"status": "AWAITING_GOAL_APPROVAL", "goal_contract": contract, "displayed_goal": token}
        shown = run_view.view(state)["displayed_plan"]
        self.assertEqual(
            plan_file.summary_markdown(original, revision=2, status="not approved"),
            shown["review_summary_markdown"],
        )
        self.assertEqual(plan_file.review_markdown(original), shown["review_markdown"])
        self.assertEqual(token, shown["token"])
        self.assertNotIn(token, shown["review_summary_markdown"])
        state["displayed_goal"] = "r1:stale"
        self.assertNotIn("displayed_plan", run_view.view(state))

    def test_editing_either_generated_overview_or_full_audit_is_stale(self):
        exported = plan_file.export(body(), revision=2, approved=True)
        for before, after in (
            ("## Proposed approach", "## My recommendation"),
            ("Verified by:", "Verified incorrectly by:"),
        ):
            with self.subTest(before=before):
                self.assertTrue(before in exported)
                edited = exported.replace(before, after, 1)
                plan = plan_file.read(edited)
                line = next(index + 1 for index, text in enumerate(edited.split("\n")) if after in text)
                self.assertEqual(line, plan.stale_line)
                self.assertEqual(exported, plan_file.rendered(edited))

    def test_contract_edits_revoke_displayed_approval_and_render_both_reading_paths(self):
        exported = plan_file.export(body(), revision=2, approved=True)
        edited = exported.replace('"Currency conversion"', '"Currency conversion and taxes"', 1)
        self.assertIsNotNone(plan_file.read(edited).stale_line)
        rendered = plan_file.rendered(edited)
        parsed = plan_file.read(rendered)
        self.assertEqual(plan_file.FORMAT, parsed.format)
        self.assertIsNone(parsed.stale_line)
        self.assertNotEqual(parsed.goal_hash, parsed.exported_hash)
        lead = rendered.split("<details>", 1)[0]
        self.assertIn("not approved", lead)
        self.assertIn("approval does not apply", lead)
        self.assertIn("Currency conversion and taxes", lead)
        self.assertEqual(
            2,
            rendered.count("- Currency conversion and taxes")
            + rendered.count("- Out of scope: Currency conversion and taxes"),
        )

    def test_contract_strings_cannot_close_the_json_fence_or_inject_envelope_markers(self):
        original = body()
        original["technical_approach"] = ["Keep ```json\n</details>\n<!-- autocode-plan-contract -->\n---\n~~~ literal"]
        exported = plan_file.export(original, revision=1, approved=False)
        self.assertIn("````json\n{", exported)
        self.assertEqual(1, exported.count("\n<!-- autocode-plan-contract -->\n"))
        parsed = plan_file.read(exported)
        self.assertEqual(original, parsed.body)
        self.assertIsNone(parsed.stale_line)
        self.assertEqual(exported, plan_file.rendered(exported))

    def test_format_two_rejects_malformed_or_ambiguous_machine_fields(self):
        exported = plan_file.export(body(), revision=1, approved=False)
        for before, after, reason in (
            ('"revision": 1,', '"revision": 1, "revision": 2,', "more than once"),
            ('"revision": 1,', '"revision": NaN,', "not a JSON number"),
            (f'"autocode_plan": {plan_file.FORMAT},', '"autocode_plan": 99,', "reads plan formats"),
            ('"contract": {', '"contract": [', "not valid JSON"),
            ("````json", "````yaml", "fenced JSON"),
            ("\n````\n", "\nmissing-fence\n", "no closing fence"),
        ):
            with self.subTest(reason=reason):
                self.assertIn(before, exported)
                with self.assertRaisesRegex(ValueError, reason):
                    plan_file.read(exported.replace(before, after, -1 if before == "\n````\n" else 1))

    def test_reformatting_contract_json_is_not_a_prose_edit(self):
        import json

        exported = plan_file.export(body(), revision=1, approved=False)
        start = exported.index("\n{\n") + 1
        front, length = json.JSONDecoder().raw_decode(exported[start:])
        compact = exported[:start] + json.dumps(front, ensure_ascii=False) + exported[start + length :]
        self.assertIsNone(plan_file.read(compact).stale_line)
        self.assertEqual(exported, plan_file.rendered(compact))

    def test_envelope_must_match_declared_format_and_have_a_json_opener(self):
        exported = plan_file.export(body(), revision=1, approved=False)
        with self.assertRaisesRegex(ValueError, "later formats have an overview"):
            plan_file.read(exported.replace(f'"autocode_plan": {plan_file.FORMAT}', '"autocode_plan": 1', 1))
        legacy = plan_file.document(
            {
                "autocode_plan": 1,
                "goal_hash": util.digest(body()),
                "revision": 1,
                "approval_status": "not approved",
                "contract": body(),
            }
        )
        with self.assertRaisesRegex(ValueError, "later formats have an overview"):
            plan_file.read(legacy.replace('"autocode_plan": 1', '"autocode_plan": 2', 1))
        with self.assertRaisesRegex(ValueError, "fenced JSON"):
            plan_file.read("# Plan\n" + plan_file.CONTRACT_MARKER)

    def test_legacy_flow_supplies_work_stages_when_milestones_are_absent(self):
        summary = plan_file.summary_markdown(
            {"end_to_end_flow": ["Read input", "Write output"]}, revision=1, status="not approved"
        )
        self.assertIn("1. Read input\n2. Write output", summary)
        self.assertIn("2 stages", summary)


if __name__ == "__main__":
    unittest.main()
