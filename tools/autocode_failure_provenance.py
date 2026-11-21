"""Compare authenticated named failures without equating a test ID with its failure.

The original-test replay uses this only for failure evidence captured by the runner.
Unsupported or ambiguous reports supply no exemption. It does not decide whether
the remainder of a proof passes.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
import stat
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_check_outcome as check_outcome
    from . import autocode_command_receipt as command_receipt
    from . import autocode_go_tests as go_tests
    from . import autocode_junit as junit
    from . import autocode_package_scope as package_scope
    from . import autocode_python_test_roles as test_roles
    from . import autocode_test_setup as test_setup
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_check_outcome as check_outcome
    import autocode_command_receipt as command_receipt
    import autocode_go_tests as go_tests
    import autocode_junit as junit
    import autocode_package_scope as package_scope
    import autocode_python_test_roles as test_roles
    import autocode_test_setup as test_setup
    import autocode_verification_schedule as schedule


def _unittest_trace_paths(detail, root):
    """Normalize frame paths before the exception; preserve every exception byte."""
    lines = detail.splitlines(keepends=True)
    meaningful = [i for i, line in enumerate(lines) if line.strip() and set(line.strip()) != {"-"}]
    marker = "Traceback (most recent call last):"
    if not meaningful or lines[meaningful[0]].strip() != marker or sum(line.strip() == marker for line in lines) != 1:
        return detail
    for index in range(meaningful[0] + 1, len(lines)):
        line = lines[index]
        if line.strip() and not line[0].isspace():
            break  # Exception type and its entire message stay byte-for-byte exact.
        lines[index] = re.sub(r'^(\s*File ")' + root + r"(?=/)", r"\1<checkout>", line)
    return "".join(lines)


def _digest(kind, message, detail, *, execution_root=None, unittest=False):
    # Preserve display values: even a standard-looking pytest instance repr can
    # be supplied by user code and encode meaningful differences.
    text = detail
    if unittest and isinstance(execution_root, str) and execution_root != "/" and Path(execution_root).is_absolute():
        text = _unittest_trace_paths(text, re.escape(execution_root.rstrip("/")))
    return hashlib.sha256(json.dumps([kind, message, text], ensure_ascii=False).encode()).hexdigest()


def _selected(candidates, failures):
    matches = set(candidates) & failures
    return next(iter(matches)) if len(matches) == 1 else None


def _project(receipt):
    """Name the project only for a directly recognized, unambiguous Python runner."""
    command = receipt.get("command")
    if not isinstance(command, str) or any(char in command for char in "`$\n"):
        return None
    entered = package_scope.entered(command)
    project, inner = entered if entered else ("", command)
    go = go_tests.parse(inner)
    if go is not None:
        if go.prefix or any(
            arg == "-C" or arg.startswith("-C=") or arg == "-exec" or arg.startswith("-exec=") for arg in go.arguments
        ):
            return None
        return project
    try:
        lexer = shlex.shlex(inner, posix=True, punctuation_chars=";&|<>")
        lexer.whitespace_split = True
        lexer.commenters = ""
        words = list(lexer)
    except ValueError:
        return None
    if any(word and all(char in ";&|<>" for char in word) for word in words):
        return None
    if not words or not re.fullmatch(r"python(?:\d+(?:\.\d+)*)?", Path(words[0]).name):
        return None
    arguments = words[1:]
    while arguments and arguments[0] in ("-B", "-E", "-I", "-s", "-S"):
        arguments = arguments[1:]
    if len(arguments) < 2 or arguments[0] != "-m" or arguments[1] not in ("pytest", "unittest"):
        return None
    return project


def read_pinned(path, digest):
    """Parse only the same bounded bytes that match the runner's capture hash."""
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nofollow is None:
        raise ValueError("Platform cannot safely open a failure capture")
    descriptor = os.open(path, os.O_RDONLY | nofollow | getattr(os, "O_NONBLOCK", 0))
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > junit.MAX_REPORT_BYTES:
            raise ValueError("Failure capture is not a bounded regular file")
        data = stream.read(junit.MAX_REPORT_BYTES + 1)
    if len(data) > junit.MAX_REPORT_BYTES or hashlib.sha256(data).hexdigest() != digest:
        raise ValueError("Failure capture changed")
    return data


def _xml(receipt, failures):
    capture = receipt["results_capture"]
    data = read_pinned(capture["path"], capture["sha256"])
    if b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        return None
    root = ET.fromstring(data)
    if root.tag not in ("testsuite", "testsuites"):
        return None
    seen = {}
    for case in root.iter("testcase"):
        classname, name = case.get("classname", ""), case.get("name", "")
        test = _selected((f"{classname}::{name}", f"{classname}.{name}"), failures)
        if test is None:
            continue
        problems = [node for node in case if node.tag in ("failure", "error")]
        if len(problems) != 1 or test in seen:
            return None
        problem = problems[0]
        if not (problem.get("message") or "").strip() or not (problem.text or "").strip():
            return None
        seen[test] = _digest(
            problem.tag,
            problem.get("message", ""),
            problem.text or "",
            execution_root=receipt.get("execution_root"),
        )
    return seen if set(seen) == failures else None


def _unittest(receipt, failures):
    output = read_pinned(receipt["output"], receipt["output_sha256"]).decode(errors="replace")
    headers = list(test_setup.UNITTEST_FAILURE.finditer(output))
    details = list(test_setup.failure_details(output))
    if len(headers) != len(details):
        return None
    seen = {}
    for header, (name, owner, detail) in zip(headers, details, strict=True):
        test = _selected((f"{owner}::{name}", f"{owner}.{name}", owner), failures)
        if test is None:
            continue
        if test in seen:
            return None
        if not re.search(r"(?m)^[A-Za-z_][\w.]*\s*(?::.*)?$", detail):
            return None
        seen[test] = _digest(
            header.group(0).partition(":")[0], "", detail, execution_root=receipt.get("execution_root"), unittest=True
        )
    return seen if set(seen) == failures else None


def _go(receipt, failures):
    text = read_pinned(receipt["output"], receipt["output_sha256"]).decode(errors="replace")
    rows = {name: {"run": 0, "fail": 0, "output": []} for name in failures}
    for line in text.splitlines():
        if not line.startswith("{"):
            continue
        event = json.loads(line)
        if not isinstance(event, dict) or not isinstance(event.get("Test"), str):
            continue
        name = f"{event.get('Package') or ''}::{event['Test']}"
        if name not in rows:
            continue
        action = event.get("Action")
        if action in ("run", "fail"):
            rows[name][action] += 1
        elif action == "output" and isinstance(event.get("Output"), str):
            rows[name]["output"].append(event["Output"])
    seen = {}
    for name, row in rows.items():
        output = row["output"]
        test = name.partition("::")[2]
        if (
            row["run"] != 1
            or row["fail"] != 1
            or len(output) < 3
            or output[0] != f"=== RUN   {test}\n"
            or not re.fullmatch(r"--- FAIL: " + re.escape(test) + r" \([0-9.]+s\)\n", output[-1])
        ):
            return None
        detail = "".join(output[1:-1])
        if not detail.strip():
            return None
        seen[name] = _digest("go-fail", "", detail, execution_root=receipt.get("execution_root"))
    return seen


def observations(receipt, *, root):
    """Return each named failure's exact detail fingerprint, or None when unproven."""
    try:
        parts = receipt.get("suites")
        if isinstance(parts, list) and parts:
            merged = {}
            for part in parts:
                results = part.get("results") or {}
                if (
                    part.get("timed_out")
                    or part.get("error")
                    or not schedule.intact(part, root=root)
                    or not schedule.complete_results(part)
                    or results.get("skipped")
                ):
                    return None
                if part.get("exit_code") == 0 and not results.get("failed"):
                    continue
                found = observations(part, root=root)
                if not found or set(merged) & set(found):
                    return None
                merged.update(found)
            return merged or None
        results = receipt.get("results") or {}
        failures = set(results.get("failed") or ())
        if (
            not schedule.intact(receipt, root=root)
            or not schedule.complete_results(receipt)
            or not failures
            or receipt.get("timed_out")
            or receipt.get("error")
            or results.get("collection_errors")
            or results.get("skipped")
            or receipt.get("exit_code") != 1
        ):
            return None
        if "results_capture" in receipt:
            return _xml(receipt, failures)
        if go_tests.parse(package_scope.within(receipt.get("command"))) is not None:
            return _go(receipt, failures)
        return _unittest(receipt, failures)
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ET.ParseError, RecursionError, OverflowError):
        return None


def baseline(state: RunState, revision):
    """The current proof's identical base/candidate failures, or None without authority."""
    try:
        run_dir = Path(state["run_dir"])
        proof = state.get("regression_proof") or {}
        path = Path(proof["path"])
        binding = (state.get("settings", {}).get("protected_tests") or {}).get("binding_hash")
        if (
            proof.get("verdict") != "PASS"
            or proof.get("source_revision") != revision
            or not test_roles.proof_current(proof, binding)
            or command_receipt.owned_relative(path, run_dir / "regression") is None
            or path.is_symlink()
            or path.stat().st_nlink != 1
        ):
            return None
        data = json.loads(read_pinned(path, proof.get("receipt_sha256")))
        checks = data.get("checks") or {}
        if (
            data.get("verdict") != "PASS"
            or data.get("source_revision") != revision
            or not schedule.proof_intact(checks, root=path.parent)
        ):
            return None
        before = observations(checks["suite_on_base"], root=path.parent)
        after = observations(checks["suite_on_candidate"], root=path.parent)
        project = _project(checks["suite_on_base"])
        if before is None or after is None or project is None or project != _project(checks["suite_on_candidate"]):
            return None
        failures = {name: fingerprint for name, fingerprint in before.items() if after.get(name) == fingerprint}
        return {"project": project, "failures": failures} if failures else None
    except (OSError, ValueError, TypeError, KeyError, AttributeError, RecursionError, OverflowError):
        return None


def matches(receipt, known, *, root):
    """The failure IDs when every observed failure is identical to the base, else None."""
    observed = observations(receipt, root=root)
    if (
        not known
        or _project(receipt) != known.get("project")
        or not observed
        or any(known["failures"].get(name) != value for name, value in observed.items())
    ):
        return None
    return sorted(observed)


def adjudicate(receipt, known, *, root):
    """Remove proven baseline failures while retaining each other part's actual outcome."""
    parts = receipt.get("suites") if isinstance(receipt, dict) else None
    if isinstance(parts, list) and parts:
        checked = [adjudicate(part, known, root=root) for part in parts]
        accepted = sorted({name for _outcome, names in checked for name in names})
        for wanted in (check_outcome.FAILED, check_outcome.UNDETERMINED):
            for index, (outcome, _names) in enumerate(checked, 1):
                if outcome["outcome"] == wanted:
                    if wanted == check_outcome.UNDETERMINED:
                        outcome = {**outcome, "detail": f"{outcome['detail']} (in part {index} of {len(parts)})"}
                    return outcome, accepted
        return {"outcome": check_outcome.PASSED, "reason": "preexisting", "detail": ""}, accepted
    outcome = check_outcome.classify(receipt)
    if outcome["outcome"] == check_outcome.FAILED:
        names = matches(receipt, known, root=root)
        if names:
            return {"outcome": check_outcome.PASSED, "reason": "preexisting", "detail": ""}, names
    elif outcome["outcome"] == check_outcome.PASSED:
        results = receipt.get("results") or {}
        if not schedule.intact(receipt, root=root) or not schedule.complete_results(receipt) or results.get("skipped"):
            return {
                "outcome": check_outcome.UNDETERMINED,
                "reason": "incomplete_receipt",
                "detail": "passing results were not fully authenticated",
            }, []
    return outcome, []
