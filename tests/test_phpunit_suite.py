"""A Composer project tested with PHPUnit runs its suite with PHPUnit (autocode_toolchain_suite.phpunit).

A real PHP bug fix (Claude models, 2026-11-14) delivered correct code with two regression tests, and the
proof failed twice: no suite was detected, so the approved plan's first command that names a test file was
taken as the regression command, and that was a guard (``vendor/bin/phpunit --filter
testPartialPageCountsAsAPage tests/PagerTest.php``), which passes on the original code. The Resolver then
asked the person to "correct the saved verification commands". The detection cases are pure. The proof
cases run a real regression proof in a Git project whose ignored ``vendor/bin/phpunit`` is a stand-in that
checks the PHP files below as PHPUnit would, so they need neither PHP nor a provider.
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_command_guards as command_guards
import autocode_toolchain_suite as toolchain_suite
import autocode_verification_plan as verification_plan
import autocode_verify as verify

COMPOSER = (
    '{"name": "acme/pager", "autoload": {"psr-4": {"Acme\\\\": "src/"}}, "require-dev": {"phpunit/phpunit": "^10"}}\n'
)
CONFIG = (
    '<?xml version="1.0" encoding="UTF-8"?>\n<phpunit bootstrap="vendor/autoload.php">\n'
    '  <testsuites><testsuite name="unit"><directory>tests</directory></testsuite></testsuites>\n</phpunit>\n'
)
PAGER = (
    "<?php\nnamespace Acme;\n\nclass Pager\n{\n"
    "    public static function pageCount(int $items, int $perPage): int\n    {\n"
    "        return intdiv($items, $perPage) + 1;\n    }\n}\n"
)
FIXED = PAGER.replace("intdiv($items, $perPage) + 1", "intdiv($items + $perPage - 1, $perPage)")


def case(name, pages, items, per_page):
    return (
        f"    public function {name}(): void\n    {{\n"
        f"        $this->assertSame({pages}, Pager::pageCount({items}, {per_page}));\n    }}\n"
    )


def test_file(*cases):
    return (
        "<?php\nuse Acme\\Pager;\nuse PHPUnit\\Framework\\TestCase;\n\nfinal class PagerTest extends TestCase\n{\n"
        + "\n".join(cases)
        + "}\n"
    )


EXISTING = test_file(case("testPartialPageCountsAsAPage", 3, 21, 10))
REGRESSION = test_file(
    case("testPartialPageCountsAsAPage", 3, 21, 10), case("testAnExactMultipleNeedsNoExtraPage", 2, 20, 10)
)
# A new test the original code already satisfies: it cannot show that anything was fixed.
VACUOUS = test_file(case("testPartialPageCountsAsAPage", 3, 21, 10), case("testFewerItemsIsOnePage", 1, 4, 10))
SEED = {
    "composer.json": COMPOSER,
    "phpunit.xml": CONFIG,
    ".gitignore": "vendor/\n.phpunit.result.cache\n",
    "src/Pager.php": PAGER,
    "tests/PagerTest.php": EXISTING,
}
# What Composer installs as vendor/bin/phpunit, reduced to this project: it evaluates the one return
# statement of src/Pager.php for each assertSame of the test files, exits as PHPUnit does (0, or 1 when a
# test fails, or 2 when the source cannot be read as PHP) and leaves PHPUnit's result cache behind.
STAND_IN = (
    f"#!{sys.executable}\n"
    """import pathlib, re, sys

def intdiv(a, b):
    return a // b

source = pathlib.Path("src/Pager.php").read_text()
found = re.search(r"^\\s*return (.+);$", source, re.M)
if not found:
    print("PHP Parse error in src/Pager.php")
    sys.exit(2)
formula = found.group(1).replace("$", "")
failed = 0
for path in sorted(pathlib.Path("tests").glob("*Test.php")):
    for pages, items, per in re.findall(r"assertSame\\((\\d+), Pager::pageCount\\((\\d+), (\\d+)\\)\\)", path.read_text()):
        if eval(formula, {"intdiv": intdiv, "items": int(items), "perPage": int(per)}) != int(pages):
            failed += 1
pathlib.Path(".phpunit.result.cache").write_text('{"version":1,"defects":[],"times":[]}')
print("FAILURES!" if failed else "OK")
sys.exit(1 if failed else 0)
"""
)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def detect(files):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        return toolchain_suite.detect(root, sorted(files), verify.is_test_path)


class DetectionTests(unittest.TestCase):
    def test_a_composer_project_with_phpunit_runs_its_tests_with_phpunit(self):
        framework = detect(SEED)
        self.assertEqual(("phpunit", "vendor/bin/phpunit"), (framework.name, framework.suite))
        self.assertFalse(framework.per_test)  # judged by the suite's exit code, as Maven is
        self.assertIsNone(framework.targeted(["tests/PagerTest.php"]))

    def test_phpunits_configuration_or_its_package_is_enough(self):
        bare = '{"name": "acme/pager"}\n'
        for files in (
            {"composer.json": bare, "phpunit.xml": CONFIG},
            {"composer.json": bare, "phpunit.xml.dist": CONFIG},
            {"composer.json": bare, "phpunit.dist.xml": CONFIG},
            {"composer.json": COMPOSER},
            {"composer.json": '{"require": {"phpunit/phpunit": "^11"}}\n'},
        ):
            with self.subTest(files=sorted(files)):
                self.assertEqual("phpunit", detect({**files, "tests/PagerTest.php": EXISTING}).name)

    def test_a_composer_project_without_phpunit_has_no_suite(self):
        for manifest in (
            '{"name": "acme/pager"}\n',
            '{"require-dev": {"pestphp/pest": "^2"}}\n',
            '{"require-dev": ["phpunit/phpunit"]}\n',
            '["phpunit/phpunit"]\n',
            "{not json",
        ):
            with self.subTest(manifest=manifest):
                self.assertIsNone(detect({"composer.json": manifest, "src/Pager.php": PAGER}))

    def test_phpunit_files_without_a_root_composer_manifest_are_not_the_projects_suite(self):
        for files in (
            {"phpunit.xml": CONFIG, "tests/PagerTest.php": EXISTING},
            {"api/composer.json": COMPOSER, "api/phpunit.xml": CONFIG, "api/tests/PagerTest.php": EXISTING},
        ):
            with self.subTest(files=sorted(files)):
                self.assertIsNone(detect(files))

    def test_a_suite_the_project_declares_another_way_keeps_priority(self):
        for extra, expected in (
            ({"Makefile": "test:\n\tvendor/bin/phpunit --testdox\n"}, "make"),
            ({"package.json": '{"scripts": {"test": "jest"}, "devDependencies": {"jest": "29"}}'}, "jest"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(expected, detect({**SEED, **extra}).name)
        # A front-end package.json with no tests of its own (a Laravel application's) leaves PHPUnit the suite.
        assets = {"package.json": '{"scripts": {"build": "vite build"}, "devDependencies": {"vite": "5"}}'}
        self.assertEqual("phpunit", detect({**SEED, **assets}).name)

    def test_a_stray_python_helper_does_not_hide_phpunit(self):
        self.assertTrue(toolchain_suite.python_is_secondary(["composer.json", "scripts/release.py"]))
        self.assertFalse(toolchain_suite.python_is_secondary(["composer.json", "pyproject.toml", "pkg/core.py"]))

    def test_a_refactor_is_guarded_by_phpunits_own_command(self):
        framework = detect(SEED)
        guard = {"id": "AC2", "command": "vendor/bin/phpunit"}
        self.assertEqual([guard], command_guards.usable([guard], framework, ["tests/PagerTest.php"]))
        self.assertIsNone(command_guards.problem([guard], framework, []))

    def test_a_plans_phpunit_command_is_a_command_the_runner_replays(self):
        for command in ("vendor/bin/phpunit", "vendor/bin/phpunit --filter testA tests/PagerTest.php", "composer test"):
            with self.subTest(command=command):
                self.assertEqual([command], verification_plan.commands(f"`{command}`"))


class ProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="phpunit-suite-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "project"
        self.evidence = Path(self.temp.name) / "evidence"
        self.write(SEED)
        git(self.root, "init", "-q")
        git(self.root, "config", "maintenance.auto", "false")  # see tests.test_verify.Project
        git(self.root, "config", "gc.auto", "0")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        self.base = git(self.root, "rev-parse", "HEAD")
        # `composer install` has run, as in a person's project: vendor/ is there and ignored.
        self.write({"vendor/bin/phpunit": STAND_IN, "vendor/autoload.php": "<?php\n"})
        (self.root / "vendor/bin/phpunit").chmod(0o755)

    def write(self, files):
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)

    def verify(self):
        framework = verify.detect_framework(self.root)
        self.assertEqual("phpunit", framework.name)
        # The runner hands each copy the project's installed packages, as it does node_modules.
        common = {"framework": framework, "timeout": 300, "dependencies_from": self.root}
        base_suite = verify.baseline(self.root, self.base, self.evidence, suite_command=framework.suite, **common)
        return verify.verify(self.root, self.base, self.evidence, base_suite=base_suite, **common)

    def test_a_fix_with_its_regression_test_is_proved_without_a_command_from_the_person(self):
        self.write({"src/Pager.php": FIXED, "tests/PagerTest.php": REGRESSION})
        result = self.verify()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual([], result["unverified"], result)
        self.assertEqual("detected:phpunit", result["commands"]["suite_source"])
        self.assertEqual("vendor/bin/phpunit", result["commands"]["suite"])
        self.assertEqual(["tests/PagerTest.php"], result["test_files"])
        # The new test ran against the original code and failed there.
        self.assertNotEqual(0, result["checks"]["regression_on_base"]["exit_code"], result["checks"])

    def test_a_new_test_the_original_code_already_passes_proves_nothing(self):
        self.write({"src/Pager.php": FIXED, "tests/PagerTest.php": VACUOUS})
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)
        self.assertIn("pass on the unfixed base code", " ".join(result["failures"]))

    def test_a_change_that_leaves_its_own_test_failing_is_refused(self):
        self.write({"tests/PagerTest.php": REGRESSION})
        self.write({"src/Pager.php": PAGER.replace("+ 1", "+ 1 + 0")})
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)


if __name__ == "__main__":
    unittest.main()
