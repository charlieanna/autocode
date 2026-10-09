"""Output display policy. No model routing, session rotation, or proof authority.

configure alone writes settings.output_transport; launch/context read it.
account alone saves stage.metrics.output_transport; view reads those saved metrics.
environment binds AUTOCODE_OUTPUT_WORKSPACE to this run; output_store reads it
so nested investigation commands retain bytes and measurements in the same store.
"""
from pathlib import Path
import shlex
import sys

try:
    from . import autocode_output_store as store
    from .autocode_request_usage import saved_metrics
except ImportError:
    import autocode_output_store as store
    from autocode_request_usage import saved_metrics


def mode(settings):
    policy = settings.get('output_transport') if isinstance(settings, dict) else None
    selected = policy.get('mode') if isinstance(policy, dict) else None
    return selected if selected in ('raw', 'conservative') else 'raw'


def configure(state, settings, args):
    requested = getattr(args, 'tool_output_mode', None)
    started = bool(state.get('settings') or state.get('sessions') or state.get('history'))
    if started and not requested and 'output_transport' not in settings:
        return settings
    selected = mode(settings) if started else 'conservative'
    if requested and requested != selected and started:
        if (not getattr(args, 'resume_paused', False) or not str(state.get('status', '')).startswith('PAUSED_')
                or any(state.get(key) for key in ('active_stage', 'pending_report_repair', 'uncertain_artifacts'))):
            raise ValueError('Change --tool-output-mode only at a reconciled pause with --resume-paused')
    settings['output_transport'] = {'version': 1, 'mode': requested or selected}
    return settings


def environment(settings, workspace, attempt):
    workspace = Path(workspace).resolve()
    return {'AUTOCODE_OUTPUT_MODE': mode(settings),
            'AUTOCODE_OUTPUT_WORKSPACE': str(workspace),
            'AUTOCODE_OUTPUT_STORE': str(Path(workspace) / '.autocode/output'),
            'AUTOCODE_OUTPUT_ATTEMPT': str(attempt)}


def context(settings):
    prefix = [sys.executable, str(Path(__file__).with_name('autocode.py')), 'output', '--mode', mode(settings)]
    return {'mode': mode(settings), 'command': shlex.join(prefix),
            'instructions': 'When shell tools are permitted, use this command with read FILE --start-line N --end-line M '
            'for exact sections, or retrieve SHA256 --raw for retained originals. Use --known-sha256 only for an exact '
            'full-file identity already read in this session. Native reads remain available. All original evidence '
            'and fresh verification remain mandatory; display references never establish proof.'}


def account(state, record):
    if not record.get('events') or not state.get('workspace') or not state.get('settings', {}).get('output_transport'):
        return
    try:
        counts = store.summary(Path(state['workspace']) / '.autocode/output', attempt=str(record['events']))
        record.setdefault('metrics', {})['output_transport'] = counts
    except (OSError, ValueError):
        record.setdefault('metrics', {})['output_transport'] = {'measurement': 'unavailable'}


def view(state):
    rows = saved_metrics(state, 'output_transport')
    fields = ('operations', 'raw_bytes', 'displayed_bytes', 'retrieval_calls')
    known = [row for row in rows if row and all(type(row.get(key)) is int and row[key] >= 0 for key in fields)]
    return {'mode': mode(state.get('settings') or {}),
            **{key: sum(row[key] for row in known) for key in fields},
            'unavailable_stages': len(rows) - len(known), 'cost_savings_usd': None,
            'scope': 'AutoCode capture/output commands in completed stages; native provider tools are not intercepted'}
