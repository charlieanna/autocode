"""Concise Builder reports retain public execution and contract freshness (#2463)."""

import json
import os
import shutil
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


class BuilderCriteriaCliTests(unittest.TestCase):
    def exercise(self, *, stale_hash=False):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        trace = provider.parent / "builder-criteria-trace.jsonl"
        env["BUILDER_CRITERIA_TRACE"] = str(trace)
        amendment = textwrap.dedent(f"""
            if stage == "terra" and {stale_hash!r}:
                report["contract_hash"] = "0" * 64
            with Path(os.environ["BUILDER_CRITERIA_TRACE"]).open("a") as witness:
                witness.write(json.dumps({{
                    "stage": stage, "repair": repairing, "error": repair_error,
                    "schema_properties": list(schema.get("properties", {{}})),
                    "asks_criteria_echo": ("Restate acceptance_criteria entries" in prompt
                        or "Report every approved criterion ID once" in prompt),
                    "expected_revision": (data.get("goal_contract") or {{}}).get("revision"),
                    "expected_hash": (data.get("goal_contract") or {{}}).get("hash"),
                    "report": report}}) + "\\n")
        """)
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=180)
        shown = run.status()
        self.assertEqual("approve_plan", (shown.get("needs") or {}).get("kind"), shown.get("stop_reason"))
        self.assertIn("Greeting contract holds", run.show_goal())
        token = shown["needs"]["token"]
        run.approve_plan(token)
        view = run.advance_until_input()
        attempts = [json.loads(line) for line in trace.read_text().splitlines()]
        if evidence := os.environ.get("BUILDER_CRITERIA_EVIDENCE_DIR"):
            destination = Path(evidence) / self._testMethodName
            destination.mkdir(parents=True)
            (destination / "status.json").write_text(json.dumps(view, indent=2))
            shutil.copy2(trace, destination / "provider-trace.jsonl")
            shutil.copytree(run.run_dir, destination / "run")
        return view, attempts, token

    def test_concise_builder_completes_with_schema_consistent_prompt_and_both_reviewers(self):
        view, attempts, token = self.exercise()
        self.assertEqual("TASK_COMPLETE", view["status"], view.get("stop_reason"))
        self.assertEqual(token, view["approved_contract"]["token"])
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
        self.assertFalse(any(row["repair"] for row in attempts), attempts)
        for stage in ("terra", "sol", "astra_review"):
            self.assertEqual(1, sum(row["stage"] == stage for row in attempts), stage)
        builder = next(row for row in attempts if row["stage"] == "terra")
        self.assertNotIn("acceptance_criteria", builder["schema_properties"])
        self.assertNotIn("acceptance_criteria", builder["report"])
        self.assertEqual(["6 tests passed"], builder["report"]["results"])
        self.assertEqual(builder["expected_hash"], builder["report"]["contract_hash"])
        self.assertEqual(builder["expected_revision"], builder["report"]["contract_revision"])
        self.assertFalse(builder["asks_criteria_echo"], "Builder prompt requests a field absent from its actual schema")

    def test_concise_builder_with_stale_contract_hash_is_refused_before_validation(self):
        view, attempts, token = self.exercise(stale_hash=True)
        self.assertFalse(view["done"])
        self.assertEqual("PAUSED_INVALID_OUTPUT", view["status"], view.get("stop_reason"))
        self.assertIn("another goal revision", view["stop_reason"])
        self.assertEqual(token, view["approved_contract"]["token"])
        builder = next(row for row in attempts if row["stage"] == "terra")
        self.assertNotIn("acceptance_criteria", builder["report"])
        self.assertNotEqual(builder["expected_hash"], builder["report"]["contract_hash"])
        self.assertFalse(any(row["stage"] in ("sol", "astra_review") for row in attempts), attempts)


if __name__ == "__main__":
    unittest.main()
