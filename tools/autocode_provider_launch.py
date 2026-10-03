"""Construct the provider command and environment used by both admission and launch."""
from __future__ import annotations

import os
try:
    from . import autocode_agent_env as agent_env, autocode_gocode as gocode
except ImportError:
    import autocode_agent_env as agent_env, autocode_gocode as gocode


def prepare(*, engine, adapter, role, route_role, workspace, run_dir, session,
            model, effort, allow_write, planning, report, schema, prompt_file,
            sandbox, transport_args, chatgpt, provider):
    environment = agent_env.scrubbed(os.environ)
    overrides = None
    if engine == "opencode":
        command, child, overrides = adapter.launch(route_role, workspace, run_dir, session,
            model, effort, allow_write, planning=planning, report=report, schema=schema,
            prompt_file=prompt_file, sandbox=sandbox)
        if child:
            environment = agent_env.scrubbed(child)
    elif engine == "gocode":
        command = gocode.launch(role=role, workspace=workspace, session=session, model=model,
                               effort=effort, sandbox=sandbox, schema=schema, output=report)
    else:
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
    worker = {"engine": engine, "provider": getattr(adapter, "NAME", "opencode") if engine == "opencode" else provider or engine,
              "configured": bool(getattr(adapter, "CONFIGURED", False)), "role": route_role,
              "sandbox": sandbox, "planning": planning, "model": model,
              "command": command, "environment": environment}
    return command, environment, overrides, worker
