"""Whether a run's built-in OpenCode stages launch inside the kernel tool boundary (#413).

Every built-in OpenCode stage (Builder, Validator, Completion Reviewer, Resolver,
Investigator, workflow jobs, and since 2026-10-21 planning, whose shell is read-only)
launches only inside the strict tool boundary of autocode_tool_containment, which exists on macOS sandbox-exec and on Linux
bubblewrap with the conformance-tested OpenCode. A run that cannot have it is refused at setup, before
any planning stage spends money, unless the person explicitly accepted uncontained
tools with --allow-uncontained-tools.

That acceptance is the settings key ``allow_uncontained_tools`` (True), written only by
configure() below, with a ``user_events`` entry saying who and when. Readers:
autocode_provider_launch (prepare launches without the kernel boundary and stage_record
notes it on the stage record), autocode_run_view (the ``tool_containment`` field) and this
module. Nothing else sets it: no environment variable, model output or dashboard default.
Once saved it stays for every resume.

Imports nothing from the runner.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_quota_route as quota_route
    from . import autocode_stuck_job as stuck_job
    from . import autocode_tool_containment as tool_containment
except ImportError:
    import autocode_quota_route as quota_route
    import autocode_stuck_job as stuck_job
    import autocode_tool_containment as tool_containment

FLAG = "--allow-uncontained-tools"
SETTING = "allow_uncontained_tools"
EVENT = "uncontained_tools_accepted"
CONTAINED, UNCONTAINED = "contained", "uncontained_user_accepted"


def applies(settings, *, configured_tool=False) -> bool:
    """Whether the run launches built-in OpenCode stages that the kernel boundary covers.

    Configured (TOML) providers never do; a native Codex run does only through an OpenCode
    route: a role on OpenCode, or a pinned --investigator-model provider/model, whose route
    is saved outside ``roles`` until the Investigator runs.
    """
    settings = settings or {}
    if configured_tool or settings.get("provider") not in (None, "opencode"):
        return False
    return (
        settings.get("engine") == "opencode"
        or any(quota_route.engine(settings, role) == "opencode" for role in settings.get("roles") or {})
        or (stuck_job.pinned_route(settings) or {}).get("engine") == "opencode"
    )


def accepted(settings) -> bool:
    return (settings or {}).get(SETTING) is True


def mode(settings) -> str | None:
    """The status view's tool_containment: contained, uncontained_user_accepted, or None (not applicable)."""
    if not applies(settings):
        return None
    return UNCONTAINED if accepted(settings) else CONTAINED


def macos_loopback_note(settings) -> str | None:
    """Explain macOS Seatbelt loopback asymmetry when a run uses Codex or contained built-in OpenCode on macOS."""
    if not tool_containment.seatbelt_available():
        return None
    settings = settings or {}
    if settings.get("engine") == "codex":
        return tool_containment.SEATBELT_CODEX_LOOPBACK_NOTE
    if mode(settings) == CONTAINED:
        return tool_containment.SEATBELT_LOOPBACK_NOTE
    return None


loopback_bind_blocked = tool_containment.loopback_bind_blocked
seatbelt_loopback_diagnostic = tool_containment.seatbelt_loopback_diagnostic


def refusal(problem: str) -> str:
    return (
        f"Refused before any stage launched: {problem}. Built-in OpenCode stages other than planning "
        "(Builder, Validator, Completion Reviewer, Resolver, Investigator) run only inside the kernel tool "
        "boundary (macOS sandbox-exec or Linux bubblewrap) with OpenCode "
        f"{tool_containment.SUPPORTED_VERSION}. "
        f"Use that setup, or add {FLAG} to run those stages with OpenCode's own permission checks only "
        "(no kernel containment); it is saved with the run."
    )


def requirements_refusal(problem: str) -> str:
    return (
        f"Requirements-only discussion in the AutoCode controller requires verified kernel tool containment: {problem}. "
        "It cannot use uncontained tools. Select an AutoCode CLI installed outside this checkout, or use a host "
        "with the supported OpenCode or Kilo containment boundary."
    )


def requirements_boundary_problem(policy, *, workspace: Path, run_dir: Path) -> str | None:
    """Whether a prepared Requirements launch is bound to this exact read-only workspace and private run."""
    if not isinstance(policy, dict) or not policy.get("backend"):
        return "the launch has no verified kernel boundary"
    if policy.get("allow_write") is not False:
        return "the launch boundary is not read-only"
    if policy.get("workspace") != str(workspace.resolve()):
        return "the launch boundary belongs to another workspace"
    private_reads = policy.get("private_reads")
    if not isinstance(private_reads, dict) or private_reads.get("run_dir") != str(run_dir.resolve()):
        return "private-run read denial belongs to another run"
    denied = private_reads.get("denied")
    if not isinstance(denied, list) or str((workspace / ".autocode" / "runs").resolve()) not in denied:
        return "private-run read denial does not cover this workspace's run store"
    return None


def configure_requirements(state: RunState, settings, *, allow, adapter, workspace) -> None:
    """Admit the exact Requirements route only when its normal positive boundary is available."""
    if allow:
        raise ValueError("Requirements-only discussion cannot use uncontained tools")
    if accepted(settings):
        raise ValueError(requirements_refusal("the saved run accepted uncontained tools"))
    check = getattr(adapter, "containment_problem", None)
    if quota_route.engine(settings, "requirements") != "opencode" or not callable(check):
        raise ValueError(requirements_refusal("this route is not covered by the built-in containment boundary"))
    problem = check(workspace)
    if problem:
        raise ValueError(requirements_refusal(problem))


def configure(state: RunState, settings, *, allow, configured_tool, workspace, now) -> None:
    """At run setup, before any stage: save an explicit opt-out, or refuse an uncontainable run.

    ``allow`` is True only when --allow-uncontained-tools was typed for this invocation.
    Raises ValueError, leaving the run unchanged, when the boundary is unavailable and
    the run has not opted out. A run the boundary does not cover is untouched.
    """
    if not applies(settings, configured_tool=configured_tool):
        if allow:
            raise ValueError(
                f"{FLAG} applies only to built-in OpenCode runs; this run launches no kernel-contained OpenCode stage"
            )
        return
    if accepted(settings):
        return
    problem = tool_containment.unavailable(workspace)
    if not allow:
        if problem:
            raise ValueError(refusal(problem))
        return
    settings[SETTING] = True
    state.setdefault("user_events", []).append(
        {
            "kind": EVENT,
            "actor": "user_cli",
            "at": now(),
            "flag": FLAG,
            "reason": problem or "strict tool containment was available; the user opted out",
        }
    )
