"""Public planning consumes the actual empty-conflict generation schema without weakening admission."""

import json
import os
import shutil
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


class ConflictGenerationCliTests(unittest.TestCase):
    def start(self, *, invent_conflict=False):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        trace = provider.parent / "conflict-generation-trace.jsonl"
        env["CONFLICT_GENERATION_TRACE"] = str(trace)
        amendment = textwrap.dedent(f"""
            field = schema.get("properties", {{}}).get("conflict_resolutions", {{}})
            with Path(os.environ["CONFLICT_GENERATION_TRACE"]).open("a") as witness:
                witness.write(json.dumps({{
                    "stage": stage, "repair": repairing, "error": repair_error,
                    "conflict_max_items": field.get("maxItems"),
                    "has_conflict_field": "conflict_resolutions" in schema.get("properties", {{}})}}) + "\\n")
            if {invent_conflict!r} and stage == "astra_discovery":
                report["conflict_resolutions"] = [{{
                    "requirement_ids": ["R1"], "basis": "user_feedback", "answer_id": "missing-feedback",
                    "source_quote": "No requirement conflict was recorded",
                    "resolution": "Invented resolution that must remain inadmissible"}}]
        """)
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--no-adaptive-planning"), env=env, timeout=180
        )
        view = run.advance_until_input()
        if evidence := os.environ.get("CONFLICT_GENERATION_EVIDENCE_DIR"):
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

    def test_no_recorded_conflicts_constrains_actual_requests_and_reaches_exact_approval(self):
        run, view, trace, workspace = self.start()
        self.assertEqual("approve_plan", (view.get("needs") or {}).get("kind"), self.diagnostic(view, trace))
        attempts = self.attempts(trace)
        constrained = [row for row in attempts if row["has_conflict_field"]]
        self.assertEqual(["astra_discovery", "glm_revise", "astra_finalize"], [row["stage"] for row in constrained])
        self.assertEqual([0, 0, 0], [row["conflict_max_items"] for row in constrained], constrained)
        self.assertFalse(any(row["repair"] for row in attempts), attempts)
        self.assertFalse(any(row["stage"] == "terra" for row in attempts), attempts)
        self.assertFalse((workspace / "greet.py").exists())
        self.assertIn("Greeting contract holds", run.show_goal())
        token = view["needs"]["token"]
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan("r0:stale")
        self.assertEqual(token, run.status()["needs"]["token"])
        self.assertEqual(token, run.approve_plan(token)["approved_contract"]["token"])
        self.assertFalse(any(row["stage"] == "terra" for row in self.attempts(trace)))

    def test_invented_conflict_is_still_refused_without_approval_or_builder(self):
        run, view, trace, workspace = self.start(invent_conflict=True)
        self.assertNotEqual("approve_plan", (view.get("needs") or {}).get("kind"), self.diagnostic(view, trace))
        attempts = self.attempts(trace)
        initial = next(row for row in attempts if row["stage"] == "astra_discovery" and not row["repair"])
        self.assertEqual(0, initial["conflict_max_items"])
        self.assertTrue(
            any("conflict_resolutions: too many items" in row["error"] for row in attempts),
            attempts,
        )
        self.assertFalse(
            any(row["stage"] in ("astra_challenge", "glm_revise", "astra_finalize", "terra") for row in attempts),
            attempts,
        )
        self.assertFalse((workspace / "greet.py").exists())
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan("r0:stale")
        self.assertNotEqual("approve_plan", (run.status().get("needs") or {}).get("kind"))


if __name__ == "__main__":
    unittest.main()
