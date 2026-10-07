"""Real Node events and hostile evidence controls for named case proof."""
import json
import shlex
import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_node_tests as node_tests
import autocode_verify as verify
from tests.test_verify import Project


class CommandTests(unittest.TestCase):
    def test_only_direct_test_commands_are_instrumented(self):
        for command in ('node --test tests/a.cjs', '/usr/bin/node --test "tests/a b.cjs"'):
            self.assertTrue(node_tests.command_words(command))
            self.assertTrue(verify.expects_results(None, command))
        for command in ('node tests/a.cjs --test', 'npm test', 'node --test | cat',
                        'node --test; true', 'node --test\ntrue', 'node --test --watch',
                        'node --test --test-reporter=spec', 'node -- tests/a.cjs --test',
                        'node --test $(echo tests/a.cjs)', 'node --test "unterminated'):
            with self.subTest(command=command):
                self.assertIsNone(node_tests.command_words(command))
                self.assertEqual(command, node_tests.instrument(command, '/tmp/proof.jsonl'))


@unittest.skipUnless(shutil.which('node'), 'Node is required for the actual test-runner protocol')
class NodeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.evidence = self.root / 'evidence'
        self.framework = verify.Framework('node', 'node --test')

    def run_node(self, files, *, label='probe'):
        for name, source in files.items():
            (self.root / name).parent.mkdir(parents=True, exist_ok=True)
            (self.root / name).write_text(source)
        command = 'node --test ' + shlex.join(list(files))
        return verify.run_suite(self.framework, command, self.root, self.evidence, label, timeout=20)

    def test_named_outcomes_nested_suites_and_same_name_in_other_files(self):
        receipt = self.run_node({
            'a.test.cjs': "const {test,describe}=require('node:test');"
                          "const assert=require('node:assert/strict');"
                          "test('test_ok',()=>{});test('test_bad',()=>assert.fail('wrong'));"
                          "test('test_skip',{skip:true},()=>{});"
                          "describe('group',()=>{test('test_nested',()=>{});"
                          "test('test_todo',{todo:true},()=>assert.fail('todo'));});",
            'b.test.cjs': "require('node:test')('test_ok',()=>{});",
        })
        self.assertEqual(1, receipt['exit_code'], receipt)
        self.assertEqual({'passed': ['a.test.cjs::group::test_nested', 'a.test.cjs::test_ok', 'b.test.cjs::test_ok'],
                          'failed': ['a.test.cjs::test_bad'],
                          'skipped': ['a.test.cjs::group::test_todo', 'a.test.cjs::test_skip'],
                          'collection_errors': [], 'uncollected': [], 'total': 6, 'complete': True},
                         receipt['results'])

    def test_forged_stdout_empty_file_and_early_exit_do_not_prove_a_named_case(self):
        for source in ("console.log('PASS test_fake');",
                       "console.log(JSON.stringify({type:'pass',name:'test_fake'}));",
                       "require('node:test')('test_fake',()=>{process.exit(0)});"):
            with self.subTest(source=source):
                receipt = self.run_node({'test_fake.cjs': source})
                self.assertTrue(receipt['results_expected'])
                self.assertEqual([], (receipt['results'] or {}).get('passed', []), receipt)

    def test_collection_and_hook_errors_do_not_reproduce_a_bug(self):
        cases = [("require('./missing.cjs');", ['a.cjs::[collection]']),
                 ("const {test,before}=require('node:test');before(()=>{throw Error('setup')});"
                  "test('test_case',()=>{});", [])]
        for source, uncollected in cases:
            with self.subTest(source=source):
                receipt = self.run_node({'a.cjs': source})
                self.assertEqual(1, receipt['exit_code'])
                self.assertTrue(receipt['results']['collection_errors'], receipt)
                self.assertEqual(receipt['results']['failed'], receipt['results']['collection_errors'])
                # Only a file that never imported is uncollected; a failed hook executed (#503).
                self.assertEqual(uncollected, receipt['results']['uncollected'], receipt)

    def test_duplicate_identities_are_ambiguous_even_when_node_exits_zero(self):
        receipt = self.run_node({'a.cjs': "const {test}=require('node:test');"
                                        "for(let i=0;i<2;i++)test('test_same',()=>{});"})
        self.assertEqual(0, receipt['exit_code'])
        self.assertIsNone(receipt['results'])

    def test_aborted_test_is_not_a_passing_case(self):
        receipt = self.run_node({'a.cjs': "const ac=new AbortController(); ac.abort();"
                                        "require('node:test')('test_cancel',{signal:ac.signal},()=>{});"})
        self.assertEqual(1, receipt['exit_code'], receipt)
        result = receipt['results']
        self.assertIsNotNone(result, receipt)
        self.assertNotIn('a.cjs::test_cancel', result['passed'])
        self.assertIn('a.cjs::test_cancel', result['collection_errors'])
        self.assertEqual([], result['uncollected'])

    def test_timeout_and_running_abort_keep_complete_nonpassing_results(self):
        # Keep Node's event loop alive until its test timeout, including on Node 22.
        sources = {
            'testTimeoutFailure': "test('test_cancel', {timeout:100}, async t=>{"
                                 "const hold=setTimeout(()=>{},1000);t.after(()=>clearTimeout(hold));"
                                 "await new Promise(()=>{});});",
            'testAborted': "const ac=new AbortController();"
                           "test('test_cancel',{signal:ac.signal,timeout:1000},async()=>{"
                           "setImmediate(()=>ac.abort(new Error('fixture abort')));await new Promise(()=>{});});",
        }
        for failure_type, source in sources.items():
            with self.subTest(failure_type=failure_type):
                receipt = self.run_node({'a.cjs': "const {test}=require('node:test');"
                                                 "test('test_ok',()=>{});" + source})
                self.assertEqual(1, receipt['exit_code'], receipt)
                self.assertFalse(receipt['timed_out'], receipt)
                events = [json.loads(line) for line in (self.evidence / 'probe.node.jsonl').read_text().splitlines()]
                failure = next(row for row in events if row.get('name') == 'test_cancel' and row['type'] == 'fail')
                self.assertEqual(failure_type, failure['failure_type'])
                self.assertEqual((1, 0, 1), tuple(events[-2]['counts'][name] for name in ('passed', 'failed', 'cancelled')))
                self.assertEqual({'passed': ['a.cjs::test_ok'], 'failed': ['a.cjs::test_cancel'], 'skipped': [],
                                  'collection_errors': ['a.cjs::test_cancel'], 'uncollected': [],
                                  'total': 2, 'complete': True}, receipt['results'])

    def regression_proof(self, *, ordinary):
        preamble = "const {test}=require('node:test');const assert=require('node:assert/strict');const p=require('./product.cjs');\n"
        healthy = "test('test_existing',()=>assert.equal(p.existing(),1));\n"
        cancelled = "test('test_increment',{timeout:100},async t=>{const hold=setTimeout(()=>{},1000);"
        cancelled += "t.after(()=>clearTimeout(hold));assert.equal(await p.increment(4),5);});\n"
        restored = "test('test_restore',async()=>{for(const x of [-7,-1,0,4,16]){const value=p.increment(x);"
        restored += "assert.ok(value instanceof Promise);const pending=Symbol('pending');"
        restored += "assert.equal(await Promise.race([value,Promise.resolve(pending)]),x+1);}});\n"
        seed = preamble + healthy + (cancelled if ordinary else '')
        project = Project({'product.cjs': "exports.existing=()=>1;exports.increment=x=>new Promise(()=>{});\n",
                           'suite.test.cjs': seed})
        self.addCleanup(project.close)
        command = 'node --test suite.test.cjs'
        framework = verify.Framework('node', command, node_files=['suite.test.cjs'])
        baseline = verify.baseline(project.root, project.base, project.evidence / 'baseline-run',
                                   framework=framework, suite_command=command, timeout=20)
        project.write({'product.cjs': "exports.existing=()=>1;exports.increment=x=>Promise.resolve(x+1);\n",
                       'suite.test.cjs': seed + (restored if ordinary else cancelled)})
        proof = verify.verify(project.root, project.base, project.evidence / 'proof', framework=framework,
                              suite_command=command, base_suite=baseline, timeout=20, new_behavior=False)
        self.assertEqual('derived:node', proof['commands']['regression_source'], proof)
        self.assertTrue((project.root / 'suite.test.cjs').read_text().startswith(seed))
        for receipt in proof['checks'].values():
            self.assertFalse(receipt['timed_out'], receipt)
            self.assertTrue(verify.command_receipt.completed(receipt), receipt)
        return proof

    def test_cancellation_alone_cannot_reproduce_a_bugfix(self):
        proof = self.regression_proof(ordinary=False)
        self.assertEqual(verify.FAIL, proof['verdict'], proof)
        self.assertEqual([], proof['fail_to_pass'], proof)
        self.assertEqual(['suite.test.cjs::test_existing'], proof['pass_to_pass'], proof)
        base = proof['checks']['regression_on_base']['results']
        self.assertTrue(base['complete'])
        self.assertEqual(['suite.test.cjs::test_increment'], base['failed'])
        self.assertEqual(base['failed'], base['collection_errors'])

    def test_ordinary_regression_restores_bugfix_beside_cancelled_seed(self):
        proof = self.regression_proof(ordinary=True)
        self.assertEqual(verify.PASS, proof['verdict'], proof)
        self.assertEqual(['suite.test.cjs::test_restore'], proof['fail_to_pass'], proof)
        self.assertEqual(['suite.test.cjs::test_existing'], proof['pass_to_pass'], proof)
        base = proof['checks']['regression_on_base']['results']
        self.assertEqual(['suite.test.cjs::test_increment'], base['collection_errors'])
        self.assertEqual(['suite.test.cjs::test_increment', 'suite.test.cjs::test_restore'], base['failed'])
        candidate = proof['checks']['suite_on_candidate']
        self.assertEqual(0, candidate['exit_code'])
        self.assertTrue(candidate['results']['complete'])
        self.assertEqual(3, len(candidate['results']['passed']))

    def test_incomplete_malformed_or_inconsistent_evidence_is_never_credited(self):
        receipt = self.run_node({'a.cjs': "require('node:test')('test_case',()=>{});"})
        self.assertEqual(['a.cjs::test_case'], receipt['results']['passed'])
        path = self.evidence / 'probe.node.jsonl'
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        broken_counts = json.loads(json.dumps(rows))
        broken_counts[-2]['counts']['passed'] += 1
        variants = [rows[:-1], rows[:1] + rows[2:], rows[:-2] + rows[-1:],
                    rows + [{'type': 'pass'}], rows[:2] + [rows[2]] + rows[2:],
                    broken_counts, rows[:-2] + [{'type': 'summary', 'counts': []}, rows[-1]],
                    [None], [{'protocol': 'autocode-node-tests', 'version': 2}, *rows[1:]]]
        for variant in variants:
            with self.subTest(variant=variant):
                path.write_text('\n'.join(map(json.dumps, variant)) + '\n')
                self.assertIsNone(node_tests.results(path))
        path.write_text('{not JSON}\n')
        self.assertIsNone(node_tests.results(path))
        path.unlink()
        self.assertIsNone(node_tests.results(path))

    def test_generic_suite_keeps_its_exit_code_and_does_not_reuse_stale_results(self):
        self.run_node({'a.cjs': "require('node:test')('test_case',()=>{});"})
        receipt = verify.run_suite(self.framework, 'node -e "process.exit(0)"', self.root,
                                   self.evidence, 'probe', timeout=20)
        self.assertEqual(0, receipt['exit_code'])
        self.assertFalse(receipt['results_expected'])
        self.assertIsNone(receipt['results'])
        # A subsequent failed Node launch must delete the previous named report.
        receipt = verify.run_suite(self.framework, '/missing/node --test a.cjs', self.root,
                                   self.evidence, 'probe', timeout=20)
        self.assertTrue(receipt['results_expected'])
        self.assertIsNone(receipt['results'])
