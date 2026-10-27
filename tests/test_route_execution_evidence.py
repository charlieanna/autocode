"""Actual launch identity survives accounting without retrospective route guesses."""

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_provider_launch as launch
import autocode_route_selector as selector
import qualify_routes
from providers.direct import DirectProvider

from . import test_direct_connections as direct_fixtures
from . import test_qualify_routes as qualify_fixtures

GLM = "zai-coding-plan/glm-5.3"


def identity():
    return {
        "schema": 1,
        "role": "glm",
        "engine": "opencode",
        "provider": "kilocode",
        "model": GLM,
        "reasoning_effort": "low",
        "runtime_commit": "a" * 40,
        "billing_kind": "subscription",
        "billing_basis": "saved_selected_route",
        "endpoint": None,
    }


class RouteExecutionEvidenceTests(unittest.TestCase):
    def test_stage_record_keeps_the_frozen_launch_identity_without_mutating_it(self):
        facts = identity()
        worker = {"engine": "opencode", "provider": "kilocode", "execution_identity": facts}
        actual = launch.stage_record(worker)
        self.assertEqual(facts, actual.get("execution_identity"))
        actual["execution_identity"]["model"] = "changed"
        self.assertEqual(GLM, facts["model"])

    def test_qualifier_retains_actual_route_instead_of_later_settings_and_marks_legacy_unknown(self):
        facts = identity()
        source = qualify_fixtures.state()
        source["settings"]["provider"] = "different-current-provider"
        rows = [
            {"role": "glm", "engine": "opencode", "model": GLM, "execution_identity": facts},
            {"role": "glm", "engine": "opencode", "model": GLM, "rejected": True},
        ]
        with patch.object(qualify_routes.usage, "accounting", return_value={"attempts": rows}):
            result = qualify_routes.run_record(source, "glm", GLM, {"date": "2026-10-27", "run": "r"})
        evidence = result.get("execution_evidence")
        self.assertIsInstance(evidence, dict)
        self.assertEqual((1, 1), (evidence["known_attempts"], evidence["unknown_attempts"]))
        self.assertEqual(facts, evidence["observations"][0]["identity"])
        original = copy.deepcopy(result)
        selector.parse(
            {
                "as_of": "x",
                "roles": {"glm": [{"model": GLM, "evidence": "fixture", "provenance": "qualified", "runs": [result]}]},
            }
        )
        self.assertEqual(original, result)

    def test_direct_backend_records_only_safe_configured_endpoint_identity_without_reading_a_key(self):
        import autocode_direct_config as config

        endpoint = "https://models.example.invalid/private-tenant"
        settings = config.validate(direct_fixtures.settings(endpoint))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            with (
                patch.object(config, "load", return_value=settings),
                patch.object(config, "api_key", side_effect=AssertionError("identity read a credential")),
            ):
                adapter = DirectProvider()
                _, _, metadata = adapter.launch("terra", root, root / "run", None, "fixture/producer", "medium", True)
        facts = metadata.get("endpoint_identity")
        self.assertIsInstance(facts, dict)
        self.assertEqual("https://models.example.invalid", facts["origin"])
        self.assertEqual("compatible", facts["protocol"])
        self.assertEqual(64, len(facts["sha256"]))
        self.assertNotIn("private-tenant", json.dumps(facts))

    def test_billing_uses_only_the_exact_selected_transport_not_an_account_pool_or_model_family(self):
        import autocode_route_evidence as evidence

        selected = {"model": GLM, "tool": "kilocode", "billing": "subscription"}
        settings = {"route_selection": {"routes": {"glm": selected}, "candidates": {"glm": [selected]}}}
        options = dict(
            role="glm",
            engine="opencode",
            provider="kilocode",
            model=GLM,
            effort="low",
            runtime_commit="a" * 40,
            settings=settings,
        )
        facts = evidence.launch_identity(**options)
        self.assertEqual(("subscription", "saved_selected_route"), (facts["billing_kind"], facts["billing_basis"]))
        for fault in ("tool", "model", "pool_only", "malformed"):
            with self.subTest(fault=fault):
                changed = copy.deepcopy(settings)
                if fault == "pool_only":
                    changed["route_selection"]["routes"] = {}
                elif fault == "malformed":
                    changed["route_selection"]["routes"] = []
                else:
                    changed["route_selection"]["routes"]["glm"][fault] = "another"
                result = evidence.launch_identity(**{**options, "settings": changed})
                self.assertIsNone(result["billing_kind"])
                self.assertEqual("unrecorded", result["billing_basis"])
        selected["billing"] = "pay per token"
        self.assertEqual("subscription", facts["billing_kind"])

    def test_credentials_queries_and_tenant_paths_do_not_leave_endpoint_facts(self):
        import autocode_route_evidence as evidence

        first = evidence.endpoint_identity("https://example.invalid/private-a", "compatible", "compatible")
        second = evidence.endpoint_identity("https://example.invalid/private-b", "compatible", "compatible")
        self.assertEqual(first["origin"], second["origin"])
        self.assertNotEqual(first["sha256"], second["sha256"])
        for bad in (
            "https://user:secret@example.invalid/v1",
            "https://example.invalid/v1?api_key=secret",
            "https://example.invalid/v1#secret",
            "http://external.invalid/v1",
        ):
            self.assertIsNone(evidence.endpoint_identity(bad, "compatible", "compatible"))

    def test_changed_or_malformed_attempt_bindings_remain_unknown(self):
        import autocode_route_evidence as evidence

        row = {"role": "glm", "engine": "opencode", "model": GLM, "execution_identity": identity()}
        for fault in ("role", "model", "engine", "effort", "version", "secret", "runtime"):
            with self.subTest(fault=fault):
                changed = copy.deepcopy(row)
                if fault in ("role", "model", "engine"):
                    changed[fault] = "different"
                elif fault == "effort":
                    changed["reasoning_effort"] = "high"
                elif fault == "version":
                    changed["execution_identity"]["schema"] = 2
                elif fault == "secret":
                    changed["execution_identity"]["api_key"] = "must-not-escape"
                elif fault == "runtime":
                    changed["execution_identity"]["runtime_commit"] = "not-a-commit"
                snapshot = evidence.execution_snapshot([changed], accepted=qualify_routes.accepted)
                self.assertEqual(
                    (0, 1, []), (snapshot["known_attempts"], snapshot["unknown_attempts"], snapshot["observations"])
                )

    def test_optional_execution_records_reject_wrong_role_model_counts_and_duplicate_groups(self):
        rows = [{"role": "glm", "engine": "opencode", "model": GLM, "execution_identity": identity()}]
        with patch.object(qualify_routes.usage, "accounting", return_value={"attempts": rows}):
            good = qualify_routes.run_record(qualify_fixtures.state(), "glm", GLM, {"date": "2026-10-27", "run": "r"})
        for fault in ("role", "model", "count", "duplicate", "endpoint"):
            with self.subTest(fault=fault):
                result = copy.deepcopy(good)
                context = result["execution_evidence"]
                facts = context["observations"][0]["identity"]
                if fault in ("role", "model"):
                    facts[fault] = "unrelated"
                elif fault == "count":
                    context["unknown_attempts"] = 1
                elif fault == "duplicate":
                    context["observations"].append(copy.deepcopy(context["observations"][0]))
                elif fault == "endpoint":
                    facts["endpoint"] = {
                        "origin": "https://example.invalid/private",
                        "sha256": "a" * 64,
                        "protocol": "compatible",
                        "api_provider": "compatible",
                    }
                with self.assertRaisesRegex(ValueError, "runs must be records"):
                    selector.parse(
                        {
                            "as_of": "x",
                            "roles": {
                                "glm": [
                                    {"model": GLM, "evidence": "fixture", "provenance": "qualified", "runs": [result]}
                                ]
                            },
                        }
                    )

    def test_effective_effort_is_not_replaced_by_the_configured_route_effort(self):
        import autocode_usage as usage

        source = {
            "stages": [
                {
                    "attempt_id": "001/req-01",
                    "role": "glm",
                    "stage": "glm_revise",
                    "engine": "opencode",
                    "reasoning_effort": "low",
                    "launch_route": {"model": GLM, "reasoning_effort": "high"},
                    "execution_identity": identity(),
                }
            ]
        }
        row = usage.accounting(source)["attempts"][0]
        self.assertEqual("low", row["execution_identity"]["reasoning_effort"])
        self.assertEqual("low", row["reasoning_effort"])
        self.assertEqual("high", row["launch_route"]["reasoning_effort"])

    def test_explicit_blank_provider_default_effort_is_preserved(self):
        facts = identity()
        facts["reasoning_effort"] = ""
        worker = {"engine": "opencode", "provider": "kilocode", "execution_identity": facts}
        self.assertEqual("", launch.stage_record(worker)["execution_identity"]["reasoning_effort"])
