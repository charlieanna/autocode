"""One display-only projection of saved chat, answer and progress records.

The durable order within each source and reply relationships take precedence
when clocks disagree. Timestamps only merge otherwise unrelated streams.
Neither this module nor its output grants runner authority or writes history.
"""

import hashlib
import heapq
import json
import math
from copy import deepcopy
from datetime import UTC, datetime


def items(value):
    return [row for row in value if isinstance(row, dict)] if isinstance(value, list) else []


def timestamp(row):
    value = row.get("created_at") or row.get("timestamp") or row.get("at")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return (value / 1000 if value >= 1e12 else value) if math.isfinite(value) else 0
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return parsed.replace(tzinfo=UTC).timestamp() if parsed.tzinfo is None else parsed.timestamp()
        except ValueError:
            pass
    return 0


def answer_is_recorded(question, answer, receipts):
    text = answer.get("text") or answer.get("answer")
    request = answer.get("resolver_request")
    for row in receipts:
        if not request and row.get("resolver_request"):
            continue
        if (
            str(row.get("question_id")) != str(question)
            or row.get("status") not in ("received", "applied", "resumed", "delivered")
            or request
            and row.get("resolver_request") != request
        ):
            continue
        delivered = row.get("delegated_default") if row.get("delegate") else row.get("submitted_text", row.get("text"))
        if isinstance(text, str) and delivered == text:
            return True
    return False


def project(view, *, state=None):
    records, edges, streams, originals, anonymous = {}, {}, {}, {}, {}
    turns = {}

    def add(source, row):
        row = deepcopy(row)
        original = row.get("id")
        if not isinstance(original, str) or not original:
            stable = {
                key: row.get(key)
                for key in ("role", "speaker", "text", "question_id", "in_reply_to", "logical_turn_id")
            }
            original = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()[:24]
            # Identical anonymous saved entries still represent separate rows.
            key = (source, original)
            occurrence = anonymous.get(key, 0)
            anonymous[key] = occurrence + 1
            original += "-" + str(occurrence)
        ident = source + ":" + original
        if ident in records:
            return ident
        row.update(id=ident, source_id=original, display_source=source)
        records[ident] = row
        edges[ident] = set()
        originals.setdefault(original, []).append(ident)
        turn = row.get("logical_turn_id")
        if isinstance(turn, str) and turn and source in ("journal", "draft", "receipt"):
            turns.setdefault(turn, set()).add(ident)
        stream = streams.setdefault(source, [])
        if stream:
            edges[stream[-1]].add(ident)
        stream.append(ident)
        return ident

    receipts = items(view.get("chat_messages"))
    by_request = {row.get("id"): row for row in receipts if isinstance(row.get("id"), str) and row["id"]}
    consumed, journal_ids = set(), set()
    document = view.get("conversation") or {}
    journal = items(document.get("messages")) if isinstance(document, dict) else []
    for message in journal:
        request = message.get("client_request_id") or message.get("request_id")
        request = request if isinstance(request, str) else None
        receipt = by_request.get(request) or {}
        if receipt:
            consumed.add(request)
        merged = {
            **message,
            **receipt,
            "id": message.get("id"),
            "created_at": message.get("created_at") or receipt.get("created_at"),
            "client_request_id": request,
        }
        add("journal", merged)
        if isinstance(message.get("id"), str):
            journal_ids.add(message["id"])
    for row in items(view.get("draft_messages")):
        if not isinstance(row.get("id"), str) or row["id"] not in journal_ids:
            add("draft", row)
    answers = view.get("answers") or {}
    saved_state = state if isinstance(state, dict) else view
    resolver = saved_state.get("resolver") or {}
    escalations = (resolver.get("human_escalations") or {}) if isinstance(resolver, dict) else {}
    event_requests = {}
    for request, entry in escalations.items() if isinstance(escalations, dict) else []:
        if not isinstance(entry, dict):
            continue
        identity, response = entry.get("identity") or {}, entry.get("response") or {}
        if not isinstance(identity, dict) or not isinstance(response, dict):
            continue
        event = response.get("event")
        if (
            isinstance(request, str)
            and entry.get("status") == "consumed"
            and identity.get("issuer") == "resolver"
            and identity.get("action") == "escalate"
            and entry.get("receipt_hash") == request
            and response.get("request_id") == request
            and response.get("actor") == "user_cli"
            and response.get("action") == "material_decision"
            and isinstance(event, dict)
        ):
            event_requests.setdefault(json.dumps(event, sort_keys=True), []).append(request)
    answer_records = [
        (row["question_id"], row)
        for row in items(saved_state.get("user_events"))
        if row.get("question_id")
        and row.get("kind") in ("answer", "delegated", "permission_answer", "checkpoint_answer")
    ]
    answer_records += sorted(answers.items()) if isinstance(answers, dict) else []
    for question, saved in answer_records:
        answer = saved if isinstance(saved, dict) else {"text": str(saved)}
        requests = event_requests.get(json.dumps(answer, sort_keys=True), [])
        request = requests[0] if len(requests) == 1 else answer.get("resolver_request")
        # CLI answer events omit the request ID. Only the exact consumed event
        # supplies it; Q1 and text alone can refer to a different invocation.
        if answer_is_recorded(question, {**answer, "resolver_request": request}, receipts):
            continue
        answer_id = str(question) + ":" + hashlib.sha256(json.dumps(answer, sort_keys=True).encode()).hexdigest()[:24]
        kind = answer.get("kind")
        add(
            "answer",
            {
                "id": answer_id,
                "role": "user",
                "speaker": "You",
                "created_at": answer.get("at") or answer.get("created_at"),
                "resolver_request": request,
                "question_id": str(question),
                "question_text": (answer.get("question") or {}).get("question")
                if isinstance(answer.get("question"), dict)
                else None,
                "text": answer.get("text") or answer.get("answer") or "Saved answer",
                "status": "received",
                "delegate": kind == "delegated",
                "provenance": "suggested" if kind == "delegated" else "typed" if kind == "answer" else "unrecorded",
            },
        )
    for row in items(view.get("progress_messages")):
        add("progress", row)
    for row in receipts:
        if not isinstance(row.get("id"), str) or row["id"] not in consumed:
            add(
                "receipt",
                {
                    "role": "user",
                    "speaker": "You",
                    **row,
                    "client_request_id": row.get("client_request_id") or row.get("id"),
                },
            )
    # Reply causality is independent of wall-clock ordering.
    for ident, row in records.items():
        parent = row.get("in_reply_to")
        candidates = (turns.get(parent) or set(originals.get(parent, []))) if isinstance(parent, str) else set()
        if len(candidates) == 1 and ident not in candidates:
            edges[next(iter(candidates))].add(ident)
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
        warning = "Some saved reply relationships conflict. Their source order is retained for inspection."
        emitted = {row["id"] for row in ordered}
        ordered += [row for ident, row in records.items() if ident not in emitted]
    return {"messages": ordered, "warning": warning}
