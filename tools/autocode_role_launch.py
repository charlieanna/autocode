"""Steps of one provider stage launch that ``autocode.run_role`` composes (#2753).

``run_role`` owns the order of a stage: guards, launch preparation, admission, the
supervised wait and the result. The self-contained steps it takes along the way live
here: the native launch environment, the replay context, the visual-runtime launch,
the launch admission, the activity reporter, the reviewer-fallback grant, the stop after the provider exits
and the source evidence recorded after it. Each step changes the record and state in
the order ``run_role`` did before. Persistence and the clock are passed in, so a
caller's (and a test's) ``write_json`` and ``now`` still apply, and this module never
imports ``autocode``.
"""

from __future__ import annotations

import json
import os
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_activity as activity_monitor_mod
    from . import autocode_agent_env as agent_env
    from . import autocode_changed_code_evidence as changed_code_evidence
    from . import autocode_classification_recovery as classification_recovery
    from . import autocode_draft_failure_correction as failure_correction
    from . import autocode_first_reply_budget as first_reply_budget
    from . import autocode_format_correction as format_correction
    from . import autocode_job_failure as job_failure
    from . import autocode_output_cap as output_cap
    from . import autocode_output_policy as output_policy
    from . import autocode_partial_work as partial_work
    from . import autocode_planning as planning
    from . import autocode_progressive_state as progressive_state
    from . import autocode_provider_auth as provider_auth
    from . import autocode_provider_env as provider_env
    from . import autocode_provider_launch as provider_launch
    from . import autocode_regression as regression
    from . import autocode_resolver_recovery as resolver_recovery
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_reviewer_fallback as reviewer_fallback
    from . import autocode_source_diff as source_diff
    from . import autocode_source_scope as source_scope
    from . import autocode_stage_stops as stage_stops
    from . import autocode_stale_handoff as stale_handoff
    from . import autocode_status
    from . import autocode_support as support
    from . import autocode_toolchain_byproducts as toolchain_byproducts
    from . import autocode_visual_profile as visual_profile
    from . import autocode_visual_runtime as visual_runtime
except ImportError:
    import autocode_activity as activity_monitor_mod
    import autocode_agent_env as agent_env
    import autocode_changed_code_evidence as changed_code_evidence
    import autocode_classification_recovery as classification_recovery
    import autocode_draft_failure_correction as failure_correction
    import autocode_first_reply_budget as first_reply_budget
    import autocode_format_correction as format_correction
    import autocode_job_failure as job_failure
    import autocode_output_cap as output_cap
    import autocode_output_policy as output_policy
    import autocode_partial_work as partial_work
    import autocode_planning as planning
    import autocode_progressive_state as progressive_state
    import autocode_provider_auth as provider_auth
    import autocode_provider_env as provider_env
    import autocode_provider_launch as provider_launch
    import autocode_regression as regression
    import autocode_resolver_recovery as resolver_recovery
    import autocode_resolver_runtime as resolver_runtime
    import autocode_reviewer_fallback as reviewer_fallback
    import autocode_source_diff as source_diff
    import autocode_source_scope as source_scope
    import autocode_stage_stops as stage_stops
    import autocode_stale_handoff as stale_handoff
    import autocode_status
    import autocode_support as support
    import autocode_toolchain_byproducts as toolchain_byproducts
    import autocode_visual_profile as visual_profile
    import autocode_visual_runtime as visual_runtime


def native_environment(state: RunState, workspace, events, adapter, output, before):
    """The scrubbed environment of a native launch, and its capture context when the tool writes a report file."""
    environment = agent_env.scrubbed(os.environ)
    environment.update(output_policy.environment(state["settings"], workspace, events))
    capture = None
    if getattr(adapter, "OUTPUT", "opencode_events") == "report_file":
        capture = {
            "attempt": str(output),
            "nonce": uuid.uuid4().hex,
            "source_revision": cast(dict[str, Any], before)["revision"],
        }
        environment["AUTOCODE_CAPTURE_CONTEXT"] = json.dumps(capture)
    return environment, capture


def changed_code_proof(state: RunState):
    """The current changed-line proof's identity, for replay to rebind a recorded Tester's gap ids (#2500)."""
    return changed_code_evidence.replay_identity(state.get("regression_proof"))


def replay_context(state: RunState, *, stage, iteration, report_only, original_stage, changed_code_proof):
    """What replay needs to match this launch to its recorded answer (docs/replay.md)."""
    context = {
        "stage": stage,
        "iteration": iteration,
        "turn": len(state.get("turns") or []) + 1,
        "milestone_id": (state.get("current_task") or {}).get("milestone_id"),
        "task_id": (state.get("current_task") or {}).get("id"),
        "contract_hash": (state.get("goal_contract") or {}).get("hash"),
        "criteria_revision": state.get("criteria_revision"),
        "report_only": report_only,
        "original_stage": original_stage if report_only else None,
        "consumed": sum(isinstance(row.get("replay"), dict) for row in state.get("stages", [])),
        "changed_code_proof": changed_code_proof,
        "regression_proof_path": (state.get("regression_proof") or {}).get("path"),
    }
    if (original_stage if report_only else stage) == "review_plan":
        draft_snapshot = failure_correction.snapshot(state)
        if draft_snapshot is not None:
            context["draft_inference_snapshot"] = draft_snapshot
    return context


def decorate_prompt(state: RunState, stage, workspace, adapter, prompt, schema, events, worker_context):
    """The existing schema/containment decoration, before provider admission."""
    prompt = adapter.prompt_for_schema(prompt, schema, events)
    current_proof = False
    if stage == "tester" and worker_context.get("tool_containment") and regression.required(state):
        current_proof = regression.complete(state, source_scope.snapshot(workspace, state)["revision"])
    return provider_launch.containment_prompt(
        prompt,
        worker_context,
        stage=stage,
        regression_proof_current=current_proof,
        regression_handoff=regression.handoff(state) if current_proof else None,
    )


def prepare_visual(
    state: RunState,
    original_stage,
    workspace,
    run_dir,
    base,
    command,
    child_environment,
    prompt,
    *,
    engine,
    configured_tool,
    adapter,
    worker_context,
    child_options,
    save,
):
    """Prepare a visual Tester launch on the approved OpenCode route; returns the context, command, env and prompt."""
    if engine != "opencode" or configured_tool:
        raise support.Paused(
            "PAUSED_VISUAL_EVIDENCE", "Verified visual delivery requires the supported native OpenCode route"
        )
    saved_transport = (
        state["settings"].get("transport_identities", {}).get("opencode", state["settings"].get("transport_identity"))
    )
    if saved_transport is None or adapter.transport_drift(adapter.local_settings(workspace), saved_transport):
        raise support.Paused("PAUSED_VISUAL_EVIDENCE", "Visual transport changed since its approved profile")
    visual_context, command, child_environment, prompt = visual_runtime.prepare(
        state,
        original_stage,
        workspace,
        run_dir,
        base,
        command,
        child_environment,
        prompt,
        launch_authority=visual_profile.authority(state, worker_context, workspace, run_dir),
        current_snapshot=source_scope.snapshot(workspace, state, base_snapshot=support.snapshot),
    )
    worker_context["visual_runtime"] = visual_context
    child_options["env"] = child_environment
    worker_context.update(command=command, environment=child_environment)
    if visual_context and visual_context.get("status") == "READY" and engine == "opencode" and not configured_tool:
        save(base.with_suffix(".opencode.json"), json.loads(child_environment["OPENCODE_CONFIG_CONTENT"]))
    return visual_context, command, child_environment, prompt


def admit_reviewer_fallback(state: RunState, run_dir, workspace, original_stage, fallback_route, record):
    """Consume the reviewer-fallback grant this launch was prepared with, and name it on the record."""
    admitted = reviewer_fallback.admit(state, run_dir, workspace, original_stage)
    if admitted != fallback_route:
        raise support.Paused("PAUSED_REVIEWER_FALLBACK", "Reviewer fallback changed before provider admission")
    record["reviewer_fallback_grant"] = next(
        grant["id"]
        for grant in state["planning"]["reviewer_route_fallbacks"]
        if grant.get("consumed") and grant["binding"]["selected_route"] == admitted
    )


def admit_launch(
    state: RunState,
    record,
    before,
    workspace,
    run_dir,
    *,
    native,
    native_isolation,
    worker_context,
    command,
    environment,
    child_environment,
    visual_context,
    original_stage,
    report_only,
    joint_stage,
    fallback_route,
    retry_authorization,
    controller,
):
    """Prepare and verify the launch boundary, then take every admission and charge; returns the native preparation.

    It runs inside the intervention admission, before the stage becomes active: a refusal here starts nothing.
    """
    stale_handoff.capture_launch_binding(state, record, before)
    native_prepared = (
        native.prepare_writer_launch(
            state, before, record, worker_context, command, environment, native_isolation, report_only=report_only
        )
        if native is not None
        else None
    )
    provider_launch.verify_containment(
        worker_context,
        **(
            {
                "state": state,
                "native_role_isolation": native_isolation,
                "command": command,
                "environment": environment,
            }
            if native is not None
            else {}
        ),
    )
    if native is not None and native_prepared is None:
        native_prepared = native.context.factory.prepare_launch(
            state,
            before,
            record,
            boundary=worker_context["tool_containment"],
            command=command,
            environment=environment,
            native_role_isolation=native_isolation,
        )
    if original_stage == "tester" and visual_runtime.requested(state):
        visual_runtime.verify_reference(state)
    if visual_context and visual_context.get("status") == "READY":
        visual_runtime.verify_prelaunch(
            visual_context,
            state,
            run_dir=run_dir,
            current_snapshot=before,
            command=command,
            env=child_environment,
            **({"scope_authority": native.context.factory.scope_authority} if native is not None else {}),
        )
    resolver_recovery.admit_dispatch(state, record, workspace, run_dir, retry_authorization=retry_authorization)
    stale_handoff.admit_relaunch(state, record, workspace, run_dir)
    if joint_stage and not report_only:
        planning.charge(state, original_stage, record=record, workspace=workspace)
        if fallback_route:
            admit_reviewer_fallback(state, run_dir, workspace, original_stage, fallback_route, record)
    resolver_runtime.charge_diagnostic_dispatch(controller, state, run_dir, workspace, record)
    progressive_state.admit_attempt(state, record, before)
    job_failure.admit(state, record, workspace)
    classification_recovery.admit(state, record, workspace)
    if original_stage == "builder" and not report_only and not record.get("dry_run"):
        partial_work.capture_start(record, before, workspace, run_dir, state=state)
    return native_prepared


def stage_deadline_and_progress(state: RunState, stage: str, configured_timeout, *, report_only: bool):
    """Return ``(stage_timeout, budget_meta, progress_budget)`` for a stage launch."""
    stage_timeout = job_failure.launch_deadline(state, stage, configured_timeout, report_only)
    if not first_reply_budget.applies(state, stage, report_only=report_only):
        return stage_timeout, None, None
    max_seconds, max_calls = first_reply_budget.limits(state)
    if isinstance(stage_timeout, (int, float)) and stage_timeout > 0:
        stage_timeout = min(int(stage_timeout), max_seconds)
    else:
        stage_timeout = max_seconds
    budget_meta = {"max_seconds": stage_timeout, "max_tool_calls": max_calls}
    return stage_timeout, budget_meta, first_reply_budget.ProgressBudget(max_calls)


def activity_monitor(events, *, idle_limit, tool_timeout, reporter, idle_origin, idle_hint, progress, plain_text):
    """Construct an ActivityMonitor with optional per-stage progress budget."""
    return activity_monitor_mod.ActivityMonitor(
        events,
        idle_seconds=idle_limit,
        tool_seconds=tool_timeout,
        reporter=reporter,
        idle_origin=idle_origin,
        idle_hint=idle_hint,
        progress=progress,
        plain_text=plain_text,
    )


def supervision(
    state: RunState,
    record,
    events,
    *,
    stage,
    model,
    idle_limit,
    tool_timeout,
    stage_timeout,
    started,
    state_path,
    save,
    clock,
    reporter,
    idle_origin,
    idle_hint,
    progress,
    plain_text,
):
    """Build ``(activity_monitor, activity_checkpoint)`` for a supervised stage wait."""
    monitor = activity_monitor(
        events,
        idle_limit=idle_limit,
        tool_timeout=tool_timeout,
        reporter=reporter,
        idle_origin=idle_origin,
        idle_hint=idle_hint,
        progress=progress,
        plain_text=plain_text,
    )
    checkpoint = activity_reporter(
        state,
        record,
        stage=stage,
        model=model,
        idle_limit=idle_limit,
        tool_timeout=tool_timeout,
        stage_timeout=stage_timeout,
        started=started,
        state_path=state_path,
        save=save,
        clock=clock,
    )
    return monitor, checkpoint


def activity_reporter(
    state: RunState, record, *, stage, model, idle_limit, tool_timeout, stage_timeout, started, state_path, save, clock
):
    """The activity checkpoint: save each observation, and print one line when it changes or once a minute."""
    printed = {"label": None, "at": 0}

    def checkpoint(snapshot):
        record["activity"] = {
            **snapshot,
            "observed_at": clock(),
            "elapsed_seconds": round(time.monotonic() - started, 1),
            "stage_limit_seconds": stage_timeout,
        }
        save(state_path, state)
        label = (snapshot.get("activity"), snapshot.get("detail"))
        current = time.monotonic()
        if label != printed["label"] or current - printed["at"] >= 60:
            stop = snapshot.get("timeout_reason") or (
                snapshot.get("detail") if snapshot.get("activity") == "stalled" else None
            )
            print(
                f"{autocode_status.role_name(stage, state)}: {snapshot.get('activity', 'waiting_for_provider')}; model={model or 'default'}; "
                f"elapsed={record['activity']['elapsed_seconds']:g}s; "
                f"idle={snapshot.get('idle_seconds', 0):g}s/{idle_limit or 'off'}; "
                f"tool={snapshot.get('tool_elapsed_seconds', 0) or 0:g}s/{tool_timeout or 'off'}; "
                f"stage_limit={stage_timeout or 'off'}" + (f"; {stop}" if stop else ""),
                flush=True,
            )
            printed.update(label=label, at=current)

    return checkpoint


def raise_for_exit(
    state: RunState,
    record,
    *,
    role,
    events,
    output,
    exit_code,
    cleanup_error,
    interrupted,
    timed_out,
    engine,
    configured_tool,
    adapter,
    supports_sessions,
    session,
    report_only,
    route_role,
):
    """Pause unless the provider exited cleanly with its report; a new session is recorded as the role's."""
    if cleanup_error:
        raise support.Paused("PAUSED_PROCESS_CLEANUP", cleanup_error)
    if interrupted:
        raise support.Paused(
            "PAUSED_INTERRUPTED", "Provider stage interrupted; inspect saved artifacts before reconciliation"
        )
    if timed_out:
        # A person's stop (#3263) ends the stage as a deadline does, so its partial work is kept the same way;
        # the queued pause that asked for it then holds the run at the next boundary.
        reason = "stopped by you" if record.get("timeout_kind") == "person" else record["timeout_reason"]
        raise support.Paused("PAUSED_PROVIDER_TIMEOUT", f"{role}: {reason}; partial work and logs retained at {events}")
    if exit_code != 0:
        refused = stage_stops.refusal_reason(state, record) or stage_stops.context_window_reason(state, record)
        hint = (
            provider_env.authentication_hint(adapter.raw_events(events), record["withheld_env"])
            if engine == "opencode" and configured_tool
            else ""
        )
        raise support.Paused(
            support.failure_status(events, record=record),
            provider_auth.exit_message(role, exit_code, events, refused=refused or "", hint=hint or ""),
        )
    if supports_sessions:
        thread = format_correction.event_thread_id(events)
        if not thread or (session and thread != session):
            raise support.Paused("PAUSED_UNCERTAIN_STAGE", "Provider returned a missing or unexpected session ID")
        if not session and not report_only:
            state["sessions"][route_role] = thread
            record["thread_id"] = thread
        if not any(e.get("type") == "turn.completed" for e in support.events(events)):
            stage_stops.refused_at_clean_exit(state, record, role, events)
            stage_stops.stopped_at_context_window(state, record, role, events)
            raise support.Paused(
                output_cap.STATUS
                if support.failure_status(events, record=record) == output_cap.STATUS
                else "PAUSED_UNCERTAIN_STAGE",
                output_cap.explain(support.terminal_failure_reason(events), record.get("output_token_cap"))
                or "Process exited without turn.completed",
            )
    elif not output.is_file():
        stage_stops.refused_at_clean_exit(state, record, role, events)
        stage_stops.stopped_at_context_window(state, record, role, events)
        raise support.Paused("PAUSED_UNCERTAIN_STAGE", "Process exited without a report file")


def record_after(state: RunState, record, workspace, base, before, events, *, save, allow_write=False):
    """Save the source after the stage, its diff and the tool summary, and name them on the record.

    A lockfile the project's toolchain created while the stage ran it is removed first and named on
    the record (autocode_toolchain_byproducts): a read-only stage leaves the repository as it found
    it, and a Builder is not charged with a file outside its assigned paths that a tool wrote. A
    writing stage with no assigned paths may write anywhere, so nothing of its is removed.
    """
    owned = (state.get("current_task") or {}).get("affected_paths") or [] if allow_write else []
    if not allow_write or owned:
        removed = toolchain_byproducts.discard(workspace, before, owned=owned)
        if removed:
            record["toolchain_byproducts"] = removed
    after = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)
    save(base.with_suffix(".after.json"), after)
    record["after_ref"] = str(base.with_suffix(".after.json"))
    record["source_revision"] = after["revision"]
    record["changed_files"] = support.changed_paths(before, after)
    diff_path = base.with_suffix(".diff")
    source_diff.write(workspace, diff_path, after)
    record["diff_ref"] = str(diff_path)
    summary_path = base.with_suffix(".tools.json")
    support.summarize_events(events, summary_path)
    record["tool_evidence"] = str(summary_path)
    return after
