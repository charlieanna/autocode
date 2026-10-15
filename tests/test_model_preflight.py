"""Launch probes each billing-routed role model once per run, before stage spend."""

import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_model_preflight as preflight
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

    def test_native_owner_makes_no_standalone_access_calls(self):
        runner = types.SimpleNamespace(opencode=self.adapter, native_calls=object())
        with (
            patch.object(self.adapter, "check_subscription_routes") as listing,
            patch.object(preflight, "probe", return_value={"status": "ok", "seconds": 0.0}) as probe,
            patch.object(preflight.transport, "execute") as execute,
        ):
            self.assertFalse(preflight.check(runner, self.state, self.run_dir, self.root))
        listing.assert_not_called()
        probe.assert_not_called()
        execute.assert_not_called()
        self.assertEqual([], list(self.run_dir.iterdir()))

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


if __name__ == "__main__":
    unittest.main()
