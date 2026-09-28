"""Offline regressions for observed OpenCode report and evidence formats."""
import copy
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autocode as runner
import autocode_goals as goals
import autocode_opencode as opencode
import autocode_support as support
from goal_fixtures import approve_fixture, envelope


class RuntimeReportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.workspace = Path(self.temp.name).resolve()
        self.run = self.workspace / '.autocode/runs/fixture'
        self.run.mkdir(parents=True)
        self.log = self.run / 'events.jsonl'
        self.rows = [
            {'type': 'step_start', 'sessionID': 'session-one', 'part': {'id': 'start-one'}},
            {'type': 'tool_use', 'sessionID': 'session-one', 'part': {
                'id': 'command-one', 'tool': 'bash', 'state': {'status': 'completed',
                'input': {'command': 'python -m unittest'}, 'metadata': {'exit': 0}, 'output': 'OK'}}},
            {'type': 'step_finish', 'sessionID': 'session-one', 'part': {'id': 'finish-one', 'reason': 'stop'}},
        ]
        self.save()

    def save(self):
        self.log.write_text('\n'.join(json.dumps(row) for row in self.rows) + '\n')

    def final_message(self, texts):
        self.rows = [{'type': 'step_start', 'sessionID': 'session-one', 'part': {'id': 'start'}}]
        self.rows += [{'type': 'text', 'sessionID': 'session-one', 'part': {
            'id': f'text-{i}', 'messageID': 'final-message', 'text': value}}
            for i, value in enumerate(texts)]
        self.rows += [{'type': 'step_finish', 'sessionID': 'session-one', 'part': {
            'id': 'finish', 'messageID': 'final-message', 'reason': 'stop'}}]
        self.save()

    def test_completed_evidence_resolves_to_preserved_log_with_file_refs_intact(self):
        self.assertEqual([str(self.log), 'capture.json'], support.implementation_evidence_paths(
            ['event:command-one', 'capture.json', 'event:command-one'], self.log))

    def test_step_markers_missing_events_and_decorated_refs_are_rejected(self):
        for ref in ['event:finish-one', 'event:absent', 'event:command-one; exit:0']:
            with self.subTest(ref=ref), self.assertRaises(ValueError):
                support.implementation_evidence_paths([ref], self.log)

    def test_incomplete_command_or_mixed_session_cannot_supply_evidence(self):
        for metadata in ({}, {'exit': False}):
            self.rows[1]['part']['state']['metadata'] = metadata
            self.save()
            with self.assertRaises(ValueError):
                support.implementation_evidence_paths(['event:command-one'], self.log)
        self.rows[1]['part']['state']['metadata'] = {'exit': 0}
        self.rows[1]['sessionID'] = 'different-session'
        self.save()
        with self.assertRaises(ValueError):
            support.implementation_evidence_paths(['event:command-one'], self.log)

    def test_cursor_shell_output_can_attest_a_capture_receipt_only(self):
        raw = self.run / 'captured.log'
        raw.write_text('1 test, 0 failures\n')
        receipt = {'command': ['python', '-m', 'unittest'], 'exit_code': 0,
                   'duration_seconds': 1, 'full_output': str(raw),
                   'full_output_sha256': support.file_hash(raw),
                   'summary': {'format': 'text', 'content': raw.read_text(),
                               'omitted_progress_lines': 0, 'repeated_lines': {}}}
        receipt_path = self.workspace / '.autocode/evidence/cursor-check.json'
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(json.dumps(receipt))
        self.rows[1]['part'].update(tool='shell', state={
            'status': 'completed',
            'input': {'command': 'autocode capture --output .autocode/evidence/cursor-check.json -- python -m unittest'},
            'metadata': {}, 'output': json.dumps(receipt)})
        self.save()
        support.verify_checks([{'command': 'python -m unittest', 'exit_code': 0,
                                'evidence_ref': str(receipt_path)}], self.workspace, self.log)
        with self.assertRaises(ValueError):
            support.verify_checks([{'command': 'python -m unittest', 'exit_code': 0,
                                    'evidence_ref': 'event:command-one'}], self.workspace, self.log)

    def test_opencode_bash_output_without_exit_can_attest_capture_receipt(self):
        raw = self.run / 'captured.log'
        raw.write_text('OK\n')
        receipt = {'command': ['python', '-m', 'unittest'], 'exit_code': 0,
                   'full_output': str(raw), 'full_output_sha256': support.file_hash(raw)}
        receipt_path = self.workspace / '.autocode/evidence/bash-check.json'
        receipt_path.parent.mkdir(parents=True)
        receipt_path.write_text(json.dumps(receipt))
        self.rows[1]['part'].update(tool='bash', state={
            'status': 'completed', 'input': {'command': 'autocode capture --output .autocode/evidence/bash-check.json -- python -m unittest'},
            'metadata': {'truncated': False}, 'output': json.dumps(receipt)})
        self.save()
        support.verify_checks([{'command': 'python -m unittest', 'exit_code': 0,
                                'evidence_ref': str(receipt_path)}], self.workspace, self.log)
        with self.assertRaises(ValueError):
            support.verify_checks([{'command': 'python -m unittest', 'exit_code': 0,
                                    'evidence_ref': 'event:command-one'}], self.workspace, self.log)

    def test_evidence_advances_only_to_independent_validation_without_approval_changes(self):
        state = {'version': 2, 'workspace': str(self.workspace), 'task': 'Fixture task',
                 'status': 'RUNNING', 'iteration': 1, 'stages': [], 'history': [],
                 'sessions': {}, 'acceptance_criteria': [], 'settings': {}}
        approve_fixture(state, goals)
        before = copy.deepcopy(state)
        value = {**envelope(state), 'summary': 'Implementation ready', 'changed_files': ['source.py'],
                 'commands_run': ['python -m unittest'], 'results': ['OK'],
                 'remaining_risks': [], 'evidence_refs': ['event:command-one']}
        record = {'events': str(self.log), 'source_revision': 'fixture-revision',
                  'changed_files': ['source.py'], 'after_ref': str(self.run / 'snapshot.json'),
                  'output': str(self.run / 'report.json')}
        invalid = {**value, 'evidence_refs': ['event:invented']}
        with self.assertRaises(ValueError):
            runner.apply_result(state, 'terra', invalid, record, self.workspace, self.run)
        self.assertEqual(before, state)
        runner.apply_result(state, 'terra', value, record, self.workspace, self.run)
        self.assertEqual('sol', state['next_stage'])
        self.assertNotEqual('TASK_COMPLETE', state['status'])
        self.assertEqual(before['goal_contract'], state['goal_contract'])
        self.assertEqual(['event:command-one'], state['implementation']['evidence_refs'])

    def test_commentary_before_one_final_json_part_is_accepted(self):
        self.final_message(['The implementation needs independent validation.', '{"status":"CONTINUE"}'])
        self.assertEqual({'status': 'CONTINUE'}, opencode.final_report(self.log))

    def test_report_only_recovery_accepts_identical_complete_json_copies(self):
        report = '{"verdict":"PASS","unverified_criteria":["T01"]}'
        self.final_message(['The saved receipts support this report.' + report + report])
        with self.assertRaises(RuntimeError):
            opencode.final_report(self.log)
        self.assertEqual(json.loads(report), opencode.final_report(self.log, recover_wrapped=True))

    def test_report_only_recovery_rejects_conflicting_or_trailing_content(self):
        for message in ('Receipt.' + '{"status":"PASS"}' + '{"status":"FAIL"}',
                        'Receipt.' + '{"status":"PASS"}' + 'changed my mind',
                        'Receipt.' + '{"status":"PASS"}' * 3):
            self.final_message([message])
            with self.subTest(message=message[:35]), self.assertRaises(RuntimeError):
                opencode.final_report(self.log, recover_wrapped=True)

    def test_ambiguous_json_or_commentary_after_report_is_rejected(self):
        for texts in [['{"status":"A"}', '{"status":"B"}'],
                      ['{"status":"A"}', 'Changed my mind.']]:
            self.final_message(texts)
            with self.assertRaises(RuntimeError):
                opencode.final_report(self.log)

    def test_commentary_handling_preserves_terminal_completion_guard(self):
        self.final_message(['Ready.', '{"status":"CONTINUE"}'])
        self.rows[-1]['part']['reason'] = 'length'
        self.save()
        with self.assertRaises(RuntimeError):
            opencode.final_report(self.log)


if __name__ == '__main__':
    unittest.main()
