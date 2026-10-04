"""One display-only projection of saved chat, answer and progress records.

The durable order within each source and reply relationships take precedence
when clocks disagree. Timestamps only merge otherwise unrelated streams.
Neither this module nor its output grants runner authority or writes history.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import heapq
import json
import math


def items(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def timestamp(row):
    value = row.get('created_at') or row.get('timestamp') or row.get('at')
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (value / 1000 if value >= 1e12 else value) if math.isfinite(value) else 0
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
            return parsed.replace(tzinfo=timezone.utc).timestamp() if parsed.tzinfo is None else parsed.timestamp()
        except ValueError:
            pass
    return 0


def answer_is_recorded(question, answer, receipts):
    text = answer.get('text') or answer.get('answer')
    request = answer.get('resolver_request')
    for row in receipts:
        if (str(row.get('question_id')) != str(question)
                or row.get('status') not in ('received', 'applied', 'resumed', 'delivered')
                or request and row.get('resolver_request') != request):
            continue
        delivered = row.get('delegated_default') if row.get('delegate') else row.get('submitted_text', row.get('text'))
        if isinstance(text, str) and delivered == text:
            return True
    return False


def project(view):
    records, edges, streams, originals, anonymous = {}, {}, {}, {}, {}
    def add(source, row):
        row = deepcopy(row)
        original = row.get('id')
        if not isinstance(original, str) or not original:
            stable = {key: row.get(key) for key in ('role', 'speaker', 'text', 'question_id', 'in_reply_to', 'logical_turn_id')}
            original = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()[:24]
            # Identical anonymous saved entries still represent separate rows.
            key = (source, original)
            occurrence = anonymous.get(key, 0)
            anonymous[key] = occurrence + 1
            original += '-' + str(occurrence)
        ident = source + ':' + original
        if ident in records:
            return ident
        row.update(id=ident, source_id=original, display_source=source)
        records[ident] = row
        edges[ident] = set()
        originals.setdefault(original, []).append(ident)
        stream = streams.setdefault(source, [])
        if stream:
            edges[stream[-1]].add(ident)
        stream.append(ident)
        return ident

    receipts = items(view.get('chat_messages'))
    by_request = {row.get('id'): row for row in receipts if isinstance(row.get('id'), str) and row['id']}
    consumed, journal_ids = set(), set()
    document = view.get('conversation') or {}
    journal = items(document.get('messages')) if isinstance(document, dict) else []
    for message in journal:
        request = message.get('client_request_id') or message.get('request_id')
        request = request if isinstance(request, str) else None
        receipt = by_request.get(request) or {}
        if receipt:
            consumed.add(request)
        merged = {**message, **receipt, 'id': message.get('id'),
                  'created_at': message.get('created_at') or receipt.get('created_at'),
                  'client_request_id': request}
        add('journal', merged)
        if isinstance(message.get('id'), str):
            journal_ids.add(message['id'])
    for row in items(view.get('draft_messages')):
        if not isinstance(row.get('id'), str) or row['id'] not in journal_ids:
            add('draft', row)
    answers = view.get('answers') or {}
    for question, saved in sorted(answers.items()) if isinstance(answers, dict) else []:
        answer = saved if isinstance(saved, dict) else {'text': str(saved)}
        if answer_is_recorded(question, answer, receipts):
            continue
        answer_id = str(question) + ':' + hashlib.sha256(json.dumps(answer, sort_keys=True).encode()).hexdigest()[:24]
        kind = answer.get('kind')
        add('answer', {'id': answer_id, 'role':'user', 'speaker':'You',
            'created_at':answer.get('at') or answer.get('created_at'),
            'question_id':str(question), 'question_text':(answer.get('question') or {}).get('question') if isinstance(answer.get('question'),dict) else None,
            'text':answer.get('text') or answer.get('answer') or 'Saved answer',
            'status':'received', 'delegate':kind=='delegated',
            'provenance':'suggested' if kind=='delegated' else 'typed' if kind=='answer' else 'unrecorded'})
    for row in items(view.get('progress_messages')):
        add('progress', row)
    for row in receipts:
        if not isinstance(row.get('id'), str) or row['id'] not in consumed:
            add('receipt', {'role':'user','speaker':'You', **row,
                            'client_request_id':row.get('client_request_id') or row.get('id')})
    # Reply causality is independent of wall-clock ordering.
    for ident, row in records.items():
        parent = row.get('in_reply_to')
        candidates = originals.get(parent, []) if isinstance(parent, str) else []
        if len(candidates) == 1 and candidates[0] != ident:
            edges[candidates[0]].add(ident)
    indegree = {ident: 0 for ident in records}
    for children in edges.values():
        for child in children:
            indegree[child] += 1
    ready = [(timestamp(records[ident]), ident) for ident, count in indegree.items() if not count]
    heapq.heapify(ready)
    ordered = []
    while ready:
        _, ident = heapq.heappop(ready)
        ordered.append(records[ident])
        for child in sorted(edges[ident]):
            indegree[child] -= 1
            if not indegree[child]:
                heapq.heappush(ready, (timestamp(records[child]), child))
    warning = None
    if len(ordered) != len(records):
        warning = 'Some saved reply relationships conflict. Their source order is retained for inspection.'
        emitted = {row['id'] for row in ordered}
        ordered += [row for ident, row in records.items() if ident not in emitted]
    return {'messages': ordered, 'warning': warning}
