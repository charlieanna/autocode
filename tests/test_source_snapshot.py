"""Real Git controls for explicitly selected ignored source deliverables."""
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode_source_snapshot as source
import autocode_util as util


class SelectedSourceSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.git('init', '-q')
        (self.root / '.gitignore').write_text('docs/\n.autocode/\nsecrets/\n')
        (self.root / 'README.md').write_text('Unchanged seed\n')
        self.git('add', '.')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid',
                 'commit', '-qm', 'Seed')

    def git(self, *args):
        return subprocess.check_output(['git', *args], cwd=self.root)

    def write(self, name, text):
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    def test_ignored_deliverable_creation_edit_and_deletion_change_identity(self):
        paths = ['docs/checklist.md']
        before = source.snapshot(self.root, paths=paths)
        path = self.write(paths[0], 'First version\n')
        created = source.snapshot(self.root, paths=paths)
        self.assertEqual(util.changed_paths(before, created), paths)
        self.assertNotEqual(before['revision'], created['revision'])
        path.write_text('Second version\n')
        edited = source.snapshot(self.root, paths=paths)
        self.assertNotEqual(created['revision'], edited['revision'])
        path.unlink()
        deleted = source.snapshot(self.root, paths=paths)
        self.assertEqual(util.changed_paths(edited, deleted), paths)
        self.assertEqual(before, deleted)

    def test_unselected_ignored_content_is_not_inventoried(self):
        self.write('docs/selected.md', 'Selected\n')
        self.write('secrets/unselected.txt', 'Do not inventory\n')
        current = source.snapshot(self.root, paths=['docs/selected.md'])
        self.assertIn('docs/selected.md', current['files'])
        self.assertNotIn('secrets/unselected.txt', current['files'])
        self.write('secrets/unselected.txt', 'Changed\n')
        self.assertEqual(current, source.snapshot(self.root, paths=current['source_paths']))

    def test_selected_directory_includes_new_children_but_not_runner_internals(self):
        before = source.snapshot(self.root, paths=['docs/'])
        self.write('docs/deep/result.md', 'Output\n')
        self.write('docs/.autocode/state.json', '{}')
        after = source.snapshot(self.root, paths=['docs'])
        self.assertEqual(util.changed_paths(before, after), ['docs/deep/result.md'])
        self.assertEqual(after['source_paths'], ['docs'])

    def test_legacy_snapshot_remains_identical_and_git_state_is_untouched(self):
        index = (self.root / '.git/index').read_bytes()
        config = (self.root / '.git/config').read_bytes()
        self.write('docs/result.md', 'Output\n')
        self.assertEqual(source.snapshot(self.root), util.snapshot(self.root))
        source.snapshot(self.root, paths=['docs/result.md'])
        self.assertEqual(index, (self.root / '.git/index').read_bytes())
        self.assertEqual(config, (self.root / '.git/config').read_bytes())
        self.assertEqual(self.git('status', '--porcelain'), b'')

    def test_injected_reader_keeps_its_revision_until_selected_content_changes(self):
        original = util.snapshot(self.root)
        original['revision'] = 'opaque-service-revision'
        selected = source.snapshot(self.root, paths=['README.md'], base_snapshot=lambda root: original)
        self.assertEqual('opaque-service-revision', selected['revision'])
        self.write('docs/result.md', 'New ignored source\n')
        expanded = source.snapshot(self.root, paths=['docs/result.md'], base_snapshot=lambda root: original)
        self.assertNotEqual('opaque-service-revision', expanded['revision'])
        self.assertNotIn('docs/result.md', original['files'])

    def test_reconciliation_preserves_original_snapshot_bytes_and_rejects_changed_source(self):
        import json
        evidence = self.root / '.autocode/saved.after.json'
        evidence.parent.mkdir()
        original = util.snapshot(self.root)
        raw = json.dumps(original, separators=(',', ':')).encode()
        evidence.write_bytes(raw)
        current = source.snapshot(self.root, paths=['README.md'])
        source.preserve_after(evidence, current)
        self.assertEqual(raw, evidence.read_bytes())
        self.write('README.md', 'Changed after the recorded attempt\n')
        with self.assertRaisesRegex(util.Paused, 'differs from current source'):
            source.preserve_after(evidence, source.snapshot(self.root, paths=['README.md']))
        self.assertEqual(raw, evidence.read_bytes())

    def test_symlink_is_recorded_without_following_its_target(self):
        outside = Path(self.tmp.name).parent / ('external-' + self.root.name)
        outside.mkdir()
        self.addCleanup(outside.rmdir)
        (self.root / 'docs').symlink_to(outside, target_is_directory=True)
        current = source.snapshot(self.root, paths=['docs'])
        self.assertEqual(current['files']['docs'], 'symlink:' + str(outside))
        with self.assertRaisesRegex(ValueError, 'symlinked parent'):
            source.snapshot(self.root, paths=['docs/file.md'])

    def test_executable_mode_changes_source_revision(self):
        path = self.write('docs/tool.sh', '#!/bin/sh\n')
        path.chmod(0o644)
        before = source.snapshot(self.root, paths=['docs/tool.sh'])
        path.chmod(0o755)
        after = source.snapshot(self.root, paths=['docs/tool.sh'])
        self.assertNotEqual(before['revision'], after['revision'])
        self.assertTrue(after['files']['docs/tool.sh'].startswith('executable:'))

    def test_unsafe_or_unbounded_paths_are_rejected(self):
        for path in ['', '.', './docs', '..', '../docs', '/tmp', 'docs/../outside',
                     'docs//file', 'docs/*', '.git/config', 'docs/.autocode/state.json',
                     'docs\\file', 'docs/\nfile', '.GIT/config', 'docs/.AutoCode/state.json']:
            with self.subTest(path=path), self.assertRaises(ValueError):
                source.snapshot(self.root, paths=[path])

    def nested_repository(self):
        nested = self.root / 'module'
        nested.mkdir()
        def git(*args):
            return subprocess.check_output(['git', '-C', str(nested), *args])
        git('init', '-q')
        (nested / '.gitignore').write_text('docs/\n')
        (nested / 'code.py').write_text('value = 1\n')
        git('add', '.')
        git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Nested seed')
        self.git('add', 'module')
        self.git('-c', 'user.name=Test', '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Nested Git link')
        return nested

    def test_nested_git_keeps_existing_identity_and_detects_selected_ignored_edits(self):
        self.nested_repository()
        before = source.snapshot(self.root, paths=['module'])
        self.assertEqual(util.snapshot(self.root)['revision'], before['revision'])
        document = self.write('module/docs/result.md', 'Nested deliverable\n')
        created = source.snapshot(self.root, paths=['module'])
        self.assertEqual(['module'], util.changed_paths(before, created))
        exact = source.snapshot(self.root, paths=['module/docs/result.md'])
        self.assertEqual(created['revision'], exact['revision'])
        document.write_text('Edited deliverable\n')
        self.assertNotEqual(exact['revision'], source.snapshot(self.root, paths=exact['source_paths'])['revision'])
        self.assertEqual(util.snapshot(self.root)['revision'], before['revision'])

    def test_nested_selected_deliverable_reaches_copy_diff_and_clean_replay(self):
        import shlex
        import sys

        import autocode_source_diff as diffs
        import autocode_verification_copy as copies
        import autocode_verify as verify
        self.nested_repository()
        document = self.write('module/docs/result.md', 'Nested deliverable\n')
        paths = ['module/docs/result.md']
        index = (self.root / '.git/index').read_bytes()
        nested_index = (self.root / 'module/.git/index').read_bytes()
        scratch = self.root / '.autocode/tool-containment-nested/scratch'
        scratch.mkdir(parents=True)
        result = copies.create(self.root, scratch, source_paths=paths)
        tree, _ = copies.execution(result['manifest'], result['sha256'], self.root)
        self.assertEqual(document.read_bytes(), (tree / paths[0]).read_bytes())
        diff = scratch / 'nested.diff'
        diffs.write(self.root, diff, source.snapshot(self.root, paths=paths))
        self.assertIn('+++ b/module/docs/result.md', diff.read_text())
        self.assertIn('+Nested deliverable', diff.read_text())
        command = shlex.join([sys.executable, '-c',
            "from pathlib import Path; assert Path('module/docs/result.md').read_text() == 'Nested deliverable\\n'"])
        receipt = verify.scratch_run(self.root, scratch / 'replay', command=command, source_paths=paths)
        self.assertEqual(0, receipt['exit_code'], receipt)
        self.assertEqual(index, (self.root / '.git/index').read_bytes())
        self.assertEqual(nested_index, (self.root / 'module/.git/index').read_bytes())
        document.write_text('Later edit\n')
        with self.assertRaisesRegex(ValueError, 'Verification source changed'):
            copies.execution(result['manifest'], result['sha256'], self.root)

    def test_fifo_is_rejected_without_reading_it(self):
        path = self.root / 'docs/pipe'
        path.parent.mkdir()
        os.mkfifo(path)
        with self.assertRaisesRegex(ValueError, 'Unsupported source file type'):
            source.snapshot(self.root, paths=['docs/pipe'])

    def test_verification_copy_includes_selected_output_and_rejects_its_later_edit(self):
        import autocode_verification_copy as copies
        doc = self.write('docs/result.md', 'Selected output\n')
        scratch = self.root / '.autocode/tool-containment-test/scratch'
        scratch.mkdir(parents=True)
        result = copies.create(self.root, scratch, source_paths=['docs/result.md'])
        tree, _ = copies.execution(result['manifest'], result['sha256'], self.root)
        self.assertEqual((tree / 'docs/result.md').read_bytes(), doc.read_bytes())
        doc.write_text('Changed after copy\n')
        with self.assertRaisesRegex(ValueError, 'Verification source changed'):
            copies.execution(result['manifest'], result['sha256'], self.root)

    def test_the_verification_copy_repository_never_starts_background_maintenance(self):
        # The copy's git runs with an environment built from scratch, outside the
        # suite-wide GIT_CONFIG_COUNT (#515), so it must disable maintenance in
        # its own config: a detached repack under .git/objects after the manifest
        # walk reads as a changed verification input (master, 2026-10-06).
        import json

        import autocode_verification_copy as copies
        self.write('docs/result.md', 'Selected output\n')
        scratch = self.root / '.autocode/tool-containment-maintenance/scratch'
        scratch.mkdir(parents=True)
        result = copies.create(self.root, scratch, source_paths=['docs/result.md'])
        tree = json.loads(Path(result['manifest']).read_text())['tree']
        # Only the repository's own config can answer here.
        probe = {'PATH': os.environ['PATH'], 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': '/dev/null'}
        for key, value in (('maintenance.auto', 'false'), ('gc.auto', '0')):
            read = subprocess.run(['git', '-C', str(tree), 'config', key],
                                  env=probe, capture_output=True, text=True)
            self.assertEqual(value, read.stdout.strip(), key)

    def test_verification_copy_rejects_new_child_under_selected_directory(self):
        import autocode_verification_copy as copies
        self.write('docs/first.md', 'First output\n')
        scratch = self.root / '.autocode/tool-containment-test/scratch'
        scratch.mkdir(parents=True)
        result = copies.create(self.root, scratch, source_paths=['docs/'])
        self.write('docs/second.md', 'New output\n')
        with self.assertRaisesRegex(ValueError, 'Verification source changed'):
            copies.execution(result['manifest'], result['sha256'], self.root)

    def approved_state(self):
        from autocode_contract_identity import token
        contract = {'task_id': 'fixture', 'revision': 1,
                    'body': {'milestones': [{'id': 'M1', 'affected_paths': ['docs/first.md']},
                                           {'id': 'M2', 'affected_paths': ['docs/second.md']}],
                             'open_blocking_questions': []}}
        contract['hash'] = util.digest(contract)
        event = {'actor': 'user_cli', 'token': token(contract)}
        contract.update(approval_status='approved', approval_event=event)
        return {'goal_contract': contract, 'user_events': [event],
                'current_task': {'milestone_id': 'M2', 'affected_paths': ['secrets/']}}

    def test_scope_uses_all_approved_milestones_and_ignores_model_path_claims(self):
        import autocode_source_scope as scope
        state = self.approved_state()
        first = self.write('docs/first.md', 'Already completed\n')
        self.write('docs/second.md', 'Current work\n')
        self.write('secrets/hidden.txt', 'Not approved source\n')
        before = scope.snapshot(self.root, state)
        self.assertEqual(before['source_paths'], ['docs/first.md', 'docs/second.md'])
        self.assertNotIn('secrets/hidden.txt', before['files'])
        first.write_text('Changed previously completed milestone\n')
        self.assertNotEqual(before['revision'], scope.snapshot(self.root, state)['revision'])

    def test_unapproved_or_tampered_contract_cannot_expand_source_scope(self):
        import autocode_source_scope as scope
        self.write('docs/first.md', 'Ignored output\n')
        for kind in ['missing_event', 'draft', 'tampered']:
            state = self.approved_state()
            if kind == 'missing_event':
                state['user_events'] = []
            elif kind == 'draft':
                state['goal_contract']['approval_status'] = 'draft'
            else:
                state['goal_contract']['body']['milestones'][0]['affected_paths'] = ['secrets/']
            with self.subTest(kind=kind):
                self.assertEqual(scope.snapshot(self.root, state), util.snapshot(self.root))

    def test_diff_and_checkpoint_capture_selected_output_without_touching_user_index(self):
        import autocode_code_checkpoints as checkpoints
        import autocode_source_diff as diffs
        doc = self.write('docs/result.md', 'Selected output\n')
        evidence = self.root / '.autocode/evidence'
        evidence.mkdir(parents=True)
        current = source.snapshot(self.root, paths=['docs/result.md'])
        index = (self.root / '.git/index').read_bytes()
        diffs.write(self.root, evidence / 'stage.diff', current)
        text = (evidence / 'stage.diff').read_text()
        self.assertIn('+++ b/docs/result.md', text)
        self.assertIn('+Selected output', text)
        revision = checkpoints.capture_tree(self.root, current, evidence)
        self.assertEqual(self.git('show', revision + ':docs/result.md'), doc.read_bytes())
        self.assertEqual(index, (self.root / '.git/index').read_bytes())
        self.assertEqual(self.git('status', '--porcelain'), b'')

    def test_clean_replay_reads_selected_ignored_deliverable(self):
        import shlex
        import sys

        import autocode_verify as verify
        self.write('docs/result.md', 'Selected output\n')
        evidence = self.root / '.autocode/replay'
        command = shlex.join([sys.executable, '-c',
            "from pathlib import Path; assert Path('docs/result.md').read_text() == 'Selected output\\n'"])
        receipt = verify.scratch_run(self.root, evidence, command=command,
                                     source_paths=['docs/result.md'])
        self.assertEqual(receipt.get('error'), '', receipt)
        self.assertEqual(receipt['exit_code'], 0, receipt)


if __name__ == '__main__':
    unittest.main()
