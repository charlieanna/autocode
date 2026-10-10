"""Construct the provider command and environment used by both admission and launch."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

try:
    from . import autocode_agent_env as agent_env
    from . import autocode_containment_policy as containment_policy
    from . import autocode_output_cap as output_cap
    from . import autocode_prompts as prompts
    from . import autocode_util as util
    from . import autocode_verification_copy as verification_copy
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_containment_policy as containment_policy
    import autocode_output_cap as output_cap
    import autocode_prompts as prompts
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
):
    """Return (command, environment, overrides, worker) for one stage launch.

    ``settings`` are the run's saved settings. Their --allow-uncontained-tools opt-out (#413),
    never an environment variable or model choice, launches without the kernel tool boundary:
    ``tool_commands`` (a list, or a callable returning one) is then not computed, and a
    non-planning OpenCode worker carries ``uncontained_tools`` for its stage record (stage_record).
    """
    # The incoming flag distinguishes native launch from admission/dry-run.
    # #413's OpenCode kernel opt-out must not disable Codex capture isolation.
    native_launch = enforce_tool_boundary
    uncontained = containment_policy.accepted(settings)
    if uncontained:
        enforce_tool_boundary, tool_commands = False, ()
    elif callable(tool_commands):
        tool_commands = tool_commands()
    environment = agent_env.scrubbed(os.environ)
    for name in ("AUTOCODE_VERIFICATION_COPY", "AUTOCODE_VERIFICATION_COPY_SHA256"):
        environment.pop(name, None)  # Never inherit another stage's authority.
    copy = None
    overrides = None
    prior_session = session
    if engine == "opencode":
        containment = None
        if enforce_tool_boundary and not planning and not getattr(adapter, "CONFIGURED", False):
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
        launch_kwargs = {"containment": containment} if containment else {}
        if engine == "opencode" and not getattr(adapter, "CONFIGURED", False):
            version = getattr(adapter, "transport_version", lambda _settings: None)(settings)
            if version:
                launch_kwargs["opencode_version"] = version
        try:
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
                env=environment,
                **launch_kwargs,
            )
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            if not containment:
                raise
            raise util.Paused(
                "PAUSED_TOOL_CONTAINMENT",
                "Native tool boundary was not established: "
                + str(error)
                + ". Restore the qualified setup, or resume with --allow-uncontained-tools to run "
                "this run's non-planning stages with OpenCode's own permission checks only",
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
        and (engine == "codex" or getattr(adapter, "CONFIGURED", False))
    ):
        try:
            copy = verification_copy.allocate(workspace, run_dir, source_paths=source_paths)
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            raise util.Paused("PAUSED_STALE_VALIDATION", "Verification copy was not prepared: " + str(error)) from error
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
    if prior_session and session is None:
        worker["fresh_session_reason"] = "Native tool containment requires a newly bound provider session"
    if uncontained and engine == "opencode" and not planning and not worker["configured"]:
        worker["uncontained_tools"] = True
    if environment.get("AUTOCODE_TOOL_CONTAINMENT"):
        worker["tool_containment"] = json.loads(environment["AUTOCODE_TOOL_CONTAINMENT"])
    if copy:
        worker["verification_copy"] = copy
    if engine == "opencode" and not worker["configured"]:
        # Read from the scrubbed environment: the cap the process will actually get.
        worker["output_token_cap"] = output_cap.recorded(os.environ, environment)
    return command, environment, overrides, worker


def stage_record(worker):
    """Launch facts, distinguishing capture isolation from a kernel boundary."""
    engine = worker.get("engine", "opencode")
    record = {"prompts_hash": prompts.HASH}
    if engine == "opencode" and worker.get("configured"):
        record["provider"] = worker["provider"]
    if worker.get("verification_copy"):
        checks = "Codex sandbox" if engine == "codex" else "Configured-provider permission checks"
        record.update(
            verification_copy=dict(worker["verification_copy"]),
            isolation=checks + " and workspace snapshot checks; captured commands use a source-bound copy",
        )
        return record
    if engine != "opencode":
        return record
    if worker.get("configured"):
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
    data["tool_containment"] = policy
    instruction = prompts.get("fragments/provider-launch/containment-prompt.md")
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
    return before + instruction + marker + json.dumps(data, indent=2)


def verify_containment(worker):
    copy = worker.get("verification_copy")
    if copy:
        try:
            environment = worker["environment"]
            if (
                environment.get("AUTOCODE_VERIFICATION_COPY") != copy["manifest"]
                or environment.get("AUTOCODE_VERIFICATION_COPY_SHA256") != copy["sha256"]
            ):
                raise ValueError("Verification copy launch authority changed")
            verification_copy.execution(copy["manifest"], copy["sha256"], copy["workspace"])
        except (OSError, ValueError, RuntimeError) as error:
            raise util.Paused(
                "PAUSED_STALE_VALIDATION", "Verification copy changed before launch: " + str(error)
            ) from error
    policy = worker.get("tool_containment")
    if policy:
        try:
            from . import autocode_tool_containment
        except ImportError:
            import autocode_tool_containment
        try:
            autocode_tool_containment.verify(policy)
        except (OSError, ValueError, RuntimeError) as error:
            raise util.Paused(
                "PAUSED_TOOL_CONTAINMENT", "Native tool boundary changed before launch: " + str(error)
            ) from error
