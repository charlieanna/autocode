"""Backend Planner dispatch module shared by conversation updates and runner handoff.

Per the recorded Q2-PLANNER-TARGET answer this module is "the actual Planner":
it reuses the runner Planner role configuration semantics and the M1 versioned
structured-draft contract, runs deny-all tool-free Planner sessions with
durable dispatch-observer records, and preserves configured model routes
without silent fallback. Visual-review defaults are suggestions; selecting a
model does not establish image-based visual evidence. No visual PASS
is claimed here.  Architect approval gates stay in the runner;
``record_product_change`` is the frozen-contract backend control.
"""

from __future__ import annotations

import json
import os
import uuid
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path

try:
    from .. import autocode_conversation as conversation_protocol
    from .. import autocode_planner_contract as planner_contract
except ImportError:  # Direct script execution from any working directory.
    import importlib.util

    _protocol_spec = importlib.util.spec_from_file_location(
        "_autocode_conversation_protocol", Path(__file__).resolve().parents[1] / "autocode_conversation.py"
    )
    conversation_protocol = importlib.util.module_from_spec(_protocol_spec)
    _protocol_spec.loader.exec_module(conversation_protocol)
    _contract_spec = importlib.util.spec_from_file_location(
        "_autocode_planner_contract", Path(__file__).resolve().parents[1] / "autocode_planner_contract.py"
    )
    planner_contract = importlib.util.module_from_spec(_contract_spec)
    _contract_spec.loader.exec_module(planner_contract)


def _now():
    return datetime.now(UTC).isoformat(timespec="milliseconds")


try:
    from ..autocode_planner_routes import (
        PlannerDispatchError,
        PlannerRouteError,
        caps_disabled,
        enforce_fresh_runner_role_models,
        enforce_route_policy,
    )
    from ..autocode_planner_routes import (
        conversation_planner_routes as conversation_planner_routes,
    )
    from ..autocode_planner_routes import (
        enforce_conversation_routes as enforce_conversation_routes,
    )
except ImportError:
    from autocode_planner_routes import (
        PlannerDispatchError,
        PlannerRouteError,
        caps_disabled,
        enforce_fresh_runner_role_models,
        enforce_route_policy,
    )
    from autocode_planner_routes import (
        conversation_planner_routes as conversation_planner_routes,
    )
    from autocode_planner_routes import (
        enforce_conversation_routes as enforce_conversation_routes,
    )


def planner_prompt(messages, *, logical_turn_id, requirements_revision, route, previous_draft=None):
    """Tool-free structured Planner prompt bound to the exact revision and turn."""
    context = ""
    if isinstance(previous_draft, dict):
        context = (
            "\n\nThe current committed structured draft (supersede it with a better one when the "
            "conversation justifies changes, or carry it forward unchanged when it does not):\n"
            + json.dumps(
                {
                    key: previous_draft.get(key)
                    for key in ("goal", "requirements", "milestones", "parallelism", "outstanding_questions")
                },
                ensure_ascii=False,
            )
        )
    return (
        "You are the independent Planner for the Autocode requirements conversation. "
        "You are NOT the conversation partner: another role (the Requirements Gatherer) talks with the user. "
        "Your only job is to return the current structured plan draft as JSON. "
        "This is a project-free session: you have no repository access and no tools; do not invoke tools, "
        "execute code, create files, or claim you inspected a project. "
        "Return ONLY one JSON object (no prose, no code fences) with exactly these fields: "
        '"contract_version": 1, "kind": "autocode.planner-structured-draft", '
        '"goal": string, "requirements": array of strings or objects, "milestones": array of strings or objects, '
        '"parallelism": array of strings or objects, "unresolved_questions": array of strings or objects, '
        '"source_revision": {"requirements_revision": integer, "logical_turn_id": string}, '
        '"attribution": {"role": "planner", "model": string, "reasoning_effort": string}, '
        '"freshness": {"state": "fresh", "updated_at": ISO-8601 string}. '
        f"You MUST bind the result with requirements_revision {requirements_revision} and "
        f'logical_turn_id "{logical_turn_id}". '
        f'Echo attribution exactly: role "planner", model "{route.get("model")}", '
        f'reasoning_effort "{route.get("reasoning_effort")}". '
        "Derive goal, requirements, milestones, parallelism and unresolved questions from the conversation; "
        "keep unresolved_questions to genuine open decisions the Architect must see."
        + context
        + "\n\nThe conversation so far, in order:\n"
        + json.dumps([{"role": row.get("role"), "text": row.get("text")} for row in messages], ensure_ascii=False)
    )


def extract_structured_draft(text):
    """Parse the Planner's raw reply into a structured draft object.

    Accepts a bare JSON object or JSON embedded in prose/code fences.  Malformed
    output raises with the raw reply retained as bounded error evidence.
    """
    bounded = text if isinstance(text, str) else ""
    if not bounded.strip():
        raise PlannerDispatchError(
            "The Planner returned no structured result.", stage="structured_parse", evidence={"raw_result": ""}
        )
    candidates = [bounded.strip()]
    for line in bounded.splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            continue
        if stripped.startswith("{"):
            candidates.append(stripped.rstrip("`"))
    first, last = bounded.find("{"), bounded.rfind("}")
    if first >= 0 and last > first:
        candidates.append(bounded[first : last + 1])
    for candidate in candidates:
        try:
            value = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(value, dict):
            return value
    raise PlannerDispatchError(
        "The Planner result was not a JSON object.", stage="structured_parse", evidence={"raw_result": bounded[:4000]}
    )


def validate_structured_result(draft, *, requirements_revision, logical_turn_id, route=None):
    """Schema-validate a structured draft and enforce its revision/turn binding."""
    try:
        validated = planner_contract.validate_structured_draft(draft)
    except planner_contract.PlannerContractError as error:
        raise PlannerDispatchError(
            f"The structured Planner result was rejected: {error}",
            stage="structured_schema",
            evidence={"error": str(error), "raw_result": bounded_raw(draft)},
        ) from error
    binding = validated.get("source_revision") if isinstance(validated.get("source_revision"), dict) else {}
    if (
        binding.get("requirements_revision") != requirements_revision
        or binding.get("logical_turn_id") != logical_turn_id
    ):
        raise PlannerDispatchError(
            "The structured Planner result is not bound to this requirements revision and logical turn.",
            stage="structured_binding",
            evidence={
                "expected": {"requirements_revision": requirements_revision, "logical_turn_id": logical_turn_id},
                "got": binding,
            },
        )
    if route is not None:
        expected = {"role": "planner", "model": route["model"], "reasoning_effort": route["reasoning_effort"]}
        actual = validated["attribution"]
        if any(actual.get(key) != value for key, value in expected.items()):
            raise PlannerDispatchError(
                "The structured Planner attribution does not match its dispatched route.",
                stage="structured_attribution",
                evidence={"expected": expected, "got": actual},
            )
    declared = validated.get("freshness") if isinstance(validated.get("freshness"), dict) else {}
    if declared.get("state") != "fresh":
        # Schema validity alone is not success: a result explicitly reporting a
        # failed/pending/stale state must never be promoted to a fresh current
        # draft.  Retain the declared state, its error evidence and the raw
        # result, and preserve the previous usable draft via the reject path.
        raise PlannerDispatchError(
            "The structured Planner result did not report a successful fresh state "
            f"({declared.get('state')!r}); it cannot become the accepted draft revision.",
            stage="structured_freshness",
            evidence={"declared_freshness": deepcopy(declared), "raw_result": bounded_raw(draft)},
        )
    return validated


def bounded_raw(value):
    try:
        text = json.dumps(value, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(value)[:4000]
    return text[:4000]


def new_dispatch(*, logical_turn_id, requirements_revision, client_request_id, route):
    """A durable, protocol-shaped Planner dispatch record bound to revision+turn.

    Same state machine as the conversation protocol's dispatch records so
    ``autocode_conversation.transition`` drives it, with the Planner binding
    (requirements revision) and role carried additively.
    """
    conversation_protocol._identifier(logical_turn_id, "Logical turn id")
    conversation_protocol._identifier(client_request_id, "Client request id", optional=True)
    if type(requirements_revision) is not int or requirements_revision < 1:
        raise PlannerDispatchError(
            "A Planner dispatch needs a positive requirements revision.", stage="dispatch_record"
        )
    enforced = enforce_route_policy({"planner": route})
    at = _now()
    return {
        "id": uuid.uuid4().hex,
        "role": "planner",
        "logical_turn_id": logical_turn_id,
        "requirements_revision": requirements_revision,
        "client_request_id": client_request_id,
        "route": enforced["planner"],
        "state": "SAVED",
        "created_at": at,
        "updated_at": at,
        "history": [{"state": "SAVED", "at": at}],
        "process": None,
        "provider_identity": None,
        "raw_result": None,
        "result": None,
        "reply_commit_id": None,
    }


def opencode_planner_provider(
    messages, route, workdir, *, dispatch_observer=None, logical_turn_id=None, requirements_revision=None
):
    """Run a fresh tool-free Planner session on its validated configured route."""
    enforced = enforce_route_policy({"planner": route})["planner"]
    try:
        from . import conversation_transport as chats
    except ImportError:
        import conversation_transport as chats
    try:
        from ..providers import opencode as transport
    except ImportError:
        transport = chats.opencode_transport
    try:
        transport.check_subscription_routes({"planner": enforced}, workdir)
    except RuntimeError as error:
        raise chats.ConversationProviderError(str(error), outcome="not_dispatched") from error
    prompt = planner_prompt(
        messages, logical_turn_id=logical_turn_id, requirements_revision=requirements_revision, route=enforced
    )
    agent = "autocode_planner_" + uuid.uuid4().hex
    env = dict(os.environ)
    try:
        inherited = json.loads(env.get("OPENCODE_CONFIG_CONTENT", "{}"))
    except (TypeError, ValueError) as error:
        raise PlannerDispatchError(
            "OpenCode inline configuration is invalid. Correct it, then retry.", stage="planner_provider"
        ) from error
    if not isinstance(inherited, dict) or not isinstance(inherited.get("agent", {}), dict):
        raise PlannerDispatchError(
            "OpenCode inline configuration must contain an object of agents.", stage="planner_provider"
        )
    config = {
        **inherited,
        "share": "disabled",
        "autoupdate": False,
        "permission": {"*": "deny"},
        "agent": {
            **inherited.get("agent", {}),
            agent: {
                "description": "Structured Planner; tool-free deny-all session",
                "mode": "primary",
                "permission": {"*": "deny"},
            },
        },
    }
    env["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    env["OPENCODE_PERMISSION"] = json.dumps({"*": "deny"})
    env["OPENCODE_DISABLE_PROJECT_CONFIG"] = "true"
    env["OPENCODE_PURE"] = "true"
    command = [
        "opencode",
        "run",
        "--pure",
        "--dir",
        str(workdir),
        "--format",
        "json",
        "--agent",
        agent,
        "--model",
        enforced["model"],
        "--variant",
        enforced["reasoning_effort"],
        "--title",
        "Autocode structured Planner draft",
    ]
    if dispatch_observer:
        code, output = chats._capture(command, env, workdir, prompt, dispatch_observer=dispatch_observer)
    else:
        code, output = chats._capture(command, env, workdir, prompt)
    if dispatch_observer:
        # Keep the complete provider event stream even when exit/schema fails.
        dispatch_observer("provider_events", {"raw_events": output})
    sessions = set()
    events = []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
            if isinstance(event.get("sessionID"), str):
                sessions.add(event["sessionID"])
    if dispatch_observer and len(sessions) == 1:
        dispatch_observer("provider_identified", {"session_id": next(iter(sessions))})
    if code:
        raise chats.ConversationProviderError(
            "OpenCode could not get a structured Planner result. Check the configured model and provider connection, then retry."
        )
    if not any(row.get("type") == "turn.completed" for row in transport.normalized_events(events)):
        raise chats.ConversationProviderError(
            "The Planner provider did not finish a successful session. The prior draft is preserved."
        )
    texts = []
    for event in events:
        if event.get("type") == "error":
            raise chats.ConversationProviderError(
                "The Planner could not finish its structured result. Check the provider connection, then retry."
            )
        if event.get("type") == "tool_use":
            raise chats.ConversationProviderError(
                "The structured Planner attempted a tool call. Planner sessions are tool-free; retry the turn."
            )
        part = event.get("part")
        if event.get("type") == "text" and isinstance(part, dict) and isinstance(part.get("text"), str):
            texts.append(part["text"])
    reply = "\n\n".join(texts).strip()
    if not reply:
        raise chats.ConversationProviderError(
            "The Planner returned no structured result. Your message is saved; retry when ready."
        )
    return reply


opencode_planner_provider.autocode_dispatch_aware = True


def record_product_change(doc, *, detail, recorded_at=None):
    """Compatibility entry point; the core consumes the domain helper directly."""
    try:
        return planner_contract.record_product_change(doc, detail=detail, recorded_at=recorded_at)
    except planner_contract.PlannerContractError as error:
        raise PlannerDispatchError(str(error), stage="product_change") from error
