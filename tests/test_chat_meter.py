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
            attempt("terra", "Builder", model="gpt-terra", effort="medium"),
            by_stage=[{"stage": "terra", "spent_usd": 0.12}],
        )
        rows = meter.stages(shown, NOW)
        self.assertEqual(["Requirements", "Builder"], [row["job"] for row in rows])
        head, detail, style = meter.stage_lines(rows[1])
        self.assertEqual("✓ Builder · 30s · $0.12", head)
        self.assertEqual("  gpt-terra medium · 1.2k tokens", detail)
        self.assertEqual("good", style)

    def test_a_stage_with_no_model_shows_no_cost_or_model_row(self):
        plain = attempt("orchestrator", "Orchestrator", launch_route={}, attributed_tokens={})
        shown = view(plain, by_stage=[{"stage": "orchestrator", "spent_usd": 0}])
        head, detail, _ = meter.stage_lines(meter.stages(shown, NOW)[0])
        self.assertEqual(("✓ Orchestrator · 30s", ""), (head, detail.strip()))
        self.assertEqual(1, len(panel.stage_section(shown, NOW).rows))

    def test_a_retry_is_struggling_and_says_why_the_last_attempt_failed(self):
        shown = view(
            attempt("terra", "Builder", rejected=True),
            attempt("terra", "Builder", active=True, duration_seconds=None, started_at="2026-11-02T01:06:00Z"),
        )
        (row,) = meter.stages(shown, NOW)
        self.assertEqual((meter.RETRYING, 2, 270.0), (row["state"], row["attempts"], row["seconds"]))
        self.assertEqual("Builder · attempt 2 · 4m30s · last one answer rejected", meter.struggling(shown, NOW))

    def test_a_first_attempt_at_work_is_not_struggling(self):
        shown = view(attempt("terra", "Builder", active=True, duration_seconds=None, started_at="2026-11-02T01:09:00Z"))
        self.assertEqual(meter.WORKING, meter.stages(shown, NOW)[0]["state"])
        self.assertEqual("", meter.struggling(shown, NOW))

    def test_a_failed_attempt_is_marked_with_its_reason(self):
        (row,) = meter.stages(view(attempt("sol", "Tester", timed_out=True)), NOW)
        head, detail, style = meter.stage_lines(row)
        self.assertTrue(head.startswith("✗ Tester"))
        self.assertIn("timed out", detail)
        self.assertEqual("bad", style)

    def test_incomplete_tokens_are_marked_as_a_floor(self):
        partial = attempt("terra", "Builder")
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
        self.assertEqual("$0.42 · 184k tokens", section.summary)
        texts = [row.text for row in section.rows]
        self.assertEqual("3 earlier stages: /details", texts[0])
        self.assertEqual(["Job 6 · 30s", "gpt-x high · 1.2k tokens"], texts[-2:])
        self.assertEqual(1 + 2 * panel.STAGE_ROWS, len(texts))
        one_more = panel.stage_section(view(*attempts[: panel.STAGE_ROWS + 1]), NOW)
        self.assertEqual("1 earlier stage: /details", one_more.rows[0].text)

    def test_the_working_stage_names_its_model_on_its_own_row(self):
        shown = view(attempt("terra", "Builder", active=True, duration_seconds=12))
        rows = panel.stage_section(shown, NOW).rows
        self.assertEqual(("●", "Builder · 12s"), (rows[0].mark, rows[0].text))
        self.assertEqual("gpt-x high · 1.2k tokens", rows[1].text)

    def test_a_struggling_stage_is_flagged_in_now(self):
        shown = view(
            attempt("terra", "Builder", exit_code=1),
            attempt("terra", "Builder", active=True, duration_seconds=5),
        )
        now = next(section for section in panel.sections(shown) if section.title == "Now")
        self.assertEqual(("!", "warn"), (now.rows[0].mark, now.rows[0].style))
        self.assertIn("attempt 2", now.rows[0].text)
        self.assertIn("last one exit 1", now.rows[0].text)


class TotalTests(unittest.TestCase):
    def test_the_run_total_is_the_cores_own(self):
        self.assertEqual("$0.42 · 184k tokens", meter.total_text(meter.run_total(view())))
        self.assertEqual("$0.42 · 184k tokens+", meter.total_text(meter.run_total(view(complete=False))))
        self.assertIsNone(meter.run_total({}))

    def test_the_session_adds_chats_without_counting_a_restored_steps_spend_twice(self):
        first = meter.run_total(view(spent=1.0))
        restored = meter.run_total(view(spent=1.5, inherited=1.0))
        self.assertEqual("$1.50 · 368k tokens", meter.total_text(meter.session_total([first, restored])))

    def test_a_chat_not_read_yet_makes_the_session_total_a_floor(self):
        total = meter.session_total([meter.run_total(view(spent=1.0)), None])
        self.assertEqual("$1.00 · 184k tokens+", meter.total_text(total))
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
