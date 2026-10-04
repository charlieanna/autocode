// Empty, saved conversations use the approved project-scope design. This is
// presentation only: opening/reloading it cannot dispatch a provider or claim
// that instructions were already read, checks ran, or a model was invoked.
function renderScopedConversationStart(doc) {
  const page=$('#draft-conversation'),scope=conversationWorkspace(doc);
  const empty=!!scope&&!doc.attachment&&!doc.archived_at&&!doc.task_archived&&!doc.project_removed&&!doc.error&&doc.status!=='thinking'&&!(doc.messages||[]).length&&!conversationPending.has(doc.id)&&!(typeof setupChat!=='undefined'&&setupChat===doc.id);
  page.classList.toggle('scoped-start',empty);
  if(currentView==='draft-conversation')$('#breadcrumb-title').textContent=empty?'autocode':'Conversation';
  let intro=$('#scoped-start-card'),context=$('#scoped-project-context'),eyebrow=$('#scoped-start-eyebrow'),models=$('#scoped-start-models'),scopeNote=$('#scoped-composer-scope');
  for(const item of [intro,context,eyebrow,models,scopeNote])if(item)item.hidden=!empty;
  if(!empty)return;
  const name=projectLabel(scope),signature=JSON.stringify([doc.id,scope,doc.configured_routes,doc.models,matchMedia('(max-width:759px)').matches]);
  const mobile=matchMedia('(max-width:759px)').matches;
  $('#draft-title').textContent=mobile?'New conversation':'New conversation in '+name;
  $('#draft-status').textContent='Ready';
  $('#draft-send').textContent='Send';
  $('#draft-subtitle').textContent=mobile?name+' · No work started':'Uses '+name+' repository and project instructions · No work started';
  $('#draft-text').placeholder='Message about '+name+'…';
  if(!eyebrow){eyebrow=n('p');eyebrow.id='scoped-start-eyebrow';eyebrow.className='eyebrow';$('#draft-title').parentElement.prepend(eyebrow);}
  eyebrow.textContent=name.toUpperCase()+' / New conversation';eyebrow.hidden=false;
  if(!intro){intro=card('','scoped-start-card');intro.id='scoped-start-card';$('#draft-messages').append(intro);}
  if(intro.dataset.scope!==signature){intro.replaceChildren(Object.assign(n('p','PROJECT SCOPE'),{className:'scoped-start-kicker'}),n('h2',mobile?'Start in '+name:'New conversation in '+name),n('p',mobile?'This conversation uses the '+name+' repository and project instructions. Describe what you want to do; questions and decisions will stay in this chat.':'This conversation will use the '+name+' repository and project instructions. No work has started. Required decisions will appear in this chat.'));intro.dataset.scope=signature;}
  intro.hidden=false;
  if(!context){context=n('aside');context.id='scoped-project-context';context.setAttribute('aria-label','Project context');$('#draft-conversation .draft-chat-layout').append(context);}
  if(context.dataset.scope!==signature){
    const ready=card('','scoped-context-card');ready.append(n('h3','Ready for a new conversation'),n('p','Scope · '+name+' repository'),n('p','Uses '+name+' project instructions.'),n('p','No model activity yet.'));
    const checks=card('','scoped-context-card');checks.append(n('h3','Verification begins after work starts'),n('p','No checks have run for this conversation.'));
    context.replaceChildren(n('h2','Project context'),n('p','No work started'),ready,checks,n('p','No open problems · No work has started.'),Object.assign(n('p','Scope'),{className:'scoped-start-kicker'}),n('p',name+' repository and project instructions'));context.dataset.scope=signature;
  }
  context.hidden=false;
  if(!scopeNote){scopeNote=n('p');scopeNote.id='scoped-composer-scope';$('#draft-text').after(scopeNote);}
  scopeNote.textContent='Scope: '+name+' repository + '+(mobile?'instructions':'project instructions');scopeNote.hidden=false;
  if(!models){models=n('details');models.id='scoped-start-models';$('#draft-form .composer-bottom').prepend(models);}
  if(models.dataset.scope!==signature){
    const routes=doc.configured_routes||{},route=routes.requirements_gatherer||routes.gatherer||routes.glm;
    const label=route?.model?[route.model,route.reasoning_effort].filter(Boolean).join(' · '):'Saved models';
    const list=card('','scoped-model-list');list.append(n('h3','Saved configuration'));
    for(const [role,saved] of Object.entries(routes))if(saved?.model)list.append(n('p',roleDisplayName(role)+' · '+[saved.model,saved.reasoning_effort].filter(Boolean).join(' · ')));
    if(!Object.keys(routes).length)list.append(n('p','The saved conversation configuration will be used on your first message.'));
    list.append(n('p','No model has been called for this conversation.'));
    models.replaceChildren(n('summary',label),list);models.dataset.scope=signature;
  }
  models.hidden=false;
}
