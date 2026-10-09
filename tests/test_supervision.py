"""Offline owner-loss faults reach the real exec and pipe-lifeline boundaries.

Event pipes/barriers establish each fault phase; timeouts only bound broken
fixtures. No model, allowance, private run-state write or elapsed-time oracle.
"""
import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import tempfile
import threading
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import autocode_process as processes
import autocode_supervision as supervision


TOOLS = Path(__file__).resolve().parents[1] / 'tools'


class SupervisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='supervision-test-')
        self.root = Path(self.temp.name)
        self.addCleanup(self.temp.cleanup)

    def receipt_until(self, metadata, phases=('stopped', 'uncertain', 'discharged')):
        limit = time.monotonic() + 20
        while time.monotonic() < limit:
            value = supervision.receipt(metadata)
            if value and value.get('phase') in phases:
                return value
            threading.Event().wait(.02)
        self.fail('Keeper never retained its terminal cleanup receipt')

    def read_event(self, child):
        # TextIO.readline can prefetch another event while select sees an empty
        # kernel pipe. Keep all received bytes here and consume queued lines
        # before asking the kernel for more readiness.
        pending = getattr(child, '_event_bytes', None)
        if pending is None:
            pending = child._event_bytes = bytearray()
        deadline = time.monotonic() + 20
        while b'\n' not in pending:
            remaining = deadline - time.monotonic()
            self.assertGreater(remaining, 0, 'Fixture did not reach its event barrier')
            self.assertTrue(select.select([child.stdout], [], [], remaining)[0],
                            'Fixture did not reach its event barrier')
            chunk = os.read(child.stdout.fileno(), 65536)
            self.assertTrue(chunk, 'Fixture exited before its event barrier')
            pending.extend(chunk)
        end = pending.index(b'\n')
        line = bytes(pending[:end])
        del pending[:end + 1]
        value = json.loads(line)
        if 'metadata' in value:
            metadata = value['metadata']
            scope = {'metadata': metadata, 'known': {metadata['provider']['pid']: metadata['provider']}}
            child._cleanup_scope = scope
            self.addCleanup(self.cleanup_supervised_owner, child, scope)
        elif 'worker' in value and hasattr(child, '_cleanup_scope'):
            scope = child._cleanup_scope
            pid = value['worker']
            if type(pid) is int and pid > 0:
                provider = scope['metadata']['provider']
                table = processes.process_table({provider['pid'], pid})
                worker = table.get(pid)
                receipt = supervision.receipt(scope['metadata'])
                recorded = (receipt or {}).get('processes', [])
                parent_verified = (processes.matches(provider, table.get(provider['pid']))
                                   and worker and worker['parent'] == provider['pid'])
                if worker and (parent_verified or any(processes.matches(row, worker) for row in recorded)):
                    # Capture each event immediately; a later failed barrier
                    # must not discard an already verified detached worker.
                    scope['known'][pid] = processes.identity(worker)
        return value

    def cleanup_supervised_owner(self, child, scope):
        metadata = scope['metadata']
        tree = processes.ProcessTree(metadata['provider']['pid'], lambda rows: None,
                                     excluded=(metadata['keeper'],))
        tree.known = dict(scope['known'])
        deadline = time.monotonic() + 20

        def retained_workers():
            receipt = supervision.receipt(metadata)
            for row in (receipt or {}).get('processes', []):
                if not processes.matches(metadata['keeper'], row):
                    tree.known[row['pid']] = row
            return list(tree.known.values())

        errors = []

        def remember(error):
            if not errors:
                errors.append(error)

        try:
            try:
                # Discover detached descendants while the native provider
                # parent still exists, then stop/reap our actual owner first.
                retained_workers()
                tree.sample(notify=False)
            except Exception as error:
                remember(error)
            finally:
                try:
                    self.stop_owner(child)
                except Exception as error:
                    remember(error)
            # A discovery error must not end the keeper's EOF cleanup chance.
            # Receipt refresh failures retain the already captured identities.
            while time.monotonic() < deadline - 2:
                try:
                    retained_workers()
                except Exception as error:
                    remember(error)
                try:
                    if not processes.live_processes(list(tree.known.values()) + [metadata['keeper']]):
                        break
                except Exception as error:
                    remember(error)
                threading.Event().wait(.02)
        finally:
            try:
                # Even failed discovery/inspection reaches native fallback.
                # Fresh absence makes this a no-op after successful cleanup.
                tree.signal(list(tree.known.values()), signal.SIGKILL)
            except Exception as error:
                remember(error)
            finally:
                try:
                    self.cleanup_identity(metadata['keeper'])
                except Exception as error:
                    remember(error)
        live = None
        while time.monotonic() < deadline:
            try:
                live = processes.live_processes(list(tree.known.values()) + [metadata['keeper']])
            except Exception as error:
                remember(error)
                break
            if not live:
                break
            threading.Event().wait(.02)
        if errors:
            raise errors[0]
        self.assertEqual([], live, 'Captured fixture processes survived bounded cleanup')

    def cleanup_identity(self, row):
        if row and processes.live_processes([row]):
            current = processes.process_table({row['pid']}).get(row['pid'])
            if processes.matches(row, current):
                os.kill(row['pid'], signal.SIGKILL)

    def start_owner(self, provider, *, callback=None, timeout=30):
        command = [sys.executable, '-u', '-c', provider]
        callback_code = callback or "print(json.dumps({'metadata':meta}),flush=True)"
        script = f"""import sys,os,json,signal
sys.path.insert(0,{str(TOOLS)!r})
import autocode_supervision as guard

def checkpoint(meta):
    {callback_code}
with guard.launch({command!r},receipt_path={str(self.root / 'receipt.json')!r},timeout={timeout!r},
                  checkpoint=checkpoint,start_new_session=True,stdout=sys.stdout,stderr=sys.stderr) as child:
    child.wait()
"""
        child = subprocess.Popen([sys.executable, '-u', '-c', script], stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                 start_new_session=True)
        self.addCleanup(self.stop_owner, child)
        return child

    def stop_owner(self, child):
        if child.poll() is None:
            child.kill()
        child.wait(timeout=10)
        for stream in (child.stdin, child.stdout, child.stderr):
            if stream:
                stream.close()

    def test_coalesced_events_are_read_while_the_pipe_writer_remains_open(self):
        read_fd, write_fd = os.pipe()
        output = os.fdopen(read_fd, 'r')
        self.addCleanup(output.close)
        self.addCleanup(os.close, write_fd)
        os.write(write_fd, b'{"provider":101}\n{"worker":102}\n')
        child = SimpleNamespace(stdout=output)
        original_select = select.select
        # Both complete events already exist. An immediate kernel-readiness
        # probe makes the old TextIO-prefetch failure deterministic and fast.
        with patch.object(select, 'select', side_effect=lambda readers, writers, errors, timeout:
                          original_select(readers, writers, errors, 0)):
            self.assertEqual({'provider': 101}, self.read_event(child))
            self.assertEqual({'worker': 102}, self.read_event(child))

    def test_failed_event_barrier_cleanup_keeps_guardian_until_owner_loss_cleanup(self):
        sentinel = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
        self.addCleanup(self.stop_owner, sentinel)
        sentinel_identity = processes.identity(processes.process_table({sentinel.pid})[sentinel.pid])
        provider = """import subprocess,sys,signal
subprocess.Popen([sys.executable,'-u','-c',"import os,json,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); print(json.dumps({'worker':os.getpid()}),flush=True); signal.pause()"],start_new_session=True)
signal.pause()
"""
        owner = self.start_owner(provider)
        metadata = self.read_event(owner)['metadata']
        event = self.read_event(owner)
        worker_identity = owner._cleanup_scope['known'][event['worker']]
        with patch.object(select, 'select', return_value=([], [], [])):
            with self.assertRaisesRegex(AssertionError, 'event barrier'):
                self.read_event(owner)
        self.cleanup_supervised_owner(owner, owner._cleanup_scope)
        self.assertIsNotNone(owner.returncode)
        receipt = supervision.receipt(metadata)
        self.assertEqual('owner_lost', receipt['cause'])
        self.assertEqual('stopped', receipt['phase'], receipt)
        self.assertFalse(processes.live_processes([worker_identity]))
        self.assertTrue(processes.live_processes([sentinel_identity]))

    def test_discovery_error_still_allows_guardian_to_clean_captured_worker(self):
        provider = """import subprocess,sys,signal
subprocess.Popen([sys.executable,'-u','-c',"import os,json,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); print(json.dumps({'worker':os.getpid()}),flush=True); signal.pause()"],start_new_session=True)
signal.pause()
"""
        owner = self.start_owner(provider)
        metadata = self.read_event(owner)['metadata']
        event = self.read_event(owner)
        worker_identity = owner._cleanup_scope['known'][event['worker']]
        with patch.object(processes.ProcessTree, 'sample', side_effect=processes.ProcessError('fixture discovery denied')):
            with self.assertRaisesRegex(processes.ProcessError, 'fixture discovery denied'):
                self.cleanup_supervised_owner(owner, owner._cleanup_scope)
        receipt = supervision.receipt(metadata)
        self.assertEqual('owner_lost', receipt['cause'])
        self.assertEqual('stopped', receipt['phase'], receipt)
        self.assertFalse(processes.live_processes([worker_identity, metadata['provider'], metadata['keeper']]))

    def test_exec_preserves_actual_popen_pid_parent_session_stdio_and_exit(self):
        code = "import os,sys,json; print(json.dumps({'pid':os.getpid(),'parent':os.getppid(),'session':os.getsid(0),'input':sys.stdin.read()})); print('stderr-kept',file=sys.stderr); raise SystemExit(7)"
        saved = []
        with supervision.launch([sys.executable, '-c', code], receipt_path=self.root/'receipt.json', timeout=30,
                                checkpoint=saved.append, start_new_session=True,
                                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as child:
            output, error = child.communicate('the prompt', timeout=20)
            row = json.loads(output)
            self.assertIsInstance(child, subprocess.Popen)
            self.assertEqual({'pid':child.pid,'parent':os.getpid(),'session':child.pid,'input':'the prompt'}, row)
            self.assertEqual(7, child.returncode)
            self.assertEqual('stderr-kept\n', error)
        self.assertEqual(child.supervision, saved[0])
        self.assertEqual('stopped', supervision.receipt(saved[0])['phase'])

    def test_verified_abnormal_stop_is_distinct_from_unknown_or_uncertain_cleanup(self):
        read_receipt = supervision.receipt
        cases = [('stage_deadline', 'stopped', None, supervision.VerifiedStop),
                 ('unknown_cause', 'stopped', None, supervision.SupervisionError),
                 ('stage_deadline', 'uncertain', 'not collected', supervision.SupervisionError)]
        for index, (cause, phase, cleanup, expected) in enumerate(cases):
            def terminal(metadata):
                value = read_receipt(metadata)
                return {**value, 'cause': cause, 'phase': phase, 'cleanup_error': cleanup} if value and value.get('phase') == 'stopped' else value
            with self.subTest(cause=cause, phase=phase), patch.object(supervision, 'receipt', side_effect=terminal):
                with self.assertRaises(expected) as caught:
                    with supervision.launch([sys.executable, '-c', 'print("partial artifact")'],
                                            receipt_path=self.root / f'receipt-{index}.json', timeout=30,
                                            start_new_session=True, stdout=subprocess.PIPE, text=True) as child:
                        output, _ = child.communicate(timeout=20)
                        self.assertEqual('partial artifact\n', output)
                self.assertIs(type(caught.exception), expected)
                if expected is supervision.VerifiedStop:
                    self.assertEqual('stage_deadline', caught.exception.cause)
                self.assertFalse(processes.live_processes(read_receipt(child.supervision)['processes']))

    def test_owner_sigkill_stops_provider_and_retains_cause_without_touching_sentinel(self):
        sentinel = subprocess.Popen([sys.executable,'-c','import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
        self.addCleanup(self.stop_owner, sentinel)
        sentinel_identity = processes.identity(processes.process_table({sentinel.pid})[sentinel.pid])
        owner = self.start_owner("import os,json,signal; print(json.dumps({'provider':os.getpid(),'parent':os.getppid()}),flush=True); signal.pause()")
        metadata = self.read_event(owner)['metadata']
        event = self.read_event(owner)
        self.assertEqual(metadata['provider']['pid'], event['provider'])
        self.assertEqual(owner.pid, event['parent'])
        os.kill(owner.pid, signal.SIGKILL)
        owner.wait(timeout=10)
        receipt = self.receipt_until(metadata)
        self.assertEqual('owner_lost', receipt['cause'])
        self.assertEqual('stopped', receipt['phase'], receipt)
        self.assertFalse(processes.live_processes(receipt['processes']))
        self.assertTrue(processes.live_processes([sentinel_identity]))
        self.assertFalse(supervision.observe(metadata)['owner']['alive'])

    def test_owner_death_after_arming_before_release_never_executes_provider(self):
        marker = self.root/'executed'
        callback = "print(json.dumps({'metadata':meta}),flush=True); sys.stdin.read()"
        owner = self.start_owner(f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')", callback=callback)
        metadata = self.read_event(owner)['metadata']
        self.assertEqual('armed', supervision.receipt(metadata)['phase'])
        owner.kill()
        owner.wait(timeout=10)
        self.assertEqual('stopped', self.receipt_until(metadata)['phase'])
        self.assertFalse(marker.exists())

    def test_failed_checkpoint_and_failed_initial_receipt_never_executes_provider(self):
        marker = self.root/'executed'
        command = [sys.executable,'-c',f"from pathlib import Path; Path({str(marker)!r}).write_text('ran')"]
        def failed(_):
            raise OSError('fixture checkpoint failed')
        with self.assertRaisesRegex(OSError, 'checkpoint failed'):
            with supervision.launch(command, receipt_path=self.root/'receipt.json', timeout=30,
                                    checkpoint=failed,start_new_session=True):
                self.fail('Failed checkpoint released provider')
        self.assertFalse(marker.exists())
        occupied = self.root/'occupied'
        occupied.mkdir()
        with self.assertRaises(supervision.SupervisionError):
            with supervision.launch(command,receipt_path=occupied,timeout=30,start_new_session=True):
                self.fail('Invalid receipt released provider')
        self.assertFalse(marker.exists())

    def test_keeper_death_causes_independent_scoped_cleanup(self):
        with self.assertRaises(supervision.SupervisionError):
            with supervision.launch([sys.executable,'-c','import signal; signal.pause()'],
                                    receipt_path=self.root/'receipt.json',timeout=30,start_new_session=True) as child:
                keeper = child.supervision['keeper']
                os.kill(keeper['pid'], signal.SIGKILL)
                child.wait(timeout=15)
                self.assertIsNotNone(child.returncode)

    def test_keeper_death_before_release_never_executes_provider(self):
        marker=self.root/'executed'
        def lose_keeper(metadata):
            os.kill(metadata['keeper']['pid'],signal.SIGKILL)
            limit=time.monotonic()+10
            while processes.live_processes([metadata['keeper']]) and time.monotonic()<limit:
                threading.Event().wait(.02)
        with self.assertRaises(supervision.SupervisionError):
            with supervision.launch([sys.executable,'-c',f"from pathlib import Path; Path({str(marker)!r}).touch()"],
                                    receipt_path=self.root/'receipt.json',timeout=30,
                                    checkpoint=lose_keeper,start_new_session=True):
                self.fail('Dead keeper released provider')
        self.assertFalse(marker.exists())

    def test_blocked_checkpoint_does_not_disable_hard_deadline(self):
        marker = self.root/'executed'
        def blocked(metadata):
            self.receipt_until(metadata)
        with self.assertRaises(supervision.SupervisionError):
            with supervision.launch([sys.executable,'-c',f"from pathlib import Path; Path({str(marker)!r}).touch()"],
                                    receipt_path=self.root/'receipt.json',timeout=1,checkpoint=blocked,
                                    start_new_session=True):
                self.fail('Deadline-expired bootstrap was released')
        self.assertFalse(marker.exists())

    def test_keeper_admission_uses_native_birth_and_preserves_declared_identity(self):
        import autocode_supervision_keeper as keeper
        pid = os.getpid()
        raw_birth = 1760000000.9995
        declared = {'pid': pid, 'group': pid, 'birth_identity': raw_birth,
                    'birth_time': raw_birth + 1.25, 'started': 'controller clock'}
        provider = {'pid': pid + 1, 'group': pid + 1, 'birth_identity': raw_birth,
                    'birth_time': raw_birth, 'started': 'provider clock'}
        for changed_birth in (False, True):
            with self.subTest(changed_birth=changed_birth):
                observed = {**declared, 'birth_time': raw_birth, 'started': 'keeper clock',
                            'birth_identity': raw_birth + 1 if changed_birth else raw_birth}
                metadata = {'schema': 1, 'nonce': 'a' * 32, 'owner': declared,
                            'keeper': declared, 'provider': provider,
                            'receipt': str(self.root / 'admission.json'), 'started_at': 'initial'}
                control_r, control_w = os.pipe()
                ack_r, ack_w = os.pipe()
                published = []
                try:
                    packet = {'metadata': metadata, 'deadline': None}
                    os.write(control_w, (json.dumps(packet) + '\n').encode())
                    with patch.object(keeper.processes, 'live_processes', return_value=[provider]), \
                         patch.object(keeper.processes, 'process_table', return_value={pid: observed}), \
                         patch.object(keeper, 'KeeperTree') as trees, \
                         patch.object(keeper.util, 'atomic_json', side_effect=lambda path, value:
                                      published.append(json.loads(json.dumps(value)))):
                        trees.return_value.sample.return_value = []
                        code = keeper.main(control_r, ack_w)
                        if changed_birth:
                            self.assertEqual(2, code)
                            self.assertFalse(select.select([ack_r], [], [], 0)[0])
                            trees.assert_not_called()
                            self.assertEqual([], published)
                        else:
                            self.assertEqual(0, code)
                            self.assertTrue(select.select([ack_r], [], [], 0)[0])
                            self.assertEqual({'armed': metadata['nonce']}, json.loads(os.read(ack_r, 4096)))
                            trees.assert_called_once()
                            trees.return_value.stop.assert_called_once()
                            self.assertEqual(['armed', 'stopped'], [value['phase'] for value in published])
                            self.assertTrue(all(value['keeper'] == declared for value in published))
                finally:
                    for fd in (control_r, control_w, ack_r, ack_w):
                        try:
                            os.close(fd)
                        except OSError:  # The admitted keeper closes its owned ends.
                            pass

    def test_nonce_or_identity_tampering_is_unknown(self):
        with supervision.launch([sys.executable,'-c','pass'],receipt_path=self.root/'receipt.json',
                                timeout=30,start_new_session=True) as child:
            child.wait(timeout=20)
        meta = child.supervision
        self.assertIsNone(supervision.receipt({**meta,'nonce':'0'*32}))
        self.assertIsNone(supervision.receipt({**meta,'provider':{**meta['provider'],'birth_identity':-1}}))
        self.assertFalse(supervision.observe(meta)['provider']['alive'])

    def test_no_fork_or_preexec_and_keeper_does_not_inherit_provider_lock(self):
        import fcntl
        lock = (self.root/'writer.lock').open('w')
        fcntl.flock(lock, fcntl.LOCK_EX)
        with patch.object(os, 'fork', side_effect=AssertionError('threaded owner forked')):
            with supervision.launch([sys.executable,'-c','pass'],receipt_path=self.root/'receipt.json',timeout=30,
                                    start_new_session=True,pass_fds=(lock.fileno(),)) as child:
                child.wait(timeout=20)
                lock.close()
                with (self.root/'writer.lock').open('w') as contender:
                    fcntl.flock(contender, fcntl.LOCK_EX|fcntl.LOCK_NB)
        self.assertEqual(0, child.returncode)

    def test_term_resistant_detached_writer_is_stopped_on_owner_loss(self):
        provider = """import os,json,subprocess,sys,signal
worker = subprocess.Popen([sys.executable,'-u','-c',"import os,json,signal; signal.signal(signal.SIGTERM,signal.SIG_IGN); print(json.dumps({'worker':os.getpid()}),flush=True); signal.pause()"],start_new_session=True)
print(json.dumps({'provider':os.getpid()}),flush=True)
signal.pause()
"""
        owner = self.start_owner(provider)
        metadata = self.read_event(owner)['metadata']
        events = [self.read_event(owner), self.read_event(owner)]
        worker_pid = next(row['worker'] for row in events if 'worker' in row)
        worker_identity = owner._cleanup_scope['known'][worker_pid]
        # The worker's readiness event precedes the owner fault; the keeper
        # discovers its detached session independently at the fault boundary.
        owner.kill()
        owner.wait(timeout=10)
        value = self.receipt_until(metadata)
        self.assertEqual('stopped', value['phase'], value)
        self.assertIn(worker_pid, [row['pid'] for row in value['processes']])
        self.assertFalse(processes.live_processes([worker_identity]))

    def test_owner_death_before_arm_never_executes_provider(self):
        marker = self.root/'executed'
        command = [sys.executable,'-c',f"from pathlib import Path; Path({str(marker)!r}).touch()"]
        script = f"""import sys,json
sys.path.insert(0,{str(TOOLS)!r})
import autocode_supervision as guard
original = guard._send
def blocked(fd,value):
    print(json.dumps({{'metadata':value['metadata']}}),flush=True)
    sys.stdin.read()
    original(fd,value)
guard._send=blocked
with guard.launch({command!r},receipt_path={str(self.root/'receipt.json')!r},timeout=30,start_new_session=True):
    raise AssertionError('unarmed provider released')
"""
        owner = subprocess.Popen([sys.executable,'-u','-c',script],stdin=subprocess.PIPE,
                                 stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,start_new_session=True)
        self.addCleanup(self.stop_owner,owner)
        metadata = self.read_event(owner)['metadata']
        owner.kill()
        owner.wait(timeout=10)
        # Bootstrap EOF is an observable process boundary, not a timing guess.
        until = time.monotonic()+15
        while processes.live_processes([metadata['provider']]) and time.monotonic()<until:
            threading.Event().wait(.02)
        self.assertFalse(processes.live_processes([metadata['provider']]))
        self.assertFalse(marker.exists())
        self.assertIsNone(supervision.receipt(metadata))

    def start_cli_guard(self, *, normal=False, exit_code=0):
        read_fd,write_fd=os.pipe()
        identity=processes.identity(processes.process_table({os.getpid()})[os.getpid()])
        nonce='a'*32
        after=f"raise SystemExit({exit_code})" if normal else 'signal.pause()'
        script=f"""import sys,os,json,signal
from pathlib import Path
sys.path.insert(0,{str(TOOLS)!r})
import autocode_supervision as guard

def interrupted(*_):
    Path({str(self.root/'interrupted')!r}).write_text('retained')
    raise SystemExit(2)
signal.signal(signal.SIGTERM,interrupted)
with guard.protect_owner({read_fd},receipt_path={str(self.root/'cli-receipt.json')!r},timeout=30,
                         owner_identity={identity!r},nonce={nonce!r}) as meta:
    print(json.dumps({{'metadata':meta}}),flush=True)
    {after}
"""
        child=subprocess.Popen([sys.executable,'-u','-c',script],stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE,text=True,start_new_session=True,pass_fds=(read_fd,))
        os.close(read_fd)
        self.addCleanup(self.stop_owner,child)
        writer=os.fdopen(write_fd,'wb',buffering=0)
        self.addCleanup(writer.close)
        return child,writer

    def test_cli_guard_normal_discharge_preserves_success_and_failure_exit(self):
        for code in (0,7):
            with self.subTest(code=code):
                path=self.root/'cli-receipt.json'
                if path.exists():
                    path.unlink()
                child,writer=self.start_cli_guard(normal=True,exit_code=code)
                metadata=self.read_event(child)['metadata']
                child.wait(timeout=15)
                self.assertEqual(code,child.returncode,child.stderr.read())
                self.assertEqual('discharged',self.receipt_until(metadata)['phase'])
                self.assertFalse((self.root/'interrupted').exists())

    def test_cli_guard_eof_allows_interrupted_receipt_before_stopping(self):
        child,writer=self.start_cli_guard()
        metadata=self.read_event(child)['metadata']
        writer.close()
        child.wait(timeout=15)
        self.assertEqual(2,child.returncode,child.stderr.read())
        self.assertEqual('retained',(self.root/'interrupted').read_text())
        value=self.receipt_until(metadata)
        self.assertEqual('owner_lost',value['cause'])
        self.assertEqual('stopped',value['phase'],value)


    def test_pid_zero_never_establishes_ownership_or_signals_a_process_group(self):
        row={'pid':0,'group':0,'birth_identity':1.0,'started':'kernel'}
        with patch.object(processes,'process_ids',return_value=[0,-1]), \
             patch.object(processes.psutil,'Process',side_effect=AssertionError('Inspected nonpositive PID')):
            self.assertEqual({},processes.process_table())
        tree=processes.ProcessTree(999,lambda rows:None)
        with patch.object(processes,'process_table',return_value={0:row}) as inspect, \
             patch.object(processes.os,'kill',side_effect=AssertionError('Signalled PID zero')):
            tree.signal([row],signal.SIGKILL)
            inspect.assert_called_once_with(set())

    def test_exact_keeper_identity_is_excluded_before_group_expansion(self):
        root={'pid':101,'parent':99,'group':101,'birth_identity':1.0,'started':'root','state':'sleeping'}
        keeper={'pid':102,'parent':101,'group':102,'birth_identity':2.0,'started':'keeper','state':'sleeping'}
        class Process:
            def __init__(self,pid):
                self.pid=pid
                self._ident=(pid,1.0 if pid==101 else 2.0)
            def create_time(self):
                return self._ident[1]
        tree=processes.ProcessTree(101,lambda rows:None,excluded=(processes.identity(keeper),))
        groups=[]
        def table(pids):
            return {pid:dict(row) for pid,row in ((101,root),(102,keeper)) if pid in pids}
        def group(pid):
            groups.append(pid)
            return 102
        with patch.object(processes,'process_table',side_effect=table), \
             patch.object(processes.psutil,'Process',Process), \
             patch.object(processes.process_children,'descendants',return_value=[Process(102)]), \
             patch.object(processes,'process_ids',return_value=[101,102,0]), \
             patch.object(processes.os,'getpgid',side_effect=group):
            rows=tree.sample()
        self.assertEqual([101],[row['pid'] for row in rows])
        self.assertNotIn(102,tree.known)
        self.assertNotIn(0,groups)
        # Exclusion is bound to birth identity; it cannot hide a reused PID.
        reused={**keeper,'birth_identity':3.0}
        self.assertTrue(tree._included(reused))

    def test_retained_group_owns_a_child_reparented_before_any_sample(self):
        # A guarded CLI leads its own session. Killed and reaped before its keeper
        # samples a new child, it leaves that child reparented; only the child's
        # group still ties it to the recorded leader.
        from autocode_supervision_keeper import Observer
        worker = "import signal; print(flush=True); signal.pause()"
        script = (f"import subprocess,sys,signal; c=subprocess.Popen([sys.executable,'-c',{worker!r}],"
                  "stdout=subprocess.PIPE); c.stdout.readline(); print(c.pid,flush=True); signal.pause()")
        leader = subprocess.Popen([sys.executable, '-c', script], stdout=subprocess.PIPE, text=True,
                                  start_new_session=True)

        def stop_leader():
            if leader.poll() is None:
                leader.kill()
            leader.wait(timeout=10)
            leader.stdout.close()
        self.addCleanup(stop_leader)
        root = processes.identity(processes.process_table({leader.pid})[leader.pid])
        orphan_pid = int(leader.stdout.readline())
        orphan = processes.identity(processes.process_table({orphan_pid})[orphan_pid])
        self.addCleanup(self.cleanup_identity, orphan)
        leader.kill()
        leader.wait(timeout=10)
        self.assertNotEqual(leader.pid, processes.process_table({orphan_pid})[orphan_pid]['parent'])
        self.assertEqual(leader.pid, orphan['group'])
        unretained = processes.ProcessTree(leader.pid, lambda rows: None)
        unretained.known = {leader.pid: root}
        self.assertEqual([], unretained.sample(notify=False))
        tree = processes.ProcessTree(leader.pid, lambda rows: None, groups=(root,))
        tree.known = {leader.pid: root}
        self.assertEqual([orphan_pid], [row['pid'] for row in tree.sample(notify=False)])
        tree.stop(Observer(root))
        self.assertFalse(processes.live_processes([orphan]))

    def test_a_reused_or_emptied_leader_group_ends_retained_ownership(self):
        leader = {'pid': 4242, 'group': 4242, 'birth_identity': 1.0, 'birth_time': 1.0, 'started': 'leader'}
        member = {'pid': 4243, 'parent': 1, 'group': 4242, 'birth_identity': 3.0, 'birth_time': 3.0,
                  'started': 'member', 'state': 'sleeping'}
        reused = {**leader, 'parent': 1, 'birth_identity': 2.0, 'birth_time': 2.0, 'state': 'sleeping'}
        stranger = {**member, 'pid': 4250, 'birth_identity': 9.0, 'birth_time': 9.0, 'started': 'stranger'}

        def sample(tree, *rows):
            current = {row['pid']: row for row in rows}

            def killpg(group, sig):
                self.assertEqual(0, sig)
                if not any(row['group'] == group for row in rows):
                    raise ProcessLookupError(group)
            with patch.object(processes, 'process_table',
                              side_effect=lambda pids: {pid: dict(current[pid]) for pid in pids if pid in current}), \
                 patch.object(processes, 'process_ids', return_value=list(current)), \
                 patch.object(processes.os, 'getpgid', side_effect=lambda pid: current[pid]['group']), \
                 patch.object(processes.os, 'killpg', side_effect=killpg):
                return [row['pid'] for row in tree.sample(notify=False)]

        def tree():
            retained = processes.ProcessTree(4242, lambda rows: None, groups=(leader,))
            retained.known = {4242: leader}
            return retained

        self.assertEqual([4243], sample(tree(), member))
        # A different process at the leader's PID proves the old group emptied
        # first. Its new group is never claimed, even after that process exits.
        reused_tree = tree()
        self.assertEqual([], sample(reused_tree, reused, member))
        self.assertEqual([], sample(reused_tree, member))
        # A sample that finds the group empty ends ownership too: from then on the
        # kernel may give the PID to a new group leader, which can start a child
        # and exit before the next sample. That child is not claimed.
        emptied = tree()
        self.assertEqual([], sample(emptied))
        self.assertEqual([], sample(emptied, stranger))

    def keeper_publication_case(self, *, deadline_fault):
        # A blocked sampling write and an independent trigger compete for the
        # same receipt. Event barriers drive the ordering; the fake timer fires
        # the hard escalation while that write remains blocked.
        import autocode_supervision_keeper as keeper_module
        from copy import deepcopy
        blocked=threading.Event()
        release=threading.Event()
        killed=threading.Event()
        published=[]
        writes=[0]
        alive=[True]
        clock=[0.0]
        outcome=[]
        errors=[]
        provider={'pid':901,'group':901,'birth_identity':1.0,'birth_time':1.0,'started':'provider'}
        guardian={'pid':os.getpid(),'group':os.getpid(),'birth_identity':2.0,'birth_time':2.0,'started':'keeper'}
        worker={'pid':902,'group':902,'birth_identity':3.0,'birth_time':3.0,'started':'worker'}
        metadata={'schema':1,'nonce':'c'*32,'owner':guardian,'keeper':guardian,'provider':provider,
                  'receipt':str(self.root/'publication.json'),'started_at':'fixture'}
        class Tree:
            def __init__(self,*args,**kwargs):
                self.known={}
            def inventory(self):
                return list(self.known.values())
            def sample(self,**kwargs):
                self.known[worker['pid']]=worker
                return list(self.known.values()) if alive[0] else []
            def signal(self,rows,sig):
                if sig==signal.SIGKILL:
                    alive[0]=False
                    killed.set()
            def stop(self,observer):
                alive[0]=False
        class Timer:
            def __init__(self,interval,callback,args=()):
                self.callback,self.args=callback,args
                self.daemon=False
            def start(self):
                self.callback(*self.args)
            def cancel(self):
                pass
        def atomic(path,value):
            snapshot=deepcopy(value)
            writes[0]+=1
            if writes[0]==2:
                blocked.set()
                if not release.wait(5):
                    raise AssertionError('Sampling receipt was not released')
            published.append(snapshot)
        control_r,control_w=os.pipe()
        ack_r,ack_w=os.pipe()
        def run():
            try:
                outcome.append(keeper_module.main(control_r,ack_w))
            except BaseException as error:
                errors.append(error)
        try:
            with patch.object(keeper_module,'KeeperTree',Tree), \
                 patch.object(keeper_module.processes,'process_table',return_value={os.getpid():guardian}), \
                 patch.object(keeper_module.processes,'live_processes',side_effect=lambda rows:[provider] if alive[0] else []), \
                 patch.object(keeper_module.util,'atomic_json',side_effect=atomic), \
                 patch.object(keeper_module.threading,'Timer',Timer), \
                 patch.object(keeper_module.time,'monotonic',side_effect=lambda:clock[0]):
                thread=threading.Thread(target=run)
                thread.start()
                packet={'metadata':metadata,'deadline':5 if deadline_fault else None,'cleanup_policy':'freeze_tree'}
                os.write(control_w,(json.dumps(packet)+'\n').encode())
                self.assertTrue(blocked.wait(5),'Sampling receipt did not reach its barrier')
                if deadline_fault:
                    clock[0]=10.0
                else:
                    os.close(control_w)
                    control_w=None
                self.assertTrue(killed.wait(5),'A blocked receipt write prevented hard cleanup')
                self.assertFalse(alive[0])
                self.assertFalse(release.is_set())
                # The trigger cannot publish past the older locked write.
                self.assertEqual(['armed'],[row['phase'] for row in published])
                release.set()
                thread.join(5)
                self.assertFalse(thread.is_alive())
                self.assertEqual([],errors)
                self.assertEqual([0],outcome)
                phases=[row['phase'] for row in published]
                self.assertIn('stopping',phases)
                self.assertNotIn('armed',phases[phases.index('stopping'):])
                self.assertEqual('stopped',published[-1]['phase'])
                self.assertEqual('stage_deadline' if deadline_fault else 'owner_lost',published[-1]['cause'])
        finally:
            release.set()
            if control_w is not None:
                os.close(control_w)
            os.close(ack_r)

    def test_receipt_publications_cannot_revert_stopping_to_armed(self):
        self.keeper_publication_case(deadline_fault=False)

    def test_blocked_receipt_writer_cannot_block_independent_deadline_escalation(self):
        self.keeper_publication_case(deadline_fault=True)


if __name__ == '__main__':
    unittest.main()
