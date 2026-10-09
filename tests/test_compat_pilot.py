"""Executed compatibility proof controls; all credentials are synthetic."""
import json
import shlex
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_compat_prep as compat
import autocode_verify as verify

from tests.test_compat_prep import (
    CANDIDATE_OPS_TESTS,
    CANDIDATE_PILOT_TRACKER,
    CANDIDATE_WINDOW_TESTS,
    CANDIDATE_WINDOW_TRACKER,
    LOCAL_OPERATOR_ADAPTER,
    PILOT_FILES,
    PILOT_NODEIDS,
    WINDOW_FILES,
    WINDOW_NODEIDS,
    Repo,
    mutate_pilot_overlay,
    mutate_window_floor_and_guard,
    sha,
)


class CompatPilotTests(unittest.TestCase):
    def test_destination_overlap_is_rejected_without_changing_candidate(self):
        for kind in ('workspace', 'ancestor', 'child', 'symlink', 'existing-scratch'):
            with self.subTest(kind=kind):
                repo, overlay = self.candidate()
                destination = {'workspace': repo.root, 'ancestor': Path(repo.temp.name),
                               'child': repo.root / 'tests',
                               'symlink': Path(repo.temp.name) / 'alias',
                               'existing-scratch': repo.root / '.autocode/scratch/existing'}[kind]
                if kind == 'symlink':
                    destination.symlink_to(repo.root, target_is_directory=True)
                if kind == 'existing-scratch':
                    destination.mkdir(parents=True)
                    (destination / 'sentinel').write_text('existing scratch data')
                original_guard = (repo.root / 'tests/test_tracker.py').read_bytes()
                original_overlay = overlay.read_bytes()
                result = compat.prepare(repo.root, repo.base, destination, overlay,
                                        overlay_sha256=sha(overlay), expected_nodeids=PILOT_NODEIDS,
                                        suite_files=['tests/test_tracker.py', 'tests/test_tracker_ops.py'],
                                        python=sys.executable, log_dir=repo.evidence)
                self.assertEqual(compat.INCOMPLETE, result['verdict'], result['reasons'])
                self.assertTrue(any('destination' in reason for reason in result['reasons']))
                self.assertIsNone(result['copy'])
                self.assertEqual(CANDIDATE_PILOT_TRACKER, (repo.root / 'tracker.py').read_text())
                self.assertEqual(CANDIDATE_OPS_TESTS, (repo.root / 'tests/test_tracker_ops.py').read_text())
                self.assertEqual(original_guard, (repo.root / 'tests/test_tracker.py').read_bytes())
                self.assertEqual(original_overlay, overlay.read_bytes())
                revision = subprocess.check_output(['git', '-C', str(repo.root), 'rev-parse', 'HEAD'],
                                                   text=True).strip()
                self.assertEqual(repo.base, revision)
                if kind == 'existing-scratch':
                    self.assertEqual('existing scratch data', (destination / 'sentinel').read_text())

    def test_fresh_runner_scratch_copy_remains_supported(self):
        repo, overlay = self.candidate()
        destination = repo.root / '.autocode/scratch/compatibility'
        result = compat.prepare(repo.root, repo.base, destination, overlay,
                                overlay_sha256=sha(overlay), expected_nodeids=PILOT_NODEIDS,
                                suite_files=['tests/test_tracker.py', 'tests/test_tracker_ops.py'],
                                transformations=(LOCAL_OPERATOR_ADAPTER,), python=sys.executable,
                                log_dir=repo.evidence)
        if result['copy']:
            self.addCleanup(verify.remove_tree, repo.root, result['copy'])
        self.assertEqual(compat.PASS, result['verdict'], result['reasons'])
        self.assertEqual(set(PILOT_NODEIDS), set(result['accounting']['passed']))
        self.assertEqual(CANDIDATE_PILOT_TRACKER, (repo.root / 'tracker.py').read_text())
        self.assertEqual(CANDIDATE_OPS_TESTS, (repo.root / 'tests/test_tracker_ops.py').read_text())

    def test_required_nodeid_iterables_are_validated_after_materializing(self):
        repo, overlay = self.candidate()
        for expected in ([], iter(()), (nodeid for nodeid in ())):
            with self.subTest(kind=type(expected).__name__):
                result = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / 'copy', overlay,
                                        overlay_sha256=sha(overlay), expected_nodeids=expected,
                                        suite_files=['tests/test_tracker.py'], python=sys.executable,
                                        log_dir=repo.evidence)
                if result['copy']:
                    verify.remove_tree(repo.root, result['copy'])
                self.assertEqual(compat.INCOMPLETE, result['verdict'], result['reasons'])
                self.assertTrue(any('nodeids' in reason for reason in result['reasons']))
        complete = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / 'complete', overlay,
                                  overlay_sha256=sha(overlay), expected_nodeids=iter(PILOT_NODEIDS),
                                  suite_files=['tests/test_tracker.py', 'tests/test_tracker_ops.py'],
                                  transformations=(LOCAL_OPERATOR_ADAPTER,), python=sys.executable,
                                  log_dir=repo.evidence)
        if complete['copy']:
            self.addCleanup(verify.remove_tree, repo.root, complete['copy'])
        self.assertEqual(compat.PASS, complete['verdict'], complete['reasons'])
        self.assertEqual(set(PILOT_NODEIDS), set(complete['accounting']['passed']))

    def test_erased_overlay_mode_on_unchanged_file_is_rejected(self):
        name = 'policy "guard".sh'
        repo = Repo({**WINDOW_FILES, name: '#!/bin/sh\nexit 0\n'})
        self.addCleanup(repo.close)
        repo.write({'tracker.py': CANDIDATE_WINDOW_TRACKER,
                    'tests/test_tracker_ops.py': CANDIDATE_WINDOW_TESTS})

        def executable(tree):
            target = tree / name
            target.chmod(target.stat().st_mode | 0o111)

        overlay = repo.overlay('unchanged-mode', executable)
        self.assertNotIn(name, verify.changed_files(repo.root, repo.base))
        options = dict(overlay_sha256=sha(overlay), expected_nodeids=WINDOW_NODEIDS,
                       suite_files=['tests/test_tracker.py', 'tests/test_tracker_ops.py'],
                       python=sys.executable, log_dir=repo.evidence)
        intact = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / 'intact', overlay, **options)
        if intact['copy']:
            self.addCleanup(verify.remove_tree, repo.root, intact['copy'])
        self.assertEqual(compat.PASS, intact['verdict'], intact['reasons'])
        self.assertEqual(4, len(intact['accounting']['passed']))
        self.assertEqual({name: 0o100755}, intact['overlay']['modes'])
        self.assertTrue((Path(intact['copy']) / name).stat().st_mode & 0o111)

        destination = Path(repo.temp.name) / 'erased'
        real_run = compat.subprocess.run

        def erase_after_application(command, **kwargs):
            result = real_run(command, **kwargs)
            if command == ['git', '-C', str(destination), 'apply', '-'] and result.returncode == 0:
                target = destination / name
                target.chmod(target.stat().st_mode & ~0o111)
            return result

        with patch.object(compat.subprocess, 'run', side_effect=erase_after_application):
            erased = compat.prepare(repo.root, repo.base, destination, overlay, **options)
        self.assertEqual(compat.INCOMPLETE, erased['verdict'], erased['reasons'])
        self.assertTrue(any('erased overlay' in reason for reason in erased['reasons']))
        self.assertIsNone(erased['copy'])
        self.assertFalse(destination.exists())
        self.assertFalse((repo.root / name).stat().st_mode & 0o111)

    def test_mode_only_overlay_retains_candidate_content_and_executable_bit(self):
        repo = Repo(WINDOW_FILES)
        self.addCleanup(repo.close)
        repo.write({'tracker.py': CANDIDATE_WINDOW_TRACKER,
                    'tests/test_tracker_ops.py': CANDIDATE_WINDOW_TESTS})

        def executable(tree):
            target = tree / 'tracker.py'
            target.chmod(target.stat().st_mode | 0o111)

        overlay = repo.overlay('mode-only', executable)
        self.assertIn(b'new mode 100755', overlay.read_bytes())
        result = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / 'copy', overlay,
                                overlay_sha256=sha(overlay), expected_nodeids=WINDOW_NODEIDS,
                                suite_files=['tests/test_tracker.py', 'tests/test_tracker_ops.py'],
                                python=sys.executable, log_dir=repo.evidence)
        if result['copy']:
            self.addCleanup(verify.remove_tree, repo.root, result['copy'])
        self.assertEqual(compat.PASS, result['verdict'], result['reasons'])
        self.assertEqual(4, len(result['accounting']['passed']))
        self.assertEqual(CANDIDATE_WINDOW_TRACKER, (Path(result['copy']) / 'tracker.py').read_text())
        self.assertTrue((Path(result['copy']) / 'tracker.py').stat().st_mode & 0o111)

    def test_quoted_rename_preserves_candidate_payload_at_destination(self):
        old, new = 'old "name".txt', 'new "name".txt'
        repo = Repo({**WINDOW_FILES, old: 'unchanged payload\n'})
        self.addCleanup(repo.close)
        repo.write({old: 'candidate changed payload\n', 'tracker.py': CANDIDATE_WINDOW_TRACKER,
                    'tests/test_tracker_ops.py': CANDIDATE_WINDOW_TESTS})

        def rename(tree):
            subprocess.run(['git', 'mv', old, new], cwd=tree, check=True, capture_output=True)

        overlay = repo.overlay('rename', rename)
        result = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / 'copy', overlay,
                                overlay_sha256=sha(overlay), expected_nodeids=WINDOW_NODEIDS,
                                suite_files=['tests/test_tracker.py', 'tests/test_tracker_ops.py'],
                                python=sys.executable, log_dir=repo.evidence)
        if result['copy']:
            self.addCleanup(verify.remove_tree, repo.root, result['copy'])
        self.assertEqual(compat.PASS, result['verdict'], result['reasons'])
        self.assertEqual(sorted([old, new]), result['overlay']['touched'])
        self.assertFalse((Path(result['copy']) / old).exists())
        self.assertEqual('candidate changed payload\n', (Path(result['copy']) / new).read_text())

    def test_patch_changed_after_pin_is_not_a_compatibility_pass(self):
        repo = Repo({**WINDOW_FILES, 'z_policy.py': 'POLICY = "unsafe"\n'})
        self.addCleanup(repo.close)
        repo.write({'tracker.py': CANDIDATE_WINDOW_TRACKER,
                    'tests/test_tracker_ops.py': CANDIDATE_WINDOW_TESTS})

        def complete_overlay(tree):
            mutate_window_floor_and_guard(tree)
            (tree / 'z_policy.py').write_text('POLICY = "safe"\n')

        overlay = repo.overlay('complete', complete_overlay)
        full_bytes = overlay.read_bytes()
        partial_bytes = full_bytes.split(b'diff --git a/z_policy.py b/z_policy.py')[0]
        self.assertNotEqual(full_bytes, partial_bytes)
        immutable = Path(repo.temp.name) / 'immutable.patch'
        immutable.write_bytes(full_bytes)
        make_tree = verify.make_tree

        def mutate_after_pin(*args, **kwargs):
            tree = make_tree(*args, **kwargs)
            overlay.write_bytes(partial_bytes)
            return tree

        options = dict(overlay_sha256=sha(overlay), expected_nodeids=WINDOW_NODEIDS,
                       suite_files=['tests/test_tracker.py', 'tests/test_tracker_ops.py'],
                       python=sys.executable, log_dir=repo.evidence)
        with patch.object(verify, 'make_tree', side_effect=mutate_after_pin):
            control = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / 'control',
                                     immutable, **options)
        if control['copy']:
            self.addCleanup(verify.remove_tree, repo.root, control['copy'])
        self.assertEqual(compat.PASS, control['verdict'], control['reasons'])
        self.assertEqual('POLICY = "safe"\n', (Path(control['copy']) / 'z_policy.py').read_text())
        self.assertEqual(options['overlay_sha256'], control['overlay']['applied_sha256'])
        overlay.write_bytes(full_bytes)
        with patch.object(verify, 'make_tree', side_effect=mutate_after_pin):
            result = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / 'copy', overlay, **options)
        self.assertEqual(compat.INCOMPLETE, result['verdict'], result['reasons'])
        self.assertTrue(any('changed' in reason for reason in result['reasons']))
        self.assertIsNone(result['copy'])
        self.assertFalse((Path(repo.temp.name) / 'copy').exists())

    def test_git_quoted_overlay_path_preserves_both_edits(self):
        name = 'my "tracker".txt'
        source = 'candidate=base\n' + 'separator\n' * 7 + 'overlay=base\n'
        original_test = ('import pathlib, unittest\n'
                         'class Guard(unittest.TestCase):\n'
                         '    def test_overlay(self):\n'
                         f'        self.assertIn("overlay=applied", pathlib.Path({name!r}).read_text())\n')
        candidate_test = original_test.replace('test_overlay', 'test_candidate').replace(
            'overlay=applied', 'candidate=changed')
        repo = Repo({name: source, 'tests/__init__.py': '',
                     'tests/test_original.py': original_test})
        self.addCleanup(repo.close)
        repo.write({name: source.replace('candidate=base', 'candidate=changed'),
                    'tests/test_candidate.py': candidate_test})

        def overlay(tree):
            target = tree / name
            target.write_text(target.read_text().replace('overlay=base', 'overlay=applied'))

        overlay_file = repo.overlay('quoted-path', overlay)
        self.assertIn('diff --git "a/my \\"tracker\\".txt"', overlay_file.read_text())
        result = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / 'copy', overlay_file,
                                overlay_sha256=sha(overlay_file), python=sys.executable,
                                expected_nodeids=['tests.test_original.Guard.test_overlay',
                                                  'tests.test_candidate.Guard.test_candidate'],
                                suite_files=['tests/test_original.py', 'tests/test_candidate.py'],
                                log_dir=repo.evidence)
        if result['copy']:
            self.addCleanup(verify.remove_tree, repo.root, result['copy'])
        self.assertEqual(compat.PASS, result['verdict'], result['reasons'])
        self.assertEqual([name], result['overlay']['touched'])
        self.assertEqual(2, len(result['accounting']['passed']))

    def candidate(self):
        repo = Repo(PILOT_FILES)
        self.addCleanup(repo.close)
        repo.write({'tracker.py': CANDIDATE_PILOT_TRACKER,
                    'tests/test_tracker_ops.py': CANDIDATE_OPS_TESTS})
        return repo, repo.overlay('pilot-api', mutate_pilot_overlay)

    def prepare(self, repo, overlay, **extra):
        result = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / 'copy', overlay,
                                overlay_sha256=sha(overlay), expected_nodeids=PILOT_NODEIDS,
                                suite_files=['tests/test_tracker.py', 'tests/test_tracker_ops.py'],
                                python=sys.executable, log_dir=repo.evidence, **extra)
        if result['copy']:
            self.addCleanup(verify.remove_tree, repo.root, result['copy'])
        return result

    def run_suite(self, repo, tree, files, label):
        framework = verify.detect_framework(tree, python=sys.executable)
        return verify.run_suite(framework, framework.targeted(files), tree, repo.evidence, label, timeout=120)

    def test_ac3_omitted_candidate_regression_rejected(self):
        repo, overlay = self.candidate()
        changes = verify.changed_files(repo.root, repo.base)
        old = verify.make_tree(repo.root, repo.base, Path(repo.temp.name) / 'old-green', repo.root,
                               {'tracker.py': changes['tracker.py']})
        self.addCleanup(verify.remove_tree, repo.root, old)
        old_suite = self.run_suite(repo, old, ['tests/test_tracker.py'], 'omitted-old-suite')
        self.assertEqual(0, old_suite['exit_code'], old_suite['tail'])
        self.assertEqual(3, len(old_suite['results']['passed']))
        self.assertFalse((old / 'tests/test_tracker_ops.py').exists())
        make_tree = verify.make_tree

        def omit_regression(*args, **kwargs):
            tree = make_tree(*args, **kwargs)
            (tree / 'tests/test_tracker_ops.py').unlink()
            return tree

        with patch.object(verify, 'make_tree', side_effect=omit_regression):
            result = self.prepare(repo, overlay)
        self.assertEqual(compat.INCOMPLETE, result['verdict'])
        self.assertTrue(any('tests/test_tracker_ops.py' in reason for reason in result['reasons']))

    def test_ac4_candidate_failures_visible_despite_other_passes(self):
        repo, overlay = self.candidate()
        result = self.prepare(repo, overlay)
        self.assertEqual(compat.FAIL, result['verdict'], result['reasons'])
        self.assertEqual(set(PILOT_NODEIDS[:3]), set(result['accounting']['passed']))
        self.assertEqual(set(PILOT_NODEIDS[3:]), set(result['accounting']['failed']))
        self.assertEqual([], result['accounting']['unaccounted'])

    def test_ac5_local_operator_adapter_preserves_preoverlay_behavior(self):
        repo, overlay = self.candidate()
        original_tests = (repo.root / 'tests/test_tracker.py').read_bytes()
        result = self.prepare(repo, overlay, transformations=(LOCAL_OPERATOR_ADAPTER,))
        self.assertEqual(compat.PASS, result['verdict'], result['reasons'])
        self.assertEqual(set(PILOT_NODEIDS), set(result['accounting']['passed']))
        tree = Path(result['copy'])
        self.assertEqual(original_tests, (tree / 'tests/test_tracker.py').read_bytes())
        probe = subprocess.run([sys.executable, '-c',
            "import json, tracker; tracker.reset(); tracker.notify_active('SYNTHETIC-FOREIGN'); "
            "foreign=len(tracker.state['adopted']); tracker.reset(); tracker.notify_active(None); "
            "print(json.dumps({'foreign_adopted':foreign,'no_operator_usage':len(tracker.state['requests'])}))"],
            cwd=tree, capture_output=True, text=True)
        self.assertEqual(0, probe.returncode, probe.stderr)
        self.assertEqual({'foreign_adopted': 0, 'no_operator_usage': 0}, json.loads(probe.stdout))

    def test_ac11_adapter_assertions_catch_broken_variant(self):
        repo, overlay = self.candidate()
        result = self.prepare(repo, overlay, transformations=(LOCAL_OPERATOR_ADAPTER,))
        self.assertEqual(compat.PASS, result['verdict'], result['reasons'])
        tree = Path(result['copy'])
        source = tree / 'tracker.py'
        source.write_text(source.read_text().replace('if from_local_operator:',
                                                     'if False and from_local_operator:')
                          .replace('if not from_local_operator:', 'if True:'))
        broken = self.run_suite(repo, tree, ['tests/test_tracker.py', 'tests/test_tracker_ops.py'], 'broken-policy')
        self.assertNotEqual(0, broken['exit_code'])
        self.assertEqual(set(PILOT_NODEIDS[:3]), set(broken['results']['passed']))
        self.assertEqual(set(PILOT_NODEIDS[3:]), set(broken['results']['failed']))

    def test_ac8_fresh_receipt_preserves_prior_fail(self):
        repo, overlay = self.candidate()
        repo.evidence.mkdir()
        old = repo.evidence / 'old-fail.json'
        old.write_text('{"verdict":"FAIL"}')
        previous = sha(old)
        result = self.prepare(repo, overlay, transformations=(LOCAL_OPERATOR_ADAPTER,))
        self.assertEqual(compat.PASS, result['verdict'], result['reasons'])
        new = compat.write_receipt(repo.evidence, result)
        self.assertNotEqual(old, new)
        self.assertEqual(previous, sha(old))
        self.assertEqual(2, len(list(repo.evidence.glob('*.json'))))

    def test_nonzero_suite_exit_cannot_be_a_compatibility_pass(self):
        repo, overlay = self.candidate()
        command = (f'{shlex.quote(sys.executable)} -m unittest -v '
                   'tests.test_tracker tests.test_tracker_ops; exit 7')
        result = self.prepare(repo, overlay, transformations=(LOCAL_OPERATOR_ADAPTER,),
                              suite_command=command)
        self.assertEqual(7, result['suite']['exit_code'])
        self.assertEqual(set(PILOT_NODEIDS), set(result['accounting']['passed']))
        self.assertEqual(compat.FAIL, result['verdict'])

    def test_missing_required_nodeids_cannot_certify_old_only_suite(self):
        repo, overlay = self.candidate()
        result = compat.prepare(repo.root, repo.base, Path(repo.temp.name) / 'copy', overlay,
                                overlay_sha256=sha(overlay), python=sys.executable,
                                suite_files=['tests/test_tracker.py'], log_dir=repo.evidence)
        self.assertEqual(compat.INCOMPLETE, result['verdict'])
        self.assertTrue(any('nodeids' in reason for reason in result['reasons']))

    def test_partial_change_map_cannot_omit_candidate_source(self):
        repo, overlay = self.candidate()
        repo.write({'policy.py': "CURRENT_POLICY = 'unsafe'\n"})
        changes = verify.changed_files(repo.root, repo.base)
        result = self.prepare(repo, overlay, changes={p: s for p, s in changes.items() if p != 'policy.py'},
                              transformations=(LOCAL_OPERATOR_ADAPTER,))
        self.assertEqual(compat.INCOMPLETE, result['verdict'])
        self.assertTrue(any('policy.py' in reason for reason in result['reasons']))

    def test_absolute_adapter_path_cannot_write_candidate(self):
        repo, overlay = self.candidate()
        source = repo.root / 'tracker.py'
        before = sha(source)
        transform = {'path': str(source), 'rationale': 'misplaced adapter',
                     'transform': lambda text: text.replace('def request_count():', 'def changed_count():')}
        result = self.prepare(repo, overlay, transformations=(transform,))
        self.assertEqual(before, sha(source))
        self.assertEqual(compat.INCOMPLETE, result['verdict'])
        self.assertTrue(any('transformation path' in reason for reason in result['reasons']))

    def test_later_pass_cannot_overwrite_prior_fail_suite_log(self):
        repo, overlay = self.candidate()
        failed = self.prepare(repo, overlay)
        self.assertEqual(compat.FAIL, failed['verdict'])
        previous_log = Path(failed['suite']['output'])
        previous_hash = sha(previous_log)
        self.assertEqual(previous_hash, failed['suite']['output_sha256'])
        verify.remove_tree(repo.root, failed['copy'])
        passed = self.prepare(repo, overlay, transformations=(LOCAL_OPERATOR_ADAPTER,))
        self.assertEqual(compat.PASS, passed['verdict'], passed['reasons'])
        self.assertNotEqual(failed['suite']['output'], passed['suite']['output'])
        self.assertEqual(previous_hash, sha(previous_log))

    def test_repeated_identical_receipts_have_distinct_paths(self):
        repo, _ = self.candidate()
        with patch.object(compat.time, 'strftime', return_value='20261002-000000'):
            first = compat.write_receipt(repo.evidence, {'verdict': 'FAIL'})
            digest = sha(first)
            second = compat.write_receipt(repo.evidence, {'verdict': 'FAIL'})
        self.assertNotEqual(first, second)
        self.assertEqual(digest, sha(first))
        self.assertEqual(2, len(list(repo.evidence.glob('*.json'))))
