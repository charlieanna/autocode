"""Shared shell-boundary selection and model-free selected-tool admission.

Early discovery grants no boundary or reusable readiness. The real provider
launch always rediscovers its tools and performs every native startup check.
"""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

try:
    from . import autocode_agent_env as agent_env
    from . import autocode_containment_policy as containment_policy
    from . import autocode_native_role_isolation as native_isolation
    from . import autocode_stage_roles as stage_roles
    from . import autocode_tool_containment as containment
    from . import autocode_toolchain as toolchain
    from . import autocode_util as util
    from .providers.kilocode import KiloProvider
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_containment_policy as containment_policy
    import autocode_native_role_isolation as native_isolation
    import autocode_stage_roles as stage_roles
    import autocode_tool_containment as containment
    import autocode_toolchain as toolchain
    import autocode_util as util
    from providers.kilocode import KiloProvider


@dataclass(frozen=True)
class ShellPolicy:
    planner_shell: bool
    kilo_owner: bool
    kilo_readonly: bool
    kilo_portable: bool
    uncontained: bool
    kernel_shell: bool


def shell_policy(*, engine, adapter, planning, allow_write, settings, enforce=True, isolation=None, recovery=False):
    """The actual launch's shell selection, shared with early admission."""
    kilo_owner = type(isolation) is native_isolation.KiloRoleIsolation and type(adapter) is KiloProvider
    planner_shell = planning and not recovery and not allow_write
    kilo_readonly = (
        type(adapter) is KiloProvider and not allow_write and (not planning or planner_shell) and not kilo_owner
    )
    kilo_portable = kilo_readonly and sys.platform == "darwin"
    kilo_readonly = kilo_readonly and not kilo_portable
    uncontained = containment_policy.accepted(settings)
    kernel_shell = (
        engine == "opencode"
        and enforce
        and not uncontained
        and (not planning or planner_shell or isolation is not None)
        and (not getattr(adapter, "CONFIGURED", False) or kilo_owner or kilo_readonly)
    )
    return ShellPolicy(planner_shell, kilo_owner, kilo_readonly, kilo_portable, uncontained, kernel_shell)


def environment(source=None):
    """The ordinary stage mapping, without inherited verification authority."""
    result = agent_env.scrubbed(os.environ if source is None else source)
    for name in ("AUTOCODE_VERIFICATION_COPY", "AUTOCODE_VERIFICATION_COPY_SHA256"):
        result.pop(name, None)
    return result


def failure(error, *, kilo_readonly=False, isolation=None):
    recovery = (
        ""
        if isinstance(error, (*containment.STORE_ERRORS, containment.NativePermissionDenied))
        else ". Restore the qualified native setup; native role isolation requires its kernel tool boundary"
        if isolation is not None
        else ". Restore qualified Kilo 7.8.3 and its kernel shell setup; readonly Kilo cannot waive this boundary"
        if kilo_readonly
        else ". Restore the qualified setup, or resume with --allow-uncontained-tools to run "
        "this run's stages with OpenCode's own permission checks only"
    )
    return util.Paused("PAUSED_TOOL_CONTAINMENT", "Native tool boundary was not established: " + str(error) + recovery)


def requirements(adapter, state: RunState, workspace):
    """Discover only an imminent ordinary Linux Requirements kernel-shell launch.

    The caller proves freshness using the exact Requirements access binding.
    Native ownership is excluded by model preflight's existing admission.
    """
    settings = state.get("settings") or {}
    if sys.platform != "linux" or settings.get("replay_recording"):
        return
    policy = shell_policy(
        engine=stage_roles.engine_for(settings, "requirements"),
        adapter=adapter,
        planning=stage_roles.is_planning(state, "requirements_gather"),
        allow_write=False,
        settings=settings,
    )
    if not policy.kernel_shell or not policy.planner_shell:
        return
    try:
        toolchain.discover(workspace, (), environment())
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        raise failure(error, kilo_readonly=policy.kilo_readonly) from error
