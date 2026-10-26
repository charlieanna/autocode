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
import autocode_route_fit as route_fit
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


class BareProbeAgentTests(unittest.TestCase):
    """#1744: the probe's agent has no tools and a one-line prompt (Kilo 19,053 -> 627 input tokens; OpenCode
    17,672 -> 218, measured 2026-10-21 on GPT-6 Luna)."""

    def test_an_opencode_family_launch_runs_the_bare_probe_agent(self):
        for variable, chosen in (
            ("OPENCODE_CONFIG_CONTENT", {"permission": {"edit": "deny"}}),
            ("KILO_CONFIG_CONTENT", {"permission": {"read": "allow"}, "model": "openai/gpt-6-luna"}),
        ):
            with self.subTest(variable=variable):
                config = {"plugin": ["activity.mjs"], "agent": {"autocode_sol_x": chosen, "other": {}}}
                argv = ["kilo", "run", "--agent", "autocode_sol_x", "--model", "openai/gpt-6-luna"]
                ran, env = preflight.bare(argv, {variable: json.dumps(config), "HOME": "/h"})
                self.assertEqual(preflight.PROBE_AGENT, ran[ran.index("--agent") + 1])
                written = json.loads(env[variable])
                agent = written["agent"][preflight.PROBE_AGENT]
                self.assertEqual(({"*": False}, {"*": "deny"}), (agent["tools"], agent["permission"]))
                self.assertEqual(preflight.PROBE_SYSTEM, agent["prompt"])
                self.assertEqual(chosen.get("model"), agent.get("model"))
                self.assertEqual(["activity.mjs"], written["plugin"])
                self.assertEqual("/h", env["HOME"])

    def test_a_launch_without_an_inline_agent_is_left_alone(self):
        for argv, env in (
            (["claude", "-p", "x"], {"OPENCODE_CONFIG_CONTENT": "{}"}),
            (["opencode", "run", "--agent", "a"], {"HOME": "/h"}),
        ):
            with self.subTest(argv=argv):
                self.assertEqual((argv, env), preflight.bare(list(argv), dict(env)))


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

    def test_the_resolvers_own_model_is_probed_only_before_its_first_stage(self):
        # #1744: GPT-6 Astra's probe cost ~$0.19 of a small run's $1.31, and the Resolver runs only after a send-back.
        self.state["settings"]["roles"]["astra"] = {"model": "plan/astra"}
        events = {"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS, "plan/astra": OK_EVENTS}
        self.state["next_stage"] = "recognize_workflow"
        self.assertTrue(self.check(events))
        self.assertEqual(["plan/mimo", "plan/glm"], self.calls)
        self.state["next_stage"] = "terra"
        self.assertFalse(self.check(events))
        self.state["next_stage"] = "astra_resolve"
        self.assertTrue(self.check(events))
        self.assertEqual(["plan/mimo", "plan/glm", "plan/astra"], self.calls)
        self.assertFalse(self.check(events), "a passed probe is never repeated")

    def test_a_deferred_roles_model_that_another_role_runs_is_probed_at_the_start(self):
        self.state["next_stage"] = "recognize_workflow"  # astra shares plan/glm with sol
        self.assertTrue(self.check({"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}))
        self.assertEqual(["plan/mimo", "plan/glm"], self.calls)

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

    def test_a_saved_route_probes_at_its_roles_effort_and_certifies_the_level(self):
        # #2057: every route, saved or selected, proves the level it will launch at.
        self.state["settings"]["roles"]["sol"] = {"model": "plan/glm", "reasoning_effort": "high"}
        probed = []
        execute = self.fake_execute({"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS})

        def spy(**kwargs):
            probed.append((kwargs["model"], kwargs.get("effort")))
            return execute(**kwargs)

        with patch.object(preflight.transport, "execute", side_effect=spy):
            runner = types.SimpleNamespace(opencode=self.adapter)
            self.assertTrue(preflight.check(runner, self.state, self.run_dir, self.root))
        self.assertEqual({("plan/glm", "high"), ("plan/glm", None), ("plan/mimo", None)}, set(probed))
        levels = {
            entry["identity"].get("reasoning_effort"): set(entry["probed"]) for entry in receipt_sets(self.run_dir)
        }
        self.assertEqual({None: {"plan/glm", "plan/mimo"}, "high": {"plan/glm"}}, levels)
        self.assertEqual({"plan/glm": ["high"]}, self.state["settings"]["certified_variants"])
        self.assertFalse(self.check({"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}), "both receipts are reused")

    def test_a_saved_receipt_certifies_its_level_without_another_probe(self):
        self.state["settings"]["roles"]["sol"] = {"model": "plan/glm", "reasoning_effort": "high"}
        events = {"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}
        self.assertTrue(self.check(events))
        self.state["settings"].pop("certified_variants")
        self.assertFalse(self.check(events))
        self.assertEqual({"plan/glm": ["high"]}, self.state["settings"]["certified_variants"])
        self.assertEqual(3, len(self.calls), "the receipt is evidence; no new request")

    def test_an_uncertain_probe_does_not_certify_its_level(self):
        self.state["settings"]["roles"]["sol"] = {"model": "plan/glm", "reasoning_effort": "high"}
        self.state["settings"]["roles"]["completion"] = {"model": "plan/mimo", "reasoning_effort": "medium"}
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertTrue(self.check({"plan/glm": OK_EVENTS, "plan/mimo": GARBLED_EVENTS}))
        self.assertEqual({"plan/glm": ["high"]}, self.state["settings"]["certified_variants"])

    def test_one_model_at_two_saved_levels_is_probed_and_certified_twice(self):
        del self.state["settings"]["roles"]["astra"]
        self.state["settings"]["roles"]["sol"] = {"model": "plan/glm", "reasoning_effort": "high"}
        self.state["settings"]["roles"]["completion"] = {"model": "plan/glm", "reasoning_effort": "medium"}
        events = {"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS}
        self.assertTrue(self.check(events))
        self.assertEqual(["plan/glm", "plan/glm", "plan/mimo"], sorted(self.calls))
        self.assertEqual(["medium", "high"], self.state["settings"]["certified_variants"]["plan/glm"])
        self.assertFalse(self.check(events))
        self.assertEqual(3, len(self.calls), "each level's receipt is reused")

    def test_a_person_named_level_its_model_does_not_declare_pauses_before_any_probe(self):
        # #2104: the access preflight is the first launch boundary, so it fits like route_fit.launch.
        self.state["settings"]["roles"]["sol"] = {"model": "plan/glm", "reasoning_effort": "medium"}
        self.state["settings"]["effort_origins"] = {"sol": "user_explicit"}
        self.state["settings"]["route_selection"] = {
            "candidates": {"sol": [{"model": "plan/glm", "levels": ["high", "low", "max"]}]}
        }
        with patch.object(preflight, "probe", side_effect=AssertionError("a probe ran before the fit")):
            with self.assertRaises(route_fit.EffortUnsupported) as caught:
                self.check({"plan/glm": OK_EVENTS, "plan/mimo": OK_EVENTS})
        self.assertEqual(route_fit.STATUS, caught.exception.status)
        self.assertIn("--sol-reasoning-effort", str(caught.exception))
        self.assertIn("low, high, max", str(caught.exception))
        self.assertEqual([], self.calls, "no model was launched")

    def test_a_runner_chosen_level_is_probed_at_the_level_the_model_declares(self):
        self.state["settings"]["roles"] = {"sol": {"model": "plan/glm", "reasoning_effort": "medium"}}
        self.state["settings"]["route_selection"] = {
            "candidates": {"sol": [{"model": "plan/glm", "levels": ["high", "low", "max"]}]}
        }
        execute = self.fake_execute({"plan/glm": OK_EVENTS})
        seen = []

        def spy(**kwargs):
            seen.append((kwargs["model"], kwargs.get("effort")))
            return execute(**kwargs)

        with patch.object(preflight.transport, "execute", side_effect=spy):
            runner = types.SimpleNamespace(opencode=self.adapter)
            self.assertTrue(preflight.check(runner, self.state, self.run_dir, self.root))
        self.assertEqual([("plan/glm", "high")], seen)
        self.assertEqual({"plan/glm": ["high"]}, self.state["settings"]["certified_variants"])
        self.assertEqual(
            "medium", self.state["settings"]["roles"]["sol"]["reasoning_effort"], "the saved route is untouched"
        )

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

    def test_rejected_authentication_blocks_before_another_model_is_probed(self):
        rejection = [
            {
                "type": "error",
                "sessionID": "ses_auth",
                "error": {
                    "name": "APIError",
                    "data": {
                        "statusCode": 401,
                        "message": "private-test-canary",
                        "responseBody": "private-test-canary; budget exhausted",
                    },
                },
            }
        ]
        with self.assertRaises(support.Paused) as caught:
            self.check({"plan/glm": rejection, "plan/mimo": OK_EVENTS})
        self.assertEqual("PAUSED_AUTH", caught.exception.status)
        self.assertIn("401", str(caught.exception))
        self.assertNotIn("private-test-canary", str(caught.exception))
        self.assertEqual(["plan/glm"], self.calls)
        self.assertTrue(probed(self.run_dir)["plan/glm"]["blocking"])
        self.assertNotIn("private-test-canary", probed(self.run_dir)["plan/glm"]["detail"])

    def test_authentication_hint_names_only_actual_withheld_probe_environment(self):
        canary = "private-probe-credential-canary"
        rejection = [
            {
                "type": "error",
                "error": {
                    "name": "APIError",
                    "data": {
                        "statusCode": 401,
                        "message": canary,
                        "responseBody": canary,
                    },
                },
            }
        ]
        for index, (passed, expected) in enumerate(
            (
                ("", ["FIXTURE_API_KEY", "OTHER_PASSWORD"]),
                ("FIXTURE_API_KEY", ["OTHER_PASSWORD"]),
                ("FIXTURE_API_KEY,OTHER_PASSWORD", []),
            )
        ):
            with self.subTest(passed=passed):
                env = {
                    "FIXTURE_API_KEY": canary,
                    "OTHER_PASSWORD": canary,
                    "AUTOCODE_RESOLVER_TOKEN": canary,
                    "AUTOCODE_PASS_ENV": passed,
                }
                with (
                    patch.dict(preflight.os.environ, env, clear=True),
                    patch.object(
                        preflight.transport, "execute", side_effect=self.fake_execute({"plan/glm": rejection})
                    ) as execute,
                ):
                    outcome = preflight.probe(
                        self.adapter,
                        "plan/glm",
                        self.root,
                        self.run_dir,
                        directory=self.root / f"probe-{index}",
                    )
                self.assertEqual(env, execute.call_args.kwargs["env"])
                self.assertEqual("PAUSED_AUTH", outcome["status"])
                self.assertTrue(outcome["blocking"])
                detail = outcome["detail"]
                self.assertIn("HTTP 401", detail)
                self.assertNotIn(canary, detail)
                self.assertNotIn("AUTOCODE_RESOLVER_TOKEN", detail)
                if expected:
                    self.assertIn("Credential-like environment variables were withheld: ", detail)
                    names = detail.split("Credential-like environment variables were withheld: ", 1)[1].split(
                        ". If this provider", 1
                    )[0]
                    self.assertEqual(expected, names.split(", "))
                    self.assertIn("AUTOCODE_PASS_ENV", detail)
                else:
                    self.assertNotIn("AUTOCODE_PASS_ENV", detail)

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

    def check(self, primary=None, before_probe=None, **runner):
        runner = types.SimpleNamespace(opencode=primary or self.primary, **runner)
        with patch.object(preflight.transport, "execute", side_effect=lambda **kwargs: self.execute(**kwargs)):
            return preflight.check(runner, self.state, self.run_dir, self.root, before_probe=before_probe)

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
        # The bound tool's login is checked first, the run's tool second (role_transport.groups).
        def refuse(roles, workspace=None, env=None):
            raise RuntimeError("cannot verify fixture login; auth listing failed")

        self.primary.check_subscription_routes = refuse
        with self.assertRaises(support.Paused) as caught:
            self.check()
        self.assertEqual("PAUSED_BILLING_ROUTE", caught.exception.status)
        self.assertIn("fixture login", str(caught.exception))
        self.assertEqual([self.kilo], [adapter for adapter, _roles in self.logins], "Kilo's login passed first")
        self.assertEqual([], self.calls, "Kilo's model is not probed before the second login is checked")
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

        # Kilo now passes, but a pause request arrives during its probe, before the run's tool is probed.
        self.events[("kilocode", "plan/glm")] = OK_EVENTS
        earlier = len(self.calls)

        def pause_after_kilo_answers():
            if len(self.calls) > earlier:
                raise support.Paused("PAUSED_REQUESTED", "Pause requested; previous model access probe saved")

        with self.assertRaises(support.Paused) as caught:
            self.check(before_probe=pause_after_kilo_answers)
        self.assertEqual("PAUSED_REQUESTED", caught.exception.status)
        self.assertEqual([(self.kilo, "plan/glm"), (self.kilo, "plan/glm")], self.calls)
        self.assertEqual("ok", self.receipts()["kilocode"]["plan/glm"]["status"])

        # Kilo's pass does not count for the run's tool, which still has to prove the same model.
        self.assertTrue(self.check())
        self.assertEqual(
            [(self.kilo, "plan/glm"), (self.kilo, "plan/glm"), (self.primary, "plan/glm")],
            self.calls,
            "The resume probes only the unprobed tool and reuses Kilo's pass",
        )
        receipts = self.receipts()
        self.assertEqual("ok", receipts["kilocode"]["plan/glm"]["status"])
        self.assertEqual("ok", receipts["fixture"]["plan/glm"]["status"])

        self.assertFalse(self.check())
        self.assertEqual(3, len(self.calls), "Both receipts are reused once each tool has its own pass")

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


class SelectionPreflightTests(unittest.TestCase):
    """The automatic selector's access check (#1426 step 8)."""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run_dir = self.root / "run"
        self.run_dir.mkdir()
        # Built-in OpenCode routes have no [auth] table: saved-run probes skip them,
        # selection probes must not.
        self.adapter = types.SimpleNamespace(
            NAME="opencode",
            check_subscription_routes=lambda roles, workspace=None, env=None: None,
        )
        self.state = {"settings": {"engine": "opencode", "roles": {}}}
        self.calls = []

    def fake_execute(self, events_by_model):
        def execute(**kwargs):
            model = kwargs["model"]
            self.calls.append((model, kwargs.get("effort")))
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

    def selection(self, *, probe=None):
        routes = {
            "requirements": {
                "model": "zai-coding-plan/glm-5.3",
                "tool": "opencode",
                "reasoning_effort": "medium",
            },
            "terra": {
                "model": "xiaomi-token-plan-sgp/mimo-v2.6-pro",
                "tool": "opencode",
                "reasoning_effort": "medium",
            },
            "sol": {"model": "openai/gpt-6-sol", "tool": "opencode", "reasoning_effort": None},
        }
        unknown = sorted({route["model"] for route in routes.values()})
        return {"routes": routes, "probe": probe if probe is not None else unknown, "stop": None}

    def check(self, events_by_model, selection):
        runner = types.SimpleNamespace(opencode=self.adapter)
        with patch.object(preflight.transport, "execute", side_effect=self.fake_execute(events_by_model)):
            return preflight.check(runner, self.state, self.run_dir, self.root, selection=selection)

    def test_unknown_routes_probe_at_their_roles_own_levels(self):
        models = ("zai-coding-plan/glm-5.3", "xiaomi-token-plan-sgp/mimo-v2.6-pro", "openai/gpt-6-sol")
        events = {model: OK_EVENTS for model in models}
        self.assertTrue(self.check(events, self.selection()))
        self.assertEqual(
            [
                ("openai/gpt-6-sol", None),
                ("xiaomi-token-plan-sgp/mimo-v2.6-pro", "medium"),
                ("zai-coding-plan/glm-5.3", "medium"),
            ],
            sorted(self.calls),
        )
        # A second check of the same selection reuses the receipts: no new calls.
        self.assertFalse(self.check(events, self.selection()))
        self.assertEqual(3, len(self.calls))
        for saved in receipt_sets(self.run_dir):
            self.assertEqual("opencode", saved["identity"]["provider"])
            self.assertEqual({"ok"}, {row["status"] for row in saved["probed"].values()})

    def test_same_model_at_different_levels_requires_both_receipts(self):
        selection = self.selection(probe=["openai/gpt-6-sol"])
        selection["routes"]["completion"] = {
            "model": "openai/gpt-6-sol",
            "tool": "opencode",
            "reasoning_effort": "high",
        }
        events = {"openai/gpt-6-sol": OK_EVENTS}
        self.assertTrue(self.check(events, selection))
        self.assertEqual({("openai/gpt-6-sol", None), ("openai/gpt-6-sol", "high")}, set(self.calls))
        self.assertFalse(self.check(events, selection))
        selection["routes"]["completion"]["reasoning_effort"] = "max"
        self.assertTrue(self.check(events, selection))
        self.assertEqual(("openai/gpt-6-sol", "max"), self.calls[-1])
        self.assertEqual(3, len(self.calls))

    def test_routes_the_selection_knows_are_not_probed(self):
        selection = self.selection(probe=["openai/gpt-6-sol"])
        events = {"openai/gpt-6-sol": OK_EVENTS}
        self.assertTrue(self.check(events, selection))
        self.assertEqual([("openai/gpt-6-sol", None)], self.calls)

    def test_a_blocking_failure_stops_before_any_stage_spend(self):
        events = {
            "openai/gpt-6-sol": OK_EVENTS,
            "zai-coding-plan/glm-5.3": PAYWALL_EVENTS,
            "xiaomi-token-plan-sgp/mimo-v2.6-pro": OK_EVENTS,
        }
        with self.assertRaises(support.Paused) as caught:
            self.check(events, self.selection())
        self.assertEqual("PAUSED_BUDGET", caught.exception.status)

    def test_saved_routes_skip_a_non_billing_tool(self):
        runner = types.SimpleNamespace(opencode=self.adapter)
        state = {"settings": {"engine": "opencode", "roles": {"terra": {"model": "zai-coding-plan/glm-5.3"}}}}
        events = {"zai-coding-plan/glm-5.3": OK_EVENTS}
        with patch.object(preflight.transport, "execute", side_effect=self.fake_execute(events)) as execute:
            self.assertFalse(preflight.check(runner, state, self.run_dir, self.root))
        execute.assert_not_called()

    def test_selection_groups_resolve_each_tool_and_keep_identities_apart(self):
        selection = self.selection()
        selection["routes"]["terra"] = {
            "model": "kilo/deepseek/deepseek-v4.1-flash",
            "tool": "kilocode",
            "reasoning_effort": "medium",
        }
        runner = types.SimpleNamespace(opencode=self.adapter)
        with patch("providers.command.load") as load:
            load.return_value = types.SimpleNamespace(NAME="kilocode", _config={"command": ["kilo"]})
            groups = preflight._selection_groups(runner, selection, self.root)
        load.assert_called_once_with("kilocode")
        by_provider = {group[2]["provider"]: group for group in groups}
        self.assertEqual({"opencode", "kilocode"}, set(by_provider))
        self.assertEqual("kilo/deepseek/deepseek-v4.1-flash", by_provider["kilocode"][1]["terra"]["model"])


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
