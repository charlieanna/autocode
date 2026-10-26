"""Public connection discovery and delegated login; never launches inference.

Credentials stay with the backend. Status subprocess output is deliberately not
returned: company helpers can print tokens, account names or environment exports.
Only user-level registered providers are discovered, never workspace TOML files.
`list --routes` and the end of `setup` read the connected tool's own model listing to
say which route each role would use (#2057); that is a listing, never a model call.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys

try:
    from . import autocode_direct_config as direct
    from . import autocode_providers as providers
    from .providers import command
except ImportError:
    import autocode_direct_config as direct
    import autocode_providers as providers
    from providers import command

BUILTINS = {
    "kilocode": ("Kilo", "kilo", ["kilo", "auth", "login"], ["kilo", "auth", "list"]),
    "opencode": ("OpenCode", "opencode", ["opencode", "auth", "login"], ["opencode", "auth", "list"]),
    "claude": ("Claude Code", "claude", ["claude", "auth", "login"], ["claude", "auth", "status", "--json"]),
}


def parser():
    result = argparse.ArgumentParser(prog="autocode connections", description=__doc__)
    actions = result.add_subparsers(dest="action", required=True)
    listing = actions.add_parser("list", help="Discover installed and registered backends without checking login")
    listing.add_argument("--json", action="store_true", help="Return the public connection view")
    listing.add_argument(
        "--routes",
        action="store_true",
        help="Also choose the route each role would use, from the connected tool's own model listing (#2057)",
    )
    check = actions.add_parser("check", help="Check backend credentials without launching a model")
    check.add_argument("--provider", required=True)
    check.add_argument("--json", action="store_true")
    login = actions.add_parser("login", help="Run the backend's own interactive sign-in in this terminal")
    login.add_argument("--provider", required=True)
    actions.add_parser("setup", help="Configure AutoCode's MiMo/Z.ai/OpenAI/Claude API connection in this terminal")
    add = actions.add_parser(
        "add", help="Add or rotate one API provider's key in this terminal; several providers may be configured at once"
    )
    add.add_argument("provider", nargs="?", help="mimo, zai, openai, openrouter, claude or compatible")
    return result


def _definition(name):
    # locate() validates names before constructing a path. Builtins have no
    # project-controlled overrides; registered login commands are explicit opt-in.
    if name in BUILTINS:
        label, executable, login, status = BUILTINS[name]
        return label, executable, login, status, None
    provider = command.load(name)
    config = provider._config
    connection = config.get("connection", {})
    version = config.get("version_command") or config["command"]
    return name, version[0], connection.get("login_command"), connection.get("status_command"), provider


def connection_view(name, *, check=False):
    """One additive, JSON-safe connection view with core-owned action argv."""
    if name == "autocode":
        return direct.describe(check=check)
    row = {
        "id": name,
        "label": name,
        "connection_status": "unavailable",
        "detail": "Backend unavailable",
        "actions": [],
    }
    try:
        label, executable, login, status, provider = _definition(name)
        row["label"] = label
        if not shutil.which(executable):
            row["detail"] = "Backend executable is not on PATH. Install it in the AutoCode CLI environment."
            return row
        row.update(
            connection_status="not_checked",
            detail="Installed. Login has not been checked; model access is verified when a run starts.",
        )
        row["actions"].append(
            {
                "id": "check",
                "label": "Check connection",
                "next_command": ["connections", "check", "--provider", name, "--json"],
            }
        )
        if login:
            row["actions"].append(
                {
                    "id": "login",
                    "label": "Sign in / Reconnect",
                    "next_command": ["connections", "login", "--provider", name],
                }
            )
        if not status:
            row["detail"] = (
                "Login is managed by this backend. Configure [connection] status_command and login_command to enable connection checks and sign-in."
            )
            return row
        if not check:
            return row
        try:
            result = subprocess.run(status, capture_output=True, text=True, timeout=15, shell=False)
        except (OSError, subprocess.TimeoutExpired):
            row.update(
                connection_status="check_failed",
                detail="Connection check could not finish. Open the backend to diagnose it.",
            )
            return row
        if name == "claude":
            try:
                logged_in = json.loads(result.stdout).get("loggedIn") is True
            except (ValueError, AttributeError):
                logged_in = False
            ready = result.returncode == 0 and logged_in
        elif name in ("kilocode", "opencode"):
            # Their auth-list command succeeds even with no saved credentials.
            # Do not mistake an empty list for a signed-in account, or echo it.
            output = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout + result.stderr)
            ready = result.returncode == 0 and bool(
                re.search(r"^\s*[●•]\s+.+\s+(?:api|oauth)\s*$", output, re.MULTILINE)
            )
        else:
            # Registered status_command contract: 0 credentials present, 1 no
            # login, other exits operational failure. No model call is allowed.
            ready = result.returncode == 0
        if result.returncode not in (0, 1):
            row.update(
                connection_status="check_failed",
                detail="Backend connection check failed. Use its own terminal diagnostics.",
            )
        elif ready:
            row.update(
                connection_status="credentials_present",
                detail="Saved credentials found; they are not tested here. An expired or revoked key, model access and billing show up when a run starts.",
            )
        else:
            row.update(
                connection_status="sign_in_required",
                detail="No saved login was confirmed. Sign in with the backend, then check again.",
            )
    except (OSError, ValueError, RuntimeError):
        row["detail"] = "Provider configuration could not be loaded. Check its user-level TOML configuration."
    return row


def view():
    """Discover connection metadata only; no backend subprocesses or model calls."""
    names = {*BUILTINS, "autocode"}
    root = command.user_config_path("discovery").parent
    names.update(path.stem for path in root.glob("*.toml") if command._NAME.fullmatch(path.stem))
    rows = [connection_view(name) for name in sorted(names)]
    # The provider new runs use without --provider (AUTOCODE_PROVIDER, then default_provider in the
    # user's config, then OpenCode): display only, so a client can say what a new chat runs on.
    try:
        default = providers.default_name()
    except (OSError, ValueError):
        default = None
    for row in rows:
        if row["id"] == default:
            row["default"] = True
    return {"version": 1, "connections": rows}


def routes():
    """The route each role would run on, from the connected tool's own local listing (#2057).

    Listing only: it reads the tool's model and login lists, launches no model and returns no
    credential. A tool that cannot list its models, or whose accounts cannot field an
    independent checker, carries ``routes_stop`` instead of failing the connection view.
    """
    try:
        from . import model_catalogue
    except ImportError:
        import model_catalogue
    try:
        found = model_catalogue.preview()
    except (RuntimeError, ValueError, OSError) as error:
        return {"tool": None, "routes": [], "routes_lines": [], "routes_stop": str(error)}
    return {
        "tool": found.get("tool"),
        "routes": model_catalogue.route_rows(found),
        "routes_lines": model_catalogue.route_lines(found),
        "routes_stop": found.get("stop"),
    }


def say_routes(stream=None):
    """Print one line per role after setup: the choice a person no longer has to make (#2057)."""
    stream = stream or sys.stdout
    found = routes()
    if found["routes_stop"] or not found["routes"]:
        print(
            "Routes for a new run are not chosen yet: " + (found["routes_stop"] or "no qualified route was found"),
            file=stream,
        )
        return
    print(f"Routes for a new run, from {found['tool']}'s local listing:", file=stream)
    for line in found["routes_lines"]:
        print("  " + line, file=stream)


def cli(argv):
    args = parser().parse_args(argv)
    if args.action in ("setup", "add"):
        try:
            code = direct.setup() if args.action == "setup" else direct.add(args.provider)
        except (KeyboardInterrupt, EOFError):
            print("\nAutoCode setup cancelled.")
            return 130
        if code == 0 and args.action == "setup":
            say_routes()
        return code
    if args.action == "list":
        result = view()
        if args.routes:
            result.update(routes())
        else:
            result["routes"] = None
            result["routes_next_command"] = ["connections", "list", "--routes", "--json"]
    elif args.action == "check":
        result = connection_view(args.provider, check=True)
    else:
        # Resolve again at execution time. The client cannot supply arbitrary
        # executables or credential material in this public action.
        try:
            _, executable, login, _, _ = _definition(args.provider)
            if not login or not shutil.which(executable):
                print("Backend login is unavailable; configure its login_command or install the backend.")
                return 2
            return subprocess.call(login, shell=False)
        except (OSError, ValueError, RuntimeError):
            print("Backend login could not start; check its user-level configuration.")
            return 2
    print(json.dumps(result))
    return 0
