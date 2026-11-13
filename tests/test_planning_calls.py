"""The cap on planning's model calls (owner decision 2026-11-12): what counts, what renews it, when it stops."""

from __future__ import annotations

import argparse
import unittest

import autocode_command_inputs as command_inputs
import autocode_configure as configure
import autocode_pause_category as pause_category
import autocode_pause_codes as pause_codes
import autocode_planning_calls as planning_calls
import autocode_stop_explanations as stop_explanations


def row(stage, at, **extra):
    return {"stage": stage, "started_at": at, **extra}


def at(minute):
    return f"2026-11-12T10:{minute:02d}:00+00:00"


def state(stages=(), events=(), investigations=(), cap=20, next_stage="review_plan", **extra):
    limits = {} if cap is None else {"planning_calls": cap}
    return {
        "settings": {"limits": limits},
        "stages": list(stages),
        "user_events": list(events),
        "stuck_investigations": list(investigations),
        "next_stage": next_stage,
        **extra,
    }


class CountTests(unittest.TestCase):
    def test_every_planning_attempt_counts_including_repairs_timeouts_rejections_and_saved_names(self):
        saved = state(
            [
                row("requirements_gather", at(1)),
                row("draft_plan", at(2), timed_out=True),
                row("draft_plan_report_repair", at(3), original_stage="draft_plan", report_only=True),
                row("review_plan", at(4), rejected=True),
                row("astra_challenge", at(5)),  # a saved run's code name for the Plan Reviewer
                row("plan_review", at(6)),  # planning v2
            ]
        )
        self.assertEqual(6, planning_calls.used(saved))

    def test_the_runner_building_and_task_planning_do_not_count(self):
        saved = state(
            [
                row("review_plan", at(1), runner_owned=True),
                row("builder", at(2)),
                row("tester", at(3)),
                row("plan_task", at(4)),  # a milestone's task Planner during building
                row("draft_plan", at(5), dry_run=True),
            ]
        )
        self.assertEqual(0, planning_calls.used(saved))

    def test_an_investigator_call_about_a_planning_stage_counts_and_one_about_the_builder_does_not(self):
        saved = state(
            investigations=[
                {"stage": "glm_revise", "model": "glm-5.3", "requested_at": at(1)},
                {"stage": "builder", "model": "glm-5.3", "requested_at": at(2)},
                {"stage": "review_plan", "requested_at": at(3)},  # settled without a model
            ]
        )
        self.assertEqual(1, planning_calls.used(saved))

    def test_only_the_persons_own_new_input_starts_the_count_again(self):
        stages = [row("draft_plan", at(1)), row("review_plan", at(2)), row("revise_plan", at(4))]
        machinery = [
            {"kind": "goal_approval", "actor": "user_cli", "at": at(3)},
            {"kind": "limit_change", "actor": "user_cli", "at": at(3)},
            {"kind": "resolver_resume_epoch", "actor": "user_cli", "at": at(3)},
            {"kind": "answer", "actor": "runner", "at": at(3)},
        ]
        self.assertEqual(3, planning_calls.used(state(stages, machinery)))
        self.assertIsNone(planning_calls.renewed_at(state(stages, machinery)))
        for kind, actor in (
            ("answer", "user_cli"),
            ("brief_feedback", "user_cli"),
            ("plan_message", "user_intervention"),
        ):
            with self.subTest(kind=kind):
                events = [*machinery, {"kind": kind, "actor": actor, "at": at(3)}]
                self.assertEqual(1, planning_calls.used(state(stages, events)))
                self.assertEqual(at(3), planning_calls.renewed_at(state(stages, events)))

    def test_a_record_without_a_readable_time_still_counts(self):
        events = [{"kind": "answer", "actor": "user_cli", "at": at(3)}]
        saved = state([row("draft_plan", None), row("review_plan", "yesterday")], events)
        self.assertEqual(2, planning_calls.used(saved))

    def test_a_damaged_saved_record_list_counts_nothing_and_keeps_the_view_readable(self):
        for damaged in (True, 7, "none", {"stage": "draft_plan"}):
            with self.subTest(damaged=damaged):
                state = {"stages": damaged, "stuck_investigations": damaged, "user_events": damaged}
                self.assertEqual(0, planning_calls.used(state))
                self.assertIsNone(planning_calls.renewed_at(state))


class CapTests(unittest.TestCase):
    def full(self, **extra):
        return state([row("draft_plan", at(minute)) for minute in range(20)], **extra)

    def test_at_the_cap_the_next_planning_call_stops(self):
        self.assertEqual((20, 20), planning_calls.reached(self.full()))
        self.assertIsNone(planning_calls.reached(self.full(cap=21)))

    def test_the_cap_does_not_stop_the_builder_or_the_tester(self):
        for stage in ("builder", "tester", "plan_task", "review_completion"):
            with self.subTest(stage=stage):
                self.assertIsNone(planning_calls.reached(self.full(next_stage=stage)))

    def test_it_stops_the_investigator_about_a_planning_stage_only(self):
        about = lambda stage: self.full(next_stage="investigate_stuck", stuck_investigation={"stage": stage})  # noqa: E731
        self.assertEqual((20, 20), planning_calls.reached(about("revise_plan")))
        self.assertIsNone(planning_calls.reached(about("builder")))

    def test_zero_and_a_run_saved_before_the_cap_have_none(self):
        for cap in (0, None):
            with self.subTest(cap=cap):
                self.assertIsNone(planning_calls.reached(self.full(cap=cap)))
                self.assertIsNone(planning_calls.view(self.full(cap=cap))["cap"])

    def test_the_view_and_the_stop_name_the_count_the_cap_and_the_flag(self):
        self.assertEqual({"used": 20, "cap": 20, "renewed_at": None}, planning_calls.view(self.full()))
        reason = planning_calls.stop_reason(20, 20)
        self.assertIn("20 model calls", reason)
        self.assertIn("--max-planning-calls N", reason)


class PauseTests(unittest.TestCase):
    def test_the_pause_is_the_persons_budget_decision_with_an_explanation(self):
        self.assertEqual("budget", pause_codes.CODES[planning_calls.STATUS])
        self.assertEqual("your_decision", pause_category.category(planning_calls.STATUS))
        self.assertIn(planning_calls.STATUS, stop_explanations.TABLE)

    def test_the_pause_offers_a_typed_cap_above_the_count(self):
        paused = state(
            [row("draft_plan", at(minute)) for minute in range(20)],
            status=planning_calls.STATUS,
        )
        need = {"kind": "resume", "action": "--resume-paused --max-planning-calls N"}
        (offer,) = command_inputs.project(paused, need, None)
        self.assertEqual(("max_planning_calls", "--max-planning-calls"), (offer["id"], offer["flag"]))
        self.assertEqual((20, 20, 21), (offer["current"], offer["used"], offer["minimum"]))


class SettingTests(unittest.TestCase):
    def args(self, **values):
        defaults = {name: None for flags in configure.BUDGET_ARGUMENTS.values() for name in flags}
        defaults.update(
            unlimited_iterations=False,
            autoresolver_managed_limits=False,
            legacy_iteration_ceiling=None,
            max_iterations=None,
            no_progress_limit=None,
        )
        defaults.update(values)
        return argparse.Namespace(**defaults)

    def test_a_new_run_caps_planning_at_twenty_calls_unless_the_person_chose_otherwise(self):
        self.assertEqual(20, configure._new_run_limits(self.args(), {})["planning_calls"])
        self.assertEqual(0, configure._new_run_limits(self.args(max_planning_calls=0), {})["planning_calls"])
        self.assertEqual(35, configure._new_run_limits(self.args(max_planning_calls=35), {})["planning_calls"])

    def test_autoresolver_never_manages_the_cap_even_when_limits_are_delegated(self):
        origins = configure.budget_origins(self.args(autoresolver_managed_limits=True))
        self.assertEqual("runner_default", origins["planning_calls"])
        self.assertEqual("resolver_delegated", origins["max_seconds"])
        chosen = configure.budget_origins(self.args(autoresolver_managed_limits=True, max_planning_calls=5))
        self.assertEqual("user_explicit", chosen["planning_calls"])


if __name__ == "__main__":
    unittest.main()
