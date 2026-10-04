"""Three real, disposable projects and saved empty chats; no live provider."""
import json,os,sys,threading,http.client
from pathlib import Path
SOURCE=Path(os.environ.get('AUTOCODE_TEST_SOURCE_ROOT',Path(__file__).resolve().parents[3]))
sys.path[:0]=[str(SOURCE/'tools/dashboard'),str(SOURCE/'tools')]
from agent_console import Console,Handler,LoopbackHTTPServer
root=Path(os.environ['AUTOCODE_FIXTURE_ROOT']).resolve();root.mkdir(parents=True,exist_ok=True)
os.environ['AUTOCODE_HOME']=str(root/'registry')
runner=root/'runner.py'
runner.write_text('import sys\nsys.path.insert(0,'+repr(str(SOURCE/'tools'))+')\nif sys.argv[1]=="registry":\n from autocode_registry import cli\n raise SystemExit(cli(sys.argv[2:]))\nraise SystemExit("No live task allowed")\n')
called=root/'provider-calls.txt';called.write_text('')
def forbid(*args,**kwargs):
 with called.open('a') as out:out.write('unexpected provider call\n')
 raise AssertionError('Opening or drafting an empty chat must not call a provider')
console=Console([],runner,lambda:False,conversation_root=root/'dashboard/conversations',project_store_root=root/'dashboard')
console.conversations.new.provider=forbid
server=LoopbackHTTPServer(('127.0.0.1',0),Handler);server.console=console;server.hosts={'127.0.0.1:'+str(server.server_port)}
threading.Thread(target=server.serve_forever,daemon=True).start()
projects=[]
try:
 for name in ['AutoCode','IdleCampus','dsa-tutor']:
  c=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=30)
  c.request('POST','/api/setup/project',json.dumps({'mode':'new','path':str(root/name),'request_id':'scoped-start-'+name}),{'Content-Type':'application/json'})
  response=c.getresponse();doc=json.loads(response.read());c.close()
  assert response.status==202,doc
  projects.append({'name':name,'workspace':doc['project'],'conversation':doc['conversation']['id']})
 print('FIXTURE='+json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'projects':projects,'provider_calls':str(called)}),flush=True)
 threading.Event().wait()
finally:
 server.shutdown();server.server_close();console.pool.shutdown(wait=True);console.conversations.close(wait=True)
