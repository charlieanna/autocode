"""Owned native metadata families shared by public recovery regressions."""

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


class MetadataCaptureFixture:
    @contextmanager
    def owned_capture(self, *, wrapper_depth=0, ignore_term=False):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready, calls = root / "ready.json", root / "calls"
            tool = root / "kilo"
            tool.write_text(
                f"#!{sys.executable}\n"
                "import json,os,pathlib,signal,sys\n"
                f"calls=pathlib.Path({str(calls)!r})\n"
                "previous=calls.exists()\n"
                "with calls.open('a') as stream: stream.write('auth\\n')\n"
                "if previous: print('● Offline fixture api',flush=True); sys.exit(0)\n"
                + ("signal.signal(signal.SIGTERM,signal.SIG_IGN)\n" if ignore_term else "")
                + "def identity(pid):\n"
                " row=pathlib.Path(f'/proc/{pid}/stat').read_text().rsplit(')',1)[1].split()\n"
                " return {'pid':pid,'birth':row[19]}\n"
                "parent=os.getpid(); helper=os.getppid()\n"
                "reader,writer=os.pipe()\n"
                "worker=os.fork()\n"
                "if worker==0:\n"
                " os.setsid(); os.close(reader); os.write(writer,b'R'); os.close(writer)\n"
                " r,w=os.pipe(); os.read(r,1); os._exit(0)\n"
                "os.close(writer); assert os.read(reader,1)==b'R'; os.close(reader)\n"
                "row={'root':identity(parent),'worker':identity(worker),'helper':identity(helper)}\n"
                f"ready=pathlib.Path({str(ready)!r}); temporary=ready.with_suffix('.tmp')\n"
                "temporary.write_text(json.dumps(row)); temporary.replace(ready)\n"
                "r,w=os.pipe(); os.read(r,1)\n"
            )
            tool.chmod(0o755)
            env = {
                **os.environ,
                "HOME": str(root),
                "XDG_STATE_HOME": str(root / "state"),
                "XDG_CONFIG_HOME": str(root / "config"),
                "XDG_DATA_HOME": str(root / "data"),
                "AUTOCODE_HOME": str(root / "autocode"),
                "PATH": os.pathsep.join((str(root), str(Path(sys.executable).parent), "/usr/bin", "/bin")),
            }
            command = [sys.executable, str(Path(__file__).resolve().parents[1] / "tools" / "autocode.py")]
            launch = [*command, "connections", "check", "--provider", "kilocode", "--json"]
            outcome = None
            for level in range(wrapper_depth):
                driver, outcome = root / f"reader-{level}.py", root / f"outcome-{level}.json"
                if level == 0:
                    work = (
                        " from autocode_chat_client import History\n"
                        f" result=History(Path({str(root)!r}),command={command!r},env=dict(os.environ)).read("
                        "['connections','check','--provider','kilocode','--json'])\n"
                        " reply={'kind':'result','connection_status':result['connection_status']}\n"
                    )
                else:
                    work = (
                        " import autocode_captured_process as capture\n"
                        f" result=capture.run({launch!r},env=dict(os.environ),timeout=60)\n"
                        " if result.returncode: raise RuntimeError('nested reader failed')\n"
                        f" reply=json.loads(Path({str(root / f'outcome-{level - 1}.json')!r}).read_text())\n"
                    )
                driver.write_text(
                    "import json,os,sys\nfrom pathlib import Path\n"
                    f"sys.path.insert(0,{str(Path(__file__).resolve().parents[1] / 'tools')!r})\n"
                    "try:\n" + work + "except KeyboardInterrupt: reply={'kind':'interrupted'}\n"
                    "except BaseException as error: reply={'kind':'error','type':type(error).__name__}\n"
                    f"Path({str(outcome)!r}).write_text(json.dumps(reply))\n"
                )
                launch = [sys.executable, str(driver)]
            handles = []
            first = sentinel = None
            try:
                first = subprocess.Popen(
                    launch,
                    cwd=root,
                    env=env,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                )
                sentinel = subprocess.Popen(
                    [sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE
                )
                deadline = time.monotonic() + (30 if wrapper_depth else 15)
                while not ready.exists():
                    self.assertIsNone(first.poll(), "Core exited before the native fault was ready")
                    self.assertLess(time.monotonic(), deadline, "backend did not become ready")
                    time.sleep(0.01)
                row = json.loads(ready.read_text())
                helper_parent = int(Path(f"/proc/{row['helper']['pid']}/stat").read_text().rsplit(")", 1)[1].split()[1])
                ancestor = helper_parent
                for number in range(2 * wrapper_depth):
                    fields = Path(f"/proc/{ancestor}/stat").read_text().rsplit(")", 1)[1].split()
                    row[f"ancestor_{number}"] = {"pid": ancestor, "birth": fields[19]}
                    ancestor = int(fields[1])
                self.assertEqual(first.pid, ancestor, "the native family must belong to this exact reader")
                for name in row:
                    identity = row[name]
                    path = Path(f"/proc/{identity['pid']}/stat")
                    before = path.read_text().rsplit(")", 1)[1].split()
                    self.assertEqual(identity["birth"], before[19])
                    descriptor = os.pidfd_open(identity["pid"])
                    try:
                        observed = path.read_text().rsplit(")", 1)[1].split()
                        self.assertEqual(identity["birth"], observed[19])
                        if name == "helper":
                            self.assertEqual(helper_parent, int(observed[1]))
                    except BaseException:
                        os.close(descriptor)
                        raise
                    handles.append((name, descriptor))
                descriptors = dict(handles)
                self.assertFalse(
                    select.select(list(descriptors.values()), [], [], 0)[0],
                    "all owned fixture processes must be live before fault injection",
                )

                def public(arguments):
                    result = subprocess.run(
                        [*command, *arguments], cwd=root, env=env, capture_output=True, text=True, timeout=15
                    )
                    self.assertEqual(0, result.returncode, result.stderr)
                    return json.loads(result.stdout)

                yield SimpleNamespace(
                    first=first, sentinel=sentinel, handles=descriptors, calls=calls, public=public, outcome=outcome
                )
            finally:
                cleanup_errors = []
                try:
                    if first is not None and first.poll() is None:
                        first.kill()
                        first.communicate(timeout=5)
                except (OSError, subprocess.SubprocessError) as error:
                    cleanup_errors.append(str(error))
                for _name, descriptor in handles:
                    try:
                        if not select.select([descriptor], [], [], 0)[0]:
                            signal.pidfd_send_signal(descriptor, signal.SIGKILL)
                            if not select.select([descriptor], [], [], 5)[0]:
                                cleanup_errors.append("Owned fixture process did not exit")
                    except OSError as error:
                        cleanup_errors.append(str(error))
                    finally:
                        os.close(descriptor)
                if sentinel is not None:
                    try:
                        sentinel.stdin.close()
                        sentinel.wait(timeout=5)
                        self.assertEqual(0, sentinel.returncode)
                    except (OSError, subprocess.SubprocessError) as error:
                        cleanup_errors.append(str(error))
                self.assertEqual([], cleanup_errors, "native recovery fixture did not collect its own processes")
