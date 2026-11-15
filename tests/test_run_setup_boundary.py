"""Preload refusals preserve their CLI boundary without acquiring pause authority."""

import contextlib
import copy
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock, patch

import autocode_metadata_custody as custody
import autocode_native_invocation as invocation
import autocode_operational_block as block
import autocode_run_setup as setup
import autocode_run_view as run_view
import autocode_util as util

from . import test_recovery_view as view_fixtures
from .test_operational_block import admission_failure


class RunSetupBoundaryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.run = self.workspace / ".autocode/runs/task"
        self.run.mkdir(parents=True)
        self.path = self.run / "state.json"
        self.state = view_fixtures.RecoveryViewTests().state(
            "AWAITING_GOAL_APPROVAL",
            workspace=str(self.workspace),
            run_dir=str(self.run),
            task="Preserve this checkpoint",
            task_id="task",
        )
        self.args = SimpleNamespace(task=None, status=False, dry_run=False)
        self.owner = MagicMock()
        self.owner.__enter__.return_value = self.owner
        self.owner.__exit__.return_value = False
        self.owner.result.side_effect = lambda code, **kwargs: code
        self.runtime = SimpleNamespace(
            run_setup=setup,
            stop_policy=SimpleNamespace(
                applied_stop=Mock(return_value=False), refuse_before_configure=Mock(return_value=2)
            ),
            util=SimpleNamespace(
                runner_identity=Mock(return_value={}),
                runner_changed=Mock(return_value=None),
                JSONCommitUncertain=util.JSONCommitUncertain,
            ),
            write_json=Mock(wraps=util.atomic_json),
            run_actions=SimpleNamespace(handle=Mock(return_value=None)),
            build_loop=SimpleNamespace(run=Mock(return_value=0)),
            support=SimpleNamespace(Paused=util.Paused),
            now=util.now,
            resolver_runtime=SimpleNamespace(record_operational_exhaustion=Mock()),
            pause_command=SimpleNamespace(recovery=Mock(return_value="Unexpected resume advice")),
            task_workspaces=setup.task_workspaces,
        )
        self.enterContext(patch("subprocess.Popen", side_effect=AssertionError("No process in boundary regression")))
        self.enterContext(patch.object(setup.keep_awake, "hold"))
        self.owner_factory = self.enterContext(patch.object(setup.controller_owner, "Owner", return_value=self.owner))
        self.enterContext(patch.object(setup.worker_binding, "bind"))
        self.load = self.enterContext(patch.object(setup, "load_locked", return_value=self.state))
        self.stderr = self.enterContext(contextlib.redirect_stderr(io.StringIO()))

    def run_locked(self, *, native=None):
        return setup.run_locked(
            self.runtime, self.args, None, self.state, self.path, self.run, self.workspace, native=native
        )

    def ordinary_failures(self):
        typed = custody.CustodyUncertain()
        uncertain = util.JSONCommitUncertain(
            OSError("directory durability unconfirmed"),
            candidate={"replaced": "must not overwrite the original state"},
            replaced=True,
            durable=False,
        )
        # Even a prior typed refusal cannot turn an uncertain JSON replacement
        # into authority to overwrite that candidate with a new pause.
        uncertain.__context__ = typed
        return (
            ValueError("setting refused"),
            RuntimeError("preflight refused"),
            OSError("preload unavailable"),
            util.Paused("PAUSED_REGISTRY", "registry has already recorded its own failure"),
            custody.ProcessError(custody.BLOCKED),
            custody.ProcessError(custody.BUSY),
            admission_failure(),
            KeyboardInterrupt(),
            uncertain,
        )

    def test_ordinary_preload_errors_preserve_exact_exception_and_checkpoint(self):
        util.atomic_json(self.path, self.state)
        original = copy.deepcopy(self.state)
        saved = self.path.read_bytes()
        for failure in self.ordinary_failures():
            with self.subTest(failure=type(failure).__name__, reason=str(failure)):
                self.state = copy.deepcopy(original)
                util.atomic_json(self.path, self.state)
                self.runtime.write_json.reset_mock()
                self.runtime.resolver_runtime.record_operational_exhaustion.reset_mock()
                self.load.side_effect = failure
                with self.assertRaises(type(failure)) as caught:
                    self.run_locked()
                self.assertIs(failure, caught.exception)
                self.assertEqual(original, self.state)
                self.assertEqual(saved, self.path.read_bytes())
                self.runtime.write_json.assert_not_called()
                self.runtime.resolver_runtime.record_operational_exhaustion.assert_not_called()
                self.owner_factory.assert_not_called()

    def test_runner_warning_errors_retain_the_same_preload_boundary(self):
        failure = RuntimeError("cannot inspect runner identity")
        self.runtime.util.runner_changed.side_effect = failure
        original = copy.deepcopy(self.state)
        with self.assertRaises(RuntimeError) as caught:
            self.run_locked()
        self.assertIs(failure, caught.exception)

        self.assertEqual(original, self.state)
        self.runtime.write_json.assert_not_called()
        self.owner_factory.assert_not_called()

    def test_admission_preload_keeps_exact_exception_checkpoint_and_cleanup(self):
        failure = admission_failure()
        self.load.side_effect = failure
        util.atomic_json(self.path, self.state)
        saved = self.path.read_bytes()
        original = copy.deepcopy(self.state)
        with self.assertRaises(custody.ProcessError) as caught:
            self.run_locked()
        self.assertIs(failure, caught.exception)
        self.assertEqual(saved, self.path.read_bytes())
        self.assertEqual(original, self.state)
        self.runtime.write_json.assert_not_called()
        self.owner_factory.assert_not_called()
        self.runtime.resolver_runtime.record_operational_exhaustion.assert_not_called()

    def test_public_launch_refusal_still_cleans_unsaved_run_and_unused_worktree(self):
        self.args._fresh_task_worktree = {
            "workspace": str(self.workspace),
            "project_workspace": str(self.workspace / "project"),
            "base_commit": "fixture-base",
            "branch": "autocode/unused",
        }
        self.args._fresh_run_dir = self.run
        resolved = self.workspace, self.run, self.path, self.state
        original = copy.deepcopy(self.state)

        def git(directory, *arguments):
            return "fixture-base" if arguments == ("rev-parse", "HEAD") else ""

        def locked(*args, **kwargs):
            return self.run_locked()

        with (
            patch.object(setup, "resolve", return_value=resolved),
            patch.object(invocation, "run", side_effect=locked),
            patch.object(setup.task_workspaces, "git", side_effect=git) as commands,
        ):
            for failure in self.ordinary_failures():
                with self.subTest(failure=type(failure).__name__):
                    self.run.mkdir(exist_ok=True)
                    self.path.unlink(missing_ok=True)
                    (self.run / "writer.lock").touch()
                    self.state = copy.deepcopy(original)
                    self.runtime.write_json.reset_mock()
                    self.load.side_effect = failure
                    commands.reset_mock()
                    with self.assertRaises(type(failure)) as caught:
                        invocation.resolve_and_run(self.runtime, self.args, None)
                    self.assertIs(failure, caught.exception)
                    self.assertFalse(self.run.exists())
                    arguments = [call.args[1:] for call in commands.call_args_list]
                    self.assertIn(("worktree", "remove", "--force", str(self.workspace)), arguments)
                    self.assertIn(("branch", "-D", "autocode/unused"), arguments)
                    self.runtime.write_json.assert_not_called()

    def test_typed_preload_denials_persist_without_person_or_model_recovery(self):
        for wrapped in (False, True):
            with self.subTest(wrapped=wrapped):
                self.state = copy.deepcopy(self.load.return_value)
                failure = custody.CustodyUncertain()
                if wrapped:
                    wrapper = ValueError("sanitized metadata wrapper")
                    wrapper.__cause__ = failure
                    failure = wrapper
                self.load.side_effect = failure
                self.assertEqual(2, self.run_locked())
                saved = util.read(self.path)
                self.assertEqual("PAUSED_PROCESS_CLEANUP", saved["status"])
                self.assertIsNotNone(block.receipt(saved, block.METADATA))
                public = run_view.view(saved, workspace=self.workspace, run_dir=self.run)
                self.assertIsNone(public["next_command"])
                self.assertIsNone(public["needs"])
                self.assertEqual("blocked", public["recovery"]["category"])
                self.runtime.build_loop.run.assert_not_called()
                self.runtime.resolver_runtime.record_operational_exhaustion.assert_not_called()
                self.runtime.pause_command.recovery.assert_not_called()
                self.owner_factory.assert_not_called()

    def test_applied_stop_precedes_even_a_typed_preload_denial(self):
        self.runtime.stop_policy.applied_stop.return_value = True
        self.load.side_effect = custody.CustodyUncertain()
        self.assertEqual(2, self.run_locked())
        self.load.assert_not_called()
        self.runtime.stop_policy.refuse_before_configure.assert_called_once_with(
            self.runtime.write_json, self.state, self.path
        )

    def test_native_preload_errors_keep_the_original_runtime_handler(self):
        self.load.side_effect = RuntimeError("native setup refusal")
        native = SimpleNamespace(require_supported_action=Mock())
        self.assertEqual(2, self.run_locked(native=native))
        self.assertEqual("PAUSED_INVALID_OUTPUT", util.read(self.path)["status"])
        native.require_supported_action.assert_not_called()

    def test_native_preload_admission_is_an_actionless_refused_attempt(self):
        self.load.side_effect = admission_failure()
        native = SimpleNamespace(require_supported_action=Mock())
        self.assertEqual(2, self.run_locked(native=native))
        saved = util.read(self.path)
        self.assertEqual("PAUSED_METADATA_ADMISSION", saved["status"])
        public = run_view.view(saved, workspace=self.workspace, run_dir=self.run)
        self.assertIsNone(public["needs"])
        self.assertIsNone(public["next_command"])
        self.assertEqual("blocked", public["recovery"]["category"])
        native.require_supported_action.assert_not_called()
        self.owner_factory.assert_not_called()
        self.runtime.pause_command.recovery.assert_not_called()
        self.runtime.resolver_runtime.record_operational_exhaustion.assert_not_called()

    def test_ordinary_errors_after_preload_still_use_the_runtime_pause_handler(self):
        self.runtime.build_loop.run.side_effect = RuntimeError("runtime failure")
        self.assertEqual(2, self.run_locked())
        self.assertEqual("PAUSED_INVALID_OUTPUT", util.read(self.path)["status"])
        self.owner_factory.assert_called_once()


if __name__ == "__main__":
    unittest.main()
