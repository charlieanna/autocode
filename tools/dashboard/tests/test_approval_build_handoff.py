"""The saved approval token remains usable for a separate revision-bound Build.

Two layers prove the handoff. The adapter tests keep every refusal the mutate
routes must raise before anything is queued. The real-runner tests drive the
actual runner CLI offline (the config-tool fixture provider; no model is ever
called) through the dashboard's real action routes, so the saved goal_approval
and build_start user events, the nonempty --expected-goal-token and every
refusal are exercised against the same sealed-contract admission the runner
enforces.
"""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console, LegacyConsole
from dashboard_backend import RegistryInterventionMixin

TOOLS = Path(__file__).resolve().parents[2]
RUNNER = TOOLS / 'autocode.py'


TOKEN = 'r7:abc'


def approved_view(*, token=TOKEN, displayed='', pending=False, event=True):
    return {
        'goal_token': displayed,
        'goal': {'revision': 7, 'hash': token.partition(':')[2],
                 'approval_status': 'approved',
                 'approval_event': {'token': token} if event else None},
        'conversation': {'plan_gate': {'pending_product_change': pending}},
        'questions': [],
    }


def tokenless_view(displayed=''):
    """A saved approval event that does not itself carry the sealed token.

    The authoritative runner records the token on every approval
    (autocode_goal_lifecycle.approve) and its admission check requires it, so
    the dashboard must refuse to queue a build start from such a record."""
    view = approved_view()
    view['goal_token'] = displayed
    view['goal'] = {**view['goal'],
                    'approval_event': {'kind': 'goal_approval', 'actor': 'user_cli'}}
    return view


class BackendHarness(RegistryInterventionMixin):
    def __init__(self, view):
        self.saved_view = view
        self.actions = []

    def workspace_for(self, raw):
        return Path('/fixture')

    def run_for(self, workspace, raw):
        return Path('/fixture/run')

    def view(self, workspace, run):
        return self.saved_view

    def _intervention_view(self, workspace, run):
        return {'mode': 'durable', 'capable': True}

    def enqueue(self, workspace, run, label, args):
        self.actions.append(list(args))
        return {'id': 'build-1', 'status': 'queued'}


class LegacyHarness:
    workspace_for = BackendHarness.workspace_for
    run_for = BackendHarness.run_for
    view = BackendHarness.view
    enqueue = BackendHarness.enqueue

    def __init__(self, view):
        self.saved_view = view
        self.actions = []


def request(token=TOKEN):
    return {'action': 'continue', 'workspace': '/fixture', 'run': '/fixture/run',
            'token': token, 'confirmation': token, 'expected_goal_token': token}


class ApprovalBuildHandoffTests(unittest.TestCase):
    def test_registry_accepts_saved_approval_after_display_token_clears(self):
        adapter = BackendHarness(approved_view())
        adapter.mutate(request())
        self.assertEqual(['--no-chat', '--resume-paused', '--expected-goal-token', TOKEN], adapter.actions[0])

    def test_legacy_adapter_accepts_same_saved_approval(self):
        adapter = LegacyHarness(approved_view())
        LegacyConsole.mutate(adapter, request())
        self.assertEqual(['--expected-goal-token', TOKEN], adapter.actions[0])

    def test_both_adapters_refuse_stale_or_unconfirmed_start(self):
        for adapter_type, invoke in ((BackendHarness, lambda adapter, data: adapter.mutate(data)),
                                     (LegacyHarness, lambda adapter, data: LegacyConsole.mutate(adapter, data))):
            for view, data in ((approved_view(), request('r6:old')),
                               (approved_view(token='r8:new'), request()),
                               (approved_view(event=False), request()),
                               (approved_view(pending=True), request()),
                               (approved_view(displayed='r8:new'), request())):
                with self.subTest(adapter=adapter_type.__name__, view=view, data=data):
                    adapter = adapter_type(view)
                    with self.assertRaisesRegex(ValueError, 'approved plan changed'):
                        invoke(adapter, data)
                    self.assertEqual([], adapter.actions)

    def test_both_adapters_refuse_tokenless_saved_approval(self):
        for adapter_type, invoke in ((BackendHarness, lambda adapter, data: adapter.mutate(data)),
                                     (LegacyHarness, lambda adapter, data: LegacyConsole.mutate(adapter, data))):
            for displayed in ('', TOKEN):
                with self.subTest(adapter=adapter_type.__name__, displayed=displayed):
                    adapter = adapter_type(tokenless_view(displayed))
                    with self.assertRaisesRegex(ValueError, 'approved plan changed'):
                        invoke(adapter, request())
                    self.assertEqual([], adapter.actions)


PROVIDER_TOML = """\
    name = "handoff"
    command = ["handoff-tool", "--report", "{report}", "--sandbox", "{sandbox}", "--model", "{model}", "--role", "{role}"]
    prompt = "stdin"
    models = ["fixture-reviewer", "fixture-builder", "fixture-validator", "fixture-completion", "fixture-planner", "fixture-plan-reviewer"]
    version_command = ["handoff-tool", "--version"]

    [roles]
    astra = { model = "fixture-reviewer", effort = "high" }
    terra = { model = "fixture-builder", effort = "medium" }
    sol = { model = "fixture-validator", effort = "high" }
    completion = { model = "fixture-completion", effort = "medium" }
    glm = { model = "fixture-planner", effort = "medium" }
    plan_reviewer = { model = "fixture-plan-reviewer", effort = "high" }
    """


class RealRunnerHandoffFixture(unittest.TestCase):
    """A git project, the offline config-tool provider, the real runner CLI and
    the dashboard's real action routes; no model is ever called."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(dir=Path.cwd())
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.make_project('project')
        bin_dir = self.root / 'bin'
        bin_dir.mkdir()
        shutil.copy2(TOOLS / 'fake_command_tool.py', bin_dir / 'handoff-tool')
        shutil.copy2(TOOLS / 'goal_fixtures.py', bin_dir / 'goal_fixtures.py')
        (bin_dir / 'handoff-tool').chmod(0o755)
        config_home = self.root / 'config'
        (config_home / 'autocode' / 'providers').mkdir(parents=True)
        (config_home / 'autocode' / 'providers' / 'handoff.toml').write_text(textwrap.dedent(PROVIDER_TOML))
        # The dashboard spawns the runner and its provider commands through the
        # inherited environment, so the offline provider is installed there.
        environment = patch.dict(os.environ, {
            'PATH': str(bin_dir) + os.pathsep + os.environ.get('PATH', ''),
            'PYTHONDONTWRITEBYTECODE': '1', 'XDG_CONFIG_HOME': str(config_home),
            'AUTOCODE_HOME': str(self.root / 'registry-home')})
        environment.start()
        self.addCleanup(environment.stop)
        self.console = self.make_console()

    def make_project(self, name):
        project = self.root / name
        project.mkdir()
        subprocess.run(['git', 'init', '-q', str(project)], check=True)
        subprocess.run(['git', '-C', str(project), '-c', 'user.name=Fixture', '-c', 'user.email=f@example.test',
                        'commit', '--allow-empty', '-qm', 'fixture'], check=True)
        return project

    def make_console(self):
        console = Console([self.project], RUNNER, lambda: None,
                          conversation_root=self.root / 'dashboard/conversations',
                          conversation_provider=lambda messages, model, workdir: 'Planner: ' + messages[-1]['text'])
        console.catalogue.fetch = lambda **kwargs: {
            'usable': True, 'models': ['fixture-builder', 'fixture-planner', 'fixture-validator']}

        def close():
            console.pool.shutdown(wait=True)
            if console._conversation_store is not None:
                console._conversation_store.close()
        self.addCleanup(close)
        return console

    def read_state(self, run):
        return json.loads((run / 'state.json').read_text())

    def wait_action(self, action, timeout=420):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if action.get('finished_at'):
                return action
            time.sleep(.05)
        self.fail('Timed out waiting for dashboard action ' + str(action.get('id')))

    def launch_awaiting_approval(self):
        """Drive the real CLI's chat flow until only the plan approval remains."""
        result = subprocess.run([sys.executable, str(RUNNER), '--provider', 'handoff',
                                 '--workspace', str(self.project), '--in-place', '--chat',
                                 'Build a greeting tool'],
                                cwd=self.root, input='CLI\n', capture_output=True, text=True, timeout=420)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn('brief remains available for approval', result.stdout)
        runs = [entry for entry in (self.project / '.autocode' / 'runs').iterdir()
                if (entry / 'state.json').is_file()]
        self.assertEqual(1, len(runs), 'expected exactly one chat-created run')
        run = runs[0]
        self.assertEqual('AWAITING_GOAL_APPROVAL', self.read_state(run)['status'])
        return run

    def public_request(self, run):
        view = self.console.view(self.project, run)
        public = view['human_escalation']
        self.assertEqual('goal_approval', public['scope'])
        return view, public['request_id'], public['request_token']


class RealRunnerApprovalBuildHandoffTests(RealRunnerHandoffFixture):
    def test_real_runner_approve_and_build_handoff_records_separate_events(self):
        run = self.launch_awaiting_approval()
        view, request_id, request_token = self.public_request(run)
        token = view['goal_token']
        self.assertRegex(token, r'^r\d+:')
        base = {'workspace': str(self.project), 'run': str(run)}

        # A failed approval receipt leaves the plan unapproved: the real CLI
        # refuses a wrong token, and no build start may be queued afterwards.
        failed = subprocess.run([sys.executable, str(RUNNER), '--provider', 'handoff',
                                 '--workspace', str(self.project), '--run-dir', str(run),
                                 '--approve-goal', 'r1:not-the-approved-plan'],
                                cwd=self.root, capture_output=True, text=True, timeout=180)
        self.assertNotEqual(0, failed.returncode, failed.stdout + failed.stderr)
        with self.assertRaisesRegex(ValueError, 'approved plan changed'):
            self.console.mutate({**base, 'action': 'continue', 'token': token,
                                 'confirmation': token, 'expected_goal_token': token})
        self.assertEqual([], self.console.action_log(self.project, run))

        # Combined Approve & build, approval half: the chat control records the
        # saved goal_approval event through the real action route.
        approved = self.wait_action(self.console.mutate({
            **base, 'action': 'approve_goal', 'token': token, 'confirmation': token,
            'resolver_request': request_id, 'resolver_token': request_token}))
        self.assertEqual(0, approved['exit_status'], approved['stdout'] + approved['stderr'])
        state = self.read_state(run)
        self.assertEqual('approved', state['goal_contract']['approval_status'])
        self.assertEqual(token, state['goal_contract']['approval_event']['token'])
        self.assertEqual([token], [row['token'] for row in state['user_events']
                                   if row.get('kind') == 'goal_approval'])
        self.assertEqual([], [row for row in state['user_events'] if row.get('kind') == 'build_start'])

        # A saved approval whose event does not itself carry the exact sealed
        # token is refused before any runner launch: the dashboard must never
        # queue a start the authoritative runner would refuse.
        tokenless = self.project / '.autocode' / 'runs' / 'tokenless-approval'
        shutil.copytree(run, tokenless)
        saved = self.read_state(tokenless)
        saved['goal_contract']['approval_event'] = {
            key: value for key, value in saved['goal_contract']['approval_event'].items() if key != 'token'}
        (tokenless / 'state.json').write_text(json.dumps(saved))
        with self.assertRaisesRegex(ValueError, 'approved plan changed'):
            self.console.mutate({**base, 'run': str(tokenless), 'action': 'continue', 'token': token,
                                 'confirmation': token, 'expected_goal_token': token})
        self.assertEqual([], self.console.action_log(self.project, tokenless))
        self.assertNotIn('build_start', [row.get('kind') for row in self.read_state(tokenless)['user_events']])

        # A stale revision/token is refused with nothing queued.
        before_stale = len(self.console.action_log(self.project, run))
        with self.assertRaisesRegex(ValueError, 'approved plan changed'):
            self.console.mutate({**base, 'action': 'continue', 'token': 'r1:stale',
                                 'confirmation': 'r1:stale', 'expected_goal_token': 'r1:stale'})
        self.assertEqual(before_stale, len(self.console.action_log(self.project, run)))

        # Build half / standalone Start building: a fresh dashboard holding only
        # the saved approved record queues the revision-bound start.
        self.console = self.make_console()
        started = self.wait_action(self.console.mutate({
            **base, 'action': 'continue', 'token': token, 'confirmation': token,
            'expected_goal_token': token}))
        command = started['command']
        expected = command[command.index('--expected-goal-token') + 1]
        self.assertEqual(token, expected)
        self.assertTrue(expected.strip(), 'the queued start carries a nonempty expected goal token')
        self.assertEqual(0, started['exit_status'], started['stdout'] + started['stderr'])
        state = self.read_state(run)
        self.assertEqual([('goal_approval', token), ('build_start', token)],
                         [(row.get('kind'), row.get('token')) for row in state['user_events']
                          if row.get('kind') in ('goal_approval', 'build_start')])
        self.assertEqual('TASK_COMPLETE', state['status'])
        self.assertTrue(any(record.get('stage') == 'terra' for record in state['stages']))

        # Duplicate submission of the identical start records no second event.
        self.wait_action(self.console.mutate({
            **base, 'action': 'continue', 'token': token, 'confirmation': token,
            'expected_goal_token': token}))
        kinds = [row.get('kind') for row in self.read_state(run)['user_events']]
        self.assertEqual(1, kinds.count('goal_approval'))
        self.assertEqual(1, kinds.count('build_start'))


if __name__ == '__main__':
    unittest.main()
