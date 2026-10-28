"""Construct the provider command and environment used by both admission and launch."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .autocode_run_state import RunState

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from . import autocode_access_evidence as access_evidence
    from . import autocode_agent_env as agent_env
    from . import autocode_containment_policy as containment_policy
    from . import autocode_native_locators as native_locators
    from . import autocode_native_role_isolation as native_isolation
    from . import autocode_output_cap as output_cap
    from . import autocode_prompts as prompts
    from . import autocode_route_evidence as route_evidence
    from . import autocode_storage_errors as storage_errors
    from . import autocode_util as util
    from . import autocode_verification_copy as verification_copy
except ImportError:
    import autocode_access_evidence as access_evidence
    import autocode_agent_env as agent_env
    import autocode_containment_policy as containment_policy
    import autocode_native_locators as native_locators
    import autocode_native_role_isolation as native_isolation
    import autocode_output_cap as output_cap
    import autocode_prompts as prompts
    import autocode_route_evidence as route_evidence
    import autocode_storage_errors as storage_errors
    import autocode_util as util
    import autocode_verification_copy as verification_copy


def prepare(
    *,
    engine,
    adapter,
    role,
    route_role,
    workspace,
    run_dir,
    session,
    model,
    effort,
    allow_write,
    planning,
    report,
    schema,
    prompt_file,
    sandbox,
    transport_args,
    chatgpt,
    provider,
    enforce_tool_boundary=True,
    tool_commands=(),
    source_paths=(),
    settings=None,
    native_role_isolation=None,
    native_environment=None,
    native_image_preparation=None,
    replay_context=None,
    planning_report_recovery=False,
):
    """Return (command, environment, overrides, worker) for one stage launch.

    ``settings`` are the run's saved settings. Their --allow-uncontained-tools opt-out (#413),
    never an environment variable or model choice, launches without the kernel tool boundary:
    ``tool_commands`` (a list, or a callable returning one) is then not computed, and a
    built-in OpenCode worker, a Planner's included, carries ``uncontained_tools`` for its stage record
    (stage_record). A planning stage gets the same shell as a read-only checker; its report recovery gets no tools.
    Explicit native role isolation always requires its kernel tool boundary; the
    saved uncontained-tools opt-out cannot weaken that owner.
    """
    # The incoming flag distinguishes native launch from admission/dry-run.
    # #413's OpenCode kernel opt-out must not disable Codex capture isolation.
    if (settings or {}).get("replay_recording"):
        if any(owner is not None for owner in (native_role_isolation, native_environment, native_image_preparation)):
            raise util.Paused(
                "PAUSED_TOOL_CONTAINMENT", "Recorded replay cannot replace the original fresh native owner"
            )
        try:
            from . import autocode_replay_transport as replay_transport
        except ImportError:
            import autocode_replay_transport as replay_transport
        return replay_transport.prepare(
            settings=settings,
            engine=engine,
            adapter=adapter,
            role=role,
            route_role=route_role,
            workspace=workspace,
            run_dir=run_dir,
            session=session,
            model=model,
            effort=effort,
            report=report,
            schema=schema,
            prompt_file=prompt_file,
            allow_write=allow_write,
            context=replay_context,
            provider=provider,
        )
    native_launch = enforce_tool_boundary
    try:
        from .providers.kilocode import KiloEffortError, KiloProvider
    except ImportError:
        from providers.kilocode import KiloEffortError, KiloProvider
    kilo_owner = type(native_role_isolation) is native_isolation.KiloRoleIsolation and type(adapter) is KiloProvider
    # A planning stage gets the same shell as a read-only checker (owner decision 2026-10-21): Planners compute the
    # exact literals the example rule asks for instead of working them out by hand. Report recovery keeps no tools.
    planner_shell = planning and not planning_report_recovery and not allow_write
    kilo_readonly = (
        type(adapter) is KiloProvider and not allow_write and (not planning or planner_shell) and not kilo_owner
    )
    # macOS has no kernel shell boundary (owner decision 2026-10-21, #1766; as #1195 for every provider there): a
    # read-only Kilo checker gets Kilo's own policy instead, read tools and its shell with edits denied. A source
    # write it still makes is put back after the stage (autocode_stray_writes), as for any read-only stage.
    kilo_portable = kilo_readonly and sys.platform == "darwin"
    kilo_readonly = kilo_readonly and not kilo_portable
    uncontained = containment_policy.accepted(settings)
    if native_image_preparation is not None:
        try:
            from . import autocode_native_calls as calls
            from . import autocode_native_image as images
        except ImportError:
            import autocode_native_calls as calls
            import autocode_native_image as images
        if (
            type(native_image_preparation) is not images.NativeImagePreparation
            or native_image_preparation.isolation is not native_role_isolation
            or engine != "opencode"
            or getattr(adapter, "CONFIGURED", False)
            and not kilo_owner
            or route_role != "sol"
            or planning
            or allow_write
            or session
            or uncontained
        ):
            raise util.Paused(
                "PAUSED_VISUAL_EVIDENCE", "Native image preparation needs the original fresh readonly Sol owner"
            )
        native_image_preparation.require_caller(calls.PlanningCalls)
    if uncontained:
        enforce_tool_boundary, tool_commands = False, ()
    if native_role_isolation is not None and planning and allow_write:
        raise util.Paused("PAUSED_TOOL_CONTAINMENT", "Native isolated planning must remain readonly")
    if native_role_isolation is not None and (
        engine != "opencode"
        or getattr(adapter, "CONFIGURED", False)
        and not kilo_owner
        or type(native_role_isolation) is not native_isolation.NativeRoleIsolation
        and not kilo_owner
        or not enforce_tool_boundary
    ):
        raise util.Paused(
            "PAUSED_TOOL_CONTAINMENT", "Native role isolation requires the exact builtin CODE owner and tool boundary"
        )
    if not uncontained and callable(tool_commands):
        tool_commands = tool_commands()
    if native_environment is not None and native_role_isolation is None:
        raise TypeError("Explicit native environment requires the original isolation owner")
    environment = agent_env.scrubbed(os.environ if native_environment is None else native_environment)
    for name in ("AUTOCODE_VERIFICATION_COPY", "AUTOCODE_VERIFICATION_COPY_SHA256"):
        environment.pop(name, None)  # Never inherit another stage's authority.
    copy = None
    overrides = None
    prior_session = session
    fresh_reason = None
    if engine == "opencode":
        if session and not planning and getattr(adapter, "RESUME_ACROSS_STAGES", True) is False:
            # A facade that never resumes across stages (#717); a same-stage correction is planning.
            session = None
            fresh_reason = "This provider starts every stage in a fresh session; only a same-stage correction resumes"
        containment = None
        if (
            enforce_tool_boundary
            and (not planning or planner_shell or native_role_isolation is not None)
            and (not getattr(adapter, "CONFIGURED", False) or kilo_owner or kilo_readonly)
        ):
            session = None
            runtime = Path(__file__).resolve().parent
            roots = {runtime, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
            developer_tools = Path("/Library/Developer/CommandLineTools")
            if developer_tools.is_dir():
                roots.add(developer_tools.resolve())
            containment = {
                "source_paths": list(source_paths),
                "tool_commands": list(tool_commands),
                "read_roots": [str(path) for path in sorted(roots)],
                "protected_paths": [str(Path(run_dir).resolve()), str(runtime)],
            }
            if planning and not kilo_owner:
                # A readonly planning shell coordinates through the handoff, not private run
                # state: no run's state, events or receipts are readable, only this run's
                # planning/ artifacts (#1250). Native Kilo isolation keeps its own profile.
                containment["private_run"] = str(Path(run_dir).resolve())
        launch_kwargs = {"containment": containment} if containment else {}
        launch_kwargs["env"] = environment
        if planner_shell and uncontained and not getattr(adapter, "CONFIGURED", False):
            # The person accepted uncontained tools (#413): the planner's shell runs under OpenCode's own
            # permissions with edits denied, like every other stage of this run.
            launch_kwargs["planning_shell"] = True
        if type(adapter) is KiloProvider and not kilo_owner and planning_report_recovery:
            launch_kwargs["report_recovery"] = True
        if kilo_portable and enforce_tool_boundary:
            launch_kwargs["portable_shell"] = True
            print(
                f"{route_role}: Kilo checks run with Kilo's own permissions on macOS (no kernel sandbox there); "
                "edits are denied and any source change is put back",
                file=sys.stderr,
            )
        if native_image_preparation is not None:
            launch_kwargs["image_preparation"] = native_image_preparation
        if engine == "opencode" and not getattr(adapter, "CONFIGURED", False):
            version = getattr(adapter, "transport_version", lambda _settings: None)(settings)
            if version:
                launch_kwargs["opencode_version"] = version
        try:
            if native_role_isolation is not None:
                containment.update(native_role_isolation.containment_request(allow_write=allow_write))
            command, child, overrides = adapter.launch(
                route_role,
                workspace,
                run_dir,
                session,
                model,
                effort,
                allow_write,
                planning=planning,
                report=report,
                schema=schema,
                prompt_file=prompt_file,
                sandbox=sandbox,
                **launch_kwargs,
                **({"isolation": native_role_isolation} if native_role_isolation is not None else {}),
            )
        except KiloEffortError as error:
            raise util.Paused("PAUSED_TRANSPORT_UNVERIFIED", str(error)) from error
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            if not containment:
                if type(adapter) is KiloProvider and (planning or not allow_write):
                    raise util.Paused(
                        "PAUSED_TOOL_CONTAINMENT", "Kilo readonly tool policy was not established: " + str(error)
                    ) from error
                raise
            try:
                from . import autocode_tool_containment as tool_containment
            except ImportError:
                import autocode_tool_containment as tool_containment
            recovery = (
                # These errors name their own remedy; reinstalling the sandbox does not clear a policy refusal.
                ""
                if isinstance(error, (*tool_containment.STORE_ERRORS, tool_containment.NativePermissionDenied))
                else ". Restore the qualified native setup; native role isolation requires its kernel tool boundary"
                if native_role_isolation is not None
                else ". Restore qualified Kilo 7.8.3 and its kernel shell setup; readonly Kilo cannot waive this boundary"
                if kilo_readonly
                else ". Restore the qualified setup, or resume with --allow-uncontained-tools to run "
                "this run's stages with OpenCode's own permission checks only"
            )
            raise util.Paused(
                "PAUSED_TOOL_CONTAINMENT", "Native tool boundary was not established: " + str(error) + recovery
            ) from error
        if child:
            environment = agent_env.scrubbed(child)
            if not containment or allow_write:
                for name in ("AUTOCODE_VERIFICATION_COPY", "AUTOCODE_VERIFICATION_COPY_SHA256"):
                    environment.pop(name, None)
    elif engine == "codex":
        command = ["codex", "exec", "-C", str(workspace), "--sandbox", sandbox, *transport_args]
        if chatgpt:
            command += ["-c", 'forced_login_method="chatgpt"']
        if effort:
            command += ["-c", f'model_reasoning_effort="{effort}"']
        if provider:
            command += ["-c", f'model_provider="{provider}"']
        if session:
            command += ["resume", session]
        command += ["-", "--json", "--output-schema", str(schema), "-o", str(report)]
        if model:
            command += ["--model", model]
    else:
        raise RuntimeError(
            f"engine {engine!r} is not bundled in this checkout; providers live in "
            "~/.config/autocode/providers/ and run with --provider"
        )
    # Judging stages use workspace-write for evidence, but may not change
    # source. Scratch-owning jobs retain their existing capture CWD contract.
    # Built-in contained OpenCode already supplies its own native copy.
    if (
        native_launch
        and not planning
        and not allow_write
        and sandbox == "workspace-write"
        and not kilo_owner
        and not (kilo_readonly and enforce_tool_boundary)
        and (engine == "codex" or getattr(adapter, "CONFIGURED", False))
    ):
        try:
            copy = verification_copy.allocate(workspace, run_dir, source_paths=source_paths)
        except subprocess.TimeoutExpired as error:
            raise util.Paused(
                "PAUSED_VERIFICATION_PREPARATION",
                "Verification copy preparation timed out before provider launch; no model was started. " + str(error),
            ) from error
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise util.Paused(
                "PAUSED_STALE_VALIDATION",
                "Verification copy was not prepared: "
                + storage_errors.describe(error, operation="preparing a verification copy", location=run_dir),
            ) from error
        environment.update(
            AUTOCODE_VERIFICATION_COPY=copy["manifest"], AUTOCODE_VERIFICATION_COPY_SHA256=copy["sha256"]
        )
    worker = {
        "engine": engine,
        "provider": getattr(adapter, "NAME", "opencode") if engine == "opencode" else provider or engine,
        "configured": bool(getattr(adapter, "CONFIGURED", False)),
        "role": route_role,
        "sandbox": sandbox,
        "planning": planning,
        "model": model,
        "command": command,
        "environment": environment,
        "provider_session": session,
    }
    if type(adapter) is KiloProvider and (planning or not allow_write) and not kilo_owner:
        worker["provider_session"] = None
        session = None
        fresh_reason = "Kilo restricted stages require a fresh session without prior tool grants"
        worker["kilo_readonly_tools"] = True
        worker["planning_report_recovery"] = planning_report_recovery
        worker["kilo_portable_shell"] = bool(kilo_portable and enforce_tool_boundary)
    if prior_session and session is None:
        worker["fresh_session_reason"] = (
            fresh_reason or "Native tool containment requires a newly bound provider session"
        )
    if uncontained and engine == "opencode" and (not planning or planner_shell) and not worker["configured"]:
        worker["uncontained_tools"] = True
    if environment.get("AUTOCODE_TOOL_CONTAINMENT"):
        worker["tool_containment"] = json.loads(environment["AUTOCODE_TOOL_CONTAINMENT"])
    if copy:
        worker["verification_copy"] = copy
    if native_role_isolation is not None:
        descriptor = native_role_isolation.environment_descriptor(environment)
        worker["native_role_isolation"] = descriptor
        worker["tool_containment"] = native_locators.expand_boundary(
            environment["AUTOCODE_TOOL_CONTAINMENT"], descriptor
        )
    if native_image_preparation is not None:
        worker["visual_runtime"] = native_image_preparation.context()
    if engine == "opencode" and (not worker["configured"] or type(adapter) is KiloProvider):
        # Read from the scrubbed environment: the cap the process will actually get.
        variable = (
            output_cap.KILO_VARIABLE
            if type(adapter) is KiloProvider and native_role_isolation is None
            else output_cap.VARIABLE
        )
        worker["output_token_cap"] = output_cap.recorded(os.environ, environment, variable)
    if engine == "opencode":
        worker["access_preflight_identity"] = access_evidence.launch_binding(
            settings, route_role, adapter, model, effort
        )
    worker["execution_identity"] = route_evidence.launch_identity(
        role=route_role,
        engine=engine,
        provider=worker["provider"],
        model=model,
        effort=effort,
        runtime_commit=util.runner_identity().get("commit"),
        settings=settings,
        endpoint=(overrides or {}).get("endpoint_identity"),
    )
    return command, environment, overrides, worker


def stage_record(worker):
    """Launch facts, distinguishing capture isolation from a kernel boundary."""
    if worker.get("replay"):
        return {
            "prompts_hash": prompts.HASH,
            "replay": dict(worker["replay"]),
            "output_mode": worker["output_mode"],
            "supports_sessions": worker["supports_sessions"],
            "isolation": "Runner-supervised recorded event emitter; no provider or model call",
        }
    engine = worker.get("engine", "opencode")
    record = {"prompts_hash": prompts.HASH}
    if route_evidence.valid_execution_identity(worker.get("execution_identity")):
        from copy import deepcopy

        record["execution_identity"] = deepcopy(worker["execution_identity"])
    if worker.get("access_preflight_identity"):
        from copy import deepcopy

        record["access_preflight_identity"] = deepcopy(worker["access_preflight_identity"])
    if engine == "opencode" and worker.get("configured"):
        record["provider"] = worker["provider"]
        if worker.get("fresh_session_reason"):
            record["fresh_session_reason"] = worker["fresh_session_reason"]
    if worker.get("output_token_cap") is not None:
        record["output_token_cap"] = worker["output_token_cap"]
    if worker.get("verification_copy"):
        checks = "Codex sandbox" if engine == "codex" else "Configured-provider permission checks"
        record.update(
            verification_copy=dict(worker["verification_copy"]),
            isolation=checks + " and workspace snapshot checks; captured commands use a source-bound copy",
        )
        return record
    if engine != "opencode":
        return record
    if (worker.get("native_role_isolation") or {}).get("backend") == "kilocode":
        return {
            **record,
            "isolation": "Kernel-constrained Kilo client and shell with original parent inference relay",
            "tool_containment": worker.get("tool_containment"),
            "output_token_cap": worker.get("output_token_cap"),
        }
    if worker.get("configured"):
        if worker.get("kilo_readonly_tools"):
            return {
                **record,
                "isolation": "Kilo report-only recovery; all tools disabled"
                if worker.get("planning_report_recovery")
                else "Kernel-constrained Kilo shell; other tools disabled"
                if worker.get("tool_containment")
                else "Kilo read tools and shell with edits denied; no kernel sandbox on macOS"
                if worker.get("kilo_portable_shell")
                else "Kilo read-tools-only permissions; no shell or OS sandbox",
                "tool_containment": worker.get("tool_containment"),
            }
        return {**record, "isolation": "Config-tool sandbox flag and workspace snapshot checks"}
    record.update(
        {
            "isolation": "Kernel-constrained native shell; other tools disabled"
            if worker.get("tool_containment")
            else "OpenCode tool permissions and workspace snapshot checks; no OS sandbox",
            "tool_containment": worker.get("tool_containment"),
            "output_token_cap": worker.get("output_token_cap"),
        }
    )
    if worker.get("uncontained_tools"):
        record.update(
            uncontained_tools=True,
            isolation=record["isolation"] + "; kernel containment waived by " + containment_policy.FLAG,
        )
    return record


def render_native_image_prompt(
    prompt, *, schema, events, adapter, boundary, regression_proof_current, regression_handoff
):
    prompt = adapter.prompt_for_schema(prompt, schema, events)
    return containment_prompt(
        prompt,
        {"tool_containment": boundary},
        stage="sol",
        regression_proof_current=regression_proof_current,
        regression_handoff=regression_handoff,
    )


def containment_prompt(prompt, worker, *, stage=None, regression_proof_current=False, regression_handoff=None):
    """Advertise only the current stage's permitted scratch area, not broader write access."""
    policy = worker.get("tool_containment")
    if not policy:
        return prompt
    # The kernel boundary lets the stage write only its scratch, so every capture example names it:
    # the provider contract's and COMMON's alike, never a location the stage could not write.
    for example in (
        "--output .autocode/evidence/<unique-name>.json",
        "--output <run-directory>/evidence/<unique-name>.json",
    ):
        prompt = prompt.replace(example, "--output " + str(Path(policy["scratch"]) / "evidence-<unique-name>.json"))
    marker = "\nCURRENT HANDOFF DATA\n"
    before, separator, after = prompt.rpartition(marker)
    if not separator:
        raise ValueError("Contained tool launch requires a structured handoff")
    data = json.loads(after)
    # The model needs the boundary and original manifest locator, not a second
    # copy of thousands of SDK member hashes. Full CODE-owned launch records
    # and verification keep the original inventory; no task input is omitted.
    data["tool_containment"] = native_locators.boundary_value(policy)
    # Planning stays compact (#1635); execution must not inflate a packet
    # that the context and provider serializers already rendered compactly.
    compact = data.get("joint_planning") is True or "\n" not in after.strip()
    instruction = prompts.get("fragments/provider-launch/containment-prompt.md")
    if policy.get("private_reads"):
        instruction += prompts.get("fragments/provider-launch/containment-prompt-run-store.md")
    if stage == "sol":
        # Bind the entire presented projection, including JSON types, not only its source revision.
        regression_proof_current = (
            regression_proof_current
            and regression_handoff is not None
            and json.dumps(data.get("regression_proof"), sort_keys=True)
            == json.dumps(regression_handoff, sort_keys=True)
        )
        data["runner_regression_proof_current"] = regression_proof_current
        instruction += prompts.get("fragments/provider-launch/containment-prompt-04.md")
        instruction += (
            prompts.get("fragments/provider-launch/containment-prompt-06.md")
            if regression_proof_current
            else prompts.get("fragments/provider-launch/containment-prompt-07.md")
        )
    return (
        before
        + instruction
        + marker
        + (json.dumps(data, separators=(",", ":")) if compact else json.dumps(data, indent=2))
    )


def verify_containment(
    worker, *, state: RunState | None = None, native_role_isolation=None, command=None, environment=None
):
    copy = worker.get("verification_copy")
    if copy:
        try:
            copy_environment = worker["environment"]
            if (
                copy_environment.get("AUTOCODE_VERIFICATION_COPY") != copy["manifest"]
                or copy_environment.get("AUTOCODE_VERIFICATION_COPY_SHA256") != copy["sha256"]
            ):
                raise ValueError("Verification copy launch authority changed")
            verification_copy.execution(copy["manifest"], copy["sha256"], copy["workspace"])
        except (OSError, ValueError, RuntimeError) as error:
            raise util.Paused(
                "PAUSED_STALE_VALIDATION", "Verification copy changed before launch: " + str(error)
            ) from error
    policy = worker.get("tool_containment")
    child = worker.get("environment") or {}
    isolated = (
        native_role_isolation is not None
        or worker.get("native_role_isolation") is not None
        or (isinstance(policy, dict) and policy.get("native_role_isolation") is not None)
        or (isinstance(child, dict) and child.get("AUTOCODE_NATIVE_ROLE_ISOLATION") is not None)
    )
    if isolated and not policy:
        raise util.Paused("PAUSED_TOOL_CONTAINMENT", "Native isolated launch is missing its mandatory tool boundary")
    if policy:
        try:
            from . import autocode_tool_containment
        except ImportError:
            import autocode_tool_containment
        try:
            if type(native_role_isolation) is native_isolation.KiloRoleIsolation:
                native_role_isolation.verify_boundary(policy)
            else:
                autocode_tool_containment.verify(policy)
            if isolated:
                if (
                    type(native_role_isolation)
                    not in (native_isolation.NativeRoleIsolation, native_isolation.KiloRoleIsolation)
                    or state is None
                ):
                    raise ValueError("Missing exact finalized native role isolation owner")
                actual_command = worker.get("command") if command is None else command
                actual_environment = worker.get("environment") if environment is None else environment
                if (
                    not isinstance(actual_command, list)
                    or not actual_command
                    or any(not isinstance(arg, str) or not arg for arg in actual_command)
                    or not isinstance(actual_environment, dict)
                    or any(
                        not isinstance(key, str) or not isinstance(value, str)
                        for key, value in actual_environment.items()
                    )
                ):
                    raise ValueError("Missing or malformed final native command/environment")
                descriptor = native_role_isolation.verify_launch(
                    state=state, command=actual_command, environment=actual_environment, boundary=policy
                )
                if (
                    descriptor != worker.get("native_role_isolation")
                    or policy.get("native_role_isolation") != descriptor
                ):
                    raise ValueError("Native role isolation locator differs from its CODE owner")
        except (OSError, ValueError, RuntimeError) as error:
            raise util.Paused(
                "PAUSED_TOOL_CONTAINMENT", "Native tool boundary changed before launch: " + str(error)
            ) from error
