"""Model-free probes in the selected worker's execution context.

Never invokes `exec`, `run`, an LLM endpoint or a configurable command provider.
Unsupported transports stop admission. OpenCode debug executes its real tool;
because it auto-allows `ask`, approval-bearing effective policies are refused.
"""
from __future__ import annotations

import json
import base64
import hashlib
import re
import os
from pathlib import Path
import shlex
import shutil
import signal
import subprocess
import sys
import time
try:
    from . import autocode_util as util
    from .providers import opencode
except ImportError:
    import autocode_util as util
    from providers import opencode


def configuration_identity(worker, workspace):
    environment = worker['environment']
    if worker['engine'] == 'opencode' and not worker['configured']:
        paths = opencode.configuration_inputs(workspace, env=environment)
    elif worker['engine'] == 'codex':
        home = Path(environment.get('CODEX_HOME') or str(Path(environment.get('HOME', str(Path.home()))) / '.codex'))
        roots = {home, *(parent / '.codex' for parent in (Path(workspace), *Path(workspace).parents))}
        paths = [path for root in roots for path in root.glob('*.toml')]
        paths += [Path('/etc/codex/requirements.toml'), Path('/etc/codex/managed_config.toml')]
    else:
        paths = []
    return {str(path): {'sha256': util.file_hash(path), 'mode': path.stat().st_mode & 0o777}
            for path in sorted(set(paths)) if path.is_file()}


def identity(worker, workspace):
    if not worker:
        return None
    executable = shutil.which(worker["command"][0], path=worker["environment"].get("PATH", ""))
    if not executable:
        raise ValueError("Worker executable is unavailable in its actual environment")
    # Report/schema/session destinations vary per call without changing tool
    # permissions. Bind the executable, actual environment and launch policies.
    environment = dict(worker["environment"])
    environment.pop('AUTOCODE_OUTPUT_ATTEMPT', None)  # attribution only; mode/store remain bound
    inline = environment.get("OPENCODE_CONFIG_CONTENT")
    agent = None
    if worker["engine"] == "opencode" and "--agent" in worker["command"]:
        name = worker["command"][worker["command"].index("--agent") + 1]
        config = json.loads(inline or "{}")
        agent = config.get("agent", {}).pop(name, None)
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config, sort_keys=True)
    return {"engine": worker["engine"], "provider": worker["provider"], "role": worker["role"],
            "sandbox": worker["sandbox"], "planning": worker["planning"], "model": worker["model"],
            "executable": str(Path(executable).resolve()), "executable_sha256": util.file_hash(executable),
            "environment_hash": util.digest(environment), "agent_hash": util.digest(agent),
            "configuration_hash": util.digest(configuration_identity(worker, workspace)),
            "workspace": str(Path(workspace).resolve())}


def permission_match(value, pattern):
    """OpenCode 1.x Wildcard.match: literals, * / ?, optional trailing ' *'."""
    escaped = re.escape(pattern.replace('\\', '/')).replace(r'\*', '.*').replace(r'\?', '.').replace(r'\ ', ' ')
    if escaped.endswith(' .*'):
        escaped = escaped[:-3] + '( .*)?'
    return re.fullmatch(escaped, value.replace('\\', '/'), re.DOTALL) is not None


def run(argv, cwd, output, *, timeout, environment, paused=lambda: False):
    """Exact worker environment, finite timeout, pause polling and group cleanup."""
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    started, timed_out, interrupted = time.monotonic(), False, False
    with output.open("wb") as stream:
        process = subprocess.Popen(argv, cwd=cwd, env=environment, stdin=subprocess.DEVNULL,
            stdout=stream, stderr=subprocess.STDOUT, start_new_session=True)
        try:
            while process.poll() is None:
                if paused():
                    interrupted = True
                    break
                if time.monotonic() - started >= timeout:
                    timed_out = True
                    break
                try:
                    process.wait(timeout=min(0.2, max(0.001, timeout - (time.monotonic() - started))))
                except subprocess.TimeoutExpired:
                    pass
        finally:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait()
    return {"command": shlex.join(argv), "exit_code": None if timed_out or interrupted else process.returncode,
            "timed_out": timed_out, "interrupted": interrupted, "duration": time.monotonic() - started,
            "output": str(output), "output_sha256": util.file_hash(output),
            "tail": output.read_text(errors="replace")[-4000:]}


def execute(worker, argv, root, output, *, timeout, paused=lambda: False, read_path=None):
    if not worker:
        raise ValueError("Worker prerequisite has no provider launch context; no paid dispatch is allowed")
    executable = worker["command"][0]
    environment = worker["environment"]
    if worker["engine"] == "codex":
        # Both supported CLI forms expose their syntax in local help. No
        # speculative paid fallback if the installed helper is unavailable.
        help_log = Path(str(output) + ".sandbox-help")
        help_result = run([executable, "sandbox", "--help"], root, help_log,
                          timeout=timeout, environment=environment, paused=paused)
        help_text = help_log.read_text(errors="replace")
        if help_result["exit_code"] != 0 or "--config" not in help_text:
            raise ValueError("Installed Codex has no usable model-free sandbox helper")
        prefix = [executable, "sandbox"]
        if "macos" in help_text and "linux" in help_text:
            platform = {"darwin": "macos", "linux": "linux"}.get(sys.platform)
            if not platform:
                raise ValueError("Worker sandbox preflight is unsupported on this platform")
            prefix.append(platform)
        prefix += ["-c", "sandbox_mode=" + json.dumps(worker["sandbox"]), "--", *argv]
        result = run(prefix, root, output, timeout=timeout, environment=environment, paused=paused)
        return result, Path(output).read_text(errors="replace")
    if worker["engine"] != "opencode" or worker["configured"]:
        raise ValueError("This transport has no model-free worker probe adapter; select a supported transport explicitly or provide one before dispatch")
    command = worker["command"]
    if "--agent" not in command:
        raise ValueError("OpenCode launch did not identify the worker agent")
    name = command[command.index("--agent") + 1]
    # Select the same model for tool registry construction without contacting it.
    environment = dict(environment)
    config = json.loads(environment.get("OPENCODE_CONFIG_CONTENT", "{}"))
    config.setdefault("agent", {}).setdefault(name, {})["model"] = worker["model"]
    environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config)
    prefix = [executable, "debug", "agent", name]
    policy_log = Path(str(output) + ".permissions")
    policy_result = run(prefix, root, policy_log, timeout=timeout, environment=environment, paused=paused)
    if policy_result["exit_code"] != 0:
        raise ValueError("Cannot inspect the worker's effective OpenCode permissions: " + policy_result["tail"])
    policy = json.loads(policy_log.read_text())
    rules = policy.get('permission')
    if not isinstance(rules, list) or any(not isinstance(rule, dict) or not all(key in rule for key in ('permission', 'pattern', 'action')) for rule in rules):
        raise ValueError('OpenCode did not return an auditable effective permission policy')
    # Later unconditional rules shadow earlier asks. For read probes the tool's
    # only in-worktree permission request is read(relative_path); bash's AST
    # can request external_directory too, so retain conservative ask rejection.
    asks = [rule for index, rule in enumerate(rules) if rule['action'] == 'ask'
            and not any(later['pattern'] == '*' and permission_match(rule['permission'], later['permission'])
                        for later in rules[index + 1:])]
    if read_path:
        relative = str(Path(read_path).relative_to(root))
        matching = [rule for rule in rules if permission_match('read', rule['permission'])
                    and permission_match(relative, rule['pattern'])]
        asks = [matching[-1]] if matching and matching[-1]['action'] == 'ask' else []
    if asks:
        raise ValueError("OpenCode debug would auto-approve an ask permission. Resolve the worker's approval-bearing policy explicitly before using a model-free probe")
    tool_name = 'read' if read_path else 'bash'
    if policy.get("tools", {}).get(tool_name) is not True:
        raise ValueError(f"The worker's actual permissions disable {tool_name}; readiness cannot be attested")
    params = ({'filePath': str(read_path), 'limit': 2000} if read_path else
              {"command": shlex.join(argv), "description": "AutoCode prerequisite (no model)",
               "workdir": str(root), "timeout": max(1, int(timeout * 1000))})
    result = run([*prefix, "--tool", tool_name, "--params", json.dumps(params)], root, output,
                 timeout=timeout, environment=environment, paused=paused)
    text = Path(output).read_text(errors="replace")
    if result["exit_code"] == 0:
        response = json.loads(text)
        tool = response.get("result", {})
        if read_path:
            expected = Path(read_path).read_bytes()
            attachments = tool.get('attachments', [])
            if attachments:
                urls = [a.get('url', '') for a in attachments]
                if len(urls) != 1 or ';base64,' not in urls[0] or base64.b64decode(urls[0].split(';base64,', 1)[1], validate=True) != expected:
                    raise ValueError('Worker image read differs from the approved reference bytes')
            else:
                visible = tool.get('metadata', {}).get('display', {}).get('text')
                if visible is None or visible != expected.decode('utf-8').rstrip('\n'):
                    raise ValueError('Worker design context is truncated or differs from the complete approved text')
            if tool.get('metadata', {}).get('truncated') is not False:
                raise ValueError('Worker design read did not confirm complete access')
            result['read_sha256'] = hashlib.sha256(expected).hexdigest()
            return result, ''
        code = tool.get("metadata", {}).get("exit")
        if type(code) is not int:
            raise ValueError("OpenCode tool result has no integer command exit status")
        result["exit_code"] = code
        text = tool.get("output", "")
        result["tail"] = text[-4000:]
    return result, text


def read_file(worker, path, root, output, *, python, timeout=120, paused=lambda: False):
    expected = util.file_hash(path)
    code = 'import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())'
    result, text = execute(worker, [python, '-c', code, str(path)], root, output,
                           timeout=timeout, paused=paused, read_path=path)
    if worker['engine'] == 'codex' and result['exit_code'] == 0 and text.strip() != expected:
        raise ValueError('Worker could not read the exact approved design input')
    return result
