"""GoCode transport contract tests; all provider execution is offline."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_gocode as gocode


class GoCodeTransportTests(unittest.TestCase):
    def test_direct_adapter_uses_gocode_then_codex_never_opencode(self):
        command = gocode.launch(
            role="terra", workspace=Path("/workspace"), session=None,
            model="gocode-openai/terra", effort="high", sandbox="workspace-write",
            schema=Path("/schema.json"), output=Path("/output.json"),
        )
        self.assertEqual(["gocode", "exec", "codex", "exec"], command[:4])
        self.assertEqual("gpt-5.6-terra", command[command.index("--model") + 1])
        self.assertIn('model_reasoning_effort="high"', command)
        self.assertNotIn("opencode", command)

    def test_display_aliases_resolve_to_the_same_gocode_api_model(self):
        for alias, api_model in (("astra", "gpt-6-astra"), ("terra", "gpt-5.6-terra"),
                                 ("luna", "gpt-5.6-luna"), ("sol", "gpt-5.6-sol"),
                                 ("gpt-6-astra", "gpt-6-astra")):
            with self.subTest(alias=alias):
                command = gocode.launch(role="astra", workspace=Path("/workspace"), session=None,
                    model="gocode-openai/" + alias, effort="high", sandbox="read-only",
                    schema=Path("/schema.json"), output=Path("/output.json"))
                self.assertEqual(api_model, command[command.index("--model") + 1])

    def test_unknown_routes_fail_before_launch(self):
        for model in ("openai/gpt-6-astra", "gocode-openai/", "gocode-openai/astra\nother",
                      "gocode-openai/astra/other", "gocode-openai/unknown"):
            with self.subTest(model=model), self.assertRaises(ValueError):
                gocode.launch(role="astra", workspace=Path("/workspace"), session=None,
                    model=model, effort="high", sandbox="read-only", schema=Path("/schema.json"),
                    output=Path("/output.json"))

    def test_managed_gocode_identity_requires_a_credential_bundle(self):
        status = "\n".join(("gocode version: fixture", "mode: managed", "credential bundle: present"))
        with patch.object(gocode.shutil, "which", return_value="/fixture/gocode"), \
             patch.object(gocode.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=status, stderr="")):
            identity = gocode.local_settings(Path("/workspace"))
        self.assertEqual("gocode", identity["engine"])
        self.assertEqual("/fixture/gocode", identity["executable"])

    def test_missing_gocode_bundle_refuses_before_any_provider_request(self):
        status = "\n".join(("gocode version: fixture", "mode: managed", "credential bundle: absent"))
        with patch.object(gocode.shutil, "which", return_value="/fixture/gocode"), \
             patch.object(gocode.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=status, stderr="")), \
             self.assertRaisesRegex(RuntimeError, "authenticated managed route"):
            gocode.local_settings(Path("/workspace"))

    def test_authenticated_unmanaged_gocode_is_a_valid_direct_route(self):
        status = "\n".join(("gocode version: fixture", "mode: unmanaged", "GoCode authentication: ok via GoCode Client Service"))
        with patch.object(gocode.shutil, "which", return_value="/fixture/gocode"), \
             patch.object(gocode.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=status, stderr="")):
            identity = gocode.local_settings(Path("/workspace"))
        self.assertEqual("gocode", identity["engine"])

    def test_usage_and_service_health_changes_do_not_change_transport_identity(self):
        stable = "gocode version: fixture\nmode: unmanaged\nGoCode authentication: ok via GoCode Client Service"
        first = SimpleNamespace(returncode=0, stdout=stable + "\nlive budget: spent $1\nGoCode Inference Endpoint: reachable", stderr="")
        later = SimpleNamespace(returncode=0, stdout=stable + "\nlive budget: spent $2\nGoCode Inference Endpoint: unreachable", stderr="")
        with patch.object(gocode.shutil, "which", return_value="/fixture/gocode"), \
             patch.object(gocode.subprocess, "run", side_effect=[first, later]):
            before = gocode.local_settings(Path("/workspace"))
            after = gocode.local_settings(Path("/workspace"))
        self.assertFalse(gocode.transport_drift(after, before))

    def test_version_changes_still_invalidate_transport_identity(self):
        stable = "mode: unmanaged\nGoCode authentication: ok via GoCode Client Service"
        first = SimpleNamespace(returncode=0, stdout="gocode version: 1\n" + stable, stderr="")
        later = SimpleNamespace(returncode=0, stdout="gocode version: 2\n" + stable, stderr="")
        with patch.object(gocode.shutil, "which", return_value="/fixture/gocode"), \
             patch.object(gocode.subprocess, "run", side_effect=[first, later]):
            before = gocode.local_settings(Path("/workspace"))
            after = gocode.local_settings(Path("/workspace"))
        self.assertTrue(gocode.transport_drift(after, before))

    def test_gocode_joint_settings_assign_all_four_roles_without_opencode(self):
        args = SimpleNamespace(
            engine="gocode", joint_planning=False, glm_model=None, astra_model=None,
            terra_model=None, sol_model=None, astra_provider=None, terra_provider=None,
            sol_provider=None, reasoning_effort=None, astra_reasoning_effort=None,
            terra_reasoning_effort=None, sol_reasoning_effort=None, headroom=None,
            context_soft_tokens=None, rotate_after_input_tokens=None,
            legacy_iteration_ceiling=None, max_iterations=None, max_seconds=None,
            max_stage_seconds=None, max_idle_seconds=None, max_tool_seconds=None,
            max_reported_tokens=None, no_progress_limit=None, unlimited_iterations=False,
            glm_reasoning_effort="xhigh",
        )
        with patch.object(gocode, "local_settings", return_value={"engine": "gocode"}):
            settings = runner.configure(args, {"workspace": "/fixture", "iteration": 0})
        self.assertTrue(settings["joint_planning"])
        self.assertEqual({"gocode"}, {role["engine"] for role in settings["roles"].values()})
        self.assertEqual("gocode-openai/astra", settings["roles"]["astra"]["model"])
        self.assertEqual("gocode-openai/terra", settings["roles"]["terra"]["model"])
        self.assertEqual("gocode-openai/sol", settings["roles"]["sol"]["model"])
        self.assertEqual("gocode-openai/luna", settings["roles"]["glm"]["model"])
        self.assertEqual("gocode-openai/sol", settings["roles"]["completion"]["model"])
        self.assertEqual("xhigh", settings["roles"]["glm"]["reasoning_effort"])


class GoCodeSubprocessTests(unittest.TestCase):
    """Exercise the GoCode wrapper route with no OpenCode executable at all."""
    new_run_engine_args = ("--engine", "gocode")

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        bin_dir = self.root / "fixture-bin"
        bin_dir.mkdir()
        source = Path(__file__).resolve().parent
        for filename in ("fake_codex.py", "goal_fixtures.py"):
            shutil.copy2(source / filename, bin_dir / ("codex" if filename == "fake_codex.py" else filename))
        (bin_dir / "codex").chmod(0o755)
        self.env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                    "PYTHONDONTWRITEBYTECODE": "1", "AUTOCODE_HOME": str(self.root / "registry-home")}
        self.entry = [sys.executable, str(source / "autocode.py")]
        wrapper = self.root / "fixture-bin" / "gocode"
        wrapper.write_text('''#!/usr/bin/env python3
import os
import sys
if sys.argv[1:] == ["status"]:
    print("gocode version: fixture\\nmode: managed\\ncredential bundle: present")
    raise SystemExit(0)
if len(sys.argv) < 3 or sys.argv[1] != "exec" or "opencode" in sys.argv:
    raise SystemExit(91)
os.execvp(sys.argv[2], sys.argv[2:])
''')
        wrapper.chmod(0o755)

    def launch(self, args, expected, *, answers=None):
        if "--run-dir" not in args and "--engine" not in args:
            args = [*self.new_run_engine_args, *args]
        result = subprocess.run([*self.entry, "--workspace", str(self.project), *args], cwd=self.root, env=self.env,
                                input=answers, capture_output=True, text=True, timeout=30)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return result

    def saved(self):
        run = next((self.project / ".autocode/runs").iterdir())
        return run, json.loads((run / "state.json").read_text())

    def test_four_roles_complete_through_gocode_without_opencode(self):
        self.launch(["Build a greeting tool", "--chat", "--in-place"], 0, answers="CLI\nyes\nyes\n")
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        engines = {stage["engine"] for stage in state["stages"]}
        self.assertIn("gocode", engines)
        self.assertLessEqual(engines, {"gocode", "runner"})
        for stage in state["stages"]:
            if stage["engine"] == "runner":
                continue
            self.assertEqual(["gocode", "exec", "codex", "exec"], stage["command"][:4])
            self.assertNotIn("opencode", stage["command"])


if __name__ == "__main__":
    unittest.main()
