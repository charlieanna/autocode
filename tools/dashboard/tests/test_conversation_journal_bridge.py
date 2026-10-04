"""Conversation continuity and approval boundaries through public dashboard APIs."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from agent_console import Console
import autocode_conversation as protocol
import autocode_support as support
from dashboard_conversation_journal import append_feedback, project_conversation


class InlinePool:
    def submit(self, function, *args):
        function(*args)

    def shutdown(self, **kwargs):
        pass


class JournalBridgeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / 'project'
        (self.workspace / '.git').mkdir(parents=True)
        self.calls = []
        def gatherer(messages, model, workdir):
            self.calls.append(('gatherer', messages[-1]['text']))
            return 'I have updated the requirements.'
        def planner(messages, route, workdir):
            self.calls.append(('planner', messages[-1]['text']))
            turn = messages[-1]['logical_turn_id']
            revision = sum(row['role'] == 'user' for row in messages)
            return json.dumps({'contract_version': 1, 'kind': 'autocode.planner-structured-draft',
                              'goal': messages[-1]['text'], 'requirements': [messages[-1]['text']],
                              'milestones': ['Implement'], 'parallelism': [], 'unresolved_questions': [],
                              'source_revision': {'requirements_revision': revision, 'logical_turn_id': turn},
                              'attribution': {'role': 'planner', 'model': route['model'], 'reasoning_effort': 'high'},
                              'freshness': {'state': 'fresh', 'updated_at': protocol.now()}})
        self.console = Console([self.workspace], self.root / 'unused-runner.py', lambda: None,
                               conversation_root=self.root / 'conversations',
                               conversation_provider=gatherer, conversation_planner=planner)
        self.addCleanup(self.console.pool.shutdown, wait=True)
        self.addCleanup(lambda: self.console.conversations.close())
        self.console.catalogue.fetch = lambda **kwargs: {'usable': True, 'models': ['openai/gpt-6-sol']}
        store = self.console.conversations.continuous
        store.pool.shutdown(wait=True)
        store.pool = InlinePool()
        self.actions = []
        def enqueue(workspace, run, label, args, **kwargs):
            self.actions.append((workspace, run, label, args))
            return {'id': 'action-1', 'status': 'queued'}
        self.console.enqueue = enqueue

    def conversation(self):
        doc = self.console.conversation_create({'text': 'Build a greeting CLI', 'request_id': 'first'})
        doc = self.console.conversation_get(doc['id'])
        self.assertEqual('ready', doc['status'])
        self.assertEqual('current', doc['plan_drafts'][-1]['status'])
        return doc

    def test_two_turn_draft_handoff_retains_independent_receipts_without_approval(self):
        doc = self.conversation()
        self.console.conversations.send(doc['id'], 'Preserve the existing interface', 'second')
        doc = self.console.conversation_get(doc['id'])
        self.assertEqual([1, 2], [row['revision'] for row in doc['requirements']['revisions']])
        self.assertEqual(2, doc['plan_drafts'][-1]['requirements_revision'])
        attached = self.console.conversation_attach({'id': doc['id'], 'workspace': str(self.workspace)})
        args = self.actions[-1][-1]
        self.assertIn('--conversation-handoff', args)
        self.assertNotIn('--approve-goal', args)
        source = Path(args[args.index('--conversation-handoff') + 1])
        handoff = protocol.load_handoff(source, workspace=self.workspace)
        self.assertEqual(doc['id'], handoff['conversation_id'])
        self.assertEqual(2, len(handoff['planner_dispatches']))
        self.assertEqual(4, len(handoff['messages']))
        self.assertEqual('openai/gpt-6-sol', handoff['messages'][1]['execution']['model'])
        self.assertEqual('starting', attached['attachment']['status'])

    def test_task_feedback_is_idempotent_and_invalidates_preview_without_approval(self):
        doc = self.conversation()
        run = self.workspace / '.autocode/runs/test'
        run.mkdir(parents=True)
        protocol.ingest_handoff(run, self.console.conversations.handoff(doc['id']))
        row = {'id': 'message-2', 'text': 'Add a JSON output option', 'status': 'received'}
        append_feedback(run, row, product_change=True)
        append_feedback(run, row, product_change=True)
        saved = protocol.read_journal(run)['conversation']
        self.assertEqual(1, sum(item['id'] == 'task-message-2' for item in saved['messages']))
        self.assertEqual(2, len(saved['requirements']['revisions']))
        self.assertEqual('stale', saved['plan_drafts'][-1]['freshness']['state'])
        self.assertNotIn('approval_event', saved)

    def test_concurrent_turn_cannot_attach_an_older_handoff(self):
        doc = self.conversation()
        handoff = self.console.conversations.handoff(doc['id'])
        self.console.conversations.send(doc['id'], 'Also support Unicode names', 'second')
        with self.assertRaisesRegex(ValueError, 'conversation changed'):
            self.console.conversations.claim_attachment(doc['id'], {
                'status': 'starting', 'workspace': str(self.workspace),
                'handoff_digest': handoff['digest']})
        self.assertIsNone(self.console.conversation_get(doc['id'])['attachment'])

    def test_checkpoint_does_not_link_until_runner_accepts_same_journal(self):
        doc = self.conversation()
        attached = self.console.conversation_attach({'id': doc['id'], 'workspace': str(self.workspace)})
        self.console.action_log = lambda *args: [{'id': 'action-1', 'status': 'running'}]
        run = self.workspace / '.autocode/runs/created'
        run.mkdir(parents=True)
        args = self.actions[-1][-1]
        state = {'workspace': str(self.workspace), 'task': args[0], 'status': 'RUNNING'}
        checkpoint = run / 'state.json'
        checkpoint.write_text(json.dumps(state))
        self.assertEqual('starting', self.console.conversation_get(doc['id'])['attachment']['status'])
        handoff = protocol.load_handoff(Path(attached['attachment']['handoff']), workspace=self.workspace)
        protocol.ingest_handoff(run, handoff)
        state['conversation_handoff'] = {'conversation_id': doc['id'], 'digest': handoff['digest']}
        checkpoint.write_text(json.dumps(state))
        self.assertEqual('linked', self.console.conversation_get(doc['id'])['attachment']['status'])

    def test_plan_gate_uses_current_architect_token_and_approval(self):
        doc = self.conversation()
        run = self.workspace / '.autocode/runs/test'
        run.mkdir(parents=True)
        protocol.ingest_handoff(run, self.console.conversations.handoff(doc['id']))
        contract = {'task_id': 'fixture', 'revision': 2, 'body': {'open_blocking_questions': []}}
        contract['hash'] = support.digest(contract)
        token = 'r2:' + contract['hash']
        event = {'kind': 'goal_approval', 'actor': 'user_cli', 'token': token}
        contract.update(approval_status='approved', approval_event=event)
        state = {'workspace': str(self.workspace), 'goal_contract': contract,
                 'user_events': [event], 'planning': {'final_token': 'r1:old'}}
        self.assertFalse(project_conversation(run, state)['plan_gate']['approved'])
        state['planning']['final_token'] = token
        self.assertTrue(project_conversation(run, state)['plan_gate']['approved'])
        state['user_events'] = []
        self.assertFalse(project_conversation(run, state)['plan_gate']['approved'])
        state['user_events'] = [event]
        append_feedback(run, {'id': 'change-1', 'text': 'Support JSON output too', 'status': 'received'},
                        product_change=True, goal_token=token)
        gate = project_conversation(run, state)['plan_gate']
        self.assertTrue(gate['pending_product_change'])
        self.assertFalse(gate['approved'])
        self.assertFalse(gate['architect_reviewed'])

    def test_public_build_action_forwards_exact_token_through_actual_mixin(self):
        run = self.workspace / '.autocode/runs/task'
        run.mkdir(parents=True)
        self.console.workspace_for = lambda raw: self.workspace
        self.console.run_for = lambda workspace, raw: run
        self.console.view = lambda workspace, run: {'goal_token': 'r2:abc', 'goal': {'revision': 2, 'hash': 'abc', 'approval_status': 'approved', 'approval_event': {'token': 'r2:abc'}}}
        self.console._intervention_view = lambda workspace, run: {'mode': 'durable', 'capable': True}
        request = {'action': 'continue', 'workspace': str(self.workspace), 'run': str(run),
                   'token': 'r2:abc', 'confirmation': 'r2:abc', 'expected_goal_token': 'r2:abc'}
        self.console.mutate(request)
        self.assertEqual(['--no-chat', '--resume-paused', '--expected-goal-token', 'r2:abc'], self.actions[-1][-1])
        self.actions.clear()
        with self.assertRaisesRegex(ValueError, 'approved plan changed'):
            self.console.mutate({**request, 'expected_goal_token': 'r1:old'})
        self.assertEqual([], self.actions)


if __name__ == '__main__':
    unittest.main()
