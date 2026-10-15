"""A plan file round-trips its contract body exactly, so its goal hash survives export and import (#720)."""

from __future__ import annotations

import copy
import json
import unittest

import autocode_plan_file as plan_file
import autocode_util as util


def body() -> dict:
    """A contract body with every section a plan can carry, and text that is awkward to quote."""
    return {
        "intended_outcome": "Add refunds to the shop package.",
        "intended_user": "Shop staff",
        "task_kind": "bugfix",
        "deliverables": ["shop/refunds.py", "tests/test_refunds.py"],
        "required_behaviors": [
            "`refund.py list` prints `ID AMOUNT [open|done]` one per line",
            "A line that is only a fence:\n---\nstays inside this behavior",
        ],
        "important_failure_cases": ["Disputed orders cannot be refunded"],
        "scope_exclusions": ["Currency conversion"],
        "constraints": ["Python standard library only"],
        "permission_boundaries": ["Edit only shop/ and tests/"],
        "accepted_assumptions": [{"text": "Store time is UTC+2", "basis": "user_answer", "answer_id": "Q1"}],
        "delegated_decisions": [{"text": "Round half up", "basis": "delegated", "answer_id": "Q2"}],
        "acceptance_criteria": [
            {
                "id": "C1",
                "criterion": "The window ends after the 30th store day — not the 30th UTC day",
                "verification_method": "test: test_window_ends_after_the_30th_store_day",
                "human_review": False,
                "sources": [{"source_id": "task:0", "quote": "until 23:59:59 on March 31 in store time"}],
            },
            {
                "id": "C2",
                "criterion": "Existing totals are kept",
                "verification_method": "guard: test_totals_unchanged",
                "human_review": False,
            },
            {
                "id": "C3",
                "criterion": "The receipt reads well",
                "verification_method": "Run `python3 -m shop.receipt` and read ``` the output\nthen compare it",
                "human_review": True,
            },
        ],
        "open_blocking_questions": [],
        "end_to_end_flow": ["Make the change", "Run python3 -m unittest discover -s tests -t ."],
        "technical_approach": ["Compute the window in store time"],
        "milestones": [
            {"id": "M1", "objective": "Refund rules", "acceptance_criteria": ["C1", "C2"], "depends_on": []},
            {
                "id": "M2",
                "objective": "Receipt",
                "acceptance_criteria": ["C3"],
                "depends_on": ["M1"],
                "affected_paths": ["shop/receipt.py"],
            },
        ],
        "initial_task": {
            "objective": "Refund rules",
            "affected_paths": ["shop/refunds.py"],
            "kind": "implement",
            "milestone_id": "M1",
            "requirements": ["Refunds close after 30 store days"],
            "acceptance_criteria": ["C1", "C2"],
            "validation_plan": ["python3 -m unittest discover -s tests -t ."],
        },
        "brief_acceptance": {
            "manifest": {
                "observations": [
                    {
                        "declaration": {
                            "program": "refund.py",
                            "observe_argv": ["list"],
                            "literal": "ID AMOUNT [open|done]",
                        }
                    }
                ]
            },
            "amendments": [],
        },
        # A runner-owned risk declaration id: `risk-<sha256>` is not a credential, so it survives export.
        "risk_acceptance": {"manifest": {"observations": [{"declaration": {"id": "risk-" + "8f" * 32}}]}},
        "source_trace": {"sources": [{"id": "task:0", "sha256": "0" * 64}]},
    }


def text_of(exported: str) -> list[str]:
    """The lines under the front matter."""
    return exported.split("\n---\n", 1)[1].split("\n")


class PlanFileTests(unittest.TestCase):
    def test_export_then_read_gives_back_the_body_and_its_goal_hash(self):
        original = body()
        exported = plan_file.export(copy.deepcopy(original), revision=3, approved=True)
        plan = plan_file.read(exported)
        self.assertEqual(original, plan.body)
        self.assertEqual(util.digest(original), plan.goal_hash)
        self.assertEqual(plan.goal_hash, plan.exported_hash)
        self.assertEqual((3, "approved", None), (plan.revision, plan.approval_status, plan.stale_line))

    def test_the_front_matter_is_json_and_carries_no_approval_token(self):
        contract = {"task_id": "4f1c-task", "revision": 3, "body": body()}
        contract["hash"] = util.digest(contract)
        exported = plan_file.export(contract["body"], revision=3, approved=False)
        front = json.loads(exported.split("\n---\n", 1)[0].removeprefix("---\n"))
        self.assertEqual(list(plan_file.KEYS), list(front))
        self.assertEqual("not approved", front["approval_status"])
        self.assertNotIn(contract["hash"], exported)
        self.assertNotIn(contract["task_id"], exported)

    def test_the_text_shows_what_the_plan_says(self):
        lines = text_of(plan_file.export(body(), revision=1, approved=False))
        for expected in (
            "Verified by: `test: test_window_ends_after_the_30th_store_day`",
            "Verified by: `guard: test_totals_unchanged`",
            "- `refund.py list` prints `ID AMOUNT [open|done]`",
            "- After: M1",
            "- May change: `shop/receipt.py`",
            "A person reviews the result before the run completes.",
            "- Assumed (user_answer, answer Q1): Store time is UTC+2",
            "## Out of scope",
            "Kept exactly as the run holds them: `brief_acceptance`, `risk_acceptance`, `source_trace`.",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, lines)
        # A multi-line verification method is fenced longer than any backtick run inside it.
        start = lines.index("Verified by:")
        self.assertEqual(
            ["", "````", "Run `python3 -m shop.receipt` and read ``` the output"], lines[start + 1 : start + 4]
        )

    def test_prose_edited_without_the_front_matter_is_named_by_line(self):
        exported = plan_file.export(body(), revision=1, approved=False)
        edited = exported.replace("- Currency conversion", "- Currency conversion and taxes")
        line = edited.split("\n").index("- Currency conversion and taxes") + 1
        plan = plan_file.read(edited)
        self.assertEqual(line, plan.stale_line)
        self.assertEqual(util.digest(body()), plan.goal_hash)

    def test_an_edited_front_matter_gets_a_new_goal_hash_and_render_regenerates_the_text(self):
        exported = plan_file.export(body(), revision=2, approved=True)
        edited = exported.replace('"Currency conversion"', '"Currency conversion and taxes"', 1)
        stale = plan_file.read(edited)
        self.assertIsNotNone(stale.stale_line)
        self.assertNotEqual(stale.exported_hash, stale.goal_hash)
        regenerated = plan_file.rendered(edited)
        plan = plan_file.read(regenerated)
        self.assertIsNone(plan.stale_line)
        self.assertEqual(stale.exported_hash, plan.exported_hash)
        self.assertEqual(["Currency conversion and taxes"], plan.body["scope_exclusions"])
        self.assertIn(
            f"; plan revision 2 (approved) was exported with goal hash `{plan.exported_hash}`, and this plan differs",
            regenerated,
        )
        self.assertIn("- Currency conversion and taxes", text_of(regenerated))

    def test_a_hand_edited_value_of_an_unexpected_type_is_still_shown(self):
        edited = body()
        edited["milestones"][0]["acceptance_criteria"] = "C1"
        edited["open_blocking_questions"] = [{"id": "Q3", "question": "Which store?", "why": "Time zone", "options": 2}]
        plan = plan_file.read(plan_file.export(edited, revision=1, approved=False))
        self.assertIsNone(plan.stale_line)
        lines = text_of(plan_file.export(edited, revision=1, approved=False))
        self.assertIn("- Criteria: C1", lines)
        self.assertIn("- 2", lines)

    def test_credential_shaped_text_never_reaches_the_file_and_the_plan_reads_as_differing(self):
        original = body()
        token = "ghp_" + "a1B2" * 9
        original["constraints"] = [f"Clone with {token}", "Deploy key is s3cr3t-deploy-value"]
        exported = plan_file.export(original, revision=1, approved=False, secret_values=["s3cr3t-deploy-value"])
        self.assertNotIn(token, exported)
        self.assertNotIn("s3cr3t-deploy-value", exported)
        plan = plan_file.read(exported)
        self.assertEqual(["Clone with [REDACTED]", "Deploy key is [REDACTED]"], plan.body["constraints"])
        self.assertEqual(util.digest(original), plan.exported_hash)
        self.assertNotEqual(plan.exported_hash, plan.goal_hash)
        self.assertIsNone(plan.stale_line)

    def test_line_endings_and_trailing_spaces_are_not_edits(self):
        exported = plan_file.export(body(), revision=1, approved=False)
        reformatted = "\r\n".join(line + "  " if line.startswith("- ") else line for line in exported.split("\n"))
        self.assertIsNone(plan_file.read("\ufeff" + reformatted + "\r\n\r\n").stale_line)

    def test_a_file_autocode_cannot_read_is_refused_with_the_reason(self):
        exported = plan_file.export(body(), revision=1, approved=False)
        front, text = exported.split("\n---\n", 1)
        cases = {
            "starts with its front matter": "# Plan\n" + exported,
            "no closing `---` line": front,
            "not valid JSON at line 4": front.replace('"goal_hash"', "goal_hash", 1) + "\n---\n" + text,
            "names revision more than once": front.replace('"revision": 1,', '"revision": 1,\n  "revision": 2,')
            + "\n---\n"
            + text,
            "not a JSON number": front.replace('"revision": 1', '"revision": NaN') + "\n---\n" + text,
            "unknown: reviewer": front.replace("{", '{\n  "reviewer": "ana",', 1) + "\n---\n" + text,
            "reads plan format 1": front.replace('"autocode_plan": 1', '"autocode_plan": 2') + "\n---\n" + text,
            "64-character SHA-256": front.replace(util.digest(body()), "abc") + "\n---\n" + text,
            "must be one JSON object": "---\n[]\n---\n",
            "contract.acceptance_criteria must be a list of objects": front.replace(
                '"acceptance_criteria": [\n      {', '"acceptance_criteria": [\n      "C1",\n      {', 1
            )
            + "\n---\n"
            + text,
            "contract.deliverables must be a list of strings": front.replace(
                '"deliverables": [', '"deliverables": [\n      7,', 1
            )
            + "\n---\n"
            + text,
        }
        for reason, broken in cases.items():
            with self.subTest(reason=reason), self.assertRaisesRegex(ValueError, reason):
                plan_file.read(broken)


if __name__ == "__main__":
    unittest.main()
