"""Pure OpenCode event normalization shared by transport and native recovery."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..autocode_run_state import RunState

try:
    from .. import autocode_context_window as context_window
    from .. import autocode_output_cap as output_cap
except ImportError:
    import autocode_context_window as context_window
    import autocode_output_cap as output_cap


def _session_parts(rows, session):
    # Repeated updates replace a part's value without moving it past newer parts.
    parts = {}
    for row in rows:
        part = row.get("part", {})
        if row.get("sessionID") == session and part.get("id"):
            parts[part["id"]] = row
    return parts


def _command_exit(state: RunState):
    """Integer exit from a 1.x event, or from OpenCode 2's nested tool metadata."""
    metadata = state.get("metadata")
    if not isinstance(metadata, dict):
        return None
    if "exit" in metadata:
        code = metadata.get("exit")
        return code if type(code) is int else None
    nested = metadata.get("metadata")
    if isinstance(nested, dict) and type(nested.get("exit")) is int:
        return nested["exit"]
    return None


def normalized_events(rows):
    """Adapt real transport events; never interpret a model's prose as tool proof."""
    sessions = {r["sessionID"] for r in rows if isinstance(r.get("sessionID"), str)}
    if len(sessions) != 1:
        return [{"type": "turn.failed", "error": "Missing or mixed OpenCode sessions"}]
    session = next(iter(sessions))
    normalized = [{"type": "thread.started", "thread_id": session}]
    # Some versions repeat completed part updates; a part is still one operation.
    parts = _session_parts(rows, session)
    errors = [{"type": "error", "error": row.get("error")} for row in rows if row.get("type") == "error"]
    # The activity plugin saw Kilo compact (summarize) the conversation (autocode_context_window, #1942).
    compactions = [
        row for row in rows if row.get("type") == context_window.PLUGIN_ROW and row.get("sessionID") == session
    ]
    steps = []
    for row in parts.values():
        part = row["part"]
        if row.get("type") == "tool_use" and part.get("tool") in ("bash", "shell"):
            state = part.get("state", {})
            command = state.get("input", {}).get("command")
            code = _command_exit(state)
            if state.get("status") == "completed" and isinstance(command, str) and type(code) is int:
                normalized.append(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "command_execution",
                            "id": part["id"],
                            "command": command,
                            "exit_code": code,
                            "aggregated_output": state.get("output", ""),
                        },
                    }
                )
            elif (
                state.get("status") == "completed" and isinstance(command, str) and isinstance(state.get("output"), str)
            ):
                normalized.append(
                    {
                        "type": "item.completed",
                        "item": {
                            "type": "tool_output",
                            "id": part["id"],
                            "command": command,
                            "aggregated_output": state["output"],
                        },
                    }
                )
        elif row.get("type") == "step_finish":
            steps.append(part)
    normalized += errors
    if not steps:
        return normalized
    # Replayed finishes cannot close newer steps, even when the newer ID is missing.
    phase_types = ("step_start", "step_finish")
    phases = [row for row in parts.values() if row.get("type") in phase_types]
    terminal = (
        not any(not row.get("part", {}).get("id") for row in rows if row.get("type") in phase_types)
        and phases[-1].get("type") == "step_finish"
        and steps[-1].get("reason") in ("stop", "length", "tool-calls", "content-filter")
    )
    partial = not terminal or bool(errors and steps[-1].get("reason") == "stop")

    def total(field, subfield=None):
        containers = [p.get("tokens") for p in steps]
        values = [tokens.get(field) if isinstance(tokens, dict) else None for tokens in containers]
        if subfield:
            values = [v.get(subfield) if isinstance(v, dict) else None for v in values]
        known = [v for v in values if type(v) is int and v >= 0]
        return sum(known) if known and (partial or len(known) == len(values)) else None

    # OpenCode input excludes cache reads/writes; output excludes reasoning.
    input_parts = [total("input"), total("cache", "read"), total("cache", "write")]
    output_parts = [total("output"), total("reasoning")]
    usage = {
        "input_tokens": sum(input_parts) if all(v is not None for v in input_parts) else None,
        "cached_input_tokens": total("cache", "read"),
        "output_tokens": sum(output_parts) if all(v is not None for v in output_parts) else None,
        "reasoning_output_tokens": total("reasoning"),
    }
    usage = {k: v for k, v in usage.items() if v is not None}
    # A compacted conversation fails the turn however its last step finished: the response that
    # follows was produced from a summary, never from the whole handoff. The plugin's row is the
    # provider's own word; a request shrinking far below the previous one is the same signature.
    if compactions:
        flags = compactions[0].get("compaction") if isinstance(compactions[0].get("compaction"), dict) else {}
        summary = {"signal": "compaction_part", **{k: flags[k] for k in ("auto", "overflow") if k in flags}}
    else:
        # A shrinking last request is a signature only when nothing else failed, or the window itself
        # failed (Kilo refuses, summarizes and carries on); an unrelated failure proves nothing.
        summary = None if errors and context_window.overflow(rows) is None else context_window.shrank(rows)
    if summary is not None:
        normalized.append(
            {
                "type": "turn.failed",
                "usage": usage,
                "error": {
                    "code": context_window.COMPACTED_CODE,
                    "message": context_window.compaction_message(summary) + "; the response is not accepted",
                },
                "compaction": summary,
            }
        )
        return normalized
    if not terminal:
        # Completed-step consumption survives interruption, but is not terminal proof.
        normalized.append({"type": "usage.partial", "usage": usage})
        return normalized
    # A stream that ends on a "tool-calls" finish stopped mid-turn: the model asked
    # for tools and no later step followed (the process exited, for example after
    # every call was auto-rejected). Like "length", it is a failed turn whose
    # reported usage is retained for accounting, including cached input.
    # A final "content-filter" finish is the provider refusing the response, even
    # when no error event follows it: a failed turn typed content_filter, after any
    # provider error rows so their own name and words are reported first
    # (autocode_provider_refusal). Only the final step counts, as for tool-calls.
    if steps[-1].get("reason") == "length":
        # A successful process exit can still be an incomplete model turn.
        normalized.append(
            {
                "type": "turn.failed",
                "usage": usage,
                "error": {"code": "output_token_limit", "message": output_cap.length_stop(steps[-1].get("tokens"))},
            }
        )
    elif steps[-1].get("reason") == "tool-calls":
        normalized.append(
            {
                "type": "turn.failed",
                "usage": usage,
                "error": {
                    "code": "incomplete_turn",
                    "message": "OpenCode stopped after a step that requested tool calls, before the model "
                    "finished its turn (finish reason: tool-calls). The attempt is incomplete; "
                    "review saved work before recovery.",
                },
            }
        )
    elif steps[-1].get("reason") == "content-filter":
        normalized.append(
            {
                "type": "turn.failed",
                "usage": usage,
                "error": {
                    "code": "content_filter",
                    "message": "OpenCode's last step finished with reason content-filter",
                },
            }
        )
    elif not errors:
        normalized.append({"type": "turn.completed", "usage": usage})
    else:
        normalized.append({"type": "usage.partial", "usage": usage})
    return normalized
