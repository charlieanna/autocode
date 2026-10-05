"""Normalize protected image-fetch audit records, never tool-read observations.

The adjacent stage-local plugin's v2 format requires a verified response body.
The original request/finish correlation was exercised with the real OpenCode
1.18.33 CLI and a local, model-free HTTP provider. The stage owner supplies its
prelaunch audit path/attempt/plugin pins and verifies tool containment separately.
Successful live-provider conformance is still required for that provider route.
"""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path
import re

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


def verify(record, *, events, report, images, binding, audit_path, attempt_id, plugin_sha256):
    """Delivery callback for visual_acceptance.accept, pinned by the stage owner.

    No subprocess, new provider request, credentials, state file or path discovery.
    audit_path must be protected from every model tool for the entire stage; this
    cannot be inferred from an on-disk file or a model-provided path/hash alone.
    """
    def require(condition, message):
        if not condition:
            raise ValueError('Image delivery: ' + message)

    path = Path(audit_path)
    require(path.is_file() and not any(p.is_symlink() for p in (path, *path.parents)), 'missing or symlinked audit')
    raw = path.read_bytes()
    require(raw.endswith(b'\n'), 'truncated audit')
    rows = [json.loads(line) for line in raw.splitlines()]
    require(rows and [row.get('sequence') for row in rows] == list(range(len(rows))), 'incomplete audit sequence')
    require(all(type(row.get('at_ms')) is int and row['at_ms'] > 0 for row in rows), 'missing transport times')
    first = rows[0]
    require(first.get('type') == 'audit_start' and first.get('version') == 2
            and first.get('attempt_id') == attempt_id and first.get('binding_sha256') == util.digest(binding)
            and first.get('plugin_sha256') == plugin_sha256, 'foreign audit provenance')
    require(sum(row.get('type') == 'audit_start' for row in rows) == 1, 'mixed audit attempts')
    require(type(first.get('max_requests')) is int and 1 <= first['max_requests'] <= 2**53 - 1,
            'missing bounded request admission')
    expected_reviewer = first.get('reviewer', {})
    require(all(expected_reviewer.get(key) == binding['reviewer'][key] for key in ('provider', 'model'))
            and isinstance(expected_reviewer.get('agent'), str) and expected_reviewer['agent'], 'unbound reviewer admission')
    require(not any(row.get('type') == 'request_denied' for row in rows), 'request admission denied')
    admissions = [row for row in rows if row.get('type') in ('request', 'request_unverified')]
    require(1 <= len(admissions) <= first['max_requests']
            and [row.get('admission_index') for row in admissions] == list(range(1, len(admissions) + 1)),
            'request admission count mismatch')
    native = [json.loads(line) for line in events.splitlines()]
    finishes = [row['part'] for row in native if row.get('type') == 'step_finish']
    require(finishes and finishes[-1].get('reason') == 'stop', 'no successful native finish')
    finish = finishes[-1]
    session, message = finish['sessionID'], finish['messageID']
    phases = [row for row in rows if row.get('type') == 'native_finish'
              and row.get('part_id') == finish['id']]
    require(len(phases) == 1 and phases[0].get('session_id') == session
            and phases[0].get('message_id') == message and phases[0].get('reason') == 'stop',
            'finish is not correlated to native events')
    messages, contexts, requests = {}, {}, []
    for row in rows:
        if row['type'] == 'native_message':
            messages[row['message_id']] = row
        if row['type'] == 'review_context':
            require(row['request_id'] not in contexts, 'duplicate request context')
            contexts[row['request_id']] = row
        if row['type'] == 'request':
            active = [info for info in messages.values() if info['session_id'] == row['session_id']
                      and info['user_message_id'] == row['user_message_id'] and info['finish'] is None]
            require(len(active) == 1, 'ambiguous native assistant request')
            requests.append((row, active[0]))
    selected = [(row, info) for row, info in requests if info['message_id'] == message and info['session_id'] == session]
    require(len(selected) == 1, 'missing or repeated fetch for accepted native message')
    request, info = selected[0]
    request_id = request['request_id']
    require(sum(row['request_id'] == request_id for row, _ in requests) == 1, 'duplicate request')
    context = contexts.get(request_id, {})
    for key in ('session_id', 'user_message_id', 'agent', 'provider', 'model', 'image_capable'):
        require(request.get(key) == context.get(key), 'changed request context')
    require(info['model'] == request.get('model') == binding['reviewer']['model']
            and info['agent'] == request.get('agent') == expected_reviewer['agent']
            and request.get('provider') == binding['reviewer']['provider'],
            'wrong independent reviewer route')
    require(request.get('image_capable') is True and request.get('payload_complete') is True, 'unsupported or partial images')
    require(isinstance(request.get('body_sha256'), str) and re.fullmatch('[a-f0-9]{64}', request['body_sha256']),
            'missing final serialized request identity')
    actual = request['images']
    require(all(type(row.get('bytes')) is int and row['bytes'] > 0 for row in actual), 'empty payload')
    require(Counter((row['sha256'], row['mime']) for row in actual)
            == Counter((row['sha256'], row['mime']) for row in images), 'exact image payloads did not reach the reviewer')
    related = [row for row in rows if row.get('request_id') == request_id]
    require(not any(row['type'] in ('request_unverified', 'response_unverified', 'request_failed') for row in related),
            'failed or unverified request')
    responses = [row for row in related if row['type'] == 'response']
    completed = [row for row in related if row['type'] == 'request_complete']
    require(len(responses) == len(completed) == 1 and type(responses[0].get('status')) is int
            and 200 <= responses[0]['status'] < 300 and completed[0].get('complete') is True
            and completed[0].get('finish_reason') == 'stop' and completed[0].get('response_id'),
            'no complete successful provider response')
    require(responses[0].get('body_present') is True and responses[0].get('cloneable') is True
            and responses[0].get('admission_index') == completed[0].get('admission_index') == request['admission_index']
            and completed[0].get('wire_format') in ('json', 'sse')
            and type(completed[0].get('response_bytes')) is int and 0 < completed[0]['response_bytes'] <= 16 * 1024 * 1024
            and isinstance(completed[0].get('response_body_sha256'), str)
            and re.fullmatch('[a-f0-9]{64}', completed[0]['response_body_sha256']),
            'no verified bounded response body')
    starts = [row for row in rows if row['type'] == 'native_start' and row['session_id'] == session
              and row['message_id'] == message]
    require(len(starts) == 1 and context['sequence'] < request['sequence'] < responses[0]['sequence']
            < starts[0]['sequence'] < phases[0]['sequence'], 'uncorrelated native request order')
    return {'version': 1, 'kind': 'final_request_image_delivery', 'image_capable': True, 'completed': True,
            'after_final_transform': True, 'session_id': session, 'message_id': message, 'finish_id': finish['id'],
            **binding['reviewer'], 'binding_sha256': util.digest(binding),
            'events_sha256': hashlib.sha256(events).hexdigest(), 'report_sha256': hashlib.sha256(report).hexdigest(),
            'request_id': request_id, 'response_id': completed[0]['response_id'], 'images': images,
            'response_body_sha256': completed[0]['response_body_sha256'], 'response_wire_format': completed[0]['wire_format'],
            'max_requests': first['max_requests'], 'admitted_requests': len(admissions),
            'request_body_sha256': request['body_sha256'], 'evidence_ref': str(path),
            'evidence_sha256': hashlib.sha256(raw).hexdigest()}
