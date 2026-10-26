"""Requirement coverage and conflict-resolution errors for one plan, with no controller dependencies."""

from __future__ import annotations

import re

try:
    from . import autocode_brief_literals as brief_literals
except ImportError:
    import autocode_brief_literals as brief_literals


def coverage_errors(requirements, by_id, contract, *, covered, cites_user_event):
    behaviors = set(contract.get("required_behaviors", []))
    criteria = {row["id"] for row in contract.get("acceptance_criteria", [])}
    exclusions = set(contract.get("scope_exclusions", []))
    id_tokens = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z_]+[0-9]+(?![A-Za-z0-9_])")
    families = {
        re.match(r"[A-Za-z_]+", cid).group().casefold() for cid in criteria if re.match(r"[A-Za-z_]+[0-9]+$", cid)
    }

    def cites_defined_criterion(evidence):
        tokens = id_tokens.findall(evidence)
        cited = [token for token in tokens if re.match(r"[A-Za-z_]+", token).group().casefold() in families]
        known = any(
            re.search(r"(?<![A-Za-z0-9_])" + re.escape(cid) + r"(?![A-Za-z0-9_])", evidence) for cid in criteria
        )
        return known and all(token in criteria for token in cited)

    errors = []
    for row in requirements:
        entry = by_id[row["id"]]
        evidence = str(entry.get("evidence", "")).strip()
        disposition = entry["disposition"]
        if (
            covered
            and disposition == "covered"
            and evidence not in (behaviors | criteria)
            and not cites_defined_criterion(evidence)
        ):
            errors.append(f"Requirement {row['id']} is not covered by a behavior or criterion")
        if disposition == "excluded":
            if evidence not in exclusions:
                errors.append(f"Requirement {row['id']} is not present in scope_exclusions")
            if not cites_user_event(evidence):
                errors.append(f"Requirement {row['id']} cannot be excluded without a saved user event")
        if disposition == "superseded" and not cites_user_event(evidence):
            errors.append(f"Requirement {row['id']} cannot be superseded without a saved user event")
        if disposition == "proxied":
            errors += _proxy_errors(row, entry, contract, cites_user_event=cites_user_event)
            if covered and evidence not in (behaviors | criteria) and not cites_defined_criterion(evidence):
                errors.append(f"Requirement {row['id']} is proxied but cites no criterion that proves the stand-in")
    return errors


def _proxy_errors(requirement, entry, contract, *, cites_user_event):
    """A ``proxied`` row (#2068) says its criteria prove a stand-in, not what the user sees. It needs the
    reason in ``proxy_reason``, and the person's word on it: a saved answer cited in that reason or the
    evidence, or, until there is one, an open blocking question of kind decision that names the
    requirement by its ID or by a literal the brief states for it."""
    rid = requirement["id"]
    reason = entry.get("proxy_reason")
    if not isinstance(reason, str) or not reason.strip():
        return [f"Requirement {rid} is proxied without a proxy_reason saying why its criteria cannot show the result"]
    if cites_user_event(reason) or cites_user_event(str(entry.get("evidence") or "")):
        return []
    if asks_about_proxy(requirement, contract):
        return []
    return [
        f"Requirement {rid} is proxied without the person's confirmation: cite the saved answer that accepts "
        f"the stand-in in proxy_reason, or ask it as an open_blocking_questions entry of kind decision "
        f"that names {rid}"
    ]


def asks_about_proxy(requirement, contract) -> bool:
    """Whether the contract asks the person a decision question about ``requirement``: one whose text
    names its ID as a whole token, or one of the inline-code literals its brief text states."""
    rid = str(requirement.get("id") or "")
    stated = [part for part in (requirement.get("text"), requirement.get("source_quote")) if isinstance(part, str)]
    found = brief_literals.literals(stated)
    for question in (contract or {}).get("open_blocking_questions") or []:
        if not isinstance(question, dict) or question.get("kind") != "decision":
            continue
        text = " ".join(str(question.get(key) or "") for key in ("question", "why"))
        if rid and re.search(r"(?<![A-Za-z0-9_])" + re.escape(rid) + r"(?![A-Za-z0-9_])", text):
            return True
        if any(literal in text for literal in found):
            return True
    return False


def proxied_rows(trace):
    """The ``proxied`` rows of a validated trace, as the status view and the approval screen show them."""
    return [
        {
            "requirement_id": row.get("requirement_id"),
            "evidence": row.get("evidence"),
            "proxy_reason": row.get("proxy_reason"),
        }
        for row in trace or []
        if isinstance(row, dict) and row.get("disposition") == "proxied"
    ]


# The plan's own note that a component sits outside this checkout, or cannot be changed here (#2068),
# is the condition for a proxy gap. Component names are never special-cased: the note itself is the
# evidence that the user-visible result is rendered by code no change in this repository can reach.
# The match is deliberately small. A miss leaves the question to the Plan Reviewer's prompt rule; a
# false match blocks an ordinary plan, so only an explicit "lives elsewhere" or "cannot be changed
# here" statement counts, never the bare words "external", "beyond" or "not".
_PLACE = r"(?:this|the|our)\s+(?:checkout|repo(?:sitory)?|workspace|codebase)"
_VERBS = r"(?:changed|modified|edited|updated|replaced)"
_ELSEWHERE = (
    re.compile(
        r"\b(?:is|are|lives?|sits?|resides?|runs?|is\s+owned|is\s+hosted)\s+(?:outside|not\s+in)\s+" + _PLACE + r"\b"
    ),
    re.compile(
        r"\b(?:cannot|can['\u2019]?t|can\s+not|unable\s+to|won['\u2019]?t|will\s+not)\s+be\s+" + _VERBS + r"\s+"
        r"(?:here\b|in\s+" + _PLACE + r"\b)"
    ),
    re.compile(
        r"\b(?:lives?\s+in|is\s+(?:owned|hosted|maintained)\s+by|is\s+part\s+of)\s+"
        r"(?:another|a\s+different|a\s+separate|the\s+other)\s+(?:repository|repo|service|team)\b"
    ),
)
_PROHIBITION = re.compile(r"^\s*(?:do\s+not|don['\u2019]?t|never|must\s+not|avoid)\b")
_NOT_A_COMPONENT = re.compile(r"scope|dependenc|\buses\b|\bcalls\b|external\s+(?:api|librar)")


def places_outside_checkout(note) -> bool:
    """Whether a contract note says a component lives elsewhere or cannot be changed here."""
    text = " ".join(str(note or "").lower().split())
    if _PROHIBITION.search(text) or _NOT_A_COMPONENT.search(text):
        return False
    return any(pattern.search(text) for pattern in _ELSEWHERE)


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for key, item in value.items():
            # A criterion's source citations are verbatim brief copies by construction; counting
            # them as what the criterion proves would mask every proxy behind the user's own words.
            if key == "sources":
                continue
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _notes(contract):
    for name in ("constraints", "scope_exclusions"):
        for item in (contract or {}).get(name) or []:
            if isinstance(item, str):
                yield item
    for row in (contract or {}).get("accepted_assumptions") or []:
        if isinstance(row, str):
            yield row
        elif isinstance(row, dict) and isinstance(row.get("text"), str):
            yield row["text"]


def _cited(evidence, criteria, behaviors):
    """What ``evidence`` cites: every criterion whose ID it names as a whole token (the id-token idea
    of ``coverage_errors``), or the required behavior the evidence equals. Each is its display text
    and the whitespace-normalised text of every field it holds."""
    text = str(evidence or "")
    found = []
    for behavior in behaviors:
        norm = " ".join(str(behavior).split())
        if norm == " ".join(text.split()):
            found.append((norm, norm))
    for row in criteria:
        cid = str(row.get("id") or "")
        if not cid or not re.search(r"(?<![A-Za-z0-9_])" + re.escape(cid) + r"(?![A-Za-z0-9_])", text):
            continue
        display = " ".join(str(row.get("criterion") or "").split())
        fields = " ".join(" ".join(str(part).split()) for part in _strings(row))
        found.append((display or fields, fields))
    return found


def proxy_gaps(requirements, trace, contract):
    """The covered requirements a stand-in criterion discharges (#2068).

    When the plan's own constraints, scope exclusions or accepted assumptions place a component
    outside this checkout (or say it cannot be changed here), a criterion traced to a user-visible
    requirement may still prove only an internal value the user never sees. Each such row whose
    cited criteria name none of the requirement's inline-code literals is one gap: the review owes
    the person the question of whether the internal proof reaches the visible result at all."""
    if not any(places_outside_checkout(item) for item in _notes(contract)):
        return []
    behaviors = [row for row in (contract or {}).get("required_behaviors") or [] if isinstance(row, str)]
    criteria = [row for row in (contract or {}).get("acceptance_criteria") or [] if isinstance(row, dict)]
    by_id = {row.get("id"): row for row in requirements or [] if isinstance(row, dict)}
    gaps = []
    for row in trace or []:
        if not isinstance(row, dict) or row.get("disposition") != "covered":
            continue
        requirement = by_id.get(row.get("requirement_id"))
        if not isinstance(requirement, dict):
            continue
        stated = [part for part in (requirement.get("text"), requirement.get("source_quote")) if isinstance(part, str)]
        found = brief_literals.literals([str(part or "") for part in stated])
        if not found:
            continue
        cited = _cited(row.get("evidence"), criteria, behaviors)
        if any(literal in fields for literal in found for _, fields in cited):
            continue
        gaps.append(
            {
                "requirement_id": row["requirement_id"],
                "literal": found[0],
                "criteria": [display for display, _ in cited],
            }
        )
    return gaps


def unrecorded_conflict_error(ids, conflict_sets):
    # The repair turn sees only this text, so name what would be accepted: a
    # saved answer that changes a requirement is not a recorded conflict, and
    # without the recorded sets the model keeps guessing at new ID groupings.
    message = "Conflict resolution must name exactly one recorded requirement conflict"
    if not conflict_sets:
        return (
            f"{message}: {', '.join(ids)} is not one, and no requirement conflict is recorded in the "
            "requirements handoff or its history, so conflict_resolutions must be []. Record a saved "
            "answer that changes a requirement in contract_changes or accepted_assumptions instead"
        )
    recorded = "; ".join("[" + ", ".join(sorted(group)) + "]" for group in sorted(sorted(g) for g in conflict_sets))
    return (
        f"{message}: {', '.join(ids)} is not one. Recorded conflicts: {recorded}. Resolve only those, "
        "each listed once with its exact requirement IDs, or leave conflict_resolutions []"
    )
