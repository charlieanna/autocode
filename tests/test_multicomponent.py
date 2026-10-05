"""tools/autocode_multicomponent.py: batching/ownership logic, and one end-to-end
build+integrate through the real CLI with a scripted, per-component fake model."""
import json
import os
import runpy
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_multicomponent as mc

REPO_ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parents[1] / "tools"  # its fixtures stay beside the runtime
FAKE_PROVIDER = HERE / "fixtures" / "multicomponent_fake.py"
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
        self.root = Path(temp.name)
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
        self.write_manifest(beta={"description": "the beta component", "file": "shared/leak.txt",
                                  "content": "leaked\n", "check": "test -f shared/leak.txt"})
        results = self.build()
        self.assertTrue(results["alpha"].ready_to_integrate, results["alpha"].error)
        self.assertTrue(results["beta"].ready_to_integrate, results["beta"].error)  # the run itself succeeds; only integration refuses it

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
            self.assertEqual(first["components"][cid]["run_dir"], second["components"][cid]["run_dir"])
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
        self.assertEqual(first["components"]["alpha"]["run_dir"], second["components"]["alpha"]["run_dir"])
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
