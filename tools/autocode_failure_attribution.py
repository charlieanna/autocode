"""Evidence-bound investigation records; never a delivery or promotion gate.

This standalone stdlib CLI also lets the black-box scenario harness validate
the same record contract without importing AutoCode's runtime modules.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path


class AttributionError(ValueError):
    pass


def unknown():
    return {
        "evidence_refs": [],
        "suspected_cause": None,
        "confirmed_diagnosis": None,
        "regression": None,
        "confirmed_by": None,
    }


def _text(value, name, *, optional=False):
    if value is None and optional:
        return None
    if not isinstance(value, str) or not value.strip():
        raise AttributionError(name + " must be nonempty text")
    return value.strip()


def _json(content):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise AttributionError("duplicate JSON key: " + key)
            result[key] = value
        return result

    def invalid(value):
        raise AttributionError("invalid JSON number: " + value)

    return json.loads(content, object_pairs_hook=unique, parse_constant=invalid)


def read_json(path):
    return _json(Path(path).read_bytes())


def _artifact(reference, root):
    if not isinstance(reference, dict):
        raise AttributionError("evidence reference must be an object")
    name = reference.get("path")
    _text(name, "evidence path")
    relative = Path(name)
    if relative.is_absolute() or any(part in ("", ".", "..") for part in name.split("/")) or "\\" in name:
        raise AttributionError("evidence path must be literal and relative to the attempt")
    expected = reference.get("sha256")
    if not isinstance(expected, str) or re.fullmatch(r"[a-f0-9]{64}", expected) is None:
        raise AttributionError("evidence reference needs a SHA-256 pin")
    path = (root / relative).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise AttributionError("evidence reference leaves the attempt or is missing")
    # Read once: the parsed regression and its authenticated bytes are the same observation.
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != expected:
        raise AttributionError("evidence changed: " + name)
    return {"path": name, "sha256": expected}, content


def _regression_check(content, name, expected):
    report = _json(content)
    if not isinstance(report, dict) or report.get("error") or report.get("oracle_error"):
        raise AttributionError("regression must be a completed evaluator report without errors")
    command = _text(report.get("command"), "regression command")
    source = report.get("source_sha256")
    if not isinstance(source, str) or re.fullmatch(r"[a-f0-9]{64}", source) is None:
        raise AttributionError("regression must name its source SHA-256")
    checks = report.get("checks")
    if not isinstance(checks, list) or not checks:
        raise AttributionError("regression report has no checks")
    seen, matched = set(), False
    for check in checks:
        if not isinstance(check, dict) or type(check.get("ok")) is not bool:
            raise AttributionError("malformed regression check")
        ident = _text(check.get("name"), "regression check name")
        if ident in seen:
            raise AttributionError("duplicate regression check")
        seen.add(ident)
        if ident == name:
            matched = check["ok"] is expected
    if not matched:
        raise AttributionError("named regression must fail before the fix and pass afterward")
    return command, source


def validate(document, evidence_root, *, reviewer=None):
    """Retain an operator's attribution with verified file pins and regression contrast.

    The reviewer argument is supplied only by an explicit annotation action,
    never inferred from a model report or a scored model-diagnosis verdict.
    This records review authority; it does not execute or certify an explanation.
    """
    if not isinstance(document, dict):
        raise AttributionError("failure attribution must be an object")
    root = Path(evidence_root).resolve()
    result = unknown()
    refs = document.get("evidence_refs", [])
    if not isinstance(refs, list):
        raise AttributionError("evidence_refs must be a list")
    for reference in refs:
        pinned, _content = _artifact(reference, root)
        result["evidence_refs"].append({"handoff": _text(reference.get("handoff"), "observed handoff"), **pinned})
    result["suspected_cause"] = _text(document.get("suspected_cause"), "suspected cause", optional=True)
    diagnosis = _text(document.get("confirmed_diagnosis"), "confirmed diagnosis", optional=True)
    regression = document.get("regression")
    if diagnosis is None:
        if regression is not None or document.get("confirmed_by") is not None:
            raise AttributionError("unconfirmed suspicion cannot carry confirmation or regression authority")
        return result
    reviewer = _text(reviewer, "explicit reviewer")
    if not refs or not isinstance(regression, dict):
        raise AttributionError("confirmation requires handoff evidence and independent regression reports")
    check = _text(regression.get("check"), "regression check")
    before, old = _artifact(regression.get("before"), root)
    after, new = _artifact(regression.get("after"), root)
    if (root / before["path"]).resolve() == (root / after["path"]).resolve():
        raise AttributionError("before and after regression reports must be distinct")
    before_command, before_source = _regression_check(old, check, False)
    after_command, after_source = _regression_check(new, check, True)
    if before_command != after_command or before_source == after_source:
        raise AttributionError("regression must use the same command against distinct source versions")
    result.update(
        confirmed_diagnosis=diagnosis,
        confirmed_by=reviewer,
        regression={"check": check, "before": before, "after": after},
    )
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("report", type=Path)
    parser.add_argument("--evidence-root", type=Path, required=True)
    parser.add_argument("--reviewer")
    args = parser.parse_args(argv)
    try:
        result = validate(read_json(args.report), args.evidence_root, reviewer=args.reviewer)
    except (OSError, ValueError) as error:
        parser.exit(2, "attribution error: " + str(error) + "\n")
    print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
