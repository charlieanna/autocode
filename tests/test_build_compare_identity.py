"""Comparison qualification through its public CLI/report entry points; no providers.

The CLI's source observer and scenario drive are scripted here. These tests do
not qualify a runtime checkout; the Linux repeated-greeting test does that.
"""

from __future__ import annotations

import contextlib
import copy
import dataclasses
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from harness import build_compare, catalog, profiles, verdict

from scenarios import run


class BuildComparisonIdentityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="comparison-identity-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.case = self.root / "case"
        for name in ("seed", "reference", "hidden", "broken/wrong"):
            (self.case / name).mkdir(parents=True)
        for name in ("scenario.toml", "oracle.py", "seed/app.py", "reference/app.py", "hidden/check.py"):
            (self.case / name).write_text(f"# {name}\n")
        self.scenario = catalog.Scenario(
            id="case",
            dir=self.case,
            title="case",
            category="greenfield",
            brief="An exact request.",
            requires=(),
            fake_check="true",
            max_steps=4,
            timeout_minutes=2,
        )
        self.revision = {"commit": "a" * 40, "branch": "fixture-observer", "dirty": False}
        self.observer = Mock(side_effect=lambda **kwargs: copy.deepcopy(self.revision))
        self.profile = profiles.resolve("build-comparison")
        self.drive_count = 0

    def compare(self, change=None, *, live=False, command=None, repeats=1, solution="reference"):
        output = self.root / f"comparison-{self.drive_count}"

        def drive(scenario, options, *, extra_flags):
            self.assertEqual(build_compare.VARIANTS[options.attempt_context["variant"]], extra_flags)
            self.drive_count += 1
            evidence = options.out / f"run-{self.drive_count}"
            evidence.mkdir()
            row = {
                "scenario": scenario.id,
                "mode": "build-comparison"
                if live
                else "fake"
                if solution == "reference"
                else f"fake-{Path(solution).name}",
                "autocode": {"commit": "a" * 40, "dirty": False},
                "evidence": str(evidence),
                "verdict": verdict.PASS,
                "oracle_passed": True,
                "summary": "scripted oracle passed",
                "checks": [{"name": "behavior", "ok": True}],
                "wall_seconds": 2,
                "metrics": {"model_stages": 1, "report_repairs": 0},
            }
            if live:
                row["profile"] = copy.deepcopy(self.profile)
            if change:
                change(row, scenario, options)
            return row

        args = ["build-compare", "case", "--repeats", str(repeats), "--out", str(output)]
        if live:
            args += [
                "--profile",
                "build-comparison",
                "--i-authorize-live-model-spend",
                "--rate-card",
                str(Path(run.__file__).parent / "api-pricing-2026-10-02.json"),
            ]
        else:
            args += ["--fake"]
        args += ["--fake-solution", str(solution)]
        if command:
            args += ["--autocode", *command]
        with (
            patch.object(run, "selected", return_value=[self.scenario]),
            patch.object(run, "run_one", side_effect=drive),
            patch.object(run, "autocode_revision", self.observer),
            patch.object(build_compare.api_cost, "estimate", return_value={"complete": True, "usd": 3}),
            contextlib.redirect_stdout(io.StringIO()),
        ):
            code = run.main(args)
        (directory,) = output.iterdir()
        return code, json.loads((directory / "comparison.json").read_text()), directory

    def assert_unqualified(self, result, text):
        code, report, _ = result
        self.assertEqual(1, code, "a native PASS on different or unknown inputs must not qualify")
        self.assertFalse(report["all_passed"])
        self.assertTrue(all(row["verdict"] == verdict.PASS for row in report["records"]))
        self.assertTrue(all(row["qualification"]["status"] != "qualified" for row in report["records"]))
        self.assertTrue(all(arm["qualified_passes"] == 0 for arm in report["summary"].values()))
        reasons = " ".join(reason for row in report["records"] for reason in row["qualification"]["reasons"])
        self.assertIn(text, reasons)
        return report

    def test_raw_revision_mismatch_cannot_qualify_matching_comparison_receipts(self):
        def wrong(row, scenario, options):
            row["autocode"]["commit"] = "b" * 40

        self.assert_unqualified(self.compare(wrong), "result revision")

    def test_raw_profile_and_mode_are_checked_independently(self):
        for field in ("profile", "mode"):
            with self.subTest(field=field):

                def wrong(row, scenario, options):
                    row[field] = {"models": {"builder": "other"}} if field == "profile" else "fake"

                self.assert_unqualified(self.compare(wrong, live=True), f"result {field}")

    def test_fresh_source_observations_are_not_the_expected_revision_argument(self):
        def moved(row, scenario, options):
            self.revision["commit"] = "b" * 40

        self.assert_unqualified(self.compare(moved), "observed revision")

    def test_dirty_source_is_readable_but_unknown(self):
        self.revision["dirty"] = True
        report = self.assert_unqualified(self.compare(), "dirty")
        self.assertTrue(all(row["qualification"]["status"] == "unknown" for row in report["records"]))

    def test_oracle_hidden_check_and_seed_edits_during_an_arm_are_detected(self):
        (self.case / "seed/standalone.pyc").write_bytes(b"original bytecode input")
        for name in ("oracle.py", "hidden/check.py", "seed/app.py", "seed/standalone.pyc"):
            with self.subTest(name=name):

                def edited(row, scenario, options):
                    (self.case / name).write_text((self.case / name).read_text() + "# changed\n")

                self.assert_unqualified(self.compare(edited), "workload")

    def test_resolved_profile_drift_does_not_mutate_the_frozen_declaration(self):
        with patch.dict(profiles.PROFILES, {"build-comparison": copy.deepcopy(self.profile)}):

            def changed(row, scenario, options):
                profiles.PROFILES["build-comparison"]["models"]["builder"] = "other-model"

            result = self.compare(changed, live=True)
            self.assert_unqualified(result, "observed profile")
            self.assertEqual(self.profile, result[1]["protocol"]["profile"])
            self.assertEqual(result[1]["protocol"], json.loads((result[2] / "protocol.json").read_text()))

    def test_resolved_brief_and_external_seed_consumed_bytes_are_observed(self):
        external = self.root / "external-seed"
        external.mkdir()
        (external / "app.py").write_text("original\n")
        with patch.object(catalog.pinned_seed, "tree", return_value=external):
            self.scenario = dataclasses.replace(
                self.scenario, seed_source=catalog.pinned_seed.PinnedSeed("fixture", "f" * 40)
            )

            def edited(row, scenario, options):
                (external / "app.py").write_text("changed\n")

            self.assert_unqualified(self.compare(edited), "workload")
        self.scenario = dataclasses.replace(self.scenario, seed_source=None)

        def changed_brief(row, scenario, options):
            object.__setattr__(scenario, "brief", scenario.brief + " Another request.")

        self.assert_unqualified(self.compare(changed_brief), "workload")

    def test_effective_configuration_changes_are_detected(self):
        def changed(row, scenario, options):
            options.max_seconds = 999

        self.assert_unqualified(self.compare(changed), "configuration")

    def test_executable_mode_change_with_unchanged_bytes_is_detected(self):
        executable = self.case / "seed/app.py"
        original = executable.read_bytes()

        def changed(row, scenario, options):
            executable.chmod(executable.stat().st_mode ^ 0o100)

        self.assert_unqualified(self.compare(changed), "workload")
        self.assertEqual(original, executable.read_bytes())

    def test_empty_directory_changes_are_observed_and_matching_empty_directories_qualify(self):
        empty = self.case / "seed/empty"
        empty.mkdir()
        self.assertTrue(self.compare()[1]["all_passed"])

        def changed(row, scenario, options):
            if empty.exists():
                empty.rmdir()
            else:
                empty.mkdir()

        self.assert_unqualified(self.compare(changed), "workload")

    def test_external_or_escaping_scripted_solution_edits_cannot_qualify(self):
        overlay = self.root / "external-overlay"
        overlay.mkdir()
        source = overlay / "app.py"
        source.write_text("original\n")

        def changed(row, scenario, options):
            source.write_text(source.read_text() + "changed\n")

        for solution in (str(overlay), "../external-overlay"):
            with self.subTest(solution=solution):
                report = self.assert_unqualified(self.compare(changed, solution=solution), "external")
                self.assertTrue(all(row["qualification"]["status"] == "unknown" for row in report["records"]))

    def test_shared_oracle_helpers_are_part_of_the_workload_identity(self):
        helpers = self.root / "helpers"
        helpers.mkdir()
        helper = helpers / "oracle.py"
        helper.write_text("original\n")

        def changed(row, scenario, options):
            helper.write_text(helper.read_text() + "changed\n")

        with patch.object(build_compare.comparison_identity, "__file__", str(helpers / "comparison_identity.py")):
            self.assert_unqualified(self.compare(changed), "workload")

    def test_unsupported_inputs_and_external_commands_remain_unknown(self):
        target = self.case / "hidden/link.py"
        target.symlink_to("check.py")
        self.assert_unqualified(self.compare(), "symlink")
        target.unlink()
        self.assert_unqualified(self.compare(command=["external-autocode"]), "external")
        os.mkfifo(target)
        self.assert_unqualified(self.compare(), "special")

    def test_unavailable_pinned_seed_keeps_all_scheduled_skips(self):
        self.scenario = dataclasses.replace(
            self.scenario, seed_source=catalog.pinned_seed.PinnedSeed("fixture", "f" * 40)
        )

        def skipped(row, scenario, options):
            row.update(verdict=verdict.SKIPPED, oracle_passed=None, summary="requires unavailable pinned seed")

        with patch.object(
            catalog.pinned_seed, "tree", side_effect=catalog.pinned_seed.SeedUnavailable("fixture missing")
        ):
            code, report, _ = self.compare(skipped)
        self.assertEqual(1, code)
        self.assertEqual(2, len(report["records"]))
        self.assertTrue(all(row["verdict"] == verdict.SKIPPED for row in report["records"]))
        self.assertTrue(all(row["qualification"]["status"] == "unknown" for row in report["records"]))

    def test_matching_control_and_python_cache_creation_qualify(self):
        def caches(row, scenario, options):
            cache = self.case / "__pycache__"
            cache.mkdir(exist_ok=True)
            (cache / "oracle.cpython-test.pyc").write_bytes(b"cache")

        code, report, directory = self.compare(caches)
        self.assertEqual(0, code)
        self.assertEqual(2, report["protocol"]["schema_version"])
        self.assertTrue(report["all_passed"])
        for arm in report["summary"].values():
            self.assertEqual((1, 1), (arm["passes"], arm["qualified_passes"]))
        with patch.object(run, "run_one", side_effect=AssertionError("rebuild launches nothing")):
            self.assertTrue(build_compare.rebuild(directory)["all_passed"])

    def test_native_pass_and_unqualified_failure_spend_keep_the_scheduled_denominator(self):
        def outcomes(row, scenario, options):
            row["autocode"]["commit"] = "b" * 40
            if options.attempt_context["variant"] == "adaptive":
                row.update(verdict=verdict.FALSE_COMPLETE, oracle_passed=False)

        code, report, _ = self.compare(outcomes, live=True)
        self.assertEqual(1, code)
        self.assertEqual(2, len(report["records"]))
        fixed, adaptive = report["summary"]["fixed"], report["summary"]["adaptive"]
        self.assertEqual(
            (1, 0, 1, 3), (fixed["passes"], fixed["qualified_passes"], fixed["scheduled"], fixed["api_usd"])
        )
        self.assertEqual(
            (0, 0, 1, 3), (adaptive["passes"], adaptive["qualified_passes"], adaptive["scheduled"], adaptive["api_usd"])
        )
        self.assertIsNone(fixed["api_usd_per_qualified_pass"])

    def test_mixed_repetitions_keep_all_spend_in_cost_per_qualified_pass(self):
        def mismatch(row, scenario, options):
            if options.attempt_context["repeat"] == 2:
                row["autocode"]["commit"] = "b" * 40

        code, report, _ = self.compare(mismatch, live=True, repeats=2)
        self.assertEqual(1, code)
        for arm in report["summary"].values():
            self.assertEqual((2, 2, 1), (arm["scheduled"], arm["passes"], arm["qualified_passes"]))
            self.assertEqual((1, 0.5), (arm["native_pass_rate"], arm["qualified_pass_rate"]))
            self.assertEqual((6, 3, 6), (arm["api_usd"], arm["api_usd_per_pass"], arm["api_usd_per_qualified_pass"]))

    def test_legacy_and_missing_receipts_rebuild_without_historical_backfill(self):
        _, report, directory = self.compare()
        protocol = report["protocol"]
        protocol.pop("schema_version", None)
        (directory / "protocol.json").write_text(json.dumps(protocol))
        for path in directory.glob("pair-*/attempt-*.json"):
            row = json.loads(path.read_text())
            row.pop("comparison_identity", None)
            path.write_text(json.dumps(row))
        rebuilt = build_compare.rebuild(directory)
        self.assertFalse(rebuilt["all_passed"], "legacy outcomes stay readable, with identity unknown")
        self.assertEqual(2, len(rebuilt["records"]))
        self.assertTrue(all(row["qualification"]["status"] == "unknown" for row in rebuilt["records"]))

    def test_v2_missing_receipts_and_legacy_with_receipts_stay_unknown(self):
        for missing in ("receipt", "version"):
            with self.subTest(missing=missing):
                _, report, directory = self.compare()
                if missing == "version":
                    report["protocol"].pop("schema_version")
                    (directory / "protocol.json").write_text(json.dumps(report["protocol"]))
                else:
                    for path in directory.glob("pair-*/attempt-*.json"):
                        row = json.loads(path.read_text())
                        row.pop("comparison_identity")
                        path.write_text(json.dumps(row))
                rebuilt = build_compare.rebuild(directory)
                self.assertFalse(rebuilt["all_passed"])
                self.assertTrue(all(row["qualification"]["status"] == "unknown" for row in rebuilt["records"]))

    def test_unfinished_admission_and_malformed_identity_stay_readable(self):
        _, report, directory = self.compare()
        path = next(directory.glob("pair-*/attempt-*.json"))
        row = json.loads(path.read_text())
        path.unlink()
        (Path(row["evidence"]) / "attempt.json").write_text(
            json.dumps(
                {
                    **row,
                    "schema": 1,
                    "phase": "interrupted",
                    "owner": None,
                    "comparison_identity": None,
                }
            )
        )
        rebuilt = build_compare.rebuild(directory)
        self.assertFalse(rebuilt["all_passed"])
        recovered = next(item for item in rebuilt["records"] if item["variant"] == row["variant"])
        self.assertEqual("unknown", recovered["qualification"]["status"])
        self.assertEqual(verdict.INTERRUPTED_UNGRADED, recovered["verdict"])
        self.assertIsNone(recovered["wall_seconds"])
        for key, value in (("schema_version", True), ("revision", "not a revision"), ("configurations", [])):
            with self.subTest(field=key):
                malformed = {**report["protocol"], key: value}
                result = build_compare.report(directory, malformed, report["records"])
                self.assertFalse(result["all_passed"])
                self.assertTrue(all(item["qualification"]["status"] == "unknown" for item in result["records"]))

    def test_comparison_revision_checks_evaluator_sources_beside_tools(self):
        completed = Mock(stdout="a" * 40, returncode=0)
        with patch.object(run.subprocess, "run", return_value=completed) as git:
            run.autocode_revision(comparison=True)
        command = git.call_args_list[-1].args[0]
        self.assertEqual(
            ["tools", "pyproject.toml", "scenarios/run.py", "scenarios/harness"], command[command.index("--") + 1 :]
        )

    def test_failed_git_status_does_not_stamp_comparison_source_clean(self):
        success = Mock(stdout="a" * 40, returncode=0)
        failed_status = Mock(stdout="", returncode=1)
        with patch.object(run.subprocess, "run", side_effect=[success, success, failed_status]):
            observed = run.autocode_revision(comparison=True)
        self.assertIsNone(observed["dirty"])
        self.observer.side_effect = None
        self.observer.return_value = observed
        self.assert_unqualified(self.compare(), "revision not recorded")
        with patch.object(run.subprocess, "run", side_effect=[success, success, failed_status]):
            self.assertFalse(run.autocode_revision()["dirty"], "ordinary record compatibility is unchanged")


if __name__ == "__main__":
    unittest.main()
