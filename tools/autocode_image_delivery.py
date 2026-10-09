"""Normalize protected image-fetch audit records, never tool-read observations.

The adjacent stage-local plugin's v3 format verifies a bounded tool session.
Historical v2 receipts retain their original single-final-fetch interpretation.
The original request/finish correlation was exercised with the real OpenCode
1.18.33 CLI and a local, model-free HTTP provider. The stage owner supplies its
prelaunch audit path/attempt/plugin pins and verifies tool containment separately.
Successful live-provider conformance is still required for that provider route.
"""
from __future__ import annotations

import hashlib
import json
import re
from collections import Counter
from pathlib import Path

try:
    from . import autocode_util as util
except ImportError:
    import autocode_util as util


# One visual session, including every actual SDK retry and tool follow-up fetch.
SESSION_MAX_REQUESTS = 16


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
    require(rows and all(isinstance(row, dict) for row in rows) and [row.get('sequence') for row in rows] == list(range(len(rows))), 'incomplete audit sequence')
    require(all(type(row.get('at_ms')) is int and row['at_ms'] > 0 for row in rows), 'missing transport times')
    first = rows[0]
    require(first.get('type') == 'audit_start' and first.get('version') in (2, 3)
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
    if first['version'] == 3:
        return _verify_session(rows, native, finish, phases[0], images, binding, report, events, path, raw, require)
    require(len(admissions) == 1, 'legacy audit cannot attribute a multi-request session')
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


def _verify_session(rows, native, finish, final_phase, images, binding, report, events, path, raw, require):
    """v3: attribute every actual fetch, including retries, before selecting final.

    Only a conclusively failed network/HTTP attempt may precede an identical SDK
    retry. A successful but unreadable response is never ignored. Native tool
    responses must finish as tool-calls; only the last stop can carry acceptance.
    """
    first = rows[0]
    require(first['max_requests'] <= SESSION_MAX_REQUESTS, 'session request bound exceeded')
    expected = first['reviewer']
    session, message = finish['sessionID'], finish['messageID']
    kinds = {'audit_start', 'review_context', 'native_message', 'native_start', 'native_finish', 'request',
             'response', 'request_complete', 'request_failed', 'response_unverified'}
    require(all(row.get('type') in kinds for row in rows), 'unknown or unverified session record')
    require(all(row.get('sessionID') == session and row.get('type') != 'error' for row in native), 'foreign native session')
    native_phases = [row['part'] for row in native if row.get('type') in ('step_start', 'step_finish')]
    audit_phases = [row for row in rows if row.get('type') in ('native_start', 'native_finish')]
    require(len(native_phases) == len(audit_phases) and all(
        part.get('id') == row.get('part_id') and part.get('messageID') == row.get('message_id')
        and part.get('sessionID') == row.get('session_id') and part.get('reason') == row.get('reason')
        for part, row in zip(native_phases, audit_phases)), 'native session phases differ from transport audit')
    contexts, messages, requests, identity = {}, {}, [], None
    route = ('provider', 'model', 'agent', 'image_capable', 'wire_model')
    context_fields = ('session_id', 'user_message_id', *route)
    native_fields = ('session_id', 'message_id', 'user_message_id', 'model', 'agent')
    for row in rows:
        kind = row['type']
        if kind == 'review_context':
            nonce = row.get('request_id')
            require(isinstance(nonce, str) and nonce and nonce not in contexts, 'duplicate or missing request context')
            require(row.get('session_id') == session and isinstance(row.get('user_message_id'), str)
                    and row['user_message_id'], 'foreign or missing review identity')
            require(all(row.get(key) == expected[key] for key in ('provider', 'model', 'agent'))
                    and row.get('image_capable') is True and isinstance(row.get('wire_model'), str)
                    and row['wire_model'] and row['model'].endswith('/' + row['wire_model']), 'wrong independent reviewer route')
            next_identity = (row['session_id'], row['user_message_id'])
            require(identity is None or identity == next_identity, 'mixed review identities')
            identity = next_identity
            contexts[nonce] = row
        elif kind == 'native_message':
            key = row.get('message_id')
            require(isinstance(key, str) and key and row.get('session_id') == session
                    and row.get('model') == expected['model'] and row.get('agent') == expected['agent']
                    and isinstance(row.get('user_message_id'), str) and row['user_message_id']
                    and row.get('finish') in (None, 'tool-calls', 'stop'), 'foreign or failed native assistant')
            previous = messages.get(key)
            require(previous is None or all(previous.get(field) == row.get(field) for field in native_fields),
                    'changed native assistant identity')
            require(previous is None or previous.get('finish') is None or previous.get('finish') == row.get('finish'),
                    'reopened native assistant')
            messages[key] = row
        elif kind == 'request':
            context = contexts.get(row.get('request_id'), {})
            require(context and all(row.get(key) == context.get(key) for key in context_fields), 'changed request context')
            require(row.get('serialized_model') == context['wire_model'], 'changed serialized model')
            require(row.get('payload_complete') is True and isinstance(row.get('images'), list)
                    and row['images'] and all(type(item.get('bytes')) is int and item['bytes'] > 0
                        and isinstance(item.get('sha256'), str) and re.fullmatch('[a-f0-9]{64}', item['sha256'])
                        and item.get('mime') in ('image/png', 'image/jpeg', 'image/webp') for item in row['images']),
                    'unsupported or partial images')
            require(isinstance(row.get('body_sha256'), str) and re.fullmatch('[a-f0-9]{64}', row['body_sha256']),
                    'missing serialized request identity')
            active = [info for info in messages.values() if info['session_id'] == session
                      and info['user_message_id'] == context['user_message_id'] and info['finish'] is None]
            require(len(active) == 1, 'ambiguous native assistant request')
            require(context['sequence'] < row['sequence'], 'request precedes context')
            requests.append((row, active[0]))
    require(identity and all(info['user_message_id'] == identity[1] for info in messages.values()), 'foreign native parent')
    require(set(contexts) == {row['request_id'] for row, _ in requests}, 'unused request context')
    admissions = {row['admission_index']: row for row, _ in requests}
    require(len(admissions) == len(requests), 'duplicate admission')
    related_kinds = {'response', 'request_complete', 'request_failed', 'response_unverified'}
    for row in rows:
        if row['type'] in related_kinds:
            request = admissions.get(row.get('admission_index'), {})
            require(request and row.get('request_id') == request['request_id'] and request['sequence'] < row['sequence'],
                    'unattributed response or failure')
    completed_requests, retry = [], None
    for request, info in requests:
        related = [row for row in rows if row.get('admission_index') == request['admission_index'] and row['type'] in related_kinds]
        responses = [row for row in related if row['type'] == 'response']
        completed = [row for row in related if row['type'] == 'request_complete']
        failures = [row for row in related if row['type'] == 'request_failed']
        unverified = [row for row in related if row['type'] == 'response_unverified']
        if retry is not None:
            prior, prior_info, failed_at = retry
            require(info['message_id'] == prior_info['message_id']
                    and request['body_sha256'] == prior['body_sha256'], 'retry changed request or native identity')
            if request['request_id'] != prior['request_id']:
                # OpenCode's outer SessionProcessor retry prepares chat.headers
                # again, unlike an SDK retry that reuses the original headers.
                # Both admit a fetch; a fresh nonce needs a fresh, unchanged
                # review context after the conclusively audited failure.
                context, prior_context = contexts[request['request_id']], contexts[prior['request_id']]
                require(all(context.get(key) == prior_context.get(key) for key in context_fields)
                        and failed_at < context['sequence'] < request['sequence'],
                        'retry context changed or precedes audited failure')
        network_failure = len(failures) == 1 and not responses and not completed and not unverified
        http_failure = (len(responses) == len(unverified) == 1 and not completed and not failures
            and type(responses[0].get('status')) is int and 400 <= responses[0]['status'] <= 599
            and unverified[0].get('reason') == 'http_failure' and responses[0]['sequence'] < unverified[0]['sequence'])
        if network_failure or http_failure:
            require(request is not requests[-1][0], 'final request failed')
            following = requests[request['admission_index']][0]
            require(max(row['sequence'] for row in related) < following['sequence'], 'retry precedes audited failure')
            retry = request, info, max(row['sequence'] for row in related)
            continue
        retry = None
        require(len(responses) == len(completed) == 1 and not failures and not unverified, 'failed or unverified session response')
        response, terminal = responses[0], completed[0]
        require(type(response.get('status')) is int and 200 <= response['status'] < 300
                and response.get('body_present') is True and response.get('cloneable') is True
                and terminal.get('complete') is True and terminal.get('finish_reason') in ('stop', 'tool_calls')
                and terminal.get('wire_format') in ('json', 'sse')
                and type(terminal.get('response_bytes')) is int and 0 < terminal['response_bytes'] <= 16 * 1024 * 1024
                and all(isinstance(terminal.get(key), str) and re.fullmatch('[a-f0-9]{64}', terminal[key])
                        for key in ('response_body_sha256', 'output_text_sha256'))
                and isinstance(terminal.get('response_id'), str) and terminal['response_id'], 'no verified bounded response body')
        calls = terminal.get('tool_call_ids')
        require(isinstance(calls, list) and all(isinstance(call, str) and call for call in calls)
                and len(calls) == len(set(calls)) and (terminal['finish_reason'] == 'tool_calls') == bool(calls),
                'malformed terminal tool calls')
        starts = [row for row in audit_phases if row['type'] == 'native_start' and row['message_id'] == info['message_id']]
        finishes = [row for row in audit_phases if row['type'] == 'native_finish' and row['message_id'] == info['message_id']]
        expected_finish = 'tool-calls' if calls else 'stop'
        require(len(starts) == len(finishes) == 1 and finishes[0].get('reason') == expected_finish
                and request['sequence'] < response['sequence'] < starts[0]['sequence'] < finishes[0]['sequence']
                and response['sequence'] < terminal['sequence'], 'uncorrelated native request order or finish')
        require(all(previous[1]['message_id'] != info['message_id'] and previous[2]['response_id'] != terminal['response_id']
                    for previous in completed_requests), 'duplicate successful fetch or response')
        require(not completed_requests or completed_requests[-1][3]['sequence'] < request['sequence'],
                'follow-up precedes native tool finish')
        completed_requests.append((request, info, terminal, finishes[0]))
    require(retry is None and completed_requests and completed_requests[-1][0] is requests[-1][0], 'unfinished final request')
    request, info, terminal, phase = completed_requests[-1]
    require(info['message_id'] == message and phase == final_phase and terminal['finish_reason'] == 'stop'
            and all(item[2]['finish_reason'] == 'tool_calls' for item in completed_requests[:-1]), 'accepted response is not final stop')
    require({item[1]['message_id'] for item in completed_requests} == set(messages), 'unattributed native assistant')
    require(Counter((row['sha256'], row['mime']) for row in request['images'])
            == Counter((row['sha256'], row['mime']) for row in images), 'exact image payloads did not reach the final reviewer')
    text_rows = [row.get('part', {}) for row in native if row.get('type') == 'text'
                 and row.get('part', {}).get('messageID') == message]
    require(text_rows and len({row.get('id') for row in text_rows}) == len(text_rows), 'missing or repeated final native text')
    text = '\n'.join(row.get('text', '') for row in text_rows)
    require(hashlib.sha256(text.encode()).hexdigest() == terminal['output_text_sha256']
            and json.loads(text) == json.loads(report), 'provider final response differs from accepted native report')
    return {'version': 1, 'kind': 'final_request_image_delivery', 'image_capable': True, 'completed': True,
            'after_final_transform': True, 'session_id': session, 'message_id': message, 'finish_id': finish['id'],
            **binding['reviewer'], 'binding_sha256': util.digest(binding),
            'events_sha256': hashlib.sha256(events).hexdigest(), 'report_sha256': hashlib.sha256(report).hexdigest(),
            'request_id': request['request_id'], 'admission_index': request['admission_index'],
            'response_id': terminal['response_id'], 'images': images,
            'response_body_sha256': terminal['response_body_sha256'], 'response_wire_format': terminal['wire_format'],
            'response_text_sha256': terminal['output_text_sha256'],
            'max_requests': first['max_requests'], 'admitted_requests': len(requests),
            'request_body_sha256': request['body_sha256'], 'evidence_ref': str(path),
            'evidence_sha256': hashlib.sha256(raw).hexdigest()}
