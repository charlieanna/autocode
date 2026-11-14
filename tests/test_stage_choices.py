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

import autocode_args
import autocode_configure
import autocode_role_transport as role_transport
import autocode_roles as role_names
import autocode_stage_choices as choices
import autocode_stages_cli as stages_cli

HAIKU, SONNET, OPUS = "claude-haiku-4-5-20251001", "claude-sonnet-5-5", "claude-opus-5-5"
CLAUDE = SimpleNamespace(
    DEFAULT_MODELS={
        "requirements": SONNET,
        "planner": SONNET,
        "plan_reviewer": OPUS,
        "builder": HAIKU,
        "tester": SONNET,
        "completion": OPUS,
        "task_planner": OPUS,
    },
    DEFAULT_REASONING_EFFORTS={role: "medium" for role in choices.ORDER},
    available_models=lambda workspace=None: {HAIKU, SONNET, OPUS},
)
# An OpenCode-like tool: its options come from discovery's candidates, never from a guess.
LISTED = SimpleNamespace(
    DEFAULT_MODELS={role: "zai-coding-plan/glm-5.3" for role in choices.ORDER} | {"tester": "openai/gpt-6-sol"},
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
        builder = self.stage(current, "builder")
        self.assertEqual("Builder", builder["label"])
        self.assertIn("Writes the code", builder["does"])
        self.assertIn("cheaper model at medium", builder["advice"])
        self.assertEqual(("--builder-model", "--builder-reasoning-effort"), (builder["flag"], builder["effort_flag"]))
        self.assertEqual({"model": HAIKU, "effort": "medium", "offered": True}, builder["default"])
        self.assertIsNone(builder["chosen"])
        self.assertIsNone(builder["reset"])
        self.assertEqual("Resolver and per-task Planner", self.stage(current, "task_planner")["label"])
        for stage in current["stages"]:
            self.assertTrue(stage["does"] and stage["advice"], stage["id"])

    def test_each_stage_names_the_jobs_that_run_on_its_choice(self):
        # A workflow job copies its route from a stage the person chooses (role_transport.DERIVED_ROLES; the
        # Design Reviewer in units/autoreview), so that stage says so. A live bug fix ran its Investigator on
        # the Plan Reviewer's model while no stage's text named the Investigator.
        current = choices.view("claude", **self.kw)
        text = {stage["id"]: f"{stage['label']}: {stage['does']}" for stage in current["stages"]}
        named = {role: role_names.SCREEN[role_names.ROUTE_JOB[role]] for role in role_transport.DERIVED_ROLES}
        named["acceptance_tester"] = "acceptance tests"
        jobs = [(sources[0], named[role]) for role, sources in role_transport.DERIVED_ROLES.items()]
        jobs += [("plan_reviewer", role_names.SCREEN["design_reviewer"])]
        for stage, job in jobs:
            self.assertIn(job, text[stage], stage)

    def test_a_listed_tool_offers_what_its_accounts_serve_with_each_models_own_levels(self):
        current = choices.view("opencode", **self.kw)
        options = {row["model"]: row for row in self.stage(current, "builder")["options"]}
        self.assertNotIn("opencode/no-tools", options, "a model that cannot call tools cannot run a stage")
        glm = options["zai-coding-plan/glm-5.3"]
        self.assertEqual(
            (["low", "high", "max"], True, "glm", 200000),
            (glm["levels"], glm["levels_listed"], glm["family"], glm["context"]),
        )
        self.assertEqual([choices.OWN_DEFAULT], options["opencode/plain"]["effort_choices"])
        # GLM 5.3 declares no medium, so its default level is the nearest one it does declare.
        self.assertEqual("high", self.stage(current, "builder")["default"]["effort"])

    def test_a_configured_provider_without_billing_routes_offers_its_declared_models(self):
        current = choices.view("gocode", **self.kw)
        builder = self.stage(current, "builder")
        self.assertEqual({"configured/fast", "configured/strong"}, {row["model"] for row in builder["options"]})
        self.assertTrue(all(row["account"] is None and row["billing"] is None for row in builder["options"]))

    def test_a_checker_is_never_offered_the_model_whose_work_it_checks(self):
        current = choices.view("opencode", **self.kw)
        tester = {row["model"]: row["unavailable"] for row in self.stage(current, "tester")["options"]}
        self.assertIn("the Tester checks the Builder's work", tester["zai-coding-plan/glm-5.3"])
        self.assertIsNone(tester["openai/gpt-6-sol"])
        builder = {row["model"]: row["unavailable"] for row in self.stage(current, "builder")["options"]}
        self.assertIn("change the Tester too", builder["openai/gpt-6-sol"])
        # On Claude Code two different Claude models may check each other; one model never checks itself.
        claude = {
            row["model"]: row["unavailable"]
            for row in self.stage(choices.view("claude", **self.kw), "tester")["options"]
        }
        self.assertIsNone(claude[OPUS])
        self.assertIn("runs " + HAIKU, claude[HAIKU])

    def test_levels_are_offered_lowest_first_whatever_order_the_listing_prints(self):
        self.assertEqual(
            ["low", "high", "max", "turbo"], choices.effort_choices({"levels": ["high", "turbo", "max", "low"]})
        )
        self.assertEqual([choices.OWN_DEFAULT], choices.effort_choices({"levels": []}))

    def test_a_builder_both_checkers_share_names_both_so_changing_one_is_not_a_dead_end(self):
        both = {"tester": OPUS, "completion": OPUS}
        self.assertEqual(
            f"the Tester and the Completion Reviewer check this stage's work and run {OPUS}; change both too",
            choices.unavailable("builder", OPUS, both, backend="claude"),
        )
        family = {"tester": "zai-coding-plan/glm-5.1", "completion": "zai/glm-5.3"}
        self.assertEqual(
            "the Tester (runs zai-coding-plan/glm-5.1) and the Completion Reviewer (runs zai/glm-5.3) check this "
            "stage's work; change both too",
            choices.unavailable("builder", "zai-coding-plan/glm-5.3", family, backend="opencode"),
        )

    def test_a_choice_is_saved_per_backend_and_offered_back_with_its_reset(self):
        # Claude Code's Tester runs Sonnet by default, so a Sonnet Builder moves the Tester in the same change.
        current = choices.choose("claude", {"builder": (SONNET, "high"), "tester": (OPUS, "high")}, **self.kw)
        builder = self.stage(current, "builder")
        self.assertEqual({"model": SONNET, "effort": "high"}, builder["chosen"])
        self.assertEqual(
            ["stages", "reset", "builder", "--backend", "claude", "--json"], builder["reset"]["next_command"]
        )
        unavailable = {row["model"]: row["unavailable"] for row in builder["options"]}
        self.assertEqual({HAIKU: None, SONNET: None}, {model: why for model, why in unavailable.items() if not why})
        self.assertIn(
            "the Tester and the Completion Reviewer check this stage's work and run " + OPUS, unavailable[OPUS]
        )
        saved = {"builder": {"model": SONNET, "effort": "high"}, "tester": {"model": OPUS, "effort": "high"}}
        self.assertEqual({"claude": saved}, choices.load(self.file))
        choices.choose("opencode", {"task_planner": ("opencode/plain", choices.OWN_DEFAULT)}, **self.kw)
        self.assertEqual(
            {"model": "opencode/plain", "effort": None}, choices.load(self.file)["opencode"]["task_planner"]
        )
        self.assertEqual(saved, choices.load(self.file)["claude"], "another backend is untouched")
        choices.reset("claude", "builder", **self.kw)
        self.assertEqual({"tester"}, set(choices.load(self.file)["claude"]))
        choices.reset("claude", **self.kw)
        self.assertEqual({"opencode"}, set(choices.load(self.file)))

    def test_a_choice_the_backend_does_not_offer_is_refused_and_nothing_is_saved(self):
        for picks, error in (
            ({"builder": ("claude-imaginary", "high")}, "does not offer claude-imaginary"),
            ({"builder": (HAIKU, "extreme")}, "takes low, medium, high, xhigh, max, not extreme"),
            ({"builder": (OPUS, "high")}, "the Completion Reviewer checks this stage's work and runs " + OPUS),
            ({"tester": (HAIKU, "high")}, "the Tester cannot run " + HAIKU),
            ({"reviewer": (OPUS, "high")}, "reviewer is not a stage"),
        ):
            with self.subTest(picks=picks), self.assertRaisesRegex(ValueError, error):
                choices.choose("claude", picks, **self.kw)
        self.assertFalse(self.file.exists())

    def test_a_saved_choice_returns_its_validated_catalogue_without_discovering_again(self):
        def listing(provider, workspace):
            if discover.call_count > 1:
                raise RuntimeError("second catalogue unavailable")
            # Preserve every choice read at save time, even when another role was
            # saved while discovery was running. Its model still constrains the view.
            choices.save({"opencode": {"tester": {"model": "openai/gpt-6-sol", "effort": "high"}}}, self.file)
            return LISTING

        discover = mock.Mock(side_effect=listing)

        current = choices.choose(
            "opencode",
            {"task_planner": ("opencode/plain", choices.OWN_DEFAULT)},
            **{**self.kw, "discover": discover},
        )
        self.assertIsNone(current["options_stop"], "an accepted save must not depend on a second discovery")
        self.assertEqual(1, discover.call_count)
        saved = choices.load(self.file)["opencode"]
        self.assertEqual({"model": "opencode/plain", "effort": None}, saved["task_planner"])
        self.assertEqual(saved["task_planner"], self.stage(current, "task_planner")["chosen"])
        self.assertEqual(saved["tester"], self.stage(current, "tester")["chosen"])
        builder = {row["model"]: row for row in self.stage(current, "builder")["options"]}
        self.assertIn("the Tester checks", builder["openai/gpt-6-sol"]["unavailable"])
        self.assertEqual(["low", "high", "max"], builder["zai-coding-plan/glm-5.3"]["effort_choices"])
        # A later operation still discovers afresh and refuses an unavailable backend.
        before = self.file.read_bytes()
        with self.assertRaisesRegex(ValueError, "second catalogue unavailable"):
            choices.choose(
                "opencode",
                {"task_planner": ("opencode/plain", choices.OWN_DEFAULT)},
                **{**self.kw, "discover": discover},
            )
        self.assertEqual(before, self.file.read_bytes())

    def test_a_builder_and_its_checkers_can_swap_models_in_one_change(self):
        choices.choose("claude", {"builder": (SONNET, "medium"), "tester": (OPUS, "high")}, **self.kw)
        swap = {"builder": (OPUS, "low"), "tester": (SONNET, "high"), "completion": (HAIKU, "medium")}
        current = choices.choose("claude", swap, **self.kw)
        self.assertEqual(OPUS, self.stage(current, "builder")["chosen"]["model"])
        self.assertEqual(SONNET, self.stage(current, "tester")["chosen"]["model"])

    def test_an_unreadable_listing_offers_nothing_and_says_why(self):
        def broken(provider, workspace):
            raise RuntimeError("cannot verify opencode login")

        current = choices.view("opencode", **{**self.kw, "discover": broken})
        self.assertEqual("cannot verify opencode login", current["options_stop"])
        self.assertEqual([], self.stage(current, "builder")["options"])
        with self.assertRaisesRegex(ValueError, "cannot list OpenCode's models"):
            choices.choose(
                "opencode", {"builder": ("zai-coding-plan/glm-5.3", "high")}, **{**self.kw, "discover": broken}
            )

    def test_a_saved_file_round_trips_and_a_broken_one_is_refused_by_name(self):
        every = {
            "claude": {"builder": {"model": HAIKU, "effort": "low"}, "task_planner": {"model": OPUS, "effort": None}}
        }
        choices.save(every, self.file)
        self.assertEqual(every, choices.load(self.file))
        for text, error in (
            ("[claude\n", "cannot be read as TOML"),
            ('[claude]\nreviewer = { model = "x" }\n', "reviewer is not a stage"),
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
            choices.reset("claude", "builder", **self.kw)
        choices.reset("claude", **self.kw)
        self.assertEqual({}, choices.load(self.file), "reset repairs an unreadable file")
        with self.assertRaisesRegex(ValueError, "reviewer is not a stage"):
            choices.reset("claude", "reviewer", **self.kw)

    def test_a_failed_write_leaves_the_saved_file_as_it_was(self):
        choices.save({"claude": {"builder": {"model": HAIKU, "effort": "low"}}}, self.file)
        with mock.patch.object(choices.os, "replace", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(OSError, "disk full"):
                choices.save({"claude": {"builder": {"model": OPUS, "effort": "low"}}}, self.file)
        self.assertEqual(HAIKU, choices.load(self.file)["claude"]["builder"]["model"])
        self.assertEqual(["stages.toml"], sorted(path.name for path in self.file.parent.iterdir()))

    def test_a_tool_that_cannot_list_its_models_offers_nothing(self):
        silent = SimpleNamespace(DEFAULT_MODELS={}, available_models=lambda workspace=None: None)
        current = choices.view("mine", **{**self.kw, "resolve": lambda name: silent})
        self.assertEqual("mine cannot list its models", current["options_stop"])
        self.assertEqual({"model": None, "effort": None, "offered": False}, self.stage(current, "builder")["default"])

    def test_codex_offers_its_bare_gpt_names_and_its_defaults_match_the_engines(self):
        current = choices.view("codex", **self.kw)
        self.assertEqual(
            list(choices.CODEX_MODELS), [row["model"] for row in self.stage(current, "builder")["options"]]
        )
        for role, model in autocode_configure.DEFAULT_ROLE_MODELS.items():
            self.assertEqual(model, self.stage(current, role)["default"]["model"], role)
        # Codex joint planning copies the astra route into the Requirements and Planner stages, and gives a
        # new Plan Reviewer the first model the Planner does not run.
        for role in ("requirements", "planner"):
            self.assertEqual(
                autocode_configure.DEFAULT_ROLE_MODELS["task_planner"], self.stage(current, role)["default"]["model"]
            )
        self.assertEqual(
            autocode_configure.CODEX_PLAN_REVIEWER_MODELS[0], self.stage(current, "plan_reviewer")["default"]["model"]
        )


class ApplyTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.file = Path(folder.name) / "stages.toml"
        choices.save(
            {
                "claude": {
                    "builder": {"model": SONNET, "effort": "high"},
                    "tester": {"model": OPUS, "effort": "xhigh"},
                    "task_planner": {"model": OPUS, "effort": None},
                }
            },
            self.file,
        )

    def args(self, **given):
        names = [f"{role}_{kind}" for role in choices.ORDER for kind in ("model", "reasoning_effort")]
        base = dict.fromkeys(names + ["single_model", "reasoning_effort", "replay_recording", "tester_provider"])
        return argparse.Namespace(**(base | given))

    def test_a_new_run_takes_the_saved_model_and_level_of_each_chosen_stage(self):
        args, said = self.args(), []
        self.assertEqual(
            ["builder", "tester", "task_planner"], choices.apply(args, "claude", file=self.file, say=said.append)
        )
        self.assertEqual((SONNET, "high"), (args.builder_model, args.builder_reasoning_effort))
        self.assertEqual((OPUS, "xhigh"), (args.tester_model, args.tester_reasoning_effort))
        self.assertEqual((OPUS, None), (args.task_planner_model, args.task_planner_reasoning_effort))
        self.assertIsNone(args.planner_model, "a stage nobody chose keeps the backend's default")
        self.assertEqual([f"Stage choices from {self.file}: Builder, Tester, Resolver and per-task Planner"], said)
        # The levels it supplied, which the run records as the person's ceiling (owner decision 2026-11-05).
        self.assertEqual(("builder", "tester"), args.stage_choice_efforts)

    def test_a_flag_or_project_setting_wins_and_whole_run_choices_take_nothing(self):
        args = self.args(builder_model=HAIKU, tester_reasoning_effort="low")
        choices.apply(args, "claude", file=self.file)
        self.assertEqual(
            (HAIKU, None), (args.builder_model, args.builder_reasoning_effort), "a typed model keeps its level"
        )
        self.assertEqual((OPUS, "low"), (args.tester_model, args.tester_reasoning_effort))
        self.assertEqual((), args.stage_choice_efforts, "a typed level is not a stage choice")
        shared = self.args(reasoning_effort="max")
        choices.apply(shared, "claude", file=self.file)
        self.assertEqual((SONNET, None), (shared.builder_model, shared.builder_reasoning_effort))
        for given in ({"single_model": HAIKU}, {"replay_recording": "/r"}, {}):
            args = self.args(**given)
            backend = "opencode" if not given else "claude"
            with self.subTest(given=given):
                self.assertEqual([], choices.apply(args, backend, file=self.file))
                self.assertIsNone(args.builder_model)
        routed = self.args(tester_provider="kilocode")
        choices.apply(routed, "claude", file=self.file)
        self.assertIsNone(routed.tester_model, "a stage another tool serves keeps that tool's route")

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
        builder = next(stage for stage in json.loads(out)["stages"] if stage["id"] == "builder")
        self.assertEqual({"model": SONNET, "effort": "high"}, builder["chosen"])
        self.assertEqual(
            ["stages", "set", "builder", "--backend", "claude", "--json", "--model", "MODEL", "--effort", "EFFORT"],
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
        self.assertEqual({"tester"}, set(choices.load()["claude"]))

    def test_set_json_keeps_the_catalogue_that_validated_the_saved_choice(self):
        with mock.patch.object(
            choices.route_discovery, "discover", side_effect=[LISTING, RuntimeError("catalogue went away")]
        ) as discover:
            code, out, err = self.run_cli(
                "set", "tester", "--model", "openai/gpt-6-sol", "--effort", "high", "--backend", "opencode", "--json"
            )
        self.assertEqual(0, code, err)
        current = json.loads(out)
        self.assertIsNone(current["options_stop"])
        self.assertEqual(1, discover.call_count)
        by_id = {stage["id"]: stage for stage in current["stages"]}
        self.assertEqual(choices.load()["opencode"]["tester"], by_id["tester"]["chosen"])
        self.assertEqual({"model": "openai/gpt-6-sol", "effort": "high"}, by_id["tester"]["chosen"])
        builder = {row["model"]: row for row in by_id["builder"]["options"]}
        self.assertIn("the Tester checks", builder["openai/gpt-6-sol"]["unavailable"])
        self.assertTrue(by_id["tester"]["reset"]["next_command"])

    def test_set_moves_checker_and_producer_together_where_one_at_a_time_cannot(self):
        # #3312: from the defaults (Builder Haiku, Tester Sonnet, Completion Reviewer Opus) to Builder Sonnet with
        # both checkers on Haiku, every single move is refused, so the target could not be reached at all.
        target = {"builder": SONNET, "tester": HAIKU, "completion": HAIKU}
        for role, model in target.items():
            code, _out, err = self.run_cli("set", role, "--model", model, "--effort", "medium", "--backend", "claude")
            self.assertEqual(2, code, role)
            self.assertIn("To change stages together, name each in one command", err)
            self.assertIn("autocode stages set --stage STAGE MODEL EFFORT", err)
        self.assertEqual("", self.file_text())
        code, out, err = self.run_cli(
            "set",
            "builder",
            "--model",
            SONNET,
            "--effort",
            "high",
            "--stage",
            "tester",
            HAIKU,
            "medium",
            "--stage",
            "completion-reviewer",
            HAIKU,
            "low",
            "--backend",
            "claude",
            "--json",
        )
        self.assertEqual(0, code, err)
        chosen = {stage["id"]: stage["chosen"] for stage in json.loads(out)["stages"] if stage["chosen"]}
        self.assertEqual(
            {
                "builder": {"model": SONNET, "effort": "high"},
                "tester": {"model": HAIKU, "effort": "medium"},
                "completion": {"model": HAIKU, "effort": "low"},
            },
            chosen,
        )
        # The way back needs no stage named positionally, and nothing is saved when one pick is refused.
        saved = self.file_text()
        code, _out, err = self.run_cli(
            "set", "--stage", "builder", HAIKU, "medium", "--stage", "tester", SONNET, "medium", "--backend", "claude"
        )
        self.assertEqual(2, code, "the Completion Reviewer still runs Haiku")
        self.assertIn("the Builder cannot run " + HAIKU, err)
        self.assertEqual(saved, self.file_text())

    def test_set_refuses_a_stage_named_twice_and_stage_picks_outside_set(self):
        for argv, error in (
            (("set", "tester", "--model", OPUS, "--effort", "high", "--stage", "tester", HAIKU, "low"), "tester twice"),
            (("set", "--stage", "builder", OPUS, "high", "--stage", "builder", HAIKU, "low"), "builder twice"),
            (("set", "tester", "--stage", "builder", OPUS, "high"), "needs a stage, --model and --effort"),
            (("list", "--stage", "builder", OPUS, "high"), "--stage names a stage for `autocode stages set`"),
            (("reset", "--stage", "builder", OPUS, "high"), "--stage names a stage for `autocode stages set`"),
        ):
            with self.subTest(argv=argv):
                code, _out, err = self.run_cli(*argv, "--backend", "claude")
                self.assertEqual(2, code)
                self.assertIn(error, err)
        self.assertEqual("", self.file_text())

    def test_a_new_chat_lists_its_stages_and_changes_them_for_itself_without_saving(self):
        """A chat that has not started shows the stages it will run and takes picks for itself alone."""
        self.assertEqual(
            0, self.run_cli("set", "tester", "--model", OPUS, "--effort", "high", "--backend", "claude")[0]
        )
        saved = self.file_text()
        code, out, _err = self.run_cli("list", "--backend", "claude", "--json")
        self.assertEqual(0, code)
        current = json.loads(out)
        builder = next(stage for stage in current["stages"] if stage["id"] == "builder")
        self.assertIsNone(builder["chat"])
        self.assertIsNone(builder["chat_reset"])
        self.assertEqual(
            {"start_args": [], "next_command": ["stages", "list", "--json", "--backend", "claude"]}, current["chat"]
        )
        self.assertEqual(
            ["stages", "list", "--json", "--backend", "claude", "--chat-stage", "builder", "MODEL", "EFFORT"],
            builder["chat_set"]["next_command"],
        )
        # The Builder's own pick, filled as `set` is: the Tester keeps its saved Opus.
        fill = {"MODEL": SONNET, "EFFORT": "high"}
        code, out, _err = self.run_cli(*[fill.get(word, word) for word in builder["chat_set"]["next_command"][1:]])
        self.assertEqual(0, code)
        picked = json.loads(out)
        by_id = {stage["id"]: stage for stage in picked["stages"]}
        self.assertEqual({"model": SONNET, "effort": "high"}, by_id["builder"]["chat"])
        self.assertIsNone(by_id["builder"]["chosen"], "the saved choices are untouched")
        self.assertEqual({"model": OPUS, "effort": "high"}, by_id["tester"]["chosen"])
        self.assertEqual(
            ["--builder-model", SONNET, "--builder-reasoning-effort", "high"], picked["chat"]["start_args"]
        )
        self.assertEqual(saved, self.file_text(), "a chat's pick is never saved")
        # Every other stage's command carries the Builder's pick, and the checkers are judged against it.
        self.assertEqual(
            [
                "stages",
                "list",
                "--json",
                "--backend",
                "claude",
                "--chat-stage",
                "builder",
                SONNET,
                "high",
                "--chat-stage",
                "tester",
                "MODEL",
                "EFFORT",
            ],
            by_id["tester"]["chat_set"]["next_command"],
        )
        sonnet = next(option for option in by_id["completion"]["options"] if option["model"] == SONNET)
        self.assertIn("checks the Builder's work, and the Builder runs " + SONNET, sonnet["unavailable"])
        self.assertEqual(
            ["stages", "list", "--json", "--backend", "claude"], by_id["builder"]["chat_reset"]["next_command"]
        )
        code, out, _err = self.run_cli("list", "--backend", "claude", "--chat-stage", "builder", SONNET, "high")
        self.assertIn(f"Builder: {SONNET} at high (this chat)", out)
        self.assertIn(f"Tester: {OPUS} at high (your choice)", out)

    def test_a_new_chats_pick_is_refused_as_a_saved_one_would_be(self):
        for given, reason in (
            (("builder", OPUS, "high", "--chat-stage", "tester", OPUS, "high"), "cannot run " + OPUS),
            (("builder", "nobody/model", "high"), "does not offer nobody/model"),
            (("builder", HAIKU, "turbo"), HAIKU + " takes"),
            (("nobody", SONNET, "high"), "nobody is not a stage"),
            (("builder", SONNET, "high", "--chat-stage", "builder", HAIKU, "low"), "names builder twice"),
        ):
            with self.subTest(given=given):
                code, out, err = self.run_cli("list", "--backend", "claude", "--json", "--chat-stage", *given)
                self.assertEqual((2, ""), (code, out))
                self.assertIn(reason, err)
        for action in (("set", "builder", "--model", SONNET, "--effort", "high"), ("reset",)):
            with self.subTest(action=action):
                code, _out, err = self.run_cli(
                    *action, "--backend", "claude", "--chat-stage", "builder", SONNET, "high"
                )
                self.assertEqual(2, code)
                self.assertIn("saves nothing", err)
        self.assertFalse(choices.path().exists())

    def test_a_new_chats_start_args_give_its_run_the_picks_over_the_saved_choices(self):
        self.assertEqual(
            0, self.run_cli("set", "builder", "--model", HAIKU, "--effort", "low", "--backend", "claude")[0]
        )
        self.assertEqual(
            0, self.run_cli("set", "tester", "--model", OPUS, "--effort", "high", "--backend", "claude")[0]
        )
        out = self.run_cli("list", "--backend", "claude", "--json", "--chat-stage", "builder", SONNET, "xhigh")[1]
        start = json.loads(out)["chat"]["start_args"]
        args = autocode_args.build_parser(None, autocode_args.DEFAULT_ROLE_MODELS).parse_args(["fix it", *start])
        choices.apply(args, "claude")
        self.assertEqual((SONNET, "xhigh"), (args.builder_model, args.builder_reasoning_effort))
        self.assertEqual((OPUS, "high"), (args.tester_model, args.tester_reasoning_effort), "the saved choice stays")
        self.assertEqual({"model": HAIKU, "effort": "low"}, choices.load()["claude"]["builder"])
        # A model that lists no levels runs at its own: no level flag, so no saved level is taken either.
        out = self.run_cli(
            "list", "--backend", "opencode", "--json", "--chat-stage", "builder", "opencode/plain", "default"
        )[1]
        self.assertEqual(["--builder-model", "opencode/plain"], json.loads(out)["chat"]["start_args"])

    def file_text(self):
        return choices.path().read_text() if choices.path().exists() else ""

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
        self.assertEqual({"model": SONNET, "effort": "high"}, saved["builder"])
        self.assertNotEqual(SONNET, saved["tester"]["model"])
        self.assertEqual("low", saved["task_planner"]["effort"])
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
        self.assertEqual({"model": "opencode/plain", "effort": None}, saved["task_planner"])

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

    def test_list_reads_project_model_and_effort_settings_without_touching_saved_choices(self):
        workspace = self.home / "project"
        (workspace / ".autocode").mkdir(parents=True)
        (workspace / "pyproject.toml").write_text(
            '[tool.autocode]\nglm-model = "openai/gpt-6-sol"\nterra-model = "zai-coding-plan/glm-5.3"\n'
            'sol-model = "opencode/plain"\n'
        )
        (workspace / ".autocode" / "config.toml").write_text(
            'planner-model = "zai-coding-plan/glm-5.3"\n'
            'builder-model = "xiaomi-token-plan-sgp/mimo-v2.6-pro"\n'
            'builder-reasoning-effort = "xhigh"\n'
        )
        self.assertEqual(
            0,
            self.run_cli("set", "tester", "--model", "openai/gpt-6-sol", "--effort", "high", "--backend", "opencode")[
                0
            ],
        )
        code, out, err = self.run_cli("list", "--workspace", str(workspace), "--backend", "opencode", "--json")
        self.assertEqual(0, code, err)
        by_id = {stage["id"]: stage for stage in json.loads(out)["stages"]}
        self.assertEqual(
            {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "effort": "xhigh", "source": "project"},
            by_id["builder"]["configured"],
            ".autocode/config.toml wins over pyproject.toml",
        )
        self.assertIsNone(by_id["builder"]["chosen"])
        self.assertEqual(
            {"model": "zai-coding-plan/glm-5.3", "effort": "high", "source": "project"},
            by_id["planner"]["configured"],
            "default medium effort fits to GLM's nearest offered level (high)",
        )
        self.assertEqual(
            {"model": "zai-coding-plan/glm-5.3", "effort": "high", "source": "project"},
            by_id["requirements"]["configured"],
            "requirements inherits planner-model when neither requirements nor stages.toml sets it",
        )
        self.assertEqual(
            {"model": "opencode/plain", "effort": None, "source": "project"},
            by_id["tester"]["configured"],
            "a model declaring no levels runs at its own default",
        )
        self.assertEqual(
            {"model": "openai/gpt-6-sol", "effort": "high"},
            by_id["tester"]["chosen"],
            "stages.toml stays untouched",
        )
        glm_on_reviewer = next(o for o in by_id["plan_reviewer"]["options"] if o["model"] == "zai-coding-plan/glm-5.3")
        self.assertIn(
            "checks the Planner's work, and the Planner runs zai-coding-plan/glm-5.3", glm_on_reviewer["unavailable"]
        )
        code, _out, err = self.run_cli(
            "list",
            "--workspace",
            str(workspace),
            "--backend",
            "opencode",
            "--chat-stage",
            "plan_reviewer",
            "zai-coding-plan/glm-5.3",
            "high",
        )
        self.assertEqual(2, code)
        self.assertIn("cannot run zai-coding-plan/glm-5.3", err)
        # Saved requirements stops planner->requirements inheritance; explicit requirements-model overrides both.
        self.assertEqual(
            0,
            self.run_cli(
                "set", "requirements", "--model", "openai/gpt-6-sol", "--effort", "medium", "--backend", "opencode"
            )[0],
        )
        by_id = {
            stage["id"]: stage
            for stage in json.loads(
                self.run_cli("list", "--workspace", str(workspace), "--backend", "opencode", "--json")[1]
            )["stages"]
        }
        self.assertIsNone(by_id["requirements"]["configured"])
        (workspace / ".autocode" / "config.toml").write_text(
            'planner-model = "zai-coding-plan/glm-5.3"\n'
            'requirements-model = "opencode/plain"\n'
            'builder-model = "xiaomi-token-plan-sgp/mimo-v2.6-pro"\n'
        )
        code, text_out, err = self.run_cli("list", "--workspace", str(workspace), "--backend", "opencode")
        self.assertEqual(0, code, err)
        self.assertIn("Requirements: opencode/plain at its own default level (project)", text_out)
        unlisted_by_id = {
            stage["id"]: stage
            for stage in json.loads(
                self.run_cli("list", "--workspace", str(workspace), "--backend", "opencode", "--json")[1]
            )["stages"]
        }
        self.assertEqual(
            {"model": "xiaomi-token-plan-sgp/mimo-v2.6-pro", "effort": "medium", "source": "project"},
            unlisted_by_id["builder"]["configured"],
            "unoffered model without explicit effort keeps the stage fallback effort",
        )

    def test_list_resolves_codex_inheritance_and_shared_reasoning_effort_overrides(self):
        workspace = self.home / "project"
        (workspace / ".autocode").mkdir(parents=True)
        self.assertEqual(
            0, self.run_cli("set", "builder", "--model", "gpt-5.6-terra", "--effort", "medium", "--backend", "codex")[0]
        )
        self.assertEqual(
            0, self.run_cli("set", "planner", "--model", "gpt-5.6-sol", "--effort", "xhigh", "--backend", "codex")[0]
        )
        (workspace / ".autocode" / "config.toml").write_text(
            'engine = "codex"\ntask-planner-model = "gpt-5.6-terra"\n'
            'task-planner-reasoning-effort = "high"\nreasoning-effort = "low"\n'
        )
        by_id = {
            stage["id"]: stage
            for stage in json.loads(self.run_cli("list", "--workspace", str(workspace), "--json")[1])["stages"]
        }
        self.assertEqual(
            {"model": "gpt-5.6-terra", "effort": "high", "source": "project"}, by_id["requirements"]["configured"]
        )
        self.assertEqual(
            {"model": "gpt-5.6-sol", "effort": "high", "source": "project"}, by_id["planner"]["configured"]
        )
        self.assertEqual(
            {"model": "gpt-6-astra", "effort": "high", "source": "project"}, by_id["plan_reviewer"]["configured"]
        )
        self.assertEqual(
            {"model": "gpt-5.6-terra", "effort": "low", "source": "project"}, by_id["builder"]["configured"]
        )
        self.assertEqual(
            {"model": "gpt-5.6-sol", "effort": "low", "source": "project"}, by_id["completion"]["configured"]
        )
        # On OpenCode, reasoning-effort overrides execution roles and strips saved planning efforts to default.
        self.assertEqual(
            0,
            self.run_cli("set", "planner", "--model", "openai/gpt-6-sol", "--effort", "xhigh", "--backend", "opencode")[
                0
            ],
        )
        self.assertEqual(
            0,
            self.run_cli(
                "set",
                "builder",
                "--model",
                "zai-coding-plan/glm-5.3",
                "--effort",
                "high",
                "--stage",
                "completion",
                "opencode/plain",
                "default",
                "--backend",
                "opencode",
            )[0],
        )
        (workspace / ".autocode" / "config.toml").write_text(
            'reasoning-effort = "low"\ntester-reasoning-effort = "medium"\n'
        )
        opencode_by_id = {
            stage["id"]: stage
            for stage in json.loads(
                self.run_cli("list", "--workspace", str(workspace), "--backend", "opencode", "--json")[1]
            )["stages"]
        }
        self.assertEqual(
            {"model": "zai-coding-plan/glm-5.3", "effort": "low", "source": "project"},
            opencode_by_id["builder"]["configured"],
        )
        self.assertEqual(
            {"model": "openai/gpt-6-sol", "effort": "medium", "source": "project"},
            opencode_by_id["planner"]["configured"],
        )
        self.assertEqual(
            {"model": "openai/gpt-6-sol", "effort": "medium", "source": "project"},
            opencode_by_id["tester"]["configured"],
        )
        with mock.patch.object(choices.route_discovery, "discover", side_effect=RuntimeError("offline")):
            offline_by_id = {
                stage["id"]: stage
                for stage in json.loads(
                    self.run_cli("list", "--workspace", str(workspace), "--backend", "opencode", "--json")[1]
                )["stages"]
            }
        self.assertIsNone(offline_by_id["tester"]["configured"], "unoffered defaults stay unconfigured when offline")

    def test_list_applies_trailing_new_run_options_and_refuses_invalid_uses(self):
        workspace = self.home / "project"
        (workspace / ".autocode").mkdir(parents=True)
        (workspace / ".autocode" / "config.toml").write_text(
            'engine = "codex"\nplanner-model = "zai-coding-plan/glm-5.3"\nbuilder-model = "opencode/plain"\n'
        )
        tail = [
            "--",
            "--provider=opencode",
            "--builder-model",
            "openai/gpt-6-sol",
            "--builder-reasoning-effort=high",
            "--resolver-model",
            "openai/gpt-6-sol",
            "--resolver-reasoning-effort",
            "high",
            "--plan-reviewer-model",
            "opencode/plain",
            "--completion-model",
            "opencode/plain",
        ]
        code, out, err = self.run_cli("list", "--workspace", str(workspace), "--json", *tail)
        self.assertEqual(0, code, err)
        current = json.loads(out)
        self.assertEqual("opencode", current["backend"])
        by_id = {stage["id"]: stage for stage in current["stages"]}
        self.assertEqual(
            {"model": "openai/gpt-6-sol", "effort": "high", "source": "settings"}, by_id["builder"]["configured"]
        )
        self.assertEqual(
            {"model": "zai-coding-plan/glm-5.3", "effort": "high", "source": "project"}, by_id["planner"]["configured"]
        )
        self.assertEqual(
            [
                "stages",
                "list",
                "--json",
                "--workspace",
                str(workspace),
                "--chat-stage",
                "builder",
                "MODEL",
                "EFFORT",
                *tail,
            ],
            by_id["builder"]["chat_set"]["next_command"],
        )
        # Following the named chat_set command checks against configured options and preserves the tail.
        fill = {"MODEL": "zai-coding-plan/glm-5.3", "EFFORT": "high"}
        step = [fill.get(word, word) for word in by_id["builder"]["chat_set"]["next_command"][1:]]
        code, picked_out, err = self.run_cli(*step)
        self.assertEqual(0, code, err)
        picked = json.loads(picked_out)
        picked_by_id = {stage["id"]: stage for stage in picked["stages"]}
        self.assertEqual({"model": "zai-coding-plan/glm-5.3", "effort": "high"}, picked_by_id["builder"]["chat"])
        self.assertEqual(
            ["stages", "list", "--json", "--workspace", str(workspace), *tail],
            picked_by_id["builder"]["chat_reset"]["next_command"],
        )
        # --engine opencode overrides project engine = "codex"; --reasoning-effort after -- sets shared effort; --engine claude selects claude.
        from tools import autocode_stages_cli as package_stages_cli

        self.assertEqual("builder", package_stages_cli._ROLE_MODEL_FLAGS["--builder-model"])
        shared = json.loads(
            self.run_cli(
                "list",
                "--workspace",
                str(workspace),
                "--json",
                "--",
                "--engine",
                "opencode",
                "--reasoning-effort",
                "low",
            )[1]
        )
        self.assertEqual("opencode", shared["backend"])
        shared_by_id = {stage["id"]: stage for stage in shared["stages"]}
        self.assertEqual(
            {"model": "openai/gpt-6-sol", "effort": "low", "source": "settings"},
            shared_by_id["tester"]["configured"],
        )
        self.assertEqual(
            "claude",
            json.loads(self.run_cli("list", "--workspace", str(workspace), "--json", "--", "--engine", "claude")[1])[
                "backend"
            ],
        )
        self.assertEqual(
            "codex",
            json.loads(self.run_cli("list", "--workspace", str(workspace), "--json", "--", "--engine", "codex")[1])[
                "backend"
            ],
        )
        for bad_argv, message in (
            (
                (
                    "set",
                    "builder",
                    "--model",
                    "openai/gpt-6-sol",
                    "--effort",
                    "high",
                    "--",
                    "--builder-model",
                    "opencode/plain",
                ),
                "nothing is saved",
            ),
            (("list", "--run-dir", str(workspace), "--", "--builder-model", "opencode/plain"), "nothing is saved"),
            (("list", "--workspace", str(workspace), "--", "positional"), "unexpected argument after --"),
            (("list", "--workspace", str(workspace), "--", "--builder-model"), "needs a value"),
            (("list", "--workspace", str(workspace), "--", "--unknown-flag", "x"), "unsupported new-run option"),
            (("list", "--workspace", str(workspace), "--", "--engine", "bad"), "--engine must be one of"),
            (("list", "--workspace", str(workspace), "--", "--provider="), "--provider is not valid"),
            (("list", "--workspace", str(workspace), "--", "--provider", "codex"), "codex is an engine"),
            (
                ("list", "--workspace", str(workspace), "--", "--engine", "claude", "--provider", "kilocode"),
                "cannot run --provider kilocode",
            ),
            (("list", "--workspace", str(workspace), "--", "--builder-model", "bad model"), "not a valid model name"),
            (("list", "--workspace", str(workspace), "--", "--builder-reasoning-effort", "turbo"), "must be one of"),
            (
                (
                    "list",
                    "--workspace",
                    str(workspace),
                    "--",
                    "--single-model",
                    "openai/gpt-6-sol",
                    "--builder-model",
                    "opencode/plain",
                ),
                "cannot be combined",
            ),
        ):
            with self.subTest(bad_argv=bad_argv):
                bad_code, _out, bad_err = self.run_cli(*bad_argv)
                self.assertEqual(2, bad_code)
                self.assertIn(message, bad_err)

    def test_single_model_locks_chat_stage_changes_while_role_flag_drops_project_single_model(self):
        workspace = self.home / "project"
        (workspace / ".autocode").mkdir(parents=True)
        (workspace / ".autocode" / "config.toml").write_text('single-model = "zai-coding-plan/glm-5.3"\n')
        self.assertEqual(
            0,
            self.run_cli("set", "planner", "--model", "openai/gpt-6-sol", "--effort", "high", "--backend", "opencode")[
                0
            ],
        )
        current = json.loads(self.run_cli("list", "--workspace", str(workspace), "--backend", "opencode", "--json")[1])
        self.assertEqual(False, current["chat"]["changeable"])
        self.assertIn("single-model", current["chat"]["reason"])
        self.assertEqual([], current["chat"]["start_args"])
        for stage in current["stages"]:
            self.assertEqual(
                {"model": "zai-coding-plan/glm-5.3", "effort": "high", "source": "project"}, stage["configured"]
            )
            self.assertIsNone(stage["chat_set"])
            self.assertTrue(all(o["unavailable"] is None for o in stage["options"]))
        # Global stages.toml remains editable in view() even when workspace single-model is set.
        self.assertNotIn("changeable", choices.view("opencode", workspace))
        code, _out, err = self.run_cli(
            "list",
            "--workspace",
            str(workspace),
            "--backend",
            "opencode",
            "--chat-stage",
            "builder",
            "openai/gpt-6-sol",
            "high",
        )
        self.assertEqual(2, code)
        self.assertIn("--single-model cannot be combined", err)
        # A per-role model flag in new-run options drops project single-model cleanly.
        overridden = json.loads(
            self.run_cli(
                "list",
                "--workspace",
                str(workspace),
                "--backend",
                "opencode",
                "--json",
                "--",
                "--builder-model",
                "openai/gpt-6-sol",
            )[1]
        )
        self.assertNotIn("changeable", overridden["chat"])
        by_id = {stage["id"]: stage for stage in overridden["stages"]}
        self.assertEqual(
            {"model": "openai/gpt-6-sol", "effort": "medium", "source": "settings"}, by_id["builder"]["configured"]
        )
        self.assertIsNone(by_id["tester"]["configured"])
        # Conversely, --single-model in new-run options drops project role models, and invalid project combo is refused.
        (workspace / ".autocode" / "config.toml").write_text('builder-model = "openai/gpt-6-sol"\n')
        single_override = json.loads(
            self.run_cli(
                "list",
                "--workspace",
                str(workspace),
                "--backend",
                "opencode",
                "--json",
                "--",
                "--single-model",
                "zai-coding-plan/glm-5.3",
                "--builder-reasoning-effort",
                "low",
            )[1]
        )
        self.assertEqual(False, single_override["chat"]["changeable"])
        self.assertEqual(
            [
                "stages",
                "list",
                "--json",
                "--backend",
                "opencode",
                "--workspace",
                str(workspace),
                "--",
                "--single-model",
                "zai-coding-plan/glm-5.3",
                "--builder-reasoning-effort",
                "low",
            ],
            single_override["chat"]["next_command"],
        )
        single_by_id = {stage["id"]: stage for stage in single_override["stages"]}
        self.assertEqual(
            {"model": "zai-coding-plan/glm-5.3", "effort": "high", "source": "settings"},
            single_by_id["planner"]["configured"],
        )
        self.assertEqual(
            {"model": "zai-coding-plan/glm-5.3", "effort": "low", "source": "settings"},
            single_by_id["builder"]["configured"],
        )
        (workspace / ".autocode" / "config.toml").write_text(
            'single-model = "zai-coding-plan/glm-5.3"\nbuilder-model = "openai/gpt-6-sol"\n'
        )
        bad_code, _out, bad_err = self.run_cli("list", "--workspace", str(workspace), "--backend", "opencode", "--json")
        self.assertEqual(2, bad_code)
        self.assertIn(f"{workspace / '.autocode' / 'config.toml'}: single-model cannot be combined", bad_err)


if __name__ == "__main__":
    unittest.main()
