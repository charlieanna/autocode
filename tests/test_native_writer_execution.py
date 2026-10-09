"""Original-owner writer joins: real barrier/source, controlled conformance.

The native provider response/kernel shell conformance seam is explicitly
controlled. Actual held roots, one supervisor, Git snapshots, parent durable
save, both journals and bounded source writes are real. These are caller
controls, not live/native provider or image qualification.
"""
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import autocode_native_calls as calls
import autocode_native_launch as launch
import autocode_native_parent as parent
import autocode_native_writer as writers
import autocode_process as processes
import autocode_source_scope as source_scope
import autocode_util as util
import autocode_visual_checkpoint as checkpoint
import autocode_visual_protection as protection
import tests.test_native_writer as original_fixture


class FirstWriterCallerControls(unittest.TestCase):
    setUp = original_fixture.FirstWriterAuthorityTests.setUp
    prepare = original_fixture.FirstWriterAuthorityTests.prepare
    acknowledge = original_fixture.FirstWriterAuthorityTests.acknowledge
    commit = original_fixture.FirstWriterAuthorityTests.commit

    def test_actual_dispatch_adapter_uses_original_approval_and_typed_scope(self):
        state, current = self.commit()
        native = object.__new__(calls.PlanningCalls)
        native.context = SimpleNamespace(factory=self.factory)
        native.snapshot = lambda value: source_scope.snapshot(self.root, value)
        seen = []
        def guard(value, stage, **options):
            checkpoint.require_launch(value, options['current'], options['visual_proof'])
            seen.append((stage, options['visual_proof']))
        native.dispatch_guard(state, 'terra', guard)
        self.assertEqual('terra', seen[0][0])
        self.assertFalse(seen[0][1].current_scope_accepted)
        changed = deepcopy(state)
        changed['current_task']['affected_paths'].append('tracked.txt')
        with self.assertRaises(util.Paused): native.dispatch_guard(changed, 'terra', guard)
        self.assertEqual(1, len(seen))

    def test_untouched_implement_cannot_claim_readonly_producer_authority(self):
        state, current = self.commit()
        with self.assertRaisesRegex(util.Paused, 'stopped first Builder'):
            self.owner.readonly_task_authority(state, snapshot=current)

    def test_writer_revision_change_requires_exact_actual_after_and_only_builder(self):
        before = {'stage': 'terra', 'role': 'terra', 'source_revision': 'before'}
        after = {'stage': 'terra', 'role': 'terra', 'source_revision': 'after'}
        protection.check_finished_record(before, after, source_after={'revision': 'after'})
        with self.assertRaises(util.Paused):
            protection.check_finished_record(before, after, source_after={'revision': 'wrong'})
        with self.assertRaises(util.Paused):
            protection.check_finished_record({**before, 'stage': 'sol', 'role': 'sol'},
                {**after, 'stage': 'sol', 'role': 'sol'}, source_after={'revision': 'after'})
        with self.assertRaises(util.Paused): protection.check_finished_record(before, after)


class OriginalWriterTransportControls(unittest.TestCase):
    """Run this class only under the reviewed original native host supervisor."""
    prepare = original_fixture.FirstWriterAuthorityTests.prepare
    acknowledge = original_fixture.FirstWriterAuthorityTests.acknowledge
    commit = original_fixture.FirstWriterAuthorityTests.commit

    def setUp(self):
        original_fixture.FirstWriterAuthorityTests.setUp(self)
        self.state['settings']['roles'] = {'terra': {'engine': 'opencode', 'provider': 'opencode',
            'model': 'controlled/model', 'reasoning_effort': 'high'}}
        self.owner._append('planning-committed', {'contract': deepcopy(self.state['goal_contract']),
            'settings': deepcopy(self.state['settings']), 'source': deepcopy(self.raw), 'token': self.token,
            'pins': {str(self.anchor): util.file_hash(self.anchor)}})
        self.state, self.before = self.commit()
        self.sink = parent.ParentSink(self.run, factory=self.factory, ordinary_write=util.atomic_json,
            snapshot=lambda value: source_scope.snapshot(self.root, value))
        self.factory.bind_parent_sink(self.sink, parent.ParentSink)
        self.context = object.__new__(launch.NativeRunContext)
        self.context.factory, self.context._run_handle, self.context._checkout_handle = (
            self.factory, self.run_handle, self.checkout_handle)
        control = self.root / '.autocode' / ('tool-containment-' + 'c' * 32)
        control.mkdir(); (control / 'scratch').mkdir()
        policy, shell, manifest = control / 'policy.sb', control / 'shell', self.run / 'controlled-isolation.json'
        policy.write_text('CONTROLLED conformance seam; no kernel-policy claim\n')
        shell.write_text('CONTROLLED shell seam; target is the real bounded test program\n')
        manifest.write_text('{}\n')
        self.boundary = {'allow_write': True, 'workspace': str(self.root), 'tools': ['bash'],
            'scratch': str(control / 'scratch'), 'shell': str(shell), 'profile': str(policy),
            'write_paths': ['ignored.txt', 'future.txt'], 'writer_path_kinds': {
                'ignored.txt': 'file', 'future.txt': 'file'}}
        self.command = [str(Path(sys.executable).resolve()), '-I', '-S', '-B', '-c',
            'import pathlib,sys; pathlib.Path(sys.argv[1]).write_text("bounded actual write\\n")',
            str(self.root / 'ignored.txt')]
        self.environment = {'PATH': '/usr/bin:/bin', 'SHELL': str(shell),
                            'AUTOCODE_TOOL_CONTAINMENT': json.dumps(self.boundary)}
        before_ref = self.run / 'builder.before.json'; util.atomic_json(before_ref, self.before)
        self.record = {'stage': 'terra', 'role': 'terra', 'engine': 'opencode', 'command': self.command,
            'task_id': self.state['current_task']['id'], 'contract_hash': self.state['goal_contract']['hash'],
            'contract_revision': self.state['goal_contract']['revision'], 'source_revision': self.before['revision'],
            'launch_route': {key: self.state['settings']['roles']['terra'].get(key) for key in (
                'engine', 'provider', 'model', 'reasoning_effort')},
            'before_ref': str(before_ref), 'events': str(self.run / 'builder.jsonl'),
            'output': str(self.run / 'builder.json')}
        self.descriptor = {'version': 1, 'files': {}, 'manifest': str(manifest),
            'manifest_sha256': util.file_hash(manifest), 'child_identity': {
                'executable': self.command[0], 'executable_sha256': util.file_hash(self.command[0])}}
        # Only native provider/isolation/kernel conformance is controlled;
        # original journal/approval/runtime, birth ownership and deltas are real.
        self.isolation = SimpleNamespace(_writer_execution=None)
        self.config = patch.object(self.owner, '_native_config', return_value=(
            {str(manifest): util.file_hash(manifest)}, self.descriptor))
        self.verify = patch.object(protection.containment, 'verify', side_effect=lambda value: deepcopy(value))
        self.config.start(); self.verify.start()
        self.addCleanup(self.config.stop); self.addCleanup(self.verify.stop)

    def preparation(self, *, save=True):
        handle = self.factory.prepare_launch(self.state, self.before, self.record, boundary=self.boundary,
            command=self.command, environment=self.environment, native_role_isolation=self.isolation)
        self.state['active_stage'] = self.record
        if save: self.sink.write(self.run / 'state.json', self.state)
        return handle

    def stopped(self, *, change=None):
        handle = self.preparation()
        options = {'cwd': self.root, 'env': self.environment, 'start_new_session': True,
                   'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        with self.context.held_launch(handle, command=self.command, environment=self.environment, options=options,
                receipt_path=self.run / 'supervision.json', timeout=15) as (child, on_root):
            code, expired = processes.wait_for_stage(child, 15, lambda _: None, on_root=on_root)
        self.assertEqual((0, False), (code, expired))
        self.assertFalse(processes.live_processes([child.supervision['provider']]))
        if change: change()
        after = source_scope.snapshot(self.root, self.state)
        events, output, after_ref = Path(self.record['events']), Path(self.record['output']), self.run / 'builder.after.json'
        events.write_text('{"controlled":"not-native-provider-evidence"}\n'); output.write_text('{}\n')
        util.atomic_json(after_ref, after)
        self.record.update(exit_code=code, after_ref=str(after_ref), source_revision=after['revision'], finished_at=util.now())
        self.factory.finish_launch(handle, self.record, after)
        self.state.pop('active_stage')
        return handle, after

    def test_original_dual_seal_actual_write_and_stopped_delta_join(self):
        protected = (self.root / 'tracked.txt').read_bytes()
        handle, after = self.stopped()
        value = self.owner._writer_executions[handle.nonce]
        self.assertNotEqual(self.before['revision'], after['revision'])
        self.assertEqual(protected, (self.root / 'tracked.txt').read_bytes())
        self.assertEqual('bounded actual write\n', (self.root / 'ignored.txt').read_text())
        self.assertEqual(['ignored.txt', 'future.txt'], value.delta['body']['write_paths'])
        facts = self.owner.readonly_task_authority(self.state, snapshot=after)
        self.assertEqual(after, facts['binding']['source'])
        # Reauthentication survives a new concrete original factory, not a
        # serialized Python handle or reconstructed legacy source adoption.
        reopened = type(self.factory)(self.root, self.run, run_handle=self.run_handle,
            checkout_handle=self.checkout_handle, start_state=self.state, snapshot=after)
        self.assertEqual(after, reopened.protection.readonly_task_authority(self.state, snapshot=after)['binding']['source'])

    def test_missing_real_durable_active_stage_refuses_before_any_target(self):
        handle = self.preparation(save=False)
        value = self.owner._writer_executions[handle.nonce]
        with self.assertRaisesRegex(util.Paused, 'durable active stage'): value.recheck()
        self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())
        self.assertIsNone(value.admission)

    def test_provider_verify_failure_after_prepare_leaves_target_unreleased(self):
        handle = self.preparation()
        value = self.owner._writer_executions[handle.nonce]
        # The actual caller must still verify its final provider after prepare.
        # A setup refusal leaves original pending journals and cannot become a
        # durable stage/launch solely because preparation returned a handle.
        self.sink.blocked = True; self.sink.error = 'controlled final provider verification failed'
        with self.assertRaisesRegex(util.Paused, 'provider verification failed'): value.recheck()
        self.assertIsNone(value.admission)
        self.assertFalse(any(row['kind'] in ('sealed', 'released', 'finished') for row in self.owner._rows()))
        self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())

    def test_partial_dual_seal_cleans_original_child_without_token_or_write(self):
        handle = self.preparation()
        original = self.owner._append
        def fail(kind, body):
            if kind == 'sealed': raise OSError('controlled native seal after domain admission')
            return original(kind, body)
        options = {'cwd': self.root, 'env': self.environment, 'start_new_session': True,
                   'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        with patch.object(self.owner, '_append', side_effect=fail), self.assertRaises(util.Paused):
            with self.context.held_launch(handle, command=self.command, environment=self.environment, options=options,
                    receipt_path=self.run / 'supervision.json', timeout=15) as (child, on_root):
                processes.wait_for_stage(child, 15, lambda _: None, on_root=on_root)
        self.assertIsNotNone(child.returncode)
        self.assertFalse(processes.live_processes([child.supervision['provider']]))
        self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())
        self.assertFalse(any(row['kind'] in ('released', 'finished') for row in self.owner._rows()))
        with self.assertRaises(util.Paused): self.owner.readonly_task_authority(self.state, snapshot=self.before)

    def test_uncharged_mutation_and_foreign_pending_handle_refuse(self):
        handle = self.preparation()
        value = self.owner._writer_executions[handle.nonce]
        self.state['settings']['roles']['terra']['reasoning_effort'] = 'max'
        with self.assertRaises(util.Paused): value.recheck()
        self.state['settings']['roles']['terra']['reasoning_effort'] = 'high'
        self.owner._pending[handle.nonce] = object()
        with self.assertRaises(util.Paused): value.recheck()
        self.assertIsNone(value.admission)

    def test_exact_protected_write_is_preserved_and_cannot_finish(self):
        with self.assertRaisesRegex(util.Paused, 'outside exact enforced grants'):
            self.stopped(change=lambda: (self.root / 'tracked.txt').write_text('retained actual wrong write\n'))
        self.assertEqual('retained actual wrong write\n', (self.root / 'tracked.txt').read_text())
        self.assertFalse(any(row['kind'] == 'finished' for row in self.owner._rows()))

    def test_changed_head_cannot_mint_stopped_delta_or_readonly_authority(self):
        def head():
            subprocess.run(['git', '-C', str(self.root), '-c', 'user.name=Fixture', '-c',
                'user.email=fixture@example.test', 'commit', '--allow-empty', '-qm', 'changed'],
                check=True, capture_output=True)
        with self.assertRaises(util.Paused): self.stopped(change=head)
        current = source_scope.snapshot(self.root, self.state)
        with self.assertRaises(util.Paused): self.owner.readonly_task_authority(self.state, snapshot=current)


# Late final-seal controls use the original fixture helpers only. They never
# invoke or inherit any existing test method, and preserve the original guard.
class FinalWriterSealControls(unittest.TestCase):
    setUp = OriginalWriterTransportControls.setUp
    prepare = OriginalWriterTransportControls.prepare
    acknowledge = OriginalWriterTransportControls.acknowledge
    commit = OriginalWriterTransportControls.commit
    preparation = OriginalWriterTransportControls.preparation

    def _refuse_at_final_seal(self, mutation, retained):
        handle = self.preparation()
        writer = self.owner._writer_executions[handle.nonce]
        original_seal = writer.seal
        entered = []

        def late_seal(barrier, child, tree):
            self.assertFalse(entered, 'controlled final seal was replayed')
            # All earlier on_root/native release checks have already passed.
            # Preserve the original guard; mutate only its owned fixture input.
            mutation()
            entered.append(True)
            return original_seal(barrier, child, tree)

        options = {'cwd': self.root, 'env': self.environment, 'start_new_session': True,
                   'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
        with patch.object(writer, 'seal', side_effect=late_seal), self.assertRaises(util.Paused):
            with self.context.held_launch(handle, command=self.command, environment=self.environment,
                    options=options, receipt_path=self.run / 'supervision.json', timeout=15) as (child, on_root):
                processes.wait_for_stage(child, 15, lambda _: None, on_root=on_root)
        self.assertEqual([True], entered, 'an earlier refusal cannot stand in for the final seal guard')
        self.assertIsNotNone(child.returncode)
        self.assertFalse(processes.live_processes([child.supervision['provider']]))
        self.assertIsNone(writer.admission)
        self.assertFalse(any(row['kind'] in ('sealed', 'released', 'finished') for row in self.owner._rows()))
        retained()

    def test_late_selected_ignored_edit_is_retained_without_target_execution(self):
        def mutation():
            (self.root / 'ignored.txt').write_text('late selected edit\n')
        self._refuse_at_final_seal(mutation, lambda: self.assertEqual(
            'late selected edit\n', (self.root / 'ignored.txt').read_text()))

    def test_late_selected_future_creation_is_retained_without_target_execution(self):
        def mutation():
            (self.root / 'future.txt').write_text('late selected creation\n')
        def retained():
            self.assertEqual('late selected creation\n', (self.root / 'future.txt').read_text())
            self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())
        self._refuse_at_final_seal(mutation, retained)

    def test_late_selected_ignored_deletion_is_retained_without_target_execution(self):
        def mutation():
            (self.root / 'ignored.txt').unlink()
        self._refuse_at_final_seal(mutation, lambda: self.assertFalse((self.root / 'ignored.txt').exists()))

    def test_late_current_task_change_refuses_before_target_execution(self):
        def mutation():
            self.state['current_task']['id'] = 'task-controlled-late-change'
        def retained():
            self.assertEqual('task-controlled-late-change', self.state['current_task']['id'])
            self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())
        self._refuse_at_final_seal(mutation, retained)

    def test_late_saved_reasoning_route_change_refuses_before_target_execution(self):
        def mutation():
            self.state['settings']['roles']['terra']['reasoning_effort'] = 'max'
        def retained():
            self.assertEqual('max', self.state['settings']['roles']['terra']['reasoning_effort'])
            self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())
        self._refuse_at_final_seal(mutation, retained)

    def test_late_durable_sink_refusal_blocks_target_execution(self):
        def mutation():
            self.sink.blocked = True
            self.sink.error = 'controlled late durable sink refusal'
        def retained():
            self.assertTrue(self.sink.blocked)
            self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())
        self._refuse_at_final_seal(mutation, retained)


class OperationWriterHistoryControls(unittest.TestCase):
    """One real owned fixture; each check is a separate original ledger operation."""
    prepare = OriginalWriterTransportControls.prepare
    acknowledge = OriginalWriterTransportControls.acknowledge
    def commit(self):
        # Approve the missing nested future leaf through the original fixture
        # lifecycle, before its initial approval and actual durable assignment.
        goal = self.state['goal_contract']
        future = 'nested/future.txt'
        # The original fixture shares its path list across these three fields.
        # Replace each field independently so the approved grant stays unique.
        initial = goal['body']['initial_task']
        milestone = goal['body']['milestones'][0]
        case = self.state['settings']['design_manifest']['body']['cases'][0]
        initial['affected_paths'] = [*initial['affected_paths'], future]
        milestone['affected_paths'] = [*milestone['affected_paths'], future]
        case['implementation_paths'] = [*case['implementation_paths'], future]
        goal['hash'] = util.digest({key: goal[key] for key in ('task_id', 'revision', 'body')})
        self.token = original_fixture.contracts.token(goal)
        self.state['displayed_goal'] = self.token
        self.state['planning']['final_token'] = self.token
        self.owner._append('planning-committed', {'contract': deepcopy(goal),
            'settings': deepcopy(self.state['settings']), 'source': deepcopy(self.raw),
            'token': self.token, 'pins': {str(self.anchor): util.file_hash(self.anchor)}})
        return OriginalWriterTransportControls.commit(self)
    preparation = OriginalWriterTransportControls.preparation

    @classmethod
    def setUpClass(cls):
        fixture = cls('runTest')
        cls.addClassCleanup(fixture.doCleanups)
        OriginalWriterTransportControls.setUp(fixture)
        fixture.boundary['write_paths'].append('nested/future.txt')
        fixture.boundary['writer_path_kinds']['nested/future.txt'] = 'file'
        fixture.environment['AUTOCODE_TOOL_CONTAINMENT'] = json.dumps(fixture.boundary)
        fixture.handle = fixture.preparation()
        fixture.writer = fixture.owner._writer_executions[fixture.handle.nonce]
        cls.fixture = fixture

    def setUp(self):
        # Do not share unittest result/method/cleanup state; only the owned inputs.
        for name, value in self.fixture.__dict__.items():
            if not name.startswith('_'):
                setattr(self, name, value)

    def _during_recheck(self, mutation, restore=lambda: None):
        writer = self.writer
        original = writer.authority.admissions.authenticate
        entered = []
        def authenticate(kind, query, selector):
            result = original(kind, query, selector)
            if not entered:
                entered.append(kind)
                mutation()
            return result
        try:
            with patch.object(writer.authority.admissions, 'authenticate', side_effect=authenticate):
                with self.assertRaises(util.Paused): writer.recheck()
        finally:
            restore()
        self.assertEqual(['approval-runtime'], entered, 'mutation must reach original verified authority')
        self.assertNotIn(writer.prepared.id, writer.authority.admissions._ready)
        self.assertIsNone(writer.admission)
        self.assertFalse(any(row['kind'] in ('sealed', 'released', 'finished') for row in self.owner._rows()))
        self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())

    def test_each_recheck_cold_verifies_binding_and_preserves_all_queries(self):
        self.assertIn('nested/future.txt', self.state['current_task']['affected_paths'])
        self.assertFalse((self.root / 'nested').exists())
        writer = self.writer
        cold = self.owner.writer_history_authority
        authenticate = writer.authority.admissions.authenticate
        kinds = []
        def observed(kind, query, selector):
            kinds.append(kind)
            return authenticate(kind, query, selector)
        with patch.object(self.owner, 'writer_history_authority', wraps=cold) as original:
            with patch.object(writer.authority.admissions, 'authenticate', side_effect=observed):
                writer.recheck()
                self.assertEqual(1, original.call_count)
                writer.recheck()
                self.assertEqual(2, original.call_count, 'a second operation must be cold')
        self.assertEqual(['approval-runtime', 'containment', 'dispatch'] * 2, kinds)
        self.assertIsNone(writer.admission)
        self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())

    def test_within_operation_source_edit_restore_refuses(self):
        def mutation():
            path = self.root / 'ignored.txt'; before = path.read_bytes()
            path.write_bytes(b'controlled temporary source edit\n'); path.write_bytes(before)
        self._during_recheck(mutation)

    def test_within_operation_evidence_edit_restore_refuses(self):
        def mutation():
            before = self.anchor.read_bytes()
            self.anchor.write_bytes(b'controlled temporary approval edit\n'); self.anchor.write_bytes(before)
        self._during_recheck(mutation)

    def test_within_operation_native_row_replacement_refuses(self):
        def mutation():
            path = sorted(self.owner.root.iterdir())[0]
            before = path.read_bytes(); mode = path.stat().st_mode & 0o777
            path.unlink(); path.write_bytes(before); path.chmod(mode)
        self._during_recheck(mutation)

    def test_within_operation_namespace_create_remove_refuses(self):
        def mutation():
            path = self.owner.root / 'controlled-newer-unknown-row'
            path.write_text('not authority'); path.unlink()
        self._during_recheck(mutation)

    def test_within_operation_current_task_change_refuses(self):
        task = self.state['current_task']; before = task['id']
        self._during_recheck(lambda: task.update(id='controlled-new-task'), lambda: task.update(id=before))

    def test_within_operation_route_change_refuses(self):
        route = self.state['settings']['roles']['terra']; before = route['reasoning_effort']
        self._during_recheck(lambda: route.update(reasoning_effort='max'), lambda: route.update(reasoning_effort=before))

    def test_within_operation_durable_candidate_change_refuses(self):
        task = self.sink.durable_candidate['current_task']; before = task['id']
        self._during_recheck(lambda: task.update(id='controlled-durable-task'), lambda: task.update(id=before))

    def test_operation_end_failure_never_publishes_ready(self):
        writer = self.writer
        original = writer.authority.admissions._recheck
        entered = []
        def changed(*args, **kwargs):
            result = original(*args, **kwargs)
            entered.append(True)
            path = self.root / 'ignored.txt'; before = path.read_bytes()
            path.write_bytes(b'controlled after final dispatch\n'); path.write_bytes(before)
            return result
        with patch.object(writer.authority.admissions, '_recheck', side_effect=changed):
            with self.assertRaises(util.Paused): writer.recheck()
        self.assertEqual([True], entered, 'original full domain recheck must complete first')
        self.assertNotIn(writer.prepared.id, writer.authority.admissions._ready)
        self.assertIsNone(writer.admission)
        self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())


    def test_cold_containment_order_and_cached_fact_copy_are_preserved(self):
        writer = self.writer
        authority = writer.authority
        query = {'workspace': str(self.root), 'run_dir': str(self.run),
            'ledger': str(authority.admissions.root), 'boundary': deepcopy(writer.boundary),
            'command_sha256': util.digest(writer.command),
            'environment_sha256': util.digest(writer.environment)}
        cold = authority.authenticate('containment', query, deepcopy(writer.boundary))
        self.assertEqual(json.loads(writer.prepared.payload)['containment'], cold)
        binding = protection._json(authority._binding)
        original_history = authority._owner.writer_history_authority
        def reordered_history(selected):
            # Authenticate the actual original rows and hashes first. Reorder
            # only their dictionary/list presentation at the adapter boundary;
            # no retained record, hash, binding or authority is changed.
            facts = original_history(selected)
            pins = facts['evidence_hashes']
            names = sorted(pins, reverse=True)
            self.assertNotEqual(names, sorted(names),
                'the actual memo input itself must expose canonical order loss')
            facts['evidence_hashes'] = {name: pins[name] for name in names}
            facts['protected_refs'] = list(names)
            return facts
        with patch.object(authority._owner, 'writer_history_authority', side_effect=reordered_history):
            # This full unmemoized proof is the expected alternate presentation,
            # separate from the unchanged saved/cold proof checked above.
            expected = authority.authenticate('containment', query, deepcopy(writer.boundary))
            with authority.admissions._operation() as operation:
                with authority._recheck_scope(operation):
                    first = authority.authenticate('containment', query, deepcopy(writer.boundary))
                    self.assertEqual(expected, first)
                    again = authority.authenticate('containment', query, deepcopy(writer.boundary))
                    self.assertEqual(expected, again)
                    returned = authority._history_facts(binding)
                    self.assertEqual(list(returned['evidence_hashes']), returned['protected_refs'])
                    self.assertNotEqual(returned['protected_refs'], sorted(returned['protected_refs']))
                    returned['evidence_hashes'].clear()
                    returned['protected_refs'].clear()
                    returned['binding']['task']['id'] = 'controlled-returned-copy-change'
                    self.assertEqual(expected,
                        authority.authenticate('containment', query, deepcopy(writer.boundary)))
        self.assertIsNone(writer.admission)
        self.assertEqual('selected ignored source\n', (self.root / 'ignored.txt').read_text())


    def test_complete_chain_copies_preserve_cold_rows_and_all_phase_maps(self):
        writer = self.writer
        cold = self.owner._chain()
        self.assertEqual(5, len(cold))
        self.assertTrue(cold[1], 'the real original prepared phase must be present')
        with writer.authority.admissions._operation() as operation:
            with writer.authority._recheck_scope(operation):
                first = self.owner._chain()
                self.assertEqual(cold, first)
                # Original maps refer to row objects. None of those caller
                # mutations may alter a later native replay in this operation.
                first[0][0]['body']['policy'] = 'controlled-returned-row-change'
                for phases in first[1:]:
                    phases.clear()
                    phases['controlled-returned-map-change'] = {'body': {'changed': True}}
                self.assertEqual(cold, self.owner._chain())
                self.assertEqual(cold, self.owner._chain())
                scope = self.owner._writer_chain_snapshot
                saved = scope.payload
                try:
                    scope.payload = saved.replace(b'kernel-grants-upper-bound-v1', b'controlled-forged-cold-policy')
                    self.assertNotEqual(saved, scope.payload)
                    with self.assertRaises(util.Paused): self.owner._chain()
                finally:
                    scope.payload = saved
                before = deepcopy(scope._chain_values)
                try:
                    scope._chain_values[0][0]['body']['policy'] = 'controlled-forged-cached-row'
                    with self.assertRaises(util.Paused): self.owner._chain()
                finally:
                    scope._chain_values = before
                self.assertEqual(cold, self.owner._chain())
        self.assertEqual(cold, self.owner._chain(), 'next operation still uses the original cold replay')
        self.assertIsNone(writer.admission)

    def test_native_append_inside_original_recheck_refuses_without_journal_growth(self):
        before = self.owner._rows()
        self._during_recheck(lambda: self.owner._append('planning-committed', {
            'controlled': 'must never become retained native authority'}))
        self.assertEqual(before, self.owner._rows())

    def test_chain_scope_foreign_expired_and_unsupported_history_fail_closed(self):
        writer = self.writer
        rows = self.owner._chain()[0]
        self.assertTrue(protection._WriterChainSnapshot._eligible(rows))
        self.assertFalse(protection._WriterChainSnapshot._eligible(
            [*rows, {'kind': 'check-prepared'}]))
        with writer.authority.admissions._operation() as operation:
            with writer.authority._recheck_scope(operation):
                saved = self.owner._writer_chain_snapshot
                with self.assertRaises(util.Paused):
                    with self.owner._writer_chain_scope(writer, operation):
                        self.fail('a reentrant scope cannot reuse native authority')
                with patch.object(self.owner, '_writer_chain_snapshot', object()):
                    with self.assertRaises(util.Paused): self.owner._chain()
            # This concrete execution-looking object has no original pending
            # execution identity. No supplied tuple/callback grants it custody.
            for foreign in (object(), SimpleNamespace(owner=self.owner, authority=writer.authority, native=writer.native)):
                with self.assertRaises(util.Paused):
                    with self.owner._writer_chain_scope(foreign, operation):
                        self.fail('foreign execution must be refused before cold acquisition')
            with patch.object(self.owner, '_writer_chain_snapshot', saved):
                with self.assertRaises(util.Paused): self.owner._chain()
            # Force only the conservative eligibility choice, with actual
            # original rows/owner/closure. A cold verifier refusal on a hit
            # must propagate; no cached result may mask unsupported history.
            with patch.object(protection._WriterChainSnapshot, '_eligible', return_value=False):
                with writer.authority._recheck_scope(operation):
                    # An injected reuse flag cannot mask the cold refusal.
                    self.owner._writer_chain_snapshot.reusable = True
                    with patch.object(self.owner, '_chain_cold', side_effect=util.Paused(
                            'PAUSED_VISUAL_EVIDENCE', 'controlled unsupported cold boundary refusal')):
                        with self.assertRaises(util.Paused): self.owner._chain()
        self.assertIsNone(writer.admission)
        self.assertFalse(any(row['kind'] in ('sealed', 'released', 'finished') for row in self.owner._rows()))

    def test_each_guard_observes_metadata_once_and_next_guard_is_fresh(self):
        # Count physical lstats made by the original metadata components,
        # separately from unchanged ownership/signature/query validation.
        writer = self.writer
        physical = Path.lstat
        active = [0]
        counts = {}
        def observed(path, *args, **kwargs):
            if active[0]:
                counts[path] = counts.get(path, 0) + 1
            return physical(path, *args, **kwargs)
        def component(original):
            def call(*args, **kwargs):
                active[0] += 1
                try:
                    return original(*args, **kwargs)
                finally:
                    active[0] -= 1
            return call
        original_ancestor = writers._WriterHistoryBatch._ancestor
        def construction_ancestor(path, observations=None):
            if observations is not None:
                return original_ancestor(path, observations)
            active[0] += 1
            try:
                return original_ancestor(path)
            finally:
                active[0] -= 1
        with patch.object(writers._WriterHistoryBatch, '_ancestor', side_effect=construction_ancestor), \
                patch.object(Path, 'lstat', new=observed), \
                writer.authority.admissions._operation() as operation:
            with writer.authority._recheck_scope(operation):
                batch = writer.authority._history_batch
                initial = dict(counts)
                self.assertGreater(len(initial), 1)
                self.assertEqual(set(batch.ancestors), set(initial))
                self.assertEqual({1}, set(initial.values()),
                    'construction must physically observe each shared authority ancestor once')
                self.assertIn(self.root / 'nested', initial)
                self.assertIsNone(batch.ancestors[self.root / 'nested'])
                counts.clear()
                with patch.object(batch, '_members', side_effect=component(batch._members)), \
                        patch.object(batch, '_ancestor', side_effect=component(batch._ancestor)), \
                        patch.object(batch, '_stamp', side_effect=component(batch._stamp)):
                    batch.check()
                    first = dict(counts)
                    self.assertGreater(len(first), 1)
                    self.assertEqual({1}, set(first.values()),
                        'membership, type, ancestor and stamp must share one physical observation')
                    self.assertIn(self.root / 'nested/future.txt', first)
                    self.assertIn(self.root / 'nested', first)
                    counts.clear()
                    batch.check()
                    self.assertEqual(first, counts,
                        'a subsequent guard must perform fresh physical observations')
        # Structural namespace checks grant no authority. They retain broken
        # symlink, nonregular member and missing future-namespace behavior.
        with tempfile.TemporaryDirectory(prefix='writer-metadata-shapes-') as name:
            root = Path(name).resolve()
            batch = object.__new__(type(batch))
            batch.namespaces = (root / 'missing',)
            self.assertEqual(set(), batch._members())
            batch.namespaces[0].symlink_to(root / 'absent-target')
            with self.assertRaises(util.Paused): batch._members()
            batch.namespaces[0].unlink()
            batch.namespaces = (root,)
            os.mkfifo(root / 'nonregular')
            with self.assertRaises(util.Paused): batch._members()
            (root / 'nonregular').unlink()
            (root / 'regular').write_bytes(b'owned bytes')
            self.assertEqual({root / 'regular'}, batch._members())
        self.assertIsNone(writer.admission)


class RetainedBlobReadControls(unittest.TestCase):
    """Controlled namespace seam only; these reads grant no native/image proof."""
    def test_retained_blob_is_hashed_and_returned_from_one_read(self):
        history = protection.history
        with tempfile.TemporaryDirectory(prefix='native-blob-read-') as name:
            root = Path(name).resolve()
            run = root / '.autocode' / 'runs' / 'owned'
            run.mkdir(parents=True)
            class NoAuthority:
                def verify_namespace(self, **kwargs):
                    raise AssertionError('This structural read cannot grant native authority')
            owner = history.NativeReviewHistory(root, run, protection=NoAuthority())
            original = b'original retained owned bytes\n'
            digest = history._sha(original)
            member = str(root / 'original.txt')
            metadata = {'kind': 'reviews', 'version': 1, 'member_hashes': {member: digest}}
            raw = history._canonical(metadata)
            publication = owner.root / 'reviews' / history._sha(raw)
            publication.mkdir(parents=True)
            manifest = publication / 'manifest.json'
            manifest.write_bytes(raw)
            blob = publication / digest
            blob.write_bytes(original)
            selector = {'kind': 'reviews', 'path': str(manifest), 'sha256': history._sha(raw)}
            read_bytes = Path.read_bytes
            reads = []
            def changing_second_read(path):
                if path == blob:
                    reads.append(path)
                    return original if len(reads) == 1 else b'unverified second-read replacement'
                return read_bytes(path)
            with patch.object(owner, '_guard', return_value=None) as guard, \
                    patch.object(Path, 'read_bytes', new=changing_second_read):
                loaded, members = owner._load(selector)
            self.assertEqual([blob], reads, 'exactly one physical read per retained member')
            self.assertEqual(metadata, loaded)
            self.assertEqual({member: original}, members)
            self.assertEqual(original, owner._reader(loaded, members)(member))
            guard.assert_called_once_with(selector, metadata,
                phase='before-native-replay', members=metadata['member_hashes'])
            reads.clear()
            def changed_first_read(path):
                if path == blob:
                    reads.append(path)
                    return b'changed first-read bytes'
                return read_bytes(path)
            with patch.object(owner, '_guard', return_value=None), \
                    patch.object(Path, 'read_bytes', new=changed_first_read):
                with self.assertRaisesRegex(util.Paused, 'Protected original evidence copy changed'):
                    owner._load(selector)
            self.assertEqual([blob], reads)


if __name__ == '__main__':
    unittest.main()
