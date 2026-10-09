"""The example Claude provider (examples/claude-provider) returns the report as structured output.

AutoCode's command-provider contract tells a stage to write its report to a file. The wrapper
replaces that instruction, so the model is not told both things: a live Validator (2026-09-29)
wrote a complete report with Bash, then returned a structured output without its checks.
"""
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

from providers import command
import autocode_support as support

EXAMPLE = Path(__file__).resolve().parents[1] / "examples" / "claude-provider"
spec = importlib.util.spec_from_file_location("claude_stage", EXAMPLE / "claude_stage.py")
claude_stage = importlib.util.module_from_spec(spec)
spec.loader.exec_module(claude_stage)
batch_spec = importlib.util.spec_from_file_location("batch", EXAMPLE / "batch.py")
batch = importlib.util.module_from_spec(batch_spec)
batch_spec.loader.exec_module(batch)


class PromptTests(unittest.TestCase):
    def test_autocode_s_write_a_file_instruction_is_replaced(self):
        config = tomllib.loads((EXAMPLE / "claude.toml").read_text())
        provider = command.CommandProvider(config, EXAMPLE / "claude.toml")
        prompt = provider.prompt_for_schema("Validate the work.\nCURRENT HANDOFF DATA\n{}", {"type": "object"},
                                            "/run/iterations/001/sol-01.jsonl")
        self.assertIn("to this file: /run/iterations/001/sol-01.json", prompt)  # the contract as AutoCode writes it
        adapted = claude_stage.adapt(prompt)
        self.assertNotIn("to this file", adapted)
        self.assertIn(claude_stage.STRUCTURED, adapted)
        self.assertIn("CURRENT HANDOFF DATA", adapted)

    def test_a_prompt_without_the_instruction_is_unchanged(self):
        self.assertEqual("Answer the question.", claude_stage.adapt("Answer the question."))


FAKE_CLAUDE = """#!{python}
import json, sys
sys.stdin.read()
report = {{"summary": "done"}}
rows = [{{"type": "assistant", "message": {{"content": [{{"type": "tool_use", "id": "t1", "name": "StructuredOutput",
                                                       "input": report}}]}}}},
        {{"type": "user", "message": {{"content": [{{"type": "tool_result", "tool_use_id": "t1",
                                                  "content": "Structured output provided successfully",
                                                  "is_error": {rejected}}}]}}}},
        {{"type": "result", "is_error": True, "result": "API Error: Server error mid-response",
          "usage": {{"input_tokens": 5}}, "total_cost_usd": 0.5}}]
for row in rows:
    print(json.dumps(row))
"""


class StreamTests(unittest.TestCase):
    """A report the CLI accepted survives an API error that ends the stream (live ladder-16, 2026-09-30)."""

    def stage(self, rejected):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            claude = root / "claude"
            claude.write_text(FAKE_CLAUDE.format(python=sys.executable, rejected=rejected))
            claude.chmod(0o755)
            (root / "schema.json").write_text("{}")
            env = {**os.environ, "PATH": f"{root}{os.pathsep}{os.environ['PATH']}"}
            done = subprocess.run([sys.executable, str(EXAMPLE / "claude_stage.py"), str(root), "workspace-write",
                                   "claude-haiku", "", str(root / "schema.json"), str(root / "report.json")],
                                  input="Build it.", capture_output=True, text=True, env=env, timeout=60)
            events = [json.loads(line) for line in done.stdout.splitlines()]
            report = json.loads((root / "report.json").read_text()) if (root / "report.json").exists() else None
            return done.returncode, events[-1], report

    def test_an_accepted_report_is_kept_when_the_api_fails_afterwards(self):
        code, last, report = self.stage(rejected=False)
        self.assertEqual((0, "turn.completed", {"summary": "done"}), (code, last["type"], report))

    def test_a_report_the_cli_rejected_is_not_used(self):
        code, last, report = self.stage(rejected=True)
        self.assertEqual((1, "turn.failed", None), (code, last["type"], report))
        self.assertIn("Server error mid-response", last["error"]["message"])


# A Builder that finishes its work and ends without calling StructuredOutput (live cent-drift, 2026-09-30).
FORGETFUL_CLAUDE = """#!{python}
import json, sys
sys.stdin.read()
if "--resume" in sys.argv:
    assert sys.argv[sys.argv.index("--resume") + 1] == "s1"
    row = {{"type": "result", "is_error": False, "session_id": "s1", "result": {said},
            "usage": {{"input_tokens": 2}}, "total_cost_usd": 0.25}}
    if {remembers}:
        row["structured_output"] = {{"summary": "done"}}
    print(json.dumps(row))
else:
    print(json.dumps({{"type": "result", "is_error": False, "session_id": "s1",
                      "result": "I have already called the StructuredOutput tool.",
                      "usage": {{"input_tokens": 5}}, "total_cost_usd": 0.5}}))
"""


class ForgottenReportTests(unittest.TestCase):
    """A stage that ends without its report is asked for it once, in the same session."""

    def stage(self, remembers, said="done"):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            claude = root / "claude"
            claude.write_text(FORGETFUL_CLAUDE.format(python=sys.executable, remembers=remembers, said=json.dumps(said)))
            claude.chmod(0o755)
            (root / "schema.json").write_text("{}")
            env = {**os.environ, "PATH": f"{root}{os.pathsep}{os.environ['PATH']}"}
            done = subprocess.run([sys.executable, str(EXAMPLE / "claude_stage.py"), str(root), "workspace-write",
                                   "claude-haiku", "", str(root / "schema.json"), str(root / "report.json")],
                                  input="Build it.", capture_output=True, text=True, env=env, timeout=60)
            events = [json.loads(line) for line in done.stdout.splitlines()]
            report = json.loads((root / "report.json").read_text()) if (root / "report.json").exists() else None
            (root / "events.jsonl").write_text(done.stdout)
            self.status = support.failure_status(root / "events.jsonl")
            self.events = events
            return done.returncode, events[-1], report

    def test_the_report_is_taken_from_the_reminder_and_both_calls_are_billed(self):
        code, last, report = self.stage(remembers=True)
        self.assertEqual((0, "turn.completed", {"summary": "done"}), (code, last["type"], report))
        self.assertEqual(0.75, last["cost_usd"])
        self.assertEqual(7, last["usage"]["input_tokens"])

    def test_a_stage_that_still_returns_no_report_fails_after_one_reminder(self):
        code, last, report = self.stage(remembers=False)
        self.assertEqual((1, "turn.failed", None), (code, last["type"], report))

    def test_the_model_s_own_words_are_never_the_provider_s_error(self):
        # AutoCode reads a failure's message for the provider's words. Prose that names a content filter or a
        # budget is the model's, so the stop stays uncertain: not a refusal, not a quota stop.
        said = "I added the profanity content filter and the token budget check but wrote no report."
        code, last, report = self.stage(remembers=False, said=said)
        self.assertEqual((1, "turn.failed", None), (code, last["type"], report))
        self.assertEqual("claude returned no structured report", last["error"]["message"])
        self.assertIn(said, [row["item"]["text"] for row in self.events if row["type"] == "item.completed"])
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", self.status)


class BatchTests(unittest.TestCase):
    """batch.py restarts only what a container restart killed: runs without a result.json."""

    def test_only_the_runs_still_missing_are_started_again(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for name, verdict in (("20260930T0759Z-bugfix-trivial-claude-tiers-a1", "PASS"),
                                  ("20260930T0800Z-bugfix-trivial-claude-tiers-b2", None),  # killed mid-run
                                  ("20260930T0801Z-review-then-fix-claude-tiers-c3", "FALSE_COMPLETE")):
                (out / name).mkdir()
                if verdict:
                    (out / name / "result.json").write_text(json.dumps(
                        {"verdict": verdict, "checks": [{"ok": True}], "wall_seconds": 60}))
            ids = ["bugfix-trivial", "review-then-fix", "discuss-cache-choice"]
            self.assertEqual(["bugfix-trivial", "review-then-fix", "discuss-cache-choice", "discuss-cache-choice"],
                             batch.missing(ids, out, 2))
            summary = batch.status(out)
            self.assertIn("finished 2 (1 FALSE_COMPLETE, 1 PASS), running 1", summary)
            self.assertIn("bugfix-trivial                 PASS 1/1 60s $0.00 | running", summary)

    def test_hybrid_runs_are_counted_and_shown_apart_from_natural_ones(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            for name in ("20261005T0759Z-feature-stock-refusals-claude-tiers-a1",
                         "20261005T0800Z-feature-stock-refusals-claude-tiers-hybrid-b2",
                         "20261005T0801Z-feature-stock-refusals-fake-hybrid-c3",
                         "20261005T0802Z-feature-stock-refusals-fake-d4"):
                (out / name).mkdir()
                (out / name / "result.json").write_text(json.dumps({"verdict": "PASS", "checks": [],
                                                                    "wall_seconds": 1}))
            ids = ["feature-stock-refusals"]
            self.assertEqual(["feature-stock-refusals"], batch.missing(ids, out, 2))
            self.assertEqual(["feature-stock-refusals"] * 2, batch.missing(ids, out, 3, "claude-tiers-hybrid"))
            self.assertEqual([], batch.missing(ids, out, 1, "fake-hybrid"))
            self.assertEqual(["feature-stock-refusals"], batch.missing(ids, out, 2, "fake"))
            hybrid = batch.mode(type("Args", (), {"fake": False, "hybrid": True})())
            self.assertEqual("claude-tiers-hybrid", hybrid)
            summary = batch.status(out)
            self.assertIn("feature-stock-refusals [claude-tiers-hybrid] PASS", summary)
            self.assertIn("feature-stock-refusals         PASS", summary)

    def test_the_qualification_list_names_real_scenarios(self):
        ids = batch.scenarios(EXAMPLE / "qualification.txt")
        self.assertEqual(17, len(ids))
        catalog = EXAMPLE.parents[1] / "scenarios" / "catalog"
        self.assertEqual([], [scenario for scenario in ids if not (catalog / scenario / "scenario.toml").is_file()])


if __name__ == "__main__":
    unittest.main()
