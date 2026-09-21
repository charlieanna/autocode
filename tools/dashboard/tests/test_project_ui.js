// node tests/test_project_ui.js — exercise project actions and stale-response guards.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
class Element {
 constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=text;this.children=[];this.dataset={};this.hidden=false;this.disabled=false;this.isConnected=true;this.classList={toggle(){}};}
 append(...children){this.children.push(...children);}
 replaceChildren(...children){this.children=children;}
 setAttribute(){} addEventListener(){} focus(){this.focused=true;} showModal(){this.open=true;} close(){this.open=false;}
}
function harness() {
 const nodes=new Map(),requests=[],pending=[];
 const context=vm.createContext({
  console,Date,Map,Set,JSON,taskArchiveBlocked:()=>false,
  $:selector=>{if(!nodes.has(selector))nodes.set(selector,new Element());return nodes.get(selector);},
  card:(_,cls)=>new Element(),n:(tag,text)=>new Element(tag,text),button:(text,action)=>Object.assign(new Element('button',text),{onclick:action}),focusKey:x=>x,
  document:{activeElement:new Element('button')},basename:p=>p.split('/').pop(),
  api:(url,options)=>{requests.push({url,payload:JSON.parse(options.body)});return new Promise((resolve,reject)=>pending.push({resolve,reject}));},
  renderTasks(){},renderConversations(){},renderLiveControls(){},setView(){},refresh(){},
 });
 vm.runInContext(`let seq=0,projectRemoval=null,projectNoticeState=null,latestRun=null,projectFilter='',activeConversation=null,latestConversation=null,currentView='task-detail';
 const projectPending=new Map();let chosen={workspace:'/a/shared',run:'/a/shared/run'};
 let latestData={workspaces:['/a/shared','/b/shared'],runs:[{workspace:'/a/shared',run:'/a/shared/run'},{workspace:'/b/shared',run:'/b/shared/run'}],conversations:[{id:'a',attachment:{workspace:'/a/shared'}},{id:'free'}],removed_projects:[]};
 function filterTasks(filter,project){currentView='tasks';projectFilter=project;}
 `+source.slice(source.indexOf('function removedProject('),source.indexOf('async function post(')),context);
 return {context,nodes,requests,pending,read:expression=>vm.runInContext(expression,context)};
}
async function test() {
 const h=harness(),{context:c}=h;
 // Cancel remains a review-only action, with no API request.
 c.reviewProjectRemoval('/a/shared');assert.equal(h.nodes.get('#project-removal-dialog').open,true);
 assert.equal(h.nodes.get('#project-removal-path').textContent,'/a/shared');c.closeProjectRemoval();assert.equal(h.requests.length,0);
 // Slow network: repeat clicks cannot launch another request or close the modal.
 c.reviewProjectRemoval('/a/shared');const removing=c.changeProject('/a/shared','remove');
 await c.changeProject('/a/shared','remove');c.closeProjectRemoval();assert.equal(h.requests.length,1);assert.equal(h.nodes.get('#project-removal-dialog').open,true);
 assert.equal(h.nodes.get('#project-removal-confirm').disabled,true);
 assert.deepEqual(h.requests[0],{url:'/api/projects',payload:{workspace:'/a/shared',action:'remove'}});
 h.pending.shift().resolve({});await removing;
 assert.equal(h.nodes.get('#project-removal-dialog').open,false);assert.equal(h.read('chosen'),null);assert.equal(h.read('currentView'),'tasks');
 assert.deepEqual(JSON.parse(h.read('JSON.stringify(latestData.workspaces)')),['/b/shared']);assert.equal(h.read('latestData.conversations[0].id'),'free');assert.equal(c.removedProject('/a/shared'),true);
 assert.equal(c.projectBlocked('/a/shared'),true);assert.equal(h.read('projectNoticeState.action'),'remove');
 // Restore is available by saved path even if no visible folder exists.
 const restoring=c.changeProject('/a/shared','restore');h.pending.shift().resolve({});await restoring;
 assert.equal(c.removedProject('/a/shared'),false);assert.equal(h.read('projectNoticeState.action'),'restore');
 // Failed removal is retained in the open review, with a usable retry and cancel.
 c.reviewProjectRemoval('/b/shared');const failing=c.changeProject('/b/shared','remove');h.pending.shift().reject(new Error('Could not save project visibility.'));await failing;
 assert.equal(h.nodes.get('#project-removal-dialog').open,true);assert.equal(h.nodes.get('#project-removal-error').hidden,false);assert.equal(h.nodes.get('#project-removal-error').textContent,'Could not save project visibility.');assert.equal(h.nodes.get('#project-removal-confirm').disabled,false);assert.equal(c.removedProject('/b/shared'),false);
 // Re-rendering poll-backed lists cannot erase the separate project notice.
 c.renderProjectManager(h.read('latestData'));assert.equal(h.read('projectNoticeState.action'),'restore');
 // Any task mutation is refused for a removed project, including review approvals.
 vm.runInContext(source.slice(source.indexOf('async function post('),source.indexOf('function concise(')),c);
 vm.runInContext("latestData.removed_projects=[{workspace:'/b/shared'}]",c);
 const count=h.requests.length;await c.post('/api/action',{workspace:'/b/shared',action:'approve_review'});assert.equal(h.requests.length,count);
 // A response begun before remove/restore cannot repopulate removed tasks.
 let finishFetch,setupCalls=0;
 const poll=vm.createContext({api:()=>new Promise(resolve=>finishFetch=resolve),setup:()=>setupCalls++,console});
 vm.runInContext("let seq=0,currentView='tasks',chosen=null,activeConversation=null,latestData=null;"+source.slice(source.indexOf('async function refreshOnce(){'),source.indexOf('async function refresh(){')),poll);
 const inflight=poll.refreshOnce();vm.runInContext('seq++',poll);finishFetch({workspaces:['/removed']});await inflight;
 assert.equal(setupCalls,0);assert.equal(vm.runInContext('latestData',poll),null);
 console.log('Project review, repeat clicks, removal, restore, retained errors, mutation and stale-poll guards passed.');
}
test().catch(error=>{console.error(error);process.exitCode=1;});
