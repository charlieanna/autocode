"""A claim a stage reports is tied to the recorded observation that establishes it (#3904, #3947).

Saved-report and saved-receipt fixtures only; no model is called. The tests reach the claim judge through
entry points that exist before it (`autocode report-check`, runner admission and `autocode capture`), so
this file loads on the original code and each regression test fails there by assertion.
"""

from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_goal_lifecycle as lifecycle
import autocode_report_check as report_check
import autocode_support as s
from goal_fixtures import body, envelope

from . import test_goals

ROOT = Path(__file__).resolve().parents[1]
SCHEMA = {"type": "object"}
PROBE = "evidence-check3-independent-probe.json"
EXIT_2_LIMITATION = f"A header-only CSV uses the exit-2 stderr-only channel ({PROBE})"
BACKLOG = f"A header-only CSV succeeds with unchanged stock ({PROBE})"
C3 = "The new usage page's :func: references to semver.dumps_version and semver.loads_version resolve"
SINGLE_BUILD = "sphinx-build -W docs docs/_build"
COMPARED_BUILD = "sphinx-build -n docs docs/_build"
NEW_WARNINGS = [
    "docs/usage.rst:12: WARNING: py:func reference target not found: semver.dumps_version",
    "docs/usage.rst:14: WARNING: py:func reference target not found: semver.loads_version",
]
BASE = "1111111111111111111111111111111111111111"
OTHER_BASE = "2222222222222222222222222222222222222222"


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), "-c", "user.name=Fixture", "-c", "user.email=f@example.test", *args],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def write_capture(workspace, name, command, exit_code, **extra):
    """A receipt shaped as `autocode capture` writes one (no per-test results)."""
    path = Path(workspace) / ".autocode/evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    log = path.with_suffix(".log")
    log.write_text("")
    path.write_text(
        json.dumps(
            {
                "command": command.split(),
                "command_text": command,
                "exit_code": exit_code,
                "duration_seconds": 0.1,
                "full_output": str(log),
                "full_output_sha256": s.file_hash(log),
                "summary": {"format": "text", "content": "", "filter": "raw"},
                **extra,
            }
        )
    )
    return path


def write_comparison(workspace, name, *, base, new_diagnostics, command=COMPARED_BUILD):
    """A receipt shaped as `autocode capture --compare-base` writes one."""
    path = Path(workspace) / ".autocode/evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "kind": "diagnostics_comparison",
                "command": command.split(),
                "command_text": command,
                "base_revision": base,
                "base_exit_code": 0,
                "candidate_exit_code": 0,
                "new_diagnostics": new_diagnostics,
            }
        )
    )
    return path


def completion_report(limitations, backlog=()):
    return {
        "status": "COMPLETE",
        "findings": [],
        "agreed_limitations": list(limitations),
        "deferred_backlog": list(backlog),
        "summary": "All criteria verified",
    }


def tester_report(criterion_id, command, refs=("check:1",)):
    return {
        "status": "PASS",
        "checks": [{"command": command, "exit_code": 0}],
        "criterion_results": [{"id": criterion_id, "status": "PASS", "evidence_refs": list(refs)}],
        "findings": [],
    }


class ReportCheckClaimTests(unittest.TestCase):
    """`autocode report-check` gives a stage the claim judgment before it finishes."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / ".autocode/evidence").mkdir(parents=True)
        self.run = self.root / ".autocode/runs/fixture"
        (self.run / "evidence").mkdir(parents=True)

    def state(self, criteria=(), **extra):
        rows = [
            {"id": cid, "criterion": text, "verification_method": "", "human_review": False} for cid, text in criteria
        ]
        return {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "acceptance_criteria": copy.deepcopy(rows),
            "goal_contract": {"revision": 1, "hash": "h", "body": {"acceptance_criteria": rows}},
            "base_commit": BASE,
            **extra,
        }

    def check(self, stage, report, state):
        spec = report_check.build_spec(stage=stage, workspace=self.root, schema=SCHEMA, state=state)
        return report_check.check_report(json.dumps(report), spec=spec)

    def test_ac5_report_check_refuses_limitation_contradicting_capture_exit(self):
        write_capture(self.root, PROBE, "python inventory.py apply header-only.csv", 0)
        report = completion_report([EXIT_2_LIMITATION], [BACKLOG])
        errors = self.check("review_completion", report, self.state())
        self.assertEqual(1, len(errors), errors)
        self.assertIn("agreed_limitations", errors[0])
        self.assertIn(PROBE, errors[0])
        self.assertIn("exit 2", errors[0])
        self.assertIn("exit 0", errors[0])
        self.assertNotIn("deferred_backlog", errors[0])

    def test_ac10_matching_claim_is_accepted_unchanged(self):
        write_capture(self.root, PROBE, "python inventory.py apply header-only.csv", 0)
        report = completion_report([f"A header-only CSV exits 0 with unchanged stock ({PROBE})"])
        text = json.dumps(report)
        spec = report_check.build_spec(
            stage="review_completion", workspace=self.root, schema=SCHEMA, state=self.state()
        )
        self.assertEqual([], report_check.check_report(text, spec=spec))
        self.assertEqual(report, json.loads(text))
        self.assertEqual([f"A header-only CSV exits 0 with unchanged stock ({PROBE})"], report["agreed_limitations"])

    def test_ac15_exit_code_rule_skips_test_runs_and_partial_matches(self):
        state = self.state([("C2", "Whitespace input exits 2")])
        self.assertEqual([], self.check("tester", tester_report("C2", "python -m unittest tests.test_cli"), state))
        write_capture(self.root, "evidence-a.json", "python app.py ''", 0)
        write_capture(self.root, "evidence-b.json", "python app.py ''", 2)
        write_capture(self.root, "evidence-unit-tests.json", "python -m unittest tests.test_cli", 0)
        for limitation in (
            "Empty input exits 2 (evidence-a.json, evidence-b.json)",
            "A name exits 0 and no name exits 2 (evidence-a.json)",
            "Whitespace input exits 2 (evidence-unit-tests.json)",
        ):
            with self.subTest(limitation=limitation):
                self.assertEqual([], self.check("review_completion", completion_report([limitation]), state))

    def test_ac17_non_diagnostic_resolve_and_candidate_pass_are_not_refused(self):
        state = self.state([("C4", "start resolves a symlinked workspace")])
        self.assertEqual([], self.check("tester", tester_report("C4", "python -m unittest tests.test_start"), state))
        state = self.state(
            [
                ("C5", "report-check returns no error for a matching claim"),
                ("C6", "The documentation build prints no new warnings"),
            ]
        )
        report = tester_report("C5", "python -m unittest tests.test_report_check")
        report["criterion_results"].append({"id": "C6", "status": "PASS", "evidence_refs": ["check:1"]})
        self.assertEqual([], self.check("tester", report, state))
        state = self.state(regression_baseline={"command": "python -m pytest", "health": "failing_tests"})
        report = completion_report(["The project's full suite passes with the change"])
        self.assertEqual([], self.check("review_completion", report, state))

    def test_ac7_reference_claim_on_single_build_is_not_verified(self):
        errors = self.check("tester", tester_report("C3", SINGLE_BUILD), self.state([("C3", C3)]))
        self.assertEqual(1, len(errors), errors)
        error = errors[0]
        self.assertIn("C3", error)
        self.assertIn(f"`{SINGLE_BUILD}`", error)
        self.assertIn("single run cannot show what the change added", error)
        self.assertIn(f"--compare-base {BASE}", error)
        self.assertIn("-- COMMAND`", error)
        self.assertIn("COMMAND must be a build that reports unresolved references", error)
        self.assertNotIn(f"-- {SINGLE_BUILD}", error)

    def test_ac8_reference_claim_judged_by_diagnostics_comparison(self):
        state = self.state([("C3", C3)])
        write_comparison(self.root, "docs-diagnostics.json", base=BASE, new_diagnostics=NEW_WARNINGS)
        report = tester_report("C3", COMPARED_BUILD, refs=[".autocode/evidence/docs-diagnostics.json"])
        errors = self.check("tester", report, state)
        self.assertEqual(1, len(errors), errors)
        self.assertIn("C3", errors[0])
        for line in NEW_WARNINGS:
            self.assertIn(line, errors[0])
        listed = f"autocode capture --compare-base {BASE} --output .autocode/evidence/docs-diagnostics.json -- {COMPARED_BUILD}"
        errors = self.check("tester", tester_report("C3", listed), state)
        self.assertEqual(1, len(errors), errors)
        self.assertIn(NEW_WARNINGS[1], errors[0])
        write_comparison(self.root, "docs-clean.json", base=BASE, new_diagnostics=[])
        report = tester_report("C3", COMPARED_BUILD, refs=[".autocode/evidence/docs-clean.json"])
        self.assertEqual([], self.check("tester", report, state))

    def test_ac19_comparison_against_another_base_does_not_establish_claim(self):
        write_comparison(self.root, "docs-other.json", base=OTHER_BASE, new_diagnostics=[])
        report = tester_report("C3", COMPARED_BUILD, refs=[".autocode/evidence/docs-other.json"])
        errors = self.check("tester", report, self.state([("C3", C3)]))
        self.assertEqual(1, len(errors), errors)
        self.assertIn("C3", errors[0])
        self.assertIn(BASE, errors[0])
        self.assertIn(OTHER_BASE, errors[0])
        self.assertIn("not made against the run's base", errors[0])


class CompareBaseCaptureTests(unittest.TestCase):
    """`autocode capture --compare-base` records the diagnostics a change added."""

    def repository(self, committed, working):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name).resolve()
        git(root, "init", "-q")
        (root / "doc.txt").write_text(committed)
        (root / "check.py").write_text(self.checker)
        git(root, "add", "doc.txt", "check.py")
        git(root, "commit", "-qm", "fixture")
        (root / "doc.txt").write_text(working)
        return root

    def capture(self, root, output):
        environment = {
            key: value
            for key, value in os.environ.items()
            if key
            not in ("AUTOCODE_CAPTURE_CONTEXT", "AUTOCODE_VERIFICATION_COPY", "AUTOCODE_VERIFICATION_COPY_SHA256")
        }
        environment["AUTOCODE_OUTPUT_STORE"] = str(root / ".autocode/output-store")
        return subprocess.run(
            [
                sys.executable,
                str(ROOT / "tools/autocode.py"),
                "capture",
                "--compare-base",
                "HEAD",
                "--output",
                output,
                "--",
                sys.executable,
                "check.py",
            ],
            cwd=root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=120,
        )

    def test_ac9_compare_diagnostics_records_new_warning_lines(self):
        self.checker = "for line in open('doc.txt').read().splitlines():\n    if 'BROKEN' in line:\n        print('WARNING: ' + line)\n"
        root = self.repository("ok\n", "ok\nBROKEN ref\n")
        result = self.capture(root, ".autocode/evidence/cmp.json")
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads((root / ".autocode/evidence/cmp.json").read_text())
        self.assertEqual("diagnostics_comparison", receipt["kind"])
        self.assertEqual(git(root, "rev-parse", "HEAD"), receipt["base_revision"])
        self.assertEqual(0, receipt["base_exit_code"])
        self.assertEqual(0, receipt["candidate_exit_code"])
        self.assertEqual(["WARNING: BROKEN ref"], receipt["new_diagnostics"])
        scratch = root / ".autocode/scratch"
        self.assertEqual([], list(scratch.iterdir()) if scratch.exists() else [])
        self.assertEqual("ok\nBROKEN ref\n", (root / "doc.txt").read_text())

    def test_ac20_moved_diagnostic_is_not_new(self):
        self.checker = (
            "for number, line in enumerate(open('doc.txt').read().splitlines(), 1):\n"
            "    if 'BROKEN' in line:\n"
            "        print(f'doc.txt:{number}: WARNING: ' + line)\n"
        )
        root = self.repository("BROKEN a\n", "intro\nBROKEN a\n")
        result = self.capture(root, ".autocode/evidence/moved.json")
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads((root / ".autocode/evidence/moved.json").read_text())
        self.assertEqual([], receipt["new_diagnostics"])
        self.assertEqual("doc.txt:2: WARNING: BROKEN a", Path(receipt["full_output"]).read_text().strip())


class RunnerAdmissionClaimTests(unittest.TestCase):
    """Runner admission applies the same judgment, so a stage that skipped report-check is refused."""

    setUp = test_goals.GoalTests.setUp
    draft = test_goals.GoalTests.draft
    approve = test_goals.GoalTests.approve
    validation = test_goals.GoalTests.validation

    def completion(self, limitations, status="COMPLETE"):
        criteria = [{**c, "status": "verified", "evidence": "event:check"} for c in self.state["acceptance_criteria"]]
        return {
            **envelope(self.state),
            "status": status,
            "acceptance_criteria": criteria,
            "next_objective": "",
            "next_task": {
                "kind": "implement",
                "milestone_id": "M1",
                "requirements": ["Greet valid names; reject empty names"],
                "acceptance_criteria": ["C1"],
                "validation_plan": ["Execute both CLI cases"],
            },
            "agreed_limitations": list(limitations),
            "blocker": "",
            "evidence": ["event:check"],
            "plan": ["Greeting and checks"],
            "affected_paths": ["greet.py"],
        }

    def stage_record(self, stage, report):
        path = self.run / "iterations" / "001" / f"{stage}-01"
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "role": "completion" if stage == "review_completion" else "tester",
            "stage": stage,
            "iteration": self.state["iteration"],
            "exit_code": 0,
            "duration_seconds": 1,
            "source_revision": s.snapshot(self.root)["revision"],
        }
        for key, suffix, text in [
            ("output", ".json", json.dumps(report)),
            ("events", ".jsonl", json.dumps({"type": "turn.completed"})),
            ("before_ref", ".before.json", "{}"),
            ("after_ref", ".after.json", "{}"),
            ("schema", ".schema.json", "{}"),
        ]:
            Path(str(path) + suffix).write_text(text)
            record[key] = str(Path(str(path) + suffix))
        return record

    def test_ac6_completion_review_contradicting_its_capture_is_refused(self):
        self.approve()
        self.validation()  # without the claim judge this review completes the run
        write_capture(self.root, PROBE, "python inventory.py apply header-only.csv", 0)
        decision = self.completion([EXIT_2_LIMITATION])
        decision["deferred_backlog"] = [BACKLOG]
        record = self.stage_record("review_completion", decision)
        backlog = copy.deepcopy(self.state.get("deferred_backlog"))
        with self.assertRaises(ValueError) as caught:
            runner.apply_result(self.state, "review_completion", decision, record, self.root, self.run)
        message = str(caught.exception)
        for part in ("agreed_limitations", PROBE, "exit 2", "exit 0"):
            self.assertIn(part, message)
        self.assertEqual(backlog, self.state.get("deferred_backlog"))
        self.state["settings"]["report_repair"] = {"max_attempts": 0}
        with self.assertRaises(s.Paused):
            runner.reject_completed_stage(self.state, self.run, record, caught.exception)
        errors = [str(entry.get("last_error")) for entry in self.state["failure_history"].values()]
        self.assertTrue(any("agreed_limitations" in text and PROBE in text for text in errors), errors)

    def test_ac16_missing_capture_is_recorded_as_unverified(self):
        self.approve()
        self.validation()
        decision = self.completion(["Empty input exits 2 (evidence-missing.json)"])
        record = {"stage": "review_completion", "output": "final"}
        runner.apply_result(self.state, "review_completion", decision, record, self.root, self.run)
        saved = next(row for row in self.state["stages"] if row.get("output") == "final")
        rows = saved.get("claim_checks") or []
        self.assertEqual(1, len(rows), saved)
        self.assertEqual("unverified", rows[0]["status"])
        self.assertIn("evidence-missing.json", rows[0]["reason"])
        self.assertIn("no recorded capture was found", rows[0]["reason"])

    def approve_reference_claim(self):
        contract = body()
        contract["acceptance_criteria"] = [
            {"id": "C3", "criterion": C3, "verification_method": "Build the documentation", "human_review": False}
        ]
        contract["milestones"][0]["acceptance_criteria"] = ["C3"]
        lifecycle.install_draft(self.state, contract, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, test_goals.g.token(self.state["goal_contract"]))
        self.state["base_commit"] = git(self.root, "rev-parse", "HEAD")

    def bound_tester_report(self, command, refs=("check:1",)):
        """A Tester report whose one check is bound to its executed event, as GoalTests.validation binds it."""
        events = self.run / "tester.jsonl"
        events.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "check",
                        "type": "command_execution",
                        "command": command,
                        "exit_code": 0,
                        "aggregated_output": "build succeeded",
                    },
                }
            )
        )
        report = {
            **envelope(self.state),
            "verdict": "PASS",
            "findings": [],
            "unverified_criteria": [],
            "checks_run": [command],
            "checks": [{"command": command, "exit_code": 0, "evidence_ref": "event:check"}],
            "criterion_results": [{"id": "C3", "status": "PASS", "evidence_refs": list(refs)}],
            "end_to_end_result": {"status": "PASS", "summary": "Docs built", "evidence_refs": ["check:1"]},
        }
        record = {"events": str(events), "source_revision": s.snapshot(self.root)["revision"], "output": str(events)}
        return report, record

    def test_ac18_runner_refuses_reference_claim_on_single_build(self):
        self.approve_reference_claim()
        report, record = self.bound_tester_report(SINGLE_BUILD)
        spec = report_check.build_spec(stage="tester", workspace=self.root, schema=SCHEMA, state=self.state)
        errors = report_check.check_report(json.dumps(report), spec=spec)
        self.assertEqual(1, len(errors), errors)
        with self.assertRaises(ValueError) as caught:
            runner.apply_result(self.state, "tester", copy.deepcopy(report), record, self.root, self.run)
        message = str(caught.exception)
        self.assertIn("C3", message)
        self.assertIn(SINGLE_BUILD, message)
        self.assertIn(errors[0], message)

    def test_empty_comparison_pass_is_admitted_with_its_reason_on_the_stage_record(self):
        self.approve_reference_claim()
        receipt = ".autocode/evidence/docs-clean.json"
        write_comparison(self.root, "docs-clean.json", base=self.state["base_commit"], new_diagnostics=[])
        report, record = self.bound_tester_report("python3 -m unittest", refs=("check:1", receipt))
        runner.apply_result(self.state, "tester", report, record, self.root, self.run)
        saved = self.state["stages"][-1]
        self.assertEqual(str(record["events"]), saved["events"])
        rows = saved.get("claim_checks") or []
        self.assertEqual(1, len(rows), saved)
        row = rows[0]
        self.assertEqual(("C3", "unverified", receipt), (row["id"], row["status"], row["observation"]))
        self.assertIn(f"`{COMPARED_BUILD}`", row["reason"])
        self.assertIn("does not show that the command reports unresolved references", row["reason"])


if __name__ == "__main__":
    unittest.main()
