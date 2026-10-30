"""Invocation-local native planning wiring through existing CLI dependencies.

No controller imports, global observer registry or new process tree. The CLI
supplies its original locked state reader and durable ownership seams.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import sys

try:
    from . import autocode_capability_startup as capability_startup
    from . import autocode_connected_engine as connected_engine
    from . import autocode_controller_owner as controller_owner
    from . import autocode_native_calls as native_calls
    from . import autocode_native_launch as native_launch
    from . import autocode_native_stage_recovery as stage_recovery
    from . import autocode_run_destination as run_destination
    from . import autocode_source_host as source_host
    from . import autocode_stage_choices as stage_choices
    from . import autocode_visual_profile as native_profile
    from . import autocode_worker_binding as worker_binding
except ImportError:
    import autocode_capability_startup as capability_startup
    import autocode_connected_engine as connected_engine
    import autocode_controller_owner as controller_owner
    import autocode_native_calls as native_calls
    import autocode_native_launch as native_launch
    import autocode_native_stage_recovery as stage_recovery
    import autocode_run_destination as run_destination
    import autocode_source_host as source_host
    import autocode_stage_choices as stage_choices
    import autocode_visual_profile as native_profile
    import autocode_worker_binding as worker_binding


def main(base, unit=None):
    """Restore the invocation's provider and output streams before returning to the caller."""
    capability_startup.prepare()
    launched_at = base.now()
    source_host.install()  # opt-in warm snapshots (#1707)
    saved_opencode = base.opencode
    saved_argv = sys.argv
    try:
        # Absorb later signals until an interrupted stage's pause is saved (#454).
        with base.processes.interrupts_held(), base.supervision_cli.guard(saved_argv[1:]) as argv:
            sys.argv = [saved_argv[0], *argv]
            return base.json_output.finish(base._main_body(unit, launched_at=launched_at))
    except base.run_setup.SettingRefused as error:
        base.json_output.refused(str(error))  # the invocation still ends with cli()'s `autocode: ...`
        raise
    finally:
        base.json_output.restore()
        sys.argv = saved_argv
        base.opencode = saved_opencode


def main_body(base, unit=None, *, launched_at=None):
    """Parse one CLI invocation before resolving its original native or ordinary ownership."""
    try:
        from . import autocode_subcommands as subcommands
    except ImportError:
        import autocode_subcommands as subcommands
    if not sys.argv[1:] and unit is None and sys.stdin.isatty() and sys.stdout.isatty():
        return subcommands.dispatch(["chat"])  # plain `autocode` in a terminal opens the chat
    handled = subcommands.dispatch(sys.argv[1:])
    if handled is not None:
        return handled
    builtins = {"capture": base.capture_command, "registry": base.registry.cli, "intervention": base.interventions.cli}
    if sys.argv[1:2] and sys.argv[1] in builtins:
        rest, entry = sys.argv[2:], builtins[sys.argv[1]]
        return subcommands.as_program(sys.argv[1], lambda: entry(rest), rest)
    args, parser = base.cli_args.parse(unit, sys.argv[1:], base.opencode.DEFAULT_MODELS)
    args._native_action_argv = tuple(sys.argv[1:])
    if args.worker_receipt:
        if sys.argv[-2:] != ["--worker-receipt", str(args.worker_receipt)]:
            parser.error("The service receipt must be the final internal argument")
        args._worker_context = worker_binding.load_entry(args.worker_receipt, sys.argv[1:-2])
    base.json_output.begin(args)
    args._interaction_launched_at = launched_at or base.now()
    if any(text is not None and not text.strip() for text in (args.feedback, args.follow_up)):
        print("Input rejected: Feedback and follow-ups must be nonempty", file=sys.stderr)
        return 2
    return resolve_and_run(base, args, parser, failure_routing=unit is None)


def resolve_and_run(base, args, parser, *, failure_routing=True):
    args._native_intent = (
        native_profile.initial_intent(args.task) if args.task and not args.status and not args.dry_run else None
    )
    if args._native_intent is not None and getattr(args, "replay_recording", None):
        raise base.support.Paused("PAUSED_VISUAL_EVIDENCE", "Native initial planning cannot use recorded inference")
    if args._native_intent is not None and any(
        getattr(args, key, None) for key in ("task_preflight", "ui_run", "figma_file")
    ):
        raise base.support.Paused(
            "PAUSED_VISUAL_EVIDENCE",
            "Native initial planning cannot use a preflight or generated handoff before its original owned origin",
        )
    try:
        resolved = base.run_setup.resolve(base, args, parser)
        if isinstance(resolved, int):
            return resolved
        return run(base, args, parser, resolved, failure_routing=failure_routing)
    except BaseException:
        fresh = getattr(args, "_fresh_task_worktree", None)
        if base.task_workspaces.discard_unused(fresh):
            print(f"Removed the unused task worktree and branch {fresh['branch']}; no run was saved.", file=sys.stderr)
        # An in-place launch reserved and locked its run directory before the refusal: without a checkpoint
        # it is only a "cannot be read" row in every run listing.
        unsaved = getattr(args, "_fresh_run_dir", None)
        if run_destination.discard_unsaved(unsaved):
            print(f"Removed the empty run directory {unsaved}; no run was saved.", file=sys.stderr)
        raise


def run(base, args, parser, resolved, *, failure_routing=True):
    workspace, run_dir, state_path, state = base.json_output.loaded(base, args, resolved)
    intent = native_profile.initial_intent(state["task"])
    if intent is not None and "replay_recording" in (state.get("settings") or {}):
        raise base.support.Paused("PAUSED_VISUAL_EVIDENCE", "Native initial planning cannot use recorded inference")
    journal = run_dir / "visual-native-protection"
    if args.run_dir and intent is not None and not journal.exists():
        raise base.support.Paused(
            "PAUSED_VISUAL_EVIDENCE", "Saved execution cannot acquire native planning authority retrospectively"
        )
    if journal.exists() and intent is None:
        raise base.support.Paused("PAUSED_VISUAL_EVIDENCE", "Original native initial intent is absent")
    if not args.run_dir:
        intent = args._native_intent
        if intent is not None and state["task"] != args.task:
            raise base.support.Paused(
                "PAUSED_VISUAL_EVIDENCE", "Native activation requires unchanged literal operator initial input"
            )
    base.support.assert_no_legacy_process(run_dir, workspace)
    base.task_workspaces.keep_out_of_git(workspace)
    with base.support.run_lock(run_dir) as run_handle:
        if args.run_dir:
            locked = base.read_json(state_path)
            if locked["workspace"] != str(workspace):
                parser.error("workspace differs from the locked checkpoint")
            if native_profile.initial_intent(locked["task"]) != intent or (
                intent is not None and locked["task"] != state["task"]
            ):
                raise base.support.Paused("PAUSED_VISUAL_EVIDENCE", "Native input changed before locked invocation")
            state = locked
        try:
            worker_binding.guard(args, state, run_dir, workspace)
        except ValueError as error:
            parser.error(str(error))
        if intent is None:
            if getattr(args, "reconcile_only", None) is None:
                if args.run_dir:
                    controller_owner.reconcile(state, run_dir, workspace, base.write_json)
                select_provider(base, args, parser, state, workspace)
            return base.run_setup.run_locked(
                base, args, parser, state, state_path, run_dir, workspace, failure_routing=failure_routing
            )
        # The existing loop receives the same checkout, not a second lock/tree.
        with base.checkout_lock.exclusive(workspace, run_dir) as checkout_handle:
            current = base.source_scope.snapshot(workspace, state, base_snapshot=base.support.snapshot)
            recovery_intent = stage_recovery.constructor_intent(
                args, state, {*base.planning.V2_STAGES, base.workflows.STAGE}
            )
            context = native_launch.NativeRunContext(
                workspace,
                run_dir,
                run_handle=run_handle,
                checkout_handle=checkout_handle,
                start_state=state,
                snapshot=current,
                activation_intent={"task": state["task"], "profile": intent},
                **({"planning_abandonment_intent": recovery_intent} if recovery_intent is not None else {}),
            )
            calls = native_calls.PlanningCalls(
                context,
                intent,
                run_dir=run_dir,
                workspace=workspace,
                initial_task=state["task"],
                write=base.write_json,
                snapshot=lambda value: base.source_scope.snapshot(
                    workspace, value, base_snapshot=base.support.snapshot
                ),
                planning_stages=base.planning.V2_STAGES,
                recognizer=base.workflows.STAGE,
            )
            calls.require_supported_action(args, state)  # Before config/probes/artifacts/charge.
            if args.run_dir:
                controller_owner.reconcile(state, run_dir, workspace, calls.sink.write, checkout_locked=True)
                calls.require_supported_action(args, state)
            select_provider(base, args, parser, state, workspace)
            return base.run_setup.run_locked(
                runner_view(base, calls),
                args,
                parser,
                state,
                state_path,
                run_dir,
                workspace,
                native=calls,
                checkout_owned=True,
                failure_routing=failure_routing,
            )


def select_provider(base, args, parser, state: "RunState", workspace):
    saved = dict(state.get("settings") or {})
    if state.get("settings") and "provider" not in saved:
        saved["provider"] = "opencode"
    try:
        if getattr(args, "replay_recording", None) or saved.get("replay_recording"):
            try:
                from . import autocode_replay_transport as replay_transport
            except ImportError:
                import autocode_replay_transport as replay_transport
            base.opencode = replay_transport.facade(args, state)
        else:
            # A new run nobody routed takes the connected backend when OpenCode is absent (#2049).
            connected_engine.apply(args, state, workspace)
            selected = base.autocode_providers.select(
                args.provider, saved, default="opencode" if args.engine == "codex" else None
            )
            base.opencode = base.autocode_providers.resolve(selected)
            if not (state.get("settings") or state.get("sessions") or state.get("history")):
                # A new run takes the stages the person chose for this backend (owner decision 2026-10-30).
                stage_choices.apply(args, "codex" if args.engine == "codex" else selected, say=_notice)
        if not args.run_dir:
            args._model_inventory = base.InvocationInventory(base.opencode, workspace)
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))


def _notice(line):
    # stderr, so --json output and captured reports stay what they were.
    print(line, file=sys.stderr, flush=True)


def dispatch_guard(runtime, state: "RunState", stage):
    """Keep the original native dispatch owner around the ordinary milestone guard."""
    native = getattr(runtime, "native_calls", None)
    if native is not None:
        native.dispatch_guard(state, stage, runtime.milestones.dispatch_guard)
    else:
        runtime.milestones.dispatch_guard(state, stage)


def runner_view(base, calls):
    """Invocation-local dependencies; no global writer or authority registry."""
    from types import SimpleNamespace

    runner = SimpleNamespace(**vars(base))
    runner.native_calls = calls
    runner.write_json = calls.sink.write
    runner.run_role = lambda *a, **kw: base.run_role(*a, **kw, persist=calls.sink.write, native=calls)
    runner.consume_interventions = lambda *a, **kw: base.consume_interventions(*a, **kw, persist=calls.sink.write)
    runner.commit_stage_result = lambda *a, **kw: base.commit_stage_result(
        *a, **kw, persist=calls.sink.write, native=calls
    )
    runner.commit_boundary_candidate = lambda *a, **kw: base.commit_boundary_candidate(
        *a, **kw, persist=calls.sink.write, native=calls
    )
    runner.commit_user_action = lambda *a, **kw: base.commit_user_action(
        *a, **kw, persist=calls.sink.write, native=calls
    )
    runner.chat_checkpoint = lambda *a, **kw: base.chat_checkpoint(*a, **kw, persist=calls.sink.write, native=calls)
    runner.rotate_if_needed = lambda *a, **kw: base.rotate_if_needed(*a, **kw, persist=calls.sink.write)
    runner.approve_goal = lambda state, selected: calls.approve(base.lifecycle.approve, state, selected)
    runner.abandon_stage = lambda *a, **kw: calls.abandon_stage(*a, **kw)
    runner.recover_default_budget = lambda *a, **kw: False
    # Reached only after run_role returned, so the launch already recorded its finish.
    runner.reject_completed_stage = lambda state, run_dir, record, error: calls.reject(
        state, record, error, closed=True
    )
    # The bounded report-only repair keeps this invocation's writer and launch owner, and a
    # resume never renews its attempt bound (--retry-report stays refused before setup).
    runner.execute_report_repair = lambda *a, **kw: base.execute_report_repair(*a, **kw, native=calls)
    runner.accept_repaired_report = lambda *a: base.repaired_result.accept(runner, *a)
    runner.apply_result = lambda *a, **kw: base.apply_result(*a, **kw, native=calls)
    runner.reset_report_repair_for_resume = lambda state: None
    return runner
