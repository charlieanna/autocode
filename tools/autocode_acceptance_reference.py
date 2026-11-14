"""Fresh original-reference checks for passing tests on a retained candidate.

These observations classify acceptance generation only. They never supply the
final regression proof, inherit a historical failure, or authorize completion.
"""

from pathlib import Path

try:
    from . import autocode_base_patch as base_patch
    from . import autocode_command_receipt as command_receipt
    from . import autocode_follow_up as follow_up
    from . import autocode_joint_inputs as joint_inputs
    from . import autocode_launch_inputs as launch_inputs
    from . import autocode_proof_stability as stability
    from . import autocode_run_base as run_base
    from . import autocode_source_scope as source_scope
    from . import autocode_test_cases as test_cases
    from . import autocode_test_role_inventory as role_inventory
    from . import autocode_util as util
    from . import autocode_verification_commands as commands
    from . import autocode_verification_schedule as schedule
    from . import autocode_verify as verify
except ImportError:
    import autocode_base_patch as base_patch
    import autocode_command_receipt as command_receipt
    import autocode_follow_up as follow_up
    import autocode_joint_inputs as joint_inputs
    import autocode_launch_inputs as launch_inputs
    import autocode_proof_stability as stability
    import autocode_run_base as run_base
    import autocode_source_scope as source_scope
    import autocode_test_cases as test_cases
    import autocode_test_role_inventory as role_inventory
    import autocode_util as util
    import autocode_verification_commands as commands
    import autocode_verification_schedule as schedule
    import autocode_verify as verify


def passing(cases, rows, run, inherited):
    """Only wholly passing restore cases need classification, using observed IDs."""
    reported = {row["case_id"]: row for row in rows}
    named = [{**case, "test_name": reported[case["id"]]["test_name"]} for case in cases]
    results = run.get("results") or {}
    passes = test_cases.match_cases(named, results.get("passed") or [])
    nonpasses = test_cases.match_cases(named, [*(results.get("failed") or []), *(results.get("skipped") or [])])
    return {
        case["id"]: sorted(passes[case["id"]])
        for case in named
        if case.get("kind") != "preserve"
        and case["id"] not in inherited
        and passes[case["id"]]
        and not nonpasses[case["id"]]
    }


def candidate_problem(run):
    if run.get("error") or run.get("timed_out") or not schedule.complete_results(run):
        return "Original reference classification requires complete candidate test results"
    return ""


def collect(state, workspace, run_dir, out, selected, files, command=None):
    """Run exactly the observed cases on original product code plus today's tests."""
    workspace, out = Path(workspace), Path(out)
    tree = out / "tree"
    uncertain = False
    try:
        base = run_base.base_commit(state, workspace)
        if not base:
            raise ValueError("no recorded original reference")
        before = source_scope.snapshot(workspace, state)
        inputs = launch_inputs.guard(state, workspace, run_dir)
        dependencies = state.get("project_workspace") or str(workspace)
        changes = verify.changed_files(workspace, base, source_paths=source_scope.paths(state))
        roles = role_inventory.from_git(workspace, base, verify.is_test_path, changes=changes)
        overlay = {path: status for path, status in changes.items() if roles.is_test(path)}
        if any(not roles.is_test(path) for path in files):
            raise ValueError("reported test file has no independent test-file classification")
        overlay.update({path: "modified" for path in files})
        framework = commands.command_framework(command) if command else verify.detect_framework(workspace)
        if framework is None:
            raise ValueError("no recognized test runner for original reference")
        selected_ids = sorted({test for names in selected.values() for test in names})
        narrowed = stability.narrow(framework.name, command or framework.targeted(files), selected_ids)
        if not narrowed:
            raise ValueError("the test runner cannot select the observed original-reference cases")
        # A reviewed change is the reference for its follow-up. An operator patch
        # is checked against its saved pin, just as the final proof checks it.
        reviewed = (follow_up.review_findings(state) or {}).get("change_patch")
        patch = None
        if reviewed:
            candidates = [workspace / reviewed, Path(dependencies) / reviewed]
            patch = next((path for path in candidates if path.is_file()), candidates[0])
        if pinned := base_patch.pinned(state):
            if patch:
                raise ValueError("combined reviewed and operator reference patches need final-proof verification")
            patch, reason = base_patch.check(pinned, workspace, base)
            if reason:
                raise ValueError(reason)
        verify.make_tree(
            workspace,
            base,
            tree,
            workspace,
            overlay,
            dependencies_from=dependencies,
            ignored_inputs=inputs,
            patch=patch,
        )
        joint_inputs.copy(workspace, tree)
        for root in dict.fromkeys((str(workspace.resolve()), str(workspace))):
            narrowed = narrowed.replace(root, str(tree))
        receipt = verify.run_suite(framework, narrowed, tree, out, "original-reference", timeout=verify.DEFAULT_TIMEOUT)
        after_inputs = launch_inputs.guard(state, workspace, run_dir)
        if before != source_scope.snapshot(workspace, state) or (inputs.identity if inputs else None) != (
            after_inputs.identity if after_inputs else None
        ):
            raise ValueError("source or launch inputs changed during original-reference check")
        if receipt.get("error") or receipt.get("timed_out") or not schedule.complete_results(receipt):
            raise ValueError("original-reference execution did not produce complete named results")
        if not schedule.intact(receipt, root=out):
            raise ValueError("original-reference output capture is incomplete")
        results = receipt["results"]
        passed, failed = set(results["passed"]), set(results["failed"])
        if set(results["skipped"]) & set(selected_ids) or not set(selected_ids) <= passed | failed:
            raise ValueError("original-reference cases were skipped or not executed")
        if set(results.get("setup_errors") or {}) & set(selected_ids):
            raise ValueError("selected original-reference tests failed during test setup")
        verdicts = {}
        for cid, names in selected.items():
            ids = set(names)
            if ids <= failed:
                verdicts[cid] = "fails"
            elif ids <= passed:
                verdicts[cid] = "passes"
            else:
                raise ValueError(f"{cid} has mixed original-reference outcomes")
        return {
            "base": base,
            "source_revision": before["revision"],
            "selected": selected,
            "test_files": {path: util.file_hash(workspace / path) for path in files},
            "receipt": receipt,
            "verdicts": verdicts,
        }
    except command_receipt.OwnershipUncertain:
        uncertain = True
        raise
    except (OSError, ValueError, RuntimeError) as error:
        return {"error": f"Cannot classify passing tests against the original reference: {error}"}
    finally:
        if not uncertain and tree.exists():
            verify.remove_tree(workspace, tree)
