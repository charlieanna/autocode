"""The runner re-runs the Validator's checks in a clean copy before a PASS counts."""
import json
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
import venv

from . import test_subprocess
import autocode_check_replay as check_replay
import autocode_verify as verify
import autocode_util as util


def receipt(exit_code=0, *, timed_out=False, error="", tail=""):
    return {"exit_code": exit_code, "timed_out": timed_out, "error": error, "tail": tail,
            "output": "/log", "output_sha256": "x", "duration_seconds": 0.1}


class ReplayTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run_dir = Path(temp.name)
        self.record = {"output": str(self.run_dir / "sol-01.json"), "source_revision": "rev1"}

    def replay(self, checks, results):
        calls = []

        def scratch_run(workspace, run_dir, *, command, timeout):
            calls.append(command)
            return results[command]
        return check_replay.replay(checks, "/ws", self.run_dir, self.record, scratch_run), calls

    def test_every_distinct_command_is_rerun_once_and_bound_to_the_source(self):
        checks = [{"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:a"},
                  {"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:b"},
                  {"command": "python cli.py --help", "exit_code": 0, "evidence_ref": "event:c"}]
        result, calls = self.replay(checks, {"pytest -q": receipt(), "python cli.py --help": receipt()})
        self.assertEqual(["pytest -q", "python cli.py --help"], calls)
        self.assertEqual(("PASS", "rev1", 3), (result["verdict"], result["source_revision"], len(result["checks"])))
        [saved_path] = (self.run_dir / "check-replay").glob("sol-01-*/replay.json")
        saved = json.loads(saved_path.read_text())
        self.assertEqual("PASS", saved["verdict"])

    def test_g1_a_passing_replay_still_runs_each_distinct_command_once_and_writes_a_pass_receipt(self):
        checks = [{"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:a"},
                  {"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:b"},
                  {"command": "python cli.py --help", "exit_code": 0, "evidence_ref": "event:c"}]
        result, calls = self.replay(checks, {"pytest -q": receipt(), "python cli.py --help": receipt()})
        self.assertEqual(["pytest -q", "python cli.py --help"], calls)
        self.assertEqual(("PASS", "rev1"), (result["verdict"], result["source_revision"]))
        self.assertEqual([0, 0, 0], [row["reported_exit_code"] for row in result["checks"]])
        receipts = list(self.run_dir.glob("check-replay/*/replay.json"))
        self.assertEqual(1, len(receipts), "one replay call writes exactly one receipt under check-replay/")
        self.assertEqual("PASS", json.loads(receipts[0].read_text())["verdict"])

    def test_a_check_that_does_not_reproduce_rejects_the_report_and_says_why(self):
        for result, words in ((receipt(1, tail="AssertionError: 3 != 4"), ["exited 1", "3 != 4"]),
                              (receipt(None, timed_out=True), ["timed out after 900 seconds"]),
                              (receipt(None, error="no such file"), ["could not run (no such file)"])):
            with self.subTest(words=words):
                with self.assertRaises(ValueError) as rejected:
                    self.replay([{"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:a"}],
                                {"pytest -q": result})
                message = str(rejected.exception)
                self.assertIn("`pytest -q` was reported as exit 0", message)
                for word in words:
                    self.assertIn(word, message)
                self.assertIn("clean checkout", message)

    def test_an_unrelated_success_does_not_replace_the_approved_command(self):
        calls = []
        state = {"current_task": {"validation_plan": ["python3 -m unittest test_greet.py"]}}
        def run(workspace, out, *, command, timeout):
            calls.append(command)
            return receipt(1 if "unittest" in command else 0, tail="wrong greeting")
        with self.assertRaisesRegex(ValueError, "test_greet.py.*exited 1"):
            check_replay.replay([{"command": "python3 -c 'print(1)'", "exit_code": 0,
                                  "evidence_ref": "event:a"}], "/ws", self.run_dir, self.record, run,
                                 approved_state=state)
        self.assertEqual(["python3 -c 'print(1)'", "python3 -m unittest test_greet.py"], calls)


class ReceiptCollisionTests(unittest.TestCase):
    """A later iteration's replay never overwrites an earlier cited receipt (issue #341).

    Stage attempt stems restart at 1 each iteration, so two Validator replays can share a
    stem; each replay call must therefore keep its own receipt and per-check-log directory.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run_dir = Path(temp.name)

    def scratch_runner(self, log_by_command, exit_by_command, calls):
        """A fake scratch runner that writes each command's log in the directory replay() gives it."""
        def scratch_run(workspace, folder, *, command, timeout):
            calls.append(command)
            folder = Path(folder)
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "scratch-command.log").write_text(log_by_command[command])
            return {"exit_code": exit_by_command.get(command, 0), "timed_out": False, "error": "",
                    "tail": log_by_command[command], "output": str(folder / "scratch-command.log"),
                    "output_sha256": "x", "duration_seconds": 0.1}
        return scratch_run

    @staticmethod
    def cited_receipt(message):
        return Path(message.split("Receipt: ", 1)[1].split(". Cite", 1)[0])

    def test_inv1_each_replay_call_writes_a_distinct_directory_and_earlier_receipts_keep_their_original_bytes(self):
        folders = []

        def scratch_run(workspace, folder, *, command, timeout):
            folders.append(Path(folder))
            folder.mkdir(parents=True, exist_ok=True)
            (folder / "scratch-command.log").write_text("log-first\n" if len(folders) == 1 else "log-second\n")
            return receipt()

        first = check_replay.replay([{"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:a"}],
                                    "/ws", self.run_dir,
                                    {"output": "iterations/001/validator-01.json", "source_revision": "first"},
                                    scratch_run)
        after_first = {path: path.read_bytes() for path in self.run_dir.glob("check-replay/*/replay.json")}
        first_log = folders[0] / "scratch-command.log"
        self.assertEqual(1, len(after_first))
        second = check_replay.replay([{"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:b"}],
                                     "/ws", self.run_dir,
                                     {"output": "iterations/002/validator-01.json", "source_revision": "second"},
                                     scratch_run)
        self.assertEqual(("PASS", "first"), (first["verdict"], first["source_revision"]))
        self.assertEqual(("PASS", "second"), (second["verdict"], second["source_revision"]))
        receipts = list(self.run_dir.glob("check-replay/*/replay.json"))
        self.assertEqual(2, len(receipts), "each replay call writes its own receipt")
        self.assertEqual(1, len([path for path in receipts if path not in after_first]),
                         "the second call's receipt is a different file")
        for path, original in after_first.items():
            self.assertEqual(original, path.read_bytes(), "an earlier receipt keeps exactly its original bytes")
        kept = json.loads(next(iter(after_first.values())))
        self.assertEqual(("PASS", "first"), (kept["verdict"], kept["source_revision"]))
        self.assertEqual("log-first\n", first_log.read_text(), "an earlier check log keeps exactly its original bytes")

    def test_t1_a_second_iterations_validator_replay_preserves_the_first_fail_receipt_and_log(self):
        cart, checkout, shipping, tax = ("python3 -m unittest tests.test_cart",
                                         "python3 -m unittest tests.test_checkout",
                                         "python3 -m unittest tests.test_shipping",
                                         "python3 -m unittest tests.test_tax")
        first_logs = {cart: "OK\n", checkout: "FAIL: test_checkout_total\nAssertionError: 120.00 != 119.99\n",
                      tax: "OK\n"}
        second_logs = {cart: "OK\n", shipping: "OK\n", tax: "OK\n"}
        first_calls, second_calls = [], []
        with self.assertRaises(ValueError) as rejected:
            check_replay.replay(
                [{"command": cart, "exit_code": 0, "evidence_ref": "event:cart"},
                 {"command": checkout, "exit_code": 0, "evidence_ref": "event:checkout"},
                 {"command": tax, "exit_code": 0, "evidence_ref": "event:tax"}],
                "/ws", self.run_dir,
                {"output": "iterations/001/validator-01.json", "source_revision": "rev-001"},
                self.scratch_runner(first_logs, {checkout: 1}, first_calls))
        cited = self.cited_receipt(str(rejected.exception))
        check_replay.replay(
            [{"command": cart, "exit_code": 0, "evidence_ref": "event:cart"},
             {"command": shipping, "exit_code": 0, "evidence_ref": "event:shipping"},
             {"command": tax, "exit_code": 0, "evidence_ref": "event:tax"}],
            "/ws", self.run_dir,
            {"output": "iterations/002/validator-01.json", "source_revision": "rev-002"},
            self.scratch_runner(second_logs, {}, second_calls))
        self.assertEqual([cart, checkout, tax], first_calls)
        self.assertEqual([cart, shipping, tax], second_calls)
        first = json.loads(cited.read_text())
        self.assertEqual(("FAIL", "rev-001"), (first["verdict"], first["source_revision"]))
        self.assertEqual(checkout, first["checks"][1]["command"])
        self.assertEqual("FAIL: test_checkout_total\nAssertionError: 120.00 != 119.99\n",
                         (cited.parent / "check-02/scratch-command.log").read_text())
        other = [path for path in self.run_dir.glob("check-replay/*/replay.json") if path != cited]
        self.assertEqual(1, len(other))
        second = json.loads(other[0].read_text())
        self.assertEqual(("PASS", "rev-002"), (second["verdict"], second["source_revision"]))
        self.assertEqual(shipping, second["checks"][1]["command"])
        self.assertEqual("OK\n", (other[0].parent / "check-02/scratch-command.log").read_text())

    def test_t2_a_second_iterations_report_repair_replay_preserves_the_first_fail_receipt_and_log(self):
        repair = "python3 repair_report.py"
        first_calls, second_calls = [], []
        with self.assertRaises(ValueError) as rejected:
            check_replay.replay([{"command": repair, "exit_code": 0, "evidence_ref": "event:repair"}],
                                "/ws", self.run_dir,
                                {"output": "iterations/001/validator-report-repair-01.json",
                                 "source_revision": "rev-repair-001"},
                                self.scratch_runner({repair: "repair failed\n"}, {repair: 1}, first_calls))
        cited = self.cited_receipt(str(rejected.exception))
        check_replay.replay([{"command": repair, "exit_code": 0, "evidence_ref": "event:repair"}],
                            "/ws", self.run_dir,
                            {"output": "iterations/002/validator-report-repair-01.json",
                             "source_revision": "rev-repair-002"},
                            self.scratch_runner({repair: "repair passed\n"}, {}, second_calls))
        first = json.loads(cited.read_text())
        self.assertEqual(("FAIL", "rev-repair-001"), (first["verdict"], first["source_revision"]))
        self.assertEqual("repair failed\n", (cited.parent / "check-01/scratch-command.log").read_text())
        other = [path for path in self.run_dir.glob("check-replay/*/replay.json") if path != cited]
        self.assertEqual(1, len(other))
        second = json.loads(other[0].read_text())
        self.assertEqual(("PASS", "rev-repair-002"), (second["verdict"], second["source_revision"]))
        self.assertEqual("repair passed\n", (other[0].parent / "check-01/scratch-command.log").read_text())


class ScratchReplayTests(unittest.TestCase):
    """The real scratch runner: the clean copy is the source as it is now, and only that."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.workspace = Path(temp.name).resolve() / "project"
        self.workspace.mkdir()
        git = lambda *a: subprocess.run(["git", "-C", str(self.workspace), *a], check=True, capture_output=True)
        git("init", "-q")
        (self.workspace / ".gitignore").write_text("local-only.txt\n")
        (self.workspace / "app.txt").write_text("committed\n")
        git("add", ".")
        git("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "base")
        (self.workspace / "new.txt").write_text("delivered, uncommitted\n")
        (self.workspace / "local-only.txt").write_text("the Validator's own setup\n")
        self.run_dir = Path(temp.name) / "run"

    def replay(self, command):
        return check_replay.replay([{"command": command, "exit_code": 0, "evidence_ref": "event:a"}],
                                   self.workspace, self.run_dir, {"output": "sol-01.json", "source_revision": "r"},
                                   verify.scratch_run, timeout=60)

    def test_multiline_and_plain_commands_have_separate_runtime_contexts(self):
        # Copied venv aliases have distinct paths even when their bytes match.
        # A multiline command falls back to the task's python, while the plain
        # command binds its explicit python3. Sharing just argv[0] is unsafe.
        with (self.workspace / ".gitignore").open("a") as stream:
            stream.write(".venv/\n")
        venv.EnvBuilder(with_pip=False, symlinks=False).create(self.workspace / ".venv")
        python = self.workspace / ".venv/bin/python3"
        multiline = shlex.join([str(python), "-c", "print('multiline')\n"])
        plain = shlex.join([str(python), "-c", "print('plain')"])
        self.run_dir.mkdir()
        events = self.run_dir / "validator.events"
        events.write_text("validator command execution\n")
        record = {"stage": "sol", "events": str(events), "output": "validator.json", "task_id": "T1",
                  "source_revision": util.snapshot(self.workspace)["revision"]}
        checks = [{"command": command, "exit_code": 0, "evidence_ref": "event:check"}
                  for command in (multiline, multiline, plain)]
        result = check_replay.replay(checks, self.workspace, self.run_dir, record, verify.scratch_run,
                                     execution_identity=verify.execution_identity, timeout=30)
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual(2, result["scheduling"]["executed_count"])
        first, duplicate, last = result["checks"]
        self.assertEqual(first["output"], duplicate["output"], "an identical command still executes once")
        self.assertEqual("multiline\n", Path(first["output"]).read_text())
        self.assertEqual("plain\n", Path(last["output"]).read_text())

    def test_later_replays_preserve_the_receipt_and_every_log_cited_by_a_rejection(self):
        variants = (
            ("iterations", "iterations/001/validator-01.json", "iterations/002/validator-01.json"),
            ("repairs", "iterations/001/validator-report-repair-01.json",
             "iterations/002/validator-report-repair-01.json"),
            ("same-attempt", "iterations/001/validator-01.json", "iterations/001/validator-01.json"),
            ("fallback", None, None),
        )
        for name, first_output, second_output in variants:
            with self.subTest(name=name):
                run = self.run_dir / name
                (self.workspace / "app.txt").write_text("first source\n")
                first_record = {"source_revision": util.snapshot(self.workspace)["revision"]}
                if first_output:
                    first_record["output"] = str(run / first_output)
                checks = [{"command": "printf 'original failure\\n'; exit 1", "exit_code": 0,
                           "evidence_ref": "event:first"},
                          {"command": "printf 'original second check\\n'", "exit_code": 0,
                           "evidence_ref": "event:second"}]
                with self.assertRaisesRegex(ValueError, "exited 1") as rejected:
                    check_replay.replay(checks, self.workspace, run, first_record, verify.scratch_run, timeout=30)
                cited = Path(str(rejected.exception).split("Receipt: ", 1)[1].split(". Cite", 1)[0])
                first = json.loads(cited.read_text())
                self.assertEqual("FAIL", first["verdict"])
                self.assertEqual([1, 0], [row["exit_code"] for row in first["checks"]])
                paths = [cited, *[Path(row["output"]) for row in first["checks"]]]
                original_bytes = {path: path.read_bytes() for path in paths}
                (self.workspace / "app.txt").write_text("second source\n")
                second_record = {"source_revision": util.snapshot(self.workspace)["revision"]}
                if second_output:
                    second_record["output"] = str(run / second_output)
                second = check_replay.replay(
                    [{"command": "printf 'later success\\n'", "exit_code": 0, "evidence_ref": "event:later"}],
                    self.workspace, run, second_record, verify.scratch_run, timeout=30)
                self.assertEqual("PASS", second["verdict"])
                self.assertNotEqual(first["source_revision"], second["source_revision"])
                for path, contents in original_bytes.items():
                    self.assertEqual(contents, path.read_bytes(), f"Later replay overwrote {path}")
                self.assertNotIn(second["checks"][0]["output"], [row["output"] for row in first["checks"]])

    def test_successful_replays_of_one_attempt_keep_independent_logs(self):
        first = self.replay("printf 'first successful check\\n'")
        original_log = Path(first["checks"][0]["output"])
        contents = original_log.read_bytes()
        second = self.replay("printf 'second successful check\\n'")
        self.assertEqual(["PASS", "PASS"], [first["verdict"], second["verdict"]])
        self.assertEqual(first["source_revision"], second["source_revision"])
        self.assertEqual(contents, original_log.read_bytes())
        self.assertNotEqual(str(original_log), second["checks"][0]["output"])

    def test_the_copy_has_committed_and_delivered_files_but_nothing_ignored(self):
        self.assertEqual("PASS", self.replay("test -f app.txt && test -f new.txt")["verdict"])
        with self.assertRaisesRegex(ValueError, "exited 1"):
            self.replay("test -f local-only.txt")

    def test_a_check_that_runs_git_status_is_refused_before_anything_runs(self):
        # A live skeleton's check ended in a grep over git status: it passed while its files were uncommitted, and
        # failed once the program committed and merged them, which undid a correct merge (2026-10-06).
        live = ("sh -c 'find notes tests -type f -not -name \"*.pyc\" | sort; "
                "git status --short --untracked-files=all | grep -v pycache'")
        for command in (live, "git -C . status --porcelain", "sh -c 'test -z \"$(git status --porcelain)\"'"):
            with self.subTest(command=command):
                with self.assertRaises(ValueError) as refused:
                    self.replay(command)
                self.assertIn("runs git status", str(refused.exception))
                self.assertIn(check_replay.WORKTREE_STATE_HINT, str(refused.exception))
        self.assertFalse(self.run_dir.exists() and any(self.run_dir.rglob("check-01")))  # refused, never run
        for command in ("git log -1 --format=%s", "sh -c 'git ls-files | grep -v status'", "test -f app.txt"):
            with self.subTest(command=command):
                self.assertEqual("PASS", self.replay(command)["verdict"])
        self.assertIn("Never cite a check that runs git status", check_replay.VALIDATOR_NOTE)

    def test_file_inventory_must_exclude_git_metadata_even_when_it_is_a_file(self):
        code = ("from pathlib import Path; "
                "found = {p.name for p in Path('.').iterdir() if p.is_file() and p.name != 'local-only.txt'}; "
                "assert found == {'.gitignore', 'app.txt', 'new.txt'}, found")
        command = shlex.join([sys.executable, "-c", code])
        original = subprocess.run([sys.executable, "-c", code], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(0, original.returncode, original.stderr)
        with self.assertRaisesRegex(ValueError, "exited 1") as rejected:
            self.replay(command)
        self.assertIn(".git", str(rejected.exception))
        portable = code.replace("p.name != 'local-only.txt'", "p.name not in {'local-only.txt', '.git'}")
        self.assertEqual("PASS", self.replay(shlex.join([sys.executable, "-c", portable]))["verdict"])
        self.assertIn(".git may be a file or a directory", check_replay.VALIDATOR_NOTE)

    def test_a_check_reading_run_files_is_rejected_with_what_to_cite_instead(self):
        # Fix run B, 2026-09-29: a Validator re-read the regression proof from .autocode/ as its check.
        proof = self.workspace / ".autocode" / "runs" / "r" / "regression" / "proof-02" / "verification.json"
        proof.parent.mkdir(parents=True)
        proof.write_text('{"verdict": "PASS"}\n')
        with self.assertRaises(ValueError) as rejected:
            self.replay(f"cat {proof.relative_to(self.workspace)}")
        self.assertIn(check_replay.RUN_FILES_HINT, str(rejected.exception))
        with self.assertRaises(ValueError) as unrelated:
            self.replay("test -f local-only.txt")
        self.assertNotIn(check_replay.RUN_FILES_HINT, str(unrelated.exception))

    def test_documentation_command_template_is_checked_as_text_without_execution(self):
        (self.workspace / "README.md").write_text("Use python3 -m temperature VALUE UNIT\n")
        command = "python3 -c \"from pathlib import Path; assert 'python3 -m temperature VALUE UNIT' in Path('README.md').read_text()\""
        state = {"current_task": {"validation_plan": [
            "Inspect README.md and verify the exact text `python3 -m temperature VALUE UNIT` "
            "without invoking the metavariable template as a command."]}}
        result = check_replay.replay([{"command": command, "exit_code": 0, "evidence_ref": "event:a"}],
                                    self.workspace, self.run_dir,
                                    {"output": "sol-01.json", "source_revision": "r"}, verify.scratch_run,
                                    approved_state=state)
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual([command], [row["command"] for row in result["checks"]])

    def test_approved_negative_cli_probes_enforce_the_declared_exit_codes(self):
        # Recorded adaptive greeting run: the Validator's assertion probe passed,
        # but plan-derived usage-error commands were incorrectly replayed as exit 0.
        (self.workspace / "greet.py").write_text(
            "import sys\n"
            "if len(sys.argv) != 2 or not sys.argv[1]:\n"
            "    print('usage: greet.py NAME', file=sys.stderr)\n"
            "    sys.exit(2)\n"
            "print('Hello, ' + sys.argv[1])\n")
        state = {"current_task": {"validation_plan": [
            'Run `python3 greet.py Alice`, `python3 greet.py`, `python3 greet.py Alice Bob`, '
            '`python3 greet.py Zoë`, `python3 greet.py -Ada` and `python3 greet.py " "` '
            'directly and confirm exact stdout/stderr bytes and exit codes 0/2/2/0/0/0.']}}
        result = check_replay.replay(
            [{"command": "python3 greet.py Alice", "exit_code": 0, "evidence_ref": "event:a"}],
            self.workspace, self.run_dir, {"output": "sol-01.json", "source_revision": "r"},
            verify.scratch_run, approved_state=state)
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual(6, len(result["checks"]), "every planned probe still runs")
        # A broken CLI returning success for usage errors must fail the same plan.
        path = self.workspace / "greet.py"
        path.write_text(path.read_text().replace("sys.exit(2)", "sys.exit(0)"))
        with self.assertRaises(ValueError):
            check_replay.replay([], self.workspace, self.run_dir,
                                {"output": "broken.json", "source_revision": "broken"},
                                verify.scratch_run, approved_state=state)

    def test_a_validators_wrong_zero_exit_claim_is_still_rejected(self):
        (self.workspace / "greet.py").write_text("raise SystemExit(2)\n")
        with self.assertRaisesRegex(ValueError, "reported as exit 0.*exited 2"):
            self.replay("python3 greet.py")

    def test_an_explicit_shell_exit_cannot_bypass_the_expected_status_assertion(self):
        for actual in (2, 0):
            with self.subTest(actual=actual):
                # Inner quotes and an explicit shell exit must stay inside the assertion.
                method = f"Run `sh -c 'printf \"literal $HOME\\n\"; exit {actual}'` and confirm exit code 2."
                state = {"current_task": {"validation_plan": [method]}}
                args = ([], self.workspace, self.run_dir,
                        {"output": f"shell-{actual}.json", "source_revision": "r"}, verify.scratch_run)
                if actual == 2:
                    self.assertEqual("PASS", check_replay.replay(*args, approved_state=state)["verdict"])
                else:
                    with self.assertRaises(ValueError):
                        check_replay.replay(*args, approved_state=state)

    def test_a_command_naming_the_workspace_runs_against_the_copy(self):
        self.replay(f"cat {self.workspace}/new.txt && touch {self.workspace}/written-by-check.txt")
        self.assertFalse((self.workspace / "written-by-check.txt").exists(), "a replay never writes the workspace")


class CliTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")

    def test_a_completed_run_shows_the_checks_the_runner_reran(self):
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        self.launch(["Build greeting", "--chat"], 0, answers="CLI\nyes\n")
        run, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        evidence = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]["evidence"]
        replay = evidence["check_replay"]
        self.assertEqual("PASS", replay["verdict"])
        self.assertEqual(state["validation"]["source_revision"], replay["source_revision"])
        self.assertEqual([0], [row["exit_code"] for row in replay["checks"]])
        self.assertIn("greet.py", replay["checks"][0]["command"])

    def test_a_check_that_passes_only_in_the_validators_session_never_completes(self):
        self.env.update(AUTOCODE_FIXTURE_MODE="no-human", AUTOCODE_FIXTURE_UNREPRODUCIBLE_CHECK="1")
        self.launch(["Build greeting", "--chat"], 2, answers="CLI\nyes\n")
        _, state = self.saved()
        self.assertNotEqual("TASK_COMPLETE", state["status"])
        self.assertIn(state["status"], ("PAUSED_INVALID_OUTPUT", "PAUSED_REPEATED_FAILURE"))
        self.assertIn("`test -f .autocode/validator-only` was reported as exit 0", state["stop_reason"])
        self.assertIn("exited 1", state["stop_reason"])
        self.assertIsNone((state.get("validation") or {}).get("check_replay"),
                          "a validation whose check did not reproduce is never stored")


if __name__ == "__main__":
    unittest.main()


class ValidatorNoteTests(unittest.TestCase):
    """A live Validator cited a check exiting 1 as a negative control inside a PASS (parallel-diamond,
    2026-09-29); every Validator request now says how to write one that exits 0."""

    def test_the_validator_is_told_and_the_builder_is_not(self):
        from tests.test_bug_job import approved_small_fix
        from units import common
        state = approved_small_fix()
        schemas = Path(check_replay.__file__).with_name("autocode-schemas")
        state_path = Path(state["workspace"]) / "state.json"
        validator = common.execution_request(state, "sol", state_path, schemas)
        self.assertIn(check_replay.VALIDATOR_NOTE, validator.prompt.split("\nCURRENT HANDOFF DATA\n")[0])
        self.assertIn("sh -c '! python3", check_replay.VALIDATOR_NOTE)
        self.assertIn("no .autocode/", check_replay.VALIDATOR_NOTE)
        # Scratch stays inside the workspace under .autocode/: a live run (2026-10-01) lost three
        # validator attempts to OpenCode's external_directory denial over /tmp scratch paths.
        self.assertIn("Never use /tmp", check_replay.VALIDATOR_NOTE)
        self.assertEqual((len(validator.prompt.encode()) + 3) // 4, validator.metrics["estimated_prompt_tokens"])
        builder = common.execution_request(state, "terra", state_path, schemas)
        self.assertNotIn(check_replay.VALIDATOR_NOTE, builder.prompt)
