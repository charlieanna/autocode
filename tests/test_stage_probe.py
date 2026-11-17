"""Stage models offers to measure each probed stage's models, and runs only what the person ticked (#3630 step 2)."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_measured_routes as measured_routes
import autocode_model_probe as model_probe
import autocode_probe_cli as probe_cli
import autocode_stage_choices as choices
import autocode_stage_probe as stage_probe

from tests.test_stage_choices import candidate, resolve

GLM, SOL, OPUS = "zai-coding-plan/glm-5.3", "openai/gpt-6-sol", "anthropic/claude-opus-5-5"
LISTING = {
    "candidates": [
        candidate(GLM, ["low", "high", "max"], family="glm", toolcall=True, context=200000),
        candidate(SOL, ["medium", "high", "xhigh"], family="gpt", toolcall=True, context=400000),
        candidate(OPUS, ["low", "medium", "high"], family="claude", toolcall=True, context=200000),
        candidate("openai/gpt-6-mini", ["low", "medium"], family="gpt", toolcall=True, context=400000),
    ]
}


def fill(command, ticked, cap=None):
    """A client's fill: ``MEASURE`` becomes one ``--measure MODEL LEVEL`` per ticked candidate, ``CAP`` the cap."""
    argv = []
    for word in command:
        if word == stage_probe.MEASURE:
            for row in ticked:
                argv += ["--measure", row["model"], row["effort"] or stage_probe.DEFAULT_LEVEL]
        else:
            argv.append(cap if word == stage_probe.CAP else word)
    return argv


def cli(argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        code = probe_cli.cli(argv[1:])
    return code, out.getvalue()


class StageProbeOfferTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "autocode" / "stages.toml"
        self.kw = {"file": self.file, "resolve": resolve, "discover": lambda provider, workspace: LISTING}
        patcher = mock.patch.dict("os.environ", {"XDG_CONFIG_HOME": folder.name})
        patcher.start()
        self.addCleanup(patcher.stop)

    def stage(self, current, role):
        return next(stage for stage in current["stages"] if stage["id"] == role)

    def test_the_probed_stages_are_the_ones_autocode_probe_measures(self):
        self.assertEqual(set(stage_probe.PROBED), set(model_probe.STAGES))
        self.assertEqual(set(stage_probe.PINNED), set(model_probe.STAGES))

    def test_each_probed_stage_offers_its_models_with_the_shortlist_ticked(self):
        current = choices.view("opencode", **self.kw)
        offered = {stage["id"] for stage in current["stages"] if stage["probe"]}
        self.assertEqual(set(stage_probe.PROBED), offered)
        self.assertIsNone(self.stage(current, "tester")["probe"], "no Tester probe yet: nothing is offered")
        probe = self.stage(current, "requirements")["probe"]
        rows = {row["model"]: row for row in probe["candidates"]}
        self.assertEqual({GLM, SOL, OPUS, "openai/gpt-6-mini"}, set(rows))
        self.assertTrue(rows[GLM]["ticked"] and rows[SOL]["ticked"] and rows[OPUS]["ticked"])
        self.assertFalse(rows["openai/gpt-6-mini"]["ticked"], "a narrow cut is listed but not suggested")
        self.assertIn("narrow variant", rows["openai/gpt-6-mini"]["reason"])
        self.assertEqual([True, True, True, False], [row["ticked"] for row in probe["candidates"]], "ticked first")
        # Each candidate runs at the stage's default level, moved to the nearest it offers: GLM has no medium.
        self.assertEqual(("high", "medium", "medium"), (rows[GLM]["effort"], rows[SOL]["effort"], rows[OPUS]["effort"]))
        self.assertEqual(
            ["probe", "--stage", "requirements", "--provider", "opencode", "--json", "MEASURE"],
            probe["estimate"]["next_command"],
        )
        self.assertEqual([*probe["estimate"]["next_command"], "--max-cost-usd", "CAP"], probe["run"]["next_command"])

    def test_a_probe_pins_the_routes_the_list_shows_for_the_stages_it_needs(self):
        choices.choose("opencode", {"tester": (OPUS, "high")}, **self.kw)
        current = choices.view("opencode", **self.kw)
        builder = self.stage(current, "builder")["probe"]["estimate"]["next_command"]
        self.assertEqual(
            ["--planner", GLM, "high", "--checker", OPUS, "high", "--requirements", GLM, "high"],
            builder[5:14],
        )
        planner = self.stage(current, "planner")["probe"]["estimate"]["next_command"]
        self.assertIn("--checker", planner)
        self.assertEqual([GLM, "high"], planner[planner.index("--checker") + 1 :][:2])
        reviewer = self.stage(current, "plan_reviewer")["probe"]["estimate"]["next_command"]
        self.assertEqual([GLM, "high"], reviewer[reviewer.index("--planner") + 1 :][:2])

    def test_a_stage_whose_pinned_route_has_no_model_offers_no_run(self):
        offer = stage_probe.offer(
            "builder",
            [{"model": GLM, "levels": ["low", "high"], "levels_listed": True}],
            {"planner": {"model": GLM, "effort": "high"}, "tester": {}},
            backend="opencode",
            default_effort="medium",
        )
        self.assertIsNone(offer["run"])
        self.assertIn("the Tester has no model to pin", offer["stop"])
        self.assertIsNotNone(offer["estimate"])

    def test_an_earlier_measurement_is_shown_on_its_candidate(self):
        grades = [{"request": f"R{n}", "passed": True, "seconds": 40.0, "cost_usd": 0.01} for n in range(9)]
        measured_routes.record("requirements", [measured_routes.result(GLM, "low", grades)])
        probe = self.stage(choices.view("opencode", **self.kw), "requirements")["probe"]
        rows = {row["model"]: row for row in probe["candidates"]}
        self.assertTrue(rows[GLM]["measured"]["qualified"])
        self.assertIn("9/9 requests at low", rows[GLM]["measured"]["reason"])
        self.assertIsNone(rows[SOL]["measured"])

    def test_a_chats_own_list_and_a_started_runs_list_offer_no_probe(self):
        current = choices.chat_view("opencode", {"builder": (OPUS, "high")}, ["stages", "list", "--json"], **self.kw)
        self.assertTrue(all(stage["probe"] is None for stage in current["stages"]))
        self.assertTrue(
            any(stage["probe"] for stage in choices.chat_view("opencode", {}, ["stages", "list"], **self.kw)["stages"])
        )

    def test_the_filled_estimate_and_run_measure_exactly_the_ticked_models_within_the_estimate(self):
        probe = self.stage(choices.view("opencode", **self.kw), "builder")["probe"]
        # The Tester runs Sol and the Completion Reviewer GLM, so neither may be measured as Builder here.
        self.assertEqual([OPUS], [row["model"] for row in probe["candidates"]])
        ticked = [row for row in probe["candidates"] if row["ticked"]]
        code, out = cli(fill(probe["estimate"]["next_command"], ticked))
        self.assertEqual(0, code)
        estimate = json.loads(out)["estimate"]
        self.assertFalse(json.loads(out)["ran"])
        self.assertEqual([OPUS], [row["model"] for row in estimate["models"]])
        with mock.patch.object(model_probe, "run_probe", return_value=_outcome()) as run:
            code, out = cli(fill(probe["run"]["next_command"], ticked, cap=str(estimate["max_usd"])))
        self.assertEqual(0, code)
        self.assertTrue(json.loads(out)["ran"])
        args, kwargs = run.call_args
        self.assertEqual([(OPUS, "medium")], args[0])
        self.assertEqual(estimate["max_usd"], kwargs["cap_usd"])
        self.assertEqual("builder", kwargs["stage"])
        self.assertEqual((GLM, "high"), kwargs["planner"])
        self.assertEqual([(SOL, "medium")], kwargs["checkers"], "the Tester the list shows checks every Builder")
        self.assertEqual(("--provider", "opencode"), kwargs["options"])


def _outcome():
    return {"results": [], "ranking": [], "spent_usd": 0.0, "unpriced_replies": 0, "stopped": None, "unmeasured": []}


if __name__ == "__main__":
    unittest.main()
