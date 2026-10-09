"""Source-backed initial public target controls, without importing project code."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_risk_targets as targets


class RiskTargetsTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)

    def source(self, path, text):
        file = self.root / path
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_bytes(text.encode('utf-8'))
        return file

    def imported_package(self):
        self.source('public/__init__.py', '"""Implement the public API."""\n')
        self.source('tests/test_import.py', 'import unittest\nimport public\n')
        return targets.capture(self.root)

    def test_actual_ladder_blank_packages_are_targets_through_original_test_imports(self):
        catalog = Path(__file__).resolve().parents[1] / 'scenarios' / 'catalog'
        for scenario, module in [('ladder-18-durable-lease-queue', 'leasequeue'),
                                 ('ladder-19-transactional-outbox', 'outbox')]:
            with self.subTest(scenario=scenario):
                seed = catalog / scenario / 'seed'
                artifact = targets.capture(seed)
                self.assertEqual(targets.verify(json.loads(json.dumps(artifact))), artifact)
                self.assertEqual(artifact['targets'], [{
                    'module': module, 'path': module + '/__init__.py',
                    'kind': 'initial_public_import',
                    'sha256': hashlib.sha256((seed / module / '__init__.py').read_bytes()).hexdigest()}])
                self.assertIn('tests/test_import.py', [row['path'] for row in artifact['files']])
                self.assertNotIn('tests', [row['module'] for row in artifact['targets']])

    def test_top_level_public_definition_is_retained_without_import_or_execution(self):
        marker = self.root / 'executed'
        self.source('public.py', "class Queue:\n    pass\n" +
                    'open(' + repr(str(marker)) + ", 'w').write('executed')\n")
        artifact = targets.capture(self.root)
        self.assertEqual(artifact['targets'][0]['module'], 'public')
        self.assertEqual(artifact['targets'][0]['kind'], 'initial_public_definition')
        self.assertEqual(targets.verify(artifact), artifact)
        self.assertFalse(marker.exists())

    def test_definition_wins_over_import_without_duplicate_module_targets(self):
        self.source('public.py', 'class Queue:\n    pass\n')
        self.source('tests.py', 'import public\nclass TestQueue:\n    pass\n')
        artifact = targets.capture(self.root)
        self.assertEqual([(row['module'], row['kind']) for row in artifact['targets']],
                         [('public', 'initial_public_definition')])

    def test_src_dotted_modules_and_parent_package_import_provenance(self):
        self.source('src/pkg/__init__.py', '"""API"""\n')
        self.source('src/pkg/model.py', 'class Queue:\n    pass\n')
        self.source('tests/test_api.py', 'from pkg import model\n')
        artifact = targets.capture(self.root)
        self.assertEqual([(row['module'], row['path'], row['kind']) for row in artifact['targets']],
                         [('pkg', 'src/pkg/__init__.py', 'initial_public_import'),
                          ('pkg.model', 'src/pkg/model.py', 'initial_public_definition')])
        self.assertEqual(targets.verify(artifact), artifact)

    def test_local_relative_imports_are_resolved_without_module_execution(self):
        self.source('pkg/__init__.py', 'from . import storage\n')
        self.source('pkg/storage.py', '"""Storage facade."""\n')
        self.source('tests/test_api.py', 'import pkg\n')
        artifact = targets.capture(self.root)
        self.assertEqual([row['module'] for row in artifact['targets']], ['pkg', 'pkg.storage'])
        self.assertEqual(targets.verify(artifact), artifact)

    def test_builtin_absent_private_and_test_modules_are_not_targets(self):
        self.source('public.py', 'import math, sys, ghost, _hidden\nfrom tests import helper\n')
        self.source('_hidden.py', 'class Hidden:\n    pass\n')
        self.source('tests/helper.py', 'class TestHelper:\n    pass\n')
        self.assertEqual(targets.capture(self.root)['targets'], [])

    def test_nested_or_private_class_does_not_invent_public_definition(self):
        self.source('public.py', 'class _Hidden:\n    pass\ndef factory():\n    class Nested:\n        pass\n')
        self.assertEqual(targets.capture(self.root)['targets'], [])

    def test_excluded_dependency_and_output_sources_are_not_read_or_targets(self):
        for directory in ('.venv', '.git', 'node_modules', 'vendor', 'build', 'outputs'):
            self.source(directory + '/unsafe.py', 'not valid python!')
        artifact = self.imported_package()
        self.assertEqual([row['path'] for row in artifact['files']],
                         ['public/__init__.py', 'tests/test_import.py'])

    def test_ambiguous_root_and_src_module_mapping_fails_closed(self):
        self.source('public.py', 'class Queue:\n    pass\n')
        self.source('src/public.py', 'class Different:\n    pass\n')
        with self.assertRaises(ValueError):
            targets.capture(self.root)

    def test_source_file_symlink_is_refused(self):
        file = self.source('real.py', 'class Queue:\n    pass\n')
        (self.root / 'public.py').symlink_to(file)
        with self.assertRaises(ValueError):
            targets.capture(self.root)

    def test_directory_and_workspace_symlinks_are_refused(self):
        directory = self.root / 'real'
        directory.mkdir()
        (self.root / 'linked').symlink_to(directory, target_is_directory=True)
        with self.assertRaises(ValueError):
            targets.capture(self.root)
        with self.assertRaises(ValueError):
            targets.capture(self.root / 'linked')

    def test_fifo_python_source_is_rejected_without_blocking(self):
        import os
        os.mkfifo(self.root / 'public.py')
        with self.assertRaises(ValueError):
            targets.capture(self.root)

    def test_source_bytes_and_crlf_are_retained_exactly(self):
        self.source('public.py', 'class Queue:\r\n    pass\r\n')
        artifact = targets.capture(self.root)
        row = artifact['files'][0]
        self.assertIn('\r\n', row['text'])
        self.assertEqual(row['sha256'], hashlib.sha256((self.root / 'public.py').read_bytes()).hexdigest())
        self.assertEqual(targets.verify(artifact), artifact)

    def test_capture_rejects_malformed_or_non_utf8_python_evidence(self):
        path = self.source('public.py', 'class Queue:\n')
        with self.assertRaises(ValueError):
            targets.capture(self.root)
        path.write_bytes(b'\xff')
        with self.assertRaises(ValueError):
            targets.capture(self.root)

    def test_changed_candidate_cannot_rebind_initial_evidence(self):
        artifact = self.imported_package()
        self.source('public/__init__.py', 'class CandidateQueue:\n    pass\n')
        self.source('tests/test_import.py', 'import invented\n')
        self.source('invented.py', 'class Invented:\n    pass\n')
        self.assertEqual(targets.verify(artifact), artifact)
        self.assertEqual(artifact['targets'][0]['kind'], 'initial_public_import')
        self.assertNotEqual(targets.capture(self.root)['targets'], artifact['targets'])

    def test_missing_import_provenance_is_rejected(self):
        artifact = self.imported_package()
        artifact['files'] = [row for row in artifact['files'] if row['path'] != 'tests/test_import.py']
        with self.assertRaises(ValueError):
            targets.verify(artifact)

    def test_retained_source_byte_tampering_is_rejected(self):
        artifact = self.imported_package()
        artifact['files'][0]['text'] += '\n# Changed\n'
        with self.assertRaises(ValueError):
            targets.verify(artifact)

    def test_rehashed_provenance_changes_still_require_rederived_targets(self):
        artifact = self.imported_package()
        row = next(row for row in artifact['files'] if row['path'] == 'tests/test_import.py')
        row['text'] = 'import missing\n'
        row['sha256'] = hashlib.sha256(row['text'].encode()).hexdigest()
        with self.assertRaises(ValueError):
            targets.verify(artifact)

    def test_invented_target_and_target_hash_are_rejected(self):
        artifact = self.imported_package()
        for change in ({'module': 'invented'}, {'sha256': '0' * 64},
                       {'kind': 'initial_public_definition'}, {'kind': 'approved_deliverable'},
                       {'path': 'public.py'}, {'kind': []}):
            with self.subTest(change=change):
                forged = copy.deepcopy(artifact)
                forged['targets'][0].update(change)
                with self.assertRaises(ValueError):
                    targets.verify(forged)

    def test_unsafe_or_excluded_retained_paths_are_rejected(self):
        artifact = self.imported_package()
        for path in ('../public.py', '/public.py', 'src/../public.py', 'public//a.py',
                     'public\\a.py', '.venv/public.py', 'vendor/public.py', 'public.py/evil'):
            with self.subTest(path=path):
                forged = copy.deepcopy(artifact)
                forged['files'][0]['path'] = path
                with self.assertRaises(ValueError):
                    targets.verify(forged)

    def test_duplicate_files_modules_unknown_fields_and_versions_are_rejected(self):
        artifact = self.imported_package()
        examples = []
        duplicate = copy.deepcopy(artifact)
        duplicate['files'].append(duplicate['files'][0].copy())
        examples.append(duplicate)
        duplicate = copy.deepcopy(artifact)
        duplicate['targets'].append(duplicate['targets'][0].copy())
        examples.append(duplicate)
        examples += [artifact | {'version': True}, artifact | {'version': 2}, artifact | {'extra': True}]
        for forged in examples:
            with self.subTest(forged=forged):
                with self.assertRaises(ValueError):
                    targets.verify(forged)

    def test_per_file_total_file_count_and_scan_entry_limits_are_enforced(self):
        self.source('public.py', '# ' + 'x' * 32)
        with patch.object(targets, 'MAX_FILE_BYTES', 16):
            with self.assertRaises(ValueError):
                targets.capture(self.root)
        artifact = targets.capture(self.root)
        with patch.object(targets, 'MAX_TOTAL_BYTES', 16):
            with self.assertRaises(ValueError):
                targets.verify(artifact)
            with self.assertRaises(ValueError):
                targets.capture(self.root)
        self.source('other.py', '# other\n')
        with patch.object(targets, 'MAX_FILES', 1):
            with self.assertRaises(ValueError):
                targets.capture(self.root)
            with self.assertRaises(ValueError):
                targets.verify(artifact | {'files': artifact['files'] * 2})
        with patch.object(targets, 'MAX_SCAN_ENTRIES', 1):
            with self.assertRaises(ValueError):
                targets.capture(self.root)

    def test_unsafe_or_unbounded_target_module_identifiers_are_rejected(self):
        artifact = self.imported_package()
        for module in ('public;exec', '_private', 'tests.test_import', 'public..child',
                       'public/child', 'x' * 1025, '.'.join(['x'] * 65)):
            with self.subTest(module=module):
                forged = copy.deepcopy(artifact)
                forged['targets'][0]['module'] = module
                with self.assertRaises(ValueError):
                    targets.verify(forged)

    def test_verification_returns_sorted_independent_canonical_artifact(self):
        artifact = self.imported_package()
        reversed_artifact = copy.deepcopy(artifact)
        reversed_artifact['files'].reverse()
        canonical = targets.verify(reversed_artifact)
        self.assertEqual(canonical, artifact)
        canonical['files'][0]['text'] = 'locally changed'
        self.assertNotEqual(canonical['files'], artifact['files'])


if __name__ == '__main__':
    unittest.main()
