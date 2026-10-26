"""Verify an externally produced change through public review and runner proof.

Only the separate review checkout is driven by TaskRun. No private task state
is read, no plan approval is synthesized, and verification never launches a
Builder or repairs the user's candidate.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

try:
    from . import autocode_agent_env as agent_env
    from . import autocode_check_replay as check_replay
    from . import autocode_util as util
    from . import autocode_verify as proof_runner
    from . import autocode_verify_contract as criteria_policy
    from . import autocode_verify_record as records
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_check_replay as check_replay
    import autocode_util as util
    import autocode_verify as proof_runner
    import autocode_verify_contract as criteria_policy
    import autocode_verify_record as records
    from autocode_taskrun import TaskRun, TaskRunError


def git_result(workspace, *args, accepted=(0,)):
    environment = {key: value for key, value in agent_env.scrubbed(os.environ).items() if not key.startswith("GIT_")}
    environment.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT="0")
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(workspace), *map(str, args)],
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
    )
    if result.returncode not in accepted:
        raise ValueError("Git input refused: " + result.stderr.strip())
    return result


def git(workspace, *args):
    return git_result(workspace, *args).stdout.strip()


def require_output_location(workspace, root):
    """Evidence may be outside the input or in its ignored evidence namespace."""
    try:
        relative = root.relative_to(workspace)
    except ValueError:
        return
    if not relative.parts or relative.parts[0] == ".git":
        raise ValueError("Verification output must not modify the original source or Git metadata")
    if git(workspace, "ls-files", "-z", "--", relative.as_posix()):
        raise ValueError("Verification output must not modify Git-visible original source")
    ignored = git_result(workspace, "check-ignore", "--no-index", "--quiet", "--", relative.as_posix(), accepted=(0, 1))
    if ignored.returncode != 0:
        raise ValueError("Verification output inside the original checkout must be Git-ignored")


def revision(workspace, value):
    if not value or value.startswith("-"):
        raise ValueError("Use a commit or branch name")
    return git(workspace, "rev-parse", "--verify", "--end-of-options", value + "^{commit}")


def clone(workspace, destination, commit):
    git(workspace, "clone", "--shared", "--no-checkout", "--quiet", "--", workspace, destination)
    git(destination, "checkout", "--detach", "--quiet", commit)


def copy_changes(workspace, candidate, changes):
    for name, status in sorted(changes.items(), key=lambda row: row[1] != "deleted"):
        path = Path(name)
        if path.is_absolute() or ".." in path.parts or name.endswith("/"):
            raise ValueError("Unsupported candidate path: " + name)
        target = candidate / path
        for parent in target.relative_to(candidate).parents:
            if parent != Path(".") and (candidate / parent).is_symlink():
                raise ValueError("Candidate path has a symlinked parent")
        if target.is_symlink() or target.is_file():
            target.unlink()
        elif target.is_dir():
            raise ValueError("Nested repositories and directory replacements need an explicit patch")
        if status != "deleted":
            target.parent.mkdir(parents=True, exist_ok=True)
            source = workspace / path
            if source.is_symlink():
                target.symlink_to(os.readlink(source))
            else:
                shutil.copy2(source, target)


def verify(args, contract):
    records.require_git_environment()
    workspace = args.workspace.resolve(strict=True)
    original_snapshot = records.source_identity(workspace)
    original_index = records.index_identity(workspace)
    base = revision(workspace, args.base)
    head = revision(workspace, args.branch or "HEAD")
    if args.patch and args.patch.is_symlink():
        raise ValueError("The patch must be a regular file")
    patch = args.patch.resolve(strict=True) if args.patch else None
    if patch and args.branch:
        raise ValueError("--patch and --branch are alternatives")
    if patch and (not patch.is_file() or patch.is_symlink()):
        raise ValueError("The patch must be a regular file")
    if not args.branch and not patch:
        records.require_review_inputs(workspace)
    changes = proof_runner.changed_files(workspace, head) if not args.branch and not patch else {}
    root = args.out.absolute() if args.out else workspace / ".autocode" / "verification" / uuid.uuid4().hex
    require_output_location(workspace, root)
    _, checkout_policy = records.review_support()
    root = checkout_policy.create_root(root)
    original = {"workspace": str(workspace), "snapshot": original_snapshot, "started_at": util.now()}
    util.atomic_json(root / "criteria.json", contract)
    candidate = root / "candidate"
    clone(workspace, candidate, base if patch else head)
    if patch:
        saved_patch = root / "input.patch"
        shutil.copy2(patch, saved_patch)
        git(candidate, "apply", "--", saved_patch)
    else:
        copy_changes(workspace, candidate, changes)
    records.require_review_inputs(candidate)
    candidate_changes = proof_runner.changed_files(candidate, base)
    if not candidate_changes:
        raise ValueError("The selected candidate has no change against --base")
    current = records.source_identity(candidate)
    comparison = {
        "comparison_base": base,
        "candidate_head": current["head"],
        "changed_files": candidate_changes,
        "untracked_files": sorted(
            name for name in git(candidate, "ls-files", "--others", "--exclude-standard", "-z").split("\0") if name
        ),
    }
    comparison_diff = git_result(candidate, "diff", "--no-ext-diff", "--no-textconv", "--binary", base, "--").stdout
    review_workspace = root / "independent-review"
    clone(candidate, review_workspace, current["head"])
    copy_changes(candidate, review_workspace, proof_runner.changed_files(candidate, current["head"]))
    records.require_review_inputs(review_workspace)
    reviewed_source = records.ReviewSource(review_workspace)
    brief = (
        "Review this existing change against the exact caller-declared acceptance criteria below. "
        "Check the behavior, not just syntax. Report a blocking finding for any failed or unsupported "
        "criterion; do not approve merely because a command exits successfully. "
        "Do not build or repair the candidate. Compare the selected workspace against the exact "
        "comparison_base below, including committed changes even when the working tree is clean. "
        "Read the listed untracked files as well; Git diff does not include their contents. "
        "change_patch must be empty.\n\nCALLER CRITERIA\n"
        + json.dumps(contract, indent=2)
        + "\n\nCOMPARISON CONTEXT\n"
        + json.dumps(comparison, indent=2)
        + "\n\nGIT DIFF AGAINST COMPARISON BASE\n"
        + comparison_diff
    )
    options = [
        "--workflow",
        "review",
        "--engine",
        args.engine,
        "--no-adaptive-planning",
        "--max-iterations",
        "1",
        "--max-seconds",
        str(args.max_seconds),
        "--max-stage-seconds",
        str(args.max_stage_seconds),
        "--evidence-provenance",
        args.evidence_provenance,
    ]
    if getattr(args, "related_profile", None):
        options = [
            "--workflow",
            "review",
            "--related-profile",
            str(args.related_profile),
            "--related-profile-sha256",
            args.related_profile_sha256,
            "--max-seconds",
            str(args.max_seconds),
            "--max-stage-seconds",
            str(args.max_stage_seconds),
        ]
    if args.provider and not getattr(args, "related_profile", None):
        options += ["--provider", args.provider]
    if args.model and not getattr(args, "related_profile", None):
        options += ["--sol-model", args.model]
    if args.reasoning_effort and not getattr(args, "related_profile", None):
        options += ["--sol-reasoning-effort", args.reasoning_effort]
    if getattr(args, "max_cost_usd", None) is not None:
        options += ["--max-cost-usd", str(args.max_cost_usd)]
    try:
        child_run = TaskRun.start(
            review_workspace,
            brief,
            options=options,
            command=(sys.executable, "-B", str(Path(__file__).with_name("autocode.py"))),
            env=checkout_policy.task_environment(),
            timeout=args.max_seconds + 120,
        )
        view = child_run.status(inspect_evidence=True)
        util.atomic_json(root / "review-status.json", view)
        if not view["done"] or view.get("workflow") != "review":
            raise TaskRunError("Independent review stopped without a completed review verdict")
        review, child = records.review_findings(child_run, reviewed_source)
    except TaskRunError as error:
        failure = {"verdict": "UNVERIFIED", "reasons": [str(error)], "record": str(root)}
        util.atomic_json(root / "failure.json", failure)
        return failure
    reasons = []
    framework = proof_runner.detect_framework(candidate, base=base)
    base_suite = None
    suite = args.suite_command or (framework.suite if framework else None)
    if suite:
        # The suite on the base is what lets the proof see a test that passed there and is now
        # skipped, deselected or missing (#1111); a task run supplies it the same way.
        try:
            base_suite = proof_runner.baseline(
                candidate,
                base,
                root / "base-suite",
                framework=framework,
                suite_command=suite,
                timeout=args.check_timeout,
                dependencies_from=workspace,
            )
        except (OSError, RuntimeError, ValueError) as error:
            reasons.append(f"The project suite could not be run on the base, so preservation is unproven: {error}")
    elif contract["proof_kind"] == "preserve":
        reasons.append("No project suite was found or given (--suite-command), so preservation is unproven")
    proof = proof_runner.verify(
        candidate,
        base,
        root / "regression",
        framework=framework,
        suite_command=args.suite_command,
        base_suite=base_suite,
        timeout=args.check_timeout,
        dependencies_from=workspace,
        new_behavior=contract["proof_kind"] == "feature",
        preserve_only=contract["proof_kind"] == "preserve",
    )
    checks = [
        {"command": row["verification_method"], "exit_code": 0, "evidence_ref": "caller-declared-check"}
        for row in contract["acceptance_criteria"]
    ]
    descriptor = root / "check-request.json"
    util.atomic_json(
        descriptor,
        {
            "contract_sha256": contract["sha256"],
            "checks": checks,
            "source_revision": current["revision"],
            "origin": "caller_declaration",
        },
    )
    try:
        replay = check_replay.replay(
            checks,
            candidate,
            root,
            {"output": str(descriptor), "source_revision": current["revision"]},
            proof_runner.scratch_run,
            timeout=args.check_timeout,
        )
    except (ValueError, OSError, RuntimeError) as error:
        reasons.append("Independent check replay refused: " + str(error))
        replay = {"verdict": "FAIL", "checks": [], "source_revision": current["revision"]}
    if records.source_identity(candidate) != current:
        reasons.append("Candidate changed during verification")
    if records.source_identity(workspace) != original_snapshot or records.index_identity(workspace) != original_index:
        reasons.append("Original checkout changed during verification")
    fresh_review, fresh_child = records.review_findings(child_run, reviewed_source)
    if fresh_review != review or fresh_child != child:
        reasons.append("Independent review evidence changed during verification")
    return records.publish(
        root,
        contract=contract,
        current=current,
        proof=proof,
        replay=replay,
        child=child,
        review=review,
        review_workspace=review_workspace,
        reasons=reasons,
        original=original,
        original_index=original_index,
        candidate=candidate,
    )


def parser():
    result = argparse.ArgumentParser(
        prog="autocode verify", description="Verify an existing change without planning, approval or building."
    )
    result.add_argument("--workspace", type=Path, default=Path.cwd(), help="Existing Git repository")
    result.add_argument("--base", help="Exact comparison commit or branch; required for a new verification")
    selection = result.add_mutually_exclusive_group()
    selection.add_argument("--branch", help="Verify this committed branch instead of current working-tree files")
    selection.add_argument("--patch", type=Path, help="Apply this patch to --base in the private candidate")
    criteria = result.add_mutually_exclusive_group()
    criteria.add_argument("--criteria", help="Explicit acceptance criteria as a JSON object")
    criteria.add_argument("--criteria-file", type=Path, help="Read the same criteria JSON from a file")
    result.add_argument("--out", type=Path, help="Fresh output directory; an existing destination is refused")
    result.add_argument(
        "--record", type=Path, help="Read an existing verification and recheck source/evidence freshness"
    )
    result.add_argument("--engine", choices=("codex", "opencode"), default="opencode")
    result.add_argument("--provider", help="Configured provider for the independent review")
    result.add_argument("--related-profile", type=Path, help="Pinned role routing and limits from a coordinating run")
    result.add_argument("--related-profile-sha256", help="Exact SHA-256 of --related-profile")
    result.add_argument("--model", help="Independent Code Reviewer model")
    result.add_argument("--reasoning-effort", choices=("low", "medium", "high", "xhigh", "max"), default="medium")
    result.add_argument("--evidence-provenance", choices=("fake", "live", "unknown"), default="unknown")
    result.add_argument("--suite-command", help="Project suite command for external-change proof")
    result.add_argument("--check-timeout", type=int, default=900, help="Seconds per independent proof/check command")
    result.add_argument("--max-seconds", type=int, default=900, help="Total review provider time limit")
    result.add_argument("--max-stage-seconds", type=int, default=600, help="Review stage time limit")
    result.add_argument("--max-cost-usd", type=float, help="Cost cap for the independent review")
    result.add_argument("--json", action="store_true", help="Print the verdict and canonical report as JSON")
    return result


def cli(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    try:
        records.require_git_environment()
        if args.record:
            if any((args.base, args.branch, args.patch, args.criteria, args.criteria_file, args.out)):
                raise ValueError("--record cannot be combined with a new verification input")
            result = records.read(args.record)
        else:
            if not args.base or (args.criteria is None and args.criteria_file is None):
                raise ValueError(
                    "Supply --base and explicit --criteria or --criteria-file; criteria are never inferred"
                )
            if min(args.check_timeout, args.max_seconds, args.max_stage_seconds) <= 0:
                raise ValueError("Verification time limits must be positive")
            text = args.criteria if args.criteria is not None else args.criteria_file.read_text()
            contract = criteria_policy.load(text)
            result = verify(args, contract)
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, subprocess.SubprocessError) as error:
        print(
            json.dumps({"verdict": "UNVERIFIED", "error": str(error)})
            if args.json
            else "Verification refused: " + str(error),
            file=sys.stderr,
        )
        return 4
    if args.json:
        print(json.dumps(result, indent=2, allow_nan=False))
    else:
        print(result["verdict"] + ": " + result["record"])
        for reason in result.get("reasons", []):
            print("  " + reason)
        report = result.get("evidence_report") or {}
        if report.get("json_path"):
            print("Evidence: " + report["json_path"])
    return 0 if result["verdict"] == "PASS" else 2


if __name__ == "__main__":
    raise SystemExit(cli())
