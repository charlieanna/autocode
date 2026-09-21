import json,os,sys,time,threading,signal
from pathlib import Path

sys.dont_write_bytecode=True
root=Path(__file__).parent
sys.path.insert(0,'/Users/ankurkothari/Documents/workspace/agent-console')
from agent_console import Console,Handler,ThreadingHTTPServer
workspace=root/'project';(workspace/'.git').mkdir(parents=True,exist_ok=True)
os.environ['AUTOCODE_HOME']=str(root/'isolated-home')
base={'workspace':str(workspace),'iteration':1,'next_stage':'astra_discovery','intervention_capability':{'supported':True,'version':1},'settings':{'engine':'opencode','roles':{'astra':{'model':'fixture/astra'},'terra':{'model':'fixture/terra'},'sol':{'model':'fixture/sol'}}}}
states={
 'question':{'task':'Question audit','status':'WAITING_FOR_USER','pending_questions':[{'id':'Q1','question':'Which output should we build?','why':'Choose the deliverable.','options':['CLI','Browser'],'proposed_default':'CLI'}]},
 'approval':{'task':'Approval audit','status':'AWAITING_GOAL_APPROVAL','displayed_goal':'r1:fixture-token','goal_contract':{'revision':1,'hash':'fixture-token','approval_status':'draft','body':{'intended_outcome':'Test typing and reviewing a draft.','acceptance_criteria':[]}}},
 'complete':{'task':'Stale completion audit','status':'TASK_COMPLETE','validation':{'criterion_results':[{'id':'C1','status':'PASS'}]},'acceptance_criteria':[{'id':'C1','criterion':'Current source verified'}]},
 'recovery':{'task':'Recovery audit','status':'PAUSED_PROVIDER_UNCERTAIN','attempt_id':'001/terra-01','stop_reason':'Inspect the interrupted attempt before continuing.','active_stage':{'stage':'terra','events':str(root/'events.jsonl'),'output':str(root/'result.json')}},
}
for name,state in states.items():
 run=workspace/'.autocode/runs'/name;run.mkdir(parents=True,exist_ok=True)
 (run/'state.json').write_text(json.dumps({**base,**state}))
(root/'control.json').write_text('{}')
class AuditHandler(Handler):
 def do_GET(self):
  control=json.loads((root/'control.json').read_text())
  delay=control.get('list_delay',0) if self.path=='/api/runs' else control.get('detail_delay',0) if self.path.startswith('/api/run?') else 0
  if delay:
   with (root/'requests.jsonl').open('a') as f:f.write(json.dumps({'path':self.path.split('?')[0],'at':time.time(),'delay':delay})+'\n')
   time.sleep(delay)
  return super().do_GET()
console=Console([workspace],root/'fake_runner.py',lambda:None,catalogue_command=(sys.executable,'-c','print("fixture/astra\\nfixture/terra\\nfixture/sol")'))
server=ThreadingHTTPServer(('127.0.0.1',0),AuditHandler);server.console=console;server.hosts={'127.0.0.1:'+str(server.server_port)}
threading.Thread(target=server.serve_forever,daemon=True).start()
metadata={'url':'http://127.0.0.1:'+str(server.server_port),'workspace':str(workspace),'pid':os.getpid()}
(root/'server.json').write_text(json.dumps(metadata));print(json.dumps(metadata),flush=True)
event=threading.Event();signal.signal(signal.SIGTERM,lambda *_:event.set());event.wait()
server.shutdown();console.pool.shutdown(wait=True)
