"""Bounded, pre-approval GLM/Astra collaboration. No provider calls here."""
from __future__ import annotations

import copy
import json

try:
    from . import autocode_goals as goals, autocode_support as s
except ImportError:
    import autocode_goals as goals
    import autocode_support as s

STAGES = ("astra_discovery", "astra_challenge", "glm_revise", "astra_finalize")
S, SS, obj = goals.STRING, goals.STRINGS, goals.obj
CONCERN = obj({"id": S, "concern": S, "evidence_refs": SS, "requested_change": S,
               "acceptance_test": S, "blocking": {"type": "boolean"}})
RESPONSE = obj({"concern_id": S, "response": S, "evidence_refs": SS,
                "change": S, "acceptance_test": S})
DECISION = obj({"concern_id": S, "decision": S, "rationale": S,
                "acceptance_test": S, "resolved": {"type": "boolean"}})
SCHEMAS = {
    "astra_discovery": obj({"contract": goals.BODY_SCHEMA, "summary": S,
                            "code_refs": SS, "alternatives": SS, "uncertainties": SS}),
    "astra_challenge": obj({"summary": S, "concerns": {"type": "array", "items": CONCERN}}),
    "glm_revise": obj({"contract": goals.BODY_SCHEMA, "summary": S, "code_refs": SS,
                       "responses": {"type": "array", "items": RESPONSE}}),
    "astra_finalize": obj({"contract": goals.PLANNING_BODY_SCHEMA, "summary": S,
                           "decisions": {"type": "array", "items": DECISION}}),
}


def enabled(state):
    return bool(state.get("settings", {}).get("joint_planning"))


def is_planning(state, stage):
    return enabled(state) and stage in STAGES


def role_for(state, stage):
    if is_planning(state, stage) and stage in ("astra_discovery", "glm_revise"):
        return "glm"
    return "astra" if stage.startswith("astra") else stage


def engine_for(settings, role):
    return settings.get("roles", {}).get(role, {}).get("engine", settings.get("engine", "codex"))


def start(state):
    if state.get("planning"):
        state.setdefault("planning_history", []).append(copy.deepcopy(state["planning"]))
    state["planning"] = {"astra_calls": 0, "reports": {}, "final_token": None}
    state.update(status="RUNNING", phase="PLANNING", next_stage="astra_challenge", pending_questions=[])


def charge(state, stage):
    if stage not in ("astra_challenge", "astra_finalize"):
        return
    planning = state["planning"]
    if planning["astra_calls"] >= 2:
        raise s.Paused("PAUSED_PLANNING_BUDGET", "Two Astra planning calls used. Inspect the saved exchange; "
                       "use --feedback to explicitly request a new planning cycle. No automatic retry or fallback.")
    planning["astra_calls"] += 1


def _coverage(rows, concerns):
    ids = [row["concern_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != {c["id"] for c in concerns}:
        raise ValueError("Every Astra concern needs exactly one response/decision using its ID")
    for row in rows:
        if any(isinstance(value, str) and not value.strip() for value in row.values()):
            raise ValueError("Planning responses and decisions must be substantive")


def apply(state, stage, value, record):
    s.validate_schema(value, SCHEMAS[stage])
    if stage == "astra_discovery":
        goals.install_draft(state, value["contract"], origin="glm_draft")
        if state.get("pending_questions"):
            state["discovery_summary"] = value["summary"]
            return
        # install_draft starts the bounded cycle once clarification is complete.
    planning = state["planning"]
    reports = planning["reports"]
    if stage == "astra_challenge":
        concerns = value["concerns"]
        ids = [c["id"] for c in concerns]
        if len(ids) != len(set(ids)) or any(not x.strip() for x in ids):
            raise ValueError("Concern IDs must be nonempty and unique")
        if any(not c[k].strip() for c in concerns for k in ("concern", "requested_change", "acceptance_test")):
            raise ValueError("Each concern needs a concrete change and acceptance test")
        state["next_stage"] = "glm_revise"
    elif stage == "glm_revise":
        concerns = reports["astra_challenge"]["report"]["concerns"]
        _coverage(value["responses"], concerns)
        if any(not r["evidence_refs"] for r in value["responses"]):
            raise ValueError("GLM responses must cite investigated evidence")
        goals.install_draft(state, value["contract"], origin=stage)
        state.update(status="RUNNING", phase="PLANNING", next_stage="astra_finalize", pending_questions=[])
    elif stage == "astra_finalize":
        concerns = reports["astra_challenge"]["report"]["concerns"]
        _coverage(value["decisions"], concerns)
        unresolved = {d["concern_id"] for d in value["decisions"] if not d["resolved"]}
        if unresolved and not value["contract"]["open_blocking_questions"]:
            raise ValueError("Unresolved planning decisions must return to the user as blocking questions")
        if not value["contract"]["open_blocking_questions"] and "initial_task" not in value["contract"]:
            raise ValueError("Final plan needs an initial_task so approval does not spend another Astra call")
        goals.install_draft(state, value["contract"], origin=stage)
        planning["final_token"] = goals.token(state["goal_contract"])
    reports[stage] = {"report": copy.deepcopy(value), "output": record["output"]}
    state["discovery_summary"] = value["summary"]


PROMPTS = {
    "astra_discovery": """You are GLM, a full planning partner. Clarify the user's outcome, scope,
constraints and definition of done using at most three material questions at a time.
Use saved answers. Once clear, explore relevant source and originate a concrete draft:
code_refs, alternatives, uncertainties, technical approach, milestones and acceptance tests.
Do not implement. You may challenge assumptions and propose better approaches.
""",
    "astra_challenge": """You are ASTRA, challenging GLM's draft (planning call 1 of 2).
Inspect additional source when needed. Identify missing requirements, unsupported assumptions,
unnecessary complexity and weak tests. Give concise, numbered concerns, evidence references,
requested changes and acceptance tests. Do not manufacture objections or write a second essay.
""",
    "glm_revise": """You are GLM, investigating Astra's concerns. Respond to EVERY concern by ID
with evidence_refs, reasoning, the concrete change (or evidence-backed pushback) and a test.
Revise the complete contract and identify what changed. You are a planning partner, not merely
a coder: retain your approach where source evidence supports it. Never hide unresolved questions.
""",
    "astra_finalize": """You are ASTRA, making the final planning decision (call 2 of 2).
Settle EVERY concern by ID using GLM's evidence-backed responses and source inspection as needed.
Return the proposed final contract and concise decisions/rationales/tests. Include initial_task
in the contract: objective, affected_paths, kind (implement or validate), milestone_id,
requirements, acceptance_criteria IDs, validation_plan. Make it a substantial, coherent,
executable milestone including related changes, tests, local fixes and evidence.
If blocked with no safe first task, use kind=none and empty task strings/lists.
Unresolved decisions MUST appear in open_blocking_questions, never silently become assumptions.
There is no further debate round. The user must approve this exact plan before implementation.
""",
}


def context(state, stage, state_path):
    exchange = copy.deepcopy(state.get("planning", {}))
    for entry in exchange.get("reports", {}).values():
        # Current contract is included once. Older full drafts stay retrievable
        # via the artifact path; concerns/responses retain the explicit delta.
        entry["report"].pop("contract", None)
    packet = {"task": state["task"], "workspace": state["workspace"], "state_file": str(state_path),
              "joint_planning": True, "execution_engine": engine_for(state["settings"], role_for(state, stage)),
              "stage": stage, "goal_contract": state.get("goal_contract"),
              "saved_answers": state.get("answers", {}), "brief_feedback": state.get("brief_feedback", []),
              "planning": exchange, "budget": "two Astra calls per explicitly requested cycle"}
    if state["settings"].get("figma_file"):
        packet["figma_file"] = state["settings"]["figma_file"]
    packet['user_events'] = state.get('user_events', [])
    try:
        from . import autocode_figma as figma
    except ImportError:
        import autocode_figma as figma
    figma_instruction = figma.instructions(state["settings"])
    prompt = PROMPTS[stage] + figma_instruction + goals.DECISION_PROVENANCE + goals.CONTRACT_REFERENCES + s.MILESTONE_POLICY + s.COMMON + "\nWork read-only; return the report, the runner saves it.\nCURRENT HANDOFF DATA\n" + json.dumps(packet, indent=2)
    return prompt, {"estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
                    "soft_budget_tokens": state["settings"].get("context_soft_tokens", 10000)}
