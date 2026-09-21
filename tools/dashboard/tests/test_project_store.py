"""Offline checks for reversible dashboard-only project removal."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from dashboard_projects import ProjectStore


class ProjectStoreTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.temp = Path(temporary.name).resolve()
        self.root = self.temp / 'dashboard'
        self.store = ProjectStore(self.root)

    def project(self, name='project'):
        path = self.temp / name
        path.mkdir()
        (path / 'README.md').write_text('Keep the project files.\n')
        return path

    def test_default_directory_honors_dashboard_then_autocode_home(self):
        with patch.dict(os.environ, {'AUTOCODE_HOME': str(self.temp / 'autocode'),
                                     'AUTOCODE_DASHBOARD_HOME': str(self.temp / 'custom')}):
            self.assertEqual(self.temp / 'custom', ProjectStore().root)
        with patch.dict(os.environ, {'AUTOCODE_HOME': str(self.temp / 'autocode')}):
            os.environ.pop('AUTOCODE_DASHBOARD_HOME', None)
            self.assertEqual(self.temp / 'autocode' / 'dashboard', ProjectStore().root)
        self.assertFalse(self.root.exists())

    def test_reading_missing_store_never_creates_data(self):
        self.assertEqual([], self.store.list())
        self.assertFalse(self.root.exists())
        self.root.mkdir()
        self.assertEqual([], self.store.list())
        self.assertEqual([], list(self.root.iterdir()))
        self.store.path.write_text('{"version":1,"removed":[]}')
        before = {path.name: path.read_bytes() for path in self.root.iterdir()}
        self.assertEqual([], self.store.list())
        self.assertEqual(before, {path.name: path.read_bytes() for path in self.root.iterdir()})

    def test_removal_is_private_idempotent_and_survives_restart(self):
        project = self.project()
        registry = self.temp / 'registry.json'
        registry.write_text('{"projects": ["keep"]}')
        initial_files = {str(p.relative_to(self.temp)): p.read_bytes()
                         for p in self.temp.rglob('*') if p.is_file()}
        first = self.store.remove(str(project))
        self.assertEqual({'workspace', 'removed_at'}, set(first))
        self.assertEqual(str(project), first['workspace'])
        self.assertIsNotNone(datetime.fromisoformat(first['removed_at']).tzinfo)
        self.assertEqual(first, ProjectStore(self.root).remove(str(project)))
        self.assertEqual([first], ProjectStore(self.root).list())
        self.assertEqual(0o700, self.root.stat().st_mode & 0o777)
        self.assertEqual(0o600, self.store.path.stat().st_mode & 0o777)
        self.assertEqual(0o600, self.store.lock_path.stat().st_mode & 0o777)
        for name, original in initial_files.items():
            self.assertEqual(original, (self.temp / name).read_bytes())
        result = self.store.restore(str(project))
        self.assertEqual({'workspace': str(project), 'restored': True, 'removed': True}, result)
        self.assertEqual([], ProjectStore(self.root).list())
        self.assertFalse(self.store.restore(str(project))['removed'])
        self.assertEqual('Keep the project files.\n', (project / 'README.md').read_text())

    def test_normalizes_aliases_dot_segments_and_home(self):
        project = self.project()
        alias = self.temp / 'alias'
        alias.symlink_to(project, target_is_directory=True)
        row = self.store.remove(str(alias / '..' / 'alias'))
        self.assertEqual(str(project), row['workspace'])
        self.assertEqual(row, self.store.remove(str(project)))
        with patch.dict(os.environ, {'HOME': str(self.temp)}):
            self.assertEqual(row, self.store.remove('~/project'))
        self.assertTrue(self.store.restore(str(alias))['removed'])

    def test_restores_missing_saved_path_without_recreating_project(self):
        missing = self.temp / 'missing'
        self.store.remove(str(missing))
        self.assertFalse(missing.exists())
        result = ProjectStore(self.root).restore(str(missing))
        self.assertTrue(result['removed'])
        self.assertEqual([], self.store.list())
        self.assertFalse(missing.exists())

    def test_remove_and_restore_match_saved_identity_before_new_symlink_target(self):
        missing = self.temp / 'old-project'
        first = self.store.remove(str(missing))
        other = self.project('other-project')
        missing.symlink_to(other, target_is_directory=True)
        self.assertEqual(first, ProjectStore(self.root).remove(str(missing)))
        self.assertEqual([first], self.store.list())
        self.store.remove(str(other))
        self.assertEqual(str(missing), self.store.restore(str(missing))['workspace'])
        self.assertEqual([str(other)], [row['workspace'] for row in self.store.list()])
        self.assertTrue(missing.is_symlink())
        self.assertTrue((other / 'README.md').exists())

    def test_validates_paths_before_writing(self):
        for bad in (None, 3, {}, [], '', ' ', 'relative', './project', '/tmp/bad\x00path'):
            with self.subTest(path=bad):
                with self.assertRaises(ValueError):
                    self.store.remove(bad)
                with self.assertRaises(ValueError):
                    self.store.restore(bad)
        self.assertFalse(self.root.exists())

    def test_two_instances_preserve_concurrent_removals_and_restores(self):
        stores = [self.store, ProjectStore(self.root)]
        barrier = threading.Barrier(2)
        def remove_many(index):
            barrier.wait(timeout=5)
            for number in range(12):
                stores[index].remove(str(self.temp / f'project-{index}-{number}'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(remove_many, range(2)))
        self.assertEqual(24, len(self.store.list()))
        def restore_many(index):
            barrier.wait(timeout=5)
            for number in range(6):
                stores[index].restore(str(self.temp / f'project-{index}-{number}'))
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(restore_many, range(2)))
        self.assertEqual(12, len(ProjectStore(self.root).list()))

    def test_separate_processes_preserve_concurrent_removals(self):
        code = ('from dashboard_projects import ProjectStore; import sys; '
                'store=ProjectStore(sys.argv[1]); '
                '[store.remove(sys.argv[2]+"/"+sys.argv[3]+"-"+str(i)) for i in range(15)]')
        processes = [subprocess.Popen([sys.executable, '-c', code, str(self.root), str(self.temp), name],
                                      cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for name in ('first', 'second')]
        for process in processes:
            try:
                out, err = process.communicate(timeout=15)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.wait()
            self.assertEqual(0, process.returncode, out + err)
        self.assertEqual(30, len(self.store.list()))

    def test_corrupt_schema_and_unknown_version_are_never_overwritten(self):
        self.root.mkdir()
        valid_row = {'workspace': str(self.temp / 'project'), 'removed_at': '2026-09-20T00:00:00+00:00'}
        invalid = [b'not-json', b'\xff', b'[]', b'{"version":2,"removed":[]}',
                   b'{"version":true,"removed":[]}', b'{"version":1,"removed":{}}',
                   json.dumps({'version': 1, 'removed': [valid_row, valid_row]}).encode(),
                   json.dumps({'version': 1, 'removed': [{**valid_row, 'workspace': 'relative'}]}).encode(),
                   json.dumps({'version': 1, 'removed': [{**valid_row, 'removed_at': 'yesterday'}]}).encode(),
                   json.dumps({'version': 1, 'removed': [{**valid_row, 'removed_at': '2026-09-20'}]}).encode()]
        for payload in invalid:
            with self.subTest(payload=payload):
                self.store.path.write_bytes(payload)
                for operation in (self.store.list, lambda: self.store.remove(str(self.temp / 'new')),
                                  lambda: self.store.restore(str(self.temp / 'new'))):
                    with self.assertRaisesRegex(ValueError, 'Restore or repair removed-projects.json'):
                        operation()
                    self.assertEqual(payload, self.store.path.read_bytes())

    def test_symlink_store_and_lock_are_rejected_without_touching_target(self):
        self.root.mkdir()
        target = self.temp / 'target.json'
        target.write_text('{"version":1,"removed":[]}')
        for link in (self.store.path, self.store.lock_path):
            with self.subTest(link=link.name):
                link.symlink_to(target)
                for operation in (self.store.list, lambda: self.store.remove(str(self.temp / 'new')),
                                  lambda: self.store.restore(str(self.temp / 'new'))):
                    with self.assertRaisesRegex(ValueError, 'not a regular file'):
                        operation()
                    self.assertEqual('{"version":1,"removed":[]}', target.read_text())
                link.unlink()
        self.store.path.symlink_to(self.temp / 'missing-target')
        with self.assertRaises(ValueError):
            self.store.list()
        self.assertFalse((self.temp / 'missing-target').exists())

    def test_directory_in_place_of_store_or_root_is_rejected(self):
        self.root.mkdir()
        self.store.path.mkdir()
        with self.assertRaises(ValueError):
            self.store.list()
        self.store.path.rmdir()
        self.root.rmdir()
        target = self.temp / 'elsewhere'
        target.mkdir()
        self.root.symlink_to(target, target_is_directory=True)
        with self.assertRaises(ValueError):
            self.store.list()
        with self.assertRaises(ValueError):
            self.store.remove(str(self.temp / 'project'))
        self.assertEqual([], list(target.iterdir()))


if __name__ == '__main__':
    unittest.main()
