"""Additional handwritten product contracts and predeclared executable oracles."""
import textwrap


def dashboard(make_plan):
    html='''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Lifecycle fixture</title>
<style>body{font:18px system-ui;margin:24px;max-width:700px}button{font:inherit;padding:12px;margin:4px}#status{overflow:visible;white-space:normal}</style>
<h1>Local task monitor</h1><p id="status" role="status">RUNNING</p>
<button id="pause">Pause</button><button id="resume">Resume</button><button id="cancel">Cancel</button>
<script>let state='RUNNING';const status=document.querySelector('#status');function render(){status.textContent=state;document.querySelector('#pause').disabled=state!=='RUNNING';document.querySelector('#resume').disabled=state!=='PAUSED';document.querySelector('#cancel').disabled=state==='CANCELLED'}document.querySelector('#pause').onclick=()=>{state='PAUSED';render()};document.querySelector('#resume').onclick=()=>{state='RUNNING';render()};document.querySelector('#cancel').onclick=()=>{state='CANCELLED';render()};render();</script></html>'''
    return make_plan([([], 'Local-only dashboard index.html: #status RUNNING; #pause -> PAUSED; #resume -> RUNNING; #cancel -> CANCELLED. Disable inapplicable controls; all disabled after cancel. Mobile text visible.', ['web/index.html']),
                      ([], 'Document that this is a local simulation, not remote process control', ['README.md'])],
        {'M1':{'web/index.html':html},'M2':{'README.md':'Local simulation only; this fixture does not control remote processes.\n'}},
        {'M1':"from pathlib import Path; p=Path('web/index.html').read_text(); assert all(x in p for x in ['RUNNING','PAUSED','CANCELLED','<button'])",
         'M2':"from pathlib import Path; assert 'simulation' in Path('README.md').read_text().lower()"},
        'Build a local monitoring dashboard lifecycle simulation; browser behavior needs separate verification')


def client_server(make_plan):
    files = {
        'M1': {'shared.py': "USER_PATH = '/users/'\n"},
        'M2': {'server.py': textwrap.dedent('''\
            import json
            from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
            from shared import USER_PATH
            class Handler(BaseHTTPRequestHandler):
                def do_GET(self):
                    if not self.path.startswith(USER_PATH):
                        self.send_error(404); return
                    raw=json.dumps({'id':self.path[len(USER_PATH):]}).encode()
                    self.send_response(200); self.end_headers(); self.wfile.write(raw)
                def log_message(self,*args): pass
            def create(): return ThreadingHTTPServer(('127.0.0.1',0),Handler)
            ''')},
        'M3': {'client.py': "import json\nfrom urllib.request import urlopen\nfrom shared import USER_PATH\ndef fetch(base,uid): return json.load(urlopen(base+USER_PATH+uid))\n"},
        'M4': {'integration.py': textwrap.dedent('''\
            from threading import Thread
            from server import create
            from client import fetch
            def check():
                server=create(); thread=Thread(target=server.serve_forever,daemon=True); thread.start()
                try: assert fetch('http://127.0.0.1:'+str(server.server_port),'42') == {'id':'42'}
                finally: server.shutdown(); server.server_close(); thread.join()
            if __name__=='__main__': check()
            ''')}}
    return make_plan([([], 'Publish USER_PATH=/users/ in shared.py', ['shared.py']),
                      (['M1'], 'HTTP server create() binds loopback ephemeral port; /users/42 returns JSON id=42', ['server.py']),
                      (['M1'], 'Client fetch(base,uid) returns user JSON from shared route', ['client.py']),
                      (['M2','M3'], 'Integration check starts server and uses real HTTP client', ['integration.py'])], files,
        {'M1': "from shared import USER_PATH; assert USER_PATH=='/users/'",
         'M2': "from server import create; s=create(); assert s.server_address[0]=='127.0.0.1'; s.server_close()",
         'M3': "from client import fetch; assert callable(fetch)",
         'M4': "from integration import check; check()"}, 'Build a compatible HTTP client and server around an authoritative shared route')


def duplicate_api(make_plan):
    server=textwrap.dedent('''\
        import json, threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        def create():
            records={}; lock=threading.Lock()
            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    key=self.headers.get('Idempotency-Key')
                    payload=self.rfile.read(int(self.headers.get('Content-Length','0')))
                    if not key: self.send_error(400); return
                    with lock:
                        if key in records and records[key][0]!=payload:
                            self.send_error(409); return
                        records.setdefault(key,(payload,len(records)+1))
                        value={'id':records[key][1],'count':len(records)}
                    self.send_response(200); self.end_headers(); self.wfile.write(json.dumps(value).encode())
                def log_message(self,*args): pass
            return ThreadingHTTPServer(('127.0.0.1',0),Handler)
        ''')
    oracle=textwrap.dedent('''\
        import concurrent.futures, json, threading
        from urllib.request import Request,urlopen
        from urllib.error import HTTPError
        from api import create
        server=create(); t=threading.Thread(target=server.serve_forever,daemon=True); t.start()
        base='http://127.0.0.1:'+str(server.server_port)
        def send(i):
            return json.load(urlopen(Request(base,data=b'same',headers={'Idempotency-Key':'k'})))
        try:
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool: results=list(pool.map(send,range(20)))
            assert all(r=={'id':1,'count':1} for r in results),results
            try: urlopen(Request(base,data=b'changed',headers={'Idempotency-Key':'k'}))
            except HTTPError as e: assert e.code==409
            else: raise AssertionError('Conflicting payload accepted')
        finally: server.shutdown(); server.server_close(); t.join()
        ''')
    return make_plan([([], 'In-memory HTTP create(): same idempotency key/payload has one effect; changed payload returns 409; missing key 400', ['api.py']),
                      ([], 'Document process-lifetime-only idempotency in README.md', ['README.md'])],
                     {'M1':{'api.py':server},'M2':{'README.md':'Idempotency is process-lifetime only; no restart durability.\n'}},
                     {'M1':oracle,'M2':"from pathlib import Path; assert 'process-lifetime' in Path('README.md').read_text()"},
                     'Build a duplicate-safe local HTTP API with an explicit in-memory lifetime')


def registry(make_plan):
    """Go result oracle is handwritten; C# execution is a separate prerequisite."""
    files={'M1':{'go.mod':'module fixture\n\ngo 1.22\n', 'registry/registry.go':'package registry\ntype Registry interface { Lookup(string) string }\n'},
           'M2':{'registry/registry_a.go':'package registry\ntype A struct{}\nfunc(A) Lookup(t string) string { return "A:"+t }\n'},
           'M3':{'registry/registry_b.go':'package registry\ntype B struct{}\nfunc(B) Lookup(t string) string { return "B:"+t }\n'},
           'M4':{'registry/de.go':'package registry\nfunc DE() string { return (A{}).Lookup("de") }\n'},
           'M5':{'registry/ca.go':'package registry\nfunc CA() string { return (B{}).Lookup("ca") }\n'},
           'M6':{'registry/parity_test.go':'package registry\nimport "testing"\nfunc TestParity(t *testing.T){ if DE()!="A:de" || CA()!="B:ca" || (A{}).Lookup("com")!="A:com" {t.Fatal("parity failed")} }\n'}}
    rows=[([], 'Define Registry interface Lookup(string) string in module fixture', ['go.mod','registry/registry.go']),
          (['M1'], 'Registry A returns A: plus TLD', ['registry/registry_a.go']),
          (['M1'], 'Registry B returns B: plus TLD', ['registry/registry_b.go']),
          (['M2'], 'DE() uses Registry A and returns A:de', ['registry/de.go']),
          (['M3'], 'CA() uses Registry B and returns B:ca', ['registry/ca.go']),
          (['M4','M5'], 'Assert synthetic reference outputs A:de, B:ca, A:com', ['registry/parity_test.go'])]
    checks={f'M{i}':f"from pathlib import Path; import subprocess; assert Path('{next(iter(files[f'M{i}']))}').exists(); subprocess.run(['go','test','./registry'],check=True)" for i in range(1,7)}
    spec=make_plan(rows, files, checks, 'Port a synthetic registry and two TLD overrides to Go')
    spec['contract']['technical_approach']=['Go standard library; synthetic C# reference behavior']
    spec['contract']['constraints']=['No third-party Go dependencies']
    return spec
