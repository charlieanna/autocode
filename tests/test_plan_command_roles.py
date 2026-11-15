"""Which of the approved plan's commands runs a project's tests, found by running them.

A project whose runner AutoCode has no detector for was proven only by a plan command that began with a
listed program and named a test file. ``sh run_tests.sh`` and ``make tests`` name none: two real bug fixes
in C (2026-11-15) ended by asking the person to "Set --test-command 'sh run_tests.sh' and
--regression-command 'sh run_tests.sh'". The owner asked for shared causes to be fixed once (AGENTS.md),
and a detector had been added for each runner a run stopped on.

The settling cases use a scripted ``observe``. The proof cases use a shell-script project, which no runner
detects, and run real commands; no provider runs.
tools/autocode_plan_command_roles.py
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_goal_lifecycle as lifecycle
import autocode_plan_command_roles as roles
import autocode_protected_oracles as guard
import autocode_regression as regression
import autocode_util as util
import autocode_verify as verify

from . import test_plan_test_suite as plan_tests
from .test_verify import Project

BUG = "page_count() { echo $(( $1 / $2 )); }\n"
FIXED = "page_count() { echo $(( ($1 + $2 - 1) / $2 )); }\n"
REFACTORED = 'round_up() { echo $(( ($1 + $2 - 1) / $2 )); }\npage_count() { round_up "$1" "$2"; }\n'
CHECK = '. ./pager.sh\n[ "$(page_count 10 5)" = 2 ] || { echo "exact multiple"; exit 1; }\n'
PARTIAL = '[ "$(page_count 11 5)" = 3 ] || { echo "partial last page"; exit 1; }\n'
# The project's own way to run its tests: a script that names no test file.
RUNNER = '#!/bin/sh\nset -e\ncd "$(dirname "$0")"\nfor file in tests/test_*.sh; do sh "$file"; done\n'
SEED = {"pager.sh": BUG, "tests/test_pager.sh": CHECK, "run_tests.sh": RUNNER}
CHANGE = {"pager.sh": FIXED, "tests/test_pager.sh": CHECK + PARTIAL}
FLIPS = "run `{}`: it fails on the original code and passes with the fix"
ORIGINAL, WITH_TESTS, WITHOUT_TESTS = roles.ORIGINAL, roles.WITH_TESTS, roles.WITHOUT_TESTS


def contract(*methods, kind="bugfix"):
    rows = [{"id": f"AC{n}", "criterion": "pages", "verification_method": m} for n, m in enumerate(methods, 1)]
    sealed = {"task_id": "task-1", "revision": 1, "body": {"task_kind": kind, "acceptance_criteria": rows}}
    return {**sealed, "hash": util.digest(sealed), "approval_status": "approved"}


def project(case, seed=SEED, executable=("run_tests.sh",)):
    """A committed project; ``executable`` files may be run by their path, as a checkout's are."""
    made = Project(seed)
    case.addCleanup(made.close)
    for name in executable:
        if (made.root / name).is_file():
            (made.root / name).chmod(0o755)
    subprocess.run(["git", "-C", str(made.root), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(made.root), "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "modes"],
        capture_output=True,
    )
    made.base = subprocess.run(
        ["git", "-C", str(made.root), "rev-parse", "HEAD"], check=True, capture_output=True, text=True
    ).stdout.strip()
    return made


def run_state(made, goal_contract):
    return {
        "workspace": str(made.root),
        "base_commit": made.base,
        "iteration": 1,
        "stages": [],
        "history": [],
        "settings": {"regression": {}},
        "goal_contract": goal_contract,
    }


def prove(case, goal_contract, change, *, seed=SEED, state=None):
    made = project(case, seed)
    made.write(change)
    state = state if state is not None else {}
    state.update(run_state(made, goal_contract))
    with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
        return regression.prove(state, made.root, made.root.parent / "run")


class Seen:
    """A scripted ``observe``: the exit code of each command on each tree, and the runs it was asked for."""

    def __init__(self, **codes):
        self.codes, self.asked = codes, []

    def __call__(self, command, where):
        self.asked.append((command, where))
        return self.codes.get(command.split()[0].replace("./", "").replace(".", "_"), {}).get(where, 0)


class SettleTests(unittest.TestCase):
    """What ``settle`` takes from what a command does, with a scripted ``observe``."""

    TESTS = {"original_tests": ["tests/test_pager.sh"], "changed_tests": ["tests/test_pager.sh"]}

    def setUp(self):
        self.tree = Path(tempfile.mkdtemp(prefix="roles-"))
        self.addCleanup(shutil.rmtree, self.tree, True)
        for name in ("run_tests.sh", "lint.sh", "build.sh"):
            (self.tree / name).write_text("#!/bin/sh\n")
            (self.tree / name).chmod(0o755)

    def settle(self, state, goal_contract, seen, **options):
        tests = {**self.TESTS, **{key: options.pop(key) for key in list(options) if key in self.TESTS}}
        roles.settle(state, "base", goal_contract, **tests, tree=self.tree, observe=seen, **options)
        return roles.adopted(state, "base", goal_contract, **tests)

    def test_a_command_that_passes_on_the_original_code_and_fails_with_the_changes_tests_takes_both_roles(self):
        seen = Seen(run_tests_sh={WITH_TESTS: 1})
        chosen = self.settle({}, contract(FLIPS.format("./run_tests.sh")), seen)
        self.assertEqual(("./run_tests.sh", "./run_tests.sh"), (chosen["suite"], chosen["regression"]))
        self.assertEqual({"./run_tests.sh": WITH_TESTS}, chosen["observed"])
        self.assertEqual([("./run_tests.sh", ORIGINAL), ("./run_tests.sh", WITH_TESTS)], seen.asked)
        self.assertIn(
            "`./run_tests.sh` passes on the original code and fails there with the change's tests, so it runs them",
            roles.note(chosen),
        )

    def test_a_plan_the_rules_understand_costs_no_run(self):
        seen = Seen()
        chosen = self.settle({}, contract("`sh tests/test_pager.sh` fails on the original code"), seen)
        self.assertEqual(("sh tests/test_pager.sh", "sh tests/test_pager.sh"), (chosen["suite"], chosen["regression"]))
        self.assertEqual(([], False), (seen.asked, "observed" in chosen))
        self.assertIsNone(roles.note(chosen))
        self.assertFalse(roles.wanted({}, "base", contract("`sh tests/test_pager.sh` exits 0"), **self.TESTS))

    def test_a_command_that_fails_on_the_original_code_takes_no_role(self):
        seen = Seen(run_tests_sh={ORIGINAL: 2, WITH_TESTS: 2})
        state = {}
        self.assertIsNone(self.settle(state, contract(FLIPS.format("./run_tests.sh")), seen))
        self.assertEqual([("./run_tests.sh", ORIGINAL)], seen.asked)
        said = roles.unsettled(state, "base", contract(FLIPS.format("./run_tests.sh")), self.tree)
        self.assertIn("none could be used: `./run_tests.sh` exits 2 on the original code", said)

    def test_a_command_that_does_not_notice_the_tests_takes_no_role(self):
        # It passes whatever the tests are: a build, a linter, `true`.
        seen = Seen()
        state = {}
        self.assertIsNone(self.settle(state, contract(FLIPS.format("./lint.sh")), seen))
        self.assertEqual([("./lint.sh", ORIGINAL), ("./lint.sh", WITH_TESTS), ("./lint.sh", WITHOUT_TESTS)], seen.asked)
        said = roles.unsettled(state, "base", contract(FLIPS.format("./lint.sh")), self.tree)
        self.assertIn(
            "`./lint.sh` passes on the original code, and still passes with the change's tests, and still passes "
            "with its test files made unloadable",
            said,
        )

    def test_a_command_that_runs_only_the_existing_tests_is_the_suite(self):
        # The change's new test is in a file the command does not run: it cannot be the regression command.
        seen = Seen(run_tests_sh={WITHOUT_TESTS: 2})
        chosen = self.settle({}, contract(FLIPS.format("./run_tests.sh")), seen)
        self.assertEqual(("./run_tests.sh", None), (chosen["suite"], chosen["regression"]))
        self.assertEqual({"./run_tests.sh": WITHOUT_TESTS}, chosen["observed"])
        self.assertIn("fails there when the project's test files cannot be loaded", roles.note(chosen))

    def test_the_plans_order_decides_among_commands_and_the_first_that_runs_the_tests_is_taken(self):
        plan = contract(*(FLIPS.format(name) for name in ("./build.sh", "./lint.sh", "./run_tests.sh")))
        seen = Seen(build_sh={ORIGINAL: 1}, run_tests_sh={WITH_TESTS: 1})
        chosen = self.settle({}, plan, seen)
        self.assertEqual("./run_tests.sh", chosen["regression"])
        self.assertEqual(
            ["./build.sh", "./lint.sh", "./lint.sh", "./lint.sh", "./run_tests.sh", "./run_tests.sh"],
            [c for c, _ in seen.asked],
        )

    def test_one_settling_looks_at_a_bounded_number_of_commands(self):
        for number in range(roles.MAX_COMMANDS + 2):
            (self.tree / f"check{number}.sh").write_text("#!/bin/sh\n")
            (self.tree / f"check{number}.sh").chmod(0o755)
        plan = contract(*(FLIPS.format(f"./check{number}.sh") for number in range(roles.MAX_COMMANDS + 2)))
        seen = Seen()
        self.assertIsNone(self.settle({}, plan, seen))
        self.assertEqual(roles.MAX_COMMANDS, len({command for command, _ in seen.asked}))

    def test_a_run_that_did_not_finish_gives_no_role(self):
        seen = Seen(run_tests_sh={WITH_TESTS: None, WITHOUT_TESTS: None})
        self.assertIsNone(self.settle({}, contract(FLIPS.format("./run_tests.sh")), seen))

    def test_what_a_command_does_on_the_original_code_is_looked_at_once_in_a_run(self):
        state, plan = {}, contract(FLIPS.format("./run_tests.sh"))
        # The first change's test does not fail on the original code; the Builder is sent back.
        self.assertIsNone(self.settle(state, plan, Seen()))
        seen = Seen(run_tests_sh={WITH_TESTS: 1})
        chosen = self.settle(state, plan, seen)
        self.assertEqual("./run_tests.sh", chosen["regression"])
        self.assertEqual([("./run_tests.sh", WITH_TESTS)], seen.asked)
        # Found once, it is not looked for again.
        self.assertFalse(roles.wanted(state, "base", plan, **self.TESTS))
        # Another plan, or another original code, is looked at again.
        self.assertTrue(roles.wanted(state, "base", contract(FLIPS.format("./lint.sh")), **self.TESTS))
        self.assertTrue(roles.wanted(state, "other", plan, **self.TESTS))

    def test_a_guards_command_is_seen_to_run_the_existing_tests(self):
        plan = contract("guard: run `./run_tests.sh`", "guard: run `./lint.sh`", kind="build")
        seen = Seen(run_tests_sh={WITHOUT_TESTS: 1})
        state = {}
        chosen = self.settle(state, plan, seen, changed_tests=[], guards=["./run_tests.sh", "./lint.sh"])
        self.assertEqual({"./run_tests.sh"}, roles.runs_tests(state, plan))
        self.assertEqual(("./run_tests.sh", None), (chosen["suite"], chosen["regression"]))
        self.assertEqual("./run_tests.sh", roles.suite(state, plan))
        self.assertEqual(set(), roles.runs_tests(state, contract("guard: run `./lint.sh`")))
        self.assertIn(("./lint.sh", WITHOUT_TESTS), seen.asked)

    def test_a_project_with_no_test_file_cannot_show_that_a_command_runs_its_tests(self):
        seen = Seen(run_tests_sh={WITHOUT_TESTS: 1})
        plan = contract("guard: run `./run_tests.sh`", kind="build")
        chosen = self.settle({}, plan, seen, original_tests=[], changed_tests=[], guards=["./run_tests.sh"])
        self.assertIsNone(chosen)
        self.assertEqual([("./run_tests.sh", ORIGINAL)], seen.asked)


class CandidateTests(unittest.TestCase):
    """Which text of a plan is a command to try: what this machine can run, not a list of runners."""

    def setUp(self):
        self.tree = Path(tempfile.mkdtemp(prefix="roles-"))
        self.addCleanup(shutil.rmtree, self.tree, True)
        (self.tree / "bin").mkdir()
        (self.tree / "backend").mkdir()
        for name, mode in (
            ("run_tests.sh", 0o755),
            ("notes.sh", 0o644),
            ("bin/mix", 0o755),
            ("backend/check.sh", 0o755),
        ):
            (self.tree / name).write_text("#!/bin/sh\n")
            (self.tree / name).chmod(mode)

    def test_a_program_on_the_path_or_a_file_of_the_project_that_may_be_executed(self):
        with mock.patch.dict(os.environ, {"PATH": f"{self.tree / 'bin'}{os.pathsep}{os.environ['PATH']}"}):
            for command, runs in (
                ("mix test", True),
                ("env MIX_ENV=test mix test", True),
                ("./run_tests.sh", True),
                ("./run_tests.sh --quick", True),
                ("cd backend && ./check.sh", True),
                ("cd backend && mix test", True),
                ("cd backend && ./run_tests.sh", False),  # the root's script, not that directory's
                ("cd nowhere && mix test", False),
                ("./notes.sh", False),  # not executable
                ("./missing.sh", False),
                ("no-such-program-anywhere test", False),
                ("page_count(11, 5)", False),
                ("autocode --status", False),
                ("'unbalanced", False),
            ):
                with self.subTest(command=command):
                    self.assertEqual(runs, roles.program(command, self.tree))

    def test_the_plans_commands_to_try_are_the_checks_it_runs_and_the_programs_it_names(self):
        plan = contract(
            "run `mix test`: it fails on the original code and passes with the fix",
            "`./run_tests.sh` exits 0",
            "`page_count 11 5` prints 3",
            "`sh tests/test_pager.sh` exits 0",
            "guard: run `./run_tests.sh --quick`",
            "The Tester reads `pager.sh` and runs `./missing.sh`",
        )
        with mock.patch.dict(os.environ, {"PATH": f"{self.tree / 'bin'}{os.pathsep}{os.environ['PATH']}"}):
            found = roles.candidates(plan, self.tree)
        # In the plan's order: a text that is no program, or a file that cannot be run, is left out.
        self.assertEqual(["mix test", "./run_tests.sh", "sh tests/test_pager.sh", "./run_tests.sh --quick"], found)
        self.assertEqual([], roles.candidates({**plan, "approval_status": "draft"}, self.tree))


class ProofTests(unittest.TestCase):
    """Real proofs in a project no runner detects, with the command its plan gives."""

    def assert_proven_with(self, proof, command):
        self.assertEqual((verify.PASS, [], []), (proof["verdict"], proof["failures"], proof["unverified"]), proof)
        self.assertEqual((command, command), (proof["commands"]["suite"], proof["commands"]["regression"]))
        said = f"`{command}` passes on the original code and fails there with the change's tests, so it runs them"
        self.assertTrue(any(said in note for note in proof["notes"]), proof["notes"])
        self.assertTrue(any(said in note for note in proof["review_reasons"]), proof["review_reasons"])
        self.assertEqual({command: WITH_TESTS}, proof["plan_command_roles"]["found"])

    def test_the_projects_test_script_proves_a_bug_fix_though_it_names_no_test_file(self):
        # The real run's plan (2026-11-15): "run `sh run_tests.sh`: it exits non-zero on the original pager.c
        # with the new checks and exits 0 with the fix". The proof had "No command to run the regression tests".
        made = project(self)
        self.assertIsNone(verify.detect_framework(made.root))
        self.assert_proven_with(prove(self, contract(FLIPS.format("sh run_tests.sh")), CHANGE), "sh run_tests.sh")

    def test_so_does_the_script_run_by_its_path(self):
        # `./run_tests.sh` begins with no program of the plan check's list: it was not read as a command at all.
        self.assert_proven_with(prove(self, contract(FLIPS.format("./run_tests.sh")), CHANGE), "./run_tests.sh")

    @unittest.skipUnless(shutil.which("make"), "the project's tests run with make")
    def test_so_does_a_make_target_of_another_name(self):
        seed = {**SEED, "Makefile": ".PHONY: tests\ntests:\n\tsh run_tests.sh\n"}
        made = project(self, seed)
        self.assertIsNone(verify.detect_framework(made.root))
        self.assert_proven_with(prove(self, contract(FLIPS.format("make tests")), CHANGE, seed=seed), "make tests")

    def test_a_new_test_that_passes_on_the_original_code_is_still_no_proof(self):
        weak = CHECK + '[ "$(page_count 5 5)" = 1 ] || { echo "one page"; exit 1; }\n'
        proof = prove(self, contract(FLIPS.format("sh run_tests.sh")), {**CHANGE, "tests/test_pager.sh": weak})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("pass on the unfixed base code", " ".join(proof["failures"]))
        # The command was seen to run the project's tests, so it is the suite, and the suite judged the change.
        self.assertEqual("sh run_tests.sh", proof["commands"]["suite"])

    def test_a_fix_that_does_not_fix_still_fails(self):
        proof = prove(self, contract(FLIPS.format("sh run_tests.sh")), {**CHANGE, "pager.sh": BUG + "\n"})
        self.assertEqual(verify.FAIL, proof["verdict"], proof)

    def test_a_command_that_runs_no_test_proves_nothing_and_the_proof_says_what_it_saw(self):
        proof = prove(self, contract("run `sh pager.sh`: it passes with the fix"), CHANGE)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
        self.assertEqual(
            {"suite": None, "regression": None}, {k: proof["commands"][k] for k in ("suite", "regression")}
        )
        said = (
            "ran the approved plan's commands to find the one that runs its tests; none could be used: `sh pager.sh` "
            "passes on the original code, and still passes with the change's tests, and still passes with its test "
            "files made unloadable"
        )
        self.assertTrue(any(said in note for note in proof["notes"]), proof["notes"])
        self.assertIsNone(proof["plan_command_roles"])

    def test_a_refactor_is_guarded_by_the_projects_test_script(self):
        proof = prove(
            self,
            contract("guard: run `./run_tests.sh`", kind="build"),
            {"pager.sh": REFACTORED},
            seed={**SEED, "pager.sh": FIXED},
        )
        self.assertEqual((verify.PASS, []), (proof["verdict"], proof["unverified"]), proof)
        checks = proof["checks"]
        self.assertEqual(
            (0, 0),
            (checks["guard_command_on_base:AC1"]["exit_code"], checks["guard_command_on_candidate:AC1"]["exit_code"]),
        )
        self.assertEqual({"./run_tests.sh": WITHOUT_TESTS}, proof["plan_command_roles"]["found"])

    def test_a_refactor_that_breaks_the_guarded_tests_is_refused(self):
        proof = prove(
            self,
            contract("guard: run `./run_tests.sh`", kind="build"),
            {"pager.sh": BUG + "\n"},
            seed={**SEED, "pager.sh": FIXED, "tests/test_pager.sh": CHECK + PARTIAL},
        )
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("passes on the original code and fails with the change", " ".join(proof["failures"]))

    def test_a_guard_whose_command_runs_no_test_guards_nothing_and_the_proof_says_so(self):
        proof = prove(
            self,
            contract("guard: run `sh pager.sh`", kind="build"),
            {"pager.sh": REFACTORED},
            seed={**SEED, "pager.sh": FIXED},
        )
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)
        self.assertIn(
            "Guard AC1's command `sh pager.sh` was not seen to run a test the project already has",
            " ".join(proof["unverified"]),
        )
        self.assertNotIn("guard_command_on_base:AC1", proof.get("checks") or {})

    def test_a_later_proof_of_the_run_does_not_look_again(self):
        state = {}
        plan = contract(FLIPS.format("sh run_tests.sh"))
        self.assert_proven_with(prove(self, plan, CHANGE, state=state), "sh run_tests.sh")
        with mock.patch.object(regression, "_observe", side_effect=AssertionError("looked again")):
            made_workspace = Path(state["workspace"])
            (made_workspace / "pager.sh").write_text(FIXED + "# tidied\n")
            with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
                proof = regression.prove(state, made_workspace, made_workspace.parent / "run")
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual("sh run_tests.sh", proof["commands"]["regression"])


class OriginalGateTests(unittest.TestCase):
    """The gate of the original tests runs them with the command the proof found."""

    def run_task(self, change):
        made = project(self)
        run = made.root.parent / "run"
        state = run_state(made, contract(FLIPS.format("sh run_tests.sh")))
        guard.reconcile(
            state,
            state["settings"],
            SimpleNamespace(run_dir=None),
            made.root,
            run,
            is_test_path=verify.is_test_path,
            discover_command=lambda: None,
        )
        made.write(change)
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            proof = regression.prove(state, made.root, run)
        return proof, lambda: guard.replay(state, made.root, run / "replays", verify.scratch_run, timeout=60)

    def test_it_runs_the_original_tests_with_the_plans_command(self):
        proof, gate = self.run_task(CHANGE)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        result = gate()
        self.assertEqual(("PASS", "sh run_tests.sh"), (result["verdict"], result.get("plan_command")), result)

    def test_a_weakened_original_assertion_still_fails_it(self):
        weakened = CHECK.replace("= 2 ]", "= 2 ] || true") + PARTIAL
        broken = "page_count() { echo $(( ($1 + $2 - 1) / $2 + 3 * ($1 == 10) )); }\n"
        proof, gate = self.run_task({"pager.sh": broken, "tests/test_pager.sh": weakened})
        with self.assertRaises(guard.commands.OriginalGateFailure):
            gate()


class PlanCheckTests(unittest.TestCase):
    """What a plan may guard with, where no runner is detected."""

    def state(self, files):
        made = Project(files)
        self.addCleanup(made.close)
        return plan_tests.CheckTests.state(self, made)

    def body(self, method, *paths):
        body = plan_tests.plan(*paths)
        body["acceptance_criteria"][0]["verification_method"] = method
        return body

    def test_a_guard_with_the_projects_own_test_command_is_left_to_the_proof(self):
        # It names no test file, as `dotnet test`, `mix test` and `swift test` name none: every such plan was
        # refused with "names no existing test file", which the Planner could not settle.
        for command in ("./run_tests.sh", "sh run_tests.sh", "make tests", "mix test"):
            with self.subTest(command=command):
                lifecycle.validate_body(self.state(SEED), self.body(f"guard: run `{command}`"), ready=True)

    def test_a_guard_that_names_a_test_file_the_project_does_not_have_is_still_refused(self):
        # The advice names this project's kind of command. In a real run (2026-11-15) it gave the Ruby
        # example, `ruby test/pager_test.rb`, to a C project whose tests run with a shell script.
        refusal = (
            r"C1 `sh tests/test_new.sh` names no existing test file\. "
            r"Write the command this project runs its existing tests with \(\"guard: run `\./run_tests\.sh`\"\)"
        )
        with self.assertRaisesRegex(ValueError, refusal):
            lifecycle.validate_body(self.state(SEED), self.body("guard: run `sh tests/test_new.sh`"), ready=True)

    def test_a_project_with_no_test_keeps_the_refusal(self):
        with self.assertRaises(ValueError):
            lifecycle.validate_body(
                self.state({"pager.sh": FIXED, "run.sh": RUNNER}), self.body("guard: run `sh run.sh`"), ready=True
            )

    def test_the_planner_is_told_to_give_the_projects_own_test_command(self):
        with self.assertRaisesRegex(
            ValueError,
            r"as the command this project runs its tests with \(the one its README, build file or CI uses\).*"
            r'"guard: run `\./run_tests\.sh`" with that same command',
        ):
            lifecycle.validate_body(
                self.state(SEED), plan_tests.named("test: test_partial", "tests/test_pager.sh"), ready=True
            )


class ReplayTests(unittest.TestCase):
    """A replay runs nothing: it takes what the recorded proof kept."""

    def test_the_recorded_roles_are_taken_for_the_same_plan(self):
        plan = contract(FLIPS.format("./run_tests.sh"))
        tests = {"original_tests": ["tests/test_pager.sh"], "changed_tests": ["tests/test_pager.sh"]}
        tree = Path(tempfile.mkdtemp(prefix="roles-"))
        self.addCleanup(shutil.rmtree, tree, True)
        (tree / "run_tests.sh").write_text("#!/bin/sh\n")
        (tree / "run_tests.sh").chmod(0o755)
        recorded: dict = {}
        roles.settle(recorded, "base", plan, **tests, tree=tree, observe=Seen(run_tests_sh={WITH_TESTS: 1}))
        kept = roles.kept(recorded)
        self.assertNotIn("seen", kept)
        state: dict = {}
        roles.restore(state, kept, "another-commit", plan)
        chosen = roles.adopted(state, "another-commit", plan, **tests)
        self.assertEqual(("./run_tests.sh", "./run_tests.sh"), (chosen["suite"], chosen["regression"]))
        # A recording of another plan, or none, gives nothing.
        for other in (None, {}, {**kept, "version": 0}):
            state = {}
            roles.restore(state, other, "another-commit", plan)
            self.assertIsNone(roles.adopted(state, "another-commit", plan, **tests))
        roles.restore(state, kept, "another-commit", contract(FLIPS.format("./lint.sh")))
        self.assertEqual({}, state)
        self.assertIsNone(roles.kept({}))


if __name__ == "__main__":
    unittest.main()
