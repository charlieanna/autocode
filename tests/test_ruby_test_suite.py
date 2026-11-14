"""A Ruby project's own test files are its suite (autocode_toolchain_suite.ruby_tests).

A live bug fix in a Ruby project whose tests are Minitest files under ``test/`` (Claude models,
2026-11-13) ended with the proof ``UNVERIFIED`` ("No project test command was found") and a
question to the person: pass ``--test-command`` and ``--regression-command``. The fix and its
tests were correct; only AutoCode had no command to run them with. The detection cases are pure;
the proof cases run real Ruby, as tests.test_verify runs real Python, and never launch a provider.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_toolchain_suite as toolchain_suite
import autocode_verify as verify

LIBRARY = "module Pager\n  def self.page_count(total, size)\n    total / size\n  end\nend\n"
FIXED = "module Pager\n  def self.page_count(total, size)\n    (total + size - 1) / size\n  end\nend\n"
EXISTING = (
    'require "minitest/autorun"\nrequire_relative "../lib/pager"\n\n'
    "class PagerTest < Minitest::Test\n"
    "  def test_exact_multiple\n    assert_equal 2, Pager.page_count(10, 5)\n  end\nend\n"
)
REGRESSION = EXISTING.replace(
    "  end\nend\n",
    "  end\n\n  def test_partial_last_page_is_counted\n    assert_equal 3, Pager.page_count(11, 5)\n  end\nend\n",
)
# A new test the original code already satisfies: it cannot show that anything was fixed.
VACUOUS = EXISTING.replace(
    "  end\nend\n",
    "  end\n\n  def test_single_page\n    assert_equal 1, Pager.page_count(5, 5)\n  end\nend\n",
)
SEED = {"lib/pager.rb": LIBRARY, "test/pager_test.rb": EXISTING}


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
    def test_test_files_under_the_root_test_directory_are_the_suite(self):
        for files in (
            SEED,
            {"lib/pager.rb": LIBRARY, "test/test_pager.rb": EXISTING},
            {"lib/pager.rb": LIBRARY, "test/unit/models/pager_test.rb": EXISTING},
        ):
            with self.subTest(files=sorted(files)):
                framework = detect(files)
                self.assertEqual("minitest", framework.name)
                self.assertTrue(framework.suite.startswith("ruby -Ilib -Itest -e "), framework.suite)
                self.assertTrue(framework.per_test)

    def test_a_project_that_pins_its_gems_runs_the_suite_through_bundler(self):
        framework = detect({**SEED, "Gemfile": "source 'https://rubygems.org'\ngem 'minitest'\n"})
        self.assertEqual("minitest", framework.name)
        self.assertTrue(framework.suite.startswith("bundle exec ruby -Ilib -Itest -e "), framework.suite)

    def test_a_helper_alone_or_ruby_outside_the_test_directory_is_no_suite(self):
        for files in (
            {"lib/pager.rb": LIBRARY},
            {"lib/pager.rb": LIBRARY, "test/test_helper.rb": 'require "minitest/autorun"\n'},
            {"lib/pager.rb": LIBRARY, "test/support/fixtures.rb": "FIXTURES = {}\n"},
            {"lib/pager_test.rb": EXISTING},
            {"scripts/test/pager_test.rb": EXISTING},
        ):
            with self.subTest(files=sorted(files)):
                self.assertIsNone(detect(files))
                self.assertEqual([], toolchain_suite.ruby_tests(sorted(files)))

    def test_a_suite_the_project_declares_another_way_keeps_priority(self):
        for extra, expected in (
            ({"Gemfile": "gem 'rspec'\n", "spec/pager_spec.rb": "describe('Pager') {}\n"}, "rspec"),
            ({"Makefile": "test:\n\truby test/pager_test.rb\n"}, "make"),
            ({"Cargo.toml": "[package]\nname = 'sample'\n"}, "cargo"),
            ({"go.mod": "module sample\ngo 1.22\n"}, "go"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(expected, detect({**SEED, **extra}).name)


@unittest.skipUnless(shutil.which("ruby"), "runs the project's tests with Ruby")
class ProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="ruby-suite-")
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

    def write(self, files):
        for name, text in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(text)

    def verify(self):
        framework = verify.detect_framework(self.root)
        self.assertEqual("minitest", framework.name)
        base_suite = verify.baseline(
            self.root, self.base, self.evidence, framework=framework, suite_command=framework.suite, timeout=120
        )
        return verify.verify(
            self.root, self.base, self.evidence, framework=framework, base_suite=base_suite, timeout=120
        )

    def test_a_fix_with_its_regression_test_is_proved_without_a_command_from_the_person(self):
        self.write({"lib/pager.rb": FIXED, "test/pager_test.rb": REGRESSION})
        result = self.verify()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual([], result["unverified"], result)
        self.assertEqual("detected:minitest", result["commands"]["suite_source"])
        self.assertEqual(["test/pager_test.rb"], result["test_files"])
        # The new test ran against the original code and failed there.
        self.assertNotEqual(0, result["checks"]["regression_on_base"]["exit_code"], result["checks"])

    def test_a_test_file_the_change_adds_joins_the_suite_on_both_sides(self):
        added = REGRESSION.replace("PagerTest", "PartialPageTest")
        self.write({"lib/pager.rb": FIXED, "test/partial_page_test.rb": added})
        result = self.verify()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(["test/partial_page_test.rb"], result["test_files"])
        self.assertNotEqual(0, result["checks"]["regression_on_base"]["exit_code"], result["checks"])

    def test_a_new_test_the_original_code_already_passes_proves_nothing(self):
        self.write({"lib/pager.rb": FIXED, "test/pager_test.rb": VACUOUS})
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)
        self.assertIn("unfixed base code and passes with the fix", " ".join(result["failures"]))

    def test_a_change_that_leaves_its_own_test_failing_is_refused(self):
        self.write(
            {"test/pager_test.rb": REGRESSION, "lib/pager.rb": LIBRARY.replace("total / size", "total / size + 0")}
        )
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)


if __name__ == "__main__":
    unittest.main()
