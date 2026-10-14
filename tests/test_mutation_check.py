"""Report-only mutation check of named test proof (#699): the runner, its record and where it is shown.

The runner units use a fake ``run`` that executes the mutated product file in-process; one real proof
goes through regression.prove on a small project. The catalog proofs are in test_mutation_check_catalog.
"""

import json
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_check_replay as check_replay
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

    def collect(self, run, *, framework="unittest", command=COMMAND, cases=CASES, proof=PROOF, clock=None):
        return mutation_check.collect(
            framework,
            command,
            cases,
            proof,
            self.tree,
            self.evidence,
            [("app.py", "modified")],
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

    def test_a_weak_proving_test_passes_the_proof_and_its_survivors_are_reported_per_case(self):
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
        # Report only: the mutant runs are not receipts of the proof, and the workspace never changed.
        self.assertFalse([label for label in proof["checks"] if "mutant" in label or "unmutated" in label])
        saved = json.loads(Path(proof["path"]).read_text())
        self.assertEqual(proof["mutation_check"], saved["mutation_check"])
        self.assertFalse([label for label in saved["checks"] if "mutant" in label or "unmutated" in label])
        self.assertTrue(regression.complete(self.state, proof["source_revision"], all_due=True))
        self.assertEqual(before, (self.root / "calc.py").read_bytes())
        logs = sorted(path.name for path in Path(proof["path"]).parent.joinpath("mutation-check").glob("*.log"))
        # The unmutated run and five mutants: `return n` (line 4) is unchanged from the base, so never mutated.
        self.assertEqual(6, len(logs), logs)
        self.assertEqual(5, len(proof["mutation_check"]["files"][0]["mutants"]))


if __name__ == "__main__":
    unittest.main()
