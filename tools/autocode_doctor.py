"""`autocode doctor`: check this machine and a workspace before the first task.

Each check reports ok, missing (a required piece is absent), or warn (worth
knowing, not blocking), and the fix for anything not ok. It runs the same
commands AutoCode itself relies on: ``opencode --version`` (1.x only, as
providers/opencode.py enforces), ``codex login status`` (autocode_support), and
``git``. It never reads credentials: OpenCode's login is left to
``opencode auth list``. The dashboard's setup screen (issue #36) is meant to
show the same checks, so ``--json`` prints them as data.

Which engine a new user should install is not decided yet (issue #67), so
doctor passes when any engine is ready, or requires the one named by --engine.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

OK, MISSING, WARN = "ok", "missing", "warn"
ENGINES = ("opencode", "codex", "gocode")


@dataclass
class Check:
    name: str
    status: str
    detail: str
    fix: str = ""


def run(cmd: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=20)
    except FileNotFoundError as error:
        return subprocess.CompletedProcess(cmd, 127, "", str(error))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(cmd, -1, "", f"{cmd[0]} did not answer within 20 s")


def python_check(version=sys.version_info) -> Check:
    found = f"{version[0]}.{version[1]}.{version[2]}"
    if tuple(version[:2]) >= (3, 11):
        return Check("python", OK, f"Python {found}")
    return Check("python", MISSING, f"Python {found}; AutoCode needs 3.11 or newer",
                 "install Python 3.11+ and reinstall AutoCode with it (pipx install --python python3.11 ...)")


def psutil_check() -> Check:
    try:
        import psutil  # noqa: F401
    except ImportError:
        return Check("psutil", MISSING, "the psutil package is not importable from this Python",
                     "reinstall AutoCode with pipx, or run it with the project's .venv/bin/python")
    return Check("psutil", OK, "psutil importable")


def git_check(which=shutil.which, runner=run) -> Check:
    if not which("git"):
        return Check("git", MISSING, "git is not on PATH", "install Git")
    return Check("git", OK, runner(["git", "--version"]).stdout.strip() or "git found")


def engine_checks(which=shutil.which, runner=run) -> list[Check]:
    checks = []
    if which("opencode"):
        version = runner(["opencode", "--version"]).stdout.strip()
        if re.fullmatch(r"1\.\d+\.\d+(?:[-+].*)?", version):
            checks.append(Check("engine:opencode", OK, f"OpenCode {version}; see `opencode auth list` for which "
                                "accounts are connected (doctor does not read credentials)"))
        else:
            checks.append(Check("engine:opencode", MISSING, f"OpenCode {version or '(no version)'}; AutoCode "
                                "requires OpenCode 1.x", "install OpenCode 1.x; 2.x is refused"))
    else:
        checks.append(Check("engine:opencode", MISSING, "opencode is not on PATH", "see docs/install.md"))
    if which("codex"):
        login = runner(["codex", "login", "status"])
        checks.append(Check("engine:codex", OK, "Codex, logged in") if login.returncode == 0 else
                      Check("engine:codex", MISSING, "Codex found but not logged in", "run `codex login`"))
    else:
        checks.append(Check("engine:codex", MISSING, "codex is not on PATH", "see docs/providers.md"))
    if which("gocode"):
        version = runner(["gocode", "--version"])
        checks.append(Check("engine:gocode", OK if version.returncode == 0 else MISSING,
                            f"GoCode {version.stdout.strip()}" if version.returncode == 0
                            else "gocode --version failed", "" if version.returncode == 0 else "reinstall GoCode"))
    else:
        checks.append(Check("engine:gocode", MISSING, "gocode is not on PATH", "see docs/providers.md"))
    return checks


def engine_verdict(checks: list[Check], wanted: str | None) -> Check:
    ready = [c.name.split(":", 1)[1] for c in checks if c.name.startswith("engine:") and c.status == OK]
    if wanted:
        ok = wanted in ready
        return Check("engine", OK if ok else MISSING, f"--engine {wanted} is {'ready' if ok else 'not ready'}",
                     "" if ok else f"fix engine:{wanted} above")
    if ready:
        return Check("engine", OK, f"ready: {', '.join(ready)}")
    return Check("engine", MISSING, "no engine is ready", "install and log in to one of: " + ", ".join(ENGINES))


def workspace_check(workspace: Path, runner=run) -> list[Check]:
    if not workspace.is_dir():
        return [Check("workspace", MISSING, f"{workspace} is not a directory", "pass --workspace PATH")]
    top = runner(["git", "rev-parse", "--show-toplevel"], cwd=workspace)
    if top.returncode != 0:
        return [Check("workspace", MISSING, f"{workspace} is not a Git repository",
                      f"git -C {workspace} init && git -C {workspace} commit --allow-empty -m 'Start'")]
    if runner(["git", "rev-parse", "--verify", "HEAD"], cwd=workspace).returncode != 0:
        return [Check("workspace", MISSING, f"{workspace} has no commit yet",
                      f"git -C {workspace} commit --allow-empty -m 'Start'")]
    checks = [Check("workspace", OK, f"Git repository at {top.stdout.strip()}")]
    dirty = runner(["git", "status", "--porcelain"], cwd=workspace).stdout.strip()
    if dirty:
        count = len(dirty.splitlines())
        checks.append(Check("workspace:clean", WARN, f"{count} uncommitted change(s); a task's review cannot "
                            "tell them apart from AutoCode's own", "commit or stash them before starting a task"))
    return checks


def all_checks(workspace: Path, engine: str | None = None, which=shutil.which, runner=run) -> list[Check]:
    engines = engine_checks(which, runner)
    return [python_check(), psutil_check(), git_check(which, runner), *engines, engine_verdict(engines, engine),
            *workspace_check(workspace, runner)]


def passed(checks: list[Check]) -> bool:
    """Missing engines are fine as long as the engine verdict is ok; everything else must not be missing."""
    return all(c.status != MISSING for c in checks if not c.name.startswith("engine:"))


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autocode doctor", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="project to check (default: cwd)")
    parser.add_argument("--engine", choices=ENGINES, help="require this engine rather than any one")
    parser.add_argument("--json", action="store_true", help="print the checks as JSON")
    args = parser.parse_args(argv)
    checks = all_checks(args.workspace.resolve(), args.engine)
    ok = passed(checks)
    if args.json:
        print(json.dumps({"ok": ok, "checks": [asdict(c) for c in checks]}, indent=2))
    else:
        marks = {OK: "✔", MISSING: "✖", WARN: "!"}
        for check in checks:
            print(f"{marks[check.status]} {check.name:<16} {check.detail}")
            if check.fix:
                print(f"  {'':<16} fix: {check.fix}")
        print("ready for a first task" if ok else "not ready: fix the ✖ items above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(cli())
