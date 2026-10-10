"""Cycle-free report schema extensions for contract-bound runtime roles."""

import copy

STRING = {"type": "string"}
STRINGS = {"type": "array", "items": STRING}


def obj(properties):
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


USER_REQUEST = obj(
    {
        "kind": {
            "type": "string",
            "enum": ["none", "clarification", "contradiction", "infeasible", "permission", "goal_change", "blocker"],
        },
        "discovered": STRING,
        "impact": STRING,
        "decision_needed": STRING,
        "options": STRINGS,
        "proposed_delta": STRING,
    }
)


def role_schema(legacy, role, *, progressive=False):
    schema = copy.deepcopy(legacy)
    schema["properties"].update(
        contract_revision={"type": "integer"},
        contract_hash=STRING,
        task_id=STRING,
        user_request=USER_REQUEST,
        deferred_backlog=STRINGS,
    )
    schema["required"] += ["contract_revision", "contract_hash", "task_id", "user_request", "deferred_backlog"]
    if role == "astra":
        schema["properties"]["status"]["enum"] = ["CONTINUE", "REWORK", "BLOCKED", "COMPLETE"]
        if progressive:
            schema["properties"]["progressive_checkpoint"] = {"type": "boolean"}
        schema["properties"]["next_task"] = obj(
            {
                "kind": {"type": "string", "enum": ["implement", "validate", "none"]},
                "milestone_id": STRING,
                "requirements": STRINGS,
                "acceptance_criteria": STRINGS,
                "validation_plan": STRINGS,
            }
        )
        schema["properties"]["next_task"]["properties"]["findings"] = STRINGS
        schema["properties"]["findings"] = {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["severity", "finding", "evidence"],
                "properties": {
                    "id": STRING,
                    "severity": {"type": "string", "enum": ["critical", "high", "medium", "low"]},
                    "finding": STRING,
                    "evidence": STRING,
                    "blocking": {"type": "boolean"},
                },
            },
        }
        schema["properties"]["finding_dispositions"] = {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "disposition", "evidence"],
                "properties": {
                    "id": STRING,
                    "disposition": {"type": "string", "enum": ["resolved", "retracted"]},
                    "evidence": STRING,
                },
            },
        }
        schema["properties"]["agreed_limitations"] = STRINGS
        schema["required"] += ["next_task", "agreed_limitations"]
    if role == "terra":
        for key in ("addressed_requirements", "untested_behavior", "recommended_checks"):
            schema["properties"][key] = STRINGS
            schema["required"].append(key)
    if role == "sol":
        findings = schema["properties"]["findings"]["items"]
        findings["properties"]["id"] = STRING
        findings["properties"]["blocking"] = {"type": "boolean"}
        findings["required"].append("blocking")
        for key, field in {
            "reproduction_steps": STRINGS,
            "expected": STRING,
            "actual": STRING,
            "why_it_matters": STRING,
            "suggested_correction": STRING,
        }.items():
            findings["properties"][key] = field
            findings["required"].append(key)
        schema["properties"]["criterion_results"]["items"]["properties"]["status"]["enum"] = [
            "PASS",
            "FAIL",
            "NOT_VERIFIED",
        ]
        schema["properties"]["end_to_end_result"] = obj(
            {
                "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_VERIFIED"]},
                "summary": STRING,
                "evidence_refs": STRINGS,
            }
        )
        # Optional for saved reports; new strict responses separate technical proof
        # from the runner-owned human gate instead of inferring it from summary prose.
        technical = obj(
            {
                "status": {"type": "string", "enum": ["PASS", "FAIL", "NOT_VERIFIED"]},
                "summary": STRING,
                "evidence_refs": STRINGS,
            }
        )
        schema["properties"]["end_to_end_result"]["properties"].update(
            technical_result={**technical, "type": ["object", "null"]}, pending_human_criteria=STRINGS
        )
        schema["properties"]["finding_dispositions"] = {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "disposition", "evidence"],
                "properties": {
                    "id": STRING,
                    "disposition": {"type": "string", "enum": ["resolved", "retracted"]},
                    "evidence": STRING,
                },
            },
        }
        schema["required"].append("end_to_end_result")
    return schema
