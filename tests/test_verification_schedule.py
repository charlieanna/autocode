"""Exact-obligation policy, real clean-copy receipts and invalidation controls."""

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_check_replay as replay
import autocode_command_receipt as command_receipt
import autocode_util as util
import autocode_verification_plan as plan
import autocode_verification_schedule as schedule
import autocode_verify as verify

from .test_verify import Project


class ReceiptPolicyTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.identity = {
            "source": "full-source",
            "command": "exact command",
            "environment": "env",
            "runtime": "runtime",
            "dependencies": "deps",
            "fixture": "seed",
            "contract": "C1 wording",
            "purpose": "independent_clean_replay",
            "obligation": "event-1",
        }
        self.calls = []

    def execute(self, out):
        self.calls.append(out)
        out.mkdir(parents=True)
        log = out / "output.log"
        log.write_text("original PASS output\n")
        return {
            "command": "exact command",
            "exit_code": 0,
            "timed_out": False,
            "error": "",
            "output": str(log),
            "output_sha256": util.file_hash(log),
            "duration_seconds": 1.5,
            "results": {
                "passed": ["test_app.Case.test_c1"],
                "failed": [],
                "skipped": [],
                "collection_errors": [],
                "complete": True,
                "total": 1,
            },
        }

    def run_check(self, *, execute=None, allow=True):
        return schedule.run(
            self.root,
            copy.deepcopy(self.identity),
            execute or self.execute,
            reuse_allowed=allow,
            reason="new_obligation",
            current_identity=lambda: self.identity,
        )

    def test_complete_receipt_resumes_without_a_second_launch(self):
        first = self.run_check()
        second = self.run_check()
        self.assertEqual(1, len(self.calls))
        self.assertEqual("reuse", second["scheduling"]["action"])
        self.assertEqual(first["output"], second["output"])
        self.assertEqual(first["output_sha256"], second["output_sha256"])
        self.assertEqual(first["scheduling"]["receipt_sha256"], second["scheduling"]["receipt_sha256"])

    def test_every_identity_dimension_invalidates_without_fuzzy_equivalence(self):
        self.run_check()
        for key in self.identity:
            with self.subTest(key=key):
                self.identity[key] += " changed"
                self.assertEqual("execute", self.run_check()["scheduling"]["action"])
        self.assertEqual(1 + len(self.identity), len(self.calls))

    def test_mandatory_execution_never_reuses(self):
        self.run_check(allow=False)
        second = self.run_check(allow=False)
        self.assertEqual("execute", second["scheduling"]["action"])
        self.assertEqual(2, len(self.calls))

    def test_missing_or_tampered_output_is_never_reused(self):
        for action in ("tamper", "delete"):
            with self.subTest(action=action):
                first = self.run_check()
                output = Path(first["output"])
                output.write_text("forged PASS") if action == "tamper" else output.unlink()
                second = self.run_check()
                self.assertEqual("execute", second["scheduling"]["action"])
                self.assertNotEqual(first["output"], second["output"])

    def test_tampered_receipt_cannot_create_coverage(self):
        first = self.run_check()
        path = Path(first["scheduling"]["receipt"])
        saved = util.read(path)
        saved["result"]["results"]["passed"] = ["invented_test_id"]
        path.write_text(json.dumps(saved))
        second = self.run_check()
        self.assertEqual("execute", second["scheduling"]["action"])
        self.assertEqual(["test_app.Case.test_c1"], second["results"]["passed"])

    def test_failed_timeout_zero_incomplete_and_absent_results_never_reuse(self):
        mutations = [
            {"exit_code": 1},
            {"timed_out": True},
            {"error": "partial output"},
            {"results": None},
            {
                "results": {
                    "total": 0,
                    "complete": True,
                    "passed": [],
                    "failed": [],
                    "skipped": [],
                    "collection_errors": [],
                }
            },
            {
                "results": {
                    "total": 2,
                    "complete": False,
                    "passed": ["test_a"],
                    "failed": [],
                    "skipped": [],
                    "collection_errors": [],
                }
            },
        ]
        for i, mutation in enumerate(mutations):
            with self.subTest(mutation=mutation):
                self.identity["obligation"] = str(i)
                first = self.run_check(execute=lambda out: {**self.execute(out), **mutation})
                second = self.run_check()
                self.assertEqual("execute", second["scheduling"]["action"])
                self.assertNotEqual(first["output"], second["output"])

    def test_only_entries_that_never_collected_break_attribution(self):
        # A failed hook or fixture executed its test; a module that never collected did
        # not. Results saved before the split have no ``uncollected`` list and fail closed.
        hook_failure = {
            "passed": ["a"],
            "failed": ["b"],
            "skipped": [],
            "collection_errors": ["b"],
            "uncollected": [],
            "total": 2,
            "complete": True,
        }
        self.assertTrue(schedule.complete_results({"results": dict(hook_failure)}))
        legacy = {key: value for key, value in hook_failure.items() if key != "uncollected"}
        self.assertFalse(schedule.complete_results({"results": legacy}))
        uncollected_module = {**hook_failure, "collection_errors": ["c"], "uncollected": ["c"]}
        self.assertFalse(schedule.complete_results({"results": uncollected_module}))

    def test_an_exception_during_verification_records_a_failed_attempt_and_reruns_fresh(self):
        # Issue #414: an exception unwinding in-process (a scratch error, Ctrl-C, the
        # source changing under the obligation) is known-failed state, unlike a hard
        # crash. It leaves a failed receipt and no pending launch, so the next attempt
        # runs a fresh check instead of pausing forever.
        def scratch_error(out):
            raise RuntimeError("git rev-parse failed")

        def interrupted(out):
            self.execute(out)
            raise KeyboardInterrupt()

        def source_changed(out):
            return self.execute(out)

        def stale_obligation():
            raise ValueError("The source changed since this Validator obligation; fresh validation is required")

        cases = (
            ("scratch error", scratch_error, lambda: self.identity),
            ("interrupted", interrupted, lambda: self.identity),
            ("source changed", source_changed, stale_obligation),
        )
        for name, execute, current in cases:
            with self.subTest(case=name):
                self.identity["obligation"] = name
                with self.assertRaises((RuntimeError, KeyboardInterrupt, ValueError)):
                    schedule.run(
                        self.root,
                        copy.deepcopy(self.identity),
                        execute,
                        reuse_allowed=True,
                        reason="new_obligation",
                        current_identity=current,
                    )
                schedule.guard(self.root)
                directory = self.root / util.digest(self.identity)
                reference = util.read(directory / "completed.json")
                saved = util.read(directory / reference["name"])
                self.assertTrue(saved["runner_owned"])
                self.assertFalse(schedule.reusable(saved["result"]))
                self.assertIn("did not complete", saved["result"]["error"])
                if name == "source changed":
                    # The executed result is preserved with the failure recorded on top.
                    self.assertEqual(["test_app.Case.test_c1"], saved["result"]["results"]["passed"])
                before = len(self.calls)
                second = self.run_check()
                self.assertEqual("execute", second["scheduling"]["action"])
                self.assertEqual(before + 1, len(self.calls))

    def test_an_interrupted_attempt_is_never_reused_under_any_identity(self):
        def crash(out):
            self.execute(out)
            raise KeyboardInterrupt()

        with self.assertRaises(KeyboardInterrupt):
            self.run_check(execute=crash)
        self.assertEqual("execute", self.run_check()["scheduling"]["action"])
        for key in self.identity:
            with self.subTest(key=key):
                self.identity[key] += " changed"
                self.assertEqual("execute", self.run_check()["scheduling"]["action"])

    def test_uncertain_command_cleanup_blocks_fresh_execution_for_every_identity(self):
        def uncertain(out):
            self.calls.append(out)
            raise command_receipt.OwnershipUncertain("Recorded command descendants may still be alive")

        with self.assertRaises(command_receipt.OwnershipUncertain) as error:
            self.run_check(execute=uncertain)
        self.assertEqual("PAUSED_VERIFICATION_UNCERTAIN", error.exception.status)
        with self.assertRaisesRegex(util.Paused, "no completed receipt"):
            schedule.guard(self.root)
        for key in self.identity:
            with self.subTest(key=key):
                self.identity[key] += " changed"
                with self.assertRaisesRegex(util.Paused, "no completed receipt"):
                    self.run_check()
        self.assertEqual(1, len(self.calls), "Unknown cleanup cannot authorize another launch")

    def test_a_hard_crash_pending_launch_still_pauses(self):
        # What a hard crash leaves: a pending launch nothing ran to record an
        # outcome for. That stays a human reconciliation, never a fresh launch.
        util.atomic_json(
            self.root / "pending.json",
            {"attempt_id": "orphan", "identity": self.identity, "started_at": "2026-10-05T00:00:00Z"},
        )
        with self.assertRaisesRegex(util.Paused, "no completed receipt"):
            self.run_check()
        self.assertEqual([], self.calls)

    def test_symlinked_output_parent_cannot_supply_reused_evidence(self):
        first = self.run_check()
        output = Path(first["output"])
        original = output.parent
        relocated = self.root / "relocated"
        original.rename(relocated)
        original.symlink_to(relocated, target_is_directory=True)
        second = self.run_check()
        self.assertEqual("execute", second["scheduling"]["action"])
        self.assertNotEqual(first["output"], second["output"])

    def test_symlinked_identity_root_is_refused_before_launch(self):
        outside = self.root / "outside"
        outside.mkdir()
        (self.root / util.digest(self.identity)).symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(util.Paused, "symlinked"):
            self.run_check()
        self.assertEqual([], self.calls)

    def test_crash_after_durable_completion_resumes_without_double_launch(self):
        original = Path.unlink

        def crash(path, *args, **kwargs):
            if path.name == "pending.json":
                raise KeyboardInterrupt()
            return original(path, *args, **kwargs)

        with mock.patch.object(Path, "unlink", crash), self.assertRaises(KeyboardInterrupt):
            self.run_check()
        result = self.run_check()
        self.assertEqual("reuse", result["scheduling"]["action"])
        self.assertEqual(1, len(self.calls))

    def test_changed_context_during_execution_is_not_passing_proof(self):
        def changed(out):
            result = self.execute(out)
            self.identity["fixture"] = "changed while running"
            return result

        result = self.run_check(execute=changed)
        self.assertIn("changed during", result["error"])
        self.assertFalse(schedule.reusable(result))


class CleanReplayTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(
            {
                "app.py": "def value():\n    return 3\n",
                "test_app.py": "import unittest\nfrom app import value\nclass Case(unittest.TestCase):\n"
                "    def test_c1(self):\n        self.assertEqual(3, value())\n",
            }
        )
        self.addCleanup(self.project.close)
        self.run = self.project.evidence
        self.run.mkdir()
        self.events = self.run / "validator.events"
        self.events.write_text("runner-authenticated validator execution\n")
        self.command = f"{sys.executable} -m unittest -v test_app"
        self.record = {
            "stage": "sol",
            "events": str(self.events),
            "output": "validator.json",
            "task_id": "T1",
            "source_revision": util.snapshot(self.project.root)["revision"],
        }
        self.state = {
            "goal_contract": {
                "hash": "approved",
                "body": {
                    "acceptance_criteria": [
                        {
                            "id": "C1",
                            "criterion": "value returns 3",
                            "verification_method": "Inspect independent evidence",
                        }
                    ]
                },
            },
            "current_task": {"id": "T1", "acceptance_criteria": ["C1"]},
        }

    def replay(self, command=None):
        # Policy identity is injected here; actual runtime binding has a separate
        # integration test below, without repeatedly hashing the whole interpreter.
        return replay.replay(
            [{"command": command or self.command, "exit_code": 0, "evidence_ref": "event:check"}],
            self.project.root,
            self.run,
            self.record,
            verify.scratch_run,
            approved_state=self.state,
            execution_identity=lambda *a, **kw: {
                "source_revision": util.snapshot(self.project.root)["revision"],
                "reuse_supported": True,
            },
        )

    def test_report_format_only_repeat_uses_genuine_collected_runner_proof(self):
        first = self.replay()
        self.record["output"] = "repaired-report.json"
        second = self.replay()
        self.assertEqual("PASS", second["verdict"])
        self.assertEqual(1, second["scheduling"]["reused_count"])
        self.assertEqual(first["checks"][0]["output"], second["checks"][0]["output"])
        self.assertEqual(["test_app.Case.test_c1"], second["checks"][0]["results"]["passed"])
        row = second["checks"][0]
        self.assertEqual(row["supervision_sha256"], replay.evidence_pins(second)[row["supervision"]["receipt"]])

    @unittest.skipUnless(verify._python_can_import(sys.executable, "pytest"), "pytest is not installed")
    def test_pytest_reuse_requires_actual_junit_inventory_and_broken_source_fails(self):
        command = f"{sys.executable} -m pytest -q test_app.py"
        first = self.replay(command)
        self.assertEqual("PASS", first["verdict"])
        self.assertEqual(1, first["checks"][0]["results"]["total"])
        self.assertTrue(first["checks"][0]["results"]["passed"][0].endswith("::test_c1"))
        self.assertEqual(1, self.replay(command)["scheduling"]["reused_count"])
        self.project.write({"app.py": "def value():\n    return 4\n"})
        self.record["source_revision"] = util.snapshot(self.project.root)["revision"]
        with self.assertRaisesRegex(ValueError, "exited 1"):
            self.replay(command)

    def test_new_validator_and_builder_receipts_cannot_replace_independent_execution(self):
        self.replay()
        self.events.write_text("different validator execution\n")
        self.assertEqual(1, self.replay()["scheduling"]["executed_count"])
        self.record["stage"] = "terra"
        self.assertIsNone(self.replay()["checks"][0]["scheduling"])

    def test_approved_command_and_explicit_repeat_are_always_executed(self):
        self.state["current_task"]["validation_plan"] = [f"Run `{self.command}` twice."]
        first, second = self.replay(), self.replay()
        self.assertEqual(2, first["scheduling"]["executed_count"])
        self.assertEqual(2, second["scheduling"]["executed_count"])
        self.assertEqual(0, second["scheduling"]["reused_count"])
        self.assertNotEqual(first["checks"][0]["output"], second["checks"][0]["output"])

    def test_same_oracle_rejects_broken_source_and_zero_tests(self):
        self.assertEqual("PASS", self.replay()["verdict"])
        self.project.write({"app.py": "def value():\n    return 4\n"})
        self.record["source_revision"] = util.snapshot(self.project.root)["revision"]
        with self.assertRaisesRegex(ValueError, "exited 1"):
            self.replay()
        self.project.write({"test_app.py": "import unittest\n"})
        self.record["source_revision"] = util.snapshot(self.project.root)["revision"]
        with self.assertRaisesRegex(ValueError, "zero tests|NO TESTS RAN"):
            self.replay()

    def test_uncertain_launch_blocks_protected_checks_before_any_new_execution(self):
        directory = self.run / "check-replay" / "obligations"
        directory.mkdir(parents=True)
        # What a hard crash leaves: a pending launch nothing recorded an outcome
        # for. An in-process exception no longer reaches this state (#414).
        util.atomic_json(
            directory / "pending.json",
            {
                "attempt_id": "orphan",
                "identity": {"obligation": "older-validator"},
                "started_at": "2026-10-05T00:00:00Z",
            },
        )
        with mock.patch.object(replay.protected_oracles, "replay") as protected:
            with self.assertRaisesRegex(util.Paused, "no completed receipt"):
                self.replay()
            protected.assert_not_called()

    def test_plan_projection_does_not_claim_model_selectors_are_collected_tests(self):
        self.state["goal_contract"]["body"]["acceptance_criteria"].append(
            {"id": "C2", "criterion": "Future milestone", "verification_method": "Run `python3 -m unittest -v later`"}
        )
        declaration = plan.obligations(self.state)
        self.assertFalse(declaration["complete"])
        self.assertIsNone(declaration["checks"][0]["collected_test_ids"])
        self.assertEqual(["C1"], declaration["checks"][0]["criterion_ids"])
        self.assertEqual(["C2"], declaration["checks"][1]["criterion_ids"])
        self.assertFalse(declaration["checks"][1]["due_in_current_task"])
        self.assertNotIn("python3 -m unittest -v later", declaration["required_commands"])

    def test_complete_collection_recipe_is_not_mislabeled_executed_inventory(self):
        self.state["goal_contract"]["body"]["acceptance_criteria"][0]["verification_method"] = f"Run `{self.command}`"
        declaration = plan.obligations(self.state)
        self.assertTrue(declaration["plan_recipe_complete"])
        self.assertFalse(declaration["execution_inventory_complete"])
        self.assertFalse(declaration["complete"])
        self.assertEqual("unittest", declaration["checks"][0]["collection_recipe"][0]["collector"])


class ExecutionIdentityTests(unittest.TestCase):
    def test_fixture_symlinks_cannot_hide_unbound_target_changes(self):
        project = Project({"app.py": "x = 1\n", ".gitignore": "ignored-fixture\n"})
        self.addCleanup(project.close)
        project.write({"ignored-fixture": "seed 1"})
        (project.root / "fixture-link").symlink_to("ignored-fixture")
        result = verify.execution_identity(project.root, command=f"{sys.executable} -m unittest -v")
        self.assertFalse(result["reuse_supported"])
        self.assertEqual(["fixture-link"], result["unbound_source_symlinks"])

    def test_unbound_editable_dependency_is_never_a_complete_cache_identity(self):
        project = Project({"app.py": "x = 1\n", ".gitignore": ".venv\n"})
        self.addCleanup(project.close)
        site = f".venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        project.write(
            {
                ".venv/pyvenv.cfg": "include-system-site-packages = false\n",
                ".venv/bin/.keep": "",
                f"{site}/demo-1.dist-info/direct_url.json": json.dumps(
                    {"dir_info": {"editable": True}, "url": (project.root.parent / "outside").as_uri()}
                ),
            }
        )
        python = project.root / ".venv/bin/python"
        python.symlink_to(sys._base_executable)
        result = verify.execution_identity(project.root, command=f"{python} -m unittest -v")
        self.assertFalse(result["cache_binding_complete"])
        self.assertFalse(result["reuse_supported"])
        self.assertEqual(1, len(result["unbound_editables"]))

    def test_controller_editable_binds_its_package_not_the_rest_of_its_checkout(self):
        # CI installs AutoCode editable and runs test modules side by side in that checkout: a file another
        # module writes there, outside the package, must not change a proof's identity mid-run (#665).
        project = Project({"app.py": "VALUE = 1\n", ".gitignore": ".venv\n"})
        self.addCleanup(project.close)
        controller = Path(verify.__file__).resolve().parent.parent
        package = Path(verify.__file__).resolve().parent.relative_to(controller).as_posix()
        site = f".venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        project.write(
            {
                ".venv/pyvenv.cfg": "include-system-site-packages = false\n",
                ".venv/bin/.keep": "",
                f"{site}/autocode_cli-1.dist-info/direct_url.json": json.dumps(
                    {"dir_info": {"editable": True}, "url": controller.as_uri()}
                ),
            }
        )
        python = project.root / ".venv/bin/python"
        python.symlink_to(sys._base_executable)
        snapshot = util.snapshot

        def identity(**files):
            def controller_snapshot(path):
                if Path(path).resolve() != controller:
                    return snapshot(path)
                inventory = {f"{package}/autocode_verify.py": "v1", **files}
                return {"head": "h", "files": inventory, "revision": util.digest({"head": "h", "files": inventory})}

            with mock.patch.object(util, "snapshot", side_effect=controller_snapshot):
                return verify.execution_identity(project.root, command=f"{python} -m unittest -v")

        base = identity()
        self.assertEqual([str(controller)], list(base["editable_sources"]))
        self.assertTrue(base["cache_binding_complete"])
        elsewhere = identity(**{"tests/stray-output.json": "x", ".tmp-run/note.txt": "y"})
        self.assertEqual(base, elsewhere)
        self.assertNotEqual(base["editable_sources"], identity(**{f"{package}/new_module.py": "x"})["editable_sources"])

    def test_unreadable_controller_editable_source_keeps_valid_checks_fresh(self):
        project = Project(
            {
                "app.py": "VALUE = 1\n",
                "test_app.py": "import unittest\nfrom app import VALUE\nclass Case(unittest.TestCase):\n"
                "    def test_c1(self):\n        self.assertEqual(1, VALUE)\n",
                ".gitignore": ".venv\n",
            }
        )
        self.addCleanup(project.close)
        controller = Path(verify.__file__).resolve().parent.parent
        site = f".venv/lib/python{sys.version_info.major}.{sys.version_info.minor}/site-packages"
        metadata = project.root / site / "autocode_cli-1.dist-info/direct_url.json"
        project.write(
            {
                ".venv/pyvenv.cfg": "include-system-site-packages = false\n",
                ".venv/bin/.keep": "",
                str(metadata.relative_to(project.root)): json.dumps(
                    {"dir_info": {"editable": True}, "url": controller.as_uri()}
                ),
            }
        )
        python = project.root / ".venv/bin/python"
        python.symlink_to(sys._base_executable)
        command = f"{python} -m unittest -v test_app"
        snapshot = util.snapshot

        def unreadable_controller(path):
            if Path(path).resolve() == controller:
                raise subprocess.CalledProcessError(
                    128, ["git", "rev-parse", "HEAD"], stderr="nested test repository has no HEAD"
                )
            return snapshot(path)

        with mock.patch.object(util, "snapshot", side_effect=unreadable_controller) as collect:
            result = verify.execution_identity(project.root, command=command)
        collect.assert_has_calls([mock.call(project.root), mock.call(controller)])
        self.assertEqual(snapshot(project.root)["revision"], result["source_revision"])
        self.assertEqual({}, result["editable_sources"])
        self.assertEqual([str(metadata)], result["unbound_editables"])
        self.assertFalse(result["cache_binding_complete"])
        self.assertFalse(result["reuse_supported"])
        self.assertIsNone(result["dependencies"])
        self.assertEqual("fresh_execution_only", result["cache_policy"])

        framework = verify.command_framework(command)

        def execute(out):
            return verify.run_suite(framework, command, project.root, out, "fresh-check", timeout=30)

        checks = [
            schedule.run(
                project.evidence,
                result,
                execute,
                reuse_allowed=result["reuse_supported"],
                reason="unbound_editable",
                current_identity=lambda: result,
            )
            for _ in range(2)
        ]
        self.assertTrue(all(schedule.reusable(check) for check in checks), checks)
        self.assertEqual(["execute", "execute"], [check["scheduling"]["action"] for check in checks])
        self.assertNotEqual(checks[0]["output"], checks[1]["output"])

    def test_venv_inheriting_global_packages_does_not_claim_isolated_cache_identity(self):
        project = Project({"app.py": "x = 1\n", ".gitignore": ".venv\n"})
        self.addCleanup(project.close)
        project.write({".venv/pyvenv.cfg": "include-system-site-packages = true\n", ".venv/bin/.keep": ""})
        python = project.root / ".venv/bin/python"
        python.symlink_to(sys._base_executable)
        result = verify.execution_identity(project.root, command=f"{python} -m unittest -v")
        self.assertFalse(result["reuse_supported"])
        self.assertFalse(result["cache_binding_complete"])
        self.assertIsNone(result["dependencies"])

    def test_global_interpreter_has_no_partial_identity_promoted_to_cache_proof(self):
        project = Project({"app.py": "x = 1\n"})
        self.addCleanup(project.close)
        command = f"{sys._base_executable} -m unittest -v"
        result = verify.execution_identity(project.root, command=command)
        self.assertFalse(result["reuse_supported"])
        self.assertFalse(result["cache_binding_complete"])
        self.assertIsNone(result["dependencies"])
        self.assertEqual("fresh_execution_only", result["cache_policy"])

    def test_full_source_ignored_fixture_dependencies_environment_and_runtime_are_bound(self):
        project = Project(
            {"app.py": "x = 1\n", "elsewhere/test_hidden.py": "x = 1\n", ".gitignore": "vendor/\n_generated.py\n"}
        )
        self.addCleanup(project.close)
        project.write({"vendor/fixture.txt": "seed 1", "_generated.py": "x = 1\n"})
        # Runtime introspection must not execute candidate startup hooks.
        project.write({"sitecustomize.py": "raise SystemExit(77)\n"})
        command = f"{sys.executable} -m unittest -v"
        # Keep dependency scans focused for this test; the actual selected
        # interpreter and shell are still measured by execution_identity.
        original = schedule.tree_identity

        def focused(path, **kwargs):
            path = Path(path)
            return (
                original(path, **kwargs) if path.is_relative_to(project.root) or path.is_file() else {"path": str(path)}
            )

        with mock.patch.object(schedule, "tree_identity", side_effect=focused):
            previous = verify.execution_identity(project.root, command=command)
            for path in ("elsewhere/test_hidden.py", "config.ini", "vendor/fixture.txt", "_generated.py"):
                project.write({path: "changed bytes\n"})
                current = verify.execution_identity(project.root, command=command)
                component = (
                    "dependencies"
                    if path.startswith("vendor/")
                    else "generated_sources"
                    if path == "_generated.py"
                    else "source_revision"
                )
                self.assertNotEqual(previous[component], current[component], path)
                previous = current
            with mock.patch.dict(os.environ, {"VERIFICATION_FIXTURE_SEED": "new-seed"}):
                self.assertNotEqual(
                    previous["environment_hash"],
                    verify.execution_identity(project.root, command=command)["environment_hash"],
                )
            with mock.patch.object(sys, "platform", "different-runtime"):
                self.assertNotEqual(
                    previous["platform"], verify.execution_identity(project.root, command=command)["platform"]
                )
            (project.root / "app.py").chmod(0o400)
            changed = verify.execution_identity(project.root, command=command)
            self.assertEqual(previous["source_revision"], changed["source_revision"])
            self.assertNotEqual(previous["source_metadata"], changed["source_metadata"])


class VerificationRestartCLI(unittest.TestCase):
    def test_reference_and_broken_variants_use_the_same_independent_oracle(self):
        from harness import catalog

        from scenarios import run

        results = run.self_test(catalog.load("verification-reuse"))
        self.assertEqual(
            ["seed", "reference", "broken/accepts-empty", "broken/vacuous-tests"], [name for name, _, _ in results]
        )
        self.assertTrue(all(ok for _, ok, _ in results), results)

    def test_restart_reuses_only_completed_supplemental_check_and_keeps_canonical_execution(self):
        from harness import catalog
        from harness.driver import DriveError, Driver, default_autocode, fake_setup
        from harness.project import materialize

        from scenarios import run as scenario_run

        results = Path(__file__).resolve().parents[1] / ".scenario-runs"
        results.mkdir(exist_ok=True)
        scratch = tempfile.TemporaryDirectory(prefix="verification-restart-", dir=results)
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name).resolve()
        scenario = catalog.load("verification-reuse")
        project = materialize(scenario.seed, root / "project")
        # Only a deliberately selected virtualenv admits cross-invocation
        # reuse; the default global interpreter always executes fresh.
        (project / ".venv").symlink_to(Path(sys.prefix), target_is_directory=True)
        flags, env = fake_setup(scenario, root, scenario.reference)
        hooks = root / "hooks"
        hooks.mkdir()
        shutil.copy2(scenario.dir / "restart_hook.py", hooks / "sitecustomize.py")
        marker = root / "verification-committed.json"
        # Exercise the repair packet before the crash: it has no live goal contract.
        env.update(
            PYTHONPATH=str(hooks),
            SCENARIO_VERIFICATION_CRASH=str(marker),
            XDG_CONFIG_HOME=str(root / "config"),
            CODEX_HOME=str(root / "codex-home"),
            AUTOCODE_PROVIDER="opencode",
            SCENARIO_VERIFICATION_FORCE_REPAIR="1",
        )
        driver = Driver(project, root, flags, env, autocode=default_autocode(), max_steps=20, timeout_seconds=180)
        try:
            stopped = driver.drive(scenario.brief)
        except DriveError as error:
            self.assertIn("exited 97", str(error))
        else:
            self.fail(f"Did not reach the post-proof crash: {stopped['status']}: {stopped.get('stop_reason')}")
        original = json.loads(marker.read_text())["replay"]
        self.assertEqual(2, original["scheduling"]["executed_count"])
        self.assertFalse(driver.view()["done"])
        # Keep the same execution environment: changing PYTHONPATH even solely
        # to remove a test hook would correctly invalidate the old receipt.
        final = driver.until_stopped()
        self.assertTrue(final["done"], final)
        accepted = final["evidence"]["check_replay"]
        self.assertEqual(1, accepted["scheduling"]["reused_count"])
        self.assertEqual(1, accepted["scheduling"]["executed_count"])
        supplemental = next(row for row in accepted["checks"] if row["purpose"] == "independent_clean_replay")
        old = next(row for row in original["checks"] if row["command"] == supplemental["command"])
        self.assertEqual(old["output"], supplemental["output"])
        self.assertEqual(old["output_sha256"], util.file_hash(supplemental["output"]))
        record = scenario_run.run_record(driver, driver.state())
        checks = scenario.oracle()(project, scenario, record)
        self.assertTrue(all(check.ok for check in checks), [check for check in checks if not check.ok])
