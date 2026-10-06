"""Construct the provider command and environment used by both admission and launch."""
from __future__ import annotations

import os
import json
from pathlib import Path
import subprocess
import sys
try:
    from . import autocode_agent_env as agent_env, autocode_output_cap as output_cap, autocode_util as util
except ImportError:
    import autocode_agent_env as agent_env, autocode_output_cap as output_cap, autocode_util as util


def prepare(*, engine, adapter, role, route_role, workspace, run_dir, session,
             model, effort, allow_write, planning, report, schema, prompt_file,
            sandbox, transport_args, chatgpt, provider, enforce_tool_boundary=True, tool_commands=()):
    environment = agent_env.scrubbed(os.environ)
    overrides = None
    prior_session = session
    if engine == "opencode":
        containment = None
        if enforce_tool_boundary and not planning and not getattr(adapter, "CONFIGURED", False):
            session = None
            runtime = Path(__file__).resolve().parent
            roots = {runtime, Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve()}
            developer_tools = Path('/Library/Developer/CommandLineTools')
            if developer_tools.is_dir():
                roots.add(developer_tools.resolve())
            containment = {'tool_commands': list(tool_commands), 'read_roots': [str(path) for path in sorted(roots)],
                           'protected_paths': [str(Path(run_dir).resolve()), str(runtime)]}
        try:
            command, child, overrides = adapter.launch(route_role, workspace, run_dir, session,
                model, effort, allow_write, planning=planning, report=report, schema=schema,
                prompt_file=prompt_file, sandbox=sandbox, **({'containment': containment} if containment else {}))
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
            if not containment:
                raise
            raise util.Paused('PAUSED_TOOL_CONTAINMENT', 'Native tool boundary was not established: ' + str(error)) from error
        if child:
            environment = agent_env.scrubbed(child)
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
        raise RuntimeError(f"engine {engine!r} is not bundled in this checkout; providers live in "
                           "~/.config/autocode/providers/ and run with --provider")
    worker = {"engine": engine, "provider": getattr(adapter, "NAME", "opencode") if engine == "opencode" else provider or engine,
              "configured": bool(getattr(adapter, "CONFIGURED", False)), "role": route_role,
              "sandbox": sandbox, "planning": planning, "model": model,
               "command": command, "environment": environment, "provider_session": session}
    if prior_session and session is None:
        worker['fresh_session_reason'] = 'Native tool containment requires a newly bound provider session'
    if environment.get('AUTOCODE_TOOL_CONTAINMENT'):
        worker['tool_containment'] = json.loads(environment['AUTOCODE_TOOL_CONTAINMENT'])
    if engine == "opencode" and not worker["configured"]:
        # Read from the scrubbed environment: the cap the process will actually get.
        worker['output_token_cap'] = output_cap.recorded(os.environ, environment)
    return command, environment, overrides, worker


def containment_prompt(prompt, worker):
    """Advertise only the current stage's permitted scratch area, not broader write access."""
    policy = worker.get('tool_containment')
    if not policy:
        return prompt
    # The kernel boundary lets the stage write only its scratch, so every capture example names it:
    # the provider contract's and COMMON's alike, never a location the stage could not write.
    for example in ('--output .autocode/evidence/<unique-name>.json',
                    '--output <run-directory>/evidence/<unique-name>.json'):
        prompt = prompt.replace(example, '--output ' + str(Path(policy['scratch']) / 'evidence-<unique-name>.json'))
    marker = '\nCURRENT HANDOFF DATA\n'
    before, separator, after = prompt.rpartition(marker)
    if not separator:
        raise ValueError('Contained tool launch requires a structured handoff')
    data = json.loads(after)
    data['tool_containment'] = policy
    instruction = ('\nNATIVE TOOL BOUNDARY: commands run in a kernel-constrained subprocess. '
                   'Use only the shell tool and approved commands. Application files remain read-only '
                   'unless this stage is the Builder. Temporary test output and captured evidence must '
                   'go below tool_containment.scratch, never an external /tmp directory or another '
                   'stage\'s state, events, or receipts. It is the only place under .autocode/ this stage '
                   'can write, so it replaces any other evidence or scratch directory named above. '
                   'A denial is a blocker, not permission to bypass.\n')
    return before + instruction + marker + json.dumps(data, indent=2)


def verify_containment(worker):
    policy = worker.get('tool_containment')
    if policy:
        try:
            from . import autocode_tool_containment
        except ImportError:
            import autocode_tool_containment
        try:
            autocode_tool_containment.verify(policy)
        except (OSError, ValueError, RuntimeError) as error:
            raise util.Paused('PAUSED_TOOL_CONTAINMENT', 'Native tool boundary changed before launch: ' + str(error)) from error
