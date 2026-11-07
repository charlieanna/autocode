"""Conservative, runner-derived patch attribution for review findings only.

This policy never applies to task acceptance criteria. Unsupported evidence
retains the blocker; only complete equivalent Python failures can exempt it.
"""

import re
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    from . import autocode_python_tests as python_tests
    from . import autocode_test_setup as test_setup
except ImportError:
    import autocode_python_tests as python_tests
    import autocode_test_setup as test_setup

RUNNER = "patch-comparison-v1"


def pre_existing(finding):
    """Only the runner's fully bound disposition changes blocking semantics."""
    row = finding.get("disposition") or {}
    return (
        isinstance(row, dict)
        and row.get("runner") == RUNNER
        and row.get("kind") == "pre_existing"
        and bool(row.get("reason"))
        and all(
            isinstance(row.get(key), dict)
            and bool(row[key].get("path"))
            and bool(re.fullmatch(r"[a-f0-9]{64}", str(row[key].get("sha256", ""))))
            for key in ("base_proof", "changed_proof", "inputs")
        )
    )


def blocking(finding):
    return finding.get("severity") == "blocking" and not pre_existing(finding)


def label(finding):
    if pre_existing(finding):
        return "pre-existing observation"
    row = finding.get("disposition") or {}
    if isinstance(row, dict) and row.get("kind") == "inconclusive":
        return "blocking; attribution inconclusive"
    return str(finding.get("severity", "advisory"))


def complete(run):
    result = run.get("results") or {}
    groups = [result.get(key) for key in ("passed", "failed", "skipped")]
    if (
        run.get("error")
        or run.get("timed_out")
        or run.get("interrupted")
        or type(run.get("exit_code")) is not int
        or run["exit_code"] not in (0, 1)
        or result.get("complete") is not True
        or any(not isinstance(group, list) for group in groups)
        or any(result.get(key) for key in ("collection_errors", "uncollected", "setup_errors"))
    ):
        return False
    ids = [name for group in groups for name in group]
    return (
        all(isinstance(name, str) and name for name in ids)
        and type(result.get("total")) is int
        and result["total"] > 0
        and len(ids) == len(set(ids)) == result["total"]
        and bool(result["failed"]) == (run["exit_code"] == 1)
    )


def preparation_errors(run, xml):
    """Identify framework setup/teardown failures, which prove no finding."""
    invocation = python_tests.parse(run.get("command"))
    if invocation is None:
        return {}
    found = {}
    if invocation.kind == "pytest":
        for case in ET.parse(xml).iter("testcase"):
            if case.find("error") is not None:
                found[f"{case.get('classname', '')}::{case.get('name', '')}"] = (
                    "pytest setup, teardown or collection error"
                )
    else:
        text = Path(run["output"]).read_text(errors="strict")
        for name, owner, body in test_setup.failure_details(text):
            if name in python_tests.UNITTEST_FIXTURES or any(
                function in {"setUp", "tearDown", *python_tests.UNITTEST_FIXTURES}
                for _, function in test_setup.PYTHON_FRAME.findall(body)
            ):
                found[python_tests._unittest_id(name, owner)] = "unittest setup or teardown error"
    return found


def failure_details(run, root, xml):
    """Full Python diagnostics, never log tails; retain line numbers and values.

    Equivalence deliberately includes the entire traceback, failure site and
    diagnostic. Only the known scratch root differs. Ambiguous/duplicate or
    unsupported details supply no equivalence evidence.
    """
    invocation = python_tests.parse(run.get("command"))
    if invocation is None:
        return {}
    details = {}
    if invocation.kind == "unittest":
        text = Path(run["output"]).read_text(errors="strict")
        rows = [
            (python_tests._unittest_id(name, owner), body) for name, owner, body in test_setup.failure_details(text)
        ]
    else:
        rows = []
        tree = ET.parse(xml)
        for case in tree.iter("testcase"):
            problem = case.find("failure")
            if problem is not None:
                rows.append((f"{case.get('classname', '')}::{case.get('name', '')}", problem.text or ""))
    duplicated = set()
    for name, body in rows:
        if name in details:
            duplicated.add(name)
        body = body.replace(str(root), "<review-source>")
        body = re.sub(r"\n={70}\s*$", "", body).strip()
        if body.startswith("-" * 70 + "\n"):
            body = body[71:]
        if invocation.kind == "unittest":
            supported = (
                body.startswith("Traceback (most recent call last):")
                and bool(test_setup.PYTHON_FRAME.search(body))
                and bool(re.search(r"^\w+(?:\.\w+)*(?:Error|Exception|Failure):", body, re.M))
            )
        else:
            supported = bool(re.search(r"^.+\.py:\d+: (?:\w+\.)*\w+(?:Error|Exception|Failure)\b", body, re.M))
        if supported and "During handling of the above exception" not in body and "direct cause" not in body:
            details[name] = {"format": invocation.kind, "traceback": body}
    return {name: value for name, value in details.items() if name not in duplicated}


def unchanged_location(finding, locations):
    """Only known unchanged cited source may support a pre-existing exemption."""
    if not isinstance(locations, dict) or not isinstance(finding, dict):
        return False
    path, lines = finding.get("file"), finding.get("lines")
    if (
        not isinstance(path, str)
        or not path
        or not isinstance(lines, list)
        or len(lines) not in (1, 2)
        or any(type(line) is not int or line < 1 for line in lines)
        or lines[0] > lines[-1]
    ):
        return False
    location = locations.get(path)
    if (
        not isinstance(location, dict)
        or type(location.get("line_count")) is not int
        or lines[-1] > location["line_count"]
        or not isinstance(location.get("changed"), list)
    ):
        return False
    for span in location["changed"]:
        if (
            not isinstance(span, list)
            or len(span) != 2
            or any(type(line) is not int or line < 1 for line in span)
            or span[0] > span[1]
            or not (lines[-1] < span[0] or lines[0] > span[1])
        ):
            return False
    return True


def classify(matched, changed, base, *, inputs=None, reason="", finding=None, changed_locations=None):
    """A demonstrated regression wins; otherwise every failing probe must match."""
    row = {"runner": RUNNER, "kind": "inconclusive", "reason": reason or "Baseline attribution is unavailable."}
    row.update(inputs=inputs, base_proof=base.get("proof"), changed_proof=changed.get("proof"))
    if not complete(changed) or not complete(base):
        row["reason"] = reason or "Base or changed proof is incomplete, unavailable, or failed during test preparation."
        return row
    passed = set(base["results"]["passed"])
    if any(name in passed for name in matched):
        return {**row, "kind": "introduced", "reason": "A finding test passes on base and fails on the changed code."}
    if not unchanged_location(finding, changed_locations):
        row["reason"] = "The finding cites changed source or its unchanged location is unverified; blocker retained."
        return row
    base_failed = set(base["results"]["failed"])
    old, new = base.get("failure_details", {}), changed.get("failure_details", {})
    if (
        inputs
        and base.get("proof")
        and changed.get("proof")
        and all(name in base_failed and old.get(name) and old[name] == new.get(name) for name in matched)
    ):
        return {
            **row,
            "kind": "pre_existing",
            "reason": "All failing finding tests have equivalent full Python failure sites and diagnostics on base and change.",
        }
    row["reason"] = (
        "Finding tests have different, missing, skipped, or unsupported baseline failure evidence; blocker retained."
    )
    return row
