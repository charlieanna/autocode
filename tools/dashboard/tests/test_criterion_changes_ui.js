// Run from the repository root: node tools/dashboard/tests/test_criterion_changes_ui.js
// #730: a requirement's changes in the Checks pane. A parallel batch is one saved step that served
// several milestones; each requirement shows only its own files of it and names what it leaves out.
// Executes the shipped requirement changes card; only the DOM and the diff read are fake.
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
function range(start,end){
  const first=source.indexOf(start),last=source.indexOf(end,first);
  assert.ok(first>=0&&last>first,`Missing shipped function range: ${start}`);
  return source.slice(first,last);
}
class Element{
  constructor(tag='div',text=''){this.tagName=tag.toUpperCase();this.textContent=String(text??'');this.children=[];this.dataset={};this.className='';this.open=false;this.isConnected=true;}
  append(...children){this.children.push(...children);}
  replaceChildren(...children){this.children=children;}
}
const flatten=node=>[node,...node.children.flatMap(flatten)];
const shown=node=>flatten(node).map(row=>row.textContent).join('\n');
const byClass=(node,name)=>flatten(node).filter(row=>row.className.split(' ').includes(name));
const RUN='/fixture/run';
function harness(data){
  const reads=[],context=vm.createContext({console,
    n:(tag,text)=>new Element(tag,text),document:{createElement:tag=>new Element(tag)},
    detailsState:new Map(),criterionDiffReads:new Map(),chosen:{run:RUN},activateTab(){},
    api:async url=>{reads.push(url);return data;}});
  vm.runInContext(range('function button(','function captureControls(')
    +range('function disclosure(','async function api(')
    +range('function diffOutput(','async function loadChanges('),context);
  // Open every requirement's changes, as a reader who expanded them.
  const open=async(run,id)=>{context.detailsState.set(RUN+'\0criterion-changes:'+id,true);const box=context.criterionChanges(run,{id});await new Promise(setImmediate);return box;};
  return {open,reads};
}
const diff=(file,line)=>'diff --git a/'+file+' b/'+file+'\n--- /dev/null\n+++ b/'+file+'\n@@ -0,0 +1 @@\n+'+line;
const statusView=criteria=>({status_view:{changes_by_criterion:{criteria},code_checkpoints:{rows:[]}},run:RUN,workspace:'/fixture/project'});

(async()=>{
// B and C were built by parallel Builders and integrated as one saved step.
{
  const run=statusView([
    {id:'C_B',checkpoints:['batch-1'],files:['server/handler.py'],overlapping:false},
    {id:'C_C',checkpoints:['batch-1'],files:['client/fetch.py'],overlapping:false}]);
  const h=harness({availability:'available',steps:{'batch-1':{label:'Parallel batch batch-1',milestone_id:null,milestones:['B','C'],availability:'available',
    files:[{path:'client/fetch.py',text:diff('client/fetch.py','def fetch(node): return node')},{path:'server/handler.py',text:diff('server/handler.py','def handle(node): return node')}]}}});
  for(const [id,own,other,line,foreign] of [['C_B','server/handler.py','client/fetch.py','def handle(node): return node','def fetch(node): return node'],
    ['C_C','client/fetch.py','server/handler.py','def fetch(node): return node','def handle(node): return node']]){
    const box=await h.open(run,id);
    assert.deepEqual(byClass(box,'criterion-file').map(row=>row.textContent),[own],id+' shows only its own file of the shared step');
    const added=byClass(box,'added').map(row=>row.textContent.slice(1));
    assert.ok(added.includes(line),id+' shows its own added lines');
    assert.ok(!added.includes(foreign),id+' does not show the other milestone\'s lines');
    const text=shown(box);
    assert.match(text,/Parallel batch batch-1 · milestones B, C/,id+' names every milestone the shared step served');
    assert.match(text,new RegExp('Not shown, changed in this step for another milestone: '+other.replace('.','\\.')),id+' names the file it leaves out');
  }
  assert.equal(h.reads.length,1,'One diff read serves every requirement');
}

// A step of one milestone, and a status view that does not list a requirement's files, show every file.
{
  const run=statusView([{id:'C_A',checkpoints:['builder-01'],overlapping:false}]);
  const h=harness({availability:'available',steps:{'builder-01':{label:'Builder step 1',milestone_id:'A',availability:'available',
    files:[{path:'contract/schema.json',text:diff('contract/schema.json','{"node": "string"}')},{path:'dependency_trace.json',text:diff('dependency_trace.json','{"edges": []}')}]}}});
  const box=await h.open(run,'C_A'),text=shown(box);
  assert.deepEqual(byClass(box,'criterion-file').map(row=>row.textContent),['contract/schema.json','dependency_trace.json']);
  assert.match(text,/Builder step 1 · milestone A/);
  assert.doesNotMatch(text,/Not shown/);
}
console.log('Requirement changes split a shared parallel step by requirement.');
})().catch(error=>{console.error(error);process.exitCode=1;});
