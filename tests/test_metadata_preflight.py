"""Metadata ownership is shared across providers, fallback policy and clients.

The command seam is replaced before any adapter runs. These tests launch no
backend, model, credential check, sandbox or installation command.
"""

import json
import os
import subprocess
import tempfile
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_captured_process as captured
import autocode_chat_client as chat
import autocode_configure as configure
import autocode_connected_engine as connected
import autocode_connections as connections
import autocode_doctor as doctor
import autocode_route_discovery as discovery
import autocode_setup as setup
import autocode_stage_choices as stages
import autocode_support as support
import autocode_tool_admission as admission
import autocode_tool_containment as containment
import model_catalogue as catalogue
import provider_conformance_transport as conformance
from providers import env_prep, kilocode_policy
from providers.kilocode import KiloProvider

from tests import test_model_catalogue as catalogue_fixture


class MetadataCaptureTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        (self.root / "run").mkdir()
        self.command = ["metadata-tool", "models"]
        self.tool = self.root / self.command[0]
        self.tool.write_text("#!/bin/sh\nexit 0\n")
        self.tool.chmod(0o755)
        self.env = {"PATH": str(self.root), "HOME": str(self.root)}

    def test_preflight_reuses_only_a_completed_owned_answer(self):
        expected = subprocess.CompletedProcess(self.command, 0, "provider/model\n", "")
        with (
            env_prep.reuse_checks(self.root / "run"),
            patch.object(captured, "run_metadata", return_value=expected) as run,
        ):
            kwargs = dict(cwd=self.root, capture_output=True, text=True, timeout=30, require_executable=True)
            self.assertIs(expected, env_prep.preflight_run(self.command, self.env, **kwargs))
            kept = env_prep.preflight_run(self.command, self.env, **kwargs)
            self.assertIsInstance(kept, env_prep.KeptCheck)
            self.assertEqual(expected.stdout, kept.stdout)
        run.assert_called_once_with(
            self.command, env=self.env, cwd=self.root, capture_output=True, text=True, timeout=30
        )

    def test_unconfirmed_cleanup_is_neither_retried_completed_nor_kept(self):
        error = captured.ProcessError("fixture cleanup remains unknown")
        complete = Mock(return_value=True)
        with (
            env_prep.reuse_checks(self.root / "run"),
            patch.object(captured, "run_metadata", side_effect=error) as run,
            self.assertRaises(captured.ProcessError) as raised,
        ):
            env_prep.preflight_listing(
                self.command,
                self.env,
                timeout=30,
                cwd=self.root,
                capture_output=True,
                text=True,
                complete=complete,
            )
        self.assertIs(error, raised.exception)
        run.assert_called_once()
        complete.assert_not_called()
        self.assertEqual([], list(self.root.rglob("preflight-reuse/*.json")))

    def test_a_legacy_kept_answer_is_replaced_by_an_owned_check(self):
        kwargs = dict(cwd=self.root, capture_output=True, text=True, timeout=30)
        expected = subprocess.CompletedProcess(self.command, 0, "owned answer", "")
        with (
            env_prep.reuse_checks(self.root / "run"),
            patch.object(captured, "run_metadata", return_value=expected) as run,
        ):
            env_prep.preflight_run(self.command, self.env, **kwargs)
            [file] = (self.root / "run" / env_prep.REUSE_FOLDER).glob("*.json")
            legacy = json.loads(file.read_text())
            legacy.pop("ownership_schema")
            legacy["stdout"] = "legacy raw answer"
            file.write_text(json.dumps(legacy))
            self.assertEqual("owned answer", env_prep.preflight_run(self.command, self.env, **kwargs).stdout)
            self.assertIsInstance(env_prep.preflight_run(self.command, self.env, **kwargs), env_prep.KeptCheck)
        self.assertEqual(2, run.call_count)
        self.assertEqual(env_prep.OWNED_CHECK_SCHEMA, json.loads(file.read_text())["ownership_schema"])

    @unittest.skipUnless(os.name == "posix", "Linux metadata filesystem and locks")
    def test_uncertain_prior_attempt_blocks_kept_answers_and_a_different_child_environment(self):
        custody = captured.custody
        expected = subprocess.CompletedProcess(self.command, 0, "provider/model\n", "")
        kwargs = dict(cwd=self.root, capture_output=True, text=True, timeout=30, require_executable=True)
        with (
            patch.dict(os.environ, {"HOME": str(self.root), "XDG_STATE_HOME": str(self.root / "state")}),
            patch.object(custody.sys, "platform", "linux"),
            patch.object(custody, "_boot_id", return_value="00000000-0000-4000-8000-000000000001"),
            env_prep.reuse_checks(self.root / "run"),
            patch.object(captured, "run_metadata", return_value=expected) as run,
        ):
            env_prep.preflight_run(self.command, self.env, **kwargs)
            self.assertIsInstance(env_prep.preflight_run(self.command, self.env, **kwargs), env_prep.KeptCheck)
            with self.assertRaises(captured.ProcessError), custody.admission(timeout=1) as scope:
                scope.arm()  # Simulates a launched helper lost without its terminal response.
            for child_env in (self.env, {"PATH": "", "HOME": str(self.root / "different-home")}):
                with self.subTest(child_env=child_env), self.assertRaises(captured.ProcessError):
                    env_prep.preflight_run(self.command, child_env, **kwargs)
            self.assertEqual(1, run.call_count, "neither a cached answer nor another backend call may pass")

    @unittest.skipUnless(os.name == "posix", "Linux metadata filesystem and locks")
    def test_uncertain_prior_attempt_also_blocks_launch_local_policy_cache(self):
        custody = captured.custody
        cache = {}
        argv = [str(self.tool), "run", "--agent", "stage"]
        expected = subprocess.CompletedProcess([], 0, '{"permission": []}', "")
        with (
            patch.dict(os.environ, {"HOME": str(self.root), "XDG_STATE_HOME": str(self.root / "state")}),
            patch.object(custody.sys, "platform", "linux"),
            patch.object(custody, "_boot_id", return_value="00000000-0000-4000-8000-000000000001"),
            patch.object(captured, "run_metadata", return_value=expected) as run,
        ):
            kilocode_policy.effective_agent(argv, self.env, self.root, cache=cache)
            self.assertEqual(1, len(cache))
            with self.assertRaises(captured.ProcessError), custody.admission(timeout=1) as scope:
                scope.arm()
            with self.assertRaises(captured.ProcessError):
                kilocode_policy.effective_agent(argv, self.env, self.root, cache=cache)
            self.assertEqual(1, run.call_count, "a kept policy must not bypass unresolved metadata ownership")

    @unittest.skipUnless(os.name == "posix", "Linux metadata filesystem and locks")
    def test_policy_workers_share_admission_and_resolve_both_reads_concurrently(self):
        custody = captured.custody
        together = threading.Barrier(2, timeout=2)

        def metadata(words, **kwargs):
            # Exercise the real admission contract below the injected backend.
            # An unrelated worker cannot acquire the coordinator's lock.
            with custody.admission(timeout=0):
                together.wait()
                value = {"default_agent": "code"} if words[-1] == "config" else {"permission": []}
                return subprocess.CompletedProcess(words, 0, json.dumps(value), "")

        with (
            patch.dict(os.environ, {"HOME": str(self.root), "XDG_STATE_HOME": str(self.root / "state")}),
            patch.object(custody.sys, "platform", "linux"),
            patch.object(custody, "_boot_id", return_value="00000000-0000-4000-8000-000000000001"),
            patch.object(captured, "run_metadata", side_effect=metadata) as run,
        ):
            _, policy = kilocode_policy.effective_agent([str(self.tool), "run"], self.env, self.root)
            self.assertEqual({"permission": []}, policy)
            self.assertEqual(2, run.call_count)
            self.assertIsNone(custody.status())

    def test_metadata_bypasses_use_the_same_owned_runner_and_bounds(self):
        expected = subprocess.CompletedProcess([], 0, "using ChatGPT\n", "")
        with (
            patch.dict(os.environ, {"CODEX_HOME": str(self.root)}, clear=False),
            patch.object(captured, "run_metadata", return_value=expected) as run,
            patch.object(subprocess, "run", side_effect=AssertionError("unowned metadata spawn")),
        ):
            self.assertEqual("ChatGPT", support.local_settings()["auth_mode"])
            run.assert_called_with(["codex", "login", "status"], timeout=env_prep.CONNECTION_TIMEOUT_SECONDS)
            self.assertEqual(["using ChatGPT"], catalogue.list_catalogue(self.root, self.command))
            run.assert_called_with(self.command, cwd=str(self.root), capture_output=True, text=True, timeout=180)
            self.assertIs(expected, doctor.run(self.command, self.root))
            run.assert_called_with(self.command, cwd=self.root, timeout=20)
            self.assertEqual("using ChatGPT/bin", setup._npm_bin())
            run.assert_called_with(
                ["npm", "prefix", "-g"], capture_output=True, text=True, stdin=subprocess.DEVNULL, timeout=30
            )

    def test_native_version_and_debug_checks_share_capture(self):
        expected = subprocess.CompletedProcess([], 0, containment.SUPPORTED_VERSION, "")
        with patch.object(captured, "run_metadata", return_value=expected) as run:
            self.assertEqual(containment.SUPPORTED_VERSION, containment.backend_version(containment.BUBBLEWRAP))
            run.assert_called_with([containment.BWRAP, "--version"], capture_output=True, text=True, timeout=15)
            with (
                patch.object(containment, "sandbox_problem", return_value=None),
                patch.object(containment.shutil, "which", return_value="/owned/opencode"),
            ):
                self.assertIsNone(containment.unavailable(self.root, self.env))
                run.assert_called_with(
                    ["/owned/opencode", "--version"],
                    env=self.env,
                    cwd=self.root,
                    capture_output=True,
                    text=True,
                    timeout=15,
                )
            expected.stdout = '{"permission": []}'
            for supervised in (False, True):
                with self.subTest(supervised=supervised):
                    self.assertEqual(
                        {"permission": []},
                        containment._native_debug(
                            ["tool", "debug", "agent", "code"], self.root, self.env, supervised=supervised
                        )(),
                    )
                    self.assertEqual(60, run.call_args.kwargs["timeout"])

    def test_native_agent_resolution_does_not_cache_private_config(self):
        expected = subprocess.CompletedProcess([], 0, '{"permission": []}', "")
        with (
            patch.object(kilocode_policy.shutil, "which", return_value=str(self.tool)),
            patch.object(captured, "run_metadata", return_value=expected) as run,
            patch.object(env_prep, "preflight_run", side_effect=AssertionError("private policy entered reuse")),
        ):
            argv, policy = kilocode_policy.effective_agent(["kilo", "run", "--agent", "review"], self.env, self.root)
        self.assertEqual("review", argv[-1])
        self.assertEqual({"permission": []}, policy)
        run.assert_called_once_with(
            [str(self.tool), "debug", "agent", "review"],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=run.call_args.kwargs["timeout"],
        )

    def test_codex_conformance_metadata_preserves_check_and_explicit_environment(self):
        def answer(command, **kwargs):
            return subprocess.CompletedProcess(
                command, 0, "codex-cli fixture" if command[-1] == "--version" else "using ChatGPT", ""
            )

        with (
            patch.object(env_prep, "resolve_executable", return_value=str(self.tool)),
            patch.object(captured, "run_metadata", side_effect=answer) as run,
        ):
            result = conformance.preflight("codex", "fixture/model", None, self.root, self.env, fake=True)
        self.assertEqual("ChatGPT", result["auth_mode"])
        self.assertEqual(2, run.call_count)
        self.assertIs(True, run.call_args_list[0].kwargs["check"])
        for call in run.call_args_list:
            self.assertEqual(
                (self.env, self.root, 15), (call.kwargs["env"], call.kwargs["cwd"], call.kwargs["timeout"])
            )

    def test_missing_process_dependency_is_a_doctor_failure_without_a_raw_fallback(self):
        missing = ModuleNotFoundError("fixture psutil absent", name="psutil")
        with (
            patch.object(captured, "run_metadata", side_effect=missing),
            patch.object(subprocess, "run", side_effect=AssertionError("unsafe fallback")),
        ):
            result = doctor.run(self.command)
        self.assertEqual(127, result.returncode)
        self.assertIn("psutil", result.stderr)

    def test_codex_metadata_timeout_does_not_disclose_captured_auth_output(self):
        with (
            patch.dict(os.environ, {"CODEX_HOME": str(self.root)}),
            patch.object(
                captured, "run_metadata", side_effect=subprocess.TimeoutExpired("fixture", 45, output="SECRET")
            ),
            self.assertRaisesRegex(RuntimeError, "Codex login check timed out") as raised,
        ):
            support.local_settings()
        self.assertNotIn("SECRET", str(raised.exception))


class MetadataUncertaintyTests(unittest.TestCase):
    def setUp(self):
        self.error = captured.ProcessError("fixture ownership uncertain")

    def test_optional_levels_and_automatic_selection_do_not_hide_uncertainty(self):
        with patch.object(discovery, "discover", side_effect=self.error):
            with self.assertRaises(captured.ProcessError):
                configure._listed_levels(SimpleNamespace(), ".", {"fixture/model"})
            with self.assertRaises(captured.ProcessError):
                configure._auto_select(
                    SimpleNamespace(auto_select=None), {}, SimpleNamespace(), False, {}, {}, "opencode", "."
                )
        with patch.object(discovery, "_run", side_effect=self.error), self.assertRaises(captured.ProcessError):
            discovery._listing(discovery.opencode, ".", {})

    def test_catalogue_cannot_keep_defaults_or_profile_after_uncertainty(self):
        provider = catalogue_fixture.FakeProvider(self.error)
        with self.assertRaises(captured.ProcessError):
            catalogue.choose(catalogue_fixture.new_settings(), provider, Path("."), interactive=False)
        with patch.object(discovery, "discover", side_effect=self.error), self.assertRaises(captured.ProcessError):
            catalogue.route_advice(catalogue_fixture.kilo_provider(), catalogue_fixture.SELECTED)

    def test_backend_selection_stops_instead_of_trying_another_backend(self):
        runner = Mock(side_effect=self.error)
        with self.assertRaises(captured.ProcessError):
            connected.choose(which=lambda name: name if name in ("claude", "codex") else None, runner=runner)
        self.assertEqual(1, runner.call_count)

    def test_kilo_selection_reads_only_the_injected_login_listing(self):
        table = {
            "name": "fixture",
            "command": ["fixture", "auth", "list"],
            "timeout_seconds": 30,
            "routes": [{"name": "Fixture", "pattern": r"Fixture (oauth)", "expect": "oauth"}],
        }
        provider = SimpleNamespace(auth_table=lambda: table)
        runner = Mock(return_value=subprocess.CompletedProcess([], 0, "Fixture oauth", ""))
        with patch.object(env_prep, "preflight_run", side_effect=AssertionError("host metadata ran")):
            result = connected.choose(
                which=lambda name: "fixture" if name == "kilo" else None,
                runner=runner,
                resolve=lambda name: provider,
                workspace="fixture-workspace",
            )
        self.assertEqual("kilocode", result["id"])
        runner.assert_called_once_with(table["command"], cwd="fixture-workspace")

    def test_stage_choices_cannot_apply_saved_or_default_routes_after_uncertainty(self):
        with patch.object(stages, "offers", side_effect=self.error):
            with self.assertRaises(captured.ProcessError):
                stages.view("codex")
            with self.assertRaises(captured.ProcessError):
                stages.run_view("codex", {}, lambda role: [])

    def test_early_tool_admission_keeps_cleanup_uncertainty_distinct_from_boundary_policy(self):
        kilo = object.__new__(KiloProvider)
        kilo.check_tools = Mock(side_effect=self.error)
        with (
            patch.object(admission.role_transport, "groups", return_value=[(kilo, {}, None)]),
            self.assertRaises(captured.ProcessError) as raised,
        ):
            admission.selected_tools(None, {}, Path("."), Path("run"))
        self.assertIs(self.error, raised.exception)
        with (
            patch.object(admission.sys, "platform", "linux"),
            patch.object(
                admission, "shell_policy", return_value=SimpleNamespace(kernel_shell=True, planner_shell=True)
            ),
            patch.object(admission.toolchain, "discover", side_effect=self.error),
            self.assertRaises(captured.ProcessError) as raised,
        ):
            admission.requirements(None, {}, Path("."))
        self.assertIs(self.error, raised.exception)

    def test_doctor_stops_instead_of_probing_after_uncertain_backend_metadata(self):
        provider = SimpleNamespace(
            local_settings=Mock(side_effect=self.error), available_models=Mock(side_effect=self.error)
        )
        with self.assertRaises(captured.ProcessError):
            doctor.provider_check("fixture", resolve=lambda name: provider)
        with self.assertRaises(captured.ProcessError):
            doctor.route_check("fixture", None, resolve=lambda name: provider)

    def test_batch_connection_checks_stop_but_single_public_check_reports_failure(self):
        with (
            patch.object(connections, "_definition", return_value=("Fixture", "fixture", None, ["fixture"], None)),
            patch.object(connections.shutil, "which", return_value="/owned/fixture"),
            patch.object(captured, "run_metadata", side_effect=self.error) as run,
        ):
            with self.assertRaises(captured.ProcessError):
                setup.rows()
            self.assertEqual(1, run.call_count)

    def test_terminal_connection_check_retains_its_full_allowance_and_owned_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            history = chat.History(Path(directory), command=("fixture-core",), env={"PATH": directory})
            words = ["connections", "check", "--provider", "fixture", "--json"]
            result = subprocess.CompletedProcess([], 0, json.dumps({"id": "fixture"}), "")
            with patch.object(captured, "run", return_value=result) as run:
                self.assertEqual({"id": "fixture"}, history.read(words))
                self.assertEqual(60, run.call_args.kwargs["timeout"])
                self.assertEqual(["fixture-core", *words], run.call_args.args[0])
                history.read(["modes", "list", "--json"])
                self.assertEqual(chat.polling.READ_TIMEOUT, run.call_args.kwargs["timeout"])
            with patch.object(captured, "run", side_effect=self.error), self.assertRaises(captured.ProcessError):
                history.read(words)


if __name__ == "__main__":
    unittest.main()
