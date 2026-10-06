"""Mandatory original-brief evidence uses actual pinned output, not model claims."""
from __future__ import annotations

import base64
import copy
import json
from pathlib import Path
import shlex
import subprocess
import tempfile
import unittest

import autocode_brief_evidence as evidence
import autocode_check_replay as check_replay
import autocode_brief_obligations as obligations
import autocode_util as util
import autocode_verify as verify
from tests.test_brief_acceptance import PRODUCT, TASK
from tests.test_command_receipt import guarded_command_receipt


def build_state(root, task=TASK):
    state = {'task': task}
    declarations = obligations.inventory(state)
    proposals = [{'declaration_id': row['id'], 'criterion_ids': [f'AC{i}'],
                  'steps': [{'argv': ['add', 'brief-probe']}, {'argv': ['list']}],
                  'observe_step': 1, 'bindings': [{'placeholder': 'TEXT', 'step': 0, 'argument': 1}]}
                 for i, row in enumerate(declarations, 1)]
    body = {'acceptance_criteria': [{'id': f'AC{i}'} for i in range(1, len(proposals) + 1)]}
    events, output = root / 'review.events', root / 'review.json'
    events.write_text('independent reviewer execution\n')
    output.write_text(json.dumps({'brief_observations': proposals}))
    body = obligations.reviewed_body(state, body, proposals,
                                    {'stage': 'astra_challenge', 'events': str(events), 'output': str(output)})
    state['goal_contract'] = {'body': body, 'hash': util.digest(body)}
    return state


def encoded(text):
    return base64.b64encode(text.encode()).decode()


class BriefEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='brief-evidence-test-')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.state = build_state(self.root)
        self.calls = []

    def runner(self, workspace, out, *, command, timeout):
        self.calls.append({'workspace': workspace, 'out': out, 'command': command, 'timeout': timeout})
        case = json.loads(shlex.split(command)[-1])
        observed = {'verdict': 'PASS', 'observation_hash': case['hash'], 'reason': '',
                    'steps': [{'argv': step['argv'], 'exit_code': 0,
                               'stdout_base64': encoded('ticket-z brief-probe [open]\n' if i == case['observe_step'] else ''),
                               'stderr_base64': ''} for i, step in enumerate(case['steps'])]}
        out = Path(out)
        out.mkdir(parents=True)
        output = out / 'scratch-command.log'
        output.write_text(json.dumps(observed) + '\n')
        return {'exit_code': 0, 'timed_out': False, 'error': '', 'output': str(output),
                'output_sha256': util.file_hash(output), 'tail': 'untrusted log tail'}

    def replay(self, **kwargs):
        result = evidence.replay(self.state, '/candidate', self.root / 'replay', self.runner,
                                 timeout=30, source_revision='current', **kwargs)
        self.state['validation'] = {'check_replay': {'brief_acceptance': result}}
        return result

    def rewrite_summary(self, result):
        summary = Path(result['summary'])
        util.atomic_json(summary, {key: value for key, value in result.items()
                                   if key not in ('summary', 'summary_sha256')})
        result['summary_sha256'] = util.file_hash(summary)

    def test_current_complete_receipt_is_ready_and_pins_every_output_and_summary(self):
        result = self.replay()
        self.assertTrue(evidence.ready(self.state, 'current'))
        self.assertEqual({result['summary']: result['summary_sha256'],
                          result['checks'][0]['output']: result['checks'][0]['output_sha256']},
                         evidence.evidence_pins(result))
        self.assertEqual(30, self.calls[0]['timeout'])
        self.assertEqual(15, json.loads(shlex.split(self.calls[0]['command'])[-1])['timeout'])

    def test_guarded_observation_requires_its_intact_complete_ownership_extension(self):
        def runner(*args, **kwargs):
            return guarded_command_receipt(self.runner(*args, **kwargs))
        result = evidence.replay(self.state, '/candidate', self.root / 'guarded', runner,
                                 timeout=30, source_revision='current')
        self.state['validation'] = {'check_replay': {'brief_acceptance': result}}
        row = result['checks'][0]
        ownership = Path(row['supervision']['receipt'])
        self.assertTrue(evidence.ready(self.state, 'current'))
        self.assertEqual(row['supervision_sha256'], evidence.evidence_pins(result)[str(ownership)])
        original = ownership.read_bytes()
        ownership.write_bytes(original + b'changed')
        self.assertFalse(evidence.ready(self.state, 'current'))
        ownership.unlink()
        self.assertFalse(evidence.ready(self.state, 'current'))
        ownership.write_bytes(original)
        self.assertTrue(evidence.ready(self.state, 'current'))
        del row['supervision_errors']
        self.rewrite_summary(result)
        self.assertFalse(evidence.ready(self.state, 'current'))

    def test_no_supported_declarations_have_no_new_completion_obligation(self):
        state = {'task': 'Explain an API.'}
        self.assertTrue(evidence.ready(state, 'current'))
        self.assertIsNone(evidence.replay(state, '/candidate', self.root, self.runner,
                                        timeout=30, source_revision='current'))
        self.assertEqual([], self.calls)

    def test_supported_original_brief_without_reviewed_manifest_is_not_ready(self):
        self.assertFalse(evidence.ready({'task': TASK}, 'current'))

    def test_model_pass_without_current_runner_evidence_is_not_ready(self):
        self.state['validation'] = {'verdict': 'PASS', 'checks': [{'exit_code': 0}]}
        self.assertFalse(evidence.ready(self.state, 'current'))

    def test_candidate_contract_and_manifest_changes_invalidate_receipt(self):
        result = self.replay()
        self.assertFalse(evidence.ready(self.state, 'other-source'))
        self.state['goal_contract']['hash'] = 'other-contract'
        self.assertFalse(evidence.ready(self.state, 'current'))
        self.state['goal_contract']['hash'] = result['contract_hash']
        result['manifest_hash'] = 'other-manifest'
        self.rewrite_summary(result)
        self.assertFalse(evidence.ready(self.state, 'current'))

    def test_output_change_deletion_or_symlink_replacement_is_not_ready(self):
        result = self.replay()
        output = Path(result['checks'][0]['output'])
        saved = output.read_bytes()
        output.write_bytes(saved + b'changed')
        self.assertFalse(evidence.ready(self.state, 'current'))
        output.unlink()
        self.assertFalse(evidence.ready(self.state, 'current'))
        replacement = self.root / 'replacement.log'
        replacement.write_bytes(saved)
        output.symlink_to(replacement)
        self.assertFalse(evidence.ready(self.state, 'current'))

    def test_summary_change_or_saved_receipt_change_is_not_ready(self):
        result = self.replay()
        result['checks'][0]['observation']['reason'] = 'changed'
        self.assertFalse(evidence.ready(self.state, 'current'))
        result['checks'][0]['observation']['reason'] = ''
        Path(result['summary']).write_text('{}')
        self.assertFalse(evidence.ready(self.state, 'current'))

    def test_omitted_case_cannot_be_hidden_by_rehashing_summary(self):
        result = self.replay()
        result['checks'].clear()
        self.rewrite_summary(result)
        self.assertFalse(evidence.ready(self.state, 'current'))

    def test_full_cli_output_and_step_identity_are_rechecked_even_after_rehash(self):
        for transform in ['unbracketed', 'wrong-argv', 'wrong-hash', 'missing-step', 'nonzero', 'bad-base64']:
            with self.subTest(transform=transform):
                result = self.replay()
                row = result['checks'][0]
                observed = copy.deepcopy(row['observation'])
                if transform == 'unbracketed':
                    observed['steps'][1]['stdout_base64'] = encoded('ticket-z brief-probe open\n')
                elif transform == 'wrong-argv':
                    observed['steps'][0]['argv'] = ['add', 'different']
                elif transform == 'wrong-hash':
                    observed['observation_hash'] = '0' * 64
                elif transform == 'missing-step':
                    observed['steps'].pop()
                elif transform == 'nonzero':
                    observed['steps'][0]['exit_code'] = 2
                else:
                    observed['steps'][1]['stdout_base64'] = 'not!base64'
                Path(row['output']).write_text(json.dumps(observed))
                row['output_sha256'] = util.file_hash(row['output'])
                row['observation'] = observed
                self.rewrite_summary(result)
                self.assertFalse(evidence.ready(self.state, 'current'))

    def test_full_output_cannot_be_replaced_by_a_passing_tail_or_partial_json(self):
        original = self.runner
        def misleading(workspace, out, **kwargs):
            result = original(workspace, out, **kwargs)
            path = Path(result['output'])
            result['tail'] = path.read_text()
            path.write_text('earlier non-JSON runner output\n' + result['tail'])
            result['output_sha256'] = util.file_hash(path)
            return result
        with self.assertRaisesRegex(ValueError, 'complete JSON'):
            evidence.replay(self.state, '/candidate', self.root / 'replay', misleading,
                            timeout=30, source_revision='current')
        summary = next((self.root / 'replay').glob('brief-acceptance/*/summary.json'))
        self.assertEqual('FAIL', json.loads(summary.read_text())['verdict'])

    def test_outer_timeout_or_failed_runner_exit_refuses_passing_json(self):
        original = self.runner
        for field, value in [('exit_code', 2), ('timed_out', True), ('error', 'copy refused')]:
            with self.subTest(field=field):
                def failed(workspace, out, **kwargs):
                    return {**original(workspace, out, **kwargs), field: value}
                with self.assertRaises(ValueError):
                    evidence.replay(self.state, '/candidate', self.root / 'replay', failed,
                                    timeout=30, source_revision='current')

    def test_each_replay_executes_fresh_without_reusing_previous_receipt(self):
        first, second = self.replay(), self.replay()
        self.assertEqual(2, len(self.calls))
        self.assertNotEqual(first['summary'], second['summary'])
        self.assertNotEqual(first['checks'][0]['output'], second['checks'][0]['output'])
        self.assertTrue(Path(first['summary']).is_file())

    def test_task_slice_and_progressive_contribution_do_not_satisfy_final_inventory(self):
        self.state = build_state(self.root, TASK + '\n' + TASK.replace('todo.py', 'other.py'))
        self.state['current_task'] = {'acceptance_criteria': ['AC1']}
        result = self.replay()
        self.assertEqual(1, len(result['checks']))
        self.assertFalse(evidence.ready(self.state, 'current'))
        context = {'required_checks': [{'relation': 'contributes_to', 'criterion_ids': ['AC1']}]}
        self.assertIsNone(self.replay(progressive_context=context))
        context = {'required_checks': [{'relation': 'fully_verify', 'criterion_ids': ['AC2']}]}
        result = self.replay(progressive_context=context)
        self.assertEqual(1, len(result['checks']))
        self.assertFalse(evidence.ready(self.state, 'current'))
        self.state.pop('current_task')
        result = self.replay()
        self.assertEqual(2, len(result['checks']))
        self.assertTrue(evidence.ready(self.state, 'current'))


class RealBriefScratchTests(unittest.TestCase):
    def test_full_product_replay_ignores_last_assignment_slice_on_current_source(self):
        with tempfile.TemporaryDirectory(prefix='brief-full-product-') as directory:
            root = Path(directory).resolve()
            workspace = root / 'product'
            workspace.mkdir()
            for name in ('todo.py', 'other.py'):
                (workspace / name).write_text(PRODUCT)
            for args in [('init', '-q'), ('config', 'maintenance.auto', 'false'), ('config', 'gc.auto', '0'),
                         ('add', '.'), ('-c', 'user.name=t', '-c', 'user.email=t@example.test', 'commit', '-qm', 'seed')]:
                subprocess.run(['git', '-C', str(workspace), *args], check=True, capture_output=True)
            state = build_state(root, TASK + '\n' + TASK.replace('todo.py', 'other.py'))
            state['current_task'] = {'acceptance_criteria': ['AC2']}
            revision = util.snapshot(workspace)['revision']
            partial = evidence.replay(state, workspace, root / 'replay', verify.scratch_run,
                                      timeout=30, source_revision=revision)
            state['validation'] = {'check_replay': {'brief_acceptance': partial}}
            self.assertEqual(1, len(partial['checks']))
            self.assertFalse(evidence.ready(state, revision))
            replayed = check_replay.replay(
                [{'command': "python3 -c 'print(1)'", 'exit_code': 0, 'evidence_ref': 'public-probe'}],
                workspace, root / 'replay', {'source_revision': revision, 'output': 'validator.json'},
                verify.scratch_run, timeout=30, approved_state=state, all_brief_observations=True)
            self.assertEqual('PASS', replayed['verdict'])
            full = replayed['brief_acceptance']
            state['validation'] = {'check_replay': {'brief_acceptance': full}}
            self.assertEqual(2, len(full['checks']))
            self.assertTrue(evidence.ready(state, revision))
            self.assertNotEqual(partial['checks'][0]['output'], full['checks'][1]['output'])
            self.assertEqual(revision, util.snapshot(workspace)['revision'])

    def test_public_candidate_reference_passes_and_unbracketed_mutant_is_refused(self):
        with tempfile.TemporaryDirectory(prefix='brief-evidence-scratch-') as directory:
            root = Path(directory).resolve()
            workspace = root / 'product'
            workspace.mkdir()
            def git(*args):
                subprocess.run(['git', '-C', str(workspace), *args], check=True, capture_output=True)
            git('init', '-q')
            git('config', 'maintenance.auto', 'false')
            git('config', 'gc.auto', '0')
            (workspace / 'todo.py').write_text(PRODUCT)
            # A candidate module tries to fabricate the trusted supervisor's
            # complete passing JSON. Only its isolated outer Python may attest.
            (workspace / 'base64.py').write_text('''import binascii,json,sys
case=json.loads(sys.argv[-1])
def enc(text): return binascii.b2a_base64(text.encode()).decode().strip()
rows=[{'argv':step['argv'],'exit_code':0,'stdout_base64':enc('ticket-z brief-probe [open]\\n' if i==case['observe_step'] else ''),'stderr_base64':''} for i,step in enumerate(case['steps'])]
print(json.dumps({'verdict':'PASS','observation_hash':case['hash'],'steps':rows,'reason':''}))
sys.exit(0)
''')
            git('add', '.')
            git('-c', 'user.name=t', '-c', 'user.email=t@example.test', 'commit', '-qm', 'public seed')
            state = build_state(root)
            for code, passing in [(PRODUCT, True), (PRODUCT.replace(' [open]', ' open'), False)]:
                with self.subTest(passing=passing):
                    (workspace / 'todo.py').write_text(code)
                    revision = util.snapshot(workspace)['revision']
                    if passing:
                        result = evidence.replay(state, workspace, root / 'replay', verify.scratch_run,
                                                 timeout=30, source_revision=revision)
                        state['validation'] = {'check_replay': {'brief_acceptance': result}}
                        self.assertTrue(evidence.ready(state, revision))
                        self.assertEqual([0, 0], [row['exit_code'] for row in result['checks'][0]['observation']['steps']])
                    else:
                        with self.assertRaisesRegex(ValueError, 'mandatory original-brief'):
                            evidence.replay(state, workspace, root / 'replay', verify.scratch_run,
                                            timeout=30, source_revision=revision)
                        self.assertFalse(evidence.ready(state, revision))
                    self.assertFalse((workspace / 'state.json').exists(), 'Probe data belongs only in contained scratch')
            failed = [json.loads(path.read_text()) for path in (root / 'replay').glob('brief-acceptance/*/summary.json')
                      if json.loads(path.read_text())['verdict'] == 'FAIL']
            self.assertEqual(1, len(failed))
            output = json.loads(Path(failed[0]['checks'][0]['output']).read_text())
            self.assertEqual([0, 0], [row['exit_code'] for row in output['steps']])
            self.assertIn('original brief format', output['reason'])


if __name__ == '__main__':
    unittest.main()
