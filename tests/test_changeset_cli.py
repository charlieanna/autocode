"""`autocode changeset link` starts a linked run in another registered project (#2066, step 4).

Drives the real, offline CLI: the source run through the task-run interface, the link through
the `autocode changeset` command, and both runs' status views.
"""

import hashlib
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_scope_question_cli import ELSEWHERE, PART
from .test_taskrun import BRIEF, FIXTURE_OPTIONS

ROOT = Path(__file__).resolve().parents[1]
OPTIONS = (*FIXTURE_OPTIONS, "--joint-planning", "--no-adaptive-planning")


def register_project(home: Path, workspace: Path) -> None:
    """Add ``workspace`` to the registry as a project AutoCode has run in (docs/registry-api.md)."""
    path = home / "registry.json"
    document = json.loads(path.read_text()) if path.exists() else {"version": 1, "workspaces": {}, "runs": {}}
    resolved = str(workspace.resolve())
    key = f"workspace-{hashlib.sha256(resolved.encode()).hexdigest()[:24]}"
    document["workspaces"][key] = {"id": key, "workspace": resolved}
    home.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))


class ChangesetCliTests(unittest.TestCase):
    def setUp(self):
        self.source, env = fixture_workspace(self)
        self.member, _ = fixture_workspace(self)
        self.home = Path(env["AUTOCODE_HOME"])
        self.env = {**env, "AUTOCODE_FIXTURE_EXTERNAL_PART": PART}
        self.run = taskrun.TaskRun.start(
            self.source, f"{BRIEF} {ELSEWHERE}", options=OPTIONS, env=self.env, timeout=180
        )
        view = self.run.advance_until_input()
        self.assertEqual("workspace-scope-1", view["needs"]["questions"][0]["id"], view["stop_reason"])

    def changeset(self, *args):
        env = {**os.environ, **self.env}
        env.pop("AUTOCODE_FIXTURE_EXTERNAL_PART")  # the linked run's own request spans nothing
        return subprocess.run(
            [sys.executable, str(ROOT / "tools" / "autocode.py"), "changeset", *args],
            env=env,
            capture_output=True,
            text=True,
            timeout=300,
        )

    def link(self, target="greet-history", workspace=None):
        return self.changeset(
            "link",
            "--run-dir",
            str(self.run.run_dir),
            "--target",
            target,
            "--workspace",
            str(workspace or self.member),
            "--json",
            "--",
            *OPTIONS,
        )

    def test_a_linked_run_starts_in_the_registered_project_with_the_ready_brief(self):
        register_project(self.home, self.member)
        brief = next(
            row["brief"] for row in self.run.status()["scope"]["briefs"] if row["repository"] == "greet-history"
        )
        proc = self.link()
        self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
        summary = json.loads(proc.stdout)
        source, member = summary["runs"]
        self.assertEqual(("source", str(self.run.run_dir.resolve())), (source["role"], source["run_dir"]))
        self.assertEqual(("member", "greet-history"), (member["role"], member["target"]))
        self.assertEqual(str(self.member.resolve()), member["workspace"])
        [member_run] = taskrun.TaskRun.runs_in(self.member)
        self.assertEqual(str(member_run.resolve()), member["run_dir"])
        self.assertNotIn(member["status"], ("STARTING", "UNREADABLE"), member)

        linked = taskrun.TaskRun(self.member.resolve(), member_run, env=self.env)
        self.assertEqual(brief, json.loads((member_run / "state.json").read_text())["task"])
        [back] = linked.status()["scope"]["linked"]
        self.assertEqual(("source", str(self.run.run_dir.resolve())), (back["role"], back["run_dir"]))
        [there] = self.run.status()["scope"]["linked"]
        self.assertEqual(
            (summary["changeset"], "greet-history", member["run_dir"]),
            (there["changeset"], there["target"], there["run_dir"]),
        )

        again = self.link()
        self.assertEqual(1, again.returncode)
        self.assertIn("already linked", json.loads(again.stdout)["error"])
        self.assertEqual(1, len(taskrun.TaskRun.runs_in(self.member)), "a refused link starts no run")
        shown = self.changeset("status", "--run-dir", member["run_dir"], "--json")
        self.assertEqual(summary["changeset"], json.loads(shown.stdout)["changeset"], shown.stderr)

    def test_only_a_registered_project_and_a_found_target_can_be_linked(self):
        refused = self.link()
        self.assertEqual(1, refused.returncode)
        self.assertIn("is not a registered project", json.loads(refused.stdout)["error"])
        register_project(self.home, self.member)
        refused = self.link(target="billing")
        self.assertEqual(1, refused.returncode)
        self.assertIn("is not work this run found in another repository", json.loads(refused.stdout)["error"])
        self.assertEqual([], taskrun.TaskRun.runs_in(self.member), "nothing started for a refused link")
        listed = self.changeset("list", "--json")
        self.assertEqual({"changesets": []}, json.loads(listed.stdout))


if __name__ == "__main__":
    unittest.main()
