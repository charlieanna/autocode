"""Mutation evidence for Go, node:test and JavaScript runners (#3817).

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
import autocode_recovery_history as history
import autocode_text_mutants as text_mutants
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
            ("x.py", b"x = 1\n", "not a Go, JavaScript or TypeScript source file"),
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


def _git(root, *args):
    subprocess.run(
        ["git", "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        cwd=root,
        check=True,
        capture_output=True,
    )


class RealRunnerMutationTests(unittest.TestCase):
    """The proof's own runner judges each mutant: a selected test that fails kills it."""

    def check(self, files, base, change, framework, command, tests):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "tree"
            root.mkdir()
            for name, text in {**files, **base}.items():
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
                run,
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
            "go test -count=1 -run '^TestA$' ./...", mutation_check._uncached("go test -run '^TestA$' ./...", "c")
        )
        self.assertEqual("cd svc && go test -count=1 ./...", mutation_check._uncached("cd svc && go test ./...", "c"))
        for kept in ("go test -count=3 ./...", "env A=1 go test -count 2 ./...", "node --test", "go vet ./..."):
            with self.subTest(command=kept):
                self.assertEqual(kept, mutation_check._uncached(kept, "c"))

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


if __name__ == "__main__":
    unittest.main()
