"""Map exact, freshly inspected recovery cards to existing CLI controls."""
from copy import deepcopy

EXECUTION_ACTIONS = {'resume', 'abandon', 'retry_builder', 'retry_job', 'retry_report', 'retry_failed_stage'}


def projection(data):
    view = data.get('view') if isinstance(data, dict) else None
    card = view.get('recovery') if isinstance(view, dict) else None
    if isinstance(card, dict) and card.get('version') == 1:
        return deepcopy(card)
    if isinstance(data, dict) and data.get('stale') is True:
        # The supported status inspector established worker absence. Do not
        # invent a saved pause/token or offer execution against RUNNING state.
        check = bool(data.get('runner_check_workers')) and not data.get('active_stage')
        return {'version': 1, 'token': None, 'status': data.get('status'),
                'category': 'recovery', 'title': 'Worker stopped before the task finished',
                'what_happened': ('The runner-owned test process was gone at the last status check.' if check else
                                  'The recorded worker was gone at the last status check, with no completed report saved.'),
                'retained': 'Saved work and attempt evidence remain available. The task has not been verified as complete.',
                'saved_reason': data.get('next_action'),
                'context': {'attempt': data.get('attempt_id'), 'active_stage': data.get('active_stage'),
                            'runner_check': view.get('runner_check') if isinstance(view, dict) else None},
                'failure_groups': [], 'actions': [
                    {'id':'inspect','kind':'inspect','label':'Inspect saved work',
                     'effect':'Read the saved attempt and checks. Worker ownership must be reconciled before any new execution.'},
                    {'id':'feedback','kind':'feedback','label':'Explain what should change',
                     'effect':'Draft corrective information in chat. This does not launch another worker.'}]}
    return None


def command(card, selected):
    """Construct an argument vector from the runner's card, never client arguments."""
    kind = selected.get('kind')
    if kind not in EXECUTION_ACTIONS:
        raise ValueError('This card action does not run a recovery command')
    args = ['--no-chat', '--expected-recovery-token', card['token']]
    if kind == 'abandon':
        attempt = selected.get('attempt_id')
        if not isinstance(attempt, str) or not attempt:
            raise ValueError('The interrupted attempt is unavailable')
        return [*args, '--abandon-stage', attempt]
    args.append('--resume-paused')
    if kind == 'retry_builder':
        members = selected.get('milestone_ids')
        if not isinstance(members, list) or not members or any(not isinstance(mid, str) or not mid for mid in members):
            raise ValueError('The stopped Builder task is unavailable')
        for member in members:
            args.extend(['--retry-builder', member])
    elif kind == 'retry_job':
        retry_token = selected.get('job_retry_token')
        if not isinstance(retry_token, str) or not retry_token:
            raise ValueError('The inspected failure token is unavailable')
        args.extend(['--retry-failed-stage', '--job-retry-token', retry_token])
    elif kind == 'retry_report':
        attempt = selected.get('attempt_id')
        if not isinstance(attempt, str) or not attempt:
            raise ValueError('The rejected report is unavailable')
        args.extend(['--retry-report', attempt])
    elif kind == 'retry_failed_stage':
        args.append('--retry-failed-stage')
    return args


class RecoveryActionsMixin:
    def mutate(self, data):
        if data.get('action') != 'recover_pause':
            return super().mutate(data)
        workspace = self.workspace_for(data.get('workspace', ''))
        run = self.run_for(workspace, data.get('run', ''))
        if not run:
            raise ValueError('Run does not belong to an available project')
        status, error = self._json_command(['--workspace', str(workspace), '--run-dir', str(run), '--status'])
        if error:
            raise ValueError(error['message'])
        card = projection(status)
        if not card or not card.get('token') or card['token'] != data.get('recovery_token'):
            raise ValueError('The saved pause changed. Refresh and inspect the current recovery card.')
        selected = next((item for item in card.get('actions', []) if item.get('id') == data.get('recovery_action')), None)
        if selected is None:
            raise ValueError('This recovery action is no longer offered. Refresh the current card.')
        extra = command(card, selected)
        self.status_cache.pop(str(run), None)
        return self.enqueue(workspace, run, selected['label'], extra)
