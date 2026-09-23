from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import sys

import pytest

from autocode_gocode_adapter.compatibility import CompatibilityManifest
from autocode_gocode_adapter.runtime import GoCodeFacade, load_upstream_runner
from autocode_gocode_adapter.transport import LaunchSpec, RoleRoute


class FakeTransport:
    def __init__(self) -> None:
        self.requests = []

    def identity(self, _workspace):
        return {"engine": "gocode", "version": "test", "route_fingerprint": "fingerprint"}

    def transport_drift(self, current, checkpoint):
        return current != checkpoint

    def validate_roles(self, roles):
        result = {}
        for role, config in roles.items():
            model = config.get("model", "openai/gpt-5.6-sol").removeprefix("openai/")
            effort = config.get("reasoning_effort") or ("medium" if role in {"glm", "terra"} else "high")
            result[role] = RoleRoute(config.get("model", model), model, effort)
        return result

    def prepare_launch(self, request, route, checkpoint):
        self.requests.append((request, route, checkpoint))
        return LaunchSpec(["codex"], {}, checkpoint, request, route, "adapted:" + request.prompt, "fingerprint")

    def dashboard_catalogue(self, _workspace):
        return ["gpt-5.6-sol", "gpt-5.6-terra"]


def test_facade_completes_the_upstream_launch_seam_without_copying_run_role(tmp_path: Path) -> None:
    subject = GoCodeFacade(FakeTransport())
    command, environment, overrides = subject.launch(
        "terra", tmp_path, tmp_path, "saved", "openai/gpt-5.6-terra", "medium", True,
    )
    events = tmp_path / "iterations/001/terra-01.jsonl"
    events.parent.mkdir(parents=True)
    prompt = subject.prompt_for_schema("work", {"type": "object"}, events)
    assert prompt == "adapted:work"
    assert command[:3] == [sys.executable, "-m", "autocode_gocode_adapter.child"]
    descriptor = Path(command[3])
    document = json.loads(descriptor.read_text())
    assert document["request"]["workspace"] == str(tmp_path)
    assert document["request"]["output"] == str(events.with_suffix(".json"))
    assert document["request"]["sandbox"] == "workspace-write"
    assert document["route"] == {"requested_model": "gpt-5.6-terra", "resolved_model": "gpt-5.6-terra", "effort": "medium"}
    assert "OPENAI_API_KEY" not in json.dumps(document)
    assert environment["PYTHONPATH"].split(os.pathsep)[0].endswith("gocode_adapter/src")
    assert overrides["transport"] == "gocode"


def test_facade_defaults_and_report_use_real_upstream_artifacts(tmp_path: Path) -> None:
    subject = GoCodeFacade(FakeTransport())
    assert subject.DEFAULT_MODELS == {
        "glm": "openai/gpt-5.6-sol", "astra": "openai/gpt-5.6-sol",
        "terra": "openai/gpt-5.6-terra", "sol": "openai/gpt-5.6-sol",
        "completion": "openai/gpt-5.6-sol",
    }
    assert subject.DEFAULT_REASONING_EFFORTS["completion"] == "high"
    events = tmp_path / "sol-01.jsonl"
    events.write_text('{"type":"thread.started","thread_id":"abc"}\n', encoding="utf-8")
    events.with_suffix(".json").write_text('{"status":"PASS"}\n', encoding="utf-8")
    assert subject.final_report(events) == {"status": "PASS"}


def test_runner_loader_injects_facade_and_does_not_modify_upstream(tmp_path: Path) -> None:
    upstream = Path(os.environ["AUTOCODE_TEST_UPSTREAM"])
    runner = upstream / "tools/autocode.py"
    before = hashlib.sha256(runner.read_bytes()).hexdigest()
    facade = GoCodeFacade(FakeTransport())
    module = load_upstream_runner(upstream, facade)
    assert module.opencode is facade
    assert hashlib.sha256(runner.read_bytes()).hexdigest() == before


def test_facade_rejects_launch_prompt_pairing_errors(tmp_path: Path) -> None:
    subject = GoCodeFacade(FakeTransport())
    with pytest.raises(RuntimeError, match="matching launch"):
        subject.prompt_for_schema("work", {}, tmp_path / "events.jsonl")
    subject.launch("sol", tmp_path, tmp_path, None, "openai/gpt-5.6-sol", "high", False)
    with pytest.raises(RuntimeError, match="pending"):
        subject.launch("sol", tmp_path, tmp_path, None, "openai/gpt-5.6-sol", "high", False)


def test_conversation_uses_tool_free_managed_responses_request(tmp_path: Path) -> None:
    transport = FakeTransport()
    transport._current = lambda *_args: (
        transport.identity(tmp_path),
        {"OPENAI_BASE_URL": "https://managed.example/v1", "OPENAI_API_KEY": "secret"},
    )
    captured = {}
    class Response:
        def __enter__(self): return self
        def __exit__(self, *_args): return None
        def read(self): return b'{"output_text":"a concise plan"}'
    def open_request(request, timeout):
        captured.update(url=request.full_url, body=json.loads(request.data), timeout=timeout)
        return Response()
    subject = GoCodeFacade(transport, urlopen=open_request)
    assert subject.conversation_provider("upstream prompt", "openai/gpt-5.6-sol", tmp_path) == "a concise plan"
    assert captured["url"] == "https://managed.example/v1/responses"
    assert captured["body"]["tools"] == [] and captured["body"]["store"] is False
    assert captured["body"]["input"] == "upstream prompt"
    assert "secret" not in json.dumps(captured["body"])
