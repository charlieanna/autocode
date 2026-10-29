"""Report-only mutation check of named test proof (#699): the runner, its record and where it is shown.

The runner units use a fake ``run`` that executes the mutated product file in-process; one real proof
goes through regression.prove on a small project. The catalog proofs are in test_mutation_check_catalog.
"""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_changed_code_proof as changed_code_proof
import autocode_check_replay as check_replay
import autocode_diff_coverage as diff_coverage
import autocode_mutation_check as mutation_check
import autocode_python_tests as python_tests
import autocode_regression as regression
import autocode_run_view as run_view
import autocode_stage_context as stage_context
import autocode_verify as verify

from tests.test_prompt_rendering import SNAPSHOT, STATE_PATH, fixed_state

WINDOW = 'def in_window(n, lo, hi):\n    """Half-open window."""\n    if lo > hi:\n        raise ValueError("empty")\n    return lo <= n < hi\n'
DIFF = "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -2,0 +3,3 @@\n"
C1 = "tests.test_app.AppTests.test_c1_inside"
C2 = "tests.test_app.AppTests.test_c2_outside"
CASES = [{"id": "C1", "test_name": "test_c1_inside"}, {"id": "C2", "test_name": "test_c2_outside"}]
PROOF = {"fail_to_pass": [C1, C2], "pass_to_pass": ["tests.test_app.AppTests.test_other"], "not_run_on_base": []}
COMMAND = "/usr/bin/python3 -m unittest -v tests/test_app.py"
WEAK = {C1: lambda f: f(5, 0, 10) is True, C2: lambda f: f(20, 0, 10) is False}
STRONG = {
    C1: lambda f: f(0, 0, 10) is True and f(10, 0, 10) is False and f(9, 0, 10) is True,
    C2: lambda f: f(3, 3, 3) is False and _raises(lambda: f(1, 3, 2)),
}


def _raises(call):
    try:
        call()
    except ValueError:
        return True
    return False


class FakeRun:
    """Runs the 'tests' as predicates over in_window, as the tree's app.py now defines it."""

    def __init__(self, tree, tests, *, clock=None, step=0.0, outcome=None):
        self.tree, self.tests, self.clock, self.step, self.outcome = Path(tree), tests, clock, step, outcome
        self.calls = []

    def __call__(self, command, label, timeout):
        self.calls.append({"command": command, "label": label, "timeout": timeout})
        if self.clock is not None:
            self.clock.now += self.step
        if self.outcome:
            forced = self.outcome(label)
            if forced is not None:
                return forced
        namespace = {}
        exec(compile((self.tree / "app.py").read_text(), "app.py", "exec"), namespace)
        passed, failed = [], []
        for test, check in self.tests.items():
            try:
                (passed if check(namespace["in_window"]) else failed).append(test)
            except Exception:
                failed.append(test)
        results = {"complete": True, "passed": passed, "failed": failed}
        return {"exit_code": 1 if failed else 0, "timed_out": False, "duration_seconds": 0.5, "results": results}


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class RunnerTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mutation-check-")
        self.addCleanup(temporary.cleanup)
        self.tree = Path(temporary.name) / "tree"
        self.tree.mkdir()
        (self.tree / "app.py").write_text(WINDOW)
        self.evidence = Path(temporary.name) / "evidence"

    def collect(self, run, *, framework="unittest", command=COMMAND, cases=CASES, proof=PROOF, clock=None, files=None):
        return mutation_check.collect(
            framework,
            command,
            cases,
            proof,
            self.tree,
            self.evidence,
            files or [("app.py", "modified")],
            lambda files: DIFF,
            run,
            **({"clock": clock} if clock else {}),
        )

    def outcomes(self, check):
        return [
            (row["line"], row["operator"], row["outcome"], row["survived_tests"])
            for item in check["files"]
            for row in item["mutants"]
        ]

    def test_weak_proving_tests_leave_survivors_and_strong_ones_kill_every_mutant(self):
        weak = self.collect(FakeRun(self.tree, WEAK))
        self.assertTrue(weak["collected"])
        self.assertEqual(
            [
                (3, "negate_condition", "killed", []),
                (3, "swap_bound", "survived", [C1, C2]),
                (4, "drop_branch", "survived", [C1, C2]),
                (5, "swap_bound", "survived", [C1, C2]),
                (5, "swap_bound", "survived", [C1, C2]),
                (5, "constant_return", "killed", []),
            ],
            self.outcomes(weak),
        )
        self.assertEqual({"collected", "note", "limit_per_file", "budget_seconds", "files"}, set(weak))
        self.assertEqual((6, 900, None), (weak["limit_per_file"], weak["budget_seconds"], weak["note"]))
        strong = self.collect(FakeRun(self.tree, STRONG))
        self.assertEqual({"killed"}, {row[2] for row in self.outcomes(strong)})
        self.assertEqual(WINDOW, (self.tree / "app.py").read_text())

    def test_only_the_proving_tests_run_each_with_its_own_empty_bytecode_cache(self):
        run = FakeRun(self.tree, WEAK)
        self.collect(run, command="env LANG=C /usr/bin/python3 -m unittest -v tests/test_app.py")
        self.assertEqual(["unmutated", *[f"mutant-{n:02d}" for n in range(1, 7)]], [c["label"] for c in run.calls])
        caches = set()
        for call in run.calls:
            invocation = python_tests.parse(call["command"])
            self.assertIsNotNone(invocation, call["command"])
            self.assertEqual(("env", "LANG=C"), invocation.prefix[:2])
            (cache,) = [word for word in invocation.prefix if word.startswith("PYTHONPYCACHEPREFIX=")]
            caches.add(cache)
            # Never the whole suite: the two proving tests, by name; the other judged test is not run.
            self.assertEqual(("-v", C1, C2), invocation.arguments)
        self.assertEqual(len(run.calls), len(caches))

    def test_the_original_bytes_come_back_and_an_error_never_reaches_the_proof(self):
        for error in (OSError("the runner lost its log directory"), RuntimeError("unexpected")):

            def broken(label, error=error):
                if label == "mutant-02":
                    raise error
                return None

            with self.subTest(error=type(error).__name__):
                check = self.collect(FakeRun(self.tree, WEAK, outcome=broken))
                self.assertFalse(check["collected"])
                self.assertEqual(f"the mutation check stopped: {type(error).__name__}: {error}", check["note"])
                self.assertEqual(WINDOW, (self.tree / "app.py").read_text())
        # Before any mutant: choosing the proving tests fails, and the proof still never sees it.
        with mock.patch.object(mutation_check.proof_stability, "proving_tests", side_effect=KeyError("id")):
            run = FakeRun(self.tree, WEAK)
            check = self.collect(run)
        self.assertEqual([], run.calls)
        self.assertEqual("the mutation check stopped: KeyError: 'id'", check["note"])

    def test_one_budget_covers_every_mutant_and_a_mutant_that_hangs_cannot_spend_it_all(self):
        clock = Clock()
        run = FakeRun(self.tree, WEAK, clock=clock, step=400)
        check = self.collect(run, clock=clock)
        # The unmutated run takes 400 s of the 900, two mutants the rest; the others never start.
        self.assertEqual(["unmutated", "mutant-01", "mutant-02"], [call["label"] for call in run.calls])
        self.assertEqual([900, 60, 60], [call["timeout"] for call in run.calls])
        self.assertEqual(["not_run"] * 4, [row[2] for row in self.outcomes(check)][2:])
        self.assertEqual("the 900-second budget ran out with 4 of 6 mutants not run", check["note"])

    def test_no_mutant_starts_without_the_floor_left_and_one_the_budget_cut_short_is_not_run(self):
        passed = {
            "exit_code": 0,
            "timed_out": False,
            "duration_seconds": 10,
            "results": {"complete": True, "passed": [C1, C2], "failed": []},
        }
        # The unmutated run took 10 s, so each mutant may take 100 s; a run that hangs takes its whole limit.
        for spent, timeouts, outcomes, unrun in (
            (780, [900, 100], ["timeout"] + ["not_run"] * 5, 5),  # then 20 s are left: under the 60 s floor
            (820, [900, 80], ["not_run"] * 6, 6),  # 80 s left: the budget, not the mutant, stops it
        ):
            clock = Clock()

            def hang(label, spent=spent, clock=clock):
                if label == "unmutated":
                    clock.now += spent
                    return passed
                clock.now += run.calls[-1]["timeout"]
                return {
                    "exit_code": None,
                    "timed_out": True,
                    "duration_seconds": run.calls[-1]["timeout"],
                    "results": None,
                }

            with self.subTest(spent=spent):
                run = FakeRun(self.tree, WEAK, outcome=hang)
                check = self.collect(run, clock=clock)
                self.assertEqual(timeouts, [call["timeout"] for call in run.calls])
                self.assertEqual(outcomes, [row[2] for row in self.outcomes(check)])
                self.assertEqual(f"the 900-second budget ran out with {unrun} of 6 mutants not run", check["note"])

    def test_a_file_that_cannot_be_mutated_is_noted_and_the_other_files_still_run(self):
        (self.tree / "big.py").write_text("x = 1\n")
        real = mutation_check.mutants.python_mutants

        def python_mutants(path, *args, **options):
            if path == "big.py":
                raise RecursionError("maximum recursion depth exceeded")
            return real(path, *args, **options)

        with mock.patch.object(mutation_check.mutants, "python_mutants", side_effect=python_mutants):
            check = self.collect(FakeRun(self.tree, WEAK), files=[("big.py", "modified"), ("app.py", "modified")])
        self.assertTrue(check["collected"])
        self.assertEqual(
            ["big.py: not mutated: RecursionError: maximum recursion depth exceeded"], check["files"][0]["notes"]
        )
        self.assertEqual(6, len(check["files"][1]["mutants"]))

    def test_a_timed_out_or_unreported_mutant_is_never_a_survivor(self):
        def hang(label):
            if label == "mutant-02":
                return {"exit_code": None, "timed_out": True, "duration_seconds": 60, "results": None}
            if label == "mutant-03":
                return {"exit_code": 0, "timed_out": False, "results": {"complete": False, "passed": [C1]}}
            return None

        check = self.collect(FakeRun(self.tree, WEAK, outcome=hang))
        self.assertEqual(["timeout", "not_run"], [row[2] for row in self.outcomes(check)][1:3])

    def test_nothing_runs_without_a_python_proof_whose_tests_can_be_selected(self):
        cases = {
            "go": ({"framework": "go", "command": "go test ./..."}, "Python unittest and pytest proofs only"),
            "shell": ({"command": COMMAND + " | tee log"}, "cannot select the proving tests alone"),
            "unnamed": ({"cases": [{"id": "C9", "test_name": "test_c9_missing"}]}, "no judged test is named"),
        }
        for name, (options, note) in cases.items():
            with self.subTest(case=name):
                run = FakeRun(self.tree, WEAK)
                check = self.collect(run, **options)
                self.assertEqual([], run.calls)
                self.assertFalse(check["collected"])
                self.assertIn(note, check["note"])

    def test_no_mutant_runs_when_the_proving_tests_fail_unmutated(self):
        run = FakeRun(self.tree, {C1: lambda f: False, C2: lambda f: True})
        check = self.collect(run)
        self.assertEqual(["unmutated"], [call["label"] for call in run.calls])
        self.assertIn("did not pass unmutated", check["note"])
        self.assertFalse(check["collected"])

    def test_a_symlinked_product_file_is_never_written_through(self):
        outside = self.tree.parent / "outside.py"
        outside.write_text(WINDOW)
        (self.tree / "app.py").unlink()
        (self.tree / "app.py").symlink_to(outside)
        run = FakeRun(self.tree, WEAK)
        check = self.collect(run)
        self.assertEqual([], run.calls)
        self.assertEqual(["app.py: not a regular file inside the proof tree; not mutated"], check["files"][0]["notes"])
        self.assertIn("no changed product line has a mutation site", check["note"])

    def test_the_budget_is_the_check_replay_limit(self):
        self.assertEqual(check_replay.TIMEOUT_SECONDS, mutation_check.BUDGET_SECONDS)


LIMIT_FIVE = "def limit(n):\n    if n > 1:\n        return 5\n    return 0\n"
ZERO = "tests.test_app.AppTests.test_c1_zero_is_zero"
EPOCH_2020 = 1_577_836_800


class StaleBytecodeTests(unittest.TestCase):
    """A proving test may import the product in a Python of its own, started with a minimal environment: it
    reads and writes the tree's own __pycache__, which trusts a compile whose source has the same
    modification time (whole seconds) and size."""

    def test_each_mutant_runs_its_own_code_when_two_are_written_in_the_same_second(self):
        temporary = tempfile.TemporaryDirectory(prefix="mutation-pyc-")
        self.addCleanup(temporary.cleanup)
        tree = Path(temporary.name)
        app = tree / "app.py"
        app.write_text(LIMIT_FIVE)
        os.utime(app, (EPOCH_2020, EPOCH_2020))
        original = app.stat().st_mtime_ns

        def run(command, label, timeout):
            # Writes in quick succession share a wall-clock second; pin any write the check left at "now" to one.
            if app.stat().st_mtime > EPOCH_2020:
                os.utime(app, (EPOCH_2020 + 3600, EPOCH_2020 + 3600))
            shown = subprocess.run(
                [sys.executable, "-c", "import app; print(app.limit(0))"],
                cwd=tree,
                env={"PATH": os.environ.get("PATH", "")},
                capture_output=True,
                text=True,
            ).stdout.strip()
            ok = shown == "0"
            results = {"complete": True, "passed": [ZERO] if ok else [], "failed": [] if ok else [ZERO]}
            return {"exit_code": 0 if ok else 1, "timed_out": False, "duration_seconds": 0.1, "results": results}

        check = mutation_check.collect(
            "unittest",
            COMMAND,
            [{"id": "C1", "test_name": "test_c1_zero_is_zero"}],
            {"fail_to_pass": [ZERO], "pass_to_pass": [], "not_run_on_base": []},
            tree,
            tree.parent / "evidence",
            [("app.py", "modified")],
            lambda files: "diff --git a/app.py b/app.py\n--- a/app.py\n+++ b/app.py\n@@ -2,0 +2,3 @@\n",
            run,
        )
        shown = [(row["line"], row["before"], row["after"], row["outcome"]) for row in check["files"][0]["mutants"]]
        self.assertEqual(
            [
                (2, "n > 1", "not (n > 1)", "killed"),
                (2, ">", ">=", "survived"),
                (2, "1", "2", "survived"),
                (3, "return 5", "pass", "survived"),
                (3, "5", "0", "survived"),
                # Same size as the mutant before it: without its own modification time, it ran that one's compile.
                (4, "0", "1", "killed"),
            ],
            shown,
        )
        self.assertTrue((tree / "__pycache__").is_dir())  # the proving test's own Python did write a compile there
        self.assertEqual((LIMIT_FIVE, original), (app.read_text(), app.stat().st_mtime_ns))


class FinalRecordTests(unittest.TestCase):
    CHECK = {
        **mutation_check.record(collected=True),
        "files": [
            {
                "path": "app.py",
                "notes": [],
                "mutants": [
                    {
                        "line": 3,
                        "operator": "negate_condition",
                        "before": "lo > hi",
                        "after": "not (lo > hi)",
                        "outcome": "killed",
                        "survived_tests": [],
                    },
                    {
                        "line": 5,
                        "operator": "swap_bound",
                        "before": "<",
                        "after": "<=",
                        "outcome": "survived",
                        "survived_tests": [C2],
                    },
                    {
                        "line": 5,
                        "operator": "constant_return",
                        "before": "x",
                        "after": "None",
                        "outcome": "timeout",
                        "survived_tests": [],
                    },
                ],
            }
        ],
    }

    def test_survivors_are_named_per_criterion_through_the_final_case_tests(self):
        final = mutation_check.final(self.CHECK, "PASS", {"C1": [C1], "C2": [C2]})
        self.assertEqual(
            {
                "C2": [
                    {
                        "test": C2,
                        "path": "app.py",
                        "line": 5,
                        "operator": "swap_bound",
                        "before": "<",
                        "after": "<=",
                        "text": "test_c2_outside passes with app.py:5 changed (`<` -> `<=`)",
                    }
                ]
            },
            final["survivors"],
        )
        self.assertEqual(self.CHECK["files"], final["files"])
        killed = mutation_check.final(self.CHECK, "PASS", {"C1": [C1]})
        self.assertEqual({}, killed["survivors"])

    def test_a_proof_that_did_not_pass_reports_no_mutant(self):
        for verdict in ("FAIL", "UNVERIFIED"):
            with self.subTest(verdict=verdict):
                final = mutation_check.final(self.CHECK, verdict, {"C2": [C2]})
                self.assertEqual(mutation_check.record("not run: the proof did not pass"), final)
                self.assertNotIn("survivors", final)
        self.assertIsNone(mutation_check.final(None, "PASS", {}))
        skipped = mutation_check.record("this runner cannot select the proving tests alone")
        self.assertEqual(skipped, mutation_check.final(skipped, "PASS", {"C2": [C2]}))


SURVIVOR = {
    "test": C2,
    "path": "app.py",
    "line": 5,
    "operator": "swap_bound",
    "before": "<",
    "after": "<=",
    "text": "test_c2_outside passes with app.py:5 changed (`<` -> `<=`)",
}


class HandoffTests(unittest.TestCase):
    """The Tester and Completion Reviewer see the record, and a note only when a mutant survived."""

    def state(self, survivors):
        state = fixed_state()
        state["goal_contract"] = {"body": {"task_kind": "bugfix"}}
        state["regression_proof"] = {
            "verdict": "PASS",
            "case_tests": {"C2": [C2]},
            "mutation_check": {**mutation_check.record(collected=True), "survivors": survivors},
        }
        return state

    def prompt(self, state, stage):
        with mock.patch.object(stage_context.source_scope, "snapshot", return_value=SNAPSHOT):
            return stage_context.context_packet(state, stage, STATE_PATH)[0]

    def test_the_note_appears_only_when_a_mutant_survived(self):
        self.assertIn("mutation_check", regression.SUMMARY_KEYS)
        for stage in ("sol", "astra_checkpoint", "astra_review"):
            with self.subTest(stage=stage):
                shown = self.prompt(self.state({"C2": [SURVIVOR]}), stage)
                self.assertEqual(1, shown.count("MUTATION CHECK:"))
                self.assertIn("Do not FAIL, REWORK or raise blocking findings on this alone.", shown)
                data = json.loads(shown.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual({"C2": [SURVIVOR]}, data["regression_proof"]["mutation_check"]["survivors"])
                self.assertNotIn("MUTATION CHECK:", self.prompt(self.state({}), stage))
        self.assertEqual("", regression.mutation_note({"mutation_check": None}))
        self.assertEqual("", regression.mutation_note(None))

    def test_the_status_view_shows_the_record_beside_diff_coverage(self):
        state = self.state({"C2": [SURVIVOR]})
        shown = run_view.evidence(state)
        self.assertEqual(state["regression_proof"]["mutation_check"], shown["mutation_check"])
        self.assertIsNot(state["regression_proof"]["mutation_check"], shown["mutation_check"])
        self.assertNotIn("mutation_check", shown["regression_proof"])


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


LIMIT = "def limit(n):\n    return n\n"
CAPPED = "def limit(n):\n    if n > 10:\n        return 10\n    return n\n"
WEAK_TEST = (
    "import unittest\n\nfrom calc import limit\n\n\nclass LimitTests(unittest.TestCase):\n"
    "    def test_t1_caps_large_values(self):\n        self.assertEqual(10, limit(50))\n"
)


SMALL_TEST = (
    "import unittest\n\nfrom calc import limit\n\n\nclass SmallTests(unittest.TestCase):\n"
    "    def test_small_values_pass_through(self):\n        self.assertEqual(3, limit(3))\n"
)


class ProofTests(unittest.TestCase):
    """Through the public entry point: a real proof, its saved record and its receipts."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="mutation-proof-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name) / "project"
        (self.root / "tests").mkdir(parents=True)
        (self.root / "calc.py").write_text(LIMIT)
        (self.root / "tests" / "__init__.py").write_text("")
        (self.root / "tests" / "test_small.py").write_text(SMALL_TEST)
        git(self.root, "init", "-q")
        git(self.root, "config", "maintenance.auto", "false")
        git(self.root, "config", "gc.auto", "0")
        git(self.root, "add", "-A")
        git(self.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        (self.root / "calc.py").write_text(CAPPED)
        (self.root / "tests" / "test_calc.py").write_text(WEAK_TEST)
        self.run_dir = Path(temporary.name) / "run"
        case = {"id": "T1", "given": "n=50", "when": "limit(50)", "then": "returns 10"}
        self.state = {
            "base_commit": git(self.root, "rev-parse", "HEAD"),
            "settings": {},
            "iteration": 1,
            "stages": [],
            "history": [],
            "goal_contract": {"body": {"task_kind": "bugfix", "acceptance_criteria": [], "milestones": [{"id": "M1"}]}},
            "investigation": {"outcome": "reproduced", "test_cases": [case]},
        }

    def test_generated_test_output_does_not_invalidate_unchanged_product_inputs(self):
        self.state["investigation"]["test_cases"][0]["then"] = "returns 9"
        source = "def limit(n):\n    if n > 10:\n        return 9\n    return n\n"
        (self.root / "calc.py").write_text(source)
        test = WEAK_TEST.replace("self.assertEqual(10, limit(50))", "self.assertEqual(9, limit(50))")
        test += "        self.assertEqual(9, limit(11))\n        self.assertEqual(10, limit(10))\n"
        test += '        with open("suite-result.json", "w") as stream:\n            stream.write("generated report")\n'
        (self.root / "tests/test_calc.py").write_text(test)
        proof = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual("PASS", proof["changed_code_evidence"]["status"], proof["changed_code_evidence"])
        self.assertEqual(source, (self.root / "calc.py").read_text())
        self.assertFalse((self.root / "suite-result.json").exists(), "checks stay in owned scratch trees")

    def test_test_writes_cannot_change_a_delivered_input_and_keep_line_authority(self):
        self.state["investigation"]["test_cases"][0]["then"] = "returns 9"
        (self.root / "fixture-input.json").write_text("original input")
        (self.root / "calc.py").write_text("def limit(n):\n    if n > 10:\n        return 9\n    return n\n")
        test = WEAK_TEST.replace("self.assertEqual(10, limit(50))", "self.assertEqual(9, limit(50))")
        test += "        self.assertEqual(9, limit(11))\n        self.assertEqual(10, limit(10))\n"
        test += '        with open("fixture-input.json", "w") as stream:\n            stream.write("changed input")\n'
        (self.root / "tests/test_calc.py").write_text(test)
        proof = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual("UNVERIFIED", proof["changed_code_evidence"]["status"])
        self.assertIsNone(proof["changed_code_inputs"]["inventory"])
        self.assertIn("fixture-input.json (after checks)", proof["changed_code_inputs"]["custody_gaps"])
        reassessed = changed_code_proof.reviewed(proof, [])
        self.assertEqual("UNVERIFIED", reassessed["status"])
        self.assertIn("fixture-input.json (after checks)", reassessed["reason"])
        self.assertEqual("original input", (self.root / "fixture-input.json").read_text())

    def test_a_weak_proving_test_keeps_its_pass_and_survivors_but_cannot_complete(self):
        before = (self.root / "calc.py").read_bytes()
        proof = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        test = "tests.test_calc.LimitTests.test_t1_caps_large_values"
        self.assertEqual({"T1": [test]}, proof["case_tests"])
        texts = [row["text"] for row in proof["mutation_check"]["survivors"]["T1"]]
        self.assertEqual(
            [
                "test_t1_caps_large_values passes with calc.py:2 changed (`>` -> `>=`)",
                "test_t1_caps_large_values passes with calc.py:2 changed (`10` -> `11`)",
            ],
            texts,
        )
        # Mutation captures stay separate from the named regression checks; the workspace never changed.
        self.assertFalse([label for label in proof["checks"] if "mutant" in label or "unmutated" in label])
        saved = json.loads(Path(proof["path"]).read_text())
        self.assertEqual(proof["mutation_check"], saved["mutation_check"])
        self.assertFalse([label for label in saved["checks"] if "mutant" in label or "unmutated" in label])
        changed = proof["changed_code_evidence"]
        self.assertEqual("UNVERIFIED", changed["status"])
        gaps = [row for row in changed["obligations"] if row["status"] == "reason_required"]
        self.assertEqual([("calc.py", 2)], [(row["path"], row["line"]) for row in gaps])
        survivors = [
            (file["path"], row)
            for file in proof["changed_code_inputs"]["mutation"]["files"]
            for row in file["mutants"]
            if row["outcome"] == "survived"
        ]
        self.assertEqual(
            {("calc.py", 2, "swap_bound"), ("calc.py", 2, "shift_bound")},
            {(path, row["line"], row["operator"]) for path, row in survivors},
        )
        for _, row in survivors:
            self.assertTrue(row["evidence"], row)
            self.assertTrue(set(row["evidence"]) <= set(gaps[0]["evidence"]))
            self.assertTrue(all(Path(ref).is_file() for ref in row["evidence"]), row)
        self.assertFalse(regression.complete(self.state, proof["source_revision"], all_due=True))
        self.assertEqual(before, (self.root / "calc.py").read_bytes())
        logs = sorted(path.name for path in Path(proof["path"]).parent.joinpath("mutation-check").glob("*.log"))
        # The unmutated run and five mutants: `return n` (line 4) is unchanged from the base, so never mutated.
        self.assertEqual(6, len(logs), logs)
        self.assertEqual(5, len(proof["mutation_check"]["files"][0]["mutants"]))
        # The candidate tree the mutants ran in is gone, and so is its worktree.
        self.assertFalse(Path(proof["path"]).parent.joinpath("scratch", "candidate").exists())
        self.assertEqual(1, git(self.root, "worktree", "list", "--porcelain").count("worktree "))

    def test_a_proof_that_fails_its_cases_runs_no_mutant(self):
        # T2 has no test: verify() alone passes, the case check fails the proof, and no mutant may run first.
        self.state["investigation"]["test_cases"].append({"id": "T2", "given": "n=0", "when": "limit(0)", "then": "0"})
        proof = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(verify.FAIL, proof["verdict"])
        self.assertEqual(mutation_check.record(mutation_check.NOT_PASSED), proof["mutation_check"])
        self.assertFalse(Path(proof["path"]).parent.joinpath("mutation-check").exists())
        self.assertEqual(1, git(self.root, "worktree", "list", "--porcelain").count("worktree "))

    def test_a_proof_refused_before_any_test_ran_carries_the_not_passed_note(self):
        del self.state["base_commit"]
        proof = regression.prove(self.state, self.root, self.run_dir)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"])
        self.assertIsNone(proof["path"])
        self.assertEqual(mutation_check.record(mutation_check.NOT_PASSED), proof["mutation_check"])

    def test_a_file_the_checkout_never_tracked_is_mutated_on_the_lines_that_changed(self):
        # An in-place run's base commit holds the files that were untracked at launch (commit_worktree).
        temporary = tempfile.TemporaryDirectory(prefix="mutation-untracked-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name) / "project"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "__init__.py").write_text("")
        (root / "tests" / "test_small.py").write_text(SMALL_TEST)
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        (root / "calc.py").write_text(LIMIT)
        base = verify.commit_worktree(root)
        (root / "calc.py").write_text(CAPPED)
        (root / "tests" / "test_calc.py").write_text(WEAK_TEST)
        self.assertEqual("?? calc.py", git(root, "status", "--porcelain", "--", "calc.py"))
        self.assertEqual("modified", verify.changed_files(root, base)["calc.py"])
        self.assertEqual(
            [{"path": "calc.py", "hunks": [{"start": 2, "end": 3}]}],
            diff_coverage.hunks(verify.product_diff(root, base, [("calc.py", "modified")])),
        )
        proof = regression.prove({**self.state, "base_commit": base}, root, Path(temporary.name) / "run")
        self.assertEqual(verify.PASS, proof["verdict"], proof["failures"] + proof["unverified"])
        self.assertEqual(
            [("calc.py", 2, "swap_bound"), ("calc.py", 2, "shift_bound")],
            [(row["path"], row["line"], row["operator"]) for row in proof["mutation_check"]["survivors"]["T1"]],
        )


class DiffTests(unittest.TestCase):
    """The changed lines come from a zero-context diff that the person's git config cannot widen or color."""

    def test_git_config_cannot_merge_hunks_color_them_or_run_a_driver(self):
        temporary = tempfile.TemporaryDirectory(prefix="mutation-diff-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        source = "def f(n):\n    a = n + 10\n    if n < 5:\n        return 1\n    b = n + 20\n"
        (root / "app.py").write_text(source)
        git(root, "init", "-q")
        git(root, "add", "-A")
        git(root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "seed")
        for key, value in (
            ("diff.interHunkContext", "3"),
            ("color.ui", "always"),
            ("color.diff", "always"),
            ("diff.external", "false"),
            ("diff.mnemonicPrefix", "true"),
        ):
            git(root, "config", key, value)
        (root / "app.py").write_text(source.replace("n + 10", "n + 11").replace("n + 20", "n + 21"))
        (root / "new.py").write_text("x = 1\n")
        base = git(root, "rev-parse", "HEAD")
        self.enterContext(mock.patch.dict(os.environ, {"GIT_DIFF_OPTS": "--unified=3"}))
        self.assertEqual(
            [
                {"path": "app.py", "hunks": [{"start": 2, "end": 2}, {"start": 5, "end": 5}]},
                {"path": "new.py", "hunks": [{"start": 1, "end": 1}]},
            ],
            diff_coverage.hunks(verify.product_diff(root, base, [("app.py", "modified"), ("new.py", "added")])),
        )


if __name__ == "__main__":
    unittest.main()
