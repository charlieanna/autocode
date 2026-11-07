"""Validation and numeric stdout corrections for model-written draft examples.

The receipt is saved in the existing contract's declared_changes/history. The
revision guard reads it, and review_notes displays it at final plan approval.
This does not infer arithmetic or authorize changes to a user's behavior.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import json
import re

PLANNER_ORIGINS = {"glm_draft", "glm_revise", "astra_finalize", "astra_discovery"}
RECEIPT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["concern_id", "before", "after"],
    "properties": {key: {"type": "string"} for key in ("concern_id", "before", "after")},
}
RULE = prompts.get("fragments/draft-examples/rule.md")
QUOTED_OUTPUT = re.compile(r"""\b(?:returns?|writes?|prints?)\s+(?:exactly\s+)?(['"])""")


def _unterminated_output(text):
    for opening in QUOTED_OUTPUT.finditer(text):
        quote = opening[1]
        rest = text[opening.end() :]
        if "\\n" not in rest or not re.search(rf"\\{quote}(?=\s|[.,;)]|$)", rest):
            continue
        # Backslashes escape one character. A genuine closing quote may still
        # follow an escaped apostrophe or a literal trailing backslash.
        if not re.match(rf"(?:[^{quote}\\]|\\[\s\S])*{quote}", rest):
            return True
    return False


def refuse_unterminated_outputs(body, previous):
    """Send a newly written ambiguous output literal back to the draft author."""
    retained = {(row["id"], row["criterion"]) for row in previous.get("acceptance_criteria", [])}
    errors = [
        f"Criterion {row['id']} has an unterminated exact-output literal"
        for row in body.get("acceptance_criteria", [])
        if (row["id"], row["criterion"]) not in retained and _unterminated_output(row["criterion"])
    ]
    errors.extend(
        f"End-to-end step {index} has an unterminated exact-output literal"
        for index, text in enumerate(body.get("end_to_end_flow", []), start=1)
        if text not in previous.get("end_to_end_flow", []) and _unterminated_output(text)
    )
    if errors:
        raise ValueError(
            ". ".join(errors)
            + ". Copy the exact result from the source or rule into a closed literal, including any terminal newline."
        )


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
        return len(before) == len(after) and all(integers_only(a, b) for a, b in zip(before, after, strict=False))
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
        return (
            bool(old_rows)
            and len(old_rows) == len(new_rows)
            and all(a and b and a[1] == b[1] for a, b in zip(old_rows, new_rows, strict=False))
        )
    return isinstance(old, (dict, list)) and old != new and integers_only(old, new)


def layout_padding(before, after):
    """Only the spaces between the tokens of a table row differ, on rows that carry a number (#676).

    A planned table pads a label to the longest label; a miscounted padding is the same row with a
    different run of spaces. Words and numbers stay as written, so this cannot change a value.
    """
    old, new = spelling(before).splitlines(), spelling(after).splitlines()
    if old == new or len(old) != len(new):
        return False
    return all(a.split() == b.split() for a, b in zip(old, new, strict=False)) and all(
        any(ch.isdigit() for ch in a) for a, b in zip(old, new, strict=False) if a != b
    )


def quoted_line_padding(text, revised, before, after, sources):
    """Accept the same explicit stdout assertion written as a list of quoted lines."""
    old, new = spelling(before), spelling(after)
    spaces = re.compile(r"(?<=\S) +(?=\S)")
    if not old.endswith("\n") or spaces.sub(" ", old) != spaces.sub(" ", new) or "`" in after:
        return False
    lines = old[:-1].split("\n")
    if len(lines) < 2 or any(
        a != b and user_literal(a, sources) for a, b in zip(lines, new[:-1].split("\n"), strict=True)
    ):
        return False
    count = str(len(lines))
    names = ("two", "three", "four", "five", "six", "seven", "eight", "nine", "ten")
    if len(lines) <= len(names) + 1:
        count += "|" + names[len(lines) - 2]
    quoted = ", ".join(json.dumps(line, ensure_ascii=False) for line in lines)
    pattern = rf"\bstdout is exactly the (?:{count}) lines {re.escape(quoted)}, each followed by a newline\b"
    matches = list(re.finditer(pattern, text))
    if len(matches) != 1 or user_literal(quoted, sources):
        return False
    match = matches[0]
    return revised == text[: match.start()] + f"it writes exactly `{after}` to stdout" + text[match.end() :]


def user_protected(state: RunState, row, user_basis):
    approvals = {e.get("token") for e in state.get("user_events", []) if e.get("kind") == "goal_approval"}
    for revision in [*(state.get("contract_history") or []), state.get("goal_contract") or {}]:
        if not any(
            r.get("id") == row["id"] and r.get("criterion") == row["criterion"]
            for r in (revision.get("body") or {}).get("acceptance_criteria", [])
        ):
            continue
        if (
            revision.get("approval_status") == "approved"
            or revision.get("approval_event")
            or f"r{revision.get('revision')}:{revision.get('hash')}" in approvals
            or revision.get("origin") == "user_cli_edit"
            or any(
                c.get("item") == row["id"] and user_basis(state, c.get("basis"), c.get("answer_id"))
                for c in revision.get("declared_changes", [])
            )
        ):
            return True
    return False


def corrections(state: RunState, before, after, changes, user_basis):
    contract = state.get("goal_contract") or {}
    if (
        contract.get("approval_status") != "draft"
        or contract.get("approval_event")
        or contract.get("origin") not in PLANNER_ORIGINS
    ):
        return set()
    old = {r["id"]: r for r in before.get("acceptance_criteria", [])}
    new = {r["id"]: r for r in after.get("acceptance_criteria", [])}
    reports = (state.get("planning") or {}).get("reports") or {}
    review = (reports.get("astra_challenge") or reports.get("plan_review") or {}).get("report") or {}
    sources = [state.get(key) for key in ("task", "answers", "brief_feedback", "user_events", "approved_design")]
    handoff = (state.get("requirements_handoff") or {}).get("report") or {}
    sources += [r.get("source_quote", "") for r in handoff.get("requirements", [])]
    found = set()
    for change in changes:
        if not isinstance(change, dict):
            continue
        cid, receipt = change.get("item"), change.get("example_correction")
        if (
            cid not in old
            or cid not in new
            or not isinstance(receipt, dict)
            or sum(isinstance(c, dict) and c.get("item") == cid for c in changes) != 1
            or change.get("change") != "reworded"
            or change.get("basis") != "agent_proposed"
            or change.get("answer_id")
            or change.get("replacement") != new[cid]["criterion"]
            or user_protected(state, old[cid], user_basis)
            or any(old[cid].get(k) != new[cid].get(k) for k in ("verification_method", "human_review"))
        ):
            continue
        previous, replacement = receipt.get("before"), receipt.get("after")
        if (
            not isinstance(previous, str)
            or not isinstance(replacement, str)
            or not (numeric_stdout(previous, replacement) or layout_padding(previous, replacement))
        ):
            continue
        literal = "`" + previous + "`"
        text = old[cid]["criterion"]
        if user_literal(previous, sources):
            continue
        revised = new[cid]["criterion"]
        prefix, separator, suffix = text.partition(literal)
        canonical = (
            separator
            and literal not in suffix
            and re.search(r"\b(?:writes?|prints?)\s+(?:exactly\s+)?$", prefix, re.I)
            and re.match(r"\s+(?:to\s+)?stdout\b", suffix, re.I)
            and revised == prefix + "`" + replacement + "`" + suffix
        )
        if not canonical and not quoted_line_padding(text, revised, previous, replacement, sources):
            continue
        for concern in review.get("concerns", []):
            evidence = " ".join(strings(concern))
            if (
                concern.get("id") == receipt.get("concern_id")
                and concern.get("blocking") is True
                and re.search(r"(?<![A-Za-z0-9_])" + re.escape(cast(str, cid)) + r"(?![A-Za-z0-9_])", evidence)
                and spelling(previous) in spelling(evidence)
                and spelling(replacement) in spelling(evidence)
            ):
                found.add(cid)
                break
    return found


def review_notes(state: RunState):
    current = {
        r["id"]: r["criterion"]
        for r in (state.get("goal_contract") or {}).get("body", {}).get("acceptance_criteria", [])
    }
    notes, seen = [], set()
    revisions = [*(state.get("contract_history") or []), state.get("goal_contract") or {}]
    for revision in reversed(revisions):
        if revision.get("approval_status") == "approved" or revision.get("approval_event"):
            break
        for change in revision.get("declared_changes", []):
            receipt = change.get("example_correction")
            key = (change.get("item"), json.dumps(receipt, sort_keys=True))
            if receipt and key not in seen and receipt.get("after") in current.get(change.get("item"), ""):
                notes.append(
                    f"  - {change['item']} ({receipt['concern_id']}): {receipt['before']} -> {receipt['after']}"
                )
                seen.add(key)
    return ["", "Reviewed draft example corrections:", *reversed(notes)] if notes else []
