"""Each stage's model and reasoning level, chosen once and kept for every new task (owner decision 2026-10-30)."""

import argparse
import contextlib
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_configure
import autocode_stage_choices as choices
import autocode_stages_cli as stages_cli

HAIKU, SONNET, OPUS = "claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5"
CLAUDE = SimpleNamespace(
    DEFAULT_MODELS={
        "requirements": SONNET,
        "glm": SONNET,
        "plan_reviewer": OPUS,
        "terra": HAIKU,
        "sol": SONNET,
        "completion": OPUS,
        "astra": OPUS,
    },
    DEFAULT_REASONING_EFFORTS={role: "medium" for role in choices.ORDER},
    available_models=lambda workspace=None: {HAIKU, SONNET, OPUS},
)
# An OpenCode-like tool: its options come from discovery's candidates, never from a guess.
LISTED = SimpleNamespace(
    DEFAULT_MODELS={role: "zai-coding-plan/glm-5.3" for role in choices.ORDER} | {"sol": "openai/gpt-6-sol"},
    DEFAULT_REASONING_EFFORTS={role: "medium" for role in choices.ORDER},
    auth_logins=lambda *a, **kw: [],
)


def candidate(model, levels=None, **facts):
    return {
        "model": model,
        "tool": "opencode",
        "account": "Z.AI Coding Plan" if "glm" in model else "OpenAI",
        "billing": "subscription",
        "levels": levels,
        "probe": None,
        "metadata": {"family": None, "toolcall": None, "reasoning": None, "context": None, "release_date": None}
        | facts,
    }


LISTING = {
    "candidates": [
        candidate("zai-coding-plan/glm-5.3", ["low", "high", "max"], family="glm", toolcall=True, context=200000),
        candidate("openai/gpt-6-sol", ["medium", "high", "xhigh"], family="gpt", toolcall=True),
        candidate("opencode/no-tools", [], toolcall=False),
        candidate("opencode/plain", []),
    ]
}


def resolve(name):
    return {"claude": CLAUDE, "opencode": LISTED, "gocode": CONFIGURED}[name]


CONFIGURED = SimpleNamespace(
    DEFAULT_MODELS={role: "configured/fast" for role in choices.ORDER},
    DEFAULT_REASONING_EFFORTS={role: "medium" for role in choices.ORDER},
    auth_table=lambda: {"name": "gocode", "command": None, "routes": []},
    available_models=lambda workspace=None: {"configured/fast", "configured/strong"},
)


class StageChoiceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "autocode" / "stages.toml"
        self.kw = {"file": self.file, "resolve": resolve, "discover": lambda provider, workspace: LISTING}

    def stage(self, current, role):
        return next(stage for stage in current["stages"] if stage["id"] == role)

    def test_every_stage_says_what_it_does_and_what_suits_it_and_names_its_flags(self):
        current = choices.view("claude", **self.kw)
        self.assertEqual(list(choices.ORDER), [stage["id"] for stage in current["stages"]])
        builder = self.stage(current, "terra")
        self.assertEqual("Builder", builder["label"])
        self.assertIn("Writes the code", builder["does"])
        self.assertIn("cheaper model at medium", builder["advice"])
        self.assertEqual(("--terra-model", "--terra-reasoning-effort"), (builder["flag"], builder["effort_flag"]))
        self.assertEqual({"model": HAIKU, "effort": "medium", "offered": True}, builder["default"])
        self.assertIsNone(builder["chosen"])
        self.assertIsNone(builder["reset"])
        self.assertEqual("Resolver and per-task Planner", self.stage(current, "astra")["label"])
        for stage in current["stages"]:
            self.assertTrue(stage["does"] and stage["advice"], stage["id"])

    def test_a_listed_tool_offers_what_its_accounts_serve_with_each_models_own_levels(self):
        current = choices.view("opencode", **self.kw)
        options = {row["model"]: row for row in self.stage(current, "terra")["options"]}
        self.assertNotIn("opencode/no-tools", options, "a model that cannot call tools cannot run a stage")
        glm = options["zai-coding-plan/glm-5.3"]
        self.assertEqual(
            (["low", "high", "max"], True, "glm", 200000),
            (glm["levels"], glm["levels_listed"], glm["family"], glm["context"]),
        )
        self.assertEqual([choices.OWN_DEFAULT], options["opencode/plain"]["effort_choices"])
        # GLM 5.3 declares no medium, so its default level is the nearest one it does declare.
        self.assertEqual("high", self.stage(current, "terra")["default"]["effort"])

    def test_a_configured_provider_without_billing_routes_offers_its_declared_models(self):
        current = choices.view("gocode", **self.kw)
        builder = self.stage(current, "terra")
        self.assertEqual({"configured/fast", "configured/strong"}, {row["model"] for row in builder["options"]})
        self.assertTrue(all(row["account"] is None and row["billing"] is None for row in builder["options"]))

    def test_a_checker_is_never_offered_the_model_whose_work_it_checks(self):
        current = choices.view("opencode", **self.kw)
        tester = {row["model"]: row["unavailable"] for row in self.stage(current, "sol")["options"]}
        self.assertIn("the Tester checks the Builder's work", tester["zai-coding-plan/glm-5.3"])
        self.assertIsNone(tester["openai/gpt-6-sol"])
        builder = {row["model"]: row["unavailable"] for row in self.stage(current, "terra")["options"]}
        self.assertIn("change the Tester too", builder["openai/gpt-6-sol"])
        # On Claude Code two different Claude models may check each other; one model never checks itself.
        claude = {
            row["model"]: row["unavailable"] for row in self.stage(choices.view("claude", **self.kw), "sol")["options"]
        }
        self.assertIsNone(claude[OPUS])
        self.assertIn("runs " + HAIKU, claude[HAIKU])

    def test_a_builder_both_checkers_share_names_both_so_changing_one_is_not_a_dead_end(self):
        both = {"sol": OPUS, "completion": OPUS}
        self.assertEqual(
            f"the Tester and the Completion Reviewer check this stage's work and run {OPUS}; change both too",
            choices.unavailable("terra", OPUS, both, backend="claude"),
        )
        family = {"sol": "zai-coding-plan/glm-5.1", "completion": "zai/glm-5.3"}
        self.assertEqual(
            "the Tester (runs zai-coding-plan/glm-5.1) and the Completion Reviewer (runs zai/glm-5.3) check this "
            "stage's work; change both too",
            choices.unavailable("terra", "zai-coding-plan/glm-5.3", family, backend="opencode"),
        )

    def test_a_choice_is_saved_per_backend_and_offered_back_with_its_reset(self):
        # Claude Code's Tester runs Sonnet by default, so a Sonnet Builder moves the Tester in the same change.
        current = choices.choose("claude", {"terra": (SONNET, "high"), "sol": (OPUS, "high")}, **self.kw)
        builder = self.stage(current, "terra")
        self.assertEqual({"model": SONNET, "effort": "high"}, builder["chosen"])
        self.assertEqual(
            ["stages", "reset", "terra", "--backend", "claude", "--json"], builder["reset"]["next_command"]
        )
        unavailable = {row["model"]: row["unavailable"] for row in builder["options"]}
        self.assertEqual({HAIKU: None, SONNET: None}, {model: why for model, why in unavailable.items() if not why})
        self.assertIn(
            "the Tester and the Completion Reviewer check this stage's work and run " + OPUS, unavailable[OPUS]
        )
        saved = {"terra": {"model": SONNET, "effort": "high"}, "sol": {"model": OPUS, "effort": "high"}}
        self.assertEqual({"claude": saved}, choices.load(self.file))
        choices.choose("opencode", {"astra": ("opencode/plain", choices.OWN_DEFAULT)}, **self.kw)
        self.assertEqual({"model": "opencode/plain", "effort": None}, choices.load(self.file)["opencode"]["astra"])
        self.assertEqual(saved, choices.load(self.file)["claude"], "another backend is untouched")
        choices.reset("claude", "terra", **self.kw)
        self.assertEqual({"sol"}, set(choices.load(self.file)["claude"]))
        choices.reset("claude", **self.kw)
        self.assertEqual({"opencode"}, set(choices.load(self.file)))

    def test_a_choice_the_backend_does_not_offer_is_refused_and_nothing_is_saved(self):
        for picks, error in (
            ({"terra": ("claude-imaginary", "high")}, "does not offer claude-imaginary"),
            ({"terra": (HAIKU, "extreme")}, "takes low, medium, high, xhigh, max, not extreme"),
            ({"terra": (OPUS, "high")}, "the Completion Reviewer checks this stage's work and runs " + OPUS),
            ({"sol": (HAIKU, "high")}, "the Tester cannot run " + HAIKU),
            ({"builder": (OPUS, "high")}, "builder is not a stage"),
        ):
            with self.subTest(picks=picks), self.assertRaisesRegex(ValueError, error):
                choices.choose("claude", picks, **self.kw)
        self.assertFalse(self.file.exists())

    def test_a_builder_and_its_checkers_can_swap_models_in_one_change(self):
        choices.choose("claude", {"terra": (SONNET, "medium"), "sol": (OPUS, "high")}, **self.kw)
        swap = {"terra": (OPUS, "low"), "sol": (SONNET, "high"), "completion": (HAIKU, "medium")}
        current = choices.choose("claude", swap, **self.kw)
        self.assertEqual(OPUS, self.stage(current, "terra")["chosen"]["model"])
        self.assertEqual(SONNET, self.stage(current, "sol")["chosen"]["model"])

    def test_an_unreadable_listing_offers_nothing_and_says_why(self):
        def broken(provider, workspace):
            raise RuntimeError("cannot verify opencode login")

        current = choices.view("opencode", **{**self.kw, "discover": broken})
        self.assertEqual("cannot verify opencode login", current["options_stop"])
        self.assertEqual([], self.stage(current, "terra")["options"])
        with self.assertRaisesRegex(ValueError, "cannot list OpenCode's models"):
            choices.choose(
                "opencode", {"terra": ("zai-coding-plan/glm-5.3", "high")}, **{**self.kw, "discover": broken}
            )

    def test_a_saved_file_round_trips_and_a_broken_one_is_refused_by_name(self):
        every = {"claude": {"terra": {"model": HAIKU, "effort": "low"}, "astra": {"model": OPUS, "effort": None}}}
        choices.save(every, self.file)
        self.assertEqual(every, choices.load(self.file))
        for text, error in (
            ("[claude\n", "cannot be read as TOML"),
            ('[claude]\nbuilder = { model = "x" }\n', "builder is not a stage"),
            ('[claude]\nterra = { effort = "low" }\n', "needs a model name"),
            ('[claude]\nterra = { model = "x", effort = "huge" }\n', "effort must be one of"),
            ("terra = 1\n", "must be a table of stages"),
        ):
            self.file.write_text(text)
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, str(self.file)):
                choices.load(self.file)
            with self.assertRaisesRegex(ValueError, error):
                choices.load(self.file)
        with self.assertRaisesRegex(ValueError, "must be a table of stages"):
            choices.reset("claude", "terra", **self.kw)
        choices.reset("claude", **self.kw)
        self.assertEqual({}, choices.load(self.file), "reset repairs an unreadable file")
        with self.assertRaisesRegex(ValueError, "builder is not a stage"):
            choices.reset("claude", "builder", **self.kw)

    def test_a_failed_write_leaves_the_saved_file_as_it_was(self):
        choices.save({"claude": {"terra": {"model": HAIKU, "effort": "low"}}}, self.file)
        with mock.patch.object(choices.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                choices.save({"claude": {"terra": {"model": OPUS, "effort": "low"}}}, self.file)
        self.assertEqual(HAIKU, choices.load(self.file)["claude"]["terra"]["model"])
        self.assertEqual(["stages.toml"], sorted(path.name for path in self.file.parent.iterdir()))

    def test_a_tool_that_cannot_list_its_models_offers_nothing(self):
        silent = SimpleNamespace(DEFAULT_MODELS={}, available_models=lambda workspace=None: None)
        current = choices.view("mine", **{**self.kw, "resolve": lambda name: silent})
        self.assertEqual("mine cannot list its models", current["options_stop"])
        self.assertEqual({"model": None, "effort": None, "offered": False}, self.stage(current, "terra")["default"])

    def test_codex_offers_its_bare_gpt_names_and_its_defaults_match_the_engines(self):
        current = choices.view("codex", **self.kw)
        self.assertEqual(list(choices.CODEX_MODELS), [row["model"] for row in self.stage(current, "terra")["options"]])
        for role, model in autocode_configure.DEFAULT_ROLE_MODELS.items():
            self.assertEqual(model, self.stage(current, role)["default"]["model"], role)
        # Codex joint planning copies the astra route into the planning roles.
        for role in ("requirements", "glm", "plan_reviewer"):
            self.assertEqual(
                autocode_configure.DEFAULT_ROLE_MODELS["astra"], self.stage(current, role)["default"]["model"]
            )


class ApplyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "stages.toml"
        choices.save(
            {
                "claude": {
                    "terra": {"model": SONNET, "effort": "high"},
                    "sol": {"model": OPUS, "effort": "xhigh"},
                    "astra": {"model": OPUS, "effort": None},
                }
            },
            self.file,
        )

    def args(self, **given):
        names = [f"{role}_{kind}" for role in choices.ORDER for kind in ("model", "reasoning_effort")]
        base = dict.fromkeys(names + ["single_model", "reasoning_effort", "replay_recording", "sol_provider"])
        return argparse.Namespace(**(base | given))

    def test_a_new_run_takes_the_saved_model_and_level_of_each_chosen_stage(self):
        args, said = self.args(), []
        self.assertEqual(["terra", "sol", "astra"], choices.apply(args, "claude", file=self.file, say=said.append))
        self.assertEqual((SONNET, "high"), (args.terra_model, args.terra_reasoning_effort))
        self.assertEqual((OPUS, "xhigh"), (args.sol_model, args.sol_reasoning_effort))
        self.assertEqual((OPUS, None), (args.astra_model, args.astra_reasoning_effort))
        self.assertIsNone(args.glm_model, "a stage nobody chose keeps the backend's default")
        self.assertEqual([f"Stage choices from {self.file}: Builder, Tester, Resolver and per-task Planner"], said)

    def test_a_flag_or_project_setting_wins_and_whole_run_choices_take_nothing(self):
        args = self.args(terra_model=HAIKU, sol_reasoning_effort="low")
        choices.apply(args, "claude", file=self.file)
        self.assertEqual(
            (HAIKU, None), (args.terra_model, args.terra_reasoning_effort), "a typed model keeps its level"
        )
        self.assertEqual((OPUS, "low"), (args.sol_model, args.sol_reasoning_effort))
        shared = self.args(reasoning_effort="max")
        choices.apply(shared, "claude", file=self.file)
        self.assertEqual((SONNET, None), (shared.terra_model, shared.terra_reasoning_effort))
        for given in ({"single_model": HAIKU}, {"replay_recording": "/r"}, {}):
            args = self.args(**given)
            backend = "opencode" if not given else "claude"
            with self.subTest(given=given):
                self.assertEqual([], choices.apply(args, backend, file=self.file))
                self.assertIsNone(args.terra_model)
        routed = self.args(sol_provider="kilocode")
        choices.apply(routed, "claude", file=self.file)
        self.assertIsNone(routed.sol_model, "a stage another tool serves keeps that tool's route")

    def test_a_broken_file_stops_a_new_run_by_name(self):
        self.file.write_text("[claude\n")
        with self.assertRaisesRegex(ValueError, str(self.file)):
            choices.apply(self.args(), "claude", file=self.file)


class StagesCommandTests(unittest.TestCase):
    """`autocode stages` through its public argv, with only the backend's model list faked."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        for patcher in (
            mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": folder.name}),
            mock.patch.object(choices.providers, "resolve", side_effect=resolve),
            mock.patch.object(choices.route_discovery, "discover", side_effect=lambda provider, workspace: LISTING),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)
        os.environ.pop("AUTOCODE_PROVIDER", None)

    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = stages_cli.cli(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_set_takes_a_job_name_and_the_json_view_names_the_dropdowns_commands(self):
        self.assertEqual(
            0, self.run_cli("set", "tester", "--model", OPUS, "--effort", "high", "--backend", "claude")[0]
        )
        code, out, _err = self.run_cli(
            "set", "builder", "--model", SONNET, "--effort", "high", "--backend", "claude", "--json"
        )
        self.assertEqual(0, code)
        builder = next(stage for stage in json.loads(out)["stages"] if stage["id"] == "terra")
        self.assertEqual({"model": SONNET, "effort": "high"}, builder["chosen"])
        self.assertEqual(
            ["stages", "set", "terra", "--backend", "claude", "--json", "--model", "MODEL", "--effort", "EFFORT"],
            builder["set"]["next_command"],
        )
        # The view's own argument array, filled from its options, is taken as given.
        command = [
            OPUS if part == "MODEL" else "low" if part == "EFFORT" else part for part in builder["set"]["next_command"]
        ]
        code, _out, err = self.run_cli(*command[1:])
        self.assertEqual(2, code, "the Tester now runs Opus, so the Builder may not")
        self.assertIn("the Builder cannot run " + OPUS, err)
        code, out, _err = self.run_cli("list", "--backend", "claude")
        self.assertIn(f"Builder: {SONNET} at high (your choice)", out)
        self.assertIn(f"Tester: {OPUS} at high (your choice)", out)
        self.assertIn(f"Planner: {SONNET} at medium (default)", out)
        self.assertEqual(0, self.run_cli("reset", "builder", "--backend", "claude")[0])
        self.assertEqual({"sol"}, set(choices.load()["claude"]))

    def test_set_without_a_model_or_level_and_choose_without_a_terminal_ask_nothing(self):
        self.assertEqual(2, self.run_cli("set", "builder", "--backend", "claude")[0])
        with mock.patch.object(stages_cli, "_terminal", return_value=False):
            code, _out, err = self.run_cli("choose", "--backend", "claude")
        self.assertEqual(2, code)
        self.assertIn("autocode stages set", err)

    def test_choose_asks_each_stage_in_turn_and_saves_the_answers_together(self):
        # Requirements and Planner keep their defaults; the Plan Reviewer gets Sonnet, which the
        # Planner runs, so it is refused and Opus is taken; the Builder takes Sonnet at high, so the
        # Tester's default (Sonnet) is not offered and Enter keeps the first free model.
        # Options are listed sorted: 1 Haiku, 2 Opus, 3 Sonnet.
        answers = iter(["", "", "", "", "3", "2", "", "3", "high", "", "", "", "", "", "low"])
        asked, said = [], []

        def ask(prompt):
            asked.append(prompt)
            return next(answers)

        self.assertTrue(stages_cli.choose("claude", ask=ask, out=said.append))
        saved = choices.load()["claude"]
        self.assertEqual({"model": SONNET, "effort": "medium"}, saved["requirements"])
        self.assertEqual(OPUS, saved["plan_reviewer"]["model"])
        self.assertEqual({"model": SONNET, "effort": "high"}, saved["terra"])
        self.assertNotEqual(SONNET, saved["sol"]["model"])
        self.assertEqual("low", saved["astra"]["effort"])
        shown = "\n".join(said)
        self.assertIn("Advice: A fast, cheaper model at medium", shown)
        self.assertIn("not available: the Plan Reviewer checks the Planner's work", shown)
        self.assertIn("Stages on Claude Code", shown)
        self.assertTrue(any(prompt.startswith("  Builder model [") for prompt in asked))

    def test_choose_on_a_listed_tool_shows_each_models_facts_and_skips_a_level_it_does_not_list(self):
        # 1 GLM 5.3, 2 GPT-6 Sol, 3 a plain model with no levels. A wrong number or level is asked again.
        answers = iter(["9", "1", "medium", "", "", "", "2", "", "1", "", "2", "", "", "", "3"])
        said = []
        self.assertTrue(stages_cli.choose("opencode", ask=lambda prompt: next(answers), out=said.append))
        shown = "\n".join(said)
        self.assertIn("1) zai-coding-plan/glm-5.3 (glm; 200k context; Z.AI Coding Plan, subscription)", shown)
        self.assertIn("Type a number from 1 to 3.", shown)
        self.assertIn("zai-coding-plan/glm-5.3 takes low, high, max.", shown)
        self.assertIn("opencode/plain lists no reasoning levels; it runs at its own default.", shown)
        saved = choices.load()["opencode"]
        self.assertEqual({"model": "zai-coding-plan/glm-5.3", "effort": "high"}, saved["requirements"])
        self.assertEqual({"model": "opencode/plain", "effort": None}, saved["astra"])

    def test_choose_says_why_when_nothing_can_be_offered(self):
        said = []
        with mock.patch.object(choices.route_discovery, "discover", side_effect=RuntimeError("no login")):
            self.assertFalse(stages_cli.choose("opencode", ask=None, out=said.append))
        self.assertEqual(["Cannot list OpenCode's models: no login"], said)
        with mock.patch.object(choices.route_discovery, "discover", return_value={"candidates": []}):
            self.assertFalse(stages_cli.choose("opencode", ask=None, out=said.append))
        self.assertIn("offers no model to your accounts yet", said[-1])
        only = {"candidates": [candidate("zai-coding-plan/glm-5.3", ["high"])]}
        with mock.patch.object(choices.route_discovery, "discover", return_value=only):
            self.assertFalse(stages_cli.choose("opencode", ask=lambda prompt: "", out=said.append))
        self.assertIn("No model your accounts offer can run the Plan Reviewer", said[-1])

    def test_the_list_says_when_a_default_is_not_offered_or_models_cannot_be_listed(self):
        code, out, _err = self.run_cli("--backend", "opencode")
        self.assertEqual(0, code)
        self.assertIn("Builder: zai-coding-plan/glm-5.3 at high (default)", out)
        with mock.patch.object(choices.route_discovery, "discover", side_effect=RuntimeError("no login")):
            out = self.run_cli("--backend", "opencode")[1]
        self.assertIn("Models cannot be listed: no login", out)
        self.assertIn("chosen from your accounts when a task starts", out)

    def test_choose_runs_only_in_a_terminal_and_an_interrupt_saves_nothing(self):
        with mock.patch.object(stages_cli, "_terminal", return_value=True):
            for result, code in ((True, 0), (False, 1), (KeyboardInterrupt(), 130)):
                with self.subTest(result=result), mock.patch.object(stages_cli, "choose", side_effect=[result]):
                    self.assertEqual(code, self.run_cli("choose", "--backend", "claude")[0])

    def test_choose_asks_only_when_both_ends_are_a_terminal(self):
        for stdin, stdout in ((True, True), (True, False), (False, True)):
            with (
                self.subTest(stdin=stdin, stdout=stdout),
                mock.patch.object(stages_cli.sys, "stdin", SimpleNamespace(isatty=lambda: stdin)),
                mock.patch.object(stages_cli.sys, "stdout", SimpleNamespace(isatty=lambda: stdout)),
            ):
                self.assertEqual(stdin and stdout, stages_cli._terminal())

    def test_without_a_backend_the_command_takes_the_one_new_runs_in_this_project_use(self):
        workspace = self.home / "project"
        (workspace / ".autocode").mkdir(parents=True)
        connected = {"id": "claude"}
        self.assertEqual("claude", stages_cli.current_backend(workspace, choose=lambda: connected))
        self.assertEqual("opencode", stages_cli.current_backend(workspace, choose=lambda: None))
        (self.home / "autocode").mkdir()
        (self.home / "autocode" / "config.toml").write_text('default_provider = "kilocode"\n')
        self.assertEqual("kilocode", stages_cli.current_backend(workspace, choose=lambda: connected))
        (workspace / ".autocode" / "config.toml").write_text('engine = "codex"\n')
        self.assertEqual("codex", stages_cli.current_backend(workspace, choose=lambda: connected))
        (workspace / ".autocode" / "config.toml").write_text("engine = \n")
        self.assertEqual("kilocode", stages_cli.current_backend(workspace, choose=lambda: connected))
        code, _out, err = self.run_cli(
            "--workspace", str(workspace), "set", "nobody", "--model", "m", "--effort", "low"
        )
        self.assertEqual(2, code)
        self.assertIn("nobody is not a stage", err)


if __name__ == "__main__":
    unittest.main()
