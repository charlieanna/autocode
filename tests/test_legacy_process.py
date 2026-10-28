"""Conservative reader/writer classification for the legacy process guard."""

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_legacy_process as legacy
import autocode_process as processes

RUNNER = "/usr/bin/python3 /ws/autocode/tools/autocode.py"
PATHS = "--workspace /ws/project --run-dir /ws/project/.autocode/runs/run-x"


class RunnerCommandTests(unittest.TestCase):
    def test_canonical_status_is_read_only(self):
        self.assertFalse(legacy.duplicate_runner_command(f"{RUNNER} --status {PATHS}"))

    def test_canonical_intervention_inspect_is_read_only(self):
        self.assertFalse(legacy.duplicate_runner_command(f"{RUNNER} intervention inspect {PATHS} --json"))
        self.assertFalse(
            legacy.duplicate_runner_command(
                f"{RUNNER} intervention inspect --json --run-dir=/ws/project/.autocode/runs/run-x --workspace=/ws/project"
            )
        )

    def test_inspector_requires_a_complete_unambiguous_reader_grammar(self):
        for args in (
            f"intervention inspect {PATHS}",
            "intervention inspect --json --workspace /ws/project",
            "intervention inspect --json --run-dir /ws/run",
            "intervention inspect --json --workspace relative --run-dir /ws/run",
            "intervention inspect --json --workspace /ws/project --run-dir relative",
            f"intervention inspect {PATHS} --json --json",
            f"intervention inspect {PATHS} --json=true",
            "intervention inspect --json --workspace= --run-dir=/ws/run",
            f"intervention inspect {PATHS} --json --workspace /ws/project",
            f"intervention inspect {PATHS} --json --run-dir /ws/run",
            f"intervention inspect {PATHS} --json --status",
            f"intervention inspect {PATHS} --json --dry-run",
            f"intervention inspect {PATHS} --json --inspect-evidence",
            f"intervention inspect {PATHS} --json --approve-goal token",
            f"intervention inspect {PATHS} --json --resume-paused",
            f"intervention inspect {PATHS} --json --request-milestone-checkpoints",
            f"intervention inspect {PATHS} --json --unknown",
            f"intervention inspect {PATHS} --json positional-task",
            f"intervention submit {PATHS} --json --request-id request --kind pause",
            f"bundle {PATHS} --json",
            "intervention inspect --json --workspace '/ws/project' --run-dir /ws/run",
            "intervention inspect --json --workspace /ws/project\\ space --run-dir /ws/run",
            f"intervention inspect {PATHS} --json\n--resume-paused",
        ):
            with self.subTest(args=args):
                self.assertTrue(legacy.duplicate_runner_command(f"{RUNNER} {args}"))

    def test_canonical_dry_run_is_read_only(self):
        self.assertFalse(legacy.duplicate_runner_command(f"{RUNNER} --dry-run {PATHS}"))

    def test_status_can_inspect_evidence(self):
        self.assertFalse(legacy.duplicate_runner_command(f"{RUNNER} --status {PATHS} --inspect-evidence"))

    def test_named_paths_accept_equals_and_either_order(self):
        self.assertFalse(
            legacy.duplicate_runner_command(
                f"{RUNNER} --status --run-dir=/ws/project/.autocode/runs/run-x --workspace=/ws/project"
            )
        )

    def test_python_bytecode_and_buffering_switches_are_structural(self):
        self.assertFalse(
            legacy.duplicate_runner_command(f"/usr/bin/python3 -B -u /ws/autocode/tools/autocode.py --status {PATHS}")
        )

    def test_missing_empty_relative_or_duplicate_paths_stay_blocking(self):
        for args in (
            "--status",
            "--status --workspace /ws/project",
            "--status --run-dir /ws/run",
            "--status --workspace /ws/project --run-dir",
            "--status --workspace= --run-dir=/ws/run",
            "--status --workspace=/ws/project --run-dir=",
            "--status --workspace relative --run-dir /ws/run",
            "--status --workspace /ws/project --run-dir relative",
            f"--status {PATHS} --workspace /ws/project",
            f"--status {PATHS} --run-dir /ws/run",
        ):
            with self.subTest(args=args):
                self.assertTrue(legacy.duplicate_runner_command(f"{RUNNER} {args}"))

    def test_unknown_duplicate_and_mixed_reader_flags_stay_blocking(self):
        for args in (
            f"--status {PATHS} --unknown",
            f"--status {PATHS} --json",
            f"--dry-run {PATHS} --json",
            f"--status {PATHS} --status",
            f"--status {PATHS} --dry-run",
            f"--dry-run {PATHS} --inspect-evidence",
            f"--status {PATHS} --inspect-evidence --inspect-evidence",
            f"--status=true {PATHS}",
        ):
            with self.subTest(args=args):
                self.assertTrue(legacy.duplicate_runner_command(f"{RUNNER} {args}"))

    def test_status_with_mutation_options_stays_blocking(self):
        for mutation in (
            "--request-milestone-checkpoints",
            "--approve-goal synthetic-token",
            "--show-goal",
            "--answer question answer",
            "--feedback text",
            "--resume-paused",
            "--migrate-only",
            "--follow-up text",
            "--test-command pytest",
            "--terra-model model",
        ):
            for reader in ("--status", "--dry-run"):
                with self.subTest(reader=reader, mutation=mutation):
                    self.assertTrue(legacy.duplicate_runner_command(f"{RUNNER} {reader} {PATHS} {mutation}"))

    def test_positional_task_and_embedded_status_text_stay_blocking(self):
        for args in (
            "--status .autocode/runs/run-x",
            f"status {PATHS}",
            f"task --status {PATHS}",
            f"--status {PATHS} task",
            f"-- --status {PATHS}",
            f"--feedback '--status {PATHS}'",
            "--workspace /ws/project --status --run-dir /ws/run",
        ):
            with self.subTest(args=args):
                self.assertTrue(legacy.duplicate_runner_command(f"{RUNNER} {args}"))

    def test_actual_script_slot_is_required(self):
        for command in (
            f"python3 -c 'autocode.py --status {PATHS}'",
            f"python3 helper.py autocode.py --status {PATHS}",
            f"python3 /ws/autocode/tools/autocode_builder_worker.py --status {PATHS}",
            f"python3 -m helper autocode.py --status {PATHS}",
        ):
            with self.subTest(command=command):
                self.assertTrue(legacy.duplicate_runner_command(command))

    def test_quoted_escaped_or_multiline_ps_text_stays_blocking(self):
        for command in (
            f"{RUNNER} --status --workspace '/ws/project' --run-dir /ws/run",
            f'{RUNNER} --status --workspace "/ws/project" --run-dir /ws/run',
            f"{RUNNER} --status --workspace /ws/project\\ space --run-dir /ws/run",
            f"{RUNNER} --status {PATHS}\n",
            f"{RUNNER} --status {PATHS}\r",
            f"{RUNNER} --status {PATHS} '",
        ):
            with self.subTest(command=command):
                self.assertTrue(legacy.duplicate_runner_command(command))

    def test_provider_commands_keep_blocking_even_with_status_tokens(self):
        for command in (
            "codex exec -C /ws/project -o /ws/project/.autocode/runs/run-x/report.json",
            f"codex exec --status {PATHS}",
            "codex exec resume session-id",
            f"opencode run --status {PATHS}",
            f"opencode.exe run --status {PATHS}",
        ):
            with self.subTest(command=command):
                self.assertTrue(legacy.duplicate_runner_command(command))

    def test_wrapper_shells_and_helper_app_prompt_text_are_not_runners(self):
        for command in (
            f"zsh -lc '{RUNNER} --status {PATHS}'",
            "sh /tmp/wrapper.sh --show-goal /ws/run",
            "/Applications/Helper.app/Contents/MacOS/Helper turn-ended "
            '{"input-messages":["python3 autocode.py --status codex exec"]}',
        ):
            with self.subTest(command=command):
                self.assertFalse(legacy.duplicate_runner_command(command))


class ProcessGuardTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="legacy-reader-guard-")
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve()
        self.run = self.workspace / ".autocode/runs/run-x"
        self.run.mkdir(parents=True)
        self.paths = f"--workspace {self.workspace} --run-dir {self.run}"
        self.row_pids: list[int] = []

    def listing(self, *commands):
        """A fake listing whose row pids are never this test's own runner or its parent.

        The guard skips both by design (pinned below), and a container test runner may
        really hold a low pid like 101 (#2446); assertions name the chosen pids.
        """
        reserved = {os.getpid(), os.getppid()}
        self.row_pids = []
        candidate = 101
        while len(self.row_pids) < len(commands):
            if candidate not in reserved:
                self.row_pids.append(candidate)
            candidate += 1
        return subprocess.CompletedProcess(
            ["ps"], 0, "".join(f"{pid} {command}\n" for pid, command in zip(self.row_pids, commands, strict=True)), ""
        )

    def test_failed_inspection_keeps_thirty_second_bound_and_only_bounded_first_stderr(self):
        detail = "ps: diagnostic " + "x" * 240
        outcomes = (
            (
                subprocess.TimeoutExpired(["ps"], 30, output="PRIVATE_OUTPUT", stderr="PRIVATE_STDERR"),
                "ps did not finish within 30 s",
            ),
            (OSError("PRIVATE_OS_ERROR"), "ps could not start: OSError"),
            (
                subprocess.CompletedProcess(["ps"], 7, "PRIVATE_OUTPUT", detail + "\nPRIVATE_SECOND_LINE\n"),
                "ps exited 7: " + detail[:200],
            ),
        )
        for outcome, expected in outcomes:
            effect = {"side_effect": outcome} if isinstance(outcome, BaseException) else {"return_value": outcome}
            with (
                self.subTest(expected=expected),
                patch.object(legacy.subprocess, "run", **effect) as listed,
                self.assertRaises(legacy.Paused) as caught,
            ):
                legacy.assert_no_legacy_process(self.run, self.workspace)
            self.assertEqual("PAUSED_PROCESS_CHECK", caught.exception.status)
            self.assertIn(expected, str(caught.exception))
            self.assertNotIn("PRIVATE_", str(caught.exception))
            self.assertNotIn(detail[:201], str(caught.exception))
            listed.assert_called_once_with(["ps", "-axo", "pid=,command="], capture_output=True, text=True, timeout=30)

    def test_listing_permission_denial_remains_workspace_lock_fallback(self):
        with patch.object(legacy.subprocess, "run", side_effect=OSError("operation not permitted PRIVATE")):
            legacy.assert_no_legacy_process(self.run, self.workspace)
        result = subprocess.CompletedProcess(["ps"], 1, "", "operation not permitted PRIVATE")
        with patch.object(legacy.subprocess, "run", return_value=result):
            legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_status_observer_does_not_block_a_writer(self):
        with patch.object(legacy.subprocess, "run", return_value=self.listing(f"{RUNNER} --status {self.paths}")):
            legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_dry_run_observer_does_not_block_a_writer(self):
        with patch.object(legacy.subprocess, "run", return_value=self.listing(f"{RUNNER} --dry-run {self.paths}")):
            legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_intervention_inspector_does_not_block_a_writer(self):
        command = f"{RUNNER} intervention inspect {self.paths} --json"
        with patch.object(legacy.subprocess, "run", return_value=self.listing(command)):
            legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_inspector_does_not_hide_a_real_writer_or_bundle_export(self):
        inspector = f"{RUNNER} intervention inspect {self.paths} --json"
        for writer in (f"{RUNNER} --approve-goal token {self.paths}", f"{RUNNER} bundle {self.run}"):
            with (
                self.subTest(writer=writer),
                patch.object(legacy.subprocess, "run", return_value=self.listing(inspector, writer)),
            ):
                with self.assertRaisesRegex(legacy.Paused, f"Existing run process {self.row_pids[1]}"):
                    legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_reader_does_not_hide_a_second_writer(self):
        with patch.object(
            legacy.subprocess,
            "run",
            return_value=self.listing(f"{RUNNER} --status {self.paths}", f"{RUNNER} --approve-goal token {self.paths}"),
        ):
            with self.assertRaisesRegex(legacy.Paused, f"Existing run process {self.row_pids[1]}"):
                legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_status_with_a_queued_mutation_still_blocks(self):
        command = f"{RUNNER} --status {self.paths} --request-milestone-checkpoints"
        with patch.object(legacy.subprocess, "run", return_value=self.listing(command)):
            with self.assertRaisesRegex(legacy.Paused, f"Existing run process {self.row_pids[0]}"):
                legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_owned_marker_still_blocks_before_reader_exemption(self):
        for owned in ([], [{"pid": 101}]):
            with (
                self.subTest(owned=owned),
                patch.object(Path, "exists", return_value=True),
                patch.object(legacy, "read", return_value={"processes": owned}),
                patch.object(processes, "live_processes", return_value=owned),
                patch.object(
                    legacy.subprocess, "run", return_value=self.listing(f"{RUNNER} --status {self.paths}")
                ) as listed,
            ):
                with self.assertRaisesRegex(legacy.Paused, "Provider commands from an earlier stage"):
                    legacy.assert_no_legacy_process(self.run, self.workspace)
                listed.assert_not_called()

    def test_dead_owned_marker_does_not_make_a_status_reader_a_writer(self):
        with (
            patch.object(Path, "exists", return_value=True),
            patch.object(legacy, "read", return_value={"processes": [{"pid": 101}]}),
            patch.object(processes, "live_processes", return_value=[]),
            patch.object(legacy.subprocess, "run", return_value=self.listing(f"{RUNNER} --status {self.paths}")),
        ):
            legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_a_failed_inspection_still_refuses_and_names_how_it_failed(self):
        # #1059: one generic refusal hid whether ps could not start, timed out or exited with an error.
        secret = "ghp_" + "S" * 36
        cases = {
            "could not start": (
                FileNotFoundError(2, "No such file or directory", "ps"),
                "ps could not start: No such file or directory",
            ),
            "timeout": (subprocess.TimeoutExpired(["ps"], 30), "ps did not finish within 30 s"),
            "exit": (
                subprocess.CompletedProcess(
                    ["ps"], 1, f"101 {RUNNER} --token {secret}\n", f"ps: sysctl failed {secret}\nmore\n"
                ),
                "ps exited 1: ps: sysctl failed [REDACTED]",
            ),
            "silent exit": (subprocess.CompletedProcess(["ps"], 1, "", ""), "ps exited 1)"),
        }
        for name, (outcome, cause) in cases.items():
            effect = {"side_effect": outcome} if isinstance(outcome, BaseException) else {"return_value": outcome}
            with (
                self.subTest(name),
                patch.object(legacy.subprocess, "run", **effect),
                self.assertRaises(legacy.Paused) as caught,
            ):
                legacy.assert_no_legacy_process(self.run, self.workspace)
            message = str(caught.exception)
            self.assertEqual("PAUSED_PROCESS_CHECK", caught.exception.status)
            self.assertTrue(message.startswith("Cannot inspect legacy workers ("), message)
            self.assertTrue(message.endswith("; refuse possible duplicate launch"), message)
            self.assertIn(cause, message)
            self.assertNotIn(secret, message)
            self.assertNotIn("--token", message)  # the listing never reaches the message

    def test_another_workspaces_relative_suffix_does_not_block_this_run(self):
        relative = os.path.relpath(self.run, self.workspace)
        with patch.object(
            legacy.subprocess, "run", return_value=self.listing(f"{RUNNER} --run-dir /another-workspace/{relative}")
        ):
            legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_mutation_continuation_cannot_hide_after_a_status_row(self):
        result = self.listing(f"{RUNNER} --status {self.paths}")
        result.stdout += "--request-milestone-checkpoints\n"
        with patch.object(legacy.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(legacy.Paused, f"Existing run process {self.row_pids[0]}"):
                legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_blank_continuation_keeps_status_text_ambiguous(self):
        result = self.listing(f"{RUNNER} --status {self.paths}")
        result.stdout += "\n"
        with patch.object(legacy.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(legacy.Paused, f"Existing run process {self.row_pids[0]}"):
                legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_non_numeric_continuation_is_not_a_new_process_row(self):
        result = self.listing(f"{RUNNER} --status {self.paths}")
        result.stdout += "not-a-pid --request-milestone-checkpoints\n"
        with patch.object(legacy.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(legacy.Paused, f"Existing run process {self.row_pids[0]}"):
                legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_numeric_continuation_without_a_command_stays_ambiguous(self):
        result = self.listing(f"{RUNNER} --status {self.paths}")
        result.stdout += "202\n"
        with patch.object(legacy.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(legacy.Paused, f"Existing run process {self.row_pids[0]}"):
                legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_malformed_leading_row_refuses_inspection_without_printing_arguments(self):
        result = self.listing(f"{RUNNER} --status {self.paths}")
        result.stdout = "not-a-pid unrelated-argument\n" + result.stdout
        with patch.object(legacy.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(legacy.Paused, "Cannot parse legacy workers") as caught:
                legacy.assert_no_legacy_process(self.run, self.workspace)
        self.assertEqual("PAUSED_PROCESS_CHECK", caught.exception.status)
        self.assertNotIn("unrelated-argument", str(caught.exception))

    def test_valid_own_and_parent_rows_remain_ignored(self):
        command = f"{RUNNER} --approve-goal token {self.paths}"
        result = subprocess.CompletedProcess(["ps"], 0, f"{os.getpid()} {command}\n{os.getppid()} {command}\n", "")
        with patch.object(legacy.subprocess, "run", return_value=result):
            legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_the_listing_fixture_still_names_a_writer_when_this_runner_holds_pid_101(self):
        """A container runner may really hold a low pid like 101 (#2446): the guard skips
        its own and its parent's rows by design, so the fixture's fake listing pids must
        never collide with either while still naming a foreign writer."""
        command = f"{RUNNER} --approve-goal token {self.paths}"
        with (
            patch("os.getpid", return_value=101),
            patch("os.getppid", return_value=102),
        ):
            result = self.listing(command)
            self.assertEqual([103], self.row_pids)
            with patch.object(legacy.subprocess, "run", return_value=result):
                with self.assertRaisesRegex(legacy.Paused, "Existing run process 103"):
                    legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_other_workspace_continuation_does_not_attach_to_next_reader(self):
        relative = os.path.relpath(self.run, self.workspace)
        result = subprocess.CompletedProcess(
            ["ps"],
            0,
            f"101 {RUNNER} --run-dir /another-workspace/{relative}\n"
            "--request-milestone-checkpoints\n"
            f"102 {RUNNER} --status {self.paths}\n",
            "",
        )
        with patch.object(legacy.subprocess, "run", return_value=result):
            legacy.assert_no_legacy_process(self.run, self.workspace)


if __name__ == "__main__":
    unittest.main()
