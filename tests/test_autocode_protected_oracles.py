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

import autocode_contract_identity as identity
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

    def test_the_pair_runs_only_the_changed_test_modules(self):
        # An unchanged original that cannot pass here belongs to the ordinary gates, not to this pair: the
        # whole bound suite of a large repository could not finish in the replay limit (#2066 rerun).
        (self.project / "test_other.py").write_text(
            "import unittest\nclass Other(unittest.TestCase):\n    def test_other(self):\n        self.fail('ran')\n"
        )
        self.command = shlex.join([sys.executable, "-m", "unittest", "discover", "-v"])
        self.state = {"settings": {}}
        self.configure(self.new_args)
        (self.project / "test_layout.py").write_text(
            ORIGINAL + "\n    def test_positive(self):\n        self.assertGreater(height(), 0)\n"
        )
        (self.project / "layout.py").write_text("def height():\n    return 40\n")
        result = self.execute()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(["test_layout.py"], result["changed_tests"])
        self.assertEqual(
            shlex.join([sys.executable, "-m", "unittest", "-v", "test_layout.py"]), result["replayed_command"]
        )
        self.assertIn("Ran 2 tests", result["candidate"]["tail"])
        self.assertIn("Ran 1 test", result["original"]["tail"])
        self.assertNotIn("test_other", result["candidate"]["tail"] + result["original"]["tail"])
        # The binding keeps the whole suite; only this replay is narrowed.
        self.assertEqual(self.command, self.binding["command"])

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
        self.state.update(status="PAUSED_REQUESTED", next_stage="tester")
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
            ("next_stage", "review_completion"),
            ("active_stage", {"stage": "tester"}),
            ("pending_report_repair", {"stage": "tester"}),
            ("uncertain_artifacts", ["old"]),
            ("active_runner_check", {"stage": "regression_proof"}),
        ]:
            state = copy.deepcopy(self.state)
            self.state.update(status="PAUSED_REQUESTED", next_stage="tester")
            self.state[field] = value
            with self.assertRaises(ValueError):
                self.configure(
                    SimpleNamespace(run_dir=self.run, resume_paused=True, revise_protected_tests=self.proposal())
                )
            self.state = state
        self.state.update(status="PAUSED_REQUESTED", next_stage="tester")
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


class UndeterminedReplayTests(unittest.TestCase):
    """A replay half that could not establish a result blocks completion without directing a repair (#3874).

    ``scratch_run`` is a fake: no command, provider or model runs; each half returns the receipt a case gives.
    """

    REPAIR = "Repair the implementation; a modified assertion is not proof."
    HOME = "cd sub && python3 -m unittest -v test_sub.py"

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.project = Path(temp.name).resolve() / "project"
        (self.project / "sub").mkdir(parents=True)
        (self.project / "layout.py").write_text("def height():\n    return 20\n")
        (self.project / "test_layout.py").write_text(ORIGINAL)
        (self.project / "sub/test_sub.py").write_text(
            "import unittest\nclass Sub(unittest.TestCase):\n    def test_sub(self):\n        self.assertTrue(True)\n"
        )
        git = ["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c", "user.email=f@example.test"]
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        subprocess.run([*git, "add", "."], check=True)
        subprocess.run([*git, "commit", "-qm", "original"], check=True)
        self.run_dir = self.project / ".autocode/runs/protected"
        self.out = self.run_dir / "replays"
        command = shlex.join([sys.executable, "-B", "-m", "unittest", "discover", "-v"])
        self.state = {"settings": {}}
        guard.reconcile(
            self.state,
            self.state["settings"],
            SimpleNamespace(run_dir=None),
            self.project,
            self.run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: command,
        )
        self.assertEqual({"test_layout.py", "sub/test_sub.py"}, set(self.state["settings"]["protected_tests"]["files"]))
        (self.project / "test_layout.py").write_text(
            ORIGINAL + "\n    def test_positive(self):\n        self.assertGreater(height(), 0)\n"
        )
        (self.project / "layout.py").write_text("def height():\n    return 40\n")
        self.calls = []

    @staticmethod
    def passing():
        return {
            "exit_code": 0,
            "timed_out": False,
            "results_expected": True,
            "results": {
                "passed": ["test_layout.LayoutTests.test_height"],
                "failed": [],
                "skipped": [],
                "total": 1,
                "complete": True,
            },
            "tail": "OK",
        }

    @staticmethod
    def timed_out():
        return {"exit_code": None, "timed_out": True, "duration_seconds": 10, "results": None, "results_expected": True}

    @staticmethod
    def failing():
        return {
            "exit_code": 1,
            "timed_out": False,
            "results_expected": True,
            "results": {
                "passed": [],
                "failed": ["test_layout.LayoutTests.test_height"],
                "skipped": [],
                "total": 1,
                "complete": True,
            },
            "tail": "AssertionError: 20 != 40",
        }

    def scratch_run(self, receipts):
        """A fake scratch run: ``receipts`` maps (half, part index) to a receipt; every other run passes."""

        def run(workspace, folder, *, command, timeout, **overlays):
            half = "original" if "files" in overlays else "candidate"
            index = 1 if command == self.HOME else 0
            self.calls.append((half, index, command))
            result = copy.deepcopy(receipts.get((half, index)) or self.passing())
            if result.get("output"):
                folder.mkdir(parents=True, exist_ok=True)
                log = folder / "output.log"
                log.write_bytes(Path(result["output"]).read_bytes())
                result.update(output=str(log), output_sha256=guard.util.file_hash(log))
            if result.get("results_capture"):
                capture = result["results_capture"]
                report = folder / "results.xml"
                report.write_bytes(Path(capture["path"]).read_bytes())
                result["results_capture"] = {"path": str(report), "sha256": guard.util.file_hash(report)}
            if isinstance(result.get("supervision"), dict):
                supervision = result["supervision"]
                owned = folder / "supervision.json"
                owned.write_bytes(Path(supervision["receipt"]).read_bytes())
                result["supervision"] = {**supervision, "receipt": str(owned)}
            return result

        return run

    def replay(self, receipts):
        """The exception the replay raises (None when it returns) and the receipt.json it wrote."""
        try:
            guard.replay(self.state, self.project, self.out, self.scratch_run(receipts), timeout=10)
            error = None
        except Exception as raised:  # noqa: BLE001 - the case asserts which exception it is
            error = raised
        written = list(self.out.glob("protected-tests/*/receipt.json"))
        self.assertEqual(1, len(written))
        return error, written[0]

    def two_parts(self):
        (self.project / "sub/test_sub.py").write_text(
            "import unittest\nclass Sub(unittest.TestCase):\n    def test_sub(self):\n        self.assertEqual(1, 1)\n"
        )
        self.state["test_homes"] = {
            "version": 1,
            "files": {
                "sub/test_sub.py": {
                    "project": "sub",
                    "from": "",
                    "moved": True,
                    "command": self.HOME,
                    "suite": "cd sub && python3 -m unittest discover -v",
                }
            },
        }

    def assert_undetermined(self, error, receipt, phrase):
        self.assertIsInstance(error, guard.util.Paused, error)
        self.assertNotIsInstance(error, ValueError)
        self.assertEqual("PAUSED_VERIFICATION_UNCERTAIN", error.status)
        self.assertIn(phrase, str(error))
        self.assertNotIn(self.REPAIR, str(error))
        saved = json.loads(receipt.read_text())
        self.assertEqual("NOT_VERIFIED", saved["verdict"])
        self.assertEqual("NOT_VERIFIED", error.receipt["verdict"])
        for half in ("candidate", "original"):
            self.assertIsInstance(saved[half], dict)
            self.assertEqual(saved[half], error.receipt[half])
        self.state["validation"] = {
            "check_replay": {
                "protected_tests": error.receipt,
                "verdict": "PASS",
                "source_revision": error.receipt["source_revision"],
            }
        }
        self.assertFalse(guard.ready(self.state, error.receipt["source_revision"]))

    def test_ac4_protected_replay_undetermined_half_blocks_without_repair_direction(self):
        incomplete = {
            **self.passing(),
            "supervision": {"receipt": "/missing/supervision.json"},
            "supervision_sha256": "0" * 64,
            "supervision_errors": ["keeper exited before collection"],
        }
        for name, receipts, phrase in (
            ("original timed out", {("original", 0): self.timed_out()}, "the original half timed out"),
            ("candidate timed out", {("candidate", 0): self.timed_out()}, "the candidate half timed out"),
            (
                "candidate supervision incomplete",
                {("candidate", 0): incomplete},
                "the candidate half's supervision receipt is incomplete: keeper exited before collection",
            ),
        ):
            with self.subTest(name):
                for old in self.out.glob("protected-tests/*/receipt.json"):
                    old.unlink()
                error, receipt = self.replay(receipts)
                self.assert_undetermined(error, receipt, phrase)

    def test_replay_names_an_interrupted_or_unproven_supervision_receipt(self):
        supervised = {
            **self.passing(),
            "supervision": {"receipt": "/missing/supervision.json"},
            "supervision_sha256": "0" * 64,
            "supervision_errors": [],
        }
        for name, receipt, detail in (
            (
                "interrupted",
                {**supervised, "interrupted": True},
                "supervision receipt is incomplete: the run was interrupted",
            ),
            ("unproven", supervised, "supervision receipt does not show a completed, cleaned-up run"),
        ):
            with self.subTest(name):
                for old in self.out.glob("protected-tests/*/receipt.json"):
                    old.unlink()
                error, written = self.replay({("original", 0): receipt})
                self.assert_undetermined(error, written, f"the original half's {detail}")
                self.assertEqual({"original": {"reason": "incomplete_receipt", "detail": detail}}, error.outcome)

    def test_replay_records_why_each_half_could_not_establish_a_result(self):
        aborted = {
            "exit_code": 3,
            "timed_out": False,
            "results_expected": True,
            "results": {
                "passed": ["test_layout.LayoutTests.test_height"],
                "failed": [],
                "skipped": [],
                "total": 1,
                "complete": False,
                "aborted": {"exit_code": 3, "diagnostic": "RuntimeError: plugin crashed"},
            },
            "tail": "INTERNALERROR> RuntimeError: plugin crashed",
        }
        not_started = {
            "command": "python3 -m pytest test_layout.py",
            "exit_code": 1,
            "timed_out": False,
            "results": None,
            "results_expected": True,
            "results_file_written": False,
            "tail": "collecting\n/usr/bin/python3: No module named pytest\n",
        }
        for name, receipts, outcome in (
            (
                "timeout",
                {("original", 0): self.timed_out()},
                {"original": {"reason": "timeout", "detail": "timed out after 10 s"}},
            ),
            (
                "session aborted",
                {("original", 0): aborted},
                {
                    "original": {
                        "reason": "session_aborted",
                        "detail": "aborted its test session (exit 3): RuntimeError: plugin crashed",
                    }
                },
            ),
            (
                "runner not started",
                {("candidate", 0): not_started},
                {
                    "candidate": {
                        "reason": "runner_not_started",
                        "detail": "could not start its test runner (exit 1): /usr/bin/python3: No module named pytest",
                    }
                },
            ),
            (
                "both halves",
                {("candidate", 0): self.timed_out(), ("original", 0): aborted},
                {
                    "candidate": {"reason": "timeout", "detail": "timed out after 10 s"},
                    "original": {
                        "reason": "session_aborted",
                        "detail": "aborted its test session (exit 3): RuntimeError: plugin crashed",
                    },
                },
            ),
        ):
            with self.subTest(name):
                for old in self.out.glob("protected-tests/*/receipt.json"):
                    old.unlink()
                error, written = self.replay(receipts)
                half = next(iter(outcome))
                self.assert_undetermined(error, written, f"the {half} half {outcome[half]['detail']}")
                self.assertEqual(outcome, error.outcome)
                self.assertEqual(outcome, json.loads(written.read_text())["undetermined"])

    def test_ac5_completed_original_failure_still_directs_repair(self):
        error, receipt = self.replay({("original", 0): self.failing()})
        self.assertIsInstance(error, guard.commands.OriginalGateFailure)
        self.assertIsInstance(error, ValueError)
        self.assertIn("Protected tests changed and the original gate did not pass", str(error))
        self.assertIn(self.REPAIR, str(error))
        self.assertIn("Failing original tests: test_layout.LayoutTests.test_height", str(error))
        self.assertEqual("FAIL", json.loads(receipt.read_text())["verdict"])

    def test_a_completed_half_with_a_legacy_error_string_still_fails(self):
        # A completed exit-0 receipt that carries an error (zero tests reported, a scratch copy that could not
        # be made) keeps today's failure handling: it is a FAIL with the repair direction, not a pause.
        for half in ("candidate", "original"):
            with self.subTest(half):
                for old in self.out.glob("protected-tests/*/receipt.json"):
                    old.unlink()
                error, receipt = self.replay({(half, 0): {**self.passing(), "error": "no tests were reported"}})
                self.assertIsInstance(error, guard.commands.OriginalGateFailure)
                self.assertNotIsInstance(error, guard.util.Paused)
                self.assertIn(self.REPAIR, str(error))
                self.assertEqual("FAIL", json.loads(receipt.read_text())["verdict"])

    def approve_replacing_test_height(self):
        """An approved plan whose AC1 replaces the original test_height (#2748): only its failure is excused."""
        criterion = {
            "id": "AC1",
            "criterion": "the layout is taller",
            "verification_method": "test: test_positive",
            "supersedes": [{"file": "test_layout.py", "test": "test_height"}],
        }
        contract = {
            "task_id": "T1",
            "revision": 1,
            "body": {"open_blocking_questions": [], "acceptance_criteria": [criterion]},
        }
        contract["hash"] = guard.util.digest(contract)
        approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract)}
        contract.update(approval_status="approved", approval_event=approval)
        self.state.update(goal_contract=contract, user_events=[approval])

    def test_a_replaced_original_failure_is_excused_only_when_the_candidate_half_passes(self):
        self.approve_replacing_test_height()
        error, receipt = self.replay({("original", 0): self.failing()})
        self.assertIsNone(error)
        saved = json.loads(receipt.read_text())
        self.assertEqual("PASS", saved["verdict"])
        self.assertEqual(["test_layout.LayoutTests.test_height"], saved["superseded"]["excused_failures"])

    def record_base_failure(self, detail="AssertionError: test dependency is missing"):
        proof_dir = self.run_dir / "regression" / "proof-01"
        proof_dir.mkdir(parents=True)
        test_id = "test_layout.LayoutTests.test_height"
        log = proof_dir / "suite_on_base.log"
        log.write_text(
            "test_height (test_layout.LayoutTests) ... FAIL\n"
            "test_other (test_layout.LayoutTests) ... ok\n"
            + "=" * 70
            + "\n"
            + "FAIL: test_height (test_layout.LayoutTests)\n"
            + "-" * 70
            + "\n"
            + detail
            + "\n"
            + "-" * 70
            + "\nRan 2 tests in 0.01s\n\nFAILED (failures=1)\n"
        )
        base = self.failing()
        base["results"]["passed"] = ["test_layout.LayoutTests.test_other"]
        base["results"]["total"] = 2
        base.update(command=self.state["settings"]["protected_tests"]["command"], output=str(log))
        base["output_sha256"] = guard.util.file_hash(log)
        candidate_log = proof_dir / "suite_on_candidate.log"
        candidate_log.write_bytes(log.read_bytes())
        candidate = {
            **copy.deepcopy(base),
            "output": str(candidate_log),
            "output_sha256": guard.util.file_hash(candidate_log),
        }
        proof = {
            "verdict": "PASS",
            "source_revision": guard.source_scope.snapshot(self.project, self.state)["revision"],
            "checks": {"suite_on_base": base, "suite_on_candidate": candidate},
        }
        path = proof_dir / "verification.json"
        path.write_text(json.dumps(proof))
        self.state["regression_proof"] = {
            "verdict": "PASS",
            "source_revision": proof["source_revision"],
            "path": str(path),
            "receipt_sha256": guard.util.file_hash(path),
        }
        replay_log = proof_dir / "replay-source.log"
        replay_log.write_text(
            log.read_text()
            .replace("test_other (test_layout.LayoutTests) ... ok\n", "")
            .replace("Ran 2 tests", "Ran 1 test")
        )
        replay = self.failing()
        replay.update(output=str(replay_log), output_sha256=guard.util.file_hash(replay_log), tail=detail)
        return test_id, replay

    def test_original_tests_failing_identically_on_base_are_recorded_without_blocking(self):
        """A failing original test already present on base must not be blamed on the candidate (#3944)."""
        test_id, replay = self.record_base_failure()
        error, receipt = self.replay({("candidate", 0): replay, ("original", 0): replay})
        self.assertIsNone(error)
        saved = json.loads(receipt.read_text())
        self.assertEqual("PASS", saved["verdict"])
        self.assertIn(test_id, str(saved.get("preexisting_failures")))

    def test_pytest_original_failure_recorded_on_base_does_not_block_replay(self):
        """The reported #3944 case uses actual pytest JUnit output, not unittest text."""
        import autocode_verification_schedule as schedule
        import autocode_verify as verify

        try:
            import pytest  # noqa: F401
        except ImportError:
            self.skipTest("pytest is not installed")
        proof_dir = self.run_dir / "regression" / "pytest-proof"
        proof_dir.mkdir(parents=True)
        project = self.run_dir / "pytest-failure"
        project.mkdir()
        (project / "test_layout.py").write_text(
            "class TestLayout:\n    def test_height(self):\n        import missing_autocode_dependency\n"
        )
        command = shlex.join([sys.executable, "-m", "pytest", "-q", "test_layout.py"])
        framework = verify.Framework("pytest", command)
        base = verify.run_suite(framework, command, project, proof_dir, "base", timeout=30)
        candidate = verify.run_suite(framework, command, project, proof_dir, "candidate", timeout=30)
        self.assertEqual(1, len(base["results"]["failed"]))
        self.assertIn("results_capture", base)
        revision = guard.source_scope.snapshot(self.project, self.state)["revision"]
        proof = {
            "verdict": "PASS",
            "source_revision": revision,
            "checks": {"suite_on_base": base, "suite_on_candidate": candidate},
        }
        path = proof_dir / "verification.json"
        path.write_text(json.dumps(proof))
        self.state["regression_proof"] = {
            "verdict": "PASS",
            "source_revision": revision,
            "path": str(path),
            "receipt_sha256": guard.util.file_hash(path),
        }
        self.assertTrue(schedule.proof_intact(proof["checks"], root=proof_dir))
        error, receipt = self.replay({("candidate", 0): candidate, ("original", 0): candidate})
        saved = json.loads(receipt.read_text())
        self.assertTrue(schedule.intact(saved["candidate"], root=receipt.parent / "candidate"))
        self.assertTrue(schedule.intact(saved["original"], root=receipt.parent / "original"))
        self.assertIsNone(error)
        self.assertEqual("PASS", saved["verdict"])
        receipt.unlink()
        changed = copy.deepcopy(candidate)
        report = proof_dir / "different.xml"
        report.write_text(
            Path(candidate["results_capture"]["path"])
            .read_text()
            .replace("missing_autocode_dependency", "different_dependency")
        )
        changed["results_capture"] = {"path": str(report), "sha256": guard.util.file_hash(report)}
        for half in ("candidate", "original"):
            receipts = {("candidate", 0): candidate, ("original", 0): candidate, (half, 0): changed}
            error, receipt = self.replay(receipts)
            self.assertIsInstance(error, guard.commands.OriginalGateFailure, half)
            self.assertEqual("FAIL", json.loads(receipt.read_text())["verdict"])
            receipt.unlink()

    def test_same_test_with_a_different_failure_is_not_excused(self):
        _test_id, replay = self.record_base_failure()
        log = self.run_dir / "regression" / "candidate-different.log"
        log.write_text(Path(replay["output"]).read_text().replace("test dependency is missing", "bad changed value"))
        changed = {
            **replay,
            "output": str(log),
            "output_sha256": guard.util.file_hash(log),
            "tail": "AssertionError: bad changed value",
        }
        error, receipt = self.replay({("candidate", 0): changed, ("original", 0): replay})
        self.assertIsInstance(error, guard.commands.OriginalGateFailure)
        self.assertEqual("FAIL", json.loads(receipt.read_text())["verdict"])

    def test_same_assertion_with_different_appended_failure_detail_is_not_excused(self):
        # CI attribution intentionally ignores unittest's appended message. It cannot establish
        # identical failures: those messages can describe different underlying failures.
        _test_id, replay = self.record_base_failure("AssertionError: False is not true : database down")
        log = self.run_dir / "regression" / "candidate-different-message.log"
        log.write_text(Path(replay["output"]).read_text().replace("database down", "authorization failed"))
        changed = {**replay, "output": str(log), "output_sha256": guard.util.file_hash(log)}
        error, receipt = self.replay({("candidate", 0): changed, ("original", 0): replay})
        self.assertIsInstance(error, guard.commands.OriginalGateFailure)
        self.assertEqual("FAIL", json.loads(receipt.read_text())["verdict"])

    def test_original_half_with_a_different_failure_is_not_excused(self):
        _test_id, replay = self.record_base_failure()
        log = self.run_dir / "regression" / "original-different.log"
        log.write_text(Path(replay["output"]).read_text().replace("test dependency is missing", "bad changed value"))
        changed = {
            **replay,
            "output": str(log),
            "output_sha256": guard.util.file_hash(log),
            "tail": "AssertionError: bad changed value",
        }
        error, receipt = self.replay({("candidate", 0): replay, ("original", 0): changed})
        self.assertIsInstance(error, guard.commands.OriginalGateFailure)
        self.assertEqual("FAIL", json.loads(receipt.read_text())["verdict"])

    def test_tampered_base_failure_evidence_is_not_excused(self):
        _test_id, replay = self.record_base_failure()
        proof = json.loads(Path(self.state["regression_proof"]["path"]).read_text())
        Path(proof["checks"]["suite_on_base"]["output"]).write_text("tampered evidence\n")
        error, receipt = self.replay({("candidate", 0): replay, ("original", 0): replay})
        self.assertIsNotNone(error)
        self.assertNotEqual("PASS", json.loads(receipt.read_text())["verdict"])

    def test_stale_proof_cannot_excuse_an_identical_failure(self):
        _test_id, replay = self.record_base_failure()
        self.state["regression_proof"]["source_revision"] = "an-old-source-revision"
        error, receipt = self.replay({("candidate", 0): replay, ("original", 0): replay})
        self.assertIsNotNone(error)
        self.assertNotEqual("PASS", json.loads(receipt.read_text())["verdict"])

    def test_tampered_candidate_failure_evidence_is_not_excused(self):
        _test_id, replay = self.record_base_failure()
        proof = json.loads(Path(self.state["regression_proof"]["path"]).read_text())
        Path(proof["checks"]["suite_on_candidate"]["output"]).write_text("tampered evidence\n")
        error, receipt = self.replay({("candidate", 0): replay, ("original", 0): replay})
        self.assertIsNotNone(error)
        self.assertNotEqual("PASS", json.loads(receipt.read_text())["verdict"])
        receipt.unlink()
        # The candidate half genuinely fails another test: the replaced failure of the original half excuses nothing.
        candidate = self.failing()
        candidate["results"]["failed"] = ["test_layout.LayoutTests.test_positive"]
        error, receipt = self.replay({("candidate", 0): candidate, ("original", 0): self.failing()})
        self.assertIsInstance(error, guard.commands.OriginalGateFailure)
        self.assertIn(self.REPAIR, str(error))
        saved = json.loads(receipt.read_text())
        self.assertEqual("FAIL", saved["verdict"])
        self.assertNotIn("superseded", saved)

    def test_a_replaced_original_failure_in_an_incomplete_supervision_receipt_is_not_excused(self):
        self.approve_replacing_test_height()
        original = {
            **self.failing(),
            "supervision": {"receipt": "/missing/supervision.json"},
            "supervision_sha256": "0" * 64,
            "supervision_errors": ["keeper exited before collection"],
        }
        error, receipt = self.replay({("original", 0): original})
        self.assert_undetermined(
            error, receipt, "the original half's supervision receipt is incomplete: keeper exited before collection"
        )
        self.assertNotIn("superseded", json.loads(receipt.read_text()))

    def test_ac16_two_part_replay_genuine_failure_wins_over_timed_out_part(self):
        self.two_parts()
        error, receipt = self.replay({("candidate", 1): self.timed_out()})
        self.assertEqual(4, len(self.calls), self.calls)  # two parts per half
        self.assertIsInstance(error, guard.util.Paused, error)
        self.assertEqual("PAUSED_VERIFICATION_UNCERTAIN", error.status)
        self.assertIn("the candidate half timed out", str(error))
        self.assertNotIn(self.REPAIR, str(error))
        self.assertEqual("NOT_VERIFIED", json.loads(receipt.read_text())["verdict"])
        receipt.unlink()
        self.calls.clear()
        error, receipt = self.replay({("original", 0): self.failing(), ("original", 1): self.timed_out()})
        self.assertEqual(4, len(self.calls), self.calls)
        self.assertIsInstance(error, guard.commands.OriginalGateFailure)
        self.assertIn(self.REPAIR, str(error))
        self.assertEqual("FAIL", json.loads(receipt.read_text())["verdict"])

    def test_ac17_undetermined_replay_pause_is_not_a_rejected_tester_answer(self):
        import autocode_repair_triage as repair_triage
        import autocode_result_application as result_application
        import autocode_stop_explanations as stop_explanations

        error, _receipt = self.replay({("original", 0): self.timed_out()})
        self.assertIsNotNone(error)
        try:
            result_application.raise_if_not_rejected(error)
            raised = None
        except Exception as again:  # noqa: BLE001 - the case asserts it is the same exception
            raised = again
        self.assertIs(error, raised)
        self.assertNotEqual("tests_failed", repair_triage.classify_rejection(self.state, {}, error))
        explained = stop_explanations.explain("PAUSED_VERIFICATION_UNCERTAIN", stop_reason=str(error))
        self.assertIn("could not establish a result", explained["what_it_means"])

    def test_ac18_undetermined_replay_sets_finished_tester_aside_so_resume_runs_it_again(self):
        import autocode_run_records as run_records
        import autocode_run_view as run_view
        import autocode_runner_check as runner_check

        error, receipt = self.replay({("original", 0): self.timed_out()})
        self.assertIsNotNone(error)
        output = self.run_dir / "iterations/003/tester-01.json"
        output.parent.mkdir(parents=True)
        output.write_text(json.dumps({"verdict": "PASS"}))
        record = {
            "stage": "tester",
            "iteration": 3,
            "output": str(output),
            "exit_code": 0,
            "accounted": True,
            "finished_at": "2026-11-20T05:00:00+00:00",
        }
        self.state.update(status="RUNNING", next_stage="tester", active_stage=record, stages=[])

        def persist(path, value):
            guard.util.atomic_json(path, value)

        try:
            run_records.set_aside_at_completion_gate(self.state, self.run_dir, record, error, persist=persist)
            raised = None
        except Exception as again:  # noqa: BLE001 - the case asserts it is the same exception
            raised = again
        self.assertIs(error, raised)
        self.assertNotIn("active_stage", self.state)
        self.assertEqual("PAUSED_VERIFICATION_UNCERTAIN", self.state["status"])
        self.assertEqual(str(error), self.state["stop_reason"])
        self.assertIn("the original half timed out", self.state["stop_reason"])
        self.assertIn("--resume-paused runs the Tester again", self.state["stop_reason"])
        self.assertNotIn("Repair the implementation", self.state["stop_reason"])
        last = self.state["stages"][-1]
        self.assertEqual(("tester", 3), (last["stage"], last["iteration"]))
        self.assertIn("archived-tester-01-", last["output"])
        self.assertTrue(Path(last["output"]).is_file())
        self.assertNotIn("failure_history", self.state)
        self.assertTrue(receipt.is_file())
        need = run_view.needs(self.state) or {}
        self.assertNotIn("abandon_stage", need)
        if "action" in need:
            self.assertEqual("--resume-paused", need["action"])
        runner_check.recover_interrupted(self.state, self.run_dir, persist)


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


class ReplayCommandTests(unittest.TestCase):
    """Which bound suites the protected-test pair narrows to the changed test modules."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        (self.root / "tests").mkdir()
        (self.root / "tests/test_catalog.py").write_text("def test_x():\n    pass\n")
        (self.root / "tests/conftest.py").write_text("")
        self.pytest = "/venv/bin/python -m pytest -q -p no:cacheprovider --continue-on-collection-errors"

    def test_a_python_suite_runs_the_changed_test_modules_with_its_options(self):
        self.assertEqual(
            self.pytest + " tests/test_catalog.py",
            guard.replay_command(self.pytest, ["tests/test_catalog.py"], self.root),
        )
        self.assertEqual(
            "python3 -m unittest -v tests/test_catalog.py",
            guard.replay_command("python3 -m unittest discover -v", ["tests/test_catalog.py"], self.root),
        )

    def test_the_whole_suite_runs_when_narrowing_is_not_shown_safe(self):
        (self.root / "tests/test_link.py").symlink_to("test_catalog.py")
        for command, changed in (
            (self.pytest, ["tests/conftest.py"]),
            (self.pytest, ["tests/test_catalog.py", "tests/conftest.py"]),
            (self.pytest, ["tests/test_gone.py"]),
            (self.pytest, ["tests/test_link.py"]),
            (self.pytest, []),
            ("npm test", ["tests/test_catalog.py"]),
            ("cd pkg && python -m pytest", ["tests/test_catalog.py"]),
            (self.pytest + " --unknown-option value", ["tests/test_catalog.py"]),
        ):
            with self.subTest(command=command, changed=changed):
                self.assertEqual(command, guard.replay_command(command, changed, self.root))


if __name__ == "__main__":
    unittest.main()
