"""Pure, bounded policy for resolving approved-contract blockers."""
from __future__ import annotations

from dataclasses import dataclass, field
from hashlib import sha256
import json
import re
from types import MappingProxyType
from typing import Any, Callable, Mapping


ACTIONS = frozenset({"continue", "retry", "replan", "escalate"})
ALLOWED_FIELDS = frozenset({"end_to_end_flow", "technical_approach", "milestones"})
HUMAN_ONLY_KINDS = frozenset({"permission", "external_permission", "destructive", "security", "access", "protected_data"})
SAFE_KINDS = frozenset({"implementation", "validation", "milestone", "tooling", "plan_detail", "model_output"})
RISK_FLAGS = frozenset({"permission", "external-system", "destructive", "security-sensitive", "access", "protected-data"})

_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_REQUIRED = frozenset({"intended_outcome", "intended_user", "deliverables", "required_behaviors", "important_failure_cases", "scope_exclusions", "constraints", "permission_boundaries", "accepted_assumptions", "delegated_decisions", "acceptance_criteria", "open_blocking_questions", "end_to_end_flow", "technical_approach", "milestones"})
_KNOWN = _REQUIRED | {"initial_task"}
_FORBIDDEN_PAYLOAD_KEYS = frozenset({"body", "contract", "contract_body", "contract_delta", "delta", "lineage", "prior_lineage", "new_lineage", "next_task", "contract_revision", "revision", "hash", "task_id", "approval_status", "approval_event", "role_output"})


@dataclass(frozen=True)
class Blocker:
    id: str
    kind: str
    description: str
    evidence_refs: tuple[str, ...] = ()
    risk_flags: tuple[str, ...] = ()


@dataclass(frozen=True)
class ContractSnapshot:
    task_id: str
    revision: int
    hash: str
    body: Mapping[str, Any]
    approval_status: str
    approval_event: Mapping[str, Any] | None


@dataclass(frozen=True)
class Boundaries:
    allowed_actions: frozenset[str]
    allowed_fields: frozenset[str]


@dataclass(frozen=True)
class Proposal:
    action: str
    payload: Mapping[str, Any]
    rationale: str


@dataclass(frozen=True)
class Review:
    approved: bool
    rationale: str


@dataclass(frozen=True)
class Decision:
    action: str
    payload: Mapping[str, Any]
    rationale: str
    in_scope_reason: str


@dataclass(frozen=True)
class Receipt:
    blocker_id: str
    blocker_digest: str
    classifier_outcome: str
    budget_key: str | None
    idempotency_key: str | None
    attempt: int | None
    action: str
    rationale: str
    in_scope_reason: str
    proposal_rationale: str | None
    review_verdict: str | None
    reviewer_rationale: str | None
    callbacks_used: bool
    prior_lineage: Mapping[str, Any] | None
    new_lineage: Mapping[str, Any] | None
    decided_at: Any


@dataclass
class Ledger:
    attempts: dict[str, int] = field(default_factory=dict)
    outcomes: dict[str, str] = field(default_factory=dict)
    cache: dict[str, tuple[Decision, Receipt]] = field(default_factory=dict)


ProposalCallback = Callable[["ResolverRequest"], Proposal]
ReviewCallback = Callable[["ResolverRequest", Proposal], Review]


@dataclass(frozen=True)
class ResolverRequest:
    blocker: Blocker
    contract: ContractSnapshot
    context: Any
    evidence: Any
    boundaries: Boundaries
    ledger: Ledger
    propose: ProposalCallback | None = None
    review: ReviewCallback | None = None
    proposed_resolution: Proposal | None = None
    clock: Callable[[], Any] | None = None


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def _digest(value: Any) -> str:
    return sha256(_canonical(value).encode("utf-8")).hexdigest()


def sealed_hash(task_id: str, revision: int, body: Mapping[str, Any]) -> str:
    return _digest({"task_id": task_id, "revision": revision, "body": body})


def _lineage(contract: ContractSnapshot) -> dict[str, Any]:
    return {"task_id": contract.task_id, "revision": contract.revision, "hash": contract.hash}


def _as_json(value: Any) -> bool:
    try:
        _canonical(value)
    except (TypeError, ValueError):
        return False
    return True


def _strings(value: Any, *, nonempty: bool = False) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and (item.strip() if nonempty else True) for item in value)


def _string_collection(value: Any) -> bool:
    return isinstance(value, (tuple, list)) and all(isinstance(item, str) for item in value)


def _freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_freeze(item) for item in value)
    return value


def _validate_body(body: Any) -> bool:
    if not isinstance(body, Mapping) or set(body) not in (_REQUIRED, _KNOWN):
        return False
    if not all(isinstance(body.get(key), str) and body[key].strip() for key in ("intended_outcome", "intended_user")):
        return False
    for key in ("deliverables", "required_behaviors", "important_failure_cases", "scope_exclusions", "constraints", "permission_boundaries", "end_to_end_flow", "technical_approach"):
        if not _strings(body.get(key), nonempty=True):
            return False
    if any(not body[key] for key in ("deliverables", "required_behaviors", "permission_boundaries", "end_to_end_flow", "technical_approach")) or not isinstance(body.get("milestones"), list) or not body["milestones"]:
        return False
    for key in ("accepted_assumptions", "delegated_decisions", "open_blocking_questions", "acceptance_criteria"):
        if not isinstance(body.get(key), list):
            return False
    if not all(isinstance(row, Mapping) and set(row) == {"text", "basis", "answer_id"} and all(isinstance(row.get(key), str) for key in ("text", "basis", "answer_id")) and row["basis"] in {"original_request", "user_answer", "user_feedback", "agent_proposed", "delegated"} for row in body["accepted_assumptions"] + body["delegated_decisions"]):
        return False
    if any(row["basis"] != "delegated" for row in body["delegated_decisions"]):
        return False
    if not _rows(body["open_blocking_questions"], {"id", "question", "why", "options", "proposed_default"}):
        return False
    if not all(_strings(row["options"]) and all(isinstance(row[k], str) and row[k].strip() for k in ("id", "question", "why", "proposed_default")) for row in body["open_blocking_questions"]):
        return False
    if not _rows(body["acceptance_criteria"], {"id", "criterion", "verification_method", "human_review"}):
        return False
    criteria = {row["id"] for row in body["acceptance_criteria"]}
    if not criteria:
        return False
    if not all(isinstance(row["criterion"], str) and row["criterion"].strip() and isinstance(row["verification_method"], str) and row["verification_method"].strip() and isinstance(row["human_review"], bool) for row in body["acceptance_criteria"]):
        return False
    if not _rows(body["milestones"], {"id", "objective", "acceptance_criteria"}, optional={"depends_on", "affected_paths"}):
        return False
    milestone_ids = {row["id"] for row in body["milestones"]}
    covered: set[str] = set()
    dependencies_present = ["depends_on" in row for row in body["milestones"]]
    if any(dependencies_present) and not all(dependencies_present):
        return False
    graph: dict[str, list[str]] = {}
    for row in body["milestones"]:
        if "affected_paths" in row and not _strings(row["affected_paths"], nonempty=True):
            return False
        refs = row["acceptance_criteria"]
        if not isinstance(row["objective"], str) or not row["objective"].strip() or not _strings(refs, nonempty=True) or not refs or len(refs) != len(set(refs)) or not set(refs) <= criteria:
            return False
        covered.update(refs)
        if "depends_on" in row:
            deps = row["depends_on"]
            if not _strings(deps, nonempty=True) or len(deps) != len(set(deps)) or not set(deps) <= milestone_ids:
                return False
            graph[row["id"]] = deps
    if covered != criteria or not _acyclic(graph):
        return False
    if "initial_task" in body:
        initial = body["initial_task"]
        required = {"objective", "affected_paths", "kind", "milestone_id", "requirements", "acceptance_criteria", "validation_plan"}
        if not isinstance(initial, Mapping) or set(initial) != required:
            return False
        milestone = next((row for row in body["milestones"] if row["id"] == initial["milestone_id"]), None)
        task_criteria = initial["acceptance_criteria"]
        if (not isinstance(initial["objective"], str) or not initial["objective"].strip()
                or not isinstance(initial["kind"], str) or initial["kind"] not in {"implement", "validate"}
                or not isinstance(initial["milestone_id"], str) or milestone is None
                or not _strings(initial["affected_paths"], nonempty=True)
                or not _strings(initial["requirements"], nonempty=True) or not initial["requirements"]
                or not _strings(task_criteria, nonempty=True) or not task_criteria
                or len(task_criteria) != len(set(task_criteria))
                or not set(task_criteria) <= set(milestone["acceptance_criteria"])
                or not _strings(initial["validation_plan"], nonempty=True) or not initial["validation_plan"]
                or graph.get(initial["milestone_id"])):
            return False
    return _as_json(body)


def _rows(rows: Any, required: set[str], optional: set[str] = frozenset()) -> bool:
    if not isinstance(rows, list):
        return False
    ids: list[str] = []
    for row in rows:
        if not isinstance(row, Mapping) or set(row) - optional != required or not isinstance(row.get("id"), str) or not _ID.fullmatch(row["id"]):
            return False
        ids.append(row["id"])
    return len(ids) == len(set(ids))


def _acyclic(graph: Mapping[str, list[str]]) -> bool:
    visiting: set[str] = set()
    visited: set[str] = set()
    def visit(node: str) -> bool:
        if node in visiting:
            return False
        if node in visited:
            return True
        visiting.add(node)
        if not all(visit(dependency) for dependency in graph[node]):
            return False
        visiting.remove(node)
        visited.add(node)
        return True
    return all(visit(node) for node in graph)


def _valid_request(request: Any) -> tuple[bool, str]:
    if not isinstance(request, ResolverRequest) or not isinstance(request.blocker, Blocker) or not isinstance(request.contract, ContractSnapshot) or not isinstance(request.boundaries, Boundaries) or not isinstance(request.ledger, Ledger):
        return False, "malformed request"
    if request.clock is not None and not callable(request.clock):
        return False, "malformed clock"
    ledger = request.ledger
    if (not isinstance(ledger.attempts, dict) or not isinstance(ledger.outcomes, dict)
            or not isinstance(ledger.cache, dict)
            or any(not isinstance(key, str) or type(count) is not int or count < 0
                   for key, count in ledger.attempts.items())
            or any(not isinstance(key, str) or not isinstance(value, str)
                   for key, value in ledger.outcomes.items())
            or any(not isinstance(key, str) or not isinstance(value, tuple) or len(value) != 2
                   or not isinstance(value[0], Decision) or not isinstance(value[1], Receipt)
                   for key, value in ledger.cache.items())):
        return False, "malformed ledger"
    if not isinstance(request.boundaries.allowed_actions, frozenset) or not isinstance(request.boundaries.allowed_fields, frozenset):
        return False, "invalid boundaries"
    callback_mode = callable(request.propose) and callable(request.review) and request.proposed_resolution is None
    policy_mode = request.propose is None and request.review is None and isinstance(request.proposed_resolution, Proposal)
    if not (callback_mode or policy_mode):
        return False, "exactly one evaluation mode is required"
    blocker = request.blocker
    if not isinstance(blocker.id, str) or not blocker.id.strip() or not isinstance(blocker.kind, str) or not isinstance(blocker.description, str) or not blocker.description.strip() or not _string_collection(blocker.evidence_refs) or not _string_collection(blocker.risk_flags):
        return False, "malformed blocker"
    if not isinstance(request.contract.task_id, str) or not request.contract.task_id or not isinstance(request.contract.revision, int) or isinstance(request.contract.revision, bool) or request.contract.revision < 1 or not isinstance(request.contract.hash, str) or not request.contract.hash:
        return False, "malformed contract lineage"
    if not _validate_body(request.contract.body) or not _as_json(request.context) or not _as_json(request.evidence):
        return False, "invalid contract or declarative input"
    if not all(isinstance(action, str) for action in request.boundaries.allowed_actions | request.boundaries.allowed_fields) or request.boundaries.allowed_actions - ACTIONS or request.boundaries.allowed_fields - ALLOWED_FIELDS:
        return False, "invalid boundaries"
    if request.contract.hash != sealed_hash(request.contract.task_id, request.contract.revision, request.contract.body):
        return False, "unsealed contract"
    event = request.contract.approval_event
    if request.contract.approval_status != "approved" or not isinstance(event, Mapping) or not _as_json(event) or event.get("kind") != "goal_approval" or event.get("actor") != "user_cli" or event.get("token") != f"r{request.contract.revision}:{request.contract.hash}" or request.contract.body["open_blocking_questions"]:
        return False, "unapproved contract"
    return True, "valid"


def _classification(blocker: Blocker) -> tuple[bool, str]:
    if not blocker.kind or blocker.kind not in SAFE_KINDS | HUMAN_ONLY_KINDS:
        return False, "unknown blocker kind"
    if blocker.kind in HUMAN_ONLY_KINDS:
        return False, "human-only blocker kind"
    flags = set(blocker.risk_flags)
    if flags - RISK_FLAGS:
        return False, "unknown risk flag"
    if flags:
        return False, "risk-flagged blocker"
    return True, "safe blocker"


def _declarative(request: ResolverRequest) -> dict[str, Any]:
    return {"blocker": request.blocker.__dict__, "contract": {"task_id": request.contract.task_id, "revision": request.contract.revision, "hash": request.contract.hash, "body": request.contract.body, "approval_status": request.contract.approval_status, "approval_event": request.contract.approval_event}, "context": request.context, "evidence": request.evidence, "boundaries": {"allowed_actions": sorted(request.boundaries.allowed_actions), "allowed_fields": sorted(request.boundaries.allowed_fields)}, "proposed_resolution": None if request.proposed_resolution is None else request.proposed_resolution.__dict__}


def _has_forbidden(value: Any) -> bool:
    if isinstance(value, Mapping):
        return any(key in _FORBIDDEN_PAYLOAD_KEYS or _has_forbidden(child) for key, child in value.items())
    if isinstance(value, (list, tuple)):
        return any(_has_forbidden(child) for child in value)
    return False


def _decision_time(clock: Callable[[], Any] | None) -> Any:
    if clock is None:
        return None
    try:
        value = clock()
    except Exception:
        return None
    return _freeze(value) if _as_json(value) else None


def _new_decision(request: ResolverRequest, action: str, payload: Mapping[str, Any], rationale: str, in_scope_reason: str, classifier_outcome: str, *, attempt: int | None, idem: str | None, budget: str | None, proposal: Proposal | None = None, review: Review | None = None, callbacks: bool = False, new_lineage: Mapping[str, Any] | None = None) -> tuple[Decision, Receipt]:
    decided_at = _decision_time(request.clock)
    decision = Decision(action, _freeze(payload), rationale, in_scope_reason)
    receipt = Receipt(request.blocker.id, _digest(request.blocker.__dict__), classifier_outcome, budget, idem, attempt, action, rationale, in_scope_reason, proposal.rationale if proposal else None, "approved" if review and review.approved else "vetoed" if review else None, review.rationale if review else None, callbacks, _freeze(_lineage(request.contract)), _freeze(new_lineage) if new_lineage else None, decided_at)
    return decision, receipt


def _fallback(request: Any, reason: str, *, classifier_outcome: str | None = None, attempt: int | None = None, idem: str | None = None, budget: str | None = None) -> tuple[Decision, Receipt]:
    blocker = getattr(request, "blocker", None)
    blocker_id = blocker.id if isinstance(blocker, Blocker) and isinstance(blocker.id, str) else ""
    contract = getattr(request, "contract", None)
    lineage = _freeze(_lineage(contract)) if isinstance(contract, ContractSnapshot) and isinstance(contract.task_id, str) and isinstance(contract.revision, int) and isinstance(contract.hash, str) else None
    decision = Decision("escalate", _freeze({"reason": reason}), reason, reason)
    receipt = Receipt(blocker_id, _digest({"blocker_id": blocker_id}), classifier_outcome or reason, budget, idem, attempt, "escalate", reason, reason, None, None, None, False, lineage, None, None)
    return decision, receipt


def _valid_review(value: Any) -> bool:
    return isinstance(value, Review) and type(value.approved) is bool and isinstance(value.rationale, str) and bool(value.rationale.strip())


def _consumed_failure(request: ResolverRequest, reason: str, classifier_outcome: str, *, attempt: int, idempotency_key: str, budget_key: str, proposal: Proposal | None, callbacks: bool) -> tuple[Decision, Receipt]:
    request.ledger.outcomes[budget_key] = reason
    action = "retry" if attempt < 2 else "escalate"
    pair = _new_decision(request, action, {"reason": reason}, reason, reason, classifier_outcome, attempt=attempt, idem=idempotency_key, budget=budget_key, proposal=proposal, callbacks=callbacks)
    request.ledger.cache[idempotency_key] = pair
    return pair


def _validate_proposal(request: ResolverRequest, proposal: Any) -> tuple[bool, str, Mapping[str, Any] | None]:
    if not isinstance(proposal, Proposal) or proposal.action not in ACTIONS or not isinstance(proposal.payload, Mapping) or not isinstance(proposal.rationale, str) or not proposal.rationale.strip():
        return False, "malformed proposal", None
    if proposal.action not in request.boundaries.allowed_actions:
        return False, "action outside boundaries", None
    if not _as_json(proposal.payload):
        return False, "non-serializable payload", None
    if proposal.action != "replan":
        if _has_forbidden(proposal.payload):
            return False, "contract-bearing non-replan payload", None
        allowed = {"guidance", "reason", "evidence_refs"}
        if set(proposal.payload) - allowed or not proposal.payload or any(not isinstance(value, str) or not value.strip() for key, value in proposal.payload.items() if key in {"guidance", "reason"}) or ("evidence_refs" in proposal.payload and not _strings(proposal.payload["evidence_refs"])):
            return False, "invalid non-replan payload", None
        return True, "proposal within boundaries", None
    if set(proposal.payload) != {"body"} or not isinstance(proposal.payload["body"], Mapping):
        return False, "replan needs complete body", None
    candidate = proposal.payload["body"]
    baseline = request.contract.body
    if set(candidate) != set(baseline) or not _validate_body(candidate):
        return False, "invalid candidate body", None
    changed = {key for key in baseline if baseline[key] != candidate[key]}
    if not changed or not changed <= (ALLOWED_FIELDS & request.boundaries.allowed_fields):
        return False, "protected contract change", None
    revision = request.contract.revision + 1
    lineage = {"task_id": request.contract.task_id, "revision": revision, "hash": sealed_hash(request.contract.task_id, revision, candidate)}
    return True, "allowed-field replan", lineage


def resolve(request: ResolverRequest) -> tuple[Decision, Receipt]:
    """Resolve one request, mutating only its caller-owned ledger."""
    valid, reason = _valid_request(request)
    if not valid:
        return _fallback(request, reason)
    eligible, classifier_outcome = _classification(request.blocker)
    if not eligible:
        return _fallback(request, classifier_outcome, classifier_outcome=classifier_outcome)
    try:
        idempotency_key = _digest(_declarative(request))
    except (TypeError, ValueError):
        return _fallback(request, "non-serializable request", classifier_outcome=classifier_outcome)
    if idempotency_key in request.ledger.cache:
        return request.ledger.cache[idempotency_key]
    budget_key = _digest({"blocker_id": request.blocker.id, **_lineage(request.contract)})
    attempt = request.ledger.attempts.get(budget_key, 0)
    if attempt >= 2:
        return _fallback(request, "attempt budget exhausted", classifier_outcome=classifier_outcome, idem=idempotency_key, budget=budget_key)
    attempt += 1
    request.ledger.attempts[budget_key] = attempt
    callbacks = request.propose is not None
    proposal: Proposal | None = None
    review: Review | None = None
    try:
        proposal = request.propose(request) if callbacks else request.proposed_resolution
        if callbacks:
            review = request.review(request, proposal)  # type: ignore[arg-type]
    except Exception:
        return _consumed_failure(request, "callback failure", classifier_outcome, attempt=attempt, idempotency_key=idempotency_key, budget_key=budget_key, proposal=proposal if isinstance(proposal, Proposal) else None, callbacks=callbacks)
    if callbacks and not _valid_review(review):
        return _consumed_failure(request, "malformed review", classifier_outcome, attempt=attempt, idempotency_key=idempotency_key, budget_key=budget_key, proposal=proposal if isinstance(proposal, Proposal) else None, callbacks=True)
    if review is not None and not review.approved:
        request.ledger.outcomes[budget_key] = "review veto"
        action = "retry" if attempt < 2 else "escalate"
        pair = _new_decision(request, action, {"reason": "review veto"}, "review veto", "review veto", classifier_outcome, attempt=attempt, idem=idempotency_key, budget=budget_key, proposal=proposal if isinstance(proposal, Proposal) else None, review=review, callbacks=callbacks)
        request.ledger.cache[idempotency_key] = pair
        return pair
    accepted, reason, new_lineage = _validate_proposal(request, proposal)
    if not accepted:
        return _consumed_failure(request, reason, classifier_outcome, attempt=attempt, idempotency_key=idempotency_key, budget_key=budget_key, proposal=proposal if isinstance(proposal, Proposal) else None, callbacks=callbacks)
    request.ledger.outcomes[budget_key] = proposal.action  # type: ignore[union-attr]
    pair = _new_decision(request, proposal.action, proposal.payload, proposal.rationale, reason, classifier_outcome, attempt=attempt, idem=idempotency_key, budget=budget_key, proposal=proposal, review=review, callbacks=callbacks, new_lineage=new_lineage)
    request.ledger.cache[idempotency_key] = pair
    return pair
