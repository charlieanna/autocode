"""Execute an explicit test command with fresh, captured JUnit evidence.

The injected executor owns process admission, cleanup and collected exits. XML
report problems stay separate from those process errors: missing reports can be
observed, but never establish a pass or authorize a first-project exception.
"""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

try:
    from . import autocode_junit as junit
    from .autocode_test_framework import Framework
except ImportError:
    import autocode_junit as junit
    from autocode_test_framework import Framework


class JUnitFramework(Framework):
    """An operator-selected command and its one explicit XML report path."""

    def __init__(self, command, spec):
        if not isinstance(command, str) or not command.strip():
            raise ValueError("JUnit proof requires an explicit test command")
        super().__init__("junit", command)
        self.test_report = junit.normalize(spec)

    @property
    def per_test(self):
        return True

    def targeted(self, test_paths):
        # A generic report format cannot derive Maven/Gradle/Cargo selectors.
        # Reusing the exact trusted suite preserves its collection and setup.
        return self.suite if tuple(test_paths) else None

    def to_dict(self):
        return dict(super().to_dict(), test_report=self.test_report)


def _unexecuted(command, log, error):
    message = f"JUnit report preparation refused execution: {error}"
    data = (message + "\n").encode("utf-8")
    with log.open("xb") as stream:
        stream.write(data)
    return {
        "command": command,
        "exit_code": None,
        "timed_out": False,
        "duration_seconds": 0.0,
        "executed": False,
        "error": message,
        "output": str(log),
        "output_sha256": hashlib.sha256(data).hexdigest(),
        "tail": message,
        "results": None,
        "results_expected": True,
        "results_format": "junit",
        "report_status": "invalid",
        "report_error": message,
    }


def run_suite(framework, command, tree, evidence_dir, label, *, timeout, execute, is_tracked):
    """Prepare, execute and capture without altering the trusted command.

    ``execute(command, tree, log_path, *, timeout)`` returns its owned command
    receipt. ``is_tracked(relative_path)`` supplies the caller's Git inventory
    for this tree. The caller owns an existing evidence directory. Ownership
    exceptions from execution propagate; a report cannot replace that boundary.
    """
    if not isinstance(label, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", label) or label in (".", ".."):
        raise ValueError("JUnit evidence label must be a contained filename stem")
    evidence_dir = Path(evidence_dir)
    if evidence_dir.is_symlink() or not evidence_dir.is_dir():
        raise ValueError("JUnit evidence directory must be an existing owned directory")
    evidence_dir = evidence_dir.resolve()
    stem = label + "." + uuid.uuid4().hex
    log = evidence_dir / (stem + ".log")
    artifact = evidence_dir / (stem + ".junit.xml")
    tree = Path(tree)
    try:
        spec = junit.normalize(framework.test_report)
        relative = spec.removeprefix("junit:")
        tracked = is_tracked(relative)
        if type(tracked) is not bool:
            raise ValueError("JUnit tracked-output inventory must establish a boolean result")
        junit.prepare(tree, spec, tracked=tracked)
    except (OSError, ValueError, TypeError, RuntimeError) as error:
        return _unexecuted(command, log, error)
    receipt = dict(execute(command, tree, log, timeout=timeout))
    receipt.update(results=None, results_expected=True, results_format="junit")
    # Never carry a previous result/report through a callback that reused a dict.
    receipt.pop("test_report", None)
    receipt.pop("report_error", None)
    receipt.pop("report_status", None)
    try:
        metadata, parsed = junit.capture(tree, spec, artifact)
    except (OSError, ValueError) as error:
        receipt["report_status"] = "missing" if isinstance(error, junit.MissingReport) else "invalid"
        receipt["report_error"] = str(error)
    else:
        receipt.update(test_report=metadata, results=parsed, report_status="captured")
    return receipt
