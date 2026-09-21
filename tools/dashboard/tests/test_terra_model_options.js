// Exercise the shipped picker updater; no provider calls or DOM test library.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../dashboard.html'),'utf8');
class Element {
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  querySelector(selector){return this.children.find(child=>child.provider==='codex')||null;}
  querySelectorAll(){return this.children.flatMap(child=>child.tag==='option'?[child]:child.querySelectorAll());}
}
const glm=new Element('select'),astra=new Element('select'),terra=new Element('select'),sol=new Element('select'),status=new Element('p');
assert.ok(!html.match(/<select id="terra-model".*?<\/select>/s)[0].includes('gpt-'));
const roles={glm,astra,terra,sol};
for(const select of Object.values(roles))select.value='openai/new-model';
const context=vm.createContext({$:id=>({'#glm-model':glm,'#astra-model':astra,'#terra-model':terra,'#sol-model':sol,'#model-catalogue-status':status}[id]),n:(tag,text)=>new Element(tag,text)});
vm.runInContext(source.slice(source.indexOf('function syncModelOptions('),source.indexOf('async function loadModels(')),context);
const values=select=>select.querySelectorAll().map(option=>option.value);
const models=['zai-coding-plan/glm-5.3','openai/gpt-5.6-terra','openai/new-model','other-provider/new-model'];
context.syncModelOptions({usable:true,models});
assert.equal(terra.value,'openai/new-model');
assert.deepEqual(values(terra),['',...models]);
for(const select of Object.values(roles))assert.deepEqual(values(select),['',...models]);
assert.match(astra.children[0].textContent,/GPT-6 Astra · OpenCode/);
assert.match(sol.children[0].textContent,/GPT-5.6 Sol · OpenCode/);
assert.match(glm.children[0].textContent,/GLM-5.3 · OpenCode/);
assert.deepEqual(terra.children.filter(child=>child.tag==='optgroup').map(child=>child.label),['Z.ai Coding Plan','OpenAI · ChatGPT OAuth','other-provider']);
for(const data of [{error:'OpenCode unavailable'},{loading:true},{usable:false,models}]){
 context.syncModelOptions(data);
 assert.equal(terra.value,'openai/new-model');
 assert.deepEqual(values(terra),['','openai/new-model']);
 assert.ok(terra.querySelectorAll().some(option=>option.textContent==='Unavailable selection: openai/new-model'));
 assert.match(status.textContent,/not API-key billing/);
 for(const select of Object.values(roles)){
   assert.equal(select.value,'openai/new-model');
   assert.deepEqual(values(select),['','openai/new-model']);
 }
}
context.syncModelOptions({usable:true,models});
assert.equal(terra.value,'openai/new-model');
assert.ok(!terra.querySelectorAll().some(option=>option.textContent.startsWith('Unavailable')));
for(const select of Object.values(roles))assert.deepEqual(values(select),['',...models]);
terra.value='zai-coding-plan/removed-model';
context.syncModelOptions({error:'No catalogue'});
assert.equal(terra.value,'zai-coding-plan/removed-model');
assert.equal(terra.querySelectorAll().find(option=>option.value===terra.value).textContent,'Unavailable selection: zai-coding-plan/removed-model');
console.log('All four roles use the live provider catalogue; distinct defaults and selections survive refresh/failure.');
