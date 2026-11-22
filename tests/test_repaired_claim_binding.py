"""An accepted Planner repair may not present a removed contract-change row as published (#3908).

Live incident (run 20261119-144416-vscode-6944d8): the revision guard rejected an unchanged
AC28 rewording declaration ("drop that row"); the repair dropped the row but kept the concern
response and summary claiming the complete AC28 change entry had been re-emitted. Acceptance
applied that report verbatim, so a person and the final review read a correction the accepted
machine-readable change list does not contain.
"""

import json
import os
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS

CLAIM = "This revision re-emitted the complete C1 change entry"
SUMMARY = "Revision re-emitted the complete C1 change entry"


class RepairedClaimBindingCliTests(unittest.TestCase):
    def repaired_revision(self):
        """The review's nonblocking concern asks for a complete C1 change entry; the revision
        declares the row without changing C1 (the guard refuses it), and the repair drops the
        row while keeping the response and summary that claim it was re-emitted. The finalizer
        records the revision responses its handoff actually carries."""
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        self.finalize_handoff = provider.parent / "finalize-handoff.jsonl"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent(f"""
            if stage == "review_plan":
                report["concerns"] = [{{
                    "id": "C6", "concern": "The C1 change entry is incomplete",
                    "evidence_refs": ["goal_contract.body.acceptance_criteria"],
                    "requested_change": "Re-emit the complete C1 change entry in the revision",
                    "acceptance_test": "The revision declares a complete C1 change entry",
                    "blocking": False}}]
            elif stage == "revise_plan":
                report["summary"] = {SUMMARY!r}
                report["responses"] = [{{
                    "concern_id": "C6", "response": {CLAIM!r},
                    "evidence_refs": ["goal_contract.body.acceptance_criteria"],
                    "change": "Re-emitted the complete C1 change entry",
                    "acceptance_test": "The revision declares a complete C1 change entry"}}]
                if not repairing:
                    report["contract_changes"] = [{{
                        "item": "C1", "change": "reworded", "basis": "agent_proposed", "answer_id": "",
                        "replacement": "Greeting contract holds", "example_correction": None}}]
            elif stage == "finalize_plan":
                if not repairing:
                    revision = (data.get("planning") or {{}}).get("reports", {{}}).get("revise_plan", {{}})
                    with Path({str(self.finalize_handoff)!r}).open("a") as log:
                        log.write(json.dumps((revision.get("report") or {{}}).get("responses")) + "\\n")
                report["decisions"] = [{{
                    "concern_id": "C6", "decision": "The C1 entry stays as written",
                    "rationale": "The revision kept the C1 criterion", "resolved": True,
                    "acceptance_test": "The C1 criterion is unchanged"}}]
        """)
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        options = (*FIXTURE_OPTIONS, "--no-adaptive-planning")
        run = taskrun.TaskRun.start(workspace, BRIEF, options=options, env=env, timeout=180)
        return run, run.advance_until_input()

    def test_accepted_repair_marks_a_removed_rows_claim_unverified(self):
        run, view = self.repaired_revision()
        self.assertEqual("approve_plan", view["needs"]["kind"], (view["status"], view["stop_reason"]))
        state = json.loads((run.run_dir / "state.json").read_text())
        history = state.get("report_repair_history") or []
        self.assertEqual(1, len(history), "the refused declaration was repaired, not redrafted")
        self.assertEqual("accepted", history[-1]["result"])
        entry = state["planning"]["reports"]["revise_plan"]
        revision = entry["report"]
        self.assertEqual([], revision["contract_changes"], "the repair dropped the refused row")
        response = revision["responses"][0]["response"]
        self.assertIn(CLAIM, response, "the model's own words stay readable")
        self.assertIn("AutoCode", response, "the correction is attributed to the runner")
        self.assertIn("C1", response)
        self.assertIn("unverified", response, "the claim about the removed row is marked unverified")
        self.assertIn("unverified", revision["summary"], "the presented summary carries the same correction")
        # The saved repair artifact says what the runner applied, not the unqualified claim.
        saved = json.loads(Path(entry["output"]).read_text())
        self.assertEqual(revision["responses"], saved["responses"])
        self.assertEqual(revision["summary"], saved["summary"])
        # The runner records the binding as evidence, naming the row and the concern it answered.
        bound = history[-1].get("unverified_change_claims")
        self.assertTrue(bound, "the repair history records the unverified claim")
        self.assertEqual({"C1"}, {row["item"] for row in bound})
        self.assertIn("C6", {row["concern_id"] for row in bound})
        self.assertIn(["summary"], [row["where"] for row in bound], "the summary correction is recorded too")
        # The refused original stays archived with its row: the failed attempt is preserved.
        original = json.loads(Path(history[-1]["original_output"]).read_text())
        self.assertEqual(["C1"], [row["item"] for row in original["contract_changes"]])

    def test_the_final_review_reads_the_corrected_response(self):
        run, view = self.repaired_revision()
        self.assertEqual("approve_plan", view["needs"]["kind"], (view["status"], view["stop_reason"]))
        rows = [json.loads(line) for line in self.finalize_handoff.read_text().splitlines()]
        self.assertEqual(1, len(rows), "one final review read the accepted revision")
        response = rows[0][0]["response"]
        self.assertIn(CLAIM, response)
        self.assertIn("unverified", response, "the downstream reviewer reads the marked claim, not a published one")


if __name__ == "__main__":
    unittest.main()
