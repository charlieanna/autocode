"""Wrong implementations must fail the acceptance tests (#3977): a report-only study.

After a spec-first batch's regression proof passes, a model that has not seen the acceptance tests writes a
few plausible wrong implementations of the approved ``test:`` cases, each as a patch against the candidate
plus a ``demo`` command that observes the violated behavior directly. The runner then, for each variant:

- drops it as ``invalid`` when it names no due ``test:`` case, edits a test file, or does not apply,
- requires the demo to exit 0 on the candidate (``demo_unproven`` otherwise) and non-zero with the patch
  (``not_shown_wrong`` otherwise), so a survivor is always a really wrong implementation,
- runs the acceptance test files of that case on the patched copy: a failure kills the variant, and a
  variant every test still passes ``survived``: the tests accept a wrong implementation.

This module changes no verdict, queues no repair and asks the person nothing. Survivors are evidence for the
independent Tester: one it judges a real violation of its approved case becomes an ``accepts_wrong_implementation``
repair (autocode_acceptance_test_repair), which sends the tests back to ``write_acceptance_tests`` within that
path's existing rounds; the changed tests are a new source, so the study runs again on them. Pure: prompt, schema, per-variant verdicts and the
record; the unit passes in the functions that copy the tree, apply a patch, run a demo and run tests, so
nothing here touches the workspace. State: ``acceptance_tests["wrong_implementations"]``.
"""

from __future__ import annotations

import hashlib
import json
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Final, TypedDict

if TYPE_CHECKING:
    from .autocode_run_state import RunState

    class _StudyIdentity(TypedDict):
        source_revision: str
        batch: str
        contract_hash: str | None


try:
    from . import autocode_acceptance_tests as acceptance
    from . import autocode_prompts as prompts
    from . import autocode_run_status as run_status
    from . import autocode_stage_access as stage_access
    from . import autocode_stray_writes as stray_writes
    from . import autocode_test_results as test_results
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_tests as acceptance
    import autocode_prompts as prompts
    import autocode_run_status as run_status
    import autocode_stage_access as stage_access
    import autocode_stray_writes as stray_writes
    import autocode_test_results as test_results
    import autocode_util as util

STAGE: Final = "write_wrong_implementations"
KEY: Final = "acceptance_tests"
FIELD: Final = "wrong_implementations"
SETTING: Final = "wrong_implementations"
MAX_VARIANTS: Final = 8
BUDGET_SECONDS: Final = 900
CATEGORIES: Final = (
    "logic",
    "validation",
    "format",
    "boundary",
    "entry_point",
    "packaging",
    "discovery",
    "hardcoded",
    "stub",
)
KILLED: Final = "killed"
SURVIVED: Final = "survived"
OUTCOMES: Final = (KILLED, SURVIVED, "invalid", "demo_unproven", "not_shown_wrong", "not_run")
TEXT = {"type": "string"}
VARIANT = {
    "type": "object",
    "additionalProperties": False,
    "required": ["case_id", "mistake", "category", "patch", "demo"],
    "properties": {
        "case_id": TEXT,
        "mistake": TEXT,
        "category": {"type": "string", "enum": list(CATEGORIES)},
        "patch": TEXT,
        "demo": {"type": "array", "items": TEXT, "minItems": 1},
    },
}
SCHEMA: Final = {
    "type": "object",
    "additionalProperties": False,
    "required": ["variants"],
    "properties": {"variants": {"type": "array", "items": VARIANT, "maxItems": MAX_VARIANTS}},
}
PROMPT = prompts.get("wrong-implementations.md") + stage_access.scratch_rule(STAGE)


@dataclass(frozen=True)
class Runners:
    """What the unit supplies; each runs in a scratch copy of the candidate, never in the workspace.

    ``demo(patch, argv)`` runs the demo with the patch applied (``patch`` None: on the candidate itself) and
    returns ``{"exit_code": int | None, "error": str}``; an ``error`` with no exit code means the patch did not
    apply or nothing ran. ``tests(patch, files)`` runs those acceptance test files with the patch applied and
    returns ``{"failed": [test ids], "error": str}``."""

    demo: Callable
    tests: Callable


def enabled(state: RunState) -> bool:
    return bool((state.get("settings") or {}).get(SETTING))


def add_argument(parser) -> None:
    import argparse

    parser.add_argument(
        "--wrong-implementations",
        action=argparse.BooleanOptionalAction,
        default=None,
        help="Spec-first runs (#3977): before the independent Tester, a model that has not seen the acceptance "
        "tests writes plausible wrong implementations of the approved test: cases, and the runner records which "
        "of them the tests still accept; report only, off by default",
    )


def new_run_setting(requested, spec_first: bool) -> bool:
    """Off unless asked for; the acceptance tests it judges exist only on a spec-first run."""
    if requested is True and not spec_first:
        raise ValueError("--wrong-implementations needs --spec-first")
    return requested is True


def case_files(state: RunState) -> dict[str, list[str]]:
    """Each due ``test:`` case with a current acceptance receipt, mapped to its batch's test files.

    A receipt names its batch's files, not one file per case, so a variant of a case is run against every
    acceptance test of that batch: it is killed when any of them fails and survives only when all pass."""
    contract = (state.get("goal_contract") or {}).get("hash")
    kinds = {case["id"]: case.get("kind") for case in acceptance.all_cases(state)}
    found: dict[str, list[str]] = {}
    for receipt in acceptance.receipts(state).values():
        if receipt.get("contract_hash") != contract:
            continue
        for cid in receipt.get("case_ids") or []:
            if cid in kinds and kinds[cid] != "preserve":
                found[cid] = sorted(receipt.get("files") or {})
    return {cid: files for cid, files in found.items() if files}


def configure_new_run(settings: dict, args) -> None:
    """Save the setting on a new run when asked for (after spec-first is decided)."""
    if new_run_setting(getattr(args, "wrong_implementations", None), bool(settings.get("spec_first"))):
        settings[SETTING] = True


def resume_refused(saved_settings: dict, requested) -> bool:
    """A saved run keeps its choice; repeating its own choice is harmless, no flag changes nothing."""
    return requested is not None and bool(requested) != bool(saved_settings.get(SETTING))


def packet(state: RunState, inventory: dict | None = None, engine: str | None = None) -> dict:
    """The approved cases and nothing of the tests: the writer must stay blind to them."""
    cases = {case["id"]: case for case in acceptance.all_cases(state)}
    return {
        "stage": STAGE,
        "task": state["task"],
        "workspace": state.get("workspace"),
        "execution_engine": engine,
        "workspace_inventory": inventory or {},
        "cases": [{"id": cid, "text": cases[cid].get("text", "")} for cid in case_files(state)],
        "max_variants": MAX_VARIANTS,
    }


def prompt(
    state: RunState, inventory: dict | None = None, soft_budget_tokens: int = 10000, engine: str | None = None
) -> tuple[str, dict]:
    text = PROMPT + "\nCURRENT HANDOFF DATA\n" + json.dumps(packet(state, inventory, engine), indent=2)
    return text, {"estimated_prompt_tokens": (len(text.encode()) + 3) // 4, "soft_budget_tokens": soft_budget_tokens}


def studied(state: RunState, source_revision: str, batch: str) -> bool:
    saved = (state.get(KEY) or {}).get(FIELD) or {}
    return saved.get("source_revision") == source_revision and saved.get("batch") == batch


def _batch(state: RunState) -> str:
    return util.digest(sorted(case_files(state)))[:16]


def route(state: RunState, current_revision: str) -> bool:
    """Send a spec-first run about to be independently tested to the study first, once per source and batch.

    Only a passing regression proof of ``current_revision``, the source as it is now, qualifies: the caller
    runs this after the runner has proved that source for the Tester (autocode_completion_result), because
    before then a first build has no proof and a later one only the previous round's (#3977). Anything else
    leaves the run alone, and the study never delays or blocks it again (the record, even a skip, marks the
    batch studied). True when the run was sent to the study and the Tester must not launch now."""
    if state.get("next_stage") != "tester" or not (enabled(state) and acceptance.enabled(state)):
        return False
    proof = state.get("regression_proof") or {}
    revision = str(proof.get("source_revision") or "")
    if proof.get("verdict") != "PASS" or not revision or revision != current_revision:
        return False
    batch = _batch(state)
    if studied(state, revision, batch):
        return False
    contract = (state.get("goal_contract") or {}).get("hash")
    if not case_files(state):
        skip(
            state,
            "no due test: case has acceptance tests",
            source_revision=revision,
            batch=batch,
            contract_hash=contract,
        )
        return False
    state.setdefault(KEY, {"receipts": {}})[FIELD + "_redirect"] = {"from": "tester", "source_revision": revision}
    state["next_stage"] = STAGE
    state["next_action"] = "A model that has not seen the acceptance tests is writing plausible wrong implementations."
    return True


def _touches_tests(patch: str) -> bool:
    for line in patch.splitlines():
        if line.startswith(("+++ ", "--- ", "diff --git ")):
            for part in line.split():
                name = part[2:] if part[:2] in ("a/", "b/") else part
                if name not in ("---", "+++", "diff", "--git", "/dev/null") and test_results.is_test_path(name):
                    return True
    return False


def _verdict(row: dict, case_files: dict, runners: Runners, deadline: float) -> dict:
    """One variant's outcome; pure over the injected runners."""
    base = {
        "case_id": row["case_id"],
        "mistake": row["mistake"],
        "category": row["category"],
        "patch_sha256": hashlib.sha256(row["patch"].encode()).hexdigest(),
        "demo": list(row["demo"]),
    }
    if row["case_id"] not in case_files:
        return {**base, "outcome": "invalid", "reason": "names no due test: case of this batch"}
    if _touches_tests(row["patch"]):
        return {**base, "outcome": "invalid", "reason": "the patch edits a test file"}
    if time.monotonic() >= deadline:
        return {**base, "outcome": "not_run", "reason": "the study's time budget was spent"}
    clean = runners.demo(None, row["demo"])
    if clean.get("exit_code") != 0:
        return {**base, "outcome": "demo_unproven", "reason": "the demo does not exit 0 on the candidate"}
    shown = runners.demo(row["patch"], row["demo"])
    if shown.get("exit_code") is None and shown.get("error"):
        return {**base, "outcome": "invalid", "reason": f"the patch does not apply to the candidate: {shown['error']}"}
    if shown.get("exit_code") in (0, None):
        return {**base, "outcome": "not_shown_wrong", "reason": "the demo still exits 0 with the patch"}
    run = runners.tests(row["patch"], case_files[row["case_id"]])
    if run.get("error"):
        return {**base, "outcome": "not_run", "reason": f"the tests could not run: {run['error']}"}
    failed = sorted(run.get("failed") or [])
    return {**base, "outcome": KILLED if failed else SURVIVED, "killed_by": failed}


def study(
    variants: list[dict], case_files: dict[str, list[str]], runners: Runners, *, budget: float = BUDGET_SECONDS
) -> dict:
    """Judge each variant of a report: ``case_files`` maps a due ``test:`` case to its acceptance test files."""
    deadline = time.monotonic() + budget
    rows = [_verdict(row, case_files, runners, deadline) for row in variants[:MAX_VARIANTS]]
    counts = {name: sum(1 for row in rows if row["outcome"] == name) for name in OUTCOMES}
    return {"variants": rows, "counts": counts, "survivors": [r for r in rows if r["outcome"] == SURVIVED]}


def save(state: RunState, result: dict, *, source_revision: str, batch: str, contract_hash: str | None) -> None:
    saved = state.setdefault(KEY, {"receipts": {}})
    saved[FIELD] = {
        "source_revision": source_revision,
        "batch": batch,
        "contract_hash": contract_hash,
        **result,
    }


def skip(state: RunState, reason: str, *, source_revision: str, batch: str, contract_hash: str | None) -> None:
    save(
        state,
        {"variants": [], "counts": {name: 0 for name in OUTCOMES}, "survivors": [], "skipped_reason": reason},
        source_revision=source_revision,
        batch=batch,
        contract_hash=contract_hash,
    )


def survivors_note(state: RunState) -> str:
    """The independent Tester's read-only evidence: wrong implementations its batch's tests still accept."""
    saved = (state.get(KEY) or {}).get(FIELD) or {}
    rows = saved.get("survivors") or []
    if not rows:
        return ""
    lines = [f"- case {row['case_id']}: {row['mistake']} ({row['category']})" for row in rows]
    return (
        "\nWRONG IMPLEMENTATIONS THE ACCEPTANCE TESTS STILL ACCEPT (#3977): the runner showed each "
        "of these is wrong (its demo fails) while the case's acceptance tests pass. For each one that really "
        "violates its approved case, fail that criterion and list it in acceptance_test_repairs with kind "
        '"accepts_wrong_implementation" and the mistake as the reason, so the tests are strengthened before the '
        "Builder works again. One that violates nothing the case states needs no action.\n" + "\n".join(lines) + "\n"
    )


def apply(state: RunState, value: dict, record: dict, workspace, run_demo=None, run_tests=None) -> None:
    """Judge the writer's variants with the runner's own demo and test runs, record them, go on to the Tester.

    Without the runner functions a variant is not run: the study records nothing it could not show."""
    stray = stage_access.stray(STAGE, record.get("changed_files"))
    if stray:
        raise stray_writes.StrayWrites(
            "Writing wrong implementations must not change the repository; this attempt changed: " + ", ".join(stray),
            stray,
        )
    runners = Runners(
        demo=run_demo or (lambda patch, argv: {"exit_code": None, "error": "no demo runner was given"}),
        tests=run_tests or (lambda patch, files: {"failed": [], "error": "no test runner was given"}),
    )
    redirect = (state.get(KEY) or {}).pop(FIELD + "_redirect", None) or {}
    result = study(value["variants"], case_files(state), runners)
    identity: _StudyIdentity = {
        "source_revision": redirect.get("source_revision") or str(record.get("source_revision") or ""),
        "batch": _batch(state),
        "contract_hash": (state.get("goal_contract") or {}).get("hash"),
    }
    save(state, result, **identity)
    run_status.running(state, "REVIEWING", next_stage=redirect.get("from") or "tester")


def owns(state: RunState) -> bool:
    """A study never completes a run: it hands it to the independent Tester."""
    return False


def render(state: RunState) -> str:
    return ""
