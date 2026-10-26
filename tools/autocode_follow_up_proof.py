"""Fresh preservation checks against an authenticated completed turn's source.

The run's original regression reference never changes. A new follow-up guard
also needs the delivered code it promises to preserve, rather than the earlier
buggy code. A historical checkpoint supplies source only, never a PASS receipt.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_acceptance_provenance as provenance
    from . import autocode_code_checkpoints as checkpoints
    from . import autocode_command_receipt as command_receipt
    from . import autocode_contract_identity as contracts
    from . import autocode_test_cases as test_cases
    from . import autocode_util as util
except ImportError:
    import autocode_acceptance_provenance as provenance
    import autocode_code_checkpoints as checkpoints
    import autocode_command_receipt as command_receipt
    import autocode_contract_identity as contracts
    import autocode_test_cases as test_cases
    import autocode_util as util


def context(state: RunState, workspace, run_dir, cases):
    """Bind only new guards to the exact previously completed source, or refuse."""
    if not state.get("turns"):
        return None
    previous = provenance.prior_contract(state)
    if previous is None or not contracts.approved(state):
        return None
    old = {row["id"]: row for row in previous["body"].get("acceptance_criteria", [])}
    current_contract = state.get("goal_contract") or {}
    current = {row["id"]: row for row in current_contract["body"].get("acceptance_criteria", [])}
    guards = [row for row in cases if row.get("kind") == "preserve" and row["id"] in current and row["id"] not in old]
    if not guards:
        return None
    result = {"cases": guards, "previous_contract": contracts.token(previous)}
    try:
        turn = state["turns"][-1]
        stages = state.get("stages", [])[: turn["stage_index"]]
        last = next(row["source_revision"] for row in reversed(stages) if row.get("source_revision"))
        rows = [
            row
            for row in state.get("code_checkpoints", [])
            if row.get("available")
            and row.get("contract_hash") == previous["hash"]
            and row.get("source_revision") == last
        ]
        if len(rows) != 1:
            raise ValueError("no unique source checkpoint matches the completed turn")
        row = rows[0]
        path = Path(row["receipt"])
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(Path(run_dir).resolve()):
            raise ValueError("checkpoint receipt is not a regular file in this run")
        if util.file_hash(path) != row["receipt_sha256"]:
            raise ValueError("checkpoint receipt changed")
        saved = util.read(path)
        if any(saved.get(key) != row.get(key) for key in ("id", "commit", "contract_hash", "source_revision")):
            raise ValueError("checkpoint identity changed")
        if saved.get("run_dir") != str(Path(run_dir).resolve()) or saved.get("workspace") != str(
            Path(workspace).resolve()
        ):
            raise ValueError("checkpoint belongs to another run or workspace")
        if saved["source"].get("revision") != last or checkpoints.tree_files(
            workspace, saved["commit"]
        ) != checkpoints.content_files(saved["source"]):
            raise ValueError("checkpoint source cannot be verified")
        result.update(commit=saved["commit"], receipt_sha256=row["receipt_sha256"], source_revision=last)
    except (KeyError, StopIteration, OSError, ValueError, TypeError) as error:
        result["error"] = "Follow-up preservation baseline cannot be verified: " + str(error)
    return result


def collect(context, workspace, out, result, framework, *, verify, dependencies, inputs, timeout):
    """Run today's tests on prior delivered source; unrelated new tests may fail."""
    if context.get("error"):
        return {"error": context["error"]}
    if framework is None:
        return {"error": "Follow-up preservation needs a named-test framework"}
    tests = result.get("test_files") or []
    command = (result.get("commands") or {}).get("regression")
    if not tests or not command:
        return {"error": "Follow-up preservation has no current targeted tests"}
    tree = Path(out) / "follow-up-baseline"
    try:
        verify.make_tree(
            workspace,
            context["commit"],
            tree,
            workspace,
            {path: "modified" for path in tests},
            dependencies_from=dependencies,
            ignored_inputs=inputs,
        )
        return verify.run_suite(framework, command, tree, out, "follow-up-baseline", timeout=timeout)
    finally:
        if tree.exists():
            verify.remove_tree(workspace, tree)


def apply(proof, context, receipt, result, *, refused=None):
    """Add fresh pass-before/pass-after evidence without changing original flips."""
    proof["follow_up_baseline"] = {key: value for key, value in context.items() if key != "cases"}
    proof.setdefault("case_tests", {})
    results = receipt.get("results") or {}
    refused = refused or {}
    command_failure = receipt.get("exit_code") not in (0, 1) or (
        receipt.get("exit_code") == 1 and not results.get("failed")
    )
    invalid = (
        not command_receipt.completed(receipt)
        or receipt.get("error")
        or receipt.get("timed_out")
        or not results.get("complete")
        or results.get("collection_errors")
        or command_failure
    )
    for case in context["cases"]:
        cid = case["id"]
        framework = proof.get("framework") or {}
        framework = framework.get("name") if isinstance(framework, dict) else framework
        before = test_cases.match_cases(
            [case], [test for test in results.get("passed") or [] if test not in refused], framework=framework
        )[cid]
        candidate = set(proof.get("pass_to_pass") or []) | set(proof.get("fail_to_pass") or [])
        matches = [test for test in before if test in candidate]
        before_failed = test_cases.match_cases(
            [case], [*(results.get("failed") or []), *(results.get("skipped") or [])], framework=framework
        )[cid]
        broken = test_cases.match_cases([case], result.get("failed_on_candidate") or [], framework=framework)[cid]
        proof["case_tests"][cid] = matches if not invalid and not broken and not before_failed else []
        if invalid:
            proof.setdefault("unverified", []).append(
                f"Follow-up guard {cid} has no complete fresh baseline check: {receipt.get('error') or 'test execution incomplete'}"
            )
        elif not matches or broken or before_failed:
            proof.setdefault("failures", []).append(
                f"Follow-up guard {cid} must pass on the completed turn's source and on the current candidate"
            )
    if proof.get("failures"):
        proof["verdict"] = "FAIL"
    elif proof.get("unverified"):
        proof["verdict"] = "UNVERIFIED"
