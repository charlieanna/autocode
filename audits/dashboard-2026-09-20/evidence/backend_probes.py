import json,sys,time
from pathlib import Path
from unittest.mock import patch
sys.dont_write_bytecode=True
sys.path.insert(0,'/Users/ankurkothari/Documents/workspace/agent-console')
from agent_console import Console
root=Path(__file__).parent;workspace=root/'probe-project';(workspace/'.git').mkdir(parents=True,exist_ok=True)
runner=root/'fake_runner.py';c=Console([workspace],runner,lambda:None)
results={}
with patch.object(c,'enqueue',side_effect=lambda ws,run,label,extra:extra):
 args=c.create({'workspace':str(workspace),'goal':'Test Codex selection','engine':'codex'})
 results['codex_create']={'arguments':args,'engine_flag_missing':'--engine' not in args}
try:c.workspace_for(None)
except Exception as e:results['null_workspace']={'exception':type(e).__name__,'message':str(e)}
bad=root/'invalid_utf8.py';bad.write_text('import os\nos.write(1,b"\\xff")\n')
x={'command':[sys.executable,str(bad)]};key='invalid-utf8';c.pending.add(key);c.workspace_busy.add(str(workspace))
try:c._execute(key,str(workspace),x)
except Exception as e:results['invalid_utf8']={'exception':type(e).__name__,'action_status':x['status'],'has_finished_at':'finished_at' in x,'pending_released':key not in c.pending}
run=workspace/'.autocode/runs/review';run.mkdir(parents=True,exist_ok=True)
state={'workspace':str(workspace),'status':'TASK_COMPLETE','task':'stale','validation':{'source_revision':'old','criterion_results':[{'id':'C1','status':'PASS'}]},'acceptance_criteria':[{'id':'C1','criterion':'current'}]}
(run/'state.json').write_text(json.dumps(state))
v=c.view(workspace,run);results['stale_completion']={'displayed_status':v['status'],'counts':v['counts'],'completion_current_exposed':'completion_current' in v}
c.pool.shutdown(wait=True)
(root/'backend-results.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
