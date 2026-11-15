"""Fresh metadata requires its original helper's authenticated completion."""

import json
import os
import select
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace


@unittest.skipUnless(sys.platform == "linux", "Linux capture child adoption")
class MetadataRecoveryTests(unittest.TestCase):
    @contextmanager
    def owned_capture(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ready, calls = root / "ready.json", root / "calls"
            tool = root / "kilo"
            tool.write_text(
                f"#!{sys.executable}\n"
                "import json,os,pathlib,sys\n"
                f"calls=pathlib.Path({str(calls)!r})\n"
                "previous=calls.exists()\n"
                "with calls.open('a') as stream: stream.write('auth\\n')\n"
                "if previous: print('● Offline fixture api',flush=True); sys.exit(0)\n"
                "def identity(pid):\n"
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
            handles = []
            first = sentinel = None
            try:
                first = subprocess.Popen(
                    [*command, "connections", "check", "--provider", "kilocode", "--json"],
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
                deadline = time.monotonic() + 15
                while not ready.exists():
                    self.assertIsNone(first.poll(), "Core exited before the native fault was ready")
                    self.assertLess(time.monotonic(), deadline, "backend did not become ready")
                    time.sleep(0.01)
                row = json.loads(ready.read_text())
                for name in ("root", "worker", "helper"):
                    identity = row[name]
                    path = Path(f"/proc/{identity['pid']}/stat")
                    before = path.read_text().rsplit(")", 1)[1].split()
                    self.assertEqual(identity["birth"], before[19])
                    descriptor = os.pidfd_open(identity["pid"])
                    try:
                        observed = path.read_text().rsplit(")", 1)[1].split()
                        self.assertEqual(identity["birth"], observed[19])
                        if name == "helper":
                            self.assertEqual(first.pid, int(observed[1]))
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

                yield SimpleNamespace(first=first, sentinel=sentinel, handles=descriptors, calls=calls, public=public)
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

    def test_lost_helper_blocks_fresh_public_checks_without_relaunching_backend(self):
        with self.owned_capture() as fixture:
            signal.pidfd_send_signal(fixture.handles["helper"], signal.SIGKILL)
            stdout, stderr = fixture.first.communicate(timeout=15)
            self.assertEqual(0, fixture.first.returncode, stderr)
            failed = json.loads(stdout)
            self.assertFalse(select.select([fixture.handles["root"], fixture.handles["worker"]], [], [], 0)[0])
            listed = fixture.public(["connections", "list", "--json"])
            kilo = next(item for item in listed["connections"] if item["id"] == "kilocode")
            forced = fixture.public(["connections", "check", "--provider", "kilocode", "--json"])
            self.assertEqual(
                {"statuses": ["check_failed"] * 3, "actions": [[], [], []], "backend_calls": ["auth"]},
                {
                    "statuses": [item["connection_status"] for item in (failed, kilo, forced)],
                    "actions": [item["actions"] for item in (failed, kilo, forced)],
                    "backend_calls": fixture.calls.read_text().splitlines(),
                },
                "a fresh Core must retain uncertainty and refuse a second backend launch",
            )
            self.assertIsNone(fixture.sentinel.poll(), "metadata recovery touched an unrelated process")

    def test_core_loss_collects_owned_family_and_clean_receipt_admits_a_fresh_public_check(self):
        with self.owned_capture() as fixture:
            fixture.first.kill()
            fixture.first.communicate(timeout=5)
            self.assertEqual(-signal.SIGKILL, fixture.first.returncode)
            self.assertTrue(
                select.select([fixture.handles["helper"]], [], [], 10)[0],
                "surviving helper did not finish owner-EOF cleanup within its bound",
            )
            descendants = [fixture.handles["root"], fixture.handles["worker"]]
            self.assertEqual(
                descendants,
                select.select(descendants, [], [], 0)[0],
                "helper completion must follow the old backend family's exit",
            )
            self.assertIsNone(fixture.sentinel.poll(), "owner-EOF cleanup touched an unrelated process")
            checked = fixture.public(["connections", "check", "--provider", "kilocode", "--json"])
            self.assertEqual("credentials_present", checked["connection_status"])
            self.assertEqual(["auth", "auth"], fixture.calls.read_text().splitlines())
            self.assertIsNone(fixture.sentinel.poll(), "fresh metadata touched an unrelated process")


if __name__ == "__main__":
    unittest.main()
