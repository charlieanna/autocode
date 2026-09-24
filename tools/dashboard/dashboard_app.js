/* Local dashboard: render saved runner data; only explicit actions advance work. */
let chosen = null, seq = 0, init = false, focusVersion = 0;
let currentView = 'inbox', currentTab = 'now', taskFilter = 'all', projectFilter = '', searchText = '';
let evidenceRequest = 0, evidenceRun = '', previewRun = '', scrollThreadToEnd = false;
let taskReturn = {view:'inbox',filter:'all',project:''};
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
  setView('inbox');
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
function newRequestId() { return crypto.randomUUID(); }
function readSavedRequest(key) {try{return JSON.parse(stored(key,'null'));}catch{return null;}}
function savedRequest(key,payload) {const previous=readSavedRequest(key),signature=JSON.stringify(payload);if(previous?.signature===signature)return previous;const request={id:newRequestId(),signature,payload};persist(key,JSON.stringify(request));return request;}
function rememberSelection(value) { persist('selection',value); history.replaceState(null,'','#'+value); }
function conversationStatus(doc) { return doc.attachment?.status==='linked'?'Attached':doc.attachment?.status==='starting'?'Connecting project':doc.status==='thinking'?'Thinking…':doc.status==='error'?'Needs attention':'Planning'; }
function conversationPayload(text, models, request_id) { return {text:text.trim(),models,request_id}; }
function chatPayload(run,text,question_id,request_id,delegate=false) { return {workspace:run.workspace,run:run.run,text,request_id,...(question_id?{question_id}:{}),...(delegate?{delegate:true}:{})}; }
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
  target.focus();
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
  const response = await fetch(url, request), data = await response.json();
  if (!response.ok) throw Error(data.error || 'The request could not be completed.');
  return data;
}
function dashboardNotice(message) { const host = $('#dashboard-notice'); host.textContent = message || ''; host.hidden = !message; }
function archivedTask(run){return !!run&&(latestData?.archived_tasks||[]).some(row=>row.run===run);}
function taskArchiveBlocked(run){return archivedTask(run)||archivePending.has(run);}
function archiveButton(run,action,label){const control=focusKey(button(label,()=>changeTaskArchive(run,action)),'archive:'+action+':'+run.run);control.disabled=archivePending.has(run.run);return control;}
function renderArchiveNotice(){const host=$('#archive-notice');host.replaceChildren();host.hidden=!archiveNotice;if(!archiveNotice)return;const {run,action,error}=archiveNotice;host.append(n('p',error||'Task '+(action==='archive'?'archived.':'restored.')));if(error)host.append(archiveButton(run,action,'Retry'));else if(action==='archive')host.append(archiveButton(run,'restore','Undo'));host.append(button('Archived tasks',()=>setView('archived')),button('Dismiss',()=>{archiveNotice=null;renderArchiveNotice();},'text-button'));}
function renderArchivedTasks(data){const host=$('#archived-task-list');host.replaceChildren();const entries=data.archived_tasks||[];$('#archive-count').textContent=entries.length;for(const run of entries){const row=card('','managed-project'),copy=card('','managed-project-copy');copy.append(n('h3',taskTitle(run)),n('p',basename(run.workspace)),Object.assign(n('p',basename(run.run)),{className:'project-path'}));row.append(copy,archiveButton(run,'restore','Restore task'));host.append(row);}if(!entries.length)host.append(n('p','No archived tasks.'));}
function reviewTaskArchive(run){if(!run?.run||taskArchiveBlocked(run.run))return;archiveReview={...run,opener:document.activeElement};$('#task-archive-name').textContent=run.task||basename(run.run);$('#task-archive-project').textContent=basename(run.workspace)+' · '+basename(run.run);$('#task-archive-error').hidden=true;$('#task-archive-dialog').showModal();$('#task-archive-cancel').focus();}
function closeTaskArchive(){if(archiveReview&&archivePending.has(archiveReview.run))return;const opener=archiveReview?.opener;archiveReview=null;$('#task-archive-dialog').close();if(opener?.isConnected)opener.focus();}
$('#task-archive-cancel').onclick=closeTaskArchive;
$('#task-archive-dialog').addEventListener('cancel',event=>{event.preventDefault();closeTaskArchive();});
$('#task-archive-confirm').onclick=()=>{if(archiveReview)changeTaskArchive(archiveReview,'archive');};
async function changeTaskArchive(run,action){
  if(archivePending.has(run.run))return;archivePending.add(run.run);seq++;
  const dialog=action==='archive'&&archiveReview?.run===run.run;
  if(dialog){$('#task-archive-confirm').disabled=true;$('#task-archive-cancel').disabled=true;$('#task-archive-error').hidden=true;}
  try{await api('/api/tasks',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace:run.workspace,run:run.run,action})});seq++;
    if(latestData){latestData.archived_tasks=(latestData.archived_tasks||[]).filter(row=>row.run!==run.run);if(action==='archive'){latestData.archived_tasks.push({workspace:run.workspace,run:run.run,task:run.task});latestData.runs=(latestData.runs||[]).filter(row=>row.run!==run.run);latestData.conversations=(latestData.conversations||[]).filter(doc=>doc.attachment?.run!==run.run);}}
    if(action==='archive'&&chosen?.run===run.run){chosen=null;latestRun=null;$('#archive-task').disabled=true;filterTasks('all',run.workspace);}
    if(action==='archive'&&latestConversation?.attachment?.run===run.run){activeConversation=null;latestConversation=null;setView('conversations');}
    archiveNotice={run:{workspace:run.workspace,run:run.run,task:run.task},action,expiresAt:Date.now()+12000};if(dialog){archiveReview=null;$('#task-archive-dialog').close();}
  }catch(error){if(dialog){$('#task-archive-error').textContent=error.message;$('#task-archive-error').hidden=false;}else archiveNotice={run,action,error:error.message};}
  finally{archivePending.delete(run.run);seq++;$('#task-archive-confirm').disabled=false;$('#task-archive-cancel').disabled=false;if(latestData){renderTasks(latestData);renderConversations(latestData);renderArchivedTasks(latestData);}renderArchiveNotice();if(dialog&&!archiveReview){$('#archive-notice').tabIndex=-1;$('#archive-notice').focus();}await refresh();}
}
function removedProject(workspace, data=latestData) { return !!workspace && (data?.removed_projects||[]).some(project=>project.workspace===workspace); }
function projectBlocked(workspace) { return removedProject(workspace)||projectPending.get(workspace)==='remove'; }
function dashboardProjects(data) { return [...new Set([...(data.workspaces||[]),...(data.runs||[]).map(run=>run.workspace).filter(Boolean)])].filter(workspace=>!removedProject(workspace,data)); }
function projectActionButton(workspace, action, label) {
  const control=focusKey(button(label,()=>action==='remove'?reviewProjectRemoval(workspace):changeProject(workspace,'restore'),action==='remove'?'remove-project-button':''),'project-'+action+':'+workspace);
  control.disabled=projectPending.has(workspace);control.setAttribute('aria-label',label+' '+basename(workspace));return control;
}
function renderProjectNotice() {
  const host=$('#project-notice');host.replaceChildren();host.hidden=!projectNoticeState;if(!projectNoticeState)return;
  const {workspace,action,error}=projectNoticeState,copy=card('','project-notice-copy');
  copy.append(n('p',error||basename(workspace)+(action==='remove'?' removed from this dashboard.':' restored to this dashboard.')),Object.assign(n('p',workspace),{className:'project-path'}));host.append(copy);host.classList.toggle('error',!!error);
  if(error)host.append(projectActionButton(workspace,action,'Retry '+(action==='remove'?'removal':'restore')));
  else if(action==='remove')host.append(projectActionButton(workspace,'restore','Undo'));
  host.append(button('Manage projects',()=>setView('settings')),button('Dismiss',()=>{projectNoticeState=null;renderProjectNotice();},'text-button'));
}
function renderProjectManager(data) {
  const visible=$('#managed-projects'),removed=$('#removed-projects');visible.replaceChildren();removed.replaceChildren();
  for(const [host,entries,action] of [[visible,dashboardProjects(data).map(workspace=>({workspace})),'remove'],[removed,data.removed_projects||[],'restore']]) {
    for(const project of entries){const row=card('','managed-project'),copy=card('','managed-project-copy');copy.append(n('h3',basename(project.workspace)),Object.assign(n('p',project.workspace),{className:'project-path'}));
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
  host.append(Object.assign(n('p',workspace),{className:'project-path'}));if(!removed&&!removedProject(workspace))host.append(projectActionButton(workspace,'remove','Remove project'));
}
function reviewProjectRemoval(workspace) {
  if(!workspace||projectPending.has(workspace))return;
  projectRemoval={workspace,opener:document.activeElement};$('#project-removal-name').textContent=basename(workspace);$('#project-removal-path').textContent=workspace;
  $('#project-removal-error').textContent='';$('#project-removal-error').hidden=true;$('#project-removal-confirm').disabled=false;$('#project-removal-confirm').textContent='Remove project';$('#project-removal-cancel').disabled=false;
  $('#project-removal-dialog').showModal();$('#project-removal-cancel').focus();
}
function closeProjectRemoval() {
  if(projectRemoval&&projectPending.has(projectRemoval.workspace))return;
  const opener=projectRemoval?.opener;projectRemoval=null;$('#project-removal-dialog').close();if(opener?.isConnected)opener.focus();
}
$('#project-removal-cancel').onclick=closeProjectRemoval;
$('#project-removal-dialog').addEventListener('cancel',event=>{event.preventDefault();closeProjectRemoval();});
$('#project-removal-confirm').onclick=()=>{if(projectRemoval)changeProject(projectRemoval.workspace,'remove');};
async function changeProject(workspace,action) {
  if(projectPending.has(workspace))return;projectPending.set(workspace,action);seq++;
  const dialog=action==='remove'&&projectRemoval?.workspace===workspace;
  if(dialog){$('#project-removal-confirm').disabled=true;$('#project-removal-confirm').textContent='Removing…';$('#project-removal-cancel').disabled=true;$('#project-removal-error').hidden=true;}
  if(latestData){renderProjectManager(latestData);renderProjectScope();}renderProjectNotice();if(chosen?.workspace===workspace){renderTaskProjectActions(workspace);if(latestRun)renderLiveControls(latestRun);}
  try {
    await api('/api/projects',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace,action})});seq++;
    if(latestData){latestData={...latestData,removed_projects:(latestData.removed_projects||[]).filter(project=>project.workspace!==workspace)};if(action==='remove'){latestData.removed_projects.push({workspace,removed_at:new Date().toISOString()});latestData.workspaces=(latestData.workspaces||[]).filter(path=>path!==workspace);latestData.runs=(latestData.runs||[]).filter(run=>run.workspace!==workspace);latestData.conversations=(latestData.conversations||[]).filter(doc=>doc.attachment?.workspace!==workspace);}}
    if(action==='remove'){
      const selectedTask=chosen?.workspace===workspace,selectedChat=latestConversation?.attachment?.workspace===workspace;
      const leave=(currentView==='task-detail'&&selectedTask)||(currentView==='draft-conversation'&&selectedChat)||(currentView==='tasks'&&projectFilter===workspace);
      if(selectedTask){chosen=null;latestRun=null;}if(selectedChat){activeConversation=null;latestConversation=null;}if(projectFilter===workspace)projectFilter='';if(leave)filterTasks('all','');
    }
    projectNoticeState={workspace,action,expiresAt:Date.now()+12000};if(dialog){projectRemoval=null;$('#project-removal-dialog').close();}
  } catch(error) {
    if(dialog){$('#project-removal-error').textContent=error.message;$('#project-removal-error').hidden=false;}
    else projectNoticeState={workspace,action,error:error.message};
  } finally {
    projectPending.delete(workspace);seq++;if(dialog){$('#project-removal-confirm').disabled=false;$('#project-removal-confirm').textContent='Remove project';$('#project-removal-cancel').disabled=false;}
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
function concise(text, limit=180) {
  const value=String(text||'').replace(/\s+/g,' ').trim();
  return value.length>limit?value.slice(0,limit-1).replace(/\s+\S*$/,'')+'…':value;
}
function taskTitle(run) {
  const outcome=!String(run.goal?.origin||'').startsWith('migration_draft')&&run.goal?.body?.intended_outcome;
  const raw=String(outcome||run.task||'Untitled task').split('Conversation reference:')[0].replace(/\s+/g,' ').trim();
  const first=raw.split(/(?<=[.!?])\s+(?=[A-Z])/)[0];
  return concise(first,86);
}
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
function statusInfo(run) {
  const status=run.status||'', questions=run.questions||[], request=run.user_request||{};
  const info=(group,tone,label,action,reason,tab='interview')=>({group,tone,label,action,reason,tab,
    stateLabel:group==='running'?'Worker confirmed running':group==='attention'?'Waiting for your decision':group==='complete'?'Complete':group==='stopped'?(tone==='failed'?'Internally blocked':'Stopped at a checkpoint'):label});
  if(run.error||run.state_error)return info('stopped','failed','Unavailable','Inspect issue',run.error||run.state_error);
  if(status==='TASK_COMPLETE')return info('complete','complete','Completed','View result','The runner recorded this task as complete. No reply is needed.','execution');
  if(status==='DRY_RUN')return info('other','','Preview only','View preview','This is a saved dry run. It did not start implementation.','execution');
  if(['PAUSED_PROVIDER_UNCERTAIN','PAUSED_UNCERTAIN_STAGE'].includes(status))return info('stopped','failed','Interrupted','Review interruption',run.stop_reason||'A provider attempt ended without a confirmed result. Review saved work before continuing.');
  // Saved questions and user requests may remain after a stage resumes. Honor
  // the current paused/running state before interpreting these old fields.
  if(status==='RUNNING') {
    const stage=run.active_stage||{};
    if(run.monitor?.live?.state==='alive')return info('running','running','Running','View progress',stageName(run)+' · Worker verified alive');
    if(stage.stage&&run.monitor?.live?.state==='exited')return info('stopped','attention','Worker stopped','Review checkpoint','The saved step says running, but its worker has exited. Review the checkpoint before continuing.');
    if(stage.stage&&!stage.finished_at&&stage.exit_code==null)return info('stopped','attention','Worker unverified','Check worker status','No live worker is confirmed. '+(run.monitor?.live?.label||'Process inspection is unavailable.')+' Inspect the saved checkpoint before continuing.');
    if(run.monitor?.orchestration_batch&&run.monitor?.next_stage==='orchestrator')return info('stopped','attention','Worker unverified','Check worker status','Orchestrator · Saved batch statuses do not confirm live workers. Inspect the saved checkpoint before continuing.');
    if(run.review_token&&run.review_criteria?.length&&run.review_criteria.every(criterion=>run.human_reviews?.[criterion.id]?.token===run.review_token))return info('stopped','','Ready to finish','Finish task','Your review is saved. Finish the task to run its final completion check.','execution');
    return info('stopped','','Ready to continue','Open to continue','The task is at a saved checkpoint. Open it to continue when you are ready.');
  }
  if(status==='PAUSED_INVALID_OUTPUT'&&/discovery|glm_revise|astra_challenge|astra_finalize/.test(run.active_stage?.stage||run.stage||''))return info('stopped','failed','Planning needs retry','Retry planning',(run.stop_reason?run.stop_reason+' · ':'')+(String(run.stop_reason||'').includes('actual saved feedback event')?'The planning draft cited user feedback that has no matching saved feedback event. The draft was rejected. Retry planning to generate a fresh draft; final plan approval is still required.':'The planning draft failed validation and was rejected. Retry planning to generate a fresh draft; final plan approval is still required.'));
  if(status==='PAUSED_INTERVENTION')return info('stopped','attention','Paused','Resume task',String(run.stop_reason||'').includes('feedback was applied')?'Your feedback has been applied. Resume to update the plan.':'Paused at a saved step. Resume when you’re ready.');
  if(/INVALID_OUTPUT|REPORT_REPAIR|PERMISSION_RECONCILIATION|ORCHESTRATOR_|FAILED/.test(status))return info('stopped','failed','Internally blocked','Inspect failure',run.stop_reason||'An internal step failed. Inspect the saved failure, correct the cause, then retry.');
  if(status==='BLOCKED_HUMAN'&&(questions.length||request.decision_needed))return info('attention','attention','Decision needed','View decision',request.decision_needed||questions[0].question);
  if(/PAUSED|BLOCKED|FAILED/.test(status))return info('stopped',/INVALID|FAILED/.test(status)?'failed':'attention','Paused','View pause reason',run.stop_reason||'The runner stopped at a checkpoint. Review its saved state before continuing.');
  const reviews=run.review_token?(run.review_criteria||[]).filter(criterion=>run.human_reviews?.[criterion.id]?.token!==run.review_token):[];
  if(status==='WAITING_FOR_USER'&&request.kind==='human_review'&&reviews.length)return info('attention','attention','Review output','Review '+reviews.length+' '+(reviews.length===1?'item':'items'),request.decision_needed||'Inspect the saved output and record your review.','execution');
  if(status==='WAITING_FOR_USER'&&request.kind==='human_review'&&run.review_token&&run.review_criteria?.length&&!reviews.length)return info('stopped','','Ready to finish','Finish task','Your review is saved. Finish the task to run its final completion check.','execution');
  if(questions.length)return info('attention','attention','Answer needed','Answer '+questions.length+' '+(questions.length===1?'question':'questions'),questions[0].question||'Open the conversation to answer the pending question.');
  if(status==='AWAITING_GOAL_APPROVAL') {
    if(run.goal_token&&run.goal?.approval_status!=='approved'&&planReady(run))return info('attention','attention','Approve plan','Review plan',run.goal?.body?.intended_outcome||'Review the finalized plan before implementation starts.');
    return info('stopped','attention','Planning checkpoint','Open planning','The saved plan is not currently ready for approval. Inspect the planning checkpoint.');
  }
  if(status==='WAITING_FOR_USER'){
    if(request.decision_needed)return info('attention','attention','Decision needed','View request',request.decision_needed);
    return info('stopped','','Ready to continue','Resume task','No unresolved decision is recorded. Resume from the saved checkpoint.');
  }
  return info('other','',human(status)||'Unknown','Inspect task','The saved state does not identify a current action. Open the task for details.');
}
function taskGroups() {return [
  ['attention','Waiting on you','Questions, plan approval, or a review that only you can resolve.'],
  ['running','In progress','Workers confirmed running by process inspection. Saved activity does not confirm a live process.'],
  ['stopped','Paused / issues','Stopped, interrupted, unavailable, or ready to continue. These are separate from unanswered questions.'],
  ['complete','Completed','Finished tasks. No reply is needed.'],
  ['other','Other saved tasks','Previews and tasks whose next action is not known.']
];}
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
function taskScopeTitle() {return projectFilter?basename(projectFilter):taskFilter==='all'?'All work':taskGroups().find(group=>group[0]===taskFilter)?.[1]||'All work';}
function badge(run) { const info = statusInfo(run), element = n('span',info.stateLabel); element.className = 'badge '+info.tone; element.title = run.status || run.error || ''; return element; }
function jointPlanning(run) { return run.model_settings?.joint_planning === true; }
function planningMode(run) { return run.monitor?.workflow_mode==='glm_final_audit_v2'?'Builder-led · Completion owner final audit':run.monitor?.workflow_mode==='glm_first_v1'?'Builder-led · Completion owner milestone reviews':jointPlanning(run)?'Requirements planning · Independent review':run.model_settings?.engine?'Legacy planning':'Saved workflow unavailable'; }
function planningSpeaker(run) { return jointPlanning(run) && run.goal?.origin !== 'astra_finalize' ? 'Requirements planner' : 'Plan reviewer'; }
function roleDisplayName(name) {
  const key=String(name||'').trim().toLowerCase().replaceAll('_',' ');
  return ({glm:'Requirements planner',astra:'Plan reviewer',terra:'Builder',sol:'Validator',completion:'Completion owner'})[key]||human(name);
}
function planReady(run) { return !jointPlanning(run) || (run.status==='AWAITING_GOAL_APPROVAL' && run.goal?.origin==='astra_finalize'); }
function stageName(run) {
  const stage=(run.stage||'').replace(/_report_repair$/,'');
  if(stage==='orchestrator')return 'Orchestrator · Coordinating Builders';
  if(stage==='resolver')return 'Resolver · Runner decision (no model call)';
  const mode=run.monitor?.workflow_mode,glmFirst=['glm_first_v1','glm_final_audit_v2'].includes(mode);
  if(glmFirst&&/terra/.test(stage))return 'Builder · Implementing';
  if(glmFirst&&/sol/.test(stage))return 'Validator · Targeted review';
  if(glmFirst&&stage==='astra_checkpoint')return mode==='glm_final_audit_v2'?'Completion owner · Final audit':'Completion owner · Milestone review';
  if(jointPlanning(run)) {
    const labels={astra_discovery:'Requirements planner · Planning',astra_challenge:'Plan reviewer · Challenging the plan',glm_revise:'Requirements planner · Revising the plan',astra_finalize:'Plan reviewer · Finalizing the plan'};
    if(labels[stage])return labels[stage];
  }
  return /astra_discovery/.test(stage)?'Plan reviewer · Planning':stage==='astra_review'?'Completion owner · Deciding complete or rework':stage==='astra_checkpoint'?'Completion owner · Auditing results':/astra/.test(stage)?'Plan reviewer · Direction':/terra/.test(stage)?'Builder · Implementing':/sol/.test(stage)?'Validator · Reviewing':stage==='unavailable'?'Step unavailable':human(stage)||'Not started';
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
  return !stage.rejected&&!stage.interrupted&&!stage.timed_out&&(stage.exit_code===0||(stage.stage==='orchestrator'&&stage.runner_owned===true&&stage.exit_code==null&&!!stage.finished_at));
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
function workflowConfig(run, state) {
  const mode=run.monitor?.workflow_mode,finalOnly=mode==='glm_final_audit_v2',glmFirst=finalOnly||mode==='glm_first_v1';
  return glmFirst?[['terra','Builder','Plan & implement'],['sol','Validator','Targeted escalation only'],['astra','Plan reviewer',finalOnly?'Final full-task audit only':'Milestone review']]:
    [...(jointPlanning(run)?[['glm','Requirements planner','Draft & revise']]:[['astra','Requirements planner','Draft plan']]),['astra','Plan reviewer',jointPlanning(run)?'Challenge & finalize':'Review & direct'],...(hasOrchestration(run)?[['orchestrator','Orchestrator','After approval · Coordinate Builders']]:[]),['terra','Builder','Implement'],['sol','Validator','Review'],['completion','Completion owner','Complete / rework']];
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
  const config=workflowConfig(run,state);
  const host=card('','workflow-cards');host.setAttribute('aria-label','Agent workflow');
  for(const [role,name,duty]of config){const selected=state.active&&state.role===role,item=card('','workflow-card'+(selected&&state.verified?' active':''));
    item.append(Object.assign(n('p',duty),{className:'monitor-kicker'}),n('h3',name));
    const saved=run.monitor?.roles?.[role]||{},model=saved.model||run.model_settings?.roles?.[role];
    item.append(Object.assign(n('p',role==='orchestrator'?'Runner-owned · No model session':model||'Model not recorded'),{className:'workflow-model'}));
    if(saved.reasoning_effort)item.append(Object.assign(n('p',saved.reasoning_effort+' reasoning'),{className:'workflow-model'}));
    if(role==='glm'||role==='astra'){
      const completed=completedPlanningStep(run,role);
      const label=completed?'Planning step completed · '+new Date(completed.finished_at).toLocaleString():'No completed planning step recorded';
      item.append(Object.assign(n('p',label),{className:'workflow-history'+(completed?' completed':'')}));
    }
    if(selected)item.append(Object.assign(n('span',state.verified?'Active now':'Last reported active'),{className:'workflow-active'}));host.append(item);
  }return host;
}
function orchestrationPanel(batch, historical=false) {
  const host=card('','monitor-activity');
  host.append(n('h3',(historical?'Saved':'Current')+' Builder batch · '+(batch.id||'ID unavailable')),
    n('p','Saved status · '+human(batch.status||'unknown')),
    Object.assign(n('p','Worker statuses are saved reports, not live process checks. Built or integrated work still requires combined validation and acceptance.'),{className:'monitor-caption'}));
  for(const worker of batch.workers||[]){const row=card('','monitor-event');
    row.append(n('strong','Builder · '+(worker.milestone_id||'Milestone unavailable')),n('span','Saved status · '+human(worker.status||'unknown')),
      n('p','Worktree · '+(worker.workspace||'Not recorded')),n('p','Run / logs · '+(worker.run_dir||'Not recorded')));host.append(row);
  }
  if(!batch.workers?.length)host.append(n('p','No workers recorded yet.'));
  return host;
}
function monitorPanel(run, compact=false) {
  const state=taskOverviewState(run),monitor=run.monitor||{},live=monitor.live||{},host=card('','monitor-panel');
  const hero=card('','monitor-hero '+state.info.group),main=card('','monitor-state');
  main.append(Object.assign(n('p',state.label+(state.verified?' · worker verified':'')),{className:'monitor-status-chip '+state.info.group}),n('h2',state.step.replace(/^Current step · /,'').replace(/^Next step · /,'').replace(/^Last reported active step · /,'')));
  const evidence=live.state==='alive'?live.label+' · PID '+live.pid+(live.elapsed?' · uptime '+live.elapsed:''):live.label||'Live worker status is unavailable';
  main.append(Object.assign(n('p',evidence),{className:'monitor-evidence'}));
  const iteration=card('','monitor-iteration');iteration.append(n('strong',Number.isFinite(run.iteration)?run.iteration:'—'),n('span','iteration'+(monitor.limits_known?(monitor.iteration_limit==null?' · unlimited':' / '+monitor.iteration_limit):'')));
  const blocker=state.info.group==='attention'||state.info.group==='stopped'?state.info.reason:'No blocker recorded.';
  main.append(Object.assign(n('p','Blocker · '+concise(blocker,220)),{className:'monitor-blocker'}));
  hero.append(main,iteration);host.append(hero);
  const summary=card('','monitor-summary-grid');
  const objective=card('','monitor-objective');objective.append(Object.assign(n('p','CURRENT OBJECTIVE'),{className:'monitor-kicker'}),n('p',concise(state.objective,220)));
  if(state.objective.length>220)objective.append(disclosure('Read full objective','monitor-objective',[n('p',state.objective)],run.run));summary.append(objective);
  const nextAction=primaryAction(run,taskActionBusy(run)),needs=card('','monitor-next');
  const running=state.info.group==='running'&&state.verified,reported=state.info.group==='running'&&!state.verified;
  needs.append(Object.assign(n('p',running?'WHILE THE WORKER RUNS':reported?'LIVE STATUS NOT CONFIRMED':'NEXT SAFE ACTION'),{className:'monitor-kicker'}),n('strong',running?'Let the current step finish':reported?'Inspect saved activity':state.info.group==='complete'?'No action needed':nextAction.label),n('p',running?'The worker is active. Follow its progress or send feedback for the next safe step.':reported?'A stage is recorded as active, but no live worker is confirmed. Inspect the saved activity before deciding whether to continue.':state.info.group==='attention'||state.info.group==='stopped'?state.info.reason:state.info.group==='complete'?'The runner recorded this task as complete.':'No reply requested. You can send feedback in the conversation.'));
  if(!compact){
    const checkpoint=state.info.label==='Worker stopped',target=checkpoint?'overview':state.info.group==='attention'?state.info.tab:'interview';
    if(running||reported)needs.append(button(running?'View live activity →':'View saved activity →',()=>host.querySelector('.monitor-activity')?.scrollIntoView({block:'start',behavior:'smooth'}),'primary'));
    needs.append(focusKey(button(checkpoint?'View saved checkpoint →':state.info.group==='attention'?state.info.action+' →':'Open conversation →',()=>activateTab(target),'text-button'),'overview-conversation'));
  }
  summary.append(needs);host.append(summary);
  const freshness=card('','monitor-freshness');freshness.append(n('span','Log updated '+statusAge(monitor.log_updated)),n('span','Checkpoint '+statusAge(monitor.checkpoint_updated)),n('span','Checked '+statusAge(monitor.checked_at)));host.append(freshness);
  host.append(disclosure('Models & role history','monitor-roles',[workflowCards(run,state)],run.run));
  if(!compact){
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
  }return host;
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
  for(const step of run.monitor?.history||[]){const row=card('','monitor-event');row.append(n('span',stageName({...run,stage:step.stage})+' · iteration '+(step.iteration??'?')),n('small',(step.rejected?'Rejected':step.interrupted?'Interrupted':step.timed_out?'Timed out':stageSucceeded(step)?'Finished':'Recorded')+' · '+(step.finished_at||'Time not recorded')));host.append(row);}
  if(run.interventions)host.append(disclosure('Saved controls & delivery history','runtime-controls',[renderDocument(run.interventions)],run.run));host.hidden=currentTab!=='overview';
}
function markMonitorStale(){document.querySelectorAll('.monitor-panel').forEach(panel=>{
  panel.classList.add('monitor-stale');
  const chip=panel.querySelector('.monitor-status-chip');if(chip){chip.textContent='Status unverified';chip.className='monitor-status-chip stale';}
  const title=panel.querySelector('.monitor-state h2');if(title&&!title.textContent.startsWith('Last reported · '))title.textContent='Last reported · '+title.textContent;
  const next=panel.querySelector('.monitor-next');if(next){const heading=next.querySelector('strong'),description=next.querySelector('p:not(.monitor-kicker)');if(heading)heading.textContent='Refresh task status';if(description)description.textContent='Live checks are unavailable. The details shown here are from the last successful read.';}
  const freshness=panel.querySelector('.monitor-freshness');if(freshness)freshness.textContent='Live checks unavailable. The details below are from the last successful read.';
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
  if (currentView !== view) seq++;
  currentView = view;
  document.body.classList.toggle('monitor-focus',view==='task-detail'&&stored('focus')==='1');
  $('#focus-toggle').hidden=view!=='task-detail';
  $('#focus-toggle').textContent=stored('focus')==='1'?'Show navigation':'Focus view';
  $('#focus-toggle').setAttribute('aria-pressed',String(stored('focus')==='1'));
  if(['tasks','inbox','conversations','settings','new-task','archived'].includes(view))rememberSelection(view==='new-task'?'new':view==='tasks'?taskSelection():view);
  for (const id of ['inbox','tasks','new-task','task-detail','settings','conversations','draft-conversation','archived']) $('#'+id).hidden = id !== view;
  $('#breadcrumb-title').textContent = view==='inbox'?'Needs you':view==='archived'?'Archived':view === 'tasks' ? taskScopeTitle() : view === 'new-task' ? 'New conversation' : view === 'settings' ? 'Settings' : ['conversations','draft-conversation'].includes(view)?'Conversation':basename(chosen?.workspace);
  $('.app-shell').classList.remove('nav-open');$('#nav-toggle').setAttribute('aria-expanded','false');
  if(view!=='task-detail')stopPreview();
  document.querySelectorAll('[data-view]').forEach(element => element.classList.toggle('selected',element.dataset.view === (view==='draft-conversation'?'conversations':view) && (view!=='tasks'||(!projectFilter&&taskFilter==='all'))));
}
function activateTab(tab) {
  if(latestRun?.task_archived){showArchivedRun(latestRun);return;}
  const changed=currentTab!==tab;currentTab = tab;
  $('#task-overview').hidden=tab!=='overview'||!latestRun||latestRun.project_removed;
  $('#detail-grid').hidden=!!latestRun?.project_removed;
  for (const id of ['now','interview','plan','execution','changes','preview']) $('#'+id).hidden = id !== tab;
  $('#live-controls').hidden=tab!=='interview'||!latestRun||latestRun.project_removed||latestRun.task_archived;
  $('#goal').hidden = true;
  if(tab!=='preview')stopPreview();
  if(tab==='changes'&&chosen?.run&&evidenceRun!==chosen.run)loadChanges();
  if(tab==='preview'&&chosen?.run&&previewRun!==chosen.run){previewRun=chosen.run;$('#preview-url').value=stored('preview:'+chosen.run);$('#preview-error').textContent='';}
  if(latestRun)renderPrimaryAction(latestRun);
  if(changed&&tab==='interview'&&scrollThreadToEnd)settleThreadScroll();
  document.querySelectorAll('[data-tab]').forEach(element => { element.classList.toggle('selected',element.dataset.tab === tab); element.setAttribute('aria-current',element.dataset.tab === tab ? 'page' : 'false'); });
  if(taskReadError)disableStaleControls();
}
function openRun(run, tab = 'now') {
  $('#archive-task').disabled=true;
  if (!run.run || run.error) return;
  if(currentView!=='task-detail')taskReturn=currentView==='inbox'?{view:'inbox'}:currentView==='tasks'?{view:'tasks',filter:taskFilter,project:projectFilter}:{view:'tasks',filter:'all',project:''};
  const changed = chosen?.run !== run.run;
  if(changed){$('#task-overview').hidden=true;$('#now').dataset.rendered='';$('#now').replaceChildren(n('p','Loading current state…'));scrollThreadToEnd=true;stopPreview();evidenceRun='';evidenceRequest++;$('#changes-content').replaceChildren();$('#thread-checkpoint').replaceChildren();$('#task-objective').textContent='';$('#conversation').dataset.rendered='';}
  chosen = {workspace:run.workspace,run:run.run}; activeConversation = null; latestRun = null; seq++;
  taskReadError='';taskReadAt=null;$('#task-load-notice').hidden=true;
  rememberSelection(runSelection(run));
  renderTaskProjectActions(run.workspace);$('#task-removed-banner').hidden=true;$('#detail-grid').hidden=false;
  if (changed) { $('#conversation').replaceChildren(); $('#brief-current').replaceChildren(); $('#plan-full').replaceChildren(); $('#goal').replaceChildren(); $('#live-controls').hidden=true; $('#task-attention').hidden=true; $('#continue-run').disabled=true; $('#pause-run').disabled=true; $('#task-project').textContent=basename(run.workspace); $('#task-title').textContent=taskTitle(run); $('#task-subtitle').textContent='Loading current task state…'; $('#task-models').textContent=''; $('#task-status').replaceChildren(); }
  $('#task-back').textContent='← '+(taskReturn.view==='inbox'?'Needs you':taskReturn.project?basename(taskReturn.project):'All work');
  setView('task-detail'); activateTab(tab); refresh(); window.scrollTo({top:0});
}
function filterTasks(filter, project = projectFilter) { taskFilter = filter; projectFilter = project; setView('tasks'); if (latestData) renderTasks(latestData); }
$('#task-back').onclick=()=>taskReturn.view==='inbox'?setView('inbox'):filterTasks(taskReturn.filter,taskReturn.project);
function showNewTask() { chosen=null; activeConversation=null; seq++; setView('new-task');rememberSelection('new');$('#new-goal').focus(); }
document.querySelectorAll('[data-new-task]').forEach(element => element.onclick = showNewTask);
document.querySelectorAll('[data-view]').forEach(element => element.onclick = () => { if(element.dataset.view === 'tasks') filterTasks('all',''); else setView(element.dataset.view); });
document.querySelectorAll('[data-tab]').forEach(element => element.onclick = () => activateTab(element.dataset.tab));
$('#brand-home').onclick = event => { event.preventDefault(); setView('inbox'); };
$('#nav-toggle').onclick=()=>{const open=$('.app-shell').classList.toggle('nav-open');$('#nav-toggle').setAttribute('aria-expanded',String(open));};
document.addEventListener('keydown',event=>{if(event.key==='Escape'){$('.app-shell').classList.remove('nav-open');$('#nav-toggle').setAttribute('aria-expanded','false');document.querySelectorAll('.context-menu[open]').forEach(menu=>menu.open=false);}});
$('#task-status-filter').onchange = event => filterTasks(event.target.value);
$('#project-filter').onchange = event => filterTasks(taskFilter,event.target.value);
$('#task-search').oninput = event => { searchText = event.target.value; if (latestData) renderTasks(latestData); };
document.addEventListener('keydown',event => { if (event.key.toLowerCase()==='n' && !$('#project-removal-dialog').open && !$('#task-archive-dialog').open && !$('#conversation-archive-dialog').open && !event.metaKey && !event.ctrlKey && !event.altKey && !/INPUT|TEXTAREA|SELECT/.test(document.activeElement?.tagName) && !document.activeElement?.isContentEditable) { event.preventDefault(); showNewTask(); } });

$('#interview').append($('#change-history'));
function composerShouldSend(event) {return event.key==='Enter'&&!event.shiftKey&&!event.altKey&&!event.isComposing;}
function draftSendBlocked(doc,pending,text) {return !doc||!!(doc.project_removed||doc.task_archived||doc.archived_at||pending||doc.status==='thinking'||doc.attachment||doc.status==='error')||!String(text||'').trim();}
function resizeComposer(input) {input.style.height='auto';input.style.height=Math.min(160,Math.max(48,input.scrollHeight))+'px';}
for(const [input,form] of [['#change-text','#change-form'],['#draft-text','#draft-form']]){
  $(input).addEventListener('keydown',event=>{if(composerShouldSend(event)){event.preventDefault();const submit=$(form).querySelector('button[type="submit"]');if(submit&&!submit.disabled&&$(input).value.trim())$(form).requestSubmit();}});
  $(input).addEventListener('input',()=>resizeComposer($(input)));
}
function updateDraftScroll(){const scroll=$('#draft-scroll');$('#draft-latest').hidden=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<90;}
function scrollDraftToEnd(){const scroll=$('#draft-scroll');scroll.scrollTop=scroll.scrollHeight;updateDraftScroll();}
$('#draft-scroll').addEventListener('scroll',updateDraftScroll);
new ResizeObserver(updateDraftScroll).observe($('#draft-scroll'));
$('#draft-latest').onclick=scrollDraftToEnd;
function syncWorkspaces(list) {
  const select = $('#workspaces'), keep = select.value;
  if (select.dataset.options === JSON.stringify(list)) return;
  select.dataset.options = JSON.stringify(list); select.replaceChildren(n('option','Choose a project…')); select.firstChild.value='';
  for (const path of list) { const option = n('option',basename(path)+' · '+path); option.value=path; select.append(option); }
  select.append(Object.assign(n('option','Choose another folder…'),{value:'__custom__'}));
  if (keep && (list.includes(keep)||keep==='__custom__')) select.value=keep;
}
function syncModelOptions(data) {
  const catalogue = data.usable && Array.isArray(data.models) ? data.models.filter(value=>typeof value==='string') : [];
  const configured=typeof data.provider==='string'&&data.provider!=='opencode';
  const defaults=configured?Object.fromEntries(['glm','astra','terra','sol','completion'].map(role=>[role,data.provider+' config'])):{glm:'GLM-5.3 · OpenCode',astra:'GPT-5.6 Sol · high',terra:'GPT-5.6 Terra · medium',sol:'GPT-5.6 Sol · high',completion:'GPT-5.6 Sol · medium'};
  for (const role of ['glm','astra','terra','sol','completion']) {
    const select = $('#'+role+'-model'), previous = select.value;
    const values=catalogue;
    select.replaceChildren(Object.assign(n('option','Default · '+defaults[role]),{value:''}));
    const groups=new Map();
    for (const value of values) {
      const provider=value.split('/')[0];
      if(!groups.has(provider)){
        const label=provider==='openai'?'OpenAI · ChatGPT OAuth':provider==='zai-coding-plan'?'Z.ai Coding Plan':provider;
        const group=Object.assign(n('optgroup'),{label});groups.set(provider,group);select.append(group);
      }
      groups.get(provider).append(Object.assign(n('option',value),{value}));
    }
    if (previous && !values.includes(previous)) select.append(Object.assign(n('option','Unavailable selection: '+previous),{value:previous}));
    select.value=previous;
  }
  const status=$('#model-catalogue-status');
  const tool=typeof data.provider==='string'&&data.provider!=='opencode'?data.provider:'OpenCode';
  const billing=tool==='OpenCode'?' OpenAI choices require ChatGPT OAuth, not API-key billing.':' '+tool+' uses its own login and billing.';
  status.textContent=(data.error?data.error+' Your choices are preserved. Retry models, or choose the default.':data.loading?tool+' model lookup is still running. Retry shortly.':data.usable?catalogue.length+' models from '+tool+' available for every role.':'No usable '+tool+' catalogue was returned.')+' Names identify workflow roles, not fixed models. Explicit selections run through '+tool+'; Default keeps the labeled route.'+billing+' Existing runs keep their saved models.';
  status.className='field-note'+(data.error?' error':'');
}
async function loadModels(refresh=false) {
  const request=++modelRequest, status=$('#model-catalogue-status'), retry=$('#retry-models');
  status.textContent=refresh?'Refreshing models…':'Loading models…';
  status.className='field-note'; retry.disabled=true;
  try {
    const data=refresh?await api('/api/models/refresh',{method:'POST',headers:{'Content-Type':'application/json'},body:'{}'}):await api('/api/models');
    if(request===modelRequest)syncModelOptions(data);
  } catch(error) { if(request===modelRequest)syncModelOptions({error:error.message}); }
  finally { if(request===modelRequest)retry.disabled=false; }
}
function setup(data) {
  syncWorkspaces(data.workspaces || []);
  if (!init) {
    init=true;
     $('#retry-models').onclick=()=>loadModels(true);
     loadModels();
  }
  $('#registry-location').textContent=data.registry?.location || 'The registry location is currently unavailable.';
  renderArchivedTasks(data);
  dashboardNotice(actionProblem || (data.registry?.error ? 'Automatic task discovery is unavailable: '+data.registry.error : ''));
  renderRoots(data.watch_roots || []);renderProjectManager(data);
}
function emptyState(title, text, action) {
  const host=card('','empty-state'); host.append(icon('layers'),n('h2',title),n('p',text));
  if (action) host.append(button('Start a conversation',showNewTask,'primary')); return host;
}
function unattachedConversations(data){return (data.conversations||[]).filter(doc=>doc.attachment?.status!=='linked');}
function renderInbox(data){
  const runs=(data.runs||[]).filter(run=>run.run&&!run.error),waiting=runs.filter(run=>statusInfo(run).group==='attention'),paused=runs.filter(run=>statusInfo(run).group==='stopped');
  const failed=unattachedConversations(data).filter(doc=>doc.status==='error'||['failed','uncertain'].includes(doc.attachment?.status));
  $('#inbox-count').textContent=waiting.length+failed.length;
  const host=$('#inbox-list');host.replaceChildren();
  const addRun=(parent,run)=>{const info=statusInfo(run),row=focusKey(button('',()=>openRun(run),'inbox-item'),'inbox:'+run.run);
    row.append(Object.assign(n('div',basename(run.workspace)+' · '+taskTitle(run)),{className:'inbox-meta'}),n('h3',info.label==='Approve plan'?'Ready to approve the plan':info.label==='Review output'?'Review the finished work':concise(info.reason,190)),n('p',info.label==='Approve plan'?concise(run.goal?.body?.intended_outcome||info.reason,240):run.questions?.length>1?(run.questions.length-1)+' more '+(run.questions.length===2?'question':'questions')+' in this conversation.':info.label==='Answer needed'?'Your answer will let the task continue.':info.label),Object.assign(n('span',info.action+' →'),{className:'next-action'}));parent.append(row);};
  for(const run of waiting)addRun(host,run);
  for(const doc of failed){const row=focusKey(button('',()=>openConversation(doc.id),'inbox-item'),'inbox-chat:'+doc.id);row.append(Object.assign(n('div',doc.title||'Planning conversation'),{className:'inbox-meta'}),n('h3','This conversation needs a retry'),n('p',concise(doc.error||doc.attachment?.error||'The last reply could not finish.',220)),Object.assign(n('span','Open conversation →'),{className:'next-action'}));host.append(row);}
  if(!waiting.length&&!failed.length)host.append(emptyState('You’re all caught up.','No questions or approvals are waiting for you.'));
  if(paused.length){const list=card('','inbox-section');for(const run of paused)addRun(list,run);host.append(disclosure(paused.length+' paused '+(paused.length===1?'task':'tasks')+' to revisit','inbox-paused',[list],'inbox'));}
  const running=runs.filter(run=>statusInfo(run).group==='running').length,foot=$('#inbox-running');foot.replaceChildren(n('span',running?running+' '+(running===1?'task is':'tasks are')+' in progress.':'Ready for your next idea.'),button('View all work →',()=>filterTasks('all',''),'text-button'));
}
function renderTasks(data) {
  const runs=data.runs||[],actual=runs.filter(run=>run.run),projects=dashboardProjects(data);
  const countFor=list=>Object.fromEntries(taskGroups().map(([key])=>[key,list.filter(run=>statusInfo(run).group===key).length]));
  const current=projectCurrentRuns(actual),counts=countFor(current);
  $('#all-count').textContent=current.length+unattachedConversations(data).length;$('#project-count').textContent=projects.length;
  const projectsHost=$('#projects'),scroll=projectsHost.scrollTop;projectsHost.replaceChildren();
  for(const path of projects) {
    const tasks=actual.filter(run=>run.workspace===path),summary=projectRunSummary(tasks);
    const row=button('',()=>filterTasks('all',path)),label=projectSummaryLabel(summary);
    row.title=path+' · '+label;row.setAttribute('aria-label',basename(path)+', '+label);focusKey(row,'project:'+path);row.classList.toggle('selected',currentView==='tasks'&&projectFilter===path);
    row.append(card('','project-dot'),n('span',basename(path)),Object.assign(n('span',String(summary.current)),{className:'nav-count'}));projectsHost.append(row);
  }projectsHost.scrollTop=scroll;
  const selector=$('#project-filter'),signature=JSON.stringify(projects);
  if(selector.dataset.options!==signature){selector.replaceChildren(Object.assign(n('option','All projects'),{value:''}));for(const path of projects)selector.append(Object.assign(n('option',projects.filter(other=>basename(other)===basename(path)).length>1?path:basename(path)),{value:path,title:path}));selector.dataset.options=signature;}selector.value=projectFilter;
  document.querySelectorAll('[data-view="tasks"]').forEach(element=>element.classList.toggle('selected',currentView==='tasks'&&!projectFilter&&taskFilter==='all'));
  $('#task-status-filter').value=taskFilter;
  renderProjectScope();
  $('#workspace-heading').textContent=projectFilter?basename(projectFilter):'All work';
  $('#workspace-description').textContent=projectFilter?'Active work and saved history in this project.':'From the first idea to the finished change.';
  const scope=runs.filter(run=>!projectFilter||run.workspace===projectFilter),projectSummary=projectRunSummary(scope),currentProjectRuns=projectFilter?projectCurrentRuns(scope):scope.filter(run=>!isPreviewRun(run)),defaultScope=currentProjectRuns.filter(run=>!isPreviewRun(run)),savedHistory=scope.filter(run=>isPreviewRun(run)||(projectFilter&&!defaultScope.includes(run))),visibleCounts=countFor(defaultScope.filter(run=>run.run)),summary=$('#workspace-summary');visibleCounts.other=savedHistory.filter(run=>run.run).length;summary.replaceChildren();
  renderProjectOverview(scope);
  const all=button('All',()=>filterTasks('all'),'summary-filter');all.setAttribute('aria-pressed',String(taskFilter==='all'));summary.append(all);
  for(const [key,label] of [['attention','waiting on you'],['running','in progress'],['stopped','paused / issues'],['complete','completed'],['other','other saved']]){if(!visibleCounts[key]&&key!=='attention')continue;const item=button('',()=>filterTasks(key),'summary-filter');item.setAttribute('aria-label',visibleCounts[key]+' '+label);item.setAttribute('aria-pressed',String(taskFilter===key));item.append(n('strong',visibleCounts[key]),n('span',label));summary.append(item);}
  const query=searchText.toLowerCase(),filtered=(taskFilter==='other'?savedHistory:defaultScope).filter(run=>(taskFilter==='all'||taskFilter==='other'||statusInfo(run).group===taskFilter)&&(!query||[run.task,run.workspace,run.status,statusInfo(run).reason,statusInfo(run).label].some(value=>String(value||'').toLowerCase().includes(query))));
  const visibleTaskCount=filtered.filter(run=>run.run).length;
  $('#task-scope-note').textContent=(taskFilter==='all'&&projectFilter?projectSummaryLabel(projectSummary)+(projectSummary.previews?' · dry runs hidden':''):taskFilter==='all'?'Active work and saved history':taskGroups().find(group=>group[0]===taskFilter)?.[1]||'All statuses')+' · '+(projectFilter?basename(projectFilter):'All projects')+' · '+visibleTaskCount+' '+(visibleTaskCount===1?'task':'tasks')+(query?' matching your search':'');
  if(currentView==='tasks')$('#breadcrumb-title').textContent=taskScopeTitle();
  const host=$('#runs');host.replaceChildren();
  const drafts=unattachedConversations(data).filter(doc=>(!projectFilter||doc.attachment?.workspace===projectFilter)&&(!query||String(doc.title).toLowerCase().includes(query)));
  if(taskFilter==='all'&&drafts.length){const section=card('','task-group'),heading=card('','task-group-heading');heading.append(n('h2','Planning conversations'),n('span',drafts.length));section.append(heading);for(const doc of drafts){const row=focusKey(button('',()=>openConversation(doc.id),'conversation-row'),'draft-row:'+doc.id),copy=card('','conversation-row-copy');copy.append(n('h2',concise(doc.title||'Untitled conversation',86)),n('p',doc.attachment?.workspace?basename(doc.attachment.workspace):'Project optional'));row.append(Object.assign(n('span','G'),{className:'astra-avatar'}),copy,Object.assign(n('span',conversationStatus(doc)),{className:'badge'}));section.append(row);}host.append(section);}
  for(const [key,label,description] of taskGroups()){
    const members=filtered.filter(run=>statusInfo(run).group===key).sort((a,b)=>taskStarted(b)-taskStarted(a)||String(a.run||'').localeCompare(String(b.run||'')));
    if(!members.length)continue;
    const section=card('','task-group'),heading=card('','task-group-heading');heading.append(n('h2',label),n('span',String(members.length)),n('p',description));section.append(heading);
    for(const run of members){
      const info=statusInfo(run),row=card('','task-row'),main=card('','task-main'),copy=card('','task-copy');
      main.append(Object.assign(n('span',basename(run.workspace).slice(0,1).toUpperCase()),{className:'project-avatar'}));
      const title=n('span',taskTitle(run));title.className='task-name';title.title=String(run.task||run.error||'');
      const meta=n('div',basename(run.workspace)+' · '+taskIdentity(run));meta.className='task-meta';meta.title=run.run||run.workspace||'';copy.append(title,meta);main.append(copy);
      const reason=n('p',concise(info.reason,220));reason.className='task-reason';reason.title=String(info.reason||'');
      const action=card('','task-row-action');action.append(badge(run),Object.assign(n('span',info.action+' →'),{className:'next-action'}));row.append(main,reason,action);
      if(run.run&&!run.error){row.tabIndex=0;focusKey(row,'task-row:'+run.run);row.setAttribute('role','button');row.setAttribute('aria-label',info.action+': '+taskTitle(run)+' · '+basename(run.workspace)+' · '+taskIdentity(run));row.onclick=()=>openRun(run);row.onkeydown=event=>{if(event.key==='Enter'||event.key===' '){event.preventDefault();row.onclick();}};}
      else {row.classList.add('unavailable');action.lastChild.textContent='Check the project folder';}section.append(row);
    }host.append(section);
  }
  if(projectFilter&&removedProject(projectFilter)){host.append(emptyState('This project was removed.','Restore it to show its tasks and linked conversations again. Files and history stay on disk.'));}
  else if(!host.childElementCount)host.append(emptyState(actual.length?'No tasks match this view.':'Make room for your next idea.',actual.length?'Try a different status, project, or search.':'Start a conversation with the requirements planner. Your terminal tasks will appear here too.',!actual.length));
}
function renderRoots(roots) {
  const host=$('#watch-roots');host.replaceChildren();
  for(const root of roots){const row=card('','root-row');row.append(n('code',root.path),n('small',root.runtime?'This session':'CLI root · protected'));if(root.removable)row.append(button('Remove',()=>rootAction('remove',root.path)));host.append(row);}
}
async function rootAction(action,path) {try{await api('/api/watch-roots',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({action,path})});$('#watch-root-error').textContent='';if(action==='add')$('#watch-root').elements.path.value='';await refresh();}catch(error){$('#watch-root-error').textContent=error.message;$('#watch-root-error').className='error';}}
$('#watch-root').onsubmit=event=>{event.preventDefault();rootAction('add',event.target.elements.path.value);};
$('#create').onsubmit=async event=>{
  event.preventDefault();if(creatingConversation)return;
  const text=$('#new-goal').value.trim(),error=$('#create-error');if(!text)return;
  const models=Object.fromEntries(['glm','astra','terra','sol','completion'].map(role=>[role+'_model',$('#'+role+'-model').value]));
  for(const role of ['astra','terra','sol','completion'])models[role+'_reasoning_effort']=$('#'+role+'-reasoning-effort').value;
  const signature=JSON.stringify({text,models});
  if(!conversationRequest||conversationRequest.signature!==signature)conversationRequest=savedRequest('create-request',{text,models});
  creatingConversation=true;$('#create-submit').disabled=true;$('#create-submit').textContent='Starting conversation…';error.hidden=true;
  try {
    const doc=await api('/api/conversations',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(conversationPayload(text,models,conversationRequest.id))});
    if($('#new-goal').value.trim()===text){$('#new-goal').value='';persist('new-idea','');}conversationRequest=null;persist('create-request','');openConversation(doc);
  } catch(problem) {error.textContent=problem.message;error.hidden=false;}
  finally {creatingConversation=false;$('#create-submit').disabled=false;$('#create-submit').textContent='Start conversation ↗';}
};
$('#new-goal').value=stored('new-idea');
$('#new-goal').oninput=event=>persist('new-idea',event.target.value);
for(const role of ['glm','astra','terra','sol','completion']){const select=$('#'+role+'-model'),value=stored('model:'+role);if(value&&!Array.from(select.options).some(option=>option.value===value))select.append(Object.assign(n('option',value),{value}));select.value=value;select.onchange=()=>persist('model:'+role,select.value);}
for(const role of ['astra','terra','sol','completion']){const select=$('#'+role+'-reasoning-effort'),value=stored('reasoning:'+role);if(value&&Array.from(select.options).some(option=>option.value===value))select.value=value;select.onchange=()=>persist('reasoning:'+role,select.value);}

function openConversation(doc) {
  $('#archive-conversation').disabled=true;$('#conversation-archived-banner').hidden=true;
  const id=typeof doc==='string'?doc:doc.id;if(!id)return;
  if(activeConversation!==id){$('#draft-messages').replaceChildren();$('#draft-messages').dataset.rendered='';$('#draft-title').textContent='Loading conversation…';$('#draft-problem').hidden=true;$('#draft-text').value=stored('conversation-draft:'+id);$('#draft-text').dataset.conversation=id;$('#attach-mode').value='existing';$('#workspaces').value='';$('#project-path').value='';$('#attach-error').textContent='';attachModeChanged();}
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
  if(!docs.length)host.append(emptyState('Start with a conversation.','Explore an idea with the requirements planner. Your conversations are saved here, even before you choose a project.',true));
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
  const name=message.speaker||(message.role==='user'?'You':'Requirements planner'),speaker=message.role==='user'?'You':roleDisplayName(name);
  const text=message.text||'',body=message.role==='user'?n('p',text):messageBody(text);
  const at=messageTime(message),stamp=at?new Date(at).toLocaleString(undefined,{month:'short',day:'numeric',hour:'numeric',minute:'2-digit'}):'';
  const meta=Object.assign(n('div',''),{className:'speaker'});
  meta.append(n('strong',speaker));
  if(message.planning_history)meta.append(n('span','Planning discussion'));
  if(stamp){const time=n('time',new Date(at).toLocaleTimeString(undefined,{hour:'numeric',minute:'2-digit'}));time.title=stamp;time.dateTime=new Date(at).toISOString();meta.append(time);}
  row.append(meta);
  if(!message.expanded&&(text.length>1000||(message.planning_history&&text.length>320))){row.append(n('p',concise(text,260)),disclosure(message.planning_history?'Read saved planning discussion':'Read the full message','message:'+speaker+':'+text.slice(0,90),[body],chosen?.run||activeConversation||''));}else row.append(body);
  if(message.role==='user'&&message.status&&!['saved','received'].includes(message.status))row.append(Object.assign(n('small',receiptLabel(message)),{className:'message-receipt'}));host.append(row);return row;
}
function renderDraftConversation(doc) {
  if(doc.id!==activeConversation)return;latestConversation=doc;
  if(!doc.archived_at&&!doc.task_archived&&!doc.project_removed&&doc.attachment?.status==='linked'&&doc.attachment.run){
    const draft=stored('conversation-draft:'+doc.id),target=stored('task-draft:'+doc.attachment.run);
    if(draft&&!target){persist('task-draft:'+doc.attachment.run,draft);changeDrafts.set(doc.attachment.run,draft);}
    openRun(doc.attachment,'interview');return;
  }
  const archiveControl=$('#archive-conversation');archiveControl.hidden=!!doc.archived_at;archiveControl.disabled=conversationArchivePending.has(doc.id);archiveControl.onclick=()=>reviewConversationArchive(doc);
  const archiveBanner=$('#conversation-archived-banner');archiveBanner.replaceChildren();archiveBanner.hidden=!doc.archived_at;
  if(doc.archived_at)archiveBanner.append(n('p','This conversation is archived. Restore it to continue.'),conversationArchiveButton(doc,'restore','Restore conversation'));
  $('#draft-title').textContent=concise(doc.title||'Your idea',86);$('#draft-status').textContent=doc.project_removed?'Project removed':conversationStatus(doc);
  const model=doc.models?.glm_model||doc.models?.glm||'zai-coding-plan/glm-5.3';
  $('#draft-subtitle').textContent=doc.attachment?.workspace?'Connecting '+basename(doc.attachment.workspace):'Planning model: '+model+'. Bring in a project when you’re ready.';$('#draft-subtitle').title=model;
  const host=$('#draft-messages'),signature=JSON.stringify([doc.messages||[],doc.status,conversationPending.has(doc.id)]);
  if(host.dataset.rendered!==signature){const scroll=$('#draft-scroll'),near=scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<90||!host.dataset.rendered,position=scroll.scrollTop;host.replaceChildren();renderMessageHistory(host,doc.messages||[],doc.id);if(doc.status==='thinking'||conversationPending.has(doc.id)){const thinking=card('','chat-thinking');thinking.setAttribute('role','status');thinking.append(n('span','Requirements planner'),n('span',doc.status==='thinking'?'Thinking through your reply…':'Sending your message…'));host.append(thinking);}host.dataset.rendered=signature;requestAnimationFrame(()=>{if(near)scrollDraftToEnd();else{scroll.scrollTop=position;updateDraftScroll();}});}
  const pending=conversationPending.has(doc.id),thinking=doc.status==='thinking',linked=doc.attachment?.status==='linked',attaching=doc.attachment?.status==='starting';
  $('#draft-send').disabled=draftSendBlocked(doc,pending,$('#draft-text').value);$('#draft-send').textContent=pending?'Sending…':'Send ↑';
  $('#draft-delivery').textContent=thinking?'You can draft your next message while the requirements planner replies.':doc.status==='error'?'Retry the saved message to continue.':doc.attachment?'Connecting the project…':'Enter to send · Shift + Enter for a new line';
  requestAnimationFrame(()=>resizeComposer($('#draft-text')));
  const problem=$('#draft-problem'),retry=conversationRetries.get(doc.id);problem.replaceChildren();
  if(!doc.task_archived&&!doc.project_removed&&(doc.error||retry)){problem.append(Object.assign(n('p',retry?.error||doc.error),{className:'error'}));problem.append(button(retry?'Retry sending':'Retry planner',()=>retry?sendDraftMessage(doc,retry):retryConversation(doc)));}problem.hidden=!problem.childElementCount;
  const attachment=$('#attachment-status');attachment.replaceChildren();
  if(doc.task_archived){attachment.append(n('h3','This task is archived.'),n('p','Restore the task to continue its saved conversation.'),archiveButton(doc.attachment,'restore','Restore task'));$('#draft-send').disabled=true;}
  else if(doc.project_removed){attachment.append(n('h3','This project was removed from the dashboard.'),n('p','Your conversation and draft are saved. Restore the project to continue.'),Object.assign(n('p',doc.attachment?.workspace||''),{className:'project-path'}),projectActionButton(doc.attachment?.workspace,'restore','Restore project'));}
  else if(doc.attachment){const attached=doc.attachment;attachment.append(n('p',attached.status==='linked'?'Project attached: '+attached.workspace:attached.status==='starting'?'Connecting your project and opening the plan for review…':attached.error||'Project connection needs attention.'));if(attached.status==='linked'&&attached.run)attachment.append(button('Open project conversation →',()=>openRun(attached),'primary'));if(attached.status==='failed')attachment.append(button('Retry project handoff',()=>retryAttachment(doc)));if(attached.status==='uncertain')attachment.append(n('p','The previous handoff could not be confirmed. Inspect All tasks before trying another project.'));}
  $('#attach-disclosure').hidden=doc.project_removed||linked||attaching||!!doc.attachment;$('#attach-submit').disabled=doc.project_removed||pending||thinking||attaching||doc.status==='error';
  if(doc.task_archived){$('#draft-status').textContent='Task archived';$('#draft-delivery').textContent='Restore the task to continue.';$('#attach-disclosure').hidden=true;$('#attach-submit').disabled=true;}
  if(doc.project_removed)$('#draft-delivery').textContent='Restore the project to continue. Your unsent draft stays saved.';
  $('#draft-form').hidden=!!doc.archived_at;$('#draft-conversation .attach-section').hidden=!!doc.archived_at;
  if(doc.archived_at){$('#draft-status').textContent='Archived';$('#draft-send').disabled=true;$('#attach-submit').disabled=true;$('#draft-problem').hidden=true;}
}
$('#draft-text').oninput=event=>{persist('conversation-draft:'+event.target.dataset.conversation,event.target.value);$('#draft-send').disabled=draftSendBlocked(latestConversation,conversationPending.has(activeConversation),event.target.value);};
$('#draft-form').onsubmit=event=>{event.preventDefault();if(latestConversation?.id===activeConversation)sendDraftMessage(latestConversation);};
async function sendDraftMessage(doc,retry) {
  if(conversationArchiveBlocked(doc)||doc.task_archived||taskArchiveBlocked(doc.attachment?.run)||doc.project_removed||projectBlocked(doc.attachment?.workspace)||conversationPending.has(doc.id)||doc.status==='thinking')return;
  const text=$('#draft-text').value.trim(),request=retry||{text,request_id:savedRequest('conversation-request:'+doc.id,{text}).id};if(!request.text)return;
  conversationPending.add(doc.id);conversationRetries.delete(doc.id);seq++;renderDraftConversation(doc);
  try{const saved=await api('/api/conversation/message',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:doc.id,text:request.text,request_id:request.request_id})});persist('conversation-request:'+doc.id,'');if($('#draft-text').dataset.conversation===doc.id&&$('#draft-text').value.trim()===request.text){$('#draft-text').value='';persist('conversation-draft:'+doc.id,'');}renderDraftConversation(saved);if(activeConversation===doc.id)requestAnimationFrame(()=>{scrollDraftToEnd();$('#draft-text').focus();});}
  catch(error){conversationRetries.set(doc.id,{...request,error:error.message});}
  finally{conversationPending.delete(doc.id);if(activeConversation===doc.id)renderDraftConversation(latestConversation||doc);refresh();}
}
async function retryConversation(doc) {
  if(conversationArchiveBlocked(doc)||doc.task_archived||taskArchiveBlocked(doc.attachment?.run)||doc.project_removed||projectBlocked(doc.attachment?.workspace)||conversationPending.has(doc.id))return;conversationPending.add(doc.id);seq++;renderDraftConversation(doc);
  try{const saved=await api('/api/conversation/retry',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:doc.id})});renderDraftConversation(saved);}catch(error){dashboardNotice(error.message);}
  finally{conversationPending.delete(doc.id);refresh();}
}
async function retryAttachment(doc) {if(conversationArchiveBlocked(doc)||doc.task_archived||taskArchiveBlocked(doc.attachment?.run)||doc.project_removed||projectBlocked(doc.attachment?.workspace))return;try{const saved=await api('/api/conversation/attach',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({id:doc.id,workspace:doc.attachment.workspace,retry:true})});renderDraftConversation(saved);refresh();}catch(error){dashboardNotice(error.message);}}
function attachModeChanged() {const create=$('#attach-mode').value==='new';$('#existing-project-field').hidden=create;$('#attach-path-field').hidden=!create&&$('#workspaces').value!=='__custom__';$('#attach-path-label').textContent=create?'New project folder':'Existing project folder';$('#attach-submit').textContent=create?'Create project & attach →':'Attach for review →';}
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
  rail.append(Object.assign(n('p',data.current_assignment?.objective||(jointPlanning(run)?'The requirements planner and plan reviewer are shaping the plan.':'The plan reviewer is shaping the next step.')),{className:'assignment-summary'}));
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
}
function waitingMessage(request){const lines=[];for(const [label,key]of [['Decision needed','decision_needed'],['Question','question'],['Discovered','discovered'],['Impact','impact'],['Options','options'],['Proposed change','proposed_delta']]){let value=request[key];if(Array.isArray(value))value=value.join(' · ');if(typeof value==='string'&&value.trim())lines.push(label+': '+value);}return lines.length?lines.join('\n'):'The plan reviewer is waiting for your input. Details are unavailable in the saved state.';}
function renderQuestion(host,question,run) {
  const id=String(question.id),element=card('','chat-message pending');element.dataset.questionCard=id;
  element.append(Object.assign(n('p',planningSpeaker(run)+' · Needs your answer'),{className:'speaker'}),n('p',question.question||'Question unavailable'));
  const context=card('','question-context');if(question.why)context.append(n('span',question.why));
  if(question.proposed_default)context.append(n('span','Suggested: '+question.proposed_default));
  if(Array.isArray(question.options)&&question.options.length)context.append(n('span','Options: '+question.options.map(option=>typeof option==='string'?option:option.label||JSON.stringify(option)).join(' · ')));
  element.append(context);
  if(question.proposed_default){const delegate=focusKey(button('Accept suggested answer',()=>sendTaskChat(run,{question_id:id,delegate:true,text:'',request_id:newRequestId()})),'question:'+run.run+':'+id+':delegate');delegate.disabled=taskActionBusy(run)||taskChatPending.has(run.run);element.append(delegate);}
  host.append(element);
}
function renderMessageHistory(host,messages,identity){
  const split=Math.max(0,messages.length-4),older=card('','earlier-content');
  const latestAssistant=messages.findLast(entry=>entry.role!=='user');
  const render=(parent,entry)=>{const row=appendMessage(parent,{...entry,expanded:entry===latestAssistant||entry===messages.at(-1),text:entry.text||(entry.delegate?'Accepted suggested answer':'Saved answer')});if(entry.question_text)row.prepend(Object.assign(n('p','In reply to: '+entry.question_text),{className:'reply-context'}));if(entry.error)row.append(Object.assign(n('p',entry.error),{className:'error'}));if(entry.role==='user'&&entry.status==='error'&&latestRun?.run===identity)row.append(button('Retry same message',()=>sendTaskChat(latestRun,{...entry,request_id:entry.id,retry:true})));};
  for(const entry of messages.slice(0,split))render(older,entry);
  if(split){const history=disclosure('Earlier conversation · '+split+' messages','earlier-messages',[older],identity);history.className='earlier-messages';host.append(history);}
  for(const entry of messages.slice(split))render(host,entry);
}
function taskMessages(run){
  const receipts=run.chat_messages||[],answered=new Set(receipts.filter(entry=>entry.question_id).map(entry=>String(entry.question_id)));
  const answers=Object.entries(run.answers||{}).filter(([id])=>!answered.has(id)).map(([id,answer])=>({role:'user',speaker:'You',id,created_at:answer.at||answer.created_at,question_text:answer.question?.question,text:answer.text||answer.answer||'Saved answer',status:'saved'}));
  const plans=(run.planning_messages?.length?run.planning_messages:run.discovery_summary?[{speaker:run.discovery_role||planningSpeaker(run),text:run.discovery_summary}]:[]).map(entry=>({role:'assistant',...entry,speaker:roleDisplayName(entry.speaker),planning_history:true}));
  return orderedMessages([...(run.draft_messages||[]),...answers,...plans,...(run.progress_messages||[]),...receipts.map(entry=>({role:'user',speaker:'You',...entry}))]);
}
function settleThreadScroll(){if(scrollThreadToEnd&&currentTab==='interview'){scrollThreadToEnd=false;requestAnimationFrame(()=>{if(currentTab==='interview')$('#interview').scrollTop=$('#interview').scrollHeight;});}}
function renderConversation(run) {
  const root=$('#conversation'),signature=JSON.stringify([run.run,run.progress_messages,run.draft_messages,run.planning_messages,run.discovery_summary,run.answers,run.questions,run.chat_messages,run.user_request,run.status,run.goal?.approval_status,taskChatPending.has(run.run)]);
  $('#conversation-heading').textContent='Conversation';
  $('#conversation-avatar').textContent=planningSpeaker(run).slice(0,1);
  $('#conversation-description').textContent=jointPlanning(run)?'The requirements planner drafts and revises. The plan reviewer challenges and finalizes. Your direction stays in the conversation.':'Shape the work, then let your team build.';
  if(root.dataset.rendered===signature)return;const scroll=$('#interview');scrollThreadToEnd=scrollThreadToEnd||scroll.scrollHeight-scroll.scrollTop-scroll.clientHeight<90;root.dataset.rendered=signature;root.replaceChildren();
  const context=card('','conversation-context');context.append(n('strong','Conversation history'),n('p','Earlier drafts and requests remain here as history. Now shows what currently needs your decision.'),button('See current state →',()=>activateTab('now'),'text-button'));root.append(context);
  renderMessageHistory(root,taskMessages(run),run.run);
  if(statusInfo(run).label==='Answer needed')for(const question of run.questions||[])renderQuestion(root,question,run);
  if(statusInfo(run).group==='attention'&&run.user_request&&!(run.questions||[]).length)appendMessage(root,{speaker:'Plan reviewer',text:waitingMessage(run.user_request)});
  if(!root.childElementCount)appendMessage(root,{text:run.goal?.approval_status==='approved'?'The plan is approved. Follow the work here and send direction whenever you need to.':run.goal?.body?'Here’s the proposed plan. Review it and approve it when it reflects what you want to build.':planningSpeaker(run)+'’s questions and plan will appear here.'});
}
async function copyText(text,element){try{await navigator.clipboard.writeText(text);element.textContent='Copied';}catch{dashboardNotice('Select the displayed token to copy it. Clipboard access is unavailable.');}}
function renderBrief(run) {
  const host=$('#brief-current');host.replaceChildren();if(!run.goal?.body)return;
  if(String(run.goal.origin||'').startsWith('migration_draft')&&!run.goal.body.acceptance_criteria?.length){
    const placeholder=card('','brief-card');placeholder.append(n('h3','Plan not ready yet'),n('p','Your discussion is saved in Conversation. A validated planning draft has not been produced yet.'));
    if(statusInfo(run).label==='Planning needs retry')placeholder.append(n('p','Retry planning to generate a fresh draft. You will review the final plan before implementation.'));
    host.append(placeholder);return;
  }
  const approved=run.goal.approval_status==='approved',box=card('','brief-card'),heading=card('','brief-header'),brief=run.goal.body;
  const ready=statusInfo(run).label==='Approve plan';
  heading.append(n('h3','Current plan · revision '+(run.goal.revision??'?')),Object.assign(n('span',approved?'Approved':ready?'Ready for your review':'Draft'),{className:'badge '+(approved?'complete':'attention')}));
  const body=card('','brief-body');body.append(n('p',brief.intended_outcome||'Proposed plan'));
  const milestones=brief.milestones||brief.plan;
  if(Array.isArray(milestones)&&milestones.length){body.append(n('h3','Milestones'),planList(milestones));}
  if(brief.acceptance_criteria?.length){body.append(n('h3','What success looks like'));const list=n('ul','');for(const item of brief.acceptance_criteria)list.append(n('li',typeof item==='string'?item:item.criterion||item.description||JSON.stringify(item)));body.append(list);}
  body.append(disclosure('Read the complete plan','complete-brief:'+run.goal_token,[renderDocument(brief)],run.run));
  body.append(disclosure('Technical details','raw-brief:'+run.goal_token,[n('pre',JSON.stringify({revision:run.goal.revision,token:run.goal_token,source:run.goal},null,2))],run.run));box.append(heading);
  if(!approved&&!ready)box.append(Object.assign(n('p','This is a draft. The next action is shown in Now; approval becomes available when the current planning step is ready.'),{className:'field-note plan-note'}));
  if(run.goal_token&&(approved||ready)){const area=card('','brief-approval');if(approved)area.append(n('p','✓ Revision '+run.goal.revision+' is already approved. No plan approval is pending.'));else{
    area.append(n('p','Approve this revision, then start or resume work when you’re ready.'));
    const actions=card('','plan-actions'),approve=focusKey(button('Approve plan revision '+run.goal.revision,()=>submitTaskAction(run,'approve_goal',{token:run.goal_token,confirmation:run.goal_token}),'primary'),'approve:'+run.run+':'+run.goal_token);
    approve.disabled=(run.questions||[]).length>0||taskActionBusy(run);
    actions.append(button('Request changes',()=>{activateTab('interview');$('#change-text').focus();$('#change-text').scrollIntoView({block:'center',behavior:'smooth'});}),approve);area.append(actions);
  }box.append(area);}box.append(body);
  if(run.astra_plan?.current_plan?.length){const strategy=card('','current-strategy');strategy.append(n('h3','Current execution approach'),n('p','The team’s latest work sequence within this task. This is separate from the plan approval above.'),planList(run.astra_plan.current_plan));box.append(strategy);}host.append(box);
}
function output(parent,actions) {for(const action of actions||[]){const status=action.exit_status===2?'needs attention':action.status;parent.append(disclosure(action.label+' · '+status,'action:'+action.id,[n('pre','Exit: '+(action.exit_status??'pending')+'\nstdout:\n'+(action.stdout||'')+'\nstderr:\n'+(action.stderr||''))],'actions'));}}
function renderExecution(run){
  const host=$('#execution_view');host.replaceChildren(n('h2','Checks & evidence'));
  if(run.review_token&&run.review_criteria?.length){
    const review=card('','requested-reviews'),pending=run.review_criteria.filter(criterion=>run.human_reviews?.[criterion.id]?.token!==run.review_token),current=statusInfo(run).label==='Review output';
    review.append(n('h3',current?'Your review · '+pending.length+' remaining':!pending.length?'Your review is recorded':'Saved review request'),n('p',current?'Inspect the evidence below before approving each item. Your approval applies to this result only.':!pending.length?'All items are approved for this saved result. The next action appears above.':'This request is historical. Current state determines when review is available.'));
    for(const criterion of run.review_criteria){const box=card('','review-card'),reviewed=run.human_reviews?.[criterion.id]?.token===run.review_token,approve=focusKey(button(reviewed?'Approved · '+criterion.id:'Approve '+criterion.id,()=>submitTaskAction(run,'approve_review',{id:criterion.id,token:run.review_token})),'review:'+run.run+':'+criterion.id);approve.disabled=reviewed||taskActionBusy(run)||!current;box.append(n('p',criterion.id+' · '+criterion.criterion),approve);review.append(box);}review.append(disclosure('Review identity','review-token',[n('code',run.review_token)],run.run));host.append(review);
  }
  const stats=card('','execution-summary');for(const [key,label]of [['pass','criteria passed'],['fail','need attention'],['unknown','not yet verified']]){const item=n('span',label);item.prepend(n('strong',run.counts?.[key]||0));stats.append(item);}host.append(stats);
  const validation=run.validation||{},results=new Map((Array.isArray(validation.criterion_results)?validation.criterion_results:[]).filter(row=>row&&typeof row==='object').map(row=>[row.id,row]));
  host.append(Object.assign(n('p',validation.source_revision?'Saved verification · source '+String(validation.source_revision).slice(0,18):'No verification report has been saved yet.'),{className:'field-note'}));
  if(validation.contract_hash&&run.goal?.hash&&validation.contract_hash!==run.goal.hash)host.append(Object.assign(n('p','This report belongs to an earlier plan revision. Current work still needs verification.'),{className:'error'}));
  for(const criterion of run.criteria||[]){const row=card('','check-row'),result=results.get(criterion.id)||{},status=String(result.status||'Unverified').toLowerCase();row.append(Object.assign(n('span',human(status)),{className:'badge '+(status==='pass'?'complete':status==='fail'?'failed':'')}),n('strong',criterion.criterion||criterion.description||criterion.id));if(result.evidence_refs?.length)row.append(disclosure('Evidence · '+result.evidence_refs.length,'evidence:'+criterion.id,[renderDocument(result)],run.run));else row.append(n('p',criterion.verification_method||'Evidence will appear after verification.'));host.append(row);}
  if(validation.checks?.length)host.append(disclosure('Executed checks ('+validation.checks.length+')','executed-checks',[renderDocument(validation.checks)],run.run));
  if(validation.end_to_end_result)host.append(disclosure('End-to-end result','e2e-check',[renderDocument(validation.end_to_end_result)],run.run));
  if(validation.findings?.length)host.append(disclosure('Review findings ('+validation.findings.length+')','review-findings',[renderDocument(validation.findings)],run.run));
  host.append(disclosure('Full acceptance criteria','criteria',[renderDocument(run.criteria||[])],run.run));
  const steps=card('','saved-steps');for(const [index,stage]of (run.stages||[]).entries()){const row=card('','stage-row');row.append(n('span',stage.runner_owned?stageName({stage:stage.stage}):human(stage.stage||stage.role||'Stage')),n('small',typeof stage.duration_seconds==='number'?stage.duration_seconds.toFixed(1)+'s':'Duration not recorded'));row.append(disclosure('Stage details','stage:'+index,[renderDocument(stage)],run.run));steps.append(row);}host.append(disclosure('Step history ('+(run.stages||[]).length+')','step-history',[steps],run.run));
  const logs=$('#actions');logs.replaceChildren();if(!(run.actions||[]).length)logs.append(Object.assign(n('p','No command output in this dashboard session. Saved task state remains available after a restart.'),{className:'field-note'}));output(logs,run.actions);
}

async function loadChanges(force=false){
  if(!chosen?.run)return;const run={...chosen},ticket=++evidenceRequest,host=$('#changes-content');evidenceRun=run.run;
  host.replaceChildren(Object.assign(n('p','Loading saved changes…'),{className:'field-note'}));
  try{const base='/api/evidence?workspace='+encodeURIComponent(run.workspace)+'&run='+encodeURIComponent(run.run),key=run.run;
    if(force)for(const id of evidenceCache.keys())if(id.startsWith(key+'\0'))evidenceCache.delete(id);
    const data=await api(base);if(ticket!==evidenceRequest||chosen?.run!==run.run)return;host.replaceChildren();
    if(!data.stages?.length){host.append(emptyState('No saved changes yet.','Code diffs appear when an implementation step finishes.'));return;}
    for(const stage of [...data.stages].reverse()){
      const body=card('','diff-content'),item=disclosure(human(stage.stage||'Step')+' · '+(stage.finished_at?new Date(stage.finished_at).toLocaleString():'Saved snapshot')+(stage.rejected?' · rejected output':stage.interrupted?' · interrupted':''),'diff-stage:'+stage.index,[body],run.run);item.className='diff-stage';let loading=false,loaded=false;const remember=item.ontoggle;
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
function stopPreview(){$('#preview-content').replaceChildren();}
$('#refresh-changes').onclick=()=>loadChanges(true);
$('#preview-form').onsubmit=event=>{event.preventDefault();if(!chosen?.run)return;try{const url=localPreviewUrl($('#preview-url').value.trim(),location.href),host=$('#preview-content');persist('preview:'+chosen.run,url);$('#preview-error').textContent='';host.replaceChildren();const toolbar=card('','preview-toolbar'),link=n('a','Open in a new tab ↗');link.href=url;link.target='_blank';link.rel='noopener noreferrer';toolbar.append(n('p','If the app cannot be embedded, open it in a new tab.'),link);const frame=n('iframe','');frame.className='preview-frame';frame.title='Local app preview';frame.setAttribute('sandbox','allow-scripts allow-forms allow-same-origin');frame.referrerPolicy='no-referrer';frame.src=url;host.append(toolbar,frame);}catch(error){$('#preview-error').textContent=error.message;}};

function requestKey(run,kind){return run+'\0'+kind;}
function interruptedAttempt(run){
  return ['PAUSED_PROVIDER_UNCERTAIN','PAUSED_UNCERTAIN_STAGE'].includes(run.status) ? run.interventions?.attempt_id : null;
}
function taskActionBusy(run){return taskActionPending.has(run.run)||(run.actions||[]).some(action=>['queued','running'].includes(action.status));}
function primaryAction(run,busy=false){
  const info=statusInfo(run);
  if(run.status==='TASK_COMPLETE')return {kind:'checks',label:'Review checks'};
  if(run.interventions?.mode==='unavailable'||run.error||run.state_error)return {kind:'none',label:'Unavailable',disabled:true};
  if(info.group==='running')return {kind:'pause',label:'Pause'};
  if(busy)return {kind:'none',label:'Working…',disabled:true};
  if(interruptedAttempt(run))return {kind:'recover',label:'Review recovery'};
  if(info.label==='Answer needed')return {kind:'answer',label:run.questions.length>1?'Answer questions':'Answer question'};
  if(info.label==='Approve plan')return {kind:'plan',label:'Review plan'};
  if(info.label==='Review output')return {kind:'checks',label:'Review output'};
  if(info.label==='Ready to finish')return {kind:'continue',label:'Finish task'};
  if(info.group==='attention')return {kind:'answer',label:'Reply'};
  if(info.label==='Planning needs retry')return {kind:'continue',label:'Retry planning'};
  if(info.label==='Internally blocked'||info.label==='Worker unverified')return {kind:'checks',label:info.action};
  if(info.group==='stopped')return {kind:'continue',label:run.goal?.approval_status==='approved'?(!run.monitor?.orchestration_batch&&!run.stages?.some(stage=>['terra','orchestrator'].includes(stage.stage))?'Start building':'Resume task'):'Resume planning'};
  return {kind:'none',label:'Inspect activity',disabled:true};
}
function taskSentence(run,busy=false){
  const info=statusInfo(run),stage=(run.active_stage?.stage||run.monitor?.next_stage||run.stage||'').replace(/_report_repair$/,''),role=stageName({...run,stage}).split(' · ')[0];
  if(busy&&info.group!=='running')return 'Processing your last action…';
  if(info.group==='running'){
    const phrases={astra_discovery:'drafting the plan',astra_challenge:'reviewing the draft plan',glm_revise:'revising the plan',astra_finalize:'finalizing the plan',orchestrator:'coordinating independent Builders',terra:'implementing the current step',sol:'verifying the changes',astra_checkpoint:'auditing the completed work',astra_review:'reviewing the latest results',astra:'assigning the next step'};
    const sentence=role+' is '+(phrases[stage]||'working on the current step');
    return run.monitor?.live?.state==='alive'?sentence:'Last reported: '+sentence;
  }
  return info.label==='Answer needed'?'Your answer is needed to continue':info.label==='Approve plan'?'The plan is ready for your approval':info.label==='Review output'?'Your review is needed before completion':info.label==='Ready to finish'?'Your review is saved. Ready to finish.':info.group==='complete'?'The task is complete':info.label==='Planning needs retry'?'Planning stopped. A fresh draft is needed.':info.label==='Ready to continue'?'Ready for the next step':info.label==='Interrupted'?'An interrupted attempt needs review':info.group==='attention'?'Your decision is needed':info.label==='Worker stopped'?'The worker stopped at a saved checkpoint':'The task is paused';
}
function taskDecision(run,busy=false){
  const info=statusInfo(run),approved=run.goal?.approval_status==='approved',revision=run.goal?.revision??'?',action=primaryAction(run,busy);
  const result=(title,description,after,required=false)=>({title,description,after,required,action});
  if(run.error||run.state_error||run.interventions?.mode==='unavailable')return result('Status needs checking','Current controls are unavailable. Review the saved issue in History.','Restore access to the checkpoint before taking action.');
  if(busy&&info.group!=='running')return result('Your last action is being processed','Wait for the saved state to update.','This page will show the next decision when it is ready.');
  if(info.group==='complete')return result('No approval pending','This task is recorded as complete.','You can inspect the saved changes and verification.');
  if(info.label==='Answer needed')return result('Answer '+run.questions.length+' '+(run.questions.length===1?'question':'questions'),run.questions[0].question||info.reason,'Choose '+action.label+' above, or Conversation → Your reply → Send answer. Work continues after the last answer. A plan approval, when required, is a separate decision.',true);
  if(info.label==='Approve plan')return result('Approve plan revision '+revision,run.goal?.body?.intended_outcome||info.reason,'Review the Current plan tab, then choose Approve plan revision '+revision+'. This records approval; Start building or Resume task is the next action.',true);
  if(info.label==='Review output')return result('Review the finished work',info.reason,'Open Checks, inspect the evidence and approve each requested item. Then choose Finish task to run the final completion check.',true);
  if(info.group==='attention')return result('Your decision is needed',info.reason,'Open Conversation and reply to the current request. Your reply does not approve a new plan revision.',true);
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
  if(/^(astra_discovery|astra_challenge|glm_revise|astra_finalize)$/.test(stage)||run.goal?.approval_status!=='approved')return 'planning';
  if(stage==='orchestrator')return 'orchestration';
  return 'implementation';
}
function taskPosition(run){
  const parts=[];
  if(run.goal?.revision!=null)parts.push('Plan r'+run.goal.revision+' · '+(run.goal.approval_status==='approved'?'approved':statusInfo(run).label==='Approve plan'?'needs approval':'draft'));
  if(Number.isFinite(run.iteration))parts.push('Iteration '+run.iteration+(run.monitor?.limits_known&&run.monitor.iteration_limit!=null?' of '+run.monitor.iteration_limit:''));
  return parts.join('  /  ');
}
function renderTaskNow(run){
  const host=$('#now'),decision=taskDecision(run,taskActionBusy(run)),phase=taskPhase(run),assignment=run.astra_plan?.current_assignment;
  const signature=JSON.stringify([run.run,run.status,run.stage,run.iteration,run.goal_token,run.goal?.approval_status,run.questions,run.user_request,run.stop_reason,run.monitor,assignment,decision]);
  if(host.dataset.rendered===signature)return;host.dataset.rendered=signature;host.replaceChildren();
  const path=n('ol','');path.className='task-path';path.setAttribute('aria-label','Workflow stage');
  const stages=[['planning','Plan'],['approval','Your approval'],...(hasOrchestration(run)?[['orchestration','Orchestrator']]:[]),['implementation','Build'],['review','Review'],['complete','Complete']];
  if(hasOrchestration(run))path.classList.add('with-orchestration');
  const currentStage=stages.findIndex(([key])=>key===phase);
  for(const [index,[key,label]]of stages.entries()){const item=n('li',label);if(index<currentStage)item.className='done';if(key===phase){item.className='current';item.setAttribute('aria-current','step');}path.append(item);}
  if(decision.required){const decisionCard=card('','decision-card needs-decision');decisionCard.append(Object.assign(n('p','YOUR NEXT ACTION'),{className:'eyebrow'}),n('h2',decision.title),n('p',decision.description),Object.assign(n('p',decision.after),{className:'decision-after'}));host.append(decisionCard);}
  host.append(monitorPanel(run));host.append(path);
  if(assignment)host.append(disclosure('Assignment scope & checks','current-assignment:'+assignment.id,[renderDocument(assignment)],run.run));
  const context=card('','current-context'),plan=card('','');plan.append(n('h3','Current plan'),n('p',run.goal?.revision!=null?'Revision '+run.goal.revision+' · '+(run.goal.approval_status==='approved'?'Approved':decision.action.kind==='plan'?'Ready for review':'Draft'):'No plan revision saved'),button('Read current plan →',()=>activateTab('plan'),'text-button'));context.append(plan);
  const checkpoint=card('',''),latest=[...(run.monitor?.history||[])].sort((a,b)=>Date.parse(b.finished_at)-Date.parse(a.finished_at))[0];checkpoint.append(n('h3','Last saved step'));
  if(latest){checkpoint.append(n('p',stageName({...run,stage:latest.stage})+' · iteration '+(latest.iteration??'?')),n('p',(latest.rejected?'Output rejected':latest.interrupted?'Interrupted':latest.timed_out?'Timed out':stageSucceeded(latest)?'Step finished':'Step stopped')+' · '+new Date(latest.finished_at).toLocaleString()));}else checkpoint.append(n('p','No completed step recorded.'));
  checkpoint.append(button('View history →',()=>activateTab('overview'),'text-button'));context.append(checkpoint);host.append(context);
}
function renderPrimaryAction(run){
  $('#task-subtitle').textContent=taskSentence(run,taskActionBusy(run));
  const action=primaryAction(run,taskActionBusy(run)),control=$('#continue-run'),pause=$('#pause-run');
  const blocked=run.task_archived||taskArchiveBlocked(run.run)||run.project_removed||projectBlocked(run.workspace);
  pause.hidden=action.kind!=='pause'||blocked;control.hidden=action.kind==='pause'||blocked||(action.kind==='plan'&&currentTab==='plan')||(action.kind==='checks'&&currentTab==='execution');
  const pauseRequested=run.interventions?.pause_intent&&!run.interventions.pause_intent.acknowledged_at||(run.interventions?.entries||[]).some(entry=>entry.kind==='pause'&&['queued','requested'].includes(entry.status));
  pause.textContent=pauseRequested?'Pause requested':sendingRequests.has(requestKey(run.run,'pause'))?'Requesting pause…':'Pause';pause.disabled=!!pauseRequested||sendingRequests.has(requestKey(run.run,'pause'))||blocked;
  pause.onclick=()=>sendChange(run,'pause');control.textContent=action.label;control.disabled=!!action.disabled||taskChatPending.has(run.run)||blocked;
  control.onclick=()=>{
    if(action.kind==='continue')submitTaskAction(run,'continue');
    else if(action.kind==='plan')activateTab('plan');
    else if(action.kind==='checks')activateTab('execution');
    else{activateTab('interview');if(action.kind==='recover')$('#task-attention').scrollIntoView({block:'nearest'});else{$('#change-text').focus();const id=$('#question-target').value;const target=[...document.querySelectorAll('[data-question-card]')].find(element=>element.dataset.questionCard===id);target?.scrollIntoView({block:'nearest'});}}
  };
  $('#continue').disabled=action.kind!=='continue'||control.disabled;$('#continue').onclick=()=>{if(!$('#continue').disabled)submitTaskAction(run,'continue');};
}
async function submitTaskAction(run,action,extra={}){
  if(taskArchiveBlocked(run.run)||run.task_archived||projectBlocked(run.workspace)||run.project_removed||taskActionBusy(run))return;
  taskActionPending.add(run.run);seq++;renderLiveControls(run);renderBrief(run);renderExecution(run);renderTaskNow(run);
  try{await post('/api/action',{workspace:run.workspace,run:run.run,action,...extra});}
  finally{taskActionPending.delete(run.run);if(chosen?.run===run.run){renderLiveControls(latestRun||run);renderBrief(latestRun||run);renderExecution(latestRun||run);renderTaskNow(latestRun||run);}}
}
function renderTaskReasoning(run){
  const efforts=run.model_settings?.role_efforts||{},active=!!Object.keys(run.active_stage||{}).length,blocked=active||taskActionBusy(run)||['running','complete'].includes(statusInfo(run).group);
  for(const role of ['astra','terra','sol','completion']){$('#task-'+role+'-reasoning').value=efforts[role]||'';$('#task-'+role+'-reasoning').disabled=blocked;}
  const latest=(run.reasoning_escalations||[]).at(-1),escalated=latest?.selected?.profile?' Last automatic escalation: '+roleDisplayName(latest.role)+' → '+latest.selected.profile+'.':'';
  $('#save-task-reasoning').disabled=blocked;$('#task-reasoning-status').textContent=(blocked?(statusInfo(run).group==='running'?'Reasoning can be changed after the current step finishes.':statusInfo(run).group==='complete'?'This task is complete.':'Wait for the current step to finish.'):'Changes apply to the next model step; later struggle advances the automatic ladder.')+escalated;$('#task-reasoning-status').className='field-note';
}
$('#task-reasoning-form').onsubmit=async event=>{
  event.preventDefault();const run=latestRun;if(!run||taskActionBusy(run)||Object.keys(run.active_stage||{}).length)return;
  const payload={workspace:run.workspace,run:run.run,action:'set_reasoning'};
  for(const role of ['astra','terra','sol','completion']){const value=$('#task-'+role+'-reasoning').value;if(value)payload[role+'_reasoning_effort']=value;}
  const status=$('#task-reasoning-status'),button=$('#save-task-reasoning');button.disabled=true;status.textContent='Saving reasoning settings…';status.className='field-note';
  try{await post('/api/action',payload);status.textContent='Reasoning change queued. It will apply to the next model step.';await refresh();}
  catch(error){status.textContent=error.message;status.className='error';button.disabled=false;}
};
function renderTaskAttention(run){
  const host=$('#task-attention');host.replaceChildren();
  const next=statusInfo(run);
  if(['running','complete','attention'].includes(next.group)||taskActionBusy(run)){host.hidden=true;return;}
  host.append(n('h3',next.label),n('p',next.reason));
  const attempt=interruptedAttempt(run),paused=/PAUSED|BLOCKED|FAILED/.test(run.status||'');
  const busy=taskActionBusy(run),last=(run.actions||[]).at(-1);
  if(paused&&run.stop_reason&&run.stop_reason!==next.reason)host.append(disclosure('Saved pause details','pause-reason',[n('p',run.stop_reason)],run.run));
  if(attempt){
    host.append(n('p','Review the interrupted attempt below. Recover saved work keeps its edits and archives the uncertain response. Then click Continue task to let the assigned agent inspect the retained work.'));
    const stage=run.active_stage||{},evidence={attempt,stage:stage.stage,started_at:stage.started_at,finished_at:stage.finished_at,exit_code:stage.exit_code,events:stage.events,output:stage.output};
    host.append(disclosure('Inspect interrupted attempt','recovery:'+attempt,[renderDocument(evidence)],run.run));
    const recover=focusKey(button('Recover saved work',()=>submitTaskAction(run,'recover_stage',{attempt_id:attempt}),'primary'),'recover:'+run.run+':'+attempt);
    recover.disabled=busy||run.interventions?.mode==='unavailable';host.append(recover);
  }else if(next.label==='Internally blocked'){
    host.append(n('p','Inspect the saved failure and correct its cause before retrying.'));
    const retry=button('Retry saved step',()=>submitTaskAction(run,'continue'),'text-button');
    retry.disabled=busy||run.interventions?.mode==='unavailable';host.append(retry);
  }else if((run.questions||[]).length){host.append(n('p','Your saved questions remain in the plan. Resume planning to continue.'));}
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
function requestRow(entry,run){const row=card('','request-row');row.append(Object.assign(n('span',(entry.kind==='pause'?'Pause':'Change')+' · '+(entry.status==='delivered'?'delivered to legacy checkpoint':entry.status)),{className:'badge '+(['failed','uncertain'].includes(entry.status)?'failed':entry.status==='queued'||entry.status?.startsWith('submitting')?'attention':['applied','resumed','delivered'].includes(entry.status)?'complete':'')}));if(entry.text)row.append(n('p',entry.text));if(entry.error)row.append(Object.assign(n('p',entry.error),{className:'error'}));row.append(disclosure('Receipt','receipt:'+entry.id,[n('code',entry.id)],run.run));if(['uncertain','failed'].includes(entry.status)&&!entry.legacy&&entry.kind)row.append(button('Retry same request',()=>sendChange(run,entry.kind,entry)));return row;}
async function sendTaskChat(run,retry) {
  if(taskArchiveBlocked(run.run)||run.task_archived||projectBlocked(run.workspace)||run.project_removed||taskChatPending.has(run.run))return;
  const questions=run.questions||[],question_id=questions.length?$('#question-target').value||String(questions[0].id):null;
  let request=retry;
  if(!request){const text=$('#change-text').value.trim();if(!text)return;const prior=readSavedRequest('task-request:'+run.run),saved=prior?.payload?.text===text?prior:savedRequest('task-request:'+run.run,{text,question_id});request={...saved.payload,request_id:saved.id};}
  if(!request.text&&!request.delegate)return;
  taskChatPending.set(run.run,request);taskChatErrors.delete(run.run);seq++;renderLiveControls(run);
  try {
    const payload={...chatPayload(run,request.submitted_text??request.text,request.question_id,request.request_id,request.delegate),...(request.retry?{retry:true}:{})};
    const response=await api('/api/chat',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(payload)});
    if(response.status==='error')taskChatErrors.set(run.run,{...request,error:response.error||'This message needs attention.'});
    else{persist('task-request:'+run.run,'');if(!request.delegate&&$('#change-text').dataset.run===run.run&&$('#change-text').value.trim()===request.text){$('#change-text').value='';changeDrafts.set(run.run,'');persist('task-draft:'+run.run,'');}}
  } catch(error) {taskChatErrors.set(run.run,{...request,error:error.message});}
  finally {taskChatPending.delete(run.run);if(chosen?.run===run.run)renderLiveControls(latestRun||run);refresh();}
}
function renderLiveControls(run){
  if(run.task_archived||taskArchiveBlocked(run.run)){$('#live-controls').hidden=true;for(const id of ['continue','continue-run','pause-run'])$('#'+id).disabled=true;return;}
  if(run.project_removed||projectBlocked(run.workspace)){$('#live-controls').hidden=true;for(const id of ['continue','continue-run','pause-run'])$('#'+id).disabled=true;return;}
  const data=run.interventions||{},input=$('#change-text');$('#live-controls').hidden=currentTab!=='interview';
  if(input.dataset.run!==run.run){input.dataset.run=run.run;input.value=changeDrafts.get(run.run)??stored('task-draft:'+run.run);}
  const pendingRequest=readSavedRequest('task-request:'+run.run),confirmed=(run.chat_messages||[]).find(message=>message.id===pendingRequest?.id&&message.status!=='error');
  if(confirmed&&!taskChatPending.has(run.run)){if(input.value.trim()===pendingRequest.payload?.text){input.value='';changeDrafts.set(run.run,'');persist('task-draft:'+run.run,'');}persist('task-request:'+run.run,'');}
  const questions=statusInfo(run).label==='Answer needed'?run.questions||[]:[],target=$('#question-target'),selected=target.value,signature=JSON.stringify(questions.map(question=>[question.id,question.question]));
  if(target.dataset.options!==signature){target.replaceChildren();for(const question of questions)target.append(Object.assign(n('option',question.question||question.id),{value:String(question.id)}));if(questions.some(question=>String(question.id)===selected))target.value=selected;target.dataset.options=signature;}
  target.hidden=questions.length<2;$('#question-target-label').hidden=target.hidden;
  target.onchange=event=>document.querySelectorAll('[data-question-card]').forEach(element=>{const selected=element.dataset.questionCard===target.value;element.hidden=!selected;element.classList.toggle('selected-question',selected);if(selected){element.querySelector('.speaker').textContent=planningSpeaker(run)+' · Question '+(questions.findIndex(question=>String(question.id)===target.value)+1)+' of '+questions.length;if(event)element.scrollIntoView({block:'nearest'});}});target.onchange();
  $('#change-label').textContent=questions.length?'Your reply':'Message your team';
  input.placeholder=questions.length?'Answer the question above…':'Give feedback, ask for a change, or add context…';
  const unavailable=data.mode==='unavailable',needsRecovery=!!interruptedAttempt(run),busy=taskActionBusy(run),sending=taskChatPending.has(run.run);
  const sendBlocked=run.status==='TASK_COMPLETE'||unavailable||sending||(questions.length>0&&busy)||((run.questions||[]).length>0&&!questions.length);
  $('#send-change').disabled=sendBlocked||!input.value.trim();$('#send-change').textContent=sending?'Sending…':questions.length?'Send answer ↑':'Send ↑';
  $('#send-change').title='Enter to send · Shift + Enter for a new line';
  input.oninput=()=>{changeDrafts.set(input.dataset.run,input.value);persist('task-draft:'+input.dataset.run,input.value);$('#send-change').disabled=sendBlocked||!!taskReadError||!input.value.trim();};
  requestAnimationFrame(()=>resizeComposer(input));
  $('#change-form').onsubmit=event=>{event.preventDefault();sendTaskChat(run);};renderPrimaryAction(run);renderTaskAttention(run);
  $('#task-delivery').textContent=sending?'Saving your message…':questions.length?'Work continues after your last answer.':run.status==='TASK_COMPLETE'?'This task is complete. Start a new conversation for more work.':'Messages are saved when sent and applied at the next safe step';
  $('#live-mode').textContent=questions.length?'Final plan approval remains a separate step.':unavailable?'Live controls are unavailable.':statusInfo(run).group==='stopped'&&!busy?'Messages stay saved while this task is paused.':'';
  $('#live-error').textContent=data.error||'';
  const host=$('#change-history');host.replaceChildren();const entries=data.entries||data.requests||[],chatIds=new Set((run.chat_messages||[]).map(entry=>entry.id));
  for(const entry of entries)if(!chatIds.has(entry.id)&&!['applied','resumed','delivered'].includes(entry.status))host.append(requestRow(entry,run));
  for(const entry of uncertainRequests.values())if(entry.run===run.run&&!entries.some(item=>item.id===entry.id))host.append(requestRow(entry,run));
  for(const entry of sendingRequests.values())if(sendingRequests.get(requestKey(run.run,entry.kind))===entry)host.append(requestRow({...entry,status:'submitting · not yet confirmed durable'},run));
  const error=taskChatErrors.get(run.run);if(error&&!chatIds.has(error.request_id)){const row=card('','conversation-problem');row.append(Object.assign(n('p',error.error),{className:'error'}),button('Retry same message',()=>sendTaskChat(run,{...error,retry:true})));host.append(row);}
  if(sending)host.append(Object.assign(n('p','Saving your message…'),{className:'field-note'}));
  if(data.pause_intent&&!data.pause_intent.acknowledged_at&&statusInfo(run).group==='running')host.append(Object.assign(n('p','Pause requested. The current step will finish first.'),{className:'field-note'}));
}
function disableStaleControls(){
  document.querySelectorAll('#task-detail button, #task-detail input, #task-detail textarea, #task-detail select').forEach(control=>{if(!control.dataset.tab&&!['task-back','retry-task'].includes(control.id)){if(control.dataset.staleDisabled===undefined)control.dataset.staleDisabled=String(control.disabled);control.disabled=true;}});
}
function unavailableRun(message){
  taskReadError=message;$('#sync-state').textContent='Task status unavailable';$('#task-status').replaceChildren(Object.assign(n('span','Status unverified'),{className:'badge failed'}));
  $('#task-load-notice').hidden=false;$('#task-load-message').textContent=message+(taskReadAt?' Showing the last successful read from '+new Date(taskReadAt).toLocaleTimeString()+'.':' No current task data is available.')+' Controls are disabled until a successful refresh.';
  // openRun clears latestRun on task changes. The server may canonicalize a
  // symlinked workspace path; textual URL equality is not a freshness test.
  if(!latestRun){$('#now').replaceChildren(n('p','Task status has not loaded. Retry to read the saved checkpoint.'));}
  markMonitorStale();disableStaleControls();$('#continue').disabled=true;$('#live-controls').hidden=true;
}
function showRemovedRun(run) {
  stopPreview();evidenceRequest++;evidenceRun='';
  $('#archive-task').disabled=true;
  $('#task-overview').hidden=true;
  latestRun=run;$('#task-title').textContent='Project removed';$('#task-project').textContent=basename(run.workspace);$('#task-project').title=run.workspace;$('#task-subtitle').textContent='This task is hidden until you restore its project.';$('#task-models').textContent='';$('#task-status').replaceChildren();
  for(const id of ['conversation','brief-current','goal','plan-full','execution_view','actions','task-attention'])$('#'+id).replaceChildren();
  $('#task-attention').hidden=true;$('#detail-grid').hidden=true;$('#live-controls').hidden=true;for(const id of ['continue','continue-run','pause-run'])$('#'+id).disabled=true;
  const host=$('#task-removed-banner');host.replaceChildren(n('h2','Restore '+basename(run.workspace)+' to view this task'),n('p','Its files and history stay on disk. Running tasks keep going.'),Object.assign(n('p',run.workspace),{className:'project-path'}),projectActionButton(run.workspace,'restore','Restore project'));host.hidden=false;renderTaskProjectActions(run.workspace,true);
}
function showArchivedRun(run){
  stopPreview();evidenceRequest++;evidenceRun='';
  latestRun=run;$('#archive-task').disabled=true;
  $('#task-title').textContent='Task archived';$('#task-project').textContent=basename(run.workspace);$('#task-project').title=run.workspace;
  $('#task-subtitle').textContent='Restore this task to view its history and controls.';$('#task-models').textContent='';$('#task-status').replaceChildren();
  for(const id of ['task-overview','task-attention','detail-grid','live-controls'])$('#'+id).hidden=true;
  for(const id of ['continue','continue-run','pause-run'])$('#'+id).disabled=true;
  const host=$('#task-removed-banner');host.replaceChildren(n('h2','This task is archived'),n('p','Its files and history are preserved. Archiving does not stop a running worker.'),archiveButton(run,'restore','Restore task'));host.hidden=false;
  renderTaskProjectActions(run.workspace);
}
function showRun(run,focus){
  taskReadError='';taskReadAt=Date.now();$('#task-load-notice').hidden=true;$('#sync-state').textContent='Task checked just now';
  document.querySelectorAll('[data-stale-disabled]').forEach(control=>{control.disabled=control.dataset.staleDisabled==='true';delete control.dataset.staleDisabled;});
  if(run.task_archived){showArchivedRun(run);restoreFocus(focus);return;}
  if(run.project_removed||removedProject(run.workspace)){showRemovedRun(run);restoreFocus(focus);return;}
  $('#task-removed-banner').hidden=true;$('#detail-grid').hidden=false;renderTaskProjectActions(run.workspace);
  latestRun=run;$('#continue').disabled=false;
  $('#archive-task').disabled=archivePending.has(run.run);$('#archive-task').onclick=()=>reviewTaskArchive(run);
  renderTaskOverview(run);
  $('#task-project').textContent=basename(run.workspace);$('#task-project').title=run.workspace;
  const task=String(run.task||'Untitled task');$('#task-title').textContent=taskTitle(run);$('#task-title').title=task;
  $('#task-subtitle').textContent=taskSentence(run,taskActionBusy(run));$('#task-status').replaceChildren(badge(run));
  $('#task-objective').textContent=taskPosition(run);
  const settings=run.model_settings||{},roles=settings.roles||{},roleNames=jointPlanning(run)?['glm','astra','terra','sol','completion']:['astra','terra','sol','completion'];
  const models=$('#task-models'),efforts=settings.role_efforts||{};models.replaceChildren();for(const role of roleNames){const item=n('p',''),detail=[roles[role]||'Model not recorded',efforts[role]?efforts[role]+' reasoning':null,settings.role_engines?.[role]||settings.engine||'Saved provider'].filter(Boolean).join(' · ');item.append(n('strong',({glm:'Requirements planning',astra:'Plan review',terra:'Implementation',sol:'Verification',completion:'Completion decision'})[role]),n('span',detail));models.append(item);}
  renderTaskReasoning(run);renderConversation(run);renderBrief(run);renderAstraPlan(run);renderExecution(run);renderLiveControls(run);renderTaskNow(run);renderThreadCheckpoint(run);activateTab(currentTab);settleThreadScroll();restoreFocus(focus);
}
function renderThreadCheckpoint(run){
  const host=$('#thread-checkpoint');host.replaceChildren();host.className='thread-checkpoint';
  const info=statusInfo(run),assignment=run.astra_plan?.current_assignment,activity=run.monitor?.activity||[];
  if(info.group==='running'){host.append(n('strong','Latest task update'),n('p',assignment?.objective||run.monitor?.objective||'Work is in progress.'));if(activity.length)host.append(Object.assign(n('p',activity.slice(-2).map(entry=>entry.label).join(' · ')),{className:'field-note'}));}
  else if(info.label==='Approve plan')host.append(n('strong','Plan revision '+(run.goal?.revision||1)+' is ready'),n('p','Open Current plan and choose Approve plan revision '+run.goal.revision+'. Starting work is a separate action.'));
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
      const data=await api('/api/runs');if(mine!==seq)return;latestData=data;setup(data);if(restorePendingShortRun(data)||restorePendingShortProject(data))return;const focus=captureControls();renderTasks(data);renderConversations(data);renderInbox(data);expireNotices();restoreFocus(focus);
      if(!selected)$('#sync-state').replaceChildren(Object.assign(n('span',''),{className:'status-dot'}),document.createTextNode('Task list checked just now'));
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
  const selection=location.hash.slice(1)||stored('selection','inbox'),params=new URLSearchParams(selection);
  if(params.get('focus')==='1')persist('focus','1');
  if(params.get('conversation'))openConversation(params.get('conversation'));
  else if(params.get('run')&&!params.get('task')){
    const key=params.get('run'),matches=matchingShortRuns(key);
    if(matches.length===1){pendingShortRun='';openRun(matches[0]);}
    // Keep the compact hash intact until the first local registry snapshot
    // arrives; setView('inbox') would overwrite it with #inbox.
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
  else setView(['inbox','conversations','settings','archived'].includes(selection)?selection:selection==='new'?'new-task':'inbox');
}
window.addEventListener('hashchange',restoreSelection);
function applyTheme(theme){document.documentElement.dataset.theme=theme;$('#theme-toggle').textContent=theme==='dark'?'Light theme':'Dark theme';$('#theme-toggle').setAttribute('aria-pressed',String(theme==='dark'));}
applyTheme(stored('theme','dark'));
$('#theme-toggle').onclick=()=>{const theme=document.documentElement.dataset.theme==='dark'?'light':'dark';persist('theme',theme);applyTheme(theme);};
$('#focus-toggle').onclick=()=>{const focus=stored('focus')!=='1';persist('focus',focus?'1':'0');document.body.classList.toggle('monitor-focus',focus);$('#focus-toggle').textContent=focus?'Show navigation':'Focus view';$('#focus-toggle').setAttribute('aria-pressed',String(focus));};
$('#focus-toggle').textContent=stored('focus')==='1'?'Show navigation':'Focus view';
$('#retry-task').onclick=()=>refresh();
restoreSelection();
refresh();
