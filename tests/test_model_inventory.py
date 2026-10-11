"""One startup roster still validates every route; later invocations ask anew."""

import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_taskrun as taskrun
import model_catalogue
from autocode_model_inventory import InvocationInventory
from providers import opencode
from providers.command import CommandProvider

from tests import test_model_catalogue as catalogue_fixture
from tests import test_opencode as opencode_fixture


class ModelInventoryTests(unittest.TestCase):
    def test_successful_selection_and_full_validation_use_one_native_roster(self):
        workspace = Path(tempfile.gettempdir())
        inventory = InvocationInventory(opencode, workspace)
        available = catalogue_fixture.ChooseTest.AVAILABLE
        response = subprocess.CompletedProcess([], 0, "\n".join(sorted(available)), "")
        with patch.object(opencode.env_prep, "preflight_run", return_value=response) as roster:
            settings = model_catalogue.choose(
                catalogue_fixture.new_settings(), opencode, workspace, interactive=False, inventory=inventory
            )
            inventory.check_models(settings["roles"])
            self.assertEqual(1, roster.call_count)
            # Consumed observations cannot exempt a later route validation.
            with self.assertRaisesRegex(RuntimeError, "Models unavailable in OpenCode"):
                inventory.check_models({"investigator": {"model": "not/offered"}})
            self.assertEqual(2, roster.call_count)
            fresh = InvocationInventory(opencode, workspace)
            fresh.check_models(settings["roles"])
            self.assertEqual(3, roster.call_count)

    def test_roster_copy_cannot_skip_a_later_unlisted_job_route(self):
        workspace = Path(tempfile.gettempdir())
        original = set(catalogue_fixture.ChooseTest.AVAILABLE)
        inventory = InvocationInventory(opencode, workspace)
        with patch.object(opencode, "available_models", return_value=original) as roster:
            returned = inventory.available_models(workspace)
            returned.add("not/offered")
            original.add("not/offered")
            with self.assertRaisesRegex(RuntimeError, "Models unavailable in OpenCode: not/offered"):
                inventory.check_models({"investigator": {"model": "not/offered"}})
            self.assertEqual(1, roster.call_count)

    def test_failed_best_effort_selection_does_not_hide_hard_prelaunch_failure(self):
        workspace = Path(tempfile.gettempdir())
        inventory = InvocationInventory(opencode, workspace)
        with patch.object(opencode, "available_models", side_effect=RuntimeError("catalogue timed out")) as roster:
            model_catalogue.choose(
                catalogue_fixture.new_settings(), opencode, workspace, interactive=False, inventory=inventory
            )
            with self.assertRaisesRegex(RuntimeError, "catalogue timed out"):
                inventory.check_models(catalogue_fixture.new_settings()["roles"])
            self.assertEqual(2, roster.call_count)

    def test_a_failed_newer_observation_cannot_reuse_an_older_success(self):
        workspace = Path(tempfile.gettempdir())
        inventory = InvocationInventory(opencode, workspace)
        with patch.object(
            opencode,
            "available_models",
            side_effect=[
                catalogue_fixture.ChooseTest.AVAILABLE,
                RuntimeError("new listing failed"),
                RuntimeError("validation failed"),
            ],
        ) as roster:
            inventory.available_models(workspace)
            with self.assertRaisesRegex(RuntimeError, "new listing failed"):
                inventory.available_models(workspace)
            with self.assertRaisesRegex(RuntimeError, "validation failed"):
                inventory.check_models(catalogue_fixture.new_settings()["roles"])
            self.assertEqual(3, roster.call_count)

    def test_a_different_workspace_observation_is_not_reused(self):
        with tempfile.TemporaryDirectory() as directory:
            workspace = Path(directory)
            inventory = InvocationInventory(opencode, workspace)
            with patch.object(
                opencode, "available_models", return_value=catalogue_fixture.ChooseTest.AVAILABLE
            ) as roster:
                inventory.available_models(workspace / "other")
                inventory.check_models(catalogue_fixture.new_settings()["roles"])
                self.assertEqual(2, roster.call_count)

    def test_configured_provider_reuses_models_command_but_preserves_name_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / "provider.toml"
            config.write_text(
                'name = "test"\ncommand = ["test", "{prompt}"]\noutput = "report_file"\nmodels_command = ["test", "models"]\n'
            )
            provider = CommandProvider(
                {"name": "test", "command": ["test"], "models_command": ["test", "models"], "roles": {}}, config
            )
            inventory = InvocationInventory(provider, Path(directory))
            response = subprocess.CompletedProcess([], 0, "model-a\nmodel-b\n", "")
            with patch.object(opencode.env_prep, "preflight_run", return_value=response) as roster:
                inventory.available_models(Path(directory))
                with self.assertRaisesRegex(RuntimeError, "Models unavailable in test: missing"):
                    inventory.check_models({"sol": {"model": "missing"}})
                self.assertEqual(1, roster.call_count)
                with self.assertRaisesRegex(RuntimeError, "non-empty and contain no whitespace"):
                    InvocationInventory(provider, Path(directory)).check_models({"sol": {"model": "bad model"}})
                self.assertEqual(2, roster.call_count)


class NativeStartupInventoryTests(unittest.TestCase):
    # Reuse only fixture setup, never another test or a real provider.
    setUp = opencode_fixture.OpenCodeFlow.setUp

    def test_public_start_lists_once_and_resume_refuses_a_disappeared_route(self):
        observations = self.root / "roster-observations.txt"
        removed = self.root / "remove-model"
        bootstrap = self.root / "counting-bootstrap.py"
        repo = Path(__file__).resolve().parents[1]
        bootstrap.write_text(
            textwrap.dedent(f"""
            import sys
            from pathlib import Path
            from unittest.mock import patch
            sys.path.insert(0, {str(repo)!r})
            sys.path.insert(0, {str(repo / "tools")!r})
            from tests import opencode_fixture_cli
            from providers import env_prep
            original = env_prep.preflight_run
            def observed(command, *args, **kwargs):
                result = original(command, *args, **kwargs)
                if list(command)[-1:] == ["models"]:
                    with Path({str(observations)!r}).open("a") as handle:
                        handle.write("models\\n")
                    if Path({str(removed)!r}).exists():
                        result.stdout = "\\n".join(line for line in result.stdout.splitlines() if line != "openai/gpt-6-sol")
                return result
            with patch.object(env_prep, "preflight_run", observed):
                opencode_fixture_cli.main()
        """)
        )
        run = taskrun.TaskRun.start(
            self.project,
            "Greeting tool",
            command=[sys.executable, str(bootstrap), str(repo / "tools/autocode.py")],
            options=("--evidence-provenance", "fake", "--allow-uncontained-tools"),
            start_options=("--pause-after-stage",),
            env=self.env,
            cwd=self.root,
            timeout=60,
        )
        self.assertEqual(["models"], observations.read_text().splitlines())
        started = run.status()
        attempts = started["usage"]["accounting"]["attempts"]
        self.assertTrue(attempts, "The real CLI reached its first fixture provider dispatch")
        self.assertIsNone(started["interaction_timing"]["first_builder_at"])
        self.assertEqual(["models"], observations.read_text().splitlines(), "Status reads do not probe the catalogue")
        removed.touch()
        resumed = run.resume_paused()
        self.assertEqual(["models", "models"], observations.read_text().splitlines())
        self.assertEqual("PAUSED_INVALID_OUTPUT", resumed["status"])
        self.assertIn("Models unavailable in OpenCode: openai/gpt-6-sol", resumed["stop_reason"])
        self.assertEqual(
            attempts, resumed["usage"]["accounting"]["attempts"], "Unavailable routes launch no further models"
        )
        self.assertEqual(started["interaction_timing"], resumed["interaction_timing"])


if __name__ == "__main__":
    unittest.main()
