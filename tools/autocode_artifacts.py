"""Naming and reservation of per-stage artifacts under a run's iterations/ tree.

Stage code names stay in state, prompts and code; artifact file stems use
readable unique slugs (terra-01.jsonl becomes builder-01.jsonl). A launch
reserves its stem only when neither the new slug nor the legacy code-name stem
holds artifacts, so a resume across the naming change cannot overlook an
uncertain prior attempt.
"""

from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_support as support
except ImportError:
    import autocode_support as support

# One unique stem per stage code name: two stages may share a human role
# (astra_review and astra_checkpoint are both the Completion Owner). The stems of the stages
# this runtime no longer runs (autocode_workflows.RETIRED_STAGES) stay, so the artifacts a saved
# run wrote at them are still found and read.
FILE_SLUGS = {
    "recognize_workflow": "recognize-workflow",
    "collect_design": "design-inventory",
    "requirements_gather": "requirements-gather",
    "draft_plan": "discovery",
    "revise_plan": "requirements-revise",
    "review_plan": "plan-challenge",
    "finalize_plan": "plan-finalize",
    "plan_task": "task-plan",
    "plan_review": "plan-review",
    "builder": "builder",
    "tester": "validator",
    "orchestrator": "orchestrator",
    "review_change": "review-change",
    "review_design": "design-review",
    "check_design": "design-check",
    "investigate_stuck": "stuck-investigation",
    "investigate_bug": "bug-investigation",
    "answer_question": "analyst",
    "review_completion": "completion-review",
    "review_checkpoint": "completion-checkpoint",
    "resolve": "resolver",
    "diagnose": "diagnosis",
    "write_acceptance_tests": "acceptance-tests",
}

REPAIR_SUFFIX = "_report_repair"
RESERVED_SUFFIXES = (".json", ".jsonl", ".prompt.md")


def slug(stage: str) -> str:
    """File-stem prefix for a stage: 'builder' -> 'builder', 'builder_report_repair' -> 'builder-report-repair'."""
    repair = str(stage or "").endswith(REPAIR_SUFFIX)
    base = str(stage or "").removesuffix(REPAIR_SUFFIX)
    name = FILE_SLUGS.get(base, base.replace("_", "-"))
    return name + ("-report-repair" if repair else "")


def stage_base(run_dir, iteration, stage, attempt) -> Path:
    return Path(run_dir) / "iterations" / f"{iteration:03d}" / f"{slug(stage)}-{attempt:02d}"


def reserve(run_dir, iteration, stage, attempt) -> Path:
    """Return this attempt's artifact base, or pause when new or legacy artifacts already exist."""
    base = stage_base(run_dir, iteration, stage, attempt)
    legacy = base.parent / f"{stage}-{attempt:02d}"
    for name in {base.name, legacy.name}:
        for suffix in RESERVED_SUFFIXES:
            if (base.parent / (name + suffix)).exists():
                raise support.Paused(
                    "PAUSED_UNCERTAIN_STAGE", f"Existing stage artifacts require reconciliation: {base.parent / name}"
                )
    return base
