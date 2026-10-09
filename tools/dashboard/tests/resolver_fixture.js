// Positive UI fixtures come from the real read-only server projection. Only
// this test setup publishes receipts; the shipped JS cannot mint authority.
const {execFileSync}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path');
const root=path.resolve(__dirname,'../../..');
const python=fs.existsSync(path.join(root,'.venv/bin/python'))?path.join(root,'.venv/bin/python'):'python3';
const setup=String.raw`
import copy,json,sys
from unittest.mock import patch
sys.path.insert(0,sys.argv[1])
from tools import autocode_resolver_human as human
scope,doc=json.load(sys.stdin)
state={'workspace':doc.get('workspace','/fixture/project'),'run_dir':doc.get('run','/fixture/run'),
       'task_id':'fixture-task','task':doc.get('task','Fixture task'),'status':doc.get('status','RUNNING'),
       'pending_questions':copy.deepcopy(doc.get('questions',[])), 'settings':{}}
contract=copy.deepcopy(doc.get('goal',{}))
contract.update(task_id=state['task_id'],revision=contract.get('revision',1),approval_status='draft' if scope in ('clarification','goal_approval') else 'approved')
body=contract.setdefault('body',{})
body['open_blocking_questions']=state['pending_questions'] if scope=='clarification' else []
criteria=copy.deepcopy(doc.get('review_criteria') or [{'id':'C1','criterion':'Inspect output'}])
for row in criteria: row['human_review']=True
body.setdefault('acceptance_criteria',criteria)
contract['hash']=human.support.digest({key:contract[key] for key in ('task_id','revision','body')})
state['goal_contract']=contract
token=f"r{contract['revision']}:{contract['hash']}"
request=copy.deepcopy(doc.get('user_request'))
origin={'stage':'astra_discovery'}
evidence={}
if scope=='goal_approval' and doc.get('model_settings',{}).get('joint_planning'):
    state['settings']['joint_planning']=True
    state['planning']={'final_token':token,'reports':{'astra_finalize':{'output':'final-plan.json'}}}
    state['stages']=[{'stage':'astra_finalize','output':'final-plan.json','exit_code':0,'source_revision':'fixture-source'}]
if scope=='human_review':
    review_token=doc.get('review_token','artifact-token')
    state['validation']={'verdict':'PASS','source_revision':'fixture-source','evidence_hashes':{}}
    state['human_reviews']=copy.deepcopy(doc.get('human_reviews',{}))
    evidence={'review_token':review_token}
    request={'kind':scope,'criteria':[row['id'] for row in criteria],'decision_needed':'Review the output',**(request or {})}
if scope in ('operational_exhaustion','blocker'):
    origin={'stage':'astra_resolve'}
    state['stages']=[{'stage':'astra_resolve','output':'diagnosis.json','exit_code':0}]
    evidence={'diagnosis':'No safe permitted recovery remains','output':'diagnosis.json'}
    request={'kind':scope,'decision_needed':'Provide corrective information','impact':'Execution remains paused',**(request or {})}
if scope in ('permission','goal_change'):
    request={'kind':scope,'decision_needed':'Allow the scoped change?','impact':'Changes authorized scope',**(request or {})}
with patch.object(human.support,'snapshot',return_value={'revision':'fixture-source'}):
    if scope:
        human.queue(state,scope,origin,request=request,questions=state['pending_questions'],evidence=evidence,
                    status='AWAITING_GOAL_APPROVAL' if scope=='goal_approval' else 'WAITING_FOR_USER')
        assert human.evaluate(state)=='escalate'
    projection=human.projection(state)
    assert bool(scope)==projection['human_request_authorized']
result={**doc,**projection,'questions':projection['pending_questions'],'goal':contract,'goal_token':token}
if scope=='human_review': result.update(review_token=review_token,review_criteria=criteria)
print(json.dumps(result))
`;
module.exports=function projectedRun(scope,run={}) {
  return JSON.parse(execFileSync(python,['-B','-c',setup,root],{input:JSON.stringify([scope,run]),encoding:'utf8',timeout:15000}));
};
