"""Pure saved-fact policy for exhausted internal recovery; never retry authority."""

from __future__ import annotations

import copy
from bisect import bisect_left
from hashlib import sha256
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_contract_identity as contract_identity
    from . import autocode_operational_outcome as owner_outcome
    from . import autocode_operational_receipt_identity as receipt_identity
    from . import autocode_pause_authority as pause_authority
    from . import autocode_permission_recovery as permission_recovery
    from . import autocode_preflight_stop as preflight_stop
    from . import autocode_provider_auth as provider_auth
    from . import autocode_quota_route as quota_route
    from . import autocode_roles as roles
    from . import autocode_stop as stop
    from . import autocode_util as util
    from .autocode_goals import RESOLVER_PROPOSAL_KEY
except ImportError:
    import autocode_contract_identity as contract_identity
    import autocode_operational_outcome as owner_outcome
    import autocode_operational_receipt_identity as receipt_identity
    import autocode_pause_authority as pause_authority
    import autocode_permission_recovery as permission_recovery
    import autocode_preflight_stop as preflight_stop
    import autocode_provider_auth as provider_auth
    import autocode_quota_route as quota_route
    import autocode_roles as roles
    import autocode_stop as stop
    import autocode_util as util
    from autocode_goals import RESOLVER_PROPOSAL_KEY

SCOPE = "operational_exhaustion"
MESSAGE = "AutoCode could not safely continue its internal recovery. Saved work, attempts and evidence are kept."
MANAGED = frozenset({"runner_default", "resolver_delegated"})
NEXT_STEP = (
    "AutoCode cannot continue this chat from here. To try the task again, start a new chat with the same "
    "request; this chat keeps its saved work and history."
)
# What stopped, in a person's words, read from the saved pause status (never a provider's message).
CAUSES = (
    (("PAUSED_PROVIDER_UNCERTAIN",), "its model provider ended the call without a confirmed result"),
    (("PAUSED_UNCERTAIN_STAGE",), "it ended without a confirmed result"),
    (("PAUSED_RATE_LIMIT",), "its model provider kept refusing requests (a rate limit)"),
    # A spent account found by a stage, not by the access probe (whose stop names the probe, below).
    (
        ("PAUSED_BUDGET",),
        "its model provider reported the account credit, quota, usage limit or spending limit used up",
    ),
    (("PAUSED_PROVIDER_CAPACITY",), "its model provider had no capacity for the request"),
    (("PAUSED_REPEATED_FAILURE",), "it failed the same way each time"),
    (("TIMEOUT", "TIME_LIMIT"), "it ran past its time limit"),
    (("NO_PROGRESS",), "it stopped making progress"),
    (("INVALID_OUTPUT", "REPORT_REPAIR", "METADATA"), "its report could not be accepted, even after repair"),
    (("VERIFICATION",), "its checks could not be prepared"),
)


def cause(stage, status, rationale=None, state: RunState | None = None) -> str:
    """One sentence naming the job that stopped and why, for an exhausted internal recovery.

    A stop the model access probe found before the stage launched names the probe, its model and
    the fixed cause of its saved status (autocode_preflight_stop), read from the saved probe record.
    A refused account is named with the core's fixed diagnostic, since only the person can fix it.
    Otherwise the job comes from the saved stage and the reason from the saved pause status.
    """
    probe = preflight_stop.sentence(state, status)
    if probe:
        return probe
    account = provider_auth.account_cause(rationale)
    if account:
        return account
    said: str | None = None
    if status == "PAUSED_PROVIDER_UNCERTAIN" and provider_auth.helper_timed_out(rationale):
        said = "signing in to its model provider timed out"
    elif status == "PAUSED_REPEATED_FAILURE":
        said = permission_recovery.exhausted_cause(rationale)
    if said is None:
        said = next((text for keys, text in CAUSES if any(key in str(status or "") for key in keys)), None)
    running = str(((state or {}).get("active_stage") or {}).get("stage") or "")
    if said is None and roles.base_stage(running) != running:
        # A wrapper status (the Resolver's own stop) names no cause; the attempt it stopped on does.
        said = "its report could not be accepted, even after repair"
    said = said or "it could not finish"
    job = roles.screen_name(stage, state) if isinstance(stage, str) and stage else ""
    return f"{'The ' + job + ' step' if job else 'A step'} stopped: {said}."


def blocked_message(stage, status, rationale=None, state: RunState | None = None) -> str:
    """The run's stop reason when internal recovery is exhausted: what stopped, then that work is kept."""
    return f"{cause(stage, status, rationale, state)} {MESSAGE}"


def budget(state: RunState, status):
    """The same saved bound/origin description for new and retained decisions."""
    kind = (
        "planning_review_call_limit"
        if status == "PAUSED_PLANNING_BUDGET"
        else pause_authority.PAUSE_BUDGET_KIND.get(status)
    )
    settings = state.get("settings") or {}
    origin = (settings.get("budget_origins") or {}).get(kind, "unknown")
    limit = (settings.get("limits") or {}).get(kind)
    if kind == "planning_review_call_limit":
        planning = state.get("planning") or {}
        origin = planning.get(
            "review_call_limit_origin", "runner_default" if "review_call_limit" not in planning else "unknown"
        )
        limit = planning.get("review_call_limit", 2)
    elif kind == "milestone_max_seconds":
        limit = (settings.get("milestone_checkpoints") or {}).get("max_seconds")
    category = (
        (
            "no_progress"
            if status == "PAUSED_NO_PROGRESS"
            else "internal_default"
            if origin in MANAGED
            else "explicit_user_cap"
            if origin == "user_explicit"
            else "protected_saved_limit"
        )
        if kind
        else "provider_or_spending_guard"
        if status in ("PAUSED_BUDGET", "PAUSED_RATE_LIMIT")
        else "provider_content_filter"
        if status == quota_route.REFUSAL_STATUS
        else "operational_recovery"
    )
    return {"category": category, "kind": kind, "origin": origin, "limit": limit}


def classify(proposal):
    """One disposition for structured operational facts, without interpreting prose."""
    if not isinstance(proposal, dict) or proposal.get("scope") != SCOPE:
        return None
    questions = proposal.get("questions", [])
    if not isinstance(questions, list) or any(not isinstance(question, dict) for question in questions):
        return "invalid"
    origin = proposal.get("origin") or {}
    status = origin.get("pause_status")
    if status in ("PAUSED_COST_LIMIT", "PAUSED_PLANNING_CALL_LIMIT"):
        return "limit"
    bound = origin.get("budget") or {}
    kind = (
        "planning_review_call_limit"
        if status == "PAUSED_PLANNING_BUDGET"
        else pause_authority.PAUSE_BUDGET_KIND.get(status)
    )
    if kind is None and isinstance(bound, dict):
        kind = bound.get("kind")
    if kind and (not isinstance(bound, dict) or bound.get("kind") != kind or bound.get("origin") not in MANAGED):
        return "limit"
    if any(
        quota_route.asked_route(proposal.get("questions"), question.get("id"))
        for question in proposal.get("questions") or []
        if isinstance(question, dict)
    ):
        return "model"
    return "machinery"


def diagnostic(state: RunState, identity):
    """Authenticate the runner's original diagnostic, without current source I/O."""
    if not isinstance(identity, str) or not identity:
        return None
    resolver = state.get("resolver") or {}
    if not isinstance(resolver, dict):
        return None
    records = resolver.get("operational_receipts") or {}
    if not isinstance(records, dict):
        return None
    row = records.get(identity)
    if not isinstance(row, dict) or identity not in (resolver.get("operational_diagnostics") or []):
        return None
    if receipt_identity.scope(row, identity) != receipt_identity.DIAGNOSTIC:
        return None
    return row


def incident_identity(state: RunState, row, *, _ledger=None):
    """A normalized witness names the exact original diagnostic, never a new incident."""
    detail = row.get("receipt") or {}
    evidence = detail.get("evidence") or {}
    original = evidence.get("original_receipt_id")
    if original is None:
        return detail.get("id")
    source = _ledger.diagnostic(original) if _ledger else diagnostic(state, original)
    if source is None or (source.get("decision") or {}).get("action") != "hold":
        return None
    facts = source["receipt"].get("evidence") or {}
    source_binding = source["receipt"].get("evaluation_binding") or {}
    binding = detail.get("evaluation_binding") or {}
    if any(binding.get(key) != source_binding.get(key) for key in ("task_id", "workspace", "run_dir", "current_task")):
        return None
    if any(evidence.get(key) != facts.get(key) for key in ("status", "stage", "budget")):
        return None
    return original


def saved_disposition(state: RunState, proposal, *, _ledger=None):
    """Interpret a request only against its exact runner-held diagnostic facts."""
    if not isinstance(proposal, dict) or proposal.get("scope") != SCOPE:
        return None
    references, origin = proposal.get("evidence"), proposal.get("origin")
    if not isinstance(references, dict) or not isinstance(origin, dict):
        return "invalid"
    identity = references.get("resolver_receipt_id")
    row = _ledger.diagnostic(identity) if _ledger else diagnostic(state, identity)
    if row is None:
        return "invalid"
    evidence = row["receipt"].get("evidence") or {}
    if any(
        origin.get(key) != evidence.get(field)
        for key, field in (("pause_status", "status"), ("stage", "stage"), ("budget", "budget"))
    ):
        return "invalid"
    answer = record_answer(state, row, _ledger=_ledger)
    if proposal.get("questions", []) != answer["questions"]:
        return "invalid"
    return answer["kind"]


def record_disposition(state: RunState, row, *, _ledger=None):
    return record_answer(state, row, _ledger=_ledger)["kind"]


def _publications(state: RunState):
    """Only complete saved resolver identities can supply legacy question bytes."""
    resolver = state.get("resolver")
    entries = resolver.get("human_escalations") if isinstance(resolver, dict) else None
    if not isinstance(entries, dict):
        return
    for key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        identity = entry.get("identity")
        if not isinstance(identity, dict) or not isinstance(identity.get("proposal"), dict):
            continue
        if (
            identity.get("issuer") == "resolver"
            and identity.get("action") == "escalate"
            and util.digest(identity) == key == entry.get("receipt_hash")
        ):
            yield identity["proposal"]


class _Ledger:
    """One read's listed identities and authenticated facts; never saved or global."""

    def __init__(self, state: RunState):
        resolver = state.get("resolver") or {}
        records = resolver.get("operational_receipts") if isinstance(resolver, dict) else None
        identities = resolver.get("operational_diagnostics") if isinstance(resolver, dict) else None
        self.state = state
        self.prefixes = None
        self.feedback = None
        self.event_hashes: dict[int, str] = {}
        self.object_hashes: dict[int, tuple[object, str]] = {}
        self.records = records if isinstance(records, dict) else {}
        self.identities = identities if isinstance(identities, list) else []
        self.listed = {key for key in self.identities if isinstance(key, str)}
        self.owners: dict[str, str | None] = {}
        self.answers: dict[str, dict] = {}
        self.publications: dict[str | None, list[dict]] = {}
        self.proposals = list(_publications(state))
        for proposal in self.proposals:
            identity = _reference(proposal)
            self.publications.setdefault(identity, []).append(proposal)

    def prefix(self, binding):
        expected = binding.get("user_events") if isinstance(binding, dict) else None
        if not isinstance(expected, str):
            return None
        if self.prefixes is None:
            events = self.state.get("user_events")
            if not isinstance(events, list):
                self.prefixes = {}
                return None

            def observe(position: int, encoded: bytes):
                digest = sha256(encoded).hexdigest()
                self.event_hashes[position] = digest
                self.object_hashes[id(events[position])] = (events[position], digest)

            self.prefixes = {
                digest: position for position, digest in util.digest_prefixes(events, observe_value=observe)
            }
        return self.prefixes.get(expected)

    def feedback_after(self, binding, token):
        start = self.prefix(binding)
        if start is None:
            return None
        if self.feedback is None:
            self.feedback = {}
            paired = {}
            rows = self.state.get("brief_feedback")
            for row in rows if isinstance(rows, list) else []:
                if not isinstance(row, dict):
                    continue
                seen = self.object_hashes.get(id(row))
                digest = seen[1] if seen is not None and seen[0] is row else util.digest(row)
                paired.setdefault(digest, []).append(row)
            applied = {}
            receipts = self.state.get("applied_interventions")
            for receipt in receipts if isinstance(receipts, list) else []:
                if not isinstance(receipt, dict):
                    continue
                facts = {key: receipt.get(key) for key in ("id", "kind", "applied_at")}
                applied.setdefault(util.digest(facts), []).append(facts)
            for position, event in enumerate(self.state.get("user_events") or []):
                if not (
                    isinstance(event, dict)
                    and event.get("kind") == "brief_feedback"
                    and event.get("actor") in ("user_cli", "user_intervention")
                    and isinstance(event.get("id"), str)
                    and event["id"]
                    and isinstance(event.get("text"), str)
                    and event["text"].strip()
                    and any(event == row for row in paired.get(self.event_hashes[position], ()))
                ):
                    continue
                if event["actor"] == "user_intervention":
                    facts = {"id": event.get("receipt_id"), "kind": "feedback", "applied_at": event.get("at")}
                    if not any(facts == receipt for receipt in applied.get(util.digest(facts), ())):
                        continue
                contract = event.get("contract_token")
                if isinstance(contract, str):
                    self.feedback.setdefault(contract, []).append((position, event))
        candidates = self.feedback.get(token, [])
        index = bisect_left(candidates, start, key=lambda candidate: candidate[0])
        return candidates[index][1] if index < len(candidates) else None

    def scope(self, identity):
        if not isinstance(identity, str):
            return None
        if identity not in self.owners:
            self.owners[identity] = receipt_identity.scope(self.records.get(identity), identity)
        return self.owners[identity]

    def diagnostic(self, identity):
        if not isinstance(identity, str) or identity not in self.listed:
            return None
        return self.records.get(identity) if self.scope(identity) == receipt_identity.DIAGNOSTIC else None


def record_answer(state: RunState, row, *, _ledger=None):
    """Read the Core-recorded answer, never new hints in a mutable proposal."""
    identity = (row.get("receipt") or {}).get("id")
    if _ledger is not None and isinstance(identity, str) and _ledger.diagnostic(identity) is row:
        if identity not in _ledger.answers:
            _ledger.answers[identity] = _record_answer(state, row, _ledger=_ledger)
        return _ledger.answers[identity]
    return _record_answer(state, row, _ledger=_ledger)


def _record_answer(state: RunState, row, *, _ledger=None):
    """Resolve a whole publication group once, preserving its authentic order."""
    detail = row.get("receipt") or {}
    facts = detail.get("evidence") or {}
    if "disposition" in facts:
        kind, questions = facts["disposition"], facts.get("questions")
        if (
            kind not in ("limit", "model", "machinery")
            or not isinstance(questions, list)
            or any(not isinstance(question, dict) for question in questions)
        ):
            return {"kind": "invalid", "questions": []}
        return {"kind": kind, "questions": copy.deepcopy(questions)}
    origin = {"pause_status": facts.get("status"), "stage": facts.get("stage"), "budget": facts.get("budget")}
    protected = classify({"scope": SCOPE, "origin": origin}) == "limit"
    # Old queued hints were never part of the diagnostic's identity. A model
    # choice survives only in an existing complete, authenticated publication.
    publications = _ledger.publications.get(detail.get("id"), []) if _ledger else _publications(state)
    for proposal in publications:
        offered_origin, references = proposal.get("origin"), proposal.get("evidence")
        if (
            proposal.get("scope") == SCOPE
            and isinstance(offered_origin, dict)
            and isinstance(references, dict)
            and all(offered_origin.get(name) == value for name, value in origin.items())
            and references.get("resolver_receipt_id") == detail.get("id")
        ):
            selected = classify(proposal)
            if selected == "invalid":
                return {"kind": "invalid", "questions": []}
            if protected or selected == "model":
                return {
                    "kind": "limit" if protected else "model",
                    "questions": copy.deepcopy(proposal.get("questions") or []),
                }
    return {"kind": "limit" if protected else "machinery", "questions": []}


def refused(state: RunState, proposal, *, _ledger=None):
    return saved_disposition(state, proposal, _ledger=_ledger) in ("machinery", "invalid")


def _reference(proposal):
    references = proposal.get("evidence")
    identity = references.get("resolver_receipt_id") if isinstance(references, dict) else None
    return identity if isinstance(identity, str) else None


def incidents(state: RunState, *, _ledger=None):
    """Unresolved generic incidents; existing typed domains retain their own reader."""
    resolver = state.get("resolver") or {}
    if not isinstance(resolver, dict):
        return [{"receipt": {"id": None}, "summary": MESSAGE}]
    identities, records = resolver.get("operational_diagnostics") or [], resolver.get("operational_receipts") or {}
    if not isinstance(identities, list) or not isinstance(records, dict):
        return [{"receipt": {"id": None}, "summary": MESSAGE}]
    ledger = _ledger or _Ledger(state)
    retired = set()
    found = []
    for identity in reversed(identities):
        if not isinstance(identity, str):
            found.append({"receipt": {"id": None}, "summary": MESSAGE})
            continue
        owner = ledger.scope(identity)
        if owner is not None and owner != receipt_identity.DIAGNOSTIC:
            continue
        row = ledger.diagnostic(identity)
        if row is None:
            # Missing or altered runner evidence cannot silently discharge an
            # incident. This is denial only; no identity can authorize recovery.
            found.append({"receipt": {"id": None}, "summary": MESSAGE})
            continue
        detail = row["receipt"]
        evidence = detail.get("evidence") or {}
        action = row["decision"].get("action")
        if action == "recovered":
            original = ledger.diagnostic(evidence.get("recovered_receipt_id"))
            facts = (original.get("receipt") or {}).get("evidence") or {} if original else {}
            if original and all(evidence.get(key) == facts.get(key) for key in ("status", "stage", "budget")):
                retired.add(evidence["recovered_receipt_id"])
        elif (
            action == "blocked"
            or action == "hold"
            and record_disposition(state, row, _ledger=ledger) in ("machinery", "invalid")
        ) and incident_identity(state, row, _ledger=ledger) not in retired:
            found.append(row)
    # Authenticate legacy publications (including consumed replies) before reading facts.
    for proposal in reversed(ledger.proposals):
        if not refused(state, proposal, _ledger=ledger):
            continue
        source = _reference(proposal)
        row = ledger.diagnostic(source)
        if source not in retired:
            found.append(row or {"receipt": {"id": None}, "summary": MESSAGE})
    proposal = state.get(RESOLVER_PROPOSAL_KEY)
    if isinstance(proposal, dict) and refused(state, proposal, _ledger=ledger):
        source = _reference(proposal)
        row = ledger.diagnostic(source)
        if source not in retired:
            found.append(row or {"receipt": {"id": None}, "summary": MESSAGE})
    unique: dict[str | None, dict] = {}
    for row in found:
        unique.setdefault(incident_identity(state, row, _ledger=ledger), row)
    return list(unique.values())


def held(state: RunState):
    """The exact unresolved incident; new input never erases its saved evidence."""
    return next(iter(incidents(state)), None)


def original_detail(state: RunState, row, *, _ledger=None):
    identity = incident_identity(state, row, _ledger=_ledger)
    original = _ledger.diagnostic(identity) if _ledger else diagnostic(state, identity)
    return original["receipt"] if original else {}


def same_run(state: RunState, binding):
    return all(binding.get(key) == state.get(key) for key in ("task_id", "task", "workspace", "run_dir"))


def planning_input(state: RunState, row, *, _ledger=None):
    """A committed owner correction to this planning incident, never a flag or answer."""
    detail = original_detail(state, row, _ledger=_ledger)
    evidence, binding = detail.get("evidence") or {}, detail.get("evaluation_binding") or {}
    if not (
        evidence.get("status") in pause_authority.FEEDBACK_ACKNOWLEDGES
        or pause_authority.planning_incident_acknowledges(evidence, binding)
    ) or not same_run(state, binding):
        return None
    contract = binding.get("contract")
    if not (
        isinstance(contract, dict)
        and type(contract.get("revision")) is int
        and isinstance(contract.get("hash"), str)
        and isinstance(contract.get("task_id"), str)
    ):
        return None
    token = contract_identity.token(contract)
    ledger = _ledger or _Ledger(state)
    if ledger.prefix(binding) is None:
        return None
    event = ledger.feedback_after(binding, token)
    if event:
        return {"kind": "brief_feedback", "event_id": event["id"], "contract_token": token}
    current = state.get("goal_contract")
    if (
        isinstance(current, dict)
        and contract_identity.sealed(current)
        and current.get("origin") == "user_cli_edit"
        and current.get("task_id") == contract.get("task_id")
        and current.get("revision") == contract["revision"] + 1
        and any(
            contract_identity.sealed(previous) and all(previous.get(key) == value for key, value in contract.items())
            for previous in state.get("contract_history") or []
        )
    ):
        return {"kind": "goal_revision", "contract_token": token, "revision_token": contract_identity.token(current)}
    return None


def _later_events(state: RunState, binding, *, _ledger=None):
    events = state.get("user_events")
    if not isinstance(events, list):
        return None
    start = (_ledger or _Ledger(state)).prefix(binding)
    return events[start:] if start is not None else None


def later_events(state: RunState, row):
    return _later_events(state, original_detail(state, row).get("evaluation_binding") or {})


def recovery_facts(state: RunState, row, selection, result, *, _ledger=None):
    """Read an exact recorded owner answer, never reimplement its eligibility."""
    return owner_outcome.read(state, result)


_ROUTE_ONLY_KEYS = frozenset(
    {
        "roles",
        "route_origins",
        "effort_origins",
        "effort_fits",
        "stuck_investigation",
        "declared_variants",
        "certified_variants",
    }
)


def _route_only_settings_change(previous: dict, selected: dict) -> bool:
    """True only when ``previous`` and ``selected`` differ exclusively in model/effort route fields."""
    if not isinstance(previous, dict) or not isinstance(selected, dict):
        return False
    if {k: v for k, v in previous.items() if k not in _ROUTE_ONLY_KEYS} != {
        k: v for k, v in selected.items() if k not in _ROUTE_ONLY_KEYS
    }:
        return False
    prev_roles, sel_roles = previous.get("roles") or {}, selected.get("roles") or {}
    if not isinstance(prev_roles, dict) or not isinstance(sel_roles, dict) or prev_roles.keys() != sel_roles.keys():
        return False
    for role, prev_cfg in prev_roles.items():
        sel_cfg = sel_roles[role]
        if not isinstance(prev_cfg, dict) or not isinstance(sel_cfg, dict):
            return False
        changed = {k for k in prev_cfg.keys() | sel_cfg.keys() if prev_cfg.get(k) != sel_cfg.get(k)}
        if not changed <= {"model", "reasoning_effort"}:
            return False
    return True


def _authenticated_recovery_settings(
    state: RunState, expected_digest: str | None, later_events: list | None
) -> dict | None:
    """Return ``state['settings']`` as bound at ``recovery_checked`` when only authenticated route changes followed."""
    if not isinstance(expected_digest, str) or later_events is None:
        return None
    current = state.get("settings", {})
    if not isinstance(current, dict):
        return None
    if util.digest(current) == expected_digest:
        return current
    # Disallow any intervening human event other than explicit CLI route assignments.
    if any(
        isinstance(event, dict)
        and event.get("actor") not in ("runner", "core", "autocode")
        and not (event.get("kind") == quota_route.KIND and event.get("actor") == "user_cli")
        for event in later_events
    ):
        return None
    cursor = copy.deepcopy(current)
    for entry in reversed(state.get("configuration_changes") or []):
        if (
            isinstance(entry, dict)
            and entry.get("selected") == cursor
            and isinstance(entry.get("previous"), dict)
            and _route_only_settings_change(entry["previous"], cursor)
        ):
            cursor = copy.deepcopy(entry["previous"])
            if util.digest(cursor) == expected_digest:
                return cursor
    for candidate in quota_route.unassigned_candidates(state, cursor, events=later_events):
        if _route_only_settings_change(candidate, current) and util.digest(candidate) == expected_digest:
            return candidate
    return None


def recovery_validation_state(state: RunState, row, observed) -> RunState:
    """State view with pre-route-change settings when validating an immutable ``recovery_checked`` proof."""
    receipt_id = (observed or {}).get("receipt_id") if isinstance(observed, dict) else None
    witness = diagnostic(state, receipt_id) if isinstance(receipt_id, str) else None
    if witness is None:
        return state
    checked_binding = (witness.get("receipt") or {}).get("evaluation_binding") or {}
    later = _later_events(state, checked_binding)
    prior_settings = _authenticated_recovery_settings(state, checked_binding.get("settings"), later)
    if prior_settings is None or prior_settings is state.get("settings"):
        return state
    return {**state, "settings": prior_settings}


def checked_recovery(state: RunState, row, *, _ledger=None):
    """A diagnostic is insufficient: its exact handler ledger must still agree."""
    ledger = _ledger or _Ledger(state)
    original = incident_identity(state, row, _ledger=ledger)
    detail = original_detail(state, row, _ledger=ledger)
    facts = detail.get("evidence") or {}
    binding = detail.get("evaluation_binding") or {}
    if not original or not same_run(state, binding):
        return None
    for identity in reversed(ledger.identities):
        witness = ledger.diagnostic(identity)
        if witness is None or witness["decision"].get("action") != "recovery_checked":
            continue
        detail = witness["receipt"]
        evidence = detail.get("evidence") or {}
        if evidence.get("original_receipt_id") != original or any(
            evidence.get(key) != facts.get(key) for key in ("status", "stage", "budget")
        ):
            continue
        checked_binding = detail.get("evaluation_binding") or {}
        if not same_run(state, checked_binding) or any(
            checked_binding.get(key) != binding.get(key)
            for key in ("task_id", "workspace", "run_dir", "current_task", "contract")
        ):
            continue
        later = _later_events(state, checked_binding, _ledger=ledger)
        if checked_binding.get("next_stage") != state.get("next_stage") or later is None:
            continue
        prior_settings = _authenticated_recovery_settings(state, checked_binding.get("settings"), later)
        if prior_settings is None:
            continue
        prior_state = state if prior_settings is state.get("settings") else {**state, "settings": prior_settings}
        current = recovery_facts(
            prior_state,
            row,
            evidence.get("selection"),
            evidence.get("result"),
            _ledger=ledger,
        )
        if current is not None and current == evidence.get("facts"):
            return {
                "receipt_id": identity,
                "selection": evidence["selection"],
                "facts": current,
                "result": evidence["result"],
            }
    return None


def inspectable(state: RunState):
    """Every retained incident has authenticated evidence; this grants no recovery."""
    ledger = _Ledger(state)
    return all(incident_identity(state, row, _ledger=ledger) for row in incidents(state, _ledger=ledger))


def decision(state: RunState):
    """One saved-fact disposition for admission and the public projection."""
    ledger = _Ledger(state)
    rows = incidents(state, _ledger=ledger)
    if not rows:
        return None
    blocked = next((row for row in rows if planning_input(state, row, _ledger=ledger) is None), None)
    row = blocked or rows[0]
    return {
        "kind": "blocked" if blocked is not None else "planning_input",
        "record": row,
        "input": planning_input(state, row, _ledger=ledger),
    }


def presentation(state: RunState):
    ledger = _Ledger(state)
    rows = incidents(state, _ledger=ledger)
    if stop.applied_stop(state) or not rows:
        return None
    same_ctx = lambda row: all(  # noqa: E731
        ((row.get("receipt") or {}).get("evaluation_binding") or {}).get(key) == value
        for key, value in (
            ("task_id", state.get("task_id")),
            ("task", state.get("task")),
            ("workspace", state.get("workspace")),
            ("run_dir", state.get("run_dir")),
            ("current_task", util.digest(state.get("current_task"))),
        )
    )
    abandoned_ready = state.get("status") == "PAUSED_STAGE_ABANDONED"
    unresolved = [
        row
        for row in rows
        if planning_input(state, row, _ledger=ledger) is None
        and not (
            abandoned_ready
            and same_ctx(row)
            and (checked := checked_recovery(state, row, _ledger=ledger)) is not None
            and checked.get("selection") == "abandon"
        )
    ]
    if not unresolved and (abandoned_ready or state.get("status") in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL")):
        # Only existing genuine decision readers (or an explicit PAUSED_STAGE_ABANDONED
        # settlement whose stage abandonment is already verified) may publish their named action.
        # An otherwise idle input or internal-allowance receipt creates no Resume or new composer.
        return None
    # The job and the reason come from the incident's saved facts, else the run's own stage and status.
    # The runner's rationale names a refused account with the core's fixed diagnostic, never the
    # provider's message; the person sees that much, since only they can fix their account.
    record = unresolved[0] if unresolved else rows[0]
    facts = (record.get("receipt") or {}).get("evidence") or {}
    stage = facts.get("stage") or (state.get("active_stage") or {}).get("stage") or state.get("next_stage")
    status = facts.get("status") or state.get("status")
    return {
        "category": "blocked",
        "title": "AutoCode is blocked on internal recovery",
        "what_happened": blocked_message(stage, status, (record.get("decision") or {}).get("rationale"), state),
        "what_it_means": "The task is not complete. " + NEXT_STEP,
    }
