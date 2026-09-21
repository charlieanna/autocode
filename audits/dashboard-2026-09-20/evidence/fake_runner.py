import json, sys, time
from pathlib import Path

args=sys.argv[1:]
def value(flag,default=None):return args[args.index(flag)+1] if flag in args else default
if args[:2]==['registry','location']:
 print(json.dumps({'registry_version':1,'operation':'location','registry_path':'isolated-audit-registry'}))
elif args[:2]==['registry','list']:
 print(json.dumps({'registry_version':1,'operation':'list','workspaces':[],'runs':[]}))
elif '--status' in args:
 s=json.loads((Path(value('--run-dir'))/'state.json').read_text())
 print(json.dumps({'status':s.get('status'),'attempt_id':s.get('attempt_id'),'completion_current':False,
  'interventions':{'inspector_capability':{'supported':True,'version':1},'runner_capability':{'supported':True,'version':1},'blocked_conditions':[]}}))
elif args[:2]==['intervention','inspect']:
 print(json.dumps({'version':1,'operation':'inspect','requests':[]}))
elif '--run-dir' not in args:
 workspace=Path(value('--workspace'));goal=args[args.index('--workspace')+2]
 run=workspace/'.autocode/runs'/('created-'+str(time.time_ns()));run.mkdir(parents=True)
 state={'workspace':str(workspace),'task':goal,'phase':'DISCOVERING','status':'RUNNING','iteration':1,'next_stage':'astra_discovery',
   'settings':{'engine':value('--engine','opencode')},'intervention_capability':{'supported':True,'version':1}}
 (run/'state.json').write_text(json.dumps(state))
 time.sleep(1)
 if goal=='Create failure audit':
  print('Fixture provider failed before it could ask a question.',file=sys.stderr);raise SystemExit(1)
 state.update(status='WAITING_FOR_USER',pending_questions=[{'id':'Q1','question':'Which outcome?','proposed_default':'CLI'}])
 (run/'state.json').write_text(json.dumps(state));print('Fixture created');raise SystemExit(2)
else:
 print('Fixture action recorded; no provider launched.')
