"""OpenCode transport. Credentials stay in OpenCode's own provider setup.

1.x and 2.x are both accepted. The command line follows the major version:
1.x keeps ``--dir`` and ``--variant``; 2.x runs in the workspace directory with
``--standalone`` and puts reasoning effort in the model id (``provider/model#effort``).
A saved run records the version, so resume does not silently change major versions.
Strict tool containment stays qualified only for the pinned 1.x release.
"""

from __future__ import annotations

try:
    from .. import autocode_native_locators as native_locators
    from .. import autocode_prompts as prompts
except ImportError:
    import autocode_native_locators as native_locators
    import autocode_prompts as prompts


import copy
import hashlib
import json
import os  # noqa: F401  (star-exported to autocode_opencode; tests patch oc.os)
import re
import shutil  # noqa: F401  (star-exported to autocode_opencode; tests patch oc.shutil)
import subprocess
import sys
import time
import uuid
from pathlib import Path

if __package__:
    from .opencode_events import _command_exit as _command_exit
    from .opencode_events import _session_parts, normalized_events
else:  # The dashboard also loads this file directly without a package.
    from providers.opencode_events import _command_exit as _command_exit
    from providers.opencode_events import _session_parts, normalized_events

# os and shutil stay imported: the autocode_opencode compatibility shim star-exports
# them, and existing tests patch oc.os / oc.shutil to fake the process environment.

try:
    from .. import autocode_native_role_isolation as native_isolation
    from .. import autocode_output_cap as output_cap
    from .. import autocode_tool_containment as tool_containment
    from . import env_prep
except ImportError:  # Script-style execution from tools/ remains supported.
    import autocode_native_role_isolation as native_isolation
    import autocode_output_cap as output_cap
    import autocode_tool_containment as tool_containment
    from providers import env_prep


try:
    from .. import autocode_tool_handoff as tool_handoff
except ImportError:
    import autocode_tool_handoff as tool_handoff


# 1.x prints ``1.18.33``. 2.x prints ``opencode v2.0.20``. Other majors are refused.
_VERSION_TEXT = re.compile(r"(?:opencode\s+v|v)?(\d+)\.(\d+)\.(\d+)([-+][0-9A-Za-z.-]+)?\Z", re.IGNORECASE)


def parse_opencode_version(text):
    """Normalized ``major.minor.patch`` for OpenCode 1.x or 2.x, otherwise None."""
    if not isinstance(text, str):
        return None
    match = _VERSION_TEXT.fullmatch(text.strip())
    if not match:
        return None
    major = int(match.group(1))
    if major not in (1, 2):
        return None
    return f"{major}.{int(match.group(2))}.{int(match.group(3))}{match.group(4) or ''}"


def opencode_major(version):
    parsed = parse_opencode_version(version) if isinstance(version, str) else None
    if not parsed:
        return None
    return int(parsed.split(".", 1)[0])


def transport_version(settings):
    """The OpenCode version recorded on a run, if that identity is present."""
    if not isinstance(settings, dict):
        return None
    identities = settings.get("transport_identities")
    identity = identities.get("opencode") if isinstance(identities, dict) else None
    if not isinstance(identity, dict):
        identity = settings.get("transport_identity")
    if not isinstance(identity, dict) or identity.get("engine") not in (None, "opencode"):
        return None
    version = identity.get("version")
    return version if isinstance(version, str) else None


DEFAULT_MODELS = {
    # Planning path (Z.ai): Requirements medium → Planner high.
    "requirements": "zai-coding-plan/glm-5.3",
    "glm": "zai-coding-plan/glm-5.3",
    # Independent Plan Reviewer must not be the Planner's model (or family).
    # GPT-6 Sol, not Astra: Astra is too expensive and only for the Resolver (user 2026-09-28).
    "plan_reviewer": "openai/gpt-6-sol",
    # Execution path (user 2026-09-29): the cheap model does the volume, the expensive one
    # judges it. Builder on GLM; Validator and Completion Owner check on GPT-6 Sol, so the
    # verifier never grades its own work (docs/models.md independence). A stuck Builder's
    # stronger attempt runs GPT-6 Sol and GLM checks it (autocode_builder_policy).
    "terra": "zai-coding-plan/glm-5.3",
    "sol": "openai/gpt-6-sol",
    "completion": "openai/gpt-6-sol",
    # The astra route: the per-task Planner (astra_plan) runs on it, and the Resolver's own
    # route ("resolver") starts as a copy of it. Only the Resolver's route climbs the GPT-6
    # Astra effort ladder (autocode_route_ladder; user 2026-09-28, #1345).
    "astra": "openai/gpt-6-astra",
}

# Ladder entry points (docs/models.md) — start medium where the ladder says so,
# escalate to higher reasoning inside the stage when evidence shows struggle.
DEFAULT_REASONING_EFFORTS = {
    "requirements": "medium",
    "glm": "high",
    "plan_reviewer": "high",
    "terra": "medium",
    "sol": "high",
    "completion": "medium",
    "astra": "high",
}


def _configuration_path(value, home):
    if value == "~" or value.startswith("~/"):
        return home / value[2:]
    return Path(value).expanduser()


def _configuration_sources(workspace, *, env=None):
    """Enumerate local configuration definitions, never OpenCode's auth database.

    Directory contents matter as well as directory names. Generated dependencies,
    caches and sessions are deliberately excluded from the configuration identity.
    Configuration roots (XDG_CONFIG_HOME, HOME, OPENCODE_CONFIG_DIR, OPENCODE_CONFIG,
    OPENCODE_TEST_MANAGED_CONFIG_DIR) resolve from the effective environment: the
    explicit ``env`` mapping, or one snapshot of the process environment.
    """
    effective = env_prep.snapshot_environment(env)
    root = Path(workspace).resolve()
    home = Path(effective["HOME"]) if effective.get("HOME") else Path.home()
    global_root = (
        Path(effective["XDG_CONFIG_HOME"]) if effective.get("XDG_CONFIG_HOME") else home / ".config"
    ) / "opencode"
    directories = {global_root, home / ".opencode"}
    paths = {Path(__file__).with_name("opencode_activity.mjs").resolve()}
    definitions = set()
    for parent in (root, *root.parents):
        paths.update(parent / name for name in ("opencode.json", "opencode.jsonc"))
        directories.add(parent / ".opencode")
        if (parent / ".git").exists():
            break
    if effective.get("OPENCODE_CONFIG_DIR"):
        directories.add(_configuration_path(effective["OPENCODE_CONFIG_DIR"], home))
    if effective.get("OPENCODE_CONFIG"):
        paths.add(_configuration_path(effective["OPENCODE_CONFIG"], home))
    managed = effective.get("OPENCODE_TEST_MANAGED_CONFIG_DIR")
    directories.add(
        Path(managed)
        if managed
        else Path("/Library/Application Support/opencode" if sys.platform == "darwin" else "/etc/opencode")
    )
    for directory in directories:
        # Resolve relative environment paths exactly as `opencode --dir` does.
        directory = directory if directory.is_absolute() else root / directory
        paths.update(directory / name for name in ("config.json", "opencode.json", "opencode.jsonc"))
        for name in ("agent", "agents", "mode", "modes", "command", "commands", "plugin", "plugins", "tool", "tools"):
            folder = directory / name
            if folder.is_dir():
                definitions.update(
                    p
                    for p in folder.rglob("*")
                    if p.is_file()
                    and p.suffix in (".md", ".json", ".jsonc", ".js", ".ts", ".mjs", ".cjs")
                    and "node_modules" not in p.relative_to(folder).parts
                )
    normalize = lambda values: {(p if p.is_absolute() else root / p).absolute() for p in values}
    return normalize(paths) - normalize(definitions), normalize(definitions)


def configuration_inputs(workspace, *, env=None):
    primary, definitions = _configuration_sources(workspace, env=env)
    return sorted(primary | definitions, key=str)


def _inert_native_config(raw):
    try:
        return json.loads(raw) in ({}, {"$schema": "https://opencode.ai/config.json"})
    except (ValueError, UnicodeDecodeError):
        return False


def _configuration_hash(path, *, primary):
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if (
        primary
        and not path.is_symlink()
        and path.name in ("config.json", "opencode.json", "opencode.jsonc")
        and _inert_native_config(raw)
    ):
        return None
    return hashlib.sha256(raw).hexdigest()


def _probe_version(workspace, *, env=None):
    effective = env_prep.snapshot_environment(env)
    cwd = workspace if env is not None else None
    executable = env_prep.resolve_executable("opencode", effective, cwd=cwd, allow_default_path=env is None)
    if not executable:
        raise RuntimeError(
            "OpenCode is not on PATH; no provider request was launched. Run `autocode doctor` to see what this "
            "host has: an installed Claude Code or Codex runs with --engine claude or --engine codex"
        )
    try:
        result = env_prep.preflight_run(
            [executable, "--version"], effective, cwd=cwd, capture_output=True, text=True, timeout=15
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("OpenCode version check timed out; no agent was launched") from error
    except OSError as error:
        raise RuntimeError("OpenCode version check cannot start; no provider request was launched") from error
    version = parse_opencode_version(result.stdout)
    if result.returncode or not version:
        raise RuntimeError("This adapter requires OpenCode 1.x or 2.x; inspect opencode --version")
    return executable, version


def local_settings(workspace, *, env=None):
    effective = env_prep.snapshot_environment(env)
    executable, version = _probe_version(workspace, env=env)
    configuration = configuration_identity(workspace, env=effective)
    return {"engine": "opencode", "identity_version": 2, "executable": executable, "version": version, **configuration}


def configuration_identity(workspace, *, env=None):
    """Pure current local fingerprints; no version/native/model call is added."""
    effective = env_prep.snapshot_environment(env)
    # Fingerprint configuration, never credentials. OAuth token refreshes must not
    # invalidate a run, and the runner never opens OpenCode's auth.json.
    primary, definitions = _configuration_sources(workspace, env=effective)
    fingerprints = {
        str(p): _configuration_hash(p, primary=p in primary) for p in sorted(primary | definitions, key=str)
    }
    inline = {
        key: hashlib.sha256(effective[key].encode()).hexdigest() if key in effective else None
        for key in (
            "OPENCODE_CONFIG_CONTENT",
            "OPENCODE_PERMISSION",
            "OPENCODE_CONFIG_DIR",
            "OPENCODE_DISABLE_PROJECT_CONFIG",
            "OPENCODE_PURE",
            "OPENCODE_TEST_MANAGED_CONFIG_DIR",
        )
    }
    return {"config_hashes": fingerprints, "environment_config_hashes": inline}


def transport_drift(current, checkpoint):
    checkpoint = copy.deepcopy(checkpoint)
    for name, previous in checkpoint.get("config_hashes", {}).items():
        # A shipped checkpoint may contain the old raw hash of the native
        # schema-only bootstrap. Confirm the exact bytes before normalizing it.
        if previous and name in current.get("config_hashes", {}) and current["config_hashes"][name] is None:
            path = Path(name)
            if (
                path.name in ("config.json", "opencode.json", "opencode.jsonc")
                and path.is_file()
                and not path.is_symlink()
            ):
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() == previous and _inert_native_config(raw):
                    checkpoint["config_hashes"][name] = None
    if checkpoint.get("identity_version", 1) >= 2:
        return current != checkpoint
    # Old checkpoints did not record every config source. Check every identity
    # field they *did* record before establishing the expanded baseline.
    for key in ("engine", "executable", "version"):
        if current.get(key) != checkpoint.get(key):
            return True
    return any(
        current.get(section, {}).get(key) != value
        for section in ("config_hashes", "environment_config_hashes")
        for key, value in checkpoint.get(section, {}).items()
    )


def available_models(workspace=None, *, env=None):
    """The model IDs ``opencode models`` offers this login."""
    # Slow opencode installations can take well over 30s just to list models
    # (observed ~57s on a free cursor-acp plan with 250+ entries). The call is
    # read-only and infrequent; give it room rather than failing the run before
    # any agent is launched.
    effective = env_prep.snapshot_environment(env)
    if env is not None and not env_prep.resolve_executable("opencode", effective, cwd=workspace):
        # The explicit environment cannot find the provider: nothing was started.
        raise RuntimeError("OpenCode is not on PATH; no agent was launched")
    try:
        result = env_prep.preflight_run(
            ["opencode", "models"], effective, cwd=workspace, capture_output=True, text=True, timeout=180
        )
    except subprocess.TimeoutExpired as error:
        raise RuntimeError("OpenCode model listing timed out; no agent was launched") from error
    except OSError as error:
        # The roster subprocess could not even be spawned (issue #226's failure
        # class): report it as the honest prelaunch state, never FileNotFoundError.
        raise RuntimeError("OpenCode is not on PATH; no agent was launched") from error
    if result.returncode:
        raise RuntimeError(
            "Cannot list OpenCode models; check opencode models and opencode auth list; no agent was launched"
        )
    return set(result.stdout.splitlines())


def check_models(roles, workspace=None, *, env=None, inventory=None):
    available = available_models(workspace, env=env) if inventory is None else inventory
    missing = [entry["model"] for entry in roles.values() if entry["model"] not in available]
    if missing:
        raise RuntimeError(
            "Models unavailable in OpenCode: "
            + ", ".join(sorted(set(missing)))
            + "; `autocode models` lists what your plans offer"
        )


def check_subscription_routes(roles, workspace=None, *, env=None):
    """Validate a configured OpenAI connection, accepting OAuth or API routes.

    Explicit API credentials/endpoints belong to the selected transport. No
    credentials are inspected or copied and no authentication fallback is made.
    The historical function name remains for provider-interface compatibility.
    """
    if not any(config.get("model", "").startswith("openai/") for config in roles.values()):
        return
    if any(key in env_prep.combined_environment(env) for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL")):
        return
    try:
        failed, modes = _openai_auth_modes(workspace, env)
    except (OSError, subprocess.TimeoutExpired) as error:
        raise RuntimeError("Cannot verify OpenCode's OpenAI connection; no provider request was launched") from error
    if failed or modes not in (["oauth"], ["api"]):
        raise RuntimeError(
            "OpenCode OpenAI models require a configured OAuth or API connection; "
            "use OpenCode /connect. No provider request was launched"
        )


def openai_auth(workspace=None, *, env=None):
    """How OpenCode signs in to OpenAI: "oauth" (the ChatGPT login), another mode such as
    "api", "missing" when OpenAI is not connected, or None when the summary cannot be read.
    OAuth and API connections pass check_subscription_routes."""
    try:
        failed, modes = _openai_auth_modes(workspace, env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if failed or len(modes) > 1:
        return None
    return modes[0] if modes else "missing"


def _openai_auth_modes(workspace, env=None):
    returncode, summary = _auth_summary(workspace, env)
    return returncode, re.findall(r"^\s*[●•]\s+OpenAI\s+(\S+)\s*$", summary, re.MULTILINE)


def auth_logins(workspace=None, *, env=None):
    """Every login `opencode auth list` shows: (name, mode) pairs, e.g. ("OpenAI", "oauth").

    For discovery (#1426 steps 4-5): which accounts OpenCode holds, without touching a
    credential or launching a model. An unreadable listing returns None; the caller
    decides whether that is fatal.
    """
    try:
        returncode, summary = _auth_summary(workspace, env)
    except (OSError, subprocess.TimeoutExpired):
        return None
    if returncode:
        return None
    return re.findall(r"^\s*[●•]\s+(.+?)\s+(\S+)\s*$", summary, re.MULTILINE)


def _auth_summary(workspace, env=None):
    # Auth listing cold-starts the whole CLI (over 30 s observed under load).
    # Share the existing 45 s allowance across at most three attempts instead
    # of repeatedly killing valid slow starts at 15 s. Completed checks stand.
    effective = env_prep.snapshot_environment(env)
    deadline = time.monotonic() + 45
    for attempt in range(3):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(["opencode", "auth", "list"], 45)
        try:
            result = env_prep.preflight_run(
                ["opencode", "auth", "list"],
                effective,
                cwd=workspace,
                require_executable=env is not None,
                capture_output=True,
                text=True,
                timeout=remaining,
            )
            break
        except (OSError, subprocess.TimeoutExpired):
            if attempt == 2:
                raise
    summary = re.sub(r"\x1b\[[0-?]*[ -/]*[@-~]", "", result.stdout + result.stderr)
    return result.returncode, summary


def launch(
    role,
    workspace,
    run_dir,
    session,
    model,
    effort,
    allow_write,
    *,
    planning=False,
    report=None,
    schema=None,
    prompt_file=None,
    sandbox=None,
    env=None,
    containment=None,
    planning_shell=False,
    opencode_version=None,
    isolation=None,
    image_preparation=None,
):
    if image_preparation is not None and (isolation is None or role != "sol" or planning or allow_write or session):
        raise ValueError("Native image staging requires the original fresh readonly Sol isolation")
    if image_preparation is not None:
        try:
            from .. import autocode_native_calls as calls
            from .. import autocode_native_image as images
        except ImportError:
            import autocode_native_calls as calls
            import autocode_native_image as images
        if type(image_preparation) is not images.NativeImagePreparation or image_preparation.isolation is not isolation:
            raise ValueError("Native image staging requires its exact original packet")
        image_preparation.require_caller(calls.PlanningCalls)
    if isolation is not None:
        if type(isolation) is not native_isolation.NativeRoleIsolation or containment is None:
            raise ValueError("Native role isolation requires its exact CODE owner and native tool boundary")
        if planning and allow_write:
            raise ValueError("Native isolated planning must remain readonly")
        restriction = isolation.containment_request(allow_write=allow_write)
        if allow_write and containment.get("write_paths") != restriction["write_paths"]:
            raise ValueError("Native isolated writer lacks the exact task footprint before setup")
        if not allow_write and "write_paths" in containment:
            raise ValueError("Readonly native isolation cannot receive writer paths")
    if not model or "/" not in model or "#" in model or any(c.isspace() for c in model):
        raise ValueError("OpenCode model must use provider/model, e.g. zai-coding-plan/glm-5.3")
    if opencode_version is None:
        major = 1
    else:
        major = opencode_major(opencode_version)
        if major is None:
            raise RuntimeError("This adapter requires OpenCode 1.x or 2.x; inspect opencode --version")
    agent = "autocode_" + role
    if planning:
        # Fresh name prevents deep-merged configured role/tool allows from
        # surviving our read-tools-only policy.
        agent += "_plan_" + uuid.uuid4().hex
    # Only restrict permissions here. Do not replace a user's denies/asks with
    # allows, and never enable OpenCode's --auto blanket permission approval.
    permissions = {"task": "deny", "question": "deny", "plan_enter": "deny", "plan_exit": "deny"}
    if not allow_write:
        permissions["edit"] = "deny"
    if planning and isolation is None and containment is None:
        # An explicit read-tools-only planning agent: no delegation, MCP, plugins' tools or edit escape hatch.
        # Its shell is the kernel-contained one (containment, which narrows the agent to bash itself) or, when the
        # person accepted uncontained tools, OpenCode's own (planning_shell). The runner persists its report.
        permissions = {
            "*": "deny",
            "read": "allow",
            "glob": "allow",
            "grep": "allow",
            "list": "allow",
            "edit": "deny",
            "bash": "allow" if planning_shell else "deny",
            "task": "deny",
            "question": "deny",
            "external_directory": "deny",
        }
    activity_plugin = Path(__file__).with_name("opencode_activity.mjs").resolve().as_uri()
    overrides = {
        "$schema": "https://opencode.ai/config.json",
        "share": "disabled",
        "autoupdate": False,
        "plugin": [activity_plugin],
        "agent": {agent: {"description": f"Autocode {role} role", "mode": "primary", "permission": permissions}},
    }
    child = env_prep.child_environment(env)
    # OpenCode's own default (32000, reasoning included) truncates large planning reports.
    output_cap.apply(child)
    configuration = configuration_identity(workspace, env=child) if isolation is not None else None
    inherited = json.loads(child.get("OPENCODE_CONFIG_CONTENT", "{}"))
    if not isinstance(inherited, dict):
        raise ValueError("OPENCODE_CONFIG_CONTENT must be a JSON object")
    # Preserve inline provider configuration and unrelated agents. Our reserved
    # role definitions are constructed for this launch only, never saved globally.
    agents = inherited.get("agent", {})
    if not isinstance(agents, dict) or not isinstance(agents.get(agent, {}), dict):
        raise ValueError("OpenCode inline agent configuration must contain agent objects")
    prior = agents.get(agent, {})
    policy = prior.get("permission", {})
    if isinstance(policy, str) and policy in ("allow", "ask", "deny"):
        policy = {"*": policy}
    if not isinstance(policy, dict):
        raise ValueError("OpenCode agent permissions must be an action or an object")
    definition = {**prior, **overrides["agent"][agent], "permission": {**policy, **permissions}}
    plugins = inherited.get("plugin", [])
    if not isinstance(plugins, list):
        raise ValueError("OpenCode inline plugin configuration must be an array")
    combined = {
        **inherited,
        **overrides,
        "agent": {**agents, agent: definition},
        "plugin": [p for p in plugins if p != activity_plugin] + [activity_plugin],
    }
    child["OPENCODE_CONFIG_CONTENT"] = json.dumps(combined)
    # 2.x has no --dir or --variant. The runner already starts the process in the
    # workspace, and --standalone keeps that process's private server inside the
    # supervised tree instead of a shared background service.
    if major == 2:
        selected = f"{model}#{effort}" if effort else model
        command = [
            "opencode",
            "run",
            "--standalone",
            "--format",
            "json",
            "--agent",
            agent,
            "--model",
            selected,
            "--title",
            f"Autocode {role}: {run_dir}",
        ]
    else:
        command = [
            "opencode",
            "run",
            "--dir",
            str(workspace),
            "--format",
            "json",
            "--agent",
            agent,
            "--model",
            model,
            "--title",
            f"Autocode {role}: {run_dir}",
        ]
    if session:
        command += ["--session", session]
    if major == 1 and effort:
        command += ["--variant", effort]
    if isolation is not None:
        command, child = isolation.stage(
            command=command, environment=child, configuration_identity=configuration, allow_write=allow_write
        )
        if image_preparation is not None:
            command, child = isolation.stage_image(image_preparation, command=command, environment=child)
    if containment is not None:
        child, boundary = tool_containment.configure(
            command,
            child,
            workspace,
            allow_write=allow_write,
            request=containment,
            **({"retain_launch_identity": True} if isolation is not None else {}),
        )
        actual = json.loads(child["OPENCODE_CONFIG_CONTENT"])
        overrides["shell"] = boundary["shell"]
        overrides["agent"][agent] = actual["agent"][agent]
        if isolation is not None:
            isolation.render_image_prompt(command=command, environment=child, boundary=boundary)
            descriptor = isolation.finalize(command=command, environment=child, boundary=boundary)
            boundary["native_role_isolation"] = descriptor
            child["AUTOCODE_TOOL_CONTAINMENT"] = json.dumps(native_locators.boundary_value(boundary))
            child["AUTOCODE_NATIVE_ROLE_ISOLATION"] = json.dumps(native_locators.descriptor_value(descriptor))
            isolation.freeze_image(command=command, environment=child, descriptor=descriptor)
            overrides = actual
    return command, child, overrides


def prompt_for_schema(prompt, schema, events):
    properties = schema.get("properties", {})
    if "assistant_reply" in properties and not any(
        name in properties for name in ("checks", "criterion_results", "commands_run", "evidence_refs")
    ):
        instructions = prompts.get("fragments/provider-opencode/conversation-output.md").format(events=events)
        instructions += "\n" + json.dumps(schema, separators=(",", ":")) + "\n"
        return prompt.replace("\nCURRENT HANDOFF DATA\n", instructions + "\nCURRENT HANDOFF DATA\n", 1)
    if tool_handoff.planning_packet(prompt):
        instructions = prompts.get("fragments/provider-opencode/planning-output.md")
        instructions += "\n" + json.dumps(schema, separators=(",", ":")) + "\n"
        return prompt.replace("\nCURRENT HANDOFF DATA\n", instructions + "\nCURRENT HANDOFF DATA\n", 1)
    # A standalone code review reports findings and tests_run, not build
    # acceptance checks. Do not inject a different job's receipt protocol.
    review_fields = {"change_under_review", "change_patch", "findings", "delivered_tests", "tests_run"}
    evidence_fields = {"checks", "criterion_results", "commands_run", "evidence_refs"}
    if review_fields <= properties.keys() and not evidence_fields.intersection(properties):
        instructions = prompts.get("fragments/provider-opencode/review-output.md").format(events=events)
        instructions += "\n" + json.dumps(schema, separators=(",", ":")) + "\n"
        return prompt.replace("\nCURRENT HANDOFF DATA\n", instructions + "\nCURRENT HANDOFF DATA\n", 1)
    prompt = tool_handoff.with_capture_command(prompt)
    instructions = (
        prompts.get("fragments/provider-opencode/prompt-for-schema-02.md")
        + str(events)
        + prompts.get("fragments/provider-opencode/prompt-for-schema.md")
        + json.dumps(schema, separators=(",", ":"))
        + "\n"
    )
    return prompt.replace("\nCURRENT HANDOFF DATA\n", instructions + "\nCURRENT HANDOFF DATA\n", 1)


def raw_events(path):
    if not Path(path).exists():
        return []
    rows = []
    for line in Path(path).read_text(errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _trailing_report(final, *, copies=1):
    """Recover a reply of prose followed by the complete JSON report and nothing else.

    Models sometimes lead with a summary ("The probe confirms ... Report:") despite the
    JSON-only instruction, whatever its length (a live attempt led with 730 characters
    and was repaired at full cost). Accept any prose lead without a code fence as long
    as the message ENDS with one complete JSON object, so a fragment quoted inside an
    explanation or a second object is never taken for the report. Report-only repairs
    (``copies=2``) also accept the same object repeated twice, or one complete
    object followed by exactly one redundant closing brace. No content is invented
    or selected between conflicting reports. Every recovered report is
    still validated against the stage's schema by the runner."""
    start = final.find("{")
    if start < 0 or "```" in final[:start]:
        return None
    decoder = json.JSONDecoder()
    reports = []
    remaining = final[start:].strip()
    while remaining and len(reports) < copies:
        try:
            report, end = decoder.raw_decode(remaining)
        except ValueError:
            return None
        if not isinstance(report, dict):
            return None
        reports.append(report)
        remaining = remaining[end:].strip()
        if copies == 2 and len(reports) == 1 and remaining == "}":
            # A completed report-only MiMo repair repeated its final delimiter (#1747).
            # The complete object is unambiguous; raw response/event bytes stay retained.
            remaining = ""
    if remaining or not reports or any(report != reports[0] for report in reports[1:]):
        return None
    return reports[0]


def final_report(path, *, recover_wrapped=False, response_path=None):
    rows = raw_events(path)
    normalized = normalized_events(rows)
    if not any(row.get("type") == "turn.completed" for row in normalized):
        raise RuntimeError("OpenCode turn has no successful terminal step; preserve it without automatic retry")
    parts = _session_parts(rows, normalized[0]["thread_id"])
    last_step = [row["part"] for row in parts.values() if row.get("type") == "step_finish"][-1]
    message = last_step.get("messageID")
    texts = {}
    for row in rows:
        part = row.get("part", {})
        if row.get("type") == "text" and message and part.get("messageID") == message:
            texts[part["id"]] = part.get("text", "")
    final = "\n".join(texts.values()).strip()
    if response_path is not None:
        # Retain only the completed assistant message, not tool output or earlier
        # turns, even when its JSON is malformed and parsing below must reject it.
        Path(response_path).write_text(final)
    report = None
    try:
        report = json.loads(final)
    except ValueError:
        # OpenCode can emit commentary and the final report as distinct text
        # parts under the same completed message. Accept the final complete JSON
        # part only when there is exactly one JSON-object part in that message.
        parts = [text.strip() for text in texts.values() if text.strip()]
        parsed_parts = []
        for index, part_text in enumerate(parts):
            try:
                candidate = json.loads(part_text)
            except ValueError:
                continue
            if isinstance(candidate, dict):
                parsed_parts.append((index, candidate))
        if len(parsed_parts) == 1 and parsed_parts[0][0] == len(parts) - 1:
            report = parsed_parts[0][1]
        # Models may wrap the report in commentary despite the schema instruction;
        # accept an explicitly fenced JSON block, or brief prose followed by the complete
        # report at the very end, but never a fragment with commentary after it.
        for candidate in reversed(re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", final, re.S)):
            if report is not None:
                break
            try:
                report = json.loads(candidate)
                break
            except ValueError:
                continue
        if report is None:
            report = _trailing_report(final, copies=2 if recover_wrapped else 1)
    if not isinstance(report, dict):
        raise RuntimeError("OpenCode final message is not a JSON report; inspect the saved raw events")
    return report


def incomplete_response(path):
    """Return text from the terminal length-limited message, never a report.

    Callers must label this as incomplete and keep its events as the evidence
    source. It is useful only to help a bounded report repair preserve findings
    already emitted before the provider reached its output limit.
    """
    rows = raw_events(path)
    terminal = [
        row for row in rows if row.get("type") == "step_finish" and row.get("part", {}).get("reason") == "length"
    ]
    if not terminal:
        return None
    finish = terminal[-1]
    part = finish.get("part") or {}
    message_id = part.get("messageID")
    session_id = part.get("sessionID") or finish.get("sessionID")
    if not message_id:
        return None
    texts = {}
    for row in rows:
        body = row.get("part") or {}
        if (
            row.get("type") == "text"
            and body.get("messageID") == message_id
            and (not session_id or (body.get("sessionID") or row.get("sessionID")) == session_id)
            and isinstance(body.get("text"), str)
        ):
            part_id = body.get("id")
            key = str(part_id) if part_id is not None else str(len(texts))
            if key in texts and texts[key] != body["text"]:
                return None
            texts[key] = body["text"]
    text = "\n".join(texts.values()).strip()
    return text if text and len(text.encode("utf-8")) <= 128 * 1024 else None
