import io
import os
import unittest

import autocode_verbose as verbose


class VerboseTests(unittest.TestCase):
    def setUp(self):
        self.saved = os.environ.get(verbose.ENV)
        os.environ.pop(verbose.ENV, None)

    def tearDown(self):
        if self.saved is None:
            os.environ.pop(verbose.ENV, None)
        else:
            os.environ[verbose.ENV] = self.saved

    def test_on_by_default_and_off_only_when_turned_off(self):
        self.assertTrue(verbose.enabled())
        self.assertIsNotNone(verbose.reporter("builder", "gpt-x"))
        for value in ("", " ", "1", "yes"):
            with self.subTest(value=value):
                os.environ[verbose.ENV] = value
                self.assertTrue(verbose.enabled())
        for value in ("0", "false", "OFF", " no "):
            with self.subTest(value=value):
                os.environ[verbose.ENV] = value
                self.assertFalse(verbose.enabled())
                self.assertIsNone(verbose.reporter("builder", "gpt-x"))
        verbose.enable()
        self.assertTrue(verbose.enabled())
        verbose.disable()
        self.assertFalse(verbose.enabled())

    def test_cli_flags_override_the_variable_and_no_flag_keeps_it(self):
        import autocode_args
        import autocode_opencode as opencode

        def parse(*argv):
            autocode_args.parse(None, ["Add a task", *argv], opencode.DEFAULT_MODELS)
            return verbose.enabled()

        self.assertTrue(parse())
        self.assertFalse(parse("--no-verbose"))
        self.assertFalse(parse(), "no flag leaves the variable as it is")
        self.assertTrue(parse("--verbose"))
        os.environ[verbose.ENV] = "0"
        self.assertTrue(parse("--verbose"))

    def test_tool_and_text_lines_carry_stage_model_and_bounded_text(self):
        verbose.enable()
        out = io.StringIO()
        ticks = iter([0.0, 10.0])
        report = verbose.reporter("builder", "gpt-x", stream=out, clock=lambda: next(ticks))
        report("tool", "command started")
        report("text", "line one\nline two  " + "x" * 400)
        lines = out.getvalue().splitlines()
        self.assertEqual("builder/gpt-x: tool: command started", lines[0])
        self.assertTrue(lines[1].startswith("builder/gpt-x: line one line two "))
        self.assertTrue(lines[1].endswith("…"))
        self.assertLessEqual(len(lines[1]), len("builder/gpt-x: ") + 201)

    def test_text_is_rate_limited_but_tool_lines_are_not(self):
        verbose.enable()
        out = io.StringIO()
        ticks = iter([0.0, 0.1, 2.0, 2.1])
        report = verbose.reporter("tester", None, stream=out, clock=lambda: next(ticks))
        report("text", "first")
        report("text", "suppressed")
        report("tool", "tool finished")
        report("text", "later")
        lines = out.getvalue().splitlines()
        self.assertEqual(["tester: first", "tester: tool: tool finished", "tester: later"], lines)

    def test_repeated_heartbeat_lines_are_coalesced_into_one_counted_line(self):
        verbose.enable()
        out = io.StringIO()
        ticks = iter([0.0, 0.1, 0.2, 0.3, 1.5])
        report = verbose.reporter("planner", "gpt-x", stream=out, clock=lambda: next(ticks))
        for _ in range(4):
            report("progress", "native provider stream advanced")
        report("tool", "command started")
        lines = out.getvalue().splitlines()
        self.assertEqual(["planner/gpt-x: tool: command started"], lines)

    def test_progress_heartbeats_are_never_shown(self):
        verbose.enable()
        out = io.StringIO()
        report = verbose.reporter("planner", None, stream=out, clock=lambda: 0.0)
        for _ in range(4):
            report("progress", "native provider stream advanced")
        report("tool", "command started")
        self.assertEqual(["planner: tool: command started"], out.getvalue().splitlines())

    def test_repeated_tool_lines_still_print_after_one_second(self):
        verbose.enable()
        out = io.StringIO()
        ticks = iter([0.0, 1.5])
        report = verbose.reporter("planner", None, stream=out, clock=lambda: next(ticks))
        report("tool", "tool finished")
        report("tool", "tool finished")
        self.assertEqual(["planner: tool: tool finished", "planner: tool: tool finished"], out.getvalue().splitlines())

    def test_suppressed_repeats_of_other_lines_carry_their_count_to_the_next_line(self):
        verbose.enable()
        out = io.StringIO()
        ticks = iter([0.0, 0.1, 0.2, 1.5])
        report = verbose.reporter("planner", None, stream=out, clock=lambda: next(ticks))
        report("tool", "tool finished")
        report("tool", "tool finished")
        report("tool", "tool finished")
        report("tool", "command started")
        self.assertEqual(
            ["planner: tool: tool finished", "planner: tool: command started (+2 similar)"],
            out.getvalue().splitlines(),
        )

    def test_a_broken_stream_never_raises(self):
        verbose.enable()

        class Broken:
            def write(self, *_):
                raise OSError("eio")

            def flush(self):
                raise OSError("eio")

        report = verbose.reporter("builder", "m", stream=Broken(), clock=lambda: 0.0)
        report("tool", "command started")


class MonitorReportTests(unittest.TestCase):
    def test_activity_monitor_reports_tool_lifecycle_and_new_text(self):
        import tempfile
        from pathlib import Path

        import autocode_activity as activity

        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text(
                '{"type":"item.started","item":{"id":"a","type":"command_execution"}}\n'
                '{"type":"item.completed","item":{"id":"a","type":"command_execution"}}\n'
                '{"type":"item.updated","item":{"id":"b","type":"agent_message","text":"hello"}}\n'
                '{"type":"item.updated","item":{"id":"b","type":"agent_message","text":"hello world"}}\n'
            )
            monitor = activity.ActivityMonitor(
                path,
                idle_seconds=0,
                tool_seconds=0,
                clock=lambda: 1.0,
                reporter=lambda kind, detail: seen.append((kind, detail)),
            )
            monitor.poll()
        self.assertEqual(
            [("tool", "command started"), ("tool", "command finished"), ("text", "hello"), ("text", " world")], seen
        )

    def test_no_reporter_keeps_monitor_silent(self):
        import tempfile
        from pathlib import Path

        import autocode_activity as activity

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "events.jsonl"
            path.write_text('{"type":"item.started","item":{"id":"a","type":"tool_call"}}\n')
            monitor = activity.ActivityMonitor(path, idle_seconds=0, tool_seconds=0, clock=lambda: 1.0)
            snapshot = monitor.poll()
        self.assertEqual("running_tool", snapshot["activity"])
