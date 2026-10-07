"""Pinned OSS experiments over TaskRun. Never submits PRs or promotes code.

Oracle scripts are trusted evaluator code: python ORACLE WORKSPACE emits
{"checks": [{"name": "...", "ok": true}]}. They run outside the Builder's
checkout. This is process/workspace separation, not a security sandbox.
"""
from __future__ import annotations

import argparse
import io
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tarfile
import time
import uuid
import psutil

try:
    from . import autocode_arena_policy as policy
    from .autocode_arena_store import Store, ArenaError, digest, encode, read_json, write_json
    from .autocode_taskrun import TaskRun, TaskRunError
    from . import autocode_github as github
    from .autocode_issue import brief
    from .autocode_oracle_process import run as run_oracle
except ImportError:
    import autocode_arena_policy as policy
    from autocode_arena_store import Store, ArenaError, digest, encode, read_json, write_json
    from autocode_taskrun import TaskRun, TaskRunError
    import autocode_github as github
    from autocode_issue import brief
    from autocode_oracle_process import run as run_oracle


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
    data = subprocess.run(["git", "-C", str(repo), "archive", "--format=tar", commit],
                          capture_output=True, check=True, timeout=BULK_GIT_TIMEOUT).stdout
    workspace.mkdir(parents=True)
    if git(repo, "ls-tree", "-r", "--name-only", commit):
        with tarfile.open(fileobj=io.BytesIO(data)) as archive:
            # Reject links outside the tree and special files even on Python 3.11.
            archive.extractall(workspace, filter="data")
    git(workspace, "init", "-q")
    git(workspace, "add", ".", timeout=BULK_GIT_TIMEOUT)
    git(workspace, "-c", "user.name=AutoCode Arena", "-c", "user.email=arena@example.invalid",
        "commit", "-q", "--allow-empty", "-m", f"Frozen benchmark {commit}", timeout=BULK_GIT_TIMEOUT)


def snapshot(workspace: Path) -> str:
    """Hash candidate content, including new files, excluding run bookkeeping."""
    rows = []
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
    rows = [(str(p.relative_to(root)), digest(p.read_bytes()))
            for p in sorted((root / "tools").rglob("*"))
            if p.is_file() and "__pycache__" not in p.parts and p.suffix in (".py", ".json", ".toml", ".cjs", ".mjs")]
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
        code, output, errors = run_oracle([sys.executable, str(oracle), str(workspace)],
                                          cwd=oracle.parent, timeout=timeout)
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
        if (not isinstance(row, dict) or not isinstance(row.get("name"), str)
                or not row["name"].strip() or type(row.get("ok")) is not bool):
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
    case = {"id": args.id, "repository": str(repo), "base_commit": commit,
            "base_tree": git(repo, "rev-parse", f"{commit}^{{tree}}"),
            "issue_ref": str(ref), "issue_path": str(issue_path), "issue_sha256": digest(issue_path.read_bytes()),
            "oracle_path": str(oracle_path), "oracle_sha256": digest(oracle_path.read_bytes()),
            "split": args.split, "required_checks": args.check,
            "task": brief(ref, issue, args.note), "reference_sha256": snapshot(args.reference.resolve())}
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


def attempt(args, store: Store):
    if not args.fixture and not args.i_authorize_live_model_spend:
        raise ArenaError("live attempts require --i-authorize-live-model-spend")
    case = store.case(args.case)
    runner = args.runner.resolve()
    options = tuple(args.option)
    # Workspace/run identity and operator actions belong to Arena, not forwarded flags.
    config = argparse.ArgumentParser(add_help=False, allow_abbrev=False, exit_on_error=False)
    for name in ("engine", "provider", "astra-model", "terra-model", "sol-model", "glm-model",
                 "completion-model", "plan-reviewer-model", "test-command", "max-iterations"):
        config.add_argument("--" + name)
    config.add_argument("--joint-planning", action="store_true")
    try:
        selected, unknown = config.parse_known_args(options)
    except argparse.ArgumentError as error:
        raise ArenaError(str(error)) from error
    if unknown:
        raise ArenaError(f"unsupported runner option: {unknown[0]}")
    if args.fixture and (selected.engine not in (None, "codex") or selected.provider):
        raise ArenaError("fixture runs must use the bundled Codex fixture, never another engine/provider")
    if args.fixture and selected.engine is None:
        options = ("--engine", "codex", *options)
    ident = uuid.uuid4().hex
    directory = store.root / "attempts" / ident
    workspace = directory / "workspace"
    directory.mkdir(parents=True)
    row = {"id": ident, "case_id": case["id"], "case_sha256": case["sha256"],
           "cohort": args.cohort, "version_sha256": version_identity(runner),
           "execution_kind": "fixture" if args.fixture else "live", "options": list(options),
           "workspace": str(workspace), "verdict": "RUNNING", "checks": [], "error": None,
           "plan_approval": "benchmark_automation" if args.approve_benchmark_plans else "operator",
           "elapsed_seconds": None, "usage": None, "estimated_usd": None}
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
        run = TaskRun.start(workspace, case["task"], command=command, options=options, env=env,
                            timeout=args.timeout, cwd=runner)
        row["run_dir"] = str(run.run_dir)
        view = run.advance_until_input()
        if args.approve_benchmark_plans and (view.get("needs") or {}).get("kind") == "approve_plan":
            (directory / "approved-plan.txt").write_text(run.show_goal())
            run.approve_plan(view["needs"]["token"])
            view = run.advance_until_input()
        # Answers, human review, quota overrides and deployment are never automated.
        write_json(directory / "status.json", view)
        row.update(runner_status=view["status"], needs=view.get("needs"), usage=view.get("usage"),
                   candidate_sha256=snapshot(workspace))
        row["checks"] = evaluate(case, workspace, args.oracle_timeout)
        if view["status"] in ("TASK_COMPLETE", "COMPLETE") and view.get("done") is not True:
            raise ArenaError("runner completion is not currently accepted by its public status view")
        # Include newly created files in the retained patch; bookkeeping is excluded.
        excludes = (":(exclude).autocode", ":(exclude).venv", ":(exclude)**/__pycache__/**")
        git(workspace, "add", "-N", "--", ".", *excludes, timeout=BULK_GIT_TIMEOUT)
        patch = git(workspace, "diff", "--binary", "HEAD", "--", ".", *excludes, timeout=BULK_GIT_TIMEOUT)
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
    rows = [{"attempt": r["id"], "case": r["case_id"], "outcome": r["verdict"],
             "failed_checks": [c["name"] for c in r["checks"] if not c["ok"]],
             "error": r["error"], "root_cause": "UNDETERMINED"} for r in failures if r["case_id"] in visible]
    return {"cohort": cohort, "development_failures": rows,
            "investigate": ["requirements", "planning", "repo_exploration", "implementation",
                            "testing", "validation", "completion", "provider_or_harness"],
            "acceptance": "fresh paired regression and holdout evaluation; operator review",
            "self_modification": False, "promoted": False}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--arena", type=Path, default=Path(".autocode/arena"))
    sub = parser.add_subparsers(dest="action", required=True)
    sub.add_parser("init")
    sub.add_parser("cases", help="list the prepared projects and their pinned revisions")
    add = sub.add_parser("ingest")
    add.add_argument("id")
    add.add_argument("--repository", type=Path, required=True)
    add.add_argument("--base", required=True)
    add.add_argument("--issue", required=True)
    add.add_argument("--issue-file", type=Path)
    add.add_argument("--note")
    add.add_argument("--oracle", type=Path, required=True)
    add.add_argument("--reference", type=Path, required=True)
    add.add_argument("--check", action="append", required=True)
    add.add_argument("--split", choices=("development", "regression", "holdout"), required=True)
    add.add_argument("--oracle-timeout", type=int, default=60)
    run = sub.add_parser("run")
    run.add_argument("case")
    run.add_argument("--cohort", required=True)
    run.add_argument("--runner", type=Path, default=Path(__file__).resolve().parent.parent)
    run.add_argument("--option", action="append", default=[], help="repeat; use --option=--FLAG for flags")
    run.add_argument("--fixture", action="store_true")
    run.add_argument("--i-authorize-live-model-spend", action="store_true")
    run.add_argument("--approve-benchmark-plans", action="store_true")
    run.add_argument("--timeout", type=int, default=1200, help="per CLI invocation deadline")
    run.add_argument("--oracle-timeout", type=int, default=60)
    report = sub.add_parser("report")
    report.add_argument("--cohort")
    prop = sub.add_parser("propose")
    prop.add_argument("--cohort", required=True)
    gate = sub.add_parser("compare")
    gate.add_argument("--baseline", required=True)
    gate.add_argument("--candidate", required=True)
    args = parser.parse_args(argv)
    store = Store(args.arena)
    try:
        if args.action == "init":
            store.initialize()
            result = {"initialized": str(store.root)}
        elif args.action == "cases":
            result = {"cases": [{key: case[key] for key in
                       ("id", "issue_ref", "split", "base_commit")}
                      for case in (store.case(row["id"]) for row in store.cases())]}
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
        parser.exit(2, f"arena error: {error}\n")


def cli():
    return main()


if __name__ == "__main__":
    raise SystemExit(main())
