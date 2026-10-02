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

    def test_reporter_is_absent_unless_enabled(self):
        self.assertFalse(verbose.enabled())
        self.assertIsNone(verbose.reporter('terra', 'gpt-x'))
        verbose.enable()
        self.assertTrue(verbose.enabled())
        self.assertIsNotNone(verbose.reporter('terra', 'gpt-x'))

    def test_tool_and_text_lines_carry_stage_model_and_bounded_text(self):
        verbose.enable()
        out = io.StringIO()
        ticks = iter([0.0, 10.0])
        report = verbose.reporter('terra', 'gpt-x', stream=out, clock=lambda: next(ticks))
        report('tool', 'command started')
        report('text', 'line one\nline two  ' + 'x' * 400)
        lines = out.getvalue().splitlines()
        self.assertEqual('terra/gpt-x: tool: command started', lines[0])
        self.assertTrue(lines[1].startswith('terra/gpt-x: line one line two '))
        self.assertTrue(lines[1].endswith('…'))
        self.assertLessEqual(len(lines[1]), len('terra/gpt-x: ') + 201)

    def test_text_is_rate_limited_but_tool_lines_are_not(self):
        verbose.enable()
        out = io.StringIO()
        ticks = iter([0.0, 0.1, 2.0])
        report = verbose.reporter('sol', None, stream=out, clock=lambda: next(ticks))
        report('text', 'first')
        report('text', 'suppressed')
        report('tool', 'tool finished')
        report('text', 'later')
        lines = out.getvalue().splitlines()
        self.assertEqual(['sol: first', 'sol: tool: tool finished', 'sol: later'], lines)

    def test_a_broken_stream_never_raises(self):
        verbose.enable()
        class Broken:
            def write(self, *_):
                raise OSError('eio')
            def flush(self):
                raise OSError('eio')
        report = verbose.reporter('terra', 'm', stream=Broken(), clock=lambda: 0.0)
        report('tool', 'command started')


class MonitorReportTests(unittest.TestCase):
    def test_activity_monitor_reports_tool_lifecycle_and_new_text(self):
        import tempfile
        from pathlib import Path
        import autocode_activity as activity
        seen = []
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            path.write_text(
                '{"type":"item.started","item":{"id":"a","type":"command_execution"}}\n'
                '{"type":"item.completed","item":{"id":"a","type":"command_execution"}}\n'
                '{"type":"item.updated","item":{"id":"b","type":"agent_message","text":"hello"}}\n'
                '{"type":"item.updated","item":{"id":"b","type":"agent_message","text":"hello world"}}\n')
            monitor = activity.ActivityMonitor(path, idle_seconds=0, tool_seconds=0,
                                               clock=lambda: 1.0,
                                               reporter=lambda kind, detail: seen.append((kind, detail)))
            monitor.poll()
        self.assertEqual([('tool', 'command started'), ('tool', 'command finished'),
                          ('text', 'hello'), ('text', ' world')], seen)

    def test_no_reporter_keeps_monitor_silent(self):
        import tempfile
        from pathlib import Path
        import autocode_activity as activity
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'events.jsonl'
            path.write_text('{"type":"item.started","item":{"id":"a","type":"tool_call"}}\n')
            monitor = activity.ActivityMonitor(path, idle_seconds=0, tool_seconds=0, clock=lambda: 1.0)
            snapshot = monitor.poll()
        self.assertEqual('running_tool', snapshot['activity'])
