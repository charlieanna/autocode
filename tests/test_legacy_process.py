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

    def listing(self, *commands):
        return subprocess.CompletedProcess(
            ["ps"], 0, "".join(f"{101 + index} {command}\n" for index, command in enumerate(commands)), ""
        )

    def test_status_observer_does_not_block_a_writer(self):
        with patch.object(legacy.subprocess, "run", return_value=self.listing(f"{RUNNER} --status {self.paths}")):
            legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_dry_run_observer_does_not_block_a_writer(self):
        with patch.object(legacy.subprocess, "run", return_value=self.listing(f"{RUNNER} --dry-run {self.paths}")):
            legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_reader_does_not_hide_a_second_writer(self):
        with patch.object(
            legacy.subprocess,
            "run",
            return_value=self.listing(f"{RUNNER} --status {self.paths}", f"{RUNNER} --approve-goal token {self.paths}"),
        ):
            with self.assertRaisesRegex(legacy.Paused, "Existing run process 102"):
                legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_status_with_a_queued_mutation_still_blocks(self):
        command = f"{RUNNER} --status {self.paths} --request-milestone-checkpoints"
        with patch.object(legacy.subprocess, "run", return_value=self.listing(command)):
            with self.assertRaisesRegex(legacy.Paused, "Existing run process 101"):
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
            with self.assertRaisesRegex(legacy.Paused, "Existing run process 101"):
                legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_blank_continuation_keeps_status_text_ambiguous(self):
        result = self.listing(f"{RUNNER} --status {self.paths}")
        result.stdout += "\n"
        with patch.object(legacy.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(legacy.Paused, "Existing run process 101"):
                legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_non_numeric_continuation_is_not_a_new_process_row(self):
        result = self.listing(f"{RUNNER} --status {self.paths}")
        result.stdout += "not-a-pid --request-milestone-checkpoints\n"
        with patch.object(legacy.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(legacy.Paused, "Existing run process 101"):
                legacy.assert_no_legacy_process(self.run, self.workspace)

    def test_numeric_continuation_without_a_command_stays_ambiguous(self):
        result = self.listing(f"{RUNNER} --status {self.paths}")
        result.stdout += "202\n"
        with patch.object(legacy.subprocess, "run", return_value=result):
            with self.assertRaisesRegex(legacy.Paused, "Existing run process 101"):
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
