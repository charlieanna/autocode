/* Code restoration is an explicit chat action. History-only stage metadata
   never becomes a restore target. Compare tokens bind the paused state and files. */
const checkpointActions=new Map();
function checkpointView(run){return run.interventions?.code_checkpoints;}
function renderCodeCheckpoints(root,run){
  const comparison=$('#checkpoint-comparison');if(comparison?.dataset.run&&comparison.dataset.run!==run.run)comparison.hidden=true;
  const view=checkpointView(run);if(view?.version!==1)return;
  let host=root.querySelector('[data-code-checkpoints]');if(!host){host=card('','code-checkpoints');host.dataset.codeCheckpoints='true';root.append(host);}host.replaceChildren();
  if(view.continuation){const parent=view.continuation,box=card('','checkpoint-lineage');
    box.append(n('h3','Restored candidate'),n('p','Plan approval preserved. Fresh independent checks are required for this branch.'),button('Open preserved original',()=>openRun({workspace:parent.source_workspace,run:parent.source_run}),'text-button'));host.append(box);}
  for(const result of view.restores||[]){const box=card('','checkpoint-lineage');box.append(n('h4','Restored branch saved'),n('p','Original branch preserved. Continuation: '+result.branch),button('Open restored candidate',()=>openRun({workspace:result.workspace,run:result.run_dir}),'text-button'));host.append(box);}
  const entries=view.rows||[];if(!entries.length)return;
  host.append(n('h3','Saved code checkpoints'));
  for(const cp of entries){const row=card('','code-checkpoint');row.dataset.codeCheckpoint=cp.id;
    const at=Date.parse(cp.at),stamp=Number.isFinite(at)?new Date(at).toLocaleString():'';
    row.append(n('h4',cp.label),Object.assign(n('p',(cp.milestone_id?cp.milestone_id+' · ':'')+stamp),{className:'field-note'}),
      n('p',cp.recorded_check?.verdict?'Recorded check: '+cp.recorded_check.verdict+' · applies to this historical candidate.':'Not independently checked at this saved step.'));
    if(cp.available){
      row.append(button('Show what changed since here',()=>compareCodeCheckpoint(run,cp,true),'text-button'));
      const restore=button('Go back to here',()=>prepareCodeRestore(run,cp),'text-button');restore.dataset.checkpointRestore=cp.id;
      restore.disabled=!!view.blocked_reason||!!taskReadError||taskActionBusy(run);row.append(restore);
      if(view.blocked_reason)row.append(Object.assign(n('p',view.blocked_reason),{className:'field-note'}));
    }else row.append(n('p',cp.reason||'This historical step has no restorable source snapshot.'));
    host.append(row);
  }
  const action=checkpointActions.get(run.run);if(!action)return;
  const box=card('','checkpoint-confirmation');box.dataset.checkpointConfirmation='true';box.setAttribute('aria-live','polite');
  if(action.result){const result=action.result;
    box.append(n('h4','New branch ready'),n('p','Preserved '+(result.preserved_branch||'the original branch')+'. Created '+result.branch+'.'),n('p',result.message),
      button('Open restored candidate',()=>openRun({workspace:result.workspace,run:result.run_dir})));}
  else if(action.loading)box.append(n('p','Reading the exact saved files and current candidate…'));
  else if(action.comparison){const compared=action.comparison;
    box.append(n('h4','Restore “'+action.cp.label+'” on a new branch?'),n('p','Your current branch '+(compared.source_branch||'and workspace')+' and all later work stay intact.'),
      n('p','Plan approval stays valid. All milestones need fresh verification. Later open findings will be marked “no longer applies (rolled back)”. The new candidate stays paused until you resume.'),
      n('p',compared.changed_files.length+' file(s) differ from this checkpoint.'));
    if(compared.blocked_reason)box.append(n('p',compared.blocked_reason));
    else {const confirm=button(action.busy?'Creating branch…':action.uncertain?'Reconcile this restore':'Create restored branch',()=>confirmCodeRestore(run,action));confirm.disabled=!!action.busy;confirm.dataset.confirmCheckpoint='true';box.append(confirm);}
    if(!action.busy&&!action.uncertain)box.append(button('Cancel',()=>{checkpointActions.delete(run.run);renderCodeCheckpoints(root,run);},'text-button'));
  }
  if(action.error)box.append(Object.assign(n('p',action.error),{className:'error'}));host.append(box);
}
async function compareCodeCheckpoint(run,cp,show){
  try{const result=await api('/api/checkpoint/compare',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({workspace:run.workspace,run:run.run,checkpoint_id:cp.id})});
    if(show){activateTab('changes');const host=$('#checkpoint-comparison');host.hidden=false;host.dataset.run=run.run;host.replaceChildren(n('h3','Changes since '+cp.label),n('p','Comparison captured at '+result.compared_at+' · source '+result.source_revision.slice(0,12)),n('p',result.changed_files.length+' changed files'),n('pre',result.patch||'The files match this checkpoint.'));
      if(result.truncated)host.append(n('p','Comparison is truncated at 200 KB; inspect remaining files before confirming.'));
      host.scrollIntoView({block:'start'});}
    return result;
  }catch(error){if(show){dashboardNotice(error.message);return null;}throw error;}
}
async function prepareCodeRestore(run,cp){
  const root=$('#conversation'),action={cp,loading:true};checkpointActions.set(run.run,action);renderCodeCheckpoints(root,run);
  try{action.comparison=await compareCodeCheckpoint(run,cp,false);}
  catch(error){action.error=error.message;}
  finally{action.loading=false;if(latestRun?.run===run.run){activateTab('interview');renderCodeCheckpoints(root,run);const box=root.querySelector('[data-checkpoint-confirmation]');box?.scrollIntoView({block:'center'});box?.querySelector('[data-confirm-checkpoint]')?.focus();}}
}
async function confirmCodeRestore(run,action){
  if(action.busy||!action.comparison||action.comparison.blocked_reason)return;
  const body={workspace:run.workspace,run:run.run,checkpoint_id:action.cp.id,expected_token:action.comparison.expected_token};
  const key='checkpoint-restore:'+run.run,request=savedRequest(key,body);action.busy=true;action.error='';renderCodeCheckpoints($('#conversation'),run);
  try{action.result=await api('/api/checkpoint/restore',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({...body,request_id:request.id})});persist(key,'');action.uncertain=false;}
  catch(error){action.error=error.message;action.uncertain=/unconfirmed|network|fetch|timeout|failed to/i.test(error.message);}
  finally{action.busy=false;if(latestRun?.run===run.run)renderCodeCheckpoints($('#conversation'),run);}
}
