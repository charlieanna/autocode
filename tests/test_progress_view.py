"""view.progress: counts from saved records only, and the exact line a person reads (#29)."""
import unittest

from pathlib import Path

import autocode_progress_view as progress_view
import autocode_run_view as run_view


def state(**changes):
    contract = {"hash": "h1", "revision": 1, "body": {
        "milestones": [{"id": "M1", "objective": "Add the reset link", "acceptance_criteria": ["C1"]},
                       {"id": "M2", "objective": "Send the reset email", "acceptance_criteria": ["C2"]},
                       {"id": "M3", "objective": "Reset page", "acceptance_criteria": ["C3"]}],
        "acceptance_criteria": [{"id": "C1", "criterion": "Link shown"}, {"id": "C2", "criterion": "Email sent"},
                                {"id": "C3", "criterion": "Password changes"}]}}
    base = {"status": "RUNNING", "goal_contract": contract,
            "settings": {"milestone_checkpoints": {"enabled": True}},
            "current_task": {"id": "task-2", "milestone_id": "M2"},
            "active_stage": {"stage": "terra"}, "findings_ledger": []}
    base.update(changes)
    return base


def checked(*rows, revision="r2", output="tester-2", contract_hash="h1"):
    return {"contract_hash": contract_hash, "source_revision": revision, "output": output,
            "criterion_results": [{"id": cid, "status": status} for cid, status in rows]}


def project(saved, accepted=(), stage="Builder", **options):
    return progress_view.progress(saved, accepted=accepted, stage=stage, needs=run_view.needs(saved), **options)


def states(view):
    return [item["state"] for item in view["requirements"]["items"]]


class ProgressLineTests(unittest.TestCase):
    def test_a_finished_run(self):
        saved = state(status="TASK_COMPLETE", active_stage=None,
                      validation=checked(("C1", "PASS"), ("C2", "PASS"), ("C3", "PASS"), revision="r9"))
        view = project(saved, accepted=["M1", "M2", "M3"], stage=None)
        self.assertEqual("Complete · 3 of 3 tasks done · 3 of 3 requirements checked · 0 open problems · "
                         "nothing needed from you", view["line"])
        self.assertEqual(["done"] * 3, [item["state"] for item in view["tasks"]["items"]])

    def test_a_run_waiting_on_questions(self):
        saved = state(status="WAITING_FOR_USER", active_stage=None, pending_questions=[
            {"id": "Q1", "question": "Which mail server?"}, {"id": "Q2", "question": "Link lifetime?"}])
        view = project(saved, accepted=["M1"])
        self.assertEqual("Waiting for you · 1 of 3 tasks done · 0 of 3 requirements checked · 0 open problems · "
                         "2 questions to answer", view["line"])
        self.assertEqual(["done", "waiting", "waiting"], [item["state"] for item in view["tasks"]["items"]])
        self.assertTrue(view["tasks"]["items"][1]["current"])

    def test_a_run_with_one_failed_check(self):
        saved = state(active_stage=None, next_stage="terra", validation=checked(("C1", "PASS"), ("C2", "FAIL")),
                      findings_ledger=[{"id": "F1", "status": "open", "source": "sol", "severity": "major",
                                        "finding": "Reset link works after it expired"},
                                       {"id": "F0", "status": "resolved", "finding": "Old"}])
        view = project(saved, accepted=["M1"])
        self.assertEqual("Next: Builder · 1 of 3 tasks done · 1 of 3 requirements checked, 1 failed · "
                         "1 open problem · nothing needed from you", view["line"])
        self.assertEqual(["checked", "failed", "unchecked"], states(view))
        self.assertEqual(["F1"], [item["id"] for item in view["problems"]["items"]])

    def test_before_planning(self):
        view = progress_view.progress({"status": "DISCOVERING"}, accepted=[], stage="Requirements",
                                      needs={"kind": "continue"})
        self.assertEqual("Next: Requirements · no task list yet · no requirements yet · 0 open problems · "
                         "nothing needed from you", view["line"])


class RequirementTests(unittest.TestCase):
    def test_not_verified_is_never_counted_as_checked(self):
        view = project(state(validation=checked(("C1", "NOT_VERIFIED"))))
        self.assertEqual((0, 3), (view["requirements"]["checked"], view["requirements"]["unchecked"]))

    def test_a_later_not_verified_row_keeps_the_earlier_verdict_and_says_it_is_from_an_earlier_check(self):
        # Testers mark criteria outside their task NOT_VERIFIED; an accepted task's pass still stands.
        saved = state(validation=checked(("C1", "NOT_VERIFIED"), ("C2", "PASS"), ("C3", "NOT_VERIFIED")),
                      validation_archive=[{"reason": "Superseded by another independent validation",
                                           "validation": checked(("C1", "PASS"), revision="r1", output="tester-1")}])
        view = project(saved)
        self.assertEqual(["checked_earlier", "checked", "unchecked"], states(view))
        self.assertEqual("2 of 3 requirements checked (1 from earlier checks)", view["requirements"]["label"])

    def test_a_failure_stays_failed_when_a_later_check_leaves_the_criterion_out(self):
        archive = [{"reason": "Superseded by another independent validation",
                    "validation": checked(("C1", "PASS"), revision="r1", output="tester-1")},
                   {"reason": "Superseded by another independent validation",
                    "validation": checked(("C1", "FAIL"), ("C2", "PASS"), revision="r2", output="tester-2")}]
        view = project(state(validation=checked(("C2", "PASS"), revision="r3", output="tester-3"),
                             validation_archive=archive))
        self.assertEqual(["failed", "checked", "unchecked"], states(view))

    def test_results_for_another_contract_never_count(self):
        # A revised plan, or a milestone carried over from the previous plan.
        archive = [{"reason": "Approved contract changed", "validation": checked(("C1", "PASS"), contract_hash="h0")}]
        view = project(state(validation=checked(("C2", "PASS"), contract_hash="h0"), validation_archive=archive))
        self.assertEqual(["unchecked"] * 3, states(view))
        self.assertIsNone(view["requirements"]["validated_source_revision"])

    def test_a_validation_that_was_never_applied_does_not_count(self):
        archive = [{"reason": progress_view.NOT_APPLIED, "validation": checked(("C1", "PASS"))}]
        self.assertEqual(["unchecked"] * 3, states(project(state(validation_archive=archive))))
        # The reason is autopilot's own wording; if it drifts, never-applied results would start counting.
        autopilot = Path(progress_view.__file__).with_name("autopilot.py").read_text()
        self.assertIn(repr(progress_view.NOT_APPLIED).strip("'"), autopilot)

    def test_a_validation_without_the_contract_hash_does_not_count_under_a_contract(self):
        # A legacy report archived when the run was migrated to a contract.
        legacy = {"source_revision": "r1", "criterion_results": [{"id": "C1", "status": "PASS"}]}
        view = project(state(validation_archive=[{"reason": "Approved contract changed", "validation": legacy}]))
        self.assertEqual(["unchecked"] * 3, states(view))

    def test_an_earlier_check_set_aside_reads_as_earlier_even_on_unchanged_code(self):
        # Feedback or changed evidence archives the validation; nothing current backs the pass any more.
        archive = [{"reason": "Queued feedback requires Plan Reviewer review", "validation": checked(("C1", "PASS"))}]
        self.assertEqual("checked_earlier", states(project(state(validation_archive=archive)))[0])

    def test_a_build_after_the_check_moves_its_passes_to_earlier_checks(self):
        stages = [{"stage": "terra", "output": "builder-1", "source_revision": "r2"},
                  {"stage": "sol", "output": "tester-2", "source_revision": "r2"},
                  {"stage": "terra", "output": "builder-2", "source_revision": "r3"}]
        view = project(state(stages=stages, validation=checked(("C1", "PASS"), ("C2", "FAIL"))))
        self.assertTrue(view["requirements"]["rebuilt_since_check"])
        self.assertEqual(["checked_earlier", "failed", "unchecked"], states(view))

    def test_a_build_that_changed_nothing_leaves_the_check_current(self):
        stages = [{"stage": "sol", "output": "tester-2", "source_revision": "r2"},
                  {"stage": "orchestrator", "output": "dispatch-1"},
                  {"stage": "terra", "output": "builder-2", "source_revision": "r2"}]
        view = project(state(stages=stages, validation=checked(("C1", "PASS"))))
        self.assertFalse(view["requirements"]["rebuilt_since_check"])
        self.assertEqual("checked", states(view)[0])

    def test_a_check_newer_than_the_last_build_is_current(self):
        # The source changed with no Builder (an edit after completion), and the Tester checked it again.
        stages = [{"stage": "terra", "output": "builder-1", "source_revision": "r1"},
                  {"stage": "sol", "output": "tester-2", "source_revision": "r2"},
                  {"stage": "astra_review", "output": "review-2", "source_revision": "r2"}]
        view = project(state(stages=stages, implementation={"source_revision": "r1"},
                             validation=checked(("C1", "PASS"), revision="r2")))
        self.assertFalse(view["requirements"]["rebuilt_since_check"])
        self.assertEqual("checked", states(view)[0])

    def test_human_review_counts_only_with_a_valid_receipt(self):
        saved = state(validation=checked(("C1", "PASS"), ("C2", "PASS"), ("C3", "PASS")))
        saved["goal_contract"]["body"]["acceptance_criteria"][2]["human_review"] = True
        waiting = project(saved)
        self.assertEqual("awaiting_review", states(waiting)[2])
        self.assertEqual("2 of 3 requirements checked, 1 awaiting your review", waiting["requirements"]["label"])
        # The #195 path: the Tester leaves the human criterion NOT_VERIFIED and the person accepts it.
        saved["validation"] = checked(("C1", "PASS"), ("C2", "PASS"), ("C3", "NOT_VERIFIED"))
        accepted = project(saved, reviewed={"C3"})
        self.assertEqual(["checked", "checked", "reviewed"], states(accepted))
        self.assertEqual("3 of 3 requirements checked (1 accepted in review)", accepted["requirements"]["label"])
        # A review of code the Builder has changed since is not a review of the code there now.
        saved["stages"] = [{"stage": "sol", "output": "tester-2", "source_revision": "r2"},
                           {"stage": "terra", "output": "builder-3", "source_revision": "r3"}]
        self.assertEqual("checked_earlier", states(project(saved, reviewed={"C3"}))[2])

    def test_legacy_runs_read_their_top_level_criteria(self):
        saved = {"status": "PAUSED_LEGACY_COMPLETION_UNVERIFIED", "acceptance_criteria": [{"id": "C1", "criterion": "x"}, "y"],
                 "validation": {"criterion_results": [{"id": "C1", "status": "PASS"}]}}
        view = project(saved, stage=None)
        self.assertEqual((1, 2), (view["requirements"]["checked"], view["requirements"]["total"]))


class TaskAndHeadlineTests(unittest.TestCase):
    def test_without_milestone_checkpoints_task_completion_is_unknown(self):
        tasks = project(state(settings={}))["tasks"]
        self.assertFalse(tasks["known"])
        self.assertEqual(["unknown", "working", "unknown"], [item["state"] for item in tasks["items"]])
        self.assertEqual("3 tasks, completion not tracked", tasks["label"])

    def test_gone_workers_are_never_called_working(self):
        view = project(state(), stopped="AutoResolver must reconcile retained attempt 002/builder-01")
        self.assertEqual("Builder stopped without saving a report", view["headline"])
        self.assertEqual("AutoResolver must reconcile retained attempt 002/builder-01", view["needs_you"])
        self.assertNotIn("working", [item["state"] for item in view["tasks"]["items"]])

    def test_a_finished_stage_is_not_called_working(self):
        # Its report is saved but not applied yet (the runner died, or is applying it now).
        view = project(state(), finished=True)
        self.assertEqual("Builder finished", view["headline"])
        self.assertNotIn("working", [item["state"] for item in view["tasks"]["items"]])

    def test_a_saved_plan_not_shown_yet_still_waits_for_the_person(self):
        view = project(state(status="AWAITING_GOAL_APPROVAL", active_stage=None, next_stage="astra_plan"),
                       stage="Planner")
        self.assertEqual(("Waiting for you", "plan approval needed"), (view["headline"], view["needs_you"]))

    def test_pauses_and_approvals_name_what_is_asked(self):
        cases = {"AWAITING_GOAL_APPROVAL": "plan approval needed", "PAUSED_BUDGET": "inspect the pause, then resume"}
        for status, asked in cases.items():
            with self.subTest(status=status):
                view = project(state(status=status, active_stage=None, displayed_goal="r1:h1"))
                self.assertEqual(asked, view["needs_you"])
                self.assertNotIn("%", view["line"])

    def test_a_follow_up_does_not_show_the_previous_request_as_its_progress(self):
        saved = state(status="RUNNING", active_stage=None, validation=checked(("C1", "PASS")),
                      turns=[{"at": "2026-10-04T10:00:00+00:00", "say": "also add X"}])
        saved["goal_contract"].update(created_at="2026-10-04T08:00:00+00:00", approval_status="approved",
                                      approval_event={"at": "2026-10-04T09:00:00+00:00", "token": "r1:h1"})
        view = project(saved, accepted=["M1", "M2", "M3"], stage="Requirements")
        self.assertTrue(view["for_earlier_request"])
        self.assertEqual("Next: Requirements · no task list yet for this request · no requirements yet for this "
                         "request · 0 open problems · nothing needed from you", view["line"])
        self.assertIsNone(view["requirements"]["validated_source_revision"])
        # Feedback that resets the old plan's approval does not make it the new request's plan.
        saved["goal_contract"].update(approval_status="draft", approval_event=None)
        self.assertTrue(project(saved)["for_earlier_request"])
        # A plan drafted after the follow-up is the new request's own.
        saved["goal_contract"]["created_at"] = "2026-10-04T11:00:00+00:00"
        self.assertFalse(project(saved)["for_earlier_request"])

    def test_a_finished_review_counts_its_findings(self):
        saved = {"status": "TASK_COMPLETE", "workflow": {"kind": "review"},
                 "review": {"blocking": 2, "advisory": 1, "verdict": "request_changes", "report_path": "review.json",
                            "output": "review-1"},
                 "stages": [{"stage": "review_change", "output": "review-1", "started_at": "2026-10-04T09:00:00+00:00"}]}
        view = project(saved, stage=None)
        self.assertEqual(3, view["problems"]["open"])
        self.assertIn("3 open problems", view["line"])
        # A follow-up asking for another review starts with none: those findings were about the earlier change.
        saved.update(status="RUNNING", turns=[{"at": "2026-10-04T10:00:00+00:00", "say": "Now review PR 185"}])
        self.assertEqual(0, project(saved, stage="Code Reviewer")["problems"]["open"])

    def test_a_design_conflict_stop_counts_its_conflicts(self):
        saved = {"status": "PAUSED_DESIGN_CONFLICT", "workflow": {"kind": "build"},
                 "design_check": {"conflicts": 2, "blockers": "design.blockers.json"}}
        self.assertEqual(2, project(saved, stage=None)["problems"]["open"])


if __name__ == "__main__":
    unittest.main()
