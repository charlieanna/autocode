"""Original-oracle binding, execution, revisions and immutable history."""

import copy
import json
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_protected_oracles as guard
import autocode_verify as verify

from .protected_store_fixture import retained_text, rewrite_archive
from .test_protected_oracles_cli import ORIGINAL, VARIANTS


class OriginalOracleTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / "project"
        self.project.mkdir()
        (self.project / "layout.py").write_text("def height():\n    return 20\n")
        (self.project / "test_layout.py").write_text(ORIGINAL)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run(["git", "-C", str(self.project), "add", "."], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.project),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "-qm",
                "original",
            ],
            check=True,
        )
        self.run = self.project / ".autocode/runs/protected"
        self.command = shlex.join([sys.executable, "-B", "-m", "unittest", "discover", "-v"])
        self.state = {"settings": {}}
        self.new_args = SimpleNamespace(run_dir=None)
        self.configure(self.new_args)

    def configure(self, args):
        return guard.reconcile(
            self.state,
            self.state["settings"],
            args,
            self.project,
            self.run,
            is_test_path=verify.is_test_path,
            discover_command=lambda: self.command,
        )

    @property
    def binding(self):
        return self.state["settings"]["protected_tests"]

    def execute(self):
        return guard.replay(self.state, self.project, self.run / "replays", verify.scratch_run, timeout=10)

    def test_all_weakening_variants_execute_original_assertion(self):
        original_hash = self.binding["binding_hash"]
        for name, variant in VARIANTS.items():
            with self.subTest(name=name):
                (self.project / "test_layout.py").write_text(variant)
                with self.assertRaisesRegex(ValueError, "original gate did not pass"):
                    self.execute()
                receipts = list((self.run / "replays").glob("protected-tests/*/receipt.json"))
                latest = json.loads(max(receipts, key=lambda path: path.stat().st_mtime).read_text())
                if name == "renamed":
                    # Zero-test discovery exits 5 on current 3.12 patch releases too.
                    self.assertIn(latest["candidate"]["exit_code"], (0, 5))
                    self.assertIn("Ran 0 tests", latest["candidate"]["tail"])
                else:
                    self.assertEqual(0, latest["candidate"]["exit_code"])
                self.assertEqual(1, latest["original"]["exit_code"])
                self.assertIn("20 != 40", latest["original"]["tail"])
                self.assertEqual(original_hash, latest["binding_hash"])
        self.assertEqual(len(VARIANTS), len(list((self.run / "replays").glob("protected-tests/*/receipt.json"))))

    def test_real_fix_and_meaningful_added_coverage_pass_both_suites(self):
        (self.project / "test_layout.py").write_text(
            ORIGINAL + "\n    def test_positive(self):\n        self.assertGreater(height(), 0)\n"
        )
        (self.project / "layout.py").write_text("def height():\n    return 40\n")
        result = self.execute()
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual(0, result["candidate"]["exit_code"])
        self.assertEqual(0, result["original"]["exit_code"])
        self.assertIn("Ran 2 tests", result["candidate"]["tail"])
        self.assertIn("Ran 1 test", result["original"]["tail"])

    def test_automatic_original_gate_collects_nested_members_without_root_marker(self):
        (self.project / "test_layout.py").unlink()
        nested = self.project / "repos/a-1/tests/test_layout.py"
        nested.parent.mkdir(parents=True)
        nested.write_text(ORIGINAL)
        marker = self.project / "tests/__init__.py"
        marker.parent.mkdir()
        marker.touch()
        (self.project / "layout.py").write_text("def height(): return 40\n")
        self.command = shlex.join([sys.executable, "-m", "unittest", "discover", "-v"])
        self.state = {"settings": {}}
        self.configure(self.new_args)
        self.assertIn("repos/a-1/tests/test_layout.py", self.binding["command"])
        marker.unlink()
        nested.write_text(ORIGINAL + "\n    def test_positive(self): self.assertGreater(height(), 0)\n")
        result = self.execute()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(2, result["candidate"]["results"]["total"])
        self.assertEqual(1, result["original"]["results"]["total"])
        nested.write_text(ORIGINAL.replace("40", "20"))
        (self.project / "layout.py").write_text("def height(): return 20\n")
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.execute()

    def test_explicit_command_only_repair_replays_the_same_original_assertions(self):
        import autocode_args
        import autocode_opencode

        from .test_protected_commands import CommandRecoveryTests, pending_gate_report

        (self.project / "test_layout.py").unlink()
        nested = self.project / "repos/a/tests/test_layout.py"
        nested.parent.mkdir(parents=True)
        nested.write_text(ORIGINAL)
        marker = self.project / "tests/__init__.py"
        marker.parent.mkdir()
        marker.touch()
        self.command = shlex.join([sys.executable, "-m", "unittest", "discover", "-v"])
        self.state = {"settings": {"regression": {"test_command": self.command}}}
        self.configure(self.new_args)
        old = copy.deepcopy(self.binding)
        archive = Path(old["root"]).read_bytes()
        marker.unlink()
        (marker.parent / "test_joint.py").write_text(
            "from layout import height\ndef test_positive_height(): assert height() > 0\n"
        )
        nested.write_text(ORIGINAL + "\n    def test_positive(self): self.assertGreater(height(), 0)\n")
        (self.project / "layout.py").write_text("def height(): return 40\n")
        state, _, _ = CommandRecoveryTests.fixture(self)
        self.state.update({key: value for key, value in state.items() if key not in ("workspace", "settings")})
        self.state["workspace"] = str(self.project)
        contract = self.state["goal_contract"]
        contract["body"]["acceptance_criteria"][0]["verification_method"] = shlex.join(
            [sys.executable, "-m", "pytest", "repos", "tests", "-q"]
        )
        contract["hash"] = guard.util.digest({k: contract[k] for k in ("task_id", "revision", "body")})
        contract["approval_event"]["token"] = "r1:" + contract["hash"]
        pending = copy.deepcopy(pending_gate_report(self.state, self.run / "legacy-report"))
        args = autocode_args.build_parser(None, autocode_opencode.DEFAULT_MODELS).parse_args(
            ["--run-dir", str(self.run), "--resume-paused", "--revise-protected-tests", "@approved-suite"]
        )
        self.configure(args)
        self.assertNotIn("pending_report_repair", self.state)
        self.assertEqual(pending, self.state["report_repair_archive"][-1]["repair"])
        self.assertEqual(old["files"], self.binding["files"])
        self.assertEqual(archive, Path(old["root"]).read_bytes())
        self.assertNotEqual(old["binding_hash"], self.binding["binding_hash"])
        self.assertEqual(old, self.state["user_events"][-1]["previous"])
        result = self.execute()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(3, result["candidate"]["results"]["total"])
        self.assertEqual(2, result["original"]["results"]["total"])
        # A candidate's nested package collides with the restored root package.
        # Keep both markers and every assertion; explicit import isolation must
        # collect all cases rather than omit the root or a member's originals.
        (nested.parent / "__init__.py").touch()
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.execute()
        self.state.update(status="PAUSED_INVALID_OUTPUT")
        previous = copy.deepcopy(self.binding)
        self.configure(args)
        self.assertEqual(previous["files"], self.binding["files"])
        self.assertEqual(
            shlex.join([sys.executable, "-m", "pytest", "tests", "repos", "-q", "--import-mode=importlib"]),
            self.binding["command"],
        )
        result = self.execute()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(3, result["candidate"]["results"]["total"])
        self.assertEqual(2, result["original"]["results"]["total"])
        # A weakened current assertion cannot replace the sealed original one.
        nested.write_text(ORIGINAL.replace("40", "20"))
        (self.project / "layout.py").write_text("def height(): return 20\n")
        with self.assertRaisesRegex(ValueError, "original gate did not pass"):
            self.execute()

    def test_completion_requires_the_unchanged_executed_receipt(self):
        (self.project / "test_layout.py").write_text(
            ORIGINAL + "\n    def test_positive(self):\n        self.assertGreater(height(), 0)\n"
        )
        (self.project / "layout.py").write_text("def height():\n    return 40\n")
        result = self.execute()
        self.state["validation"] = {
            "check_replay": {"protected_tests": result, "verdict": "PASS", "source_revision": result["source_revision"]}
        }
        self.assertTrue(guard.ready(self.state, result["source_revision"]))
        for name in ("candidate", "original"):
            ownership = Path(result[name]["supervision"]["receipt"])
            original = ownership.read_bytes()
            ownership.write_bytes(original + b"changed")
            self.assertFalse(guard.ready(self.state, result["source_revision"]))
            ownership.unlink()
            self.assertFalse(guard.ready(self.state, result["source_revision"]))
            ownership.write_bytes(original)
        self.assertTrue(guard.ready(self.state, result["source_revision"]))
        self.state["validation"]["check_replay"]["verdict"] = "FAIL"
        self.assertFalse(guard.ready(self.state, result["source_revision"]))
        self.state["validation"]["check_replay"]["verdict"] = "PASS"
        Path(result["receipt"]).write_text("{}\n")
        self.assertFalse(guard.ready(self.state, result["source_revision"]))

    def test_restart_retains_binding_and_failed_receipts(self):
        (self.project / "test_layout.py").write_text(VARIANTS["expected_value"])
        with self.assertRaises(ValueError):
            self.execute()
        failed = {path: path.read_bytes() for path in (self.run / "replays").glob("protected-tests/*/receipt.json")}
        self.state = json.loads(json.dumps(self.state))
        self.configure(SimpleNamespace(run_dir=self.run))
        with self.assertRaises(ValueError):
            self.execute()
        self.assertTrue(all(path.read_bytes() == data for path, data in failed.items()))
        self.assertEqual(2, len(list((self.run / "replays").glob("protected-tests/*/receipt.json"))))

    def proposal(self):
        path = self.project / ".autocode/revision.json"
        path.parent.mkdir(exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "previous_hash": self.binding["binding_hash"],
                    "reason": "User changes the layout height requirement",
                    "files": guard.inventory(self.project, verify.is_test_path),
                    "command": self.command,
                }
            )
        )
        return path

    def test_explicit_user_revision_preserves_old_binding_and_fresh_proof(self):
        old = copy.deepcopy(self.binding)
        (self.project / "test_layout.py").write_text(VARIANTS["expected_value"])
        self.state.update(status="PAUSED_REQUESTED", next_stage="sol")
        self.configure(SimpleNamespace(run_dir=self.run, resume_paused=True, revise_protected_tests=self.proposal()))
        self.assertNotEqual(old["binding_hash"], self.binding["binding_hash"])
        self.assertEqual(ORIGINAL, retained_text(old, self.project, "test_layout.py"))
        self.assertEqual("user_cli", self.state["user_events"][0]["actor"])
        self.assertEqual(old, self.state["user_events"][0]["previous"])
        self.assertFalse(guard.ready(self.state, "old-validation"))
        self.assertEqual("PASS", self.execute()["verdict"])

    def test_revision_needs_reconciled_pause_and_exact_old_and_current_inputs(self):
        for field, value in [
            ("status", "RUNNING"),
            ("next_stage", "astra_review"),
            ("active_stage", {"stage": "sol"}),
            ("pending_report_repair", {"stage": "sol"}),
            ("uncertain_artifacts", ["old"]),
            ("active_runner_check", {"stage": "regression_proof"}),
        ]:
            state = copy.deepcopy(self.state)
            self.state.update(status="PAUSED_REQUESTED", next_stage="sol")
            self.state[field] = value
            with self.assertRaises(ValueError):
                self.configure(
                    SimpleNamespace(run_dir=self.run, resume_paused=True, revise_protected_tests=self.proposal())
                )
            self.state = state
        self.state.update(status="PAUSED_REQUESTED", next_stage="sol")
        for field, value in [("previous_hash", "stale"), ("files", {}), ("reason", ""), ("command", "")]:
            proposal = self.proposal()
            data = json.loads(proposal.read_text())
            data[field] = value
            proposal.write_text(json.dumps(data))
            with self.assertRaises(ValueError):
                self.configure(SimpleNamespace(run_dir=self.run, resume_paused=True, revise_protected_tests=proposal))

    def test_retained_test_tampering_is_not_permission_to_rebind(self):
        rewrite_archive(self.binding, {"test_layout.py": VARIANTS["unconditional_pass"].encode()})
        with self.assertRaisesRegex(ValueError, "bundle changed"):
            self.execute()
        self.assertFalse(guard.ready(self.state, "current"))

    def test_replacement_symlink_cannot_write_through_to_source(self):
        target = self.project / "layout.py"
        original = target.read_bytes()
        path = self.project / "test_layout.py"
        path.unlink()
        path.symlink_to(target)
        with self.assertRaises(ValueError):
            self.execute()
        self.assertEqual(original, target.read_bytes())
        receipts = list((self.run / "replays").glob("protected-tests/*/receipt.json"))
        self.assertEqual(1, len(receipts))
        self.assertEqual(1, json.loads(receipts[0].read_text())["original"]["exit_code"])

    def test_dirty_and_untracked_tests_are_bound_at_start(self):
        (self.project / "test_layout.py").write_text(VARIANTS["expected_value"])
        (self.project / "test_extra.py").write_text(
            "import unittest\nclass Extra(unittest.TestCase):\n    def test_no_zero(self):\n        self.assertNotEqual(20, 0)\n"
        )
        self.state = {"settings": {}}
        self.configure(self.new_args)
        self.assertEqual({"test_layout.py", "test_extra.py"}, set(self.binding["files"]))
        self.assertEqual(VARIANTS["expected_value"], retained_text(self.binding, self.project, "test_layout.py"))

    def test_old_saved_runs_are_not_bound_after_a_builder_edit(self):
        self.state = {"settings": {}}
        self.configure(SimpleNamespace(run_dir=self.run))
        self.assertNotIn("protected_tests", self.state["settings"])


class HandoffBoundTests(unittest.TestCase):
    """A stage handoff lists the protected tests of its affected paths within a bound (#1140)."""

    def retained(self, count):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        project = Path(temp.name).resolve() / "project"
        (project / "tests").mkdir(parents=True)
        for index in range(count):
            (project / "tests" / f"test_case_{index:05d}.py").write_text(f"def test_{index}():\n    assert True\n")
        (project / "app.py").write_text("VALUE = 1\n")
        git = ["git", "-C", str(project), "-c", "user.name=Fixture", "-c", "user.email=f@example.test"]
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "seed"], check=True)
        files = guard.inventory(project, verify.is_test_path)
        return guard.retain(project, Path(temp.name) / "run", files, None)

    def test_a_large_inventory_is_listed_within_the_bound_and_names_the_rest(self):
        record = self.retained(500)
        shown = guard.context({"protected_tests": record}, ["tests"])
        listed = shown["assigned_tests"]
        self.assertLessEqual(len(listed), guard.MAX_ASSIGNED_TESTS)
        self.assertLessEqual(len(json.dumps(listed)), guard.MAX_ASSIGNED_BYTES)
        self.assertEqual(list(listed), list(record["files"])[: len(listed)])  # the record's own order
        self.assertEqual(500, shown["assigned_total"])
        self.assertEqual(500 - len(listed), shown["assigned_omitted"])
        self.assertEqual(500, shown["total_files"])
        self.assertIn("assigned_omitted more are listed in the full inventory", shown["instruction"])
        # The full inventory the handoff points at still names every protected file, and the binding is intact.
        self.assertEqual(record["files"], json.loads(Path(shown["inventory_path"]).read_text())["files"])
        self.assertEqual(record["binding_hash"], shown["binding_hash"])

    def test_a_small_inventory_is_listed_whole_as_before(self):
        record = self.retained(3)
        shown = guard.context({"protected_tests": record}, ["tests/"])
        self.assertEqual(list(record["files"].items()), list(shown["assigned_tests"].items()))
        self.assertNotIn("assigned_omitted", shown)
        self.assertNotIn("assigned_total", shown)
        self.assertNotIn("inventory_path, under files", shown["instruction"])
        self.assertEqual({}, guard.context({"protected_tests": record}, ["src"])["assigned_tests"])


if __name__ == "__main__":
    unittest.main()
