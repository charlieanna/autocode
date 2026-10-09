"""Offline campaign accounting, evidence integrity, and live-report integration."""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

import score_issue_campaign as campaign
import live_trial
import live_scenarios
import score_autocode_run as costs


def sha(text):
    return hashlib.sha256(text.encode()).hexdigest()


class CampaignTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / "campaign.json"
        self.sequence = 0
        self.manifest = {"schema_version": 1, "name": "Development baseline", "profile": "model-pair",
                         "execution_kind": "live", "workload_kind": "repository", "split": "development",
                         "cases": [self.case("issue-1")]}

    def case(self, ident):
        return {"id": ident, "scenario": ident, "baseline_content_sha256": sha("base-" + ident),
                "task_sha256": sha("task-" + ident), "oracle_sha256": sha("oracle"),
                "required_checks": ["regression", "compatibility"], "attempts": []}

    def report(self, case=None, verdict="PASS", cost=2):
        self.sequence += 1
        case = case or self.manifest["cases"][0]
        return {"report_version": 2, "attempt_id": f"attempt-{self.sequence}",
                "run_identity": sha(f"run-{self.sequence}"), "profile": "model-pair",
                "profile_detail": {"provider": "opencode"}, "scenario": case["scenario"],
                "oracle_sha256": case["oracle_sha256"], "candidate_revision": sha("candidate"),
                "runner_status": "TASK_COMPLETE" if verdict == "PASS" else "PAUSED_RATE_LIMIT",
                "verdict": verdict, "checks": [{"name": name, "ok": True} for name in case["required_checks"]],
                "measurement": {"schema_version": 1, "execution_kind": "live", "workload_kind": "repository",
                    "baseline_content_sha256": case["baseline_content_sha256"], "task_sha256": case["task_sha256"],
                    "elapsed_seconds": 5,
                    "usage": {"estimated_api_equivalent_usd": cost, "known_estimated_api_equivalent_usd": cost,
                              "pricing_basis": {"kind": "test-only", "input": 1}}}}

    def add(self, report, case=None, **metadata):
        case = case or self.manifest["cases"][0]
        path = self.root / f"report-{len(list(self.root.glob('report-*.json')))}.json"
        path.write_text(json.dumps(report))
        entry = {"report": path.name, "sha256": campaign.digest(path.read_bytes()), **metadata}
        case["attempts"].append(entry)
        return path

    def score(self):
        self.path.write_text(json.dumps(self.manifest))
        return campaign.score_campaign(self.path)

    def test_all_attempts_are_charged_but_each_case_counts_once(self):
        self.add(self.report(verdict="HONEST_BLOCKER", cost=3), assistance="unassisted", human_seconds=0)
        self.add(self.report(cost=2), assistance="unassisted", human_seconds=0)
        self.add(self.report(cost=1), assistance="assisted", human_seconds=60)
        self.manifest["cases"].append(self.case("issue-2"))
        report = self.score()
        s = report["summary"]
        self.assertEqual(3, s["attempts"])
        self.assertEqual(1, s["verified_cases"])
        self.assertEqual(.5, s["verified_case_rate"])
        self.assertEqual(1, s["unattempted_cases"])
        self.assertEqual(6, s["cost_per_verified_case_usd"])
        self.assertEqual(60, s["human_seconds"])
        self.assertEqual(15, s["elapsed_seconds"])
        self.assertEqual(1, s["failure_states"]["PAUSED_RATE_LIMIT"])

    def test_unknown_cost_is_not_zero_or_omitted(self):
        self.add(self.report(cost=2))
        unknown = self.report(verdict="ERROR", cost=None)
        unknown["measurement"]["usage"]["known_estimated_api_equivalent_usd"] = .5
        self.add(unknown)
        s = self.score()["summary"]
        self.assertIsNone(s["estimated_api_equivalent_usd"])
        self.assertIsNone(s["cost_per_verified_case_usd"])
        self.assertEqual(2.5, s["known_estimated_api_equivalent_usd"])
        self.assertEqual(1, s["unpriced_attempts"])
        self.assertIsNone(s["human_seconds"])
        self.assertEqual(2, s["unknown_human_time_attempts"])
        self.assertEqual(0, s["unassisted_verified_cases"])

    def test_zero_cost_and_zero_human_time_are_preserved(self):
        self.add(self.report(cost=0), assistance="unassisted", human_seconds=0)
        s = self.score()["summary"]
        self.assertEqual(0, s["cost_per_verified_case_usd"])
        self.assertEqual(0, s["human_seconds"])

    def test_zero_success_has_no_cost_per_fix(self):
        self.add(self.report(verdict="HONEST_BLOCKER"))
        s = self.score()["summary"]
        self.assertEqual(0, s["verified_cases"])
        self.assertEqual(2, s["estimated_api_equivalent_usd"])
        self.assertIsNone(s["cost_per_verified_case_usd"])

    def test_missing_report_stays_in_attempt_denominator(self):
        self.manifest["cases"][0]["attempts"].append({"report": "missing.json", "sha256": sha("missing")})
        s = self.score()["summary"]
        self.assertEqual(1, s["attempts"])
        self.assertEqual({"UNVERIFIED": 1}, s["attempt_outcomes"])
        self.assertIsNone(s["estimated_api_equivalent_usd"])

    def test_pinned_report_change_cannot_keep_its_pass(self):
        path = self.add(self.report())
        path.write_text(path.read_text() + "\n")
        report = self.score()
        self.assertEqual(0, report["summary"]["verified_cases"])
        self.assertIn("SHA-256 mismatch", report["attempts"][0]["errors"][0])

    def test_copy_or_second_snapshot_of_same_run_cannot_inflate_metrics(self):
        for identity in ("content", "attempt_id", "run_identity"):
            with self.subTest(identity=identity):
                self.manifest["cases"][0]["attempts"] = []
                first, second = self.report(), self.report()
                if identity == "content":
                    second = first
                else:
                    second[identity] = first[identity]
                self.add(first)
                self.add(second)
                with self.assertRaisesRegex(campaign.CampaignError, "duplicate"):
                    self.score()

    def test_duplicate_cases_are_rejected_even_with_different_display_ids(self):
        second = copy.deepcopy(self.manifest["cases"][0])
        second["id"] = "renamed"
        self.manifest["cases"].append(second)
        with self.assertRaisesRegex(campaign.CampaignError, "duplicate case"):
            self.score()

    def test_stale_or_cross_cohort_passes_are_unverified(self):
        for key in ("baseline_content_sha256", "task_sha256", "oracle_sha256", "profile",
                    "execution_kind", "workload_kind"):
            with self.subTest(key=key):
                self.manifest["cases"][0]["attempts"] = []
                report = self.report()
                target = report if key in ("oracle_sha256", "profile") else report["measurement"]
                target[key] = "different"
                self.add(report)
                result = self.score()
                self.assertEqual(0, result["summary"]["verified_cases"])
                self.assertEqual("UNVERIFIED", result["attempts"][0]["outcome"])

    def test_fixture_provider_cannot_masquerade_as_live(self):
        report = self.report()
        report["profile_detail"]["provider"] = "fixture"
        self.add(report)
        self.assertEqual(0, self.score()["summary"]["verified_cases"])

    def test_legacy_report_is_not_silently_qualified(self):
        report = self.report()
        report.pop("measurement")
        report.pop("report_version")
        self.add(report)
        result = self.score()
        self.assertEqual("UNVERIFIED", result["attempts"][0]["outcome"])
        self.assertIsNone(result["summary"]["estimated_api_equivalent_usd"])

    def test_missing_duplicate_or_false_oracle_checks_cannot_pass(self):
        for checks, expected in (([], "UNVERIFIED"),
                                 ([{"name": "regression", "ok": True}], "UNVERIFIED"),
                                 ([{"name": "regression", "ok": True}] * 2, "UNVERIFIED"),
                                 ([{"name": "regression", "ok": False}, {"name": "compatibility", "ok": True}], "FALSE_COMPLETE"),
                                 ([{"name": "regression", "ok": 1}, {"name": "compatibility", "ok": True}], "FALSE_COMPLETE")):
            with self.subTest(checks=checks):
                self.manifest["cases"][0]["attempts"] = []
                report = self.report()
                report["checks"] = checks
                self.add(report)
                result = self.score()
                self.assertEqual(expected, result["attempts"][0]["outcome"])

    def test_complete_claim_without_candidate_or_with_blocker_is_not_verified(self):
        for updates in ({"candidate_revision": None}, {"run_identity": None},
                        {"blocker": "needs input"}, {"runner_status": "RUNNING"}):
            with self.subTest(updates=updates):
                self.manifest["cases"][0]["attempts"] = []
                report = self.report()
                report.update(updates)
                self.add(report)
                self.assertEqual(0, self.score()["summary"]["verified_cases"])

    def test_incompatible_pricing_bases_are_not_summed(self):
        self.add(self.report())
        report = self.report()
        report["measurement"]["usage"]["pricing_basis"]["input"] = 10
        self.add(report)
        s = self.score()["summary"]
        self.assertFalse(s["pricing_comparable"])
        self.assertIsNone(s["estimated_api_equivalent_usd"])
        self.assertIsNone(s["known_estimated_api_equivalent_usd"])

    def test_inconsistent_or_invalid_costs_cannot_look_cheap(self):
        for value in (-1, True, "0", float("nan"), float("inf"), 0):
            with self.subTest(value=value):
                self.manifest["cases"][0]["attempts"] = []
                report = self.report()
                report["measurement"]["usage"]["estimated_api_equivalent_usd"] = value
                self.add(report)
                result = self.score()
                self.assertIsNone(result["summary"]["estimated_api_equivalent_usd"])
                self.assertEqual("UNVERIFIED", result["attempts"][0]["outcome"])

    def test_manifest_invalid_numbers_and_duplicate_keys_are_rejected(self):
        self.add(self.report(), human_seconds=-1)
        with self.assertRaises(campaign.CampaignError):
            self.score()
        self.path.write_text('{"schema_version": 1, "schema_version": 1}')
        with self.assertRaisesRegex(campaign.CampaignError, "duplicate JSON key"):
            campaign.score_campaign(self.path)

    def test_empty_attempt_set_is_reported_as_unattempted_not_success(self):
        s = self.score()["summary"]
        self.assertEqual(1, s["unattempted_cases"])
        self.assertEqual(0, s["verified_case_rate"])
        self.assertIsNone(s["cost_per_verified_case_usd"])

    def test_report_generation_preserves_input_bytes_and_supports_cli_modules(self):
        report_path = self.add(self.report())
        expected = self.score()
        before = {p: p.read_bytes() for p in (self.path, report_path)}
        repo = Path(__file__).resolve().parents[1]
        for entry in (["-m", "tools.score_issue_campaign"], [str(repo / "tools/score_issue_campaign.py")]):
            output = self.root / "summary.json"
            proc = subprocess.run([sys.executable, *entry, str(self.path), "--json-out", str(output)],
                                  cwd=repo, capture_output=True, text=True, timeout=10)
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual(expected, json.loads(output.read_text()))
        self.assertEqual(before, {p: p.read_bytes() for p in before})

    def test_cli_cannot_overwrite_inputs_or_use_same_output_twice(self):
        path = self.add(self.report())
        self.score()
        before = path.read_bytes()
        for flags in (("--out", str(path)), ("--json-out", str(self.path)),
                      ("--out", str(self.root / "x"), "--json-out", str(self.root / "x"))):
            with self.subTest(flags=flags), mock.patch("sys.stderr"):
                with self.assertRaises(SystemExit) as error:
                    campaign.main([str(self.path), *flags])
                self.assertEqual(2, error.exception.code)
        self.assertEqual(before, path.read_bytes())


class ReportCaptureTest(unittest.TestCase):
    def test_fresh_seed_commit_metadata_does_not_change_case_content_identity(self):
        reports = []
        with tempfile.TemporaryDirectory() as temp:
            for revision in (sha("first-commit"), sha("second-commit")):
                folder = Path(temp) / revision
                folder.mkdir()
                snapshot = {"revision": revision, "files": {"greet.py": sha("same contents")}}
                with mock.patch.object(live_trial, "Bundle", return_value=mock.Mock(dir=folder)), \
                        mock.patch.object(live_trial, "make_workspace", return_value=folder), \
                        mock.patch.object(live_trial.util, "snapshot", return_value=snapshot), \
                        mock.patch.object(live_trial, "drive", return_value={"state": {"status": "TASK_COMPLETE"}}), \
                        mock.patch.object(live_trial, "judge", return_value=live_scenarios.OracleResult("PASS", "fixture", [])), \
                        mock.patch.object(live_trial, "source_revision", return_value={}), mock.patch("sys.stdout"):
                    self.assertEqual(0, live_trial.main(["LIVE-01", "--workspace", str(folder)]))
                reports.append(json.loads((folder / "live-trial.json").read_text())["measurement"])
        self.assertEqual(reports[0]["baseline_content_sha256"], reports[1]["baseline_content_sha256"])
        self.assertNotEqual(reports[0]["baseline_revision"], reports[1]["baseline_revision"])
        self.assertGreaterEqual(reports[0]["elapsed_seconds"], 0)

    def test_failure_report_keeps_baseline_and_unknown_usage(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            snapshot = {"revision": sha("commit"), "files": {"file": sha("content")}}
            with mock.patch.object(live_trial, "Bundle", return_value=mock.Mock(dir=folder)), \
                    mock.patch.object(live_trial, "make_workspace", return_value=folder), \
                    mock.patch.object(live_trial.util, "snapshot", return_value=snapshot), \
                    mock.patch.object(live_trial, "drive", side_effect=live_trial.TrialError("setup failed")), \
                    mock.patch.object(live_trial, "_discover_run_dir", return_value=None), \
                    mock.patch.object(live_trial, "source_revision", return_value={}), mock.patch("sys.stderr"):
                self.assertEqual(1, live_trial.main(["LIVE-01", "--workspace", str(folder)]))
            report = json.loads((folder / "live-trial.json").read_text())
            self.assertEqual("ERROR", report["verdict"])
            self.assertEqual(live_trial.util.digest(snapshot["files"]), report["measurement"]["baseline_content_sha256"])
            self.assertIsNone(report["measurement"]["usage"]["estimated_api_equivalent_usd"])

    def test_report_embeds_usage_without_reading_or_mutating_workspace(self):
        with tempfile.TemporaryDirectory() as temp:
            bundle = mock.Mock(dir=Path(temp))
            state = {"status": "TASK_COMPLETE", "stages": [
                {"stage": "terra", "command": ["cmd", "--model", "zai-coding-plan/glm-5.3"],
                 "metrics": {"provider_tokens": {"input_tokens": 10, "output_tokens": 20}}}]}
            run = {"state": state, "run_dir": Path(temp) / "deleted-run", "baseline_content_sha256": sha("base"),
                   "elapsed_seconds": 3, "workload_kind": "synthetic"}
            before = copy.deepcopy(run)
            with mock.patch.object(live_trial, "source_revision", return_value={"commit": "test"}):
                path = live_trial.write_report(bundle, "LIVE-01", live_scenarios.scenario("LIVE-01"),
                    "fixture", {"provider": "fixture"}, live_scenarios.OracleResult("PASS", "fixture", []), run)
            report = json.loads(path.read_text())
            self.assertEqual(before, run)
            self.assertEqual(costs.usage_summary(state), report["measurement"]["usage"])
            self.assertEqual("fixture", report["measurement"]["execution_kind"])
            self.assertEqual(sha("base"), report["measurement"]["baseline_content_sha256"])


if __name__ == "__main__":
    unittest.main()
