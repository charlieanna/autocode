"""What no stage does outside its workspace: change GitHub or push.

Publishing is the owner's (AGENTS.md): issue labels and comments, pull requests, reviews, releases and
pushes happen through the person or AutoCode's own delivery, never from inside a stage. A stage may still
read an issue the person names (``gh issue view``). A run on AutoCode's own repository showed why this
needs saying: its Requirements stage followed the repository's "Taking an issue" rules for agents, added
the ``in progress`` label to an issue and posted a comment on it before any plan existed.

This module is the one list. Each engine whose shell permissions AutoCode writes denies these commands
before they run (Claude Code's disallowed tools; OpenCode's and Kilo's bash patterns), and every stage's
prompt says so, so a model neither tries them nor takes a refusal as a blocker to report. Codex's own
sandbox has no network. Pure functions over values; the rule's text is a prompt resource.
"""

from __future__ import annotations

try:
    from . import autocode_prompts as prompts
except ImportError:
    import autocode_prompts as prompts

# Command prefixes that change something outside the workspace. Reads (gh issue view/list, gh pr view/diff,
# git fetch) stay allowed. ``gh api`` reads as often as it writes, so the prompt rule covers its writes.
COMMANDS = (
    "git push",
    *(
        f"gh issue {verb}"
        for verb in (
            "create",
            "edit",
            "comment",
            "close",
            "reopen",
            "delete",
            "lock",
            "unlock",
            "pin",
            "unpin",
            "transfer",
            "develop",
        )
    ),
    *(
        f"gh pr {verb}"
        for verb in (
            "create",
            "edit",
            "comment",
            "review",
            "merge",
            "close",
            "reopen",
            "ready",
            "lock",
            "unlock",
            "update-branch",
        )
    ),
    *(f"gh label {verb}" for verb in ("create", "edit", "delete", "clone")),
    *(f"gh release {verb}" for verb in ("create", "edit", "delete", "upload")),
    *(f"gh repo {verb}" for verb in ("create", "edit", "delete", "fork", "rename", "archive", "sync")),
    "gh gist create",
    "gh workflow run",
    "gh run rerun",
    "gh run cancel",
    "gh secret set",
    "gh variable set",
)

RULE = "\n" + prompts.get("fragments/support/nothing-outside-workspace.md")
MARKER = "\nCURRENT HANDOFF DATA\n"


def claude_rules() -> tuple[str, ...]:
    """Claude Code ``--disallowedTools`` rules for each command and anything after it."""
    return tuple(f"Bash({command}:*)" for command in COMMANDS)


def bash_permission(existing=None) -> dict:
    """An OpenCode or Kilo ``bash`` permission that denies each command, never allowing more than ``existing``.

    ``existing`` is the bash permission the launch already has: an action ("allow", "ask" or "deny"), an
    ordered pattern object, or None when the agent inherits one. Its rules keep their order and the denials
    follow them, so they are the last to match; with no bash permission of its own the object holds only the
    denials, and every other command falls through to the agent's wider rule as before.
    """
    if isinstance(existing, str):
        patterns = {"*": existing}
    elif isinstance(existing, dict):
        patterns = dict(existing)
    elif existing is None:
        patterns = {}
    else:
        raise ValueError("A bash permission must be an action or a pattern object")
    if set(patterns.values()) == {"deny"} and patterns.get("*") == "deny":
        return patterns  # no command can run, so there is nothing to deny
    for command in COMMANDS:
        for pattern in (command, command + " *"):
            patterns.pop(pattern, None)  # re-inserted last: a native permission's last match wins
            patterns[pattern] = "deny"
    return patterns


def with_rule(prompt: str) -> str:
    """The stage prompt with the rule before its handoff data, or after the text when it has none."""
    if RULE in prompt:
        return prompt
    before, marker, after = prompt.partition(MARKER)
    return before + RULE + marker + after if marker else prompt + RULE
