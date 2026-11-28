"""Readiness checks the saved execution routes, not just billing metadata."""

import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_access_evidence as access
import autocode_model_preflight as preflight
import autocode_util as util
import provider_conformance_transport as transport

from . import test_model_preflight


class BuildConnectivityTests(unittest.TestCase):
    setUp = test_model_preflight.PreflightCheckTests.setUp
    fake_execute = test_model_preflight.PreflightCheckTests.fake_execute

    def check(self, events):
        self.state["next_stage"] = "builder"
        with patch.object(preflight.transport, "execute", side_effect=self.fake_execute(events)) as execute:
            result = preflight.check(
                types.SimpleNamespace(opencode=self.adapter),
                self.state,
                self.run_dir,
                self.root,
                before_build=True,
            )
        return result, execute

    def test_non_billing_provider_is_probed_and_bound_receipt_is_reused(self):
        self.adapter._config = {}
        self.assertTrue(
            self.check({"plan/glm": test_model_preflight.OK_EVENTS, "plan/mimo": test_model_preflight.OK_EVENTS})[0]
        )
        self.assertEqual(["plan/mimo", "plan/glm"], self.calls)
        self.assertFalse(self.check({})[0])

    def test_transient_transport_failure_blocks_builder_and_keeps_attempt(self):
        with self.assertRaises(util.Paused) as stopped:
            self.check({"plan/mimo": test_model_preflight.GARBLED_EVENTS})
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", stopped.exception.status)
        self.assertEqual(["plan/mimo"], self.calls)
        self.assertTrue(self.state["stages"][0]["accounted"])
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", self.state["stages"][0]["preflight_outcome"]["status"])

    def test_partial_provider_stream_cannot_certify_build_readiness(self):
        with self.assertRaises(util.Paused) as stopped:
            self.check({"plan/mimo": [{"type": "step_start"}]})
        self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", stopped.exception.status)

    def test_completed_native_stage_proves_connectivity_not_another_models_effort(self):
        settings = self.state["settings"]
        settings.update(engine="codex", transport_identities={"codex": {**self.identity, "engine": "codex"}})
        binding = access.launch_binding(settings, "task_planner", None, "plan/glm", "high")
        self.assertIsNotNone(binding)
        events, output = self.run_dir / "planner.events", self.run_dir / "planner.output"
        events.write_text('{"type":"turn.completed"}\n')
        output.write_text("{}")
        self.state["stages"] = [
            {
                "engine": "codex",
                "access_preflight_identity": binding,
                "model": "plan/glm",
                "launch_route": {"model": "plan/glm", "reasoning_effort": "high"},
                "reasoning_effort": "high",
                "accounted": True,
                "exit_code": 0,
                "finished_at": "2026-11-28T00:00:00Z",
                "events": str(events),
                "output": str(output),
            }
        ]
        self.assertFalse(self.check({})[0])
        self.assertNotIn("plan/mimo", settings.get(preflight.route_fit.CERTIFIED, {}))
        self.state["stages"][0]["rejected"] = True
        self.assertTrue(
            self.check({"plan/glm": test_model_preflight.OK_EVENTS, "plan/mimo": test_model_preflight.OK_EVENTS})[0]
        )

    def test_native_codex_uses_saved_provider_and_login_instead_of_conformance_defaults(self):
        self.state["settings"].update(
            engine="codex",
            provider="saved-endpoint",
            joint_planning=False,
            transport_identities={"codex": {**self.identity, "engine": "codex"}},
        )
        result, execute = self.check(
            {"plan/glm": test_model_preflight.OK_EVENTS, "plan/mimo": test_model_preflight.OK_EVENTS}
        )
        self.assertTrue(result)
        self.assertEqual(
            {"provider": "saved-endpoint", "chatgpt": False, "arguments": []},
            execute.call_args.kwargs["native_options"],
        )
        self.assertIsNone(execute.call_args.kwargs["adapter"])
        self.assertEqual({"codex"}, {row["engine"] for row in self.state["stages"]})

    def test_codex_launch_options_are_used_by_the_actual_probe_transport(self):
        captured = {}

        def resolve(name, env, **kwargs):
            return sys.executable

        def child(argv, *args, **kwargs):
            captured["argv"] = argv
            raise RuntimeError("stop before provider traffic")

        with (
            patch.object(transport.env_prep, "resolve_executable", side_effect=resolve),
            patch.object(transport, "_provider_child", side_effect=child),
        ):
            with self.assertRaisesRegex(RuntimeError, "stop before provider traffic"):
                transport.execute(
                    adapter=None,
                    workspace=self.root,
                    directory=self.root / "native",
                    model="saved-model",
                    effort=None,
                    session=None,
                    allow_write=False,
                    prompt="probe",
                    schema=preflight.SCHEMA,
                    env={},
                    timeout=5,
                    native_options={"provider": "saved-endpoint", "chatgpt": False, "arguments": []},
                )
        self.assertIn('model_provider="saved-endpoint"', captured["argv"])
        self.assertFalse(
            any("forced_login_method" in word or "model_reasoning_effort" in word for word in captured["argv"])
        )
        self.assertIn("read-only", captured["argv"])

    def test_scripted_codex_answers_readiness_without_building_any_source(self):
        schema = self.root / "schema.json"
        output = self.root / "output.json"
        util.atomic_json(schema, preflight.SCHEMA)
        result = subprocess.run(
            [
                sys.executable,
                str(Path(__file__).resolve().parents[1] / "tools/fake_codex.py"),
                "exec",
                "--output-schema",
                str(schema),
                "-o",
                str(output),
            ],
            input=preflight.PROMPT,
            text=True,
            capture_output=True,
            cwd=self.root,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual({"ok": True}, util.read_object(output))
        self.assertIn('"turn.completed"', result.stdout)
        self.assertFalse((self.root / "greet.py").exists())
