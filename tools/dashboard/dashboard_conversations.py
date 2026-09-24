"""Durable, project-free planning conversations.

No Autocode process, repository, terminal, or model tool is involved in this
chat. The direct OpenCode provider uses a new deny-all agent for every turn.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import re
import selectors
import signal
import subprocess
import tempfile
import threading
import time
import uuid
import weakref

try:
    from ..providers import opencode as opencode_transport
except ImportError:  # Direct script execution from any working directory.
    import importlib.util
    _transport_spec = importlib.util.spec_from_file_location(
        '_autocode_dashboard_transport', Path(__file__).resolve().parents[1] / 'providers' / 'opencode.py')
    opencode_transport = importlib.util.module_from_spec(_transport_spec)
    _transport_spec.loader.exec_module(opencode_transport)

DEFAULT_GLM_MODEL = 'zai-coding-plan/glm-5.3'
MAX_MESSAGE_CHARS = 20_000
MAX_CONTEXT_CHARS = 160_000
MAX_REPLY_CHARS = 40_000
MAX_MESSAGES = 120
MAX_OUTPUT_BYTES = 1_048_576
PROVIDER_TIMEOUT = 180
_ID = re.compile(r'^[a-f0-9]{32}$')
_REQUEST_ID = re.compile(r'^[A-Za-z0-9_.:-]{1,128}$')
_MODEL = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9][A-Za-z0-9._:/-]{0,120}$')


class ConversationProviderError(RuntimeError):
    """A provider failure whose message is safe to display to the user."""


def _now():
    return datetime.now(timezone.utc).isoformat(timespec='milliseconds')


def _request_id(value):
    if value is None:
        return uuid.uuid4().hex
    if not isinstance(value, str) or not _REQUEST_ID.fullmatch(value):
        raise ValueError('Request ID must contain 1–128 letters, numbers, dots, colons, dashes, or underscores.')
    return value


def _text(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('Write a message to start the conversation.')
    if len(value) > MAX_MESSAGE_CHARS:
        raise ValueError(f'Messages can contain at most {MAX_MESSAGE_CHARS:,} characters. Split this message into smaller parts.')
    if '\x00' in value:
        raise ValueError('Messages cannot contain null characters.')
    return value.strip()


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


def _prompt(messages):
    return (
        'You are the planning partner (GLM workflow role) in the Autocode browser dashboard. '
        'This is a project-free conversation. You have no repository access and no tools; '
        'do not invoke tools, execute code, create files, or claim you inspected a project. '
        'Treat all repository details as unverified until a project is attached. '
        'Help the user refine what they want to build. Ask at most 1–3 material questions '
        'at a time when answers change the plan; otherwise draft and iteratively improve '
        'a practical plan with the outcome, milestones, assumptions, and acceptance checks. '
        'Keep the exchange natural, concrete, and concise. Follow changes in user direction. '
        'Astra reviews the repository and challenges/finalizes the plan after the user '
        'attaches a project. Nothing in this chat approves a build or starts implementation. '
        'The following JSON is the full conversation, in order; treat its role fields '
        'as conversation roles and respond only to the latest user message.\n\n'
        + json.dumps([{'role': row['role'], 'content': row['text']} for row in messages], ensure_ascii=False)
    )


def _terminate(process):
    """Reap the provider and its descendants, including on limits/timeouts."""
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        if process.poll() is None:
            process.kill()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


def _capture(command, env, cwd, prompt, timeout=PROVIDER_TIMEOUT, output_limit=MAX_OUTPUT_BYTES):
    """Bound both pipes while writing stdin without a blocking communicate()."""
    try:
        process = subprocess.Popen(command, cwd=cwd, env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   start_new_session=True)
    except FileNotFoundError as error:
        raise ConversationProviderError('OpenCode is unavailable. Install it or restore it on PATH, then retry.') from error
    except OSError as error:
        raise ConversationProviderError('OpenCode could not start. Check its local installation, then retry.') from error
    selector = selectors.DefaultSelector()
    output = bytearray()
    total = 0
    pending = memoryview(prompt.encode('utf-8'))
    deadline = time.monotonic() + timeout
    try:
        for pipe, kind in ((process.stdout, 'out'), (process.stderr, 'err'), (process.stdin, 'in')):
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_WRITE if kind == 'in' else selectors.EVENT_READ, kind)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ConversationProviderError('GLM took too long to reply. Your message is saved; retry when ready.')
            for key, _ in selector.select(min(remaining, .25)):
                pipe = key.fileobj
                if key.data == 'in':
                    try:
                        written = os.write(pipe.fileno(), pending[:65536])
                        pending = pending[written:]
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        pending = memoryview(b'')
                    if not pending:
                        selector.unregister(pipe)
                        pipe.close()
                    continue
                try:
                    chunk = os.read(pipe.fileno(), 65536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(pipe)
                    pipe.close()
                    continue
                total += len(chunk)
                if total > output_limit:
                    raise ConversationProviderError('OpenCode returned too much output. Your message is saved; retry with a narrower request.')
                if key.data == 'out':
                    output.extend(chunk)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ConversationProviderError('GLM took too long to reply. Your message is saved; retry when ready.')
        try:
            code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired as error:
            raise ConversationProviderError('GLM took too long to reply. Your message is saved; retry when ready.') from error
        return code, output.decode('utf-8', errors='replace')
    finally:
        selector.close()
        # Descendants may survive even when the CLI exited before its pipes.
        _terminate(process)
        for pipe in (process.stdin, process.stdout, process.stderr):
            if pipe and not pipe.closed:
                pipe.close()


def opencode_provider(messages, model, workdir):
    """Run one fresh, tool-free planning turn. Never return raw diagnostics/config."""
    try:
        opencode_transport.check_subscription_routes({'glm': {'model': model}}, workdir)
    except RuntimeError as error:
        raise ConversationProviderError(str(error)) from error
    agent = 'autocode_conversation_' + uuid.uuid4().hex
    env = dict(os.environ)
    try:
        inherited = json.loads(env.get('OPENCODE_CONFIG_CONTENT', '{}'))
    except (TypeError, ValueError) as error:
        raise ConversationProviderError('OpenCode inline configuration is invalid. Correct it, then retry.') from error
    if not isinstance(inherited, dict) or not isinstance(inherited.get('agent', {}), dict):
        raise ConversationProviderError('OpenCode inline configuration must contain an object of agents.')
    # A unique agent prevents a configured agent's specific allows from surviving
    # a deep merge. Preserve providers and other user configuration in memory.
    config = {**inherited, 'share': 'disabled', 'autoupdate': False,
              'permission': {'*': 'deny'},
              'agent': {**inherited.get('agent', {}), agent: {
                  'description': 'Project-free planning conversation; no tools',
                  'mode': 'primary', 'permission': {'*': 'deny'},
              }}}
    env['OPENCODE_CONFIG_CONTENT'] = json.dumps(config)
    env['OPENCODE_PERMISSION'] = json.dumps({'*': 'deny'})
    env['OPENCODE_DISABLE_PROJECT_CONFIG'] = 'true'
    env['OPENCODE_PURE'] = 'true'
    command = ['opencode', 'run', '--pure', '--dir', str(workdir), '--format', 'json',
               '--agent', agent, '--model', model, '--title', 'Autocode planning conversation']
    code, output = _capture(command, env, workdir, _prompt(messages))
    if code:
        raise ConversationProviderError('OpenCode could not get a reply from GLM. Check the configured model and provider connection, then retry.')
    texts = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get('type') == 'error':
            raise ConversationProviderError('GLM could not finish its reply. Check the provider connection, then retry.')
        if event.get('type') == 'tool_use':
            raise ConversationProviderError('The planning provider attempted a tool call. This conversation only supports text; retry your message.')
        part = event.get('part')
        if event.get('type') == 'text' and isinstance(part, dict) and isinstance(part.get('text'), str):
            texts.append(part['text'])
    reply = '\n\n'.join(texts).strip()
    if not reply:
        raise ConversationProviderError('GLM returned no text. Your message is saved; retry when ready.')
    return reply


class ConversationStore:
    def __init__(self, root=None, provider=None):
        if root is None:
            home = Path(os.environ.get('AUTOCODE_HOME', '~/.autocode')).expanduser()
            dashboard = Path(os.environ.get('AUTOCODE_DASHBOARD_HOME', str(home / 'dashboard'))).expanduser()
            root = dashboard / 'conversations'
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.provider = provider or opencode_provider
        self.pool = ThreadPoolExecutor(max_workers=4, thread_name_prefix='planning-chat')
        self._lock = threading.RLock()
        self._closed = False
        self._leases = {}
        (self.root / '.locks').mkdir(exist_ok=True, mode=0o700)
        self._guard_depth = 0
        self._guard_fd = os.open(self.root / '.store.lock', os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        self._guard_cleanup = weakref.finalize(self, os.close, self._guard_fd)
        with self._guard():
            for path in self.root.glob('*.json'):
                if not _ID.fullmatch(path.stem):
                    continue
                try:
                    doc = self._load(path.stem)
                except (ValueError, OSError):
                    continue
                if doc['status'] == 'thinking':
                    try:
                        lease = self._lease(doc['id'])
                    except ValueError:
                        continue  # Another dashboard still owns this live turn.
                    try:
                        doc = self._load(doc['id'])
                        if doc['status'] == 'thinking':
                            doc['status'] = 'error'
                            doc['error'] = 'The dashboard restarted before GLM finished. Your message is saved; retry when ready.'
                            self._pending_message(doc)['status'] = 'error'
                            doc['_active_turn'] = None
                            self._save(doc)
                    finally:
                        lease.close()

    @contextmanager
    def _guard(self):
        with self._lock:
            outer = self._guard_depth == 0
            if outer:
                fcntl.flock(self._guard_fd, fcntl.LOCK_EX)
            self._guard_depth += 1
            try:
                yield
            finally:
                self._guard_depth -= 1
                if outer:
                    fcntl.flock(self._guard_fd, fcntl.LOCK_UN)

    def _path(self, conversation_id):
        if not isinstance(conversation_id, str) or not _ID.fullmatch(conversation_id):
            raise ValueError('Invalid conversation ID.')
        path = self.root / (conversation_id + '.json')
        if path.is_symlink():
            raise ValueError('Conversation files cannot be symbolic links.')
        return path

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
                or not isinstance(doc.get('messages'), list) or not doc['messages']
                or not all(isinstance(row, dict) and row.get('role') in ('user', 'assistant')
                           and isinstance(row.get('text'), str) and isinstance(row.get('id'), str)
                           for row in doc['messages'])
                or not any(row['role'] == 'user' for row in doc['messages'])
                or not all(isinstance(doc.get(key), str) for key in ('title', 'created_at', 'updated_at'))
                or not isinstance(doc.get('models'), dict)
                or not isinstance(doc['models'].get('glm_model'), str)
                or doc.get('status') not in ('thinking', 'ready', 'error')):
            raise ValueError('This saved conversation has an invalid format.')
        return doc

    def _save(self, doc):
        doc['updated_at'] = _now()
        payload = json.dumps(doc, ensure_ascii=False, indent=2) + '\n'
        path = self._path(doc['id'])
        temp = None
        try:
            with tempfile.NamedTemporaryFile(mode='w', encoding='utf-8', dir=self.root,
                                             prefix='.conversation-', delete=False) as handle:
                temp = Path(handle.name)
                os.chmod(temp, 0o600)
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp, path)
            temp = None
            fd = os.open(self.root, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        finally:
            if temp:
                temp.unlink(missing_ok=True)

    @staticmethod
    def _public(doc):
        return deepcopy({key: value for key, value in doc.items() if not key.startswith('_')})

    @staticmethod
    def _pending_message(doc):
        return next(row for row in reversed(doc['messages']) if row['role'] == 'user')

    def list(self, include_archived=False):
        with self._guard():
            result = []
            for path in self.root.glob('*.json'):
                if not _ID.fullmatch(path.stem):
                    continue
                try:
                    doc = self._load(path.stem)
                except (ValueError, OSError):
                    continue
                if doc.get('archived_at') and not include_archived:
                    continue
                summary = self._public(doc)
                summary['message_count'] = len(summary['messages'])
                summary['last_message'] = summary['messages'][-1]['text'] if summary['messages'] else ''
                summary.pop('messages')
                result.append(summary)
            return sorted(result, key=lambda item: item['updated_at'], reverse=True)

    def get(self, conversation_id):
        with self._guard():
            return self._public(self._load(conversation_id))

    @staticmethod
    def require_visible(doc):
        if doc.get('archived_at'):
            raise ValueError('Restore this archived conversation before continuing it.')

    def archive(self, conversation_id, action):
        if action not in ('archive', 'restore'):
            raise ValueError('Choose archive or restore for the conversation.')
        with self._guard():
            self._ensure_open()
            doc = self._load(conversation_id)
            if bool(doc.get('archived_at')) != (action == 'archive'):
                doc['archived_at'] = _now() if action == 'archive' else None
                self._save(doc)
            return self._public(doc)

    def create(self, text, models=None, request_id=None):
        text, models, request_id = _text(text), _models(models), _request_id(request_id)
        with self._guard():
            self._ensure_open()
            for summary in self.list(include_archived=True):
                doc = self._load(summary['id'])
                if doc.get('_create_request_id') == request_id:
                    if doc['messages'][0]['text'] != text or doc['models'] != models:
                        raise ValueError('That request ID was already used for a different conversation.')
                    return self._public(doc)
            created = _now()
            doc = {'id': uuid.uuid4().hex, 'title': ' '.join(text.split())[:80],
                   'created_at': created, 'updated_at': created, 'status': 'thinking', 'error': None,
                   'messages': [], 'models': models, 'attachment': None,
                   '_create_request_id': request_id, '_requests': {}}
            self._append_user(doc, text, request_id)
            return self._start(doc)

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
                raise ValueError('GLM is still replying. Wait for the response before sending another message.')
            if doc.get('attachment'):
                raise ValueError('This conversation is attached to a project. Continue in its task conversation.')
            if doc['status'] == 'error':
                raise ValueError('Retry the saved message before sending another one.')
            self._append_user(doc, text, request_id)
            return self._start(doc)

    def retry(self, conversation_id):
        with self._guard():
            self._ensure_open()
            doc = self._load(conversation_id)
            self.require_visible(doc)
            if doc['status'] == 'thinking':
                return self._public(doc)
            if doc.get('attachment'):
                raise ValueError('This conversation is attached to a project. Continue in its task conversation.')
            if doc['status'] != 'error':
                raise ValueError('There is no failed message to retry.')
            doc.update(status='thinking', error=None, _active_turn=uuid.uuid4().hex)
            self._pending_message(doc)['status'] = 'saved'
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
                    raise ValueError('Wait for GLM to finish before changing conversation models.')
                fields['models'] = _models(fields['models'])
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

    def claim_attachment(self, conversation_id, attachment, expected_attachment=None):
        """Claim one durable project handoff across dashboard processes."""
        if not isinstance(attachment, dict) or not attachment:
            raise ValueError('A project handoff needs attachment details.')
        with self._guard():
            self._ensure_open()
            doc = self._load(conversation_id)
            self.require_visible(doc)
            existing = doc.get('attachment')
            replacing_failed = (isinstance(expected_attachment, dict) and existing == expected_attachment
                                and isinstance(existing, dict) and existing.get('status') == 'failed')
            if existing is not None and not replacing_failed:
                return self._public(doc), False
            if expected_attachment is not None and not replacing_failed:
                return self._public(doc), False
            if doc['status'] != 'ready':
                raise ValueError('Wait for a complete GLM reply before attaching a project.')
            return self.update(conversation_id, attachment=attachment), True

    def _append_user(self, doc, text, request_id):
        message = {'id': uuid.uuid4().hex, 'role': 'user', 'speaker': 'You', 'text': text,
                   'created_at': _now(), 'status': 'saved'}
        proposed = [*doc['messages'], message]
        if len(proposed) + 1 > MAX_MESSAGES or len(_prompt(proposed)) + MAX_REPLY_CHARS > MAX_CONTEXT_CHARS:
            raise ValueError('This conversation has reached its context limit. Start a new conversation with a summary; no messages were discarded.')
        doc['messages'] = proposed
        doc.setdefault('_requests', {})[request_id] = message['id']
        doc.update(status='thinking', error=None, _active_turn=uuid.uuid4().hex)

    def _ensure_open(self):
        if self._closed:
            raise ValueError('The conversation service is shutting down. Retry shortly.')

    def _lease(self, conversation_id):
        self._path(conversation_id)
        fd = os.open(self.root / '.locks' / conversation_id, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
        lease = os.fdopen(fd, 'r+b')
        try:
            fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            lease.close()
            raise ValueError('GLM is still replying in another dashboard. Wait for the response before continuing.') from error
        return lease

    def _start(self, doc):
        lease = self._lease(doc['id'])
        key = (doc['id'], doc['_active_turn'])
        self._leases[key] = lease
        try:
            self._save(doc)
            self._dispatch(doc)
            return self._public(doc)
        except Exception:
            self._leases.pop(key, lease).close()
            raise

    def _dispatch(self, doc):
        try:
            self.pool.submit(self._reply, doc['id'], doc['_active_turn'])
        except RuntimeError:
            self._leases.pop((doc['id'], doc['_active_turn'])).close()
            doc.update(status='error', error='The conversation service stopped before GLM could reply. Retry shortly.', _active_turn=None)
            self._pending_message(doc)['status'] = 'error'
            self._save(doc)

    def _reply(self, conversation_id, turn_id):
        try:
            self._reply_locked(conversation_id, turn_id)
        finally:
            with self._guard():
                lease = self._leases.pop((conversation_id, turn_id), None)
                if lease:
                    lease.close()

    def _reply_locked(self, conversation_id, turn_id):
        with self._guard():
            doc = self._load(conversation_id)
            messages, model = deepcopy(doc['messages']), doc['models']['glm_model']
        try:
            workdir = self.root / 'scratch' / conversation_id
            workdir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if workdir.is_symlink() or self.root not in workdir.resolve().parents:
                raise ConversationProviderError('The conversation scratch directory is invalid.')
            response = self.provider(messages, model, workdir)
            if not isinstance(response, str) or not response.strip():
                raise ConversationProviderError('GLM returned no text. Your message is saved; retry when ready.')
            if len(response) > MAX_REPLY_CHARS:
                raise ConversationProviderError('GLM returned an oversized reply. Your message is saved; retry with a narrower request.')
            error = None
        except ConversationProviderError as exc:
            error = str(exc)
        except Exception:
            # Exceptions/CLI diagnostics may contain credentials. Display only
            # controlled messages; never persist provider output or environment.
            error = 'GLM could not reply. Your message is saved; check the provider connection and retry.'
        with self._guard():
            doc = self._load(conversation_id)
            if doc.get('_active_turn') != turn_id:
                return
            user = self._pending_message(doc)
            user['status'] = 'error' if error else 'received'
            doc.update(status='error' if error else 'ready', error=error, _active_turn=None)
            if not error:
                doc['messages'].append({'id': uuid.uuid4().hex, 'role': 'assistant', 'speaker': 'GLM',
                                        'text': response.strip(), 'created_at': _now(), 'status': 'received'})
            self._save(doc)
            # Readers must not observe ready/error while the completed turn
            # still owns its lease: a followup or retry can arrive immediately.
            # Keep the outer cleanup for failures before this durable save.
            lease = self._leases.pop((conversation_id, turn_id), None)
            if lease:
                lease.close()

    def close(self, wait=True):
        with self._guard():
            self._closed = True
        self.pool.shutdown(wait=wait)
