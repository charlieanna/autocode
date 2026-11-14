"""The one table of project kinds, and the modules that read it (autocode_project_kinds)."""

import unittest

import autocode_package_scope as package_scope
import autocode_plan_test_suite as plan_test_suite
import autocode_project_kinds as kinds
import autocode_python_test_roles as python_test_roles
import autocode_python_tests as python_tests
import autocode_test_examples as test_examples
import autocode_toolchain_byproducts as byproducts
import autocode_verify as verify
from autocode_test_framework import Framework


class TestRoleTests(unittest.TestCase):
    TESTS = (
        "test_calc.py",
        "calc_test.py",
        "calc_tests.py",
        "calc_test.go",
        "calc.test.ts",
        "calc.spec.mjs",
        "calc.test.jsx",
        "calc_spec.rb",
        "calc_test.rb",
        "test_calc.rb",
        "CalcTest.java",
        "CalcTests.kt",
        "TestCalc.cs",
        "Test_Calc.swift",
        "CalcTest.scala",
        "CalcTest.php",
        "test_calc.sh",
    )
    SUPPORT = ("conftest.py", "Calc.test.tsx.snap")
    PRODUCT = (
        "calc.py",
        "tests.py",
        "calc.go",
        "test_calc.go",
        "calc.ts",
        "calc_spec.py",
        "calc_test.rs",
        "Latest.java",
        "Contest.kt",
        "Testament.cs",
        "Contest.php",
        "CalcTests.php",
        "calc_test.sh",
        "README.md",
    )

    def test_a_file_is_a_test_or_test_support_by_its_name_in_every_language(self):
        for name in self.TESTS:
            self.assertTrue(kinds.is_test_file(name), name)
            self.assertTrue(verify.is_test_path(f"src/{name}"), name)
        for name in self.SUPPORT:
            self.assertFalse(kinds.is_test_file(name), name)
            self.assertTrue(verify.is_test_path(f"src/{name}"), name)
        for name in self.PRODUCT:
            self.assertFalse(kinds.is_test_file(name), name)
            self.assertFalse(verify.is_test_path(f"src/{name}"), name)

    def test_examples_shown_to_a_stage_are_test_files_of_the_same_table(self):
        for name in self.TESTS:
            self.assertTrue(test_examples.is_test_file(f"src/{name}"), name)
        for name in (*self.SUPPORT, *self.PRODUCT):
            self.assertFalse(test_examples.is_test_file(f"src/{name}"), name)

    def test_code_is_known_by_the_suffix_of_any_language(self):
        for path in ("a.py", "a.pyi", "a.tsx", "a.go", "a.rs", "a.rb", "a.kts", "a.hpp", "a.zig", "a.sh"):
            self.assertTrue(verify.is_code_path(path), path)
        for path in ("a.md", "a.json", "a.toml", "a.txt", "Makefile"):
            self.assertFalse(verify.is_code_path(path), path)

    def test_test_directories_are_shared_by_every_reader(self):
        self.assertTrue(verify.is_test_path("pkg/__tests__/a.js"))
        self.assertTrue(verify.is_test_path("spec/a.rb"))
        self.assertFalse(verify.is_test_path("pkg/spec/a.rb"))
        self.assertIs(kinds.TEST_DIRS, python_test_roles.TEST_DIRS)
        self.assertIs(kinds.ROOT_TEST_DIRS, python_test_roles.ROOT_TEST_DIRS)


class RunnerTests(unittest.TestCase):
    def test_each_runner_runs_its_own_languages_collected_files(self):
        expected = {
            "tests/test_a.py": "python",
            "tests/a_test.py": "python",
            "tests/a_tests.py": None,  # pytest does not collect it by default
            "a_test.go": "go",
            "spec/a_spec.rb": "ruby",
            "test/a_test.rb": None,  # Minitest's runner is never targeted file by file
            "a.spec.cts": "javascript",
            "a.ts": None,
        }
        for path, language in expected.items():
            self.assertEqual(language, plan_test_suite.kind(path), path)
        self.assertEqual("RSpec", plan_test_suite.SHOWN[plan_test_suite.RUNNER_KIND["rspec"]])
        self.assertNotIn("minitest", plan_test_suite.RUNNER_KIND)

    def test_per_test_results_come_from_the_runners_the_table_names(self):
        for name in ("pytest", "unittest", "go", "node", "vitest", "jest", "mocha", "rspec", "minitest", "cargo"):
            self.assertTrue(Framework(name, "suite").per_test, name)
        for name in ("make", "npm"):
            self.assertFalse(Framework(name, "suite").per_test, name)

    def test_a_targeted_run_is_given_only_its_runners_test_files(self):
        files = ["pkg/a_test.go", "spec/a_spec.rb", "test/a_test.rb", "web/a.test.ts", "tests/test_a.py", "tests.py"]
        self.assertEqual("go test ./pkg", Framework("go", "go test ./...").targeted(files))
        self.assertEqual(
            "bundle exec rspec spec/a_spec.rb", Framework("rspec", "x", runner="bundle exec rspec").targeted(files)
        )
        self.assertEqual("npx --no-install jest web/a.test.ts", Framework("jest", "x").targeted(files))
        self.assertEqual(
            "py -m pytest -q -p no:cacheprovider tests.py tests/test_a.py",
            Framework("pytest", "x", python="py").targeted(files),
        )
        self.assertTrue(python_tests.TEST_MODULE.match("tests.py"))


class ProjectFilesTests(unittest.TestCase):
    def test_manifests_and_lockfiles_come_from_the_languages_rows(self):
        self.assertEqual(
            {
                "package.json",
                "go.mod",
                "Cargo.toml",
                "pyproject.toml",
                "setup.py",
                "setup.cfg",
                "Gemfile",
                "composer.json",
            },
            set(package_scope.MANIFESTS),
        )
        self.assertEqual(
            {
                "go.sum": "go.mod",
                "Gemfile.lock": "Gemfile",
                "uv.lock": "pyproject.toml",
                "poetry.lock": "pyproject.toml",
                "pdm.lock": "pyproject.toml",
                "package-lock.json": "package.json",
                "npm-shrinkwrap.json": "package.json",
                "yarn.lock": "package.json",
                "pnpm-lock.yaml": "package.json",
                "bun.lock": "package.json",
                "bun.lockb": "package.json",
                "Cargo.lock": "Cargo.toml",
                "composer.lock": "composer.json",
            },
            byproducts.LOCKFILES,
        )
        # PHPUnit's record of its last run, beside its configuration or Composer's manifest.
        phpunit = ("phpunit.xml", "phpunit.xml.dist", "phpunit.dist.xml", "composer.json")
        self.assertEqual({".phpunit.result.cache": phpunit}, byproducts.RUN_FILES)
        self.assertIn("__pycache__", kinds.CACHE_DIRS)
        self.assertIn(".pytest_cache", kinds.CACHE_DIRS)
        self.assertEqual({"__pycache__"}, kinds.BYTECODE_DIRS)
        self.assertEqual((".pyc", ".pyo"), kinds.BYTECODE_SUFFIXES)
        self.assertEqual(("go.mod", "go.sum"), kinds.TEST_PREREQUISITES["go"])
        self.assertEqual(frozenset({"go.mod", "go.sum"}), kinds.ALL_TEST_PREREQUISITES)
        for language in kinds.LANGUAGES:
            for lockfile, manifest in language.lockfiles.items():
                self.assertIn(manifest, language.manifests, lockfile)

    def test_build_directories_come_from_the_languages_rows(self):
        gradle = ("build.gradle", "build.gradle.kts", "settings.gradle", "settings.gradle.kts")
        phpunit = ("phpunit.xml", "phpunit.xml.dist", "phpunit.dist.xml", "composer.json")
        self.assertEqual(
            {
                "target": ("Cargo.toml", "pom.xml"),
                "build": (*gradle, "CMakeLists.txt"),
                ".gradle": gradle,
                ".phpunit.cache": phpunit,
            },
            byproducts.BUILD_DIRS,
        )

    def test_every_language_is_named_once_and_every_targeted_language_says_what_its_runners_run(self):
        names = [language.name for language in kinds.LANGUAGES]
        self.assertEqual(len(names), len(set(names)))
        runners = [runner for language in kinds.LANGUAGES for runner in language.runners]
        self.assertEqual(len(runners), len(set(runners)))
        for language in kinds.LANGUAGES:
            self.assertEqual(bool(language.runners), bool(language.collected), language.name)
            self.assertEqual(bool(language.runners), bool(language.shown), language.name)


if __name__ == "__main__":
    unittest.main()
