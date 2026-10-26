"""Workflow-owned stages ("jobs"): stages that belong to one workflow rather than to the build pipeline.

Each job module is pure (prompt, schema, transition, rendering) and has
``STAGE``, ``apply(state, value, record, workspace)``, ``owns(state)`` (the run
ended in this job) and ``render(state)``. This table is what the runner and
Autopilot consult, so adding a job means adding it here, not editing them.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_tests_job as acceptance_tests_job
    from . import autocode_approval_provenance as approval_provenance
    from . import autocode_bug_job as bug_job
    from . import autocode_cost as cost
    from . import autocode_design_check_job as design_check_job
    from . import autocode_design_intake as design_intake
    from . import autocode_design_job as design_job
    from . import autocode_discuss_job as discuss_job
    from . import autocode_prompts as prompts
    from . import autocode_review_job as review_job
    from . import autocode_stuck_job as stuck_job
except ImportError:
    import autocode_acceptance_tests_job as acceptance_tests_job
    import autocode_approval_provenance as approval_provenance
    import autocode_bug_job as bug_job
    import autocode_cost as cost
    import autocode_design_check_job as design_check_job
    import autocode_design_intake as design_intake
    import autocode_design_job as design_job
    import autocode_discuss_job as discuss_job
    import autocode_prompts as prompts
    import autocode_review_job as review_job
    import autocode_stuck_job as stuck_job

JOBS = (review_job, bug_job, design_job, discuss_job, design_check_job, stuck_job, design_intake, acceptance_tests_job)
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
    acceptance_tests_job.STAGE: "autoreview",
}
STAGES = tuple(UNIT)

# The jobs whose report carries evidence rows the runner probes. `investigate_stuck` is not here: its
# repair prompt already states its own evidence and probe-path policy (autocode_stuck_repair_context).
PROBING_STAGES = frozenset({discuss_job.STAGE, bug_job.STAGE, design_job.STAGE, design_check_job.STAGE})
# The probing jobs whose probes must also fail on a deliberately constructed counterexample (#1769).
COUNTEREXAMPLE_STAGES = frozenset({discuss_job.STAGE, design_job.STAGE, design_check_job.STAGE})


def ended_in(state: RunState):
    """The job that ended this run, or None when it ended in the build pipeline."""
    return next((job for job in JOBS if job.owns(state)), None)


def render(state: RunState, fallback) -> str:
    """The completion summary: the job's own, or ``fallback(state)`` for a build-pipeline completion,
    then who approved its plan and reviews, edited the plan, resumed it from a pause or accepted its
    completion, or that no approval was recorded when its workflow can ask for one (#722), ending with
    the run's cost line (#721)."""
    job = ended_in(state)
    summary = (job.render(state) if job else fallback(state)).rstrip("\n")
    approvals = approval_provenance.lines(state)
    return "\n".join([summary, *(["", *approvals] if approvals else []), cost.line(cost.view(state))])


def repair_rules(stage: str) -> str:
    """The job's own report rules for a report-only repair of its stage, or "" when it has none.

    A probing stage also gets what a `probe` is. The repair prompt carries the report schema, where a probe
    is only a string, and a model that guesses wrote an event ID, which the runner then handed to the shell
    and lost a call over (#1769)."""
    rules = next((getattr(job, "REPAIR_RULES", "") for job in JOBS if stage == job.STAGE), "")
    if stage not in PROBING_STAGES:
        return rules
    probe = prompts.get("fragments/report-repair-context/evidence-probe.md")
    if stage in COUNTEREXAMPLE_STAGES:
        probe += prompts.get("fragments/report-repair-context/evidence-counterexample.md")
    return f"{rules}\n{probe}" if rules else probe


def repair_context(stage: str, state: RunState) -> dict:
    """The handoff data a report-only repair of the job's stage needs beyond the rejected report (the
    review a revision must keep, for the Architect), or {} when the job has none."""
    job = next((job for job in JOBS if stage == job.STAGE), None)
    if job is None:
        return {}
    return job.repair_context(state) if hasattr(job, "repair_context") else {}
