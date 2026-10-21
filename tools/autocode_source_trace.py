"""Trace every acceptance criterion to the run's human sources (#595).

The brief_acceptance manifest covers one bounded kind of declaration (a CLI's
backticked exact output). Every other obligation reached completion through
derived text alone: a criterion could invent a requirement nobody asked for, a
sentence of the brief could silently drop, and a quoted limit, literal or
negation could change between the brief and the criteria that "cover" it. This
module is the runner's mechanical check for the rest of the brief:

- Each criterion may carry ``sources``: citations of the run's human sources
  (autocode_source_log / brief_obligations.sources), each a source id and a
  quote copied verbatim from that source's text.
- The Plan Reviewer's report may carry ``source_trace``: the source sentences
  it deliberately leaves uncovered (``out_of_scope``) and the quoted spans
  whose negation it accepts dropping (``negation_waivers``), each with a
  reason. The runner seals that judgment into the contract body with the
  review's execution provenance, the same way brief_acceptance seals
  observations; a Planner, Builder or edited contract cannot replace it. A
  report without the field seals the strictest judgment (nothing out of
  scope, no dropped negation); the strict generation schema requires the
  field, so a reviewer that means to excuse something must say so.
- At approval and at completion (validate_body ready=True) a contract whose
  criteria cite sources must cite one for every criterion, cover every source
  sentence by a citation or an out-of-scope record, and keep every number,
  backticked literal and negation inside the cited sentences in the citing
  criteria (or the contract's restated behaviors, constraints and approach,
  where obligations are allowed to live). A dropped negation stands only with
  the reviewer's pinned waiver.

Contracts sealed before this change carry no source_trace key, and contracts
whose criteria cite nothing, and both validate as before. Imports nothing
from AutoCode beyond the shared helpers and the literal and sentence
machinery it builds on; RunState is imported for type checking only.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import re
from pathlib import Path

try:
    from . import autocode_brief_acceptance as acceptance
    from . import autocode_brief_literals as brief_literals
    from . import autocode_prompts as prompts
    from . import autocode_requirement_cues as cues
    from . import autocode_requirements_review as requirements_review
    from . import autocode_util as util
    from .autocode_requirement_coverage import requirement_coverage_text
except ImportError:
    import autocode_brief_acceptance as acceptance
    import autocode_brief_literals as brief_literals
    import autocode_prompts as prompts
    import autocode_requirement_cues as cues
    import autocode_requirements_review as requirements_review
    import autocode_util as util
    from autocode_requirement_coverage import requirement_coverage_text

KEY = "source_trace"
TRACE_VERSION = 1
REVIEW_STAGES = frozenset({"astra_challenge", "astra_finalize", "plan_finalize"})
WRAPPER_KEYS = {"version", "out_of_scope", "negation_waivers", "review"}
REVIEW_KEYS = {"stage", "events", "events_sha256", "output", "output_sha256", "trace_sha256"}

# Dotted runs are one token: an IP (127.0.0.1) or version is a single stated
# value, while a range's double dot (1..100) still separates its two numbers.
_NUMBER = re.compile(r"\d+(?:\.\d+)*")
# A standard's designation names the standard, not a value the request states: "ISO 8601 weeks"
# is satisfied by criteria about ISO weeks without repeating 8601 (#1689). Version-like suffixes
# stay part of the name (ISO/IEC 27001:2022, RFC 9110, HTTP/2, UTF-8, SHA-256, PEP 8).
_STANDARD = re.compile(
    r"\b(?:ISO(?:/IEC)?|IEC|RFC|IEEE|ECMA(?:-)?|ANSI|PEP|UTF|SHA|HTTP|TLS|IPv)[-/ ]?\d+(?:[-:.]\d+)*(?!\w)"
)
_NEGATION = re.compile(r"\b(?:must not|never|do not|don't|cannot|can't|not|no)\b", re.I)


def _number_in(number, text):
    # Not inside a longer number or a decimal fragment; a range dot (1..100) or a
    # sentence-final period still counts as the stated number.
    return re.search(r"(?<!\d)(?<!\d\.)" + re.escape(number) + r"(?!(?:\d|\.\d))", text) is not None


def any_citations(body):
    """True when at least one criterion carries a non-empty sources list."""
    return any(row.get("sources") for row in body.get("acceptance_criteria") or [])


def reviewer_evidence(record):
    """True when the record names a Plan Reviewer stage with retained events and output."""
    if record.get("stage") not in REVIEW_STAGES:
        return False
    events = Path(record.get("events") or "")
    output = Path(record.get("output") or "")
    return not events.is_symlink() and events.is_file() and not output.is_symlink() and output.is_file()


def check_citations(body, records):
    """Every criterion citation names a live source and quotes it exactly."""
    by_id = {row["id"]: row for row in records}
    for row in body.get("acceptance_criteria") or []:
        citations = row.get("sources")
        if citations is None:
            continue
        # An empty array means uncited: allowed in a draft (strict output schemas
        # force the key), refused by the ready gate like a missing field.
        if not isinstance(citations, list):
            raise ValueError(f"Criterion {row.get('id')} sources must be an array")
        seen = set()
        for citation in citations:
            if not isinstance(citation, dict) or set(citation) != {"source_id", "quote"}:
                raise ValueError(f"Criterion {row.get('id')} sources hold only source_id and quote")
            source = by_id.get(citation.get("source_id"))
            quote = str(citation.get("quote", "")).strip()
            if source is None or not quote or quote not in source["text"]:
                raise ValueError(
                    f"Criterion {row.get('id')} cites {citation.get('source_id')} without quoting it verbatim"
                )
            key = (citation["source_id"], quote)
            if key in seen:
                raise ValueError(f"Criterion {row.get('id')} cites {citation['source_id']} twice with one quote")
            seen.add(key)


def _covered(sentence, quotes, *, spans=False):
    """A quote covers a sentence when it names that sentence, normalized.

    Criterion citations may use ``spans=True``: a contiguous verbatim span
    inside the sentence counts (a live planner often cites the command clause,
    not the ``Commands:`` lead-in). Out-of-scope and negation waivers still
    require a whole-sentence quote so a fragment cannot excuse the rest.
    """
    normalized = requirement_coverage_text(sentence)
    bare = normalized.removesuffix(".")
    for raw in quotes:
        quote = requirement_coverage_text(str(raw))
        if not quote:
            continue
        clipped = quote.removesuffix(".")
        if bare in clipped or bare == clipped or normalized in quote:
            return True
        if spans and len(clipped) >= 12 and (clipped in bare or quote in normalized):
            return True
    return False


def _normalize_entries(value, label, records, *, negation=False):
    if not isinstance(value, list):
        raise ValueError(f"source_trace {label} must be an array")
    by_id = {row["id"]: row for row in records}
    normalized, seen = [], set()
    for entry in value:
        if not isinstance(entry, dict) or set(entry) != {"source_id", "quote", "reason"}:
            raise ValueError(f"source_trace {label} entries hold only source_id, quote and reason")
        source = by_id.get(entry.get("source_id"))
        quote = str(entry.get("quote", "")).strip()
        if source is None or not quote or quote not in source["text"]:
            raise ValueError(f"source_trace {label} cites {entry.get('source_id')} without quoting it verbatim")
        if not str(entry.get("reason", "")).strip():
            raise ValueError(f"source_trace {label} entry for {entry['source_id']} needs a reason")
        if negation and not _NEGATION.search(quote):
            raise ValueError("A negation waiver quotes a span with no negation")
        key = (entry["source_id"], quote)
        if key in seen:
            raise ValueError(f"source_trace {label} repeats {entry['source_id']}")
        seen.add(key)
        normalized.append({"source_id": entry["source_id"], "quote": quote, "reason": str(entry["reason"]).strip()})
    return normalized


def _citing(criteria, sentence, source_id):
    """The criteria whose citations of this source cover ``sentence``."""
    return [
        row
        for row in criteria
        if _covered(
            sentence,
            [citation["quote"] for citation in row.get("sources") or [] if citation["source_id"] == source_id],
        )
    ]


def _corpus(body, citing):
    """Where an obligation may survive: the citing criteria, and the contract's
    restated behaviors, constraints and approach."""
    parts = [str(row.get("criterion", "")) + " " + str(row.get("verification_method", "")) for row in citing]
    parts += [
        str(text) for key in ("required_behaviors", "constraints", "technical_approach") for text in body.get(key) or []
    ]
    return " ".join(" ".join(part.split()) for part in parts)


def _bound_literals(body, citing):
    """Literals a sealed brief-acceptance observation binds to one of the citing criteria:
    the behavioral carry for an output format a human amendment replaced (#595)."""
    wrapper = body.get("brief_acceptance") or {}
    manifest = wrapper.get("manifest") or {}
    ids = {row.get("id") for row in citing}
    found = []
    for row in manifest.get("observations") or []:
        if ids & set((row.get("proposal") or {}).get("criterion_ids") or []):
            literal = (row.get("declaration") or {}).get("literal")
            if literal and literal not in found:
                found.append(literal)
    return found


def _inactive_literals(body, records, source_id):
    """Literals of this source's declarations the manifest inactivated: a later
    authenticated human amendment replaced them (the amendment chain is checked by
    the brief_acceptance machinery), so the sentence stating them is discharged."""
    wrapper = body.get("brief_acceptance") or {}
    manifest = wrapper.get("manifest") or {}
    inactive = set(manifest.get("inactive_declaration_ids") or [])
    if not inactive:
        return set()
    return {
        row["literal"]
        for row in acceptance.inventory(records)
        if row["id"] in inactive and row.get("source_id") == source_id and row.get("literal")
    }


def _survival_errors(body, records, waivers):
    errors = []
    criteria = body.get("acceptance_criteria") or []
    for record in records:
        for sentence in cues.sentences(record["text"]):
            citing = _citing(criteria, sentence, record["id"])
            if not citing:
                continue
            corpus = _corpus(body, citing)
            numbers = _NUMBER.findall(_STANDARD.sub(" ", sentence))
            lost = [number for number in dict.fromkeys(numbers) if not _number_in(number, corpus)]
            if lost:
                errors.append(f"Criteria citing {record['id']} changed a number the source states: " + ", ".join(lost))
            bound = set(_bound_literals(body, citing)) | _inactive_literals(body, records, record["id"])
            # The quotes themselves are not survival: a citation names the
            # obligation; the criterion must still carry it.
            stripped = [{key: value for key, value in row.items() if key != "sources"} for row in citing]
            pseudo = {"acceptance_criteria": stripped, "corpus": corpus}
            lost_literals = [
                literal
                for literal in brief_literals.missing(brief_literals.literals([sentence]), pseudo)
                if literal not in bound
            ]
            if lost_literals:
                errors.append(
                    f"Criteria citing {record['id']} dropped a literal the source states: "
                    + ", ".join(f"`{item}`" for item in lost_literals)
                )
            waived = any(
                record["id"] == waiver["source_id"] and _covered(sentence, [waiver["quote"]]) for waiver in waivers
            )
            if _NEGATION.search(sentence) and not _NEGATION.search(corpus) and not waived:
                errors.append(
                    f"Criteria citing {record['id']} dropped the source's negation "
                    f"({sentence[:80]!r}); only the Plan Reviewer's pinned waiver allows that"
                )
    return errors


def _coverage_errors(body, records, out_of_scope, *, coverage_records=None):
    criteria = body.get("acceptance_criteria") or []
    errors = []
    for record in records if coverage_records is None else coverage_records:
        quotes = [
            citation["quote"]
            for row in criteria
            for citation in row.get("sources") or []
            if citation["source_id"] == record["id"]
        ]
        excluded = [entry["quote"] for entry in out_of_scope if entry["source_id"] == record["id"]]
        for sentence in cues.sentences(record["text"]):
            covered = _covered(sentence, quotes, spans=True)
            excluded_here = _covered(sentence, excluded)
            if covered and excluded_here:
                errors.append(f"{record['id']} sentence is both covered and out of scope: {sentence[:80]!r}")
            elif not covered and not excluded_here:
                errors.append(f"{record['id']} sentence is neither cited nor out of scope: {sentence[:80]!r}")
    # Exclusions stay bound to the full authenticated inventory, even when a
    # new workflow turn scopes the obligations being checked to later sources.
    by_id = {row["id"]: row for row in records}
    for entry in out_of_scope:
        source = by_id.get(entry["source_id"])
        if source is None:
            errors.append(f"out_of_scope cites unknown source {entry['source_id']}")
        elif not any(_covered(sentence, [entry["quote"]]) for sentence in cues.sentences(source["text"])):
            errors.append(f"out_of_scope quote in {entry['source_id']} covers no source sentence")
    return errors


def quotable(records):
    """The sources a criterion must quote: all but a requirements review's bare go-ahead.

    A ``plan`` reply only starts planning. Requiring a quote of it cost live Planners reasoning, and a draft that
    rightly left it uncited failed the Plan Reviewer's seal (#1630). It stays a valid source to cite or exclude,
    so saved contracts that cite it still verify.
    """
    return [row for row in records if not requirements_review.approval_source(row)]


def _turn_scoped(state: RunState, records):
    """A receipted follow-up that changed jobs scopes obligations to its own turn
    (requirement_cues.new_workflow_turn); same-kind revisions keep every source."""
    turn = cues.new_workflow_turn(state)
    if not turn:
        return quotable(records)
    marker = "feedback:" + turn["event_id"]
    ids = [row["id"] for row in records]
    return quotable(records[ids.index(marker) :] if marker in ids else records)


def check_ready(body, records, wrapper, *, coverage_records=None):
    """The approval/completion gate for a body carrying the source-trace seal.

    A contract whose criteria cite nothing predates the trace (or a report sealed
    the strict default before the planner engaged it); it validates as before.
    Once any criterion cites, every criterion must, and coverage and survival
    apply in full."""
    criteria = body.get("acceptance_criteria") or []
    if not any(row.get("sources") for row in criteria):
        return
    uncited = [row.get("id", "?") for row in criteria if not row.get("sources")]
    if uncited:
        raise ValueError("Every criterion must cite its source; uncited: " + ", ".join(uncited))
    errors = _coverage_errors(body, records, wrapper["out_of_scope"], coverage_records=coverage_records)
    errors += _survival_errors(
        body, records if coverage_records is None else coverage_records, wrapper["negation_waivers"]
    )
    if errors:
        raise ValueError("The contract no longer traces to the original request: " + "; ".join(errors))


def _verify_review(wrapper):
    review = wrapper["review"]
    if not isinstance(review, dict) or set(review) != REVIEW_KEYS:
        raise ValueError("Invalid source-trace review provenance")
    path, output = Path(review["events"]), Path(review["output"])
    if (
        review["stage"] not in REVIEW_STAGES
        or path.is_symlink()
        or not path.is_file()
        or util.file_hash(path) != review["events_sha256"]
        or output.is_symlink()
        or not output.is_file()
        or util.file_hash(output) != review["output_sha256"]
    ):
        raise ValueError("Source-trace Plan Reviewer provenance is missing or changed")
    raw = util.read_object(output).get(KEY)
    # A report that names no source_trace seals the strict default (reviewed()).
    return raw if isinstance(raw, dict) else DEFAULT_TRACE


def verify(state: RunState, body, records, *, ready=False):
    """Check a body's source-trace seal against the review that pinned it."""
    wrapper = body.get(KEY)
    if wrapper is None:
        return
    if not isinstance(wrapper, dict) or set(wrapper) != WRAPPER_KEYS or wrapper["version"] != TRACE_VERSION:
        raise ValueError("Invalid source-trace record")
    raw = _verify_review(wrapper)
    out_of_scope = _normalize_entries(raw.get("out_of_scope"), "out_of_scope", records)
    waivers = _normalize_entries(raw.get("negation_waivers"), "negation_waivers", records, negation=True)
    normalized = {"out_of_scope": out_of_scope, "negation_waivers": waivers}
    if (
        util.digest(normalized) != wrapper["review"]["trace_sha256"]
        or out_of_scope != wrapper["out_of_scope"]
        or waivers != wrapper["negation_waivers"]
    ):
        raise ValueError("Source-trace Plan Reviewer provenance is missing or changed")
    if ready:
        check_ready(body, records, wrapper, coverage_records=_turn_scoped(state, records))


# A report without source_trace seals the strictest judgment: nothing is out of
# scope and no negation may drop. Only the reviewer's pinned output may loosen
# that; the strict generation schema requires the field, so a live reviewer who
# means to excuse something must say so, and a report repair tells it how.
DEFAULT_TRACE: dict[str, list] = {"out_of_scope": [], "negation_waivers": []}


def reviewed(state: RunState, body, trace, record, records):
    """Seal the Plan Reviewer's source judgment into the contract body."""
    if record.get("stage") not in REVIEW_STAGES:
        raise ValueError("Only the independent Plan Reviewer can originate a source trace")
    trace = DEFAULT_TRACE if trace is None else trace
    events, output = Path(record.get("events") or ""), Path(record.get("output") or "")
    if events.is_symlink() or not events.is_file():
        raise ValueError("A source trace requires the actual Plan Reviewer execution evidence")
    if output.is_symlink() or not output.is_file():
        raise ValueError("A source trace requires the actual Plan Reviewer report")
    out_of_scope = _normalize_entries(trace.get("out_of_scope"), "out_of_scope", records)
    waivers = _normalize_entries(trace.get("negation_waivers"), "negation_waivers", records, negation=True)
    normalized = {"out_of_scope": out_of_scope, "negation_waivers": waivers}
    result = dict(body)
    result[KEY] = {
        "version": TRACE_VERSION,
        "out_of_scope": out_of_scope,
        "negation_waivers": waivers,
        "review": {
            "stage": record["stage"],
            "events": str(events),
            "events_sha256": util.file_hash(events),
            "output": str(output),
            "output_sha256": util.file_hash(output),
            "trace_sha256": util.digest(normalized),
        },
    }
    # Seal-time coverage: incomplete citations repair before human approval.
    # Pending planner drafts keep the old seal until the next Plan Reviewer pass.
    if any_citations(result):
        check_ready(result, records, result[KEY], coverage_records=_turn_scoped(state, records))
    return result


def prepare(state: RunState, body, origin, record=None):
    """Carry or refuse a source-trace seal the same way brief_acceptance travels."""
    result = dict(body)
    previous = ((state.get("goal_contract") or {}).get("body") or {}).get(KEY)
    supplied = result.get(KEY)
    if supplied == previous:
        return result
    if supplied is None and previous is not None:
        result[KEY] = previous
        return result
    if origin in {"astra_finalize", "plan_finalize", "adaptive_review_approval"}:
        if supplied is not None and (record or {}).get("stage") != supplied["review"]["stage"]:
            raise ValueError("Source-trace observations do not belong to this Plan Reviewer execution")
        return result
    raise ValueError("A Planner, Builder or edited contract cannot replace an independently derived source trace")


def rule(records):
    """The planner/reviewer instruction naming the run's sources, from the prompt fragments.

    Lists ids only. Verbatim source text lives once in the handoff's source_inventory
    so a large program-child brief is not copied into the instruction (#1337).
    """
    return (
        prompts.get("fragments/autoplanner/source-citation-rule.md")
        + "\n"
        + "\n".join(f"  [{row['id']}] kind={row['kind']}" for row in quotable(records))
        + "\n"
    )
