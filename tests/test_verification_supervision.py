"""Public runner-check owner-loss fault, with event barriers and native cleanup.

The disposable TaskRun driver is killed before the CLI owner, so the client's
outer process supervisor cannot supply the cleanup being tested. No model calls,
private state writes, wall-clock outcome oracle, or test-limit changes.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import shlex
import signal
import socket
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest

import psutil

import autocode_process as processes
import autocode_supervision as supervision
import autocode_taskrun as taskrun

TOOLS = Path(__file__).resolve().parents[1] / "tools"
BRIEF = ("Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
         "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
         "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
         "Python standard library only.")
OPTIONS = ("--engine", "codex", "--astra-model", "gpt-6-astra",
           "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--completion-model",
           "gpt-6-astra")


class VerificationSupervisionTests(unittest.TestCase):
    def setUp(self):
        retained = os.environ.get("VERIFICATION_SUPERVISION_ARTIFACTS")
        if retained:
            Path(retained).mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix="public-crash-", dir=retained)).resolve()
        else:
            temporary = tempfile.TemporaryDirectory(prefix="verification-owner-loss-")
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name).resolve()
        # Unix socket paths have a small native size limit. Keep this one short,
        # even when the diagnostic artifacts are retained under a long checkout.
        sockets = tempfile.TemporaryDirectory(prefix="verify-barrier-")
        self.addCleanup(sockets.cleanup)
        self.endpoint = str(Path(sockets.name) / "events")
        self.server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(self.server.close)
        self.server.bind(self.endpoint)
        self.server.listen(4)
        self.server.settimeout(30)
        self.known = {}
        self.driver = None
        self.metadata = None
        self.addCleanup(self.cleanup_owned)

    def retain(self, name, value):
        (self.root / name).write_text(json.dumps(value, indent=2, sort_keys=True))

    def capture(self, pid):
        current = processes.process_table({pid}).get(pid)
        self.assertIsNotNone(current, f"Fixture process {pid} disappeared before its barrier")
        saved = processes.identity(current)
        self.known[pid] = saved
        return saved

    def signal_identity(self, saved, sig):
        current = processes.process_table({saved["pid"]}).get(saved["pid"])
        if processes.matches(saved, current) and not current["state"].startswith("Z"):
            os.kill(saved["pid"], sig)

    def cleanup_owned(self):
        # Preserve the first inspection error while ensuring it cannot bypass
        # actual-owner reap, authenticated keeper chance, or native fallback.
        errors = []

        def attempt(action):
            try:
                return action()
            except Exception as error:
                if not errors:
                    errors.append(error)

        if self.driver is not None:
            tree = processes.ProcessTree(self.driver.pid, lambda rows: None)
            saved = self.known.get(self.driver.pid)
            tree.known = {self.driver.pid: saved} if saved else {}
            # Capture descendants before a failed first barrier loses ancestry.
            attempt(lambda: tree.sample(notify=False))
            self.known.update(tree.known)
            if self.driver.poll() is None:
                attempt(self.driver.kill)
            attempt(lambda: self.driver.wait(timeout=10))
        if self.metadata is not None:
            attempt(lambda: self.signal_identity(self.metadata["owner"], signal.SIGKILL))
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                receipt = attempt(lambda: supervision.receipt(self.metadata))
                if receipt:
                    def retain_inventory():
                        for row in receipt.get("processes", []):
                            self.known[row["pid"]] = row
                    attempt(retain_inventory)
                if receipt and receipt.get("phase") in ("stopped", "uncertain", "discharged"):
                    break
                threading.Event().wait(.02)
        for row in list(self.known.values()):
            attempt(lambda row=row: self.signal_identity(row, signal.SIGKILL))
        if self.metadata is not None:
            attempt(lambda: self.signal_identity(self.metadata["keeper"], signal.SIGKILL))
        deadline = time.monotonic() + 10
        live = attempt(lambda: processes.live_processes(list(self.known.values())))
        while live and time.monotonic() < deadline:
            threading.Event().wait(.02)
            live = attempt(lambda: processes.live_processes(list(self.known.values())))
        if errors:
            raise errors[0]
        self.assertEqual([], live, "Captured fixture process leaked")

    def event(self):
        connection, _ = self.server.accept()
        self.addCleanup(connection.close)
        connection.settimeout(30)
        data = bytearray()
        while b"\n" not in data:
            chunk = connection.recv(4096)
            self.assertTrue(chunk, "Fixture ended before its event barrier")
            data.extend(chunk)
            self.assertLessEqual(len(data), 4096)
        event = json.loads(bytes(data).partition(b"\n")[0])
        self.capture(event["pid"])
        return event

    def public_status(self, run):
        path = run.run_dir / "state.json"
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        result = subprocess.run([*run.command, "--status", "--workspace", str(run.workspace),
                                 "--run-dir", str(run.run_dir)], capture_output=True, text=True,
                                check=True, timeout=run.timeout, env={**os.environ, **(run.env or {})})
        self.last_status = json.loads(result.stdout)
        self.assertEqual(before, hashlib.sha256(path.read_bytes()).hexdigest(), "Public status mutated run state")
        return self.last_status["view"]

    def prepare_prerequisite(self):
        workspace = self.root / "project"
        workspace.mkdir()
        worker = ("import json,os,signal,socket; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                  f"channel=socket.socket(socket.AF_UNIX); channel.connect({self.endpoint!r}); "
                  "channel.sendall((json.dumps({'kind':'worker','pid':os.getpid(),'parent':os.getppid(),"
                  "'term_ignored':True})+'\\n').encode()); channel.recv(1); signal.pause()")
        fixture = f"""import json,os,socket,subprocess,sys
subprocess.Popen([sys.executable,'-u','-c',{worker!r}],start_new_session=True)
channel=socket.socket(socket.AF_UNIX)
channel.connect({self.endpoint!r})
channel.sendall((json.dumps({{'kind':'command','pid':os.getpid(),'parent':os.getppid(),'cwd':os.getcwd()}})+'\\n').encode())
channel.recv(1)
"""
        (workspace / "probe.py").write_text(fixture)
        (workspace / ".gitignore").write_text(".autocode/\n__pycache__/\n")
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        for key in ("maintenance.auto", "gc.auto"):
            subprocess.run(["git", "-C", str(workspace), "config", key, "false" if key == "maintenance.auto" else "0"], check=True)
        subprocess.run(["git", "-C", str(workspace), "add", "probe.py", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(workspace), "-c", "user.name=T", "-c", "user.email=t@example.test",
                        "commit", "-qm", "public prerequisite fixture"], check=True)
        manifest = self.root / "preflight.json"
        manifest.write_text(json.dumps({"version": 1, "inputs": [], "runtime_files": [], "checks": [
            {"id": "ownership", "phase": "planning", "argv": [sys.executable, "probe.py"],
             "recovery": "Inspect and repair the owned prerequisite before resuming", "reuse": True}]}))
        bindir = self.root / "bin"
        bindir.mkdir()
        # Setup/auth probing remains offline. Any attempted provider execution
        # fails visibly: the prerequisite must block before a model dispatch.
        provider = bindir / "codex"
        provider.write_text(f"""#!/usr/bin/env python3
import sys
from pathlib import Path
if sys.argv[1:] == ['login','status']:
    print('Logged in using ChatGPT (offline owner-loss fixture)')
else:
    Path({str(self.root / 'provider-called')!r}).write_text(repr(sys.argv))
    raise SystemExit(90)
""")
        provider.chmod(0o755)
        environment = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                       "AUTOCODE_HOME": str(self.root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.assertEqual([], taskrun.TaskRun.runs_in(workspace))
        return workspace, manifest, environment

    def test_public_command_owner_loss_stops_command_and_detached_worker(self):
        worker_code = ("import json,os,signal,socket; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                       f"channel=socket.socket(socket.AF_UNIX); channel.connect({self.endpoint!r}); "
                       "channel.sendall((json.dumps({'kind':'worker','pid':os.getpid(),'parent':os.getppid(),"
                       "'term_ignored':True})+'\\n').encode()); channel.recv(1); signal.pause()")
        command_file = self.root / "owned-command.py"
        command_file.write_text(f"""import json,os,socket,subprocess,sys
subprocess.Popen([sys.executable,'-u','-c',{worker_code!r}],start_new_session=True)
channel=socket.socket(socket.AF_UNIX)
channel.connect({self.endpoint!r})
channel.sendall((json.dumps({{'kind':'command','pid':os.getpid(),'parent':os.getppid()}})+'\\n').encode())
channel.recv(1)
""")
        sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE,
                                    start_new_session=True)
        self.addCleanup(lambda: sentinel.stdin.close())
        self.addCleanup(lambda: sentinel.wait(timeout=10))
        self.addCleanup(lambda: sentinel.kill() if sentinel.poll() is None else None)
        sentinel_identity = processes.identity(processes.process_table({sentinel.pid})[sentinel.pid])
        command = shlex.join([sys.executable, "-u", str(command_file)])
        script = f"""import json,sys
sys.path.insert(0,{str(TOOLS)!r})
from autocode_verify import run_command
from pathlib import Path
result=run_command({command!r},{str(self.root)!r},{str(self.root / 'command.log')!r})
Path({str(self.root / 'collected-result.json')!r}).write_text(json.dumps(result))
"""
        with (self.root / "driver.log").open("wb") as output:
            self.driver = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
        owner_identity = self.capture(self.driver.pid)
        events = {event["kind"]: event for event in (self.event(), self.event())}
        command_event, worker = events["command"], events["worker"]
        self.assertEqual(command_event["pid"], worker["parent"])
        self.assertTrue(worker["term_ignored"])
        worker_identity = self.known[worker["pid"]]
        self.assertEqual(worker["pid"], worker_identity["group"])
        ancestors = psutil.Process(command_event["pid"]).parents()
        self.assertIn(self.driver.pid, [parent.pid for parent in ancestors])
        for parent in ancestors:
            if parent.pid == self.driver.pid:
                break
            self.capture(parent.pid)
        # A standalone public command has no run view. Its unique retained
        # sidecar is authenticated against the barrier-confirmed native owner
        # and command ancestry, then bound immutably across the fault.
        for path in self.root.rglob("supervision.json"):
            value = json.loads(path.read_text())
            candidate = {key: value[key] for key in
                         ("schema", "nonce", "owner", "keeper", "provider", "receipt", "started_at")}
            if (processes.matches(owner_identity, candidate["owner"])
                    and candidate["provider"]["pid"] in self.known
                    and processes.matches(self.known[candidate["provider"]["pid"]], candidate["provider"])):
                self.metadata = candidate
                break
        if self.metadata:
            keeper = processes.process_table({self.metadata["keeper"]["pid"]}).get(self.metadata["keeper"]["pid"])
            self.assertTrue(processes.matches(self.metadata["keeper"], keeper))
            self.assertEqual(self.driver.pid, keeper["parent"])
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                receipt = supervision.receipt(self.metadata)
                if receipt and any(processes.matches(worker_identity, row) for row in receipt.get("processes", [])):
                    break
                threading.Event().wait(.02)
            else:
                self.fail("Command keeper did not retain the barrier-confirmed detached worker")
            self.assertEqual("armed", receipt["phase"])
        self.retain("before-owner-loss.json", {"events": events, "identities": self.known,
                    "sentinel": sentinel_identity, "supervision": self.metadata})
        self.driver.kill()
        self.driver.wait(timeout=10)
        if self.metadata:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                receipt = supervision.receipt(self.metadata)
                if receipt and receipt.get("phase") in ("stopped", "uncertain"):
                    break
                threading.Event().wait(.02)
            else:
                self.fail("Command keeper never retained owner-loss cleanup")
        else:
            receipt = None
        live = processes.live_processes([self.known[command_event["pid"]], worker_identity])
        sentinel_live = processes.live_processes([sentinel_identity])
        self.retain("after-owner-loss.json", {"receipt": receipt, "live_workers": live,
                    "sentinel_live": sentinel_live, "owner_live": processes.live_processes([owner_identity]),
                    "collected_result": (self.root / "collected-result.json").exists()})
        self.assertTrue(sentinel_live, "Owner-loss cleanup touched an unrelated sentinel")
        self.assertFalse((self.root / "collected-result.json").exists(), "Dead owner collected a command result")
        if not self.metadata:
            self.assertEqual(2, len(live), "Unchanged source did not reproduce both orphan workers")
            self.fail("Unchanged public run_command left command and detached worker alive after owner loss: "
                      + repr([row["pid"] for row in live]))
        self.assertEqual("owner_lost", receipt["cause"])
        self.assertEqual("stopped", receipt["phase"], receipt)
        self.assertIsNone(receipt["cleanup_error"], receipt)
        self.assertEqual([], live, "Owned command or detached worker survived owner loss")

    def provider_calls(self):
        path = self.root / "provider-called"
        return path.read_text().splitlines() if path.exists() else []

    def taskrun_fault(self, workspace, environment, script, *, stage, provider_calls):
        sentinel = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE,
                                    start_new_session=True)
        self.addCleanup(lambda: sentinel.stdin.close())
        self.addCleanup(lambda: sentinel.wait(timeout=10))
        self.addCleanup(lambda: sentinel.kill() if sentinel.poll() is None else None)
        sentinel_identity = processes.identity(processes.process_table({sentinel.pid})[sentinel.pid])
        with (self.root / "driver.log").open("wb") as output:
            self.driver = subprocess.Popen([sys.executable, "-u", "-c", script], stdout=output,
                                           stderr=subprocess.STDOUT, start_new_session=True)
        self.capture(self.driver.pid)
        events = {event["kind"]: event for event in (self.event(), self.event())}
        command, worker = events["command"], events["worker"]
        self.assertEqual(command["pid"], worker["parent"])
        self.assertTrue(worker["term_ignored"])
        worker_identity = self.known[worker["pid"]]
        self.assertEqual(worker["pid"], worker_identity["group"], "Worker is not detached from the command group")
        ancestors = psutil.Process(command["pid"]).parents()
        owner = next((parent for parent in ancestors if parent.ppid() == self.driver.pid), None)
        self.assertIsNotNone(owner, "Verification command has no disposable CLI owner")
        self.assertIn(str(TOOLS / "autocode.py"), owner.cmdline())
        owner_identity = self.capture(owner.pid)
        for parent in ancestors:
            if parent.pid == self.driver.pid:
                break
            self.capture(parent.pid)
        run = taskrun.TaskRun.attach(workspace, options=OPTIONS, env=environment, timeout=300)
        self.assertIsNotNone(run, "Blocked start did not expose a public task run")
        before = self.public_status(run)
        self.assertEqual(stage, before["runner_check"]["stage"])
        if stage == "task_preflight":
            self.assertIn("ownership (workspace)", before["runner_check"]["summary"])
            self.assertEqual(str(workspace), command["cwd"])
        else:
            self.assertIn("review-proof/scratch/tree", command["cwd"])
            self.assertTrue(self.last_status["active_stage_finished"], "Result application began before provider collection")
            self.assertEqual(0, self.last_status["active_stage"]["exit_code"])
            self.assertIs(self.last_status["active_stage_workers"]["alive"], False)
            self.assertFalse((workspace / "review/findings.json").exists(), "Unproven review was accepted")
        self.assertEqual(provider_calls, self.provider_calls(), "Unexpected provider execution before command fault")
        self.assertIsNone(before["evidence"]["regression_proof"])
        self.metadata = before["runner_check"].get("supervision")
        if self.metadata:
            self.assertTrue(processes.matches(owner_identity, self.metadata["owner"]))
            self.assertEqual("supervised", before["runner_check"]["liveness"]["kind"])
            # Only authenticated keeper inventory establishes that the detached
            # child was sampled before the fault. Readiness alone is insufficient.
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                receipt = supervision.receipt(self.metadata)
                if receipt and any(processes.matches(worker_identity, row) for row in receipt.get("processes", [])):
                    break
                threading.Event().wait(.02)
            else:
                self.fail("Keeper did not retain the barrier-confirmed detached worker")
            self.assertEqual("armed", receipt["phase"])
        self.retain("before-owner-loss.json", {"status": self.last_status, "view": before, "events": events,
                    "identities": self.known, "sentinel": sentinel_identity, "supervision": self.metadata})
        # SIGKILL the advancing caller first. Its finally block cannot clean the
        # CLI or test tree, leaving the independent command keeper as the oracle.
        self.driver.kill()
        self.driver.wait(timeout=10)
        self.signal_identity(owner_identity, signal.SIGKILL)
        deadline = time.monotonic() + 10
        while processes.live_processes([owner_identity]) and time.monotonic() < deadline:
            threading.Event().wait(.02)
        self.assertFalse(processes.live_processes([owner_identity]))
        if self.metadata:
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline:
                receipt = supervision.receipt(self.metadata)
                if receipt and receipt.get("phase") in ("stopped", "uncertain"):
                    break
                threading.Event().wait(.02)
            else:
                self.fail("Verification keeper never recorded owner-loss cleanup")
        else:
            receipt = None
        after = self.public_status(run)
        live = processes.live_processes([self.known[command["pid"]], worker_identity])
        self.retain("after-owner-loss.json", {"status": self.last_status, "view": after, "receipt": receipt, "live_workers": live,
                    "sentinel_live": processes.live_processes([sentinel_identity])})
        if self.metadata is None:
            self.assertTrue(processes.live_processes([sentinel_identity]), "Fault touched the unrelated sentinel")
            self.assertIsNone(after["evidence"]["regression_proof"])
            self.assertTrue(live, "Unchanged source did not reproduce orphan verification workers")
            self.fail("Prerequisite workers survived CLI and driver loss without an independent keeper: "
                      + repr([row["pid"] for row in live]))
        self.assertEqual("owner_lost", receipt["cause"])
        self.assertEqual("stopped", receipt["phase"], receipt)
        self.assertIsNone(receipt["cleanup_error"], receipt)
        self.assertEqual([], live, "Runner command or detached TERM-resistant worker survived owner loss")
        self.assertTrue(processes.live_processes([sentinel_identity]), "Cleanup touched an unrelated sentinel")
        self.assertIsNone(after["evidence"]["regression_proof"], "Interrupted command supplied PASS or reusable proof")
        self.assertTrue(self.last_status["stale"], "Dead runner check was presented as current work")
        self.assertEqual("interrupted", after["runner_check"]["liveness"]["kind"])
        self.assertEqual("owner_lost", after["runner_check"]["liveness"]["reason"])
        self.assertFalse(after["done"], after)
        self.assertEqual([], after["evidence"]["acceptance"])
        self.assertNotEqual("READY", (after.get("task_preflight") or {}).get("status"))
        self.assertEqual(provider_calls, self.provider_calls(), "Owner loss dispatched another provider")
        if stage == "review_change":
            self.assertTrue(self.last_status["active_stage_finished"])
            self.assertIs(self.last_status["active_stage_workers"]["alive"], False)
            self.assertFalse((workspace / "review/findings.json").exists(), "Interrupted proof accepted a review")
        self.uncertain_retry_guard(run, workspace, provider_calls)

    def uncertain_retry_guard(self, run, workspace, provider_calls):
        # Inject only retained cleanup evidence after native ownership has ended.
        # The public relaunch must preserve that uncertainty before admitting any
        # replacement command or applying the previously finished review report.
        actors = [self.metadata["provider"], self.metadata["keeper"], *supervision.receipt(self.metadata)["processes"]]
        deadline = time.monotonic() + 10
        live = processes.live_processes(actors)
        while live and time.monotonic() < deadline:
            threading.Event().wait(.02)
            live = processes.live_processes(actors)
        self.assertEqual([], live, "Cannot inject cleanup uncertainty while an owned actor remains alive")
        receipt_path = Path(self.metadata["receipt"])
        original = json.loads(receipt_path.read_text())
        self.retain("owner-loss-original-receipt.json", original)
        uncertain = {**original, "phase": "uncertain", "cleanup_error": "injected cleanup uncertainty"}
        receipt_path.write_text(json.dumps(uncertain, indent=2))
        admissions = {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                      for path in self.root.rglob("admission.json")}
        retried = run.advance()
        after = self.public_status(run)
        after_admissions = {str(path.relative_to(self.root)): hashlib.sha256(path.read_bytes()).hexdigest()
                            for path in self.root.rglob("admission.json")}
        self.retain("after-uncertain-retry.json", {"status": self.last_status, "view": after,
                    "advance_view": retried, "receipt": json.loads(receipt_path.read_text()),
                    "admissions_before": admissions, "admissions_after": after_admissions,
                    "live_actors": processes.live_processes(actors)})
        for view in (retried, after):
            self.assertEqual("PAUSED_VERIFICATION_UNCERTAIN", view["status"], view)
            self.assertEqual(self.metadata["receipt"], view["runner_check"]["supervision"]["receipt"])
            self.assertEqual(self.metadata["nonce"], view["runner_check"]["supervision"]["nonce"])
            self.assertFalse(view["done"])
            self.assertIsNone(view["evidence"]["regression_proof"])
            self.assertEqual([], view["evidence"]["acceptance"])
        self.assertEqual(uncertain, json.loads(receipt_path.read_text()), "Retry rewrote uncertain ownership evidence")
        self.assertEqual(admissions, after_admissions, "Uncertain cleanup admitted a new verification command")
        self.assertEqual(provider_calls, self.provider_calls(), "Uncertain cleanup dispatched another provider")
        self.assertEqual([], processes.live_processes(actors))
        if after["runner_check"]["stage"] == "review_change":
            self.assertFalse((workspace / "review/findings.json").exists(), "Uncertain cleanup accepted a review")

    def test_taskrun_owner_loss_stops_prerequisite_without_accepting_readiness(self):
        workspace, manifest, environment = self.prepare_prerequisite()
        script = f"""import sys
sys.path.insert(0,{str(TOOLS)!r})
from autocode_taskrun import TaskRun
TaskRun.start({str(workspace)!r},{BRIEF!r},options={OPTIONS!r},
    start_options=('--workflow','build','--task-preflight',{str(manifest)!r}),env={environment!r},timeout=300)
"""
        self.taskrun_fault(workspace, environment, script, stage="task_preflight", provider_calls=[])

    def test_taskrun_owner_loss_during_finished_review_result_application(self):
        workspace = self.root / "project"
        workspace.mkdir()
        (workspace / "calc.py").write_text("def double(value):\n    return value + 1\n")
        (workspace / ".gitignore").write_text(".autocode/\n__pycache__/\n")
        subprocess.run(["git", "init", "-q", str(workspace)], check=True)
        for key in ("maintenance.auto", "gc.auto"):
            subprocess.run(["git", "-C", str(workspace), "config", key, "false" if key == "maintenance.auto" else "0"], check=True)
        subprocess.run(["git", "-C", str(workspace), "add", "calc.py", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(workspace), "-c", "user.name=T", "-c", "user.email=t@example.test",
                        "commit", "-qm", "review result-application fixture"], check=True)
        worker = ("import json,os,signal,socket; signal.signal(signal.SIGTERM,signal.SIG_IGN); "
                  f"channel=socket.socket(socket.AF_UNIX); channel.connect({self.endpoint!r}); "
                  "channel.sendall((json.dumps({'kind':'worker','pid':os.getpid(),'parent':os.getppid(),"
                  "'term_ignored':True})+'\\n').encode()); channel.recv(1); signal.pause()")
        barrier = f"""subprocess.Popen([sys.executable,'-u','-c',{worker!r}],start_new_session=True)
channel=socket.socket(socket.AF_UNIX)
channel.connect({self.endpoint!r})
channel.sendall((json.dumps({{'kind':'command','pid':os.getpid(),'parent':os.getppid(),'cwd':os.getcwd()}})+'\\n').encode())
channel.recv(1)
"""
        delivered = ("import json,os,socket,subprocess,sys,unittest\nfrom calc import double\n"
                     "class Finding(unittest.TestCase):\n    def test_f1_owner(self):\n"
                     + textwrap.indent(barrier, "        ") + "        self.assertEqual(6,double(3))\n")
        report = {"verdict": "request_changes", "summary": "One scripted blocking finding",
                  "change_under_review": "The existing double implementation", "change_patch": "",
                  "findings": [{"id": "F1", "severity": "blocking", "file": "calc.py", "lines": [1, 2],
                                "summary": "Adds one instead of doubling", "evidence": "double(3) is 4",
                                "example": "Given input 3, when double runs, it returns 4 (expected 6)",
                                "untestable": ""}],
                  "tests_run": [], "delivered_tests": ["review/tests/test_f1_owner.py"]}
        bindir = self.root / "bin"
        bindir.mkdir()
        provider = bindir / "codex"
        provider.write_text(f"""#!/usr/bin/env python3
import json,sys,uuid
from pathlib import Path
if sys.argv[1:] == ['login','status']:
    print('Logged in using ChatGPT (offline review fixture)')
    raise SystemExit(0)
prompt=sys.stdin.read()
data=json.loads(prompt.split('CURRENT HANDOFF DATA\\n',1)[1])
stage=data.get('stage')
with Path({str(self.root / 'provider-called')!r}).open('a') as calls:
    calls.write(str(stage)+'\\n')
assert stage == 'review_change', stage
path=Path('review/tests/test_f1_owner.py')
path.parent.mkdir(parents=True,exist_ok=True)
path.write_text({delivered!r})
Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps({report!r}))
print(json.dumps({{'type':'thread.started','thread_id':str(uuid.uuid4())}}),flush=True)
print(json.dumps({{'type':'turn.completed','usage':{{'input_tokens':10,'output_tokens':10}}}}),flush=True)
""")
        provider.chmod(0o755)
        environment = {"PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
                       "AUTOCODE_HOME": str(self.root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        self.assertEqual([], taskrun.TaskRun.runs_in(workspace))
        script = f"""import sys
sys.path.insert(0,{str(TOOLS)!r})
from autocode_taskrun import TaskRun
TaskRun.start({str(workspace)!r},'Review the double implementation and deliver a finding test.',options={OPTIONS!r},
    start_options=('--workflow','review'),env={environment!r},timeout=300)
"""
        self.taskrun_fault(workspace, environment, script, stage="review_change", provider_calls=["review_change"])
