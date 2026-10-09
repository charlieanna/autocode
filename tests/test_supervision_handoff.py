"""Real nested keepers: exec follows durable outer admission, including thread launches."""
import json
import os
import select
import signal
import socket
import subprocess
import sys
import tempfile
import unittest
import uuid
from pathlib import Path

import autocode_supervision as supervision
import psutil
from autocode_supervision_keeper import KeeperTree

TOOLS = Path(__file__).resolve().parents[1] / 'tools'
CONTROLLER = r'''
import json,os,socket,subprocess,sys,threading
from pathlib import Path
sys.path.insert(0,sys.argv[1])
import autocode_supervision as s
root=Path(sys.argv[2]); channel=socket.socket(fileno=int(sys.argv[3])); fault=sys.argv[7]
def event(value): channel.sendall((json.dumps(value)+'\n').encode())
with s.protect_owner(int(sys.argv[4]),receipt_path=root/'outer.json',timeout=20,
                     owner_identity=json.loads(sys.argv[5]),nonce=sys.argv[6]):
 original=s._send
 def barrier(fd,packet):
  if 'admit' in packet:
   metadata=packet['admit']
   event({'event':'admission','metadata':metadata})
   assert channel.recv(1)==b'G'
   if fault=='unrelated': metadata={**metadata,'provider':json.loads(sys.argv[8])}
   if fault=='wrong_birth': metadata={**metadata,'provider':{**metadata['provider'],'birth_identity':metadata['provider']['birth_identity']+1}}
   packet={**packet,'admit':metadata}
  return original(fd,packet)
 s._send=barrier
 def launch():
  try:
   code="from pathlib import Path; import signal; Path("+repr(str(root/'executed'))+").touch(); signal.signal(signal.SIGTERM,signal.SIG_IGN); print('READY',flush=True); signal.pause()"
   with s.launch([sys.executable,'-u','-c',code],receipt_path=root/'inner.json',timeout=15,
                 start_new_session=True,stdout=subprocess.PIPE,text=True) as child:
    assert child.stdout.readline().strip()=='READY'
    event({'event':'running','metadata':child.supervision})
    assert channel.recv(1)==b'F'
    child.kill(); child.wait(timeout=3)
   event({'event':'finished'})
  except BaseException as error:
   event({'event':'error','type':type(error).__name__,'message':str(error)})
 thread=threading.Thread(target=launch); thread.start(); thread.join()
'''


class NestedOwnershipTests(unittest.TestCase):
    def live(self, process):
        try:
            return process.is_running() and process.status() != psutil.STATUS_ZOMBIE
        except psutil.NoSuchProcess:
            return False

    def exercise(self, fault):
        with tempfile.TemporaryDirectory(prefix='nested-handoff-') as tmp:
            root = Path(tmp)
            script = root / 'controller.py'
            script.write_text(CONTROLLER)
            parent, child = socket.socketpair()
            reader, writer = os.pipe()
            owned = []
            def receive():
                self.assertTrue(select.select([parent], [], [], 10)[0], 'Controller event missing')
                data = b''
                while not data.endswith(b'\n'):
                    chunk = parent.recv(1)
                    self.assertTrue(chunk, 'Controller closed before event')
                    data += chunk
                return json.loads(data)
            def read(name):
                return json.loads((root / name).read_text())
            sentinel = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
            sentinel_birth = psutil.Process(sentinel.pid)
            controller = subprocess.Popen([sys.executable, str(script), str(TOOLS), str(root), str(child.fileno()),
                str(reader), json.dumps(supervision._identity(os.getpid())), uuid.uuid4().hex, fault,
                json.dumps(supervision._identity(sentinel.pid))],
                pass_fds=(child.fileno(), reader), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                start_new_session=True)
            child.close()
            os.close(reader)
            try:
                pending = receive()
                self.assertEqual('admission', pending['event'], pending)
                metadata = pending['metadata']
                outer = read('outer.json')
                guardian = psutil.Process(outer['keeper']['pid'])
                stage_keeper = psutil.Process(metadata['keeper']['pid'])
                provider = psutil.Process(metadata['provider']['pid'])
                owned.extend([guardian, stage_keeper, provider])
                # No model code has executed: the provider is the blocked bootstrap.
                self.assertTrue(any('autocode_supervision_bootstrap.py' in arg for arg in provider.cmdline()))
                if fault == 'admission_loss':
                    guardian.kill()
                parent.sendall(b'G')
                if fault not in ('admission_loss', 'wrong_birth', 'unrelated'):
                    response = receive()
                    self.assertEqual('running', response['event'], response)
                    admitted = read('outer.json')['processes']
                    for key in ('provider', 'keeper'):
                        self.assertTrue(any(supervision.processes.matches(metadata[key], row) for row in admitted), admitted)
                    if fault == 'cli_loss':
                        # Delay inner cleanup while the outer keeper must use the
                        # admitted edge after its CLI has been reaped.
                        stage_keeper.send_signal(signal.SIGSTOP)
                        controller.kill()
                    else:
                        parent.sendall(b'F')
                        self.assertEqual('finished', receive()['event'])
                stdout, stderr = controller.communicate(timeout=10)
                psutil.wait_procs(owned, timeout=8)
                survivors = [p.as_dict(attrs=['pid', 'status', 'cmdline']) for p in owned if self.live(p)]
                self.assertEqual([], survivors, stderr)
                self.assertTrue(self.live(sentinel_birth), 'Unrelated sentinel was signalled')
                if fault in ('admission_loss', 'wrong_birth', 'unrelated'):
                    self.assertFalse((root / 'executed').exists(), 'Unadmitted provider executed')
                    self.assertNotEqual(0, controller.returncode, stderr)
                elif fault == 'cli_loss':
                    self.assertEqual(-signal.SIGKILL, controller.returncode)
                    final = read('outer.json')
                    self.assertEqual(('stopped', 'owner_lost', None),
                                     tuple(final[key] for key in ('phase', 'cause', 'cleanup_error')))
                elif fault == 'normal':
                    self.assertEqual(0, controller.returncode, stderr)
                    self.assertEqual('discharged', read('outer.json')['phase'])
            finally:
                if controller.poll() is None:
                    owned.extend(psutil.Process(controller.pid).children(recursive=True))
                    controller.kill()
                controller.wait(timeout=5)
                for process in reversed(owned):
                    if self.live(process):
                        process.kill()
                psutil.wait_procs(owned, timeout=3)
                sentinel.kill(); sentinel.wait(timeout=3)
                sentinel.stdin.close()
                controller.stdout.close(); controller.stderr.close()
                parent.close(); os.close(writer)

    def test_nested_provider_survives_normal_threaded_launch_and_discharge(self):
        self.exercise('normal')

    def test_outer_keeper_cleans_provider_when_inner_keeper_cannot_run(self):
        self.exercise('cli_loss')

    def test_outer_keeper_loss_refuses_provider_exec(self):
        self.exercise('admission_loss')

    def test_wrong_native_birth_refuses_provider_exec(self):
        self.exercise('wrong_birth')

    def test_unrelated_process_cannot_be_admitted_or_signalled(self):
        self.exercise('unrelated')


class NativeAdmissionTests(unittest.TestCase):
    def setUp(self):
        self.children = []
        self.addCleanup(self.cleanup)
        for _ in range(2):
            self.children.append(subprocess.Popen(
                [sys.executable, '-c', 'import sys;sys.stdin.read()'], stdin=subprocess.PIPE))
        self.owner = supervision._identity(os.getpid())
        self.metadata = {'schema': 1, 'nonce': 'a' * 32, 'owner': self.owner,
                         'keeper': supervision._identity(self.children[0].pid),
                         'provider': supervision._identity(self.children[1].pid)}
        self.tree = KeeperTree(self.owner['pid'], lambda rows: None)

    def cleanup(self):
        for child in self.children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=3)
            child.stdin.close()

    def test_real_owned_children_with_native_birth_are_admitted(self):
        self.tree.admit(self.metadata, self.owner)

    def test_legacy_child_identity_is_rejected_even_when_started_matches(self):
        for role in ('provider', 'keeper'):
            with self.subTest(role=role):
                row = dict(self.metadata[role])
                row.pop('birth_identity')
                with self.assertRaises(supervision.processes.ProcessError):
                    self.tree.admit({**self.metadata, role: row}, self.owner)

    def test_invalid_native_birth_is_rejected(self):
        for born in (None, True, False, '1.0', 0, -1, float('nan'), float('inf'), float('-inf')):
            with self.subTest(born=born):
                row = {**self.metadata['provider'], 'birth_identity': born}
                with self.assertRaises(supervision.processes.ProcessError):
                    self.tree.admit({**self.metadata, 'provider': row}, self.owner)
