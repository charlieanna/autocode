"""A claim a stage reports is tied to the recorded observation that establishes it (#3904, #3947).

Saved-report and saved-receipt fixtures only; no model is called. The tests reach the claim judge through
entry points that exist before it (`autocode report-check`, runner admission and `autocode capture`), so
this file loads on the original code and each regression test fails there by assertion.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import io
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode as runner
import autocode_capture_command as capture_command
import autocode_claim_evidence as claim_evidence
import autocode_diagnostics_comparison as comparison
import autocode_goal_lifecycle as lifecycle
import autocode_job_source as job_source
import autocode_person_text as person_text
import autocode_report_check as report_check
import autocode_role_launch as role_launch
import autocode_support as s
import autocode_util as util
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
SUITE_COMMAND = "python -m unittest"
SUITE_CRITERION = "The project suite command `python -m unittest` exits 0."
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
    attest_capture(path)
    return path


def attest_capture(path):
    events = path.parent.parent / "claim-events.jsonl"
    receipt = json.loads(path.read_text())
    command = shlex.join(
        [sys.executable, str(ROOT / "tools/autocode.py"), "capture", "--output", str(path), "--", *receipt["command"]]
    )
    with events.open("a") as handle:
        handle.write(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {"type": "command_execution", "command": command, "aggregated_output": path.read_text()},
                }
            )
            + "\n"
        )


def write_comparison(
    workspace, name, *, base, new_diagnostics, command=COMPARED_BUILD, base_log="", candidate_log=None
):
    """A receipt shaped as `autocode capture --compare-base` writes one."""
    path = Path(workspace) / ".autocode/evidence" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    output, base_output = path.with_suffix(".log"), path.with_suffix(".base.log")
    output.write_text("\n".join(new_diagnostics) if candidate_log is None else candidate_log)
    base_output.write_text(base_log)
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
                "full_output": str(output),
                "full_output_sha256": s.file_hash(output),
                "base_output": str(base_output),
                "base_output_sha256": s.file_hash(base_output),
            }
        )
    )
    attest_capture(path)
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
        spec = report_check.build_spec(
            stage=stage,
            workspace=self.root,
            schema=SCHEMA,
            state=state,
            events_path=self.root / ".autocode/claim-events.jsonl",
        )
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

    def test_unattested_or_changed_capture_cannot_establish_or_contradict_a_claim(self):
        path = write_capture(self.root, PROBE, "python inventory.py apply header-only.csv", 0)
        report = completion_report([EXIT_2_LIMITATION])
        events = self.root / ".autocode/claim-events.jsonl"
        events.write_text("")
        self.assertEqual([], self.check("review_completion", report, self.state()))
        attest_capture(path)
        self.assertEqual(1, len(self.check("review_completion", report, self.state())))
        path.with_suffix(".log").write_text("altered output")
        self.assertEqual([], self.check("review_completion", report, self.state()))

    def test_report_file_capture_is_bound_to_the_current_attempt(self):
        attempt = {"attempt": "attempt-1", "nonce": "one", "source_revision": "revision-1"}
        write_capture(self.root, PROBE, "python inventory.py apply header-only.csv", 0, capture_context=attempt)
        report = completion_report([EXIT_2_LIMITATION])
        for seen, expected in ((attempt, "contradicted"), ({**attempt, "nonce": "two"}, "unverified")):
            with self.subTest(attempt=seen):
                context = claim_evidence.context(
                    self.state(), self.root, record={"output_mode": "report_file", "capture_context": seen}
                )
                problems, rows = claim_evidence.judge("review_completion", report, context)
                self.assertEqual(expected, rows[0]["status"])
                self.assertEqual(expected == "contradicted", bool(problems))

    def test_report_check_binds_the_actual_capture_attempt_before_launch(self):
        stage = "review_completion"
        base = self.run / "stages" / "completion-1"
        base.parent.mkdir(parents=True)
        events = base.with_suffix(".jsonl")
        spec_path = self.run / "planning" / "report-check" / f"{stage}-{base.parent.name}-{base.name}.json"
        spec_path.parent.mkdir(parents=True)
        spec = report_check.build_spec(
            stage=stage, workspace=self.root, schema=SCHEMA, state=self.state(), events_path=events
        )
        for mode in ("report_file", "opencode_events"):
            for attempt in (1, 2):
                with self.subTest(mode=mode, attempt=attempt):
                    spec_path.write_text(json.dumps(spec))
                    record = {"stage": stage, "events": str(events), "output_mode": mode}
                    options = {"env": dict(os.environ)}
                    output = base.with_suffix(".json")
                    job_source.prepare_capture_context(record, {"revision": f"source-{attempt}"}, output, options)
                    role_launch.bind_report_check_attempt(self.run, base, stage, record, util.atomic_json)
                    bound = json.loads(spec_path.read_text())
                    name = f"evidence-check3-independent-probe-{mode}-{attempt}.json"
                    path = self.root / ".autocode/evidence" / name
                    environment = {**options["env"], "AUTOCODE_OUTPUT_STORE": str(self.root / ".autocode/output-store")}
                    command = [
                        sys.executable,
                        str(ROOT / "tools/autocode.py"),
                        "capture",
                        "--output",
                        str(path),
                        "--",
                        sys.executable,
                        "-c",
                        "print('ok')",
                    ]
                    result = subprocess.run(
                        command, cwd=self.root, env=environment, capture_output=True, text=True, timeout=20
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    actual_report = completion_report([f"The program exits 2 ({name})"])
                    if mode == "opencode_events":
                        events.write_text(
                            json.dumps(
                                {
                                    "type": "item.completed",
                                    "item": {
                                        "type": "command_execution",
                                        "command": shlex.join(command),
                                        "aggregated_output": result.stdout + result.stderr,
                                    },
                                }
                            )
                            + "\n"
                        )
                    pre = report_check.check_report(json.dumps(actual_report), spec=bound)
                    post, _ = claim_evidence.judge(
                        stage, actual_report, claim_evidence.context(self.state(), self.root, record=record)
                    )
                    self.assertEqual(1, len(pre), pre)
                    self.assertEqual(pre, post)
                    if mode == "report_file":
                        stale = copy.deepcopy(bound)
                        stale[claim_evidence.SPEC_KEY]["attempt"]["capture_context"]["nonce"] = "previous-attempt"
                        self.assertEqual([], report_check.check_report(json.dumps(actual_report), spec=stale))
        # A pre-submit spec from another attempt must not be silently rebound.
        spec_path.write_text(json.dumps(spec))
        with self.assertRaisesRegex(ValueError, "does not belong"):
            role_launch.bind_report_check_attempt(self.run, base, stage, {"events": "elsewhere"}, util.atomic_json)
        # Report-repair launches do not have a pre-submit spec.
        role_launch.bind_report_check_attempt(self.run, base.with_name("repair"), stage, {}, util.atomic_json)

    def test_comparison_does_not_trust_tampered_baseline_output(self):
        state = self.state([("C3", C3)])
        path = write_comparison(self.root, "docs-clean.json", base=BASE, new_diagnostics=[])
        report = tester_report("C3", COMPARED_BUILD, refs=[".autocode/evidence/docs-clean.json"])
        self.assertEqual([], self.check("tester", report, state))
        path.with_suffix(".base.log").write_text("changed baseline")
        self.assertEqual(1, len(self.check("tester", report, state)))

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
        self.assertIn("exact capture_command your handoff names", error)
        self.assertNotIn("`autocode capture", error)
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

    def test_reference_refusal_keeps_diagnostics_in_person_text(self):
        command = "sphinx-build -E -a -n -W --keep-going -b html docs docs/_build"
        name = "docs-diagnostics.json"
        write_comparison(self.root, name, base=BASE, command=command, new_diagnostics=NEW_WARNINGS)
        report = tester_report("C3", command, refs=[f".autocode/evidence/{name}"])
        errors = self.check("tester", report, self.state([("C3", C3)]))
        self.assertEqual(1, len(errors), errors)
        saved = f"Completed tester output was rejected ({errors[0]}); attempt archived."
        shown = person_text.for_person(saved)
        self.assertNotIn("--keep-going", shown)
        for warning in NEW_WARNINGS:
            self.assertIn(warning, shown)

    def test_cached_sphinx_environment_cannot_support_a_reference_claim(self):
        fresh = "Running Sphinx v7.0.1\nupdating environment: 1 added, 0 changed, 0 removed\n"
        cached = (
            "\x1b[01mRunning Sphinx v7.0.1\x1b[39;49;00m\n"
            "loading pickled environment... done\n"
            "building [html]: targets for 0 source files that are out of date\n"
            "no targets are out of date.\nbuild succeeded.\n"
        )
        for label, command, base_log, candidate_log in (
            ("candidate", COMPARED_BUILD, fresh, cached),
            ("base", "python3.11 -m sphinx -n docs docs/_build", cached, fresh),
        ):
            with self.subTest(label=label):
                name = f"docs-reused-{label}.json"
                write_comparison(
                    self.root,
                    name,
                    base=BASE,
                    new_diagnostics=[],
                    command=command,
                    base_log=base_log,
                    candidate_log=candidate_log,
                )
                report = tester_report("C3", command, refs=[f".autocode/evidence/{name}"])
                errors = self.check("tester", report, self.state([("C3", C3)]))
                self.assertEqual(1, len(errors), errors)
                self.assertIn("Sphinx reused a cached reference environment", errors[0])
                self.assertIn("-E -a -n", errors[0])
        for label, command, candidate_log in (
            ("fresh", COMPARED_BUILD, fresh),
            ("other-tool", "python3 docs/check.py", cached),
        ):
            with self.subTest(label=label):
                name = f"docs-{label}.json"
                write_comparison(
                    self.root, name, base=BASE, new_diagnostics=[], command=command, candidate_log=candidate_log
                )
                report = tester_report("C3", command, refs=[f".autocode/evidence/{name}"])
                self.assertEqual([], self.check("tester", report, self.state([("C3", C3)])))

    def test_delivered_sphinx_page_linked_from_toctree_needs_reference_check(self):
        page = "docs/usage/json.rst"
        source = self.root / page
        source.parent.mkdir(parents=True)
        source.write_text("JSON usage\n==========\n\nSee :func:`semver.dumps_version`.\n")
        config = self.root / "docs/conf.py"
        config.write_text("project = 'Example'\n")
        text = f"The toctree in docs/usage/index.rst lists json and the page {page} contains the examples."
        state = self.state([("C7", text)], changed_files=[page])
        errors = self.check("tester", tester_report("C7", "python -m pytest -q"), state)
        self.assertEqual(1, len(errors), errors)
        self.assertIn(f"Criterion C7 delivers linked Sphinx documentation ({page})", errors[0])

        receipt = ".autocode/evidence/json-links.json"
        write_comparison(self.root, "json-links.json", base=BASE, new_diagnostics=NEW_WARNINGS)
        errors = self.check("tester", tester_report("C7", COMPARED_BUILD, refs=[receipt]), state)
        self.assertEqual(1, len(errors), errors)
        self.assertIn(NEW_WARNINGS[0], errors[0])

        # Unchanged documentation and projects without a Sphinx config do not
        # acquire this inferred obligation.
        unlinked = tester_report("C7", "python -m pytest -q")
        self.assertEqual([], self.check("tester", unlinked, self.state([("C7", text)], changed_files=[])))
        config.unlink()
        self.assertEqual([], self.check("tester", unlinked, state))

    def proof_state(self, criterion, suite_exit):
        """A run whose current PASS proof recorded the suite command and its exit code on the candidate."""
        path = self.run / "regression" / "proof-1.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "verdict": "PASS",
                    "commands": {"suite": SUITE_COMMAND},
                    "checks": {"suite_on_candidate": {"exit_code": suite_exit}},
                }
            )
        )
        proof = {"verdict": "PASS", "path": str(path), "receipt_sha256": s.file_hash(path)}
        return path, self.state([("AC11", criterion)], regression_proof=proof)

    def test_ac1_suite_exit_criterion_is_read_from_suite_on_candidate_exit_code(self):
        report = tester_report("AC11", SUITE_COMMAND)
        _, state = self.proof_state(SUITE_CRITERION, 1)
        errors = self.check("tester", report, state)
        self.assertEqual(1, len(errors), errors)
        self.assertIn("AC11", errors[0])
        self.assertIn("`suite_on_candidate.exit_code`", errors[0])
        self.assertIn("exit 1", errors[0])
        _, state = self.proof_state(SUITE_CRITERION, 0)
        self.assertEqual([], self.check("tester", report, state))

    def test_ac12_suite_exit_rule_reads_each_phrasing_and_only_an_authentic_answer(self):
        report = tester_report("AC11", SUITE_COMMAND)
        for criterion in ("The suite command exits 0.", "The command `python -m unittest` exits 0."):
            with self.subTest(criterion=criterion):
                _, state = self.proof_state(criterion, 1)
                errors = self.check("tester", report, state)
                self.assertEqual(1, len(errors), errors)
                self.assertIn("AC11", errors[0])
                self.assertIn("`suite_on_candidate.exit_code`", errors[0])
                self.assertIn("exit 1", errors[0])
        for criterion in (
            "The suite command exits 0 on the candidate and exits 1 on the original code.",
            "The command `make lint` exits 0.",
        ):
            with self.subTest(criterion=criterion):
                _, state = self.proof_state(criterion, 1)
                self.assertEqual([], self.check("tester", report, state))
        path, state = self.proof_state(SUITE_CRITERION, 1)
        path.write_text(path.read_text().replace('"exit_code": 1', '"exit_code": 0'))
        self.assertEqual([], self.check("tester", report, state))
        problems, rows = claim_evidence.judge("tester", report, claim_evidence.context(state, self.root))
        self.assertEqual([], problems)
        self.assertEqual(["unverified"], [row["status"] for row in rows])
        self.assertIn("AC11", rows[0]["reason"])
        _, state = self.proof_state(SUITE_CRITERION, 1)
        for status in ("FAIL", "UNVERIFIED"):
            with self.subTest(status=status):
                other = copy.deepcopy(report)
                other["criterion_results"][0]["status"] = status
                self.assertEqual([], self.check("tester", other, state))

    def test_suite_exit_rule_reads_only_the_current_passing_proof_of_the_attempts_revision(self):
        report = tester_report("AC11", SUITE_COMMAND)

        def judged(state, record=None):
            return claim_evidence.judge("tester", report, claim_evidence.context(state, self.root, record=record))

        path, state = self.proof_state(SUITE_CRITERION, 0)
        problems, rows = judged(state)
        self.assertEqual(([], ["established"]), (problems, [row["status"] for row in rows]))
        state["regression_proof"]["source_revision"] = "revision-1"
        problems, rows = judged(state, {"source_revision": "revision-2"})
        self.assertEqual(([], ["unverified"]), (problems, [row["status"] for row in rows]))
        self.assertIn("another source revision", rows[0]["reason"])
        self.assertEqual(
            ["established"], [row["status"] for row in judged(state, {"source_revision": "revision-1"})[1]]
        )
        _, state = self.proof_state(SUITE_CRITERION, 1)
        state["regression_proof"]["source_revision"] = "revision-1"
        problems, _ = judged(state, {"source_revision": "revision-1"})
        self.assertEqual(1, len(problems), problems)
        for change, reason in (
            ({"regression_proof": None}, "has no regression proof"),
            ({"regression_proof": {**state["regression_proof"], "verdict": "UNVERIFIED"}}, "did not pass"),
            ({"regression_proof": {**state["regression_proof"], "path": str(path) + ".missing"}}, "could not be read"),
        ):
            with self.subTest(reason=reason):
                problems, rows = judged({**state, **change})
                self.assertEqual(([], ["unverified"]), (problems, [row["status"] for row in rows]))
                self.assertIn(reason, rows[0]["reason"])
        path.write_text(json.dumps({"verdict": "PASS", "commands": {"suite": SUITE_COMMAND}, "checks": {}}))
        state["regression_proof"]["receipt_sha256"] = s.file_hash(path)
        problems, rows = judged(state)
        self.assertEqual(([], ["unverified"]), (problems, [row["status"] for row in rows]))
        self.assertIn("records no `suite_on_candidate.exit_code`", rows[0]["reason"])


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

    def test_scoped_sphinx_check_uses_three_fresh_copies_and_binds_the_control(self):
        self.checker = ""
        root = self.repository("base\n", "base\n")
        (root / "docs").mkdir()
        (root / "docs/conf.py").write_text("project = 'Fixture'\n")
        doc = "docs/usage.rst"
        index = "docs/index.rst"
        (root / doc).write_text("Title\n=====\n\n:py:func:`pkg.current`\n")
        (root / index).write_text("Overview\n========\n\n.. toctree::\n\n   usage\n")
        git(root, "add", "docs")
        git(root, "commit", "-qm", "documentation")
        cache = root / ".autocode/scratch/fixture-html"
        cache.mkdir(parents=True)
        (cache / "cached").write_text("do not reuse or remove this output")
        evidence = root / ".autocode/evidence"
        evidence.mkdir()
        executable_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, executable_dir)
        executable = executable_dir / "sphinx-build"
        executable.write_text(
            f"#!{sys.executable}\n"
            "import pathlib, re, sys\n"
            "path = pathlib.Path('docs/usage.rst').resolve()\n"
            "index = pathlib.Path('docs/index.rst').resolve()\n"
            "out = pathlib.Path(sys.argv[-1])\n"
            "assert not out.exists()\n"
            "out.mkdir(parents=True)\n"
            "targets = re.findall(r':py:func:`([^`]+)`', path.read_text())\n"
            'html = \'<a href="api.html"><code class="xref py py-func">pkg.current</code></a>\'\n'
            "if 'pkg.suppressed' in targets:\n"
            "    html += '<code class=\"xref py py-func\"><span>pkg.suppressed</span></code>'\n"
            "(out / 'usage.html').write_text(html)\n"
            "(out / 'index.html').write_text('<div class=\"toctree-wrapper compound\">'\n"
            '    \'<a class="reference internal" href="usage.html">Usage</a></div>\')\n'
            "print('Running Sphinx v7.0.1')\n"
            "bad = [item for item in targets if item not in ('pkg.current', 'pkg.suppressed')]\n"
            "for item in bad:\n"
            "    print(f'{path}:5: WARNING: py:func reference target not found: {item}')\n"
            "labels = re.findall(r':ref:`([^`]+)`', index.read_text())\n"
            "for item in labels:\n"
            "    print(f'{index}:5: WARNING: undefined label: {item}')\n"
            "count = len(bad) + len(labels)\n"
            "print(f'build finished with problems, {count} warnings.' if count else 'build succeeded.')\n"
            "raise SystemExit(bool(count))\n"
        )
        executable.chmod(0o755)
        command = [
            str(executable),
            "-E",
            "-a",
            "-n",
            "-W",
            "--keep-going",
            "-b",
            "html",
            "docs",
            str(cache.relative_to(root)),
        ]
        revision = util.snapshot(root)["revision"]
        context = {"attempt": "test-attempt", "nonce": "test-nonce", "source_revision": revision}
        path = evidence / "scoped.json"
        argv = [
            sys.executable,
            str(ROOT / "tools/autocode.py"),
            "capture",
            "--compare-base",
            "HEAD",
            "--reference-doc",
            doc,
            "--output",
            str(path),
            "--",
            *command,
        ]
        result = subprocess.run(
            argv,
            cwd=root,
            env={**os.environ, "AUTOCODE_CAPTURE_CONTEXT": json.dumps(context)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        import autocode_reference_diagnostics as reference_diagnostics

        receipt = json.loads(path.read_text())
        self.assertTrue(reference_diagnostics.sensitivity_passed(receipt, root))
        self.assertEqual([], receipt["new_diagnostics"])
        self.assertEqual(revision, receipt["sphinx_reference_control"]["source_revision"])
        self.assertEqual("do not reuse or remove this output", (cache / "cached").read_text())
        self.assertEqual([cache], list((root / ".autocode/scratch").iterdir()))

        event = root / ".autocode/evidence-events.jsonl"
        invocation = {
            "type": "item.completed",
            "item": {"type": "command_execution", "command": shlex.join(argv), "aggregated_output": result.stdout},
        }
        event.write_text(json.dumps(invocation) + "\n")
        report = tester_report("C7", shlex.join(command), refs=[".autocode/evidence/scoped.json"])
        claim_context = {
            "workspace": str(root),
            "criteria": {"C7": f"The toctree links {doc}."},
            "linked_sphinx_documents": {"C7": [doc]},
            "base_commit": git(root, "rev-parse", "HEAD"),
            "attempt": {"events": str(event), "capture_context": context},
        }
        errors, observations = claim_evidence.judge("tester", report, claim_context)
        self.assertEqual([], errors)
        self.assertEqual(["unverified"], [row["status"] for row in observations])
        self.assertIn(
            "original base revision could not be verified",
            claim_evidence.judge("tester", report, {**claim_context, "base_commit": ""})[0][0],
        )
        claim_context["attempt"]["output_mode"] = "report_file"
        event.write_text("")
        self.assertTrue(claim_evidence.judge("tester", report, claim_context)[0])
        event.write_text(json.dumps(invocation) + "\n")
        self.assertEqual((errors, observations), claim_evidence.judge("tester", report, claim_context))
        invocation["item"]["command"] = shlex.join(["docs/other.rst" if word == doc else word for word in argv])
        event.write_text(json.dumps(invocation) + "\n")
        self.assertTrue(claim_evidence.judge("tester", report, claim_context)[0])
        invocation["item"]["command"] = shlex.join(argv)
        event.write_text(json.dumps(invocation) + "\n")

        log = Path(receipt["sphinx_reference_control"]["output"])
        log.write_text(log.read_text().replace(":5: WARNING:", ".other:5: WARNING:"))
        self.assertFalse(reference_diagnostics.sensitivity_passed(receipt, root))
        self.assertIn(
            "did not demonstrate a fresh checker", claim_evidence.judge("tester", report, claim_context)[0][0]
        )
        receipt["sphinx_reference_control"]["sha256"] = util.file_hash(log)
        self.assertFalse(reference_diagnostics.sensitivity_passed(receipt, root))

        (root / doc).write_text("Title\n=====\n\n:py:func:`pkg.suppressed`\n")
        context["source_revision"] = util.snapshot(root)["revision"]
        suppressed = evidence / "suppressed.json"
        argv[argv.index(str(path))] = str(suppressed)
        argv[argv.index(doc)] = index
        result = subprocess.run(
            argv,
            cwd=root,
            env={**os.environ, "AUTOCODE_CAPTURE_CONTEXT": json.dumps(context)},
            capture_output=True,
            text=True,
        )
        self.assertEqual(1, result.returncode, result.stderr)
        receipt = json.loads(suppressed.read_text())
        self.assertEqual(1, receipt["exit_code"])
        self.assertEqual(0, receipt["candidate_exit_code"])
        self.assertTrue(reference_diagnostics.sensitivity_passed(receipt, root))
        self.assertEqual(
            [f"{doc}: WARNING: rendered Python reference has no link: pkg.suppressed"], receipt["new_diagnostics"]
        )
        invocation["item"].update(command=shlex.join(argv), aggregated_output=result.stdout)
        event.write_text(json.dumps(invocation) + "\n")
        claim_context["criteria"]["C7"] = f"The toctree links the desired topic from {index}."
        claim_context["linked_sphinx_documents"]["C7"] = [index]
        report["criterion_results"][0]["evidence_refs"] = [".autocode/evidence/suppressed.json"]
        self.assertIn("pkg.suppressed", claim_evidence.judge("tester", report, claim_context)[0][0])

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

    def test_timeout_retains_attempt_and_removes_base_copy_without_running_candidate(self):
        self.assertTrue(hasattr(comparison, "captured_process"), "comparison must own and bound its checker")
        self.checker = "print('safe')\n"
        root = self.repository("ok\n", "new\n")
        evidence = root / ".autocode/evidence"
        evidence.mkdir(parents=True)
        command = [sys.executable, "check.py", "a; echo not-a-command"]

        def expired(words, **kwargs):
            kwargs["output_stream"].write(b"partial diagnostic\n")
            raise subprocess.TimeoutExpired(words, kwargs["timeout"])

        with mock.patch.object(comparison.captured_process, "run_metadata", side_effect=expired) as run:
            with self.assertRaises(subprocess.TimeoutExpired):
                comparison.compare(command, "HEAD", root, evidence / "comparison.json")
        self.assertEqual(1, run.call_count, "a timed-out base must not run the candidate")
        self.assertEqual(command, run.call_args.args[0])
        self.assertGreater(run.call_args.kwargs["timeout"], 0)
        self.assertLessEqual(run.call_args.kwargs["timeout"], comparison.COMPARISON_TIMEOUT)
        self.assertTrue(run.call_args.kwargs["merge_output"])
        self.assertEqual("partial diagnostic\n", (evidence / "comparison.base.log").read_text())
        self.assertEqual([], list((root / ".autocode/scratch").iterdir()))
        self.assertFalse((evidence / "comparison.json").exists())

    def test_uncertain_descendant_cleanup_does_not_create_a_comparison(self):
        self.assertTrue(hasattr(comparison, "captured_process"), "comparison must own and bound its checker")
        self.checker = "print('safe')\n"
        root = self.repository("ok\n", "new\n")
        evidence = root / ".autocode/evidence"
        evidence.mkdir(parents=True)
        error = comparison.captured_process.ProcessError("descendant cleanup uncertain")
        with mock.patch.object(comparison.captured_process, "run_metadata", side_effect=error):
            with self.assertRaisesRegex(comparison.captured_process.ProcessError, "descendant cleanup uncertain"):
                comparison.compare([sys.executable, "check.py"], "HEAD", root, evidence / "comparison.json")
        self.assertEqual([], list((root / ".autocode/scratch").iterdir()))
        self.assertFalse((evidence / "comparison.json").exists())

    def test_capture_reports_uncertain_cleanup_without_publishing_a_receipt(self):
        self.assertTrue(hasattr(comparison, "captured_process"), "comparison must own and bound its checker")
        root = Path(tempfile.mkdtemp())
        self.addCleanup(__import__("shutil").rmtree, root)
        receipt = root / "comparison.json"
        error = comparison.captured_process.ProcessError("descendant cleanup uncertain")
        stderr = io.StringIO()
        with (
            mock.patch.object(comparison, "compare", side_effect=error),
            contextlib.redirect_stderr(stderr),
            self.assertRaises(SystemExit) as failure,
        ):
            capture_command._compare(argparse.ArgumentParser(prog="capture"), "HEAD", ["checker"], receipt, None)
        self.assertEqual(2, failure.exception.code)
        self.assertIn("no comparison was published", stderr.getvalue())
        self.assertIn("descendant cleanup uncertain", stderr.getvalue())
        self.assertFalse(receipt.exists())

    def test_missing_executable_remains_a_start_error(self):
        self.checker = "print('safe')\n"
        root = self.repository("ok\n", "new\n")
        evidence = root / ".autocode/evidence"
        evidence.mkdir(parents=True)
        with self.assertRaises(FileNotFoundError):
            comparison.compare([str(root / "no-such-checker")], "HEAD", root, evidence / "comparison.json")
        self.assertEqual([], list((root / ".autocode/scratch").iterdir()))
        self.assertFalse((evidence / "comparison.json").exists())

    def test_real_comparison_preserves_exact_argv_and_combined_stream_order(self):
        self.checker = (
            "import sys\n"
            "sys.stdout.write('stdout:' + sys.argv[1] + '\\n'); sys.stdout.flush()\n"
            "sys.stderr.write('stderr:' + sys.argv[1] + '\\n'); sys.stderr.flush()\n"
        )
        root = self.repository("ok\n", "new\n")
        evidence = root / ".autocode/evidence"
        evidence.mkdir(parents=True)
        word = "literal; $(echo expanded)"
        found = comparison.compare([sys.executable, "check.py", word], "HEAD", root, evidence / "comparison.json")
        output = f"stdout:{word}\nstderr:{word}\n"
        self.assertEqual((0, 0), (found["base_exit_code"], found["candidate_exit_code"]))
        self.assertEqual(output, Path(found["base_output"]).read_text())
        self.assertEqual(output, Path(found["full_output"]).read_text())

    def test_base_and_candidate_share_one_budget_and_keep_nonzero_exits(self):
        self.assertTrue(hasattr(comparison, "COMPARISON_TIMEOUT"), "comparison needs one bounded budget")
        self.checker = "print('safe')\n"
        root = self.repository("ok\n", "new\n")
        evidence = root / ".autocode/evidence"
        evidence.mkdir(parents=True)
        deadlines = []

        def completed(_command, _cwd, log, deadline):
            deadlines.append(deadline)
            log.write_text("WARNING: added\n" if len(deadlines) == 2 else "")
            return len(deadlines) + 1

        with mock.patch.object(comparison, "_run", side_effect=completed):
            found = comparison.compare([sys.executable, "check.py"], "HEAD", root, evidence / "comparison.json")
        self.assertEqual(2, len(deadlines))
        self.assertEqual(deadlines[0], deadlines[1])
        self.assertEqual((2, 3), (found["base_exit_code"], found["candidate_exit_code"]))
        self.assertEqual(["WARNING: added"], found["new_diagnostics"])

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

    def test_executed_comparison_event_authenticates_the_recorded_outputs(self):
        self.checker = "for line in open('doc.txt').read().splitlines():\n    if 'BROKEN' in line:\n        print('WARNING: ' + line)\n"
        root = self.repository("ok\n", "ok\nBROKEN ref\n")
        result = self.capture(root, ".autocode/evidence/cmp.json")
        self.assertEqual(0, result.returncode, result.stderr)
        events = root / ".autocode/events.jsonl"
        command = shlex.join(
            [
                sys.executable,
                str(ROOT / "tools/autocode.py"),
                "capture",
                "--compare-base",
                "HEAD",
                "--output",
                ".autocode/evidence/cmp.json",
                "--",
                sys.executable,
                "check.py",
            ]
        )
        events.write_text(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "type": "command_execution",
                        "command": command,
                        "aggregated_output": result.stdout + result.stderr,
                    },
                }
            )
            + "\n"
        )
        context = {
            "workspace": str(root),
            "criteria": {"C3": C3},
            "base_commit": git(root, "rev-parse", "HEAD"),
            "attempt": {"events": str(events)},
        }
        report = tester_report("C3", "python check.py", refs=[".autocode/evidence/cmp.json"])
        errors, _ = claim_evidence.judge("tester", report, context)
        self.assertEqual(1, len(errors))
        self.assertIn("WARNING: BROKEN ref", errors[0])
        events.write_text("")
        errors, _ = claim_evidence.judge("tester", report, context)
        self.assertEqual(1, len(errors))
        self.assertNotIn("WARNING: BROKEN ref", errors[0])

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

    def test_new_diagnostic_retains_numbers_in_message(self):
        self.checker = (
            "for number, line in enumerate(open('doc.txt').read().splitlines(), 1):\n"
            "    if 'BROKEN' in line:\n"
            "        print(f'doc.txt:{number}: WARNING: ' + line)\n"
        )
        root = self.repository("BROKEN ref1\n", "intro\nBROKEN ref2\n")
        result = self.capture(root, ".autocode/evidence/numbered.json")
        self.assertEqual(0, result.returncode, result.stderr)
        receipt = json.loads((root / ".autocode/evidence/numbered.json").read_text())
        self.assertEqual(["doc.txt:2: WARNING: BROKEN ref2"], receipt["new_diagnostics"])
        self.assertEqual(
            ["WARNING:13: failure"], comparison.new_lines(["WARNING:12: failure"], ["WARNING:13: failure"])
        )


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
        fixture_events = self.root / ".autocode/claim-events.jsonl"
        if fixture_events.exists():
            Path(record["events"]).write_text(fixture_events.read_text())
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
        fixture_events = self.root / ".autocode/claim-events.jsonl"
        if fixture_events.exists():
            events.write_text(events.read_text() + "\n" + fixture_events.read_text())
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
