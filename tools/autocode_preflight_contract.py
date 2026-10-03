"""Validation of operator-authored prerequisite contracts and probe receipts.

Version 1 remains a command/exit-status contract. Version 2 adds finite probe
budgets, worker execution and structured browser/proof setup. No result here
is evidence about the candidate's correctness.
"""
from __future__ import annotations

import json
import math
import re
try:
    from . import autocode_test_cases as test_cases
except ImportError:
    import autocode_test_cases as test_cases

MARKER = "AUTOCODE_PREREQUISITE="


def positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def validate_check(row, version):
    extra = {"execution", "timeout_seconds", "contract"} if version == 2 else set()
    if not isinstance(row, dict) or set(row) - {"id", "phase", "argv", "recovery", "reuse", *extra}:
        raise ValueError("Unknown task preflight check fields")
    if version == 1:
        return row
    if row.get("execution") not in ("runner", "worker"):
        raise ValueError("Version 2 checks require execution: runner or worker")
    timeout = row.get("timeout_seconds", 120)
    if not positive(timeout):
        raise ValueError("Prerequisite timeout_seconds must be finite and positive")
    contract = row.get("contract", {"kind": "command"})
    if not isinstance(contract, dict) or contract.get("kind") not in ("command", "browser", "proof"):
        raise ValueError("Unknown prerequisite result contract")
    kind = contract["kind"]
    if kind == "command" and set(contract) != {"kind"}:
        raise ValueError("Command contracts have only a kind")
    if kind == "browser":
        if (set(contract) != {"kind", "viewport", "fonts", "ready", "canvas"}
                or row["execution"] != "worker" or row.get("reuse", False)):
            raise ValueError("Browser prerequisites require worker execution, reuse=false and explicit capture/readiness inputs")
        validate_viewport(contract["viewport"])
        validate_canvas(contract["canvas"])
        for key in ("fonts", "ready"):
            values = contract[key]
            if not isinstance(values, list) or not values or any(not isinstance(s, str) or not s for s in values) or len(set(values)) != len(values):
                raise ValueError(f"Browser {key} must be a nonempty unique inventory")
    if kind == "proof":
        if set(contract) != {"kind", "cases", "test_changes_allowed"} or type(contract["test_changes_allowed"]) is not bool:
            raise ValueError("Proof prerequisites require cases and an explicit test-change policy")
        cases = contract["cases"]
        if not isinstance(cases, list) or not cases:
            raise ValueError("Proof prerequisite cases cannot be empty")
        ids, selectors = set(), set()
        for case in cases:
            if (not isinstance(case, dict) or set(case) != {"id", "selector", "kind"}
                    or not isinstance(case["id"], str) or not case["id"]
                    or not isinstance(case["selector"], str) or not case["selector"]
                    or case["kind"] not in ("fail_to_pass", "preserve")
                    or case["id"] in ids or case["selector"] in selectors):
                raise ValueError("Proof cases require unique criterion IDs/selectors and fail_to_pass or preserve")
            ids.add(case["id"])
            selectors.add(case["selector"])
            if not test_cases.match_cases([case], [case['selector']])[case['id']]:
                raise ValueError(f"Proof selector must carry its criterion ID as the runner requires: {case['id']}")
    return {**row, "timeout_seconds": timeout, "contract": contract, "reuse": row.get("reuse", kind != "browser")}


def validate_viewport(value):
    if (not isinstance(value, dict) or set(value) != {"width", "height", "device_scale_factor"}
            or any(not positive(n) for n in value.values())
            or type(value["width"]) is not int or type(value["height"]) is not int):
        raise ValueError("Capture viewport requires positive integer width/height and a positive DPR")


def validate_canvas(value):
    if (not isinstance(value, dict) or set(value) != {"alpha", "background"}
            or value["alpha"] not in ("preserve", "composite")
            or (value["alpha"] == "preserve" and value["background"] is not None)
            or (value["alpha"] == "composite" and not re.fullmatch(r"#[0-9a-fA-F]{6}", str(value["background"])))):
        raise ValueError("Canvas requires alpha=preserve/background=null or alpha=composite/background=#RRGGBB")


def read_result(text):
    lines = [line[len(MARKER):] for line in text.splitlines() if line.startswith(MARKER)]
    if len(lines) != 1:
        raise ValueError("Expected exactly one structured prerequisite result, not an aggregate exit status")
    value = json.loads(lines[0])
    if not isinstance(value, dict) or value.get("kind") != "prerequisite" or value.get("status") != "READY":
        raise ValueError("Probe did not establish setup readiness: " + str(value.get("errors", value) if isinstance(value, dict) else value)[:2000])
    return value


def check_result(check, text, state):
    contract = check.get("contract", {"kind": "command"})
    kind = contract["kind"]
    if kind == "command":
        return None
    result = read_result(text)
    if result.get("setup") is not True or result.get("teardown") is not True:
        raise ValueError("Both setup and teardown must finish; collection alone is insufficient")
    if kind == "browser":
        for key in ("viewport", "fonts", "ready", "canvas"):
            if result.get(key) != contract[key]:
                raise ValueError(f"Browser {key} differs from the approved prerequisite contract")
        if result.get("launched") is not True or result.get("captured") is not True:
            raise ValueError("Browser must actually launch and capture under worker permissions")
        preflight = (state.get('settings', {}).get('task_preflight') or {}).get('body', {})
        design = preflight.get('design') or {}
        expected_inputs = {item['path']: item['sha256'] for item in preflight.get('inputs', [])}
        for case in design.get('cases', []):
            if [font['family'] for font in case['fonts']] == contract['fonts'] and case['canvas'] == contract['canvas']:
                expected_fonts = [{'family': font['family'], 'path': font['path'], 'sha256': expected_inputs.get(font['path'])} for font in case['fonts']]
                if result.get('font_sources') != expected_fonts:
                    raise ValueError('Browser did not load the exact approved design font files')
    else:
        cases = contract["cases"]
        reports = result.get("controls")
        if not isinstance(reports, dict) or set(reports) != {"baseline", "reference", "broken"}:
            raise ValueError("Proof readiness requires baseline, reference and broken controls")
        expected = {case["selector"] for case in cases}
        for name, outcomes in reports.items():
            if not isinstance(outcomes, dict) or set(outcomes) != expected:
                raise ValueError(f"{name}: named results must exactly match the approved selector inventory")
        for case in cases:
            selector = case["selector"]
            baseline = "FAIL" if case["kind"] == "fail_to_pass" else "PASS"
            if (reports["baseline"][selector] != baseline or reports["reference"][selector] != "PASS"
                    or reports["broken"][selector] != "FAIL"):
                raise ValueError(f"{selector}: expected {baseline} baseline, passing reference and failing broken control; setup ERROR/SKIP cannot establish eligibility")
        # The runner's test: proof requires added/changed tests. An operator
        # forbidding all test changes cannot satisfy that gate with old tests.
        if not contract["test_changes_allowed"] and any(case["kind"] == "fail_to_pass" for case in cases):
            raise ValueError("Fail-to-pass proof requires added/changed tests; the protected-test policy forbids them. Obtain a corrected proof contract before dispatch")
        declared = {case["id"]: case for case in cases}
        for criterion in ((state.get("goal_contract") or {}).get("body") or {}).get("acceptance_criteria", []):
            method = str(criterion.get("verification_method", "")).strip()
            if not method.startswith(("test:", "guard:")):
                continue
            case = declared.get(criterion["id"])
            if case is None or case["kind"] != ("fail_to_pass" if method.startswith("test:") else "preserve"):
                raise ValueError(f"Approved criterion {criterion['id']} has no matching proof-readiness case")
    return result
