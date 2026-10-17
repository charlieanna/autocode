"""`autocode --goal-file PATH`: a saved run's next plan revision from a plan file (#720).

A team reviews a run's plan as the file `autocode plan --export` writes (autocode_plan_file) and edits it in a
pull request. Loading the merged file makes its front matter's contract the run's next draft revision,
exactly as `--edit-goal` does with a body (autocode_action_decisions), and the plan then goes through
ordinary approval: in joint planning the Plan Reviewer reviews it first, as it reviews any edit.

The file is refused when AutoCode cannot read it, or when its text no longer matches its front matter:
reviewers read the text, so loading a front matter that says something else would build a plan nobody read.
Its plan gets the checks every draft gets on install (autocode_goal_lifecycle.install_draft) and, with the
planner's own messages, the ones only a planner's draft gets (``check``). A file still holding text the export
masked as credential-shaped is refused first, since loading it would make the mask the plan's text.

The revision installed from the file keeps its path and goal hash in the contract record's ``plan_file``
(``record``); the hash covers the body, not the record. The approval stop then says whether the plan it
asks to approve is still the plan loaded from that file (``approval_lines``): the approval token is the
lock on that exact revision, and the file's goal hash is what `autocode plan --check FILE` prints.

Imports nothing that imports autocode, autopilot or a module in an import cycle.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_brief_literals as brief_literals
    from . import autocode_native_test_names as native_test_names
    from . import autocode_plan_file as plan_file
    from . import autocode_plan_file_origin as origin
    from . import autocode_requirement_cues as cues
except ImportError:
    import autocode_brief_literals as brief_literals
    import autocode_native_test_names as native_test_names
    import autocode_plan_file as plan_file
    import autocode_plan_file_origin as origin
    import autocode_requirement_cues as cues

# Which revision a file was loaded as, and how a plan differs from it, are in autocode_plan_file_origin, which
# the status view and the evidence report read too; these names stay here for this module's callers.
FIELD = origin.FIELD
RUNNER_RECORDS = origin.RUNNER_RECORDS
loaded = origin.loaded
changed = origin.changed
# autocode_util.redact's marker: `autocode plan --export` writes it in place of credential-shaped text.
MASKED = "[REDACTED]"


def read(path: Path) -> plan_file.PlanFile:
    """The plan in the file at ``path``; ValueError names a file that cannot be loaded."""
    try:
        with Path(path).open(encoding="utf-8", newline="") as stream:
            text = stream.read()
    except (OSError, UnicodeError) as error:
        raise ValueError(f"Cannot read the plan file {path}: {error}") from None
    try:
        plan = plan_file.read(text)
    except ValueError as error:
        raise ValueError(f"{path} is not a plan file AutoCode can load: {error}") from None
    if plan.stale_line is not None:
        raise ValueError(
            f"{path}: the text under the front matter no longer matches it (line {plan.stale_line}), so reviewers "
            f"read a plan other than the one it holds. Run `autocode plan --render {path}`, review the "
            "regenerated text, then load the file"
        )
    return plan


def check(state: RunState, body: dict, *, coverage: bool) -> None:
    """Refuse a plan from a file for what a planner's draft of it would be sent back for.

    install_draft checks every draft, a person's own --edit-goal included: the contract's shape and
    references, the runner's records (only the run's own are kept), and the commands it plans (no
    `git status`, and each must parse with `sh -n`). A planner's draft is also checked against the user's
    own words, which an --edit-goal body is not: it must keep every literal the brief states
    (autocode_goals.check_requirement_trace) and prove each Go test the brief names by that name
    (autocode_native_test_names). A plan file is edited by hand outside the run, so it gets both.
    ``coverage`` is false for a plan with open blocking questions, which a planner's draft is not checked
    against these for either (units.autoplanner.traces_coverage). Text the export masked is refused before
    any of these, which it would otherwise fail with a misleading cause (``_refuse_masked``).
    """
    current = (state.get("goal_contract") or {}).get("body") or {}
    _refuse_masked(current, body)
    for key in RUNNER_RECORDS:
        # install_draft refuses these too; this names the file's cause and the way out.
        if key in body and body[key] != current.get(key):
            raise ValueError(
                f"The plan file's {key} is not this run's: the file was exported from another run, or from a plan "
                f"revision this run has since replaced. Export the run's plan again, or delete {key} from the front "
                "matter to keep the run's own"
            )
    if not coverage:
        return
    brief_literals.check(cues.scan_texts(state), body)
    native_test_names.check(state, body)


def _refuse_masked(current: dict, body: dict) -> None:
    """Refuse a file whose plan holds text `autocode plan --export` masked (autocode_plan_file.masked).

    Loading it as it is would make the marker the plan's text where the run's plan says something else, or
    refuse it for a runner record or a brief literal the masking changed. A string the run's plan holds
    as it is, marker included, is the plan's own text: a plan for a redaction feature may say `[REDACTED]`.
    """
    own = _marked(current)
    keys = sorted(key for key, value in body.items() if (_marked(key) | _marked(value)) - own)
    if keys:
        raise ValueError(
            f"The plan file was masked on export: `autocode plan --export` wrote `{MASKED}` in its "
            f"{', '.join(keys)} in place of credential-shaped text, and loading it would make the marker the plan's "
            "text. Restore the masked values in its front matter (or reword that text so it holds "
            "no credential), run `autocode plan --render FILE` on it, then load it"
        )


def _marked(value) -> set[str]:
    """Every string in ``value``, keys included, that holds the masking marker."""
    if isinstance(value, str):
        return {value} if MASKED in value else set()
    found: set[str] = set()
    if isinstance(value, list):
        for item in value:
            found |= _marked(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            found |= _marked(key) | _marked(item)
    return found


def record(state: RunState, path: Path, plan: plan_file.PlanFile) -> None:
    """Name the plan file the revision just installed came from."""
    cast(dict, state["goal_contract"])[FIELD] = {"path": str(Path(path).resolve()), "goal_hash": plan.goal_hash}


def approval_lines(state: RunState) -> list[str]:
    """What the approval stop says about the plan file the plan waiting for approval came from, if one did."""
    source = loaded(state)
    if source is None:
        return []
    named = source[FIELD]
    body = cast(dict, state["goal_contract"])["body"]
    goal = plan_file.goal_hash(body)
    differences = changed(source["body"], body)
    lines = [
        "",
        f"Plan file: {named['path']} (goal hash {named['goal_hash']}), loaded as revision {source['revision']}.",
    ]
    if differences:
        return lines + [
            f"  This revision differs from the plan loaded from that file in {', '.join(differences)}: approving it "
            f"approves those changes. Its goal hash is {goal}; `autocode plan --export FILE` writes it for review."
        ]
    if goal == named["goal_hash"]:
        return lines + ["  This revision is the plan in that file, unchanged: the approval token approves exactly it."]
    return lines + [
        "  This revision is the plan loaded from that file; only the runner's records of the plan's review differ "
        f"from the file, so its goal hash is {goal}."
    ]


def summary(state: RunState, path: Path) -> str:
    """The line printed once the file is loaded."""
    contract = cast(dict, state["goal_contract"])
    return (
        f"Loaded the plan in {path} (goal hash {contract[FIELD]['goal_hash']}) as plan revision "
        f"{contract['revision']}; it is not approved."
    )
