"""Recover saved conversation delivery without repeating an ambiguous provider call."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
import threading
import uuid

try:
    from .. import autocode_conversation as protocol
except ImportError:
    import autocode_conversation as protocol


try:
    from . import conversation_draft_cadence as cadence
    from .conversation_draft_cadence import coalesced, held
except ImportError:
    import conversation_draft_cadence as cadence
    from conversation_draft_cadence import coalesced, held


class RecoveryMixin:
    @contextmanager
    def _planner_lease(self, conversation_id, logical_turn, *, wait=False):
        self._path(conversation_id)
        key = hashlib.sha256(str(logical_turn).encode()).hexdigest()
        path = self.root / '.locks' / ('planner-' + conversation_id + '-' + key)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        acquired = False
        try:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | (0 if wait else fcntl.LOCK_NB))
                acquired = True
            except BlockingIOError:
                pass
            yield acquired
        finally:
            os.close(fd)

    def _planner_reply(self, conversation_id, turn_id, logical_turn=None):
        # A competing owner may still be before its external dispatch boundary.
        # Wait for its lease rather than treating lock contention as launch proof.
        with self._planner_lease(conversation_id, logical_turn, wait=True) as acquired:
            if acquired:
                self._planner_reply_owned(conversation_id, turn_id, logical_turn)

    def archive(self, conversation_id, action):
        result = super().archive(conversation_id, action)
        if action == 'restore':
            self._recover_saved(only=conversation_id)
            return self.get(conversation_id)
        return result

    def _recover_saved(self, *, only=None):
        pending, behind = [], []
        with self._guard():
            for path in self.root.glob('*.json'):
                if len(path.stem) != 32 or only is not None and path.stem != only:
                    continue
                try:
                    doc = self._load(path.stem)
                except (ValueError, OSError):
                    continue
                if doc.get('attachment') or doc.get('archived_at'):
                    continue
                if any(coalesced(row) for row in doc.get('_planner_dispatches', {}).values()):
                    behind.append(doc['id'])
                # The Gatherer can already be ready while the independent
                # Planner is still in flight. Reconcile their receipts separately.
                for turn, saved in list(doc.get('_planner_dispatches', {}).items()):
                    if held(saved) or saved.get('state') == 'REPLY_COMMITTED' or saved.get('stale_result_discarded') or saved.get('structured_rejected'):
                        continue
                    with self._planner_lease(doc['id'], turn) as acquired:
                        if not acquired:
                            continue
                        phase = saved.get('state')
                        if phase == 'RESULT_CAPTURED':
                            pending.append((doc['id'], turn))
                        elif phase in ('SAVED', 'SAFE_NOT_DISPATCHED', 'DISPATCH_PREPARED'):
                            pending.append((doc['id'], turn))
                        else:
                            if phase != 'UNCERTAIN':
                                doc['_planner_dispatches'][turn] = protocol.transition(
                                    saved, 'UNCERTAIN', reason='Server restarted after provider dispatch; no replay authorized')
                            self._mark_planner_draft_failed(doc, turn, {
                                'stage': 'planner_recovery', 'reason': 'delivery_uncertain',
                                'message': 'The Planner delivery could not be confirmed. The previous draft and delivery evidence are preserved.'})
                try:
                    lease = self._lease(doc['id'])
                except ValueError:
                    self._save(doc)
                    continue
                try:
                    turn = doc.get('_active_logical_turn')
                    dispatch = doc.get('_dispatches', {}).get(turn, {})
                    if doc.get('status') == 'thinking':
                        if dispatch.get('state') in ('RESULT_CAPTURED', 'REPLY_COMMITTED'):
                            try:
                                self._commit_result(doc, turn, dispatch)
                            except (ValueError, RuntimeError):
                                doc.update(status='uncertain', _active_turn=None,
                                           error='Saved reply failed integrity validation. Its delivery evidence is retained; no provider was retried.')
                                self._pending_message(doc)['status'] = 'uncertain'
                        else:
                            self._record_failure(doc, turn, 'not_dispatched' if dispatch.get('state') in ('SAVED', 'SAFE_NOT_DISPATCHED', 'DISPATCH_PREPARED') else None,
                                                 'The conversation was interrupted. Its saved delivery evidence is retained.')
                    self._save(doc)
                finally:
                    lease.close()
        for ident, turn in pending:
            self._submit_planner(ident, None, turn)
        # A draft coalesced behind one that never finished in a live process
        # launches once nothing is in flight; a recovered draft still running
        # releases it when it finishes instead.
        for ident in behind:
            self._release_coalesced_draft(ident)

    def retry(self, conversation_id):
        """Retry a confirmed pre-dispatch failure, or commit retained output."""
        with self._guard():
            self._ensure_open()
            doc = self._load(conversation_id)
            self.require_visible(doc)
            if doc.get('attachment'):
                raise ValueError('Continue in the attached task conversation.')
            turn = self._pending_message(doc).get('logical_turn_id')
            gatherer = doc.get('_dispatches', {}).get(turn, {})
            planner = doc.get('_planner_dispatches', {}).get(turn, {})
            safe = ('SAVED', 'SAFE_NOT_DISPATCHED', 'DISPATCH_PREPARED', 'RESULT_CAPTURED')
            if doc.get('status') == 'ready' and coalesced(planner):
                if cadence.launch(doc, planner.get('requirements_revision'), self._planner_turns_in_flight(doc),
                                  explicit=True) == cadence.COALESCE:
                    raise ValueError('This draft update starts automatically when the Planner finishes the draft in progress.')
                raise ValueError('The draft this update waits for stopped reporting progress. '
                                 'Use Update draft in chat to refresh it now.')
            if doc.get('status') == 'ready' and held(planner):
                raise ValueError('This draft is batching answers. Use Update draft in chat to refresh it now.')
            if doc.get('status') == 'ready' and planner.get('state') in safe:
                self._authorized_dispatch_routes(doc)
                for row in doc.get('plan_drafts', []):
                    if row.get('logical_turn_id') == turn and row.get('status') == 'failed':
                        row['status'] = 'pending'
                        row['freshness']['state'] = 'pending'
                self._save(doc)
                self._submit_planner(conversation_id, None, turn)
                return self._public(doc)
            if doc.get('status') == 'thinking':
                return self._public(doc)
            if gatherer.get('state') not in safe:
                raise ValueError('Delivery is uncertain. A provider receipt is required before retrying this saved message.')
            if gatherer.get('state') == 'RESULT_CAPTURED':
                try:
                    self._commit_result(doc, turn, gatherer)
                except (ValueError, RuntimeError) as error:
                    raise ValueError('Saved reply failed integrity validation; no provider was retried.') from error
                return self._public(doc)
            self._authorized_dispatch_routes(doc)
            doc.update(status='thinking', error=None, _active_turn=uuid.uuid4().hex,
                       _active_logical_turn=turn)
            self._pending_message(doc)['status'] = 'saved'
            return self._start(doc)
