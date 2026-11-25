"""Shell syntax counts only where the shell reads it (autocode_shell_syntax), for every command parser.

AutoCode quotes test names in the commands it narrows to the proving tests. Each parser checked the raw
text for shell characters, so the quoted ``(`` of a Rust documentation test or the ``|`` of a Go
``-run`` pattern made AutoCode's own command unreadable: no per-test results, no coverage, no mutation
check (setup sweep, docs/setup-sweep.md).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import autocode_cargo_tests as cargo_tests
import autocode_diff_coverage as diff_coverage
import autocode_minitest_tests as minitest_tests
import autocode_node_tests as node_tests
import autocode_npm_scripts as npm_scripts
import autocode_proof_stability as proof_stability
import autocode_python_tests as python_tests
import autocode_rspec_tests as rspec_tests
from autocode_shell_syntax import outside_quotes
from autocode_test_framework import Framework


class OutsideQuotes(unittest.TestCase):
    def test_syntax_the_shell_reads_is_found(self):
        for command in (
            "cargo test; rm -rf /",
            "go test ./... && echo",
            "go test $(cat list)",
            "rspec `ls`",
            'rspec "$HOME/spec"',
            'rspec "a`b`"',
            "pytest (x)",
            "pytest a > out",
            "pytest 'a\nb'",
            "pytest 'open",
            'pytest "open',
        ):
            with self.subTest(command=command):
                self.assertTrue(outside_quotes(command))

    def test_a_quoted_literal_is_a_word(self):
        for command in (
            "cargo test -- --exact 'src/lib.rs - (line 20)'",
            "go test -run '^(TestA|TestB)$' ./...",
            'rspec -e "adds (two) pages"',
            "node --test --test-name-pattern 'a|b' test/",
            "ruby -e 'Dir.glob(\"t/*.rb\").each { |f| require f }'",
        ):
            with self.subTest(command=command):
                self.assertFalse(outside_quotes(command))

    def test_only_the_given_characters_count(self):
        self.assertFalse(outside_quotes(r"C:\venv\python.exe -m pytest", python_tests.PYTHON_SHELL_SYNTAX))
        self.assertTrue(outside_quotes(r"C:\venv\python.exe -m pytest"))


class NarrowedCommandsAreRead(unittest.TestCase):
    """The command narrow() writes is one its own runner's parser reads, whatever the test names hold."""

    def test_a_rust_documentation_test_is_selected_and_its_results_read(self):
        command = proof_stability.narrow(
            "cargo", "cargo test", ["Doc-tests itoa::src/lib.rs - (line 20)", "tests/test.rs::test_u64_max"]
        )
        # rustdoc matches no documentation test under --exact, so the selection filters by substring.
        self.assertEqual("cargo test -- 'src/lib.rs - (line 20)' test_u64_max", command)
        self.assertEqual(
            "cargo test -- --exact test_u64_max", proof_stability.narrow("cargo", "cargo test", ["t.rs::test_u64_max"])
        )
        self.assertIsNotNone(cargo_tests.command_words(command))
        self.assertIn("--no-fail-fast", cargo_tests.instrument(command))

    def test_a_go_selection_is_wrapped_by_the_coverage_tool(self):
        command = proof_stability.narrow("go", "go test ./...", ["example.com/m::TestA", "example.com/m::TestB/sub"])
        self.assertEqual("go test -run '^(TestA|TestB)$' ./...", command)
        with tempfile.TemporaryDirectory() as folder:
            spec = diff_coverage.prepare(Framework("go", "go test ./..."), command, folder, Path(folder) / "e")
        self.assertEqual("go coverprofile", spec["tool"])
        self.assertTrue(spec["command"].startswith("go test -coverprofile="))
        self.assertTrue(spec["command"].endswith(" -run '^(TestA|TestB)$' ./..."))

    def test_an_rspec_example_with_parentheses_is_read(self):
        command = proof_stability.narrow("rspec", "bundle exec rspec", ["spec/a_spec.rb::Pager adds (two) pages"])
        self.assertIsNotNone(rspec_tests.command_words(command))

    def test_a_node_name_pattern_is_read(self):
        command = proof_stability.narrow("node", "node --test test/", ["a::adds (two)", "a::removes"])
        self.assertIsNotNone(node_tests.command_words(command))

    def test_a_quoted_npm_argument_is_read(self):
        self.assertEqual(["npm", "test", "--", "-t", "adds (two)"], npm_scripts.words("npm test -- -t 'adds (two)'"))

    def test_the_minitest_suite_stays_readable(self):
        suite = "ruby -Itest -e 'Dir.glob(\"test/**/*_test.rb\").each { |f| require File.expand_path(f) }'"
        self.assertIsNotNone(minitest_tests.command_words(suite))

    def test_shell_programs_are_still_refused(self):
        self.assertIsNone(cargo_tests.command_words("cargo test && rm -rf target"))
        self.assertIsNone(rspec_tests.command_words("rspec $(ls spec)"))
        self.assertIsNone(npm_scripts.words("npm test | tee out"))
        self.assertIsNone(node_tests.command_words("node --test test/ > out"))
        self.assertIsNone(python_tests.parse("python3 -m pytest; true"))
        self.assertIsNone(proof_stability.narrow("go", "go test ./... && true", ["m::TestA"]))
        with tempfile.TemporaryDirectory() as folder:
            self.assertIsNone(
                diff_coverage.prepare(Framework("go", "go test ./..."), "go test ./... `x`", folder, Path(folder) / "e")
            )


if __name__ == "__main__":
    unittest.main()
