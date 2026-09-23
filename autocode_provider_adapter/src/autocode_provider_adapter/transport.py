"""Versioned external transport contract and fail-closed GoCode implementation.

This module intentionally contains provider mechanics only.  It neither imports nor
copies upstream orchestration, dashboard handlers, persistence, prompts, or state.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import shlex
import shutil
import stat
import subprocess
import tempfile
from typing import Callable, Mapping, Protocol, Sequence
from urllib.parse import urlsplit

from .compatibility import CompatibilityError, CompatibilityManifest


TRANSPORT_CONTRACT_VERSION = 1
APPROVED_GPT_MODELS = frozenset({"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"})
CLAUDE_MODEL = "claude-opus-5"
TERMINAL_WIRE_ALIAS = "openai/gpt-6-astra"
ROLE_DEFAULTS = {
    "glm": ("gpt-5.6-sol", "medium"),
    "astra": ("gpt-5.6-sol", "high"),
    "terra": ("gpt-5.6-terra", "medium"),
    "sol": ("gpt-5.6-sol", "high"),
    "completion": ("gpt-5.6-sol", "high"),
}
REASONING_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
TERMINAL_CLAUDE_ROLES = frozenset({"astra", "sol", "completion"})
ROUTE_CHECK_TIMEOUT_SECONDS = 60


class TransportError(RuntimeError):
    """A transport request was rejected before a provider process could start."""


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str
    stderr: str


@dataclass(frozen=True)
class RoleRoute:
    requested_model: str
    resolved_model: str
    effort: str


@dataclass(frozen=True)
class RunRoleLaunchRequest:
    """Versioned copy of the complete untouched upstream ``run_role`` launch seam."""

    version: int
    role: str
    prompt: str
    sandbox: str
    workspace: Path
    run_dir: Path
    session: str | None
    model: str | None
    effort: str | None
    allow_write: bool
    planning: bool
    report_repair: bool
    schema: Path
    output: Path
    events: Path
    child_options: Mapping[str, object]

    def __post_init__(self) -> None:
        if self.version != TRANSPORT_CONTRACT_VERSION:
            raise TransportError("unsupported run-role transport request version")
        if (not isinstance(self.role, str) or not self.role or not isinstance(self.prompt, str)
                or self.sandbox not in {"read-only", "workspace-write"}):
            raise TransportError("run-role transport request has malformed role, prompt, or sandbox")
        if self.allow_write != (self.sandbox == "workspace-write"):
            raise TransportError("run-role transport request has inconsistent write permission and sandbox")
        if (self.planning or self.report_repair) and (self.sandbox != "read-only" or self.allow_write or self.session is not None):
            raise TransportError("planning and report-repair requests must be fresh read-only launches")
        if self.session is not None and (not isinstance(self.session, str) or not self.session):
            raise TransportError("run-role transport request has malformed session")
        if self.model is not None and not isinstance(self.model, str):
            raise TransportError("run-role transport request has malformed model")
        if self.effort is not None and (not isinstance(self.effort, str) or self.effort not in REASONING_EFFORTS):
            raise TransportError("run-role transport request has malformed reasoning effort")
        if not all(isinstance(path, Path) and path.is_absolute()
                   for path in (self.workspace, self.run_dir, self.schema, self.output, self.events)):
            raise TransportError("run-role transport request paths must be absolute")
        if set(self.child_options) - {"start_new_session"}:
            raise TransportError("run-role transport request has unsupported child options")
        if "start_new_session" in self.child_options and type(self.child_options["start_new_session"]) is not bool:
            raise TransportError("run-role transport request start_new_session must be boolean")


@dataclass(frozen=True)
class LaunchSpec:
    command: list[str]
    environment: dict[str, str]
    identity: dict[str, object]
    request: RunRoleLaunchRequest | None = None
    route: RoleRoute | None = None
    prompt: str = ""
    route_fingerprint: str | None = None


class ExternalTransport(Protocol):
    """The versioned runner/dashboard surface implemented by every transport."""

    contract_version: int

    def identity(self, workspace: Path) -> dict[str, object]: ...
    def transport_drift(self, current: Mapping[str, object], checkpoint: Mapping[str, object]) -> bool: ...
    def validate_roles(self, roles: Mapping[str, Mapping[str, object]]) -> dict[str, RoleRoute]: ...
    def prepare_launch(self, request: RunRoleLaunchRequest, route: RoleRoute,
                       checkpoint: Mapping[str, object] | None) -> LaunchSpec: ...
    def adapt_prompt(self, prompt: str, schema: Mapping[str, object]) -> str: ...
    def normalize_events(
        self, events: Sequence[Mapping[str, object]], session: str, expected_model: str | None = None
    ) -> list[dict[str, object]]: ...
    def final_report(
        self, events: Sequence[Mapping[str, object]], session: str, expected_model: str | None = None
    ) -> dict[str, object]: ...
    def dashboard_catalogue(self, workspace: Path) -> list[str]: ...
    def validate_dashboard_models(self, values: Mapping[str, object]) -> dict[str, str]: ...
    def task_arguments(self, models: Mapping[str, str], efforts: Mapping[str, object]) -> list[str]: ...
    def prepare_conversation(self, *args: object, **kwargs: object) -> LaunchSpec: ...
    def spawn(self, spec: LaunchSpec, workspace: Path, checkpoint: Mapping[str, object] | None = None) -> object: ...


CommandRunner = Callable[..., CommandResult]
ProcessFactory = Callable[..., object]


def _subprocess_runner(command: list[str], **kwargs: object) -> CommandResult:
    result = subprocess.run(command, text=True, capture_output=True, **kwargs)
    return CommandResult(result.returncode, result.stdout, result.stderr)


def _sha256_fd(descriptor: int) -> str:
    digest = hashlib.sha256()
    while chunk := os.read(descriptor, 65536):
        digest.update(chunk)
    return digest.hexdigest()


def _safe_executable(raw: str, label: str, expected_path: str | None = None) -> dict[str, object]:
    """Resolve a reported path once, then reopen its vetted canonical target safely."""
    candidate = Path(raw)
    if not candidate.is_absolute():
        raise TransportError(f"GoCode did not report an absolute {label} path")
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as error:
        raise TransportError(f"GoCode-reported {label} is unavailable") from error
    if expected_path is not None and str(resolved) != expected_path:
        raise TransportError(f"GoCode-reported {label} resolved to an untrusted canonical path")
    try:
        before = os.lstat(resolved)
    except OSError as error:
        raise TransportError(f"GoCode-reported {label} is unavailable") from error
    # A provider may report a stable convenience symlink (such as Homebrew's
    # codex entry point), but process creation always uses this non-symlink target.
    if stat.S_ISLNK(before.st_mode):
        raise TransportError(f"GoCode-reported {label} canonical path uses a forbidden symlink")
    if not stat.S_ISREG(before.st_mode) or not os.access(resolved, os.X_OK):
        raise TransportError(f"GoCode-reported {label} is not an executable regular file")
    if not hasattr(os, "O_NOFOLLOW"):
        raise TransportError("this platform cannot safely validate executable identities")
    try:
        descriptor = os.open(resolved, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as error:
        raise TransportError(f"cannot reopen GoCode-reported {label} safely") from error
    try:
        opened = os.fstat(descriptor)
        if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
            raise TransportError(f"GoCode-reported {label} changed during validation")
        return {"path": str(resolved), "sha256": _sha256_fd(descriptor), "device": opened.st_dev, "inode": opened.st_ino}
    finally:
        os.close(descriptor)


def _parse_env(exported: str) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in exported.splitlines():
        if not line.strip():
            continue
        try:
            tokens = shlex.split(line, posix=True)
        except ValueError as error:
            raise TransportError("GoCode returned malformed route exports") from error
        if len(tokens) != 2 or tokens[0] != "export" or "=" not in tokens[1]:
            raise TransportError("GoCode returned an unsupported route export")
        name, value = tokens[1].split("=", 1)
        if name not in {"OPENAI_API_KEY", "OPENAI_BASE_URL"}:
            continue
        if name in values or not value or len(value) > 4096 or any(ord(char) < 32 for char in value):
            raise TransportError("GoCode returned an ambiguous route export")
        values[name] = value
    if set(values) != {"OPENAI_API_KEY", "OPENAI_BASE_URL"}:
        raise TransportError("GoCode did not provide a complete managed OpenAI route")
    endpoint = urlsplit(values["OPENAI_BASE_URL"])
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password
            or endpoint.query or endpoint.fragment or not endpoint.path.endswith("/v1")):
        raise TransportError("GoCode returned an invalid managed endpoint")
    return values


def _route_fingerprint(route: Mapping[str, str]) -> str:
    """Bind a route snapshot without retaining its bearer credential."""
    try:
        encoded = (route["OPENAI_BASE_URL"] + "\0" + route["OPENAI_API_KEY"]).encode("utf-8")
    except (KeyError, TypeError) as error:
        raise TransportError("GoCode returned an incomplete managed route") from error
    return hashlib.sha256(encoded).hexdigest()


def _catalogue_models(output: str) -> set[str]:
    """Read either legacy bare model rows or GoCode's human table output."""
    selected: set[str] = set()
    for line in output.splitlines():
        fields = line.split()
        if len(fields) == 1 and fields[0] in APPROVED_GPT_MODELS:
            selected.add(fields[0])
        elif len(fields) >= 2 and fields[1] == "openai" and fields[0] in APPROVED_GPT_MODELS:
            selected.add(fields[0])
    return selected


def _event_stream(path: Path):
    """Open the caller-owned event file without following a final-path symlink."""
    if not hasattr(os, "O_NOFOLLOW"):
        raise TransportError("this platform cannot safely open the requested event stream")
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
    except OSError as error:
        raise TransportError("cannot open the requested GoCode event stream") from error
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise TransportError("requested GoCode event stream is not a regular file")
        return os.fdopen(descriptor, "w", encoding="utf-8")
    except Exception:
        os.close(descriptor)
        raise


def _spawn_with_stream_bindings(
    process_factory: ProcessFactory, command: list[str], *, workspace: Path, environment: Mapping[str, str],
    request: RunRoleLaunchRequest, prompt: str, pass_fds: tuple[int, ...] = (),
) -> object:
    """Create a run-role child with the contract's shared prompt/event wiring."""
    options = dict(request.child_options)
    if pass_fds:
        options["pass_fds"] = pass_fds
    events = _event_stream(request.events)
    try:
        child = process_factory(
            command, cwd=str(workspace), env=dict(environment), stdin=subprocess.PIPE,
            stdout=events, stderr=subprocess.STDOUT, text=True, **options,
        )
        stream = getattr(child, "stdin", None)
        if stream is not None:
            stream.write(prompt)
            stream.close()
        return child
    finally:
        events.close()


def _status_fields(summary: str) -> dict[str, str]:
    fields: dict[str, str] = {}
    for line in summary.splitlines():
        if not line.strip():
            continue
        key, separator, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if not separator or not key or not value:
            raise TransportError("GoCode returned malformed status fields")
        if key in fields:
            raise TransportError(f"GoCode returned duplicate status field {key!r}")
        fields[key] = value
    return fields


def _status_executable(raw: str, label: str) -> str:
    """Accept only GoCode's documented path renderings, never an inferred value."""
    if raw.startswith("active (") and raw.endswith(")"):
        raw = raw[len("active ("):-1]
    elif " (" in raw and raw.endswith(")"):
        raw = raw.split(" (", 1)[0]
    if not raw or not Path(raw).is_absolute():
        raise TransportError(f"GoCode status omitted an absolute {label} path")
    return raw


def _schema_document(path: Path) -> dict[str, object]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise TransportError("run-role transport request schema cannot be read") from error
    if not isinstance(value, dict):
        raise TransportError("run-role transport request schema must be a JSON object")
    return value


class NativeOpenCodeTransport:
    """Thin contract facade over an untouched upstream OpenCode module.

    The caller passes the imported upstream module; this adapter never changes it.
    """

    contract_version = TRANSPORT_CONTRACT_VERSION

    def __init__(self, operations: object) -> None:
        self.operations = operations

    @classmethod
    def from_upstream_module(cls, module: object) -> "NativeOpenCodeTransport":
        """Compose the untouched upstream OpenCode module through this contract."""
        return cls(_UpstreamOpenCodeOperations(module))

    def _operation(self, name: str) -> Callable[..., object]:
        try:
            return getattr(self.operations, name)
        except AttributeError as error:
            raise TransportError(f"upstream OpenCode transport lacks contract operation {name}") from error

    def identity(self, workspace: Path) -> dict[str, object]:
        return dict(self._operation("identity")(workspace))

    def transport_drift(self, current: Mapping[str, object], checkpoint: Mapping[str, object]) -> bool:
        return bool(self._operation("transport_drift")(current, checkpoint))

    def validate_roles(self, roles: Mapping[str, Mapping[str, object]]) -> dict[str, RoleRoute]:
        return dict(self._operation("validate_roles")(roles))

    def prepare_launch(self, request: RunRoleLaunchRequest, route: RoleRoute,
                       checkpoint: Mapping[str, object] | None) -> LaunchSpec:
        return self._operation("prepare_launch")(request, route, checkpoint)

    def adapt_prompt(self, prompt: str, schema: Mapping[str, object]) -> str:
        return str(self._operation("adapt_prompt")(prompt, schema))

    def normalize_events(
        self, events: Sequence[Mapping[str, object]], session: str, expected_model: str | None = None
    ) -> list[dict[str, object]]:
        return list(self._operation("normalize_events")(events, session, expected_model))

    def final_report(
        self, events: Sequence[Mapping[str, object]], session: str, expected_model: str | None = None
    ) -> dict[str, object]:
        return dict(self._operation("final_report")(events, session, expected_model))

    def dashboard_catalogue(self, workspace: Path) -> list[str]:
        return list(self._operation("dashboard_catalogue")(workspace))

    def validate_dashboard_models(self, values: Mapping[str, object]) -> dict[str, str]:
        return dict(self._operation("validate_dashboard_models")(values))

    def task_arguments(self, models: Mapping[str, str], efforts: Mapping[str, object]) -> list[str]:
        return list(self._operation("task_arguments")(models, efforts))

    def prepare_conversation(self, *args: object, **kwargs: object) -> LaunchSpec:
        return self._operation("prepare_conversation")(*args, **kwargs)

    def spawn(self, spec: LaunchSpec, workspace: Path, checkpoint: Mapping[str, object] | None = None) -> object:
        return self._operation("spawn")(spec, workspace, checkpoint)


class _UpstreamOpenCodeOperations:
    """Small external translator for public, untouched upstream OpenCode functions."""

    def __init__(self, module: object) -> None:
        self.module = module

    def identity(self, workspace: Path) -> dict[str, object]:
        return dict(getattr(self.module, "local_settings")(workspace))

    def transport_drift(self, current: Mapping[str, object], checkpoint: Mapping[str, object]) -> bool:
        return bool(getattr(self.module, "transport_drift")(dict(current), dict(checkpoint)))

    def validate_roles(self, roles: Mapping[str, Mapping[str, object]]) -> dict[str, RoleRoute]:
        upstream_roles: dict[str, dict[str, object]] = {}
        result: dict[str, RoleRoute] = {}
        for role, raw in roles.items():
            if not isinstance(role, str) or not isinstance(raw, Mapping):
                raise TransportError("OpenCode role configuration is malformed")
            model, effort = raw.get("model"), raw.get("reasoning_effort")
            if not isinstance(model, str) or not isinstance(effort, str):
                raise TransportError("OpenCode role requires model and reasoning effort")
            upstream_roles[role] = {"model": model}
            result[role] = RoleRoute(model, model, effort)
        try:
            getattr(self.module, "check_models")(upstream_roles)
        except (RuntimeError, ValueError) as error:
            raise TransportError(str(error)) from error
        return result

    def prepare_launch(self, request: RunRoleLaunchRequest, route: RoleRoute,
                       checkpoint: Mapping[str, object] | None) -> LaunchSpec:
        current = self.identity(request.workspace)
        if checkpoint is not None and self.transport_drift(current, checkpoint):
            raise TransportError("OpenCode transport identity changed before process creation")
        try:
            command, environment, _overrides = getattr(self.module, "launch")(
                request.role, request.workspace, request.run_dir, request.session, route.resolved_model, route.effort,
                request.allow_write, planning=request.planning or request.report_repair,
            )
        except (RuntimeError, ValueError) as error:
            raise TransportError(str(error)) from error
        prompt = str(getattr(self.module, "prompt_for_schema")(request.prompt, _schema_document(request.schema), request.events))
        return LaunchSpec(list(command), dict(environment), current, request, route, prompt)

    def adapt_prompt(self, prompt: str, schema: Mapping[str, object]) -> str:
        return str(getattr(self.module, "prompt_for_schema")(prompt, dict(schema), Path("events.jsonl")))

    def normalize_events(self, events: Sequence[Mapping[str, object]], session: str,
                         expected_model: str | None = None) -> list[dict[str, object]]:
        del expected_model
        rows = list(getattr(self.module, "normalized_events")(list(events)))
        if not rows or rows[0].get("thread_id") != session:
            raise TransportError("OpenCode events have a missing or mixed owned session")
        return [dict(row) for row in rows]

    def final_report(self, events: Sequence[Mapping[str, object]], session: str,
                     expected_model: str | None = None) -> dict[str, object]:
        del session, expected_model
        # The authoritative upstream function intentionally consumes its JSONL
        # event file.  Serialize the caller-owned fixture into an isolated,
        # short-lived file rather than reimplementing report extraction here.
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "events.jsonl"
            path.write_text("".join(json.dumps(dict(event)) + "\n" for event in events), encoding="utf-8")
            try:
                return dict(getattr(self.module, "final_report")(path))
            except (RuntimeError, ValueError) as error:
                raise TransportError(str(error)) from error

    def dashboard_catalogue(self, workspace: Path) -> list[str]:
        runner = getattr(self.module, "subprocess").run
        try:
            response = runner(["opencode", "models"], cwd=workspace, capture_output=True, text=True, timeout=30)
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TransportError("OpenCode model catalogue is unavailable") from error
        models = [line.strip() for line in response.stdout.splitlines() if line.strip()]
        if response.returncode or not models or len(models) != len(set(models)):
            raise TransportError("OpenCode model catalogue is unavailable or ambiguous")
        return models

    def validate_dashboard_models(self, values: Mapping[str, object]) -> dict[str, str]:
        result: dict[str, str] = {}
        for key, value in values.items():
            if not isinstance(key, str) or not key.endswith("_model") or not isinstance(value, str) or "/" not in value:
                raise TransportError("OpenCode dashboard model choices are malformed")
            result[key.removesuffix("_model")] = value
        return result

    def task_arguments(self, models: Mapping[str, str], efforts: Mapping[str, object]) -> list[str]:
        result: list[str] = []
        for role, model in models.items():
            if not isinstance(role, str) or not isinstance(model, str):
                raise TransportError("OpenCode dashboard task model is malformed")
            result.extend((f"--{role}-model", model))
        for key, effort in efforts.items():
            if not isinstance(key, str) or not key.endswith("_reasoning_effort") or not isinstance(effort, str):
                raise TransportError("OpenCode dashboard reasoning choice is malformed")
            result.extend((f"--{key.removesuffix('_reasoning_effort')}-reasoning-effort", effort))
        return result

    def prepare_conversation(self, workspace: Path, run_dir: Path, model: str, effort: str,
                             checkpoint: Mapping[str, object] | None) -> LaunchSpec:
        request = RunRoleLaunchRequest(
            version=TRANSPORT_CONTRACT_VERSION, role="conversation", prompt="", sandbox="read-only",
            workspace=workspace, run_dir=run_dir, session=None, model=model, effort=effort, allow_write=False,
            planning=False, report_repair=False, schema=run_dir / "conversation-schema.json",
            output=run_dir / "conversation-output.json", events=run_dir / "conversation-events.jsonl",
            child_options={},
        )
        return self.prepare_launch(request, RoleRoute(model, model, effort), checkpoint)

    def spawn(self, spec: LaunchSpec, workspace: Path, checkpoint: Mapping[str, object] | None = None) -> object:
        del checkpoint
        if spec.request is None or spec.request.workspace != workspace:
            raise TransportError("OpenCode process creation requires a complete matching run-role request")
        try:
            return _spawn_with_stream_bindings(
                subprocess.Popen, spec.command, workspace=workspace, environment=spec.environment,
                request=spec.request, prompt=spec.prompt,
            )
        except (OSError, TypeError) as error:
            raise TransportError("OpenCode process creation failed") from error


class GoCodeTransport:
    """GoCode provider adapter with manifest-pinned identities and routes."""

    contract_version = TRANSPORT_CONTRACT_VERSION

    def __init__(
        self, manifest: CompatibilityManifest, *, command_runner: CommandRunner = _subprocess_runner,
        which: Callable[[str], str | None] = shutil.which,
        process_factory: ProcessFactory = subprocess.Popen,
        before_spawn: Callable[[Mapping[str, str]], None] | None = None,
    ) -> None:
        self.manifest = manifest
        self._run = command_runner
        self._which = which
        self._spawn = process_factory
        self._before_spawn = before_spawn

    def identity(self, workspace: Path) -> dict[str, object]:
        executable = self._which("gocode")
        if not executable:
            raise TransportError("GoCode is not on PATH; no provider process was launched")
        gocode = _safe_executable(executable, "GoCode executable")
        if str(gocode["sha256"]) not in {identity.gocode_sha256 for identity in self.manifest.gocode_identities}:
            raise TransportError("untrusted GoCode executable digest; no provider process was launched")
        try:
            status = self._run([str(gocode["path"]), "status"], cwd=str(workspace), timeout=ROUTE_CHECK_TIMEOUT_SECONDS)
            exported = self._run(
                [str(gocode["path"]), "env", "--shell", "bash"], cwd=str(workspace), timeout=ROUTE_CHECK_TIMEOUT_SECONDS,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise TransportError("GoCode route check failed; no provider process was launched") from error
        fields = _status_fields(status.stdout + "\n" + status.stderr)
        mode = fields.get("mode", "").lower()
        managed = mode == "managed" and fields.get("gocode authentication", "").lower() == "ok"
        unmanaged = (mode == "unmanaged"
                     and fields.get("gocode client service", "").lower().startswith("reachable")
                     and fields.get("gocode inference endpoint", "").lower().startswith("reachable"))
        if status.returncode or exported.returncode or not (managed or unmanaged):
            raise TransportError("GoCode has no authenticated verified managed or unmanaged route; no provider process was launched")
        version = fields.get("gocode version")
        codex_raw = fields.get("codex real")
        shim_raw = fields.get("claude shim")
        if not version or not codex_raw or not shim_raw:
            raise TransportError("GoCode status omitted a required managed executable or shim identity")
        codex = _safe_executable(
            _status_executable(codex_raw, "real Codex executable"), "real Codex executable",
        )
        shim = _safe_executable(_status_executable(shim_raw, "Claude shim"), "Claude shim")
        enrolled = next((row for row in self.manifest.gocode_identities
                         if row.version == version and row.gocode_sha256 == str(gocode["sha256"])), None)
        if enrolled is None or not enrolled.gocode_real_path:
            raise TransportError("untrusted GoCode shim or missing real executable identity")
        gocode_real = _safe_executable(
            enrolled.gocode_real_path, "real GoCode executable", enrolled.gocode_real_path,
        )
        route = _parse_env(exported.stdout)
        try:
            self.manifest.match_gocode_identity(
                version=version, gocode_sha256=str(gocode["sha256"]),
                gocode_real_path=str(gocode_real["path"]), gocode_real_sha256=str(gocode_real["sha256"]),
                codex_path=str(codex["path"]), codex_sha256=str(codex["sha256"]),
                shim_path=str(shim["path"]), shim_sha256=str(shim["sha256"]),
            )
        except CompatibilityError as error:
            raise TransportError(str(error)) from error
        return {
            "engine": "gocode", "contract_version": self.contract_version, "version": version,
            "gocode": gocode, "gocode_real": gocode_real, "codex": codex, "claude_shim": shim,
            "provider_endpoint": route["OPENAI_BASE_URL"],
            # Credentials are deliberately never retained in an identity or
            # launch specification.  This digest lets the final launch reject
            # any endpoint or credential replacement without disclosing it.
            "route_fingerprint": _route_fingerprint(route),
        }

    @staticmethod
    def transport_drift(current: Mapping[str, object], checkpoint: Mapping[str, object]) -> bool:
        return dict(current) != dict(checkpoint)

    def _current(
        self, workspace: Path, checkpoint: Mapping[str, object] | None, run_dir: Path,
    ) -> tuple[dict[str, object], dict[str, str]]:
        """Revalidate installed identities and the managed route before launch."""
        del run_dir
        current = self.identity(workspace)
        if checkpoint is not None and self.transport_drift(current, checkpoint):
            raise TransportError("GoCode transport identity changed before process creation")
        try:
            gocode_path = str(dict(current["gocode_real"])["path"])
            expected_fingerprint = current["route_fingerprint"]
        except (KeyError, TypeError, ValueError) as error:
            raise TransportError("GoCode transport checkpoint is malformed") from error
        if not isinstance(expected_fingerprint, str):
            raise TransportError("GoCode transport checkpoint has no route fingerprint")
        try:
            exported = self._run([gocode_path, "env", "--shell", "bash"], cwd=str(workspace), timeout=ROUTE_CHECK_TIMEOUT_SECONDS)
        except (OSError, subprocess.TimeoutExpired, TypeError) as error:
            raise TransportError("GoCode could not revalidate its managed route before process creation") from error
        if exported.returncode:
            raise TransportError("GoCode could not revalidate its managed route before process creation")
        route = _parse_env(exported.stdout)
        self._validate_managed_route(current, route, expected_fingerprint)
        return current, route

    @staticmethod
    def _validate_managed_route(
        identity: Mapping[str, object], route: Mapping[str, str], expected_fingerprint: str,
    ) -> None:
        if (route["OPENAI_BASE_URL"] != identity.get("provider_endpoint")
                or not hmac.compare_digest(_route_fingerprint(route), expected_fingerprint)):
            raise TransportError("GoCode managed credential or endpoint changed before process creation")

    def validate_roles(self, roles: Mapping[str, Mapping[str, object]]) -> dict[str, RoleRoute]:
        result: dict[str, RoleRoute] = {}
        for role, raw in roles.items():
            if role not in ROLE_DEFAULTS or not isinstance(raw, Mapping):
                raise TransportError("unsupported GoCode role configuration")
            default_model, default_effort = ROLE_DEFAULTS[role]
            requested = raw.get("model", default_model)
            effort = raw.get("reasoning_effort", default_effort)
            if effort is None:
                effort = default_effort
            if not isinstance(requested, str) or not isinstance(effort, str) or effort not in REASONING_EFFORTS:
                raise TransportError("GoCode role requires a supported model and reasoning effort")
            if requested in {TERMINAL_WIRE_ALIAS, CLAUDE_MODEL} and role in TERMINAL_CLAUDE_ROLES:
                result[role] = RoleRoute(requested, CLAUDE_MODEL, effort)
            elif requested in APPROVED_GPT_MODELS:
                result[role] = RoleRoute(requested, requested, effort)
            else:
                raise TransportError("GoCode role selected an unsupported model")
        return result

    @staticmethod
    def _private_home(run_dir: Path) -> Path:
        if not run_dir.is_dir():
            raise TransportError("GoCode private Codex home requires an existing run directory")
        home = run_dir / "gocode-codex-home"
        if home.is_symlink():
            raise TransportError("GoCode private Codex home cannot be a symlink")
        home.mkdir(mode=0o700, exist_ok=True)
        if home.resolve() != home.absolute():
            raise TransportError("GoCode private Codex home must not resolve through a symlink")
        os.chmod(home, 0o700)
        return home

    @staticmethod
    def _base_environment(run_dir: Path) -> dict[str, str]:
        environment = {name: value for name, value in os.environ.items()
                       if name in {"PATH", "HOME", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TMPDIR"}}
        environment.update(CODEX_HOME=str(GoCodeTransport._private_home(run_dir)))
        return environment

    @classmethod
    def _environment(cls, route: Mapping[str, str], run_dir: Path) -> dict[str, str]:
        environment = cls._base_environment(run_dir)
        environment["OPENAI_API_KEY"] = route["OPENAI_API_KEY"]
        return environment

    def _command(self, request: RunRoleLaunchRequest, route: RoleRoute, executable: str,
                 managed_route: Mapping[str, str]) -> list[str]:
        if request.role not in ROLE_DEFAULTS:
            raise TransportError("unsupported GoCode role")
        if route.resolved_model == CLAUDE_MODEL:
            if request.sandbox != "read-only" or request.allow_write:
                raise TransportError("GoCode Claude terminal routes require read-only planning permission")
            command = [executable, "-p", "--model", CLAUDE_MODEL,
                       "--output-format", "stream-json", "--permission-mode", "plan", "--effort", route.effort]
            if request.session:
                command += ["--resume", request.session]
        else:
            if route.resolved_model not in APPROVED_GPT_MODELS:
                raise TransportError("GoCode route resolution is unsupported")
            command = [executable, "exec", "-C", str(request.workspace), "--sandbox", request.sandbox,
                       "--ignore-user-config", "-c", 'model_provider="gocode"',
                       "-c", f'model_providers.gocode.base_url="{managed_route["OPENAI_BASE_URL"]}"',
                       "-c", 'model_providers.gocode.env_key="OPENAI_API_KEY"', "-c", f'model_reasoning_effort="{route.effort}"']
            if request.session:
                command += ["resume", request.session]
            command += ["-", "--json", "--output-schema", str(request.schema), "-o", str(request.output), "--model", route.resolved_model]
        return command

    def prepare_launch(
        self, request: RunRoleLaunchRequest, route: RoleRoute,
        checkpoint: Mapping[str, object] | None,
    ) -> LaunchSpec:
        if request.role not in ROLE_DEFAULTS or request.model != route.requested_model or request.effort != route.effort:
            raise TransportError("run-role request does not match its validated GoCode route")
        identity, managed_route = self._current(request.workspace, checkpoint, request.run_dir)
        executable = str(dict(identity["claude_shim" if route.resolved_model == CLAUDE_MODEL else "codex"])["path"])
        return LaunchSpec(
            self._command(request, route, executable, managed_route), self._base_environment(request.run_dir),
            identity, request, route, self.adapt_prompt(request.prompt, _schema_document(request.schema)),
            _route_fingerprint(managed_route),
        )

    def adapt_prompt(self, prompt: str, schema: Mapping[str, object]) -> str:
        if not isinstance(prompt, str) or not isinstance(schema, Mapping):
            raise TransportError("GoCode prompt and schema must be structured values")
        return (
            "GOCODE OUTPUT CONTRACT\nReturn exactly one structured JSON report matching this schema. "
            "Preserve durable event IDs and never claim command evidence absent a completed command event.\n"
            + json.dumps(dict(schema), sort_keys=True) + "\n\n" + prompt
        )

    def normalize_events(
        self, events: Sequence[Mapping[str, object]], session: str, expected_model: str | None = None
    ) -> list[dict[str, object]]:
        if not isinstance(session, str) or not session:
            raise TransportError("GoCode events require a transport-owned session")
        normalized = [dict(event) for event in events]
        starts = [event for event in normalized if event.get("type") == "thread.started"]
        if len(starts) != 1 or starts[0].get("thread_id") != session:
            raise TransportError("GoCode events have a missing or mixed owned session")
        initialized_model = starts[0].get("resolved_model")
        if initialized_model not in APPROVED_GPT_MODELS | {CLAUDE_MODEL}:
            raise TransportError("GoCode initialization event omitted a supported resolved model")
        if expected_model is not None and initialized_model != expected_model:
            raise TransportError("GoCode initialization model route drifted")
        for event in normalized:
            thread_id = event.get("thread_id")
            if thread_id is not None and thread_id != session:
                raise TransportError("GoCode events have a mixed owned session")
            if event.get("type") == "turn.failed":
                raise TransportError("GoCode event stream reported a failure")
        terminal = [event for event in normalized if event.get("type") == "turn.completed"]
        if len(terminal) != 1:
            raise TransportError("GoCode event stream requires exactly one successful terminal event")
        usage = terminal[0].get("usage")
        if not isinstance(usage, Mapping) or any(type(usage.get(field)) is not int or usage[field] < 0
                                             for field in ("input_tokens", "output_tokens")):
            raise TransportError("GoCode terminal event has incomplete usage")
        return normalized

    def final_report(
        self, events: Sequence[Mapping[str, object]], session: str, expected_model: str | None = None
    ) -> dict[str, object]:
        normalized = self.normalize_events(events, session, expected_model)
        terminal = next(event for event in normalized if event.get("type") == "turn.completed")
        report = terminal.get("report")
        if not isinstance(report, dict):
            raise TransportError("GoCode terminal event omitted a structured final report")
        return report

    def dashboard_catalogue(self, workspace: Path) -> list[str]:
        # The dashboard catalogue has no run directory in the versioned
        # contract and creates no adapter files in the checkout.
        identity = self.identity(workspace)
        try:
            result = self._run(
                [str(dict(identity["gocode"])["path"]), "models"], cwd=str(workspace), timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired, TypeError) as error:
            raise TransportError("GoCode model catalogue is unavailable") from error
        if result.returncode:
            raise TransportError("GoCode model catalogue is unavailable")
        models = _catalogue_models(result.stdout)
        if not models:
            raise TransportError("GoCode model catalogue contains unsupported or ambiguous routes")
        return sorted(models)

    def validate_dashboard_models(self, values: Mapping[str, object]) -> dict[str, str]:
        selected: dict[str, str] = {}
        for key, value in values.items():
            if not isinstance(key, str) or not key.endswith("_model") or not isinstance(value, str):
                raise TransportError("dashboard model choices are malformed")
            if value not in APPROVED_GPT_MODELS:
                raise TransportError("dashboard roles require a supported GoCode GPT model")
            selected[key.removesuffix("_model")] = value
        return selected

    def task_arguments(self, models: Mapping[str, str], efforts: Mapping[str, object]) -> list[str]:
        arguments: list[str] = []
        for role, model in models.items():
            if role not in ROLE_DEFAULTS or model not in APPROVED_GPT_MODELS:
                raise TransportError("dashboard task model is unsupported")
            arguments += [f"--{role}-model", model]
        for key, effort in efforts.items():
            if not isinstance(key, str) or not key.endswith("_reasoning_effort") or not isinstance(effort, str):
                raise TransportError("dashboard reasoning choice is malformed")
            role = key.removesuffix("_reasoning_effort")
            if role not in ROLE_DEFAULTS or effort not in REASONING_EFFORTS:
                raise TransportError("dashboard reasoning choice is unsupported")
            arguments += [f"--{role}-reasoning-effort", effort]
        return arguments

    def prepare_conversation(
        self, workspace: Path, run_dir: Path, model: str, effort: str,
        checkpoint: Mapping[str, object] | None,
    ) -> LaunchSpec:
        if workspace.resolve() != run_dir.resolve():
            raise TransportError("project-free conversations may not receive a project workspace")
        if model not in APPROVED_GPT_MODELS or effort not in REASONING_EFFORTS:
            raise TransportError("project-free conversation selected an unsupported GoCode model or effort")
        identity, managed_route = self._current(workspace, checkpoint, run_dir)
        command = [str(dict(identity["codex"])["path"]), "exec", "-C", str(workspace), "--sandbox", "read-only",
                   "--ignore-user-config", "-c", 'model_provider="gocode"',
                   "-c", f'model_providers.gocode.base_url="{managed_route["OPENAI_BASE_URL"]}"',
                   "-c", 'model_providers.gocode.env_key="OPENAI_API_KEY"', "-c", f'model_reasoning_effort="{effort}"',
                   "-", "--json", "--model", model]
        return LaunchSpec(
            command, self._base_environment(run_dir), identity,
            route_fingerprint=_route_fingerprint(managed_route),
        )

    def spawn(self, spec: LaunchSpec, workspace: Path, checkpoint: Mapping[str, object] | None = None) -> object:
        """Revalidate, then launch the canonical installed executable path.

        The operating system does not offer a portable atomic validate-and-exec
        primitive for Node scripts. This rejects changes through the final
        pre-launch check; a malicious same-user replacement in the tiny interval
        after that check remains outside this adapter's guarantee.
        """
        if spec.request is None or spec.route is None or spec.request.workspace != workspace:
            raise TransportError("GoCode process creation requires a complete matching run-role request")
        try:
            identity, _ = self._current(workspace, checkpoint, spec.request.run_dir)
            if self._before_spawn is not None:
                self._before_spawn({key: str(dict(identity[key])["path"])
                                    for key in ("gocode", "gocode_real", "codex", "claude_shim")})
            identity, managed_route = self._current(workspace, checkpoint, spec.request.run_dir)
            if (spec.route_fingerprint is None
                    or not hmac.compare_digest(_route_fingerprint(managed_route), spec.route_fingerprint)):
                raise TransportError("GoCode managed credential or endpoint changed after launch preparation")
            executable_key = "claude_shim" if spec.route.resolved_model == CLAUDE_MODEL else "codex"
            command = self._command(
                spec.request, spec.route, str(dict(identity[executable_key])["path"]), managed_route,
            )
            environment = self._environment(managed_route, spec.request.run_dir)
            return _spawn_with_stream_bindings(
                self._spawn, command, workspace=workspace, environment=environment, request=spec.request,
                prompt=spec.prompt,
            )
        except (OSError, TypeError) as error:
            raise TransportError("GoCode process creation failed after identity validation") from error
