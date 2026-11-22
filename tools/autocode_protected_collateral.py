"""Recheck Node tests that never finished behind an approved replaced test."""

import shlex
from pathlib import Path

try:
    from . import autocode_node_tests as node
    from . import autocode_package_scope as scope
    from . import autocode_superseded_tests as superseded
    from . import autocode_verification_schedule as schedule
except ImportError:
    import autocode_node_tests as node
    import autocode_package_scope as scope
    import autocode_superseded_tests as superseded
    import autocode_verification_schedule as schedule


def _named(receipt, root):
    if not isinstance(receipt, dict):
        return None
    if "scheduling" in receipt:
        # The report-local pair receipt points to an exact scheduler attempt
        # under check-replay/obligations, outside the pair's own directory.
        if len(root.parents) < 3 or root.parent.name != "protected-tests" or root.parents[2].name != "check-replay":
            return None
        root = schedule.authenticated_attempt(receipt, root.parents[2] / "obligations")
    if root is None or not schedule.intact(receipt, root=root) or not schedule.complete_results(receipt):
        return None
    results = node.bound_results(receipt, root)
    return results if results == receipt.get("results") else None


def offer(command, receipt, entries, changed, root, workspace):
    """One supported original command and a complete stream naming all failures."""
    results = _named(receipt, root)
    if not results or not results.get("cancelled_by_parent") or receipt["exit_code"] == 0:
        return None
    approved = [entry for entry in entries if superseded.edited(entry, changed)]
    failed = set(results["failed"])
    excused = {name for name in failed if any(superseded.matches(entry, name) for entry in approved)}
    cancelled = failed - excused
    # Never retry a real failure (even if another test was cancelled).
    if not excused or not cancelled or not cancelled <= set(results["cancelled_by_parent"]):
        return None
    if set(results["collection_errors"]) - cancelled - excused:
        return None
    package, inner = scope.entered(command) or (None, command)
    if isinstance(workspace, (str, Path)) and (script := node.npm_test(inner, Path(workspace) / (package or "."))):
        _, inner = script  # normal scratch execution restores the detected built prerequisites
    filtered = node.exclude_tests(inner, [name.split("::")[-1] for name in sorted(excused)])
    if filtered is None:
        return None
    return scope.command(package, filtered) if package else filtered


def recovered(command, first, retry, entries, changed, root: Path, workspace):
    """Verify the whole observed inventory and require actual passes of all other tests."""
    expected = offer(command, first, entries, changed, root, workspace)
    if expected is None:
        return None
    package, requested = scope.entered(expected) or (None, expected)
    actual_package, executed = scope.entered(retry.get("command")) or (None, retry.get("command"))
    if not isinstance(executed, str) or actual_package != package:
        return None
    try:
        actual = shlex.split(executed)
    except ValueError:
        return None
    # Discard the detected build prefix, then require the exact Node argv
    # except for the reporter options supplied by the runner itself.
    start = max((i + 1 for i, word in enumerate(actual) if word == "&&"), default=0)
    stripped = [
        word for word in actual[start:] if not word.startswith(("--test-reporter=", "--test-reporter-destination="))
    ]
    if stripped != shlex.split(requested):
        return None
    original, repeated = _named(first, root), _named(retry, root)
    if not original or not repeated or retry["exit_code"] != 0 or repeated["failed"] or repeated["collection_errors"]:
        return None
    approved = [entry for entry in entries if superseded.edited(entry, changed)]
    replaced = {name for name in original["failed"] if any(superseded.matches(entry, name) for entry in approved)}
    passed = set(original["passed"]) | (set(original["failed"]) - replaced)
    skipped = set(original["skipped"])
    if set(repeated["passed"]) != passed or set(repeated["skipped"]) - replaced != skipped:
        return None
    return sorted(replaced)
