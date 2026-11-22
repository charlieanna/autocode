"""Exact offline-provider bytes for the retained-report editor fixture."""

import hashlib
import json
import os
from pathlib import Path

REFERENCE = {
    "labels.py": "1950da2d0077d5b3eca5ac7dd0d359a88e390a261fd74b5c6f1aaa04cf7a13c5",
    "test_added.py": "5b8b59f7ba3165761c2959a5c74d57a963aa9592d77d7255625f8af589937c04",
    "test_existing.py": "f0b3a4112f2dc4fdc7a0dc9e84cd99d2985d4961f8224b5271f6e37dffe7249b",
}


def scripted_provider(base: str) -> str:
    """Emit a full repair and record calls, while keeping real command events."""
    report = "def _report(report, output):\n"
    stage = '    if data.get("report_repair"):\n        # Report repairs are answered as the stage that owns them.\n        stage = original.get("stage", stage)\n'
    if base.count(report) != 1 or base.count(stage) != 1:
        raise ValueError("The fixture's scripted provider changed")
    base = base.replace(
        report,
        report
        + """    if DATA.get("report_repair") and (DATA.get("original") or {}).get("stage") == "tester" and isinstance(report.get("repair_report"), dict):
        Path(output).write_text(json.dumps(report["repair_report"]))
        return
""",
    )
    return base.replace(
        stage,
        stage
        + """    with Path(os.environ["AUTOCODE_REPORT_FAKE_TRACE"]).open("a") as trace:
        trace.write(json.dumps({"stage": stage, "repair": bool(data.get("report_repair"))}) + "\\n")
""",
    )


def verify(root: Path, repo: Path, env) -> None:
    """Refuse before launching the CLI unless its provider is the owned fake."""
    root = root.resolve(strict=True)
    bindir = root / "bin"
    provider, helper = bindir / "codex", bindir / "fake_changed_code.py"
    config, trace, manifest = root / "fake-config.json", root / "provider-stages.jsonl", root / "provider-manifest.json"
    if (
        any(path.is_symlink() for path in (bindir, provider, helper, config, trace, manifest))
        or not all(path.is_file() for path in (provider, helper, config, trace, manifest))
        or not os.access(provider, os.X_OK)
        or provider.read_text() != scripted_provider((repo / "scenarios/harness/fake_codex.py").read_text())
        or helper.read_bytes() != (repo / "scenarios/harness/fake_changed_code.py").read_bytes()
        or env.get("SCENARIO_FAKE_CONFIG") != str(config)
        or env.get("AUTOCODE_REPORT_FAKE_TRACE") != str(trace)
        or env.get("AUTOCODE_HOME") != str(root / "registry")
        or env.get("AUTOCODE_SOURCE_HOST") != "0"
    ):
        raise ValueError("The retained-report fixture cannot identify its owned offline provider")
    try:
        recorded = json.loads(manifest.read_text())
        settings = json.loads(config.read_text())
        digest = hashlib.sha256(config.read_bytes()).hexdigest()
    except (OSError, ValueError) as error:
        raise ValueError("The retained-report fixture's config pin is unavailable") from error
    reference = root / "reference"
    if (
        recorded != {"config_sha256": digest}
        or not isinstance(settings, dict)
        or settings.get("reference") != str(reference)
        or settings.get("paths") != sorted(REFERENCE)
        or settings.get("check") != "python3 -m unittest -v test_added test_existing"
        or settings.get("fault") != ""
        or reference.is_symlink()
        or not reference.is_dir()
        or any(
            (path := reference / name).is_symlink()
            or not path.is_file()
            or hashlib.sha256(path.read_bytes()).hexdigest() != expected
            for name, expected in REFERENCE.items()
        )
    ):
        raise ValueError("The retained-report fixture's config changed")
