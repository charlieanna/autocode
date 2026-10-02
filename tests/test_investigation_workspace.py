"""The runner provides a complete, isolated repository for bug reproduction."""
import tempfile
import unittest
from pathlib import Path

import autocode_investigation_workspace as investigation_workspace


class ScratchRootTests(unittest.TestCase):
    def test_root_is_inside_workspace_and_does_not_replace_existing_evidence(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace = Path(temporary)
            evidence = workspace / '.autocode/investigation/previous/result.txt'
            evidence.parent.mkdir(parents=True)
            evidence.write_text('retained')
            root = investigation_workspace.scratch_root(workspace)
            self.assertEqual(workspace.resolve() / '.autocode/investigation', root)
            self.assertEqual('retained', evidence.read_text())

    def test_symlinked_runner_directory_is_rejected_before_external_write(self):
        with tempfile.TemporaryDirectory() as temporary:
            workspace, outside = Path(temporary) / 'repo', Path(temporary) / 'outside'
            workspace.mkdir()
            outside.mkdir()
            (workspace / '.autocode').symlink_to(outside, target_is_directory=True)
            with self.assertRaisesRegex(ValueError, 'symlink'):
                investigation_workspace.scratch_root(workspace)
            self.assertEqual([], list(outside.iterdir()))


class SourceFilterTests(unittest.TestCase):
    def test_excludes_repository_metadata_and_runner_state_at_every_depth(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            self.assertEqual({'.git', '.autocode'}, set(investigation_workspace.ignored_entries(
                root, root, ['.git', '.autocode', 'accessors', 'rule', 'vendor', 'go.mod'])))

    def test_internal_file_symlink_is_safe_to_copy_as_contents(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'source.txt').write_text('source')
            (root / 'alias.txt').symlink_to('source.txt')
            self.assertEqual(set(), set(investigation_workspace.ignored_entries(root, root, ['alias.txt'])))

    def test_external_state_and_directory_symlinks_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary) / 'repo'
            root.mkdir()
            outside = Path(temporary) / 'outside.txt'
            outside.write_text('private')
            (root / '.autocode').mkdir()
            (root / '.autocode/state.json').write_text('retained')
            (root / 'source').mkdir()
            for name, target in [('outside-link', outside), ('state-link', root / '.autocode/state.json'),
                                 ('directory-link', root / 'source')]:
                with self.subTest(name=name):
                    (root / name).symlink_to(target)
                    with self.assertRaisesRegex(ValueError, 'symlink'):
                        investigation_workspace.ignored_entries(root, root, [name])


class PrepareTests(unittest.TestCase):
    def test_a_directory_named_venv_without_a_virtualenv_is_still_copied(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'venv').mkdir()
            (root / 'venv/source.py').write_text('value = 42\n')
            scratch = investigation_workspace.prepare(root)
            self.assertEqual('value = 42\n', (scratch / 'venv/source.py').read_text())

    def test_prepares_all_source_dependencies_and_non_go_resources_without_metadata(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            contents = {'go.mod': 'module example', 'accessors/datainterface/model.go': 'package datainterface',
                        'rule/rule.go': 'package rule', 'internal/utils/slice.go': 'package utils',
                        'vendor/modules.txt': 'offline', 'vendor/native/lib.so': 'native',
                        'config/settings.yaml': 'setting: value', '.git/config': 'metadata',
                        '.autocode/runs/old/state.json': 'retained'}
            for name, text in contents.items():
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text)
            scratch = investigation_workspace.prepare(root)
            self.assertEqual(root.resolve() / '.autocode/investigation', scratch.parent)
            for name, text in contents.items():
                if name.startswith(('.git/', '.autocode/')):
                    self.assertFalse((scratch / name).exists())
                else:
                    self.assertEqual(text, (scratch / name).read_text(), name)
                self.assertEqual(text, (root / name).read_text(), name)
            (scratch / 'internal/utils/slice.go').write_text('scratch edit')
            self.assertEqual('package utils', (root / 'internal/utils/slice.go').read_text())

    def test_each_attempt_gets_a_fresh_copy_and_retains_prior_scratch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'code.py').write_text('original')
            first = investigation_workspace.prepare(root)
            (first / 'code.py').write_text('first attempt')
            second = investigation_workspace.prepare(root)
            self.assertNotEqual(first, second)
            self.assertEqual('first attempt', (first / 'code.py').read_text())
            self.assertEqual('original', (second / 'code.py').read_text())

    def test_internal_file_symlink_is_materialized_and_cannot_write_original(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'source.txt').write_text('original')
            (root / 'alias.txt').symlink_to('source.txt')
            scratch = investigation_workspace.prepare(root)
            self.assertFalse((scratch / 'alias.txt').is_symlink())
            (scratch / 'alias.txt').write_text('scratch edit')
            self.assertEqual('original', (root / 'source.txt').read_text())


if __name__ == '__main__':
    unittest.main()
