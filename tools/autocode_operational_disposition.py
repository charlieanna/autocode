"""Pure saved-fact policy for exhausted internal recovery; never retry authority."""

from __future__ import annotations

import copy
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_builder_policy as builder_policy
    from . import autocode_contract_identity as contract_identity
    from . import autocode_limit_events as limit_events
    from . import autocode_operational_receipt_identity as receipt_identity
    from . import autocode_pause_authority as pause_authority
    from . import autocode_quota_route as quota_route
    from . import autocode_recovery_accounting as recovery_accounting
    from . import autocode_stop as stop
    from . import autocode_util as util
    from .autocode_goals import RESOLVER_PROPOSAL_KEY
except ImportError:
    import autocode_builder_policy as builder_policy
    import autocode_contract_identity as contract_identity
    import autocode_limit_events as limit_events
    import autocode_operational_receipt_identity as receipt_identity
    import autocode_pause_authority as pause_authority
    import autocode_quota_route as quota_route
    import autocode_recovery_accounting as recovery_accounting
    import autocode_stop as stop
    import autocode_util as util
    from autocode_goals import RESOLVER_PROPOSAL_KEY

SCOPE = "operational_exhaustion"
MESSAGE = "AutoCode could not safely continue its internal recovery. Saved work, attempts and evidence are kept."
MANAGED = frozenset({"runner_default", "resolver_delegated"})


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


def incident_identity(state: RunState, row):
    """A normalized witness names the exact original diagnostic, never a new incident."""
    detail = row.get("receipt") or {}
    evidence = detail.get("evidence") or {}
    original = evidence.get("original_receipt_id")
    if original is None:
        return detail.get("id")
    source = diagnostic(state, original)
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


def saved_disposition(state: RunState, proposal):
    """Interpret a request only against its exact runner-held diagnostic facts."""
    if not isinstance(proposal, dict) or proposal.get("scope") != SCOPE:
        return None
    references, origin = proposal.get("evidence"), proposal.get("origin")
    if not isinstance(references, dict) or not isinstance(origin, dict):
        return "invalid"
    row = diagnostic(state, references.get("resolver_receipt_id"))
    if row is None:
        return "invalid"
    evidence = row["receipt"].get("evidence") or {}
    if any(
        origin.get(key) != evidence.get(field)
        for key, field in (("pause_status", "status"), ("stage", "stage"), ("budget", "budget"))
    ):
        return "invalid"
    answer = record_answer(state, row)
    if proposal.get("questions", []) != answer["questions"]:
        return "invalid"
    return answer["kind"]


def record_disposition(state: RunState, row):
    return record_answer(state, row)["kind"]


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


def record_answer(state: RunState, row):
    """Read the Core-recorded answer, never new hints in a mutable proposal."""
    detail = row.get("receipt") or {}
    facts = detail.get("evidence") or {}
    if "disposition" in facts:
        kind, questions = facts["disposition"], facts.get("questions")
        if kind not in ("limit", "model", "machinery") or not isinstance(questions, list):
            return {"kind": "invalid", "questions": []}
        return {"kind": kind, "questions": copy.deepcopy(questions)}
    origin = {"pause_status": facts.get("status"), "stage": facts.get("stage"), "budget": facts.get("budget")}
    protected = classify({"scope": SCOPE, "origin": origin}) == "limit"
    # Old queued hints were never part of the diagnostic's identity. A model
    # choice survives only in an existing complete, authenticated publication.
    for proposal in _publications(state):
        offered_origin, references = proposal.get("origin"), proposal.get("evidence")
        if (
            proposal.get("scope") == SCOPE
            and isinstance(offered_origin, dict)
            and isinstance(references, dict)
            and all(offered_origin.get(name) == value for name, value in origin.items())
            and references.get("resolver_receipt_id") == detail.get("id")
            and (protected or classify(proposal) == "model")
        ):
            return {
                "kind": "limit" if protected else "model",
                "questions": copy.deepcopy(proposal.get("questions") or []),
            }
    return {"kind": "limit" if protected else "machinery", "questions": []}


def refused(state: RunState, proposal):
    return saved_disposition(state, proposal) in ("machinery", "invalid")


def _reference(proposal):
    references = proposal.get("evidence")
    identity = references.get("resolver_receipt_id") if isinstance(references, dict) else None
    return identity if isinstance(identity, str) else None


def incidents(state: RunState):
    """Unresolved generic incidents; existing typed domains retain their own reader."""
    resolver = state.get("resolver") or {}
    if not isinstance(resolver, dict):
        return [{"receipt": {"id": None}, "summary": MESSAGE}]
    identities, records = resolver.get("operational_diagnostics") or [], resolver.get("operational_receipts") or {}
    if not isinstance(identities, list) or not isinstance(records, dict):
        return [{"receipt": {"id": None}, "summary": MESSAGE}]
    retired = set()
    found = []
    for identity in reversed(identities):
        if not isinstance(identity, str):
            found.append({"receipt": {"id": None}, "summary": MESSAGE})
            continue
        owner = receipt_identity.scope(records.get(identity), identity)
        if owner is not None and owner != receipt_identity.DIAGNOSTIC:
            continue
        row = diagnostic(state, identity)
        if row is None:
            # Missing or altered runner evidence cannot silently discharge an
            # incident. This is denial only; no identity can authorize recovery.
            found.append({"receipt": {"id": None}, "summary": MESSAGE})
            continue
        detail = row["receipt"]
        evidence = detail.get("evidence") or {}
        action = row["decision"].get("action")
        if action == "recovered":
            original = diagnostic(state, evidence.get("recovered_receipt_id"))
            facts = (original.get("receipt") or {}).get("evidence") or {} if original else {}
            if original and all(evidence.get(key) == facts.get(key) for key in ("status", "stage", "budget")):
                retired.add(evidence["recovered_receipt_id"])
        elif (
            action == "blocked" or action == "hold" and record_disposition(state, row) in ("machinery", "invalid")
        ) and incident_identity(state, row) not in retired:
            found.append(row)
    # Authenticate legacy publications (including consumed replies) before reading facts.
    for proposal in reversed(list(_publications(state))):
        if not refused(state, proposal):
            continue
        source = _reference(proposal)
        row = diagnostic(state, source)
        if source not in retired:
            found.append(row or {"receipt": {"id": None}, "summary": MESSAGE})
    proposal = state.get(RESOLVER_PROPOSAL_KEY)
    if isinstance(proposal, dict) and refused(state, proposal):
        source = _reference(proposal)
        row = diagnostic(state, source)
        if source not in retired:
            found.append(row or {"receipt": {"id": None}, "summary": MESSAGE})
    unique: dict[str | None, dict] = {}
    for row in found:
        unique.setdefault(incident_identity(state, row), row)
    return list(unique.values())


def held(state: RunState):
    """The exact unresolved incident; new input never erases its saved evidence."""
    return next(iter(incidents(state)), None)


def original_detail(state: RunState, row):
    original = diagnostic(state, incident_identity(state, row))
    return original["receipt"] if original else {}


def same_run(state: RunState, binding):
    return all(binding.get(key) == state.get(key) for key in ("task_id", "task", "workspace", "run_dir"))


def planning_input(state: RunState, row):
    """A committed owner correction to this planning incident, never a flag or answer."""
    detail = original_detail(state, row)
    evidence, binding = detail.get("evidence") or {}, detail.get("evaluation_binding") or {}
    if evidence.get("status") not in pause_authority.FEEDBACK_ACKNOWLEDGES or not same_run(state, binding):
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
    events = _later_events(state, binding)
    if events is None:
        return None
    for event in events:
        if not (
            isinstance(event, dict)
            and event.get("kind") == "brief_feedback"
            and event.get("actor") in ("user_cli", "user_intervention")
            and isinstance(event.get("id"), str)
            and event.get("id")
            and isinstance(event.get("text"), str)
            and event["text"].strip()
            and event.get("contract_token") == token
            and event in (state.get("brief_feedback") or [])
        ):
            continue
        if event["actor"] == "user_intervention" and not any(
            isinstance(applied, dict)
            and applied.get("id") == event.get("receipt_id")
            and applied.get("kind") == "feedback"
            and applied.get("applied_at") == event.get("at")
            for applied in state.get("applied_interventions") or []
        ):
            continue
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


def _later_events(state: RunState, binding):
    events = state.get("user_events")
    if not isinstance(events, list):
        return None
    start = util.digest_prefix_length(events, binding.get("user_events"))
    return events[start:] if start is not None else None


def recovery_facts(state: RunState, row, selection, result):
    """Revalidate an actual handler result against its existing saved ledger."""
    detail = original_detail(state, row)
    evidence, binding = detail.get("evidence") or {}, detail.get("evaluation_binding") or {}
    later = _later_events(state, binding)
    if later is None:
        return None
    events = [event for event in later if isinstance(event, dict)]
    if selection == "abandon" and isinstance(result, dict):
        original, archived = result.get("original"), result.get("record")
        context = state.get("recovery_context") or {}
        if not (
            isinstance(original, dict)
            and isinstance(archived, dict)
            and binding.get("active_stage") == util.digest(original)
            and not state.get("active_stage")
            and all(archived.get(key) is True for key in ("accounted", "abandoned", "rejected"))
            and archived in (state.get("stages") or [])
            and all(archived.get(key) == original.get(key) for key in ("stage", "role", "iteration"))
            and original.get("stage") == evidence.get("stage")
            and (archived.get("archived_paths") or {}).get(original.get("events")) == archived.get("events")
            and (archived.get("archived_paths") or {}).get(original.get("before_ref")) == archived.get("before_ref")
            and context.get("attempt_id") == result.get("selected")
            and context.get("role") == archived.get("role")
            and context.get("events") == archived.get("events")
            and context.get("source_snapshot") == archived.get("after_ref")
            and context.get("source_revision") == archived.get("source_revision")
            and context.get("changed_files") == archived.get("changed_files")
            and budget(state, evidence.get("status")) == evidence.get("budget")
        ):
            return None
        event = next(
            (
                event
                for event in events
                if event.get("kind") == "stage_abandoned"
                and event.get("actor") == "user_cli"
                and event.get("attempt_id") == result.get("selected")
                and event.get("changed_files") == archived.get("changed_files")
            ),
            None,
        )
        if event is not None:
            return {
                "record": util.digest(archived),
                "event": copy.deepcopy(event),
                "files": copy.deepcopy(result.get("files")),
                "accounting": {
                    key: copy.deepcopy(state.get(key))
                    for key in (
                        "active_seconds",
                        "milestone_active_seconds",
                        "automatic_recoveries_since_resume",
                        "consecutive_timeout_recoveries",
                        "automatic_timeout_recoveries",
                        "automatic_capacity_recoveries",
                        "automatic_permission_recoveries",
                        "no_progress_batches",
                    )
                },
                "histories": {
                    key: util.digest(state.get(key))
                    for key in ("failure_history", "recovery_grants", "planning_history", "report_repair_archive")
                },
                "settings": util.digest(state.get("settings")),
            }
    if selection == "planning_reserve":
        planning = state.get("planning") or {}
        grants = planning.get("recovery_review_grants") or []
        grant = next((grant for grant in grants if isinstance(grant, dict) and grant == result), None)
        original = evidence.get("budget") or {}
        if (
            evidence.get("status") == "PAUSED_PLANNING_BUDGET"
            and original.get("origin") == "runner_default"
            and isinstance(grant, dict)
            and grant.get("consumed") is False
            and grant.get("run_dir") == state.get("run_dir")
            and grant.get("binding", {}).get("stage") == evidence.get("stage") == state.get("next_stage")
            and grant["binding"].get("plan_review_calls")
            == evidence.get("plan_review_calls")
            == planning.get("plan_review_calls")
            and grant["binding"].get("recovery_calls_used")
            == evidence.get("recovery_calls_used")
            == planning.get("recovery_review_calls_used", 0)
            and grant["binding"].get("settings_hash") == util.digest(state.get("settings"))
            and grant["binding"].get("contract_digest") == util.digest(state.get("goal_contract"))
        ):
            # Files/source/ordinary-timeout proof remain the original runtime
            # validator's authority, repeated at final task admission.
            return {"grant": copy.deepcopy(grant), "planning": util.digest(planning)}
    if selection == "grant" and evidence.get("status") == "PAUSED_TIMEOUT_RECOVERY":
        grants = state.get("recovery_grants") or []
        grant = grants[-1] if isinstance(grants, list) and grants else None
        if not (
            isinstance(grant, dict)
            and grant.get("actor") == "user_cli"
            and type(grant.get("amount")) is int
            and grant["amount"] > 0
            and type(grant.get("previous_count")) is int
            and grant.get("remaining_count") == max(0, grant["previous_count"] - grant["amount"])
            and result == grant["remaining_count"] == recovery_accounting.spent(state)
            and state.get("consecutive_timeout_recoveries") == 0
        ):
            return None
        event = next(
            (
                event
                for event in reversed(events)
                if event.get("kind") == "recovery_grant"
                and all(event.get(key) == grant.get(key) for key in ("actor", "amount", "request_id", "previous_count"))
            ),
            None,
        )
        return {"grant": copy.deepcopy(grant), "event": copy.deepcopy(event)} if event else None
    if selection == "failure_retry" and isinstance(result, dict):
        identity = result.get("identity") or {}
        saved = (state.get("failure_history") or {}).get(result.get("failure_key")) or {}
        event = next(
            (
                event
                for event in reversed(events)
                if event.get("kind") == "failure_retry_authorized"
                and event.get("failure_key") == result.get("failure_key")
                and event.get("stage") == evidence.get("stage")
                and event.get("count") == result.get("count")
            ),
            None,
        )
        if (
            evidence.get("status") == "PAUSED_REPEATED_FAILURE"
            and result in (state.get("failure_retry_authorizations") or [])
            and identity.get("stage") == evidence.get("stage")
            and saved.get("identity") == identity
            and saved.get("count") == result.get("count")
            and binding.get("failure_history") == util.digest(state.get("failure_history", {}))
            and event
        ):
            return {"authorization": copy.deepcopy(result), "event": copy.deepcopy(event)}
    if selection == "report" and isinstance(result, dict):
        expected = binding.get("pending_report_repair")
        archive = any(
            isinstance(item, dict) and util.digest(item.get("repair")) == expected
            for item in state.get("report_repair_archive") or []
        )
        event = next(
            (
                event
                for event in reversed(events)
                if event.get("kind") == "report_retry_after_format_fix"
                and event.get("actor") == "user_cli"
                and event.get("attempt_id") == result.get("attempt")
            ),
            None,
        )
        if archive and event and not state.get("pending_report_repair"):
            return {"event": copy.deepcopy(event), "repair_digest": expected}
    if selection == "builder" and evidence.get("status") == "PAUSED_BUILDER_RETRY_LIMIT" and isinstance(result, dict):
        selected = result.get("milestone")
        event = next(
            (
                event
                for event in reversed(events)
                if event.get("kind") == "builder_retry"
                and event.get("actor") == "user_cli"
                and event.get("milestone_ids") == selected
            ),
            None,
        )
        key = builder_policy.key(state)
        current = (state.get("builder_retries") or {}).get(key) or {}
        decisions = state.get("builder_retry_decisions") or []
        latest = decisions[-1] if isinstance(decisions, list) and decisions else None
        if (
            event
            and event.get("mode") == "serial"
            and isinstance(latest, dict)
            and latest.get("owner") == "user_cli"
            and latest.get("action") == current.get("action") == "retry"
            and latest.get("milestone_key") == key
            and latest.get("failure") == event.get("failure")
        ):
            return {"event": copy.deepcopy(event), "decision": copy.deepcopy(latest), "milestone_key": key}
    if selection == "bound":
        original = evidence.get("budget") or {}
        current = budget(state, evidence.get("status"))
        if (
            current.get("kind")
            and current.get("kind") == original.get("kind")
            and current.get("origin") == "user_explicit"
            and current.get("limit") != original.get("limit")
        ):
            event = next(
                (
                    event
                    for event in reversed(events)
                    if event.get("kind") == limit_events.KIND
                    and event.get("actor") == "user_cli"
                    and event.get("limit") == current["kind"]
                    and event.get("value") == current.get("limit")
                    and event.get("previous") == original.get("limit")
                ),
                None,
            )
            if current["kind"] in limit_events.LIMIT_FLAGS and event is None:
                return None
            return {"bound": current, "settings": util.digest(state.get("settings")), "event": copy.deepcopy(event)}
    return None


def checked_recovery(state: RunState, row):
    """A diagnostic is insufficient: its exact handler ledger must still agree."""
    original = incident_identity(state, row)
    detail = original_detail(state, row)
    facts = detail.get("evidence") or {}
    binding = detail.get("evaluation_binding") or {}
    if not original or not same_run(state, binding):
        return None
    resolver = state.get("resolver") or {}
    for identity in reversed(resolver.get("operational_diagnostics") or []):
        witness = diagnostic(state, identity)
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
        if (
            checked_binding.get("settings") != util.digest(state.get("settings", {}))
            or checked_binding.get("next_stage") != state.get("next_stage")
            or _later_events(state, checked_binding) is None
        ):
            continue
        current = recovery_facts(state, row, evidence.get("selection"), evidence.get("result"))
        if current is not None and current == evidence.get("facts"):
            return {"receipt_id": identity, "selection": evidence["selection"], "facts": current}
    return None


def decision(state: RunState):
    """One saved-fact disposition for admission and the public projection."""
    rows = incidents(state)
    if not rows:
        return None
    blocked = next((row for row in rows if planning_input(state, row) is None), None)
    row = blocked or rows[0]
    return {
        "kind": "blocked" if blocked is not None else "planning_input",
        "record": row,
        "input": planning_input(state, row),
    }


def presentation(state: RunState):
    current = decision(state)
    if stop.applied_stop(state) or current is None:
        return None
    if current["kind"] == "planning_input" and state.get("status") in ("WAITING_FOR_USER", "AWAITING_GOAL_APPROVAL"):
        # Only existing genuine decision readers may publish their named action.
        # An otherwise idle input receipt creates no Resume or new composer.
        return None
    return {
        "category": "blocked",
        "title": "AutoCode is blocked on internal recovery",
        "what_happened": MESSAGE,
        "what_it_means": "The task is not complete. AutoCode retained the failed attempts and will not ask you to decide its machinery.",
    }
