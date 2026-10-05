"""check(): move and remove work and refuse correctly (hidden tests), and every delivered test for them
fails on the original code (run one at a time by the oracle). That is the product, and the run verdict.

diagnosis() (issue #59) scores AutoResolver apart from the run verdict, from the runner's own records. A
trap revision is one whose regression proof FAILed because a planned "Test case Cn" test passed on the
original code too (its test_cn_* test is under pass_to_pass): on the original code `stock.py move` is an
unknown subcommand, so argparse exits 2 and never touches stock.json. The first counted Resolver call at a
trap revision is scored; scenarios/README.md ("Diagnosis") has the block's contract.
"""
import ast
import json
import re
import shutil
import sys
from pathlib import Path

from harness.oracle import (Check, hidden_tests, mentions, non_stdlib_imports, python_tests, run as command,
                            scratch_copy, tail, test_names)

NEW_COMMANDS = {"move", "remove"}
TEST_FILE = "tests/test_stock.py"
# Why such a test passes on the original code: the command does not exist there, and argparse refuses it.
CAUSE = ("invalid choice", "argparse", "unknown subcommand", "unknown command", "not a command",
         "no such command", "does not exist", "doesn't exist", "did not exist", "not exist", "no move",
         "no remove", "unrecognized", "usage error")
STATUS = ("exit 2", "exits 2", "exited 2", "exit code 2", "exit status 2", "returncode 2", "return code 2",
          "== 2", "==2", "status 2", "code 2", "nonzero", "non-zero")
ORIGINAL = ("original", "base", "seed", "pass_to_pass", "pass-to-pass", "before the change",
            "without the change", "unchanged code")
# A repair that makes the proof pass by weakening or dropping the tests is not a repair. Lexical: a
# negated mention ("do not skip the test") also counts, which the hand audit of a live attempt catches.
WEAKENING = ("guard:", "skiptest", "skipif", "skipunless", "unittest.skip", "@skip", "skip the", "skip these",
             "expectedfailure", "delete the test", "delete these tests", "remove the test", "remove these tests",
             "drop the test", "drop these tests", "relax", "loosen")


def check(project, scenario, run=None):
    """The product only. How AutoResolver did is diagnosis()'s, and never part of this verdict."""
    checks = []
    with scratch_copy(project) as copy:
        suite = python_tests(copy)
        checks.append(Check("project_tests_pass", suite.returncode == 0, tail(suite)))
        hidden = hidden_tests(copy, scenario.dir / "hidden")
        checks.append(Check("hidden_tests_pass", hidden.returncode == 0, tail(hidden)))
    tests = new_command_tests(project, scenario.seed)
    vacuous = []
    with scratch_copy(project) as copy:
        shutil.copy2(scenario.seed / "stock.py", copy / "stock.py")
        for test in tests:
            if command([sys.executable, "-m", "unittest", test], copy).returncode == 0:
                vacuous.append(test)
    checks.append(Check("new_command_tests_fail_on_original_code", bool(tests) and not vacuous,
                        f"pass without move/remove: {vacuous}" if vacuous else f"{len(tests)} tests checked"))
    missing = sorted(test_names(scenario.seed / "tests") - test_names(project / "tests"))
    checks.append(Check("existing_tests_kept", not missing, f"removed: {missing}" if missing else ""))
    text = (project / "README.md").read_text() if (project / "README.md").is_file() else ""
    checks.append(Check("readme_documents_move_and_remove", "stock.py move" in text and "stock.py remove" in text))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    return checks


def new_command_tests(project, seed):
    """Delivered tests not in the seed that are about move or remove (by name or by a literal in the body),
    as unittest ids (module.Class.test)."""
    seeded = test_names(seed / "tests")
    found = []
    for path in sorted((project / "tests").glob("test*.py")):
        try:
            tree = ast.parse(path.read_text())
        except SyntaxError:
            continue
        module = ".".join(path.relative_to(project).with_suffix("").parts)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            for item in node.body:
                if not (isinstance(item, ast.FunctionDef) and item.name.startswith("test")
                        and item.name not in seeded):
                    continue
                literals = {n.value for n in ast.walk(item) if isinstance(n, ast.Constant)}
                if literals & NEW_COMMANDS or any(name in item.name for name in NEW_COMMANDS):
                    found.append(f"{module}.{node.name}.{item.name}")
    return found


def diagnosis(project, run):
    """The diagnosis block for a run (scenarios/README.md, "Diagnosis"); never part of the run verdict."""
    if run is None:
        return {"verdict": "NOT_EXERCISED", "reason": "no run (check mode)", "checks": []}
    states = sorted((project / ".autocode" / "runs").glob("*/state.json"))
    if not states:
        return {"verdict": "NOT_EXERCISED", "reason": "the run saved no state", "checks": []}
    return score(json.loads(states[-1].read_text()), states[-1].parent)


def score(state, run_dir, *, cause=CAUSE, test_file=TEST_FILE):
    """Score AutoResolver on one saved run. ``cause`` and ``test_file`` are this scenario's; passing another
    scenario's lets the same rules read its runs (the trap and the counting do not depend on them).

    A Resolver call counts as an attempt only when it is astra_resolve, not runner-owned, launched with a
    model (``runner_calls`` >= 1) and its report file was saved. Report repairs, astra_diagnose and
    investigate_stuck calls never count. Rejected and BLOCKED reports are read from the stage rows, so they
    are seen too; the scored attempt is the first accepted one at a trap revision, else the first saved.
    """
    run_dir = Path(run_dir)
    traps = trap_proofs(state, run_dir)
    proofs = sorted((row for row in state.get("regression_proofs") or [] if isinstance(row, dict)),
                    key=lambda row: row.get("proved_at") or "")
    rows = [row for row in state.get("stages") or [] if isinstance(row, dict)]
    active = state.get("active_stage")
    if isinstance(active, dict) and active not in rows:
        rows.append(active)  # a call still running when the run was stopped: launched, never saved
    launched = [(index, row) for index, row in enumerate(rows) if _launched(row)]
    other = [_brief(row, _read(_local(row.get("output"), run_dir)), run_dir) for _, row in launched
             if _revision(row, run_dir) not in traps]
    block = {"trap_tests": {revision[:12]: names for revision, names in traps.items()},
             "other_resolver_calls": other}  # unrelated REWORKs: kept for a human read, never scored here
    if not traps:
        return {"verdict": "NOT_EXERCISED", "checks": [], **block,
                "reason": "no regression proof failed on a planned test that also passes on the original code"}
    at_trap = [(index, row) for index, row in launched if _revision(row, run_dir) in traps]
    if not at_trap:
        return {"verdict": "NOT_EXERCISED", "checks": [], **block,
                "reason": "a regression proof failed on tests that pass on the original code, but AutoResolver "
                          "never ran at that revision"}
    saved = [(index, row, report) for index, row in at_trap
             if isinstance(report := _read(_local(row.get("output"), run_dir)), dict)]
    block["launched_without_report"] = len(at_trap) - len(saved)
    if not saved:
        return {"verdict": "UNSCORED", "checks": [], **block,
                "reason": "AutoResolver launched at the failed proof's revision but saved no report"}
    calls = [score_call(state, rows, index, row, report, traps[_revision(row, run_dir)], proofs, run_dir,
                        cause=cause, test_file=test_file) for index, row, report in saved]
    chosen = next((call for call in calls if call["accepted"]), calls[0])
    chosen["scored"] = True
    failed = [check.name for check in chosen["checks"] if not check.ok]
    return {"verdict": "INCORRECT" if failed else "CORRECT",
            "reason": f"failing: {', '.join(failed)}" if failed else f"{len(chosen['checks'])} required checks passed",
            "checks": chosen["checks"], "vacuous_tests": chosen["vacuous_tests"], "model": chosen["model"],
            "cost_usd": chosen["cost_usd"], "review_already_named_cause": chosen["review_already_named_cause"],
            "resolver_added_beyond_review": chosen["resolver_added_beyond_review"],
            "resolver_calls_on_trap": len(calls), "trap_calls": calls, **block}


def score_call(state, rows, index, row, report, vacuous, proofs, run_dir, *, cause, test_file):
    """One counted Resolver call at a trap revision, with every required check and the unscored context."""
    task = report.get("next_task") if isinstance(report.get("next_task"), dict) else {}
    text = _text({"diagnosis": report.get("diagnosis"), "next_objective": report.get("next_objective"),
                  "next_task": task})
    task_text = _text({"next_objective": report.get("next_objective"), "next_task": task})
    lowered = text.lower()
    missing = [name for name in vacuous if not _named(name, text)]
    weakening = [word for word in WEAKENING if word in lowered]
    checks = [
        Check("diagnosis_accepted", not row.get("rejected"),
              f"the runner rejected the report: {row.get('rejection_reason', '')}"[:300] if row.get("rejected") else ""),
        Check("diagnosis_names_each_vacuous_test", not missing, f"vacuous: {vacuous}; not named: {missing}"),
        Check("diagnosis_explains_why_they_pass_on_original_code", mentions(text, cause, STATUS, ORIGINAL),
              str(report.get("diagnosis"))[:400]),
        Check("resolver_chose_bounded_test_repair",
              report.get("status") == "REWORK" and task.get("kind") == "implement" and test_file in task_text,
              f"status {report.get('status')}, next_task.kind {task.get('kind')}, names {test_file}: "
              f"{test_file in task_text}"),
        Check("repair_does_not_weaken_tests", not weakening, f"weakening words: {weakening}" if weakening else ""),
        # The runner already enforces a read-only Resolver; recorded because #59 asks about it.
        Check("resolver_stayed_read_only", not row.get("changed_files"), str(row.get("changed_files") or "")),
    ]
    # Did the task work? The runner's proof of the next build (a runner fact).
    after = _next_proof(rows, index, row, proofs)
    verification = _read(_local(after.get("path"), run_dir)) if after else None
    verification = verification if isinstance(verification, dict) else {}
    flipped = {_function(test) for test in verification.get("fail_to_pass") or [] if isinstance(test, str)}
    checks.append(Check("repair_made_the_tests_discriminate",
                        verification.get("verdict") == "PASS" and set(vacuous) <= flipped,
                        f"next proof {verification.get('verdict') or 'none'}; not under fail_to_pass: "
                        f"{sorted(set(vacuous) - flipped)}"))
    # Not scored: what the Completion Owner had already said, so a correct diagnosis may be a confirmation.
    review = _review_text(state, rows, index, row, run_dir)
    failed = [check.name for check in checks if not check.ok]
    return {**_brief(row, report, run_dir), "accepted": not row.get("rejected"), "scored": False,
            "verdict": "INCORRECT" if failed else "CORRECT", "failing": failed, "checks": checks,
            "vacuous_tests": vacuous, "review_already_named_cause": mentions(review, cause, STATUS, ORIGINAL),
            "resolver_added_beyond_review": sorted(_facets(text, vacuous, cause) - _facets(review, vacuous, cause))}


def trap_proofs(state, run_dir):
    """{source_revision: [vacuous test functions]} for each regression proof that FAILed because a planned
    "Test case Cn" test (test_cn_*) also passed on the original code. Runner facts, no model claims."""
    found = {}
    for row in state.get("regression_proofs") or []:
        if not isinstance(row, dict) or row.get("verdict") != "FAIL":
            continue
        verification = _read(_local(row.get("path"), run_dir))
        if not isinstance(verification, dict):
            continue
        cases = {case.lower() for failure in verification.get("failures") or [] if isinstance(failure, str)
                 for case in re.findall(r"^Test case (\w+):", failure)}
        vacuous = sorted({_function(test) for test in verification.get("pass_to_pass") or []
                          if isinstance(test, str) and _planned(_function(test), cases)})
        if vacuous and row.get("source_revision"):
            found.setdefault(row["source_revision"], vacuous)
    return found


def _next_proof(rows, index, row, proofs):
    """The proof of the first build after this call; a build that changed nothing keeps its earlier proof."""
    finished = row.get("finished_at") or row.get("started_at") or ""
    build = next((later for later in rows[index + 1:] if later.get("stage") == "terra" and not later.get("rejected")
                  and later.get("source_revision")), None)
    same = [proof for proof in proofs if build and proof.get("source_revision") == build["source_revision"]]
    if same:
        return next((proof for proof in same if (proof.get("proved_at") or "") > finished), same[-1])
    return next((proof for proof in proofs if (proof.get("proved_at") or "") > finished), None)


def _launched(row):
    return (row.get("stage") == "astra_resolve" and not row.get("runner_owned")
            and bool((row.get("launch_route") or {}).get("model"))
            and isinstance(row.get("runner_calls"), int) and row["runner_calls"] >= 1)


def _revision(row, run_dir):
    """The source the call saw; an unfinished call has only its launch snapshot."""
    before = _read(_local(row.get("before_ref"), run_dir))
    return (row.get("source_revision") or (row.get("capture_context") or {}).get("source_revision")
            or (before.get("revision") if isinstance(before, dict) else None))


def _brief(row, report, run_dir):
    report = report if isinstance(report, dict) else {}
    return {"output": row.get("output"), "source_revision": (_revision(row, run_dir) or "")[:12],
            "model": (row.get("launch_route") or {}).get("model"),
            "cost_usd": (row.get("metrics") or {}).get("provider_cost_usd"), "rejected": bool(row.get("rejected")),
            "status": report.get("status"), "diagnosis": report.get("diagnosis"),
            "next_objective": report.get("next_objective"), "next_task": report.get("next_task")}


def _review_text(state, rows, index, row, run_dir):
    """The Completion Owner's findings and next task that sent this call to AutoResolver."""
    path = next((entry.get("review_output") for entry in state.get("resolution_history") or []
                 if isinstance(entry, dict) and entry.get("output") == row.get("output")), None)
    if not path:
        earlier = [r for r in rows[:index] if r.get("stage") == "astra_review" and not r.get("rejected")
                   and _revision(r, run_dir) == _revision(row, run_dir)]
        path = earlier[-1].get("output") if earlier else None
    review = _read(_local(path, run_dir))
    if not isinstance(review, dict):
        return ""
    task = review.get("next_task") if isinstance(review.get("next_task"), dict) else {}
    own = [finding for finding in review.get("findings") or [] if isinstance(finding, dict)]
    ids = {finding.get("id") for finding in own} | set(task.get("findings") or [])
    ledger = [entry for entry in state.get("findings_ledger") or [] if isinstance(entry, dict) and entry.get("id") in ids]
    return _text({"findings": own + [{key: entry.get(key) for key in ("finding", "evidence")} for entry in ledger],
                  "next_objective": review.get("next_objective"), "next_task": task})


def _facets(text, vacuous, cause):
    """What a text says about the trap: which vacuous tests it names, and whether it explains the cause."""
    return ({f"names {name}" for name in vacuous if _named(name, text)}
            | ({"explains the cause"} if mentions(text, cause, STATUS, ORIGINAL) else set()))


def _named(name, text):
    """By function name, or by the short test_<case> form (test_c3) the proof's case ids give."""
    short = re.match(r"test_[A-Za-z]+\d+", name)
    return name in text or bool(short and re.search(rf"\b{re.escape(short.group(0))}\b", text))


def _planned(function, cases):
    """test_c3 or test_c3_*, for a planned case C3 the proof reported."""
    return any(function == f"test_{case}" or function.startswith(f"test_{case}_") for case in cases)


def _function(test_id):
    return test_id.rsplit(".", 1)[-1]


def _text(value):
    """JSON text for the word lists, keeping non-ASCII as written and curly apostrophes straight."""
    return json.dumps(value, ensure_ascii=False).replace("\u2019", "'")


def _local(path, run_dir):
    """A saved path, or the same file under ``run_dir`` when the evidence was copied elsewhere."""
    if not isinstance(path, str) or not path:
        return None
    candidate = Path(path)
    if candidate.exists():
        return candidate
    parts = candidate.parts
    for index in range(len(parts) - 2):
        if parts[index:index + 2] == (".autocode", "runs"):
            return Path(run_dir).joinpath(*parts[index + 3:])
    return candidate


def _read(path):
    try:
        return json.loads(Path(path).read_text()) if path else None
    except (OSError, ValueError, TypeError):
        return None
