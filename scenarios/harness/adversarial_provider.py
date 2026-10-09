#!/usr/bin/env python3
"""Install narrowly scoped scripted-provider faults; AutoCode runs unchanged."""
import importlib
import json
import os
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from harness import fake_codex as fake

attack = fake.CONFIG.get("adversarial", {})
root = Path(attack["root"])


def trace(event, **fields):
    row = {"event": event, "case": attack.get("case"), **fields}
    # One append write keeps concurrent provider records separate.
    data = (json.dumps(row) + "\n").encode()
    fd = os.open(root / "provider-trace.jsonl", os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    try:
        os.write(fd, data)
    finally:
        os.close(fd)


if attack.get("module"):
    importlib.import_module("harness.attack_" + attack["module"]).install(fake, attack, trace)

original = fake.report_for


def observed(stage, data):
    import psutil
    process = psutil.Process()
    trace("stage_enter", stage=stage, repair=bool(data.get("report_repair")),
          pid=process.pid, birth_identity=process._ident[1])
    try:
        report = original(stage, data)
        trace("stage_exit", stage=stage)
        return report
    except BaseException as error:
        trace("stage_error", stage=stage, error=type(error).__name__)
        raise


fake.report_for = observed
raise SystemExit(fake.main())
