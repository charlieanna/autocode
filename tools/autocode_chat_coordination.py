"""Render and take the core's automatic coordination actions from terminal chat."""

from __future__ import annotations

import json
import math
import os
import re
from pathlib import Path

try:
    from . import autocode_chat_task_messages as task_messages
    from . import autocode_client_actions as actions
except ImportError:
    import autocode_chat_task_messages as task_messages
    import autocode_client_actions as actions


def environment(workspace, selected=None):
    """Keep repository discovery shared while preserving project-local run storage."""
    result = {**os.environ, **(selected or {})}
    if not result.get("AUTOCODE_SOURCE_CATALOG_HOME"):
        # Capture the user's explicit shared home before the chat assigns its
        # legacy workspace-local AUTOCODE_HOME default below. This makes fresh
        # repository discovery independent of the directory used to launch chat.
        selected_home = result.get("AUTOCODE_HOME") or str(Path.home() / ".autocode")
        result["AUTOCODE_SOURCE_CATALOG_HOME"] = selected_home
    result.setdefault("AUTOCODE_HOME", str(Path(workspace).resolve() / ".autocode" / "registry"))
    return result


def named(value, run_dir, operation=None):
    if not isinstance(value, list) or len(value) < 6 or value[:2] != ["autocode", "coordinate"]:
        raise actions.ClientGap("The core supplied no valid coordination action")
    if value[3:5] != ["--run-dir", str(Path(run_dir).resolve())] or value[-1] != "--json":
        raise actions.ClientGap("The coordination action names another run or output contract")
    if operation and value[2] != operation:
        raise actions.ClientGap("The coordination action differs from the displayed decision")
    if value[2] not in (
        "status",
        "advance",
        "bind",
        "approve",
        "choose",
        "cancel",
        "resume",
        "budget",
        "planning-budget",
    ):
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
    if summary.get("accounting") and getattr(client, "_observe_coordination", None):
        client._observe_coordination(summary["accounting"])
    if getattr(client, "_observe_conversation", None):
        client._observe_conversation(summary)
    task_messages.show(client, summary)
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
    if need["kind"] == "carry_approval":
        carried = need.get("carried") or {}
        line = (
            "The revised plans keep what you approved ("
            + ", ".join(carried.get("unchanged") or [])
            + "); continuing under that approval."
        )
        if line not in client.said:
            client.say(line)
            client.said.add(line)
    if summary.get("next_command"):
        action = summary["next_command"]
        operation = action[2] if isinstance(action, list) and len(action) > 2 else None
        if operation not in ("advance", "bind"):
            raise actions.ClientGap("The core supplied an unsupported automatic coordination action")
        advance_with_progress(client, summary, named(action, client.run_dir, operation))
        return None
    if need["kind"] in ("budget", "planning_budget", "coordination_resume"):
        return budget_reply(client, view, need)
    if summary.get("approval"):
        if need["kind"] == "approval":
            source_catalog = summary.get("source_catalog") or {}
            if source_catalog:
                client.say(
                    "Repository discovery: "
                    + str(source_catalog.get("catalog"))
                    + " · catalog "
                    + str(source_catalog.get("catalog_identity"))
                    + " · source snapshot "
                    + str(source_catalog.get("source_snapshot"))
                )
            for member in summary["members"]:
                evidence = member.get("supporting_evidence") or {}
                client.say(
                    f"\nRepository: {member['target']} · role {member.get('role', 'member')}"
                    + f"\n  Location: {member.get('location', member.get('workspace'))}"
                    + f"\n  Baseline: {member.get('base', 'unknown')}"
                )
                if evidence.get("source_quote"):
                    client.say("  Ticket grounding: " + evidence["source_quote"])
                if evidence.get("evidence"):
                    client.say(
                        "  Source evidence: "
                        + str(evidence.get("evidence_repository", "unknown repository"))
                        + "@"
                        + str(evidence.get("evidence_revision", "unknown revision"))
                        + ":"
                        + evidence["evidence"]
                    )
            for row in need["summary"]["runs"]:
                client.say("\n" + row["target"] + "\n" + render_plan(row["plan"]))
            if need.get("joint_plan"):
                client.say("\nJoint regression\n" + render_plan(need["joint_plan"]))
        else:
            client.say("\nJoint regression\n" + render_plan(need["plan"]))
        client.say(summary["approval"]["publication"])
        can_message = ((summary.get("conversation") or {}).get("input") or {}).get("kind") == "coordination_message"
        hint = ", or type a question or change" if can_message else ""
        answer = client.ask(f"Approve these coordinated plans? [y/N]{hint}: ").strip()
        if can_message and answer and answer.casefold() not in ("y", "yes", "n", "no"):
            sender = getattr(client, "_task_message_sender", None)
            if sender is None:
                sender = client._task_message_sender = task_messages.Sender(client)
            try:
                sender.send(answer, summary)
            except (actions.ClientGap, OSError, ValueError, TypeError, AttributeError):
                client.say(
                    "Task message receipt is unconfirmed. Retry the same text to reconcile it; plans remain unapproved."
                )
            return None
        if answer.casefold() not in ("y", "yes"):
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
            client.say(
                "No matching repository is available in the configured source catalog. "
                "Configure a source root once with `autocode registry roots add PATH` "
                "or register a standalone checkout with `autocode registry repositories add PATH`, "
                "then reopen this chat."
            )
            return 2
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


def advance_with_progress(client, summary, argv):
    """Observe the named participant while the anchor keeps sole control of advancement."""
    target = summary["needs"].get("target")
    participant = (
        summary.get("integration")
        if target == "Joint regression"
        else next((row for row in summary["members"] if row["target"] == target), None)
    )
    if not participant or not participant.get("workspace") or not participant.get("run_dir") or not client._observe:
        client.advance(argv)
        return
    observe, unavailable, wait = client._observe, client._unavailable, client._wait
    child = type(client)(
        Path(participant["workspace"]),
        Path(participant["run_dir"]),
        command=client.command,
        env=client.env,
        ask=client._ask,
        say=client._say,
        observe=observe,
        unavailable=unavailable,
        wait=wait,
        poll_seconds=client.poll_seconds,
    )

    def wait_with_progress(seconds):
        # poll schedules bounded public reads and consumes completed ones; it
        # never waits on a status subprocess or advances the participant.
        child.poll()
        return wait(seconds)

    client._observe, client._unavailable, client._wait = None, None, wait_with_progress
    try:
        client.advance(argv)
    finally:
        client._observe, client._unavailable, client._wait = observe, unavailable, wait
        child.close()


def budget_reply(client, view, need):
    planning = need["kind"] == "planning_budget"
    resuming = need["kind"] == "coordination_resume"
    operation = "resume" if resuming else "planning-budget" if planning else "budget"
    flag = "--max-planning-cost-usd" if planning else "--max-cost-usd"
    argv = list(named(need.get("next_command"), client.run_dir, operation))
    if argv != [
        "coordinate",
        operation,
        "--run-dir",
        str(client.run_dir.resolve()),
        "--token",
        need.get("token"),
        flag,
        "N",
        "--json",
    ]:
        raise actions.ClientGap("The shared-budget action does not match the displayed checkpoint")
    minimum = need.get("minimum_cap_usd")
    if type(minimum) not in (int, float) or not math.isfinite(minimum) or minimum <= 0:
        raise actions.ClientGap("The shared-budget decision has no finite minimum")
    maximum = need.get("maximum_cap_usd") if planning else actions.MAX_SAFE_INTEGER
    if type(maximum) not in (int, float) or not math.isfinite(maximum) or maximum < minimum:
        raise actions.ClientGap("The planning-budget decision has no valid maximum")
    accounting = "complete" if need.get("complete") else "incomplete; unknown costs remain unknown"
    cap = "no cap" if need["cap_usd"] in (None, 0) else f"${need['cap_usd']:g}"
    client.say(f"Shared accounting cap: {cap}; known spend: ${need['known_spent_usd']:g} ({accounting}).")
    client.say(need["effect"])
    if planning:
        client.say(
            f"Planning allowance: ${need['planning_cap_usd']:g}; planning spend: ${need['planning_spent_usd']:g}."
        )
    label = "planning allowance" if planning else "whole-set accounting cap"
    bounds = f"{minimum:g} to {maximum:g}" if planning else f"{minimum:g} or more"
    no_cap = resuming and need.get("allows_no_cap") is True
    if no_cap:
        bounds += "; 0 removes spending limits"
    text = client.ask(f"New {label} in USD ({bounds}; Enter keeps it paused): ").strip()
    if not text:
        return 2
    if not re.fullmatch(r"[0-9]+(?:\.[0-9]{1,6})?", text) or not (
        minimum <= float(text) <= maximum or (no_cap and float(text) == 0)
    ):
        client.say("No cap changed. Enter a finite amount within the displayed bounds.")
        return None
    confirmation = (
        "Remove spending limits and recover this saved coordination? [y/N]: "
        if no_cap and float(text) == 0
        else f"Set the {label} to ${text} and continue the displayed cost recovery? [y/N]: "
    )
    if client.ask(confirmation).strip().lower() not in ("y", "yes"):
        return 2
    fresh = read(client, view)
    if (fresh.get("needs") or {}).get("token") != need.get("token") or (fresh.get("needs") or {}).get("kind") != need[
        "kind"
    ]:
        client.say("The shared-budget checkpoint changed; read the current offer before deciding.")
        return None
    argv[argv.index(flag) + 1] = text
    token_index = argv.index("--token") + 1
    token = argv[token_index]
    argv[token_index] = "-"
    client.advance(argv, env={"AUTOCODE_COORDINATION_TOKEN": token})
    return None


def repositories(history, argument):
    listing = history.read(["registry", "roots", "list", "--json"])
    discovery = history.read(["registry", "roots", "discover", "--json"])
    readiness = history.read(["coordinate", "access", "--json"])
    access = readiness.get("github")
    if not isinstance(access, dict):
        raise actions.ClientGap("GitHub readiness is unavailable: " + str(readiness.get("error", "invalid response")))
    rows = [row["path"] for row in listing.get("roots", []) if isinstance(row, dict) and row.get("path")]
    registered = [row["path"] for row in listing.get("repositories", []) if isinstance(row, dict) and row.get("path")]
    found = sorted(
        {
            candidate["path"]
            for repository in discovery.get("repositories", [])
            for candidate in repository.get("candidates", [])
            if isinstance(candidate, dict) and candidate.get("path")
        }
    )
    lines = ["Configured source roots:", *(f"  {path}" for path in rows or ["(none)"]), "Discovered repositories:"]
    lines.extend(f"  {path}" for path in found or ["(none)"])
    if registered:
        lines.extend(("Standalone registrations:", *(f"  {path}" for path in registered)))
    if not discovery.get("complete", False):
        codes = sorted({row.get("code", "incomplete") for row in discovery.get("diagnostics", [])})
        lines.append("Discovery is incomplete" + (": " + ", ".join(codes) if codes else "."))
    if argument.strip():
        lines.append(
            "The supplied path was not registered. Configure roots once with `autocode registry roots add PATH` or a standalone checkout with `autocode registry repositories add PATH`."
        )
    return (
        "Repositories available for automatic discovery:\n"
        + "\n".join(lines)
        + (
            "\nGitHub authentication is available; write access is checked when selecting a repository."
            if access["configured"]
            else "\nGitHub authentication is missing: use gh auth login, GITHUB_TOKEN or GH_TOKEN before starting automatic delivery."
        )
        + "\nAutoCode discovers relevant repositories from each bug. Configure source locations once; no repository list is needed per task."
    )
