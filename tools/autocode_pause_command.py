"""Display the next scoped CLI action from the public needs projection.

This formats existing controls; it grants no permission and never changes state.
Controller tokens use the CLI's private environment channel, so copying a shown
command does not put them in the launched process's argv. ANSWER, MODEL and N
remain values a person must supply rather than decisions made by presentation.
"""

from __future__ import annotations

import shlex

try:
    from .autocode_control_tokens import private_command
except ImportError:
    from autocode_control_tokens import private_command


def command(need, *, run_dir=None, workspace=None, resume_flags=None, terminal=False):
    """One action, or None when no recovery is offered; all paths/values are shell quoted."""
    if not need or terminal or need.get("kind") == "dependency":
        return None
    prefix = ["autocode"]
    if run_dir:
        prefix.extend(("--run-dir", str(run_dir)))
    else:
        prefix.extend(("--run-dir", "RUN_DIR"))
    if workspace:
        prefix.extend(("--workspace", str(workspace)))
    kind = need.get("kind")
    flags = []
    if need.get("new_run_required") or kind == "recover_source":
        flags = ["--explain"]
    elif need.get("edit_required"):
        flags = ["--edit-goal", "GOAL_FILE"]
    elif kind == "approve_plan":
        flags = ["--approve-goal", need["token"]] if need.get("token") else ["--no-chat"]
    elif kind == "review":
        if need.get("token") and need.get("criteria"):
            for criterion in need["criteria"]:
                flags.extend(("--approve-review", criterion))
            flags.extend(("--review-token", need["token"]))
        else:
            flags = ["--no-chat"]
    elif kind == "answer":
        token = need.get("resolver_token")
        route = need.get("route") or {}
        operational = need.get("resolver_scope") in ("blocker", "operational_exhaustion") and not route
        if operational and token and need.get("resolver_request_id"):
            flags = [
                "--resolver-request",
                need["resolver_request_id"],
                "--resolver-token",
                token,
                "--resolver-response",
                "provide_information",
                "--resolver-message",
                "WHAT CHANGED",
            ]
        elif token and (route.get("question_id") or need.get("questions")):
            question = route.get("question_id") or need["questions"][0].get("id")
            if question:
                flags = ["--answer", f"{question}={'MODEL' if route else 'ANSWER'}", "--resolver-token", token]
        if not flags:
            flags = ["--no-chat"]  # No issued token: relaunch to publish the current request.
    elif kind == "retry_job" and not need.get("job_retry_token"):
        flags = ["--explain"]
    elif kind in ("resume", "planning_budget", "retry_job"):
        action = need.get("action") or resume_flags
        if not action and kind == "planning_budget":
            action = "--resume-paused --planning-review-call-limit N"
        if not action and kind == "retry_job":
            action = "--resume-paused --retry-failed-stage --job-retry-token TOKEN"
        flags = _action_flags(action or "--resume-paused", need)
    else:
        flags = ["--no-chat"]
    return _display([*prefix, *flags])


def _action_flags(action, need):
    words = shlex.split(action)
    # Abandonment changes the saved frontier; show that first action only.
    if "then" in words:
        words = words[: words.index("then")]
    tokens = {
        "--job-retry-token": need.get("job_retry_token"),
        "--resolver-token": need.get("resolver_token"),
        "--review-token": need.get("token"),
        "--approve-goal": need.get("token"),
    }
    for index, flag in enumerate(words[:-1]):
        if flag in tokens and words[index + 1] == "TOKEN":
            if not tokens[flag]:
                return ["--no-chat"]
            words[index + 1] = tokens[flag]
    return words


def _display(words):
    public, private = private_command(words, argument_offset=1)
    assignments = " ".join(f"{name}={shlex.quote(value)}" for name, value in private.items())
    return (assignments + " " if assignments else "") + shlex.join(public)


def summary(view):
    """A short terminal header; the detailed existing rendered plan follows it."""
    if not view.get("pause_category"):
        return ""
    lines = [view["pause_category_label"], f"State: {view['status']}"]
    if view.get("next_command"):
        lines.append("Next command: " + view["next_command"])
    return "\n".join(lines)
