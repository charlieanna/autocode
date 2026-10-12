"""Export a dispatched bounded repair as a standalone handoff and adopt an external implementation.

The classification/review that dispatches a bounded repair leaves it only in run
state (issue #969): the export turns that dispatch into a standalone spec any
executor can implement, and the submit path re-runs the exact verification the
in-loop Builder would have faced before the repaired milestone advances. The
workspace stays at the dispatched base; the implementation arrives as a separate
checkout, is imported only within the repair's affected paths, and is rolled back
on any rejection. External provenance lands in user_events, never in a stage.
"""

import copy
import os
import shutil
from pathlib import Path

try:
    from . import autocode_milestones as milestones
    from . import autocode_planning as planning
    from . import autocode_regression as regression
    from . import autocode_source_scope as source_scope
    from . import autocode_source_snapshot as source_snapshot
    from . import autocode_util as util
    from . import autocode_verification_plan as verification_plan
    from . import autocode_verify as verify
except ImportError:
    import autocode_milestones as milestones
    import autocode_planning as planning
    import autocode_regression as regression
    import autocode_source_scope as source_scope
    import autocode_source_snapshot as source_snapshot
    import autocode_util as util
    import autocode_verification_plan as verification_plan
    import autocode_verify as verify

VERSION = 1
KIND = "repair-spec"
SPEC_NAME = "repair-spec.json"
DOC_NAME = "repair-spec.md"
DELIVERY = (
    "Deliver the completed implementation as a checkout of this repository at the dispatched base "
    "revision with only this repair applied (a git worktree is ideal), keeping the run workspace "
    "untouched at that base. Then run `autocode --run-dir RUN --submit-repair CHECKOUT`: the runner "
    "imports the changed files inside the repair's affected paths, re-runs the static gates and the "
    "regression proof (including the fail-on-base property), and advances or rejects the milestone."
)


def dispatched(state):
    """The bounded repair a paused classification/review dispatched, or None."""
    if not str(state.get("status") or "").startswith("PAUSED_"):
        return None
    if not state.get("resolution_request"):
        return None
    task = state.get("current_task") or {}
    if not (task.get("requirements") and task.get("acceptance_criteria") and task.get("validation_plan")):
        return None
    return task if task.get("contract_hash") else None


def _require(state):
    task = dispatched(state)
    if task is None:
        raise ValueError(
            "a repair handoff needs a run paused at a dispatched bounded repair "
            "(resolution_request + current_task with requirements, acceptance criteria and a validation plan)"
        )
    return task


def spec(state):
    """The standalone, machine-readable handoff for the dispatched bounded repair."""
    task = _require(state)
    body = (state.get("goal_contract") or {}).get("body") or {}
    ids = set(task.get("acceptance_criteria") or [])
    request = state.get("resolution_request") or {}
    regression_settings = (state.get("settings") or {}).get("regression") or {}
    return {
        "version": VERSION,
        "kind": KIND,
        "task_id": task.get("id"),
        "milestone_id": task.get("milestone_id"),
        "milestone_ids": list(task.get("milestone_ids") or []),
        "decision": task.get("decision"),
        "contract_hash": task.get("contract_hash"),
        "contract_revision": task.get("contract_revision"),
        "source_revision": task.get("source_revision"),
        "repair_instruction": {
            "objective": task.get("objective"),
            "requirements": list(task.get("requirements") or []),
        },
        "affected_paths": [str(path) for path in task.get("affected_paths") or []],
        "acceptance_criteria": [
            {
                "id": row.get("id"),
                "criterion": row.get("criterion"),
                "verification_method": row.get("verification_method"),
                "human_review": bool(row.get("human_review")),
            }
            for row in body.get("acceptance_criteria") or []
            if row.get("id") in ids
        ],
        "validation_plan": list(task.get("validation_plan") or []),
        "verification_commands": verification_plan.approved_commands(state),
        "obligations": verification_plan.obligations(state),
        "regression": {
            key: regression_settings[key]
            for key in ("test_command", "regression_command", "test_root")
            if regression_settings.get(key)
        },
        "dispatch": {
            "source_stage": request.get("source_stage"),
            "provenance": request.get("provenance"),
            "request_task_id": request.get("task_id"),
        },
        "delivery": DELIVERY,
    }


def render(specification):
    """Human-readable twin of the spec JSON."""
    lines = [
        f"# Repair handoff: {specification.get('task_id')}",
        "",
        f"Milestone: {specification.get('milestone_id')} (batch {', '.join(specification.get('milestone_ids') or []) or 'single'})",
        f"Contract: {specification.get('contract_hash')} (revision {specification.get('contract_revision')})",
        f"Base source revision: {specification.get('source_revision')}",
        "",
        "## Repair instruction",
        "",
        str(specification.get("repair_instruction", {}).get("objective") or ""),
        "",
    ]
    for requirement in specification.get("repair_instruction", {}).get("requirements") or []:
        lines.append(f"- {requirement}")
    lines += ["", "## Affected paths", ""]
    lines += [f"- {path}" for path in specification.get("affected_paths") or []]
    lines += ["", "## Acceptance criteria", ""]
    for row in specification.get("acceptance_criteria") or []:
        lines.append(f"### {row.get('id')}")
        lines += ["", str(row.get("criterion") or ""), ""]
        if row.get("verification_method"):
            lines += [f"Verification: {row['verification_method']}", ""]
        if row.get("human_review"):
            lines += ["Requires human review.", ""]
    lines += ["## Verification commands", ""]
    lines += [f"- `{command}`" for command in specification.get("verification_commands") or []]
    if not specification.get("verification_commands"):
        lines.append("- (none extracted; see the acceptance criteria methods and the validation plan)")
    regression = specification.get("regression") or {}
    if regression:
        lines += ["", "## Regression gates", ""]
        lines += [f"- {key}: `{value}`" for key, value in sorted(regression.items())]
    lines += ["", "## Delivery", "", str(specification.get("delivery") or DELIVERY), ""]
    return "\n".join(lines)


def export(state, run_dir):
    """Write handoff/repair-spec.json and .md beside the run; run state is untouched."""
    specification = spec(state)
    handoff = Path(run_dir) / "handoff"
    handoff.mkdir(exist_ok=True)
    json_path = handoff / SPEC_NAME
    util.atomic_json(json_path, specification)
    doc_path = handoff / DOC_NAME
    doc_path.write_text(render(specification) + "\n")
    return {"spec": str(json_path), "readme": str(doc_path)}


def _handoff_matches(state, run_dir):
    path = Path(run_dir) / "handoff" / SPEC_NAME
    if not path.is_file():
        raise ValueError("no exported handoff beside this run; run --export-repair first")
    try:
        saved = util.read_object(path)
    except (OSError, ValueError, TypeError) as error:
        raise ValueError(f"cannot read the exported handoff: {error}") from error
    if saved != spec(state):
        raise ValueError("the exported handoff no longer matches the dispatched repair; run --export-repair again")


def _changed_files(workspace, external, task):
    selected = [str(path) for path in task.get("affected_paths") or []]
    before = source_snapshot.snapshot(workspace, paths=selected)
    after = source_snapshot.snapshot(external, paths=selected)
    changed = util.changed_paths(before, after)
    if not changed:
        raise ValueError("the delivered checkout contains no changes against the dispatched base")
    scope = [str(path).rstrip("/") for path in task.get("affected_paths") or []]
    outside = [name for name in changed if not any(name == root or name.startswith(root + "/") for root in scope)]
    if outside:
        raise ValueError("changes outside the dispatched repair's affected paths: " + ", ".join(outside))
    return changed, after["files"]


def _backup(path):
    if path.is_symlink():
        return ("symlink", os.readlink(path))
    if path.is_file():
        return ("file", path.read_bytes())
    return ("absent", None)


def _place(delivered, target, expected):
    if expected == "deleted":
        target.unlink(missing_ok=True)
        return
    if expected.startswith("symlink:"):
        target.parent.mkdir(parents=True, exist_ok=True)
        target.unlink(missing_ok=True)
        target.symlink_to(expected[len("symlink:") :])
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(delivered, target)
    observed = ("executable:" if target.stat().st_mode & 0o111 else "") + util.file_hash(target)
    if observed != expected:
        raise ValueError("source changed while importing the delivered repair: " + str(target))


def _restore(workspace, staged):
    for name, (kind, value) in staged.items():
        path = Path(workspace) / name
        path.unlink(missing_ok=True)
        if kind == "symlink":
            path.symlink_to(value)
        elif kind == "file":
            path.write_bytes(value)


def _command_criteria(state):
    body = (state.get("goal_contract") or {}).get("body") or {}
    task = state.get("current_task") or {}
    ids = sorted(task.get("acceptance_criteria") or [])
    mapping = {}
    for row in body.get("acceptance_criteria") or []:
        if row.get("human_review") or (ids and row.get("id") not in ids):
            continue
        for command in verification_plan.commands(row.get("verification_method", "")):
            mapping.setdefault(command, set()).add(str(row.get("id")))
    for method in task.get("validation_plan") or []:
        for command in verification_plan.commands(method):
            mapping.setdefault(command, set()).update(ids)
    return {command: sorted(found) for command, found in mapping.items()}


def _verify(state, workspace, run_dir):
    """Run the static gates and the regression proof; return (summary, rejection reason)."""
    mapping = _command_criteria(state)
    logs = Path(run_dir) / "handoff" / "verification"
    logs.mkdir(parents=True, exist_ok=True)
    results = []
    for index, command in enumerate(verification_plan.approved_commands(state)):
        receipt = verify.run_command(command, workspace, logs / f"static-{index:02d}.log")
        results.append({"command": command, "exit_code": receipt.get("exit_code")})
        if receipt.get("exit_code"):
            ids = mapping.get(command) or []
            where = "criterion " + ", ".join(ids) if ids else "the dispatched validation plan"
            return {"commands": results}, f"{where} failed: `{command}` exited {receipt.get('exit_code')}"
    summary = {"commands": results}
    if regression.required(state):
        proof = regression.prove(state, workspace, run_dir)
        summary["regression"] = {
            key: proof.get(key)
            for key in ("verdict", "failures", "unverified", "fail_to_pass", "pass_to_pass", "case_scope")
            if key in proof
        }
        revision = source_scope.snapshot(workspace, state)["revision"]
        if not regression.complete(state, revision, all_due=True):
            return summary, regression.rejection(state)
    return summary, ""


def _record(state, kind, external, changed, task, summary, reason=""):
    state.setdefault("user_events", []).append(
        {
            "kind": kind,
            "actor": "user_cli",
            "at": util.now(),
            "external_dir": str(external),
            "changed_paths": list(changed),
            "contract_hash": task.get("contract_hash"),
            "contract_revision": task.get("contract_revision"),
            "source_revision": task.get("source_revision"),
            "verification": copy.deepcopy(summary),
            "criterion": reason,
        }
    )


def submit(state, run_dir, workspace, external_dir):
    """Adopt an externally implemented repair after re-running its verification obligations.

    Returns {"accepted": bool, "message": str}. Gate refusals raise ValueError
    before any state or source change; a rejected verification rolls the
    workspace back to the base and records the rejection provenance.
    """
    task = _require(state)
    _handoff_matches(state, run_dir)
    workspace = Path(workspace).resolve()
    current = source_scope.snapshot(workspace, state)
    if current["revision"] != task.get("source_revision"):
        raise ValueError(
            "the workspace is no longer at the dispatched base; the repair must be delivered against "
            f"{task.get('source_revision')} (workspace is {current['revision']})"
        )
    external = Path(external_dir).resolve()
    if external == workspace:
        raise ValueError(
            "deliver the repair in a separate checkout; the run workspace must stay at the dispatched base"
        )
    if not external.is_dir():
        raise ValueError("the delivered implementation directory does not exist")
    changed, delivered = _changed_files(workspace, external, task)
    staged = {name: _backup(Path(workspace) / name) for name in changed}
    try:
        for name in changed:
            _place(Path(external) / name, Path(workspace) / name, delivered.get(name, "deleted"))
        summary, reason = _verify(state, workspace, run_dir)
    except BaseException:
        _restore(workspace, staged)
        raise
    if reason:
        _restore(workspace, staged)
        _record(state, "external_repair_rejected", external, changed, task, summary, reason)
        return {"accepted": False, "message": f"External repair rejected: {reason}"}
    _record(state, "external_repair_accepted", external, changed, task, summary)
    milestones.accept(state, source_scope.snapshot(workspace, state))
    archived = {
        **copy.deepcopy(task),
        "completed_by": "external_repair",
        "completed_at": util.now(),
        "verification": copy.deepcopy(summary),
        "external_dir": str(external),
        "changed_paths": list(changed),
    }
    state.setdefault("task_archive", []).append(archived)
    state.pop("current_task", None)
    state.pop("resolution_request", None)
    state.update(
        status="RUNNING", phase="PLANNING" if planning.is_planning(state, state.get("next_stage")) else "EXECUTING"
    )
    state.pop("stop_reason", None)
    return {
        "accepted": True,
        "message": f"External repair accepted: {len(changed)} path(s) verified; the milestone advanced. "
        "Resume the run to continue.",
    }
