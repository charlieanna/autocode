"""Explicit chat request to refresh one held, revision-bound live draft."""
from copy import deepcopy

try:
    from .. import autocode_conversation as protocol
except ImportError:
    import autocode_conversation as protocol
try:
    from . import conversation_draft_cadence as cadence
    from .dashboard_conversations import _now, _request_id
except ImportError:
    import conversation_draft_cadence as cadence
    from dashboard_conversations import _now, _request_id


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
            if not cadence.refreshable(doc):
                raise ValueError('This draft is not waiting for a refresh. Its saved delivery is preserved.')
            self._authorized_dispatch_routes(doc)
            dispatch = doc['_planner_dispatches'][logical_turn_id]
            if cadence.launch(doc, requirements_revision, self._planner_turns_in_flight(doc),
                              explicit=True) == cadence.COALESCE:
                if cadence.coalesced(dispatch):
                    raise ValueError('This draft update starts automatically when the Planner finishes the draft in progress.')
                # Another draft is running: this request joins the one update
                # released when it finishes instead of launching a second one.
                doc['_planner_dispatches'][logical_turn_id] = protocol.transition(
                    dispatch, dispatch['state'], cadence_reason=cadence.COALESCED)
                for draft in doc.get('plan_drafts', []):
                    if draft.get('logical_turn_id') == logical_turn_id and draft.get('status') == 'pending':
                        draft['freshness'].update(reason=cadence.COALESCED, updated_at=_now())
                doc.setdefault('_draft_refresh_requests', {})[request_id] = {'target': deepcopy(requested), 'at': _now()}
                self._save(doc)
                return self._public(doc)
            # Nothing is in flight, or the draft ahead of a coalesced update has
            # stalled: launch this revision's draft now.
            doc.setdefault('_draft_refresh_requests', {})[request_id] = {'target': deepcopy(requested), 'at': _now()}
            self._launch_deferred_draft(doc, logical_turn_id, 'explicit_chat_refresh')
            return self._public(doc)
