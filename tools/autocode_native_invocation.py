"""Invocation-local native planning wiring through existing CLI dependencies.

No controller imports, global observer registry, new process tree or private
state reader. The CLI supplies its original runner and durable ownership seams.
"""

try:
    from . import autocode_native_calls as native_calls
    from . import autocode_native_launch as native_launch
    from . import autocode_visual_profile as native_profile
except ImportError:
    import autocode_native_calls as native_calls
    import autocode_native_launch as native_launch
    import autocode_visual_profile as native_profile


def resolve_and_run(base, args, parser, *, failure_routing=True):
    args._native_intent = (
        native_profile.initial_intent(args.task) if args.task and not args.status and not args.dry_run else None
    )
    if args._native_intent is not None and any(
        getattr(args, key, None) for key in ("task_preflight", "ui_run", "figma_file")
    ):
        raise base.support.Paused(
            "PAUSED_VISUAL_EVIDENCE",
            "Native initial planning cannot use a preflight or generated handoff before its original owned origin",
        )
    resolved = base.run_setup.resolve(base, args, parser)
    if isinstance(resolved, int):
        return resolved
    return run(base, args, parser, resolved, failure_routing=failure_routing)


def run(base, args, parser, resolved, *, failure_routing=True):
    workspace, run_dir, state_path, state = resolved
    intent = native_profile.initial_intent(state["task"])
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
        if intent is None:
            select_provider(base, args, parser, state)
            return base.run_setup.run_locked(
                base, args, parser, state, state_path, run_dir, workspace, failure_routing=failure_routing
            )
        # The existing loop receives the same checkout, not a second lock/tree.
        with base.checkout_lock.exclusive(workspace, run_dir) as checkout_handle:
            current = base.source_scope.snapshot(workspace, state, base_snapshot=base.support.snapshot)
            context = native_launch.NativeRunContext(
                workspace,
                run_dir,
                run_handle=run_handle,
                checkout_handle=checkout_handle,
                start_state=state,
                snapshot=current,
                activation_intent={"task": state["task"], "profile": intent},
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
            select_provider(base, args, parser, state)
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


def select_provider(base, args, parser, state):
    saved = dict(state.get("settings") or {})
    if state.get("settings") and "provider" not in saved:
        saved["provider"] = "opencode"
    try:
        selected = base.autocode_providers.select(
            args.provider, saved, default="opencode" if args.engine == "codex" else None
        )
        base.opencode = base.autocode_providers.resolve(selected)
    except (RuntimeError, ValueError) as error:
        parser.error(str(error))


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
    runner.recover_default_budget = lambda *a, **kw: False
    runner.reject_completed_stage = lambda state, run_dir, record, error: calls.reject(state, record, error)
    return runner
