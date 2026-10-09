"""The extracted run-configuration module: import purity, surface, wiring.

Covers the autocode_configure extraction contract: the module imports nothing
from the CLI or the controller, keeps the shared names resolvable on the runner
module for the frozen dashboard and status command, and the runner wrappers
pass the same collaborators a direct call receives.

The module-level imports of autocode_configure and tests.test_planning are
guarded so a pre-extraction tree (the regression proof's base, which also lacks
tools/autocode_configure.py for test_planning's own module import) still
collects this module and fails each criterion case individually instead of
erroring at collection time. The test_ac* aliases run the contract-named
test_c* bodies unchanged; the regression proof discovers criterion cases by
their test_ac<id>_ names.
"""
import ast
import copy
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import autocode as runner
import autocode_milestones as milestones
import autocode_opencode as oc
import autocode_planning as planning
import autocode_providers
import autocode_support as support
import autopilot
try:
    import autocode_configure
except ImportError:  # pre-extraction base: each case below fails, not collection
    autocode_configure = None
from . import test_architecture
try:
    from . import test_planning
except ImportError:  # the migrated fixture module imports autocode_configure too
    test_planning = None

REPO = Path(__file__).resolve().parents[1]


def require_extraction():
    """Fail the calling case when the extraction under test is absent."""
    if autocode_configure is None or test_planning is None:
        raise AssertionError(
            "tools/autocode_configure.py is not importable in this tree; "
            "the extraction this module verifies has not been applied")


class ConfigureModuleImportTests(unittest.TestCase):
    def test_c1_configure_module_imports_no_runner_modules(self):
        require_extraction()
        source = (REPO / "tools" / "autocode_configure.py").read_text()
        tree = ast.parse(source)
        entry = {id(node) for block in tree.body if test_architecture.script_entry(block)
                 for node in ast.walk(block)}
        imports = set()
        for node in ast.walk(tree):
            if id(node) in entry:
                continue
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom):
                base = (node.module or "").removeprefix("autocode_cli.")
                if base:
                    imports.add(base)
                imports.update(f"{base}.{alias.name}" if base else alias.name for alias in node.names)
        for forbidden in ("autocode", "autopilot", "autocode_cli.autocode"):
            self.assertNotIn(forbidden, imports)
        for name in sorted(imports):
            self.assertFalse(name.startswith(("autocode.", "autopilot.", "autocode_cli.autocode.")),
                             f"{name} reaches a forbidden module")
        # tests/test_architecture.py's cycle detection must not report the new module.
        self.assertNotIn("autocode_configure",
                         test_architecture.modules_in_cycles(test_architecture.import_graph()))

    # The regression-proof gate discovers criterion cases as test_ac<id>_*;
    # the alias runs the contract-named body above, unchanged.
    test_ac1_configure_module_imports_no_runner_modules = test_c1_configure_module_imports_no_runner_modules


class ConfigureModuleSurfaceTests(unittest.TestCase):
    def test_c2_configure_module_exports_and_compat_surface(self):
        require_extraction()
        for name in ("configure", "configure_joint", "configure_codex_joint",
                     "migrate_opencode_roles", "_provider_model", "budget_origins", "check_subscription",
                     "BUDGET_ARGUMENTS", "DEFAULT_ROLE_MODELS", "DEFAULT_ENGINE"):
            self.assertTrue(hasattr(autocode_configure, name), name)
        self.assertEqual({"astra": "gpt-5.6-sol", "terra": "gpt-5.6-terra", "sol": "gpt-5.6-sol",
                          "completion": "gpt-5.6-sol"}, autocode_configure.DEFAULT_ROLE_MODELS)
        self.assertEqual("opencode", autocode_configure.DEFAULT_ENGINE)
        # The runner module keeps resolving the shared names (frozen dashboard,
        # status command and argparse all read them through autocode).
        self.assertTrue(callable(runner.configure))
        self.assertTrue(callable(runner.check_subscription))
        self.assertEqual(autocode_configure.DEFAULT_ROLE_MODELS, runner.DEFAULT_ROLE_MODELS)
        self.assertEqual(autocode_configure.DEFAULT_ENGINE, runner.DEFAULT_ENGINE)
        self.assertEqual(autocode_configure.BUDGET_ARGUMENTS, runner.BUDGET_ARGUMENTS)

    # The regression-proof gate discovers criterion cases as test_ac<id>_*;
    # the alias runs the contract-named body above, unchanged.
    test_ac2_configure_module_exports_and_compat_surface = test_c2_configure_module_exports_and_compat_surface


class WrapperWiringTests(unittest.TestCase):
    def setUp(self):
        require_extraction()
        test_planning.PlanningTests.setUp(self)
        # main() rebinds runner.opencode to the selected provider; pin the
        # builtin module so both calls below see the same facade.
        facade = patch.object(runner, "opencode", oc)
        facade.start()
        self.addCleanup(facade.stop)

    def configure_args(self, **overrides):
        require_extraction()
        return test_planning.PlanningTests.configure_args(self, **overrides)

    def test_c3_wrapper_wiring_matches_direct_call(self):
        require_extraction()
        args = self.configure_args()
        state = {"workspace": "/tmp/fixture", "iteration": 0}
        with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}), \
             patch.object(runner.opencode, "local_settings", return_value={"engine": "opencode"}):
            through_wrapper = runner.configure(args, state)
            direct = autocode_configure.configure(args, state, planning=planning, milestones=milestones,
                                                  autopilot=autopilot)
        self.assertEqual(through_wrapper, direct)
        self.assertEqual("opencode", direct["engine"])
        self.assertEqual("runner_default", direct["budget_origins"]["iteration_ceiling"])
        for role, config in direct["roles"].items():
            self.assertEqual("opencode", config["engine"], role)

    def test_fresh_review_limit_matches_resume_without_mutating_inputs_or_other_budgets(self):
        for requested, expected in ((None, 3), (1, 1), (0, None)):
            with self.subTest(requested=requested):
                args = self.configure_args(max_milestone_stalled_reviews=requested)
                state = {"workspace": "/tmp/fixture", "iteration": 0}
                original_args, original_state = copy.deepcopy(vars(args)), copy.deepcopy(state)
                with patch.object(support, "local_settings", return_value={"auth_mode": "ChatGPT"}), \
                     patch.object(runner.opencode, "local_settings", return_value={"engine": "opencode"}):
                    fresh = autocode_configure.configure(args, state, planning=planning, milestones=milestones,
                                                         autopilot=autopilot)
                    self.assertEqual(expected, fresh['milestone_checkpoints']['stalled_reviews'])
                    saved = {**state, "settings": copy.deepcopy(fresh)}
                    saved['settings']['milestone_checkpoints']['stalled_reviews'] = 7
                    original_saved = copy.deepcopy(saved)
                    resumed = autocode_configure.configure(args, saved, planning=planning, milestones=milestones,
                                                           autopilot=autopilot)
                self.assertEqual(7 if requested is None else expected,
                                 resumed['milestone_checkpoints']['stalled_reviews'])
                self.assertEqual(original_saved, saved, 'Configure must return a changed copy of saved settings')
                self.assertEqual(original_state, state)
                self.assertEqual(original_args, vars(args))
                self.assertEqual(fresh['builder_retry'], resumed['builder_retry'])
                self.assertEqual(fresh['limits'], resumed['limits'])
                self.assertEqual(milestones.DEFAULTS['max_seconds'], fresh['milestone_checkpoints']['max_seconds'])
                self.assertEqual(milestones.DEFAULTS['max_replans'], fresh['milestone_checkpoints']['max_replans'])
        self.assertEqual(3, milestones.DEFAULTS['stalled_reviews'], 'Explicit requests must not mutate defaults')

    # The regression-proof gate discovers criterion cases as test_ac<id>_*;
    # the alias runs the contract-named body above, unchanged.
    test_ac3_wrapper_wiring_matches_direct_call = test_c3_wrapper_wiring_matches_direct_call


class SelectedProviderFacadeTests(unittest.TestCase):
    def setUp(self):
        # Register fixturetool exactly as tests/test_command_flow.py
        # ConfigToolFlow does: a TOML under a temporary XDG_CONFIG_HOME plus
        # tools/fake_command_tool.py on PATH as fixture-tool. Unlike
        # PlanningTests.setUp this must allow subprocesses: the provider's
        # local_settings runs its version_command.
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        source = Path(runner.__file__).resolve().parent
        bin_dir = self.root / "bin"
        bin_dir.mkdir()
        shutil.copy2(source / "fake_command_tool.py", bin_dir / "fixture-tool")
        shutil.copy2(source / "goal_fixtures.py", bin_dir / "goal_fixtures.py")
        (bin_dir / "fixture-tool").chmod(0o755)
        config_home = self.root / "config"
        provider = config_home / "autocode" / "providers"
        provider.mkdir(parents=True)
        (provider / "fixturetool.toml").write_text(textwrap.dedent("""\
            name = "fixturetool"
            command = ["fixture-tool", "--report", "{report}", "--sandbox", "{sandbox}", "--model", "{model}", "--role", "{role}"]
            prompt = "stdin"
            models = ["fixture-reviewer", "fixture-builder", "fixture-validator", "fixture-completion", "fixture-planner", "fixture-plan-reviewer"]
            version_command = ["fixture-tool", "--version"]

            [roles]
            astra = { model = "fixture-reviewer", effort = "high" }
            terra = { model = "fixture-builder", effort = "medium" }
            sol = { model = "fixture-validator", effort = "high" }
            completion = { model = "fixture-completion", effort = "medium" }
            glm = { model = "fixture-planner", effort = "medium" }
            plan_reviewer = { model = "fixture-plan-reviewer", effort = "high" }
        """))
        environment = patch.dict(os.environ, {"PATH": str(bin_dir) + os.pathsep + os.environ["PATH"],
                                              "XDG_CONFIG_HOME": str(config_home)})
        environment.start()
        self.addCleanup(environment.stop)
        previous_provider = os.environ.pop("AUTOCODE_PROVIDER", None)
        if previous_provider is not None:
            self.addCleanup(os.environ.__setitem__, "AUTOCODE_PROVIDER", previous_provider)

    def configure_args(self, **overrides):
        require_extraction()
        return test_planning.PlanningTests.configure_args(self, **overrides)

    def test_c7_selected_provider_facade_used_by_configure(self):
        require_extraction()
        provider = autocode_providers.resolve("fixturetool")
        args = self.configure_args(provider="fixturetool")
        state = {"workspace": str(self.root), "iteration": 0}
        direct = autocode_configure.configure(args, state, planning=planning, milestones=milestones,
                                              autopilot=autopilot, opencode=provider)
        previous = runner.opencode
        runner.opencode = provider
        try:
            through_wrapper = runner.configure(args, state)
        finally:
            runner.opencode = previous
        for settings in (direct, through_wrapper):
            self.assertEqual("fixturetool", settings["provider"])
            self.assertEqual("fixturetool", settings["transport_identity"]["engine"])
            self.assertEqual("fixture-reviewer", settings["roles"]["astra"]["model"])
            self.assertEqual("fixture-builder", settings["roles"]["terra"]["model"])
            self.assertEqual("high", settings["roles"]["astra"]["reasoning_effort"])

    # The regression-proof gate discovers criterion cases as test_ac<id>_*;
    # the alias runs the contract-named body above, unchanged.
    test_ac7_selected_provider_facade_used_by_configure = test_c7_selected_provider_facade_used_by_configure


class PackageModeImportTests(unittest.TestCase):
    def test_c8_package_mode_imports_and_reexports(self):
        require_extraction()
        code = ("import tools.autocode as runner, tools.autocode_configure as configure; "
                "assert callable(configure.configure) and callable(runner.configure) "
                "and callable(runner.check_subscription); "
                "assert runner.DEFAULT_ENGINE == 'opencode'; "
                "assert runner.BUDGET_ARGUMENTS['iteration_ceiling'][0] == 'max_iterations'")
        result = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_c9_installed_package_imports_configure_module(self):
        require_extraction()
        # The interpreter running the tests has this checkout installed (editable, as autocode_cli); a fixed
        # .venv path exists only on a developer machine, not in CI.
        if subprocess.run([sys.executable, "-c", "import autocode_cli"], cwd=str(REPO),
                          capture_output=True).returncode != 0:
            self.skipTest("autocode_cli is not installed for this interpreter; "
                          "install the checkout editable (`pip install -e .`) to run this case")
        code = ("import autocode_cli.autocode as runner, autocode_cli.autocode_configure as configure; "
                "assert callable(runner.configure) and callable(configure.configure) "
                "and callable(runner.check_subscription); "
                "assert runner.DEFAULT_ENGINE == 'opencode'; "
                "assert runner.BUDGET_ARGUMENTS['iteration_ceiling'][0] == 'max_iterations'")
        result = subprocess.run([sys.executable, "-c", code], cwd=str(REPO), capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    # The regression-proof gate discovers criterion cases as test_ac<id>_*;
    # the aliases run the contract-named bodies above, unchanged.
    test_ac8_package_mode_imports_and_reexports = test_c8_package_mode_imports_and_reexports
    test_ac9_installed_package_imports_configure_module = test_c9_installed_package_imports_configure_module


if __name__ == "__main__":
    unittest.main()
