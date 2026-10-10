"""Nonsecret connection-summary checks; no network or credential-file reads."""

import subprocess
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode_opencode as oc


class OpenCodeSubscriptionTests(unittest.TestCase):
    roles = {"terra": {"model": "openai/gpt-5.6-terra"}}

    def test_oauth_summary_is_accepted_without_reading_credentials(self):
        response = SimpleNamespace(returncode=0, stdout="● Z.AI Coding Plan api\n● OpenAI \x1b[90moauth\n", stderr="")
        with (
            patch.object(oc.time, "monotonic", side_effect=[100, 100]),
            patch.object(oc.subprocess, "run", return_value=response) as run,
            patch.object(Path, "read_text", side_effect=AssertionError("Do not read auth files")),
        ):
            oc.check_subscription_routes(self.roles, Path("/workspace"))
        self.assertEqual(["opencode", "auth", "list"], run.call_args.args[0])
        self.assertEqual(45, run.call_args.kwargs["timeout"])

    def test_missing_ambiguous_and_unrecognized_auth_cannot_launch(self):
        for summary in (
            "● OpenAI unknown",
            "",
            "OAuth exists somewhere",
            "● Other OpenAI oauth",
            "● OpenAI oauth\n● OpenAI api",
        ):
            with (
                self.subTest(summary=summary),
                patch.object(
                    oc.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=summary, stderr="")
                ),
                self.assertRaisesRegex(RuntimeError, "configured OAuth or API connection"),
            ):
                oc.check_subscription_routes(self.roles)
        with (
            patch.object(
                oc.subprocess, "run", return_value=SimpleNamespace(returncode=1, stdout="● OpenAI oauth", stderr="")
            ),
            self.assertRaises(RuntimeError),
        ):
            oc.check_subscription_routes(self.roles)

    def test_timeout_and_missing_cli_do_not_fallback_or_expose_output(self):
        for error in (subprocess.TimeoutExpired(["opencode"], 15), FileNotFoundError("missing")):
            with (
                patch.object(oc.subprocess, "run", side_effect=error) as run,
                self.assertRaisesRegex(RuntimeError, "no provider request"),
            ):
                oc.check_subscription_routes(self.roles)
            # Every transport failure is retried before the guard concludes
            # anything; the conclusion is still "cannot verify", never a guess.
            self.assertEqual(3, run.call_count)

    def test_slow_auth_probe_gets_the_full_startup_allowance(self):
        ok = SimpleNamespace(returncode=0, stdout="● OpenAI oauth\n", stderr="")

        def cold_start(*args, **kwargs):
            if kwargs["timeout"] < 32:
                raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])
            return ok

        with (
            patch.object(oc.time, "monotonic", side_effect=[100, 100]),
            patch.object(oc.subprocess, "run", side_effect=cold_start) as run,
        ):
            oc.check_subscription_routes(self.roles)
        self.assertEqual(1, run.call_count)
        self.assertEqual(45, run.call_args.kwargs["timeout"])

    def test_early_transport_failure_retries_with_remaining_budget(self):
        ok = SimpleNamespace(returncode=0, stdout="● OpenAI oauth\n", stderr="")
        with (
            patch.object(oc.time, "monotonic", side_effect=[100, 100, 112]),
            patch.object(oc.subprocess, "run", side_effect=[OSError("transient"), ok]) as run,
        ):
            oc.check_subscription_routes(self.roles)
        self.assertEqual([45, 33], [call.kwargs["timeout"] for call in run.call_args_list])

    def test_exhausted_budget_never_starts_another_probe(self):
        with (
            patch.object(oc.time, "monotonic", side_effect=[100, 100, 145]),
            patch.object(oc.subprocess, "run", side_effect=subprocess.TimeoutExpired(["opencode"], 45)) as run,
            self.assertRaisesRegex(RuntimeError, "no provider request"),
        ):
            oc.check_subscription_routes(self.roles)
        self.assertEqual(1, run.call_count)

    def test_completed_auth_check_is_never_retried(self):
        for response in (
            SimpleNamespace(returncode=0, stdout="● OpenAI unknown\n", stderr=""),
            SimpleNamespace(returncode=1, stdout="● OpenAI oauth\n", stderr=""),
        ):
            with patch.object(oc.subprocess, "run", return_value=response) as run, self.assertRaises(RuntimeError):
                oc.check_subscription_routes(self.roles)
            self.assertEqual(1, run.call_count)

    def test_existing_zai_routes_do_not_gain_an_auth_probe(self):
        with patch.object(oc.subprocess, "run", side_effect=AssertionError("Unrelated routes remain unchanged")):
            oc.check_subscription_routes({"terra": {"model": "zai-coding-plan/glm-5.3"}})

    def test_api_summary_is_accepted(self):
        with patch.object(
            oc.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="● OpenAI api", stderr="")
        ):
            oc.check_subscription_routes(self.roles)

    def test_configured_api_environment_is_accepted_for_all_roles_without_probe(self):
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
            for role in ("glm", "astra", "terra", "sol"):
                with patch.dict(oc.os.environ, {key: "fixture-only"}), patch.object(oc.subprocess, "run") as command:
                    oc.check_subscription_routes({role: {"model": "openai/gpt-5.6-sol"}})
                    command.assert_not_called()


if __name__ == "__main__":
    unittest.main()
