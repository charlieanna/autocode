"""Conversation archives preserve history and do not change task visibility."""
import threading
import unittest
from test_project_removal import ProjectRemovalFixture


class ConversationArchiveTests(ProjectRemovalFixture, unittest.TestCase):
    def archive(self, doc, action='archive'):
        return self.console.conversation_archive({'id':doc['id'],'action':action})

    def test_persistence_restore_and_idempotent_creation(self):
        doc=self.create_conversation('Keep my idea')
        before=doc['messages'];archived=self.archive(doc)
        self.assertEqual(archived['archived_at'],self.archive(doc)['archived_at'])
        self.assertEqual([],self.console.conversation_list())
        fresh=self.make_console()
        self.assertEqual(doc['id'],fresh.dashboard_snapshot()['archived_conversations'][0]['id'])
        self.assertEqual(before,fresh.conversation_get(doc['id'])['messages'])
        duplicate=fresh.conversation_create({'text':'Keep my idea','request_id':'start-request'})
        self.assertEqual(doc['id'],duplicate['id'])
        self.assertEqual(1,len(self.provider_calls))
        self.archive(doc,'restore');self.archive(doc,'restore')
        self.assertEqual(doc['id'],fresh.conversation_list()[0]['id'])
        self.assertEqual([],fresh.dashboard_snapshot()['archived_conversations'])

    def test_archived_mutations_rejected(self):
        doc=self.create_conversation();self.archive(doc)
        for action in [lambda:self.console.conversations.send(doc['id'],'new text'),lambda:self.console.conversations.retry(doc['id']),lambda:self.console.conversation_attach({'id':doc['id'],'workspace':str(self.workspace)}),lambda:self.console.conversations.claim_attachment(doc['id'],{'status':'starting'})]:
            with self.assertRaisesRegex(ValueError,'Restore'):action()
        self.assertEqual([],self.commands())

    def test_linked_task_and_project_remain_independent(self):
        doc=self.create_conversation()
        self.console.conversations.update(doc['id'],attachment={'status':'linked','workspace':str(self.workspace),'run':str(self.run)})
        before=self.project_files();self.archive(doc)
        self.assertEqual(1,len(self.console.dashboard_snapshot()['runs']))
        self.assertEqual(before,self.project_files());self.assertEqual([],self.commands())
        self.remove();self.archive(doc,'restore')
        self.assertEqual([],self.console.conversation_list())
        self.restore();self.assertEqual(1,len(self.console.conversation_list()))

    def test_reply_in_flight_preserves_archive_and_new_message(self):
        started=threading.Event();release=threading.Event()
        def slow(*args):
            started.set();release.wait(5);return 'Saved late reply'
        self.console.conversations.provider=slow
        self.addCleanup(release.set)
        doc=self.console.conversation_create({'text':'Slow fixture reply','request_id':'slow-request'})
        self.assertTrue(started.wait(2));self.archive(doc);release.set()
        saved=self.ready(doc)
        self.assertTrue(saved['archived_at']);self.assertEqual('Saved late reply',saved['messages'][-1]['text'])
        self.assertEqual([],self.console.conversation_list())
        self.archive(doc,'restore');self.assertEqual('Saved late reply',self.console.conversation_get(doc['id'])['messages'][-1]['text'])

    def test_invalid_action_and_id_preserve_history(self):
        doc=self.create_conversation();before=self.console.conversations.get(doc['id'])
        for payload in [{'id':doc['id'],'action':'delete'},{'id':'../bad','action':'archive'}]:
            with self.assertRaises(ValueError):self.console.conversation_archive(payload)
        self.assertEqual(before,self.console.conversations.get(doc['id']))
