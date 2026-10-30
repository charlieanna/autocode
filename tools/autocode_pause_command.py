"""Display the next scoped CLI action from the public needs projection.

This formats existing controls; it grants no permission and never changes state.
Controller tokens use the CLI's private environment channel, so copying a shown
command does not put them in the launched process's argv. ANSWER, MODEL, EFFORT and N
remain values a person must supply rather than decisions made by presentation.
"""

from __future__ import annotations

import shlex

try:
    from .autocode_control_tokens import TOKEN_ENV_VARS, private_command
except ImportError:
    from autocode_control_tokens import TOKEN_ENV_VARS, private_command


def command(need, *, run_dir=None, workspace=None, resume_flags=None, terminal=False, recovery_token=None):
    """One action, or None when no recovery is offered; all paths/values are shell quoted."""
    if not need or terminal:
        return None
    if need.get("kind") == "dependency":
        # The run's own recovery is the dependency worker's delivery, not a command on this run;
        # what a person can do is watch the producer until it reaches them or ends (#1253's wait).
        producer = need.get("producer_run")
        if not producer:
            return None
        words = ["autocode", "wait", "--run-dir", str(producer)]
        if need.get("producer_workspace"):
            words.extend(("--workspace", str(need["producer_workspace"])))
        return shlex.join(words)
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
        effort = need.get("effort") or {}
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
        elif route.get("question_id"):
            # A cross-model stop derives its question from the saved routes; no issued
            # token binds it. The answer is its own action; --resume-paused comes next.
            flags = ["--answer", f"{route['question_id']}=MODEL"]
        elif effort.get("question_id"):
            # An unsupported named effort (#2057), likewise derived from the saved routes.
            flags = ["--answer", f"{effort['question_id']}=EFFORT"]
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
    elif kind == "continue" and need.get("action"):
        flags = _action_flags(need["action"], need)
    else:
        flags = ["--no-chat"]
    private = {}
    if recovery_token is not None:
        # Use the existing private token channel; even this intermediate argv
        # carries only the selector, never the recovery token's value.
        flags.extend(("--expected-recovery-token", "-"))
        private[TOKEN_ENV_VARS["expected_recovery_token"]] = recovery_token
    return _display([*prefix, *flags], private=private)


def _action_flags(action, need):
    words = shlex.split(action)
    # Public action hints may describe the private token channel after the flags.
    if len(words) >= 2 and words[-2] == "(reads" and words[-1] in {name + ")" for name in TOKEN_ENV_VARS.values()}:
        words = words[:-2]
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
        if flag in tokens and words[index + 1] in ("TOKEN", "-"):
            if not tokens[flag]:
                return ["--no-chat"]
            words[index + 1] = tokens[flag]
    return words


def _display(words, *, private=None):
    public, extracted = private_command(words, argument_offset=1)
    private = {**extracted, **(private or {})}
    assignments = " ".join(f"{name}={shlex.quote(value)}" for name, value in private.items())
    return (assignments + " " if assignments else "") + shlex.join(public)


def recovery(need, *, run_dir=None, workspace=None, terminal=False) -> str:
    """The #909 recovery suffix: the exact copy-pasteable command with the live
    token, refreshed at render time from the caller's current need. Empty when
    no recovery exists (no need, terminal, a dependency without its producer) —
    a pause never emits a command its own CLI would reject."""
    rendered = command(need, run_dir=run_dir, workspace=workspace, terminal=terminal)
    return f" -- recovery: {rendered}" if rendered else ""


def summary(view):
    """A short terminal header; the detailed existing rendered plan follows it."""
    if not view.get("pause_category"):
        return ""
    lines = [view["pause_category_label"], f"State: {view['status']}"]
    if view.get("next_command"):
        lines.append("Next command: " + view["next_command"])
    return "\n".join(lines)


SCREEN = 20


def trailer(view, rendered):
    """Repeat the command after a presentation longer than a screen (#2301).

    The header's command scrolls off above a long brief, so the last line names it again,
    unless the presentation's own last screen already does (the approval screen, #381).
    """
    command = view.get("next_command")
    lines = rendered.splitlines()
    if not command or not view.get("pause_category") or len(lines) <= SCREEN:
        return ""
    if command in "\n".join(lines[-SCREEN:]):
        return ""
    return "Next command: " + command
