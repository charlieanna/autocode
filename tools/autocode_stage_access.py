"""Where each stage may write: the one place the launch sandbox, the write guards and the prompts read.

Three mechanisms enforce what a stage may change in the workspace: the OS sandbox it is launched
with (units.common.launch_sandbox), the stray-write check each workflow job runs on the files the
attempt changed, and the guard on native OpenCode snapshots (autocode_readonly_events). Kept as
separate constants they drifted: the OpenCode guard carried a hand copy of the review job's prefix
(#332), and the judging stages' prompts required evidence writes their sandbox refused (#313).
docs/plans/stage-access.md lists the rules still restated elsewhere and the order they move here.

Imports nothing from AutoCode, so every layer can read it. Add to it rather than restating a rule.
"""
from __future__ import annotations

# Stages that never change source but must write runner-owned evidence under .autocode/: the
# capture_command receipts every Validator is promised, and their own report. They launch
# workspace-write; the runner's after-stage source snapshot, not the sandbox, keeps their source
# unchanged (#313).
JUDGING_STAGES = ("sol", "astra_review", "astra_checkpoint")

# Repository paths a workflow job's model may change, by stage. Any other change is a stray write:
# the job rejects the attempt and the runner puts the files back (autocode_stray_writes). A job not
# listed may change nothing; answer_question may change only the note its report names.
JOB_WRITES = {
    "review_change": ("review/",),
    "review_design": ("review/",),
    "investigate_bug": ("docs/bugs/",),
}

# What a native OpenCode snapshot may gain while a stage runs: new files only, proved with Git;
# an edit or deletion still pauses (#332). Always within that stage's JOB_WRITES.
OPENCODE_ADDITIONS = {
    "review_change": ("review/",),
}

# Where a workflow job keeps scratch copies and test output: inside the workspace, because OpenCode
# stops a stage that touches an external directory (#228, #301), and under .autocode/, which every
# source comparison ignores.
SCRATCH_ROOT = ".autocode/scratch/"


def job_writes(stage: str) -> tuple[str, ...]:
    """The repository path prefixes ``stage`` may change; empty when it may change nothing."""
    return JOB_WRITES.get(stage, ())


def stray(stage: str, changed_files) -> list[str]:
    """The changed paths ``stage`` was not allowed to change, sorted."""
    allowed = job_writes(stage)
    return sorted(str(path) for path in (changed_files or []) if not str(path).startswith(allowed))


def opencode_additions(stage: str) -> tuple[str, ...]:
    """The prefixes under which a native OpenCode snapshot of ``stage`` may gain new files."""
    return OPENCODE_ADDITIONS.get(stage.removesuffix("_report_repair"), ())


def scratch(stage: str) -> str:
    """The workspace-relative scratch directory a job's prompt names, e.g. .autocode/scratch/review_design."""
    return SCRATCH_ROOT + stage


def scratch_rule(stage: str) -> str:
    """The prompt sentence that tells a job where to run code without changing the workspace."""
    return (f"Make any scratch copy inside the workspace under {scratch(stage)}/ (the runner's before/after "
            "comparison ignores .autocode/). Never use /tmp, mktemp or another path outside the workspace: "
            "a provider may stop a stage that touches an external directory, and the attempt is lost.")
