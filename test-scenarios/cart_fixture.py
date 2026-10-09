#!/usr/bin/env python3
"""Cart-spec-aware Validator for the mutation scenarios (09/10/11).

The stock offline fixture (tools/fake_codex.py) independently executes greet.py
behavior checks. The mutation suite needs the same *architectural* treatment
applied to a well-specified cart module: a Validator that inspects the real
tree, runs behavior against the spec, checks requirement coverage in the test
suite, and refuses to trust builder-claimed evidence.

This is a harness fixture, not product code. Scores produced with it measure
whether the runner correctly delivers work to an independent Validator, records
its findings, and gates COMPLETE on them — not whether a live model would
judge as sharply. Live-model scores are a separate trial.

Modes (AUTOCODE_FIXTURE_VALIDATION_MODE):
  behavior  — gate on spec behavior only (scenario 09: code mutants)
  trace     — behavior must pass AND every requirement must be proven by tests
              (scenario 11: test-suite mutants)
  strict    — trace + never trust builder-evidence claims (scenario 10)

Report shape matches schemas/v3-sol.json exactly (additionalProperties:false).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import uuid
from pathlib import Path

# Spec clauses. Finding vocabulary is aligned with the scenario scorers.
REQUIREMENTS = [
    {"id": "R1", "desc": "total([])==0",
     "hint": r"empty|total\(\[\]\)|sum.*default|returns? 1|off.by.one",
     "marker": r"total\(\s*\[\]\s*\)"},
    {"id": "R2", "desc": "total([1,2,3])==6",
     "hint": r"1, *2, *3|sums|total\(\s*\[1",
     "marker": r"total\(\s*\[1, *2, *3\]\s*\)"},
    {"id": "R3", "desc": "TypeError on non-numeric",
     "hint": r"TypeError|non.numeric|swallow|silently|type coerc|concatenat|string",
     "marker": r"raises\(\s*TypeError"},
    {"id": "R4", "desc": "discount(100)==100 exclusive boundary",
     "hint": r"boundar|exactly 100|discount\(100|>=|inclusive|off.by.one",
     "marker": r"discount\(\s*100\s*\)"},
    {"id": "R5", "desc": "discount(101)==90.9",
     "hint": r"90\.9|discount\(101|round|precision|decimal|above threshold|invert|small order",
     "marker": r"90\.9"},
    {"id": "R6", "desc": "ValueError on negative",
     "hint": r"ValueError|negative|not raised|missing.*(raise|check)",
     "marker": r"raises\(\s*ValueError"},
]

BEHAVIOR_CHECKS = r'''
import json, sys
sys.path.insert(0, ".")
results = []

def check(rid, desc, fn):
    try:
        fn()
        results.append({"id": rid, "ok": True, "detail": "ok", "desc": desc, "got": None})
    except AssertionError as e:
        results.append({"id": rid, "ok": False, "detail": str(e), "desc": desc, "got": getattr(e, "got", None)})
    except Exception as e:
        results.append({"id": rid, "ok": False, "detail": "%s: %s" % (type(e).__name__, e), "desc": desc, "got": None})

try:
    from cart import total, discount
except Exception as e:
    print(json.dumps([{"id": "import", "ok": False, "detail": "import failed: %s" % e, "desc": "import cart", "got": None}]))
    raise SystemExit(0)

class A(AssertionError):
    def __init__(self, msg, got=None):
        super().__init__(msg)
        self.got = got

def r1():
    v = total([])
    if v != 0:
        raise A("total([]) -> %r, expected 0 (empty-list / sum default)" % (v,), got=v)
check("R1", "total([])==0", r1)

def r2():
    v = total([1, 2, 3])
    if v != 6:
        raise A("total([1,2,3]) -> %r, expected 6" % (v,), got=v)
check("R2", "total([1,2,3])==6", r2)

def r3():
    try:
        v = total([1, "2", 3])
    except TypeError:
        return
    except Exception as e:
        raise A("non-numeric raised %s, expected TypeError" % type(e).__name__)
    raise A("non-numeric returned %r instead of raising TypeError (swallowed/silent)" % (v,), got=v)
check("R3", "TypeError on non-numeric", r3)

def r4():
    v = discount(100)
    if v != 100:
        raise A("discount(100) -> %r, expected 100 (boundary exclusive: over 100, not >=)" % (v,), got=v)
check("R4", "discount(100)==100 exclusive boundary", r4)

def r5():
    v = discount(101)
    if v != 90.9:
        raise A("discount(101) -> %r, expected 90.9 (rounding/precision)" % (v,), got=v)
check("R5", "discount(101)==90.9", r5)

def r6():
    try:
        v = discount(-1)
    except ValueError:
        return
    except Exception as e:
        raise A("negative raised %s, expected ValueError" % type(e).__name__)
    raise A("negative returned %r instead of raising ValueError (missing raise/check)" % (v,), got=v)
check("R6", "ValueError on negative", r6)

# Extra probes used only for diagnosis (never gated on their own).
def probe(name, fn):
    try:
        results.append({"id": name, "ok": True, "detail": "ok", "desc": name, "got": fn()})
    except Exception as e:
        results.append({"id": name, "ok": False, "detail": str(e), "desc": name, "got": None})

probe("probe_discount_50", lambda: discount(50))
probe("probe_discount_200", lambda: discount(200))
probe("probe_total_str", lambda: total(["1", "2"]))
print(json.dumps(results))
'''


def _run_behaviors(cwd: str) -> list[dict]:
    try:
        proc = subprocess.run(
            [sys.executable, "-c", BEHAVIOR_CHECKS],
            capture_output=True, text=True, cwd=cwd, timeout=30,
        )
    except Exception as e:
        return [{"id": "driver", "ok": False, "detail": str(e), "desc": "behavior driver", "got": None}]
    out = (proc.stdout or "").strip().splitlines()
    if not out:
        return [{"id": "driver", "ok": False, "detail": (proc.stderr or "")[-500:], "desc": "behavior driver", "got": None}]
    try:
        return json.loads(out[-1])
    except json.JSONDecodeError:
        return [{"id": "driver", "ok": False, "detail": out[-1], "desc": "behavior driver", "got": None}]


def _diagnose(rows: list[dict]) -> dict[str, str]:
    """Map requirement id -> rich finding text naming the actual defect."""
    by_id = {r.get("id"): r for r in rows}
    text = {}
    def ok(rid):
        r = by_id.get(rid) or {}
        return bool(r.get("ok"))

    if not ok("R1"):
        got = (by_id.get("R1") or {}).get("got")
        text["R1"] = (
            f"empty-list / off-by-one: total([]) returned {got!r} instead of 0. "
            "sum(items, 1) supplies a start value of 1, so the empty input returns 1 (default/sum-default bug)."
        )
    if not ok("R2"):
        got = (by_id.get("R2") or {}).get("got")
        extra = ""
        if not ok("R1"):
            extra = " Same off-by-one sum default corrupts every sum."
        text["R2"] = f"total([1,2,3]) returned {got!r} instead of 6.{extra}"
    if not ok("R3"):
        got = (by_id.get("R3") or {}).get("got")
        probe = (by_id.get("probe_total_str") or {}).get("got")
        if isinstance(got, str) or isinstance(probe, str):
            text["R3"] = (
                "type coercion / string concatenation: non-numeric input is concatenated "
                f"(total([1,'2',3]) -> {got!r}) instead of raising TypeError. Swallowed silently."
            )
        else:
            text["R3"] = (
                f"TypeError not raised: non-numeric input returned {got!r} instead of raising "
                "TypeError (swallow / except-typeerror / silently ignored)."
            )
    if not ok("R4"):
        got = (by_id.get("R4") or {}).get("got")
        if got is not None and got != 100:
            text["R4"] = (
                f"boundary inclusive / off-by-one: discount(100) returned {got!r} instead of 100. "
                "The threshold is exclusive (over 100), but the check used >= so exactly 100 is discounted."
            )
        else:
            text["R4"] = "boundary case at exactly 100 is wrong: discount(100) must return 100."
    if not ok("R5"):
        got = (by_id.get("R5") or {}).get("got")
        d50 = (by_id.get("probe_discount_50") or {}).get("got")
        d200 = (by_id.get("probe_discount_200") or {}).get("got")
        if d50 is not None and d50 != 50:
            text["R5"] = (
                f"condition inverted: discount is applied to small orders (discount(50) -> {d50!r}) "
                f"and not to large ones (discount(101) -> {got!r}, expected 90.9). "
                "The comparison direction is wrong."
            )
        elif isinstance(got, (int, float)) and float(got).is_integer() and got == 91:
            text["R5"] = (
                f"rounding/precision: discount(101) returned {got!r} (whole currency) instead of 90.9. "
                "Decimal truncation/round() lost the tenths."
            )
        else:
            text["R5"] = f"discount(101) returned {got!r}, expected 90.9 (above-threshold discount)."
    if not ok("R6"):
        got = (by_id.get("R6") or {}).get("got")
        text["R6"] = (
            f"ValueError not raised for negative: discount(-1) returned {got!r} instead of raising "
            "ValueError (missing raise/check)."
        )
    return text


def _coverage_gaps(test_files: list[Path]) -> list[str]:
    blob = ""
    for f in test_files:
        try:
            blob += f.read_text() + "\n"
        except OSError:
            continue
    return [r["id"] for r in REQUIREMENTS if not re.search(r["marker"], blob)]


def _find_test_files(cwd: str) -> list[Path]:
    root = Path(cwd)
    hits = list(root.glob("tests/test*.py")) + list(root.glob("test*.py"))
    return [p for p in hits if p.is_file()]


def _run_suite(cwd: str) -> tuple[int, str]:
    if not _find_test_files(cwd):
        return -1, "no tests"
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "tests/", "-q", "--tb=no"],
        capture_output=True, text=True, cwd=cwd, timeout=60,
    )
    return proc.returncode, (proc.stdout or "")[-400:]


def _forged_claims(cwd: str) -> dict:
    for name in (".autocode/builder-evidence.json", "builder-evidence.json"):
        p = Path(cwd) / name
        if p.is_file():
            try:
                return json.loads(p.read_text())
            except Exception:
                return {"_unparsed": str(p)}
    return {}


def _common(data: dict) -> dict:
    contract = data.get("goal_contract") or {"revision": 0, "hash": ""}
    return {
        "contract_revision": contract.get("revision", 0),
        "contract_hash": contract.get("hash", ""),
        "task_id": (data.get("current_task") or {}).get("id", ""),
        "deferred_backlog": ["Optional web UI"],
        "user_request": {
            "kind": "none", "discovered": "", "impact": "",
            "decision_needed": "", "options": [], "proposed_delta": "",
        },
    }


def validate(cwd: str | None = None, data: dict | None = None) -> dict:
    cwd = cwd or os.getcwd()
    data = data or {}
    mode = os.environ.get("AUTOCODE_FIXTURE_VALIDATION_MODE", "behavior")
    findings = []
    checks = []

    behaviors = _run_behaviors(cwd)
    gated = [r for r in behaviors if str(r.get("id", "")).startswith("R")]
    diagnosis = _diagnose(behaviors)
    failed_ids = [r["id"] for r in gated if not r.get("ok")]

    for row in gated:
        ok = bool(row.get("ok"))
        checks.append({
            "command": f"fixture:behavior:{row.get('id')}",
            "exit_code": 0 if ok else 1,
            "evidence_ref": f"event:behavior-{row.get('id')}",
        })

    for rid in failed_ids:
        row = next(r for r in gated if r.get("id") == rid)
        findings.append({
            "id": "",
            "severity": "high",
            "blocking": True,
            "finding": diagnosis.get(rid) or f"{row.get('desc')} failed: {row.get('detail')}",
            "evidence": f"event:behavior-{rid}",
            "reproduction_steps": [f"Run the behavior check for {rid}: {row.get('desc')}"],
            "expected": row.get("desc", ""),
            "actual": row.get("detail", ""),
            "why_it_matters": "Acceptance criterion is not met by the implementation",
            "suggested_correction": "Fix cart.py so the spec behavior holds",
        })

    test_files = _find_test_files(cwd)
    gaps = _coverage_gaps(test_files)
    suite_rc, _suite_out = _run_suite(cwd)
    checks.append({
        "command": "fixture:pytest:tests/",
        "exit_code": 0 if suite_rc == 0 else (1 if suite_rc > 0 else 2),
        "evidence_ref": "event:pytest",
    })

    if suite_rc == -1:
        findings.append({
            "id": "", "severity": "high", "blocking": True,
            "finding": (
                "No tests found — claims cannot be reproduced; missing tests / "
                "0 tests / evidence absent / unverified"
            ),
            "evidence": "event:pytest",
            "reproduction_steps": ["Look for tests/"],
            "expected": "A test suite proving the acceptance criteria",
            "actual": "not found",
            "why_it_matters": "Completion requires independently reproducible evidence, not builder claims",
            "suggested_correction": "Write behavioral tests for every acceptance criterion",
        })

    gap_findings = []
    for req in REQUIREMENTS:
        if req["id"] not in gaps:
            continue
        gap_findings.append({
            "id": "", "severity": "high", "blocking": mode in ("trace", "strict"),
            "finding": (
                f"Requirement {req['id']} unproven ({req['desc']}): "
                f"no test covers it — gap / missing coverage / not covered / unproven"
            ),
            "evidence": "event:pytest",
            "reproduction_steps": ["Inspect tests for a marker of this requirement"],
            "expected": f"A test exercising {req['desc']}",
            "actual": "absent from the suite",
            "why_it_matters": "Green tests that no longer prove a requirement are not completion evidence",
            "suggested_correction": f"Restore a test asserting {req['desc']}",
        })
    if mode in ("trace", "strict"):
        findings.extend(gap_findings)

    claims = _forged_claims(cwd)
    if claims:
        claimed_tests = (claims.get("claims") or {}).get("tests_passed")
        findings.append({
            "id": "", "severity": "high", "blocking": mode == "strict",
            "finding": (
                "Builder evidence claim is unsubstantiated / unverified — "
                f"claimed tests_passed={claimed_tests!r} cannot be reproduced; "
                "claims unsupported, evidence missing"
            ),
            "evidence": "event:pytest",
            "reproduction_steps": ["Re-run the suite and compare with the claimed evidence file"],
            "expected": "Independently reproduced test results",
            "actual": "claim only; not verified",
            "why_it_matters": "A Writer's self-assessment never authorizes completion",
            "suggested_correction": "Delete the forged claim and supply a real suite",
        })

    # Criterion roll-up MUST use only contract-approved IDs (C1, …). The
    # runner rejects reports whose criterion_results invent new IDs. Per-
    # requirement detail lives in findings, which the scorers grep.
    behavior_ok = not failed_ids
    if mode in ("trace", "strict"):
        rollup_ok = behavior_ok and not gaps and not claims
    else:
        rollup_ok = behavior_ok
    approved = []
    for c in data.get("acceptance_criteria") or []:
        cid = c.get("id") or "C1"
        approved.append(cid)
    if not approved:
        approved = ["C1"]
    criterion_results = [{
        "id": cid,
        "status": "PASS" if rollup_ok else "FAIL",
        "evidence_refs": [c["evidence_ref"] for c in checks],
    } for cid in approved]

    blocking = [f for f in findings if f.get("blocking")]
    verdict = "PASS" if not blocking else "FAIL"
    result = {
        "verdict": verdict,
        "checks_run": [c["command"] for c in checks],
        "findings": findings,
        "unverified_criteria": [c["id"] for c in criterion_results if c["status"] != "PASS"],
        "checks": checks,
        "criterion_results": criterion_results,
        "end_to_end_result": {
            "status": verdict,
            "summary": (
                "Independent cart validation: spec behaviors executed; "
                f"behavior_failures={failed_ids} coverage_gaps={gaps} mode={mode}"
            ),
            "evidence_refs": [c["evidence_ref"] for c in checks],
        },
        "finding_dispositions": [],
    }
    result.update(_common(data))
    return result


def _event_stream(session: str, result: dict) -> None:
    """Emit item.completed events whose item.id matches each evidence_ref.

    apply_review_result requires exactly one command_execution item per
    event:<id> evidence reference, so the ids must line up.
    """
    print(json.dumps({"type": "thread.started", "thread_id": session}))
    seen = set()
    for check in result.get("checks") or []:
        eid = (check.get("evidence_ref") or "check").split(":", 1)[-1]
        if eid in seen:
            continue
        seen.add(eid)
        print(json.dumps({
            "type": "item.completed",
            "item": {
                "id": eid,
                "type": "command_execution",
                "command": check.get("command", ""),
                "exit_code": check.get("exit_code", 1),
                "aggregated_output": "",
            },
        }))
    for finding in result.get("findings") or []:
        if finding.get("blocking"):
            print(json.dumps({
                "type": "item.completed",
                "item": {
                    "id": "finding-" + (finding.get("evidence") or "msg").split(":", 1)[-1],
                    "type": "agent_message",
                    "text": finding.get("finding", ""),
                },
            }))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 40, "output_tokens": 20}}))


def main() -> None:
    argv = sys.argv[1:]
    raw = sys.stdin.read()
    data = {}
    parts = raw.split("CURRENT HANDOFF DATA\n", 1)
    if len(parts) == 2:
        try:
            data = json.loads(parts[1])
        except Exception:
            data = {}
    session = argv[argv.index("resume") + 1] if "resume" in argv else str(uuid.uuid4())
    out_path = argv[argv.index("-o") + 1]
    result = validate(data=data)
    if data.get("stage") == "astra_checkpoint":
        passed = result["verdict"] == "PASS"
        result = {
            "validation": result,
            "consult_sol": {"requested": False, "question": "", "reason": ""},
            "decision": {
                "status": "COMPLETE" if passed else "REWORK",
                "acceptance_criteria": [
                    {**c, "status": "verified" if passed else "unverified",
                     "evidence": "Independent cart Validator executed spec behaviors; event:check"}
                    for c in data.get("acceptance_criteria") or []
                ],
                "next_objective": "" if passed else "Repair cart.py and restore requirement coverage",
                "findings": [] if passed else result["findings"],
                "evidence": ["event:check"],
                "plan": ["Implement and independently verify"],
                "affected_paths": ["cart.py"],
            },
        }
    _event_stream(session, result)
    Path(out_path).write_text(json.dumps(result))


if __name__ == "__main__":
    main()
