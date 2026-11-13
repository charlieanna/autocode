"""Observed input identity for paired comparisons, not authenticated execution proof.

Receipts belong to the comparison, not ordinary scenario admissions. Rebuilding
uses only their saved observations; it never fills gaps from today's checkout.
"""

from __future__ import annotations

import copy
import hashlib
import json
import stat
from pathlib import Path
from typing import Any

from . import pinned_seed, profiles

OPTIONS = (
    "fake",
    "profile",
    "fake_solution",
    "autocode",
    "jobs",
    "repeats",
    "max_steps",
    "timeout_minutes",
    "max_seconds",
    "max_stage_seconds",
    "max_iterations",
    "provider",
    "hybrid",
    "fake_fault",
    "local_docker",
)
TreeRow = tuple[str, str, str | None]


def _digest(value) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()


def _tree(root: Path, label: str, *, optional=False) -> tuple[list[TreeRow], list[str]]:
    rows: list[TreeRow] = []
    reasons: list[str] = []

    def visit(path):
        name = f"{label}/{path.relative_to(root).as_posix()}"
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode):
            reasons.append(f"workload contains symlink: {name}")
        elif stat.S_ISDIR(info.st_mode):
            rows.append((name, "040000", None))
            for child in sorted(path.iterdir()):
                if child.name != "__pycache__" or not stat.S_ISDIR(child.lstat().st_mode):
                    visit(child)
        elif stat.S_ISREG(info.st_mode):
            mode = "100755" if info.st_mode & 0o111 else "100644"
            rows.append((name, mode, hashlib.sha256(path.read_bytes()).hexdigest()))
        else:
            reasons.append(f"workload contains special file: {name}")

    try:
        if optional and not root.exists() and not root.is_symlink():
            rows.append((label, "absent", None))
        else:
            visit(root)
    except OSError as error:
        reasons.append(f"workload unreadable: {label}: {error}")
    return rows, reasons


def workload(scenario) -> dict:
    """Hash source inputs, including shared oracle helpers and the consumed pinned seed.

    Only generated Python caches are excluded. Symlinks and special files stay
    unknown rather than reading a target or blocking on a pipe. These boundary
    observations detect persistent edits, not edit-and-restore races.
    """
    rows, reasons = _tree(scenario.dir, "definition")
    helpers, helper_reasons = _tree(Path(__file__).parent, "harness")
    rows.extend(helpers)
    reasons.extend(helper_reasons)
    seed_rows: list[TreeRow] = []
    try:
        seed_rows, seed_reasons = _tree(scenario.seed, "seed", optional=True)
        rows.extend(seed_rows)
        reasons.extend(seed_reasons)
        rows.append(("resolved_brief", "text", hashlib.sha256(scenario.brief.encode()).hexdigest()))
    except (OSError, ValueError, TypeError, AttributeError, pinned_seed.SeedUnavailable) as error:
        reasons.append(f"workload unavailable: {error}")
    return {
        "sha256": _digest(sorted(rows)) if not reasons else None,
        "files": len(rows),
        "reasons": reasons,
        "seed_sha256": {name.removeprefix("seed/"): pin for name, mode, pin in seed_rows if pin is not None},
    }


def configuration(args, scenario, extra_flags) -> dict:
    card = json.loads(args.rate_card.read_text()) if args.rate_card else None
    solution = Path(args.fake_solution)
    outside = (args.fake or getattr(args, "hybrid", False)) and (solution.is_absolute() or ".." in solution.parts)
    return {
        "options": copy.deepcopy({key: getattr(args, key, None) for key in OPTIONS}),
        "max_steps": args.max_steps or scenario.max_steps,
        "timeout_minutes": args.timeout_minutes or scenario.timeout_minutes,
        "extra_flags": list(extra_flags),
        "rate_card_sha256": _digest(card),
        "scripted_solution_supported": not outside,
    }


def observe(scenario, args, extra_flags, revision_reader) -> dict:
    reasons: list[str] = []
    observation: dict[str, Any] = {"workload": workload(scenario), "reasons": reasons}
    try:
        observation["revision"] = copy.deepcopy(revision_reader()) if revision_reader else None
        observation["profile"] = copy.deepcopy(profiles.resolve(args.profile)) if not args.fake else None
        observation["configuration"] = configuration(args, scenario, extra_flags)
    except (OSError, ValueError, TypeError) as error:
        reasons.append(f"input observation unavailable: {error}")
    return observation


def qualification(protocol: dict, row: dict) -> dict:
    reasons, unknown = [], []

    def missing(reason):
        unknown.append(reason)

    def revision(actual, expected, label):
        if not isinstance(actual, dict) or not _hex(actual.get("commit"), 40) or type(actual.get("dirty")) is not bool:
            missing(f"{label} not recorded")
        elif actual["dirty"]:
            missing(f"{label} is dirty")
        elif isinstance(expected, dict) and actual.get("commit") != expected.get("commit"):
            reasons.append(f"{label} differs from declared revision")

    def inputs(actual, expected, label):
        if not isinstance(actual, dict) or not _hex(actual.get("sha256"), 64):
            details = actual.get("reasons") if isinstance(actual, dict) else None
            missing(f"{label} unknown" + (f": {details}" if details else ""))
        elif isinstance(expected, dict) and actual != expected:
            reasons.append(f"{label} differs from declared workload")

    if type(protocol.get("schema_version")) is not int or protocol["schema_version"] != 2:
        missing("comparison protocol identity version unknown")
    if type(protocol.get("fake")) is not bool:
        missing("comparison execution mode unknown")
    expected_revision = protocol.get("revision")
    revision(expected_revision, expected_revision, "declared revision")
    workloads, configurations = protocol.get("workloads"), protocol.get("configurations")
    expected_workload = workloads.get(row["scenario"]) if isinstance(workloads, dict) else None
    inputs(expected_workload, expected_workload, "declared workload")
    scenario_configs = configurations.get(row["scenario"]) if isinstance(configurations, dict) else None
    expected_config = scenario_configs.get(row["variant"]) if isinstance(scenario_configs, dict) else None
    if not isinstance(expected_config, dict) or not isinstance(expected_config.get("options"), dict):
        missing("declared configuration unknown")
        expected_config = {}
    if expected_config.get("scripted_solution_supported") is not True:
        missing("external or escaping scripted solution identity unknown")
    if protocol.get("autocode") is not None:
        missing("external AutoCode command runtime identity unknown")
    expected_profile = protocol.get("profile")
    if protocol.get("fake") is False and not isinstance(expected_profile, dict):
        missing("declared profile unknown")
    receipt = row.get("comparison_identity")
    if (
        not isinstance(receipt, dict)
        or type(receipt.get("schema_version")) is not int
        or receipt["schema_version"] != 1
    ):
        missing("comparison receipt identity unknown")
        receipt = {}
    for boundary in ("before", "after"):
        observed = receipt.get(boundary)
        if not isinstance(observed, dict):
            missing(f"{boundary} input observation missing")
            continue
        if observed.get("reasons"):
            missing(f"{boundary} input observation incomplete: {observed['reasons']}")
        revision(observed.get("revision"), expected_revision, f"{boundary} observed revision")
        inputs(observed.get("workload"), expected_workload, f"{boundary} workload")
        actual_config = observed.get("configuration")
        if not isinstance(actual_config, dict):
            missing(f"{boundary} configuration unknown")
        elif actual_config != expected_config:
            reasons.append(f"{boundary} configuration differs from declaration")
        if "profile" not in observed:
            missing(f"{boundary} profile unknown")
        elif observed["profile"] != expected_profile:
            reasons.append(f"{boundary} observed profile differs from declaration")
    revision(row.get("autocode"), expected_revision, "result revision")
    if protocol.get("fake") is False:
        if not isinstance(row.get("profile"), dict):
            missing("result profile unknown")
        elif row["profile"] != expected_profile:
            reasons.append("result profile differs from declaration")
    options = expected_config.get("options", {})
    if options:
        fake = options.get("fake")
        solution = options.get("fake_solution")
        if type(fake) is not bool or not isinstance(solution, str):
            missing("declared result mode unknown")
        else:
            expected_mode = (
                ("fake" if solution == "reference" else f"fake-{Path(solution).name}")
                if fake
                else options.get("profile")
            )
            if not isinstance(row.get("mode"), str):
                missing("result mode unknown")
            elif row["mode"] != expected_mode:
                reasons.append("result mode differs from declaration")
        if options.get("autocode") is not None:
            missing("external AutoCode command runtime identity unknown")
    return {"status": "unknown" if unknown else "mismatch" if reasons else "qualified", "reasons": unknown + reasons}


def _hex(value, length):
    return isinstance(value, str) and len(value) == length and all(char in "0123456789abcdef" for char in value)
