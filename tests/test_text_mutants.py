"""Mutation evidence for Go, node:test, JavaScript, Cargo and RSpec runners (#3817).

A Go or JavaScript change had no mutation source, so every changed line needed its own Tester reason
and the Builder was sent back twice for a gap no Builder change could close.
"""

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_changed_code_rework as rework
import autocode_mutants as mutants
import autocode_mutation_check as mutation_check
import autocode_mutation_results as mutation_results
import autocode_proof_stability as proof_stability
import autocode_recovery_history as history
import autocode_text_mutants as text_mutants
import autocode_toolchain_suite as toolchain_suite
import autocode_verify as verify

GO = b"""package x

import "fmt"

func F(a, b int, s string) (int, error) {
\tif a < b && s != "x // y < z" {
\t\tmu.Lock()
\t\tlog.Printf("a < %d", a) // a note
\t\treturn a + b, nil
\t}
\tif err := g(); err != nil {
\t\treturn 0, err
\t}
\tv := `raw < string
still raw true`
\tch <- v
\tfmt.Println(v, true)
\treturn a - 1, fmt.Errorf("a %d", a)
}
"""

JS = b"""export function f(a, b) {
  const re = /a < b/g; // c < d
  if (a === b || isOk("(")) {
    save(a, "x");
    return a > b ? `x ${a < b}` : "y";
  }
  const total = a + b;
  const pick = (x) => x >= 1;
  const list = new Map<string, number>();
  return total / 2 < 3;
}
"""


RUST = b"""use std::fmt;

pub fn pages(total: usize, size: usize) -> usize {
    let s = "a < b // c"; // x < y
    let c = '<'; let l: &'static str = "q";
    if total == 0 {
        return 0;
    }
    if let Some(x) = opt {
        log(x);
    }
    let raw = r#"a + b "quoted" true"#;
    let f = move || total + 1;
    /* outer /* nested < */ still > comment */
    counter.bump();
    total / size
}
fn helper<'a, 'b>(x: &'a str, y: &'b str) -> bool where T: A + B { x < y }
"""

RUBY = b"""class Pager
  def count(total, size)
    return 0 if size == 0
    s = "a < #{"b" + c} > d" # x < y
    q = %w[a + b]
    sql = <<~SQL
      SELECT a < b
    SQL
    if total > 0 && size != 1
      return total / size + 1
    end
    record.save!(x)
    Integer === total
  end
end
"""


def sites(path, source, lines=None):
    made, notes = text_mutants.text_mutants(path, source, lines or range(1, 40), limit=100)
    return [(m.line, m.operator, m.before, m.after) for m in made], notes


class TextMutantTests(unittest.TestCase):
    def test_go_sites_skip_strings_comments_raw_strings_and_channel_sends(self):
        found, notes = sites("x.go", GO)
        self.assertEqual(
            [
                (6, "negate_condition", 'a < b && s != "x // y < z"', '!(a < b && s != "x // y < z")'),
                (6, "swap_bound", "<", "<="),
                (6, "negate_equality", "!=", "=="),
                (6, "swap_logic", "&&", "||"),
                (7, "drop_call", "mu.Lock()", "(removed)"),
                (8, "drop_call", 'log.Printf("a < %d", a)', "(removed)"),
                (9, "swap_arithmetic", "+", "-"),
                (11, "negate_equality", "!=", "=="),
                (12, "constant_return", "err", "nil"),
                (17, "flip_boolean", "true", "false"),
                (17, "drop_call", "fmt.Println(v, true)", "(removed)"),
                (18, "swap_arithmetic", "-", "+"),
            ],
            found,
        )
        self.assertEqual([], notes)

    def test_javascript_sites_skip_regexes_templates_arrows_and_generics(self):
        found, _ = sites("src/x.ts", JS)
        self.assertEqual(
            [
                (3, "negate_condition", 'a === b || isOk("(")', '!(a === b || isOk("("))'),
                (3, "negate_equality", "===", "!=="),
                (3, "swap_logic", "||", "&&"),
                (4, "drop_call", 'save(a, "x");', "(removed)"),
                (5, "swap_bound", ">", ">="),
                (5, "constant_return", 'a > b ? `x ${a < b}` : "y"', "undefined"),
                (7, "swap_arithmetic", "+", "-"),
                (8, "swap_bound", ">=", ">"),
                (10, "swap_bound", "<", "<="),
                (10, "constant_return", "total / 2 < 3", "undefined"),
            ],
            found,
        )

    def test_rust_sites_skip_raw_strings_chars_lifetimes_closures_and_declarations(self):
        found, notes = sites("src/lib.rs", RUST)
        self.assertEqual([], notes)
        self.assertEqual(
            [
                (6, "negate_condition", "total == 0", "!(total == 0)"),
                (6, "negate_equality", "==", "!="),
                (7, "constant_return", "0", "Default::default()"),
                (10, "drop_call", "log(x);", "(removed)"),
                (13, "swap_arithmetic", "+", "-"),
                (15, "drop_call", "counter.bump();", "(removed)"),
            ],
            found,
        )

    def test_ruby_sites_skip_interpolation_heredocs_percent_literals_and_case_equality(self):
        found, notes = sites("lib/pager.rb", RUBY)
        self.assertEqual([], notes)
        self.assertEqual(
            [
                (3, "negate_equality", "==", "!="),
                (9, "negate_condition", "total > 0 && size != 1", "!(total > 0 && size != 1)"),
                (9, "swap_bound", ">", ">="),
                (9, "negate_equality", "!=", "=="),
                (9, "swap_logic", "&&", "||"),
                (10, "swap_arithmetic", "+", "-"),
                (10, "constant_return", "total / size + 1", "nil"),
                (12, "drop_call", "record.save!(x)", "(removed)"),
            ],
            found,
        )

    def test_cargo_and_rspec_commands_are_narrowed_to_the_proving_tests(self):
        self.assertEqual(
            "cargo test -p core -- --test-threads 1 --exact exact_multiple tests::adds",
            proof_stability.narrow(
                "cargo",
                "cargo test -p core -- --test-threads 1",
                ["tests/pages.rs::exact_multiple", "src/lib.rs::tests::adds"],
            ),
        )
        self.assertEqual(
            "bundle exec rspec --example 'Pager counts a partial page'",
            proof_stability.narrow("rspec", "bundle exec rspec", ["spec/pager_spec.rb::Pager counts a partial page"]),
        )
        for name, command in (
            ("cargo", "cargo test adds"),
            ("cargo", "cargo test -- --exact adds"),
            ("cargo", "cargo test -q"),
            ("rspec", "rspec -e partial"),
            ("rspec", "rspec && echo"),
        ):
            with self.subTest(command=command):
                self.assertIsNone(proof_stability.narrow(name, command, ["src/lib.rs::adds"]))

    def test_ac11_minitest_commands_are_narrowed_with_one_anchored_name_filter(self):
        one = ["test/label_test.rb::LabelTest::test_one"]
        for command, tests, expected in (
            (
                "ruby -Ilib -Itest test/label_test.rb",
                one,
                "ruby -Ilib -Itest test/label_test.rb -n '/^(LabelTest#test_one)$/'",
            ),
            (toolchain_suite.RUBY_SUITE, one, toolchain_suite.RUBY_SUITE + " -- -n '/^(LabelTest#test_one)$/'"),
            ("rake test", one, "rake test 'TESTOPTS=-n '\"'\"'/^(LabelTest#test_one)$/'\"'\"''"),
            (
                "ruby test/label_test.rb",
                [*one, "test/label_test.rb::LabelTest::test_many"],
                "ruby test/label_test.rb -n '/^(LabelTest#test_many|LabelTest#test_one)$/'",
            ),
            (
                "ruby test/q_test.rb",
                ["test/q_test.rb::QTest::test_ok?"],
                "ruby test/q_test.rb -n '/^(QTest#test_ok\\?)$/'",
            ),
        ):
            with self.subTest(command=command, tests=tests):
                self.assertEqual(expected, proof_stability.narrow("minitest", command, tests))
        for command in (
            "ruby -Ilib -Itest test/label_test.rb --name test_one",
            "ruby -Ilib -Itest test/label_test.rb -e test_many",
            "ruby -Ilib -Itest test/label_test.rb --exclude test_many",
            "ruby test/label_test.rb; ls",
            "bundle exec rspec",
        ):
            with self.subTest(command=command):
                self.assertIsNone(proof_stability.narrow("minitest", command, one))

    def test_minitest_narrowing_keeps_existing_options_and_refuses_competing_filters(self):
        one = ["test/label_test.rb::LabelTest::test_one"]
        self.assertEqual(
            "rake test 'TESTOPTS=--seed 1 -n '\"'\"'/^(LabelTest#test_one)$/'\"'\"''",
            proof_stability.narrow("minitest", "rake test 'TESTOPTS=--seed 1'", one),
        )
        self.assertEqual(
            "ruby -Ilib -e 'require \"./test/label_test\"' -- --seed 1 -n '/^(LabelTest#test_one)$/'",
            proof_stability.narrow("minitest", "ruby -Ilib -e 'require \"./test/label_test\"' -- --seed 1", one),
        )
        self.assertEqual(
            "ruby test/a_test.rb -n '/^(Pager::Query#test_ok)$/'",
            proof_stability.narrow("minitest", "ruby test/a_test.rb", ["Pager::Query::test_ok"]),
        )
        for command, tests in (
            ("rake test 'TESTOPTS=-n test_one'", one),
            ("rake test TESTOPT=-v", one),
            ("ruby -Ilib -e 'require \"./test/label_test\"' -- --name=test_one", one),
            ("ruby test/label_test.rb", ["test/label_test.rb::LabelTest::[collection]"]),
            ("ruby test/label_test.rb", ["test/label_test.rb::LabelTest"]),
            ("ruby test/label_test.rb", []),
        ):
            with self.subTest(command=command, tests=tests):
                self.assertIsNone(proof_stability.narrow("minitest", command, tests))

    def test_a_ruby_parse_check_runs_through_bundler_only_when_the_proof_does(self):
        self.assertEqual("ruby -c lib/a.rb", mutation_check._parse_command(toolchain_suite.RUBY_SUITE, "lib/a.rb"))
        self.assertEqual("bundle exec ruby -c lib/a.rb", mutation_check._parse_command("bundle exec rspec", "lib/a.rb"))
        self.assertEqual("ruby -c lib/a.rb", mutation_check._parse_command("rspec '", "lib/a.rb"))
        self.assertTrue(mutation_check._did_not_parse({"exit_code": 1, "timed_out": False}))
        for receipt in (
            {"exit_code": 0},
            {"exit_code": None, "timed_out": True},
            {"exit_code": 1, "timed_out": True},
            {"exit_code": 1, "error": "could not start"},
            {"exit_code": 1, "interrupted": True},
            None,
        ):
            with self.subTest(receipt=receipt):
                self.assertFalse(mutation_check._did_not_parse(receipt))

    def test_only_changed_lines_are_mutated_and_every_mutant_keeps_the_line_count(self):
        made, _ = text_mutants.text_mutants("x.go", GO, [6, 12], limit=100)
        self.assertEqual({6, 12}, {m.line for m in made})
        for mutant in made:
            changed = mutants.apply(GO, mutant)
            self.assertNotEqual(GO, changed)
            self.assertEqual(GO.count(b"\n"), changed.count(b"\n"))
            self.assertEqual(mutant.line, mutant.end_line)
        self.assertEqual(made, text_mutants.text_mutants("x.go", GO, [12, 6], limit=100)[0])

    def test_a_call_continuing_an_expression_is_not_dropped(self):
        source = b"func f() {\n\tx := a +\n\t\tg(b)\n\th(c)\n}\n"
        found, _ = sites("x.go", source)
        self.assertEqual([(4, "drop_call", "h(c)", "(removed)")], found)

    def test_the_limit_spreads_over_operators_and_lines(self):
        made, notes = text_mutants.text_mutants("x.go", GO, range(1, 40), limit=3)
        self.assertEqual(3, len(made))
        self.assertEqual(3, len({m.operator for m in made}))
        self.assertIn("12 mutation sites on the changed lines; 3 were chosen", notes[0])

    def test_files_it_cannot_read_safely_get_a_note_and_no_mutant(self):
        for path, source, note in (
            ("x.py", b"x = 1\n", "not a source file of a language this module mutates"),
            ("types.d.ts", b"export type A = 1 | 2;\n", "type declaration"),
            ("x.go", b"\xff\xfe\n", "not valid UTF-8"),
            ("x.js", b"a = 1 < 2;\rb = 3;\n", "carriage return"),
        ):
            with self.subTest(path=path):
                made, notes = text_mutants.text_mutants(path, source, [1])
                self.assertEqual([], made)
                self.assertIn(note, notes[0])


class SelectedResultsTests(unittest.TestCase):
    def receipt(self, passed, failed, *, exit_code=0, uncollected=()):
        tests = [*passed, *failed]
        return {
            "exit_code": exit_code,
            "timed_out": False,
            "results": {
                "passed": list(passed),
                "failed": list(failed),
                "skipped": [],
                "collection_errors": list(uncollected),
                "uncollected": list(uncollected),
                "total": len(tests),
                "complete": True,
            },
        }

    def test_a_parent_and_siblings_beside_the_selected_tests_do_not_change_the_outcome(self):
        selected = ["p::TestA/low"]
        survived = mutation_results.selected(self.receipt(["p::TestA", "p::TestA/low", "p::TestA/high"], []), selected)
        self.assertEqual(("survived", selected), mutation_results.outcome(survived, selected))
        killed = mutation_results.selected(
            self.receipt(["p::TestA/high"], ["p::TestA", "p::TestA/low"], exit_code=1), selected
        )
        self.assertEqual(("killed", selected), mutation_results.outcome(killed, selected))

    def test_a_failure_beside_passing_selected_tests_or_a_build_failure_proves_nothing(self):
        selected = ["p::TestA"]
        other = mutation_results.selected(self.receipt(["p::TestA"], ["p::TestB"], exit_code=1), selected)
        self.assertEqual(("not_run", []), mutation_results.outcome(other, selected))
        build = ["p::[build failed]"]
        broken = mutation_results.selected(self.receipt([], build, exit_code=1, uncollected=build), selected)
        self.assertEqual(("not_run", []), mutation_results.outcome(broken, selected))


class UnclosableGapRouteTests(unittest.TestCase):
    def state(self, mutation):
        return {
            "status": "RUNNING",
            "iteration": 4,
            "current_task": {"id": "fix"},
            "goal_contract": {"hash": "plan", "revision": 1, "body": {}},
            "regression_proof": {
                "verdict": "PASS",
                "mutation_check": mutation,
                "changed_code_evidence": {
                    "source_revision": "source",
                    "status": "UNVERIFIED",
                    "reason": "Changed product lines still need behavioral evidence: app.rs:3",
                },
            },
        }

    def test_a_runner_with_no_mutation_source_goes_to_the_investigator_in_the_first_round(self):
        for unavailable in (mutation_check.NO_SOURCE, mutation_check.NO_SELECTION):
            with self.subTest(unavailable=unavailable):
                state = self.state(mutation_check.record("this proof ran cargo", unavailable=unavailable))
                record = {"stage": "tester", "role": "tester", "source_revision": "source", "output": "/run/t.json"}
                self.assertTrue(rework.route(state, record, investigation_enabled=False))
                event = state["regression_proofs"][-1]
                self.assertEqual("investigator", event["action"])
                self.assertIn("this proof ran cargo", event["builder_skipped"])
                self.assertIn("No Builder change can give these lines mutation evidence", state["next_action"])
                self.assertEqual(4, state["iteration"])
                shown = str(history.project(state))
                self.assertIn("No Builder change can give these lines mutation evidence", shown)
                self.assertNotIn("sent back twice", shown)

    def test_a_collected_mutation_check_keeps_the_builder_rounds(self):
        state = self.state(mutation_check.record(None, collected=True))
        record = {"stage": "tester", "role": "tester", "source_revision": "source", "output": "/run/t.json"}
        rework.route(state, record, investigation_enabled=False)
        event = state["regression_proofs"][-1]
        self.assertEqual(("builder", 1), (event["action"], event["round"]))
        self.assertNotIn("builder_skipped", event)
        self.assertEqual(5, state["iteration"])

    def gap(self, line, outcomes=(), executed=True):
        return {
            "gap_id": f"changed-code:http2/transport.go:{line}",
            "handle": f"L{line}",
            "path": "http2/transport.go",
            "line": line,
            "status": "reason_required",
            "executed": executed,
            "mutation_outcomes": list(outcomes),
        }

    def collected(self, *gaps):
        # A Go proof whose mutation check ran: the issue's 155 executed lines had no mutant at all.
        state = self.state(mutation_check.record(None, collected=True))
        state["regression_proof"]["changed_code_evidence"]["obligations"] = [
            {**self.gap(1, ["killed"]), "status": "tested"},
            *gaps,
        ]
        return state

    def test_lines_with_no_mutation_site_go_to_the_investigator_in_the_first_round(self):
        # Executed or not, no test can give a line with no mutant behavioral evidence (#3817).
        state = self.collected(self.gap(3), self.gap(4), self.gap(9, executed=False))
        record = {"stage": "tester", "role": "tester", "source_revision": "source", "output": "/run/t.json"}
        self.assertTrue(rework.route(state, record, investigation_enabled=False))
        event = state["regression_proofs"][-1]
        self.assertEqual("investigator", event["action"])
        self.assertNotIn("round", event)
        self.assertIn("no mutation site", event["builder_skipped"])
        self.assertIn("http2/transport.go:3, http2/transport.go:4, http2/transport.go:9", event["builder_skipped"])
        self.assertEqual(4, state["iteration"])
        self.assertIn("no mutation site", str(history.project(state)))

    def test_a_line_a_test_can_settle_keeps_the_builder_round_and_names_the_others(self):
        state = self.collected(self.gap(3), self.gap(5, ["survived"]))
        record = {"stage": "tester", "role": "tester", "source_revision": "source", "output": "/run/t.json"}
        rework.route(state, record, investigation_enabled=False)
        event = state["regression_proofs"][-1]
        self.assertEqual(("builder", 1), (event["action"], event["round"]))
        self.assertNotIn("builder_skipped", event)
        self.assertIn("1 of the 2 open lines have no mutation outcome", state["next_action"])
        self.assertIn("Tester's reasons: http2/transport.go:3", state["next_action"])
        self.assertNotIn("transport.go:5", state["next_action"].split("Tester's reasons:")[1])

    def test_a_test_the_tester_asks_for_keeps_the_builder_round(self):
        rejected = [{"path": "http2/transport.go", "line": 3, "reason": "Add a test that resets the stream."}]
        for mutation in (mutation_check.record(None, collected=True), mutation_check.unsupported("cargo")):
            with self.subTest(unavailable=mutation.get("unavailable")):
                state = self.collected(self.gap(3))
                state["regression_proof"]["mutation_check"] = mutation
                record = {"stage": "tester", "role": "tester", "source_revision": "source", "output": "/run/t.json"}
                rework.route(state, record, investigation_enabled=False, rejected=rejected)
                self.assertEqual("builder", state["regression_proofs"][-1]["action"])
                self.assertIn("Add a test that resets the stream.", state["next_action"])


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


class RealRunnerMutationTests(unittest.TestCase):
    """The proof's own runner judges each mutant: a selected test that fails kills it."""

    def check(self, files, base, change, framework, command, tests, *, wrap=None):
        """``wrap(run)``, when given, returns the run callback the collector uses in place of ``run``."""
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tree"
            root.mkdir()
            for name, text in {**files, **base}.items():
                (root / name).parent.mkdir(parents=True, exist_ok=True)
                (root / name).write_text(text)
            _git(root, "init", "-q")
            _git(root, "add", "-A")
            _git(root, "commit", "-qm", "base")
            for name, text in change.items():
                (root / name).write_text(text)
            evidence = Path(directory) / "evidence"
            detected = verify.detect_framework(root)
            self.assertEqual(framework, detected.name)

            def diff(chosen):
                paths = [path for path, _ in chosen]
                return subprocess.run(
                    ["git", "diff", "-U0", "HEAD", "--", *paths], cwd=root, capture_output=True, text=True, check=True
                ).stdout

            def run(command, label, limit):
                return verify.run_suite(detected, command, root, evidence, label, timeout=limit)

            result = mutation_check.collect_selected_tests(
                framework,
                command,
                tests,
                root,
                evidence,
                [(name, "modified") for name in change],
                diff,
                wrap(run) if wrap else run,
                enforcement=True,
            )
            for name, text in change.items():
                self.assertEqual(text, (root / name).read_text(), "a mutated file is restored")
            return result

    def outcomes(self, result):
        return [(m["line"], m["operator"], m["outcome"]) for f in result["files"] for m in f["mutants"]]

    @unittest.skipUnless(shutil.which("go"), "go is not installed")
    def test_go_test_kills_a_mutant_its_selected_subtest_observes(self):
        base = {"calc.go": "package calc\n\nfunc Clamp(v, lo, hi int) int {\n\treturn v\n}\n"}
        change = {
            "calc.go": "package calc\n\nfunc Clamp(v, lo, hi int) int {\n\tif v > hi {\n\t\treturn hi\n\t}\n"
            "\treturn v\n}\n"
        }
        files = {
            "go.mod": "module example.com/calc\n\ngo 1.21\n",
            "calc_test.go": 'package calc\n\nimport "testing"\n\nfunc TestClamp(t *testing.T) {\n'
            '\tt.Run("high", func(t *testing.T) {\n\t\tif Clamp(9, 0, 5) != 5 {\n\t\t\tt.Fatal("high")\n\t\t}\n\t})\n'
            '\tt.Run("inside", func(t *testing.T) {})\n}\n',
        }
        result = self.check(files, base, change, "go", "go test ./...", ["example.com/calc::TestClamp/high"])
        self.assertTrue(result["collected"], result["note"])
        # `return hi` -> `return nil` does not build for an int: not a mutant at all, so the line has no site
        # and needs a reason, rather than a `not_run` mutant no test could ever kill.
        self.assertEqual([(4, "negate_condition", "killed"), (4, "swap_bound", "survived")], self.outcomes(result))
        self.assertEqual(
            ["calc.go:5: the constant_return mutant did not build; not a site"], result["files"][0]["notes"]
        )

    @unittest.skipUnless(shutil.which("go"), "go is not installed")
    def test_go_runs_each_mutant_instead_of_replaying_its_cached_pass(self):
        # A main package's external test builds and runs the command, so its test binary links none of the
        # mutated code and `go test` answered every mutant with the unmutated run's cached pass.
        main = (
            'package main\n\nimport (\n\t"fmt"\n\t"os"\n)\n\nfunc main() {\n\tif len(os.Args) > 1 {\n'
            '\t\tfmt.Println("args")\n\t\treturn\n\t}\n\tfmt.Println("none")\n}\n'
        )
        files = {
            "go.mod": "module example.com/cli\n\ngo 1.21\n",
            "main_test.go": 'package main_test\n\nimport (\n\t"os/exec"\n\t"path/filepath"\n\t"testing"\n)\n\n'
            'func TestCLI(t *testing.T) {\n\tbinary := filepath.Join(t.TempDir(), "cli")\n'
            '\tif out, err := exec.Command("go", "build", "-o", binary, ".").CombinedOutput(); err != nil {\n'
            '\t\tt.Fatalf("build: %v %s", err, out)\n\t}\n'
            '\tfor arg, want := range map[string]string{"": "none\\n", "x": "args\\n"} {\n'
            '\t\targs := []string{}\n\t\tif arg != "" {\n\t\t\targs = append(args, arg)\n\t\t}\n'
            "\t\tif out, _ := exec.Command(binary, args...).Output(); string(out) != want {\n"
            '\t\t\tt.Fatalf("%q: %q, want %q", arg, out, want)\n\t\t}\n\t}\n}\n',
        }
        base = {"main.go": 'package main\n\nimport "fmt"\n\nfunc main() {\n\tfmt.Println("none")\n}\n'}
        result = self.check(files, base, {"main.go": main}, "go", "go test ./...", ["example.com/cli::TestCLI"])
        self.assertTrue(result["collected"], result["note"])
        self.assertEqual(
            [(9, "negate_condition", "killed"), (9, "swap_bound", "killed"), (10, "drop_call", "killed")],
            self.outcomes(result),
        )

    def test_a_mutant_that_breaks_one_packages_build_is_not_a_site_whatever_other_packages_did(self):
        broken = "example.com/app/cmd::[build failed]"
        results = {"passed": ["example.com/app/policy::TestA"], "failed": [broken], "collection_errors": [broken]}
        self.assertTrue(mutation_check._did_not_build({"results": results}))
        for receipt in (
            {"results": {**results, "collection_errors": ["x.test.js::[collection]"]}},
            {"results": {**results, "collection_errors": []}},
            {"results": results, "timed_out": True},
            {"results": results, "error": "launch failed"},
            None,
        ):
            with self.subTest(receipt=receipt):
                self.assertFalse(mutation_check._did_not_build(receipt))

    def test_only_a_go_test_command_is_made_uncached(self):
        self.assertEqual(
            "go test -count=1 -run '^TestA$' ./...", mutation_check._uncached("go test -run '^TestA$' ./...")
        )
        self.assertEqual("cd svc && go test -count=1 ./...", mutation_check._uncached("cd svc && go test ./..."))
        for kept in ("go test -count=3 ./...", "env A=1 go test -count 2 ./...", "node --test", "go vet ./..."):
            with self.subTest(command=kept):
                self.assertEqual(kept, mutation_check._uncached(kept))

    @unittest.skipUnless(shutil.which("node"), "node is not installed")
    def test_node_test_kills_a_mutant_and_keeps_an_unobserved_one_a_survivor(self):
        base = {"lib.js": "export function label(n) {\n  return String(n);\n}\n"}
        change = {"lib.js": 'export function label(n) {\n  if (n === 1) return "one";\n  return `${n} items`;\n}\n'}
        files = {
            "package.json": '{"name": "x", "type": "module", "scripts": {"test": "node --test"}}\n',
            "lib.test.js": "import test from 'node:test';\nimport assert from 'node:assert';\n"
            "import { label } from './lib.js';\n"
            "test('labels one', () => { assert.equal(label(1), 'one'); });\n"
            "test('labels many', () => { assert.ok(label(2)); });\n",
        }
        result = self.check(files, base, change, "node", "node --test lib.test.js", ["lib.test.js::labels one"])
        self.assertTrue(result["collected"], result["note"])
        self.assertEqual(
            [(2, "negate_condition", "killed"), (2, "negate_equality", "killed"), (3, "constant_return", "survived")],
            self.outcomes(result),
        )

    @unittest.skipUnless(shutil.which("cargo"), "cargo is not installed")
    def test_cargo_rebuilds_each_mutant_and_its_selected_test_kills_it(self):
        # Cargo rebuilds a crate only when a source is newer than its last build, so a mutant given an older
        # time than that build ran the unmutated binary and survived whatever the test asserted.
        base = {"src/lib.rs": "pub fn clamp(v: i32, hi: i32) -> i32 {\n    v\n}\n"}
        change = {
            "src/lib.rs": "pub fn clamp(v: i32, hi: i32) -> i32 {\n    if v > hi {\n        return hi;\n    }\n"
            "    v\n}\n\n#[cfg(test)]\nmod tests {\n    use super::*;\n\n    #[test]\n    fn clamps_high() {\n"
            "        assert_eq!(clamp(9, 5), 5);\n    }\n\n    #[test]\n    fn keeps_inside() {}\n}\n"
        }
        files = {"Cargo.toml": '[package]\nname = "calc"\nversion = "0.1.0"\nedition = "2021"\n'}
        result = self.check(files, base, change, "cargo", "cargo test", ["src/lib.rs::tests::clamps_high"])
        self.assertTrue(result["collected"], result["note"])
        self.assertEqual(
            [(2, "negate_condition", "killed"), (2, "swap_bound", "survived"), (3, "constant_return", "killed")],
            self.outcomes(result),
        )

    @unittest.skipUnless(shutil.which("rspec"), "rspec is not installed")
    def test_rspec_kills_a_mutant_its_selected_example_observes(self):
        base = {"lib/label.rb": "def label(n)\n  n.to_s\nend\n"}
        change = {"lib/label.rb": 'def label(n)\n  return "one" if n == 1\n  "#{n} items"\nend\n'}
        files = {
            "Gemfile": 'source "https://rubygems.org"\ngem "rspec"\n',
            "spec/label_spec.rb": 'require_relative "../lib/label"\n\nRSpec.describe "label" do\n'
            '  it "names one" do\n    expect(label(1)).to eq("one")\n  end\n\n'
            '  it "names many" do\n    expect(label(2)).to be_a(String)\n  end\nend\n',
        }
        result = self.check(files, base, change, "rspec", "rspec", ["spec/label_spec.rb::label names one"])
        self.assertTrue(result["collected"], result["note"])
        self.assertEqual([(2, "negate_equality", "killed")], self.outcomes(result))

    MINITEST_BASE = {"lib/label.rb": "def label(n)\n  n.to_s\nend\n"}
    MINITEST_PARSE_CHANGE = {"lib/label.rb": 'def label(n)\n  if n > 1; return "many"; end\n  "one"\nend\n'}

    def minitest_files(self, many):
        return {
            "test/label_test.rb": 'require "minitest/autorun"\nrequire_relative "../lib/label"\n\n'
            "class LabelTest < Minitest::Test\n"
            '  def test_one\n    assert_equal "one", label(1)\n  end\n\n'
            f'  def test_many\n    assert_equal "{many}", label(2)\n  end\nend\n'
        }

    @unittest.skipUnless(shutil.which("ruby"), "ruby is not installed")
    def test_ac2_minitest_proof_is_narrowed_and_its_mutant_killed(self):
        # test_many fails on the change (label(2) is "2 items"), so only a reference run of test_one alone passes.
        change = {"lib/label.rb": 'def label(n)\n  return "one" if n == 1\n  "#{n} items"\nend\n'}
        result = self.check(
            self.minitest_files("two"),
            self.MINITEST_BASE,
            change,
            "minitest",
            toolchain_suite.RUBY_SUITE,
            ["test/label_test.rb::LabelTest::test_one"],
        )
        self.assertTrue(result["collected"], result["note"])
        self.assertEqual([(2, "negate_equality", "killed")], self.outcomes(result))
        self.assertIsNone(
            proof_stability.narrow(
                "minitest",
                "ruby -Ilib -Itest test/label_test.rb -n test_one",
                ["test/label_test.rb::LabelTest::test_one"],
            )
        )

    @unittest.skipUnless(shutil.which("ruby"), "ruby is not installed")
    def test_ac3_ruby_mutant_that_does_not_parse_is_not_a_site(self):
        result = self.check(
            self.minitest_files("many"),
            self.MINITEST_BASE,
            self.MINITEST_PARSE_CHANGE,
            "minitest",
            toolchain_suite.RUBY_SUITE,
            ["test/label_test.rb::LabelTest::test_many", "test/label_test.rb::LabelTest::test_one"],
        )
        self.assertTrue(result["collected"], result["note"])
        self.assertEqual(
            ["lib/label.rb:2: the negate_condition mutant did not parse; not a site"], result["files"][0]["notes"]
        )
        self.assertEqual([(2, "swap_bound", "killed")], self.outcomes(result))

    @unittest.skipUnless(shutil.which("ruby"), "ruby is not installed")
    def test_ac13_a_ruby_parse_check_that_times_out_leaves_the_mutant_a_site(self):
        def wrap(run):
            def timed_out_parse(command, label, limit):
                if command.startswith("ruby -c"):
                    return {"command": command, "exit_code": None, "timed_out": True, "output": None}
                return run(command, label, limit)

            return timed_out_parse

        result = self.check(
            self.minitest_files("many"),
            self.MINITEST_BASE,
            self.MINITEST_PARSE_CHANGE,
            "minitest",
            toolchain_suite.RUBY_SUITE,
            ["test/label_test.rb::LabelTest::test_many", "test/label_test.rb::LabelTest::test_one"],
            wrap=wrap,
        )
        self.assertTrue(result["collected"], result["note"])
        self.assertFalse([note for note in result["files"][0]["notes"] if "did not parse" in note])
        # The mutant that cannot load still runs, and its collection error leaves it not_run beside the kill.
        self.assertEqual([(2, "negate_condition", "not_run"), (2, "swap_bound", "killed")], self.outcomes(result))


if __name__ == "__main__":
    unittest.main()
