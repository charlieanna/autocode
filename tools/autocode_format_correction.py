"""One cheap same-session serialization correction before a full report repair.

A completed response rejected only because its final message is not JSON still
holds the whole answer; the model mis-serialized it. Before spending a
fresh-session report repair (whose handoff re-sends the rejected report and
checklists), resume the attempt's own session once and ask for the report
re-emitted as JSON alone: a few hundred prompt tokens instead of a full
stage round trip.

The correction does not consume a repair attempt. A correction that fails to
parse or validate flows through the normal rejection path, so the next attempt
is the unchanged full repair with its whole budget. Bounded to one correction
per rejection by the first-attempt gate; eligibility is narrow: the recorded
error exactly the format error, an OpenCode-events stage that ran in a session
thread, and a session-capable non-joint stage. A final message that was only a
shell command (autocode_cmd_only_report, issue #512) is the same kind of slip with
nothing to re-serialize: it gets the same one correction, with its own targeted
instruction, on any session-capable transport (OpenCode events or Codex) and for
planning stages too, whose Plan Reviewer is where it was seen. A session is resumed
only on the route that started it. Kilo restricted launches always start fresh, so
they skip this reminder and receive the complete report/schema repair handoff.
Runtime services are passed in; this module
imports nothing from the runner.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


from pathlib import Path

try:
    from . import autocode_cmd_only_report as cmd_only
    from . import autocode_leaked_markup_report as leaked_markup
    from . import autocode_providers as providers
    from . import autocode_support as support
    from .autocode_run_records import stage_completed, stage_supports_sessions
except ImportError:
    import autocode_cmd_only_report as cmd_only
    import autocode_leaked_markup_report as leaked_markup
    import autocode_providers as providers
    import autocode_support as support
    from autocode_run_records import stage_completed, stage_supports_sessions

FORMAT_ERROR = "OpenCode final message is not a JSON report"


def event_thread_id(jsonl: Path) -> str | None:
    for event in support.events(jsonl):
        if event.get("type") == "thread.started" and event.get("thread_id"):
            return str(event["thread_id"])
    return None


def session_for(state: RunState, pending, route_role=None) -> str | None:
    """The original attempt's session thread, when a format-only correction run on ``route_role``
    (the route the correction launches on; the original's own when not given) applies."""
    original = pending.get("original") or {}
    error = pending.get("error") or original.get("rejection_reason") or ""
    if pending.get("attempts") or pending.get("correction_attempted"):
        return None
    if cmd_only.matches(error) or leaked_markup.matches(error):
        if original.get("engine") not in ("opencode", "codex"):
            return None
    elif original.get("engine") != "opencode" or not error.startswith(FORMAT_ERROR) or original.get("planning"):
        return None
    if not stage_supports_sessions(state, original) or not same_route(state, original, route_role):
        return None
    settings = state.get("settings") or {}
    role = route_role or original.get("route_role") or original.get("role")
    route = (settings.get("roles") or {}).get(role) or {}
    provider = original.get("provider") or route.get("provider") or settings.get("provider")
    if original.get("engine") == "opencode" and provider and providers.kilo_connection(provider):
        # Every correction is read-only. The Kilo launch boundary strips its
        # session to prevent inherited tool grants, so a context-free reminder
        # cannot work. Leave the original report intact for the full repair.
        # A configured Kilo connection gets the same launch (#2010), so the same skip.
        return None
    return event_thread_id(Path(original["events"]))


def same_route(state: RunState, original, route_role=None) -> bool:
    """Whether the correction, run on ``route_role``'s saved route, would use the route (engine,
    provider, model, effort) that started the session. A Plan Reviewer attempt that ran on its one-use
    fallback (autocode_reviewer_fallback) recorded another role's route, which the correction never
    runs on. A record saved before routes were recorded is taken as unchanged."""
    launched = original.get("launch_route")
    if not isinstance(launched, dict):
        return True
    role = route_role or original.get("route_role") or original["role"]
    route = (state.get("settings", {}).get("roles") or {}).get(role)
    return isinstance(route, dict) and launched == {key: route.get(key) for key in launched}


def repair_session(state: RunState, pending, route_role=None, *, native=None) -> tuple[str | None, str | None]:
    """Return ``(session_id, None)`` to resume for a tools-off report repair, or ``(None, reason)``."""
    original = pending.get("original") or {}
    if native is not None or original.get("native_role_isolation"):
        return None, "native_role_isolation"
    settings = state.get("settings") or {}
    if settings.get("replay_recording"):
        try:
            from . import autocode_replay_transport as replay_transport
        except ImportError:
            import autocode_replay_transport as replay_transport
        tape = replay_transport.selected(settings)
        consumed = sum(isinstance(row.get("replay"), dict) for row in state.get("stages", []))
        dispatches = tape.document.get("dispatches") or []
        if not (0 <= consumed < len(dispatches)) or (dispatches[consumed].get("session") or {}).get("kind") != "resume":
            return None, "replay_fresh_dispatch"
    role = route_role or original.get("route_role") or original.get("role")
    route = (settings.get("roles") or {}).get(role) or {}
    provider = original.get("provider") or route.get("provider") or settings.get("provider")
    if original.get("engine") == "opencode" and provider and providers.kilo_connection(provider):
        return None, "kilo_restricted_stage"
    if original.get("engine") not in (None, "opencode", "codex") or not stage_supports_sessions(state, original):
        return None, "sessionless_route"
    if original.get("engine") == "opencode" and provider not in (None, "opencode"):
        try:
            adapter = providers.resolve(provider)
        except Exception:
            adapter = None
        if adapter is None or not getattr(adapter, "SUPPORTS_SESSIONS", False):
            return None, "sessionless_route"
        if not getattr(adapter, "SUPPORTS_REPORT_RECOVERY", False):
            return None, "provider_without_report_recovery"
    if not same_route(state, original, route_role):
        return None, "route_changed"
    latest = pending.get("latest_rejected")
    if pending.get("first_reply_budget") or original.get("first_reply_budget"):
        return None, "budget_stopped_attempt"
    if (
        original.get("truncated")
        or original.get("truncated_output")
        or (isinstance(latest, dict) and (latest.get("truncated") or latest.get("truncated_output")))
    ):
        return None, "truncated_attempt"
    if not stage_completed(state, original) or (isinstance(latest, dict) and not stage_completed(state, latest)):
        return None, "incomplete_attempt"
    if pending.get("correction_failed"):
        return None, "correction_failed"
    stages = state.get("stages", [])
    indices = [i for i, row in enumerate(stages) if row.get("events") == original.get("events")]
    if len(indices) == 1:
        for row in stages[indices[0] + 1 :]:
            if (
                row.get("report_only")
                and row.get("iteration") == original.get("iteration")
                and row.get("original_stage", str(row.get("stage", "")).removesuffix("_report_repair"))
                == original.get("stage")
                and (row.get("timed_out") or row.get("abandoned") or not stage_completed(state, row))
            ):
                return None, "timed_out_prior_repair"
    events_path = Path(original["events"]) if original.get("events") else None
    thread = event_thread_id(events_path) if events_path and events_path.is_file() else None
    if not thread:
        return None, "missing_session_thread"
    return thread, None


def record_repair_session(record, session: str | None, fresh_reason: str | None) -> None:
    """Record on the repair attempt record whether its provider session was resumed or started fresh."""
    if not isinstance(record, dict):
        return
    actual = record.get("expected_session") if "expected_session" in record else session
    if actual:
        record["repair_session_mode"] = "resumed"
        record.pop("repair_fresh_reason", None)
    else:
        record["repair_session_mode"] = "fresh"
        record["repair_fresh_reason"] = fresh_reason or record.get("fresh_session_reason") or "fresh_session"


def prompt(error: str) -> str:
    if cmd_only.matches(error):
        return cmd_only.CORRECTION
    if leaked_markup.matches(error):
        return leaked_markup.CORRECTION
    return (
        prompts.get("fragments/format-correction/prompt-02.md")
        + error[:200]
        + prompts.get("fragments/format-correction/prompt.md")
    )


def execute(runtime, state: RunState, run_dir, workspace) -> bool:
    """Run the one correction turn; True when it ran at all. Any failure falls back to the full repair."""
    pending = state.get("pending_report_repair") or {}
    original = pending.get("original") or {}
    role = original.get("role")
    # The route this correction launches on, which must be the one that started the session.
    route_role = runtime.planning.route_for(state, original["stage"], role) if role and original.get("stage") else None
    thread = session_for(state, pending, route_role)
    if not thread:
        return False
    pending["correction_attempted"] = True  # one shot per rejection, surviving restarts
    state.update({"phase": "REPORT_REPAIR"})
    runtime.write_json(run_dir / "state.json", state)
    try:
        value, record = runtime.run_role(
            role=role,
            prompt=prompt(pending.get("error") or ""),
            sandbox="read-only",
            workspace=workspace,
            run_dir=run_dir,
            state=state,
            schema=Path(original["schema"]),
            model=state["settings"]["roles"][route_role]["model"],
            allow_write=False,
            dry_run=False,
            report_only=True,
            resume_session=thread,
        )
    except BaseException:
        pending["correction_failed"] = True
        active = state.get("active_stage")
        if isinstance(active, dict) and active.get("report_only"):
            record_repair_session(active, thread, None)
        if (Path(run_dir) / "state.json").is_file():
            runtime.write_json(Path(run_dir) / "state.json", state)
        raise
    record_repair_session(record, thread, None)
    runtime.account_stage(state, record)
    runtime.accept_repaired_report(state, run_dir, workspace, value, record)
    return True
