"""Reported request input context, never a cumulative token total or an estimate."""
import json
from pathlib import Path


def measurement(rows):
    samples, seen, missing, pending = [], {}, 0, False
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get('type') == 'step_start':
            pending = True
        if row.get('type') != 'step_finish':
            continue
        part = row.get('part') or {}
        if not isinstance(part, dict):
            samples.append(None)
            missing += 1
            continue
        identity = (row.get('sessionID'), part.get('id'))
        tokens = part.get('tokens') or {}
        cache = (tokens.get('cache') or {}) if isinstance(tokens, dict) else {}
        values = [tokens.get('input'), cache.get('read'), cache.get('write')] if isinstance(tokens, dict) and isinstance(cache, dict) else []
        if not all(isinstance(value, str) and value for value in identity):
            samples.append(None)
            missing += 1
            continue
        if identity in seen:
            index, previous = seen[identity]
            if values != previous and samples[index] is not None:
                samples[index] = None
                missing += 1
            continue
        pending = False
        seen[identity] = (len(samples), values)
        if len(values) != 3 or any(type(value) is not int or value < 0 for value in values):
            samples.append(None)
            missing += 1
        else:
            samples.append({'input_tokens': sum(values), 'cached_input_tokens': values[1],
                            'cache_write_tokens': values[2]})
    known = [value for value in samples if value is not None]
    return {'basis': 'reported-opencode-step' if known else 'unavailable',
            'observed_requests': len(samples), 'requests_with_usage': len(known),
            'missing_usage_requests': missing, 'last': samples[-1] if samples else None,
            'peak_input_tokens': max(value['input_tokens'] for value in known) if known else None,
            'unfinished_request': pending, 'complete': bool(samples) and missing == 0 and not pending,
            'scope': 'Input to individual completed provider requests, including cache reads/writes; not cumulative usage'}


def _rows(path):
    try:
        with Path(path).open(errors='replace') as stream:
            for line in stream:
                try:
                    row = json.loads(line)
                    if isinstance(row, dict) and row.get('type') in ('step_start', 'step_finish'):
                        yield row
                except ValueError:
                    continue
    except OSError:
        pass


def read(path):
    return measurement(_rows(path))


def saved_metrics(state, key):
    stages = state.get('stages')
    rows = []
    for record in stages if isinstance(stages, list) else []:
        metrics = record.get('metrics') if isinstance(record, dict) else None
        value = metrics.get(key) if isinstance(metrics, dict) else None
        rows.append(value if isinstance(value, dict) else None)
    return rows


def view(state):
    rows = saved_metrics(state, 'request_context')
    known = [row for row in rows if row and type(row.get('peak_input_tokens')) is int and row['peak_input_tokens'] >= 0]
    return {'last_completed_stage': rows[-1] if rows else None,
            'peak_input_tokens': max(row['peak_input_tokens'] for row in known) if known else None,
            'unavailable_stages': sum(not row or not row.get('complete') for row in rows),
            'scope': 'Saved completed stages; active and unreported requests are unavailable'}
