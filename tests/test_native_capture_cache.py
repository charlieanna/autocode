"""Actual ordinary final-task handoff; controlled acquisition/provenance seam.

The original PlanningCalls guard is exercised. Ordinary lifecycle assignment
creates the real new UUID/archive with unchanged source and criteria. Capture
acquisition and publication checks here are explicitly controlled; no browser,
model, native acquisition, durable journal or image qualification is claimed.
"""
from copy import deepcopy
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import autocode_goal_lifecycle as lifecycle
import autocode_native_assignment as assignment
import autocode_native_calls as calls
import autocode_util as util
import autocode_visual_checkpoint as checkpoint
import autocode_visual_scope as scope
from tests.test_native_assignment import fixture


def setup_caller():
    state, source, decision = fixture()
    state.update(task='controlled exact operator input', next_stage='sol')
    native = object.__new__(calls.PlanningCalls)
    native.initial_task = state['task']
    native.sink = SimpleNamespace(require_ready=lambda: None, write=lambda *a, **k: None)
    native.snapshot = lambda value: deepcopy(source)
    native._captures = native._captures_source = native._captures_binding = None
    observed = {'acquisitions': [], 'published': 0, 'fail_next': False}
    def binding(value):
        return {'contract': deepcopy(value['goal_contract']), 'settings': deepcopy(value['settings']),
                'task': deepcopy(value['current_task']), 'source': deepcopy(source), 'runtime_sha256': 'c' * 64}
    def authority(value, *, snapshot):
        if snapshot != source: raise util.Paused('HELD', 'foreign controlled source')
        return {'binding': binding(value), 'pins': {'journal-' + str(observed['published']): 'e' * 64}}
    native.context = SimpleNamespace(factory=SimpleNamespace(protection=SimpleNamespace(readonly_task_authority=authority)))
    def image_authority(*, state, snapshot, captures):
        if set(captures) != {'chat', 'work'} or any(item['binding'] != binding(state) for item in captures.values()):
            raise util.Paused('HELD', 'controlled publication belongs to another exact task binding')
        return {'controlled': 'no actual native image authority'}
    native.image_capture_authority = image_authority
    class Acquisition:
        def __init__(self, context, value, *, persist): self.value = value
        def all_cases(self):
            observed['acquisitions'].append(deepcopy(self.value['current_task']))
            if observed['fail_next']:
                observed['fail_next'] = False
                raise util.Paused('HELD', 'controlled acquisition failed before return')
            observed['published'] += 1
            return {cid: {'binding': binding(self.value)} for cid in ('chat', 'work')}
    return native, state, source, decision, observed, Acquisition


def assign_final_validate(state, source, decision):
    derived = scope.derive(state['goal_contract']['body'], state['settings']['design_manifest']['body'],
        state['current_task'], accepted_milestones=(), accepted_case_input_paths={})
    normalized = assignment.normalized(state, decision)
    proof = checkpoint.VerifiedCheckpoint(checkpoint.binding(state, source), source['revision'], 'c' * 64,
        json.dumps(derived, sort_keys=True, separators=(',', ':')), (), (), True, 'd' * 64,
        authorized_assignment=checkpoint.assignment_identity(normalized))
    return lifecycle.assign_task(state, decision, source, visual_proof=proof)


class CaptureCacheCallerTests(unittest.TestCase):
    def test_actual_final_validate_uuid_with_unchanged_source_reacquires_both_cases(self):
        native, state, source, decision, observed, acquisition = setup_caller()
        original_task, original_source = deepcopy(state['current_task']), deepcopy(source)
        with patch.object(calls.captures, 'NativeCaptureExecution', acquisition):
            native.guard(state, 'sol')
            self.assertEqual(assign_final_validate(state, source, decision), 'validate')
            self.assertNotEqual(state['current_task']['id'], original_task['id'])
            self.assertEqual(state['task_archive'], [original_task])
            self.assertEqual(source, original_source)
            self.assertEqual(state['current_task']['acceptance_criteria'], ['C1', 'C2'])
            native.guard(state, 'sol')
        self.assertEqual([row['id'] for row in observed['acquisitions']],
                         [original_task['id'], state['current_task']['id']])
        self.assertEqual(set(native._captures), {'chat', 'work'})

    def test_same_authenticated_binding_reuses_acquisition_despite_new_journal_pins(self):
        native, state, source, decision, observed, acquisition = setup_caller()
        with patch.object(calls.captures, 'NativeCaptureExecution', acquisition):
            native.guard(state, 'sol')
            native.guard(state, 'sol')
            assign_final_validate(state, source, decision)
            native.guard(state, 'sol')
            native.guard(state, 'sol')
        self.assertEqual(len(observed['acquisitions']), 2)
        self.assertEqual(observed['published'], 2)

    def test_failed_new_task_acquisition_cannot_publish_old_capture_for_new_task(self):
        native, state, source, decision, observed, acquisition = setup_caller()
        with patch.object(calls.captures, 'NativeCaptureExecution', acquisition):
            native.guard(state, 'sol')
            old = deepcopy(native._captures)
            assign_final_validate(state, source, decision)
            observed['fail_next'] = True
            with self.assertRaisesRegex(util.Paused, 'acquisition failed before return'):
                native.guard(state, 'sol')
            self.assertEqual(native._captures, old)
            with self.assertRaisesRegex(util.Paused, 'another exact task binding'):
                native.image_capture_authority(state=state, snapshot=source, captures=old)
        self.assertEqual(len(observed['acquisitions']), 2)
        self.assertEqual(observed['published'], 1)


if __name__ == '__main__': unittest.main()
