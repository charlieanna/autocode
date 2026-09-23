from __future__ import annotations

from pathlib import Path

import pytest

from autocode_provider_adapter.compatibility import CompatibilityError, CompatibilityManifest


def test_manifest_identity_is_stable_and_probes_are_versioned(tmp_path: Path) -> None:
    root = tmp_path / "upstream"
    root.mkdir()
    (root / "runner.py").write_text("class Runner: pass\n")
    manifest = CompatibilityManifest.from_dict(
        {"version": 1, "probes": [{"path": "runner.py", "contains": "class Runner"}]}
    )
    assert manifest.identity == manifest.identity
    manifest.verify(root)


def test_manifest_rejects_missing_or_invalid_probe(tmp_path: Path) -> None:
    manifest = CompatibilityManifest.from_dict(
        {"version": 1, "probes": [{"path": "missing.py", "contains": "seam"}]}
    )
    with pytest.raises(CompatibilityError, match="missing.py"):
        manifest.verify(tmp_path)
    with pytest.raises(CompatibilityError, match="version"):
        CompatibilityManifest.from_dict({"version": 2, "probes": []})


def test_bundled_manifest_defines_runner_and_dashboard_seams() -> None:
    manifest = CompatibilityManifest.default()
    paths = {probe.path for probe in manifest.probes}
    assert manifest.version == 1
    assert "tools/autocode_opencode.py" in paths
    assert "tools/dashboard/agent_console.py" in paths


def test_python_probe_requires_a_real_top_level_definition_not_a_comment(tmp_path: Path) -> None:
    root = tmp_path / "upstream"
    root.mkdir()
    source = root / "runner.py"
    source.write_text("# def launch(\ndef other(): pass\n", encoding="utf-8")
    manifest = CompatibilityManifest.from_dict(
        {"version": 1, "probes": [{"path": "runner.py", "contains": "def launch("}]}
    )
    with pytest.raises(CompatibilityError, match="definition.*launch"):
        manifest.verify(root)
    source.write_text("def launch(): pass\n", encoding="utf-8")
    manifest.verify(root)


def test_structural_probe_rejects_signature_drift(tmp_path: Path) -> None:
    root = tmp_path / "upstream"
    root.mkdir()
    source = root / "runner.py"
    source.write_text("def launch(role, workspace, *, unsafe=False): pass\n", encoding="utf-8")
    manifest = CompatibilityManifest.from_dict({
        "version": 1,
        "probes": [{"path": "runner.py", "contains": "def launch(",
                    "parameters": ["role", "workspace", "planning"]}],
    })
    with pytest.raises(CompatibilityError, match="signature.*launch"):
        manifest.verify(root)
