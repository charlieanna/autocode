"""Credential-free public chat and reopen proof for agent-interpreted gate replies."""

import json
import os
import shutil
import subprocess
import sys
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from tests.test_agent_step import fixture_workspace
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised provider requires supported Linux")
class GateChatCliTests(unittest.TestCase):
    def tearDown(self):
        result = self._outcome.result
        if any(test is self for test, _ in [*result.errors, *result.failures]):
            destination = (
                Path(os.environ.get("BUILD_AUDIT_ARTIFACTS", str(self.workspace.parents[1] / "gate-failures")))
                / self.workspace.parent.name
            )
            shutil.copytree(self.workspace.parent, destination, symlinks=True)
            print("Retained gate failure: " + str(destination))

    def setup_provider(self):
        workspace, env = fixture_workspace(self)
        self.workspace = workspace
        env["PATH"] = os.pathsep.join([env["PATH"].split(os.pathsep)[0], str(Path(sys.executable).parent), env["PATH"]])
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        amendment = textwrap.dedent("""
            if data.get("message", {}).get("gate_kind"):
                message = data["message"]
                intent = "question" if message["text"].startswith("Why") else "change" if ", but" in message["text"] else "confirm"
                report = {"message_id": message["id"], "gate_token": message["gate_token"], "intent": intent,
                          "assistant_reply": "That is a question about the saved understanding." if intent == "question" else
                                             "I will reconsider your requested correction." if intent == "change" else
                                             "You confirmed the displayed understanding or plan."}
        """)
        self.assertIn(marker, provider.read_text())
        provider.write_text(provider.read_text().replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        return workspace, env

    def test_plain_chat_reads_both_approvals_with_agents_and_completes_once(self):
        workspace, env = self.setup_provider()
        result = subprocess.run(
            [
                *taskrun.AUTOCODE,
                "chat",
                "--workspace",
                str(workspace),
                "--brief",
                BRIEF,
                "--",
                *FIXTURE_OPTIONS,
                "--no-adaptive-planning",
                "--max-parallel-builders",
                "1",
            ],
            input="yes coplete\nplease go ahead with this exact plan\n\n",
            text=True,
            capture_output=True,
            env={**os.environ, **env},
            timeout=300,
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        runs = list((workspace / ".autocode/runs").iterdir())
        self.assertEqual(1, len(runs))
        run = taskrun.TaskRun(workspace, runs[0], env=env, timeout=300)
        completed = run.status()
        self.assertTrue(completed["done"], completed.get("needs"))
        prompts = [
            json.loads(p.read_text().split("CURRENT HANDOFF DATA\n", 1)[1])
            for p in runs[0].glob("iterations/*/*.prompt.md")
            if "CURRENT HANDOFF DATA\n" in p.read_text()
        ]
        exchanges = {p["message"]["gate_kind"]: p for p in prompts if (p.get("message") or {}).get("gate_kind")}
        self.assertEqual({"requirements", "plan"}, set(exchanges))
        self.assertEqual("requirements_gather", exchanges["requirements"]["stage"])
        self.assertEqual("astra_finalize", exchanges["plan"]["stage"])
        self.assertEqual("yes coplete", exchanges["requirements"]["message"]["text"])
        self.assertEqual("please go ahead with this exact plan", exchanges["plan"]["message"]["text"])
        self.assertTrue((workspace / "greet.py").is_file())
        saved_cost, saved_usage = completed["cost"], completed["usage"]
        reopened = taskrun.TaskRun(workspace, runs[0], env=env, timeout=300).status()
        self.assertEqual(saved_cost, reopened["cost"])
        self.assertEqual(saved_usage, reopened["usage"])
        exported = subprocess.run(
            [*taskrun.AUTOCODE, "replay", str(runs[0]), "--export", str(workspace.parent / "gate-recording"), "--json"],
            text=True,
            capture_output=True,
            env={**os.environ, **env},
            timeout=120,
        )
        self.assertEqual(0, exported.returncode, exported.stdout + exported.stderr)
        manifest = json.loads(exported.stdout)["manifest"]
        replayed = subprocess.run(
            [
                *taskrun.AUTOCODE,
                "replay",
                manifest,
                "--into",
                str(workspace.parent / "gate-replayed/.autocode/runs/replay"),
                "--json",
            ],
            text=True,
            capture_output=True,
            env={**os.environ, **env},
            timeout=300,
        )
        (workspace.parent / "replay-stdout.json").write_text(replayed.stdout)
        (workspace.parent / "replay-stderr.json").write_text(replayed.stderr)
        self.assertEqual(0, replayed.returncode, replayed.stdout + replayed.stderr)
        self.assertTrue(json.loads(replayed.stdout)["view"]["done"])

    def test_question_preserves_understanding_and_mixed_plan_reply_does_not_approve(self):
        workspace, env = self.setup_provider()
        run = taskrun.TaskRun.start(
            workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"), env=env, timeout=300
        )
        initial = run.advance_until_input()
        self.assertEqual("requirements", initial["chat"]["gate"]["kind"])
        summary = initial["chat"]["review"]["summary"]
        run.feedback("Why this outcome?")
        answered = run.advance_until_input()
        self.assertEqual(summary, answered["chat"]["review"]["summary"])
        self.assertIsNone(answered["chat"]["gate"]["decision"])
        run.feedback("yes coplete")
        confirmed = run.advance_until_input()
        self.assertEqual("confirm", confirmed["chat"]["gate"]["decision"]["intent"])
        review = confirmed["chat"]["review"]
        run.answer(review["question_id"], review["answer"])
        planned = run.advance_until_input()
        self.assertEqual("plan", planned["chat"]["gate"]["kind"])
        run.feedback("yes, but change the output format")
        changed = run.advance_until_input()
        self.assertEqual("approve_plan", changed["needs"]["kind"])
        self.assertNotEqual(planned["needs"]["token"], changed["needs"]["token"])
        self.assertIn("yes, but change the output format", [row["text"] for row in changed["request_sources"]])
        self.assertFalse((workspace / "greet.py").exists())
        self.assertIsNone(changed.get("approved_contract"))


if __name__ == "__main__":
    unittest.main()
