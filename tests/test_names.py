"""The job names that replace AutoCode's old code names, step 1: one table, and the new spellings work."""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import autocode_args
import autocode_conversation_policy as conversation_policy
import autocode_conversation_session as conversation_session
import autocode_names as names
import autocode_project_config as project_config
import autocode_stage_choices as choices
import autocode_stages_cli as stages_cli
from providers import opencode


def parse(*argv):
    with contextlib.redirect_stderr(io.StringIO()):
        args, _ = autocode_args.parse(None, ["Build it", *argv], opencode.DEFAULT_MODELS)
    return args


class OneTableOfNames(unittest.TestCase):
    def test_every_old_route_and_stage_has_one_new_name_and_no_new_name_is_taken_twice(self):
        for table in (names.ROUTES, names.STAGES):
            self.assertEqual(len(table), len(set(table.values())), table)
            self.assertFalse(set(table) & set(table.values()), "a new name must not be an old one")
        self.assertEqual("builder", names.route("terra"))
        self.assertEqual("plan_reviewer", names.route("plan_reviewer"), "a name that is already clear stays")
        self.assertEqual("build", names.stage("terra"))

    def test_a_retired_route_becomes_its_job_name_and_a_route_not_yet_renamed_stays(self):
        self.assertEqual("task_planner", names.current_route("astra"))
        self.assertEqual("sol", names.current_route("sol"), "the Tester's route is renamed with the stages")
        self.assertEqual("astra", names.file_stem("task_planner"), "its schema files keep the old stem")
        self.assertEqual("terra", names.file_stem("terra"))
        kept = {"astra": {"model": "a"}, "terra": {"model": "b"}}
        self.assertEqual({"task_planner": {"model": "a"}, "terra": {"model": "b"}}, names.renamed_routes(kept, "x"))
        with self.assertRaisesRegex(ValueError, "names the planner route twice"):
            names.renamed_routes({"glm": {}, "planner": {}}, "provider config [roles]")

    def test_the_legacy_planning_stages_do_not_reuse_the_v2_flows_names(self):
        v2 = {"requirements", "plan", "plan_review", "plan_revise", "plan_finalize"}
        self.assertFalse(v2 & set(names.STAGES.values()))

    def test_each_old_flag_has_a_job_named_spelling_and_other_flags_have_one(self):
        self.assertEqual(("--builder-model", "--terra-model"), names.spellings("--terra-model"))
        self.assertEqual(("--task-planner-provider", "--astra-provider"), names.spellings("--astra-provider"))
        self.assertEqual(("--completion-model",), names.spellings("--completion-model"))
        self.assertEqual("--glm-reasoning-effort=high", names.legacy_flag("--planner-reasoning-effort=high"))
        self.assertEqual("--max-seconds", names.legacy_flag("--max-seconds"))
        for new in names.FLAG_ALIASES:
            self.assertNotRegex(new, r"astra|glm|terra|sol\b")


class TheNewSpellingsWork(unittest.TestCase):
    def test_a_new_flag_sets_what_its_old_spelling_sets(self):
        for old in names.OLD_FLAGS:
            value = "high" if old.endswith("reasoning-effort") else "vendor/model-x"
            # The destination follows the route's name: renamed routes take the new one (planner_model).
            route, _, suffix = old.removeprefix("--").partition("-")
            if route in names.RETIRED_ROUTES:
                route = names.route(route)
            dest = f"{route}_{suffix.replace('-', '_')}"
            with self.subTest(flag=old):
                self.assertEqual(value, getattr(parse(old, value), dest))
                self.assertEqual(value, getattr(parse(names.flag(old), value), dest))
                self.assertEqual(value, getattr(parse(f"{names.flag(old)}={value}"), dest))

    def test_a_conversation_takes_the_new_spellings_and_reads_the_planners_model_from_them(self):
        options = conversation_policy.creation_options(["--builder-model", "m1", "--planner-model=m2"])
        self.assertEqual(("--builder-model", "m1", "--planner-model", "m2"), options)
        self.assertEqual("m2", conversation_session._option(options, "--glm-model"))
        self.assertEqual("m1", conversation_session._option(("--terra-model=m1",), "--terra-model"))

    def test_a_project_file_may_use_the_new_keys_but_not_both_spellings_of_one(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder) / ".autocode" / "config.toml"
            config.parent.mkdir()
            config.write_text('builder-model = "vendor/b"\ntester-reasoning-effort = "low"\n')
            settings = project_config.read(folder)
            self.assertEqual("vendor/b", settings["builder-model"][0])
            with contextlib.redirect_stderr(io.StringIO()):
                args, _ = autocode_args.parse(None, ["Build it", "--workspace", folder], opencode.DEFAULT_MODELS)
            self.assertEqual(("vendor/b", "low"), (args.terra_model, args.sol_reasoning_effort))
            config.write_text('builder-model = "vendor/b"\nterra-model = "vendor/c"\n')
            with self.assertRaisesRegex(ValueError, "terra-model set the same thing"):
                project_config.read(folder)
            config.write_text('builder-provider = "x"\n')
            with self.assertRaisesRegex(ValueError, "cannot be set by a project"):
                project_config.read(folder)

    def test_stages_toml_and_autocode_stages_take_the_job_names(self):
        with tempfile.TemporaryDirectory() as folder:
            file = Path(folder) / "stages.toml"
            file.write_text('[claude]\nbuilder = { model = "m-b" }\nplanner = { model = "m-p", effort = "high" }\n')
            self.assertEqual(
                {"terra": {"model": "m-b", "effort": None}, "planner": {"model": "m-p", "effort": "high"}},
                choices.load(file)["claude"],
            )
            # A file saved before the routes were renamed keeps its choices under the new names.
            file.write_text('[claude]\nglm = { model = "m-p" }\nastra = { model = "m-a" }\n')
            self.assertEqual({"planner", "task_planner"}, set(choices.load(file)["claude"]))
            file.write_text('[claude]\nbuilder = { model = "m-b" }\nterra = { model = "m-t" }\n')
            with self.assertRaisesRegex(ValueError, "names terra twice"):
                choices.load(file)
        self.assertEqual("task_planner", stages_cli.ALIASES["task-planner"])
        self.assertIn("task-planner", stages_cli.STAGE_NAMES)


if __name__ == "__main__":
    unittest.main()
