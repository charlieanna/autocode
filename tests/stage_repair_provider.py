#!/usr/bin/env python3
"""Explicitly offline provider faults derived from the revision-31 reports.

The wrapper delegates normal behavior to tools/fake_codex.py. Its repair turns
model the observed mistakes only while the real prompt lacks the corrective
field placement or file/probe mapping. This proves controller behavior and
handoff contents, not that a live model will follow the instructions.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import os
import runpy
import shlex
import subprocess
import sys
import traceback
import uuid
from pathlib import Path


def initial_task(contract):
    """A coherent first task for the existing greeting fixture contract."""
    milestone = contract.get("milestones", [{"id": "M1", "acceptance_criteria": ["C1"]}])[0]
    return {
        "objective": "Implement greeting CLI",
        "affected_paths": ["greet.py"],
        "kind": "implement",
        "milestone_id": milestone["id"],
        "requirements": ["Greet names; reject empty/whitespace input"],
        "acceptance_criteria": milestone["acceptance_criteria"],
        "validation_plan": ["Execute valid, empty and whitespace input"],
    }


def finalizer_report(contract):
    value = {
        "contract": copy.deepcopy(contract),
        "summary": "Ready for approval",
        "contract_changes": [],
        "requirement_trace": [],
        "conflict_resolutions": [],
        "obligation_decisions": [],
        "decisions": [
            {
                "concern_id": "P1",
                "decision": "Reject whitespace",
                "rationale": "Consistent invalid-input contract",
                "acceptance_test": "Whitespace input exits 2",
                "resolved": True,
            }
        ],
    }
    value["contract"]["initial_task"] = initial_task(contract)
    return value


def repair_finalizer(prompt, value):
    """Follow explicit nested placement, otherwise reproduce the observed root field."""
    result = copy.deepcopy(value)
    task = result.pop("initial_task", None) or result["contract"].pop("initial_task", None)
    task = task or initial_task(result["contract"])
    head = prompt.split("CURRENT HANDOFF DATA\n", 1)[0]
    if "contract.initial_task" in head:
        result["contract"]["initial_task"] = task
    else:
        result["initial_task"] = task
    return result


def probe_command(path):
    script = (
        "import json; from pathlib import Path; "
        f"p = Path({path!r}); r = json.loads(p.read_text()); "
        "assert r['decisions'] == []; print('diagnosed missing concern decisions')"
    )
    return shlex.join([sys.executable, "-c", script])


def with_decisions(source, scratch_path):
    """The counterexample the probe must fail on (#1769): the cited report with one decision recorded."""
    text = Path(source).read_text()
    report = json.loads(text)
    return {"file": scratch_path, "find": text, "replace": json.dumps({**report, "decisions": ["P1"]})}


def investigator_report(source):
    source = Path(source)
    return {
        "diagnosis": "The final report omitted the saved concern decisions.",
        "cause": "stage_output",
        "guidance": "Return every saved concern ID once in decisions.",
        "recommendation": "retry",
        "user_question": "",
        "evidence_refs": [str(source)],
        "example": "Given an empty decisions array, the runner rejected missing concern P1.",
        # The live report guessed an archive-prefixed scratch name. The actual
        # scratch contract copies an archived file under its unchanged basename.
        "probe": probe_command("run/" + source.parent.name + "-" + source.name),
        "counterexample": with_decisions(source, "run/" + source.parent.name + "-" + source.name),
        "untestable": "",
    }


def repair_investigator(prompt, data, value):
    """Use the emitted exact scratch map; reproduce generic-event advice when present."""
    result = copy.deepcopy(value)
    head = prompt.split("CURRENT HANDOFF DATA\n", 1)[0]
    result["evidence_refs"] = [ref for ref in result["evidence_refs"] if not ref.startswith("event:")]
    files = (data.get("investigation_context") or {}).get("evidence_files") or []
    mapping = next((row for row in files if row["evidence_ref"] in result["evidence_refs"]), None)
    if mapping:
        result["probe"] = probe_command(mapping["probe_path"])
        result["counterexample"] = with_decisions(mapping["evidence_ref"], mapping["probe_path"])
    else:
        # This was the other observed repair error: a live absolute path rather
        # than the runner's copied scratch path.
        result["probe"] = probe_command(result["evidence_refs"][0])
    permits_events = "Evidence references must be bare event: IDs or exact file paths" in head
    if permits_events and "event: IDs are invalid for investigate_stuck" not in head:
        result["evidence_refs"].append("event:diagnostic-read")
    return result


def append_trace(row):
    with Path(os.environ["STAGE_REPAIR_TRACE"]).open("a") as stream:
        stream.write(json.dumps(row) + "\n")


# The 2026-10-05 self-build planned a ~100 KB contract. Each planning report stayed
# under the 128 KiB report limit, but the Plan Reviewer's repair handoff carries the
# earlier reports too, so it exceeded the 256 KiB handoff limit.
OVERSIZED_REPORT_PADDING = 90 * 1024


def traced(stage, repair):
    path = Path(os.environ["STAGE_REPAIR_TRACE"])
    rows = [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []
    return [row for row in rows if row["stage"] == stage and row["repair"] == repair]


def main():
    delegate = os.environ["STAGE_REPAIR_DELEGATE"]
    if sys.argv[1:] == ["login", "status"]:
        os.execv(sys.executable, [sys.executable, delegate, *sys.argv[1:]])
    prompt = sys.stdin.read()
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    repair = bool(data.get("report_repair"))
    stage = data["original"]["stage"] if repair else data["stage"]
    if repair and stage == "glm_revise" and os.environ["STAGE_REPAIR_CASE"].startswith("empty-planning-repair"):
        case = os.environ["STAGE_REPAIR_CASE"]
        previous = traced(stage, True)
        output = Path(sys.argv[sys.argv.index("-o") + 1])
        if not previous or case.endswith("always-empty"):
            text = ""
        else:
            assert data["rejected_report"]["empty_response"] is True
            assert data["rejected_report"]["content"] == ""
            assert "EMPTY REPAIR RESPONSE" in prompt
            assert "JSON SYNTAX DIAGNOSTIC" not in prompt
            source = data["original_report"]
            text = source["content"]
            assert hashlib.sha256(text.encode()).hexdigest() == source["sha256"]
            diagnostic = source["parse_error"]
            assert diagnostic["message"] == "Expecting ',' delimiter"
            position = diagnostic["position"]
            assert text[position:].startswith('"summary":')
            text = text[:position] + "," + text[position:]
            json.loads(text)
        output.write_text(text)
        append_trace(
            {
                "stage": stage,
                "repair": True,
                "empty": not text,
                "original_retained": bool(data.get("original_report")),
                "output": str(output),
            }
        )
        print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}))
        return 0
    if repair and stage == "glm_revise" and os.environ["STAGE_REPAIR_CASE"] == "json-syntax-diagnostic":
        source = data["rejected_report"]
        text = source["content"]
        diagnostic = source.get("parse_error")
        row = {
            "stage": stage,
            "repair": True,
            "error": data.get("error", ""),
            "parse_error": diagnostic,
            "source_hash_matches": hashlib.sha256(text.encode()).hexdigest() == source["sha256"],
            "syntax_instruction": "zero-based Unicode character offset" in prompt,
        }
        if diagnostic and diagnostic["message"] == "Expecting ',' delimiter":
            position = diagnostic["position"]
            assert text[position:].startswith('"summary":')
            # Correct only the syntax named by the actual handoff location.
            text = text[:position] + "," + text[position:]
            json.loads(text)
        output = Path(sys.argv[sys.argv.index("-o") + 1])
        output.write_text(text)
        append_trace({**row, "output": str(output)})
        print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}))
        return 0
    if repair and stage == "sol" and os.environ["STAGE_REPAIR_CASE"] == "criterion-evidence":
        value = copy.deepcopy(data["rejected_report"]["content"])
        missing = [row for row in value["criterion_results"] if row["status"] == "PASS" and not row["evidence_refs"]]
        for row in missing:
            row["evidence_refs"] = ["check:1"]
        output = Path(sys.argv[sys.argv.index("-o") + 1])
        output.write_text(json.dumps(value))
        append_trace(
            {
                "stage": stage,
                "repair": True,
                "error": data.get("error", ""),
                "repaired_ids": [row["id"] for row in missing],
                "output": str(output),
            }
        )
        session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
        print(json.dumps({"type": "thread.started", "thread_id": session}))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}))
        return 0
    if repair and stage == "astra_discovery" and os.environ["STAGE_REPAIR_CASE"].startswith("discovery-edits"):
        case = os.environ["STAGE_REPAIR_CASE"]
        source = data["rejected_report"]
        text_mode = "-text" in case
        operations = (
            [{"find": '"summary":', "replace": ',"summary":'}]
            if text_mode
            else [{"op": "remove", "path": ["contract", "contract_changes_placeholder"], "value_json": "null"}]
        )
        if case.endswith("authority"):
            if text_mode:
                operations.append({"find": '"answer_id": "Q1"', "replace": '"answer_id": "missing-answer"'})
            else:
                operations.append(
                    {
                        "op": "replace",
                        "path": ["contract", "accepted_assumptions", 0, "answer_id"],
                        "value_json": '"missing-answer"',
                    }
                )
        token = "0" * 64 if case.endswith("stale") else source["sha256"]
        value = {"repair_report": {"sha256": token, "text_edits" if text_mode else "edits": operations}}
        schema = json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
        output = Path(sys.argv[sys.argv.index("-o") + 1])
        output.write_text(json.dumps(value))
        append_trace(
            {
                "stage": stage,
                "repair": True,
                "error": data.get("error", ""),
                "patch": value,
                "output": str(output),
                "source_path": source["path"],
                "source_hash": source["sha256"],
                "offered": "repair_report" in schema.get("properties", {}),
                "rule": ("BOUNDED JSON TEXT REPAIR" if text_mode else "BOUNDED REPORT EDITS") in prompt,
            }
        )
        print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}))
        return 0
    if repair and stage == "glm_revise" and os.environ["STAGE_REPAIR_CASE"].startswith("bounded-report-edits"):
        case = os.environ["STAGE_REPAIR_CASE"]
        operations = [{"op": "remove", "path": ["acceptance_test"], "value": None}]
        if case.endswith("protected"):
            operations.append(
                {"op": "replace", "path": ["contract", "required_behaviors"], "value": ["Weaken the requested outcome"]}
            )
        token = "0" * 64 if case.endswith("stale") else data["rejected_report"]["sha256"]
        value = {"repair_report": {"sha256": token, "edits": operations}}
        output = Path(sys.argv[sys.argv.index("-o") + 1])
        output.write_text(json.dumps(value))
        append_trace(
            {"stage": stage, "repair": True, "error": data.get("error", ""), "patch": value, "output": str(output)}
        )
        print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}))
        return 0
    if repair and stage == "glm_revise" and os.environ["STAGE_REPAIR_CASE"].startswith("malformed-text-edits"):
        case = os.environ["STAGE_REPAIR_CASE"]
        source = data["rejected_report"]
        if not isinstance(source["content"], str):
            source = data["original_report"]
        text = source["content"]
        prefix = text[:60]
        operations = [{"find": prefix, "replace": '{"access_blockers":[],"contract":' + prefix}]
        if case.endswith("protected"):
            body = json.loads('{"contract":' + text)["contract"]
            operations.append(
                {
                    "find": '"required_behaviors": ' + json.dumps(body["required_behaviors"]),
                    "replace": '"required_behaviors": ["Weaken the requested outcome"]',
                }
            )
        if case.endswith("ambiguous"):
            operations = [{"find": '"', "replace": ""}]
        token = "0" * 64 if case.endswith("stale") else source["sha256"]
        value = {"repair_report": {"sha256": token, "text_edits": operations}}
        output = Path(sys.argv[sys.argv.index("-o") + 1])
        output.write_text(json.dumps(value))
        append_trace(
            {
                "stage": stage,
                "repair": True,
                "error": data.get("error", ""),
                "patch": value,
                "output": str(output),
                "text_rule": "BOUNDED JSON TEXT REPAIR" in prompt,
                "source_bytes": len(text.encode()),
                "patch_bytes": len(json.dumps(value).encode()),
            }
        )
        print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}))
        print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 20, "output_tokens": 10}}))
        return 0
    # The runner already launched this provider process. Reuse it for the fake,
    # rather than adding another interpreter and supervised descendant per turn.
    stdout, stderr = io.StringIO(), io.StringIO()
    original_stdin, sys.stdin = sys.stdin, io.StringIO(prompt)
    returncode = 0
    try:
        with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
            try:
                runpy.run_path(delegate, run_name="__main__")
            except SystemExit as error:
                if error.code is not None:
                    returncode = error.code if isinstance(error.code, int) else 1
                    if not isinstance(error.code, int):
                        print(error.code, file=sys.stderr)
            except Exception:
                traceback.print_exc()
                returncode = 1
    finally:
        sys.stdin = original_stdin
    if returncode:
        sys.stdout.write(stdout.getvalue())
        sys.stderr.write(stderr.getvalue())
        return returncode
    output = Path(sys.argv[sys.argv.index("-o") + 1])
    value = json.loads(output.read_text())
    event_rows = [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]
    case = os.environ["STAGE_REPAIR_CASE"]
    row = {"stage": stage, "repair": repair, "error": data.get("error", ""), "output": str(output)}
    if case == "verification-claims" and stage in ("terra", "sol", "astra_review") and not repair:
        checks = data["verification_obligations"]["checks"]
        referenced = [check for check in checks if "claim_ref" in check]
        assert referenced, "Expected identity-bound claim references in the actual stage handoff"
        for check in referenced:
            parts = check["claim_ref"].removeprefix("#/").split("/")
            target = data
            for key in parts:
                target = target[int(key)] if isinstance(target, list) else target[key]
            criteria = data["goal_contract"]["body"]["acceptance_criteria"]
            matches = [criterion for criterion in criteria if [criterion["id"]] == check["criterion_ids"]]
            assert len(matches) == 1 and target == matches[0]["criterion"]
            identity = hashlib.sha256(
                json.dumps(matches[0], sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
            assert identity == check["criterion_identity"]
            assert check["method"] == matches[0]["verification_method"]
            assert isinstance(check["commands"], list) and isinstance(check["collection_recipe"], list)
        row["claim_references"] = len(referenced)
        row["verification_obligations"] = len(checks)
    if stage == "sol" and case == "criterion-evidence":
        value["criterion_results"][0]["evidence_refs"] = []
        row["missing_evidence_id"] = value["criterion_results"][0]["id"]
    if stage == "astra_discovery" and case == "criteria-delta-weakened-proof":
        # Mark the draft proof as runner-enforced. A free-form unapproved
        # proposal may legitimately change, so it is not a protected-proof
        # negative control (autocode_contract_revision.draft_proof_corrections).
        value["contract"]["acceptance_criteria"][0]["verification_method"] = "test: test_c1_greeting"
    if stage == "astra_discovery" and case in (
        "criteria-delta-proof-only",
        "criteria-delta-proof-only-repair",
        "criteria-delta-proof-weakened",
    ):
        value["contract"]["acceptance_criteria"][0]["verification_method"] = (
            "test: test_c1_greeting"
            if case == "criteria-delta-proof-weakened"
            else "test: python3 -m unittest -v test_greet.py"
        )
    if stage == "astra_challenge" and case == "review-policy":
        # Follow the actual active writer commands, rather than returning PASS
        # regardless of the instructions. Contextual audit rules are data.
        active = prompt.split("CURRENT HANDOFF DATA\n", 1)[0]
        commands = {
            "delegated_decisions": "Otherwise return delegated_decisions=[]",
            "contract_changes": "Use contract_changes=[]",
            "contract": "Set contract.initial_task.kind=none",
        }
        row["active_contract_commands"] = [field for field, command in commands.items() if command in active]
        for field in row["active_contract_commands"]:
            value[field] = {} if field == "contract" else []
    if stage == "glm_revise" and case.startswith("revision-reference"):
        if repair:
            value = copy.deepcopy(data["rejected_report"]["content"])
            value["summary"] = "Addressed concern with exact inherited obligations"
        else:
            original = data["goal_contract"]["body"]
            proposed = value["contract"]
            changes = {
                key: proposed[key]
                for key in ("technical_approach", "milestones", "initial_task", "deliverables", "end_to_end_flow")
                if key in proposed and proposed[key] != original.get(key)
            }
            value["contract"] = {
                "revise_contract": data.get("retained_contract"),
                "changes": changes,
                "retain_requirement_trace": True,
            }
            value["requirement_trace"] = []
            value["contract_changes"] = []
            if case == "revision-reference-repair":
                value.pop("summary")
        if case == "revision-reference-stale":
            value["contract"]["revise_contract"] = "r0:stale"
        elif case == "revision-reference-protected":
            value["contract"]["changes"]["permission_boundaries"] = ["Arbitrary network allowed"]
        elif case == "revision-reference-missing-criterion":
            value["contract"]["acceptance_criteria"] = {
                "retain_from": value["contract"]["revise_contract"],
                "upsert": [],
                "remove": ["C1"],
            }
        row["revision_reference"] = copy.deepcopy(value["contract"])
        row["reference_instruction"] = "REVISE ONLY CHANGED FIELDS" in prompt
    if stage == "glm_revise" and case == "batched-protected-diagnostics":
        exact = "Reject an empty name with nonzero exit status"
        if not repair:
            value["contract"]["important_failure_cases"] = ["Reject empty input with a failing exit code"]
            value.pop("summary")
        else:
            value = copy.deepcopy(data["rejected_report"]["content"])
            value["summary"] = "Retained exact failure-case obligation"
            differences = data.get("protected_list_differences", [])
            row["protected_list_differences"] = differences
            if any(
                item.get("field") == "important_failure_cases" and exact in item.get("missing", [])
                for item in differences
            ) or exact in data.get("error", ""):
                value["contract"]["important_failure_cases"] = [exact]
    if stage == "glm_revise" and case == "revision-concerns":
        if repair:
            value = copy.deepcopy(data["rejected_report"]["content"])
            review = (data.get("planning_exchange") or {}).get("astra_challenge") or {}
            concerns = (review.get("report") or {}).get("concerns", [])
            value["responses"] = [
                {
                    "concern_id": concern["id"],
                    "response": "Whitespace is invalid",
                    "evidence_refs": ["goal_contract.body"],
                    "change": "Added whitespace case",
                    "acceptance_test": "Whitespace input exits 2",
                }
                for concern in concerns
            ]
            row["current_review_ids"] = [concern["id"] for concern in concerns]
            row["execution_findings"] = data.get("open_findings")
        else:
            value["responses"] = []  # The retained incident omitted current concern responses.
    if (
        case == "unchanged-rewording"
        and not repair
        and "contract" in value
        and "retain_contract" not in value["contract"]
        and any(event.get("kind") == "goal_approval" for event in data.get("user_events", []))
    ):
        # This follow-up asks to preserve the delivery. The ordinary greeting
        # fake regenerates its initial draft and drops the review's whitespace
        # obligation; retain the approved body so that unrelated fixture fault
        # cannot mask the intentionally overdeclared change below.
        before = (data.get("goal_contract") or {}).get("body") or {}
        after = value["contract"]
        for key in (
            "required_behaviors",
            "scope_exclusions",
            "constraints",
            "important_failure_cases",
            "permission_boundaries",
        ):
            after[key] = list(dict.fromkeys([*before.get(key, []), *after.get(key, [])]))
        old = {item["id"]: item for item in before.get("acceptance_criteria", [])}
        for item in after["acceptance_criteria"]:
            if item["id"] in old:
                for key in ("criterion", "verification_method", "human_review"):
                    item[key] = copy.deepcopy(old[item["id"]].get(key, False))
    if (
        stage == "glm_revise"
        and case == "unchanged-rewording"
        and (repair or any(event.get("kind") == "goal_approval" for event in data.get("user_events", [])))
    ):
        if repair:
            value = copy.deepcopy(data["rejected_report"]["content"])
            value["contract_changes"] = []
        else:
            criterion = value["contract"]["acceptance_criteria"][0]
            value["contract_changes"] = [
                {
                    "item": criterion["id"],
                    "change": "reworded",
                    "basis": "agent_proposed",
                    "answer_id": "",
                    "replacement": json.dumps(criterion),
                    "example_correction": None,
                }
            ]
        row["spurious_rewording"] = True
    elif stage == "glm_revise" and case.startswith("criteria-delta"):
        if repair:
            value = copy.deepcopy(data["rejected_report"]["content"])
            value["summary"] = "Added whitespace case without retranscribing retained criteria"
        else:
            contract = data["goal_contract"]
            value["contract"]["acceptance_criteria"] = {
                "retain_from": f"r{contract['revision']}:{contract['hash']}",
                "upsert": [],
                "remove": [],
            }
            if case in ("criteria-delta-repair", "criteria-delta-proof-only-repair"):
                value.pop("summary")  # one unrelated formatting fault
        delta = value["contract"]["acceptance_criteria"]
        row["delta_offered"] = "CRITERIA DELTA." in prompt
        row["current_contract_offered"] = data.get("retained_contract") == delta["retain_from"]
        if case == "criteria-delta-stale":
            delta["retain_from"] = "r0:stale"
        elif case == "criteria-delta-invalid-row":
            delta["upsert"] = [{"id": "C1"}]
        elif case == "criteria-delta-weakened-proof":
            if repair:
                original = data["protected_contract"]["acceptance_criteria"][0]
            else:
                original = data["goal_contract"]["body"]["acceptance_criteria"][0]
            delta["upsert"] = [{**copy.deepcopy(original), "verification_method": "echo PASS"}]
            value["contract_changes"] = []
        elif case in ("criteria-delta-proof-only", "criteria-delta-proof-only-repair", "criteria-delta-proof-weakened"):
            original = (data["protected_contract"] if repair else data["goal_contract"]["body"])["acceptance_criteria"][
                0
            ]
            delta["proofs"] = [
                {
                    "id": original["id"],
                    "verification_method": (
                        original["verification_method"][len("test:") :].strip()
                        if case != "criteria-delta-proof-weakened"
                        else "echo PASS"
                    ),
                }
            ]
            value["contract_changes"] = []
            row["proof_rule_offered"] = "PROOF-ONLY DELTA" in prompt
            if not repair:
                row["criteria_before"] = copy.deepcopy(data["goal_contract"]["body"]["acceptance_criteria"])
        row["criteria_delta"] = copy.deepcopy(delta)
    elif stage == "glm_revise" and case in ("retained-criteria", "retained-criteria-large"):
        if repair:
            references = {item["retain_criterion"]: item for item in data.get("retained_criteria", [])}
            if not references or "RETAIN VALIDATED TEXT." not in prompt:
                raise RuntimeError("The repair handoff omitted the current retained-criterion inventory or rule")
            value = copy.deepcopy(data["rejected_report"]["content"])
            for index, item in enumerate(value["contract"]["acceptance_criteria"]):
                if "retain_criterion" in item:
                    value["contract"]["acceptance_criteria"][index] = references[item["retain_criterion"]]
            row["used_current_retained_criteria"] = True
            if case == "retained-criteria-large" and not traced(stage, True):
                value["repair_fault"] = True  # one schema fault, preserving the complete large draft
            else:
                value.pop("repair_fault", None)
        else:
            references = {item["retain_criterion"]: item for item in data.get("retained_criteria", [])}
            for index, item in enumerate(value["contract"]["acceptance_criteria"]):
                reference = references.get(item.get("id"))
                identity = hashlib.sha256(json.dumps(item, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
                if reference and identity == reference["sha256"]:
                    bad = dict(reference)
                    bad["sha256"] = ("0" if identity[0] != "0" else "1") + identity[1:]
                    value["contract"]["acceptance_criteria"][index] = bad
                    break
            else:
                raise RuntimeError("Fixture needs an unchanged criterion before injecting the stale-reference fault")
            if case == "retained-criteria-large":
                # Stay below the per-artifact 128 KiB bound, while two copies
                # plus the real repair instructions exceed the handoff bound.
                value["summary"] += "x" * (129000 - len(json.dumps(value).encode()))
    elif stage == "astra_finalize" and case == "repair-limit":
        # Distinct schema errors keep the consecutive-failure guard from ending
        # this run before its two report-only repair attempts are exhausted.
        value["contract"]["initial_task"] = initial_task(value["contract"])
        value["summary"] = "Ready for approval"
        attempt = len(traced(stage, True)) if repair else -1
        if attempt == -1:
            value["contract"].pop("initial_task")
        elif attempt == 0:
            value.pop("summary")
        elif attempt == 1:
            value["decisions"] = "not an array"
        # A wrongly admitted third repair succeeds, exposing an off-by-one gate.
    elif stage == "astra_finalize" and case == "finalizer":
        if repair:
            value = repair_finalizer(prompt, data["rejected_report"]["content"])
        else:
            value["contract"].pop("initial_task")
        row["initial_task_path"] = (
            "contract.initial_task"
            if "initial_task" in value["contract"]
            else "initial_task"
            if "initial_task" in value
            else "missing"
        )
    elif case == "oversized" and not repair and stage in ("astra_discovery", "astra_challenge", "glm_revise"):
        value["summary"] += " " + "x" * OVERSIZED_REPORT_PADDING
    elif stage == "astra_finalize" and case == "oversized":
        # Only the first final review is rejected; a repair of it would be a runner fault.
        if not repair and not traced(stage, False):
            value["contract"].pop("initial_task")
    elif stage == "astra_finalize" and case == "investigator":
        # A persistent empty concern response is an explicit fixture fault. The
        # Investigator is the behavior under test, and its guidance ends it.
        if "INVESTIGATOR GUIDANCE" not in prompt.split("CURRENT HANDOFF DATA\n", 1)[0]:
            value["decisions"] = []
    elif stage == "investigate_stuck" and case == "investigator":
        request = (data.get("investigation_context") or {}).get("stuck") if repair else data.get("stuck")
        if request:
            row.update(stuck_status=request["status"], stuck_identity=request["stage"] + ":" + request["status"])
        if repair:
            value = repair_investigator(prompt, data, data["rejected_report"]["content"])
        else:
            source = next(
                item["output"]
                for item in reversed(data["recent_stages"])
                if item["stage"].startswith("astra_finalize") and item.get("rejected")
            )
            value = investigator_report(source)
            # The event is a real, independently observed read, but the job's
            # evidence field still requires file paths rather than its event ID.
            command = shlex.join(
                [sys.executable, "-c", f"from pathlib import Path; print(Path({source!r}).read_text())"]
            )
            read = subprocess.run(shlex.split(command), text=True, capture_output=True)
            event_rows.insert(
                -1,
                {
                    "type": "item.completed",
                    "item": {
                        "id": "diagnostic-read",
                        "type": "command_execution",
                        "command": command,
                        "exit_code": read.returncode,
                        "aggregated_output": read.stdout + read.stderr,
                    },
                },
            )
            if read.returncode:
                raise RuntimeError("Offline diagnostic read failed")
        row.update(
            evidence_refs=value["evidence_refs"],
            probe=value["probe"],
            has_scratch_map=bool((data.get("investigation_context") or {}).get("evidence_files")),
        )
    if stage == "glm_revise" and case.startswith("bounded-report-edits") and not repair:
        value["acceptance_test"] = "Already covered by the retained criteria"
    if stage == "astra_discovery" and case.startswith("discovery-edits") and data.get("saved_answers") and not repair:
        if "-text" not in case:
            value["contract"]["contract_changes_placeholder"] = None
        row["discovery_fault"] = True
    text = json.dumps(value)
    if row.get("discovery_fault") and "-text" in case:
        assert ', "summary":' in text
        text = text.replace(', "summary":', '"summary":', 1)
    if stage == "glm_revise" and (case == "json-syntax-diagnostic" or case.startswith("empty-planning-repair")):
        text = json.dumps(value, separators=(",", ":"))
        assert not repair and ',"summary":' in text
        text = text.replace(',"summary":', '"summary":', 1)
    if stage == "glm_revise" and case.startswith("malformed-text-edits") and not repair:
        contract = value.pop("contract")
        value.pop("access_blockers", None)
        text = json.dumps(contract) + "," + json.dumps(value)[1:]
    if stage == "glm_revise" and case.endswith("original-empty"):
        text = ""
    output.write_text(text)
    append_trace(row)
    for event in event_rows:
        print(json.dumps(event))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
