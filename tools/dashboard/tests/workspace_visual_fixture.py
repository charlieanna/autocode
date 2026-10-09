"""Recorded sample tasks for Figma comparison, using the real dashboard UI.

All records, patches, receipts and workspaces are disposable. These demonstrate
presentation states, never acceptance of an implementation or a real run.
"""
import copy
import os

import unified_browser_fixture as fixture
from autocode_roles import SCREEN

original_scenarios = fixture.scenario_states


def message(speaker, text, at, *, role='assistant'):
    return {'role': role, 'speaker': speaker, 'status': 'saved' if role == 'user' else 'received',
            'text': text, 'created_at': '2026-09-22T' + at + 'Z'}


def scenarios(workspace):
    states = original_scenarios(workspace)
    milestones = [
        'Conversation and requirements', 'Plan reviewed and approved',
        'Navigation and task overview', 'Context panel and mobile drawer',
        'Full browser journey', 'Independent implementation review',
    ]
    for name, source in [('building', 'running'), ('requirements', 'waiting'),
                         ('reviewed-plan', 'plan'), ('recovery', 'running'),
                         ('ready', 'completed')]:
        state = copy.deepcopy(states[source])
        state['task'] = 'Dashboard redesign'
        state['goal_contract']['body']['intended_outcome'] = 'Build a clearer agent dashboard'
        state['goal_contract']['body']['milestones'] = [
            {'id': 'M' + str(i + 1), 'objective': text}
            for i, text in enumerate(milestones)]
        state['_fixture_accepted'] = ['M1', 'M2', 'M3'] if name in ('building', 'recovery') else []
        if name == 'ready':
            state['_fixture_accepted'] = ['M' + str(i) for i in range(1, 7)]
        state['current_task'].update(id='visual-M4', milestone_id='M4', objective=milestones[3])
        state['iteration'] = 4
        state['stages'] = [{'stage': 'astra_finalize', 'role': 'astra', 'iteration': 2,
                            'finished_at': '2026-09-22T12:18:00Z', 'exit_code': 0}]
        state['goal_contract']['approval_event'] = {
            'at': '2026-09-22T12:20:00Z', 'actor': 'Fixture reviewer',
            'kind': 'goal_approval', 'token': 'r7:fixture-plan-revision-7'}
        monitor = state['_fixture_monitor']
        monitor['findings'] = []
        monitor['objective'] = 'Adding details while keeping the conversation visible.'
        state['settings']['roles'] = {
            role: {'model': 'openai/gpt-6-sol', 'reasoning_effort': 'high'}
            for role in ('astra', 'terra', 'sol')}
        monitor['roles'] = copy.deepcopy(state['settings']['roles'])
        state['progress_messages'] = [
            message('You', 'Approved version 7. Build it.', '12:20:00', role='user'),
            message(SCREEN['builder'], 'The navigation and conversation foundation are complete. I’m adding the context panel while keeping your chat and draft visible.', '12:24:00'),
            message(SCREEN['tester'], 'Persistence checks passed. Mobile layout and the complete browser journey are next.', '12:28:00'),
        ]
        if name in ('building', 'recovery', 'ready'):
            # An actual saved, successful test build record produces the UI
            # checkpoint. It is not independent acceptance or real runner proof.
            state['_fixture_saved_diff'] = 'diff --git a/app.js b/app.js\n--- a/app.js\n+++ b/app.js\n@@ -1 +1,2 @@\n-renderTaskTabs();\n+renderConversation();\n+renderWorkPane();\n'
            state['stages'].append({'stage': 'terra', 'role': 'terra', 'iteration': 3,
                                   'started_at': '2026-09-22T12:21:00Z', 'finished_at': '2026-09-22T12:25:00Z',
                                   'exit_code': 0, 'source_revision': fixture.FIXTURE_SOURCE,
                                   'changed_files': ['app.js'], 'diff_ref': 'saved.diff',
                                   'execution': {'kind': 'model', 'model': 'openai/gpt-6-sol', 'reasoning_effort': 'high'}})
            state['validation'] = {
                'source_revision': fixture.FIXTURE_SOURCE, 'recorded_at': '2026-09-22T12:28:00Z',
                'criterion_results': [{'id': 'C' + str(i), 'status': 'PASS'} for i in range(1, 5)],
                'checks': [{'name': 'Declared browser-fixture verification sample; not real task proof.'}]}
        if name == 'reviewed-plan':
            state['goal_contract'].pop('approval_event', None)
            state['progress_messages'] = [message(SCREEN['plan_reviewer'],
                'Revision 7 is ready for review. Inspect the scope and verification requirements before approving it.', '12:28:00')]
        if name in ('building', 'recovery'):
            state['active_stage'].update(iteration=4, started_at='2026-09-22T12:29:00Z')
            monitor['live'] = {'state': 'alive', 'label': 'Worker verified alive', 'pid': 2430}
            monitor['active_execution'] = {'kind': 'model', 'model': 'openai/gpt-6-sol', 'reasoning_effort': 'high'}
        if name == 'requirements':
            state['goal_contract'].pop('approval_event', None)
            state['progress_messages'] = [
                message('You', 'I want one place to understand and control all my coding work.', '12:20:00', role='user'),
                message(SCREEN['requirements'], 'I found the monitoring flow. Should you also be able to pause, resume and stop tasks from here?', '12:24:00'),
                message(SCREEN['planner'], 'Draft updated: task overview, durable chat, clear progress and responsive layout. Task controls are the remaining decision.', '12:28:00')]
            state['pending_questions'] = [
                {'id': 'visual-question', 'question': 'Should the dashboard include task controls?',
                 'options': [], 'proposed_default': 'Include controls',
                 'why': 'Pause, resume and stop affect the task controls in the reviewed scope.'}]
        if name == 'recovery':
            state['active_stage'].update(stage='astra_resolve', role='astra', started_at='2026-09-22T12:29:00Z')
            monitor.update(active_role='astra', next_stage='astra_resolve',
                           objective='The preview port is already in use. Changes and the approved plan are preserved while Resolver checks the startup configuration.')
            state['progress_messages'] = [
                message(SCREEN['builder'], 'The chat and context panel are implemented. Browser verification found a problem starting the preview server.', '12:28:00'),
                message(SCREEN['resolver'], 'Investigating the existing process and safe restart path. Approval and completed work are preserved.', '12:30:00')]
            state['stages'].append({'stage': 'sol', 'role': 'sol', 'iteration': 3,
                                   'started_at': '2026-09-22T12:26:00Z', 'finished_at': '2026-09-22T12:28:00Z',
                                   'exit_code': 1, 'reason': 'The preview port is already in use.'})
        if name == 'ready':
            state['validation']['criterion_results'] = [{'id': 'C' + str(i), 'status': 'PASS'} for i in range(1, 7)]
            state['validation']['recorded_at'] = '2026-09-22T12:34:00Z'
            state['completed_at'] = '2026-09-22T12:36:00Z'
            state['updated_at'] = '2026-09-22T12:36:00Z'
            state['stages'].extend([
                {'stage': 'sol', 'role': 'sol', 'iteration': 3, 'finished_at': '2026-09-22T12:33:00Z', 'exit_code': 0},
                {'stage': 'astra_review', 'role': 'astra', 'iteration': 3, 'finished_at': '2026-09-22T12:35:00Z', 'exit_code': 0}])
            state['progress_messages'] = [
                message(SCREEN['tester'], 'All 6 required checks passed for this candidate, including persistence, mobile layout and the full browser journey.', '12:33:00'),
                message(SCREEN['completion'], 'The implementation satisfies approved plan revision 7. No unresolved findings remain in this recorded sample.', '12:35:00')]
        monitor['history'] = copy.deepcopy(state['stages'])
        states['flow-visual-' + name] = state
    # A declared recent-interval sample mirrors the mobile reference. The
    # desktop route retains the earlier explicit approval/user transcript.
    mobile = copy.deepcopy(states['flow-visual-building'])
    mobile['progress_messages'] = [
        message(SCREEN['builder'], 'Navigation is complete. I’m adding the context panel.', '12:24:00'),
        message(SCREEN['tester'], 'Persistence checks passed. Mobile and browser checks are next.', '12:28:00')]
    mobile['_fixture_transcript_scope'] = 'Recent saved Builder/Tester interval; earlier approval remains in the desktop route and approval receipt.'
    states['flow-visual-mobile-building'] = mobile
    states['flow-visual-drawer-overflow'] = copy.deepcopy(mobile)
    # Current checked rows require an idle, inspected checkpoint. Keep the
    # original live Builder/Resolver samples: their old report must remain
    # unverified while an active attempt is recorded, even in a visual fixture.
    checked = copy.deepcopy(states['flow-visual-building'])
    checked.pop('active_stage', None)
    checked.update(status='PAUSED_REQUESTED', next_stage='sol',
                   stop_reason='Saved check checkpoint; remaining checks have not run.')
    checked['_fixture_transcript_scope'] = 'Declared idle saved-check checkpoint; not real task proof.'
    checked['_fixture_monitor'].update(
        active_role='sol', next_stage='sol', active_execution=None,
        objective='Four saved checks passed. The keyboard flow and complete browser journey are still unchecked.',
        live={'state': 'none', 'label': 'No active worker recorded; saved check checkpoint'})
    states['flow-visual-checked-checkpoint'] = checked
    return states


if __name__ == '__main__':
    fixture.scenario_states = scenarios
    os.environ['AUTOCODE_PREVIEW_FIXTURE'] = '1'
    os.environ['AUTOCODE_VISUAL_REFERENCE_FIXTURE'] = '1'
    fixture.main()
