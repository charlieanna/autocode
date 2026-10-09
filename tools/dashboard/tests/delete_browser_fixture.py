"""Real deletion/archive endpoints, registry and Git; disposable fixture only."""
import json
import os
import sys
from pathlib import Path
from urllib.parse import urlencode

SOURCE=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(SOURCE/'tools/dashboard'),str(SOURCE/'tools')]
import autocode_registry as registry
from agent_console import Console, Handler, LoopbackHTTPServer
from autocode_workspaces import create as worktree
from autocode_worktrees import deliver
from dashboard_delete import git

root=Path(os.environ['AUTOCODE_FIXTURE_ROOT']).resolve()
root.mkdir(parents=True,exist_ok=True)
os.environ['AUTOCODE_HOME']=str(root/'registry')
project=root/'project'
manifest=root/'fixture.json'
if not manifest.exists():
    project.mkdir();git(project,'init','-q');(project/'keep.txt').write_text('project must survive')
    git(project,'add','keep.txt');git(project,'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture')
    cases={}
    for name in ['desktop','tablet','mobile','partial','stale','live','unknown','sibling']:
        managed=name in ['desktop','tablet','mobile','partial']
        meta=worktree(project,'Delete fixture '+name) if managed else None
        ws=Path(meta['workspace']) if managed else project
        run=ws/'.autocode/runs'/name;run.mkdir(parents=True)
        state={'workspace':str(ws),'project_workspace':str(project),'run_dir':str(run),'task':'Deletion fixture '+name,'task_id':'fixture-'+name,'status':'PAUSED_INTERVENTION','phase':'EXECUTING','active_stage':None,'next_stage':'terra','updated_at':'2026-10-03T00:00:00+00:00','view':{'evidence':{'outcome':'Deletion fixture '+name}}}
        if name=='live':state.update(status='RUNNING',active_stage={'stage':'terra','pid':os.getpid()})
        if name=='unknown':state['active_stage_workers']={'checked':False}
        (run/'state.json').write_text(json.dumps(state));(run/'output.txt').write_text(name+' output')
        if name in ['desktop','tablet','mobile']:
            (ws/'feature.py').write_text('print("delivered '+name+'")\n')
            state.update(status='TASK_COMPLETE',next_stage=None)
            assert 'Delivered on branch' in deliver(state,ws)
            assert git(ws,'rev-parse','--abbrev-ref','HEAD')=='HEAD'
            (run/'state.json').write_text(json.dumps(state))
        registry.register_run(ws,run,state)
        cases[name]={'workspace':str(ws),'run':str(run),'branch':meta['branch'] if meta else None,'head':git(project,'rev-parse','HEAD')}
    manifest.write_text(json.dumps(cases))
    (root/'fail-registry-once').write_text('controlled failure')
cases=json.loads(manifest.read_text())
runner=root/'fixture_runner.py'
runner.write_text('import json,sys\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(SOURCE/'tools'))+')\nfrom autocode_registry import cli\nif sys.argv[1]=="registry":raise SystemExit(cli(sys.argv[2:]))\nargs=sys.argv\nif "--status" in args:\n print((Path(args[args.index("--run-dir")+1])/"state.json").read_text())\nelse:raise SystemExit(2)\n')
class FixtureConsole(Console):
    def model_catalogue(self,refresh=False):
        return {'models':[], 'error':'Offline deletion fixture: no provider requests'}
    def _forget_deleted(self,preview):
        marker=root/'fail-registry-once'
        if Path(preview['run']).name=='partial' and marker.exists():
            marker.unlink();raise ValueError('Injected discovery cleanup failure; files and branch are already removed')
        return super()._forget_deleted(preview)

console=FixtureConsole([project],runner,lambda:False,conversation_root=root/'dashboard/conversations')
server=LoopbackHTTPServer(('127.0.0.1',0),Handler);server.console=console
server.hosts={'127.0.0.1:'+str(server.server_port)}
base='http://127.0.0.1:'+str(server.server_port)
print('FIXTURE='+json.dumps({'base_url':base,'root':str(root),'project':str(project),'cases':{name:{**data,'url':base+'/#'+urlencode({'task':data['workspace'],'run':data['run']})}for name,data in cases.items()}}),flush=True)
try:server.serve_forever()
finally:server.server_close();console.pool.shutdown(wait=True);console.conversations.close(wait=True)
