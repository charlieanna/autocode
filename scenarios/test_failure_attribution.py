"""Explicit investigation metadata must not rewrite or rescore a scenario run."""

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from . import run

attribution = run.attribution
stats = run.stats


class FailureAttributionTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="scenario-attribution-test-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.directory = self.root / "saved-run"
        self.directory.mkdir()
        self.result = {
            "scenario": "fixture",
            "mode": "fake",
            "verdict": "FALSE_COMPLETE",
            "summary": "The check failed",
            "diagnosis": {"verdict": "CORRECT", "reason": "word-list score"},
        }
        self.result_path = self.directory / "result.json"
        self.result_path.write_text(json.dumps(self.result, indent=2))
        self.original = self.result_path.read_bytes()
        self.report = self.root / "operator-report.json"

    def pin(self, name, document):
        path = self.directory / name
        path.write_text(json.dumps(document))
        return {"path": name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}

    def suspicion(self):
        evidence = self.pin("handoff.json", {"observed": "validator handoff omitted the receipt"})
        return {
            "evidence_refs": [{"handoff": "Builder to Tester", **evidence}],
            "suspected_cause": "receipt forwarding may have lost the result",
        }

    def confirmation(self, *, before_ok=False):
        document = self.suspicion()
        before = self.pin(
            "before.json",
            {
                "command": "python -m unittest receipt_regression",
                "source_sha256": "a" * 64,
                "checks": [{"name": "receipt survives the handoff", "ok": before_ok}],
                "error": "",
            },
        )
        after = self.pin(
            "after.json",
            {
                "command": "python -m unittest receipt_regression",
                "source_sha256": "b" * 64,
                "checks": [{"name": "receipt survives the handoff", "ok": True}],
                "error": "",
            },
        )
        document.update(
            confirmed_diagnosis="receipt path was dropped at the handoff",
            regression={"check": "receipt survives the handoff", "before": before, "after": after},
        )
        return document

    def append(self, document, *, reviewer=None):
        self.report.write_text(json.dumps(document))
        return attribution.append(self.directory, self.report, reviewer=reviewer)

    def projected(self):
        (result,) = stats.load_results(self.root)
        self.assertEqual(self.original, self.result_path.read_bytes())
        self.assertEqual(self.result["verdict"], result["verdict"])
        self.assertEqual(self.result["diagnosis"], result["diagnosis"])
        return result["failure_attribution"]

    def test_old_results_default_unknown_without_invoking_the_validator(self):
        with patch.object(attribution, "validate", side_effect=AssertionError("unexpected validator launch")):
            self.assertEqual(attribution.unknown(), self.projected())

    def test_new_scenario_and_baseline_records_start_unknown(self):
        other = self.root / "new-run"
        other.mkdir()
        result = run.finish(other, {"scenario": "fixture"}, "FALSE_COMPLETE", "failed")
        self.assertEqual(attribution.unknown(), result["failure_attribution"])
        with (
            patch.object(run, "materialize", return_value=other),
            patch.object(run.baseline, "fake_setup", return_value=(["not-launched"], {})),
            patch.object(run.baseline, "run", return_value={"status": "CLAIMED_COMPLETE"}),
            patch.object(
                run.verdict,
                "evaluate",
                return_value=SimpleNamespace(passed=False, summary="failed", checks=[], error=""),
            ),
            patch.object(run.verdict, "judge", return_value=("FALSE_COMPLETE", "failed")),
        ):
            result = run.run_baseline(
                SimpleNamespace(
                    id="fixture", seed=other, npm_setup=None, brief="fixture", timeout_minutes=1, expected="pass"
                ),
                SimpleNamespace(fake=True, timeout_minutes=None),
                self.root,
                other,
            )
        self.assertEqual(attribution.unknown(), result["failure_attribution"])
        saved = json.loads((self.root / "fixture-baseline/result.json").read_text())
        self.assertEqual(attribution.unknown(), saved["failure_attribution"])

    def test_cli_explicitly_appends_suspicion_without_rescoring_model_diagnosis(self):
        self.report.write_text(json.dumps(self.suspicion()))
        call = subprocess.run(
            [
                sys.executable,
                str(Path(run.__file__).resolve()),
                "annotate",
                str(self.directory),
                "--report",
                str(self.report),
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(0, call.returncode, call.stderr)
        self.assertIn("Failure attribution appended:", call.stdout)
        projected = self.projected()
        self.assertIsNotNone(projected["suspected_cause"])
        self.assertIsNone(projected["confirmed_diagnosis"])
        self.assertIsNone(projected["confirmed_by"])

    def test_confirmation_needs_explicit_reviewer_even_if_report_claims_one(self):
        document = self.confirmation()
        document["confirmed_by"] = "model-authority"
        with self.assertRaisesRegex(ValueError, "explicit reviewer"):
            self.append(document)
        self.assertFalse((self.directory / attribution.ANNOTATIONS).exists())
        self.append(document, reviewer="Operator")
        projected = self.projected()
        self.assertEqual("Operator", projected["confirmed_by"])
        self.assertEqual(document["confirmed_diagnosis"], projected["confirmed_diagnosis"])

    def test_confirmation_refuses_a_regression_that_never_failed(self):
        with self.assertRaisesRegex(ValueError, "fail before"):
            self.append(self.confirmation(before_ok=True), reviewer="Operator")
        self.assertEqual(attribution.unknown(), self.projected())

    def test_changed_pinned_regression_or_handoff_invalidates_projection(self):
        for name in ("before.json", "after.json", "handoff.json"):
            with self.subTest(name=name):
                self.append(self.confirmation(), reviewer="Operator")
                (self.directory / name).write_text('{"changed": true}')
                self.assertEqual(attribution.unknown(), self.projected())

    def test_each_annotation_appends_and_latest_invalid_metadata_does_not_revive_confirmation(self):
        first = self.append(self.confirmation(), reviewer="Operator")
        original_annotation = first.read_bytes()
        latest = self.append(self.suspicion())
        self.assertNotEqual(first, latest)
        self.assertEqual(original_annotation, first.read_bytes())
        self.assertIsNone(self.projected()["confirmed_diagnosis"])
        latest.write_text(latest.read_text().replace("receipt forwarding", "modified forwarding"))
        self.assertEqual(attribution.unknown(), self.projected())
        self.assertEqual(original_annotation, first.read_bytes())

    def test_clock_rollback_does_not_revive_an_older_confirmation(self):
        with patch.object(attribution.time, "time_ns", side_effect=[2000, 1000]):
            self.append(self.confirmation(), reviewer="Operator")
            later = self.suspicion()
            later["suspected_cause"] = "Later investigation leaves the cause unconfirmed"
            self.append(later)
        projected = self.projected()
        self.assertEqual(later["suspected_cause"], projected["suspected_cause"])
        self.assertIsNone(projected["confirmed_diagnosis"])
        self.assertIsNone(projected["confirmed_by"])

    def test_simultaneous_ordering_tie_stays_unknown(self):
        first = self.append(self.confirmation(), reviewer="Operator")
        later = self.append(self.suspicion())
        # Emulate concurrent appenders that both observed the same preceding sequence.
        tied = later.with_name(first.name[:20] + later.name[20:])
        later.rename(tied)
        self.assertEqual(attribution.unknown(), self.projected())

    def test_annotation_cannot_follow_a_changed_result_or_redirected_record(self):
        path = self.append(self.confirmation(), reviewer="Operator")
        self.result_path.write_text(json.dumps({**self.result, "summary": "changed result"}))
        changed = stats.load_results(self.root)[0]
        self.assertEqual(attribution.unknown(), changed["failure_attribution"])
        self.result_path.write_bytes(self.original)
        raw = path.read_bytes()
        path.unlink()
        external = self.root / "redirected.json"
        external.write_bytes(raw)
        path.symlink_to(external)
        self.assertEqual(attribution.unknown(), self.projected())


if __name__ == "__main__":
    unittest.main()
