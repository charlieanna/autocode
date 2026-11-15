"""Where a change's tests run is found by running them (autocode_test_homes).

The layout rules put each changed test somewhere; one run there shows whether it loads. These cases
script that run (``probe``) and check what is looked for, in which order, what is saved and what the
proof and the gate of the original tests then run. The proofs and gate runs in real projects are in
tests/test_nested_project_tests.py.
"""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_package_scope as package_scope
import autocode_replay_checks as replay_checks
import autocode_test_homes as homes
from autocode_test_framework import Framework

PYTHON = "python3 -m unittest"
A, B, W, TOP = "api/tests/test_a.py", "api/tests/test_b.py", "worker/tests/test_w.py", "tests/test_top.py"


def receipt(*, passed=1, failed=0, unloaded=0, complete=True, **more):
    results = {
        "passed": [f"p{number}" for number in range(passed)],
        "failed": [f"f{number}" for number in range(failed)],
        "skipped": [],
        "collection_errors": [f"e{number}" for number in range(unloaded)],
        "complete": complete,
    }
    return {"exit_code": 1 if failed or unloaded else 0, "timed_out": False, "results": results, **more}


BROKEN = receipt(passed=0, unloaded=1)


def stays(file, project=""):
    """The saved home of a test that loads where the rules put it."""
    inside = f"cd {project} && " if project else ""
    return {
        "project": project,
        "from": project,
        "moved": False,
        "command": f"{inside}{PYTHON} -v {file[len(project) + 1 :] if project else file}",
        "suite": f"{inside}{PYTHON} discover -v",
    }


class LoadsTests(unittest.TestCase):
    def test_what_a_run_shows(self):
        for label, shown, expected in (
            ("tests ran and passed", receipt(), True),
            ("a test that runs and fails has loaded", receipt(passed=0, failed=2), True),
            ("a file could not be loaded", receipt(unloaded=1), False),
            (
                "a file was not collected",
                {**receipt(), "results": {**receipt()["results"], "uncollected": ["x"]}},
                False,
            ),
            ("no test ran", receipt(passed=0), False),
            ("the report did not finish", receipt(complete=False), False),
            ("only an exit code, and it passed", {"exit_code": 0, "timed_out": False, "results": None}, True),
            ("only an exit code, and it failed", {"exit_code": 1, "timed_out": False, "results": None}, False),
            ("it timed out", {**receipt(), "timed_out": True}, None),
            ("it could not be started", {"error": "no test command runs these files", "results": None}, None),
            ("zero tests, said as an error", {**receipt(passed=0), "error": "reported zero tests"}, False),
            ("no receipt", None, None),
        ):
            with self.subTest(label):
                self.assertIs(expected, homes.loads(shown))


class Project(unittest.TestCase):
    """api/ and worker/, each with code and tests, and tests at the root. Every directory has a suite."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        for name in (A, B, W, TOP, "api/src/deep/tests/unit/test_d.py", "tests/fixtures/app/tests/test_f.py"):
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text("")
        self.at_root = Framework("unittest", f"{PYTHON} discover -v", python="python3")
        self.rules = (self.at_root, [], [])
        self.ran: list[str] = []
        self.without: set[str] = set()  # directories where nothing is detected

    def detect(self, directory):
        name = str(Path(directory).relative_to(self.root))
        return None if name in self.without else Framework("unittest", f"{PYTHON} discover -v", python="python3")

    def probe(self, shows):
        """``shows(command)`` is the receipt of a run, or True and False for one that loads or does not."""

        def run(suite, command, _label):
            self.assertTrue(suite.suite.startswith(command.split(PYTHON)[0] + PYTHON), (suite.suite, command))
            self.ran.append(command)
            shown = shows(command)
            return receipt() if shown is True else BROKEN if shown is False else shown

        return run

    def settle(self, state, tests, shows, rules=None):
        homes.settle(state, self.root, self.at_root, rules or self.rules, tests, self.detect, self.probe(shows))

    def place(self, state, tests, rules=None):
        framework, suites, notes = homes.place(state, self.root, self.at_root, rules or self.rules, tests, self.detect)
        return getattr(framework, "directory", ""), {suite["project"]: suite["files"] for suite in suites}, notes


class SettleTests(Project):
    def test_a_test_that_loads_where_the_rules_put_it_stays(self):
        state: dict = {}
        self.assertTrue(homes.unsettled(state, self.rules, [TOP]))
        self.settle(state, [TOP], lambda command: True)
        self.assertEqual([f"{PYTHON} -v {TOP}"], self.ran)
        self.assertEqual({TOP: stays(TOP)}, state["test_homes"]["files"])
        self.assertFalse(homes.unsettled(state, self.rules, [TOP]))
        self.assertIs(self.rules, homes.place(state, self.root, self.at_root, self.rules, [TOP], self.detect))
        self.assertEqual({}, homes.moved(state))

    def test_one_that_does_not_load_goes_to_the_first_directory_around_it_where_it_does(self):
        state: dict = {}
        self.settle(state, [A], lambda command: command.startswith("cd api && "))
        inside = f"cd api && {PYTHON} -v tests/test_a.py"
        self.assertEqual([f"{PYTHON} -v {A}", inside], self.ran)
        saved = {
            "project": "api",
            "from": "",
            "moved": True,
            "command": inside,
            "suite": f"cd api && {PYTHON} discover -v",
        }
        self.assertEqual({A: saved}, state["test_homes"]["files"])
        self.assertEqual({A: saved}, homes.moved(state))
        directory, suites, notes = self.place(state, [A])
        self.assertEqual(("api", {}), (directory, suites))
        self.assertEqual(
            [
                f"The changed test {A} does not load from the repository root (`{PYTHON} -v {A}`); "
                f"it loads from api/ (`{inside}`), so that is where it runs"
            ],
            notes,
        )

    def test_the_places_a_test_may_run_from(self):
        for label, test, without, expected in (
            ("the directories around it, then the root", A, (), ["api", ""]),
            (
                "innermost first, and no test directory",
                "api/src/deep/tests/unit/test_d.py",
                (),
                ["api/src/deep", "api/src", "api", ""],
            ),
            (
                "only where a suite is detected",
                "api/src/deep/tests/unit/test_d.py",
                ("api/src",),
                ["api/src/deep", "api", ""],
            ),
            ("a fixture among the tests is no project", "tests/fixtures/app/tests/test_f.py", (), [""]),
            ("a test at the root", TOP, (), [""]),
        ):
            with self.subTest(label):
                self.without = set(without)
                suite_of = lambda project: package_scope.detect(self.root, project, self.detect)  # noqa: E731
                found = homes.candidates(test, self.at_root, suite_of)
                self.assertEqual(expected, [project for project, _suite in found])
        self.assertEqual([], homes.candidates("docs/notes.txt", self.at_root, suite_of))

    def test_the_tests_of_one_project_go_there_in_one_run(self):
        state: dict = {}
        self.settle(state, [A, B], lambda command: command.startswith("cd api && "))
        self.assertEqual([f"{PYTHON} -v {A} {B}", f"cd api && {PYTHON} -v tests/test_a.py tests/test_b.py"], self.ran)
        self.assertEqual(("api", {}), self.place(state, [A, B])[:2])
        self.assertEqual(f"cd api && {PYTHON} -v tests/test_b.py", homes.moved(state)[B]["command"])

    def test_tests_of_two_projects_each_go_to_their_own(self):
        state: dict = {}
        self.settle(state, [A, W, TOP], lambda command: command.startswith("cd ") or command.endswith(TOP))
        self.assertEqual(
            [
                f"{PYTHON} -v {A} {TOP} {W}",
                f"cd api && {PYTHON} -v tests/test_a.py",
                f"{PYTHON} -v {TOP}",  # the root's own test, alone: it was in the run that did not load
                f"cd worker && {PYTHON} -v tests/test_w.py",
            ],
            self.ran,
        )
        directory, suites, notes = self.place(state, [A, W, TOP])
        self.assertEqual(("", {"api": [A], "worker": [W]}), (directory, suites))
        self.assertTrue(any("that project's own suite `cd worker && " in note for note in notes), notes)
        # Without a test of the root's own, the first project's suite is the proof's.
        state = {}
        self.settle(state, [A, W], lambda command: command.startswith("cd "))
        self.assertEqual(("api", {"worker": [W]}), self.place(state, [A, W])[:2])

    def test_one_that_still_does_not_load_is_run_alone_and_looked_for_again(self):
        state: dict = {}
        loads = lambda command: command.startswith("cd api && ") and "test_b" not in command  # noqa: E731
        self.settle(state, [A, B], loads)
        inside = f"cd api && {PYTHON} -v"
        self.assertEqual(
            [
                f"{PYTHON} -v {A} {B}",
                f"{inside} tests/test_a.py tests/test_b.py",
                f"{inside} tests/test_a.py",
                f"{inside} tests/test_b.py",
                f"{PYTHON} -v {B}",  # it ran among others at the root: alone there last
            ],
            self.ran,
        )
        # Only what a run showed is saved: the next proof looks for the other again.
        self.assertEqual([A], sorted(state["test_homes"]["files"]))
        self.assertTrue(homes.unsettled(state, self.rules, [A, B]))
        self.assertEqual(("", {"api": [A]}), self.place(state, [A, B])[:2])
        del self.ran[:]
        self.settle(state, [A, B], lambda command: command.startswith("cd api && "))
        self.assertEqual([f"{PYTHON} -v {B}", f"{inside} tests/test_b.py"], self.ran)
        self.assertEqual([A, B], sorted(homes.moved(state)))

    def test_a_run_that_timed_out_leaves_the_test_where_the_rules_put_it_and_is_not_repeated(self):
        state: dict = {}
        self.settle(state, [A], lambda command: {**receipt(), "timed_out": True})
        self.assertEqual([f"{PYTHON} -v {A}"], self.ran)
        self.assertEqual({A: stays(A)}, state["test_homes"]["files"])
        self.assertEqual(("", {}), self.place(state, [A])[:2])
        self.assertFalse(homes.unsettled(state, self.rules, [A]))

    def test_a_run_that_could_not_be_made_leaves_it_there_and_is_looked_for_again(self):
        state: dict = {}
        self.settle(state, [A], lambda command: None)
        self.assertEqual([f"{PYTHON} -v {A}"], self.ran)
        self.assertEqual({}, state["test_homes"]["files"])
        self.assertEqual(("", {}), self.place(state, [A])[:2])
        self.assertTrue(homes.unsettled(state, self.rules, [A]))

    def test_a_place_whose_run_timed_out_is_no_home(self):
        state: dict = {}
        slow = {**receipt(), "timed_out": True}
        self.settle(state, [A], lambda command: slow if command.startswith("cd api && ") else False)
        self.assertEqual({}, state["test_homes"]["files"])

    def test_a_settled_test_is_not_run_again(self):
        state: dict = {}
        self.settle(state, [A, TOP], lambda command: command.startswith("cd api && ") or command.endswith(TOP))
        ran = len(self.ran)
        self.settle(state, [A, TOP], lambda command: self.fail("a settled test ran again"))
        self.assertEqual(ran, len(self.ran))
        # A new test beside them is the only one looked for.
        self.settle(state, [A, B, TOP], lambda command: command.startswith("cd api && "))
        self.assertEqual([f"{PYTHON} -v {B}", f"cd api && {PYTHON} -v tests/test_b.py"], self.ran[ran:])

    def test_a_test_the_rules_now_put_elsewhere_is_looked_for_again(self):
        state: dict = {}
        self.settle(state, [A], lambda command: True)
        inside = package_scope.detect(self.root, "api", self.detect)
        rules = (inside, [], [])
        self.assertTrue(homes.unsettled(state, rules, [A]))
        del self.ran[:]
        self.settle(state, [A], lambda command: True, rules)
        self.assertEqual([f"cd api && {PYTHON} -v tests/test_a.py"], self.ran)
        self.assertEqual(stays(A, "api"), state["test_homes"]["files"][A])
        # The gate reads it: the test does not run where the root's suite runs.
        self.assertEqual({A: stays(A, "api")}, homes.away(state))
        self.assertEqual({}, homes.moved(state))

    def test_a_test_that_loads_only_from_the_root_goes_there(self):
        state: dict = {}
        rules = (package_scope.detect(self.root, "api", self.detect), [], [])
        self.settle(state, [A], lambda command: not command.startswith("cd "), rules)
        self.assertEqual([f"cd api && {PYTHON} -v tests/test_a.py", f"{PYTHON} -v {A}"], self.ran)
        directory, suites, notes = self.place(state, [A], rules)
        self.assertEqual(("", {}), (directory, suites))
        self.assertIn("does not load from api/", notes[0])
        self.assertIn("it loads from the repository root", notes[0])

    def test_a_test_no_suite_of_the_rules_runs_is_looked_for_without_a_first_run(self):
        state: dict = {}
        node = Framework("node", "npm test --silent")
        rules = (node, [], [f"The changed tests {A} are in the repository root, and neither ... so they were not run"])
        homes.settle(state, self.root, node, rules, [A], self.detect, self.probe(lambda command: True))
        self.assertEqual([f"cd api && {PYTHON} -v tests/test_a.py"], self.ran)
        framework, suites, notes = homes.place(state, self.root, node, rules, [A], self.detect)
        self.assertEqual(("api", []), (framework.directory, suites))
        self.assertEqual(1, len(notes), "the rules' note on a test that was not run is no longer true")
        self.assertIn(f"The changed test {A} is run by no suite of the repository root; it loads from api/", notes[0])

    def test_a_runner_that_reports_only_an_exit_code_shows_a_home_by_passing(self):
        state: dict = {}
        code = lambda number: {"exit_code": number, "timed_out": False, "results": None}  # noqa: E731
        self.settle(state, [A], lambda command: code(0 if command.startswith("cd api && ") else 1))
        self.assertEqual("api", homes.moved(state)[A]["project"])
        state = {}
        self.settle(state, [A], lambda command: code(1))
        self.assertEqual({}, state["test_homes"]["files"])

    def test_one_settling_runs_a_bounded_number_of_commands(self):
        state: dict = {}
        with mock.patch.object(homes, "MAX_PROBES", 2):
            self.settle(state, [A, W], lambda command: command.startswith("cd worker && "))
        self.assertEqual(2, len(self.ran))
        self.assertEqual({}, state["test_homes"]["files"])

    def test_a_home_whose_suite_is_gone_leaves_the_rules_choice(self):
        state: dict = {}
        self.settle(state, [A], lambda command: command.startswith("cd api && "))
        self.without = {"api"}
        self.assertEqual(("", {}), self.place(state, [A])[:2])

    def test_a_saved_record_of_another_version_is_not_read(self):
        state = {"test_homes": {"version": 0, "files": {A: {"project": "api", "from": "", "moved": True}}}}
        self.assertTrue(homes.unsettled(state, self.rules, [A]))
        self.assertEqual({}, homes.moved(state))
        self.assertIs(self.rules, homes.place(state, self.root, self.at_root, self.rules, [A], self.detect))
        self.settle(state, [A], lambda command: True)
        self.assertEqual({A: stays(A)}, state["test_homes"]["files"])


class GateCommandTests(unittest.TestCase):
    """What the gate of the original tests runs for the changed files the proof found a home for."""

    def home(self, project, file):
        runs = f"cd {project} && " if project else ""
        return {
            "project": project,
            "from": "",
            "moved": True,
            "command": f"{runs}{PYTHON} -v {file}",
            "suite": f"{runs}{PYTHON} discover -v",
        }

    def test_which_command_runs_which_changed_file(self):
        found = {
            A: self.home("api", "tests/test_a.py"),
            B: self.home("api", "tests/test_b.py"),
            W: self.home("worker", "tests/test_w.py"),
        }
        api, worker = f"cd api && {PYTHON} discover -v", f"cd worker && {PYTHON} discover -v"
        for label, names, missing, expected in (
            ("a moved test runs alone in its home", [A], (), {A: found[A]["command"]}),
            ("each in its own", [A, W], (), {A: found[A]["command"], W: found[W]["command"]}),
            ("a test the proof did not move is left to the bound command", [A, TOP], (), {A: found[A]["command"]}),
            (
                "a helper in such a project runs its whole suite",
                ["api/tests/helpers.py"],
                (),
                {"api/tests/helpers.py": api},
            ),
            (
                "which then runs its tests too",
                [A, "api/tests/helpers.py", W],
                (),
                {A: api, "api/tests/helpers.py": api, W: found[W]["command"]},
            ),
            ("a deleted test runs its project's whole suite", [A, W], (W,), {A: found[A]["command"], W: worker}),
            ("a file of no such project", ["docs/tests/test_d.py"], (), {}),
        ):
            with self.subTest(label):
                self.assertEqual(expected, homes.replay_commands(found, names, lambda name: name not in missing))

    def test_a_test_moved_to_the_root_runs_alone_there(self):
        found = {A: self.home("", A)}
        self.assertEqual({A: f"{PYTHON} -v {A}"}, homes.replay_commands(found, [A, B], lambda name: True))
        # Deleted, it has no project whose suite could stand in: the bound command runs.
        self.assertEqual({}, homes.replay_commands(found, [A], lambda name: False))

    def test_only_complete_moved_records_are_read(self):
        state = {
            "test_homes": {
                "version": homes.VERSION,
                "files": {
                    A: self.home("api", "tests/test_a.py"),
                    B: stays(B, "api"),
                    "api/tests/test_c.py": {"project": "api", "from": "api", "moved": False},
                    W: {"project": "worker", "from": "", "moved": True, "command": None, "suite": "x"},
                    TOP: "api",
                },
            }
        }
        self.assertEqual([A], sorted(homes.moved(state)))
        self.assertEqual([A, B], sorted(homes.away(state)))
        self.assertEqual({}, homes.away({"test_homes": {"version": homes.VERSION, "files": {TOP: stays(TOP)}}}))
        self.assertEqual({}, homes.moved({}))
        self.assertEqual({}, homes.moved({"test_homes": {"version": homes.VERSION, "files": None}}))

    def test_a_proof_keeps_the_moved_homes_and_a_replay_takes_them(self):
        home = self.home("api", "tests/test_a.py")
        recorded = {A: home, B: stays(B, "api"), W: "worker", 7: home}
        for kept in (None, {"version": 0, "files": {W: home}}):
            with self.subTest(kept=kept):
                state = {"test_homes": kept} if kept else {}
                homes.restore(state, recorded)
                self.assertEqual({"version": homes.VERSION, "files": {A: home}}, state["test_homes"])
                self.assertEqual({A: home}, homes.moved(state))
        state = {"test_homes": {"version": homes.VERSION, "files": {TOP: stays(TOP)}}}
        homes.restore(state, None)  # a recording made before homes were kept
        self.assertEqual([TOP], sorted(state["test_homes"]["files"]))


class RecordedHomesTests(unittest.TestCase):
    """A replay reads the homes of the recording's next regression proof (autocode_replay_checks.test_homes)."""

    def read(self, rows, consumed, values):
        tape = SimpleNamespace(document={"checks": rows})
        state = {"settings": {}, "replay_result": {"consumed_checks": consumed}}
        with (
            mock.patch.object(replay_checks.transport, "selected", return_value=tape),
            mock.patch.object(replay_checks, "authenticated", side_effect=lambda _, row: (b"", b"", values[row["id"]])),
        ):
            return replay_checks.test_homes(state)

    def test_the_next_proof_not_yet_replayed_is_read(self):
        row = lambda number, purpose: {"id": f"check-{number}", "purpose": purpose}  # noqa: E731
        rows = [
            row(1, "regression_baseline"),
            row(2, "regression_proof"),
            row(3, "check_replay"),
            row(4, "regression_proof"),
        ]
        values = {"check-2": {"test_homes": {A: "first"}}, "check-4": {"commands": {}}}
        self.assertEqual({A: "first"}, self.read(rows, [], values))
        self.assertEqual({A: "first"}, self.read(rows, ["check-1"], values))
        self.assertIsNone(self.read(rows, ["check-1", "check-2"], values))  # the second proof kept none
        self.assertIsNone(self.read(rows, ["check-1", "check-2", "check-3", "check-4"], values))
        self.assertIsNone(self.read(rows[:1], [], values))


class PlacesTests(unittest.TestCase):
    def test_the_directories_a_test_may_run_from(self):
        for test, expected in (
            ("test_top.py", []),
            ("tests/test_top.py", []),
            ("api/tests/test_a.py", ["api"]),
            ("api/test_a.py", ["api"]),
            ("services/api/src/tests/unit/test_d.py", ["services/api/src", "services/api", "services"]),
            ("tests/fixtures/app/tests/test_f.py", []),
        ):
            with self.subTest(test):
                self.assertEqual(expected, homes.places(test))


if __name__ == "__main__":
    unittest.main()
