"""Offline oracles for live-product evidence; never dispatch a model."""
import contextlib
import importlib
import io
import json
import os
import shlex
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

import autoreview_product_probe as probe

from . import test_autoreview_products as audit


class ProductProbeTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.command = [sys.executable, str(Path(probe.__file__).resolve()), '--project', str(self.root)]
        for name in ('go.mod', 'main.go', 'reference.cs'):
            (self.root / name).write_text(name)
        self.identity = probe.candidate_identity(self.root)

    def execution(self, tld='de', **overrides):
        return dict(command=['go', 'run', '.', tld], cwd=str(self.root), scope='probe',
                    returncode=0, stdout='30\n', stderr='', timed_out=False, error=None, **overrides)

    def event(self):
        return dict(command=shlex.join(self.command), exit_code=0, aggregated_output='\n'.join(
            json.dumps(dict(candidate=self.identity, execution=self.execution(tld)))
            for tld in ('de', 'com', 'org')))

    def test_canonical_receipts_and_shell_launcher_are_accepted(self):
        event = self.event()
        self.assertTrue(audit.go_evidence([event], self.command, self.root, self.identity))
        event['command'] = shlex.join(['/bin/zsh', '-lc', event['command']])
        self.assertTrue(audit.go_evidence([event], self.command, self.root, self.identity))

    def test_arbitrary_wrappers_and_summaries_are_not_execution(self):
        for command, output in (
            ('printf "30\\n" # go run . de', '30\n'),
            ('go run . de; printf "de status=0 output=30 expected=7"', 'de status=0 output=30 expected=7'),
            (shlex.join(self.command), 'de status=0 output=30 expected=7'),
            ('printf fake; ' + shlex.join(self.command), self.event()['aggregated_output']),
        ):
            with self.subTest(command=command):
                self.assertFalse(audit.go_evidence([dict(command=command, exit_code=0,
                    aggregated_output=output)], self.command, self.root, self.identity))

    def test_negative_execution_receipts(self):
        changes = [dict(returncode=1), dict(timed_out=True), dict(interrupted=True), dict(error='launch failed'),
                   dict(stderr='compile error'), dict(stdout='7\n'),
                   dict(stdout='de status=0 output=30 expected=7\n'),
                   dict(command=['go', 'run', '.', 'com']), dict(cwd='/other/candidate'),
                   dict(scope='host-only')]
        for change in changes:
            with self.subTest(change=change):
                event = self.event()
                rows = list(map(json.loads, event['aggregated_output'].splitlines()))
                rows[0]['execution'].update(change)
                event['aggregated_output'] = '\n'.join(map(json.dumps, rows))
                self.assertFalse(audit.go_evidence([event], self.command, self.root, self.identity))
        for edit in ('old_candidate', 'default_only', 'outer_failure', 'missing_exit', 'malformed'):
            with self.subTest(edit=edit):
                event = self.event()
                rows = list(map(json.loads, event['aggregated_output'].splitlines()))
                if edit == 'old_candidate': rows[0]['candidate'] = {'main.go': 'old'}
                if edit == 'default_only': rows = rows[1:]
                if edit == 'outer_failure': event['exit_code'] = 1
                if edit == 'missing_exit': del event['exit_code']
                event['aggregated_output'] = '\n'.join(map(json.dumps, rows)) if edit != 'malformed' else '{'
                self.assertFalse(audit.go_evidence([event], self.command, self.root, self.identity))

    def test_reviewer_judgment_is_separate(self):
        comparison = dict(input='de', expected='7', observed='30', reference='reference.cs',
                          candidate='main.go', relation='different')
        finding = dict(finding='reference-parity-mismatch', evidence=json.dumps(comparison))
        self.assertTrue(audit.go_finding(dict(verdict='FAIL', findings=[finding])))
        for validation in (
            dict(verdict='PASS', findings=[finding]),
            dict(verdict='FAIL', findings=[]),
            dict(verdict='FAIL', findings=[{'description': 'com returns 30'}]),
            dict(verdict='FAIL', findings=[{'description': 'browser blocked'}]),
            dict(verdict='FAIL', findings=[{'finding': 'de returns 7 instead of required 30'}]),
            dict(verdict='FAIL', findings=[{'finding': 'de correctly returns30;7 not required, failure unrelated'}]),
            dict(verdict='FAIL', findings=[dict(finding='unrelated failure', evidence=json.dumps(comparison))]),
            dict(verdict='FAIL', findings=[dict(finding='reference-parity-mismatch', description=json.dumps(comparison))]),
        ):
            self.assertFalse(audit.go_finding(validation))
        for change in (dict(expected='30', observed='7'), dict(expected='30', observed='30'),
                       dict(relation='equal'), dict(input='com'), dict(reference='unrelated.cs'),
                       dict(candidate='other.go'), dict(denied=True), dict(expected=7)):
            with self.subTest(change=change):
                wrong = dict(finding, evidence=json.dumps(dict(comparison, **change)))
                self.assertFalse(audit.go_finding(dict(verdict='FAIL', findings=[wrong])))
        for evidence in ('not JSON', json.dumps(comparison)[:-1] + ', "expected":"7"}',
                         json.dumps(comparison)[:-1] + ', "relation":"equal"}'):
            wrong = dict(finding, evidence=evidence)
            self.assertFalse(audit.go_finding(dict(verdict='FAIL', findings=[wrong])))
        # Neither the marker alone nor successful raw execution is the complete oracle.
        self.assertFalse(audit.go_evidence([], self.command, self.root, self.identity))
        self.assertFalse(audit.go_finding(dict(verdict='FAIL', findings=[])))

    def test_reviewer_comparison_accepts_only_output_line_endings(self):
        comparison = dict(input='de', expected='7', observed='30', reference='reference.cs',
                          candidate='main.go', relation='different')
        def accepted(change):
            finding = dict(finding='reference-parity-mismatch', evidence=json.dumps({**comparison, **change}))
            return audit.go_finding(dict(verdict='FAIL', findings=[finding]))
        for expected in ('7', '7\n', '7\r\n'):
            for observed in ('30', '30\n', '30\r\n'):
                with self.subTest(expected=expected, observed=observed):
                    self.assertTrue(accepted(dict(expected=expected, observed=observed)))
        for change in (dict(input='de\n'), dict(reference='reference.cs\n'), dict(candidate='main.go\n'),
                       dict(relation='different\n'), dict(expected='7\n\n'), dict(observed='30 '),
                       dict(expected='30\n', observed='7\n'), dict(expected=7), dict(denied=True)):
            with self.subTest(change=change):
                self.assertFalse(accepted(change))

    def test_probe_direct_argv_and_early_failure_preserve_prior_case(self):
        rows = [self.execution(tld) for tld in ('de', 'com', 'org')]
        with patch.object(probe, 'capture', side_effect=rows) as capture, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertTrue(probe.go_probe(self.root))
        self.assertEqual([['go', 'run', '.', tld] for tld in ('de', 'com', 'org')],
                         [call.args[0] for call in capture.call_args_list])
        self.assertEqual(3, len(out.getvalue().splitlines()))
        rows[1]['timed_out'] = True
        with patch.object(probe, 'capture', side_effect=rows) as capture, contextlib.redirect_stdout(io.StringIO()) as out:
            self.assertFalse(probe.go_probe(self.root))
        self.assertEqual(2, capture.call_count)
        receipts = list(map(json.loads, out.getvalue().splitlines()))
        self.assertEqual('de', receipts[0]['execution']['command'][-1])
        self.assertTrue(receipts[1]['execution']['timed_out'])

    def test_capture_success_nonzero_and_write_once(self):
        for code in (0, 3):
            path = self.root / f'cli-{code}.json'
            row = probe.capture([sys.executable, '-c', f'import sys; print("raw"); sys.exit({code})'],
                                cwd=self.root, timeout=5, receipt=path)
            self.assertEqual(code, row['returncode'])
            self.assertEqual('raw\n', row['stdout'])
            self.assertEqual(row, json.loads(path.read_text()))
            before = path.read_bytes()
            with self.assertRaises(FileExistsError):
                probe.capture(['never-dispatch'], cwd=self.root, timeout=5, receipt=path)
            self.assertEqual(before, path.read_bytes())

    def test_launch_failure_has_receipt(self):
        path = self.root / 'missing.json'
        row = probe.capture([str(self.root / 'missing-command')], cwd=self.root, timeout=1, receipt=path)
        self.assertFalse(probe.successful(row))
        self.assertTrue(json.loads(path.read_text())['error'])

    def test_deferred_cancellation_is_not_swallowed_by_launch_failure(self):
        path = self.root / 'cancelled-launch.json'
        def interrupted_launch(*args, **kwargs):
            signal.raise_signal(signal.SIGTERM)
            raise OSError('launch failed at cancellation')
        with patch.object(probe.subprocess, 'Popen', side_effect=interrupted_launch):
            with self.assertRaises(KeyboardInterrupt):
                probe.capture(['unused'], cwd=self.root, timeout=1, receipt=path)
        row = json.loads(path.read_text())
        self.assertTrue(row['interrupted'])
        self.assertFalse(probe.successful(row))

    def test_live_invoke_retains_earlier_success_and_failed_receipts(self):
        module = importlib.import_module('tests.test_autoreview_products')
        case = module.ReviewProducts('test_06_go_tld_override_parity_exception')
        case.root, case.env, case.counter = self.root, None, 0
        case.command = lambda unit, args: [sys.executable, '-c', unit]
        case.invoke('print("first")', [])
        first = (self.root / 'cli-1.json').read_bytes()
        with self.assertRaises(AssertionError):
            case.invoke('import sys; print("failed"); sys.exit(3)', [])
        self.assertEqual(3, json.loads((self.root / 'cli-2.json').read_text())['returncode'])
        with self.assertRaisesRegex(AssertionError, 'timeout; receipt'):
            case.invoke('import time; print("partial",flush=True); time.sleep(30)', [], timeout=.5)
        timeout = json.loads((self.root / 'cli-3.json').read_text())
        self.assertTrue(timeout['timed_out'])
        self.assertEqual('partial\n', timeout['stdout'])
        self.assertEqual(first, (self.root / 'cli-1.json').read_bytes())

    def test_live_case_directory_survives_cleanup(self):
        module = importlib.import_module('tests.test_autoreview_products')
        case = module.ReviewProducts('test_06_go_tld_override_parity_exception')
        before = dict(os.environ)
        with patch.dict(os.environ, BUILD_AUDIT_ARTIFACTS=str(self.root / 'retained')):
            case.setUp()
        self.assertEqual(before, dict(os.environ))
        self.assertTrue((case.project / '.git').is_dir())
        case.command = lambda unit, args: [sys.executable, '-c', 'print("completed case")']
        case.invoke('unused', [])
        case.doCleanups()
        self.assertTrue((case.root / 'cli-1.json').is_file())

    def test_timeout_preserves_output_and_stops_own_detached_child(self):
        path = self.root / 'timeout.json'
        script = ('import subprocess,sys,time; '
                  'p=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"],start_new_session=True); '
                  'print(p.pid,flush=True); print("partial",file=sys.stderr,flush=True); time.sleep(30)')
        row = probe.capture([sys.executable, '-c', script], cwd=self.root, timeout=1, receipt=path)
        self.assertTrue(row['timed_out'])
        self.assertEqual('partial\n', row['stderr'])
        pid = int(row['stdout'].strip())
        table = probe.processes.process_table({pid})
        self.assertTrue(pid not in table or table[pid]['state'] == 'Z', table)
        self.assertEqual(row, json.loads(path.read_text()))
        self.assertLess(row['elapsed_seconds'], 10)

    def test_sigterm_records_interruption_and_cleans_detached_descendants(self):
        for gap in (False, True):
            with self.subTest(launch_handoff=gap):
                root = self.root / str(gap)
                root.mkdir()
                child_script = '''import json,os,subprocess,sys,time
from pathlib import Path
p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'], start_new_session=True)
print('partial', flush=True)
Path('ready.json').write_text(json.dumps([os.getpid(), p.pid]))
time.sleep(30)
'''
                supervisor_script = f'''import sys,time
from pathlib import Path
sys.path.insert(0, {str(Path(probe.__file__).resolve().parent)!r})
import autoreview_product_probe as probe
if {gap!r}:
    original = probe.subprocess.Popen
    def handoff(*args, **kwargs):
        child = original(*args, **kwargs)
        deadline = time.monotonic() + 10
        while not Path('release').exists() and time.monotonic() < deadline:
            time.sleep(.01)
        return child
    probe.subprocess.Popen = handoff
probe.capture([sys.executable, '-c', {child_script!r}], cwd=Path.cwd(), timeout=20, receipt='receipt.json')
Path('continued').write_text('must not continue after cancellation')
'''
                supervisor = subprocess.Popen([sys.executable, '-c', supervisor_script], cwd=root,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True)
                tree = probe.processes.ProcessTree(supervisor.pid, lambda rows: None)
                try:
                    deadline = time.monotonic() + 5
                    while not (root / 'ready.json').exists() and time.monotonic() < deadline:
                        tree.sample()
                        time.sleep(.01)
                    self.assertTrue((root / 'ready.json').exists(), 'Child never became ready')
                    tree.sample()
                    pids = json.loads((root / 'ready.json').read_text())
                    supervisor.send_signal(signal.SIGTERM)
                    (root / 'release').write_text('release launch handoff')
                    _, stderr = supervisor.communicate(timeout=10)
                    self.assertNotEqual(0, supervisor.returncode, stderr)
                    row = json.loads((root / 'receipt.json').read_text())
                    self.assertTrue(row['interrupted'])
                    self.assertFalse(probe.successful(row))
                    self.assertIn('KeyboardInterrupt', row['error'])
                    self.assertEqual('partial\n', row['stdout'])
                    self.assertFalse((root / 'continued').exists())
                    table = probe.processes.process_table(set(pids))
                    self.assertTrue(all(pid not in table or table[pid]['state'] == 'Z' for pid in pids), table)
                finally:
                    # The regression must not leak even against the unfixed capture.
                    tree.stop(supervisor)
                    supervisor.communicate(timeout=2)

    def test_budgets_are_finite_and_positive(self):
        for budget in (0, -1, float('inf'), float('nan')):
            with self.subTest(budget=budget), patch.object(probe.subprocess, 'Popen') as launch:
                with self.assertRaises(ValueError):
                    probe.capture(['unused'], cwd=self.root, timeout=budget)
                with self.assertRaises(ValueError):
                    probe.go_probe(self.root, timeout=budget)
                with self.assertRaises(ValueError):
                    probe.preflight('browser', self.root, {}, timeout=budget)
                launch.assert_not_called()

    def test_preflight_requires_rendered_launch_and_never_installs(self):
        failed = dict(returncode=1, timed_out=False, error=None, stdout='', stderr='unavailable')
        with patch.object(probe, 'capture', return_value=failed) as capture:
            probe.preflight('browser', self.root, {}, timeout=2)
        command = capture.call_args_list[0].args[0]
        script = command[-1]
        for fragment in ('chromium.launch', 'chromium_sandbox=True', 'bounding_box', 'screenshot'):
            self.assertIn(fragment, script)
        for call in capture.call_args_list:
            self.assertNotIn('--no-sandbox', ' '.join(call.args[0]))
            # A checkout directory may itself contain 'install'. Inspect
            # executable arguments and inline code, not incidental paths.
            args = call.args[0]
            self.assertFalse(any(arg in ('install', 'add') for arg in args))
            self.assertNotIn(Path(args[0]).name, ('pip', 'pip3', 'npm', 'npx', 'yarn', 'pnpm'))
            inline = args[2] if args[0] == 'node' else args[-1]
            self.assertNotRegex(inline, r'\b(?:pip|npm|playwright|puppeteer)\s+install\b')
            self.assertEqual('host-only', call.kwargs['scope'])
            self.assertLessEqual(call.kwargs['timeout'], 2)
        node = [call.args[0] for call in capture.call_args_list if call.args[0][0] == 'node']
        self.assertEqual(['playwright', 'puppeteer'], [command[3] for command in node])
        for command in node:
            self.assertIn('getBoundingClientRect', command[2])
            self.assertIn('page.screenshot()', command[2])
        self.assertIn('chromiumSandbox:true', node[0][2])
        with patch.object(probe, 'capture', return_value={'returncode': 1}) as capture:
            probe.preflight('go', self.root, {}, timeout=2)
        self.assertEqual(['go', 'run'], capture.call_args.args[0][:2])
        self.assertEqual('local', capture.call_args.kwargs['env']['GOTOOLCHAIN'])
        self.assertEqual('off', capture.call_args.kwargs['env']['GOPROXY'])

    def test_browser_primary_fails_alternative_passes_and_receipts_survive(self):
        def capture(command, **kwargs):
            row = dict(returncode=0 if command[0] == 'node' else 1, timed_out=False, error=None,
                       stdout='rendered-browser-ready\n' if command[0] == 'node' else '', stderr='', scope=kwargs['scope'])
            with kwargs['receipt'].open('x') as handle:
                json.dump(row, handle)
            return row
        with patch.object(probe, 'capture', side_effect=capture) as dispatch:
            row = probe.preflight('browser', self.root, {}, timeout=3)
        self.assertTrue(probe.successful(row))
        self.assertEqual(2, dispatch.call_count)
        self.assertEqual([1, 0], [json.loads(Path(path).read_text())['returncode'] for path in row['attempt_receipts']])
        self.assertEqual('host-only', json.loads((self.root / 'preflight-browser.json').read_text())['scope'])

    def test_canonical_browser_requirement_does_not_accept_an_unrelated_backend(self):
        def capture(command, **kwargs):
            row = dict(returncode=1 if command[0] == sys.executable else 0, timed_out=False,
                       error=None, stdout='rendered-browser-ready\n', stderr='', scope='host-only')
            with kwargs['receipt'].open('x') as handle:
                json.dump(row, handle)
            return row
        case = audit.ReviewProducts('test_04_mobile_initial_visibility_requires_rendered_evidence')
        case.root, case.env = self.root, {}
        with patch.object(probe, 'capture', side_effect=capture) as dispatch, patch.object(case, 'prepare') as prepare:
            with self.assertRaisesRegex(unittest.SkipTest, 'NOT_VERIFIED'):
                case.test_04_mobile_initial_visibility_requires_rendered_evidence()
        self.assertEqual(1, dispatch.call_count)
        self.assertEqual(sys.executable, dispatch.call_args.args[0][0])
        prepare.assert_not_called()

    def test_puppeteer_and_direct_chromium_alternatives_require_rendered_evidence(self):
        for backend in ('puppeteer', 'chromium'):
            for rendered in (False, True):
                with self.subTest(backend=backend, rendered=rendered):
                    root = self.root / f'{backend}-{rendered}'
                    root.mkdir()
                    def capture(command, **kwargs):
                        selected = (command[0] == 'node' and command[3] == 'puppeteer'
                                    if backend == 'puppeteer' else '--dump-dom' in command)
                        row = dict(returncode=0 if selected else 1, timed_out=False, error=None,
                                   stdout='', stderr='', scope='host-only')
                        if selected and rendered:
                            if backend == 'puppeteer':
                                row['stdout'] = 'rendered-browser-ready\n'
                            else:
                                screenshot = Path(next(arg.split('=', 1)[1] for arg in command if arg.startswith('--screenshot=')))
                                screenshot.write_bytes(b'\x89PNG\r\n\x1a\n' + b'\0' * 8
                                                       + (375).to_bytes(4, 'big') + (812).to_bytes(4, 'big'))
                                row['stdout'] = '<body data-rendered="browser-ready">'
                        with kwargs['receipt'].open('x') as handle:
                            json.dump(row, handle)
                        return row
                    with patch.object(probe, 'capture', side_effect=capture):
                        row = probe.preflight('browser', root, {}, timeout=3)
                    self.assertEqual(rendered, probe.successful(row))
                    self.assertTrue(all(Path(path).is_file() for path in row['attempt_receipts']))

    def test_browser_all_fail_skips_before_dispatch_with_all_receipts(self):
        def capture(command, **kwargs):
            row = dict(returncode=1, timed_out=False, error=None, stdout='', stderr='launch failed', scope='host-only')
            with kwargs['receipt'].open('x') as handle:
                json.dump(row, handle)
            return row
        case = audit.ReviewProducts('test_04_mobile_initial_visibility_requires_rendered_evidence')
        case.root, case.env = self.root, {}
        with patch.object(probe, 'capture', side_effect=capture) as dispatch, patch.object(case, 'prepare') as prepare:
            with self.assertRaisesRegex(unittest.SkipTest, 'NOT_VERIFIED.*host-only'):
                case.require_capability('browser')
                case.prepare({})
        prepare.assert_not_called()
        row = json.loads((self.root / 'preflight-browser.json').read_text())
        self.assertFalse(probe.successful(row))
        self.assertGreaterEqual(len(row['attempt_receipts']), 4)
        self.assertEqual(dispatch.call_count, len(row['attempt_receipts']))
        self.assertTrue(all(Path(path).is_file() for path in row['attempt_receipts']))

    def test_browser_total_budget_accounts_for_elapsed_attempts(self):
        clock = [100.0]
        budgets = []
        def capture(command, **kwargs):
            budgets.append(kwargs['timeout'])
            clock[0] += 1.0
            row = dict(returncode=-15, timed_out=True, error=None, stdout='', stderr='', scope='host-only')
            with kwargs['receipt'].open('x') as handle:
                json.dump(row, handle)
            return row
        with patch.object(probe.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(probe, 'capture', side_effect=capture) as dispatch:
            row = probe.preflight('browser', self.root, {}, timeout=1.5)
        self.assertEqual(2, dispatch.call_count)
        self.assertTrue(row['timed_out'])
        self.assertIn('budget exhausted', row['error'])
        self.assertLessEqual(budgets[0], 1.5)
        self.assertLessEqual(budgets[1], .5)
        self.assertTrue(all(Path(path).is_file() for path in row['attempt_receipts']))

    def test_browser_unused_time_is_available_to_later_backends(self):
        clock = [100.0]
        budgets = []
        def capture(command, **kwargs):
            budgets.append(kwargs['timeout'])
            clock[0] += .1
            row = dict(returncode=1, timed_out=False, error=None, stdout='', stderr='unavailable', scope='host-only')
            with kwargs['receipt'].open('x') as handle:
                json.dump(row, handle)
            return row
        with patch.object(probe.time, 'monotonic', side_effect=lambda: clock[0]), \
                patch.object(probe.shutil, 'which', return_value=None), \
                patch.object(probe.Path, 'is_file', return_value=False), \
                patch.object(probe, 'capture', side_effect=capture):
            row = probe.preflight('browser', self.root, {}, timeout=30)
        self.assertEqual(4, len(budgets))
        self.assertAlmostEqual(7.5, budgets[0])
        self.assertAlmostEqual(29.7, budgets[-1])
        self.assertTrue(all(0 < budget <= 30 for budget in budgets))
        self.assertFalse(probe.successful(row))

    def test_canonical_browser_probe_records_exact_candidate_and_driver(self):
        (self.root / 'index.html').write_text('<p id="sample">candidate text</p>')
        identity = probe.candidate_identity(self.root, files=('index.html',))
        row = dict(returncode=0, timed_out=False, error=None, stdout='raw observations', stderr='')
        output = io.StringIO()
        with patch.object(probe, 'capture', return_value=row) as capture, contextlib.redirect_stdout(output):
            self.assertTrue(probe.browser_probe(self.root, timeout=4))
        self.assertEqual([sys.executable, '-c', probe.BROWSER_PROBE, (self.root / 'index.html').as_uri()],
                         capture.call_args.args[0])
        self.assertEqual(self.root, capture.call_args.kwargs['cwd'])
        self.assertEqual('probe', capture.call_args.kwargs['scope'])
        self.assertEqual(4, capture.call_args.kwargs['timeout'])
        self.assertEqual(dict(candidate=identity, execution=row), json.loads(output.getvalue()))
        for fragment in ('chromium_sandbox=True', 'page.goto(sys.argv[1]', 'getBoundingClientRect',
                         "querySelectorAll('[id]')", 'page.screenshot()', 'browser.close()'):
            self.assertIn(fragment, probe.BROWSER_PROBE)
        self.assertNotIn('--no-sandbox', probe.BROWSER_PROBE)

    def test_canonical_browser_probe_preserves_failure_and_rejects_mutation(self):
        source = self.root / 'index.html'
        source.write_text('<p>original</p>')
        failed = dict(returncode=1, timed_out=False, error=None, stdout='', stderr='browser unavailable')
        output = io.StringIO()
        with patch.object(probe, 'capture', return_value=failed), contextlib.redirect_stdout(output):
            self.assertFalse(probe.browser_probe(self.root))
        self.assertEqual(failed, json.loads(output.getvalue())['execution'])
        def mutate(*args, **kwargs):
            source.write_text('<p>changed</p>')
            return dict(returncode=0, timed_out=False, error=None)
        with patch.object(probe, 'capture', side_effect=mutate), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'Candidate changed'):
                probe.browser_probe(self.root)
        for limit in (0, -1, float('inf'), float('nan')):
            with self.subTest(limit=limit), patch.object(probe, 'capture') as capture:
                with self.assertRaises(ValueError):
                    probe.browser_probe(self.root, timeout=limit)
                capture.assert_not_called()

    def test_live_suite_skips_before_dispatch_and_unavailable_preflight_skips(self):
        self.assertTrue(audit.ReviewProducts.__unittest_skip__)
        suite = unittest.defaultTestLoader.loadTestsFromModule(audit)
        result = unittest.TestResult()
        with patch.object(audit.ReviewProducts, 'setUp') as setup, \
                patch.object(audit.ReviewProducts, 'prepare') as prepare:
            suite.run(result)
        self.assertEqual(11, result.testsRun)
        self.assertEqual(11, len(result.skipped))
        setup.assert_not_called()
        prepare.assert_not_called()
        for method, kind in (
                ('test_04_mobile_initial_visibility_requires_rendered_evidence', 'browser'),
                ('test_06_go_tld_override_parity_exception', 'go')):
            with self.subTest(kind=kind):
                case = audit.ReviewProducts(method)
                case.root, case.env = self.root, {}
                row = dict(returncode=1, timed_out=False, error=None, stderr='launch denied')
                with patch.object(probe, 'preflight', return_value=row), patch.object(case, 'prepare') as prepare:
                    with self.assertRaisesRegex(unittest.SkipTest, 'NOT_VERIFIED.*host-only'):
                        getattr(case, method)()
                    prepare.assert_not_called()

    def test_canonical_test_owner_has_no_duplicate_discovery(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(root / 'tests' / 'test_autoreview_products.py', Path(audit.__file__).resolve())
        for name in ('test_autoreview_products.py', 'test_autoreview_product_probe.py',
                     'test_autoreview_browser_evidence.py'):
            self.assertFalse((root / 'tools' / name).exists())
        ids = []
        for module in (audit, sys.modules[__name__],
                       importlib.import_module('tests.test_autoreview_browser_evidence')):
            for cases in unittest.defaultTestLoader.loadTestsFromModule(module):
                ids.extend(case.id() for case in cases)
        self.assertEqual(len(ids), len(set(ids)), 'TestCase reexports duplicate discovery')
        self.assertEqual(11, sum(name.startswith('tests.test_autoreview_products.') for name in ids))


if __name__ == '__main__':
    unittest.main()
