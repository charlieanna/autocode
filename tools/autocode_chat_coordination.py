"""Render and take the core's automatic coordination actions from terminal chat."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

try:
    from . import autocode_client_actions as actions
except ImportError:
    import autocode_client_actions as actions


def environment(workspace, selected=None):
    """Use the task's existing registry default for setup and every child call."""
    result = {**os.environ, **(selected or {})}
    result.setdefault("AUTOCODE_HOME", str(Path(workspace).resolve() / ".autocode" / "registry"))
    return result


def named(value, run_dir, operation=None):
    if not isinstance(value, list) or len(value) < 6 or value[:2] != ["autocode", "coordinate"]:
        raise actions.ClientGap("The core supplied no valid coordination action")
    if value[3:5] != ["--run-dir", str(Path(run_dir).resolve())] or value[-1] != "--json":
        raise actions.ClientGap("The coordination action names another run or output contract")
    if operation and value[2] != operation:
        raise actions.ClientGap("The coordination action differs from the displayed decision")
    if value[2] not in ("status", "advance", "approve", "choose", "cancel", "budget"):
        raise actions.ClientGap("Unsupported coordination action")
    return value[1:]


def read(client, view):
    offered = view.get("coordination") or {}
    reply = client.cli(named(offered.get("status_command"), client.run_dir, "status"), timeout=120)
    try:
        summary = json.loads(reply.stdout)
    except ValueError:
        raise actions.ClientGap("Coordination status is unreadable; no action was taken") from None
    if (
        reply.returncode
        or not isinstance(summary, dict)
        or summary.get("version") != 1
        or summary.get("anchor") != str(client.run_dir.resolve())
    ):
        raise actions.ClientGap(
            "Coordination status refused: "
            + str(summary.get("error", "invalid identity") if isinstance(summary, dict) else "invalid identity")
        )
    return summary


def step(client, view, render_plan):
    summary = read(client, view)
    for row in summary["members"]:
        line = f"{row['target']}: {row['status']}" + (f" · {row['pr_url']}" if row.get("pr_url") else "")
        if line not in client.said:
            client.say(line)
            client.said.add(line)
    if summary["done"]:
        client.say("Joint regression passed. Linked pull requests are ready:")
        for row in summary["members"]:
            if row.get("pr_url"):
                client.say(f"{row['target']}: {row['pr_url']}")
        return 0
    need = summary["needs"]
    if summary.get("next_command"):
        client.advance(named(summary["next_command"], client.run_dir, "advance"))
        return None
    if need["kind"] == "budget":
        return budget_reply(client, view, need)
    if summary.get("approval"):
        if need["kind"] == "approval":
            for row in need["summary"]["runs"]:
                client.say("\n" + row["target"] + "\n" + render_plan(row["plan"]))
        else:
            client.say("\nJoint regression\n" + render_plan(need["plan"]))
        client.say(summary["approval"]["publication"])
        if client.ask("Approve these coordinated plans? [y/N]: ").strip().casefold() not in ("y", "yes"):
            client.say("Plans remain unapproved. Reopen this chat to continue.")
            return 2
        fresh = read(client, view)
        if (fresh.get("approval") or {}).get("token") != summary["approval"]["token"]:
            client.say("A plan changed; showing the current plans before approval.")
            return None
        argv = list(named(summary["approval"]["next_command"], client.run_dir, "approve"))
        token_index = argv.index("--token") + 1
        token = argv[token_index]
        argv[token_index] = "-"
        client.advance(argv, env={"AUTOCODE_COORDINATION_TOKEN": token})
        return None
    if need["kind"] == "repository":
        client.say(need["reason"] + ": " + need["target"])
        candidates = need["candidates"]
        if not candidates:
            client.ask(
                "Use /repos PATH to authorize its local checkout, then press Enter to retry; /pause leaves it stopped: "
            )
            return None
        for index, row in enumerate(candidates, 1):
            client.say(f"{index}. {row['workspace']} (base {row['base'][:12]})")
        selected = client.ask(
            "Which repository owns this interface? Enter its number, or leave blank to stop: "
        ).strip()
        if not selected.isdigit() or not 1 <= int(selected) <= len(candidates):
            return 2
        client.advance(named(candidates[int(selected) - 1]["next_command"], client.run_dir, "choose"))
        return None
    if need["kind"] == "input":
        child_view = need["view"]
        observe, unavailable = client._observe, client._unavailable
        child = type(client)(
            Path(child_view["workspace"]),
            Path(child_view["run_dir"]),
            command=client.command,
            env=client.env,
            ask=client._ask,
            say=client._say,
            observe=observe,
            unavailable=unavailable,
            wait=client._wait,
        )
        # The terminal's idle callback still polls this anchor while the child
        # owns the screen. Keep those reads without replacing its foreground.
        client._observe, client._unavailable = None, None
        try:
            child.accept_status(child_view)
            child.show(child_view)
            return None if child.respond(child_view) else 2
        finally:
            client._observe, client._unavailable = observe, unavailable
            child.close()
    if need["kind"] == "watch":
        client.pace(client.poll_seconds)
        return None
    client.say(need.get("reason") or "Coordination is cancelled; saved work is retained.")
    return 2


def budget_reply(client, view, need):
    argv = list(named(need.get("next_command"), client.run_dir, "budget"))
    if argv != [
        "coordinate",
        "budget",
        "--run-dir",
        str(client.run_dir.resolve()),
        "--token",
        need.get("token"),
        "--max-cost-usd",
        "N",
        "--json",
    ]:
        raise actions.ClientGap("The shared-budget action does not match the displayed checkpoint")
    minimum = need.get("minimum_cap_usd")
    if type(minimum) not in (int, float) or not math.isfinite(minimum) or minimum <= 0:
        raise actions.ClientGap("The shared-budget decision has no finite minimum")
    accounting = "complete" if need.get("complete") else "incomplete; unknown costs remain unknown"
    client.say(
        f"Shared accounting cap: ${need['cap_usd']:g}; known spend: ${need['known_spent_usd']:g} ({accounting})."
    )
    client.say(need["effect"])
    text = client.ask(f"New whole-set cap in USD ({minimum:g} or more; Enter keeps it paused): ").strip()
    if not text:
        return 2
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,6})?", text) or not minimum <= float(text) <= actions.MAX_SAFE_INTEGER:
        client.say("No cap changed. Enter a finite amount at or above the displayed minimum.")
        return None
    if client.ask(
        f"Set the whole-set accounting cap to ${text} and continue the displayed cost recovery? [y/N]: "
    ).strip().lower() not in ("y", "yes"):
        return 2
    fresh = read(client, view)
    if (fresh.get("needs") or {}).get("token") != need.get("token") or (fresh.get("needs") or {}).get(
        "kind"
    ) != "budget":
        client.say("The shared-budget checkpoint changed; read the current offer before deciding.")
        return None
    argv[argv.index("--max-cost-usd") + 1] = text
    token_index = argv.index("--token") + 1
    token = argv[token_index]
    argv[token_index] = "-"
    client.advance(argv, env={"AUTOCODE_COORDINATION_TOKEN": token})
    return None


def repositories(history, argument):
    if argument.strip():
        history.read(["registry", "add", str(Path(argument.strip()).expanduser().absolute()), "--json"])
    listing = history.read(["registry", "list", "--json"])
    readiness = history.read(["coordinate", "access", "--json"])
    access = readiness.get("github")
    if not isinstance(access, dict):
        raise actions.ClientGap("GitHub readiness is unavailable: " + str(readiness.get("error", "invalid response")))
    rows = [f"{row['workspace']} · {row.get('availability', 'unknown')}" for row in listing["workspaces"]]
    return (
        "Repositories available for automatic discovery:\n"
        + "\n".join(rows)
        + (
            "\nGitHub authentication is available; write access is checked when selecting a repository."
            if access["configured"]
            else "\nGitHub authentication is missing: use gh auth login, GITHUB_TOKEN or GH_TOKEN before starting automatic delivery."
        )
        + "\n/repos PATH authorizes another local Git checkout. AutoCode selects matching repositories when investigating a bug."
    )
