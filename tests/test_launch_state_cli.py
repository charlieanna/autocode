"""Through the real CLI: an in-place run starts from the files its checkout holds at launch.

Real Git, real CLI and real unittest suites in scratch worktrees; no provider is launched except
the fake one the flow puts on PATH. Slow (tests/suite_slow.json): each case launches the CLI.
"""
import json
import subprocess
from pathlib import Path
import unittest

import autocode_regression as regression
import autocode_verify as verify
import autocode_workspaces as workspaces
from . import test_subprocess
from .test_launch_state import APP, AUTOCODE_EXCLUDE, BROKEN_APP, FEATURE, TEST_APP, build_state, files
from .test_verify import git, isolated_python


class InPlaceLaunchCli(unittest.TestCase):
    def flow(self):
        flow = test_subprocess.SubprocessFlow()
        flow.setUp()
        self.addCleanup(flow.doCleanups)
        return flow

    def launch(self, flow, workspace):
        result = subprocess.run([*flow.entry, "--workspace", str(workspace), "--engine", "codex", "--no-chat",
                                 "--in-place", "Add a feature function"], cwd=flow.root, capture_output=True,
                                text=True, env={**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"}, timeout=240)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        run = next((workspace / ".autocode/runs").iterdir())
        return run, json.loads((run / "state.json").read_text())

    def test_a_builder_cannot_break_an_untracked_launch_suite_and_pass(self):
        flow = self.flow()
        project = flow.project  # one empty commit
        with (project / ".git/info/exclude").open("a") as exclude:
            exclude.write(AUTOCODE_EXCLUDE)  # as after an earlier run's OpenCode stage
        (project / "app.py").write_text(APP)
        (project / "test_app.py").write_text(TEST_APP)
        head, status = git(project, "rev-parse", "HEAD"), git(project, "status", "--porcelain")
        run, state = self.launch(flow, project)
        checkout = (git(project, "rev-parse", "HEAD"), git(project, "status", "--porcelain"))
        ref = git(project, "for-each-ref", "--format=%(objectname)", regression.LAUNCH_REFS + run.name)
        # The Builder breaks greet(), deletes its test and adds a feature with its own test.
        (project / "app.py").write_text(BROKEN_APP)
        (project / "test_app.py").unlink()
        for name, text in FEATURE.items():
            (project / name).write_text(text)
        proof = regression.prove({**state, **build_state(state["base_commit"], isolated_python(self))}, project, run)
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("Existing test files were deleted: test_app.py", proof["failures"])
        self.assertEqual((head, status), checkout, "the launch must not commit, stage or move anything")
        self.assertEqual(["app.py", "test_app.py"], files(project, state["base_commit"]))
        self.assertEqual(state["base_commit"], ref)

    def test_a_new_run_in_an_earlier_tasks_worktree_starts_from_its_files(self):
        flow = self.flow()
        (flow.project / ".gitignore").write_text(".autocode/\n")
        git(flow.project, "add", ".gitignore")
        git(flow.project, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "ignore")
        worktree = Path(workspaces.create(flow.project, "Earlier task")["workspace"])
        (worktree / "app.py").write_text(APP)  # what the earlier task delivered, uncommitted
        (worktree / "test_app.py").write_text(TEST_APP)
        _, state = self.launch(flow, worktree)
        self.assertEqual([".gitignore", "app.py", "test_app.py"], files(worktree, state["base_commit"]))
        self.assertEqual(git(worktree, "rev-parse", "HEAD"), git(worktree, "rev-parse", state["base_commit"] + "^"))


if __name__ == "__main__":
    unittest.main()
