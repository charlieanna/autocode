"""A scripted scenario builds to completion on OpenCode with its stages contained by Linux bubblewrap (#709).

The scenario-layer counterpart of tests/test_contained_fake_cli.py. The harness drives
greenfield-greeting-cli through the real CLI as every fake run does, judged by the scenario's own
oracle, but on the OpenCode engine: the checked fake OpenCode client (tools/fake_opencode.py, copied
as ``opencode`` and run, never imported) hands each stage to this harness's scripted model, its
sibling ``codex``. With AUTOCODE_FIXTURE_QUALIFIED_OPENCODE=1 the client reports the qualified
OpenCode 1.18.33 and answers the runner's model-free conformance checks through the boundary shell.
Run setup, each launch's conformance checks and the kernel shell are the production ones, and the run
is not given --allow-uncontained-tools, so a host without the boundary would refuse it. As in the CLI
test, the scripted model's own edits and checks run outside the boundary: this proves the contained
launch path and the public status view, not OpenCode's own tool dispatch.

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
CONTAINED_STAGES = ("terra", "sol", "astra_review")


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
        result = run.run_one(catalog.load(SCENARIO), args, extra_flags=OPENCODE_FLAGS, extra_env=self.env)
        diagnostic = json.dumps(result, indent=2)
        self.assertEqual(verdict.PASS, result["verdict"], diagnostic)
        self.assertEqual("TASK_COMPLETE", result["runner_status"], diagnostic)
        self.assertIs(True, result["oracle_passed"], diagnostic)
        for stage in CONTAINED_STAGES:
            self.assertIn(stage, result["metrics"]["model_stage_names"], diagnostic)

        project = Path(result["evidence"]) / "project"
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
