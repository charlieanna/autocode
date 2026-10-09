"""Rerun native metadata regressions without provider inference or user credentials."""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[2]
original = ROOT / "audits/opencode-2026-09-19/autocode_opencode_audit_probes.py"
spec = importlib.util.spec_from_file_location("audit_probes", original)
probes = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probes)


def model_context():
    root, workspace, env = probes.fixture()
    (workspace / "opencode.json").write_text(json.dumps({"provider": {"audit-fixture": {
        "npm": "@ai-sdk/openai-compatible", "name": "Offline fixture",
        "options": {"baseURL": "http://127.0.0.1:9/v1", "apiKey": "audit-placeholder"},
        "models": {"audit-model": {"name": "Audit model"}}}}}))
    previous = Path.cwd()
    try:
        os.chdir(root)
        with patch.dict(os.environ, env, clear=True):
            probes.oc.check_models({"sol": {"model": "audit-fixture/audit-model"}}, workspace)
    finally:
        os.chdir(previous)
    return {"fixture": str(root), "caller_differs_from_workspace": True, "target_model_accepted": True}


if __name__ == "__main__":
    result = {
        "opencode_version": subprocess.check_output(["opencode", "--version"], text=True).strip(),
        "hosted_model_requests": 0,
        "inline_permission_override": probes.inline_permission_probe(),
        "custom_config_drift": probes.config_drift_probe(),
        "project_model_context": model_context(),
        "malformed_report_recovery": probes.malformed_report_probe("The implementation is finished."),
        "wrong_schema_recovery": probes.malformed_report_probe('{"summary":"missing required fields"}'),
    }
    assert result["inline_permission_override"]["after"] == {"bash": False, "webfetch": False}
    assert not result["custom_config_drift"]["identity_equal_after_permission_change"]
    for name in ("malformed_report_recovery", "wrong_schema_recovery"):
        attempts = result[name]["attempts"]
        assert len(attempts) == 1 and not attempts[0]["active_stage_retained"] and attempts[0]["archived_attempts"] == 1
    target = Path(__file__).with_name("native-results.json")
    target.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
