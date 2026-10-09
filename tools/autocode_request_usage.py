"""Native request receipts and input context, with explicit incomplete coverage."""
import json
import math
import hashlib
import os
from pathlib import Path

TOKEN_KEYS = ('input_tokens', 'cached_input_tokens', 'cache_write_tokens',
              'output_tokens', 'reasoning_output_tokens', 'fresh_input_tokens', 'visible_output_tokens')


def accounting(rows, *, issues=()):
    """Exact native request identities and independently known quantities.

    OpenCode input excludes cache reads/writes and output excludes reasoning.
    Our inclusive input/output contain each exactly once; reasoning is a subset
    of output, not another quantity to add to it. Turn aggregates are not requests.
    """
    requests, problems, pending, starts = {}, list(issues), {}, set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        if row.get('type') == 'error':
            problems.append('provider error may contain an unreported request')
        if row.get('type') not in ('step_start', 'step_finish'):
            continue
        if row['type'] == 'step_start':
            part = row.get('part') if isinstance(row.get('part'), dict) else {}
            session = row.get('sessionID')
            if not isinstance(session, str) or not session or part.get('sessionID', session) != session:
                session = None
                problems.append('request start has no exact session identity')
            identity = part.get('id')
            if session and isinstance(identity, str) and identity:
                key = (session, identity)
                if key in starts:
                    continue
                starts.add(key)
            pending.setdefault(session, []).append(row.get('timestamp'))
            continue
        part = row.get('part')
        part = part if isinstance(part, dict) else {}
        session, identity = row.get('sessionID'), part.get('id')
        if (not isinstance(session, str) or not session or not isinstance(identity, str) or not identity
                or part.get('sessionID', session) != session):
            problems.append('request finish has no exact matching session/part identity')
            continue
        raw = part.get('tokens')
        raw = raw if isinstance(raw, dict) else {}
        cache = raw.get('cache')
        cache = cache if isinstance(cache, dict) else {}
        values = {key: value if type(value) is int and value >= 0 else None for key, value in {
            'fresh': raw.get('input'), 'read': cache.get('read'), 'write': cache.get('write'),
            'visible': raw.get('output'), 'reasoning': raw.get('reasoning')}.items()}
        def total(*keys):
            return sum(values[key] for key in keys) if all(values[key] is not None for key in keys) else None
        cost = part.get('cost')
        cost = cost if type(cost) in (int, float) and math.isfinite(cost) and cost > 0 else None
        current = {'identity': [session, identity], 'tokens': {
            'input_tokens': total('fresh', 'read', 'write'), 'cached_input_tokens': values['read'],
            'cache_write_tokens': values['write'], 'output_tokens': total('visible', 'reasoning'),
            'reasoning_output_tokens': values['reasoning'], 'fresh_input_tokens': values['fresh'],
            'visible_output_tokens': values['visible']}, 'reported_cost_usd': cost,
            'started_at_ms': pending.get(session, [None])[0] if pending.get(session) else None,
            'finished_at_ms': row.get('timestamp')}
        key = (session, identity)
        if key in requests:
            previous = requests[key]
            if previous.get('conflict') or any(previous.get(field) != current.get(field)
                                               for field in ('tokens', 'reported_cost_usd')):
                problems.append(f'conflicting request usage: {session}/{identity}')
                requests[key] = {'identity': [session, identity], 'tokens': dict.fromkeys(TOKEN_KEYS),
                                 'reported_cost_usd': None, 'conflict': True}
            # A replay of an old finish cannot close a newer pending request.
            continue
        if pending.get(session):
            pending[session].pop(0)
        requests[key] = current
    unfinished = any(pending.values())
    if unfinished:
        problems.append('unfinished request has no final usage')
    return {'requests': list(requests.values()), 'observed_requests': len(requests),
            'complete': bool(requests) and not problems and
                        all(all(value is not None for value in row['tokens'].values()) for row in requests.values()),
            'unfinished_request': unfinished, 'unfinished_requests': sum(map(len, pending.values())),
            'issues': list(dict.fromkeys(problems)),
            'scope': 'Native session/part request identities; missing or unobserved requests are not zero'}


def measurement(rows):
    rows = list(rows)
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
    pending = accounting(rows)['unfinished_request']
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
    rows, issues, identity = [], [], None
    try:
        digest = hashlib.sha256()
        with Path(path).open('rb') as stream:
            before = os.fstat(stream.fileno())
            for line in stream:
                digest.update(line)
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                    if isinstance(row, dict):
                        rows.append(row)
                    else:
                        issues.append('non-object event')
                except (ValueError, UnicodeError):
                    issues.append('unparsed or truncated event line')
            after = os.fstat(stream.fileno())
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                issues.append('event stream changed during measurement')
            identity = {'path': str(Path(path).resolve()), 'sha256': digest.hexdigest(),
                        'size': after.st_size, 'mtime_ns': after.st_mtime_ns}
    except (OSError, ValueError) as error:
        issues.append(f'events unavailable: {error}')
    result = accounting(rows, issues=issues)
    result['event_file'] = identity
    return {**measurement(rows), 'accounting': result}


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
