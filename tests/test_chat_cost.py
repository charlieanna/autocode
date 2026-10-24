"""Cost and actual/saved routes are rendered from the public view, without recomputation."""

import copy
import json
import os
import sys
import unittest

import autocode_chat_panel as panel

from tests import ROOT
from tests.terminal_screen import TerminalScreen, require_tmux


def quantity(value, *, known=None, complete=True):
    return {"value": value, "known": value if known is None else known, "complete": complete}


def public_view():
    return {
        "status": "RUNNING",
        "explanation": {"explanation": "The Tester is checking the fix."},
        "cost": {
            "spent_usd": 1.25,
            "estimated_usd": 1.25,
            "reported_usd": None,
            "complete": False,
            "unpriced_stages": 2,
            "cap_usd": 3,
            "prices_as_of": "2026-10-17",
            "breakdown": {
                "by_billing": [{"billing": "subscription", "estimated_usd": 1.25, "spent_usd": 1.25}],
                "unpriced": {"usage_missing": 1, "rate_unavailable": 1, "models_without_rate": ["unknown/model"]},
            },
        },
        "usage": {
            "accounting": {
                "attempt_count": 3,
                "active_attempts": 1,
                "parallel_workers_pending": ["worker-2"],
                "complete": True,
                "tokens": {
                    "input_tokens": quantity(2500),
                    "cached_input_tokens": quantity(1800),
                    "output_tokens": quantity(200),
                    "reasoning_output_tokens": quantity(None, known=50, complete=False),
                },
                "cost": {
                    "reported_usd": quantity(None, known=0, complete=False),
                    "historical_estimated_usd": quantity(None, known=0.9, complete=False),
                    "subscription_invoice_usd": None,
                },
                "attempts": [
                    {
                        "attempt_id": "001/validator-01",
                        "job": "Tester",
                        "launch_route": {"model": "openai/old-model", "engine": "opencode", "reasoning_effort": "high"},
                        "rejected": True,
                        "tokens": {"input_tokens": 1000, "output_tokens": 100},
                    },
                    {
                        "attempt_id": "001/validator-report-repair-01",
                        "job": "Tester (report repair)",
                        "launch_route": {"model": "openai/old-model", "reasoning_effort": "high"},
                        "report_only": True,
                        "tokens": {"input_tokens": 1000, "output_tokens": 100},
                    },
                    {
                        "attempt_id": "002/builder-01",
                        "job": "Builder",
                        "worker_attempt": "worker-1",
                        "launch_route": {"model": "openai/worker-model"},
                        "active": True,
                        "tokens": {"input_tokens": 500},
                    },
                ],
            }
        },
        "routes": {
            "sol": {
                "job": "Tester",
                "model": "zai-coding-plan/glm-5.3",
                "reasoning_effort": "low",
                "provider": "kilocode",
            }
        },
    }


def details(view):
    return panel.text("/details", view, panel.StageLog())


class CostDisplayTests(unittest.TestCase):
    def test_panel_shows_accounted_subtotal_and_coverage_gaps(self):
        section = next(s for s in panel.sections(public_view()) if s.title == "Usage")
        shown = section.summary + "\n" + "\n".join(row.text for row in section.rows)
        self.assertIn("$1.25", shown)
        self.assertIn("incomplete", shown)
        self.assertIn("2 unpriced", shown)
        self.assertIn("/details", shown)

    def test_details_keep_pricing_bases_and_billing_meaning_separate(self):
        shown = details(public_view())
        self.assertIn("Accounted subtotal: $1.25", shown)
        self.assertIn("Dated-table estimate: $1.25", shown)
        self.assertIn("Historical comparison estimate: $0.90 known", shown)
        self.assertIn("Provider-reported: $0.00 known", shown)
        self.assertIn("Subscription", shown)
        self.assertIn("not an invoice", shown)
        self.assertIn("1 missing usage", shown)
        self.assertIn("1 missing rate", shown)
        self.assertNotIn("remaining budget", shown.lower())

    def test_attempt_routes_are_not_replaced_by_saved_future_routes(self):
        shown = details(public_view())
        self.assertIn("Actual attempts", shown)
        self.assertIn("openai/old-model", shown)
        self.assertIn("high", shown)
        self.assertIn("Saved routes for future attempts", shown)
        self.assertIn("zai-coding-plan/glm-5.3", shown)
        self.assertIn("low", shown)
        self.assertIn("provider unreported", shown)

    def test_attempts_and_subsets_are_not_added_to_the_core_totals(self):
        shown = details(public_view())
        self.assertIn("Input: 2,500", shown)
        self.assertIn("Output: 200", shown)
        self.assertIn("Cache reads (included in input): 1,800", shown)
        self.assertIn("Reasoning (included in output): 50 known", shown)
        self.assertIn("report repair", shown)
        self.assertIn("rejected", shown)
        self.assertIn("worker-1", shown)
        self.assertIn("1 active", shown)
        self.assertIn("1 worker pending", shown)
        self.assertNotIn("Input: 4,300", shown)

    def test_attempt_costs_prefer_the_cores_deduplicated_attribution(self):
        current = public_view()
        attempt = current["usage"]["accounting"]["attempts"][0]
        attempt.update(reported_cost_usd=1.0, attributed_reported_usd=quantity(0.25))
        shown = details(current)
        self.assertIn("Provider-reported (attributed): $0.25", shown)
        self.assertNotIn("Provider-reported (attempt observation): $1.00", shown)

    def test_zero_and_unknown_never_mean_a_free_or_complete_bill(self):
        current = public_view()
        current["cost"].update(spent_usd=0, estimated_usd=None, reported_usd=None)
        shown = details(current)
        self.assertIn("Accounted subtotal: $0.00", shown)
        self.assertIn("incomplete", shown)
        self.assertIn("Dated-table estimate: unknown", shown)
        self.assertIn("not an invoice", shown)

    def test_absent_fields_do_not_inherit_an_earlier_views_values(self):
        first = public_view()
        before = copy.deepcopy(first)
        details(first)
        self.assertEqual(before, first)
        later = {"cost": {"complete": False}, "routes": {"sol": {"job": "Tester"}}}
        shown = details(later)
        self.assertIn("unknown", shown)
        self.assertNotIn("$1.25", shown)
        self.assertNotIn("openai/old-model", shown)
        self.assertNotIn("zai-coding-plan/glm-5.3", shown)
        self.assertEqual("AutoCode has said nothing more yet.", details({}))

    def test_complete_cost_does_not_claim_complete_running_worker_coverage(self):
        current = public_view()
        current["cost"].update(complete=True, unpriced_stages=0)
        self.assertIn("Accounted subtotal", details(current))
        self.assertIn("incomplete", details(current))

    def test_only_complete_coverage_is_labelled_an_accounted_total(self):
        current = public_view()
        current["cost"].update(complete=True, unpriced_stages=0)
        current["usage"]["accounting"].update(active_attempts=0, parallel_workers_pending=[])
        self.assertIn("Accounted total: $1.25", details(current))
        del current["usage"]["accounting"]
        self.assertIn("Accounted subtotal: $1.25", details(current))
        self.assertIn("attempt coverage unavailable", details(current))

    def test_invalid_amounts_remain_unknown_instead_of_crashing_or_becoming_zero(self):
        for value in (float("inf"), float("nan"), -1, True, 10**400, "1.25"):
            with self.subTest(value=str(value)[:20]):
                current = public_view()
                current["cost"].update(spent_usd=value, estimated_usd=value)
                shown = details(current)
                self.assertIn("Accounted subtotal: unknown", shown)
                self.assertIn("Dated-table estimate: unknown", shown)

    def test_escalations_and_recorded_route_assignments_show_their_core_reason(self):
        current = public_view()
        current["escalations"] = [
            {
                "job": "Tester",
                "from": {"model": "old/model"},
                "to": {"model": "new/model"},
                "trigger": "rejected_output",
                "outcome": "failed_again",
            }
        ]
        current["route_assignments"] = [
            {"job": "Tester", "from": "new/model", "to": "saved/model", "via": "resume_flag", "actor": "user_cli"}
        ]
        shown = details(current)
        self.assertIn("old/model", shown)
        self.assertIn("rejected output", shown)
        self.assertIn("Outcome: failed again", shown)
        self.assertIn("resume flag", shown)
        self.assertIn("saved/model", shown)

    def test_legacy_tokens_are_labelled_as_saved_stage_subtotals(self):
        current = {"usage": {"tokens": {"input_tokens": 100}, "cost_usd": {"reported": 0}}}
        shown = details(current)
        self.assertIn("Saved-stage", shown)
        self.assertIn("all-attempt coverage unavailable", shown)
        self.assertIn("Input: 100", shown)

    @unittest.skipUnless(os.name == "posix", "the TUI uses a POSIX curses terminal")
    def test_long_route_ids_are_readable_through_details_at_80_columns(self):
        import autocode_chat_tui as tui

        current = public_view()
        model = "provider/" + "long-model-name-" * 20
        current["routes"]["sol"]["model"] = model
        transcript = tui.Transcript()
        transcript.info("/details", details(current))
        rendered = transcript.lines(80)
        self.assertTrue(all(tui.display_width(line) <= 80 for _, line in rendered))
        self.assertIn(model, "".join(line for _, line in rendered).replace(" ", ""))


@unittest.skipUnless(os.name == "posix", "the TUI uses a POSIX curses terminal")
class CostTerminalTests(unittest.TestCase):
    def test_existing_panel_and_details_work_at_wide_and_80_column_sizes_without_a_provider(self):
        tmux = require_tmux(self)
        program = (
            "import json,sys; sys.path.insert(0,sys.argv[1]); "
            "from autocode_chat_tui import ChatTUI\n"
            "with ChatTUI() as ui:\n"
            " ui.observe(json.loads(sys.argv[2]))\n"
            " ui.say('AutoCode: Offline public accounting fixture.')\n"
            " ui.ask('Fixture ready. Type /details to inspect.')\n"
        )
        for cols in (120, 80):
            with (
                self.subTest(cols=cols),
                TerminalScreen(
                    [sys.executable, "-c", program, str(ROOT / "tools"), json.dumps(public_view())],
                    cwd=ROOT,
                    env=os.environ,
                    cols=cols,
                    rows=30,
                    tmux=tmux,
                ) as screen,
            ):
                shown = screen.wait_for("Fixture ready")
                if cols == 120:
                    self.assertIn("$1.25 accounted", shown)
                    self.assertIn("2 unpriced", shown)
                screen.type("/details ")
                screen.keys("Enter")
                screen.wait_for("Saved routes for future attempts")
                for _ in range(20):
                    screen.keys("PPage")
                shown = screen.wait_for("Accounted subtotal: $1.25")
                self.assertIn("Dated-table estimate: $1.25", shown)
                self.assertTrue(all(len(line) <= cols for line in shown.splitlines()))
                screen.keys("Enter")
                self.assertEqual(0, screen.exit_status())
