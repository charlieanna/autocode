"""Prerequisite semantics: a healthy setup can still have a failing product."""
import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import autocode_preflight_contract as contract
import autocode_preflight_design as design
import autocode_preflight_worker as worker
import autocode_task_preflight as preflight
import autocode_util as util
from tests.test_task_preflight import PreflightFixture, ROOT, entry
from tests.test_design_manifest import bundle, inventory_bundle


def browser_contract():
    return {'kind': 'browser', 'viewport': {'width': 2, 'height': 1, 'device_scale_factor': 1},
            'canvas': {'alpha': 'composite', 'background': '#ffffff'},
            'fonts': ['Fixture Font'], 'ready': ['catalogue-completed', 'catalogue-nonempty']}


def row(kind=None):
    return {'id': 'fixture', 'phase': 'planning', 'argv': ['{python}', 'probe.py'],
            'execution': 'worker', 'reuse': False, 'recovery': 'Restore the approved fixture and resume',
            'contract': kind or {'kind': 'command'}}


class ResultContractTests(unittest.TestCase):
    def browser(self):
        expected = browser_contract()
        return row(expected), {**expected, 'kind': 'prerequisite', 'status': 'READY',
                              'setup': True, 'teardown': True, 'launched': True, 'captured': True}

    def test_browser_rejects_missing_state_font_dpr_canvas_capture_and_teardown(self):
        check, good = self.browser()
        self.assertEqual(good, contract.check_result(check, contract.MARKER + json.dumps(good), {}))
        for key, value in [('ready', ['catalogue-completed']), ('fonts', []), ('viewport', {'width': 2, 'height': 1, 'device_scale_factor': 2}),
                           ('canvas', {'alpha': 'preserve', 'background': None}), ('captured', False), ('teardown', False)]:
            with self.subTest(key=key), self.assertRaises(ValueError):
                contract.check_result(check, contract.MARKER + json.dumps({**good, key: value}), {})

    def test_aggregate_exit_and_duplicate_results_cannot_establish_browser_readiness(self):
        check, result = self.browser()
        for text in ('tests passed', contract.MARKER + json.dumps(result) + '\n' + contract.MARKER + json.dumps(result)):
            with self.assertRaisesRegex(ValueError, 'exactly one'):
                contract.check_result(check, text, {})

    def test_protected_tests_and_mismatched_plan_refuse_impossible_proof(self):
        policy = {'kind': 'proof', 'test_changes_allowed': True, 'cases': [{'id': 'C1', 'selector': 'test_x.T.test_c1', 'kind': 'fail_to_pass'}]}
        result = {'kind': 'prerequisite', 'status': 'READY', 'setup': True, 'teardown': True,
                  'controls': {name: {'test_x.T.test_c1': status} for name, status in [('baseline', 'FAIL'), ('reference', 'PASS'), ('broken', 'FAIL')]}}
        text = contract.MARKER + json.dumps(result)
        contract.check_result(row(policy), text, {})
        policy['test_changes_allowed'] = False
        with self.assertRaisesRegex(ValueError, 'protected-test policy'):
            contract.check_result(row(policy), text, {})
        policy['test_changes_allowed'] = True
        state = {'goal_contract': {'body': {'acceptance_criteria': [{'id': 'C1', 'verification_method': 'guard: test_c1'}]}}}
        with self.assertRaisesRegex(ValueError, 'no matching'):
            contract.check_result(row(policy), text, state)
        for control, status in [('baseline', 'PASS'), ('baseline', 'ERROR'), ('reference', 'FAIL'), ('broken', 'PASS')]:
            broken = copy.deepcopy(result)
            broken['controls'][control]['test_x.T.test_c1'] = status
            with self.subTest(control=control, status=status), self.assertRaises(ValueError):
                contract.check_result(row(policy), contract.MARKER + json.dumps(broken), {})

    def test_v2_cannot_select_unbounded_timeout_or_controller_browser(self):
        for broken in [{**row(), 'timeout_seconds': 0}, {**row(), 'timeout_seconds': float('inf')},
                       {**row(browser_contract()), 'execution': 'runner'}, {**row(browser_contract()), 'reuse': True}]:
            with self.assertRaises(ValueError):
                contract.validate_check(broken, 2)


class ControlExecutionTests(PreflightFixture):
    def controls(self):
        for name, result in [('baseline', 0), ('reference', 1), ('broken', 0)]:
            root = self.workspace / name
            root.mkdir()
            (root / 'product.py').write_text(f'VALUE = {result}\n')
            (root / 'test_product.py').write_text('import unittest\nimport product\nclass T(unittest.TestCase):\n def test_c1(self): self.assertEqual(1, product.VALUE)\n')
        path = self.workspace / 'controls.json'
        path.write_text(json.dumps({'controls': {name: name for name in ('baseline', 'reference', 'broken')}, 'selectors': ['test_product.T.test_c1']}))
        return path

    def run_controls(self, path):
        result = subprocess.run([sys.executable, str(ROOT / 'tools/autocode_preflight_proof.py'), '--inventory', str(path)],
                                cwd=self.workspace, capture_output=True, text=True)
        lines = [line[len(contract.MARKER):] for line in result.stdout.splitlines() if line.startswith(contract.MARKER)]
        return result, json.loads(lines[-1])

    def test_separate_control_processes_report_expected_failure_not_setup_error(self):
        path = self.controls()
        process, result = self.run_controls(path)
        self.assertEqual(0, process.returncode, process.stderr)
        self.assertEqual({'baseline': {'test_product.T.test_c1': 'FAIL'}, 'reference': {'test_product.T.test_c1': 'PASS'},
                          'broken': {'test_product.T.test_c1': 'FAIL'}}, result['controls'])
        (self.workspace / 'broken/product.py').unlink()
        process, result = self.run_controls(path)
        self.assertEqual(1, process.returncode, process.stdout)
        self.assertEqual('ERROR', result['controls']['broken']['test_product.T.test_c1'])

    def test_teardown_error_is_never_misclassified_as_success(self):
        path = self.controls()
        test = self.workspace / 'reference/test_product.py'
        test.write_text(test.read_text() + '\ndef tearDownModule(): raise RuntimeError("fixture cleanup failed")\n')
        process, result = self.run_controls(path)
        self.assertEqual(1, process.returncode)
        self.assertEqual('ERROR', result['controls']['reference']['test_product.T.test_c1'])

    def test_later_subtest_assertion_cannot_hide_an_earlier_error(self):
        path = self.controls()
        (self.workspace / 'broken/test_product.py').write_text(
            'import unittest\nclass T(unittest.TestCase):\n'
            ' def test_c1(self):\n'
            '  with self.subTest(part="setup"): raise RuntimeError("fixture unavailable")\n'
            '  with self.subTest(part="behavior"): self.fail("expected behavior failure")\n')
        process, result = self.run_controls(path)
        self.assertEqual(1, process.returncode)
        self.assertEqual('ERROR', result['controls']['broken']['test_product.T.test_c1'])

    def test_aggregate_approved_command_blocks_before_control_execution(self):
        state = self.state()
        state['settings']['regression'] = {'test_command': 'node checks/all-tests.js'}
        contract_body = {'kind': 'proof', 'test_changes_allowed': True,
            'cases': [{'id': 'C1', 'selector': 'test_product.T.test_c1', 'kind': 'fail_to_pass'}]}
        body = {'version': 2, 'checks': [{**row(contract_body), 'execution': 'runner'}]}
        self.path.write_text(json.dumps(body))
        state['settings']['task_preflight'] = preflight.load(self.path)
        with patch.object(worker, 'run') as execute, self.assertRaisesRegex(util.Paused, 'cannot supply the named results'):
            preflight.guard(state, self.workspace, self.run_dir)
        execute.assert_not_called()

    def test_collection_hooks_and_exclusions_do_not_execute_test_methods(self):
        (self.workspace / 'test_setup.py').write_text('import unittest\nclass T(unittest.TestCase):\n def test_c1(self): raise AssertionError("must not execute")\n')
        (self.workspace / 'hooks.py').write_text('def setup(): pass\ndef teardown(): raise RuntimeError("cleanup failed")\n')
        command = [sys.executable, str(ROOT / 'tools/autocode_preflight_unittest.py'), '--discover', '.', '--setup', 'hooks:setup', '--teardown', 'hooks:teardown']
        bad = subprocess.run(command, cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(1, bad.returncode)
        self.assertIn('cleanup failed', bad.stdout)
        (self.workspace / 'hooks.py').write_text('def setup(): pass\ndef teardown(): pass\n')
        inventory = self.workspace / 'excluded.json'
        inventory.write_text(json.dumps({'test_setup.T.test_c1': 'Separate mandatory gate'}))
        good = subprocess.run([*command, '--exclusions', str(inventory)], cwd=self.workspace, capture_output=True, text=True)
        self.assertEqual(0, good.returncode, good.stderr + good.stdout)
        result = json.loads(good.stdout)
        self.assertFalse(result['tests_executed'])
        self.assertEqual(['test_setup.T.test_c1'], result['collections'][0]['identities'])


class DesignReadinessTests(PreflightFixture):
    def configure_design(self):
        path, body = bundle(self.workspace / 'public')
        text = (path.parent / 'context.txt').read_text()
        (self.workspace / 'context-part.txt').write_text(text)
        (self.workspace / 'font.woff2').write_bytes(b'fixture font; browser load checked separately')
        design_config = {'manifest': 'public/manifest.json', 'cases': [
            {'id': case['id'], 'encoding': 'text', 'context_parts': ['context-part.txt'], 'assets': [],
             'fonts': [{'family': 'Fixture Font', 'path': 'font.woff2'}], 'canvas': browser_contract()['canvas']} for case in body['cases']]}
        inputs = [entry(p, str(p.relative_to(self.workspace))) for p in [path, path.parent / 'screen.png', path.parent / 'context.txt', self.workspace / 'context-part.txt', self.workspace / 'font.woff2']]
        settings = {'design_manifest': design.design.load(path), 'task_preflight': {'body': {'checks': [row(browser_contract())]}}}
        return design_config, inputs, settings

    def test_missing_case_input_or_truncated_derivative_blocks_design_readiness(self):
        config, inputs, settings = self.configure_design()
        result = design.check(settings, config, self.workspace, self.workspace, inputs)
        self.assertEqual(['greet.empty', 'greet.filled'], [r['id'] for r in result])
        with self.assertRaisesRegex(ValueError, 'not hash-bound'):
            design.check(settings, config, self.workspace, self.workspace, inputs[:-1])
        (self.workspace / 'context-part.txt').write_text('partial context')
        with self.assertRaisesRegex(ValueError, 'verbatim'):
            design.check(settings, config, self.workspace, self.workspace, inputs)
        with self.assertRaisesRegex(ValueError, 'no design readiness inventory'):
            design.check(settings, None, self.workspace, self.workspace, inputs)

    def test_wrong_alpha_and_omitted_frame_cannot_match_capture_contract(self):
        config, inputs, settings = self.configure_design()
        config['cases'][0]['canvas'] = {'alpha': 'preserve', 'background': None}
        with self.assertRaisesRegex(ValueError, 'viewport/DPR, canvas'):
            design.check(settings, config, self.workspace, self.workspace, inputs)

    def test_browser_readiness_for_another_phase_cannot_admit_visual_build(self):
        config, inputs, settings = self.configure_design()
        settings['task_preflight']['body']['checks'][0]['phase'] = 'validate'
        with self.assertRaisesRegex(ValueError, 'no worker browser prerequisite'):
            design.check(settings, config, self.workspace, self.workspace, inputs, phase='build')
        config['cases'].pop()
        with self.assertRaisesRegex(ValueError, 'every approved'):
            design.check(settings, config, self.workspace, self.workspace, inputs)

    def test_v2_full_frame_metadata_catalog_and_native_node_are_bound_before_planning(self):
        path, body = inventory_bundle(self.workspace / "public")
        (self.workspace / "context-part.txt").write_text((path.parent / "context.txt").read_text())
        config = {"manifest": str(path.relative_to(self.workspace)), "cases": [
            {"id": case["id"], "encoding": "text", "context_parts": ["context-part.txt"],
             "assets": ["public/icon.svg"], "fonts": [{"family": "Inter", "path": "public/inter.woff2"}],
             "canvas": browser_contract()["canvas"]} for case in body["cases"]]}
        source_files = [path, self.workspace / "context-part.txt"]
        source_files.extend(path.parent / rel for rel in ("context.txt", "screen.png", "icon.svg", "inter.woff2",
                                                            "FILEA-page.xml", "FILEB-page.xml",
                                                            "FILEA-file.xml", "FILEB-file.xml",
                                                            "FILEA-source.json", "FILEB-source.json"))
        inputs = [entry(source, str(source.relative_to(self.workspace))) for source in source_files]
        browser = {**browser_contract(), "fonts": ["Inter"]}
        settings = {"figma_file": "https://www.figma.com/design/FILEA?node-id=1-2",
                    "design_manifest": design.design.load(path),
                    "task_preflight": {"body": {"checks": [row(browser)]}}}
        result = design.check(settings, config, self.workspace, self.workspace, inputs)
        self.assertEqual({case["id"] for case in body["cases"]}, {item["id"] for item in result})
        with self.assertRaisesRegex(ValueError, "not hash-bound"):
            design.check(settings, config, self.workspace, self.workspace,
                         [item for item in inputs if item["path"] != "public/FILEB-page.xml"])
        settings["figma_file"] = "https://www.figma.com/design/FILEB?node-id=3-5"
        self.assertEqual(3, len(design.check(settings, config, self.workspace, self.workspace, inputs)))
        settings["figma_file"] = "https://www.figma.com/design/FILEA?node-id=1-90"
        with self.assertRaisesRegex(ValueError, "no matching exported file/frame"):
            design.check(settings, config, self.workspace, self.workspace, inputs)

    def test_context_export_is_lossless_unicode_and_bound_to_original(self):
        source = self.workspace / 'raw.json'
        value = 'font-weight: 600;\n' + 'é🙂' * 1700
        source.write_text(json.dumps({'content': [{'type': 'text', 'text': value}]}))
        output = self.workspace / 'readable'
        design.main([str(source), '--encoding', 'mcp-text', '--output', str(output)])
        parts = sorted(output.glob('part-*.txt'))
        self.assertEqual(value, ''.join(p.read_text() for p in parts))
        self.assertTrue(all(p.stat().st_size <= 1600 for p in parts))


class WorkerPermissionTests(PreflightFixture):
    def opencode_context(self):
        return {'engine': 'opencode', 'configured': False, 'command': ['opencode', 'run', '--agent', 'autocode_terra'],
                'environment': {'OPENCODE_CONFIG_CONTENT': '{}'}, 'model': 'fixture/model'}

    def test_ask_cannot_be_auto_approved_by_opencode_debug(self):
        policy = {'permission': [{'permission': 'bash', 'pattern': '*', 'action': 'ask'}], 'tools': {'bash': True}}
        def inspect(argv, cwd, output, **kwargs):
            Path(output).write_text(json.dumps(policy))
            return {'exit_code': 0}
        with patch.object(worker, 'run', side_effect=inspect) as execute, self.assertRaisesRegex(ValueError, 'auto-approve'):
            worker.execute(self.opencode_context(), ['echo', 'ready'], self.workspace, self.root / 'log', timeout=2)
        self.assertEqual(1, execute.call_count)

    def test_debug_process_success_does_not_hide_command_failure(self):
        policy = {'permission': [{'permission': '*', 'pattern': '*', 'action': 'allow'}], 'tools': {'bash': True}}
        def inspect(argv, cwd, output, **kwargs):
            value = {'result': {'metadata': {'exit': 13}, 'output': 'browser launch denied'}} if '--tool' in argv else policy
            Path(output).write_text(json.dumps(value))
            return {'exit_code': 0}
        with patch.object(worker, 'run', side_effect=inspect):
            result, text = worker.execute(self.opencode_context(), ['browser'], self.workspace, self.root / 'log', timeout=2)
        self.assertEqual(13, result['exit_code'])
        self.assertIn('launch denied', text)

    def test_changed_worker_permission_configuration_invalidates_identity(self):
        config = self.root / 'codex-config'
        config.mkdir()
        path = config / 'config.toml'
        path.write_text('sandbox_mode="read-only"\n')
        context = {'engine': 'codex', 'provider': 'codex', 'configured': False, 'role': 'terra',
                   'sandbox': 'read-only', 'planning': False, 'model': 'fixture', 'command': [sys.executable, 'exec'],
                   'environment': {**os.environ, 'CODEX_HOME': str(config)}}
        previous = worker.identity(context, self.workspace)
        path.write_text('sandbox_mode="workspace-write"\n')
        self.assertNotEqual(previous['configuration_hash'], worker.identity(context, self.workspace)['configuration_hash'])

    def test_unsupported_provider_cannot_fall_back_to_a_paid_command(self):
        context = {'engine': 'opencode', 'configured': True, 'command': ['must-not-launch'], 'environment': {}}
        with patch.object(worker, 'run') as execute, self.assertRaisesRegex(ValueError, 'no model-free'):
            worker.execute(context, ['node', 'fixture.js'], self.workspace, self.root / 'log', timeout=2)
        execute.assert_not_called()

    def test_output_attribution_reuses_identity_but_output_policy_does_not(self):
        context = {'engine': 'codex', 'provider': 'codex', 'configured': False, 'role': 'terra',
            'sandbox': 'read-only', 'planning': False, 'model': 'fixture', 'command': [sys.executable],
            'environment': {'CODEX_HOME': str(self.root), 'AUTOCODE_OUTPUT_MODE': 'conservative',
                            'AUTOCODE_OUTPUT_ATTEMPT': 'attempt-one.jsonl'}}
        previous = worker.identity(context, self.workspace)
        context['environment']['AUTOCODE_OUTPUT_ATTEMPT'] = 'attempt-two.jsonl'
        self.assertEqual(previous, worker.identity(context, self.workspace))
        context['environment']['AUTOCODE_OUTPUT_MODE'] = 'raw'
        self.assertNotEqual(previous, worker.identity(context, self.workspace))

    def test_opencode_wildcards_preserve_literal_brackets_and_optional_arguments(self):
        self.assertTrue(worker.permission_match('file[1].png', 'file[1].png'))
        self.assertFalse(worker.permission_match('file1.png', 'file[1].png'))
        self.assertTrue(worker.permission_match('python', 'python *'))
        self.assertTrue(worker.permission_match('read', '*'))

    def test_v2_timeout_and_pause_stop_probe_without_a_model(self):
        # The fake process remains pending; fake time crosses the deadline.
        process = unittest.mock.MagicMock(pid=99999999, returncode=-9)
        process.poll.return_value = None
        process.wait.side_effect = [subprocess.TimeoutExpired('fixture', 1), 0]
        with patch.object(worker.subprocess, 'Popen', return_value=process), patch.object(worker.time, 'monotonic', side_effect=[0, 0, 0, 2, 2]), patch.object(worker.os, 'killpg') as kill:
            result = worker.run(['fixture'], self.workspace, self.root / 'log', timeout=1, environment={})
        self.assertTrue(result['timed_out'])
        self.assertIsNone(result['exit_code'])
        kill.assert_called_once()
        process.wait.side_effect = None
        with patch.object(worker.subprocess, 'Popen', return_value=process), patch.object(worker.os, 'killpg'):
            result = worker.run(['fixture'], self.workspace, self.root / 'pause-log', timeout=1, environment={}, paused=lambda: True)
        self.assertTrue(result['interrupted'])


if __name__ == '__main__':
    unittest.main()
