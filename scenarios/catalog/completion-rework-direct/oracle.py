import hashlib
import json
import shutil
import sys

from harness.oracle import Check, non_stdlib_imports, scratch_copy, tail
from harness.oracle import run as command


def check(project, scenario, run=None):
    checks = []
    for name, args, code, out, err in (
            ("Ada", ["Ada"], 0, "Hello, Ada\n", ""),
            ("empty", [""], 2, "", "usage: greet.py NAME\n"),
            ("whitespace", [" \t "], 2, "", "usage: greet.py NAME\n"),
            ("noargs", [], 2, "", "usage: greet.py NAME\n"),
            ("twoargs", ["Ada", "Lovelace"], 2, "", "usage: greet.py NAME\n")):
        proc = command([sys.executable, "greet.py", *args], project)
        checks.append(Check(name + ".exact", (proc.returncode, proc.stdout, proc.stderr) == (code, out, err),
                            repr((proc.returncode, proc.stdout, proc.stderr))))
    digest = lambda path: hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
    checks.append(Check("seed_tests_unchanged", digest(project / "test_greet.py") ==
                        digest(scenario.seed / "test_greet.py"), "immutable seed test SHA-256"))
    with scratch_copy(project) as copy:
        proc = command([sys.executable, "-m", "unittest", "test_greet.py"], copy)
        checks.append(Check("project_tests_pass", proc.returncode == 0, tail(proc)))
        shutil.copy2(scenario.seed / "greet.py", copy / "greet.py")
        proc = command([sys.executable, "-m", "unittest", "test_greet.py"], copy)
        checks.append(Check("tests_reject_seed", proc.returncode != 0, tail(proc)))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, repr(foreign)))
    if run is not None:
        view = run.get("view") or {}
        stages = [stage for stage in run.get("model_stages", [])
                  if stage in ("terra", "sol", "astra_review", "astra_resolve", "astra_plan")]
        checks.append(Check("first_direct_repair_route", stages ==
                            ["terra", "sol", "astra_review", "terra", "sol", "astra_review"], repr(stages)))
        checks.append(Check("public_done", view.get("done") is True, repr(view.get("status"))))
        replay = (view.get("evidence") or {}).get("check_replay") or {}
        checks.append(Check("current_clean_replay_pass", replay.get("verdict") == "PASS" and
                            bool(replay.get("checks")) and all(row.get("exit_code") == 0 for row in replay["checks"]),
                            repr(replay)))
        trace_path = project.parent / "rework-trace.jsonl"
        rows = [json.loads(line) for line in trace_path.read_text().splitlines()] if trace_path.is_file() else []
        validators = [row for row in rows if row["stage"] == "sol"]
        owners = [row for row in rows if row["stage"] == "astra_review"]
        checks.append(Check("fault_reached_first_completion", bool(owners) and owners[0]["status"] == "REWORK"
                            and owners[0]["validation_verdict"] == "FAIL", repr(owners[:1])))
        checks.append(Check("actual_fail_then_fresh_pass", len(validators) == 2 and
                            validators[0]["exit_code"] != 0 and validators[1]["exit_code"] == 0 and
                            validators[0]["source_revision"] != validators[1]["source_revision"] and
                            validators[0]["task_id"] != validators[1]["task_id"] and
                            replay.get("source_revision") == validators[1]["source_revision"], repr(validators)))
        checks.append(Check("fresh_completion_after_validator", len(owners) == 2 and len(validators) == 2 and
                            owners[1]["status"] == "COMPLETE" and
                            owners[1]["task_id"] == validators[1]["task_id"] == owners[1]["validation_task_id"] and
                            owners[1]["source_revision"] == validators[1]["source_revision"], repr(owners[-1:])))
        assignments = view.get("direct_rework_assignments") or []
        checks.append(Check("one_runner_assignment", len(assignments) == 1 and
                            assignments[0].get("provenance") == "completion_direct_assignment", repr(assignments)))
    return checks
