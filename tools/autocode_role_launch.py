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
    from . import autocode_agent_env as agent_env
    from . import autocode_classification_recovery as classification_recovery
    from . import autocode_format_correction as format_correction
    from . import autocode_job_failure as job_failure
    from . import autocode_output_cap as output_cap
    from . import autocode_output_policy as output_policy
    from . import autocode_planning as planning
    from . import autocode_progressive_state as progressive_state
    from . import autocode_provider_auth as provider_auth
    from . import autocode_provider_env as provider_env
    from . import autocode_provider_launch as provider_launch
    from . import autocode_resolver_recovery as resolver_recovery
    from . import autocode_resolver_runtime as resolver_runtime
    from . import autocode_reviewer_fallback as reviewer_fallback
    from . import autocode_source_diff as source_diff
    from . import autocode_source_scope as source_scope
    from . import autocode_stage_stops as stage_stops
    from . import autocode_status
    from . import autocode_support as support
    from . import autocode_visual_profile as visual_profile
    from . import autocode_visual_runtime as visual_runtime
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_classification_recovery as classification_recovery
    import autocode_format_correction as format_correction
    import autocode_job_failure as job_failure
    import autocode_output_cap as output_cap
    import autocode_output_policy as output_policy
    import autocode_planning as planning
    import autocode_progressive_state as progressive_state
    import autocode_provider_auth as provider_auth
    import autocode_provider_env as provider_env
    import autocode_provider_launch as provider_launch
    import autocode_resolver_recovery as resolver_recovery
    import autocode_resolver_runtime as resolver_runtime
    import autocode_reviewer_fallback as reviewer_fallback
    import autocode_source_diff as source_diff
    import autocode_source_scope as source_scope
    import autocode_stage_stops as stage_stops
    import autocode_status
    import autocode_support as support
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


def replay_context(state: RunState, *, stage, iteration, report_only, original_stage, changed_code_proof):
    """What replay needs to match this launch to its recorded answer (docs/replay.md)."""
    return {
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
    if original_stage == "sol" and visual_runtime.requested(state):
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
    if joint_stage and not report_only:
        planning.charge(state, original_stage, record=record, workspace=workspace)
        if fallback_route:
            admit_reviewer_fallback(state, run_dir, workspace, original_stage, fallback_route, record)
    resolver_runtime.charge_diagnostic_dispatch(controller, state, run_dir, workspace, record)
    progressive_state.admit_attempt(state, record, before)
    job_failure.admit(state, record, workspace)
    classification_recovery.admit(state, record, workspace)
    return native_prepared


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
        raise support.Paused(
            "PAUSED_PROVIDER_TIMEOUT", f"{role}: {record['timeout_reason']}; partial work and logs retained at {events}"
        )
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


def record_after(state: RunState, record, workspace, base, before, events, *, save):
    """Save the source after the stage, its diff and the tool summary, and name them on the record."""
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
