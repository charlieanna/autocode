"""Escalation scenarios through the real CLI (#612): one that converts, one that reaches the ladder's end,
and a rung the provider does not serve, refused when the run is created.

The first two run the catalog scenarios escalation-converts and escalation-ladder-end with the scripted
model (about 35 s each), require PASS, then read the run as a person would: ``autocode --status --json``,
whose view lists the escalation as a row of ``escalations`` and whose stderr says it in a line. The third
puts the scripted provider behind a configured tool that lists its models (the stand-in a hybrid rehearsal
runs on, harness/hybrid.py) and names a stronger Builder model that tool does not list.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from harness import catalog, hybrid, profiles  # noqa: E402
from harness.driver import FAKE_BILLING_ENV, STRIP_BILLING_AUTH, default_autocode, fake_setup  # noqa: E402
from harness.processes import run_cli  # noqa: E402
from harness.project import materialize  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
# How the Builder climbed in both scenarios: the driver's default Builder route to the retry policy's default
# stronger attempt, as `--status` names them.
CLIMB = "Builder: gpt-5.6-terra at medium → gpt-6-sol at xhigh after execution failure; 1 attempt, $"


class EscalationScenarioTests(unittest.TestCase):
    def scenario(self, scenario_id: str) -> tuple[dict, dict, str]:
        """Run a catalog scenario with the scripted model: its result.json, then its run's view and stderr from
        ``autocode --status --json``."""
        directory = tempfile.TemporaryDirectory(prefix="escalation-scenario-")
        self.addCleanup(directory.cleanup)
        root = Path(directory.name)
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "AUTOCODE_HOME": str(root / "registry")}
        command = [sys.executable, str(REPO / "scenarios/run.py"), "run", scenario_id, "--fake", "--out", str(root)]
        completed = run_cli([*command, "--timeout-minutes", "8"], env=env, cwd=REPO, timeout=600)
        self.assertEqual(0, completed.returncode, completed.stdout + completed.stderr)
        [saved] = root.glob("*/result.json")
        result = json.loads(saved.read_text())
        self.assertEqual("PASS", result["verdict"], result["summary"])
        self.assertTrue(all(row["ok"] for row in result["checks"]), result["checks"])
        project = Path(result["evidence"]) / "project"
        status = run_cli(
            [*default_autocode(), "--workspace", str(project), "--run-dir", result["run_dir"], "--status", "--json"],
            env=env,
            cwd=REPO,
            timeout=60,
        )
        self.assertEqual(0, status.returncode, status.stdout + status.stderr)
        return result, json.loads(status.stdout)["view"], status.stderr

    def test_an_escalation_that_converts_is_listed_as_passed(self):
        result, view, said = self.scenario("escalation-converts")
        self.assertEqual("TASK_COMPLETE", result["runner_status"])
        [row] = view["escalations"]
        self.assertEqual(("passed", "validation"), (row["outcome"], row["outcome_basis"]))
        # The runner's own stamps agree with the row: the ordinary retry and the stronger attempt passed.
        self.assertEqual({"passed": 2, "paused": 0, "open": 0}, view["escalation_outcomes"])
        self.assertIn("Escalations: 1 (1 passed).", said)
        self.assertRegex(said, "  " + CLIMB.replace("$", r"\$") + r"[0-9.]+; passed\.")

    def test_an_escalation_that_reaches_the_ladders_end_pauses_with_the_diagnosis(self):
        result, view, said = self.scenario("escalation-ladder-end")
        self.assertEqual("PAUSED_BUILDER_RETRY_LIMIT", result["runner_status"])
        [row] = view["escalations"]
        self.assertEqual(("paused", "recorded"), (row["outcome"], row["outcome_basis"]))
        self.assertEqual({"passed": 0, "paused": 2, "open": 0}, view["escalation_outcomes"])
        self.assertIn("Investigator (paused): greet.py still guards only the argument count", view["stop_reason"])
        self.assertEqual("resume", view["needs"]["kind"], view["needs"])
        self.assertIn("Escalations: 1 (1 paused).", said)
        self.assertRegex(said, "  " + CLIMB.replace("$", r"\$") + r"[0-9.]+; the run paused\.")


class UnservedRungTests(unittest.TestCase):
    """A rung the provider does not serve is refused when the run is created, not when the climb launches."""

    def setUp(self):
        directory = tempfile.TemporaryDirectory(prefix="unserved-rung-")
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.scenario = catalog.load("escalation-converts")

    def launch(self, strong_model: str):
        """A new run of the scenario's brief on the stand-in tool, naming ``strong_model`` as the Builder's stronger
        model: (the CLI's result, the scripted provider's session files, the view of each run it saved)."""
        root = self.root / strong_model
        project = materialize(self.scenario.seed, root / "project")
        _, fake_env = fake_setup(self.scenario, root, self.scenario.reference)
        tools = root / "config" / "autocode" / "providers"
        tools.mkdir(parents=True)
        tool = hybrid.standin_tool([sys.executable, str(root / "bin" / "codex")])
        (tools / f"{hybrid.STANDIN}.toml").write_text(hybrid.toml(tool))
        env = {
            **os.environ,
            **fake_env,
            "XDG_CONFIG_HOME": str(root / "config"),
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        for key in (STRIP_BILLING_AUTH, *FAKE_BILLING_ENV):
            env.pop(key, None)
        flags = profiles.flags({"provider": hybrid.STANDIN, "models": hybrid.STANDIN_MODELS})
        completed = run_cli(
            [
                *default_autocode(),
                self.scenario.brief,
                "--workspace",
                str(project),
                "--in-place",
                "--no-chat",
                *flags,
                "--evidence-provenance",
                "fake",
                "--builder-strong-model",
                strong_model,
            ],
            env=env,
            cwd=root,
            timeout=300,
        )
        sessions = sorted((root / "fake-sessions").glob("*")) if (root / "fake-sessions").is_dir() else []
        views = []
        for saved in sorted((project / ".autocode" / "runs").glob("*/state.json")):
            run_dir = str(saved.parent)
            status = run_cli(
                [*default_autocode(), "--workspace", str(project), "--run-dir", run_dir, "--status", "--json"],
                env=env,
                cwd=root,
                timeout=60,
            )
            views.append(json.loads(status.stdout)["view"])
        return completed, sessions, views

    def test_a_stronger_builder_model_the_tool_does_not_list_refuses_the_new_run(self):
        listed = sorted(hybrid.STANDIN_MODELS.values())
        self.assertNotIn("standin-stronger", listed)
        refused, sessions, views = self.launch("standin-stronger")
        # Exit 2 also ends an accepted run waiting for plan approval (below), so the message and the empty
        # sessions and saved runs are what tell a refusal apart.
        self.assertEqual(2, refused.returncode, refused.stdout + refused.stderr)
        self.assertIn(
            f"--builder-strong-model standin-stronger is not a standin model: {', '.join(listed)}", refused.stderr
        )
        self.assertEqual(([], []), (sessions, views), "nothing may be launched or saved before the refusal")
        # The same request naming a model the tool lists is created and launched: the refusal is the rung's.
        launched, sessions, views = self.launch("standin-resolver")
        self.assertEqual(2, launched.returncode, launched.stdout + launched.stderr)
        self.assertTrue(sessions, "the scripted provider was never called")
        [view] = views
        self.assertEqual("AWAITING_GOAL_APPROVAL", view["status"], view)


if __name__ == "__main__":
    unittest.main()
