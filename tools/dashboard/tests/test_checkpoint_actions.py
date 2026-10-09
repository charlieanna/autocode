"""Real public checkpoint commands, with dashboard visibility boundaries."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

TOOLS = Path(__file__).resolve().parents[2]
ROOT = TOOLS.parent
sys.path[:0] = [str(ROOT), str(TOOLS), str(TOOLS / 'dashboard')]
import autocode_registry as registry
from agent_console import Console

from tests import test_code_checkpoints as checkpoint_fixtures


class CheckpointActions(unittest.TestCase):
    def setUp(self):
        self.fixture=checkpoint_fixtures.CodeCheckpoints();self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        f=self.fixture
        env=patch.dict(os.environ,{'AUTOCODE_HOME':str(f.root/'registry')});env.start();self.addCleanup(env.stop)
        registry.register_run(f.workspace,f.run,f.state)
        self.console=Console([f.workspace],TOOLS/'autocode.py',lambda:False,conversation_root=f.root/'dashboard/conversations',project_store_root=f.root/'dashboard')
        self.addCleanup(lambda:self.console.pool.shutdown(wait=True))
        self.payload={'workspace':str(f.workspace),'run':str(f.run),'checkpoint_id':f.ident}

    def test_real_adapter_restores_from_exact_comparison_and_rejects_extra_flags(self):
        compared=self.console.checkpoint_action(self.payload)
        self.assertIn('checkpoint-v1:',compared['expected_token'])
        with self.assertRaisesRegex(ValueError,'Unexpected'):
            self.console.checkpoint_action({**self.payload,'args':['--approve-goal','anything']})
        result=self.console.checkpoint_action({**self.payload,'expected_token':compared['expected_token'],'request_id':'dashboard-owned-restore'},restore=True)
        self.assertEqual('PAUSED_REQUESTED',result['status'])
        self.assertTrue(Path(result['run_dir']).is_dir())
        self.assertEqual('checkpoint code\n',(self.fixture.workspace/'app.txt').read_text())

    def test_archived_removed_unregistered_and_stale_refuse_without_mutation(self):
        compared=self.console.checkpoint_action(self.payload)
        for archive in (True,False):
            if archive:self.console.task_archive.remove(str(self.fixture.run))
            else:self.console.project_preferences.remove(str(self.fixture.workspace))
            with self.assertRaisesRegex(ValueError,'[Rr]estore|[Rr]emoved|[Aa]rchived'):
                self.console.checkpoint_action(self.payload)
            if archive:self.console.task_archive.restore(str(self.fixture.run))
            else:self.console.project_preferences.restore(str(self.fixture.workspace))
        (self.fixture.workspace/'app.txt').write_text('new change')
        with self.assertRaisesRegex(ValueError,'source changed'):
            self.console.checkpoint_action({**self.payload,'expected_token':compared['expected_token'],'request_id':'dashboard-stale-restore'},restore=True)
        self.assertFalse((self.fixture.workspace/'.autocode/worktrees').exists())
        with self.assertRaises(ValueError):
            self.console.checkpoint_action({**self.payload,'run':'/unregistered/run'})
