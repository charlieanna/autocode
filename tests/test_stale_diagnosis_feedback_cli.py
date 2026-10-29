"""Same-chat correction refreshes a conflicting diagnosis before exact approval (#2468)."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

FEEDBACK = (
    "Correct diagnosis case T4 and criterion AC4: page([], 1, 5) already returns []. "
    "Preserve that empty-list result rather than requiring IndexError. "
    "Reclassify T4 as preserve and retain the paging fix, existing tests, paths and permissions."
)
BEFORE = "page([], 1, 5) raises IndexError"
AFTER = "page([], 1, 5) returns []"


class StaleDiagnosisFeedbackCliTests(unittest.TestCase):
    def start(self, *, authority=True, corrupt_scope=False):
        temporary = tempfile.TemporaryDirectory(prefix="stale-diagnosis-cli-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.scenario = catalog.load("bugfix-trivial")
        self.workspace = without_maintenance(materialize(self.scenario.seed, self.root / "project"))
        self.original_source = (self.workspace / "pager/__init__.py").read_bytes()
        flags, env = fake_setup(self.scenario, self.root, self.scenario.dir / "reference")
        self.trace = self.root / "provider-trace.jsonl"
        self.env = {
            **env,
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "STALE_DIAGNOSIS_TRACE": str(self.trace),
        }
        paths = self.env["PATH"].split(os.pathsep)
        self.env["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        provider = self.root / "bin/codex"
        amendment = textwrap.dedent(f"""
            _ordinary_report_for = report_for
            def report_for(stage, data):
                if stage == "requirements_gather" and data.get("message"):
                    return {{"message_id": data["message"]["id"],
                            "contract_token": data["message"]["contract_token"],
                            "intent": "change", "assistant_reply": "I will correct the T4 preservation requirement."}}
                report = _ordinary_report_for(stage, data)
                feedback = next((row for row in (data.get("brief_feedback") or [])
                    if row.get("text") == {FEEDBACK!r}), None)
                # Only the saved feedback source or the core's pinned correction
                # packet establishes this fixture's changed diagnosis.
                corrected = any(row.get("text") == {FEEDBACK!r}
                    for row in (data.get("source_inventory") or [])) or bool(feedback)
                corrected = corrected or (data.get("diagnosis_correction") or {{}}).get("feedback", {{}}).get("text") == {FEEDBACK!r}
                if stage == "investigate_bug":
                    report["test_cases"] = [
                        {{"id": "T1", "given": "eleven items and size five", "when": "page_count(11, 5)",
                          "then": "returns 3", "kind": "restore"}},
                        {{"id": "T4", "given": "empty items and size five", "when": "page([], 1, 5)",
                          "then": "returns []" if corrected else "raises IndexError",
                          "kind": "preserve" if corrected else "restore"}}]
                    report["invariant"] = "Count the last partial page; " + ({AFTER!r} if corrected else {BEFORE!r})
                    report["fix_plan"] = ["Correct page_count rounding; retain the existing empty-page behavior"] if corrected else []
                    if corrected and {corrupt_scope!r}:
                        report["affected_paths"].append("tests/test_pager.py")
                body = report.get("contract")
                if isinstance(body, dict) and isinstance(body.get("acceptance_criteria"), list):
                    body["acceptance_criteria"] = [
                        {{"id": "AC1", "criterion": "page_count(11, 5) returns 3 and the paging regression suite passes",
                          "verification_method": "test: test_t1_partial_page", "human_review": False}},
                        {{"id": "AC4", "criterion": {AFTER!r} if corrected else {BEFORE!r},
                          "verification_method": ("guard:" if corrected else "test:") + " test_t4_empty_page",
                          "human_review": False}}]
                    body["milestones"][0]["acceptance_criteria"] = ["AC1", "AC4"]
                    if body.get("initial_task"):
                        body["initial_task"]["acceptance_criteria"] = ["AC1", "AC4"]
                    cite_sources(body)
                    prior = next((row for row in (data.get("goal_contract") or {{}}).get("body", {{}})
                                  .get("acceptance_criteria", []) if row.get("id") == "AC4"), {{}})
                    if corrected and prior.get("criterion") == {BEFORE!r} and {authority!r}:
                        source = feedback or next(row for row in data["source_inventory"] if row.get("text") == {FEEDBACK!r})
                        report["contract_changes"] = [{{"item": "AC4", "change": "reworded", "basis": "user_feedback",
                            "answer_id": source["id"], "replacement": {AFTER!r}, "example_correction": None}}]
                return report
        """)
        marker = 'if __name__ == "__main__":'
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        source = source.replace(marker, amendment + "\n" + marker)
        marker = '    _report(report, sys.argv[sys.argv.index("-o") + 1])'
        witness = textwrap.dedent("""
            with Path(os.environ["STALE_DIAGNOSIS_TRACE"]).open("a") as handle:
                handle.write(json.dumps({"stage": stage, "repair": bool(data.get("report_repair")),
                    "error": data.get("error"), "handoff": data, "report": report,
                    "output": sys.argv[sys.argv.index("-o") + 1]}) + "\\n")
        """)
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(witness, "    ") + marker))
        self.run = None
        self.addCleanup(self.export)
        self.run = taskrun.TaskRun.start(
            self.workspace,
            self.scenario.brief,
            options=(*flags, "--spec-first", "--adaptive-planning", "--max-seconds", "900"),
            env=self.env,
            timeout=240,
            cwd=self.root,
        )
        self.confirm_requirements()
        self.first = self.run.advance_until_input()
        self.assertEqual("approve_plan", self.first["needs"]["kind"], self.diagnostic())
        self.first_goal = self.run.show_goal()
        self.first_settings = self.settings()
        self.saved_reports = {
            row["output"]: Path(row["output"]).read_bytes()
            for row in self.attempts()
            if row["stage"] in ("requirements_gather", "investigate_bug")
        }
        return self.run

    def attempts(self):
        return [json.loads(line) for line in self.trace.read_text().splitlines()] if self.trace.exists() else []

    def diagnostic(self):
        view = self.run.status() if self.run else {}
        return {
            "status": view.get("status"),
            "reason": view.get("stop_reason"),
            "needs": view.get("needs"),
            "attempts": [{key: row.get(key) for key in ("stage", "repair", "error")} for row in self.attempts()],
        }

    def settings(self):
        result = subprocess.run(
            [*self.run.command, "--run-dir", str(self.run.run_dir), "--status"],
            env={**os.environ, **self.env},
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        # First use of a recovery role adds an effort-fit audit row; its actual
        # model, effort and route remain in roles and are still compared exactly.
        return {key: value for key, value in json.loads(result.stdout)["settings"].items() if key != "effort_fits"}

    def confirm_requirements(self):
        view = self.run.advance_until_input()
        self.assertEqual("answer", view["needs"]["kind"], self.diagnostic())
        question = next(q for q in view["needs"]["questions"] if q["id"].startswith("requirements-review-"))
        self.run.answer(question["id"], "plan", resolver_token=view["needs"]["resolver_token"])

    def correct(self):
        self.run.feedback(FEEDBACK)
        view = self.run.advance_until_input()
        # Answer only an actually offered Requirements confirmation. The legacy
        # conversation path may reuse its prior confirmation after a correction.
        if view["needs"]["kind"] == "answer" and any(
            q["id"].startswith("requirements-review-") for q in view["needs"].get("questions", [])
        ):
            self.confirm_requirements()
            view = self.run.advance_until_input()
        gathered = [
            row for row in self.attempts() if row["stage"] == "requirements_gather" and "requirements" in row["report"]
        ]
        self.assertEqual(1, len(gathered), "Adaptive plan feedback keeps its accepted Requirements handoff")
        drafts = [row for row in self.attempts() if row["stage"] == "astra_discovery"]
        self.assertEqual(
            drafts[0]["handoff"]["requirements_handoff"]["output"],
            drafts[-1]["handoff"]["requirements_handoff"]["output"],
        )
        return view

    def export(self):
        if not (folder := os.environ.get("STALE_DIAGNOSIS_EVIDENCE_DIR")):
            return
        target = Path(folder) / self._testMethodName
        target.mkdir(parents=True)
        if self.trace.exists():
            shutil.copy2(self.trace, target / self.trace.name)
        if self.run:
            (target / "status.json").write_text(json.dumps(self.run.status(), indent=2))
            shutil.copytree(self.run.run_dir, target / "run")

    def test_same_chat_authorized_case_correction_reinvestigates_once_before_approval(self):
        self.start()
        view = self.correct()
        self.assertEqual("approve_plan", view["needs"]["kind"], self.diagnostic())
        attempts = self.attempts()
        investigations = [row for row in attempts if row["stage"] == "investigate_bug"]
        self.assertEqual(2, len(investigations), self.diagnostic())
        self.assertEqual("restore", investigations[0]["report"]["test_cases"][1]["kind"])
        self.assertEqual("preserve", investigations[1]["report"]["test_cases"][1]["kind"])
        refreshed = next(row for row in reversed(attempts) if row["stage"] == "astra_discovery")
        self.assertEqual([], refreshed["report"]["contract_changes"])
        self.assertEqual("preserve", refreshed["handoff"]["bug_diagnosis"]["test_cases"][1]["kind"])
        self.assertEqual(FEEDBACK, investigations[1]["handoff"]["diagnosis_correction"]["feedback"]["text"])
        self.assertEqual(investigations[0]["report"]["test_cases"][0], investigations[1]["report"]["test_cases"][0])
        self.assertFalse(any(row["repair"] for row in attempts), self.diagnostic())
        self.assertFalse(any(row["stage"] in ("write_acceptance_tests", "terra") for row in attempts))
        self.assertEqual(self.first_settings, self.settings())
        self.assertEqual(self.first["routes"], view["routes"])
        self.assertEqual(
            {key: value for key, value in self.first["scope"].items() if key != "identity"},
            {key: value for key, value in view["scope"].items() if key != "identity"},
            "Fresh scope evidence may change its identity, but never its boundaries",
        )
        self.assertNotEqual(self.first["needs"]["token"], view["needs"]["token"])
        self.assertGreater(view["displayed_plan"]["revision"], self.first["displayed_plan"]["revision"])
        for path, contents in self.saved_reports.items():
            self.assertEqual(contents, Path(path).read_bytes(), path)
        self.assertEqual(self.original_source, (self.workspace / "pager/__init__.py").read_bytes())
        self.assertIn(AFTER, self.run.show_goal())
        count = len(attempts)
        self.assertEqual("approve_plan", self.run.advance_until_input()["needs"]["kind"])
        self.assertEqual(count, len(self.attempts()), "inspection at the gate must not repeat the Investigator")
        with self.assertRaises(taskrun.TaskRunError):
            self.run.approve_plan(self.first["needs"]["token"])
        self.assertEqual(count, len(self.attempts()))
        self.run.approve_plan(view["needs"]["token"])
        self.assertEqual(count, len(self.attempts()), "approval records authority without launching Builder")

    def test_unbacked_kind_change_cannot_silently_replace_diagnosis(self):
        self.start(authority=False)
        view = self.correct()
        self.assertEqual("PAUSED_STALE_DIAGNOSIS", view["status"], self.diagnostic())
        self.assertNotEqual("approve_plan", view["needs"]["kind"], self.diagnostic())
        self.assertIn("admitted user-feedback correction", view["stop_reason"])
        self.assertEqual(1, sum(row["stage"] == "investigate_bug" for row in self.attempts()))
        self.assertFalse(any(row["stage"] in ("write_acceptance_tests", "terra") for row in self.attempts()))

    def test_refreshed_investigator_cannot_expand_source_scope(self):
        self.start(corrupt_scope=True)
        view = self.correct()
        self.assertNotEqual("approve_plan", view["needs"]["kind"], self.diagnostic())
        self.assertIn("cannot change affected_paths", view["stop_reason"], self.diagnostic())
        self.assertEqual(2, sum(row["stage"] == "investigate_bug" and not row["repair"] for row in self.attempts()))
        self.assertFalse(any(row["stage"] in ("write_acceptance_tests", "terra") for row in self.attempts()))
        self.assertEqual(self.first_settings, self.settings())
        self.assertEqual(self.original_source, (self.workspace / "pager/__init__.py").read_bytes())


if __name__ == "__main__":
    unittest.main()
