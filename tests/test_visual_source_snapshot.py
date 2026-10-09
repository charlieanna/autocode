"""Native snapshot schema compatibility; no native authority qualification."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import autocode_source_snapshot as source
import autocode_util as util
import autocode_visual_history as history
import autocode_visual_writer_admission as admission


class NativeSnapshotSchemaTests(unittest.TestCase):
    def setUp(self):
        body = {'head': 'original-head', 'files': {'page.js': 'a' * 64}}
        self.original = {**body, 'revision': util.digest(body)}

    def readers(self):
        return (history._snapshot, admission._snapshot)

    def assert_accepted_unchanged(self, snapshot):
        # Noncanonical JSON formatting/key order is evidence, not a schema error.
        raw = json.dumps(snapshot, indent=3, sort_keys=False).encode()
        for read in self.readers():
            with self.subTest(reader=read.__module__):
                value = json.loads(raw)
                returned = read(value)
                self.assertEqual(returned, snapshot)
                self.assertEqual(json.dumps(value, indent=3, sort_keys=False).encode(), raw)
                self.assertEqual(json.dumps(returned, indent=3, sort_keys=False).encode(), raw)
                if read is history._snapshot:
                    self.assertIs(returned, value)
                else:
                    self.assertIsNot(returned, value)
                    self.assertIsNot(returned['files'], value['files'])
                    if 'source_paths' in value:
                        self.assertIsNot(returned['source_paths'], value['source_paths'])

    def assert_rejected(self, snapshot):
        original = deepcopy(snapshot)
        for read in self.readers():
            with self.subTest(reader=read.__module__), self.assertRaises(util.Paused):
                read(snapshot)
            self.assertEqual(snapshot, original)

    def test_legacy_and_canonical_scope_preserve_the_original_snapshot(self):
        self.assert_accepted_unchanged(self.original)
        for paths in ([], ['docs'], ['docs/missing.md', 'page.js']):
            with self.subTest(paths=paths):
                self.assert_accepted_unchanged({**self.original, 'source_paths': paths})

    def test_noncanonical_or_nonliteral_scope_fails_closed(self):
        for paths in (None, 'page.js', ('page.js',), {'page.js': True}, [None], [7],
                      ['page.js', 'docs'], ['docs', 'docs'], ['docs/'],
                      [''], ['.'], ['..'], ['./docs'], ['docs//page.js'], ['docs/../page.js'],
                      ['/tmp/page.js'], ['docs/*'], ['docs/?'], ['docs/[ab]'],
                      ['docs\\page.js'], ['docs/\x00page.js'], ['docs/\npage.js'], ['docs/\rpage.js'],
                      ['.git/config'], ['.GIT/config'], ['docs/.autocode/proof.json'],
                      ['docs/.AutoCode/proof.json'], ['.autocode-ui/settings.json']):
            with self.subTest(paths=paths):
                self.assert_rejected({**self.original, 'source_paths': paths})

    def test_unknown_fields_and_missing_required_fields_still_fail_closed(self):
        for scoped in (self.original, {**self.original, 'source_paths': ['page.js']}):
            self.assert_rejected({**scoped, 'approved': True})
            for key in ('head', 'files', 'revision'):
                with self.subTest(key=key):
                    self.assert_rejected({name: value for name, value in scoped.items() if name != key})
        for value in (None, [], 'source'):
            self.assert_rejected(value)

    def test_scope_cannot_hide_changed_head_files_or_revision(self):
        scoped = {**self.original, 'source_paths': ['page.js']}
        for field, value in (('head', 'different-head'), ('head', ''), ('head', 12),
                             ('files', {'page.js': 'b' * 64}), ('files', []),
                             ('revision', 'b' * 64)):
            with self.subTest(field=field, value=value):
                self.assert_rejected({**scoped, field: value})

    def test_invalid_inventory_still_fails_with_a_matching_revision(self):
        for files in ({'/tmp/page.js': 'a' * 64}, {'../page.js': 'a' * 64}, {'page.js': 12}):
            body = {'head': self.original['head'], 'files': files}
            self.assert_rejected({**body, 'revision': util.digest(body), 'source_paths': ['page.js']})
        # Keep the consumers' distinct preexisting validators intact.
        for read, files in ((history._snapshot, {'page.js': ''}),
                            (admission._snapshot, {'.git/config': 'a' * 64}),
                            (admission._snapshot, {'docs\\page.js': 'a' * 64})):
            body = {'head': self.original['head'], 'files': files}
            with self.subTest(reader=read.__module__, files=files), self.assertRaises(util.Paused):
                read({**body, 'revision': util.digest(body), 'source_paths': ['page.js']})

    def test_ignored_file_edits_and_deletion_keep_exact_source_identity(self):
        with tempfile.TemporaryDirectory(prefix='native-scoped-source-unit-') as temporary:
            root = Path(temporary)
            subprocess.run(['git', 'init', '-q', str(root)], check=True)
            (root / '.gitignore').write_text('docs/\nsecrets/\n')
            (root / 'page.js').write_text('Original page\n')
            subprocess.run(['git', '-C', str(root), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(root), '-c', 'user.name=Test',
                            '-c', 'user.email=test@example.invalid', 'commit', '-qm', 'Seed'], check=True)
            document = root / 'docs' / 'result.md'
            document.parent.mkdir()
            secret = root / 'secrets' / 'unselected.txt'
            secret.parent.mkdir()
            secret.write_text('Unselected ignored content\n')
            before = source.snapshot(root, paths=['docs/result.md'])
            self.assert_accepted_unchanged(before)
            document.write_text('First version\n')
            subprocess.run(['git', '-C', str(root), 'check-ignore', '-q', 'docs/result.md'], check=True)
            created = source.snapshot(root, paths=['docs/result.md'])
            self.assertNotIn('docs/result.md', util.snapshot(root)['files'])
            self.assertEqual(created['files']['docs/result.md'], util.file_hash(document))
            self.assertNotIn('secrets/unselected.txt', created['files'])
            self.assert_accepted_unchanged(created)
            saved = json.dumps(created, indent=3).encode()
            document.write_text('Second version\n')
            edited = source.snapshot(root, paths=created['source_paths'])
            self.assert_accepted_unchanged(edited)
            self.assertNotEqual(created['revision'], edited['revision'])
            self.assert_rejected({**created, 'files': edited['files']})
            self.assert_rejected({**created, 'revision': edited['revision']})
            self.assertEqual(json.dumps(created, indent=3).encode(), saved)
            document.unlink()
            deleted = source.snapshot(root, paths=created['source_paths'])
            self.assert_accepted_unchanged(deleted)
            self.assertEqual(before, deleted)
            self.assertEqual(util.changed_paths(created, deleted), ['docs/result.md'])


if __name__ == '__main__':
    unittest.main()
