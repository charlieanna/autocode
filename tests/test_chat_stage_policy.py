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
        self.assertLess(titles.index("Plan path"), titles.index("Acceptance checks"))
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
    """Both compact layouts keep planning-policy detail reachable through /details."""

    def drawn(self, ui, rows=30, cols=120):
        ui._screen.getmaxyx.return_value = (rows, cols)
        puts = []
        ui._put = lambda row, col, text, attr=0: puts.append((row, col, text))
        ui._draw()
        return [text for _, _, text in puts]

    def test_compact_wide_and_narrow_screens_keep_the_plan_path_in_details(self):
        ui = screen_ui()
        ui.observe(with_policy(COMPACT))
        for rows, cols in ((30, 120), (24, 80)):
            with self.subTest(cols=cols):
                texts = self.drawn(ui, rows, cols)
                self.assertTrue(any("Builder working" in text for text in texts), texts)
                self.assertFalse(any("PLAN PATH" in text.upper() or "no Planner draft" in text for text in texts))
                details = panel.text("/details", with_policy(COMPACT), panel.StageLog())
                self.assertIn("Planning path\nPath: compact", details)
                self.assertIn("Planner draft: omitted", details)


ROUTES = {
    role: {"model": model}
    for role, model in (
        ("requirements", "sonnet"),
        ("planner", "sonnet"),
        ("task_planner", "opus"),
        ("terra", "haiku"),
        ("sol", "sonnet"),
        ("completion", "opus"),
    )
}


def decided(*rows, **fields):
    current = view()
    current.update(routes=ROUTES, **fields)
    current["stage_decisions"] = [
        {
            "stage": stage,
            "job": job,
            "label": label,
            "decision": decision,
            "reason": reason,
            "covered_by": covered,
            "launches": 0 if decision == "skip" else 1,
            "effort_choice": None,
        }
        for stage, job, label, decision, reason, covered in rows
    ]
    return current


EARLY_APPROVAL = (
    ("requirements_gather", "requirements", "Requirements", "run", None, []),
    ("astra_discovery", "planner", "Planner", "run", None, []),
    ("astra_challenge", "plan_reviewer", "Plan Reviewer", "run", None, []),
    (
        "glm_revise",
        "planner",
        "Planner",
        "skip",
        "no revision: the Plan Reviewer raised no blocking concern",
        ["astra_challenge", "person:approve_goal"],
    ),
    (
        "astra_finalize",
        "plan_reviewer",
        "Plan Reviewer",
        "skip",
        "no final review: the Plan Reviewer approved the plan at its review 1",
        ["astra_challenge", "person:approve_goal"],
    ),
)
ANSWERED = (
    ("requirements_gather", "requirements", "Requirements", "run", None, []),
    *(
        (
            stage,
            job,
            label,
            "skip",
            "Requirements answered the question from the code, and nothing was to be written",
            ["requirements_gather"],
        )
        for stage, job, label in (
            ("astra_discovery", "planner", "Planner"),
            ("astra_challenge", "plan_reviewer", "Plan Reviewer"),
            ("terra", "builder", "Builder"),
            ("sol", "tester", "Tester"),
            ("astra_review", "completion", "Completion Reviewer"),
        )
    ),
)


class PipelineTests(unittest.TestCase):
    """The Pipeline section marks what ran and what was skipped from the view's stage_decisions."""

    def marks(self, current):
        return [(row.mark, row.text) for row in panel.pipeline_section(current).rows]

    def test_stages_that_ran_are_marked_done_and_the_working_stage_stays_current(self):
        building = decided(*EARLY_APPROVAL, ("terra", "builder", "Builder", "run", None, []), next_stage="terra")
        building["progress"]["stage"] = "terra"
        self.assertEqual(
            [
                ("✓", "Requirements · sonnet"),
                ("✓", "Planner · sonnet"),
                ("✓", "Plan Reviewer · opus"),
                ("●", "Builder · haiku"),
                ("·", "Tester · sonnet"),
                ("·", "Completion Reviewer · opus"),
            ],
            self.marks(building),
        )

    def test_a_job_with_a_skipped_step_that_also_ran_is_done_not_skipped(self):
        marks = dict((text.split(" · ")[0], mark) for mark, text in self.marks(decided(*EARLY_APPROVAL)))
        self.assertEqual("✓", marks["Planner"], "the draft ran; only its revision was skipped")
        self.assertEqual("✓", marks["Plan Reviewer"])

    def test_a_discussion_requirements_answered_shows_every_later_job_skipped(self):
        self.assertEqual(
            [
                ("✓", "Requirements · sonnet"),
                ("–", "Planner · skipped"),
                ("–", "Plan Reviewer · skipped"),
                ("–", "Builder · skipped"),
                ("–", "Tester · skipped"),
                ("–", "Completion Reviewer · skipped"),
            ],
            self.marks(decided(*ANSWERED, status="TASK_COMPLETE")),
        )
        self.assertEqual("-", panel.ascii_mark("–"))

    def test_the_plan_reviewer_and_completion_rows_show_their_own_slots_not_the_resolvers(self):
        # #3087: the Plan Reviewer row read the Resolver's astra slot, so a run whose Plan Reviewer was
        # independent of the Planner showed the Planner's model on it.
        current = view()
        current["routes"] = {
            **ROUTES,
            "planner": {"model": "openai/gpt-6.1-sol", "reasoning_effort": "high"},
            "task_planner": {"model": "openai/gpt-6.1-sol", "reasoning_effort": "xhigh"},
            "plan_reviewer": {"model": "anthropic/claude-opus-5-5", "reasoning_effort": "high"},
            "completion": {"model": "anthropic/claude-sonnet-5-5"},
        }
        texts = [text for _mark, text in self.marks(current)]
        self.assertEqual("Planner · gpt-6.1-sol · high", texts[1])
        self.assertEqual("Plan Reviewer · claude-opus-5-5 · high", texts[2])
        self.assertEqual("Completion Reviewer · claude-sonnet-5-5", texts[5], "no level saved: none shown")

    def test_a_row_shows_the_level_the_job_last_ran_at_over_the_saved_one(self):
        current = decided(*EARLY_APPROVAL)
        current["routes"] = {
            **ROUTES,
            "task_planner": {"model": "opus", "reasoning_effort": "high"},
            "completion": {"model": "opus", "reasoning_effort": "high"},
        }
        current["stage_decisions"][2]["effort_choice"] = {"level": "medium", "ceiling": "high", "lowered": True}
        texts = [text for _mark, text in self.marks(current)]
        self.assertEqual("Plan Reviewer · opus · medium", texts[2], "the review ran one level below the saved high")
        self.assertEqual("Completion Reviewer · opus · high", texts[5], "not run yet: the saved level")

    def test_an_older_core_without_the_record_marks_nothing_done(self):
        older = view()
        older["routes"] = ROUTES
        self.assertEqual({"·"}, {mark for mark, _ in self.marks(older)})


class DecisionDetailsTests(unittest.TestCase):
    def test_details_list_what_ran_and_what_was_skipped_with_why_and_what_covers_it(self):
        current = decided(*EARLY_APPROVAL)
        current["stage_decisions"][2].update(
            launches=2, effort_choice={"level": "medium", "ceiling": "high", "lowered": True}
        )
        self.assertEqual(
            [
                "Stages",
                "Requirements: ran once",
                "Planner: ran once",
                "Plan Reviewer: ran 2 times, at medium, below your high",
                "Planner: skipped (no revision: the Plan Reviewer raised no blocking concern)",
                "  Covered by: the Plan Reviewer, your approval of the plan",
                "Plan Reviewer: skipped (no final review: the Plan Reviewer approved the plan at its review 1)",
                "  Covered by: the Plan Reviewer, your approval of the plan",
                "The record reports what the core decided; it grants nothing.",
            ],
            stage_policy.decisions(current).splitlines(),
        )
        raised = decided(("astra_discovery", "planner", "Planner", "run", None, []))
        raised["stage_decisions"][0]["effort_choice"] = {"level": "xhigh", "saved": "high", "raised": True}
        self.assertIn("Planner: ran once, at xhigh, above your high", stage_policy.decisions(raised))

    def test_details_show_the_record_after_the_other_blocks_and_nothing_without_it(self):
        text = panel.text("/details", decided(*ANSWERED), panel.StageLog())
        self.assertIn("Stages\nRequirements: ran once\nPlanner: skipped", text)
        self.assertIn("  Covered by: Requirements' own answer", text)
        self.assertLess(text.index("AutoCode is building the first task."), text.index("Stages"))
        self.assertNotIn("Stages\n", panel.text("/details", view(), panel.StageLog()))
