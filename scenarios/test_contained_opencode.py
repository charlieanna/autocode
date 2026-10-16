"""A scripted scenario builds to completion on OpenCode with its stages contained by Linux bubblewrap (#709).

The scenario-layer counterpart of tests/test_contained_fake_cli.py. The harness drives
greenfield-greeting-cli through the real CLI as every fake run does, judged by the scenario's own
oracle, but on the OpenCode engine: the checked fake OpenCode client (tools/fake_opencode.py, copied
as ``opencode`` and run, never imported) hands each stage to this harness's scripted model, its
sibling ``codex``. With AUTOCODE_FIXTURE_QUALIFIED_OPENCODE=1 the client reports the qualified
OpenCode 1.18.33 and answers the runner's model-free conformance checks through the boundary shell.
Run setup, each launch's conformance checks and the kernel shell are the production ones, and the run
is not given --allow-uncontained-tools, so a host without the boundary would refuse it. As in the CLI
test, AUTOCODE_FIXTURE_SHELL_ESCAPE makes the Builder write a path outside the project through the shell
its launch was given, and that write must be blocked; the Builder, Tester and Completion Reviewer
records in the state the harness saves as evidence must each name the boundary. The scripted model's
own edits and checks run outside the boundary: this proves the contained launch path, the stage records
and the public status view, not OpenCode's own tool dispatch.

Whether this host has the boundary is asked of `autocode doctor --json`, whose ``containment`` check
is the one run setup makes. Without a working bubblewrap the test skips, except in CI on Linux, where
the suite job installs bubblewrap before the scenario harness runs: there it fails instead. As root,
run it without CAP_SYS_ADMIN and CAP_SYS_PTRACE (docs/testing.md), as every scenario run.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import run  # noqa: E402
from harness import catalog, verdict  # noqa: E402
from harness.driver import default_autocode  # noqa: E402
from harness.processes import run_cli  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SCENARIO = "greenfield-greeting-cli"
# A fake run starts on the Codex engine with bare model names (harness.driver.FAKE_FLAGS). Given after
# those, these move it to OpenCode with the same models under OpenCode's provider/model names.
OPENCODE_FLAGS = (
    "--engine",
    "opencode",
    "--astra-model",
    "openai/gpt-6-astra",
    "--terra-model",
    "openai/gpt-5.6-terra",
    "--sol-model",
    "openai/gpt-5.6-sol",
    "--completion-model",
    "openai/gpt-6-astra",
    "--glm-model",
    "openai/gpt-5.6-sol",
    "--plan-reviewer-model",
    "openai/gpt-6-astra",
)
# The OpenCode stages that launch contained: the Builder, the Tester and the Completion Reviewer.
# Each one's stage record must name the boundary.
CONTAINED_STAGES = ("terra", "sol", "astra_review")


def planted_commands(run_dir):
    """The planted write's bash tool events, from the Builder's recorded OpenCode events."""
    events = [
        json.loads(line)
        for path in sorted(run_dir.glob("iterations/*/builder-*.jsonl"))
        for line in path.read_text().splitlines()
        if line.strip()
    ]
    return [
        event["part"]["state"]
        for event in events
        if event.get("type") == "tool_use" and event["part"].get("callID") == "call_planted_escape"
    ]


class ContainedOpenCodeScenarioTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="contained-opencode-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        bindir = self.root / "bin"
        bindir.mkdir()
        shutil.copy2(REPO / "tools/fake_opencode.py", bindir / "opencode")
        shutil.copy2(REPO / "scenarios/harness/fake_codex.py", bindir / "codex")
        for name in ("opencode", "codex"):
            (bindir / name).chmod(0o755)
        (self.root / "config").mkdir()
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}",
            "AUTOCODE_FIXTURE_QUALIFIED_OPENCODE": "1",
            # No contributor's own AutoCode or OpenCode configuration.
            "XDG_CONFIG_HOME": str(self.root / "config"),
        }
        # Never a real OpenCode client: the run would spend.
        self.assertEqual(bindir / "opencode", Path(shutil.which("opencode", path=self.env["PATH"]) or ""))

    def cli(self, args, *, timeout):
        env = {
            **os.environ,
            **self.env,
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        return run_cli([*default_autocode(), *args], env=env, cwd=REPO, timeout=timeout)

    def boundary(self):
        """The kernel tool boundary `autocode doctor --json` reports for a built-in OpenCode run here."""
        workspace = self.root / "doctor"
        workspace.mkdir()
        checked = self.cli(["doctor", "--json", "--engine", "opencode", "--workspace", str(workspace)], timeout=120)
        # Doctor exits 1 for this empty folder (no Git repository); its containment check is what is read.
        self.assertIn(checked.returncode, (0, 1), checked.stdout + checked.stderr)
        return json.loads(checked.stdout)["containment"]

    def test_a_scripted_build_completes_with_its_stages_contained_by_bubblewrap(self):
        found = self.boundary()
        if not found or found["backend"] != "bubblewrap" or found["problem"]:
            message = f"requires the Linux bubblewrap tool boundary; autocode doctor reports {found}"
            if sys.platform.startswith("linux") and (os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS")):
                self.fail(message)
            self.skipTest(message)
        args = argparse.Namespace(
            fake=True,
            profile=None,
            fake_solution="reference",
            out=self.root / "results",
            autocode=None,
            max_steps=None,
            timeout_minutes=10,
        )
        # The Builder writes this through its launch's shell (tools/fake_opencode.py): outside the project.
        escape = self.root / "planted-escape"
        env = {**self.env, "AUTOCODE_FIXTURE_SHELL_ESCAPE": str(escape)}
        result = run.run_one(catalog.load(SCENARIO), args, extra_flags=OPENCODE_FLAGS, extra_env=env)
        diagnostic = json.dumps(result, indent=2)
        self.assertEqual(verdict.PASS, result["verdict"], diagnostic)
        self.assertEqual("TASK_COMPLETE", result["runner_status"], diagnostic)
        self.assertIs(True, result["oracle_passed"], diagnostic)
        for stage in CONTAINED_STAGES:
            self.assertIn(stage, result["metrics"]["model_stage_names"], diagnostic)

        project = Path(result["evidence"]) / "project"
        self.assertFalse(escape.is_relative_to(project))
        self.assertFalse(escape.exists(), "the Builder's planted write outside the project landed")
        planted = planted_commands(Path(result["run_dir"]))
        self.assertTrue(planted, "the Builder never ran the planted write")
        for command in planted:
            self.assertNotEqual(0, command["metadata"]["exit"], command)
            self.assertRegex(command["output"], "Read-only file system|Permission denied|No such file or directory")
        # Each contained stage's own records, from the state the harness saved as evidence (run.run_one).
        state = json.loads((Path(result["evidence"]) / "state.json").read_text())
        launched = [record for record in state["stages"] if record["stage"] in CONTAINED_STAGES]
        self.assertEqual(set(CONTAINED_STAGES), {record["stage"] for record in launched})
        for record in launched:
            boundary = record.get("tool_containment")
            self.assertIsInstance(boundary, dict, f"{record['stage']} launched uncontained")
            self.assertEqual("bubblewrap", boundary["backend"], record["stage"])
            self.assertEqual(found["backend_version"], boundary["backend_version"], record["stage"])
            self.assertNotIn("uncontained_tools", record)

        status = self.cli(
            ["--workspace", str(project), "--run-dir", result["run_dir"], "--status", "--json"], timeout=60
        )
        self.assertEqual(0, status.returncode, status.stdout + status.stderr)
        view = json.loads(status.stdout)["view"]
        self.assertEqual("TASK_COMPLETE", view["status"])
        self.assertEqual("contained", view["tool_containment"])
        # Read from the latest contained launch's own record: the Completion Reviewer, in this host's boundary.
        launch = view["tool_containment_backend"]
        self.assertIsNotNone(launch, "no stage launched contained")
        self.assertEqual("bubblewrap", launch["backend"], launch)
        self.assertEqual(found["backend_version"], launch["backend_version"], launch)
        self.assertRegex(launch["backend_version"], r"^bubblewrap \d")
        self.assertEqual("astra_review", launch["stage"], launch)


if __name__ == "__main__":
    unittest.main()
