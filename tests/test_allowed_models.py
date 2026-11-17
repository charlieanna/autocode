"""The models a person allows AutoCode to use: one list every stage list, measurement, choice and climb reads."""

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_allowed_models as allowed_models
import autocode_builder_policy as builder_policy
import autocode_route_ladder as route_ladder
import autocode_route_pool as route_pool
import autocode_stage_choices as choices
import autocode_stages_cli as stages_cli
import model_catalogue

from .test_stage_choices import HAIKU, LISTING, OPUS, SONNET, resolve

GLM, SOL = "zai-coding-plan/glm-5.3", "openai/gpt-6-sol"


class AllowedModelsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "autocode" / "stages.toml"
        self.kw = {"file": self.file, "resolve": resolve, "discover": lambda provider, workspace: LISTING}

    def stage(self, current, role):
        return next(stage for stage in current["stages"] if stage["id"] == role)

    def test_the_view_lists_every_model_with_the_suggestion_ticked_and_the_command_that_limits_them(self):
        current = choices.view("claude", **self.kw)
        models = current["models"]
        self.assertIsNone(models["allowed"])
        self.assertEqual({HAIKU, SONNET, OPUS}, {row["model"] for row in models["options"]})
        # Every model a stage runs now is suggested, so taking the suggestion moves no stage.
        self.assertTrue(all(row["suggested"] and row["ticked"] for row in models["options"]))
        self.assertEqual(["stages", "models", "--backend", "claude", "--json", "MODELS"], models["set"]["next_command"])
        self.assertIsNone(models["clear"])

    def test_allowing_models_limits_every_stage_list_and_moves_a_stage_outside_them(self):
        current = choices.allow("claude", [SONNET, OPUS], **self.kw)
        self.assertEqual([SONNET, OPUS], current["models"]["allowed"])
        self.assertEqual(
            {HAIKU: False, SONNET: True, OPUS: True},
            {row["model"]: row["ticked"] for row in current["models"]["options"]},
        )
        for stage in current["stages"]:
            self.assertEqual({SONNET, OPUS}, {row["model"] for row in stage["options"]}, stage["id"])
        # The Builder's default (Haiku) is left out: it takes the first allowed model, Sonnet, and the Tester,
        # which checks it and ran Sonnet by default, moves to Opus. Completion's Opus does not check Sonnet.
        self.assertEqual({"model": SONNET, "effort": "medium"}, self.stage(current, "builder")["chosen"])
        self.assertEqual({"model": OPUS, "effort": "medium"}, self.stage(current, "tester")["chosen"])
        self.assertIsNone(self.stage(current, "planner")["chosen"], "a stage whose default is allowed stays as it is")
        self.assertEqual((SONNET, OPUS), choices.allowed("claude", self.file))
        self.assertEqual({"builder", "tester"}, set(choices.load(self.file)["claude"]))
        with self.assertRaisesRegex(ValueError, "does not offer"):
            choices.choose("claude", {"builder": (HAIKU, "medium")}, **self.kw)
        # Measuring a stage offers only the allowed models too (the Tester never runs the Builder's Sonnet).
        self.assertEqual({OPUS}, {row["model"] for row in self.stage(current, "tester")["probe"]["candidates"]})

    def test_a_list_is_kept_beside_the_stage_choices_and_cleared_with_allow_all(self):
        choices.allow("claude", [SONNET, OPUS], **self.kw)
        choices.choose("claude", {"tester": (OPUS, "high")}, **self.kw)
        self.assertEqual((SONNET, OPUS), choices.allowed("claude", self.file), "saving a stage keeps the list")
        choices.reset("claude", None, **self.kw)
        # Resetting every stage moves the Builder, and the Tester that checks it, back inside the list.
        self.assertEqual(
            {"builder": SONNET, "tester": OPUS},
            {role: route["model"] for role, route in choices.load(self.file)["claude"].items()},
        )
        current = choices.allow("claude", None, **self.kw)
        self.assertIsNone(current["models"]["allowed"])
        self.assertIsNone(choices.allowed("claude", self.file))
        self.assertEqual({HAIKU, SONNET, OPUS}, {row["model"] for row in self.stage(current, "builder")["options"]})

    def test_a_model_not_offered_or_a_list_that_leaves_a_stage_nothing_is_refused_and_nothing_saved(self):
        with self.assertRaisesRegex(ValueError, "does not offer claude-unknown"):
            choices.allow("claude", [SONNET, "claude-unknown"], **self.kw)
        with self.assertRaisesRegex(ValueError, "allow one more model"):
            choices.allow("opencode", [GLM], **self.kw)
        self.assertFalse(self.file.exists())

    def test_an_unreadable_list_names_the_file(self):
        self.file.parent.mkdir(parents=True)
        self.file.write_text('[allowed]\nclaude = ["ok", 3]\n')
        with self.assertRaisesRegex(ValueError, "not a model name"):
            choices.allowed("claude", self.file)
        self.file.write_text('[allowed]\nclaude = []\n[claude]\nbuilder = { model = "x" }\n')
        with self.assertRaisesRegex(ValueError, "at least one model"):
            choices.allowed("claude", self.file)
        self.assertEqual({"builder": {"model": "x", "effort": None}}, choices.load(self.file)["claude"])

    def test_a_new_run_reads_the_list_beside_its_stage_choices(self):
        choices.allow("claude", [SONNET, OPUS], **self.kw)
        args = SimpleNamespace(single_model=None, replay_recording=None, reasoning_effort=None)
        for role in choices.ORDER:
            setattr(args, f"{role}_model", None)
            setattr(args, f"{role}_reasoning_effort", None)
            setattr(args, f"{role}_provider", None)
        choices.apply(args, "claude", file=self.file)
        self.assertEqual((SONNET, OPUS), args.allowed_models)
        self.assertEqual(SONNET, args.builder_model)
        args.single_model = OPUS
        choices.apply(args, "claude", file=self.file)
        self.assertIsNone(args.allowed_models, "--single-model names the one model a run uses")

    def test_the_builders_stronger_attempt_and_climbs_stay_inside_the_list(self):
        retry = {"strong_model": SONNET, "checker_model": OPUS, "strong_origin": "runner"}
        self.assertEqual(
            {"strong_model": None, "checker_model": OPUS, "strong_origin": "runner", "not_allowed": SONNET},
            allowed_models.restrict_retry(dict(retry), [HAIKU, OPUS]),
        )
        self.assertIsNone(allowed_models.restrict_retry(dict(retry), [HAIKU, SONNET])["strong_model"])
        self.assertEqual(retry, allowed_models.restrict_retry(dict(retry), [HAIKU, SONNET, OPUS]))
        typed = {**retry, "strong_origin": "user_explicit"}
        self.assertEqual(typed, allowed_models.restrict_retry(dict(typed), [HAIKU]), "a typed flag stays exact")
        # A Codex list names bare models; the ladders and the retry name them provider-qualified.
        self.assertEqual(["openai/gpt-6-sol"], allowed_models.available(["openai/gpt-6-sol", GLM], ["gpt-6-sol"]))
        self.assertEqual([GLM], allowed_models.available(None, [GLM]))
        self.assertIsNone(allowed_models.available(None, None))
        settings = {
            "engine": "opencode",
            "roles": {"builder": {"model": GLM}},
            "route_ladders": route_ladder.configure_ladders(),
            "allowed_models": [GLM],
        }
        served = model_catalogue._serve(settings, [GLM, SOL], None)
        self.assertEqual([], served["route_ladders"]["effort"]["builder"], "no climb onto a model left out")
        stop = builder_policy._escalate(
            {"settings": settings}, {}, {"strong_model": None, "not_allowed": SOL}, {"model": GLM}
        )
        self.assertEqual("pause", stop[0])
        self.assertIn("not among the models you allow AutoCode to use", stop[2])

    def test_a_fallback_after_a_model_stop_stays_inside_the_list_the_run_started_with(self):
        offers = [
            {"model": model, "billing": "subscription", "account": "x", "levels": ["high"], "levels_listed": True}
            for model in (GLM, SOL, "xiaomi-token-plan-sgp/mimo-v2.6-pro")
        ]
        settings = {"engine": "opencode", "roles": {"builder": {"model": GLM}}, "allowed_models": [GLM, SOL]}
        rows = route_pool._subscription_pool(
            SimpleNamespace(), settings, "builder", ".", {}, offers_fn=lambda backend, workspace: offers
        )
        self.assertEqual({GLM, SOL}, {row["model"] for row in rows})


class AllowedModelsCommandTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "stages.toml"
        patches = [
            mock.patch.object(choices, "path", return_value=self.file),
            mock.patch.object(choices.providers, "resolve", side_effect=resolve),
            mock.patch.object(choices.route_discovery, "discover", return_value=LISTING),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = stages_cli.cli(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_models_lists_and_changes_the_models_autocode_may_use(self):
        code, out, _err = self.run_cli("models", "--backend", "claude")
        self.assertEqual(0, code)
        self.assertIn("AutoCode may use every model your Claude Code accounts serve (3)", out)
        code, out, _err = self.run_cli("models", "--backend", "claude", "--allow", OPUS, "--allow", SONNET, "--json")
        self.assertEqual(0, code)
        self.assertEqual([OPUS, SONNET], json.loads(out)["models"]["allowed"])
        self.assertIn("AutoCode may use 2 of the 3", self.run_cli("models", "--backend", "claude")[1])
        code, _out, err = self.run_cli("models", "--backend", "claude", "--allow", "nope")
        self.assertEqual(2, code)
        self.assertIn("does not offer nope", err)
        self.assertEqual(2, self.run_cli("models", "--allow", OPUS, "--allow-all")[0])
        self.assertEqual(2, self.run_cli("list", "--allow", OPUS)[0])
        self.assertEqual(0, self.run_cli("models", "--backend", "claude", "--allow-all")[0])
        self.assertIsNone(choices.allowed("claude"))

    def test_setup_asks_which_models_first_and_enter_takes_the_suggestion(self):
        listing = {
            "candidates": [
                *LISTING["candidates"],
                *(
                    {**LISTING["candidates"][1], "model": f"openai/gpt-{n}-old", "levels": ["high"]}
                    for n in range(1, 30)
                ),
            ]
        }
        answers = iter(["list", "zzz", "", *[""] * 30])
        said = []
        with mock.patch.object(choices.route_discovery, "discover", return_value=listing):
            self.assertTrue(stages_cli.choose("opencode", ask=lambda prompt: next(answers), out=said.append))
        shown = "\n".join(said)
        self.assertIn("Which models may AutoCode use on OpenCode? Your accounts serve 32.", shown)
        self.assertIn("more are not suggested; type `list` to see them", shown)
        self.assertIn("    openai/gpt-1-old", shown, "`list` prints every model")
        self.assertIn("Not a number from 1 to", shown)
        allowed = choices.allowed("opencode")
        self.assertIn(GLM, allowed)
        self.assertLess(len(allowed), 32, "the suggestion, not every model")
        for role, route in choices.load()["opencode"].items():
            self.assertIn(route["model"], allowed, role)

    def test_setup_takes_numbers_names_or_all(self):
        answers = iter(["2 " + GLM, *[""] * 20])
        self.assertTrue(stages_cli.choose("opencode", ask=lambda prompt: next(answers), out=lambda line: None))
        self.assertEqual(2, len(choices.allowed("opencode")))
        self.assertEqual(GLM, choices.allowed("opencode")[1])
        answers = iter(["all", *[""] * 20])
        self.assertTrue(stages_cli.choose("opencode", ask=lambda prompt: next(answers), out=lambda line: None))
        self.assertIsNone(choices.allowed("opencode"))


if __name__ == "__main__":
    unittest.main()
