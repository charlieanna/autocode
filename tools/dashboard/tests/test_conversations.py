"""Offline conversation-store and direct-provider boundary checks."""
from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import dashboard_conversations as chats


class ConversationTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name) / 'conversations'
        self.stores = []
        self.addCleanup(self.close_stores)

    def close_stores(self):
        for store in self.stores:
            store.close()

    def store(self, provider=lambda messages, model, workdir: 'What should the first milestone deliver?'):
        store = chats.ConversationStore(self.root, provider)
        self.stores.append(store)
        return store

    def finished(self, store, conversation_id):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            doc = store.get(conversation_id)
            if doc['status'] != 'thinking':
                return doc
            time.sleep(.005)
        self.fail('The fake provider did not finish')

    def test_first_message_is_durable_before_provider_without_a_project(self):
        evidence = []
        def provider(messages, model, workdir):
            saved = json.loads(next(self.root.glob('*.json')).read_text())
            evidence.append((saved, messages, model, workdir))
            return 'Who will use the tool?'
        store = self.store(provider)
        initial = store.create('Build a review helper', request_id='initial')
        doc = self.finished(store, initial['id'])
        self.assertEqual('thinking', initial['status'])
        self.assertEqual('ready', doc['status'])
        self.assertEqual('saved', evidence[0][0]['messages'][0]['status'])
        self.assertEqual('thinking', evidence[0][0]['status'])
        self.assertEqual(chats.DEFAULT_GLM_MODEL, evidence[0][2])
        self.assertTrue(evidence[0][3].is_dir())
        self.assertFalse(any(self.root.rglob('.git')))
        self.assertIsNone(doc['attachment'])
        self.assertEqual(['You', 'Planner'], [m['speaker'] for m in doc['messages']])
        self.assertEqual(['received', 'received'], [m['status'] for m in doc['messages']])
        self.assertEqual(0o600, (self.root / (doc['id'] + '.json')).stat().st_mode & 0o777)
        self.assertFalse(any(k.startswith('_') for k in doc))

    def test_followup_receives_full_transcript_and_persists_across_restart(self):
        transcripts = []
        def provider(messages, model, workdir):
            transcripts.append(messages)
            return 'Reply ' + str(len(transcripts))
        store = self.store(provider)
        doc = self.finished(store, store.create('Idea', request_id='one')['id'])
        store.send(doc['id'], 'Refine it for beginners', request_id='two')
        doc = self.finished(store, doc['id'])
        self.assertEqual(['Idea', 'Reply 1', 'Refine it for beginners'], [m['text'] for m in transcripts[1]])
        other = self.store()
        self.assertEqual(doc, other.get(doc['id']))
        summary = other.list()[0]
        self.assertEqual(4, summary['message_count'])
        self.assertNotIn('messages', summary)
        self.assertEqual('Reply 2', summary['last_message'])

    def test_ready_reply_accepts_followup_before_worker_cleanup(self):
        for other_dashboard in (False, True):
            with self.subTest(other_dashboard=other_dashboard):
                store = self.store()
                sender = self.store() if other_dashboard else store
                reply_saved, release = threading.Event(), threading.Event()
                self.addCleanup(release.set)
                original = store._reply_locked

                def delay_cleanup(conversation_id, turn_id):
                    original(conversation_id, turn_id)
                    if not reply_saved.is_set():
                        reply_saved.set()
                        if not release.wait(3):
                            raise AssertionError('fixture cleanup release timed out')

                # Hold the worker exactly between publishing its completed reply
                # and its outer cleanup, where the old lease blocked new input.
                with patch.object(store, '_reply_locked', delay_cleanup):
                    initial = store.create('First message')
                    try:
                        self.assertTrue(reply_saved.wait(1))
                        self.assertEqual('ready', sender.get(initial['id'])['status'])
                        sent = sender.send(initial['id'], 'Immediate followup')
                        self.assertEqual('thinking', sent['status'])
                        done = self.finished(sender, initial['id'])
                        self.assertEqual(['First message', 'Immediate followup'],
                                         [row['text'] for row in done['messages'] if row['role'] == 'user'])
                    finally:
                        release.set()
                        store.close()

    def test_create_and_send_are_idempotent_and_busy_turn_is_serialized(self):
        release = threading.Event()
        self.addCleanup(release.set)
        started = threading.Event()
        calls = []
        def provider(messages, model, workdir):
            calls.append(messages)
            started.set()
            release.wait(3)
            return 'Ready'
        store = self.store(provider)
        first = store.create('Idea', request_id='create')
        self.assertTrue(started.wait(1))
        duplicate = store.create('Idea', request_id='create')
        self.assertEqual(first['id'], duplicate['id'])
        with self.assertRaisesRegex(ValueError, 'different conversation'):
            store.create('Other idea', request_id='create')
        with self.assertRaisesRegex(ValueError, 'still replying'):
            store.send(first['id'], 'Too soon', request_id='early')
        release.set()
        self.finished(store, first['id'])
        store.send(first['id'], 'Second message', request_id='second')
        done = self.finished(store, first['id'])
        duplicate = store.send(first['id'], 'Second message', request_id='second')
        self.assertEqual(done['messages'], duplicate['messages'])
        self.assertEqual(2, len(calls))
        with self.assertRaisesRegex(ValueError, 'different message'):
            store.send(first['id'], 'Different message', request_id='second')

    def test_retry_preserves_one_user_bubble_and_hides_raw_provider_errors(self):
        calls = []
        def provider(messages, model, workdir):
            calls.append(messages)
            if len(calls) == 1:
                raise RuntimeError('credential=must-not-leak')
            return 'Recovered response'
        store = self.store(provider)
        failed = self.finished(store, store.create('A useful idea')['id'])
        self.assertEqual('error', failed['status'])
        self.assertEqual('error', failed['messages'][0]['status'])
        self.assertNotIn('must-not-leak', json.dumps(failed))
        with self.assertRaisesRegex(ValueError, 'Retry the saved message'):
            store.send(failed['id'], 'Another message')
        retry = store.retry(failed['id'])
        self.assertEqual(1, len(retry['messages']))
        done = self.finished(store, failed['id'])
        self.assertEqual(['user', 'assistant'], [m['role'] for m in done['messages']])
        self.assertIsNone(done['error'])
        with self.assertRaisesRegex(ValueError, 'no failed'):
            store.retry(done['id'])

    def test_restart_marks_abandoned_turn_interrupted_without_calling_provider(self):
        store = self.store()
        doc = self.finished(store, store.create('Idea')['id'])
        store.close()
        path = self.root / (doc['id'] + '.json')
        saved = json.loads(path.read_text())
        saved['messages'].pop()
        saved['status'] = 'thinking'
        saved['_active_turn'] = 'orphaned'
        path.write_text(json.dumps(saved))
        def never(*args):
            self.fail('Restart must not automatically invoke the provider')
        restarted = self.store(never)
        recovered = restarted.get(doc['id'])
        self.assertEqual('error', recovered['status'])
        self.assertIn('restarted', recovered['error'])
        self.assertEqual('error', recovered['messages'][0]['status'])

    def test_second_dashboard_preserves_live_turn_and_attachment_update(self):
        release = threading.Event()
        self.addCleanup(release.set)
        started = threading.Event()
        def provider(messages, model, workdir):
            started.set()
            release.wait(3)
            return 'The plan is ready to review'
        store = self.store(provider)
        doc = store.create('Idea')
        self.assertTrue(started.wait(1))
        second = self.store()
        self.assertEqual('thinking', second.get(doc['id'])['status'])
        second.update(doc['id'], attachment={'status': 'attaching', 'workspace': '/example'})
        release.set()
        done = self.finished(store, doc['id'])
        self.assertEqual({'status': 'attaching', 'workspace': '/example'}, done['attachment'])
        with self.assertRaisesRegex(ValueError, 'attached to a project'):
            store.send(doc['id'], 'New input')
        second.update(doc['id'], attachment=None, title='Useful project')
        self.assertEqual('Useful project', store.get(doc['id'])['title'])

    def test_attachment_claim_is_atomic_across_two_dashboard_instances(self):
        first = self.store()
        doc = self.finished(first, first.create('Attach this plan')['id'])
        second = self.store()
        gate = threading.Barrier(2)
        def claim(store, workspace):
            gate.wait()
            return store.claim_attachment(doc['id'], {'status': 'attaching', 'workspace': workspace})
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in [
                pool.submit(claim, first, '/project-one'),
                pool.submit(claim, second, '/project-two')]]
        self.assertEqual([False, True], sorted(claimed for _, claimed in results))
        self.assertEqual(results[0][0]['attachment'], results[1][0]['attachment'])
        self.assertEqual(results[0][0]['attachment'], first.get(doc['id'])['attachment'])

    def test_failed_attachment_retry_claim_cannot_erase_a_newer_attempt(self):
        first = self.store()
        doc = self.finished(first, first.create('Retry this plan')['id'])
        second = self.store()
        failed = {'status': 'failed', 'workspace': '/example', 'action_id': 'original'}
        first.update(doc['id'], attachment=failed)
        gate = threading.Barrier(2)
        def claim(store, action):
            gate.wait()
            return store.claim_attachment(doc['id'], {'status': 'starting', 'action_id': action},
                                          expected_attachment=failed)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [future.result() for future in [
                pool.submit(claim, first, 'retry-one'),
                pool.submit(claim, second, 'retry-two')]]
        self.assertEqual([False, True], sorted(claimed for _, claimed in results))
        self.assertEqual(results[0][0]['attachment'], results[1][0]['attachment'])
        final = first.get(doc['id'])['attachment']
        self.assertIn(final['action_id'], ('retry-one', 'retry-two'))
        retry, claimed = second.claim_attachment(doc['id'], {'status': 'starting'}, expected_attachment=failed)
        self.assertFalse(claimed)
        self.assertEqual(final, retry['attachment'])
        first.update(doc['id'], attachment=None)
        _, claimed = second.claim_attachment(doc['id'], {'status': 'starting'}, expected_attachment=failed)
        self.assertFalse(claimed, 'A stale expected attachment must not claim even a cleared conversation')

    def test_unchanged_metadata_does_not_rewrite_or_reorder_conversations(self):
        store = self.store()
        doc = self.finished(store, store.create('Idea')['id'])
        path = self.root / (doc['id'] + '.json')
        before = path.stat().st_mtime_ns
        result = store.update(doc['id'], attachment=None, title=doc['title'])
        self.assertEqual(before, path.stat().st_mtime_ns)
        self.assertEqual(doc['updated_at'], result['updated_at'])

    def test_conversation_and_request_validation(self):
        store = self.store()
        for invalid in (None, '', '../outside', '/tmp/a.json', 12, 'a' * 31):
            with self.subTest(conversation_id=invalid), self.assertRaises(ValueError):
                store.get(invalid)
        for invalid in ('', None, 3, 'a' * (chats.MAX_MESSAGE_CHARS + 1), 'null\x00byte'):
            with self.subTest(message=type(invalid).__name__), self.assertRaises(ValueError):
                store.create(invalid)
        for invalid in ({'glm_model': 'gpt-6-astra'}, {'glm_model': 'zai/x y'}, {'glm_model': None}, []):
            with self.subTest(models=invalid), self.assertRaises(ValueError):
                store.create('Idea', models=invalid)
        with self.assertRaises(ValueError):
            store.create('Idea', request_id='../bad')
        with self.assertRaises(ValueError):
            store.update('a' * 32, status='ready')
        self.assertEqual([], store.list())

    def test_context_limit_does_not_discard_or_append_any_message(self):
        store = self.store()
        doc = self.finished(store, store.create('Idea')['id'])
        with patch.object(chats, 'MAX_CONTEXT_CHARS', 10000):
            with self.assertRaisesRegex(ValueError, 'no messages were discarded'):
                store.send(doc['id'], 'Too much context')
        self.assertEqual(doc['messages'], store.get(doc['id'])['messages'])

    def test_output_limit_and_empty_provider_reply_are_explicit_failures(self):
        for response in ('', 'a' * (chats.MAX_REPLY_CHARS + 1)):
            store = self.store(lambda *args, value=response: value)
            doc = self.finished(store, store.create('Idea')['id'])
            self.assertEqual('error', doc['status'])
            self.assertEqual(1, len(doc['messages']))

    def test_default_storage_expands_home_and_explicit_dashboard_override(self):
        with patch.dict(os.environ, {'AUTOCODE_HOME': str(self.root.parent / 'home')}, clear=False):
            with patch.dict(os.environ, {}, clear=False):
                prior = os.environ.pop('AUTOCODE_DASHBOARD_HOME', None)
                try:
                    store = chats.ConversationStore(provider=lambda *args: 'Reply')
                    self.stores.append(store)
                    self.assertEqual((self.root.parent / 'home/dashboard/conversations').resolve(), store.root)
                finally:
                    if prior is not None:
                        os.environ['AUTOCODE_DASHBOARD_HOME'] = prior
        with patch.dict(os.environ, {'AUTOCODE_DASHBOARD_HOME': str(self.root.parent / 'custom')}):
            store = chats.ConversationStore(provider=lambda *args: 'Reply')
            self.stores.append(store)
            self.assertEqual((self.root.parent / 'custom/conversations').resolve(), store.root)


class ProviderTests(unittest.TestCase):
    def test_selected_openai_planner_uses_its_oauth_connection_and_exact_model(self):
        model = 'openai/gpt-5.6-sol'
        directory = Path('/private/tmp/scratch')
        with patch.object(chats.opencode_transport, 'check_subscription_routes') as auth, \
             patch.object(chats, '_capture', return_value=(0, json.dumps({'type':'text','part':{'text':'A plan'}}))) as capture:
            self.assertEqual('A plan', chats.opencode_provider([{'role':'user','text':'My idea'}], model, directory))
        auth.assert_called_once_with({'glm':{'model':model}}, directory)
        command = capture.call_args.args[0]
        self.assertEqual(model, command[command.index('--model') + 1])
        self.assertIn('--pure', command)
        self.assertNotIn('--session', command)

    def test_planner_billing_guard_stops_before_a_model_request(self):
        with patch.object(chats.opencode_transport, 'check_subscription_routes', side_effect=RuntimeError('OAuth required')), \
             patch.object(chats, '_capture') as capture:
            with self.assertRaisesRegex(chats.ConversationProviderError, 'OAuth required'):
                chats.opencode_provider([], 'openai/gpt-5.6-sol', Path('/private/tmp/scratch'))
        capture.assert_not_called()

    def test_command_is_pure_tool_free_and_preserves_inline_provider_config(self):
        captured = []
        def capture(command, env, cwd, prompt):
            captured.append((command, env, cwd, prompt))
            return 0, json.dumps({'type': 'text', 'part': {'text': 'A draft plan'}})
        inherited = {'provider': {'zai-coding-plan': {'options': {'test': 'preserved'}}},
                     'agent': {'other': {'permission': {'bash': 'allow'}}},
                     'share': 'auto', 'permission': {'*': 'allow'}}
        with patch.dict(os.environ, {'OPENCODE_CONFIG_CONTENT': json.dumps(inherited), 'OPENCODE_PERMISSION': 'allow'}), \
                patch.object(chats, '_capture', side_effect=capture):
            reply = chats.opencode_provider([{'role': 'user', 'text': 'My idea'}], chats.DEFAULT_GLM_MODEL, Path('/private/tmp/scratch'))
        command, env, _, prompt = captured[0]
        self.assertEqual('A draft plan', reply)
        self.assertIn('--pure', command)
        self.assertNotIn('--auto', command)
        self.assertNotIn('--session', command)
        config = json.loads(env['OPENCODE_CONFIG_CONTENT'])
        name = command[command.index('--agent') + 1]
        self.assertTrue(name.startswith('autocode_conversation_'))
        self.assertEqual({'*': 'deny'}, config['agent'][name]['permission'])
        self.assertEqual({'*': 'deny'}, json.loads(env['OPENCODE_PERMISSION']))
        self.assertEqual(inherited['provider'], config['provider'])
        self.assertEqual(inherited['agent']['other'], config['agent']['other'])
        self.assertEqual('disabled', config['share'])
        self.assertFalse(config['autoupdate'])
        self.assertIn('no repository access', prompt)
        self.assertIn('My idea', prompt)

    def test_transport_errors_never_expose_raw_diagnostics(self):
        rows = [(1, 'api_key=private-secret'),
                (0, json.dumps({'type': 'error', 'error': 'private-secret'})),
                (0, json.dumps({'type': 'tool_use', 'part': {'text': 'private-secret'}})),
                (0, 'not a JSON event private-secret')]
        for row in rows:
            with self.subTest(output=row), patch.object(chats, '_capture', return_value=row):
                with self.assertRaises(chats.ConversationProviderError) as error:
                    chats.opencode_provider([{'role': 'user', 'text': 'Idea'}], chats.DEFAULT_GLM_MODEL, Path('/tmp'))
                self.assertNotIn('private-secret', str(error.exception))

    def test_capture_replaces_invalid_utf8_and_writes_full_stdin(self):
        script = 'import sys; data=sys.stdin.buffer.read(); sys.stdout.buffer.write(data+b"\\xff")'
        code, output = chats._capture([sys.executable, '-c', script], dict(os.environ), '/private/tmp', 'prompt' * 30000, timeout=3)
        self.assertEqual(0, code)
        self.assertEqual('prompt' * 30000 + '\ufffd', output)

    def test_capture_stops_timeout_and_output_overflow(self):
        cases = [('import time; time.sleep(5)', .1, 1024, 'too long'),
                 ('import sys; sys.stdout.write("x"*1000000)', 3, 1024, 'too much output')]
        for script, timeout, limit, message in cases:
            with self.subTest(message=message):
                start = time.monotonic()
                with self.assertRaisesRegex(chats.ConversationProviderError, message):
                    chats._capture([sys.executable, '-c', script], dict(os.environ), '/private/tmp', 'prompt', timeout=timeout, output_limit=limit)
                self.assertLess(time.monotonic() - start, 3)

    def test_provider_kills_process_group_and_reaps_on_timeout(self):
        with patch.object(chats.os, 'killpg', wraps=os.killpg) as killpg:
            with self.assertRaises(chats.ConversationProviderError):
                chats._capture([sys.executable, '-c', 'import time; time.sleep(5)'], dict(os.environ), '/private/tmp', 'prompt', timeout=.1)
        self.assertTrue(killpg.called)
        self.assertEqual(chats.signal.SIGKILL, killpg.call_args.args[1])


if __name__ == '__main__':
    unittest.main()
