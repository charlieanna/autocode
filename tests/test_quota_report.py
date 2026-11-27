"""`autocode usage quota`: the plan-pool burn report built over the usage ledger (#4229)."""

from __future__ import annotations

import contextlib
import datetime as dt
import io
import json
import tempfile
import unittest
from pathlib import Path

import autocode_quota_report as quota_report
import autocode_usage_report as usage_report

NOW = dt.datetime(2026, 11, 20, 12, 0, tzinfo=dt.UTC)


def _attempt(provider: str, model: str, usd: float | None, *, role: str = "builder", rejected: bool = False) -> dict:
    return {
        "model": model,
        "role": role,
        "route_role": role,
        "reasoning_effort": "medium",
        "execution_identity": {"provider": provider},
        "reported_cost_usd": usd,
        "tokens": {},
        "rejected": rejected,
        "finished_at": (NOW - dt.timedelta(hours=1)).isoformat(),
    }


def _row(run: str, attempts: list[dict], *, provenance: str = "live", started: str = "2026-11-19T10:00:00+00:00"):
    return {
        "run": run,
        "status": "TASK_COMPLETE",
        "started_at": started,
        "updated_at": started,
        "evidence_provenance": provenance,
        "accounting": {"attempts": attempts},
    }


class BuildTests(unittest.TestCase):
    def test_counts_runs_and_the_fake_runs_it_leaves_out(self):
        rows = [
            _row("run-a", [_attempt("claude", "claude-opus-5-5", 2.0)]),
            _row("run-b", [_attempt("codex", "gpt-6-sol", 9.0)], provenance="fake"),
        ]

        payload = quota_report.build(rows, scope="workspace", since="2026-11-01", now=NOW)

        self.assertEqual(2, payload["total_runs"])
        self.assertEqual(1, payload["fake_runs_excluded"])
        self.assertEqual("workspace", payload["scope"])
        self.assertEqual("2026-11-01", payload["since"])
        self.assertEqual(["claude"], list(payload["pools"]))
        self.assertEqual(2.0, payload["pools"]["claude"]["burn_usd"])
        self.assertEqual(2.0, payload["pools"]["claude"]["windows"]["5h"])

    def test_says_the_figures_are_not_a_bill_or_a_plan_share(self):
        payload = quota_report.build([], scope="workspace", now=NOW)

        self.assertIn("not a bill", payload["basis"])
        self.assertIn("not a share of the plan", payload["basis"])


class FormatTests(unittest.TestCase):
    def test_shows_each_pool_with_burn_waste_and_windows(self):
        rows = [
            _row(
                "run-a",
                [
                    _attempt("claude", "claude-opus-5-5", 3.0, role="plan_reviewer"),
                    _attempt("claude", "claude-opus-5-5", 1.0, role="plan_reviewer", rejected=True),
                    _attempt("codex", "gpt-6-sol", 4.0),
                ],
            )
        ]

        text = quota_report.format_text(quota_report.build(rows, scope="workspace", now=NOW))

        self.assertIn("Quota pool report (scope: workspace, runs: 1, attempts: 3)", text)
        self.assertIn("claude pool: 2 attempts, $4.00 burned, $1.00 wasted (25%)", text)
        self.assertIn("last 5h $4.00 | last 7d $4.00", text)
        self.assertIn("codex pool: 1 attempt, $4.00 burned, $0.00 wasted (0%)", text)
        self.assertIn("plan_reviewer", text)
        self.assertIn("claude-opus-5-5", text)

    def test_marks_a_pool_total_as_a_floor_when_usage_was_not_observed(self):
        rows = [_row("run-a", [_attempt("claude", "claude-opus-5-5", 2.0), _attempt("claude", "mystery", None)])]

        text = quota_report.format_text(quota_report.build(rows, scope="workspace", now=NOW))

        self.assertIn("$2.00 burned (a floor: 1 attempt has no recorded usage)", text)

    def test_names_the_empty_case_instead_of_printing_zero_pools(self):
        text = quota_report.format_text(quota_report.build([], scope="all projects", since="2026-11-01", now=NOW))

        self.assertIn("Quota pool report (scope: all projects, since: 2026-11-01, runs: 0, attempts: 0)", text)
        self.assertIn("No live model attempts recorded in usage.jsonl.", text)
        self.assertNotIn("pool:", text)

    def test_columns_line_up_whatever_the_role_and_model_names(self):
        rows = [
            _row(
                "run-a",
                [
                    _attempt("claude", "claude-opus-5-5", 3.0, role="stuck_investigator"),
                    _attempt("claude", "gocode-anthropic/claude-opus-5-5", 1.0, role="plan"),
                ],
            )
        ]

        text = quota_report.format_text(quota_report.build(rows, scope="workspace", now=NOW))

        lines = text.splitlines()
        start = next(index for index, line in enumerate(lines) if "attempts" in line and "role" in line)
        table = lines[start : start + 3]
        self.assertTrue(any("stuck_investigator" in line for line in table))
        self.assertEqual(1, len({len(line) for line in table}))

    def test_explains_the_unit_once(self):
        rows = [_row("run-a", [_attempt("claude", "claude-opus-5-5", 2.0)])]

        text = quota_report.format_text(quota_report.build(rows, scope="workspace", now=NOW))

        self.assertEqual(1, text.count("API-equivalent"))


class CliTests(unittest.TestCase):
    def _project(self, rows: list[dict]) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        project = Path(tmp.name) / "project"
        ledger = project / ".autocode" / "usage.jsonl"
        ledger.parent.mkdir(parents=True)
        ledger.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")
        return project

    def _run(self, *argv: str) -> str:
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            self.assertEqual(0, usage_report.cli(list(argv)))
        return out.getvalue()

    def test_quota_prints_the_pool_report(self):
        project = self._project([_row("20261119-aaa", [_attempt("claude", "claude-opus-5-5", 2.0)])])

        text = self._run("quota", "--workspace", str(project))

        self.assertIn("claude pool: 1 attempt, $2.00 burned", text)

    def test_quota_json_is_structured(self):
        project = self._project([_row("20261119-aaa", [_attempt("codex", "gpt-6-sol", 5.0)])])

        payload = json.loads(self._run("quota", "--workspace", str(project), "--json"))

        self.assertEqual(1, payload["schema"])
        self.assertEqual(5.0, payload["pools"]["codex"]["burn_usd"])
        self.assertEqual("workspace", payload["scope"])

    def test_since_keeps_only_runs_started_on_or_after_the_date(self):
        project = self._project(
            [
                _row("20261101-old", [_attempt("claude", "claude-opus-5-5", 7.0)], started="2026-11-01T10:00:00+00:00"),
                _row("20261119-new", [_attempt("claude", "claude-opus-5-5", 2.0)]),
            ]
        )

        payload = json.loads(self._run("quota", "--workspace", str(project), "--since", "2026-11-10", "--json"))

        self.assertEqual(1, payload["total_runs"])
        self.assertEqual("2026-11-10", payload["since"])
        self.assertEqual(2.0, payload["pools"]["claude"]["burn_usd"])

    def test_an_empty_ledger_says_so(self):
        project = self._project([])

        self.assertIn("No live model attempts recorded", self._run("quota", "--workspace", str(project)))

    def test_a_trailing_project_path_still_works_like_the_other_actions(self):
        self.assertEqual(
            ["quota", "--workspace", "/tmp/project"],
            usage_report._normalize_legacy_path(["quota", "/tmp/project"]),
        )

    def test_list_and_report_are_unchanged(self):
        project = self._project([_row("20261119-aaa", [_attempt("claude", "claude-opus-5-5", 2.0)])])

        self.assertIn("20261119-aaa", self._run("list", "--workspace", str(project)))
        self.assertIn("Usage outcome report", self._run("report", "--workspace", str(project)))


if __name__ == "__main__":
    unittest.main()
