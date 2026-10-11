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
only on the route that started it. Runtime services are passed in; this module
imports nothing from the runner.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts


from pathlib import Path

try:
    from . import autocode_cmd_only_report as cmd_only
    from . import autocode_support as support
    from .autocode_run_records import stage_supports_sessions
except ImportError:
    import autocode_cmd_only_report as cmd_only
    import autocode_support as support
    from autocode_run_records import stage_supports_sessions

FORMAT_ERROR = "OpenCode final message is not a JSON report"


def event_thread_id(jsonl: Path) -> str | None:
    for event in support.events(jsonl):
        if event.get("type") == "thread.started" and event.get("thread_id"):
            return str(event["thread_id"])
    return None


def session_for(state, pending, route_role=None) -> str | None:
    """The original attempt's session thread, when a format-only correction run on ``route_role``
    (the route the correction launches on; the original's own when not given) applies."""
    original = pending.get("original") or {}
    error = pending.get("error") or original.get("rejection_reason") or ""
    if pending.get("attempts") or pending.get("correction_attempted"):
        return None
    if cmd_only.matches(error):
        if original.get("engine") not in ("opencode", "codex"):
            return None
    elif original.get("engine") != "opencode" or not error.startswith(FORMAT_ERROR) or original.get("planning"):
        return None
    if not stage_supports_sessions(state, original) or not same_route(state, original, route_role):
        return None
    return event_thread_id(Path(original["events"]))


def same_route(state, original, route_role=None) -> bool:
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


def prompt(error: str) -> str:
    if cmd_only.matches(error):
        return cmd_only.CORRECTION
    return (
        prompts.get("fragments/format-correction/prompt-02.md")
        + error[:200]
        + prompts.get("fragments/format-correction/prompt.md")
    )


def execute(runtime, state, run_dir, workspace) -> bool:
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
    state.update(phase="REPORT_REPAIR")
    runtime.write_json(run_dir / "state.json", state)
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
    runtime.account_stage(state, record)
    runtime.accept_repaired_report(state, run_dir, workspace, value, record)
    return True
