"""Bind supported CLI evidence inspection to the report shown by the dashboard."""
from copy import deepcopy
import hashlib
import json


def mapping(value):
    return value if isinstance(value, dict) else {}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def matches(view, source):
    goal = mapping(view.get('goal'))
    expected = f"r{goal['revision']}:{goal['hash']}" if goal.get('hash') and goal.get('revision') is not None else None
    report = mapping(view.get('validation'))
    return (source.get('version') == 1
            and source.get('report_token') == (digest(report) if report else None)
            and source.get('contract_token') == expected
            and source.get('criteria_token') == digest(view.get('criteria') or [])
            and source.get('criteria_revision') == view.get('criteria_revision')
            and source.get('task_id') == mapping(mapping(view.get('astra_plan')).get('current_assignment')).get('id')
            and not view.get('active_stage') and not view.get('runner_check'))


def for_view(view, status=None, error=None):
    report = mapping(view.get('validation'))
    source = mapping(mapping(mapping(status).get('view')).get('verification'))
    same = not error and mapping(status).get('status') == view.get('status') and matches(view, source)
    if same:
        return deepcopy(source)
    return {'version': 1, 'freshness': 'unavailable' if report else 'not_recorded',
            'reasons': ['Current checks could not be confirmed for this displayed task. Saved evidence remains available.'
                        if report else 'No independent verification report has been saved.'], 'coverage': []}


class VerificationViewMixin:
    def inspect_verification(self, workspace, run, view):
        # Detail only: project lists must not hash every checkout while polling.
        if not view.get('validation'):
            view['verification'] = for_view(view)
            if view.get('status') == 'TASK_COMPLETE':
                view['completion_current'] = False
            return
        data, error = self._json_command(['--workspace', str(workspace), '--run-dir', str(run),
                                         '--status', '--inspect-evidence'], timeout=self.INTERVENTION_TIMEOUT)
        result = for_view(view, data, error)
        view['verification'] = result
        if view.get('status') == 'TASK_COMPLETE':
            view['completion_current'] = (not error and result['freshness'] == 'current'
                                          and mapping(data).get('completion_current') is True)
