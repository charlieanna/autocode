"""A Validator that certifies criteria with prose cannot complete a run (#1070)."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from scenarios import run  # isort: skip

from harness import catalog, verdict


def run_todo_cli(*, fault=None, reports=False):
    """One full fake CLI run of greenfield-todo-cli: (result, run state) for the fault given.

    With ``reports``, each Tester stage record also carries its saved ``raw`` and ``accepted``
    report bodies, read before the run's directory is removed."""
    with tempfile.TemporaryDirectory(prefix="lazy-validator-") as directory:
        args = argparse.Namespace(
            fake=True,
            fake_solution="reference",
            fake_fault=fault,
            profile=None,
            provider=None,
            hybrid=False,
            out=Path(directory),
            autocode=None,
            max_steps=None,
            timeout_minutes=None,
            local_docker=False,
            i_authorize_live_model_spend=False,
        )
        result = run.run_one(catalog.load("greenfield-todo-cli"), args)
        state = json.loads((Path(result["run_dir"]) / "state.json").read_text()) if result["run_dir"] else {}
        for row in state.get("stages", []) if reports else ():
            if row["stage"] == "sol":
                row["accepted"] = json.loads(Path(row["output"]).read_text())
                row["raw"] = json.loads(Path(row.get("reported_output") or row["output"]).read_text())
        return result, state


class LazyValidatorTests(unittest.TestCase):
    def test_honest_citations_still_complete(self):
        result, state = run_todo_cli()
        self.assertEqual(verdict.PASS, result["verdict"])
        self.assertEqual("TASK_COMPLETE", state["status"])

    def test_prose_citations_refuse_completion_even_with_passing_checks(self):
        # fault=lazy_validator: every check exits 0 and the oracle passes 10/10, yet a PASS row
        # whose evidence names nothing the runner verified must not end the run.
        result, state = run_todo_cli(fault="lazy_validator")
        self.assertEqual(verdict.HONEST_BLOCKER, result["verdict"])
        self.assertTrue(result["oracle_passed"])
        self.assertNotEqual("TASK_COMPLETE", state["status"])
        self.assertIn("cites evidence the runner did not verify", state["stop_reason"])

    def test_a_file_named_beside_passing_checks_completes_without_a_report_repair(self):
        # fault=extra_file_citation: each criterion cites its check and also "todo.py", as a live
        # Kilo Tester cited "hello.txt" (2026-10-20). The runner drops the extra name, keeps the
        # model's own report, and accepts; the old refusal cost a Resolver step and a repair call.
        result, state = run_todo_cli(fault="extra_file_citation", reports=True)
        self.assertEqual(verdict.PASS, result["verdict"])
        self.assertEqual("TASK_COMPLETE", state["status"])
        stages = [row["stage"] for row in state["stages"]]
        self.assertNotIn("sol_report_repair", stages)
        testers = [row for row in state["stages"] if row["stage"] == "sol" and row.get("dropped_evidence_refs")]
        self.assertTrue(testers, stages)
        for row in testers:
            self.assertTrue(all(drop["ref"] == "todo.py" for drop in row["dropped_evidence_refs"]))
            self.assertTrue(all("todo.py" in r["evidence_refs"] for r in row["raw"]["criterion_results"]))
            self.assertFalse(any("todo.py" in r["evidence_refs"] for r in row["accepted"]["criterion_results"]))
        self.assertFalse(any("todo.py" in r["evidence_refs"] for r in state["validation"]["criterion_results"]))


if __name__ == "__main__":
    unittest.main()
