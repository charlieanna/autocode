const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm');
const source=fs.readFileSync(require('node:path').join(__dirname,'../dashboard_app.js'),'utf8');
class Element{constructor(){this.children=[];this.dataset={};this.isConnected=true;}append(...x){this.children.push(...x);}replaceChildren(...x){this.children=x;}addEventListener(){}showModal(){this.open=true;}close(){this.open=false;}focus(){}}
const nodes=new Map(),calls=[],pending=[];
const c=vm.createContext({Date,Set,Map,console,$:id=>{if(!nodes.has(id))nodes.set(id,new Element());return nodes.get(id);},n:()=>new Element(),card:()=>new Element(),button:(text,onclick)=>Object.assign(new Element(),{text,onclick}),focusKey:x=>x,basename:p=>p.split('/').pop(),document:{activeElement:new Element()},renderConversations(){},renderDraftConversation(){},setView(){},refresh(){},api:(url,options)=>{calls.push({url,...JSON.parse(options.body)});return new Promise((resolve,reject)=>pending.push({resolve,reject}));}});
vm.runInContext(`let latestData={conversations:[{id:'one'},{id:'two'}],archived_conversations:[]},latestConversation={id:'one'},activeConversation='one',seq=0,conversationArchiveReview=null,conversationArchiveNotice=null;const conversationArchivePending=new Set();`+source.slice(source.indexOf('function conversationArchiveBlocked('),source.indexOf('function renderConversations(')),c);
const doc={id:'one',title:'First conversation'};
(async()=>{
 c.reviewConversationArchive(doc);c.closeConversationArchive();assert.equal(calls.length,0);
 c.reviewConversationArchive(doc);const first=c.changeConversationArchive(doc,'archive');await c.changeConversationArchive(doc,'archive');c.closeConversationArchive();assert.equal(calls.length,1);assert.equal(nodes.get('#conversation-archive-dialog').open,true);
 pending.shift().resolve({...doc,archived_at:'today'});await first;assert.equal(vm.runInContext('latestData.conversations[0].id',c),'two');assert.equal(vm.runInContext('activeConversation',c),null);
 const restore=c.changeConversationArchive(doc,'restore');pending.shift().resolve(doc);await restore;assert.equal(vm.runInContext('latestData.archived_conversations.length',c),0);
 c.reviewConversationArchive(doc);const failed=c.changeConversationArchive(doc,'archive');pending.shift().reject(new Error('Disk unavailable'));await failed;assert.equal(nodes.get('#conversation-archive-dialog').open,true);assert.equal(nodes.get('#conversation-archive-error').textContent,'Disk unavailable');assert.equal(nodes.get('#conversation-archive-confirm').disabled,false);
 console.log('Conversation archive cancel, duplicate request, sibling preservation, restore and retained error checks passed.');
})().catch(e=>{console.error(e);process.exitCode=1;});
