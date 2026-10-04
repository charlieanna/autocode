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
    from .. import autocode_conversation as conversation_protocol
except ImportError:
    import autocode_conversation as conversation_protocol

try:
    from .dashboard_conversation_journal import project_conversation, append_feedback
    from .dashboard_monitor import snapshot
    from .dashboard_metrics import project_metrics
    from . import dashboard_chat_intent as chat_intent
    from .dashboard_transcript import project as transcript
    from .dashboard_screenshots import project as screenshot_evidence
    from .dashboard_work_summary import project as work_summary
    from .dashboard_verification import VerificationViewMixin
except ImportError:  # Direct source launch, as well as the installed entry point.
    from dashboard_conversation_journal import project_conversation, append_feedback
    from dashboard_monitor import snapshot
    from dashboard_metrics import project_metrics
    import dashboard_chat_intent as chat_intent
    from dashboard_transcript import project as transcript
    from dashboard_screenshots import project as screenshot_evidence
    from dashboard_work_summary import project as work_summary
    from dashboard_verification import VerificationViewMixin


def object_value(value):
    return value if isinstance(value, dict) else {}


def planning_messages(state, run=None):
    """Internal report history for inspection, never actionable chat requests."""
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
                               'speaker': 'Planner' if stage in ('astra_discovery', 'glm_revise') else 'Plan Reviewer',
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
                                   'role': 'assistant', 'speaker': 'Planner' if record.get('role') == 'glm' else 'Plan Reviewer',
                                   'text': text, 'status': 'received', 'created_at': record.get('finished_at') or record.get('started_at')})
            except (KeyError, TypeError, OSError, ValueError):
                continue
    return result


class ConversationMixin(VerificationViewMixin):
    def __init__(self, *args, conversation_root=None, conversation_provider=None, conversation_planner=None, **kwargs):
        self._conversation_store = None
        self._conversation_root = conversation_root
        self._conversation_provider = conversation_provider
        self._conversation_planner = conversation_planner
        self.chat_lock = threading.RLock()
        self._chat_leases = {}
        super().__init__(*args, **kwargs)

    @property
    def conversations(self):
        with self.chat_lock:
            if self._conversation_store is None:
                try:
                    from .conversation_store import ConversationStore
                except ImportError:  # Support direct execution from this source directory.
                    from conversation_store import ConversationStore
                self._conversation_store = ConversationStore(root=self._conversation_root, provider=self._conversation_provider, planner=self._conversation_planner)
            return self._conversation_store

    def conversation_create(self, data):
        text = data.get('text')
        if data.get('empty') is True and (not isinstance(text, str) or not text.strip()):
            # Pre-send creation: activating a New conversation control opens a
            # saved empty conversation carrying the validated project scope
            # before any message exists. The first send continues this record.
            workspace = None
            raw = data.get('project') or data.get('workspace')
            if isinstance(raw, str) and raw.strip():
                workspace = self.selected_workspace(raw)
                if not workspace:
                    raise ValueError('Select an existing Git project for the new conversation')
            return self.conversations.create_empty(str(workspace) if workspace else None,
                                                   request_id=data.get('request_id'))
        models = data.get('models', {})
        if not isinstance(models, dict):
            raise ValueError('Models must be an object')
        chosen = self.joint_models(models)
        efforts = self.joint_efforts(models)
        settings = {key + '_model': value for key, value in chosen.items()}
        settings.update({key + '_reasoning_effort': value for key, value in efforts.items()})
        # Creation-time project scoping: the saved record starts inside the
        # chosen project without launching a task handoff. Attaching a project
        # to an existing conversation stays a separate explicit action.
        workspace = None
        raw = data.get('project') or data.get('workspace')
        if isinstance(raw, str) and raw.strip():
            workspace = self.selected_workspace(raw)
            if not workspace:
                raise ValueError('Select an existing Git project for the new conversation')
        return self.conversations.create(text, models=settings, request_id=data.get('request_id'),
                                         workspace=str(workspace) if workspace else None)

    def _attachment_state(self, doc):
        attachment = object_value(doc.get('attachment'))
        if not attachment or attachment.get('status') == 'linked':
            return doc
        workspace = self.selected_workspace(attachment.get('workspace'))
        if not workspace:
            return self.conversations.update(doc['id'], attachment={**attachment, 'status': 'failed', 'error': 'The attached project is unavailable.'})
        try:
            (workspace / '.autocode/runs').resolve().relative_to(workspace)
        except ValueError:
            return self.conversations.update(doc['id'], attachment={**attachment, 'status': 'failed', 'error': 'Task storage escapes the attached project. Fix the project storage path before retrying.'})
        found = []
        candidates = [workspace]
        # New CLI tasks live in independent worktrees. Include them after a
        # dashboard restart, before relying on registry discovery or action logs.
        worktrees = workspace / '.autocode/worktrees'
        safe_storage = worktrees.resolve().is_relative_to(workspace)
        for child in worktrees.iterdir() if safe_storage and worktrees.is_dir() else []:
            if child.is_symlink() or not child.is_dir():
                continue
            try:
                metadata_path = child / '.autocode/task-workspace.json'
                if metadata_path.is_symlink() or not metadata_path.resolve().is_relative_to(child.resolve()):
                    continue
                meta = json.loads(metadata_path.read_text())
                if meta.get('project_workspace') == str(workspace) and meta.get('workspace') == str(child.resolve()):
                    candidates.append(child.resolve())
            except (OSError, ValueError):
                continue
        for candidate in candidates:
            root = candidate / '.autocode/runs'
            try:
                root.resolve().relative_to(candidate)
            except ValueError:
                continue
            for path in root.iterdir() if root.is_dir() else []:
                if path.is_symlink() or not path.is_dir() or not self._contained_run(candidate, str(path), identity=True):
                    continue
                path = path.resolve()
                try:
                    state_path = path / 'state.json'
                    if state_path.is_symlink():
                        continue
                    state = json.loads(state_path.read_text())
                    if isinstance(state.get('task'), str) and hashlib.sha256(state['task'].encode()).hexdigest() == attachment.get('goal_hash') and state.get('workspace') == str(candidate):
                        if attachment.get('handoff_digest'):
                            pointer = object_value(state.get('conversation_handoff'))
                            if (pointer.get('conversation_id') != doc['id']
                                    or pointer.get('digest') != attachment['handoff_digest']):
                                continue
                            journal = conversation_protocol.read_journal(path)
                            if (journal['conversation_id'] != doc['id']
                                    or journal['handoff_digest'] != attachment['handoff_digest']):
                                continue
                        found.append((candidate, path))
                except (OSError, ValueError):
                    continue
        if len(found) == 1:
            candidate, run = found[0]
            if candidate not in self.created_workspaces:
                self.created_workspaces.append(candidate)
            return self.conversations.update(doc['id'], attachment={**attachment, 'status': 'linked', 'project_workspace': str(workspace), 'workspace': str(candidate), 'run': str(run), 'error': None})
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
                raise ValueError('Wait for the current conversation reply before attaching a project')
            self.joint_models(doc.get('models', {}))
            self.joint_efforts(doc.get('models', {}))
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
            handoff = self.conversations.handoff(doc['id'])
            transcript = '\n\n'.join(f"{message.get('speaker', message.get('role', 'Message'))}:\n{message.get('text', '')}" for message in doc['messages'])
            goal = (doc['title'] + '\n\nConversation reference: ' + doc['id'] +
                    '\nThe following is the user’s saved project-free planning discussion. Use it as context, including corrections. '
                    'Inspect this repository, resolve remaining questions, and run the Planner/Plan Reviewer joint planning process. '
                    'Prior discussion is a draft, not approval to implement. Present the final repository-aware plan for explicit approval.\n\n' + transcript
                    + '\n\nStructured draft context (unapproved):\n' + json.dumps(handoff.get('plan_drafts', []), ensure_ascii=False))
            staged = (conversation_protocol.stage_handoff(workspace, handoff)
                      if self.conversations.is_continuous(doc['id']) else None)
            attachment = {'status': 'starting', 'workspace': str(workspace), 'goal_hash': hashlib.sha256(goal.encode()).hexdigest(), 'started_at': time.time(), 'run': None, 'action_id': None, 'error': None}
            if staged is not None:
                attachment.update(handoff=str(staged), handoff_digest=handoff['digest'])
            claimed_doc, claimed = self.conversations.claim_attachment(doc['id'], attachment, expected_attachment=expected_attachment)
            if not claimed:
                return claimed_doc
            try:
                action = self.create({'workspace': str(workspace), 'project': '', 'goal': goal, 'engine': 'opencode',
                                      **({'conversation_handoff': str(staged)} if staged is not None else {}),
                                      **doc.get('models', {})})
            except Exception as error:
                self.conversations.update(doc['id'], attachment={**attachment, 'status': 'failed', 'error': str(error), 'launch_rejected': isinstance(error, ValueError)})
                raise ValueError('Conversation saved, but project handoff failed: ' + str(error)) from error
            return self.conversations.update(doc['id'], attachment={**attachment, 'action_id': action['id']})

    def _chat_path(self, run):
        if self._conversation_store is not None:
            conversation_root = self._conversation_store.root
        elif self._conversation_root is not None:
            conversation_root = Path(self._conversation_root).expanduser().resolve()
        else:
            home = Path(os.environ.get('AUTOCODE_HOME', '~/.autocode')).expanduser()
            conversation_root = Path(os.environ.get('AUTOCODE_DASHBOARD_HOME', str(home / 'dashboard'))).expanduser() / 'conversations'
        root = conversation_root.parent / 'task-chat'
        return root / (hashlib.sha256(str(run).encode()).hexdigest() + '.json')

    @contextmanager
    def _chat_guard(self, run):
        # Serialize receipt read/modify/write and request-ID checks across servers.
        with self.chat_lock:
            key = str(run)
            lease = self._chat_leases.get(key)
            if lease is None:
                path = self._chat_path(run).with_suffix('.lock')
                path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
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
            try:
                with temporary.open('x') as handle:
                    os.chmod(temporary, 0o600)
                    handle.write(json.dumps(rows))
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, path)
                directory = os.open(path.parent, os.O_RDONLY)
                try:
                    os.fsync(directory)
                finally:
                    os.close(directory)
            finally:
                temporary.unlink(missing_ok=True)
        return row

    def _reconcile_chat_journal(self, run, view, state):
        """Repair the local receipt/journal gap using accepted runner evidence only."""
        if not conversation_protocol.journal_file(run).exists() or not self._chat_path(run).exists():
            return
        deliveries = {row.get('id'): row for row in object_value(view.get('interventions')).get('entries', [])}
        with self._chat_guard(run):
            committed = {row['id'] for row in conversation_protocol.read_journal(run)['conversation']['messages']}
            for saved in self._chat_rows(run):
                row = copy.deepcopy(saved)
                question_id = row.get('question_id')
                if question_id:
                    answer = object_value(object_value(state.get('answers')).get(question_id))
                    expected = row.get('delegated_default') if row.get('delegate') else row.get('submitted_text')
                    confirmed = (isinstance(expected, str) and bool(expected)
                                 and answer.get('text', answer.get('answer')) == expected
                                 and answer.get('question_id', question_id) == question_id
                                 and (not row.get('delegate') or answer.get('kind') == 'delegated'))
                    if not confirmed:
                        continue
                    row.update(status='received', error=None)
                    product_change, token = False, row.get('prior_goal_token')
                else:
                    delivery = object_value(deliveries.get(row['id']))
                    if (delivery.get('durable') is not True or delivery.get('legacy')
                            or delivery.get('status') not in ('queued', 'applied', 'resumed')
                            or delivery.get('kind') != 'feedback'
                            or delivery.get('text') != row.get('submitted_text')):
                        continue
                    row.update(status='applied' if delivery['status'] in ('applied', 'resumed') else 'received',
                               error=None, receipt=copy.deepcopy(delivery), delivery_status=delivery['status'])
                    product_change = True
                    token = delivery.get('observed_goal_token', row.get('prior_goal_token'))
                if 'task-' + row['id'] not in committed:
                    append_feedback(run, row, product_change=product_change, goal_token=token)
                if row != saved:
                    self._save_chat(run, row)

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
        explicit_answer = data.get('explicit_answer') is True
        if explicit_answer and (not question_id or data.get('decision') or data.get('delegate')):
            raise ValueError('An explicit answer must target one current question')
        read_only = not explicit_answer and chat_intent.classify(text, question_id) in ('question', 'control', 'approval')
        if read_only:
            # The composer may already target a question. Asking about status
            # or typing a control must not accidentally answer that question.
            question_id = None
            data = {**data, 'question_id': None, 'delegate': False,
                    'resolver_request': None, 'resolver_token': None}
        with self._chat_guard(run):
            previous = next((item for item in self._chat_rows(run) if item['id'] == ident), None)
            decision = data.get('decision')
            if decision and not previous:
                raise ValueError('Review the saved message before confirming a change')
            if previous:
                if (previous.get('submitted_text') != text or previous.get('question_id') != question_id
                        or bool(previous.get('explicit_answer')) != explicit_answer
                        or previous.get('delegate') != (data.get('delegate') is True)
                        or previous.get('resolver_request') != data.get('resolver_request')
                        or previous.get('resolver_token') != data.get('resolver_token')):
                    raise ValueError('This request ID was already used for a different message')
                # A repeat can close a crash window without submitting anything.
                # view() reconciles only matching durable receipt/answer evidence.
                if conversation_protocol.journal_file(run).exists():
                    self.view(workspace, run)
                    previous = next(item for item in self._chat_rows(run) if item['id'] == ident)
                lost_action = (previous.get('status') == 'saved' and previous.get('question_id')
                               and previous.get('action_id') not in {row['id'] for row in self.action_log(workspace, run)})
                if decision:
                    pending_decision = (previous.get('confirmation') or {}).get('status') == 'pending'
                    previous = chat_intent.decide(copy.deepcopy(previous), decision, data.get('decision_token'),
                                                  self.view(workspace, run), time.time())
                    if (not pending_decision and data.get('retry') is not True
                            or previous.get('kind') != 'correction' or previous.get('status') not in ('saved', 'error')):
                        return copy.deepcopy(self._save_chat(run, previous))
                    # Save a confirmed correction only after the current request
                    # and plan checks below allow delivery. A new question must
                    # not consume the pending confirmation without an effect.
                elif data.get('retry') is not True or (previous.get('status') != 'error' and not lost_action):
                    return previous
            view = self.view(workspace, run)
            if read_only and chat_intent.classify(text) == 'question':
                self.inspect_verification(workspace, run, view)
            scope = object_value(view.get('human_escalation')).get('scope')
            questions = view.get('questions', []) if scope in ('clarification', 'permission', 'goal_change') else []
            if questions and not question_id and not read_only:
                raise ValueError('Choose which question this message answers')
            question = next((q for q in questions if q.get('id') == question_id), None)
            if question_id and not question:
                raise ValueError('That question is no longer pending. Your message was not sent.')
            if question:
                public = self.require_human_response(view, data, ('clarification', 'permission', 'goal_change'))
            elif data.get('delegate') or data.get('resolver_request') or data.get('resolver_token'):
                raise ValueError('That question is no longer pending. Your message was not sent.')
            elif not read_only and object_value(view.get('human_escalation')).get('scope') in ('operational_exhaustion', 'blocker'):
                raise ValueError('Use the current AutoResolver response action; feedback does not resolve this request.')
            if (previous and previous.get('kind') == 'correction' and previous.get('confirmation')
                    and previous['confirmation'].get('goal_token') != view.get('goal_token')):
                raise ValueError('The plan changed since this confirmation. Send a new message to review the current plan.')
            row = {'id': ident, 'role': 'user', 'speaker': 'You', 'text': text, 'submitted_text': text, 'question_id': question_id,
                   'question_text': question.get('question') if question else None, 'delegate': data.get('delegate') is True,
                   'explicit_answer': explicit_answer,
                   'resolver_request': data.get('resolver_request'), 'resolver_token': data.get('resolver_token'),
                   'prior_goal_token': previous.get('prior_goal_token') if previous else view.get('goal_token'),
                   'created_at': previous.get('created_at') if previous else time.time(), 'status': 'saved', 'error': None}
            if previous and previous.get('confirmation'):
                row.update(kind=previous['kind'], confirmation=copy.deepcopy(previous['confirmation']),
                           classification_rule=previous.get('classification_rule'), reply=previous.get('reply'))
            elif previous and not previous.get('kind') and not question:
                # Previously submitted durable feedback keeps its original authority on retry.
                row.update(kind='correction', classification_rule='legacy-explicit-feedback')
            elif question and explicit_answer:
                row.update(kind='answer', classification_rule='explicit-question-card')
            else:
                chat_intent.prepare(row, view)
            if not question and row['kind'] != 'correction':
                return copy.deepcopy(self._save_chat(run, row))
            if question and row['delegate']:
                row['delegated_default'] = question.get('proposed_default', '')
                row['text'] = 'Use the suggested default: ' + row['delegated_default']
            self._save_chat(run, row)
            if question:
                extra = ['--delegate', question_id] if row['delegate'] else ['--answer', question_id + '=' + text]
                extra += ['--resolver-token', public['request_token']]
                if row['delegate']:
                    row['text'] = 'Use the suggested default: ' + str(question.get('proposed_default', ''))
                def done(action):
                    if action.get('exit_status') != 0:
                        row.update(status='error', error=(action.get('stderr') or 'Could not save the answer. Your message is retained.')[-2400:])
                        self._save_chat(run, row)
                        return
                    row.update(status='received', error=None)
                    append_feedback(run, row, product_change=False)
                    self._save_chat(run, row)
                    try:
                        saved = json.loads((run / 'state.json').read_text())
                        if (saved.get('status') == 'RUNNING' and not saved.get('pending_questions')
                                and not saved.get('user_request') and not saved.get('resolver_human_request')
                                and not saved.get('resolver_human_proposal')):
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
                if not failed:
                    append_feedback(run, row, product_change=True, goal_token=receipt.get('observed_goal_token', row.get('prior_goal_token')))
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
        self.inspect_verification(workspace, run, view)
        view['work_summary'] = work_summary(view)
        counts = view['work_summary']['counts']
        view['recorded_counts'] = view.get('counts', {})
        view['counts'] = {'pass': counts['checked'], 'fail': counts['failed'], 'unknown': counts['unchecked']}
        view['screenshots'] = screenshot_evidence(view)
        view['transcript'] = transcript(view)
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
        view['criteria_revision'] = object_value(state).get('criteria_revision')
        view['runner_check'] = object_value(object_value(state).get('active_runner_check'))
        view['conversation'] = project_conversation(run, object_value(state))
        view['draft_messages'] = [row for row in (view['conversation'] or {}).get('messages', []) if not row.get('id', '').startswith('task-')]
        view['chat_messages'] = []
        if (self._conversation_store is not None or conversation_protocol.journal_file(run).exists()
                or self._chat_path(run).exists()):
            # An untouched task has no receipt file to reconcile; polling it
            # must not create a new task-chat directory or filesystem lock.
            with self._chat_guard(run) if self._chat_path(run).exists() else self.chat_lock:
                try:
                    view['chat_messages'] = self._chat_rows(run)
                    self._reconcile_chat_journal(run, view, object_value(state))
                    view['conversation'] = project_conversation(run, object_value(state)) or view['conversation']
                    view['draft_messages'] = [row for row in (view['conversation'] or {}).get('messages', []) if not row.get('id', '').startswith('task-')]
                    view['chat_messages'] = self._chat_rows(run)
                except (OSError, ValueError) as error:
                    view['chat_error'] = str(error)
                for summary in self.conversations.list(include_archived=True) if self._conversation_store is not None else []:
                    attachment = object_value(summary.get('attachment'))
                    if attachment.get('run') == str(run) and attachment.get('workspace') == str(workspace):
                        doc = self.conversations.get(summary['id'])
                        view['conversation'] = view['conversation'] or doc
                        view['draft_messages'] = [row for row in view['conversation'].get('messages', doc['messages']) if not row.get('id', '').startswith('task-')]
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
                        if (saved_text == message.get('submitted_text') and not message.get('delegate')
                                and not conversation_protocol.journal_file(run).exists()):
                            message.update(status='received', error=None)
                        else:
                            message.update(status='error', error='Your message is saved, but its delivery could not be confirmed after restart. Retry this same message after checking the current question.')
                    delivery = deliveries.get(message['id'])
                    if (delivery and delivery.get('kind') == 'feedback'
                            and delivery.get('text') == message.get('submitted_text')):
                        message['delivery_status'] = delivery.get('status')
                        if delivery.get('status') in ('applied', 'resumed'):
                            message['status'] = 'applied'
        return view
