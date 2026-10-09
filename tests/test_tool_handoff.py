"""Provider tool contracts expose a usable capture command for every job."""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_bug_job as bug
import autocode_design_check_job as check_design
import autocode_design_job as design
import autocode_discuss_job as discuss
import autocode_review_job as review
import autocode_stuck_job as stuck
from providers import command, opencode

MARKER = '\nCURRENT HANDOFF DATA\n'
ROOT = Path(__file__).resolve().parents[1]


def renderers():
    for output in ('report_file', 'opencode_events'):
        provider = command.CommandProvider({'name': 'fixture', 'roles': {}, 'output': output}, Path('unused.toml'))
        yield output, provider.prompt_for_schema
    yield 'native_opencode', opencode.prompt_for_schema


class ToolHandoffTests(unittest.TestCase):
    def test_every_specialized_job_receives_the_capture_helper(self):
        state = {'task': 'Inspect the integer result.', 'workspace': '/workspace',
                 'stuck_investigation': {'stage': 'terra', 'status': 'PAUSED_PROVIDER_TIMEOUT',
                                        'reason': 'timeout', 'identity': 'current'}}
        prompts = [(module.STAGE, module.prompt(state)[0])
                   for module in (bug, review, design, check_design, discuss)]
        prompts.append((stuck.STAGE, stuck.prompt(state, '/run/state.json')[0]))
        for name, render in renderers():
            for stage, prompt in prompts:
                with self.subTest(provider=name, stage=stage):
                    before = json.loads(prompt.split(MARKER, 1)[1])
                    result = render(prompt, {'type': 'object'}, Path('/run/attempt.jsonl'))
                    data = json.loads(result.split(MARKER, 1)[1])
                    self.assertIn('capture_command', data)
                    helper = shlex.split(data.pop('capture_command'))
                    self.assertEqual([sys.executable, str(ROOT/'tools/autocode.py'), 'capture'], helper)
                    self.assertEqual(before, data)
                    self.assertTrue(result.startswith(prompt.split(MARKER, 1)[0]))

    def test_existing_capture_and_original_packet_bytes_are_preserved(self):
        packet = '{ "capture_command": "custom-helper --mode raw", "task": "a\\nb" }\n'
        prompt = 'Keep this instruction.' + MARKER + packet
        for name, render in renderers():
            with self.subTest(provider=name):
                result = render(prompt, {}, Path('/run/attempt.jsonl'))
                self.assertEqual(packet, result.split(MARKER, 1)[1])

    def test_helper_executes_the_public_cli_and_preserves_evidence_and_exit(self):
        prompt = bug.prompt({'task': 'Inspect result.', 'workspace': '/project'})[0]
        rendered = next(renderers())[1](prompt, {}, Path('/run/attempt.jsonl'))
        data = json.loads(rendered.split(MARKER, 1)[1])
        self.assertIn('capture_command', data)
        with tempfile.TemporaryDirectory(prefix='capture handoff ') as directory:
            workspace = Path(directory)
            receipt = workspace/'.autocode/evidence/check.json'
            context = {'attempt': '/run/attempt.json', 'nonce': 'test-nonce', 'source_revision': 'original'}
            env = {**os.environ, 'AUTOCODE_OUTPUT_MODE': 'raw',
                   'AUTOCODE_CAPTURE_CONTEXT': json.dumps(context),
                   'AUTOCODE_OUTPUT_STORE': str(workspace/'.autocode/output')}
            probe = [sys.executable, '-c', "import sys; print('observed'); sys.exit(7)"]
            result = subprocess.run([*shlex.split(data['capture_command']), '--output', str(receipt), '--', *probe],
                                    cwd=workspace, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(7, result.returncode, result.stderr)
            saved = json.loads(receipt.read_text())
            self.assertEqual(probe, saved['command'])
            self.assertEqual(7, saved['exit_code'])
            self.assertEqual(context, saved['capture_context'])
            self.assertEqual('raw', saved['summary']['filter'])
            output = Path(saved['full_output']).read_bytes()
            self.assertEqual(b'observed\n', output)
            self.assertEqual(hashlib.sha256(output).hexdigest(), saved['full_output_sha256'])

    def test_invalid_tool_context_fails_before_provider_dispatch(self):
        for packet in ('[]', 'null', 'invalid JSON', '{"capture_command": null}', '{"capture_command": " "}'):
            for name, render in renderers():
                with self.subTest(provider=name, packet=packet):
                    with self.assertRaisesRegex(ValueError, 'Provider handoff'):
                        render('Task' + MARKER + packet, {}, Path('/run/a.jsonl'))

    def test_helper_quotes_installation_paths_with_spaces(self):
        import autocode_tool_handoff as handoff
        with patch.object(handoff.sys, 'executable', '/runtime space/python'), \
                patch.object(handoff, '__file__', '/install space/autocode_tool_handoff.py'):
            rendered = handoff.with_capture_command('Task' + MARKER + '{}')
        data = json.loads(rendered.split(MARKER, 1)[1])
        self.assertEqual(['/runtime space/python', '/install space/autocode.py', 'capture'],
                         shlex.split(data['capture_command']))

    def test_markerless_prompt_keeps_its_existing_behavior(self):
        for name, render in renderers():
            with self.subTest(provider=name):
                self.assertEqual('Unstructured legacy prompt', render('Unstructured legacy prompt', {}, Path('/run/a.jsonl')))


if __name__ == '__main__':
    unittest.main()
