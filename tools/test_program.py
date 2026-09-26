"""Program runner: manifest rules, derivation from an approved plan, waves, merges, gates.

Execution tests use real Git worktrees and a scripted stand-in for the child
``autocode`` process; git commands pass through to the real binary. No provider
is launched. The final test drives the real CLI with the fake Codex fixture up
to the first human gate.
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import autocode_goals as goals  # noqa: E402
import autocode_program as program  # noqa: E402
import goal_fixtures  # noqa: E402
import task_scenarios  # noqa: E402
from tools import test_subprocess  # noqa: E402

REAL_RUN = subprocess.run


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def manifest(*, deploy=False, integration=True):
    rows = [
        {"id": "contracts", "kind": "code", "brief": "Write the shared contracts", "owns": ["contracts/"], "depends_on": []},
        {"id": "a", "kind": "code", "brief": "Build service a", "owns": ["a/"], "depends_on": ["contracts"]},
        {"id": "b", "kind": "code", "brief": "Build service b", "owns": ["b/"], "depends_on": ["contracts"]},
    ]
    if integration:
        rows.append({"id": "integration", "kind": "integration", "brief": "Validate the whole flow", "owns": ["tests/"],
                     "depends_on": ["a", "b"]})
    if deploy:
        rows.append({"id": "deploy", "kind": "deployment", "brief": "Write deployment descriptors", "owns": ["deploy/"],
                     "depends_on": ["integration"]})
    return {"version": 1, "name": "Demo program", "brief": "A two-service demo",
            "shared": {"constraints": ["stdlib only"], "interfaces": [{"id": "contracts", "summary": "JSON shapes", "paths": ["contracts/"]}]},
            "workstreams": rows}


class ManifestTests(unittest.TestCase):
    def test_valid_manifest_and_scenario_manifest_load(self):
        program.validate_manifest(manifest(deploy=True))
        program.validate_manifest(copy.deepcopy(task_scenarios.PROGRAM_MANIFEST))

    def test_rejects_cycles_unknown_and_duplicate_ids(self):
        value = manifest(); value["workstreams"][0]["depends_on"] = ["a"]
        with self.assertRaisesRegex(ValueError, "cycle"):
            program.validate_manifest(value)
        value = manifest(); value["workstreams"][1]["depends_on"] = ["nope"]
        with self.assertRaisesRegex(ValueError, "unknown"):
            program.validate_manifest(value)
        value = manifest(); value["workstreams"][2]["id"] = "a"
        with self.assertRaisesRegex(ValueError, "unique"):
            program.validate_manifest(value)

    def test_parallel_workstreams_cannot_share_ownership_but_dependent_ones_can(self):
        value = manifest(); value["workstreams"][2]["owns"] = ["a/handlers"]
        with self.assertRaisesRegex(ValueError, "both own"):
            program.validate_manifest(value)
        value = manifest(); value["workstreams"][2]["owns"] = ["a/handlers"]; value["workstreams"][2]["depends_on"] = ["a"]
        program.validate_manifest(value)

    def test_code_workstreams_declare_ownership_and_paths_stay_inside_the_repo(self):
        value = manifest(); value["workstreams"][1]["owns"] = []
        with self.assertRaisesRegex(ValueError, "must declare"):
            program.validate_manifest(value)
        for bad in ("../outside", "/abs", ".git/hooks", ".autocode/runs", "src/*", "src/[ab].py"):
            value = manifest(); value["workstreams"][1]["owns"] = [bad]
            with self.assertRaisesRegex(ValueError, "not an allowed"):
                program.validate_manifest(value)

    def test_ui_workstreams_are_explicitly_deferred(self):
        value = manifest(); value["workstreams"][1]["kind"] = "ui"
        with self.assertRaisesRegex(ValueError, "UI checkpoint recovery"):
            program.validate_manifest(value)

    def test_integration_must_cover_every_workstream_and_be_unique(self):
        value = manifest(); value["workstreams"][-1]["depends_on"] = ["a"]
        with self.assertRaisesRegex(ValueError, "must \\(transitively\\) depend on b"):
            program.validate_manifest(value)
        value = manifest(); value["workstreams"].append({"id": "x", "kind": "integration", "brief": "again", "owns": [], "depends_on": ["a"]})
        with self.assertRaisesRegex(ValueError, "At most one integration"):
            program.validate_manifest(value)

    def test_deployment_placement_rules(self):
        value = manifest(deploy=True, integration=False)
        value["workstreams"][-1]["depends_on"] = ["a", "b"]
        value["workstreams"].append({"id": "c", "kind": "code", "brief": "after deploy", "owns": ["c/"], "depends_on": ["deploy"]})
        with self.assertRaisesRegex(ValueError, "cannot depend on a deployment"):
            program.validate_manifest(value)
        value = manifest(deploy=True); value["workstreams"][-1]["depends_on"] = ["a"]
        with self.assertRaisesRegex(ValueError, "must \\(transitively\\) depend on the integration"):
            program.validate_manifest(value)

    def test_brief_carries_shared_context_ownership_and_merged_prerequisites(self):
        value = program.validate_manifest(manifest())
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "program.json"
            source.write_text(json.dumps(value))
            state = program.new_state(source, value, Path(tmp), "demo")
        state["workstreams"]["contracts"]["status"] = "MERGED"
        text = program.compose_brief(value, value["workstreams"][1], state)
        self.assertIn("PROGRAM WORKSTREAM a (code)", text)
        self.assertIn("stdlib only", text)
        self.assertIn("contracts: JSON shapes", text)
        self.assertIn("- contracts: Write the shared contracts", text)
        self.assertIn("only under: a.", text)
        self.assertIn("do not modify): b, contracts, tests.", text)
        self.assertIn("do not merge branches", text)

    def test_integration_brief_allows_cross_component_repairs(self):
        value = program.validate_manifest(manifest())
        text = program.compose_brief(value, value["workstreams"][-1], {"workstreams": {}})
        self.assertIn("Ownership exception", text)
        self.assertNotIn("Paths owned by other workstreams (do not modify)", text)

    def test_dependent_ownership_does_not_forbid_its_own_paths(self):
        value = manifest()
        value["workstreams"][2].update(owns=["a/handlers"], depends_on=["a"])
        program.validate_manifest(value)
        text = program.compose_brief(value, value["workstreams"][2], {"workstreams": {}})
        self.assertIn("only under: a/handlers", text)
        self.assertNotIn("do not modify): a,", text)


class DeriveTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        env = patch.dict(os.environ, {"AUTOCODE_HOME": str(self.root / "registry-home")}); env.start(); self.addCleanup(env.stop)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        git(self.root, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "--allow-empty", "-qm", "fixture")
        self.state = {"version": 2, "workspace": str(self.root), "task": "Build greeting and goodbye", "status": "RUNNING",
                      "iteration": 1, "sessions": {}, "stages": [], "history": [], "next_stage": "terra", "acceptance_criteria": [],
                      "settings": {"roles": {r: {"model": r, "reasoning_effort": "high"} for r in ("astra", "terra", "sol")},
                                   "transport_identity": {"auth_mode": "fixture"}, "headroom": {"enabled": False},
                                   "context_soft_tokens": 10000,
                                   "limits": {"iteration_ceiling": 5, "max_seconds": None, "max_reported_tokens": None,
                                              "no_progress_batches": 3}}}
        goals.migrate(self.state)

    def two_milestones(self):
        body = goal_fixtures.body()
        body["acceptance_criteria"].append({"id": "C2", "criterion": "Goodbye CLI prints Goodbye, NAME",
                                            "verification_method": "Run bye.py", "human_review": False})
        body["milestones"].append({"id": "M2", "objective": "Deliver goodbye CLI", "acceptance_criteria": ["C2"],
                                   "depends_on": ["M1"], "affected_paths": ["bye.py"]})
        return body

    def test_unapproved_plan_cannot_become_a_program(self):
        goals.install_draft(self.state, self.two_milestones(), origin="test")
        with self.assertRaisesRegex(ValueError, "approved plan"):
            program.derive_manifest(self.state)

    def test_approved_milestones_become_workstreams_plus_integration(self):
        goals.install_draft(self.state, self.two_milestones(), origin="test")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        value = program.derive_manifest(self.state, source_run=self.root / "run")
        ids = [row["id"] for row in value["workstreams"]]
        self.assertEqual(["M1", "M2", "integration"], ids)
        by_id = {row["id"]: row for row in value["workstreams"]}
        self.assertEqual(["greet.py"], by_id["M1"]["owns"])
        self.assertEqual(["M1"], by_id["M2"]["depends_on"])
        self.assertEqual(["M2"], by_id["integration"]["depends_on"])
        self.assertIn("C2: Goodbye CLI prints Goodbye, NAME", by_id["M2"]["brief"])
        self.assertIn("Run the CLI with a name", by_id["integration"]["brief"])
        self.assertEqual(["C1", "C2"], by_id["integration"]["acceptance_criteria"])
        self.assertEqual(self.state["goal_contract"]["hash"], value["contract"]["hash"])
        self.assertEqual(["Python standard library only"], value["shared"]["constraints"])
        self.assertEqual(str(self.root / "run"), value["source_run"])

    def test_every_child_receives_the_complete_approved_contract(self):
        body = goal_fixtures.body(human=True)
        goals.install_draft(self.state, body, origin="test")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        value = program.derive_manifest(self.state)
        self.assertEqual(body, value["contract"]["body"])
        for row in value["workstreams"]:
            text = program.compose_brief(value, row, {"workstreams": {}})
            self.assertIn(json.dumps(body, indent=2), text)
            self.assertIn('"human_review": true', text)
            self.assertIn("CLI regression tests", text)
            self.assertIn("Parent approval does not approve this child plan", text)
            self.assertIn("Execute greeting and invalid-input regression checks", text)
        value["contract"]["body"]["scope_exclusions"].append("Other")
        self.assertEqual(body, self.state["goal_contract"]["body"])

    def test_generated_integration_id_does_not_collide_with_an_approved_milestone(self):
        body = goal_fixtures.body()
        body["milestones"][0]["id"] = "integration"
        goals.install_draft(self.state, body, origin="test")
        goals.present(self.state)
        goals.approve(self.state, goals.token(self.state["goal_contract"]))
        value = program.derive_manifest(self.state)
        self.assertEqual(["integration", "integration-final"], [r["id"] for r in value["workstreams"]])
        self.assertEqual(["integration"], value["workstreams"][-1]["depends_on"])


class ExecutionTests(unittest.TestCase):
    """Real worktrees and merges; the child autocode process is scripted."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.root / "project"; self.project.mkdir()
        subprocess.run(["git", "init", "-q", "-b", "main", str(self.project)], check=True)
        (self.project / "README.md").write_text("# demo\n")
        git(self.project, "add", "-A")
        git(self.project, "-c", "user.name=F", "-c", "user.email=f@example.test", "commit", "-qm", "base")
        self.head = git(self.project, "rev-parse", "HEAD")
        self.launches = []
        self.child_outcome = {}  # workstream id -> run status to leave behind
        self.child_extra_files = {}  # workstream id -> {relpath: text}

    def write_manifest(self, value):
        path = self.root / "program.json"
        path.write_text(json.dumps(value))
        return path

    def fake_run(self, command, **kwargs):
        if command[0] == "git":
            return REAL_RUN(command, **kwargs)
        workspace = Path(command[command.index("--workspace") + 1])
        if "--run-dir" in command:
            run = Path(command[command.index("--run-dir") + 1])
            saved = json.loads((run / "state.json").read_text())
            wid = saved["workstream"]
        else:
            brief = command[2]
            wid = brief.split()[2]
            run = workspace / ".autocode/runs" / f"run-{wid}"
            run.mkdir(parents=True)
        self.launches.append({"id": wid, "workspace": str(workspace), "files": sorted(
            p.relative_to(workspace).as_posix() for p in workspace.rglob("*")
            if p.is_file() and not {".git", ".autocode"} & set(p.relative_to(workspace).parts)),
            "resume": "--run-dir" in command, "in_place": "--in-place" in command})
        outcome = self.child_outcome.get(wid, "TASK_COMPLETE")
        if outcome == "TASK_COMPLETE":
            owned = {"contracts": "contracts/spec.json", "a": "a/service.py", "b": "b/service.py",
                     "integration": "tests/test_flow.py", "deploy": "deploy/compose.yml"}[wid]
            target = workspace / owned; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{wid} result\n")
            for rel, text in self.child_extra_files.get(wid, {}).items():
                (workspace / rel).write_text(text)
        (run / "state.json").write_text(json.dumps({"status": outcome, "workstream": wid, "workspace": str(workspace)}))
        return subprocess.CompletedProcess(command, 0 if outcome == "TASK_COMPLETE" else 2, "", "")

    def run_program(self, path, *extra):
        output = io.StringIO()
        with patch.object(program.subprocess, "run", side_effect=self.fake_run), contextlib.redirect_stdout(output):
            code = program.cli(["run", str(path), "--workspace", str(self.project), "--max-parallel", "2", *extra])
        return code, json.loads(output.getvalue())

    def integration_files(self, result):
        return set(git(self.project, "ls-tree", "-r", "--name-only", result["integration_branch"]).splitlines())

    def test_waves_run_in_dependency_order_and_merge_onto_the_integration_branch(self):
        path = self.write_manifest(manifest())
        code, result = self.run_program(path)
        self.assertEqual(0, code, result)
        self.assertEqual("COMPLETE", result["status"])
        self.assertEqual({"README.md", "contracts/spec.json", "a/service.py", "b/service.py", "tests/test_flow.py"},
                         self.integration_files(result))
        order = [row["id"] for row in self.launches]
        self.assertEqual("contracts", order[0])
        self.assertEqual({"a", "b"}, set(order[1:3]))
        self.assertEqual("integration", order[3])
        by_id = {row["id"]: row for row in self.launches}
        # Dependents start from the merged upstream result, not from project HEAD.
        self.assertIn("contracts/spec.json", by_id["a"]["files"])
        self.assertNotIn("b/service.py", by_id["a"]["files"])
        self.assertEqual({"README.md", "a/service.py", "b/service.py", "contracts/spec.json"}, set(by_id["integration"]["files"]))
        self.assertEqual(result["integration_workspace"], by_id["integration"]["workspace"])
        self.assertTrue(all(row["in_place"] for row in self.launches))
        self.assertNotEqual(by_id["a"]["workspace"], by_id["b"]["workspace"])
        # The project's own branch is untouched; the human merges the integration branch.
        self.assertEqual(self.head, git(self.project, "rev-parse", "HEAD"))
        self.assertEqual("main", git(self.project, "rev-parse", "--abbrev-ref", "HEAD"))
        merges = git(self.project, "log", "--merges", "--format=%s", result["integration_branch"]).splitlines()
        self.assertEqual(3, len(merges))
        self.assertIn("Review", result["next"])

    def test_waiting_child_pauses_the_program_and_resumes_the_same_run(self):
        path = self.write_manifest(manifest())
        self.child_outcome["contracts"] = "AWAITING_GOAL_APPROVAL"
        code, result = self.run_program(path)
        self.assertEqual(2, code)
        self.assertEqual("WAITING", result["status"])
        record = next(row for row in result["workstreams"] if row["id"] == "contracts")
        self.assertEqual(("WAITING", "AWAITING_GOAL_APPROVAL"), (record["status"], record["run_status"]))
        self.assertEqual(1, len(self.launches))
        worktrees = git(self.project, "worktree", "list").splitlines()
        self.assertEqual(3, len(worktrees))  # main checkout, integration, contracts
        # Rerunning while the gate is still open launches nothing and touches no run.
        code, again = self.run_program(path)
        self.assertEqual((2, "WAITING", 1), (code, again["status"], len(self.launches)))
        # The human approves in the run dir (status returns to RUNNING); the program then
        # resumes that same run instead of starting another one or another worktree.
        run_state = Path(record["run_dir"]) / "state.json"
        run_state.write_text(json.dumps({**json.loads(run_state.read_text()), "status": "RUNNING"}))
        self.child_outcome["contracts"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual(0, code, result)
        self.assertEqual("COMPLETE", result["status"])
        self.assertTrue(self.launches[1]["resume"])
        self.assertEqual(self.launches[0]["workspace"], self.launches[1]["workspace"])
        self.assertEqual(5, len(git(self.project, "worktree", "list").splitlines()))  # main, integration, contracts, a, b

    def test_merge_conflict_pauses_without_losing_either_branch_and_manual_resolution_resumes(self):
        path = self.write_manifest(manifest())
        # An external commit changes a's owned path after its worktree was branched.
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        _, waiting = self.run_program(path)
        a = next(row for row in waiting["workstreams"] if row["id"] == "a")
        integration = Path(waiting["integration_workspace"])
        (integration / "a").mkdir()
        (integration / "a/service.py").write_text("external repair\n")
        git(integration, "add", "a/service.py")
        git(integration, *program.GIT_IDENTITY, "commit", "-qm", "External repair")
        run_state = Path(a["run_dir"]) / "state.json"
        run_state.write_text(json.dumps({**json.loads(run_state.read_text()), "status": "RUNNING"}))
        self.child_outcome["a"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual(2, code)
        self.assertEqual("PAUSED_MERGE_CONFLICT", result["status"])
        rows = {row["id"]: row for row in result["workstreams"]}
        statuses = sorted((rows["a"]["status"], rows["b"]["status"]))
        self.assertEqual(["CONFLICT", "MERGED"], statuses)
        conflicted = "a" if rows["a"]["status"] == "CONFLICT" else "b"
        integration = Path(result["integration_workspace"])
        self.assertEqual("", git(integration, "status", "--porcelain", "--untracked-files=no"))
        self.assertFalse((integration / ".git" / "MERGE_HEAD").exists())
        self.assertIn("Resolve", result["next"])
        self.assertEqual(f"{conflicted} result\n", (Path(rows[conflicted]["workspace"]) / conflicted / "service.py").read_text())
        # A human resolves it on the integration worktree and commits.
        merge = subprocess.run(["git", "-C", str(integration), "-c", "user.name=H", "-c", "user.email=h@example.test",
                                "merge", "--no-ff", "-X", "theirs", "--no-edit", rows[conflicted]["branch"]],
                               capture_output=True, text=True)
        self.assertEqual(0, merge.returncode, merge.stdout + merge.stderr)
        code, result = self.run_program(path)
        self.assertEqual(0, code, result)
        self.assertEqual("COMPLETE", result["status"])
        rows = {row["id"]: row for row in result["workstreams"]}
        self.assertEqual("conflict resolved manually", rows[conflicted]["merge_note"])
        self.assertIn("tests/test_flow.py", self.integration_files(result))

    def test_integration_commits_its_own_tracked_repairs(self):
        path = self.write_manifest(manifest())
        self.child_extra_files["integration"] = {"README.md": "# Repaired integration documentation\n"}
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]))
        self.assertEqual("# Repaired integration documentation", git(self.project, "show", result["integration_branch"] + ":README.md"))

    def test_unowned_edits_pause_before_commit_or_merge(self):
        path = self.write_manifest(manifest())
        self.child_extra_files["contracts"] = {"README.md": "out of scope\n", "unexpected.txt": "extra\n"}
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_OWNERSHIP"), (code, result["status"]))
        self.assertIn("README.md", result["next"])
        self.assertIn("unexpected.txt", result["next"])
        self.assertEqual({"README.md"}, self.integration_files(result))
        record = result["workstreams"][0]
        self.assertEqual(self.head, git(Path(record["workspace"]), "rev-parse", "HEAD"))
        # Correct the delivery without discarding the valid owned changes.
        workspace = Path(record["workspace"])
        (workspace / "README.md").write_text("# demo\n")
        (workspace / "unexpected.txt").unlink()
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]))

    def test_ownership_checks_committed_changes_and_rename_source(self):
        path = self.write_manifest(manifest())
        self.child_outcome["contracts"] = "AWAITING_GOAL_APPROVAL"
        _, result = self.run_program(path)
        record = result["workstreams"][0]
        workspace = Path(record["workspace"])
        (workspace / "contracts").mkdir()
        git(workspace, "mv", "README.md", "contracts/README.md")
        git(workspace, *program.GIT_IDENTITY, "commit", "-qm", "Rename foreign file")
        run_state = Path(record["run_dir"]) / "state.json"
        run_state.write_text(json.dumps({"status": "TASK_COMPLETE"}))
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_OWNERSHIP"), (code, result["status"]))
        self.assertIn("README.md", result["next"])
        self.assertEqual({"README.md"}, self.integration_files(result))

    def test_staged_runner_metadata_is_not_committed(self):
        (self.project / ".autocode").mkdir()
        (self.project / ".autocode/state.json").write_text("{}")
        git(self.project, "add", ".autocode/state.json")
        with self.assertRaisesRegex(program.support.Paused, "metadata is staged"):
            program._commit_all(self.project, "Do not commit metadata")
        self.assertEqual(self.head, git(self.project, "rev-parse", "HEAD"))

    def test_committed_metadata_cannot_hide_behind_unstaged_deletion(self):
        path = self.write_manifest(manifest())
        self.child_outcome["contracts"] = "AWAITING_GOAL_APPROVAL"
        _, result = self.run_program(path)
        record = result["workstreams"][0]
        workspace = Path(record["workspace"])
        metadata = workspace / ".autocode/note.txt"
        metadata.write_text("must not merge\n")
        git(workspace, "add", "-f", ".autocode/note.txt")
        git(workspace, *program.GIT_IDENTITY, "commit", "-qm", "Accidental metadata")
        metadata.unlink()
        (Path(record["run_dir"]) / "state.json").write_text(json.dumps({"status": "TASK_COMPLETE"}))
        code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_OWNERSHIP"), (code, result["status"]))
        self.assertNotIn(".autocode/note.txt", self.integration_files(result))

    def test_integration_cannot_complete_with_committed_runner_metadata(self):
        path = self.write_manifest(manifest())

        def commits_metadata(command, **kwargs):
            result = self.fake_run(command, **kwargs)
            if command[0] != "git" and "PROGRAM WORKSTREAM integration " in command[2]:
                workspace = Path(command[command.index("--workspace") + 1])
                git(workspace, "add", "-f", ".autocode/task-workspace.json")
                git(workspace, *program.GIT_IDENTITY, "commit", "-qm", "Accidental metadata")
            return result

        output = io.StringIO()
        with patch.object(program.subprocess, "run", side_effect=commits_metadata), contextlib.redirect_stdout(output):
            code = program.cli(["run", str(path), "--workspace", str(self.project)])
        result = json.loads(output.getvalue())
        self.assertEqual((2, "PAUSED_METADATA"), (code, result["status"]))
        self.assertEqual("COMPLETE", result["workstreams"][-1]["status"])

    def test_reopened_child_loses_cached_completion_before_merging(self):
        path = self.write_manifest(manifest())
        pause = program.support.Paused("PAUSED_METADATA", "Inspect staged files")
        with patch.object(program, "_commit_all", side_effect=pause):
            _, result = self.run_program(path)
        record = result["workstreams"][0]
        self.assertEqual("COMPLETE", record["status"])
        run_state = Path(record["run_dir"]) / "state.json"
        run_state.write_text(json.dumps({**json.loads(run_state.read_text()), "status": "RUNNING"}))
        self.child_outcome["contracts"] = "RUNNING"
        code, result = self.run_program(path)
        self.assertEqual((2, "WAITING"), (code, result["status"]))
        self.assertEqual(2, len(self.launches))
        self.assertTrue(self.launches[-1]["resume"])
        self.assertEqual({"README.md"}, self.integration_files(result))

    def test_deployment_waits_for_explicit_authorization(self):
        path = self.write_manifest(manifest(deploy=True))
        code, result = self.run_program(path)
        self.assertEqual(2, code)
        self.assertEqual("AUTHORIZATION_REQUIRED", result["status"])
        deploy = next(row for row in result["workstreams"] if row["id"] == "deploy")
        self.assertEqual("PENDING", deploy["status"])
        self.assertIn("--authorize-deployment", deploy["blocked_reason"])
        self.assertNotIn("deploy", [row["id"] for row in self.launches])
        code, result = self.run_program(path, "--authorize-deployment")
        self.assertEqual(0, code, result)
        self.assertEqual("COMPLETE", result["status"])
        self.assertIn("deploy/compose.yml", self.integration_files(result))
        self.assertEqual("deploy", self.launches[-1]["id"])

    def test_a_child_that_returns_without_progress_is_invoked_once_per_pass(self):
        path = self.write_manifest(manifest())
        # e.g. a second writer refused by the child's own workspace lock: exit 2, state still RUNNING.
        self.child_outcome["contracts"] = "RUNNING"
        code, result = self.run_program(path)
        self.assertEqual((2, "WAITING"), (code, result["status"]))
        self.assertEqual(1, len(self.launches))
        code, result = self.run_program(path)
        self.assertEqual((2, "WAITING"), (code, result["status"]))
        self.assertEqual(2, len(self.launches))
        self.assertTrue(self.launches[1]["resume"])

    def test_failed_child_blocks_the_program(self):
        path = self.write_manifest(manifest())
        with patch.object(program.subprocess, "run", side_effect=lambda command, **kw: REAL_RUN(command, **kw)
                          if command[0] == "git" else subprocess.CompletedProcess(command, 1, "", "boom")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = program.cli(["run", str(path), "--workspace", str(self.project)])
        result = json.loads(output.getvalue())
        self.assertEqual((2, "BLOCKED"), (code, result["status"]))
        self.assertEqual("FAILED", result["workstreams"][0]["status"])
        self.assertTrue((self.project / ".autocode/programs").exists())
        failed_workspace = result["workstreams"][0]["workspace"]
        code, again = self.run_program(path)
        self.assertEqual((2, "BLOCKED", []), (code, again["status"], self.launches))
        code, retried = self.run_program(path, "--retry-workstream", "contracts")
        self.assertEqual((0, "COMPLETE"), (code, retried["status"]))
        self.assertEqual(failed_workspace, self.launches[0]["workspace"])
        self.assertEqual(5, len(git(self.project, "worktree", "list").splitlines()))

    def test_failed_invocation_with_checkpoint_retries_the_same_run(self):
        path = self.write_manifest(manifest())
        self.child_outcome["contracts"] = "RUNNING"

        def fail_after_checkpoint(command, **kw):
            result = self.fake_run(command, **kw)
            if command[0] != "git":
                result.returncode = 1
            return result

        output = io.StringIO()
        with patch.object(program.subprocess, "run", side_effect=fail_after_checkpoint), contextlib.redirect_stdout(output):
            code = program.cli(["run", str(path), "--workspace", str(self.project)])
        self.assertEqual("BLOCKED", json.loads(output.getvalue())["status"])
        self.child_outcome["contracts"] = "TASK_COMPLETE"
        code, result = self.run_program(path, "--retry-workstream", "contracts")
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertTrue(self.launches[1]["resume"])
        self.assertEqual(self.launches[0]["workspace"], self.launches[1]["workspace"])

    def test_retry_never_bypasses_a_child_gate(self):
        path = self.write_manifest(manifest())
        self.child_outcome["contracts"] = "AWAITING_GOAL_APPROVAL"
        _, result = self.run_program(path)
        state_path = Path(result["state_file"])
        state = json.loads(state_path.read_text())
        state["workstreams"]["contracts"]["status"] = "FAILED"
        state_path.write_text(json.dumps(state))
        code, result = self.run_program(path, "--retry-workstream", "contracts")
        self.assertEqual((2, "WAITING", 1), (code, result["status"], len(self.launches)))

    def test_missing_child_checkpoint_is_not_silently_replaced(self):
        path = self.write_manifest(manifest())
        self.child_outcome["contracts"] = "RUNNING"
        _, result = self.run_program(path)
        run = Path(result["workstreams"][0]["run_dir"])
        (run / "state.json").unlink()
        code, result = self.run_program(path)
        self.assertEqual((2, "BLOCKED", 1), (code, result["status"], len(self.launches)))
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(io.StringIO()):
            self.run_program(path, "--retry-workstream", "contracts")
        self.assertEqual(1, len(self.launches))

    def test_interrupted_controller_discovers_and_resumes_existing_child(self):
        path = self.write_manifest(manifest())
        self.child_outcome["contracts"] = "RUNNING"
        checkpoint = {}

        def interrupted(command, **kw):
            if command[0] != "git":
                state_file = next((self.project / ".autocode/programs").glob("*/state.json"))
                checkpoint.update(json.loads(state_file.read_text()))
                record = checkpoint["workstreams"]["contracts"]
                self.assertEqual("RUNNING", record["status"])
                self.assertIn("workspace", record)
                self.assertIn("runs_before", record)
                self.assertNotIn("run_dir", record)
            return self.fake_run(command, **kw)

        output = io.StringIO()
        with patch.object(program.subprocess, "run", side_effect=interrupted), contextlib.redirect_stdout(output):
            program.cli(["run", str(path), "--workspace", str(self.project)])
        result = json.loads(output.getvalue())
        # Restore the durable checkpoint as if the controller died while its child ran.
        Path(result["state_file"]).write_text(json.dumps(checkpoint))
        self.child_outcome["contracts"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]))
        self.assertTrue(self.launches[1]["resume"])
        self.assertEqual(self.launches[0]["workspace"], self.launches[1]["workspace"])

    def test_resuming_deployment_still_requires_authorization(self):
        path = self.write_manifest(manifest(deploy=True))
        self.child_outcome["deploy"] = "RUNNING"
        self.run_program(path, "--authorize-deployment")
        before = len(self.launches)
        code, result = self.run_program(path)
        self.assertEqual(before, len(self.launches))
        self.assertEqual((2, "AUTHORIZATION_REQUIRED"), (code, result["status"]))
        self.child_outcome["deploy"] = "TASK_COMPLETE"
        code, result = self.run_program(path, "--authorize-deployment")
        self.assertEqual((0, "COMPLETE"), (code, result["status"]))

    def test_dry_run_and_status_never_create_worktrees(self):
        path = self.write_manifest(manifest())
        for command in (["run", str(path), "--workspace", str(self.project), "--dry-run"],
                        ["status", str(path), "--workspace", str(self.project)]):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                self.assertEqual(0, program.cli(command))
            self.assertEqual("NOT_STARTED", json.loads(output.getvalue())["status"])
        self.assertFalse((self.project / ".autocode").exists())
        self.assertEqual(1, len(git(self.project, "worktree", "list").splitlines()))

    def test_changed_manifest_cannot_reuse_a_checkpoint(self):
        path = self.write_manifest(manifest())
        self.child_outcome["contracts"] = "AWAITING_GOAL_APPROVAL"
        self.run_program(path)
        value = manifest(); value["workstreams"][1]["brief"] = "changed"
        path.write_text(json.dumps(value))
        with self.assertRaises(SystemExit):
            with contextlib.redirect_stderr(io.StringIO()):
                self.run_program(path)


class CliFixtureTest(unittest.TestCase):
    def test_first_wave_starts_real_isolated_runs_and_stops_at_the_human_gate(self):
        flow = test_subprocess.SubprocessFlow(); flow.setUp(); self.addCleanup(flow.doCleanups)
        path = flow.root / "program.json"
        path.write_text(json.dumps(manifest()))
        env = {**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"}
        result = subprocess.run([*flow.entry, "program", "run", str(path), "--workspace", str(flow.project),
                                 "--engine", "codex"], cwd=flow.root, env=env, capture_output=True, text=True, timeout=90)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual("WAITING", value["status"])
        rows = {row["id"]: row for row in value["workstreams"]}
        self.assertEqual("WAITING", rows["contracts"]["status"])
        self.assertIn(rows["contracts"]["run_status"], ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"))
        self.assertEqual("PENDING", rows["a"]["status"])
        saved = json.loads((Path(rows["contracts"]["run_dir"]) / "state.json").read_text())
        self.assertEqual(rows["contracts"]["workspace"], saved["workspace"])
        self.assertTrue(Path(rows["contracts"]["workspace"]).is_relative_to(flow.project / ".autocode/worktrees"))
        self.assertIn("PROGRAM WORKSTREAM contracts", saved["task"])
        branches = [name for name in git(flow.project, "for-each-ref", "--format=%(refname:short)",
                                          "refs/heads/autocode/").splitlines() if "/program-" in name]
        self.assertEqual(2, len(branches), branches)
        status = subprocess.run([*flow.entry, "program", "status", str(path), "--workspace", str(flow.project)],
                                cwd=flow.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual("WAITING", json.loads(status.stdout)["status"])


if __name__ == "__main__":
    unittest.main()
