"""Explicit chat request to refresh one held, revision-bound live draft."""
from copy import deepcopy

try:
    from .. import autocode_conversation as protocol
except ImportError:
    import autocode_conversation as protocol
try:
    from . import conversation_draft_cadence as cadence
    from .dashboard_conversations import _request_id, _now
except ImportError:
    import conversation_draft_cadence as cadence
    from dashboard_conversations import _request_id, _now


class DraftRefreshMixin:
    def refresh_draft(self, conversation_id, *, requirements_revision, logical_turn_id, request_id=None):
        request_id = _request_id(request_id)
        if type(requirements_revision) is not int or requirements_revision < 1 or not isinstance(logical_turn_id, str):
            raise ValueError('A displayed draft revision and turn are required.')
        requested = {'requirements_revision': requirements_revision, 'logical_turn_id': logical_turn_id}
        with self._guard():
            self._ensure_open()
            doc = self._load(conversation_id)
            self.require_visible(doc)
            previous = doc.get('_draft_refresh_requests', {}).get(request_id)
            if previous:
                if previous['target'] != requested:
                    raise ValueError('That request ID was already used for a different draft revision.')
                return self._public(doc)
            current = cadence.public(doc) or {}
            if any(current.get(key) != value for key, value in requested.items()):
                raise ValueError('The conversation changed. Inspect the latest draft before updating it.')
            if not current.get('can_refresh'):
                raise ValueError('This draft is not waiting for a refresh. Its saved delivery is preserved.')
            self._authorized_dispatch_routes(doc)
            dispatch = doc['_planner_dispatches'][logical_turn_id]
            doc['_planner_dispatches'][logical_turn_id] = protocol.transition(
                dispatch, 'DISPATCH_PREPARED', cadence_hold=False, cadence_reason='explicit_chat_refresh')
            for draft in doc.get('plan_drafts', []):
                if draft.get('logical_turn_id') == logical_turn_id and draft.get('status') == 'pending':
                    draft['freshness'].update(reason='explicit_chat_refresh', updated_at=_now())
            doc.setdefault('_draft_refresh_requests', {})[request_id] = {'target': deepcopy(requested), 'at': _now()}
            self._save(doc)  # a crash from here is a normal recoverable pre-dispatch intent
            try:
                self.pool.submit(self._planner_reply, conversation_id, None, logical_turn_id)
            except RuntimeError:
                doc['_planner_dispatches'][logical_turn_id] = protocol.transition(
                    doc['_planner_dispatches'][logical_turn_id], 'SAFE_NOT_DISPATCHED',
                    reason='Draft worker was unavailable before provider launch')
                self._mark_planner_draft_failed(doc, logical_turn_id, {
                    'stage': 'planner_dispatch', 'reason': 'worker_unavailable',
                    'message': 'The draft update is saved. Retry its confirmed pre-dispatch failure from chat.'})
                self._save(doc)
            return self._public(doc)
