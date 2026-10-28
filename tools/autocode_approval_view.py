"""What the plan approval stop tells a person (issue #381).

At ``AWAITING_GOAL_APPROVAL`` the brief says which plan revision is waiting and what
approving it authorizes, that its token is a SHA-256 lock on that exact plan, the limits
the run works within, and the commands that approve or revise it. The ``Approval token:``
line itself stays where and as it was: tools parse it.

The full brief runs to hundreds of lines, so it ends with a short summary of the decision
(``summary``), just above the limits and the commands: what the plan will do, what it may
change, when it is done and what passing proves. That is the last screen of the terminal.

Pure functions over saved values. Only the pure cost formatter (autocode_cost, which imports
nothing above the standard library) is imported, so the brief renderer (autocode_goal_lifecycle)
uses them without adding to an import cycle.
"""

from __future__ import annotations

import json
import shlex

try:
    from . import autocode_cost as cost
except ImportError:
    import autocode_cost as cost

# How many scope exclusions the summary lists; the rest are in the full brief above it.
SCOPE_SHOWN = 3
# What the runner's regression proof (autocode_regression.check_cases, autocode_verify) requires of
# the test for each case. A build's new test may fail or not even load on the original code; a bug
# fix's must run there and fail. A preserve case (a plan's guard:) must pass on the original code
# too, unless its test file cannot load there: that counts, with a note (``proof``).
RESTORE = {
    "build": "must pass with the change and must not have passed without it",
    "bugfix": "must fail on the original code and pass with the fix",
}
PRESERVE = "must pass with the change and on the original code"
DESIGN = "no test named in a criterion is run as proof, so nothing shows that a check would fail without the change"


def intro(revision: int) -> str:
    """Under the brief header, above the token."""
    return (
        f"Plan revision {revision} (r{revision}) waits for your approval. Approving it authorizes "
        "implementation of exactly this plan: AutoCode may then change files in the workspace to build it."
    )


def token_note() -> str:
    """Under the ``Approval token:`` line."""
    return (
        "  The token is a SHA-256 lock on this exact plan: revising the plan changes the token, "
        "and an old token approves nothing."
    )


def actions(token: str, settings: dict, iteration: int, run_dir=None) -> list[str]:
    """Last: the limits in effect, then how to approve this plan or ask for a change."""
    command = "autocode" + (f" --run-dir {shlex.quote(str(run_dir))}" if run_dir else "")
    return [
        limits(settings, iteration),
        f"To approve this plan: {command} --approve-goal {shlex.quote(token)}",
        f"To change it instead: {command} --feedback 'WHAT TO CHANGE' (the revised plan gets a new token)",
    ]


def limits(settings: dict, iteration: int) -> str:
    """The saved run limits as one line. A missing or zero time limit is no limit; a cost cap shows only when set."""
    saved = settings.get("limits") or {}
    run, stage, ceiling = saved.get("max_seconds"), saved.get("stage_timeout_seconds"), saved.get("iteration_ceiling")
    orchestration = settings.get("orchestration") or {}
    builders = orchestration.get("max_parallel") if orchestration.get("enabled") is True else 1
    parts = [
        f"{duration(run)} of active time for the run" if run else "no time limit for the run",
        f"{duration(stage)} per stage" if stage else "no per-stage time limit",
        "no iteration ceiling" if ceiling is None else f"stops after iteration {ceiling} (now at {iteration})",
        f"up to {builders} Builders at once" if type(builders) is int and builders > 1 else "one Builder at a time",
    ]
    cap = saved.get("max_cost_usd")
    if isinstance(cap, (int, float)) and not isinstance(cap, bool) and cap > 0:
        parts.append(f"a {cost.usd(cap)} cost cap")
    return "Limits in effect: " + ", ".join(parts) + "."


def field_lines(value, indent=2) -> list[str]:
    """Show structured declarations without implying missing values were measured."""
    prefix = " " * indent
    if isinstance(value, dict):
        lines = []
        for key, item in value.items():
            lines.append(prefix + key.replace("_", " ").capitalize() + ":")
            lines.extend(field_lines(item, indent + 2))
        return lines or [prefix + "(none declared)"]
    if isinstance(value, list):
        return [line for item in value for line in field_lines(item, indent + 2)] or [prefix + "(none declared)"]
    text = "(unmeasured)" if value is None else "yes" if value is True else "no" if value is False else str(value)
    return [prefix + text]


def duration(seconds) -> str:
    if type(seconds) is int and seconds % 3600 == 0:
        return f"{seconds // 3600} h"
    if type(seconds) is int and seconds % 60 == 0:
        return f"{seconds // 60} min"
    return f"{seconds} s"


def summary(body: dict, revision, cases: list[dict], *, design_only: bool = False, proxied=()) -> list[str]:
    """The decision in brief, shown between the full brief and the limits.

    Every plan field is quoted verbatim from the contract body; a field an older body lacks is
    left out. ``cases`` are the cases the regression proof will require a test for at completion
    (autocode_test_cases.proof_cases) — a reproduced bug's diagnosis or the plan's marked criteria,
    never a mix, so ``proof`` tells which from the cases themselves; ``design_only`` is a design
    job (autocode_test_cases.design_only), whose criteria no named test proves. ``proxied`` are the
    trace's proxied rows (autocode_trace_coverage.proxied_rows, #2068): requirements whose criteria prove
    a stand-in for what the user sees, listed so the person never reads them as fully covered."""
    body = body if isinstance(body, dict) else {}
    lines = [f"Before you approve r{revision} (a summary of the plan above):"]
    outcome = body.get("intended_outcome")
    if isinstance(outcome, str) and outcome.strip():
        lines += ["What it will do:"] + ["  " + line for line in outcome.splitlines()]
    milestones = [row for row in _rows(body.get("milestones")) if isinstance(row, dict)]
    if milestones:
        ids = [str(row["id"]) for row in milestones if row.get("id")]
        count = f"{len(milestones)} milestone" + ("" if len(milestones) == 1 else "s")
        lines.append(f"Built in {count}" + (": " + ", ".join(ids) if ids else "") + ".")
    permissions = _items(body.get("permission_boundaries"))
    if permissions is not None:
        lines += ["What it may change:"] + (["  - " + row for row in permissions] or ["  (none declared)"])
    exclusions = _items(body.get("scope_exclusions"))
    if exclusions is not None:
        lines += ["Out of scope:"] + (["  - " + row for row in exclusions[:SCOPE_SHOWN]] or ["  (none declared)"])
        if len(exclusions) > SCOPE_SHOWN:
            lines.append(f"  - ... and {len(exclusions) - SCOPE_SHOWN} more in the plan above")
    criteria = [row for row in _rows(body.get("acceptance_criteria")) if isinstance(row, dict)]
    if criteria:
        lines.append("Done when:")
        for row in criteria:
            lines.append(
                "  "
                + " ".join(
                    part
                    for part in (f"[{row['id']}]" if row.get("id") else "", str(row.get("criterion") or ""))
                    if part
                )
            )
            if row.get("current_behavior"):
                lines.append(f"    Today: {row['current_behavior']}")
            if row.get("verification_method"):
                lines.append(f"    Checked by: {row['verification_method']}")
            if row.get("human_review") is True:
                lines.append("    Also needs your review of the result before the run can complete.")
        lines += ["What passing proves:"] + ["  - " + line for line in proof(body, criteria, cases, design_only)]
    rows = [row for row in proxied or () if isinstance(row, dict)]
    if rows:
        lines.append("Proven only on a stand-in (what you see is rendered outside this checkout):")
        for row in rows:
            lines.append(f"  - {row.get('requirement_id')}: {row.get('evidence')}")
            lines.append(f"    Why: {row.get('proxy_reason')}")
    return lines


def proof(body: dict, criteria: list[dict], cases: list[dict], design_only: bool = False) -> list[str]:
    """What the completion gate enforces for these criteria, and what it cannot show.

    True of the runner, not of a model's report: completion needs an independent validation of the
    current source passing every criterion with evidence, whose checks the runner re-runs in a clean
    copy (autocode_completion, autocode_check_replay); a human-review criterion may be left to the
    person's bound review. A bug fix, or a job whose ``cases`` are not empty, also needs the runner's
    regression proof for that source (autocode_regression, autocode_verify)."""
    human = any(row.get("human_review") is True for row in criteria)
    lines = [
        "An independent check of the final source must pass every criterion above"
        + (" (it may leave those marked for your review to you)" if human else "")
        + ", and the runner itself re-runs that check's commands in a clean copy: each must exit 0."
    ]
    bugfix = body.get("task_kind") == "bugfix"
    # proof_cases never mixes its two sources: a diagnosis case is (id, given, when, then), a plan
    # case carries its criterion as "text" (autocode_test_cases.diagnosis_cases, plan_cases).
    from_diagnosis = bool(cases) and "text" not in cases[0]
    restore = [str(case["id"]) for case in cases if case.get("kind") != "preserve"]
    preserve = [str(case["id"]) for case in cases if case.get("kind") == "preserve"]
    named = "; ".join(
        f"{', '.join(ids)}{'' if from_diagnosis else f' ({mark})'} {wanted}"
        for ids, mark, wanted in (
            (restore, "test:", RESTORE["bugfix" if bugfix else "build"]),
            (preserve, "guard:", PRESERVE),
        )
        if ids
    )
    runs = (
        (
            "The runner also runs a test named after each test case in the bug's diagnosis: "
            if from_diagnosis
            else "The runner also runs the test each of these criteria names: "
        )
        + named
        + "."
    )
    if bugfix:
        lines.append(
            "Bug fix: the runner also checks that a new or changed test fails on the original code and "
            "passes with the fix, and that no test that passed before now fails."
        )
        lines += [runs] if cases else []
    elif design_only:
        lines.append(f"Design job: {DESIGN}.")
    elif cases:
        lines.append(runs + " No test that passed before may fail now.")
    else:
        lines.append(
            "No criterion is marked test: or guard:, so nothing shows that a check would fail without the change."
        )
    if preserve:
        lines.append(
            f"If the test for {', '.join(preserve)} cannot load on the original code (its file imports "
            "code the change adds), it still counts, with a note that it is not shown to have passed there."
        )
    lines.append("Not proven: behavior no criterion describes, or inputs no check exercises.")
    return lines


def _rows(value) -> list:
    return value if isinstance(value, list) else []


def _items(value) -> list[str] | None:
    """A list field's rows as text, verbatim; None when the field is missing or not a list."""
    if not isinstance(value, list):
        return None
    return [row if isinstance(row, str) else json.dumps(row, sort_keys=True) for row in value]


def review_request(
    body: dict, request: dict, validation: dict | None, changes: dict | None = None, run_dir=None
) -> list[str]:
    """The human-review gate as a person reads it: each criterion to review with its text, how the
    independent check covered it, which files its saved Builder steps changed, what the gate asks
    (#2327), and the one command that shows the change with its evidence (#2360). ``changes`` is the
    status view's ``changes_by_criterion``; the raw request stays in the payload."""
    rows = {row.get("id"): row for row in body.get("acceptance_criteria") or [] if isinstance(row, dict)}
    results = {
        row.get("id"): row.get("status")
        for row in (validation or {}).get("criterion_results") or []
        if isinstance(row, dict)
    }
    changed = {
        row.get("id"): [str(path) for path in row.get("files") or []]
        for row in (changes or {}).get("criteria") or []
        if isinstance(row, dict)
    }
    lines = ["Your review is needed before the run can complete:"]
    for identifier in request.get("criteria") or []:
        row = rows.get(identifier) or {}
        text = str(row.get("criterion") or row.get("text") or "").strip()
        lines.append(f"  [{identifier}] {text}".rstrip())
        if row.get("verification_method"):
            status = results.get(identifier)
            verdict = f" ({str(status).lower()} in the independent check)" if status else ""
            lines.append(f"    Checked by: {row['verification_method']}{verdict}")
        if changed.get(identifier):
            lines.append(f"    Changed: {', '.join(changed[identifier])}")
    if request.get("discovered"):
        lines.append(f"  {request['discovered']}")
    if run_dir is not None:
        lines.append(f"  See the change and its evidence: autocode review --show --run-dir {run_dir}")
    return lines


def validation_summary(validation: dict) -> list[str]:
    """The independent check's verdict in a few lines instead of its whole record (#2327): what ran,
    how it exited, the findings and any criterion it left unverified, then where the record is."""
    checks = [row for row in validation.get("checks") or [] if isinstance(row, dict)]
    exits = [row.get("exit_code") for row in checks]
    ran = f"{len(checks)} check(s) ran"
    if checks:
        ran += ", each exited 0" if all(code == 0 for code in exits) else f", exit codes {exits}"
    findings = [row for row in validation.get("findings") or []]
    unverified = [str(item) for item in validation.get("unverified_criteria") or []]
    lines = [f"Validation: {validation.get('verdict') or 'no verdict'}; {ran}; {len(findings)} finding(s)."]
    for row in findings[:10]:
        if isinstance(row, dict):
            label = row.get("criterion_id") or row.get("id") or row.get("severity") or "finding"
            text = row.get("text") or row.get("description") or row.get("summary") or json.dumps(row, sort_keys=True)
            lines.append(f"  - {label}: {text}")
        else:
            lines.append(f"  - {row}")
    if len(findings) > 10:
        lines.append(f"  ... and {len(findings) - 10} more")
    if unverified:
        lines.append(f"  Not verified by the check: {', '.join(unverified)}")
    if validation.get("output"):
        lines.append(f"  Full record: {validation['output']}")
    return lines
