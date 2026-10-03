#!/usr/bin/env python3
"""One conformance contract across Codex, OpenCode and KiloCode.

Offline: python tools/provider_conformance.py --fake
Live:    python tools/provider_conformance.py --route codex=gpt-6-luna \
           --i-authorize-live-model-spend
Evidence is saved under .scenario-runs; there are no automatic model retries.
"""
from __future__ import annotations

import argparse
import json
import math
import os
from pathlib import Path
import subprocess
import tempfile
import time

try:
    from . import provider_conformance_contract as contract, provider_conformance_transport as transport
    from . import provider_conformance_fake as fake
    from .autocode_util import atomic_json, file_hash, snapshot
except ImportError:
    import provider_conformance_contract as contract, provider_conformance_transport as transport
    import provider_conformance_fake as fake
    from autocode_util import atomic_json, file_hash, snapshot


PROVIDERS = ("codex", "opencode", "kilocode")


def run_route(name, model, directory, *, effort, timeout, env, fake_mode=False):
    directory.mkdir()
    workspace, nonce = contract.fixture(directory)
    result = {"provider": name, "model": model, "effort": effort,
              "mode": "fake" if fake_mode else "live", "workspace": str(workspace), "phases": []}
    started = time.monotonic()
    try:
        adapter = transport.adapter_for(name, fake=fake_mode)
        result["identity"] = transport.preflight(name, model, adapter, workspace, env, fake=fake_mode)
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
        result.update(status="UNAVAILABLE", error=str(error))
    else:
        session = None
        for phase in ("build", "validate", "resume"):
            data = contract.handoff(workspace, phase)
            stage_dir = workspace / ".autocode" / phase
            expected_session = session if phase == "resume" else None
            before = snapshot(workspace)
            try:
                execution = transport.execute(
                    adapter=adapter, workspace=workspace, directory=stage_dir, model=model, effort=effort,
                    session=expected_session, allow_write=phase == "build", prompt=contract.prompt(data),
                    schema=contract.SCHEMA, env=env, timeout=timeout)
                path = workspace / "output.txt"
                output = path.read_bytes() if path.is_file() and not path.is_symlink() else None
                checked = contract.assess(
                    report=execution["report"], rows=execution["rows"], data=data, nonce=nonce,
                    before=before, after=snapshot(workspace), output=output, expected_session=expected_session)
                checked.update(phase=phase, artifacts=str(stage_dir), metrics=execution["metrics"])
                if execution["exit_code"] or execution["timed_out"] or execution["report_error"]:
                    checked.update(status="FAIL", process_exit=execution["exit_code"],
                                   timed_out=execution["timed_out"], report_error=execution["report_error"])
                if phase == "validate" and checked["session"] == session:
                    checked["failures"].append("validator_reused_builder_session")
                    checked["status"] = "FAIL"
            except KeyboardInterrupt:
                checked = {"phase": phase, "status": "INTERRUPTED", "artifacts": str(stage_dir)}
            except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as error:
                checked = {"phase": phase, "status": "FAIL", "error": str(error), "artifacts": str(stage_dir)}
            atomic_json(stage_dir / "result.json", checked)
            result["phases"].append(checked)
            print(f"{name} {model} {phase}: {checked['status']}", flush=True)
            if checked["status"] != "PASS":
                break
            session = checked["session"]
        result["status"] = "PASS" if len(result["phases"]) == 3 and all(
            p["status"] == "PASS" for p in result["phases"]) else "FAIL"
        if result["phases"][-1]["status"] == "INTERRUPTED":
            result["status"] = "INTERRUPTED"
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    atomic_json(directory / "result.json", result)
    return result


def route(value):
    name, separator, model = value.partition("=")
    if name not in PROVIDERS or not separator or not model or any(c.isspace() for c in model):
        raise argparse.ArgumentTypeError("Use codex=MODEL, opencode=PROVIDER/MODEL or kilocode=PROVIDER/MODEL")
    return name, model


def source_identity():
    root = Path(__file__).resolve().parent
    revision = subprocess.run(["git", "rev-parse", "HEAD"], cwd=root,
                              capture_output=True, text=True)
    files = [*root.glob("provider_conformance*.py"), root / "autocode_support.py",
             root / "autocode_process.py", root / "autocode_util.py",
             *root.glob("providers/*.py"), root / "providers/configs/kilocode.toml"]
    return {"revision": revision.stdout.strip() if revision.returncode == 0 else None,
            "file_sha256": {str(p.relative_to(root)): file_hash(p) for p in sorted(files)}}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--fake", action="store_true", help="all three transports, no model/network requests")
    parser.add_argument("--fake-fault", choices=fake.FAULTS, help="exercise a rejecting oracle (requires --fake)")
    parser.add_argument("--route", action="append", type=route, default=[])
    parser.add_argument("--i-authorize-live-model-spend", action="store_true")
    parser.add_argument("--effort", choices=("low", "medium", "high"), default="medium")
    parser.add_argument("--timeout", type=float, default=180, help="maximum seconds per provider turn")
    parser.add_argument("--output", type=Path, default=Path(".scenario-runs/provider-conformance"))
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be positive and finite")
    if args.fake_fault and not args.fake:
        parser.error("--fake-fault requires --fake")
    if args.fake and args.route:
        parser.error("--fake selects all three local fake routes; omit --route")
    if not args.fake and (not args.route or not args.i_authorize_live_model_spend):
        parser.error("Live probes require --route and --i-authorize-live-model-spend")
    args.output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="fake-" if args.fake else "live-", dir=args.output.resolve()))
    env = dict(os.environ)
    if args.fake:
        env = fake.environment(directory, env, args.fake_fault)
    routes = [(p, "probe" if p == "codex" else "test/probe") for p in PROVIDERS] if args.fake else args.route
    results = []
    summary = {"contract_version": 1, "mode": "fake" if args.fake else "live",
               "source": source_identity(), "routes": results}
    for index, (name, model) in enumerate(routes):
        results.append(run_route(name, model, directory / f"{index + 1}-{name}", effort=args.effort,
                                 timeout=args.timeout, env=env, fake_mode=args.fake))
        atomic_json(directory / "summary.json", summary)
        if results[-1]["status"] == "INTERRUPTED":
            break
    summary["status"] = "PASS" if all(r["status"] == "PASS" for r in results) else "FAIL"
    atomic_json(directory / "summary.json", summary)
    print(json.dumps({"status": summary["status"], "report": str(directory / "summary.json")}), flush=True)
    return 130 if any(r["status"] == "INTERRUPTED" for r in results) else 0 if summary["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
