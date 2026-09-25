"""Autoplanner owns requirements, draft plans and independent plan review."""
from __future__ import annotations

import copy
import json

try:
    from .. import autocode_goals as goals, autocode_support as s
except ImportError:
    import autocode_goals as goals
    import autocode_support as s

STAGES = ("requirements_gather", "astra_discovery", "astra_challenge", "glm_revise", "astra_finalize")
S, SS, obj = goals.STRING, goals.STRINGS, goals.obj
CONCERN = obj({"id": S, "concern": S, "evidence_refs": SS, "requested_change": S,
               "acceptance_test": S, "blocking": {"type": "boolean"}})
RESPONSE = obj({"concern_id": S, "response": S, "evidence_refs": SS,
                "change": S, "acceptance_test": S})
DECISION = obj({"concern_id": S, "decision": S, "rationale": S,
                "acceptance_test": S, "resolved": {"type": "boolean"}})
REQUIREMENT = obj({"id": S, "text": S, "source_quote": S})
CONFLICT = obj({"requirement_ids": SS, "description": S})
CONFLICT_RESOLUTION = obj({"requirement_ids": SS,
    "basis": {"type": "string", "enum": ["user_answer", "user_feedback"]},
    "answer_id": S, "source_quote": S, "resolution": S})
CHANGE = obj({"item": S, "change": {"type": "string", "enum": ["removed", "reworded", "permission_changed"]},
              "basis": {"type": "string", "enum": ["user_answer", "user_feedback", "agent_proposed"]},
              "answer_id": S, "replacement": S})
TRACE = obj({"requirement_id": S, "disposition": {"type": "string", "enum": ["covered", "excluded", "superseded"]},
             "evidence": S})
SCHEMAS = {
    "requirements_gather": obj({
        "summary": S, "intended_outcome": S, "required_behaviors": SS,
        "constraints": SS, "acceptance_tests": SS, "source_refs": SS,
        "proposed_assumptions": SS,
        "open_questions": {"type": "array", "maxItems": 3, "items": goals.QUESTION},
        "requirements": {"type": "array", "items": REQUIREMENT},
        "ignored_statements": SS,
        "conflicts": {"type": "array", "items": CONFLICT},
    }),
    "astra_discovery": obj({"contract": goals.BODY_SCHEMA, "summary": S,
                            "code_refs": SS, "alternatives": SS, "uncertainties": SS,
                            "contract_changes": {"type": "array", "items": CHANGE},
                            "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
                            "requirement_trace": {"type": "array", "items": TRACE}}),
    "astra_challenge": obj({"summary": S, "concerns": {"type": "array", "items": CONCERN}}),
    "glm_revise": obj({"contract": goals.BODY_SCHEMA, "summary": S, "code_refs": SS,
                       "responses": {"type": "array", "items": RESPONSE},
                       "contract_changes": {"type": "array", "items": CHANGE},
                       "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
                       "requirement_trace": {"type": "array", "items": TRACE}}),
    "astra_finalize": obj({"contract": goals.PLANNING_BODY_SCHEMA, "summary": S,
                           "decisions": {"type": "array", "items": DECISION},
                           "contract_changes": {"type": "array", "items": CHANGE},
                           "conflict_resolutions": {"type": "array", "items": CONFLICT_RESOLUTION},
                           "requirement_trace": {"type": "array", "items": TRACE}}),
}


def enabled(state):
    return bool(state.get("settings", {}).get("joint_planning"))


def is_planning(state, stage):
    return enabled(state) and stage in STAGES


def role_for(state, stage):
    if is_planning(state, stage) and stage == "requirements_gather":
        return "requirements"
    if is_planning(state, stage) and stage in ("astra_discovery", "glm_revise"):
        return "glm"
    return "astra" if stage.startswith("astra") else stage


def route_for(state, stage, role=None):
    """Return the saved model route for a semantic workflow role.

    Completion remains an Astra-format decision stage, but it intentionally has
    its own model, reasoning level, and session so plan review and completion
    ownership can be tuned independently.
    """
    if stage == "astra_resolve":
        return "resolver"
    if stage == "requirements_gather":
        return "requirements"
    role = role or role_for(state, stage)
    roles = state.get("settings", {}).get("roles", {})
    if stage in ("astra_challenge", "astra_finalize") and "plan_reviewer" in roles:
        return "plan_reviewer"
    if stage in ("astra_review", "astra_checkpoint") and "completion" in roles:
        return "completion"
    return role


def engine_for(settings, role):
    return settings.get("roles", {}).get(role, {}).get("engine", settings.get("engine", "codex"))


def start(state):
    try:
        from .. import autopilot
    except ImportError:
        import autopilot
    return autopilot.start_planning(state)


def charge(state, stage):
    if stage not in ("astra_challenge", "astra_finalize"):
        return
    planning = state["planning"]
    if planning["astra_calls"] >= 2:
        raise s.Paused("PAUSED_PLANNING_BUDGET", "Two plan-review calls used. Inspect the saved exchange; "
                       "use --feedback to explicitly request a new planning cycle. No automatic retry or fallback.")
    planning["astra_calls"] += 1


def _coverage(rows, concerns):
    ids = [row["concern_id"] for row in rows]
    if len(ids) != len(set(ids)) or set(ids) != {c["id"] for c in concerns}:
        raise ValueError("Every plan-review concern needs exactly one response/decision using its ID")
    for row in rows:
        if any(isinstance(value, str) and not value.strip() for value in row.values()):
            raise ValueError("Planning responses and decisions must be substantive")


def apply(state, stage, value, record):
    """Compatibility entry; planning transitions belong to Autopilot."""
    try:
        from .. import autopilot
    except ImportError:
        import autopilot
    return autopilot.apply_planning(state, stage, value, record)


PROMPTS = {
    "requirements_gather": """You are the Requirements Gatherer, in your own read-only session.
Inspect the user's idea and relevant workspace source. Return only a requirements handoff:
intended outcome, stated behaviors, constraints, acceptance tests, source references,
up to three genuinely blocking questions, and clearly labeled proposed assumptions.
Do not create a technical approach, milestone, dependency graph, or implementation plan.
Do not treat a proposed default as a user answer. Do not implement.
Return requirements: each has an id, the requirement text, and a source_quote copied
verbatim from the task or a saved user event. Put requirement-like sentences you are
not carrying (must, must not, never, only, required, exactly) in ignored_statements
with the reason. Put unresolved contradictions in conflicts with the requirement ids.
Do not label an explicit saved clarification or a historical/current distinction as
an unresolved conflict. Preserve the applicable requirements and their provenance.
The runner saves this report as a separate artifact for the Planner.
The requirement_coverage_checklist contains the exact task sentences checked by
the runner. Account for every entry in requirements using a verbatim source_quote,
or in ignored_statements with the exact statement and a substantive reason.
Include requirements from the rest of the task and saved user events as well.
""",
    "astra_discovery": """You are the Planner, in a session separate from the Requirements Gatherer.
For a new run, use requirements_handoff and its saved artifact as your input; do not silently
replace its stated requirements or convert its proposed assumptions into user decisions.
Carry unresolved requirements questions into open_blocking_questions unless saved answers
resolve them. Older saved runs may lack a requirements handoff; only then gather missing
requirements yourself. Explore relevant source and originate a concrete draft:
code_refs, alternatives, uncertainties, technical approach, milestones and acceptance tests.
For every milestone, state depends_on as prerequisite milestone IDs or [] when it can
start independently. Base those edges on actual interfaces, shared files, sequencing
and validation needs. Do not turn milestones into parallel jobs or launch any work.
Declare affected_paths for each milestone, including its tests and shared files.
Autopilot dispatches the Builder scheduler using approved dependencies and disjoint path ownership.
Do not implement. You may challenge assumptions and propose better approaches.
""",
    "astra_challenge": """You are the independent Plan Reviewer, challenging the Planner's draft (review call 1 of 2).
Inspect additional source when needed. Check every dependency edge, missing prerequisite,
cycle and claimed independent milestone against source evidence and interface ownership.
Check affected_paths for every milestone; overlapping writes must not be called independent.
Identify missing requirements, unsupported assumptions, unnecessary complexity and weak tests.
Give concise, numbered concerns, evidence references,
requested changes and acceptance tests. Do not manufacture objections or write a second essay.
""",
    "glm_revise": """You are the Planner, investigating the Plan Reviewer's concerns. Respond to EVERY concern by ID
with evidence_refs, reasoning, the concrete change (or evidence-backed pushback) and a test.
Revise the complete contract, including depends_on for every milestone, and identify what changed.
You are a planning partner, not merely
a coder: retain your approach where source evidence supports it. Never hide unresolved questions.
""",
    "astra_finalize": """You are the independent Plan Reviewer, making the final planning decision (review call 2 of 2).
Settle EVERY concern by ID using the Planner's evidence-backed responses and source inspection as needed.
Confirm that milestone dependencies are complete and acyclic, and that [] is used only
for genuinely independent work. Do not schedule or launch milestones.
Return the proposed final contract and concise decisions/rationales/tests. Include initial_task
in the contract: objective, affected_paths, kind (implement or validate), milestone_id,
requirements, acceptance_criteria IDs, validation_plan. Its milestone must have depends_on [].
Make it a substantial, coherent,
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
              "joint_planning": True, "execution_engine": engine_for(state["settings"], route_for(state, stage)),
              "stage": stage,
              "goal_contract": None if stage == "requirements_gather" else state.get("goal_contract"),
              "requirements_handoff": None if stage == "requirements_gather" else state.get("requirements_handoff"),
              "requirements_history": None if stage == "requirements_gather" else [
                  {"output": entry.get("output"),
                   "requirements": [{"id": row["id"], "source_quote": row.get("source_quote", "")}
                                    for row in (entry.get("report") or {}).get("requirements", [])],
                   "conflicts": (entry.get("report") or {}).get("conflicts", [])}
                  for entry in state.get("requirements_history", [])
                  if (entry.get("report") or {}).get("conflicts")],
              "saved_answers": state.get("answers", {}), "brief_feedback": state.get("brief_feedback", []),
              "planning": exchange, "budget": "two plan-review calls per explicitly requested cycle"}
    if stage == "requirements_gather":
        packet["requirement_coverage_checklist"] = goals.cue_sentences(state.get("task"))
    if state["settings"].get("figma_file"):
        packet["figma_file"] = state["settings"]["figma_file"]
    packet['user_events'] = state.get('user_events', [])
    try:
        from .. import autocode_figma as figma
    except ImportError:
        import autocode_figma as figma
    figma_instruction = figma.instructions(state["settings"])
    planning_policy = "" if stage == "requirements_gather" else (
        goals.DECISION_PROVENANCE + goals.CONTRACT_REFERENCES + s.MILESTONE_POLICY)
    prompt = PROMPTS[stage] + figma_instruction + planning_policy + s.COMMON + "\nWork read-only; return the report, the runner saves it.\nCURRENT HANDOFF DATA\n" + json.dumps(packet, indent=2)
    return prompt, {"estimated_prompt_tokens": (len(prompt.encode()) + 3) // 4,
                    "soft_budget_tokens": state["settings"].get("context_soft_tokens", 10000)}


def prepare(state, stage, state_path, schema_dir):
    from .common import ModelRequest
    if stage not in STAGES:
        raise ValueError(f"Autoplanner cannot run {stage}")
    joint = is_planning(state, stage)
    state["phase"] = "PLANNING" if joint else "DISCOVERING"
    prompt, metrics = context(state, stage, state_path) if joint else s.context_packet(state, stage, state_path)
    role = role_for(state, stage)
    return ModelRequest(role, route_for(state, stage, role), prompt, metrics,
                        SCHEMAS[stage] if joint else goals.DISCOVERY_SCHEMA, False)


def apply_result(state, stage, value, record):
    """Compatibility entry; Autopilot consumes the planner result."""
    try:
        from .. import autopilot
    except ImportError:
        import autopilot
    return autopilot.apply_planning_result(state, stage, value, record)
