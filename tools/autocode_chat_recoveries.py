"""What went wrong and what AutoCode did about it, in the chat.

Reads only the status view's ``recoveries`` (tools/autocode_recovery_history.py): the
core's own plain sentences for each rejected answer, rewritten report, Builder
send-back, Investigator call and Resolver decision. The chat says each one once as it
is recorded, the side panel lists the newest, and ``/why`` lists them all with the
Investigator's findings. Display only: nothing here is an action.
"""

from __future__ import annotations

PREFIX = "Recovery ·"  # the transcript draws a block that starts with it as a notice
PANEL_ROWS = 3
# When AutoCode calls each of the two jobs that handle a failure, said under /why when one ran.
WHY_CALLED = {
    "Investigator": (
        "Why the Investigator ran: it runs first in a bug fix to reproduce the bug, and again when a stage "
        "stops making progress (its answer rejected again, the Builder sent back twice or making no progress, "
        "the Planner and Plan Reviewer unable to agree). Instead of pausing for you, AutoCode has it find the "
        "cause, and the stage gets one more try with its advice."
    ),
    "Resolver": (
        "Why the Resolver ran: when a stage fails in a way AutoCode's rules cover, the Resolver picks the "
        "next step (try again, rewrite a report, another route) without a model, and records its reason."
    ),
}


def entries(view: dict | None) -> list[dict]:
    rows = (view or {}).get("recoveries")
    if not isinstance(rows, list):
        return []
    return [
        row
        for row in rows
        if isinstance(row, dict)
        and isinstance(row.get("id"), str)
        and isinstance(row.get("headline"), str)
        and isinstance(row.get("action"), str)
    ]


def clock(row: dict) -> str:
    """``05:41:01`` from the entry's UTC time, as the run log writes it; empty when unknown."""
    at = row.get("at")
    return at[11:19] if isinstance(at, str) and len(at) >= 19 else ""


def notice(row: dict) -> str:
    """The transcript block for one recovery: what went wrong, then what AutoCode did."""
    lines = [f"{PREFIX} {row['headline']}"]
    if isinstance(row.get("problem"), str):
        lines.append(row["problem"])
    lines.append(row["action"])
    return "\n".join(lines)


def why(view: dict | None) -> str:
    """``/why``: every recovery so far, oldest first, with the Investigator's findings."""
    rows = entries(view)
    if not rows:
        return "Nothing has gone wrong in this run so far: no stage has been rejected, sent back or investigated."
    blocks = []
    for row in rows:
        lines = [" ".join(part for part in (clock(row), row["headline"]) if part)]
        if isinstance(row.get("problem"), str):
            lines.append(f"  What went wrong: {row['problem']}")
        lines.append(f"  Then: {row['action']}")
        if isinstance(row.get("detail"), str):
            lines.append(f"  What it found: {row['detail']}")
        blocks.append("\n".join(lines))
    called = {row.get("by") for row in rows}
    blocks += [WHY_CALLED[job] for job in ("Investigator", "Resolver") if job in called]
    return "\n\n".join(blocks)


def compact(view: dict | None) -> tuple[str, list[str]] | None:
    """The side panel's summary and its newest few rows, or None when nothing has gone wrong."""
    rows = entries(view)
    if not rows:
        return None
    summary = f"{len(rows)} so far · /why"
    shown = [" ".join(part for part in (clock(row)[:5], row["headline"]) if part) for row in rows[-PANEL_ROWS:]]
    return summary, list(reversed(shown))


def details(view: dict | None) -> str:
    """The line ``/details`` adds under its explanation, or empty."""
    rows = entries(view)
    if not rows:
        return ""
    newest = rows[-1]
    return (
        f"What went wrong: {len(rows)} time{'' if len(rows) == 1 else 's'} so far, most recently "
        f'"{newest["headline"]}". /why shows each one and what AutoCode did.'
    )
