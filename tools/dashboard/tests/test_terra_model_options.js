// Exercise the shipped picker updater; no provider calls or DOM test library.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../dashboard_app.js'),'utf8');
const html=fs.readFileSync(path.join(__dirname,'../dashboard.html'),'utf8');
class Element {
  constructor(tag,text=''){this.tag=tag;this.textContent=text;this.children=[];this.value='';this.hidden=false;this.disabled=false;this.attributes=new Map();this.classList={toggle:(name,enabled)=>{this.attributes.set('class:'+name,String(Boolean(enabled)));}};}
  append(...nodes){this.children.push(...nodes);}
  replaceChildren(...nodes){this.children=[...nodes];}
  querySelector(selector){return this.children.find(child=>child.provider==='codex')||null;}
  querySelectorAll(){return this.children.flatMap(child=>child.tag==='option'?[child]:child.querySelectorAll());}
  setAttribute(name,value){this.attributes.set(name,String(value));}
  getAttribute(name){return this.attributes.get(name)||null;}
  removeAttribute(name){this.attributes.delete(name);}
}
const glm=new Element('select'),astra=new Element('select'),terra=new Element('select'),sol=new Element('select'),completion=new Element('select'),status=new Element('p'),createSubmit=new Element('button'),gate=new Element('div'),gateStatus=new Element('p'),retry=new Element('button');
assert.ok(!html.match(/<select id="terra-model".*?<\/select>/s)[0].includes('gpt-'));
assert.match(html,/<div id="create-model-catalogue-gate" class="create-model-catalogue-gate">.*?<p id="create-model-catalogue-status" class="field-note" role="status" aria-live="polite">/s);
assert.match(html,/<button id="retry-models" type="button" hidden>Retry catalogue<\/button>/);
assert.match(html,/<button class="primary" type="submit" id="create-submit" disabled aria-describedby="create-model-catalogue-status">/);
assert.doesNotMatch(html.match(/<p id="model-catalogue-status"[^>]*>/)[0],/aria-live/);
const roles={glm,astra,terra,sol,completion};
for(const select of Object.values(roles))select.value='openai/new-model';
const context=vm.createContext({$:id=>({'#glm-model':glm,'#astra-model':astra,'#terra-model':terra,'#sol-model':sol,'#completion-model':completion,'#model-catalogue-status':status,'#create-model-catalogue-gate':gate,'#create-model-catalogue-status':gateStatus,'#retry-models':retry,'#create-submit':createSubmit}[id]),n:(tag,text)=>new Element(tag,text)});
vm.runInContext(source.slice(source.indexOf('function syncModelOptions('),source.indexOf('async function loadModels(')),context);
const values=select=>select.querySelectorAll().map(option=>option.value);
const models=['zai-coding-plan/glm-5.3','openai/gpt-5.6-terra','openai/new-model','other-provider/new-model'];
context.syncModelOptions({usable:true,models});
assert.equal(terra.value,'openai/new-model');
assert.equal(createSubmit.disabled,false,'creation becomes available only after a usable catalogue arrives');
assert.equal(gate.hidden,true,'the submit-area status hides after a usable catalogue arrives');
assert.equal(retry.hidden,true,'the submit-area retry is hidden while the catalogue is usable');
assert.equal(createSubmit.getAttribute('aria-describedby'),null,'a usable catalogue removes the unavailable-state description');
assert.deepEqual(values(terra),['',...models]);
for(const select of Object.values(roles))assert.deepEqual(values(select),['',...models]);
assert.match(astra.children[0].textContent,/GPT-5.6 Sol · high/);
assert.match(terra.children[0].textContent,/GPT-5.6 Terra · medium/);
assert.match(sol.children[0].textContent,/GPT-5.6 Sol · high/);
assert.match(completion.children[0].textContent,/GPT-5.6 Sol · medium/);
assert.match(glm.children[0].textContent,/GLM-5.3 · OpenCode/);
assert.deepEqual(terra.children.filter(child=>child.tag==='optgroup').map(child=>child.label),['Z.ai Coding Plan','OpenAI · ChatGPT OAuth','other-provider']);
for(const data of [{error:'OpenCode unavailable'},{loading:true},{usable:false,models}]){
 context.syncModelOptions(data);
 assert.equal(createSubmit.disabled,true,'creation stays disabled while the catalogue is unavailable or loading');
 assert.equal(createSubmit.getAttribute('aria-describedby'),'create-model-catalogue-status','the disabled submit describes the visible status');
 assert.equal(gate.hidden,false,'the submit-area status stays rendered for every unavailable state');
 assert.match(gateStatus.textContent,data.error?/OpenCode unavailable/:data.loading?/Loading compatible models/:/No compatible models are currently available/);
 assert.equal(retry.hidden,Boolean(data.loading),'retry is only hidden while loading');
 assert.equal(retry.disabled,Boolean(data.loading),'retry is only disabled while loading');
 assert.equal(terra.value,'openai/new-model');
 assert.deepEqual(values(terra),['','openai/new-model']);
 assert.ok(terra.querySelectorAll().some(option=>option.textContent==='Unavailable selection: openai/new-model'));
 assert.match(status.textContent,/not API-key billing/);
 for(const select of Object.values(roles)){
   assert.equal(select.value,'openai/new-model');
   assert.deepEqual(values(select),['','openai/new-model']);
 }
}
context.syncModelOptions({usable:true,models:[]});
assert.equal(createSubmit.disabled,true,'an empty compatible catalogue cannot start creation');
assert.equal(terra.disabled,true,'an empty compatible catalogue keeps selectors disabled');
assert.match(status.textContent,/No compatible models are currently available/);
assert.equal(gate.hidden,false,'the empty state stays visible next to Start conversation');
assert.equal(retry.hidden,false,'the empty state exposes Retry catalogue');
assert.equal(retry.disabled,false,'the empty state keeps Retry catalogue actionable');
context.syncModelOptions({usable:true,models});
assert.equal(terra.value,'openai/new-model');
assert.ok(!terra.querySelectorAll().some(option=>option.textContent.startsWith('Unavailable')));
for(const select of Object.values(roles))assert.deepEqual(values(select),['',...models]);
assert.equal(gate.hidden,true,'recovery hides the stale submit-area status');
assert.equal(retry.hidden,true,'recovery hides Retry catalogue');
terra.value='zai-coding-plan/removed-model';
context.syncModelOptions({error:'No catalogue'});
assert.equal(terra.value,'zai-coding-plan/removed-model');
assert.equal(terra.querySelectorAll().find(option=>option.value===terra.value).textContent,'Unavailable selection: zai-coding-plan/removed-model');
context.syncModelOptions({provider:'kilofixture',usable:true,models:['vendor/current-model']});
for(const select of Object.values(roles))assert.match(select.children[0].textContent,/kilofixture config/);
assert.match(status.textContent,/Explicit selections run through kilofixture/);
assert.match(status.textContent,/kilofixture uses its own login and billing/);
assert.doesNotMatch(status.textContent,/ChatGPT OAuth|API-key billing/);
assert.equal(createSubmit.disabled,false);
console.log('All five roles use the live provider catalogue; distinct defaults and selections survive refresh/failure.');
