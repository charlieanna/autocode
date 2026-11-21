"""Regression tests for partial --<role>-model effort fitting and deterministic Model preflight stops."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_route_fit as route_fit
import autocode_support as support
import autopilot


class SharedCauseLPartialModelOverrideEffortFitTests(unittest.TestCase):
    """Task 2A: partial --<role>-model override fits untouched default effort while preserving explicit PAUSED_EFFORT_UNSUPPORTED."""

    def test_partial_completion_model_override_fits_implicit_default_effort_and_launches_kilo_cleanly(self):
        import autocode_args
        import autocode_configure as configure
        import autocode_provider_launch as provider_launch
        import autocode_route_ladder as route_ladder
        import autocode_stage_choices as stage_choices
        from providers import kilocode

        listing = {
            "tool": "kilocode",
            "accounts": [
                {"name": "OpenAI", "mode": "oauth", "billing": "subscription", "prefixes": ["openai/"]},
                {
                    "name": "Z.AI Coding Plan",
                    "mode": "api",
                    "billing": "subscription",
                    "prefixes": ["zai-coding-plan/"],
                },
            ],
            "candidates": [
                {
                    "model": "openai/gpt-6.1-terra",
                    "tool": "kilocode",
                    "account": "OpenAI",
                    "billing": "subscription",
                    "levels": ["low", "medium", "high", "xhigh"],
                    "probe": None,
                    "metadata": {"family": "gpt", "toolcall": True},
                },
                {
                    "model": "openai/gpt-6-sol",
                    "tool": "kilocode",
                    "account": "OpenAI",
                    "billing": "subscription",
                    "levels": ["low", "medium", "high", "xhigh", "max"],
                    "probe": None,
                    "metadata": {"family": "gpt", "toolcall": True},
                },
                {
                    "model": "zai-coding-plan/glm-5.3",
                    "tool": "kilocode",
                    "account": "Z.AI Coding Plan",
                    "billing": "subscription",
                    "levels": ["low", "high", "max"],
                    "probe": None,
                    "metadata": {"family": "glm", "toolcall": True},
                },
            ],
        }
        kilo_provider = kilocode.load("kilocode")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            stages_path = root / "stages.toml"
            stage_choices.save(
                {
                    "kilocode": {
                        "requirements": {"model": "zai-coding-plan/glm-5.3", "effort": "high"},
                        "planner": {"model": "zai-coding-plan/glm-5.3", "effort": "high"},
                        "plan_reviewer": {"model": "openai/gpt-6-sol", "effort": "high"},
                        "task_planner": {"model": "openai/gpt-6-sol", "effort": "high"},
                    }
                },
                stages_path,
            )
            argv = [
                "Build subsystem",
                "--workspace",
                str(root),
                "--in-place",
                "--no-chat",
                "--provider",
                "kilocode",
                "--builder-model",
                "openai/gpt-6.1-terra",
                "--tester-model",
                "zai-coding-plan/glm-5.3",
                "--completion-model",
                "zai-coding-plan/glm-5.3",
            ]
            args, _ = autocode_args.parse(None, argv, kilo_provider.DEFAULT_MODELS)
            stage_choices.apply(args, "kilocode", file=stages_path)
            with (
                patch.object(kilo_provider, "local_settings", return_value={"engine": "kilocode", "version": "7.8.3"}),
                patch("autocode_providers.resolve", return_value=kilo_provider),
                patch("autocode_route_discovery.discover", return_value=listing),
            ):
                settings = configure.configure(
                    args,
                    {"workspace": str(root), "iteration": 0, "task": "Build subsystem"},
                    planning=runner.planning,
                    milestones=runner.milestones,
                    autopilot=autopilot,
                    opencode=kilo_provider,
                )
            # Untouched completion effort ("medium" in kilocode.toml) fitted to GLM-5.3's declared "high":
            self.assertEqual("runner_default", settings["effort_origins"]["completion"])
            self.assertEqual("high", settings["roles"]["completion"]["reasoning_effort"])
            self.assertEqual(
                {"from": "medium", "to": "high", "reason": "route_fit"},
                settings["effort_fits"]["completion"],
            )
            self.assertEqual(["low", "high", "max"], settings["declared_variants"]["zai-coding-plan/glm-5.3"])
            # Dynamic ladder climb works from fitted "high" -> "max":
            climb = route_ladder.next_route(settings, "completion")
            self.assertEqual("max", climb["reasoning_effort"])
            # KiloProvider launch succeeds without KiloEffortError / PAUSED_TRANSPORT_UNVERIFIED:
            run_dir = root / "run"
            run_dir.mkdir()
            prompt_file = run_dir / "prompt.md"
            prompt_file.write_text("Review completion")
            effort = route_fit.launch(
                settings,
                "completion",
                settings["roles"]["completion"]["reasoning_effort"],
                "review_completion",
                model="zai-coding-plan/glm-5.3",
            )
            self.assertEqual("high", effort)
            with patch(
                "providers.kilocode_policy.restrict",
                side_effect=lambda argv, child, *_a, **_k: (argv, child),
            ):
                cmd, _env, _ov, _worker = provider_launch.prepare(
                    engine="opencode",
                    adapter=kilo_provider,
                    role="completion",
                    route_role="completion",
                    workspace=root,
                    run_dir=run_dir,
                    session=None,
                    model="zai-coding-plan/glm-5.3",
                    effort=effort,
                    allow_write=False,
                    planning=False,
                    report=run_dir / "out.json",
                    schema={"type": "object"},
                    prompt_file=prompt_file,
                    sandbox="read-only",
                    transport_args=(),
                    chatgpt=False,
                    provider="kilocode",
                    enforce_tool_boundary=False,
                    settings=settings,
                )
            self.assertIn("high", cmd)

            # Explicitly typed unsupported --completion-reasoning-effort medium STILL pauses PAUSED_EFFORT_UNSUPPORTED!
            explicit_args, _ = autocode_args.parse(
                None, [*argv, "--completion-reasoning-effort", "medium"], kilo_provider.DEFAULT_MODELS
            )
            stage_choices.apply(explicit_args, "kilocode", file=stages_path)
            with (
                patch.object(kilo_provider, "local_settings", return_value={"engine": "kilocode", "version": "7.8.3"}),
                patch("autocode_providers.resolve", return_value=kilo_provider),
                patch("autocode_route_discovery.discover", return_value=listing),
            ):
                explicit_settings = configure.configure(
                    explicit_args,
                    {"workspace": str(root), "iteration": 0, "task": "Build subsystem"},
                    planning=runner.planning,
                    milestones=runner.milestones,
                    autopilot=autopilot,
                    opencode=kilo_provider,
                )
            self.assertEqual("user_explicit", explicit_settings["effort_origins"]["completion"])
            self.assertEqual("medium", explicit_settings["roles"]["completion"]["reasoning_effort"])
            with self.assertRaises(route_fit.EffortUnsupported) as caught:
                route_fit.guard(explicit_settings)
            self.assertEqual("PAUSED_EFFORT_UNSUPPORTED", caught.exception.status)
            self.assertIn("--completion-reasoning-effort", str(caught.exception))


class SharedCauseMModelPreflightDeterministicUnavailableTests(unittest.TestCase):
    """Task 2B: Model preflight stops cleanly on deterministic model_not_found / account-unsupported errors."""

    def test_borg_spark_account_unsupported_probe_stops_at_preflight_with_zero_priced_probe(self):
        import types

        import autocode_model_preflight as preflight
        import autocode_usage as usage

        from .test_model_unavailable import BORG_SPARK_ERROR

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run_dir = root / "run"
            run_dir.mkdir()
            identity = {
                "engine": "kilocode",
                "identity_version": 1,
                "executable": "/usr/bin/kilo",
                "version": "7.8.3",
                "config_path": "/cfg/kilocode.toml",
                "config_sha256": "a" * 64,
            }
            adapter = types.SimpleNamespace(
                NAME="kilocode",
                _config={"auth": {"command": ["kilo", "auth", "list"], "routes": []}},
                check_subscription_routes=lambda roles, workspace=None, env=None: None,
            )
            state = {
                "next_stage": "requirements_gather",
                "settings": {
                    "engine": "opencode",
                    "provider": "kilocode",
                    "roles": {
                        "requirements": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                        "planner": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                        "builder": {"model": "openai/gpt-5.3-codex-spark", "reasoning_effort": "medium"},
                        "tester": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "high"},
                    },
                    "transport_identities": {"opencode": identity},
                },
            }
            probed_models = []

            def fake_execute(**kwargs):
                probed_models.append(kwargs["model"])
                kwargs["directory"].mkdir(parents=True, exist_ok=True)
                rows = [BORG_SPARK_ERROR]
                (kwargs["directory"] / "events.jsonl").write_text(json.dumps(BORG_SPARK_ERROR) + "\n")
                return {"exit_code": 0, "timed_out": False, "report": None, "rows": rows, "metrics": {}}

            with patch.object(preflight.transport, "execute", side_effect=fake_execute):
                with self.assertRaises(support.Paused) as caught:
                    preflight.check(types.SimpleNamespace(opencode=adapter), state, run_dir, root)
            self.assertEqual("PAUSED_BILLING_ROUTE", caught.exception.status)
            self.assertIn("openai/gpt-5.3-codex-spark", str(caught.exception))
            self.assertIn("role: builder", str(caught.exception))
            # The stop names the access check and its fixed cause; the provider's words stay in the probe's
            # saved record and evidence (#3888).
            self.assertIn("model access check", str(caught.exception))
            self.assertIn("billing route", str(caught.exception))
            self.assertNotIn("not supported when using Codex with a ChatGPT account", str(caught.exception))
            # Stopped immediately on the rejected Builder probe before spending on GLM probes or planning!
            self.assertEqual(["openai/gpt-5.3-codex-spark"], probed_models)
            # Refused 0-token probe bills $0.00 (never PAUSED_USAGE_UNKNOWN):
            totals = usage.summary(state)
            self.assertEqual(0, totals["unknown_stages"])
            self.assertTrue(totals["cost_usd"]["complete"])


if __name__ == "__main__":
    unittest.main()
