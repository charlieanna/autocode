"""Pure rules for progressive planning inside one approved goal run.

A large goal keeps one fixed product contract and delivers reviewed, verified
end-to-end slices one at a time. This module holds only pure policy: proposal
validation, the sealed continuation delegation, requirement coverage across
slice revisions, the cumulative check obligations and immutable evidence
bindings. Callers supply normalized contract/plan data, required checks, product
findings and receipts as arguments; this module never reads run state and never
imports the controller, the lifecycle, findings, milestones or the planning
unit.

Check commands are parsed by the runner's real parser
(``autocode_verification_plan``): a promised machine check is valid only when
that parser extracts an explicit supported command from it. Parsing intent is
not a security sandbox.
"""

from __future__ import annotations

import math
import re
import shlex

try:
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
except ImportError:
    import autocode_util as util
    import autocode_verification_plan as verification_plan

VERSION = 1

RELATIONS = frozenset({"contributes_to", "fully_verify"})

DEFAULT_SLICE_REVIEW_CALLS = 2
DEFAULT_SLICE_STAGE_SECONDS = 5400
DEFAULT_RUN_MAX_SECONDS = 43200

DISCLOSURE_DELEGATION = "Progressive delegation:"
DISCLOSURE_SLICE = "Progressive slice:"
DISCLOSURE_OUTSTANDING = "Product criteria explicitly outstanding:"

# Path operands a check names must live in repository source or fixtures, never
# in run or session state a later replay could not see.
SESSION_ROOTS = (".git", ".autocode", ".scenario-runs", ".tmp-autopilot-testkit")

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}\Z")


def _text(value, where):
    if type(value) is not str or not value.strip() or "\x00" in value:
        raise ValueError(f"{where} must be a nonempty string without NUL")
    return value.strip()


def _id(value, where):
    value = _text(value, where)
    if not _ID.fullmatch(value) or ".." in value:
        raise ValueError(f"{where} must be a plain, non-traversing ID")
    return value


def _list(value, where):
    if type(value) is not list:
        raise ValueError(f"{where} must be a list")
    return value


def check_commands(check):
    """Return the check's replayable commands, or reject it as unverifiable.

    A promised machine check must extract at least one explicit supported
    command the runner can replay at the checkpoint; prose that merely sounds
    executable is invalid. The commands need not pass before the slice's
    implementation exists.
    """
    where = f"check {_text(check.get('id', ''), 'check id')}"
    _text(check.get("method", ""), f"{where} method")
    commands = verification_plan.commands(check["method"])
    if not commands:
        raise ValueError(
            f"{where} extracts no executable command; promise an explicit supported command "
            "the runner can replay, not prose"
        )
    for command in commands:
        if not verification_plan.executable(command):
            raise ValueError(f"{where} command is not a supported runner command: {command}")
        _validate_operands(command, where)
    return commands


def check_rows(proposal):
    """A proposal's slice check methods, labelled for verification_plan.refuse_new_plan.

    Refused only where an author's new proposal is accepted. check_commands and validate_revision also read
    saved plans, so a progressive plan saved before its rules keeps working.
    """
    return [
        (f"check {check.get('id', '')} method", check.get("method", ""))
        for row in proposal.get("slices") or []
        for check in row.get("checks") or []
    ]


def _validate_operands(command, where):
    try:
        words = shlex.split(command)
    except ValueError as error:
        raise ValueError(f"{where} command is not parseable: {error}") from error
    for word in words[1:]:
        operand = word.split("=", 1)[-1] if word.startswith("-") and "=" in word else word
        if not operand or operand.startswith("-"):
            continue
        parts = operand.split("/")
        if operand.startswith("/") or "\\" in operand or ".." in parts:
            raise ValueError(f"{where} command uses an absolute or traversing path operand: {word}")
        if any(part.rstrip(":") in SESSION_ROOTS for part in parts):
            raise ValueError(
                f"{where} command reads repository session state instead of repository source or fixtures: {word}"
            )
        if ":" in parts[0]:
            raise ValueError(f"{where} command names a remote or device path operand: {word}")


def validate_check(check, where="check"):
    """One slice check: identity, product-criterion mapping and replayable command."""
    _id(check.get("id", ""), f"{where} id")
    relation = check.get("relation")
    if relation not in RELATIONS:
        raise ValueError(f"{where} relation must be one of {sorted(RELATIONS)}")
    criteria = [
        _id(item, f"{where} criterion") for item in _list(check.get("criterion_ids", []), f"{where} criterion_ids")
    ]
    if not criteria:
        raise ValueError(f"{where} must reference at least one product criterion")
    commands = check_commands(check)
    return {
        "id": check["id"],
        "relation": relation,
        "criterion_ids": criteria,
        "method": check["method"],
        "commands": commands,
    }


def validate_slice(slice_row, criteria, where="slice"):
    """A delivery slice: observable result, bounded paths, criterion refs, real checks."""
    slice_id = _id(slice_row.get("id", ""), f"{where} id")
    _text(slice_row.get("intended_result", ""), f"{where} intended_result")
    mapped = sorted(
        {
            _id(item, f"{where} criterion")
            for item in _list(slice_row.get("criterion_ids", []), f"{where} criterion_ids")
        }
    )
    unknown = sorted(set(mapped) - set(criteria))
    if unknown:
        raise ValueError(f"{where} {slice_id} references unknown product criteria: {', '.join(unknown)}")
    if not mapped:
        raise ValueError(f"{where} {slice_id} must reference at least one product criterion")
    paths = [_path(item, f"{where} {slice_id} paths") for item in _list(slice_row.get("paths", []), f"{where} paths")]
    if not paths:
        raise ValueError(f"{where} {slice_id} must declare bounded writable paths")
    checks = _list(slice_row.get("checks", []), f"{where} checks")
    if not checks:
        raise ValueError(f"{where} {slice_id} must declare concrete checks")
    parsed_checks = [validate_check(check, f"{where} {slice_id} check") for check in checks]
    ids = [row["id"] for row in parsed_checks]
    if len(ids) != len(set(ids)):
        raise ValueError(f"{where} {slice_id} check IDs must be unique")
    for row in parsed_checks:
        unknown = sorted(set(row["criterion_ids"]) - set(mapped))
        if unknown:
            raise ValueError(
                f"{where} {slice_id} check {row['id']} references criteria the slice does "
                f"not carry: {', '.join(unknown)}"
            )
    depends_on = sorted(
        {_id(item, f"{where} depends_on") for item in _list(slice_row.get("depends_on", []), f"{where} depends_on")}
    )
    if type(slice_row.get("tentative")) is not bool:
        raise ValueError(f"{where} {slice_id} tentative must be a boolean")
    return {
        "id": slice_id,
        "criterion_ids": mapped,
        "depends_on": depends_on,
        "checks": parsed_checks,
        "tentative": slice_row["tentative"],
        "intended_result": slice_row["intended_result"],
        "paths": paths,
    }


def _path(value, where):
    value = _text(value, where).rstrip("/")
    parts = value.split("/")
    if (
        not value
        or value.startswith("/")
        or "\\" in value
        or ":" in value
        or any(part in ("", ".", "..") for part in parts)
    ):
        raise ValueError(f"{where} must be a repository-relative path without traversal")
    if any(part in SESSION_ROOTS for part in parts):
        raise ValueError(f"{where} may not point at repository session state ({value})")
    return value


def declares(proposal):
    """Whether a report actually proposes progressive planning.

    Generation schemas require every property, so a report always carries a
    proposal object; the all-empty placeholder a model emits for an ordinary
    goal means no proposal. Anything else is a declaration and must then be
    fully valid: a malformed proposal is refused (fail-closed), never guessed.
    """
    if proposal is None:
        return False
    if type(proposal) is not dict or type(proposal.get("needed_because", "")) is not str:
        return True
    content = bool(
        proposal.get("slices")
        or proposal.get("needed_because", "").strip()
        or proposal.get("shared_decisions")
        or proposal.get("outstanding_criteria")
        or proposal.get("done_slices")
    )
    version = proposal.get("version", 0)
    return content or type(version) is not int or version != 0


def validate_proposal(proposal, criteria, *, initial=False, verified_done=(), verified_criteria=()):
    """The progressive proposal against the fixed product criteria.

    ``verified_done`` and ``verified_criteria`` are boundary-authenticated
    completed slices and current product proof, never model claims. Together the slice
    criterion mappings, verified criteria and ``outstanding_criteria`` account for every one:
    planned on at least one slice, or explicitly outstanding. Exactly one slice
    is dispatchable (non-tentative) and it is the head of the sequence; future
    entries stay tentative until a reviewed revision promotes them. An initial
    proposal needs at least one follow-up slice, which is what makes it
    progressive rather than an ordinary plan.
    """
    if type(proposal) is not dict or type(proposal.get("version")) is not int or proposal.get("version") != VERSION:
        raise ValueError(f"progressive proposal version must be {VERSION}")
    _text(proposal.get("needed_because", ""), "proposal needed_because")
    criteria = sorted({_id(item, "criterion") for item in _list(list(criteria), "criteria")})
    verified = {_id(item, "verified criterion") for item in verified_criteria}
    completed = {_id(item, "verified slice") for item in verified_done}
    if verified - set(criteria):
        raise ValueError("verified_criteria names unknown product criteria")
    if initial and (verified or completed):
        raise ValueError("an initial proposal cannot use earlier verified proof")
    slices = _list(proposal.get("slices", []), "proposal slices")
    if initial and len(slices) < 2:
        raise ValueError("a progressive proposal needs a first slice and at least one future slice")
    if not slices:
        raise ValueError("a progressive proposal needs a dispatchable slice")
    parsed = [validate_slice(row, criteria, f"slice {index + 1}") for index, row in enumerate(slices)]
    slice_ids = [row["id"] for row in parsed]
    if len(slice_ids) != len(set(slice_ids)):
        raise ValueError("slice IDs must be unique")
    done = [_id(item, "done_slices") for item in _list(proposal.get("done_slices", []), "done_slices")]
    if len(done) != len(set(done)):
        raise ValueError("done_slices IDs must be unique")
    if initial and done:
        raise ValueError("an initial proposal cannot claim completed slices")
    if set(done) - completed:
        raise ValueError("done_slices claims slices without independently verified completion")
    if set(done) & set(slice_ids):
        raise ValueError(
            "a slice cannot be both completed and planned: " + ", ".join(sorted(set(done) & set(slice_ids)))
        )
    for row in parsed:
        if row["id"] in row["depends_on"]:
            raise ValueError(f"slice {row['id']} cannot depend on itself")
        unknown = sorted(set(row["depends_on"]) - set(slice_ids) - completed)
        if unknown:
            raise ValueError(f"slice {row['id']} depends on unknown slices: {', '.join(unknown)}")
    if parsed[0]["tentative"]:
        raise ValueError("the head slice must be concrete and dispatchable, not tentative")
    if set(parsed[0]["depends_on"]) - completed:
        raise ValueError("the head slice dependencies must be independently verified completed slices")
    graph = {row["id"]: set(row["depends_on"]) & set(slice_ids) for row in parsed}
    while graph:
        ready = {sid for sid, dependencies in graph.items() if not dependencies}
        if not ready:
            raise ValueError("slice dependency graph contains a cycle")
        graph = {sid: dependencies - ready for sid, dependencies in graph.items() if sid not in ready}
    for row in parsed[1:]:
        if not row["tentative"]:
            raise ValueError(
                f"slice {row['id']} is future work and must stay tentative until a reviewed revision promotes it"
            )
    outstanding = sorted(
        {
            _id(item, "outstanding criterion")
            for item in _list(proposal.get("outstanding_criteria", []), "outstanding_criteria")
        }
    )
    unknown = sorted(set(outstanding) - set(criteria))
    if unknown:
        raise ValueError("outstanding_criteria names unknown product criteria: " + ", ".join(unknown))
    planned = {criterion for row in parsed for criterion in row["criterion_ids"]}
    missing = sorted(set(criteria) - planned - set(outstanding) - verified)
    if missing:
        raise ValueError("the capability map must cover every product criterion; unmapped: " + ", ".join(missing))
    overlap = sorted(planned & set(outstanding))
    if overlap:
        raise ValueError("a criterion cannot be both planned on a slice and outstanding: " + ", ".join(overlap))
    for item in _list(proposal.get("shared_decisions", []), "proposal shared_decisions"):
        _text(item, "shared decision")
    return {
        "criteria": criteria,
        "slices": parsed,
        "outstanding": outstanding,
        "done": done,
        "verified_criteria": sorted(verified),
        "planned": {
            criterion: sorted(row["id"] for row in parsed if criterion in row["criterion_ids"]) for criterion in planned
        },
    }


def plan_identity(proposal):
    """Sealable identity of the initial plan content, computed before any approval.

    The hash covers the structured proposal only: it must exist before the
    contract token does, so the approval can seal it without a circular hash.
    """
    payload = {key: proposal[key] for key in sorted(proposal) if key != "version"}
    return util.digest({"version": VERSION, **payload})


def default_limits():
    return {
        "slice_review_calls": DEFAULT_SLICE_REVIEW_CALLS,
        "slice_stage_seconds": DEFAULT_SLICE_STAGE_SECONDS,
        "run_max_seconds": DEFAULT_RUN_MAX_SECONDS,
        "run_max_seconds_explicit_only": True,
    }


def normalize_limits(limits=None):
    """Normalize the frozen limits disclosed at initial approval.

    The boundary authenticates configured provenance before supplying a full
    limits record; this helper never reads settings or infers authority. Zero
    and None explicitly mean unlimited and normalize to None. Later authorized
    increases change the runtime ledger, not this initial approval grant.
    """
    if limits is None:
        return default_limits()
    if type(limits) is not dict or set(limits) != set(default_limits()):
        raise ValueError("progressive limits must contain the complete supported limits shape")
    if limits["run_max_seconds_explicit_only"] is not True:
        raise ValueError("progressive limits require explicit-increase-only provenance")
    normalized = {"run_max_seconds_explicit_only": True}
    for key in ("slice_review_calls", "slice_stage_seconds", "run_max_seconds"):
        value = limits[key]
        if value is None:
            normalized[key] = None
            continue
        types = (int,) if key == "slice_review_calls" else (int, float)
        if type(value) not in types or value < 0 or (type(value) is float and not math.isfinite(value)):
            raise ValueError(
                f"progressive limit {key} must be a finite nonnegative "
                + ("integer or None" if key == "slice_review_calls" else "number or None")
            )
        normalized[key] = None if value == 0 else int(value) if type(value) is float and value.is_integer() else value
    return normalized


def disclosure(proposal, criteria, *, limits=None):
    """Deterministic disclosure strings for the ordinary plan card fields.

    The delegation and its limits go to ``constraints``; the slice sequence goes
    to ``technical_approach``. Both are existing string-array fields, so the
    ordinary expanded plan card renders them with no renderer change.
    ``limits`` is the boundary-authenticated frozen initial configuration.
    """
    parsed = validate_proposal(proposal, criteria, initial=True)
    identity = plan_identity(proposal)
    shown_limits = {
        key: "unlimited" if value is None else str(value) for key, value in normalize_limits(limits).items()
    }
    constraints = [
        f"{DISCLOSURE_DELEGATION} initial approval delegates continuation within the agreed product "
        f"outcome, the fixed constraints and the granted permissions only. Product changes, new "
        f"permissions and unresolved product decisions return to the user. Limits: "
        f"{shown_limits['slice_review_calls']} plan-review calls and {shown_limits['slice_stage_seconds']} stage-seconds "
        f"per genuinely new slice, retries and splits sharing their lineage's pool; "
        f"{shown_limits['run_max_seconds']} whole-run accumulated provider-stage seconds, raised only by "
        f"explicit user increase. Every slice checkpoint re-runs the entire cumulative required-check "
        f"set. Plan identity: {identity}.",
    ]
    if parsed["outstanding"]:
        constraints.append(DISCLOSURE_OUTSTANDING + " " + ", ".join(parsed["outstanding"]))
    sequence = []
    for index, row in enumerate(parsed["slices"]):
        role = (
            "first slice, dispatchable after approval" if index == 0 else ("future slice, tentative, not dispatchable")
        )
        checks = "; ".join(
            f"{check['id']} [{' '.join(check['commands'])}] {check['relation']} {','.join(check['criterion_ids'])}"
            for check in row["checks"]
        )
        sequence.append(
            f"{DISCLOSURE_SLICE} {row['id']} ({role}): {row['intended_result']}; "
            f"criteria {','.join(row['criterion_ids'])}; "
            f"depends on {','.join(row['depends_on']) or 'nothing'}; "
            f"writable paths {','.join(row['paths'])}; checks {checks}"
        )
    return {"constraints": constraints, "technical_approach": sequence}


def check_disclosure(body, proposal, criteria, *, limits=None):
    """Reject a plan card whose disclosure does not match its structured proposal."""
    expected = disclosure(proposal, criteria, limits=limits)
    for field, lines in expected.items():
        shown = list(body.get(field) or [])
        for line in lines:
            if line not in shown:
                raise ValueError(f"plan card {field} does not show the generated disclosure line: " + line[:80] + "...")
        for line in shown:
            if line.startswith((DISCLOSURE_DELEGATION, DISCLOSURE_SLICE, DISCLOSURE_OUTSTANDING)) and line not in lines:
                raise ValueError(
                    f"plan card {field} carries a disclosure line the proposal does not generate: " + line[:80] + "..."
                )


def seal_delegation(proposal, contract_token, limits=None):
    """Bind the plan identity to the approval the user actually granted.

    Ordinary approval of a contract without a progressive declaration never
    produces a delegation; a model's assertion or a question default cannot
    grant this authority. Limits are a detached, validated initial grant, not
    live ledger settings. The boundary freezes their authenticated provenance
    before independent review and approval.
    """
    if type(contract_token) is not str or not contract_token:
        raise ValueError("delegation must be sealed to the displayed contract token")
    return {"plan_hash": plan_identity(proposal), "contract_token": contract_token, "limits": normalize_limits(limits)}


def require_delegation(
    progressive, *, contract_token=None, contract_body=None, contract_approved=False, contract_sealed=False
):
    """Validate initial delegation against the authenticated current approval.

    The application boundary supplies the actual current token/body and results
    of its approval and seal checks. These inputs are not cryptographic trust:
    a model report or a mutable record must never supply authentication flags.
    ``initial_plan`` is the immutable initial plan, not a later active revision.
    """
    if contract_approved is not True or contract_sealed is not True:
        raise ValueError("progressive delegation requires an authenticated current approved, sealed contract")
    _text(contract_token, "current contract token")
    if type(progressive) is not dict or progressive.get("version") != VERSION:
        raise ValueError("no versioned progressive delegation is approved; this run keeps the ordinary path")
    delegation = (progressive or {}).get("delegation")
    if type(delegation) is not dict or not delegation:
        raise ValueError("no progressive delegation is approved; this run keeps the ordinary path")
    if delegation.get("contract_token") != contract_token:
        raise ValueError("the progressive delegation was sealed to a different contract token")
    if not delegation.get("plan_hash"):
        raise ValueError("the progressive delegation has no sealed plan identity")
    plan = progressive.get("initial_plan") or {}
    if type(plan) is not dict:
        raise ValueError("delegation needs a normalized immutable initial plan")
    proposal = plan.get("proposal")
    if type(proposal) is not dict or type(contract_body) is not dict:
        raise ValueError("delegation needs its actual initial plan and approved contract body")
    criteria = [row["id"] for row in contract_body.get("acceptance_criteria") or []]
    validate_proposal(proposal, criteria, initial=True)
    identity = plan_identity(proposal)
    if identity != delegation["plan_hash"] or identity != plan.get("plan_hash"):
        raise ValueError("the initial plan does not match the sealed plan identity")
    if "limits" not in delegation or delegation["limits"] is None:
        raise ValueError("the delegation has no frozen approved limits")
    limits = normalize_limits(delegation["limits"])
    check_disclosure(contract_body, proposal, criteria, limits=limits)
    return delegation


def coverage(previous, proposed):
    """Compare two capability maps; nothing planned or outstanding may vanish."""
    before = set(previous["planned"]) | set(previous["outstanding"]) | set(previous["verified_criteria"])
    after = set(proposed["planned"]) | set(proposed["outstanding"]) | set(proposed["verified_criteria"])
    dropped = sorted(before - after)
    if dropped:
        raise ValueError("a slice revision cannot drop product criteria from the capability map: " + ", ".join(dropped))


def validate_revision(
    previous,
    proposed,
    criteria,
    *,
    established=(),
    retirement_grants=(),
    retirement_grants_authenticated=False,
    contract_token=None,
    verified_done=(),
    verified_criteria=(),
):
    """A slice revision may evolve the technical plan, never product coverage.

    Every product criterion the previous map accounted for stays accounted for.
    A head-slice check the revision replaces must be established, kept, or
    explicitly retired by a boundary-authenticated product-change grant. Grants
    name kind="product_change", check_id, check_hash, removes (the approved
    visible removal), and the current contract_token. The boundary verifies that
    the approved change authorizes exactly that removal before setting
    retirement_grants_authenticated=True. Arbitrary approval strings are not
    authority. Established obligations are carried forward (``carried``) minus those retirements, and a kept check that
    keeps its identity keeps its obligation; changing its method is a
    technical replacement that must produce new evidence.
    """
    before = validate_proposal(previous, criteria, verified_done=verified_done, verified_criteria=verified_criteria)
    parsed = validate_proposal(proposed, criteria, verified_done=verified_done, verified_criteria=verified_criteria)
    coverage(before, parsed)
    kept = {check["id"]: check for row in parsed["slices"][:1] for check in row["checks"]}
    established_ids = {check["id"] for check in established}
    owed = {check["id"]: check for row in before["slices"][:1] for check in row["checks"]}
    retired = {}
    if retirement_grants and retirement_grants_authenticated is not True:
        raise ValueError("check retirement requires authenticated approved product-change grants")
    for row in retirement_grants:
        if type(row) is not dict or row.get("kind") != "product_change":
            raise ValueError("retirement must be a normalized approved product-change grant")
        row = dict(row)
        check_id = _id(row.get("check_id", ""), "retirement check_id")
        _text(row.get("removes", ""), f"retirement {check_id} removes")
        _text(contract_token, "approved product-change contract token")
        if row.get("contract_token") != contract_token:
            raise ValueError(f"retirement {check_id} belongs to another product-change approval")
        old = owed.get(check_id) or next((check for check in established if check["id"] == check_id), None)
        if old is None or check_id in kept:
            raise ValueError("retirement entries name checks the revision does not remove: " + check_id)
        if row.get("check_hash") != check_identity(old):
            raise ValueError(f"retirement {check_id} does not match the approved removed obligation")
        if check_id in retired:
            raise ValueError("retirement check IDs must be unique")
        retired[check_id] = row
    vanished = sorted(set(owed) - set(kept) - established_ids - set(retired))
    if vanished:
        raise ValueError("a slice revision cannot silently drop check obligations: " + ", ".join(vanished))
    invented = sorted(set(retired) - ((set(owed) | established_ids) - set(kept)))
    if invented:
        raise ValueError("retirement entries name checks the revision does not remove: " + ", ".join(invented))
    carried = [check for check in established if check["id"] not in retired]
    replaced = []
    for check_id, row in kept.items():
        old = owed.get(check_id) or next((check for check in established if check["id"] == check_id), None)
        if old is None:
            continue
        if (row["relation"], tuple(row["criterion_ids"])) != (old["relation"], tuple(old.get("criterion_ids") or ())):
            raise ValueError(
                f"check {check_id} keeps its identity but changes its obligation; retire it "
                "with an approval or give the new obligation a new ID"
            )
        if row["method"] != old.get("method"):
            replaced.append(check_id)
    carried = [
        {**check, **kept[check["id"]], "verified_once": False} if check["id"] in replaced else dict(check)
        for check in carried
    ]
    return {"retired": retired, "replaced": replaced, "carried": carried}


def cumulative_checks(proposal, established=()):
    """The entire required-check set at a slice checkpoint.

    Version one re-runs everything: previously established product obligations
    and earlier slices' retained demonstrations, plus the head slice's checks.
    Reordering cannot drop an obligation and a prior PASS is never substituted.
    """
    rows, seen = [], {}
    candidates = [{**check, "origin": check.get("origin", "established")} for check in established]
    candidates += [{**check, "origin": row["id"]} for row in proposal["slices"][:1] for check in row["checks"]]
    for check in candidates:
        identity = check_identity(check)
        if check["id"] in seen:
            if seen[check["id"]] != identity:
                raise ValueError(
                    f"check {check['id']} has conflicting cumulative definitions; apply its reviewed replacement"
                )
            continue
        seen[check["id"]] = identity
        rows.append(check)
    return rows


def retain(checks, slice_id):
    """Carry a verified slice's checks into the cumulative checklist as obligations."""
    return [{**check, "origin": slice_id, "verified_once": True} for check in checks]


def bind_attempt(
    slice_id, task_id, attempt, *, plan_hash, assignment_source, validated_source=None, contract_token=None
):
    """Immutable binding captured before dispatch; reports prove against this."""
    _text(plan_hash, "binding sealed plan identity")
    _text(assignment_source, "binding assignment_source")
    if type(attempt) is not int or attempt < 1:
        raise ValueError("binding attempt must be a positive integer")
    if contract_token is not None:
        _text(contract_token, "binding contract_token")
    if validated_source is not None:
        _text(validated_source, "binding validated_source")
    return {
        "slice_id": _id(slice_id, "binding slice_id"),
        "task_id": _id(task_id, "binding task_id"),
        "attempt": int(attempt),
        "plan_hash": plan_hash,
        "assignment_source": assignment_source,
        "validated_source": validated_source,
        "contract_token": contract_token,
    }


def check_binding(binding, report, *, plan_hash, slice_id, task_id, attempt, validated_source, contract_token=None):
    """A report/repair/receipt proves exactly the bound attempt, nothing else.

    Assignment-source and validated-source snapshots are different legitimate
    identities; a receipt is never transplanted onto another plan, task or
    source. ``report`` is normalized by the boundary from authenticated runner
    records, not identities guessed from whatever plan is now active. A launch
    can precede validated_source; the boundary authenticates that later source
    independently and passes it explicitly, preserving the assignment snapshot.
    """
    _text(contract_token, "current contract token")
    _text(validated_source, "current validated source")
    if type(report) is not dict or type(binding) is not dict:
        raise ValueError("binding validation needs a normalized report and immutable binding")
    for key, expected in (
        ("plan_hash", plan_hash),
        ("slice_id", slice_id),
        ("task_id", task_id),
        ("attempt", attempt),
        ("contract_token", contract_token),
        ("assignment_source", binding.get("assignment_source")),
    ):
        if expected is None or (key == "attempt" and (type(expected) is not int or expected < 1)):
            raise ValueError(f"binding has no valid {key} identity")
        if key != "attempt":
            _text(expected, f"binding {key}")
        if binding.get(key) != expected:
            raise ValueError(f"report binds to {key}={binding.get(key)!r}, not the dispatched {expected!r}")
        if key not in report or type(report[key]) is not type(expected) or report[key] != expected:
            raise ValueError(f"normalized report {key} does not match the immutable attempt binding")
    if binding.get("validated_source") not in (None, validated_source):
        raise ValueError("report proves a different source than the bound attempt validated")
    if report.get("validated_source") != validated_source:
        raise ValueError("normalized report proves a different or missing validated source")
    return binding


def check_identity(check):
    """Identity of executable obligation content, excluding historical metadata."""
    parsed = validate_check(check)
    parsed["criterion_ids"] = sorted(set(parsed["criterion_ids"]))
    return util.digest(parsed)


def normalize_receipt(receipt, check, *, contract_token, source_revision, authenticated=False):
    """Validate successful runner proof after the boundary authenticates evidence.

    The caller verifies runner provenance, attempt binding, current-checkpoint execution and
    source/evidence hashes before setting authenticated=True. The receipt must
    contain status=PASS, exit_code=0, the exact check_hash, contract_token,
    source_revision and nonempty evidence_hashes. This pure normalization does
    not read files or authenticate a model's self-reported dictionary.
    """
    if authenticated is not True:
        raise ValueError("receipt needs authenticated runner evidence")
    _text(contract_token, "receipt current contract token")
    _text(source_revision, "receipt current source revision")
    if (
        type(receipt) is not dict
        or receipt.get("status") != "PASS"
        or type(receipt.get("exit_code")) is not int
        or receipt["exit_code"] != 0
    ):
        raise ValueError("receipt must record successful PASS execution")
    for key, expected in (
        ("check_hash", check_identity(check)),
        ("contract_token", contract_token),
        ("source_revision", source_revision),
    ):
        if receipt.get(key) != expected:
            raise ValueError(f"receipt {key} is missing or stale")
    evidence = receipt.get("evidence_hashes")
    if type(evidence) is not dict or not evidence:
        raise ValueError("receipt must retain nonempty authenticated evidence hashes")
    for path, digest in evidence.items():
        _text(path, "receipt evidence path")
        _text(digest, "receipt evidence hash")
    return {key: receipt[key] for key in ("status", "exit_code", "check_hash", "contract_token", "source_revision")} | {
        "evidence_hashes": dict(evidence)
    }


def criterion_proof(obligations, results, *, contract_token=None, source_revision=None, receipts_authenticated=False):
    """Which product criteria current proof fully verifies.

    ``obligations`` are cumulative check rows and ``results`` maps check IDs to
    a normalized successful runner receipt or None. Authentication is supplied
    by the application boundary, never inferred from receipt fields. A criterion
    needs at least one reviewed ``fully_verify`` obligation and current successful
    proof for every required check naming it, including retained ``contributes_to``
    demonstrations. Contribution-only criteria explicitly remain unproven; an
    unrelated criterion's failed checks do not invalidate this criterion's proof.
    """
    proven = {}
    eligible = set()
    for check in obligations:
        receipt = results.get(check["id"])
        try:
            normalize_receipt(
                receipt,
                check,
                contract_token=contract_token,
                source_revision=source_revision,
                authenticated=receipts_authenticated,
            )
            valid = True
        except (ValueError, TypeError, KeyError):
            valid = False
        for criterion in check.get("criterion_ids") or ():
            proven.setdefault(criterion, []).append(valid)
            if check.get("relation") == "fully_verify":
                eligible.add(criterion)
    return {criterion: criterion in eligible and all(rows) for criterion, rows in proven.items()}
