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

import autocode_changed_code_trigger as changed_code_trigger
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


# A project that keeps its checks where no path rule looks.
CHECKS_RUNNER = RUNNER.replace("tests/test_*.sh", "checks/*.sh")
CHECKS = {"pager.sh": BUG, "checks/pager.sh": CHECK, "run_checks.sh": CHECKS_RUNNER}
CHECKS_FLIP = FLIPS.format("./run_checks.sh")
# The other way a plan says which command runs the project's tests: a command guard.
CHECKS_GUARD = "guard: run `./run_checks.sh`"
# A criterion that says no such thing.
CHECKS_RUN = "run `./run_checks.sh` and see it pass"
BROKEN = "page_count() { echo $(( ($1 + $2) / $2 )); }\n"
NO_TEST = "No regression test was added or changed, so the bug is not shown to be reproduced"
ALSO_PASS = "The regression tests also pass on the unfixed base code, so they do not reproduce the bug"


class Probe:
    """A scripted ``probe``: the exit code for each (path, how), and what was asked."""

    def __init__(self, **codes):
        self.codes, self.asked = codes, []

    def __call__(self, command, path, how, where):
        self.asked.append((path, how, where))
        return self.codes.get(path.replace("/", "_").replace(".", "_"), {}).get(how, 0)


class FindTestsTests(unittest.TestCase):
    """Which files are tests a command runs, with scripted looks."""

    LEAF = {roles.UNLOADED: 2, roles.EMPTIED: 0}
    NEEDED = {roles.UNLOADED: 2, roles.EMPTIED: 1}

    def setUp(self):
        self.tree = Path(tempfile.mkdtemp(prefix="roles-"))
        self.addCleanup(shutil.rmtree, self.tree, True)
        for name in ("run_checks.sh", "lint.sh"):
            (self.tree / name).write_text("#!/bin/sh\n")
            (self.tree / name).chmod(0o755)

    def find(self, state, plan, seen, probe, files, commands=("./run_checks.sh",), beside=()):
        looks = {"files": files, "beside": beside, "tree": self.tree, "observe": seen, "probe": probe}
        return roles.find_tests(state, "base", plan, commands=commands, **looks)

    def test_a_file_the_command_runs_and_nothing_else_needs_is_a_test(self):
        probe = Probe(checks_pager_sh=self.LEAF, pager_sh=self.NEEDED, notes_sh={roles.UNLOADED: 0})
        files = {"checks/pager.sh": ORIGINAL, "pager.sh": ORIGINAL, "notes.sh": ORIGINAL}
        found = roles.leaves("./run_checks.sh", files, probe)
        self.assertEqual({"checks/pager.sh": {"where": ORIGINAL, roles.UNLOADED: 2, roles.EMPTIED: 0}}, found)
        # A file the command never loads is not emptied: one look says it is no test.
        self.assertNotIn(("notes.sh", roles.EMPTIED, ORIGINAL), probe.asked)

    def test_a_file_the_command_names_is_the_program_and_is_not_looked_at(self):
        for command in ("sh pager.sh", "./pager.sh --pages", "env N=1 ./pager.sh"):
            with self.subTest(command=command):
                probe = Probe(pager_sh=self.LEAF)
                self.assertEqual({}, roles.leaves(command, {"pager.sh": ORIGINAL}, probe))
                self.assertEqual([], probe.asked)
        # Named from inside a directory of the project, it is the same file.
        probe = Probe(backend_run_sh=self.LEAF, backend_checks_a_sh=self.LEAF)
        files = {"backend/run.sh": ORIGINAL, "backend/checks/a.sh": ORIGINAL}
        self.assertEqual(["backend/checks/a.sh"], list(roles.leaves("cd backend && ./run.sh", files, probe)))

    def test_a_look_that_did_not_finish_finds_no_test(self):
        for codes in ({roles.UNLOADED: None}, {roles.UNLOADED: 2, roles.EMPTIED: None}):
            with self.subTest(codes=codes):
                self.assertEqual({}, roles.leaves("./run_checks.sh", {"a.sh": ORIGINAL}, Probe(a_sh=codes)))

    def test_one_look_tries_a_bounded_number_of_files(self):
        probe = Probe()
        files = dict.fromkeys((f"src/{n}.sh" for n in range(roles.MAX_FILES + 3)), ORIGINAL)
        self.assertEqual({}, roles.leaves("./run_checks.sh", files, probe))
        self.assertEqual(roles.MAX_FILES, len(probe.asked))

    def test_the_command_that_runs_a_changed_file_as_a_test_takes_the_empty_roles(self):
        state, plan = {}, contract("run `./lint.sh` and see it pass", CHECKS_FLIP)
        seen = Seen()
        probe = Probe(checks_pager_sh=self.LEAF, pager_sh=self.NEEDED)
        files = {"checks/pager.sh": ORIGINAL, "pager.sh": ORIGINAL}
        both = ("./lint.sh", "./run_checks.sh")
        found = self.find(state, plan, seen, lambda c, *rest: probe(c, *rest) if "checks" in c else 0, files, both)
        self.assertEqual(["checks/pager.sh"], list(found))
        self.assertEqual(found, roles.tests(state, "base", plan))
        chosen = roles.adopted(state, "base", plan, original_tests=list(found), changed_tests=list(found))
        self.assertEqual(("./run_checks.sh", "./run_checks.sh"), (chosen["suite"], chosen["regression"]))
        self.assertEqual({"./run_checks.sh": roles.OBSERVED_TESTS}, chosen["observed"])
        self.assertIn("runs files it shows to be tests", roles.note(chosen))
        self.assertEqual({"./run_checks.sh"}, roles.runs_tests(state, plan))
        said = roles.tests_note(state, "base", plan)
        self.assertIn("No rule says checks/pager.sh is a test file. AutoCode ran `./run_checks.sh` to see", said)
        self.assertIn("the approved plan says that command runs this project's tests", said)
        self.assertIn("The changed lines there are still checked as code", said)
        self.assertEqual(found, roles.kept(state)["tests"])

    def test_a_command_that_fails_on_the_original_code_is_not_looked_with(self):
        state, plan, probe = {}, contract(CHECKS_FLIP), Probe(checks_pager_sh=self.LEAF)
        self.assertEqual(
            {}, self.find(state, plan, Seen(run_checks_sh={ORIGINAL: 1}), probe, {"checks/pager.sh": ORIGINAL})
        )
        self.assertEqual([], probe.asked)
        self.assertIsNone(roles.tests_note(state, "base", plan))
        self.assertIsNone(roles.adopted(state, "base", plan, original_tests=[], changed_tests=[]))

    def test_the_original_code_is_looked_at_once_and_the_changed_code_each_time(self):
        state, plan, seen = {}, contract(CHECKS_FLIP), Seen()
        probe = Probe(checks_pager_sh=self.LEAF, checks_new_sh=self.LEAF)
        files = {"checks/new.sh": roles.CHANGE, "checks/pager.sh": ORIGINAL}
        self.assertEqual(sorted(files), sorted(self.find(state, plan, seen, probe, files)))
        first = len(probe.asked)
        self.assertEqual(sorted(files), sorted(self.find(state, plan, seen, probe, files)))
        self.assertEqual(
            [("checks/new.sh", roles.UNLOADED, roles.CHANGE), ("checks/new.sh", roles.EMPTIED, roles.CHANGE)],
            probe.asked[first:],
        )
        self.assertEqual([("./run_checks.sh", ORIGINAL)], seen.asked)
        # Only a test of the original code shows that the command runs tests the project already has.
        only_new: dict = {}
        self.find(only_new, plan, Seen(), probe, {"checks/new.sh": roles.CHANGE})
        self.assertEqual(set(), roles.runs_tests(only_new, plan))

    def test_a_file_that_is_a_test_no_longer_takes_its_commands_roles_with_it(self):
        state, plan = {}, contract(CHECKS_FLIP)
        self.find(state, plan, Seen(), Probe(checks_new_sh=self.LEAF), {"checks/new.sh": roles.CHANGE})
        self.assertEqual("./run_checks.sh", state["plan_command_roles"]["regression"])
        self.find(state, plan, Seen(), Probe(checks_new_sh=self.NEEDED), {"checks/new.sh": roles.CHANGE})
        self.assertEqual({}, roles.tests(state, "base", plan))
        self.assertEqual(
            (None, None), (state["plan_command_roles"]["suite"], state["plan_command_roles"]["regression"])
        )

    def test_the_plan_says_which_command_runs_the_tests(self):
        says = (
            CHECKS_FLIP,
            "run `./run_checks.sh` fails (exit 1) on the original code and passes (exit 0) with the fix",
            "Run `./run_checks.sh`: it FAILS before the change and PASSES after it",
            CHECKS_GUARD,
        )
        for method in says:
            with self.subTest(method=method):
                self.assertEqual(["./run_checks.sh"], roles.declared(contract(method)["body"], self.tree))
        silent = (
            CHECKS_RUN,
            "run `./run_checks.sh`: it passes and never fails",
            "test: run `./run_checks.sh`: it fails on the original code and passes with the fix",
            "the checks fail on the original code and pass with the fix",
        )
        for method in silent:
            with self.subTest(method=method):
                self.assertEqual([], roles.declared(contract(method)["body"], self.tree))
        review = contract(CHECKS_FLIP)["body"]
        review["acceptance_criteria"][0]["human_review"] = True
        self.assertEqual([], roles.declared(review, self.tree))
        self.assertEqual([], roles.declared(None, self.tree))

    def test_only_a_command_the_plan_says_runs_tests_is_looked_with(self):
        # What a command does cannot tell a test from a program's own entry file, so the plan says.
        state, plan, probe = {}, contract(CHECKS_FLIP), Probe(checks_pager_sh=self.LEAF)
        self.assertEqual({}, self.find(state, plan, Seen(), probe, {"checks/pager.sh": ORIGINAL}, commands=()))
        self.assertEqual([], probe.asked)
        self.assertEqual(set(), roles.runs_tests(state, plan))

    def test_a_new_test_beside_one_of_the_original_code_shows_the_command_runs_existing_tests(self):
        state, plan = {}, contract(CHECKS_FLIP)
        probe = Probe(checks_new_sh=self.LEAF, checks_pager_sh=self.LEAF, checks_support_sh=self.NEEDED)
        files, beside = {"checks/new.sh": roles.CHANGE}, ["checks/support.sh", "checks/pager.sh", "checks/more.sh"]
        self.assertEqual(["checks/new.sh"], list(self.find(state, plan, Seen(), probe, files, beside=beside)))
        self.assertEqual({"./run_checks.sh"}, roles.runs_tests(state, plan))
        self.assertEqual("checks/pager.sh", roles.kept(state)["existing_test"])
        # The first one found is enough, and it is no test of the change.
        self.assertNotIn(("checks/more.sh", roles.UNLOADED, ORIGINAL), probe.asked)
        self.assertEqual(["checks/new.sh"], list(roles.tests(state, "base", plan)))

    def test_a_look_at_no_file_drops_what_an_earlier_one_found(self):
        state, plan = {}, contract(CHECKS_FLIP)
        self.find(state, plan, Seen(), Probe(checks_pager_sh=self.LEAF), {"checks/pager.sh": ORIGINAL})
        self.assertTrue(roles.has_tests(state))
        self.assertEqual(
            {}, roles.find_tests(state, "base", plan, commands=(), files={}, tree=self.tree, observe=None, probe=None)
        )
        self.assertFalse(roles.has_tests(state))
        self.assertEqual({}, roles.tests(state, "base", plan))

    def test_a_settling_keeps_what_a_look_for_tests_found(self):
        state, plan = {}, contract(CHECKS_FLIP)
        found = self.find(state, plan, Seen(), Probe(checks_pager_sh=self.LEAF), {"checks/pager.sh": ORIGINAL})
        tests = {"original_tests": list(found), "changed_tests": list(found)}
        roles.settle(state, "base", plan, **tests, tree=self.tree, observe=Seen())
        self.assertEqual(found, roles.tests(state, "base", plan))

    def test_a_replay_takes_the_recorded_tests(self):
        recorded, plan = {}, contract(CHECKS_FLIP)
        found = self.find(recorded, plan, Seen(), Probe(checks_pager_sh=self.LEAF), {"checks/pager.sh": ORIGINAL})
        state: dict = {}
        roles.restore(state, roles.kept(recorded), "another-commit", plan)
        self.assertEqual(found, roles.tests(state, "another-commit", plan))


class ObservedTestsProofTests(unittest.TestCase):
    """Real proofs in a shell project whose checks are in ``checks/``, a directory no path rule calls
    tests, run by ``./run_checks.sh``, which the plan says fails on the original code and passes with
    the fix. No runner is detected."""

    PLAN = contract(CHECKS_FLIP)

    def prove(self, change, seed=CHECKS, plan=None):
        state: dict = {}
        made = project(self, seed, ("run_checks.sh",))
        made.write(change)
        state.update(run_state(made, plan or self.PLAN))
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            return regression.prove(state, made.root, made.root.parent / "run"), state

    def test_a_bug_fix_with_a_check_added_to_the_existing_check_file_is_proven(self):
        proof, state = self.prove({"pager.sh": FIXED, "checks/pager.sh": CHECK + PARTIAL})
        self.assertEqual(("PASS", [], []), (proof["verdict"], proof["failures"], proof["unverified"]))
        self.assertEqual("./run_checks.sh", proof["commands"]["regression"])
        seen = proof["plan_command_roles"]["tests"]
        self.assertEqual({"checks/pager.sh": {"where": ORIGINAL, roles.UNLOADED: 2, roles.EMPTIED: 0}}, seen)
        said = "No rule says checks/pager.sh is a test file. AutoCode ran `./run_checks.sh` to see"
        self.assertTrue(any(said in note for note in proof["notes"]), proof["notes"])
        self.assertTrue(any(said in note for note in proof["review_reasons"]), proof["review_reasons"])
        # The file is still code for the evidence of changed lines, which no longer reads "The project
        # has no test command and no test files" from the paths.
        self.assertIn("checks/pager.sh", proof["source_files"])
        self.assertIsNone(changed_code_trigger.unmeasurable(state))
        self.assertEqual(
            changed_code_trigger.UNMEASURABLE, changed_code_trigger.unmeasurable({**state, "plan_command_roles": {}})
        )
        self.assertIn("regression_on_base", proof["checks"])

    def test_a_new_check_file_the_runner_picks_up_is_seen_on_the_changed_code(self):
        proof, _ = self.prove({"pager.sh": FIXED, "checks/partial.sh": ". ./pager.sh\n" + PARTIAL})
        self.assertEqual(("PASS", [], []), (proof["verdict"], proof["failures"], proof["unverified"]))
        self.assertEqual(roles.CHANGE, proof["plan_command_roles"]["tests"]["checks/partial.sh"]["where"])

    def test_a_script_the_command_names_is_the_program_and_no_test(self):
        # `sh selftest.sh` runs selftest.sh and nothing else needs it, as `sh pager.sh` does pager.sh: no
        # look tells a script of checks from the program, so a file the command names is never its test.
        seed = {"pager.sh": BUG, "selftest.sh": "#!/bin/sh\nset -e\n" + CHECK}
        change = {"pager.sh": FIXED, "selftest.sh": "#!/bin/sh\nset -e\n" + CHECK + PARTIAL}
        proof, _ = self.prove(change, seed, contract(FLIPS.format("sh selftest.sh"), "guard: run `sh selftest.sh`"))
        self.assertEqual(("FAIL", [NO_TEST]), (proof["verdict"], proof["failures"]))
        self.assertIn("Guard AC2's command `sh selftest.sh` was not seen to run a test", " ".join(proof["unverified"]))

    def test_a_project_in_a_directory_below_the_root_is_proven_the_same_way(self):
        seed = {f"backend/{path}": text for path, text in CHECKS.items()}
        change = {"backend/pager.sh": FIXED, "backend/checks/pager.sh": CHECK + PARTIAL}
        command = "cd backend && ./run_checks.sh"
        state: dict = {}
        made = project(self, seed, ("backend/run_checks.sh",))
        made.write(change)
        state.update(run_state(made, contract(FLIPS.format(command), f"guard: run `{command}`")))
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            proof = regression.prove(state, made.root, made.root.parent / "run")
        self.assertEqual(("PASS", [], []), (proof["verdict"], proof["failures"], proof["unverified"]))
        self.assertEqual(["backend/checks/pager.sh"], list(proof["plan_command_roles"]["tests"]))

    def test_a_fix_with_no_check_still_has_no_test(self):
        proof, state = self.prove({"pager.sh": FIXED})
        self.assertEqual(("FAIL", [NO_TEST]), (proof["verdict"], proof["failures"]))

    def test_a_later_proof_with_a_test_the_rules_name_forgets_the_files_seen_before(self):
        state: dict = {}
        made = project(self, CHECKS, ("run_checks.sh",))
        made.write({"pager.sh": FIXED, "checks/pager.sh": CHECK + PARTIAL})
        state.update(run_state(made, self.PLAN))
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            regression.prove(state, made.root, made.root.parent / "run")
            self.assertTrue(roles.has_tests(state))
            made.write({"tests/test_pager.sh": CHECK + PARTIAL})
            regression.prove(state, made.root, made.root.parent / "run")
        self.assertFalse(roles.has_tests(state))
        self.assertEqual({}, roles.tests(state, state["plan_command_roles"]["base"], self.PLAN))

    def test_a_check_that_passes_on_the_original_code_still_fails_the_proof(self):
        proof, _ = self.prove({"pager.sh": FIXED, "checks/pager.sh": CHECK + CHECK.split("\n", 1)[1]})
        self.assertEqual(("FAIL", [ALSO_PASS]), (proof["verdict"], proof["failures"]))

    def test_two_code_files_that_need_each_other_are_no_test(self):
        # Each alone breaks the command, and that is no check: other code needs what the file defines.
        change = {
            "pager.sh": '. ./round.sh\npage_count() { round_up "$1" "$2"; }\n',
            "round.sh": "round_up() { echo $(( ($1 + $2 - 1) / $2 )); }\n",
        }
        proof, _ = self.prove(change)
        self.assertEqual(("FAIL", [NO_TEST]), (proof["verdict"], proof["failures"]))

    def test_a_file_the_command_loads_and_nothing_uses_is_taken_with_the_tests_and_proves_nothing(self):
        # A settings file passes for a test by what the command does. Taken with the tests, it has to
        # make the command fail on the original code, which it does not.
        seed = {**CHECKS, "settings.sh": "PAGE_SIZE=5\n", "checks/pager.sh": ". ./settings.sh\n" + CHECK}
        proof, _ = self.prove({"pager.sh": FIXED, "settings.sh": "PAGE_SIZE=10\n"}, seed)
        self.assertEqual(("FAIL", [ALSO_PASS]), (proof["verdict"], proof["failures"]))
        self.assertEqual(["settings.sh"], list(proof["plan_command_roles"]["tests"]))
        self.assertIn("settings.sh", proof["source_files"])

    def test_a_change_that_drops_a_check_it_breaks_fails_on_the_original_checks(self):
        # The original check file is kept as the original code has it and run with the change.
        proof, _ = self.prove({"pager.sh": BROKEN, "checks/pager.sh": ". ./pager.sh\n" + PARTIAL})
        self.assertEqual("FAIL", proof["verdict"])
        self.assertTrue(proof["failures"][0].startswith("The base suite definition fails against the candidate code"))

    def test_a_project_with_no_check_at_all_has_no_test(self):
        command = "sh -c '. ./pager.sh; [ \"$(page_count 11 5)\" = 3 ]'"
        proof, _ = self.prove({"pager.sh": FIXED}, {"pager.sh": BUG}, contract(FLIPS.format(command)))
        self.assertEqual(("FAIL", [NO_TEST]), (proof["verdict"], proof["failures"]))

    def test_without_the_plans_word_no_file_is_taken_for_a_test(self):
        change = {"pager.sh": FIXED, "checks/pager.sh": CHECK + PARTIAL}
        proof, state = self.prove(change, plan=contract(CHECKS_RUN))
        self.assertEqual(("FAIL", [NO_TEST]), (proof["verdict"], proof["failures"]))
        self.assertNotIn("tests", state.get("plan_command_roles") or {})

    def test_a_guard_is_the_plans_word_too(self):
        change = {"pager.sh": FIXED, "checks/pager.sh": CHECK + PARTIAL}
        proof, _ = self.prove(change, plan=contract(CHECKS_RUN, CHECKS_GUARD))
        self.assertEqual(("PASS", [], []), (proof["verdict"], proof["failures"], proof["unverified"]))
        self.assertEqual(0, proof["checks"]["guard_command_on_base:AC2"]["exit_code"])

    def test_a_programs_own_entry_file_is_no_test(self):
        # `./app.sh` runs app.sh, and nothing else needs it: what a test looks like to a command. Edited to
        # use a file the change adds, it fails on the original code and passes with the change, with no
        # check anywhere. Even a plan that says `./app.sh` runs the tests does not make it one: the
        # command names it, so it is the program.
        app = '#!/bin/sh\ncd "$(dirname "$0")"\n. ./pager.sh\necho "pages: $(page_count 10 5)"\n'
        uses = app.replace("echo", ". ./fmt.sh; fmt_pages")
        change = {"app.sh": uses, "fmt.sh": 'fmt_pages() { echo "$1"; }\n'}
        state: dict = {}
        made = project(self, {"pager.sh": BUG, "app.sh": app}, ("app.sh",))
        made.write(change)
        state.update(run_state(made, contract(FLIPS.format("./app.sh"), "guard: run `./app.sh`")))
        with mock.patch.object(verify.diff_coverage, "collect", return_value=None):
            proof = regression.prove(state, made.root, made.root.parent / "run")
        self.assertEqual(("FAIL", [NO_TEST]), (proof["verdict"], proof["failures"]))


class ObservedTestsPlanCheckTests(unittest.TestCase):
    """The plan check no longer says "the project has no tests yet" of a project whose command shows it has."""

    def state(self, files):
        made = project(self, files, ("run_checks.sh",))
        run_dir = Path(tempfile.mkdtemp(prefix="plan-check-"))
        self.addCleanup(shutil.rmtree, run_dir, True)
        return {**plan_tests.CheckTests.state(self, made), "base_commit": made.base, "run_dir": str(run_dir)}

    def body(self, method, *paths, guard=None):
        body = plan_tests.plan(*paths)
        body["milestones"][0]["affected_paths"] = list(paths)
        body["acceptance_criteria"][0]["verification_method"] = method
        if guard:
            row = {**body["acceptance_criteria"][0], "id": "G1", "verification_method": guard}
            body["acceptance_criteria"].append(row)
            body["milestones"][0]["acceptance_criteria"].append("G1")
        return body

    def test_a_plan_that_says_the_command_fails_first_and_edits_a_check_it_runs_is_accepted(self):
        lifecycle.validate_body(self.state(CHECKS), self.body(CHECKS_FLIP, "pager.sh", "checks/pager.sh"), ready=True)

    def test_so_is_one_that_adds_a_check_beside_one(self):
        lifecycle.validate_body(self.state(CHECKS), self.body(CHECKS_FLIP, "pager.sh", "checks/partial.sh"), ready=True)

    def test_so_is_one_that_guards_with_the_command(self):
        body = self.body(CHECKS_RUN, "pager.sh", "checks/pager.sh", guard=CHECKS_GUARD)
        lifecycle.validate_body(self.state(CHECKS), body, ready=True)

    def test_a_plan_of_code_alone_is_still_refused(self):
        with self.assertRaisesRegex(ValueError, r"This plan changes code \(pager\.sh\) and plans no test"):
            lifecycle.validate_body(self.state(CHECKS), self.body(CHECKS_FLIP, "pager.sh"), ready=True)

    def test_a_plan_that_does_not_say_so_is_refused_and_told_how_to_say_it(self):
        # The look alone accepts nothing: it cannot tell a test from a program's own entry file.
        told = (
            r"plans no test, and the project has no tests yet.*`\./run_checks\.sh` runs checks/pager\.sh as a "
            r"test is run.*write the criterion the new check shows as \"run `\./run_checks\.sh`: it fails on the "
            r"original code and passes with the fix\".*plan a new test file"
        )
        with self.assertRaisesRegex(ValueError, told):
            lifecycle.validate_body(
                self.state(CHECKS), self.body(CHECKS_RUN, "pager.sh", "checks/pager.sh"), ready=True
            )

    def test_a_guard_whose_command_runs_no_file_the_plan_is_about_is_refused(self):
        told = r"G1 `\./run_checks\.sh` names no existing test file.*Name the file that gets the change's check"
        with self.assertRaisesRegex(ValueError, told):
            lifecycle.validate_body(
                self.state(CHECKS), self.body(CHECKS_RUN, "pager.sh", guard=CHECKS_GUARD), ready=True
            )

    def test_a_guard_with_a_check_of_its_own_is_refused_and_the_plan_stands_without_it(self):
        # A live Planner guarded with a one-line check of its own beside the fail-first criterion. The
        # guard is refused as before; with an ordinary method in its place the plan is accepted.
        inline = "guard: run `sh -c '. ./pager.sh; [ \"$(page_count 10 5)\" = 2 ]'`"
        body = self.body(CHECKS_FLIP, "pager.sh", "checks/pager.sh", guard=inline)
        with self.assertRaisesRegex(ValueError, "G1 .* names no existing test file.*or give the criterion an ordinary"):
            lifecycle.validate_body(self.state(CHECKS), body, ready=True)
        body["acceptance_criteria"][1]["verification_method"] = inline.removeprefix("guard: ")
        lifecycle.validate_body(self.state(CHECKS), body, ready=True)

    def test_a_project_with_no_check_is_refused_whatever_the_plan_says(self):
        files = {"pager.sh": BUG, "run_checks.sh": "#!/bin/sh\nexit 0\n"}
        # The runner is named by the command, so it is the program and no test of the project.
        with self.assertRaisesRegex(ValueError, "plans no test, and the project has no tests yet") as refused:
            lifecycle.validate_body(self.state(files), self.body(CHECKS_FLIP, "pager.sh", "lib.sh"), ready=True)
        self.assertNotIn("as a test is run", str(refused.exception))

    def test_a_run_with_nowhere_to_look_keeps_the_refusal(self):
        state = self.state(CHECKS)
        del state["run_dir"]
        with self.assertRaisesRegex(ValueError, "plans no test, and the project has no tests yet"):
            lifecycle.validate_body(state, self.body(CHECKS_FLIP, "pager.sh", "checks/pager.sh"), ready=True)
        body = self.body(CHECKS_RUN, "pager.sh", "checks/pager.sh", guard=CHECKS_GUARD)
        with self.assertRaisesRegex(ValueError, "names no existing test file"):
            lifecycle.validate_body(state, body, ready=True)

    def test_a_replay_runs_nothing_and_reads_what_the_recorded_proof_found(self):
        import autocode_command_looks as command_looks
        import autocode_replay_checks as recorded_checks

        state = self.state(CHECKS)
        body = self.body(CHECKS_FLIP, "pager.sh", "checks/pager.sh")
        found = {"tests_command": "./run_checks.sh", "existing_test": "checks/pager.sh"}
        with (
            mock.patch.object(recorded_checks, "simulated", return_value=True),
            mock.patch.object(command_looks, "looker", side_effect=AssertionError("a replay runs nothing")),
        ):
            with mock.patch.object(recorded_checks, "kept", return_value=found):
                lifecycle.validate_body(state, body, ready=True)
            with mock.patch.object(recorded_checks, "kept", return_value=None), self.assertRaises(ValueError):
                lifecycle.validate_body(state, body, ready=True)


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
