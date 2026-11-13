"""Per-test results from ``cargo test``'s own output (autocode_cargo_tests).

A live Rust bug fix (Claude models, 2026-11-13) named its tests, the proof saw one exit code, and the
Resolver asked the person to accept Cargo's log lines by hand. The outputs below are Cargo 1.97's own,
trimmed of backtraces and build paths. The proof cases run real Cargo, as tests.test_ruby_test_suite
runs real Ruby, and never launch a provider.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

import autocode_cargo_tests as cargo_tests
import autocode_changed_tests as changed_tests
import autocode_named_reporters as reporters
import autocode_regression as regression
import autocode_test_cases as test_cases
import autocode_verify as verify
from autocode_test_framework import Framework

# A workspace of two crates, `a` and `b`, each with unit tests in src/lib.rs, tests/api.rs and a doc test.
WORKSPACE = """\
   Compiling b v0.1.0 (/work/crates/b)
   Compiling a v0.1.0 (/work/crates/a)
    Finished `test` profile [unoptimized + debuginfo] target(s) in 0.23s
     Running unittests src/lib.rs (target/debug/deps/a-5bf8184f8e9b44d9)

running 3 tests
test tests::slow ... ignored, slow
test tests::adds ... ok
test tests::prints ... ok

test result: ok. 2 passed; 0 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.00s

     Running tests/api.rs (target/debug/deps/api-42e7584c31e15232)

running 1 test
test api_adds ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

     Running unittests src/lib.rs (target/debug/deps/b-0696592e21a00733)

running 3 tests
test tests::slow ... ignored, slow
test tests::adds ... ok
test tests::prints ... FAILED

failures:

---- tests::prints stdout ----
test fake ... ok

thread 'tests::prints' (32763) panicked at crates/b/src/lib.rs:17:49:
assertion `left == right` failed
  left: 2
 right: 3


failures:
    tests::prints

test result: FAILED. 1 passed; 1 failed; 1 ignored; 0 measured; 0 filtered out; finished in 0.08s

error: test failed, to rerun pass `-p b --lib`
     Running tests/api.rs (target/debug/deps/api-9681724918d63a82)

running 1 test
test api_adds ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

   Doc-tests a

running 1 test
test crates/a/src/lib.rs - add (line 3) ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.08s

   Doc-tests b

running 1 test
test crates/b/src/lib.rs - add (line 3) ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.06s

error: 1 target failed:
    `-p b --lib`
"""
# The new test calls a function the original code lacks: nothing builds, so no test runs.
BUILD_FAILED = """\
   Compiling a v0.1.0 (/work/crates/a)
error[E0425]: cannot find function `sub` in crate `a`
 --> crates/a/tests/api.rs:4:31
  |
4 | fn api_subs() { assert_eq!(a::sub(2, 2), 0); }
  |                               ^^^ not found in `a`

For more information about this error, try `rustc --explain E0425`.
error: could not compile `a` (test "api") due to 1 previous error
"""
# A test called std::process::exit: the binary printed its header and no result.
EXITED = """\
     Running unittests src/lib.rs (target/debug/deps/a-5bf8184f8e9b44d9)

running 1 test
test tests::adds ... ok

test result: ok. 1 passed; 0 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s

     Running tests/api.rs (target/debug/deps/api-42e7584c31e15232)

running 2 tests
error: test failed, to rerun pass `-p a --test api`

Caused by:
  process didn't exit successfully: `/work/target/debug/deps/api-42e7584c31e15232` (exit status: 3)
note: test exited abnormally; to see the full output pass --no-capture to the harness.
"""
ONE_BINARY = """\
     Running tests/pages.rs (target/debug/deps/pages-e003a3cc7525206f)

running 3 tests
test exact_multiple ... ok
test rejects_zero - should panic ... ok
test test_partial_last_page ... FAILED

failures:

test result: FAILED. 2 passed; 1 failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.10s
"""


class ResultsTests(unittest.TestCase):
    def test_each_test_is_named_by_the_file_cargo_ran_and_the_harness_name(self):
        found = cargo_tests.results(ONE_BINARY)
        self.assertEqual(["tests/pages.rs::exact_multiple", "tests/pages.rs::rejects_zero"], found["passed"])
        self.assertEqual(["tests/pages.rs::test_partial_last_page"], found["failed"])
        self.assertEqual((3, True, [], []), (found["total"], found["complete"], found["skipped"], found["uncollected"]))

    def test_a_workspace_reports_every_binary_and_one_crates_failure_is_not_erased_by_the_others_pass(self):
        found = cargo_tests.results(WORKSPACE)
        self.assertEqual(
            [
                "Doc-tests a::crates/a/src/lib.rs - add (line 3)",
                "Doc-tests b::crates/b/src/lib.rs - add (line 3)",
                "src/lib.rs::tests::adds",
                "tests/api.rs::api_adds",
            ],
            found["passed"],
        )
        # `a` passed tests::prints and `b` failed it: both print src/lib.rs, so the identity failed.
        self.assertEqual(["src/lib.rs::tests::prints"], found["failed"])
        self.assertEqual(["src/lib.rs::tests::slow"], found["skipped"])
        self.assertEqual((6, True), (found["total"], found["complete"]))

    def test_a_tests_own_output_that_looks_like_a_result_is_not_one(self):
        # "test fake ... ok" is printed by tests::prints and shown under "failures:".
        every = [name for group in ("passed", "failed", "skipped") for name in cargo_tests.results(WORKSPACE)[group]]
        self.assertFalse([name for name in every if "fake" in name])

    def test_a_build_that_fails_is_a_failed_uncollected_entry(self):
        found = cargo_tests.results(BUILD_FAILED)
        entry = 'a (test "api")::[build failed]'
        self.assertEqual(([], [entry], [entry], [entry]), tuple(found[k] for k in ("passed", "failed", "collection_errors", "uncollected")))  # fmt: skip
        self.assertEqual((1, True), (found["total"], found["complete"]))

    def test_a_binary_that_printed_no_result_makes_the_results_incomplete(self):
        found = cargo_tests.results(EXITED)
        self.assertEqual(["src/lib.rs::tests::adds"], found["passed"])
        self.assertEqual(["tests/api.rs::[no result]"], found["failed"])
        self.assertFalse(found["complete"])
        self.assertEqual([], found["uncollected"])

    def test_results_that_disagree_with_the_binarys_own_summary_are_incomplete(self):
        for text in (
            ONE_BINARY.replace("2 passed; 1 failed", "3 passed; 0 failed"),
            ONE_BINARY.replace("running 3 tests", "running 4 tests"),
            ONE_BINARY.replace("test exact_multiple ... ok\n", "test exact_multiple ... hello\nok\n"),  # --nocapture
        ):
            with self.subTest(text=text[60:140]):
                found = cargo_tests.results(text)
                self.assertFalse(found["complete"])
                self.assertIn("tests/pages.rs::[no result]", found["failed"])

    def test_colored_output_reads_the_same_and_other_output_gives_no_results(self):
        colored = ONE_BINARY.replace("... ok", "... \x1b[32mok\x1b[0m").replace("FAILED", "\x1b[31mFAILED\x1b[0m")
        self.assertEqual(cargo_tests.results(ONE_BINARY), cargo_tests.results(colored))
        for text in ("", None, "make: *** No rule to make target 'test'.\n", "test result: ok. 1 passed; 0 failed;\n"):
            with self.subTest(text=text):
                self.assertIsNone(cargo_tests.results(text))

    def test_a_benchmark_line_is_counted_as_measured_not_as_a_test(self):
        text = ONE_BINARY.replace("test exact_multiple ... ok", "test exact_multiple ... bench:  12 ns/iter (+/- 1)")
        found = cargo_tests.results(text.replace("2 passed; 1 failed; 0 ignored; 0 measured", "1 passed; 1 failed; 0 ignored; 1 measured"))  # fmt: skip
        self.assertEqual((["tests/pages.rs::rejects_zero"], 2, True), (found["passed"], found["total"], found["complete"]))  # fmt: skip


class CommandTests(unittest.TestCase):
    def test_a_plain_cargo_test_command_runs_every_binary(self):
        for command, expected in (
            ("cargo test", "cargo test --no-fail-fast"),
            ("cargo test --workspace -- --test-threads 1", "cargo test --workspace --no-fail-fast -- --test-threads 1"),
            ("RUSTFLAGS=-Dwarnings cargo test --locked", "RUSTFLAGS=-Dwarnings cargo test --locked --no-fail-fast"),
            ("cargo test --no-fail-fast", "cargo test --no-fail-fast"),
        ):
            with self.subTest(command=command):
                self.assertIsNotNone(cargo_tests.command_words(command))
                self.assertEqual(expected, cargo_tests.instrument(command))

    def test_a_command_that_changes_what_the_harness_prints_is_left_alone(self):
        for command in (
            "cargo test -q",
            "cargo test --quiet",
            "cargo test --no-run",
            "cargo test --message-format=json",
            "cargo test -- --nocapture",
            "cargo test -- --format json",
            "cargo test -- --format=terse",
            "cargo test -- -q",
            "cargo test -- --list",
            "cargo test && echo done",
            "cd crates/a && cargo test",
            "cargo nextest run",
            "cargo build",
            "make test",
            "cargo test 'unterminated",
            "",
            None,
        ):
            with self.subTest(command=command):
                self.assertIsNone(cargo_tests.command_words(command))
                self.assertEqual(command, cargo_tests.instrument(command))

    def test_the_proof_expects_and_reads_cargo_results_whichever_suite_was_detected(self):
        cargo = Framework("cargo", "cargo test")
        self.assertTrue(cargo.per_test)
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "suite-on-base.log"
            log.write_text(ONE_BINARY)
            receipt = {"command": "cargo test --no-fail-fast", "output": str(log)}
            for framework in (cargo, Framework("make", "make test"), None):
                with self.subTest(framework=framework and framework.name):
                    self.assertTrue(verify.expects_results(framework, "cargo test"))
                    self.assertEqual("cargo test --no-fail-fast", verify._with_results(framework, "cargo test", log))
                    self.assertEqual(
                        3, verify.per_test_results(framework, receipt, Path(folder) / "x.junit.xml")["total"]
                    )
            # A command whose lines cannot be read is judged by its exit code, as before.
            self.assertFalse(verify.expects_results(cargo, "cargo test -q"))
            self.assertEqual("cargo test -q", verify._with_results(cargo, "cargo test -q", log))
            quiet = {"command": "cargo test -q", "output": str(log)}
            self.assertIsNone(verify.per_test_results(cargo, quiet, Path(folder) / "x.junit.xml"))
            missing = {"command": "cargo test --no-fail-fast", "output": str(Path(folder) / "absent.log")}
            self.assertIsNone(verify.per_test_results(cargo, missing, Path(folder) / "x.junit.xml"))

    def test_changed_rust_tests_are_proved_by_the_suite_itself(self):
        cargo = Framework("cargo", "cargo test")
        self.assertEqual("cargo test", cargo.targeted(["tests/pages.rs"]))
        self.assertEqual("cargo test", cargo.targeted(["tests/data/pages.json", "crates/a/tests/api.rs"]))
        self.assertIsNone(cargo.targeted(["tests/data/pages.json"]))
        self.assertIsNone(cargo.targeted([]))
        chosen = verify.select_commands(cargo, ["tests/pages.rs"])
        self.assertEqual(("cargo test", "derived:cargo"), (chosen["regression"], chosen["regression_source"]))
        # A command the person typed is the suite and the proving command; one that cannot be read is neither.
        typed = verify.command_framework("cargo test --workspace")
        self.assertEqual(("cargo", "cargo test --workspace"), (typed.name, typed.targeted(["tests/pages.rs"])))
        self.assertIsNone(verify.command_framework("cargo test -q"))

    def test_a_criterion_names_a_rust_test_by_its_function(self):
        ids = [
            "tests/pages.rs::exact_multiple",
            "tests/pages.rs::test_partial_last_page",
            "src/lib.rs::tests::exact_multiple_inline",
            "Doc-tests pager::src/lib.rs - page_count (line 3)",
        ]
        cases = [
            {"id": "AC1", "test_name": "test_partial_last_page"},
            {"id": "AC2", "test_name": "exact_multiple"},
            {"id": "AC3", "test_name": "exact_multiple_inline"},
            {"id": "AC4", "test_name": "tests"},  # a file or module is not a test
        ]
        self.assertEqual(
            {"AC1": [ids[1]], "AC2": [ids[0]], "AC3": [ids[2]], "AC4": []},
            test_cases.match_cases(cases, ids, framework="cargo"),
        )
        # The test's own name, not the "tests" of its path, decides whether a changed file names it.
        self.assertEqual("exact_multiple", test_cases.function_name(ids[0]))
        self.assertEqual("exact_multiple_inline", test_cases.function_name(ids[2]))
        self.assertEqual("page_count", test_cases.function_name(ids[3]))
        self.assertEqual("add", test_cases.function_name("Doc-tests a::src/lib.rs - Sum::add (line 9) - should panic"))
        self.assertFalse(test_cases.named_in(ids[0], "#[cfg(test)]\nmod tests {\n    fn other() {}\n}\n"))
        self.assertTrue(test_cases.named_in(ids[0], "#[test]\nfn exact_multiple() {}\n"))


class PlanNameTests(unittest.TestCase):
    """A Rust test needs no test_ prefix, so a plan names an existing one as it is."""

    def cases(self, *methods):
        rows = [{"id": f"AC{n}", "criterion": f"c{n}", "verification_method": m} for n, m in enumerate(methods, 1)]
        return test_cases.plan_cases({"acceptance_criteria": rows})

    def test_a_declared_identifier_without_a_test_prefix_is_kept_as_a_native_name(self):
        cases = self.cases(
            "guard: exact_multiple",
            "guard: tests::zero_size_is_refused — the existing unit test",
            "test: test_ac3_partial_last_page",
            "guard: manual",  # a lone word is prose
            "guard: the existing tests keep passing",
            "guard: cargo test",
            "test: exact_multiple, partial_last_page",
        )
        self.assertEqual(
            [("exact_multiple", None), ("tests::zero_size_is_refused", None), (None, "test_ac3_partial_last_page")]
            + [(None, None)] * 4,
            [(case.get("native_name"), case.get("test_name")) for case in cases],
        )
        self.assertEqual(["preserve", "preserve", None], [case.get("kind") for case in cases[:3]])

    def test_only_a_runner_whose_tests_need_no_prefix_binds_the_native_name(self):
        cases = self.cases("guard: exact_multiple", "guard: tests::zero_size_is_refused", "test: test_ac3_partial")
        self.assertEqual(
            ["exact_multiple", "tests::zero_size_is_refused", "test_ac3_partial"],
            [case.get("test_name") for case in test_cases.bound(cases, "cargo")],
        )
        for framework in ("pytest", "go", "node", None):
            with self.subTest(framework=framework):
                self.assertEqual(cases, test_cases.bound(cases, framework))
        ids = [
            "tests/pages.rs::exact_multiple",
            "src/lib.rs::tests::zero_size_is_refused",
            "src/lib.rs::other::zero_size_is_refused",
            "tests/pages.rs::test_ac3_partial",
            "tests/pages.rs::test_ac1_exact_multiple",
        ]
        self.assertEqual(
            {"AC1": [ids[0]], "AC2": [ids[1]], "AC3": [ids[3]]}, test_cases.match_cases(cases, ids, framework="cargo")
        )
        # Elsewhere the criterion id still decides: AC1's test is the one named after it.
        self.assertEqual(
            {"AC1": [ids[4]], "AC2": [], "AC3": [ids[3]]}, test_cases.match_cases(cases, ids, framework="pytest")
        )

    def test_a_guard_that_names_an_existing_rust_test_rests_on_the_suite(self):
        cases = self.cases("guard: exact_multiple", "guard: zero_size_is_refused")
        passing = ["tests/pages.rs::exact_multiple", "tests/pages.rs::zero_size_is_refused"]

        def checked(framework, suite_passing):
            proof = {"framework": {"name": framework}, "verdict": "PASS", "failures": [], "unverified": []}
            proof.update(fail_to_pass=[], pass_to_pass=[])
            regression.check_cases(proof, cases, suite_passing=suite_passing)
            return proof

        proof = checked("cargo", passing)
        self.assertEqual("PASS", proof["verdict"], proof)
        self.assertEqual({"AC1": [passing[0]], "AC2": [passing[1]]}, proof["case_tests"])
        missing = checked("cargo", passing[:1])
        self.assertEqual("FAIL", missing["verdict"])
        self.assertIn("has no test named zero_size_is_refused", " ".join(missing["failures"]))
        self.assertEqual("FAIL", checked("pytest", passing)["verdict"])


OLD = """\
use pager::page_count;

const SIZE: u32 = 5;

/// Ten items fill two pages.
#[test]
fn exact_multiple() {
    assert_eq!(page_count(10, SIZE), 2);
}

mod edges {
    #[test]
    fn empty() {
        assert_eq!(pager::page_count(0, 5), 0);
    }
}
"""
ADDED = '\n#[test]\n#[should_panic(expected = "size")]\nfn test_zero_size() {\n    page_count(1, 0);\n}\n'


class ChangedTests(unittest.TestCase):
    def changed(self, before, after, path="tests/pages.rs"):
        return cargo_tests.changed_names([(path, before, after)])

    def test_only_the_functions_the_change_adds_or_edits_are_changed(self):
        self.assertEqual({("tests/pages.rs", "test_zero_size")}, self.changed(OLD, OLD + ADDED))
        edited = OLD.replace("page_count(0, 5), 0", 'page_count(0, 5), 0, "empty"')
        self.assertEqual({("tests/pages.rs", "empty")}, self.changed(OLD, edited))
        # Its attributes and doc comment are part of a test: ignoring it changes it.
        ignored = OLD.replace("#[test]\nfn exact_multiple", "#[test]\n#[ignore]\nfn exact_multiple")
        self.assertEqual({("tests/pages.rs", "exact_multiple")}, self.changed(OLD, ignored))
        self.assertEqual(set(), self.changed(OLD, OLD.replace("\n\nconst", "\n\n\nconst")))  # layout only

    def test_a_change_outside_every_function_or_a_new_file_changes_every_test_of_the_file(self):
        self.assertIn(("tests/pages.rs", None), self.changed(OLD, OLD.replace("SIZE: u32 = 5", "SIZE: u32 = 4")))
        self.assertEqual({("tests/pages.rs", None)}, self.changed(None, OLD))

    def test_a_result_is_matched_to_its_changed_function_in_its_own_file(self):
        names = {("crates/a/tests/api.rs", "api_subs"), ("tests/new.rs", None)}
        for test, expected in (
            ("tests/api.rs::api_subs", True),  # Cargo prints the file relative to its package
            ("tests/api.rs::api_adds", False),
            ("tests/api.rs::edges::api_subs", True),
            ("src/lib.rs::tests::api_subs", False),
            ("tests/new.rs::anything", True),
            ("Doc-tests a::crates/a/src/lib.rs - add (line 3)", False),
            ('a (test "api")::[build failed]', False),
        ):
            with self.subTest(test=test):
                self.assertEqual(expected, cargo_tests.defined_in(names, test))

    def test_the_filter_reads_the_changed_rust_test_files_and_keeps_every_test_without_one(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            (root / "tests/pages.rs").write_text(OLD + ADDED)
            (root / "tests/new.rs").write_text("#[test]\nfn fresh() {}\n")
            changes = {"tests/pages.rs": "modified", "tests/new.rs": "added", "tests/gone.rs": "deleted"}
            changed = cargo_tests.changed_filter(root, changes, lambda path: OLD)
            self.assertTrue(changed("tests/pages.rs::test_zero_size"))
            self.assertFalse(changed("tests/pages.rs::exact_multiple"))
            self.assertTrue(changed("tests/new.rs::fresh"))
            self.assertFalse(changed("tests/gone.rs::old"))
            self.assertIsNone(cargo_tests.changed_filter(root, {"tests/data.json": "modified"}, lambda path: ""))
            self.assertIsNone(cargo_tests.changed_filter(root, {"tests/gone.rs": "deleted"}, lambda path: OLD))

    def test_the_proof_asks_the_runner_of_the_run_and_keeps_every_test_for_any_other(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "tests").mkdir()
            (root / "tests/pages.rs").write_text(OLD + ADDED)
            (root / "pager_test.go").write_text("package pager\n\nfunc TestNew(t *testing.T) {\n}\n")
            (root / "go.mod").write_text("module example.test/pager\n")
            asked = []

            def git(workspace, *args, check=True):
                asked.append((str(workspace), args, check))
                return OLD

            run = SimpleNamespace(
                framework=Framework("cargo", "cargo test"),
                workspace=root,
                base="abc123",
                changes={"src/lib.rs": "modified", "tests/pages.rs": "modified", "pager_test.go": "added"},
                runnable_tests=["tests/pages.rs"],
            )
            changed = changed_tests.changed_filter(run, git)
            self.assertTrue(changed("tests/pages.rs::test_zero_size"))
            self.assertFalse(changed("tests/pages.rs::exact_multiple"))
            # The original text is read from the base commit, and only for the changed test files.
            self.assertEqual([(str(root), ("show", "abc123:tests/pages.rs"), False)], asked)
            run.framework = Framework("go", "go test ./...")
            changed = changed_tests.changed_filter(run, git)
            self.assertTrue(changed("example.test/pager::TestNew"))
            self.assertFalse(changed("example.test/pager::TestOld"))
            for framework in (Framework("pytest", "pytest", python="python3"), None):
                run.framework = framework
                self.assertIsNone(changed_tests.changed_filter(run, git))


class ReceiptTests(unittest.TestCase):
    def test_a_cargo_receipt_is_read_from_its_own_output_also_inside_a_package(self):
        with tempfile.TemporaryDirectory() as folder:
            log = Path(folder) / "suite-on-base.log"
            log.write_text(ONE_BINARY)
            path = Path(folder) / "suite-on-base.junit.xml"
            for command in ("cargo test --no-fail-fast", "cd crates/a && cargo test --no-fail-fast"):
                with self.subTest(command=command):
                    read = reporters.reader(path, {"command": command, "output": str(log)})
                    self.assertEqual(3, read(path)["total"])
            self.assertIsNone(reporters.reader(path, {"command": "cargo test --no-fail-fast"})(path))
            for receipt in (None, {}, {"command": "make test", "output": str(log)}, {"command": "cargo test -q"}):
                with self.subTest(receipt=receipt):
                    self.assertIsNone(reporters.reader(path, receipt))
            # A reporter's own file is still read by its reporter.
            self.assertIsNotNone(reporters.reader(Path(folder) / "x.rspec.json", {"command": "cargo test"}))

    def test_cargo_commands_report_and_are_instrumented_without_a_result_file(self):
        self.assertTrue(reporters.reports("cargo test"))
        self.assertTrue(reporters.reports("bundle exec rspec"))
        self.assertFalse(reporters.reports("cargo test -q"))
        self.assertFalse(reporters.reports("make test"))
        self.assertIsNone(reporters.extension("cargo test"))
        self.assertEqual("cargo test --no-fail-fast", reporters.instrument("cargo test", "/evidence/x.junit.xml"))
        self.assertIsNone(reporters.instrument("make test", "/evidence/x.junit.xml"))


MANIFEST = '[package]\nname = "pager"\nversion = "0.1.0"\nedition = "2021"\n'
LIBRARY = "pub fn page_count(total: u32, size: u32) -> u32 {\n    total / size\n}\n"
FIXED = LIBRARY.replace("total / size", "total.div_ceil(size)")
EXISTING = "use pager::page_count;\n\n#[test]\nfn exact_multiple() {\n    assert_eq!(page_count(10, 5), 2);\n}\n"
REGRESSION = EXISTING + "\n#[test]\nfn test_partial_last_page() {\n    assert_eq!(page_count(11, 5), 3);\n}\n"
# A new test the original code already satisfies: it cannot show that anything was fixed.
VACUOUS = EXISTING + "\n#[test]\nfn test_single_page() {\n    assert_eq!(page_count(5, 5), 1);\n}\n"
SEED = {"Cargo.toml": MANIFEST, ".gitignore": "target/\n", "src/lib.rs": LIBRARY, "tests/pages.rs": EXISTING}


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


@unittest.skipUnless(shutil.which("cargo"), "runs the project's tests with Cargo")
class ProofTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="cargo-suite-")
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

    def verify(self, **options):
        framework = verify.detect_framework(self.root)
        self.assertEqual("cargo", framework.name)
        base_suite = verify.baseline(
            self.root, self.base, self.evidence, framework=framework, suite_command=framework.suite, timeout=300
        )
        return verify.verify(
            self.root, self.base, self.evidence, framework=framework, base_suite=base_suite, timeout=300, **options
        )

    def test_a_fix_is_proved_by_its_named_test_failing_before_and_passing_after(self):
        self.write({"src/lib.rs": FIXED, "tests/pages.rs": REGRESSION})
        result = self.verify()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(["tests/pages.rs::test_partial_last_page"], result["fail_to_pass"])
        self.assertEqual(["tests/pages.rs::exact_multiple"], result["pass_to_pass"])
        self.assertEqual([], result["review_reasons"])  # named tests, not an exit code
        self.assertEqual("cargo test --no-fail-fast", result["checks"]["regression_on_base"]["command"])
        # The approved criteria: a restored behavior and one that already worked.
        cases = [
            {
                "id": "AC1",
                "text": "a partial last page counts",
                "kind": "restore",
                "test_name": "test_partial_last_page",
            },
            {
                "id": "AC2",
                "text": "an exact multiple is unchanged",
                "kind": "preserve",
                "native_name": "exact_multiple",
            },
        ]
        regression.check_cases(result, cases)
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(
            {"AC1": ["tests/pages.rs::test_partial_last_page"], "AC2": ["tests/pages.rs::exact_multiple"]},
            result["case_tests"],
        )

    def test_a_criterion_whose_test_does_not_exist_is_refused(self):
        self.write({"src/lib.rs": FIXED, "tests/pages.rs": REGRESSION})
        result = self.verify()
        regression.check_cases(result, [{"id": "AC1", "text": "x", "kind": "restore", "test_name": "test_missing"}])
        self.assertEqual("FAIL", result["verdict"], result)

    def test_a_new_test_the_original_code_already_passes_proves_nothing(self):
        self.write({"src/lib.rs": FIXED, "tests/pages.rs": VACUOUS})
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)
        self.assertEqual([], result["fail_to_pass"])
        self.assertIn("do not reproduce the bug", " ".join(result["failures"]))

    def test_a_change_that_leaves_its_own_test_failing_is_refused_by_name(self):
        self.write({"src/lib.rs": LIBRARY.replace("total / size", "total / size + 0"), "tests/pages.rs": REGRESSION})
        result = self.verify()
        self.assertEqual("FAIL", result["verdict"], result)
        self.assertIn("tests/pages.rs::test_partial_last_page", " ".join(result["failures"]))

    def test_a_feature_whose_test_cannot_build_on_the_original_code_keeps_the_untouched_tests(self):
        added = "\npub fn last_page(total: u32, size: u32) -> u32 {\n    total % size\n}\n"
        test = "\n#[test]\nfn test_last_page() {\n    assert_eq!(pager::last_page(11, 5), 1);\n}\n"
        self.write({"src/lib.rs": LIBRARY + added, "tests/pages.rs": EXISTING + test})
        result = self.verify(new_behavior=True)
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(['pager (test "pages")::[build failed]'], result["checks"]["regression_on_base"]["results"]["failed"])  # fmt: skip
        # Only the new test shows the new behavior; exact_multiple passed on the original code and was kept.
        self.assertEqual(["tests/pages.rs::test_last_page"], result["fail_to_pass"])
        self.assertEqual(["tests/pages.rs::exact_multiple"], result["pass_to_pass"])

    def test_a_refactor_with_no_new_test_rests_on_the_tests_the_project_has(self):
        helper = "\nfn pages(total: u32, size: u32) -> u32 {\n    total / size\n}\n"
        self.write({"src/lib.rs": LIBRARY.replace("total / size", "pages(total, size)") + helper})
        result = self.verify(preserve_only=True)
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(["tests/pages.rs::exact_multiple"], result["checks"]["suite_on_candidate"]["results"]["passed"])  # fmt: skip


if __name__ == "__main__":
    unittest.main()
