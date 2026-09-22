#!/usr/bin/env python3
"""Loopback-only UI for autocode's documented command-per-turn CLI."""
import argparse,json,os,re,selectors,signal,subprocess,sys,threading,time,tomllib,uuid
from concurrent.futures import ThreadPoolExecutor
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlparse
sys.dont_write_bytecode=True
CODEX_DEFAULT_MODELS={'astra':'gpt-5.6-sol','terra':'gpt-5.6-terra','sol':'gpt-5.6-sol','completion':'gpt-5.6-sol'}
GLM_MODELS={'astra':'glm-5.3','terra':'glm-5.3-flash','sol':'glm-5.3','completion':'glm-5.3'}
DEFAULT_REASONING_EFFORTS={'astra':'high','terra':'medium','sol':'high','completion':'medium'}
REASONING_EFFORTS={'low','medium','high','xhigh','max'}
BARE_OPENAI_ALIASES={'gpt-5.6-sol','gpt-5.6-terra','gpt-6-astra'}
MODEL_ID=re.compile(r'^[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._:/-]{0,120}$',re.I)
def obj(x): return x if isinstance(x,dict) else {}
def items(x): return x if isinstance(x,list) else []
def json_file(p):
 try:
  x=json.loads(p.read_text(encoding='utf8'));return x if isinstance(x,dict) else {'_console_error':'state is not a JSON object'}
 except (OSError,ValueError) as e:return {'_console_error':str(e)}
def configured_zai(config_path=None):
 try:data=tomllib.loads((Path(config_path) if config_path else Path.home()/'.codex/config.toml').read_text(encoding='utf8'))
 except (OSError,tomllib.TOMLDecodeError):return None
 for k,v in obj(data.get('model_providers')).items():
  if isinstance(v,dict) and k.lower().replace('.','')=='zai':return k
 return None
def string_list(x):return [v for v in items(x) if isinstance(v,str)]
def saved_models(state):
 state=obj(state);settings=obj(state.get('settings'));roles=obj(settings.get('roles'));models=obj(state.get('models'));result={};engines={};efforts={}
 engine=settings.get('engine') if isinstance(settings.get('engine'),str) else state.get('engine') if isinstance(state.get('engine'),str) else None
 names=['astra','terra','sol']+(['completion'] if 'completion' in roles or 'completion' in models else [])+(['glm'] if 'glm' in roles or 'glm' in models else [])
 for role in names:
  config=obj(roles.get(role));value=config.get('model')
  if not isinstance(value,str):value=models.get(role)
  result[role]=value if isinstance(value,str) else None
  engines[role]=config.get('engine') if isinstance(config.get('engine'),str) else engine if config or result[role] is not None else None
  effort=config.get('reasoning_effort')
  efforts[role]=effort if isinstance(effort,str) else None
 return {'engine':engine,'joint_planning':settings.get('joint_planning') is True,'roles':result,'role_engines':engines,'role_efforts':efforts}
def discovery_role(state):
 state=obj(state);joint=obj(state.get('settings')).get('joint_planning') is True
 for record in reversed(items(state.get('stages'))):
  record=obj(record)
  if record.get('rejected') or record.get('timed_out') or record.get('interrupted') or record.get('exit_code') not in (None,0):continue
  stage=record.get('original_stage') if record.get('applied_original_events') else record.get('stage')
  if stage not in ('astra_discovery','astra_challenge','glm_revise','astra_finalize'):continue
  if record.get('role') in ('astra','glm'):return record['role']
  return 'glm' if joint and stage in ('astra_discovery','glm_revise') else 'astra'
 origin=obj(state.get('goal_contract')).get('origin')
 if origin in ('astra_discovery','astra_finalize'):return 'astra'
 if joint and origin in ('glm_draft','glm_revise'):return 'glm'
 return None
class ModelCatalogue:
    def __init__(self, command=('opencode', 'models'), ttl=300, timeout=10, output_limit=65536):
        self.command = tuple(command)
        self.ttl, self.timeout, self.output_limit = ttl, timeout, output_limit
        self.lock = threading.Condition()
        self.models, self.at, self.loading, self.error = None, 0, False, None

    def _read_command(self):
        deadline = time.monotonic() + self.timeout
        process = subprocess.Popen(list(self.command), stdin=subprocess.DEVNULL,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
        streams = selectors.DefaultSelector()
        output = {'stdout': bytearray(), 'stderr': bytearray()}
        total = 0
        try:
            for name in output:
                streams.register(getattr(process, name), selectors.EVENT_READ, name)
            while streams.get_map():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise subprocess.TimeoutExpired(self.command, self.timeout)
                for key, _ in streams.select(remaining):
                    chunk = os.read(key.fd, min(4096, self.output_limit - total + 1))
                    if not chunk:
                        streams.unregister(key.fileobj)
                        continue
                    total += len(chunk)
                    if total > self.output_limit:
                        raise ValueError('Model catalogue output exceeded the safe limit')
                    output[key.data].extend(chunk)
            code = process.wait(timeout=max(.001, deadline - time.monotonic()))
            return code, output['stdout'].decode('utf8', errors='replace'), output['stderr'].decode('utf8', errors='replace')
        except BaseException:
            # Only this catalogue lookup and its children share the new group.
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=1)
            raise
        finally:
            streams.close()
            process.stdout.close()
            process.stderr.close()

    def fetch(self, refresh=False):
        with self.lock:
            if self.loading:
                self.lock.wait_for(lambda: not self.loading, self.timeout + 2)
                return self.status()
            if not refresh and not self.error and self.models is not None and time.monotonic() - self.at < self.ttl:
                return self.status()
            self.loading, self.error = True, None
        values, error = None, None
        try:
            code, stdout, stderr = self._read_command()
            if code:
                raise ValueError((stderr.strip() or 'opencode models exited with status ' + str(code))[:400])
            values = sorted({line.strip() for line in stdout.splitlines() if MODEL_ID.fullmatch(line.strip())})
            if not values:
                raise ValueError('No usable provider/model identifiers were returned')
        except subprocess.TimeoutExpired:
            error = 'Model catalogue lookup timed out.'
        except (OSError, ValueError) as failure:
            error = 'Model catalogue unavailable: ' + str(failure)
        finally:
            with self.lock:
                if not error and values:
                    self.models, self.at = values, time.monotonic()
                self.error, self.loading = error, False
                self.lock.notify_all()
        return self.status()

    def status(self):
        with self.lock:
            return {'models': list(self.models or []), 'usable': bool(self.models) and not self.error and not self.loading,
                    'loading': self.loading, 'error': self.error}
def assignment_snapshot(raw,source,at=None,reason=None):
 raw=obj(raw)
 if not raw:return None
 snapshot={'source':source,'id':raw.get('id') if isinstance(raw.get('id'),str) else None,
  'objective':raw.get('objective') if isinstance(raw.get('objective'),str) else None,
  'requirements':string_list(raw.get('requirements')),'validation_plan':string_list(raw.get('validation_plan')),
  'owner':raw.get('owner') if isinstance(raw.get('owner'),str) else raw.get('assigned_role') if isinstance(raw.get('assigned_role'),str) else None,
  'next_role':raw.get('next_role') if isinstance(raw.get('next_role'),str) else None,
  'timestamp':raw.get('assigned_at') if isinstance(raw.get('assigned_at'),str) else at if isinstance(at,str) else None,
  'brief_revision':raw.get('contract_revision') if type(raw.get('contract_revision')) is int else None,
  'decision':raw.get('decision') if isinstance(raw.get('decision'),str) else None,
  'reason':reason if isinstance(reason,str) else raw.get('reason') if isinstance(raw.get('reason'),str) else raw.get('rationale') if isinstance(raw.get('rationale'),str) else None}
 return snapshot if any(snapshot[k] is not None for k in ('id','objective','timestamp','brief_revision','decision','reason')) or snapshot['requirements'] or snapshot['validation_plan'] else None
def astra_plan_state(s):
 s=obj(s);history=[];by_identity={};decisions=[]
 def add(entry):
  if not entry:return None
  # Assignment and review timestamps are distinct; they do not identify a task.
  key=('task',entry['id'],entry['brief_revision']) if entry['id'] else ('legacy',json.dumps(entry,sort_keys=True))
  if key not in by_identity:by_identity[key]=entry;history.append(entry)
  else:
   existing=by_identity[key]
   for field,value in entry.items():
    if value is not None and value!=[] and (not existing.get(field) or field in ('source','next_role','next_stage','reason')):existing[field]=value
  return by_identity[key]
 for raw in items(s.get('task_archive')):add(assignment_snapshot(raw,'archived assignment'))
 for raw in items(s.get('decisions')):
  decision=obj(raw);report=obj(decision.get('report'));reason=decision.get('reason') or decision.get('rationale')
  if not isinstance(reason,str):reason='\n'.join(string_list(report.get('evidence'))) or report.get('blocker') or None
  entry=assignment_snapshot(decision.get('current_task'),'recorded decision',decision.get('at'),reason)
  next_stage=decision.get('next_stage') if isinstance(decision.get('next_stage'),str) else None
  if entry:
   entry['next_stage']=next_stage
   if not entry['next_role'] and next_stage in ('astra','terra','sol'):entry['next_role']=next_stage
   add(entry)
  plan=string_list(decision.get('plan')) or string_list(report.get('plan'))
  if plan or isinstance(report.get('status'),str) or isinstance(reason,str):
   decisions.append({'timestamp':decision.get('at') if isinstance(decision.get('at'),str) else None,
    'iteration':decision.get('iteration') if type(decision.get('iteration')) is int else None,
    'brief_revision':report.get('contract_revision') if type(report.get('contract_revision')) is int else None,
    'status':report.get('status') if isinstance(report.get('status'),str) else None,
    'plan':plan,'reason':reason if isinstance(reason,str) else None,'next_stage':next_stage})
 current=add(assignment_snapshot(s.get('current_task'),'current assignment'))
 history.sort(key=lambda entry:(entry['timestamp'] is None,entry['timestamp'] or ''))
 decisions.sort(key=lambda entry:(entry['timestamp'] is None,entry['timestamp'] or ''))
 contract=obj(s.get('goal_contract'));briefs=[];seen=set();approved_tokens=set()
 for raw in items(s.get('user_events')):
  event=obj(raw)
  if event.get('kind')=='goal_approval' and isinstance(event.get('token'),str):approved_tokens.add(event['token'])
 for raw in items(s.get('contract_history'))+[contract]:
  value=obj(raw);body=obj(value.get('body'))
  if not body:continue
  revision=value.get('revision') if type(value.get('revision')) is int else None
  contract_hash=value.get('hash') if isinstance(value.get('hash'),str) else None
  key=(revision,contract_hash or json.dumps(body,sort_keys=True))
  if key in seen:continue
  seen.add(key)
  token='r'+str(revision)+':'+contract_hash if revision is not None and contract_hash is not None else None
  briefs.append({'revision':revision,'approval_status':value.get('approval_status') if isinstance(value.get('approval_status'),str) else 'unavailable',
   'timestamp':obj(value.get('approval_event')).get('at') or value.get('created_at'),
   'current':value is contract,'historically_approved':token in approved_tokens if token else False,'body':body})
 briefs.sort(key=lambda value:(value['revision'] is None,value['revision'] or 0))
 initial=string_list(s.get('initial_plan'));initial_status=s.get('initial_plan_approval') if isinstance(s.get('initial_plan_approval'),str) else None
 if not initial:
  approved=next((brief for brief in briefs if brief['historically_approved']),None)
  if approved:
   initial=[milestone['objective'] for milestone in items(approved['body'].get('milestones')) if isinstance(milestone,dict) and isinstance(milestone.get('objective'),str)]
   initial=initial or string_list(approved['body'].get('technical_approach'))
   initial_status='historically approved/inactive' if not approved['current'] and approved['approval_status']!='approved' else 'approved'
 if not initial:
  for raw in items(s.get('contract_history')):
   value=obj(raw);plan=string_list(value.get('plan'))
   if plan:initial=plan;initial_status=value.get('approval_status') if isinstance(value.get('approval_status'),str) else None;break
 if not initial:
  first=next((decision for decision in decisions if decision['plan']),None)
  if first:initial=first['plan'];initial_status=None
 revisions=[]
 for raw in items(s.get('contract_history')):
  value=obj(raw);approval=value.get('approval_status') if isinstance(value.get('approval_status'),str) else None
  revision=value.get('revision') if type(value.get('revision')) is int else None
  at=value.get('created_at') if isinstance(value.get('created_at'),str) else obj(value.get('approval_event')).get('at')
  contract_hash=value.get('hash') if isinstance(value.get('hash'),str) else None
  token='r'+str(revision)+':'+contract_hash if revision is not None and contract_hash is not None else None
  if approval is not None or revision is not None or isinstance(at,str):revisions.append({'revision':revision,'approval_status':approval,'historically_approved':token in approved_tokens if token else False,'timestamp':at if isinstance(at,str) else None})
 return {'initial_plan':initial,'initial_plan_approval':initial_status,'current_plan':string_list(s.get('plan')),
  'current_plan_approval':contract.get('approval_status') if isinstance(contract.get('approval_status'),str) else None,
  'current_assignment':current,'history':history,'briefs':briefs,'decisions':decisions,'revision_history':revisions}
class LegacyConsole:
 def __init__(self,workspaces,runner,zai_probe=configured_zai,watch_roots=(),watch_depth=3,watch_ttl=4.0,catalogue_command=('opencode','models')):
  self.explicit=list(dict.fromkeys(Path(x).resolve() for x in workspaces));self._explicit_set=set(self.explicit);self.created_workspaces=[];self.runner=str(Path(runner).resolve());self.zai_probe=zai_probe;self.catalogue=ModelCatalogue(catalogue_command);self.actions={};self.pending=set();self.workspace_busy=set();self.lock=threading.Lock();self.cli_watch_roots=list(dict.fromkeys(Path(x).resolve() for x in watch_roots));self.runtime_watch_roots=[];self.watch_depth=max(0,int(watch_depth));self.watch_ttl=max(0.0,float(watch_ttl));self.scan_lock=threading.Lock();self.discovery_cache={};self.pool=ThreadPoolExecutor(max_workers=max(4,len(self.explicit)))
 @property
 def watch_roots(self):return self.cli_watch_roots+self.runtime_watch_roots
 def watch_root_rows(self):
  return [{'path':str(root),'runtime':root in self.runtime_watch_roots,'removable':root in self.runtime_watch_roots} for root in self.watch_roots]
 def add_runtime_watch_root(self,raw):
  if not isinstance(raw,str) or not raw.strip():raise ValueError('Watch root path is required')
  try:root=Path(raw).expanduser().resolve(strict=True)
  except (OSError,ValueError):raise ValueError('Watch root must be an existing directory')
  if not root.is_dir():raise ValueError('Watch root must be an existing directory')
  with self.scan_lock:
   if root in self.cli_watch_roots or root in self.runtime_watch_roots:raise ValueError('Watch root is already configured')
   self.runtime_watch_roots.append(root);self.discovery_cache.pop(str(root),None)
  return root
 def remove_runtime_watch_root(self,raw):
  if not isinstance(raw,str):raise ValueError('Watch root path is required')
  try:root=Path(raw).expanduser().resolve(strict=False)
  except (OSError,ValueError):raise ValueError('Watch root is not a removable runtime root')
  with self.scan_lock:
   if root in self.cli_watch_roots:raise ValueError('CLI watch roots cannot be removed at runtime')
   if root not in self.runtime_watch_roots:raise ValueError('Watch root is not a removable runtime root')
   self.runtime_watch_roots.remove(root);self.discovery_cache.pop(str(root),None)
  return root
 @property
 def workspaces(self):
  found=self._discovered();ws=list(self.explicit)+list(self.created_workspaces)
  for w in sorted(found,key=str):
   if w not in ws:ws.append(w)
  return list(dict.fromkeys(ws))
 def _refresh_discovery(self):
  now=time.time();entries=[]
  for root in self.watch_roots:
   e=self.discovery_cache.get(str(root))
   if e is None or now-e['at']>=self.watch_ttl:
    found,error=self._scan_watch_root(root);e={'at':now,'found':found,'error':error};self.discovery_cache[str(root)]=e
   entries.append((root,e))
  return entries
 def _discovered(self):
  with self.scan_lock:
   found=set()
   for _,e in self._refresh_discovery():found|=e['found']
   return found
 def root_error_rows(self):
  with self.scan_lock:return [{'workspace':str(root),'error':e['error']} for root,e in self._refresh_discovery() if e['error']]
 def _scan_watch_root(self,root):
  found=set()
  try:
   # A Git repository is a discovery boundary. Descending into repositories
   # makes a broad watch root walk dependency trees, build output and every
   # saved Autocode artifact on each cache refresh.
   if (root/'.git').exists():
    if (root/'.autocode/runs').is_dir():found.add(root)
    return found,None
   stack=[(root,0)]
   while stack:
    base,level=stack.pop()
    try:entries=list(os.scandir(base))
    except OSError as e:
     if level==0:return found,'watch root unavailable: '+str(e)
     continue
    for entry in entries:
     if entry.name in ('.git','.autocode','node_modules','.next','.venv','venv','__pycache__') or level>=self.watch_depth:continue
     try:
      isdir=entry.is_dir(follow_symlinks=False);resolved=Path(entry.path).resolve();resolved.relative_to(root)
      if (resolved/'.git').exists():
       if (resolved/'.autocode/runs').is_dir():found.add(resolved)
       continue
      if isdir:stack.append((Path(entry.path),level+1))
     except (OSError,ValueError):continue
  except OSError as e:return found,'watch root unavailable: '+str(e)
  return found,None
 def workspace_for(self,raw):
  try:p=Path(raw).resolve(strict=True)
  except (OSError,ValueError):return None
  return p if p in self.workspaces and p.is_dir() and (p/'.git').exists() else None
 def selected_workspace(self,raw):
  if not isinstance(raw,str) or not raw.strip():return None
  try:p=Path(raw).expanduser().resolve(strict=True)
  except (OSError,ValueError):return None
  return p if p.is_dir() and (p/'.git').exists() else None
 def run_for(self,ws,raw):
  if not ws or not isinstance(raw,str):return None
  try:r=Path(raw).resolve(strict=True);root=(ws/'.autocode/runs').resolve(strict=True);root.relative_to(ws);r.relative_to(root);(r/'state.json').resolve(strict=True).relative_to(root)
  except (OSError,ValueError):return None
  return r if r.is_dir() and (r/'state.json').is_file() else None
 def view(self,ws,run,s=None):
  s=obj(s) if s is not None else json_file(run/'state.json');contract=obj(s.get('goal_contract'));body=obj(contract.get('body'));criteria=items(body.get('acceptance_criteria')) or items(s.get('acceptance_criteria'));result={str(x.get('id')):str(x.get('status','unknown')).lower() for x in items(obj(s.get('validation')).get('criterion_results')) if isinstance(x,dict)};counts={'pass':0,'fail':0,'unknown':0}
  for c in criteria:
   status=result.get(str(obj(c).get('id')),'unknown');counts['pass' if status in ('pass','verified') else 'fail' if status in ('fail','blocked') else 'unknown']+=1
  active=obj(s.get('active_stage'))
  return {'workspace':str(ws),'project_workspace':s.get('project_workspace',str(ws)),'task_branch':s.get('task_branch'),'run':str(run),'created_at':s.get('created_at'),'task':s.get('task','unavailable'),'phase':s.get('phase','unavailable'),'status':s.get('status','unavailable'),'stage':active.get('stage') or s.get('stage') or s.get('next_stage') or 'unavailable','iteration':s.get('iteration','unavailable'),'state_error':s.get('_console_error'),'stop_reason':s.get('stop_reason'),'goal':contract,'goal_token':s.get('displayed_goal') if isinstance(s.get('displayed_goal'),str) else '','criteria':criteria,'counts':counts,'questions':items(s.get('pending_questions')),'answers':obj(s.get('answers')),'discovery_summary':s.get('discovery_summary') if isinstance(s.get('discovery_summary'),str) else '','discovery_role':discovery_role(s),'stages':[x for x in items(s.get('stages')) if isinstance(x,dict)],'active_stage':active,'plan':items(s.get('plan')),'astra_plan':astra_plan_state(s),'user_request':s.get('user_request') if isinstance(s.get('user_request'),dict) else None,'review_token':s.get('displayed_review') if isinstance(s.get('displayed_review'),str) else '','review_criteria':[obj(c) for c in criteria if obj(c).get('human_review')],'human_reviews':obj(s.get('human_reviews')),'reasoning_escalations':items(s.get('reasoning_escalations')),'zai':bool(self.zai_probe()),'model_settings':saved_models(s)}
 def discover(self):
  rows=self.root_error_rows()
  for ws in self.workspaces:
   root=ws/'.autocode/runs'
   try:children=list(root.iterdir()) if root.is_dir() else []
   except OSError as e:rows.append({'workspace':str(ws),'error':'runs unavailable: '+str(e)});continue
   for p in children:
    if not p.is_dir():continue
    run=self.run_for(ws,str(p))
    if not run:rows.append({'workspace':str(ws),'run':str(p),'error':'run escapes watched workspace or state is unavailable'});continue
    s=json_file(run/'state.json');rows.append({'workspace':str(ws),'run':str(run),'error':'state unavailable: '+s['_console_error']} if '_console_error' in s else self.view(ws,run,s))
  return rows
 def _record(self,key,label,cmd):
  x={'id':uuid.uuid4().hex,'label':label,'command':cmd,'queued_at':time.time(),'status':'queued'}
  with self.lock:self.actions.setdefault(key,[]).append(x)
  return x
 def enqueue(self,ws,run,label,extra,on_complete=None):
  isolated=run is None
  key=str(run) if run else str(ws)+':new:'+uuid.uuid4().hex
  with self.lock:
   if key in self.pending or (not isolated and str(ws) in self.workspace_busy):raise ValueError('A run or workspace action is already queued or running')
   self.pending.add(key)
   if not isolated:self.workspace_busy.add(str(ws))
  cmd=[sys.executable,self.runner,'--workspace',str(ws)]+(['--run-dir',str(run)] if run else [])+list(extra);x=self._record(str(run or ws),label,cmd);x['isolated_task']=isolated;self.pool.submit(self._execute,key,str(ws),x,on_complete);return x
 def _execute(self,key,ws,x,on_complete=None):
  x['status']='running';x['started_at']=time.time()
  try:
   # A dashboard restart must not disconnect the runner's output or kill its
   # process group. Full logs live beside the workspace's runner metadata;
   # only bounded tails are copied into the dashboard response.
   logs=Path(ws)/'.autocode'/'dashboard-actions'/x['id'];logs.mkdir(parents=True,mode=0o700)
   out_path,err_path=logs/'stdout.log',logs/'stderr.log'
   x.update(stdout_path=str(out_path),stderr_path=str(err_path))
   with out_path.open('xb') as out,err_path.open('xb') as err:
    os.chmod(out_path,0o600);os.chmod(err_path,0o600)
    p=subprocess.Popen(x['command'],stdin=subprocess.DEVNULL,stdout=out,stderr=err,start_new_session=True)
    x['pid']=p.pid
    (logs/'action.json').write_text(json.dumps(x),encoding='utf8')
    code=p.wait()
   def tail(path):
    with path.open('rb') as handle:
     handle.seek(0,os.SEEK_END);size=handle.tell();handle.seek(max(0,size-131072))
     return ('[Earlier output is in the saved log]\n' if size>131072 else '')+handle.read().decode('utf8',errors='replace')
   x.update(stdout=tail(out_path),stderr=tail(err_path),exit_status=code,status='finished' if code==0 else 'failed')
  except Exception as e:x.update(stdout='',stderr=str(e),exit_status=None,status='launch_failed',uncertain=True)
  finally:
   x['finished_at']=time.time()
   with self.lock:
    self.pending.discard(key)
    if not x.get('isolated_task'):self.workspace_busy.discard(ws)
  if on_complete:
   try:on_complete(x)
   except Exception as error:x['callback_error']=str(error)
 def action_log(self,ws,run=None):
  try:key=str(Path(run or ws).resolve())
  except OSError:key=str(run or ws)
  return list(self.actions.get(key,[]))
 def joint_models(self,d):
  explicit={role:d.get(role+'_model','') for role in ('glm','astra','terra','sol','completion')}
  if any(not isinstance(value,str) for value in explicit.values()):raise ValueError('Model choices must be strings')
  chosen={role:value for role,value in explicit.items() if value}
  # Retain bare OpenAI aliases in older conversations. The runner expands them
  # to openai/model on OpenCode; they never select a separate Codex login.
  opencode_choices={role:value for role,value in chosen.items()
                    if not (role in ('astra','sol','completion') and value in BARE_OPENAI_ALIASES)}
  for role,value in opencode_choices.items():
   if not MODEL_ID.fullmatch(value):raise ValueError(role.title()+' requires an OpenCode provider/model identifier')
  if opencode_choices:
   catalogue=self.catalogue.fetch()
   if not catalogue['usable']:raise ValueError(catalogue['error'] or 'Model catalogue is unavailable; reset role choices to Use Autocode default')
   for value in opencode_choices.values():
    if value not in catalogue['models']:raise ValueError('Choose a current provider/model identifier from the catalogue')
  return chosen
 def joint_efforts(self,d):
  explicit={role:d.get(role+'_reasoning_effort','') for role in ('astra','terra','sol','completion')}
  if any(not isinstance(value,str) for value in explicit.values()):raise ValueError('Reasoning choices must be strings')
  if any(value and value not in REASONING_EFFORTS for value in explicit.values()):raise ValueError('Choose a supported reasoning level')
  return {role:value for role,value in explicit.items() if value}
 def create(self,d):
  raw=d.get('project') if isinstance(d.get('project'),str) and d.get('project').strip() else d.get('workspace','');ws=self.selected_workspace(raw);goal=d.get('goal','');engine=d.get('engine','opencode')
  if not ws:raise ValueError('Select or enter an existing Git workspace')
  if not isinstance(goal,str) or not goal.strip():raise ValueError('A non-empty goal is required')
  if engine not in ('opencode','codex'):raise ValueError('Unsupported engine')
  if ws not in self.created_workspaces and ws not in self.explicit:self.created_workspaces.append(ws)
  if engine=='opencode':
   chosen=self.joint_models(d);efforts=self.joint_efforts(d)
   extra=[goal,'--engine','opencode','--joint-planning','--no-chat']
   for role,value in chosen.items():extra+=['--'+role+'-model',value]
   for role,value in efforts.items():extra+=['--'+role+'-reasoning-effort',value]
   return self.enqueue(ws,None,'Create OpenCode task',extra)
  if d.get('glm_model'):raise ValueError('GLM discovery requires the default joint-planning engine')
  models={r:d.get(r+'_model',v) for r,v in CODEX_DEFAULT_MODELS.items()};provider=self.zai_probe()
  for r,m in models.items():
   if m not in (CODEX_DEFAULT_MODELS[r],GLM_MODELS[r]):raise ValueError('Unsupported model')
   if m==GLM_MODELS[r] and not provider:raise ValueError('Z.ai is not configured in local Codex')
  efforts={**DEFAULT_REASONING_EFFORTS,**self.joint_efforts(d)}
  extra=[goal,'--engine','codex','--no-chat']
  for role,model in models.items():extra+=['--'+role+'-model',model]
  for role,value in efforts.items():extra+=['--'+role+'-reasoning-effort',value]
  for r,m in models.items():
   if m==GLM_MODELS[r]:extra+=['--'+r+'-provider',provider]
  return self.enqueue(ws,None,'Create Codex task',extra)
 def mutate(self,d):
  ws=self.workspace_for(d.get('workspace',''));run=self.run_for(ws,d.get('run',''))
  if not run:raise ValueError('Run does not belong to the selected watched workspace')
  v=self.view(ws,run);action=d.get('action');pending={str(obj(q).get('id')) for q in v['questions']}
  if action=='answer':
   ident,text=str(d.get('id','')),d.get('text','')
   if ident not in pending or not isinstance(text,str) or not text.strip():raise ValueError('Question is not currently pending')
   return self.enqueue(ws,run,'Answer '+ident,['--answer',ident+'='+text])
  if action=='delegate':
   ident=str(d.get('id',''))
   if ident not in pending:raise ValueError('Question is not currently pending')
   return self.enqueue(ws,run,'Delegate '+ident,['--delegate',ident])
  if action=='approve_goal':
   token=d.get('token','')
   if not isinstance(token,str) or token!=v['goal_token'] or d.get('confirmation')!=token:raise ValueError('Displayed goal token changed or confirmation does not match')
   return self.enqueue(ws,run,'Approve goal',['--approve-goal',token])
  if action=='approve_review':
   ident,token=str(d.get('id','')),d.get('token','')
   if ident not in {str(c.get('id')) for c in v['review_criteria']} or not isinstance(token,str) or token!=v['review_token']:raise ValueError('Displayed review token changed or criterion is not eligible')
   return self.enqueue(ws,run,'Approve review '+ident,['--approve-review',ident,'--review-token',token])
  if action=='set_reasoning':
   efforts=self.joint_efforts(d)
   if not efforts:raise ValueError('Choose at least one reasoning level')
   if v.get('active_stage'):raise ValueError('Wait for the current model step to finish before changing reasoning')
   extra=[]
   for role,value in efforts.items():extra+=['--'+role+'-reasoning-effort',value]
   return self.enqueue(ws,run,'Save reasoning settings',extra+['--show-goal','--no-chat'])
  if action=='continue':return self.enqueue(ws,run,'Continue',[])
  raise ValueError('Unknown action')
try:
 from .dashboard_backend import RegistryInterventionMixin
 from .dashboard_chat import ConversationMixin
 from .dashboard_project_controls import ProjectRemovalMixin
 from .dashboard_tasks import TaskArchiveMixin
 from .dashboard_evidence import stage_evidence
except ImportError:  # Support running this file directly from a source checkout.
 from dashboard_backend import RegistryInterventionMixin
 from dashboard_chat import ConversationMixin
 from dashboard_project_controls import ProjectRemovalMixin
 from dashboard_tasks import TaskArchiveMixin
 from dashboard_evidence import stage_evidence

class Console(TaskArchiveMixin, ProjectRemovalMixin, ConversationMixin, RegistryInterventionMixin, LegacyConsole):
 pass

# Static presentation is kept separate from the read-only adapter and mutation API.
INDEX = Path(__file__).with_name('dashboard.html').read_text()
STYLE = Path(__file__).with_name('dashboard.css').read_text()
APP = Path(__file__).with_name('dashboard_app.js').read_text()

class Handler(BaseHTTPRequestHandler):
 server_version='agent-console';protocol_version='HTTP/1.1'
 def log_message(self,*x):pass
 @property
 def console(self):return self.server.console
 def reply(self,c,x,t='application/json'):
  raw=x.encode() if isinstance(x,str) else json.dumps(x).encode();self.send_response(c);self.send_header('Content-Type',t+'; charset=utf-8');self.send_header('Content-Length',str(len(raw)));self.end_headers();self.wfile.write(raw)
 def same_origin(self):
  hosts=self.headers.get_all('Host') or []
  if len(hosts)!=1 or hosts[0] not in self.server.hosts:return False
  o=self.headers.get('Origin')
  if not o:return True
  try:p=urlparse(o);port=p.port
  except ValueError:return False
  return p.scheme=='http' and p.netloc==hosts[0] and p.hostname is not None and p.username is None and p.password is None and not(p.path or p.params or p.query or p.fragment)
 def do_GET(self):
  try:return self.get_request()
  except (BrokenPipeError,ConnectionResetError):return
  except (OSError,ValueError,TypeError) as error:
   try:return self.reply(503,{'error':str(error)})
   except (BrokenPipeError,ConnectionResetError):return
  except Exception as error:
   # A failed view must not close the socket or look like a fresh checkpoint.
   # Do not send state, credentials, or arbitrary exception text to the browser.
   print('Dashboard GET failed: '+type(error).__name__,file=sys.stderr,flush=True)
   try:return self.reply(500,{'error':'Task status could not be loaded. Saved work is unchanged; retry or check the dashboard server log.'})
   except (BrokenPipeError,ConnectionResetError):return
 def get_request(self):
  p=urlparse(self.path)
  if not self.same_origin():return self.reply(403,{'error':'cross-origin request rejected'})
  if p.path=='/api/conversations':return self.reply(200,{'conversations':self.console.conversation_list()})
  if p.path=='/api/conversation':
   try:return self.reply(200,self.console.conversation_get(parse_qs(p.query).get('id',[''])[0]))
   except (OSError,ValueError):return self.reply(404,{'error':'Conversation unavailable'})
  if p.path=='/':return self.reply(200,INDEX,'text/html')
  if p.path=='/static/style.css':return self.reply(200,STYLE,'text/css')
  if p.path=='/static/app.js':return self.reply(200,APP,'application/javascript')
  if p.path=='/api/runs':return self.reply(200,self.console.dashboard_snapshot())
  if p.path=='/api/models':return self.reply(200,self.console.catalogue.fetch())
  if p.path=='/api/evidence':
   q=parse_qs(p.query);raw=q.get('workspace',[''])[0];selected=q.get('run',[''])[0]
   if self.console.removed_project(raw) or self.console.archived_task(selected):return self.reply(404,{'error':'Restore this task and project to inspect its changes'})
   ws=self.console.workspace_for(raw);run=self.console.run_for(ws,selected)
   if not run:return self.reply(404,{'error':'Task unavailable'})
   try:return self.reply(200,stage_evidence(run,int(q['stage'][0]) if 'stage' in q else None,workspace=ws,file_index=int(q['file'][0]) if 'file' in q else None))
   except (ValueError,OSError):return self.reply(400,{'error':'Saved changes unavailable for this stage'})
  if p.path=='/api/run':
   archived=self.console.archived_task(parse_qs(p.query).get('run',[''])[0])
   if archived:return self.reply(200,self.console.archived_view(archived))
   q=parse_qs(p.query);raw=q.get('workspace',[''])[0];removed=self.console.removed_project(raw)
   if removed:return self.reply(200,{'project_removed':True,'workspace':removed['workspace'],'run':q.get('run',[''])[0],'task':'Removed project'})
   self.console.conversations
   ws=self.console.workspace_for(q.get('workspace',[''])[0]);run=self.console.run_for(ws,q.get('run',[''])[0]);return self.reply(200,self.console.task_view(ws,run)) if run else self.reply(404,{'error':'run unavailable'})
  self.reply(404,{'error':'not found'})
 def do_POST(self):
  if not self.same_origin():return self.reply(403,{'error':'cross-origin mutation rejected'})
  try:
   d=json.loads(self.rfile.read(int(self.headers.get('Content-Length','0'))));
   if not isinstance(d,dict):raise ValueError('JSON body must be an object')
   if self.path=='/api/projects':x=self.console.project_action(d)
   elif self.path=='/api/tasks':x=self.console.task_archive_action(d)
   elif self.path=='/api/conversations':x=self.console.conversation_create(d)
   elif self.path=='/api/conversation/archive':x=self.console.conversation_archive(d)
   elif self.path=='/api/conversation/message':
    self.console.require_unarchived_conversation(d.get('id'));x=self.console.conversations.send(d.get('id'),d.get('text'),d.get('request_id'))
   elif self.path=='/api/conversation/retry':
    self.console.require_unarchived_conversation(d.get('id'));x=self.console.conversations.retry(d.get('id'))
   elif self.path=='/api/conversation/attach':x=self.console.conversation_attach(d)
   elif self.path=='/api/chat':x=self.console.chat(d)
   elif self.path=='/api/create':x=self.console.create(d)
   elif self.path=='/api/action':x=self.console.mutate(d)
   elif self.path=='/api/models/refresh':x=self.console.catalogue.fetch(refresh=True)
   elif self.path=='/api/watch-roots':
    action=d.get('action');path=d.get('path')
    if action=='add':x={'path':str(self.console.add_runtime_watch_root(path))}
    elif action=='remove':x={'path':str(self.console.remove_runtime_watch_root(path))}
    else:raise ValueError('Unknown watch-root action')
   else:raise ValueError('not found')
   self.reply(202,x)
  except (BrokenPipeError,ConnectionResetError):return
  except (ValueError,TypeError,OSError,json.JSONDecodeError) as e:
   try:self.reply(400,{'error':str(e)})
   except (BrokenPipeError,ConnectionResetError):return
def main():
  p=argparse.ArgumentParser();p.add_argument('--workspace',action='append',default=[]);p.add_argument('--watch-root',action='append',default=[]);p.add_argument('--watch-depth',type=int,default=3);p.add_argument('--runner',default=str(Path(__file__).resolve().parents[1]/'autocode.py'));p.add_argument('--port',type=int,default=8765);a=p.parse_args()
  if a.watch_depth<0:p.error('--watch-depth must be zero or greater')
  c=Console(a.workspace,a.runner,watch_roots=a.watch_root,watch_depth=a.watch_depth);c._discovered();s=ThreadingHTTPServer(('127.0.0.1',a.port),Handler);s.console=c;s.hosts={'127.0.0.1:'+str(s.server_port),'localhost:'+str(s.server_port)};print('http://127.0.0.1:'+str(s.server_port),flush=True);s.serve_forever()
if __name__=='__main__':main()
