"""Numeric stdout corrections in model-written, never-approved draft examples.

The receipt is saved in the existing contract's declared_changes/history. The
revision guard reads it, and review_notes displays it at final plan approval.
This does not infer arithmetic or authorize changes to a user's behavior.
"""
from __future__ import annotations

import json
import re

PLANNER_ORIGINS = {"glm_draft", "glm_revise", "astra_finalize", "astra_discovery"}
RECEIPT_SCHEMA = {"type": "object", "additionalProperties": False,
                  "required": ["concern_id", "before", "after"],
                  "properties": {key: {"type": "string"} for key in ("concern_id", "before", "after")}}
RULE = """
DRAFT EXAMPLE CORRECTIONS: recompute examples before proposing the plan. Only a numeric
stdout result in a never-approved, model-written draft may be corrected without a user
answer. Keep its input, command, exit code, stderr, verification_method, human_review and
all binding behavior unchanged. A saved blocking Plan Reviewer concern must name the
criterion ID and quote both complete old and corrected stdout literals. Use the explicit
form 'writes exactly `<stdout>` to stdout'. In contract_changes declare item=<criterion ID>,
change=reworded, basis=agent_proposed, answer_id='', replacement=<complete new criterion>,
and example_correction={concern_id, before:<old stdout>, after:<corrected stdout>}.
Only JSON integer values or tab-separated word/count integers may differ; retain keys,
strings, array lengths and every other part of the example. Do not fabricate a concern
or change a literal supplied by the user, saved feedback or an approved revision.
The final Plan Reviewer must independently recompute the result from the unchanged
brief and input before resolving the concern. Preserve the repaired criterion exactly
in later reports; omit the old delta once incorporated. User approval is still required.
Other criterion changes require a saved user basis. Put purely engineering details in
verification_method; arbitrary prose, inputs, behavior and permissions are not corrections.
"""


def spelling(text):
    return str(text).replace("\\n", "\n").replace("\\t", "\t").replace("\\r", "\r")


def strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from strings(child)
    elif isinstance(value, list):
        for child in value:
            yield from strings(child)


def integers_only(before, after):
    if type(before) is not type(after):
        return False
    if isinstance(before, dict):
        return before.keys() == after.keys() and all(integers_only(before[k], after[k]) for k in before)
    if isinstance(before, list):
        return len(before) == len(after) and all(integers_only(a, b) for a, b in zip(before, after))
    return type(before) is int or before == after


def unique_object(pairs):
    result = dict(pairs)
    if len(result) != len(pairs):
        raise ValueError("Duplicate JSON output keys")
    return result


def stdout_json(text):
    return json.loads(text.removesuffix("\\n").strip(), object_pairs_hook=unique_object)


def user_literal(previous, sources):
    try:
        expected = stdout_json(previous)
    except ValueError:
        expected = None
    decoder = json.JSONDecoder(object_pairs_hook=unique_object)
    for source in strings(sources):
        if spelling(previous) in spelling(source):
            return True
        if isinstance(expected, (dict, list)):
            for start in re.finditer(r"[\[{]", source):
                try:
                    value, _ = decoder.raw_decode(source, start.start())
                except ValueError:
                    continue
                if value == expected:
                    return True
    return False


def numeric_stdout(before, after):
    if not before or before == after:
        return False
    try:
        old = stdout_json(before)
        new = stdout_json(after)
    except (ValueError, TypeError):
        pattern = re.compile(r"([^\t\n]+)\t([0-9]+)")
        old_rows = [pattern.fullmatch(row) for row in spelling(before).splitlines()]
        new_rows = [pattern.fullmatch(row) for row in spelling(after).splitlines()]
        return bool(old_rows) and len(old_rows) == len(new_rows) and all(
            a and b and a[1] == b[1] for a, b in zip(old_rows, new_rows))
    return isinstance(old, (dict, list)) and old != new and integers_only(old, new)


def user_protected(state, row, user_basis):
    approvals = {e.get("token") for e in state.get("user_events", []) if e.get("kind") == "goal_approval"}
    for revision in [*(state.get("contract_history") or []), state.get("goal_contract") or {}]:
        if not any(r.get("id") == row["id"] and r.get("criterion") == row["criterion"]
                   for r in (revision.get("body") or {}).get("acceptance_criteria", [])):
            continue
        if (revision.get("approval_status") == "approved" or revision.get("approval_event")
                or f"r{revision.get('revision')}:{revision.get('hash')}" in approvals
                or revision.get("origin") == "user_cli_edit"
                or any(c.get("item") == row["id"] and user_basis(state, c.get("basis"), c.get("answer_id"))
                       for c in revision.get("declared_changes", []))):
            return True
    return False


def corrections(state, before, after, changes, user_basis):
    contract = state.get("goal_contract") or {}
    if (contract.get("approval_status") != "draft" or contract.get("approval_event")
            or contract.get("origin") not in PLANNER_ORIGINS):
        return set()
    old = {r["id"]: r for r in before.get("acceptance_criteria", [])}
    new = {r["id"]: r for r in after.get("acceptance_criteria", [])}
    reports = (state.get("planning") or {}).get("reports") or {}
    review = (reports.get("astra_challenge") or reports.get("plan_review") or {}).get("report") or {}
    sources = [state.get(key) for key in ("task", "answers", "brief_feedback", "user_events", "approved_design")]
    sources += [r.get("source_quote", "") for r in (state.get("requirements_handoff") or {}).get("requirements", [])]
    found = set()
    for change in changes:
        if not isinstance(change, dict):
            continue
        cid, receipt = change.get("item"), change.get("example_correction")
        if (cid not in old or cid not in new or not isinstance(receipt, dict)
                or sum(isinstance(c, dict) and c.get("item") == cid for c in changes) != 1
                or change.get("change") != "reworded" or change.get("basis") != "agent_proposed"
                or change.get("answer_id") or change.get("replacement") != new[cid]["criterion"]
                or user_protected(state, old[cid], user_basis)
                or any(old[cid].get(k) != new[cid].get(k) for k in ("verification_method", "human_review"))):
            continue
        previous, replacement = receipt.get("before"), receipt.get("after")
        if not isinstance(previous, str) or not isinstance(replacement, str) or not numeric_stdout(previous, replacement):
            continue
        literal = "`" + previous + "`"
        text = old[cid]["criterion"]
        if text.count(literal) != 1 or user_literal(previous, sources):
            continue
        start = text.index(literal)
        if (not re.search(r"\b(?:writes?|prints?)\s+(?:exactly\s+)?$", text[:start], re.I)
                or not re.match(r"\s+(?:to\s+)?stdout\b", text[start + len(literal):], re.I)
                or new[cid]["criterion"] != text.replace(literal, "`" + replacement + "`")):
            continue
        for concern in review.get("concerns", []):
            evidence = " ".join(strings(concern))
            if (concern.get("id") == receipt.get("concern_id") and concern.get("blocking") is True
                    and re.search(r"(?<![A-Za-z0-9_])" + re.escape(cid) + r"(?![A-Za-z0-9_])", evidence)
                    and spelling(previous) in spelling(evidence) and spelling(replacement) in spelling(evidence)):
                found.add(cid)
                break
    return found


def review_notes(state):
    current = {r["id"]: r["criterion"] for r in (state.get("goal_contract") or {}).get("body", {}).get("acceptance_criteria", [])}
    notes, seen = [], set()
    revisions = [*(state.get("contract_history") or []), state.get("goal_contract") or {}]
    for revision in reversed(revisions):
        if revision.get("approval_status") == "approved" or revision.get("approval_event"):
            break
        for change in revision.get("declared_changes", []):
            receipt = change.get("example_correction")
            key = (change.get("item"), json.dumps(receipt, sort_keys=True))
            if receipt and key not in seen and receipt.get("after") in current.get(change.get("item"), ""):
                notes.append(f"  - {change['item']} ({receipt['concern_id']}): {receipt['before']} -> {receipt['after']}")
                seen.add(key)
    return ["", "Reviewed draft example corrections:", *reversed(notes)] if notes else []
