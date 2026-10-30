"""`autocode setup`: connect the backends AutoCode runs on, once, right after installing it (#2049).

AutoCode offers four backends (owner decision 2026-10-30): Claude Code (a Claude subscription) and
Codex (a ChatGPT login) as engines, and OpenCode and Kilo for every other account and API key.
``setup`` checks each one with its own login listing, the check ``autocode connections check``
runs. In a terminal, when neither OpenCode nor Kilo is installed, it offers to install OpenCode
(the pinned, containment-qualified release) with npm, the one question it asks before changing
the machine; ``--install opencode|kilocode`` installs that hub without asking. It then offers each
installed, signed-out backend's own sign-in, where a person connects ChatGPT, GLM, MiMo or an API
key. Finally it says which backend new runs will use and, when it can, the route each role takes,
so the first task needs no flags and nobody is asked to pick a model per stage.

It reads no credential and launches no model. ``--json`` and a non-terminal run ask nothing and
install only what ``--install`` names; ``--json`` returns the same rows clients read from
``autocode connections``, each with its named actions.

Layer: interface. It composes autocode_connections (the connection view and its login action)
and autocode_connected_engine (which backend a run that names none takes); nothing imports it.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys

try:
    from . import autocode_connected_engine as connected_engine
    from . import autocode_connections as connections
    from . import autocode_providers as providers
except ImportError:
    import autocode_connected_engine as connected_engine
    import autocode_connections as connections
    import autocode_providers as providers

# The four backends, in the order setup shows them, with the install command for a missing one.
BACKENDS = (
    ("claude", "npm install -g @anthropic-ai/claude-code"),
    ("codex", "npm install -g @openai/codex"),
    ("opencode", "npm install -g opencode-ai@1.18.33"),
    ("kilocode", "npm install -g @kilocode/cli@7.8.3"),
)
INSTALL = dict(BACKENDS)
# The hubs a person connects every other account and API key in; setup installs one when neither is present.
HUBS = {"opencode": "OpenCode 1.18.33", "kilocode": "Kilo 7.8.3"}
NODE = "https://nodejs.org"
CONNECTED = frozenset({"credentials_present"})
MARKS = {"credentials_present": "✔", "sign_in_required": "•", "not_checked": "•"}


def parser():
    result = argparse.ArgumentParser(prog="autocode setup", description=__doc__.split("\n\n")[0])
    result.add_argument("--json", action="store_true", help="Report the backends and their actions; ask nothing")
    result.add_argument(
        "--install",
        choices=sorted(HUBS),
        help="Install this hub with npm when it is missing, without asking (OpenCode or Kilo carry your accounts)",
    )
    return result


def rows(view=None):
    """Each backend's connection row, checked with its own login listing, plus its install command."""
    found = []
    for name, install in BACKENDS:
        row = dict((view or connections.connection_view)(name, check=True))
        if row.get("connection_status") == "unavailable" and "not on PATH" in str(row.get("detail")):
            row["install_command"] = install
        found.append(row)
    return found


def new_runs_use(found, *, choose=None) -> dict | None:
    """The backend a new run that names none takes, as the runner chooses it, or None when none is ready.

    An explicit choice (``AUTOCODE_PROVIDER``, ``default_provider``) is reported as such; otherwise
    OpenCode when it is signed in, else the first connected backend ``connected_engine`` takes.
    """
    try:
        default, origin = providers.default_choice()
    except (OSError, ValueError) as error:
        return {"id": None, "label": None, "reason": f"your AutoCode config could not be read: {error}"}
    if origin != "built-in default":
        return {"id": default, "label": default, "reason": f"named by {origin}"}
    status = {row["id"]: row.get("connection_status") for row in found}
    if status.get("opencode") in CONNECTED:
        return {"id": "opencode", "label": "OpenCode", "reason": "OpenCode is installed and signed in"}
    chosen = (choose or connected_engine.choose)()
    if chosen is None:
        return None
    return {"id": chosen["id"], "label": chosen["label"], "reason": f"{chosen['label']} is connected"}


def text(found, use) -> str:
    lines = ["AutoCode runs on Claude Code, Codex, OpenCode or Kilo. What this machine has:"]
    for row in found:
        state = str(row.get("connection_status") or "unknown")
        if row.get("install_command"):
            lines.append(f"✘ {row['label']}: not installed. Install it with: {row['install_command']}")
            continue
        words = {"credentials_present": "signed in", "sign_in_required": "installed, not signed in"}
        lines.append(f"{MARKS.get(state, '✘')} {row['label']}: {words.get(state, state.replace('_', ' '))}")
        if state not in CONNECTED:
            for action in row.get("actions") or []:
                if action.get("id") == "login":
                    lines.append(f"    Sign in: autocode {' '.join(action['next_command'])}")
    if use is None:
        lines += [
            "",
            "Nothing is connected yet. Sign in to one of these, or install one and sign in, then run "
            "`autocode setup` again. API keys (Z.ai, MiMo, OpenAI, Anthropic) go in OpenCode or Kilo.",
        ]
    elif use.get("id") is None:
        lines += ["", f"New runs cannot start: {use['reason']}."]
    else:
        lines += ["", f'New runs use {use["label"]} ({use["reason"]}). Start a task with: autocode "your idea"']
    return "\n".join(lines)


def _sign_in(found, *, ask=input, login=None, out=print) -> bool:
    """Offer each installed, signed-out backend's own sign-in; True when one was attempted."""
    tried = False
    for row in found:
        if row.get("connection_status") != "sign_in_required":
            continue
        if not any(action.get("id") == "login" for action in row.get("actions") or []):
            continue
        answer = ask(f"Sign in to {row['label']} now? [y/N] ").strip().lower()
        if answer not in ("y", "yes"):
            continue
        tried = True
        code = (login or connections.cli)(["login", "--provider", row["id"]])
        if code != 0:
            out(f"{row['label']} sign-in did not finish (exit {code}); run `autocode setup` again to retry.")
    return tried


def _install(found, *, wanted=None, ask=None, which=shutil.which, run=None, out=print) -> bool:
    """Install a missing hub with npm; True when an install finished.

    ``wanted`` (``--install``) installs that hub without asking. Otherwise, only when ``ask`` is given
    (an interactive terminal) and neither hub is installed, OpenCode is offered once.
    """
    missing = {row["id"] for row in found if row.get("install_command")}
    if wanted is not None:
        if wanted not in missing:
            return False
        name = wanted
    else:
        if ask is None or not HUBS.keys() <= missing:
            return False
        name = "opencode"
        answer = ask(f"Install {HUBS[name]} now, to connect your accounts in it ({INSTALL[name]})? [Y/n] ")
        if answer.strip().lower() not in ("", "y", "yes"):
            return False
    if not which("npm"):
        out(f"npm is not on PATH. Install Node.js ({NODE}), then run: {INSTALL[name]}")
        return False
    # npm's own output goes to stderr, so --json stays one JSON document.
    code = (run or _run)(INSTALL[name].split())
    if code != 0:
        out(f"npm could not install {HUBS[name]} (exit {code}). Run it yourself: {INSTALL[name]}")
        return False
    out(f"Installed {HUBS[name]}.")
    return True


def _run(argv) -> int:
    return subprocess.call(argv, stdout=sys.stderr, shell=False)


# Tools whose own model and login listings automatic selection reads; the engines run their defaults.
LISTED = frozenset({"opencode", "kilocode"})


def say_routes(use, *, stream=None):
    """The route each role takes on the backend new runs use, or where that backend's defaults are."""
    if use["id"] in LISTED:
        connections.say_routes(stream, provider=use["id"])
        return
    print(
        f"Each role runs {use['label']}'s default model for it (docs/models.md#default-models).",
        file=stream or sys.stdout,
    )


def _interactive() -> bool:
    return sys.stdin.isatty() and sys.stdout.isatty()


def cli(argv=None) -> int:
    args = parser().parse_args(argv)
    found = rows()
    if args.json or not _interactive():
        notes = sys.stderr if args.json else sys.stdout
        if _install(found, wanted=args.install, out=lambda line: print(line, file=notes)):
            found = rows()
        use = new_runs_use(found)
        if args.json:
            print(json.dumps({"version": 1, "backends": found, "new_runs_use": use}))
        else:
            print(text(found, use))
    else:
        print(text(found, new_runs_use(found)))
        try:
            changed = False
            if _install(found, wanted=args.install, ask=input):
                found, changed = rows(), True
            if _sign_in(found):
                found, changed = rows(), True
        except (KeyboardInterrupt, EOFError):
            print("\nAutoCode setup stopped; nothing else was changed.")
            return 130
        use = new_runs_use(found)
        if changed:
            print()
            print(text(found, use))
    if use is not None and use.get("id") is not None and not args.json:
        say_routes(use)
    return 0 if use is not None and use.get("id") is not None else 1
