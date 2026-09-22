"""Multiple tasks queue independently; checkpoints remain tied to their worktree."""
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from agent_console import Console
from test_chat_bridge import ChatFixture


class ParallelLaunches(unittest.TestCase):
    def test_new_tasks_do_not_reserve_or_release_an_existing_checkout_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve(); (project / '.git').mkdir()
            console = Console([project], project / 'fake.py', lambda: None)
            self.addCleanup(console.pool.shutdown, wait=True)
            console.workspace_busy.add(str(project))
            with patch.object(console.pool, 'submit') as submit:
                left = console.create({'workspace': str(project), 'goal': 'One', 'engine': 'codex'})
                right = console.create({'workspace': str(project), 'goal': 'Two', 'engine': 'codex'})
            self.assertEqual(2, submit.call_count)
            self.assertNotEqual(submit.call_args_list[0].args[1], submit.call_args_list[1].args[1])
            self.assertNotEqual(left['id'], right['id'])
            self.assertNotIn('--in-place', left['command'])
            self.assertIn(str(project), console.workspace_busy)
            left['command'] = [sys.executable, '-c', 'print("done")']
            console._execute(submit.call_args_list[0].args[1], str(project), left)
            self.assertIn(str(project), console.workspace_busy)
            self.assertEqual(1, len(console.pending))


class WorktreeConversation(ChatFixture, unittest.TestCase):
    def test_restart_finds_the_task_in_its_isolated_workspace(self):
        doc = self.create_conversation()
        task = 'Saved project conversation'
        child = self.workspace / '.autocode/worktrees/task-123'
        (child / '.git').mkdir(parents=True)
        run = child / '.autocode/runs/one'; run.mkdir(parents=True)
        (child / '.autocode/task-workspace.json').write_text(json.dumps({
            'project_workspace': str(self.workspace), 'workspace': str(child)}))
        (run / 'state.json').write_text(json.dumps({'workspace': str(child), 'project_workspace': str(self.workspace),
                                                  'task': task, 'status': 'WAITING_FOR_USER'}))
        self.console.conversations.update(doc['id'], attachment={
            'workspace': str(self.workspace), 'status': 'starting', 'goal_hash': hashlib.sha256(task.encode()).hexdigest()})
        linked = self.make_console().conversation_get(doc['id'])['attachment']
        self.assertEqual('linked', linked['status'])
        self.assertEqual(str(child), linked['workspace'])
        self.assertEqual(str(self.workspace), linked['project_workspace'])
        self.assertEqual(str(run), linked['run'])
        self.assertEqual([], self.commands())


if __name__ == '__main__':
    unittest.main()
