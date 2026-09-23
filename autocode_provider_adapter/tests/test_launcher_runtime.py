from __future__ import annotations

import json
from pathlib import Path

import pytest

from autocode_gocode_adapter import launcher


def test_run_forwards_arguments_to_the_untouched_runner(monkeypatch, tmp_path: Path) -> None:
    calls = []
    class Runner:
        @staticmethod
        def cli():
            calls.append(list(launcher.sys.argv))
            return 7
    monkeypatch.setattr(launcher, "_runner", lambda _checkout, _record: Runner)
    record = tmp_path / "pin.json"
    result = launcher.main(["run", "--checkout", str(tmp_path), "--record", str(record),
                            "--", "task", "--workspace", "/work"])
    assert result == 7
    assert calls == [[str(tmp_path / "tools/autocode.py"), "task", "--workspace", "/work"]]


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
