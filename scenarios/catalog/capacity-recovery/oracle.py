"""Independent CLI acceptance and retained evidence of one bounded capacity recovery."""

import json
import sys
from pathlib import Path

from harness.oracle import Check, non_stdlib_imports, scratch_copy
from harness.oracle import run as command

CASES = [
    ("no-arg", [], 2, "", "usage"),
    ("one-name", ["Ada"], 0, "Hello, Ada\n", ""),
    ("name-with-space", ["Ada Lovelace"], 0, "Hello, Ada Lovelace\n", ""),
    ("unicode", ["Zoë"], 0, "Hello, Zoë\n", ""),
    ("empty-name", [""], 2, "", "usage"),
    ("two-args", ["Ada", "Lovelace"], 2, "", "usage"),
]


def product_checks(project):
    if not (project / "greet.py").is_file():
        return [Check("greet.py", False, "not delivered")]
    checks = []
    with scratch_copy(project) as copy:
        for case, argv, exit_code, stdout, stderr in CASES:
            proc = command([sys.executable, "greet.py", *argv], copy, timeout=30)
            checks.append(Check(f"{case}.exit", proc.returncode == exit_code, f"exit {proc.returncode}"))
            checks.append(Check(f"{case}.stdout", proc.stdout == stdout, repr(proc.stdout)))
            checks.append(
                Check(f"{case}.stderr", stderr in proc.stderr.lower() if stderr else not proc.stderr, repr(proc.stderr))
            )
        proc = command([sys.executable, "-m", "unittest", "test_greet.py"], copy, timeout=30)
        checks.append(Check("delivered_tests_pass", proc.returncode == 0, (proc.stdout + proc.stderr)[-600:]))
    missing = [name for name in ("greet.py", "test_greet.py", "README.md") if not (project / name).is_file()]
    checks.append(Check("deliverables", not missing, str(missing)))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    return checks


def archived_file(run, name):
    """Read only a retained artifact inside this run's failed-attempt archive."""
    path = Path(name).resolve()
    root = Path(run["run_dir"]).resolve()
    if not path.is_relative_to(root / "iterations") or not path.parent.name.startswith("archived-"):
        raise ValueError("Capacity evidence is outside this run's failed-attempt archive")
    return path


def recovery_checks(project, run):
    view = run.get("view") or {}
    try:
        trace = project.parent / "capacity-trace.jsonl"
        rows = [json.loads(line) for line in trace.read_text().splitlines()]
        attempts = [
            row
            for row in ((view.get("usage") or {}).get("accounting") or {}).get("attempts", [])
            if row.get("stage") == "astra_discovery" and not row.get("runner_owned") and not row.get("report_only")
        ]
        fresh_request = False
        hybrid_trace = project.parent / "hybrid" / "trace.jsonl"
        is_hybrid = hybrid_trace.is_file()
        if is_hybrid:
            failed, succeeded, *later_attempts = attempts
            served = [json.loads(line) for line in hybrid_trace.read_text().splitlines()]
            discovery = [row for row in served if row.get("stage") == "astra_discovery" and not row.get("repair")]
            injected, retry, *later_calls = discovery
            if len(discovery) != len(attempts) or any(
                call.get("side") != "live"
                or call.get("report") != attempt.get("output")
                or call.get("model") != attempt.get("model")
                or attempt.get("exit_code") != 0
                or attempt.get("rejected")
                or attempt.get("interrupted")
                for call, attempt in zip(later_calls, later_attempts, strict=True)
            ):
                raise ValueError("Later Requirements calls must be successful live continuations")
            if len(rows) not in (1, 2):
                raise ValueError("Expected one injected capacity failure and at most one stand-in retry")
            if injected.get("side") != "scripted" or retry.get("side") != "live":
                raise ValueError("Capacity retry must cross from the injected fault to the live side")
            # report_file tools need not publish provider session IDs. Their
            # distinct invocations are bound to the runner's accounting outputs.
            fresh_request = (
                bool(injected.get("report"))
                and bool(retry.get("report"))
                and injected["report"] != retry["report"]
                and Path(failed["output"]).name == Path(injected["report"]).name
                and succeeded.get("output") == retry["report"]
            )
            first = rows[0]
            second = {
                "stage": retry["stage"],
                "model": retry["model"],
                "outcome": "continue",
                "recovery_context": retry.get("recovery_context"),
            }
            rows = [first, second]
        else:
            failed, succeeded = attempts
            first, second = rows
            fresh_request = (
                bool(first.get("session")) and bool(second.get("session")) and first["session"] != second["session"]
            )
        context = second.get("recovery_context") or {}
        events_path = archived_file(run, context["events"])
        events = [json.loads(line) for line in events_path.read_text().splitlines()]
        snapshot = json.loads(archived_file(run, context["source_snapshot"]).read_text())
        failed_output = archived_file(run, failed["output"])
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [Check("capacity_evidence_retained", False, str(error))]
    checks = [
        Check(
            "one_capacity_failure_then_fresh_request",
            [row.get("outcome") for row in rows] == ["capacity", "continue"]
            and all(row.get("stage") == "astra_discovery" for row in rows)
            and fresh_request,
            str([{key: row.get(key) for key in ("stage", "outcome", "session")} for row in rows]),
        ),
        Check(
            "one_bounded_recovery_of_the_same_stage",
            context.get("stage") == context.get("next_stage") == "astra_discovery"
            and context.get("retry_number") == 1
            and context.get("changed_files") == []
            and bool(context.get("source_revision"))
            and context["source_revision"] == snapshot.get("revision") == failed.get("source_revision"),
            str(context),
        ),
        Check(
            "failed_attempt_has_no_success_or_report",
            any(row.get("type") == "thread.started" and row.get("thread_id") == first["session"] for row in events)
            and any(
                row.get("type") == "turn.failed" and (row.get("error") or {}).get("code") == "model_overloaded"
                for row in events
            )
            and not any(row.get("type") == "turn.completed" for row in events)
            and not failed_output.exists(),
            str(events_path),
        ),
        Check(
            "public_accounting_retains_failed_and_successful_attempts",
            failed.get("rejected") is True
            and failed.get("interrupted") is True
            and failed.get("exit_code") not in (None, 0)
            and failed.get("events") == str(events_path)
            and succeeded.get("exit_code") == 0
            and succeeded.get("rejected") is False
            and succeeded.get("interrupted") is False,
            str(attempts),
        ),
        Check(
            "same_model_without_operator_recovery",
            bool(first.get("model"))
            and first["model"] == second.get("model") == failed.get("model") == succeeded.get("model")
            and not view.get("route_assignments")
            # Ordinary Requirements clarification can follow the automatic
            # retry in a live run; it is not permission to change provider route.
            and (is_hybrid or not run.get("answers"))
            and "feedback" not in run.get("cli_calls", []),
            str([row.get("model") for row in rows]),
        ),
    ]
    replay = (view.get("evidence") or {}).get("check_replay") or {}
    checks.append(
        Check(
            "fresh_independent_acceptance",
            run.get("status") == "TASK_COMPLETE" and view.get("done") is True and replay.get("verdict") == "PASS",
            str({"status": view.get("status"), "replay": replay.get("verdict")}),
        )
    )
    return checks


def check(project, scenario, run=None):
    checks = product_checks(project)
    if run is not None and run.get("provider_evidence") != "live":
        checks.extend(recovery_checks(project, run))
    return checks


def diagnosis(project, run):
    """A naturally successful live run cannot qualify a scripted capacity fault."""
    if run is None or run.get("provider_evidence") == "live":
        return {
            "verdict": "NOT_EXERCISED",
            "reason": "Capacity recovery qualification requires the scripted capacity_once fault (--fake)",
            "checks": [],
        }
    checks = recovery_checks(project, run)
    return {
        "verdict": "CORRECT" if all(check.ok for check in checks) else "INCORRECT",
        "reason": "The terminal capacity failure, fresh same-model retry and completion require retained evidence",
        "checks": checks,
    }
