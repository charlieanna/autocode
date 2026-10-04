/* Setup is an explicit card in a saved chat. Dependency checks are read-only;
   project creation uses one durable request, and model controls retain their
   existing authority. No credentials or provider output enter browser storage. */
let setupChat=stored('setup-conversation'),setupOpening=false,setupReport=null,setupCheckBusy=false,setupProjectBusy=false,setupError='',setupProjectPath='',setupProjectMode='new',setupEngine='opencode';
let setupModelHome=null,setupCheckAttempted=false;
function setupModels(){
  const models={};
  for(const role of ['glm','plan_reviewer','astra','terra','sol','completion']){
    const select=$('#'+role+'-model');
    if(select?.dataset.userSelected==='true'&&select.value)models[role+'_model']=select.value;
  }
  for(const role of ['plan_reviewer','astra','terra','sol','completion']){
    const select=$('#'+role+'-reasoning-effort');
    if(select?.dataset.userSelected==='true')models[role+'_reasoning_effort']=select.value;
  }
  return models;
}
async function openWorkspaceSetup(){
  if(setupOpening)return;setupOpening=true;
  try{
    let doc=null;
    if(setupChat){try{doc=await api('/api/conversation?id='+encodeURIComponent(setupChat));}catch{}}
    if(!doc||doc.archived_at||doc.attachment||doc.messages?.length){
      const request=savedRequest('setup-chat-request',{empty:true});
      doc=await api('/api/conversations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({empty:true,request_id:request.id})});
      setupChat=doc.id;persist('setup-conversation',doc.id);persist('setup-chat-request','');
    }
    openConversation(doc);renderWorkspaceSetup(doc);
    if(!setupReport&&!setupCheckBusy)checkWorkspaceSetup();
  }catch(error){dashboardNotice(error.message);}finally{setupOpening=false;}
}
function returnSetupModels(){
  if(setupModelHome?.marker.isConnected&&setupModelHome.element.parentElement!==setupModelHome.marker.parentElement)
    setupModelHome.marker.after(setupModelHome.element);
}
function renderWorkspaceSetup(doc){
  const old=$('#workspace-setup-card');
  if(doc?.id!==setupChat||doc.archived_at||doc.attachment||doc.messages?.length){returnSetupModels();old?.remove();return;}
  if(old){if(setupModelHome)$('#setup-model-settings').append(setupModelHome.element);return;}
  const box=card('','workspace-setup-card');box.id='workspace-setup-card';box.setAttribute('aria-label','Workspace setup');
  box.append(n('p','SETUP'),n('h2','Let’s get AutoCode ready'),n('p','Check this computer, choose a project, and continue your conversation. Your accounts stay with your model tool.'));
  const engineLabel=n('label','Model tool to check'),engine=n('select');engine.id='setup-engine';engineLabel.htmlFor=engine.id;
  for(const [value,label] of [['opencode','OpenCode'],['codex','Codex']])engine.append(Object.assign(n('option',label),{value}));
  engine.value=setupEngine;engine.onchange=()=>{setupEngine=engine.value;setupReport=null;renderSetupChecks();};
  const check=button('Check this computer',checkWorkspaceSetup);check.id='setup-recheck';
  const checks=n('div');checks.id='setup-checklist';checks.setAttribute('aria-live','polite');
  box.append(engineLabel,engine,check,checks);
  const instructions=n('details'),summary=n('summary','Install and connect your model tool');instructions.append(summary,n('p','OpenCode: install version 1.x, then connect accounts in OpenCode with “opencode auth login”. Use “opencode auth list” to inspect the connected providers. Codex: install Codex and complete “codex login”. Enter credentials only in the provider’s own sign-in.'));
  const guide=n('a','Installation instructions');guide.href='https://github.com/charlieanna/autocode/blob/master/docs/install.md';guide.target='_blank';guide.rel='noopener noreferrer';instructions.append(guide);box.append(instructions);
  const form=n('form');form.id='setup-project-form';form.append(n('h3','Choose a project'));
  const modeLabel=n('label','Project'),mode=n('select');mode.id='setup-project-mode';modeLabel.htmlFor=mode.id;
  for(const [value,label] of [['new','Create a new project'],['existing','Use an existing project']])mode.append(Object.assign(n('option',label),{value}));
  mode.value=setupProjectMode;mode.onchange=()=>{setupProjectMode=mode.value;setupError='';renderSetupChecks();};
  const pathLabel=n('label','Full project folder path'),input=n('input');input.id='setup-project-path';input.required=true;input.autocomplete='off';input.placeholder='/absolute/path/to/project';input.value=setupProjectPath;pathLabel.htmlFor=input.id;input.oninput=()=>{setupProjectPath=input.value;};
  const note=n('p','A new project creates this folder, initializes Git and saves an empty first commit. An existing project must have a commit and no uncommitted changes.');note.className='field-note';
  const submit=n('button','Create project and open chat');submit.id='setup-project-submit';submit.type='submit';submit.className='primary';
  const error=n('p');error.id='setup-error';error.className='error';error.setAttribute('role','alert');
  form.append(modeLabel,mode,pathLabel,input,note,error,submit);form.onsubmit=event=>{event.preventDefault();submitSetupProject();};box.append(form);
  const modelHost=n('section');modelHost.id='setup-model-settings';modelHost.append(n('h3','Models for the new project conversation'),n('p','These are the same per-role controls used for new conversations. Existing conversations and running tasks keep their saved settings.'));
  const models=$('#opencode-role-settings')?.closest('details');
  if(models){if(!setupModelHome){const marker=document.createComment('new conversation model settings');models.before(marker);setupModelHome={marker,element:models};}modelHost.append(models);}
  box.append(modelHost);$('#draft-messages').prepend(box);renderSetupChecks();
  if(!setupCheckAttempted)queueMicrotask(checkWorkspaceSetup);
}
function renderSetupChecks(){
  const host=$('#setup-checklist');if(!host)return;host.replaceChildren();
  if(setupReport){
    const list=n('ul');
    for(const item of setupReport.checks){const row=n('li'),status={ok:'Found',missing:'Missing',warn:'Check needed',unknown:'Not verified',unverified:'Not verified'}[item.status]||'Not verified';row.dataset.setupCheck=item.id;row.dataset.checkStatus=item.status;row.append(n('strong',item.label+' · '+status));if(item.guidance)row.append(n('p',item.guidance));list.append(row);}
    host.append(list,n('p',setupReport.scope));
  }else host.append(n('p',setupCheckBusy?'Checking local prerequisites…':'Check this computer to see which prerequisites are available.'));
  $('#setup-recheck').disabled=setupCheckBusy;$('#setup-recheck').textContent=setupCheckBusy?'Checking…':'Re-check this computer';$('#setup-engine').disabled=setupCheckBusy;
  $('#setup-project-submit').disabled=setupProjectBusy;$('#setup-project-submit').textContent=setupProjectBusy?'Saving project…':setupProjectMode==='new'?'Create project and open chat':'Open project chat';
  $('#setup-project-mode').disabled=setupProjectBusy;$('#setup-project-path').disabled=setupProjectBusy;$('#setup-error').textContent=setupError;
}
async function checkWorkspaceSetup(){
  if(setupCheckBusy)return;setupCheckAttempted=true;setupCheckBusy=true;setupError='';renderSetupChecks();
  const engine=setupEngine,workspace=setupProjectMode==='existing'?setupProjectPath.trim():'';
  try{const report=await api('/api/setup/check',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({engine,...(workspace?{workspace}:{})})});if(engine===setupEngine)setupReport=report;}
  catch(error){setupError=error.message;}finally{setupCheckBusy=false;renderSetupChecks();}
}
async function submitSetupProject(){
  if(setupProjectBusy)return;const path=setupProjectPath.trim();if(!path)return;
  const body={mode:setupProjectMode,path,models:setupModels()},request=savedRequest('setup-project-request',body);
  setupProjectBusy=true;setupError='';renderSetupChecks();
  try{
    const result=await api('/api/setup/project',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,request_id:request.id})});
    persist('setup-project-request','');returnSetupModels();openConversation(result.conversation);
  }catch(error){setupError=error.message;}finally{setupProjectBusy=false;renderSetupChecks();}
}
$('#workspace-setup')?.addEventListener('click',openWorkspaceSetup);
