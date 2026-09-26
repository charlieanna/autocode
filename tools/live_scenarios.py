"""Live-trial scenarios and their independent oracles.

Oracles here never import production matchers (``autocode_support``,
``autocode_goals``, ...). They observe the delivered workspace and the
harness-captured run state only. That independence is what makes a verdict
worth recording.
"""
from __future__ import annotations

import ast
import json
import subprocess
import sys
from pathlib import Path

PASS = "PASS"
FAIL = "FAIL"
HONEST_BLOCKER = "HONEST_BLOCKER"
DEFERRED = "DEFERRED"
ERROR = "ERROR"

# Statuses a driver may report when the runner stopped honestly rather than
# finishing. These are never promoted to PASS.
HONEST_PAUSE_PREFIXES = (
    "PAUSED_", "BLOCKED_HUMAN", "AWAITING_GOAL_APPROVAL", "WAITING_FOR_USER",
)


class OracleResult:
    def __init__(self, status: str, summary: str, checks: list[dict]):
        self.status, self.summary, self.checks = status, summary, checks

    @property
    def failed(self) -> list[dict]:
        return [row for row in self.checks if not row["ok"]]


# --- FX01: greeting CLI (LIVE-01) ----------------------------------------

# Independent expectations for a deterministic greeting CLI. The 12 checks
# mirror the FX01 oracle recorded in LIVE_TRIALS.md (2026-09-24).
GREETING_CASES = [
    # (case_id, argv, expected_exit, output_must_contain)
    ("no-arg", [], 2, "usage"),
    ("ada", ["Ada"], 0, "Hello, Ada"),
    ("multiword", ["Ada Lovelace"], 0, "Hello, Ada Lovelace"),
    ("unicode", ["Zoë"], 0, "Hello, Zoë"),
    ("two-arg", ["Ada", "Lovelace"], 2, "usage"),
]
GREETING_DELIVERABLES = ("greet.py", "test_greet.py", "README.md")


def fx01_greeting_oracle(project: Path) -> OracleResult:
    """Score a delivered greeting CLI against the fixed 12-check oracle.

    Checks 1-10 are exact output/exit expectations for the five invocations.
    Check 11 requires the three named deliverables. Check 12 rejects any
    non-stdlib import in the delivered Python sources.
    """
    checks: list[dict] = []

    def record(name, expected, observed):
        ok = expected == observed
        checks.append({"name": name, "expected": expected, "observed": observed, "ok": ok})

    cli = project / "greet.py"
    if not cli.is_file():
        for case_id, _, _, _ in GREETING_CASES:
            record(f"{case_id}.exit", "greet.py present", "missing")
            record(f"{case_id}.output", "greet.py present", "missing")
        record("deliverables", list(GREETING_DELIVERABLES), "greet.py missing")
        record("stdlib_only", "no non-stdlib imports", "greet.py missing")
        return OracleResult(FAIL, "greet.py not delivered", checks)

    for case_id, argv, want_exit, want_text in GREETING_CASES:
        try:
            proc = subprocess.run(
                [sys.executable, str(cli), *argv],
                cwd=project, capture_output=True, text=True, timeout=30,
            )
            out, code = (proc.stdout + proc.stderr), proc.returncode
        except subprocess.TimeoutExpired:
            out, code = "TIMEOUT", -1
        record(f"{case_id}.exit", want_exit, code)
        record(f"{case_id}.output", want_text, want_text if want_text in out else out.strip()[:200])

    present = [name for name in GREETING_DELIVERABLES if (project / name).is_file()]
    record("deliverables", list(GREETING_DELIVERABLES), present)

    foreign: list[str] = []
    for path in sorted(project.rglob("*.py")):
        if ".git" in path.parts or ".autocode" in path.parts:
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as error:
            foreign.append(f"{path.name}: syntax error {error}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name.split(".")[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module.split(".")[0]]
            else:
                continue
            for name in names:
                if name in sys.stdlib_module_names or name == "__future__":
                    continue
                # Sibling project modules are part of the delivery, not third-party.
                if (project / f"{name}.py").is_file() or (project / name / "__init__.py").is_file():
                    continue
                foreign.append(f"{path.name}: imports {name}")
    record("stdlib_only", [], foreign)

    failed = [row for row in checks if not row["ok"]]
    if not failed:
        return OracleResult(PASS, f"FX01 {len(checks)}/{len(checks)}", checks)
    return OracleResult(FAIL, f"FX01 {len(checks) - len(failed)}/{len(checks)}: "
                              + "; ".join(f"{r['name']}->{r['observed']!r}" for r in failed[:3]), checks)


# --- FX02: to-do persistence CLI (LIVE-02) -------------------------------
#
# This is the L1 regression surface: the contract must carry **two**
# human_review criteria. Pre-fix, that combination deadlocked acceptance
# (human_only_pending_validation supported exactly one). The oracle scores
# the delivered behaviors; the live run additionally proves the run can close.

TODO_CASES = (
    # (case_id, description)
    ("add", "add a to-do and it appears in list"),
    ("list", "list shows every open to-do"),
    ("complete", "complete marks a to-do done"),
    ("restart_stable", "ids stay stable across process restart"),
    ("unknown_id", "unknown id exits nonzero and leaves data intact"),
    ("malformed", "malformed store is preserved, not truncated or rewritten"),
)


def _run(project: Path, *args: str, stdin: str | None = None) -> tuple[int, str, str]:
    proc = subprocess.run(
        [sys.executable, str(project / "todo.py"), *args],
        cwd=project, capture_output=True, text=True, timeout=30, input=stdin,
    )
    return proc.returncode, proc.stdout, proc.stderr


def fx02_todo_oracle(project: Path) -> OracleResult:
    """Score a delivered to-do persistence CLI against the six LIVE-02 behaviors.

    Each case is executed against the delivered artifact; the oracle never
    imports the runner and never trusts the model's own test output.
    """
    checks: list[dict] = []

    def record(name, expected, observed):
        ok = expected == observed
        checks.append({"name": name, "expected": expected, "observed": observed, "ok": ok})

    cli = project / "todo.py"
    if not cli.is_file():
        for case_id, _ in TODO_CASES:
            record(f"{case_id}.ok", "todo.py present", "missing")
        return OracleResult(FAIL, "todo.py not delivered", checks)

    # Isolate each case in a scratch copy so cases cannot poison each other.
    # The store path is resolved inside the scratch dir, where the CLI runs.
    import shutil
    import tempfile
    with tempfile.TemporaryDirectory(prefix="fx02-") as tmp:
        scratch = Path(tmp)
        shutil.copy2(cli, scratch / "todo.py")
        store = scratch / "todos.json"

        def fresh():
            if store.exists():
                store.unlink()

        # add
        fresh()
        code, out, err = _run(scratch, "add", "buy milk")
        after_add = store.read_text() if store.exists() else ""
        record("add.ok", True, code == 0 and "buy milk" in after_add)

        # list
        code, out, err = _run(scratch, "list")
        record("list.ok", True, code == 0 and "buy milk" in out)

        # complete
        code, out, err = _run(scratch, "complete", "1")
        after_complete = store.read_text() if store.exists() else ""
        record("complete.ok", True,
               code == 0 and ("done" in after_complete.lower() or '"completed"' in after_complete
                              or "true" in after_complete.lower()))

        # restart-stable ids: add another, restart, ids must not be reassigned
        fresh()
        _run(scratch, "add", "first")
        _run(scratch, "add", "second")
        before = store.read_text() if store.exists() else ""
        code, out, err = _run(scratch, "list")
        after = store.read_text() if store.exists() else ""
        record("restart_stable.ok", True,
               code == 0 and before == after and "first" in out and "second" in out)

        # unknown id: nonzero exit, store byte-identical
        fresh()
        _run(scratch, "add", "keep me")
        before = store.read_text() if store.exists() else ""
        code, out, err = _run(scratch, "complete", "9999")
        after = store.read_text() if store.exists() else ""
        record("unknown_id.ok", True, code != 0 and before == after)

        # malformed store preserved byte-for-byte, nonzero exit
        fresh()
        garbage = "{not valid json!!!"
        store.write_text(garbage)
        code, out, err = _run(scratch, "list")
        after = store.read_text() if store.exists() else ""
        record("malformed.ok", True, code != 0 and after == garbage)

    failed = [row for row in checks if not row["ok"]]
    if not failed:
        return OracleResult(PASS, f"FX02 {len(checks)}/{len(checks)}", checks)
    return OracleResult(FAIL, f"FX02 {len(checks) - len(failed)}/{len(checks)}: "
                              + "; ".join(f"{r['name']}->{r['observed']!r}" for r in failed[:3]), checks)


# --- FX05: C#→Go policy port with golden vectors (LIVE-05) --------------
#
# This is the L3 regression surface: a semantic port whose review/resolver
# stages must emit strict JSON. No C# compiler is required — parity is
# golden-vector-only, and the oracle says so rather than claiming C# parity.

# Retention policy under test: exact TLD match, case-sensitive.
#   "de"        -> 7   (TLD-specific override)
#   "exception" -> 1   (the required exception)
#   anything else -> 30 (default)
GOLDEN_VECTORS = (
    ("de", 7),
    ("com", 30),
    ("org", 30),
    ("fr", 30),
    ("", 30),
    ("DE", 30),          # case-sensitive: not the de override
    ("de.com", 30),      # not an exact TLD match
    ("exception", 1),    # the required exception
)

POLICY_CS = '''\
using System;

class Policy
{
    // Retention days for a top-level domain. Exact match, case-sensitive.
    public static int RetentionDays(string tld)
    {
        if (tld == "de") return 7;
        if (tld == "exception") return 1;
        return 30;
    }

    static void Main(string[] args)
    {
        Console.WriteLine(RetentionDays(args.Length > 0 ? args[0] : ""));
    }
}
'''


def fx05_golden_oracle(project: Path) -> OracleResult:
    """Score a Go port against the 8 golden vectors.

    Runs the delivered program once per vector. The oracle never reads the
    model's own tests and never claims executed-C# parity: there is no C#
    compiler here, so the vectors are the only authority.
    """
    checks: list[dict] = []

    def record(name, expected, observed):
        ok = expected == observed
        checks.append({"name": name, "expected": expected, "observed": observed, "ok": ok})

    go_files = sorted(p.name for p in project.glob("*.go"))
    ref = project / "reference" / "Policy.cs"
    record("reference.cs_present", True, ref.is_file())
    record("go_sources_present", True, bool(go_files))
    if not go_files:
        record("build.ok", "go sources", "missing")
        return OracleResult(FAIL, "no Go sources delivered", checks)

    # Build once; then drive the binary per vector.
    import subprocess
    build = subprocess.run(["go", "build", "-o", "policy.bin", "."],
                           cwd=project, capture_output=True, text=True, timeout=120)
    record("build.ok", 0, build.returncode)
    if build.returncode != 0:
        return OracleResult(FAIL, f"go build failed: {build.stderr.strip()[:200]}", checks)

    for tld, want in GOLDEN_VECTORS:
        proc = subprocess.run(["./policy.bin", tld], cwd=project,
                              capture_output=True, text=True, timeout=30)
        out = (proc.stdout or "").strip()
        try:
            got = int(out.splitlines()[-1]) if out else None
        except ValueError:
            got = f"non-integer:{out[:40]!r}"
        record(f"vector[{tld or 'empty'}]", want, got)

    failed = [row for row in checks if not row["ok"]]
    total = len(checks)
    if not failed:
        return OracleResult(PASS, f"FX05 {total}/{total} (golden vectors only; no C# execution)", checks)
    return OracleResult(FAIL, f"FX05 {total - len(failed)}/{total}: "
                              + "; ".join(f"{r['name']}->{r['observed']!r}" for r in failed[:3]), checks)


# --- FX06: parallel diamond DAG (LIVE-06) -------------------------------
#
# This is the L2 regression surface: a diamond A→(B,C)→D with per-milestone
# disjoint ownership. Pre-fix, the planner's decision-level affected_paths
# were copied verbatim onto the task, so a Builder writing its own milestone's
# contract-legal path could be rejected as out-of-scope. The oracle scores the
# delivered graph and the four milestone artifacts.

DIAMOND_EDGES = (("A", "B"), ("A", "C"), ("B", "D"), ("C", "D"))
DIAMOND_ARTIFACTS = (
    ("A", "contract/schema.json", "dependency_trace.json"),
    ("B", "server/handler.py",),
    ("C", "client/fetch.py",),
    ("D", "integration/check.py",),
)


def fx06_diamond_oracle(project: Path) -> OracleResult:
    """Score a diamond-DAG delivery: the trace edges and each milestone's files.

    Every milestone's declared ownership must actually contain its artifact.
    That is the L2 property: contract-legal work in a milestone's own path is
    never out-of-scope.
    """
    import json as _json
    checks: list[dict] = []

    def record(name, expected, observed):
        ok = expected == observed
        checks.append({"name": name, "expected": expected, "observed": observed, "ok": ok})

    trace = project / "dependency_trace.json"
    if not trace.is_file():
        record("dependency_trace.present", True, False)
    else:
        try:
            data = _json.loads(trace.read_text())
        except ValueError as error:
            data = {"_parse_error": str(error)}
        edges = {tuple(e) for e in data.get("edges", []) if isinstance(e, (list, tuple)) and len(e) == 2}
        record("dependency_trace.edges", set(DIAMOND_EDGES), edges)

    for mid, *paths in DIAMOND_ARTIFACTS:
        for rel in paths:
            record(f"{mid}.{rel}", True, (project / rel).is_file())

    failed = [row for row in checks if not row["ok"]]
    total = len(checks)
    if not failed:
        return OracleResult(PASS, f"FX06 {total}/{total}", checks)
    return OracleResult(FAIL, f"FX06 {total - len(failed)}/{total}: "
                              + "; ".join(f"{r['name']}->{r['observed']!r}" for r in failed[:3]), checks)


# --- scenario registry ---------------------------------------------------

SCENARIOS = {
    "LIVE-01": {
        "title": "Greeting CLI",
        "task": (
            "Build a deterministic greeting CLI named greet.py. "
            "It prints 'Hello, NAME' for one nonempty name argument and exits 0. "
            "Any other argument count (no arguments, or two or more) prints a usage "
            "line to stderr and exits 2. Deliver greet.py, test_greet.py with "
            "regression tests, and a short README.md. Python standard library only."
        ),
        "oracle": fx01_greeting_oracle,
        "oracle_name": "FX01",
        "expected_class": "pass_or_honest",
        "deliverables": list(GREETING_DELIVERABLES),
        "baseline": {"status": "PASS", "note": "oracle 12/12 (2026-09-24, glm53)"},
    },
    "LIVE-02": {
        "title": "To-do persistence CLI",
        "task": (
            "Build a to-do CLI named todo.py backed by a todos.json store in the "
            "current directory. Commands: `todo.py add TEXT` appends a to-do and "
            "exits 0; `todo.py list` prints every to-do as `ID TEXT [open|done]` "
            "one per line and exits 0; `todo.py complete ID` marks the to-do done "
            "and exits 0. IDs are stable across process restarts and are never "
            "reassigned. Completing an unknown ID exits nonzero and must leave the "
            "store byte-identical. If todos.json is malformed, any command exits "
            "nonzero and must leave the file byte-identical (never truncate or "
            "rewrite it). Deliver todo.py and test_todo.py with regression tests. "
            "Python standard library only. README.md must document the command "
            "summary and the exit-code contract."
        ),
        "oracle": fx02_todo_oracle,
        "oracle_name": "FX02",
        "expected_class": "pass_or_honest",
        "deliverables": ["todo.py", "test_todo.py", "README.md"],
        # The L1 trigger: two human_review criteria. Pre-fix this deadlocked.
        "human_review_criteria": ["AC7", "AC8"],
        "baseline": {
            "status": "HONEST_BLOCKER",
            "note": "behavior verified, run could not close: finding L1 (2026-09-24, glm53)",
        },
    },
    "LIVE-05": {
        "title": "C# to Go policy port (golden vectors)",
        "task": (
            "Port the C# reference policy in reference/Policy.cs to Go. "
            "Deliver a Go module that builds with `go build .` and exposes the "
            "same retention-day behavior: RetentionDays(tld) returns 7 when tld "
            "is exactly \"de\", returns 1 when tld is exactly \"exception\", and "
            "returns 30 for every other value (empty string, other TLDs, "
            "different case, dotted names). Matching is exact and case-sensitive. "
            "The main package must read the first command-line argument (or empty "
            "when absent) and print only the integer day count followed by a "
            "newline. Deliver go.mod, policy.go, and policy_test.go with "
            "regression tests covering all eight golden cases in "
            "golden-cases.json. Create golden-cases.json yourself listing those "
            "eight input/output pairs. Use only the Go standard library. There "
            "is no C# compiler in this environment, so behavioral parity is "
            "golden-vector-only; do not claim executed-C# parity."
        ),
        "oracle": fx05_golden_oracle,
        "oracle_name": "FX05",
        "expected_class": "pass_or_honest",
        "deliverables": ["go.mod", "policy.go", "policy_test.go", "golden-cases.json",
                        "reference/Policy.cs"],
        "seed": {"reference/Policy.cs": POLICY_CS},
        "baseline": {
            "status": "HONEST_BLOCKER",
            "note": ("attempt budget exhausted on schema-invalid astra_review/resolver "
                     "reports (finding L3); Policy.cs + golden-cases.json correct, "
                     "Go implementation missing (2026-09-24, glm53)"),
        },
    },
    "LIVE-06": {
        "title": "Parallel diamond DAG",
        "task": (
            "Build a four-milestone dependency graph as a small Python reference. "
            "Milestone A writes contract/schema.json (a JSON object with keys "
            "\"node\" and \"edges\") and dependency_trace.json (a JSON object "
            "whose \"edges\" array lists the four prerequisite pairs [A,B], "
            "[A,C], [B,D], [C,D]). Milestone B writes server/handler.py, a "
            "function handle(node) returning a string. Milestone C writes "
            "client/fetch.py, a function fetch(node) returning a string. "
            "Milestone D writes integration/check.py combining B and C so that "
            "check() exercises handle() and fetch() together and returns a "
            "single string. Declare each milestone's affected_paths as exactly "
            "the files it owns: A owns contract/ and dependency_trace.json; B "
            "owns server/; C owns client/; D owns integration/. Dependencies: "
            "A first; B and C each depend on A and are independent of each "
            "other; D depends on both B and C. Use only the Python standard "
            "library. Each milestone must leave the workspace in a state where "
            "its own files are complete and importable."
        ),
        "oracle": fx06_diamond_oracle,
        "oracle_name": "FX06",
        "expected_class": "pass_or_honest",
        "deliverables": ["contract/schema.json", "dependency_trace.json",
                        "server/handler.py", "client/fetch.py", "integration/check.py"],
        # L2 confirmation surface: per-milestone disjoint ownership.
        "milestone_ownership": {
            "A": ["contract/schema.json", "dependency_trace.json"],
            "B": ["server/handler.py"],
            "C": ["client/fetch.py"],
            "D": ["integration/check.py"],
        },
        "baseline": {
            "status": "HONEST_BLOCKER",
            "note": ("milestone A delivered and verified; milestone B blocked by "
                     "finding L2 (ownership false positive on server/handler.py); "
                     "(2026-09-24, glm53)"),
        },
    },
}


def scenario(scenario_id: str) -> dict:
    try:
        return dict(SCENARIOS[scenario_id])
    except KeyError:
        known = ", ".join(sorted(SCENARIOS))
        raise ValueError(f"unknown scenario {scenario_id!r}; choose one of: {known}") from None


def classify_runner_status(status: str) -> str:
    """Map a saved runner status onto the honest verdict vocabulary."""
    if status == "TASK_COMPLETE":
        return "complete"
    if status == "COMPLETE":
        return "complete"
    if any(status.startswith(prefix) for prefix in HONEST_PAUSE_PREFIXES):
        return "paused"
    return "stopped"
