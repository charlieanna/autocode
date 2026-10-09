"""Task lanes sequence shared-loop work and isolate parallel lanes."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools import autocode_tasks as tasks
from tools import test_subprocess


class TaskFlows(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        subprocess.run(['git', 'init', '-q', str(self.root)], check=True)
        subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Test', '-c',
                        'user.email=t@example.test', 'commit', '--allow-empty', '-qm', 'base'], check=True)

    def manifest(self, value):
        path = self.root / 'flow.json'
        path.write_text(json.dumps(value))
        return path

    def sample(self):
        return {'version': 1, 'name': 'Product flow', 'lanes': [
            {'id': 'app', 'tasks': [
                {'id': 'design', 'mode': 'ui', 'task': 'Design dashboard'},
                {'id': 'build', 'mode': 'code', 'task': 'Build dashboard', 'ui_from': 'design'}]},
            {'id': 'docs', 'tasks': [
                {'id': 'guide', 'mode': 'code', 'task': 'Write guide'}]}]}

    def test_ready_runs_one_task_per_lane_and_preserves_lane_order(self):
        path = self.manifest(self.sample())
        _, manifest = tasks.load_manifest(path)
        state = tasks.new_state(path, manifest, self.root)
        first = [(lane['id'], task['id']) for lane, task, _ in tasks.ready(manifest, state)]
        self.assertEqual([('app', 'design'), ('docs', 'guide')], first)
        state['lanes']['app']['tasks']['design']['status'] = 'COMPLETE'
        second = [(lane['id'], task['id']) for lane, task, _ in tasks.ready(manifest, state)]
        self.assertIn(('app', 'build'), second)
        self.assertIn(('docs', 'guide'), second)

    def test_ui_handoff_enters_next_code_task_in_the_same_workspace(self):
        path = self.manifest(self.sample()); _, manifest = tasks.load_manifest(path)
        state = tasks.new_state(path, manifest, self.root)
        lane = manifest['lanes'][0]; lane_state = state['lanes']['app']
        lane_state['workspace'] = str(self.root)
        ui_spec, code_spec = lane['tasks']
        ui_record, code_record = (lane_state['tasks'][item['id']] for item in lane['tasks'])

        def completed(command, **_kwargs):
            if command[2:3] == ['ui']:
                run = Path(command[command.index('--run-dir') + 1]); run.mkdir(parents=True)
                (run / 'state.json').write_text(json.dumps({'status': 'COMPLETE', 'figma_file': 'https://www.figma.com/design/Example123/App'}))
            else:
                run = self.root / '.autocode/runs/code'; run.mkdir(parents=True)
                (run / 'state.json').write_text(json.dumps({'status': 'AWAITING_GOAL_APPROVAL'}))
            return subprocess.CompletedProcess(command, 0, '', '')

        with patch.object(tasks.subprocess, 'run', side_effect=completed):
            tasks.launch_task(self.root, self.root / '.autocode/task-flows/x', lane, ui_spec, ui_record, lane_state)
            tasks.launch_task(self.root, self.root / '.autocode/task-flows/x', lane, code_spec, code_record, lane_state)
        self.assertEqual('COMPLETE', ui_record['status'])
        self.assertEqual('WAITING', code_record['status'])
        self.assertIn('--in-place', code_record['command'])
        self.assertEqual(ui_record['run_dir'], code_record['command'][code_record['command'].index('--ui-run') + 1])
        self.assertEqual(str(self.root), code_record['command'][code_record['command'].index('--workspace') + 1])

    def test_dry_run_validates_graph_without_creating_worktrees(self):
        path = self.manifest(self.sample())
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            self.assertEqual(0, tasks.cli([str(path), '--workspace', str(self.root), '--dry-run']))
        result = json.loads(output.getvalue())
        self.assertEqual('RUNNING', result['status'])
        self.assertEqual(['app', 'docs'], [lane['id'] for lane in result['lanes']])
        self.assertFalse((self.root / '.autocode').exists())

    def test_rejects_forward_or_cross_lane_ui_handoffs(self):
        value = self.sample(); value['lanes'][0]['tasks'][1]['ui_from'] = 'build'
        with self.assertRaisesRegex(ValueError, 'earlier task'):
            tasks.load_manifest(self.manifest(value))

    def test_ids_cannot_escape_the_flow_directory(self):
        value = self.sample(); value['lanes'][0]['id'] = '../outside'
        with self.assertRaisesRegex(ValueError, 'lane needs'):
            tasks.load_manifest(self.manifest(value))

    def test_cli_starts_parallel_lanes_as_normal_isolated_autocode_runs(self):
        flow = test_subprocess.SubprocessFlow(); flow.setUp(); self.addCleanup(flow.doCleanups)
        manifest = flow.root / 'parallel.json'
        manifest.write_text(json.dumps({'version': 1, 'name': 'Parallel fixture', 'lanes': [
            {'id': 'one', 'tasks': [{'id': 'first', 'mode': 'code', 'task': 'Build first greeting', 'engine': 'codex'}]},
            {'id': 'two', 'tasks': [{'id': 'second', 'mode': 'code', 'task': 'Build second greeting', 'engine': 'codex'}]}]}))
        env = {**flow.env, 'AUTOCODE_FIXTURE_MODE': 'no-human'}
        result = subprocess.run([*flow.entry, 'tasks', str(manifest), '--workspace', str(flow.project),
                                 '--max-parallel', '2'], cwd=flow.root, env=env,
                                capture_output=True, text=True, timeout=45)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        value = json.loads(result.stdout)
        self.assertEqual('WAITING', value['status'])
        self.assertEqual(2, len({lane['workspace'] for lane in value['lanes']}))
        for lane in value['lanes']:
            record = lane['tasks'][0]
            self.assertEqual('WAITING', record['status'])
            saved = json.loads((Path(record['run_dir']) / 'state.json').read_text())
            self.assertIn(saved['status'], ('WAITING_FOR_USER', 'AWAITING_GOAL_APPROVAL'))
            self.assertEqual(lane['workspace'], saved['workspace'])


if __name__ == '__main__':
    unittest.main()
