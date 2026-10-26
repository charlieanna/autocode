"""Supported Linux: direct HTTP fixtures through the actual supervised CLI."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_direct_config as config
import autocode_direct_runtime as runtime
import autocode_taskrun as taskrun

from tests.test_direct_connections import HttpFixture, settings


@unittest.skipUnless(sys.platform == "linux", "Supervised provider execution requires supported Linux")
class DirectCliTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="autocode-direct-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-qm",
                "base",
                "--allow-empty",
            ],
            check=True,
        )
        self.env = {
            "PATH": "/usr/bin:/bin",
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def save(self, value):
        from unittest import mock

        with mock.patch.dict(os.environ, self.env):
            config.save(value, "fixture-secret")

    def test_hi_uses_direct_http_and_requirements_with_no_external_agent_installed(self):
        self._greeting("compatible")

    def test_hi_uses_claude_messages_and_requirements_without_showing_approval(self):
        self._greeting("anthropic")

    def _greeting(self, protocol):
        stages = []

        def respond(body):
            prompt = (
                body["messages"][0]["content"][0]["text"] if protocol == "anthropic" else body["messages"][1]["content"]
            )
            data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
            stage = data["stage"]
            stages.append(stage)
            if stage == "recognize_workflow":
                report = {
                    "workflow": "build",
                    "reason": "Greeting needs an engineering outcome",
                    "signals": [],
                    "design_document": "",
                    "clarity": "vague",
                }
            elif stage == "requirements_gather":
                report = {
                    "assistant_reply": "Hi! What would you like to build or change?",
                    "job": "build",
                    "design_document": "",
                    "clarity": "vague",
                    "summary": "No task specified",
                    "intended_outcome": "Not yet specified by the user",
                    "required_behaviors": [],
                    "constraints": [],
                    "acceptance_tests": [],
                    "source_refs": [],
                    "proposed_assumptions": [],
                    "requirements": [],
                    "ignored_statements": [],
                    "workspace_scope": {"spans_repositories": False, "external_parts": []},
                    "conflicts": [],
                    "proposed_reframes": [],
                    "ignored_requirements": [],
                    "machine_resolutions": [],
                    "access_blockers": [],
                    "open_questions": [
                        {
                            "id": "outcome",
                            "question": "Hi! What would you like to build or change?",
                            "why": "A greeting names no engineering outcome",
                            "options": [],
                            "proposed_default": "",
                            "kind": "decision",
                            "category": "requested_outcome",
                            "delegable": False,
                        }
                    ],
                }
            else:
                return 400, {"error": "Unexpected stage"}
            if protocol == "anthropic":
                return 200, {
                    "type": "message",
                    "role": "assistant",
                    "content": [{"type": "text", "text": json.dumps(report)}],
                    "stop_reason": "end_turn",
                    "usage": {"input_tokens": 15, "output_tokens": 10},
                }
            return 200, {
                "choices": [{"message": {"role": "assistant", "content": json.dumps(report)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 15, "completion_tokens": 10},
            }

        fixture = HttpFixture(respond)
        self.addCleanup(fixture.close)
        self.save(settings(fixture.url, protocol))
        for executable in ("kilo", "opencode", "codex", "claude"):
            self.assertIsNone(shutil.which(executable, path=self.env["PATH"]))
        run = taskrun.TaskRun.start(
            self.workspace,
            "hi",
            options=(
                "--provider",
                "autocode",
                "--evidence-provenance",
                "fake",
                "--joint-planning",
                "--spec-first",
                "--max-seconds",
                "120",
                "--max-stage-seconds",
                "60",
            ),
            env=self.env,
            timeout=180,
        )
        view = run.status()
        self.assertEqual("answer", view["needs"]["kind"], view)
        self.assertIn("What would you like to build", view["needs"]["questions"][0]["question"])
        self.assertIsNone(view.get("displayed_plan"))
        self.assertEqual(
            ["requirements_gather"], stages, "A chat run talks with Requirements before recognizing the job"
        )
        self.assertEqual("autocode", view["provider"])
        self.assertFalse((self.workspace / "greet.py").exists())

    def test_discussion_tool_roundtrip_completes_through_the_supervised_cli(self):
        (self.workspace / "README.md").write_text("DIRECT_TOOL_EVIDENCE\n")
        subprocess.run(["git", "-C", str(self.workspace), "add", "README.md"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-qm",
                "readme",
            ],
            check=True,
        )
        stages = []
        tool_results = []

        def respond(body):
            data = json.loads(body["messages"][1]["content"].split("CURRENT HANDOFF DATA\n", 1)[1])
            stages.append(data["stage"])
            if data["stage"] == "requirements_gather":
                report = {
                    "job": "discuss",
                    "design_document": "",
                    "clarity": "clear",
                    "summary": "Answer a repository question",
                    "intended_outcome": "Explain README.md without changing files.",
                    "required_behaviors": ["Explain README.md without changing files."],
                    "constraints": ["Read only"],
                    "acceptance_tests": ["Answer cites README.md"],
                    "source_refs": ["README.md"],
                    "proposed_assumptions": [],
                    "requirements": [],
                    "ignored_statements": [],
                    "workspace_scope": {"spans_repositories": False, "external_parts": []},
                    "conflicts": [],
                    "proposed_reframes": [],
                    "ignored_requirements": [],
                    "machine_resolutions": [],
                    "access_blockers": [],
                    "open_questions": [],
                }
            elif body["messages"][-1]["role"] != "tool":
                return 200, {
                    "choices": [
                        {
                            "message": {
                                "role": "assistant",
                                "content": None,
                                "tool_calls": [
                                    {
                                        "id": "read-source",
                                        "type": "function",
                                        "function": {
                                            "name": "shell",
                                            "arguments": '{"command":"cat README.md; exit 7"}',
                                        },
                                    }
                                ],
                            },
                            "finish_reason": "tool_calls",
                        }
                    ],
                    "usage": {"prompt_tokens": 15, "completion_tokens": 10},
                }
            else:
                tool_results.append(json.loads(body["messages"][-1]["content"]))
                report = {
                    "answer": "README contains DIRECT_TOOL_EVIDENCE.",
                    "evidence": [
                        {"claim": "README contains the marker", "source": "README.md", "example": "", "probe": ""}
                    ],
                    "questions": [],
                    "note_path": "",
                    "note_content": "",
                }
            return 200, {
                "choices": [{"message": {"role": "assistant", "content": json.dumps(report)}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 15, "completion_tokens": 10},
            }

        fixture = HttpFixture(respond)
        self.addCleanup(fixture.close)
        self.save(settings(fixture.url))
        run = taskrun.TaskRun.start(
            self.workspace,
            "Explain README.md without changing files.",
            options=(
                "--provider",
                "autocode",
                "--evidence-provenance",
                "fake",
                "--max-seconds",
                "120",
                "--max-stage-seconds",
                "60",
            ),
            env=self.env,
            timeout=180,
        )
        view = run.status()
        self.assertTrue(view["done"], view.get("stop_reason"))
        self.assertEqual(["requirements_gather", "answer_question", "answer_question"], stages)
        self.assertEqual(7, tool_results[0]["exit_code"])
        self.assertIn("DIRECT_TOOL_EVIDENCE", tool_results[0]["output"])
        self.assertEqual("DIRECT_TOOL_EVIDENCE\n", (self.workspace / "README.md").read_text())

    def test_kernel_tools_enforce_readonly_and_hide_api_key(self):
        run_dir = self.workspace / ".autocode" / "runs" / "test"
        run_dir.mkdir(parents=True)
        source = self.workspace / "source.txt"
        source.write_text("unchanged")
        secret = self.root / "secret-auth.json"
        secret.write_text("fixture-secret")
        readonly = runtime.Shell(self.workspace, run_dir, False, True)
        code, _ = readonly("printf changed > source.txt")
        self.assertNotEqual(0, code)
        self.assertEqual("unchanged", source.read_text())
        code, output = readonly(f"cat {secret}")
        self.assertNotEqual(0, code)
        self.assertNotIn("fixture-secret", output)
        code, output = readonly("python3 -c \"print('x'*100000)\"")
        self.assertEqual(0, code)
        self.assertLess(len(output), 66000)
        self.assertIn("output truncated", output)
        code, _ = readonly("cat .autocode/runs/test/state.json")
        self.assertNotEqual(0, code)
        writable = runtime.Shell(self.workspace, run_dir, True, False)
        code, _ = writable("printf changed > source.txt")
        self.assertEqual(0, code)
        self.assertEqual("changed", source.read_text())
        code, _ = writable("printf forged > .autocode/forged")
        self.assertNotEqual(0, code)
