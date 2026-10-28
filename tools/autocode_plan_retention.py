"""Hash-bound references to already validated planning text.

Model reports may retain a criterion or final contract without copying its prose.
References expand before the ordinary schema, provenance and approval guards;
no semantic equivalence guess, new authority, or validation bypass is involved.
A Planner stage may write its criteria as a delta on the retained contract, and
the final reviewer may correct acceptance criteria, and nothing else, with the same
delta inside its contract reference (#1991).
"""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_plan_revision_reference as revision_reference
    from . import autocode_util as util
except ImportError:
    import autocode_plan_revision_reference as revision_reference
    import autocode_util as util

# The default flow is affected by the incident. V2 keeps its separately sealed
# artifact protocol; changing that protocol is outside this fix.
AUTHORS = {"astra_discovery", "glm_revise", "astra_finalize"}
FINAL = {"astra_finalize"}
RULE = """
RETAIN VALIDATED TEXT. retained_criteria lists hash-bound references to the current
contract's criteria. For an unchanged criterion, return that reference object in
contract.acceptance_criteria instead of copying or paraphrasing its wording.
The runner expands it to the exact saved row before all ordinary checks. New or
user-authorized changed criteria use full rows and the existing change receipts.
When adding a new human source binding to an existing criterion, use a full row
with its exact saved wording and proof; do not paraphrase the inherited behavior.
A reference cannot add, remove or alter behavior, proofs, sources or permissions.
"""
DELTA_RULE = """
CRITERIA DELTA. Prefer contract.acceptance_criteria={"retain_from":retained_contract,
"upsert":[],"remove":[]} instead of retranscribing every unchanged criterion or hash.
Copy the one current retained_contract token exactly. In upsert put only full changed
or new criterion rows, with their existing or new IDs; remove lists only IDs to remove.
The runner preserves every other row exactly and in order, replaces existing rows
in place, and appends new rows in upsert order. An empty delta retains every criterion.
Do not repeat unchanged rows. Actual edits/removals still require the ordinary change
receipts, source coverage and independent review; this syntax grants no authority.
Other contract fields and concern responses are still required by the saved schema.
"""
PROOF_RULE = """
PROOF-ONLY DELTA. To correct only a criterion's verification_method, keep upsert=[]
for that ID and add proofs=[{"id":"AC7","verification_method":"the corrected proof"}]
to the criteria delta. The runner copies its criterion, sources, human_review and
every other saved field exactly. Never retype or paraphrase a criterion merely to
fix a test selector. Use upsert only for genuinely changed/new criterion content.
All proof authority checks still apply. A valid test:/guard: obligation cannot
become an ordinary command. In an unapproved model draft only, if test: was put
before a literal Python suite command, correct it by removing exactly that prefix;
keep the command identical, without adding prose. Approved or human-provided
proofs still need their existing user authority.
"""
FINAL_PROMPT = """You are the independent Plan Reviewer making the final planning decision.
Settle EVERY concern by ID against the exact current plan and evidence-backed
Planner responses. Check dependencies, safety, source coverage and the executable
first task. Return a contract reference and concise decisions/rationales/tests.
Raise unresolved blockers as questions; do not author another plan or implement.
The user must still approve the exact reviewed plan before implementation.
"""
FINAL_RULE = """
FINAL REVIEW, NOT PLAN REAUTHORING. Independently settle every concern against the
current goal_contract and the Planner's responses; inspect source where needed
to decide a concern. Do not redo the bug investigation or re-run the complete
behavioral suite merely to copy a plan. When the current plan needs no change,
return contract={"retain_contract": retained_contract} and requirement_trace=[];
the runner retains that exact contract and its validated Planner trace when available.
If no validated trace is available, supply requirement_trace for the ordinary
source-coverage checks. If that
contract has no initial_task yet, include only the newly proposed contract.initial_task
inside the reference object; it receives the ordinary task/permission checks. Return
contract_changes=[] and conflict_resolutions=[] in that case. This reference
satisfies instructions to return the final contract without rewriting it.
If a concern remains unresolved, put it in contract.open_blocking_questions; the
runner retains existing obligations and makes the task non-executable. Never mark
an unresolved concern resolved to use retention. The final reviewer does not
rewrite permissions, milestones or the technical approach, and changes nothing the
reference object does not offer; the Planner owns those revisions.
Independent observations, source_trace and concern decisions are still yours to
supply and are checked normally. Retention is not user approval.
"""
FINAL_DELTA_RULE = """
FINAL CRITERIA CORRECTION. Beside retain_contract, always include
contract.acceptance_criteria={"retain_from":retained_contract,"upsert":[],"remove":[],
"proofs":[]}, copying the same current token; empty, it retains every criterion.
A concern that only an acceptance criterion settles (a wrong or missing criterion, a
proof naming the wrong test) is yours to correct there: full changed or new rows in
upsert, IDs to drop in remove. The runner keeps every other row exactly and in order.
Each change passes the guards a Planner revision passes: a removed or reworded
protected criterion needs its contract_changes receipt with a saved user answer or
feedback, source coverage still applies, and a test:/guard: proof cannot become a
plain command; this syntax grants no authority. requirement_trace=[] still inherits
the validated Planner trace, which must still cover every requirement by criterion ID
after your change; otherwise supply requirement_trace in full.
"""


def final_rule(schema=None):
    """An older pinned final-review schema without the criteria delta is never told to emit one."""
    return FINAL_RULE + (FINAL_DELTA_RULE + PROOF_RULE if schema is None or allows_delta(schema) else "")


def token(state: RunState):
    contract = state.get("goal_contract") or {}
    if not contract.get("hash") or not contract.get("revision"):
        return None
    return f"r{contract['revision']}:{contract['hash']}"


def inventory(state: RunState):
    return [
        {"retain_criterion": row["id"], "sha256": util.digest(row)}
        for row in ((state.get("goal_contract") or {}).get("body") or {}).get("acceptance_criteria", [])
    ]


def model_schema(schema, state: RunState, stage):
    """Only generation syntax changes; saved contracts keep their existing schema."""
    if stage not in AUTHORS or not token(state) or "contract" not in schema.get("properties", {}):
        return schema
    result = copy.deepcopy(schema)
    body = result["properties"]["contract"]
    criteria = body["properties"]["acceptance_criteria"]
    full_row = copy.deepcopy(criteria["items"])
    reference: dict = {
        "type": "object",
        "properties": {"retain_criterion": {"type": "string"}, "sha256": {"type": "string"}},
        "required": ["retain_criterion", "sha256"],
        "additionalProperties": False,
    }
    criteria["items"] = {"anyOf": [reference, criteria["items"]]}
    delta = {
        "type": "object",
        "properties": {
            "retain_from": {"type": "string", "enum": [token(state)]},
            "upsert": {"type": "array", "items": full_row},
            "remove": {"type": "array", "items": {"type": "string"}},
            "proofs": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {"id": {"type": "string"}, "verification_method": {"type": "string"}},
                    "required": ["id", "verification_method"],
                    "additionalProperties": False,
                },
            },
        },
        "required": ["retain_from", "upsert", "remove"],
        "additionalProperties": False,
    }
    if stage not in FINAL:
        body["properties"]["acceptance_criteria"] = {"anyOf": [criteria, delta]}
    if stage in FINAL:
        # The final reviewer corrects acceptance criteria only, and only as a delta on the
        # retained contract (#1991); the rest of the plan stays the Planner's to revise.
        reference = {
            "type": "object",
            "properties": {"retain_contract": {"type": "string"}, "acceptance_criteria": delta},
            "required": ["retain_contract"],
            "additionalProperties": False,
        }
        if "initial_task" not in ((state.get("goal_contract") or {}).get("body") or {}):
            reference["properties"]["initial_task"] = copy.deepcopy(body["properties"]["initial_task"])
            reference["required"].append("initial_task")
        reference["properties"]["open_blocking_questions"] = copy.deepcopy(
            body["properties"]["open_blocking_questions"]
        )
        reference["required"].append("open_blocking_questions")
        result["properties"]["contract"] = reference
    if stage == "glm_revise":
        result["properties"]["contract"] = revision_reference.model_schema(body, token(state))
    return result


def _delta_offers(schema):
    """The criteria shapes a generation schema offers: the author's branches, or the final reference's delta."""
    body = schema.get("properties", {}).get("contract", {})
    offers = []
    for contract in body.get("anyOf", [body]):
        criteria = contract.get("properties", {}).get("acceptance_criteria", {})
        offers.extend(branch.get("properties", {}) for branch in criteria.get("anyOf", [criteria]))
    return offers


def allows_delta(schema):
    """A repair must obey its pinned generation schema, including older array-only reports."""
    return any("retain_from" in offer for offer in _delta_offers(schema))


def delta_rule(schema=None):
    """Old pinned repair schemas must never be told to emit a new protocol field."""
    offered = schema is None or any("proofs" in offer for offer in _delta_offers(schema))
    return DELTA_RULE + (PROOF_RULE if offered else "")


def apply_delta(rows, delta):
    """The row operations of a well-formed delta on one criteria list.

    Rows named in upsert are replaced in place, new ones appended in upsert order, removed IDs
    dropped and proofs copied onto their otherwise unchanged row. Applied to a list the delta
    already produced, it changes nothing, which is how a saved reference is compared with the
    contract the runner expanded from it."""
    upsert, remove, proofs = delta.get("upsert") or [], delta.get("remove") or [], delta.get("proofs") or []
    originals = {row["id"]: row for row in rows}
    replacements = {row["id"]: row for row in upsert}
    replacements.update(
        {
            row["id"]: {**originals[row["id"]], "verification_method": row["verification_method"]}
            for row in proofs
            if row["id"] in originals
        }
    )
    return copy.deepcopy(
        [replacements.get(row["id"], row) for row in rows if row["id"] not in remove]
        + [row for row in upsert if row["id"] not in originals]
    )


def _expand_delta(state: RunState, stage, delta, *, reference=False):
    """Apply exact row operations to one hash-bound contract; never guess a copied identity.

    A Planner stage writes the delta as its contract's acceptance_criteria; the final
    reviewer only inside its retained-contract reference (``reference``)."""
    old = ((state.get("goal_contract") or {}).get("body") or {}).get("acceptance_criteria", [])
    upsert, remove, proofs = delta.get("upsert"), delta.get("remove"), delta.get("proofs", [])
    if (
        (stage not in FINAL if reference else stage not in AUTHORS - FINAL)
        or set(delta) - {"proofs"} != {"retain_from", "upsert", "remove"}
        or not token(state)
        or delta["retain_from"] != token(state)
        or not isinstance(upsert, list)
        or not isinstance(remove, list)
        or not isinstance(old, list)
        or not isinstance(proofs, list)
        or any(
            not isinstance(row, dict)
            or set(row) != {"id", "verification_method"}
            or any(not isinstance(row[key], str) or not row[key].strip() for key in ("id", "verification_method"))
            for row in proofs
        )
        or any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"] for row in [*old, *upsert]
        )
        or any(not isinstance(cid, str) or not cid for cid in remove)
    ):
        raise ValueError("Retained criteria delta is stale, malformed or not a criterion-author report")
    ids, changed, proof_ids = [row["id"] for row in old], [row["id"] for row in upsert], [row["id"] for row in proofs]
    if (
        len(set(ids)) != len(ids)
        or len(set(changed)) != len(changed)
        or len(set(remove)) != len(remove)
        or not set(remove) <= set(ids)
        or set(remove) & set(changed)
        or len(set(proof_ids)) != len(proof_ids)
        or not set(proof_ids) <= set(ids)
        or set(proof_ids) & (set(changed) | set(remove))
    ):
        raise ValueError("Retained criteria delta has duplicate, unknown or conflicting IDs")
    return apply_delta(old, delta)


def validated_trace(state: RunState):
    """Inherit a trace only from the report that produced this exact contract."""
    contract = state.get("goal_contract") or {}
    origin = str(contract.get("origin") or "")
    previous = {"glm_draft": "astra_discovery"}.get(origin, origin)
    report = ((state.get("planning") or {}).get("reports") or {}).get(previous, {}).get("report") or {}
    saved, proposed = contract.get("body"), report.get("contract")
    if not isinstance(saved, dict) or not isinstance(proposed, dict):
        return None
    sealed = {"brief_acceptance", "risk_acceptance", "source_trace"}
    if {k: v for k, v in saved.items() if k not in sealed} != {k: v for k, v in proposed.items() if k not in sealed}:
        return None
    trace = report.get("requirement_trace")
    return copy.deepcopy(trace) if isinstance(trace, list) else None


def expand(state: RunState, stage, value):
    """Resolve only explicit, current references; leave real changes to the guards."""
    body = value.get("contract")
    if not isinstance(body, dict):
        return value
    if "revise_contract" in body:
        value = revision_reference.expand(
            value, stage, token(state), (state.get("goal_contract") or {}).get("body"), validated_trace(state)
        )
    result = copy.deepcopy(value)
    body = result["contract"]
    if "retain_contract" in body:
        saved_contract = state.get("goal_contract") or {}
        allowed = {"retain_contract", "open_blocking_questions", "acceptance_criteria"}
        if "initial_task" not in saved_contract.get("body", {}):
            allowed.add("initial_task")
        if (
            stage not in FINAL
            or not set(body) <= allowed
            or not token(state)
            or body["retain_contract"] != token(state)
        ):
            raise ValueError("Retained contract reference is stale, malformed or not a final review")
        delta = body.get("acceptance_criteria")
        if delta is not None and not isinstance(delta, dict):
            raise ValueError("Retained contract reference corrects criteria only through a criteria delta")
        # The final reviewer's criteria delta (#1991) takes the change receipts a Planner revision
        # would; a reference that changes no criterion declares none. Conflicts stay the Planner's.
        corrected = isinstance(delta, dict) and any(delta.get(key) for key in ("upsert", "remove", "proofs"))
        if result.get("conflict_resolutions") or (result.get("contract_changes") and not corrected):
            raise ValueError("Retained contract cannot also declare changes or replace its validated trace")
        result["contract"] = copy.deepcopy(saved_contract["body"])
        if delta is not None:
            result["contract"]["acceptance_criteria"] = _expand_delta(state, stage, delta, reference=True)
        if "initial_task" in body:
            result["contract"]["initial_task"] = copy.deepcopy(body["initial_task"])
        questions = body.get("open_blocking_questions", [])
        if not isinstance(questions, list):
            raise ValueError("Retained contract blocking questions must be a list")
        if questions:
            # A reviewer that echoes a question the contract already holds adds nothing; ids stay unique.
            held = result["contract"].get("open_blocking_questions", [])
            known = {row.get("id") for row in held if isinstance(row, dict)}
            result["contract"]["open_blocking_questions"] = [
                *held,
                *(copy.deepcopy(row) for row in questions if not (isinstance(row, dict) and row.get("id") in known)),
            ]
            result["contract"]["technical_approach"] = []
            result["contract"]["milestones"] = []
            first = result["contract"].get("initial_task")
            if first is not None:
                result["contract"]["initial_task"] = {
                    key: "none" if key == "kind" else [] if isinstance(item, list) else ""
                    for key, item in first.items()
                }
        trace = validated_trace(state)
        if trace is not None and "requirement_trace" in result:
            if result["requirement_trace"] and result["requirement_trace"] != trace:
                if not corrected:
                    raise ValueError("Retained contract cannot replace its validated trace")
                # A corrected criterion set may need its own trace; the ordinary coverage checks judge it.
            else:
                result["requirement_trace"] = trace
        return result
    old = {
        row["id"]: row for row in ((state.get("goal_contract") or {}).get("body") or {}).get("acceptance_criteria", [])
    }
    if isinstance(body.get("acceptance_criteria"), dict):
        body["acceptance_criteria"] = _expand_delta(state, stage, body["acceptance_criteria"])
    for index, row in enumerate(body.get("acceptance_criteria") or []):
        if not isinstance(row, dict) or "retain_criterion" not in row:
            continue
        saved = old.get(row.get("retain_criterion"))
        if (
            stage not in AUTHORS
            or set(row) != {"retain_criterion", "sha256"}
            or not saved
            or row.get("sha256") != util.digest(saved)
        ):
            raise ValueError("Retained criterion reference is stale, unknown or malformed")
        body["acceptance_criteria"][index] = copy.deepcopy(saved)
    return result


def compact_final_packet(packet):
    """Remove repeated author material; retain every requirement, conflict and concern."""
    handoff = packet.get("requirements_handoff")
    if isinstance(handoff, dict) and isinstance(handoff.get("report"), dict):
        packet["requirements_handoff"] = {
            **{key: value for key, value in handoff.items() if key != "report"},
            "report": {
                key: value
                for key, value in handoff["report"].items()
                if key in ("requirements", "conflicts", "open_questions", "ignored_requirements")
            },
        }
    # The latest human text and its source IDs are already in source_inventory;
    # keep receipt identities and authority, not duplicate display/approval prose.
    packet["user_events"] = [
        {
            key: value
            for key, value in event.items()
            if key in ("kind", "id", "actor", "answer_id", "contract_token", "token", "text")
        }
        for event in packet.get("user_events", [])
    ]
