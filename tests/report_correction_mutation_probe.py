"""Show that the in-process report-correction tests notice each named break of the #3868 change.

A validation helper, not a test module: ``python3 -B tests/report_correction_mutation_probe.py`` from the
repository root. Each mutant replaces one exact source snippet, which must occur exactly once in its
``tools/`` module (otherwise the probe exits 2 naming the mutant), in an in-memory copy only. A fresh
``sys.executable -B`` child compiles that copy into the module's top-level name before importing the tests,
then runs only the in-process classes. The unmutated run must pass first (otherwise exit 1). One line per
mutant says KILLED or SURVIVED; the exit is 0 only when every mutant is killed. Nothing under ``tools/`` is
written; the children's temporary files stay in a ``.tmp-`` directory of the repository, removed afterwards.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
CLASSES = (
    "ReportCorrectionDispatch",
    "ReportCorrectionDecision",
    "ReportCorrectionAuthentication",
    "ReportCorrectionRecoveryEdges",
)
CORRECTION = "autocode_report_correction"
QUEUE = "autocode_report_repair_queue"
RECOVERY = "autocode_stage_recovery"
DISPATCH = "autocode_action_recovery"

MUTANTS = (
    ("K01", CORRECTION, "or (entry is None and superseded(state, pending, _latest_record(pending)))", "or False"),
    (
        "K02",
        CORRECTION,
        'record = pending.get("latest_rejected") or pending.get("original") or {}',
        'record = pending.get("original") or {}',
    ),
    ("K03", CORRECTION, 'return record.get("started_at") if isinstance(record, dict) else None', "return None"),
    ("K04", CORRECTION, "and started > after", "and started >= after"),
    ("K05", CORRECTION, 'or rows[-1].get("result") != "rejected"', "or False"),
    ("K06", CORRECTION, "if not isinstance(pins, dict):", "if False:"),
    ("K07", CORRECTION, 'not record.get("report_only")', "False"),
    ("K08", CORRECTION, 'or record.get("iteration") != original.get("iteration")', "or False"),
    ("K09", CORRECTION, 'or record.get("source_revision") != original.get("source_revision")', "or False"),
    ("K10", CORRECTION, 'or record.get("contract_hash") != original.get("contract_hash")', "or False"),
    (
        "K11",
        CORRECTION,
        '("output", "events", "schema", "before_ref", "after_ref")',
        '("output", "events", "schema", "after_ref")',
    ),
    (
        "K12",
        CORRECTION,
        'required.extend((record, key) for key in ("output", "events", "schema"))',
        "required.extend(())",
    ),
    (
        "K13",
        CORRECTION,
        "any(not isinstance(row.get(key), str) or not row[key] or row[key] not in pins for row, key in required)",
        "False",
    ),
    (
        "K14",
        CORRECTION,
        'or (state.get("goal_contract") or {}).get("hash") != pending.get("contract_hash")',
        "or False",
    ),
    ("K15", CORRECTION, "or not runtime.stage_completed(state, record)", "or False"),
    ("K16", CORRECTION, 'if isinstance(active, dict) and active.get("stage") == stage:', "if False:"),
    (
        "K17",
        CORRECTION,
        'if state.get("pending_report_repair") or state.get("active_stage") or not isinstance(archive, list)',
        'if state.get("active_stage") or not isinstance(archive, list)',
    ),
    (
        "K18",
        CORRECTION,
        'if state.get("pending_report_repair") or state.get("active_stage") or not isinstance(archive, list)',
        'if state.get("pending_report_repair") or not isinstance(archive, list)',
    ),
    ("K19", CORRECTION, "if revalidator is None:", "if False:"),
    ("K20", CORRECTION, 'seen = len(pending.get("revalidations") or [])', "seen = 0"),
    # "at": at, also occurs in the mirrored user event; the note's opening line anchors it.
    ("K21", CORRECTION, 'note = {\n        "at": at,', 'note = {\n        "at": None,'),
    # "stage": original["stage"], also occurs in the user event; the note's next line anchors it.
    (
        "K22",
        CORRECTION,
        '"stage": original["stage"],\n        "iteration"',
        '"stage": None,\n        "iteration"',
    ),
    ("K23", CORRECTION, '"iteration": original.get("iteration"),', '"iteration": None,'),
    ("K24", CORRECTION, '"original_output": original.get("output"),', '"original_output": None,'),
    ("K25", CORRECTION, '"restored_from_archive": entry is not None,', '"restored_from_archive": True,'),
    ("K26", CORRECTION, '"restored_from_archive": entry is not None,', '"restored_from_archive": False,'),
    ("K27", CORRECTION, 'state.setdefault("user_events", []).append(', "("),
    ("K28", CORRECTION, '"at": at, "stage": original["stage"], "reason": reason}', "}"),
    ("K29", QUEUE, 'actor="user_cli"', 'actor="runner"'),
    ("K30", RECOVERY, ' if state.get("status") == "RUNNING" else None', ""),
    ("K31", RECOVERY, '"reason": report_correction.UNLAUNCHABLE,', '"reason": report_correction.SPENT,'),
    ("K32", RECOVERY, "else report_correction.SPENT", "else report_correction.UNLAUNCHABLE"),
    # The resume dispatch's bypass of the shared decision (tools/autocode_action_recovery.py:429-430).
    (
        "K33",
        DISPATCH,
        'if not args.retry_report and getattr(decided, "operational_incident", None) is None\n',
        'if getattr(decided, "operational_incident", None) is None\n',
    ),
    (
        "K34",
        DISPATCH,
        'if not args.retry_report and getattr(decided, "operational_incident", None) is None\n',
        "if not args.retry_report\n",
    ),
    (
        "K35",
        DISPATCH,
        'if not args.retry_report and getattr(decided, "operational_incident", None) is None\n',
        "if True\n",
    ),
    (
        "K36",
        DISPATCH,
        "else None\n            )\n            revalidated_report",
        'else "accepted"\n            )\n            revalidated_report',
    ),
    # A record saved without original_stage names the stage it repairs in its own stage
    # (tools/autocode_report_correction.py:84).
    ("K37", CORRECTION, '.removesuffix("_report_repair")', ""),
)

CHILD = r"""
import importlib.util, json, sys, unittest
plan = json.loads(sys.stdin.read())
sys.path.insert(0, plan["tools"])
sys.path.insert(0, plan["root"])
if plan["module"]:
    path = plan["path"]
    spec = importlib.util.spec_from_file_location(plan["module"], path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[plan["module"]] = module
    exec(compile(plan["source"], path, "exec"), module.__dict__)
import tests.test_report_correction_resume_cli as tests
loader = unittest.TestLoader()
suite = unittest.TestSuite(loader.loadTestsFromTestCase(getattr(tests, name)) for name in plan["classes"])
result = unittest.TextTestRunner(stream=sys.stderr, verbosity=0).run(suite)
sys.exit(0 if result.wasSuccessful() and result.testsRun else 1)
"""


def run(module, path, source, scratch):
    """True when the in-process classes pass against the given module source (None: unmutated)."""
    plan = {
        "tools": str(TOOLS),
        "root": str(ROOT),
        "module": module,
        "path": str(path) if path else None,
        "source": source,
        "classes": list(CLASSES),
    }
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", TMPDIR=scratch, TEMP=scratch, TMP=scratch)
    completed = subprocess.run(
        [sys.executable, "-B", "-c", CHILD],
        cwd=ROOT,
        env=env,
        input=json.dumps(plan),
        capture_output=True,
        text=True,
        timeout=600,
    )
    return completed.returncode == 0, completed.stderr


def main():
    sources = {}
    mutated = []
    for mutant, module, snippet, replacement in MUTANTS:
        path = TOOLS / f"{module}.py"
        source = sources.setdefault(module, path.read_text())
        count = source.count(snippet)
        if count != 1:
            print(f"{mutant} {module}: snippet occurs {count} times, not exactly once: {snippet!r}")
            return 2
        changed = source.replace(snippet, replacement)
        try:
            compile(changed, str(path), "exec")
        except SyntaxError as error:
            # A mutant that does not load would be "killed" by the import alone, which proves nothing.
            print(f"{mutant} {module}: the mutated source does not compile: {error}")
            return 2
        mutated.append((mutant, module, path, changed))
    with tempfile.TemporaryDirectory(dir=ROOT, prefix=".tmp-report-correction-probe-") as scratch:
        passed, output = run(None, None, None, scratch)
        if not passed:
            print("UNMUTATED FAILED: the in-process tests must pass on the unchanged source")
            print(output[-4000:])
            return 1
        print("UNMUTATED PASSED")
        # The same loading path with each module's unchanged source must pass too, so a kill is the mutant's.
        for module, source in sources.items():
            passed, output = run(module, TOOLS / f"{module}.py", source, scratch)
            if not passed:
                print(f"UNMUTATED {module} FAILED when compiled in memory")
                print(output[-4000:])
                return 1
            print(f"UNMUTATED {module} PASSED when compiled in memory")
        survivors = []
        for mutant, module, path, source in mutated:
            passed, _ = run(module, path, source, scratch)
            print(f"{mutant} {module} {'SURVIVED' if passed else 'KILLED'}")
            if passed:
                survivors.append(mutant)
    print(f"{len(mutated) - len(survivors)}/{len(mutated)} killed" + (f"; survived: {survivors}" if survivors else ""))
    return 1 if survivors else 0


if __name__ == "__main__":
    sys.exit(main())
