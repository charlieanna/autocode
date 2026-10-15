"""Pure draft-task feasibility, separate from actual execution admission.

There is no approved contract, assignment, native proof or historical acceptance
here. A reviewed first task must be structurally assignable before showing it.
"""

try:
    from . import autocode_verification_plan as verification
except ImportError:
    import autocode_verification_plan as verification


def first_task(body, settings, *, workspace=None, owned_paths=None):
    spec = body["initial_task"]
    if spec["kind"] not in ("implement", "validate"):
        raise ValueError("CONTINUE or REWORK requires a concrete next task")
    for field in ("requirements", "acceptance_criteria", "validation_plan"):
        if not spec[field] or any(not entry.strip() for entry in spec[field]):
            raise ValueError("The bounded task needs " + field)
    if not spec["objective"].strip():
        raise ValueError("The bounded task needs an objective")
    ids = spec["acceptance_criteria"]
    if len(set(ids)) != len(ids) or not set(ids) <= {c["id"] for c in body["acceptance_criteria"]}:
        raise ValueError("Task criteria must reference the approved brief")
    milestones = {row["id"]: row for row in body.get("milestones", [])}
    owned = milestones.get(spec["milestone_id"], {})
    if milestones and (not owned or not set(ids) <= set(owned["acceptance_criteria"])):
        raise ValueError("Task must belong to an approved milestone and its acceptance criteria")
    paths = list(spec["affected_paths"])
    if owned_paths is not None:
        if not paths and spec["kind"] == "validate":
            paths = list(owned_paths)
    elif (paths or spec["kind"] == "validate") and owned.get("affected_paths"):
        paths = list(owned["affected_paths"])
    verification.require_scaffolding(workspace, paths, spec["validation_plan"])
    if settings.get("milestone_checkpoints", {}).get("enabled"):
        if not spec["milestone_id"].strip() or (not paths and spec["kind"] != "validate"):
            raise ValueError(
                "Milestone tasks require a named milestone and explicit affected paths: an implement "
                "task (initial_task in a plan) must list in affected_paths the repository files or "
                "directories it writes, even when its milestone's affected_paths is []"
            )
        if owned.get("depends_on"):
            raise ValueError("initial_task milestone has unmet prerequisites")
    return paths
