# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from . import autocode_baseline as b


def log(blocks=None, *, files=2, passed=2, skipped=0):
    blocks = blocks if blocks is not None else [('a.test.ts', 'works', 'AssertionError: expected 2 to equal 3')]
    suites = sum(title == '[suite-load]' for _, title, _ in blocks)
    failed = len(blocks) - suites
    rows = [' RUN  v2.1.9 /checkout']
    if suites:
        rows.append(f'⎯⎯⎯⎯⎯ Failed Suites {suites} ⎯⎯⎯⎯⎯')
    if failed:
        rows.append(f'⎯⎯⎯⎯⎯ Failed Tests {failed} ⎯⎯⎯⎯⎯')
    for file, title, cause in blocks:
        header = f'{file} [ {file} ]' if title == '[suite-load]' else f'{file} > {title}'
        rows.extend([' FAIL  ' + header, cause, '⎯⎯⎯⎯⎯ [1/1] ⎯⎯⎯⎯⎯'])
    failed_files = len({f for f, _, _ in blocks})
    rows.extend([f' Test Files  {failed_files} failed | {files-failed_files} passed ({files})',
                 f' Tests  {failed} failed | {passed} passed' + (f' | {skipped} skipped' if skipped else '') + f' ({failed+passed+skipped})',
                 ' Start at  01:02:03', ' Duration  1.23s'])
    return '\n'.join(rows) + '\n'


class BaselineTests(unittest.TestCase):
    def test_unique_failed_file_union_and_signatures(self):
        text = log([('a.ts', '[suite-load]', 'Error: load'), ('a.ts', 'test', 'Error: test')])
        report = b.parse(text)
        self.assertTrue(report['valid'], report['errors'])
        self.assertEqual('matched', b.compare(report, report)['status'])

    def test_numbers_operands_locations_are_preserved(self):
        original = b.parse(log())
        for changed in ('expected 20 to equal 3', 'expected 2 to equal 4'):
            candidate = b.parse(log().replace('expected 2 to equal 3', changed))
            self.assertEqual('blocked', b.compare(original, candidate)['status'])
        self.assertNotEqual(b.diagnostic('Error: x\n at /one/a.ts:1'), b.diagnostic('Error: x\n at /one/a.ts:2'))

    def test_only_transport_and_explicit_roots_normalize(self):
        self.assertEqual(b.diagnostic('\x1b[31mError: x\x1b[0m \r\n at /one/a.ts:12\t', '/one'),
                         b.diagnostic('Error: x\n at /two/a.ts:12', '/two'))
        self.assertNotEqual(b.diagnostic('Error: /other/one/a.ts', '/one'), 'Error: /other<workspace>/a.ts')

    def test_dependency_prefix_normalization_is_opt_in_and_preserves_details(self):
        first = 'Error: missing import\n ❯ transform ../../frontend/node_modules/vite@6/chunk.js:12:4'
        second = 'Error: missing import\n ❯ transform node_modules/vite@6/chunk.js:12:4'
        self.assertNotEqual(b.diagnostic(first), b.diagnostic(second))
        self.assertEqual(b.diagnostic(first, dependency_prefixes=True), b.diagnostic(second))
        for changed in (second.replace('vite@6', 'vite@7'), second.replace(':12:4', ':13:4')):
            self.assertNotEqual(b.diagnostic(first, dependency_prefixes=True), b.diagnostic(changed))
        self.assertEqual('Error: ../frontend/node_modules/x', b.diagnostic('Error: ../frontend/node_modules/x', dependency_prefixes=True))

    def test_new_and_absent_failures_are_not_silently_waived(self):
        base = b.parse(log())
        newer = b.parse(log([('new.ts', 'test', 'Error: new')]))
        self.assertEqual('blocked', b.compare(base, newer)['status'])
        recovered = b.compare(base, b.parse(log([], passed=3)))
        self.assertEqual('matched', recovered['status'])
        self.assertEqual(['a.test.ts > works'], recovered['baseline_only'])
        self.assertIn('not proof of recovery', recovered['policy'])

    def test_incomplete_unknown_duplicate_and_unhandled_output_rejected(self):
        valid = log()
        variants = ['', valid[:valid.index(' Duration')], valid + valid,
                    valid.replace('1 failed | 1 passed (2)', '2 failed | 0 passed (2)'),
                    valid.replace('Failed Tests 1', 'Failed Tests 2'),
                    valid.replace('AssertionError: expected 2 to equal 3', 'unrecognized diagnostic'),
                    valid.replace(' FAIL  ', ' FAIL '), valid + '\nUnhandled Error: crash',
                    log([('a.ts', 'x', 'Error: 1'), ('a.ts', 'x', 'Error: 1')])]
        for text in variants:
            with self.subTest(text=text):
                report = b.parse(text)
                self.assertFalse(report['valid'])
                self.assertEqual('invalid', b.compare(report, report)['status'])

    def test_reduced_or_skipped_inventory_fails_closed(self):
        base = b.parse(log())
        for candidate in (log(files=1), log(passed=1), log(passed=1, skipped=1)):
            self.assertEqual('invalid', b.compare(base, b.parse(candidate))['status'])

    def test_cli_exit_codes_and_saved_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            baseline, candidate, output = (root / name for name in ('baseline.log', 'candidate.log', 'report.json'))
            baseline.write_text(log())
            for text, code in ((log(), 0), (log().replace('equal 3', 'equal 4'), 1), ('truncated', 2)):
                candidate.write_text(text)
                result = subprocess.run([sys.executable, '-m', 'tools.autocode', 'compare-baseline',
                                         str(baseline), str(candidate), '--output', str(output)], capture_output=True, text=True)
                self.assertEqual(code, result.returncode, result.stderr)
                self.assertIn('raw_sha256', json.loads(output.read_text())['baseline'])
