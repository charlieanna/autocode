"""Per-task archives preserve workers, history, and sibling visibility."""
import json
import unittest
from unittest.mock import patch
from test_project_removal import ProjectRemovalFixture


class TaskArchiveTests(ProjectRemovalFixture, unittest.TestCase):
    def archive(self, action='archive'):
        return self.console.task_archive_action({'workspace':str(self.workspace),'run':str(self.run),'action':action})

    def test_archive_restore_persistence_and_siblings(self):
        sibling=self.run.with_name('sibling');sibling.mkdir()
        (sibling/'state.json').write_text(json.dumps(self.state))
        before=self.project_files()
        self.console.pending.add(str(self.run))
        self.assertTrue(self.archive()['archived']);self.archive()
        snapshot=self.console.dashboard_snapshot()
        self.assertEqual([str(sibling)],[r['run'] for r in snapshot['runs']])
        self.assertIn(str(self.workspace),snapshot['workspaces'])
        self.assertEqual(str(self.run),snapshot['archived_tasks'][0]['run'])
        self.assertEqual(before,self.project_files())
        self.assertIn(str(self.run),self.console.pending)
        self.assertEqual([],self.commands())
        fresh=self.make_console()
        self.assertTrue(fresh.archived_task(self.run))
        saved=json.loads(fresh.task_archive.path.read_text())
        self.assertEqual({'version','archived'},set(saved))
        self.assertFalse(self.archive('restore')['archived'])
        self.assertEqual(2,len(fresh.dashboard_snapshot()['runs']))

    def test_conversation_history_and_mutation_guards(self):
        doc=self.create_conversation('Keep this conversation')
        self.console.conversations.update(doc['id'],attachment={'status':'linked','workspace':str(self.workspace),'run':str(self.run)})
        before=self.console.conversation_get(doc['id'])['messages']
        self.archive()
        self.assertEqual([],self.console.conversation_list())
        self.assertTrue(self.console.conversation_get(doc['id'])['task_archived'])
        self.assertTrue(self.console.task_view(self.workspace,self.run)['task_archived'])
        for method,data in [(self.console.mutate,{'run':str(self.run),'workspace':str(self.workspace),'action':'continue'}),(self.console.chat,{'run':str(self.run),'text':'hello'})]:
            with self.assertRaisesRegex(ValueError,'Restore'):method(data)
        with self.assertRaisesRegex(ValueError,'Restore'):
            self.console.require_unarchived_conversation(doc['id'])
        self.archive('restore')
        self.assertEqual(before,self.console.conversation_get(doc['id'])['messages'])
        self.assertEqual(1,len(self.console.conversation_list()))

    def test_missing_folder_restore_and_project_independence(self):
        self.archive();self.remove()
        with patch.object(self.console,'workspace_for',side_effect=AssertionError('No filesystem lookup')):
            self.assertFalse(self.archive('restore')['archived'])
        self.assertEqual([],self.console.dashboard_snapshot()['runs'])
        self.restore()
        self.assertEqual(1,len(self.console.dashboard_snapshot()['runs']))

    def test_invalid_target_and_corrupt_archive_are_rejected(self):
        with self.assertRaises(ValueError):
            self.console.task_archive_action({'workspace':str(self.workspace),'run':str(self.root/'unknown'),'action':'archive'})
        self.archive();self.console.task_archive.path.write_text('{broken')
        with self.assertRaisesRegex(ValueError,'archived-tasks.json'):self.archive('restore')
        self.assertEqual('{broken',self.console.task_archive.path.read_text())
