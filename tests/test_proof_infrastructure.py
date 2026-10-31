"""A newer authenticated proof can settle an earlier disk-only collection failure."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_completion_result as completion
import autocode_git_delivery as delivery
import autocode_proof_findings as findings
import autocode_regression as regression
import autocode_source_scope as source_scope
import autocode_util as util
import autocode_verification_schedule as schedule
import autocode_workflow as workflow


class InfrastructureProofTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(); self.addCleanup(tmp.cleanup)
        self.run = Path(tmp.name).resolve(); self.folder = self.run / 'regression' / 'old'
        self.folder.mkdir(parents=True); self.path = self.folder / 'verification.json'
        checks = {}
        for name in ('regression_on_base', 'regression_on_candidate', 'suite_on_base', 'suite_on_candidate'):
            log = self.folder / (name + '.log')
            log.write_text('compile: writing output: no space left on device\n')
            checks[name] = {'exit_code': 1, 'output': str(log), 'output_sha256': util.file_hash(log),
                            'results': {'failed': ['pkg::[build failed]'], 'collection_errors': ['pkg::[build failed]'],
                                        'passed': [], 'uncollected': ['pkg::[build failed]']}}
        self.receipt = {'verdict': 'FAIL', 'source_revision': 'rev', 'checks': checks}
        self.write_receipt()
        self.state = {'version': 3, 'workspace': '/work', 'run_dir': str(self.run),
                      'status': 'RUNNING', 'next_stage': 'astra_review',
                      'current_task': {'id': 'task', 'milestone_id': 'M1'},
                      'stages': [{'stage': 'regression_proof', 'runner_owned': True, 'output': str(self.path),
                                  'finished_at': '2026-01-01T00:00:00+00:00'}],
                      'regression_proof': {'verdict': 'FAIL', 'source_revision': 'rev', 'path': str(self.path)}}
        self.row = findings.reconcile(self.state, 'rev', required=True, proved=False, reason='Build failed')
        self.row['scope'] = {'milestone_id': 'M1'}
        self.state['regression_proof'] = {'verdict': 'PASS', 'source_revision': 'rev',
                                         'proved_at': '2026-01-01T00:01:00+00:00',
                                         'path': str(self.run / 'regression' / 'new' / 'verification.json')}

    def write_receipt(self):
        self.path.write_text(json.dumps(self.receipt))

    def review(self, proved=True, intact=True):
        with (patch.object(regression, 'before_review'), patch.object(regression, 'required', return_value=True),
              patch.object(regression, 'complete', return_value=proved),
              patch.object(regression, 'rejection', return_value='Still unproven'),
              patch.object(source_scope, 'snapshot', return_value={'revision': 'rev'}),
              patch.object(delivery, 'requirement', return_value=None),
              patch.object(workflow, 'enabled', return_value=False),
              patch.object(schedule, 'proof_intact', return_value=intact)):
            completion.before_review(self.state, 'astra_review', '/work', self.run)

    def test_current_authenticated_pass_settles_only_the_runner_disk_collection_finding(self):
        self.state['findings_ledger'].append({'id': 'human-review', 'source': 'sol', 'status': 'open', 'blocking': True})
        before = copy.deepcopy(self.state)
        self.review()
        self.assertEqual('resolved', self.row['status'])
        self.assertEqual(self.state['regression_proof']['path'], self.row['resolved_in'])
        self.assertEqual('open', self.state['findings_ledger'][1]['status'])
        for k in ('current_task', 'regression_proof', 'stages'):
            self.assertEqual(before[k], self.state[k])

    def test_unproven_or_unauthenticated_results_cannot_retire_the_failure(self):
        for kwargs in ({'proved': False}, {'intact': False}):
            with self.subTest(kwargs=kwargs):
                self.setUp(); self.review(**kwargs)
                self.assertEqual('open', self.row['status'])

    def test_behavior_failures_mixed_errors_tampering_newer_or_unowned_receipts_remain(self):
        edits = {
            'unknown_scope': lambda: (self.state['current_task'].pop('milestone_id'), self.row.pop('scope')),
            'assertion': lambda: self.receipt['checks']['suite_on_candidate']['results']['failed'].append('pkg::TestBroken'),
            'hash_tampering': lambda: (self.folder / 'regression_on_base.log').write_text('changed bytes no space left on device\n'),
            'other_error': lambda: (self.folder / 'regression_on_base.log').write_text('unknown symbol\n'),
            'source': lambda: self.receipt.update(source_revision='other'),
            'unowned': lambda: self.state['stages'][0].update(runner_owned=False),
            'newer_failure': lambda: self.state['stages'][0].update(finished_at='2026-01-01T00:02:00+00:00'),
            'outside': lambda: self.row.update(last_reported_in='/outside/verification.json'),
            'malformed': lambda: self.receipt.update(checks=[]),
        }
        for name, edit in edits.items():
            with self.subTest(name=name):
                self.setUp(); edit(); self.write_receipt(); self.review()
                self.assertEqual('open', self.row['status'])
