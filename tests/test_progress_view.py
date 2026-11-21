"""view.progress: counts from saved records only, and the exact line a person reads (#29)."""

import unittest
from pathlib import Path

import autocode_progress_view as progress_view
import autocode_recovery_history as recovery_history
import autocode_run_view as run_view

# The changed-code gate's record that sent the selected tests to the independent Tester (#3924).
STRENGTHENING = {
    "kind": "changed_code_rework",
    "test_owner": "write_acceptance_tests",
    "action": "builder",
    "round": 1,
    "round_limit": 2,
    "at": "2026-11-20T02:19:05+00:00",
}
# A review's record that sent a protected acceptance test back to the Tester for repair (#3350).
TEST_REPAIR = {"action": "writer", "round": 1, "round_limit": 2, "at": "2026-11-20T02:19:05+00:00"}


def state(**changes):
    contract = {
        "hash": "h1",
        "revision": 1,
        "body": {
            "milestones": [
                {"id": "M1", "objective": "Add the reset link", "acceptance_criteria": ["C1"]},
                {"id": "M2", "objective": "Send the reset email", "acceptance_criteria": ["C2"]},
                {"id": "M3", "objective": "Reset page", "acceptance_criteria": ["C3"]},
            ],
            "acceptance_criteria": [
                {"id": "C1", "criterion": "Link shown"},
                {"id": "C2", "criterion": "Email sent"},
                {"id": "C3", "criterion": "Password changes"},
            ],
        },
    }
    base = {
        "status": "RUNNING",
        "goal_contract": contract,
        "settings": {"milestone_checkpoints": {"enabled": True}},
        "current_task": {"id": "task-2", "milestone_id": "M2"},
        "active_stage": {"stage": "builder"},
        "findings_ledger": [],
    }
    base.update(changes)
    return base


def checked(*rows, revision="r2", output="tester-2", contract_hash="h1"):
    return {
        "contract_hash": contract_hash,
        "source_revision": revision,
        "output": output,
        "criterion_results": [{"id": cid, "status": status} for cid, status in rows],
    }


def project(saved, accepted=(), stage="Builder", **options):
    options.setdefault("liveness", {"kind": "supervised", "receipt": {"provider": {"pid": 103}}})
    return progress_view.progress(saved, accepted=accepted, stage=stage, needs=run_view.needs(saved), **options)


def states(view):
    return [item["state"] for item in view["requirements"]["items"]]


class ProgressLineTests(unittest.TestCase):
    def test_blocked_recovery_preserves_real_questions_and_feedback(self):
        blocked = {"category": "blocked"}
        saved = state(status="WAITING_FOR_USER", active_stage=None)
        question = {"kind": "answer", "questions": [{"id": "Q1", "question": "Which format?"}]}
        view = progress_view.progress(saved, accepted=[], stage=None, needs=question, recovery=blocked)
        self.assertEqual("Waiting for you", view["headline"])
        self.assertIn("question", view["needs_you"])
        unavailable = {"kind": "resume", "resume_unavailable": "Retry refused"}
        feedback = {"kind": "feedback", "next_command": "autocode --feedback MESSAGE"}
        view = progress_view.progress(
            saved, accepted=[], stage=None, needs=unavailable, recovery=blocked, person_input=feedback
        )
        self.assertIn("resume", view["needs_you"])
        view = progress_view.progress(
            saved, accepted=[], stage=None, needs=unavailable, recovery=blocked, next_command="autocode --resume-paused"
        )
        self.assertIn("resume", view["needs_you"])

    def test_continuing_is_a_machine_readable_observation_not_a_saved_pause(self):
        for status in ("PAUSED_REQUESTED", "PAUSED_TOOL_CONTAINMENT", "PAUSED_TIME_LIMIT"):
            with self.subTest(status=status):
                result = progress_view.progress(
                    state(status=status, active_stage=None),
                    accepted=[],
                    stage="Requirements",
                    needs=None,
                    controller_running=True,
                    continuing=True,
                )
                self.assertIs(True, result["continuing"])
                self.assertEqual("Continuing", result["headline"])
                self.assertEqual("nothing needed from you", result["needs_you"])
        for options in ({"continuing": False}, {"continuing": True, "internal_failure": {"title": "Blocked"}}):
            with self.subTest(options=options):
                self.assertIs(
                    False,
                    progress_view.progress(
                        state(status="PAUSED_REQUESTED", active_stage=None),
                        accepted=[],
                        stage=None,
                        needs=None,
                        **options,
                    )["continuing"],
                )

    def test_a_fresh_runs_first_save_names_no_stage_it_has_not_chosen(self):
        """A new run's first save carries the default next stage (plan_task) for a moment before the
        run chooses its first stage; the chat read that save and logged "Next: Planner" ahead of
        Requirements (2026-11-08 live run)."""
        fresh = {"status": "RUNNING", "iteration": 1, "stages": [], "next_stage": "plan_task"}
        shown = progress_view.progress(fresh, accepted=[], stage="Planner", needs=run_view.needs(fresh))
        self.assertEqual("Starting the run", shown["headline"])
        self.assertIsNone(shown["stage"], "no stage is chosen yet")
        self.assertTrue(shown["line"].startswith("Starting the run ·"))
        chosen = {**fresh, "phase": "DISCOVERING", "next_stage": "requirements_gather"}
        shown = progress_view.progress(chosen, accepted=[], stage="Requirements", needs=run_view.needs(chosen))
        self.assertEqual("Next: Requirements", shown["headline"])
        planned = {**fresh, "phase": "PLANNING"}  # a run that does start at plan_task says so once it has
        shown = progress_view.progress(planned, accepted=[], stage="Planner", needs=run_view.needs(planned))
        self.assertEqual("Next: Planner", shown["headline"])

    def test_a_runner_check_between_stages_is_named_not_the_next_job(self):
        """While the runner checks the saved work between the Builder and the Tester, the headline
        said "Next: Tester" for the whole check (2026-11-11 live run on #3233)."""
        saved = state(
            active_stage=None, active_runner_check={"stage": "regression", "summary": "Running the Tester's tests"}
        )
        view = project(saved, stage="Tester")
        self.assertEqual("Running the Tester's tests", view["headline"])
        self.assertEqual("Tester", view["stage"], "the next job stays known")
        self.assertTrue(view["line"].startswith("Running the Tester's tests · "))
        idle = project(state(active_stage=None), stage="Tester")
        self.assertEqual("Next: Tester", idle["headline"], "with no check running the next job is named")

    def test_a_run_autocode_ended_says_stopped_not_the_next_job(self):
        """A run AutoCode ended itself (autocode_stop.end) read "Next: Builder · ... · nothing needed from
        you", though no stage runs again (2026-11-16 fake runs of rework-same-failure and
        escalation-ladder-end on master)."""
        ended = state(
            status="PAUSED_INTERVENTION",
            active_stage=None,
            stop_reason="AutoCode could not finish this task: Blocked. This run launches no further stages.",
        )
        view = project(ended, stage="Builder")
        self.assertEqual(progress_view.ENDED, view["headline"])
        self.assertIsNone(view["stage"])
        self.assertTrue(view["line"].startswith(progress_view.ENDED + " · "))
        self.assertTrue(view["line"].endswith(" · nothing needed from you"))
        # A person's own stop is not AutoCode's ending, and keeps its existing headline.
        stopped = state(status="PAUSED_INTERVENTION", active_stage=None, stop_reason="Stopped at your request.")
        self.assertNotEqual(progress_view.ENDED, project(stopped, stage="Builder")["headline"])

    def test_a_finished_run(self):
        saved = state(
            status="TASK_COMPLETE",
            active_stage=None,
            validation=checked(("C1", "PASS"), ("C2", "PASS"), ("C3", "PASS"), revision="r9"),
        )
        view = project(saved, accepted=["M1", "M2", "M3"], stage=None)
        self.assertEqual(
            "Complete · 3 of 3 tasks done · 3 of 3 requirements checked · 0 open problems · nothing needed from you",
            view["line"],
        )
        self.assertEqual(["done"] * 3, [item["state"] for item in view["tasks"]["items"]])

    def test_a_run_waiting_on_questions(self):
        saved = state(
            status="WAITING_FOR_USER",
            active_stage=None,
            pending_questions=[
                {"id": "Q1", "question": "Which mail server?"},
                {"id": "Q2", "question": "Link lifetime?"},
            ],
        )
        view = project(saved, accepted=["M1"])
        self.assertEqual(
            "Waiting for you · 1 of 3 tasks done · 0 of 3 requirements checked · 0 open problems · "
            "2 questions to answer",
            view["line"],
        )
        self.assertEqual(["done", "waiting", "waiting"], [item["state"] for item in view["tasks"]["items"]])
        self.assertTrue(view["tasks"]["items"][1]["current"])

    def test_requirements_review_confirmation_is_named_not_counted_as_a_question(self):
        saved = state(
            status="WAITING_FOR_USER",
            active_stage=None,
            pending_questions=[{"id": "requirements_review", "question": "Confirm requirements summary?"}],
        )
        shown = project(saved)
        self.assertIn("requirements confirmation needed", shown["line"])
        self.assertNotIn("1 question to answer", shown["line"])

    def test_a_run_with_one_failed_check(self):
        saved = state(
            active_stage=None,
            next_stage="builder",
            validation=checked(("C1", "PASS"), ("C2", "FAIL")),
            findings_ledger=[
                {
                    "id": "F1",
                    "status": "open",
                    "source": "tester",
                    "severity": "major",
                    "finding": "Reset link works after it expired",
                },
                {"id": "F0", "status": "resolved", "finding": "Old"},
            ],
        )
        view = project(saved, accepted=["M1"])
        self.assertEqual(
            "Next: Builder · 1 of 3 tasks done · 1 of 3 requirements checked, 1 failed · "
            "1 open problem · nothing needed from you",
            view["line"],
        )
        self.assertEqual(["checked", "failed", "unchecked"], states(view))
        self.assertEqual(["F1"], [item["id"] for item in view["problems"]["items"]])

    def test_a_stage_its_live_controller_is_starting_says_so(self):
        """A new chat's "hi" showed "Next: Requirements" for the tens of seconds the controller spent
        getting Requirements ready (2026-11-12); while that controller is alive, the stage is starting."""
        chosen = {
            "status": "RUNNING",
            "iteration": 1,
            "stages": [],
            "phase": "PLANNING",
            "next_stage": "requirements_gather",
        }
        needs = run_view.needs(chosen)
        shown = progress_view.progress(chosen, accepted=[], stage="Requirements", needs=needs, controller_running=True)
        self.assertEqual("Starting Requirements", shown["headline"])
        self.assertTrue(shown["line"].startswith("Starting Requirements ·"))
        # With no live controller nothing is starting it: the run waits for whoever continues it.
        self.assertEqual(
            "Next: Requirements",
            progress_view.progress(chosen, accepted=[], stage="Requirements", needs=needs)["headline"],
        )
        paused = {**chosen, "status": "PAUSED_PROCESS_CHECK"}
        shown = progress_view.progress(
            paused, accepted=[], stage="Requirements", needs=run_view.needs(paused), controller_running=True
        )
        self.assertNotIn("Starting", shown["headline"])

    def test_before_planning(self):
        view = progress_view.progress(
            {"status": "DISCOVERING"}, accepted=[], stage="Requirements", needs={"kind": "continue"}
        )
        self.assertEqual(
            "Next: Requirements · no task list yet · no requirements yet · 0 open problems · nothing needed from you",
            view["line"],
        )


class RequirementTests(unittest.TestCase):
    def test_not_verified_is_never_counted_as_checked(self):
        view = project(state(validation=checked(("C1", "NOT_VERIFIED"))))
        self.assertEqual((0, 3), (view["requirements"]["checked"], view["requirements"]["unchecked"]))

    def test_a_later_not_verified_row_keeps_the_earlier_verdict_and_says_it_is_from_an_earlier_check(self):
        # Testers mark criteria outside their task NOT_VERIFIED; an accepted task's pass still stands.
        saved = state(
            validation=checked(("C1", "NOT_VERIFIED"), ("C2", "PASS"), ("C3", "NOT_VERIFIED")),
            validation_archive=[
                {
                    "reason": "Superseded by another independent validation",
                    "validation": checked(("C1", "PASS"), revision="r1", output="tester-1"),
                }
            ],
        )
        view = project(saved)
        self.assertEqual(["checked_earlier", "checked", "unchecked"], states(view))
        self.assertEqual("2 of 3 requirements checked (1 from earlier checks)", view["requirements"]["label"])

    def test_a_failure_stays_failed_when_a_later_check_leaves_the_criterion_out(self):
        archive = [
            {
                "reason": "Superseded by another independent validation",
                "validation": checked(("C1", "PASS"), revision="r1", output="tester-1"),
            },
            {
                "reason": "Superseded by another independent validation",
                "validation": checked(("C1", "FAIL"), ("C2", "PASS"), revision="r2", output="tester-2"),
            },
        ]
        view = project(
            state(validation=checked(("C2", "PASS"), revision="r3", output="tester-3"), validation_archive=archive)
        )
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
        stages = [
            {"stage": "builder", "output": "builder-1", "source_revision": "r2"},
            {"stage": "tester", "output": "tester-2", "source_revision": "r2"},
            {"stage": "builder", "output": "builder-2", "source_revision": "r3"},
        ]
        view = project(state(stages=stages, validation=checked(("C1", "PASS"), ("C2", "FAIL"))))
        self.assertTrue(view["requirements"]["rebuilt_since_check"])
        self.assertEqual(["checked_earlier", "failed", "unchecked"], states(view))

    def test_a_build_that_changed_nothing_leaves_the_check_current(self):
        stages = [
            {"stage": "tester", "output": "tester-2", "source_revision": "r2"},
            {"stage": "orchestrator", "output": "dispatch-1"},
            {"stage": "builder", "output": "builder-2", "source_revision": "r2"},
        ]
        view = project(state(stages=stages, validation=checked(("C1", "PASS"))))
        self.assertFalse(view["requirements"]["rebuilt_since_check"])
        self.assertEqual("checked", states(view)[0])

    def test_a_check_newer_than_the_last_build_is_current(self):
        # The source changed with no Builder (an edit after completion), and the Tester checked it again.
        stages = [
            {"stage": "builder", "output": "builder-1", "source_revision": "r1"},
            {"stage": "tester", "output": "tester-2", "source_revision": "r2"},
            {"stage": "review_completion", "output": "review-2", "source_revision": "r2"},
        ]
        view = project(
            state(
                stages=stages,
                implementation={"source_revision": "r1"},
                validation=checked(("C1", "PASS"), revision="r2"),
            )
        )
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
        saved["stages"] = [
            {"stage": "tester", "output": "tester-2", "source_revision": "r2"},
            {"stage": "builder", "output": "builder-3", "source_revision": "r3"},
        ]
        self.assertEqual("checked_earlier", states(project(saved, reviewed={"C3"}))[2])

    def test_legacy_runs_read_their_top_level_criteria(self):
        saved = {
            "status": "PAUSED_LEGACY_COMPLETION_UNVERIFIED",
            "acceptance_criteria": [{"id": "C1", "criterion": "x"}, "y"],
            "validation": {"criterion_results": [{"id": "C1", "status": "PASS"}]},
        }
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

    def test_a_tester_writing_tests_before_the_builder_says_so(self):
        """2026-11-11 live run: "Tester working · 0 of 1 task done" while the Tester wrote the approved
        tests before any Builder ran read as a Tester checking finished work that never came."""
        writing = state(active_stage={"stage": "write_acceptance_tests"})
        view = project(writing, stage="Tester")
        self.assertEqual("Tester writing tests first", view["headline"])
        self.assertTrue(view["line"].startswith("Tester writing tests first · 0 of 3 tasks done"))
        self.assertEqual("Tester", view["stage"])
        checking = project(state(active_stage={"stage": "tester"}), stage="Tester")
        self.assertEqual("Tester working", checking["headline"])
        self.assertEqual("Tester finished", project(writing, stage="Tester", finished=True)["headline"])

    def test_c1_strengthening_round_headline(self):
        """#3924: after the Builder, the changed-code gate sent the selected tests back to the independent
        Tester (Independent test strengthening, round 1 of 2) on the same stage; the status bar still read
        "Tester writing tests first · 0 of 3 tasks done", as if the run had restarted."""
        strengthening = state(
            active_stage={"stage": "write_acceptance_tests"},
            acceptance_tests={"pending_evidence_repair": {"binding": {"review_output": "tester-2"}}},
            regression_proofs=[STRENGTHENING],
        )
        view = project(strengthening, stage="Tester")
        self.assertEqual("Tester strengthening tests (round 1 of 2)", view["headline"])
        self.assertTrue(view["line"].startswith("Tester strengthening tests (round 1 of 2) · 0 of 3 tasks done"))
        self.assertNotIn("writing tests first", view["line"])
        # The headline reads the round of the record the recoveries list shows.
        self.assertEqual(
            ["Independent test strengthening (round 1 of 2)"],
            [row["headline"] for row in recovery_history.project(strengthening)],
        )
        # The same record with the strengthening done (no pending pin) is no longer the stage's purpose.
        done = state(active_stage={"stage": "write_acceptance_tests"}, regression_proofs=[STRENGTHENING])
        self.assertEqual("Tester writing tests first", project(done, stage="Tester")["headline"])

    def test_c3_protected_test_repair_round_headline(self):
        """A review sent a protected acceptance test back to the Tester for repair (#3350): the same stage
        repairs tests, and its headline reads that purpose and round from the recovery record."""
        repair = {"pending_test_repair": {"binding": {"case_ids": ["C1"]}}, "test_repairs": [TEST_REPAIR]}
        repairing = state(active_stage={"stage": "write_acceptance_tests"}, acceptance_tests=repair)
        view = project(repairing, stage="Tester")
        self.assertEqual("Tester repairing tests (round 1 of 2)", view["headline"])
        self.assertEqual(
            ["Independent acceptance-test repair (round 1 of 2)"],
            [row["headline"] for row in recovery_history.project(repairing)],
        )
        # Both pending: the runner writes the repair's next action last, so repair takes precedence.
        both = state(
            active_stage={"stage": "write_acceptance_tests"},
            acceptance_tests={**repair, "pending_evidence_repair": {"binding": {"review_output": "tester-2"}}},
            regression_proofs=[STRENGTHENING],
        )
        self.assertEqual("Tester repairing tests (round 1 of 2)", project(both, stage="Tester")["headline"])

    def test_a_test_writer_purpose_saved_without_rounds_names_no_round(self):
        # A record saved before rounds were recorded still names the purpose, with no round to show.
        legacy = {key: value for key, value in STRENGTHENING.items() if key not in ("round", "round_limit")}
        strengthening = state(
            active_stage={"stage": "write_acceptance_tests"},
            acceptance_tests={"pending_evidence_repair": {"binding": {}}},
            regression_proofs=[legacy, {"kind": "changed_code_rework", "action": "builder", "round": 2}],
        )
        self.assertEqual("Tester strengthening tests", project(strengthening, stage="Tester")["headline"])
        repairing = state(
            active_stage={"stage": "write_acceptance_tests"},
            acceptance_tests={"pending_test_repair": {"binding": {}}, "test_repairs": [{"action": "exhausted"}]},
        )
        self.assertEqual("Tester repairing tests", project(repairing, stage="Tester")["headline"])
        self.assertEqual(
            {"kind": "repair", "round": None, "round_limit": None}, recovery_history.test_writer_purpose(repairing)
        )
        self.assertIsNone(recovery_history.test_writer_purpose({"acceptance_tests": None}))

    def test_a_finished_stage_names_the_job_that_comes_next(self):
        # A finished Builder must not keep the headline across a Builder→Tester handoff (#1711).
        view = project(state(), finished=True, following="Tester")
        self.assertEqual("Builder finished · Next: Tester", view["headline"])
        self.assertTrue(view["line"].startswith("Builder finished · Next: Tester ·"))
        # Without a known next job the plain finished line still stands.
        self.assertEqual("Builder finished", project(state(), finished=True)["headline"])

    def test_a_saved_plan_not_shown_yet_still_waits_for_the_person(self):
        view = project(
            state(status="AWAITING_GOAL_APPROVAL", active_stage=None, next_stage="plan_task"), stage="Planner"
        )
        self.assertEqual(("Waiting for you", "plan approval needed"), (view["headline"], view["needs_you"]))

    def test_pauses_and_approvals_name_what_is_asked(self):
        cases = {"AWAITING_GOAL_APPROVAL": "plan approval needed", "PAUSED_BUDGET": "inspect the pause, then resume"}
        for status, asked in cases.items():
            with self.subTest(status=status):
                view = project(state(status=status, active_stage=None, displayed_goal="r1:h1"))
                self.assertEqual(asked, view["needs_you"])
                self.assertNotIn("%", view["line"])

    def test_a_job_stopped_on_its_model_asks_for_a_model_then_only_the_retry(self):
        saved = state(status="PAUSED_JOB_FAILURE", active_stage=None, job_failure={"route": {}})
        need = {"kind": "retry_job", "route": {"job": "Code Reviewer", "current_model": "gpt-6-sol"}}
        asked = lambda: progress_view.progress(saved, accepted=(), stage="Code Reviewer", needs=need)["needs_you"]
        self.assertEqual("name another model for the Code Reviewer, then retry it", asked())
        saved["job_failure"]["route_assignment"] = {"to": "gpt-6-luna"}
        need["route"]["current_model"] = "gpt-6-luna"
        self.assertEqual("retry the Code Reviewer on gpt-6-luna", asked())
        need = {"kind": "retry_job"}
        self.assertEqual("inspect the failed job, then retry", asked())

    def test_a_follow_up_does_not_show_the_previous_request_as_its_progress(self):
        saved = state(
            status="RUNNING",
            active_stage=None,
            validation=checked(("C1", "PASS")),
            turns=[{"at": "2026-10-04T10:00:00+00:00", "say": "also add X"}],
        )
        saved["goal_contract"].update(
            created_at="2026-10-04T08:00:00+00:00",
            approval_status="approved",
            approval_event={"at": "2026-10-04T09:00:00+00:00", "token": "r1:h1"},
        )
        view = project(saved, accepted=["M1", "M2", "M3"], stage="Requirements")
        self.assertTrue(view["for_earlier_request"])
        self.assertEqual(
            "Next: Requirements · no task list yet for this request · no requirements yet for this "
            "request · 0 open problems · nothing needed from you",
            view["line"],
        )
        self.assertIsNone(view["requirements"]["validated_source_revision"])
        # Feedback that resets the old plan's approval does not make it the new request's plan.
        saved["goal_contract"].update(approval_status="draft", approval_event=None)
        self.assertTrue(project(saved)["for_earlier_request"])
        # A plan drafted after the follow-up is the new request's own.
        saved["goal_contract"]["created_at"] = "2026-10-04T11:00:00+00:00"
        self.assertFalse(project(saved)["for_earlier_request"])

    def test_a_finished_review_counts_its_findings(self):
        saved = {
            "status": "TASK_COMPLETE",
            "workflow": {"kind": "review"},
            "review": {
                "blocking": 2,
                "advisory": 1,
                "verdict": "request_changes",
                "report_path": "review.json",
                "output": "review-1",
            },
            "stages": [{"stage": "review_change", "output": "review-1", "started_at": "2026-10-04T09:00:00+00:00"}],
        }
        view = project(saved, stage=None)
        self.assertEqual(3, view["problems"]["open"])
        self.assertIn("3 open problems", view["line"])
        # A follow-up asking for another review starts with none: those findings were about the earlier change.
        saved.update(status="RUNNING", turns=[{"at": "2026-10-04T10:00:00+00:00", "say": "Now review PR 185"}])
        self.assertEqual(0, project(saved, stage="Code Reviewer")["problems"]["open"])

    def test_a_revised_design_review_counts_its_open_concerns_and_says_which_revision(self):
        saved = {
            "status": "TASK_COMPLETE",
            "workflow": {"kind": "design"},
            "design_review": {
                "mode": "review",
                "blocking": 1,
                "advisory": 1,
                "resolved": 1,
                "questions": 2,
                "revision": 3,
                "report_path": "review/design-review.json",
                "output": "design-3",
            },
            "stages": [{"stage": "review_design", "output": "design-3", "started_at": "2026-10-04T09:00:00+00:00"}],
        }
        view = project(saved, stage=None)
        # Added fields only: kind, blocking, advisory and report_path keep their meaning (open concerns).
        self.assertEqual(
            [
                {
                    "kind": "design_review",
                    "blocking": 1,
                    "advisory": 1,
                    "report_path": "review/design-review.json",
                    "questions": 2,
                    "revision": 3,
                }
            ],
            view["problems"]["reports"],
        )
        self.assertEqual(2, view["problems"]["open"])
        # A review saved before revisions existed is revision 1.
        for key in ("revision", "resolved"):
            del saved["design_review"][key]
        self.assertEqual(1, project(saved, stage=None)["problems"]["reports"][0]["revision"])

    def test_a_design_conflict_stop_counts_its_conflicts(self):
        saved = {
            "status": "PAUSED_DESIGN_CONFLICT",
            "workflow": {"kind": "build"},
            "design_check": {"conflicts": 2, "blockers": "design.blockers.json"},
        }
        self.assertEqual(2, project(saved, stage=None)["problems"]["open"])


if __name__ == "__main__":
    unittest.main()
