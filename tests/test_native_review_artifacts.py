"""Native Git evidence permits new review tests but never existing-source changes."""
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import autocode as runner
import autocode_job_source as source
import autocode_readonly_events as policy
import autocode_util as util


class NativeReviewArtifactTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_dir = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.seed_dir.cleanup)
        cls.seed = Path(cls.seed_dir.name) / 'project'
        subprocess.run(['git', 'init', '-q', str(cls.seed)], check=True)
        # A copied seed must stay immutable while each case copies its objects: Git's background
        # auto-maintenance after the commit raced copytree over .git/objects/maintenance.lock in CI.
        subprocess.run(['git', '-C', str(cls.seed), 'config', 'maintenance.auto', 'false'], check=True)
        subprocess.run(['git', '-C', str(cls.seed), 'config', 'gc.auto', '0'], check=True)
        (cls.seed / 'app.py').write_text('VALUE = 1\n')
        (cls.seed / 'review').mkdir()
        (cls.seed / 'review/existing.py').write_text('existing = True\n')
        subprocess.run(['git', '-C', str(cls.seed), 'add', '.'], check=True)
        subprocess.run(['git', '-C', str(cls.seed), '-c', 'user.name=Fixture', '-c',
                        'user.email=fixture@example.test', 'commit', '-qm', 'fixture'], check=True)

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name).resolve()
        self.root = self.directory / 'project'
        shutil.copytree(self.seed, self.root)
        self.data = self.directory / 'data'
        self.artifacts = self.root / '.autocode/test'
        self.artifacts.mkdir(parents=True)
        self.events = self.artifacts / 'events.jsonl'
        schema = self.artifacts / 'schema.json'
        schema.write_text(json.dumps({'type': 'object', 'properties': {'ok': {'type': 'boolean'}},
                                     'required': ['ok'], 'additionalProperties': False}))
        self.record = {'engine': 'opencode', 'output_mode': 'opencode_events',
                       'role': 'sol', 'stage': 'review_change', 'events': str(self.events),
                       'command': ['opencode', 'run', '--dir', str(self.root)],
                       'output': str(self.artifacts / 'report.json'), 'schema': str(schema)}
        policy.prepare_opencode_snapshots(self.root, record=self.record,
                                          env={'XDG_DATA_HOME': str(self.data)})
        source.capture(self.root, self.artifacts / 'review', self.record, util.snapshot(self.root))
        project = subprocess.check_output(['git', '-C', str(self.root), 'rev-parse', 'HEAD'], text=True).strip()
        (self.root / '.git/opencode').write_text(project)
        self.cache = self.data / 'opencode/snapshot' / project / ('c' * 64)
        subprocess.run(['git', 'init', '-q', '--bare', str(self.cache)], check=True)
        self.git('config', 'core.bare', 'false')
        self.git('config', 'core.worktree', str(self.root))
        (self.cache / 'info/exclude').write_text('/.autocode/\n')
        self.before = self.tree()

    def git(self, *args):
        return subprocess.check_output(['git', '--git-dir=' + str(self.cache), *args], text=True).strip()

    def tree(self):
        self.git('add', '-A')
        return self.git('write-tree')

    def add_test(self):
        path = self.root / 'review/tests/test_f1.py'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('import app\ndef test_f1(): assert app.VALUE == 2\n')
        return path

    def events_for(self, trees):
        rows = [{'type': 'step_start', 'sessionID': 'ses_review', 'part': {
            'id': f'start-{i}', 'messageID': 'msg_review', 'type': 'step-start',
            'snapshot': tree}} for i, tree in enumerate(trees)]
        rows.extend([{'type': 'text', 'sessionID': 'ses_review', 'part': {
            'id': 'text', 'type': 'text', 'messageID': 'msg_review', 'text': '{"ok":true}'}},
            {'type': 'step_finish', 'sessionID': 'ses_review', 'part': {
                'id': 'finish', 'type': 'step-finish', 'messageID': 'msg_review', 'reason': 'stop',
                'snapshot': trees[-1], 'tokens': {'input': 1, 'output': 1, 'reasoning': 0,
                                                 'cache': {'read': 0, 'write': 0}}}}])
        self.events.write_text('\n'.join(json.dumps(row) for row in rows))

    def rejected(self):
        with self.assertRaises(util.Paused) as caught:
            runner.load_stage_report(self.record)
        self.assertEqual('PAUSED_STALE_VALIDATION', caught.exception.status)
        self.assertFalse(Path(self.record['output']).exists())

    def test_new_review_test_report_is_accepted(self):
        self.add_test()
        self.events_for([self.before, self.tree()])
        self.assertEqual({'ok': True}, runner.load_stage_report(self.record))

    def test_new_review_test_can_be_refined_before_delivery(self):
        path = self.add_test()
        first = self.tree()
        path.write_text(path.read_text() + '# refined evidence\n')
        self.events_for([self.before, first, self.tree()])
        self.assertEqual({'ok': True}, runner.load_stage_report(self.record))

    def test_source_edit_then_restore_is_rejected(self):
        path = self.root / 'app.py'
        original = path.read_bytes()
        path.write_text('VALUE = 2\n')
        edited = self.tree()
        path.write_bytes(original)
        self.add_test()
        self.events_for([self.before, edited, self.tree()])
        self.rejected()

    def test_existing_review_file_edit_then_restore_is_rejected(self):
        path = self.root / 'review/existing.py'
        original = path.read_bytes()
        path.write_text('existing = False\n')
        edited = self.tree()
        path.write_bytes(original)
        self.add_test()
        self.events_for([self.before, edited, self.tree()])
        self.rejected()

    def test_disallowed_changes_cannot_hide_beside_new_test(self):
        for kind in ('outside', 'delete', 'rename', 'mode', 'symlink'):
            with self.subTest(kind=kind):
                self.add_test()
                existing = self.root / 'review/existing.py'
                if kind == 'outside':
                    (self.root / 'other.py').write_text('outside\n')
                elif kind == 'delete':
                    existing.unlink()
                elif kind == 'rename':
                    existing.rename(existing.with_name('renamed.py'))
                elif kind == 'mode':
                    existing.chmod(0o755)
                else:
                    (self.root / 'review/link').symlink_to('../app.py')
                self.events_for([self.before, self.tree()])
                self.rejected()
                # Restore this fixture for the next independent mutation.
                shutil.rmtree(self.root / 'review')
                shutil.copytree(self.seed / 'review', self.root / 'review')
                (self.root / 'other.py').unlink(missing_ok=True)

    def test_existing_untracked_review_file_is_protected_when_native_baseline_omits_it(self):
        path = self.root / 'review/untracked.py'
        path.write_text('original\n')
        source.capture(self.root, self.artifacts / 'untracked', self.record, util.snapshot(self.root))
        path.write_text('changed\n')
        self.events_for([self.before, self.tree()])
        self.rejected()

    def test_corrupt_or_missing_capture_is_rejected(self):
        self.add_test()
        self.events_for([self.before, self.tree()])
        manifest = Path(self.record['job_source']['capture'])
        manifest.write_text('{}')
        self.rejected()
        manifest.unlink()
        self.rejected()

    def test_wrong_workspace_cache_is_rejected(self):
        self.add_test()
        self.events_for([self.before, self.tree()])
        self.git('config', 'core.worktree', str(self.directory / 'other'))
        self.rejected()

    def test_missing_native_objects_is_rejected(self):
        self.add_test()
        self.events_for([self.before, self.tree()])
        shutil.rmtree(self.cache / 'objects')
        self.rejected()

    def test_malformed_saved_context_is_rejected(self):
        self.add_test()
        self.events_for([self.before, self.tree()])
        for context in (None, [], 'bad', {}, {'version': 1, 'workspace': str(self.root), 'root': '.'}):
            with self.subTest(context=context):
                self.record['opencode_snapshot_context'] = context
                self.rejected()

    def test_old_report_uses_current_location_only_as_lookup_hint(self):
        self.add_test()
        self.events_for([self.before, self.tree()])
        del self.record['opencode_snapshot_context']
        with patch.dict(os.environ, {'XDG_DATA_HOME': str(self.data)}):
            self.assertEqual({'ok': True}, runner.load_stage_report(self.record))

    def test_other_readonly_stage_cannot_add_new_tests(self):
        self.add_test()
        self.events_for([self.before, self.tree()])
        self.record['stage'] = 'sol'
        self.rejected()

    def test_report_cannot_be_accepted_for_a_different_workspace(self):
        self.add_test()
        self.events_for([self.before, self.tree()])
        with self.assertRaises(util.Paused) as caught:
            runner.load_stage_report(self.record, workspace=self.directory / 'other')
        self.assertEqual('PAUSED_STALE_VALIDATION', caught.exception.status)

    def test_report_repair_cannot_add_new_tests(self):
        self.add_test()
        self.events_for([self.before, self.tree()])
        self.record.update(stage='review_change_report_repair', report_only=True)
        self.rejected()

    def test_clean_repair_does_not_excuse_original_source_edit(self):
        (self.root / 'app.py').write_text('VALUE = 2\n')
        self.events_for([self.before, self.tree()])
        original = copy.deepcopy(self.record)
        original['events'] = str(self.artifacts / 'original.jsonl')
        Path(original['events']).write_bytes(self.events.read_bytes())
        self.record.update(stage='review_change_report_repair', report_only=True)
        self.events_for([self.before, self.before])
        with self.assertRaises(util.Paused) as caught:
            runner.load_stage_report(self.record, evidence_record=original)
        self.assertEqual('PAUSED_STALE_VALIDATION', caught.exception.status)


if __name__ == '__main__':
    unittest.main()
