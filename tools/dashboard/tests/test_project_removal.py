"""Project removal changes dashboard visibility only; fake runners never launch models."""
import http.client
import json
from pathlib import Path
import threading
import unittest
from unittest.mock import patch

from test_chat_bridge import ChatFixture
from agent_console import Console, Handler, ThreadingHTTPServer


class ProjectRemovalFixture(ChatFixture):
    def setUp(self):
        super().setUp()
        self.run = self.workspace / '.autocode/runs/existing'
        self.run.mkdir(parents=True)
        self.state = {'workspace': str(self.workspace), 'task': 'Keep my work', 'status': 'RUNNING',
                      'active_stage': {'stage': 'terra', 'pid': 1234}, 'pending_questions': [],
                      'settings': {'engine': 'opencode', 'joint_planning': True}}
        (self.run / 'state.json').write_text(json.dumps(self.state))
        (self.workspace / 'important.txt').write_text('Never delete this')

    def project_files(self):
        return {str(p.relative_to(self.workspace)): p.read_bytes() for p in self.workspace.rglob('*') if p.is_file()}

    def remove(self):
        return self.console.project_action({'action': 'remove', 'workspace': str(self.workspace)})

    def restore(self):
        return self.console.project_action({'action': 'restore', 'workspace': str(self.workspace)})


class ProjectRemovalTests(ProjectRemovalFixture, unittest.TestCase):
    def test_remove_survives_restart_and_new_runs_without_touching_project(self):
        before = self.project_files()
        self.assertEqual(1, len(self.console.dashboard_snapshot()['runs']))
        self.assertTrue(self.remove()['removed'])
        self.assertTrue(self.remove()['removed'])
        self.assertEqual(before, self.project_files())
        self.assertEqual([], self.commands())
        newer = self.run.with_name('later-run')
        newer.mkdir()
        (newer / 'state.json').write_text(json.dumps(self.state))
        fresh = self.make_console()
        snapshot = fresh.dashboard_snapshot()
        self.assertEqual([], snapshot['runs'])
        self.assertEqual([], snapshot['workspaces'])
        self.assertEqual(str(self.workspace), snapshot['removed_projects'][0]['workspace'])
        self.assertFalse(fresh.project_action({'action': 'restore', 'workspace': str(self.workspace)})['removed'])
        self.assertEqual(2, len(fresh.dashboard_snapshot()['runs']))
        self.assertEqual([], self.commands())

    def test_attached_conversation_is_hidden_and_restored_with_history(self):
        doc = self.create_conversation('Preserve this planning discussion')
        self.console.conversations.update(doc['id'], attachment={
            'status': 'linked', 'workspace': str(self.workspace), 'run': str(self.run)})
        before = self.console.conversation_get(doc['id'])['messages']
        self.remove()
        self.assertEqual([], self.console.conversation_list())
        self.assertEqual([], self.console.dashboard_snapshot()['conversations'])
        saved = self.console.conversation_get(doc['id'])
        self.assertTrue(saved['project_removed'])
        self.assertEqual(before, saved['messages'])
        self.restore()
        self.assertEqual(doc['id'], self.console.conversation_list()[0]['id'])
        self.assertEqual(before, self.console.conversation_get(doc['id'])['messages'])

    def test_unknown_project_is_rejected_and_alias_removes_canonical_project(self):
        with self.assertRaisesRegex(ValueError, 'not connected'):
            self.console.project_action({'action': 'remove', 'workspace': str(self.root/'unrelated')})
        alias = self.root/'alias'
        alias.symlink_to(self.workspace, target_is_directory=True)
        self.console.project_action({'action': 'remove', 'workspace': str(alias)})
        self.assertEqual(str(self.workspace), self.console.removed_projects()[0]['workspace'])
        self.assertTrue(self.console.removed_project(alias))
        self.assertEqual([], self.console.dashboard_snapshot()['runs'])

    def test_removal_does_not_pause_or_kill_inflight_work(self):
        before = self.project_files()
        self.console.workspace_busy.add(str(self.workspace))
        self.console.pending.add(str(self.run))
        self.remove()
        self.assertIn(str(self.workspace), self.console.workspace_busy)
        self.assertIn(str(self.run), self.console.pending)
        self.assertEqual(before, self.project_files())
        self.assertEqual([], self.commands())

    def test_stale_task_links_are_restoreable_and_mutations_require_restore(self):
        self.remove()
        self.assertTrue(self.console.task_view(self.workspace,self.run)['project_removed'])
        for method, data in [
            (self.console.mutate, {'workspace':str(self.workspace),'run':str(self.run),'action':'continue'}),
            (self.console.chat, {'workspace':str(self.workspace),'run':str(self.run),'text':'Hidden task'}),
            (self.console.create, {'workspace':str(self.workspace),'goal':'New work'}),
            (self.console.create, {'project':' ', 'workspace':str(self.workspace),'goal':'New work'}),
            (self.console.create, {'project':123, 'workspace':str(self.workspace),'goal':'New work'}),
            (self.console.conversation_attach, {'workspace':str(self.workspace),'id':'irrelevant'})]:
            with self.subTest(method=method.__name__), self.assertRaisesRegex(ValueError,'Restore'):
                method(data)
        self.assertEqual([], self.commands())

    def test_registry_and_watch_roots_cannot_rediscover_removed_project(self):
        from test_registry_interventions import FAKE
        self.fake.write_text(FAKE)
        registry = {'operation':'list','registry_version':1,
                    'workspaces':[{'workspace':str(self.workspace)}],
                    'runs':[{'workspace':str(self.workspace),'run_dir':str(self.run),'availability':'available'}]}
        (self.root/'registry.json').write_text(json.dumps(registry))
        (self.root/'status.json').write_text(json.dumps({'interventions':{}}))
        (self.root/'inbox.json').write_text('[]')
        before = (self.root/'registry.json').read_bytes()
        self.remove()
        fresh = Console([],self.fake,lambda:None,watch_roots=[self.root],
                        conversation_root=self.root/'dashboard/conversations',conversation_provider=self.provider)
        self.addCleanup(fresh.pool.shutdown,wait=True)
        self.addCleanup(lambda: fresh._conversation_store.close() if fresh._conversation_store else None)
        self.assertEqual([],fresh.dashboard_snapshot()['runs'])
        self.assertEqual([],fresh.discover())
        self.assertEqual(before,(self.root/'registry.json').read_bytes())
        fresh.project_action({'action':'restore','workspace':str(self.workspace)})
        self.assertEqual(1,len(fresh.dashboard_snapshot()['runs']))


class ProjectRemovalHttpTests(ProjectRemovalFixture, unittest.TestCase):
    def test_http_remove_restore_origin_and_missing_folder(self):
        server = ThreadingHTTPServer(('127.0.0.1',0),Handler)
        server.console = self.console
        server.hosts = {'127.0.0.1:'+str(server.server_port)}
        thread = threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        def request(method,path,payload=None,origin=None):
            connection=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=10)
            headers={'Content-Type':'application/json'}
            if origin:headers['Origin']=origin
            connection.request(method,path,json.dumps(payload) if payload is not None else None,headers)
            response=connection.getresponse();result=(response.status,json.loads(response.read()));connection.close();return result
        payload={'action':'remove','workspace':str(self.workspace)}
        loop=self.root/'loop';loop.symlink_to(loop)
        self.assertEqual(400,request('POST','/api/projects',{'action':'remove','workspace':str(loop)})[0])
        self.assertEqual(400,request('POST','/api/projects',{'action':'remove','workspace':'~missing-autocode-test-user/project'})[0])
        self.assertEqual(403,request('POST','/api/projects',payload,'https://example.invalid')[0])
        self.assertEqual([],self.console.removed_projects())
        self.assertEqual(202,request('POST','/api/projects',payload)[0])
        self.assertEqual([],request('GET','/api/runs')[1]['runs'])
        # A removed link remains restorable even if its folder is unavailable.
        from urllib.parse import urlencode
        with patch.object(self.console,'workspace_for',side_effect=AssertionError('must not read removed folder')):
            status,data=request('GET','/api/run?'+urlencode({'workspace':str(self.workspace),'run':str(self.run)}))
        self.assertEqual(200,status);self.assertTrue(data['project_removed'])
        self.assertEqual(202,request('POST','/api/projects',{'action':'restore','workspace':str(self.workspace)})[0])
        self.assertEqual(1,len(request('GET','/api/runs')[1]['runs']))

if __name__=='__main__':unittest.main()
