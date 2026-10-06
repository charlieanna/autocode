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
        self.assertEqual({'Boltons', 'Humanize', 'More Itertools', 'SymPy', 'Django', 'pytest'},
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
                        issue_ref='example/demo#1', split='development', checks=['output'])
            destination = root / 'arena'
            prepare.prepare(destination, [case], catalog_root=root, oracle_timeout=5)
            catalog = json.loads((destination / 'catalog.json').read_text())
            self.assertEqual(['demo'], [row['id'] for row in catalog['cases']])
            controls = json.loads((destination / 'cases/demo/controls.json').read_text())
            self.assertFalse(controls['negative'][0]['ok'])
            self.assertTrue(controls['positive'][0]['ok'])
            baseline = destination / 'cases/demo/negative-control'
            self.assertEqual('print("broken")\n', (baseline / 'answer.py').read_text())
            self.assertEqual('', subprocess.check_output(['git', '-C', str(baseline), 'remote'], text=True))
            with self.assertRaisesRegex(ValueError, 'fresh Arena'):
                prepare.prepare(destination, [case], catalog_root=root)
            # A broken reference cannot silently populate the workload catalog.
            with self.assertRaises(subprocess.CalledProcessError):
                prepare.prepare(root / 'invalid', [{**case, 'reference_commit': base}], catalog_root=root)
            self.assertEqual([], json.loads((root / 'invalid/catalog.json').read_text())['cases'])
