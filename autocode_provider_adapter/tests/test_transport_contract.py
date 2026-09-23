from __future__ import annotations

import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess

import pytest

from autocode_gocode_adapter.compatibility import CompatibilityManifest
import autocode_gocode_adapter.transport as transport_module
from autocode_gocode_adapter.transport import (
    APPROVED_GPT_MODELS,
    CLAUDE_MODEL,
    CommandResult,
    GoCodeTransport,
    NativeOpenCodeTransport,
    RoleRoute,
    RunRoleLaunchRequest,
    TransportError,
)


class FakeNativeOperations:
    def identity(self, _workspace: Path) -> dict[str, object]: return {"engine": "opencode"}
    def transport_drift(self, current: object, checkpoint: object) -> bool: return current != checkpoint
    def validate_roles(self, _roles: object) -> dict[str, RoleRoute]: return {"sol": RoleRoute("openai/gpt", "openai/gpt", "high")}
    def prepare_launch(self, *_args: object, **_kwargs: object) -> object: return None
    def adapt_prompt(self, prompt: str, _schema: object) -> str: return prompt
    def normalize_events(self, events: object, _session: str, _model: str | None = None) -> object: return events
    def final_report(self, _events: object, _session: str, _model: str | None = None) -> dict[str, object]: return {"status": "PASS"}
    def dashboard_catalogue(self, _workspace: Path) -> list[str]: return ["openai/gpt"]
    def validate_dashboard_models(self, _values: object) -> dict[str, str]: return {"sol": "openai/gpt"}
    def task_arguments(self, _models: object, _efforts: object) -> list[str]: return ["--sol-model", "openai/gpt"]
    def prepare_conversation(self, *_args: object, **_kwargs: object) -> object: return None
    def spawn(self, *_args: object, **_kwargs: object) -> object: return None


class FakeGoCode:
    def __init__(self, codex: Path, shim: Path) -> None:
        self.codex = codex
        self.shim = shim
        self.endpoint = "https://managed.example.test/v1"
        self.mode = "managed"
        self.authenticated = True
        self.exports: str | None = None
        self.catalogue = sorted(APPROVED_GPT_MODELS)
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: list[str], **_kwargs: object) -> CommandResult:
        self.calls.append(tuple(command))
        descriptor_index = next((index for index, value in enumerate(command) if value.startswith("/dev/fd/")), None)
        arguments = command[descriptor_index + 1:] if descriptor_index is not None else command[1:]
        if arguments == ["status"]:
            return CommandResult(
                0,
                "\n".join((
                    "GoCode version: 1.2.3",
                    f"Mode: {self.mode}",
                    "GoCode authentication: ok" if self.authenticated else "GoCode authentication: unavailable",
                    f"Codex real: {self.codex}",
                    f"Claude shim: {self.shim}",
                )),
                "",
            )
        if arguments == ["env", "--shell", "bash"]:
            return CommandResult(0, self.exports or f"export OPENAI_API_KEY=managed-token\nexport OPENAI_BASE_URL={self.endpoint}\n", "")
        if arguments == ["models"]:
            return CommandResult(0, "\n".join(self.catalogue), "")
        raise AssertionError(command)


def executable(path: Path, contents: str) -> Path:
    path.write_text(contents, encoding="utf-8")
    path.chmod(0o700)
    return path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def launch_request(
    workspace: Path, *, role: str = "sol", sandbox: str = "read-only", model: str = "gpt-5.6-sol",
    effort: str = "high", session: str | None = "saved", planning: bool = False, report_repair: bool = False,
) -> RunRoleLaunchRequest:
    schema = workspace / "schema.json"
    schema.write_text('{"type":"object"}', encoding="utf-8")
    return RunRoleLaunchRequest(
        version=1, role=role, prompt="complete the assigned work", sandbox=sandbox, workspace=workspace,
        run_dir=workspace, session=session, model=model, effort=effort, allow_write=sandbox == "workspace-write",
        planning=planning, report_repair=report_repair, schema=schema, output=workspace / "output.json",
        events=workspace / "events.jsonl", child_options={"start_new_session": True},
    )


@pytest.fixture
def transport(tmp_path: Path) -> tuple[GoCodeTransport, FakeGoCode, Path, Path]:
    gocode = executable(tmp_path / "gocode", "#!/bin/sh\n# managed gocode\n")
    codex = executable(tmp_path / "codex-real", "#!/bin/sh\n# real codex\n")
    shim = executable(tmp_path / "claude-shim", "#!/bin/sh\n# gocode claude shim\n")
    runner = FakeGoCode(codex, shim)
    manifest = CompatibilityManifest.from_dict({
        "version": 1,
        "probes": [{"path": "runner.py", "contains": "Runner"}],
        "gocode": [{
            "version": "1.2.3",
            "gocode_sha256": digest(gocode),
            "gocode_real_path": str(gocode),
            "gocode_real_sha256": digest(gocode),
            "codex_path": str(codex),
            "codex_sha256": digest(codex),
            "shim_path_regex": "^" + str(shim) + "$",
            "shim_sha256": digest(shim),
        }],
    })
    return GoCodeTransport(manifest, command_runner=runner, which=lambda _name: str(gocode)), runner, codex, shim


def test_bundled_manifest_contains_only_the_runner_approved_tuple() -> None:
    manifest = CompatibilityManifest.default()
    assert len(manifest.gocode_identities) == 1
    identity = manifest.gocode_identities[0]
    assert (identity.version, identity.gocode_sha256, identity.gocode_real_path, identity.gocode_real_sha256,
            identity.codex_path, identity.codex_sha256,
            identity.shim_path_regex, identity.shim_sha256) == (
        "0.1.310", "06f364874ed8b4b064e432c9fb1c4a6083dc590cbee60779e1b2eec2003b1750",
        "/Users/akothari/.gocode/versions/0.1.310/gocode-dev.app/Contents/MacOS/gocode",
        "09bad9e3bbbe7f0092e0c7391e0335a933ce1a328fb03a7bd27c64856ae15926",
        "/opt/homebrew/lib/node_modules/@openai/codex/bin/codex.js",
        "61b0194f3bb6534439c8d26a3ed57d0805f84b884588b761795323eeb92fcf70",
        r"^/Users/akothari/\.gocode/shims/claude$",
        "3176fce2c8dc89e59f8a9a67012da9c6f8173882412d5a6c25f8f4cd58deaa1e",
    )


def test_manifest_rejects_extra_or_altered_trusted_identities() -> None:
    approved = CompatibilityManifest.default().gocode_identities[0]
    row = vars(approved)
    with pytest.raises(Exception, match="multiple"):
        CompatibilityManifest.from_dict({"version": 1, "probes": [{"path": "runner.py", "contains": "Runner"}],
                                         "gocode": [row, row]})
    with pytest.raises(Exception, match="canonical"):
        CompatibilityManifest.from_dict({"version": 1, "probes": [{"path": "runner.py", "contains": "Runner"}],
                                         "gocode": [{**row, "codex_path": "relative/codex"}]})


def test_gocode_contract_preserves_approved_role_mappings_and_terminal_boundary(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], tmp_path: Path
) -> None:
    subject, _runner, codex, shim = transport
    identity = subject.identity(tmp_path)
    roles = subject.validate_roles({
        "glm": {"model": "gpt-5.6-sol", "reasoning_effort": "medium"},
        "astra": {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
        "terra": {"model": "gpt-5.6-terra", "reasoning_effort": "medium"},
        "sol": {"model": "gpt-5.6-sol", "reasoning_effort": "high"},
        "completion": {"model": "gpt-5.6-luna", "reasoning_effort": "max"},
    })
    assert {route.resolved_model for route in roles.values()} == {"gpt-5.6-sol", "gpt-5.6-terra", "gpt-5.6-luna"}
    terminal = subject.validate_roles({"completion": {"model": "openai/gpt-6-astra", "reasoning_effort": "medium"}})
    assert terminal["completion"].resolved_model == CLAUDE_MODEL

    gpt = subject.prepare_launch(launch_request(tmp_path, role="terra", model="gpt-5.6-terra", effort="medium"), roles["terra"], identity)
    assert gpt.command[0] == str(codex)
    assert "opencode" not in gpt.command
    assert "gpt-5.6-terra" in gpt.command
    assert gpt.environment["CODEX_HOME"].startswith(str(tmp_path))
    # A prepared launch may retain only a fingerprint of its managed route.
    # The credential itself is recovered from GoCode at the final spawn boundary.
    assert "OPENAI_API_KEY" not in gpt.environment
    assert "ANTHROPIC_API_KEY" not in gpt.environment

    claude = subject.prepare_launch(launch_request(tmp_path, role="completion", model="openai/gpt-6-astra", effort="medium", session=None), terminal["completion"], identity)
    assert claude.command[0] == str(shim)
    assert CLAUDE_MODEL in claude.command
    assert "opencode" not in claude.command


def test_gocode_role_defaults_match_the_authoritative_run_roles(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path]
) -> None:
    subject, _runner, _codex, _shim = transport
    routes = subject.validate_roles({role: {} for role in ("glm", "astra", "terra", "sol", "completion")})
    assert {role: (route.resolved_model, route.effort) for role, route in routes.items()} == {
        "glm": ("gpt-5.6-sol", "medium"), "astra": ("gpt-5.6-sol", "high"),
        "terra": ("gpt-5.6-terra", "medium"), "sol": ("gpt-5.6-sol", "high"),
        "completion": ("gpt-5.6-sol", "high"),
    }


def test_native_opencode_and_gocode_expose_the_same_external_contract_surface(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], tmp_path: Path
) -> None:
    native = NativeOpenCodeTransport(FakeNativeOperations())
    gocode, _runner, _codex, _shim = transport
    operations = (
        "identity", "transport_drift", "validate_roles", "prepare_launch", "adapt_prompt", "normalize_events",
        "final_report", "dashboard_catalogue", "validate_dashboard_models", "task_arguments", "prepare_conversation", "spawn",
    )
    assert all(callable(getattr(native, name)) and callable(getattr(gocode, name)) for name in operations)
    assert native.identity(tmp_path)["engine"] == "opencode"
    assert native.transport_drift({"a": 1}, {"a": 2})
    assert native.validate_roles({})["sol"].resolved_model == "openai/gpt"
    assert native.prepare_launch(launch_request(tmp_path), RoleRoute("openai/gpt", "openai/gpt", "high"), None) is None
    assert native.adapt_prompt("prompt", {}) == "prompt"
    assert native.normalize_events([], "native-session") == []
    assert native.final_report([], "native-session") == {"status": "PASS"}
    assert native.dashboard_catalogue(tmp_path) == ["openai/gpt"]
    assert native.validate_dashboard_models({}) == {"sol": "openai/gpt"}
    assert native.task_arguments({}, {}) == ["--sol-model", "openai/gpt"]
    assert native.prepare_conversation() is None


def test_native_contract_is_backed_by_the_untouched_upstream_module(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    source = Path(os.environ["AUTOCODE_TEST_UPSTREAM"]) / "tools" / "autocode_opencode.py"
    spec = importlib.util.spec_from_file_location("untouched_opencode_for_contract", source)
    assert spec and spec.loader
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)
    calls: list[str] = []
    launch_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def local_settings(_workspace: Path) -> dict[str, object]:
        calls.append("identity")
        return {"engine": "opencode", "identity_version": 2}

    def check_models(roles: object) -> None:
        calls.append("validate")
        assert roles == {"sol": {"model": "openai/gpt-5.6-sol"}}

    def launch(*_args: object, **_kwargs: object) -> tuple[list[str], dict[str, str], dict[str, object]]:
        calls.append("launch")
        launch_calls.append((_args, _kwargs))
        return ["opencode", "run"], {"PATH": "/bin"}, {}

    monkeypatch.setattr(upstream, "local_settings", local_settings)
    monkeypatch.setattr(upstream, "check_models", check_models)
    monkeypatch.setattr(upstream, "launch", launch)
    monkeypatch.setattr(upstream, "transport_drift", lambda current, checkpoint: current != checkpoint)
    monkeypatch.setattr(upstream, "prompt_for_schema", lambda prompt, schema, events: f"{prompt}:{schema}:{events}")
    monkeypatch.setattr(upstream, "normalized_events", lambda rows: [{"type": "thread.started", "thread_id": "native"}, *rows])
    monkeypatch.setattr(upstream, "final_report", lambda path: {"status": "PASS", "raw": json.loads(path.read_text().splitlines()[0])})

    class Completed:
        returncode = 0
        stdout = "openai/gpt-5.6-sol\nzai/glm\n"

    monkeypatch.setattr(upstream.subprocess, "run", lambda *_args, **_kwargs: Completed())
    subject = NativeOpenCodeTransport.from_upstream_module(upstream)
    identity = subject.identity(tmp_path)
    route = subject.validate_roles({"sol": {"model": "openai/gpt-5.6-sol", "reasoning_effort": "high"}})["sol"]
    launch_spec = subject.prepare_launch(launch_request(tmp_path, session=None), route, identity)
    assert launch_spec.command == ["opencode", "run"]
    assert subject.adapt_prompt("prompt", {"type": "object"}).startswith("prompt:")
    assert subject.normalize_events([{"type": "turn.completed"}], "native")[-1]["type"] == "turn.completed"
    assert subject.final_report([{"provider": "opencode"}], "native")["status"] == "PASS"
    assert subject.dashboard_catalogue(tmp_path) == ["openai/gpt-5.6-sol", "zai/glm"]
    assert subject.validate_dashboard_models({"sol_model": "openai/gpt-5.6-sol"}) == {"sol": "openai/gpt-5.6-sol"}
    assert subject.task_arguments({"sol": "openai/gpt-5.6-sol"}, {"sol_reasoning_effort": "high"}) == [
        "--sol-model", "openai/gpt-5.6-sol", "--sol-reasoning-effort", "high"]
    for planning, report_repair, sandbox in ((True, False, "read-only"), (False, True, "read-only"),
                                             (False, False, "workspace-write")):
        request = launch_request(tmp_path, sandbox=sandbox, session=None if planning or report_repair else "saved",
                                 planning=planning, report_repair=report_repair)
        spec = subject.prepare_launch(request, route, identity)
        assert spec.request == request and str(request.events) in spec.prompt
    assert [(args[0], args[3], args[6], kwargs["planning"]) for args, kwargs in launch_calls] == [
        ("sol", None, False, False), ("sol", None, False, True), ("sol", None, False, True),
        ("sol", "saved", True, False),
    ]
    assert calls == ["identity", "validate", "identity", "launch", "identity", "launch", "identity", "launch", "identity", "launch"]


@pytest.mark.parametrize("target", ["gocode", "codex", "shim"])
def test_spawn_revalidates_every_identity_immediately_before_process_creation(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], tmp_path: Path, target: str
) -> None:
    subject, _runner, codex, shim = transport
    checkpoint = subject.identity(tmp_path)
    route = subject.validate_roles({"completion" if target == "shim" else "sol":
                                    {"model": "openai/gpt-6-astra", "reasoning_effort": "medium"} if target == "shim" else {}})
    role = "completion" if target == "shim" else "sol"
    spec = subject.prepare_launch(launch_request(tmp_path, role=role, model="openai/gpt-6-astra" if target == "shim" else "gpt-5.6-sol", effort="medium" if target == "shim" else "high", session=None), route[role], checkpoint)
    gocode = Path(str(checkpoint["gocode"]["path"]))
    executable({"gocode": gocode, "codex": codex, "shim": shim}[target], "#!/bin/sh\n# replaced after preflight\n")
    with pytest.raises(TransportError, match="digest|identity"):
        subject.spawn(spec, tmp_path, checkpoint)


def test_identity_rejects_untrusted_or_changed_executables_and_bad_routes(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], tmp_path: Path
) -> None:
    subject, runner, codex, _shim = transport
    checkpoint = subject.identity(tmp_path)
    executable(codex, "#!/bin/sh\n# replaced\n")
    with pytest.raises(TransportError, match="digest|changed"):
        subject.prepare_launch(launch_request(tmp_path, session=None), subject.validate_roles({"sol": {}})["sol"], checkpoint)

    executable(codex, "#!/bin/sh\n# real codex\n")
    runner.endpoint = "http://managed.example.test/v1"
    with pytest.raises(TransportError, match="endpoint"):
        subject.identity(tmp_path)
    runner.endpoint = "https://managed.example.test/v1"
    runner.authenticated = False
    with pytest.raises(TransportError, match="authenticated"):
        subject.identity(tmp_path)
    runner.authenticated = True
    runner.exports = "export OPENAI_API_KEY=first\nexport OPENAI_API_KEY=second\nexport OPENAI_BASE_URL=https://managed.example.test/v1\n"
    with pytest.raises(TransportError, match="ambiguous"):
        subject.identity(tmp_path)


def test_identity_rejects_untrusted_canonical_target_and_unknown_shim_digest(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], tmp_path: Path
) -> None:
    subject, runner, codex, shim = transport
    replacement = executable(tmp_path / "replacement", "#!/bin/sh\n# real codex\n")
    codex.unlink()
    codex.symlink_to(replacement)
    # GoCode may report a stable convenience symlink, but its canonical target
    # must be the manifest-pinned path; this fixture resolves elsewhere.
    with pytest.raises(TransportError, match="untrusted"):
        subject.identity(tmp_path)

    codex.unlink()
    executable(codex, "#!/bin/sh\n# real codex\n")
    executable(shim, "#!/bin/sh\n# counterfeit gocode claude shim\n")
    with pytest.raises(TransportError, match="digest"):
        subject.identity(tmp_path)
    assert runner.calls


def test_events_reports_and_sessions_fail_closed(transport: tuple[GoCodeTransport, FakeGoCode, Path, Path]) -> None:
    subject, _runner, _codex, _shim = transport
    events = [
        {"type": "thread.started", "thread_id": "session-a", "resolved_model": "gpt-5.6-sol"},
        {"type": "item.completed", "thread_id": "session-a", "item": {"type": "command_execution", "id": "1", "command": "pytest", "exit_code": 0}},
        {"type": "turn.completed", "thread_id": "session-a", "usage": {"input_tokens": 3, "output_tokens": 2}, "report": {"status": "PASS"}},
    ]
    normalized = subject.normalize_events(events, "session-a", "gpt-5.6-sol")
    assert normalized[-1]["type"] == "turn.completed"
    assert subject.final_report(events, "session-a") == {"status": "PASS"}
    with pytest.raises(TransportError, match="mixed|owned"):
        subject.normalize_events([*events, {"type": "item.completed", "thread_id": "other", "item": {}}], "session-a")
    with pytest.raises(TransportError, match="usage"):
        subject.normalize_events([{**events[0]}, {"type": "turn.completed", "thread_id": "session-a", "usage": {}}], "session-a")
    with pytest.raises(TransportError, match="terminal"):
        subject.final_report(events[:-1], "session-a")
    with pytest.raises(TransportError, match="model route"):
        subject.normalize_events(events, "session-a", "gpt-5.6-terra")


def test_dashboard_contract_validates_catalogue_task_arguments_and_project_free_conversation(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], tmp_path: Path
) -> None:
    subject, runner, codex, _shim = transport
    assert subject.dashboard_catalogue(tmp_path) == sorted(APPROVED_GPT_MODELS)
    models = subject.validate_dashboard_models({"terra_model": "gpt-5.6-terra", "sol_model": "gpt-5.6-sol"})
    arguments = subject.task_arguments(models, {"terra_reasoning_effort": "high"})
    assert arguments == ["--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol", "--terra-reasoning-effort", "high"]
    conversation = subject.prepare_conversation(tmp_path, tmp_path, "gpt-5.6-luna", "medium", subject.identity(tmp_path))
    assert conversation.command[0] == str(codex)
    assert "read-only" in conversation.command
    with pytest.raises(TransportError, match="supported GoCode GPT"):
        subject.validate_dashboard_models({"sol_model": CLAUDE_MODEL})
    with pytest.raises(TransportError, match="project-free"):
        subject.prepare_conversation(tmp_path / "project", tmp_path, "gpt-5.6-sol", "medium", subject.identity(tmp_path))
    runner.catalogue = ["unmanaged/model"]
    with pytest.raises(TransportError, match="unsupported"):
        subject.dashboard_catalogue(tmp_path)


def test_prompt_adaptation_and_route_parsing_fail_closed(transport: tuple[GoCodeTransport, FakeGoCode, Path, Path]) -> None:
    subject, _runner, _codex, _shim = transport
    prompt = subject.adapt_prompt("work carefully", {"type": "object"})
    assert "GOCODE OUTPUT CONTRACT" in prompt
    assert "work carefully" in prompt
    with pytest.raises(TransportError, match="unsupported"):
        subject.validate_roles({"sol": {"model": "openai/gpt-5.6-sol"}})
    with pytest.raises(TransportError, match="reasoning"):
        subject.validate_roles({"sol": {"reasoning_effort": "unsafe"}})


@pytest.mark.parametrize(("role", "model", "effort", "resolved"), [
    ("glm", "gpt-5.6-sol", "medium", "gpt-5.6-sol"),
    ("astra", "gpt-5.6-sol", "high", "gpt-5.6-sol"),
    ("astra", "gpt-5.6-sol", "xhigh", "gpt-5.6-sol"),
    ("astra", "claude-opus-5", "high", CLAUDE_MODEL),
    ("astra", "openai/gpt-6-astra", "high", CLAUDE_MODEL),
    ("terra", "gpt-5.6-terra", "medium", "gpt-5.6-terra"),
    ("terra", "gpt-5.6-terra", "high", "gpt-5.6-terra"),
    ("terra", "gpt-5.6-terra", "xhigh", "gpt-5.6-terra"),
    ("terra", "gpt-5.6-terra", "max", "gpt-5.6-terra"),
    ("sol", "gpt-5.6-sol", "high", "gpt-5.6-sol"),
    ("sol", "gpt-5.6-sol", "xhigh", "gpt-5.6-sol"),
    ("sol", "claude-opus-5", "high", CLAUDE_MODEL),
    ("sol", "openai/gpt-6-astra", "high", CLAUDE_MODEL),
    ("completion", "gpt-5.6-sol", "medium", "gpt-5.6-sol"),
    ("completion", "gpt-5.6-sol", "high", "gpt-5.6-sol"),
    ("completion", "claude-opus-5", "high", CLAUDE_MODEL),
    ("completion", "openai/gpt-6-astra", "high", CLAUDE_MODEL),
])
def test_every_authoritative_role_default_and_escalation_rung_routes_exactly(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], role: str, model: str, effort: str, resolved: str
) -> None:
    subject, _runner, _codex, _shim = transport
    route = subject.validate_roles({role: {"model": model, "reasoning_effort": effort}})[role]
    assert (route.requested_model, route.resolved_model, route.effort) == (model, resolved, effort)


@pytest.mark.parametrize("planning,report_repair,sandbox", [
    (True, False, "read-only"), (False, True, "read-only"), (False, False, "workspace-write"),
])
def test_typed_run_role_request_preserves_full_upstream_launch_seam(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], tmp_path: Path,
    planning: bool, report_repair: bool, sandbox: str,
) -> None:
    subject, _runner, codex, _shim = transport
    request = launch_request(tmp_path, role="terra", sandbox=sandbox, model="gpt-5.6-terra", effort="high",
                             planning=planning, report_repair=report_repair,
                             session=None if planning or report_repair else "saved")
    route = subject.validate_roles({"terra": {"model": request.model, "reasoning_effort": request.effort}})["terra"]
    spec = subject.prepare_launch(request, route, subject.identity(tmp_path))
    assert spec.request == request
    assert spec.command[0] == str(codex)
    assert ["--sandbox", sandbox] == spec.command[spec.command.index("--sandbox"):spec.command.index("--sandbox") + 2]
    assert str(request.schema) in spec.command and str(request.output) in spec.command
    if request.session:
        assert request.session in spec.command
    assert request.events == spec.request.events
    assert request.prompt in spec.prompt and "GOCODE OUTPUT CONTRACT" in spec.prompt


@pytest.mark.parametrize("target", ["gocode", "codex", "claude_shim"])
@pytest.mark.parametrize("replacement", [True, False])
def test_spawn_rejects_changes_before_final_identity_check(transport, tmp_path, target, replacement):
    subject, _, _, _ = transport
    checkpoint = subject.identity(tmp_path)
    request = launch_request(tmp_path)
    prepared = subject.prepare_launch(request, subject.validate_roles({"sol": {}})["sol"], checkpoint)
    calls = []
    subject._spawn = lambda command, **kwargs: calls.append(command)
    def mutate(paths):
        path = Path(paths[target])
        if replacement:
            substitute = executable(path.with_name(path.name + ".replacement"), "#!/bin/sh\n# changed\n")
            os.replace(substitute, path)
        else:
            path.write_text("#!/bin/sh\n# changed in place\n")
    subject._before_spawn = mutate
    with pytest.raises(TransportError, match="digest|changed|identity"):
        subject.spawn(prepared, tmp_path, checkpoint)
    assert calls == []


def test_canonical_launch_does_not_need_dev_fd(transport, tmp_path):
    subject, runner, codex, _ = transport
    checkpoint = subject.identity(tmp_path)
    request = launch_request(tmp_path)
    spec = subject.prepare_launch(request, subject.validate_roles({"sol": {}})["sol"], checkpoint)
    calls = []
    subject._spawn = lambda command, **kwargs: calls.append((command, kwargs))
    subject.spawn(spec, tmp_path, checkpoint)
    assert calls[0][0][0] == str(codex)
    assert "pass_fds" not in calls[0][1]
    assert all(not any("/dev/fd/" in arg for arg in command) for command in runner.calls)


@pytest.mark.parametrize("planning,report_repair,sandbox", [
    (True, False, "read-only"), (False, True, "read-only"), (False, False, "workspace-write"),
])
def test_gocode_spawn_preserves_the_complete_request_stream_bindings(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], tmp_path: Path,
    planning: bool, report_repair: bool, sandbox: str,
) -> None:
    subject, _runner, _codex, _shim = transport
    request = launch_request(
        tmp_path, role="terra", sandbox=sandbox, model="gpt-5.6-terra", effort="high",
        session=None if planning or report_repair else "saved", planning=planning, report_repair=report_repair,
    )
    route = subject.validate_roles({"terra": {"model": request.model, "reasoning_effort": request.effort}})["terra"]
    checkpoint = subject.identity(tmp_path)
    prepared = subject.prepare_launch(request, route, checkpoint)
    calls: list[tuple[list[str], dict[str, object]]] = []

    class RecordingInput(io.StringIO):
        def close(self) -> None:
            pass

    class RecordingChild:
        stdin = RecordingInput()

    child = RecordingChild()

    def spawn(command: list[str], **kwargs: object) -> RecordingChild:
        calls.append((command, kwargs))
        return child

    subject._spawn = spawn
    assert subject.spawn(prepared, tmp_path, checkpoint) is child
    command, kwargs = calls.pop()
    assert "--sandbox" in command and command[command.index("--sandbox") + 1] == sandbox
    assert child.stdin.getvalue() == prepared.prompt
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["stdin"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["text"] is True
    assert kwargs["start_new_session"] is True
    assert "pass_fds" not in kwargs
    assert request.events.is_file()


@pytest.mark.parametrize("planning,report_repair,sandbox", [
    (True, False, "read-only"), (False, True, "read-only"), (False, False, "workspace-write"),
])
def test_native_spawn_uses_the_same_prompt_and_event_stream_bindings_as_gocode(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, planning: bool, report_repair: bool, sandbox: str,
) -> None:
    source = Path(os.environ["AUTOCODE_TEST_UPSTREAM"]) / "tools" / "autocode_opencode.py"
    spec = importlib.util.spec_from_file_location("untouched_opencode_stream_contract", source)
    assert spec and spec.loader
    upstream = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(upstream)
    monkeypatch.setattr(upstream, "local_settings", lambda _workspace: {"engine": "opencode"})
    monkeypatch.setattr(upstream, "transport_drift", lambda _current, _checkpoint: False)
    monkeypatch.setattr(upstream, "check_models", lambda _roles: None)
    monkeypatch.setattr(
        upstream, "launch",
        lambda *_args, **_kwargs: (["opencode", "run"], {"PATH": "/bin"}, {}),
    )
    monkeypatch.setattr(
        upstream, "prompt_for_schema",
        lambda prompt, _schema, _events: "ADAPTED: " + prompt,
    )
    subject = NativeOpenCodeTransport.from_upstream_module(upstream)
    request = launch_request(
        tmp_path, sandbox=sandbox, session=None if planning or report_repair else "saved",
        planning=planning, report_repair=report_repair,
    )
    route = RoleRoute("openai/gpt-5.6-sol", "openai/gpt-5.6-sol", "high")
    prepared = subject.prepare_launch(request, route, subject.identity(tmp_path))
    calls: list[tuple[list[str], dict[str, object]]] = []

    class RecordingInput(io.StringIO):
        def close(self) -> None:
            pass

    class RecordingChild:
        stdin = RecordingInput()

    child = RecordingChild()

    def spawn(command: list[str], **kwargs: object) -> RecordingChild:
        calls.append((command, kwargs))
        return child

    monkeypatch.setattr(transport_module.subprocess, "Popen", spawn)
    assert subject.spawn(prepared, tmp_path, subject.identity(tmp_path)) is child
    command, kwargs = calls.pop()
    assert command == ["opencode", "run"]
    assert child.stdin.getvalue() == "ADAPTED: " + request.prompt
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["env"] == {"PATH": "/bin"}
    assert kwargs["stdin"] is subprocess.PIPE
    assert kwargs["stderr"] is subprocess.STDOUT
    assert kwargs["text"] is True
    assert kwargs["start_new_session"] is True
    assert request.events.is_file()


def test_prepared_route_is_acquired_from_verified_gocode_bytes_and_credential_drift_fails_closed(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], tmp_path: Path,
) -> None:
    subject, runner, _codex, _shim = transport
    checkpoint = subject.identity(tmp_path)
    live_gocode = str(checkpoint["gocode"]["path"])
    assert all(command[0] == live_gocode for command in runner.calls)
    call_count = len(runner.calls)
    route = subject.validate_roles({"sol": {}})["sol"]
    spec = subject.prepare_launch(launch_request(tmp_path), route, checkpoint)

    # Recheck the canonical installed executable; no descriptor-based execution.
    assert all(command[0] == live_gocode for command in runner.calls[call_count:])
    runner.exports = "export OPENAI_API_KEY=replaced-token\nexport OPENAI_BASE_URL=https://managed.example.test/v1\n"
    with pytest.raises(TransportError, match="credential|route snapshot|changed"):
        subject.spawn(spec, tmp_path, checkpoint)


@pytest.mark.parametrize(("role", "model", "effort"), [
    ("sol", "gpt-5.6-sol", "high"),
    ("completion", "openai/gpt-6-astra", "medium"),
])
def test_spawn_supplies_adapted_prompt_and_combined_text_events_stream(
    transport: tuple[GoCodeTransport, FakeGoCode, Path, Path], tmp_path: Path,
    role: str, model: str, effort: str,
) -> None:
    subject, _runner, codex, shim = transport
    executable(codex, "#!/bin/sh\ncat\nprintf child-stderr >&2\n")
    executable(shim, "#!/bin/sh\ncat\nprintf child-stderr >&2\n")
    gocode = tmp_path / "gocode"
    subject.manifest = CompatibilityManifest.from_dict({
        "version": 1,
        "probes": [{"path": "runner.py", "contains": "Runner"}],
        "gocode": [{
            "version": "1.2.3", "gocode_sha256": digest(gocode),
            "gocode_real_path": str(gocode), "gocode_real_sha256": digest(gocode),
            "codex_path": str(codex), "codex_sha256": digest(codex),
            "shim_path_regex": "^" + str(shim) + "$", "shim_sha256": digest(shim),
        }],
    })
    checkpoint = subject.identity(tmp_path)
    request = launch_request(tmp_path, role=role, model=model, effort=effort, session=None)
    route = subject.validate_roles({role: {"model": model, "reasoning_effort": effort}})[role]
    process = subject.spawn(subject.prepare_launch(request, route, checkpoint), tmp_path, checkpoint)
    assert process.wait(timeout=5) == 0
    captured = request.events.read_text(encoding="utf-8")
    assert "GOCODE OUTPUT CONTRACT" in captured
    assert request.prompt in captured
    assert "child-stderr" in captured
