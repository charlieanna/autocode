"""Prepare real Git revisions through the Arena CLI without network or model calls."""
import contextlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('arena_prepare', ROOT / 'arena/prepare.py')
prepare = importlib.util.module_from_spec(spec)
spec.loader.exec_module(prepare)


class CatalogTests(unittest.TestCase):
    def test_catalog_lists_real_pinned_projects_without_preparing(self):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, prepare.main(['--list']))
        cases = json.loads(output.getvalue())
        self.assertEqual({'Boltons', 'Humanize', 'More Itertools', 'SymPy', 'Django', 'pytest', 'TypeScript', 'Go x/net/http2'},
                         {c['project'] for c in cases})
        self.assertEqual({'development', 'regression', 'holdout'}, {c['split'] for c in cases})
        self.assertEqual({'starter', 'hard'}, {c['tier'] for c in cases})
        for case in cases:
            for field in ('base_commit', 'reference_commit'):
                self.assertRegex(case[field], r'^[0-9a-f]{40}$')
            self.assertNotEqual(case['base_commit'], case['reference_commit'])
            fixture = ROOT / 'arena/cases' / case['id']
            self.assertTrue((fixture / 'oracle.py').is_file())
            self.assertIn('problem-only adaptation', json.loads((fixture / 'issue.json').read_text())['provenance'])

    def test_prepare_validates_controls_and_keeps_future_fix_out_of_baseline(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repo = root / 'upstream'
            repo.mkdir()
            def git(*args):
                return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()
            git('init', '-q')
            (repo / 'answer.py').write_text('print("broken")\n')
            git('add', '.')
            git('-c', 'user.name=T', '-c', 'user.email=t@example.test', 'commit', '-qm', 'baseline')
            base = git('rev-parse', 'HEAD')
            (repo / 'answer.py').write_text('print("correct")\n')
            git('add', '.')
            git('-c', 'user.name=T', '-c', 'user.email=t@example.test', 'commit', '-qm', 'fix')
            fixed = git('rev-parse', 'HEAD')
            fixture = root / 'cases/demo'
            fixture.mkdir(parents=True)
            (fixture / 'issue.json').write_text(json.dumps({'title': 'Correct output', 'body': 'Print correct.'}))
            (fixture / 'oracle.py').write_text(
                'import json, subprocess, sys\nfrom pathlib import Path\n'
                'result = subprocess.run([sys.executable, "-B", str(Path(sys.argv[1]) / "answer.py")], '
                'capture_output=True, text=True, timeout=5)\n'
                'print(json.dumps({"checks": [{"name": "output", "ok": '
                'result.returncode == 0 and result.stdout == "correct\\n"}]}))\n')
            case = dict(id='demo', repository=str(repo), base_commit=base, reference_commit=fixed,
                        issue_ref='example/demo#1', split='development', checks=['output'], test_overlay=True)
            overlay = fixture / 'test_overlay/tests/preservation.test.cjs'
            overlay.parent.mkdir(parents=True)
            overlay.write_text('// Evaluator-owned preservation adapter, no runtime implementation.\n')
            destination = root / 'arena'
            prepare.prepare(destination, [case], catalog_root=root, oracle_timeout=5)
            catalog = json.loads((destination / 'catalog.json').read_text())
            self.assertEqual(['demo'], [row['id'] for row in catalog['cases']])
            controls = json.loads((destination / 'cases/demo/controls.json').read_text())
            self.assertFalse(controls['negative'][0]['ok'])
            self.assertTrue(controls['positive'][0]['ok'])
            baseline = destination / 'cases/demo/negative-control'
            self.assertEqual('print("broken")\n', (baseline / 'answer.py').read_text())
            self.assertEqual(overlay.read_bytes(), (baseline / 'tests/preservation.test.cjs').read_bytes())
            self.assertEqual(overlay.read_bytes(), (destination / 'references/demo/tests/preservation.test.cjs').read_bytes())
            trace = json.loads((destination / 'cases/demo/preparation.json').read_text())
            self.assertEqual(base, trace['upstream_base_commit'])
            self.assertEqual(fixed, trace['upstream_reference_commit'])
            self.assertEqual(catalog['cases'][0]['base_commit'], trace['adapted_base_commit'])
            self.assertNotEqual(base, trace['adapted_base_commit'])
            self.assertEqual(base, subprocess.check_output(
                ['git', '-C', str(destination / 'sources/demo'), 'rev-parse', 'HEAD^'], text=True).strip())
            self.assertIn('identical native preservation tests', catalog['cases'][0]['task'])
            self.assertEqual('', subprocess.check_output(['git', '-C', str(baseline), 'remote'], text=True))
            with self.assertRaisesRegex(ValueError, 'fresh Arena'):
                prepare.prepare(destination, [case], catalog_root=root)
            # A broken reference cannot silently populate the workload catalog.
            with self.assertRaises(subprocess.CalledProcessError):
                prepare.prepare(root / 'invalid', [{**case, 'reference_commit': base}], catalog_root=root)
            self.assertEqual([], json.loads((root / 'invalid/catalog.json').read_text())['cases'])

    def test_overlay_refuses_runtime_paths_and_symlinks_before_changing_sources(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            overlay = root / 'overlay'
            source, reference = root / 'source', root / 'reference'
            source.mkdir()
            reference.mkdir()
            (overlay / 'src').mkdir(parents=True)
            runtime = overlay / 'src/product.js'
            runtime.write_text('exports.answer = 42;\n')
            with self.assertRaisesRegex(ValueError, 'only add test source'):
                prepare.add_test_overlay(overlay, source, reference, 'unused')
            runtime.unlink()
            (overlay / 'tests').mkdir()
            (overlay / 'tests/escape.test.cjs').symlink_to(root / 'outside')
            with self.assertRaisesRegex(ValueError, 'symlinks'):
                prepare.add_test_overlay(overlay, source, reference, 'unused')
            self.assertEqual([], list(source.iterdir()))
            self.assertEqual([], list(reference.iterdir()))

    def test_overlay_does_not_replace_existing_tests_in_either_control(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, reference, overlay = root / 'source', root / 'reference', root / 'overlay'
            for tree in (source, reference, overlay):
                (tree / 'tests').mkdir(parents=True)
            subprocess.run(['git', '-C', str(source), 'init', '-q'], check=True)
            (source / 'product.js').write_text('exports.answer=1;\n')
            subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
            subprocess.run(['git', '-C', str(source), '-c', 'user.name=T', '-c', 'user.email=t@example.test',
                            'commit', '-qm', 'baseline'], check=True)
            base = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
            existing = reference / 'tests/preservation.test.cjs'
            existing.write_text('existing reference test\n')
            (overlay / 'tests/preservation.test.cjs').write_text('replacement\n')
            (overlay / 'tests/new.test.cjs').write_text('new test\n')
            with self.assertRaisesRegex(ValueError, 'must not overwrite'):
                prepare.add_test_overlay(overlay, source, reference, base)
            self.assertEqual('existing reference test\n', existing.read_text())
            self.assertFalse((source / 'tests/new.test.cjs').exists())
            self.assertFalse((reference / 'tests/new.test.cjs').exists())

    def test_overlay_rejects_file_or_symlink_parents_before_copying(self):
        for parent_kind in ('file', 'symlink'):
            with self.subTest(parent_kind=parent_kind), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                source, reference, overlay = root / 'source', root / 'reference', root / 'overlay'
                for tree in (source, reference, overlay):
                    tree.mkdir()
                subprocess.run(['git', '-C', str(source), 'init', '-q'], check=True)
                (source / 'product.js').write_text('exports.answer=1;\n')
                subprocess.run(['git', '-C', str(source), 'add', '.'], check=True)
                subprocess.run(['git', '-C', str(source), '-c', 'user.name=T', '-c', 'user.email=t@example.test',
                                'commit', '-qm', 'baseline'], check=True)
                base = subprocess.check_output(['git', '-C', str(source), 'rev-parse', 'HEAD'], text=True).strip()
                (overlay / 'tests').mkdir()
                (overlay / 'tests/preservation.test.cjs').write_text('new preservation test\n')
                if parent_kind == 'file':
                    (reference / 'tests').write_text('not a directory\n')
                else:
                    (reference / 'src').mkdir()
                    (reference / 'tests').symlink_to(reference / 'src', target_is_directory=True)
                with self.assertRaisesRegex(ValueError, 'parents must be real directories'):
                    prepare.add_test_overlay(overlay, source, reference, base)
                self.assertFalse((source / 'tests').exists())
                self.assertFalse((reference / 'src/preservation.test.cjs').exists())
