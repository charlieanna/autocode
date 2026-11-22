"""The bounded suite a regression proof falls back to when the whole detected suite cannot finish.

A detected suite is the whole project's. When its run on the original code times out, the proof
has nothing to compare the candidate with. AutoCode used to leave the proof UNVERIFIED there, and a
Resolver then asked a person for a longer timeout or a narrower test command: a question about its
own machinery (#3158). The proof now chooses the narrower command itself, deterministically, and
records the choice and its reason in its evidence. The same holds when the whole suite runs no test
that passes on the original code (``unusable``) while the tests nearest the change do pass there.

The focused suite is the framework's own targeted command over the existing tests nearest the change:

* every changed test file the original code already holds, and
* every test file the original code holds that is named after a changed product file
  (``test_calc.py``, ``calc_test.go`` or ``calc.test.ts`` for ``calc``), and
* existing Python tests that import a changed product, directly or through another original
  module. Ambiguous import targets are all retained; computed imports conservatively remain candidates.

Nothing is chosen when no such test exists, when the framework cannot target them, or when the
targeted command is the whole suite again. An explicit ``--test-command`` is the person's choice
and the caller never replaces it. This module only selects and describes; it runs nothing.
"""

from __future__ import annotations

import copy
import re
from pathlib import PurePosixPath

try:
    from . import autocode_command_receipt as command_receipt
    from . import autocode_plan_proof_command as plan_command
    from . import autocode_python_test_roles as policy
    from . import autocode_suite_definition_inputs as definition_inputs
    from . import autocode_test_cases as test_cases
    from . import autocode_test_role_inventory as inventory
except ImportError:
    import autocode_command_receipt as command_receipt
    import autocode_plan_proof_command as plan_command
    import autocode_python_test_roles as policy
    import autocode_suite_definition_inputs as definition_inputs
    import autocode_test_cases as test_cases
    import autocode_test_role_inventory as inventory

VERSION = 4
DECISION = "focused_suite"
MAX_FOCUSED_FILES = 64
MAX_OMITTED_SAMPLE = 20

SCOPE_FULL_CLOSURE = "full_closure"
SCOPE_DIRECT_CONSUMERS = "direct_consumers"
SCOPE_APPROVED_AND_NAMED = "approved_and_named"
SCOPE_CHANGED_AND_NAMED = "changed_and_named"

# What the whole suite did on the original code (``whole_suite_outcome``). A record without it is a timeout.
TIMED_OUT = "timed_out"
NO_PASSING_TEST = "no_passing_test"


def _named_after(product: str) -> frozenset[str]:
    """The test file names that name ``product`` as their subject."""
    path = PurePosixPath(product)
    stem, suffix = path.stem, path.suffix
    return frozenset(
        name + suffix
        for name in (
            f"test_{stem}",
            f"{stem}_test",
            f"{stem}_tests",
            f"{stem}_spec",
            f"{stem}.test",
            f"{stem}.spec",
            f"{stem}Test",
            f"{stem}Tests",
            f"Test{stem}",
        )
    )


def _python_aliases(paths) -> dict[str, set[str]]:
    aliases: dict[str, set[str]] = {}
    for path in paths:
        parts = PurePosixPath(path).with_suffix("").parts
        if parts[-1] == "__init__":
            parts = parts[:-1]
        for start in range(len(parts)):
            aliases.setdefault(".".join(parts[start:]), set()).add(path)
    return aliases


def _importing_tests(base_files, changes, roles, read):
    """Conservative reverse import reachability over pinned original Python files.

    Flat source roots and src-layout packages can expose several names for a module. Keep every
    matching target, never pick one arbitrarily. Unknown/computed imports cannot exclude a test.
    Candidate text never supplies edges, so deleting an import cannot hide existing coverage.
    """
    products = {path for path in changes if path.endswith(".py") and roles.role(path) == policy.PRODUCT}
    if not products:
        return set()
    paths = {path for path in base_files if path.endswith(".py")} | products
    aliases = _python_aliases(paths)
    importers: dict[str, set[str]] = {}
    affected = set(products)
    for path in paths & set(base_files):
        modules, uncertain = policy._imports(path, read(path))
        if uncertain or any(module.startswith(".") for module in modules):
            affected.add(path)
        for module in modules:
            for target in aliases.get(module, ()):
                importers.setdefault(target, set()).add(path)
    pending = list(affected)
    while pending:
        for path in importers.get(pending.pop(), ()):
            if path not in affected:
                affected.add(path)
                pending.append(path)
    return {path for path in affected & set(base_files) if roles.role(path) == policy.TEST}


def _direct_importing_tests(base_files, changes, roles, read) -> set[str]:
    """Original Python tests that directly import a changed product without transitive/helper expansion."""
    products = {path for path in changes if path.endswith(".py") and roles.role(path) == policy.PRODUCT}
    if not products:
        return set()
    aliases = _python_aliases(products)
    direct: set[str] = set()
    for path in base_files:
        if not path.endswith(".py") or roles.role(path) != policy.TEST:
            continue
        modules, _uncertain = policy._imports(path, read(path))
        for module in modules:
            key = module.lstrip(".") if module.startswith(".") else module
            if key and key in aliases:
                direct.add(path)
                break
    return direct


def _changed_and_named(base_files, changes, roles) -> set[str]:
    tests = {
        path
        for path, status in changes.items()
        if status != "deleted" and path in base_files and roles.role(path) == policy.TEST
    }
    names = frozenset().union(
        *(
            _named_after(path)
            for path, status in changes.items()
            if status != "deleted" and roles.role(path) == policy.PRODUCT
        )
    )
    tests.update(path for path in base_files if PurePosixPath(path).name in names and roles.role(path) == policy.TEST)
    return tests


def _plan_named_tests(base_files, roles, goal_contract, read) -> set[str]:
    """Original test files that define a test the approved plan names in a ``test:`` or ``guard:`` criterion.

    The proof looks for each named test by name (autocode_test_cases.match_cases), and a guard must pass on
    the original code and with the change, so a focused suite that leaves its file out fails the guard as
    "no test named ..." though the test is there and passes. A live self-build named guards in two test files
    no changed product file was named after, and its proof failed four guards that way."""
    if not isinstance(goal_contract, dict) or goal_contract.get("approval_status") != "approved":
        return set()  # only an approved plan's names are trusted, as for its commands
    names = sorted(
        {case["test_name"] for case in test_cases.plan_cases(goal_contract.get("body")) if case.get("test_name")}
    )
    if not names or read is None:
        return set()
    pattern = re.compile(r"(?<![\w])(?:" + "|".join(map(re.escape, names)) + r")(?![\w])")
    found = set()
    for path in base_files:
        if roles.role(path) != policy.TEST:
            continue
        try:
            text = read(path)
        except (OSError, ValueError, KeyError):
            continue
        if isinstance(text, str) and pattern.search(text):
            found.add(path)
    return found


def _named_in_command(command: str, files: set[str]) -> set[str]:
    named = set(plan_command.test_files(command, files))
    words = command.split()
    if len(words) > 3 and words[0] == "cd" and words[2] == "&&":
        folder = words[1].removeprefix("./").strip("/") + "/"
        inside = {path[len(folder) :]: path for path in files if path.startswith(folder)}
        named |= {inside[name] for name in plan_command.test_files(" ".join(words[3:]), inside)}
    return named


def _approved_plan_tests(base_files, roles, goal_contract) -> set[str]:
    """Original test files explicitly targeted by approved plan commands (never whole-suite commands)."""
    if not goal_contract:
        return set()
    original_tests = {path for path in base_files if roles.role(path) == policy.TEST}
    if not original_tests:
        return set()
    found: set[str] = set()
    for command in plan_command.approved_test_commands(goal_contract):
        selected = _named_in_command(command, original_tests)
        if 0 < len(selected) < len(original_tests):
            found |= selected
    return found


def nearest_tests(base_files, changes, roles, *, read=None) -> list[str]:
    """The original code's test files nearest the change, sorted."""
    base_files = frozenset(base_files)
    tests = _changed_and_named(base_files, changes, roles)
    if read is not None:
        tests.update(_importing_tests(base_files, changes, roles, read))
    return sorted(tests)


def candidates(
    framework,
    base_files,
    changes,
    roles,
    *,
    read=None,
    goal_contract=None,
    max_files: int = MAX_FOCUSED_FILES,
) -> list[dict]:
    """Ordered, deduplicated focused fallback tiers from broadest to narrowest trusted scope.

    Preserves full transitive + computed import protection whenever within ``max_files`` (or when no
    narrower non-empty tier exists). Drops over-broad leading tiers while a narrower valid tier remains.
    """
    if framework is None:
        return []
    base_files = frozenset(base_files)
    # The plan's named tests run in every tier, the narrowest too: each is needed for the proof to pass.
    changed_named = _changed_and_named(base_files, changes, roles) | _plan_named_tests(
        base_files, roles, goal_contract, read
    )
    approved_named = changed_named | _approved_plan_tests(base_files, roles, goal_contract)
    direct = approved_named | (_direct_importing_tests(base_files, changes, roles, read) if read is not None else set())
    full = direct | (_importing_tests(base_files, changes, roles, read) if read is not None else set())

    raw_tiers: list[tuple[str, list[str]]] = [(SCOPE_FULL_CLOSURE, sorted(full))]
    if direct != full:
        raw_tiers.append((SCOPE_DIRECT_CONSUMERS, sorted(direct)))
    if approved_named != changed_named:
        raw_tiers.append((SCOPE_APPROVED_AND_NAMED, sorted(approved_named)))
    raw_tiers.append((SCOPE_CHANGED_AND_NAMED, sorted(changed_named)))
    entries: list[dict] = []
    seen_commands: set[str] = set()
    for scope, tests in raw_tiers:
        if not tests:
            continue
        command = framework.targeted(roles.selectors(tests))
        if not command or command == framework.suite or command in seen_commands:
            continue
        seen_commands.add(command)
        omitted = sorted(full - set(tests))
        entries.append(
            {
                "command": command,
                "tests": tests,
                "selection_scope": scope,
                "omitted_tests": omitted,
            }
        )
    while len(entries) > 1 and len(entries[0]["tests"]) > max_files:
        entries.pop(0)
    return entries


def candidates_from_git(
    framework,
    workspace,
    base,
    changes,
    roles,
    *,
    goal_contract=None,
    max_files: int = MAX_FOCUSED_FILES,
) -> list[dict]:
    """Build bounded fallback tiers from pinned original git blobs."""
    original = inventory.entries(workspace, base)
    blobs = {path: (value[0], value[2]) for path, value in original.items()}
    with definition_inputs.blob_reader(workspace, blobs, None) as read:
        return candidates(
            framework,
            original,
            changes,
            roles,
            read=read,
            goal_contract=goal_contract,
            max_files=max_files,
        )


def choose(
    framework,
    base_files,
    changes,
    roles,
    *,
    read=None,
    goal_contract=None,
    max_files: int = MAX_FOCUSED_FILES,
) -> dict | None:
    """The focused suite for a whole detected suite that timed out, or None when there is none."""
    tiers = candidates(
        framework,
        base_files,
        changes,
        roles,
        read=read,
        goal_contract=goal_contract,
        max_files=max_files,
    )
    if not tiers:
        return None
    chosen = dict(tiers[0])
    if len(tiers) > 1:
        chosen["narrower_tiers"] = tiers[1:]
    return chosen


def choose_from_git(
    framework,
    workspace,
    base,
    changes,
    roles,
    *,
    goal_contract=None,
    max_files: int = MAX_FOCUSED_FILES,
) -> dict | None:
    """Use the original tree's imports without opening candidate-controlled source or links."""
    tiers = candidates_from_git(
        framework,
        workspace,
        base,
        changes,
        roles,
        goal_contract=goal_contract,
        max_files=max_files,
    )
    if not tiers:
        return None
    chosen = dict(tiers[0])
    if len(tiers) > 1:
        chosen["narrower_tiers"] = tiers[1:]
    return chosen


def timed_out(baseline) -> bool:
    """Whether a baseline result (autocode_verify.baseline) ended at its time limit."""
    return bool(baseline and (baseline.get("receipt") or {}).get("timed_out") is True)


def ran_no_passing_test(baseline) -> bool:
    """Whether a whole-suite baseline ended on its own, not at its time limit or interrupted, with no test
    that passed: the runner reported nothing, or only files it could not load.

    RSpec runs no example at all when one spec file cannot be loaded. In a Rails backend four specs required
    a file from a repository kept beside the project, which no copy AutoCode tests in has: the whole suite
    reported "0 examples, 4 errors occurred outside of examples" in 23 seconds on the original code, so every
    proof there would have ended UNVERIFIED and asked the person for a test command (2026-11-14). A run that
    stopped reporting after some tests passed, one that was interrupted, and a command that could not be
    started (exit 126 or 127, which the nearest tests' command would not be either) are not this, and are
    judged as before.
    """
    receipt = (baseline or {}).get("receipt") or {}
    return bool(
        baseline
        and baseline.get("health") == "broken"
        and not receipt.get("timed_out")
        and command_receipt.completed(receipt)
        and isinstance(receipt.get("exit_code"), int)
        and receipt["exit_code"] not in (126, 127)
        and not (receipt.get("results") or {}).get("passed")
    )


def unusable(baseline) -> bool:
    """Whether a whole-suite baseline (autocode_verify.baseline) gives the change nothing to be compared
    with: it ended at its time limit, or it ran no test that passed."""
    return timed_out(baseline) or ran_no_passing_test(baseline)


def usable(baseline) -> bool:
    """Whether a baseline ran tests that passed on the original code, so a change can be compared with it."""
    return bool(baseline) and baseline.get("health") in ("passing", "failing_tests")


def framework_for(framework, command):
    """A copy of ``framework`` whose whole suite is the focused ``command``; targeting is unchanged."""
    focused = copy.copy(framework)
    focused.suite = command
    return focused


_SCOPE_LABELS = {
    SCOPE_DIRECT_CONSUMERS: (
        "direct Python importers, approved plan selectors, filename matches, and changed original tests"
    ),
    SCOPE_APPROVED_AND_NAMED: "approved plan selectors, filename matches, and changed original tests",
    SCOPE_CHANGED_AND_NAMED: "changed original tests and tests named after changed product files",
}


def record(focus, whole, timeout, *, timed_out_focus=None) -> dict:
    """The evidence for the decision: what the whole suite did, what replaced it, and why."""
    limit = f"{timeout} seconds" if timeout else "its time limit"
    outcome = TIMED_OUT if timed_out(whole) else NO_PASSING_TEST
    happened = (
        f"did not finish on the original code within {limit}"
        if outcome == TIMED_OUT
        else "ran no test that passed on the original code"
    )
    scope = focus.get("selection_scope") or SCOPE_FULL_CLOSURE
    omitted = list(focus.get("omitted_tests") or ())
    tests = list(focus["tests"])
    result: dict = {
        "version": VERSION,
        "decision": DECISION,
        "whole_suite": whole["command"],
        "whole_suite_output": (whole.get("receipt") or {}).get("output"),
        "whole_suite_outcome": outcome,
        "timeout_seconds": timeout,
        "command": focus["command"],
        "tests": tests,
        "selection_scope": scope,
    }
    if scope == SCOPE_FULL_CLOSURE and not omitted and timed_out_focus is None:
        result["reason"] = (
            f"The detected whole suite {happened}, so existing "
            "behavior was checked with the original tests selected by changed tests, filenames and Python "
            "imports (including transitive and uncertain imports): " + ", ".join(tests)
        )
        return result

    scope_desc = _SCOPE_LABELS.get(scope, scope)
    gap_parts = []
    if timed_out_focus is not None:
        tf_scope = timed_out_focus.get("selection_scope") or SCOPE_FULL_CLOSURE
        tf_count = len(timed_out_focus.get("tests") or ())
        result["timed_out_focused_suite"] = {
            "command": timed_out_focus["command"],
            "selection_scope": tf_scope,
            "tests_count": tf_count,
        }
        gap_parts.append(
            f"the broader {tf_scope} fallback ({tf_count} tests: {timed_out_focus['command']}) also timed out on the original code"
        )
    if omitted:
        gap_parts.append(
            f"{len(omitted)} original test file(s) from broader transitive or computed-import reachability "
            "were not re-run in this bounded fallback"
        )
    gap = "; ".join(gap_parts) if gap_parts else f"selection narrowed to {scope}"
    result["omitted_tests_count"] = len(omitted)
    result["omitted_tests_sample"] = omitted[:MAX_OMITTED_SAMPLE]
    result["verification_gap"] = gap
    result["reason"] = (
        f"The detected whole suite {happened}, so existing behavior was checked with the bounded {scope} "
        f"fallback ({scope_desc}): {', '.join(tests)}. Verification gap: {gap}."
    )
    return result


def review_reason(fallback) -> str:
    happened = "ran no test that passed" if fallback.get("whole_suite_outcome") == NO_PASSING_TEST else "timed out"
    base = (
        "the no-regression check ran only the original tests nearest the change, because the whole suite "
        f"{happened} on the original code: " + fallback["command"]
    )
    scope = fallback.get("selection_scope")
    omitted_count = fallback.get("omitted_tests_count")
    if scope and scope != SCOPE_FULL_CLOSURE:
        detail = f"bounded {scope}"
        if isinstance(omitted_count, int) and omitted_count > 0:
            detail += f"; {omitted_count} broader reachability test(s) not re-run"
        if fallback.get("timed_out_focused_suite"):
            detail += "; broader focused tier also timed out"
        return f"{base} ({detail})"
    return base
