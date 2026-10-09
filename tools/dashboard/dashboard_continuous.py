"""Continuous requirements and structured drafts; legacy intake stays compatible."""
import contextlib
import hashlib
import json
import os
import threading
import uuid
import weakref
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

try:
    from .. import autocode_conversation as conversation_protocol
    from ..autocode_role_names import role_label
except ImportError:
    import autocode_conversation as conversation_protocol
    from autocode_role_names import role_label


try:
    from . import conversation_draft_cadence as draft_cadence
    from . import dashboard_conversations as legacy
    from . import planner_dispatch
    from .conversation_draft_coalescing import DraftCoalescingMixin
    from .conversation_draft_refresh import DraftRefreshMixin
    from .conversation_recovery import RecoveryMixin
    from .conversation_transport import ConversationProviderError, _prompt, opencode_provider
except ImportError:
    import conversation_draft_cadence as draft_cadence
    import dashboard_conversations as legacy
    import planner_dispatch
    from conversation_draft_coalescing import DraftCoalescingMixin
    from conversation_draft_refresh import DraftRefreshMixin
    from conversation_recovery import RecoveryMixin
    from conversation_transport import ConversationProviderError, _prompt, opencode_provider

_now, _request_id, _text = legacy._now, legacy._request_id, legacy._text
_ID, _MODEL = legacy._ID, legacy._MODEL
MAX_CONTEXT_CHARS, MAX_MESSAGES, MAX_REPLY_CHARS = legacy.MAX_CONTEXT_CHARS, legacy.MAX_MESSAGES, legacy.MAX_REPLY_CHARS
DEFAULT_GLM_MODEL = 'openai/gpt-6-sol'

class PlannerDispatchFailure(ValueError):
    """An internal structured-Planner failure carrying retained evidence metadata."""

    def __init__(self, message, *, stage='planner', outcome=None, evidence=None):
        super().__init__(message)
        self.stage = stage
        self.outcome = outcome
        self.evidence = evidence


def _models(value):
    if value is None:
        value = {}
    if not isinstance(value, dict) or len(value) > 12:
        raise ValueError('Models must be an object containing model names.')
    if any(not isinstance(k, str) or not isinstance(v, str) or len(k) > 80 or len(v) > 180
           for k, v in value.items()):
        raise ValueError('Model names must be short strings.')
    result = {'glm_model': DEFAULT_GLM_MODEL, **value}
    if not _MODEL.fullmatch(result['glm_model']):
        raise ValueError('The conversation model must be an OpenCode provider/model identifier.')
    return result


def _seed_goal(doc):
    """Seed a draft goal from the conversation until the Planner structures one."""
    for row in reversed(doc.get('messages', [])):
        if row.get('role') == 'user' and isinstance(row.get('text'), str) and row['text'].strip():
            return ' '.join(row['text'].split())[:300]
    return 'Describe the goal of this project.'


def _current_plan_draft(doc):
    return next((row for row in reversed(doc.get('plan_drafts', []))
                 if row.get('status') == 'current'), None)


def _record_requirements_update(doc, message):
    """One requirements-state revision per substantive human turn."""
    requirements = doc.setdefault('requirements', {'revisions': [], 'provenance': []})
    revisions = requirements.setdefault('revisions', [])
    previous = revisions[-1] if revisions else {}
    revision = previous.get('revision', 0) + 1
    requirements.setdefault('provenance', []).append({
        'revision': revision, 'source': 'user_message', 'message_id': message['id'],
        'logical_turn_id': message['logical_turn_id'], 'recorded_at': _now()})
    revisions.append({
        'revision': revision, 'created_at': _now(),
        'source': {'kind': 'user_message', 'message_id': message['id'],
                   'logical_turn_id': message['logical_turn_id']},
        'goal': previous.get('goal') or _seed_goal(doc),
        'requirements': list(previous.get('requirements', [])),
        'outstanding_questions': list(previous.get('outstanding_questions', [])),
        'status': 'updated'})
    return revisions[-1]


def _record_plan_draft_revision(doc, message, revision):
    """Open the pending plan-draft revision a substantive human turn prompts.

    The pending record is bound to the exact (requirements revision, logical
    turn) before any dispatch runs, and older still-pending results are
    invalidated: newer human input supersedes them (AC5).  Freshness is never
    granted here; only a validated structured Planner result commits one (AC2).
    """
    drafts = doc.setdefault('plan_drafts', [])
    previous = _current_plan_draft(doc) or (drafts[-1] if drafts else {})
    source_messages = draft_cadence.sources(doc, (_current_plan_draft(doc) or {}).get('requirements_revision', 0))
    for row in drafts:
        if row.get('status') == 'current':
            # Retain the last usable structure, but it no longer answers the
            # newest human requirements revision until a Planner validates it.
            row['freshness'] = {**row.get('freshness', {}), 'state': 'stale', 'updated_at': _now(),
                                'reason': 'newer_requirements_input'}
        if row.get('status') == 'pending':
            row['status'] = 'superseded'
            row['freshness'] = {**row.get('freshness', {}), 'state': 'stale', 'updated_at': _now(),
                                'reason': 'invalidated_by_newer_requirements_input',
                                'source_logical_turn_id': row.get('logical_turn_id')}
    entry = {
        'id': 'plan-' + uuid.uuid4().hex,
        'revision': (drafts[-1]['revision'] + 1) if drafts else 1,
        'created_at': _now(), 'status': 'pending',
        'goal': previous.get('goal') or _seed_goal(doc),
        'requirements': list(previous.get('requirements', [])),
        'milestones': deepcopy(previous.get('milestones', [])),
        'parallelism': list(previous.get('parallelism', [])),
        'outstanding_questions': list(previous.get('outstanding_questions', [])),
        'reply_preview': None,
        'freshness': {'state': 'pending', 'updated_at': _now(),
                      'source_message_id': message['id'], 'source_messages': source_messages,
                      'source_logical_turn_id': message['logical_turn_id']},
        'requirements_revision': revision,
        'logical_turn_id': message['logical_turn_id'],
    }
    drafts.append(entry)
    return entry


def _configured_routes(models):
    """Preserve the configured Gatherer and add the default independent Planner."""
    routes = {'requirements_gatherer': {
        'engine': 'opencode', 'provider': 'opencode', 'model': models['glm_model'],
        'reasoning_effort': models.get('glm_reasoning_effort') or 'low'}}
    routes.update(planner_dispatch.conversation_planner_routes())
    return planner_dispatch.enforce_conversation_routes(routes)


PROJECT_INSTRUCTIONS_FILE = 'AGENTS.md'
MAX_PROJECT_INSTRUCTIONS_CHARS = 12_000


try:
    from .dashboard_project_scope import ScopeConfirmationMixin, scope_problem
    from .dashboard_project_scope import creation_scope as _creation_project_scope
except ImportError:
    from dashboard_project_scope import ScopeConfirmationMixin, scope_problem
    from dashboard_project_scope import creation_scope as _creation_project_scope


def _project_scope_context(doc):
    """Validated creation-time project scope for provider dispatch.

    Returns (workspace Path, leading provider message) for a saved scoped
    conversation, or (None, None) for scratch conversations. The saved scope
    was validated against the dashboard's Git projects at creation; it is
    re-resolved here so a removed project fails safely instead of silently
    planning outside the repository. Repository instructions come from the
    project's AGENTS.md, bounded, and are supplied as conversation context.
    """
    raw = doc.get('project_workspace')
    if not isinstance(raw, str) or not raw.strip():
        return None, None
    workspace = Path(raw).expanduser()
    try:
        workspace = workspace.resolve(strict=True)
    except OSError as error:
        raise ConversationProviderError(
            'The saved project scope is unavailable. Restore the project before continuing this conversation.') from error
    if not workspace.is_dir():
        raise ConversationProviderError(
            'The saved project scope is not a repository folder. Restore the project before continuing this conversation.')
    problem = scope_problem({**doc, 'attachment': None}).get('project_scope_error')
    if problem:
        raise ConversationProviderError(problem)
    instructions = ''
    instructions_path = workspace / PROJECT_INSTRUCTIONS_FILE
    try:
        resolved = instructions_path.resolve(strict=True)
        if resolved.parent == workspace and resolved.is_file():
            instructions = resolved.read_text(encoding='utf-8', errors='replace')[:MAX_PROJECT_INSTRUCTIONS_CHARS]
    except OSError:
        instructions = ''
    text = ('This conversation is scoped to the project at ' + str(workspace)
            + '. All planning in this conversation targets that repository.'
            + (' Repository instructions from ' + PROJECT_INSTRUCTIONS_FILE
               + ' follow below and govern the work:\n\n' + instructions
               if instructions else ' No ' + PROJECT_INSTRUCTIONS_FILE + ' is saved in the project.'))
    message = {'id': 'project-scope', 'role': 'system', 'speaker': 'Project scope', 'text': text}
    return workspace, message


class ContinuousConversationStore(ScopeConfirmationMixin, DraftRefreshMixin, DraftCoalescingMixin, RecoveryMixin,
                                  legacy.ConversationStore):
    def __init__(self, root=None, provider=None, planner=None):
        if root is None:
            home = Path(os.environ.get('AUTOCODE_HOME', '~/.autocode')).expanduser()
            dashboard = Path(os.environ.get('AUTOCODE_DASHBOARD_HOME', str(home / 'dashboard'))).expanduser()
            root = dashboard / 'conversations'
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.provider = provider or opencode_provider
        # The actual independent Planner (planner_dispatch); never a Gatherer
        # reply relabeled as a structured result.  Tests inject doubles at this
        # provider-call boundary only.
        self.planner = planner or planner_dispatch.opencode_planner_provider
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='planning-chat')
        self._lock = threading.RLock()
        self._closed = False
        self._leases = {}
        self._planner_inflight = {}
        self._planner_queued = {}  # (conversation, logical turn) -> Planner workers queued or running
        self._planner_started = {}
        (self.root / '.locks').mkdir(exist_ok=True, mode=0o700)
        self._guard_depth = 0
        self._guard_fd = os.open(self.root / '.store.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        self._guard_cleanup = weakref.finalize(self, os.close, self._guard_fd)
        self._recover_saved()

    def _load(self, conversation_id):
        path = self._path(conversation_id)
        try:
            if path.stat().st_size > MAX_CONTEXT_CHARS * 8:
                raise ValueError('This saved conversation exceeds the supported size.')
            doc = json.loads(path.read_text(encoding='utf-8'))
        except FileNotFoundError as error:
            raise ValueError('Conversation not found.') from error
        except (UnicodeError, json.JSONDecodeError) as error:
            raise ValueError('This saved conversation could not be read.') from error
        if (not isinstance(doc, dict) or doc.get('id') != conversation_id
                or not isinstance(doc.get('messages'), list)
                or (doc['messages'] and (not all(isinstance(row, dict) and row.get('role') in ('user', 'assistant')
                            and isinstance(row.get('text'), str) and isinstance(row.get('id'), str)
                            for row in doc['messages'])
                    or not any(row['role'] == 'user' for row in doc['messages'])))
                or (not doc['messages'] and doc.get('status') != 'ready')
                or not all(isinstance(doc.get(key), str) for key in ('title', 'created_at', 'updated_at'))
                or not isinstance(doc.get('models'), dict)
                or not isinstance(doc['models'].get('glm_model'), str)
                or doc.get('status') not in ('thinking', 'ready', 'error', 'uncertain')):
            raise ValueError('This saved conversation has an invalid format.')
        return doc


    @staticmethod
    def _public(doc):
        value = deepcopy({key: item for key, item in doc.items() if not key.startswith('_')})
        turn = next((row.get('logical_turn_id') for row in reversed(doc['messages']) if row['role'] == 'user'), None)
        for name, records in (('pending_dispatch', '_dispatches'), ('planner_delivery', '_planner_dispatches')):
            record = doc.get(records, {}).get(turn)
            public = conversation_protocol.public_delivery(record)
            if public:
                public['retryable'] = public['state'] == 'SAFE_NOT_DISPATCHED'
                public['recovery'] = 'retry' if public['retryable'] else 'receipt_required' if public['state'] == 'UNCERTAIN' else None
            value[name] = public
        value['draft_update'] = draft_cadence.public(doc)
        problem = scope_problem(doc)
        if problem:
            value.update(problem, status='error', error=problem['project_scope_error'])
        return value


    def create(self, text, models=None, request_id=None, workspace=None):
        text, models, request_id = _text(text), _models(models), _request_id(request_id)
        if workspace is not None and (not isinstance(workspace, str) or not workspace.strip() or len(workspace) > 4096):
            raise ValueError('The creation-time project scope must be a short non-empty path.')
        with self._guard():
            self._ensure_open()
            for summary in self.list(include_archived=True):
                doc = self._load(summary['id'])
                if doc.get('_create_request_id') == request_id:
                    if ((doc['messages'] and doc['messages'][0]['text'] != text)
                            or doc['models'] != models
                            or (doc.get('project_workspace') or None) != (workspace or None)):
                        raise ValueError('That request ID was already used for a different conversation.')
                    return self._public(doc)
            created = _now()
            doc = {'id': uuid.uuid4().hex, 'title': ' '.join(text.split())[:80],
                   'created_at': created, 'updated_at': created, 'status': 'thinking', 'error': None,
                   'messages': [], 'models': models, 'attachment': None,
                   **_creation_project_scope(workspace),
                   'schema_version': conversation_protocol.HANDOFF_VERSION, 'drafts': [],
                   'requirements': {'revisions': [], 'provenance': []}, 'plan_drafts': [],
                   'configured_routes': _configured_routes(models),
                   'provider_capabilities': conversation_protocol.capabilities(),
                   '_create_request_id': request_id, '_requests': {},
                   '_dispatches': {}, '_planner_dispatches': {}}
            self._append_user(doc, text, request_id)
            return self._start(doc)

    def create_empty(self, workspace=None, request_id=None, models=None):
        """Save an empty scoped conversation before the first message is sent.

        Activating a New conversation control opens this saved record with its
        project scope already attached; the first message continues it through
        the normal turn pipeline, providers included.
        """
        request_id, models = _request_id(request_id), _models(models)
        if workspace is not None and (not isinstance(workspace, str) or not workspace.strip() or len(workspace) > 4096):
            raise ValueError('The creation-time project scope must be a short non-empty path.')
        with self._guard():
            self._ensure_open()
            for summary in self.list(include_archived=True):
                doc = self._load(summary['id'])
                if doc.get('_create_request_id') == request_id:
                    if doc['messages'] or doc['models'] != models or (doc.get('project_workspace') or None) != (workspace or None):
                        raise ValueError('That request ID was already used for a different conversation.')
                    return self._public(doc)
            created = _now()
            doc = {'id': uuid.uuid4().hex, 'title': 'New conversation',
                   'created_at': created, 'updated_at': created, 'status': 'ready', 'error': None,
                   'messages': [], 'models': models, 'attachment': None,
                   **_creation_project_scope(workspace),
                   'schema_version': conversation_protocol.HANDOFF_VERSION, 'drafts': [],
                   'requirements': {'revisions': [], 'provenance': []}, 'plan_drafts': [],
                   'configured_routes': _configured_routes(models),
                   'provider_capabilities': conversation_protocol.capabilities(),
                   '_create_request_id': request_id, '_requests': {},
                   '_dispatches': {}, '_planner_dispatches': {}}
            self._save(doc)
            return self._public(doc)


    def send(self, conversation_id, text, request_id=None):
        text, request_id = _text(text), _request_id(request_id)
        with self._guard():
            self._ensure_open()
            doc = self._load(conversation_id)
            self.require_visible(doc)
            if request_id in doc.get('_requests', {}):
                message_id = doc['_requests'][request_id]
                prior = next(row for row in doc['messages'] if row['id'] == message_id)
                if prior['text'] != text:
                    raise ValueError('That request ID was already used for a different message.')
                return self._public(doc)
            if doc['status'] == 'thinking':
                raise ValueError('The Planner is still replying. Wait for the response before sending another message.')
            if doc['status'] == 'uncertain':
                raise ValueError('The Planner outcome is uncertain. Check delivery status before sending another message.')
            if doc.get('attachment'):
                raise ValueError('This conversation is attached to a project. Continue in its task conversation.')
            if doc['status'] == 'error':
                raise ValueError('Retry the saved message before sending another one.')
            if not doc['messages']:
                # The first message titles a pre-send conversation opened from
                # the sidebar's New conversation control.
                doc['title'] = ' '.join(text.split())[:80]
            self._append_user(doc, text, request_id)
            return self._start(doc)


    def update(self, conversation_id, **fields):
        if not fields or set(fields) - {'title', 'models', 'attachment'}:
            raise ValueError('Only the conversation title, models, and attachment can be updated.')
        with self._guard():
            self._ensure_open()
            doc = self._load(conversation_id)
            if 'title' in fields:
                title = fields['title']
                if not isinstance(title, str) or not title.strip() or len(title) > 120:
                    raise ValueError('Conversation titles must contain 1–120 characters.')
                fields['title'] = title.strip()
            if 'models' in fields:
                if doc['status'] == 'thinking':
                    raise ValueError('Wait for the Planner to finish before changing conversation models.')
                fields['models'] = _models(fields['models'])
                fields['configured_routes'] = _configured_routes(fields['models'])
            if 'attachment' in fields:
                attachment = fields['attachment']
                if attachment is not None and not isinstance(attachment, dict):
                    raise ValueError('The project attachment must be an object.')
                try:
                    payload = json.dumps(attachment, allow_nan=False)
                except (ValueError, TypeError) as error:
                    raise ValueError('The project attachment must be valid JSON.') from error
                if len(payload) > 32_000:
                    raise ValueError('The project attachment is too large.')
                fields['attachment'] = deepcopy(attachment)
            if all(doc.get(key) == value for key, value in fields.items()):
                return self._public(doc)
            doc.update(fields)
            self._save(doc)
            return self._public(doc)


    def _authorized_dispatch_routes(self, doc):
        """Validate the next turn's saved routes, preserving explicit model choices.

        A legacy document without configured routes receives the defaults.
        """
        if 'configured_routes' not in doc:
            return doc.setdefault('configured_routes',
                                  _configured_routes({'glm_model': DEFAULT_GLM_MODEL}))
        return planner_dispatch.enforce_conversation_routes(doc['configured_routes'])


    def _append_user(self, doc, text, request_id):
        # Authorize this turn's dispatch routes before any state mutation: a
        # historical persisted route never launches a provider for a new turn.
        routes = self._authorized_dispatch_routes(doc)
        message = {'id': uuid.uuid4().hex, 'role': 'user', 'speaker': 'You', 'text': text,
                   'created_at': _now(), 'status': 'saved', 'client_request_id': request_id,
                   'logical_turn_id': 'turn-' + uuid.uuid4().hex, 'in_reply_to': None}
        proposed = [*doc['messages'], message]
        if len(proposed) + 1 > MAX_MESSAGES or len(_prompt(proposed)) + MAX_REPLY_CHARS > MAX_CONTEXT_CHARS:
            raise ValueError('This conversation has reached its context limit. Start a new conversation with a summary; no messages were discarded.')
        doc['messages'] = proposed
        doc.setdefault('_requests', {})[request_id] = message['id']
        doc.setdefault('requirements', {'revisions': [], 'provenance': []})
        doc.setdefault('plan_drafts', [])
        doc.setdefault('_dispatches', {})
        doc.setdefault('_planner_dispatches', {})
        # Every substantive human turn persists a requirements-state revision,
        # opens the pending structured draft bound to (requirements revision,
        # logical turn), and records BOTH dispatch intents — the Gatherer and
        # the actual independent Planner — before anything is dispatched, so
        # the Planner dispatch is durable in the same window as the turn.
        # The Gatherer always replies; the Planner intent dispatches, waits for
        # the draft in flight (coalesced) or waits for the answer cadence.
        in_flight = self._planner_turns_in_flight(doc)
        revision = _record_requirements_update(doc, message)
        draft = _record_plan_draft_revision(doc, message, revision['revision'])
        decision = draft_cadence.launch(doc, revision['revision'], in_flight)
        scheduled = decision == draft_cadence.DISPATCH
        reason = {draft_cadence.DISPATCH: ('scheduled_refresh', 'automatic_batch'),
                  draft_cadence.HOLD: ('batching_answers', 'batching_answers'),
                  draft_cadence.COALESCE: (draft_cadence.COALESCED, draft_cadence.COALESCED)}[decision]
        draft['freshness']['reason'] = reason[0]
        doc['_dispatches'][message['logical_turn_id']] = conversation_protocol.new_dispatch(
            logical_turn_id=message['logical_turn_id'], client_request_id=request_id,
            route=routes['requirements_gatherer'])
        doc['_planner_dispatches'][message['logical_turn_id']] = planner_dispatch.new_dispatch(
            logical_turn_id=message['logical_turn_id'], requirements_revision=revision['revision'],
            client_request_id=request_id, route=routes['planner'])
        doc['_planner_dispatches'][message['logical_turn_id']].update(
            cadence_hold=not scheduled, cadence_reason=reason[1])
        doc.update(status='thinking', error=None, _active_turn=uuid.uuid4().hex,
                   _active_logical_turn=message['logical_turn_id'])


    def _prepare_dispatches(self, doc):
        """Move both turn dispatch intents to DISPATCH_PREPARED (idempotent)."""
        logical_turn = doc.get('_active_logical_turn')
        for records in ('_dispatches', '_planner_dispatches'):
            dispatch = (doc.get(records) or {}).get(logical_turn) if logical_turn else None
            if (isinstance(dispatch, dict) and not draft_cadence.held(dispatch)
                    and dispatch.get('state') in ('SAVED', 'SAFE_NOT_DISPATCHED')):
                doc[records][logical_turn] = conversation_protocol.transition(dispatch, 'DISPATCH_PREPARED')


    def _start(self, doc):
        lease = self._lease(doc['id'])
        key = (doc['id'], doc['_active_turn'])
        self._leases[key] = lease
        try:
            self._prepare_dispatches(doc)
            self._save(doc)
            self._dispatch(doc)
            return self._public(doc)
        except Exception:
            self._leases.pop(key, lease).close()
            raise


    def _dispatch(self, doc):
        logical_turn = doc.get('_active_logical_turn')
        key = (doc['id'], logical_turn)
        held = draft_cadence.held(doc.get('_planner_dispatches', {}).get(logical_turn))
        self._planner_started[key] = threading.Event()
        if held:
            self._planner_started[key].set()  # held is not provider-launch evidence
        try:
            # Start the independent Planner first. The Gatherer can converse
            # concurrently, but cannot commit its reply before Planner launch.
            if not held:
                self._submit_planner(doc['id'], doc['_active_turn'], logical_turn)
            self.pool.submit(self._reply, doc['id'], doc['_active_turn'], logical_turn)
        except RuntimeError:
            self._planner_started.pop(key).set()
            self._leases.pop((doc['id'], doc['_active_turn'])).close()
            for records in ('_dispatches', '_planner_dispatches'):
                dispatch = (doc.get(records) or {}).get(logical_turn)
                if isinstance(dispatch, dict) and not draft_cadence.held(dispatch) and dispatch.get('state') != 'SAFE_NOT_DISPATCHED':
                    with contextlib.suppress(conversation_protocol.ConversationProtocolError):
                        doc[records][logical_turn] = conversation_protocol.transition(
                            dispatch, 'SAFE_NOT_DISPATCHED', reason='Conversation worker was unavailable before process creation')
            self._mark_planner_draft_failed(doc, logical_turn, {
                'stage': 'planner_dispatch', 'message': 'The conversation service stopped before the structured Planner could start.',
                'reason': 'worker_unavailable'})
            doc.update(status='error', error='The conversation service stopped before the Planner could reply. Retry shortly.', _active_turn=None)
            self._pending_message(doc)['status'] = 'error'
            self._save(doc)


    def _reply(self, conversation_id, turn_id, logical_turn=None):
        try:
            # The worker boundary keeps its completed-reply visibility and lease
            # release here so an immediate followup can enter while the
            # independent structured Planner may still be running.
            self._reply_locked(conversation_id, turn_id, logical_turn)
        finally:
            with self._guard():
                lease = self._leases.pop((conversation_id, turn_id), None)
                if lease:
                    lease.close()
                self._planner_started.pop((conversation_id, logical_turn), None)


    def _reply_locked(self, conversation_id, turn_id, logical_turn=None):
        launch = self._planner_started.get((conversation_id, logical_turn))
        if launch is not None:
            launch.wait()
        with self._guard():
            doc = self._load(conversation_id)
            logical_turn = logical_turn or doc.get('_active_logical_turn')
            if doc.get('_active_turn') != turn_id:
                return
            dispatch = doc.get('_dispatches', {}).get(logical_turn, {})
            if dispatch.get('state') in ('RESULT_CAPTURED', 'REPLY_COMMITTED'):
                self._commit_result(doc, logical_turn, dispatch)
                return
            if dispatch.get('state') not in ('SAVED', 'SAFE_NOT_DISPATCHED', 'DISPATCH_PREPARED'):
                self._record_failure(doc, logical_turn, None, 'Provider delivery is uncertain; the saved turn was not repeated.')
                return
            messages = deepcopy(doc['messages'])
            gatherer = dispatch.get('route') or {}
            model = gatherer.get('model') or doc['models']['glm_model']
            effort = gatherer.get('reasoning_effort') or 'low'
        try:
            # A saved project scope routes this turn's Requirements Gatherer
            # into the selected repository with its instructions as context;
            # unscoped conversations keep their scratch working directory.
            scope_workspace, scope_message = _project_scope_context(doc)
            if scope_workspace is not None:
                workdir = scope_workspace
            else:
                workdir = self.root / 'scratch' / conversation_id
                workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
                if workdir.is_symlink() or self.root not in workdir.resolve().parents:
                    raise ConversationProviderError('The conversation scratch directory is invalid.')
            provider_messages = [scope_message, *messages] if scope_message is not None else messages
            response = self._invoke_provider(conversation_id, turn_id, logical_turn, provider_messages,
                                             model, workdir, effort)
            if not isinstance(response, str) or not response.strip():
                raise ConversationProviderError('The ' + role_label('requirements') + ' returned no text. Your message is saved; retry when ready.')
            if len(response) > MAX_REPLY_CHARS:
                raise ConversationProviderError('The ' + role_label('requirements') + ' returned an oversized reply. Your message is saved; retry with a narrower request.')
            self._capture_result(conversation_id, turn_id, logical_turn, response.strip())
            error, outcome = None, None
        except ConversationProviderError as exc:
            error, outcome = str(exc), exc.outcome
        except Exception:
            # Exceptions/CLI diagnostics may contain credentials. Display only
            # controlled messages; never persist provider output or environment.
            error, outcome = 'The ' + role_label('requirements') + ' could not reply. Your message is saved; check the provider connection and retry.', None
        with self._guard():
            doc = self._load(conversation_id)
            if doc.get('_active_turn') != turn_id:
                return
            if error:
                self._record_failure(doc, logical_turn, outcome, error)
            else:
                dispatch = (doc.get('_dispatches') or {}).get(logical_turn)
                self._commit_result(doc, logical_turn, dispatch)
            # Readers must not observe ready/error while the completed turn
            # still owns its lease: a followup or retry can arrive immediately.
            # Keep the outer cleanup for failures before this durable save.
            lease = self._leases.pop((conversation_id, turn_id), None)
            if lease:
                lease.close()


    def _dispatch_event(self, conversation_id, turn_id, logical_turn, event, details):
        """Persist Gatherer provider evidence as it becomes available, before reply commit."""
        with self._guard():
            doc = self._load(conversation_id)
            if doc.get('_active_turn') != turn_id or doc.get('_active_logical_turn') != logical_turn:
                return
            dispatch = (doc.get('_dispatches') or {}).get(logical_turn)
            if not isinstance(dispatch, dict):
                return
            current = dispatch.get('state')
            try:
                if event == 'process_starting' and current != 'PROCESS_STARTING':
                    dispatch = conversation_protocol.transition(dispatch, 'PROCESS_STARTING', process={'owned': False, 'pid': None})
                elif event == 'process_started' and current != 'PROCESS_STARTED':
                    dispatch = conversation_protocol.transition(dispatch, 'PROCESS_STARTED',
                                                               process={'owned': details.get('owned') is True, 'pid': details.get('pid')})
                elif event == 'provider_identified' and current != 'PROVIDER_IDENTIFIED':
                    dispatch = conversation_protocol.transition(dispatch, 'PROVIDER_IDENTIFIED',
                                                               provider_identity={'session_id': details.get('session_id')})
                elif event == 'safe_not_dispatched' and current != 'SAFE_NOT_DISPATCHED':
                    dispatch = conversation_protocol.transition(dispatch, 'SAFE_NOT_DISPATCHED', reason='Provider process was not created')
                elif event == 'result_captured':
                    text = details.get('result_text')
                    if isinstance(text, str) and text.strip():
                        dispatch = self._capture_result_locked(doc, logical_turn, text, raw_events=details.get('raw_events'),
                                                               session_id=details.get('session_id'))
                else:
                    raise ConversationProviderError('Unknown ' + role_label('requirements') + ' delivery event.')
            except conversation_protocol.ConversationProtocolError:
                pass
            else:
                doc['_dispatches'][logical_turn] = dispatch
                self._save(doc)


    def _invoke_provider(self, conversation_id, turn_id, logical_turn, messages, model, workdir,
                         effort=None):
        def observer(event, details):
            self._dispatch_event(conversation_id, turn_id, logical_turn, event, details)
        if getattr(self.provider, 'autocode_dispatch_aware', False):
            return self.provider(messages, model, workdir, dispatch_observer=observer,
                                 logical_turn_id=logical_turn, effort=effort)
        # A callback without an observer could still make an external request.
        # Save uncertainty before entering it; only its captured return proves
        # completion. A crash must never leave a replayable prepared receipt.
        with self._guard():
            doc = self._load(conversation_id)
            dispatch = doc['_dispatches'][logical_turn]
            doc['_dispatches'][logical_turn] = conversation_protocol.transition(
                dispatch, 'UNCERTAIN', reason='Callback entered without durable dispatch observations')
            self._save(doc)
        return self.provider(messages, model, workdir)


    def _raw_result_path(self, conversation_id, logical_turn):
        root = self.root / 'scratch' / conversation_id / 'dispatch-records'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = root / (logical_turn + '.json')
        if path.is_symlink() or self.root not in path.resolve(strict=False).parents:
            raise ConversationProviderError('The conversation dispatch record path is invalid.')
        return path


    def _capture_result_locked(self, doc, logical_turn, response, *, raw_events=None, session_id=None):
        dispatch = (doc.get('_dispatches') or {}).get(logical_turn)
        if not isinstance(dispatch, dict):
            raise ConversationProviderError('The saved delivery receipt is unavailable.')
        result = {'sha256': hashlib.sha256(response.encode('utf-8')).hexdigest(), 'text': response}
        raw_record = dispatch.get('raw_result')
        if isinstance(raw_events, str):
            path = self._raw_result_path(doc['id'], logical_turn)
            payload = {'sha256': hashlib.sha256(raw_events.encode('utf-8')).hexdigest(), 'events': raw_events}
            conversation_protocol._atomic_json(path, payload)
            raw_record = {'path': str(path), 'sha256': payload['sha256']}
        fields = {'result': result, 'raw_result': raw_record}
        if session_id:
            fields['provider_identity'] = {'session_id': session_id}
        return conversation_protocol.transition(dispatch, 'RESULT_CAPTURED', **fields)


    def _capture_result(self, conversation_id, turn_id, logical_turn, response):
        with self._guard():
            doc = self._load(conversation_id)
            if doc.get('_active_turn') != turn_id or doc.get('_active_logical_turn') != logical_turn:
                return
            doc['_dispatches'][logical_turn] = self._capture_result_locked(doc, logical_turn, response)
            self._save(doc)


    def _commit_result(self, doc, logical_turn, dispatch=None):
        """Commit the Gatherer reply. Never grants plan-draft freshness (AC2)."""
        dispatch = dispatch if isinstance(dispatch, dict) else (doc.get('_dispatches') or {}).get(logical_turn)
        if not isinstance(dispatch, dict) or dispatch.get('state') not in ('RESULT_CAPTURED', 'REPLY_COMMITTED'):
            raise ConversationProviderError('A completed ' + role_label('requirements') + ' result is unavailable.')
        result = dispatch.get('result') if isinstance(dispatch.get('result'), dict) else {}
        response = result.get('text')
        if not isinstance(response, str) or not response.strip() or result.get('sha256') != hashlib.sha256(response.encode('utf-8')).hexdigest():
            raise ConversationProviderError('The saved ' + role_label('requirements') + ' result failed validation.')
        reply = next((row for row in doc['messages']
                      if row.get('role') == 'assistant' and row.get('in_reply_to') == logical_turn), None)
        if reply is None:
            route = dispatch.get('route') if isinstance(dispatch.get('route'), dict) else {}
            doc['messages'].append({'id': uuid.uuid4().hex, 'role': 'assistant', 'speaker': role_label('requirements'),
                                    'text': response, 'created_at': _now(), 'status': 'received',
                                    'client_request_id': None,
                                    'logical_turn_id': 'reply-' + uuid.uuid4().hex, 'in_reply_to': logical_turn,
                                    'execution': {'role': 'requirements_gatherer', 'engine': route.get('engine'),
                                                  'provider': route.get('provider'), 'model': route.get('model'),
                                                  'reasoning_effort': route.get('reasoning_effort')}})
            reply = doc['messages'][-1]
        # The Gatherer reply intentionally does NOT touch plan_drafts: only a
        # validated structured Planner result may grant freshness (the snapshot
        # stamped freshness here; this pipeline removed that behavior).
        self._pending_message(doc)['status'] = 'received'
        if dispatch.get('state') != 'REPLY_COMMITTED':
            doc['_dispatches'][logical_turn] = conversation_protocol.transition(
                dispatch, 'REPLY_COMMITTED', reply_commit_id='reply-' + reply['id'])
        doc.update(status='ready', error=None, _active_turn=None, _active_logical_turn=None)
        self._save(doc)


    def _record_failure(self, doc, logical_turn, outcome, error):
        dispatch = (doc.get('_dispatches') or {}).get(logical_turn)
        if not isinstance(dispatch, dict):
            doc.update(status='error', error=error, _active_turn=None)
            self._pending_message(doc)['status'] = 'error'
            self._save(doc)
            return
        state = dispatch.get('state')
        # A failure is a safe, retryable error only while no external dispatch
        # boundary was entered (or the provider authoritatively reported it
        # never dispatched).  A generic failure inside the process-creation
        # window may have a running provider process, so it stays visibly
        # uncertain instead of being retried as a safe failure.
        if (outcome == 'not_dispatched' and state != 'UNCERTAIN') or state in ('DISPATCH_PREPARED', 'SAFE_NOT_DISPATCHED'):
            if state != 'SAFE_NOT_DISPATCHED':
                doc['_dispatches'][logical_turn] = conversation_protocol.transition(dispatch, 'SAFE_NOT_DISPATCHED', reason=error)
            self._pending_message(doc)['status'] = 'error'
            doc.update(status='error', error=error, _active_turn=None)
        elif state == 'RESULT_CAPTURED':
            self._commit_result(doc, logical_turn, dispatch)
            return
        else:
            if state != 'UNCERTAIN':
                doc['_dispatches'][logical_turn] = conversation_protocol.transition(dispatch, 'UNCERTAIN', reason=error)
            self._pending_message(doc)['status'] = 'uncertain'
            doc.update(status='uncertain',
                       error='Delivery outcome is uncertain. Check delivery status before retrying or sending another message.',
                       _active_turn=None)
        self._save(doc)


    def _planner_reply_owned(self, conversation_id, turn_id, logical_turn=None):
        # One in-flight structured Planner launch per logical turn per store:
        # a Gatherer retry of the same turn must never double-launch the
        # Planner or double-commit its result (cross-process restart
        # reconciliation is owned by the M3 hardening).
        key = (conversation_id, logical_turn)
        with self._guard():
            if key in self._planner_inflight:
                return
            self._planner_inflight[key] = True
        try:
            self._planner_reply_locked(conversation_id, logical_turn)
        except Exception:
            # Never leak diagnostics; the durable dispatch/draft records hold
            # the retained, controlled evidence.
            try:
                with self._guard():
                    doc = self._load(conversation_id)
                    self._planner_reject(doc, logical_turn, PlannerDispatchFailure(
                        'The structured Planner could not complete. The previous plan draft is preserved.',
                        stage='planner'))
                    self._save(doc)
            except (ValueError, OSError, KeyError):
                pass
        finally:
            with self._guard():
                self._planner_inflight.pop(key, None)
                launch = self._planner_started.get(key)
                if launch is not None:
                    launch.set()


    def _planner_reply_locked(self, conversation_id, logical_turn):
        with self._guard():
            doc = self._load(conversation_id)
            dispatch = (doc.get('_planner_dispatches') or {}).get(logical_turn)
            if draft_cadence.held(dispatch) or not isinstance(dispatch, dict) or dispatch.get('state') not in ('SAVED', 'SAFE_NOT_DISPATCHED', 'DISPATCH_PREPARED', 'RESULT_CAPTURED'):
                return  # Committed or ambiguous deliveries are never dispatched twice.
            revision = dispatch.get('requirements_revision')
            latest = (doc.get('requirements', {}).get('revisions') or [{}])[-1].get('revision')
            if doc.get('attachment') or doc.get('archived_at') or latest != revision:
                doc['_planner_dispatches'][logical_turn] = conversation_protocol.transition(
                    dispatch, dispatch['state'], stale_result_discarded=True,
                    stale_reason='Conversation was superseded, archived, or attached before recovery')
                self._save(doc)
                return
            captured = deepcopy(dispatch.get('result')) if dispatch.get('state') == 'RESULT_CAPTURED' else None
            if not dispatch.get('structured_rejected'):
                for row in doc.get('plan_drafts', []):
                    if row.get('logical_turn_id') == logical_turn and row.get('status') == 'failed':
                        row['status'] = 'pending'
                        row['freshness']['state'] = 'pending'
            if captured is None and dispatch.get('state') != 'DISPATCH_PREPARED':
                dispatch = conversation_protocol.transition(dispatch, 'DISPATCH_PREPARED')
                doc['_planner_dispatches'][logical_turn] = dispatch
            revision = dispatch.get('requirements_revision')
            route = deepcopy(dispatch.get('route') or {})
            messages = deepcopy(doc['messages'])
            self._save(doc)
        try:
            # The same saved project scope routes the structured Planner into
            # the selected repository with its instructions as context; the
            # Gatherer turn and the Planner turn cannot drift apart.
            scope_workspace, scope_message = _project_scope_context(doc)
            if scope_workspace is not None:
                workdir = scope_workspace
            else:
                workdir = self.root / 'scratch' / conversation_id
                workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
                if workdir.is_symlink() or self.root not in workdir.resolve().parents:
                    raise planner_dispatch.PlannerDispatchError('The conversation scratch directory is invalid.',
                                                                 stage='planner_provider')
            planner_messages = [scope_message, *messages] if scope_message is not None else messages
            if captured is not None:
                raw = captured.get('text')
                if not isinstance(raw, str) or captured.get('sha256') != hashlib.sha256(raw.encode()).hexdigest():
                    raise planner_dispatch.PlannerDispatchError('Saved Planner result failed integrity validation.', stage='recovery')
            else:
                raw = self._invoke_planner(conversation_id, logical_turn, planner_messages, route, workdir, revision)
                self._capture_planner_result(conversation_id, logical_turn, raw)
            draft = planner_dispatch.extract_structured_draft(raw)
            validated = planner_dispatch.validate_structured_result(
                draft, requirements_revision=revision, logical_turn_id=logical_turn, route=route)
        except planner_dispatch.PlannerDispatchError as error:
            with self._guard():
                doc = self._load(conversation_id)
                self._planner_reject(doc, logical_turn, error)
                self._save(doc)
            return
        except ConversationProviderError as error:
            with self._guard():
                doc = self._load(conversation_id)
                self._planner_reject(doc, logical_turn, PlannerDispatchFailure(
                    str(error), stage='planner_provider', outcome=getattr(error, 'outcome', None)))
                self._save(doc)
            return
        except Exception:
            # Controlled message only: raw exceptions may contain credentials.
            with self._guard():
                doc = self._load(conversation_id)
                self._planner_reject(doc, logical_turn, PlannerDispatchFailure(
                    'The structured Planner could not reply. The previous plan draft is preserved.',
                    stage='planner_provider'))
                self._save(doc)
            return
        with self._guard():
            doc = self._load(conversation_id)
            latest = (doc.get('requirements', {}).get('revisions') or [{}])[-1].get('revision', 0)
            if latest != revision:
                # A newer human turn already produced a newer requirements
                # revision: this out-of-order result is invalidated and never
                # committed over the newer input (AC5).
                dispatch = (doc.get('_planner_dispatches') or {}).get(logical_turn)
                if isinstance(dispatch, dict):
                    with contextlib.suppress(conversation_protocol.ConversationProtocolError):
                        doc['_planner_dispatches'][logical_turn] = conversation_protocol.transition(
                            dispatch, 'RESULT_CAPTURED', stale_result_discarded=True,
                            stale_reason='newer requirements input',
                            structured_digest=conversation_protocol.digest(validated))
                self._save(doc)
                return
            self._commit_structured(doc, logical_turn, revision, validated, route)
            self._save(doc)


    def _invoke_planner(self, conversation_id, logical_turn, messages, route, workdir, revision):
        def observer(event, details):
            self._planner_dispatch_event(conversation_id, logical_turn, event, details)
        # The Gatherer commit barrier is NOT released here: entry into this
        # method, a DISPATCH_PREPARED record, or the adapter's subscription
        # preflight are not launch evidence.  The barrier opens only on durable
        # production-adapter process-started evidence persisted by
        # _planner_dispatch_event ('process_started'), or truthfully without
        # claiming a launch on an authoritative prelaunch failure
        # ('safe_not_dispatched'); _planner_reply's finally block remains the
        # safety net for failures that never reach an observer.
        if getattr(self.planner, 'autocode_dispatch_aware', False):
            return self.planner(messages, route, workdir, dispatch_observer=observer,
                                logical_turn_id=logical_turn, requirements_revision=revision)
        with self._guard():
            doc = self._load(conversation_id)
            doc['_planner_dispatches'][logical_turn] = conversation_protocol.transition(
                doc['_planner_dispatches'][logical_turn], 'UNCERTAIN',
                reason='Callback entered without durable dispatch observations')
            self._save(doc)
        return self.planner(messages, route, workdir)


    def _capture_planner_result(self, conversation_id, logical_turn, raw):
        """Durably capture the independent result before schema validation."""
        if not isinstance(raw, str):
            raise planner_dispatch.PlannerDispatchError('The Planner result was not text.',
                                                        stage='structured_parse',
                                                        evidence={'raw_result': planner_dispatch.bounded_raw(raw)})
        with self._guard():
            doc = self._load(conversation_id)
            dispatch = (doc.get('_planner_dispatches') or {}).get(logical_turn)
            if not isinstance(dispatch, dict) or dispatch.get('state') == 'REPLY_COMMITTED':
                return
            result = {'sha256': hashlib.sha256(raw.encode('utf-8')).hexdigest(), 'text': raw}
            doc['_planner_dispatches'][logical_turn] = conversation_protocol.transition(
                dispatch, 'RESULT_CAPTURED', result=result)
            self._save(doc)


    def _planner_dispatch_event(self, conversation_id, logical_turn, event, details):
        """Persist Planner provider evidence durably; independent of the Gatherer turn state.

        Persisting 'process_started' is the durable production-adapter launch
        evidence that releases the Gatherer commit barrier, and
        'safe_not_dispatched' truthfully releases it after an authoritative
        prelaunch failure without claiming a launch.  Both releases happen only
        after the transition is saved.
        """
        with self._guard():
            doc = self._load(conversation_id)
            dispatch = (doc.get('_planner_dispatches') or {}).get(logical_turn)
            if not isinstance(dispatch, dict):
                return
            current = dispatch.get('state')
            try:
                if event == 'process_starting' and current != 'PROCESS_STARTING':
                    dispatch = conversation_protocol.transition(dispatch, 'PROCESS_STARTING', process={'owned': False, 'pid': None})
                elif event == 'process_started' and current != 'PROCESS_STARTED':
                    dispatch = conversation_protocol.transition(dispatch, 'PROCESS_STARTED',
                                                                process={'owned': details.get('owned') is True, 'pid': details.get('pid')})
                elif event == 'provider_identified' and current != 'PROVIDER_IDENTIFIED':
                    dispatch = conversation_protocol.transition(dispatch, 'PROVIDER_IDENTIFIED',
                                                                provider_identity={'session_id': details.get('session_id')})
                elif event == 'safe_not_dispatched' and current != 'SAFE_NOT_DISPATCHED':
                    dispatch = conversation_protocol.transition(dispatch, 'SAFE_NOT_DISPATCHED', reason='Provider process was not created')
                elif event == 'provider_events':
                    raw_events = details.get('raw_events')
                    if not isinstance(raw_events, str):
                        return
                    path = self._raw_result_path(conversation_id, 'planner-' + logical_turn)
                    payload = {'sha256': hashlib.sha256(raw_events.encode('utf-8')).hexdigest(),
                               'events': raw_events}
                    conversation_protocol._atomic_json(path, payload)
                    dispatch = conversation_protocol.transition(
                        dispatch, current, raw_result={'path': str(path), 'sha256': payload['sha256']})
                else:
                    return
            except conversation_protocol.ConversationProtocolError:
                return
            doc['_planner_dispatches'][logical_turn] = dispatch
            self._save(doc)
            if event in ('process_started', 'safe_not_dispatched'):
                launch = self._planner_started.get((conversation_id, logical_turn))
                if launch is not None:
                    launch.set()


    def _mark_planner_draft_failed(self, doc, logical_turn, evidence):
        """Record a failed draft state with retained evidence; keep the previous usable draft."""
        for row in doc.get('plan_drafts', []):
            if (row.get('status') == 'pending' and row.get('logical_turn_id') == logical_turn
                    and not draft_cadence.held(doc.get('_planner_dispatches', {}).get(logical_turn))):
                row['status'] = 'failed'
                row['freshness'] = {**row.get('freshness', {}), 'state': 'failed', 'updated_at': _now(),
                                    'source_logical_turn_id': logical_turn,
                                    'error': evidence}


    def _planner_reject(self, doc, logical_turn, error):
        """A malformed, schema-invalid or failed Planner result: reject loudly, retain evidence."""
        evidence = {'stage': getattr(error, 'stage', 'planner'),
                    'message': str(error)}
        extra = getattr(error, 'evidence', None)
        if isinstance(extra, dict):
            evidence.update(extra)
        outcome = getattr(error, 'outcome', None)
        dispatch = (doc.get('_planner_dispatches') or {}).get(logical_turn)
        if isinstance(dispatch, dict):
            state = dispatch.get('state')
            try:
                if outcome == 'not_dispatched':
                    if state != 'SAFE_NOT_DISPATCHED':
                        doc['_planner_dispatches'][logical_turn] = conversation_protocol.transition(
                            dispatch, 'SAFE_NOT_DISPATCHED', reason=str(error))
                elif state == 'RESULT_CAPTURED':
                    stage = getattr(error, 'stage', 'planner')
                    rejected = stage in ('structured_parse', 'structured_schema', 'structured_binding',
                                         'structured_attribution', 'structured_freshness', 'recovery')
                    doc['_planner_dispatches'][logical_turn] = conversation_protocol.transition(
                        dispatch, 'RESULT_CAPTURED', structured_rejected=rejected, error=evidence)
                else:
                    # A captured-but-invalid result records RESULT_CAPTURED with
                    # the rejection evidence; a provider failure records UNCERTAIN.
                    stage = getattr(error, 'stage', 'planner')
                    if stage in ('structured_parse', 'structured_schema', 'structured_binding',
                                 'structured_attribution', 'structured_freshness'):
                        doc['_planner_dispatches'][logical_turn] = conversation_protocol.transition(
                            dispatch, 'RESULT_CAPTURED', structured_rejected=True, error=evidence)
                    else:
                        doc['_planner_dispatches'][logical_turn] = conversation_protocol.transition(
                            dispatch, 'UNCERTAIN', reason=str(error), error=evidence)
            except conversation_protocol.ConversationProtocolError:
                pass
        self._mark_planner_draft_failed(doc, logical_turn, evidence)


    def _commit_structured(self, doc, logical_turn, revision, validated, route):
        """Commit a validated structured Planner result as the accepted draft revision."""
        declared = validated.get('freshness') if isinstance(validated.get('freshness'), dict) else {}
        if declared.get('state') != 'fresh':
            # Commit-boundary guard: only a successfully validated fresh
            # structured result may become current (validate_structured_result
            # already rejects non-success states; this protects the commit).
            raise planner_dispatch.PlannerDispatchError(
                'A non-successful structured result cannot become the accepted draft revision.',
                stage='structured_freshness',
                evidence={'declared_freshness': deepcopy(declared)})
        attribution = {'role': 'planner', 'model': route.get('model'),
                       'reasoning_effort': route.get('reasoning_effort'),
                       'engine': route.get('engine'), 'provider': route.get('provider')}
        drafts = doc.setdefault('plan_drafts', [])
        target = next((row for row in drafts if row.get('status') == 'pending'
                       and row.get('logical_turn_id') == logical_turn
                       and row.get('requirements_revision') == revision), None)
        if target is None:
            return  # The pending record was superseded by newer input; nothing to commit.
        for row in drafts:
            if row is target:
                continue
            if row.get('status') == 'current':
                row['status'] = 'superseded'
            elif row.get('status') == 'pending':
                row['status'] = 'superseded'
                row['freshness'] = {**row.get('freshness', {}), 'state': 'stale', 'updated_at': _now(),
                                    'reason': 'invalidated_by_newer_requirements_input',
                                    'source_logical_turn_id': row.get('logical_turn_id')}
        target.update({
            'status': 'current',
            'goal': validated['goal'],
            'requirements': deepcopy(validated['requirements']),
            'milestones': deepcopy(validated['milestones']),
            'parallelism': deepcopy(validated['parallelism']),
            'outstanding_questions': deepcopy(validated['unresolved_questions']),
            'reply_preview': None,
            'attribution': attribution,
            'freshness': {**target.get('freshness', {}), 'state': 'fresh', 'updated_at': _now(),
                          'requirements_revision': revision,
                          'source_logical_turn_id': logical_turn,
                          'structured_result': True},
        })
        # The human update was already durable before dispatch. Attach only
        # this validated result to that exact revision; stale/out-of-order
        # results cannot rewrite a newer requirements-state revision.
        state = next((row for row in doc['requirements']['revisions']
                      if row.get('revision') == revision and
                      row.get('source', {}).get('logical_turn_id') == logical_turn), None)
        if state is None:
            raise planner_dispatch.PlannerDispatchError(
                'The Planner result has no matching requirements-state revision.',
                stage='structured_binding')
        state.update(goal=validated['goal'], requirements=deepcopy(validated['requirements']),
                     outstanding_questions=deepcopy(validated['unresolved_questions']),
                     status='structured')
        # The authoritative protocol normalize validates the committed records.
        normalized = conversation_protocol.normalize_document(doc)
        normalized['plan_drafts'] = conversation_protocol._plan_drafts(normalized.get('plan_drafts', []))
        doc.clear()
        doc.update(normalized)
        dispatch = (doc.get('_planner_dispatches') or {}).get(logical_turn)
        if isinstance(dispatch, dict):
            result = {'sha256': conversation_protocol.digest(validated),
                      'structured': True}
            dispatch = conversation_protocol.transition(dispatch, 'RESULT_CAPTURED', result=result)
            doc['_planner_dispatches'][logical_turn] = conversation_protocol.transition(
                dispatch, 'REPLY_COMMITTED', reply_commit_id=target['id'], attribution=attribution)
