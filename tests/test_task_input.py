"""Literal brief loading and its user-visible CLI/status contract."""

import hashlib
import io
import tempfile
import unittest
from pathlib import Path

import autocode_run_view as run_view
import autocode_task_input as task_input
import autocode_taskrun as taskrun

from tests import test_subprocess


class TaskInputTests(unittest.TestCase):
    def test_file_retains_utf8_bom_shell_syntax_and_mixed_newlines(self):
        text = "\ufeffFix `run()` and $(command) 'quotes' \"double\" café\r\n\n"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "brief.txt"
            path.write_bytes(text.encode("utf-8"))
            self.assertEqual(text, task_input.load(None, brief=None, brief_file=path, stdin=io.StringIO()))

    def test_stdin_reads_raw_utf8_instead_of_text_newline_conversion(self):
        text = " `code` $(literal) café\r\n\n"
        stream = io.TextIOWrapper(io.BytesIO(text.encode("utf-8")), encoding="utf-8", newline=None)
        self.assertEqual(text, task_input.load(None, brief="-", brief_file=None, stdin=stream))

    def test_text_and_positional_inputs_keep_their_original_text(self):
        text = "  quoted `name`\n"
        self.assertEqual(text, task_input.load(None, brief=text, brief_file=None, stdin=io.StringIO()))
        self.assertEqual(text, task_input.load(text, brief=None, brief_file=None, stdin=io.StringIO()))
        self.assertIsNone(task_input.load(None, brief=None, brief_file=None, stdin=io.StringIO()))

    def test_a_positional_task_that_names_a_file_is_refused_with_both_readings(self):
        """#2323: `autocode spec.md` started a run whose task was the literal words "spec.md"."""
        with tempfile.TemporaryDirectory() as temp:
            spec = Path(temp) / "spec.md"
            spec.write_text("Build a greeting CLI\n")
            with self.assertRaisesRegex(ValueError, "--brief-file .*spec.md; .*--brief .*spec.md"):
                task_input.load(str(spec), brief=None, brief_file=None, stdin=io.StringIO())
            # The same words as an explicit brief, a task with spaces, or a name that is no file stay literal.
            self.assertEqual(str(spec), task_input.load(None, brief=str(spec), brief_file=None, stdin=io.StringIO()))
            spaced = f"fix {spec}"
            self.assertEqual(spaced, task_input.load(spaced, brief=None, brief_file=None, stdin=io.StringIO()))
            missing = str(Path(temp) / "missing.md")
            self.assertEqual(missing, task_input.load(missing, brief=None, brief_file=None, stdin=io.StringIO()))

    def test_conflicting_inputs_are_rejected_before_reading_stdin(self):
        stream = io.StringIO("must remain unread")
        with self.assertRaisesRegex(ValueError, "positional task"):
            task_input.load("old task", brief="-", brief_file=None, stdin=stream)
        self.assertEqual(0, stream.tell())
        with self.assertRaisesRegex(ValueError, "cannot be combined"):
            task_input.load(None, brief="-", brief_file=Path("missing"), stdin=stream)
        self.assertEqual(0, stream.tell())

    def test_empty_and_invalid_utf8_inputs_are_rejected(self):
        for text in ("", " \r\n\t"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "non-whitespace"):
                task_input.load(None, brief="-", brief_file=None, stdin=io.StringIO(text))
        # #2404: a blank positional task started a worktree and a run; "" already stops at "a task is needed".
        for text in ("", "   ", " \r\n\t"):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "The task is blank"):
                task_input.load(text, brief=None, brief_file=None, stdin=io.StringIO())
        stream = io.TextIOWrapper(io.BytesIO(b"\xff"), encoding="utf-8")
        with self.assertRaises(UnicodeDecodeError):
            task_input.load(None, brief="-", brief_file=None, stdin=stream)

    def test_unencodable_argument_or_text_stream_is_rejected_before_assignment(self):
        for task, brief, stdin in (
            ("bad \udcff", None, io.StringIO()),
            (None, "bad \udcff", io.StringIO()),
            (None, "-", io.StringIO("bad \udcff")),
        ):
            with self.subTest(task=task, brief=brief), self.assertRaises(UnicodeEncodeError):
                task_input.load(task, brief=brief, brief_file=None, stdin=stdin)

    def test_legacy_status_remains_readable_for_unencodable_saved_task(self):
        state = {"task": "legacy \udcff"}
        self.assertEqual(state["task"], run_view.view(state)["task"])
        self.assertIsNone(run_view.view(state)["task_sha256"])
        self.assertEqual({"task": "legacy \udcff"}, state)

    def test_legacy_status_hash_identifies_saved_text_without_mutating_it(self):
        text = "Fix `run()`\r\n"
        state = {"task": text}
        shown = run_view.view(state)
        self.assertEqual(text, shown["task"])
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), shown["task_sha256"])
        self.assertEqual({"task": text}, state)
        self.assertIsNone(run_view.view({})["task_sha256"])


class TaskInputCliTests(unittest.TestCase):
    # Reuse only fixture setup and the subprocess launcher, not other test cases.
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    new_run_engine_args = test_subprocess.SubprocessFlow.new_run_engine_args

    def literal_brief(self):
        return (
            "Build greeting with `run()` and $(touch shell-expanded-marker).\r\n"
            "Preserve 'single' and \"double\" quotes, café, $HOME and trailing lines.\n\n"
        )

    def assert_public_brief(self, text):
        run = taskrun.TaskRun.attach(self.project, command=tuple(self.entry), env=self.env, cwd=self.root)
        shown = run.status()
        self.assertEqual(text.encode("utf-8"), shown["task"].encode("utf-8"))
        self.assertEqual(hashlib.sha256(text.encode("utf-8")).hexdigest(), shown["task_sha256"])
        self.assertFalse((self.root / "shell-expanded-marker").exists())
        self.assertFalse((self.project / "shell-expanded-marker").exists())

    def test_file_brief_round_trips_through_cli_and_public_status(self):
        text = self.literal_brief()
        path = self.root / "brief.txt"
        path.write_bytes(text.encode("utf-8"))
        self.launch(["--brief-file", str(path), "--no-chat"], 2)
        self.assert_public_brief(text)

    def test_stdin_brief_round_trips_through_cli_and_public_status(self):
        text = self.literal_brief()
        self.launch(["--brief", "-", "--no-chat"], 2, answers=text)
        self.assert_public_brief(text)

    def test_saved_run_rejects_stdin_brief_before_consuming_input(self):
        # A missing run is enough: argument validation must precede input and lookup.
        result = self.launch(["--run-dir", str(self.root / "missing"), "--brief", "-"], 2)
        self.assertIn("cannot accompany a saved run", result.stderr)
        self.assertFalse((self.project / ".autocode/runs").exists())


if __name__ == "__main__":
    unittest.main()
