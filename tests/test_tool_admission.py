"""Early selected-tool discovery preserves the actual launch's shell scope and environment."""

import os
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
