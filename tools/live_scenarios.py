"""Live-trial scenarios and their independent oracles.

Oracles here never import production matchers (``autocode_support``,
``autocode_goals``, ...). They observe the delivered workspace and the
harness-captured run state only. That independence is what makes a verdict
worth recording.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
import stat
import subprocess
import sys
import tempfile
from pathlib import Path

try:
    from .scenario_verdicts import (  # noqa: F401
        DEFERRED,
        ERROR,
        FAIL,
        FALSE_COMPLETE,
        HONEST_BLOCKER,
        PASS,
        OracleResult,
    )
except ImportError:
    from scenario_verdicts import (  # noqa: F401
        DEFERRED,
        ERROR,
        FAIL,
        FALSE_COMPLETE,
        HONEST_BLOCKER,
        PASS,
        OracleResult,
    )

# Statuses a driver may report when the runner stopped honestly rather than
# finishing. These are never promoted to PASS.
HONEST_PAUSE_PREFIXES = (
    "PAUSED_",
    "BLOCKED_HUMAN",
    "AWAITING_GOAL_APPROVAL",
    "WAITING_FOR_USER",
)


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
                cwd=project,
                capture_output=True,
                text=True,
                timeout=30,
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
    return OracleResult(
        FAIL,
        f"FX01 {len(checks) - len(failed)}/{len(checks)}: "
        + "; ".join(f"{r['name']}->{r['observed']!r}" for r in failed[:3]),
        checks,
    )


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
        cwd=project,
        capture_output=True,
        text=True,
        timeout=30,
        input=stdin,
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
        record(
            "complete.ok",
            True,
            code == 0
            and (
                "done" in after_complete.lower() or '"completed"' in after_complete or "true" in after_complete.lower()
            ),
        )

        # restart-stable ids: add another, restart, ids must not be reassigned
        fresh()
        _run(scratch, "add", "first")
        _run(scratch, "add", "second")
        before = store.read_text() if store.exists() else ""
        code, out, err = _run(scratch, "list")
        after = store.read_text() if store.exists() else ""
        record("restart_stable.ok", True, code == 0 and before == after and "first" in out and "second" in out)

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
    return OracleResult(
        FAIL,
        f"FX02 {len(checks) - len(failed)}/{len(checks)}: "
        + "; ".join(f"{r['name']}->{r['observed']!r}" for r in failed[:3]),
        checks,
    )


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
    ("DE", 30),  # case-sensitive: not the de override
    ("de.com", 30),  # not an exact TLD match
    ("exception", 1),  # the required exception
)

POLICY_CS = """\
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
"""


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

    build = subprocess.run(
        ["go", "build", "-o", "policy.bin", "."], cwd=project, capture_output=True, text=True, timeout=120
    )
    record("build.ok", 0, build.returncode)
    if build.returncode != 0:
        return OracleResult(FAIL, f"go build failed: {build.stderr.strip()[:200]}", checks)

    for tld, want in GOLDEN_VECTORS:
        proc = subprocess.run(["./policy.bin", tld], cwd=project, capture_output=True, text=True, timeout=30)
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
    return OracleResult(
        FAIL,
        f"FX05 {total - len(failed)}/{total}: " + "; ".join(f"{r['name']}->{r['observed']!r}" for r in failed[:3]),
        checks,
    )


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
    (
        "B",
        "server/handler.py",
    ),
    (
        "C",
        "client/fetch.py",
    ),
    (
        "D",
        "integration/check.py",
    ),
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
    return OracleResult(
        FAIL,
        f"FX06 {total - len(failed)}/{total}: " + "; ".join(f"{r['name']}->{r['observed']!r}" for r in failed[:3]),
        checks,
    )


# --- FX07: status filter in an existing project (LIVE-07) ----------------
#
# "Feature in an existing project" (RELIABILITY.md's second bounded case).
# The seeded project is Agent Observatory: a zero-dependency read-only
# dashboard. The feature is status filtering on /api/runs. The oracle checks
# that the existing suite still passes, the documented security invariants
# hold, and the filter actually filters — all via real HTTP, never the
# model's own tests.


def _fixture_workspace(root: Path) -> Path:
    """Two runs with different statuses, in the shape observatory expects."""
    runs = root / ".autocode" / "runs"
    for name, status in (("aaa-running", "RUNNING"), ("bbb-complete", "TASK_COMPLETE")):
        d = runs / name
        d.mkdir(parents=True, exist_ok=True)
        (d / "state.json").write_text(
            json.dumps(
                {
                    "version": 3,
                    "task": f"task {name}",
                    "workspace": str(root),
                    "status": status,
                    "iteration": 1,
                    "sessions": {},
                    "stages": [],
                    "next_stage": "sol" if status == "RUNNING" else None,
                }
            )
        )
    return root


def _http_get(port: int, path: str, timeout: float = 10.0) -> tuple[int, str]:
    import urllib.request

    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode("utf-8", "replace")


def _http_post(port: int, path: str, body: bytes = b"{}", timeout: float = 10.0) -> int:
    import urllib.request

    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=body, method="POST", headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status
    except urllib.error.HTTPError as error:
        return error.code


def fx07_status_filter_oracle(project: Path) -> OracleResult:
    """Score the status-filter feature on the seeded Agent Observatory."""
    import importlib.util
    import socket
    import tempfile
    import threading

    checks: list[dict] = []

    def record(name, expected, observed):
        ok = expected == observed
        checks.append({"name": name, "expected": expected, "observed": observed, "ok": ok})

    observatory = project / "observatory.py"
    record("observatory.py_present", True, observatory.is_file())
    if not observatory.is_file():
        return OracleResult(FAIL, "observatory.py not delivered", checks)

    # 1. Existing suite must still pass — the feature must not regress the project.
    test = subprocess.run([sys.executable, "-m", "unittest"], cwd=project, capture_output=True, text=True, timeout=300)
    record("existing_suite_ok", 0, test.returncode)

    # 2. Zero-dependency promise: stdlib-only imports in every delivered .py.
    foreign: list[str] = []
    for path in sorted(project.rglob("*.py")):
        if any(part in (".git", ".autocode", "__pycache__", ".venv") for part in path.parts):
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
                if (project / f"{name}.py").is_file() or (project / name / "__init__.py").is_file():
                    continue
                foreign.append(f"{path.name}: imports {name}")
    record("stdlib_only", [], foreign)

    # 3-5. Behaviour over real HTTP against a fixture workspace.
    with tempfile.TemporaryDirectory(prefix="fx07-ws-") as tmp:
        watch = _fixture_workspace(Path(tmp))
        spec = importlib.util.spec_from_file_location("fx07_observatory", observatory)
        server_mod = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(server_mod)
        except Exception as error:
            record("importable", True, f"import failed: {error}")
            return OracleResult(FAIL, f"observatory.py is not importable: {error}", checks)
        record("importable", True, True)

        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        try:
            server = server_mod.make_server(watch, port=port)
        except Exception as error:
            record("server_starts", True, f"start failed: {error}")
            return OracleResult(FAIL, f"server would not start: {error}", checks)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            code, body = _http_get(port, "/api/runs")
            record("api_runs_ok", 200, code)
            try:
                payload = json.loads(body)
                rows = payload if isinstance(payload, list) else payload.get("runs", [])
            except ValueError:
                rows = []
            statuses = sorted({str(r.get("status")) for r in rows if isinstance(r, dict)})
            record("api_runs_lists_all", ["RUNNING", "TASK_COMPLETE"], statuses)

            # The feature: ?status= filters.
            code, body = _http_get(port, "/api/runs?status=RUNNING")
            record("filter_http_ok", 200, code)
            try:
                filtered = json.loads(body)
                frows = filtered if isinstance(filtered, list) else filtered.get("runs", [])
            except ValueError:
                frows = []
            got = sorted({str(r.get("status")) for r in frows if isinstance(r, dict)})
            record("filter_status_running", ["RUNNING"], got)
            record("filter_shows_one", 1, len(frows))

            # 3. No mutation endpoints (documented invariant).
            record("post_rejected", 405, _http_post(port, "/api/runs"))
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=5)

    # 6-7. Frontend and docs mention the feature.
    html = (
        (project / "static" / "index.html").read_text(encoding="utf-8", errors="replace")
        if (project / "static" / "index.html").is_file()
        else ""
    )
    js = (
        (project / "static" / "observatory.js").read_text(encoding="utf-8", errors="replace")
        if (project / "static" / "observatory.js").is_file()
        else ""
    )
    record("frontend_exposes_filter", True, ("status" in html.lower() and "filter" in (html + js).lower()))
    readme = (
        (project / "README.md").read_text(encoding="utf-8", errors="replace")
        if (project / "README.md").is_file()
        else ""
    )
    record("readme_documents_filter", True, "filter" in readme.lower() and "status" in readme.lower())

    failed = [row for row in checks if not row["ok"]]
    total = len(checks)
    if not failed:
        return OracleResult(PASS, f"FX07 {total}/{total}", checks)
    return OracleResult(
        FAIL,
        f"FX07 {total - len(failed)}/{total}: " + "; ".join(f"{r['name']}->{r['observed']!r}" for r in failed[:3]),
        checks,
    )


# --- T02/T03: frozen synthetic existing-project fixtures -----------------

CAMPAIGN_TIMEOUT = 3
GREETING_USAGE = "usage: greet.py NAME\n"
GREETING_HELP = GREETING_USAGE + "Print a greeting for NAME.\n"
BUGFIX_SOURCE = """\
import sys


def main(args):
    if args in (["--help"], ["-h"]):
        print("usage: greet.py NAME\\nPrint a greeting for NAME.")
        return 0
    if len(args) != 1:
        print("usage: greet.py NAME", file=sys.stderr)
        return 2
    print(f"Hello, {args[0]}!")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
"""

BUGFIX_ORIGINAL_TESTS = """\
import subprocess
import sys

for args, expected in [
    (["Ada"], (0, "Hello, Ada!\\n", "")),
    ([" Ada "], (0, "Hello,  Ada !\\n", "")),
    ([], (2, "", "usage: greet.py NAME\\n")),
    (["--help"], (0, "usage: greet.py NAME\\nPrint a greeting for NAME.\\n", "")),
]:
    p = subprocess.run([sys.executable, "-I", "-B", "greet.py", *args],
                       capture_output=True, text=True, timeout=2)
    assert (p.returncode, p.stdout, p.stderr) == expected
print("original tests: OK")
"""

NOTES_USAGE = "usage: notes.py list | add TEXT TAG | show ID\n"
NOTES_HELP = NOTES_USAGE + "Store notes in notes.json in the current directory.\n"
NOTES_SOURCE = """\
import json
from pathlib import Path
import sys

USAGE = "usage: notes.py list | add TEXT TAG | show ID"


def main(args):
    if args in (["--help"], ["-h"]):
        print(USAGE + "\\nStore notes in notes.json in the current directory.")
        return 0
    if not (args == ["list"] or
            (len(args) == 3 and args[0] == "add" and args[1].strip() and args[2].strip()) or
            (len(args) == 2 and args[0] == "show" and args[1].isdigit())):
        print(USAGE, file=sys.stderr)
        return 2
    store = Path("notes.json")
    try:
        notes = json.loads(store.read_text(encoding="utf-8")) if store.exists() else []
    except (ValueError, OSError):
        print("error: invalid notes store", file=sys.stderr)
        return 1
    if args[0] == "add":
        ident = max((n["id"] for n in notes), default=0) + 1
        notes.append({"id": ident, "text": args[1], "tag": args[2]})
        store.write_text(json.dumps(notes, ensure_ascii=False, indent=2) + "\\n", encoding="utf-8")
        print(f"added {ident}")
    elif args[0] == "show":
        matches = [n for n in notes if n["id"] == int(args[1])]
        if not matches:
            print("error: note not found", file=sys.stderr)
            return 1
        n = matches[0]
        print(f'{n["id"]}: {n["text"]} [{n["tag"]}]')
    else:
        for n in notes:
            print(f'{n["id"]}: {n["text"]} [{n["tag"]}]')
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
"""

NOTES_DATA = '[\n  {"id": 2, "text": "Ship draft", "tag": "Work"},\n  {"id": 5, "text": "Buy tea", "tag": "home"},\n  {"id": 9, "text": "Review draft", "tag": "WORK"}\n]\n'
NOTES_ORIGINAL_TESTS = """\
import subprocess
import sys

for args, expected in [
    (["list"], (0, "2: Ship draft [Work]\\n5: Buy tea [home]\\n9: Review draft [WORK]\\n", "")),
    (["show", "5"], (0, "5: Buy tea [home]\\n", "")),
    (["show", "999"], (1, "", "error: note not found\\n")),
    ([], (2, "", "usage: notes.py list | add TEXT TAG | show ID\\n")),
]:
    p = subprocess.run([sys.executable, "-I", "-B", "notes.py", *args],
                       capture_output=True, text=True, timeout=2)
    assert (p.returncode, p.stdout, p.stderr) == expected
print("original tests: OK")
"""

BUGFIX_SEED = {
    "greet.py": BUGFIX_SOURCE,
    "test_original.py": BUGFIX_ORIGINAL_TESTS,
    "README.md": "Greeting CLI: python greet.py NAME; --help or -h for help.\nValid names are printed verbatim. Missing/extra arguments exit 2.\n",
    "settings.json": '{"format_version": 1, "owner": "synthetic-fixture"}\n',
}
FEATURE_SEED = {
    "notes.py": NOTES_SOURCE,
    "notes.json": NOTES_DATA,
    "test_original.py": NOTES_ORIGINAL_TESTS,
    "README.md": "Notes CLI: list; add TEXT TAG; show ID; --help or -h.\nOutput: ID: TEXT [TAG]. IDs increase from the largest saved ID.\n",
    "settings.json": '{"format_version": 1, "owner": "synthetic-fixture"}\n',
}


def _campaign_process(scratch: Path, script: str, args=()) -> dict:
    """Execute only a scratch copy; never import candidate code into the oracle.

    This is isolation from accidental writes, not an OS security sandbox. The
    driver must enforce filesystem/network permissions for untrusted candidates.
    """
    try:
        proc = subprocess.run(
            [sys.executable, "-I", "-B", script, *args],
            cwd=scratch,
            env={"PATH": os.defpath, "HOME": str(scratch), "TMPDIR": str(scratch), "PYTHONIOENCODING": "utf-8"},
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            timeout=CAMPAIGN_TIMEOUT,
        )
        return {"exit": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr}
    except subprocess.TimeoutExpired:
        return {"error": "TIMEOUT", "timeout_seconds": CAMPAIGN_TIMEOUT}
    except OSError as error:
        return {"error": type(error).__name__, "detail": str(error)}


def _campaign_oracle(project: Path, scenario_id: str) -> OracleResult:
    """Frozen behavior, original tests and byte-level scope, without project writes."""
    spec = SCENARIOS[scenario_id]
    seed = spec["seed"]
    cli = spec["allowed_paths"][0]
    checks = []

    def record(name, expected, observed):
        checks.append({"name": name, "expected": expected, "observed": observed, "ok": expected == observed})

    def snapshot():
        files, unsafe = {}, []
        if project.is_symlink() or not project.is_dir():
            return {}, ["project must be a regular directory"]
        for root, dirs, names in os.walk(project, followlinks=False):
            for name in list(dirs):
                path = Path(root) / name
                rel = path.relative_to(project).as_posix()
                if path.is_symlink():
                    unsafe.append(rel)
                    dirs.remove(name)
                elif Path(root) == project and name in (".git", ".autocode"):
                    dirs.remove(name)
                else:
                    # These deliberately flat fixtures have no source subtrees.
                    unsafe.append(rel + "/")
                    dirs.remove(name)
            for name in names:
                path = Path(root) / name
                rel = path.relative_to(project).as_posix()
                mode = path.lstat().st_mode
                if not stat.S_ISREG(mode):
                    unsafe.append(rel)
                else:
                    files[rel] = {
                        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                        "executable": bool(mode & 0o111),
                    }
        return files, sorted(unsafe)

    try:
        before, unsafe = snapshot()
        record("scope.regular_files", [], unsafe)
        record("scope.unexpected_paths", [], sorted(set(before) - set(seed) - set(spec["allowed_paths"])))
        for rel in seed:
            if rel in spec["allowed_paths"]:
                continue
            record(
                f"protected.{rel}",
                {"sha256": hashlib.sha256(seed[rel].encode()).hexdigest(), "executable": False},
                before.get(rel),
            )
        for rel in spec["allowed_paths"]:
            record(f"delivery.{rel}", True, rel in before and not before[rel]["executable"])
        foreign = []
        for rel in spec["allowed_paths"]:
            if rel not in before:
                continue
            try:
                tree = ast.parse((project / rel).read_text(encoding="utf-8"))
                for node in ast.walk(tree):
                    names = []
                    if isinstance(node, ast.Import):
                        names = [alias.name.split(".")[0] for alias in node.names]
                    elif isinstance(node, ast.ImportFrom) and node.module:
                        names = [node.module.split(".")[0]]
                    foreign.extend(
                        f"{rel}: {name}"
                        for name in names
                        if name not in sys.stdlib_module_names and name != Path(cli).stem
                    )
            except (SyntaxError, UnicodeError) as error:
                foreign.append(f"{rel}: {type(error).__name__}")
        record("scope.stdlib_imports", [], foreign)
        if any(not row["ok"] for row in checks):
            return OracleResult(FAIL, f"{scenario_id}: scope or protected-file violation", checks)

        with tempfile.TemporaryDirectory(prefix=f"{scenario_id.lower()}-oracle-") as tmp:
            scratch = Path(tmp)
            for rel in before:
                target = scratch / rel
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes((project / rel).read_bytes())

            def run(name, args, code, out="", err=""):
                record(name, {"exit": code, "stdout": out, "stderr": err}, _campaign_process(scratch, cli, args))

            record(
                "original_tests",
                {"exit": 0, "stdout": "original tests: OK\n", "stderr": ""},
                _campaign_process(scratch, "test_original.py"),
            )

            if scenario_id == "BUGFIX-01":
                for i, name in enumerate(("", " ", "\t\n", "\u2003", "\r\n ")):
                    run(f"blank.{i}", [name], 2, err="error: name must not be blank\n")
                for i, name in enumerate(("Ada", "Ada Lovelace", "Zo\u00eb", " Ada ", "x\ty", "--unknown")):
                    run(f"valid.{i}", [name], 0, f"Hello, {name}!\n")
                for i, args in enumerate(([], ["Ada", "Lovelace"], ["", "x"], ["--help", "x"])):
                    run(f"arity.{i}", args, 2, err=GREETING_USAGE)
                for flag in ("-h", "--help"):
                    run(f"help.{flag}", [flag], 0, GREETING_HELP)
            else:
                store = scratch / "notes.json"
                original = store.read_bytes()
                all_notes = "2: Ship draft [Work]\n5: Buy tea [home]\n9: Review draft [WORK]\n"
                work_notes = "2: Ship draft [Work]\n9: Review draft [WORK]\n"
                for tag, output in (
                    ("work", work_notes),
                    ("wOrK", work_notes),
                    ("HOME", "5: Buy tea [home]\n"),
                    ("absent", ""),
                    ("wor", ""),
                    (" work ", ""),
                ):
                    run(f"filter.{tag!r}", ["list", "--tag", tag], 0, output)
                    record(f"filter.{tag!r}.data", original.hex(), store.read_bytes().hex())
                for i, args in enumerate(
                    (
                        ["list", "--tag"],
                        ["list", "--tag", ""],
                        ["list", "--tag", " \t"],
                        ["list", "--tag", "\u2003"],
                        ["list", "--tag", "work", "extra"],
                        ["show", "2", "--tag", "work"],
                        ["list", "--bad", "work"],
                        [],
                        ["delete", "2"],
                        ["add", "", "work"],
                        ["show", "x"],
                    )
                ):
                    run(f"invalid.{i}", args, 2, err=NOTES_USAGE)
                    record(f"invalid.{i}.data", original.hex(), store.read_bytes().hex())
                run("old.list", ["list"], 0, all_notes)
                run("old.show", ["show", "2"], 0, "2: Ship draft [Work]\n")
                run("old.unknown_id", ["show", "999"], 1, err="error: note not found\n")
                for flag in ("-h", "--help"):
                    run(f"old.help.{flag}", [flag], 0, NOTES_HELP)
                record("old.readonly_data", original.hex(), store.read_bytes().hex())
                run("old.add", ["add", "Meet Zo\u00eb", "wOrK"], 0, "added 10\n")
                expected = json.loads(NOTES_DATA) + [{"id": 10, "text": "Meet Zo\u00eb", "tag": "wOrK"}]
                record(
                    "old.add.data",
                    json.dumps(expected, ensure_ascii=False, indent=2) + "\n",
                    store.read_text(encoding="utf-8"),
                )
                added = store.read_bytes()
                run("restart.filter", ["list", "--tag", "WORK"], 0, work_notes + "10: Meet Zo\u00eb [wOrK]\n")
                run("restart.show", ["show", "10"], 0, "10: Meet Zo\u00eb [wOrK]\n")
                record("restart.data", added.hex(), store.read_bytes().hex())
                # A second saved dataset rejects hard-coded seed answers and
                # distinguishes Unicode casefold equality from lower/substring.
                store.write_text(
                    '[{"id": 17, "text": "Travel", "tag": "Stra\\u00dfe"}, {"id": 4, "text": "Trip", "tag": "other"}]\n'
                )
                holdout = store.read_bytes()
                run("saved.unicode_casefold", ["list", "--tag", "STRASSE"], 0, "17: Travel [Stra\u00dfe]\n")
                record("saved.data", holdout.hex(), store.read_bytes().hex())
                store.write_text("[]\n")
                run("empty.filter", ["list", "--tag", "work"], 0)
                record("empty.data", "[]\n", store.read_text())
                store.write_text("{broken json\n")
                for args in (["list"], ["list", "--tag", "work"], ["add", "x", "work"], ["show", "2"]):
                    run(f"malformed.{args[0]}.{len(args)}", args, 1, err="error: invalid notes store\n")
                    record(f"malformed.{args[0]}.{len(args)}.data", "{broken json\n", store.read_text())

            # Execute added candidate regressions separately; their success is
            # supporting evidence only, never a replacement for checks above.
            for rel, body in seed.items():
                if rel != cli:
                    (scratch / rel).write_text(body, encoding="utf-8")
            regression = _campaign_process(scratch, "test_regression.py")
            record("candidate_regressions.exit", 0, regression.get("exit", regression))
            # Prove that the submitted regression detects the original defect,
            # not merely that a file with the requested name exits successfully.
            with tempfile.TemporaryDirectory(prefix="campaign-seed-control-") as seed_tmp:
                seed_scratch = Path(seed_tmp)
                for rel, body in seed.items():
                    (seed_scratch / rel).write_text(body, encoding="utf-8")
                (seed_scratch / "test_regression.py").write_bytes((project / "test_regression.py").read_bytes())
                seed_regression = _campaign_process(seed_scratch, "test_regression.py")
                record(
                    "seed_regressions.detect_defect",
                    True,
                    isinstance(seed_regression.get("exit"), int) and seed_regression["exit"] > 0,
                )
        after, after_unsafe = snapshot()
        record("oracle.project_unchanged", before, after)
        record("oracle.paths_unchanged", unsafe, after_unsafe)
    except (OSError, ValueError) as error:
        record("oracle.readable_artifact", True, f"{type(error).__name__}: {error}")
    failed = sum(not row["ok"] for row in checks)
    return OracleResult(FAIL if failed else PASS, f"{scenario_id}: {len(checks) - failed}/{len(checks)} checks", checks)


def bugfix_blank_name_oracle(project: Path) -> OracleResult:
    return _campaign_oracle(project, "BUGFIX-01")


def feature_tag_filter_oracle(project: Path) -> OracleResult:
    return _campaign_oracle(project, "FEATURE-01")


# --- scenario registry ---------------------------------------------------

SCENARIOS = {
    "BUGFIX-01": {
        "title": "T02: blank-name fix in a seeded greeting CLI",
        "plan_id": "T02",
        "task_type": "bugfix",
        "task": (
            "Fix only the blank-name validation bug in the existing greet.py. "
            "For exactly one argument whose str.strip() is empty (including Unicode "
            "whitespace), print exactly 'error: name must not be blank\\n' to stderr, "
            "nothing to stdout, and exit 2. Preserve every valid name verbatim: "
            "stdout 'Hello, NAME!\\n', empty stderr, exit 0; do not trim valid names. "
            "Preserve -h/--help and missing/extra argument behavior exactly as seeded. "
            "Only greet.py and a new runnable test_regression.py may change (regular "
            "non-executable-mode files, run using Python). New regressions must fail "
            "against the unchanged seed and pass against the fix. "
            "Run original tests with python -B test_original.py and new tests with "
            "python -B test_regression.py. Do not weaken original tests, change README.md "
            "or settings.json, add dependencies, or rewrite unrelated code. Python stdlib only."
        ),
        "oracle": bugfix_blank_name_oracle,
        "oracle_name": "T02-blank-name-v1",
        "expected_class": "pass_or_honest",
        "synthetic_seed_only": True,
        "seed": BUGFIX_SEED,
        "allowed_paths": ["greet.py", "test_regression.py"],
        "protected_paths": ["test_original.py", "README.md", "settings.json"],
        "deliverables": ["greet.py", "test_regression.py"],
        "acceptance_criteria": {
            "AC1": "Blank and whitespace-only names fail with exact error and exit 2.",
            "AC2": "Valid, padded, Unicode names; help and arity contracts unchanged.",
            "AC3": "Original tests unchanged and passing; new regressions fail on seed and pass on fix.",
            "AC4": "Only allowed paths changed; all protected bytes and modes preserved.",
        },
        "baseline": {
            "status": "ERROR",
            "profile": "glm53-mimo",
            "record": "VALIDATION.md",
            "note": "2026-09-26: report-repair fix rerun reached Builder but exhausted 1200-second deadline; unchanged seed still fails oracle; no completed delivery",
        },
    },
    "FEATURE-01": {
        "title": "T03: tag filtering in a seeded persistent notes CLI",
        "plan_id": "T03",
        "task_type": "feature",
        "task": (
            "Add `notes.py list --tag TAG` to the existing notes CLI. Match the whole "
            "stored tag using Unicode str.casefold() equality, without trimming. "
            "Print matching notes in saved order using the existing 'ID: TEXT [TAG]\\n' "
            "format; no matches or empty store means empty stdout, empty stderr, exit 0. "
            "Missing, empty, or whitespace-only TAG and extra/invalid arguments must "
            "produce exactly the existing usage line on stderr, empty stdout, exit 2. "
            "Retain list, add, show, -h/--help, errors, output and persistence contracts "
            "exactly. Read-only commands and failed commands must preserve notes.json "
            "byte-for-byte. Malformed JSON keeps its bytes and emits 'error: invalid "
            "notes store\\n' on stderr with exit 1. Keep saved IDs, text, tag case, and "
            "order; add allocates max ID + 1 and persists across process restarts. "
            "Only notes.py and a new runnable test_regression.py may change (regular "
            "non-executable-mode files, run using Python). New regressions must fail "
            "against the unchanged seed and pass against the feature. "
            "Keep notes.json, test_original.py, README.md and settings.json unchanged. "
            "Run python -B test_original.py and python -B test_regression.py. "
            "Python stdlib only; no dependencies, schema migration, or unrelated rewrite."
        ),
        "oracle": feature_tag_filter_oracle,
        "oracle_name": "T03-tag-filter-v1",
        "expected_class": "pass_or_honest",
        "synthetic_seed_only": True,
        "seed": FEATURE_SEED,
        "allowed_paths": ["notes.py", "test_regression.py"],
        "protected_paths": ["notes.json", "test_original.py", "README.md", "settings.json"],
        "deliverables": ["notes.py", "test_regression.py"],
        "acceptance_criteria": {
            "AC1": "Whole-tag Unicode casefold matching; mixed case, absent and empty-store results.",
            "AC2": "Invalid filters give exact usage/exit 2 without modifying saved data.",
            "AC3": "Existing commands, output, stable IDs and restart persistence retained.",
            "AC4": "Read-only and malformed-store paths preserve bytes; saved fixture unchanged.",
            "AC5": "Frozen original tests pass; new regressions fail on seed/pass on feature; only allowed paths change.",
        },
        "baseline": {
            "status": "ERROR",
            "profile": "glm53-mimo",
            "record": "VALIDATION.md",
            "note": "2026-09-26: externally interrupted during report repair; unchanged seed fails oracle; no completed delivery",
        },
    },
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
            'is exactly "de", returns 1 when tld is exactly "exception", and '
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
        "deliverables": ["go.mod", "policy.go", "policy_test.go", "golden-cases.json", "reference/Policy.cs"],
        "seed": {"reference/Policy.cs": POLICY_CS},
        "baseline": {
            "status": "HONEST_BLOCKER",
            "note": (
                "attempt budget exhausted on schema-invalid astra_review/resolver "
                "reports (finding L3); Policy.cs + golden-cases.json correct, "
                "Go implementation missing (2026-09-24, glm53)"
            ),
        },
    },
    "LIVE-06": {
        "title": "Parallel diamond DAG",
        "task": (
            "Build a four-milestone dependency graph as a small Python reference. "
            "Milestone A writes contract/schema.json (a JSON object with keys "
            '"node" and "edges") and dependency_trace.json (a JSON object '
            'whose "edges" array lists the four prerequisite pairs [A,B], '
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
        "deliverables": [
            "contract/schema.json",
            "dependency_trace.json",
            "server/handler.py",
            "client/fetch.py",
            "integration/check.py",
        ],
        # L2 confirmation surface: per-milestone disjoint ownership.
        "milestone_ownership": {
            "A": ["contract/schema.json", "dependency_trace.json"],
            "B": ["server/handler.py"],
            "C": ["client/fetch.py"],
            "D": ["integration/check.py"],
        },
        "baseline": {
            "status": "HONEST_BLOCKER",
            "note": (
                "milestone A delivered and verified; milestone B blocked by "
                "finding L2 (ownership false positive on server/handler.py); "
                "(2026-09-24, glm53)"
            ),
        },
    },
    "LIVE-07": {
        "title": "Status filter in an existing project (Agent Observatory)",
        "task": (
            "Agent Observatory is an existing zero-dependency read-only dashboard "
            "for local autocode runs (see README.md and observatory.py). Add a "
            "status filter feature with these decisions already fixed: status "
            "matching is exact case-sensitive equality against each run's "
            "reported status string; the browser control filters server-side by "
            "appending ?status= to the existing /api/runs fetch and dropping the "
            "parameter when cleared; and the filter narrows only the run cards "
            "in the Runs section, leaving the Action needed section unchanged. "
            "GET /api/runs must keep returning every run when no filter is "
            "supplied, and must return only the runs whose status equals the "
            "`status` query parameter when it is supplied "
            "(for example /api/runs?status=RUNNING). The browser UI must expose "
            "the filter: a control the user can use to narrow the visible run "
            "cards by status, including a way to clear it and show all again. "
            "Preserve every documented invariant: Python standard library only "
            "(zero dependencies), read-only (POST/PUT/DELETE stay rejected), "
            "only the existing three local resources plus the JSON data route, "
            "all state-controlled text rendered as text not HTML, and malformed "
            "run state isolated to that run. Keep the existing test suite green "
            "and add tests covering the new filter. Update README.md to document "
            "the filter. Do not add mutation endpoints and do not import or "
            "invoke autocode."
        ),
        "oracle": fx07_status_filter_oracle,
        "oracle_name": "FX07",
        "expected_class": "pass_or_honest",
        "deliverables": ["observatory.py", "static/index.html", "static/observatory.js", "README.md"],
        # RELIABILITY.md's second bounded case: feature in an existing project.
        "seed_from": "/Users/ankurkothari/Documents/workspace/agent-observatory",
        "baseline": {
            "status": "NOT_RUN",
            "note": "no prior run; this is RELIABILITY.md's feature-in-existing-project case",
        },
    },
}


def _dual_layout_oracle(spec_id, rich_spec, thin_spec):
    """Score seeded campaign projects with the campaign oracle and delivered
    reference packages (score-only mode) with the task-type oracle."""
    rich_oracle, thin_oracle = rich_spec["oracle"], thin_spec["oracle"]
    seed_names = set(rich_spec["seed"])

    def oracle(project):
        names = {row.name for row in Path(project).iterdir()} if Path(project).is_dir() else set()
        return rich_oracle(project) if seed_names <= names else thin_oracle(project)

    return {**rich_spec, "oracle": oracle}


def registry() -> dict:
    """Every registered scenario: the task-type catalogue plus this module's
    richer campaign definitions, which win for ids both define."""
    try:
        from . import task_scenarios
    except ImportError:
        import task_scenarios
    merged = dict(task_scenarios.SCENARIOS)
    for scenario_id, spec in SCENARIOS.items():
        merged[scenario_id] = (
            _dual_layout_oracle(scenario_id, spec, merged[scenario_id]) if scenario_id in merged else spec
        )
    return merged


def scenario(scenario_id: str) -> dict:
    known = registry()
    try:
        return dict(known[scenario_id])
    except KeyError:
        names = ", ".join(sorted(known))
        raise ValueError(f"unknown scenario {scenario_id!r}; choose one of: {names}") from None


def classify_runner_status(status: str) -> str:
    """Map a saved runner status onto the honest verdict vocabulary."""
    if status == "TASK_COMPLETE":
        return "complete"
    if status == "COMPLETE":
        return "complete"
    if any(status.startswith(prefix) for prefix in HONEST_PAUSE_PREFIXES):
        return "paused"
    return "stopped"
