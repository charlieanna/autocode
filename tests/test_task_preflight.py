"""Readiness is distinct from proof; all providers here are offline scripts."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import autocode_task_preflight as preflight
import autocode_run_view as run_view
import autocode_taskrun as taskrun
import autocode_util as util

ROOT = Path(__file__).resolve().parents[1]
BRIEF = ("Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
         "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
         "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
         "Python standard library only.")
OPTIONS = ("--engine", "codex", "--joint-planning", "--astra-model", "gpt-6-astra",
           "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--completion-model", "gpt-6-astra",
           "--glm-model", "gpt-5.6-sol", "--plan-reviewer-model", "gpt-6-astra", "--pin-model-role", "terra",
           "--terra-reasoning-effort", "high", "--max-seconds", "0", "--max-stage-seconds", "0",
           "--max-tool-seconds", "0", "--max-idle-seconds", "0")


def git(root, *args):
    return subprocess.run(["git", "-C", str(root), *args], text=True, capture_output=True, check=True).stdout.strip()


def manifest(path, *, argv=None, phase="planning", inputs=(), runtime_files=(), reuse=True):
    body = {"version": 1, "inputs": list(inputs), "runtime_files": list(runtime_files), "checks": [
        {"id": "setup", "phase": phase, "argv": argv or ["{python}", "probe.py"],
         "recovery": "Repair the local fixture setup and resume the same run", "reuse": reuse}]}
    path.write_text(json.dumps(body))
    return body


def entry(path, relative):
    return {"path": relative, "type": "file", "mode": path.stat().st_mode & 0o777,
            "size": path.stat().st_size, "sha256": util.file_hash(path)}


class PreflightFixture(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="task-preflight-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        git(self.workspace, "init", "-q")
        (self.workspace / ".gitignore").write_text(".autocode/\n.venv/\n__pycache__/\n")
        (self.workspace / "probe.py").write_text("print('fixture ready')\n")
        git(self.workspace, "add", ".")
        git(self.workspace, "-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "fixture")
        self.path = self.root / "preflight.json"
        self.run_dir = self.workspace / ".autocode" / "runs" / "fixture"
        manifest(self.path)

    def state(self):
        return {"status": "RUNNING", "next_stage": "requirements_gather", "settings": {
            "engine": "codex", "roles": {"terra": {"model": "fixture", "reasoning_effort": "high", "model_pinned": True}},
            "limits": {"max_seconds": 0, "tool_timeout_seconds": 0}, "task_preflight": preflight.load(self.path)}}


class PrerequisiteExecutionTests(PreflightFixture):
    def test_workspace_success_cannot_hide_nested_scratch_setup_failure(self):
        (self.workspace / "probe.py").write_text("from pathlib import Path\nraise SystemExit(7 if '/.autocode/' in str(Path.cwd()) else 0)\n")
        state = self.state()
        with self.assertRaisesRegex(util.Paused, r"setup \(scratch\)"):
            preflight.guard(state, self.workspace, self.run_dir)
        receipt = json.loads(Path(state["task_preflight"]["receipt"]).read_text())
        self.assertEqual([0, 7], [row["exit_code"] for row in receipt["checks"]])
        self.assertIn("Repair the local fixture", state["stop_reason"] if "stop_reason" in state else receipt["errors"][0])
        self.assertFalse(list((self.run_dir / "preflight").glob("*/scratch/candidate/.git")))

    def test_ignored_proof_input_is_diagnosed_before_any_probe(self):
        asset = self.workspace / ".autocode" / "reference.png"
        asset.parent.mkdir()
        asset.write_bytes(b"approved input")
        manifest(self.path, inputs=[entry(asset, ".autocode/reference.png")])
        with patch.object(preflight.verify, "run_command") as execute:
            with self.assertRaisesRegex(util.Paused, "missing from the copy root"):
                preflight.guard(self.state(), self.workspace, self.run_dir)
        execute.assert_not_called()

    def test_receipt_reuse_and_input_command_runtime_invalidation(self):
        asset = self.workspace / "font.dat"
        asset.write_bytes(b"font")
        dependency = self.root / "browser-capability"
        dependency.write_bytes(b"available")
        manifest(self.path, inputs=[entry(asset, "font.dat")], runtime_files=[str(dependency)])
        state = self.state()
        settings = copy.deepcopy(state["settings"])
        with patch.object(preflight.verify, "run_command", wraps=preflight.verify.run_command) as execute:
            preflight.guard(state, self.workspace, self.run_dir)
            receipt = state["task_preflight"]["receipt"]
            preflight.guard(state, self.workspace, self.run_dir)
            self.assertEqual(2, execute.call_count)
            self.assertEqual(receipt, state["task_preflight"]["receipt"])
            dependency.write_bytes(b"changed capability")
            preflight.guard(state, self.workspace, self.run_dir)
            self.assertEqual(4, execute.call_count)
            state["settings"]["regression"] = {"test_command": "changed approved invocation"}
            preflight.guard(state, self.workspace, self.run_dir)
            self.assertEqual(6, execute.call_count)
            asset.chmod(0o600)  # Non-executable modes are also bound, not just Git content.
            with self.assertRaisesRegex(util.Paused, "mode"):
                preflight.guard(state, self.workspace, self.run_dir)
            self.assertEqual(6, execute.call_count)
            self.assertTrue(all(call.kwargs["timeout"] == 120 for call in execute.call_args_list))
        self.assertEqual(settings["roles"], state["settings"]["roles"])
        self.assertEqual(settings["limits"], state["settings"]["limits"])

    def test_receipt_is_readiness_only_and_tampering_forces_new_execution(self):
        state = self.state()
        preflight.guard(state, self.workspace, self.run_dir)
        Path(state["task_preflight"]["receipt"]).write_text("tampered")
        with patch.object(preflight.verify, "run_command", wraps=preflight.verify.run_command) as execute:
            preflight.guard(state, self.workspace, self.run_dir)
        self.assertEqual(2, execute.call_count)
        view = run_view.view(state)
        self.assertEqual("prerequisite", view["task_preflight"]["kind"])
        self.assertEqual("READY", view["task_preflight"]["status"])
        self.assertFalse(view["done"])
        self.assertFalse(view["evidence"].get("regression_proof"))
        self.assertNotIn("current_visual_acceptance", view["task_preflight"])
        self.assertFalse(view["task_preflight"]["execution_identity"]["provider_sandbox_attested"])

    def test_existing_binary_is_executed_and_failed_capability_blocks_the_selected_phase(self):
        browser = self.root / "browser"
        browser.write_text("#!/bin/sh\nprintf 'fixture: launch denied\\n' >&2\nexit 13\n")
        browser.chmod(0o755)
        manifest(self.path, argv=[str(browser), "--headless"], phase="build")
        state = self.state()
        with patch.object(preflight.verify, "run_command", wraps=preflight.verify.run_command) as execute:
            preflight.guard(state, self.workspace, self.run_dir)  # Planning has no browser prerequisite.
            execute.assert_not_called()
            state["next_stage"] = "terra"
            with self.assertRaisesRegex(util.Paused, "build prerequisite blocked"):
                preflight.guard(state, self.workspace, self.run_dir)
        self.assertEqual(1, execute.call_count)
        receipt = json.loads(Path(state["task_preflight"]["receipt"]).read_text())
        self.assertEqual(13, receipt["checks"][0]["exit_code"])
        self.assertIn("launch denied", receipt["checks"][0]["tail"])

    def test_unobservable_capability_can_refuse_reuse_and_source_mutation_is_blocked(self):
        manifest(self.path, reuse=False)
        state = self.state()
        with patch.object(preflight.verify, "run_command", wraps=preflight.verify.run_command) as execute:
            preflight.guard(state, self.workspace, self.run_dir)
            preflight.guard(state, self.workspace, self.run_dir)
        self.assertEqual(4, execute.call_count)
        (self.workspace / "probe.py").write_text("from pathlib import Path\nPath('product.txt').write_text('unexpected mutation')\n")
        with self.assertRaisesRegex(util.Paused, "changed source"):
            preflight.guard(state, self.workspace, self.run_dir)
        self.assertEqual("unexpected mutation", (self.workspace / "product.txt").read_text())

    def test_manifest_correction_requires_its_own_reconciled_pause(self):
        state = self.state()
        previous = copy.deepcopy(state["settings"])
        manifest(self.path, argv=["{python}", "-c", "print('corrected setup')"])
        args = SimpleNamespace(task_preflight=self.path, resume_paused=True)
        with self.assertRaisesRegex(ValueError, "PAUSED_TASK_PREFLIGHT"):
            preflight.configure(state, copy.deepcopy(previous), args)
        paused = {**state, "status": "PAUSED_TASK_PREFLIGHT", "active_stage": {"pid": 1}}
        with self.assertRaisesRegex(ValueError, "active or uncertain"):
            preflight.configure(paused, copy.deepcopy(previous), args)
        paused.pop("active_stage")
        changed = preflight.configure(paused, copy.deepcopy(previous), args)
        self.assertEqual(previous["roles"], changed["roles"])
        self.assertEqual(previous["limits"], changed["limits"])
        self.assertEqual(previous["task_preflight"], paused["user_events"][0]["previous"])

    def test_bad_manifest_is_not_silently_accepted(self):
        body = manifest(self.path)
        for broken in ({**body, "version": 2}, {**body, "checks": []}, {**body, "unknown": True},
                       {**body, "checks": [body["checks"][0], body["checks"][0]]},
                       {**body, "inputs": [{"path": "../escape", "type": "file"}]}):
            self.path.write_text(json.dumps(broken))
            with self.assertRaises(ValueError):
                preflight.load(self.path)


class CollectionReadinessTests(PreflightFixture):
    def collect(self, *args):
        return subprocess.run([sys.executable, str(ROOT / "tools/autocode_preflight_unittest.py"), *args],
                              cwd=self.workspace, text=True, capture_output=True)

    def test_discovery_import_success_does_not_mask_named_import_failure(self):
        folder = self.workspace / "fixture_tests"
        folder.mkdir()
        (folder / "__init__.py").write_text("")
        (folder / "fixture_helper.py").write_text("READY = True\n")
        case = folder / "test_setup.py"
        case.write_text("import fixture_helper\nimport unittest\nclass Setup(unittest.TestCase):\n def test_never_execute(self):\n  raise AssertionError('collection must not execute this')\n")
        result = self.collect("--named", "fixture_tests.test_setup", "--discover", "fixture_tests")
        self.assertEqual(1, result.returncode, result.stderr)
        rows = json.loads(result.stdout)["collections"]
        self.assertEqual(["NOT_READY", "COLLECTION_READY"], [row["status"] for row in rows])
        self.assertIn("fixture_helper", rows[0]["errors"][0])
        case.write_text(case.read_text().replace("import fixture_helper", "from fixture_tests import fixture_helper"))
        result = self.collect("--named", "fixture_tests.test_setup", "--discover", "fixture_tests", "--top-level", ".")
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        rows = json.loads(result.stdout)["collections"]
        self.assertEqual(rows[0]["identities"], rows[1]["identities"])
        self.assertTrue(all(row["tests_executed"] is False for row in rows))
        self.assertNotIn("PASS", result.stdout)

    def test_empty_duplicate_and_changed_inventory_refuse_readiness(self):
        empty = self.collect("--discover", ".")
        self.assertEqual(1, empty.returncode)
        case = self.workspace / "test_setup.py"
        case.write_text("import unittest\nclass Setup(unittest.TestCase):\n def test_a(self): raise AssertionError('never execute')\n")
        duplicate = self.collect("--named", "test_setup", "--named", "test_setup")
        self.assertEqual(1, duplicate.returncode)
        inventory = self.root / "ids.json"
        inventory.write_text(json.dumps({"named": ["test_setup.Setup.test_removed"]}))
        changed = self.collect("--named", "test_setup", "--expected-ids", str(inventory))
        self.assertEqual(1, changed.returncode)
        self.assertIn("approved identity inventory", changed.stdout)


class PrerequisiteCliTests(PreflightFixture):
    def setUp(self):
        super().setUp()
        bindir = self.root / "bin"
        bindir.mkdir()
        provider = (ROOT / "tools/live_fixture_provider.py").read_text()
        hook = "    with open(os.environ['FAKE_PREFLIGHT_CALLS'], 'a') as marker:\n        marker.write(stage + '\\n')\n"
        provider = provider.replace("import json", "import os\nimport json", 1)
        provider = provider.replace("    output.write_text(json.dumps(report))", hook + "    output.write_text(json.dumps(report))")
        (bindir / "codex").write_text(provider)
        (bindir / "codex").chmod(0o755)
        self.marker = self.root / "provider-calls"
        self.env = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}", "AUTOCODE_HOME": str(self.root / "registry"),
                    "FAKE_PREFLIGHT_CALLS": str(self.marker), "PYTHONDONTWRITEBYTECODE": "1"}

    def start(self):
        return taskrun.TaskRun.start(self.workspace, BRIEF, options=OPTIONS,
            start_options=("--workflow", "build", "--task-preflight", str(self.path)), env=self.env, timeout=60)

    def test_no_paid_stage_on_failed_setup_one_offline_dispatch_after_explicit_resume(self):
        (self.workspace / "probe.py").write_text("from pathlib import Path\nraise SystemExit(9 if '/.autocode/' in str(Path.cwd()) else 0)\n")
        run = self.start()
        view = run.status()
        self.assertEqual("PAUSED_TASK_PREFLIGHT", view["status"], view)
        self.assertIn("scratch", view["stop_reason"])
        self.assertFalse(self.marker.exists())
        failed_receipt = Path(view["task_preflight"]["receipt"])
        (self.workspace / "probe.py").write_text("print('bounded setup correction')\n")
        # Existing public CLI operation stops after exactly the corrected next step.
        run.options += ("--pause-after-stage",)
        view = run.resume_paused()
        self.assertEqual(["requirements_gather"], self.marker.read_text().splitlines())
        self.assertEqual("READY", view["task_preflight"]["status"])
        self.assertFalse(view["done"])
        self.assertEqual("BLOCKED", json.loads(failed_receipt.read_text())["status"])
        self.assertEqual("prerequisite", view["task_preflight"]["kind"])
        ready_receipt = view["task_preflight"]["receipt"]
        # Another planning step with unchanged source/runtime reuses readiness.
        view = run.resume_paused()
        self.assertEqual(["requirements_gather", "astra_discovery"], self.marker.read_text().splitlines())
        self.assertEqual(ready_receipt, view["task_preflight"]["receipt"])

    def test_corrected_manifest_uses_public_cli_and_preserves_failed_receipt(self):
        manifest(self.path, argv=["{python}", "-c", "raise SystemExit(3)"])
        run = self.start()
        failed = run.status()["task_preflight"]
        corrected = self.root / "corrected.json"
        manifest(corrected, argv=["{python}", "-c", "print('operator-approved correction')"])
        result = subprocess.run([*run.command, "--workspace", str(self.workspace), "--run-dir", str(run.run_dir),
            "--task-preflight", str(corrected), "--resume-paused", "--pause-after-stage", "--no-chat"],
            text=True, capture_output=True, env={**os.environ, **self.env}, timeout=60)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        view = run.status()
        self.assertEqual("READY", view["task_preflight"]["status"], view)
        self.assertNotEqual(failed["manifest_hash"], view["task_preflight"]["manifest_hash"])
        self.assertEqual("BLOCKED", json.loads(Path(failed["receipt"]).read_text())["status"])
        self.assertEqual(["requirements_gather"], self.marker.read_text().splitlines())

    def test_malformed_manifest_refused_before_run_allocation_or_provider(self):
        self.path.write_text('{"version":1,"checks":[]}')
        with self.assertRaises(taskrun.TaskRunError):
            self.start()
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.workspace / ".autocode").exists())


if __name__ == "__main__":
    unittest.main()
