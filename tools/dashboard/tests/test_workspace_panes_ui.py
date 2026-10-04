"""M2 acceptance cases: the chat-and-pane workspace (AC10-AC17, AC24, AC25 guard).

Frontend behavior is driven through the Node VM harness
(workspace_panes_harness.js, the same lightweight pattern as
test_sidebar_workspace_ui.py): each case opens a saved run record through the
app's real fetch paths and asserts what the shipped page renders. AC16, AC17
and AC25 are guards: the pause labels, the revision/token binding with stale
refusal, and the in-chat permission question card predate the pane relayout
and must keep working through it, so their cases assert only pre-existing
behavior through surfaces both layouts render. AC14 and AC24 reload the page
(re-running the shipped source against the saved localStorage state and the
remembered selection) on both transcript surfaces, #draft-scroll and
#interview. AC15's placement is markup the page ships, so its ancestry is
proved against dashboard.html here while the harness case proves the
relocated controls stay actionable.
"""
import html.parser
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / 'dashboard'))

import dashboard_monitor

HARNESS = Path(__file__).resolve().parent / 'workspace_panes_harness.js'


def harness_case(name, monitor=None):
    env = dict(os.environ)
    if monitor is not None:
        env['PANES_MONITOR_JSON'] = json.dumps(monitor)
    elif 'PANES_MONITOR_JSON' in env:
        del env['PANES_MONITOR_JSON']
    return subprocess.run(['node', str(HARNESS), name], capture_output=True, text=True,
                          timeout=180, env=env)


class WorkspacePanesUITests(unittest.TestCase):
    def assert_harness(self, name, monitor=None):
        result = harness_case(name, monitor)
        self.assertEqual(0, result.returncode,
                         'case %s failed:\n%s\n%s' % (name, result.stdout, result.stderr))

    def test_ac10_work_default_pane_reads_saved_records(self):
        self.assert_harness('ac10')

    def test_ac11_pane_switches_keep_chat_and_draft_visible(self):
        self.assert_harness('ac11')

    def test_ac12_work_pane_counts_match_saved_records(self):
        self.assert_harness('ac12')

    def test_ac13_empty_conversation_truthful_states_no_cross_links(self):
        self.assert_harness('ac13')

    def test_ac14_draft_scroll_selection_survive_refresh(self):
        self.assert_harness('ac14')

    def test_composer_run_controls_beside_composer_with_stop(self):
        # Non-credited placement proof (the credited AC15 case lives in
        # tests/test_autocode_stop.py with the real stop-submission adapter).
        ancestors = approval_host_ancestors()
        for control in ('pause-run', 'stop-run', 'continue-run'):
            chain = ancestors.get(control) or []
            self.assertTrue(chain, 'the shipped page renders the %s control' % control)
            self.assertIn('composer-run-controls', chain,
                          'the %s control must sit in the beside-composer run controls row; '
                          'enclosing ids: %s' % (control, chain))
            self.assertIn('live-controls', chain,
                          'the %s control must sit in the composer section, beside the chat transcript '
                          'composer inside the conversation view; enclosing ids: %s' % (control, chain))
        for control in ('task-model-settings', 'task-reasoning-form'):
            chain = ancestors.get(control) or []
            self.assertTrue(chain, 'the shipped page renders the %s model-route host' % control)
            self.assertIn('composer-models', chain,
                          'the %s model-route host must sit in the beside-composer models disclosure; '
                          'enclosing ids: %s' % (control, chain))
            self.assertIn('live-controls', chain,
                          'the %s model-route host must sit beside the chat composer, not in the task '
                          'settings dropdown; enclosing ids: %s' % (control, chain))
        self.assert_harness('composer_run_controls')

    def test_ac16_pause_labeled_after_current_step(self):
        self.assert_harness('ac16')

    def test_ac17_chat_actions_revision_token_binding_stale_refusal(self):
        self.assert_harness('ac17')

    def test_ac24_active_project_scope_survives_refresh_and_navigation(self):
        self.assert_harness('ac24')

    def test_ac10_configured_routes_not_listed_as_executed_attributions(self):
        self.assert_harness('ac10_configured')

    def test_plan_and_checks_panes_leave_approvals_to_chat(self):
        self.assert_harness('pane_exclusivity')

    def test_ac25_permission_question_rendered_in_chat(self):
        self.assert_harness('ac25')

    def test_ac10_real_monitor_projection_configured_routes_are_not_attributions(self):
        # The real backend projection (dashboard_monitor.snapshot, the read
        # behind /api/run) carries settings.roles even when no stage has ever
        # run, so this case cannot pass by trimming the fixture.
        state = {'settings': {'roles': {
                    'terra': {'model': 'configured-terra-route', 'reasoning_effort': 'high'},
                    'sol': {'model': 'configured-sol-route', 'reasoning_effort': 'low'}}},
                 'stages': [], 'active_stage': {}}
        projection = dashboard_monitor.snapshot(state, Path('/nonexistent-projection-run'), detailed=True)
        self.assertEqual('configured-terra-route', projection['roles']['terra']['model'],
                         'the real projection keeps configured routes visible with zero stages')
        self.assertEqual([], projection['stage_history'])
        self.assertIsNone(projection['active_execution'])
        self.assert_harness('ac10_projection_configured', projection)

    def test_ac10_real_monitor_projection_recorded_launches_render(self):
        # A launched stage and an active execution are the only sources of
        # actual role/model facts; the configured future routes differ so a
        # settings-derived line can never satisfy the assertions.
        state = {'settings': {'roles': {
                    'terra': {'model': 'configured-future-terra', 'reasoning_effort': 'max'},
                    'sol': {'model': 'configured-future-sol', 'reasoning_effort': 'low'}}},
                 'stages': [{'stage': 'terra', 'role': 'terra', 'route_role': 'terra',
                             'finished_at': '2026-09-30T11:30:00Z', 'exit_code': 0,
                             'command': ['python', 'tools/autocode.py', '--model',
                                         'gpt-5.6-terra', '--variant', 'medium']}],
                 'active_stage': {'stage': 'sol', 'role': 'sol', 'route_role': 'sol',
                                  'command': ['python', 'tools/autocode.py', '--model',
                                              'gpt-5.6-sol', '--variant', 'high']}}
        projection = dashboard_monitor.snapshot(state, Path('/nonexistent-projection-run'), detailed=True)
        self.assertEqual('gpt-5.6-terra', projection['stage_history'][0]['execution']['model'])
        self.assertEqual('medium', projection['stage_history'][0]['execution']['reasoning_effort'])
        self.assertEqual('gpt-5.6-sol', projection['active_execution']['model'])
        self.assertEqual('high', projection['active_execution']['reasoning_effort'])
        self.assertEqual('configured-future-terra', projection['roles']['terra']['model'])
        self.assert_harness('ac10_projection_executed', projection)

    def test_approval_actions_render_inside_the_chat_transcript(self):
        ancestors = approval_host_ancestors()
        self.assertIn('inline-task-action', ancestors,
                      'the shipped page still renders the inline approval host')
        chain = ancestors['inline-task-action']
        self.assertTrue('interview' in chain or 'conversation' in chain,
                        'the approval host must be a DOM descendant of the continuous '
                        'chat transcript (#interview or #conversation); enclosing ids: %s' % chain)
        self.assertNotIn('live-controls', chain,
                         'the approval host must not live in the composer section outside the transcript')


class _MarkupAncestry(html.parser.HTMLParser):
    """Record the enclosing element ids of every id-addressed element."""

    VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input',
            'link', 'meta', 'param', 'source', 'track', 'wbr'}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self._stack = []  # (tag, id-or-None) pairs of currently open elements
        self.ancestors = {}

    def _record(self, attrs):
        ident = dict(attrs).get('id')
        if ident is not None and ident not in self.ancestors:
            self.ancestors[ident] = [entry[1] for entry in self._stack if entry[1]]

    def handle_starttag(self, tag, attrs):
        self._record(attrs)
        if tag not in self.VOID:
            self._stack.append((tag, dict(attrs).get('id')))

    def handle_startendtag(self, tag, attrs):
        self._record(attrs)

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, -1, -1):
            if self._stack[index][0] == tag:
                del self._stack[index:]
                break


def approval_host_ancestors():
    page = (Path(__file__).resolve().parents[1] / 'dashboard.html').read_text(encoding='utf-8')
    parser = _MarkupAncestry()
    parser.feed(page)
    parser.close()
    return parser.ancestors


if __name__ == '__main__':
    unittest.main()
