"""M3 acceptance cases: lifecycle, drawers, and accessibility (AC18-AC20).

AC18 drives the Node VM harness (workspace_lifecycle_harness.js, the same
lightweight pattern as test_workspace_panes_ui.py): each lifecycle state opens
a saved run record through the app's real fetch paths and asserts the Work
pane renders exactly that state's saved content, with every approval and
answer control staying in the chat transcript.

AC19 and AC20 are browser-level: workspace_lifecycle_browser.js boots the
disposable unified fixture and drives the shipped page through the real
agent-browser bridge at 390 px (drawers, 44 px targets, draft and transcript
retention) and at desktop plus mobile widths (keyboard order, visible focus,
live-region announcements, contrast).
"""
import shutil
import subprocess
import unittest
from pathlib import Path

TESTS = Path(__file__).resolve().parent
HARNESS = TESTS / 'workspace_lifecycle_harness.js'
BROWSER = TESTS / 'workspace_lifecycle_browser.js'


def run_node(script, case, timeout=420):
    return subprocess.run(['node', str(script), case], capture_output=True, text=True,
                          timeout=timeout, cwd=str(TESTS))


class WorkspaceLifecycleUITests(unittest.TestCase):
    def assert_case(self, script, case, browser=False):
        if browser and shutil.which('agent-browser') is None:
            self.skipTest('agent-browser bridge is not on PATH; install it to run '
                          f'the real-browser {case!r} case (see tools/dashboard/tests/)')
        result = run_node(script, case)
        self.assertEqual(0, result.returncode,
                         f'case {case} failed:\n{result.stdout}\n{result.stderr}')

    def test_ac18_lifecycle_states_render_saved_content(self):
        self.assert_case(HARNESS, 'ac18')

    def test_ac19_drawers_retain_chat_draft_and_44px_targets(self):
        self.assert_case(BROWSER, 'ac19', browser=True)

    def test_ac20_keyboard_focus_announcements_contrast(self):
        self.assert_case(BROWSER, 'ac20', browser=True)


if __name__ == '__main__':
    unittest.main()
