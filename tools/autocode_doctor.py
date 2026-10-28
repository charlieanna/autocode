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
runner resolves it (autocode_configure): ``--engine codex`` runs Codex; without
--engine, the ``engine`` the project's files set (#707) counts as that flag;
otherwise (no engine, or --engine opencode) the OpenCode engine runs the
provider AUTOCODE_PROVIDER or default_provider in ~/.config/autocode/config.toml
names, else the built-in OpenCode adapter. That provider must answer and offer every
role's default model. A ready Codex does not pass for a default that is not
ready; doctor names ``--engine codex``, the single-login route, as the
alternative (issue #67). ``--engine claude``, or a ``claude`` default provider, runs
Claude Code (the built-in claude provider, #717): ``claude`` must be on PATH and logged in
to a Claude subscription in the environment AutoCode gives its agents
(``claude auth status --json``; an API key, Bedrock or Vertex is refused, by the same
check a run makes, which also reads the project's ``.claude/settings.json``). Doctor checks
Claude Code only then, and never prints the account's email or organization.

When a new run launches built-in OpenCode stages (no engine, or --engine opencode, with the
built-in opencode provider), the ``containment`` check (#709) names the kernel boundary its
stages other than planning get on this machine, by the check run setup makes
(autocode_tool_containment.unavailable, ``opencode --version`` run in the workspace):
macOS Seatbelt or Linux bubblewrap and its version, then OpenCode exactly 1.18.33. Without
both, such a run is refused at setup unless it is started with --allow-uncontained-tools, so
the check warns; it never fails doctor. ``opencode --version`` runs once per doctor.

The ``session`` check fails when this host refuses every supervised stage, by the check each Linux
stage launch makes (autocode_session_boundary): above all a root shell in a container, VM or CI image
that holds CAP_SYS_ADMIN or CAP_SYS_PTRACE, which it tells to run as an ordinary user or drop the two.

The ``path`` check (#1761) warns, without failing doctor, when ``autocode`` is not on PATH, so
a new shell would answer ``command not found``, and names the virtual environment's ``bin``
directory to add or the ``pipx`` install that puts it there.
"""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
import os
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tomllib
from dataclasses import asdict, dataclass
from pathlib import Path

try:
    from . import autocode_install_drift
    from . import autocode_session_boundary as session_boundary
    from . import autocode_storage_budget as storage_budget
    from . import autocode_tool_containment as tool_containment
    from . import autocode_util as util
except ImportError:  # direct-script mode has no package parent
    import autocode_install_drift
    import autocode_session_boundary as session_boundary
    import autocode_storage_budget as storage_budget
    import autocode_tool_containment as tool_containment
    import autocode_util as util

try:
    from . import autocode_providers, model_catalogue
    from .providers import opencode as opencode_provider
except ImportError:
    import autocode_providers
    import model_catalogue
    from providers import opencode as opencode_provider

OK, MISSING, WARN = "ok", "missing", "warn"

# How a person stops typing --engine on every task: the project's settings file, or, for Claude Code, the
# user's default provider (docs/cli.md, Project settings; docs/providers.md).
PROJECT_ENGINE = ', or once with engine = "codex" in the project\'s .autocode/config.toml'
CLAUDE_DEFAULT = (
    ', or once with engine = "claude" in the project\'s .autocode/config.toml '
    '(or default_provider = "claude" in ~/.config/autocode/config.toml for every project)'
)
ENGINES = ("opencode", "codex", "claude")


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
    return Check(
        "python",
        MISSING,
        f"Python {found}; AutoCode needs 3.11 or newer",
        "install Python 3.11+ and reinstall AutoCode with it (pipx install --python python3.11 ...)",
    )


def psutil_check(runner=run) -> Check:
    try:
        import psutil  # noqa: F401
    except ImportError:
        return Check(
            "psutil",
            MISSING,
            "the psutil package is not importable from this Python",
            "reinstall AutoCode with pipx, or run it with the project's .venv/bin/python",
        )
    # The keeper ignores PYTHON* variables with -E. The -I bootstrap uses only
    # the standard library, so it does not need a separate psutil probe.
    try:
        probe = runner([sys.executable, "-E", "-c", "import psutil"], cwd=Path(__file__).resolve().parent)
    except (OSError, subprocess.TimeoutExpired):
        probe = None
    if probe is None or probe.returncode != 0:
        return Check(
            "psutil",
            MISSING,
            "psutil is not available to the process keeper's -E interpreter",
            "install psutil into a virtualenv's site-packages and use that interpreter, rather than relying on PYTHONPATH; "
            "reinstall AutoCode with pipx or use the project's .venv/bin/python",
        )
    return Check("psutil", OK, "psutil importable by the controller and the -E process keeper")


def git_check(which=shutil.which, runner=run) -> Check:
    if not which("git"):
        return Check("git", MISSING, "git is not on PATH", "install Git")
    return Check("git", OK, runner(["git", "--version"]).stdout.strip() or "git found")


def conformance_check(engine: str, version: str) -> Check:
    """The published conformance row for this installed version (#706), or untested."""
    try:
        from . import provider_matrix
    except ImportError:
        import provider_matrix
    try:
        row = provider_matrix.status_for(engine, version)
    except (OSError, ValueError) as error:
        return Check(
            f"conformance:{engine}",
            WARN,
            f"cannot read the conformance matrix: {error}",
            "see docs/providers.md#conformance-matrix",
        )
    if row is None:
        return Check(
            f"conformance:{engine}",
            WARN,
            f"{engine} {version} is untested against the offline conformance suite",
            "the tested versions are listed in docs/providers.md#conformance-matrix; a maintainer adds this one with "
            "tools/provider_conformance.py --fake",
        )
    return Check(
        f"conformance:{engine}",
        OK if row["result"] == "known-good" else WARN,
        f"{engine} {version}: {row['result']} (containment {row['containment']}, "
        f"visual {row['visual_profile']}, {row['date']})",
        "" if row["result"] == "known-good" else row["note"] or "",
    )


def engine_checks(which=shutil.which, runner=run, opencode_version=None) -> list[Check]:
    """``opencode_version`` is ``opencode --version``'s result when the caller already ran it."""
    checks = []
    if which("opencode"):
        version = (opencode_version or runner(["opencode", "--version"])).stdout.strip()
        parsed = opencode_provider.parse_opencode_version(version)
        if parsed:
            # Whether this version can run contained is the containment check's line (#709).
            checks.append(Check("engine:opencode", OK, f"OpenCode {parsed}"))
        else:
            checks.append(
                Check(
                    "engine:opencode",
                    MISSING,
                    f"OpenCode {version or '(no version)'}; AutoCode requires OpenCode 1.x or 2.x",
                    "install OpenCode 1.x or 2.x, then log in (docs/install.md)",
                )
            )
        checks.append(conformance_check("opencode", parsed or version or "unknown"))
    else:
        checks.append(
            Check(
                "engine:opencode",
                MISSING,
                "opencode is not on PATH",
                "npm install -g opencode-ai@1, then log in (docs/install.md)",
            )
        )
    if which("codex"):
        login = runner(["codex", "login", "status"])
        checks.append(
            Check("engine:codex", OK, "Codex, logged in")
            if login.returncode == 0
            else Check("engine:codex", MISSING, "Codex found but not logged in", "run `codex login`")
        )
        checks.append(codex_billing_check())
    else:
        checks.append(
            Check(
                "engine:codex",
                MISSING,
                "codex is not on PATH",
                "only for --engine codex: npm install -g @openai/codex, then `codex login`",
            )
        )
    return checks


def codex_billing_check(*, environ=None, config_path=None) -> Check:
    """The billing-route gate a Codex joint-planning run applies before any launch (#2055): ambient
    API-key or endpoint overrides, or another provider named in Codex's own config, pause with
    PAUSED_BILLING_ROUTE. Doctor names the exact command instead of letting the run discover it."""
    environ = os.environ if environ is None else environ
    if config_path is None:
        config_path = Path(environ.get("CODEX_HOME", str(Path.home() / ".codex"))) / "config.toml"
    ambient = [name for name in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL") if name in environ]
    overrides = []
    if config_path.is_file():
        try:
            config = tomllib.loads(config_path.read_text())
        except (OSError, tomllib.TOMLDecodeError):
            config = {}
        if config.get("model_provider") not in (None, "openai"):
            overrides.append(f"model_provider = {config['model_provider']!r}")
        if config.get("openai_base_url"):
            overrides.append(f"openai_base_url = {config['openai_base_url']!r}")
    if not ambient and not overrides:
        return Check("billing:codex", OK, "no API-key or endpoint override would reach a Codex run")
    gaps, fixes = [], []
    if ambient:
        gaps.append("this shell exports " + ", ".join(ambient))
        fixes.append("unset " + " ".join(ambient) + " (also remove them from the shell profile that sets them)")
    if overrides:
        gaps.append(f"{config_path} sets " + " and ".join(overrides))
        fixes.append(f"remove those lines from {config_path}")
    return Check(
        "billing:codex",
        MISSING,
        "; ".join(gaps) + "; a Codex joint-planning run pauses (PAUSED_BILLING_ROUTE) rather than bill another route",
        "; ".join(fixes) + "; to run on API keys instead: `autocode connections setup`",
    )


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
    if not settings.get("config_path"):
        return Check(f"provider:{name}", OK, f"provider {name} is built in")
    return Check(f"provider:{name}", OK, f"provider {name} answers (configured in {settings.get('config_path')})")


# What connects a default OpenCode route's model, by its provider prefix (docs/install.md).
LOGINS = {
    "openai/": "sign in to ChatGPT (`opencode auth login`, choose OpenAI)",
    "zai-coding-plan/": "connect the Z.AI Coding Plan (`opencode auth login`, choose Z.AI Coding Plan, "
    "not the pay-per-token Z.AI)",
}


def route_check(name: str, workspace: Path | None, resolve=autocode_providers.resolve) -> Check:
    """Every route a new run takes must be in the provider's model list: the profile's own defaults,
    or the routes automatic selection replaces them with (model_catalogue.route_advice). A list that
    cannot be read only warns, since a run then starts anyway."""
    try:
        provider = resolve(name)
        available = provider.available_models(workspace)
    except (OSError, RuntimeError, ValueError) as error:
        return Check(
            "routes",
            WARN,
            f"cannot list {name}'s models: {error}",
            "run `autocode models`; a new run checks again before its first model call",
        )
    if available is None:
        return Check("routes", OK, f"provider {name} does not list its models; its routes are not checked")
    routes = model_catalogue.route_advice(provider, available, workspace)
    missing = routes["missing"]
    if not missing:
        if routes["source"] != "selection":
            return Check("routes", OK, f"{name} offers every role's default model")
        moved = "; ".join(
            f"{model_catalogue.ROLES[role][0]}: {seed} -> {taken}" for role, (seed, taken) in routes["replaced"].items()
        )
        return Check(
            "routes",
            OK,
            f"{name} offers every route a new run takes"
            + (
                f"; automatic selection replaces the profile's fallback for {moved}"
                if moved
                else "; the profile's own routes are only that fallback"
            ),
        )
    by_model: dict[str, list[str]] = {}
    for role, model in missing.items():
        by_model.setdefault(model, []).append(model_catalogue.ROLES[role][0])
    fixes = [
        login
        for prefix, login in LOGINS.items()
        if name == "opencode" and any(model.startswith(prefix) for model in by_model)
    ]
    gap = f"{name} does not offer: " + "; ".join(f"{model} ({', '.join(labels)})" for model, labels in by_model.items())
    if routes["reason"]:
        gap += f", and automatic selection cannot cover it ({routes['reason']})"
    return Check(
        "routes",
        MISSING,
        gap,
        "; ".join([*fixes, "`autocode models` lists what your plans offer and suggests replacements"]),
    )


SANDBOXES = {
    tool_containment.SEATBELT: f"macOS Seatbelt ({tool_containment.SANDBOX_EXEC})",
    tool_containment.BUBBLEWRAP: f"Linux bubblewrap ({tool_containment.BWRAP})",
}


def containment_status(opencode_version) -> dict:
    """#709: the kernel boundary a new built-in OpenCode run gets here, by run setup's two refusals
    (tool_containment.unavailable): this host's sandbox, then the OpenCode version.
    ``opencode_version`` is ``opencode --version``'s result, run in the workspace as run setup runs
    it, or None when opencode is not on PATH. ``backend`` and ``backend_version`` name the sandbox
    when it works; ``problem`` is why a new run would get no boundary, or None."""
    problem = tool_containment.sandbox_problem()
    name = None if problem else tool_containment.backend()
    if name:
        problem = tool_containment.opencode_problem(opencode_version)
    return {
        "backend": name,
        "backend_version": tool_containment.backend_version(name) if name else None,
        "problem": problem,
    }


def containment_check(status: dict) -> Check:
    """A warning, never missing: a run without the boundary can still be started with
    --allow-uncontained-tools. Shown only for runs that launch built-in OpenCode stages."""
    name, problem = status["backend"], status["problem"]
    sandbox = f"{SANDBOXES[name]}, {status['backend_version'] or 'version unknown'}" if name else None
    if not problem:
        return Check(
            "containment", OK, f"{sandbox}: built-in OpenCode stages other than planning run their shell inside it"
        )
    if problem == tool_containment.NO_OPENCODE:
        # engine:opencode already says how to install it; no run starts without it, flag or not.
        return Check(
            "containment",
            WARN,
            f"{sandbox} works; no opencode is on PATH to run inside it",
            f"see engine:opencode above; strict tool containment needs OpenCode {tool_containment.SUPPORTED_VERSION}",
        )
    return Check(
        "containment",
        WARN,
        (f"{sandbox} works, but " if sandbox else "no kernel boundary: ")
        + problem
        + "; a new built-in OpenCode run is refused at setup without --allow-uncontained-tools",
        f"for built-in OpenCode runs: macOS sandbox-exec or Linux bubblewrap ({tool_containment.BWRAP}) with "
        f"OpenCode {tool_containment.SUPPORTED_VERSION} (docs/execution.md#native-tool-containment); or start "
        "the run with --allow-uncontained-tools for OpenCode's own permission checks only",
    )


def engine_verdict(
    checks: list[Check],
    wanted: str | None = None,
    provider: str = "opencode",
    origin: str | None = None,
    claude_on_path: bool = False,
) -> Check:
    """Codex must be ready for ``--engine codex``. Otherwise the OpenCode engine runs ``provider``
    (default_provider()), which must answer and must not be missing a default route. When that is not
    ready, the verdict names the other engines this host already has: a ready Codex, and a ``claude``
    on PATH (``claude_on_path``; its login is checked by ``doctor --engine claude``), so a person with
    only Claude Code installed is not sent to install OpenCode first."""
    status = {c.name: c.status for c in checks}
    if wanted == "codex":
        ok = status.get("engine:codex") == OK
        what = f"engine codex (set in {origin})" if origin else "--engine codex"
        return Check(
            "engine",
            OK if ok else MISSING,
            f"{what} is {'ready' if ok else 'not ready'}",
            "" if ok else "fix engine:codex above",
        )
    tool = "engine:opencode" if provider == "opencode" else f"provider:{provider}"
    what = ("OpenCode" if provider == "opencode" else f"provider {provider}") + (
        " (what --engine opencode runs)" if wanted else " (the default for a new run)"
    )
    if status.get(tool) == OK and status.get("routes") != MISSING:
        return Check("engine", OK, f"{what} is ready")
    fix = f"fix {tool if status.get(tool) != OK else 'routes'} above"
    if provider == "codex":
        fix += (
            "; AUTOCODE_PROVIDER or default_provider names a provider config called codex, not the Codex "
            "engine: remove that setting and pass --engine codex"
        )
    elif provider != "opencode":
        fix += "; AUTOCODE_PROVIDER or default_provider in ~/.config/autocode/config.toml selects it"
    codex_ready = status.get("engine:codex") == OK
    if codex_ready and provider != "codex":
        fix += "; or pass --engine codex (the single-login route) to doctor and to every task" + PROJECT_ENGINE
    if claude_on_path and provider != "claude":
        fix += "; or pass --engine claude (Claude Code is on PATH) to doctor and to every task" + CLAUDE_DEFAULT
    found = [
        name for name, ready in (("Codex is ready", codex_ready), ("Claude Code is on PATH", claude_on_path)) if ready
    ]
    return Check("engine", MISSING, "; ".join([f"{what} is not ready", *found]), fix)


def workspace_check(workspace: Path, runner=run) -> list[Check]:
    if not workspace.is_dir():
        return [Check("workspace", MISSING, f"{workspace} is not a directory", "pass --workspace PATH")]
    top = runner(["git", "rev-parse", "--show-toplevel"], cwd=workspace)
    if top.returncode != 0:
        return [
            Check(
                "workspace",
                MISSING,
                f"{workspace} is not a Git repository",
                f"git -C {workspace} init && git -C {workspace} commit --allow-empty -m 'Start'",
            )
        ]
    if runner(["git", "rev-parse", "--verify", "HEAD"], cwd=workspace).returncode != 0:
        return [
            Check(
                "workspace",
                MISSING,
                f"{workspace} has no commit yet",
                f"git -C {workspace} commit --allow-empty -m 'Start'",
            )
        ]
    checks = [Check("workspace", OK, f"Git repository at {top.stdout.strip()}")]
    dirty = runner(["git", "status", "--porcelain"], cwd=workspace).stdout.strip()
    if dirty:
        count = len(dirty.splitlines())
        checks.append(
            Check(
                "workspace:clean",
                WARN,
                f"{count} uncommitted change(s); a task's review cannot tell them apart from AutoCode's own",
                "commit or stash them before starting a task",
            )
        )
    return checks


def opencode_checks(
    checks: list[Check], workspace: Path, wanted: str | None, resolve, which=shutil.which, runner=run
) -> Check:
    """The OpenCode engine's provider, as the runner picks it, and its default routes: appended to
    ``checks``; returns the verdict."""
    try:
        provider = default_provider()
    except (OSError, ValueError) as error:  # no run can start either
        return Check(
            "engine",
            MISSING,
            f"cannot tell which provider a new run uses: {error}",
            "make that setting valid and its file readable, or pass --engine codex",
        )
    if provider == "claude":
        return claude_checks(checks, workspace, wanted, which, runner, resolve)
    if provider != "opencode":
        checks.append(provider_check(provider, resolve))
    tool = "engine:opencode" if provider == "opencode" else f"provider:{provider}"
    if any(c.name == tool and c.status == OK for c in checks):
        checks.append(route_check(provider, workspace if workspace.is_dir() else None, resolve))
    return engine_verdict(checks, wanted, provider, claude_on_path=bool(which("claude")))


def claude_checks(
    checks: list[Check], workspace: Path, wanted: str | None, which, runner, resolve, origin: str | None = None
) -> Check:
    """Claude Code (the built-in claude provider, #717): on PATH, logged in to a Claude subscription in
    the environment AutoCode gives its agents, and its conformance row. Appended to ``checks``; returns
    the verdict. An injected ``runner`` (tests) is the only thing that runs: no host ``claude`` is executed.
    ``origin`` names where the engine came from when no flag gave it (the project's file)."""
    ws = workspace if workspace.is_dir() else None
    shadow = autocode_providers.ignored_user_config("claude")
    if shadow:
        checks.append(
            Check(
                "provider-config:claude",
                WARN,
                f"{shadow} is ignored: claude is built in",
                "remove it; to keep that tool, rename the file and its name = (for example claude_example); "
                "runs saved with it resume only after --accept-transport-change",
            )
        )
    if not which("claude"):
        checks.append(
            Check(
                "engine:claude",
                MISSING,
                "claude is not on PATH",
                "install Claude Code (docs/install.md), then `claude auth login`",
            )
        )
    else:
        try:
            provider = resolve("claude")
            # The billing gate a run applies first: `auth status` does not report every route it refuses
            # (ANTHROPIC_AUTH_TOKEN passed through, a project's .claude/settings.json).
            provider.check_forbidden_environment(workspace=ws)
            account = provider.login_check(ws, run=None if runner is run else runner)
        except (OSError, RuntimeError, ValueError) as error:
            if "billing routes" in str(error):
                fix = (
                    "keep API keys, Bedrock and Vertex away from Claude Code (AUTOCODE_PASS_ENV, the project's "
                    ".claude/settings.json)"
                )
            elif ".claude/settings.json" in str(error):
                fix = "fix or remove the project's .claude/settings.json"
            else:
                fix = "run `claude auth login` with a Claude subscription"
            checks.append(Check("engine:claude", MISSING, str(error), fix + "; docs/providers.md#claude-code-provider"))
        else:
            checks.append(
                Check(
                    "engine:claude",
                    OK,
                    f"Claude Code {account['version']}, logged in ({account['authMethod']}, {account['apiProvider']})",
                )
            )
            checks.append(conformance_check("claude", account["version"]))
            checks.append(route_check("claude", ws, resolve))
    status = {c.name: c.status for c in checks}
    ok = status.get("engine:claude") == OK and status.get("routes") != MISSING
    if origin:
        what = f"engine claude (set in {origin})"
    else:
        what = "--engine claude" if wanted == "claude" else "provider claude (the default for a new run)"
    fix = "" if ok else "fix engine:claude above"
    if not ok and status.get("engine:codex") == OK:
        fix += "; or pass --engine codex (the single-login route) to doctor and to every task" + PROJECT_ENGINE
    return Check("engine", OK if ok else MISSING, f"{what} is {'ready' if ok else 'not ready'}", fix)


# What a root shell in a container or VM should do instead: the bounding-set drop docs/testing.md documents.
UNPRIVILEGED = "setpriv --bounding-set=-sys_admin,-sys_ptrace"


def session_check(require=session_boundary.require_supported) -> Check:
    """Whether this host admits supervised stages at all, by the check every Linux stage launch makes.

    A Linux shell holding CAP_SYS_ADMIN or CAP_SYS_PTRACE (root in most containers, VMs and CI images) has
    every stage refused before any provider starts, the demo included, with PAUSED_PROCESS_CLEANUP. Say so
    here rather than let the first run discover it. macOS runs under portable supervision and passes."""
    try:
        require()
    except session_boundary.PortableHost:
        return Check("session", OK, "macOS: stages run under portable supervision")
    except session_boundary.UnsupportedBoundary as error:
        fix = "run AutoCode on a supported Linux x86_64 or aarch64 host (docs/install.md)"
        if "CAP_SYS" in str(error):
            fix = (
                "run AutoCode as an ordinary user rather than root, or drop the two capabilities for it: "
                f"{UNPRIVILEGED} autocode ..."
            )
        return Check(
            "session",
            MISSING,
            f"{error}: every stage, the offline demo included, is refused before it starts (PAUSED_PROCESS_CLEANUP)",
            fix,
        )
    return Check("session", OK, "stages can run under supervised session ownership")


def shared_host_check(passwd_lines=None, uid=None) -> Check:
    """#818: on a shared machine, other local accounts can read /proc/<pid>/cmdline
    (world-readable) while /proc/<pid>/environ is owner-only — so authorization
    tokens passed as argv values are exposed to co-tenants. Advisory only: the
    env channel (`--resolver-token -` with AUTOCODE_RESOLVER_TOKEN set) is not
    affected. Non-POSIX hosts (no /etc/passwd) skip the advisory."""
    if uid is None and hasattr(os, "getuid"):
        uid = os.getuid()
    if passwd_lines is None:
        try:
            with open("/etc/passwd", encoding="utf-8") as handle:
                passwd_lines = handle.readlines()
        except OSError:
            return Check("shared-host", OK, "no local account database readable; shared-host advisory skipped")
    others = set()
    for line in passwd_lines:
        fields = line.strip().split(":")
        if len(fields) < 7:
            continue
        try:
            account_uid = int(fields[2])
        except ValueError:
            continue
        if account_uid < 1000 or account_uid == uid:
            continue
        if fields[6] in ("/usr/sbin/nologin", "/sbin/nologin", "/bin/false", "/usr/bin/false"):
            continue
        others.add(fields[0])
    if not others:
        return Check("shared-host", OK, "no other interactive local accounts; argv tokens are not co-tenant readable")
    return Check(
        "shared-host",
        WARN,
        f"shared host: {len(others)} other interactive local account(s) can read /proc/<pid>/cmdline; "
        "tokens passed as argv values are visible to them",
        "pass tokens by environment instead: set the AUTOCODE_* variable and use the `-` value "
        "(e.g. --resolver-token -); see docs/cli.md, Keeping authorization tokens out of argv",
    )


def path_check(which=shutil.which, scripts: str | None = None, environ=None) -> Check:
    """#1761: whether typing ``autocode`` in a new shell finds this install. A virtual environment's
    ``bin`` is on PATH only while it is activated, so ``.venv/bin/autocode doctor`` and a later shell
    can disagree; name the directory to put on PATH, or the pipx install that does it once."""
    environ = os.environ if environ is None else environ
    scripts = scripts if scripts is not None else sysconfig.get_path("scripts")
    found = which("autocode")
    if found is None:
        installed = Path(scripts) / "autocode"
        pipx = "pipx install autocode-supervisor puts it on PATH once"
        fix = pipx
        if installed.exists():
            activate = shlex.join(["source", str(Path(scripts) / "activate")])
            fix = f'{activate} in each new shell, or add export PATH="{scripts}:$PATH" to your shell profile; or {pipx}'
        return Check("path", WARN, "autocode is not on PATH: a new shell answers `command not found`", fix)
    venv = environ.get("VIRTUAL_ENV")
    if venv and Path(found).resolve().parent == (Path(venv) / "bin").resolve():
        return Check(
            "path",
            OK,
            f"{found}, through the activated virtual environment {venv}; a new shell finds it only after "
            f"{shlex.join(['source', str(Path(venv) / 'bin' / 'activate')])} (pipx install autocode-supervisor puts "
            "it on PATH for good)",
        )
    return Check("path", OK, found)


def installed_release():
    """The installed autocode-supervisor version, or None when this code is not an installed distribution."""
    try:
        return importlib.metadata.version("autocode-supervisor")
    except importlib.metadata.PackageNotFoundError:
        return None


def runner_check(commit_source=None, release=installed_release) -> Check:
    """#980: report the autocode code that would execute runs from this machine.

    A released install has no source commit by design; it is not told to run from a checkout instead."""
    identity = util.runner_identity(commit_source)
    if identity["commit"] is None and (version := release()):
        return Check(
            "runner",
            OK,
            f"installed release autocode-supervisor {version} at {identity['path']} (no source commit; run "
            "states record this path)",
        )
    if identity["commit"] is None:
        return Check(
            "runner",
            WARN,
            f"unversioned runner at {identity['path']} — run states will not identify their producer",
            "run autocode from a git checkout so state.json records the runner commit",
        )
    return Check("runner", OK, f"{identity['path']} @ {identity['commit']}")


def _workspace_runner_check(workspace: Path, executing: Path) -> Check | None:
    comparison = autocode_install_drift.compare(workspace, executing)
    if comparison is None:
        return None
    commit = util.runner_identity()["commit"] or "unknown (installed package)"
    detail = (
        f"executing AutoCode at {executing} (commit {commit}); checkout {comparison.checkout} @ {comparison.commit}"
    )
    source = shlex.join([sys.executable, str(comparison.checkout / "tools/autocode.py")])
    reinstall = shlex.join(
        [sys.executable, "-m", "pip", "install", "--upgrade", "--force-reinstall", str(comparison.checkout)]
    )
    fix = f"run the checkout directly: {source}; or reinstall deliberately: {reinstall}"
    if comparison.error:
        return Check("install-drift", WARN, detail + "; payload comparison unavailable: " + comparison.error, fix)
    pieces = []
    for label, names in (("differ", comparison.changed), ("missing", comparison.missing), ("extra", comparison.extra)):
        if names:
            pieces.append(f"{len(names)} {label}: {', '.join(names[:5])}")
    if pieces:
        return Check("install-drift", WARN, detail + "; payload drift: " + "; ".join(pieces), fix)
    return Check("install-drift", OK, detail + "; application payload matches")


def install_drift_check(checkout=None, installed=None, workspace: Path | None = None) -> Check:
    """#972: a separately-installed autocode_cli can silently diverge from the
    tree a maintainer edits — reinstalling reverts installed-copy patches, and a
    stale second copy beside a live one is the mid-rewrite hazard.

    Only compares what is honestly discoverable: the executing tree against a
    *separate* physical copy reachable by the same interpreter. The editable
    mapping (the healthy linked configuration) and the single-copy cases are OK,
    never warnings. Drift while executing from the installed copy itself stays
    detectable only when workspace names a verified AutoCode source checkout;
    its commit is a comparison target, never the installed runner commit."""
    import hashlib

    reference = Path(checkout) if checkout else Path(util.__file__).resolve().parent
    workspace_check = _workspace_runner_check(workspace, reference) if workspace is not None else None
    if installed is None:
        spec = importlib.util.find_spec("autocode_cli")
        locations = getattr(spec, "submodule_search_locations", None) if spec else None
        installed = Path(next(iter(locations))).resolve() if locations else None
    if installed is None:
        return workspace_check or Check(
            "install-drift", OK, "no separately-installed autocode_cli for this interpreter"
        )
    installed = Path(installed).resolve()
    if installed == reference.resolve():
        return workspace_check or Check(
            "install-drift", OK, f"{installed} is the copy executing now; no second installed copy to drift from"
        )

    def digest(path):
        try:
            return hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            return None

    def inventory(root):
        return {
            str(path.relative_to(root)): digest(path)
            for path in root.rglob("*")
            if path.is_file() and "__pycache__" not in path.parts and path.suffix != ".pyc"
        }

    reference_files = inventory(reference)
    installed_files = inventory(installed)
    drifted = sorted(
        name for name in set(reference_files) & set(installed_files) if reference_files[name] != installed_files[name]
    )
    only_reference = sorted(set(reference_files) - set(installed_files))
    only_installed = sorted(set(installed_files) - set(reference_files))
    if not drifted and not only_reference and not only_installed:
        return workspace_check or Check("install-drift", OK, f"{installed} matches {reference}")
    pieces = []
    if drifted:
        pieces.append(f"{len(drifted)} file(s) differ: {', '.join(drifted[:5])}")
    if only_reference:
        pieces.append(f"{len(only_reference)} missing from the install: {', '.join(only_reference[:5])}")
    if only_installed:
        pieces.append(f"{len(only_installed)} extra in the install: {', '.join(only_installed[:3])}")
    return Check(
        "install-drift",
        WARN,
        f"installed copy diverges from {reference}: " + "; ".join(pieces),
        "a reinstall from the checkout reverts these differences — keep patches in the checkout, "
        "and reinstall deliberately rather than at run startup (#972)",
    )


def storage_check(
    workspace: Path, *, multiplier=storage_budget.SNAPSHOT_MULTIPLIER, disk_usage=shutil.disk_usage
) -> Check:
    """#971: project the run's copy growth (worktrees, verification copies, attempt
    snapshots, measured ~Nx the workspace) against free space before the first stage spends."""
    try:
        budget = storage_budget.projection(workspace, multiplier=multiplier, disk_usage=disk_usage)
    except OSError as error:
        return Check("storage", WARN, f"cannot measure the workspace: {error}")
    reason = storage_budget.refusal(budget)
    if reason:
        return Check(
            "storage",
            MISSING,
            reason,
            "free disk space on this filesystem, or move the workspace to a larger disk",
        )
    note = storage_budget.warning(budget)
    if note:
        return Check("storage", WARN, note, "prune or move the workspace before a long run")
    return Check(
        "storage",
        OK,
        f"workspace {storage_budget.human(budget['workspace_bytes'])} projects "
        f"{storage_budget.human(budget['projected_bytes'])} of run copies; "
        f"{storage_budget.human(budget['free_bytes'])} free",
    )


def builtin_opencode(engine: str | None) -> bool:
    """Whether a new run with this resolved engine launches built-in OpenCode stages, the ones kernel
    containment covers: not Codex or Claude Code, and the default provider is the built-in opencode."""
    if engine in ("codex", "claude"):
        return False
    try:
        return default_provider() == "opencode"
    except (OSError, ValueError):  # the engine verdict reports it
        return False


def all_checks(
    workspace: Path,
    engine: str | None = None,
    which=shutil.which,
    runner=run,
    resolve=autocode_providers.resolve,
    found: dict | None = None,
) -> list[Check]:
    """``found``, when given, receives ``containment``: the containment_status() the checks show, or
    None when the run launches no built-in OpenCode stage (Codex, Claude Code, a configured provider),
    which then has no containment row."""
    # Read once, in the workspace as run setup reads it: the engine and containment checks both use it,
    # so an OpenCode that does not answer costs one timeout.
    opencode_version = (
        runner(["opencode", "--version"], cwd=workspace if workspace.is_dir() else None) if which("opencode") else None
    )
    checks = engine_checks(which, runner, opencode_version)
    # --engine codex uses Codex's own transport; --engine claude runs Claude Code (the built-in claude
    # provider); no flag and --engine opencode run the default provider. Without the flag a new run
    # takes the engine its project's files set (#707), and so does this verdict.
    origin = None
    if engine is None:
        engine, origin = project_engine(workspace)
    if engine == "codex":
        verdict = engine_verdict(checks, engine, origin=origin)
    elif engine == "claude":
        verdict = claude_checks(checks, workspace, engine, which, runner, resolve, origin)
    else:
        verdict = opencode_checks(checks, workspace, None if origin else engine, resolve, which=which, runner=runner)
    containment = containment_status(opencode_version) if builtin_opencode(engine) else None
    if found is not None:
        found["containment"] = containment
    return [
        python_check(),
        psutil_check(runner),
        git_check(which, runner),
        *checks,
        verdict,
        *([containment_check(containment)] if containment else []),
        *workspace_check(workspace, runner),
        *local_compose_checks(which, runner),
        session_check(),
        shared_host_check(),
        path_check(which),
        runner_check(),
        install_drift_check(workspace=workspace),
        storage_check(workspace),
        project_settings(workspace)[0],
        project_memory_check(workspace),
    ]


def project_memory_snapshot(workspace):
    try:
        from . import autocode_project_memory_interface as memory
    except ImportError:
        import autocode_project_memory_interface as memory
    return memory.load(workspace)


def project_memory_check(workspace):
    snapshot = project_memory_snapshot(workspace)
    if snapshot["status"] == "invalid":
        return Check(
            "project memory", WARN, snapshot["note"], "inspect or use autocode forget; learned defaults are ignored"
        )
    record = snapshot.get("record") or {}
    detail = (
        f"{len(record.get('conventions', []))} historical human conventions; "
        f"framework {record.get('framework') or 'unknown'}; .autocode/project.json"
        if record
        else "none; future completed runs can record shareable project conventions"
    )
    return Check("project memory", OK, detail)


def project_engine(workspace: Path) -> tuple[str | None, str | None]:
    """The engine a new run in ``workspace`` takes from its project's files (#707) and the file that sets
    it, or (None, None); a file a run would refuse is reported by the project settings check instead."""
    try:
        from . import autocode_project_config as project_config
    except ImportError:
        import autocode_project_config as project_config
    try:
        value, source = project_config.read(workspace).get("engine", (None, None))
    except ValueError:
        return None, None
    return (value, str(source)) if value in ENGINES else (None, None)


def project_settings(workspace: Path) -> tuple[Check, list[dict]]:
    """Where a new run in ``workspace`` takes each setting from, when no flag gives it (#707): the
    project's files (autocode_project_config), the user's provider choice, or the built-in default.
    A project file a run would refuse is missing; the rows then hold only the provider."""
    try:
        from . import autocode_args
        from . import autocode_project_config as project_config
    except ImportError:
        import autocode_args
        import autocode_project_config as project_config
    parser = autocode_args.build_parser(None, opencode_provider.DEFAULT_MODELS)
    actions, args = parser._option_string_actions, parser.parse_args([])
    rows = []
    try:
        applied = project_config.apply(actions, args, set(), workspace)
        check = Check(
            "project settings",
            OK,
            f"{len(applied)} from {', '.join(sorted(set(applied.values())))}"
            if applied
            else "none; a new run uses its flags and the built-in defaults",
        )
        for key in project_config.KEYS:
            value = getattr(args, actions["--" + key].dest) if key in applied else None
            rows.append({"key": key, "value": value, "source": applied.get(key, "built-in default")})
        for key in project_config.FILE_ONLY:  # no flag; the value apply saved on args (require-approver)
            value = getattr(args, key.replace("-", "_")) if key in applied else None
            rows.append({"key": key, "value": value, "source": applied.get(key, "built-in default")})
    except ValueError as error:
        check = Check("project settings", MISSING, str(error), "correct the file: a new run refuses it")
    engine = next((row for row in rows if row["key"] == "engine" and row["value"] == "claude"), None)
    if engine is not None:  # engine = "claude" is the built-in claude provider (#717)
        rows.append({"key": "provider", "value": "claude", "source": engine["source"]})
        return check, rows
    try:
        name, source = autocode_providers.default_choice()
        rows.append({"key": "provider", "value": name, "source": source})
    except (OSError, ValueError):
        pass  # the provider check reports it
    return check, rows


def local_compose_checks(which=shutil.which, runner=run) -> list[Check]:
    """Docker is optional: missing or unsupported local setup only warns."""
    try:
        from . import autocode_local_run as local_run
    except ImportError:
        import autocode_local_run as local_run
    fix = "only for components --run-local: install/start Docker with a supported Compose plugin and a local context"
    if not which("docker"):
        return [Check("docker", WARN, "Docker is not on PATH; optional for local component smoke checks", fix)]
    checks = [Check("docker", OK, "Docker found on PATH (optional)")]
    for row in local_run.docker_checks(lambda argv, timeout: runner(argv)):
        checks.append(Check(row["name"], OK if row["ok"] else WARN, row["detail"], "" if row["ok"] else fix))
    return checks


def passed(checks: list[Check]) -> bool:
    """Missing engines are fine as long as the engine verdict is ok; everything else, the provider
    and routes the run would use included, must not be missing."""
    return all(c.status != MISSING for c in checks if not c.name.startswith("engine:"))


def parser() -> argparse.ArgumentParser:
    """The arguments `autocode doctor` reads; `autocode completion` reads them too."""
    result = argparse.ArgumentParser(
        prog="autocode doctor", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    result.add_argument("--workspace", type=Path, default=Path.cwd(), help="project to check (default: cwd)")
    result.add_argument(
        "--engine",
        choices=ENGINES,
        help="check what a run with this --engine uses: codex is Codex; claude is Claude Code "
        "(the built-in claude provider); opencode, like no flag, is the default provider",
    )
    result.add_argument("--json", action="store_true", help="print the checks as JSON")
    return result


def cli(argv: list[str] | None = None) -> int:
    arguments = parser()
    args = arguments.parse_args(argv)
    found: dict = {}
    checks = all_checks(args.workspace.resolve(), args.engine, found=found)
    ok = passed(checks)
    settings = project_settings(args.workspace.resolve())[1]
    memory = project_memory_snapshot(args.workspace.resolve())
    if args.json:
        print(
            json.dumps(
                {
                    "ok": ok,
                    "checks": [asdict(c) for c in checks],
                    "containment": found.get("containment"),
                    "settings": settings,
                    "project_memory": memory,
                },
                indent=2,
            )
        )
    else:
        marks = {OK: "✔", MISSING: "✖", WARN: "!"}
        for check in checks:
            print(f"{marks[check.status]} {check.name:<16} {check.detail}")
            if check.fix:
                print(f"  {'':<16} fix: {check.fix}")
        chosen = [row for row in settings if row["source"] != "built-in default" or row["key"] == "provider"]
        print("Settings for a new run when no flag gives them:")
        for row in chosen:
            print(f"  {row['key']:<30} {row['value']}  ({row['source']})")
        print("  every other setting: its built-in default (a flag on the command line always wins)")
        remembered = memory.get("record") or {}
        if remembered:
            print("Project memory (historical preferences; current task and configuration take precedence):")
            for row in remembered.get("conventions", []):
                print(
                    "  "
                    + json.dumps(row["question"], ensure_ascii=True)
                    + " -> "
                    + json.dumps(row["answer"], ensure_ascii=True)
                )
            for row in remembered.get("routes", []):
                print("  observed route " + json.dumps(row, ensure_ascii=True, sort_keys=True))
            if remembered.get("human_pause_states"):
                print("  human pause states: " + ", ".join(remembered["human_pause_states"]))
        print("ready for a first task" if ok else "not ready: fix the ✖ items above")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(cli())
