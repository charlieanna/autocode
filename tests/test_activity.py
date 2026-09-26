"""Deterministic activity/deadline tests using raw provider events only."""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import json
from pathlib import Path
import sys
import tempfile
import unittest

_ROOT = _Path(__file__).resolve().parents[1] if _Path(__file__).name != 'live_trial.py' else _Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / 'tools', _ROOT / 'tests', _ROOT / 'tests' / 'fakes'):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
from autocode_activity import ActivityMonitor


class ActivityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'events.jsonl'
        self.now = 0
        self.monitor = ActivityMonitor(self.path, idle_seconds=5, tool_seconds=20, clock=lambda: self.now)

    def append(self, row):
        with self.path.open('ab') as stream:
            stream.write(json.dumps(row).encode() + b'\n')
        return self.monitor.poll()

    def codex(self, phase, identifier='call-1', kind='command_execution', **extra):
        return self.append({'type': 'item.' + phase, 'item': {'id': identifier, 'type': kind, **extra}})

    def opencode(self, status, identifier='part-1', **extra):
        return self.append({'type': 'tool_use', 'part': {'id': identifier, 'tool': 'read',
                                                       'state': {'status': status, **extra}}})

    def test_quiet_codex_tool_has_fixed_deadline_independent_of_idle(self):
        self.codex('started', command='a secret command')
        self.now = 6
        self.assertIsNone(self.monitor.expired())
        self.assertEqual('running_tool', self.monitor.snapshot()['activity'])
        self.now = 15
        self.codex('updated', status='running', aggregated_output='still working')
        self.codex('started')
        self.now = 20
        self.assertEqual('tool', self.monitor.expired()['kind'])
        self.assertEqual(20, self.monitor.snapshot()['tool_elapsed_seconds'])
        self.assertNotIn('secret', json.dumps(self.monitor.snapshot()))

    def test_all_codex_tool_types_receive_quiet_tool_grace(self):
        for index, kind in enumerate(ActivityMonitor.TOOL_TYPES):
            self.codex('started', identifier=str(index), kind=kind)
        self.now = 6
        self.assertIsNone(self.monitor.expired())
        self.assertEqual(len(ActivityMonitor.TOOL_TYPES), self.monitor.snapshot()['active_tool_count'])

    def test_opencode_non_bash_tool_updates_and_errors_do_not_reset_start(self):
        self.opencode('pending')
        self.now = 12
        self.opencode('running', output='output')
        self.assertEqual(12, self.monitor.snapshot()['tool_elapsed_seconds'])
        self.now = 16
        self.opencode('error', error='failed')
        self.assertEqual('waiting_for_provider', self.monitor.snapshot()['activity'])
        self.now = 19
        self.opencode('error', error='different error output')
        self.opencode('running')  # A delayed duplicate cannot reopen this tool.
        self.now = 21
        self.assertEqual('idle', self.monitor.expired()['kind'])

    def test_completed_only_events_show_activity_without_assuming_live_tool(self):
        self.now = 4
        self.opencode('completed')
        self.now = 8
        self.assertIsNone(self.monitor.expired())
        self.opencode('completed', output='updated output')
        self.now = 9
        self.assertEqual('idle', self.monitor.expired()['kind'])
        self.assertEqual(0, self.monitor.snapshot()['active_tool_count'])

    def test_new_provider_text_is_activity_but_duplicate_text_and_logs_are_not(self):
        self.now = 4
        self.codex('completed', kind='agent_message', text='Inspecting the tests')
        self.now = 8
        self.codex('completed', identifier='another-id', kind='agent_message', text='Inspecting the tests')
        self.append({'type': 'log', 'text': 'new output every poll'})
        self.append({'type': 'unrecognized', 'item': {'id': 'unseen', 'text': 'work'}})
        self.now = 9
        self.assertEqual('idle', self.monitor.expired()['kind'])

    def test_cumulative_repeated_text_does_not_continuously_refresh(self):
        self.codex('updated', kind='reasoning', text='Checking. ')
        self.now = 3
        self.codex('updated', kind='reasoning', text='Checking. Checking. ')
        self.now = 5
        self.assertEqual('idle', self.monitor.expired()['kind'])
        self.codex('updated', kind='reasoning', text='Checking. Checking. New finding.')
        self.assertIsNone(self.monitor.expired())

    def test_opencode_text_is_not_interpreted_as_a_tool_claim(self):
        self.append({'type': 'text', 'part': {'id': 'message', 'text': 'I am running tests now'}})
        self.assertEqual('provider_active', self.monitor.snapshot()['activity'])
        self.assertEqual(0, self.monitor.snapshot()['active_tool_count'])
        self.now = 5
        self.assertEqual('idle', self.monitor.expired()['kind'])

    def test_multiple_tools_use_earliest_outstanding_deadline(self):
        self.codex('started')
        self.now = 8
        self.codex('started', identifier='call-2')
        self.now = 18
        self.codex('completed')
        self.now = 21
        self.assertIsNone(self.monitor.expired())
        self.now = 28
        self.assertEqual('tool', self.monitor.expired()['kind'])

    def test_process_fallback_interval_is_fixed_despite_descendant_churn(self):
        self.monitor.poll(processes=[{'pid': 10}], root_pid=10)
        self.now = 4
        self.monitor.poll(processes=[{'pid': 10}, {'pid': 11}], root_pid=10)
        self.now = 18
        self.monitor.poll(processes=[{'pid': 10}, {'pid': 12}], root_pid=10)
        self.assertEqual(14, self.monitor.snapshot()['tool_elapsed_seconds'])
        self.now = 24
        self.assertEqual('tool', self.monitor.expired()['kind'])
        self.assertTrue(self.monitor.snapshot()['process_fallback'])

    def test_mcp_helpers_do_not_convert_provider_wait_to_tool_activity(self):
        root = {'pid': 10, 'state': 'S', 'executable': 'opencode'}
        helper = {'pid': 11, 'parent': 10, 'state': 'S', 'executable': 'mcp-server-darwin-arm64'}
        observed = self.monitor.poll(processes=[root, helper], root_pid=10)
        self.assertEqual('waiting_for_provider', observed['activity'])
        self.assertFalse(observed['process_fallback'])
        self.assertIsNone(observed['tool_elapsed_seconds'])
        self.now = 4
        self.monitor.poll(processes=[root, {**helper, 'pid': 12, 'executable': 'mcp-server'}], root_pid=10)
        self.now = 5
        self.assertEqual('idle', self.monitor.expired()['kind'])

    def test_real_or_unknown_children_below_mcp_helper_keep_tool_grace(self):
        for executable in ['sh', 'node', 'python3', None]:
            with self.subTest(executable=executable):
                self.now = 0
                monitor = ActivityMonitor(self.path, idle_seconds=5, tool_seconds=20, clock=lambda: self.now)
                helper = {'pid': 11, 'parent': 10, 'state': 'S', 'executable': 'mcp-server-darwin-arm64'}
                command = {'pid': 12, 'parent': 11, 'state': 'S', 'executable': executable}
                monitor.poll(processes=[helper, command], root_pid=10)
                self.now = 6
                observed = monitor.poll(processes=[helper, command], root_pid=10)
                self.assertEqual('running_tool', observed['activity'])
                self.assertTrue(observed['process_fallback'])
                self.assertIsNone(monitor.expired())
                self.now = 20
                self.assertEqual('tool', monitor.expired()['kind'])

    def test_explicit_mcp_call_still_receives_tool_deadline(self):
        helper = {'pid': 11, 'state': 'S', 'executable': 'mcp-server-darwin-arm64'}
        self.monitor.poll(processes=[helper], root_pid=10)
        self.codex('started', kind='mcp_tool_call')
        self.now = 6
        observed = self.monitor.poll(processes=[helper], root_pid=10)
        self.assertEqual('running_tool', observed['activity'])
        self.assertEqual(1, observed['active_tool_count'])
        self.assertFalse(observed['process_fallback'])
        self.assertIsNone(self.monitor.expired())
        self.now = 20
        self.assertEqual('tool', self.monitor.expired()['kind'])

    def test_finished_command_returns_to_idle_despite_mcp_helper(self):
        helper = {'pid': 11, 'state': 'S', 'executable': 'mcp-server-darwin-arm64'}
        self.monitor.poll(processes=[helper, {'pid': 12, 'executable': 'sh'}], root_pid=10)
        self.now = 6
        observed = self.monitor.poll(processes=[helper], root_pid=10)
        self.assertFalse(observed['process_fallback'])
        self.assertIsNone(observed['tool_elapsed_seconds'])
        self.now = 11
        self.assertEqual('idle', self.monitor.expired()['kind'])

    def test_new_completions_renew_fallback_for_persistent_helper_but_duplicates_do_not(self):
        self.monitor.poll(processes=[{'pid': 11}], root_pid=10)
        for index in range(1, 4):
            self.now = index * 15
            self.opencode('completed', identifier='part-' + str(index))
            self.monitor.poll(processes=[{'pid': 11}], root_pid=10)
            self.assertIsNone(self.monitor.expired())
            self.assertEqual(0, self.monitor.snapshot()['tool_elapsed_seconds'])
        self.now = 64
        self.opencode('completed', identifier='part-3', output='same completion with different output')
        self.now = 65
        self.assertEqual('tool', self.monitor.expired()['kind'])

    def test_process_completion_returns_to_idle_and_ignores_zombies(self):
        self.monitor.poll(processes=[{'pid': 11}], root_pid=10)
        self.now = 12
        self.monitor.poll(processes=[{'pid': 10}, {'pid': 11, 'state': 'Z'}], root_pid=10)
        self.assertFalse(self.monitor.snapshot()['process_fallback'])
        self.now = 16
        self.assertIsNone(self.monitor.expired())
        self.now = 17
        self.assertEqual('idle', self.monitor.expired()['kind'])

    def test_explicit_starts_replace_fallback_and_wrappers_cannot_extend_idle(self):
        self.monitor.poll(processes=[{'pid': 11}], root_pid=10)
        self.now = 8
        self.codex('started')
        self.now = 20
        self.assertIsNone(self.monitor.expired())
        self.codex('completed')
        self.monitor.poll(processes=[{'pid': 11}], root_pid=10)
        self.now = 25
        self.assertEqual('idle', self.monitor.expired()['kind'])

    def test_partial_jsonl_is_read_incrementally_and_not_credited_early(self):
        self.path.write_bytes(b'{"type":"item.completed","item":{"id":"x",')
        self.monitor.poll()
        first_offset = self.monitor._offset
        self.now = 4
        self.monitor.poll()
        self.assertEqual(first_offset, self.monitor._offset)
        self.assertEqual(4, self.monitor.snapshot()['idle_seconds'])
        with self.path.open('ab') as stream:
            stream.write(b'"type":"agent_message","text":"new finding"}}\n')
        self.monitor.poll()
        self.now = 8
        self.assertIsNone(self.monitor.expired())
        self.assertEqual(self.path.stat().st_size, self.monitor._offset)

    def test_oversized_malformed_and_non_object_events_are_ignored(self):
        self.monitor.MAX_LINE_BYTES = 100
        self.monitor.MAX_READ_BYTES = 40
        self.path.write_bytes(b'{' + b'x' * 200 + b'}\n[]\n{bad json}\n{"item":{"type":[]},"type":"item.started"}\n')
        while self.monitor._offset < self.path.stat().st_size:
            before = self.monitor._offset
            self.monitor.poll()
            self.assertLessEqual(self.monitor._offset - before, 40)
            self.assertLessEqual(len(self.monitor._line.data) if self.monitor._line else 0, 100)
        self.now = 5
        self.assertEqual('idle', self.monitor.expired()['kind'])
        self.append({'type': 'turn.started'})
        self.assertIsNone(self.monitor.expired())

    def test_large_valid_completion_clears_known_tool_across_bounded_reads(self):
        self.codex('started')
        output = 'large output with \\" quotes and braces {} 😄 ' * 35000
        self.assertGreater(len(output.encode()), self.monitor.MAX_LINE_BYTES)
        self.now = 15
        self.codex('completed', aggregated_output=output, exit_code=0)
        self.assertEqual(1, self.monitor.snapshot()['active_tool_count'])
        while self.monitor._offset < self.path.stat().st_size:
            self.monitor.poll()
            self.assertLessEqual(len(self.monitor._line.data) if self.monitor._line else 0,
                                 self.monitor.MAX_LINE_BYTES)
        self.assertEqual(0, self.monitor.snapshot()['active_tool_count'])
        self.now = 19
        self.assertIsNone(self.monitor.expired())
        self.now = 20
        self.assertEqual('idle', self.monitor.expired()['kind'])

    def test_large_opencode_completion_preserves_structural_fields_after_output(self):
        self.opencode('running')
        self.now = 15
        self.append({'type': 'tool_use', 'part': {'state': {'output': 'x' * (2 * self.monitor.MAX_LINE_BYTES),
                                                        'status': 'completed'}, 'tool': 'bash', 'id': 'part-1'}})
        while self.monitor._offset < self.path.stat().st_size:
            self.monitor.poll()
        self.assertEqual(0, self.monitor.snapshot()['active_tool_count'])
        self.assertIsNone(self.monitor.expired())

    def test_string_projection_handles_utf8_and_escape_sequences_split_across_chunks(self):
        self.codex('started')
        self.monitor.MAX_READ_BYTES = 7
        self.monitor.MAX_STRING_BYTES = 32
        self.now = 15
        row = {'type': 'item.completed', 'item': {'id': 'call-1', 'type': 'command_execution',
                                                'aggregated_output': '😄 \\" \n\t' * 30}}
        with self.path.open('ab') as stream:
            stream.write(json.dumps(row, ensure_ascii=False).encode() + b'\n')
        while self.monitor._offset < self.path.stat().st_size:
            self.monitor.poll()
        self.assertEqual(0, self.monitor.snapshot()['active_tool_count'])
        self.assertIsNone(self.monitor.expired())

    def test_malformed_large_output_cannot_close_tool_or_refresh_activity(self):
        for suffix in (b'\\q"}}\n', b'\\uXX00"}}\n', b'\xff"}}\n', b'"}} trailing\n', b'"},}\n'):
            with self.subTest(suffix=suffix):
                self.monitor = ActivityMonitor(self.path, idle_seconds=5, tool_seconds=20, clock=lambda: self.now)
                self.path.write_bytes(b'')
                self.now = 0
                self.codex('started')
                self.now = 15
                with self.path.open('ab') as stream:
                    stream.write(b'{"type":"item.completed","item":{"id":"call-1","type":"command_execution",'
                                 b'"aggregated_output":"' + b'x' * (2 * self.monitor.MAX_LINE_BYTES) + suffix)
                while self.monitor._offset < self.path.stat().st_size:
                    self.monitor.poll()
                self.assertEqual(1, self.monitor.snapshot()['active_tool_count'])
                self.now = 20
                self.assertEqual('tool', self.monitor.expired()['kind'])

    def test_tool_completion_json_inside_large_output_remains_opaque(self):
        self.codex('started')
        forged = json.dumps({'type': 'item.completed', 'item': {'id': 'call-1', 'type': 'command_execution'}})
        self.now = 15
        self.append({'type': 'log', 'output': 'x' * (2 * self.monitor.MAX_LINE_BYTES) + forged})
        while self.monitor._offset < self.path.stat().st_size:
            self.monitor.poll()
        self.now = 20
        self.assertEqual(1, self.monitor.snapshot()['active_tool_count'])
        self.assertEqual('tool', self.monitor.expired()['kind'])

    def test_large_structural_string_is_not_shortened_to_a_valid_event_identifier(self):
        self.codex('started')
        self.now = 15
        self.codex('completed', identifier='call-1' + ' ' * (2 * self.monitor.MAX_LINE_BYTES))
        while self.monitor._offset < self.path.stat().st_size:
            self.monitor.poll()
        self.now = 20
        self.assertEqual('tool', self.monitor.expired()['kind'])

    def test_truncation_and_replacement_do_not_recredit_repeated_events(self):
        event = {'type': 'thread.started', 'thread_id': 'thread-1'}
        self.append(event)
        self.now = 4
        self.path.write_bytes(b'')
        self.monitor.poll()
        self.append(event)
        self.path.rename(self.path.with_suffix('.old'))
        self.append(event)
        self.now = 5
        self.assertEqual('idle', self.monitor.expired()['kind'])

    def test_disabled_limits_still_report_activity(self):
        self.monitor = ActivityMonitor(self.path, idle_seconds=0, tool_seconds=0, clock=lambda: self.now)
        self.now = 10000
        self.assertIsNone(self.monitor.expired())
        self.codex('started')
        self.now = 20000
        self.assertIsNone(self.monitor.expired())
        self.assertEqual(10000, self.monitor.snapshot()['tool_elapsed_seconds'])
        self.assertEqual('running_tool', self.monitor.snapshot()['activity'])

    def test_disabled_tool_limit_does_not_fall_back_to_idle_limit(self):
        self.monitor = ActivityMonitor(self.path, idle_seconds=5, tool_seconds=0, clock=lambda: self.now)
        self.codex('started')
        self.now = 10000
        self.assertIsNone(self.monitor.expired())
        self.codex('completed')
        self.now += 5
        self.assertEqual('idle', self.monitor.expired()['kind'])

    def test_snapshot_is_detached_and_serializable(self):
        snapshot = self.monitor.snapshot()
        self.codex('started')
        self.assertEqual('waiting_for_provider', snapshot['activity'])
        json.dumps(self.monitor.snapshot())

    def test_deduplication_cache_saturation_never_evicts_old_credit(self):
        self.monitor.MAX_SEEN = 2
        self.codex('completed', kind='agent_message', text='first')
        self.codex('completed', identifier='two', kind='agent_message', text='second')
        self.now = 4
        self.codex('completed', identifier='three', kind='agent_message', text='third')
        self.codex('completed', kind='agent_message', text='first')
        self.now = 5
        self.assertEqual('idle', self.monitor.expired()['kind'])


if __name__ == '__main__':
    unittest.main()
