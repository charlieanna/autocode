"""Disposable operational-state dashboard fixture for browser-only checks.

The fixture never opens a real workspace, provider, or runner. Its data belongs
only to this test server, so browser captures can exercise the same state shapes
without putting fixture copy in production state or mutating a user's task.
"""
import copy
import json
import os
from pathlib import Path
import sys
import tempfile
import time
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console, Handler, ThreadingHTTPServer


FIXTURE_NOW = '2026-09-22T12:41:00Z'


def plan_contract(*, approval_status='approved', origin='astra_finalize'):
    """Return test-only plan data that exercises the document presentation."""
    criteria = [
        {'id': 'C1', 'criterion': 'Confirm exact responsive viewport dimensions.',
         'verification_method': 'Record the current viewport values.', 'human_review': False},
        {'id': 'C2', 'criterion': 'Verify no unintended clipping or overlap.',
         'verification_method': 'Inspect the captured bounds.', 'human_review': False},
        {'id': 'C3', 'criterion': 'Verify every touch target is at least 44×44 px.',
         'verification_method': 'Measure interactive controls.', 'human_review': False},
        {'id': 'C4', 'criterion': 'Verify semantic variables and AA contrast.',
         'verification_method': 'Inspect resolved semantic tokens.', 'human_review': False},
        {'id': 'C5', 'criterion': 'Verify canonical component-instance reuse.',
         'verification_method': 'Compare reusable browser patterns.', 'human_review': False},
        {'id': 'C6', 'criterion': 'Verify approval and Start building remain separate.',
         'verification_method': 'Exercise the approval gate.', 'human_review': False},
    ]
    return {
        'revision': 7,
        'hash': 'fixture-plan-revision-7',
        'origin': origin,
        'approval_status': approval_status,
        'approval_event': {'at': FIXTURE_NOW, 'actor': 'Fixture reviewer'},
        'body': {
            'intended_outcome': 'Repair runtime monitoring, approval receipts, and interrupted-task recovery.',
            'requirements': ['Keep runtime truth and saved activity distinct.',
                             'Keep approval and implementation as separate actions.'],
            'constraints': ['Never mutate a real task from this fixture.',
                            'Keep the mobile shell within its viewport.'],
            'technical_approach': ['Render state from saved records.',
                                   'Validate shell geometry at each target viewport.'],
            'milestones': [{'id': 'M1', 'objective': 'Establish the responsive shell and accessible visual system.'}],
            'accepted_assumptions': ['The saved revision remains authoritative until a separate approval receipt is confirmed.'],
            'history': ['Revision 6 recorded the same recovery constraint before this revised plan.'],
            'acceptance_criteria': criteria,
        },
    }


def base_state(workspace, task, *, status, stage='terra', role='terra'):
    state = {
        'workspace': str(workspace),
        'created_at': '2026-09-22T12:00:00Z',
        'updated_at': FIXTURE_NOW,
        'task': task,
        'status': status,
        'phase': 'EXECUTING',
        'iteration': 31,
        'active_stage': {'stage': stage, 'role': role, 'started_at': FIXTURE_NOW},
        'settings': {
            'joint_planning': True,
            'workflow': {'mode': 'glm_first_v1'},
            'limits': {'iteration_ceiling': 40},
            'roles': {
                'astra': {'model': 'gpt-6-astra', 'reasoning_effort': 'high'},
                'terra': {'model': 'gpt-5.6-terra', 'reasoning_effort': 'high'},
                'sol': {'model': 'gpt-5.6-sol', 'reasoning_effort': 'high'},
            },
        },
        'current_task': {
            'objective': 'Complete the accepted dashboard repair while preserving verified behavior and saved work.',
            'requirements': ['Use deterministic browser evidence.', 'Preserve task controls.'],
            'validation_plan': ['Check all target viewport sizes.'],
        },
        'goal_contract': plan_contract(),
        'displayed_goal': 'r7:fixture-plan-revision-7',
        'stages': [{'stage': 'astra_finalize', 'role': 'astra', 'iteration': 30,
                    'finished_at': '2026-09-22T12:30:00Z', 'exit_code': 0}],
        'plan': ['Establish the shell.', 'Render the current task state.', 'Validate every viewport.'],
        'acceptance_criteria': [{'id': 'C1', 'criterion': 'Fixture state renders safely.',
                                 'verification_method': 'Browser-only fixture check.', 'human_review': False}],
        '_fixture_monitor': {
            'checked_at': FIXTURE_NOW,
            'checkpoint_updated': FIXTURE_NOW,
            'log_updated': FIXTURE_NOW,
            'workflow_mode': 'glm_first_v1',
            'roles': {'astra': {'model': 'gpt-6-astra', 'reasoning_effort': 'high'},
                      'terra': {'model': 'gpt-5.6-terra', 'reasoning_effort': 'high'},
                      'sol': {'model': 'gpt-5.6-sol', 'reasoning_effort': 'high'}},
            'iteration_limit': 40,
            'limits_known': True,
            'objective': 'Complete the accepted dashboard repair while preserving verified behavior and saved work.',
            'active_role': role,
            'next_stage': stage,
            'findings': [{'severity': 'medium', 'finding': 'Fixture evidence remains local and read-only.'}],
            'activity': [{'label': 'Browser fixture', 'status': 'completed', 'exit_code': 0}],
            'history': [{'stage': 'astra_finalize', 'role': 'astra', 'iteration': 30,
                         'finished_at': '2026-09-22T12:30:00Z', 'exit_code': 0}],
            'live': {'state': 'none', 'label': 'No active worker recorded'},
        },
    }
    # The task hero intentionally derives its title from the saved plan when
    # available. Keep that test-only plan outcome scenario-specific so each
    # matrix route proves it did not accidentally render another route's data.
    state['goal_contract']['body']['intended_outcome'] = task
    return state


def scenario_states(workspace):
    running = base_state(workspace, 'Repair runtime monitoring, approval receipts, and interrupted-task recovery',
                         status='RUNNING')
    running['_fixture_monitor']['live'] = {'state': 'alive', 'label': 'Worker verified alive', 'pid': 2418, 'elapsed': '00:09:12'}

    activity = base_state(workspace, 'Review saved activity and reconcile the interrupted build', status='RUNNING')
    activity['_fixture_monitor']['live'] = {'state': 'unknown', 'label': 'Live process could not be confirmed', 'pid': 2421}

    waiting = base_state(workspace, 'Confirm the routing choice for the dashboard recovery flow',
                         status='WAITING_FOR_USER', stage='glm_revise', role='glm')
    waiting['pending_questions'] = [
        {'id': 'question-1', 'question': 'The worker state says the planning step was saved, but the live process could not be confirmed. Should recovery preserve the saved revision and inspect the interrupted attempt before any new provider request is submitted?\n\nIf recovery succeeds, should the dashboard return to plan review, or remain paused until you explicitly choose Resume planning?',
         'options': ['Keep the saved revision', 'Start a new provider request'],
         'why': 'The first unresolved question is preserved.', 'proposed_default': 'Keep the saved revision'},
        {'id': 'question-2', 'question': 'Should recovery return to plan review?', 'options': ['Plan review', 'Remain paused']},
        {'id': 'question-3', 'question': 'Who should verify the recovered checkpoint?', 'options': ['Sol', 'Astra']},
    ]

    plan = base_state(workspace, 'Approve the exact plan revision before building',
                      status='AWAITING_GOAL_APPROVAL', stage='astra_finalize', role='astra')
    plan['goal_contract'] = plan_contract(approval_status='draft', origin='astra_finalize')
    # The task hero intentionally prioritizes the displayed plan outcome.
    # Keep this plan-only fixture's document outcome aligned with its task so
    # an independent flow route cannot inherit the running fixture title.
    plan['goal_contract']['body']['intended_outcome'] = plan['task']
    plan['active_stage']['finished_at'] = '2026-09-22T12:40:00Z'
    plan['active_stage'].pop('started_at', None)
    plan['_fixture_monitor']['next_stage'] = 'terra'
    plan['_fixture_monitor']['active_role'] = 'astra'

    paused = base_state(workspace, 'Recover the interrupted build without losing its checkpoint',
                        status='PAUSED_PROVIDER_UNCERTAIN')
    paused.pop('active_stage')
    paused['stop_reason'] = 'The saved worker stopped before its provider receipt could be confirmed.'
    paused['_fixture_attempt'] = 'req_autocode_20260922_141233_7f4a91'
    paused['_fixture_monitor']['live'] = {'state': 'unknown', 'label': 'Live worker status is unverified'}
    paused['_fixture_monitor']['next_stage'] = 'terra'

    recovery = base_state(workspace, 'Resume after a preserved feedback checkpoint',
                          status='PAUSED_INTERVENTION')
    recovery.pop('active_stage')
    recovery['stop_reason'] = 'Feedback was applied at the last safe checkpoint.'

    completed = base_state(workspace, 'Review completed runtime-monitoring acceptance checks', status='TASK_COMPLETE')
    completed.pop('active_stage')
    # These deliberately distinct values prove that the browser displays the
    # runner's saved completion record, rather than a monitor poll, stage end,
    # or evidence-record time.
    completed['completed_at'] = '2026-09-22T12:44:00Z'
    completed['updated_at'] = '2026-09-22T13:07:00Z'
    completed['stages'].append({'stage': 'sol', 'role': 'sol', 'iteration': 31,
                                'finished_at': '2026-09-22T12:48:00Z', 'exit_code': 0})
    completed['validation'] = {
        'source_revision': 'abc123',
        'recorded_at': '2026-09-22T12:51:00Z',
        'criterion_results': [{'id': 'C1', 'status': 'pass'}],
        'checks': [{'name': '8 checks were recorded as passing'}],
    }
    completed['_fixture_monitor'].update({
        'checked_at': '2026-09-22T13:03:00Z',
        'checkpoint_updated': '2026-09-22T13:02:00Z',
        'log_updated': '2026-09-22T13:01:00Z',
        'next_stage': None,
    })
    completed_freshness = copy.deepcopy(completed)
    completed_freshness['updated_at'] = '2026-09-22T14:07:00Z'
    completed_freshness['validation']['recorded_at'] = '2026-09-22T14:01:00Z'
    completed_freshness['validation']['source_revision'] = 'def456'
    completed_freshness['_fixture_monitor'].update({
        'checked_at': '2026-09-22T14:03:00Z',
        'checkpoint_updated': '2026-09-22T14:02:00Z',
        'log_updated': '2026-09-22T14:01:00Z',
    })
    completed_missing = copy.deepcopy(completed)
    completed_missing.pop('completed_at')
    completed_invalid = copy.deepcopy(completed)
    completed_invalid['completed_at'] = 'not-a-completion-timestamp'

    # The operational-fold browser probe needs representative saved content
    # that cannot safely fit in a one-line mobile metric. The production UI
    # must expose this text through an untruncated disclosure while retaining
    # a compact, above-fold fact label and freshness signal.
    long_objectives = {
        'running': 'Complete the accepted dashboard repair while preserving verified runtime truth, saved activity, explicit plan gates, recovery receipts, and responsive behavior at every supported viewport.',
        'waiting': 'Preserve the saved recovery decision, its exact first unresolved question, and the separate plan-approval gate while the developer chooses the safe next action.',
        'paused': 'Recover the interrupted build without losing its saved checkpoint, inspect the interrupted attempt before any new provider request, and resume only through a separate explicit action.',
        'completed': 'Review the authoritative completion record and its recorded checks while keeping evidence freshness independent and making the checks, changes, and history destinations available.',
    }
    for name, state in {'running': running, 'waiting': waiting, 'paused': paused, 'completed': completed}.items():
        state['_fixture_monitor']['objective'] = long_objectives[name]

    pending_answer = base_state(workspace, 'Answer the saved recovery questions without approving a plan',
                                status='WAITING_FOR_USER', stage='glm_revise', role='glm')
    pending_answer['goal_contract'] = plan_contract(approval_status='draft', origin='astra_finalize')
    pending_answer['goal_contract']['body']['intended_outcome'] = pending_answer['task']
    pending_answer['pending_questions'] = waiting['pending_questions']
    pending_answer['discovery_summary'] = 'The saved planning step needs an explicit recovery answer before another provider request.'
    pending_answer['answers'] = {'earlier-question': {'text': 'Inspect the saved attempt first.', 'at': '2026-09-22T12:32:00Z'}}
    pending_answer['_fixture_interventions'] = {
        'mode': 'supported',
        'entries': [{
            'id': 'req_autocode_20260922_141233_7f4a91',
            'kind': 'feedback',
            'status': 'uncertain',
            'error': 'Provider delivery could not be confirmed because the connection closed before a final receipt was returned. The request may still have reached the provider. Refresh status and reconcile the request ID before retrying to avoid submitting the same answer twice.',
        }],
    }

    unavailable_model = base_state(workspace, 'Replace an unavailable saved planning model before continuing',
                                   status='PAUSED_PROVIDER_UNCERTAIN')
    unavailable_model.pop('active_stage')
    unavailable_model['settings']['engine'] = 'opencode'
    unavailable_model['settings']['roles']['astra']['model'] = 'openai/retired-model'
    unavailable_model['_fixture_monitor']['live'] = {'state': 'unknown', 'label': 'Live worker status is unverified'}

    states = {
        'running': running,
        'activity': activity,
        'waiting': waiting,
        'plan': plan,
        'paused': paused,
        'recovery': recovery,
        'completed': completed,
        'completed-freshness': completed_freshness,
        'completed-missing': completed_missing,
        'completed-invalid': completed_invalid,
        'pending-answer': pending_answer,
        'unavailable-model': unavailable_model,
    }
    # Isolated copies make the three full browser flows independently
    # repeatable: a successful answer, approval, start, or recovery in one
    # viewport cannot become hidden state for another viewport's evidence.
    for viewport in ('desktop', 'tablet', 'mobile'):
        states['flow-answer-' + viewport] = copy.deepcopy(pending_answer)
        states['flow-plan-' + viewport] = copy.deepcopy(plan)
        states['flow-recovery-' + viewport] = copy.deepcopy(paused)
        states['flow-model-' + viewport] = copy.deepcopy(unavailable_model)
        uncertain_model = copy.deepcopy(unavailable_model)
        uncertain_model['_fixture_model_confirm_mode'] = 'uncertain'
        states['flow-model-uncertain-' + viewport] = uncertain_model
        failed_model = copy.deepcopy(unavailable_model)
        failed_model['_fixture_model_confirm_mode'] = 'failed'
        states['flow-model-failed-' + viewport] = failed_model
        states['flow-archive-' + viewport] = copy.deepcopy(paused)
        states['flow-remove-' + viewport] = copy.deepcopy(paused)
    # This isolated task makes an uncertain supported-model confirmation
    # reconcile through an authoritative later status read without replaying
    # the original mutation.
    states['flow-model-uncertain'] = copy.deepcopy(unavailable_model)
    states['flow-model-uncertain']['_fixture_model_confirm_mode'] = 'uncertain'
    return states


def fixture_base_directory():
    # Keep temporary fixture state inside the checkout/evidence root. Tests can
    # override this location for a run-specific evidence directory.
    configured = os.environ.get('AUTOCODE_FIXTURE_ROOT')
    base = Path(configured) if configured else Path(__file__).resolve().parents[3] / '.autocode' / 'evidence' / 'fixture-tmp'
    base.mkdir(parents=True, exist_ok=True)
    return base


def main():
    with tempfile.TemporaryDirectory(prefix='autocode-unified-browser-', dir=fixture_base_directory()) as temporary:
        root = Path(temporary)
        os.environ['AUTOCODE_HOME'] = str(root / 'home')
        workspace = root / 'Example project'
        (workspace / '.git').mkdir(parents=True)
        runs_root = workspace / '.autocode' / 'runs'
        states = scenario_states(workspace)
        for name, state in states.items():
            run = runs_root / name
            run.mkdir(parents=True)
            (run / 'state.json').write_text(json.dumps(state), encoding='utf8')
        (workspace / 'coverage.json').write_text(json.dumps({'groups': {'shell': {'done': 21, 'total': 21}}}), encoding='utf8')
        (workspace / '.autocode' / 'dashboard.json').write_text(json.dumps({'version': 1, 'metrics': [{
            'id': 'matrix', 'label': 'Browser matrix coverage', 'runs': ['running'], 'source': 'coverage.json',
            'description': 'Fixture-only matrix coverage; it is not task completion.',
            'groups_pointer': '/groups', 'completed_field': 'done', 'total_field': 'total'}]}), encoding='utf8')
        catalogue = root / 'fixture_models.py'
        catalogue.write_text("print('openai/gpt-6-astra')\nprint('openai/gpt-5.6-terra')\nprint('openai/gpt-5.6-sol')\n", encoding='utf8')

        class FixtureConsole(Console):
            _model_action_count = 0
            _lifecycle_action_count = 0

            def _json_command(self, *args, **kwargs):
                operation = args[0][1]
                return {'registry_version': 1, 'operation': operation, 'registry_path': str(root / 'registry.json'), 'runs': [], 'workspaces': []}, None

            def _intervention_view(self, workspace, run):
                state = json.loads((run / 'state.json').read_text(encoding='utf8'))
                configured = state.get('_fixture_interventions', {})
                return {'mode': configured.get('mode', 'legacy'), 'capable': False, 'entries': configured.get('entries', []),
                        'attempt_id': state.get('_fixture_attempt'), 'blocked_conditions': [], 'pause_intent': None}

            def _fixture_monitor(self, run):
                state = json.loads((run / 'state.json').read_text(encoding='utf8'))
                return dict(state['_fixture_monitor'])

            def _state(self, run):
                return json.loads((Path(run) / 'state.json').read_text(encoding='utf8'))

            def _save_state(self, run, state):
                (Path(run) / 'state.json').write_text(json.dumps(state), encoding='utf8')

            def _lifecycle_receipt(self, run, label, request_id=None):
                self._lifecycle_action_count += 1
                action = {
                    'id': 'fixture-lifecycle-' + str(self._lifecycle_action_count),
                    'label': label,
                    'command': ['fixture-only', label],
                    'status': 'finished',
                    # ConversationMixin determines receipt success from the
                    # runner-compatible exit status, not the display status.
                    # Keep the disposable lifecycle receipt shaped like a
                    # completed Console action so successful-answer flows do
                    # not get incorrectly rewritten as delivery failures.
                    'exit_status': 0,
                    'queued_at': time.time(),
                    'started_at': time.time(),
                    'finished_at': time.time(),
                    'stdout': 'Fixture lifecycle receipt.',
                    'stderr': '',
                }
                if request_id:
                    action['request_id'] = request_id
                self.actions.setdefault(str(run), []).append(action)
                return action

            def dashboard_snapshot(self):
                data = super().dashboard_snapshot()
                # Extra lifecycle and completion-record specimens are addressable
                # directly but intentionally excluded from the mixed Workspace
                # inventory used by the 21-frame M1/M2 matrix. They are not
                # production or discovered tasks.
                hidden_specs = {'unavailable-model', 'completed-freshness',
                                'completed-missing', 'completed-invalid'}
                data['runs'] = [row for row in data['runs']
                                if Path(row['run']).name not in hidden_specs
                                and not Path(row['run']).name.startswith('flow-')]
                for row in data['runs']:
                    row['monitor'] = self._fixture_monitor(Path(row['run']))
                return data

            def task_view(self, workspace, run):
                if (root / 'offline').exists():
                    raise AttributeError('Simulated detail failure')
                state = self._state(run)
                remaining = state.get('_fixture_pause_reconcile_reads')
                if isinstance(remaining, int):
                    if remaining > 0:
                        state['_fixture_pause_reconcile_reads'] = remaining - 1
                    else:
                        for entry in state.get('_fixture_interventions', {}).get('entries', []):
                            if entry.get('kind') == 'pause' and entry.get('status') == 'uncertain':
                                entry['status'] = 'reconciled'
                                entry['receipt'] = 'fixture-pause-reconciled'
                                entry['error'] = None
                        state.pop('_fixture_pause_reconcile_reads', None)
                    self._save_state(run, state)
                model_remaining = state.get('_fixture_model_reconcile_reads')
                if isinstance(model_remaining, int):
                    if model_remaining > 0:
                        state['_fixture_model_reconcile_reads'] = model_remaining - 1
                    else:
                        role = state.pop('_fixture_model_reconcile_role', None)
                        proposed = state.pop('_fixture_model_reconcile_proposed', None)
                        if role and proposed:
                            state['settings']['roles'][role]['model'] = proposed
                        state.pop('_fixture_model_reconcile_reads', None)
                    self._save_state(run, state)
                view = super().task_view(workspace, run)
                view['monitor'] = self._fixture_monitor(run)
                return view

            def mutate(self, data):
                """Authoritative, disposable transitions used only by browser flows.

                The production handler and payload shape remain intact: the
                fixture only records an explicit action after validating its
                current saved state, then writes the resulting test-only state
                and receipt for the next dashboard refresh.
                """
                action = data.get('action')
                if action not in {'approve_goal', 'continue', 'recover_stage', 'pause'}:
                    return super().mutate(data)
                workspace = self.workspace_for(data.get('workspace', ''))
                run = self.run_for(workspace, data.get('run', ''))
                if not run:
                    raise ValueError('Run does not belong to the disposable fixture workspace')
                state = self._state(run)
                if action == 'approve_goal':
                    token = data.get('token')
                    if token != state.get('displayed_goal') or data.get('confirmation') != token:
                        raise ValueError('Displayed fixture plan revision changed before approval')
                    state['goal_contract']['approval_status'] = 'approved'
                    state['goal_contract']['approval_event'] = {'at': FIXTURE_NOW, 'actor': 'Fixture developer'}
                    state.setdefault('user_events', []).append({'kind': 'goal_approval', 'token': token, 'at': FIXTURE_NOW, 'actor': 'Fixture developer'})
                    state['status'] = 'PAUSED_INTERVENTION'
                    state.pop('active_stage', None)
                    state['_fixture_monitor']['next_stage'] = 'terra'
                    state['_fixture_monitor']['active_role'] = 'astra'
                    self._save_state(run, state)
                    return self._lifecycle_receipt(run, 'Approve goal', data.get('request_id'))
                if action == 'continue':
                    if state.get('goal_contract', {}).get('approval_status') != 'approved':
                        raise ValueError('Fixture plan approval must be confirmed before Start building')
                    resuming = any(stage.get('stage') == 'terra' for stage in state.get('stages', []))
                    state['status'] = 'RUNNING'
                    state['active_stage'] = {'stage': 'terra', 'role': 'terra', 'started_at': FIXTURE_NOW}
                    state['_fixture_monitor']['next_stage'] = 'sol'
                    state['_fixture_monitor']['active_role'] = 'terra'
                    state['_fixture_monitor']['live'] = {'state': 'alive', 'label': 'Worker verified alive', 'pid': 2422}
                    self._save_state(run, state)
                    return self._lifecycle_receipt(run, 'Resume task' if resuming else 'Start building', data.get('request_id'))
                if action == 'pause':
                    request_id = data.get('request_id') or 'fixture-pause-unknown'
                    state['_fixture_interventions'] = {
                        'mode': 'supported',
                        'entries': [{
                            'id': request_id,
                            'kind': 'pause',
                            'status': 'uncertain',
                            'error': 'Fixture pause delivery could not be confirmed. Refresh status and reconcile this request ID before retrying.',
                        }],
                    }
                    # The action's own refresh sees the uncertain result. A
                    # later explicit refresh provides the authoritative receipt.
                    state['_fixture_pause_reconcile_reads'] = 1
                    self._save_state(run, state)
                    return {'id': request_id, 'kind': 'pause', 'status': 'uncertain',
                            'error': 'Fixture pause delivery could not be confirmed. Refresh status and reconcile this request ID before retrying.'}
                attempt = data.get('attempt_id')
                if state.get('status') not in {'PAUSED_PROVIDER_UNCERTAIN', 'PAUSED_UNCERTAIN_STAGE'} or attempt != state.get('_fixture_attempt'):
                    raise ValueError('Inspect the matching interrupted fixture attempt before recovery')
                state['status'] = 'PAUSED_INTERVENTION'
                state.pop('active_stage', None)
                # The recovered attempt has a durable implementation-stage
                # checkpoint. This is what makes the subsequent control a
                # separate Resume task action rather than a first Start
                # building action.
                state.setdefault('stages', []).append({
                    'stage': 'terra', 'role': 'terra', 'iteration': 31,
                    'finished_at': FIXTURE_NOW, 'interrupted': True,
                })
                state['_fixture_monitor']['next_stage'] = 'terra'
                state['_fixture_monitor']['live'] = {'state': 'none', 'label': 'Saved work recovered; no live worker started'}
                self._save_state(run, state)
                return self._lifecycle_receipt(run, 'Recover saved work', data.get('request_id'))

            def enqueue(self, *args, **kwargs):
                workspace, run, label, extra = args[:4]
                if run and label == 'Send answer':
                    # Keep the ordinary pending-answer specimen as a failed
                    # delivery so the existing draft-retention regression
                    # remains meaningful. The dedicated flow copies exercise
                    # the separately required successful receipt path.
                    if not Path(run).name.startswith('flow-answer-'):
                        raise ValueError('Fixture delivery could not be confirmed; preserve the draft before retrying.')
                    answer_index = extra.index('--answer') if '--answer' in extra else -1
                    if answer_index < 0 or answer_index + 1 >= len(extra):
                        raise ValueError('Fixture answer is missing its explicit question value')
                    question_id = extra[answer_index + 1].split('=', 1)[0]
                    state = self._state(run)
                    pending = state.get('pending_questions', [])
                    if question_id not in {str(question.get('id')) for question in pending}:
                        raise ValueError('Fixture question is no longer pending')
                    state['pending_questions'] = [question for question in pending if str(question.get('id')) != question_id]
                    self._save_state(run, state)
                    action = self._lifecycle_receipt(run, 'Send answer', None)
                    callback = kwargs.get('on_complete')
                    if callback:
                        callback(action)
                    return action
                if not (run and label.startswith('Confirm model replacement for ')):
                    raise ValueError('No execution in this fixture')
                role = label.rsplit(' ', 1)[-1]
                flag = '--' + role + '-model'
                if flag not in extra:
                    raise ValueError('Fixture replacement is missing its explicit role/model flag')
                model = extra[extra.index(flag) + 1]
                state_path = Path(run) / 'state.json'
                state = json.loads(state_path.read_text(encoding='utf8'))
                if state.get('_fixture_model_confirm_mode') == 'uncertain':
                    state['_fixture_model_reconcile_reads'] = 1
                    state['_fixture_model_reconcile_role'] = role
                    state['_fixture_model_reconcile_proposed'] = model
                    state_path.write_text(json.dumps(state), encoding='utf8')
                    self._model_action_count += 1
                    action = {
                        'id': 'fixture-model-uncertain-' + str(self._model_action_count),
                        'label': label,
                        'command': [sys.executable, str(self.runner), '--workspace', str(workspace), '--run-dir', str(run), *extra],
                        'status': 'launch_failed',
                        'queued_at': time.time(),
                        'started_at': time.time(),
                        'finished_at': time.time(),
                        'stdout': '',
                        'stderr': 'Fixture replacement receipt is uncertain; reconcile status before retrying.',
                    }
                    self.actions.setdefault(str(run), []).append(action)
                    return action
                if state.get('_fixture_model_confirm_mode') == 'failed':
                    self._model_action_count += 1
                    action = {
                        'id': 'fixture-model-failed-' + str(self._model_action_count),
                        'label': label,
                        'command': [sys.executable, str(self.runner), '--workspace', str(workspace), '--run-dir', str(run), *extra],
                        'status': 'failed',
                        'queued_at': time.time(),
                        'started_at': time.time(),
                        'finished_at': time.time(),
                        'stdout': '',
                        'stderr': 'Fixture replacement was rejected. The saved model remains unchanged.',
                    }
                    self.actions.setdefault(str(run), []).append(action)
                    return action
                state['settings']['roles'][role]['model'] = model
                state_path.write_text(json.dumps(state), encoding='utf8')
                self._model_action_count += 1
                action = {
                    'id': 'fixture-model-replacement-' + str(self._model_action_count),
                    'label': label,
                    'command': [sys.executable, str(self.runner), '--workspace', str(workspace), '--run-dir', str(run), *extra],
                    'status': 'finished',
                    'queued_at': time.time(),
                    'started_at': time.time(),
                    'finished_at': time.time(),
                    'stdout': 'Fixture model replacement receipt.',
                    'stderr': '',
                }
                self.actions.setdefault(str(run), []).append(action)
                return action

        console = FixtureConsole([workspace], root / 'no-runner', lambda: False,
                                 conversation_root=root / 'conversations', project_store_root=root / 'dashboard',
                                 catalogue_command=(sys.executable, str(catalogue)))
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        server.console = console
        server.hosts = {f'127.0.0.1:{server.server_port}', f'localhost:{server.server_port}'}
        base_url = f'http://127.0.0.1:{server.server_port}/'
        scenario_urls = {'workspace': base_url + '#tasks'}
        for name in states:
            scenario_urls[name] = base_url + '#' + urlencode({'task': str(workspace), 'run': str(runs_root / name)})
        print('FIXTURE=' + json.dumps({'base_url': base_url, 'scenarios': scenario_urls}, sort_keys=True), flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
            console.pool.shutdown(wait=True)
            if console._conversation_store is not None:
                console._conversation_store.close()


if __name__ == '__main__':
    main()
