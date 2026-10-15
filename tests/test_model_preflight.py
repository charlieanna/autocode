"""Launch probes each billing-routed role model once per run, before stage spend."""

import contextlib
import io
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode_model_preflight as preflight
import autocode_providers
import autocode_session_boundary as boundary
import autocode_supervision as supervision
import autocode_support as support
import autocode_util as util

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


def receipt_sets(run_dir):
    """The saved marker's receipt sets, one per tool identity."""
    saved = json.loads((Path(run_dir) / "model-preflight.json").read_text())
    if saved.get("version") != 3:
        raise AssertionError(f"model-preflight.json has version {saved.get('version')!r}, expected 3")
    return saved["receipts"]


def probed(run_dir):
    """The receipts of a run with one billing-aware tool."""
    (receipt,) = receipt_sets(run_dir)
    return receipt["probed"]


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
        self.identity = {
            "engine": "fixture",
            "identity_version": 1,
            "executable": "/fixture/provider",
            "version": "1.0",
            "config_path": "/fixture/provider.toml",
            "config_sha256": "a" * 64,
        }
        self.adapter = types.SimpleNamespace(
            NAME="fixture",
            _config={"auth": {"command": ["kilo", "auth", "list"], "routes": []}},
            check_subscription_routes=lambda roles, workspace=None, env=None: None,
        )
        self.state = {
            "settings": {
                "engine": "opencode",
                "roles": {
                    "astra": {"model": "plan/glm"},
                    "terra": {"model": "plan/mimo"},
                    "sol": {"model": "plan/glm"},
                },
                "transport_identities": {"opencode": self.identity},
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

    def run_identity(self):
        return preflight._receipt_identity(self.adapter, self.state["settings"]["transport_identities"]["opencode"])

    def test_each_distinct_model_is_probed_once_per_run(self):
        events = {"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}
        self.assertTrue(self.check(events))
        self.assertEqual(["plan/glm", "plan/mimo"], self.calls)
        self.assertFalse(self.check(events))
        self.assertEqual(["plan/glm", "plan/mimo"], self.calls)
        (saved,) = receipt_sets(self.run_dir)
        self.assertEqual(self.run_identity(), saved["identity"])
        self.assertEqual({"plan/glm", "plan/mimo"}, set(saved["probed"]))
        self.assertEqual("ok", saved["probed"]["plan/mimo"]["status"])

    def test_changed_provider_configuration_reprobes_the_same_models(self):
        events = {"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}
        self.assertTrue(self.check(events))
        old_identity = self.run_identity()

        self.adapter._config["command"] = ["changed-provider-command"]
        self.state["settings"]["transport_identities"]["opencode"] = {
            **self.identity,
            "config_sha256": "b" * 64,
        }
        new_identity = self.run_identity()

        self.assertNotEqual(old_identity, new_identity)
        self.assertTrue(self.check(events))
        self.assertEqual(["plan/glm", "plan/mimo", "plan/glm", "plan/mimo"], self.calls)
        self.assertEqual(
            [old_identity, old_identity, new_identity, new_identity],
            [row["preflight_identity"] for row in self.state["stages"]],
        )
        self.assertEqual([new_identity], [saved["identity"] for saved in receipt_sets(self.run_dir)])

    def test_legacy_model_only_marker_is_unbound_and_reprobed(self):
        (self.run_dir / "model-preflight.json").write_text(
            json.dumps(
                {
                    "version": 1,
                    "probed": {
                        "plan/glm": {"status": "ok"},
                        "plan/mimo": {"status": "ok"},
                    },
                }
            )
        )
        events = {"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}

        self.assertTrue(self.check(events))
        self.assertEqual(["plan/glm", "plan/mimo"], self.calls)
        self.assertEqual([self.run_identity()], [saved["identity"] for saved in receipt_sets(self.run_dir)])

    def test_saved_version_2_marker_is_reused_without_a_new_request(self):
        # Runs saved before per-tool receipts hold one receipt set, bound to the run's tool.
        (self.run_dir / "model-preflight.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "identity": {"provider": "fixture", "transport_sha256": util.digest(self.identity)},
                    "probed": {
                        "plan/glm": {"model": "plan/glm", "status": "ok", "blocking": False},
                        "plan/mimo": {"model": "plan/mimo", "status": "ok", "blocking": False},
                    },
                }
            )
        )
        self.assertFalse(self.check({}))
        self.assertEqual([], self.calls)

    def test_bound_charged_attempts_restore_an_interrupted_marker_publication(self):
        events = {"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}
        self.assertTrue(self.check(events))
        (self.run_dir / "model-preflight.json").unlink()

        self.assertFalse(self.check(events))
        self.assertEqual(["plan/glm", "plan/mimo"], self.calls)

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
        saved = probed(self.run_dir)
        self.assertTrue(saved["plan/mimo"]["blocking"])
        self.assertIn("Add credits", saved["plan/mimo"]["detail"])

    def test_resume_reprobes_a_route_that_failed_until_it_passes(self):
        with self.assertRaises(support.Paused):
            self.check({"plan/glm": OK_EVENTS, "plan/mimo": PAYWALL_EVENTS})
        with self.assertRaises(support.Paused):
            self.check({"plan/glm": OK_EVENTS, "plan/mimo": PAYWALL_EVENTS})
        self.assertEqual(["plan/glm", "plan/mimo", "plan/mimo"], self.calls)
        self.assertTrue(self.check({"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}))
        self.assertEqual(["plan/glm", "plan/mimo", "plan/mimo", "plan/mimo"], self.calls)
        self.assertEqual("ok", probed(self.run_dir)["plan/mimo"]["status"])

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
        self.assertFalse(probed(self.run_dir)["plan/glm"]["blocking"])


class MixedProviderPreflightTests(unittest.TestCase):
    """A role bound with --role-transport is probed through its own tool, never the run's tool."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.kilo_identity = {
            "engine": "kilocode",
            "identity_version": 1,
            "executable": "/fixture/kilo",
            "version": "1.0",
            "config_path": "/fixture/kilocode.toml",
            "config_sha256": "b" * 64,
        }
        self.logins = []
        self.primary = self.spy("fixture", billing=True)
        self.kilo = self.spy("kilocode", billing=True)
        self.kilo.CONFIGURED = True
        self.kilo.local_settings = lambda workspace=None, env=None: dict(self.kilo_identity)
        self.kilo.transport_drift = lambda current, saved: current != saved
        self.state = {
            "settings": {
                "engine": "opencode",
                "provider": "opencode",
                "roles": {
                    "astra": {"model": "plan/glm"},
                    "terra": {"model": "plan/glm"},
                    "sol": {"engine": "opencode", "provider": "kilocode", "model": "kilo/xiaomi/mimo"},
                },
                "transport_identities": {
                    "opencode": {"engine": "fixture", "config_sha256": "a" * 64},
                    "role:sol": {"provider": "kilocode", "identity": dict(self.kilo_identity)},
                },
            }
        }
        self.calls = []
        self.events = {}
        resolve = patch.object(autocode_providers, "resolve", side_effect=self.resolve)
        resolve.start()
        self.addCleanup(resolve.stop)

    def spy(self, name, *, billing):
        adapter = types.SimpleNamespace(NAME=name)
        if billing:
            adapter._config = {"auth": {"command": [name, "auth", "list"], "routes": []}}
        adapter.check_subscription_routes = lambda roles, workspace=None, env=None: self.logins.append(
            (adapter, dict(roles))
        )
        return adapter

    def resolve(self, name):
        if name == "kilocode":
            return self.kilo
        raise RuntimeError(f"no provider config for '{name}'")

    def execute(self, **kwargs):
        self.calls.append((kwargs["adapter"], kwargs["model"]))
        kwargs["directory"].mkdir(parents=True, exist_ok=True)
        rows = self.events.get((kwargs["adapter"].NAME, kwargs["model"]), OK_EVENTS)
        (kwargs["directory"] / "events.jsonl").write_text("".join(json.dumps(row) + "\n" for row in rows))
        return {"exit_code": 0, "timed_out": False, "report": None, "rows": rows, "metrics": {}}

    def check(self, primary=None, **runner):
        runner = types.SimpleNamespace(opencode=primary or self.primary, **runner)
        with patch.object(preflight.transport, "execute", side_effect=lambda **kwargs: self.execute(**kwargs)):
            return preflight.check(runner, self.state, self.run_dir, self.root)

    def receipts(self):
        return {entry["identity"]["provider"]: entry["probed"] for entry in receipt_sets(self.run_dir)}

    def test_non_billing_primary_still_probes_the_bound_role_on_its_own_tool(self):
        # Phase 1 role transport requires the built-in OpenCode primary, which declares no [auth].
        builtin = self.spy("opencode", billing=False)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertTrue(self.check(builtin))
        self.assertIn("Model preflight: kilo/xiaomi/mimo on kilocode ok", output.getvalue())
        self.assertEqual([(self.kilo, "kilo/xiaomi/mimo")], self.calls)
        self.assertEqual([(self.kilo, {"sol": self.state["settings"]["roles"]["sol"]})], self.logins)
        self.assertEqual({"kilocode"}, set(self.receipts()))
        self.assertEqual("ok", self.receipts()["kilocode"]["kilo/xiaomi/mimo"]["status"])
        self.assertEqual(["kilocode"], [row["launch_route"]["provider"] for row in self.state["stages"]])

        self.assertFalse(self.check(builtin))
        self.assertEqual(1, len(self.calls), "A passed bound-role receipt is reused on resume")

    def test_each_model_goes_only_to_the_tool_that_serves_its_role(self):
        self.assertTrue(self.check())
        self.assertEqual([(self.kilo, "kilo/xiaomi/mimo"), (self.primary, "plan/glm")], self.calls)
        self.assertEqual(
            [(self.kilo, {"sol"}), (self.primary, {"astra", "terra"})],
            [(adapter, set(checked)) for adapter, checked in self.logins],
        )

    def test_roles_bound_to_the_same_tool_share_one_login_and_one_probe_per_model(self):
        settings = self.state["settings"]
        settings["roles"]["astra"] = {"engine": "opencode", "provider": "kilocode", "model": "kilo/xiaomi/mimo"}
        settings["transport_identities"]["role:astra"] = {"provider": "kilocode", "identity": dict(self.kilo_identity)}
        self.assertTrue(self.check(self.spy("opencode", billing=False)))
        self.assertEqual([(self.kilo, "kilo/xiaomi/mimo")], self.calls)
        self.assertEqual([(self.kilo, {"astra", "sol"})], [(adapter, set(roles)) for adapter, roles in self.logins])

    def test_every_login_is_checked_before_the_first_request(self):
        order = []
        self.primary.check_subscription_routes = lambda roles, workspace=None, env=None: order.append("login")
        self.kilo.check_subscription_routes = lambda roles, workspace=None, env=None: order.append("login")
        execute = self.execute
        self.execute = lambda **kwargs: order.append("probe") or execute(**kwargs)
        self.assertTrue(self.check())
        self.assertEqual(["login", "login", "probe", "probe"], order)

    def test_second_tool_login_refusal_stops_before_any_request(self):
        def refuse(roles, workspace=None, env=None):
            raise RuntimeError("cannot verify kilocode login; auth listing failed")

        self.kilo.check_subscription_routes = refuse
        with self.assertRaises(support.Paused) as caught:
            self.check()
        self.assertEqual("PAUSED_BILLING_ROUTE", caught.exception.status)
        self.assertIn("kilocode login", str(caught.exception))
        self.assertEqual([], self.calls)
        self.assertFalse((self.run_dir / "model-preflight.json").exists())

    def test_the_same_model_on_two_tools_is_probed_and_cached_separately(self):
        self.state["settings"]["roles"]["sol"]["model"] = "plan/glm"
        self.events[("kilocode", "plan/glm")] = PAYWALL_EVENTS
        with self.assertRaises(support.Paused) as caught:
            self.check()
        self.assertEqual("PAUSED_BUDGET", caught.exception.status)
        self.assertIn("kilocode", str(caught.exception))
        self.assertEqual([(self.kilo, "plan/glm")], self.calls, "The bound tool's refusal stops the build")
        self.assertTrue(self.receipts()["kilocode"]["plan/glm"]["blocking"])

        self.events[("kilocode", "plan/glm")] = OK_EVENTS
        self.assertTrue(self.check())
        self.assertEqual([(self.kilo, "plan/glm"), (self.kilo, "plan/glm"), (self.primary, "plan/glm")], self.calls)
        receipts = self.receipts()
        self.assertEqual("ok", receipts["kilocode"]["plan/glm"]["status"])
        self.assertEqual("ok", receipts["fixture"]["plan/glm"]["status"])

        self.assertFalse(self.check())
        self.assertEqual(3, len(self.calls), "Both receipts are reused; neither satisfies the other")

    def test_saved_single_tool_marker_is_trusted_only_for_its_own_tool(self):
        # Version 2, as written before per-tool receipts: one identity, the run's own tool.
        self.state["settings"]["roles"]["sol"]["model"] = "plan/glm"
        transport = self.state["settings"]["transport_identities"]["opencode"]
        (self.run_dir / "model-preflight.json").write_text(
            json.dumps(
                {
                    "version": 2,
                    "identity": {"provider": "fixture", "transport_sha256": util.digest(transport)},
                    "probed": {"plan/glm": {"model": "plan/glm", "status": "ok", "blocking": False}},
                }
            )
        )
        self.assertTrue(self.check())
        self.assertEqual([(self.kilo, "plan/glm")], self.calls)
        self.assertEqual({"fixture", "kilocode"}, set(self.receipts()))

    def test_saved_marker_alone_restores_each_tool_receipt(self):
        self.state["settings"]["roles"]["sol"]["model"] = "plan/glm"
        self.assertTrue(self.check())
        self.state["stages"] = []  # Leave only the marker to vouch for both passes.
        self.assertFalse(self.check())
        self.assertEqual(2, len(self.calls))

    def test_charged_attempts_restore_each_tool_receipt_after_a_lost_marker(self):
        self.state["settings"]["roles"]["sol"]["model"] = "plan/glm"
        self.assertTrue(self.check())
        self.assertEqual(2, len(self.calls))
        (self.run_dir / "model-preflight.json").unlink()
        self.assertFalse(self.check())
        self.assertEqual(2, len(self.calls))

    def test_changed_bound_tool_pauses_before_any_request(self):
        self.kilo.local_settings = lambda workspace=None, env=None: {**self.kilo_identity, "config_sha256": "c" * 64}
        with self.assertRaises(support.Paused) as caught:
            self.check()
        self.assertEqual("PAUSED_TRANSPORT_CHANGED", caught.exception.status)
        self.assertEqual([], self.calls)
        self.assertEqual([], self.logins)

    def test_native_owner_makes_no_standalone_call_on_any_tool(self):
        self.kilo.local_settings = lambda *args, **kwargs: self.fail("native ownership must not inspect tools")
        with patch.object(preflight, "probe") as probe:
            self.assertFalse(self.check(native_calls=object()))
        probe.assert_not_called()
        self.assertEqual([], self.calls)
        self.assertEqual([], self.logins)
        self.assertEqual([], list(self.run_dir.iterdir()))


class OwnedPreflightTests(unittest.TestCase):
    """Exercise real probe formatting/admission with no subprocess or model."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        self.model = "plan/glm"
        self.state = {
            "settings": {
                "engine": "opencode",
                "roles": {"astra": {"model": self.model}},
                "transport_identities": {
                    "opencode": {
                        "engine": "fixture",
                        "identity_version": 1,
                        "config_sha256": "a" * 64,
                    }
                },
            }
        }
        self.adapter = types.SimpleNamespace(
            NAME="fixture",
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
        saved = probed(self.run_dir)[self.model]
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
            first = probed(self.run_dir)[self.model]
            self.assertEqual("PAUSED_PROVIDER_UNCERTAIN", first["status"])
            self.assertFalse(first["blocking"])
            first_path = Path(first["evidence"])
            original_receipt = (first_path / "supervision.json").read_bytes()
            self.assertTrue(self.check())
            second = probed(self.run_dir)[self.model]
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
