"""Early selected-tool discovery preserves the actual launch's shell scope and environment."""

import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_build_loop as build_loop
import autocode_provider_launch as launch
import autocode_tool_admission as admission
from providers.kilocode import KiloProvider


class RequirementsToolAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.kilo = object.__new__(KiloProvider)
        self.state = {"settings": {"engine": "opencode", "joint_planning": True}}

    def test_non_kernel_requirements_do_not_discover_or_launch_anything(self):
        cases = (
            ("portable Kilo", self.kilo, {}, "darwin"),
            ("generic configured tool", SimpleNamespace(CONFIGURED=True), {}, "linux"),
            ("Codex", self.kilo, {"engine": "codex"}, "linux"),
            ("accepted uncontained", self.kilo, {"allow_uncontained_tools": True}, "linux"),
            ("recorded replay", self.kilo, {"replay_recording": {"path": "fixture"}}, "linux"),
            ("nonjoint run", self.kilo, {"joint_planning": False}, "linux"),
        )
        for name, adapter, changes, platform in cases:
            state = deepcopy(self.state)
            state["settings"].update(changes)
            with (
                self.subTest(name=name),
                patch.object(admission.sys, "platform", platform),
                patch.object(admission.toolchain, "discover") as discover,
            ):
                admission.requirements(adapter, state, Path("/not-used"))
                discover.assert_not_called()

    def test_early_discovery_and_actual_launch_share_exact_scrubbed_environment(self):
        environment = {
            "PATH": ".venv/bin:/usr/bin:/bin",
            "HOME": "/fixture/home",
            "FIXTURE_API_KEY": "withheld",
            "AUTOCODE_VERIFICATION_COPY": "another stage",
            "AUTOCODE_VERIFICATION_COPY_SHA256": "stale authority",
        }
        self.kilo.NAME = "kilocode"
        seen = []

        def provider(*_args, **kwargs):
            seen.append(kwargs)
            return ["/fixture/kilo"], kwargs["env"], {}

        self.kilo.launch = provider
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(admission.sys, "platform", "linux"),
            patch.dict(os.environ, environment, clear=True),
            patch.object(admission.toolchain, "discover") as discover,
        ):
            workspace = Path(directory)
            admission.requirements(self.kilo, self.state, workspace)
            launch.prepare(
                engine="opencode",
                adapter=self.kilo,
                role="requirements",
                route_role="requirements",
                workspace=workspace,
                run_dir=workspace / "run",
                session=None,
                model="fixture/model",
                effort="high",
                allow_write=False,
                planning=True,
                report=None,
                schema=None,
                prompt_file=None,
                sandbox="read-only",
                transport_args=[],
                chatgpt=True,
                provider="kilocode",
                settings=self.state["settings"],
            )
            discover.assert_called_once_with(workspace, (), seen[0]["env"])
            self.assertEqual([], seen[0]["containment"]["tool_commands"])
            self.assertEqual({"PATH": environment["PATH"], "HOME": environment["HOME"]}, seen[0]["env"])
            self.assertEqual(environment, dict(os.environ), "Preparation must not mutate the caller mapping")


class SelectedToolsAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.kilo = object.__new__(KiloProvider)
        self.kilo.NAME = "kilocode"
        self.kilo.check_tools = Mock()
        self.routes = {"tester": {"engine": "opencode", "provider": "kilocode", "model": "fixture/tester"}}
        self.identity = {"engine": "kilocode", "config_sha256": "a" * 64}
        self.state = {"settings": {"engine": "opencode", "output_transport": {"mode": "conservative"}}}

    def test_a_separately_routed_kilo_tester_is_checked_before_any_stage_spends(self):
        before = deepcopy(self.state)
        fallback = SimpleNamespace(NAME="fixture_codex")
        with patch.object(admission.role_transport, "groups", return_value=[(self.kilo, self.routes, self.identity)]):
            admission.selected_tools(fallback, self.state, Path("/project"), Path("/run"))
        self.kilo.check_tools.assert_called_once()
        args, kwargs = self.kilo.check_tools.call_args
        self.assertEqual((self.routes, Path("/project"), Path("/run")), args)
        self.assertTrue(kwargs["capture_command"].endswith("autocode.py capture --mode conservative"))
        self.assertNotIn("AUTOCODE_VERIFICATION_COPY", kwargs["env"])
        self.assertEqual(before, self.state, "Readiness cannot change routes, caps, or task state")

    def test_identical_kilo_bindings_share_one_model_free_policy_read(self):
        completion = {"completion": {"engine": "opencode", "provider": "kilocode", "model": "fixture/completion"}}
        groups = [(self.kilo, self.routes, self.identity), (self.kilo, completion, dict(self.identity))]
        with patch.object(admission.role_transport, "groups", return_value=groups):
            admission.selected_tools(None, self.state, Path("/project"), Path("/run"))
        self.kilo.check_tools.assert_called_once()
        self.assertEqual(self.routes | completion, self.kilo.check_tools.call_args.args[0])
        self.assertEqual(["tester"], list(self.routes), "The grouped saved route mapping stays untouched")

    def test_missing_capability_is_a_setup_stop_without_an_uncontained_retry_hint(self):
        self.kilo.check_tools.side_effect = RuntimeError("Configured Kilo agent denies the capture command")
        before = deepcopy(self.state)
        with (
            patch.object(admission.role_transport, "groups", return_value=[(self.kilo, self.routes, self.identity)]),
            self.assertRaises(admission.util.Paused) as raised,
        ):
            admission.selected_tools(None, self.state, Path("/project"), Path("/run"))
        self.assertEqual("PAUSED_TOOL_CONTAINMENT", raised.exception.status)
        self.assertIn("denies the capture command", str(raised.exception))
        self.assertNotIn("--allow-uncontained-tools", str(raised.exception))
        self.assertEqual(before, self.state)

    def test_non_kilo_transports_and_recorded_replays_do_not_run_a_kilo_check(self):
        with patch.object(
            admission.role_transport, "groups", return_value=[(SimpleNamespace(NAME="another"), self.routes, None)]
        ):
            admission.selected_tools(None, self.state, Path("/project"), Path("/run"))
        self.kilo.check_tools.assert_not_called()
        self.state["settings"]["replay_recording"] = {"path": "recording"}
        with patch.object(admission.role_transport, "groups") as groups:
            admission.selected_tools(None, self.state, Path("/project"), Path("/run"))
        groups.assert_not_called()

    def test_native_owned_dispatch_never_resolves_an_ordinary_agent_before_its_owner(self):
        class StopBeforeDispatch(Exception):
            code = 0

        runner = SimpleNamespace(
            opencode=self.kilo,
            native_calls=object(),
            orchestrator=SimpleNamespace(LoopExit=StopBeforeDispatch),
        )
        with (
            patch.object(
                admission.role_transport, "groups", side_effect=AssertionError("ordinary agent inspection")
            ) as groups,
            patch.object(build_loop.model_preflight, "check", side_effect=StopBeforeDispatch),
        ):
            result = build_loop.run(
                runner, SimpleNamespace(), self.state, Path("/state"), Path("/run"), Path("/project")
            )
        self.assertEqual(0, result)
        groups.assert_not_called()
        self.kilo.check_tools.assert_not_called()
