"""Read and describe saved report-repair inputs without changing evidence."""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


import copy
import json
from pathlib import Path

try:
    from . import autocode_cmd_only_report as cmd_only
    from . import autocode_opencode as opencode
    from . import autocode_support as support
    from .autocode_util import read as read_json
    from .providers import claude as claude_provider
except ImportError:
    import autocode_cmd_only_report as cmd_only
    import autocode_opencode as opencode
    import autocode_support as support
    from autocode_util import read as read_json
    from providers import claude as claude_provider

REPAIR_REPORT_BYTES = 128 * 1024
TRUNCATED_REVIEW_STAGES = ("sol", "astra_review", "astra_checkpoint", "astra_challenge", "astra_finalize")
# Saved repair inputs never shrink, so a repair refused for them cannot launch on a later resume;
# an explicit resume archives it and starts a fresh attempt of the stage (autocode_stage_recovery).
REPAIR_INPUT_ROUTE = (
    " Resume with autocode resume to archive this repair (evidence retained) and start a fresh attempt of the stage."
)


def _event_facade(record):
    """The facade whose event log holds this stage's report; None when its saved output is the report (#717)."""
    if record.get("engine") != "opencode":
        return None
    mode = record.get("output_mode", "opencode_events")  # records older than output_mode were OpenCode
    if mode == "opencode_events":
        return opencode
    if mode == "codex_events" and record.get("provider") == "claude":
        return claude_provider
    return None


def completed_provider_report(record, *, opencode_report, read_object, persist):
    """Extract provider output using the caller's original report and persistence callbacks."""
    if record.get("engine") == "opencode":
        # Raw provider events are authoritative, including during recovery.
        if record.get("output_mode") != "report_file":
            record["response_text"] = str(Path(record["output"]).with_suffix(".response.txt"))
        value = opencode_report(
            record["events"],
            recover_wrapped=bool(record.get("report_only")),
            response_path=None if record.get("output_mode") == "report_file" else record["response_text"],
        )
        # Persist rejected reports too, so archival never leaves a missing repair input.
        if record.get("output_mode") != "report_file":
            persist(Path(record["output"]), value)
    else:
        value = read_object(Path(record["output"]))
    return value


def original_report_for_repair(original):
    """Expose terminal output directly, including reports rejected before saving.

    OpenCode's schema-invalid JSON may exist only in a long raw event line.
    Read tools cannot reliably recover those lines. Extract the same terminal
    report as admission does, without validating, accepting or altering it.
    A configured tool's saved report file, or the claude provider's event log, is read the same way.
    """
    try:
        facade = _event_facade(original)
        if facade is not None:
            value = facade.final_report(original["events"])
        else:
            value = read_json(Path(original["output"]))
        return {"report": value, "extraction_error": None}
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        return {"report": None, "extraction_error": str(error)}


def _write_json(path, value):
    support.atomic_json(Path(path), value)


def repair_report_source(record):
    """Return a bounded report artifact or an explicitly incomplete transcript."""
    output = Path(record["output"])
    response = Path(record.get("response_text") or output.with_suffix(".response.txt"))
    if record.get("truncated_output"):
        event_path = Path(record["events"])
        facade = _event_facade(record)
        partial = facade.incomplete_response(event_path) if facade is not None else None
        return {
            "path": str(event_path),
            "sha256": support.file_hash(event_path),
            "format": "truncated_provider_response",
            "bytes": event_path.stat().st_size,
            "truncated": True,
            "content": {
                "complete_report": False,
                "diagnostic": support.terminal_failure_reason(event_path),
                "partial_text": partial,
            },
        }
    facade = _event_facade(record)
    if not output.is_file() and facade is not None:
        # Persisted pre-fix checkpoints may have no report file. Their pinned
        # events can be extracted locally without asking a model to search JSONL.
        try:
            value = facade.final_report(
                record["events"], recover_wrapped=bool(record.get("report_only")), response_path=response
            )
        except RuntimeError:
            if not response.is_file():
                raise support.Paused(
                    "PAUSED_REPORT_REPAIR_INPUT",
                    "No completed response is available for report repair." + REPAIR_INPUT_ROUTE,
                ) from None
        else:
            _write_json(output, value)
        record["response_text"] = str(response)
    path = output if output.is_file() else response
    if not path.is_file() or path.stat().st_size > REPAIR_REPORT_BYTES:
        raise support.Paused(
            "PAUSED_REPORT_REPAIR_INPUT",
            f"Repair report is missing or exceeds {REPAIR_REPORT_BYTES} bytes: {path}; "
            "inspect the saved artifact instead of truncating or reconstructing it." + REPAIR_INPUT_ROUTE,
        )
    text = path.read_text()
    if not text.strip():
        raise support.Paused("PAUSED_REPORT_REPAIR_INPUT", f"Repair report is empty: {path}." + REPAIR_INPUT_ROUTE)
    parse_error = None
    try:
        content, format_ = json.loads(text), "json"
    except json.JSONDecodeError as error:
        content, format_ = text, "text"
        # Keep the decoder's location beside the exact pinned text. The generic
        # provider failure remains unchanged; these are diagnostics, not a repair
        # or authority to accept/drop any part of the rejected report (#2359).
        parse_error = {
            "message": error.msg,
            "position": error.pos,
            "line": error.lineno,
            "column": error.colno,
        }
    except ValueError:
        content, format_ = text, "text"
    return {
        "path": str(path),
        "sha256": support.file_hash(path),
        "format": format_,
        **({"parse_error": parse_error} if parse_error is not None else {}),
        "bytes": len(text.encode("utf-8")),
        "truncated": False,
        "content": content,
    }


def valid_truncated_report_attempt(record, stage_completed):
    """Accept only a failed, length-limited transcript with no completion event."""
    stage = record.get("stage")
    if record.get("report_only"):
        owner = record.get("original_stage")
        if stage != str(owner) + "_report_repair":
            return False
        stage = owner
    elif record.get("original_stage") not in (None, stage):
        return False
    events = support.events(record.get("events", ""))
    return bool(
        stage in TRUNCATED_REVIEW_STAGES
        and record.get("truncated_output")
        and any(
            event.get("type") == "turn.failed"
            and isinstance(event.get("error"), dict)
            and event["error"].get("code") == "output_token_limit"
            for event in events
        )
        and not any(event.get("type") == "turn.completed" for event in events)
        and not stage_completed
    )


def recovered_timeout_attempt(record, stage_completed):
    """A repair the runner archived after a nonterminal timeout: it produced no report to pair an error with."""
    return bool(
        record.get("timed_out") and record.get("abandoned") and record.get("automatic_recovery") and not stage_completed
    )


def repair_report_instruction(pending):
    """State the stricter source-only rule when the original output was cut off, and that a
    shell command returned as the report holds nothing to repair (autocode_cmd_only_report)."""
    if cmd_only.matches(pending.get("error")):
        return cmd_only.REPAIR_INSTRUCTION
    truncated = bool(
        (pending.get("original") or {}).get("truncated_output")
        or (pending.get("latest_rejected") or {}).get("truncated_output")
    )
    if truncated:
        return prompts.get("fragments/report-source/repair-report-instruction-02.md")
    return prompts.get("fragments/report-source/repair-report-instruction.md")


def load_stage_report(runtime, record, workspace=None, evidence_record=None, state=None):
    """Validate provider output, retaining raw bytes before hydrating review IDs."""
    runtime.readonly_events.assert_unchanged_review(record, workspace=workspace)
    if evidence_record:
        runtime.readonly_events.assert_unchanged_review(evidence_record, workspace=workspace)
    runtime.rework_policy.verify_existing(record)
    value = runtime.completed_provider_report(
        record,
        opencode_report=runtime.role_transport.report_reader(state, record, runtime.opencode, workspace),
        read_object=runtime.util.read_object,
        persist=runtime.write_json,
    )
    reported = copy.deepcopy(runtime.cmd_only.refuse(value, record.get("schema")))  # never a report, never run (#512)
    value = runtime.report_edits.expand(value, record)
    value = (
        runtime.planning.fill_trace_id(state, str(record.get("stage", "")).removesuffix("_report_repair"), value)
        if state
        else value
    )
    value = runtime.default_missing_provenance(
        runtime.normalize_plan_challenge_blocking(
            runtime.planning_metadata.normalize_planning_metadata(value, state, record), record
        ),
        record,
    )
    evidence_record = evidence_record or record
    validation = value.get("validation", value)
    checks = validation.get("checks") if isinstance(validation, dict) else None
    if isinstance(checks, list) and (
        (record.get("stage") == "sol" and workspace is not None)
        or any(isinstance(check, dict) and None in (check.get("exit_code"), check.get("command")) for check in checks)
    ):
        if workspace is None:
            raise ValueError("Cannot derive check metadata without the validation workspace")
        runtime.support.verify_checks(
            checks, workspace, evidence_record["events"], **runtime.check_evidence_options(evidence_record)
        )
    # A Tester's extra citation beside a verified check costs a repair; the raw report stays as .reported.json.
    runtime.check_refs.tidy_tester_citations(record, validation, workspace, (state or {}).get("settings"))
    value = runtime.report_identity.adopt(
        value, record, state
    )  # a mistyped echo on a bound launch is no repair (#2135)
    schema = runtime.support.review_validation_schema(
        runtime.report_edits.validation_schema(runtime.read_json(Path(record["schema"]))), state, record, value
    )
    value = runtime.support.normalize_loaded_report(value, schema, record)
    # finding_dispositions may be present in reports validated against schemas
    # saved before the field was introduced. Strip it before validation rather
    # than rejecting a correct report.
    if "finding_dispositions" not in schema.get("properties", {}) and "finding_dispositions" in value:
        stripped = {k: v for k, v in value.items() if k != "finding_dispositions"}
        runtime.support.validate_schema(stripped, schema)
    else:
        runtime.support.validate_schema(value, schema)
    value = runtime.support.hydrate_review_report(value, state, record)
    if value != reported:
        original = Path(record["output"]).with_suffix(".reported.json")
        if original.exists():
            if runtime.read_json(original) != reported:
                raise ValueError("Preserved raw report differs; reconcile before normalization")
        else:
            runtime.write_json(original, reported)
        record["reported_output"] = str(original)
        reported_checks = reported.get("validation", reported).get("checks") or []
        record["derived_check_metadata"] = runtime.support.derived_check_fields(
            checks or [], reported_checks, evidence_record.get("events")
        )
    if (record.get("engine") == "opencode" and record.get("output_mode") != "report_file") or value != reported:
        runtime.write_json(Path(record["output"]), value)
    return runtime.rework_policy.capture(record, value) if workspace is not None else value
