"""Bounded conformance launches through the existing provider facades.

Codex's native CLI has no provider facade, so its small probe launch is explicit.
This does not import the workflow controller or change its production launcher.
"""

from __future__ import annotations

import json
import subprocess
import time
import tomllib
from contextlib import nullcontext
from pathlib import Path

try:
    from . import autocode_agent_env as agent_env
    from . import autocode_process as processes
    from . import autocode_provider_env as provider_env
    from . import autocode_providers as providers
    from . import autocode_supervision as supervision
    from . import autocode_support as support
    from .autocode_event_log import open_events
    from .autocode_util import atomic_json
    from .providers import command, env_prep
except ImportError:
    import autocode_agent_env as agent_env
    import autocode_process as processes
    import autocode_provider_env as provider_env
    import autocode_providers as providers
    import autocode_supervision as supervision
    import autocode_support as support
    from autocode_event_log import open_events
    from autocode_util import atomic_json
    from providers import command, env_prep


def adapter_for(name, *, fake=False):
    if name == "codex":
        return None
    if fake and name == "kilocode":
        path = Path(__file__).parent / "providers/configs/kilocode.toml"
        return command.CommandProvider(tomllib.loads(path.read_text()), path)
    adapter = providers.resolve(name)
    if name == "kilocode" and adapter.OUTPUT != "opencode_events":
        raise ValueError("This probe requires the KiloCode opencode_events contract")
    return adapter


def preflight(name, model, adapter, workspace, env, *, fake=False):
    if adapter is not None:
        withheld = []
        if getattr(adapter, "CONFIGURED", False):
            withheld = agent_env.withheld(env)
            if not fake:
                adapter.check_forbidden_environment(env)
            env = agent_env.scrubbed(env)
        identity = adapter.local_settings(workspace, env=env)
        roles = {"terra": {"model": model}}
        if not fake:
            try:
                adapter.check_subscription_routes(roles, workspace, env=env)
            except RuntimeError as error:
                hint = provider_env.withheld_hint(withheld)
                if hint:
                    raise RuntimeError(f"{error}. {hint}") from error
                raise
        adapter.check_models(roles, workspace, env=env)
        return identity
    executable = env_prep.resolve_executable("codex", env, cwd=workspace)
    if not executable:
        raise RuntimeError("Codex is not on PATH")
    if not fake:
        if any(key in env for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")):
            raise RuntimeError("Codex subscription probe refuses API-key or endpoint overrides")
        home = Path(env.get("CODEX_HOME", str(Path.home() / ".codex")))
        path = home / "config.toml"
        config = tomllib.loads(path.read_text()) if path.exists() else {}
        if config.get("model_provider") not in (None, "openai") or config.get("openai_base_url"):
            raise RuntimeError("Codex subscription probe requires the default OpenAI route")
    version = subprocess.run(
        [executable, "--version"], env=env, cwd=workspace, capture_output=True, text=True, timeout=15, check=True
    )
    login = subprocess.run(
        [executable, "login", "status"], env=env, cwd=workspace, capture_output=True, text=True, timeout=15
    )
    if login.returncode or "using ChatGPT" not in login.stdout + login.stderr:
        raise RuntimeError("Codex subscription probe requires ChatGPT login")
    return {"engine": name, "executable": executable, "version": version.stdout.strip(), "auth_mode": "ChatGPT"}


def _provider_child(argv, directory, timeout, supervised, checkpoint=None, **options):
    if not supervised:
        return nullcontext(subprocess.Popen(argv, **options))

    def admitted(metadata):
        atomic_json(directory / "supervision-launch.json", metadata)
        if checkpoint is not None:
            checkpoint(metadata)

    return supervision.launch(
        argv,
        receipt_path=directory / "supervision.json",
        timeout=timeout,
        checkpoint=admitted,
        **options,
    )


def execute(
    *,
    adapter,
    workspace,
    directory,
    model,
    effort,
    session,
    allow_write,
    prompt,
    schema,
    env,
    timeout,
    supervised=False,
    checkpoint=None,
    deadline=None,
    prepare=None,
    role=None,
):
    """``prepare(argv, env) -> (argv, env)`` adjusts a tool launch before it runs (the access probe's bare agent).

    ``role`` names the role whose route is being proven when one does; the launch then carries that
    role, so a tool that refuses the effort names the flag that changes it (#2108). Without it the
    probe keeps its generic read-only/write role."""
    directory.mkdir(parents=True)
    # One stem: a report_file adapter reads its report next to the events file the launch
    # wrote (Path.with_suffix), so the two paths must share it.
    event_path, report_path = directory / "stage.jsonl", directory / "stage.json"
    schema_path, prompt_path = directory / "schema.json", directory / "prompt.txt"
    atomic_json(schema_path, schema)
    child_env = agent_env.scrubbed(env)
    if adapter is None:
        argv = [
            "codex",
            "exec",
            "-C",
            str(workspace),
            "--sandbox",
            "workspace-write" if allow_write else "read-only",
            "-c",
            'forced_login_method="chatgpt"',
            "-c",
            'model_provider="openai"',
            "-c",
            f'model_reasoning_effort="{effort}"',
        ]
        if session:
            argv += ["resume", session]
        argv += ["-", "--json", "--output-schema", str(schema_path), "-o", str(report_path), "--model", model]
    else:
        # The probe supplies its schema and matches native shell calls, not capture wrappers.
        launch_kwargs = {}
        if hasattr(adapter, "parse_opencode_version"):
            launch_kwargs["opencode_version"] = adapter.local_settings(workspace, env=env)["version"]
        try:
            from .providers.kilocode import KiloEffortError
        except ImportError:
            from providers.kilocode import KiloEffortError
        try:
            argv, child_env, _ = adapter.launch(
                role or ("terra" if allow_write else "sol"),
                workspace,
                directory,
                session,
                model,
                effort,
                allow_write,
                report=report_path,
                schema=schema_path,
                prompt_file=prompt_path,
                env=child_env,
                **launch_kwargs,
            )
        except KiloEffortError as error:
            # The tool refused the variant before any request. A probe has no stage wrapper to
            # classify it, so report the same pause its stage launch would (autocode_provider_launch),
            # instead of an unclassified RuntimeError (#2108).
            raise support.Paused("PAUSED_TRANSPORT_UNVERIFIED", str(error)) from error
    if prepare is not None and adapter is not None:
        argv, child_env = prepare(list(argv), dict(child_env))
    prompt_path.write_text(prompt)
    executable = env_prep.resolve_executable(argv[0], child_env, cwd=workspace)
    if not executable:
        raise RuntimeError("Provider executable disappeared after preflight")
    argv[0] = executable
    atomic_json(directory / "launch.json", {"argv": argv, "withheld_env": agent_env.withheld(env)})
    with (
        open_events(event_path) as sink,
        (directory / "stderr.txt").open("w") as stderr,
        prompt_path.open() as input_file,
    ):
        with processes.interruption_handler():
            child = None
            try:
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise support.Paused("PAUSED_TIME_LIMIT", "Run time limit reached while preparing model access")
                    timeout = min(timeout, remaining) if timeout is not None else remaining
                with _provider_child(
                    argv,
                    directory,
                    timeout,
                    supervised,
                    checkpoint=checkpoint,
                    cwd=workspace,
                    env=child_env,
                    stdin=input_file,
                    stdout=sink,
                    stderr=stderr,
                    text=True,
                    start_new_session=True,
                ) as child:
                    code, timed_out = processes.wait_for_stage(
                        child, timeout, lambda ids: atomic_json(directory / "processes.json", ids)
                    )
            except supervision.VerifiedStop as error:
                if error.cause != "stage_deadline" or child is None or child.returncode is None:
                    raise
                code, timed_out = child.returncode, True
    rows = support.events(event_path)
    if adapter is not None and getattr(adapter, "NORMALIZES_EVENTS", False):
        # The adapter's public event contract (usage totals on the terminal row) differs
        # from its raw stream; the oracle judges the contract a consumer reads.
        rows = adapter.normalized_events(rows)
    atomic_json(directory / "normalized-events.json", rows)
    report, report_error = None, None
    try:
        if adapter is None:
            report = json.loads(report_path.read_text())
        else:
            report = adapter.final_report(event_path, response_path=directory / "response.txt")
            atomic_json(report_path, report)
    except (OSError, ValueError, RuntimeError) as error:
        report_error = str(error)
    return {
        "exit_code": code,
        "timed_out": timed_out,
        "report": report,
        "rows": rows,
        "report_error": report_error,
        "metrics": support.event_metrics(event_path),
    }
