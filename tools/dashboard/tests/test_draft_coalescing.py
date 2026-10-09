"""At most one live Planner draft in flight; quick turns coalesce into one update (#21).

Offline only: a deterministic worker queue and a fake dispatch-aware Planner
transport. Human turns arrive from inside the fake Planner call, i.e. exactly
while that draft is in flight and holds its delivery lease.
"""
import json
import sys
import tempfile
import unittest
from concurrent.futures import Future
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(__file__).resolve().parents[2])]
import autocode_conversation as protocol
import conversation_draft_cadence as cadence
import dashboard_continuous as continuous
from conversation_transport import ConversationProviderError


class Queue:
    def __init__(self, *args, **kwargs):
        self.jobs = []

    def submit(self, callback, *args):
        self.jobs.append((callback, args))
        return Future()

    def drain(self):
        while self.jobs:
            callback, args = self.jobs.pop(0)
            callback(*args)

    def shutdown(self, **kwargs):
        pass


def structured(messages, route):
    user = next(row for row in reversed(messages) if row['role'] == 'user')
    return json.dumps({'contract_version': 1, 'kind': 'autocode.planner-structured-draft',
        'goal': 'Plan from every answer', 'requirements': [user['text']],
        'milestones': ['Implement and verify'], 'parallelism': [], 'unresolved_questions': [],
        'source_revision': {'requirements_revision': sum(row['role'] == 'user' for row in messages),
                            'logical_turn_id': user['logical_turn_id']},
        'attribution': {'role': 'planner', 'model': route['model'], 'reasoning_effort': route['reasoning_effort']},
        'freshness': {'state': 'fresh', 'updated_at': '2026-10-04T00:00:00+00:00'}})


class FakePlannerTransport:
    """Records each Planner launch and the largest number running at once."""
    autocode_dispatch_aware = True

    def __init__(self):
        self.calls, self.running, self.most_running = [], 0, 0
        self.during = {}   # requirements revision -> callable run while that draft is in flight
        self.fail = set()  # requirements revisions whose provider call fails

    def __call__(self, messages, route, workdir, *, dispatch_observer, logical_turn_id, requirements_revision):
        dispatch_observer('process_starting', {})
        dispatch_observer('process_started', {'owned': True, 'pid': None})
        self.calls.append(requirements_revision)
        self.running += 1
        self.most_running = max(self.most_running, self.running)
        try:
            hook = self.during.pop(requirements_revision, None)
            if hook:
                hook()
            if requirements_revision in self.fail:
                raise ConversationProviderError('Injected provider failure after launch')
            return structured(messages, route)
        finally:
            self.running -= 1


class DraftCoalescingTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.gatherer_calls = []
        self.planner = FakePlannerTransport()
        worker = patch.object(continuous, 'ThreadPoolExecutor', Queue)
        worker.start(); self.addCleanup(worker.stop)
        self.store = self.open()

    def open(self):
        store = continuous.ContinuousConversationStore(root=self.root, provider=self.gatherer, planner=self.planner)
        self.addCleanup(store.close)
        return store

    def gatherer(self, messages, model, workdir):
        self.gatherer_calls.append(messages[-1]['text'])
        return 'Saved. What else matters?'

    def quick_turns(self, conversation_id, texts):
        """Send turns while a draft is in flight; each waits only for its Gatherer reply."""
        def send():
            self.store.pool.drain()  # the in-flight turn's own Gatherer reply
            for text in texts:
                self.assertEqual('ready', self.store.get(conversation_id)['status'])
                self.store.send(conversation_id, text)
                self.store.pool.drain()
        return send

    def step(self):
        """Run only the next queued worker, leaving later ones waiting for a pool thread."""
        callback, args = self.store.pool.jobs.pop(0)
        callback(*args)

    def drafts(self, doc):
        return [row for row in doc['plan_drafts'] if row.get('attribution')]

    def causes(self, draft):
        return [source['requirements_revision'] for source in draft['freshness']['source_messages']]

    def receipts(self, conversation_id):
        """Planner delivery receipts exactly as a task handoff carries them."""
        saved = json.loads((self.root / (conversation_id + '.json')).read_text())
        return protocol.validate_handoff(protocol.handoff_from_document(saved))['planner_dispatches']

    def test_quick_turns_make_one_draft_in_flight_plus_one_coalesced_update_covering_all(self):
        answers = ['Use SQLite', 'Export to CSV', 'Dark theme', 'Keyboard shortcuts', 'Offline mode']
        doc = self.store.create('Build a notes app', request_id='create')
        self.planner.during[1] = self.quick_turns(doc['id'], answers)
        self.store.pool.drain()
        result = self.store.get(doc['id'])
        turns = 1 + len(answers)
        self.assertEqual([1, turns], self.planner.calls, 'One draft in flight plus one coalesced update')
        self.assertEqual(1, self.planner.most_running)
        self.assertEqual(['Build a notes app', *answers], self.gatherer_calls, 'The Gatherer still replies to every turn')
        self.assertEqual(turns, sum(row['role'] == 'assistant' for row in result['messages']))
        current = [row for row in result['plan_drafts'] if row['status'] == 'current']
        self.assertEqual(1, len(current))
        self.assertEqual(turns, current[0]['requirements_revision'])
        self.assertEqual('fresh', current[0]['freshness']['state'])
        self.assertEqual(list(range(1, turns + 1)), self.causes(current[0]), 'The final draft covers every turn')
        self.assertEqual(['Build a notes app', *answers],
                         [source['excerpt'] for source in current[0]['freshness']['source_messages']])
        self.assertEqual(cadence.COALESCED_RELEASE, current[0]['freshness']['reason'])
        receipts = self.receipts(doc['id'])
        first = receipts[result['messages'][0]['logical_turn_id']]
        self.assertTrue(first['stale_result_discarded'], 'The in-flight result never overwrites newer answers')
        skipped = [row for row in receipts.values() if cadence.coalesced(row)]
        self.assertEqual(len(answers) - 1, len(skipped))
        self.assertTrue(all(row['state'] == 'SAVED' for row in skipped), 'Superseded coalesced turns never launch')
        self.assertFalse(result['draft_update']['held'])

    def test_waiting_update_is_visible_and_cannot_be_forced_or_retried(self):
        doc = self.store.create('Build a notes app', request_id='create')
        seen = {}
        def during():
            self.quick_turns(doc['id'], ['Use SQLite', 'Export to CSV'])()
            seen['doc'] = self.store.get(doc['id'])
            update = seen['doc']['draft_update']
            with self.assertRaisesRegex(ValueError, 'starts automatically'):
                self.store.refresh_draft(doc['id'], requirements_revision=update['requirements_revision'],
                                         logical_turn_id=update['logical_turn_id'], request_id='force')
            with self.assertRaisesRegex(ValueError, 'starts automatically'):
                self.store.retry(doc['id'])
        self.planner.during[1] = during
        self.store.pool.drain()
        update = seen['doc']['draft_update']
        self.assertEqual({'held': True, 'coalesced': True, 'can_refresh': False, 'answers_since_update': 2,
                          'requirements_revision': 3},
                         {key: update[key] for key in ('held', 'coalesced', 'can_refresh',
                                                       'answers_since_update', 'requirements_revision')})
        self.assertEqual(cadence.COALESCED, seen['doc']['plan_drafts'][-1]['freshness']['reason'])
        self.assertEqual([1, 3], self.planner.calls)

    def test_each_draft_lists_the_turns_that_caused_it(self):
        doc = self.store.create('Build a notes app', request_id='create')
        self.planner.during[1] = self.quick_turns(doc['id'], ['Use SQLite', 'Export to CSV'])
        self.store.pool.drain()
        # With nothing in flight a single answer follows the answer cadence.
        doc = self.store.send(doc['id'], 'Dark theme'); self.store.pool.drain()
        update = self.store.get(doc['id'])['draft_update']
        self.assertEqual((True, False), (update['held'], update['coalesced']))
        self.planner.during[4] = self.quick_turns(doc['id'], ['Keyboard shortcuts', 'Offline mode'])
        self.store.refresh_draft(doc['id'], requirements_revision=4, logical_turn_id=update['logical_turn_id'],
                                 request_id='refresh-4')
        self.store.pool.drain()
        result = self.store.get(doc['id'])
        self.assertEqual([1, 3, 4, 6], self.planner.calls)
        self.assertEqual(1, self.planner.most_running)
        generated = self.drafts(result)
        self.assertEqual([3, 6], [row['requirements_revision'] for row in generated])
        self.assertEqual([[1, 2, 3], [4, 5, 6]], [self.causes(row) for row in generated])
        self.assertEqual([['Build a notes app', 'Use SQLite', 'Export to CSV'],
                          ['Dark theme', 'Keyboard shortcuts', 'Offline mode']],
                         [[source['excerpt'] for source in row['freshness']['source_messages']] for row in generated])
        # The cause list is saved data: it survives the task handoff unchanged.
        transported = protocol.validate_handoff(protocol.handoff_from_document(
            json.loads((self.root / (doc['id'] + '.json')).read_text())))
        self.assertEqual(result['plan_drafts'], transported['plan_drafts'])

    def test_failed_in_flight_draft_still_releases_one_coalesced_update(self):
        doc = self.store.create('Build a notes app', request_id='create')
        self.planner.fail.add(1)
        self.planner.during[1] = self.quick_turns(doc['id'], ['Use SQLite', 'Export to CSV'])
        self.store.pool.drain()
        result = self.store.get(doc['id'])
        self.assertEqual([1, 3], self.planner.calls)
        self.assertEqual('fresh', result['plan_drafts'][-1]['freshness']['state'])
        self.assertEqual([1, 2, 3], self.causes(result['plan_drafts'][-1]))
        self.assertEqual('REPLY_COMMITTED', result['planner_delivery']['state'])

    def test_turn_arriving_before_the_finished_draft_releases_its_follow_up_launches_it(self):
        doc = self.store.create('Build a notes app', request_id='create')
        self.planner.during[1] = self.quick_turns(doc['id'], ['Use SQLite'])
        # The finished draft's lease is free but its release has not run yet.
        with patch.object(self.store, '_release_coalesced_draft', return_value=None):
            self.store.pool.drain()
        self.assertEqual([1], self.planner.calls)
        self.store.send(doc['id'], 'Export to CSV')  # the answer cadence alone would hold this turn
        self.store.pool.drain()
        self.assertIsNone(self.store._release_coalesced_draft(doc['id']), 'The late release finds nothing left')
        result = self.store.get(doc['id'])
        self.assertEqual([1, 3], self.planner.calls)
        self.assertEqual([1, 2, 3], self.causes(result['plan_drafts'][-1]))
        self.assertEqual('current', result['plan_drafts'][-1]['status'])
        self.assertFalse(result['draft_update']['held'])

    def test_restart_releases_coalesced_update_without_replaying_the_lost_draft(self):
        doc = self.store.create('Build a notes app', request_id='create')
        path, snapshot = self.root / (doc['id'] + '.json'), {}
        def during():
            self.quick_turns(doc['id'], ['Use SQLite', 'Export to CSV'])()
            snapshot['text'] = path.read_text()  # the saved state if this process died now
        self.planner.during[1] = during
        self.store.pool.drain()
        self.store.close()
        path.write_text(snapshot['text'])
        self.planner.calls.clear()
        self.store = self.open()
        self.store.pool.drain()
        result = self.store.get(doc['id'])
        self.assertEqual([3], self.planner.calls, 'Only the coalesced update launches; the lost draft is not replayed')
        first = self.receipts(doc['id'])[result['messages'][0]['logical_turn_id']]
        self.assertEqual('UNCERTAIN', first['state'])
        self.assertEqual([1, 2, 3], self.causes(result['plan_drafts'][-1]))
        self.assertEqual('current', result['plan_drafts'][-1]['status'])

    def test_turn_sent_while_the_released_update_waits_for_a_worker_joins_it(self):
        # The shared pool can hold the released follow-up in a backlog before
        # its worker takes the lease; a turn sent then must not drop it.
        doc = self.store.create('Build a notes app', request_id='create')
        self.planner.during[1] = self.quick_turns(doc['id'], ['Use SQLite', 'Export to CSV'])
        self.step()  # draft 1 ends and releases revision 3, whose worker is still queued
        self.assertEqual([1], self.planner.calls)
        self.assertFalse(self.store.get(doc['id'])['draft_update']['held'], 'Revision 3 was released')
        self.store.send(doc['id'], 'Dark theme')
        self.assertTrue(self.store.get(doc['id'])['draft_update']['coalesced'], 'The queued draft counts as in flight')
        self.store.pool.drain()
        result = self.store.get(doc['id'])
        self.assertEqual([1, 4], self.planner.calls, 'Revision 3 is stale before launch; its end releases revision 4')
        self.assertEqual(('current', 4), (result['plan_drafts'][-1]['status'], result['plan_drafts'][-1]['requirements_revision']))
        self.assertEqual([1, 2, 3, 4], self.causes(result['plan_drafts'][-1]))
        self.assertFalse(result['draft_update']['held'])

    def test_turn_sent_while_an_explicit_update_waits_for_a_worker_joins_it(self):
        doc = self.store.create('Build a notes app', request_id='create')
        self.store.pool.drain()
        self.store.send(doc['id'], 'Use SQLite'); self.store.pool.drain()
        target = self.store.get(doc['id'])['draft_update']
        self.store.refresh_draft(doc['id'], requirements_revision=2, logical_turn_id=target['logical_turn_id'],
                                 request_id='refresh-2')  # queued, not yet running
        self.store.send(doc['id'], 'Export to CSV')
        callback, args = self.store.pool.jobs.pop()  # only the new turn's Gatherer reply runs
        callback(*args)
        latest = self.store.get(doc['id'])['draft_update']
        self.assertEqual((True, True, False), (latest['held'], latest['coalesced'], latest['can_refresh']))
        with self.assertRaisesRegex(ValueError, 'starts automatically'):
            self.store.refresh_draft(doc['id'], requirements_revision=3, logical_turn_id=latest['logical_turn_id'],
                                     request_id='refresh-3')
        self.store.pool.drain()  # revision 2 is stale before launch; its end releases revision 3
        result = self.store.get(doc['id'])
        self.assertEqual([1, 3], self.planner.calls)
        self.assertEqual([2, 3], self.causes(result['plan_drafts'][-1]))
        self.assertEqual('current', result['plan_drafts'][-1]['status'])

    def test_explicit_refresh_joins_a_draft_running_in_another_dashboard(self):
        doc = self.store.create('Build a notes app', request_id='create')
        self.store.pool.drain()
        self.store.send(doc['id'], 'Use SQLite'); self.store.pool.drain()
        other = self.open()  # a second dashboard process on the same conversations
        target = other.get(doc['id'])['draft_update']
        other.refresh_draft(doc['id'], requirements_revision=2, logical_turn_id=target['logical_turn_id'],
                            request_id='refresh-2')
        self.store.send(doc['id'], 'Export to CSV')  # this dashboard cannot see the other one's queue
        self.store.pool.drain()
        latest = self.store.get(doc['id'])['draft_update']
        self.assertEqual((True, False), (latest['held'], latest['coalesced']))
        # The other dashboard's worker is now running revision 2's draft.
        with self.store._planner_lease(doc['id'], target['logical_turn_id']) as acquired:
            self.assertTrue(acquired)
            joined = self.store.refresh_draft(doc['id'], requirements_revision=3,
                                              logical_turn_id=latest['logical_turn_id'], request_id='refresh-3')
        self.assertTrue(joined['draft_update']['coalesced'])
        self.assertEqual([1], self.planner.calls)
        other.pool.drain()  # revision 2 is stale before launch; its end releases revision 3 there
        result = self.store.get(doc['id'])
        self.assertEqual([1, 3], self.planner.calls)
        self.assertEqual([2, 3], self.causes(result['plan_drafts'][-1]))

    def test_a_stalled_draft_stops_blocking_an_explicit_update(self):
        # The Planner provider call has no timeout; draft 1 never returns here
        # while five answers arrive.
        answers = ['Use SQLite', 'Export to CSV', 'Dark theme', 'Keyboard shortcuts', 'Offline mode']
        doc = self.store.create('Build a notes app', request_id='create')
        seen = {}
        def hung():
            self.quick_turns(doc['id'], answers)()
            seen['waiting'] = update = self.store.get(doc['id'])['draft_update']
            with self.assertRaisesRegex(ValueError, 'starts automatically'):
                self.store.refresh_draft(doc['id'], requirements_revision=6,
                                         logical_turn_id=update['logical_turn_id'], request_id='too-soon')
            later = datetime.now(UTC) + timedelta(seconds=cadence.STALLED_AFTER_SECONDS + 1)
            with patch.object(cadence, '_clock', return_value=later):
                seen['stalled'] = update = self.store.get(doc['id'])['draft_update']
                with self.assertRaisesRegex(ValueError, 'Use Update draft in chat'):
                    self.store.retry(doc['id'])
                self.store.refresh_draft(doc['id'], requirements_revision=6,
                                         logical_turn_id=update['logical_turn_id'], request_id='unstick')
            seen['calls'] = list(self.planner.calls)
        self.planner.during[1] = hung
        self.store.pool.drain()
        keys = ('coalesced', 'stalled', 'can_refresh', 'answers_since_update')
        self.assertEqual((True, False, False, 5), tuple(seen['waiting'][key] for key in keys))
        self.assertEqual((True, True, True, 5), tuple(seen['stalled'][key] for key in keys))
        self.assertEqual([1], seen['calls'], 'The explicit update is queued while draft 1 still hangs')
        result = self.store.get(doc['id'])
        self.assertEqual([1, 6], self.planner.calls)
        self.assertEqual(('current', 6), (result['plan_drafts'][-1]['status'], result['plan_drafts'][-1]['requirements_revision']))
        self.assertEqual([1, 2, 3, 4, 5, 6], self.causes(result['plan_drafts'][-1]))
        first = self.receipts(doc['id'])[result['messages'][0]['logical_turn_id']]
        self.assertTrue(first['stale_result_discarded'], 'A late result from the stalled draft is discarded')

    def test_update_left_by_a_dashboard_that_died_mid_draft_is_released_when_read(self):
        other = self.open()  # a second dashboard process on the same conversations
        doc = other.create('Build a notes app', request_id='create')

        class Died(BaseException):
            pass

        def dies():
            other.pool.drain()  # its own Gatherer reply
            self.quick_turns(doc['id'], ['Use SQLite', 'Export to CSV'])()  # sent through this dashboard
            other.close(wait=False)  # killed mid-draft: it never finishes draft 1
            raise Died()             # or releases the update coalesced behind it
        self.planner.during[1] = dies
        with self.assertRaises(Died):
            other.pool.drain()
        self.assertEqual([1], self.planner.calls)
        result = self.store.get(doc['id'])  # its lease is free: reading releases revision 3
        self.assertFalse(result['draft_update']['held'])
        self.store.pool.drain()
        result = self.store.get(doc['id'])
        self.assertEqual([1, 3], self.planner.calls)
        self.assertEqual(('current', 3), (result['plan_drafts'][-1]['status'], result['plan_drafts'][-1]['requirements_revision']))
        self.assertEqual([1, 2, 3], self.causes(result['plan_drafts'][-1]))


class CoalescingPolicyTests(unittest.TestCase):
    def doc(self, *dispatches, **fields):
        records = {}
        for index, extra in enumerate(dispatches, 1):
            records['turn-%d' % index] = {'logical_turn_id': 'turn-%d' % index, 'requirements_revision': index,
                                          'state': 'SAVED', **extra}
        revisions = [{'revision': index, 'source': {'logical_turn_id': 'turn-%d' % index}}
                     for index in range(1, len(dispatches) + 1)]
        return {'requirements': {'revisions': revisions}, '_planner_dispatches': records, **fields}

    def test_launch_coalesces_behind_any_draft_in_flight_even_when_explicit_or_scheduled(self):
        batched = {'cadence_hold': True, 'cadence_reason': 'batching_answers'}
        doc = self.doc({'state': 'REPLY_COMMITTED'}, batched)
        self.assertEqual(cadence.HOLD, cadence.launch(doc, 3, set()))
        self.assertEqual(cadence.DISPATCH, cadence.launch(doc, 3, set(), explicit=True))
        self.assertEqual(cadence.DISPATCH, cadence.launch(doc, 4, set()))
        for explicit in (False, True):
            self.assertEqual(cadence.COALESCE, cadence.launch(doc, 4, {'turn-1'}, explicit=explicit))

    def test_a_turn_arriving_after_the_running_draft_ended_is_the_owed_follow_up(self):
        behind = {'cadence_hold': True, 'cadence_reason': cadence.COALESCED}
        doc = self.doc({'state': 'RESULT_CAPTURED', 'stale_result_discarded': True}, behind)
        self.assertTrue(cadence.owed(doc))
        self.assertEqual(cadence.DISPATCH, cadence.launch(doc, 3, set()), 'The cadence alone would hold revision 3')
        self.assertEqual(cadence.COALESCE, cadence.launch(doc, 3, {'turn-1'}))
        self.assertFalse(cadence.owed(self.doc({}, behind, {'state': 'DISPATCH_PREPARED'})), 'A later launch covered it')

    def test_release_launches_only_the_newest_coalesced_turn_when_nothing_runs(self):
        behind = {'cadence_hold': True, 'cadence_reason': cadence.COALESCED}
        doc = self.doc({'state': 'UNCERTAIN'}, behind, behind)
        self.assertEqual('turn-3', cadence.release(doc, set()))
        self.assertIsNone(cadence.release(doc, {'turn-1'}))
        for fields in ({'archived_at': 'now'}, {'attachment': {'status': 'linked'}}):
            self.assertIsNone(cadence.release({**doc, **fields}, set()))
        batched = self.doc({}, behind, {'cadence_hold': True, 'cadence_reason': 'batching_answers'})
        self.assertIsNone(cadence.release(batched, set()), 'A cadence-held newest turn waits for its own trigger')
        released = self.doc({}, behind, {**behind, 'state': 'DISPATCH_PREPARED'})
        self.assertIsNone(cadence.release(released, set()), 'Released once')

    def test_only_a_stalled_draft_in_flight_lets_an_explicit_update_dispatch(self):
        now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        recent = (now - timedelta(seconds=cadence.STALLED_AFTER_SECONDS - 1)).isoformat()
        old = (now - timedelta(seconds=cadence.STALLED_AFTER_SECONDS)).isoformat()
        behind = {'cadence_hold': True, 'cadence_reason': cadence.COALESCED}
        for updated, explicit, expected in ((recent, False, cadence.COALESCE), (recent, True, cadence.COALESCE),
                                            (old, False, cadence.COALESCE), (old, True, cadence.DISPATCH)):
            doc = self.doc({'state': 'PROCESS_STARTED', 'updated_at': updated}, behind)
            self.assertEqual(expected, cadence.launch(doc, 2, {'turn-1'}, explicit=explicit, now=now),
                             (updated, explicit))
        self.assertFalse(cadence.stalled({'updated_at': 'not a time'}, now), 'Unreadable progress never counts as stalled')

    def test_public_offers_an_update_only_when_nothing_live_runs_ahead_of_a_coalesced_one(self):
        now = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
        behind = {'cadence_hold': True, 'cadence_reason': cadence.COALESCED}
        def view(running, **fields):
            return cadence.public({**self.doc(running, behind), 'status': 'ready', **fields}, now)
        live = view({'state': 'PROCESS_STARTED', 'updated_at': now.isoformat()})
        self.assertEqual((True, False, False), (live['coalesced'], live['stalled'], live['can_refresh']))
        hung = view({'state': 'PROCESS_STARTED', 'updated_at': (now - timedelta(hours=1)).isoformat()})
        self.assertEqual((True, True, True), (hung['coalesced'], hung['stalled'], hung['can_refresh']))
        settled = view({'state': 'REPLY_COMMITTED', 'updated_at': now.isoformat()})
        self.assertTrue(settled['can_refresh'], 'Nothing is recorded as running ahead of it')
        self.assertFalse(view({'state': 'UNCERTAIN', 'updated_at': now.isoformat()}, archived_at='now')['can_refresh'])

    def test_only_authorized_unsettled_drafts_can_be_in_flight(self):
        doc = self.doc({'state': 'PROCESS_STARTED'}, {'state': 'REPLY_COMMITTED'},
                       {'state': 'RESULT_CAPTURED', 'stale_result_discarded': True},
                       {'state': 'RESULT_CAPTURED', 'structured_rejected': True},
                       {'cadence_hold': True, 'cadence_reason': cadence.COALESCED}, {'state': 'DISPATCH_PREPARED'})
        self.assertEqual(['turn-1', 'turn-6'], cadence.in_flight_candidates(doc))


if __name__ == '__main__':
    unittest.main()
