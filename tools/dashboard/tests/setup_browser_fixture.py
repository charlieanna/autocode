"""Real HTTP/project setup with offline diagnostics; never installs or signs in."""
import json, os, sys
from pathlib import Path
SOURCE=Path(__file__).resolve().parents[3]
sys.path[:0]=[str(SOURCE/'tools/dashboard'),str(SOURCE/'tools')]
from agent_console import Console,Handler,LoopbackHTTPServer
from dashboard_setup import git
root=Path(os.environ['AUTOCODE_FIXTURE_ROOT']).resolve();root.mkdir(parents=True,exist_ok=True)
os.environ['AUTOCODE_HOME']=str(root/'registry')
report=root/'doctor.json'
report.write_text(json.dumps({'ok':False,'checks':[{'name':name,'status':'missing','detail':'SECRET_UI_fixture_token','fix':'SECRET_UI_fixture_token'}for name in ['python','psutil','git','engine:opencode','engine:codex','engine','workspace']]}))
runner=root/'runner.py'
runner.write_text('import json,sys\nfrom pathlib import Path\nsys.path.insert(0,'+repr(str(SOURCE/'tools'))+')\nif sys.argv[1]=="registry":\n from autocode_registry import cli\n raise SystemExit(cli(sys.argv[2:]))\nif sys.argv[1]=="doctor":\n d=json.loads(Path(__file__).with_name("doctor.json").read_text());print(json.dumps(d));raise SystemExit(0 if d.get("ok") else 1)\nraise SystemExit("No live model or task allowed")\n')
catalogue=root/'catalogue.py';catalogue.write_text("print('openai/gpt-6-sol\\nopenai/gpt-5.6-terra\\nxiaomi-token-plan-singapore/mimo-v2.6-pro')\n")
console=Console([],runner,lambda:False,conversation_root=root/'dashboard/conversations',project_store_root=root/'dashboard',catalogue_command=(sys.executable,str(catalogue)))
dirty=root/'dirty';dirty.mkdir();git(dirty,'init','-q');git(dirty,'-c','user.name=Fixture','-c','user.email=fixture@invalid','commit','--allow-empty','-qm','fixture');(dirty/'keep.txt').write_text('human changes remain')
server=LoopbackHTTPServer(('127.0.0.1',0),Handler);server.console=console;server.hosts={'127.0.0.1:'+str(server.server_port)}
print('FIXTURE='+json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'root':str(root),'dirty':str(dirty),'report':str(report)}),flush=True)
try:server.serve_forever()
finally:server.server_close();console.pool.shutdown(wait=True);console.conversations.close(wait=True)
