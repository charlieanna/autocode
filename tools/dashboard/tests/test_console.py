import http.client,json,os,subprocess,sys,tempfile,threading,time,unittest
from unittest.mock import patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]))
from agent_console import Console,Handler,ThreadingHTTPServer,configured_zai
class Tests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();isolation=patch.dict(os.environ,{'AUTOCODE_HOME':str(Path(self.tmp.name)/'registry-home')});isolation.start();self.addCleanup(isolation.stop);self.ws=Path(self.tmp.name)/'w';(self.ws/'.git').mkdir(parents=True);self.run=self.ws/'.autocode/runs/r';self.run.mkdir(parents=True);self.fake=Path(self.tmp.name)/'fake.py';self.fake.write_text('import sys\nprint("o"*3000);print("e"*3000,file=sys.stderr)')
  self.state={'task':'<unsafe>','phase':'WAITING_FOR_USER','status':'WAITING_FOR_USER','iteration':1,'active_stage':{'stage':'terra'},'pending_questions':[{'id':'Q1','question':'what','options':['a'],'proposed_default':'a','why':'why'}],'answers':{'Q0':{'text':'saved'}},'displayed_goal':'r3:abc','displayed_review':'review','goal_contract':{'body':{'intended_outcome':'out','acceptance_criteria':[{'id':'C1','criterion':'one'},{'id':'C11','criterion':'eleven','human_review':True}]}},'initial_plan':['approved first step'],'initial_plan_approval':'approved','plan':['current plan\nwith recorded line break'],'current_task':{'id':'current','objective':'current <unsafe>\nobjective','requirements':['current requirement'],'validation_plan':['current validation'],'assigned_at':'2026-09-19T22:14:47Z','contract_revision':3,'decision':'CONTINUE','owner':'Terra','next_role':'Sol'},'task_archive':[{'id':'older','objective':'older assignment','requirements':['old requirement'],'validation_plan':['old validation'],'assigned_at':'2026-09-19T20:00:00Z','contract_revision':1,'decision':'CONTINUE'},'malformed'],'decisions':[{'at':'2026-09-19T20:00:00Z','current_task':{'id':'older','objective':'older assignment','requirements':['old requirement'],'validation_plan':['old validation'],'assigned_at':'2026-09-19T20:00:00Z','contract_revision':1,'decision':'CONTINUE'}},{'at':'2026-09-19T21:00:00Z','current_task':{'id':'rework','objective':'rework assignment','contract_revision':2,'decision':'REWORK'},'reason':'recorded rationale\nonly'}],'stages':[{'stage':'astra','duration_seconds':2,'metrics':{'provider_tokens':{'input_tokens':3}}}],'validation':{'criterion_results':[{'id':'C1','status':'PASS'}]},'user_request':{'question':'choose'}};(self.run/'state.json').write_text(json.dumps(self.state));self.c=Console([self.ws],self.fake,lambda:'ZAI')
 def tearDown(self):self.tmp.cleanup()
 def wait(self,run=True):
  for _ in range(300):
   x=self.c.action_log(self.ws,self.run if run else None)
   if x and x[-1]['status'] not in ('queued','running'):return x[-1]
   time.sleep(.01)
  self.fail('timed out')
 def test_v3_adapter(self):
   v=self.c.view(self.ws,self.run);self.assertEqual('saved',v['answers']['Q0']['text']);self.assertEqual('terra',v['stage']);self.assertEqual(1,len(v['stages']));self.assertEqual(1,v['counts']['pass']);self.assertEqual(['current plan\nwith recorded line break'],v['plan']);self.assertEqual('review',v['review_token'])
 def test_completion_timestamp_projection_is_optional_and_not_derived(self):
  completion='2026-09-22T12:44:00Z';polling='2026-09-22T13:07:00Z';stage='2026-09-22T12:48:00Z';evidence='2026-09-22T12:51:00Z'
  self.state.update(status='TASK_COMPLETE',completed_at=completion,updated_at=polling,stages=[{'stage':'sol','role':'sol','finished_at':stage,'exit_code':0}],validation={'recorded_at':evidence,'criterion_results':[{'id':'C1','status':'PASS'}]})
  view=self.c.view(self.ws,self.run,self.state);self.assertEqual(completion,view['completed_at']);self.assertNotIn(polling,[view['completed_at']]);self.assertNotIn(stage,[view['completed_at']]);self.assertNotIn(evidence,[view['completed_at']])
  self.state['completed_at']='not-a-completion-timestamp';self.assertEqual('not-a-completion-timestamp',self.c.view(self.ws,self.run,self.state)['completed_at'])
  self.state.pop('completed_at');self.assertIsNone(self.c.view(self.ws,self.run,self.state)['completed_at'])
 def test_astra_plan_history_is_defensive_chronological_and_deduplicated(self):
   self.state['goal_contract']['approval_status']='approved';self.state['contract_history']=[{'revision':1,'approval_status':'proposed','created_at':'2026-09-19T19:00:00Z'},{'revision':2,'approval_status':'approved','approval_event':{'at':'2026-09-19T20:00:00Z'}}];(self.run/'state.json').write_text(json.dumps(self.state));history=self.c.view(self.ws,self.run)['astra_plan'];self.assertEqual(['approved first step'],history['initial_plan']);self.assertEqual('approved',history['initial_plan_approval']);self.assertEqual('approved',history['current_plan_approval']);self.assertEqual([1,2],[entry['revision'] for entry in history['revision_history']]);self.assertEqual(['current plan\nwith recorded line break'],history['current_plan']);self.assertEqual('current',history['current_assignment']['id']);self.assertEqual(['older','rework','current'],[entry['id'] for entry in history['history']]);self.assertEqual('recorded rationale\nonly',history['history'][1]['reason']);self.assertIsNone(history['history'][0]['owner'])
   self.state['task_archive']=[None,{'id':['malformed']}];self.state['current_task']='legacy';self.state['plan']='legacy';(self.run/'state.json').write_text(json.dumps(self.state));history=self.c.view(self.ws,self.run)['astra_plan'];self.assertEqual([],history['current_plan']);self.assertIsNone(history['current_assignment'])
 def test_plan_history_and_responsive_controls_are_rendered_from_escaped_text(self):
   from agent_console import APP,STYLE
   self.assertIn("function renderAstraPlan(run)",APP);self.assertIn("textContent = text ?? ''",APP);self.assertIn("Initial approved plan (recorded)",APP);self.assertIn("Initial historically approved plan (inactive)",APP);self.assertIn("historically approved/inactive",APP);self.assertIn("awaiting its own approval",APP);self.assertIn("Initial plan (approval unavailable)",APP);self.assertIn("Current plan approval status",APP);self.assertIn("not proof of completion",APP);self.assertIn("No current assigned step is recorded.",APP);self.assertIn("planLines(lines)",APP);self.assertIn("#create select,#create input,#watch-root input{display:block;width:100%;max-width:100%;min-width:0}",STYLE)
 def test_waiting_request_fields_are_preserved(self):
  request={'decision_needed':'Choose a safety boundary','discovered':'runner lock exists','impact':'cannot continue','options':['wait','use another workspace'],'proposed_delta':'defer continuation'}
  self.state['user_request']=request;(self.run/'state.json').write_text(json.dumps(self.state));self.assertEqual(request,self.c.view(self.ws,self.run)['user_request'])
 def test_discovery_summary_and_author_follow_saved_successful_planning_stage(self):
  self.state.update(settings={'joint_planning':True},discovery_summary='The draft needs a decision.',goal_contract={'origin':'glm_draft'},stages=[{'stage':'astra_discovery','role':'glm','exit_code':0}])
  def view():return self.c.view(self.ws,self.run,self.state)
  self.assertEqual('The draft needs a decision.',view()['discovery_summary']);self.assertEqual('glm',view()['discovery_role'])
  self.state['stages'].append({'stage':'astra_challenge','role':'astra','exit_code':0});self.assertEqual('astra',view()['discovery_role'])
  self.state['stages'].append({'stage':'glm_revise','role':'glm','exit_code':0,'rejected':True});self.assertEqual('astra',view()['discovery_role'])
  self.state['stages'].append({'stage':'glm_revise','role':'glm','exit_code':1});self.assertEqual('astra',view()['discovery_role'])
  self.state['stages'].append({'stage':'glm_revise_report_repair','original_stage':'glm_revise','role':'glm','exit_code':0,'applied_original_events':'saved-events'});self.assertEqual('glm',view()['discovery_role'])
  self.state['stages']=[];self.state['goal_contract']['origin']='astra_finalize';self.assertEqual('astra',view()['discovery_role'])
  self.state['goal_contract']['origin']='glm_revise';self.assertEqual('glm',view()['discovery_role'])
  self.state['goal_contract']={};self.state['discovery_summary']={'malformed':True};self.assertIsNone(view()['discovery_role']);self.assertEqual('',view()['discovery_summary'])
 def test_symlinked_runs_root_outside_watched_workspace_is_rejected(self):
  external=Path(self.tmp.name)/'external-runs';escaped=external/'escaped';escaped.mkdir(parents=True);(escaped/'state.json').write_text(json.dumps(self.state))
  watched=Path(self.tmp.name)/'watched';(watched/'.git').mkdir(parents=True);(watched/'.autocode').mkdir();(watched/'.autocode/runs').symlink_to(external,target_is_directory=True)
  console=Console([watched],self.fake,lambda:'ZAI')
  self.assertIsNone(console.run_for(console.workspaces[0],str(escaped)))
  rows=console.discover();self.assertEqual(1,len(rows));self.assertIn('escapes watched workspace',rows[0]['error'])
  with self.assertRaises(ValueError):console.mutate({'workspace':str(watched),'run':str(escaped),'action':'continue'})
 def test_selected_run_disappearance_api_and_ui_invalidation_regression(self):
  s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.console=self.c;s.hosts={'127.0.0.1:'+str(s.server_port)};threading.Thread(target=s.serve_forever,daemon=True).start()
  def get():
   h=http.client.HTTPConnection('127.0.0.1',s.server_port);h.request('GET','/api/run?workspace='+str(self.ws).replace('/','%2F')+'&run='+str(self.run).replace('/','%2F'));reply=h.getresponse();status=reply.status;body=json.loads(reply.read());h.close();return status,body
  try:
   self.assertEqual(200,get()[0]);(self.run/'state.json').unlink();self.assertEqual(404,get()[0]);(self.run/'state.json').write_text('{');status,body=get();self.assertEqual(200,status);self.assertIn('state_error',body)
   from unittest.mock import patch
   with patch('pathlib.Path.read_text',side_effect=PermissionError('denied')):
    status,body=get();self.assertEqual(200,status);self.assertIn('state_error',body)
   (self.run/'state.json').write_text(json.dumps(self.state));self.assertEqual(200,get()[0])
  finally:s.shutdown();s.server_close()
  from agent_console import APP
  self.assertIn("function unavailableRun(message)",APP);self.assertIn("$('#continue').disabled=true",APP);self.assertIn("$('#continue').disabled=false",APP);self.assertIn("unavailableRun('Selected run unavailable: '+run.state_error)",APP);self.assertIn("unavailableRun('Selected run unavailable: '+error.message)",APP);self.assertIn("selected=currentView==='task-detail'?chosen:null",APP)
 def test_actions_exact_and_no_implicit_continue(self):
  x=self.c.mutate({'workspace':str(self.ws),'run':str(self.run),'action':'answer','id':'Q1','text':'hi'});self.assertIn('Q1=hi',x['command']);self.wait();self.assertEqual(1,len(self.c.action_log(self.ws,self.run)))
  self.c.mutate({'workspace':str(self.ws),'run':str(self.run),'action':'delegate','id':'Q1'});self.wait();x=self.c.mutate({'workspace':str(self.ws),'run':str(self.run),'action':'approve_goal','token':'r3:abc','confirmation':'r3:abc'});self.assertIn('--approve-goal',x['command']);self.wait();x=self.c.mutate({'workspace':str(self.ws),'run':str(self.run),'action':'approve_review','id':'C11','token':'review'});self.assertEqual(['--approve-review','C11','--review-token','review'],x['command'][-4:]);self.wait()
  with self.assertRaises(ValueError):self.c.mutate({'workspace':str(self.ws),'run':str(self.run),'action':'approve_goal','token':'stale','confirmation':'stale'})
 def test_explicit_codex_provider_and_capture_serialization(self):
  p=Path(self.tmp.name)/'c.toml';p.write_text('# zai\n[model_providers.ZAI]\nx=1\n');self.assertEqual('ZAI',configured_zai(p));p.write_text('# [model_providers.ZAI]\n');self.assertIsNone(configured_zai(p));x=self.c.create({'workspace':str(self.ws),'goal':'x','engine':'codex','astra_model':'glm-5.3'});self.assertIn('--astra-provider',x['command']);self.assertIn('--astra-reasoning-effort',x['command']);self.wait(False)
  self.c.mutate({'workspace':str(self.ws),'run':str(self.run),'action':'continue'})
  with self.assertRaises(ValueError):self.c.mutate({'workspace':str(self.ws),'run':str(self.run),'action':'continue'})
  x=self.wait();self.assertEqual(0,x['exit_status']);self.assertGreater(len(x['stdout']),1000);self.assertGreater(len(x['stderr']),1000)
 def test_http_security(self):
  s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.console=self.c;authority='127.0.0.1:'+str(s.server_port);s.hosts={authority};threading.Thread(target=s.serve_forever,daemon=True).start()
  def post(headers=None,missing_host=False):
   body=json.dumps({'workspace':str(self.ws),'run':str(self.run),'action':'continue'});h=http.client.HTTPConnection('127.0.0.1',s.server_port)
   if missing_host:
    h.putrequest('POST','/api/action',skip_host=True);h.putheader('Content-Length',str(len(body)));h.putheader('Content-Type','application/json');h.endheaders(body.encode())
   else:h.request('POST','/api/action',body,headers or {})
   response=h.getresponse();status=response.status;response.read();h.close();return status
  try:
   h=http.client.HTTPConnection('127.0.0.1',s.server_port);h.request('GET','/api/run?workspace='+str(self.ws).replace('/','%2F')+'&run='+str(self.run).replace('/','%2F'));reply=h.getresponse();self.assertEqual(200,reply.status);reply.read();h.close()
   for headers,missing_host in [({'Host':'evil.example'},False),({'Host':authority,'Origin':'https://'+authority},False),({'Host':authority,'Origin':'null'},False),({'Host':authority,'Origin':'http://evil.example'},False),({'Host':authority,'Origin':'http://'+authority+'/path'},False),({},True)]:
    self.assertEqual(403,post(headers,missing_host));self.assertEqual([],self.c.action_log(self.ws,self.run))
   self.assertEqual(202,post({'Host':authority,'Origin':'http://'+authority,'Content-Type':'application/json'}));self.wait()
  finally:s.shutdown();s.server_close()
 def test_task_chat_has_one_composer_and_explicit_question_selection(self):
  from agent_console import APP,INDEX
  self.assertEqual(1,INDEX.count('id="change-text"'))
  self.assertEqual(1,INDEX.count('id="question-target"'))
  self.assertNotIn('question-form',APP)
  self.assertIn("api('/api/chat'",APP)
  self.assertIn("question_id",APP)
  self.assertIn("focus.version !== focusVersion",APP)
  self.assertIn("setSelectionRange(focus.start, focus.end, focus.direction)",APP)
 def test_composer_drafts_are_scoped_and_polling_has_no_overlapping_interval(self):
  from agent_console import APP
  self.assertIn("persist('task-draft:'+input.dataset.run,input.value)",APP)
  self.assertIn("stored('task-draft:'+run.run)",APP)
  self.assertIn("persist('conversation-draft:'+event.target.dataset.conversation,event.target.value)",APP)
  self.assertIn("setTimeout(refresh,2000)",APP)
  self.assertNotIn("setInterval(refresh",APP)
 def make_ws(self,path):
  ws=Path(path);(ws/'.git').mkdir(parents=True);run=ws/'.autocode/runs/r';run.mkdir(parents=True);(run/'state.json').write_text(json.dumps(self.state));return ws
 def wait_done(self,c,ws,run=None):
  for _ in range(300):
   x=c.action_log(ws,run)
   if x and x[-1]['status'] not in ('queued','running'):return x[-1]
   time.sleep(.01)
  self.fail('timed out')
 def test_watch_root_depth_discovery_lists_labeled_rows_and_excludes_deeper(self):
  root=Path(self.tmp.name).resolve()/'tree';d1=self.make_ws(root/'d1');d3=self.make_ws(root/'a'/'b'/'d3');d4=self.make_ws(root/'a'/'b'/'c'/'d4');ng=root/'nogit';(ng/'.autocode/runs/r').mkdir(parents=True);(ng/'.autocode/runs/r'/'state.json').write_text(json.dumps(self.state))
  c=Console([],self.fake,lambda:'ZAI',watch_roots=[root]);rows=c.discover();run_rows=[r for r in rows if r.get('run')]
  self.assertEqual({str(d1),str(d3)},{r['workspace'] for r in run_rows})
  self.assertEqual(str(d1),next(r['workspace'] for r in run_rows if r['run']==str(d1/'.autocode/runs/r')))
  self.assertEqual(str(d3),next(r['workspace'] for r in run_rows if r['run']==str(d3/'.autocode/runs/r')))
  flat=str(rows);self.assertNotIn(str(d4),flat);self.assertNotIn(str(ng),flat)
  c1=Console([],self.fake,lambda:'ZAI',watch_roots=[root],watch_depth=1)
  self.assertEqual({str(d1)},{r['workspace'] for r in c1.discover() if r.get('run')})
  s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.console=c;s.hosts={'127.0.0.1:'+str(s.server_port)};threading.Thread(target=s.serve_forever,daemon=True).start()
  try:
   h=http.client.HTTPConnection('127.0.0.1',s.server_port);h.request('GET','/api/runs');reply=h.getresponse();payload=json.loads(reply.read());h.close()
   self.assertIn(str(d1),payload['workspaces']);self.assertIn(str(d3),payload['workspaces']);self.assertNotIn(str(d4),payload['workspaces']);self.assertNotIn(str(ng),payload['workspaces'])
   labeled={r['run']:r['workspace'] for r in payload['runs'] if r.get('run')};self.assertEqual({str(d1/'.autocode/runs/r'):str(d1),str(d3/'.autocode/runs/r'):str(d3)},labeled)
  finally:s.shutdown();s.server_close()
 def test_watch_root_itself_is_eligible_workspace(self):
  root=self.make_ws(Path(self.tmp.name).resolve()/'rootws');c=Console([],self.fake,lambda:'ZAI',watch_roots=[root])
  rows=[r for r in c.discover() if r.get('run')];self.assertEqual([str(root)],sorted(r['workspace'] for r in rows));self.assertEqual([str(root/'.autocode/runs/r')],[r['run'] for r in rows])
 def test_discovery_stops_at_git_project_and_skips_generated_trees(self):
  root=Path(self.tmp.name).resolve()/'projects';project=self.make_ws(root/'project')
  nested=self.make_ws(project/'node_modules'/'dependency'/'nested-project')
  generated=self.make_ws(project/'.autocode'/'worktrees'/'generated-project')
  c=Console([],self.fake,lambda:'ZAI',watch_roots=[root],watch_depth=8,watch_ttl=0)
  rows=[r for r in c.discover() if r.get('run')]
  self.assertEqual([str(project)],[r['workspace'] for r in rows])
  self.assertNotIn(str(nested),str(rows));self.assertNotIn(str(generated),str(rows))
 def test_task_list_snapshot_omits_bulky_stage_internals(self):
  self.state['active_stage'].update(processes=[{'pid':number,'detail':'x'*200} for number in range(1000)])
  self.state['stages'][0]['provider_payload']='y'*1000000
  (self.run/'state.json').write_text(json.dumps(self.state))
  row=self.c.dashboard_snapshot()['runs'][0]
  self.assertNotIn('processes',row['active_stage']);self.assertNotIn('provider_payload',row['stages'][0])
  self.assertEqual('terra',row['active_stage']['stage']);self.assertEqual('astra',row['stages'][0]['stage'])
  self.assertLess(len(json.dumps(row)),50000)
  detail=self.c.task_view(self.ws,self.run)
  self.assertIn('processes',detail['active_stage']);self.assertIn('provider_payload',detail['stages'][0])
 def test_one_discovery_reuses_one_process_table_snapshot(self):
  self.state['active_stage']['pid']=123
  (self.run/'state.json').write_text(json.dumps(self.state))
  other=self.make_ws(Path(self.tmp.name)/'other');other_state={**self.state,'active_stage':{**self.state['active_stage'],'pid':456}}
  (other/'.autocode/runs/r/state.json').write_text(json.dumps(other_state))
  c=Console([self.ws,other],self.fake,lambda:'ZAI')
  with patch('dashboard_backend.monitor_process_table',return_value=None) as inspect:
   rows=[row for row in c.discover() if row.get('run')]
  self.assertEqual(2,len(rows));inspect.assert_called_once_with()
 def test_dashboard_snapshot_pins_one_watch_root_scan(self):
  root=Path(self.tmp.name).resolve()/'projects';self.make_ws(root/'project')
  c=Console([],self.fake,lambda:'ZAI',watch_roots=[root],watch_depth=3,watch_ttl=0)
  with patch.object(c,'_scan_watch_root',wraps=c._scan_watch_root) as inspect:
   rows=[row for row in c.dashboard_snapshot()['runs'] if row.get('run')]
  self.assertEqual(1,len(rows));inspect.assert_called_once_with(root)
 def test_watch_root_symlink_escape_rejected_like_explicit(self):
  base=Path(self.tmp.name).resolve();root=base/'wroot';root.mkdir();external=self.make_ws(base/'external')
  (root/'link').symlink_to(external,target_is_directory=True)
  ext_runs=base/'ext-runs';esc=ext_runs/'esc';esc.mkdir(parents=True);(esc/'state.json').write_text(json.dumps(self.state))
  inside=root/'inside';(inside/'.git').mkdir(parents=True);(inside/'.autocode').mkdir();(inside/'.autocode/runs').symlink_to(ext_runs,target_is_directory=True)
  c=Console([],self.fake,lambda:'ZAI',watch_roots=[root]);rows=c.discover()
  self.assertNotIn(str(external),str(rows))
  err=[r for r in rows if r.get('workspace')==str(inside)];self.assertEqual(1,len(err));self.assertIn('escapes watched workspace',err[0]['error'])
  with self.assertRaises(ValueError):c.mutate({'workspace':str(inside),'run':str(esc),'action':'continue'})
  with self.assertRaises(ValueError):c.mutate({'workspace':str(root/'link'),'run':str(esc),'action':'continue'})
 def test_watch_root_overlap_and_duplicate_explicit_dedupe(self):
  root=Path(self.tmp.name).resolve()/'r1';(root/'nested').mkdir(parents=True);shared=self.make_ws(root/'nested'/'shared')
  c=Console([shared,shared],self.fake,lambda:'ZAI',watch_roots=[root,root/'nested'])
  run_rows=[r for r in c.discover() if r.get('run')];self.assertEqual(1,len(run_rows));self.assertEqual(str(shared),run_rows[0]['workspace'])
  self.assertEqual(1,[str(w) for w in c.workspaces].count(str(shared)))
  run=shared/'.autocode/runs/r';c.mutate({'workspace':str(shared),'run':str(run),'action':'continue'})
  with self.assertRaises(ValueError) as ctx:c.mutate({'workspace':str(shared),'run':str(run),'action':'continue'})
  self.assertIn('already queued or running',str(ctx.exception));self.wait_done(c,shared,run)
 def test_discovered_workspace_supports_create_and_actions(self):
  root=Path(self.tmp.name).resolve()/'dw';dw=self.make_ws(root/'disc');c=Console([],self.fake,lambda:'ZAI',watch_roots=[root])
  x=c.create({'workspace':str(dw),'goal':'build it'});self.assertEqual(['--workspace',str(dw)],x['command'][2:4]);self.assertEqual(['build it','--engine','opencode','--joint-planning','--no-chat'],x['command'][4:]);self.assertEqual(0,self.wait_done(c,dw)['exit_status'])
  run=dw/'.autocode/runs/r';y=c.mutate({'workspace':str(dw),'run':str(run),'action':'continue'});self.assertEqual([sys.executable,c.runner,'--workspace',str(dw),'--run-dir',str(run),'--no-chat'],y['command']);self.wait_done(c,dw,run)
 def test_opencode_creation_accepts_entered_git_worktree_and_preserves_actions(self):
  external=Path(self.tmp.name).resolve()/'external';external.mkdir();(external/'.git').write_text('gitdir: /tmp/worktree')
  fake=Path(self.tmp.name)/'creator.py';fake.write_text("import json,sys\nfrom pathlib import Path\na=sys.argv[1:];w=Path(a[a.index('--workspace')+1]);r=w/'.autocode/runs/new';r.mkdir(parents=True,exist_ok=True);defaults={'glm':'zai-coding-plan/glm-5.3','astra':'gpt-6-astra','terra':'zai-coding-plan/glm-5.3','sol':'gpt-5.6-sol'};assert not any(x.endswith('-model') for x in a);(r/'state.json').write_text(json.dumps({'task':'new','phase':'WAITING_FOR_USER','models':defaults}));(w/'argv.json').write_text(json.dumps(a))")
  c=Console([],fake,lambda:None);x=c.create({'project':str(external),'goal':'make it','engine':'opencode'});self.assertEqual([sys.executable,str(fake.resolve()),'--workspace',str(external),'make it','--engine','opencode','--joint-planning','--no-chat'],x['command']);self.assertNotIn('--reasoning-effort',x['command']);self.assertFalse(any('provider' in arg for arg in x['command']));self.wait_done(c,external,False)
  self.assertEqual({'glm':'zai-coding-plan/glm-5.3','astra':'gpt-6-astra','terra':'zai-coding-plan/glm-5.3','sol':'gpt-5.6-sol'},json.loads((external/'.autocode/runs/new/state.json').read_text())['models']);self.assertEqual([str(external)],[str(w) for w in c.workspaces]);self.assertEqual([str(external/'.autocode/runs/new')],[r['run'] for r in c.discover() if r.get('run')]);y=c.mutate({'workspace':str(external),'run':str(external/'.autocode/runs/new'),'action':'continue'});self.assertEqual([sys.executable,str(fake.resolve()),'--workspace',str(external),'--run-dir',str(external/'.autocode/runs/new'),'--no-chat'],y['command']);self.wait_done(c,external,external/'.autocode/runs/new')
 def test_create_rejects_non_git_or_missing_entered_project_without_authority(self):
  c=Console([],self.fake,lambda:None);missing=Path(self.tmp.name)/'missing';plain=Path(self.tmp.name)/'plain';plain.mkdir()
  for path in (missing,plain):
   with self.assertRaisesRegex(ValueError,'existing Git workspace'):c.create({'project':str(path),'goal':'x','engine':'opencode'})
  with self.assertRaisesRegex(ValueError,'existing Git workspace'):c.create({'project':['not','a','path'],'goal':'x','engine':'opencode'})
  self.assertEqual([],c.workspaces);self.assertFalse(missing.exists());self.assertEqual({},c.actions)
 def test_create_api_requires_same_origin_and_authorizes_only_valid_entered_project(self):
  external=Path(self.tmp.name).resolve()/'api-worktree';external.mkdir();(external/'.git').write_text('gitdir: /tmp/worktree');s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.console=Console([],self.fake,lambda:None);authority='127.0.0.1:'+str(s.server_port);s.hosts={authority};threading.Thread(target=s.serve_forever,daemon=True).start()
  def post(body,headers):
   h=http.client.HTTPConnection('127.0.0.1',s.server_port);h.request('POST','/api/create',json.dumps(body),headers);reply=h.getresponse();status=reply.status;reply.read();h.close();return status
  try:
   body={'project':str(external),'goal':'api goal','engine':'opencode'};self.assertEqual(403,post(body,{'Host':'evil.example','Content-Type':'application/json'}));self.assertEqual({},s.console.actions);self.assertEqual(202,post(body,{'Host':authority,'Origin':'http://'+authority,'Content-Type':'application/json'}));self.wait_done(s.console,external);self.assertIn(external,s.console.workspaces)
  finally:s.shutdown();s.server_close()
 def test_unavailable_watch_root_reports_error_row_only_for_that_root(self):
  base=Path(self.tmp.name).resolve();ok=self.make_ws(base/'ok');missing=base/'missing';bad=base/'bad';bad.mkdir();bad.chmod(0)
  c=Console([],self.fake,lambda:'ZAI',watch_roots=[missing,ok,bad])
  try:
   rows=c.discover();errs={r['workspace']:r['error'] for r in rows if 'error' in r and not r.get('run')}
   self.assertEqual({str(missing),str(bad)},set(errs))
   for e in errs.values():self.assertIn('watch root unavailable',e)
   self.assertEqual([str(ok)],[r['workspace'] for r in rows if r.get('run')])
  finally:bad.chmod(0o755)
 def test_discovery_cache_ttl_and_lazy_rescan(self):
  root=Path(self.tmp.name).resolve()/'troot';self.make_ws(root/'one');c=Console([],self.fake,lambda:'ZAI',watch_roots=[root],watch_ttl=60)
  self.assertEqual(1,len([r for r in c.discover() if r.get('run')]))
  self.make_ws(root/'two');self.assertEqual(1,len([r for r in c.discover() if r.get('run')]))
  c.watch_ttl=0;self.assertEqual(2,len([r for r in c.discover() if r.get('run')]))
 def test_cli_accepts_registry_only_empty_startup(self):
  proc,port=self._launch(['--port','0'])
  try:
   h=http.client.HTTPConnection('127.0.0.1',port);h.request('GET','/api/runs');payload=json.loads(h.getresponse().read());h.close();self.assertEqual([],payload['workspaces']);self.assertEqual([],payload['runs'])
  finally:proc.terminate();proc.wait(timeout=10);proc.stdout.close();proc.stderr.close()
 def test_cli_rejects_negative_watch_depth(self):
  here=Path(__file__).parents[1];r=subprocess.run([sys.executable,str(here/'agent_console.py'),'--workspace',str(self.ws),'--watch-depth','-1'],capture_output=True,text=True,timeout=30)
  self.assertNotEqual(0,r.returncode);self.assertIn('--watch-depth',r.stderr)
 def _launch(self,args):
  here=Path(__file__).parents[1];proc=subprocess.Popen([sys.executable,str(here/'agent_console.py'),'--runner',str(self.fake)]+args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  try:
   line=proc.stdout.readline()
   if not line.startswith('http://127.0.0.1:'):
    proc.kill();proc.wait(timeout=10);err=proc.stderr.read();proc.stdout.close();proc.stderr.close();self.fail('server did not start; stderr: '+err)
   return proc,int(line.strip().rsplit(':',1)[1])
  except:proc.kill();proc.wait(timeout=10);proc.stdout.close();proc.stderr.close();raise
 def test_cli_launches_with_watch_root_only_and_lists_discovered(self):
  base=Path(self.tmp.name).resolve();root=self.make_ws(base/'cliroot'/'ws');proc,port=self._launch(['--watch-root',str(base/'cliroot'),'--port','0'])
  try:
   h=http.client.HTTPConnection('127.0.0.1',port);h.request('GET','/api/runs');payload=json.loads(h.getresponse().read());h.close()
   self.assertEqual([str(root)],payload['workspaces'])
  finally:proc.terminate();proc.wait(timeout=10);proc.stdout.close();proc.stderr.close()
 def test_cli_workspace_only_launch_unchanged(self):
  proc,port=self._launch(['--workspace',str(self.ws),'--port','0'])
  try:
   h=http.client.HTTPConnection('127.0.0.1',port);h.request('GET','/api/runs');payload=json.loads(h.getresponse().read());h.close()
   self.assertEqual([str(self.ws.resolve())],payload['workspaces'])
  finally:proc.terminate();proc.wait(timeout=10);proc.stdout.close();proc.stderr.close()
class RuntimeWatchRootTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.base=Path(self.tmp.name).resolve();self.fake=self.base/'fake.py';self.fake.write_text('')
 def tearDown(self):self.tmp.cleanup()
 def workspace(self,path):
  (path/'.git').mkdir(parents=True);run=path/'.autocode/runs/r';run.mkdir(parents=True);(run/'state.json').write_text('{}');return path
 def test_runtime_roots_are_memory_only_and_preserve_explicit_sources(self):
  explicit=self.workspace(self.base/'explicit');runtime=self.workspace(self.base/'runtime'/'project');c=Console([explicit],self.fake,lambda:None,watch_roots=[])
  c.add_runtime_watch_root(str(runtime.parent));self.assertIn(str(runtime),[str(w) for w in c.workspaces])
  c.remove_runtime_watch_root(str(runtime.parent));self.assertNotIn(str(runtime),[str(w) for w in c.workspaces]);self.assertIn(str(explicit),[str(w) for w in c.workspaces])
  fresh=Console([explicit],self.fake,lambda:None);self.assertEqual([],fresh.watch_root_rows())
 def test_runtime_root_removal_preserves_overlapping_cli_discovery_and_protects_cli_root(self):
  cli_root=self.base/'cli';project=self.workspace(cli_root/'nested'/'project');runtime_root=cli_root/'nested';c=Console([],self.fake,lambda:None,watch_roots=[cli_root],watch_ttl=60)
  self.assertEqual([str(project)],[str(w) for w in c.workspaces])
  c.add_runtime_watch_root(str(runtime_root));self.assertEqual([str(project)],[str(w) for w in c.workspaces])
  with self.assertRaisesRegex(ValueError,'CLI watch roots cannot be removed'):
   c.remove_runtime_watch_root(str(cli_root))
  c.remove_runtime_watch_root(str(runtime_root))
  # Removing runtime provenance must retain the project found by the CLI root.
  self.assertEqual([str(project)],[str(w) for w in c.workspaces])
  self.assertEqual([{'path':str(cli_root),'runtime':False,'removable':False}],c.watch_root_rows())
 def test_runtime_root_api_rejects_hostile_origin_and_invalid_paths(self):
  cli_root=self.base/'cli-root';cli_root.mkdir();explicit=self.workspace(self.base/'explicit');c=Console([explicit],self.fake,lambda:None,watch_roots=[cli_root]);s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.console=c;authority='127.0.0.1:'+str(s.server_port);s.hosts={authority};threading.Thread(target=s.serve_forever,daemon=True).start();root=self.workspace(self.base/'runtime'/'project').parent
  def post(body,headers):
   h=http.client.HTTPConnection('127.0.0.1',s.server_port);h.request('POST','/api/watch-roots',json.dumps(body),headers);reply=h.getresponse();status=reply.status;reply.read();h.close();return status
  try:
   headers={'Host':authority,'Origin':'http://'+authority,'Content-Type':'application/json'}
   self.assertEqual(403,post({'action':'add','path':str(root)},{'Host':'evil.example','Content-Type':'application/json'}));self.assertEqual(400,post({'action':'add','path':str(root/'missing')},headers));self.assertEqual(400,post({'action':'remove','path':str(cli_root)},headers));self.assertEqual(400,post({'action':'remove','path':str(explicit)},headers));self.assertEqual(202,post({'action':'add','path':str(root)},headers));self.assertIn(str(root/'project'),[str(w) for w in c.workspaces]);self.assertEqual(202,post({'action':'remove','path':str(root)},headers));self.assertEqual([str(cli_root)],[row['path'] for row in c.watch_root_rows()])
  finally:s.shutdown();s.server_close()
 def test_runtime_root_api_removes_disappeared_source_by_returned_canonical_path(self):
  cli_root=self.base/'cli-root';project=self.workspace(cli_root/'project');c=Console([],self.fake,lambda:None,watch_roots=[cli_root],watch_ttl=0);s=ThreadingHTTPServer(('127.0.0.1',0),Handler);s.console=c;authority='127.0.0.1:'+str(s.server_port);s.hosts={authority};threading.Thread(target=s.serve_forever,daemon=True).start();root=self.base/'runtime-root';root.mkdir()
  def request(method,path,body=None):
   h=http.client.HTTPConnection('127.0.0.1',s.server_port);headers={'Host':authority,'Origin':'http://'+authority,'Content-Type':'application/json'};h.request(method,path,json.dumps(body) if body is not None else None,headers if body is not None else {'Host':authority});reply=h.getresponse();status=reply.status;payload=json.loads(reply.read());h.close();return status,payload
  try:
   status,added=request('POST','/api/watch-roots',{'action':'add','path':str(root)});self.assertEqual(202,status);canonical=added['path'];root.rmdir()
   _,before=request('GET','/api/runs');self.assertIn(canonical,{row['workspace'] for row in before['runs'] if row.get('error')});self.assertIn(str(project),before['workspaces'])
   status,removed=request('POST','/api/watch-roots',{'action':'remove','path':canonical});self.assertEqual(202,status);self.assertEqual(canonical,removed['path'])
   _,after=request('GET','/api/runs');self.assertNotIn(canonical,{row['workspace'] for row in after['runs'] if row.get('error')});self.assertEqual([str(cli_root)],[row['path'] for row in after['watch_roots']]);self.assertIn(str(project),after['workspaces']);self.assertEqual({},c.actions)
  finally:s.shutdown();s.server_close()
 def test_dashboard_renders_full_brief_and_real_newlines(self):
  from agent_console import APP,INDEX
  self.assertIn("function syncWorkspaces(list)",APP)
  self.assertIn("syncWorkspaces(data.workspaces || []);",APP)
  self.assertIn("if (keep && (list.includes(keep)||keep==='__custom__')) select.value=keep",APP)
  self.assertIn("renderDocument(brief)",APP);self.assertIn("lines.join('\\n')",APP);self.assertIn("Approve plan",APP);self.assertIn("confirmation:run.goal_token",APP);self.assertIn("renderDocument(run.criteria||[])",APP);self.assertIn("Technical details",APP);self.assertIn("Autocode dashboard",INDEX);self.assertIn("No project needed yet.",INDEX);self.assertIn("Attach a project",INDEX);self.assertIn("conversationPayload(text,models,conversationRequest.id)",APP)
 def test_literal_user_backslash_n_is_not_decoded_by_rendering(self):
  from agent_console import APP
  literal=r'first line\nsecond line';payload={'task':literal};self.assertEqual(literal,payload['task']);self.assertIn("element.textContent = text ?? ''",APP);self.assertNotIn("replaceAll('\\\\n'",APP)
if __name__=='__main__':unittest.main()
