"""The terminal chat shows which planning path a bug-fix run took, from the status view's ``stage_policy`` alone
(#1884 F2): a side-panel section and a /details block, nothing inferred and nothing offered as an action."""

import unittest

import autocode_chat_panel as panel
import autocode_chat_stage_policy as stage_policy

from .test_chat_panel import view
from .test_chat_tui import screen_ui

COMPACT = {
    "version": 1,
    "path": "compact",
    "reasons": [],
    "basis": {
        "diagnosis_output_hash": "d49d02bf8da6ed52620f28c45949c7fa08a85675696004e75db2822c59ec69e6",
        "diagnosis_source_revision": "b477fdce8551f141a3f542481a9fdf560db2503fe406f8151d6e86f7f7a078c2",
        "requirements_output": "/runs/x/iterations/001/requirements-01.json",
    },
    "decisions": [
        {
            "stage": "astra_discovery",
            "decision": "omitted",
            "reason": "The runner built the draft from the reproduced diagnosis and the Requirements record",
            "covered_by": ["runner:compact_draft", "astra_challenge", "person:approve_goal", "runner:regression_proof"],
        }
    ],
    "fallback": None,
}
FULL = {
    "version": 1,
    "path": "full",
    "reasons": [
        "the run did not opt in (--compact-bugfix-planning)",
        "the project declares no sensitive-paths, so every path counts as sensitive",
    ],
    "basis": {"diagnosis_output_hash": None, "diagnosis_source_revision": None, "requirements_output": None},
    "decisions": [{"stage": "astra_discovery", "decision": "included", "reason": "...", "covered_by": []}],
    "fallback": None,
}
FELL_BACK = {
    **COMPACT,
    "path": "full",
    "decisions": [
        {
            "stage": "astra_discovery",
            "decision": "included",
            "reason": "the Plan Reviewer raised blocking concerns PR1, PR2",
            "covered_by": [],
        }
    ],
    "fallback": {
        "from": "astra_challenge",
        "reason": "the Plan Reviewer raised blocking concerns PR1, PR2",
        "review_call_refunded": True,
    },
}


def with_policy(record):
    current = view()
    current["stage_policy"] = record
    return current


class CompactTests(unittest.TestCase):
    def test_a_compact_path_names_the_omitted_stage_and_what_covers_it(self):
        summary, rows = stage_policy.compact(with_policy(COMPACT))
        self.assertEqual("compact · no Planner draft", summary)
        self.assertEqual(
            [
                "covered by the runner's draft from the diagnosis, the Plan Reviewer, your approval of the plan, "
                "the regression proof",
                "Why: /details",
            ],
            rows,
        )

    def test_a_full_path_gives_its_first_reason_and_counts_the_rest(self):
        summary, rows = stage_policy.compact(with_policy(FULL))
        self.assertEqual("full · every stage runs", summary)
        self.assertEqual(
            ["the run did not opt in (--compact-bugfix-planning)", "and 1 more: /details", "Why: /details"], rows
        )

    def test_a_fallback_says_where_and_why(self):
        summary, rows = stage_policy.compact(with_policy(FELL_BACK))
        self.assertEqual("full · every stage runs", summary)
        self.assertEqual(
            ["fell back at astra_challenge: the Plan Reviewer raised blocking concerns PR1, PR2", "Why: /details"], rows
        )

    def test_nothing_is_shown_without_a_record_and_malformed_records_stay_unknown(self):
        self.assertIsNone(stage_policy.compact(view()))
        self.assertIsNone(stage_policy.compact({"stage_policy": None}))
        self.assertIsNone(stage_policy.compact({"stage_policy": "compact"}))
        summary, rows = stage_policy.compact({"stage_policy": {"path": "shortcut", "decisions": "none", "reasons": 3}})
        self.assertEqual(("unknown", ["Why: /details"]), (summary, rows))
        self.assertEqual("", stage_policy.details(view()))


class DetailsTests(unittest.TestCase):
    def test_the_details_carry_the_whole_record_in_words(self):
        text = stage_policy.details(with_policy(FELL_BACK))
        self.assertEqual(
            [
                "Planning path",
                "Path: full",
                "Planner draft: included (the Plan Reviewer raised blocking concerns PR1, PR2)",
                "Fallback: at astra_challenge, the Plan Reviewer raised blocking concerns PR1, PR2; the review call "
                "was refunded",
                "Decided from: diagnosis d49d02bf8da6, source b477fdce8551",
                "The record reports a decision the core made from trusted inputs; it grants nothing.",
            ],
            text.splitlines(),
        )
        compact = stage_policy.details(with_policy(COMPACT)).splitlines()
        self.assertIn(
            "Planner draft: omitted (The runner built the draft from the reproduced diagnosis and the Requirements record)",
            compact,
        )
        self.assertIn(
            "  Covered by: the runner's draft from the diagnosis, the Plan Reviewer, your approval of the plan, "
            "the regression proof",
            compact,
        )
        full = stage_policy.details(with_policy(FULL)).splitlines()
        self.assertIn("Kept the full path because:", full)
        self.assertIn("  - the project declares no sensitive-paths, so every path counts as sensitive", full)
        self.assertNotIn("Decided from", "\n".join(full), "no hashes, nothing invented")


class PanelTests(unittest.TestCase):
    def test_the_panel_gains_a_plan_path_section_after_usage_only_when_the_view_has_one(self):
        shown = panel.sections(with_policy(COMPACT))
        titles = [section.title for section in shown]
        self.assertIn("Plan path", titles)
        self.assertLess(titles.index("Now"), titles.index("Plan path"))
        self.assertLess(titles.index("Plan path"), titles.index("Requirements"))
        section = next(section for section in shown if section.title == "Plan path")
        self.assertEqual("compact · no Planner draft", section.summary)
        self.assertEqual("dim", section.rows[0].style)
        self.assertNotIn("Plan path", [section.title for section in panel.sections(view())])

    def test_details_include_the_planning_path_after_verification_and_cost(self):
        text = panel.text("/details", with_policy(FELL_BACK), panel.StageLog())
        self.assertIn("Planning path\nPath: full", text)
        self.assertLess(text.index("AutoCode is building the first task."), text.index("Planning path"))
        self.assertNotIn("Planning path", panel.text("/details", view(), panel.StageLog()))


class ScreenTests(unittest.TestCase):
    """The real layout at 120 columns shows the section in the side panel; at 80 columns, where the panel is
    hidden, /details carries the same record."""

    def drawn(self, ui, rows=30, cols=120):
        ui._screen.getmaxyx.return_value = (rows, cols)
        puts = []
        ui._put = lambda row, col, text, attr=0: puts.append((row, col, text))
        ui._draw()
        return [text for _, _, text in puts]

    def test_the_wide_panel_shows_the_plan_path_and_the_narrow_screen_keeps_it_in_details(self):
        ui = screen_ui()
        ui.observe(with_policy(COMPACT))
        texts = self.drawn(ui, 30, 120)
        self.assertTrue(any("PLAN PATH" in text.upper() for text in texts), texts)
        self.assertIn("PLAN PATH  compact · no Planner draft", texts)
        narrow = self.drawn(ui, 24, 80)
        self.assertFalse(any("no Planner draft" in text for text in narrow), "no panel at 80 columns")
        self.assertIn("Planning path\nPath: compact", panel.text("/details", with_policy(COMPACT), panel.StageLog()))
