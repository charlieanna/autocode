import http.client
import json
import os
import subprocess
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(ROOT/'tools/dashboard'),str(ROOT/'tools')]
from agent_console import Console, Handler, LoopbackHTTPServer
from dashboard_setup import git


class SetupTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name).resolve()
  self.env=patch.dict(os.environ,{'AUTOCODE_HOME':str(self.root/'registry')});self.env.start();self.addCleanup(self.env.stop)
  self.runner=self.root/'doctor.py';self.report=self.root/'report.json'
  self.runner.write_text('import json,sys\nfrom pathlib import Path\nd=json.loads(Path(__file__).with_name("report.json").read_text())\nprint(json.dumps(d))\nraise SystemExit(0 if d.get("ok") else 1)\n')
  names=['python','psutil','git','engine:opencode','engine:codex','engine','workspace']
  self.report.write_text(json.dumps({'ok':False,'checks':[{'name':x,'status':'missing','detail':'SECRET_fixture_token','fix':'SECRET_fixture_token'} for x in names]}))
  self.console=Console([],self.runner,lambda:False,conversation_root=self.root/'dashboard/conversations',project_store_root=self.root/'dashboard')
  self.console._probe_conversation_transport=lambda *_: next((row for row in json.loads(self.report.read_text()).get('checks',[]) if row.get('name')=='engine:opencode'),{'status':'warn'})
  self.addCleanup(self.console.pool.shutdown,wait=True);self.addCleanup(self.console.conversations.close,wait=True)
  self.server=LoopbackHTTPServer(('127.0.0.1',0),Handler);self.server.console=self.console;self.server.hosts={'127.0.0.1:'+str(self.server.server_port)}
  threading.Thread(target=self.server.serve_forever,kwargs={'poll_interval':.01},daemon=True).start();self.addCleanup(self.server.server_close);self.addCleanup(self.server.shutdown)
 def post(self,route,data,origin=None):
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=30)
  headers={'Content-Type':'application/json'}
  if origin:headers['Origin']=origin
  try:
   c.request('POST','/api/setup/'+route,json.dumps(data),headers);r=c.getresponse();return r.status,json.loads(r.read())
  finally:c.close()
 def request(self,name='new-project',mode='new',ident='setup-test-123'):
  return {'mode':mode,'path':str(self.root/name),'request_id':ident}
 def test_missing_dependencies_are_data_not_transport_error_and_no_secret_leaks(self):
  code,data=self.post('check',{});self.assertEqual(202,code,data)
  self.assertTrue(all(row['status']=='missing' for row in data['checks'] if row['id']!='opencode-accounts'))
  self.assertNotIn('SECRET_fixture_token',json.dumps(data));self.assertIsNone(data['workspace'])
  self.assertFalse((self.root/'dashboard/project-setup-requests.json').exists())
 def test_credential_urls_refuse_before_doctor_catalogue_or_model_work(self):
  value='https://user:SECRET_fixture_token@example.invalid/model'
  with patch.object(self.console,'_setup_doctor',side_effect=AssertionError('No doctor')),patch.object(self.console.conversation_catalogue,'fetch',side_effect=AssertionError('No catalogue')),patch.object(self.console.conversations.new,'provider',side_effect=AssertionError('No model')):
   for role in ('glm','plan_reviewer','astra','terra','sol','completion'):
    code,data=self.post('check',{'models':{role+'_model':value}})
    self.assertEqual(400,code,data);self.assertNotIn('SECRET_fixture_token',json.dumps(data))

 def test_transport_major_cache_refusal_recheck_and_cheap_empty_project(self):
  from autocode_planner_routes import MANDATED_ROUTES
  full=sorted({route['model'] for route in MANDATED_ROUTES.values()})
  with patch.object(self.console.conversation_catalogue,'fetch',return_value={'usable':True,'models':full}):
   for version in ('2.0.20','3.0.0+SECRET-fixture-token'):
    self.console.conversation_transport_readiness({'status':'ok','data':{'version':version}})
    ready=self.console.model_catalogue()['conversation_readiness']
    self.assertFalse(ready['usable']);self.assertEqual('unsupported',ready['transport']);self.assertNotIn('SECRET-fixture-token',json.dumps(ready));self.assertEqual(version.split('+')[0],ready['version'])
    with self.assertRaisesRegex(ValueError,'OpenCode 1.x'):self.console.conversation_models({})
   self.assertEqual('unsupported',self.console.conversation_transport_readiness({'status':'warn'})['transport'])
   self.console.conversation_transport_readiness({'status':'ok','data':{'version':'1.18.33'}})
   self.assertTrue(self.console.conversation_model_readiness({})['usable'])
  with patch.object(self.console.conversation_catalogue,'fetch',side_effect=AssertionError('No catalogue')),patch.object(self.console,'_probe_conversation_transport',side_effect=AssertionError('No probe')):
   self.assertEqual([],self.console.conversation_create({'empty':True,'request_id':'cheap-empty'})['messages'])
   code,data=self.post('project',self.request());self.assertEqual(202,code,data)
  with patch.object(self.console,'enqueue',return_value={'command':[]}) as enqueue:
   self.console.create({'project':data['project'],'goal':'ordinary CLI task'})
   enqueue.assert_called_once()

 def test_transport_probe_and_doctor_capture_are_bounded_and_safe(self):
  from agent_console import ModelCatalogue
  from dashboard_setup import SetupMixin
  captures=[]
  def fail(capture):
   captures.append((capture.command,capture.timeout,capture.output_limit));raise subprocess.TimeoutExpired(capture.command,capture.timeout,output='SECRET_fixture_token')
  with patch.object(ModelCatalogue,'_read_command',fail):
   row=SetupMixin._probe_conversation_transport(self.console,'/fixture/opencode')
   self.assertEqual('unknown',__import__('dashboard_setup').conversation_transport_signal(row)['transport'])
   self.assertNotIn('SECRET_fixture_token',json.dumps(row))
   self.assertEqual({'checks':[]},self.console._setup_doctor(self.root,'opencode'))
  self.assertEqual((3,4096),captures[0][1:]);self.assertEqual((90,262144),captures[1][1:])

 def test_transport_cache_uses_executable_identity_and_expires(self):
  executable=self.root/'fake-opencode';executable.write_text('first version')
  clock=[100.0]
  with patch('dashboard_setup.doctor.shutil.which',return_value=str(executable)),patch('dashboard_setup.time.monotonic',side_effect=lambda:clock[0]),patch.object(self.console,'_probe_conversation_transport',side_effect=[{'status':'ok','data':{'version':'2.0.20'}},{'status':'warn'},{'status':'ok','data':{'version':'1.18.33'}},{'status':'missing'}]) as probe:
   self.assertEqual('unsupported',self.console.conversation_transport_readiness()['transport'])
   self.assertEqual('unsupported',self.console.conversation_transport_readiness({'status':'warn'})['transport'])
   self.assertEqual('unsupported',self.console.conversation_transport_readiness()['transport']);self.assertEqual(1,probe.call_count)
   clock[0]+=301
   self.assertEqual('unsupported',self.console.conversation_transport_readiness()['transport']);self.assertEqual(2,probe.call_count)
   replacement=self.root/'replacement';replacement.write_text('different executable');replacement.replace(executable)
   self.assertEqual('available',self.console.conversation_transport_readiness()['transport']);self.assertEqual(3,probe.call_count)
   clock[0]+=301
   self.assertEqual('missing',self.console.conversation_transport_readiness()['transport']);self.assertEqual(4,probe.call_count)
 def test_open_code_accounts_never_falsely_report_connected(self):
  self.report.write_text(json.dumps({'ok':True,'checks':[{'name':'engine:opencode','status':'ok'}]}))
  code,data=self.post('check',{});self.assertEqual(202,code)
  self.assertEqual('unverified',next(x for x in data['checks'] if x['id']=='opencode-accounts')['status'])
  self.assertEqual('unknown',next(x for x in data['checks'] if x['id']=='python')['status'])
 def test_new_project_committed_and_scoped_conversation_without_model_call(self):
  with patch.object(self.console.conversations.new,'provider',side_effect=AssertionError('No provider spend')):
   code,data=self.post('project',self.request());self.assertEqual(202,code,data)
  project=Path(data['project']);self.assertEqual(str(project),data['conversation']['project_workspace'])
  self.assertEqual([],data['conversation']['messages']);self.assertFalse(data['conversation'].get('attachment'))
  self.assertEqual(0,git(project,'rev-parse','--verify','HEAD').returncode)
  self.assertEqual('',git(project,'status','--porcelain').stdout.strip())
  self.assertEqual('',git(project,'ls-tree','--name-only','HEAD').stdout.strip())
  code,replay=self.post('project',self.request());self.assertEqual(202,code,replay)
  self.assertEqual(data['conversation']['id'],replay['conversation']['id']);self.assertTrue(replay['replayed'])
 def test_setup_project_is_visible_before_any_run_and_after_restart(self):
  _,data=self.post('project',self.request());project=Path(data['project'])
  self.assertIn(project,self.console.workspaces)
  restarted=Console([],self.runner,lambda:False,conversation_root=self.root/'dashboard/conversations',project_store_root=self.root/'dashboard')
  self.addCleanup(restarted.pool.shutdown,wait=True);self.addCleanup(restarted.conversations.close,wait=True)
  self.assertIn(project,restarted.workspaces)
  self.assertEqual(data['conversation']['id'],restarted.conversations.list()[0]['id'])
  moved=self.root/'preserved-project';project.rename(moved);project.mkdir();(project/'.git').mkdir()
  self.assertNotIn(project,restarted.workspaces,'A replacement folder is not the authorized setup project')
  self.assertTrue((moved/'.git').exists())
 def test_dirty_existing_project_refused_with_no_file_or_index_changes(self):
  _,data=self.post('project',self.request());p=Path(data['project']);(p/'keep.txt').write_text('human work')
  before=git(p,'status','--porcelain').stdout
  code,error=self.post('project',self.request(mode='existing',ident='other-request'));self.assertEqual(400,code,error)
  self.assertIn('uncommitted',error['error']);self.assertEqual('human work',(p/'keep.txt').read_text());self.assertEqual(before,git(p,'status','--porcelain').stdout)
 def test_existing_clean_project_starts_separate_conversation(self):
  _,first=self.post('project',self.request());code,data=self.post('project',self.request(mode='existing',ident='another-request'))
  self.assertEqual(202,code,data);self.assertFalse(data['created']);self.assertNotEqual(first['conversation']['id'],data['conversation']['id'])
 def test_existing_new_path_and_reused_request_for_other_path_refused(self):
  (self.root/'new-project').mkdir();(self.root/'new-project/keep').write_text('keep')
  self.assertEqual(400,self.post('project',self.request())[0]);self.assertEqual('keep',(self.root/'new-project/keep').read_text())
  self.assertEqual(202,self.post('project',self.request('separate'))[0]);self.assertEqual(400,self.post('project',self.request('different'))[0]);self.assertFalse((self.root/'different').exists())
 def test_partial_initialization_preserved_and_safe_retry(self):
  from dashboard_setup import git as original
  def refuse(project,*args):
   if args[:2]==('init','-q'):return subprocess.CompletedProcess(args,1,'','controlled refusal')
   return original(project,*args)
  with patch('dashboard_setup.git',side_effect=refuse):
   code,error=self.post('project',self.request());self.assertEqual(400,code,error)
  self.assertTrue((self.root/'new-project').is_dir());self.assertEqual(202,self.post('project',self.request())[0])
 def test_changed_partial_folder_is_preserved(self):
  with patch('dashboard_setup.git',side_effect=ValueError('controlled failure')):
   self.assertEqual(400,self.post('project',self.request())[0])
  (self.root/'new-project/human-work').write_text('preserve')
  code,error=self.post('project',self.request());self.assertEqual(400,code,error);self.assertEqual('preserve',(self.root/'new-project/human-work').read_text())
 def test_cross_origin_and_credential_fields_refused(self):
  self.assertEqual(403,self.post('project',self.request(),origin='http://outside.example')[0])
  self.assertEqual(400,self.post('project',{**self.request(),'api_key':'SECRET_fixture_token'})[0])
  self.assertFalse((self.root/'new-project').exists())
  self.assertEqual(400,self.post('check',{'api_key':'SECRET_fixture_token'})[0])
 def test_symlink_traversal_and_invalid_parent_refused(self):
  (self.root/'linked').symlink_to(self.root,target_is_directory=True)
  for path in [str(self.root/'linked/new'),str(self.root/'parent/../new'),str(self.root/'missing/new'),'relative']:
   code,_=self.post('project',{**self.request(),'path':path});self.assertEqual(400,code,path)
 def test_invalid_doctor_reply_is_not_ready(self):
  self.report.write_text(json.dumps({'ok':True}))
  code,error=self.post('check',{});self.assertEqual(400,code,error);self.assertIn('supported setup',error['error'])

 def test_model_settings_use_existing_authority_and_bind_replay(self):
  request={**self.request(), 'models':{'terra_reasoning_effort':'high'}}
  code,data=self.post('project',request);self.assertEqual(202,code,data)
  self.assertEqual('high',data['conversation']['models']['terra_reasoning_effort'])
  self.assertEqual('openai/gpt-6-sol',data['conversation']['configured_routes']['requirements_gatherer']['model'])
  code,error=self.post('project',{**request,'models':{'terra_reasoning_effort':'max'}});self.assertEqual(400,code,error)
  self.assertTrue(Path(data['project']).exists())
 def test_model_credentials_and_invalid_effort_never_create_folder(self):
  for models in [{'api_key':'SECRET_fixture_token'},{'terra_reasoning_effort':'secret'},{'terra_model':'SECRET_fixture_token'}]:
   code,error=self.post('project',{**self.request(),'models':models});self.assertEqual(400,code,error)
   self.assertFalse((self.root/'new-project').exists())
  for saved in (self.root/'dashboard').rglob('*.json'):
   self.assertNotIn('SECRET_fixture_token',saved.read_text())
 def test_codex_check_does_not_require_opencode(self):
  code,data=self.post('check',{'engine':'codex'});self.assertEqual(202,code,data)
  self.assertNotIn('opencode-accounts',[row['id'] for row in data['checks']])
  self.assertNotIn('engine:opencode',[row['id'] for row in data['checks']])

 def raw_request(self,method,route,data=None):
  c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=10)
  try:
   c.request(method,route,None if data is None else json.dumps(data),{'Content-Type':'application/json'});r=c.getresponse();return r.status,json.loads(r.read())
  finally:c.close()

 def test_removed_empty_scoped_chat_is_hidden_and_refuses_every_dispatch(self):
  _,saved=self.post('project',self.request());ident=saved['conversation']['id'];project=saved['project']
  self.assertEqual(202,self.raw_request('POST','/api/projects',{'action':'remove','workspace':project})[0])
  self.assertNotIn(ident,[d['id'] for d in self.console.dashboard_snapshot()['conversations']])
  self.assertTrue(self.console.conversation_get(ident)['project_removed'])
  with patch.object(self.console.conversations.new,'provider',side_effect=AssertionError('No dispatch from removed project')):
   for route in ['message','retry','refresh-draft']:
    code,error=self.raw_request('POST','/api/conversation/'+route,{'id':ident,'text':'keep working','request_id':'removed-request'})
    self.assertEqual(400,code,error);self.assertIn('removed',error['error'])
  self.assertEqual([],self.console.conversations.get(ident)['messages'])
  self.assertEqual(400,self.post('project',self.request())[0])
  self.assertEqual(202,self.raw_request('POST','/api/projects',{'action':'restore','workspace':project})[0])
  self.assertIn(ident,[d['id'] for d in self.console.dashboard_snapshot()['conversations']])
  self.console.require_unarchived_conversation(ident)

 def test_replaced_setup_folder_refuses_replay_and_chat_dispatch_without_losing_history(self):
  _,saved=self.post('project',self.request());ident=saved['conversation']['id'];project=Path(saved['project'])
  original=self.root/'original-saved';project.rename(original);project.mkdir();(project/'.git').mkdir();(project/'keep').write_text('replacement')
  code,error=self.post('project',self.request());self.assertEqual(400,code,error);self.assertIn('replaced',error['error'])
  view=self.console.conversation_get(ident);self.assertEqual('error',view['status']);self.assertIn('replaced',view['project_scope_error'])
  for route in ['message','retry','refresh-draft']:
   code,error=self.raw_request('POST','/api/conversation/'+route,{'id':ident,'text':'continue','request_id':'replaced-request'})
   self.assertEqual(400,code,error);self.assertIn('replaced',error['error'])
  self.assertEqual([],self.console.conversations.get(ident)['messages']);self.assertEqual('replacement',(project/'keep').read_text());self.assertTrue((original/'.git').exists())
  replacement=self.root/'replacement-preserved';project.rename(replacement);original.rename(project)
  self.assertEqual(202,self.post('project',self.request())[0]);self.assertFalse(self.console.conversation_get(ident).get('project_scope_error'))

 def test_legacy_existing_setup_chat_requires_explicit_exact_folder_confirmation(self):
  _,first=self.post('project',self.request())
  request=self.request(mode='existing',ident='legacy-existing')
  _,saved=self.post('project',request);ident=saved['conversation']['id']
  # Fault fixture: reproduce the on-disk pre-upgrade format, never a real chat.
  receipt_path=self.root/'dashboard/project-setup-requests.json'
  receipts=json.loads(receipt_path.read_text());receipts['legacy-existing'].pop('identity');receipt_path.write_text(json.dumps(receipts))
  doc_path=self.root/'dashboard/conversations/continuous'/(ident+'.json')
  old=json.loads(doc_path.read_text());old.pop('_project_identity');doc_path.write_text(json.dumps(old))
  code,replay=self.post('project',request);self.assertEqual(202,code,replay)
  view=self.console.conversation_get(ident);confirmation=view['project_scope_confirmation']
  self.assertEqual(saved['project'],confirmation['workspace'])
  self.assertEqual(400,self.raw_request('POST','/api/conversation/message',{'id':ident,'text':'Do not dispatch','request_id':'blocked'})[0])
  self.assertEqual([],self.console.conversations.get(ident)['messages'])
  self.assertEqual(400,self.raw_request('POST','/api/conversation/confirm-scope',{'id':ident,'token':'stale'})[0])
  with patch.object(self.console.conversations.new,'provider',side_effect=AssertionError('Confirmation is not dispatch')):
   code,confirmed=self.raw_request('POST','/api/conversation/confirm-scope',{'id':ident,'token':confirmation['token']})
  self.assertEqual(202,code,confirmed);self.assertFalse(confirmed.get('project_scope_error'))
  self.assertEqual([],confirmed['messages']);self.console.require_unarchived_conversation(ident)
  self.assertEqual(202,self.post('project',request)[0])

 def test_legacy_confirmation_refuses_folder_changed_since_display_and_removed_project(self):
  _,saved=self.post('project',self.request());ident=saved['conversation']['id'];project=Path(saved['project'])
  receipt_path=self.root/'dashboard/project-setup-requests.json';receipts=json.loads(receipt_path.read_text());receipts['setup-test-123'].pop('identity');receipt_path.write_text(json.dumps(receipts))
  doc_path=self.root/'dashboard/conversations/continuous'/(ident+'.json');old=json.loads(doc_path.read_text());old.pop('_project_identity');doc_path.write_text(json.dumps(old))
  token=self.console.conversation_get(ident)['project_scope_confirmation']['token']
  project.rename(self.root/'preserved-original');project.mkdir();(project/'.git').mkdir()
  code,error=self.raw_request('POST','/api/conversation/confirm-scope',{'id':ident,'token':token})
  self.assertEqual(400,code,error);self.assertIn('changed',error['error'])
  latest=self.console.conversation_get(ident)['project_scope_confirmation']['token']
  self.assertEqual(202,self.raw_request('POST','/api/projects',{'action':'remove','workspace':str(project)})[0])
  self.assertEqual(400,self.raw_request('POST','/api/conversation/confirm-scope',{'id':ident,'token':latest})[0])
  self.assertTrue((self.root/'preserved-original/.git').exists())

if __name__=='__main__':unittest.main(verbosity=2)
