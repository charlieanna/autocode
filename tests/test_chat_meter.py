"""What each stage did and used, and the run and session totals, from the status view alone."""

import unittest
from datetime import UTC, datetime

import autocode_chat_meter as meter
import autocode_chat_panel as panel

NOW = datetime(2026, 11, 2, 1, 10, 0, tzinfo=UTC)


def attempt(stage, job, model="gpt-x", effort="high", **extra):
    row = {
        "stage": stage,
        "job": job,
        "launch_route": {"model": model, "reasoning_effort": effort},
        "duration_seconds": 30,
        "exit_code": 0,
        "attributed_tokens": {
            "input_tokens": {"value": 1000, "known": 1000, "complete": True},
            "output_tokens": {"value": 200, "known": 200, "complete": True},
        },
    }
    row.update(extra)
    return row


def view(*attempts, spent=0.42, by_stage=(), inherited=None, complete=True):
    return {
        "usage": {
            "accounting": {
                "attempts": list(attempts),
                "tokens": {
                    "input_tokens": {"value": 150_000, "known": 150_000, "complete": True},
                    "output_tokens": {"value": 34_000, "known": 34_000, "complete": complete},
                },
            }
        },
        "cost": {
            "spent_usd": spent,
            "inherited_usd": inherited,
            "complete": complete,
            "breakdown": {"by_stage": list(by_stage)},
        },
    }


class StageTests(unittest.TestCase):
    def test_each_stage_shows_its_model_time_tokens_and_cost(self):
        shown = view(
            attempt("requirements_gather", "Requirements"),
            attempt("builder", "Builder", model="gpt-terra", effort="medium"),
            by_stage=[{"stage": "builder", "spent_usd": 0.12}],
        )
        rows = meter.stages(shown, NOW)
        self.assertEqual(["Requirements", "Builder"], [row["job"] for row in rows])
        head, detail, style = meter.stage_lines(rows[1])
        self.assertEqual("✓ Builder · 30s · $0.12", head)
        self.assertEqual("  gpt-terra medium · 1.2k tokens", detail)
        self.assertEqual("good", style)

    def test_a_lowered_review_shows_the_level_it_ran_at_not_the_saved_ceiling(self):
        lowered = attempt("review_plan", "Plan Reviewer", model="mimo", effort="high", reasoning_effort="medium")
        (row,) = meter.stages(view(lowered), NOW)
        self.assertEqual("mimo medium", row["route"])

    def test_a_stage_with_no_model_shows_no_cost_or_model_row(self):
        plain = attempt("orchestrator", "Orchestrator", launch_route={}, attributed_tokens={})
        shown = view(plain, by_stage=[{"stage": "orchestrator", "spent_usd": 0}])
        head, detail, _ = meter.stage_lines(meter.stages(shown, NOW)[0])
        self.assertEqual(("✓ Orchestrator · 30s", ""), (head, detail.strip()))
        self.assertEqual(1, len(panel.stage_section(shown, NOW).rows))

    def test_a_retry_is_struggling_and_says_why_the_last_attempt_failed(self):
        shown = view(
            attempt("builder", "Builder", rejected=True),
            attempt("builder", "Builder", active=True, duration_seconds=None, started_at="2026-11-02T01:06:00Z"),
        )
        (row,) = meter.stages(shown, NOW)
        self.assertEqual((meter.RETRYING, 2, 270.0), (row["state"], row["attempts"], row["seconds"]))
        self.assertEqual("Builder · attempt 2 · 4m00s · last answer rejected", meter.struggling(shown, NOW))

    def test_retry_time_is_distinct_from_the_stage_total(self):
        shown = view(
            attempt("tester", "Tester", timed_out=True, duration_seconds=900),
            attempt("tester", "Tester", timed_out=True, duration_seconds=2100),
            attempt("tester", "Tester", active=True, duration_seconds=None, started_at="2026-11-02T01:05:00Z"),
        )
        (row,) = meter.stages(shown, NOW)
        self.assertEqual(3300, row["seconds"])
        self.assertEqual(300, row["attempt_seconds"])
        self.assertEqual("Tester · attempt 3 · 5m00s · last one timed out", meter.struggling(shown, NOW))
        self.assertEqual("↻ Tester · 55m00s total · attempt 3", meter.stage_lines(row)[0])

    def test_retry_with_unknown_start_does_not_show_previous_time_as_current(self):
        shown = view(
            attempt("tester", "Tester", timed_out=True, duration_seconds=900),
            attempt("tester", "Tester", active=True, duration_seconds=None, started_at="unknown"),
        )
        (row,) = meter.stages(shown, NOW)
        self.assertIsNone(row["attempt_seconds"])
        self.assertEqual("Tester · attempt 2 · last one timed out", meter.struggling(shown, NOW))
        self.assertIn("15m00s total", meter.stage_lines(row)[0])

    def test_a_report_repair_is_the_repaired_stages_next_attempt(self):
        # As in the live run of 2026-11-02: sol was rejected, the Resolver ran, then tester_report_repair passed.
        rejected = attempt("tester", "Tester", rejected=True)
        repairing = attempt(
            "tester_report_repair",
            "Tester",
            original_stage="tester",
            active=True,
            duration_seconds=None,
            started_at="2026-11-02T01:09:30Z",
        )
        shown = view(rejected, attempt("resolver", "Resolver"), repairing)
        self.assertEqual(["Tester", "Resolver"], [row["job"] for row in meter.stages(shown, NOW)])
        self.assertEqual("Tester · attempt 2 · 30s · last answer rejected", meter.struggling(shown, NOW))
        repaired = {**repairing, "active": False, "duration_seconds": 18}
        costs = [{"stage": "tester", "spent_usd": 0.25}, {"stage": "tester_report_repair", "spent_usd": 0.1}]
        tester = meter.stages(view(rejected, attempt("resolver", "Resolver"), repaired, by_stage=costs), NOW)[0]
        self.assertEqual((meter.DONE, 2, 0.35), (tester["state"], tester["attempts"], tester["spent_usd"]))

    def test_a_second_round_after_a_success_is_not_struggling(self):
        # Live: the Plan Reviewer's challenge ran twice, and both rounds succeeded.
        shown = view(
            attempt("review_plan", "Plan Reviewer"),
            attempt(
                "review_plan",
                "Plan Reviewer",
                active=True,
                duration_seconds=None,
                started_at="2026-11-02T01:09:00Z",
            ),
        )
        (row,) = meter.stages(shown, NOW)
        self.assertEqual((meter.WORKING, 2), (row["state"], row["attempts"]))
        self.assertEqual("", meter.struggling(shown, NOW))
        self.assertEqual("● Plan Reviewer · 1m30s total · 2 runs", meter.stage_lines(row)[0])

    def test_a_new_turn_after_a_repaired_attempt_is_not_a_retry(self):
        # Live 2026-11-05: Requirements' second turn was rejected and repaired, then the person
        # answered and its third turn ran. The panel said "attempt 4", as if it were failing.
        shown = view(
            attempt("requirements_gather", "Requirements"),
            attempt("requirements_gather", "Requirements", rejected=True),
            attempt("requirements_gather_report_repair", "Requirements", original_stage="requirements_gather"),
            attempt(
                "requirements_gather",
                "Requirements",
                active=True,
                duration_seconds=None,
                started_at="2026-11-02T01:09:00Z",
            ),
        )
        (row,) = meter.stages(shown, NOW)
        self.assertEqual((meter.WORKING, 4), (row["state"], row["attempts"]))
        self.assertEqual("", meter.struggling(shown, NOW))
        self.assertEqual("● Requirements · 2m30s total · 4 runs", meter.stage_lines(row)[0])
        done = meter.stages(view(*shown["usage"]["accounting"]["attempts"][:3]), NOW)[0]
        self.assertEqual("✓ Requirements · 1m30s total · 3 runs", meter.stage_lines(done)[0])

    def test_a_retry_says_how_many_tries_the_core_still_allows(self):
        # The core stops a stage after three failures in a row of the same kind (progress.retry);
        # the person could not tell from "attempt 2" whether the next failure goes to the Investigator.
        shown = view(
            attempt("builder", "Builder", rejected=True),
            attempt("builder", "Builder", active=True, duration_seconds=5),
        )
        self.assertEqual("", meter.tries_left(shown), "an older core says nothing about the bound")
        shown["progress"] = {"retry": {"failures": 1, "limit": 3}}
        self.assertEqual("Builder · attempt 2 · 2 tries left · 5s · last answer rejected", meter.struggling(shown, NOW))
        shown["progress"]["retry"] = {"failures": 2, "limit": 3}
        self.assertEqual("last try", meter.tries_left(shown))
        shown["progress"]["retry"] = {"failures": 3, "limit": 3}
        self.assertEqual("no tries left", meter.tries_left(shown))
        for malformed in ({"failures": "1", "limit": 3}, {"failures": 1}, {"failures": -1, "limit": 3}, {"limit": 0}):
            with self.subTest(retry=malformed):
                shown["progress"]["retry"] = malformed
                self.assertEqual("", meter.tries_left(shown))

    def test_the_run_has_worked_for_every_attempts_duration_added_together(self):
        self.assertIsNone(meter.run_seconds(view(), NOW))
        self.assertEqual("", meter.worked_text(view(), NOW))
        shown = view(
            attempt("requirements_gather", "Requirements", duration_seconds=90),
            attempt("builder", "Builder", duration_seconds=600),
            attempt("builder", "Builder", active=True, duration_seconds=None, started_at="2026-11-02T01:05:00Z"),
        )
        self.assertEqual(990, meter.run_seconds(shown, NOW))  # the working attempt counts to now
        self.assertEqual("worked 16m30s", meter.worked_text(shown, NOW))
        unknown = view(attempt("tester", "Tester", active=True, duration_seconds=None, started_at="unknown"))
        self.assertIsNone(meter.run_seconds(unknown, NOW), "an attempt with no known time adds nothing")

    def test_a_first_attempt_at_work_is_not_struggling(self):
        shown = view(
            attempt("builder", "Builder", active=True, duration_seconds=None, started_at="2026-11-02T01:09:00Z")
        )
        self.assertEqual(meter.WORKING, meter.stages(shown, NOW)[0]["state"])
        self.assertEqual("", meter.struggling(shown, NOW))

    def test_a_failed_attempt_is_marked_with_its_reason(self):
        (row,) = meter.stages(view(attempt("tester", "Tester", timed_out=True)), NOW)
        head, detail, style = meter.stage_lines(row)
        self.assertTrue(head.startswith("✗ Tester"))
        self.assertIn("timed out", detail)
        self.assertEqual("bad", style)

    def test_execution_failure_is_not_mislabeled_as_a_rejected_answer(self):
        # The public ledger also marks an attempt rejected when no acceptable
        # answer exists because its provider timed out or was interrupted.
        for flags, expected in (
            ({"timed_out": True}, "timed out"),
            ({"interrupted": True}, "interrupted"),
            ({"timed_out": True, "interrupted": True}, "timed out"),
        ):
            with self.subTest(flags=flags):
                failed = attempt("tester", "Tester", rejected=True, exit_code=-15, **flags)
                (row,) = meter.stages(view(failed), NOW)
                self.assertEqual(expected, row["why"])
                self.assertNotIn("answer rejected", meter.stage_lines(row)[1])
                retry = attempt("tester", "Tester", active=True, duration_seconds=10)
                self.assertIn(f"last one {expected}", meter.struggling(view(failed, retry), NOW))

    def test_incomplete_tokens_are_marked_as_a_floor(self):
        partial = attempt("builder", "Builder")
        partial["attributed_tokens"]["output_tokens"] = {"value": None, "known": 50, "complete": False}
        _, detail, _ = meter.stage_lines(meter.stages(view(partial), NOW)[0])
        self.assertIn("1.1k+ tokens", detail)

    def test_no_attempts_no_stages(self):
        self.assertEqual([], meter.stages({}, NOW))
        self.assertIsNone(panel.stage_section({}, NOW))


class PanelTests(unittest.TestCase):
    def test_the_panel_lists_the_newest_stages_and_their_total(self):
        attempts = [attempt(f"stage{number}", f"Job {number}") for number in range(7)]
        section = panel.stage_section(view(*attempts), NOW)
        self.assertEqual("$0.42 · 184k tokens · worked 3m30s", section.summary)
        texts = [row.text for row in section.rows]
        self.assertEqual("3 earlier stages: /details", texts[0])
        self.assertEqual(["Job 6 · 30s", "gpt-x high · 1.2k tokens"], texts[-2:])
        self.assertEqual(1 + 2 * panel.STAGE_ROWS, len(texts))
        one_more = panel.stage_section(view(*attempts[: panel.STAGE_ROWS + 1]), NOW)
        self.assertEqual("1 earlier stage: /details", one_more.rows[0].text)

    def test_the_working_stage_names_its_model_on_its_own_row(self):
        shown = view(attempt("builder", "Builder", active=True, duration_seconds=12))
        rows = panel.stage_section(shown, NOW).rows
        self.assertEqual(("●", "Builder · 12s"), (rows[0].mark, rows[0].text))
        self.assertEqual("gpt-x high · 1.2k tokens", rows[1].text)

    def test_a_struggling_stage_is_flagged_in_now(self):
        shown = view(
            attempt("builder", "Builder", exit_code=1),
            attempt("builder", "Builder", active=True, duration_seconds=5),
        )
        now = next(section for section in panel.sections(shown) if section.title == "Now")
        self.assertEqual(("!", "warn"), (now.rows[0].mark, now.rows[0].style))
        self.assertIn("attempt 2", now.rows[0].text)
        self.assertIn("last one exit 1", now.rows[0].text)


class CalmPanelTests(unittest.TestCase):
    """The panel says each thing once: the spend in Stages, and delivery only once there is one to judge."""

    def test_the_stages_section_carries_the_spend_and_cap_so_usage_is_not_repeated(self):
        shown = view(attempt("builder", "Builder"), spent=1.73)
        shown["cost"]["cap_usd"] = 5
        titles = {section.title: section for section in panel.sections(shown)}
        self.assertEqual("$1.73 of $5.00 · 184k tokens · worked 30s", titles["Stages"].summary)
        self.assertNotIn("Usage", titles)
        # Without stage records the Usage section still says what the run spent.
        self.assertIn("Usage", {section.title for section in panel.sections(view(spent=1.73))})

    def test_verification_waits_until_the_run_has_a_delivery_to_judge(self):
        working = {**view(attempt("builder", "Builder")), "status": "RUNNING", "delivery": {"verified_complete": False}}
        self.assertNotIn("Verification", {section.title for section in panel.sections(working)})
        finished = {**working, "status": "TASK_COMPLETE", "done": True, "delivery": {"verified_complete": True}}
        section = next(section for section in panel.sections(finished) if section.title == "Verification")
        self.assertEqual("verified by core gate", section.summary)


class TotalTests(unittest.TestCase):
    def test_the_run_total_is_the_cores_own(self):
        self.assertEqual("$0.42 · 184k tokens", meter.total_text(meter.run_total(view())))
        self.assertEqual("$0.42+ · 184k+ tokens", meter.total_text(meter.run_total(view(complete=False))))
        # Live: the cost was fully accounted while some output tokens went unreported.
        partial = view()
        partial["usage"]["accounting"]["tokens"]["output_tokens"] = {"value": None, "known": 34_000, "complete": False}
        self.assertEqual("$0.42 · 184k+ tokens", meter.total_text(meter.run_total(partial)))
        self.assertIsNone(meter.run_total({}))

    def test_the_running_stages_spend_so_far_is_in_its_row_and_the_total(self):
        # Ankur, 2026-11-11: "$0+ · 1.9M+ tokens" while Requirements ran, since nothing had finished.
        running = attempt("requirements_gather", "Requirements", active=True, duration_seconds=None)
        shown = view(running, spent=0.0, complete=False)
        shown["cost"]["running_usd"] = 0.103
        self.assertEqual("$0.103+ · 184k+ tokens", meter.total_text(meter.run_total(shown)))
        self.assertEqual(0.103, meter.stages(shown, NOW)[0]["spent_usd"])
        # A second round adds the running attempt to the stage's finished spend.
        again = view(attempt("review_plan", "Plan Reviewer"), by_stage=[{"stage": "review_plan", "spent_usd": 0.2}])
        again["usage"]["accounting"]["attempts"].append(
            attempt("review_plan", "Plan Reviewer", active=True, duration_seconds=None)
        )
        again["cost"].update(running_usd=0.05, spent_usd=0.2)
        self.assertAlmostEqual(0.25, meter.stages(again, NOW)[0]["spent_usd"])
        self.assertAlmostEqual(0.25, meter.run_total(again)["spent_usd"])

    def test_the_session_adds_chats_without_counting_a_restored_steps_spend_twice(self):
        first = meter.run_total(view(spent=1.0))
        restored = meter.run_total(view(spent=1.5, inherited=1.0))
        self.assertEqual("$1.50 · 368k tokens", meter.total_text(meter.session_total([first, restored])))

    def test_a_chat_not_read_yet_makes_the_session_total_a_floor(self):
        total = meter.session_total([meter.run_total(view(spent=1.0)), None])
        self.assertEqual("$1.00+ · 184k+ tokens", meter.total_text(total))
        self.assertIsNone(meter.session_total([None, None]))

    def test_small_amounts_and_counts_read_briefly(self):
        self.assertEqual(
            ["$0", "<$0.001", "$0.00206", "$1.25", "$?"],
            [meter.money(value) for value in (0, 0.0004, 0.00206, 1.25, None)],
        )
        self.assertEqual(["950", "1.2k", "184k", "3.4M"], [meter.tokens(v) for v in (950, 1234, 184_000, 3_400_000)])
        self.assertEqual(["9s", "4m05s", "1h02m"], [meter.duration(v) for v in (9, 245, 3725)])


if __name__ == "__main__":
    unittest.main()


class CoordinatedTaskUsageTests(unittest.TestCase):
    def test_new_joint_identity_from_own_anchor_keeps_prior_task_spend(self):
        from tests.test_coordination_usage import ledger

        task = meter.TaskUsage()
        task.accept(self.snapshot())
        publication = {"integration": {"workspace": "/joint", "run_dir": "/joint/run"}}
        task.observe({**ledger("/source", 1, 100), "coordination": publication})
        self.assertEqual(6, task.total("/joint/run")["spent_usd"])
        self.assertFalse(task.total("/joint/run")["cost_complete"])
        task.observe(ledger("/joint/run", 0.5, 50))
        task.observe({**ledger("/source", 1, 100), "coordination": publication})
        self.assertEqual(6.5, task.total("/joint/run")["spent_usd"])
        self.assertEqual(650, task.total("/joint/run")["input_tokens"])
        task.observe({**ledger("/source", 1, 100), "coordination": {"integration": None}})
        self.assertEqual(6.5, task.total("/source")["spent_usd"], "Retired joint attempts remain accounted")
        task.accept({"anchor": "/next", "runs": [ledger("/next", 2, 200)]})
        task.observe({**ledger("/source", 1, 100), "coordination": publication})
        self.assertIsNone(task.total("/joint/run"), "A previous task cannot enroll a run in the new task")

    def test_unknown_views_and_member_offers_cannot_enroll_new_task_ledgers(self):
        from tests.test_coordination_usage import ledger

        task = meter.TaskUsage()
        for run in ("/source", "/stranger"):
            task.observe({**ledger(run, 1, 100), "coordination": {"integration": {"run_dir": "/untrusted"}}})
        task.accept(self.snapshot())
        for run in ("/api", "/stranger"):
            task.observe({**ledger(run, 2, 200), "coordination": {"integration": {"run_dir": "/untrusted"}}})
        self.assertIsNone(task.total("/untrusted"))
        self.assertEqual(6, task.total("/source")["spent_usd"])

    def snapshot(self):
        from tests.test_coordination_usage import ledger

        return {
            "version": 1,
            "anchor": "/source",
            "runs": [
                ledger("/source", 1, 100),
                ledger("/api", 2, 200),
                ledger("/history", 0, 0),
                ledger("/failed-joint", 3, 300, complete=False),
            ],
        }

    def test_switching_to_new_repository_or_back_keeps_task_usage(self):
        from tests.test_coordination_usage import ledger

        task = meter.TaskUsage()
        task.accept(self.snapshot())
        task.observe(ledger("/history", 0, 0, complete=False))
        for run in ("/source", "/api", "/history"):
            total = task.total(run)
            self.assertEqual(6, total["spent_usd"])
            self.assertEqual(600, total["input_tokens"])
            self.assertFalse(total["cost_complete"])
        task.observe(ledger("/history", 0.5, 50))
        task.observe(ledger("/history", 0.5, 50))
        self.assertEqual(6.5, task.total("/source")["spent_usd"])
        self.assertEqual(650, task.total("/source")["input_tokens"])
        self.assertIsNone(task.total("/unrelated"))

    def test_reopen_uses_all_saved_ledgers_and_does_not_add_inherited_cost_twice(self):
        from tests.test_coordination_usage import ledger

        task = meter.TaskUsage()
        snapshot = self.snapshot()
        snapshot["runs"].append(ledger("/restored", 7, 20, inherited=6))
        task.accept(snapshot)
        task.accept(snapshot)
        self.assertEqual(7, task.total("/source")["spent_usd"])
        self.assertEqual(620, task.total("/source")["input_tokens"])

    def test_unpriced_member_keeps_known_task_cost_as_incomplete(self):
        from tests.test_coordination_usage import ledger

        task = meter.TaskUsage()
        task.accept(self.snapshot())
        task.observe(ledger("/history", None, 10, complete=False))
        total = task.total("/history")
        self.assertEqual(6, total["spent_usd"])
        self.assertFalse(total["cost_complete"])
        self.assertFalse(total["tokens_complete"])
        self.assertIn("$6.00+", meter.total_text(total, show_pending=True))
        task.accept({"version": 1, "anchor": "/unknown", "runs": [ledger("/unknown", None, 0, complete=False)]})
        self.assertEqual("$? · tokens pending", meter.total_text(task.total("/unknown"), show_pending=True))
        self.assertIsNone(task.total("/source"))

    def test_active_stage_without_usage_says_tokens_pending(self):
        row = {
            "state": meter.WORKING,
            "job": "Planner",
            "seconds": 1,
            "attempts": 1,
            "route": "model high",
            "spent_usd": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "tokens_complete": False,
            "why": "",
        }
        _, detail, _ = meter.stage_lines(row)
        self.assertIn("tokens pending", detail)
