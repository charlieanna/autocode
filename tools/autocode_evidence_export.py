"""Owned-run publication and authenticated, read-only consumption of canonical reports.

Only record() writes state['evidence_export']. Status and TaskRun readers use
its digests and binding; nothing in this module reads another run's state.
"""
from pathlib import Path
import hashlib
import json
import os
import tempfile

try:
    from . import autocode_evidence_document as document, autocode_util as util
except ImportError:
    import autocode_evidence_document as document, autocode_util as util

COMPLETE = ("TASK_COMPLETE", "COMPLETE")


def _role_context(state):
    return {'settings': {'workflow': (state.get('settings') or {}).get('workflow') or {}}}


def binding(state, accounting):
    """Bind stable state and supplied document facts, never inspected liveness.

    Accounting may refresh explicit native logs. Bind the facts it supplies now,
    rather than claiming the saved report attests every current raw log byte.
    """
    # The anchor itself is excluded, so adding digests does not change the facts.
    stable = {key: state.get(key) for key in (
        "task_id", "run_dir", "status", "completed_at", "finished_at", "workflow", "goal_contract",
        "current_task", "criteria_revision", "acceptance_criteria", "validation", "final_decision",
        "last_decision", "human_reviews", "regression_proof", "turns", "settings", "stages",
        "review", "design_review", "answer", "created_at", "base_commit", "findings_ledger",
        "investigation", "active_stage", "orchestration_batch")}
    return util.digest({'state': stable,
                        'accounting': document.accounting_facts(accounting, role_context=_role_context(state))})


def unavailable(reason="No completed-run report is recorded"):
    return {"version": 1, "availability": "missing", "reasons": [reason], "binding": None,
            "json_path": None, "markdown_path": None, "json_sha256": None, "markdown_sha256": None}


def metadata(state):
    anchor = state.get("evidence_export")
    if not isinstance(anchor, dict):
        return unavailable()
    return {**anchor, "availability": "export_failed" if anchor.get("error") else "recorded",
            "reasons": [anchor["error"]] if anchor.get("error") else
                       ["Historical report; its files and current source have not been inspected"]}


def record(state, anchor):
    """Store the small authentication anchor, never another report's document."""
    state["evidence_export"] = {key: anchor.get(key) for key in (
        "version", "binding", "json_path", "markdown_path", "json_sha256", "markdown_sha256", "error")}


def _atomic_text(path, text):
    if path.is_symlink():
        raise ValueError("Report publication refuses a symlink")
    fd, name = tempfile.mkstemp(prefix=".evidence-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding='utf-8', newline='') as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def write(root, value):
    """Publish a canonical pair; the caller durably saves the returned anchor."""
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    value = document.sanitize(value)
    document.validate(value)
    markdown = document.render(value)
    body = json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n"
    if util.redact(body) != body:
        raise ValueError("Encoded canonical JSON is unsafe for publication")
    json_path, markdown_path = root / "evidence.json", root / "evidence.md"
    for path, text in ((json_path, body), (markdown_path, markdown)):
        if path.is_symlink():
            raise ValueError("Report publication refuses a symlink")
        if not path.is_file() or path.read_bytes() != text.encode('utf-8'):
            _atomic_text(path, text)
    directory = os.open(root, os.O_RDONLY)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    return {"version": 1, "binding": value["binding"], "json_path": str(json_path),
            "markdown_path": str(markdown_path), "json_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "markdown_sha256": hashlib.sha256(markdown.encode()).hexdigest(), "error": None}


def publish(root, state, view, provenance):
    if state.get("status") not in COMPLETE:
        return False
    previous = state.get("evidence_export")
    bound = None
    try:
        bound = binding(state, document.mapping(view.get('usage')).get('accounting'))
        value = document.build(view, run_identity=Path(root).name,
            completed_at=state.get("completed_at") or state.get("finished_at"),
            provenance=provenance, binding=bound, role_context=_role_context(state))
        anchor = write(root, value)
    except (OSError, ValueError, TypeError, KeyError) as error:
        anchor = {**unavailable(), "error": f"Evidence report export failed: {type(error).__name__}",
                  "binding": bound}
    record(state, anchor)
    return previous != state["evidence_export"]


def read(root, anchor, *, expected_binding=None, current=False, include=False):
    result = {**anchor, "availability": "recorded", "reasons": []}
    if anchor.get("error"):
        return {**result, "availability": "export_failed", "reasons": [anchor["error"]]}
    try:
        root = Path(root).resolve()
        json_path, markdown_path = root / "evidence.json", root / "evidence.md"
        captured = {}
        for key, path in (("json", json_path), ("markdown", markdown_path)):
            if path.is_symlink() or not path.is_file() or anchor.get(key + "_path") != str(path):
                raise ValueError("Missing, redirected or modified canonical evidence files")
            captured[key] = path.read_bytes()
            if hashlib.sha256(captured[key]).hexdigest() != anchor.get(key + '_sha256'):
                raise ValueError('Captured canonical evidence bytes differ from their anchor')
        body = captured['json'].decode('utf-8')
        if util.redact(body) != body:
            raise ValueError("Captured canonical JSON is unsafe for publication")
        value, markdown = json.loads(body), captured['markdown'].decode('utf-8')
        document.validate(value)
        if document.sanitize(value) != value or util.redact(markdown) != markdown:
            raise ValueError("Canonical evidence contains text unsafe for publication")
        if (value["binding"] != anchor.get("binding") or document.render(value) != markdown
                or any(path.is_symlink() or not path.is_file() for path in (json_path, markdown_path))
                or util.file_hash(json_path) != anchor["json_sha256"]
                or util.file_hash(markdown_path) != anchor["markdown_sha256"]):
            raise ValueError("Canonical evidence pair is inconsistent")
        if expected_binding is not None and expected_binding != value["binding"]:
            result.update(availability="stale", reasons=["The run's approved plan, evidence, accounting snapshot or completion changed"])
        elif current:
            result.update(availability="current", reasons=["Report files, completion and current source inspected"])
        else:
            result["reasons"] = ["Historical record; current completion has not been authenticated"]
        if include:
            result.update(document=value, markdown=markdown)
    except (OSError, ValueError, TypeError, KeyError):
        result.update(availability="invalid", reasons=["Missing, redirected, changed or invalid evidence report; revalidate/export from the owned run"])
    return result
