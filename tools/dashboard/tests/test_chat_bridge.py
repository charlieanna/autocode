"""Conversation/task handoff tests with local fake models and a fake CLI only."""
import http.client
import hashlib
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent_console import Console, Handler, ThreadingHTTPServer
from dashboard_chat import planning_messages


FAKE_RUNNER = r'''
import json, sys
from pathlib import Path
root = Path(__file__).parent
args = sys.argv[1:]
def arg(flag): return args[args.index(flag) + 1]
def read(name, default):
 p = root / name
 return json.loads(p.read_text()) if p.exists() else default
def emit(value): print(json.dumps(value))
if args[:2] == ['registry', 'location']:
 emit({'registry_version':1,'operation':'location','registry_path':str(root/'registry.json')})
elif args[:2] == ['registry', 'list']:
 emit({'registry_version':1,'operation':'list','runs':[],'workspaces':[]})
elif '--status' in args:
 state=json.loads((Path(arg('--run-dir'))/'state.json').read_text())
 emit({'status':state['status'],'interventions':{
   'inspector_capability':{'supported':True,'version':1},
   'runner_capability':{'supported':True,'version':1},
   'applied_receipts':read('applied.json',[]),'blocked_conditions':[]}})
elif args[:2] == ['intervention', 'inspect']:
 emit({'version':1,'operation':'inspect','requests':read('inbox.json',[])})
elif args[:2] == ['intervention', 'submit']:
 if (root/'bad-receipt').exists(): emit({'receipt':{'id':'wrong'}})
 else:
  inbox=read('inbox.json',[]);ident=arg('--request-id');text=arg('--text')
  receipt=next((r for r in inbox if r['id']==ident),None)
  duplicate=bool(receipt)
  if not receipt:
   receipt={'id':ident,'text':text,'kind':arg('--kind'),'order':len(inbox)+1,'submitted_at':'2026-09-20T00:00:00Z'}
   inbox.append(receipt);(root/'inbox.json').write_text(json.dumps(inbox))
  emit({'version':1,'operation':'submit','accepted':True,'receipt':receipt,'idempotent':duplicate})
else:
 with (root/'commands.jsonl').open('a') as handle: handle.write(json.dumps(args)+'\n')
 workspace=Path(arg('--workspace'))
 if '--run-dir' not in args:
  goal=args[args.index('--workspace')+2]
  if (root/'fail-before-checkpoint').exists():
   print('fixture startup failed before checkpoint',file=sys.stderr);raise SystemExit(1)
  run=workspace/'.autocode/runs/created';run.mkdir(parents=True)
  roles={role:{'model':arg('--'+role+'-model')} for role in ('glm','astra','terra','sol') if '--'+role+'-model' in args}
  state={'workspace':str(workspace),'task':goal,'status':'WAITING_FOR_USER','phase':'discovery',
   'settings':{'joint_planning':'--joint-planning' in args,'engine':'opencode','roles':roles},'pending_questions':[],
   'intervention_capability':{'supported':True,'version':1}}
  (run/'state.json').write_text(json.dumps(state))
  if (root/'fail-after-checkpoint').exists():
   print('fixture provider unavailable after checkpoint',file=sys.stderr);raise SystemExit(1)
 else:
  run=Path(arg('--run-dir'));path=run/'state.json';state=json.loads(path.read_text())
  if '--answer' in args or '--delegate' in args:
   if (root/'answer-fails').exists():
    print('fixture answer rejected',file=sys.stderr);raise SystemExit(1)
   ident,text=arg('--answer').split('=',1) if '--answer' in args else (arg('--delegate'),'delegated')
   state.setdefault('answers',{})[ident]=text
   state['pending_questions']=[q for q in state.get('pending_questions',[]) if q['id']!=ident]
  else: state['continued']=state.get('continued',0)+1
  path.write_text(json.dumps(state))
 print('fixture saved')
'''


class ChatFixture:
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.workspace = self.root / 'project'
        (self.workspace / '.git').mkdir(parents=True)
        self.fake = self.root / 'runner.py'
        self.fake.write_text(FAKE_RUNNER)
        self.provider_calls = []
        self.console = self.make_console()

    def provider(self, messages, model, workdir):
        self.provider_calls.append((messages, model, workdir))
        return 'GLM: ' + messages[-1]['text']

    def make_console(self):
        console = Console([self.workspace], self.fake, lambda: None,
                          conversation_root=self.root / 'dashboard/conversations',
                          conversation_provider=self.provider)
        console.catalogue.fetch = lambda **kwargs: {
            'usable': True, 'models': ['zai-coding-plan/glm-5.3', 'zai-coding-plan/glm-5.3-flash']}
        def close():
            console.pool.shutdown(wait=True)
            if console._conversation_store is not None:
                console._conversation_store.close()
        self.addCleanup(close)
        return console

    def eventually(self, function, timeout=5):
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            result = function()
            if result:
                return result
            time.sleep(.01)
        self.fail('Timed out waiting for the fake runner/provider')

    def ready(self, doc):
        return self.eventually(lambda: (value if (value := self.console.conversation_get(doc['id']))['status'] == 'ready' else None))

    def create_conversation(self, text='Build a personal engineering journal', **extra):
        return self.ready(self.console.conversation_create({'text': text, 'request_id': 'start-request', **extra}))

    def commands(self):
        path = self.root / 'commands.jsonl'
        return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []

    def settled(self):
        def idle():
            return not self.console.pending and all('finished_at' in a for rows in self.console.actions.values() for a in rows)
        self.eventually(idle)

    def make_run(self, questions=()):
        self.run = self.workspace / '.autocode/runs/manual'
        self.run.mkdir(parents=True)
        self.state = {'workspace': str(self.workspace), 'task': 'Existing task', 'status': 'WAITING_FOR_USER',
                      'pending_questions': list(questions), 'intervention_capability': {'supported': True, 'version': 1}}
        self.save_state()
        return self.run

    def save_state(self):
        (self.run / 'state.json').write_text(json.dumps(self.state))

    def read_state(self):
        return json.loads((self.run / 'state.json').read_text())

    def chat(self, text='Please retain the history', **extra):
        return self.console.chat({'workspace': str(self.workspace), 'run': str(self.run),
                                  'request_id': 'message-request', 'text': text, **extra})


class ChatBridgeTests(ChatFixture, unittest.TestCase):
    def test_project_free_conversation_persists_and_replays_without_starting_runner(self):
        data = {'text': 'Plan a journal', 'request_id': 'conversation-start'}
        first = self.ready(self.console.conversation_create(data))
        self.assertEqual(first['id'], self.console.conversation_create(data)['id'])
        second = self.ready(self.console.conversations.send(first['id'], 'Keep it local', 'followup-request'))
        restored = self.make_console().conversation_get(first['id'])
        self.assertEqual(second['messages'], restored['messages'])
        self.assertEqual(['Plan a journal', 'GLM: Plan a journal', 'Keep it local', 'GLM: Keep it local'],
                         [row['text'] for row in restored['messages']])
        self.assertIsNone(restored['attachment'])
        self.assertEqual([], self.commands())
        self.assertFalse((self.workspace / '.autocode').exists())
        self.assertTrue(all(workdir.is_relative_to(self.root / 'dashboard') for _, _, workdir in self.provider_calls))

    def test_attachment_carries_full_transcript_models_joint_flag_and_canonical_project(self):
        models = {'glm_model': 'zai-coding-plan/glm-5.3', 'astra_model': 'gpt-6-astra',
                  'terra_model': 'zai-coding-plan/glm-5.3-flash', 'sol_model': 'gpt-5.6-sol'}
        doc = self.create_conversation(models=models)
        doc = self.ready(self.console.conversations.send(doc['id'], 'Actually include review notes', 'revise-request'))
        alias = self.root / 'project-alias'
        alias.symlink_to(self.workspace)
        attach = {'id': doc['id'], 'project': str(alias) + '/'}
        self.console.conversation_attach(attach)
        linked = self.eventually(lambda: (value if (value := self.console.conversation_get(doc['id']))['attachment']['status'] == 'linked' else None))
        self.settled()
        self.console.conversation_attach(attach)
        commands = self.commands()
        self.assertEqual(1, len(commands))
        args = commands[0]
        self.assertEqual(str(self.workspace), args[args.index('--workspace') + 1])
        goal = args[args.index('--workspace') + 2]
        for message in doc['messages']:
            self.assertIn(message['text'], goal)
        self.assertIn(doc['id'], goal)
        for role, model in models.items():
            self.assertEqual(model, args[args.index('--' + role.replace('_', '-')) + 1])
        self.assertIn('--joint-planning', args)
        self.assertIn('--no-chat', args)
        self.assertNotIn('--approve-goal', args)
        run = Path(linked['attachment']['run'])
        view = self.console.task_view(self.workspace, run)
        self.assertEqual(doc['messages'], view['draft_messages'])
        self.assertEqual(doc['title'], view['task'])
        self.assertEqual(linked['attachment']['action_id'], view['actions'][0]['id'])

    def test_attach_creates_requested_git_project_with_initial_commit(self):
        doc = self.create_conversation()
        project = self.root / 'new-project'
        self.console.conversation_attach({'id': doc['id'], 'project': str(project), 'create_project': True})
        self.settled()
        self.assertTrue((project / '.git').is_dir())
        revision = subprocess.run(['git', '-C', str(project), 'rev-parse', '--verify', 'HEAD'], capture_output=True, text=True)
        self.assertEqual(0, revision.returncode, revision.stderr)
        self.assertEqual(str(project), self.commands()[0][self.commands()[0].index('--workspace') + 1])

    def test_escaping_run_storage_is_rejected_before_launch_and_during_reconciliation(self):
        doc = self.create_conversation()
        outside = self.root / 'outside-runs'
        outside.mkdir()
        (self.workspace / '.autocode').mkdir()
        (self.workspace / '.autocode/runs').symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'storage must stay inside'):
            self.console.conversation_attach({'id': doc['id'], 'project': str(self.workspace)})
        self.assertEqual([], self.commands())
        self.assertIsNone(self.console.conversation_get(doc['id'])['attachment'])
        escaped_run = outside / 'matching-checkpoint'
        escaped_run.mkdir()
        goal = 'Exact prior handoff goal'
        (escaped_run / 'state.json').write_text(json.dumps({'workspace': str(self.workspace), 'task': goal}))
        self.console.conversations.update(doc['id'], attachment={
            'workspace': str(self.workspace), 'goal_hash': hashlib.sha256(goal.encode()).hexdigest(),
            'status': 'starting', 'run': None, 'action_id': 'prior-action'})
        attachment = self.console.conversation_get(doc['id'])['attachment']
        self.assertEqual('failed', attachment['status'])
        self.assertIsNone(attachment['run'])
        self.assertIn('storage escapes', attachment['error'])
        self.assertEqual([], self.commands())

    def test_startup_failure_before_checkpoint_stays_visible_and_does_not_spawn_twice(self):
        (self.root / 'fail-before-checkpoint').touch()
        doc = self.create_conversation()
        attach = {'id': doc['id'], 'project': str(self.workspace)}
        self.console.conversation_attach(attach)
        self.settled()
        failed = self.console.conversation_get(doc['id'])
        self.assertEqual('failed', failed['attachment']['status'])
        self.assertIn('startup failed', failed['attachment']['error'])
        self.console.conversation_attach(attach)
        self.assertEqual(1, len(self.commands()))

    def test_confirmed_startup_failure_can_retry_explicitly_once(self):
        marker = self.root / 'fail-before-checkpoint'
        marker.touch()
        doc = self.create_conversation()
        attach = {'id': doc['id'], 'project': str(self.workspace)}
        self.console.conversation_attach(attach)
        self.settled()
        self.assertEqual('failed', self.console.conversation_get(doc['id'])['attachment']['status'])
        marker.unlink()
        self.console.conversation_attach({**attach, 'retry': True})
        self.settled()
        self.assertEqual('linked', self.console.conversation_get(doc['id'])['attachment']['status'])
        self.assertEqual(2, len(self.commands()))

    def test_unconfirmed_handoff_after_restart_never_automatically_relaunches(self):
        marker = self.root / 'fail-before-checkpoint'
        marker.touch()
        doc = self.create_conversation()
        attach = {'id': doc['id'], 'project': str(self.workspace)}
        self.console.conversation_attach(attach)
        self.settled()
        # No status reconciliation before the restart: durable intent remains starting.
        restored = self.make_console()
        recovered = restored.conversation_get(doc['id'])
        self.assertEqual('uncertain', recovered['attachment']['status'])
        restored.conversation_attach({**attach, 'retry': True})
        self.assertEqual(1, len(self.commands()))

    def test_two_dashboard_instances_cannot_attach_same_conversation_twice(self):
        doc = self.create_conversation()
        other = self.make_console()
        barrier = threading.Barrier(2)
        for console in (self.console, other):
            original = console.conversation_get
            def synchronized(ident, original=original):
                value = original(ident)
                barrier.wait(timeout=3)
                return value
            console.conversation_get = synchronized
        with ThreadPoolExecutor(max_workers=2) as pool:
            actions = [pool.submit(console.conversation_attach, {'id': doc['id'], 'project': str(self.workspace)})
                       for console in (self.console, other)]
            for action in actions:
                action.result(timeout=5)
        self.console.pool.shutdown(wait=True)
        other.pool.shutdown(wait=True)
        self.assertEqual(1, len(self.commands()))

    def test_two_dashboard_instances_retrying_same_rejected_handoff_launch_once(self):
        doc = self.create_conversation()
        attach = {'id': doc['id'], 'project': str(self.workspace)}
        with patch.object(self.console, 'create', side_effect=ValueError('fixture rejected before launch')):
            with self.assertRaisesRegex(ValueError, 'handoff failed'):
                self.console.conversation_attach(attach)
        prior = self.console.conversation_get(doc['id'])['attachment']
        self.assertEqual('failed', prior['status'])
        self.assertTrue(prior['launch_rejected'])
        other = self.make_console()
        barrier = threading.Barrier(2)
        for console in (self.console, other):
            original = console.conversation_get
            def synchronized(ident, original=original):
                value = original(ident)
                self.assertEqual(prior, value['attachment'])
                barrier.wait(timeout=3)
                return value
            console.conversation_get = synchronized
        with ThreadPoolExecutor(max_workers=2) as pool:
            actions = [pool.submit(console.conversation_attach, {**attach, 'retry': True})
                       for console in (self.console, other)]
            for action in actions:
                action.result(timeout=5)
        self.console.pool.shutdown(wait=True)
        other.pool.shutdown(wait=True)
        self.assertEqual(1, len(self.commands()))

    def test_startup_failure_after_checkpoint_is_visible_in_linked_task(self):
        (self.root / 'fail-after-checkpoint').touch()
        doc = self.create_conversation()
        self.console.conversation_attach({'id': doc['id'], 'project': str(self.workspace)})
        self.settled()
        linked = self.console.conversation_get(doc['id'])
        run = Path(linked['attachment']['run'])
        view = self.console.task_view(self.workspace, run)
        self.assertEqual('failed', view['actions'][0]['status'])
        self.assertIn('provider unavailable', view['actions'][0]['stderr'])
        self.assertEqual(doc['messages'], view['draft_messages'])

    def test_long_valid_conversation_can_attach_without_losing_context(self):
        doc = self.create_conversation('a' * 18000)
        self.assertGreater(sum(len(row['text']) for row in doc['messages']), 32000)
        self.console.conversation_attach({'id': doc['id'], 'project': str(self.workspace)})
        self.settled()
        self.assertEqual(1, len(self.commands()))
        self.assertIn(doc['messages'][-1]['text'], self.commands()[0][2])

    def test_answers_continue_only_after_last_pending_question_and_replay_is_idempotent(self):
        self.make_run([{'id': 'q1', 'question': 'Who is this for?'}, {'id': 'q2', 'question': 'Which platform?'}])
        row = self.chat('Just me', question_id='q1')
        self.eventually(lambda: self.console._chat_rows(self.run)[0]['status'] == 'received')
        self.assertEqual('Who is this for?', row['question_text'])
        self.assertEqual(1, len(self.commands()))
        self.assertNotIn('continued', self.read_state())
        self.chat('Just me', question_id='q1')
        self.assertEqual(1, len(self.commands()))
        self.chat('Browser', question_id='q2', request_id='second-message')
        self.eventually(lambda: self.read_state().get('continued') == 1)
        self.assertEqual(3, len(self.commands()))
        self.assertEqual({'q1': 'Just me', 'q2': 'Browser'}, self.read_state()['answers'])
        self.assertTrue(all('--no-chat' in args for args in self.commands()))
        self.assertIn('--resume-paused', self.commands()[-1])
        restored = self.make_console()
        restored.conversations
        self.assertEqual(['q1', 'q2'], [row['question_id'] for row in restored.task_view(self.workspace, self.run)['chat_messages']])

    def test_delegate_uses_same_composer_and_continues_after_last_question(self):
        self.make_run([{'id': 'q1', 'question': 'Platform?', 'proposed_default': 'Browser'}])
        self.chat('', question_id='q1', delegate=True)
        self.eventually(lambda: self.read_state().get('continued') == 1)
        self.assertIn('--delegate', self.commands()[0])
        self.assertEqual('Use the suggested default: Browser', self.console._chat_rows(self.run)[0]['text'])

    def test_failed_answer_retains_message_and_never_continues(self):
        self.make_run([{'id': 'q1', 'question': 'Platform?'}])
        (self.root / 'answer-fails').touch()
        self.chat('Browser', question_id='q1')
        self.eventually(lambda: self.console._chat_rows(self.run)[0]['status'] == 'error')
        self.assertIn('answer rejected', self.console._chat_rows(self.run)[0]['error'])
        self.assertNotIn('continued', self.read_state())
        self.assertEqual(1, len(self.commands()))

    def test_restart_confirms_saved_answer_only_from_matching_checkpoint_text(self):
        self.make_run()
        self.state['answers'] = {'q1': {'text': 'Browser only'}}
        self.save_state()
        row = {'id': 'restart-answer', 'role': 'user', 'speaker': 'You', 'text': 'Browser only',
               'submitted_text': 'Browser only', 'question_id': 'q1', 'question_text': 'Platform?',
               'delegate': False, 'status': 'saved', 'action_id': 'lost-process-action', 'error': None}
        self.console._save_chat(self.run, row)
        restored = self.make_console()
        restored.conversations
        message = restored.task_view(self.workspace, self.run)['chat_messages'][0]
        self.assertEqual('received', message['status'])
        self.assertIsNone(message['error'])
        self.assertEqual('received', restored._chat_rows(self.run)[0]['status'])
        self.assertNotIn('continued', self.read_state())
        self.assertEqual([], self.commands())

    def test_restart_keeps_unconfirmed_answers_visible_for_explicit_same_id_retry(self):
        self.make_run([{'id': ident, 'question': ident} for ident in ('missing', 'changed', 'delegated')])
        self.state['answers'] = {'changed': {'text': 'Different value'}, 'delegated': {'text': 'Browser only'}}
        self.save_state()
        for ident in ('missing', 'changed', 'delegated'):
            self.console._save_chat(self.run, {
                'id': 'restart-' + ident, 'role': 'user', 'speaker': 'You', 'text': 'Browser only',
                'submitted_text': 'Browser only', 'question_id': ident, 'question_text': ident,
                'delegate': ident == 'delegated', 'status': 'saved', 'action_id': 'lost-' + ident, 'error': None})
        restored = self.make_console()
        restored.conversations
        messages = restored.task_view(self.workspace, self.run)['chat_messages']
        self.assertEqual(['error'] * 3, [row['status'] for row in messages])
        self.assertTrue(all('could not be confirmed after restart' in row['error'] for row in messages))
        self.assertEqual(['error'] * 3, [row['status'] for row in restored._chat_rows(self.run)])
        self.assertEqual([], self.commands())
        data = {'workspace': str(self.workspace), 'run': str(self.run), 'text': 'Browser only',
                'question_id': 'missing', 'request_id': 'restart-missing'}
        self.assertEqual('error', restored.chat(data)['status'])
        self.assertEqual([], self.commands())
        restored.chat({**data, 'retry': True})
        self.eventually(lambda: restored._chat_rows(self.run)[0]['status'] == 'received')
        self.assertEqual(1, len(self.commands()))
        self.assertIn('--answer', self.commands()[0])
        self.assertEqual(3, len(restored._chat_rows(self.run)))

    def test_planning_timeline_keeps_timestamps_and_reads_only_contained_unique_artifacts(self):
        self.make_run()
        initial = self.run / 'initial.json'
        initial.write_text(json.dumps({'summary': 'First clarification reply'}))
        joint = self.run / 'joint.json'
        joint.write_text(json.dumps({'summary': 'Duplicate artifact text must not appear'}))
        outside = self.root / 'outside.json'
        outside.write_text(json.dumps({'summary': 'Outside project private content'}))
        escaped = self.run / 'symlink.json'
        escaped.symlink_to(outside)
        oversized = self.run / 'oversized.json'
        oversized.write_text(json.dumps({'summary': 'x' * 524288}))
        rejected = self.run / 'rejected.json'
        rejected.write_text(json.dumps({'summary': 'Rejected stage text'}))
        state = {'planning': {'reports': {'astra_discovery': {'output': str(joint), 'report': {'summary': 'Current GLM draft'}}}},
                 'stages': [
                     {'stage': 'astra_discovery', 'role': 'glm', 'output': str(initial), 'exit_code': 0,
                      'started_at': '2026-09-20T10:00:00Z', 'finished_at': '2026-09-20T10:01:00Z'},
                     {'stage': 'astra_discovery', 'role': 'glm', 'output': str(joint), 'exit_code': 0,
                      'started_at': '2026-09-20T10:02:00Z'},
                     *[{'stage': 'astra_discovery', 'output': str(path), 'exit_code': 0} for path in (outside, escaped, oversized)],
                     {'stage': 'astra_discovery', 'output': str(rejected), 'exit_code': 0, 'rejected': True}]}
        messages = planning_messages(state, self.run)
        self.assertEqual({'First clarification reply', 'Current GLM draft'}, {row['text'] for row in messages})
        by_text = {row['text']: row for row in messages}
        self.assertEqual('2026-09-20T10:01:00Z', by_text['First clarification reply']['created_at'])
        self.assertEqual('2026-09-20T10:02:00Z', by_text['Current GLM draft']['created_at'])
        self.assertEqual(['GLM', 'GLM'], [row['speaker'] for row in messages])

    def test_stale_question_and_conflicting_replay_are_rejected_without_commands(self):
        self.make_run([{'id': 'current', 'question': 'Current question'}])
        for question in ('old', None):
            with self.assertRaisesRegex(ValueError, 'no longer pending|Choose which question'):
                self.chat('answer', question_id=question)
        self.assertEqual([], self.commands())
        self.assertEqual([], self.console._chat_rows(self.run))
        self.chat('answer', question_id='current')
        with self.assertRaisesRegex(ValueError, 'different message'):
            self.chat('different answer', question_id='current')

    def test_feedback_has_durable_receipt_survives_reload_and_replays_once(self):
        self.make_run()
        first = self.chat()
        self.assertEqual('received', first['status'])
        self.assertTrue(first['receipt']['durable'])
        self.chat()
        inbox = json.loads((self.root / 'inbox.json').read_text())
        self.assertEqual(1, len(inbox))
        restored = self.make_console()
        restored.conversations
        self.assertEqual(first, restored.task_view(self.workspace, self.run)['chat_messages'][0] | {})
        (self.root / 'applied.json').write_text(json.dumps(inbox))
        restored.status_cache.clear()
        self.assertEqual('applied', restored.task_view(self.workspace, self.run)['chat_messages'][0]['status'])
        self.assertEqual([], self.commands())

    def test_uncertain_feedback_is_not_reported_received(self):
        self.make_run()
        (self.root / 'bad-receipt').touch()
        result = self.chat()
        self.assertEqual('error', result['status'])
        self.assertIn('uncertain', result['error'].lower())
        self.assertFalse(result['receipt']['durable'])

    def test_explicit_feedback_retry_reuses_durable_request_id(self):
        self.make_run()
        marker = self.root / 'bad-receipt'
        marker.touch()
        failed = self.chat()
        marker.unlink()
        self.assertEqual(failed, self.chat())
        self.assertFalse((self.root / 'inbox.json').exists())
        retry = self.chat(retry=True)
        self.assertEqual('received', retry['status'])
        self.assertTrue(retry['receipt']['durable'])
        self.assertEqual(failed['id'], retry['receipt']['id'])
        self.chat(retry=True)
        self.assertEqual(1, len(json.loads((self.root / 'inbox.json').read_text())))
        self.assertEqual(1, len(self.console._chat_rows(self.run)))

    def test_two_dashboards_replaying_same_message_submit_only_once(self):
        self.make_run()
        other = self.make_console()
        entered, release, second_started = threading.Event(), threading.Event(), threading.Event()
        submitted = []
        for console in (self.console, other):
            original = console.intervene
            def held_submit(*args, original=original, **kwargs):
                submitted.append(args)
                entered.set()
                if not release.wait(timeout=3):
                    raise AssertionError('fixture release timed out')
                return original(*args, **kwargs)
            console.intervene = held_submit
        data = {'workspace': str(self.workspace), 'run': str(self.run), 'text': 'Keep the same history',
                'request_id': 'concurrent-replay'}
        def replay():
            second_started.set()
            return other.chat(data)
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(self.console.chat, data)
            try:
                self.assertTrue(entered.wait(timeout=2))
                second = pool.submit(replay)
                self.assertTrue(second_started.wait(timeout=2))
            finally:
                release.set()
            first.result(timeout=3)
            second.result(timeout=3)
        self.assertEqual(1, len(submitted))
        self.assertEqual(1, len(json.loads((self.root / 'inbox.json').read_text())))
        self.assertEqual(1, len(self.console._chat_rows(self.run)))

    def test_concurrent_receipt_saves_from_two_dashboards_do_not_overwrite_each_other(self):
        self.make_run()
        other = self.make_console()
        barrier = threading.Barrier(2)
        for console in (self.console, other):
            original = console._chat_rows
            def delayed_read(run, original=original):
                value = original(run)
                # Both writers could read the empty list before either saved without the OS lock.
                time.sleep(.03)
                return value
            console._chat_rows = delayed_read
        def save(console, ident):
            barrier.wait(timeout=2)
            return console._save_chat(self.run, {'id': ident, 'text': ident, 'status': 'saved'})
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(save, console, ident) for console, ident in
                       ((self.console, 'message-one'), (other, 'message-two'))]
            for future in futures:
                future.result(timeout=3)
        self.assertEqual({'message-one', 'message-two'}, {row['id'] for row in self.console._chat_rows(self.run)})


class ChatHttpTests(ChatFixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.console = self.console
        self.host = '127.0.0.1:' + str(self.server.server_port)
        self.server.hosts = {self.host, 'localhost:' + str(self.server.server_port)}
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        def shutdown():
            self.server.shutdown()
            self.server.server_close()
            self.thread.join(timeout=2)
        self.addCleanup(shutdown)

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=json.dumps(data) if data is not None else None,
                               headers={'Content-Type': 'application/json', **(headers or {})})
            response = connection.getresponse()
            return response.status, json.loads(response.read())
        finally:
            connection.close()

    def test_http_chat_round_trip_is_persistent_without_project(self):
        status, started = self.request('POST', '/api/conversations', {'text': 'Draft a review journal', 'request_id': 'http-start'})
        self.assertEqual(202, status)
        self.ready(started)
        status, loaded = self.request('GET', '/api/conversation?id=' + started['id'])
        self.assertEqual(200, status)
        self.assertEqual(2, len(loaded['messages']))
        status, _ = self.request('POST', '/api/conversation/message', {'id': started['id'], 'text': 'Only local storage', 'request_id': 'http-followup'})
        self.assertEqual(202, status)
        self.ready(started)
        status, listing = self.request('GET', '/api/conversations')
        self.assertEqual(200, status)
        self.assertEqual(4, listing['conversations'][0]['message_count'])
        self.assertEqual([], self.commands())

    def test_saved_diff_endpoint_is_scoped_read_only_and_hidden_with_task(self):
        from urllib.parse import urlencode
        self.make_run()
        diff = self.run / 'implementation.diff'
        diff.write_text('--- a/app.py\n+++ b/app.py\n+print("Hello")\n')
        self.state['stages'] = [{'stage': 'terra', 'diff_ref': str(diff)}]
        self.save_state()
        route = '/api/evidence?' + urlencode({'workspace': str(self.workspace), 'run': str(self.run)})
        code, data = self.request('GET', route)
        self.assertEqual(200, code)
        self.assertEqual(0, data['stages'][0]['index'])
        code, data = self.request('GET', route + '&stage=0')
        self.assertEqual(200, code)
        self.assertIn('+print', data['text'])
        self.assertEqual(403, self.request('GET', route, headers={'Origin': 'http://evil.test'})[0])
        self.assertEqual(400, self.request('GET', route + '&stage=-1')[0])
        self.console.task_archive_action({'workspace': str(self.workspace), 'run': str(self.run), 'action': 'archive'})
        self.assertEqual(404, self.request('GET', route)[0])
        self.assertEqual([], self.commands())

    def test_http_chat_endpoint_rejects_stale_question_and_accepts_feedback(self):
        self.make_run()
        data = {'workspace': str(self.workspace), 'run': str(self.run), 'text': 'Keep history', 'request_id': 'http-feedback'}
        code, result = self.request('POST', '/api/chat', {**data, 'question_id': 'obsolete'})
        self.assertEqual(400, code)
        self.assertIn('no longer pending', result['error'])
        code, result = self.request('POST', '/api/chat', data)
        self.assertEqual(202, code)
        self.assertTrue(result['receipt']['durable'])

    def test_all_read_and_write_conversation_routes_reject_untrusted_hosts(self):
        for method, path, data in [('GET', '/api/conversations', None), ('GET', '/api/runs', None),
                                   ('POST', '/api/conversations', {'text': 'blocked'})]:
            code, value = self.request(method, path, data, {'Host': 'untrusted.example'})
            self.assertEqual(403, code)
            self.assertIn('cross-origin', value['error'])
        self.assertEqual([], self.provider_calls)


if __name__ == '__main__':
    unittest.main()
