"""Workflow-owned stages ("jobs"): stages that belong to one workflow rather than to the build pipeline.

Each job module is pure (prompt, schema, transition, rendering) and has
``STAGE``, ``apply(state, value, record, workspace)``, ``owns(state)`` (the run
ended in this job) and ``render(state)``. This table is what the runner and
Autopilot consult, so adding a job means adding it here, not editing them.
"""

from __future__ import annotations

try:
    from . import autocode_bug_job as bug_job
    from . import autocode_design_check_job as design_check_job
    from . import autocode_design_intake as design_intake
    from . import autocode_design_job as design_job
    from . import autocode_discuss_job as discuss_job
    from . import autocode_review_job as review_job
    from . import autocode_stuck_job as stuck_job
except ImportError:
    import autocode_bug_job as bug_job
    import autocode_design_check_job as design_check_job
    import autocode_design_intake as design_intake
    import autocode_design_job as design_job
    import autocode_discuss_job as discuss_job
    import autocode_review_job as review_job
    import autocode_stuck_job as stuck_job

JOBS = (review_job, bug_job, design_job, discuss_job, design_check_job, stuck_job, design_intake)
# Which unit prepares and applies each job's stage (autopilot.unit_for). Units
# expose apply_job(stage, state, value, record, workspace).
UNIT = {
    review_job.STAGE: "autoreview",
    bug_job.STAGE: "autoresolver",
    design_job.STAGE: "autoreview",
    discuss_job.STAGE: "autoresolver",
    design_check_job.STAGE: "autoreview",
    stuck_job.STAGE: "autoresolver",
    design_intake.STAGE: "autoreview",
}
STAGES = tuple(UNIT)


def ended_in(state: dict):
    """The job that ended this run, or None when it ended in the build pipeline."""
    return next((job for job in JOBS if job.owns(state)), None)


def render(state: dict, fallback) -> str:
    """The completion summary: the job's own, or ``fallback(state)`` for a build-pipeline completion."""
    job = ended_in(state)
    return job.render(state) if job else fallback(state)


def repair_rules(stage: str) -> str:
    """The job's own report rules for a report-only repair of its stage, or "" when it has none."""
    return next((getattr(job, "REPAIR_RULES", "") for job in JOBS if stage == job.STAGE), "")


def repair_context(stage: str, state: dict) -> dict:
    """The handoff data a report-only repair of the job's stage needs beyond the rejected report (the
    review a revision must keep, for the Architect), or {} when the job has none."""
    job = next((job for job in JOBS if stage == job.STAGE), None)
    return job.repair_context(state) if hasattr(job, "repair_context") else {}
