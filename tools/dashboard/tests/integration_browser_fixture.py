"""Real runner + fake providers for the complete local browser workflow.

Only disposable projects are used; PATH resolves both providers to local fixtures.
The hold-terra file is a deterministic barrier for live feedback/Pause checks.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import threading

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--runner', type=Path, required=True)
parser.add_argument('--root', type=Path)
parser.add_argument('--port', type=int, default=0)
args = parser.parse_args()
fixture_home = Path(__file__).resolve().parents[1] / '.autocode/browser-fixtures'
fixture_home.mkdir(parents=True, exist_ok=True)
root = (args.root or Path(tempfile.mkdtemp(prefix='autocode-browser-integration-', dir=fixture_home))).resolve()
root.mkdir(parents=True, exist_ok=True)
bin_dir = root/'bin'
bin_dir.mkdir(exist_ok=True)
source = args.runner.resolve().parent
if not (root/'fixture-ready').exists():
    for name, target in [('fake_codex.py', 'codex'), ('fake_opencode.py', 'opencode'), ('goal_fixtures.py', 'goal_fixtures.py')]:
        shutil.copy2(source/name, bin_dir/target)
    codex = bin_dir/'codex'
    text = codex.read_text()
    anchor = 'stage = data["stage"]\n'
    assert anchor in text
    text = text.replace(anchor, anchor+'''
barrier_root = Path(os.environ["AUTOCODE_BROWSER_FIXTURE"])
with (barrier_root / "provider-launches.jsonl").open("a") as stream:
    stream.write(json.dumps({"stage": stage, "workspace": str(Path.cwd())}) + "\\n")
if stage == "terra" and (barrier_root / "hold-terra").exists():
    import time
    (barrier_root / "terra-entered").write_text(str(Path.cwd()))
    deadline = time.monotonic() + 600
    while (barrier_root / "hold-terra").exists():
        if time.monotonic() > deadline:
            raise SystemExit("Fixture barrier timed out")
        time.sleep(0.05)
''')
    codex.write_text(text)
    for name in ('codex','opencode'):
        (bin_dir/name).chmod(0o755)
    for name in ('outside-root-project', 'browser-created-project'):
        project = root/name
        project.mkdir(exist_ok=True)
        subprocess.run(['git','init','-q',str(project)],check=True)
        subprocess.run(['git','-C',str(project),'-c','user.name=Fixture','-c','user.email=fixture@example.test',
                        'commit','--allow-empty','-qm','Disposable browser fixture'],check=True)
    (root/'legacy-root').mkdir()
    (root/'runtime-root').mkdir()
    (root/'fixture-ready').write_text('Disposable fixture; never real provider access.\n')

os.environ.update(PATH=str(bin_dir)+os.pathsep+os.environ['PATH'], AUTOCODE_HOME=str(root/'registry-home'),
                  AUTOCODE_BROWSER_FIXTURE=str(root), PYTHONDONTWRITEBYTECODE='1')
assert shutil.which('opencode') == str(bin_dir/'opencode')
assert shutil.which('codex') == str(bin_dir/'codex')
sys.dont_write_bytecode=True
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console, Handler, ThreadingHTTPServer

console = Console([], args.runner.resolve(), lambda: None, conversation_root=root/'conversations',
                  conversation_provider=lambda messages, model, workdir: 'Let’s plan the change. What outcome matters most? Attach the disposable project when ready for repository-aware review.')
server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
server.console = console
server.hosts = {'127.0.0.1:'+str(server.server_port), 'localhost:'+str(server.server_port)}
threading.Thread(target=server.serve_forever, daemon=True).start()
metadata = {'root':str(root), 'url':'http://127.0.0.1:'+str(server.server_port),
            'runner':str(args.runner.resolve()), 'pid':os.getpid(), 'autocode_home':os.environ['AUTOCODE_HOME'],
            'outside_project':str(root/'outside-root-project'), 'new_project':str(root/'browser-created-project'),
            'provider_bin':str(bin_dir), 'hold_terra':str(root/'hold-terra')}
(root/'server.json').write_text(json.dumps(metadata,indent=2)+'\n')
print(json.dumps(metadata,indent=2),flush=True)
stopped = threading.Event()
def stop(*_): stopped.set()
signal.signal(signal.SIGTERM,stop)
signal.signal(signal.SIGINT,stop)
stopped.wait()
server.shutdown()
server.server_close()
console.pool.shutdown(wait=True)
