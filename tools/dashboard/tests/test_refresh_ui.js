// Controlled read responses exercise polling races without a server or provider.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
class Element {
  constructor(tag='div',text=''){this.tag=tag;this.textContent=text;this.children=[];this.dataset={};this.disabled=false;this.hidden=false;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;this.textContent=children.map(child=>child.textContent).join('');}
}
const flush=()=>new Promise(resolve=>setImmediate(resolve));
function fixture(view='task-detail') {
  const nodes=new Map(),requests=[],events=[],focus={key:'change-text',version:1};
  const $=id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);};
  const controls=[$('#continue-run'),$('#change-text'),$('#retry-task')];
  controls.forEach((control,index)=>control.id=['continue-run','change-text','retry-task'][index]);
  $('#change-text').value='Unsent task draft';$('#draft-text').value='Unsent conversation draft';
  const context=vm.createContext({
    console,Date,Set,Promise,$,n:(tag,text)=>new Element(tag,text),
    document:{createTextNode:text=>new Element('text',text),querySelectorAll:selector=>selector==='[data-stale-disabled]'?controls.filter(control=>control.dataset.staleDisabled!==undefined):controls},
    api(url){events.push(['read',url]);return new Promise((resolve,reject)=>requests.push({url,resolve,reject}));},
    setup(data){events.push(['setup',data]);},renderTasks(data){events.push(['list',data]);},renderConversations(){},renderInbox(){},expireNotices(){},
    captureControls(){return {...focus};},restoreFocus(value){events.push(['focus',value]);},
    dashboardNotice(message){$('#dashboard-notice').textContent=message;},markMonitorStale(){events.push(['stale']);},
    removedProject:()=>false,renderTaskProjectActions(){},reviewTaskArchive(){},renderTaskOverview(){},basename:value=>value,
    taskTitle:run=>run.task,taskSentence:()=> 'Paused',taskActionBusy:()=>false,badge:()=>new Element(),taskPosition:()=> 'Paused',jointPlanning:()=>false,
    renderTaskReasoning(){},renderConversation(){},renderBrief(){},renderAstraPlan(){},renderExecution(){},renderLiveControls(){},renderTaskNow(){},renderThreadCheckpoint(){},activateTab(){},settleThreadScroll(){},
    renderDraftConversation(doc){vm.runInContext('latestConversation=receivedConversation',Object.assign(context,{receivedConversation:doc}));$('#draft-send').disabled=false;$('#attach-submit').disabled=false;events.push(['conversation',doc]);}
  });
  vm.runInContext(`let seq=1,currentView=${JSON.stringify(view)},chosen={workspace:'/p',run:'/p/task'},activeConversation='draft-one',latestData=null,latestRun={workspace:'/p',run:'/p/task'},latestConversation={id:'draft-one'},taskReadError='',taskReadAt=1,actionProblem='',currentTab='now';const archivePending=new Set();`,context);
  vm.runInContext(source.slice(source.indexOf('function disableStaleControls()'),source.indexOf('function renderThreadCheckpoint(')),context);
  vm.runInContext(source.slice(source.indexOf('async function refreshOnce()'),source.indexOf('async function refresh(){')),context);
  return {context,$,events,focus,read:expression=>vm.runInContext(expression,context),request:prefix=>{const request=requests.find(entry=>entry.url.startsWith(prefix));assert(request,'Expected an independent read for '+prefix);return request;}};
}
const run={workspace:'/p',run:'/p/task',task:'Selected task',status:'PAUSED'};
(async()=>{
  {
    const f=fixture();f.context.unavailableRun('Previous read failed');f.events.length=0;
    let finished=false;const poll=f.context.refreshOnce().then(()=>{finished=true;});
    f.focus.version=2;f.request('/api/run?').resolve(run);await flush();
    assert.equal(finished,false,'selected data must render while the list remains unresolved');
    assert.equal(f.read('latestRun.task'),'Selected task');assert.equal(f.read('taskReadError'),'');assert(f.read('taskReadAt')>1);
    assert.equal(f.$('#sync-state').textContent,'Task checked just now');assert.equal(f.$('#continue-run').disabled,false);
    assert.equal(f.$('#change-text').value,'Unsent task draft');assert.equal(f.events.filter(event=>event[0]==='list').length,0);
    assert.equal(f.events.find(event=>event[0]==='focus')[1].version,2,'restore the focus captured when the detail response arrives');
    f.read("actionProblem='Saved action needs attention'");f.request('/api/runs').reject(Error('Registry timed out'));await poll;
    assert.equal(f.read('taskReadError'),'');assert.equal(f.$('#sync-state').textContent,'Task checked just now');assert.equal(f.$('#continue-run').disabled,false);
    assert.equal(f.events.filter(event=>event[0]==='stale').length,0);assert.equal(f.$('#dashboard-notice').textContent,'Saved action needs attention');
  }
  {
    const f=fixture(),poll=f.context.refreshOnce();f.request('/api/runs').reject(Error('Registry offline'));await flush();
    assert.equal(f.read('taskReadError'),'');assert.equal(f.events.filter(event=>event[0]==='stale').length,0);
    f.request('/api/run?').resolve(run);await poll;assert.equal(f.read('latestRun.task'),'Selected task');assert.equal(f.$('#continue-run').disabled,false);
  }
  for(const stateError of [false,true]){
    const f=fixture(),poll=f.context.refreshOnce();
    if(stateError)f.request('/api/run?').resolve({...run,state_error:'Checkpoint unreadable'});else f.request('/api/run?').reject(Error('Task offline'));
    await flush();assert.match(f.read('taskReadError'),/Selected run unavailable/);assert.equal(f.$('#continue-run').disabled,true);assert.equal(f.$('#change-text').disabled,true);
    f.request('/api/runs').resolve({runs:[run]});await poll;
    assert.match(f.read('taskReadError'),/Selected run unavailable/);assert.equal(f.read('taskReadAt'),1);assert.equal(f.$('#sync-state').textContent,'Task status unavailable');
    assert.equal(f.$('#continue-run').disabled,true);assert.equal(f.$('#retry-task').disabled,false);assert.equal(f.read('latestRun.task'),undefined,'list data must not replace the selected read');
  }
  for(const reject of [false,true]){
    const f=fixture(),old=f.context.refreshOnce();f.read("seq++;chosen={workspace:'/p',run:'/p/other'};latestRun={run:'/p/other',task:'New selection'}");
    if(reject){f.request('/api/run?').reject(Error('Old task failed'));f.request('/api/runs').reject(Error('Old list failed'));}
    else{f.request('/api/run?').resolve(run);f.request('/api/runs').resolve({runs:[run]});}
    await old;assert.equal(f.read('latestRun.task'),'New selection');assert.equal(f.read('taskReadError'),'');assert.equal(f.read('latestData'),null);
    assert.equal(f.events.filter(event=>event[0]==='stale'||event[0]==='list').length,0);assert.equal(f.$('#dashboard-notice').textContent,'');
  }
  for(const guard of ["chosen={workspace:'/p',run:'/p/other'}","currentView='tasks'"]){
    for(const reject of [false,true]){
      const f=fixture(),poll=f.context.refreshOnce();f.read(guard);
      if(reject)f.request('/api/run?').reject(Error('Unselected task failed'));else f.request('/api/run?').resolve(run);
      f.request('/api/runs').resolve({runs:[]});await poll;assert.equal(f.read('latestRun.task'),undefined);assert.equal(f.read('taskReadError'),'');
    }
  }
  {
    const f=fixture('draft-conversation'),poll=f.context.refreshOnce();
    f.request('/api/conversation?').resolve({id:'draft-one',title:'Fresh conversation'});await flush();
    assert.equal(f.read('latestConversation.title'),'Fresh conversation');assert.equal(f.$('#draft-text').value,'Unsent conversation draft');
    f.request('/api/runs').reject(Error('Registry timed out'));await poll;assert.equal(f.$('#draft-send').disabled,false);assert.equal(f.$('#attach-submit').disabled,false);
  }
  {
    const f=fixture('draft-conversation'),poll=f.context.refreshOnce();f.request('/api/conversation?').reject(Error('Conversation offline'));await flush();
    f.request('/api/runs').resolve({runs:[]});await poll;assert.equal(f.read('latestConversation'),null);assert.equal(f.$('#draft-send').disabled,true);assert.equal(f.$('#attach-submit').disabled,true);
    assert.equal(f.$('#draft-problem').hidden,false);assert.equal(f.$('#draft-text').value,'Unsent conversation draft');
  }
  for(const guard of ["seq++","activeConversation='draft-two'","currentView='tasks'"]){
    for(const reject of [false,true]){
      const f=fixture('draft-conversation'),poll=f.context.refreshOnce();f.read(guard);
      if(reject)f.request('/api/conversation?').reject(Error('Old conversation failed'));else f.request('/api/conversation?').resolve({id:'draft-one',title:'Old conversation'});
      f.request('/api/runs').resolve({runs:[]});await poll;assert.equal(f.read('latestConversation.title'),undefined);assert.equal(f.$('#draft-send').disabled,false);
    }
  }
  console.log('Independent selected reads, list failure isolation, stale controls, selection guards, focus and draft checks passed.');
})().catch(error=>{console.error(error);process.exitCode=1;});
