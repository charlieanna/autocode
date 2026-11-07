"""Pinned OSS experiments over TaskRun. Never submits PRs or promotes code.

Oracle scripts are trusted evaluator code: python ORACLE WORKSPACE emits
{"checks": [{"name": "...", "ok": true}]}. They run outside the Builder's
checkout. This is process/workspace separation, not a security sandbox.
"""

from __future__ import annotations

import argparse
import io
import os
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
from pathlib import Path
from typing import Any

import psutil

try:
    from . import autocode_arena_policy as policy
    from . import autocode_failure_attribution as attribution
    from . import autocode_github as github
    from .autocode_arena_store import ArenaError, Store, digest, encode, read_json, write_json
    from .autocode_issue import brief
    from .autocode_oracle_process import run as run_oracle
    from .autocode_taskrun import TaskRun, TaskRunError
except ImportError:
    import autocode_arena_policy as policy
    import autocode_failure_attribution as attribution
    import autocode_github as github
    from autocode_arena_store import ArenaError, Store, digest, encode, read_json, write_json
    from autocode_issue import brief
    from autocode_oracle_process import run as run_oracle
    from autocode_taskrun import TaskRun, TaskRunError


# Archiving or staging a large upstream tree is bounded separately from metadata
# queries. TypeScript's 61,000-file tree exceeded the metadata deadline (#619).
BULK_GIT_TIMEOUT = 600


def git(repo: Path, *args: str, timeout: int = 60) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args], text=True, capture_output=True, timeout=timeout)
    if proc.returncode:
        raise ArenaError(proc.stderr.strip())
    return proc.stdout.strip()


def checkout(repo: Path, commit: str, workspace: Path) -> None:
    """Archive only the frozen tree, without later commits, remotes or hooks."""
    data = subprocess.run(
        ["git", "-C", str(repo), "archive", "--format=tar", commit],
        capture_output=True,
        check=True,
        timeout=BULK_GIT_TIMEOUT,
    ).stdout
    workspace.mkdir(parents=True)
    if git(repo, "ls-tree", "-r", "--name-only", commit):
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            # Reject links outside the tree and special files even on Python 3.11.
            archive.extractall(workspace, filter="data")
    git(workspace, "init", "-q")
    git(workspace, "add", ".", timeout=BULK_GIT_TIMEOUT)
    git(
        workspace,
        "-c",
        "user.name=AutoCode Arena",
        "-c",
        "user.email=arena@example.invalid",
        "commit",
        "-q",
        "--allow-empty",
        "-m",
        f"Frozen benchmark {commit}",
        timeout=BULK_GIT_TIMEOUT,
    )


def snapshot(workspace: Path) -> str:
    """Hash candidate content, including new files, excluding run bookkeeping."""
    rows: list[tuple[str, Any, Any]] = []
    for path in sorted(workspace.rglob("*")):
        rel = path.relative_to(workspace)
        if any(p in (".git", ".autocode", ".venv", "__pycache__") for p in rel.parts):
            continue
        if path.is_symlink():
            rows.append((str(rel), "link", os.readlink(path)))
        elif path.is_file():
            rows.append((str(rel), path.stat().st_mode & 0o777, digest(path.read_bytes())))
    return digest(encode(rows).encode())


def version_identity(root: Path) -> str:
    # Runtime source, including local changes. Does not hash dependencies or credentials.
    rows = [
        (str(p.relative_to(root)), digest(p.read_bytes()))
        for p in sorted((root / "tools").rglob("*"))
        if p.is_file()
        and "__pycache__" not in p.parts
        and (
            p.suffix in (".py", ".json", ".toml", ".cjs", ".mjs")
            or (p.suffix == ".md" and p.is_relative_to(root / "tools/prompts"))
        )
    ]
    if not rows or not (root / "tools/autocode.py").is_file():
        raise ArenaError("runner must be an AutoCode checkout")
    rows.append(("pyproject.toml", digest((root / "pyproject.toml").read_bytes())))
    return digest(encode(rows).encode())


def evaluate(case: dict, workspace: Path, timeout: int) -> list[dict]:
    oracle = Path(case["oracle_path"])
    if digest(oracle.read_bytes()) != case["oracle_sha256"]:
        raise ArenaError("oracle changed")
    before = snapshot(workspace)
    try:
        code, output, errors = run_oracle(
            [sys.executable, str(oracle), str(workspace)], cwd=oracle.parent, timeout=timeout
        )
    except psutil.Error as error:
        raise ArenaError(f"oracle supervision unavailable: {error}") from error
    if code != 0:
        raise ArenaError(f"oracle failed ({code}): {errors[-600:]}")
    import json

    result = json.loads(output)
    checks = result.get("checks") if isinstance(result, dict) else None
    if not isinstance(checks, list) or not checks:
        raise ArenaError("oracle emitted no checks")
    names = []
    for row in checks:
        if (
            not isinstance(row, dict)
            or not isinstance(row.get("name"), str)
            or not row["name"].strip()
            or type(row.get("ok")) is not bool
        ):
            raise ArenaError("malformed oracle check")
        names.append(row["name"])
    if len(names) != len(set(names)) or not set(case["required_checks"]).issubset(names):
        raise ArenaError("duplicate or missing required oracle checks")
    if snapshot(workspace) != before or digest(oracle.read_bytes()) != case["oracle_sha256"]:
        raise ArenaError("oracle or candidate content changed during evaluation")
    return checks


def ingest(args, store: Store):
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", args.id):
        raise ArenaError("case id must be a simple filename")
    repo = args.repository.resolve()
    if not re.fullmatch(r"[a-f0-9]{40}|[a-f0-9]{64}", args.base):
        raise ArenaError("base must be a full commit SHA, never a moving ref")
    commit = git(repo, "rev-parse", "--verify", f"{args.base}^{{commit}}")
    ref = github.parse_ref(args.issue)
    issue = read_json(args.issue_file) if args.issue_file else github.Client().issue(ref)
    # Historical issues must be supplied as snapshots; newest comments can leak the fix.
    directory = store.root / "cases" / args.id
    if directory.exists():
        raise ArenaError("case directory already exists")
    directory.mkdir(parents=True)
    issue_path, oracle_path = directory / "issue.json", directory / "oracle.py"
    write_json(issue_path, issue)
    shutil.copyfile(args.oracle, oracle_path)
    case = {
        "id": args.id,
        "repository": str(repo),
        "base_commit": commit,
        "base_tree": git(repo, "rev-parse", f"{commit}^{{tree}}"),
        "issue_ref": str(ref),
        "issue_path": str(issue_path),
        "issue_sha256": digest(issue_path.read_bytes()),
        "oracle_path": str(oracle_path),
        "oracle_sha256": digest(oracle_path.read_bytes()),
        "split": args.split,
        "required_checks": args.check,
        "task": brief(ref, issue, args.note),
        "reference_sha256": snapshot(args.reference.resolve()),
    }
    if len(set(args.check)) != len(args.check):
        raise ArenaError("duplicate required check")
    control = directory / "negative-control"
    checkout(repo, commit, control)
    negative = evaluate(case, control, args.oracle_timeout)
    positive = evaluate(case, args.reference.resolve(), args.oracle_timeout)
    if all(r["ok"] for r in negative) or not all(r["ok"] for r in positive):
        raise ArenaError("oracle controls failed: frozen baseline must fail and reference must pass")
    write_json(directory / "controls.json", {"negative": negative, "positive": positive})
    store.add(case)
    return {"case": args.id, "sha256": case["sha256"], "controls": "PASS"}


# The route, reasoning and budget flags Arena forwards to the runner as they are typed (#953). Workspace and
# run identity and operator actions belong to Arena, never a forwarded flag. The runner checks what a
# route means; Arena checks only the flags' own choices, which test_arena keeps equal to autocode_args.
RUNNER_VALUES = (
    "engine",
    "provider",
    "single-model",
    "astra-model",
    "terra-model",
    "sol-model",
    "glm-model",
    "completion-model",
    "plan-reviewer-model",
    "requirements-model",
    "resolver-model",
    "investigator-model",
    "test-command",
    "max-iterations",
)
EFFORTS = ("low", "medium", "high", "xhigh", "max")
EFFORT_FLAGS = (
    "reasoning-effort",
    "astra-reasoning-effort",
    "terra-reasoning-effort",
    "sol-reasoning-effort",
    "completion-reasoning-effort",
    "glm-reasoning-effort",
    "requirements-reasoning-effort",
    "plan-reviewer-reasoning-effort",
    "resolver-reasoning-effort",
    "investigator-reasoning-effort",
)
PIN_ROLES = ("astra", "terra", "sol", "completion")


def runner_options(options) -> argparse.Namespace:
    """Check the runner options an attempt forwards; ArenaError for an unsupported flag or value."""
    config = argparse.ArgumentParser(add_help=False, allow_abbrev=False, exit_on_error=False)
    for name in RUNNER_VALUES:
        config.add_argument("--" + name)
    for name in EFFORT_FLAGS:
        config.add_argument("--" + name, choices=EFFORTS)
    config.add_argument("--pin-model-role", action="append", choices=PIN_ROLES)
    config.add_argument("--joint-planning", action="store_true")
    try:
        selected, unknown = config.parse_known_args(list(options))
    except argparse.ArgumentError as error:
        raise ArenaError(str(error)) from error
    if unknown:
        raise ArenaError(f"unsupported runner option: {unknown[0]}")
    return selected


def attempt(args, store: Store):
    if not args.fixture and not args.i_authorize_live_model_spend:
        raise ArenaError("live attempts require --i-authorize-live-model-spend")
    case = store.case(args.case)
    runner = args.runner.resolve()
    options = tuple(args.option)
    selected = runner_options(options)
    if args.fixture and (selected.engine not in (None, "codex") or selected.provider):
        raise ArenaError("fixture runs must use the bundled Codex fixture, never another engine/provider")
    if args.fixture and selected.engine is None:
        options = ("--engine", "codex", *options)
    ident = uuid.uuid4().hex
    directory = store.root / "attempts" / ident
    workspace = directory / "workspace"
    directory.mkdir(parents=True)
    row = {
        "id": ident,
        "case_id": case["id"],
        "case_sha256": case["sha256"],
        "cohort": args.cohort,
        "version_sha256": version_identity(runner),
        "execution_kind": "fixture" if args.fixture else "live",
        "options": list(options),
        "workspace": str(workspace),
        "verdict": "RUNNING",
        "checks": [],
        "error": None,
        "plan_approval": "benchmark_automation" if args.approve_benchmark_plans else "operator",
        "elapsed_seconds": None,
        "usage": None,
        "estimated_usd": None,
        "failure_attribution": attribution.unknown(),
    }
    store.insert(row)  # Interrupted attempts remain visible and disqualify comparisons.
    started = time.monotonic()
    try:
        checkout(Path(case["repository"]), case["base_commit"], workspace)
        env = {"AUTOCODE_HOME": str(directory / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        if args.fixture:
            bindir = directory / "bin"
            bindir.mkdir()
            shutil.copy2(runner / "tools/live_fixture_provider.py", bindir / "codex")
            (bindir / "codex").chmod(0o755)
            env["PATH"] = f"{bindir}{os.pathsep}{os.environ['PATH']}"
        command = (sys.executable, str(runner / "tools/autocode.py"))
        run = TaskRun.start(
            workspace, case["task"], command=command, options=options, env=env, timeout=args.timeout, cwd=runner
        )
        row["run_dir"] = str(run.run_dir)
        view = run.advance_until_input()
        if args.approve_benchmark_plans and (view.get("needs") or {}).get("kind") == "approve_plan":
            (directory / "approved-plan.txt").write_text(run.show_goal())
            run.approve_plan(view["needs"]["token"])
            view = run.advance_until_input()
        # Answers, human review, quota overrides and deployment are never automated.
        write_json(directory / "status.json", view)
        row.update(
            runner_status=view["status"],
            needs=view.get("needs"),
            usage=view.get("usage"),
            candidate_sha256=snapshot(workspace),
        )
        row["checks"] = evaluate(case, workspace, args.oracle_timeout)
        if view["status"] in ("TASK_COMPLETE", "COMPLETE") and view.get("done") is not True:
            raise ArenaError("runner completion is not currently accepted by its public status view")
        # Include newly created files in the retained patch; bookkeeping is excluded.
        # `git add` applies its ignored-path advice to every pathspec it is handed, negative
        # magic ones included, so naming .autocode here aborts any run whose workspace hides
        # its own bookkeeping, which AutoCode always does. `git diff` has no such advice.
        staged_excludes = (":(exclude).venv", ":(exclude)**/__pycache__/**")
        patch_excludes = (":(exclude).autocode", *staged_excludes)
        git(workspace, "add", "-N", "--", ".", *staged_excludes, timeout=BULK_GIT_TIMEOUT)
        patch = git(workspace, "diff", "--binary", "HEAD", "--", ".", *patch_excludes, timeout=BULK_GIT_TIMEOUT)
        patch_path = directory / "candidate.patch"
        patch_path.write_text(patch + "\n" if patch else "")
        row.update(patch_path=str(patch_path), patch_sha256=digest(patch_path.read_bytes()))
        if version_identity(runner) != row["version_sha256"]:
            raise ArenaError("runner source changed during attempt")
        store.case(args.case)  # Recheck evaluator inputs after the Builder ran.
        row["verdict"] = policy.verdict(view["status"], row["checks"])
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as error:
        row.update(verdict="ERROR", error=str(error))
        if isinstance(error, TaskRunError) and error.run_dir is not None:
            row.setdefault("run_dir", str(error.run_dir))
    row["elapsed_seconds"] = time.monotonic() - started
    store.finish(row)
    write_json(directory / "result.json", row)
    return row


def proposal(store: Store, cohort: str):
    # No automatic root-cause claim: supply evidence for investigation.
    failures = [r for r in store.rows(cohort) if r["verdict"] != "PASS"]
    visible = {c["id"] for c in store.cases() if c["split"] == "development"}
    rows = [
        {
            "attempt": r["id"],
            "case": r["case_id"],
            "outcome": r["verdict"],
            "failed_checks": [c["name"] for c in r["checks"] if not c["ok"]],
            "error": r["error"],
            "root_cause": r["failure_attribution"]["confirmed_diagnosis"] or "UNDETERMINED",
            "failure_attribution": r["failure_attribution"],
            **({"attribution_error": r["attribution_error"]} if r.get("attribution_error") else {}),
        }
        for r in failures
        if r["case_id"] in visible
    ]
    return {
        "cohort": cohort,
        "development_failures": rows,
        "investigate": [
            "requirements",
            "planning",
            "repo_exploration",
            "implementation",
            "testing",
            "validation",
            "completion",
            "provider_or_harness",
        ],
        "acceptance": "fresh paired regression and holdout evaluation; operator review",
        "self_modification": False,
        "promoted": False,
    }


def parser() -> argparse.ArgumentParser:
    """The arguments `autocode arena` reads; `autocode completion` reads them too."""
    result = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    result.add_argument(
        "--arena", type=Path, default=Path(".autocode/arena"), help="Arena store (default: .autocode/arena)"
    )
    sub = result.add_subparsers(dest="action", required=True)
    sub.add_parser("init", help="create the arena store")
    sub.add_parser("cases", help="list the prepared projects and their pinned revisions")
    add = sub.add_parser("ingest", help="pin a project, request, oracle and reference as a case")
    add.add_argument("id", help="case ID")
    add.add_argument("--repository", type=Path, required=True, help="local Git repository to pin")
    add.add_argument("--base", required=True, help="commit the case starts from")
    add.add_argument("--issue", required=True, help="the request text")
    add.add_argument("--issue-file", type=Path, help="file whose text is the request, in place of --issue text")
    add.add_argument("--note", help="free-text note kept with the case")
    add.add_argument("--oracle", type=Path, required=True, help="oracle script that judges an attempt")
    add.add_argument("--reference", type=Path, required=True, help="reference solution the oracle must accept")
    add.add_argument("--check", action="append", required=True, help="check command (repeatable)")
    add.add_argument(
        "--split",
        choices=("development", "regression", "holdout"),
        required=True,
        help="which split the case belongs to",
    )
    add.add_argument("--oracle-timeout", type=int, default=60, help="seconds per oracle run (default: 60)")
    run = sub.add_parser("run", help="run one case for a cohort")
    run.add_argument("case", help="case ID")
    run.add_argument("--cohort", required=True, help="cohort the attempt is recorded under")
    run.add_argument(
        "--runner", type=Path, default=Path(__file__).resolve().parent.parent, help="autocode checkout to run"
    )
    run.add_argument("--option", action="append", default=[], help="repeat; use --option=--FLAG for flags")
    run.add_argument("--fixture", action="store_true", help="run on the fake provider")
    run.add_argument("--i-authorize-live-model-spend", action="store_true", help="allow a paid live-model run")
    run.add_argument("--approve-benchmark-plans", action="store_true", help="approve each plan without review")
    run.add_argument("--timeout", type=int, default=1200, help="per CLI invocation deadline")
    run.add_argument("--oracle-timeout", type=int, default=60, help="seconds per oracle run (default: 60)")
    report = sub.add_parser("report", help="summarize recorded attempts")
    report.add_argument("--cohort", help="only this cohort")
    prop = sub.add_parser("propose", help="propose the next cases to run for a cohort")
    prop.add_argument("--cohort", required=True, help="cohort to propose for")
    annotate = sub.add_parser("annotate", help="append an operator's evidence-backed failure investigation")
    annotate.add_argument("attempt", help="attempt ID")
    annotate.add_argument("--report", type=Path, required=True, help="attribution JSON report")
    annotate.add_argument("--reviewer", help="explicit independent reviewer for a confirmed diagnosis")
    gate = sub.add_parser("compare", help="compare a candidate cohort against a baseline")
    gate.add_argument("--baseline", required=True, help="baseline cohort")
    gate.add_argument("--candidate", required=True, help="candidate cohort")
    return result


def main(argv=None):
    arguments = parser()
    args = arguments.parse_args(argv)
    store = Store(args.arena)
    try:
        if args.action == "init":
            store.initialize()
            result = {"initialized": str(store.root)}
        elif not store.exists():  # one message for every action, not an errno or a sqlite traceback
            raise ArenaError(f"No arena store at {store.root}; run autocode arena init first")
        elif args.action == "cases":
            result = {
                "cases": [
                    {key: case[key] for key in ("id", "issue_ref", "split", "base_commit")}
                    for case in (store.case(row["id"]) for row in store.cases())
                ]
            }
        elif args.action == "ingest":
            result = ingest(args, store)
        elif args.action == "run":
            if args.timeout <= 0 or args.oracle_timeout <= 0:
                raise ArenaError("deadlines must be positive")
            result = attempt(args, store)
        elif args.action == "report":
            result = policy.summarize(store.rows(args.cohort))
        elif args.action == "propose":
            result = proposal(store, args.cohort)
            write_json(store.root / "proposals" / (digest(args.cohort.encode()) + ".json"), result)
        elif args.action == "annotate":
            result = store.annotate(args.attempt, attribution.read_json(args.report), reviewer=args.reviewer)
        else:
            cases = [store.case(c["id"]) for c in store.cases()]
            result = policy.compare(cases, store.rows(args.baseline), store.rows(args.candidate))
        print(encode(result))
        if args.action == "run":
            return 0 if result["verdict"] == "PASS" else 2 if result["verdict"] == "STOPPED" else 1
        if args.action == "compare" and result["decision"] == "REJECT":
            return 1
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError, tarfile.TarError) as error:
        arguments.exit(2, f"arena error: {error}\n")


def cli():
    return main()


if __name__ == "__main__":
    raise SystemExit(main())
