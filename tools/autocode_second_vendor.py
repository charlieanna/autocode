"""Checks by a second vendor, chosen at launch from the tools this machine has signed in.

On a run whose every stage would run on one vendor's tool (Claude Code, or Gemini CLI), a model checks
work from its own family: Opus reviews what Sonnet planned, Pro tests what Flash built. When another
vendor's tool is signed in here too, a new run moves its checking stages (the Plan Reviewer, the
Tester and the Completion Reviewer) onto that tool, exactly as ``--role-transport ROLE=TOOL
--ROLE-model MODEL`` would, so a second vendor checks the work without the person learning those flags.

``apply`` decides this once, for a new run, before the run's settings are configured, and records the
decision on ``args._second_vendor``; configuration saves it as ``settings["second_vendor"]``, where the
run's evidence and its report show it. It is decided, never asked:

- a stage keeps its own route when the person chose one: a typed ``--ROLE-model`` or
  ``--ROLE-provider``, a saved stage choice for that backend (``autocode stages``), any
  ``--role-transport``, ``--single-model`` or a replay;
- the second tool must pass its own sign-in check (no model call); when it is installed but cannot
  sign in, one line says why and the run keeps its own vendor for every stage;
- each moved stage takes the second tool's own default model for it.

The hook for another tool: a CONFIGURED provider facade that ``--role-transport`` accepts, with
``DEFAULT_MODELS`` for the checking roles, added to ``SECOND_VENDORS`` under the run tools it may check.
Its sign-in is checked by its own ``login_check(workspace)``, or, for a provider config such as the bundled
``codex.toml``, by the same checks ``--role-transport`` runs before a launch: its version and its billing
route for the moved roles' models. Codex checks a Claude Code or Gemini CLI run through that config. A run
on Codex (``--engine codex``) gets none until ``--role-transport`` binds roles of a Codex-engine run.

Layer: runtime helper over the provider facades; imports nothing above them.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from collections.abc import Callable, Mapping
from typing import Any

try:
    from . import autocode_providers as providers
except ImportError:
    import autocode_providers as providers

# The stages that check work: each runs on a route role of this name.
CHECKERS = ("plan_reviewer", "tester", "completion")
# The run's own tool -> the tools that may check it, first signed-in one taken, each (provider, executable).
SECOND_VENDORS: dict[str, tuple[tuple[str, str], ...]] = {
    "claude": (("gemini", "gemini"), ("codex", "codex")),
    "gemini": (("claude", "claude"), ("codex", "codex")),
    "codex": (("claude", "claude"), ("gemini", "gemini")),
}
LABELS = {"plan_reviewer": "Plan Reviewer", "tester": "Tester", "completion": "Completion Reviewer"}
# ``off`` keeps every run on its own tool on this host. The test suites set it (tests/__init__.py, the scenario
# driver's fake runs): their fixture tools stand in for the run's own tool, and a real Claude Code, Codex or
# Gemini CLI signed in on the developer's machine must never be handed a fixture run's checks.
OFF_SWITCH = "AUTOCODE_SECOND_VENDOR"


def kept_roles(args, stage_choices_taken=()) -> list[str]:
    """The checking roles whose route the person chose, which a second vendor never takes."""
    kept = []
    for role in CHECKERS:
        if (
            role in stage_choices_taken
            or getattr(args, f"{role}_model", None)
            or getattr(args, f"{role}_provider", None)
        ):
            kept.append(role)
    return kept


def decide(
    args,
    run_tool: str | None,
    *,
    stage_choices_taken=(),
    workspace=None,
    which: Callable[[str], str | None] = shutil.which,
    resolve=providers.resolve,
    environ: Mapping[str, str] | None = None,
) -> dict | None:
    """What a new run on ``run_tool`` moves to a second vendor, or None to keep every stage on its own.

    Returns ``{"provider", "label", "roles": {role: model}, "kept", "reason"}`` for the first installed tool
    that signs in, or ``{"provider", "label", "roles": {}, "unavailable": reason}`` for the first installed
    one whose sign-in check failed when none signs in. Launches no model.
    """
    candidates = SECOND_VENDORS.get(run_tool or "")
    if not candidates or getattr(args, "role_transport", None):
        return None
    if getattr(args, "single_model", None) or getattr(args, "replay_recording", None):
        return None
    if (os.environ if environ is None else environ).get(OFF_SWITCH, "").strip().lower() == "off":
        return None
    kept = kept_roles(args, stage_choices_taken)
    # A Codex run plans jointly only when asked (--joint-planning); without it, it has no Plan Reviewer to move.
    present = [role for role in CHECKERS if role != "plan_reviewer" or run_tool != "codex" or joint(args)]
    moving = [role for role in present if role not in kept]
    if not moving:
        return None
    unavailable: dict[str, Any] | None = None
    for name, executable in candidates:
        if not which(executable):
            continue
        facade = resolve(name)
        label = getattr(facade, "LABEL", name)
        roles = {role: facade.DEFAULT_MODELS[role] for role in moving if facade.DEFAULT_MODELS.get(role)}
        if not roles:
            continue
        try:
            signed_in(facade, roles, workspace)
        except (OSError, RuntimeError, ValueError, subprocess.TimeoutExpired) as error:
            reason = str(error).splitlines()[0] if str(error) else type(error).__name__
            unavailable = unavailable or {"provider": name, "label": label, "roles": {}, "unavailable": reason}
            continue
        own = getattr(resolve(run_tool), "LABEL", run_tool)
        return {
            "provider": name,
            "label": label,
            "roles": roles,
            "kept": kept,
            "reason": f"{label} is signed in, so a second vendor checks what {own} plans and builds",
        }
    return unavailable


def joint(args) -> bool:
    """Whether a new Codex run plans jointly, as autocode_configure decides it for a new run on that engine."""
    return bool(getattr(args, "joint_planning", False) or getattr(args, "single_model", None))


def signed_in(facade, roles, workspace) -> None:
    """Raise unless ``facade`` can run ``roles`` here; no model call.

    A built-in tool has its own ``login_check``. A provider config is checked as ``--role-transport`` checks
    it before a launch: its executable and version (``local_settings``) and the billing route of each model.
    """
    check = getattr(facade, "login_check", None)
    if callable(check):
        check(workspace)
        return
    facade.local_settings(workspace)
    facade.check_subscription_routes({role: {"model": model} for role, model in roles.items()}, workspace)


def apply(args, run_tool, *, stage_choices_taken=(), workspace=None, say=None, **checks) -> dict | None:
    """Give a new run its second-vendor checks, in place on ``args``, and say so in one line."""
    if getattr(args, "_second_vendor", None) is not None:
        return args._second_vendor
    decision = decide(args, run_tool, stage_choices_taken=stage_choices_taken, workspace=workspace, **checks)
    if decision is None:
        return None
    if decision.get("unavailable"):
        if say is not None:
            say(f"{decision['label']} is installed but cannot check this run: {decision['unavailable']}")
        return None
    args.role_transport = [f"{role}={decision['provider']}" for role in decision["roles"]]
    for role, model in decision["roles"].items():
        setattr(args, f"{role}_model", model)
    args._second_vendor = decision
    if say is not None:
        stages = ", ".join(f"{LABELS[role]} ({model})" for role, model in decision["roles"].items())
        say(f"Checks by a second vendor: {decision['label']} runs the {stages}. {decision['reason']}.")
    return decision
