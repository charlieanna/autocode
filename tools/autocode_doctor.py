"""`autocode doctor`: check this machine and a workspace before the first task.

Each check reports ok, missing (a required piece is absent), or warn (worth
knowing, not blocking), and the fix for anything not ok. It runs the same
commands AutoCode itself relies on: ``opencode --version`` (1.x or 2.x, as
providers/opencode.py enforces), ``codex login status`` (autocode_support),
``git``, for a configured default provider the check a run makes before
launching it, and the model list a new run checks its default routes against
(``opencode models``, as `autocode models` does; it can take up to a minute).
It never reads credentials. OpenCode 1.x and 2.x both count as a ready engine;
strict tool containment stays qualified only for OpenCode 1.18.33. The dashboard's
setup screen (issue #36) is meant to show the same checks, so ``--json`` prints
them as data.

Doctor passes only when the engine a new run uses is ready, resolved as the
runner resolves it (autocode_configure): ``--engine codex`` runs Codex;
otherwise (no --engine, or --engine opencode) the OpenCode engine runs the
provider AUTOCODE_PROVIDER or default_provider in ~/.config/autocode/config.toml
names, else the built-in OpenCode adapter. That provider must answer and offer every
role's default model. A ready Codex does not pass for a default that is not
ready; doctor names ``--engine codex``, the single-login route, as the
alternative (issue #67).
"""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from . import autocode_providers, model_catalogue
    from .providers import opencode as opencode_provider
except ImportError:
    import autocode_providers, model_catalogue
    from providers import opencode as opencode_provider

OK, MISSING, WARN = "ok", "missing", "warn"
ENGINES = ("opencode", "codex")


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
        parsed = opencode_provider.parse_opencode_version(version)
        if parsed:
            detail = f"OpenCode {parsed}"
            if parsed.split(".", 1)[0] == "2":
                detail += "; strict tool containment is qualified only for OpenCode 1.18.33"
            checks.append(Check("engine:opencode", OK, detail))
        else:
            checks.append(Check("engine:opencode", MISSING, f"OpenCode {version or '(no version)'}; AutoCode "
                                "requires OpenCode 1.x or 2.x",
                                "install OpenCode 1.x or 2.x, then log in (docs/install.md)"))
    else:
        checks.append(Check("engine:opencode", MISSING, "opencode is not on PATH",
                            "npm install -g opencode-ai@1, then log in (docs/install.md)"))
    if which("codex"):
        login = runner(["codex", "login", "status"])
        checks.append(Check("engine:codex", OK, "Codex, logged in") if login.returncode == 0 else
                      Check("engine:codex", MISSING, "Codex found but not logged in", "run `codex login`"))
    else:
        checks.append(Check("engine:codex", MISSING, "codex is not on PATH",
                            "only for --engine codex: npm install -g @openai/codex, then `codex login`"))
    return checks


def default_provider() -> str:
    """The provider the OpenCode engine runs when no --provider is given (``autocode`` or
    ``autocode --engine opencode``): AUTOCODE_PROVIDER, then default_provider in
    ~/.config/autocode/config.toml, then the built-in ``opencode``. Any other name, ``codex``
    included, is a provider config, never the Codex engine. Raises ValueError or OSError
    when that setting cannot be read, as a run would.
    """
    return autocode_providers.default_name()


def provider_check(name: str, resolve=autocode_providers.resolve) -> Check:
    """A default provider other than the built-in OpenCode (docs/providers.md#add-a-tool) is
    ready when its config loads and its command answers: the check a run makes before launching it."""
    try:
        settings = resolve(name).local_settings()
    except (OSError, RuntimeError, ValueError) as error:
        return Check(f"provider:{name}", MISSING, str(error), "see docs/providers.md#add-a-tool")
    return Check(f"provider:{name}", OK, f"provider {name} answers (configured in {settings.get('config_path')})")


# What connects a default OpenCode route's model, by its provider prefix (docs/install.md).
LOGINS = {"openai/": "sign in to ChatGPT (`opencode auth login`, choose OpenAI)",
          "zai-coding-plan/": "connect the Z.AI Coding Plan (`opencode auth login`, choose Z.AI Coding Plan, "
                              "not the pay-per-token Z.AI)"}


def route_check(name: str, workspace: Path | None, resolve=autocode_providers.resolve) -> Check:
    """Every role's default model must be in the provider's model list: the check a new run makes
    before its first model call (model_catalogue.choose). A list that cannot be read only warns,
    since a run then starts anyway."""
    try:
        provider = resolve(name)
        available = provider.available_models(workspace)
    except (OSError, RuntimeError, ValueError) as error:
        return Check("routes", WARN, f"cannot list {name}'s models: {error}",
                     "run `autocode models`; a new run checks again before its first model call")
    if available is None:
        return Check("routes", OK, f"provider {name} does not list its models; its routes are not checked")
    missing = model_catalogue.advise(model_catalogue.default_roles(provider), available)["missing"]
    if not missing:
        return Check("routes", OK, f"{name} offers every role's default model")
    by_model: dict[str, list[str]] = {}
    for role, model in missing.items():
        by_model.setdefault(model, []).append(model_catalogue.ROLES[role][0])
    fixes = [login for prefix, login in LOGINS.items()
             if name == "opencode" and any(model.startswith(prefix) for model in by_model)]
    return Check("routes", MISSING, f"{name} does not offer: " + "; ".join(
        f"{model} ({', '.join(labels)})" for model, labels in by_model.items()),
        "; ".join([*fixes, "`autocode models` lists what your plans offer and suggests replacements"]))


def engine_verdict(checks: list[Check], wanted: str | None = None, provider: str = "opencode") -> Check:
    """Codex must be ready for ``--engine codex``. Otherwise the OpenCode engine runs ``provider``
    (default_provider()), which must answer and must not be missing a default route."""
    status = {c.name: c.status for c in checks}
    if wanted == "codex":
        ok = status.get("engine:codex") == OK
        return Check("engine", OK if ok else MISSING, f"--engine codex is {'ready' if ok else 'not ready'}",
                     "" if ok else "fix engine:codex above")
    tool = "engine:opencode" if provider == "opencode" else f"provider:{provider}"
    what = ("OpenCode" if provider == "opencode" else f"provider {provider}") + (
        " (what --engine opencode runs)" if wanted else " (the default for a new run)")
    if status.get(tool) == OK and status.get("routes") != MISSING:
        return Check("engine", OK, f"{what} is ready")
    fix = f"fix {tool if status.get(tool) != OK else 'routes'} above"
    if provider == "codex":
        fix += ("; AUTOCODE_PROVIDER or default_provider names a provider config called codex, not the Codex "
                "engine: remove that setting and pass --engine codex")
    elif provider != "opencode":
        fix += "; AUTOCODE_PROVIDER or default_provider in ~/.config/autocode/config.toml selects it"
    codex_ready = status.get("engine:codex") == OK
    if codex_ready and provider != "codex":
        fix += "; or pass --engine codex (the single-login route) to doctor and to every task"
    return Check("engine", MISSING, f"{what} is not ready" + ("; Codex is ready" if codex_ready else ""), fix)


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


def opencode_checks(checks: list[Check], workspace: Path, wanted: str | None, resolve) -> Check:
    """The OpenCode engine's provider, as the runner picks it, and its default routes: appended to
    ``checks``; returns the verdict."""
    try:
        provider = default_provider()
    except (OSError, ValueError) as error:  # no run can start either
        return Check("engine", MISSING, f"cannot tell which provider a new run uses: {error}",
                     "make that setting valid and its file readable, or pass --engine codex")
    if provider != "opencode":
        checks.append(provider_check(provider, resolve))
    tool = "engine:opencode" if provider == "opencode" else f"provider:{provider}"
    if any(c.name == tool and c.status == OK for c in checks):
        checks.append(route_check(provider, workspace if workspace.is_dir() else None, resolve))
    return engine_verdict(checks, wanted, provider)


def all_checks(workspace: Path, engine: str | None = None, which=shutil.which, runner=run,
               resolve=autocode_providers.resolve) -> list[Check]:
    checks = engine_checks(which, runner)
    # --engine codex uses Codex's own transport; no flag and --engine opencode run the default provider.
    verdict = (engine_verdict(checks, engine) if engine == "codex"
               else opencode_checks(checks, workspace, engine, resolve))
    return [python_check(), psutil_check(), git_check(which, runner), *checks, verdict,
            *workspace_check(workspace, runner)]


def passed(checks: list[Check]) -> bool:
    """Missing engines are fine as long as the engine verdict is ok; everything else, the provider
    and routes the run would use included, must not be missing."""
    return all(c.status != MISSING for c in checks if not c.name.startswith("engine:"))


def cli(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="autocode doctor", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--workspace", type=Path, default=Path.cwd(), help="project to check (default: cwd)")
    parser.add_argument("--engine", choices=ENGINES, help="check what a run with this --engine uses: codex is "
                        "Codex; opencode, like no flag, is the default provider")
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
