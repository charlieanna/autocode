from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocode_provider_adapter import launcher


def test_gocode_plugin_exposes_the_core_provider_factory() -> None:
    import autocode_provider_gocode
    provider = autocode_provider_gocode.create_provider()
    assert provider.DEFAULT_MODELS["terra"] == "openai/gpt-5.6-terra"
    assert callable(provider.launch)


def test_run_forwards_arguments_to_the_untouched_runner(monkeypatch, tmp_path: Path) -> None:
    calls = []
    class Runner:
        @staticmethod
        def cli():
            calls.append(list(launcher.sys.argv))
            return 7
    monkeypatch.setattr(launcher, "_runner", lambda _checkout, _record, _provider: Runner)
    record = tmp_path / "pin.json"
    result = launcher.main(["run", "--checkout", str(tmp_path), "--record", str(record),
                            "--provider", "gocode", "--", "task", "--workspace", "/work"])
    assert result == 7
    assert calls == [[str(tmp_path / "tools/autocode.py"), "--provider", "gocode", "task", "--workspace", "/work"]]


def test_dashboard_runner_forwards_the_dashboard_provider(monkeypatch, tmp_path: Path) -> None:
    import runpy
    import sys
    calls = []
    monkeypatch.setattr(launcher, "main", lambda argv: calls.append(list(argv)) or 0)
    monkeypatch.setenv("AUTOCODE_GOCODE_CHECKOUT", str(tmp_path / "checkout"))
    monkeypatch.setenv("AUTOCODE_GOCODE_PIN_RECORD", str(tmp_path / "pin.json"))
    monkeypatch.setenv("AUTOCODE_PROVIDER", "gocode")
    runner = Path(launcher.__file__).with_name("dashboard_runner.py")
    for argv in (["--workspace", "/work", "--run-dir", "/work/.autocode/runs/r"], ["--models"]):
        monkeypatch.setattr(sys, "argv", [str(runner), *argv])
        with pytest.raises(SystemExit) as exit_info:
            runpy.run_path(str(runner), run_name="__main__")
        assert exit_info.value.code == 0
    assert calls[0] == ["run", "--provider", "gocode", "--checkout", str(tmp_path / "checkout"),
                        "--record", str(tmp_path / "pin.json"), "--",
                        "--workspace", "/work", "--run-dir", "/work/.autocode/runs/r"]
    assert calls[1][:3] == ["models", "--provider", "gocode"]


def test_opencode_is_a_native_provider_and_other_providers_use_plugins(monkeypatch, tmp_path: Path) -> None:
    manifest = launcher.CompatibilityManifest.default()
    monkeypatch.setattr(launcher, "_verify_record", lambda *_args: None)
    monkeypatch.setattr(launcher.CompatibilityManifest, "verify", lambda _self, _checkout: None)
    monkeypatch.setattr(launcher.CompatibilityManifest, "default", lambda: manifest)
    sentinel = object()
    monkeypatch.setattr(launcher, "load_native_upstream_runner", lambda _checkout: sentinel)
    assert launcher._runner(tmp_path, tmp_path / "pin.json", "opencode") is sentinel
    with pytest.raises(launcher.TransportError, match="provider plugin"):
        launcher._runner(tmp_path, tmp_path / "pin.json", "kilocode")


def test_models_print_provider_qualified_catalogue(monkeypatch, tmp_path: Path, capsys) -> None:
    class Transport:
        def dashboard_catalogue(self, _workspace):
            return ["gpt-5.6-sol", "gpt-5.6-terra"]
    monkeypatch.setattr(launcher, "_transport", lambda: Transport())
    assert launcher.main(["models", "--workspace", str(tmp_path)]) == 0
    assert capsys.readouterr().out.splitlines() == ["openai/gpt-5.6-sol", "openai/gpt-5.6-terra"]


def test_pin_record_rejects_checkout_or_manifest_drift(monkeypatch, tmp_path: Path) -> None:
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    record = tmp_path / "pin.json"
    manifest = launcher.CompatibilityManifest.default()
    record.write_text(json.dumps({
        "upstream_commit": "expected", "compatibility_manifest": manifest.identity,
    }), encoding="utf-8")
    class Result:
        returncode = 0
        stdout = "different\n"
        stderr = ""
    monkeypatch.setattr(launcher.subprocess, "run", lambda *_args, **_kwargs: Result())
    with pytest.raises(launcher.SyncError, match="pinned to expected"):
        launcher._verify_record(checkout, record, manifest)
