"""Launch probes each billing-routed role model once per run, before stage spend."""

import contextlib
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_model_preflight as preflight
import autocode_session_boundary as boundary
import autocode_supervision as supervision
import autocode_support as support

PAYWALL_EVENTS = [
    {
        "type": "error",
        "sessionID": "ses_probe",
        "error": {
            "name": "APIError",
            "data": {
                "message": "Add credits to continue, or switch to a free model",
                "statusCode": 402,
                "responseBody": '{"error":{"title":"Paid Model - Credits Required","error_type":"usage_limit_exceeded"}}',
            },
        },
    }
]
GARBLED_EVENTS = [{"type": "turn.failed", "error": {"message": "connection reset by peer"}}]
OK_EVENTS = [
    {"type": "step_start", "sessionID": "ses_probe", "part": {"id": "p1", "sessionID": "ses_probe"}},
    {"type": "step_finish", "sessionID": "ses_probe", "part": {"id": "p1", "sessionID": "ses_probe", "reason": "stop"}},
]


class BillingAwareTests(unittest.TestCase):
    def test_only_auth_declaring_configs_are_probed(self):
        self.assertTrue(preflight.billing_aware(types.SimpleNamespace(_config={"auth": {"command": ["x"]}})))
        self.assertFalse(preflight.billing_aware(types.SimpleNamespace(_config={"command": ["x"]})))
        self.assertFalse(preflight.billing_aware(types.SimpleNamespace()))
        self.assertFalse(preflight.billing_aware(None))

    def test_distinct_models_keep_declaration_order(self):
        settings = {
            "roles": {
                "astra": {"model": "plan/glm"},
                "terra": {"model": "plan/mimo"},
                "sol": {"model": "plan/glm"},
                "completion": {"model": ""},
            }
        }
        self.assertEqual(["plan/glm", "plan/mimo"], preflight.distinct_models(settings))


class PreflightCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.adapter = types.SimpleNamespace(
            _config={"auth": {"command": ["kilo", "auth", "list"], "routes": []}},
            check_subscription_routes=lambda roles, workspace=None, env=None: None,
        )
        self.state = {
            "settings": {
                "roles": {"astra": {"model": "plan/glm"}, "terra": {"model": "plan/mimo"}, "sol": {"model": "plan/glm"}}
            }
        }
        self.calls = []

    def fake_execute(self, events_by_model):
        def execute(**kwargs):
            model = kwargs["model"]
            self.calls.append(model)
            kwargs["directory"].mkdir(parents=True, exist_ok=True)
            rows = events_by_model[model]
            (kwargs["directory"] / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
            return {
                "exit_code": 0,
                "timed_out": False,
                "report": None,
                "rows": rows,
                "report_error": None,
                "metrics": {},
            }

        return execute

    def check(self, events_by_model, runner=None):
        runner = runner if runner is not None else types.SimpleNamespace(opencode=self.adapter)
        with patch.object(preflight.transport, "execute", side_effect=self.fake_execute(events_by_model)):
            return preflight.check(runner, self.state, self.run_dir, self.root)

    def test_each_distinct_model_is_probed_once_per_run(self):
        events = {"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}
        self.assertTrue(self.check(events))
        self.assertEqual(["plan/glm", "plan/mimo"], self.calls)
        self.assertFalse(self.check(events))
        self.assertEqual(["plan/glm", "plan/mimo"], self.calls)
        saved = json.loads((self.run_dir / "model-preflight.json").read_text())
        self.assertEqual({"plan/glm", "plan/mimo"}, set(saved["probed"]))
        self.assertEqual("ok", saved["probed"]["plan/mimo"]["status"])

    def test_non_billing_adapters_make_no_probe_calls(self):
        runner = types.SimpleNamespace(opencode=types.SimpleNamespace(_config={"command": ["fake"]}))
        self.assertFalse(self.check({"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}, runner=runner))
        self.assertEqual([], self.calls)
        self.assertFalse((self.run_dir / "model-preflight.json").exists())

    def test_paywalled_model_refuses_the_build_and_is_recorded(self):
        events = {"plan/glm": OK_EVENTS, "plan/mimo": PAYWALL_EVENTS}
        with self.assertRaises(support.Paused) as caught:
            self.check(events)
        self.assertEqual("PAUSED_BUDGET", caught.exception.status)
        self.assertIn("plan/mimo", str(caught.exception))
        saved = json.loads((self.run_dir / "model-preflight.json").read_text())
        self.assertTrue(saved["probed"]["plan/mimo"]["blocking"])
        self.assertIn("Add credits", saved["probed"]["plan/mimo"]["detail"])

    def test_resume_reprobes_a_route_that_failed_until_it_passes(self):
        with self.assertRaises(support.Paused):
            self.check({"plan/glm": OK_EVENTS, "plan/mimo": PAYWALL_EVENTS})
        with self.assertRaises(support.Paused):
            self.check({"plan/glm": OK_EVENTS, "plan/mimo": PAYWALL_EVENTS})
        self.assertEqual(["plan/glm", "plan/mimo", "plan/mimo"], self.calls)
        self.assertTrue(self.check({"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}))
        self.assertEqual(["plan/glm", "plan/mimo", "plan/mimo", "plan/mimo"], self.calls)
        saved = json.loads((self.run_dir / "model-preflight.json").read_text())
        self.assertEqual("ok", saved["probed"]["plan/mimo"]["status"])

    def test_failed_auth_listing_stops_before_any_probe_call(self):
        def refuse(roles, workspace=None, env=None):
            raise RuntimeError("cannot verify fixture login; auth listing failed")

        self.adapter.check_subscription_routes = refuse
        with self.assertRaises(support.Paused) as caught:
            self.check({"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS})
        self.assertEqual("PAUSED_BILLING_ROUTE", caught.exception.status)
        self.assertIn("auth listing failed", str(caught.exception))
        self.assertEqual([], self.calls)
        self.assertFalse((self.run_dir / "model-preflight.json").exists())

    def test_transient_probe_noise_is_reported_and_the_run_continues(self):
        events = {"plan/glm": GARBLED_EVENTS, "plan/mimo": OK_EVENTS}
        self.assertTrue(self.check(events))
        self.assertEqual(["plan/glm", "plan/mimo"], self.calls)
        saved = json.loads((self.run_dir / "model-preflight.json").read_text())
        self.assertFalse(saved["probed"]["plan/glm"]["blocking"])


class OwnedPreflightTests(unittest.TestCase):
    """Exercise real probe formatting/admission with no subprocess or model."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.model = "plan/glm"
        self.state = {"settings": {"roles": {"astra": {"model": self.model}}}}
        self.adapter = types.SimpleNamespace(
            _config={"auth": {"command": ["fixture", "auth", "list"]}},
            check_subscription_routes=lambda roles, workspace=None, env=None: None,
            launch=lambda *args, **kwargs: ([sys.executable, "-c", "pass"], kwargs["env"], {}),
            final_report=lambda *args, **kwargs: {"ok": True},
        )
        self.runner = types.SimpleNamespace(opencode=self.adapter)
        self.admissions = []
        self.stack = contextlib.ExitStack()
        self.addCleanup(self.stack.close)
        self.stack.enter_context(patch.dict(preflight.os.environ, {"PATH": "/usr/bin:/bin"}, clear=True))
        self.stack.enter_context(
            patch.object(preflight.transport.env_prep, "resolve_executable", return_value=sys.executable)
        )
        self.stack.enter_context(
            patch.object(preflight.transport.processes, "interruption_handler", contextlib.nullcontext)
        )
        self.raw_popen = self.stack.enter_context(
            patch.object(
                preflight.transport.subprocess,
                "Popen",
                side_effect=AssertionError("Automatic model preflight reached raw Popen"),
            )
        )
        self.wait = self.stack.enter_context(
            patch.object(preflight.transport.processes, "wait_for_stage", side_effect=self.finish_child)
        )

    @staticmethod
    def finish_child(child, timeout, checkpoint):
        child.stdout.write("".join(json.dumps(row) + "\n" for row in OK_EVENTS))
        child.stdout.flush()
        checkpoint([])
        return 0, False

    def check(self):
        return preflight.check(self.runner, self.state, self.run_dir, self.root, timeout=3)

    def owned_launcher(self, errors=()):
        errors = iter(errors)

        @contextlib.contextmanager
        def launch(argv, **kwargs):
            self.assertEqual(sys.executable, argv[0])
            self.assertTrue(kwargs["start_new_session"])
            self.assertEqual(3, kwargs["timeout"])
            self.assertEqual(self.root, kwargs["cwd"])
            self.assertIn('"ok": true', kwargs["stdin"].read())
            receipt = Path(kwargs["receipt_path"])
            self.assertEqual("supervision.json", receipt.name)
            metadata = {
                "schema": 1,
                "nonce": f"fixture-{len(self.admissions)}",
                "owner": {"pid": 11, "birth_identity": 1},
                "keeper": {"pid": 12, "birth_identity": 2},
                "provider": {"pid": 13, "birth_identity": 3},
                "receipt": str(receipt),
            }
            receipt.write_text(json.dumps({**metadata, "phase": "armed"}))
            kwargs["checkpoint"](metadata)
            saved = json.loads((receipt.parent / "supervision-launch.json").read_text())
            self.assertEqual(metadata, saved.get("supervision", saved))
            self.admissions.append(receipt.parent)
            child = types.SimpleNamespace(stdout=kwargs["stdout"], supervision=metadata, returncode=0)
            yield child
            error = next(errors, None)
            if isinstance(error, supervision.VerifiedStop):
                receipt.write_text(json.dumps({**metadata, "phase": "stopped", "cause": error.cause}))
            elif error is None:
                receipt.write_text(json.dumps({**metadata, "phase": "stopped", "cause": "provider_stopped"}))
            if error is not None:
                raise error

        return launch

    def test_unsupported_host_refuses_before_any_probe_child_or_success_marker(self):
        # macOS runs under portable supervision (#1195); every other non-Linux host still refuses.
        with patch.object(boundary.sys, "platform", "freebsd14"):
            with self.assertRaises(support.Paused) as caught:
                self.check()
        self.assertEqual("PAUSED_PROCESS_CLEANUP", caught.exception.status)
        self.assertIn("no provider launched", str(caught.exception))
        self.assertEqual(0, self.raw_popen.call_count, "Automatic model preflight must not use raw Popen")
        self.wait.assert_not_called()
        self.assertFalse((self.run_dir / "model-preflight.json").exists())
        self.assertEqual([], list(self.run_dir.rglob("supervision.json")))
        self.assertEqual([], list(self.run_dir.rglob("supervision-launch.json")))

    def test_automatic_probe_uses_provider_guard_and_persists_admission_before_wait(self):
        with patch.object(supervision, "launch", side_effect=self.owned_launcher()) as guarded:
            self.assertTrue(self.check())
        guarded.assert_called_once()
        self.assertEqual(0, self.raw_popen.call_count, "Automatic model preflight must not use raw Popen")
        self.wait.assert_called_once()
        saved = json.loads((self.run_dir / "model-preflight.json").read_text())["probed"][self.model]
        self.assertEqual("ok", saved["status"])
        self.assertEqual(self.admissions[0], Path(saved["evidence"]))

    def test_admission_and_cleanup_failures_block_without_caching_ok_events(self):
        for phase in ("admission", "cleanup"):
            for error_type in (supervision.SupervisionError, OSError):
                with self.subTest(phase=phase, error=error_type.__name__):
                    self.run_dir = self.root / f"{phase}-{error_type.__name__}"
                    self.run_dir.mkdir()
                    error = error_type("fixture ownership is uncertain")
                    launcher = self.owned_launcher([error]) if phase == "cleanup" else None
                    with patch.object(supervision, "launch", side_effect=launcher or error):
                        with self.assertRaises(support.Paused) as caught:
                            self.check()
                    self.assertEqual("PAUSED_PROCESS_CLEANUP", caught.exception.status)
                    self.assertIn("fixture ownership is uncertain", str(caught.exception))
                    self.assertFalse((self.run_dir / "model-preflight.json").exists())
        self.assertEqual(0, self.raw_popen.call_count, "Automatic model preflight must not use raw Popen")

    def test_non_deadline_verified_stop_blocks_the_probe(self):
        with patch.object(
            supervision, "launch", side_effect=self.owned_launcher([supervision.VerifiedStop("owner_lost")])
        ):
            with self.assertRaises(support.Paused) as caught:
                self.check()
        self.assertEqual("PAUSED_PROCESS_CLEANUP", caught.exception.status)
        self.assertIn("owner_lost", str(caught.exception))
        self.assertFalse((self.run_dir / "model-preflight.json").exists())
        self.assertEqual(0, self.raw_popen.call_count)

    def test_verified_deadline_is_uncertain_and_retry_preserves_prior_evidence(self):
        with patch.object(
            supervision, "launch", side_effect=self.owned_launcher([supervision.VerifiedStop("stage_deadline"), None])
        ) as guarded:
            self.assertTrue(self.check())
            first = json.loads((self.run_dir / "model-preflight.json").read_text())["probed"][self.model]
            self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", first["status"])
            self.assertFalse(first["blocking"])
            first_path = Path(first["evidence"])
            original_receipt = (first_path / "supervision.json").read_bytes()
            self.assertTrue(self.check())
            second = json.loads((self.run_dir / "model-preflight.json").read_text())["probed"][self.model]
        self.assertEqual(2, guarded.call_count)
        self.assertEqual("ok", second["status"])
        self.assertNotEqual(first_path, Path(second["evidence"]))
        self.assertTrue((first_path / "events.jsonl").is_file())
        self.assertEqual(original_receipt, (first_path / "supervision.json").read_bytes())
        self.assertEqual(0, self.raw_popen.call_count, "Automatic model preflight must not use raw Popen")

    def test_standalone_transport_retains_its_unsupervised_default(self):
        self.raw_popen.side_effect = lambda argv, **kwargs: types.SimpleNamespace(stdout=kwargs["stdout"])
        with patch.object(
            supervision, "launch", side_effect=AssertionError("Standalone probe changed admission")
        ) as guarded:
            result = preflight.transport.execute(
                adapter=self.adapter,
                workspace=self.root,
                directory=self.root / "standalone",
                model=self.model,
                effort="low",
                session=None,
                allow_write=False,
                prompt=preflight.PROMPT,
                schema=preflight.SCHEMA,
                env={},
                timeout=3,
            )
        self.assertEqual(0, result["exit_code"])
        self.assertFalse(result["timed_out"])
        self.assertEqual({"ok": True}, result["report"])
        guarded.assert_not_called()
        self.raw_popen.assert_called_once()
        self.assertFalse((self.root / "standalone" / "supervision.json").exists())


if __name__ == "__main__":
    unittest.main()
