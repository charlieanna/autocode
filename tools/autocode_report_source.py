"""Read and describe saved report-repair inputs without changing evidence."""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


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
