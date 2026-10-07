"""Approved executable checks cannot be replaced; their scope must be possible."""
from pathlib import Path
import re
import shlex
import subprocess
import tempfile
import unittest
from unittest import mock

import autocode_check_replay as check_replay
import autocode_verification_plan as plan
import autocode_goal_lifecycle as lifecycle
import autocode_verify as verify
from goal_fixtures import body

# The exit declaration at 6cbf805, verbatim: the reference CommandsTests compares every probe method against.
HEAD_DECLARATION = re.compile(
    r"\s*(?:directly\s+)?(?:[,;]?\s*(?:and|then)\s+)?"
    r"(?:confirm|expect|assert|verify|check)\s+"
    r"(?:exact\s+(?:stdout/stderr|stdout\s+and\s+stderr)\s+bytes\s+and\s+)?"
    r"(?:exit\s+(?:codes?|statuses?)|(?:it\s+)?exits?)\s*"
    r"(?:of\s+|are\s+|[:=]\s*)?"
    r"(?P<codes>[+-]?\d+(?:\s*[/,]\s*[+-]?\d+)*)\s*[.)]*\s*", re.IGNORECASE)
# Every verification method the skeptic probes of this fix tried (2026-10-06), including the live S4 method.
PROBES = [
        'Run `go test ./a`; then run `go test ./b` and assert exit 0, empty stderr.',
        'Run `go test ./a` and inspect `README.md` then confirm exit code 2, empty stdout.',
        'Run `go test ./a` and `go test ./b` and confirm exit codes 2/0/1, empty stdout.',
        'Run `go test ./a` and confirm exit code 256, empty stdout.',
        'Run `go test ./a`; then run `go test ./b` and assert exit 2, empty stdout.',
        'Run `python3 -m notes add x`, then run `python3 -m notes add` and assert exit 2, empty stdout.',
        'Run `python3 -m notes add x` and `python3 -m notes frobnicate` and assert exit 2, the initial add only '
        'seeding the store.',
        'Run `python3 -m notes search` and assert exit 2, or zero when no notes file exists.',
        'Run `go test ./a` and `go test ./b` and assert exit 2, except for the last one, which succeeded.',
        'Run `go test ./a` and `go test ./b` and assert exit 2, except the latter which passed.',
        'Run `go test ./a` and `go test ./b` and assert exit 2, but only for the one before.',
        'Run `python3 -m notes search` and assert exit 2, unless the store is empty.',
        'Run `python3 -m notes search` and assert exit 2, or success on an empty store.',
        'Run `go test ./a` and `go test ./b` and assert exit 2, the other one is a setup step.',
        'Run `go test ./a` and `go test ./b` and assert exit 2, and zero for the last.',
        'Run `go test ./a` and `go test ./b` and assert exit 2, each separately; the second one exits zero.',
        'Run `python3 -m notes add x` and `python3 -m notes frobnicate` and assert exit 2.',
        'Run `python3 -m pytest tests/test_cli.py -q` and confirm the CLI exits 2 on bad input.',
        'Run `go test ./...`, where `notes frobnicate` must exit 2.',
        'Run `npm test`; it verifies that `bin/cli` with no args should exit 2.',
        'Run `python3 -m unittest -v`; the usage-error case must exit 2.',
        'Run `pytest -q`: the CLI must exit 2 for an unknown command.',
        'Run `go test ./...` and confirm exit 0 even though the fixture script exits 1.',
        'Run `python3 -m unittest -v` and check that the program exits with status 2 on a missing argument.',
        'Run `python3 -m unittest tests.test_cli -v`; it asserts the CLI exits 2 for usage errors.',
        'Run `pytest -q` to confirm the parser exits 2 on unknown flags.',
        'Run `make check`, which verifies the binary exits 1 when the config is missing.',
        'Run `cargo test` and confirm that bad flags make the binary exit 2.',
        'Run `python3 -m unittest -v` and confirm the usage scenario exits 2 with empty stdout.',
        'Run `python3 -m unittest -v`. The usage errors should exit 2.',
        'Run `go test ./...` and verify that the CLI rejects bad input by exiting with code 2.',
        'Run `python3 -m notes add`; exit status 2 is expected.',
        'Run `python3 -m notes add` and confirm a usage error with exit status 2.',
        'Run `python3 -m notes add` and confirm that it returns 2.',
        'Run `python3 -m notes add` and confirm it returns exit code 2.',
        'Run `python3 -m notes add` and confirm the exit code is 2.',
        'Run `python3 -m notes add` and confirm its exit code is 2.',
        'Run `python3 -m notes add` and confirm status 2.',
        'Run `python3 -m notes add` and confirm it terminates with code 2.',
        'Run `python3 -m notes add` and confirm it fails (exit 2).',
        'Run `python3 -m notes add` and confirm it fails.',
        'Run `python3 -m notes add` and confirm it errors out with a usage message.',
        'Run `python3 -m notes add` and confirm that the command exits 2.',
        'Run `python3 -m notes add`, which must fail with a usage error.',
        'Run `python3 -m notes add` and confirm a nonzero status.',
        'Run `python3 -m notes add` and confirm that exit is 2.',
        'Run `python3 -m notes add` and confirm exit=2.',
        'Run `python3 -m notes add` and confirm exit-code 2.',
        'Run `python3 -m notes add` and confirm `$?` is 2.',
        'Run `python3 -m notes add` and confirm it exits two.',
        'Run `python3 -m notes add` and confirm the process exits non-zero.',
        'Run `python3 -m notes add` and confirm the run is unsuccessful.',
        'Run `python3 -m notes add` and confirm the test is expected to exit 2.',
        'Run `python3 -m notes add`: must exit 2.',
        'Run `python3 -m notes add` - it must exit 2.',
        'Run `python3 -m notes add` and it must exit 2.',
        'Execute `python3 -m notes add` and expect exit code 2.',
        'Run `python3 -m notes add` and expect exit code 2 and no output.',
        'Run `python3 -m notes add` and expect exit code 2 with empty stdout.',
        'Run `python3 -m notes add` and assert exit code 2 and empty stdout.',
        'Run `python3 -m notes add` and assert exit 2 and empty stdout.',
        'Run `python3 -m notes add` then assert exit 2, empty stdout.',
        'Run `python3 -m notes add` and assert that it exits 2.',
        'Run `python3 -m notes add` and assert exit 2 (usage error).',
        'Run `python3 -m notes add` and assert exit 2.\nThen inspect stdout.',
        'Run `python3 -m unittest -v` and inspect `notes.json` and confirm exit 0, unchanged contents.',
        'Run `go test ./...` and open `coverage.html` and confirm exit 0, no failing packages.',
        'Run `pytest -q`, then run `npm run lint` and confirm exit 0, no warnings.',
        'Run `python3 -m unittest -v` and confirm exit code 0, empty stderr.',
        'Run `go test ./...`; a non-zero exit fails the check.',
        'Run `pytest -q` and confirm it does not exit non-zero.',
        'Run `npm test`; it must not exit 1.',
        'Run `python3 -m unittest tests.test_cli`, which checks that `notes frobnicate` exits 2.',
        'Run `python3 -m unittest -v` and confirm the usage-error tests (exit 2, empty stdout) pass.',
        'Run `python3 -m unittest -v`: all pass, including test_s4_usage_errors_exit_2 (usage errors exit 2).',
        'Run `python3 -m unittest discover -s tests` and confirm 0 failures; the CLI tests cover exit code 2 for '
        'usage errors.',
        'Run `python3 -m notes add x`, then run `python3 -m notes add` and assert exit 2.',
        'Run `python3 -m notes add x` and `python3 -m notes frobnicate` and assert exit 2, the first only seeding '
        'the store.',
        'Run `go test ./a` and `go test ./b` and assert exit 2, apart from the second which succeeds.',
        'Run `go test ./a` and `go test ./b` and assert exit 0, and the second prints an error and fails.',
        'Run `go test ./a` and `go test ./b` and assert exit 2; the second must succeed.',
        'Run `go test ./a` and `go test ./b` and assert exit 2 for the first; the second must pass.',
        '`python3 -m notes list` prints nothing on a fresh store; run `python3 -m notes frobnicate` and assert '
        'exit 2.',
        'Run `python3 -m unittest -v` and confirm exit code 0, and that the usage tests see exit 2.',
        'Run `python3 -m notes search` and assert exit 2, or 0 when no notes file exists.',
        'Run `go test ./a` and `go test ./b` and assert exit 2, and 0 for the second.',
        'Run `python3 -m unittest -v`; any non-zero exit fails the criterion.',
        'Run `python3 -m notes add` and assert exit 2, empty stdout and an unchanged `notes.json`.',
        'Run `python3 -m notes add` and assert exit 2, empty stdout and an unchanged notes.json.',
        'Run `go test ./a` and `go test ./b` and assert exit 2, the second returns 0.',
        'Reviewer runs `python3 greet.py` with no name and confirms it exits with status 2 and a readable usage line.',
        'Run `python3 -m unittest -v`; the suite includes the usage test (exit 2, empty stdout).',
        'review: confirm notes/cli.py whitelists `search` so notes/commands/search.py can serve this without '
        'editing skeleton files; the output itself is verified by M2 and is not executed in M1',
        'test: test_s4_usage_errors_exit_2 (also: after one `add x`, run `python3 -m notes add` and `python3 -m '
        'notes frobnicate` and assert exit 2, empty stdout and byte-identical notes file content)',
]


class CommandsTests(unittest.TestCase):
    def test_explicit_commands_are_extracted_but_prose_and_test_names_are_not(self):
        for text, expected in (
            ("python3 -m unittest test_greet.py", ["python3 -m unittest test_greet.py"]),
            ("Run `go test ./...` and inspect `balance`.", ["go test ./..."]),
            ("Validator runs `python3 -m unittest` and reads `README.md`.", ["python3 -m unittest"]),
            ("Run `go test ./a` and `go test ./b`.", ["go test ./a", "go test ./b"]),
            ("`go test ./...`", ["go test ./..."]),
            ("Inspect README.md and verify the exact text `python3 -m temperature VALUE UNIT` "
             "without invoking the metavariable template as a command.", []),
            ("Run python3 -m unittest tests.test_greet", ["python3 -m unittest tests.test_greet"]),
            ("test: test_c1_hello", []), ("Execute CLI cases", []),
            # Prose after a plain command (live bugfix-trivial, 2026-09-30): replayed as a command, it never passes.
            ("python3 -m unittest -v passes; Validator reads the diff", []),
            ("Run python3 -m unittest -v via capture and read the diff", []),
            # A sentence after the command (live parallel-diamond, 2026-10-01): unittest read its words as modules.
            ("Run python3 -m unittest integration.test_check from repo root: 2 tests OK.", []),
            ("python3 -m unittest integration.test_check, expect 2 tests OK.", []),
            ("python3 -m unittest tests.test_a tests.test_b", ["python3 -m unittest tests.test_a tests.test_b"]),
            ("pytest tests/test_x.py::test_y -k name", ["pytest tests/test_x.py::test_y -k name"]),
            ("go test ./...", ["go test ./..."]),
            # A sentence after -c (live architecture-two-services, 2026-09-30): NameError when replayed.
            ("Run python3 -c doing a topological sort/DFS over depends_on.", []),
            ("Run `python3 -c doing a topological sort` over the graph", []),
            ("python3 -c: Kahn topological sort over depends_on sorts all nodes (AC6)", []),
            ("sh -c checking each file exists", []),
            ("python3 -c exit", ["python3 -c exit"]),
            ('python3 -c "import a; a.f()" x y', ['python3 -c "import a; a.f()" x y']),
            ("python3 -m unittest discover -s tests -t .", ["python3 -m unittest discover -s tests -t ."]),
            ("python3 -c 'assert f(1, 2) == 3'", ["python3 -c 'assert f(1, 2) == 3'"]),
            ("pytest --verify --output=out.xml tests", ["pytest --verify --output=out.xml tests"])):
            with self.subTest(text=text):
                self.assertEqual(expected, plan.commands(text))

    def test_live_unquoted_verification_prose_stays_with_the_validator(self):
        for method in (
            "Run python3 -m unittest -v after the correction and require a successful exit with the complete suite passing.",
            "Run python3 -m unittest -v and confirm it exits successfully.",
        ):
            with self.subTest(method=method):
                self.assertEqual([], plan.commands(method))
        self.assertEqual(["python3 -m unittest -v"], plan.commands(
            "Run `python3 -m unittest -v` and confirm it exits successfully."))

    def test_a_parenthesized_note_after_a_command_stays_with_the_validator(self):
        # A live to-do run (Claude models, 2026-10-04): AutoResolver wrote this validation_plan entry, the replay
        # ran it whole in /bin/sh (Syntax error: "(" unexpected) and rejected every Validator report.
        self.assertEqual([], plan.commands("python3 -m unittest test_todo -v (all 10 pass)"))
        self.assertEqual(["python3 -m unittest test_todo -v"], plan.commands(
            "`python3 -m unittest test_todo -v` (all 10 pass)"))
        for command in ("python3 -c 'print(1)'", "pytest $(ls tests)"):
            with self.subTest(command=command):
                self.assertEqual([command], plan.commands(command))

    def test_declared_zero_exits_keep_the_original_required_commands(self):
        self.assertEqual(["go test ./a", "go test ./b"], plan.commands(
            "Run `go test ./a` and `go test ./b` and confirm exit codes 0/0."))

    def test_exit_expectations_with_ambiguous_assignments_are_refused(self):
        for method in (
            "Run `go test ./a` and `go test ./b` and confirm exit code 2.",
            "Run `go test ./a` and inspect `README.md` then confirm exit code 2.",
            "Run `go test ./a` and confirm exit code 256.",
            "Run `go test ./a` and confirm exit code -1.",
        ):
            with self.subTest(method=method), self.assertRaisesRegex(ValueError, "one status.*per executable command"):
                plan.commands(method)

    # Live program-notes-cli S4 (Claude models, 2026-10-06), verbatim: both usage errors were left bare, replay
    # required them to exit 0, correct code exits 2, and no Validator report could pass.
    LIVE_S4 = ("test: test_s4_usage_errors_exit_2 (also: after one `add x`, run `python3 -m notes add` and "
               "`python3 -m notes frobnicate` and assert exit 2, empty stdout and byte-identical notes file content)")

    @staticmethod
    def wrapped(command, code):
        return "sh -c " + shlex.quote(f'({command}); autocode_plan_exit=$?; test "$autocode_plan_exit" -eq {code}')

    def test_the_live_usage_error_method_asserts_exit_2_for_both_commands(self):
        self.assertEqual([self.wrapped("python3 -m notes add", 2), self.wrapped("python3 -m notes frobnicate", 2)],
                         plan.commands(self.LIVE_S4))

    def test_a_run_list_with_a_status_and_plain_output_clauses_is_asserted(self):
        a, b = "go test ./a", "go test ./b"
        for method, expected in (
            ("Run `go test ./a` and confirm exit code 2, empty stdout.", [self.wrapped(a, 2)]),
            ("Run `python3 -m notes add` and assert exit 2, empty stdout and an unchanged notes.json.",
             [self.wrapped("python3 -m notes add", 2)]),
            ("Run `go test ./a` and assert exit 2; usage on stderr.", [self.wrapped(a, 2)]),
            ("Execute `go test ./a` and assert exit 2 and no output.", [self.wrapped(a, 2)]),
            ("Run `go test ./a` and confirm exit code 0 and an unchanged notes file.", [a]),
            # N statuses, one per command; or one status for every command of the run list.
            ("Run `go test ./a` and `go test ./b` and confirm exit codes 2/0; stderr names the flag",
             [self.wrapped(a, 2), b]),
            ("Run `go test ./a` and `go test ./b` and assert exit 2, empty stdout.",
             [self.wrapped(a, 2), self.wrapped(b, 2)]),
            ("Invoke `go test ./a`, `go test ./b`, and `go test ./c` and assert exit 3, an error line.",
             [self.wrapped(f"go test ./{name}", 3) for name in "abc"]),
            # A quoted literal before the run list, and a ")" closing a parenthetical the method opened.
            ("With `add x` stored, run `go test ./a` and assert exit 1, empty stdout.", [self.wrapped(a, 1)]),
            ("(run `go test ./a` and assert exit 2, empty stdout).", [self.wrapped(a, 2)])):
            with self.subTest(method=method):
                self.assertEqual(expected, plan.commands(method))

    BARE = (
        # Not one run list: no run verb, a gap other than and/",", a quoted literal after the commands.
        ("`go test ./a` and `go test ./b` and assert exit 2, empty stdout.", ["go test ./a", "go test ./b"]),
        ("Run `go test ./a`; run `go test ./b` and assert exit 2, empty stdout.", ["go test ./a", "go test ./b"]),
        ("Run `python3 -m notes add x`, then run `python3 -m notes add` and assert exit 2, empty stdout.",
         ["python3 -m notes add x", "python3 -m notes add"]),
        ("Run `go test ./a`, then run `./notes add` and assert exit 2, empty stdout.", ["go test ./a"]),
        # Statuses that do not fit: a count other than one or one per command, a status outside 0-255.
        ("Run `go test ./a` and `go test ./b` and `go test ./c` and assert exit codes 2/2, empty stdout.",
         ["go test ./a", "go test ./b", "go test ./c"]),
        ("Run `go test ./a` and assert exit 256, empty stdout.", ["go test ./a"]),
        # Text after the status that is not plain clauses introduced by , ; or "and".
        ("Run `go test ./a` and assert exit 2 with empty stdout.", ["go test ./a"]),
        ("Run `go test ./a` and assert exit 2, as documented.", ["go test ./a"]),
        ("Run `go test ./a` and assert exit 2, empty stdout)", ["go test ./a"]),
        ("Run `go test ./a` and assert exit 2, empty stdout and no ` in the output file.", ["go test ./a"]),
        ("Run `go test ./a` and `go test ./b` and assert exit 2, empty stdout and an error line for case 0.",
         ["go test ./a", "go test ./b"]),
        ("Run `python3 -m notes add` and `python3 -m notes list` and assert exit 2, empty stdout. The list output "
         "names each note.", ["python3 -m notes add", "python3 -m notes list"]),
        ("Run `python3 -m notes add` and `python3 -m notes list` and assert exit 2, empty stdout\nThe list output "
         "names each note", ["python3 -m notes add", "python3 -m notes list"]),
        # The status comes right after the commands, not after more prose about them.
        ("Run `python3 -m notes add x` and `python3 -m notes frobnicate` and read the add's output, then assert "
         "exit 2, empty stdout.", ["python3 -m notes add x", "python3 -m notes frobnicate"]),
        # Denied words in any case; a sentence ending in ! or ?; a status with thousands of digits.
        ("Run `go test ./a` and `go test ./b` and assert exit 2, EXCEPT the stdout.", ["go test ./a", "go test ./b"]),
        ("Run `go test ./a` and assert exit 2, empty stdout! Then read the file.", ["go test ./a"]),
        ("Run `go test ./a` and assert exit 2, empty stdout? Read the file.", ["go test ./a"]),
        ("Run `go test ./a` and assert exit " + "2" * 5000 + ", empty stdout.", ["go test ./a"]))

    def test_anything_else_after_a_run_list_leaves_the_commands_bare_without_raising(self):
        for method, expected in self.BARE:
            with self.subTest(method=method):
                self.assertEqual(expected, plan.commands(method))

    # Words that could state, qualify or redirect a status, or single out one command. HEAD left all of these bare.
    NOT_PLAIN = (
        "zero one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen "
        "seventeen eighteen nineteen twenty thirty forty fifty sixty seventy eighty ninety hundred once twice "
        "exit exits exited exiting return returns returned returning code codes status statuses succeed succeeds "
        "succeeded success successful successfully pass passes passed passing fail fails failed failing failure "
        "failures otherwise unless except but only first second third last initial former latter respectively "
        "other non-zero nonzero or if when whenever instead else excluding exclude excludes aside save besides "
        "exception alone just final preceding previous earlier provided given assuming until while depending "
        "alternatively either possibly skip skips skipping").split()
    PLAIN = "stdout stderr output outputs file files content contents message messages line lines usage error " \
            "errors notes".split()

    def test_a_clause_with_a_status_word_number_ordinal_or_condition_leaves_the_commands_bare(self):
        two = ["go test ./a", "go test ./b"]
        for word in self.NOT_PLAIN:
            method = f"Run `go test ./a` and `go test ./b` and assert exit 2, empty stdout and {word} in the file."
            with self.subTest(word=word):
                self.assertEqual(two, plan.commands(method))

    def test_a_long_run_of_spaces_after_the_status_is_read_at_once(self):
        # A regex here once backtracked as about n**4.6 on such input; this method took hours, now microseconds.
        self.assertEqual([self.wrapped("go test ./a", 2)], plan.commands(
            "Run `go test ./a` and assert exit 2, empty stdout" + " " * 3000 + "x"))
        self.assertEqual(["go test ./a"], plan.commands(
            "Run `go test ./a` and assert exit 2, empty stdout" + " " * 3000 + "unless asked"))
        self.assertEqual([self.wrapped("go test ./a", 2)], plan.commands(
            "Run `go test ./a` and assert exit 2, empty stdout" + " " * 3000 + "."))

    def test_each_clause_must_state_plain_output_or_files(self):
        for word in self.PLAIN:
            with self.subTest(word=word):
                self.assertEqual([self.wrapped("go test ./a", 2)], plan.commands(
                    f"Run `go test ./a` and assert exit 2, the {word} as documented."))
        self.assertEqual(["go test ./a"], plan.commands(
            "Run `go test ./a` and assert exit 2, empty stdout and the store as documented."))

    @staticmethod
    def head_assertion_commands(method, commands):
        """assertion_commands at 6cbf805, verbatim, as the reference for every method it read."""
        if not commands:
            return commands
        snippets = list(re.finditer(r"`([^`]+)`", method))
        if not snippets:
            return commands
        declaration = HEAD_DECLARATION.fullmatch(method[snippets[-1].end():])
        if not declaration:
            return commands
        codes = [int(value.strip()) for value in re.split(r"[/,]", declaration["codes"])]
        quoted = [snippet.group(1).strip() for snippet in snippets]
        if quoted != commands or len(codes) != len(commands) or any(not 0 <= code <= 255 for code in codes):
            raise ValueError("Planned exit codes need one status (0–255) per executable command; "
                             "use an explicit zero-exit assertion check for more complex expectations")
        return [command if code == 0 else "sh -c " + shlex.quote(
            f'({command}); autocode_plan_exit=$?; test "$autocode_plan_exit" -eq {code}')
            for command, code in zip(commands, codes)]

    def test_every_method_reads_as_before_or_gains_only_the_narrow_wrapping(self):
        # Skeptic probes (g2skeptic, gatecheck, 2026-10-06) and this module's own cases: never a new error, and
        # a changed result only wraps the commands HEAD returned bare.
        self.assertEqual(HEAD_DECLARATION.pattern, plan.expectations.DECLARATION.pattern)
        methods = dict.fromkeys(PROBES + [method for method, _ in self.BARE] + [self.LIVE_S4]
                                + [f"Run `go test ./a` and `go test ./b` and assert exit 2, the file says {word}."
                                   for word in self.NOT_PLAIN])
        changed = set()
        for method in methods:
            with self.subTest(method=method):
                with mock.patch.object(plan.expectations, "assertion_commands", self.head_assertion_commands):
                    try:
                        before = plan.commands(method)
                    except ValueError as error:
                        before = error
                if isinstance(before, ValueError):
                    with self.assertRaisesRegex(ValueError, "one status.*per executable command"):
                        plan.commands(method)
                    continue
                after = plan.commands(method)
                self.assertEqual(len(before), len(after))
                for old, new in zip(before, after):
                    self.assertIn(new, [old] + [self.wrapped(old, code) for code in range(1, 256)])
                if before != after:
                    changed.add(method)
        self.assertEqual({self.LIVE_S4, *(f"Run `python3 -m notes add` {rest}" for rest in (
            "and expect exit code 2 and no output.", "and assert exit code 2 and empty stdout.",
            "and assert exit 2 and empty stdout.", "then assert exit 2, empty stdout.",
            "and assert exit 2, empty stdout and an unchanged notes.json."))}, changed)

    def test_approved_contracts_never_raise_where_they_did_not_before(self):
        # autopilot.prepare_request builds prompts from these outside dispatch's try.
        methods = []
        for method in PROBES + [method for method, _ in self.BARE]:
            with mock.patch.object(plan.expectations, "assertion_commands", self.head_assertion_commands):
                try:
                    plan.commands(method)
                except ValueError:
                    continue
            methods.append(method)
        state = {"goal_contract": {"hash": "h", "body": {"acceptance_criteria": [
            {"id": f"C{index}", "criterion": "c", "verification_method": method}
            for index, method in enumerate(methods)]}}}
        commands = plan.approved_commands(state)
        self.assertIn(self.wrapped("python3 -m notes frobnicate", 2), commands)
        self.assertEqual(commands, plan.launch_commands(state))
        self.assertEqual(commands, plan.obligations(state)["required_commands"])

    def test_later_milestone_commands_are_not_forced_on_the_current_task(self):
        state = {"goal_contract": {"body": {"acceptance_criteria": [
            {"id": "C1", "verification_method": "go test ./first"},
            {"id": "C2", "verification_method": "go test ./later"}]}},
            "current_task": {"acceptance_criteria": ["C1"], "validation_plan": ["go test ./first"]}}
        self.assertEqual(["go test ./first"], plan.approved_commands(state))

    def test_visual_check_alias_is_prescribed_without_admitting_other_task_commands(self):
        command = "autocode visual-check --policy visual-policy.json --policy-sha256 " + "a" * 64
        self.assertEqual([command], plan.commands(command))
        self.assertEqual(["/venv/bin/" + command], plan.commands("Run `/venv/bin/" + command + "`"))
        self.assertEqual([], plan.commands(command + " and inspect the screenshots"))
        self.assertEqual([], plan.commands("autocode ui 'Design a dashboard'"))
        self.assertEqual([], plan.commands("autocode 'Build an application'"))
        state = {"goal_contract": {"body": {"acceptance_criteria": [
            {"id": "visual", "human_review": False, "verification_method": command}]}},
            "current_task": {"acceptance_criteria": ["visual"], "validation_plan": ["python -m unittest"]}}
        self.assertEqual(["python -m unittest", command], plan.approved_commands(state))

    def test_discovery_only_requires_packages_between_start_and_explicit_top(self):
        for command, expected in (
            ("python3 -m unittest discover -s tests -t .", ["tests/__init__.py"]),
            ("python3 -m unittest discover -s src/tests -t src", ["src/tests/__init__.py"]),
            ("python3 -m unittest discover --start-directory=a/b --top-level-directory=.",
             ["a/__init__.py", "a/b/__init__.py"]),
            ("python3 -m unittest discover -s tests", []),
            ("python3 -m unittest discover -s . -t .", [])):
            with self.subTest(command=command):
                self.assertEqual(expected, plan.package_markers(command))


class ScopeTests(unittest.TestCase):
    def test_missing_initializer_must_be_assigned_before_the_plan_is_approved(self):
        with tempfile.TemporaryDirectory() as workspace:
            value = body()
            value["acceptance_criteria"][0]["verification_method"] = "python3 -m unittest discover -s tests -t ."
            value["milestones"][0]["affected_paths"] = ["greet.py", "tests/test_greet.py"]
            state = {"workspace": workspace}
            with self.assertRaisesRegex(ValueError, "requires tests/__init__.py.*outside affected_paths"):
                lifecycle.validate_body(state, value)
            self.assertNotIn("goal_contract", state)
            value["milestones"][0]["affected_paths"].append("tests/__init__.py")
            lifecycle.validate_body(state, value)
            value["milestones"][0]["affected_paths"] = ["greet.py", "tests/"]
            lifecycle.validate_body(state, value)
            Path(workspace, "tests").mkdir()
            Path(workspace, "tests/__init__.py").write_text("")
            value["milestones"][0]["affected_paths"] = ["greet.py", "tests/test_greet.py"]
            lifecycle.validate_body(state, value)


class PlannerGuidanceTests(unittest.TestCase):
    def test_the_planner_is_told_the_form_the_runner_asserts(self):
        from units import autoplanner
        rule = " ".join(autoplanner.EXAMPLE_CRITERIA_RULE.split())
        self.assertIn('A command that must fail (a usage error, a refused input) is checked inside the criterion\'s '
                      'test; if a verification_method does name such a command, end it with "and assert exit N" '
                      'right after the commands (N/M with one status per command for several)', rule)
        wrapped = CommandsTests.wrapped
        self.assertEqual([wrapped("python3 -m notes add", 2)],
                         plan.commands("Run `python3 -m notes add` and assert exit 2"))
        self.assertEqual([wrapped("python3 -m notes add", 2), wrapped("python3 -m notes frobnicate", 2)],
                         plan.commands("Run `python3 -m notes add` and `python3 -m notes frobnicate` "
                                       "and assert exit 2/2"))


class UsageErrorReplayTests(unittest.TestCase):
    def test_clean_replay_of_the_live_usage_error_method_passes_only_for_exit_2(self):
        with tempfile.TemporaryDirectory() as temp:
            workspace = Path(temp).resolve() / "project"
            (workspace / "notes").mkdir(parents=True)
            main = workspace / "notes" / "__main__.py"
            main.write_text("import sys\nif sys.argv[1:2] != ['list']:\n"
                            "    print('usage: notes list', file=sys.stderr)\n    sys.exit(2)\n")
            git = lambda *a: subprocess.run(["git", "-C", str(workspace), *a], check=True, capture_output=True)
            git("init", "-q")
            git("add", ".")
            git("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "base")
            state = {"goal_contract": {"body": {"acceptance_criteria": [
                {"id": "S4", "verification_method": CommandsTests.LIVE_S4}]}}}
            args = ([], workspace, Path(temp) / "run", {"output": "sol-01.json", "source_revision": "r"},
                    verify.scratch_run)
            result = check_replay.replay(*args, approved_state=state, timeout=60)
            self.assertEqual("PASS", result["verdict"])
            self.assertEqual(2, len(result["checks"]))
            main.write_text("print('usage: notes list')\n")  # a CLI that accepts usage errors must fail
            with self.assertRaisesRegex(ValueError, "notes add.*exited 1"):
                check_replay.replay(*args, approved_state=state, timeout=60)
