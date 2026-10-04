"""The runner re-runs the Validator's checks in a clean copy before a PASS counts."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from . import test_subprocess
import autocode_check_replay as check_replay
import autocode_verify as verify


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
        saved = json.loads((self.run_dir / "check-replay" / "sol-01" / "replay.json").read_text())
        self.assertEqual("PASS", saved["verdict"])

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

    def test_the_copy_has_committed_and_delivered_files_but_nothing_ignored(self):
        self.assertEqual("PASS", self.replay("test -f app.txt && test -f new.txt")["verdict"])
        with self.assertRaisesRegex(ValueError, "exited 1"):
            self.replay("test -f local-only.txt")

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


class IssueReplayDirectoriesTests(unittest.TestCase):
    """Issue #341: check replay receipts are overwritten by the next iteration's Validator replay.

    Each replay must write to a directory unique to its iteration and attempt, so a failed
    replay's receipt and logs survive later replays.
    """

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.run_dir = Path(temp.name)

    def test_t1_failed_replay_receipt_survives_next_iteration(self):
        """AC1: Two replays in different iterations get different replay.json files."""
        calls = []
        def scratch_run(workspace, run_dir, *, command, timeout):
            calls.append(command)
            if command == "pytest -q":
                return receipt(1, tail="failure")
            return receipt(0)

        # First replay: iterations/001/validator-01.json, will fail
        record1 = {"output": str(self.run_dir / "iterations" / "001" / "validator-01.json"),
                   "source_revision": "rev1"}
        try:
            check_replay.replay([{"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:a"}],
                               "/ws", self.run_dir, record1, scratch_run)
        except ValueError:
            pass

        # Second replay: iterations/002/validator-01.json, will pass
        record2 = {"output": str(self.run_dir / "iterations" / "002" / "validator-01.json"),
                   "source_revision": "rev2"}
        result2 = check_replay.replay([{"command": "python cli.py --help", "exit_code": 0, "evidence_ref": "event:b"}],
                                      "/ws", self.run_dir, record2, scratch_run)

        # Both replay.json files should exist and contain their respective revisions
        replay1_path = self.run_dir / "check-replay" / "001-validator-01" / "replay.json"
        replay2_path = self.run_dir / "check-replay" / "002-validator-01" / "replay.json"

        self.assertTrue(replay1_path.exists(), f"First replay.json should exist at {replay1_path}")
        self.assertTrue(replay2_path.exists(), f"Second replay.json should exist at {replay2_path}")

        replay1 = json.loads(replay1_path.read_text())
        replay2 = json.loads(replay2_path.read_text())

        self.assertEqual("FAIL", replay1["verdict"])
        self.assertEqual("rev1", replay1["source_revision"])
        self.assertEqual("PASS", replay2["verdict"])
        self.assertEqual("rev2", replay2["source_revision"])

    def test_t3_each_iteration_gets_its_own_replay_directory(self):
        """AC1: An empty run_dir with exactly two replay.json files, one containing '001' with rev1 and one containing '002' with rev2."""
        calls = []
        def scratch_run(workspace, run_dir, *, command, timeout):
            calls.append(command)
            return receipt(0)

        record1 = {"output": str(self.run_dir / "iterations" / "001" / "validator-01.json"),
                   "source_revision": "rev1"}
        check_replay.replay([{"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:a"}],
                           "/ws", self.run_dir, record1, scratch_run)

        record2 = {"output": str(self.run_dir / "iterations" / "002" / "validator-01.json"),
                   "source_revision": "rev2"}
        check_replay.replay([{"command": "python cli.py --help", "exit_code": 0, "evidence_ref": "event:b"}],
                           "/ws", self.run_dir, record2, scratch_run)

        # Count replay.json files
        replay_files = list((self.run_dir / "check-replay").glob("*/replay.json"))
        self.assertEqual(2, len(replay_files), f"Should have exactly 2 replay.json files, found: {replay_files}")

        paths = sorted([str(f.parent.name) for f in replay_files])
        self.assertIn("001", paths[0], "First directory should contain iteration '001'")
        self.assertIn("002", paths[1], "Second directory should contain iteration '002'")

    def test_t2_logs_of_failed_replay_are_not_mixed_with_later_replay(self):
        """AC2: First replay has checks a and b, second has only c; they should be in separate directories."""
        def scratch_run(workspace, run_dir, *, command, timeout):
            # Write command text to scratch-command.log
            log_path = run_dir / "scratch-command.log"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(command)

            # Fail only on 'b'
            if command == "b":
                return receipt(1, tail="failed")
            return receipt(0)

        # First replay with 'a' and 'b' will fail
        record1 = {"output": str(self.run_dir / "iterations" / "001" / "validator-01.json"),
                   "source_revision": "rev1"}
        try:
            check_replay.replay([{"command": "a", "exit_code": 0, "evidence_ref": "event:a"},
                                {"command": "b", "exit_code": 0, "evidence_ref": "event:b"}],
                               "/ws", self.run_dir, record1, scratch_run)
        except ValueError:
            pass

        # Second replay with only 'c' will pass
        record2 = {"output": str(self.run_dir / "iterations" / "002" / "validator-01.json"),
                   "source_revision": "rev2"}
        check_replay.replay([{"command": "c", "exit_code": 0, "evidence_ref": "event:c"}],
                           "/ws", self.run_dir, record2, scratch_run)

        # First directory should have check-01 and check-02
        dir1 = self.run_dir / "check-replay" / "001-validator-01"
        self.assertTrue((dir1 / "check-01" / "scratch-command.log").exists())
        self.assertTrue((dir1 / "check-02" / "scratch-command.log").exists())
        self.assertEqual("a", (dir1 / "check-01" / "scratch-command.log").read_text())
        self.assertEqual("b", (dir1 / "check-02" / "scratch-command.log").read_text())

        # Second directory should have only check-01
        dir2 = self.run_dir / "check-replay" / "002-validator-01"
        self.assertTrue((dir2 / "check-01" / "scratch-command.log").exists())
        self.assertEqual("c", (dir2 / "check-01" / "scratch-command.log").read_text())
        self.assertFalse((dir2 / "check-02").exists(), "Second replay should not have check-02")

    def test_t4_report_repair_receipt_survives_next_iteration(self):
        """AC3: Report repair stems also survive across iterations."""
        def scratch_run(workspace, run_dir, *, command, timeout):
            if command == "x":
                return receipt(1, tail="failed")
            return receipt(0)

        # First replay of report repair: iterations/001/validator-report-repair-01.json
        record1 = {"output": str(self.run_dir / "iterations" / "001" / "validator-report-repair-01.json"),
                   "source_revision": "rev1"}
        try:
            check_replay.replay([{"command": "x", "exit_code": 0, "evidence_ref": "event:a"}],
                               "/ws", self.run_dir, record1, scratch_run)
        except ValueError:
            pass

        # Second replay of report repair: iterations/002/validator-report-repair-01.json
        record2 = {"output": str(self.run_dir / "iterations" / "002" / "validator-report-repair-01.json"),
                   "source_revision": "rev2"}
        check_replay.replay([{"command": "y", "exit_code": 0, "evidence_ref": "event:b"}],
                           "/ws", self.run_dir, record2, scratch_run)

        # First repair receipt should still exist with its verdict
        replay1_path = self.run_dir / "check-replay" / "001-validator-report-repair-01" / "replay.json"
        replay1 = json.loads(replay1_path.read_text())
        self.assertEqual("FAIL", replay1["verdict"])
        self.assertEqual("rev1", replay1["source_revision"])

    def test_t5_reused_attempt_name_gets_a_fresh_replay_directory(self):
        """AC4: Same output path in later replay gets a fresh directory with numeric suffix."""
        def scratch_run(workspace, run_dir, *, command, timeout):
            if command == "x":
                return receipt(1, tail="failed")
            return receipt(0)

        # First replay: iterations/003/validator-01.json
        record = {"output": str(self.run_dir / "iterations" / "003" / "validator-01.json"),
                  "source_revision": "rev1"}
        try:
            check_replay.replay([{"command": "x", "exit_code": 0, "evidence_ref": "event:a"}],
                               "/ws", self.run_dir, record, scratch_run)
        except ValueError:
            pass

        # Second replay with same output path
        record2 = {"output": str(self.run_dir / "iterations" / "003" / "validator-01.json"),
                   "source_revision": "rev2"}
        result2 = check_replay.replay([{"command": "y", "exit_code": 0, "evidence_ref": "event:b"}],
                                      "/ws", self.run_dir, record2, scratch_run)

        # First replay.json should keep its verdict
        replay1_path = self.run_dir / "check-replay" / "003-validator-01" / "replay.json"
        replay1 = json.loads(replay1_path.read_text())
        self.assertEqual("FAIL", replay1["verdict"])
        self.assertEqual("rev1", replay1["source_revision"])

        # Second replay should be at a different path (with suffix)
        replay2_path = self.run_dir / "check-replay" / "003-validator-01-2" / "replay.json"
        replay2 = json.loads(replay2_path.read_text())
        self.assertEqual("PASS", replay2["verdict"])
        self.assertEqual("rev2", replay2["source_revision"])

        self.assertNotEqual(str(replay1_path), str(replay2_path), "Receipts should be at different paths")

    def test_t6_single_replay_still_reruns_each_distinct_command_once(self):
        """AC6: A single replay with duplicate commands still calls scratch_run for each distinct command once."""
        calls = []
        def scratch_run(workspace, run_dir, *, command, timeout):
            calls.append(command)
            return receipt(0)

        record = {"output": str(self.run_dir / "iterations" / "001" / "validator-01.json"),
                  "source_revision": "rev1"}
        result = check_replay.replay(
            [{"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:a"},
             {"command": "pytest -q", "exit_code": 0, "evidence_ref": "event:b"},
             {"command": "python cli.py --help", "exit_code": 0, "evidence_ref": "event:c"}],
            "/ws", self.run_dir, record, scratch_run)

        self.assertEqual("PASS", result["verdict"])
        self.assertEqual("rev1", result["source_revision"])
        self.assertEqual(3, len(result["checks"]))
        self.assertEqual(2, len(calls), "Should call scratch_run exactly twice for two distinct commands")
        self.assertEqual(["pytest -q", "python cli.py --help"], calls)

        # Should have exactly one replay.json under check-replay
        replay_files = list((self.run_dir / "check-replay").glob("*/replay.json"))
        self.assertEqual(1, len(replay_files), f"Should have exactly 1 replay.json, found: {replay_files}")


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
