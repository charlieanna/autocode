"""Execute retained linked tests against weakening and replacement controls."""
import json
import os
from pathlib import Path
import shlex
import sys
import unittest

import autocode_protected_oracles as guard
import autocode_verify as verify
import autocode_protected_store as store
from .protected_store_fixture import rewrite_archive
from .test_verify import Project, git

ORIGINAL = '''import unittest
from pathlib import Path
from layout import height
class Layout(unittest.TestCase):
    def test_original(self):
        self.assertEqual(Path(__file__).resolve().parent.name, "shared")
        self.assertEqual(height(), 40)
'''


class ProtectedLinkTests(unittest.TestCase):
    def setUp(self):
        self.project = Project({'layout.py': 'def height(): return 20\n',
                                'shared/oracle.py': ORIGINAL, '.gitignore': '.autocode/\nignored/\n'})
        self.addCleanup(self.project.close)
        self.root = self.project.root
        (self.root / 'shared/bridge.py').symlink_to('oracle.py')
        (self.root / 'test_layout.py').symlink_to('shared/bridge.py')
        git(self.root, 'add', '.')
        git(self.root, '-c', 'user.name=t', '-c', 'user.email=t@example.test', 'commit', '-qm', 'links')
        self.command = shlex.join([sys.executable, '-B', '-m', 'unittest', 'discover', '-v'])
        self.record = guard.retain(self.root, self.project.evidence,
                                   guard.inventory(self.root, verify.is_test_path), self.command)
        self.state = {'settings': {'protected_tests': self.record}}

    def replay(self):
        return guard.replay(self.state, self.root, self.project.evidence / 'replays', verify.scratch_run, timeout=10)

    def rejected_receipt(self):
        with self.assertRaisesRegex(ValueError, 'original gate did not pass'):
            self.replay()
        receipts = list((self.project.evidence / 'replays').glob('protected-tests/*/receipt.json'))
        self.assertEqual(1, len(receipts))
        receipt = json.loads(receipts[0].read_text())
        self.assertEqual(0, receipt['candidate']['exit_code'])
        self.assertEqual(1, receipt['original']['exit_code'])
        self.assertIn('20 != 40', receipt['original']['tail'])
        return receipt

    def test_complete_chain_and_non_test_target_are_retained(self):
        self.assertEqual({'test_layout.py', 'shared/bridge.py', 'shared/oracle.py'}, set(self.record['files']))
        with store.opened(self.record, self.root) as retained:
            self.assertEqual('shared/bridge.py', os.readlink(retained / 'test_layout.py'))
            self.assertEqual('oracle.py', os.readlink(retained / 'shared/bridge.py'))
            self.assertEqual(ORIGINAL, (retained / 'shared/oracle.py').read_text())
        result = self.replay()
        self.assertEqual([], result['changed_tests'])
        self.assertEqual('PASS', result['verdict'])

    def test_target_weakening_cannot_replace_original_assertion(self):
        (self.root / 'shared/oracle.py').write_text(ORIGINAL.replace('height(), 40', 'height(), 20'))
        before = verify.util.snapshot(self.root)
        receipt = self.rejected_receipt()
        self.assertIn('shared/oracle.py', receipt['changed_tests'])
        self.assertEqual(before, verify.util.snapshot(self.root))

    def test_real_fix_preserves_file_resolution_semantics_during_replay(self):
        (self.root / 'layout.py').write_text('def height(): return 40\n')
        (self.root / 'shared/oracle.py').write_text(ORIGINAL + '\n# Added comment in candidate\n')
        result = self.replay()
        self.assertEqual('PASS', result['verdict'])
        self.assertEqual(0, result['original']['exit_code'])
        self.assertTrue((self.root / 'test_layout.py').is_symlink())
        self.assertTrue((self.root / 'shared/bridge.py').is_symlink())

    def test_retargeted_link_cannot_silently_replace_gate(self):
        (self.root / 'shared/replacement.py').write_text(ORIGINAL.replace('height(), 40', 'height(), 20'))
        link = self.root / 'test_layout.py'
        link.unlink(); link.symlink_to('shared/replacement.py')
        receipt = self.rejected_receipt()
        self.assertIn('test_layout.py', receipt['changed_tests'])
        self.assertEqual('shared/replacement.py', os.readlink(link))

    def test_original_restoration_never_writes_through_replaced_parent(self):
        external = Path(self.project.temp.name) / 'external'
        external.mkdir()
        (external / 'oracle.py').write_text(ORIGINAL.replace('height(), 40', 'height(), 20')
                                           .replace('parent.name, "shared"', 'parent.name, "external"'))
        (external / 'bridge.py').symlink_to('oracle.py')
        (self.root / 'shared').rename(self.root / 'old-shared')
        (self.root / 'shared').symlink_to(external, target_is_directory=True)
        before = (external / 'oracle.py').read_bytes()
        self.rejected_receipt()
        self.assertEqual(before, (external / 'oracle.py').read_bytes())
        self.assertTrue((self.root / 'shared').is_symlink())

    def test_unbound_or_unsafe_link_targets_are_rejected_at_capture(self):
        (self.root / 'ignored').mkdir()
        (self.root / 'ignored/oracle.py').write_text(ORIGINAL)
        (self.root / 'directory-link').symlink_to('shared', target_is_directory=True)
        (self.root / 'cycle-a.py').symlink_to('cycle-b.py')
        (self.root / 'cycle-b.py').symlink_to('cycle-a.py')
        external = Path(self.project.temp.name) / 'outside.py'
        external.write_text(ORIGINAL)
        targets = ['ignored/oracle.py', 'missing.py', '../outside.py',
                   str(self.root / 'shared/oracle.py'), 'test_layout.py', 'cycle-a.py',
                   'directory-link/oracle.py', 'directory-link/../shared/oracle.py']
        for target in targets:
            with self.subTest(target=target):
                link = self.root / 'test_layout.py'; link.unlink(); link.symlink_to(target)
                with self.assertRaises(ValueError):
                    guard.inventory(self.root, verify.is_test_path)

    def test_relative_parent_link_is_supported(self):
        nested = self.root / 'client/v3'
        nested.mkdir(parents=True)
        (nested / 'sample_test.go').symlink_to('../../shared/oracle.py')
        files = guard.inventory(self.root, verify.is_test_path)
        record = guard.retain(self.root, self.project.evidence, files, self.command)
        with store.opened(record, self.root) as retained:
            self.assertEqual('../../shared/oracle.py', os.readlink(retained / 'client/v3/sample_test.go'))
            self.assertEqual(ORIGINAL, (retained / 'client/v3/sample_test.go').read_text())

    def test_retained_target_and_link_tampering_are_rejected(self):
        rewrite_archive(self.record, {'shared/oracle.py': ORIGINAL.replace('height(), 40', 'height(), 20').encode()})
        with self.assertRaisesRegex(ValueError, 'bundle changed'):
            self.replay()
        rewrite_archive(self.record, {'shared/oracle.py': ORIGINAL.encode(), 'test_layout.py': b'shared/oracle.py'})
        with self.assertRaisesRegex(ValueError, 'bundle changed'):
            self.replay()

    def test_incomplete_link_bundle_is_rejected_even_with_a_matching_digest(self):
        record = dict(self.record, files=dict(self.record['files']))
        del record['files']['shared/oracle.py']
        record.pop('inventory_path')
        record['binding_hash'] = verify.util.digest(guard.body(record))
        with self.assertRaisesRegex(ValueError, 'source snapshot'):
            guard.verify_binding(record)

    def test_version_one_regular_bindings_keep_their_original_identity(self):
        files = {'shared/oracle.py': guard.identity(self.root / 'shared/oracle.py')}
        record = guard.retain(self.root, self.project.evidence, files, self.command)
        self.assertEqual(1, record['version'])
        self.assertEqual({'version': 1, 'files': files, 'command': self.command}, guard.body(record))
        self.assertEqual(record, guard.verify_binding(record))
