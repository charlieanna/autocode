"""Project-free conversations and their explicit handoff to the task runner."""
import copy
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import threading
import time
import uuid

try:
    from .dashboard_monitor import snapshot
    from .dashboard_metrics import project_metrics
except ImportError:  # Direct source launch, as well as the installed entry point.
    from dashboard_monitor import snapshot
    from dashboard_metrics import project_metrics


def object_value(value):
    return value if isinstance(value, dict) else {}


def planning_messages(state, run=None):
    result, seen = [], set()
    stages = [record for record in state.get('stages', []) if isinstance(record, dict)] if isinstance(state.get('stages'), list) else []
    by_output = {record.get('output'): record for record in stages if record.get('output')}
    cycles = list(state.get('planning_history', [])) if isinstance(state.get('planning_history'), list) else []
    cycles.append(object_value(state.get('planning')))
    for number, cycle in enumerate(cycles):
        for stage, entry in object_value(object_value(cycle).get('reports')).items():
            entry = object_value(entry)
            report = object_value(entry.get('report'))
            summary = report.get('summary')
            if isinstance(summary, str) and summary:
                record = by_output.get(entry.get('output'), {})
                result.append({'id': f'planning-{number}-{stage}', 'role': 'assistant',
                               'speaker': 'GLM' if stage in ('astra_discovery', 'glm_revise') else 'Astra',
                               'text': summary, 'stage': stage, 'status': 'received',
                               'created_at': record.get('finished_at') or record.get('started_at')})
                if entry.get('output'):
                    seen.add(entry['output'])
    # Clarification rounds can finish before the joint report exchange starts.
    # Keep those actual replies in the timeline, reading only contained artifacts.
    if run:
        for record in stages:
            if record.get('output') in seen or record.get('stage') != 'astra_discovery' or record.get('rejected') or record.get('exit_code') not in (0, None):
                continue
            try:
                path = Path(record['output']).resolve(strict=True)
                path.relative_to(Path(run).resolve(strict=True))
                if path.stat().st_size > 524288:
                    continue
                report = json.loads(path.read_text())
                text = object_value(report).get('summary')
                if isinstance(text, str) and text:
                    result.append({'id': 'discovery-' + hashlib.sha256(str(path).encode()).hexdigest()[:16],
                                   'role': 'assistant', 'speaker': 'GLM' if record.get('role') == 'glm' else 'Astra',
                                   'text': text, 'status': 'received', 'created_at': record.get('finished_at') or record.get('started_at')})
            except (KeyError, TypeError, OSError, ValueError):
                continue
    return result


class ConversationMixin:
    def __init__(self, *args, conversation_root=None, conversation_provider=None, **kwargs):
        self._conversation_store = None
        self._conversation_root = conversation_root
        self._conversation_provider = conversation_provider
        self.chat_lock = threading.RLock()
        self._chat_leases = {}
        super().__init__(*args, **kwargs)

    @property
    def conversations(self):
        with self.chat_lock:
            if self._conversation_store is None:
                try:
                    from .dashboard_conversations import ConversationStore
                except ImportError:  # Support direct execution from this source directory.
                    from dashboard_conversations import ConversationStore
                self._conversation_store = ConversationStore(root=self._conversation_root, provider=self._conversation_provider)
            return self._conversation_store

    def conversation_create(self, data):
        models = data.get('models', {})
        if not isinstance(models, dict):
            raise ValueError('Models must be an object')
        chosen = self.joint_models(models)
        return self.conversations.create(data.get('text'), models={key + '_model': value for key, value in chosen.items()}, request_id=data.get('request_id'))

    def _attachment_state(self, doc):
        attachment = object_value(doc.get('attachment'))
        if not attachment or attachment.get('status') == 'linked':
            return doc
        workspace = self.selected_workspace(attachment.get('workspace'))
        if not workspace:
            return self.conversations.update(doc['id'], attachment={**attachment, 'status': 'failed', 'error': 'The attached project is unavailable.'})
        found = []
        root = workspace / '.autocode/runs'
        try:
            root.resolve().relative_to(workspace)
        except ValueError:
            return self.conversations.update(doc['id'], attachment={**attachment, 'status': 'failed', 'error': 'Task storage escapes the attached project. Fix the project storage path before retrying.'})
        for path in root.iterdir() if root.is_dir() else []:
            if path.is_symlink() or not path.is_dir() or not self._contained_run(workspace, str(path), identity=True):
                continue
            path = path.resolve()
            try:
                state_path = path / 'state.json'
                if state_path.is_symlink():
                    continue
                state = json.loads(state_path.read_text())
                if isinstance(state.get('task'), str) and hashlib.sha256(state['task'].encode()).hexdigest() == attachment.get('goal_hash') and state.get('workspace') == str(workspace):
                    found.append(path)
            except (OSError, ValueError):
                continue
        if len(found) == 1:
            if workspace not in self.created_workspaces:
                self.created_workspaces.append(workspace)
            return self.conversations.update(doc['id'], attachment={**attachment, 'status': 'linked', 'run': str(found[0]), 'error': None})
        if len(found) > 1:
            return self.conversations.update(doc['id'], attachment={**attachment, 'status': 'uncertain', 'error': 'Multiple task checkpoints match this conversation. Inspect the project before starting anything else.'})
        action = next((a for a in self.action_log(workspace) if a['id'] == attachment.get('action_id')), None)
        if action and action['status'] in ('queued', 'running'):
            return doc
        if action:
            return self.conversations.update(doc['id'], attachment={**attachment, 'status': 'failed', 'error': (action.get('stderr') or action.get('stdout') or 'Task creation ended before a checkpoint was saved.')[-2400:]})
        if attachment.get('status') == 'starting':
            return self.conversations.update(doc['id'], attachment={**attachment, 'status': 'uncertain', 'error': 'The dashboard restarted during project handoff. No second task has been launched. Check the project task list for the original attempt.'})
        return doc

    def conversation_get(self, ident):
        with self.chat_lock:
            doc = self.conversations.get(ident)
            return doc if doc.get('archived_at') else self._attachment_state(doc)

    def conversation_archive(self, data):
        with self.chat_lock:
            return self.conversations.archive(data.get('id'), data.get('action'))

    def conversation_list(self):
        # Reconcile handoffs without discarding a conversation's durable history.
        for summary in self.conversations.list():
            if summary.get('attachment') and summary['attachment'].get('status') != 'linked':
                self.conversation_get(summary['id'])
        return self.conversations.list()

    def conversation_attach(self, data):
        with self.chat_lock:
            doc = self.conversation_get(data.get('id'))
            self.conversations.require_visible(doc)
            expected_attachment = None
            if doc.get('attachment'):
                attachment = doc['attachment']
                if data.get('retry') is not True or attachment.get('status') != 'failed':
                    return doc  # repeated clicks or uncertain client response never launch twice
                action = next((a for a in self.action_log(attachment['workspace']) if a['id'] == attachment.get('action_id')), None)
                if not attachment.get('launch_rejected') and (not action or action.get('status') in ('queued', 'running')):
                    raise ValueError('The original handoff cannot be confirmed as stopped. Inspect the project task list before starting another task.')
                expected_attachment = copy.deepcopy(attachment)
                data = {**data, 'workspace': attachment['workspace'], 'project': '', 'create_project': False}

            if doc.get('status') != 'ready':
                raise ValueError('Wait for GLM’s reply before attaching a project')
            self.joint_models(doc.get('models', {}))
            raw = data.get('project') or data.get('workspace')
            if not isinstance(raw, str) or not raw.strip():
                raise ValueError('Choose an existing project or enter a new project path')
            if data.get('create_project') is True:
                project = Path(raw).expanduser()
                if not project.is_absolute():
                    raise ValueError('Enter an absolute path for the new project')
                if project.exists() or project.is_symlink():
                    raise ValueError('That path already exists. Choose an existing project or use a new folder name.')
                parent = project.parent.resolve(strict=True)
                if not parent.is_dir():
                    raise ValueError('The parent directory must exist')
                project = parent / project.name
                project.mkdir(mode=0o700)
                try:
                    for args in (['git', 'init', '-q', str(project)], ['git', '-C', str(project), '-c', 'user.name=Autocode', '-c', 'user.email=autocode@localhost', 'commit', '--allow-empty', '-qm', 'Initialize project']):
                        subprocess.run(args, check=True, capture_output=True, text=True, timeout=15)
                except (OSError, subprocess.SubprocessError) as error:
                    raise ValueError('Project initialization did not finish. The new folder was retained: ' + str(project)) from error
                raw = str(project)
            workspace = self.selected_workspace(raw)
            if not workspace:
                raise ValueError('Select an existing Git project or create a new one')
            try:
                (workspace / '.autocode/runs').resolve().relative_to(workspace)
            except ValueError as error:
                raise ValueError('Task storage must stay inside the selected project') from error
            transcript = '\n\n'.join(f"{message.get('speaker', message.get('role', 'Message'))}:\n{message.get('text', '')}" for message in doc['messages'])
            goal = (doc['title'] + '\n\nConversation reference: ' + doc['id'] +
                    '\nThe following is the user’s saved project-free planning discussion. Use it as context, including corrections. '
                    'Inspect this repository, resolve remaining questions, and run the GLM/Astra joint planning process. '
                    'Prior discussion is a draft, not approval to implement. Present the final repository-aware plan for explicit approval.\n\n' + transcript)
            attachment = {'status': 'starting', 'workspace': str(workspace), 'goal_hash': hashlib.sha256(goal.encode()).hexdigest(), 'started_at': time.time(), 'run': None, 'action_id': None, 'error': None}
            claimed_doc, claimed = self.conversations.claim_attachment(doc['id'], attachment, expected_attachment=expected_attachment)
            if not claimed:
                return claimed_doc
            try:
                action = self.create({'workspace': str(workspace), 'project': '', 'goal': goal, 'engine': 'opencode', **doc.get('models', {})})
            except Exception as error:
                self.conversations.update(doc['id'], attachment={**attachment, 'status': 'failed', 'error': str(error), 'launch_rejected': isinstance(error, ValueError)})
                raise ValueError('Conversation saved, but project handoff failed: ' + str(error)) from error
            return self.conversations.update(doc['id'], attachment={**attachment, 'action_id': action['id']})

    def _chat_path(self, run):
        root = self.conversations.root.parent / 'task-chat'
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        return root / (hashlib.sha256(str(run).encode()).hexdigest() + '.json')

    @contextmanager
    def _chat_guard(self, run):
        # Serialize receipt read/modify/write and request-ID checks across servers.
        with self.chat_lock:
            key = str(run)
            lease = self._chat_leases.get(key)
            if lease is None:
                path = self._chat_path(run).with_suffix('.lock')
                fd = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                fcntl.flock(fd, fcntl.LOCK_EX)
                lease = self._chat_leases[key] = [fd, 0]
            lease[1] += 1
            try:
                yield
            finally:
                lease[1] -= 1
                if not lease[1]:
                    os.close(lease[0])
                    del self._chat_leases[key]

    def _chat_rows(self, run):
        path = self._chat_path(run)
        if not path.exists():
            return []
        value = json.loads(path.read_text())
        if not isinstance(value, list):
            raise ValueError('Saved conversation receipts are unreadable')
        return value

    def _save_chat(self, run, row):
        with self._chat_guard(run):
            rows = self._chat_rows(run)
            index = next((i for i, item in enumerate(rows) if item['id'] == row['id']), None)
            if index is None:
                rows.append(copy.deepcopy(row))
            else:
                rows[index] = copy.deepcopy(row)
            path = self._chat_path(run)
            temporary = path.with_suffix('.' + uuid.uuid4().hex + '.tmp')
            temporary.write_text(json.dumps(rows))
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        return row

    def chat(self, data):
        workspace = self.workspace_for(data.get('workspace', ''))
        run = self.run_for(workspace, data.get('run', ''))
        if not run:
            raise ValueError('Task does not belong to an available project')
        ident, text = data.get('request_id'), data.get('text', '')
        if not isinstance(ident, str) or not re.fullmatch(r'[A-Za-z0-9_-]{8,100}', ident):
            raise ValueError('A stable request ID is required')
        if not isinstance(text, str) or len(text) > 16000 or (not text.strip() and data.get('delegate') is not True):
            raise ValueError('Enter a message of up to 16000 characters')
        question_id = data.get('question_id')
        with self._chat_guard(run):
            previous = next((item for item in self._chat_rows(run) if item['id'] == ident), None)
            if previous:
                if previous.get('submitted_text') != text or previous.get('question_id') != question_id or previous.get('delegate') != (data.get('delegate') is True):
                    raise ValueError('This request ID was already used for a different message')
                if data.get('retry') is not True or previous.get('status') != 'error':
                    return previous
            view = self.view(workspace, run)
            questions = view.get('questions', [])
            if questions and not question_id:
                raise ValueError('Choose which question this message answers')
            question = next((q for q in questions if q.get('id') == question_id), None)
            if question_id and not question:
                raise ValueError('That question is no longer pending. Your message was not sent.')
            row = {'id': ident, 'role': 'user', 'speaker': 'You', 'text': text, 'submitted_text': text, 'question_id': question_id,
                   'question_text': question.get('question') if question else None, 'delegate': data.get('delegate') is True,
                   'created_at': previous.get('created_at') if previous else time.time(), 'status': 'saved', 'error': None}
            self._save_chat(run, row)
            if question:
                extra = ['--delegate', question_id] if row['delegate'] else ['--answer', question_id + '=' + text]
                if row['delegate']:
                    row['text'] = 'Use the suggested default: ' + str(question.get('proposed_default', ''))
                def done(action):
                    if action.get('exit_status') != 0:
                        row.update(status='error', error=(action.get('stderr') or 'Could not save the answer. Your message is retained.')[-2400:])
                        self._save_chat(run, row)
                        return
                    row.update(status='received', error=None)
                    self._save_chat(run, row)
                    try:
                        saved = json.loads((run / 'state.json').read_text())
                        if not saved.get('pending_questions'):
                            followup = self.continue_run(workspace, run)
                            row['continue_action_id'] = followup['id']
                            self._save_chat(run, row)
                    except (ValueError, OSError) as error:
                        row['notice'] = 'Answer saved. Use Continue task to advance: ' + str(error)
                        self._save_chat(run, row)
                try:
                    action = self.enqueue(workspace, run, 'Send answer', extra + ['--no-chat'], on_complete=done)
                    row['action_id'] = action['id']
                    self._save_chat(run, row)
                except Exception as error:
                    row.update(status='error', error=str(error))
                    self._save_chat(run, row)
                return copy.deepcopy(row)
            try:
                receipt = self.intervene(workspace, run, 'feedback', text, ident)
                failed = receipt.get('status') in ('failed', 'uncertain', 'launch_failed')
                row.update(status='error' if failed else 'received', receipt=receipt, delivery_status=receipt.get('status'), error=receipt.get('error') if failed else None)
            except Exception as error:
                row.update(status='error', error=str(error))
            return copy.deepcopy(self._save_chat(run, row))

    def task_view(self, workspace, run):
        try:
            state = json.loads((run / 'state.json').read_text())
            if not isinstance(state, dict):
                raise ValueError('State is not an object')
            view = self.view(workspace, run, state)
            view['monitor'] = snapshot(state, run, detailed=True)
            view['monitor']['metrics'] = project_metrics(workspace, run)
        except (OSError, ValueError):
            view = self.view(workspace, run)
        actions = self.action_log(workspace, run)
        if view.get('startup_action'):
            actions = [view.pop('startup_action'), *actions]
        return {**view, 'actions': actions}

    def discover(self):
        result = super().discover()
        if self._conversation_store is not None:
            titles = {object_value(doc.get('attachment')).get('run'): doc['title'] for doc in self.conversations.list() if doc.get('attachment')}
            for row in result:
                if row.get('run') in titles:
                    row['original_task'] = row.get('task')
                    row['task'] = titles[row['run']]
        return result

    def view(self, workspace, run, s=None):
        view = super().view(workspace, run, s)
        # Avoid creating any new local files merely to poll pre-existing tasks.
        state = s
        if state is None:
            try:
                state = json.loads((run / 'state.json').read_text())
            except (OSError, ValueError):
                state = {}
        view['planning_messages'] = planning_messages(object_value(state), run)
        view['validation'] = object_value(object_value(state).get('validation'))
        view['draft_messages'] = []
        view['chat_messages'] = []
        if self._conversation_store is not None:
            with self.chat_lock:
                try:
                    view['chat_messages'] = self._chat_rows(run)
                except (OSError, ValueError) as error:
                    view['chat_error'] = str(error)
                for summary in self.conversations.list(include_archived=True):
                    attachment = object_value(summary.get('attachment'))
                    if attachment.get('run') == str(run) and attachment.get('workspace') == str(workspace):
                        doc = self.conversations.get(summary['id'])
                        view['draft_messages'] = doc['messages']
                        view['conversation_id'] = doc['id']
                        view['original_task'] = view.get('task')
                        view['task'] = doc['title']
                        startup = next((a for a in self.action_log(workspace) if a['id'] == attachment.get('action_id')), None)
                        if startup:
                            view['startup_action'] = startup
                        break
                deliveries = {entry['id']: entry for entry in view.get('interventions', {}).get('entries', []) if 'id' in entry}
                local_actions = {action['id']: action for action in self.action_log(workspace, run)}
                for message in view['chat_messages']:
                    if message.get('status') == 'saved' and message.get('question_id') and message.get('action_id') not in local_actions:
                        answer = object_value(object_value(state).get('answers')).get(message['question_id'])
                        saved_text = object_value(answer).get('text') or object_value(answer).get('answer')
                        if saved_text == message.get('submitted_text') and not message.get('delegate'):
                            message.update(status='received', error=None)
                        else:
                            message.update(status='error', error='Your message is saved, but its delivery could not be confirmed after restart. Retry this same message after checking the current question.')
                        self._save_chat(run, message)
                    delivery = deliveries.get(message['id'])
                    if delivery:
                        message['delivery_status'] = delivery.get('status')
                        if delivery.get('status') in ('applied', 'resumed'):
                            message['status'] = 'applied'
        return view
