"""Naming and reservation of per-stage artifacts under a run's iterations/ tree.

Artifact file stems use readable unique slugs (the Builder's are builder-01.jsonl,
once terra-01.jsonl). A launch reserves its stem only when neither the slug nor
the stem under the stage's old code name (autocode_names.old_stage) holds
artifacts, so a resume across the naming change cannot overlook an uncertain
prior attempt.
"""

from __future__ import annotations

from pathlib import Path

try:
    from . import autocode_names as names
    from . import autocode_support as support
except ImportError:
    import autocode_names as names
    import autocode_support as support

# One unique stem per stage code name (autocode_names.FILE_SLUGS).
FILE_SLUGS = names.FILE_SLUGS

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
    legacy = base.parent / f"{names.old_stage(stage)}-{attempt:02d}"
    for name in {base.name, legacy.name}:
        for suffix in RESERVED_SUFFIXES:
            if (base.parent / (name + suffix)).exists():
                raise support.Paused(
                    "PAUSED_UNCERTAIN_STAGE", f"Existing stage artifacts require reconciliation: {base.parent / name}"
                )
    return base
