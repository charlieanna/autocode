"""Keep at most one live Planner draft in flight per conversation.

The decision is the pure policy in ``conversation_draft_cadence`` (``launch``
and ``release``); this mixin observes which drafts are queued or running and
launches the one coalesced follow-up when the draft ahead of it ends.
"""
try:
    from .. import autocode_conversation as protocol
except ImportError:
    import autocode_conversation as protocol
try:
    from . import conversation_draft_cadence as cadence
    from .dashboard_conversations import _now
except ImportError:
    import conversation_draft_cadence as cadence
    from dashboard_conversations import _now


class DraftCoalescingMixin:
    def _planner_turns_in_flight(self, doc):
        """Logical turns whose Planner draft is queued here or holds its delivery lease.

        A worker submitted to this process's pool counts from submission until
        it ends, because the shared pool can hold it in a backlog before it
        takes the lease. The lease is an OS file lock held for a worker's whole
        run, in any dashboard process, and released when the process exits, so
        a crashed worker never looks in flight. Callers hold the store guard.
        """
        with self._lock:
            busy = {turn for ident, turn in self._planner_queued if ident == doc['id']}
        for turn in cadence.in_flight_candidates(doc):
            if turn in busy:
                continue
            with self._planner_lease(doc['id'], turn) as acquired:
                if not acquired:
                    busy.add(turn)
        return busy

    def _submit_planner(self, conversation_id, turn_id, logical_turn):
        """Queue one Planner worker; it counts as in flight until ``_planner_reply`` ends."""
        key = (conversation_id, logical_turn)
        with self._lock:
            self._planner_queued[key] = self._planner_queued.get(key, 0) + 1
        try:
            self.pool.submit(self._planner_reply, conversation_id, turn_id, logical_turn)
        except BaseException:
            self._planner_dequeued(key)
            raise

    def _planner_dequeued(self, key):
        with self._lock:
            count = self._planner_queued.get(key, 0) - 1
            if count > 0:
                self._planner_queued[key] = count
            else:
                self._planner_queued.pop(key, None)

    def _planner_reply(self, conversation_id, turn_id, logical_turn=None):
        try:
            super()._planner_reply(conversation_id, turn_id, logical_turn)
        finally:
            self._planner_dequeued((conversation_id, logical_turn))
            # This draft finished, failed or was superseded and is no longer in
            # flight: launch the one draft coalesced behind it, if any.
            self._release_coalesced_draft(conversation_id)

    def get(self, conversation_id):
        """Return the conversation, first releasing a coalesced update nothing is running ahead of.

        The draft ahead of it normally releases it when it ends. A draft that
        ended in another dashboard process, or died with it, never does, so
        reading the conversation releases it once no draft is in flight.
        """
        public = super().get(conversation_id)
        if (public.get('draft_update') or {}).get('coalesced') and self._release_coalesced_draft(conversation_id):
            return super().get(conversation_id)
        return public

    def _launch_deferred_draft(self, doc, turn, reason):
        """Launch a saved, held draft intent now and save the document.

        The intent was authorized when its human turn was saved; this is its
        deferred launch, like restart recovery of a prepared dispatch. Callers
        hold the store guard.
        """
        doc['_planner_dispatches'][turn] = protocol.transition(
            doc['_planner_dispatches'][turn], 'DISPATCH_PREPARED', cadence_hold=False, cadence_reason=reason)
        for draft in doc.get('plan_drafts', []):
            if draft.get('logical_turn_id') == turn and draft.get('status') == 'pending':
                draft['freshness'].update(reason=reason, updated_at=_now())
        self._save(doc)  # a crash from here is a normal recoverable pre-dispatch intent
        try:
            self._submit_planner(doc['id'], None, turn)
        except RuntimeError:
            doc['_planner_dispatches'][turn] = protocol.transition(
                doc['_planner_dispatches'][turn], 'SAFE_NOT_DISPATCHED',
                reason='Draft worker was unavailable before provider launch')
            self._mark_planner_draft_failed(doc, turn, {
                'stage': 'planner_dispatch', 'reason': 'worker_unavailable',
                'message': 'The draft update is saved. Retry its confirmed pre-dispatch failure from chat.'})
            self._save(doc)

    def _release_coalesced_draft(self, conversation_id):
        """Launch the one draft coalesced behind a finished one; return its turn or None."""
        with self._guard():
            if self._closed:
                return None  # restart recovery releases it
            try:
                doc = self._load(conversation_id)
            except (ValueError, OSError):
                return None
            if cadence.release(doc, set()) is None:
                return None  # nothing waits; skip probing the leases
            turn = cadence.release(doc, self._planner_turns_in_flight(doc))
            if turn is not None:
                self._launch_deferred_draft(doc, turn, cadence.COALESCED_RELEASE)
            return turn
