"""Versioned Autocode registry and intervention adapter; never writes task state."""
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
try:
    from .dashboard_monitor import process_table as monitor_process_table, snapshot as monitor_snapshot
except ImportError:  # Support direct execution from this source directory.
    from dashboard_monitor import process_table as monitor_process_table, snapshot as monitor_snapshot


def mapping(value):
    return value if isinstance(value, dict) else {}


def rows(value):
    return value if isinstance(value, list) else []


def capability(value):
    value = mapping(value)
    return value.get('supported') is True and type(value.get('version')) is int and value['version'] == 1


class RegistryInterventionMixin:
    def __init__(self, *args, registry_ttl=30.0, **kwargs):
        super().__init__(*args, **kwargs)
        self.registry_lock = threading.RLock()
        self.registry_scope = threading.local()
        self.registry_ttl = max(0.0, float(registry_ttl))
        self.registry_cache = {'at': 0, 'workspaces': [], 'runs': {}, 'diagnostics': [], 'error': None, 'location': None}
        self.status_cache = {}
        self.intervention_actions = {}

    def _json_command(self, args, timeout=4):
        try:
            result = subprocess.run([sys.executable, self.runner, *args], capture_output=True, text=True, timeout=timeout)
        except (OSError, subprocess.TimeoutExpired) as error:
            return None, {'message': str(error), 'uncertain': True}
        try:
            data = json.loads(result.stdout)
            if not isinstance(data, dict):
                raise ValueError('Expected a JSON object')
        except ValueError:
            return None, {'message': 'Runner does not provide this JSON interface, or returned malformed output.', 'uncertain': True}
        if result.returncode or data.get('error'):
            error = mapping(data.get('error'))
            return data, {'message': str(error.get('message') or error.get('code') or 'Runner command failed'),
                          'uncertain': False, 'code': error.get('code'), 'exit_code': result.returncode}
        return data, None

    def _registered(self):
        pinned = getattr(self.registry_scope, 'value', None)
        if pinned is not None:
            return pinned
        with self.registry_lock:
            if time.monotonic() - self.registry_cache['at'] < self.registry_ttl:
                return self.registry_cache
            result = {'workspaces': [], 'workspace_ids': {}, 'runs': {}, 'diagnostics': [], 'error': None, 'location': None}
            location, error = self._json_command(['registry', 'location', '--json'])
            if error:
                result['error'] = error['message']
            elif location.get('registry_version') != 1 or location.get('operation') != 'location':
                result['error'] = 'Unsupported registry location interface.'
            else:
                result['location'] = location.get('registry_path')
                data, error = self._json_command(['registry', 'list', '--json'])
                if error:
                    result['error'] = error['message']
                elif data.get('registry_version') != 1 or data.get('operation') != 'list' or not isinstance(data.get('runs'), list) or not isinstance(data.get('workspaces'), list):
                    result['error'] = 'Unsupported or malformed registry listing.'
                else:
                    for item in data['workspaces']:
                        item = mapping(item)
                        raw = item.get('workspace')
                        try:
                            path = Path(raw).resolve(strict=True)
                            if str(path) != raw or not path.is_dir() or not (path / '.git').exists():
                                raise ValueError('Not an available canonical Git workspace')
                            result['workspaces'].append(path)
                            ident = item.get('id')
                            if (item.get('availability') == 'available' and isinstance(ident, str)
                                    and re.fullmatch(r'workspace-[a-f0-9]{24}', ident)):
                                result['workspace_ids'][ident] = str(path)
                        except (OSError, TypeError, ValueError) as failure:
                            result['diagnostics'].append({'workspace': str(raw), 'error': str(failure)})
                    for item in data['runs']:
                        item = mapping(item)
                        try:
                            workspace, run = Path(item['workspace']), Path(item['run_dir'])
                            if item.get('availability') != 'available':
                                raise ValueError(str(item.get('availability') or 'Availability unavailable'))
                            if workspace not in result['workspaces'] or run.resolve(strict=True) != run:
                                raise ValueError('Invalid registered workspace or run pointer')
                            if not self._contained_run(workspace, str(run), identity=True):
                                raise ValueError('Run or checkpoint identity/containment is invalid')
                            result['runs'][run] = workspace
                        except (KeyError, OSError, TypeError, ValueError) as failure:
                            result['diagnostics'].append({'workspace': str(item.get('workspace', 'registry')),
                                                          'run': item.get('run_dir'), 'error': str(failure)})
            # Loading and validating a large registry can exceed the cache TTL.
            # Give every completed result, including errors, a full reuse window.
            result['at'] = time.monotonic()
            self.registry_cache = result
            return result

    @contextmanager
    def pin_registry(self):
        """Reuse one membership snapshot throughout a composite dashboard read."""
        prior = getattr(self.registry_scope, 'value', None)
        if prior is None:
            self.registry_scope.value = self._registered()
        try:
            yield self.registry_scope.value
        finally:
            if prior is None:
                del self.registry_scope.value
            else:
                self.registry_scope.value = prior

    @contextmanager
    def pin_discovery(self):
        """Reuse one watched-project set throughout a composite dashboard read."""
        prior = getattr(self.registry_scope, 'discovered', None)
        prior_errors = getattr(self.registry_scope, 'discovery_errors', None)
        if prior is None:
            self.registry_scope.discovered = super()._discovered()
            with self.scan_lock:
                self.registry_scope.discovery_errors = [
                    {'workspace': str(root), 'error': entry['error']}
                    for root in self.watch_roots
                    if (entry := self.discovery_cache.get(str(root))) and entry.get('error')
                ]
        try:
            yield self.registry_scope.discovered
        finally:
            if prior is None:
                del self.registry_scope.discovered
                del self.registry_scope.discovery_errors
            else:
                self.registry_scope.discovered = prior
                self.registry_scope.discovery_errors = prior_errors

    def _discovered(self):
        pinned = getattr(self.registry_scope, 'discovered', None)
        return pinned if pinned is not None else super()._discovered()

    def root_error_rows(self):
        pinned = getattr(self.registry_scope, 'discovery_errors', None)
        return list(pinned) if pinned is not None else super().root_error_rows()

    def registry_status(self):
        registry = self._registered()
        return {'error': registry['error'], 'location': registry['location'], 'version': 1 if not registry['error'] else None}

    @property
    def workspaces(self):
        return self._workspaces_for_registry(self._registered())

    def _workspaces_for_registry(self, registry):
        return list(dict.fromkeys([*self.explicit, *self.created_workspaces,
                                   *sorted(self._discovered(), key=str), *registry['workspaces']]))

    def _contained_run(self, workspace, raw, identity=False):
        if not workspace or not isinstance(raw, str):
            return None
        run = super().run_for(workspace, raw)
        if not run:
            return None
        try:
            state_path = run / 'state.json'
            if state_path.is_symlink() or state_path.resolve(strict=True).parent != run:
                return None
            data = json.loads(state_path.read_text())
            saved_workspace = mapping(data).get('workspace')
            if (identity or saved_workspace is not None) and saved_workspace != str(workspace):
                return None
        except (OSError, ValueError):
            # Malformed legacy state must remain visible as unavailable, not gain mutation access.
            if identity:
                return None
        return run

    def run_for(self, workspace, raw, *, registry=None):
        run = self._contained_run(workspace, raw)
        if not run:
            return None
        if workspace in self.explicit or workspace in self.created_workspaces or workspace in self._discovered():
            return run
        registry = self._registered() if registry is None else registry
        return run if registry['runs'].get(run) == workspace else None

    def discover(self):
        registry = self._registered()
        result = self.root_error_rows() + list(registry['diagnostics'])
        seen = set()
        process_snapshot, process_snapshot_loaded = None, False
        # One traversal uses one membership snapshot even if its reads outlast
        # the TTL. run_for still checks each current path and checkpoint identity.
        for workspace in self._workspaces_for_registry(registry):
            root = workspace / '.autocode/runs'
            try:
                children = list(root.iterdir()) if root.is_dir() else []
            except OSError as error:
                result.append({'workspace': str(workspace), 'error': str(error)})
                continue
            for child in children:
                if not child.is_dir():
                    continue
                run = self.run_for(workspace, str(child), registry=registry)
                if not run:
                    # A registry-only project grants access only to its registered runs.
                    if workspace in self.explicit or workspace in self.created_workspaces or workspace in self._discovered():
                        result.append({'workspace': str(workspace), 'run': str(child), 'error': 'Run escapes watched workspace or state is unavailable'})
                    continue
                if run in seen:
                    continue
                seen.add(run)
                try:
                    state = json.loads((run / 'state.json').read_text())
                    if not isinstance(state, dict):
                        raise ValueError('State is not an object')
                    view = super().view(workspace, run, state)
                    active = mapping(state.get('active_stage'))
                    if type(active.get('pid')) is int and not process_snapshot_loaded:
                        process_snapshot = monitor_process_table()
                        process_snapshot_loaded = True
                    view['monitor'] = monitor_snapshot(mapping(state), run, process_snapshot=process_snapshot)
                except (OSError, ValueError):
                    view = super().view(workspace, run)
                if view.get('state_error'):
                    view['error'] = 'state unavailable: ' + view['state_error']
                result.append(view)
        return result

    def _intervention_view(self, workspace, run):
        key = str(run)
        cached = self.status_cache.get(key)
        if cached and time.monotonic() - cached['at'] < .8:
            return cached['value']
        data, error = self._json_command(['--workspace', str(workspace), '--run-dir', key, '--status'])
        interventions = mapping(mapping(data).get('interventions'))
        inspector = capability(interventions.get('inspector_capability'))
        capable = inspector and capability(interventions.get('runner_capability'))
        mode = 'capable' if capable else 'legacy'
        if interventions and not inspector:
            mode = 'unavailable'
            error = error or {'message': 'Unsupported intervention inspector version.'}
        if inspector and mapping(interventions.get('runner_capability')).get('supported') is True and not capable:
            mode = 'unavailable'
            error = {'message': 'Unsupported saved runner intervention version.'}
        if error:
            try:
                saved = json.loads((run / 'state.json').read_text())
                if mapping(saved.get('intervention_capability')).get('supported'):
                    mode = 'unavailable'
            except (OSError, ValueError):
                pass
        with self.lock:
            local = {ident: dict(item) for ident, item in self.intervention_actions.get(key, {}).items()}
        for ident, item in local.items():
            if item.get('legacy_action'):
                action = next((x for x in self.action_log(workspace, run) if x['id'] == item['legacy_action']), {})
                item['status'] = {'finished': 'delivered', 'launch_failed': 'uncertain'}.get(action.get('status'), action.get('status', 'uncertain'))
        inspect_error = None
        if inspector:
            inbox, inspect_error = self._json_command(['intervention', 'inspect', '--workspace', str(workspace), '--run-dir', key, '--json'])
            if not inspect_error and (inbox.get('version') != 1 or inbox.get('operation') != 'inspect' or not isinstance(inbox.get('requests'), list)):
                inspect_error = {'message': 'Unsupported or malformed intervention inbox.'}
            if not inspect_error:
                for item in inbox['requests']:
                    item = mapping(item)
                    if isinstance(item.get('id'), str):
                        local[item['id']] = {**item, 'status': 'queued', 'durable': True}
        for item in rows(interventions.get('applied_receipts')):
            item = mapping(item)
            if isinstance(item.get('id'), str):
                local[item['id']] = {**item, 'status': 'resumed' if item.get('resumed_at') else 'applied', 'durable': True}
        blocked = [x for x in rows(interventions.get('blocked_conditions')) if isinstance(x, str)]
        inbox_error = mapping(interventions.get('inbox_error'))
        problem = error or inspect_error or inbox_error
        value = {'capable': capable and not bool(error or inspect_error), 'mode': mode,
                 'attempt_id': mapping(data).get('attempt_id'),
                 'runner_status': mapping(data).get('status'),
                 'error': mapping(problem).get('message') or mapping(problem).get('code'),
                 'entries': sorted(local.values(), key=lambda x: (x['order'] if type(x.get('order')) is int else 10**15,
                                                                 x.get('submitted_at') if isinstance(x.get('submitted_at'), str) else '')),
                 'blocked_conditions': blocked, 'pause_intent': interventions.get('pause_intent')}
        if capable and (error or inspect_error):
            value['mode'] = 'unavailable'
        self.status_cache[key] = {'at': time.monotonic(), 'value': value}
        return value

    def view(self, workspace, run, s=None):
        return {**super().view(workspace, run, s), 'interventions': self._intervention_view(workspace, run)}

    def _marker(self, run):
        marker = run / 'pause-requested'
        if marker.is_symlink():
            raise ValueError('Pause marker must be a regular run-local file, including when its symlink target is missing')
        try:
            if marker.exists() and not stat.S_ISREG(marker.stat().st_mode):
                raise ValueError('Pause marker is not a regular file')
        except OSError as error:
            raise ValueError('Cannot inspect pause marker: ' + str(error)) from error
        return marker

    def intervene(self, workspace, run, kind, text='', request_id=None):
        if kind == 'feedback' and (not isinstance(text, str) or not text.strip()):
            raise ValueError('A non-empty change request is required')
        if request_id is not None and (not isinstance(request_id, str) or not request_id):
            raise ValueError('Request ID must be a non-empty string')
        current = self._intervention_view(workspace, run)
        if current['mode'] == 'unavailable':
            raise ValueError(current['error'] or 'Live control capability is unavailable')
        ident = request_id or uuid.uuid4().hex
        row = {'id': ident, 'kind': kind, 'text': text, 'status': 'pending', 'durable': False}
        if current['capable']:
            args = ['intervention', 'submit', '--workspace', str(workspace), '--run-dir', str(run), '--request-id', ident, '--kind', kind]
            if kind == 'feedback':
                args += ['--text', text]
            data, error = self._json_command([*args, '--json'])
            if error:
                row.update(status='uncertain' if error['uncertain'] else 'failed', error=error['message'])
            else:
                receipt = mapping(data.get('receipt'))
                if data.get('version') != 1 or data.get('operation') != 'submit' or data.get('accepted') is not True or receipt.get('id') != ident or receipt.get('kind') != kind or receipt.get('text') != text:
                    row.update(status='uncertain', error='Unrecognized receipt; delivery is uncertain. Explicit retry uses the same request ID.')
                else:
                    row.update(receipt, status='applied' if data.get('consumer') == 'already_applied' else 'queued', durable=True,
                               idempotent=data.get('idempotent', False))
        elif kind == 'pause':
            marker = self._marker(run)
            try:
                fd = os.open(marker, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, 'O_NOFOLLOW', 0), 0o600)
                os.close(fd)
            except FileExistsError:
                self._marker(run)
            except OSError as error:
                raise ValueError('Pause request could not be saved: ' + str(error)) from error
            row.update(status='requested', legacy=True, text='Legacy pause marker saved; worker stoppage is not confirmed.')
        else:
            action = self.enqueue(workspace, run, 'Feedback', ['--feedback', text])
            row.update(status='queued', legacy=True, legacy_action=action['id'])
        with self.lock:
            self.intervention_actions.setdefault(str(run), {})[ident] = row
            self.status_cache.pop(str(run), None)
        return row

    def continue_run(self, workspace, run):
        current = self._intervention_view(workspace, run)
        if current['mode'] == 'unavailable':
            raise ValueError(current['error'] or 'Runner status is unavailable')
        if current.get('attempt_id') and current.get('runner_status') in ('PAUSED_PROVIDER_UNCERTAIN', 'PAUSED_UNCERTAIN_STAGE'):
            raise ValueError('Inspect the interrupted attempt, then use Recover saved work before continuing. Existing edits will be retained.')
        if current['capable']:
            return self.enqueue(workspace, run, 'Continue', ['--no-chat', '--resume-paused'])
        key = str(run)
        with self.lock:
            if key in self.pending or str(workspace) in self.workspace_busy:
                raise ValueError('A run or workspace action is already queued or running')
            self.pending.add(key)
            self.workspace_busy.add(str(workspace))
        try:
            marker = self._marker(run)
            if marker.exists():
                marker.unlink()
        except (OSError, ValueError) as error:
            with self.lock:
                self.pending.discard(key)
                self.workspace_busy.discard(str(workspace))
            raise ValueError('Cannot clear the pause request; Continue was not launched: ' + str(error)) from error
        command = [sys.executable, self.runner, '--workspace', str(workspace), '--run-dir', str(run), '--no-chat']
        action = self._record(key, 'Continue', command)
        self.pool.submit(self._execute, key, str(workspace), action)
        return action

    def recover_stage(self, workspace, run, attempt):
        # Read fresh runner status, and let the runner recheck the exact attempt
        # and process liveness under its workspace lock. Never edit state here.
        status, error = self._json_command(['--workspace', str(workspace), '--run-dir', str(run), '--status'])
        if error:
            raise ValueError(error['message'])
        if status.get('status') not in ('PAUSED_PROVIDER_UNCERTAIN', 'PAUSED_UNCERTAIN_STAGE'):
            raise ValueError('This task is no longer waiting for interrupted-stage recovery. Refresh its status.')
        if not isinstance(attempt, str) or not attempt or attempt != status.get('attempt_id'):
            raise ValueError('The interrupted attempt changed. Review the current attempt before recovering it.')
        self.status_cache.pop(str(run), None)
        return self.enqueue(workspace, run, 'Recover saved work', ['--no-chat', '--abandon-stage', attempt])

    def mutate(self, data):
        if data.get('action') not in ('feedback', 'pause', 'continue', 'recover_stage'):
            return super().mutate(data)
        workspace = self.workspace_for(data.get('workspace', ''))
        run = self.run_for(workspace, data.get('run', ''))
        if not run:
            raise ValueError('Run does not belong to an available project')
        if data['action'] == 'continue':
            return self.continue_run(workspace, run)
        if data['action'] == 'recover_stage':
            return self.recover_stage(workspace, run, data.get('attempt_id'))
        return self.intervene(workspace, run, data['action'], data.get('text', '') if data['action'] == 'feedback' else '', data.get('request_id'))
