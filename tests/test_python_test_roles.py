"""Declared product modules stay out of test overlays and protected oracles."""

import copy
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_changed_code_trigger as changed_code_trigger
import autocode_protected_oracles as protected
import autocode_protected_paths as protected_paths
import autocode_protected_store as protected_store
import autocode_python_test_roles as role_policy
import autocode_regression as regression
import autocode_test_role_inventory as role_inventory
import autocode_util as util
import autocode_verify as verify


class PythonPathRoleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="python-test-roles-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        self.root.mkdir()
        (self.root / "pkg").mkdir()
        (self.root / "tests").mkdir()
        (self.root / "pkg/__init__.py").write_text("")
        (self.root / "pkg/billing_tests.py").write_text("def charge():\n    return 1\n")
        (self.root / "pkg/service.py").write_text("from .billing_tests import charge\n")
        (self.root / "tests/test_service.py").write_text(
            "import unittest\nfrom pkg.service import charge\n"
            "class ServiceTests(unittest.TestCase):\n"
            "    def test_charge_is_an_integer(self):\n        self.assertIsInstance(charge(), int)\n"
        )
        (self.root / "pyproject.toml").write_text('[tool.setuptools]\npackages = ["pkg"]\n')
        self.git("init", "-q")
        self.git("config", "maintenance.auto", "false")
        self.git("config", "gc.auto", "0")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "original")
        self.base = self.git("rev-parse", "HEAD")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.root), *args], text=True).strip()

    def test_shipped_suffix_module_is_not_an_original_test_oracle(self):
        inventory = protected.inventory(self.root, verify.is_test_path)
        self.assertNotIn("pkg/billing_tests.py", inventory)
        self.assertIn("tests/test_service.py", inventory)

    def test_measurement_consumes_the_proven_source_inventory_instead_of_filename_guessing(self):
        changes = {"pkg/billing_tests.py": "modified", "tests/test_service.py": "modified"}
        self.assertEqual(
            [("pkg/billing_tests.py", "modified")],
            verify.product_files(changes, source_files=["pkg/billing_tests.py"]),
        )

    def test_product_diff_statistics_keep_a_changed_shipped_suffix_module(self):
        (self.root / "pkg/billing_tests.py").write_text("def charge():\n    return 2\n")
        changes = verify.changed_files(self.root, self.base)
        stats = verify.diff_stats(self.root, self.base, changes)
        self.assertEqual(1, stats["source_files"])
        self.assertEqual(0, stats["test_files"])
        self.assertGreater(stats["source_lines_changed"], 0)

    def test_noncode_directory_statistics_do_not_grant_unknown_python_authority(self):
        (self.root / "unowned").mkdir()
        (self.root / "mystery_tests.py").write_text("VALUE=1\n")
        changes = {"unowned/": "added", "mystery_tests.py": "added"}
        roles = role_inventory.from_git(self.root, self.base, verify.is_test_path, changes=changes)
        before = roles.partition(changes)
        stats = verify.diff_stats(self.root, self.base, changes, roles=roles, allow_ambiguous=True)
        self.assertEqual(2, stats["files"])
        # #4095: the changed ambiguous mystery_tests.py is settled as both test and product (recorded), so it
        # counts as a source file and a test file; the non-code directory unowned/ grants no Python authority.
        self.assertEqual(2, stats["source_files"])
        self.assertEqual(1, stats["test_files"])
        self.assertEqual(["unowned/"], stats["non_code_files"])
        self.assertEqual(before, roles.partition(changes))
        self.assertEqual(role_policy.AMBIGUOUS, roles.role("mystery_tests.py"))
        self.assertIn("mystery_tests.py", roles.settlements)
        self.assertTrue(roles.is_product("mystery_tests.py"))
        # The settled change no longer refuses measurement: the decision is recorded, not asked of a person.
        settled_stats = verify.diff_stats(self.root, self.base, changes, roles=roles)
        self.assertEqual(stats["source_files"], settled_stats["source_files"])

    def legacy_binding(self):
        run_dir = self.root.parent / "run"
        files = protected_paths.collect(
            self.root, ["pkg/billing_tests.py", "tests/test_service.py"], util.snapshot(self.root)["files"]
        )
        record = protected.retain(self.root, run_dir, files, "python -m unittest discover -s tests -v")
        proof = {"verdict": "PASS", "changed_code_evidence": {"old": True}, "path": "old-evidence.json"}
        state = {
            "base_commit": self.base,
            "status": "PAUSED_INTERVENTION",
            "next_stage": "tester",
            "regression_proof": proof,
            "regression_proofs": [{"old": True}],
        }
        return run_dir, state, {"protected_tests": record}

    def reconcile(self, run_dir, state, settings, *, args=None, original_inputs=lambda: None):
        args = args or SimpleNamespace(run_dir=str(run_dir), resume_paused=True)
        return protected.reconcile(
            state,
            settings,
            args,
            self.root,
            run_dir,
            is_test_path=verify.is_test_path,
            discover_command=lambda: None,
            original_inputs=original_inputs,
        )

    def test_saved_binding_subtracts_only_original_product_and_keeps_original_test_bytes(self):
        run_dir, state, settings = self.legacy_binding()
        previous = copy.deepcopy(settings["protected_tests"])
        old_proof = copy.deepcopy(state["regression_proof"])
        (self.root / "pkg/billing_tests.py").write_text("def charge(): return 2\n")
        (self.root / "tests/test_service.py").write_text("# candidate changed the oracle\n")
        (self.root / "pyproject.toml").write_text("[tool.setuptools]\npackages=[]\n")
        self.reconcile(run_dir, state, settings)
        current = settings["protected_tests"]
        self.assertEqual({"tests/test_service.py": previous["files"]["tests/test_service.py"]}, current["files"])
        self.assertEqual(previous["command"], current["command"])
        with protected_store.opened(current, self.root) as original:
            self.assertIn("test_charge_is_an_integer", (original / "tests/test_service.py").read_text())
        event = state["user_events"][-1]
        self.assertEqual(previous, event["previous"])
        self.assertEqual(old_proof, event["previous_regression_proof"])
        self.assertNotIn("regression_proof", state)
        self.assertEqual([{"old": True}], state["regression_proofs"])
        protected.verify_binding(previous)
        context = protected.context(settings, ["tests/test_service.py"], state)
        self.assertEqual(event["reason"], context["role_correction"]["reason"])
        self.assertEqual(previous["binding_hash"], context["role_correction"]["previous"]["binding_hash"])
        self.assertIn("fresh proof is required", context["instruction"])
        before = copy.deepcopy(settings)
        self.reconcile(run_dir, state, settings)
        self.assertEqual(before, settings)
        self.assertEqual(1, len(state["user_events"]))

    def test_saved_correction_uses_dirty_launch_commit_instead_of_head_or_candidate_manifest(self):
        (self.root / "pyproject.toml").write_text("[tool.setuptools]\npackages=[]\n")
        (self.root / "pkg/service.py").write_text("# no runtime import\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "no ownership")
        head = self.git("rev-parse", "HEAD")
        (self.root / "pyproject.toml").write_text('[tool.setuptools]\npackages=["pkg"]\n')
        run_dir = self.root.parent / "run"
        self.base = regression.launch_base(self.root, run_dir)
        self.assertNotEqual(head, self.base)
        run_dir, state, settings = self.legacy_binding()
        (self.root / "pyproject.toml").write_text("[tool.setuptools]\npackages=[]\n")
        self.reconcile(run_dir, state, settings)
        self.assertNotIn("pkg/billing_tests.py", settings["protected_tests"]["files"])
        self.assertEqual(self.base, state["user_events"][-1]["base_commit"])

    def test_candidate_packaging_cannot_remove_an_ambiguous_original_entry(self):
        (self.root / "pyproject.toml").write_text("[tool.setuptools]\npackages=[]\n")
        (self.root / "pkg/service.py").write_text("# no runtime import\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "ambiguous")
        self.base = self.git("rev-parse", "HEAD")
        run_dir, state, settings = self.legacy_binding()
        previous = copy.deepcopy(settings)
        (self.root / "pyproject.toml").write_text('[tool.setuptools]\npackages=["pkg"]\n')
        self.reconcile(run_dir, state, settings)
        self.assertEqual(previous, settings)
        self.assertIn("regression_proof", state)

    def test_read_only_terminal_nonvalidation_and_nonresume_boundaries_do_not_migrate(self):
        for status, stage, options in (
            ("PAUSED_INTERVENTION", "tester", {"status": True}),
            ("PAUSED_INTERVENTION", "tester", {"explain": True}),
            ("PAUSED_INTERVENTION", "tester", {"dry_run": True}),
            ("TASK_COMPLETE", "tester", {}),
            ("PAUSED_INTERVENTION", "builder", {}),
            ("PAUSED_INTERVENTION", "tester", {"resume_paused": False}),
        ):
            with self.subTest(status=status, stage=stage, options=options):
                run_dir, state, settings = self.legacy_binding()
                state.update(status=status, next_stage=stage)
                previous = copy.deepcopy(settings)
                args = SimpleNamespace(**{"run_dir": str(run_dir), "resume_paused": True, **options})
                self.reconcile(run_dir, state, settings, args=args)
                self.assertEqual(previous, settings)
                self.assertIn("regression_proof", state)

    def test_applied_terminal_stop_keeps_old_binding_and_proof_unchanged(self):
        run_dir, state, settings = self.legacy_binding()
        state["applied_interventions"] = [{"kind": "stop", "id": "terminal"}]
        previous_state, previous_settings = copy.deepcopy(state), copy.deepcopy(settings)
        self.reconcile(run_dir, state, settings)
        self.assertEqual(previous_state, state)
        self.assertEqual(previous_settings, settings)

    def test_active_uncertain_or_pending_repair_prevents_saved_correction(self):
        for key in (
            "active_stage",
            "active_runner_check",
            "runner_check",
            "uncertain_artifacts",
            "pending_report_repair",
        ):
            with self.subTest(key=key):
                run_dir, state, settings = self.legacy_binding()
                state[key] = {"pending": True}
                previous = copy.deepcopy(settings)
                self.reconcile(run_dir, state, settings)
                self.assertEqual(previous, settings)
                self.assertIn("regression_proof", state)

    def test_original_product_bytes_must_match_archive_before_subtraction(self):
        (self.root / "pkg/billing_tests.py").write_text("def charge(): return 999\n")
        run_dir, state, settings = self.legacy_binding()
        previous_state, previous_settings = copy.deepcopy(state), copy.deepcopy(settings)
        with self.assertRaisesRegex(ValueError, "does not match the protected archive"):
            self.reconcile(run_dir, state, settings)
        self.assertEqual(previous_state, state)
        self.assertEqual(previous_settings, settings)

    def test_ignored_launch_overlay_refuses_before_any_binding_or_proof_mutation(self):
        run_dir, state, settings = self.legacy_binding()
        original_state, original_settings = copy.deepcopy(state), copy.deepcopy(settings)
        inputs = SimpleNamespace(unverified=[], generated={"pyproject.toml": ["hash", 420]}, vendored={})
        with self.assertRaisesRegex(ValueError, "ignored launch-source"):
            self.reconcile(run_dir, state, settings, original_inputs=lambda: inputs)
        self.assertEqual(original_state, state)
        self.assertEqual(original_settings, settings)

    def test_ambiguous_changed_file_is_settled_as_both_test_and_product(self):
        # #4095 (supersedes the old "unverified before any scratch tree or command" stop): a changed Python
        # file whose product/test ownership is ambiguous is settled deterministically as both test and
        # product, recorded, instead of stopping the run before the Tester to ask a person about machinery.
        (self.root / "pkg/billing_tests.py").write_text(PinnedRolePolicyTests.CASE)
        changes = {"pkg/billing_tests.py": "modified"}
        roles = role_inventory.from_git(self.root, self.base, verify.is_test_path, changes=changes)
        self.assertEqual(role_policy.AMBIGUOUS, roles.role("pkg/billing_tests.py"))
        self.assertIn("pkg/billing_tests.py", roles.settlements)
        self.assertTrue(roles.is_test("pkg/billing_tests.py"))
        self.assertTrue(roles.is_product("pkg/billing_tests.py"), "no escape from product changed-line evidence")
        tests, sources, ambiguous = roles.partition(changes)
        self.assertEqual(["pkg/billing_tests.py"], tests)
        self.assertEqual(["pkg/billing_tests.py"], sources)
        self.assertEqual([], ambiguous)

    def test_ambiguous_test_reached_only_by_a_loose_script_is_proven_not_paused(self):
        # #4095 confirmed-live case: a declared test module under tests/ that a loose archive script
        # bare-imports (a test-named module outside any test tree enables the reach walk) is classified
        # ambiguous. Before the fix the proof refused it ("ownership is ambiguous") before any command and
        # the run paused to ask a person about machinery; now it is settled as both test and product,
        # recorded in the proof's evidence, and proven through the Tester.
        temporary = tempfile.TemporaryDirectory(prefix="ambiguous-reach-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "project"
        for directory in ("pkg", "tests", "audits", "tools"):
            (root / directory).mkdir(parents=True)
        (root / "pkg/__init__.py").write_text("")
        (root / "pkg/service.py").write_text("def charge():\n    return 2\n")
        (root / "tests/test_opencode.py").write_text(
            "import unittest\nfrom pkg.service import charge\n"
            "class T(unittest.TestCase):\n    def test_charge(self):\n        self.assertEqual(charge(), 1)\n"
        )
        (root / "audits/probes.py").write_text("from test_opencode import T\n")
        (root / "tools/test_kilo_fake.py").write_text("VALUE=1\n")
        (root / "pyproject.toml").write_text('[tool.setuptools]\npackages = ["pkg"]\n')

        def git(*args):
            return subprocess.check_output(["git", "-C", str(root), *args], text=True).strip()

        git("init", "-q")
        git("config", "maintenance.auto", "false")
        git("config", "gc.auto", "0")
        git("add", ".")
        git("-c", "user.name=F", "-c", "user.email=f@e.test", "commit", "-qm", "original")
        base = git("rev-parse", "HEAD")
        # The approved plan fixes the product and rewrites the ambiguous test.
        (root / "pkg/service.py").write_text("def charge():\n    return 1\n")
        (root / "tests/test_opencode.py").write_text(
            "import unittest\nfrom pkg.service import charge\n"
            "class T(unittest.TestCase):\n    def test_charge(self):\n        # rewritten\n        self.assertEqual(charge(), 1)\n"
        )
        changes = verify.changed_files(root, base)
        roles = role_inventory.from_git(root, base, verify.is_test_path, changes=changes)
        self.assertEqual(role_policy.AMBIGUOUS, roles.role("tests/test_opencode.py"))
        self.assertIn("tests/test_opencode.py", roles.settlements)
        result = verify.verify(
            root,
            base,
            root.parent / "proof",
            framework=verify.Framework(
                "unittest", f"{sys.executable} -m unittest discover -s tests -v", python=sys.executable
            ),
        )
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["tests.test_opencode.T.test_charge"], result["fail_to_pass"])
        self.assertFalse(any("ambiguous" in reason for reason in result.get("unverified") or []))
        self.assertTrue(any("Settled ambiguous" in note for note in result.get("notes") or []))

    def test_changed_product_obligation_includes_shipped_suffix_and_refuses_ambiguous_escape(self):
        state = {"workspace": str(self.root), "base_commit": self.base}
        (self.root / "pkg/billing_tests.py").write_text("def charge(): return 2\n")
        self.assertTrue(changed_code_trigger.owes(state))
        (self.root / "pkg/billing_tests.py").write_text(PinnedRolePolicyTests.CASE)
        self.assertTrue(changed_code_trigger.owes(state))

    def test_original_tree_reader_uses_constant_git_processes_for_many_modules(self):
        for index in range(40):
            (self.root / "pkg" / f"helper_{index}.py").write_text("VALUE=1\n")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "many modules")
        with mock.patch("subprocess.Popen", wraps=subprocess.Popen) as processes:
            roles = role_inventory.from_git(self.root, "HEAD", verify.is_test_path)
        self.assertFalse(roles.protected("pkg/billing_tests.py"))
        git_commands = [call.args[0] for call in processes.call_args_list]
        self.assertEqual(2, len(git_commands))
        self.assertIn("--batch", git_commands[-1])

    def test_selector_snapshot_detects_same_byte_alias_replacement_and_deletion(self):
        (self.root / "shared").mkdir()
        (self.root / "shared/oracle.py").write_text(PinnedRolePolicyTests.CASE)
        alias = self.root / "test_alias.py"
        alias.symlink_to("shared/oracle.py")
        self.git("add", ".")
        self.git("-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "original link")
        base = self.git("rev-parse", "HEAD")
        changes = {"test_alias.py": "modified"}
        roles = role_inventory.from_git(self.root, base, verify.is_test_path, changes=changes)
        self.assertTrue(role_inventory.matches(roles, self.root, changes))
        alias.unlink()
        alias.write_text("shared/oracle.py")
        self.assertFalse(role_inventory.matches(roles, self.root, changes))
        alias.unlink()
        self.assertFalse(role_inventory.matches(roles, self.root, {"test_alias.py": "deleted"}))


class PinnedRolePolicyTests(unittest.TestCase):
    CASE = (
        "import unittest\nclass Checks(unittest.TestCase):\n    def test_value(self):\n        self.assertTrue(True)\n"
    )
    ASSERTION = "import unittest\nassert 2 + 2 == 4\n"

    def roles(self, sources, *, candidates=None, links=None, candidate_links=None):
        return role_policy.build(
            sources,
            sources.__getitem__,
            verify.is_test_path,
            candidates=candidates,
            links=links,
            candidate_links=candidate_links,
        )

    def test_declared_runtime_ownership_does_not_depend_on_module_name(self):
        for name in ("billing_tests", "ledger_tests", "anything_test"):
            with self.subTest(name=name):
                path = "pkg/" + name + ".py"
                result = self.roles(
                    {"pyproject.toml": '[tool.setuptools]\npackages=["pkg"]\n', path: "def value(): return 1\n"}
                )
                self.assertEqual(role_policy.PRODUCT, result.role(path))
                self.assertFalse(result.protected(path))

    def test_literal_package_dir_and_py_modules_are_supported(self):
        for manifest, path in (
            (
                '[tool.setuptools]\npackages=["api.inner"]\npackage-dir={api="src/runtime"}\n',
                "src/runtime/inner/value_tests.py",
            ),
            ('[tool.setuptools]\npy-modules=["billing_tests"]\npackage-dir={""="src"}\n', "src/billing_tests.py"),
        ):
            with self.subTest(path=path):
                self.assertEqual(
                    role_policy.PRODUCT, self.roles({"pyproject.toml": manifest, path: "VALUE=1\n"}).role(path)
                )

    def test_unsupported_backend_or_executable_setup_does_not_prove_packaging_ownership(self):
        for extra in (
            {"pyproject.toml": '[build-system]\nbuild-backend="other.backend"\n[tool.setuptools]\npackages=["pkg"]\n'},
            {"setup.py": "# executable configuration\n"},
            {"setup.cfg": "[options]\npackages=find:\n"},
        ):
            with self.subTest(extra=extra):
                sources = {
                    "pyproject.toml": '[tool.setuptools]\npackages=["pkg"]\n',
                    "pkg/value_tests.py": "VALUE=1\n",
                    **extra,
                }
                roles = self.roles(sources)
                self.assertTrue(roles.protected("pkg/value_tests.py"))
                self.assertEqual(role_policy.AMBIGUOUS, roles.role("pkg/value_tests.py"))

    def test_custom_unittest_names_and_dedicated_test_helpers_remain_protected(self):
        result = self.roles({"checks_tests.py": self.CASE, "tests/helper_tests.py": "VALUE=1\n"})
        for path in result.roles:
            self.assertEqual(role_policy.TEST, result.role(path))
            self.assertTrue(result.protected(path))

    def test_original_root_assertion_oracles_keep_their_identity_when_assertions_are_removed(self):
        result = self.roles({"checks_tests.py": self.ASSERTION}, candidates={"checks_tests.py": "import unittest\n"})
        self.assertEqual(role_policy.TEST, result.role("checks_tests.py"))
        self.assertTrue(result.protected("checks_tests.py"))
        for source in ("assert 2 + 2 == 4\n", "import unittest\nVALUE=1\n"):
            with self.subTest(source=source):
                self.assertEqual(role_policy.AMBIGUOUS, self.roles({"checks_tests.py": source}).role("checks_tests.py"))

    def test_assertion_oracles_with_original_runtime_ownership_remain_ambiguous(self):
        for sources in (
            {"pyproject.toml": '[tool.setuptools]\npy-modules=["checks_tests"]\n'},
            {"service.py": "import checks_tests\n"},
        ):
            with self.subTest(sources=sources):
                result = self.roles({**sources, "checks_tests.py": self.ASSERTION})
                self.assertTrue(result.protected("checks_tests.py"))
                with self.assertRaisesRegex(ValueError, "ambiguous"):
                    result.is_test("checks_tests.py")

    def test_candidate_metadata_cannot_relabel_original_runtime_as_tests(self):
        path = "pkg/billing_tests.py"
        original = {"pyproject.toml": '[tool.setuptools]\npackages=["pkg"]\n', path: "VALUE=1\n"}
        result = self.roles(
            original, candidates={"pyproject.toml": "[tool.setuptools]\npackages=[]\n", path: "VALUE=2\n"}
        )
        self.assertEqual(role_policy.PRODUCT, result.role(path))

    def test_mixed_runtime_and_test_ownership_refuses_changed_authority(self):
        path = "pkg/billing_tests.py"
        result = self.roles({"pyproject.toml": '[tool.setuptools]\npackages=["pkg"]\n', path: self.CASE})
        self.assertTrue(result.protected(path))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            result.is_test(path)

    def test_unknown_unchanged_entries_stay_protected_without_poisoning_other_roles(self):
        result = self.roles({"mystery_tests.py": "VALUE=1\n", "tests/test_ok.py": self.CASE, "product.py": "VALUE=1\n"})
        self.assertTrue(result.protected("mystery_tests.py"))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            result.is_test("mystery_tests.py")
        self.assertTrue(result.is_test("tests/test_ok.py"))
        self.assertFalse(result.is_test("product.py"))

    def test_pinned_literal_runtime_imports_establish_product_without_build_metadata(self):
        result = self.roles(
            {"pkg/service.py": "from .billing_tests import value\n", "pkg/billing_tests.py": "def value(): return 1\n"}
        )
        self.assertEqual(role_policy.PRODUCT, result.role("pkg/billing_tests.py"))

    def test_a_tests_module_beside_the_code_is_a_test_unless_the_package_imports_it(self):
        code = {"shop/__init__.py": "", "shop/pager.py": "def pages():\n    return 1\n"}
        result = self.roles({**code, "shop/tests.py": self.CASE, "test.py": self.CASE, "shop/admin/test.py": self.CASE})
        self.assertEqual(role_policy.TEST, result.role("shop/tests.py"))
        self.assertEqual(role_policy.TEST, result.role("test.py"))
        self.assertTrue(result.protected("shop/tests.py"))
        self.assertEqual(role_policy.PRODUCT, result.role("shop/admin/test.py"))
        # jinja2/tests.py holds the template language's tests as functions named test_odd, and the package
        # imports it: product code, whatever it declares.
        rules = "def test_odd(value):\n    return value % 2 == 1\n\n\nTESTS = {'odd': test_odd}\n"
        for source in (rules, self.CASE, "VALUE = 1\n"):
            result = self.roles({**code, "shop/defaults.py": "from .tests import *\n", "shop/tests.py": source})
            self.assertEqual(role_policy.PRODUCT, result.role("shop/tests.py"), source)
            self.assertFalse(result.protected("shop/tests.py"))
        # Any other test name the package imports keeps its refusal, and so does a tests.py the packaging ships.
        result = self.roles({**code, "shop/defaults.py": "from .rule_tests import *\n", "shop/rule_tests.py": rules})
        self.assertEqual(role_policy.AMBIGUOUS, result.role("shop/rule_tests.py"))
        packaged = {"pyproject.toml": '[tool.setuptools]\npackages=["shop"]\n', **code}
        result = self.roles({**packaged, "shop/defaults.py": "from .tests import *\n", "shop/tests.py": rules})
        self.assertEqual(role_policy.AMBIGUOUS, result.role("shop/tests.py"))
        # One that declares nothing and that nothing imports is unknown, as any file of a test name is.
        self.assertEqual(
            role_policy.AMBIGUOUS, self.roles({**code, "shop/tests.py": "VALUE = 1\n"}).role("shop/tests.py")
        )
        # A change cannot make the application's tests product by importing them.
        result = self.roles({**code, "shop/tests.py": self.CASE}, candidates={"shop/pager.py": "from . import tests\n"})
        self.assertEqual(role_policy.TEST, result.role("shop/tests.py"))
        self.assertTrue(result.protected("shop/tests.py"))

    def test_a_project_below_the_root_keeps_its_tests_at_its_own_top(self):
        web = {"web/package.json": "{}\n", "web/index.js": "module.exports = 1;\n"}
        result = self.roles({**web, "web/test/pager.js": "// tests\n", "web/lib/test/run.js": "// a command\n"})
        self.assertEqual(role_policy.TEST, result.role("web/test/pager.js"))
        self.assertTrue(result.protected("web/test/pager.js"))
        self.assertTrue(result.test_path("web/test/pager.js"))
        self.assertEqual(role_policy.PRODUCT, result.role("web/lib/test/run.js"))
        self.assertFalse(result.test_path("web/lib/test/run.js"))
        # A manifest the change adds names no project: code that was product stays product, and a file the
        # change adds beside it is product too.
        result = self.roles(
            {"lib/index.js": "module.exports = 1;\n", "lib/test/run.js": "// a command\n"},
            candidates={"lib/package.json": "{}\n", "lib/test/run.js": "// tests\n", "lib/test/new.js": "// tests\n"},
        )
        self.assertEqual(role_policy.PRODUCT, result.role("lib/test/run.js"))
        self.assertEqual(role_policy.PRODUCT, result.role("lib/test/new.js"))
        # A Python project below the root is read as before, from the packages around it.
        docs = {
            "docs/pyproject.toml": "[project]\nname='docs'\n",
            "docs/test/build.py": "VALUE = 1\n",
            "docs/test/expected/index.html": "<p>docs</p>\n",
        }
        result = self.roles(docs)
        self.assertEqual(role_policy.PRODUCT, result.role("docs/test/build.py"))
        self.assertEqual(role_policy.PRODUCT, result.role("docs/test/expected/index.html"))
        self.assertFalse(result.test_path("docs/test/expected/index.html"))

    def test_candidate_test_declarations_do_not_convert_original_product_or_unknown_files(self):
        for original in (
            {"pyproject.toml": '[tool.setuptools]\npackages=["pkg"]\n', "pkg/billing_tests.py": "VALUE=1\n"},
            {"pkg/billing_tests.py": "VALUE=1\n"},
        ):
            for declaration in (self.CASE, self.ASSERTION):
                with self.subTest(packaged="pyproject.toml" in original, declaration=declaration):
                    result = self.roles(original, candidates={"pkg/billing_tests.py": declaration})
                    self.assertEqual(role_policy.AMBIGUOUS, result.role("pkg/billing_tests.py"))

    def test_code_replacing_a_link_out_of_the_tree_takes_its_own_role(self):
        # Live run 2026-11-17: the person replaced a link to a sibling directory with the file itself, and every
        # resume stopped on "ownership is ambiguous", although the tree had never held that path's bytes.
        for target in ("/elsewhere/shared/discount.py", "../../../shared/discount.py"):
            sources = {
                "src/pricing/__init__.py": "",
                "src/pricing/discount.py": target,
                "src/pricing/cart.py": "from .discount import clamp\n",
                "tests/test_cart.py": self.CASE,
                "tests/test_shared.py": target,
            }
            links = {"src/pricing/discount.py": target, "tests/test_shared.py": target}
            with self.subTest(target=target):
                kept = self.roles(sources, links=links)
                self.assertEqual(role_policy.AMBIGUOUS, kept.role("src/pricing/discount.py"))
                still_a_link = self.roles(
                    sources,
                    links=links,
                    candidates={"src/pricing/discount.py": target},
                    candidate_links={"src/pricing/discount.py": target},
                )
                self.assertEqual(role_policy.AMBIGUOUS, still_a_link.role("src/pricing/discount.py"))
                replaced = self.roles(
                    sources,
                    links=links,
                    candidates={
                        "src/pricing/discount.py": "def clamp(v):\n    return v\n",
                        "tests/test_shared.py": self.CASE,
                    },
                    candidate_links={"src/pricing/discount.py": None, "tests/test_shared.py": None},
                )
                self.assertEqual(role_policy.PRODUCT, replaced.role("src/pricing/discount.py"))
                self.assertFalse(replaced.is_test("src/pricing/discount.py"))
                self.assertEqual(role_policy.TEST, replaced.role("tests/test_shared.py"))
                disguised = self.roles(
                    sources,
                    links=links,
                    candidates={"src/pricing/discount.py": self.CASE},
                    candidate_links={"src/pricing/discount.py": None},
                )
                self.assertFalse(
                    disguised.is_test("src/pricing/discount.py"),
                    "code outside a test path does not become a test by declaring one",
                )

    def test_deleted_test_declarations_keep_original_oracle_identity(self):
        result = self.roles({"checks_tests.py": self.CASE}, candidates={"checks_tests.py": "# removed tests\n"})
        self.assertEqual(role_policy.TEST, result.role("checks_tests.py"))

    def test_a_test_named_link_cannot_pull_runtime_bytes_into_the_oracle_set(self):
        sources = {
            "pyproject.toml": '[tool.setuptools]\npackages=["pkg"]\n',
            "pkg/billing_tests.py": "def charge(): return 1\n",
            "tests/test_alias.py": "../pkg/billing_tests.py",
        }
        result = self.roles(sources, links={"tests/test_alias.py": sources["tests/test_alias.py"]})
        self.assertEqual(role_policy.PRODUCT, result.role("tests/test_alias.py"))
        self.assertEqual(role_policy.PRODUCT, result.role("pkg/billing_tests.py"))

    def test_an_original_test_link_keeps_its_complete_test_closure(self):
        sources = {"tests/original.py": self.CASE, "tests/test_alias.py": "original.py"}
        result = self.roles(sources, links={"tests/test_alias.py": "original.py"})
        self.assertTrue(all(result.protected(path) for path in sources))
        self.assertTrue(all(result.is_test(path) for path in sources))

    def test_original_test_link_keeps_declared_oracles_outside_test_roots(self):
        sources = {"shared/oracle.py": self.CASE, "test_alias.py": "shared/oracle.py"}
        result = self.roles(
            sources,
            links={"test_alias.py": "shared/oracle.py"},
            candidates={"shared/oracle.py": self.CASE + "# added coverage\n"},
        )
        for path in sources:
            self.assertTrue(result.is_test(path))
            self.assertTrue(result.protected(path))

    def test_changed_original_link_endpoint_selects_the_original_test_module_identity(self):
        sources = {"shared/oracle.py": self.CASE, "test_alias.py": "shared/oracle.py"}
        roles = self.roles(sources, links={"test_alias.py": "shared/oracle.py"})
        framework = verify.Framework("unittest", "python -m unittest discover -v", python="python")
        framework.test_roles = roles
        commands = verify.select_commands(framework, ["shared/oracle.py"])
        self.assertEqual("python -m unittest -v test_alias.py", commands["regression"])

    def test_changed_or_missing_link_chain_cannot_select_another_oracle(self):
        sources = {
            "shared/oracle.py": self.CASE,
            "shared/other.py": self.CASE,
            "shared/bridge.py": "oracle.py",
            "test_alias.py": "shared/bridge.py",
        }
        links = {"shared/bridge.py": "oracle.py", "test_alias.py": "shared/bridge.py"}
        original = self.roles(sources, links=links)
        self.assertEqual(["shared/oracle.py", "test_alias.py"], original.selectors(["shared/oracle.py"]))
        for path, source, target in (
            ("test_alias.py", None, None),
            ("test_alias.py", self.CASE, None),
            ("test_alias.py", "shared/other.py", "shared/other.py"),
            ("shared/bridge.py", "other.py", "other.py"),
            ("shared/bridge.py", None, None),
            ("shared/bridge.py", self.CASE, None),
            ("test_alias.py", "../outside.py", "../outside.py"),
            ("shared/oracle.py", None, None),
        ):
            with self.subTest(path=path, source=source):
                result = self.roles(sources, links=links, candidates={path: source}, candidate_links={path: target})
                self.assertEqual(original.original_test_links, result.original_test_links)
                self.assertEqual(["shared/oracle.py"], result.selectors(["shared/oracle.py"]))

    def test_original_link_cannot_promote_packaged_reached_or_uncertain_oracles(self):
        for extra, extra_links in (
            ({"pyproject.toml": '[tool.setuptools]\npackages=["shared"]\n'}, {}),
            ({"service.py": "from shared import oracle\n"}, {}),
            ({"service.py": "import importlib\nimportlib.import_module(selected)\n"}, {}),
            (
                {"runtime_alias.py": "shared/oracle.py", "service.py": "import runtime_alias\n"},
                {"runtime_alias.py": "shared/oracle.py"},
            ),
        ):
            with self.subTest(extra=extra):
                sources = {"shared/oracle.py": self.CASE, "test_alias.py": "shared/oracle.py", **extra}
                result = self.roles(sources, links={"test_alias.py": "shared/oracle.py", **extra_links})
                for path in ("shared/oracle.py", "test_alias.py"):
                    with self.assertRaisesRegex(ValueError, "ambiguous"):
                        result.is_test(path)

    def test_candidate_added_link_cannot_promote_original_product_or_unknown_role(self):
        for path, source in (("shared/oracle.py", self.CASE), ("shared/mystery_tests.py", "VALUE=1\n")):
            with self.subTest(path=path):
                sources = {path: source}
                original = self.roles(sources)
                result = self.roles(
                    sources,
                    candidates={"test_alias.py": path},
                    candidate_links={"test_alias.py": path},
                )
                self.assertEqual(original.role(path), result.role(path))
                # #4095: the added link of unknown ownership is settled as both test and product (recorded),
                # never promoted to a pure test that escapes product evidence; the original role is frozen.
                self.assertEqual(role_policy.AMBIGUOUS, result.role("test_alias.py"))
                self.assertIn("test_alias.py", result.settlements)
                self.assertTrue(result.is_test("test_alias.py"))
                self.assertTrue(result.is_product("test_alias.py"))
                self.assertTrue(result.protected("test_alias.py"))

    def test_a_fixture_link_that_leads_to_no_file_has_the_role_of_its_place(self):
        # A change that adds `tests/data/linked/alias -> real` ended its proof UNVERIFIED: "Product/test
        # ownership is ambiguous; no proof commands were started for: tests/data/linked/alias".
        sources = {"scan.py": "VALUE = 1\n", "tests/test_scan.py": self.CASE, "tests/data/tree/a.txt": "a\n"}
        for link, target in (
            ("tests/data/current", "tree"),
            ("tests/data/gone", "nowhere"),
            ("tests/data/host", "/etc/hostname"),
            ("tests/data/up", "../../../outside"),
            ("tests/data/loop", "loop"),
        ):
            with self.subTest(link=link):
                added = self.roles(sources, candidates={link: target}, candidate_links={link: target})
                self.assertTrue(added.is_test(link))
                original = self.roles({**sources, link: target}, links={link: target})
                self.assertTrue(original.is_test(link))
                self.assertTrue(original.protected(link))
        added = self.roles(sources, candidates={"docs/latest": "v2"}, candidate_links={"docs/latest": "v2"})
        self.assertFalse(added.is_test("docs/latest"))
        # Code that leads to no file cannot be read, so it keeps no role.
        for link in ("tests/test_ghost.py", "ghost.py", "tests/helper.js"):
            with self.subTest(link=link):
                added = self.roles(sources, candidates={link: "missing"}, candidate_links={link: "missing"})
                with self.assertRaisesRegex(ValueError, "ambiguous"):
                    added.is_test(link)

    def test_mixed_runtime_test_link_ownership_keeps_both_entries_protected_but_refuses_changes(self):
        sources = {
            "pyproject.toml": '[tool.setuptools]\npackages=["pkg"]\n',
            "pkg/service.py": self.CASE,
            "tests/test_alias.py": "../pkg/service.py",
        }
        result = self.roles(sources, links={"tests/test_alias.py": "../pkg/service.py"})
        for path in ("pkg/service.py", "tests/test_alias.py"):
            self.assertTrue(result.protected(path))
            with self.assertRaisesRegex(ValueError, "ambiguous"):
                result.is_test(path)


class NestedProjectTestTreeTests(unittest.TestCase):
    """A real repository can keep its project one directory down (#2944: Docutils keeps its package in
    ``docutils/docutils/`` and its suite in ``docutils/test/``), beside old code Python 3 cannot parse and
    product code that imports modules by computed name, so reach is uncertain and only the test tree can
    show that the suite is the suite."""

    CASE = PinnedRolePolicyTests.CASE

    def roles(self, sources, candidates=None):
        return role_policy.build(sources, sources.__getitem__, verify.is_test_path, candidates=candidates)

    def project(self):
        return {
            "proj/pyproject.toml": '[project]\nname = "proj"\n',
            "proj/proj/__init__.py": "",
            "proj/proj/readers.py": "import importlib\ndef load(name):\n    return importlib.import_module(name)\n",
            "proj/test/__init__.py": "",
            "proj/test/support.py": "import proj\n",
            "proj/test/test_parsers/__init__.py": "",
            "proj/test/test_parsers/test_include.py": self.CASE,
            "sandbox/old/legacy.py": 'print "Python 2"\n',
        }

    def test_a_suite_in_test_beside_a_project_one_level_down_is_test(self):
        added = "proj/test/test_parsers/test_include_number_lines.py"
        roles = self.roles(self.project(), candidates={added: self.CASE})
        self.assertTrue(roles.is_test("proj/test/test_parsers/test_include.py"))
        self.assertTrue(roles.is_test(added))
        self.assertTrue(roles.protected("proj/test/test_parsers/test_include.py"))
        self.assertFalse(roles.is_test("proj/proj/readers.py"))

    def test_test_inside_a_package_stays_a_possible_product_subpackage(self):
        sources = {
            "pkg/__init__.py": "",
            "pkg/loader.py": "import importlib\ndef load(name):\n    return importlib.import_module(name)\n",
            "pkg/test/__init__.py": "",
            "pkg/test/test_helpers.py": self.CASE,
            "nested/pkg/__init__.py": "",
            "nested/pkg/test/test_helpers.py": self.CASE,
        }
        roles = self.roles(sources)
        for path in ("pkg/test/test_helpers.py", "nested/pkg/test/test_helpers.py"):
            with self.subTest(path=path), self.assertRaisesRegex(ValueError, "ambiguous"):
                roles.is_test(path)

    def test_a_nested_test_directory_the_packaging_ships_is_not_a_test(self):
        sources = self.project()
        sources["pyproject.toml"] = '[tool.setuptools]\npackages = ["proj.test", "proj.test.test_parsers"]\n'
        roles = self.roles(sources)
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            roles.is_test("proj/test/test_parsers/test_include.py")

    def test_a_candidate_that_makes_the_parent_a_package_cannot_keep_its_added_test(self):
        added = "proj/test/test_parsers/test_include_number_lines.py"
        roles = self.roles(self.project(), candidates={added: self.CASE, "proj/__init__.py": ""})
        self.assertTrue(roles.is_test("proj/test/test_parsers/test_include.py"), "original roles are frozen")
        # #4095: the added test in a package's test/ subpackage is ambiguous; settled as both test and
        # product (recorded), so it cannot escape product changed-line evidence.
        self.assertEqual(role_policy.AMBIGUOUS, roles.role(added))
        self.assertIn(added, roles.settlements)
        self.assertTrue(roles.is_test(added))
        self.assertTrue(roles.is_product(added))


class DeclaredTestPathTests(unittest.TestCase):
    """A project names where its tests are in the configuration its runner reads. pytest keeps its suite in
    ``testing/``, named in its ``pyproject.toml`` ``testpaths``, beside product code that imports modules by
    computed name; every test there was ambiguous and a live run stopped on ``testing/test_assertrewrite.py``
    (2026-11-18)."""

    CASE = PinnedRolePolicyTests.CASE
    LOADER = "import importlib\ndef load(name):\n    return importlib.import_module(name)\n"

    def roles(self, sources, candidates=None):
        return role_policy.build(sources, sources.__getitem__, verify.is_test_path, candidates=candidates)

    def project(self, config, directory="testing", prefix=""):
        return {
            **config,
            f"{prefix}src/pkg/__init__.py": "",
            f"{prefix}src/pkg/plugins.py": self.LOADER,
            f"{prefix}{directory}/conftest.py": "",
            f"{prefix}{directory}/helpers.py": "VALUE = 1\n",
            f"{prefix}{directory}/test_rewrite.py": self.CASE,
            f"{prefix}{directory}/io/test_wcwidth.py": self.CASE,
        }

    CONFIGS = (
        ("pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["testing"]\n'),
        ("pyproject.toml", '[tool.pytest]\ntestpaths = ["testing"]\n'),
        ("pytest.ini", "[pytest]\ntestpaths = testing\n"),
        ("pytest.toml", '[pytest]\ntestpaths = ["testing"]\n'),
        ("tox.ini", "[tox]\nenvlist = py\n[pytest]\ntestpaths =\n    doc\n    testing\n"),
        ("setup.cfg", "[metadata]\nname = pkg\n[tool:pytest]\ntestpaths = testing\n"),
    )

    def test_a_suite_in_a_directory_the_pytest_configuration_names_is_test(self):
        for name, config in self.CONFIGS:
            for directory in ("testing", "unit_tests", "qa/suite"):
                with self.subTest(config=name, directory=directory):
                    sources = self.project({name: config.replace("testing", directory)}, directory)
                    added = f"{directory}/test_added.py"
                    roles = self.roles(sources, candidates={added: self.CASE})
                    for path in ("test_rewrite.py", "io/test_wcwidth.py", "helpers.py", "conftest.py"):
                        self.assertTrue(roles.is_test(f"{directory}/{path}"), path)
                    self.assertTrue(roles.is_test(added))
                    self.assertTrue(roles.protected(f"{directory}/test_rewrite.py"))
                    self.assertFalse(roles.is_test("src/pkg/plugins.py"))
                    self.assertTrue(roles.test_path(f"{directory}/helpers.py"), "path-only readers get the same answer")

    def test_a_project_below_the_root_names_its_tests_relative_to_itself(self):
        sources = self.project(
            {"web/pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["testing"]\n'}, prefix="web/"
        )
        roles = self.roles(sources)
        self.assertTrue(roles.is_test("web/testing/test_rewrite.py"))
        self.assertFalse(roles.is_test("web/src/pkg/plugins.py"))

    def test_without_a_named_directory_a_test_there_stays_ambiguous(self):
        for name, config in (
            ("pyproject.toml", '[project]\nname = "pkg"\n'),
            ("pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["."]\n'),
            ("pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["test*"]\n'),
            ("pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["../testing"]\n'),
            ("pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["missing"]\n'),
            ("pyproject.toml", "[tool.pytest.ini_options\n"),
            ("tox.ini", "[testenv]\ncommands = pytest testing\n"),
            ("setup.cfg", "[pytest]\ntestpaths = testing\n"),
        ):
            with self.subTest(config=config):
                roles = self.roles(self.project({name: config}))
                with self.assertRaisesRegex(ValueError, "ambiguous"):
                    roles.is_test("testing/test_rewrite.py")

    def test_the_first_configuration_pytest_reads_is_the_one_that_counts(self):
        sources = self.project(
            {
                "pytest.ini": "[pytest]\naddopts = -q\n",
                "pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["testing"]\n',
            }
        )
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            self.roles(sources).is_test("testing/test_rewrite.py")

    def test_a_named_directory_that_holds_product_code_stays_under_the_rule_by_name(self):
        # Doctests: ``testpaths = ["src", "testing"]`` names the product too.
        sources = self.project({"pyproject.toml": '[tool.pytest.ini_options]\ntestpaths = ["src", "testing"]\n'})
        sources["src/pkg/test_utils.py"] = "def helper():\n    return 1\n"
        sources["src/pkg/core.py"] = "from pkg import test_utils\n"
        sources["testing/test_core.py"] = "import pkg.core\n" + self.CASE
        roles = self.roles(sources)
        self.assertTrue(roles.is_test("testing/test_rewrite.py"))
        self.assertFalse(roles.is_test("src/pkg/core.py"))
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            roles.is_test("src/pkg/test_utils.py")  # As without the configuration: never a test by it.
        # A directory code outside it imports, or a package the packaging ships, is not a test tree.
        for extra in (
            {"src/pkg/core.py": "from testing import helpers\n"},
            {
                "pyproject.toml": '[tool.setuptools]\npackages = ["testing"]\n[tool.pytest.ini_options]\ntestpaths = ["testing"]\n'
            },
        ):
            with self.subTest(extra=extra):
                roles = self.roles({**sources, **extra})
                with self.assertRaisesRegex(ValueError, "ambiguous"):
                    roles.is_test("testing/test_rewrite.py")

    def test_a_change_cannot_name_new_test_paths(self):
        sources = self.project({"pyproject.toml": '[project]\nname = "pkg"\n'})
        config = '[project]\nname = "pkg"\n[tool.pytest.ini_options]\ntestpaths = ["testing"]\n'
        roles = self.roles(sources, candidates={"pyproject.toml": config, "testing/test_added.py": self.CASE})
        # The candidate's configuration cannot name the ORIGINAL tree's test paths: the unchanged
        # testing/test_rewrite.py stays ambiguous and protected (no retroactive test authority).
        with self.assertRaisesRegex(ValueError, "ambiguous"):
            roles.is_test("testing/test_rewrite.py")
        # #4095: the test the change itself adds is settled as both test and product (recorded), so its
        # declared tests run while its changed lines still need product changed-line evidence.
        self.assertEqual(role_policy.AMBIGUOUS, roles.role("testing/test_added.py"))
        self.assertIn("testing/test_added.py", roles.settlements)
        self.assertTrue(roles.is_test("testing/test_added.py"))
        self.assertTrue(roles.is_product("testing/test_added.py"))

    def test_the_configuration_read_is_recorded_with_the_roles(self):
        sources = self.project({"tox.ini": "[pytest]\ntestpaths = testing\n"})
        self.assertIn("tox.ini", self.roles(sources).metadata)


class RoleReadCostTests(unittest.TestCase):
    def test_unowned_dedicated_test_roots_need_no_source_or_ast_reads(self):
        sources = {f"tests/test_{index}.py": "# helper or test body\n" for index in range(500)}
        reader = mock.Mock(side_effect=sources.__getitem__)
        roles = role_policy.build(sources, reader, verify.is_test_path)
        self.assertTrue(all(roles.is_test(path) for path in sources))
        reader.assert_not_called()


class UndecodableSourceTests(unittest.TestCase):
    """A real repository can hold Python 2 era files Python 3 cannot read (#2944: Docutils' sandbox has
    Latin-1 bytes with no coding line). They are unparseable sources, never a crash of the whole run."""

    def test_an_unreadable_product_file_makes_reach_uncertain_instead_of_stopping_the_run(self):
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            (workspace / "pkg").mkdir()
            (workspace / "pkg/legacy.py").write_bytes(b"# caf\xe9 without a coding line\nimport os\n")
            (workspace / "pkg/cookie.py").write_bytes(b"# coding: no-such-codec\nimport os\n")
            (workspace / "pkg/latin.py").write_bytes(b"# -*- coding: latin-1 -*-\nNAME = 'caf\xe9'\n")
            (workspace / "pkg/test_core.py").write_text(
                "import unittest\n\nclass TestCore(unittest.TestCase):\n    def test_it(self):\n        pass\n"
            )
            snapshot = {name: "file" for name in ("pkg/legacy.py", "pkg/cookie.py", "pkg/latin.py", "pkg/test_core.py")}
            roles = role_inventory.from_workspace(workspace, verify.is_test_path, snapshot=snapshot)
        for path in ("pkg/legacy.py", "pkg/cookie.py", "pkg/latin.py"):
            self.assertEqual(role_policy.PRODUCT, roles.role(path))
        self.assertEqual(role_policy.AMBIGUOUS, roles.role("pkg/test_core.py"), "unknown imports prove nothing")

    def test_a_declared_encoding_is_still_honoured_and_unreadable_bytes_are_kept(self):
        latin = b"# coding: latin-1\nNAME = 'caf\xe9'\n"
        self.assertEqual(latin.decode("latin-1"), role_inventory._text("a.py", latin))
        self.assertEqual("x = 1  # caf\udce9\n", role_inventory._text("b.py", b"x = 1  # caf\xe9\n"))
        # A file of another name is read as UTF-8, and keeps what UTF-8 cannot decode the same way.
        self.assertEqual("café", role_inventory._text("notes.txt", "café".encode()))
        self.assertEqual("caf\udce9", role_inventory._text("notes.txt", b"caf\xe9"))

    def test_a_link_to_a_file_that_is_not_text_takes_its_place_instead_of_stopping_the_run(self):
        """Django links its documentation theme's PNG icons (docs/_theme/djangodocs-epub/static/*.png): a
        run there stopped at setup with "'utf-8' codec can't decode byte 0x89 in position 0". A link is
        read to see whether its endpoint declares tests; bytes that are not text declare none."""
        png = b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR"
        with tempfile.TemporaryDirectory() as root:
            workspace = Path(root)
            for directory in ("docs/theme/static", "docs/epub/static", "tests"):
                (workspace / directory).mkdir(parents=True)
            (workspace / "docs/theme/static/note.png").write_bytes(png)
            (workspace / "docs/epub/static/note.png").symlink_to("../../theme/static/note.png")
            (workspace / "docs/notes.txt").write_bytes(b"caf\xe9 in Latin-1")
            (workspace / "docs/latest.txt").symlink_to("notes.txt")
            # A link named as a test is read too, before its endpoint's place is known.
            (workspace / "tests/test_icon.py").symlink_to("../docs/theme/static/note.png")
            (workspace / "tests/test_core.py").write_text(
                "import unittest\n\nclass TestCore(unittest.TestCase):\n    def test_it(self):\n        pass\n"
            )
            names = (
                "docs/theme/static/note.png",
                "docs/epub/static/note.png",
                "docs/notes.txt",
                "docs/latest.txt",
                "tests/test_icon.py",
                "tests/test_core.py",
            )
            roles = role_inventory.from_workspace(workspace, verify.is_test_path, snapshot=dict.fromkeys(names, "file"))
        for path in names[:4]:
            self.assertEqual(role_policy.PRODUCT, roles.role(path), path)
        self.assertNotEqual(role_policy.TEST, roles.role("tests/test_icon.py"), "a picture is no test oracle")
        self.assertEqual(role_policy.TEST, roles.role("tests/test_core.py"))


class RoleProofFreshnessTests(unittest.TestCase):
    def test_old_policy_cannot_complete_even_when_all_other_proof_evidence_is_accepted(self):
        result = {"verdict": "PASS", "checks": {"suite": {}}, "case_tests": {}, "case_scope": []}
        proof = {"verdict": "PASS", "source_revision": "current", "path": "proof.json", "receipt_sha256": "pin"}
        state = {"regression_proof": proof}
        with (
            mock.patch.object(regression, "required", return_value=True),
            mock.patch.object(regression.test_cases, "proof_cases", return_value=[]),
            mock.patch.object(regression.util, "read", return_value=result),
            mock.patch.object(regression.util, "file_hash", return_value="pin"),
            mock.patch.object(regression.schedule, "proof_intact", return_value=True),
            mock.patch.object(regression.changed_code, "ready", return_value=True),
        ):
            self.assertFalse(regression.complete(state, "current"))
            for previous_policy in (1, 2, 3, 4, 5, 6):
                proof["execution_context"] = {"test_role_policy": previous_policy}
                self.assertFalse(regression.complete(state, "current"))
            proof["execution_context"] = {"test_role_policy": role_policy.VERSION}
            self.assertTrue(regression.complete(state, "current"))
            state["settings"] = {"protected_tests": {"binding_hash": "new"}}
            self.assertFalse(regression.complete(state, "current"))
            proof["execution_context"]["protected_test_binding"] = "new"
            self.assertTrue(regression.complete(state, "current"))


class CorrectionProjectionTests(unittest.TestCase):
    def event(self, count=1596):
        files = {f"tests/test_{n}.py": {"sha256": "a" * 64, "size": n, "mode": 420} for n in range(count)}
        return {
            "kind": "protected_tests_roles_corrected",
            "actor": "core",
            "at": "2026-11-09T00:00:00Z",
            "reason": "Original runtime product entries are not test oracles; original tests and command are retained.",
            "base_commit": "b" * 40,
            "previous": {
                "binding_hash": "c" * 64,
                "root": "/owned/original.zip",
                "inventory_path": "/owned/original.json",
                "files": files,
                "command": "python -m unittest discover -v",
            },
            "current": {
                "binding_hash": "d" * 64,
                "root": "/owned/corrected.zip",
                "inventory_path": "/owned/corrected.json",
                "files": dict(list(files.items())[1:]),
                "command": "python -m unittest discover -v",
            },
            "previous_regression_proof": {
                "path": "/owned/old-proof.json",
                "receipt_sha256": "e" * 64,
                "source_revision": "f" * 64,
                "verdict": "PASS",
                "raw": "x" * 200_000,
            },
        }

    def test_model_packet_references_large_history_without_copying_or_mutating_inventories(self):
        import json

        import autocode_packet_events as packets

        event = self.event()
        original = copy.deepcopy(event)
        projected = packets.reference_repeats([event], None, None)
        self.assertLess(len(json.dumps(projected)), 2500)
        self.assertEqual(1596, projected[0]["previous"]["file_count"])
        self.assertEqual(1595, projected[0]["current"]["file_count"])
        self.assertEqual("/owned/original.json", projected[0]["previous"]["inventory"])
        self.assertEqual("/owned/old-proof.json", projected[0]["retired_proof"]["path"])
        self.assertNotIn("files", projected[0]["previous"])
        self.assertEqual(original, event)
        self.assertEqual(projected, packets.reference_repeats(projected, None, None))
        import autocode_plan_retention as retention

        final_packet = {"user_events": projected}
        retention.compact_final_packet(final_packet)
        self.assertEqual(projected, final_packet["user_events"])
        event["reason"] = "x" * 100_000
        event["previous"]["root"] = "/owned/" + "z" * 100_000
        oversized = packets.reference_repeats([event], None, None)
        self.assertLess(len(json.dumps(oversized)), 5000)
        self.assertIsNone(oversized[0]["previous"]["archive"])

    def test_public_recovery_and_final_report_show_the_bounded_correction_reason(self):
        import json

        import autocode_evidence_document as document
        import autocode_recovery_history as recovery
        import autocode_run_view as run_view

        from .test_evidence_report import public_view, value

        event = self.event()
        state = {
            "settings": {"protected_tests": event["current"]},
            "user_events": [event],
            "regression_proof": {"verdict": "PASS", "source_revision": "current"},
        }
        history = recovery.project(state)
        self.assertEqual("protected_tests_roles_corrected", history[0]["kind"])
        self.assertIn("original tests and command", history[0]["detail"])
        evidence = run_view.evidence(state)
        self.assertEqual(1596, evidence["regression_proof"]["role_correction"]["previous"]["file_count"])
        shown = public_view()
        shown["evidence"]["regression_proof"] = evidence["regression_proof"]
        report = value(shown)
        markdown = document.render(report)
        self.assertIn("Original test inventory correction", markdown)
        self.assertIn(event["reason"], markdown)
        self.assertIn("/owned/original.json", markdown)
        self.assertLess(len(json.dumps(report["regression_proof"]["role_correction"])), 2500)


class TypeCheckingImportTests(unittest.TestCase):
    CASE = PinnedRolePolicyTests.CASE

    def test_type_checking_imports_are_excluded_from_runtime_imports_and_reach(self):
        cases = (
            (
                "pkg/mod.py",
                "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import foo\n",
                {"typing", "typing.TYPE_CHECKING"},
            ),
            (
                "pkg/mod.py",
                "from typing import TYPE_CHECKING as TC\nif TC:\n    import foo\n",
                {"typing", "typing.TYPE_CHECKING"},
            ),
            (
                "pkg/mod.py",
                "from typing_extensions import TYPE_CHECKING\nif TYPE_CHECKING:\n    import foo\n",
                {"typing_extensions", "typing_extensions.TYPE_CHECKING"},
            ),
            (
                "pkg/mod.py",
                "from typing_extensions import TYPE_CHECKING as TC\nif TC:\n    import foo\n",
                {"typing_extensions", "typing_extensions.TYPE_CHECKING"},
            ),
            ("pkg/mod.py", "import typing\nif typing.TYPE_CHECKING:\n    from . import bar\n", {"typing"}),
            ("pkg/mod.py", "import typing as t\nif t.TYPE_CHECKING:\n    from . import bar\n", {"typing"}),
            (
                "pkg/mod.py",
                "import typing_extensions\nif typing_extensions.TYPE_CHECKING:\n    from . import bar\n",
                {"typing_extensions"},
            ),
            (
                "pkg/mod.py",
                "import typing_extensions as te\nif te.TYPE_CHECKING:\n    from . import bar\n",
                {"typing_extensions"},
            ),
            (
                "pkg/mod.py",
                "from typing import TYPE_CHECKING\nif TYPE_CHECKING and flag:\n    import foo\n",
                {"typing", "typing.TYPE_CHECKING"},
            ),
            (
                "pkg/mod.py",
                "import typing, typing_extensions\nif typing.TYPE_CHECKING or typing_extensions.TYPE_CHECKING:\n    import foo\n",
                {"typing", "typing_extensions"},
            ),
        )
        for path, source, expected in cases:
            with self.subTest(source=source):
                modules, uncertain = role_policy._imports(path, source)
                self.assertEqual(expected, modules)
                self.assertFalse(uncertain)

        roles = role_policy.build(
            {
                "pkg/__init__.py": "",
                "pkg/service.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from . import helper_tests\n",
                "pkg/helper_tests.py": self.CASE,
            },
            lambda p: {
                "pkg/__init__.py": "",
                "pkg/service.py": "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    from . import helper_tests\n",
                "pkg/helper_tests.py": self.CASE,
            }[p],
            verify.is_test_path,
        )
        self.assertEqual(role_policy.TEST, roles.role("pkg/helper_tests.py"))
        self.assertTrue(roles.protected("pkg/helper_tests.py"))

    def test_if_type_checking_still_walks_test_expression_and_else_branches(self):
        source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    import type_only\n"
            "else:\n"
            "    import runtime_else\n"
        )
        self.assertEqual(
            ({"typing", "typing.TYPE_CHECKING", "runtime_else"}, False), role_policy._imports("a.py", source)
        )

        elif_source = (
            "from typing import TYPE_CHECKING\n"
            "if TYPE_CHECKING:\n"
            "    import type_only\n"
            "elif runtime_flag:\n"
            "    import runtime_elif\n"
            "else:\n"
            "    import runtime_else\n"
        )
        self.assertEqual(
            ({"typing", "typing.TYPE_CHECKING", "runtime_elif", "runtime_else"}, False),
            role_policy._imports("a.py", elif_source),
        )

        inner_elif = (
            "from typing import TYPE_CHECKING\n"
            "if runtime_flag:\n"
            "    import runtime_if\n"
            "elif TYPE_CHECKING:\n"
            "    import type_only\n"
            "else:\n"
            "    import runtime_else\n"
        )
        self.assertEqual(
            ({"typing", "typing.TYPE_CHECKING", "runtime_if", "runtime_else"}, False),
            role_policy._imports("a.py", inner_elif),
        )

        test_expr_dynamic = (
            "from typing import TYPE_CHECKING\n"
            "import importlib\n"
            "if TYPE_CHECKING and importlib.import_module(selected):\n"
            "    import type_only\n"
        )
        modules, uncertain = role_policy._imports("a.py", test_expr_dynamic)
        self.assertEqual({"typing", "typing.TYPE_CHECKING", "importlib"}, modules)
        self.assertTrue(uncertain)

        for kept_source in (
            "from typing import TYPE_CHECKING\nif not TYPE_CHECKING:\n    import runtime_kept\n",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING or runtime_flag:\n    import runtime_kept\n",
        ):
            with self.subTest(kept_source=kept_source):
                modules, uncertain = role_policy._imports("a.py", kept_source)
                self.assertIn("runtime_kept", modules)
                self.assertFalse(uncertain)

    def test_shadowed_or_rebound_type_checking_keeps_body_conservatively(self):
        cases = (
            "from typing import TYPE_CHECKING\nTYPE_CHECKING = True\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nTYPE_CHECKING = False\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nTYPE_CHECKING: bool = True\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nif (TYPE_CHECKING := True):\n    import kept\n",
            "from typing import TYPE_CHECKING\ndef TYPE_CHECKING(): pass\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nasync def TYPE_CHECKING(): pass\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nclass TYPE_CHECKING: pass\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\ndef f(TYPE_CHECKING):\n    if TYPE_CHECKING:\n        import kept\n",
            "from typing import TYPE_CHECKING\nf = lambda TYPE_CHECKING: TYPE_CHECKING\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\ntry:\n    pass\nexcept Exception as TYPE_CHECKING:\n    pass\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\ndef f():\n    global TYPE_CHECKING\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\ndef outer(TYPE_CHECKING):\n    def inner():\n        nonlocal TYPE_CHECKING\n    if TYPE_CHECKING:\n        import kept\n",
            "from typing import TYPE_CHECKING\nmatch value:\n    case TYPE_CHECKING:\n        pass\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nmatch value:\n    case [*TYPE_CHECKING]:\n        pass\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nmatch value:\n    case {**TYPE_CHECKING}:\n        pass\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nimport other as TYPE_CHECKING\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nfrom other import TYPE_CHECKING\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nfrom .typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING\nfrom typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import kept\n",
            "if flag:\n    from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import kept\n",
            "def f():\n    from typing import TYPE_CHECKING\n    if TYPE_CHECKING:\n        import kept\n",
            "import typing as t\nt = object()\nif t.TYPE_CHECKING:\n    import kept\n",
            "import typing as t\ndef f(t):\n    if t.TYPE_CHECKING:\n        import kept\n",
            "import typing as t\nimport other as t\nif t.TYPE_CHECKING:\n    import kept\n",
            "import typing\ntyping.TYPE_CHECKING = True\nif typing.TYPE_CHECKING:\n    import kept\n",
            "import typing\nimport typing.sub\nif typing.TYPE_CHECKING:\n    import kept\n",
            "from typing import TYPE_CHECKING as t\nimport typing as t\nif t:\n    import kept\n",
        )
        if sys.version_info >= (3, 12):
            cases += (
                "from typing import TYPE_CHECKING\ntype TYPE_CHECKING = int\nif TYPE_CHECKING:\n    import kept\n",
            )
        for source in cases:
            with self.subTest(source=source):
                modules, _ = role_policy._imports("pkg/mod.py", source)
                self.assertIn("kept", modules)

        shadowed = {
            "pkg/__init__.py": "",
            "pkg/service.py": "from typing import TYPE_CHECKING\nTYPE_CHECKING = True\nif TYPE_CHECKING:\n    from . import helper_tests\n",
            "pkg/helper_tests.py": self.CASE,
        }
        roles = role_policy.build(shadowed, shadowed.__getitem__, verify.is_test_path)
        self.assertEqual(role_policy.AMBIGUOUS, roles.role("pkg/helper_tests.py"))

    def test_dynamic_imports_outside_type_checking_remain_uncertain(self):
        for source in (
            "from typing import TYPE_CHECKING\nimport importlib\nif TYPE_CHECKING:\n    import type_only\nimportlib.import_module(name)\n",
            "from typing import TYPE_CHECKING\nimport importlib\nif TYPE_CHECKING:\n    import type_only\nelse:\n    importlib.import_module(name)\n",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import type_only\n__import__(name)\n",
            "from typing import TYPE_CHECKING\nif TYPE_CHECKING:\n    import type_only\nelse:\n    from .. import too_far\n",
        ):
            with self.subTest(source=source):
                modules, uncertain = role_policy._imports("pkg/mod.py", source)
                self.assertNotIn("type_only", modules)
                self.assertTrue(uncertain)

        type_only_dynamic = (
            "from typing import TYPE_CHECKING\nimport importlib\nif TYPE_CHECKING:\n    importlib.import_module(name)\n"
        )
        modules, uncertain = role_policy._imports("pkg/mod.py", type_only_dynamic)
        self.assertEqual({"importlib", "typing", "typing.TYPE_CHECKING"}, modules)
        self.assertFalse(uncertain)
