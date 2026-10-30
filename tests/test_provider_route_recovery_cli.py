"""Configured-provider model recovery through public CLI status and named actions."""

import json
import sys
import shlex
import subprocess
import unittest
from pathlib import Path

from . import test_subprocess
from autocode_taskrun import TaskRun


class ProviderRouteRecoveryCliTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    new_run_engine_args = ("--engine", "opencode")

    def test_gocode_recovery_offers_bare_models_and_checks_actual_availability(self):
        source = Path(__file__).resolve().parents[1] / "tools"
        config = Path(self.env["XDG_CONFIG_HOME"]) / "autocode/providers/gocode.toml"
        config.parent.mkdir(parents=True)
        command = [sys.executable, str(source / "fake_command_tool.py"), "--model", "{model}",
                   "--report", "{report}"]
        models = ["gpt-6.1-sol", "claude-opus-5-5", "glm-5", "qwen3-235b-a22b-2507"]
        config.write_text(
            'name = "gocode"\ncommand = ' + json.dumps(command)
            + '\nprompt = "stdin"\nmodels = ' + json.dumps(models)
            + '\nversion_command = ' + json.dumps([sys.executable, str(source / "fake_command_tool.py"), "--version"])
            + '\n[roles]\n' + '\n'.join(
                f'{role} = {{ model = "{model}", effort = "high" }}'
                for role, model in {
                    "astra": "glm-5", "glm": "glm-5", "terra": "gpt-6.1-sol",
                    "sol": "gpt-6.1-sol", "completion": "qwen3-235b-a22b-2507",
                    "plan_reviewer": "claude-opus-5-5",
                }.items()
            ) + '\n'
        )
        run = TaskRun.start(self.project, "Build greeting", options=(
            "--engine", "opencode", "--provider", "gocode", "--joint-planning",
            "--no-auto-select", "--evidence-provenance", "fake",
        ), env=self.env, timeout=180)
        view = run.status()
        self.assertEqual("PAUSED_CROSS_MODEL", view["status"])
        question = view["needs"]["questions"][0]
        self.assertEqual("route-sol", question["id"])
        for model in models[1:]:
            self.assertIn(model, question["why"])
        def respond(model, expected):
            command = shlex.split(run.status()["next_command"])
            self.assertEqual("autocode", command[0])
            command = [item.replace("route-sol=MODEL", "route-sol=" + model) for item in command[1:]]
            result = subprocess.run([*self.entry, *command], cwd=self.root, env=self.env,
                                    capture_output=True, text=True, timeout=60)
            self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
            return result

        self.assertIn("Models unavailable", respond("claude-unavailable", 2).stderr)
        rejected = run.status()
        self.assertEqual("gpt-6.1-sol", rejected["routes"]["sol"]["model"])
        self.assertEqual(view["route_assignments"], rejected["route_assignments"])
        run.assign_model("sol", "claude-opus-5-5")
        assigned = run.status()
        self.assertEqual("claude-opus-5-5", assigned["routes"]["sol"]["model"])
        self.assertEqual("gpt-6.1-sol", assigned["routes"]["terra"]["model"])
        self.assertEqual("gocode", assigned["provider"])
        self.assertEqual("claude-opus-5-5", assigned["route_assignments"][-1]["to"])
        self.assertEqual(view["usage"]["accounting"]["attempts"], assigned["usage"]["accounting"]["attempts"])
