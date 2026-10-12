"""A Validator that certifies criteria with prose cannot complete a run (#1070)."""

import argparse
import json
import tempfile
import unittest
from pathlib import Path

from scenarios import run  # isort: skip

from harness import catalog, verdict


def run_todo_cli(*, fault=None):
    """One full fake CLI run of greenfield-todo-cli: (result, run state) for the fault given."""
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


if __name__ == "__main__":
    unittest.main()
