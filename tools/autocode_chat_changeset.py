"""A change set in the terminal chat (#2066, step 7): its side panel rows, /changeset and /runs.

The status view lists the other runs of this run's change set (``view.scope.linked``), and the panel
draws only that. ``/changeset`` reads ``autocode changeset status --run-dir RUN --json`` (owner
decision 2026-10-25) and offers only the two actions it names: approving the set
(``approval.next_command``), bound to the set token of the plans it showed, and checking the runs
together (``verification.next_command``). Linking, the check command, waivers, reopening, the merge
order and the pull-request text stay the person's commands at the CLI. ``/runs`` groups the chats of
one change set by the ``changeset`` its ``runs --json`` rows carry.

Pure functions over that JSON; standard library only.
"""

from __future__ import annotations

from collections.abc import Callable

# `changeset status` and `approve` read every run's status view, so they get longer than one status read;
# the joint check runs the person's own command, which the core bounds at 1800 s.
READ_TIMEOUT = 120.0
CHECK_TIMEOUT = 1860.0
APPROVE = "Approve every plan above together?"
VERIFY = "Run the set's integration check now?"


def linked(view: dict | None) -> list[dict]:
    """The other runs of the view's change set; [] when it is in none."""
    rows = ((view or {}).get("scope") or {}).get("linked")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def compact(view: dict | None) -> tuple[str, list[str]] | None:
    """The side panel's Change set section, as (summary, rows), or None when the run is in no set."""
    rows = linked(view)
    if not rows:
        return None
    lines = [
        f"{row.get('target')} · {'source run' if row.get('role') == 'source' else 'linked run'}"
        + ("" if row.get("run_dir") else " · starting")
        for row in rows
    ]
    return f"with {len(rows)} other run{'s' if len(rows) != 1 else ''}", [*lines, "/changeset shows the set"]


def approval_hint(view: dict | None) -> str | None:
    """What the plan approval prompt adds for a run in a change set."""
    rows = linked(view)
    if not rows:
        return None
    others = ", ".join(str(row.get("target")) for row in rows)
    return (
        f"This run is in a change set with {others}. /changeset shows every run's plan and approves them "
        "together; approving here releases this run alone."
    )


def text(summary: dict, render_plan: Callable[[dict], str]) -> str:
    """What /changeset prints: each run and its state, the plans a set approval would approve, and
    where the set stands."""
    lines = [f"Change set {summary.get('changeset')}:"]
    runs = [row for row in summary.get("runs") or [] if isinstance(row, dict)]
    for row in runs:
        needs = f", needs {row['needs']}" if row.get("needs") else ""
        lines.append(f"  {row.get('target')} ({row.get('role')}): {row.get('status') or 'starting'}{needs}")
    approval = summary.get("approval") or {}
    verification = summary.get("verification") or {}
    if approval.get("next_command"):
        for row in runs:
            lines.append(f"\n{row.get('target')} ({row.get('role')}): " + render_plan(row["plan"]))
    elif approval.get("waiting_on") and not verification.get("done"):
        lines.append("A set approval waits for a complete plan from: " + ", ".join(approval["waiting_on"]))
    result = verification.get("result") or {}
    if verification.get("done"):
        lines.append("Checked together: done. Merge order: " + ", then ".join(verification.get("merge_order") or []))
    elif verification.get("next_command"):
        check = verification.get("check") or {}
        lines.append(
            f"Every run is complete; the set's check is due in {check.get('in')}: "
            + " ".join(check.get("command") or [])
        )
    elif result.get("current") and not result.get("passed"):
        lines.append(
            "The integration check failed. Reopen the run that diverged (autocode changeset reopen) or "
            "waive it with a reason (autocode changeset waive)."
        )
    elif verification.get("waiting_on") and not approval.get("next_command"):
        lines.append(
            "Checked together once every run is complete; waiting on: " + ", ".join(verification["waiting_on"])
        )
    return "\n".join(lines)


def offer(summary: dict) -> tuple[str, str] | None:
    """The one action /changeset offers now, as (summary key, question): the set approval first."""
    if (summary.get("approval") or {}).get("next_command"):
        return "approval", APPROVE
    if (summary.get("verification") or {}).get("next_command"):
        return "verification", VERIFY
    return None


def groups(rows: list[dict]) -> list[str]:
    """The /runs lines that group the numbered chats of each change set, for sets with two or more."""
    numbers: dict[str, list[int]] = {}
    for number, row in enumerate(rows, 1):
        if isinstance(row, dict) and row.get("changeset"):
            numbers.setdefault(str(row["changeset"]), []).append(number)
    return [
        f"Change set {key}: chats {', '.join(str(n) for n in found[:-1])} and {found[-1]} are one request."
        for key, found in numbers.items()
        if len(found) > 1
    ]
