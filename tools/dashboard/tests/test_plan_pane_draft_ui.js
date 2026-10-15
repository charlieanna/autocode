// Run from the repository root: node tools/dashboard/tests/test_plan_pane_draft_ui.js
// #1056: the task workspace hides the quiet plan rail, so the visible Plan pane
// must show the attached conversation draft the shared selection rule picks.
// Executes the shipped Plan pane, draft card and History link; only the DOM
// and network I/O are fake.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('./dashboard_vm');
const projectedRun=require('./resolver_fixture');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
function range(start,end){
  const first=source.indexOf(start),last=source.indexOf(end,first);
  assert.ok(first>=0&&last>first,`Missing shipped function range: ${start}`);
  return source.slice(first,last);
}
class Element{
  constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=String(text??'');this.children=[];this.dataset={};this.open=false;this.disabled=false;this.classList={add:()=>{},toggle:()=>{}};}
  setAttribute(name,value){this[name]=value;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
  focus(){this.focused=true;}
  get childElementCount(){return this.children.length;}
}
const flatten=node=>[node,...node.children.flatMap(flatten)];
// What a reader sees without opening a collapsed disclosure: its summary only.
const visible=node=>[node,...(node.tagName==='DETAILS'&&!node.open?node.children.filter(row=>row.tagName==='SUMMARY'):node.children.flatMap(visible))];
const disclosed=(host,label)=>flatten(host).some(row=>row.tagName==='DETAILS'&&row.children[0]?.tagName==='SUMMARY'&&row.children[0].textContent===label);
const shownText=node=>visible(node).map(row=>row.textContent).join(' ');
function harness(){
  const nodes=new Map(),calls=[],tabs=[];
  const $=selector=>{if(!nodes.has(selector))nodes.set(selector,new Element());return nodes.get(selector);};
  const context=vm.createContext({console,Date,URLSearchParams,$,
    n:(tag,text)=>new Element(tag,text),card:()=>new Element(),
    button:(label,onclick)=>Object.assign(new Element('button',label),{onclick}),
    document:{createElement:tag=>new Element(tag)},detailsState:new Map(),
    renderDocument:value=>new Element('pre',JSON.stringify(value)),human:value=>String(value),
    taskArchiveBlocked:()=>false,projectBlocked:()=>false,interruptedAttempt:()=>null,
    basename:value=>String(value||'').split('/').filter(Boolean).pop()||'',
    activateTab:tab=>tabs.push(tab),matchMedia:()=>({matches:false}),planList:()=>new Element(),
    renderLiveControls(){},renderExecution(){},renderTaskNow(){},dashboardNotice(){},refresh:async()=>{},
    latestRun:null,chosen:null,taskReadError:'',seq:0,taskActionPending:new Set(),taskChatPending:new Map(),
    api:async url=>{calls.push({url});return {};},
    post:async(url,payload)=>{calls.push({url,payload});return {status:'queued'};},
  });
  vm.runInContext(range('function conversationStatus(', 'function icon(')
    +range('function focusKey(', 'function captureControls(')
    +range('function disclosure(', 'async function api(')
    +range('function concise(', 'function badge(')
    +range('function jointPlanning(', 'function setView(')
    +range('const approveBuildState=', 'function inlineSavedChanges(')
    +range('function renderWorkflowTimeline(', 'function renderConversation(')
    +range('function planEntryText(', 'function output(')
    +range('function workRequirementRows(', 'function renderTaskNow(')
    +range('function requestKey(', 'function taskSentence(')
    +range('async function submitTaskAction(', 'function modelCatalogueSnapshot('),context);
  const plan=run=>{context.latestRun=run;context.chosen={run:run.run};context.renderBrief(run);return $('#brief-current');};
  return {context,$,calls,tabs,plan};
}
const draft=(revision,status,goal,extra={})=>({revision,status,goal,requirements:['Persist notes','Export to CSV'],
  milestones:[{id:'M1',objective:'Saved notes'}],parallelism:[],outstanding_questions:[{question:'Which export format?'}],
  attribution:{role:'planner',model:'zai/glm-5.3',reasoning_effort:'high'},
  freshness:{state:'fresh',updated_at:'2026-10-11T10:00:00Z'},...extra});
const conversation=(drafts,gate={})=>({id:'conv-1056',status:'ready',attachment:{status:'linked',run:'/fixture/run'},
  plan_gate:{token:null,architect_reviewed:false,pending_product_change:false,approved:false,status:'reviewing',...gate},
  plan_drafts:drafts});
const attached={workspace:'/fixture/project',run:'/fixture/run',task:'Notes app',status:'RUNNING',
  model_settings:{joint_planning:true},
  conversation:conversation([draft(1,'superseded','Build a notes app'),draft(2,'current','Build a notes app with CSV export')])};
const draftCard=host=>flatten(host).find(row=>row['aria-label']==='Planner draft preview');

// An attached current draft without a goal contract is readable in the Plan pane.
{
  const h=harness(),original=JSON.stringify(attached),host=h.plan(attached),text=shownText(host);
  assert.ok(draftCard(host),'The Plan pane shows the shipped read-only draft card');
  assert.doesNotMatch(text,/No saved plan yet/);
  assert.match(text,/Draft plan \(quiet\)/,'Draft status stays explicit');
  assert.match(text,/Build a notes app with CSV export/);
  assert.match(text,/Planner · zai\/glm-5\.3 · high/,'Actual attribution is shown');
  assert.match(text,/Updated with your latest message/,'Freshness is shown');
  assert.match(text,/Draft only\. Independent plan review and your approval are required before implementation\./);
  assert.ok(disclosed(host,'Earlier drafts (1)'),'Earlier drafts stay inspectable');
  assert.equal(flatten(host).some(row=>row.tagName==='BUTTON'),false,'The draft pane offers no approval or build control');
  assert.equal(JSON.stringify(attached),original,'Viewing a draft never rewrites the run');

  // The History link to that draft opens the visible Plan pane at every width.
  const timeline=new Element();h.context.renderWorkflowTimeline(timeline,attached);
  const link=flatten(timeline).find(row=>row.tagName==='BUTTON'&&/Read draft/.test(row.textContent));
  assert.ok(link,'History links to the live draft');
  assert.match(shownText(timeline),/Live draft plan · v2/);
  link.onclick();
  assert.deepEqual(h.tabs,['plan'],'The draft link opens the Plan pane instead of focusing the hidden rail');
  assert.equal(h.$('#goal').focused,undefined);
  assert.equal(h.calls.length,0,'Viewing a draft submits nothing');
}

// A newer current draft supersedes the one displayed before.
{
  const h=harness();
  const newer={...attached,conversation:conversation([draft(1,'superseded','Build a notes app'),
    draft(2,'superseded','Build a notes app with CSV export'),
    draft(3,'current','Build a notes app with CSV and JSON export',{freshness:{state:'fresh',structured_result:true,
      updated_at:'2026-10-11T11:00:00Z',source_messages:[{message_id:'m3',excerpt:'Add JSON export too'}]}})])};
  const text=shownText(h.plan(newer));
  assert.match(text,/Build a notes app with CSV and JSON export/);
  assert.doesNotMatch(text,/Build a notes app with CSV export/,'The superseded draft is not shown as current');
  assert.match(text,/Updated after your answer:\s+“Add JSON export too”/);
  assert.match(text,/Earlier drafts \(2\)/);
  assert.equal(h.calls.length,0);
}

// A placeholder or unreviewed saved contract no longer hides the selected draft.
{
  const h=harness();
  const placeholder={...attached,goal:{revision:1,origin:'migration_draft',approval_status:'draft',body:{intended_outcome:'Notes app'}}};
  const text=shownText(h.plan(placeholder));
  assert.doesNotMatch(text,/Plan not ready yet/);
  assert.match(text,/Build a notes app with CSV export/);
  const unreviewed={...attached,goal_token:'r1:abc',goal:{revision:1,origin:'astra_plan',approval_status:'draft',
    body:{intended_outcome:'An older saved outcome',acceptance_criteria:[{id:'C1',criterion:'Notes persist'}]}}};
  const host=h.plan(unreviewed),shown=shownText(host);
  assert.ok(draftCard(host));
  assert.match(shown,/Build a notes app with CSV export/);
  assert.doesNotMatch(shown,/An older saved outcome/,'The selected draft leads; the older saved revision is not presented as the plan');
  assert.ok(disclosed(host,'Saved plan revision 1 · not yet approved'),
    'The saved revision stays inspectable');
  assert.equal(h.calls.length,0);
}

// Reviewed and approved contracts keep their presentation and chat-only gates.
{
  const reviewed=projectedRun('goal_approval',{workspace:'/fixture/project',run:'/fixture/reviewed',
    model_settings:{joint_planning:true},goal:{revision:1,origin:'astra_finalize',body:{intended_outcome:'Reviewed notes app'}}});
  for(const conv of [conversation(attached.conversation.plan_drafts,{token:reviewed.goal_token,architect_reviewed:true,status:'ready'}),
    // Even if the journal has not caught up with final review yet.
    conversation(attached.conversation.plan_drafts)]){
    const h=harness(),run={...reviewed,conversation:conv},host=h.plan(run),text=shownText(host);
    assert.equal(h.context.statusInfo(run).label,'Approve plan');
    assert.equal(draftCard(host),undefined,'A reviewed revision is not replaced by a conversation draft');
    assert.match(text,/Plan revision 1 · Ready for your review/);
    assert.ok(flatten(host).some(row=>row.tagName==='BUTTON'&&row.textContent==='Review in conversation →'));
    assert.equal(h.calls.length,0);
  }
  // A requirement change after review shows the draft and drops the stale approval path.
  const h=harness(),changed={...reviewed,conversation:conversation(attached.conversation.plan_drafts,{pending_product_change:true})};
  const host=h.plan(changed);
  assert.ok(draftCard(host));
  assert.equal(flatten(host).some(row=>row.tagName==='BUTTON'),false);
  assert.equal(h.calls.length,0);

  const approved=projectedRun('',{workspace:'/fixture/project',run:'/fixture/approved',
    goal:{revision:2,origin:'astra_finalize',body:{intended_outcome:'Approved notes app'}}});
  assert.equal(approved.goal.approval_status,'approved');
  for(const gate of [{architect_reviewed:true,approved:true,status:'approved'},{pending_product_change:true}]){
    const a=harness(),run={...approved,conversation:conversation(attached.conversation.plan_drafts,gate)},host=a.plan(run);
    assert.equal(draftCard(host),undefined,'An approved contract keeps its presentation');
    assert.match(shownText(host),/Plan revision 2 · Reviewed & approved/);
    assert.equal(a.calls.length,0);
  }
}
console.log('Plan pane shows the selected attached draft read-only; reviewed and approved contracts keep their presentation.');
