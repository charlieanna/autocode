"""Dependency recovery through the real CLI, with only provider I/O replaced."""
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from autocode_taskrun import TaskRun, TaskRunError
from autocode_dependencies import tick, transport
from autocode_dependency import contained
from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS, HERE


class DependencyFlowTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='dependency-flow-')
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        bindir = self.root / 'bin'
        bindir.mkdir()
        provider = (HERE / 'live_fixture_provider.py').read_text()
        # A valid reviewed implementation that needs an external delivery before continuing.
        inject = '''    if stage == "astra_review" and __import__('os').environ.get('DEPENDENT') and not (data.get('recovery_context') or {}).get('manifest'):
        report.update(status="BLOCKED", blocker="Waiting for accepted producer delivery")
        report['user_request'] = {"kind": "permission", "discovered": "Prerequisite delivery absent",
            "impact": "Cannot integrate without accepted source", "decision_needed": "Supply accepted producer delivery",
            "options": ["Supply delivery", "Keep paused"], "proposed_delta": ""}
'''
        provider = provider.replace('    output = Path(sys.argv', inject + '    output = Path(sys.argv')
        (bindir / 'codex').write_text(provider)
        (bindir / 'codex').chmod(0o755)
        self.env = {'PATH': f'{bindir}{os.pathsep}{os.environ["PATH"]}',
                    'AUTOCODE_HOME': str(self.root / 'registry'), 'PYTHONDONTWRITEBYTECODE': '1'}

    def start(self, name, dependent=False):
        workspace = self.root / name
        workspace.mkdir()
        subprocess.run(['git', 'init', '-q', str(workspace)], check=True)
        subprocess.run(['git', '-C', str(workspace), '-c', 'user.name=T', '-c', 'user.email=t@example.test',
                        'commit', '-q', '--allow-empty', '-m', 'base'], check=True)
        env = {**self.env, **({'DEPENDENT': '1'} if dependent else {})}
        run = TaskRun.start(workspace, BRIEF, options=FIXTURE_OPTIONS, env=env, timeout=120)
        run.approve_plan(run.status()['needs']['token'])
        return run

    def test_wait_transfer_resume_and_stale_evidence(self):
        producer = self.start('producer')
        consumer = self.start('consumer', dependent=True)
        view = consumer.advance_until_input()
        need = view['needs']
        self.assertEqual('answer', need['kind'], view)
        spec = {'producer_workspace': str(producer.workspace), 'producer_run': str(producer.run_dir),
                'question_id': need['questions'][0]['id'], 'request_token': need['resolver_token'],
                'files': ['greet.py', 'test_greet.py', 'README.md'],
                'destination': 'evidence/delivery', 'label': 'Greeting backend'}
        specfile = self.root / 'binding.json'
        specfile.write_text(json.dumps({**spec, 'request_token': 'stale'}))
        with self.assertRaises(TaskRunError):
            consumer.bind_dependency(specfile)
        self.assertEqual('answer', consumer.status()['needs']['kind'])
        specfile.write_text(json.dumps(spec))
        view = consumer.bind_dependency(specfile)
        self.assertEqual('dependency', view['needs']['kind'])
        self.assertNotIn('questions', view['needs'])
        # Reattach after restart: no provider launched while the producer is incomplete.
        restarted = TaskRun(consumer.workspace, consumer.run_dir, options=FIXTURE_OPTIONS, env=consumer.env)
        self.assertEqual('waiting', tick(restarted))
        self.assertFalse((consumer.run_dir / 'evidence/delivery').exists())
        completed = producer.advance_until_input()
        self.assertTrue(completed['done'], completed)
        self.assertTrue(completed['delivery']['verified_complete'])
        # A completed flag alone cannot authorize stale source.
        source = producer.workspace / 'greet.py'
        original = source.read_bytes()
        source.write_bytes(original + b'\n# changed after review\n')
        self.assertIsNone(producer.status()['delivery'])
        self.assertEqual('waiting', tick(restarted))
        source.write_bytes(original)
        wait = restarted.status()['dependency']
        stable = producer.status()
        with patch.object(producer, 'status', side_effect=[stable, {**stable, 'delivery': None}]):
            with self.assertRaisesRegex(ValueError, 'changed during transport'):
                transport(wait, producer, consumer.run_dir / wait['destination'])
        self.assertFalse((consumer.run_dir / wait['destination']).exists())
        manifest = transport(wait, producer, consumer.run_dir / wait['destination'])
        # Replay transport is idempotent, and altered delivery is rejected before resume.
        self.assertEqual(manifest, transport(wait, producer, manifest.parent))
        target = manifest.parent / 'source/greet.py'
        target.write_bytes(b'corrupt')
        with self.assertRaises(TaskRunError):
            restarted.receive_dependency(manifest)
        self.assertEqual('dependency', restarted.status()['needs']['kind'])
        target.write_bytes(original)
        consumer_source = consumer.workspace / 'greet.py'
        consumer_original = consumer_source.read_bytes()
        consumer_source.write_bytes(consumer_original + b'\n# concurrent consumer edit\n')
        with self.assertRaises(TaskRunError):
            restarted.receive_dependency(manifest)
        consumer_source.write_bytes(consumer_original)
        self.assertEqual('delivered', tick(restarted))
        self.assertTrue(restarted.status()['done'])
        self.assertEqual('delivered', tick(restarted))

    def test_paths_cannot_escape(self):
        for name in ('../secret', '/etc/passwd'):
            with self.assertRaises(ValueError):
                contained(self.root, name)
        (self.root / 'link').symlink_to('/tmp')
        with self.assertRaises(ValueError):
            contained(self.root, 'link/file')
