"""Run the pinned real-repository benchmark sweep on the Claude models and print its reliability row.

    python3 examples/claude-provider/real_repo_sweep.py run starter --out /tmp/sweep --repeat 3 \
        --i-authorize-live-model-spend
    python3 examples/claude-provider/real_repo_sweep.py summary /tmp/sweep --master SHA

The sets are fixed here so that every sweep measures the same tasks (docs/real-repo-sweep.md):

- ``starter`` (weekly): the Docutils scenario and the three starter Arena cases;
- ``hard`` (monthly): the five hard Arena cases (SymPy, Django, pytest, TypeScript, Go), after their
  dependencies are installed as docs/arena.md describes.

Every case runs on the ``claude-tiers`` route (scenarios/harness/profiles.py): Sonnet for Requirements, the
Planner and the Tester, Opus for the Plan Reviewer, Resolver, Completion Reviewer and Investigator, Haiku for
the Builder. Scenario cases go through ``scenarios/run.py``; Arena cases through ``tools/autocode_arena.py``,
which prepares a fresh store under ``--out`` and approves each plan by its exact token (benchmark
automation). Cases run one at a time: Arena supports one writer. Each Arena case gets its own store
(``--out/arenas/CASE``), because ``arena/prepare.py`` never adds a case to a store that already holds one.
``run`` starts only the attempts still missing a record, so the same command continues after a restart.

``summary`` reads the records and prints one ``docs/reliability-sweeps.json`` row: runs, passes, false
completions, median minutes and each run's reported cost. Prefix ``run`` with
``setpriv --bounding-set=-sys_admin,-sys_ptrace`` in a cloud container (CLOUD-SESSION.md).
"""

from __future__ import annotations

import argparse
import datetime
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
PY = REPO / ".venv" / "bin" / "python"
PROFILE = "claude-tiers"


def _pytest(args: str, **env: str) -> dict:
    return {"test": f"{PY} -m pytest {args}", "env": env}


# The pinned cases. "scenario" cases name a catalog entry; the others are arena/catalog.json ids with the
# test command docs/arena.md gives for each and the environment its setup helper needs.
SETS = {
    "starter": {
        "bugfix-realrepo-docutils-number-lines": {"harness": "scenario"},
        "boltons-indexedset-update": _pytest("tests/test_setutils.py -q"),
        "humanize-intcomma-large": _pytest(
            "-q -p no:cacheprovider tests/test_number.py",
            PYTHONPATH=str(REPO / "arena/support/humanize"),
            PYTEST_PLUGINS="arena_humanize_bootstrap",
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        ),
        "more-itertools-custom-exceptions": _pytest("-q tests/test_more.py"),
    },
    "hard": {
        "sympy-matrix-derivatives": _pytest(
            "-q -p no:cacheprovider sympy/core/tests/test_diff.py sympy/core/tests/test_function.py "
            "sympy/core/tests/test_args.py sympy/matrices/expressions/tests "
            "sympy/tensor/array/tests/test_array_derivatives.py sympy/tensor/array/expressions/tests",
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        ),
        "django-filteredrelation-join-lifecycle": _pytest(
            "-q -o python_files=test*.py -p no:cacheprovider tests/filtered_relation tests/queries",
            PYTHONPATH=str(REPO / "arena/support/django"),
            PYTEST_PLUGINS="arena_django_bootstrap",
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        ),
        "pytest-stop-fixture-teardown": _pytest(
            "-o minversion= testing/test_runner.py testing/test_session.py testing/test_stepwise.py -q",
            PYTHONPATH=str(REPO / "arena/support/pytest"),
            PYTEST_DISABLE_PLUGIN_AUTOLOAD="1",
        ),
        "typescript-dependent-destructuring-baselines-v2": {
            "test": "node --test tests/cases/unittests/arenaDependentDestructuring.test.cjs",
            "env": {},
        },
        "go-http2-hung-reset-health": {
            "test": "go test ./http2 ./http2/hpack -count=1 -timeout=120s -parallel=2",
            "env": {},
            "oracle_timeout": 400,
        },
    },
}
ORACLE_TIMEOUT = 120

MODELS = {
    "--requirements-model": "claude-sonnet-5-5",
    "--planner-model": "claude-sonnet-5-5",
    "--plan-reviewer-model": "claude-opus-5-5",
    "--builder-model": "claude-haiku-4-5-20251001",
    "--tester-model": "claude-sonnet-5-5",
    "--task-planner-model": "claude-opus-5-5",
    "--completion-model": "claude-opus-5-5",
    "--investigator-model": "claude-opus-5-5",
}
EFFORTS = (
    "--reasoning-effort",
    "--requirements-reasoning-effort",
    "--planner-reasoning-effort",
    "--plan-reviewer-reasoning-effort",
    "--tester-reasoning-effort",
    "--task-planner-reasoning-effort",
    "--completion-reasoning-effort",
)


def arena_options(test_command: str) -> list[str]:
    """The ``--option`` arguments that give an Arena attempt the claude-tiers route."""
    pairs = [("--provider", "claude"), *MODELS.items(), *((flag, "medium") for flag in EFFORTS)]
    pairs.append(("--test-command", test_command))
    return [f"--option={flag}={value}" for flag, value in pairs]


def _env_prefix(env: dict) -> str:
    return "".join(f"{key}={value} " for key, value in sorted(env.items()))


def record_path(out: Path, case: str, index: int) -> Path:
    return out / "records" / f"{case}-{index:02d}.json"


def arena_store(out: Path, case: str) -> Path:
    """The case's own Arena store: ``arena/prepare.py`` prepares only a fresh directory, so cases never share one."""
    return out / "arenas" / case


def run_arena(out: Path, case: str, spec: dict, cohort: str, timeout: int) -> dict:
    arena = arena_store(out, case)
    test = spec["test"]
    if spec["env"]:
        test = "env " + _env_prefix(spec["env"]) + test
    oracle_timeout = str(spec.get("oracle_timeout", ORACLE_TIMEOUT))
    if not (arena / "cases" / case).exists():
        subprocess.run(
            [str(PY), "arena/prepare.py", "--arena", str(arena), "--case", case, "--oracle-timeout", oracle_timeout],
            cwd=REPO,
            check=True,
            env={**os.environ, **spec["env"]},
        )
    command = [str(PY), "tools/autocode_arena.py", "--arena", str(arena), "run", case, "--cohort", cohort]
    command += ["--i-authorize-live-model-spend", "--approve-benchmark-plans", "--timeout", str(timeout)]
    command += ["--oracle-timeout", oracle_timeout, *arena_options(test)]
    env = {**os.environ, **spec["env"], "PATH": f"{PY.parent}{os.pathsep}{os.environ['PATH']}"}
    done = subprocess.run(command, cwd=REPO, env=env, capture_output=True, text=True)
    row = json.loads(done.stdout)
    status = Path(row["workspace"]).parent / "status.json"
    cost = (json.loads(status.read_text()).get("cost") or {}) if status.exists() else {}
    checks = row.get("checks") or []
    return {
        "case": case,
        "harness": "arena",
        "verdict": row["verdict"],
        "status": row.get("runner_status"),
        "oracle": f"{sum(1 for c in checks if c['ok'])}/{len(checks)}",
        "minutes": round(row["elapsed_seconds"] / 60, 1),
        "spent_usd": cost.get("spent_usd"),
        "evidence": str(Path(row["workspace"]).parent),
    }


def run_scenario(out: Path, case: str, index: int, timeout: int) -> dict:
    where = out / "scenarios" / f"{case}-{index:02d}"
    command = [str(PY), "scenarios/run.py", "run", case, "--profile", PROFILE, "--i-authorize-live-model-spend"]
    command += ["--out", str(where), "--timeout-minutes", str(timeout // 60)]
    subprocess.run(command, cwd=REPO)
    result = json.loads(next(where.glob("*/result.json")).read_text())
    checks = result.get("checks") or []
    return {
        "case": case,
        "harness": "scenario",
        "verdict": result["verdict"],
        "status": result.get("status"),
        "oracle": f"{sum(1 for c in checks if c.get('ok'))}/{len(checks)}",
        "minutes": round(result["wall_seconds"] / 60, 1),
        "spent_usd": (result.get("cost") or {}).get("spent_usd"),
        "evidence": str(next(where.glob("*/result.json")).parent),
    }


def run(args) -> int:
    if not args.i_authorize_live_model_spend:
        sys.exit("a live sweep needs --i-authorize-live-model-spend")
    out = args.out.resolve()
    (out / "records").mkdir(parents=True, exist_ok=True)
    cohort = args.cohort or f"{args.set}-{datetime.date.today().isoformat()}"
    for case, spec in SETS[args.set].items():
        for index in range(1, args.repeat + 1):
            path = record_path(out, case, index)
            if path.exists():
                continue
            if spec.get("harness") == "scenario":
                record = run_scenario(out, case, index, args.timeout)
            else:
                record = run_arena(out, case, spec, cohort, args.timeout)
            path.write_text(json.dumps(record, indent=2) + "\n")
            print(json.dumps(record), flush=True)
    return 0


def summarize(records: list[dict], *, date: str, master: str, note: str = "") -> dict:
    """One reliability-sweeps.json row from the sweep's records (pure)."""
    minutes = [r["minutes"] for r in records if isinstance(r.get("minutes"), (int, float))]
    known = [r["spent_usd"] for r in records if isinstance(r.get("spent_usd"), (int, float))]
    passed = sum(1 for r in records if r["verdict"] == "PASS")
    false = sum(1 for r in records if r["verdict"] == "FALSE_COMPLETE")
    stops = [
        f"{r['case']} {r['verdict']} ({r.get('status')}, oracle {r.get('oracle')})"
        for r in records
        if r["verdict"] not in ("PASS", "FALSE_COMPLETE")
    ]
    detail = f"{len(records)} runs over {len({r['case'] for r in records})} pinned real-repo cases"
    detail += f"; reported spend ${sum(known):.2f}" + ("" if len(known) == len(records) else " (some unknown)")
    if stops:
        detail += ". Not passed: " + "; ".join(stops)
    return {
        "date": date,
        "profile": PROFILE,
        "master": master,
        "mode": "live",
        "runs": len(records),
        "passed": passed,
        "false_completions": false,
        "median_minutes": round(statistics.median(minutes), 1) if minutes else None,
        "note": (note + " " if note else "") + detail + ".",
        "case_costs": [
            {
                "case": r["case"],
                "run": None,
                "estimated_usd": None,
                "reported_usd": r.get("spent_usd"),
                "spent_usd": r.get("spent_usd"),
                "cap_usd": None,
            }
            for r in records
        ],
    }


def summary(args) -> int:
    records = [json.loads(p.read_text()) for p in sorted((args.out / "records").glob("*.json"))]
    if not records:
        sys.exit(f"no records under {args.out / 'records'}")
    date = args.date or datetime.date.today().isoformat()
    print(json.dumps(summarize(records, date=date, master=args.master, note=args.note), indent=2))
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    start = sub.add_parser("run", help="run a pinned set live")
    start.add_argument("set", choices=sorted(SETS))
    start.add_argument("--out", type=Path, required=True, help="sweep directory, outside the repository")
    start.add_argument("--repeat", type=int, default=3)
    start.add_argument("--timeout", type=int, default=3600, help="seconds per AutoCode invocation")
    start.add_argument("--cohort", help="Arena cohort (default: SET-DATE)")
    start.add_argument("--i-authorize-live-model-spend", action="store_true")
    report = sub.add_parser("summary", help="print the sweep's reliability row")
    report.add_argument("out", type=Path)
    report.add_argument("--master", required=True, help="the AutoCode commit the sweep ran on")
    report.add_argument("--date")
    report.add_argument("--note", default="")
    args = parser.parse_args(argv)
    return run(args) if args.command == "run" else summary(args)


if __name__ == "__main__":
    raise SystemExit(main())
