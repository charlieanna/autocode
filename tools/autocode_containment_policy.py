"""Whether a run's built-in OpenCode stages launch inside the kernel tool boundary (#413).

Every built-in OpenCode stage that is not planning (Builder, Validator, Completion
Reviewer, Resolver, workflow jobs) launches only inside the strict tool boundary of
autocode_tool_containment, which exists only on macOS sandbox-exec with the
conformance-tested OpenCode. A run that cannot have it is refused at setup, before
any planning stage spends money, unless the person explicitly accepted uncontained
tools with --allow-uncontained-tools.

That acceptance is the settings key ``allow_uncontained_tools`` (True), written only by
configure() below, with a ``user_events`` entry saying who and when. Readers:
autocode.run_role (launch without the kernel boundary and note it on the stage record),
autocode_run_view (the ``tool_containment`` field) and this module. Nothing else sets
it: no environment variable, model output or dashboard default. Once saved it stays
for every resume.

Imports nothing from the runner.
"""
from __future__ import annotations

try:
    from . import autocode_tool_containment as tool_containment, autocode_quota_route as quota_route
except ImportError:
    import autocode_tool_containment as tool_containment, autocode_quota_route as quota_route

FLAG = "--allow-uncontained-tools"
SETTING = "allow_uncontained_tools"
EVENT = "uncontained_tools_accepted"
CONTAINED, UNCONTAINED = "contained", "uncontained_user_accepted"


def applies(settings, *, configured_tool=False) -> bool:
    """Whether the run launches built-in OpenCode stages that the kernel boundary covers.

    Configured (TOML) providers and the native Codex engine never do.
    """
    settings = settings or {}
    if configured_tool or settings.get("provider") not in (None, "opencode"):
        return False
    return settings.get("engine") == "opencode" or any(
        quota_route.engine(settings, role) == "opencode" for role in settings.get("roles") or {})


def accepted(settings) -> bool:
    return (settings or {}).get(SETTING) is True


def mode(settings) -> str | None:
    """The status view's tool_containment: contained, uncontained_user_accepted, or None (not applicable)."""
    if not applies(settings):
        return None
    return UNCONTAINED if accepted(settings) else CONTAINED


def refusal(problem: str) -> str:
    return (f"Refused before any stage launched: {problem}. Built-in OpenCode stages other than planning "
            "(Builder, Validator, Completion Reviewer, Resolver) run only inside the kernel tool boundary, "
            f"qualified on macOS sandbox-exec with OpenCode {tool_containment.SUPPORTED_VERSION}. Use that setup, "
            f"or add {FLAG} to run those stages with OpenCode's own permission checks only (no kernel "
            "containment); it is saved with the run.")


def configure(state, settings, *, allow, configured_tool, workspace, now) -> None:
    """At run setup, before any stage: save an explicit opt-out, or refuse an uncontainable run.

    ``allow`` is True only when --allow-uncontained-tools was typed for this invocation.
    Raises ValueError, leaving the run unchanged, when the boundary is unavailable and
    the run has not opted out. A run the boundary does not cover is untouched.
    """
    if not applies(settings, configured_tool=configured_tool):
        if allow:
            raise ValueError(f"{FLAG} applies only to built-in OpenCode runs; this run launches no "
                             "kernel-contained OpenCode stage")
        return
    if accepted(settings):
        return
    problem = tool_containment.unavailable(workspace)
    if not allow:
        if problem:
            raise ValueError(refusal(problem))
        return
    settings[SETTING] = True
    state.setdefault("user_events", []).append({
        "kind": EVENT, "actor": "user_cli", "at": now(), "flag": FLAG,
        "reason": problem or "strict tool containment was available; the user opted out"})
