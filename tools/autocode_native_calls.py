"""Bounded original planning and first-approval caller dependencies.

The existing CLI supplies lifecycle operations; this module imports no
controller/cycle member and never reads a private state file. A same-owner repair and final validate handoff retain the ordinary lifecycle;
new owners and unrelated recovery controls remain held.
"""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_native_capture as captures
    from . import autocode_native_check_execution as checks
    from . import autocode_native_completion as completion
    from . import autocode_native_parent as parent
    from . import autocode_native_role_isolation as isolation
    from . import autocode_native_stage_recovery as stage_recovery
    from . import autocode_run_status as run_status
    from . import autocode_util as util
    from . import autocode_visual_evidence as evidence
    from . import autocode_visual_profile as profiles
except ImportError:
    import autocode_native_capture as captures
    import autocode_native_check_execution as checks
    import autocode_native_completion as completion
    import autocode_native_parent as parent
    import autocode_native_role_isolation as isolation
    import autocode_native_stage_recovery as stage_recovery
    import autocode_run_status as run_status
    import autocode_util as util
    import autocode_visual_evidence as evidence  # noqa: F401 - preserve existing import behavior
    import autocode_visual_profile as profiles


class PlanningCalls:
    def __init__(
        self, context, intent, *, initial_task, run_dir, workspace, write, snapshot, planning_stages, recognizer
    ):
        self.context = context
        self.intent = profiles.profile_value(intent)
        if not isinstance(initial_task, str) or not initial_task:
            raise ValueError("Native planning needs the complete original operator input")
        self.initial_task = initial_task
        self.workspace, self.run_dir = Path(workspace), Path(run_dir)
        self.snapshot = snapshot
        self.stages = frozenset(planning_stages) | {recognizer}
        self.sink = parent.ParentSink(run_dir, factory=context.factory, ordinary_write=write, snapshot=snapshot)
        context.factory.bind_parent_sink(self.sink, parent.ParentSink)
        self.approval = None
        self._captures = None
        self._captures_binding = None

    def guard(self, state: RunState, stage=None, *, report_only=False, allow_write=None):
        self.sink.require_ready()
        self.require_initial_input(state)
        stage = stage or state.get("next_stage")
        if state.get("current_task") is not None:
            if (
                stage not in ("terra", "sol", "astra_review", "astra_resolve")
                or report_only
                or state.get("pending_report_repair")
                or any(
                    state.get(key)
                    for key in (
                        "parent_run",
                        "parent_batch",
                        "orchestration_batch",
                        "orchestration_history",
                        "progressive",
                    )
                )
            ):
                raise util.Paused(
                    "PAUSED_VISUAL_EVIDENCE", "Native task is outside the same-owner reviewed execution path"
                )
            current = self.snapshot(state)
            if stage == "terra":
                if allow_write is False or cast(dict, state["current_task"]).get("kind") != "implement":
                    raise util.Paused(
                        "PAUSED_VISUAL_EVIDENCE", "Original Builder must use its exact writable task route"
                    )
                self.context.factory.first_writer_proof(state, snapshot=current)
                return
            if allow_write:
                raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Readonly review cannot borrow writer permission")
            if stage in ("astra_review", "astra_resolve"):
                self.review_proof(state, current)
                if stage == "astra_resolve":
                    self.context.factory.require_resolver_request(state, snapshot=current)
                return
            binding = self.context.factory.protection.readonly_task_authority(state, snapshot=current)["binding"]
            if self._captures is None or self._captures_binding != binding:
                self._captures = captures.NativeCaptureExecution(
                    self.context, state, persist=self.sink.write
                ).all_cases()
                self._captures_binding = deepcopy(binding)
            self.image_capture_authority(state=state, snapshot=current, captures=self._captures)
            return
        if (
            stage not in self.stages
            or report_only
            or allow_write
            or state.get("current_task") is not None
            or state.get("pending_report_repair")
            or state.get("settings", {}).get("planning_flow") != "v2"
            or any(
                state.get(key)
                for key in ("parent_run", "parent_batch", "orchestration_batch", "orchestration_history", "progressive")
            )
        ):
            raise util.Paused(
                "PAUSED_VISUAL_EVIDENCE",
                "Native Scope 3 ends at planning and first approval; execution/recovery/capture require later scopes",
            )
        self.context.factory.require_planning_origin(state, snapshot=self.snapshot(state))

    def dispatch_guard(self, state: RunState, stage, ordinary_guard):
        """Thread the original typed first-writer scope into existing guards."""
        if stage != "terra":
            return ordinary_guard(state, stage)
        current = self.snapshot(state)
        proof = self.context.factory.first_writer_proof(state, snapshot=current)
        return ordinary_guard(state, stage, current=current, visual_proof=proof)

    def prepare_writer_launch(
        self,
        state: RunState,
        snapshot,
        record,
        worker_context,
        command,
        environment,
        native_role_isolation,
        *,
        report_only=False,
    ):
        """Prepare only the original writable Builder before provider verification.

        The CLI must still invoke its mandatory final provider verifier. Every
        other native role keeps the original verify-then-prepare ordering.
        """
        boundary = worker_context["tool_containment"]
        if (
            report_only
            or record.get("stage") != "terra"
            or record.get("role") != "terra"
            or boundary.get("allow_write") is not True
        ):
            return None
        return self.context.factory.prepare_launch(
            state,
            snapshot,
            record,
            boundary=boundary,
            command=command,
            environment=environment,
            native_role_isolation=native_role_isolation,
        )

    def role_isolation(self, state: RunState, route_role):
        self.guard(state)
        if profiles.kilo.selected(self.intent, state["settings"], route_role) is not None:
            return isolation.KiloRoleIsolation(
                state, self.workspace, self.run_dir, route_role, profile=self.intent, native=self
            )
        return isolation.NativeRoleIsolation(
            state,
            self.workspace,
            self.run_dir,
            route_role,
            sdk_path=self.intent["sdk_path"],
            sdk_manifest_sha256=self.intent["sdk_manifest_sha256"],
            transport_identity_sha256=self.intent["transport_identity_sha256"],
            executable_sha256=self.intent["executable_sha256"],
        )

    def approve(self, operation, state: RunState, selected):
        self.sink.require_ready()
        self.require_initial_input(state)
        self.context.factory.require_planning_origin(state, snapshot=self.snapshot(state))
        if state.get("current_task") is not None or self.approval is not None:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Only the original initial approval is supported")
        # Validate profile data without inventing an approval event. Actual
        # approved authority is checked by lifecycle and the concrete factory.
        constraints = cast(dict, state.get("goal_contract", {})).get("body", {}).get("constraints", [])
        markers = [line for line in constraints if isinstance(line, str) and line.startswith(profiles.MARKER)]
        if (
            len(markers) != 1
            or profiles.profile_value(profiles._json(markers[0][len(profiles.MARKER) :])) != self.intent
        ):
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Reviewed native profile differs from original operator intent")
        handle = self.context.factory.prepare_initial_approval(state, selected, snapshot=self.snapshot(state))

        def proof(candidate, *, snapshot):
            return self.context.factory.initial_assignment_proof(handle, candidate, snapshot=snapshot)

        operation(state, selected, initial_assignment_proof=proof)
        self.approval = handle

    def require_supported_action(self, args, state: RunState):
        """Hold later authority controls before setup, artifacts or model charges."""
        self.sink.require_ready()
        self.require_initial_input(state)
        if getattr(args, "abandon_stage", None) is not None:
            stage_recovery.only_abandon(getattr(args, "_native_action_argv", None))
            record = stage_recovery.request(state, args.abandon_stage, self.stages)
            self.context.factory.protection.planning_abandonment(
                state, record, snapshot=self.snapshot(state), commit=False
            )
            return
        unsupported: tuple[str, ...] = (
            "revise_figma_manifest",
            "retry_builder",
            "retry_failed_stage",
            "reconcile_stage",
            "abandon_stage",
            "retry_format",
            "retry_report",
            "approve_review",
            "reconcile_review",
            "accept_completion",
            "supply_dependency",
            "restore_protected_tests",
            "edit_goal",
            "resume_parallel",
            "diagnose_failed_stage",
            "grant_recovery",
        )
        if getattr(args, "run_dir", None):
            unsupported += ("test_command", "regression_command")
        if any(getattr(args, key, None) for key in unsupported):
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "This control needs a native scope beyond first approval")
        if state.get("active_stage") or state.get("pending_report_repair"):
            raise util.Paused(
                "PAUSED_VISUAL_EVIDENCE", "Original native worker reconciliation is not implemented by Scope 3"
            )
        if state.get("current_task") is not None:
            stage = state.get("next_stage")
            if stage not in ("terra", "sol", "astra_review", "astra_resolve"):
                raise util.Paused(
                    "PAUSED_VISUAL_EVIDENCE",
                    "Only the same-owner Builder, Tester, Completion and Resolver path is enabled",
                )
            current = self.snapshot(state)
            if stage == "terra":
                if cast(dict, state["current_task"]).get("kind") != "implement":
                    raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Readonly task cannot resume as Builder")
                self.context.factory.first_writer_proof(state, snapshot=current)
            elif stage in ("astra_review", "astra_resolve"):
                self.review_proof(state, current)
                if stage == "astra_resolve":
                    self.context.factory.require_resolver_request(state, snapshot=current)
            else:
                self.context.factory.protection.readonly_task_authority(state, snapshot=current)

    def abandon_stage(self, state: RunState, run_dir, workspace, selected, *, launch=None):
        """Use the original journal/sink; ordinary archive and escalation stay untouched."""
        self.sink.require_ready()
        self.require_initial_input(state)
        stage_recovery.require(
            Path(run_dir) == self.run_dir and Path(workspace) == self.workspace, "foreign recovery workspace/run"
        )
        record = stage_recovery.request(state, selected, self.stages)
        retained = self.context.factory.protection.planning_abandonment(
            state, record, snapshot=self.snapshot(state), commit=True
        )
        candidate = stage_recovery.abandoned(state, retained, selected, at=util.now())
        self.sink.write(self.run_dir / "state.json", candidate)
        cast(dict, state).clear()
        state.update(candidate)

    def image_capture_authority(self, *, state: RunState, snapshot, captures):
        self.sink.require_ready()
        self.require_initial_input(state)
        return self.context.factory.protection.authenticate_captures(state, snapshot=snapshot, captures=captures)

    def verification_execution(self, state: RunState, record):
        self.sink.require_ready()
        self.require_initial_input(state)
        if record.get("stage") != "sol" or record.get("role") != "sol":
            raise util.Paused(
                "PAUSED_VISUAL_EVIDENCE", "Native clean replay requires the original independent Sol review"
            )
        return checks.NativeCheckExecution(self.context, state, persist=self.sink.write, review_record=record)

    def current_review(self, state: RunState, snapshot):
        self.sink.require_ready()
        return self.context.factory.current_decision_review(state, snapshot=snapshot)

    def review_proof(self, state: RunState, snapshot):
        return self.context.factory.decision_review_proof(
            state, snapshot=snapshot, selector=self.current_review(state, snapshot)
        )

    def review_progress(self, state: RunState, record, observe):
        """Only AFTER actual publication; rejection cannot accept a milestone."""
        current = self.snapshot(state)
        selected = self.current_review(state, current)
        original = self.context.factory.verify_decision_review(selected)
        if original["metadata"]["record"] != record:
            raise util.Paused("PAUSED_VISUAL_EVIDENCE", "Milestone observation requires the same original review")
        candidate = deepcopy(state)
        if selected["kind"] == "reviews":
            proof = self.context.factory.checkpoint_proof(state, snapshot=current, review_selector=selected)
            observe(candidate, current, visual_proof=proof)
        return candidate

    def assign_task(self, operation, state: RunState, value, current, record):
        self.sink.require_ready()
        self.require_initial_input(state)
        return self.context.factory.assign_task(operation, state, value, current, record)

    def completion(self, state: RunState, current, record, value):
        """Require the original all-case review before terminal preparation."""
        self.sink.require_ready()
        self.require_initial_input(state)
        selected = self.current_review(state, current)
        self.context.factory.require_final_integration(state, snapshot=current, review_selector=selected)
        return completion.NativeCompletionPreparation(
            self.context, state, snapshot=current, record=record, decision=value
        )

    def require_initial_input(self, state: RunState):
        if state.get("task") != self.initial_task:
            raise util.Paused(
                "PAUSED_VISUAL_EVIDENCE", "Complete initial operator input changed after original activation"
            )

    def reject(self, state: RunState, record, error):
        """Retain original failed bytes; never archive/delete protected inputs."""
        record.update(rejected=True, rejection_reason=str(error))
        if not any(row.get("output") == record.get("output") for row in state.get("stages", [])):
            state.setdefault("stages", []).append(deepcopy(record))
        state.pop("active_stage", None)
        run_status.pause(state, "PAUSED_INVALID_OUTPUT", str(error))
        self.sink.write(self.run_dir / "state.json", state)
        raise util.Paused("PAUSED_INVALID_OUTPUT", "Original native output retained: " + str(error))
