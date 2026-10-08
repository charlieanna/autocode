"""tools/autocode_multicomponent.py: batching/ownership logic, and one end-to-end
build+integrate through the real CLI with a scripted, per-component fake model."""
import json
import copy
import os
import runpy
import shlex
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

import autocode_multicomponent as mc
import autocode_goals as goals
import autocode_util as util
from autocode_taskrun import TaskRun, TaskRunError
from .test_verify import isolated_python_env

REPO_ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parents[1] / "tools"  # its fixtures stay beside the runtime
FAKE_PROVIDER = HERE / "fixtures" / "multicomponent_fake.py"
FAKE_DOCKER = HERE / "fixtures" / "fake_docker.py"
FIXTURE_OPTIONS = ("--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
                   "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--completion-model",
                   "gpt-6-astra", "--glm-model", "gpt-5.6-sol", "--plan-reviewer-model", "gpt-6-astra")


def component(id, *, depends_on=(), publishes=(), consumes=()):
    return {"id": id, "description": f"the {id} component", "requirements": ["R1"],
            "depends_on": list(depends_on), "publishes_contracts": list(publishes),
            "consumes_contracts": list(consumes)}


def architecture(*components, contracts_dir):
    return mc.Architecture(components={c["id"]: mc.Component(
        id=c["id"], description=c["description"], requirements=tuple(c["requirements"]),
        depends_on=tuple(c["depends_on"]), publishes_contracts=tuple(c["publishes_contracts"]),
        consumes_contracts=tuple(c["consumes_contracts"])) for c in components}, contracts_dir=contracts_dir)


class WorkdirPathTests(unittest.TestCase):
    def test_workdir_is_canonical_whether_the_workspace_is_spelled_var_or_private_var(self):
        import autocode_local_run as local_run
        with tempfile.TemporaryDirectory() as temp:
            raw = Path(temp) / "project"
            raw.mkdir()
            # /var vs /private/var on macOS: same directory, different spelling.
            alias = Path(os.path.realpath(raw))
            self.assertEqual(local_run.workdir(raw).resolve(), local_run.workdir(alias).resolve())
            self.assertEqual(alias, local_run.workdir(raw).parent.parent.resolve())


class BatchingTests(unittest.TestCase):
    def test_independent_components_share_one_batch(self):
        arch = architecture(component("alpha"), component("beta"), contracts_dir=Path("."))
        self.assertEqual([["alpha", "beta"]], [[c.id for c in batch] for batch in arch.batches()])

    def test_a_chain_is_sequenced_into_separate_batches(self):
        arch = architecture(component("a"), component("b", depends_on=["a"]), component("c", depends_on=["b"]),
                            contracts_dir=Path("."))
        self.assertEqual([["a"], ["b"], ["c"]], [[c.id for c in batch] for batch in arch.batches()])

    def test_independent_and_dependent_components_mix(self):
        arch = architecture(component("a"), component("b"), component("c", depends_on=["a", "b"]),
                            contracts_dir=Path("."))
        self.assertEqual([["a", "b"], ["c"]], [[c.id for c in batch] for batch in arch.batches()])

    def test_a_cycle_is_refused(self):
        arch = architecture(component("a", depends_on=["b"]), component("b", depends_on=["a"]), contracts_dir=Path("."))
        with self.assertRaisesRegex(mc.ArchitectureError, "cycle"):
            arch.batches()

    def test_an_unknown_dependency_is_refused_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "components.json").write_text(json.dumps([component("a", depends_on=["ghost"])]))
            with self.assertRaisesRegex(mc.ArchitectureError, "unknown"):
                mc.Architecture.load(directory)

    def test_a_path_traversal_component_id_is_refused_at_load(self):
        # components.json is a model's own output, not trusted input: the id becomes a
        # worktree directory and a branch name, so a slash or ".." must never reach git.
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            for bad_id in ("../../etc", "a/b", "..", "/etc/passwd"):
                with self.subTest(bad_id=bad_id):
                    (directory / "components.json").write_text(json.dumps([component(bad_id)]))
                    with self.assertRaisesRegex(mc.ArchitectureError, "plain name"):
                        mc.Architecture.load(directory)

    def test_a_path_traversal_contract_name_is_refused_at_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "components.json").write_text(json.dumps(
                [component("a", publishes=["../../etc/passwd"])]))
            with self.assertRaisesRegex(mc.ArchitectureError, "plain name"):
                mc.Architecture.load(directory)


class BriefTests(unittest.TestCase):
    def test_brief_embeds_the_contract_schema_and_ownership_rule(self):
        with tempfile.TemporaryDirectory() as tmp:
            contracts = Path(tmp) / "contracts"
            contracts.mkdir()
            (contracts / "greeting.schema.json").write_text('{"type": "object", "properties": {}}')
            arch = architecture(component("writer", publishes=["greeting"]),
                                component("reader", depends_on=["writer"], consumes=["greeting"]),
                                contracts_dir=contracts)
            brief = mc.component_brief(arch.components["reader"], arch)
            self.assertIn("Implement the reader component", brief)
            self.assertIn('"type": "object"', brief)
            self.assertIn("Own only the directory components/reader/", brief)
            self.assertIn("Do not implement or stub another component's directory", brief)


def git(cwd, *args):
    subprocess.run(["git", "-c", "user.name=T", "-c", "user.email=t@example.test", *args],
                   cwd=cwd, check=True, capture_output=True, text=True)


class FakeSchemaCompatibilityTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="component-schema-")
        self.addCleanup(temp.cleanup)
        manifest = Path(temp.name) / "manifest.json"
        manifest.write_text("{}")
        with patch.dict(os.environ, {"FAKE_MANIFEST": str(manifest)}):
            self.complete = runpy.run_path(str(FAKE_PROVIDER))["complete"]
        self.schema = {"type": "object", "required": ["recovery_change"], "properties": {
            "contract_hash": {"type": "string"},
            "recovery_change": {"type": ["object", "null"],
                                "required": ["before", "after", "evidence_refs"], "properties": {
                                    "before": {"type": "string"}, "after": {"type": "string"},
                                    "evidence_refs": {"type": "array", "items": {"type": "string"}}}}}}

    def test_absent_nullable_field_defaults_to_null_and_preserves_explicit_null(self):
        for value in ({}, {"recovery_change": None}):
            with self.subTest(value=value):
                self.assertEqual({"recovery_change": None}, self.complete(value, self.schema))

    def test_explicit_nullable_object_keeps_its_values_and_completes_its_fields(self):
        proposal = {"before": "original", "after": "changed"}
        value = self.complete({"recovery_change": proposal}, self.schema)
        self.assertIs(proposal, value["recovery_change"])
        self.assertEqual({"before": "original", "after": "changed", "evidence_refs": []}, proposal)

    def test_invalid_supplied_value_and_report_identity_are_not_rewritten(self):
        value = {"recovery_change": "invalid object", "contract_hash": "not-the-approved-contract"}
        expected = dict(value)
        self.assertEqual(expected, self.complete(value, self.schema))

    def test_report_repair_preserves_original_failures_and_runs_no_new_checks(self):
        with tempfile.TemporaryDirectory(prefix="component-report-repair-") as tmp:
            root = Path(tmp)
            marker = root / "must-not-be-created"
            command = f"touch {marker}"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"alpha": {"description": "the alpha component", "file": "output.txt",
                                                       "content": "new work", "check": command}}))
            events = root / "original.jsonl"
            event = {"type": "item.completed", "item": {"id": "original-check", "type": "command_execution",
                     "command": command, "exit_code": 1, "aggregated_output": "original failure"}}
            events.write_text(json.dumps(event) + "\n")
            original_events = events.read_bytes()
            report = {"contract_revision": 3, "contract_hash": "original-contract", "task_id": "original-task",
                      "summary": "Implement the alpha component of this fixture", "verdict": "FAIL",
                      "checks_run": [command], "checks": [{"command": command, "exit_code": 1,
                                                           "evidence_ref": "event:original-check"}],
                      "findings": [{"id": "F1", "summary": "Original failure remains open"}],
                      "unverified_criteria": ["C1"], "implementation_captures": []}
            schema = root / "schema.json"
            schema.write_text(json.dumps({"type": "object", "properties": {}, "required": []}))
            output = root / "report.json"
            for repeated in (False, True):
                with self.subTest(repeated=repeated):
                    data = {"report_repair": True, "original": {"stage": "sol", "events": str(events)},
                            "report_identity": {key: report[key] for key in
                                                ("contract_revision", "contract_hash", "task_id")},
                            "rejected_report": {"content": dict(report)},
                            "original_executed_checks": report["checks"]}
                    if repeated:
                        data["original_report"] = {"content": report}
                        data["rejected_report"]["content"].update(verdict="PASS", findings=[], contract_hash="wrong")
                    proc = subprocess.run([sys.executable, "-B", str(FAKE_PROVIDER), "exec",
                                           "--output-schema", str(schema), "-o", str(output)],
                                          input="CURRENT HANDOFF DATA\n" + json.dumps(data), cwd=root,
                                          env={**os.environ, "FAKE_MANIFEST": str(manifest)},
                                          capture_output=True, text=True, timeout=10)
                    self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
                    self.assertEqual(report, json.loads(output.read_text()))
                    self.assertFalse(marker.exists(), "format repair must not execute the reported command")
                    self.assertEqual(original_events, events.read_bytes())
                    self.assertNotIn("command_execution", proc.stdout, "evidence must cite the original events")


class BuildAndIntegrateTests(unittest.TestCase):
    """Two independent components, built through the real CLI with a scripted model."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="multicomponent-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        git(self.repo, "init", "-q")
        # A committed file, not an empty repo: this is what makes the fake's source_refs
        # and code_refs handling meaningful (autopilot._check_code_refs only requires a
        # citation once the workspace has tracked files; an empty repo would never catch
        # the fake citing "task" instead of a real path, as an earlier version of this
        # fake did).
        (self.repo / "architecture.md").write_text("placeholder architecture note\n")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "base")
        bindir = self.root / "bin"
        bindir.mkdir()
        shutil.copy2(FAKE_PROVIDER, bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.manifest = self.root / "manifest.json"
        self.env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "AUTOCODE_HOME": str(self.root / "registry"),
                    "PYTHONDONTWRITEBYTECODE": "1", "FAKE_MANIFEST": str(self.manifest)}
        self.arch = architecture(component("alpha"), component("beta"), contracts_dir=self.root / "contracts")

    def write_manifest(self, **overrides):
        base = {"alpha": {"description": "the alpha component", "file": "components/alpha/message.txt",
                          "content": "from alpha\n", "check": "test -f components/alpha/message.txt"},
                "beta": {"description": "the beta component", "file": "components/beta/message.txt",
                        "content": "from beta\n", "check": "test -f components/beta/message.txt"}}
        base.update(overrides)
        self.manifest.write_text(json.dumps(base))

    def build(self, *, auto_approve=True, max_advances=30):
        return mc.MultiComponentBuild(self.repo, self.arch, options=FIXTURE_OPTIONS, env=self.env, timeout=300,
                                      max_advances=max_advances).build(auto_approve=auto_approve)

    def test_a_worktree_directory_removed_without_prune_is_recovered(self):
        # Reproduces a real failure: deleting .autocode-components/<id> by hand (or a
        # crashed earlier attempt) leaves git's own worktree registration behind, so
        # the next `git worktree add` for that path fails outright — this raised an
        # unhandled subprocess.CalledProcessError before build() pruned stale
        # registrations itself.
        self.write_manifest(beta={"description": "the beta component", "file": "components/beta/message.txt",
                                  "content": "unused\n", "check": "true"})
        git(self.repo, "worktree", "add", "-q", "-b", "components/alpha-stale",
            str(self.repo / ".autocode-components" / "alpha"), "HEAD")
        shutil.rmtree(self.repo / ".autocode-components" / "alpha")
        build = self.build(auto_approve=True)
        self.assertTrue(build["alpha"].ready_to_integrate, build["alpha"].error)

    def test_two_independent_components_build_and_integrate(self):
        # Batching itself (independent components share one ThreadPoolExecutor batch)
        # is proven deterministically in BatchingTests; concurrent execution within a
        # batch is then standard-library ThreadPoolExecutor behavior, not re-proven here
        # by a timing assertion, which would be fragile rather than informative.
        self.write_manifest()
        build = mc.MultiComponentBuild(self.repo, self.arch, options=FIXTURE_OPTIONS, env=self.env, timeout=300)
        results = build.build(auto_approve=True)
        for cid in ("alpha", "beta"):
            self.assertTrue(results[cid].ready_to_integrate, results[cid].error)
            self.assertEqual("TASK_COMPLETE", results[cid].view["status"])
            self.assertTrue((results[cid].workspace / "components" / cid / "message.txt").is_file())

        target = self.repo / "integration"
        git(self.repo, "worktree", "add", str(target), "HEAD")
        outcome = build.integrate(target)
        self.assertEqual({"alpha", "beta"}, set(outcome["integrated"]))
        self.assertIsNone(outcome["failed"])
        self.assertEqual("from alpha\n", (target / "components" / "alpha" / "message.txt").read_text())
        self.assertEqual("from beta\n", (target / "components" / "beta" / "message.txt").read_text())
        # The unrelated original worktree at HEAD never received either component's file:
        # git worktree add above checked out plain HEAD, so this also confirms integrate()
        # did the copying, not something upstream of it.
        self.assertFalse((self.repo / "components").exists())

    def test_a_component_that_writes_outside_its_own_directory_is_refused_at_integration(self):
        self.write_manifest()
        results = self.build()
        self.assertTrue(results["alpha"].ready_to_integrate, results["alpha"].error)
        self.assertTrue(results["beta"].ready_to_integrate, results["beta"].error)
        # A post-build external edit cannot bypass integration's ownership guard.
        (results["beta"].workspace / "shared").mkdir()
        (results["beta"].workspace / "shared/leak.txt").write_text("leaked\n")

        build = mc.MultiComponentBuild(self.repo, self.arch, options=FIXTURE_OPTIONS, env=self.env)
        build.results = results
        target = self.repo / "integration"
        git(self.repo, "worktree", "add", str(target), "HEAD")
        outcome = build.integrate(target)
        self.assertEqual("beta", outcome["failed"])
        self.assertIn("outside components/beta/", outcome["detail"])
        # alpha, processed first alphabetically, is still applied; beta's leak is not.
        self.assertEqual(["alpha"], outcome["integrated"])
        self.assertTrue((target / "components" / "alpha" / "message.txt").is_file())
        self.assertFalse((target / "shared").exists())


    def test_wrong_root_plan_is_refused_before_builder_despite_enthusiastic_reviewer(self):
        observations = self.root / "observations"
        self.write_manifest(beta={"description": "the beta component", "file": "shared/leak.txt",
            "content": "leaked\n", "check": "test -f shared/leak.txt", "observations": str(observations)})
        results = self.build()
        self.assertTrue(results["alpha"].ready_to_integrate, results["alpha"].error)
        self.assertFalse(results["beta"].ready_to_integrate)
        self.assertIn("Component plan", results["beta"].view["stop_reason"])
        stages = {json.loads(path.read_text())["stage"] for path in observations.glob("*.json")}
        self.assertIn("astra_discovery", stages)
        self.assertNotIn("terra", stages)
        self.assertFalse((results["beta"].workspace / "shared/leak.txt").exists())

    def test_component_proves_its_own_tests_with_isolated_stdlib_runtime(self):
        self.env = isolated_python_env(self, self.env, directory=self.root)
        test_python = str(Path(self.env["PATH"].split(os.pathsep)[0]) / "python3")
        test = ("import os\nimport sys\nimport unittest\n"
                "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
                "import message\nclass MessageTests(unittest.TestCase):\n"
                "    def test_c1_message_is_hello(self):\n        self.assertEqual('hello', message.TEXT)\n")
        self.write_manifest(alpha={"description": "the alpha component", "test": "test_c1_message_is_hello",
            "plan_paths": ["components/alpha/"], "files": {"components/alpha/message.py": "TEXT = 'hello'\n",
                "components/alpha/tests/__init__.py": "", "components/alpha/tests/test_message.py": test},
            "check": "python3 -m unittest discover -v -s components/alpha"})
        results = self.build()
        proof = (results["alpha"].view or {}).get("evidence", {}).get("regression_proof")
        self.assertTrue(results["alpha"].ready_to_integrate, (results["alpha"].error, proof))
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertEqual(test_python, shlex.split(proof["commands"]["suite"])[0], proof)
        self.assertTrue(proof["commands"]["suite"].endswith(" -m unittest discover -v -s components/alpha"), proof)
        self.assertEqual({"C1": ["components.alpha.tests.test_message.MessageTests.test_c1_message_is_hello"]}, proof["case_tests"])
        self.assertEqual((sys.executable, str(HERE / "autocode.py")), results["alpha"].run.command)

    def test_taskrun_bad_component_approvals_leave_events_and_checkpoint_unchanged(self):
        observations = self.root / "observations"
        outside = self.root / "outside-marker"
        for index, flow in enumerate((None, "Run the container and build the Docker image.",
                "docker build components/alpha/, deferred to separate integration.",
                "`docker build components/alpha/` at integration time.")):
            with self.subTest(flow=flow):
                path = str(outside) if flow is None else "components/alpha/server.py"
                spec = {"description": "the alpha component", "file": path, "content": "fixture\n",
                    "check": "test -f " + path, "plan_paths": ["components/alpha/"], "observations": str(observations)}
                if flow:
                    spec.update(end_to_end_flow=[flow], permission_boundaries=["No docker build."]
                        if index == 1 else ["Local Docker allowed."])
                self.write_manifest(alpha=spec)
                task = mc.component_brief(self.arch.components["alpha"], self.arch) + f" Admission case {index}."
                run = TaskRun.start(self.repo, task, command=(sys.executable, str(HERE / "autocode.py")),
                    options=FIXTURE_OPTIONS, start_options=("--test-root", "components/alpha"), env=self.env, timeout=120)
                view = run.advance_until_input()
                token = view["needs"].get("token", "r1:" + "0" * 64)
                if view["needs"]["kind"] == "approve_plan":
                    run.show_goal()
                checkpoint = run.run_dir / "state.json"
                before = checkpoint.read_bytes()
                with self.assertRaises(TaskRunError):
                    run.approve_plan(token)
                self.assertEqual(before, checkpoint.read_bytes())
                self.assertIsNone(run.status().get("approved_contract"))
                self.assertIn("Component plan", view.get("stop_reason", ""))
                self.assertFalse(any(event.get("kind") == "goal_approval"
                    for event in json.loads(checkpoint.read_text()).get("user_events", [])))
        self.assertFalse(outside.exists())
        stages = {json.loads(path.read_text())["stage"] for path in observations.glob("*.json")}
        self.assertNotIn("terra", stages)


class LegacyComponentRunTests(unittest.TestCase):
    """Real CLI reattachment of the shipped rootless schema, without implicit approval."""
    setUp = BuildAndIntegrateTests.setUp
    write_manifest = BuildAndIntegrateTests.write_manifest

    def edit(self, run, path):
        view = TaskRun(run.workspace, run.run_dir, command=run.command,
            options=("--edit-goal", str(path)), env=self.env, timeout=120).advance()
        return run.advance_until_input() if (view.get("needs") or {}).get("kind") == "continue" else view

    def saved_run(self, *, generic=False, invalid=False):
        observations = self.root / "observations"
        spec = {"description": "the alpha component", "file": "components/alpha/message.txt",
            "content": "from alpha\n", "check": "test -f components/alpha/message.txt", "observations": str(observations)}
        old_spec = copy.deepcopy(spec)
        if invalid == "flow":
            old_spec["end_to_end_flow"] = ["Build the Docker image, deferred to separate integration."]
        elif invalid:
            old_spec.update(file="server.py", plan_paths=["components/alpha/message.txt"])
        self.write_manifest(alpha=old_spec)
        original = mc.component_brief(self.arch.components["alpha"], self.arch)
        # Seed an approved checkpoint through the public CLI's generic path, then
        # restore the shipped original task in this owned historical fixture only.
        # The approval, contract, events and rootless settings are not rewritten.
        run = TaskRun.start(self.repo, "Legacy task:\n" + original,
            command=(sys.executable, str(HERE / "autocode.py")), options=FIXTURE_OPTIONS,
            env=self.env, timeout=120)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        run.approve_plan(view["needs"]["token"])
        self.write_manifest(alpha=spec)
        saved = util.read(run.run_dir / "state.json")
        self.assertNotIn("test_root", saved["settings"].get("regression", {}))
        if not generic:
            saved["task"] = original
        util.atomic_json(run.run_dir / "state.json", saved)
        return TaskRun(self.repo, run.run_dir, command=run.command, options=FIXTURE_OPTIONS,
                       env=self.env, timeout=120), observations

    def test_valid_legacy_approved_component_advances_with_original_approval(self):
        run, _ = self.saved_run()
        approved = run.status()["approved_contract"]
        saved = util.read(run.run_dir / "state.json")
        before = copy.deepcopy(saved)
        view = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        goals.execution_guard(saved)
        self.assertEqual(before, saved)
        self.assertEqual(approved, view["approved_contract"])
        after = util.read(run.run_dir / "state.json")
        self.assertEqual(before["user_events"], after["user_events"])
        self.assertNotIn("test_root", after["settings"].get("regression", {}))

    def test_invalid_legacy_plan_keeps_evidence_and_needs_edit_then_new_approval(self):
        run, observations = self.saved_run(invalid=True)
        checkpoint = run.run_dir / "state.json"
        saved = util.read(checkpoint)
        saved["validation"] = {"source_revision": "historical", "evidence_hashes": {"proof": "historical"}}
        util.atomic_json(checkpoint, saved)
        retained = {key: copy.deepcopy(saved[key]) for key in ("goal_contract", "user_events", "validation", "settings")}
        view = run.advance()
        self.assertEqual("PAUSED_COMPONENT_PLAN", view["status"])
        self.assertEqual("resume", view["needs"]["kind"])
        self.assertTrue(view["needs"]["edit_required"])
        after = util.read(checkpoint)
        self.assertEqual(retained, {key: after[key] for key in retained})
        before = checkpoint.read_bytes()
        run.resume_paused()
        self.assertEqual(before, checkpoint.read_bytes())
        self.assertFalse(any(json.loads(path.read_text())["stage"] == "terra" for path in observations.glob("*.json")))
        self.assertFalse((self.repo / "components").exists())
        corrected = copy.deepcopy(view["approved_contract"]["body"])
        corrected["deliverables"] = ["components/alpha/message.txt"]
        path = run.run_dir / "corrected.json"
        util.atomic_json(path, corrected)
        revised = self.edit(run, path)
        self.assertEqual("approve_plan", revised["needs"]["kind"])
        before = checkpoint.read_bytes()
        with self.assertRaises(TaskRunError):
            run.approve_plan(view["approved_contract"]["token"])
        self.assertEqual(before, checkpoint.read_bytes())
        run.approve_plan(revised["needs"]["token"])
        self.assertEqual("TASK_COMPLETE", run.advance_until_input()["status"])
        self.assertNotIn("test_root", util.read(checkpoint)["settings"].get("regression", {}))

    def test_generic_rootless_taskrun_keeps_normal_advance_behavior(self):
        run, _ = self.saved_run(generic=True)
        self.assertEqual("TASK_COMPLETE", run.advance_until_input()["status"])
        self.assertNotIn("test_root", util.read(run.run_dir / "state.json")["settings"].get("regression", {}))

    def test_legacy_valid_paths_with_deferred_docker_flow_need_feedback_and_fresh_approval(self):
        run, observations = self.saved_run(invalid="flow")
        view = run.advance()
        self.assertEqual("PAUSED_COMPONENT_PLAN", view["status"])
        self.assertIn("explicitly deferred", view["stop_reason"])
        self.assertFalse(view["done"])
        self.assertFalse(any(json.loads(path.read_text())["stage"] == "terra" for path in observations.glob("*.json")))
        old_token = view["approved_contract"]["token"]
        run.feedback("Component-local check correction.")
        revised = run.advance_until_input()
        self.assertEqual("approve_plan", revised["needs"]["kind"], (revised["status"], revised.get("stop_reason")))
        self.assertNotEqual(old_token, revised["needs"]["token"])
        self.assertFalse((self.repo / "components").exists())
        with self.assertRaises(TaskRunError):
            run.approve_plan(old_token)
        self.assertNotIn("test_root", util.read(run.run_dir / "state.json")["settings"].get("regression", {}))

    def test_legacy_ambiguous_malformed_and_explicit_mismatch_are_rejected_before_builder(self):
        run, observations = self.saved_run()
        checkpoint = run.run_dir / "state.json"
        original = util.read(checkpoint)
        ownership = "Own only the directory components/alpha/; do not create or edit any file outside it."
        for task, root in ((original["task"] + " " + ownership, None),
                (original["task"].replace("components/alpha/;", "components/beta/;"), None),
                (original["task"].replace(ownership, ""), None),
                (original["task"], "components/beta")):
            with self.subTest(task=task, root=root):
                saved = copy.deepcopy(original)
                saved["task"] = task
                if root:
                    saved["settings"].setdefault("regression", {})["test_root"] = root
                util.atomic_json(checkpoint, saved)
                view = run.advance()
                self.assertEqual("PAUSED_COMPONENT_PLAN", view["status"])
                self.assertTrue(view['needs'].get('new_run_required'))
                self.assertFalse(view['needs'].get('edit_required'))
                self.assertEqual(saved["goal_contract"], util.read(checkpoint)["goal_contract"])
                before = checkpoint.read_bytes()
                run.resume_paused()
                self.assertEqual(before, checkpoint.read_bytes())
                corrected = run.run_dir / 'binding-corrected-body.json'
                util.atomic_json(corrected, saved['goal_contract']['body'])
                with self.assertRaises(TaskRunError):
                    self.edit(run, corrected)
                self.assertEqual(before, checkpoint.read_bytes())
                attempts = {path.name: path.read_bytes() for path in observations.glob("*.json")}
                with self.assertRaises(TaskRunError):
                    run.feedback('Component-local check correction.')
                self.assertEqual(before, checkpoint.read_bytes())
                self.assertEqual(attempts, {path.name: path.read_bytes() for path in observations.glob("*.json")})
        self.assertFalse(any(json.loads(path.read_text())["stage"] == "terra" for path in observations.glob("*.json")))
        fresh = self.root / 'fresh-component'
        git(self.root, 'clone', '--quiet', str(self.repo), str(fresh))
        started = TaskRun.start(fresh, original['task'], command=run.command,
            options=FIXTURE_OPTIONS, start_options=('--test-root', 'components/alpha'), env=self.env, timeout=120)
        self.assertEqual('approve_plan', started.status()['needs']['kind'])
        self.assertFalse((fresh / 'components').exists(), 'Fresh start is not implicit Builder approval')


class CliTests(BuildAndIntegrateTests):
    """The real CLI entry point (`autocode components`), not just the Python API."""

    def setUp(self):
        super().setUp()
        architecture = self.repo / "architecture"
        architecture.mkdir()
        (architecture / "components.json").write_text(json.dumps(
            [component("alpha"), component("beta")]))
        (architecture / "dependency_trace.json").write_text(json.dumps({"edges": []}))
        (architecture / "contracts").mkdir()
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "architecture")

    def run_cli(self, *args):
        return subprocess.run([sys.executable, str(HERE / "autocode.py"), "components", *args], cwd=REPO_ROOT,
                              env={**os.environ, **self.env}, capture_output=True, text=True, timeout=120)

    def test_cli_builds_and_integrates_both_components(self):
        self.write_manifest()
        proc = self.run_cli("architecture", "--workspace", str(self.repo), "--auto-approve",
                            "--integrate", "integration", "--options", " ".join(FIXTURE_OPTIONS))
        self.assertEqual(0, proc.returncode, proc.stderr[-1500:])
        summary = json.loads(proc.stdout)
        self.assertEqual({"alpha": "done", "beta": "done"},
                         {cid: info["status"] for cid, info in summary["components"].items()})
        self.assertEqual(["alpha", "beta"], summary["integration"]["integrated"])
        target = self.repo / "integration"
        self.assertEqual("from alpha\n", (target / "components" / "alpha" / "message.txt").read_text())
        self.assertEqual("from beta\n", (target / "components" / "beta" / "message.txt").read_text())

    def test_cli_integrates_into_the_same_target_twice(self):
        # Reproduces a real failure: rerunning the same command after a finished
        # build stopped at alpha with "already exists in working directory" and exit 1.
        self.write_manifest()
        args = ("architecture", "--workspace", str(self.repo), "--auto-approve",
                "--integrate", "integration", "--options", " ".join(FIXTURE_OPTIONS))
        first = self.run_cli(*args)
        self.assertEqual(0, first.returncode, first.stderr[-1500:])
        second = self.run_cli(*args)
        self.assertEqual(0, second.returncode, second.stdout[-1500:] + second.stderr[-1500:])

        self.assertEqual([], json.loads(first.stdout)["integration"]["already_applied"])
        summary = json.loads(second.stdout)
        self.assertTrue(all(info["resumed"] for info in summary["components"].values()))
        self.assertIsNone(summary["integration"]["failed"])
        self.assertEqual(["alpha", "beta"], summary["integration"]["integrated"])
        self.assertEqual(["alpha", "beta"], summary["integration"]["already_applied"])
        target = self.repo / "integration"
        self.assertEqual("from alpha\n", (target / "components" / "alpha" / "message.txt").read_text())
        self.assertEqual("from beta\n", (target / "components" / "beta" / "message.txt").read_text())

    def test_cli_builds_components_that_declare_how_they_run(self):
        # Runtime sentences, an embedded schema's "required" and a backticked contract name
        # all reach the real requirement-coverage and brief-literal checks; the scripted
        # model keeps the whole brief, and alpha delivers two files.
        architecture = self.repo / "architecture"
        (architecture / "contracts" / "greeting.schema.json").write_text(
            '{"type": "object", "required": ["text"], "properties": {"text": {"type": "string"}}}')
        (architecture / "components.json").write_text(json.dumps([
            {**component("alpha", publishes=["greeting"]),
             "runtime": {"kind": "service", "port": 8001, "dockerfile": "Dockerfile", "health": "/health"}},
            {**component("beta", consumes=["greeting"]),  # built against the contract, in the same batch
             "runtime": {"kind": "service", "port": 8002, "start": "python3 server.py", "health": "/health",
                         "runtime_depends_on": ["alpha"]}}]))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "runtime blocks")
        self.write_manifest(alpha={"description": "the alpha component", "check": "test -f components/alpha/Dockerfile",
                                   "files": {"components/alpha/server.py": "print('alpha')\n",
                                             "components/alpha/Dockerfile": "FROM python:3.12-slim\n"}})
        proc = self.run_cli("architecture", "--workspace", str(self.repo), "--auto-approve",
                            "--integrate", "integration", "--options", " ".join(FIXTURE_OPTIONS))
        self.assertEqual(0, proc.returncode, proc.stderr[-1500:])
        summary = json.loads(proc.stdout)
        self.assertEqual({"alpha": "done", "beta": "done"},
                         {cid: info["status"] for cid, info in summary["components"].items()})
        target = self.repo / "integration"
        self.assertEqual("FROM python:3.12-slim\n", (target / "components" / "alpha" / "Dockerfile").read_text())
        self.assertEqual("print('alpha')\n", (target / "components" / "alpha" / "server.py").read_text())
        self.assertEqual("from beta\n", (target / "components" / "beta" / "message.txt").read_text())

    def test_cli_runs_the_integrated_system_locally(self):
        # The whole --run-local path without Docker: a fake `docker` on PATH records each
        # command and publishes alpha on the port of a local HTTP server standing in for
        # its container. A second invocation, against a server that now answers wrongly,
        # fails the smoke check, prints alpha's logs and still tears down.
        architecture = self.repo / "architecture"
        (architecture / "components.json").write_text(json.dumps([
            {**component("alpha"), "runtime": {"kind": "service", "port": 8001, "start": "python3 server.py",
                                               "health": "/health"}},
            {**component("beta"), "runtime": {"kind": "worker", "start": "python3 work.py",
                                              "runtime_depends_on": ["alpha"]}}]))
        (architecture / "smoke.json").write_text(json.dumps({"version": 1, "steps": [
            {"name": "greet", "service": "alpha", "method": "GET", "path": "/greeting", "expect_status": 200,
             "expect_json": {"text": "hello"}}]}))
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "runtime blocks and smoke check")
        self.write_manifest()
        (self.root / "bin" / "docker").write_text(f"#!{sys.executable}\n" + FAKE_DOCKER.read_text())
        (self.root / "bin" / "docker").chmod(0o755)

        class Alpha(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                body = json.dumps({"text": self.server.greeting}).encode()
                self.send_response(200 if self.path in ("/health", "/greeting") else 404)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server = ThreadingHTTPServer(("127.0.0.1", 0), Alpha)
        server.greeting = "hello"
        threading.Thread(target=server.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True).start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        log = self.root / "docker.jsonl"
        # DOCKER_HOST empty: the fake's current context, a local socket, decides where the daemon is.
        self.env.update(FAKE_DOCKER_LOG=str(log), FAKE_DOCKER_PORTS=json.dumps({"alpha": server.server_port}),
                        DOCKER_HOST="", DOCKER_CONTEXT="")
        args = ("architecture", "--workspace", str(self.repo), "--auto-approve", "--integrate", "integration",
                "--run-local", "--options", " ".join(FIXTURE_OPTIONS))

        proc = self.run_cli(*args)
        self.assertEqual(0, proc.returncode, proc.stderr[-1500:])
        summary = json.loads(proc.stdout)
        self.assertEqual(["alpha", "beta"], summary["integration"]["integrated"])
        local = summary["local_run"]
        self.assertEqual("passed", local["status"], local["detail"])
        self.assertEqual([["alpha"], ["beta"]], local["layers"])
        self.assertEqual([("greet", True, 200)], [(s["name"], s["ok"], s["status"]) for s in local["steps"]])
        self.assertTrue(local["torn_down"])
        compose = Path(local["compose_file"])
        # macOS temporary paths may use /var, while the CLI emits canonical /private/var paths.
        self.assertEqual((self.repo / ".autocode-components" / ".local-run" / local["project"]).resolve(),
                         compose.parent.resolve())
        self.assertIn(str((self.repo / "integration" / "components" / "alpha").resolve()), compose.read_text())
        prefix = ["--host", "unix:///var/run/docker.sock", "compose", "-p", local["project"], "-f", str(compose)]
        calls = [json.loads(line) for line in log.read_text().splitlines()]
        self.assertEqual([["compose", "version", "--short"],
                          ["context", "inspect", "--format", "{{.Endpoints.docker.Host}}"],
                          ["--host", "unix:///var/run/docker.sock", "version", "--format", "{{.Server.Version}}"]], calls[:3])
        self.assertEqual([prefix + ["up", "-d", "--build", "--no-deps", "alpha"],
                          prefix + ["up", "-d", "--build", "--no-deps", "beta"],
                          prefix + ["down", "-v", "--remove-orphans", "--rmi", "local"]],
                          [call for call in calls if call[7:8] in (["up"], ["down"])])
        status = subprocess.run(["git", "status", "--porcelain", "--untracked-files=all"], cwd=self.repo / "integration",
                                capture_output=True, text=True, check=True).stdout
        self.assertEqual(["components/"], sorted({line[3:].split("/")[0] + "/" for line in status.splitlines()}))

        server.greeting = "goodbye"
        log.unlink()
        proc = self.run_cli(*args)
        self.assertEqual(1, proc.returncode, proc.stderr[-1500:])
        local = json.loads(proc.stdout)["local_run"]
        self.assertEqual(("failed", "alpha", "greet"), (local["status"], local["failed_component"],
                                                        local["failed_step"]))
        self.assertIn("does not match expect_json", local["detail"])
        self.assertIn("fake log line from alpha", proc.stderr)
        self.assertEqual(["down", "-v", "--remove-orphans", "--rmi", "local"],
                         json.loads(log.read_text().splitlines()[-1])[5:])

    def test_cli_refuses_a_cycle_before_starting_any_component(self):
        (self.repo / "architecture" / "components.json").write_text(json.dumps(
            [{**component("alpha"), "depends_on": ["beta"]}, {**component("beta"), "depends_on": ["alpha"]}]))
        proc = self.run_cli("architecture", "--workspace", str(self.repo), "--auto-approve")
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("cycle", proc.stderr)
        self.assertFalse((self.repo / ".autocode-components").exists())

    def stop_both_for_plan_approval(self):
        """A first invocation without --auto-approve: both components stop for a person."""
        self.write_manifest()
        proc = self.run_cli("architecture", "--workspace", str(self.repo), "--options", " ".join(FIXTURE_OPTIONS))
        self.assertEqual(2, proc.returncode, proc.stderr[-1500:])
        summary = json.loads(proc.stdout)
        self.assertEqual({"alpha": "needs_input", "beta": "needs_input"},
                         {cid: info["status"] for cid, info in summary["components"].items()})
        return summary

    def component_branches(self):
        out = subprocess.run(["git", "branch", "--list", "components/*"], cwd=self.repo, capture_output=True,
                             text=True, check=True).stdout
        return sorted(line.strip(" *+") for line in out.splitlines())

    def test_a_second_invocation_resumes_stopped_components(self):
        first = self.stop_both_for_plan_approval()
        branches = self.component_branches()
        self.assertEqual(2, len(branches))

        proc = self.run_cli("architecture", "--workspace", str(self.repo), "--auto-approve",
                            "--integrate", "integration", "--options", " ".join(FIXTURE_OPTIONS))
        self.assertEqual(0, proc.returncode, proc.stderr[-1500:])
        second = json.loads(proc.stdout)
        for cid in ("alpha", "beta"):
            self.assertEqual("done", second["components"][cid]["status"])
            self.assertTrue(second["components"][cid]["resumed"])
            # The same run was continued, not a new one started beside it.
            self.assertEqual(Path(first["components"][cid]["run_dir"]).resolve(),
                             Path(second["components"][cid]["run_dir"]).resolve())
        self.assertEqual(branches, self.component_branches())
        self.assertEqual(["alpha", "beta"], second["integration"]["integrated"])

    def test_a_run_started_before_a_crash_is_reattached(self):
        # A crash while TaskRun.start was still advancing leaves a worktree and a run
        # but no run_dir in the manifest; the next invocation finds the run itself.
        first = self.stop_both_for_plan_approval()
        manifest_path = self.repo / ".autocode-components" / "manifest.json"
        saved = json.loads(manifest_path.read_text())
        saved["components"]["alpha"]["run_dir"] = None
        manifest_path.write_text(json.dumps(saved))

        proc = self.run_cli("architecture", "--workspace", str(self.repo), "--auto-approve",
                            "--options", " ".join(FIXTURE_OPTIONS))
        self.assertEqual(0, proc.returncode, proc.stderr[-1500:])
        second = json.loads(proc.stdout)
        self.assertEqual(Path(first["components"]["alpha"]["run_dir"]).resolve(),
                         Path(second["components"]["alpha"]["run_dir"]).resolve())
        self.assertEqual("done", second["components"]["alpha"]["status"])

    def test_a_changed_architecture_is_not_resumed(self):
        self.stop_both_for_plan_approval()
        (self.repo / "architecture" / "components.json").write_text(json.dumps(
            [{**component("alpha"), "description": "a different alpha"}, component("beta")]))
        proc = self.run_cli("architecture", "--workspace", str(self.repo), "--auto-approve",
                            "--options", " ".join(FIXTURE_OPTIONS))
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("architecture changed", proc.stderr)

    def test_cli_refuses_to_touch_an_existing_worktree(self):
        (self.repo / ".autocode-components" / "alpha").mkdir(parents=True)
        proc = self.run_cli("architecture", "--workspace", str(self.repo))
        self.assertNotEqual(0, proc.returncode)
        self.assertIn("already exists for alpha", proc.stderr)


if __name__ == "__main__":
    unittest.main()
