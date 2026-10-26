#!/usr/bin/env python3
"""Qualify the routes a live run proved, and write them into the selector's table (#2057 E).

Run by hand after a live run on a stock profile, then commit the table by pull request, as the
price table is. The script reads one finished run's saved state, checks that it is real evidence
(a live run, `TASK_COMPLETE`, with a recorded attempt for the role and model it would qualify)
and merges the reached route into `tools/autocode_route_qualification.json`.

It never launches a model, never answers anything and never invents a route: a role with no
recorded attempt is skipped and named, and so is a model the table excludes for its role even when
a profile pinned it. `--dry-run` prints the table it would write and the lines
a person would put in the PR; without it the file is rewritten and validated first.

    python tools/qualify_routes.py --run-dir .autocode/runs/RUN \
        --scenario greenfield-greeting-cli --profile glm-mimo-kilo
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_model_identity as identity
    from . import autocode_role_route as role_route
    from . import autocode_roles as roles
    from . import autocode_route_selector as selector
    from . import autocode_usage as usage
except ImportError:  # executed as a script, with tools/ on sys.path
    import autocode_model_identity as identity
    import autocode_role_route as role_route
    import autocode_roles as roles
    import autocode_route_selector as selector
    import autocode_usage as usage

TABLE = Path(__file__).with_name("autocode_route_qualification.json")
# Accounting flags that mean an attempt proves nothing about its route (autocode_usage.accounting).
FAILED = ("runner_owned", "rejected", "timed_out", "interrupted", "active", "report_only")


class NotEvidence(ValueError):
    """The run cannot qualify a route; the message says which check failed."""


def read_run(run_dir: Path) -> RunState:
    """The run's saved state; a missing or unreadable one is not evidence."""
    path = Path(run_dir) / "state.json"
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise NotEvidence(f"{path} cannot be read ({error})") from None
    if not isinstance(state, dict):
        raise NotEvidence(f"{path} is not a run state")
    return cast("RunState", state)


def check_live(state: RunState) -> str:
    """The run's declared provenance, refusing anything that cannot be live evidence."""
    kind = ((state.get("settings") or {}).get("evidence_provenance") or {}).get("kind")
    if kind != "live":
        raise NotEvidence(
            f"the run's evidence provenance is {kind or 'undeclared'!r}, not 'live'; a fake or unknown run "
            "qualifies nothing (start a live run with --evidence-provenance live)"
        )
    if state.get("status") != "TASK_COMPLETE":
        raise NotEvidence(f"the run ended {state.get('status')!r}, not TASK_COMPLETE")
    return kind


def reached(state: RunState) -> dict:
    """The model each role actually sent an attempt to, from the run's own accounting.

    A stage's unit role is not always the slot whose route it launched (``astra`` runs the
    Plan Reviewer and Completion Reviewer stages), so an attempt counts for its route slot
    first and its unit role only when no other slot was named (#2114). A reviewer fallback
    launches on another slot's route, so its attempt counts for that slot.

    Only an attempt that finished with accepted output proves its route. A rejected, timed-out,
    interrupted, still-active or report-repair attempt does not: a reviewer route that went
    silent and was replaced by a fallback must not read as qualified because the run completed."""
    rows = (usage.accounting(state) or {}).get("attempts") or []
    seen: dict[str, list[str]] = {}
    for row in rows:
        role = row.get("route_role") or row.get("role")
        model = row.get("model")
        if not role or not model or any(row.get(flag) for flag in FAILED):
            continue
        seen.setdefault(role, []).append(model)
    return seen


def route_of(state: RunState, role: str) -> dict:
    """The saved route the role completed on: model, level, job."""
    record = role_route.record(state, role)
    if not record.get("model"):
        raise NotEvidence(f"the run configured no model for {roles.screen_name(role)}")
    return record


def entry_for(table: dict, role: str, model: str) -> dict | None:
    """The table entry whose spellings cover ``model``, comparing model identity."""
    wanted = identity.identity(model)
    for entry in table["roles"].get(role) or ():
        spellings = {identity.identity(route_id) for route_id in entry.get("routes") or [entry["model"]]}
        if wanted in spellings:
            return entry
    return None


def merge(table: dict, role: str, record: dict, evidence: str) -> str:
    """Add or confirm one role's route; returns 'added' or 'confirmed'."""
    model = record["model"]
    entry = entry_for(table, role, model)
    if entry is None:
        entry = {
            "model": model,
            "routes": [model],
            "effort": record.get("reasoning_effort"),
            "provenance": "qualified",
            "evidence": evidence,
        }
        table["roles"].setdefault(role, []).append(entry)
        return "added"
    if model not in (entry.get("routes") or []) and model not in (entry.get("routes") or [entry["model"]]):
        entry.setdefault("routes", [entry["model"]]).append(model)
    if entry.get("effort") is None and record.get("reasoning_effort"):
        entry["effort"] = record["reasoning_effort"]
    was = entry.get("provenance")
    entry["provenance"] = "qualified"
    if evidence not in (entry.get("evidence") or ""):
        entry["evidence"] = (entry.get("evidence") or "").rstrip() + " " + evidence
    return "confirmed" if was == "qualified" else f"promoted from {was!r}"


def qualify(state: RunState, table: dict, *, evidence: str, only=()) -> list[str]:
    """Merge every reached role the table covers; returns one line per role.

    A model the table excludes for its role is named and skipped: a live run can reach it
    through a profile that pins it, but that is not permission to auto-select it (#2121)."""
    seen = reached(state)
    lines = []
    for role in sorted((state.get("settings") or {}).get("roles") or {}):
        if only and role not in only:
            continue
        if role not in selector.PIPELINE_ROLES:
            continue
        record = route_of(state, role)
        job = record.get("job") or roles.screen_name(role)
        blocked = {
            identity.identity(entry["model"]): entry["evidence"]
            for entry in (table.get("excluded") or {}).get(role) or ()
        }.get(identity.identity(record["model"]))
        if blocked:
            lines.append(f"{job}: not qualified — {record['model']} is excluded from automatic selection: {blocked}")
            continue
        models = seen.get(role) or []
        if identity.identity(record["model"]) not in {identity.identity(model) for model in models}:
            lines.append(
                f"{job}: not qualified — no recorded attempt at {record['model']} "
                f"(attempts: {', '.join(models) or 'none'})"
            )
            continue
        outcome = merge(table, role, record, evidence)
        level = record.get("reasoning_effort") or "the model's default"
        lines.append(f"{job}: {record['model']} at {level} — {outcome}")
    return lines


def args(argv):
    parser = argparse.ArgumentParser(
        prog="qualify_routes.py",
        description="Merge the routes a finished live run proved into the qualification table.",
    )
    parser.add_argument("--run-dir", type=Path, required=True, help="the finished run's directory (holds state.json)")
    parser.add_argument("--scenario", help="the scenario the run used, named in the evidence")
    parser.add_argument("--profile", help="the provider profile the run used, named in the evidence")
    parser.add_argument("--role", action="append", default=[], help="qualify only this role (repeatable)")
    parser.add_argument("--date", default=None, help="the run's date, YYYY-MM-DD (default: today)")
    parser.add_argument("--dry-run", action="store_true", help="print what would change; write nothing")
    parser.add_argument("--table", type=Path, default=TABLE, help="the table to update")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    options = args(argv)
    try:
        state = read_run(options.run_dir)
        check_live(state)
    except NotEvidence as error:
        print(f"qualify_routes: {error}", file=sys.stderr)
        return 2
    try:
        raw = json.loads(options.table.read_text(encoding="utf-8"))
        table = selector.parse(raw)
    except (OSError, ValueError) as error:
        print(f"qualify_routes: the qualification table cannot be read ({error})", file=sys.stderr)
        return 2
    when = options.date or date.today().isoformat()
    subject = options.scenario or Path(options.run_dir).name
    profile = options.profile or "a stock profile"
    evidence = (
        f"Live {when} ({subject} on {profile}, run {Path(options.run_dir).name}) reached TASK_COMPLETE on this route."
    )
    # Keep every top-level field the selector does not own (the table's basis text), or a write
    # would delete it (#2117).
    document = {"as_of": table["as_of"]}
    document.update({key: value for key, value in raw.items() if key not in ("as_of", "roles")})
    document["roles"] = json.loads(json.dumps(table["roles"]))
    lines = qualify(state, document, evidence=evidence, only=set(options.role))
    changed = document["roles"] != table["roles"]
    if changed:
        document["as_of"] = max(table["as_of"], when)
        try:
            selector.parse(document)
        except ValueError as error:
            print(f"qualify_routes: the merged table would be invalid ({error})", file=sys.stderr)
            return 2
    for line in lines:
        print(line)
    if not changed:
        print("qualify_routes: nothing to change; the table already lists these routes.")
        return 0
    if options.dry_run:
        print("\nqualify_routes: --dry-run; the table would become:\n")
        print(json.dumps(document, indent=2, ensure_ascii=False))
        return 0
    options.table.write_text(json.dumps(document, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"\nqualify_routes: wrote {options.table}. Commit it with the PR and name the run directory in the body.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
