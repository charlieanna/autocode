"""Read and describe saved report-repair inputs without changing evidence."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from . import autocode_support as support
    from . import autocode_opencode as opencode
    from .autocode_util import read as read_json
except ImportError:
    import autocode_support as support
    import autocode_opencode as opencode
    from autocode_util import read as read_json

REPAIR_REPORT_BYTES = 128 * 1024


def original_report_for_repair(original):
    """Expose terminal output directly, including reports rejected before saving.

    OpenCode's schema-invalid JSON may exist only in a long raw event line.
    Read tools cannot reliably recover those lines. Extract the same terminal
    report as admission does, without validating, accepting or altering it.
    """
    try:
        if original.get('engine') == 'opencode':
            value = opencode.final_report(original['events'])
        else:
            value = read_json(Path(original['output']))
        return {'report': value, 'extraction_error': None}
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        return {'report': None, 'extraction_error': str(error)}


def _write_json(path, value):
    support.atomic_json(Path(path), value)


def repair_report_source(record):
    """Return a bounded report artifact or an explicitly incomplete transcript."""
    output = Path(record['output'])
    response = Path(record.get('response_text') or output.with_suffix('.response.txt'))
    if record.get('truncated_output'):
        event_path = Path(record['events'])
        partial = opencode.incomplete_response(event_path) if record.get('engine') == 'opencode' else None
        return {'path': str(event_path), 'sha256': support.file_hash(event_path),
                'format': 'truncated_provider_response', 'bytes': event_path.stat().st_size,
                'truncated': True, 'content': {
                    'complete_report': False,
                    'diagnostic': support.terminal_failure_reason(event_path),
                    'partial_text': partial}}
    if not output.is_file() and record.get('engine') == 'opencode':
        # Persisted pre-fix checkpoints may have no report file. Their pinned
        # events can be extracted locally without asking a model to search JSONL.
        try:
            value = opencode.final_report(record['events'], recover_wrapped=bool(record.get('report_only')),
                                          response_path=response)
        except RuntimeError:
            if not response.is_file():
                raise support.Paused('PAUSED_REPORT_REPAIR_INPUT', 'No completed response is available for report repair')
        else:
            _write_json(output, value)
        record['response_text'] = str(response)
    path = output if output.is_file() else response
    if not path.is_file() or path.stat().st_size > REPAIR_REPORT_BYTES:
        raise support.Paused('PAUSED_REPORT_REPAIR_INPUT',
                             f'Repair report is missing or exceeds {REPAIR_REPORT_BYTES} bytes: {path}; '
                             'inspect the saved artifact instead of truncating or reconstructing it')
    text = path.read_text()
    if not text.strip():
        raise support.Paused('PAUSED_REPORT_REPAIR_INPUT', f'Repair report is empty: {path}')
    try:
        content, format_ = json.loads(text), 'json'
    except ValueError:
        content, format_ = text, 'text'
    return {'path': str(path), 'sha256': support.file_hash(path), 'format': format_,
            'bytes': len(text.encode('utf-8')), 'truncated': False, 'content': content}


def valid_truncated_report_attempt(record, stage_completed):
    """Accept only a failed, length-limited transcript with no completion event."""
    path = record.get('events', '')
    return bool(record.get('truncated_output')
                and support.terminal_failure_reason(path)
                and not any(event.get('type') == 'turn.completed' for event in support.events(path))
                and not stage_completed)


def recovered_timeout_attempt(record, stage_completed):
    """A repair the runner archived after a nonterminal timeout: it produced no report to pair an error with."""
    return bool(record.get('timed_out') and record.get('abandoned')
                and record.get('automatic_recovery') and not stage_completed)


def repair_report_instruction(pending):
    """State the stricter source-only rule when the original output was cut off."""
    truncated = bool((pending.get('original') or {}).get('truncated_output')
                     or (pending.get('latest_rejected') or {}).get('truncated_output'))
    if truncated:
        return ('report from this incomplete stage using the supplied partial response and original executed checks. '
                'Never rerun checks or treat the partial response as a completed report. If evidence is insufficient, '
                'return BLOCKED or NOT_VERIFIED as the schema allows. ')
    return 'report from this completed stage. Do not redo '
