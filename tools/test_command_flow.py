"""One config-registered tool runs planning, build, receipt-backed validation, and completion."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest


class ConfigToolFlow(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c", "user.email=f@example.test",
                        "commit", "--allow-empty", "-qm", "fixture"], check=True)
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        source = Path(__file__).resolve().parent
        shutil.copy2(source / "fake_command_tool.py", bin_dir / "fixture-tool")
        shutil.copy2(source / "goal_fixtures.py", bin_dir / "goal_fixtures.py")
        (bin_dir / "fixture-tool").chmod(0o755)
        config_home = self.root / "config"
        provider = config_home / "autocode" / "providers"
        provider.mkdir(parents=True)
        (provider / "fixturetool.toml").write_text(textwrap.dedent("""\
            name = "fixturetool"
            command = ["fixture-tool", "--report", "{report}", "--sandbox", "{sandbox}", "--model", "{model}", "--role", "{role}"]
            prompt = "stdin"
            models = ["fixture-model"]
            version_command = ["fixture-tool", "--version"]

            [roles]
            astra = { model = "fixture-model", effort = "high" }
            terra = { model = "fixture-model", effort = "medium" }
            sol = { model = "fixture-model", effort = "high" }
            completion = { model = "fixture-model", effort = "medium" }
            glm = { model = "fixture-model", effort = "medium" }
            plan_reviewer = { model = "fixture-model", effort = "high" }
        """))
        self.env = {**os.environ, "PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                    "PYTHONDONTWRITEBYTECODE": "1", "XDG_CONFIG_HOME": str(config_home),
                    "AUTOCODE_HOME": str(self.root / "registry-home")}
        self.entry = [sys.executable, str(source / "autocode.py")]

    def test_config_tool_completes_with_a_capture_receipt(self):
        result = subprocess.run([*self.entry, "--provider", "fixturetool", "--workspace", str(self.project),
                                 "--in-place", "--chat", "Build a greeting tool"],
                                cwd=self.root, env=self.env, input="CLI\nyes\n",
                                capture_output=True, text=True, timeout=60)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        run = next((self.project / ".autocode/runs").iterdir())
        state = json.loads((run / "state.json").read_text())
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual("fixturetool", state["settings"]["provider"])
        self.assertEqual({}, state["sessions"])
        sol = next(record for record in state["stages"] if record["stage"] == "sol")
        report = json.loads(Path(sol["output"]).read_text())
        self.assertTrue(report["checks"][0]["evidence_ref"].endswith("sol-greet.json"))
        self.assertFalse(report["checks"][0]["evidence_ref"].startswith("event:"))
        self.assertTrue(Path(report["checks"][0]["evidence_ref"]).is_file())
        self.assertEqual("read-only", sol["command"][sol["command"].index("--sandbox") + 1])
        terra = next(record for record in state["stages"] if record["stage"] == "terra")
        self.assertEqual("workspace-write", terra["command"][terra["command"].index("--sandbox") + 1])


if __name__ == "__main__":
    unittest.main()
