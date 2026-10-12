"""Edits that apply to a saved run on their own (a step of autocode_run_actions.handle).

A design reference revision (--revise-figma-manifest), a dependency delivery, an inspected
Investigator report (--recover-job-report) and a repair handoff (--export-repair, --submit-repair)
each apply and end the invocation; nothing builds. A conversation handoff and the expected goal
token are checked and recorded, and the invocation goes on.

Like every step of autocode_run_actions.handle, its steps take the invocation's arguments and
``decided`` (what earlier steps established), return an exit code to stop or None to go on, and
call the runner's own functions as runner.X, so tests that patch the runner still reach them.
Only autocode_run_actions imports it.
"""

from __future__ import annotations

import sys

try:
    from . import autocode_conversation_ingress as conversation_ingress
    from . import autocode_dependency as dependency
    from . import autocode_design_revision as design_revision
    from . import autocode_goals as goals
    from . import autocode_job_report_recovery as job_report_recovery
    from . import autocode_pause_authority as pause_authority
    from . import autocode_repair_handoff as repair_handoff
    from . import autocode_resolver_human as resolver_human
    from . import autocode_source_scope as source_scope
    from . import autocode_support as support
except ImportError:
    import autocode_conversation_ingress as conversation_ingress
    import autocode_dependency as dependency
    import autocode_design_revision as design_revision
    import autocode_goals as goals
    import autocode_job_report_recovery as job_report_recovery
    import autocode_pause_authority as pause_authority
    import autocode_repair_handoff as repair_handoff
    import autocode_resolver_human as resolver_human
    import autocode_source_scope as source_scope
    import autocode_support as support


def apply(runner, args, parser, state, state_path, run_dir, workspace, decided):
    """Apply a standalone edit and stop, or record the conversation handoff and go on."""
    if args.revise_figma_manifest:
        try:
            metadata = runner.intervention_metadata(workspace, run_dir, state)
            if metadata["pending_count"] or metadata["inbox_error"]:
                raise ValueError("Apply queued interventions before revising design references")
            # A revision restarts plan review in place of the pause, like an edited goal (#486 review).
            refusal = pause_authority.correction_refusal(state, "A design reference revision")
            if refusal:
                raise ValueError(refusal)
            scope_arguments = {
                "approved_scope": getattr(args, "figma_approved_scope", None),
                "approved_scope_sha256": getattr(args, "figma_approved_scope_sha256", None),
            }
            design_revision.apply(
                state,
                design_revision.manifest.load(args.revise_figma_manifest, **scope_arguments),
                args.expected_design_hash,
                args.design_change_reason,
                workspace,
            )
        except (ValueError, OSError) as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        runner.write_json(state_path, state)
        print(state["stop_reason"])
        return 0
    try:
        conversation_ingress.require_expected_goal(
            state, getattr(args, "expected_goal_token", None), token_for=goals.token, is_approved=goals.approved
        )
        if conversation_ingress.ingest(
            state,
            run_dir,
            workspace,
            getattr(args, "conversation_handoff", None),
            existing_run=bool(args.run_dir),
            core_session=getattr(args, "conversation_session", False),
        ):
            runner.write_json(state_path, state)
    except ValueError as error:
        print(f"Input rejected: {error}", file=sys.stderr)
        return 2
    dependency_result = dependency.apply(
        args,
        state,
        run_dir,
        resolver_human.current(state),
        runner.write_json,
        lambda: source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)["revision"],
    )
    if dependency_result is not None:
        return dependency_result
    if getattr(args, "recover_job_report", None) is not None:
        try:
            with runner.checkout_lock.exclusive(workspace, run_dir):
                job_report_recovery.apply(runner, state, run_dir, workspace, args.recover_job_report)
        except (ValueError, OSError, KeyError, RuntimeError) as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        print("Inspected Investigator report applied; provider exit remains unknown. No model launched.")
        return 0
    if args.export_repair or getattr(args, "submit_repair", None) is not None:
        try:
            if args.export_repair:
                written = repair_handoff.export(state, run_dir)
                print(f"Repair handoff written: {written['spec']}\n{written['readme']}")
                return 0
            verdict = repair_handoff.submit(state, run_dir, workspace, args.submit_repair)
        except (ValueError, OSError, KeyError) as error:
            print(f"Input rejected: {error}", file=sys.stderr)
            return 2
        runner.write_json(state_path, state)
        print(verdict["message"])
        return 0 if verdict["accepted"] else 2
