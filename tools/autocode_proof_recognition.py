"""Authenticate proof refusals that only failed to recognize a passing unittest suite (#3037).

Before #2972 a plain ``python -m unittest`` command chose the repository's pytest parser. The runner
still executed it with ``-v``, every check exited zero, but no per-test result was parsed, so the
exit-code fallback refused preservation because the base suite's definition could not be bounded.
That refusal is about AutoCode's own collector, not the change, yet the runner finding it opened
stayed open on that source (autocode_proof_findings keeps a finding for every source it failed on,
so a later pass cannot erase a flaky failure) and asked for a pointless source edit.

``recognition_only`` lets the caller settle that finding only when a fresh ordinary proof already
passes in the current approved context and every failed proof of the source behind it is that
defect: the same source, base and case scope, the same protected suite command (verbosity aside),
owned and intact captures whose retained logs the unittest parser reads as complete passes with no
failure, skip or collection error, and the same passing suite inventory as the fresh proof. Anything
else keeps the finding. Model text is never read, and no receipt is rewritten.

Custody is the one the disk recovery uses (autocode_proof_infrastructure): history keeps each
proof's path and time but not its whole-receipt hash, so an old receipt is trusted through its
runner-owned stage record, its path inside the run's ``regression`` folder, and the per-check log
and supervision pins ``proof_intact`` checks. No retroactive pin is invented.
"""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_package_scope as package_scope
    from . import autocode_python_tests as python_tests
    from . import autocode_util as util
    from . import autocode_verification_commands as commands
    from . import autocode_verification_schedule as schedule
    from . import autocode_verify_judgment as judgment
except ImportError:
    import autocode_package_scope as package_scope
    import autocode_python_tests as python_tests
    import autocode_util as util
    import autocode_verification_commands as commands
    import autocode_verification_schedule as schedule
    import autocode_verify_judgment as judgment

PASS, UNVERIFIED = "PASS", "UNVERIFIED"
SUITES = ("suite_on_candidate", "suite_on_base", "suite_on_base_with_tests")
_REFUSAL = re.compile("^" + r"(.+)".join(map(re.escape, judgment.BASE_DEFINITION_UNKNOWN.split("{}"))) + "$")


def recognition_only(state: RunState, row, revision) -> list[str] | None:
    """The failed receipts a fresh proof supersedes when each only missed unittest results, else None.

    The caller must already have authenticated the current proof as passing for ``revision``
    (autocode_regression.complete). Every failed proof of ``revision`` in the run's history is
    examined, not only the one the finding last cites; one that does not qualify keeps the finding.
    """
    try:
        proof = state.get("regression_proof") or {}
        history = state.get("regression_proofs") or []
        if (
            proof.get("verdict") != PASS
            or proof.get("source_revision") != revision
            or (proof.get("execution_context") or {}).get("contract_identity")
            != util.digest(state.get("goal_contract"))
            or history[-1].get("path") != proof.get("path")
            or history[-1].get("proof_context") != util.digest(proof.get("execution_context"))
        ):
            return None
        current = Path(proof["path"])
        fresh = util.read_object(current)
        if util.file_hash(current) != proof.get("receipt_sha256") or not _fresh(fresh):
            return None
        failed = [r for r in history[:-1] if r.get("source_revision") == revision and r.get("verdict") != PASS]
        paths = [r.get("path") for r in failed]
        if row.get("last_reported_in") not in paths:
            return None
        proved_at = datetime.fromisoformat(proof["proved_at"])
        root = (Path(state["run_dir"]) / "regression").resolve()
        for entry in failed:
            if not _superseded(state, entry, fresh, root, proved_at):
                return None
        return paths
    except (OSError, KeyError, TypeError, ValueError, AttributeError, RuntimeError):
        return None


def _fresh(receipt):
    """The current proof ran the recognized unittest suite and named complete passing results."""
    checks = receipt.get("checks")
    return (
        receipt.get("verdict") == PASS
        and (receipt.get("framework") or {}).get("name") == "unittest"
        and isinstance(checks, dict)
        and "suite_on_candidate" in checks
        and all(schedule.complete_results(checks[label]) for label in SUITES if label in checks)
    )


def _superseded(state: RunState, entry, fresh, root, proved_at):
    path = Path(entry["path"]).absolute()
    if (
        not path.resolve().is_relative_to(root)
        or path.stat().st_nlink != 1
        or any(p.is_symlink() for p in (path, *path.parents) if p.is_relative_to(root))
    ):
        return False
    owned = next(
        (
            s
            for s in reversed(state.get("stages", []))
            if s.get("stage") == "regression_proof" and s.get("runner_owned") and s.get("output") == str(path)
        ),
        None,
    )
    if not owned or datetime.fromisoformat(owned["finished_at"]) >= proved_at:
        return False
    old = util.read_object(path)
    checks = old.get("checks")
    suite = (old.get("commands") or {}).get("suite")
    recognized = commands.command_framework(suite) or package_scope.own(
        state.get("settings"), suite, commands.command_framework
    )
    if (
        old.get("verdict") != UNVERIFIED
        or old.get("failures")
        or not old.get("unverified")
        or not all(isinstance(reason, str) and _REFUSAL.match(reason) for reason in old["unverified"])
        or any(old.get(key) != fresh.get(key) for key in ("source_revision", "base", "base_patch", "case_scope"))
        # The collector was the defect: the corrected recognizer chooses unittest where the old run did not.
        or recognized is None
        or recognized.name != "unittest"
        or (old.get("framework") or {}).get("name") == "unittest"
        or _protected(suite) != _protected((fresh.get("commands") or {}).get("suite"))
        or "suite_on_candidate" not in checks
        or not schedule.proof_intact(checks, root=path.parent)
    ):
        return False
    for label, check in checks.items():
        results = _reparse(check)
        if results is None or (
            label in SUITES
            and label in fresh["checks"]
            and set(results["passed"]) != set(fresh["checks"][label]["results"]["passed"])
        ):
            return False
    return True


def _protected(command):
    """The suite command as run: its package and its unittest invocation with ``-v`` added."""
    if not isinstance(command, str) or not command.strip():
        return None
    package, inner = package_scope.entered(command) or (None, command)
    return package, python_tests.verbose_unittest(inner)


def _reparse(check):
    """Complete passing unittest results from an exit-zero check whose results were never parsed."""
    inner = package_scope.within(check.get("command"))
    invocation = python_tests.parse(inner)
    if (
        invocation is None
        or invocation.kind != "unittest"
        or type(check.get("exit_code")) is not int
        or check["exit_code"] != 0
        or check.get("timed_out")
        or check.get("error")
        or check.get("results") is not None
    ):
        return None
    text = Path(check["output"]).read_text(errors="replace")
    results = python_tests.unittest_results(text)
    if (
        results is None
        or results.get("failed")
        or results.get("skipped")
        or not schedule.complete_results({"results": results})
    ):
        return None
    return results
