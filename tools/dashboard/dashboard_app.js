const setupInitiallyEmptyLocation=!globalThis.location?.hash;
/* Local dashboard: render saved runner data; only explicit actions advance work. */
let chosen = null, seq = 0, init = false, focusVersion = 0;
let currentView = 'tasks', currentTab = 'now', taskFilter = 'all', projectFilter = '', searchText = '';
let evidenceRequest = 0, evidenceRun = '', previewRun = '', scrollThreadToEnd = false, pendingThreadAnchorKey = '';
let taskReturn = {view:'tasks',filter:'all',project:''};
const evidenceCache = new Map();
let latestData = null, latestRun = null, actionProblem = '', newTaskPending = null, pendingShortRun = '', pendingShortProject = null;
let taskReadError = '', taskReadAt = null;
let activeConversation = null, latestConversation = null, refreshPromise = null, refreshAgain = false, refreshTimer = null;
let creatingConversation = false, conversationRequest = null;
const conversationPending = new Set(), conversationRetries = new Map(), taskChatPending = new Map(), taskChatErrors = new Map();
const $ = selector => document.querySelector(selector);
const n = (tag, text) => { const element = document.createElement(tag); element.textContent = text ?? ''; return element; };
const drafts = new Map(), approvalDrafts = new Map(), changeDrafts = new Map(), detailsState = new Map();
const sendingRequests = new Map(), uncertainRequests = new Map();
const taskActionPending = new Set();
const projectPending = new Map();
let projectRemoval = null, projectNoticeState = null;
let archiveReview=null,archiveNotice=null;
const archivePending=new Set();
let conversationArchiveReview=null,conversationArchiveNotice=null;
const conversationArchivePending=new Set();
let modelRequest = 0;
let modelCatalogue={models:[],usable:false,loading:true,error:null};
let conversationCatalogue={models:[],usable:false,loading:true,error:null},conversationProfile=null;
let conversationTransport={transport:'unknown',version:null};
const modelReplacementState=new Map(),archiveUncertain=new Map(),projectUncertain=new Map();
const draftKey = (id, run = chosen?.run || '') => run + '\0' + id;
const basename = path => String(path || '').split('/').filter(Boolean).pop() || 'Unavailable project';
const human = text => String(text || '').replace(/_/g, ' ').replace(/\b\w/g, value => value.toUpperCase());
const legacyRunSelection = run => 'task='+encodeURIComponent(run.workspace)+'&run='+encodeURIComponent(run.run);
function shortRunKey(run) {
  const match=basename(run?.run).match(/-([a-f0-9]{8,64})$/i);
  return match ? 'r-'+match[1].toLowerCase() : '';
}
function matchingShortRuns(key,runs=latestData?.runs||[]) {
  return /^r-[a-f0-9]{8,64}$/.test(key||'') ? (runs||[]).filter(run=>shortRunKey(run)===key) : [];
}
function runSelection(run,runs=latestData?.runs||[]) {
  const key=shortRunKey(run);
  return key&&matchingShortRuns(key,runs).length===1 ? 'run='+encodeURIComponent(key) : legacyRunSelection(run);
}
function projectPathForKey(key,data=latestData) {
  const path=data?.workspace_ids?.[key];
  return typeof path==='string'?path:'';
}
function projectKeyForPath(path,data=latestData) {
  const matches=Object.entries(data?.workspace_ids||{}).filter(([,value])=>value===path);
  return matches.length===1?matches[0][0]:'';
}
function unavailableShortRun() {
  dashboardNotice('This saved task link is unavailable or ambiguous. Choose the task from All work.');
  setView('tasks');
}
function restorePendingShortRun(data) {
  if(!pendingShortRun)return false;
  const key=pendingShortRun,matches=matchingShortRuns(key,data?.runs||[]);pendingShortRun='';
  if(matches.length===1){openRun(matches[0]);return true;}
  unavailableShortRun();return false;
}
document.addEventListener('focusin', () => focusVersion++);
function stored(key, fallback='') { try { return localStorage.getItem('autocode:'+key) ?? fallback; } catch { return fallback; } }
function persist(key, value) { try { localStorage.setItem('autocode:'+key,value); } catch {} }
// The active project scopes new conversations before anything is submitted;
// sidebar groups expand or collapse per project and filter by conversation title.
let activeProject = stored('active-project','');
let conversationSearch = '';
let newTaskProject = '';
function newRequestId() { return crypto.randomUUID(); }
function readSavedRequest(key) {try{return JSON.parse(stored(key,'null'));}catch{return null;}}
function savedRequest(key,payload) {const previous=readSavedRequest(key),signature=JSON.stringify(payload);if(previous?.signature===signature)return previous;const request={id:newRequestId(),signature,payload};persist(key,JSON.stringify(request));return request;}
function rememberSelection(value) { persist('selection',value); history.replaceState(null,'','#'+value); }
function conversationStatus(doc) { return doc.attachment?.status==='linked'?'Attached':doc.attachment?.status==='starting'?'Connecting project':doc.status==='thinking'?'Thinking…':doc.status==='error'?'Needs attention':'Planning'; }
function conversationPayload(text, models, request_id, workspace) { const payload = {text:text.trim(),models,request_id}; if(workspace)payload.workspace=workspace; return payload; }
function resolverResponseFields(run) {
  const envelope=run.human_escalation;
  return run.human_request_authorized===true&&typeof envelope?.request_id==='string'&&envelope.request_id&&typeof envelope.request_token==='string'&&envelope.request_token?{resolver_request:envelope.request_id,resolver_token:envelope.request_token}:{};
}
function resolverReplyCurrent(run,request,scopes) {
  const current=resolverResponseFields(run);
  return !!current.resolver_request&&['WAITING_FOR_USER','AWAITING_GOAL_APPROVAL'].includes(run.status)&&scopes.includes(run.human_escalation.scope)&&request.resolver_request===current.resolver_request&&request.resolver_token===current.resolver_token;
}
function chatPayload(run,text,question_id,request_id,delegate=false) {
  const fields=question_id?resolverResponseFields(run):{};
  if((question_id||delegate)&&(!question_id||!resolverReplyCurrent(run,fields,['clarification','permission','goal_change'])||!(run.questions||[]).some(question=>String(question.id)===String(question_id))))throw Error('This answer needs the current Resolver request. Refresh the task.');
  return {workspace:run.workspace,run:run.run,text,request_id,...(question_id?{question_id,...fields}:{}),...(delegate?{delegate:true}:{})};
}
function messageTime(entry) {const value=entry.created_at||entry.timestamp||entry.at;if(typeof value==='number')return value<1e12?value*1000:value;return Date.parse(value)||0;}
function orderedMessages(entries) {return entries.map((entry,index)=>({entry,index,time:messageTime(entry)})).sort((a,b)=>a.time-b.time||a.index-b.index).map(item=>item.entry);}
function receiptLabel(entry) {const status=entry.delivery_status||entry.status||'';return ({received:'Saved',saved:'Saved',queued:'Saved · queued for the next step',applied:'Applied',resumed:'Applied',delivered:'Delivered',error:'Delivery needs attention'})[status]||human(status);}

function icon(name) {
  const paths = {grid:'M3 3h7v7H3z M14 3h7v7h-7z M3 14h7v7H3z M14 14h7v7h-7z',
    inbox:'M4 4h16l2 12v4H2v-4z M2 15h6l2 3h4l2-3h6',activity:'M3 12h4l3-8 4 16 3-8h4',
    settings:'M12 8a4 4 0 1 0 0 8 4 4 0 0 0 0-8 M12 2v3 M12 19v3 M2 12h3 M19 12h3 M5 5l2 2 M17 17l2 2 M5 19l2-2 M17 7l2-2',
    layers:'m12 3 9 5-9 5-9-5z M3 12l9 5 9-5 M3 16l9 5 9-5',search:'M10 3a7 7 0 1 0 0 14 7 7 0 0 0 0-14 M15 15l6 6',
    check:'m5 12 4 4L19 6',plan:'M8 5h13 M8 12h13 M8 19h13 M3 5h.01 M3 12h.01 M3 19h.01',
    folder:'M3 6h7l2 3h9v11H3z',arrow:'M5 12h14 M13 6l6 6-6 6'};
  const svg = document.createElementNS('http://www.w3.org/2000/svg','svg');
  svg.setAttribute('viewBox','0 0 24 24'); svg.setAttribute('fill','none'); svg.setAttribute('stroke','currentColor');
  svg.setAttribute('stroke-width','1.5'); svg.setAttribute('stroke-linecap','round'); svg.setAttribute('stroke-linejoin','round'); svg.setAttribute('aria-hidden','true');
  const path = document.createElementNS('http://www.w3.org/2000/svg','path'); path.setAttribute('d',paths[name] || paths.folder); svg.append(path); return svg;
}
document.querySelectorAll('[data-icon]').forEach(element => element.append(icon(element.dataset.icon)));
function button(text, action, className = '') { const element = n('button', text); element.type = 'button'; element.className = className; element.onclick = action; return element; }
function card(text, className = '') { const element = n('div', text); element.className = className; return element; }
function focusKey(element, key) { element.dataset.focusKey = key; return element; }
function captureControls() {
  const active = document.activeElement;
  if (!active?.dataset?.focusKey) return null;
  return {key:active.dataset.focusKey, version:focusVersion, start:active.selectionStart, end:active.selectionEnd, direction:active.selectionDirection};
}
function restoreFocus(focus) {
  if (!focus || focus.version !== focusVersion) return;
  const target = [...document.querySelectorAll('[data-focus-key]')].find(element => element.dataset.focusKey === focus.key);
  if (!target || document.activeElement === target) return;
  // Polling may replace the focused node after the reader has scrolled to a
  // related action. Restore focus without moving that action out of reach.
  target.focus({preventScroll:true});
  if (typeof target.setSelectionRange === 'function' && focus.start != null) target.setSelectionRange(focus.start, focus.end, focus.direction);
}
function disclosure(label, key, children, run = chosen?.run || '') {
  const element = document.createElement('details'), identity = run + '\0' + key;
  element.open = !!detailsState.get(identity);
  element.append(focusKey(n('summary',label),'details:'+identity), ...children);
  element.ontoggle = () => { if (element.isConnected) detailsState.set(identity,element.open); };
  return element;
}
async function api(url, options) {
  // Only reads have a client deadline. Never replay an uncertain mutation.
  const request = options || {cache:'no-store',signal:AbortSignal.timeout(20000)};
  let response;
  try { response = await fetch(url, request); }
  catch (cause) { const error=Error(cause?.message||'The request connection closed before a receipt was returned.');error.uncertain=true;throw error; }
  let data;
  try { data=await response.json(); }
  catch (cause) { const error=Error('The server response could not be read. Refresh status before retrying this mutation.');error.uncertain=true;throw error; }
  if (!response.ok) { const error=Error(data.error || 'The request could not be completed.');error.rejected=true;throw error; }
  return data;
}
function dashboardNotice(message) { const host = $('#dashboard-notice'); host.textContent = message || ''; host.hidden = !message; }
function dialogFocusables(dialog){return dialog&&typeof dialog.querySelectorAll==='function'?[...dialog.querySelectorAll('button:not([disabled]),[href],input:not([disabled]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(control=>!control.hidden):[];}
function trapDialogFocus(event,dialog){
  if(event.key!=='Tab')return;
  const controls=dialogFocusables(dialog);if(!controls.length)return;
  const first=controls[0],last=controls.at(-1);
  if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}
  else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}
}
function bindDialogBehavior(dialog,close){
  dialog.addEventListener('keydown',event=>{if(event.key==='Escape'){event.preventDefault();close();return;}trapDialogFocus(event,dialog);});
  dialog.addEventListener('click',event=>{if(event.target===dialog)close();});
  dialog.addEventListener('cancel',event=>{event.preventDefault();close();});
  dialog.addEventListener('close',()=>{const opener=dialog._returnFocus;dialog._returnFocus=null;returnDialogFocus(opener);});
}
function returnDialogFocus(opener){
  const key=opener?.dataset?.focusKey;
  const restore=()=>{
    const current=opener?.isConnected?opener:key?[...document.querySelectorAll('[data-focus-key]')].find(control=>control.dataset.focusKey===key):null;
    if(current?.isConnected)current.focus();
  };restore();
  if(typeof requestAnimationFrame==='function')requestAnimationFrame(restore);
  // Native dialog dismissal can restore the browser's prior focus after the
  // close event; defer one final restoration beyond that native cleanup.
  if(typeof setTimeout==='function')setTimeout(restore,32);
}
async function copySavedValue(value,label){
  const text=String(value||'');if(!text)return;
  try{if(navigator.clipboard?.writeText)await navigator.clipboard.writeText(text);dashboardNotice(label+' copied.');}
  catch{dashboardNotice(label+' could not be copied automatically. It remains visible to copy.');}
}
function archivedTask(run){return !!run&&(latestData?.archived_tasks||[]).some(row=>row.run===run);}
function taskArchiveBlocked(run){return archivedTask(run)||archivePending.has(run);}
function archiveButton(run,action,label){const control=focusKey(button(label,()=>changeTaskArchive(run,action)),'archive:'+action+':'+run.run);control.disabled=archivePending.has(run.run);if(action==='restore')control.dataset.staleSafe='true';return control;}
function taskArchiveUncertain(run){return typeof archiveUncertain==='undefined'?null:archiveUncertain.get(run);}
function setTaskArchiveUncertain(run,value){if(typeof archiveUncertain!=='undefined'){if(value)archiveUncertain.set(run,value);else archiveUncertain.delete(run);}}
function mutationRequestId(){return typeof newRequestId==='function'?newRequestId():'request-'+Date.now()+'-'+Math.random().toString(16).slice(2);}
function renderArchiveNotice(){
  const host=$('#archive-notice');host.replaceChildren();host.hidden=!archiveNotice;if(!host.hidden)host.classList?.toggle('mutation-toast',!archiveNotice.error);if(!archiveNotice)return;
  const {run,action,error,uncertain}=archiveNotice;
  host.append(n('p',error||uncertain?'Archive request unconfirmed — reconcile before retrying.':action==='archive'?'Task archived · Undo':'Task restored.'));
  if(error)host.append(archiveButton(run,action,'Retry'));
  else if(uncertain)host.append(button('Retry status',()=>reconcileTaskArchive(run),'retry-task-status'));
  else if(action==='archive')host.append(archiveButton(run,'restore','Undo'));
  host.append(button('Archived tasks',()=>setView('archived')),button('Dismiss',()=>{archiveNotice=null;renderArchiveNotice();},'text-button'));
}
function renderArchivedTasks(data){const host=$('#archived-task-list');host.replaceChildren();const entries=data.archived_tasks||[];$('#archive-count').textContent=entries.length;for(const run of entries){const row=card('','managed-project'),copy=card('','managed-project-copy');copy.append(n('h3',taskTitle(run)),n('p',projectTitle(run.workspace)),Object.assign(n('p',basename(run.run)),{className:'project-path'}));row.append(copy,archiveButton(run,'restore','Restore task'),button('Delete task & files…',()=>reviewTaskDelete(run),'text-button'));host.append(row);}if(!entries.length)host.append(n('p','No archived tasks.'));renderPendingDeletions(host,data);}
function renderTaskArchiveReview(){
  const review=archiveReview,uncertain=review&&taskArchiveUncertain(review.run),confirm=$('#task-archive-confirm'),reconcile=$('#task-archive-reconcile'),message=$('#task-archive-uncertain');
  if(!review)return;
  message.hidden=!uncertain;reconcile.hidden=!uncertain;
  if(uncertain){message.textContent='Request '+uncertain.requestId+' may have reached the dashboard. Refresh status and reconcile before submitting another archive request.';confirm.disabled=true;reconcile.onclick=()=>reconcileTaskArchive(review);}
  else{message.textContent='';confirm.disabled=archivePending.has(review.run);}
}
function reviewTaskArchive(run){if(!run?.run||taskArchiveBlocked(run.run))return;archiveReview={...run,opener:document.activeElement};const dialog=$('#task-archive-dialog');dialog._returnFocus=archiveReview.opener;$('#task-archive-name').textContent=run.task||basename(run.run);$('#task-archive-project').textContent=projectTitle(run.workspace)+' · '+basename(run.run);$('#task-archive-error').textContent='';$('#task-archive-error').hidden=true;$('#task-archive-confirm').textContent='Archive task';$('#task-archive-cancel').disabled=false;renderTaskArchiveReview();dialog.showModal();$('#task-archive-cancel').focus();}
function closeTaskArchive(){if(archiveReview&&archivePending.has(archiveReview.run))return;const opener=archiveReview?.opener;archiveReview=null;$('#task-archive-dialog').close();if(typeof returnDialogFocus==='function')returnDialogFocus(opener);else if(opener?.isConnected)opener.focus();}
$('#task-archive-cancel').onclick=closeTaskArchive;
if(typeof bindDialogBehavior==='function')bindDialogBehavior($('#task-archive-dialog'),closeTaskArchive);else $('#task-archive-dialog').addEventListener('cancel',event=>{event.preventDefault();closeTaskArchive();});
$('#task-archive-confirm').onclick=()=>{if(archiveReview)changeTaskArchive(archiveReview,'archive');};
async function reconcileTaskArchive(run){
  const request=taskArchiveUncertain(run.run);if(!request)return;
  const control=$('#task-archive-reconcile');control.disabled=true;
  try{await refresh();const archived=archivedTask(run.run);if(archived===(request.action==='archive')){setTaskArchiveUncertain(run.run,null);archiveNotice={run,action:request.action,expiresAt:Date.now()+12000,reconciled:true};if(archiveReview?.run===run.run){archiveReview=null;$('#task-archive-dialog').close();}}
    else{setTaskArchiveUncertain(run.run,null);if(archiveReview?.run===run.run){$('#task-archive-uncertain').textContent='Status reconciled: no archive was recorded. You may submit a new archive request.';$('#task-archive-uncertain').hidden=false;$('#task-archive-confirm').disabled=false;}archiveNotice={run,action:request.action,error:'Status reconciled: no archive was recorded. Submit a new archive request only if you still want to archive this task.'};}}
  catch(error){if(archiveReview?.run===run.run){$('#task-archive-uncertain').textContent='Status is still unavailable. Do not retry the mutation yet: '+error.message;$('#task-archive-uncertain').hidden=false;}}
  finally{control.disabled=false;renderArchiveNotice();}
}
async function changeTaskArchive(run,action){
  if(archivePending.has(run.run))return;archivePending.add(run.run);seq++;
  const dialog=action==='archive'&&archiveReview?.run===run.run;
  const requestId=typeof mutationRequestId==='function'?mutationRequestId():'request-'+Date.now()+'-'+Math.random().toString(16).slice(2);
  if(dialog){$('#task-archive-confirm').disabled=true;$('#task-archive-confirm').textContent='Archiving…';$('#task-archive-cancel').disabled=true;$('#task-archive-error').hidden=true;}
  try{await api('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace:run.workspace,run:run.run,action})});seq++;
    setTaskArchiveUncertain(run.run,null);
    if(latestData){latestData.archived_tasks=(latestData.archived_tasks||[]).filter(row=>row.run!==run.run);if(action==='archive'){latestData.archived_tasks.push({workspace:run.workspace,run:run.run,task:run.task});latestData.runs=(latestData.runs||[]).filter(row=>row.run!==run.run);latestData.conversations=(latestData.conversations||[]).filter(doc=>doc.attachment?.run!==run.run);}}
    if(action==='archive'&&chosen?.run===run.run){chosen=null;latestRun=null;$('#archive-task').disabled=true;$('#delete-task').disabled=true;filterTasks('all',run.workspace);}
    if(action==='archive'&&latestConversation?.attachment?.run===run.run){activeConversation=null;latestConversation=null;setView('conversations');}
    archiveNotice={run:{workspace:run.workspace,run:run.run,task:run.task},action,expiresAt:Date.now()+12000};if(dialog){archiveReview=null;$('#task-archive-dialog').close();}
  }catch(error){if(error?.uncertain){setTaskArchiveUncertain(run.run,{action,requestId});if(dialog){$('#task-archive-error').hidden=true;$('#task-archive-cancel').disabled=false;renderTaskArchiveReview();}archiveNotice={run,action,uncertain:true,requestId};}
    else if(dialog){$('#task-archive-error').textContent=error.message;$('#task-archive-error').hidden=false;}else archiveNotice={run,action,error:error.message};}
  finally{archivePending.delete(run.run);seq++;if(dialog){$('#task-archive-confirm').textContent='Archive task';$('#task-archive-cancel').disabled=false;if(!taskArchiveUncertain(run.run))$('#task-archive-confirm').disabled=false;}if(latestData){renderTasks(latestData);renderConversations(latestData);renderArchivedTasks(latestData);}renderArchiveNotice();if(dialog&&!archiveReview){$('#archive-notice').tabIndex=-1;$('#archive-notice').focus();}await refresh();}
}
function removedProject(workspace, data=latestData) { return !!workspace && (data?.removed_projects||[]).some(project=>project.workspace===workspace); }
function projectBlocked(workspace) { return removedProject(workspace)||projectPending.get(workspace)==='remove'; }
function projectRemovalUncertain(workspace){return typeof projectUncertain==='undefined'?null:projectUncertain.get(workspace);}
function setProjectRemovalUncertain(workspace,value){if(typeof projectUncertain!=='undefined'){if(value)projectUncertain.set(workspace,value);else projectUncertain.delete(workspace);}}
function expiredTemporaryEntry(run) { return run.workspace_cleanup?.eligible===true&&run.workspace_cleanup?.reason==='confirmed_missing'; }
function dashboardProjects(data) { return [...new Set([...(data.workspaces||[]),...(data.runs||[]).map(run=>run.workspace).filter(Boolean)])].filter(workspace=>!removedProject(workspace,data)&&!((data.runs||[]).some(run=>run.workspace===workspace)&&(data.runs||[]).filter(run=>run.workspace===workspace).every(expiredTemporaryEntry))); }
function renderTemporaryHistory(data){
  const host=$('#temporary-history');if(!host)return;
  const entries=[...new Map((data.runs||[]).filter(expiredTemporaryEntry).map(run=>[run.workspace,run])).values()];
  host.replaceChildren();host.hidden=!entries.length;if(!entries.length)return;
  const rows=n('div');for(const run of entries){const row=card('','managed-project-copy');row.append(n('h3',projectTitle(run.workspace)),Object.assign(n('p',run.workspace),{className:'project-path'}),n('p','Confirmed missing '+new Date(run.workspace_cleanup.checked_at).toLocaleString()+'. Its history is preserved and it returns automatically when the workspace is available.'));rows.append(row);}
  host.append(disclosure('Temporary workspace history ('+entries.length+')','temporary-history',[n('p','These temporary folders were confirmed missing with no active or uncertain worker. Only their dashboard placement changed; no files were deleted.'),rows,button('Recheck status',()=>refresh())],'settings'));
}
function projectActionButton(workspace, action, label) {
  const control=focusKey(button(label,()=>action==='remove'?reviewProjectRemoval(workspace):changeProject(workspace,'restore'),action==='remove'?'remove-project-button':''),'project-'+action+':'+workspace);
  control.disabled=projectPending.has(workspace);if(action==='restore')control.dataset.staleSafe='true';control.setAttribute('aria-label',label+' '+projectTitle(workspace));return control;
}
function renderProjectNotice() {
  const host=$('#project-notice');host.replaceChildren();host.hidden=!projectNoticeState;if(!projectNoticeState)return;
  const {workspace,action,error,uncertain}=projectNoticeState,copy=card('','project-notice-copy');
  copy.append(n('p',error||uncertain?'Project removal request unconfirmed — reconcile before retrying.':action==='remove'?'Project removed from dashboard · Undo':'Project restored to dashboard.'),Object.assign(n('p',workspace),{className:'project-path'}));host.append(copy);host.classList?.toggle('error',!!error);host.classList?.toggle('mutation-toast',!error);
  if(error)host.append(projectActionButton(workspace,action,'Retry '+(action==='remove'?'removal':'restore')));
  else if(uncertain)host.append(button('Retry status',()=>reconcileProjectRemoval(workspace),'retry-project-status'));
  else if(action==='remove')host.append(projectActionButton(workspace,'restore','Undo'));
  host.append(button('Manage projects',()=>setView('settings')),button('Dismiss',()=>{projectNoticeState=null;renderProjectNotice();},'text-button'));
}
function renderProjectManager(data) {
  const visible=$('#managed-projects'),removed=$('#removed-projects');visible.replaceChildren();removed.replaceChildren();
  for(const [host,entries,action] of [[visible,dashboardProjects(data).map(workspace=>({workspace})),'remove'],[removed,data.removed_projects||[],'restore']]) {
    for(const project of entries){const row=card('','managed-project'),copy=card('','managed-project-copy');copy.append(n('h3',projectTitle(project.workspace)),Object.assign(n('p',project.workspace),{className:'project-path'}));
      if(action==='restore'&&project.removed_at){const date=new Date(project.removed_at);if(!Number.isNaN(date.valueOf()))copy.append(Object.assign(n('p','Removed '+date.toLocaleString()),{className:'field-note'}));}
      row.append(copy,projectActionButton(project.workspace,action,action==='remove'?'Remove project':'Restore project'));host.append(row);}
    if(!entries.length)host.append(Object.assign(n('p',action==='remove'?'No projects are currently shown.':'No removed projects.'),{className:'field-note'}));
  }
}
function renderProjectScope() {
  const host=$('#project-scope-actions');host.replaceChildren();host.hidden=!projectFilter;$('#project-scope-menu').hidden=!projectFilter;if(!projectFilter)return;
  host.append(Object.assign(n('p',projectFilter),{className:'project-path'}),projectActionButton(projectFilter,removedProject(projectFilter)?'restore':'remove',removedProject(projectFilter)?'Restore project':'Remove project'));
}
function renderTaskProjectActions(workspace, removed=false) {
  const host=$('#task-project-actions');host.replaceChildren();if(!workspace)return;
  const copy=button('Copy path',()=>copySavedValue(workspace,'Project path'),'text-button');copy.dataset.staleSafe='true';copy.setAttribute('aria-label','Copy project path');
  host.append(Object.assign(n('p',workspace),{className:'project-path'}),copy);if(!removed&&!removedProject(workspace))host.append(projectActionButton(workspace,'remove','Remove project'));
}
function reviewProjectRemoval(workspace) {
  if(!workspace||projectPending.has(workspace))return;
  projectRemoval={workspace,opener:document.activeElement};const dialog=$('#project-removal-dialog');dialog._returnFocus=projectRemoval.opener;$('#project-removal-name').textContent=projectTitle(workspace);$('#project-removal-path').textContent=workspace;
  $('#project-removal-error').textContent='';$('#project-removal-error').hidden=true;$('#project-removal-confirm').disabled=false;$('#project-removal-confirm').textContent='Remove from dashboard';$('#project-removal-cancel').disabled=false;renderProjectRemovalReview();
  dialog.showModal();$('#project-removal-cancel').focus();
}
function renderProjectRemovalReview(){
  const review=projectRemoval,uncertain=review&&projectRemovalUncertain(review.workspace),confirm=$('#project-removal-confirm'),reconcile=$('#project-removal-reconcile'),message=$('#project-removal-uncertain');
  if(!review)return;
  message.hidden=!uncertain;reconcile.hidden=!uncertain;
  if(uncertain){message.textContent='Request '+uncertain.requestId+' may have reached the dashboard. Refresh status and reconcile before submitting another removal request.';confirm.disabled=true;reconcile.onclick=()=>reconcileProjectRemoval(review.workspace);}
  else{message.textContent='';confirm.disabled=projectPending.has(review.workspace);}
}
function closeProjectRemoval() {
  if(projectRemoval&&projectPending.has(projectRemoval.workspace))return;
  const opener=projectRemoval?.opener;projectRemoval=null;$('#project-removal-dialog').close();if(typeof returnDialogFocus==='function')returnDialogFocus(opener);else if(opener?.isConnected)opener.focus();
}
$('#project-removal-cancel').onclick=closeProjectRemoval;
if(typeof bindDialogBehavior==='function')bindDialogBehavior($('#project-removal-dialog'),closeProjectRemoval);else $('#project-removal-dialog').addEventListener('cancel',event=>{event.preventDefault();closeProjectRemoval();});
$('#project-removal-confirm').onclick=()=>{if(projectRemoval)changeProject(projectRemoval.workspace,'remove');};
async function reconcileProjectRemoval(workspace){
  const request=projectRemovalUncertain(workspace);if(!request)return;
  const control=$('#project-removal-reconcile');control.disabled=true;
  try{await refresh();const removed=removedProject(workspace);if(removed===(request.action==='remove')){setProjectRemovalUncertain(workspace,null);projectNoticeState={workspace,action:request.action,expiresAt:Date.now()+12000,reconciled:true};if(projectRemoval?.workspace===workspace){projectRemoval=null;$('#project-removal-dialog').close();}}
    else{setProjectRemovalUncertain(workspace,null);if(projectRemoval?.workspace===workspace){$('#project-removal-uncertain').textContent='Status reconciled: no removal was recorded. You may submit a new removal request.';$('#project-removal-uncertain').hidden=false;$('#project-removal-confirm').disabled=false;}projectNoticeState={workspace,action:request.action,error:'Status reconciled: no removal was recorded. Submit a new removal request only if you still want to hide this project.'};}}
  catch(error){if(projectRemoval?.workspace===workspace){$('#project-removal-uncertain').textContent='Status is still unavailable. Do not retry the mutation yet: '+error.message;$('#project-removal-uncertain').hidden=false;}}
  finally{control.disabled=false;renderProjectNotice();}
}
async function changeProject(workspace,action) {
  if(projectPending.has(workspace))return;projectPending.set(workspace,action);seq++;
  const dialog=action==='remove'&&projectRemoval?.workspace===workspace;
  const requestId=typeof mutationRequestId==='function'?mutationRequestId():'request-'+Date.now()+'-'+Math.random().toString(16).slice(2);
  if(dialog){$('#project-removal-confirm').disabled=true;$('#project-removal-confirm').textContent='Removing…';$('#project-removal-cancel').disabled=true;$('#project-removal-error').hidden=true;}
  if(latestData){renderProjectManager(latestData);renderProjectScope();}renderProjectNotice();if(chosen?.workspace===workspace){renderTaskProjectActions(workspace);if(latestRun)renderLiveControls(latestRun);}
  try {
    await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace,action})});seq++;
    setProjectRemovalUncertain(workspace,null);
    if(latestData){latestData={...latestData,removed_projects:(latestData.removed_projects||[]).filter(project=>project.workspace!==workspace)};if(action==='remove'){latestData.removed_projects.push({workspace,removed_at:new Date().toISOString()});latestData.workspaces=(latestData.workspaces||[]).filter(path=>path!==workspace);latestData.runs=(latestData.runs||[]).filter(run=>run.workspace!==workspace);latestData.conversations=(latestData.conversations||[]).filter(doc=>doc.attachment?.workspace!==workspace);}}
    if(action==='remove'){
      const selectedTask=chosen?.workspace===workspace,selectedChat=latestConversation?.attachment?.workspace===workspace;
      const leave=(currentView==='task-detail'&&selectedTask)||(currentView==='draft-conversation'&&selectedChat)||(currentView==='tasks'&&projectFilter===workspace);
      if(selectedTask){chosen=null;latestRun=null;}if(selectedChat){activeConversation=null;latestConversation=null;}if(projectFilter===workspace)projectFilter='';if(leave)filterTasks('all','');
    }
    projectNoticeState={workspace,action,expiresAt:Date.now()+12000};if(dialog){projectRemoval=null;$('#project-removal-dialog').close();}
  } catch(error) {
    if(error?.uncertain){setProjectRemovalUncertain(workspace,{action,requestId});if(dialog){$('#project-removal-error').hidden=true;$('#project-removal-cancel').disabled=false;renderProjectRemovalReview();}projectNoticeState={workspace,action,uncertain:true,requestId};}
    else if(dialog){$('#project-removal-error').textContent=error.message;$('#project-removal-error').hidden=false;}
    else projectNoticeState={workspace,action,error:error.message};
  } finally {
    projectPending.delete(workspace);seq++;if(dialog){if(!projectRemovalUncertain(workspace))$('#project-removal-confirm').disabled=false;$('#project-removal-confirm').textContent='Remove from dashboard';$('#project-removal-cancel').disabled=false;}
    if(latestData){renderTasks(latestData);renderConversations(latestData);renderProjectManager(latestData);}renderProjectNotice();if(dialog&&!projectRemoval){$('#project-notice').tabIndex=-1;$('#project-notice').focus();}refresh();
  }
}
async function post(url, payload) {
  if(payload?.run&&taskArchiveBlocked(payload.run)){dashboardNotice('Restore this archived task before changing it.');return null;}
  if(payload?.workspace&&projectBlocked(payload.workspace)){projectNoticeState={workspace:payload.workspace,action:'restore',error:'Restore this project before changing its tasks.'};renderProjectNotice();return null;}
  try {
    const result = await api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    actionProblem = ''; dashboardNotice(''); await refresh(); return result;
  } catch (error) { actionProblem = error.message; dashboardNotice(actionProblem); return null; }
}

let taskDeleteReview=null;
function deletionRequest(url,payload){return api(url,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});}
function deleteRequestKey(run){return 'task-delete:'+run.run;}
function updateTaskDeleteConfirm(){
  const review=taskDeleteReview;if(!review)return;
  const matches=$('#task-delete-confirmation').value===review.preview?.confirmation;
  $('#task-delete-confirm').disabled=review.busy||review.blocked||!review.preview?.preview_id||(!review.uncertain&&!matches);
  $('#task-delete-confirm').textContent=review.busy?'Deleting…':review.blocked?'Confirmation no longer valid':review.uncertain?'Retry same deletion':'Delete selected files';
}
function renderTaskDeletePreview(){
  const review=taskDeleteReview,preview=review?.preview;if(!review)return;
  $('#task-delete-name').textContent=taskTitle(review.run)+' · '+(preview?.workspace||review.run.workspace||'');
  $('#task-delete-phrase').textContent=preview?.confirmation||'the task name after the preview loads';
  const list=$('#task-delete-scope');list.replaceChildren();
  for(const entry of preview?.scope||[]){const item=n('li');item.append(n('strong',human(entry.kind||'File')),n('code',entry.path||String(entry)));list.append(item);}
  $('#task-delete-worktree').disabled=review.busy||review.uncertain||!preview?.can_include_worktree;
  $('#task-delete-branch').disabled=review.busy||review.uncertain||!preview?.can_include_branch||!$('#task-delete-worktree').checked;
  $('#task-delete-scope-note').textContent=preview?.worktree_unavailable_reason||'';
  updateTaskDeleteConfirm();
}
async function loadTaskDeletePreview(){
  const review=taskDeleteReview;if(!review||review.busy||review.uncertain)return;
  review.busy=true;review.preview=null;$('#task-delete-confirmation').value='';$('#task-delete-error').hidden=true;renderTaskDeletePreview();
  try{review.preview=await deletionRequest('/api/tasks/delete-preview',{workspace:review.run.workspace,run:review.run.run,include_worktree:$('#task-delete-worktree').checked,include_branch:$('#task-delete-worktree').checked&&$('#task-delete-branch').checked});}
  catch(error){$('#task-delete-error').textContent=error.message;$('#task-delete-error').hidden=false;}
  finally{review.busy=false;renderTaskDeletePreview();}
}
function renderPendingDeletions(host,data){
  const pending=data.pending_deletions||[];if(!pending.length)return;
  const section=card('','settings-block');section.id='pending-deletions';section.append(n('h2','Incomplete deletions'),n('p','Files may already have been removed. Review the saved receipt before retrying the same scope.'));
  for(const receipt of pending){
    const row=card('','managed-project');row.append(n('h3',receipt.title||'Saved deletion'),n('p',(receipt.errors||[]).join(' ')||'The earlier deletion has no final receipt.'),button('Review incomplete deletion',()=>reviewTaskDelete({run:receipt.run,workspace:receipt.workspace,task:receipt.title},receipt)));
    section.append(row);
  }
  host.append(section);
}
async function reviewTaskDelete(run,receipt=null){
  if((taskReadError&&!receipt)||taskDeleteReview?.busy)return;
  const saved=receipt?{preview:receipt,blocked:receipt.retry_blocked}:readSavedRequest(deleteRequestKey(run));taskDeleteReview={run,opener:document.activeElement,preview:saved?.preview||null,uncertain:!!saved?.preview,blocked:!!saved?.blocked,busy:false};
  $('#task-delete-worktree').checked=false;$('#task-delete-branch').checked=false;$('#task-delete-confirmation').value='';$('#task-delete-error').hidden=true;$('#task-delete-result').hidden=true;
  $('#task-delete-dialog').showModal();$('#task-delete-cancel').focus();
  if(taskDeleteReview.uncertain){$('#task-delete-error').textContent=taskDeleteReview.blocked?'The saved confirmation cannot safely remove the retained resource. Close this dialog and inspect the remaining files or branch.':'The earlier deletion did not return a complete receipt. Retry the same saved deletion to reconcile its result; the scope cannot be expanded.';$('#task-delete-error').hidden=false;renderTaskDeletePreview();}
  else await loadTaskDeletePreview();
}
function closeTaskDelete(){
  if(taskDeleteReview?.busy)return;const opener=taskDeleteReview?.opener;taskDeleteReview=null;$('#task-delete-dialog').close();if(opener?.isConnected)opener.focus();
}
async function confirmTaskDelete(){
  const review=taskDeleteReview;if(!review||review.busy||$('#task-delete-confirm').disabled)return;
  const preview=review.preview;review.busy=true;persist(deleteRequestKey(review.run),JSON.stringify({preview}));renderTaskDeletePreview();$('#task-delete-error').hidden=true;
  try{
    const result=await deletionRequest('/api/tasks/delete',{preview_id:preview.preview_id,confirmation:preview.confirmation});
    if(result.status!=='deleted'){
      review.uncertain=true;review.blocked=result.retry_blocked===true;persist(deleteRequestKey(review.run),JSON.stringify({preview,blocked:review.blocked}));
      const remaining=(result.remaining||[]).map(item=>typeof item==='string'?item:item.path||item.kind).filter(Boolean).join(', ');
      $('#task-delete-error').textContent='Deletion is incomplete. '+(result.errors||[]).map(item=>typeof item==='string'?item:item.error||item.message||JSON.stringify(item)).join(' ') +(remaining?' Remaining: '+remaining:'')+(review.blocked?' The old confirmation is disabled; retained work needs a new review.':' Retry uses the same approved scope.');
      $('#task-delete-error').hidden=false;return;
    }
    persist(deleteRequestKey(review.run),'');review.busy=false;closeTaskDelete();
    if(chosen?.run===review.run.run){chosen=null;latestRun=null;filterTasks('all',review.run.workspace);}
    dashboardNotice('Deleted “'+taskTitle(review.run)+'” and the confirmed files.');await refresh();
  }catch(error){review.uncertain=!error.rejected;if(error.rejected)persist(deleteRequestKey(review.run),'');$('#task-delete-error').textContent=error.message+(review.uncertain?' Retry the same deletion to reconcile its receipt.':'');$('#task-delete-error').hidden=false;}
  finally{review.busy=false;if(taskDeleteReview===review)renderTaskDeletePreview();}
}
$('#task-delete-confirmation').oninput=updateTaskDeleteConfirm;
$('#task-delete-worktree').onchange=loadTaskDeletePreview;$('#task-delete-branch').onchange=loadTaskDeletePreview;
$('#task-delete-cancel').onclick=closeTaskDelete;$('#task-delete-confirm').onclick=confirmTaskDelete;
$('#task-delete-dialog').addEventListener('cancel',event=>{event.preventDefault();closeTaskDelete();});

function concise(text, limit=180) {
  const value=String(text||'').replace(/\s+/g,' ').trim();
  return value.length>limit?value.slice(0,limit-1).replace(/\s+\S*$/,'')+'…':value;
}
function taskTitle(run) {
  const outcome=!String(run.goal?.origin||'').startsWith('migration_draft')&&run.goal?.body?.intended_outcome;
  const description=String(run.display_title||run.task||outcome||'').split('Conversation reference:')[0].trim();
  const heading=description.match(/^\s*#{1,6}\s+([^\n]+)/);
  const raw=(heading?heading[1]:description).replace(/\s+/g,' ').trim();
  const recovered=basename(run.run).replace(/^\d{8}-\d{6}-/,'').replace(/-[a-f0-9]{8,64}$/i,'').replace(/-/g,' ');
  const first=(raw||recovered||'Untitled task').split(/(?<=[.!?])\s+(?=[A-Z])/)[0];
  return concise(first,86);
}
function projectTitle(workspace) { return projectLabel(workspace); }
// The project's own name (its folder), used for sidebar group headers and the
// scope shown on new-conversation controls before anything is submitted.
function projectLabel(workspace) { return basename(workspace); }

function taskStarted(run) {
  const explicit=Date.parse(run.created_at);
  if(Number.isFinite(explicit))return explicit;
  const stages=[...(run.stages||[]),run.active_stage||{}].map(stage=>Date.parse(stage.started_at)).filter(Number.isFinite);
  return stages.length?Math.min(...stages):0;
}
function taskIdentity(run) {
  const time=taskStarted(run);
  return time?'Started '+new Date(time).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit',second:'2-digit'}):'Saved task · '+basename(run.run).slice(0,22);
}
const ARCHIVE_SUGGESTION_AGE_MS=48*60*60*1000;
function archiveSuggestionKind(run,now=Date.now()) {
  if(!run?.run||run.error||run.state_error)return '';
  const checkpoint=Date.parse(run.monitor?.checkpoint_updated||run.updated_at||run.created_at);
  if(!Number.isFinite(checkpoint)||now-checkpoint<ARCHIVE_SUGGESTION_AGE_MS)return '';
  if(run.status==='TASK_COMPLETE')return 'Completed';
  if(run.questions?.length||run.user_request?.decision_needed)return '';
  if(run.status!=='RUNNING'||run.monitor?.live?.state==='alive')return '';
  if(run.monitor?.live?.state==='exited')return 'Recorded worker exited';
  if(!run.active_stage?.stage&&run.monitor?.live?.state==='none')return 'No worker recorded; check before archiving';
  return '';
}
function archiveSuggestionDismissed() {
  try { const saved=JSON.parse(stored('archive-suggestions-dismissed','{}'));return saved&&typeof saved==='object'&&!Array.isArray(saved)?saved:{}; }
  catch { return {}; }
}
function renderArchiveSuggestions(runs) {
  const host=$('#archive-suggestions'),dismissed=archiveSuggestionDismissed();
  const candidates=runs.map(run=>({run,kind:archiveSuggestionKind(run)})).filter(item=>item.kind&&!dismissed[item.run.run]);
  host.replaceChildren();host.hidden=!candidates.length;
  if(!candidates.length)return;
  host.append(n('h2','Older work to review'),n('p','These tasks are complete or have an old, unconfirmed running status. Review each one before archiving; files and history stay saved.'));
  for(const {run,kind} of candidates){
    const row=card('','archive-suggestion-row'),copy=card('','archive-suggestion-copy');
    copy.append(n('strong',taskTitle(run)),n('span',projectTitle(run.workspace)+' · '+kind));
    // Stable focus keys keep keyboard focus on the same control across the
    // polling re-render of this section.
    const review=button('Review archive',()=>reviewTaskArchive(run),'text-button'),dismiss=button('Dismiss',()=>{const next=archiveSuggestionDismissed();next[run.run]=true;persist('archive-suggestions-dismissed',JSON.stringify(next));renderArchiveSuggestions(runs);},'text-button');
    review.dataset.focusKey='archive-review:'+run.run;dismiss.dataset.focusKey='archive-dismiss:'+run.run;
    row.append(copy,review,dismiss);
    host.append(row);
  }
}
function operationalRequest(run) {return run.status==='WAITING_FOR_USER'&&run.human_request_authorized===true&&typeof run.human_escalation?.request_id==='string'&&!!run.human_escalation.request_id&&typeof run.human_escalation.request_token==='string'&&!!run.human_escalation.request_token&&['operational_exhaustion','blocker'].includes(run.human_escalation.scope);}
function statusInfo(run) {
  const envelope=run.human_escalation,authorized=run.human_request_authorized===true&&typeof envelope?.request_id==='string'&&!!envelope.request_id&&typeof envelope.request_token==='string'&&!!envelope.request_token,scope=authorized?envelope.scope:null;
  const status=run.status||'', questions=authorized?run.questions||[]:[], request=authorized?run.user_request||{}:{};
  const info=(group,tone,label,action,reason,tab='interview')=>({group,tone,label,action,reason,tab,
    stateLabel:group==='running'?'Worker confirmed running':group==='attention'?'Waiting for your decision':group==='complete'?'Complete':group==='stopped'?(tone==='failed'?'Internally blocked':'Stopped at a checkpoint'):label});
  if(run.error||run.state_error)return info('stopped','failed','Unavailable','Inspect issue',run.error||run.state_error);
  if(status==='TASK_COMPLETE'&&run.completion_current===false)return {...info('stopped','','Checks need review','Inspect checks','Completion was recorded earlier, but current source and evidence do not establish completion now. Inspect the saved checks.','execution'),stateLabel:'Completion needs verification'};
  if(status==='TASK_COMPLETE')return info('complete','complete','Completed','View result','The runner recorded this task as complete. No reply is needed.','execution');
  if(status==='WAITING_FOR_DEPENDENCY')return info('stopped','','Waiting for prerequisite','View progress',run.stop_reason||'Waiting for another task to finish and pass review. No action needed from you.','execution');
  if(status==='DRY_RUN')return info('other','','Preview only','View preview','This is a saved dry run. It did not start implementation.','execution');
  if(status==='RESOLVER_PENDING'||(!authorized&&['WAITING_FOR_USER','AWAITING_GOAL_APPROVAL','BLOCKED_HUMAN'].includes(status)))return info('stopped','','Awaiting Resolver','Inspect details','No current Resolver request is published. Saved drafts and internal questions are available for inspection, not for answers or approval.');
  if(status.startsWith('PAUSED_RESOLVER'))return info('stopped','','Resolver paused','Inspect details',run.stop_reason||'Resolver is paused. No execution or additional allowance is authorized.');
  if(operationalRequest(run))return info('attention','attention','Resolver needs information','Respond to Resolver',request.decision_needed||questions[0]?.question||'Provide corrective information or leave the task paused.');
  if(['PAUSED_PROVIDER_UNCERTAIN','PAUSED_UNCERTAIN_STAGE'].includes(status))return info('stopped','failed','Interrupted','Review interruption',run.stop_reason||'A provider attempt ended without a confirmed result. Review saved work before continuing.');
  // Saved questions and user requests may remain after a stage resumes. Honor
  // the current paused/running state before interpreting these old fields.
  if(status==='RUNNING') {
    const stage=run.active_stage||{};
    if(run.monitor?.live?.state==='alive')return info('running','running',stage.stage==='astra_resolve'?'Recovering':'Running','View progress',stageName(run)+' · Worker verified alive');
    if(stage.stage&&run.monitor?.live?.state==='exited')return info('stopped','attention','Worker stopped','Review checkpoint','The saved step says running, but its worker has exited. Review the checkpoint before continuing.');
    if(stage.stage&&!stage.finished_at&&stage.exit_code==null)return info('stopped','attention','Worker unverified','Check worker status','No live worker is confirmed. '+(run.monitor?.live?.label||'Process inspection is unavailable.')+' Inspect the saved checkpoint before continuing.');
    if(archiveSuggestionKind(run)==='No worker recorded; check before archiving')return info('stopped','attention','Status needs review','Check saved status','This run still says running, but no worker was recorded and its checkpoint has not changed for two days. Check it before continuing or archiving.');
    if(run.monitor?.orchestration_batch&&run.monitor?.next_stage==='orchestrator')return info('stopped','attention','Worker unverified','Check worker status','Orchestrator · Saved batch statuses do not confirm live workers. Inspect the saved checkpoint before continuing.');
    if(run.review_token&&run.review_criteria?.length&&run.review_criteria.every(criterion=>run.human_reviews?.[criterion.id]?.token===run.review_token))return info('stopped','','Ready to finish','Finish task','Your review is saved. Finish the task to run its final completion check.','execution');
    return info('stopped','','Ready to continue','Open to continue','The task is at a saved checkpoint. Open it to continue when you are ready.');
  }
  if(status==='PAUSED_INVALID_OUTPUT'&&/discovery|glm_revise|astra_challenge|astra_finalize/.test(run.active_stage?.stage||run.stage||''))return info('stopped','failed','Planning needs retry','Retry planning',(run.stop_reason?run.stop_reason+' · ':'')+(String(run.stop_reason||'').includes('actual saved feedback event')?'The planning draft cited user feedback that has no matching saved feedback event. The draft was rejected. Retry planning to generate a fresh draft; final plan approval is still required.':'The planning draft failed validation and was rejected. Retry planning to generate a fresh draft; final plan approval is still required.'));
  if(status==='PAUSED_INTERVENTION'&&run.interventions?.stop_intent)return info('stopped','attention','Stopped','Inspect details','Stopped at your request after a saved step. Saved work, history and receipts are kept; this conversation launches no further stages.','execution');
  if(status==='PAUSED_INTERVENTION')return info('stopped','attention','Paused','Resume task',String(run.stop_reason||'').includes('feedback was applied')?'Your feedback has been applied. Resume to update the plan.':'Paused at a saved step. Resume when you’re ready.');
  if(/INVALID_OUTPUT|REPORT_REPAIR|PERMISSION_RECONCILIATION|ORCHESTRATOR_|FAILED/.test(status))return info('stopped','failed','Internally blocked','Inspect failure',run.stop_reason||'An internal step failed. Inspect the saved failure, correct the cause, then retry.');
  if(status==='BLOCKED_HUMAN'&&(questions.length||request.decision_needed))return info('attention','attention','Decision needed','View decision',request.decision_needed||questions[0].question);
  if(/PAUSED|BLOCKED|FAILED/.test(status))return info('stopped',/INVALID|FAILED/.test(status)?'failed':'attention','Paused','View pause reason',run.stop_reason||'The runner stopped at a checkpoint. Review its saved state before continuing.');
  const reviews=run.review_token?(run.review_criteria||[]).filter(criterion=>run.human_reviews?.[criterion.id]?.token!==run.review_token):[];
  if(status==='WAITING_FOR_USER'&&scope==='human_review'&&reviews.length)return info('attention','attention','Review output','Review '+reviews.length+' '+(reviews.length===1?'item':'items'),request.decision_needed||'Inspect the saved output and record your review.','execution');
  if(status==='WAITING_FOR_USER'&&scope==='human_review'&&run.review_token&&run.review_criteria?.length&&!reviews.length)return info('stopped','','Ready to finish','Finish task','Your review is saved. Finish the task to run its final completion check.','execution');
  if(questions.length&&['clarification','permission','goal_change'].includes(scope))return info('attention','attention','Answer needed','Answer '+questions.length+' '+(questions.length===1?'question':'questions'),questions[0].question||'Open the conversation to answer the pending question.');
  if(status==='AWAITING_GOAL_APPROVAL') {
    if(scope==='goal_approval'&&run.goal_token&&run.goal?.approval_status!=='approved'&&planReady(run))return info('attention','attention','Approve plan','Review plan',run.goal?.body?.intended_outcome||'Review the finalized plan before implementation starts.');
    return info('stopped','attention','Planning checkpoint','Open planning','The saved plan is not currently ready for approval. Inspect the planning checkpoint.');
  }
  if(status==='WAITING_FOR_USER'){
    if(request.decision_needed)return info('attention','attention','Decision needed','View request',request.decision_needed);
    return info('stopped','','Ready to continue','Resume task','No unresolved decision is recorded. Resume from the saved checkpoint.');
  }
  return info('other','',human(status)||'Unknown','Inspect task','The saved state does not identify a current action. Open the task for details.');
}
function runtimeLabel(run) {
  const info=statusInfo(run),live=run.monitor?.live||{},active=run.active_stage||{};
  if(run.status==='RUNNING') {
    if(live.state==='alive')return (info.label==='Recovering'?'Recovering':'Running')+' · worker verified alive';
    if(live.state!=='exited'&&active.stage&&!active.finished_at&&active.exit_code==null)return 'Activity reported · live process not confirmed';
  }
  if(info.label==='Answer needed')return 'Waiting on you · Answer needed';
  if(info.label==='Approve plan')return 'Waiting on you · Approve plan';
  if(info.label==='Decision needed')return 'Waiting on you · Decision needed';
  if(info.label==='Interrupted')return 'Interrupted · saved work preserved';
  if(info.label==='Paused')return 'Paused · '+(String(run.stop_reason||'').includes('feedback')?'Feedback applied':'checkpoint preserved');
  if(info.label==='Stopped')return 'Stopped · no further stages run';
  if(info.label==='Completed')return 'Completed · recorded evidence';
  return info.label;
}
function recordedTime(run) {
  for(const value of [run.monitor?.checked_at,run.monitor?.checkpoint_updated,run.monitor?.log_updated,run.active_stage?.started_at,run.updated_at,run.created_at]) {
    const at=Date.parse(value);if(Number.isFinite(at))return at;
  }
  return null;
}
function recordedTimeLabel(run) {
  const at=recordedTime(run);return at?new Date(at).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}):'time unavailable';
}
function lastConfirmedStage(run) {
  return [...(run.stages||[])].findLast(stage=>stage?.stage&&stage.finished_at&&stage.exit_code===0&&!stage.rejected&&!stage.interrupted&&!stage.timed_out&&!stage.abandoned)
    || [...(run.monitor?.history||[])].findLast(stage=>stage?.stage&&stage.finished_at&&stage.exit_code===0&&!stage.rejected&&!stage.interrupted&&!stage.timed_out&&!stage.abandoned)
    || null;
}
function stateFacts(run) {
  const info=statusInfo(run),monitor=run.monitor||{},live=monitor.live||{},active=run.active_stage||{};
  const confirmed=lastConfirmedStage(run),activeStage=active.stage||run.stage||'',savedStage=confirmed?.stage||activeStage||monitor.next_stage||'';
  const isLive=run.status==='RUNNING'&&live.state==='alive';
  const savedCurrent=['WAITING_FOR_USER','AWAITING_GOAL_APPROVAL'].includes(run.status)&&active.stage&&!active.finished_at&&active.exit_code==null;
  const step=isLive&&activeStage
    ?'Current step · '+stageName({...run,stage:activeStage})
    :savedCurrent
      ?'Current saved step · '+stageName({...run,stage:activeStage})
      :confirmed
        ?'Last confirmed step · '+stageName({...run,stage:confirmed.stage})+(info.group==='complete'?' · completion recorded':'')
        :'Last confirmed step unavailable';
  const responsibleStage=isLive||savedCurrent?active:savedStage?confirmed||{stage:savedStage}:active;
  // A saved monitor role can describe an old live process. Once the process is
  // no longer live, identify the role from the last authoritative saved step.
  const role=responsibleStage?.stage?stageName({...run,stage:responsibleStage.stage}).split(' · ')[0]:roleDisplayName(monitor.active_role||'unavailable');
  const question=run.human_request_authorized===true?(run.questions||[])[0]:null;
  const blocker=info.group==='attention'?(question?.question||run.user_request?.decision_needed||info.reason):info.group==='stopped'?info.reason:'No blocker';
  const freshness=isLive?'Current verification · PID '+(live.pid||'recorded')+' checked '+recordedTimeLabel(run):info.group==='complete'?'Recorded evidence · '+(run.validation?.source_revision?'source '+run.validation.source_revision:'freshness unavailable'):run.status==='PAUSED_PROVIDER_UNCERTAIN'||run.status==='PAUSED_UNCERTAIN_STAGE'?'Verification unavailable · checkpoint saved '+recordedTimeLabel(run):'Recorded evidence · updated '+recordedTimeLabel(run);
  return {state:runtimeLabel(run),step,role,blocker,freshness,objective:monitor.objective||run.astra_plan?.current_assignment?.objective||run.goal?.body?.intended_outcome||run.current_task?.objective||'No current objective has been recorded.'};
}
function completionEvidenceStatement(run) {
  const source=run.validation?.source_revision||'the recorded source';
  const recorded=run.validation?.checks?.find?.(check=>typeof check==='string'&&/checks? (were )?recorded/i.test(check))||run.validation?.checks?.find?.(check=>typeof check?.name==='string'&&/checks? (were )?recorded/i.test(check?.name));
  const summary=typeof recorded==='string'?recorded:recorded?.name;
  return (summary||'Recorded checks are available')+' for source '+source+'. Current freshness is unavailable.';
}
function completionTimestamp(run) {
  const raw=typeof run.completed_at==='string'?run.completed_at:'';
  if(!raw.trim())return {state:'unavailable',raw:'',text:'Completion timestamp unavailable.'};
  const at=Date.parse(raw);
  if(!Number.isFinite(at))return {state:'invalid',raw,text:'Completion timestamp unavailable — saved value is invalid.'};
  return {state:'recorded',raw,text:new Date(at).toLocaleString(undefined,{dateStyle:'medium',timeStyle:'medium'})};
}
function completionRecord(run) {
  const timestamp=completionTimestamp(run),record=card('','completion-record');
  record.dataset.completionRecordState=timestamp.state;
  const label=n('p','COMPLETION RECORDED'),value=n(timestamp.state==='recorded'?'time':'p',timestamp.text);
  label.className='monitor-kicker';value.dataset.completionTimestamp=timestamp.raw;
  if(timestamp.state==='recorded'){value.dateTime=timestamp.raw;value.title='Saved completion timestamp: '+timestamp.raw;}
  record.append(label,value);return record;
}
function workspaceRowFacts(run) {
  const facts=stateFacts(run),info=statusInfo(run),isLive=run.monitor?.live?.state==='alive';
  const step=info.group==='attention'?'Blocker: '+facts.blocker:info.group==='stopped'?'Blocker: '+facts.blocker:'Step: '+facts.step.replace(/^(Current|Last confirmed|Next) step · /,'');
  return {state:facts.state,step,freshness:facts.freshness,updated:'Updated '+recordedTimeLabel(run),action:info.action,live:isLive};
}
function taskGroups() {return [
  ['attention','Waiting on you','Questions, plan approval, or a review that only you can resolve.'],
  ['running','In progress','Workers confirmed running by process inspection. Saved activity does not confirm a live process.'],
  ['stopped','Paused / issues','Stopped, interrupted, unavailable, or ready to continue. These are separate from unanswered questions.'],
  ['complete','Completed','Finished tasks. No reply is needed.'],
  ['other','Other saved tasks','Previews and tasks whose next action is not known.']
];}
const workspaceFilterDefinitions=[
  ['attention','Waiting on you'],['running','In progress'],['stopped','Paused / issues'],['complete','Completed']
];
function workspacePriority(run) {
  const info=statusInfo(run),live=run.monitor?.live?.state;
  if(info.group==='attention')return 1;
  if(info.group==='stopped')return 2;
  if(info.group==='running'&&live==='alive')return 3;
  if(info.group==='running')return 4;
  if(info.group==='complete')return 6;
  return 7;
}
function orderedWorkspaceRuns(runs) {
  return [...runs].sort((left,right)=>workspacePriority(left)-workspacePriority(right)||taskStarted(right)-taskStarted(left)||String(left.run||'').localeCompare(String(right.run||'')));
}
function workspaceSection(run) {
  const priority=workspacePriority(run);
  return priority===1?'attention':priority===2?'stopped':priority===3||priority===4?'running':priority===6?'complete':'other';
}
function isPreviewRun(run) { return run?.status === 'DRY_RUN'; }
function taskSignature(run) { return String(run?.task||'').replace(/\s+/g,' ').trim(); }
function projectCurrentRuns(runs) {
  const sorted=(runs||[]).filter(run=>run.run&&!run.error&&!isPreviewRun(run)).slice().sort((a,b)=>taskStarted(b)-taskStarted(a));
  const seen=new Set();
  return sorted.filter(run=>{const key=run.workspace+'\0'+taskSignature(run);if(seen.has(key))return false;seen.add(key);return true;});
}
function projectRunSummary(runs) {
  const actual=(runs||[]).filter(run=>run.run&&!run.error), work=actual.filter(run=>!isPreviewRun(run)),current=projectCurrentRuns(work);
  return {
    current:current.length,
    active:current.filter(run=>['running','attention'].includes(statusInfo(run).group)).length,
    paused:current.filter(run=>statusInfo(run).group==='stopped').length,
    previous:work.length-current.length,
    previews:actual.filter(isPreviewRun).length
  };
}
function projectSummaryLabel(summary) {
  const parts=[];
  if(summary.current)parts.push(summary.current+' current '+(summary.current===1?'task':'tasks'));
  if(summary.previous)parts.push(summary.previous+' previous '+(summary.previous===1?'attempt':'attempts'));
  if(summary.previews)parts.push(summary.previews+' '+(summary.previews===1?'dry run':'dry runs'));
  return parts.join(' · ') || 'No saved tasks';
}
function taskSelection(filter=taskFilter,project=projectFilter) {
  const projectKey=projectKeyForPath(project);
  return 'tasks'+(filter!=='all'?'&filter='+encodeURIComponent(filter):'')+(project?'&project='+encodeURIComponent(projectKey||project):'');
}
function unavailableShortProject() {
  dashboardNotice('This project link is unavailable. Choose the project from All work.');
  filterTasks('all','');
}
function restorePendingShortProject(data) {
  if(!pendingShortProject)return false;
  const pending=pendingShortProject;pendingShortProject=null;
  const project=projectPathForKey(pending.key,data);
  if(project){filterTasks(pending.filter,project);return true;}
  unavailableShortProject();return true;
}
function taskScopeTitle() {return projectFilter?projectTitle(projectFilter):taskFilter==='all'?'All work':taskGroups().find(group=>group[0]===taskFilter)?.[1]||'All work';}
function badge(run) { const info = statusInfo(run), element = n('span',runtimeLabel(run)); element.className = 'badge '+info.tone; element.title = run.status || run.error || ''; return element; }
function taskStatusBadge(run) {
  const element=badge(run);
  if(statusInfo(run).group!=='complete')return element;
  const timestamp=completionTimestamp(run);
  if(timestamp.state!=='recorded')return element;
  element.textContent='Completed · recorded '+timestamp.text;
  element.dataset.completionTimestamp=timestamp.raw;
  element.title='Saved completion timestamp: '+timestamp.raw;
  return element;
}
function jointPlanning(run) { return run.model_settings?.joint_planning === true; }
function planningMode(run) { return run.monitor?.workflow_mode==='glm_final_audit_v2'?'Builder-led · '+roleDisplayName('completion')+' final audit':run.monitor?.workflow_mode==='glm_first_v1'?'Builder-led · '+roleDisplayName('completion')+' milestone reviews':jointPlanning(run)?'Requirements planning · Independent review':run.model_settings?.engine?'Legacy planning':'Saved workflow unavailable'; }
function planningSpeaker(run) {
  if(run.model_settings?.engine==='gocode'){
    return roleDisplayName(['glm_draft','glm_revise'].includes(run.goal?.origin)?'planner':'plan_reviewer');
  }
  return jointPlanning(run) && run.goal?.origin === 'astra_finalize' ? roleDisplayName('plan_reviewer') : roleDisplayName('planner');
}
function roleDisplayName(name) {
  const catalogue=globalThis.AUTOCODE_ROLE_NAMES,key=String(name||'').trim().toLowerCase().replaceAll('_',' ');
  return catalogue.roles[catalogue.aliases[key]||name]||human(name);
}
function planReady(run) { return !jointPlanning(run) || (run.status==='AWAITING_GOAL_APPROVAL' && run.goal?.origin==='astra_finalize'); }
function stageName(run) {
  const stage=String(run.stage||'').replace(/_report_repair$/,''),catalogue=globalThis.AUTOCODE_ROLE_NAMES;
  const entry=catalogue.modes[run.monitor?.workflow_mode]?.[stage]||catalogue.stages[stage];
  return entry?entry.role+' · '+entry.activity:stage==='unavailable'?'Step unavailable':human(stage)||'Not started';
}
function statusAge(value) {
  const at=Date.parse(value);if(!Number.isFinite(at))return 'Not recorded';
  const seconds=Math.max(0,Math.floor((Date.now()-at)/1000));
  return seconds<60?seconds+'s ago':seconds<3600?Math.floor(seconds/60)+'m ago':Math.floor(seconds/3600)+'h '+Math.floor(seconds%3600/60)+'m ago';
}
function hasOrchestration(run) {
  return run.monitor?.orchestration?.enabled===true||run.stage==='orchestrator'||run.active_stage?.stage==='orchestrator'||run.monitor?.next_stage==='orchestrator'||!!run.monitor?.orchestration_batch||!!run.monitor?.orchestration_history?.length||(run.stages||[]).some(stage=>stage.stage==='orchestrator');
}
function stageSucceeded(stage) {
  return !stage.rejected&&!stage.abandoned&&!stage.interrupted&&!stage.timed_out&&(stage.exit_code===0||(stage.stage==='orchestrator'&&stage.runner_owned===true&&stage.exit_code==null&&!!stage.finished_at));
}
function taskOverviewState(run) {
  const info=statusInfo(run),live=run.monitor?.live||{},active=run.active_stage||{};
  const ongoing=run.status==='RUNNING'&&active.stage&&!active.finished_at&&active.exit_code==null;
  const role=run.monitor?.active_role||active.role||(active.stage==='astra_discovery'&&jointPlanning(run)?'glm':active.stage?.split('_')[0]);
  const next=run.monitor?.next_stage,last=(run.stages||[]).findLast(stage=>stage.finished_at&&stageSucceeded(stage));
  const batchReported=run.status==='RUNNING'&&next==='orchestrator'&&run.monitor?.orchestration_batch;
  const savedStep=ongoing?'Last reported active step · '+stageName({...run,stage:active.stage}):batchReported?'Last reported step · Orchestrator · Coordinating Builders':next?'Next step · '+stageName({...run,stage:next}):last?'Last completed step · '+stageName({...run,stage:last.stage}):'No active step';
  return {info,role,active:!!ongoing&&live.state==='alive',verified:!!ongoing&&live.state==='alive',label:info.stateLabel,
    step:ongoing&&live.state==='alive'?'Current step · '+stageName(run):info.group==='complete'?'Work complete':info.group==='attention'?info.action:savedStep,
    objective:run.monitor?.objective||run.astra_plan?.current_assignment?.objective||'No current objective has been recorded.'};
}
function workflowRoleConfig(run, state) {
  const mode=run.monitor?.workflow_mode,finalOnly=mode==='glm_final_audit_v2',glmFirst=finalOnly||mode==='glm_first_v1';
  return glmFirst?[['terra',roleDisplayName('builder'),'Plan & implement'],['sol',roleDisplayName('validator'),'Targeted escalation only'],['astra',globalThis.AUTOCODE_ROLE_NAMES.modes[mode].astra_checkpoint.role,finalOnly?'Final full-task audit only':'Milestone review']]:
    [...(jointPlanning(run)?[['glm',roleDisplayName('planner'),'Draft & revise']]:[]),['astra',roleDisplayName('planner'),'Plan & direct'],['terra',roleDisplayName('builder'),'Implement'],['sol',roleDisplayName('validator'),'Review']];
}
function workflowConfig(run, state) {
  if(run.model_settings?.engine==='gocode'){
    const stages={glm:['astra_discovery','glm_revise'],astra:['astra_challenge','astra_finalize'],terra:['terra'],sol:['sol']};
    return workflowRoleConfig(run,state).map(([role,name,duty])=>[role,name,duty,stages[role]||[],'']);
  }
  const mode=run.monitor?.workflow_mode,finalOnly=mode==='glm_final_audit_v2',glmFirst=finalOnly||mode==='glm_first_v1',joint=jointPlanning(run),roles={...run.model_settings?.roles,...run.monitor?.roles};
  const hasRequirements=!!roles.requirements||(run.stages||[]).some(step=>step.stage==='requirements_gather');
  return [
    ['requirements',roleDisplayName('requirements'),'Gather requirements before planning',['requirements_gather'],hasRequirements?'':joint?'No separate requirements step recorded':glmFirst?'Handled by Builder':'Combined with discovery; no separate session'],
    [joint?'glm':'astra',roleDisplayName('planner'),joint?'Draft & revise':'Requirements, plan & implementation direction',joint?['astra_discovery','glm_revise']:['astra_discovery','astra_plan'],glmFirst?'Handled by Builder':''],
    [roles.plan_reviewer?'plan_reviewer':'astra',roleDisplayName('plan_reviewer'),'Independent challenge & finalization',['astra_challenge','astra_finalize'],joint?'':'Not enabled for this run'],
    ['orchestrator',roleDisplayName('orchestrator'),'Schedule Builders after approval',['orchestrator'],hasOrchestration(run)?'':'Not enabled for this run'],
    ['terra',roleDisplayName('builder'),glmFirst?'Plan & implement':'Implement the approved assignment',['terra'],''],
    ['sol',roleDisplayName('validator'),glmFirst?'Targeted review when needed':'Review implementation & evidence',['sol'],''],
    [roles.completion?'completion':'astra',roleDisplayName('completion'),finalOnly?'Final full-task audit':'Accept completion or request rework',['astra_review','astra_checkpoint'],''],
    ['resolver',roleDisplayName('resolver'),'Diagnose failures when needed',['astra_resolve','resolver'],'']
  ];
}
function completedPlanningStep(run, role) {
  const planningStages=role==='glm'?new Set(['astra_discovery','glm_revise']):new Set(['astra_discovery','astra_challenge','astra_finalize']);
  return (run.stages||[]).findLast(stage=>{
    const name=String(stage.stage||'').replace(/_report_repair$/,'');
    const owner=stage.role||(name==='glm_revise'?'glm':'astra');
    return planningStages.has(name)&&owner===role&&stage.finished_at&&stage.exit_code===0&&!stage.rejected&&!stage.abandoned&&!stage.interrupted&&!stage.timed_out;
  });
}
function workflowCards(run, state) {
  const host=card('','workflow-cards');host.setAttribute('aria-label','Agent workflow');
  const active=run.active_stage||{},history=run.monitor?.stage_history||run.stages||[];
  for(const [role,name,duty,stages,unavailable]of workflowConfig(run,state)){
    const matches=step=>stages.includes(String(step?.stage||'').replace(/_report_repair$/,''));
    const selected=run.status==='RUNNING'&&!unavailable&&matches(active)&&!active.finished_at&&active.exit_code==null;
    const live=selected&&state.verified,item=card('','workflow-card'+(live?' active':''));
    item.append(n('h3',name),Object.assign(n('p',duty),{className:'monitor-caption'}));
    const inherited=role==='resolver'&&!run.monitor?.roles?.resolver&&!run.model_settings?.roles?.resolver;
    const route=inherited?'astra':role,saved=run.monitor?.roles?.[route]||{};
    const configured={model:saved.model||run.model_settings?.roles?.[route],reasoning_effort:saved.reasoning_effort||run.model_settings?.role_efforts?.[route]};
    const last=history.findLast(matches),execution=selected?run.monitor?.active_execution:last?.execution;
    if(unavailable)item.append(Object.assign(n('p',unavailable),{className:'workflow-model'}));
    else {
      item.append(Object.assign(n('p',role==='orchestrator'?'Runner · No model call':'Configured: '+executionLabel(configured)+(inherited?' · inherited from '+roleDisplayName('planner'):'')),{className:'workflow-model'}));
      if(selected||last)item.append(Object.assign(n('p',(selected?'Launch: ':'Last launch: ')+executionLabel(execution)),{className:'workflow-model'}));
      const status=selected?(live?'Active now':'Last reported active · worker unverified'):last?(stageSucceeded(last)?'Step finished':last.rejected?'Output rejected':last.interrupted?'Interrupted':last.timed_out?'Timed out':'Attempt recorded')+(last.finished_at?' · '+new Date(last.finished_at).toLocaleString():''):role==='resolver'?'Conditional · not used yet':'Not started';
      item.append(Object.assign(n('p',status),{className:'workflow-history'+(live?' workflow-active':'')}));
    }
    host.append(item);
  }return host;
}
function executionLabel(execution) {
  if(execution?.kind==='runner')return 'Runner · No model call';
  return [execution?.model||'Model not recorded',execution?.reasoning_effort?execution.reasoning_effort+' reasoning':null,execution?.engine].filter(Boolean).join(' · ');
}
function workflowModelsPanel(run) {
  const host=card('','workflow-models-panel monitor-panel');
  host.append(n('h2','Stages & models'),n('p','Requirements → planning → independent plan review → approval → orchestration → build → validation → completion. Resolver runs when needed.'));
  if(!jointPlanning(run)&&!['glm_first_v1','glm_final_audit_v2'].includes(run.monitor?.workflow_mode))host.append(Object.assign(n('p','This run combines requirements and discovery, and has no independent plan-review stage.'),{className:'workflow-gap'}));
  host.append(workflowCards(run,taskOverviewState(run)));return host;
}
function executionCheckpoints(saved) {
  const host=card('','monitor-activity');
  host.append(n('h3','Milestone checkpoints'+(saved.milestone_id?' · '+saved.milestone_id:'')),
    n('p','Saved execution progress. Only independent validation can verify criteria; recorded implementation is not acceptance.'));
  for(const checkpoint of saved.rows||[]){const row=card('','monitor-event');
    row.append(n('strong',checkpoint.label),n('span',human(checkpoint.status)+(checkpoint.completed_tools!=null?' · '+checkpoint.completed_tools+' completed tool events':'')));host.append(row);}
  if(saved.paused)host.append(n('p','Paused — completed work and evidence retained.'));
  return host;
}
function orchestrationPanel(batch, historical=false) {
  const host=card('','monitor-activity');
  host.append(n('h3',(historical?'Saved':'Current')+' Builder batch · '+(batch.id||'ID unavailable')),
    n('p','Saved status · '+human(batch.status||'unknown')),
    Object.assign(n('p','Worker statuses are saved reports, not live process checks. Built or integrated work still requires combined validation and acceptance.'),{className:'monitor-caption'}));
  for(const worker of batch.workers||[]){const row=card('','monitor-event');
    row.append(n('strong','Builder · '+(worker.milestone_id||'Milestone unavailable')),n('span','Saved status · '+human(worker.status||'unknown')),
      n('p','Worktree · '+(worker.workspace||'Not recorded')),n('p','Run / logs · '+(worker.run_dir||'Not recorded')));host.append(row);
    if(worker.checkpoints?.rows?.length)row.append(executionCheckpoints(worker.checkpoints));
  }
  if(!batch.workers?.length)host.append(n('p','No workers recorded yet.'));
  return host;
}
function monitorDetailsPanel(run) {
  const monitor=run.monitor||{},host=card('','monitor-panel');
  if(monitor.checkpoints?.rows?.length)host.append(executionCheckpoints(monitor.checkpoints));
  host.append(disclosure('Models & role history','monitor-roles',[workflowCards(run,taskOverviewState(run))],run.run));
    if(monitor.orchestration_batch)host.append(orchestrationPanel(monitor.orchestration_batch));
    if(monitor.orchestration_history?.length)host.append(disclosure('Saved Builder batches ('+monitor.orchestration_history.length+')','orchestration-history',monitor.orchestration_history.map(batch=>orchestrationPanel(batch,true)),run.run));
    const details=card('','monitor-details'),activity=card('','monitor-activity'),history=card('','monitor-history');
    activity.append(n('h3','Latest activity'),Object.assign(n('p','Recent tool events. A quiet log alone does not mean the worker is stuck.'),{className:'monitor-caption'}));
    for(const entry of monitor.activity||[]){const row=card('','monitor-event');row.append(n('span',entry.label),n('small',human(entry.status)+(entry.exit_code!=null?' · exit '+entry.exit_code:'')));if(entry.test_summary)row.append(Object.assign(n('p',entry.test_summary),{className:'monitor-test-summary'}));activity.append(row);}
    activity.append(Object.assign(n('p','Latest six events from a bounded log window. Numeric test totals are tool-reported, not a task verdict. Full evidence is in Checks.'),{className:'monitor-caption'}));
    if(!monitor.activity?.length)activity.append(n('p','No recent tool events are available.'));
    const ledger=monitor.findings_summary;
    history.append(n('h3','Open findings'),Object.assign(n('p',(ledger?ledger.open+' open · '+ledger.resolved+' resolved'+(ledger.repeated?' · '+ledger.repeated+' reported again after a fix':'')+(ledger.not_rechecked?' · '+ledger.not_rechecked+' not rechecked by the latest review':'')+'. Both reviewers, one list; a finding closes only when the reviewer who raised it rechecks that work and no longer reports it.':'Latest saved review'+(monitor.validation_verdict?' · '+monitor.validation_verdict:'')+'. Fixes in progress may not yet be revalidated.')),{className:'monitor-caption'}));
    for(const finding of monitor.findings||[]){const row=card('','monitor-event');row.append(n('span',(finding.id?finding.id+' · ':'')+(finding.finding||'Finding details unavailable')),n('small',human(finding.severity)+(finding.source?' · raised by '+human(finding.source):'')+(finding.milestone?' · '+finding.milestone:'')+(finding.assigned_task?' · fix: '+finding.assigned_task:'')+((finding.times_reported||1)>1?' · reported '+finding.times_reported+'×':'')+(finding.not_rechecked?' · not rechecked':'')));history.append(row);}
    if(!monitor.findings?.length)history.append(n('p','No open findings recorded. This is not proof of completion.'));details.append(activity,history);host.append(details);
    for(const metric of monitor.metrics||[])host.append(metricPanel(metric));
    const counts=run.counts||{},criteria=card('','monitor-criteria');criteria.append(n('h3','Acceptance checks'),n('p',(counts.pass||0)+' passed · '+(counts.fail||0)+' failed or blocked · '+(counts.unknown||0)+' unverified'),button('View evidence & reviews →',()=>activateTab('execution'),'text-button'));host.append(criteria);
  return host;
}
function operationalPreview(value,limit=136) {
  const text=String(value||'').replace(/\s+/g,' ').trim();
  if(text.length<=limit)return text;
  const prefix=text.slice(0,limit-1),breakAt=Math.max(prefix.lastIndexOf(' '),Math.floor(limit*.6));
  return prefix.slice(0,breakAt).trimEnd()+'…';
}
function freshnessPreview(run,freshness) {
  const live=run.monitor?.live||{};
  if(run.status==='RUNNING'&&live.state==='alive')return 'PID '+(live.pid||'recorded')+' · '+recordedTimeLabel(run);
  if(statusInfo(run).group==='complete')return run.validation?.source_revision?'Source '+run.validation.source_revision+' · unavailable':'Recorded evidence · unavailable';
  if(['PAUSED_PROVIDER_UNCERTAIN','PAUSED_UNCERTAIN_STAGE'].includes(run.status))return 'Checkpoint · '+recordedTimeLabel(run);
  return operationalPreview(String(freshness||'').replace(/^(?:Recorded evidence|Current verification) · /,''),64);
}
function monitorFact(label,value,key,preview=value) {
  const item=card('','monitor-fact monitor-fact-'+key);item.dataset.taskFact=key;
  const heading=n('p',label),visible=n('p',preview);
  heading.dataset.taskFactLabel=key;visible.dataset.taskFactVisible=key;visible.dataset.taskFactFull=key;
  item.append(heading,visible);
  return item;
}
function monitorPanel(run) {
  const facts=stateFacts(run),info=statusInfo(run),host=card('','monitor-panel task-state-hero work-facts');
  host.dataset.taskStateHero='true';
  // Compact saved-facts section (422:1613 verification block). The state chip
  // and current step lead the Now-working card; every remaining saved fact
  // stays readable here without enlarging that lead. A short, task-specific
  // blocker preview protects the mobile fold; the complete question/reason is
  // rendered in the adjacent decision or recovery detail rather than hidden
  // inside this fact's textContent.
  const blockerPreview=info.group==='attention'&&Array.isArray(run.questions)&&run.questions.length
    // Keep the decision itself readable in the mobile fold. The complete exact
    // question is rendered immediately below in the decision card and in
    // Conversation; this is deliberately a truthful, task-specific preview.
    ?'Answer '+run.questions.length+' '+(run.questions.length===1?'question':'questions')+' · first unresolved: '+operationalPreview(facts.blocker,48)
    :operationalPreview(facts.blocker,136);
  host.append(Object.assign(n('p','SAVED STATUS'),{className:'monitor-kicker'}));
  const freshness=monitorFact('FRESHNESS',facts.freshness,'freshness',freshnessPreview(run,facts.freshness));freshness.classList.add('monitor-freshness');
  const operations=card('','monitor-fact-grid monitor-operational-facts');
  const objectivePreview=operationalPreview(facts.objective,112);
  operations.append(
    monitorFact('OBJECTIVE',facts.objective,'objective',objectivePreview),
    monitorFact(info.group==='attention'?'DECISION':'BLOCKER',facts.blocker,'blocker',blockerPreview),
    monitorFact('RESPONSIBLE ROLE',facts.role,'role'),
    freshness
  );
  host.append(operations);
  if(objectivePreview!==facts.objective){
    const detail=disclosure('Read complete objective','objective:'+run.run,[n('p',facts.objective)],run.run);
    detail.className='monitor-objective-detail';detail.dataset.taskFactDetail='objective';host.append(detail);
  }
  if(info.label==='Interrupted')host.append(Object.assign(n('p','Recovery order: Review recovery → Inspect interrupted attempt → Recover saved work → Resume separately.',),{className:'recovery-order'}));
  if(info.group==='complete')host.append(completionRecord(run),Object.assign(n('p','No reply needed. '+completionEvidenceStatement(run)),{className:'completion-evidence'}));
  return host;
}
function metricPanel(metric){
  const host=card('','metric-panel');host.append(n('h3',metric.label||'Project progress'));
  if(metric.error){host.append(Object.assign(n('p',metric.error),{className:'error'}));return host;}
  const fmt=value=>Number.isFinite(value)?value.toLocaleString():'Unknown';
  host.append(Object.assign(n('p',fmt(metric.done)+' / '+fmt(metric.total)),{className:'metric-total'}),n('p',metric.description||'Artifact counters, not a task-completion verdict.'),Object.assign(n('p','Artifact snapshot · '+new Date(metric.updated).toLocaleString()+' · '+statusAge(metric.updated)),{className:'monitor-caption'}));
  const areas=card('','metric-areas');for(const area of metric.areas||[]){const item=card('','metric-area'),bar=n('progress','');bar.max=area.total||1;bar.value=area.done;bar.setAttribute('aria-label',area.name);item.append(n('span',human(area.name)),n('strong',fmt(area.done)+' / '+fmt(area.total)),bar);areas.append(item);}host.append(areas,n('code',metric.source));return host;
}
function renderTaskOverview(run) {
  const host=$('#task-overview');host.replaceChildren(n('h2','Recent saved steps'),n('p','Stage exits are not task-completion verdicts. Full evidence remains in Checks.'));
  for(const step of run.monitor?.history||[]){const row=card('','monitor-event');row.append(n('span',stageName({...run,stage:step.stage})+' · iteration '+(step.iteration??'?')),n('span',executionLabel(step.execution)),n('small',(step.rejected?'Rejected':step.interrupted?'Interrupted':step.timed_out?'Timed out':stageSucceeded(step)?'Finished':'Recorded')+' · '+(step.finished_at||'Time not recorded')));host.append(row);}
  if(run.interventions)host.append(disclosure('Saved controls & delivery history','runtime-controls',[renderDocument(run.interventions)],run.run));
  // The reviewer findings, contract receipts, verification summaries and saved
  // change disclosures are operational history: they stay inspectable here in
  // History instead of displacing the continuous chat transcript (422-1495).
  renderWorkflowTimeline(host,run);
  const checkpoints=renderSessionCheckpoints(run,sessionCheckpoints(run),false);if(checkpoints)host.append(checkpoints);
  if(run.monitor?.checkpoints?.rows?.length)host.append(executionCheckpoints(run.monitor.checkpoints));
  if(run.monitor?.orchestration_batch)host.append(orchestrationPanel(run.monitor.orchestration_batch));
  const assignment=run.astra_plan?.current_assignment;
  if(assignment)host.append(disclosure('Assignment scope & checks','current-assignment:'+assignment.id,[renderDocument(assignment)],run.run));
  const context=card('','current-context'),plan=card('','');plan.append(n('h3','Current plan'),n('p',run.goal?.revision!=null?'Revision '+run.goal.revision+' · '+(run.goal.approval_status==='approved'?'Approved':statusInfo(run).label==='Approve plan'?'Ready for review':'Draft'):'No plan revision saved'),button('Read current plan →',()=>activateTab('plan'),'text-button'));context.append(plan);
  const checkpoint=card('',''),latest=[...(run.monitor?.history||[])].sort((a,b)=>Date.parse(b.finished_at)-Date.parse(a.finished_at))[0];checkpoint.append(n('h3','Last saved step'));
  if(latest){checkpoint.append(n('p',stageName({...run,stage:latest.stage})+' · iteration '+(latest.iteration??'?')),n('p',(latest.rejected?'Output rejected':latest.interrupted?'Interrupted':latest.timed_out?'Timed out':stageSucceeded(latest)?'Step finished':'Step stopped')+' · '+new Date(latest.finished_at).toLocaleString()));}else checkpoint.append(n('p','No completed step recorded.'));
  checkpoint.append(button('View history →',()=>activateTab('overview'),'text-button'));context.append(checkpoint);host.append(context);
  host.hidden=currentTab!=='overview';
}
function markMonitorStale(){document.querySelectorAll('.monitor-panel').forEach(panel=>{
  panel.classList.add('monitor-stale');
  const chip=panel.querySelector('.monitor-status-chip');if(chip){chip.textContent='Status unverified';chip.className='monitor-status-chip stale';}
  const title=panel.querySelector('.monitor-state h2');if(title&&!title.textContent.startsWith('Last reported · '))title.textContent='Last reported · '+title.textContent;
  const next=panel.querySelector('.monitor-next');if(next){const heading=next.querySelector('strong'),description=next.querySelector('p:not(.monitor-kicker)');if(heading)heading.textContent='Refresh task status';if(description)description.textContent='Live checks are unavailable. The details shown here are from the last successful read.';}
  const freshness=panel.querySelector('.monitor-freshness');if(freshness){const value=freshness.querySelector('[data-task-fact-visible]');if(value)value.textContent='Live checks unavailable. The details below are from the last successful read.';}
  panel.querySelectorAll('.workflow-card.active').forEach(card=>card.classList.remove('active'));
});}
function renderProjectOverview(scope) {
  const host=$('#project-overview');host.replaceChildren();host.hidden=!projectFilter;if(!projectFilter)return;
  const actual=scope.filter(run=>run.run&&!run.error),summary=projectRunSummary(actual);
  const heading=card('','project-overview-heading');
  heading.append(n('p',projectSummaryLabel(summary)+'. Active work and saved history are shown separately.'));
  if(summary.previous||summary.previews)heading.append(n('p','Saved history is hidden until you select “other saved”.'));
  host.append(heading);
  if(!actual.length)host.append(n('p','No saved task status is available for this project.'));
}
function setView(view) {
  if(init&&view!=='draft-conversation'&&typeof returnSetupModels==='function')returnSetupModels();
  if (currentView !== view) seq++;
  if(view!=='task-detail'&&typeof cancelApprovalReceipts==='function')cancelApprovalReceipts();
  currentView = view;
  const draftDialog=$('#draft-plan-dialog');if(view!=='draft-conversation'&&draftDialog?.open){draftDialog._returnFocus=null;draftDialog.close();}
  document.body.classList.toggle('monitor-focus',view==='task-detail'&&stored('focus')==='1');
  $('#focus-toggle').hidden=view!=='task-detail';
  $('#focus-toggle').textContent=stored('focus')==='1'?'Show navigation':'Focus view';
  $('#focus-toggle').setAttribute('aria-pressed',String(stored('focus')==='1'));
  $('#details-drawer-toggle').hidden=view!=='task-detail';
  if(view!=='task-detail')closeDetailsDrawer();
  if(['tasks','conversations','settings','new-task','archived'].includes(view))rememberSelection(view==='new-task'?'new':view==='tasks'?taskSelection():view);
  for (const id of ['tasks','new-task','task-detail','settings','conversations','draft-conversation','archived']) $('#'+id).hidden = id !== view;
  $('#breadcrumb-title').textContent = view==='archived'?'Archived':view === 'tasks' ? taskScopeTitle() : view === 'new-task' ? 'New conversation' : view === 'settings' ? 'Settings' : ['conversations','draft-conversation'].includes(view)?'Conversation':projectTitle(chosen?.workspace);
  closeNavigation();
  if(view!=='task-detail')stopPreview();
  document.querySelectorAll('[data-view]').forEach(element => element.classList.toggle('selected',element.dataset.view === (view==='draft-conversation'?'conversations':view) && (view!=='tasks'||(!projectFilter&&taskFilter==='all'))));
}
const WORKSPACE_PANES = ['now', 'plan', 'preview', 'changes', 'execution', 'overview'];
function activateTab(tab) {
  if(latestRun?.task_archived){showArchivedRun(latestRun);return;}
  // The chat column and its composer are part of the conversation view in
  // every pane. 'interview' stays as the alias callers use to surface chat
  // without changing which artifact pane is open.
  if(tab==='interview')tab=WORKSPACE_PANES.includes(currentTab)?currentTab:'now';
  const changed=currentTab!==tab;currentTab = tab;
  const paneTitle=$('#details-pane-title');if(paneTitle)paneTitle.textContent=({now:'Work',plan:'Your plan',preview:'Preview',changes:'Changes',execution:'Checks',overview:'History'})[tab]||'Work';
  $('#task-overview').hidden=tab!=='overview'||!latestRun||!!latestRun?.project_removed;
  $('#detail-grid').hidden=!!latestRun?.project_removed;
  for (const id of ['now','plan','execution','changes','preview']) $('#'+id).hidden = id !== tab;
  $('#live-controls').hidden=!latestRun||!!latestRun?.project_removed||!!latestRun?.task_archived;
  $('#goal').hidden = true;
  $('#task-detail').classList.add('conversation-active');
  if(tab!=='preview')stopPreview();
  if(tab==='changes'&&chosen?.run&&evidenceRun!==chosen.run)loadChanges();
  if(tab==='preview'){
    if(chosen?.run&&previewRun!==chosen.run){previewRun=chosen.run;$('#preview-url').value=savedPreview(previewContext());$('#preview-error').textContent='';}
    renderPreviewState();
  }
  if(latestRun)renderPrimaryAction(latestRun);
  if(changed)settleThreadScroll();
  document.querySelectorAll('[data-tab]').forEach(element => { const selected=element.dataset.tab===tab;element.classList.toggle('selected',selected);element.setAttribute('aria-selected',String(selected));element.setAttribute('tabindex',selected?'0':'-1'); });
  if(taskReadError)disableStaleControls();
}
function openRun(run, tab = 'now') {
  $('#archive-task').disabled=true;$('#delete-task').disabled=true;
  if (!run.run || run.error) return;
  if(currentView!=='task-detail')taskReturn=currentView==='tasks'?{view:'tasks',filter:taskFilter,project:projectFilter}:{view:'tasks',filter:'all',project:''};
  const changed = chosen?.run !== run.run;
  if(changed&&typeof cancelApprovalReceipts==='function')cancelApprovalReceipts();
  if(changed){$('#task-overview').hidden=true;$('#now').dataset.rendered='';$('#now').replaceChildren(n('p','Loading current state…'));scrollThreadToEnd=true;pendingThreadAnchorKey='task-scroll:'+run.run;stopPreview();evidenceRun='';evidenceRequest++;$('#changes-content').replaceChildren();$('#thread-checkpoint').replaceChildren();$('#task-objective').textContent='';$('#conversation').dataset.rendered='';}
  chosen = {workspace:run.workspace,run:run.run}; activeConversation = null; latestRun = null; seq++;
  if(run.workspace&&run.workspace!==activeProject){activeProject=run.workspace;persist('active-project',activeProject);}
  taskReadError='';taskReadAt=null;$('#task-load-notice').hidden=true;
  rememberSelection(runSelection(run));
  renderTaskProjectActions(run.workspace);$('#task-removed-banner').hidden=true;$('#detail-grid').hidden=false;
  if (changed) { $('#conversation').replaceChildren(); $('#brief-current').replaceChildren(); $('#plan-full').replaceChildren(); $('#goal').replaceChildren(); $('#live-controls').hidden=true; $('#task-attention').hidden=true; $('#continue-run').disabled=true; $('#pause-run').disabled=true; $('#stop-run').disabled=true; $('#task-project').textContent=projectLabel(run.workspace); $('#task-title').textContent=taskTitle(run); $('#task-subtitle').textContent='Loading current task state…'; $('#task-models').textContent=''; $('#task-status').replaceChildren();$('#task-progress-summary').hidden=true; }
  $('#task-back').textContent='← '+(taskReturn.project?projectTitle(taskReturn.project):'All work');
  setView('task-detail'); activateTab(tab); refresh(); window.scrollTo({top:0}); syncNewTaskScope();
}
function filterTasks(filter, project = projectFilter) { taskFilter = filter; projectFilter = project; setView('tasks'); if (latestData) renderTasks(latestData); }
$('#task-back').onclick=()=>filterTasks(taskReturn.filter||'all',taskReturn.project||'');
function showNewTask(scope) {
  const project=typeof scope==='string'?scope:activeProject;
  newTaskProject=project||'';activeProject=project||'';persist('active-project',activeProject);
  chosen=null; activeConversation=null; seq++; setView('new-task');rememberSelection('new');syncNewTaskScope();$('#new-goal').focus();
}
async function startProjectConversation(scope) {
  // Activating a New conversation control opens a saved, empty, scoped
  // conversation immediately: the header and composer show the project before
  // any message is sent, and the first send continues that saved record.
  const project=(typeof scope==='string'?scope:activeProject)||'';
  newTaskProject=project;activeProject=project;persist('active-project',activeProject);syncNewTaskScope();
  const request=savedRequest('create-request',{empty:true,workspace:project});
  try{
    const body={empty:true,request_id:request.id};
    if(project)body.workspace=project;
    const doc=await api('/api/conversations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)});
    persist('create-request','');newTaskProject='';
    openConversation(doc);
  }catch(problem){
    dashboardNotice(problem.message);
    showNewTask(project);
  }
}
document.querySelectorAll('[data-new-task]').forEach(element => element.onclick = () => showNewTask());
document.querySelectorAll('[data-view]').forEach(element => element.onclick = () => { if(element.dataset.view === 'tasks') filterTasks('all',''); else setView(element.dataset.view); });
document.querySelectorAll('[data-tab]').forEach(element => element.onclick = () => activateTab(element.dataset.tab));
$('#brand-home').onclick = event => { event.preventDefault(); setView('tasks'); };
$('#conversation-search').oninput = event => { conversationSearch = event.target.value; if (latestData) renderProjectGroups(latestData); };
// A drawer control joins the focus order only when it is actually rendered:
// no hidden or disabled ancestor, so rows inside a collapsed project group
// (their group rows container is hidden) never act as the wrap boundary.
// A control the stylesheet does not paint (the mobile project sheet hides its
// desktop-only utility rows) cannot take focus either, so it must not bound
// the keyboard wrap.
function navigationFocusables(){return [...$('#primary-navigation').querySelectorAll('summary,a[href],button:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),[tabindex]:not([tabindex="-1"])')].filter(element=>!element.hidden&&!element.disabled&&drawerControlRendered(element)&&drawerControlPainted(element));}
function drawerControlPainted(element){
  if(typeof getComputedStyle!=='function')return true;
  const style=getComputedStyle(element),rect=element.getBoundingClientRect();
  return style.display!=='none'&&style.visibility!=='hidden'&&rect.width>0&&rect.height>0;
}
// The closed navigation drawer is off-canvas at phone widths; keep its
// controls out of the keyboard order until the drawer is opened.
function syncNavigationInert(){
  if(typeof matchMedia!=='function')return;
  $('#primary-navigation').inert=matchMedia('(max-width: 759px)').matches&&!$('.app-shell').classList.contains('nav-open');
}
function openNavigation(){
  const shell=$('.app-shell');if(shell.classList.contains('nav-open'))return;
  shell.classList.add('nav-open');$('#nav-toggle').setAttribute('aria-expanded','true');$('#drawer-scrim').hidden=false;
  $('.main-area').inert=true;$('.topbar').inert=true;$('#primary-navigation').inert=false;const focusable=navigationFocusables();(focusable[0]||$('#primary-navigation')).focus();
}
function closeNavigation(){
  const shell=$('.app-shell');if(!shell.classList.contains('nav-open'))return;
  shell.classList.remove('nav-open');$('#nav-toggle').setAttribute('aria-expanded','false');$('.main-area').inert=false;$('.topbar').inert=false;$('#drawer-scrim').hidden=true;
  syncNavigationInert();
  if(matchMedia('(max-width: 759px)').matches)$('#nav-toggle').focus();
}
// The details drawer lifts the artifact pane over the chat at phone widths.
// The chat transcript and its composer stay mounted underneath; closing the
// drawer returns focus to its control.
// A drawer control joins the focus order only when it is actually rendered:
// no hidden or disabled ancestor, and inside a <details> only when that
// details is open or the control is its own summary.
function drawerControlRendered(element){
  for(let node=element;node;node=node.parentElement){
    if(node.hidden||node.disabled)return false;
    if(node.tagName==='DETAILS'&&node!==element&&!node.open){
      let child=element,parent=element.parentElement;
      while(parent&&parent!==node){child=parent;parent=parent.parentElement;}
      if(child.tagName!=='SUMMARY')return false;
    }
  }
  return true;
}
function detailsDrawerFocusables(){return [...$('#context-pane').querySelectorAll('button:not([disabled]),[role=tab]:not([disabled]),input:not([disabled]):not([type=hidden]),select:not([disabled]),textarea:not([disabled]),summary,[tabindex="0"]')].filter(element=>!element.hidden&&!element.disabled&&drawerControlRendered(element));}
function openDetailsDrawer(){
  const shell=$('.app-shell'),toggle=$('#details-drawer-toggle');
  if(toggle.hidden||shell.classList.contains('details-open'))return;
  shell.classList.add('details-open');toggle.setAttribute('aria-expanded','true');$('#details-scrim').hidden=false;
  $('.conversation-column').inert=true;$('#details-drawer-close').focus();
}
function closeDetailsDrawer(){
  const shell=$('.app-shell');if(!shell.classList.contains('details-open'))return;
  shell.classList.remove('details-open');$('#details-drawer-toggle').setAttribute('aria-expanded','false');$('#details-scrim').hidden=true;
  $('.conversation-column').inert=false;
  $('#details-drawer-toggle').focus();
}
$('#nav-toggle').onclick=()=>$('.app-shell').classList.contains('nav-open')?closeNavigation():openNavigation();
$('#drawer-close').onclick=closeNavigation;
$('#drawer-scrim').onclick=closeNavigation;
$('#details-drawer-toggle').onclick=()=>$('.app-shell').classList.contains('details-open')?closeDetailsDrawer():openDetailsDrawer();
$('#details-drawer-close').onclick=closeDetailsDrawer;
$('#details-scrim').onclick=closeDetailsDrawer;
addEventListener('resize',()=>{const narrow=typeof matchMedia==='function'&&matchMedia('(max-width: 759px)').matches;if(!narrow)closeDetailsDrawer();closeNavigation();syncNavigationInert();});
syncNavigationInert();
document.addEventListener('keydown',event=>{
  if(event.key==='Escape'){closeNavigation();closeDetailsDrawer();document.querySelectorAll('.context-menu[open]').forEach(menu=>menu.open=false);return;}
  if(event.key==='Tab'&&$('.app-shell').classList.contains('nav-open')){const focusable=navigationFocusables();if(!focusable.length)return;const first=focusable[0],last=focusable.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
  if(event.key==='Tab'&&$('.app-shell').classList.contains('details-open')){const focusable=detailsDrawerFocusables();if(!focusable.length)return;const first=focusable[0],last=focusable.at(-1);if(event.shiftKey&&document.activeElement===first){event.preventDefault();last.focus();}else if(!event.shiftKey&&document.activeElement===last){event.preventDefault();first.focus();}}
});
$('.detail-tabs').addEventListener('keydown',event=>{
  if(!['ArrowLeft','ArrowRight','Home','End'].includes(event.key))return;
  const tabs=[...document.querySelectorAll('.detail-tabs [role=tab]')],index=tabs.indexOf(document.activeElement);if(index<0)return;
  event.preventDefault();const next=event.key==='Home'?0:event.key==='End'?tabs.length-1:(index+(event.key==='ArrowRight'?1:-1)+tabs.length)%tabs.length;
  tabs[next].focus();tabs[next].scrollIntoView({block:'nearest',inline:'nearest'});
});
$('#task-status-filter').onchange = event => filterTasks(event.target.value);
$('#project-filter').onchange = event => filterTasks(taskFilter,event.target.value);
$('#task-search').oninput = event => { searchText = event.target.value; if (latestData) renderTasks(latestData); };
document.addEventListener('keydown',event => { if (event.key.toLowerCase()==='n' && !$('#project-removal-dialog').open && !$('#task-archive-dialog').open && !$('#conversation-archive-dialog').open && !$('#task-delete-dialog').open && !event.metaKey && !event.ctrlKey && !event.altKey && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) && !document.activeElement?.isContentEditable) { event.preventDefault(); showNewTask(); } });

$('#interview').append($('#change-history'));
function composerShouldSend(event) {return event.key==='Enter'&&!event.shiftKey&&!event.altKey&&!event.isComposing;}
function conversationDeliveryBlocked(doc){return [doc?.pending_dispatch,doc?.planner_delivery].some(delivery=>delivery&&['UNCERTAIN','ACTIVE','DISPATCH_PREPARED','PROCESS_STARTING','PROCESS_STARTED','PROVIDER_IDENTIFIED','RESULT_CAPTURED','PENDING'].includes(String(delivery.state||delivery.status||'').toUpperCase()));}
function conversationRetryAllowed(doc){if(doc?.project_scope_error)return false;const deliveries=[doc?.pending_dispatch,doc?.planner_delivery].filter(Boolean);return !conversationDeliveryBlocked(doc)&&(!deliveries.length||deliveries.some(delivery=>delivery.retryable===true));}
// A Planner draft still running does not block the next answer: the server
// coalesces answers sent meanwhile into one follow-up draft (#21). A Gatherer
// reply in flight, or an uncertain Planner delivery, still does.
function conversationSendBlocked(doc){const planner=doc?.planner_delivery,uncertain=String(planner?.state||planner?.status||'').toUpperCase()==='UNCERTAIN';return conversationDeliveryBlocked({pending_dispatch:doc?.pending_dispatch,planner_delivery:uncertain?planner:null});}
function draftSendBlocked(doc,pending,text) {return !doc||!!(doc.project_removed||doc.task_archived||doc.archived_at||pending||conversationSendBlocked(doc)||doc.status==='thinking'||doc.attachment||doc.status==='error')||!String(text||'').trim();}
function resizeComposer(input) {const minimum=(input.id==='draft-text'&&$('#draft-conversation').classList.contains('scoped-start'))?24:48;input.style.height='auto';input.style.height=Math.min(160,Math.max(minimum,input.scrollHeight))+'px';}
for(const [input,form] of [['#change-text','#change-form'],['#draft-text','#draft-form']]){
  $(input).addEventListener('keydown',event=>{if(composerShouldSend(event)){event.preventDefault();const submit=$(form).querySelector('button[type="submit"]');if(submit&&!submit.disabled&&$(input).value.trim())$(form).requestSubmit();}});
  $(input).addEventListener('input',()=>resizeComposer($(input)));
  if(typeof ResizeObserver!=='undefined'){let measured='';const observer=new ResizeObserver(()=>{const element=$(input),key=element.clientWidth+':'+getComputedStyle(element).fontSize;if(key!==measured){measured=key;resizeComposer(element);}});observer.observe($(input));}
}
function updateDraftScroll(){const scroll=$('#draft-scroll');$('#draft-latest').hidden=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<90;rememberThreadAnchor(scroll,activeConversation?'conversation-scroll:'+activeConversation:'');}
function scrollDraftToEnd(){const scroll=$('#draft-scroll');scroll.scrollTop=scroll.scrollHeight;updateDraftScroll();}
$('#draft-scroll').addEventListener('scroll',updateDraftScroll);
new ResizeObserver(updateDraftScroll).observe($('#draft-scroll'));
$('#draft-latest').onclick=scrollDraftToEnd;
// Per-conversation scroll anchors: the transcript remembers which message
// sits at the top of its viewport, so an ordinary reload (or returning to the
// conversation) restores the reading position instead of jumping to the end.
function messageAnchorKey(message) {
  if(message?.id)return 'id:'+message.id;
  return 'txt:'+(message?.role||'')+':'+(messageTime(message)||0)+':'+String(message?.text||'').slice(0,48);
}
function threadAnchorMessages(scroller){return [...scroller.querySelectorAll('[data-message-key]')];}
function rememberThreadAnchor(scroller,key) {
  if(!key)return;
  const messages=threadAnchorMessages(scroller);
  if(!messages.length)return;
  const mark=(scroller.scrollTop||0)+2;
  let anchor=messages[0];
  for(const element of messages){if((element.offsetTop||0)<=mark)anchor=element;else break;}
  persist(key,String(anchor.dataset.messageKey||''));
}
function restoreThreadAnchor(scroller,key) {
  let anchor='';
  try{anchor=stored(key,'');}catch{return false;}
  if(!anchor)return false;
  const messages=threadAnchorMessages(scroller);
  if(!messages.length)return false;
  const target=messages.find(element=>element.dataset.messageKey===anchor);
  if(!target)return false;
  scroller.scrollTop=Math.max(0,target.offsetTop||0);
  return true;
}
$('#interview').addEventListener('scroll',()=>{if(chosen?.run)rememberThreadAnchor($('#interview'),'task-scroll:'+chosen.run);});
function syncWorkspaces(list) {
  const select = $('#workspaces'), keep = select.value;
  if (select.dataset.options === JSON.stringify(list)) return;
  select.dataset.options = JSON.stringify(list); select.replaceChildren(n('option','Choose a project…')); select.firstChild.value='';
  for (const path of list) { const option = n('option',projectTitle(path)+' · '+path); option.value=path; select.append(option); }
  select.append(Object.assign(n('option','Choose another folder…'),{value:'__custom__'}));
  if (keep && (list.includes(keep)||keep==='__custom__')) select.value=keep;
}
function supportedReasoningLevels(data,role,model){
  const supplied=data?.reasoning_levels||data?.reasoningLevels||{},candidate=supplied?.[model]||supplied?.[role];
  const levels=Array.isArray(candidate)?candidate.filter(value=>['low','medium','high','xhigh','max'].includes(value)):['low','medium','high','xhigh','max'];
  return levels.length?levels:['medium'];
}
function syncReasoningSelector(id,levels,defaultEffort){
  const select=$(id);if(!select)return;
  if(defaultEffort)select.dataset.defaultEffort=defaultEffort;
  const previous=select.dataset.userSelected==='true'?select.value:select.dataset.defaultEffort||select.value;select.replaceChildren();
  for(const level of levels)select.append(Object.assign(n('option',level==='max'?'Maximum':human(level)),{value:level}));
  select.value=levels.includes(previous)?previous:levels[0];
}
function syncConversationTransport(signal){
  if(!signal||!['available','missing','unsupported','unknown'].includes(signal.transport))return;
  conversationTransport={transport:signal.transport,version:signal.version||null};
  syncConversationReadiness();
}
function conversationModelReadiness(){
  const catalogue=conversationCatalogue,profile=conversationProfile,signal=conversationTransport,blocked=['missing','unsupported'].includes(signal.transport);
  if(!profile?.routes||!profile?.controls||!profile?.pattern||!Array.isArray(profile.required))return {...signal,usable:false,missing:[],status:blocked?'missing':'unknown',authentication:'unknown'};
  const pattern=new RegExp(profile.pattern,'i'),available=new Set(catalogue.usable&&!catalogue.error&&!catalogue.loading?catalogue.models:[]),routes=Object.fromEntries(Object.entries(profile.routes).map(([role,route])=>[role,{...route}]));
  for(const [control,role] of Object.entries(profile.controls)){
    const select=$('#'+control+'-model'),effort=$('#'+control+'-reasoning-effort');
    if(select?.dataset.userSelected==='true'&&select.value){const value=select.value;routes[role].model=profile.aliases?.[control]?.includes(value)?'openai/'+value:value;}
    if(effort?.dataset.userSelected==='true')routes[role].reasoning_effort=effort.value;
  }
  const required=profile.visual?[...profile.required,'visual_review']:profile.required;
  const missing=required.filter(role=>!routes[role]||!pattern.test(routes[role].model)||!available.has(routes[role].model));
  return {...signal,usable:!blocked&&catalogue.usable===true&&!catalogue.error&&!catalogue.loading&&missing.length===0,missing,routes,status:blocked||missing.length?'missing':'unknown',authentication:'unknown'};
}
function syncConversationReadiness(){
  const catalogue=conversationCatalogue,readiness=conversationModelReadiness(),gate=$('#create-model-catalogue-gate'),status=$('#create-model-catalogue-status'),retry=$('#retry-models'),submit=$('#create-submit');
  const message=readiness.transport==='unsupported'?'Dashboard conversations require built-in OpenCode 1.x. This transport is unsupported; recheck workspace setup.':readiness.transport==='missing'?'Built-in OpenCode is unavailable for dashboard conversations. Install OpenCode 1.x and recheck workspace setup.':catalogue.error?'Model catalogue unavailable. Check OpenCode in your terminal and retry.':catalogue.loading?'Loading compatible models. Start conversation is unavailable until the catalogue responds.':!catalogue.models?.length?'No compatible models are currently available. Start conversation is unavailable.':readiness.missing.length?'Required conversation routes are unavailable. Choose listed models or retry the catalogue.':'The conversation profile is not verified. Retry the catalogue.';
  if(gate){gate.hidden=readiness.usable;gate.classList?.toggle('error',!!catalogue.error);}
  if(status)status.textContent=readiness.usable?'':message;
  if(retry){retry.hidden=readiness.usable||!!catalogue.loading;retry.disabled=retry.hidden;}
  if(submit){submit.disabled=!readiness.usable||(typeof creatingConversation!=='undefined'&&creatingConversation);if(readiness.usable)submit.removeAttribute?.('aria-describedby');else submit.setAttribute?.('aria-describedby','create-model-catalogue-status');}
  return readiness;
}
function syncModelOptions(data={}) {
  const catalogue=data.usable&&Array.isArray(data.models)?data.models.filter(value=>typeof value==='string'):[];
  // A responsive catalogue without any compatible routes is an empty state,
  // not a usable selection state. Preserve saved selections visibly, but do
  // not allow a new task to start until a compatible route is available.
  const usable=data.usable===true&&catalogue.length>0;
  modelCatalogue={models:catalogue,usable,lastUsableModels:usable?catalogue:modelCatalogue.lastUsableModels,loading:data.loading===true,error:data.error?'Model catalogue unavailable. Check the provider in your terminal.':null,reasoning_levels:data.reasoning_levels||data.reasoningLevels||{}};
  if(data.conversation_routes)conversationProfile={routes:data.conversation_routes,controls:data.conversation_controls,required:data.conversation_required_roles,pattern:data.conversation_model_pattern,aliases:data.conversation_aliases,visual:data.conversation_visual===true};
  if(data.conversation_readiness)syncConversationTransport(data.conversation_readiness);
  const conversationData=data.conversation_catalogue||data,conversationModels=conversationData.usable&&Array.isArray(conversationData.models)?conversationData.models.filter(value=>typeof value==='string'&&(!conversationProfile?.pattern||new RegExp(conversationProfile.pattern,'i').test(value))):[],conversationUsable=conversationData.usable===true&&conversationModels.length>0;
  conversationCatalogue={models:conversationModels,usable:conversationUsable,error:!!conversationData.error,loading:conversationData.loading===true};
  const defaults=data.conversation_defaults||{};
  for (const role of ['glm','plan_reviewer','astra','terra','sol','completion']) {
    const select = $('#'+role+'-model');if(!select)continue;
    if(defaults[role])select.dataset.defaultModel=defaults[role];
    const defaultModel=select.dataset.defaultModel||'Conversation default (not verified)';
    let previous = data.conversation_defaults&&select.dataset.userSelected!=='true'?'':select.value;
    if(conversationProfile?.pattern&&select._savedModel!==undefined){previous=select._savedModel;delete select._savedModel;}
    const values=conversationModels;
    select.replaceChildren(Object.assign(n('option','Default · '+defaultModel),{value:''}));
    const groups=new Map();
    for (const value of values) {
      const provider=value.split('/')[0];
      if(!groups.has(provider)){
        const label=provider==='openai'?'OpenAI':provider==='zai-coding-plan'?'Z.ai Coding Plan':provider;
        const group=Object.assign(n('optgroup'),{label});groups.set(provider,group);select.append(group);
      }
      groups.get(provider).append(Object.assign(n('option',value),{value}));
    }
    const valid=!previous||conversationProfile?.aliases?.[role]?.includes(previous)||(conversationProfile?.pattern&&new RegExp(conversationProfile.pattern,'i').test(previous)),selection=valid?previous:'invalid';
    if(previous&&!values.includes(previous))select.append(Object.assign(n('option',valid?'Unavailable selection: '+previous:'Invalid model selection'),{value:selection}));
    select.value=selection;select.disabled=!conversationUsable;
    if(role!=='glm'&&typeof syncReasoningSelector==='function'&&typeof supportedReasoningLevels==='function')syncReasoningSelector('#'+role+'-reasoning-effort',supportedReasoningLevels(conversationData,role,previous||defaultModel),data.conversation_efforts?.[role]);
  }
  if(data.conversation_routes){const summary=Object.entries(data.conversation_routes).map(([role,route])=>human(role)+': '+route.model+(route.reasoning_effort?' '+route.reasoning_effort:'')).join(' · ');$('.models-disclosure summary span').textContent='Continuous planning profile';$('#create-conversation-label').textContent='Requirements and plan review stay in this conversation.';$('#create-conversation-label').nextElementSibling.textContent=summary;}
  const detailStatus=$('#model-catalogue-status');
  const detail=(conversationData.error?'Model catalogue unavailable. Check OpenCode in your terminal and retry.':conversationData.loading?'Loading compatible models.':conversationUsable?conversationModels.length+' provider/model identifiers are listed.':'No compatible models are currently available.')+' Conversations use built-in OpenCode, not the terminal provider profile. Model listing does not verify authentication or billing. Existing runs keep their saved models.';
  if(detailStatus){detailStatus.textContent=detail;detailStatus.className='field-note'+(conversationData.error?' error':'');}
  syncConversationReadiness();
  if(typeof latestRun!=='undefined'&&latestRun){
    if(typeof renderTaskModelSettings==='function')renderTaskModelSettings(latestRun);
    if(typeof renderPrimaryAction==='function')renderPrimaryAction(latestRun);
  }
}
async function loadModels(refresh=false) {
  const request=++modelRequest, retry=$('#retry-models');
  syncModelOptions({loading:true});
  if(retry)retry.disabled=true;
  try {
    const data=refresh?await api('/api/models/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}):await api('/api/models');
    if(request===modelRequest)syncModelOptions(data);
  } catch(error) { if(request===modelRequest)syncModelOptions({error:error.message}); }
  finally { if(request===modelRequest&&retry&&!retry.hidden)retry.disabled=false; }
}
function setup(data) {
  syncWorkspaces(data.workspaces || []);
  if (!init) {
    init=true;
     $('#retry-models').onclick=()=>loadModels(true);
     loadModels();
     if(setupInitiallyEmptyLocation&&!(data.runs||[]).length&&!(data.conversations||[]).length&&typeof openWorkspaceSetup==='function')openWorkspaceSetup();
  }
  $('#registry-location').textContent=data.registry?.location || 'The registry location is currently unavailable.';
  renderArchivedTasks(data);
  dashboardNotice(actionProblem || (data.registry?.error ? 'Automatic task discovery is unavailable: '+data.registry.error : ''));
  renderRoots(data.watch_roots || []);renderProjectManager(data);renderTemporaryHistory(data);
}
function emptyState(title, text, action) {
  const host=card('','empty-state'); host.append(icon('layers'),n('h2',title),n('p',text));
  if (action) host.append(button('Start a conversation',showNewTask,'primary')); return host;
}
function unattachedConversations(data){return (data.conversations||[]).filter(doc=>doc.attachment?.status!=='linked');}
/* Project-grouped sidebar workspace: each project's conversations render as
   indented rows inside an expandable group; project-free conversations stay
   visible in a "No project" group without being attached to any project. */
function conversationWorkspace(doc) {
  const attachment=doc?.attachment;
  return attachment?.project_workspace||doc?.project_workspace||attachment?.workspace||'';
}
function runConversationAttention(run) {
  const info=statusInfo(run);
  if(info.group!=='attention')return '';
  return ['Approve plan','Review output'].includes(info.label)?'Approval requested':'Awaiting your reply';
}
function docConversationAttention(doc) {
  if(doc?.project_scope_confirmation&&!doc.archived_at&&!doc.project_removed)return 'Confirm project folder in chat';
  // Amber attention means saved human input is pending. The list summary
  // carries the store-verified intake projection; a delivery error or an
  // autonomous recovery is not a human request, so without that saved
  // projection an unattached conversation row never carries the marker
  // (R8/R17). Run rows derive theirs from saved escalation state.
  const request=doc&&doc.human_request;
  return request&&typeof request==='object'&&request.kind==='intake'
    &&typeof request.decision_needed==='string'&&!!request.decision_needed.trim()
    ?'Awaiting your reply':'';
}
function projectGroupStates() {
  try { const saved=JSON.parse(stored('project-groups','{}'));return saved&&typeof saved==='object'&&!Array.isArray(saved)?saved:{}; }
  catch { return {}; }
}
function projectGroupOpen(path) { const states=projectGroupStates(),key=path||'__no_project__';return key in states?states[key]!==false:true; }
function toggleProjectGroup(path) {
  const states=projectGroupStates(),key=path||'__no_project__';
  states[key]=!projectGroupOpen(path);persist('project-groups',JSON.stringify(states));
  if(latestData)renderProjectGroups(latestData);
}
function activeProjectTitle() {
  if(!activeProject)return '';
  if(latestData){
    const known=[...new Set([...(latestData.workspaces||[]),...(latestData.runs||[]).map(run=>run.workspace).filter(Boolean)])];
    if(!known.includes(activeProject))return '';
  }
  return projectLabel(activeProject);
}
function syncNewTaskScope() {
  const title=activeProjectTitle(),text=title?'New conversation in '+title:'New conversation';
  const label=$('#new-task-scope-label');if(label)label.textContent=text;
  const control=$('[data-new-task]');if(control)control.setAttribute('aria-label',text);
  const scope=$('#create-scope');
  if(scope){const scoped=newTaskProject?projectLabel(newTaskProject):'';scope.hidden=!scoped;scope.textContent=scoped?'New conversation in '+scoped:'';}
}
function sidebarConversationRow(row) {
  const element=card('','conversation-nav-row'+(row.selected?' selected':''));
  element.setAttribute('role','button');element.tabIndex=0;element.dataset.conversationKey=row.key;
  focusKey(element,'conversation-row:'+row.key);
  element.setAttribute('aria-label',row.title+(row.attention?' · '+row.attention:''));
  element.append(Object.assign(n('span',row.title),{className:'conversation-nav-title'}));
  const marks=card('','conversation-row-marks');
  if(row.attention){
    const marker=card('!','attention-marker');
    marker.title=row.attention;marker.setAttribute('aria-label',row.attention);marker.setAttribute('role','img');marker.tabIndex=0;
    focusKey(marker,'conversation-marker:'+row.key);
    marks.append(marker);
  }
  if(row.complete){
    const tick=card('✓','complete-tick');
    tick.title='Completed';tick.setAttribute('aria-label','Completed');tick.setAttribute('role','img');tick.tabIndex=0;
    focusKey(tick,'conversation-tick:'+row.key);
    marks.append(tick);
  }
  element.append(marks);
  element.onclick=row.open;
  element.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();row.open();}};
  return element;
}
function projectGroupNode(path,rows,query) {
  const group=card('','project-group');group.dataset.project=path||'';
  const open=query?true:projectGroupOpen(path);
  const header=card('','project-group-header');
  const name=path?projectLabel(path):'No project';
  const toggle=focusKey(button('',()=>toggleProjectGroup(path),'project-group-toggle'),'project-group:'+(path||'__no_project__'));
  toggle.setAttribute('aria-expanded',String(open));
  toggle.setAttribute('aria-label',name+' conversations, '+(open?'collapse':'expand'));
  toggle.append(Object.assign(n('span',''),{className:'project-group-chevron'}),Object.assign(n('span',name),{className:'project-group-name'}),Object.assign(n('span',String(rows.length)),{className:'nav-count'}));
  const scope=path?'New conversation in '+name:'New conversation without a project';
  const create=focusKey(button('＋',()=>startProjectConversation(path||''),'project-group-new'),'project-new:'+(path||'__no_project__'));
  create.title=scope;create.setAttribute('aria-label',scope);
  header.append(toggle,create);
  const list=card('','project-group-rows');
  for(const row of rows)list.append(sidebarConversationRow(row));
  list.hidden=!open;
  group.append(header,list);
  return group;
}
function sidebarConversationRows(data,projects) {
  const runs=(data.runs||[]).filter(run=>run.run&&!run.error&&!expiredTemporaryEntry(run)&&!removedProject(run.workspace));
  const linked=new Map();
  for(const doc of data.conversations||[])if(doc.attachment?.status==='linked'&&doc.attachment.run)linked.set(doc.attachment.run,doc);
  const rows=[];
  for(const run of runs){
    const doc=linked.get(run.run);
    rows.push({key:'run:'+run.run,workspace:run.workspace,title:taskTitle(run),
               attention:runConversationAttention(run),complete:statusInfo(run).group==='complete',
               selected:chosen?.run===run.run||(doc&&doc.id===activeConversation),open:()=>openRun(run)});
  }
  for(const doc of data.conversations||[]){
    if(doc.archived_at)continue;
    if(doc.attachment?.status==='linked'&&runs.some(run=>run.run===doc.attachment.run))continue;
    const workspace=conversationWorkspace(doc);
    if(workspace&&removedProject(workspace))continue;
    if(workspace&&!projects.includes(workspace)&&!(data.runs||[]).some(run=>run.workspace===workspace))continue;
    rows.push({key:'doc:'+doc.id,workspace,title:doc.title||'Untitled conversation',
               attention:docConversationAttention(doc),complete:false,
               selected:activeConversation===doc.id,open:()=>openConversation(doc.id)});
  }
  return rows;
}
function renderProjectGroups(data) {
  const host=$('#projects');if(!host)return;
  const scroll=host.scrollTop;
  const projects=dashboardProjects(data);
  const rows=sidebarConversationRows(data,projects);
  const query=conversationSearch.trim().toLowerCase();
  const matches=row=>!query||String(row.title||'').toLowerCase().includes(query);
  const groupPaths=[...new Set([...projects,...rows.map(row=>row.workspace).filter(Boolean)])];
  const rendered=[];
  for(const path of groupPaths){
    const members=rows.filter(row=>row.workspace===path).filter(matches);
    if(query&&!members.length)continue;
    rendered.push(projectGroupNode(path,members,query));
  }
  const free=rows.filter(row=>!row.workspace).filter(matches);
  if(free.length)rendered.push(projectGroupNode('',free,query));
  host.replaceChildren(...rendered);
  host.scrollTop=scroll;
}
function renderTasks(data) {
  const runs=data.runs||[],actual=runs.filter(run=>run.run),projects=dashboardProjects(data);
  renderArchiveSuggestions(actual.filter(run=>!projectFilter||run.workspace===projectFilter));
  const countFor=list=>Object.fromEntries(taskGroups().map(([key])=>[key,list.filter(run=>statusInfo(run).group===key).length]));
  const current=projectCurrentRuns(actual).filter(run=>!expiredTemporaryEntry(run)),counts=countFor(current);
  $('#all-count').textContent=current.length+unattachedConversations(data).length;$('#project-count').textContent=projects.length;
  renderProjectGroups(data);
  const selector=$('#project-filter'),signature=JSON.stringify(projects);
  if(selector.dataset.options!==signature){selector.replaceChildren(Object.assign(n('option','All projects'),{value:''}));for(const path of projects)selector.append(Object.assign(n('option',projects.filter(other=>projectTitle(other)===projectTitle(path)).length>1?path:projectTitle(path)),{value:path,title:path}));selector.dataset.options=signature;}selector.value=projectFilter;
  document.querySelectorAll('[data-view="tasks"]').forEach(element=>element.classList.toggle('selected',currentView==='tasks'&&!projectFilter&&taskFilter==='all'));
  $('#task-status-filter').value=taskFilter;
  renderProjectScope();
  $('#workspace-heading').textContent=projectFilter?projectTitle(projectFilter):'All work';
  $('#workspace-description').textContent=projectFilter?'Active work and saved history in this project.':'From the first idea to the finished change.';
  const scope=runs.filter(run=>!projectFilter||run.workspace===projectFilter),projectSummary=projectRunSummary(scope),currentProjectRuns=projectFilter?projectCurrentRuns(scope):scope.filter(run=>!isPreviewRun(run)),defaultScope=currentProjectRuns.filter(run=>!isPreviewRun(run)&&!expiredTemporaryEntry(run)),savedHistory=scope.filter(run=>expiredTemporaryEntry(run)||isPreviewRun(run)||(projectFilter&&!defaultScope.includes(run))),visibleCounts=countFor(defaultScope.filter(run=>run.run)),summary=$('#workspace-summary');visibleCounts.other=savedHistory.filter(run=>run.run).length;summary.replaceChildren();
  renderProjectOverview(scope);
  for(const [key,label] of workspaceFilterDefinitions){const count=visibleCounts[key]||0,item=button('',()=>filterTasks(key),'summary-filter');item.dataset.workspaceFilter=key;focusKey(item,'workspace-filter:'+key);item.setAttribute('aria-label',label+' '+count);item.setAttribute('aria-pressed',String(taskFilter===key));item.append(n('span',label),n('strong',String(count)));summary.append(item);}
  const query=searchText.toLowerCase(),filtered=(taskFilter==='other'?savedHistory:defaultScope).filter(run=>(taskFilter==='all'||taskFilter==='other'||statusInfo(run).group===taskFilter)&&(!query||[run.task,run.workspace,run.status,statusInfo(run).reason,statusInfo(run).label].some(value=>String(value||'').toLowerCase().includes(query))));
  const visibleTaskCount=filtered.filter(run=>run.run).length;
  $('#task-scope-note').textContent=(taskFilter==='all'&&projectFilter?projectSummaryLabel(projectSummary)+(projectSummary.previews?' · dry runs hidden':''):taskFilter==='all'?'Active work and saved history':taskGroups().find(group=>group[0]===taskFilter)?.[1]||'All statuses')+' · '+(projectFilter?projectTitle(projectFilter):'All projects')+' · '+visibleTaskCount+' '+(visibleTaskCount===1?'task':'tasks')+(query?' matching your search':'');
  if(currentView==='tasks')$('#breadcrumb-title').textContent=taskScopeTitle();
  const host=$('#runs');host.replaceChildren();
  const drafts=unattachedConversations(data).filter(doc=>(!projectFilter||doc.attachment?.workspace===projectFilter)&&(!query||String(doc.title).toLowerCase().includes(query)));
  if(taskFilter==='all'&&drafts.length){const section=card('','task-group'),heading=card('','task-group-heading');heading.append(n('h2','Planning conversations'),n('span',drafts.length));section.append(heading);for(const doc of drafts){const row=focusKey(button('',()=>openConversation(doc.id),'conversation-row'),'draft-row:'+doc.id),copy=card('','conversation-row-copy');copy.append(n('h2',concise(doc.title||'Untitled conversation',86)),n('p',doc.attachment?.workspace?basename(doc.attachment.workspace):'Project optional'));row.append(Object.assign(n('span','G'),{className:'astra-avatar'}),copy,Object.assign(n('span',conversationStatus(doc)),{className:'badge'}));section.append(row);}host.append(section);}
  const sections=[
    ['attention','Waiting on you','Questions, plan approval, or a review that only you can resolve.'],
    ['stopped','Paused / issues','Recovery, interruption, and saved checkpoints requiring careful review.'],
    ['running','In progress','Verified workers appear before recorded activity whose live process is not confirmed.'],
    ['complete','Completed','Finished tasks. No reply is needed.'],
    ['other','Other saved tasks','Previews and saved records whose next action is not known.']
  ];
  for(const [key,label,description] of sections){
    const members=orderedWorkspaceRuns(filtered).filter(run=>workspaceSection(run)===key);
    if(!members.length)continue;
    const section=card('','task-group'),heading=card('','task-group-heading');heading.append(n('h2',label),n('span',String(members.length)),n('p',description));section.append(heading);
    for(const run of members){
      const info=statusInfo(run),facts=workspaceRowFacts(run),row=card('','task-row'),main=card('','task-main'),copy=card('','task-copy');
      row.dataset.workspacePriority=String(workspacePriority(run));row.dataset.runtimeState=facts.state;
      main.append(Object.assign(n('span',projectTitle(run.workspace).slice(0,1).toUpperCase()),{className:'project-avatar'}));
      const title=n('span',taskTitle(run));title.className='task-name';title.title=String(run.task||run.error||'');
      const meta=n('div','Project: '+projectTitle(run.workspace)+' · '+facts.updated);meta.className='task-meta';meta.title=run.run||run.workspace||'';copy.append(title,meta);main.append(copy);
      const reason=card('','task-reason');reason.append(Object.assign(n('p',facts.step),{className:'task-row-step'}),Object.assign(n('p',facts.freshness),{className:'task-row-freshness'}));reason.title=String(info.reason||'');
      const action=card('','task-row-action');action.append(badge(run),Object.assign(n('span',facts.action+' →'),{className:'next-action'}));row.append(main,reason,action);
      if(run.run&&!run.error){row.tabIndex=0;focusKey(row,'task-row:'+run.run);row.setAttribute('role','button');row.setAttribute('aria-label',info.action+': '+taskTitle(run)+' · '+projectTitle(run.workspace)+' · '+taskIdentity(run));row.onclick=()=>openRun(run);row.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();row.onclick();}};}
      else {row.classList.add('unavailable');action.lastChild.textContent='Check the project folder';}section.append(row);
    }host.append(section);
  }
  if(projectFilter&&removedProject(projectFilter)){host.append(emptyState('This project was removed.','Restore it to show its tasks and linked conversations again. Files and history stay on disk.'));}
  else if(!host.childElementCount)host.append(emptyState(actual.length?'No tasks match this view.':'Make room for your next idea.',actual.length?'Try a different status, project, or search.':'Start a conversation with your team. Your terminal tasks will appear here too.',!actual.length));
  syncNewTaskScope();
}
function renderRoots(roots) {
  const host=$('#watch-roots');host.replaceChildren();
  for(const root of roots){const row=card('','root-row');row.append(n('code',root.path),n('small',root.runtime?'This session':'CLI root · protected'));if(root.removable)row.append(button('Remove',()=>rootAction('remove',root.path)));host.append(row);}
}
async function rootAction(action,path) {try{await api('/api/watch-roots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,path})});$('#watch-root-error').textContent='';if(action==='add')$('#watch-root').elements.path.value='';await refresh();}catch(error){$('#watch-root-error').textContent=error.message;$('#watch-root-error').className='error';}}
$('#watch-root').onsubmit=event=>{event.preventDefault();rootAction('add',event.target.elements.path.value);};
$('#create').onsubmit=async event=>{
  event.preventDefault();if(creatingConversation)return;
  if(!syncConversationReadiness().usable)return;
  const text=$('#new-goal').value.trim(),error=$('#create-error');if(!text)return;
  const models={};
  for(const role of ['glm','plan_reviewer','astra','terra','sol','completion'])if($('#'+role+'-model').dataset.userSelected==='true'&&$('#'+role+'-model').value)models[role+'_model']=$('#'+role+'-model').value;
  for(const role of ['plan_reviewer','astra','terra','sol','completion'])if($('#'+role+'-reasoning-effort').dataset.userSelected==='true')models[role+'_reasoning_effort']=$('#'+role+'-reasoning-effort').value;
  const signature=JSON.stringify({text,models,workspace:newTaskProject});
  if(!conversationRequest||conversationRequest.signature!==signature)conversationRequest=savedRequest('create-request',{text,models,workspace:newTaskProject});
  creatingConversation=true;$('#create-submit').disabled=true;$('#create-submit').textContent='Starting conversation…';error.hidden=true;
  try {
    const doc=await api('/api/conversations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(conversationPayload(text,models,conversationRequest.id,newTaskProject))});
    if($('#new-goal').value.trim()===text){$('#new-goal').value='';persist('new-idea','');}conversationRequest=null;persist('create-request','');newTaskProject='';openConversation(doc);
  } catch(problem) {error.textContent=problem.message;error.hidden=false;}
  finally {creatingConversation=false;syncConversationReadiness();$('#create-submit').textContent='Start conversation ↗';}
};
$('#new-goal').value=stored('new-idea');
$('#new-goal').oninput=event=>persist('new-idea',event.target.value);
for(const role of ['glm','plan_reviewer','astra','terra','sol','completion']){const select=$('#'+role+'-model'),value=stored('model:'+role);if(value){select._savedModel=value;select.dataset.userSelected='true';}select.onchange=()=>{select.dataset.userSelected='true';persist('model:'+role,select.value);syncConversationReadiness();};}
for(const role of ['plan_reviewer','astra','terra','sol','completion']){const select=$('#'+role+'-reasoning-effort'),value=stored('reasoning:'+role);if(value&&Array.from(select.options).some(option=>option.value===value)){select.value=value;select.dataset.userSelected='true';}select.onchange=()=>{select.dataset.userSelected='true';persist('reasoning:'+role,select.value);syncConversationReadiness();};}

function openConversation(doc) {
  $('#archive-conversation').disabled=true;$('#conversation-archived-banner').hidden=true;
  const id=typeof doc==='string'?doc:doc.id;if(!id)return;
  if(activeConversation!==id){const dialog=$('#draft-plan-dialog');if(dialog?.open){dialog._returnFocus=null;dialog.close();}$('#draft-messages').replaceChildren();$('#draft-messages').dataset.rendered='';$('#draft-title').textContent='Loading conversation…';$('#draft-problem').hidden=true;$('#draft-text').value=stored('conversation-draft:'+id);$('#draft-text').dataset.conversation=id;$('#attach-mode').value='existing';$('#workspaces').value='';$('#project-path').value='';$('#attach-error').textContent='';attachModeChanged();}
  activeConversation=id;chosen=null;latestRun=null;seq++;setView('draft-conversation');rememberSelection('conversation='+encodeURIComponent(id));
  if(typeof doc==='object')renderDraftConversation(doc);refresh();window.scrollTo({top:0});
}
function conversationArchiveBlocked(doc){return !!doc.archived_at||conversationArchivePending.has(doc.id);}
function conversationArchiveButton(doc,action,label){const b=focusKey(button(label,()=>changeConversationArchive(doc,action)),'conversation-archive:'+action+':'+doc.id);b.disabled=conversationArchivePending.has(doc.id);return b;}
function renderArchivedConversations(data){
  const docs=data.archived_conversations||[],host=$('#archived-conversation-list');host.replaceChildren();$('#conversation-archives-title').textContent='Archived conversations ('+docs.length+')';
  for(const doc of docs){const row=card('','managed-project'),copy=card('','managed-project-copy');copy.append(button(doc.title||'Untitled conversation',()=>openConversation(doc.id),'text-button'),n('p',doc.attachment?.workspace?basename(doc.attachment.workspace):'No project attached'));row.append(copy,conversationArchiveButton(doc,'restore','Restore conversation'));host.append(row);}
  if(!docs.length)host.append(n('p','No archived conversations.'));
}
function renderConversationArchiveNotice(){
  const host=$('#conversation-archive-notice');host.replaceChildren();host.hidden=!conversationArchiveNotice;if(!conversationArchiveNotice)return;
  const {doc,action,error}=conversationArchiveNotice;host.append(n('p',error||'Conversation '+(action==='archive'?'archived.':'restored.')));
  if(error)host.append(conversationArchiveButton(doc,action,'Retry'));else if(action==='archive')host.append(conversationArchiveButton(doc,'restore','Undo'));
  host.append(button('View archived',()=>{setView('archived');$('#conversation-archives').open=true;}),button('Dismiss',()=>{conversationArchiveNotice=null;renderConversationArchiveNotice();},'text-button'));
}
function reviewConversationArchive(doc){if(conversationArchiveBlocked(doc))return;conversationArchiveReview={...doc,opener:document.activeElement};$('#conversation-archive-name').textContent=doc.title||'Untitled conversation';$('#conversation-archive-error').hidden=true;$('#conversation-archive-dialog').showModal();$('#conversation-archive-cancel').focus();}
function closeConversationArchive(){if(conversationArchiveReview&&conversationArchivePending.has(conversationArchiveReview.id))return;const opener=conversationArchiveReview?.opener;conversationArchiveReview=null;$('#conversation-archive-dialog').close();if(opener?.isConnected)opener.focus();}
$('#conversation-archive-cancel').onclick=closeConversationArchive;
$('#conversation-archive-dialog').addEventListener('cancel',event=>{event.preventDefault();closeConversationArchive();});
$('#conversation-archive-confirm').onclick=()=>{if(conversationArchiveReview)changeConversationArchive(conversationArchiveReview,'archive');};
async function changeConversationArchive(doc,action){
  if(conversationArchivePending.has(doc.id))return;conversationArchivePending.add(doc.id);seq++;
  const dialog=action==='archive'&&conversationArchiveReview?.id===doc.id;
  if(dialog){$('#conversation-archive-confirm').disabled=true;$('#conversation-archive-cancel').disabled=true;$('#conversation-archive-error').hidden=true;}
  try{
    const saved=await api('/api/conversation/archive',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:doc.id,action})});seq++;
    if(latestData){latestData.conversations=(latestData.conversations||[]).filter(row=>row.id!==doc.id);latestData.archived_conversations=(latestData.archived_conversations||[]).filter(row=>row.id!==doc.id);if(action==='archive')latestData.archived_conversations.push(saved);}
    if(action==='archive'&&activeConversation===doc.id){activeConversation=null;latestConversation=null;setView('conversations');}
    else if(activeConversation===doc.id)renderDraftConversation(saved);
    conversationArchiveNotice={doc:{id:doc.id,title:doc.title},action,expiresAt:Date.now()+12000};if(dialog){conversationArchiveReview=null;$('#conversation-archive-dialog').close();}
  }catch(error){if(dialog){$('#conversation-archive-error').textContent=error.message;$('#conversation-archive-error').hidden=false;}else conversationArchiveNotice={doc,action,error:error.message};}
  finally{conversationArchivePending.delete(doc.id);seq++;$('#conversation-archive-confirm').disabled=false;$('#conversation-archive-cancel').disabled=false;if(latestData)renderConversations(latestData);renderConversationArchiveNotice();if(dialog&&!conversationArchiveReview){$('#conversation-archive-notice').tabIndex=-1;$('#conversation-archive-notice').focus();}await refresh();}
}
function renderConversations(data) {
  renderArchivedConversations(data);
  const docs=data.conversations||[];$('#conversation-count').textContent=docs.length;
  const host=$('#conversation-list');host.replaceChildren();
  for(const doc of docs){const row=button('',()=>openConversation(doc.id),'conversation-row');const copy=card('','conversation-row-copy');copy.append(n('h2',doc.title||'Untitled conversation'),n('p',doc.attachment?.workspace?basename(doc.attachment.workspace):'No project attached'));row.append(Object.assign(n('span','G'),{className:'astra-avatar'}),copy,Object.assign(n('span',conversationStatus(doc)),{className:'badge '+(doc.status==='error'?'attention':'')}),n('span','›'));host.append(row);}
  if(!docs.length)host.append(emptyState('Start with a conversation.','Explore an idea with your team. Your conversations are saved here, even before you choose a project.',true));
}
function inlineText(host,text) {
  const pieces=String(text).split(/(\*\*[^*\n]+\*\*|`[^`\n]+`)/g);
  for(const piece of pieces)host.append(piece.startsWith('**')&&piece.endsWith('**')?n('strong',piece.slice(2,-2)):piece.startsWith('`')&&piece.endsWith('`')?n('code',piece.slice(1,-1)):document.createTextNode(piece));
}
function messageBody(text) {
  const host=card('','message-body'),lines=String(text).split('\n');let paragraph=[],list=null,code=null;
  const flush=()=>{if(paragraph.length){const p=n('p','');inlineText(p,paragraph.join('\n'));host.append(p);paragraph=[];}list=null;};
  for(const line of lines){
    if(/^\s*```/.test(line)){flush();if(code){host.append(n('pre',code.join('\n')));code=null;}else code=[];continue;}
    if(code){code.push(line);continue;}
    if(!line.trim()){flush();continue;}
    const heading=line.match(/^#{1,6}\s+(.+)$/),bullet=line.match(/^\s*([-*]|\d+\.)\s+(.+)$/);
    if(heading){flush();const title=n('h3','');inlineText(title,heading[1]);host.append(title);}
    else if(bullet){if(paragraph.length)flush();const kind=/\d/.test(bullet[1])?'ol':'ul';if(!list||list.tagName.toLowerCase()!==kind){list=n(kind,'');host.append(list);}const item=n('li','');inlineText(item,bullet[2]);list.append(item);}
    else{list=null;paragraph.push(line);}
  }
  flush();if(code)host.append(n('pre',code.join('\n')));return host;
}
function appendMessage(host,message) {
  const row=card('','chat-message '+(message.role==='user'?'learner':'assistant'));
  row.dataset.messageKey=messageAnchorKey(message);
  const name=message.speaker||(message.role==='user'?'You':roleDisplayName('requirements')),speaker=message.role==='user'?'You':roleDisplayName(name);
  const text=message.text||'',body=message.role==='user'?n('p',text):messageBody(text);
  const at=messageTime(message),stamp=at?new Date(at).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'';
  const meta=Object.assign(n('div',''),{className:'speaker'});
  meta.dataset.initials=speaker.slice(0,1).toUpperCase();
  meta.append(n('strong',speaker));
  if(message.planning_history)meta.append(n('span','Planning discussion'));
  if(stamp){const time=n('time',new Date(at).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}));time.title=stamp;time.dateTime=new Date(at).toISOString();meta.append(time);}
  if(message.model||message.attribution?.model)meta.append(Object.assign(n('span',[message.model||message.attribution.model,message.reasoning_effort||message.attribution?.reasoning_effort].filter(Boolean).join(' · ')),{className:'message-model'}));
  row.append(meta);
  if(!message.expanded&&(text.length>1000||(message.planning_history&&text.length>320))){row.append(n('p',concise(text,260)),disclosure(message.planning_history?'Read saved planning discussion':'Read the full message','message:'+speaker+':'+text.slice(0,90),[body],chosen?.run||activeConversation||''));}else row.append(body);
  if(message.role==='user'&&message.status&&!['saved','received'].includes(message.status))row.append(Object.assign(n('small',receiptLabel(message)),{className:'message-receipt'}));host.append(row);return row;
}
function draftUpdateCard(doc){
  const update=doc.draft_update,host=card('','lifecycle-card');
  host.dataset.draftUpdate='true';host.append(n('h3','Draft update'));
  if(update.coalesced&&!update.stalled){
    host.append(n('p',update.answers_since_update+' of your latest messages are saved. The Planner is finishing the draft it already started; one update covering all of them starts as soon as it finishes.'));
    host.append(n('p','You can keep answering. This does not approve a plan or start implementation.'));
    return host;
  }
  host.append(n('p',update.answers_since_update+' of your latest messages are saved. '+(update.coalesced?'The draft they were waiting for has stopped reporting progress, so its update may not arrive.':'Automatic draft updates group '+update.answers_per_update+' messages to limit extra model calls.')));
  host.append(n('p','You can keep answering or update the draft now. This does not approve a plan or start implementation.'));
  const action=button('Update draft now',()=>refreshConversationDraft(doc),'secondary');
  action.disabled=!update.can_refresh||conversationPending.has(doc.id)||conversationArchiveBlocked(doc)||doc.task_archived||doc.project_removed;
  host.append(action);return host;
}
async function refreshConversationDraft(doc){
  const update=doc.draft_update;
  if(!update?.can_refresh||conversationPending.has(doc.id)||conversationArchiveBlocked(doc)||doc.task_archived||doc.project_removed||doc.id!==activeConversation)return;
  const target={requirements_revision:update.requirements_revision,logical_turn_id:update.logical_turn_id};
  const key='draft-refresh:'+doc.id,request=savedRequest(key,target);
  conversationPending.add(doc.id);seq++;renderDraftConversation(doc);
  try{
    const saved=await api('/api/conversation/refresh-draft',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:doc.id,...target,request_id:request.id})});
    persist(key,'');renderDraftConversation(saved);
  }catch(error){dashboardNotice(error.message);}
  finally{conversationPending.delete(doc.id);if(activeConversation===doc.id)renderDraftConversation(latestConversation||doc);refresh();}
}

function renderDraftDeliveryProblem(problem,doc,retry){
  problem.replaceChildren();
  const failedDraft=doc.plan_drafts?.at(-1)?.status==='failed';
  const plannerFailed=failedDraft||['SAFE_NOT_DISPATCHED','UNCERTAIN'].includes(doc.planner_delivery?.state);
  const message=retry?.error||doc.error||(plannerFailed?'The draft update could not finish. Your answers and previous draft are saved.':null);
  if(!doc.archived_at&&!doc.task_archived&&!doc.project_removed&&message){
    problem.append(Object.assign(n('p',message),{className:'error'}));
    if(doc.project_scope_confirmation){
      const scope=doc.project_scope_confirmation;problem.append(n('p',scope.workspace));
      const action=button('Confirm this project folder',async()=>{
        action.disabled=true;
        try{const saved=await api('/api/conversation/confirm-scope',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:doc.id,token:scope.token})});renderDraftConversation(saved);refresh();}
        catch(error){dashboardNotice(error.message);action.disabled=false;}
      });problem.append(action);
    }else if(doc.project_scope_error){
      problem.append(n('p','Your conversation and project files are preserved.'));
    }else if(conversationRetryAllowed(doc)){
      const action=button(retry?'Retry sending':plannerFailed?'Retry draft update':'Retry planner',()=>retry?sendDraftMessage(doc,retry):retryConversation(doc));
      action.disabled=conversationPending.has(doc.id);problem.append(action);
    }else problem.append(n('p','Delivery is not safe to repeat. The saved turn and any provider result are preserved; no second request will be sent.'));
  }
  problem.hidden=!problem.childElementCount;
}

function renderDraftConversation(doc) {
  if(doc.id!==activeConversation)return;latestConversation=doc;
  if(typeof returnSetupModels==='function')returnSetupModels();
  const scoped=conversationWorkspace(doc);
  if(!doc.attachment?.run&&scoped!==activeProject){activeProject=scoped;persist('active-project',activeProject);}
  if(!doc.attachment?.run)syncNewTaskScope();
  if(!doc.archived_at&&!doc.task_archived&&!doc.project_removed&&doc.attachment?.status==='linked'&&doc.attachment.run){
    const draft=stored('conversation-draft:'+doc.id),target=stored('task-draft:'+doc.attachment.run);
    if(draft&&!target){persist('task-draft:'+doc.attachment.run,draft);changeDrafts.set(doc.attachment.run,draft);}
    openRun(doc.attachment,'interview');return;
  }
  const archiveControl=$('#archive-conversation');archiveControl.hidden=!!doc.archived_at;archiveControl.disabled=conversationArchivePending.has(doc.id);archiveControl.onclick=()=>reviewConversationArchive(doc);
  const archiveBanner=$('#conversation-archived-banner');archiveBanner.replaceChildren();archiveBanner.hidden=!doc.archived_at;
  if(doc.archived_at)archiveBanner.append(n('p','This conversation is archived. Restore it to continue.'),conversationArchiveButton(doc,'restore','Restore conversation'));
  renderQuietPlanPreview(doc);
  $('#draft-title').textContent=concise(doc.title||'Your idea',86);$('#draft-status').textContent=doc.project_removed?'Project removed':conversationStatus(doc);
  const route=doc.configured_routes?.requirements_gatherer||doc.configured_routes?.gatherer||doc.configured_routes?.glm;const model=route?.model||doc.models?.glm_model||doc.models?.glm||'saved requirements model';
  $('#draft-subtitle').textContent=doc.attachment?.workspace?'Connecting '+basename(doc.attachment.workspace):scoped&&!doc.attachment?'Project '+projectLabel(scoped)+' · Planning model: '+model+'. This conversation starts inside that project.':'Planning model: '+model+'. Bring in a project when you’re ready.';
  $('#draft-subtitle').title=model;
  $('#draft-text').placeholder=scoped&&!doc.attachment?'Message your team in '+projectLabel(scoped)+'…':'Message your team…';
  const host=$('#draft-messages'),signature=JSON.stringify([doc.messages||[],doc.status,doc.draft_update,conversationPending.has(doc.id)]);
  if(host.dataset.rendered!==signature){const scroll=$('#draft-scroll'),first=!host.dataset.rendered,near=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<90,position=scroll.scrollTop;host.replaceChildren();renderMessageHistory(host,doc.messages||[],doc.id);if(doc.draft_update?.held&&!doc.archived_at&&!doc.attachment)host.append(draftUpdateCard(doc));if(doc.status==='thinking'||conversationPending.has(doc.id)){const thinking=card('','chat-thinking');thinking.setAttribute('role','status');thinking.append(n('span',roleDisplayName('requirements')),n('span',doc.status==='thinking'?'Thinking through your reply…':'Sending your message…'));host.append(thinking);}host.dataset.rendered=signature;requestAnimationFrame(()=>{if(first&&restoreThreadAnchor(scroll,'conversation-scroll:'+doc.id))updateDraftScroll();else if(first||near)scrollDraftToEnd();else{scroll.scrollTop=position;updateDraftScroll();}});}
  if(typeof renderWorkspaceSetup==='function')renderWorkspaceSetup(doc);
  const pending=conversationPending.has(doc.id),thinking=doc.status==='thinking',linked=doc.attachment?.status==='linked',attaching=doc.attachment?.status==='starting';
  $('#draft-send').disabled=draftSendBlocked(doc,pending,$('#draft-text').value);$('#draft-send').textContent=pending?'Sending…':'Send ↑';
  $('#draft-delivery').textContent=thinking?'You can draft your next message while the team replies.':doc.status==='error'?'Retry the saved message to continue.':doc.attachment?'Connecting the project…':'Enter to send · Shift + Enter for a new line';
  requestAnimationFrame(()=>resizeComposer($('#draft-text')));
  renderDraftDeliveryProblem($('#draft-problem'),doc,conversationRetries.get(doc.id));
  const attachment=$('#attachment-status');attachment.replaceChildren();
  if(doc.task_archived){attachment.append(n('h3','This task is archived.'),n('p','Restore the task to continue its saved conversation.'),archiveButton(doc.attachment,'restore','Restore task'));$('#draft-send').disabled=true;}
  else if(doc.project_removed){attachment.append(n('h3','This project was removed from the dashboard.'),n('p','Your conversation and draft are saved. Restore the project to continue.'),Object.assign(n('p',doc.attachment?.workspace||''),{className:'project-path'}),projectActionButton(conversationWorkspace(doc),'restore','Restore project'));}
  else if(doc.attachment){const attached=doc.attachment;attachment.append(n('p',attached.status==='linked'?'Project attached: '+attached.workspace:attached.status==='starting'?'Connecting your project and opening the plan for review…':attached.error||'Project connection needs attention.'));if(attached.status==='linked'&&attached.run)attachment.append(button('Open project conversation →',()=>openRun(attached),'primary'));if(attached.status==='failed')attachment.append(button('Retry project handoff',()=>retryAttachment(doc)));if(attached.status==='uncertain')attachment.append(n('p','The previous handoff could not be confirmed. Inspect All tasks before trying another project.'));}
  $('#attach-disclosure').hidden=doc.project_removed||linked||attaching||!!doc.attachment;$('#attach-submit').disabled=doc.project_removed||pending||thinking||attaching||doc.status==='error'||conversationDeliveryBlocked(doc)||(Array.isArray(doc.plan_drafts)&&(!doc.plan_drafts.some(row=>row.status==='current'&&row.freshness?.state==='fresh')||['pending','failed'].includes(doc.plan_drafts.at(-1)?.status)));
  if(doc.task_archived){$('#draft-status').textContent='Task archived';$('#draft-delivery').textContent='Restore the task to continue.';$('#attach-disclosure').hidden=true;$('#attach-submit').disabled=true;}
  if(doc.project_removed)$('#draft-delivery').textContent='Restore the project to continue. Your unsent draft stays saved.';
  $('#draft-form').hidden=!!doc.archived_at;$('#draft-conversation .attach-section').hidden=!!doc.archived_at;
  if(doc.archived_at){$('#draft-status').textContent='Archived';$('#draft-send').disabled=true;$('#attach-submit').disabled=true;$('#draft-problem').hidden=true;}
  if(typeof renderScopedConversationStart==='function')renderScopedConversationStart(doc);
}
$('#draft-text').oninput=event=>{persist('conversation-draft:'+event.target.dataset.conversation,event.target.value);$('#draft-send').disabled=draftSendBlocked(latestConversation,conversationPending.has(activeConversation),event.target.value);};
$('#draft-form').onsubmit=event=>{event.preventDefault();if(latestConversation?.id===activeConversation)sendDraftMessage(latestConversation);};
async function sendDraftMessage(doc,retry) {
  if(conversationArchiveBlocked(doc)||doc.task_archived||taskArchiveBlocked(doc.attachment?.run)||doc.project_removed||doc.project_scope_error||projectBlocked(conversationWorkspace(doc))||conversationPending.has(doc.id)||conversationSendBlocked(doc)||doc.status==='thinking')return;
  const text=$('#draft-text').value.trim(),request=retry||{text,request_id:savedRequest('conversation-request:'+doc.id,{text}).id};if(!request.text)return;
  conversationPending.add(doc.id);conversationRetries.delete(doc.id);seq++;renderDraftConversation(doc);
  try{const saved=await api('/api/conversation/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:doc.id,text:request.text,request_id:request.request_id})});persist('conversation-request:'+doc.id,'');persist('conversation-scroll:'+doc.id,'');if($('#draft-text').dataset.conversation===doc.id&&$('#draft-text').value.trim()===request.text){$('#draft-text').value='';persist('conversation-draft:'+doc.id,'');}renderDraftConversation(saved);if(activeConversation===doc.id)requestAnimationFrame(()=>{scrollDraftToEnd();$('#draft-text').focus();});}
  catch(error){conversationRetries.set(doc.id,{...request,error:error.message});}
  finally{conversationPending.delete(doc.id);if(activeConversation===doc.id)renderDraftConversation(latestConversation||doc);refresh();}
}
async function retryConversation(doc) {
  if(conversationArchiveBlocked(doc)||doc.task_archived||taskArchiveBlocked(doc.attachment?.run)||doc.project_removed||doc.project_scope_error||projectBlocked(conversationWorkspace(doc))||conversationPending.has(doc.id)||!conversationRetryAllowed(doc))return;conversationPending.add(doc.id);seq++;renderDraftConversation(doc);
  try{const saved=await api('/api/conversation/retry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:doc.id})});renderDraftConversation(saved);}catch(error){dashboardNotice(error.message);}
  finally{conversationPending.delete(doc.id);refresh();}
}
async function retryAttachment(doc) {if(conversationArchiveBlocked(doc)||doc.task_archived||taskArchiveBlocked(doc.attachment?.run)||doc.project_removed||doc.project_scope_error||projectBlocked(conversationWorkspace(doc)))return;try{const saved=await api('/api/conversation/attach',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:doc.id,workspace:doc.attachment.workspace,retry:true})});renderDraftConversation(saved);refresh();}catch(error){dashboardNotice(error.message);}}
function attachModeChanged() {const create=$('#attach-mode').value==='new';$('#existing-project-field').hidden=create;$('#attach-path-field').hidden=!create&&$('#workspaces').value!=='__custom__';$('#attach-path-label').textContent=create?'New project folder':'Existing project folder';$('#attach-submit').textContent=create?'Create project & review plan →':'Review this plan →';}
$('#attach-mode').onchange=attachModeChanged;$('#workspaces').onchange=attachModeChanged;
$('#attach-form').onsubmit=async event=>{
  event.preventDefault();const doc=latestConversation;if(!doc||doc.id!==activeConversation||conversationArchiveBlocked(doc)||doc.project_removed)return;
  const create=$('#attach-mode').value==='new',custom=create||$('#workspaces').value==='__custom__',path=custom?$('#project-path').value.trim():$('#workspaces').value;
  if(!path){$('#attach-error').textContent='Choose a project or enter its folder path.';return;}
  $('#attach-submit').disabled=true;$('#attach-error').textContent='';seq++;
  try{const saved=await api('/api/conversation/attach',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:doc.id,...(custom?{project:path}:{workspace:path}),create_project:create})});renderDraftConversation(saved);refresh();}catch(error){$('#attach-error').textContent=error.message;}finally{$('#attach-submit').disabled=false;}
};

function renderDocument(value) {
  if(value===null||value===undefined)return n('span','Not specified');
  if(Array.isArray(value)){const list=n('ul','');for(const item of value){const li=n('li','');li.append(renderDocument(item));list.append(li);}if(!value.length)list.append(n('li','None'));return list;}
  if(typeof value==='object'){const list=n('dl','');list.className='document-fields';for(const [key,item]of Object.entries(value)){list.append(n('dt',human(key)));const dd=n('dd','');dd.append(renderDocument(item));list.append(dd);}return list;}
  return n('span',String(value));
}
function planLines(lines){return Array.isArray(lines)&&lines.length?lines.join('\n'):'Unavailable in saved runner state.';}
function planList(lines) {const list=n('ol','');list.className='plan-list';for(const [index,line]of (Array.isArray(lines)?lines:[]).entries()){const item=n('li','');item.append(Object.assign(n('span',index+1),{className:'plan-number'}),n('span',typeof line==='string'?line:line.objective||JSON.stringify(line)));list.append(item);}if(!list.childElementCount)list.append(Object.assign(n('p','The plan will appear here.'),{className:'plan-hint'}));return list;}
function assignmentCard(entry,run) {const host=card('','assignment-card');host.append(n('strong',entry.objective||'Recorded assignment'),disclosure('Assignment details and validation','assignment:'+(entry.id||entry.timestamp)+':'+entry.brief_revision,[renderDocument(entry)],run));return host;}
function renderAstraPlan(run) {
  const data=run.astra_plan||{},rail=$('#goal'),full=$('#plan-full');rail.replaceChildren();full.replaceChildren();
  const heading=card('','rail-heading');heading.append(n('h2','The plan'),icon('plan'));rail.append(heading);
  rail.append(Object.assign(n('p','CURRENT ASSIGNMENT'),{className:'rail-kicker'}));
  rail.append(Object.assign(n('p',data.current_assignment?.objective||(jointPlanning(run)?'The Planner and independent Plan Reviewer are shaping the plan.':'The Planner is shaping the next step.')),{className:'assignment-summary'}));
  const initialLabel=data.initial_plan_approval==='approved'?'Initial approved plan (recorded)':data.initial_plan_approval==='historically approved/inactive'?'Initial historically approved plan (inactive)':'Initial plan (approval unavailable)';
  rail.append(Object.assign(n('p',data.current_plan?.length?'CURRENT PLAN':'INITIAL PLAN'),{className:'rail-kicker'}),planList(data.current_plan?.length?data.current_plan:data.initial_plan));
  rail.append(Object.assign(n('p','Current plan approval status: '+(data.current_plan_approval||'unavailable')),{className:'rail-status'}));
  rail.append(button('View full plan & history →',()=>activateTab('plan'),'text-button'));
  const sections=[['Initial plan',initialLabel,planList(data.initial_plan)],['Current plan','Approval: '+(data.current_plan_approval||'unavailable'),planList(data.current_plan)]];
  for(const [title,note,body]of sections){const section=card('','history-section');section.append(n('h2',title),Object.assign(n('p',note),{className:'field-note'}),body);full.append(section);}
  const current=card('','history-section');current.append(n('h2','Current assigned step'));current.append(data.current_assignment?assignmentCard(data.current_assignment,run.run):n('p','No current assigned step is recorded.'));current.append(Object.assign(n('p','An assignment is not proof of completion. Execution and validation are shown separately.'),{className:'field-note'}));full.append(current);
  const history=card('','history-section');history.append(n('h2','Intermediate assignments & decisions'));
  for(const entry of data.history||[])if(!data.current_assignment||entry.id!==data.current_assignment.id||entry.brief_revision!==data.current_assignment.brief_revision)history.append(assignmentCard(entry,run.run));
  for(const [index,entry]of (data.decisions||[]).entries())history.append(disclosure((entry.status?human(entry.status):'Plan update')+' · '+(entry.timestamp||'Time unavailable'),'decision:'+index+':'+entry.timestamp,[renderDocument(entry)],run.run));
  if(history.childElementCount===1)history.append(Object.assign(n('p','Updates will appear as the team assigns and reviews the work.'),{className:'field-note'}));full.append(history);
  const briefs=card('','history-section');briefs.append(n('h2','Brief revisions'));
  for(const [index,brief]of (data.briefs||[]).entries()){const context=brief.current?(brief.approval_status==='approved'?'current · approved':'current · awaiting its own approval'):brief.historically_approved?'historically approved/inactive':'superseded';briefs.append(disclosure('Brief r'+(brief.revision??'?')+' · '+context,'brief:'+index+':'+brief.revision,[renderDocument(brief.body)],run.run));}
  if(briefs.childElementCount===1)briefs.append(n('p','Historical brief bodies unavailable.'));full.append(briefs);
  if(run.planning_messages?.length||run.discovery_summary)full.append(disclosure('Internal planning reports (not human requests)','internal-planning',[renderDocument(run.planning_messages?.length?run.planning_messages:run.discovery_summary)],run.run));
}
function waitingMessage(request){const lines=[];for(const [label,key]of [['Decision needed','decision_needed'],['Question','question'],['Discovered','discovered'],['Impact','impact'],['Options','options'],['Proposed change','proposed_delta']]){let value=request[key];if(Array.isArray(value))value=value.join(' · ');if(typeof value==='string'&&value.trim())lines.push(label+': '+value);}return lines.length?lines.join('\n'):'Resolver is waiting for your input. Details are unavailable in the saved state.';}
function questionDraftKey(run,id){return 'question-draft:'+run.run+':'+id;}
function renderQuestionAnswer(element,question,run){
  const id=String(question.id),fields=resolverResponseFields(run),key=questionDraftKey(run,id),saved=readSavedRequest(key);
  const form=n('form','');form.className='question-answer-form';
  const label=n('label','Your answer'),input=focusKey(n('textarea',''),'question:'+run.run+':'+id+':answer');
  input.id='question-answer-'+encodeURIComponent(id);input.rows=2;input.maxLength=16000;input.value=saved?.text||'';label.htmlFor=input.id;
  const send=focusKey(button('Send this answer',()=>submit()),'question:'+run.run+':'+id+':send');send.className='question-answer-submit';
  const disabled=()=>!!taskReadError||taskActionBusy(run)||taskChatPending.has(run.run);
  input.disabled=disabled();send.disabled=disabled()||!input.value.trim();
  input.oninput=()=>{persist(key,JSON.stringify({text:input.value,request_token:fields.resolver_token}));send.disabled=disabled()||!input.value.trim();};
  async function submit(){
    if(disabled()||!input.value.trim())return;
    const payload={question_id:id,text:input.value.trim(),explicit_answer:true,...fields},request=savedRequest('question-request:'+run.run+':'+id,payload);
    send.disabled=true;input.disabled=true;
    const result=await sendTaskChat(run,{...request.payload,request_id:request.id});
    input.disabled=disabled();send.disabled=disabled()||!input.value.trim();
    if(result&&!['error','failed','uncertain','launch_failed'].includes(result.status))persist(key,'');
  }
  form.onsubmit=event=>{event.preventDefault();submit();};
  form.append(label,input,send);
  if(saved?.text&&saved.request_token!==fields.resolver_token)form.append(Object.assign(n('p','Saved draft from an earlier request. Review it before sending.'),{className:'field-note'}));
  element.append(form);
}
function renderQuestion(host,question,run) {
  if(!resolverReplyCurrent(run,resolverResponseFields(run),['clarification','permission','goal_change','operational_exhaustion','blocker'])||!(run.human_escalation.questions||[]).some(current=>String(current.id)===String(question.id)&&current.question===question.question))return;
  const id=String(question.id),element=card('','chat-message pending');element.dataset.questionCard=id;
  element.append(Object.assign(n('p','Resolver · '+(operationalRequest(run)?'Corrective information':'Needs your answer')),{className:'speaker'}),n('p',question.question||'Question unavailable'));
  const context=card('','question-context');if(question.why)context.append(n('span',question.why));
  if(question.proposed_default)context.append(n('span','Suggested: '+question.proposed_default));
  if(Array.isArray(question.options)&&question.options.length)context.append(n('span','Options: '+question.options.map(option=>typeof option==='string'?option:option.label||JSON.stringify(option)).join(' · ')));
  element.append(context);
  if(question.proposed_default&&!operationalRequest(run)){const delegate=focusKey(button('Accept suggested answer',()=>sendTaskChat(run,{question_id:id,delegate:true,text:'',request_id:newRequestId(),...resolverResponseFields(run)})),'question:'+run.run+':'+id+':delegate');delegate.disabled=taskActionBusy(run)||taskChatPending.has(run.run);element.append(delegate);}
  if(!operationalRequest(run))renderQuestionAnswer(element,question,run);
  host.append(element);
}
function renderChatIntent(row,entry,identity) {
  if(!entry.kind||entry.kind==='answer')return;
  const detail=card('','chat-intent');
  detail.append(Object.assign(n('p','AutoCode · '+({proposed_change:'Proposed plan change',correction:'Confirmed plan change',question:'Saved status',control:'Control guidance',approval:'Approval guidance',answer:'Answer'}[entry.kind]||entry.kind)),{className:'speaker'}));
  if(entry.reply)detail.append(n('p',entry.reply));
  const confirmation=entry.confirmation;
  if(confirmation?.status==='pending'&&latestRun?.run===identity){
    const current=latestRun;
    const stale=confirmation.goal_token!==(current.goal_token??null);
    if(stale)detail.append(Object.assign(n('p','The plan changed. Send a new message to review the current plan.'),{className:'field-note'}));
    else for(const [decision,label]of [['confirm','Yes, change the plan'],['question','No, keep as a question']]){
      const control=button(label,()=>sendTaskChat(latestRun,{...entry,request_id:entry.client_request_id||entry.id,text:entry.submitted_text??entry.text,decision,decision_token:confirmation.token}),'text-button');
      control.disabled=!!taskReadError||taskChatPending.has(identity)||taskActionBusy(current)||!!operationalRequest(current)||statusInfo(current).label==='Answer needed';
      detail.append(control);
    }
  }
  row.append(detail);
}
function answerHistoryItems(messages){
  const result=[];
  for(const entry of messages){
    const answered=entry.role==='user'&&entry.question_id&&entry.provenance!=='unrecorded'&&['received','applied','resumed','delivered'].includes(entry.status);
    const previous=result.at(-1);
    if(answered&&previous?.answers&&previous.answers.at(-1).display_source===entry.display_source&&!previous.answers.some(row=>row.question_id===entry.question_id))previous.answers.push(entry);
    else result.push(answered?{answers:[entry]}:entry);
  }
  return result;
}
function renderMessageHistory(host,messages,identity){
  const items=answerHistoryItems(messages),split=Math.max(0,items.length-4),older=card('','earlier-content');
  const latestAssistant=messages.findLast(entry=>entry.role!=='user');
  const render=(parent,entry)=>{const row=appendMessage(parent,{...entry,expanded:entry===latestAssistant||entry===messages.at(-1),text:entry.text||(entry.delegate?'Accepted suggested answer':'Saved answer')});if(entry.role==='user'&&entry.kind)renderChatIntent(row,entry,identity);if(entry.provenance==='unrecorded')row.append(Object.assign(n('p','Saved answer · origin not recorded'),{className:'answer-provenance field-note'}));if(entry.question_text)row.prepend(Object.assign(n('p','In reply to: '+entry.question_text),{className:'reply-context'}));if(entry.error)row.append(Object.assign(n('p',entry.error),{className:'error'}));if(entry.role==='user'&&entry.status==='error'&&latestRun?.run===identity)row.append(button('Retry same message',()=>sendTaskChat(latestRun,{...entry,request_id:entry.client_request_id||entry.request_id||entry.id,retry:true})));};
  const renderItem=(parent,item)=>{
    if(!item.answers)return render(parent,item);
    const entries=item.answers,body=card('','answer-history-content'),suggested=entries.filter(entry=>entry.delegate).length;
    for(const entry of entries){render(body,entry);body.append(Object.assign(n('p',entry.delegate?'You accepted the suggested answer':entry.provenance==='unrecorded'?'Saved answer · origin not recorded':'You wrote this answer'),{className:'answer-provenance field-note'}));}
    const label='You answered '+entries.length+' '+(entries.length===1?'question':'questions')+' · '+suggested+' used the suggestion';
    const group=disclosure(label,'answer-history:'+entries[0].id,[body],identity);group.className='answer-history';parent.append(group);
  };
  for(const entry of items.slice(0,split))renderItem(older,entry);
  if(split){const history=disclosure('Earlier conversation · '+split+' messages','earlier-messages',[older],identity);history.className='earlier-messages';host.append(history);}
  for(const entry of items.slice(split))renderItem(host,entry);
}
function taskMessages(run){return Array.isArray(run.transcript?.messages)?run.transcript.messages:[];}
function settleThreadScroll(){
  if(currentView!=='task-detail')return;
  const thread=$('#interview');
  if(pendingThreadAnchorKey){
    // A freshly opened conversation first restores its saved scroll anchor;
    // the end-scroll fallback only applies when no anchor survives.
    if(!threadAnchorMessages(thread).length)return;
    const key=pendingThreadAnchorKey;pendingThreadAnchorKey='';scrollThreadToEnd=false;
    requestAnimationFrame(()=>{if(currentView==='task-detail'&&!restoreThreadAnchor(thread,key))thread.scrollTop=thread.scrollHeight;});
    return;
  }
  if(scrollThreadToEnd){scrollThreadToEnd=false;requestAnimationFrame(()=>{if(currentView==='task-detail')thread.scrollTop=thread.scrollHeight;});}
}
function answerSafetyPanel(count) {
  const panel=card('','answer-safety');
  panel.append(Object.assign(n('p',count>1?'Answer '+count+' questions':'Answer needed'),{className:'monitor-kicker'}),n('p','Planning continues only if the controller returns to Running with no pending request.'),n('p','This does not approve a plan or start implementation.'));
  return panel;
}
/* Session checkpoints: meaningful saved moments in this task's conversation.
   Derived only from saved state; each answers what changed, what was verified,
   which findings existed, and which candidate it was. */
const CHECKPOINT_DISPLAY_LIMIT=30;
function sessionCheckpoints(run) {
  const list=[];
  const goal=run.goal||{};
  if(goal.approval_status==='approved')list.push({id:'plan',kind:'plan',label:'Plan approved',at:goal.approval_event?.at||goal.updated_at||run.updated_at||run.created_at||'',iteration:null,candidate:0});
  const slots=new Map();
  for(const [index,stage] of (run.stages||[]).entries()){
    if(!stageSucceeded(stage)||!stage.finished_at)continue;
    const name=String(stage.stage||'').replace(/_report_repair$/,'');
    const kind=/terra|orchestrator/.test(name)?'built':/^(sol|astra_review|astra_checkpoint)/.test(name)?'review':'';
    if(!kind)continue;
    const iteration=stage.iteration??index;
    const slot=slots.get(iteration)||{iteration,built:null,review:null};
    slot[kind]={index,stage,name,at:stage.finished_at};
    slots.set(iteration,slot);
  }
  let candidate=0;
  for(const slot of slots.values()){
    if(slot.built){candidate+=1;list.push({id:'built-i'+slot.iteration,kind:'built',label:slot.built.name==='orchestrator'?'Builder batch completed':'Build completed',at:slot.built.at,iteration:slot.iteration,candidate,stageIndex:slot.built.index,changedFiles:Array.isArray(slot.built.stage.changed_files)?slot.built.stage.changed_files.filter(file=>typeof file==='string'):[],hasDiff:!!slot.built.stage.diff_ref,sourceRevision:typeof slot.built.stage.source_revision==='string'?slot.built.stage.source_revision:''});}
    if(slot.review)list.push({id:'review-i'+slot.iteration,kind:'review',label:/^sol/.test(slot.review.name)?'Validation review':'Completion review',at:slot.review.at,iteration:slot.iteration,candidate,stageIndex:slot.review.index,changedFiles:Array.isArray(slot.review.stage.changed_files)?slot.review.stage.changed_files.filter(file=>typeof file==='string'):[],hasDiff:!!slot.review.stage.diff_ref,sourceRevision:typeof slot.review.stage.source_revision==='string'?slot.review.stage.source_revision:''});
  }
  if(run.status==='TASK_COMPLETE')list.push({id:'final',kind:'final',label:'Final candidate',at:run.completed_at||list.at(-1)?.at||'',iteration:null,candidate});
  return list.sort((left,right)=>(Date.parse(left.at)||0)-(Date.parse(right.at)||0));
}
function checkpointChangedText(cp) {
  if(cp.kind==='plan')return 'The approved plan revision only. Approval does not change project files.';
  if(cp.kind==='final')return 'The completed result of the final candidate. Open Changes to inspect its saved snapshot.';
  if(!cp.changedFiles.length)return cp.hasDiff?'A saved diff exists for this step; changed file names were not recorded.':'No changed files or diff were recorded for this step.';
  const shown=cp.changedFiles.slice(0,6).join(' · ');
  return cp.changedFiles.length>6?shown+' · +'+(cp.changedFiles.length-6)+' more':shown;
}
function checkpointVerifiedText(run,cp) {
  const validation=run.validation||{},counts=run.counts||{};
  const tally=verdict=>'Saved verification'+(verdict?' · verdict '+verdict:'')+' · '+(counts.pass||0)+' criteria passed · '+(counts.fail||0)+' failed or blocked · '+(counts.unknown||0)+' unverified.';
  if(cp.kind==='plan')return 'Approval records plan revision '+(run.goal?.revision??'?')+' by '+(run.goal?.approval_event?.actor||'you')+'. It is not implementation verification.';
  if(cp.kind==='final')return (run.monitor?.validation_verdict||validation.verdict)?tally(run.monitor?.validation_verdict||validation.verdict):'This task is recorded complete, but no independent verification report is saved for the final result.';
  if(cp.sourceRevision&&validation.source_revision===cp.sourceRevision)return tally(validation.verdict);
  return 'No independent verification report is saved for this candidate. Recorded implementation and tool activity are not acceptance.';
}
function checkpointFindingsText(run,cp,isLatest) {
  const summary=run.monitor?.findings_summary,open=run.monitor?.findings||[];
  if(!summary&&!open.length)return 'No findings are recorded in the saved review ledger.';
  const ledger=summary?summary.open+' open · '+summary.resolved+' resolved'+(summary.repeated?' · '+summary.repeated+' reported again after a fix':'')+'.':'';
  if(!isLatest)return 'Earlier per-candidate finding history is not saved. Latest ledger: '+ledger;
  if(!open.length)return 'Latest ledger: '+ledger+' No findings remain open.';
  const items=open.slice(0,5).map(finding=>(finding.id?finding.id+' · ':'')+concise(finding.finding||'Finding details unavailable',80));
  return 'Latest ledger: '+ledger+' Open: '+items.join(' | ')+(open.length>5?' · +'+(open.length-5)+' more':'');
}
function checkpointCandidateText(cp) {
  if(cp.kind==='plan')return 'Plan revision checkpoint · recorded before build candidates.';
  if(cp.kind==='final')return 'Final candidate'+(cp.candidate?' · candidate '+cp.candidate:'')+'.';
  return 'Candidate '+cp.candidate+' · iteration '+(cp.iteration??'?')+(cp.sourceRevision?' · source '+cp.sourceRevision.slice(0,12):'')+(cp.kind==='review'?' · review of candidate '+cp.candidate:'')+'.';
}
function checkpointAnswers(run,cp,isLatest) {
  const list=n('dl','checkpoint-answers');
  for(const [question,answer] of [['What changed?',checkpointChangedText(cp)],['What was verified?',checkpointVerifiedText(run,cp)],['What findings existed?',checkpointFindingsText(run,cp,isLatest)],['Which candidate was this?',checkpointCandidateText(cp)]])list.append(n('dt',question),n('dd',answer));
  return list;
}
function revealChangeStage(run,stageIndex) {
  activateTab('changes');
  const key='details:'+run.run+'\0diff-stage:'+stageIndex;
  let attempts=0;
  const reveal=()=>{
    const summary=typeof document!=='undefined'?document.querySelector('[data-focus-key="'+key+'"]'):null;
    if(summary?.parentElement){summary.parentElement.open=true;summary.parentElement.scrollIntoView({block:'start'});return;}
    if(++attempts<40&&typeof setTimeout==='function')setTimeout(reveal,100);
  };
  reveal();
}
function renderSessionCheckpoints(run,checkpoints,allowRestore=true) {
  if(!checkpoints.length)return null;
  const host=card('','session-checkpoints');
  host.setAttribute('aria-label','Session checkpoints');
  host.append(n('h2','Session checkpoints'),Object.assign(n('p','Meaningful stages saved during this session. Open a checkpoint to see what changed, what was verified, which findings existed, and which candidate it was.'),{className:'field-note'}));
  const visible=checkpoints.length>CHECKPOINT_DISPLAY_LIMIT?checkpoints.slice(-CHECKPOINT_DISPLAY_LIMIT):checkpoints;
  if(visible.length<checkpoints.length)host.append(Object.assign(n('p','Showing the latest '+visible.length+' of '+checkpoints.length+' saved checkpoints.'),{className:'field-note'}));
  const list=Object.assign(n('ol'),{className:'checkpoint-list'}),latest=checkpoints[checkpoints.length-1];
  for(const cp of visible){
    const detail=card('','checkpoint-detail');
    const at=Date.parse(cp.at),stamp=at?new Date(at).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'time unavailable';
    const toggle=focusKey(button('',()=>{detail.hidden=!detail.hidden;toggle.setAttribute('aria-expanded',String(!detail.hidden));},'checkpoint-toggle'),'checkpoint:'+run.run+':'+cp.id);
    toggle.dataset.staleSafe='true';toggle.setAttribute('aria-expanded','false');
    const mark=n('span','✓');mark.setAttribute('aria-hidden','true');mark.className='checkpoint-mark';
    toggle.append(mark,Object.assign(n('span',cp.label),{className:'checkpoint-label'}),Object.assign(n('span',stamp),{className:'checkpoint-time'}));
    detail.hidden=true;
    detail.append(checkpointAnswers(run,cp,cp===latest));
    if(cp.stageIndex!=null&&cp.hasDiff){const open=button('Open saved changes →',()=>revealChangeStage(run,cp.stageIndex),'text-button');open.dataset.staleSafe='true';detail.append(open);}
    const item=Object.assign(n('li'),{className:'checkpoint-item checkpoint-'+cp.kind});
    item.append(toggle,detail);list.append(item);
  }
  host.append(list);
  return host;
}
const LIFECYCLE_CARD_SCHEMA_VERSION=1;
const LIFECYCLE_CARD_TYPES=['plan_draft','reviewer_feedback','approval','build_start','diff','checks','blocker','recovery','evidence','version_history','contract_status','decision','launch','reply'];
function normalizeLifecycleEvent(raw){
  if(!raw||typeof raw!=='object')return null;
  const type=String(raw.type||'');
  if(!LIFECYCLE_CARD_TYPES.includes(type))return null;
  const source=raw.attribution&&typeof raw.attribution==='object'?raw.attribution:{};
  const event={
    schema_version:LIFECYCLE_CARD_SCHEMA_VERSION,type,
    id:String(raw.id||''),
    title:String(raw.title||human(type)),
    summary:raw.summary==null?'':String(raw.summary),
    at:raw.at!=null&&Date.parse(raw.at)?raw.at:null,
    role:String(source.role??raw.role??''),
    model:String(source.model??raw.model??''),
    reasoning:String(source.reasoning??raw.reasoning??''),
    status:String(source.status??raw.status??'recorded'),
    severity:['info','attention','failed','complete'].includes(raw.severity)?raw.severity:'info',
    revision:raw.revision==null?null:raw.revision,
    body:raw.body&&typeof raw.body==='object'?raw.body:null,
    actions:Array.isArray(raw.actions)?raw.actions.filter(entry=>entry&&typeof entry.label==='string').map(entry=>({label:String(entry.label),kind:String(entry.kind||'tab'),target:String(entry.target||'')})):[],
    links:Array.isArray(raw.links)?raw.links.filter(entry=>entry&&typeof entry.path==='string').map(entry=>({label:String(entry.label||entry.path),path:String(entry.path)})):[]
  };
  if(!event.id)event.id=type+':'+(event.revision??'')+':'+(event.at||'')+':'+event.title;
  return event;
}
function lifecycleAttributionLine(event){
  const parts=[];
  if(event.role)parts.push(n('span',event.role));
  if(event.model)parts.push(n('span',event.model));
  if(event.reasoning)parts.push(n('span','reasoning '+event.reasoning));
  if(event.status)parts.push(Object.assign(n('span',human(event.status)),{className:'lifecycle-status'}));
  return parts;
}
function lifecycleCard(event,context={}){
  const node=card('','lifecycle-card lifecycle-'+event.type+(event.severity!=='info'?' severity-'+event.severity:''));
  node.dataset.lifecycleType=event.type;
  node.dataset.lifecycleSchema=String(event.schema_version);
  node.setAttribute('aria-label',event.title);
  const header=card('','lifecycle-header');
  header.append(Object.assign(n('h4',event.title),{className:'lifecycle-title'}));
  const attribution=card('','lifecycle-attribution');
  const parts=lifecycleAttributionLine(event);
  for(const part of parts)attribution.append(part);

  if(event.revision!=null)attribution.append(n('span','revision '+event.revision));
  if(event.at){const at=n('time',new Date(event.at).toLocaleString());at.dateTime=new Date(event.at).toISOString();attribution.append(at);}
  header.append(attribution);
  node.append(header);
  if(event.summary)node.append(n('p',event.summary));
  if(event.body){
    const bodyItems=Array.isArray(event.body.items)?event.body.items:[];
    const details=disclosure(event.body.label||'Details','lifecycle-body:'+event.id,[renderDocument(bodyItems.length?bodyItems:event.body)],context.run||'');
    node.append(details);
  }
  if(event.links.length){
    const linkRow=card('','lifecycle-links');
    for(const link of event.links)linkRow.append(button(link.label+' →',()=>activateTab(link.path),'text-button'));
    node.append(linkRow);
  }
  if(event.actions.length){
    const row=card('','lifecycle-actions');
    for(const action of event.actions){
      if(action.kind==='tab')row.append(button(action.label,()=>activateTab(action.target||'now'),'text-button'));
      else if(action.kind==='callback'&&typeof context[action.target]==='function')row.append(button(action.label,context[action.target],'text-button'));
    }
    if(row.childElementCount)node.append(row);
  }
  return node;
}

const approveBuildState=new Map(),approvalReceiptWaiters=new Map();
function approvalReceiptState(run,token,receipt){
  if(run.state_error||(run.goal_token&&run.goal_token!==token)||run.conversation?.plan_gate?.pending_product_change)return {state:'failed',reason:'The plan changed or could not be verified. Review the current revision before building.'};
  const action=(run.actions||[]).find(row=>row.id===receipt.id)||receipt;
  if(['failed','launch_failed','uncertain'].includes(action.status)||action.status==='finished'&&action.exit_status!=null&&action.exit_status!==0)return {state:'failed',reason:'Approval did not finish successfully. No build was started.'};
  if(action.status==='finished'&&approvedGoalToken(run)===token)return {state:'approved'};
  if(action.status==='finished')return {state:'failed',reason:'The approval command finished without an approval for this revision. No build was started.'};
  return {state:'pending'};
}
function approvedGoalToken(run){
  const goal=run?.goal,revision=goal?.revision,hash=goal?.hash,approval=goal?.approval_event;
  if(goal?.approval_status!=='approved'||!Number.isInteger(revision)||revision<1||typeof hash!=='string'||!hash||!approval||typeof approval!=='object')return '';
  const token=`r${revision}:${hash}`;
  // The saved approval event must itself carry exactly the sealed token (the
  // runner records it on every approval and requires it at admission); a
  // tokenless event is no approval to start a build from.
  return approval.token===token?token:'';
}
function settleApprovalReceipt(run){const waiter=approvalReceiptWaiters.get(run.run);if(waiter)waiter.check(run);}
function cancelApprovalReceipts(message='Build start cancelled when you left this task. Any recorded approval remains saved.'){
  for(const waiter of approvalReceiptWaiters.values())waiter.reject(Error(message));approvalReceiptWaiters.clear();
}
async function waitForApprovalReceipt(run,token,receipt){
  if(!receipt.id&&receipt.status!=='finished')throw Error('The approval receipt has no action identity. Check status before starting separately.');
  const fresh=await api('/api/run?workspace='+encodeURIComponent(run.workspace)+'&run='+encodeURIComponent(run.run));
  const result=approvalReceiptState(fresh,token,receipt);
  if(result.state==='failed')throw Error(result.reason);if(result.state==='approved')return fresh;
  return new Promise((resolve,reject)=>{approvalReceiptWaiters.set(run.run,{reject,check(current){const outcome=approvalReceiptState(current,token,receipt);if(outcome.state==='pending')return;approvalReceiptWaiters.delete(run.run);if(outcome.state==='approved')resolve(current);else reject(Error(outcome.reason));}});refresh();});
}
function currentConversationDraft(doc){
  const drafts=Array.isArray(doc?.plan_drafts)?doc.plan_drafts:[];
  return drafts.findLast(row=>row.status==='current')||drafts.findLast(row=>row.status==='superseded')||drafts.at(-1)||null;
}
function runPlanDraft(run){
  const current=currentConversationDraft(run.conversation);
  if(current&&(run.conversation?.plan_gate?.pending_product_change||run.goal?.approval_status!=='approved'&&!run.conversation?.plan_gate?.architect_reviewed))return current;
  const body=run.goal?.body;if(!body)return null;
  return {revision:run.goal.revision,goal:body.intended_outcome,requirements:body.requirements||body.required_behaviors||body.acceptance_criteria||[],milestones:body.milestones||body.implementation_sequence||run.astra_plan?.current_plan||[],parallelism:body.parallelism||[],outstanding_questions:run.questions||body.open_blocking_questions||[],freshness:{state:'recorded',updated_at:run.goal.updated_at},status:run.goal.approval_status};
}
function planDraftPreviewCard(draft,options={}){
  const host=card('','quiet-plan');host.setAttribute('aria-label',options.frozen?'Approved contract':'Planner draft preview');
  const header=card('','quiet-plan-header');header.append(n('h2',options.frozen?'Approved contract · r'+draft.revision:options.ready?'Plan revision '+draft.revision+' · ready':'Draft plan (quiet)'));
  const freshness=draft.freshness||{},labels={fresh:'Updated with your latest message',refreshing:'Updating with your latest message',pending:'Planner is updating the draft',failed:'Planner needs recovery · previous draft retained',stale:'Earlier draft · update pending',recorded:'Saved plan revision'};
  if(draft.attribution?.model)header.append(Object.assign(n('p',['Planner',draft.attribution.model,draft.attribution.reasoning_effort].filter(Boolean).join(' · ')),{className:'quiet-plan-attribution message-model'}));
  host.append(header,n('p',draft.goal||'The Planner is shaping your idea.'),Object.assign(n('p','Requirements '+(draft.requirements||[]).length+' · Milestones '+(draft.milestones||[]).length+' · Lanes '+(draft.parallelism||[]).length+' · Questions '+(draft.outstanding_questions||[]).length),{className:'quiet-plan-counts'}));
  for(const [title,rows] of [['Requirements',draft.requirements],['Milestones',draft.milestones],['Parallel work',draft.parallelism],['Open questions',draft.outstanding_questions]]){
    if(!Array.isArray(rows)||!rows.length)continue;
    const section=card('','quiet-plan-section');section.append(n('h3',title));const list=n('ul');
    for(const row of rows)list.append(n('li',typeof row==='string'?row:row.question||planEntryText(row)));section.append(list);host.append(section);
  }
  const stamp=freshness.updated_at?new Date(freshness.updated_at).toLocaleString():'';
  const causes=draftCauseLine(draft),reason=options.updateReason||freshness.reason,state=options.updateState||freshness.state;
  const status=reason==='batching_answers'?'Answers saved · draft update batched':reason===DRAFT_COALESCED?'Answers saved · one draft update follows the one in progress':causes&&state==='fresh'?'':labels[state]||'Saved draft';
  if(causes){if(!status&&stamp)causes.append(n('span',' · '+stamp));host.append(causes);}
  if(status)host.append(Object.assign(n('p',status+(stamp?' · '+stamp:'')),{className:'quiet-plan-freshness'}));
  const prior=(options.allDrafts||[]).filter(row=>row.revision!==draft.revision);
  if(prior.length)host.append(disclosure('Earlier drafts ('+prior.length+')','draft-history:'+options.key,[renderDocument(prior)],options.key||''));
  host.append(Object.assign(n('p',options.frozen?'Frozen scope. Requirement changes need a newly reviewed and approved revision.':options.ready?'Plan reviewed. Approve this revision before building.':'Draft only. Independent plan review and your approval are required before implementation.'),{className:'quiet-plan-safety'}));return host;
}
const DRAFT_COALESCED='coalesced_behind_draft_in_flight';
function draftCauseQuote(text){
  const value=String(text||'').replace(/\s+/g,' ').trim();
  return value.length>80?value.slice(0,79).replace(/\s+\S*$/,'')+'…':value;
}
// The human turns a Planner-produced draft answers, from its saved
// freshness.source_messages (every turn since the previous accepted draft, so a
// coalesced update lists all of them). Only a validated Planner result has
// them: a pending, failed or superseded placeholder the Planner never produced
// claims no update.
function draftCauseLine(draft){
  const freshness=draft?.freshness||{},sources=Array.isArray(freshness.source_messages)?freshness.source_messages:[];
  if(!sources.length||freshness.structured_result!==true||!['fresh','stale'].includes(freshness.state))return null;
  const line=Object.assign(n('p',sources.length>1?'Updated after your answers: ':'Updated after your answer: '),{className:'quiet-plan-freshness quiet-plan-causes'});
  sources.forEach((item,index)=>{const quote=n('span',(index?' · ':'')+'“'+(draftCauseQuote(item.excerpt)||'Saved message')+'”');quote.dataset.sourceMessage=item.message_id||'';line.append(quote);});
  return line;
}
function renderQuietPlanPreview(doc){
  const host=$('#quiet-plan');if(!host)return;const draft=currentConversationDraft(doc);host.hidden=!draft;
  $('#draft-conversation').classList.toggle('has-plan',!!draft);
  if(!draft){host.replaceChildren();return;}
  const latest=doc.plan_drafts?.at(-1),options={allDrafts:doc.plan_drafts,key:doc.id,updateState:latest?.freshness?.state,updateReason:latest?.freshness?.reason};
  const full=planDraftPreviewCard(draft,options);full.classList.add('full-draft-preview');
  const compact=card('','compact-draft-preview');
  compact.append(n('h2','Draft plan · r'+draft.revision),Object.assign(n('p',(draft.requirements||[]).length+' requirements · '+(draft.milestones||[]).length+' milestones · '+(draft.outstanding_questions||[]).length+' open questions'),{className:'quiet-plan-counts'}));
  const question=draft.outstanding_questions?.[0];
  if(question)compact.append(Object.assign(n('p','Open question: '+(typeof question==='string'?question:question.question||planEntryText(question))),{className:'compact-draft-question'}));
  compact.append(button('View full draft plan',()=>{
    const dialog=$('#draft-plan-dialog');dialog._returnFocus=document.activeElement;
    $('#draft-plan-title').textContent='Draft plan · revision '+draft.revision;
    $('#draft-plan-content').replaceChildren(planDraftPreviewCard(draft,options));
    dialog.showModal();$('#draft-plan-close').focus();
  },'text-button'));
  host.replaceChildren(full,compact);
}
function renderPlanRail(run){
  const host=$('#goal'),draft=runPlanDraft(run);host.replaceChildren();
  if(draft)host.append(planDraftPreviewCard(draft,{frozen:run.goal?.approval_status==='approved'&&!run.conversation?.plan_gate?.pending_product_change,ready:statusInfo(run).label==='Approve plan'&&!run.conversation?.plan_gate?.pending_product_change,allDrafts:run.conversation?.plan_drafts,key:run.conversation?.id||run.run}));
  else host.append(n('h2','Your plan'),n('p','The Planner will build a draft here as the requirements become clear.'));
  if(run.astra_plan?.current_assignment)host.append(Object.assign(n('p','Current milestone'),{className:'rail-kicker'}),n('p',run.astra_plan.current_assignment.objective));
  host.append(button('Full plan & history',()=>activateTab('plan'),'text-button'));
}
function approveBuildStatusLine(run){return approveBuildState.get(run.run)?.message||'';}
async function approveAndBuild(run){
  const token=run.goal_token;
  if(!token||run.conversation?.plan_gate?.pending_product_change||approveBuildState.has(run.run)||taskActionBusy(run)||taskReadError||statusInfo(run).label!=='Approve plan')return;
  const update=message=>{approveBuildState.set(run.run,{token,message});renderBrief(latestRun||run);renderInlineTaskAction(latestRun||run);};
  update('Recording approval for revision '+run.goal.revision+'…');
  try{
    const receipt=await submitTaskAction(run,'approve_goal',{token,confirmation:token});
    if(!receipt)throw Error('Approval was not confirmed. Refresh before trying again.');
    const fresh=await waitForApprovalReceipt(run,token,receipt);
    update('Approval recorded. Starting the same revision as a separate event…');
    const started=await submitTaskAction(fresh,'continue',{token,confirmation:token,expected_goal_token:token});
    if(!started||['failed','uncertain','launch_failed'].includes(started.status))throw Error('Start could not be confirmed. Check the current task state before retrying.');
    dashboardNotice('Approval recorded and build requested for revision '+fresh.goal.revision+'.');
  }catch(error){dashboardNotice(error.message);}
  finally{approveBuildState.delete(run.run);await refresh();}
}
function focusChatAction(key){
  // Panes stay read-only: this focus path surfaces the matching chat control
  // without submitting anything from the pane itself.
  activateTab('interview');
  const control=[...document.querySelectorAll('[data-focus-key]')].find(element=>element.dataset.focusKey===key);
  if(control){control.scrollIntoView({block:'center',behavior:'smooth'});control.focus();}
}
function renderInlineTaskAction(run){
  // #inline-task-action lives inside the #interview transcript scroller in
  // dashboard.html: human approvals are actionable only in the continuous
  // chat transcript (saved feedback intervention
  // intervention-figma-chat-plan-alignment-20260930-2113), never beside the
  // composer or inside an artifact pane.
  const host=$('#inline-task-action');if(!host)return;host.replaceChildren();
  if(run.conversation?.plan_gate?.pending_product_change){host.append(n('strong','Requirement change saved'),n('p','The '+roleDisplayName('planner')+' and '+roleDisplayName('plan_reviewer')+' must review a new revision before another build can start.'));host.hidden=false;return;}
  const ready=statusInfo(run).label==='Approve plan',approved=run.goal?.approval_status==='approved',busy=taskActionBusy(run)||approveBuildState.has(run.run)||taskReadError;
  if(ready&&run.goal_token){
    host.append(n('strong','Plan ready · revision '+run.goal.revision),n('p','Independent plan review accepted this revision. Review the plan, then approve and build.'));
    const actions=card('','lifecycle-actions');
    const request=button('Request changes',()=>{$('#change-text').focus();$('#change-text').scrollIntoView({block:'center',behavior:'smooth'});});
    const approve=focusKey(button('Approve plan revision '+run.goal.revision,()=>submitTaskAction(run,'approve_goal',{token:run.goal_token,confirmation:run.goal_token}),'primary'),'approve:'+run.run+':'+run.goal_token);
    approve.disabled=busy||(run.questions||[]).length>0;
    const combined=button('Approve & build revision '+run.goal.revision,()=>approveAndBuild(run),'primary');combined.disabled=approve.disabled;
    actions.append(button('View plan',()=>activateTab('plan')),request,approve,combined);host.append(actions);
  }else if(approved&&primaryAction(run,busy).kind==='continue'){
    host.append(n('strong','Approved contract · revision '+run.goal.revision));const start=button(primaryAction(run,busy).label,()=>submitTaskAction(run,'continue'),'primary');start.disabled=busy;host.append(start);
  }
  if(statusInfo(run).label==='Review output'&&run.review_token&&run.review_criteria?.length&&run.human_request_authorized===true){
    const eligible=(run.review_criteria||[]).filter(criterion=>run.human_escalation?.request?.criteria?.includes(criterion.id));
    if(eligible.length){
      host.append(n('strong','Review requested · '+eligible.length+' '+(eligible.length===1?'item':'items')),n('p','Approve each requested item here in the conversation. Your approval applies to this saved result only.'));
      const actions=card('','lifecycle-actions');
      for(const criterion of eligible){
        const reviewed=run.human_reviews?.[criterion.id]?.token===run.review_token;
        const approve=focusKey(button(reviewed?'Approved · '+criterion.id:'Approve '+criterion.id,()=>submitTaskAction(run,'approve_review',{id:criterion.id,token:run.review_token})),'review:'+run.run+':'+criterion.id);
        approve.disabled=reviewed||taskActionBusy(run);actions.append(approve);
      }
      host.append(actions);
    }
  }
  const message=approveBuildStatusLine(run);if(message)host.append(Object.assign(n('p',message),{role:'status'}));
  host.hidden=!host.childElementCount;
}
function inlineSavedChanges(run){
  const body=card('','inline-diff'),details=disclosure('Inspect saved changes','inline-changes',[body],run.run),base='/api/evidence?workspace='+encodeURIComponent(run.workspace)+'&run='+encodeURIComponent(run.run);let loading=false,loaded=false;
  const previous=details.ontoggle;
  const read=async()=>{if(!details.open||loading||loaded)return;loading=true;body.replaceChildren(n('p','Reading saved changes…'));
    try{const listing=await api(base);if(!details.isConnected)return;body.replaceChildren();
      for(const stage of [...(listing.stages||[])].reverse()){
        const content=card('','inline-diff-content'),entry=disclosure(human(stage.stage||'Build step')+' · '+(stage.finished_at?new Date(stage.finished_at).toLocaleString():'Saved snapshot'),'inline-diff:'+stage.index,[content],run.run);let pending=false,done=false;
        const remember=entry.ontoggle;entry.ontoggle=async()=>{remember?.();if(!entry.open||pending||done)return;pending=true;content.replaceChildren(n('p','Reading this snapshot…'));
          try{const result=await api(base+'&stage='+stage.index);if(!entry.isConnected)return;content.replaceChildren(n('p',(stage.changed_files||[]).join(' · ')||'Saved tracked changes'),n('pre',result.text||'No tracked diff recorded.'));if(result.truncated)content.append(n('p','Showing the first 192 KB. The full diff remains saved with the task.'));done=true;}
          catch(error){content.replaceChildren(n('p',error.message),n('p','Close and reopen to retry.'));}finally{pending=false;}};body.append(entry);
      }
      if(!listing.stages?.length)body.append(n('p','No saved code changes yet.'));loaded=true;
    }catch(error){body.replaceChildren(n('p',error.message),button('Retry reading changes',read));}finally{loading=false;}
  };details.ontoggle=()=>{previous?.();read();};return details;
}
function renderWorkflowTimeline(host,run){
  const records=[...(run.stages||[]),...(run.active_stage?[run.active_stage]:[])],seen=new Set(),events=[];
  for(const record of records)for(const raw of record.lifecycle||[]){const event=normalizeLifecycleEvent(raw);if(event&&!seen.has(event.id)){seen.add(event.id);events.push(event);}}
  if(events.length){const region=card('','lifecycle-region');region.setAttribute('aria-label','Task lifecycle');for(const event of events.slice(-20))region.append(lifecycleCard(event,{run:run.run}));host.append(region);}
  const draft=runPlanDraft(run);
  if(draft&&!run.goal?.approval_event){const ready=statusInfo(run).label==='Approve plan'&&!run.conversation?.plan_gate?.pending_product_change,box=card('','lifecycle-card');box.append(n('h3',ready?'Plan revision '+draft.revision+' · ready':'Live draft plan · v'+draft.revision),n('p',(draft.requirements||[]).length+' requirements · '+(draft.milestones||[]).length+' milestones · '+(draft.outstanding_questions||[]).length+' open questions'),button(ready?'View reviewed plan':'Read draft in plan rail',()=>{if(matchMedia('(max-width:1199px)').matches)activateTab('plan');else $('#goal').focus();},'text-button'));host.append(box);}
  if(records.some(row=>row.finished_at))host.append(inlineSavedChanges(run));
  if(statusInfo(run).group==='complete'&&!events.some(event=>event.role===roleDisplayName('completion'))){const complete=card('','lifecycle-card severity-complete');complete.append(n('h3','Work completed'),n('p',(run.counts?.pass||0)+' acceptance checks reported passing. Inspect the saved changes and verification below.'));host.append(complete);}
  const validation=run.validation||{};
  if(validation.checks?.length||validation.criterion_results?.length){
    const box=card('','lifecycle-card');box.append(n('h3','Verification & evidence'),n('p',run.monitor?.validation_verdict?'Recorded verdict: '+human(run.monitor.validation_verdict):'Verification results recorded.'),disclosure('Read checks and evidence','inline-checks',[renderDocument(validation)],run.run));host.append(box);
  }
  const findings=run.monitor?.findings||[];
  if(findings.length){const box=card('','lifecycle-card severity-attention');box.append(n('h3','Reviewer findings'),n('p',findings.length+' findings recorded.'),disclosure('Read reviewer feedback','inline-findings',[renderDocument(findings)],run.run));host.append(box);}
  if(run.goal?.approval_event){const box=card('','lifecycle-card contract-receipt');box.append(n('h3','Contract frozen · revision '+run.goal.revision),n('p','Approval is recorded for this revision. New requirements must be reviewed and approved before the scope changes.'));host.append(box);}
}

function renderConversation(run) {
  const checkpoints=sessionCheckpoints(run);
  const root=$('#conversation'),signature=JSON.stringify([run.run,run.transcript,run.progress_messages,run.draft_messages,run.planning_messages,run.discovery_summary,run.answers,run.questions,run.chat_messages,run.user_request,run.human_request_authorized,run.human_escalation,run.status,run.goal?.approval_status,run.goal?.approval_event,run.completed_at,run.monitor?.live,run.monitor?.objective,run.monitor?.findings_summary,run.monitor?.findings,run.monitor?.validation_verdict,run.validation?.source_revision,run.counts,taskChatPending.has(run.run),checkpoints,run.conversation,run.goal_token,run.validation,run.verification,run.completion_current,run.screenshots,run.stages,run.active_stage,run.interventions?.code_checkpoints]);
  $('#conversation-heading').textContent='Conversation';
  $('#conversation-avatar').textContent=planningSpeaker(run).slice(0,1);
  $('#conversation-description').textContent=jointPlanning(run)?'The '+roleDisplayName('requirements')+' role captures the scope. The '+roleDisplayName('planner')+' drafts and revises. The independent '+roleDisplayName('plan_reviewer')+' challenges and finalizes.':'Shape the work, then let your team build.';
  if(root.dataset.rendered===signature)return;const scroll=$('#interview');scrollThreadToEnd=scrollThreadToEnd||scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<90;root.dataset.rendered=signature;root.replaceChildren();
  root.dataset.conversationId=run.conversation?.id||run.conversation_id||'';
  if(run.transcript?.warning)root.append(Object.assign(n('p',run.transcript.warning),{className:'field-note'}));
  renderMessageHistory(root,taskMessages(run),run.run);
  if(statusInfo(run).label==='Recovering'){
    const recovery=workRecoveryPanel(run);recovery.classList.add('chat-recovery');
    recovery.setAttribute('aria-label','Technical recovery in progress');root.append(recovery);
  }
  if(statusInfo(run).group==='complete'){
    const ready=card('','chat-ready');ready.append(n('h3','Ready to inspect'),
      n('p','The runner recorded this task as complete. Inspect the preview, saved changes and verification evidence alongside this conversation.'));
    const actions=card('','lifecycle-actions');
    actions.append(button('Open preview',()=>activateTab('preview'),'primary'),button('View changes',()=>activateTab('changes')));
    ready.append(actions);root.append(ready);
  }
  renderScreenshotEvidence(root,run);
  if(typeof renderCodeCheckpoints==='function')renderCodeCheckpoints(root,run);
  if(statusInfo(run).label==='Answer needed'){
    const questions=run.questions||[];
    for(const question of questions)renderQuestion(root,question,run);
    root.append(answerSafetyPanel(questions.length));
  }
  if(operationalRequest(run)){
    for(const question of run.questions||[])renderQuestion(root,question,run);
    const note=card('','answer-safety');note.append(n('p','Provide corrective information or leave this task paused. Neither action approves a plan, changes permissions, resets budgets, or continues execution.'));
    const paused=button('Leave paused',()=>submitTaskAction(run,'resolver_response',{resolver_response:'leave_paused'}));paused.disabled=taskActionBusy(run)||taskChatPending.has(run.run);note.append(paused);root.append(note);
  }
  if(run.human_request_authorized===true&&statusInfo(run).group==='attention'&&run.user_request&&!(run.questions||[]).length)appendMessage(root,{speaker:'Resolver',text:waitingMessage(run.user_request)});
  if(!root.childElementCount)appendMessage(root,{text:run.goal?.approval_status==='approved'?'The plan is approved. Follow the work here and send direction whenever you need to.':run.goal?.body?'Here’s the proposed plan. Review it and approve it when it reflects what you want to build.':planningSpeaker(run)+'’s questions and plan will appear here.'});
}
async function copyText(text,element){try{await navigator.clipboard.writeText(text);element.textContent='Copied';}catch{dashboardNotice('Select the displayed token to copy it. Clipboard access is unavailable.');}}
function planEntryText(entry) {
  if(typeof entry==='string')return entry;
  if(!entry||typeof entry!=='object')return String(entry||'Not recorded');
  return entry.criterion||entry.requirement||entry.text||entry.description||entry.objective||entry.title||JSON.stringify(entry);
}
function planDocumentSection(title,entries,ordered=false) {
  const section=card('','plan-document-section'),items=Array.isArray(entries)?entries:entries?[entries]:[];
  section.append(n('h3',title));
  if(!items.length){section.append(n('p','Not recorded in this revision.'));return section;}
  const list=n(ordered?'ol':'ul','');
  for(const entry of items)list.append(n('li',planEntryText(entry)));
  section.append(list);return section;
}
function renderBrief(run) {
  const host=$('#brief-current');host.replaceChildren();if(!run.goal?.body){
    const empty=card('','brief-card');
    empty.append(n('h3','No saved plan yet'),n('p','No plan has been saved for “'+taskTitle(run)+'” yet. Reviewed plans and their revisions appear here.'));
    host.append(empty);return;
  }
  if(String(run.goal.origin||'').startsWith('migration_draft')&&!run.goal.body.acceptance_criteria?.length){
    const placeholder=card('','brief-card');placeholder.append(n('h3','Plan not ready yet'),n('p','Your discussion is saved in Conversation. A validated planning draft has not been produced yet.'));
    if(statusInfo(run).label==='Planning needs retry')placeholder.append(n('p','Retry planning to generate a fresh draft. You will review the final plan before implementation.'));
    host.append(placeholder);return;
  }
  const approved=run.goal.approval_status==='approved',box=card('','brief-card plan-document'),heading=card('','brief-header'),brief=run.goal.body;
  const ready=statusInfo(run).label==='Approve plan',revision=run.goal.revision??'?';
  const reviewer=run.goal.approval_event?.actor||run.goal.reviewer||roleDisplayName('plan_reviewer'),origin=run.goal.origin||brief.origin||'Saved task plan';
  const stamped=run.goal.approval_event?.at||run.goal.updated_at||run.updated_at||run.created_at;
  heading.append(Object.assign(n('p','Plan revision '+revision+' · '+(approved?'Reviewed & approved':ready?'Ready for your review':'Draft')),{className:'plan-revision-label'}),n('h2',brief.intended_outcome||taskTitle(run)));
  const provenance=Object.assign(n('p','Origin: '+origin+' · Reviewer: '+reviewer+' · State: '+(approved?'Approved':ready?'Ready':'Draft')+' · '+(stamped?new Date(stamped).toLocaleString():'Timestamp unavailable')),{className:'plan-provenance'});
  const body=card('','brief-body plan-document-body');
  body.append(planDocumentSection('Intended outcome',[brief.intended_outcome||'No intended outcome recorded.']));
  body.append(planDocumentSection('Requirements',brief.requirements||brief.required_behaviors||brief.acceptance_criteria));
  body.append(planDocumentSection('Constraints',brief.constraints));
  body.append(planDocumentSection('Implementation sequence',brief.implementation_sequence||brief.technical_approach||brief.milestones||brief.plan,true));
  body.append(planDocumentSection('Verification criteria',brief.acceptance_criteria));
  body.append(planDocumentSection('Assumptions',brief.accepted_assumptions||brief.assumptions));
  const earlier=Array.isArray(brief.history)?brief.history:brief.previous_revision?[brief.previous_revision]:['Earlier plan revisions remain available in saved History.'];
  body.append(disclosure('Earlier-revision disclosure','earlier-plan-revisions:'+run.goal_token,[planDocumentSection('Earlier revisions',earlier)],run.run));
  body.append(disclosure('Read the complete plan','complete-brief:'+run.goal_token,[renderDocument(brief)],run.run));
  body.append(disclosure('Technical details','raw-brief:'+run.goal_token,[n('pre',JSON.stringify({revision:run.goal.revision,token:run.goal_token,source:run.goal},null,2))],run.run));box.append(heading);
  if(!approved&&!ready)box.append(Object.assign(n('p','This is a draft. The next action is shown in Now; approval becomes available when the current planning step is ready.'),{className:'field-note plan-note'}));
  if(run.goal_token&&(approved||ready)&&!run.conversation?.plan_gate?.pending_product_change){
    // Human approvals happen only in the chat transcript (saved feedback
    // intervention-figma-chat-plan-alignment-20260930-2113). The Plan pane
    // keeps read-only saved status plus a focus path to the chat action.
    const area=card('','brief-approval');
    area.append(n('p','Approval records this revision. Starting work is a separate action.'));
    if(approved)area.append(n('p','✓ Revision '+revision+' is confirmed. The approval receipt remains part of this saved plan. Approval and every further action happen in the conversation transcript.'));
    else{
      area.append(n('p','Revision '+revision+' is ready for your review. The revision-bound approval controls are in the conversation transcript.'));
      area.append(focusKey(button('Review in conversation →',()=>focusChatAction('approve:'+run.run+':'+run.goal_token)),'brief-focus-approve:'+run.run+':'+run.goal_token));
    }
    body.append(area);
  }
  const checklist=workChecklistPanel(run);checklist.classList.add('plan-checklist');
  const milestones=run.work_summary?.tasks?.length?run.work_summary.tasks:
    (brief.milestones||[]).map(task=>({label:planEntryText(task),state:'unknown'}));
  const overview=n('ol','');overview.className='plan-milestones';overview.setAttribute('aria-label','Saved plan milestones');
  for(const task of milestones){
    const item=n('li','');item.dataset.taskState=task.state;
    const mark=n('span',({done:'✓',working:'◐',waiting:'·',unknown:'·'}[task.state]||'·'));
    mark.setAttribute('aria-label',({done:'Recorded acceptance',working:'Current step',waiting:'Waiting',unknown:'Not verified'}[task.state]||'Not verified'));
    item.append(mark,n('span',task.label));overview.append(item);
  }
  const current=card('','plan-current-task'),facts=stateFacts(run);current.append(n('h3','Current task'),n('p',run.work_summary?.tasks?.find(task=>task.state==='working')?.label||run.current_task?.objective||facts.objective),n('p',facts.step));
  // A plan awaiting approval must expose its constraints and work sequence
  // before the reader opens the complete saved-plan disclosure. The action
  // itself stays in the conversation transcript.
  if(ready&&!approved){
    box.append(planDocumentSection('Constraints',brief.constraints),
               planDocumentSection('Implementation sequence',brief.implementation_sequence||brief.technical_approach||brief.milestones||brief.plan,true));
  }
  box.append(overview,current,disclosure('Requirements and verification','plan-requirements:'+run.goal_token,[checklist],run.run),disclosure('Saved plan details','saved-plan-details:'+run.goal_token,[provenance,body],run.run));
  if(run.astra_plan?.current_plan?.length){const strategy=card('','current-strategy');strategy.append(n('h3','Current execution approach'),n('p','The team’s latest work sequence within this task. This is separate from the plan approval above.'),planList(run.astra_plan.current_plan));box.append(strategy);}host.append(box);
}
function output(parent,actions) {for(const action of actions||[]){const status=action.exit_status===2?'needs attention':action.status;parent.append(disclosure(action.label+' · '+status,'action:'+action.id,[n('pre','Exit: '+(action.exit_status??'pending')+'\nstdout:\n'+(action.stdout||'')+'\nstderr:\n'+(action.stderr||''))],'actions'));}}
function renderExecution(run){
  const host=$('#execution_view');host.replaceChildren(n('h2','Checks & evidence'));
  if(!(run.criteria||[]).length&&!run.validation?.source_revision)host.append(emptyState('No checks recorded yet.','No verification evidence has been saved for “'+taskTitle(run)+'”. Checks appear here after the Tester runs.'));
  if(run.review_token&&run.review_criteria?.length){
    // Review approvals happen only in the chat transcript: the Checks pane
    // keeps read-only saved status plus a focus path to the chat controls.
    const review=card('','requested-reviews'),pending=run.review_criteria.filter(criterion=>run.human_reviews?.[criterion.id]?.token!==run.review_token),current=statusInfo(run).label==='Review output';
    review.append(n('h3',current?'Your review · '+pending.length+' remaining':!pending.length?'Your review is recorded':'Saved review request'),n('p',current?'Inspect the evidence below, then approve each item in the conversation transcript. Your approval applies to this result only.':!pending.length?'All items are approved for this saved result. The next action appears in the conversation.':'This request is historical. Current state determines when review is available.'));
    for(const criterion of run.review_criteria){const box=card('','review-card'),reviewed=run.human_reviews?.[criterion.id]?.token===run.review_token;box.append(n('p',criterion.id+' · '+criterion.criterion),n('p',reviewed?'Approved for this saved result.':'Waiting for your approval in the conversation transcript.'));review.append(box);}
    if(current&&run.human_request_authorized===true&&pending.length)review.append(button('Review in conversation →',()=>focusChatAction('review:'+run.run+':'+pending[0].id)));
    review.append(disclosure('Review identity','review-token',[n('code',run.review_token)],run.run));host.append(review);
  }
  host.append(disclosure('Activity, findings & Builder batches','monitor-details',[monitorDetailsPanel(run)],run.run));
  const currentCounts=run.work_summary?{pass:run.work_summary.counts.checked,fail:run.work_summary.counts.failed,unknown:run.work_summary.counts.unchecked}:run.counts;
  const stats=card('','execution-summary');for(const [key,label]of [['pass','criteria passed'],['fail','need attention'],['unknown','not yet verified']]){const item=n('span',label);item.prepend(n('strong',currentCounts?.[key]||0));stats.append(item);}host.append(stats);
  const validation=run.validation||{},results=new Map((Array.isArray(validation.criterion_results)?validation.criterion_results:[]).filter(row=>row&&typeof row==='object').map(row=>[row.id,row]));
  host.append(Object.assign(n('p',validation.source_revision?'Saved verification · source '+String(validation.source_revision).slice(0,18):results.size?'Saved verification · source not recorded':'No verification report has been saved yet.'),{className:'field-note'}));
  for(const reason of run.verification?.reasons||[])host.append(Object.assign(n('p',reason),{className:'field-note'}));
  for(const criterion of run.criteria||[]){const row=card('','check-row'),result=results.get(criterion.id)||{},status=({checked:'pass',failed:'fail'}[workRequirementRows(run).find(item=>String(item.criterion.id)===String(criterion.id))?.state]||'unverified');row.dataset.workDetail='requirement';row.dataset.workId=String(criterion.id);row.tabIndex=-1;focusKey(row,'work-requirement:'+run.run+':'+criterion.id);row.append(Object.assign(n('span',human(status)),{className:'badge '+(status==='pass'?'complete':status==='fail'?'failed':'')}),n('strong',criterion.criterion||criterion.description||criterion.id));row.append(n('p',criterion.verification_method?'Planned check: '+criterion.verification_method:'No verification method was recorded.'));if(criterion.human_review)row.append(n('p','Human acceptance is required separately in chat.'));if(result.evidence_refs?.length)row.append(disclosure('Recorded evidence · '+result.evidence_refs.length,'evidence:'+criterion.id,[renderDocument(result)],run.run));host.append(row);}
  for(const problem of run.work_summary?.problems||[]){const row=card('','problem-detail');row.dataset.workDetail='problem';row.dataset.workId=String(problem.id);row.tabIndex=-1;focusKey(row,'work-problem:'+run.run+':'+problem.id);row.append(n('h3',problem.label),n('p',[problem.id,problem.severity,problem.source?'Reported by '+roleDisplayName(problem.source):null,problem.times_reported>1?'Reported '+problem.times_reported+' times':null].filter(Boolean).join(' · ')));host.append(row);}
  if(validation.checks?.length)host.append(disclosure('Executed checks ('+validation.checks.length+')','executed-checks',[renderDocument(validation.checks)],run.run));
  if(validation.end_to_end_result)host.append(disclosure('End-to-end result','e2e-check',[renderDocument(validation.end_to_end_result)],run.run));
  if(validation.findings?.length)host.append(disclosure('Review findings ('+validation.findings.length+')','review-findings',[renderDocument(validation.findings)],run.run));
  host.append(disclosure('Full acceptance criteria','criteria',[renderDocument(run.criteria||[])],run.run));
  const steps=card('','saved-steps');for(const [index,stage]of (run.stages||[]).entries()){const row=card('','stage-row');row.append(n('span',stageName({...run,stage:stage.stage||stage.role})),n('small',typeof stage.duration_seconds==='number'?stage.duration_seconds.toFixed(1)+'s':'Duration not recorded'));row.append(disclosure('Stage details','stage:'+index,[renderDocument(stage)],run.run));steps.append(row);}host.append(disclosure('Step history ('+(run.stages||[]).length+')','step-history',[steps],run.run));
  const logs=$('#actions');logs.replaceChildren();if(!(run.actions||[]).length)logs.append(Object.assign(n('p','No command output in this dashboard session. Saved task state remains available after a restart.'),{className:'field-note'}));output(logs,run.actions);
}

async function loadChanges(force=false){
  if(!chosen?.run)return;const run={...chosen},ticket=++evidenceRequest,host=$('#changes-content');evidenceRun=run.run;
  host.replaceChildren(Object.assign(n('p','Loading saved changes…'),{className:'field-note'}));
  try{const base='/api/evidence?workspace='+encodeURIComponent(run.workspace)+'&run='+encodeURIComponent(run.run),key=run.run;
    if(force)for(const id of evidenceCache.keys())if(id.startsWith(key+'\0'))evidenceCache.delete(id);
    const data=await api(base);if(ticket!==evidenceRequest||chosen?.run!==run.run)return;host.replaceChildren();
    if(!data.stages?.length){host.append(emptyState('No saved changes yet.','No code snapshots have been saved for “'+taskTitle(latestRun&&latestRun.run===run.run?latestRun:run)+'”. Diffs appear when an implementation step finishes.'));return;}
    for(const stage of [...data.stages].reverse()){
      const body=card('','diff-content'),item=disclosure(stageName({...latestRun,stage:stage.stage})+' · '+(stage.finished_at?new Date(stage.finished_at).toLocaleString():'Saved snapshot')+(stage.rejected?' · rejected output':stage.interrupted?' · interrupted':''),'diff-stage:'+stage.index,[body],run.run);item.className='diff-stage';let loading=false,loaded=false;const remember=item.ontoggle;
      const read=async()=>{if(!item.open||loading||loaded)return;loading=true;body.replaceChildren(n('p','Loading snapshot…'));try{
        const cacheKey=key+'\0'+stage.index,result=evidenceCache.get(cacheKey)||await api(base+'&stage='+stage.index);evidenceCache.set(cacheKey,result);if(!item.isConnected||ticket!==evidenceRequest)return;body.replaceChildren();
        if(Array.isArray(stage.changed_files))for(const [index,path]of stage.changed_files.entries()){
          const file=card('','changed-file'),content=card('','file-contents');
          const open=button('View current '+String(path),async()=>{open.disabled=true;content.replaceChildren(n('p','Reading current file…'));try{const data=await api(base+'&stage='+stage.index+'&file='+index);if(!file.isConnected||ticket!==evidenceRequest)return;content.replaceChildren(Object.assign(n('p','Current file contents · this file may have changed since the saved step.'),{className:'field-note'}),data.binary?n('p','Binary file. Text preview is unavailable.'):n('pre',data.text));if(data.truncated)content.append(n('p','Showing the first 192 KB.'));}catch(error){content.replaceChildren(Object.assign(n('p','Current file unavailable. It may have been removed or renamed.'),{className:'error'}));}finally{open.disabled=false;}},'text-button');file.append(open,content);body.append(file);
        }
        if(result.text){const pre=n('pre','');pre.className='diff-output';for(const line of result.text.split('\n'))pre.append(Object.assign(n('span',line||' '),{className:'diff-line '+(line.startsWith('+++')||line.startsWith('---')||line.startsWith('@@')||line.startsWith('diff ')?'header':line.startsWith('+')?'added':line.startsWith('-')?'removed':'')}));body.append(pre);}else body.append(Object.assign(n('p','No tracked diff in this snapshot. Newly created files can be read above.'),{className:'field-note'}));
        if(result.truncated)body.append(Object.assign(n('p','Large snapshot: showing the first 192 KB. The full diff remains in the task folder.'),{className:'field-note'}));loaded=true;
      }catch(error){body.replaceChildren(Object.assign(n('p',error.message),{className:'error'}),button('Retry loading',read));}finally{loading=false;}};
      item.ontoggle=()=>{remember?.();read();};host.append(item);
    }
  }catch(error){if(ticket===evidenceRequest&&chosen?.run===run.run)host.replaceChildren(Object.assign(n('p',error.message),{className:'error'}),button('Retry loading',()=>loadChanges(true)));}
}
function localPreviewUrl(raw,dashboardUrl){
  const value=new URL(raw),dashboard=new URL(dashboardUrl),local=['127.0.0.1','localhost','[::1]'];
  if(!['http:','https:'].includes(value.protocol)||!local.includes(value.hostname)||value.username||value.password||value.port===dashboard.port)throw Error('Use a local app address on a different port, such as http://127.0.0.1:3000.');
  return value.href;
}
const sessionPreviewUrls=new Map();
function previewContext(){return latestRun?.run===chosen?.run?latestRun:chosen||{};}
function previewStorageKey(run){return 'preview-project:'+(run.project_workspace||run.workspace||('task:'+run.run));}
function savedPreview(run){return sessionPreviewUrls.get(previewStorageKey(run))||stored(previewStorageKey(run))||stored('preview:'+run.run);}
function previewChangeKey(run){
  const stages=(run.stages||[]).filter(row=>['terra','orchestrator'].includes(row.stage)&&row.finished_at&&stageSucceeded(row)&&(row.changed_files||[]).length);
  const last=stages.at(-1);return last?JSON.stringify([last.source_revision,last.diff_ref,last.finished_at]):'';
}
function stopPreview(){const host=$('#preview-content');host.replaceChildren();delete host.dataset.previewIdentity;}
function mountPreview(run,url){
  const host=$('#preview-content'),identity=JSON.stringify([previewStorageKey(run),url,previewChangeKey(run)]);
  if(host.dataset.previewIdentity===identity&&host.querySelector('iframe'))return;
  host.replaceChildren();host.dataset.previewIdentity=identity;
  const toolbar=card('','preview-toolbar'),link=n('a','Open in a new tab ↗');link.href=url;link.target='_blank';link.rel='noopener noreferrer';
  toolbar.append(n('p','If the app cannot be embedded, open it in a new tab.'),link);
  const frame=n('iframe','');frame.className='preview-frame';frame.title='Local app preview';frame.setAttribute('sandbox','allow-scripts allow-forms allow-same-origin');frame.referrerPolicy='no-referrer';frame.src=url;host.append(toolbar,frame);
}
function renderPreviewState(){
  const note=$('#preview-empty-note');if(!note)return;
  const run=previewContext(),saved=savedPreview(run);
  const title=taskTitle(run);
  const persistent=stored(previewStorageKey(run))===saved||stored('preview:'+run.run)===saved;
  note.textContent=saved?(persistent?'Saved for this project':'Open for this browser session only')+' · “'+title+'”: '+saved:'No app address is saved for this project yet · “'+title+'”. Start your app, then enter its local address below.';
  if(!saved){stopPreview();return;}
  try{mountPreview(run,localPreviewUrl(saved,location.href));}
  catch(error){stopPreview();$('#preview-error').textContent=error.message;}
}
$('#refresh-changes').onclick=()=>loadChanges(true);
$('#preview-form').onsubmit=event=>{
  event.preventDefault();if(!chosen?.run)return;
  try{const run=previewContext(),url=localPreviewUrl($('#preview-url').value.trim(),location.href);sessionPreviewUrls.set(previewStorageKey(run),url);persist(previewStorageKey(run),url);$('#preview-error').textContent='';stopPreview();renderPreviewState();}
  catch(error){$('#preview-error').textContent=error.message;}
};
function renderScreenshotEvidence(host,run){
  for(const shot of run.screenshots||[]){
    const box=card('','screenshot-evidence'),url='/api/screenshot?'+new URLSearchParams({workspace:run.workspace,run:run.run,image:shot.id});
    box.dataset.screenshotId=shot.id;
    box.append(n('h3','Saved screenshot · '+shot.criterion_id),n('p',shot.label),Object.assign(n('p','Recorded result: '+shot.status+' · '+(shot.source_revision?'source '+shot.source_revision:'source not recorded')+'. '+(shot.hash_recorded?'The saved fingerprint is checked when the image opens.':'No image fingerprint was recorded.')+' Opening this image does not approve it.'),{className:'field-note'}));
    const image=n('img','');image.src=url;image.alt=shot.criterion_id+' — '+shot.label;image.loading='lazy';image.decoding='async';
    image.onerror=()=>{image.hidden=true;box.append(Object.assign(n('p','This saved image is unavailable. Refresh the task to inspect its current evidence.'),{className:'field-note'}));};
    const link=n('a','Open saved screenshot');link.href=url;link.target='_blank';link.rel='noopener noreferrer';link.className='text-button';
    box.append(image,link,button('View requirement '+shot.criterion_id,()=>openWorkDetail('requirement',shot.criterion_id),'text-button'));host.append(box);
  }
}

function requestKey(run,kind){return run+'\0'+kind;}
function interruptedAttempt(run){
  return ['PAUSED_PROVIDER_UNCERTAIN','PAUSED_UNCERTAIN_STAGE'].includes(run.status) ? run.interventions?.attempt_id : null;
}
function taskActionBusy(run){return taskActionPending.has(run.run)||(run.actions||[]).some(action=>['queued','running'].includes(action.status));}
function primaryAction(run,busy=false){
  const info=statusInfo(run);
  if(run.status==='TASK_COMPLETE')return {kind:'checks',label:'Review checks'};
  if(run.interventions?.mode==='unavailable'||run.error||run.state_error)return {kind:'none',label:'Unavailable',disabled:true};
  // An applied durable stop is terminal: Continue, Resume, Start building and
  // Finish task stay suppressed while the stop receipt stands. Every other
  // stopped-group resume path below keeps working when no stop stands.
  if(run.interventions?.stop_intent)return {kind:'checks',label:'Stopped'};
  if(info.group==='running')return {kind:'pause',label:'Pause after current step'};
  if(busy)return {kind:'none',label:'Working…',disabled:true};
  if(['Awaiting Resolver','Resolver paused'].includes(info.label))return {kind:'checks',label:'Inspect details'};
  if(interruptedAttempt(run))return {kind:'recover',label:'Review recovery'};
  if(info.label==='Answer needed')return {kind:'answer',label:run.questions.length>1?'Answer '+run.questions.length+' questions':'Answer question'};
  if(info.label==='Approve plan')return {kind:'plan',label:'Review plan'};
  if(info.label==='Review output')return {kind:'checks',label:'Review output'};
  if(info.label==='Ready to finish')return {kind:'continue',label:'Finish task'};
  if(info.group==='attention')return {kind:'answer',label:'Reply'};
  if(run.interventions?.recovery?.version===1&&info.group==='stopped')return {kind:'recover',label:'Review recovery'};
  if(info.label==='Planning needs retry')return {kind:'continue',label:'Retry planning'};
  if(info.label==='Internally blocked'||info.label==='Worker unverified')return {kind:'checks',label:info.action};
  if(info.group==='stopped')return {kind:'continue',label:run.goal?.approval_status==='approved'?(!run.monitor?.orchestration_batch&&!run.stages?.some(stage=>['terra','orchestrator'].includes(stage.stage))?'Start building':'Resume task'):'Resume planning'};
  return {kind:'none',label:'Inspect activity',disabled:true};
}
function taskSentence(run,busy=false){
  const info=statusInfo(run),stage=(run.active_stage?.stage||run.monitor?.next_stage||run.stage||'').replace(/_report_repair$/,''),role=stageName({...run,stage}).split(' · ')[0];
  if(busy&&info.group!=='running')return 'Processing your last action…';
  if(info.group==='running'){
    const phrases={requirements_gather:'gathering requirements',astra_resolve:'diagnosing the failure',astra_discovery:'drafting the plan',astra_challenge:'reviewing the draft plan',glm_revise:'revising the plan',astra_finalize:'finalizing the plan',orchestrator:'coordinating independent Builders',terra:'implementing the current step',sol:'verifying the changes',astra_checkpoint:'auditing the completed work',astra_review:'reviewing the latest results',astra:'assigning the next step'};
    const sentence=role+' is '+(phrases[stage]||'working on the current step');
    return run.monitor?.live?.state==='alive'?sentence:'Last reported: '+sentence;
  }
  return info.label==='Checks need review'?'Recorded completion needs verification':info.label==='Answer needed'?'Your answer is needed to continue':info.label==='Approve plan'?'The plan is ready for your approval':info.label==='Review output'?'Your review is needed before completion':info.label==='Ready to finish'?'Your review is saved. Ready to finish.':info.group==='complete'?'The task is complete':info.label==='Planning needs retry'?'Planning stopped. A fresh draft is needed.':info.label==='Ready to continue'?'Ready for the next step':info.label==='Interrupted'?'An interrupted attempt needs review':info.group==='attention'?'Your decision is needed':info.label==='Worker stopped'?'The worker stopped at a saved checkpoint':'The task is paused';
}
function taskDecision(run,busy=false){
  const info=statusInfo(run),approved=run.goal?.approval_status==='approved',revision=run.goal?.revision??'?',action=primaryAction(run,busy);
  const result=(title,description,after,required=false)=>({title,description,after,required,action});
  if(run.error||run.state_error||run.interventions?.mode==='unavailable')return result('Status needs checking','Current controls are unavailable. Review the saved issue in History.','Restore access to the checkpoint before taking action.');
  if(busy&&info.group!=='running')return result('Your last action is being processed','Wait for the saved state to update.','This page will show the next decision when it is ready.');
  if(info.group==='complete')return result('No approval pending','This task is recorded as complete.','You can inspect the saved changes and verification.');
  if(['Awaiting Resolver','Resolver paused'].includes(info.label))return result(info.label,info.reason,'Inspect the saved plan, reports, and evidence. No answer or approval is currently requested.');
  if(operationalRequest(run))return result('Resolver needs information',info.reason,'Provide corrective information in Conversation or choose Leave paused. Neither response continues execution, approves goals, changes permissions, or resets budgets.',true);
  if(info.label==='Answer needed')return result('Answer '+run.questions.length+' '+(run.questions.length===1?'question':'questions'),run.questions[0].question||info.reason,'Choose '+action.label+' above, or Conversation → Your reply → Send answer. Planning continues only when the controller returns to Running without a pending request. Plan approval is separate.',true);
  if(info.label==='Approve plan')return result('Approve plan revision '+revision,run.goal?.body?.intended_outcome||info.reason,'Review the Plan pane, then approve revision '+revision+' in the conversation transcript. This records approval; Start building or Resume task is the next action.',true);
  if(info.label==='Review output')return result('Review the finished work',info.reason,'Open Checks, inspect the evidence and approve each requested item. Then choose Finish task to run the final completion check.',true);
  if(info.group==='attention')return result('Your decision is needed',info.reason,'Open Conversation and reply to the current request. Your reply does not approve a new plan revision.',true);
  if(action.kind==='recover'&&run.interventions?.recovery?.version===1)return result(run.interventions.recovery.title,run.interventions.recovery.what_happened,'Inspect the saved checkpoint in chat and choose its specific next action.',true);
  if(action.kind==='recover')return result('Review the interrupted attempt',info.reason,'Choose Review recovery above, inspect the saved attempt, then Recover saved work. Resume is a separate action.',true);
  if(info.label==='Planning needs retry')return result('Retry the planning step',info.reason,'Retry planning requests a new draft. You will review the final plan before approving it.');
  if(info.label==='Internally blocked')return result('Internal failure needs repair',info.reason,'Inspect the saved failure in Checks, correct its cause, then retry the saved step. No approval is requested.');
  if(info.label==='Ready to finish')return result('Your review is saved','All requested review items have an approval for the current result.','Choose Finish task above to run the final completion check.');
  if(approved)return result('No approval pending','Plan revision '+revision+' is already approved.',info.group==='running'?'The team is working under that approved plan. You can send feedback in Conversation.':action.kind==='continue'?'Choose '+action.label+' above to continue the saved next step under this approved plan.':'Inspect History for the next saved action.');
  if(run.status==='DRY_RUN')return result('Preview only','This dry run did not start implementation.','There is no current approval request.');
  return result('Plan approval is not ready yet','The team is still preparing or revising the plan.',info.group==='running'?'Wait for the final draft. The approval request will appear here.':'Resume planning from the saved step. The final draft will need your approval.');
}
function taskPhase(run){
  const info=statusInfo(run),stage=String(run.monitor?.next_stage||run.stage||run.active_stage?.stage||'').replace(/_report_repair$/,'');
  if(info.group==='complete')return 'complete';
  if(info.label==='Approve plan')return 'approval';
  if(info.label==='Review output'||info.label==='Ready to finish'||/^(sol|astra_review|astra_checkpoint)$/.test(stage))return 'review';
  if(/^(requirements_gather|astra_discovery|astra_challenge|glm_revise|astra_finalize)$/.test(stage)||run.goal?.approval_status!=='approved')return 'planning';
  if(stage==='orchestrator')return 'orchestration';
  return 'implementation';
}
function taskPosition(run){
  const parts=[];
  if(run.goal?.revision!=null)parts.push('Plan r'+run.goal.revision+' · '+(run.goal.approval_status==='approved'?'approved':statusInfo(run).label==='Approve plan'?'needs approval':'draft'));
  if(Number.isFinite(run.iteration))parts.push('Iteration '+run.iteration+(run.monitor?.limits_known&&run.monitor.iteration_limit!=null?' of '+run.monitor.iteration_limit:''));
  return parts.join('  /  ');
}
function stageRoleKey(record) {
  const route=record?.route_role||record?.role;
  if(route)return String(route);
  const stage=String(record?.stage||'').replace(/_report_repair$/,'');
  return ({requirements_gather:'requirements',astra_discovery:'glm',glm_revise:'glm',astra_challenge:'plan_reviewer',astra_finalize:'plan_reviewer',astra_plan:'astra',astra_review:'completion',astra_checkpoint:'completion',astra_resolve:'resolver'})[stage]||stage.split('_')[0];
}
function workRoleAttributions(run) {
  // Actual role/model attributions come only from recorded executions: the
  // launches saved with finished stages and the active execution. The real
  // dashboard_monitor.snapshot projection carries settings.roles in
  // monitor.roles even before any stage runs, so a configured future route
  // must never be presented here; configured routes stay in the separately
  // labeled configured view (Stages & models).
  const monitor=run.monitor||{};
  const history=Array.isArray(monitor.stage_history)&&monitor.stage_history.length?monitor.stage_history:run.stages||[];
  const lines=[];
  const add=(role,execution,stage)=>{
    if(!execution||execution.kind!=='model'||!execution.model)return;
    const effort=execution.reasoning_effort;
    const catalogue=globalThis.AUTOCODE_ROLE_NAMES,entry=catalogue.modes[monitor.workflow_mode]?.[stage]||catalogue.stages[stage];
    const line=(entry?.role||roleDisplayName(role))+' · '+[execution.model,effort?effort+' reasoning':null].filter(Boolean).join(' · ');
    if(!lines.includes(line))lines.push(line);
  };
  for(const record of history)add(record?.route_role||record?.role||stageRoleKey(record),record?.execution,record?.stage);
  if(monitor.active_execution)add(monitor.active_role||run.active_stage?.route_role||run.active_stage?.role||stageRoleKey(run.active_stage||{}),monitor.active_execution,run.active_stage?.stage);
  return lines;
}
function workSummaryPanel(run) {
  const info=statusInfo(run),facts=stateFacts(run),host=card('','work-summary monitor-panel');
  const title=run.work_summary?.tasks?.find(task=>task.state==='working')?.label||run.current_task?.objective||(info.group==='complete'?'Work complete':taskTitle(run));
  host.append(Object.assign(n('p',info.label==='Recovering'?'Recovering':run.status==='RUNNING'?'Now working':info.group==='complete'?'Completed work':'Saved work'),{className:'monitor-kicker'}));
  const current=card('','work-current');current.append(n('h2',title));
  // 448:635 compact card: one saved-status line (state chip plus current
  // step) under the title, then the actual role/model attributions.
  const status=card('','monitor-state work-status');
  const state=n('p',facts.state);state.className='monitor-status-chip '+info.group;state.dataset.taskFact='state';state.dataset.taskFactVisible='state';state.dataset.taskFactFull='state';
  const step=n('h2',facts.step);step.dataset.taskFact='step';step.dataset.taskFactVisible='step';step.dataset.taskFactFull='step';
  status.append(state,step);
  current.append(status);
  const roles=card('','work-roles');
  const lines=workRoleAttributions(run);
  if(lines.length)for(const line of lines)roles.append(n('p',line));
  else roles.append(n('p','No saved role and model attributions yet.'));
  current.append(roles);host.append(current);
  return host;
}
// The requirements lifecycle state: the saved open questions as a read-only
// summary in Work that points into the conversation, where every answer
// control lives. Work never carries the answer controls itself.
// Each Work row points at its own chat question card: select that question
// in the composer's target control, reveal its card in the transcript, and
// hand focus to the composer. The answer itself stays a chat action.
function questionCardById(id){
  const scope=$('#conversation');
  if(!scope||typeof scope.querySelectorAll!=='function')return null;
  for(const element of scope.querySelectorAll('[data-question-card]'))if(String(element.dataset.questionCard)===String(id))return element;
  return null;
}
function focusQuestionCard(run,question){
  const id=String(question?.id??'');
  const target=$('#question-target');
  if(id&&target&&String(target.value||'')!==id){target.value=id;if(typeof target.onchange==='function')target.onchange();}
  const cardElement=questionCardById(id);
  if(cardElement){cardElement.classList.add('selected-question');cardElement.scrollIntoView({block:'center'});}
  const input=$('#change-text');
  if(input){input.focus();input.scrollIntoView({block:'nearest'});}
}
function workQuestionsPanel(run) {
  const questions=(run.questions||[]);
  const host=card('','work-questions monitor-panel');
  host.append(Object.assign(n('p','WAITING FOR YOUR REPLY'),{className:'monitor-kicker'}),
              n('h3','Answer '+questions.length+' open '+(questions.length===1?'question':'questions')+' in the conversation'));
  const list=card('','work-question-rows');
  for(const question of questions){
    const row=card('','work-question-row');
    row.dataset.questionId=String(question?.id??'');
    row.append(n('p',String(question?.question||'').trim()||'Saved open question'));
    row.append(Object.assign(n('p','Open · waiting for you in the conversation'),{className:'monitor-caption work-question-status'}));
    row.append(button('Open in conversation →',()=>focusQuestionCard(run,question),'text-button work-question-link'));
    list.append(row);
  }
  host.append(list,Object.assign(n('p','This summary reads the saved questions; nothing is answered here.'),{className:'monitor-caption'}));
  return host;
}
// The recovery lifecycle state: an autonomous technical recovery explains
// itself from the saved record and asks for nothing.
function workRecoveryPanel(run) {
  const host=card('','work-recovery monitor-panel');
  host.append(Object.assign(n('p','TECHNICAL RECOVERY IN PROGRESS'),{className:'monitor-kicker'}),
              n('h3','Recovering automatically'),
              n('p',stateFacts(run).objective),
              Object.assign(n('p','No action is needed from you. Recovery runs on its own; only a real question or approval would ask for input here.'),{className:'monitor-caption'}));
  return host;
}
function workRequirementRows(run) {
  if(run.work_summary?.requirements)return run.work_summary.requirements.map(row=>({criterion:{id:row.id,criterion:row.label},state:row.state}));
  // A saved PASS alone cannot authenticate the current checkout.
  const results=new Map((run.verification?.freshness==='current'?run.verification.coverage||[]:[]).map(row=>[String(row.id),row.state]));
  return (run.criteria||[]).map(criterion=>({criterion,state:results.get(String(criterion.id))||'unchecked'}));
}
function openWorkDetail(kind,id){
  activateTab('execution');
  if(matchMedia('(max-width:759px)').matches)openDetailsDrawer();
  const target=[...document.querySelectorAll('#execution_view [data-work-detail]')].find(node=>node.dataset.workDetail===kind&&node.dataset.workId===String(id));
  if(target){target.scrollIntoView({block:'center'});target.focus();}
}
function appendWorkTasks(host,run){
  const summary=run.work_summary;if(!summary)return;
  const list=n('ol','');list.className='work-task-rows';
  for(const task of summary.tasks){const item=n('li','');item.dataset.taskState=task.state;item.append(n('span',({done:'✓',working:'●',waiting:'○',unknown:'?'}[task.state]||'?')+' '+task.label),n('small',({done:'Done · recorded acceptance',working:'Working · saved active step',waiting:'Waiting',unknown:'Completion not yet verified'}[task.state]||'Unknown')));list.append(item);}
  const tasks=disclosure(summary.task_label,'work-task-list',[list],run.run);tasks.classList.add('work-task-list');if(!detailsState.has(run.run+'\0work-task-list')&&matchMedia('(min-width:1200px)').matches)tasks.open=true;host.append(tasks);
  if(summary.verification_stale)for(const reason of summary.verification_reasons||['Current evidence needs verification.'])host.append(Object.assign(n('p',reason),{className:'field-note'}));
  if(summary.problems.length){const problems=n('div','');for(const problem of summary.problems)problems.append(button(problem.label,()=>openWorkDetail('problem',problem.id),'text-button work-detail-link'));host.append(disclosure('Open problems · '+summary.problems.length,'work-problem-list',[problems],run.run));}
}
function renderTaskProgress(run){
  const control=$('#task-progress-summary'),summary=run.work_summary;
  control.hidden=!summary||!!run.task_archived||!!run.project_removed;
  if(control.hidden)return;
  control.textContent=summary.line;control.title=control.textContent;
  control.onclick=()=>{activateTab('now');if(matchMedia('(max-width:759px)').matches)openDetailsDrawer();const list=$('#now .work-task-list');if(list){list.open=true;const summary=list.querySelector('summary');summary?.focus();requestAnimationFrame(()=>{if(summary?.isConnected&&document.activeElement===summary)summary.scrollIntoView({block:'start'});});}};
}
function workChecklistPanel(run,linked=false) {
  const host=card('','work-checklist monitor-panel'),rows=workRequirementRows(run),counts=card('','work-checklist-counts');
  host.append(n('h3','Live checklist'),counts);
  if(linked)appendWorkTasks(host,run);
  if(!rows.length){
    counts.append(n('p','No requirements have been saved for this work yet.'));
    host.append(Object.assign(n('p','Requirements come from the saved plan. Checkmarks require current evidence.'),{className:'monitor-caption'}));
    return host;
  }
  const checked=rows.filter(row=>row.state==='checked').length,failed=rows.filter(row=>row.state==='failed').length,unchecked=rows.length-checked-failed;
  counts.append(n('p',[checked+' checked',failed?failed+' failed':null,unchecked+' unchecked'].filter(Boolean).join(' · ')));
  const list=card('','work-checklist-rows');
  for(const row of rows){
    const item=card('','work-check-row '+row.state);
    item.append(Object.assign(n('span',row.state==='checked'?'✓':row.state==='failed'?'!':'○'),{className:'work-check-icon'}),(()=>{const copy=n('p','');if(linked)copy.append(button(planEntryText(row.criterion),()=>openWorkDetail('requirement',row.criterion.id),'text-button work-detail-link'));else copy.textContent=planEntryText(row.criterion);return copy;})());
    list.append(item);
  }
  if(linked){
    const details=disclosure('Requirements and evidence · '+rows.length,'work-requirement-list',[list],run.run);
    details.classList.add('work-requirement-list');host.append(details);
  }else host.append(list);
  host.append(Object.assign(n('p','Requirements come from the saved plan. Checkmarks require current evidence.'),{className:'monitor-caption'}));
  return host;
}
function renderTaskNow(run){
  renderTaskProgress(run);
  const host=$('#now'),decision=taskDecision(run,taskActionBusy(run)),phase=taskPhase(run),assignment=run.astra_plan?.current_assignment;
  const signature=JSON.stringify([run.run,run.task,run.display_title,run.status,run.stage,run.iteration,run.goal_token,run.goal?.approval_status,run.questions,run.user_request,run.stop_reason,run.monitor,run.criteria,run.validation,run.verification,run.completion_current,run.work_summary,assignment,decision]);
  if(host.dataset.rendered===signature)return;host.dataset.rendered=signature;host.replaceChildren();
  const path=n('ol','');path.className='task-path';path.setAttribute('aria-label','Workflow stage');
  const stages=[['planning','Plan'],['approval','Your approval'],...(hasOrchestration(run)?[['orchestration','Orchestrator']]:[]),['implementation','Build'],['review','Review'],['complete','Complete']];
  if(hasOrchestration(run))path.classList.add('with-orchestration');
  const currentStage=stages.findIndex(([key])=>key===phase);
  for(const [index,[key,label]]of stages.entries()){const item=n('li',label);if(index<currentStage)item.className='done';if(key===phase){item.className='current';item.setAttribute('aria-current','step');}path.append(item);}
  // Chat-first Work hierarchy (422:1613): the compact Now-working lead card
  // carries the saved status line (state chip, current step) and the actual
  // role attributions. The live checklist follows integrated directly below
  // — no second bordered card displaces it — and the remaining operational
  // facts render in their own compact Work section after the stage path.
  // Milestone checkpoints, Builder batches, the assignment scope and the
  // current-plan/last-step context render in History instead of stacking
  // ahead of the summary.
  const lead=workSummaryPanel(run);
  host.append(lead);
  if(run.status==='RUNNING'&&(run.active_stage?.stage||run.stage)==='astra_resolve')host.append(workRecoveryPanel(run));
  host.append(workChecklistPanel(run,true));
  // The remaining saved facts follow the integrated checklist in their own
  // compact section — the reference's verification block (448:667) — so they
  // stay inside the unscrolled fold while the lead stays small. Every human
  // decision summary and its chat pointer render after them.
  host.append(monitorPanel(run));
  if(decision.required){const decisionCard=card('','decision-card needs-decision');decisionCard.append(Object.assign(n('p','YOUR NEXT ACTION'),{className:'eyebrow'}),n('h2',decision.title),n('p',decision.description),Object.assign(n('p',decision.after),{className:'decision-after'}));host.append(decisionCard);}
  if(statusInfo(run).label==='Answer needed')host.append(answerSafetyPanel((run.questions||[]).length),workQuestionsPanel(run));
  host.append(path);
  // Configured routes stay inspectable in Work, clearly after the summary and
  // checklist, never as actual attributions inside the summary itself.
  host.append(workflowModelsPanel(run));
}
function renderPrimaryAction(run){
  $('#task-subtitle').textContent=taskSentence(run,taskActionBusy(run));
  const action=primaryAction(run,taskActionBusy(run)),control=$('#continue-run'),pause=$('#pause-run'),stopControl=$('#stop-run');
  const blocked=run.task_archived||taskArchiveBlocked(run.run)||run.project_removed||projectBlocked(run.workspace),modelBlocked=typeof unresolvedModelReplacement==='function'&&unresolvedModelReplacement(run)&&action.kind==='continue';
  pause.hidden=action.kind!=='pause'||blocked;control.hidden=action.kind==='pause'||blocked||currentTab==='plan'||(action.kind==='checks'&&currentTab==='execution');
  const stopStanding=!!run.interventions?.stop_intent,stopRequested=(run.interventions?.entries||[]).some(entry=>entry.kind==='stop'&&['queued','requested'].includes(entry.status))||stopStanding;
  stopControl.hidden=(action.kind!=='pause'&&!stopStanding)||blocked;stopControl.disabled=stopRequested||sendingRequests.has(requestKey(run.run,'stop'))||blocked||run.interventions?.stop_capable!==true;stopControl.title=run.interventions?.stop_capable===true?'Finish and save the current step, then stop':'This runner does not support Stop; Pause remains available';
  stopControl.textContent=stopStanding?'Stopped':stopRequested?'Stop requested':sendingRequests.has(requestKey(run.run,'stop'))?'Requesting stop…':'Stop';
  stopControl.onclick=()=>sendChange(run,'stop');
  const pauseRequested=run.interventions?.pause_intent&&!run.interventions.pause_intent.acknowledged_at||(run.interventions?.entries||[]).some(entry=>entry.kind==='pause'&&['queued','requested'].includes(entry.status));
  pause.textContent=pauseRequested?'Pause requested':sendingRequests.has(requestKey(run.run,'pause'))?'Requesting pause…':'Pause after current step';pause.disabled=!!pauseRequested||sendingRequests.has(requestKey(run.run,'pause'))||blocked;
  // Completed work has its primary inspection action in the chat's ready
  // card. Keep Checks reachable without competing with Open preview. A
  // completion whose evidence is stale still needs the primary Checks action.
  pause.classList.toggle('primary',!pause.hidden);control.classList.toggle('primary',!control.hidden&&statusInfo(run).group!=='complete');
  pause.onclick=()=>sendChange(run,'pause');control.textContent=action.label;control.disabled=!!action.disabled||taskChatPending.has(run.run)||blocked||modelBlocked;
  const gate=$('#task-model-gate');if(gate){gate.hidden=!modelBlocked;gate.textContent=modelBlocked&&typeof taskModelGateCopy==='function'?taskModelGateCopy(run):'';if(modelBlocked)control.setAttribute?.('aria-describedby','task-model-gate');else control.removeAttribute?.('aria-describedby');}
  control.onclick=()=>{
    if(action.kind==='continue')submitTaskAction(run,'continue');
    else if(action.kind==='plan')activateTab('plan');
    else if(action.kind==='checks')activateTab('execution');
    else{activateTab('interview');if(action.kind==='recover')$('#task-attention').scrollIntoView({block:'nearest'});else{$('#change-text').focus();const id=$('#question-target').value;const target=[...document.querySelectorAll('[data-question-card]')].find(element=>element.dataset.questionCard===id);target?.scrollIntoView({block:'nearest'});}}
  };
  $('#continue').disabled=action.kind!=='continue'||control.disabled;$('#continue').onclick=()=>{if(!$('#continue').disabled)submitTaskAction(run,'continue');};
  const boundary=$('#task-safe-boundary'),isRunning=action.kind==='pause';
  boundary.hidden=!isRunning;boundary.textContent=isRunning?'Pause and Stop wait for the current safe step to finish; no saved checkpoint is discarded.':'';
}
async function submitTaskAction(run,action,extra={}){
  const current=latestRun?.run===run.run?latestRun:run;
  if(action==='continue'&&(extra.expected_goal_token||primaryAction(run).label==='Start building')){
    const token=extra.expected_goal_token||approvedGoalToken(run);
    if(!token||token!==approvedGoalToken(run)||(run.goal_token&&run.goal_token!==token)){
      dashboardNotice('The approved plan changed. Reload before building.');return;
    }
    extra={...extra,token,confirmation:token,expected_goal_token:token};
  }
  if(action==='continue'&&extra.expected_goal_token&&current.conversation?.plan_gate?.pending_product_change){dashboardNotice('A requirement change is awaiting a new reviewed plan.');return;}
  if(taskReadError||taskArchiveBlocked(run.run)||current.task_archived||projectBlocked(run.workspace)||current.project_removed||taskActionBusy(current)||taskChatPending.has(run.run)||(action==='continue'&&typeof unresolvedModelReplacement==='function'&&unresolvedModelReplacement(current)))return;
  if(action==='recover_pause'&&(!extra.recovery_token||current.interventions?.recovery?.token!==extra.recovery_token)){dashboardNotice('The saved pause changed. Refresh and inspect the current recovery card.');return;}
  const scopes={approve_goal:['goal_approval'],approve_review:['human_review'],answer:['clarification','permission','goal_change'],delegate:['clarification','permission','goal_change'],resolver_response:['operational_exhaustion','blocker']}[action];
  const response=scopes?{...resolverResponseFields(run),...extra}:extra;
  if(scopes&&(!resolverReplyCurrent(current,response,scopes)
      ||(action==='approve_goal'&&(statusInfo(current).label!=='Approve plan'||response.token!==current.goal_token||response.confirmation!==current.goal_token))
      ||(action==='approve_review'&&(statusInfo(current).label!=='Review output'||response.token!==current.review_token||!current.human_escalation.request?.criteria?.includes(response.id)))
      ||(['answer','delegate'].includes(action)&&!(current.questions||[]).some(question=>String(question.id)===String(response.id))))) {dashboardNotice('The Resolver request changed. Refresh and review the current request before responding.');return;}
  taskActionPending.add(run.run);seq++;renderLiveControls(run);renderBrief(run);renderExecution(run);renderTaskNow(run);renderInlineTaskAction(run);
  try{return await post('/api/action',{workspace:run.workspace,run:run.run,action,...response});}
  finally{taskActionPending.delete(run.run);if(chosen?.run===run.run){renderLiveControls(latestRun||run);renderBrief(latestRun||run);renderExecution(latestRun||run);renderTaskNow(latestRun||run);renderInlineTaskAction(latestRun||run);}}
}
function modelCatalogueSnapshot(){return typeof modelCatalogue==='object'&&modelCatalogue?modelCatalogue:{models:[],usable:false,loading:true,error:null};}
function modelRouteCandidates(value){const saved=String(value||'');return [saved,/^[a-z0-9.-]+$/i.test(saved)?'openai/'+saved:''].filter(Boolean);}
function savedModelAvailability(value,catalogue=modelCatalogueSnapshot()){
  if(!value)return 'unknown';
  const models=catalogue.usable?catalogue.models:catalogue.lastUsableModels;
  if(!models)return 'unknown';
  return modelRouteCandidates(value).some(candidate=>models.includes(candidate))?'available':'unavailable';
}
function modelReplacementKey(run,role){return 'model-replacement:'+run.run+':'+role;}
function readModelReplacement(run,role){
  const key=modelReplacementKey(run,role),memory=typeof modelReplacementState==='undefined'?null:modelReplacementState.get(key);if(memory)return memory;
  try{const value=JSON.parse(stored(key,''));if(value&&typeof value==='object'){if(typeof modelReplacementState!=='undefined')modelReplacementState.set(key,value);return value;}}catch{}
  return null;
}
function saveModelReplacement(run,role,value){
  const key=modelReplacementKey(run,role);if(typeof modelReplacementState!=='undefined'){if(value)modelReplacementState.set(key,value);else modelReplacementState.delete(key);}persist(key,value?JSON.stringify(value):'');return value;
}
function modelRoleName(role){return roleDisplayName(role);}
function canEditFutureTaskSettings(run){return !taskReadError&&!Object.keys(run.active_stage||{}).length&&!taskActionBusy(run)&&run.status!=='TASK_COMPLETE'&&statusInfo(run).group!=='complete'&&run.interventions?.mode!=='unavailable';}
function replacementCapability(run,role,saved){
  const catalogue=modelCatalogueSnapshot(),declared=run.model_settings?.replacement_support?.[role]||run.model_settings?.replacement_capabilities?.[role];
  if(declared===false||declared?.supported===false)return {supported:false,reason:declared?.reason||'This provider does not support replacement for this saved role.'};
  if(!catalogue.usable)return {supported:false,temporary:true,reason:catalogue.loading?'Compatible models are still loading.':'The compatible-model catalogue is unavailable. Retry catalogue before replacing this model.'};
  const candidates=(catalogue.models||[]).filter(model=>!modelRouteCandidates(saved).includes(model));
  return candidates.length?{supported:true,candidates}:{supported:false,reason:'No compatible replacement is available for this role.'};
}
function updateReplacementFromReceipt(run,role,saved,record){
  if(!record)return record;
  if(record.saved!==saved&&saved===record.proposed&&record.state!=='confirmed')return saveModelReplacement(run,role,{...record,state:'confirmed',confirmed_at:new Date().toISOString(),actor:'Dashboard',receipt:record.receipt||record.action_id});
  if(record.state!=='confirming'||!record.action_id)return record;
  const action=(run.actions||[]).find(entry=>entry.id===record.action_id);if(!action)return record;
  if(action.status==='finished'&&saved===record.proposed)return saveModelReplacement(run,role,{...record,state:'confirmed',confirmed_at:action.finished_at?new Date(action.finished_at*1000).toISOString():new Date().toISOString(),actor:'Dashboard',receipt:action.id});
  if(action.status==='failed')return saveModelReplacement(run,role,{...record,state:'failed',error:action.stderr||action.stdout||'The replacement command failed before changing the saved configuration.'});
  if(action.status==='launch_failed')return saveModelReplacement(run,role,{...record,state:'unconfirmed',error:'The replacement launch could not be confirmed. Refresh status and reconcile request '+record.request_id+' before retrying.'});
  return record;
}
function unresolvedModelReplacement(run){
  const roles=run.model_settings?.roles||{};
  return Object.entries(roles).some(([role,saved])=>saved&&savedModelAvailability(saved)==='unavailable'&&readModelReplacement(run,role)?.state!=='confirmed');
}
function taskModelGateCopy(run){
  const roles=run.model_settings?.roles||{},blocked=Object.entries(roles).find(([,saved])=>saved&&savedModelAvailability(saved)==='unavailable');
  return blocked?'Start or continue is disabled because the saved '+modelRoleName(blocked[0])+' model '+blocked[1]+' is unavailable. Select and separately confirm a supported replacement first.':'';
}
function replacementSelect(run,role,saved,capability,record,editable){
  const id='task-'+role+'-replacement',label=n('label','Proposed replacement for '+modelRoleName(role));label.htmlFor=id;
  const select=n('select');select.id=id;select.setAttribute('aria-label','Proposed replacement for '+modelRoleName(role));
  select.append(Object.assign(n('option','Select a compatible replacement…'),{value:''}));
  for(const model of capability.candidates||[])select.append(Object.assign(n('option',model),{value:model}));
  select.value=record?.proposed||'';select.disabled=!editable||record?.state==='confirming'||record?.state==='unconfirmed';
  const reasonId=id+'-reason';select.setAttribute('aria-describedby',reasonId);label.append(select);
  select.onchange=event=>{const proposed=event.target.value;saveModelReplacement(run,role,proposed?{state:'selected-unconfirmed',saved,proposed,affected:'Future '+modelRoleName(role)+' steps',selected_at:new Date().toISOString()}:null);renderTaskModelSettings(run);renderPrimaryAction(run);};
  return {label,select,reasonId};
}
async function confirmModelReplacement(run,role){
  let record=readModelReplacement(run,role),saved=run.model_settings?.roles?.[role];if(!record?.proposed||!saved||record.state==='confirming'||record.state==='unconfirmed')return;
  record=saveModelReplacement(run,role,{...record,state:'confirming',request_id:mutationRequestId(),requested_at:new Date().toISOString(),error:''});renderTaskModelSettings(run);renderPrimaryAction(run);
  try{const receipt=await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace:run.workspace,run:run.run,action:'set_model',role,model:record.proposed,request_id:record.request_id})});saveModelReplacement(run,role,{...record,action_id:receipt?.id||'',receipt:receipt?.id||record.request_id});}
  catch(error){saveModelReplacement(run,role,{...record,state:error?.uncertain?'unconfirmed':'failed',error:error.message});}
  finally{if(chosen?.run===run.run){renderTaskModelSettings(latestRun||run);renderPrimaryAction(latestRun||run);}await refresh();}
}
async function reconcileModelReplacement(run,role){
  const record=readModelReplacement(run,role);if(!record||!['confirming','unconfirmed'].includes(record.state))return;
  record.reconciling=true;saveModelReplacement(run,role,record);renderTaskModelSettings(run);
  try{await refresh();}
  finally{const current=readModelReplacement(run,role);if(current?.reconciling)saveModelReplacement(run,role,{...current,reconciling:false});if(chosen?.run===run.run)renderTaskModelSettings(latestRun||run);}
}
function renderTaskModelSettings(run){
  const host=$('#task-model-settings');if(!host)return;host.replaceChildren();
  const settings=run.model_settings||{},roles=settings.roles||{},efforts=settings.role_efforts||{},engines=settings.role_engines||{},editable=canEditFutureTaskSettings(run);
  for(const [role,saved] of Object.entries(roles)){
    if(!saved)continue;
    let record=updateReplacementFromReceipt(run,role,saved,readModelReplacement(run,role)),availability=savedModelAvailability(saved),state=availability==='unavailable'?'Unavailable':editable?'Future steps editable':'Read-only';
    if(record?.state==='selected-unconfirmed')state='Replacement selected';if(record?.state==='confirming')state='Confirming';if(record?.state==='confirmed')state='Confirmed';if(record?.state==='unconfirmed')state='Unconfirmed';if(record?.state==='failed')state='Failed';
    const row=card('','saved-model-row'),header=n('header',''),title=n('h4',modelRoleName(role));header.append(title,Object.assign(n('span',state),{className:'model-state '+(availability==='unavailable'?'unavailable':editable?'mutable':'')}));
    row.append(header,Object.assign(n('p',saved),{className:'saved-model-value'}),n('p',(engines[role]||settings.engine||'Saved provider')+' · '+(efforts[role]?efforts[role]+' reasoning':'Reasoning not recorded')));
    if(availability!=='unavailable'){
      if(record?.state==='confirmed')row.append(Object.assign(n('p','Confirmed replacement: '+saved+'. Actor: '+(record.actor||'Dashboard')+'. '+(record.confirmed_at?'Time: '+new Date(record.confirmed_at).toLocaleString()+'. ':'')+'Affected: '+(record.affected||'Future steps only.')+'. Receipt: '+(record.receipt||record.request_id||'unavailable')),{className:'model-replacement-receipt'}));
      if(!editable)row.append(Object.assign(n('p','Saved configuration is read-only while a step is active or after completion.'),{className:'field-note'}));host.append(row);continue;
    }
    row.append(Object.assign(n('p','Saved model unavailable: '+saved+'. It remains the saved value until a replacement is explicitly confirmed.'),{className:'model-replacement-note error'}));
    const capability=replacementCapability(run,role,saved),replacement=card('','model-replacement');
    if(!capability.supported){const selector=replacementSelect(run,role,saved,{candidates:[]},record,false);const reason=Object.assign(n('p',capability.reason),{className:'model-replacement-note'});reason.id=selector.reasonId;replacement.append(selector.label,reason);}
    else{
      const selector=replacementSelect(run,role,saved,capability,record,editable),note=Object.assign(n('p','No replacement is applied by selecting it. '+(record?.proposed?'Proposed model: '+record.proposed+'. The saved model remains '+saved+'. '+(record.affected||'Future steps only.'):'Choose a compatible model for future '+modelRoleName(role)+' steps.')),{className:'model-replacement-note'});note.id=selector.reasonId;replacement.append(selector.label,note);
      if(record?.state==='confirmed'){replacement.append(Object.assign(n('p','Confirmed replacement: '+record.proposed+'. Actor: '+(record.actor||'Dashboard')+'. '+(record.confirmed_at?'Time: '+new Date(record.confirmed_at).toLocaleString()+'. ':'')+'Affected: '+(record.affected||'Future steps only.')+'.'),{className:'model-replacement-receipt'}),Object.assign(n('code',record.receipt||record.request_id||''),{className:'model-replacement-receipt'}));}
      else if(record?.state==='confirming'||record?.state==='unconfirmed'){
        replacement.append(Object.assign(n('p',(record.state==='confirming'?'Confirmation is being recorded. Duplicate confirmation is disabled.':'Confirmation is unconfirmed. Refresh status and reconcile this request before retrying.')+' Request ID: '+(record.request_id||record.receipt||'unavailable')+'.'),{className:'model-replacement-receipt'}));
        const reconcile=button(record.reconciling?'Checking status…':'Retry status',()=>reconcileModelReplacement(run,role),'text-button');reconcile.disabled=!!record.reconciling;reconcile.dataset.staleSafe='true';reconcile.setAttribute('aria-describedby',selector.reasonId);replacement.append(reconcile);
      }
      else {const confirm=button(record?.state==='failed'?'Confirm model replacement':'Confirm model replacement',()=>confirmModelReplacement(run,role),'primary');confirm.disabled=!editable||!record?.proposed;confirm.setAttribute('aria-describedby',selector.reasonId);replacement.append(confirm);if(record?.state==='failed')replacement.append(Object.assign(n('p',record.error||'The prior confirmation failed; the saved model remains unchanged. Retry explicitly if you still want this replacement.'),{className:'model-replacement-note error'}));}
    }
    row.append(replacement);host.append(row);
  }
  if(!host.childElementCount)host.append(Object.assign(n('p','No saved model configuration is available for this task.'),{className:'field-note'}));
}
function renderTaskReasoning(run){
  const efforts=run.model_settings?.role_efforts||{},roles=run.model_settings?.roles||{},active=!!Object.keys(run.active_stage||{}).length,blocked=active||taskActionBusy(run)||!!taskReadError||['running','complete'].includes(statusInfo(run).group),catalogue=modelCatalogueSnapshot();
  for(const role of ['plan_reviewer','astra','terra','sol','completion']){
    const select=$('#task-'+role+'-reasoning');if(!select)continue;
    const levels=supportedReasoningLevels(catalogue,role,roles[role]);if(select.dataset.levels!==JSON.stringify(levels)){const previous=efforts[role]||select.value;select.replaceChildren(Object.assign(n('option','Current rung'),{value:''}));for(const level of levels)select.append(Object.assign(n('option',level==='max'?'Maximum':human(level)),{value:level}));select.value=levels.includes(previous)?previous:'';select.dataset.levels=JSON.stringify(levels);}else select.value=efforts[role]||'';
    select.disabled=blocked;
  }
  const latest=(run.reasoning_escalations||[]).at(-1),escalated=latest?.selected?.profile?' Last automatic escalation: '+roleDisplayName(latest.role)+' → '+latest.selected.profile+'.':'';
  $('#save-task-reasoning').disabled=blocked;$('#task-reasoning-status').textContent=(blocked?(taskReadError?'Reasoning changes are disabled while verification is unavailable.':statusInfo(run).group==='running'?'Reasoning can be changed after the current step finishes.':statusInfo(run).group==='complete'?'This task is complete.':'Wait for the current step to finish.'):'Changes apply to the next model step and only expose levels supported by the selected saved route; later struggle advances the automatic ladder.')+escalated;$('#task-reasoning-status').className='field-note';
}
$('#task-reasoning-form').onsubmit=async event=>{
  event.preventDefault();const run=latestRun;if(!run||taskActionBusy(run)||Object.keys(run.active_stage||{}).length)return;
  const payload={workspace:run.workspace,run:run.run,action:'set_reasoning'};
  for(const role of ['plan_reviewer','astra','terra','sol','completion']){const value=$('#task-'+role+'-reasoning').value;if(value)payload[role+'_reasoning_effort']=value;}
  const status=$('#task-reasoning-status'),button=$('#save-task-reasoning');button.disabled=true;status.textContent='Saving reasoning settings…';status.className='field-note';
  try{await post('/api/action',payload);status.textContent='Reasoning change queued. It will apply to the next model step.';await refresh();}
  catch(error){status.textContent=error.message;status.className='error';button.disabled=false;}
};
function renderRecoveryCard(host,run,recovery){
  host.hidden=false;host.dataset.recoveryToken=recovery.token;
  // The saved projection describes a checkpoint, not human authority. Use the
  // existing current-request gate before presenting it as something to answer.
  const requestInfo=recovery.category==='request'?statusInfo(run):null;
  host.append(n('h3',requestInfo?.label||recovery.title),n('h4','What happened'),n('p',requestInfo?.reason||recovery.what_happened),n('h4','What is retained'),n('p',recovery.retained));
  const groups=recovery.failure_groups||[];
  if(groups.length){
    const history=n('div','');history.append(n('p','Recorded failures are grouped below. Earlier attempts remain in History.'));
    for(const group of groups.slice(0,5)){
      const row=n('p',(group.role||'Step')+' · '+group.count+' recorded attempt(s)'+(group.source_revision?' · source '+String(group.source_revision).slice(0,12):'')+'. '+(group.last_reason||'No per-attempt explanation was recorded.'));
      history.append(row);
    }
    if(groups.length>5)history.append(n('p',(groups.length-5)+' earlier failure group(s) remain in History.'));
    host.append(disclosure('Repeated failures','recovery-failures:'+recovery.token,[history],run.run));
  }
  const details=[];
  if(recovery.saved_reason)details.push(n('p',recovery.saved_reason));
  details.push(renderDocument(recovery.context||{}));
  const inspection=disclosure(recovery.token?'Inspect this saved pause':'Inspect this saved checkpoint','recovery-card:'+recovery.token,details,run.run);
  inspection.dataset.recoveryInspection='true';
  host.append(inspection,n('h4','What you can do'));
  const note=n('p',recovery.token?'Inspect this saved pause before running a recovery action. Each action keeps the existing approval and verification gates.':'Inspect the retained work and worker status. These actions do not start a new worker.');
  note.id='recovery-card-inspection';note.className='field-note';host.append(note);
  const actions=card('','lifecycle-actions'),mutations=[];
  for(const item of recovery.actions||[]){
    if(item.kind==='decision'&&requestInfo?.group!=='attention')continue;
    const execute=['resume','abandon','retry_builder','retry_job','retry_report','retry_failed_stage'].includes(item.kind);
    const control=focusKey(button(item.label,()=>{
      if(execute){
        const current=latestRun?.run===run.run?latestRun:run;
        if(!inspection.open||current.interventions?.recovery?.token!==recovery.token){dashboardNotice('The saved pause changed. Refresh and inspect the current recovery card.');return;}
        return submitTaskAction(run,'recover_pause',{recovery_token:recovery.token,recovery_action:item.id});
      }
      if(item.kind==='inspect'){activateTab('now');return;}
      if(item.kind==='new_conversation'){return startProjectConversation(run.workspace);}
      activateTab('interview');
      if(item.kind==='decision'){
        const request=[...document.querySelectorAll('[data-question-card]')].find(row=>!row.hidden)||$('#inline-task-action');
        if(request&&!request.hidden){request.scrollIntoView({block:'center'});request.querySelector('button,input,textarea')?.focus();return;}
      }
      $('#change-text').focus();$('#change-text').scrollIntoView({block:'nearest'});
    }), 'recovery:'+run.run+':'+item.id);
    control.title=item.effect;control.dataset.recoveryAction=item.id;
    const group=card('','recovery-choice'),effect=n('p',item.effect);effect.className='field-note';
    effect.id='recovery-effect-'+String(item.id).replace(/[^a-z0-9_-]/gi,'-');
    control.setAttribute('aria-describedby',effect.id+(execute?' '+note.id:''));
    if(execute)mutations.push(control);
    group.append(control,effect);actions.append(group);
  }
  if(/PROVIDER|TRANSPORT|ENVIRONMENT|PREFLIGHT|DEPENDENC|AUTH|CONFIG/.test(String(recovery.cause||run.status))&&typeof openWorkspaceSetup==='function'){
    const setup=button('Check workspace setup',openWorkspaceSetup,'text-button');setup.dataset.setupRecovery='true';
    actions.append(setup,n('p','Check local prerequisites and account instructions in a setup chat. This task stays paused.'));
  }
  const update=()=>{for(const control of mutations)control.disabled=!inspection.open||!!taskReadError||taskActionBusy(run)||run.interventions?.mode==='unavailable'||!!(typeof unresolvedModelReplacement==='function'&&unresolvedModelReplacement(run));};
  inspection.addEventListener('toggle',update);update();host.append(actions);
}

function renderTaskAttention(run){
  const host=$('#task-attention'),recovery=run.interventions?.recovery;
  // A native toggle can precede its queued event. Keep the connected disclosure's
  // current value before replacing it, only for this exact run and saved pause.
  if(recovery?.version===1&&recovery.status===run.status&&typeof run.run==='string'&&run.run&&typeof recovery.token==='string'&&recovery.token){
    const key=run.run+'\0recovery-card:'+recovery.token,inspection=host.querySelector('details[data-recovery-inspection]');
    if(inspection?.isConnected&&inspection.querySelector('summary')?.dataset.focusKey==='details:'+key)detailsState.set(key,inspection.open);
  }
  host.replaceChildren();
  const next=statusInfo(run);
  if(recovery?.version===1&&recovery.status===run.status&&!taskActionBusy(run)){renderRecoveryCard(host,run,recovery);return;}
  if(['running','complete','attention'].includes(next.group)||taskActionBusy(run)){host.hidden=true;return;}
  host.append(n('h3',next.label),n('p',next.reason));
  const attempt=interruptedAttempt(run),paused=/PAUSED|BLOCKED|FAILED/.test(run.status||'');
  const busy=taskActionBusy(run),last=(run.actions||[]).at(-1);
  if(paused&&run.stop_reason&&run.stop_reason!==next.reason)host.append(disclosure('Saved pause details','pause-reason',[n('p',run.stop_reason)],run.run));
  if(attempt){
    host.append(n('p','Recovery preserves saved work and never resumes it automatically.'));
    const order=n('ol','recovery-sequence');
    for(const step of ['Review recovery','Inspect interrupted attempt','Recover saved work','Resume separately'])order.append(n('li',step));
    host.append(order);
    const stage=run.active_stage||{},evidence={attempt,stage:stage.stage,started_at:stage.started_at,finished_at:stage.finished_at,exit_code:stage.exit_code,events:stage.events,output:stage.output};
    const inspection=disclosure('Inspect interrupted attempt','recovery:'+attempt,[renderDocument(evidence)],run.run);
    host.append(inspection);
    const gate=n('p','Inspect the saved interrupted attempt before recovering saved work. Recovery does not resume the task.');
    gate.id='recovery-inspection-required';gate.className='field-note';host.append(gate);
    const recover=focusKey(button('Recover saved work',()=>submitTaskAction(run,'recover_stage',{attempt_id:attempt})), 'recover:'+run.run+':'+attempt);
    recover.setAttribute('aria-describedby',gate.id);
    const updateRecoveryGate=()=>{recover.disabled=busy||run.interventions?.mode==='unavailable'||!inspection.open;};
    inspection.addEventListener('toggle',updateRecoveryGate);updateRecoveryGate();host.append(recover);
  }else if(next.label==='Internally blocked'){
    host.append(n('p','Inspect the saved failure and correct its cause before retrying.'));
    const retry=button('Retry saved step',()=>submitTaskAction(run,'continue'),'text-button');
    retry.disabled=busy||run.interventions?.mode==='unavailable';host.append(retry);
  }else if(['Awaiting Resolver','Resolver paused'].includes(next.label)){host.append(n('p','Inspect the saved plan and evidence. No current answer or approval is requested.'));}
  else if((run.questions||[]).length){host.append(n('p','Your saved questions remain in the plan. Resume planning to continue.'));}
  else if(next.label==='Planning needs retry'){host.append(n('p','Retry planning makes a fresh planning request. It does not approve the plan or start implementation.'));}
  else if(jointPlanning(run)&&!planReady(run)&&run.goal?.approval_status!=='approved'){host.append(n('p','Resume to continue planning. You will approve the final plan before implementation.'));}
  const staleFailure=last&&run.status==='RUNNING'&&Date.parse(run.active_stage?.started_at)>last.finished_at*1000;
  if(last&&!staleFailure&&last.exit_status!==2&&['failed','launch_failed'].includes(last.status))host.append(disclosure('Technical attempt details','last-action-error',[n('pre',(last.stderr||last.stdout||'The command could not finish. Inspect command output in Checks.').slice(-2400))],run.run));
  host.hidden=!host.childElementCount;
}
async function sendChange(run,kind,retry){
  const key=requestKey(run.run,kind);if(taskArchiveBlocked(run.run)||run.task_archived||projectBlocked(run.workspace)||run.project_removed||sendingRequests.has(key))return;
  const request=retry||{id:crypto.randomUUID(),kind,text:kind==='feedback'?(changeDrafts.get(run.run)||''):''};
  if(kind==='feedback'&&!request.text.trim())return;
  sendingRequests.set(key,request);uncertainRequests.delete(request.id);renderLiveControls(run);
  try{const response=await api('/api/action',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace:run.workspace,run:run.run,action:kind,text:request.text,request_id:request.id})});if(response.status==='uncertain'||response.status==='failed')uncertainRequests.set(request.id,{...request,run:run.run,status:response.status,error:response.error||'The request was not confirmed.'});else if(kind==='feedback'&&changeDrafts.get(run.run)===request.text){changeDrafts.set(run.run,'');if($('#change-text').dataset.run===run.run)$('#change-text').value='';}}
  catch(error){uncertainRequests.set(request.id,{...request,run:run.run,status:'uncertain',error:error.message});}
  finally{sendingRequests.delete(key);if(chosen?.run===run.run)renderLiveControls(run);await refresh();}
}
function requestRow(entry,run){const row=card('','request-row');row.append(Object.assign(n('span',({pause:'Pause',stop:'Stop'}[entry.kind]||'Change')+' · '+(entry.status==='delivered'?'delivered to legacy checkpoint':entry.status)),{className:'badge '+(['failed','uncertain'].includes(entry.status)?'failed':entry.status==='queued'||entry.status?.startsWith('submitting')?'attention':['applied','resumed','delivered'].includes(entry.status)?'complete':'')}));if(entry.text)row.append(n('p',entry.text));if(entry.error)row.append(Object.assign(n('p',entry.error),{className:'error'}));row.append(disclosure('Receipt','receipt:'+entry.id,[n('code',entry.id)],run.run));if(entry.status==='uncertain')row.append(n('p','Refresh status and reconcile this request ID before retrying to avoid a duplicate mutation.'));else if(entry.status==='failed'&&!entry.legacy&&entry.kind)row.append(button('Retry same request',()=>sendChange(run,entry.kind,entry)));return row;}
function isReadOnlyChatText(text){
  const words=String(text||'').trim().toLowerCase().replace(/[.!?]+$/,'');
  return /^(?:please )?(?:stop|pause|resume|continue)(?: (?:now|the task|after (?:this|the current) step))?$/.test(words)||String(text||'').includes('?')||/^(?:how|why|what|when|where|is|are|did|does|can|could|would)\b/.test(words)||['status','status update','progress','update'].includes(words);
}
async function sendTaskChat(run,retry) {
  const current=latestRun?.run===run.run?latestRun:run;
  if(taskReadError||taskArchiveBlocked(run.run)||current.task_archived||projectBlocked(run.workspace)||current.project_removed||taskChatPending.has(run.run)||current.status==='TASK_COMPLETE'||current.interventions?.mode==='unavailable')return;
  const questions=statusInfo(run).label==='Answer needed'?run.questions||[]:[],operational=operationalRequest(run),question_id=questions.length?$('#question-target').value||String(questions[0].id):null;
  let request=retry;
  if(!request){const text=$('#change-text').value.trim();if(!text)return;const prior=readSavedRequest('task-request:'+run.run),passive=isReadOnlyChatText(text),payload={text,question_id:passive?null:question_id,...(!passive&&operational?{resolver_response:'provide_information'}:{}),...(!passive&&(question_id||operational)?resolverResponseFields(run):{})},saved=prior?.payload?.text===text?prior:savedRequest('task-request:'+run.run,payload);request={...saved.payload,request_id:saved.id};}
  if(!request.text&&!request.delegate)return;
  const responding=!!(request.question_id||request.delegate||request.resolver_response||request.resolver_request||request.resolver_token);
  if(responding&&taskActionBusy(current))return;
  taskChatPending.set(run.run,request);taskChatErrors.delete(run.run);seq++;renderLiveControls(run);
  try {
    const scopes=request.resolver_response?['operational_exhaustion','blocker']:['clarification','permission','goal_change'];
    if(responding&&(!resolverReplyCurrent(current,request,scopes)
        ||(request.resolver_response&&(request.resolver_response!=='provide_information'||request.delegate||request.question_id))
        ||(!request.resolver_response&&(!(current.questions||[]).some(question=>String(question.id)===String(request.question_id))||statusInfo(current).label!=='Answer needed'))))throw Error('This saved reply belongs to an outdated Resolver request. Review the current request and edit your draft before sending a new reply.');
    if(!responding&&!isReadOnlyChatText(request.text)&&(operationalRequest(current)||statusInfo(current).label==='Answer needed'))throw Error('A new Resolver request is pending. Review it before sending this saved message.');
    const text=request.submitted_text??request.text;
    const payload=request.resolver_response?{workspace:run.workspace,run:run.run,action:'resolver_response',resolver_request:request.resolver_request,resolver_token:request.resolver_token,resolver_response:request.resolver_response,resolver_message:text}:{...chatPayload(current,text,request.question_id,request.request_id,request.delegate),...(request.retry?{retry:true}:{}),...(request.explicit_answer?{explicit_answer:true}:{}),...(request.decision?{decision:request.decision,decision_token:request.decision_token}:{})};
    const response=await api(request.resolver_response?'/api/action':'/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(['error','failed','uncertain','launch_failed'].includes(response.status))taskChatErrors.set(run.run,{...request,error:response.error||'This message needs attention.'});
    else{persist('task-scroll:'+run.run,'');if(!request.delegate&&!request.explicit_answer){persist('task-request:'+run.run,'');if($('#change-text').dataset.run===run.run&&$('#change-text').value.trim()===request.text){$('#change-text').value='';changeDrafts.set(run.run,'');persist('task-draft:'+run.run,'');}}}
    return response;
  } catch(error) {taskChatErrors.set(run.run,{...request,error:error.message});}
  finally {taskChatPending.delete(run.run);if(chosen?.run===run.run)renderLiveControls(latestRun||run);refresh();}
}
function renderLiveControls(run){
  if(run.task_archived||taskArchiveBlocked(run.run)){$('#live-controls').hidden=true;for(const id of ['continue','continue-run','pause-run','stop-run'])$('#'+id).disabled=true;return;}
  if(run.project_removed||projectBlocked(run.workspace)){$('#live-controls').hidden=true;for(const id of ['continue','continue-run','pause-run','stop-run'])$('#'+id).disabled=true;return;}
  const data=run.interventions||{},input=$('#change-text');
  // The composer is part of the always-visible conversation column; archived
  // and removed runs are the only states that hide it (handled above).
  $('#live-controls').hidden=false;
  if(input.dataset.run!==run.run){input.dataset.run=run.run;input.value=changeDrafts.get(run.run)??stored('task-draft:'+run.run);}
  const pendingRequest=readSavedRequest('task-request:'+run.run),confirmed=(run.chat_messages||[]).find(message=>message.id===pendingRequest?.id&&message.status!=='error');
  if(confirmed&&!taskChatPending.has(run.run)){if(input.value.trim()===pendingRequest.payload?.text){input.value='';changeDrafts.set(run.run,'');persist('task-draft:'+run.run,'');}persist('task-request:'+run.run,'');}
  const questions=statusInfo(run).label==='Answer needed'?run.questions||[]:[],operational=operationalRequest(run),target=$('#question-target'),selected=target.value,signature=JSON.stringify(questions.map(question=>[question.id,question.question]));
  if(target.dataset.options!==signature){target.replaceChildren();for(const question of questions)target.append(Object.assign(n('option',question.question||question.id),{value:String(question.id)}));target.value=questions.some(question=>String(question.id)===selected)?selected:String(questions[0]?.id||'');target.dataset.options=signature;}
  target.hidden=questions.length<2;$('#question-target-label').hidden=target.hidden;$('#question-target-label').textContent=questions.length>1?'Choose the first unresolved question ('+questions.length+' total)':'Your answer';
  target.onchange=event=>document.querySelectorAll('[data-question-card]').forEach(element=>{const selected=questions.length?element.dataset.questionCard===target.value:operational;element.hidden=!selected;element.classList.toggle('selected-question',selected);if(selected&&questions.length){element.querySelector('.speaker').textContent='Resolver · Question '+(questions.findIndex(question=>String(question.id)===target.value)+1)+' of '+questions.length;if(event)element.scrollIntoView({block:'nearest'});}});target.onchange();
  $('#change-label').textContent=operational?'Information for Resolver':questions.length?'Your answer':'Message your team';
  input.placeholder=operational?'Provide corrective information; the task will stay paused':questions.length?'Answer the question above…':'Message the task…';
  const unavailable=data.mode==='unavailable',needsRecovery=!!interruptedAttempt(run),busy=taskActionBusy(run),sending=taskChatPending.has(run.run);
  const sendBlocked=!!taskReadError||run.status==='TASK_COMPLETE'||unavailable||sending||((questions.length>0||operational)&&busy);
  $('#send-change').disabled=sendBlocked||!input.value.trim();$('#send-change').textContent=sending?'Sending…':operational?'Send to Resolver':questions.length?'Send answer ↑':'Send ↑';
  $('#send-change').title='Enter to send · Shift + Enter for a new line';
  input.oninput=()=>{changeDrafts.set(input.dataset.run,input.value);persist('task-draft:'+input.dataset.run,input.value);$('#send-change').disabled=sendBlocked||!!taskReadError||!input.value.trim();};
  requestAnimationFrame(()=>resizeComposer(input));
  $('#change-form').onsubmit=event=>{event.preventDefault();sendTaskChat(run);};renderPrimaryAction(run);renderTaskAttention(run);
  $('#task-delivery').textContent=sending?'Saving your message…':operational?'Information is saved for Resolver. The task stays paused.':questions.length?'Planning continues only if the controller returns to Running with no pending request.':run.status==='TASK_COMPLETE'?(run.completion_current===false?'Completion was recorded earlier. Inspect current checks before starting more work in a new conversation.':'This task is complete. Start a new conversation for more work.'):'Questions use saved status. Plan changes need confirmation before being applied.';
  $('#live-mode').textContent=operational?'This does not approve goals, change permissions, reset budgets, or continue execution.':questions.length?'This does not approve a plan or start implementation.':unavailable?'Live controls are unavailable.':run.status==='TASK_COMPLETE'&&run.completion_current===false?'Saved messages are retained while you inspect current checks.':statusInfo(run).group==='stopped'&&!busy?'Messages stay saved while this task is paused.':'';
  $('#live-error').textContent=data.error||'';
  const host=$('#change-history');host.replaceChildren();const entries=data.entries||data.requests||[],chatIds=new Set((run.chat_messages||[]).map(entry=>entry.id));
  for(const entry of entries)if(!chatIds.has(entry.id)&&!['applied','resumed','delivered'].includes(entry.status))host.append(requestRow(entry,run));
  for(const entry of uncertainRequests.values())if(entry.run===run.run&&!entries.some(item=>item.id===entry.id))host.append(requestRow(entry,run));
  for(const entry of sendingRequests.values())if(sendingRequests.get(requestKey(run.run,entry.kind))===entry)host.append(requestRow({...entry,status:'submitting · not yet confirmed durable'},run));
  const error=taskChatErrors.get(run.run);if(error&&!chatIds.has(error.request_id)){const row=card('','conversation-problem');row.append(Object.assign(n('p',error.error),{className:'error'}),button('Retry same message',()=>sendTaskChat(run,{...error,retry:true})));host.append(row);}
  if(sending)host.append(Object.assign(n('p','Saving your message…'),{className:'field-note'}));
  if(data.pause_intent&&!data.pause_intent.acknowledged_at&&statusInfo(run).group==='running')host.append(Object.assign(n('p','Pause requested. The current step will finish first.'),{className:'field-note'}));
  if(data.stop_intent)host.append(Object.assign(n('p','Stopped after the current step finished and saved. Work, history and receipts are kept; no further stages run for this conversation.'),{className:'field-note'}));
  else if((data.entries||[]).some(entry=>entry.kind==='stop'&&['queued','requested'].includes(entry.status))&&statusInfo(run).group==='running')host.append(Object.assign(n('p','Stop requested. The current step will finish first; no further stages will run after it.'),{className:'field-note'}));
}
function disableStaleControls(){
  document.querySelectorAll('#task-detail button, #task-detail input, #task-detail textarea, #task-detail select').forEach(control=>{
    const safe=control.dataset.tab!==undefined||control.dataset.staleSafe!==undefined||['task-back','retry-task','retry-stale-status'].includes(control.id);if(safe)return;
    if(control.dataset.staleDisabled===undefined){control.dataset.staleDisabled=String(control.disabled);control.dataset.staleDescription=control.getAttribute?.('aria-describedby')||'';}
    const descriptions=[control.dataset.staleDescription,'stale-mutation-reason'].filter(Boolean).join(' ');control.setAttribute?.('aria-describedby',descriptions);control.disabled=true;
  });
}
function unavailableRun(message){
  taskReadError=message;$('#sync-state').textContent='Task status unavailable';$('#task-status').replaceChildren(Object.assign(n('span','Status unverified'),{className:'badge failed'}));
  $('#task-load-notice').hidden=false;$('#task-load-message').textContent=message+(taskReadAt?' Showing the last successful read from '+new Date(taskReadAt).toLocaleTimeString()+'.':' No current task data is available.')+' Controls are disabled until a successful refresh.';
  const stale=$('#task-stale-state');if(stale){stale.hidden=false;const retry=$('#retry-stale-status');retry.disabled=false;retry.onclick=()=>refresh();}
  // openRun clears latestRun on task changes. The server may canonicalize a
  // symlinked workspace path; textual URL equality is not a freshness test.
  if(!latestRun){$('#now').replaceChildren(n('p','Task status has not loaded. Retry to read the saved checkpoint.'));}
  markMonitorStale();disableStaleControls();$('#continue').disabled=true;$('#live-controls').hidden=true;
}
function showRemovedRun(run) {
  stopPreview();evidenceRequest++;evidenceRun='';
  $('#archive-task').disabled=true;$('#delete-task').disabled=true;
  $('#task-overview').hidden=true;
  latestRun=run;$('#task-title').textContent='Project removed';$('#task-project').textContent=projectLabel(run.workspace);$('#task-project').title=run.workspace;$('#task-subtitle').textContent='This task is hidden until you restore its project.';$('#task-models').textContent='';$('#task-model-settings').replaceChildren();$('#task-status').replaceChildren();$('#task-progress-summary').hidden=true;
  for(const id of ['conversation','brief-current','goal','plan-full','execution_view','actions','task-attention'])$('#'+id).replaceChildren();
  $('#task-attention').hidden=true;$('#detail-grid').hidden=true;$('#live-controls').hidden=true;for(const id of ['continue','continue-run','pause-run','stop-run'])$('#'+id).disabled=true;
  const host=$('#task-removed-banner');host.replaceChildren(n('h2','Restore '+projectTitle(run.workspace)+' to view this task'),n('p','Its files and history stay on disk. Running tasks keep going.'),Object.assign(n('p',run.workspace),{className:'project-path'}),projectActionButton(run.workspace,'restore','Restore project'));host.hidden=false;renderTaskProjectActions(run.workspace,true);
}
function showArchivedRun(run){
  stopPreview();evidenceRequest++;evidenceRun='';
  latestRun=run;$('#archive-task').disabled=true;$('#delete-task').disabled=true;
  $('#task-title').textContent='Task archived';$('#task-project').textContent=projectLabel(run.workspace);$('#task-project').title=run.workspace;
  $('#task-subtitle').textContent='Restore this task to view its history and controls.';$('#task-models').textContent='';$('#task-model-settings').replaceChildren();$('#task-status').replaceChildren();$('#task-progress-summary').hidden=true;
  for(const id of ['task-overview','task-attention','detail-grid','live-controls'])$('#'+id).hidden=true;
  for(const id of ['continue','continue-run','pause-run','stop-run'])$('#'+id).disabled=true;
  const host=$('#task-removed-banner');host.replaceChildren(n('h2','This task is archived'),n('p','Its files and history are preserved. Archiving does not stop a running worker.'),archiveButton(run,'restore','Restore task'));host.hidden=false;
  renderTaskProjectActions(run.workspace);
}
function showRun(run,focus){
  taskReadError='';taskReadAt=Date.now();$('#task-load-notice').hidden=true;$('#sync-state').replaceChildren(Object.assign(n('img'),{src:'/static/connected.svg',alt:'',width:6,height:6}),document.createTextNode('Task checked just now'));
  $('#task-stale-state').hidden=true;
  document.querySelectorAll('[data-stale-disabled]').forEach(control=>{control.disabled=control.dataset.staleDisabled==='true';if(control.dataset.staleDescription)control.setAttribute?.('aria-describedby',control.dataset.staleDescription);else control.removeAttribute?.('aria-describedby');delete control.dataset.staleDisabled;delete control.dataset.staleDescription;});
  if(run.task_archived){showArchivedRun(run);restoreFocus(focus);return;}
  if(run.project_removed||removedProject(run.workspace)){showRemovedRun(run);restoreFocus(focus);return;}
  $('#task-removed-banner').hidden=true;$('#detail-grid').hidden=false;renderTaskProjectActions(run.workspace);
  latestRun=run;$('#continue').disabled=false;if(typeof settleApprovalReceipt==='function')settleApprovalReceipt(run);
  $('#archive-task').disabled=archivePending.has(run.run);$('#archive-task').onclick=()=>reviewTaskArchive(run);$('#delete-task').disabled=false;$('#delete-task').onclick=()=>reviewTaskDelete(run);
  renderTaskOverview(run);
  $('#task-project').textContent=projectLabel(run.workspace);$('#task-project').title=run.workspace;
  const task=String(run.task||'Untitled task');$('#task-title').textContent=taskTitle(run);$('#task-title').title=task;
  $('#task-subtitle').textContent=taskSentence(run,taskActionBusy(run));$('#task-status').replaceChildren(typeof taskStatusBadge==='function'?taskStatusBadge(run):badge(run));
  $('#task-objective').textContent=taskPosition(run);
  const settings=run.model_settings||{},roles=settings.roles||{},roleNames=Object.keys(roles);
  const models=$('#task-models'),efforts=settings.role_efforts||{};models.replaceChildren();for(const role of roleNames){const item=n('p',''),detail=[roles[role]||'Model not recorded',efforts[role]?efforts[role]+' reasoning':null,settings.role_engines?.[role]||settings.engine||'Saved provider'].filter(Boolean).join(' · ');item.append(n('strong',roleDisplayName(role)),n('span',detail));models.append(item);}
  if(typeof renderTaskModelSettings==='function')renderTaskModelSettings(run);renderTaskReasoning(run);renderConversation(run);renderBrief(run);renderAstraPlan(run);renderPlanRail(run);renderInlineTaskAction(run);renderExecution(run);renderLiveControls(run);renderTaskNow(run);renderThreadCheckpoint(run);activateTab(currentTab);settleThreadScroll();restoreFocus(focus);
}
function renderThreadCheckpoint(run){
  const host=$('#thread-checkpoint');host.replaceChildren();host.className='thread-checkpoint';
  const info=statusInfo(run),assignment=run.astra_plan?.current_assignment,activity=run.monitor?.activity||[];
  if(info.group==='running'){host.append(n('strong','Latest task update'),n('p',assignment?.objective||run.monitor?.objective||'Work is in progress.'));if(activity.length)host.append(Object.assign(n('p',activity.slice(-2).map(entry=>entry.label).join(' · ')),{className:'field-note'}));}
  else if(info.label==='Approve plan')return void(host.hidden=true);
  else if(info.group==='complete')host.append(n('strong','Work completed'),n('p',(run.counts?.pass||0)+' acceptance checks reported passing. Open Changes and Checks to review the result.'));
  else if(run.goal?.approval_status==='approved'&&info.group==='stopped')host.append(n('strong','Plan revision '+(run.goal.revision||1)+' approved'),n('p','Your approved scope is in Current plan. The current assignment and next action are in Now.'));
  host.hidden=!host.childElementCount;
}
function expireNotices(){
  const expired=(value,id)=>value&&!value.error&&value.expiresAt<Date.now()&&!$(id).contains(document.activeElement);
  if(expired(archiveNotice,'#archive-notice')){archiveNotice=null;renderArchiveNotice();}
  if(expired(projectNoticeState,'#project-notice')){projectNoticeState=null;renderProjectNotice();}
  if(expired(conversationArchiveNotice,'#conversation-archive-notice')){conversationArchiveNotice=null;renderConversationArchiveNotice();}
}
async function refreshOnce(){
  const mine=seq,selected=currentView==='task-detail'?chosen:null,conversationId=currentView==='draft-conversation'?activeConversation:null;
  // Discovery is independent of the selected read: its latency and failures
  // must never prevent a fresh task response or invalidate one already shown.
  const listRead=(async()=>{
    try{
      const data=await api('/api/runs');if(mine!==seq)return;latestData=data;setup(data);if(restorePendingShortRun(data)||restorePendingShortProject(data))return;const focus=captureControls();renderTasks(data);renderConversations(data);expireNotices();restoreFocus(focus);
      if(!selected)$('#sync-state').replaceChildren(Object.assign(n('img'),{src:'/static/connected.svg',alt:'',width:6,height:6}),document.createTextNode('Task list checked just now'));
    }catch(error){if(mine!==seq)return;if(!selected)$('#sync-state').textContent='Task list unavailable';dashboardNotice(actionProblem||error.message);}
  })();
  const detailRead=(async()=>{
    if(conversationId){try{const doc=await api('/api/conversation?id='+encodeURIComponent(conversationId));if(mine!==seq||activeConversation!==conversationId||currentView!=='draft-conversation')return;renderDraftConversation(doc);}catch(error){if(mine===seq&&activeConversation===conversationId&&currentView==='draft-conversation'){latestConversation=null;$('#attach-submit').disabled=true;$('#draft-problem').replaceChildren(Object.assign(n('p',error.message),{className:'error'}));$('#draft-problem').hidden=false;$('#draft-send').disabled=true;}}}
    if(selected){try{const run=await api('/api/run?workspace='+encodeURIComponent(selected.workspace)+'&run='+encodeURIComponent(selected.run));if(mine!==seq||chosen?.run!==selected.run||currentView!=='task-detail')return;if(run.state_error)unavailableRun('Selected run unavailable: '+run.state_error);else showRun(run,captureControls());}catch(error){if(mine===seq&&chosen?.run===selected.run&&currentView==='task-detail')unavailableRun('Selected run unavailable: '+error.message);}}
  })();
  await Promise.all([listRead,detailRead]);
}
async function refresh(){
  if(refreshPromise){refreshAgain=true;return refreshPromise;}
  clearTimeout(refreshTimer);
  refreshPromise=(async()=>{do{refreshAgain=false;await refreshOnce();}while(refreshAgain);})();
  try{await refreshPromise;}finally{refreshPromise=null;refreshTimer=setTimeout(refresh,2000);}
}
function restoreSelection() {
  const selection=location.hash.slice(1)||stored('selection','tasks'),params=new URLSearchParams(selection);
  if(params.get('focus')==='1')persist('focus','1');
  if(params.get('conversation'))openConversation(params.get('conversation'));
  else if(params.get('run')&&!params.get('task')){
    const key=params.get('run'),matches=matchingShortRuns(key);
    if(matches.length===1){pendingShortRun='';openRun(matches[0]);}
    // Keep the compact hash intact until the first local registry snapshot
    // arrives; setView('tasks') would overwrite it with #tasks.
    else if(!latestData){pendingShortRun=key;}
    else unavailableShortRun();
  }
  else if(params.get('task')&&params.get('run'))openRun({workspace:params.get('task'),run:params.get('run')});
  else if(selection==='tasks'||selection.startsWith('tasks&')){
    const filter=params.get('filter')||'all',validFilter=['all',...taskGroups().map(group=>group[0])].includes(filter)?filter:'all';
    const project=params.get('project')||'',compact=/^workspace-[a-f0-9]{24}$/.test(project);
    if(compact){const path=projectPathForKey(project);if(path)filterTasks(validFilter,path);else if(!latestData)pendingShortProject={key:project,filter:validFilter};else unavailableShortProject();}
    else filterTasks(validFilter,project);
  }
  else setView(['tasks','conversations','settings','archived'].includes(selection)?selection:selection==='new'?'new-task':'tasks');
}
$('#draft-plan-close').onclick=()=>$('#draft-plan-dialog').close();
bindDialogBehavior($('#draft-plan-dialog'),()=>$('#draft-plan-dialog').close());
if(typeof ResizeObserver!=='undefined'){const headerObserver=new ResizeObserver(entries=>{const height=Math.ceil(entries[0].target.getBoundingClientRect().height);document.documentElement.style.setProperty('--dashboard-header-height',height+'px');});headerObserver.observe($('.topbar'));}
window.addEventListener('hashchange',restoreSelection);
function applyTheme(theme){document.documentElement.dataset.theme=theme;$('#theme-toggle').textContent=theme==='dark'?'Light theme':'Dark theme';$('#theme-toggle').setAttribute('aria-pressed',String(theme==='dark'));}
applyTheme(stored('theme','light'));
$('#theme-toggle').onclick=()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';persist('theme',theme);applyTheme(theme);};
$('#focus-toggle').onclick=()=>{const focus=stored('focus')!=='1';persist('focus',focus?'1':'0');document.body.classList.toggle('monitor-focus',focus);$('#focus-toggle').textContent=focus?'Show navigation':'Focus view';$('#focus-toggle').setAttribute('aria-pressed',String(focus));};
$('#focus-toggle').textContent=stored('focus')==='1'?'Show navigation':'Focus view';
$('#retry-task').onclick=()=>refresh();
$('#retry-stale-status').onclick=()=>refresh();
restoreSelection();
refresh();
