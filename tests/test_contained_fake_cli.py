"""#709 through the CLI: an OpenCode run whose stages launch contained under Linux bubblewrap.

The runner's side is real: run setup, each launch's model-free conformance checks and the
kernel-constrained shell are the production ones (opencode_fixture_cli native_boundary). Only the
OpenCode client is the checked fake, which with AUTOCODE_FIXTURE_QUALIFIED_OPENCODE=1 reports the
qualified version and answers those `debug agent` checks through the shell the runner configured.
The fake's own work, the scripted Codex delegate's edits and checks, still runs outside the
boundary; only the conformance commands and the Builder shell write planted with
AUTOCODE_FIXTURE_SHELL_ESCAPE go through the contained shell. So this proves the contained launch
path, the stage records, `--status` and a blocked write outside the workspace. It does not prove
OpenCode's own tool dispatch: the opt-in NativeToolTests in tests/test_tool_containment.py do.

As root, run it without CAP_SYS_ADMIN and CAP_SYS_PTRACE (docs/testing.md), as every CLI flow
that launches a provider: otherwise the run stops at PAUSED_PROCESS_CLEANUP.
"""

import json
import sys
import unittest

import autocode_tool_containment as containment

from . import test_containment_policy_cli as policy_cli

NATIVE_PROBLEM = containment.sandbox_problem()
CONTAINED_STAGES = ("terra", "sol", "astra_review")


@unittest.skipUnless(
    containment.backend() == containment.BUBBLEWRAP and NATIVE_PROBLEM is None,
    f"requires working Linux bubblewrap: {NATIVE_PROBLEM or sys.platform}",
)
class ContainedFakeOpenCodeFlow(unittest.TestCase):
    # Composed rather than inherited, so UncontainedToolsFlow's own tests do not run twice.
    new_run_engine_args = policy_cli.UncontainedToolsFlow.new_run_engine_args
    setUp = policy_cli.UncontainedToolsFlow.setUp
    launch = policy_cli.UncontainedToolsFlow.launch
    saved = policy_cli.UncontainedToolsFlow.saved
    status_view = policy_cli.UncontainedToolsFlow.status_view

    def build(self, *new_run_flags):
        """Plan, answer, approve and build the greeting tool while the Builder plants a write outside the workspace."""
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.escape = self.root / "planted-escape"
        self.assertFalse(self.escape.is_relative_to(self.project))
        self.env["AUTOCODE_FIXTURE_SHELL_ESCAPE"] = str(self.escape)
        planning = self.launch(["Build a greeting tool", "--no-chat", *new_run_flags], 2)
        self.assertNotIn("Refused before any stage launched", planning.stderr)
        run, _ = self.saved()
        self.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 2)
        self.launch(["--run-dir", str(run), "--approve-goal", self.saved()[1]["displayed_goal"]], 0)
        self.launch(["--run-dir", str(run), "--no-chat"], 0)
        run, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        return run, state

    def planted_commands(self, run):
        """The planted write's bash tool events, from the Builder's recorded OpenCode events."""
        events = [
            json.loads(line)
            for path in sorted(run.glob("iterations/*/builder-*.jsonl"))
            for line in path.read_text().splitlines()
            if line.strip()
        ]
        return [
            event["part"]["state"]
            for event in events
            if event.get("type") == "tool_use" and event["part"].get("callID") == "call_planted_escape"
        ]

    def test_contained_run_finishes_blocks_the_planted_write_and_its_status_names_the_backend(self):
        self.env["AUTOCODE_FIXTURE_QUALIFIED_OPENCODE"] = "1"
        run, state = self.build()  # No --allow-uncontained-tools: setup found the boundary available.
        self.assertFalse([e for e in state.get("user_events", []) if e["kind"] == "uncontained_tools_accepted"])
        launched = [record for record in state["stages"] if record["stage"] in CONTAINED_STAGES]
        self.assertEqual(set(CONTAINED_STAGES), {record["stage"] for record in launched})
        for record in launched:
            boundary = record["tool_containment"]
            self.assertEqual(containment.BUBBLEWRAP, boundary["backend"], record["stage"])
            self.assertRegex(boundary["backend_version"], r"^bubblewrap \d")
            self.assertNotIn("uncontained_tools", record)
        self.assertFalse(self.escape.exists(), "the Builder's planted write outside the workspace landed")
        planted = self.planted_commands(run)
        self.assertTrue(planted, "the Builder never ran the planted write")
        for command in planted:
            self.assertNotEqual(0, command["metadata"]["exit"], command)
            self.assertRegex(command["output"], "Read-only file system|Permission denied|No such file or directory")
        view = self.status_view(run)
        self.assertEqual("contained", view["tool_containment"])
        latest = [record for record in state["stages"] if isinstance(record.get("tool_containment"), dict)][-1]
        self.assertEqual(
            {
                "backend": containment.BUBBLEWRAP,
                "backend_version": latest["tool_containment"]["backend_version"],
                "stage": latest["stage"],
                "iteration": latest["iteration"],
                "started_at": latest["started_at"],
            },
            view["tool_containment_backend"],
        )

    def test_the_same_planted_write_lands_when_the_user_accepts_uncontained_tools(self):
        # The control that shows the contained test's absence check can fail.
        run, state = self.build(policy_cli.FLAG)
        self.assertTrue(self.escape.exists(), "the uncontained Builder's planted write did not run")
        planted = self.planted_commands(run)
        self.assertTrue(planted and all(command["metadata"]["exit"] == 0 for command in planted), planted)
        for record in state["stages"]:
            self.assertFalse(record.get("tool_containment"), record["stage"])
        view = self.status_view(run)
        self.assertEqual("uncontained_user_accepted", view["tool_containment"])
        self.assertIsNone(view["tool_containment_backend"])


if __name__ == "__main__":
    unittest.main()
