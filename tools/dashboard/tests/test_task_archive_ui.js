const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../dashboard_app.js'),'utf8');
class Element{constructor(){this.children=[];this.dataset={};this.isConnected=true;}append(...x){this.children.push(...x);}replaceChildren(...x){this.children=x;}addEventListener(){}showModal(){this.open=true;}close(){this.open=false;}focus(){}}
const nodes=new Map(),calls=[],pending=[];
const c=vm.createContext({Date,Set,Map,console,$:id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},n:()=>new Element(),card:()=>new Element(),button:(text,onclick)=>Object.assign(new Element(),{text,onclick}),focusKey:x=>x,basename:p=>p.split('/').pop(),taskTitle:r=>r.task,document:{activeElement:new Element()},renderTasks(){},renderConversations(){},setView(){},filterTasks(){},refresh(){},api:(url,options)=>{calls.push({url,...JSON.parse(options.body)});return new Promise((resolve,reject)=>pending.push({resolve,reject}));}});
vm.runInContext(`let latestData={runs:[{run:'/p/one'},{run:'/p/two'}],conversations:[],archived_tasks:[]},chosen={run:'/p/one'},latestRun=null,latestConversation=null,activeConversation=null,seq=0,archiveReview=null,archiveNotice=null;const archivePending=new Set();`+source.slice(source.indexOf('function archivedTask('),source.indexOf('function removedProject(')),c);
const run={workspace:'/p',run:'/p/one',task:'First task'};
(async()=>{
 c.reviewTaskArchive(run);c.closeTaskArchive();assert.equal(calls.length,0);
 c.reviewTaskArchive(run);const first=c.changeTaskArchive(run,'archive');await c.changeTaskArchive(run,'archive');c.closeTaskArchive();assert.equal(calls.length,1);assert.equal(nodes.get('#task-archive-dialog').open,true);
 pending.shift().resolve({});await first;assert(c.archivedTask(run.run));assert.equal(vm.runInContext('latestData.runs[0].run',c),'/p/two');assert.equal(vm.runInContext('chosen',c),null);
 const restore=c.changeTaskArchive(run,'restore');pending.shift().resolve({});await restore;assert(!c.archivedTask(run.run));
 c.reviewTaskArchive(run);const failed=c.changeTaskArchive(run,'archive');pending.shift().reject(new Error('Disk unavailable'));await failed;assert.equal(nodes.get('#task-archive-dialog').open,true);assert.equal(nodes.get('#task-archive-error').textContent,'Disk unavailable');assert(!c.archivedTask(run.run));assert.equal(nodes.get('#task-archive-confirm').disabled,false);
 console.log('Task archive cancel, duplicate request, sibling preservation, restore and retained error checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
