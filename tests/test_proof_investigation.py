"""A regression proof that still fails after the Builder's direct round goes to the failure Investigator.

Before, the Tester relayed the runner's verdict, the Completion Reviewer asked for the rework the proof had already
decided, and often the Resolver ran, before the Builder heard anything new (autocode_proof_investigation).
"""

from __future__ import annotations

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_builder_policy as builder_policy
import autocode_completion_result as completion
import autocode_goals as goals
import autocode_proof_findings as proof_findings
import autocode_proof_investigation as proof_investigation
import autocode_stuck_job as stuck_job
import autocode_support as support
import autopilot
import goal_fixtures


class ProofInvestigationTests(unittest.TestCase):
    def setUp(self):
        scratch = self.enterContext(tempfile.TemporaryDirectory(prefix="proof-investigation-"))
        self.root = Path(scratch).resolve()
        git = ["git", "-C", str(self.root), "-c", "user.name=t", "-c", "user.email=t@e"]
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "pager.py").write_text("def pages(n, size):\n    return n // size\n")
        subprocess.run([*git, "add", "pager.py"], check=True)
        subprocess.run([*git, "commit", "-qm", "init"], check=True)
        self.run = self.root / ".autocode" / "runs" / "proof-1"
        (self.run / "iterations" / "002").mkdir(parents=True)
        (self.run / "regression" / "proof-02").mkdir(parents=True)

    def state(self):
        state = {
            "version": 3,
            "workspace": str(self.root),
            "run_dir": str(self.run),
            "task": "Count a partial last page",
            "task_id": "t-pages",
            "status": "RUNNING",
            "phase": "VALIDATING",
            "next_stage": "tester",
            "iteration": 2,
            "settings": {
                "engine": "opencode",
                "builder_retry": dict(builder_policy.DEFAULTS),
                "roles": {
                    role: {"engine": "opencode", "model": "openai/gpt-6-sol", "reasoning_effort": "high"}
                    for role in ("task_planner", "planner", "plan_reviewer", "builder", "tester", "resolver")
                },
                "limits": {},
            },
            "stages": [],
            "history": [],
            "user_events": [],
        }
        goal_fixtures.approve_fixture(state, goals)
        revision = runner.source_scope.snapshot(self.root, state, base_snapshot=support.snapshot)["revision"]
        state["current_task"] = {
            "id": "task-1",
            "kind": "implement",
            "objective": "Count a partial last page",
            "affected_paths": ["pager.py"],
            "contract_hash": state["goal_contract"]["hash"],
            "contract_revision": state["goal_contract"]["revision"],
        }
        receipt = self.run / "regression" / "proof-02" / "verification.json"
        receipt.write_text(json.dumps({"verdict": "FAIL", "failures": ["test_partial_last_page failed"]}))
        state["regression_proof"] = {
            "verdict": "FAIL",
            "failures": ["test_partial_last_page failed"],
            "source_revision": revision,
            "path": str(receipt),
        }
        # The first failing source opened the finding and took the Builder's direct round.
        proof_findings.reconcile(state, revision, required=True, proved=False, reason="test_partial_last_page failed")
        self.assertTrue(proof_findings.builder_round(state, revision))
        # The Builder's next source fails too: this is its record.
        report = self.run / "iterations" / "002" / "builder-01.json"
        report.write_text('{"summary": "Rounded the page count down again"}\n')
        state["stages"].append(
            {
                "stage": "builder",
                "role": "builder",
                "iteration": 2,
                "output": str(report),
                "task_id": "task-1",
                "source_revision": revision,
                "exit_code": 0,
            }
        )
        state.update(status="RUNNING", phase="VALIDATING", next_stage="tester")
        return state, revision

    def test_a_proof_failing_after_the_builder_round_goes_to_the_investigator_not_the_tester(self):
        state, revision = self.state()
        self.assertTrue(completion._builder_first(state, revision, "tester"))
        self.assertEqual("investigate_stuck", state["next_stage"])
        request = state["stuck_investigation"]
        self.assertEqual("builder_failure", request["mode"])
        self.assertIn(proof_investigation.REASON, request["reason"])
        proof = request["failure_evidence"]["diagnosis"]["regression_proof"]
        self.assertEqual(("FAIL", "tester"), (proof["verdict"], proof["review_stage"]))
        # The Investigator may cite the proof's receipt as well as the Builder's report.
        self.assertIn(
            str(Path(state["regression_proof"]["path"]).resolve()), request["failure_evidence"]["evidence_refs"]
        )
        # A failing proof is AutoCode's machinery: the classification cannot ask the person.
        schema = stuck_job.schema(state)
        self.assertNotIn("needs_user", schema["properties"]["cause"]["enum"])
        self.assertEqual({"type": "string", "enum": [""]}, schema["properties"]["user_question"])

    def test_once_for_each_source(self):
        state, revision = self.state()
        self.assertTrue(proof_investigation.route(state, revision))
        state.pop("stuck_investigation")
        state.update(next_stage="tester")
        self.assertFalse(proof_investigation.route(state, revision))

    def test_the_existing_route_runs_whenever_the_classification_cannot_start_now(self):
        cases = {
            "the direct round is not spent": lambda st, rev: st["findings_ledger"][-1].pop("builder_rounds"),
            "the Investigator is off": lambda st, rev: st.update(version=2),
            "a report repair is pending": lambda st, rev: st.update(pending_report_repair={"original": {}}),
            "another stage is running": lambda st, rev: st.update(active_stage={"stage": "tester"}),
            "no Builder record of this source": lambda st, rev: st["stages"][-1].update(source_revision="other"),
            "a later stage than that Builder": lambda st, rev: st["stages"].append(
                {"stage": "builder", "exit_code": 1}
            ),
            "the proof passes": lambda st, rev: st["regression_proof"].update(verdict="PASS"),
            "the classification budget is spent": lambda st, rev: st.update(
                stuck_investigations=[
                    {"trigger": "builder_failure", "milestone_key": builder_policy.key(st), "outcome": "classified"}
                ]
                * builder_policy.classification_limit(st)
            ),
        }
        for label, edit in cases.items():
            with self.subTest(label):
                state, revision = self.state()
                edit(state, revision)
                before = copy.deepcopy(state)
                self.assertFalse(proof_investigation.route(state, revision))
                self.assertEqual(before, state, "nothing is held or recorded")

    def classify(self, state, **answer):
        request = state["stuck_investigation"]["failure_evidence"]
        output = self.run / "iterations" / "002" / "stuck-investigation-01.json"
        report = {
            "diagnosis": "pages() floors the division, so a partial last page is dropped.",
            "cause": "other",
            "guidance": "Round the page count up: (n + size - 1) // size.",
            "recommendation": "retry",
            "user_question": "",
            "evidence_refs": request["evidence_refs"][:1],
            "example": "pages(11, 5) returns 2; the test expects 3.",
            "probe": "",
            "untestable": "A unit fixture runs no probe.",
            "failure_class": "execution",
            "failure_id": request["failure_id"],
            **answer,
        }
        output.write_text(json.dumps(report))
        return stuck_job.apply(state, report, {"output": str(output), "changed_files": []}, self.root)

    def test_a_named_cause_sends_the_builder_its_guidance(self):
        state, revision = self.state()
        self.assertTrue(proof_investigation.route(state, revision))
        continuation = self.classify(state)
        self.assertEqual("builder", state["next_stage"])
        self.assertEqual("execution", continuation["diagnosis"]["failure_class"])
        self.assertIn("Round the page count up", continuation["diagnosis"]["guidance"])
        action = autopilot.route_builder_failure(state, continuation["failure_evidence"], continuation["reason"])
        self.assertEqual(("retry", "RUNNING", "builder"), (action, state["status"], state["next_stage"]))
        self.assertNotIn("builder_failure_hold", state)

    def test_no_cause_named_gives_the_run_back_to_the_review_it_stood_in_for(self):
        state, revision = self.state()
        self.assertTrue(proof_investigation.route(state, revision))
        self.assertIsNone(self.classify(state, failure_class="unknown", recommendation="pause"))
        self.assertEqual(("RUNNING", "tester"), (state["status"], state["next_stage"]))
        self.assertNotIn("builder_failure_hold", state)
        self.assertEqual("reviewed", state["stuck_investigations"][-1]["outcome"])
        # And the Tester then launches: this source was investigated once.
        self.assertFalse(completion._builder_first(state, revision, "tester"))
        # A later source failing the same finding is not classified again: the review route keeps it.
        row = state["findings_ledger"][-1]
        row["failed_revisions"].append("r2")
        state["regression_proof"]["source_revision"] = "r2"
        state["stages"].append({**state["stages"][-1], "source_revision": "r2"})
        self.assertFalse(proof_investigation.route(state, "r2"))
        state["stuck_investigations"][-1].pop("proof_finding")
        self.assertTrue(proof_investigation.route(state, "r2"), "a classification that named a cause does not stop it")

    def test_an_investigator_that_cannot_finish_gives_the_run_back_to_the_review_too(self):
        state, revision = self.state()
        self.assertTrue(proof_investigation.route(state, revision))
        status, reason = stuck_job.abandon(state, "provider exited 1 without a terminal report")
        self.assertEqual("", status, "nothing paused")
        self.assertIn("could not finish", reason)
        self.assertEqual(("RUNNING", "tester"), (state["status"], state["next_stage"]))
        self.assertNotIn("builder_failure_hold", state)
        self.assertNotIn("stop_reason", state)
        self.assertFalse(proof_investigation.route(state, revision))


if __name__ == "__main__":
    unittest.main()
