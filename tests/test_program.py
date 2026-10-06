"""Program runner: manifest rules, derivation from an approved plan, waves, merges, gates.

Execution tests use real Git worktrees and a scripted stand-in for the child
``autocode`` process, which answers ``--status`` with the real status view of
the state it saved; git commands pass through to the real binary. No provider
is launched. The final test drives the real CLI with the fake Codex fixture up
to the first human gate.
"""
from __future__ import annotations

import concurrent.futures
import contextlib
import copy
import io
import json
import os
import re
import subprocess
import sys
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

import autocode_brief_literals as brief_literals  # noqa: E402
import autocode_goals as goals  # noqa: E402
import autocode_goal_lifecycle as lifecycle
import autocode_program as program  # noqa: E402
import autocode_run_view as run_view  # noqa: E402
import autocode_taskrun as taskrun  # noqa: E402
import goal_fixtures  # noqa: E402
import task_scenarios  # noqa: E402
from . import test_subprocess  # noqa: E402

REAL_RUN = subprocess.run


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], capture_output=True, text=True, check=True).stdout.strip()


def manifest(*, deploy=False):
    rows = [
        {"id": "contracts", "kind": "code", "skeleton": True, "brief": "Write the shared contracts", "owns": ["contracts/"],
         "depends_on": []},
        {"id": "a", "kind": "code", "brief": "Build service a", "owns": ["a/"], "depends_on": ["contracts"]},
        {"id": "b", "kind": "code", "brief": "Build service b", "owns": ["b/"], "depends_on": ["contracts"]},
        {"id": "integration", "kind": "integration", "brief": "Validate the whole flow", "owns": ["tests/"],
         "depends_on": ["a", "b"]},
    ]
    if deploy:
        rows.append({"id": "deploy", "kind": "deployment", "brief": "Write deployment descriptors", "owns": ["deploy/"],
                     "depends_on": ["integration"]})
    return {"version": 1, "name": "Demo program", "brief": "A two-service demo",
            "journeys": [{"id": "J1", "name": "Order through both services", "steps": ["call a", "call b"]}],
            "shared": {"constraints": ["stdlib only"], "interfaces": [{"id": "contracts", "summary": "JSON shapes", "paths": ["contracts/"]}]},
            "workstreams": rows}


def with_requirements(value):
    """The demo program with requirements: a inherits C2, b inherits C3, the skeleton C1."""
    value["requirements"] = [{"id": "C1", "criterion": "Contracts exist"}, {"id": "C2", "criterion": "a answers"},
                             {"id": "C3", "criterion": "b answers"}]
    for row, cid in zip(value["workstreams"], ("C1", "C2", "C3")):
        row["acceptance_criteria"] = [cid]
    value["shared"]["interfaces"][0].update(producer="contracts", consumers=["a", "b"])
    return value


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
        with self.assertRaisesRegex(ValueError, "exactly one integration"):
            program.validate_manifest(value)
        value = manifest(); value["workstreams"].pop()
        with self.assertRaisesRegex(ValueError, "exactly one integration"):
            program.validate_manifest(value)

    def test_every_part_extends_the_walking_skeleton_unless_exempt_with_a_reason(self):
        value = manifest(); value["workstreams"][2]["depends_on"] = []
        with self.assertRaisesRegex(ValueError, "must \\(transitively\\) depend on the walking skeleton contracts"):
            program.validate_manifest(value)
        value = manifest(); value["workstreams"][2].update(depends_on=[], skeleton_exempt="Lesson text only; nothing runs")
        program.validate_manifest(value)
        value = manifest(); value["workstreams"][0].pop("skeleton")
        with self.assertRaisesRegex(ValueError, "exactly one code workstream skeleton"):
            program.validate_manifest(value)
        value = manifest(); value.pop("journeys")
        with self.assertRaisesRegex(ValueError, "needs journeys"):
            program.validate_manifest(value)

    def test_deployment_placement_rules(self):
        value = manifest(deploy=True)
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
        self.assertIn("Workstreams already merged on the integration branch", text)
        self.assertIn("- contracts: Write the shared contracts", text)
        self.assertIn("only under: a.", text)
        self.assertIn("do not modify): b, contracts, tests.", text)
        self.assertIn("do not merge branches", text)

    def test_malformed_interfaces_and_contract_are_refused_as_manifest_errors(self):
        # The CLI reports only ValueError; a string of paths was iterated character by character.
        for field, bad in (("paths", "contracts"), ("producer", ["contracts"]), ("consumers", [["a"]])):
            value = manifest(); value["shared"]["interfaces"][0][field] = bad
            with self.subTest(field=field), self.assertRaises(ValueError):
                program.validate_manifest(value)
        for bad in ("text", None, [], {"body": "text"}):
            value = manifest(); value["contract"] = bad
            with self.subTest(contract=bad), self.assertRaisesRegex(ValueError, "contract must be an object"):
                program.validate_manifest(value)

    def test_the_skeleton_brief_walks_each_journey_by_its_steps(self):
        value = manifest()
        value["journeys"].append({"id": "J2", "name": "Simulated load", "steps": ["simulate", "count"],
                                  "simulated": True, "does_not_prove": "real traffic"})
        value = program.validate_manifest(value)
        text = program.compose_brief(value, value["workstreams"][0], {"workstreams": {}})
        self.assertIn("- J1 Order through both services: call a -> call b", text)
        self.assertIn("- J2 Simulated load: simulate -> count", text)
        self.assertIn("does not prove: real traffic", text)

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
                                   "limits": {"iteration_ceiling": 5, "max_seconds": None, "no_progress_batches": 3}}}
        lifecycle.migrate(self.state)

    def two_milestones(self):
        body = goal_fixtures.body()
        body["acceptance_criteria"].append({"id": "C2", "criterion": "Goodbye CLI prints Goodbye, NAME",
                                            "verification_method": "Run bye.py", "human_review": False})
        body["milestones"].append({"id": "M2", "objective": "Deliver goodbye CLI", "acceptance_criteria": ["C2"],
                                   "depends_on": ["M1"], "affected_paths": ["bye.py"]})
        return body

    def test_unapproved_plan_cannot_become_a_program(self):
        lifecycle.install_draft(self.state, self.two_milestones(), origin="test")
        with self.assertRaisesRegex(ValueError, "approved plan"):
            program.derive_manifest(run_view.approved_contract(self.state))

    def test_approved_milestones_become_workstreams_plus_integration(self):
        lifecycle.install_draft(self.state, self.two_milestones(), origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        value = program.derive_manifest(run_view.approved_contract(self.state), source_run=self.root / "run")
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
        # The first milestone without dependencies is the walking skeleton; the approved flow is the main journey.
        self.assertTrue(by_id["M1"]["skeleton"])
        self.assertEqual([{"id": "J1", "name": "Main user journey", "steps": goal_fixtures.body()["end_to_end_flow"]}],
                         value["journeys"])

    def test_every_child_receives_the_complete_approved_contract(self):
        body = goal_fixtures.body(human=True)
        lifecycle.install_draft(self.state, body, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        value = program.derive_manifest(run_view.approved_contract(self.state))
        self.assertEqual(body, value["contract"]["body"])
        for row in value["workstreams"]:
            text = program.compose_brief(value, row, {"workstreams": {}})
            self.assertIn("```json\n" + json.dumps(body, indent=2, ensure_ascii=False) + "\n```", text)
            self.assertIn('"human_review": true', text)
            self.assertIn("human_review: true", text)  # the workstream's own criterion line
            self.assertIn("CLI regression tests", text)
            self.assertIn("Approving the parent approves neither this child plan", text)
            self.assertIn("Execute greeting and invalid-input regression checks", text)
        value["contract"]["body"]["scope_exclusions"].append("Other")
        self.assertEqual(body, self.state["goal_contract"]["body"])

    def test_a_child_keeps_only_its_own_criteria_literals_and_as_the_user_wrote_them(self):
        # A live run's skeleton could never plan: the parent contract, pasted as escaped JSON, made the
        # brief-literal rule demand `say \\"hi\\" \\u00e9` back verbatim, and every other workstream's literals too.
        body = goal_fixtures.body()
        body["milestones"] = [
            {"id": "M1", "objective": "Add notes", "acceptance_criteria": ["C1"], "depends_on": [], "affected_paths": ["a.py"]},
            {"id": "M2", "objective": "Export notes", "acceptance_criteria": ["C2"], "depends_on": ["M1"],
             "affected_paths": ["b.py"]}]
        body["acceptance_criteria"] = [
            {"id": "C1", "criterion": 'Adding `say "hi" é` then listing prints `1 say "hi" é`',
             "verification_method": "python3 -m unittest", "human_review": False},
            {"id": "C2", "criterion": "Export prints `[{\"id\": 1}]`", "verification_method": "python3 -m unittest",
             "human_review": False}]
        lifecycle.install_draft(self.state, body, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        value = program.derive_manifest(run_view.approved_contract(self.state))
        rows = {row["id"]: row for row in value["workstreams"]}
        found = brief_literals.literals([program.compose_brief(value, rows["M1"], {"workstreams": {}})])
        self.assertEqual(['say "hi" é', '1 say "hi" é'], found)
        found = brief_literals.literals([program.compose_brief(value, rows["M2"], {"workstreams": {}})])
        self.assertEqual(['[{"id": 1}]'], found)

    def test_generated_integration_id_does_not_collide_with_an_approved_milestone(self):
        body = goal_fixtures.body()
        body["milestones"][0]["id"] = "integration"
        lifecycle.install_draft(self.state, body, origin="test")
        lifecycle.human.evaluate(self.state)
        lifecycle.present(self.state)
        lifecycle.approve(self.state, goals.token(self.state["goal_contract"]))
        value = program.derive_manifest(run_view.approved_contract(self.state))
        self.assertEqual(["integration", "integration-final"], [r["id"] for r in value["workstreams"]])
        self.assertEqual(["integration"], value["workstreams"][-1]["depends_on"])


class ProgramHarness(unittest.TestCase):
    """Real worktrees and merges; the child autocode process is scripted, including its status view."""

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
        self.child_view = {}  # workstream id -> status-view fields the child shows (approved_contract, displayed_plan)
        self.child_checks = {}  # workstream id -> the checks its validation re-ran
        self.journey_status = {}  # journey id -> the integration run's recorded status
        self.feedback = []  # (workstream id, text) sent to child runs
        self.status_override = {}  # workstream id -> status `--status` reports instead of the saved one

    def write_manifest(self, value):
        path = self.root / "program.json"
        path.write_text(json.dumps(value))
        return path

    @staticmethod
    def is_launch(command):
        return command[0] != "git" and "--status" not in command and "--feedback" not in command

    def owned(self, wid):
        return {"contracts": "contracts/spec.json", "a": "a/service.py", "b": "b/service.py",
                "integration": "tests/test_flow.py", "deploy": "deploy/compose.yml"}.get(wid, f"{wid}/result.txt")

    def fake_status(self, command):
        """`autocode --status`: the status view of the saved run as the real CLI prints it, plus the
        fields the scripted child shows (approved_contract, displayed_plan, evidence)."""
        state = Path(command[command.index("--run-dir") + 1]) / "state.json"
        if not state.is_file():
            return subprocess.CompletedProcess(command, 1, "", "autocode: no saved run\n")
        saved = json.loads(state.read_text())
        if saved.get("workstream") in self.status_override:
            saved["status"] = self.status_override[saved["workstream"]]
        view = run_view.view({key: value for key, value in saved.items() if key != "view"})
        view.update(saved.get("view", {}))
        return subprocess.CompletedProcess(command, 0, json.dumps({"view": view}), "")

    def fake_feedback(self, command):
        """`autocode --feedback`: saved for the run's next relaunch, which plans again."""
        run = Path(command[command.index("--run-dir") + 1])
        saved = json.loads((run / "state.json").read_text())
        self.feedback.append((saved["workstream"], command[command.index("--feedback") + 1]))
        saved["status"] = "RUNNING"
        saved.get("view", {}).pop("displayed_plan", None)
        (run / "state.json").write_text(json.dumps(saved))
        return subprocess.CompletedProcess(command, 0, "Saved; no agent launched by this action\n", "")

    def fake_run(self, command, **kwargs):
        if command[0] == "git":
            return REAL_RUN(command, **kwargs)
        if kwargs.get("cwd") is not None and not Path(kwargs["cwd"]).is_dir():
            raise FileNotFoundError(2, "No such file or directory", str(kwargs["cwd"]))  # as subprocess.run does
        if "--status" in command:
            return self.fake_status(command)
        if "--feedback" in command:
            return self.fake_feedback(command)
        workspace = Path(command[command.index("--workspace") + 1])
        brief = None
        if "--run-dir" in command:
            run = Path(command[command.index("--run-dir") + 1])
            saved = json.loads((run / "state.json").read_text())
            wid = saved["workstream"]
        else:
            brief = command[2]
            wid = re.search(r"PROGRAM WORKSTREAM (\S+)", brief).group(1)
            run = workspace / ".autocode/runs" / f"run-{wid}-{len(self.launches) + 1}"
            run.mkdir(parents=True)
        self.launches.append({"id": wid, "workspace": str(workspace), "brief": brief, "run_dir": str(run), "files": sorted(
            p.relative_to(workspace).as_posix() for p in workspace.rglob("*")
            if p.is_file() and not {".git", ".autocode"} & set(p.relative_to(workspace).parts)),
            "resume": "--run-dir" in command, "in_place": "--in-place" in command,
            "workflow": command[command.index("--workflow") + 1] if "--workflow" in command else None})
        outcome = self.child_outcome.get(wid, "TASK_COMPLETE")
        view = copy.deepcopy(self.child_view.get(wid, {}))
        if outcome == "TASK_COMPLETE":
            owned = self.owned(wid)
            target = workspace / owned; target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(f"{wid} result\n")
            for rel, text in self.child_extra_files.get(wid, {}).items():
                (workspace / rel).parent.mkdir(parents=True, exist_ok=True)
                (workspace / rel).write_text(text)
            checks = self.child_checks.get(wid, [f"test -f {owned}"])
            view.setdefault("evidence", {
                "acceptance": [{"id": jid, "status": status} for jid, status in
                               ({"J1": "verified", **self.journey_status}.items() if wid == "integration" else ())],
                "check_replay": {"verdict": "PASS", "checks": [{"command": c, "exit_code": 0} for c in checks]}})
        (run / "state.json").write_text(json.dumps({"status": outcome, "workstream": wid, "workspace": str(workspace),
                                                    "view": view}))
        return subprocess.CompletedProcess(command, 0 if outcome == "TASK_COMPLETE" else 2, "", "")

    def approve(self, path):
        """Approve the displayed program agreement, as the person would; return what was pending."""
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            program.cli(["run", str(path), "--workspace", str(self.project), "--dry-run"])
        pending = json.loads(output.getvalue())["agreement"]["pending"]
        if pending:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(0, program.cli(["approve", str(path), "--workspace", str(self.project),
                                                 "--token", pending["token"]]))
        return pending

    @contextlib.contextmanager
    def scripted_invocations(self, side_effect=None):
        """Script advancing and status calls together; callbacks keep Git real."""
        callback = self.fake_run if side_effect is None else side_effect
        with patch.object(program.subprocess, "run", side_effect=callback), \
                patch.object(taskrun, "run_captured", side_effect=callback):
            yield

    def run_program(self, path, *extra, approve=True):
        if approve:
            self.approve(path)
        output = io.StringIO()
        with self.scripted_invocations(), contextlib.redirect_stdout(output):
            code = program.cli(["run", str(path), "--workspace", str(self.project), "--max-parallel", "2", *extra])
        return code, json.loads(output.getvalue())

    def child_state(self, record):
        return Path(record["run_dir"]) / "state.json"

    def set_child(self, record, **fields):
        path = self.child_state(record)
        path.write_text(json.dumps({**json.loads(path.read_text()), **fields}))

    def integration_files(self, result):
        return set(git(self.project, "ls-tree", "-r", "--name-only", result["integration_branch"]).splitlines())


class ExecutionTests(ProgramHarness):
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

    def test_a_dirty_integration_worktree_holds_the_final_check_and_names_the_worktree(self):
        path = self.write_manifest(manifest())
        real_ready = program.ready

        def leftovers_before_the_final_check(value, state, **kwargs):
            rows, blocked = real_ready(value, state, **kwargs)
            if any(row["kind"] == "integration" for row, _ in rows):  # e.g. left by a retired integration run
                (Path(state["integration"]["workspace"]) / "README.md").write_text("leftover\n")
            return rows, blocked

        with patch.object(program, "ready", side_effect=leftovers_before_the_final_check):
            code, result = self.run_program(path)
        self.assertEqual((2, "PAUSED_INTEGRATION_DIRTY"), (code, result["status"]))
        self.assertIn(f"commit or discard (git restore) the uncommitted changes in {result['integration_workspace']}",
                      result["next"])
        self.assertIn("retired integration run", result["next"])
        self.assertNotIn("integration", [row["id"] for row in self.launches])

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
        with self.assertRaisesRegex(program.util.Paused, "metadata is staged"):
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
        self.approve(path)

        def commits_metadata(command, **kwargs):
            result = self.fake_run(command, **kwargs)
            if command[0] != "git" and "PROGRAM WORKSTREAM integration " in command[2]:
                workspace = Path(command[command.index("--workspace") + 1])
                git(workspace, "add", "-f", ".autocode/task-workspace.json")
                git(workspace, *program.GIT_IDENTITY, "commit", "-qm", "Accidental metadata")
            return result

        output = io.StringIO()
        with self.scripted_invocations(commits_metadata), contextlib.redirect_stdout(output):
            code = program.cli(["run", str(path), "--workspace", str(self.project)])
        result = json.loads(output.getvalue())
        self.assertEqual((2, "PAUSED_METADATA"), (code, result["status"]))
        self.assertEqual("COMPLETE", result["workstreams"][-1]["status"])

    def test_reopened_child_loses_cached_completion_before_merging(self):
        path = self.write_manifest(manifest())
        pause = program.util.Paused("PAUSED_METADATA", "Inspect staged files")
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

    def test_parallel_workstreams_never_add_worktrees_concurrently(self):
        # a and b launch in parallel threads; concurrent `git worktree add` on one repository
        # collided on macOS CI and blocked the program. Slow each add down so an overlap
        # would be seen, and require that none happens.
        active, overlaps, real_git = [0], [], program.workspaces.git

        def slow_git(cwd, *args, **kwargs):
            if args[:2] != ("worktree", "add"):
                return real_git(cwd, *args, **kwargs)
            with program.STATE_LOCK:
                active[0] += 1
                overlaps.append(active[0])
            try:
                time.sleep(0.2)
                return real_git(cwd, *args, **kwargs)
            finally:
                with program.STATE_LOCK:
                    active[0] -= 1

        path = self.write_manifest(manifest())
        with patch.object(program.workspaces, "git", side_effect=slow_git):
            code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        self.assertEqual({"a", "b", "integration"}, {row["id"] for row in self.launches} - {"contracts"})
        self.assertEqual(1, max(overlaps), f"concurrent worktree adds: {overlaps}")

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

    def test_bytecode_caches_are_neither_foreign_paths_nor_delivered(self):
        # A workstream's tests leave __pycache__ beside files it does not own; that is not a change to them.
        path = self.write_manifest(manifest())
        self.child_extra_files["a"] = {"tests/__pycache__/test_a.cpython-311.pyc": "cache", "a/stale.pyc": "cache"}
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        files = self.integration_files(result)
        self.assertIn("a/service.py", files)
        self.assertFalse([name for name in files if "__pycache__" in name or name.endswith(".pyc")], files)

    def test_failed_child_blocks_the_program(self):
        path = self.write_manifest(manifest())
        self.approve(path)
        with self.scripted_invocations(lambda command, **kw: REAL_RUN(command, **kw)
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
        self.approve(path)
        self.child_outcome["contracts"] = "RUNNING"

        def fail_after_checkpoint(command, **kw):
            result = self.fake_run(command, **kw)
            if self.is_launch(command):
                result.returncode = 1
            return result

        output = io.StringIO()
        with self.scripted_invocations(fail_after_checkpoint), contextlib.redirect_stdout(output):
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
        self.approve(path)
        self.child_outcome["contracts"] = "RUNNING"
        checkpoint = {}

        def interrupted(command, **kw):
            if self.is_launch(command) and not checkpoint:
                state_file = next((self.project / ".autocode/programs").glob("*/state.json"))
                checkpoint.update(json.loads(state_file.read_text()))
                record = checkpoint["workstreams"]["contracts"]
                self.assertEqual("RUNNING", record["status"])
                self.assertIn("workspace", record)
                self.assertEqual([], record["runs_before"])  # the worktree's runs before the start
                self.assertNotIn("run_dir", record)
            return self.fake_run(command, **kw)

        output = io.StringIO()
        with self.scripted_invocations(interrupted), contextlib.redirect_stdout(output):
            program.cli(["run", str(path), "--workspace", str(self.project)])
        result = json.loads(output.getvalue())
        # Restore the durable checkpoint as if the controller died while its child ran.
        Path(result["state_file"]).write_text(json.dumps(checkpoint))
        self.child_outcome["contracts"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]))
        self.assertTrue(self.launches[1]["resume"])
        self.assertEqual(self.launches[0]["workspace"], self.launches[1]["workspace"])

    def test_a_removed_worktree_blocks_the_program_and_its_retry_is_refused(self):
        path = self.write_manifest(manifest())
        self.child_outcome["contracts"] = "AWAITING_GOAL_APPROVAL"
        _, result = self.run_program(path)
        shutil.rmtree(result["workstreams"][0]["workspace"])  # e.g. removed with `git worktree remove`
        output = io.StringIO()
        with self.scripted_invocations(), contextlib.redirect_stdout(output):
            self.assertEqual(0, program.cli(["status", str(path), "--workspace", str(self.project)]))
        self.assertEqual("BLOCKED", json.loads(output.getvalue())["status"])
        code, result = self.run_program(path)
        record = result["workstreams"][0]
        self.assertEqual((2, "BLOCKED", "FAILED", None), (code, result["status"], record["status"], record["run_status"]))
        self.assertIn("No such file or directory", record["error"])
        self.assertNotIn("needs", record)
        error = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(error):
            self.run_program(path, "--retry-workstream", "contracts")
        self.assertIn("restore it before retrying", error.getvalue())
        self.assertEqual(1, len(self.launches))

    def test_integration_starts_its_own_run_beside_runs_already_in_its_worktree(self):
        path = self.write_manifest(manifest())
        self.child_outcome["a"] = "AWAITING_GOAL_APPROVAL"
        _, result = self.run_program(path)
        # A person's own runs in the integration worktree, which the conflict pause sends them to.
        integration = Path(result["integration_workspace"])
        theirs = []
        for name in ("person-1", "person-2"):
            run = integration / ".autocode/runs" / name
            run.mkdir(parents=True)
            (run / "state.json").write_text(json.dumps({"status": "TASK_COMPLETE", "workstream": "person"}))
            theirs.append(str(run))
        a = next(row for row in result["workstreams"] if row["id"] == "a")
        run_state = Path(a["run_dir"]) / "state.json"
        run_state.write_text(json.dumps({**json.loads(run_state.read_text()), "status": "RUNNING"}))
        self.child_outcome["a"] = "TASK_COMPLETE"
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)
        launch = self.launches[-1]
        self.assertEqual(("integration", False), (launch["id"], launch["resume"]))
        record = next(row for row in result["workstreams"] if row["id"] == "integration")
        self.assertNotIn(record["run_dir"], theirs)
        self.assertIn("tests/test_flow.py", self.integration_files(result))  # its own brief ran
        self.assertEqual("person", json.loads((Path(theirs[0]) / "state.json").read_text())["workstream"])

    def test_a_person_finishing_a_child_while_a_sibling_runs_is_merged_in_the_same_pass(self):
        path = self.write_manifest(manifest())
        self.approve(path)
        self.child_outcome.update(a="AWAITING_GOAL_APPROVAL", b="AWAITING_GOAL_APPROVAL")
        futures = {}

        class Pool(concurrent.futures.ThreadPoolExecutor):
            def submit(self, fn, *args, **kwargs):  # launch(project, program_dir, manifest, workstream, ...)
                futures[args[3]["id"]] = future = super().submit(fn, *args, **kwargs)
                return future

        def run(command, **kwargs):
            if command[0] != "git" and "--status" not in command and "PROGRAM WORKSTREAM b" in str(command[2]):
                # a's launch is over (it read a's view) when a person finishes a by hand; b still runs.
                concurrent.futures.wait([futures["a"]], timeout=60)
                a_run = next(self.project.glob(".autocode/worktrees/*/.autocode/runs/run-a-*"))
                (a_run.parents[2] / "a").mkdir()
                (a_run.parents[2] / "a/service.py").write_text("a by hand\n")
                (a_run / "state.json").write_text(json.dumps({**json.loads((a_run / "state.json").read_text()),
                                                              "status": "TASK_COMPLETE"}))
            return self.fake_run(command, **kwargs)

        output = io.StringIO()
        with patch.object(program, "ThreadPoolExecutor", Pool), \
                self.scripted_invocations(run), contextlib.redirect_stdout(output):
            code = program.cli(["run", str(path), "--workspace", str(self.project), "--max-parallel", "2"])
        result = json.loads(output.getvalue())
        rows = {row["id"]: row for row in result["workstreams"]}
        self.assertEqual((2, "MERGED", "WAITING"), (code, rows["a"]["status"], rows["b"]["status"]))
        self.assertIn("a/service.py", self.integration_files(result))
        self.assertEqual(["contracts", "a", "b"], [row["id"] for row in self.launches])

    def test_program_follows_the_status_view_not_the_checkpoint_file(self):
        path = self.write_manifest(manifest())
        # The child saves TASK_COMPLETE, but its status view says it waits for plan approval.
        self.status_override["contracts"] = "AWAITING_GOAL_APPROVAL"
        code, result = self.run_program(path)
        self.assertEqual((2, "WAITING"), (code, result["status"]))
        record = next(row for row in result["workstreams"] if row["id"] == "contracts")
        self.assertEqual(("WAITING", "AWAITING_GOAL_APPROVAL"), (record["status"], record["run_status"]))
        self.assertEqual("TASK_COMPLETE", json.loads((Path(record["run_dir"]) / "state.json").read_text())["status"])
        self.assertEqual({"README.md"}, self.integration_files(result))
        # Once the view reports completion, the same delivery is merged.
        del self.status_override["contracts"]
        self.child_outcome.update(a="AWAITING_GOAL_APPROVAL", b="AWAITING_GOAL_APPROVAL")
        code, result = self.run_program(path)
        self.assertEqual((2, "WAITING", 3), (code, result["status"], len(self.launches)))
        self.assertIn("contracts/spec.json", self.integration_files(result))

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

    def test_changed_workstream_graph_cannot_reuse_a_checkpoint(self):
        path = self.write_manifest(manifest())
        self.child_outcome["contracts"] = "AWAITING_GOAL_APPROVAL"
        self.run_program(path)
        value = manifest(); value["workstreams"][1]["owns"] = ["a2/"]
        path.write_text(json.dumps(value))
        error = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(error):
            self.run_program(path, approve=False)
        self.assertIn("new program", error.getvalue())

    def test_nothing_starts_until_the_agreement_is_approved(self):
        path = self.write_manifest(manifest())
        code, result = self.run_program(path, approve=False)
        self.assertEqual((2, "WAITING_AGREEMENT_APPROVAL"), (code, result["status"]))
        self.assertEqual([], self.launches)
        self.assertIsNone(result["integration_branch"])
        self.assertEqual(1, len(git(self.project, "worktree", "list").splitlines()))
        token = result["agreement"]["pending"]["token"]
        self.assertRegex(token, r"^a1:[0-9a-f]{64}$")
        shown = io.StringIO()
        with contextlib.redirect_stdout(shown):
            program.cli(["show", str(path), "--workspace", str(self.project)])
        self.assertIn(token, shown.getvalue())
        self.assertIn("J1 Order through both services", shown.getvalue())
        self.assertIn("contracts (walking skeleton", shown.getvalue())
        error = io.StringIO()
        with self.assertRaises(SystemExit), contextlib.redirect_stderr(error):
            program.cli(["approve", str(path), "--workspace", str(self.project), "--token", "a1:" + "0" * 64])
        self.assertIn("exact token", error.getvalue())
        code, result = self.run_program(path)
        self.assertEqual((0, "COMPLETE"), (code, result["status"]), result)


class ScenarioFakeBriefTests(unittest.TestCase):
    """The scenario harness's scripted model reads compose_brief's text (scenarios/ may not import tools/).

    It is run here as the script the harness puts on PATH, on handoffs carrying a real workstream brief, so
    a rewording of the brief that the fake no longer reads fails here, not as a stalled scenario run."""
    FAKE = Path(__file__).resolve().parents[1] / "scenarios" / "harness" / "fake_codex.py"
    FILES = {"contracts/shapes.json": "{}\n", "a/service.py": "A = 1\n", "b/service.py": "B = 1\n",
             "tests/test_journey.py": "# journey\n"}

    def setUp(self):
        temp = tempfile.TemporaryDirectory(); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        reference = self.root / "reference"
        for name, text in self.FILES.items():
            (reference / name).parent.mkdir(parents=True, exist_ok=True)
            (reference / name).write_text(text)
        self.manifest = program.validate_manifest(with_requirements(manifest()))
        state = program.new_state(self.root / "program.json", self.manifest, self.root, "key")
        state["agreement"]["revision"] = 1
        self.briefs = {row["id"]: program.compose_brief(self.manifest, row, state) for row in self.manifest["workstreams"]}
        milestones = [{"id": row["id"], "depends_on": row["depends_on"], "objective": row["brief"], "verify": "true",
                       "paths": [name for name in self.FILES if name.startswith(row["owns"][0] + "/")]}
                      for row in self.manifest["workstreams"] if row["kind"] == "code"]
        self.config = self.root / "fake-config.json"
        self.config.write_text(json.dumps({"title": "Demo", "brief": self.manifest["brief"], "reference": str(reference),
                                           "check": "true", "paths": sorted(self.FILES), "milestones": milestones}))

    def report(self, stage, wid, worktree):
        worktree.mkdir(exist_ok=True)
        data = {"stage": stage, "task": self.briefs[wid], "goal_contract": {"revision": 0, "hash": ""}}
        out = self.root / f"{stage}-{wid}.json"
        result = subprocess.run([sys.executable, str(self.FAKE), "exec", "-o", str(out)],
                                input="PROMPT\nCURRENT HANDOFF DATA\n" + json.dumps(data), capture_output=True,
                                text=True, cwd=worktree, timeout=60,
                                env={**os.environ, "SCENARIO_FAKE_CONFIG": str(self.config), "PYTHONDONTWRITEBYTECODE": "1"})
        self.assertEqual(0, result.returncode, result.stderr)
        return json.loads(out.read_text())

    def test_each_workstream_is_built_planned_alone_under_its_inherited_ids(self):
        for row in self.manifest["workstreams"]:
            wid = row["id"]
            with self.subTest(workstream=wid):
                worktree = self.root / f"worktree-{wid}"
                self.assertEqual("build", self.report("recognize_workflow", wid, worktree)["workflow"])
                plan = self.report("astra_finalize", wid, worktree)["contract"]
                self.assertEqual([], program.agreement.dropped(self.manifest, wid, plan["acceptance_criteria"]))
                self.assertEqual(program.agreement.inherited(self.manifest, wid),
                                 [criterion["id"] for criterion in plan["acceptance_criteria"]])
                if row["kind"] == "code":
                    self.assertEqual([wid], [milestone["id"] for milestone in plan["milestones"]])
                    self.assertEqual([], plan["milestones"][0]["depends_on"])
                    paths = plan["initial_task"]["affected_paths"]
                    self.assertTrue(paths)
                    self.assertTrue(all(any(path == own or path.startswith(own + "/") for own in row["owns"])
                                        for path in paths), paths)
                    self.assertEqual("implement", plan["initial_task"]["kind"])

    def test_a_re_check_whose_files_already_conform_plans_validation_only(self):
        worktree = self.root / "worktree-a"
        (worktree / "a").mkdir(parents=True)
        (worktree / "a" / "service.py").write_text(self.FILES["a/service.py"])
        self.assertEqual("validate", self.report("astra_finalize", "a", worktree)["contract"]["initial_task"]["kind"])


class CliFixtureTest(unittest.TestCase):
    def test_first_wave_starts_real_isolated_runs_and_stops_at_the_human_gate(self):
        flow = test_subprocess.SubprocessFlow(); flow.setUp(); self.addCleanup(flow.doCleanups)
        path = flow.root / "program.json"
        path.write_text(json.dumps(manifest()))
        env = {**flow.env, "AUTOCODE_FIXTURE_MODE": "no-human"}
        preview = subprocess.run([*flow.entry, "program", "run", str(path), "--workspace", str(flow.project), "--dry-run"],
                                 cwd=flow.root, env=env, capture_output=True, text=True, timeout=30)
        token = json.loads(preview.stdout)["agreement"]["pending"]["token"]
        approved = subprocess.run([*flow.entry, "program", "approve", str(path), "--workspace", str(flow.project),
                                   "--token", token], cwd=flow.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, approved.returncode, approved.stdout + approved.stderr)
        result = subprocess.run([*flow.entry, "program", "run", str(path), "--workspace", str(flow.project),
                                 "--engine", "codex"], cwd=flow.root, env=env, capture_output=True, text=True, timeout=90)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual("WAITING", value["status"])
        rows = {row["id"]: row for row in value["workstreams"]}
        self.assertEqual("WAITING", rows["contracts"]["status"])
        self.assertIn(rows["contracts"]["run_status"], ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"))
        self.assertEqual("PENDING", rows["a"]["status"])
        self.assertIn(rows["contracts"]["needs"]["kind"], ("answer", "approve_plan"))
        # The child is read through its public status view, never its checkpoint file.
        child = subprocess.run([*flow.entry, "--workspace", rows["contracts"]["workspace"], "--run-dir",
                                rows["contracts"]["run_dir"], "--status"],
                               cwd=flow.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual(0, child.returncode, child.stdout + child.stderr)
        saved = json.loads(child.stdout)
        self.assertEqual(rows["contracts"]["workspace"], saved["workspace"])
        self.assertEqual(rows["contracts"]["run_status"], saved["view"]["status"])
        self.assertTrue(Path(rows["contracts"]["workspace"]).is_relative_to(flow.project / ".autocode/worktrees"))
        brief = Path(value["state_file"]).parent / "contracts" / "brief.md"
        self.assertIn("PROGRAM WORKSTREAM contracts", brief.read_text())
        branches = [name for name in git(flow.project, "for-each-ref", "--format=%(refname:short)",
                                          "refs/heads/autocode/").splitlines() if "/program-" in name]
        self.assertEqual(2, len(branches), branches)
        status = subprocess.run([*flow.entry, "program", "status", str(path), "--workspace", str(flow.project)],
                                cwd=flow.root, env=env, capture_output=True, text=True, timeout=30)
        self.assertEqual("WAITING", json.loads(status.stdout)["status"])


if __name__ == "__main__":
    unittest.main()
