"""A reviewed draft-inference correction reaches exact approval through the public CLI.

Offline fixture only. The real first-review/revision/final-review admission paths
run; the provider supplies the semantic judgment and never grants approval.
"""

import json
import os
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS

WRONG = "The existing paging helper raises IndexError for page 1 of an empty list"
CORRECT = "The existing paging helper returns [] for page 1 of an empty list"
RATIONALE = (
    "pager.py returns [] for page([], 1, 3); the task and confirmed Requirements concern the greeting CLI "
    "and request no paging behavior change. The first draft invented the IndexError preservation claim."
)
FEEDBACK = (
    "The repository returns an empty list. Request fresh independent review; "
    "this grants no behavior change or plan approval."
)
BEFORE_INTERPRETATION = (
    "The empty-input description is mistaken, but no saved first-review correction receipt exists yet; "
    "keep existing behavior."
)
AFTER_INTERPRETATION = (
    "A saved first-review correction receipt now permits correcting the mistaken description while "
    "preserving existing behavior; no behavior change or plan approval is granted."
)


class DraftFailureCorrectionCliTests(unittest.TestCase):
    def start(self, *, stale=False, adaptive=False, compose=False):
        workspace, env = fixture_workspace(self)
        (workspace / "pager.py").write_text(
            "def page(items, number, size):\n"
            "    if number != 1:\n"
            "        raise IndexError(number)\n"
            "    return list(items[:size])\n"
        )
        subprocess.run(["git", "-C", str(workspace), "add", "pager.py"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-qm",
                "existing paging helper",
            ],
            check=True,
        )
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        trace = provider.parent / "draft-failure-trace.jsonl"
        env["DRAFT_FAILURE_TRACE"] = str(trace)
        amendment = textwrap.dedent(f"""
            with Path(os.environ["DRAFT_FAILURE_TRACE"]).open("a") as witness:
                witness.write(json.dumps({{"stage": stage, "repair": repairing, "error": repair_error}}) + "\\n")
            if stage == "astra_discovery":
                report["contract"]["important_failure_cases"].append({WRONG!r})
                if {compose!r}:
                    feedback = next(row for row in data["brief_feedback"] if row["text"] == {FEEDBACK!r})
                    report["contract"]["accepted_assumptions"].append({{
                        "text": {BEFORE_INTERPRETATION!r}, "basis": "user_feedback", "answer_id": feedback["id"]}})
            elif stage == "astra_challenge" and ({stale!r} or {WRONG!r} in data.get("goal_contract", {{}}).get("body", {{}}).get("important_failure_cases", [])):
                receipt = {{
                    "snapshot": data.get("draft_failure_correction_snapshot", "missing snapshot"),
                    "judgment": "unapproved_model_inference",
                    "before": {WRONG!r}, "after": {CORRECT!r}, "rationale": {RATIONALE!r}}}
                if {stale!r}:
                    receipt["snapshot"] = "draft-failure-v1:" + "0" * 64
                report["concerns"] = [{{
                    "id": "C3", "concern": "The draft invents an empty-page IndexError result",
                    "evidence_refs": ["pager.py", "goal_contract.body.important_failure_cases"],
                    "requested_change": "Correct only the mistaken preservation statement",
                    "acceptance_test": "The plan preserves the existing [] result",
                    "blocking": True, "draft_failure_correction": receipt}}]
            elif stage == "glm_revise":
                report["contract"] = copy.deepcopy(data["goal_contract"]["body"])
                report["contract"]["important_failure_cases"] = [
                    {CORRECT!r} if line == {WRONG!r} else line
                    for line in report["contract"]["important_failure_cases"]]
                report["contract_changes"] = [{{
                    "item": {WRONG!r}, "change": "reworded", "basis": "agent_proposed",
                    "answer_id": "", "replacement": {CORRECT!r}, "example_correction": None}}]
                if {compose!r}:
                    for assumption in report["contract"]["accepted_assumptions"]:
                        if assumption["text"] == {BEFORE_INTERPRETATION!r}:
                            assumption["text"] = {AFTER_INTERPRETATION!r}
                    report["contract_changes"].append({{
                        "item": {BEFORE_INTERPRETATION!r}, "change": "reworded", "basis": "agent_proposed",
                        "answer_id": "", "replacement": {AFTER_INTERPRETATION!r}, "example_correction": None}})
                report["responses"] = [{{
                    "concern_id": "C3", "response": "The exact reviewer-backed correction is applied",
                    "evidence_refs": ["pager.py"], "change": "Corrected the failure-case inference",
                    "acceptance_test": "The plan preserves the existing [] result"}}]
            elif stage == "astra_finalize":
                # Keep the real provider fixture's retention-format contract:
                # it already references the corrected revision in the handoff.
                report["decisions"] = [{{
                    "concern_id": "C3", "decision": "The corrected preservation statement is consistent",
                    "rationale": {RATIONALE!r}, "resolved": True,
                    "acceptance_test": "The plan preserves the existing [] result"}}]
        """)
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        options = (*FIXTURE_OPTIONS, "--adaptive-planning" if adaptive else "--no-adaptive-planning")
        if compose:
            options = (*options, "--spec-first")
        run = taskrun.TaskRun.start(workspace, BRIEF, options=options, env=env, timeout=180)
        if compose:
            self.assertEqual("answer", run.status()["needs"]["kind"])
            run.feedback(FEEDBACK)
            requirements = run.advance_until_input()
            self.assertEqual("answer", requirements["needs"]["kind"], self.diagnostic(requirements, trace))
            question = requirements["needs"]["questions"][-1]
            self.assertTrue(question["id"].startswith("requirements-review-"))
            run.answer(question["id"], "plan", resolver_token=requirements["needs"]["resolver_token"])
        view = run.advance_until_input()
        if evidence := os.environ.get("DRAFT_FAILURE_EVIDENCE_DIR"):
            destination = Path(evidence) / self._testMethodName
            destination.mkdir(parents=True)
            (destination / "status.json").write_text(json.dumps(view, indent=2))
            shutil.copy2(trace, destination / "provider-trace.jsonl")
            shutil.copytree(run.run_dir, destination / "run")
        return run, view, trace, workspace

    @staticmethod
    def attempts(trace):
        return [json.loads(line) for line in trace.read_text().splitlines()]

    def diagnostic(self, view, trace):
        return json.dumps({"stop_reason": view.get("stop_reason"), "attempts": self.attempts(trace)}, indent=2)

    def test_reviewed_correction_requires_final_review_and_exact_public_approval(self):
        run, view, trace, workspace = self.start()
        self.assertEqual("AWAITING_GOAL_APPROVAL", view["status"], self.diagnostic(view, trace))
        self.assertEqual("approve_plan", view["needs"]["kind"])
        shown = run.show_goal()
        failures = shown.split("\nImportant failure cases:\n", 1)[1].split("\nAcceptance criteria:\n", 1)[0]
        self.assertIn(CORRECT, failures)
        self.assertNotIn(WRONG, failures)
        self.assertIn(WRONG + " -> " + CORRECT, shown)
        self.assertIn("Plan Reviewer concern C3", shown)
        self.assertIn(RATIONALE, shown)
        self.assertIn("draft-failure-v1:", shown)
        attempts = self.attempts(trace)
        planning = {"astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"}
        self.assertEqual(
            ["astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"],
            [row["stage"] for row in attempts if row["stage"] in planning],
        )
        self.assertFalse(any(row["repair"] for row in attempts), attempts)
        self.assertFalse(any(row["stage"] == "terra" for row in attempts), attempts)
        self.assertFalse((workspace / "greet.py").exists())
        token = view["needs"]["token"]
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan("r0:stale")
        self.assertEqual(token, run.status()["needs"]["token"])
        approved = run.approve_plan(token)
        self.assertEqual(token, approved["approved_contract"]["token"])
        self.assertFalse(any(row["stage"] == "terra" for row in self.attempts(trace)))

    def test_stale_review_receipt_never_reaches_approval_or_builder(self):
        run, view, trace, workspace = self.start(stale=True)
        self.assertNotEqual("approve_plan", (view.get("needs") or {}).get("kind"), view.get("stop_reason"))
        attempts = self.attempts(trace)
        self.assertTrue(any("fresh first-review inference receipt" in row["error"] for row in attempts), attempts)
        self.assertFalse(any(row["stage"] in ("glm_revise", "astra_finalize", "terra") for row in attempts), attempts)
        self.assertFalse((workspace / "greet.py").exists())
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan("r0:stale")
        self.assertNotEqual("approve_plan", (run.status().get("needs") or {}).get("kind"))

    def test_receipt_composes_with_independently_allowed_feedback_interpretation(self):
        run, view, trace, workspace = self.start(compose=True, adaptive=True)
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), self.diagnostic(view, trace))
        shown = run.show_goal()
        failures = shown.split("\nImportant failure cases:\n", 1)[1].split("\nAcceptance criteria:\n", 1)[0]
        assumptions = shown.split("\nAccepted assumptions:\n", 1)[1].split("\nDelegated decisions:\n", 1)[0]
        self.assertIn(CORRECT, failures)
        self.assertNotIn(WRONG, failures)
        self.assertIn(AFTER_INTERPRETATION, assumptions)
        self.assertNotIn(BEFORE_INTERPRETATION, assumptions)
        self.assertIn("user_feedback", assumptions)
        self.assertIn(WRONG + " -> " + CORRECT, shown)
        self.assertIn("Plan Reviewer concern C3", shown)
        self.assertIn(RATIONALE, shown)
        self.assertIn("draft-failure-v1:", shown)
        attempts = self.attempts(trace)
        planning = {"astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"}
        self.assertEqual(
            ["astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"],
            [row["stage"] for row in attempts if row["stage"] in planning],
        )
        self.assertFalse(any(row["repair"] for row in attempts), attempts)
        self.assertFalse(any(row["stage"] == "terra" for row in attempts), attempts)
        self.assertFalse((workspace / "greet.py").exists())
        token = view["needs"]["token"]
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan("r0:stale")
        self.assertEqual(token, run.status()["needs"]["token"])
        self.assertEqual(token, run.approve_plan(token)["approved_contract"]["token"])
        self.assertFalse(any(row["stage"] == "terra" for row in self.attempts(trace)))

    def test_adaptive_final_review_keeps_disclosure_without_an_extra_correction_round(self):
        run, view, trace, workspace = self.start(adaptive=True)
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), self.diagnostic(view, trace))
        self.assertTrue(view["planning_depth"]["adaptive"])
        self.assertEqual(2, view["planning_depth"]["review_limit"])
        shown = run.show_goal()
        failures = shown.split("\nImportant failure cases:\n", 1)[1].split("\nAcceptance criteria:\n", 1)[0]
        self.assertIn(CORRECT, failures)
        self.assertNotIn(WRONG, failures)
        self.assertIn(WRONG + " -> " + CORRECT, shown)
        attempts = self.attempts(trace)
        planning = {"astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"}
        self.assertEqual(
            ["astra_discovery", "astra_challenge", "glm_revise", "astra_finalize"],
            [row["stage"] for row in attempts if row["stage"] in planning],
        )
        self.assertFalse(any(row["repair"] for row in attempts), attempts)
        self.assertFalse(any(row["stage"] == "terra" for row in attempts), attempts)
        self.assertFalse((workspace / "greet.py").exists())


if __name__ == "__main__":
    unittest.main()
