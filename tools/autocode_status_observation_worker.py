"""Allowlisted read-only status observations; owns no run actions."""

import json
import sys


def _worker(request):
    if not isinstance(request, dict):
        raise ValueError("Invalid status observation")
    payload = request["payload"]
    if request.get("operation") == "retained_retry":
        import autocode_acceptance_retry_next as retry_next

        return retry_next.decide(payload["state"], payload["workspace"])
    if request.get("operation") == "job_retry":
        import autocode_job_retry_admission as admission
        import autocode_util as util

        state = payload["state"]
        try:
            admission.validate_retry(state, payload["workspace"], payload["token"])
        except admission.RetryConfigurationChanged as error:
            from pathlib import Path

            import autocode_dispatch as dispatch
            import autocode_interventions as interventions
            import autocode_opencode as opencode
            import autocode_support as support

            run_dir = payload.get("run_dir") or state.get("run_dir")
            if not isinstance(run_dir, str) or interventions.pending(Path(run_dir)):
                return {"allowed": False, "reason": str(error)}
            possible = admission.route_is_repairable(
                state,
                cross_check=dispatch.enforce_cross_model_verification,
                failure_status=support.failure_status,
                configured_tool=getattr(opencode, "CONFIGURED", False),
            )
            return {"allowed": False, "route_allowed": possible, "reason": str(error)}
        except (ValueError, OSError, KeyError, TypeError, util.Paused) as error:
            return {"allowed": False, "reason": str(error)}
        return {"allowed": True}
    if request.get("operation") == "failure_retry":
        import autocode_failure_retry_admission as admission
        import autocode_util as util

        try:
            admission.failure_retry_frontier(payload["state"], payload["workspace"])
        except (ValueError, OSError, KeyError, TypeError, util.Paused) as error:
            return {"allowed": False, "reason": str(error)}
        return {"allowed": True}
    if request.get("operation") == "source_snapshot":
        import autocode_source_scope as source_scope
        import autocode_support as support

        return source_scope.snapshot(payload["workspace"], payload["state"], base_snapshot=support.snapshot)
    if request.get("operation") == "evidence_inspection":
        from pathlib import Path

        import autocode_run_records as run_records
        import autocode_source_scope as source_scope
        import autocode_support as support
        import autocode_verification_inspection as verification

        state = payload["state"]
        return verification.inspect(
            state,
            payload["workspace"],
            snapshot=lambda root: source_scope.snapshot(root, state, base_snapshot=support.snapshot),
            read_state=lambda: run_records.read_json(Path(payload["run_dir"]) / "state.json"),
            accepted_human_ids=payload["accepted_human_ids"],
            initial_snapshot=payload["initial_snapshot"],
        )
    if request.get("operation") == "launch_guard":
        import autocode_launch_inputs as launch_inputs

        try:
            launch_inputs.guard(payload["state"], payload["workspace"], payload["run_dir"])
        except ValueError as error:
            return {"ok": False, "reason": str(error)}
        return {"ok": True}
    if request.get("operation") == "completion":
        import autocode_completion as completion
        import autocode_launch_inputs as launch_inputs
        import autocode_visual_runtime as visual

        state, current = payload["state"], payload["current"]
        visual_needed = bool((state.get("settings") or {}).get("design_manifest") or visual.requested(state))
        acceptance = visual.projection(state, current_snapshot=current) if visual_needed else None
        ready = (
            completion.completion_ready(state, state.get("final_decision", {}), current)
            if state.get("status") == "TASK_COMPLETE"
            else None
        )
        if ready:
            try:
                launch_inputs.guard(state, payload["workspace"], payload["run_dir"])
            except ValueError:
                ready = False
        if ready is True and visual_needed:
            ready = visual.completion_check(state, current_snapshot=current)["passed"]
        return {"current": ready, "visual_acceptance": acceptance}
    if request.get("operation") == "delivery":
        import autocode_dependency as dependency

        return dependency.export(payload["state"], payload["current"], True)
    if request.get("operation") == "report_repair":
        import autocode_source_scope as source_scope
        import autocode_stage_recovery as stage_recovery
        import autocode_support as support

        state, workspace = payload["state"], payload["workspace"]
        revision = (
            payload.get("revision")
            or source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"]
        )
        return {
            "revision": revision,
            "stale": stage_recovery.stale_report_repair(state, workspace, revision=revision) is not None,
        }
    if request.get("operation") == "catalog_offer":
        import autocode_catalog_reconciliation as catalog

        return catalog.offer(payload["state"], controller=payload["controller"])
    if request.get("operation") == "source_offer":
        import autocode_source_reconciliation as source

        return source.offer(
            payload["state"], payload["workspace"], payload["inspection"], controller=payload["controller"]
        )
    raise ValueError("Unsupported status observation")


if __name__ == "__main__":
    if sys.argv[1:] != ["--worker"]:
        raise SystemExit(2)
    print(json.dumps({"result": _worker(json.load(sys.stdin))}))
