"""Task-type scenarios: bug fix, feature, architecture, multi-service program, UI.

Each scenario freezes one kind of engineering request the runner must handle
(see README "Support the engineering outcome, not one compulsory coding
pipeline") together with an independent oracle. Oracles observe only the
delivered workspace: they never import production matchers, never read the
runner's own reports, and never trust a candidate's green tests.

Scenario ids are typed on purpose (``BUGFIX-01``, ``FEATURE-01``, ``ARCH-01``,
``PROGRAM-01``, ``UI-01``) so a result row says what kind of work was tried.
``tools/live_trial.py`` drives any of them; ``tools/test_scenario_oracles.py``
proves each oracle against a reference delivery and broken variants before it
is allowed to score a live run.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from html.parser import HTMLParser
from pathlib import Path

try:
    from . import live_scenarios as base
except ImportError:  # pragma: no cover - script execution
    import live_scenarios as base

OracleResult = base.OracleResult
PASS, FAIL, DEFERRED, ERROR = base.PASS, base.FAIL, base.DEFERRED, base.ERROR

IGNORED_DIRS = {".git", ".autocode", "__pycache__", ".pytest_cache", "node_modules"}


class Checks:
    """Ordered oracle rows; ``result`` folds them into an OracleResult."""

    def __init__(self, label: str):
        self.label = label
        self.rows: list[dict] = []

    def record(self, name: str, expected, observed) -> bool:
        ok = expected == observed
        self.rows.append({"name": name, "expected": expected, "observed": observed, "ok": ok})
        return ok

    @property
    def failed(self) -> list[dict]:
        return [row for row in self.rows if not row["ok"]]

    def result(self, *, note: str = "", deferred: str = "") -> OracleResult:
        total = len(self.rows)
        failed = self.failed
        suffix = f" ({note})" if note else ""
        if failed:
            detail = "; ".join(f"{r['name']}->{r['observed']!r}" for r in failed[:3])
            return OracleResult(FAIL, f"{self.label} {total - len(failed)}/{total}: {detail}{suffix}", self.rows)
        if deferred:
            return OracleResult(DEFERRED, f"{self.label} {total}/{total} static; {deferred}{suffix}", self.rows)
        return OracleResult(PASS, f"{self.label} {total}/{total}{suffix}", self.rows)


def _run(cmd: list[str], cwd: Path, timeout: int = 60, stdin: str | None = None) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=timeout, input=stdin)
    except subprocess.TimeoutExpired:
        return -1, "", "TIMEOUT"
    return proc.returncode, proc.stdout, proc.stderr


def workspace_files(project: Path) -> set[str]:
    """Repository-relative files, ignoring VCS and runner metadata."""
    found = set()
    for path in project.rglob("*"):
        rel = path.relative_to(project)
        if any(part in IGNORED_DIRS for part in rel.parts) or not path.is_file():
            continue
        found.add(rel.as_posix())
    return found


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _http(method: str, url: str, body: dict | None = None, timeout: float = 10) -> tuple[int, object]:
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method,
                                     headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode(errors="replace")
            status = response.status
    except urllib.error.HTTPError as error:
        raw = error.read().decode(errors="replace")
        status = error.code
    except (urllib.error.URLError, OSError, TimeoutError) as error:
        return -1, f"unreachable: {error}"
    try:
        return status, json.loads(raw) if raw.strip() else None
    except ValueError:
        return status, raw


def _wait_http(url: str, deadline: float) -> bool:
    while time.monotonic() < deadline:
        status, _ = _http("GET", url, timeout=2)
        if status == 200:
            return True
        time.sleep(0.2)
    return False


# --- BUGFIX-01: blank-name validation bug in a committed CLI ----------------

BUGFIX_SEED = {
    "greet.py": '''\
"""Greeting CLI (committed before the bug report)."""
import sys

USAGE = "usage: greet.py NAME"


def greet(name: str) -> str:
    return f"Hello, {name}"


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1:
        print(USAGE, file=sys.stderr)
        return 2
    print(greet(argv[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
''',
    "test_greet.py": '''\
import subprocess
import sys
import unittest

from greet import greet


class TestGreet(unittest.TestCase):
    def test_ada(self):
        self.assertEqual(greet("Ada"), "Hello, Ada")

    def test_no_arg(self):
        proc = subprocess.run([sys.executable, "greet.py"], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)
        self.assertIn("usage", proc.stderr.lower())

    def test_two_arg(self):
        proc = subprocess.run([sys.executable, "greet.py", "Ada", "Lovelace"],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 2)


if __name__ == "__main__":
    unittest.main()
''',
    "README.md": "# Greeting CLI\n\n`greet.py NAME` prints `Hello, NAME`. Invalid usage exits 2.\n",
}
BUGFIX_SEED_TESTS = ("test_ada", "test_no_arg", "test_two_arg")
BUGFIX_DELIVERABLES = ("greet.py", "test_greet.py", "README.md")


def bugfix01_oracle(project: Path) -> OracleResult:
    """Fix must reject blank names, keep valid behavior, and add a real regression.

    The regression is real only if the candidate's own test file fails against
    the seeded (buggy) greet.py and passes against the candidate.
    """
    checks = Checks("BUGFIX-01")
    cli, tests = project / "greet.py", project / "test_greet.py"
    if not cli.is_file():
        checks.record("greet.py.present", True, False)
        return checks.result()
    cases = [("blank", [""], 2), ("whitespace", ["   "], 2), ("ada", ["Ada"], 0),
             ("no-arg", [], 2), ("two-arg", ["Ada", "Lovelace"], 2)]
    for case_id, argv, want_exit in cases:
        code, out, err = _run([sys.executable, "greet.py", *argv], cwd=project, timeout=30)
        checks.record(f"{case_id}.exit", want_exit, code)
        if want_exit == 2:
            checks.record(f"{case_id}.usage_on_stderr", True, "usage" in err.lower() and out == "")
        else:
            checks.record(f"{case_id}.output", "Hello, Ada", out.strip())
    text = tests.read_text(encoding="utf-8", errors="replace") if tests.is_file() else ""
    for name in BUGFIX_SEED_TESTS:
        checks.record(f"seed_test_kept.{name}", True, f"def {name}(" in text)
    code, _, err = _run([sys.executable, "-m", "unittest", "-q", "test_greet"], cwd=project)
    checks.record("candidate_suite.exit", 0, code if code == 0 else f"{code}: {err.strip()[-160:]}")
    with tempfile.TemporaryDirectory(prefix="bugfix01-") as tmp:
        scratch = Path(tmp)
        (scratch / "greet.py").write_text(BUGFIX_SEED["greet.py"])
        if tests.is_file():
            shutil.copy2(tests, scratch / "test_greet.py")
        code, _, _ = _run([sys.executable, "-m", "unittest", "-q", "test_greet"], cwd=scratch)
        checks.record("regression.fails_on_seed", True, tests.is_file() and code != 0)
    readme = project / "README.md"
    checks.record("readme.unchanged", True, readme.is_file() and readme.read_text() == BUGFIX_SEED["README.md"])
    checks.record("no_extra_files", [], sorted(workspace_files(project) - set(BUGFIX_DELIVERABLES)))
    return checks.result()


# --- FEATURE-01: case-insensitive tag filter in a seeded notes CLI ----------

FEATURE_NOTES = {"notes": [
    {"id": 1, "text": "Renew passport", "tags": ["Admin", "urgent"]},
    {"id": 2, "text": "Draft quarterly report", "tags": ["Work"]},
    {"id": 3, "text": "Book dentist", "tags": ["health"]},
    {"id": 4, "text": "Prepare sprint review", "tags": ["WORK", "Urgent"]},
]}

FEATURE_SEED = {
    "notes.py": '''\
"""Notes CLI backed by notes.json in the current directory."""
import json
import sys
from pathlib import Path

STORE = Path("notes.json")
USAGE = "usage: notes.py add TEXT [--tag TAG ...] | notes.py list"


def load():
    if not STORE.exists():
        return {"notes": []}
    return json.loads(STORE.read_text())


def save(data):
    STORE.write_text(json.dumps(data, indent=2) + "\\n")


def render(note):
    return f"{note['id']}\\t{note['text']}\\t{','.join(note['tags'])}"


def usage():
    print(USAGE, file=sys.stderr)
    return 2


def main(argv=None):
    argv = sys.argv[1:] if argv is None else list(argv)
    if not argv:
        return usage()
    command, rest = argv[0], argv[1:]
    if command == "add":
        if not rest or rest[0].startswith("--") or not rest[0].strip():
            return usage()
        text, tags, index = rest[0], [], 1
        while index < len(rest):
            if rest[index] == "--tag" and index + 1 < len(rest) and rest[index + 1].strip():
                tags.append(rest[index + 1])
                index += 2
            else:
                return usage()
        data = load()
        note_id = max((note["id"] for note in data["notes"]), default=0) + 1
        data["notes"].append({"id": note_id, "text": text, "tags": tags})
        save(data)
        print(note_id)
        return 0
    if command == "list":
        if rest:
            return usage()
        for note in load()["notes"]:
            print(render(note))
        return 0
    return usage()


if __name__ == "__main__":
    raise SystemExit(main())
''',
    "notes.json": json.dumps(FEATURE_NOTES, indent=2) + "\n",
    "test_notes.py": '''\
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent


def run(cwd, *args):
    return subprocess.run([sys.executable, str(HERE / "notes.py"), *args],
                          cwd=cwd, capture_output=True, text=True)


class TestNotes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.cwd = self.temp.name

    def test_add_then_list(self):
        self.assertEqual(run(self.cwd, "add", "Buy milk", "--tag", "home").returncode, 0)
        listed = run(self.cwd, "list")
        self.assertEqual(listed.returncode, 0)
        self.assertEqual(listed.stdout, "1\\tBuy milk\\thome\\n")
        saved = json.loads(Path(self.cwd, "notes.json").read_text())
        self.assertEqual(saved["notes"][0]["tags"], ["home"])

    def test_list_empty_store(self):
        listed = run(self.cwd, "list")
        self.assertEqual((listed.returncode, listed.stdout), (0, ""))

    def test_unknown_command(self):
        self.assertEqual(run(self.cwd, "frobnicate").returncode, 2)


if __name__ == "__main__":
    unittest.main()
''',
    "README.md": "# Notes CLI\n\n`notes.py add TEXT [--tag TAG ...]` stores a note in `notes.json`; "
                 "`notes.py list` prints `ID<TAB>TEXT<TAB>tag1,tag2` per note.\n",
}
FEATURE_SEED_TESTS = ("test_add_then_list", "test_list_empty_store", "test_unknown_command")
FEATURE_DELIVERABLES = ("notes.py", "test_notes.py", "README.md", "notes.json")


def _render_note(note: dict) -> str:
    return f"{note['id']}\t{note['text']}\t{','.join(note['tags'])}"


def feature01_oracle(project: Path) -> OracleResult:
    """Filter must be case-insensitive, honest on no match, strict on empty tag.

    Every case runs against a scratch copy seeded with the original store so
    the check never depends on what the Builder left in notes.json.
    """
    checks = Checks("FEATURE-01")
    cli = project / "notes.py"
    if not cli.is_file():
        checks.record("notes.py.present", True, False)
        return checks.result()
    seed_store = FEATURE_SEED["notes.json"]
    with tempfile.TemporaryDirectory(prefix="feature01-") as tmp:
        scratch = Path(tmp)
        shutil.copy2(cli, scratch / "notes.py")
        store = scratch / "notes.json"
        store.write_text(seed_store)

        def notes(*args):
            return _run([sys.executable, "notes.py", *args], cwd=scratch, timeout=30)

        expected_all = "".join(_render_note(n) + "\n" for n in FEATURE_NOTES["notes"])
        code, out, _ = notes("list")
        checks.record("old_list.unchanged", (0, expected_all), (code, out))
        by_id = {n["id"]: n for n in FEATURE_NOTES["notes"]}
        for tag, ids in (("work", [2, 4]), ("WORK", [2, 4]), ("Urgent", [1, 4]), ("health", [3])):
            code, out, _ = notes("list", "--tag", tag)
            want = "".join(_render_note(by_id[i]) + "\n" for i in ids)
            checks.record(f"filter[{tag}]", (0, want), (code, out))
        code, out, _ = notes("list", "--tag", "nothing-here")
        checks.record("filter.no_match", (0, ""), (code, out))
        code, out, err = notes("list", "--tag", "")
        checks.record("filter.empty_tag_rejected", True, code == 2 and out == "" and "usage" in err.lower())
        code, out, err = notes("list", "--tag")
        checks.record("filter.missing_value_rejected", True, code == 2 and out == "")
        checks.record("store.unchanged_after_reads", True, store.read_text() == seed_store)
        code, out, _ = notes("add", "Water plants", "--tag", "home")
        checks.record("old_add.works", (0, "5\n"), (code, out))
        code, out, _ = notes("list", "--tag", "HOME")
        checks.record("filter.sees_new_note", (0, "5\tWater plants\thome\n"), (code, out))
    tests = project / "test_notes.py"
    text = tests.read_text(encoding="utf-8", errors="replace") if tests.is_file() else ""
    for name in FEATURE_SEED_TESTS:
        checks.record(f"seed_test_kept.{name}", True, f"def {name}(" in text)
    code, _, err = _run([sys.executable, "-m", "unittest", "-q", "test_notes"], cwd=project)
    checks.record("candidate_suite.exit", 0, code if code == 0 else f"{code}: {err.strip()[-160:]}")
    checks.record("no_extra_files", [], sorted(workspace_files(project) - set(FEATURE_DELIVERABLES)))
    return checks.result()


# --- ARCH-01: service decomposition with enforced code boundaries ----------

ARCH_COMPONENTS = ("catalog", "cart", "checkout", "notifications")
ARCH_REQUIRED_EDGES = {("checkout", "catalog"), ("checkout", "cart"), ("notifications", "checkout")}
ARCH_ROOTS = ("catalog", "cart")
ARCH_ADR = "docs/adr/0001-service-decomposition.md"
ARCH_ADR_SECTIONS = ("## Context", "## Options considered", "## Decision", "## Consequences")
_IMPORT_RE = re.compile(r"^\s*(?:from\s+services\.([A-Za-z_][\w]*)|import\s+services\.([A-Za-z_][\w]*)|"
                        r"from\s+services\s+import\s+([A-Za-z_][\w]*))", re.MULTILINE)


def component_imports(package: Path) -> set[str]:
    """Names under ``services.`` imported anywhere inside one component package."""
    found = set()
    for path in package.rglob("*.py"):
        for match in _IMPORT_RE.finditer(path.read_text(encoding="utf-8", errors="replace")):
            found.add(next(group for group in match.groups() if group))
    return found


def _acyclic(edges: dict[str, list[str]]) -> bool:
    visiting, done = set(), set()

    def visit(node):
        if node in visiting:
            return False
        if node in done:
            return True
        visiting.add(node)
        if not all(visit(dep) for dep in edges.get(node, [])):
            return False
        visiting.remove(node)
        done.add(node)
        return True

    return all(visit(node) for node in edges)


def _disjoint(paths: dict[str, list[str]]) -> list[str]:
    overlaps = []
    items = [(cid, Path(p).as_posix().rstrip("/")) for cid, rows in paths.items() for p in rows]
    for index, (left_id, left) in enumerate(items):
        for right_id, right in items[index + 1:]:
            if left_id != right_id and (left == right or left.startswith(right + "/") or right.startswith(left + "/")):
                overlaps.append(f"{left_id}:{left} vs {right_id}:{right}")
    return overlaps


def arch01_oracle(project: Path) -> OracleResult:
    """Architecture is accepted only when the code obeys the declared boundaries."""
    checks = Checks("ARCH-01")
    adr = project / ARCH_ADR
    adr_text = adr.read_text(encoding="utf-8", errors="replace") if adr.is_file() else ""
    checks.record("adr.present", True, adr.is_file())
    for section in ARCH_ADR_SECTIONS:
        checks.record(f"adr.section[{section[3:]}]", True, section in adr_text)
    spec_path = project / "architecture" / "components.json"
    try:
        spec = json.loads(spec_path.read_text())
        rows = spec["components"]
        ids = [row["id"] for row in rows]
    except (OSError, ValueError, KeyError, TypeError) as error:
        checks.record("components.json.valid", True, f"invalid: {error}")
        return checks.result()
    checks.record("components.ids", sorted(ARCH_COMPONENTS), sorted(ids))
    edges = {row["id"]: list(row.get("depends_on", [])) for row in rows}
    checks.record("components.acyclic", True, _acyclic(edges))
    present = {(cid, dep) for cid, deps in edges.items() for dep in deps}
    checks.record("components.required_edges", sorted(ARCH_REQUIRED_EDGES), sorted(ARCH_REQUIRED_EDGES & present))
    checks.record("components.roots_have_no_deps", [], [cid for cid in ARCH_ROOTS if edges.get(cid)])
    owns = {row["id"]: list(row.get("owns", [])) for row in rows}
    checks.record("ownership.disjoint", [], _disjoint(owns))
    for cid in ids:
        owned = [Path(p).as_posix().rstrip("/") for p in owns.get(cid, [])]
        package = f"services/{cid}"
        checks.record(f"ownership[{cid}].covers_package", True,
                      any(package == p or package.startswith(p + "/") for p in owned))
        checks.record(f"ownership[{cid}].paths_exist", [], [p for p in owned if not (project / p).exists()])
    contracts = {}
    for row in rows:
        cid = row["id"]
        contract_path = project / row.get("contract", f"contracts/{cid}.json")
        try:
            contract = json.loads(contract_path.read_text())
            names = [op["name"] for op in contract["operations"]]
        except (OSError, ValueError, KeyError, TypeError) as error:
            checks.record(f"contract[{cid}].valid", True, f"invalid: {error}")
            names = []
        contracts[cid] = names
        checks.record(f"contract[{cid}].has_operations", True, bool(names))
        probe = ("import importlib, json, sys; m = importlib.import_module('services.%s.api'); "
                 "print(json.dumps([n for n in %s if callable(getattr(m, n, None))]))" % (cid, json.dumps(names)))
        code, out, err = _run([sys.executable, "-c", probe], cwd=project, timeout=30)
        implemented = json.loads(out) if code == 0 and out.strip() else f"import failed: {err.strip()[-120:]}"
        checks.record(f"api[{cid}].implements_contract", names, implemented)
        allowed = set(edges.get(cid, []))
        stray = sorted(component_imports(project / "services" / cid) - allowed - {cid})
        checks.record(f"boundary[{cid}].imports_only_declared_deps", [], stray)
    check_script = project / "architecture" / "check.py"
    checks.record("check.py.present", True, check_script.is_file())
    if check_script.is_file():
        code, _, err = _run([sys.executable, "architecture/check.py"], cwd=project, timeout=60)
        checks.record("check.py.passes_on_candidate", 0, code if code == 0 else f"{code}: {err.strip()[-160:]}")
        with tempfile.TemporaryDirectory(prefix="arch01-") as tmp:
            scratch = Path(tmp) / "copy"
            shutil.copytree(project, scratch, ignore=shutil.ignore_patterns(*IGNORED_DIRS))
            api = scratch / "services" / "catalog" / "api.py"
            if api.is_file():
                api.write_text("import services.notifications.api\n" + api.read_text())
            code, _, _ = _run([sys.executable, "architecture/check.py"], cwd=scratch, timeout=60)
            checks.record("check.py.rejects_injected_violation", True, code != 0)
    return checks.result()


# --- PROGRAM-01: four-service order system behind a gateway ---------------

PROGRAM_SERVICES = ("catalog", "cart", "checkout", "gateway")
PROGRAM_STATIC_FILES = ("contracts/README.md", "deploy/docker-compose.yml", "deploy/README.md",
                        "scripts/run_local.py", "tests/test_e2e.py")


def compose_services(text: str) -> list[str]:
    """Top-level keys under ``services:`` in a compose file (no YAML dependency)."""
    names, inside, indent = [], False, None
    for line in text.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if re.match(r"^services:\s*$", line):
            inside, indent = True, None
            continue
        if inside:
            current = len(line) - len(line.lstrip(" "))
            if current == 0:
                inside = False
                continue
            if indent is None:
                indent = current
            match = re.match(r"^\s*([A-Za-z0-9_.-]+):\s*$", line)
            if match and current == indent:
                names.append(match.group(1))
    return names


class _Services:
    """Own the four service processes for one oracle run."""

    def __init__(self, project: Path):
        self.project = project
        self.ports = {name: _free_port() for name in PROGRAM_SERVICES}
        self.procs: dict[str, subprocess.Popen] = {}
        self.logs: dict[str, Path] = {}

    def url(self, name: str) -> str:
        return f"http://127.0.0.1:{self.ports[name]}"

    def start(self, tmp: Path) -> dict[str, bool]:
        commands = {
            "catalog": [sys.executable, "services/catalog/server.py", "--port", str(self.ports["catalog"])],
            "cart": [sys.executable, "services/cart/server.py", "--port", str(self.ports["cart"])],
            "checkout": [sys.executable, "services/checkout/server.py", "--port", str(self.ports["checkout"]),
                         "--catalog-url", self.url("catalog"), "--cart-url", self.url("cart")],
            "gateway": [sys.executable, "gateway/server.py", "--port", str(self.ports["gateway"]),
                        "--catalog-url", self.url("catalog"), "--cart-url", self.url("cart"),
                        "--checkout-url", self.url("checkout")],
        }
        healthy = {}
        for name, command in commands.items():
            if not (self.project / command[1]).is_file():
                healthy[name] = False
                continue
            log = tmp / f"{name}.log"
            self.logs[name] = log
            with log.open("w") as handle:
                self.procs[name] = subprocess.Popen(command, cwd=self.project, stdout=handle,
                                                    stderr=subprocess.STDOUT, start_new_session=True)
            healthy[name] = _wait_http(self.url(name) + "/health", time.monotonic() + 20)
        return healthy

    def stop(self):
        for proc in self.procs.values():
            if proc.poll() is None:
                proc.terminate()
        deadline = time.monotonic() + 5
        for proc in self.procs.values():
            while proc.poll() is None and time.monotonic() < deadline:
                time.sleep(0.05)
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)


def program01_oracle(project: Path) -> OracleResult:
    """Drive the full order journey through the gateway against live processes.

    Deployment descriptors are checked statically and never executed; the
    summary says so rather than implying a deployment happened.
    """
    checks = Checks("PROGRAM-01")
    for rel in PROGRAM_STATIC_FILES:
        checks.record(f"file[{rel}]", True, (project / rel).is_file())
    compose = project / "deploy" / "docker-compose.yml"
    listed = compose_services(compose.read_text(encoding="utf-8", errors="replace")) if compose.is_file() else []
    checks.record("compose.services", sorted(PROGRAM_SERVICES), sorted(set(listed) & set(PROGRAM_SERVICES)))
    services = _Services(project)
    with tempfile.TemporaryDirectory(prefix="program01-") as tmp:
        try:
            healthy = services.start(Path(tmp))
            for name in PROGRAM_SERVICES:
                checks.record(f"health[{name}]", True, healthy.get(name, False))
            if not all(healthy.values()):
                return checks.result(note="deployment descriptors checked statically, never executed")
            gateway = services.url("gateway")
            status, items = _http("GET", gateway + "/catalog")
            ok_items = (status == 200 and isinstance(items, list) and len(items) >= 3
                        and all(isinstance(i, dict) and {"sku", "name", "price_cents"} <= set(i)
                                and isinstance(i["price_cents"], int) for i in items))
            checks.record("catalog.list", True, ok_items)
            if not ok_items:
                return checks.result(note="deployment descriptors checked statically, never executed")
            first, second = items[0], items[1]
            status, cart = _http("POST", f"{gateway}/cart/c1/items",
                                 {"sku": first["sku"], "quantity": 2, "price_cents": first["price_cents"]})
            checks.record("cart.add_first", 200, status)
            status, cart = _http("POST", f"{gateway}/cart/c1/items",
                                 {"sku": second["sku"], "quantity": 1, "price_cents": second["price_cents"]})
            checks.record("cart.add_second", 200, status)
            status, cart = _http("GET", f"{gateway}/cart/c1")
            lines = cart.get("items", []) if isinstance(cart, dict) else []
            checks.record("cart.get", {"status": 200, "items": 2}, {"status": status, "items": len(lines)})
            status, order = _http("POST", f"{gateway}/checkout", {"cartId": "c1"})
            expected_total = 2 * first["price_cents"] + 1 * second["price_cents"]
            checks.record("checkout.created", 201, status)
            order_id = order.get("orderId") if isinstance(order, dict) else None
            checks.record("checkout.order_id", True, bool(order_id))
            checks.record("checkout.total", expected_total, order.get("total_cents") if isinstance(order, dict) else order)
            status, cart = _http("GET", f"{gateway}/cart/c1")
            checks.record("cart.cleared_after_checkout", {"status": 200, "items": 0},
                          {"status": status, "items": len(cart.get("items", [])) if isinstance(cart, dict) else cart})
            status, body = _http("POST", f"{gateway}/checkout", {"cartId": "c1"})
            checks.record("checkout.empty_cart_rejected", 409, status)
            status, fetched = _http("GET", f"{gateway}/orders/{order_id}")
            checks.record("orders.get", {"status": 200, "total_cents": expected_total},
                          {"status": status, "total_cents": fetched.get("total_cents") if isinstance(fetched, dict) else fetched})
            status, _ = _http("GET", f"{gateway}/orders/does-not-exist")
            checks.record("orders.unknown", 404, status)
        finally:
            services.stop()
    return checks.result(note="deployment descriptors checked statically, never executed")


# --- UI-01: implement a frozen design reference (Figma stand-in) -----------

UI_SPEC = {
    "screen": "Task monitor",
    "title": "Local task monitor",
    "copy": {
        "heading": "Local task monitor",
        "status_label": "Status",
        "pause": "Pause", "resume": "Resume", "cancel": "Cancel",
        "cancelled_note": "This task was cancelled. Start a new task to continue.",
    },
    "elements": [
        {"id": "heading", "kind": "heading", "text": "Local task monitor"},
        {"id": "status", "kind": "status", "role": "status", "initial": "RUNNING"},
        {"id": "pause", "kind": "button", "label": "Pause"},
        {"id": "resume", "kind": "button", "label": "Resume"},
        {"id": "cancel", "kind": "button", "label": "Cancel"},
        {"id": "note", "kind": "text", "text": "This task was cancelled. Start a new task to continue.",
         "visible_only_in": ["CANCELLED"]},
    ],
    "states": {
        "RUNNING": {"enabled": ["pause", "cancel"], "disabled": ["resume"]},
        "PAUSED": {"enabled": ["resume", "cancel"], "disabled": ["pause"]},
        "CANCELLED": {"enabled": [], "disabled": ["pause", "resume", "cancel"]},
    },
    "transitions": [
        {"from": "RUNNING", "click": "pause", "to": "PAUSED"},
        {"from": "PAUSED", "click": "resume", "to": "RUNNING"},
        {"from": "RUNNING", "click": "cancel", "to": "CANCELLED"},
    ],
    "tokens": {
        "color.background": "#0f172a", "color.surface": "#1e293b", "color.text": "#f8fafc",
        "color.accent": "#38bdf8", "color.danger": "#f87171",
        "font.family": "system-ui, sans-serif", "space.unit": 8,
    },
    "layout": {
        "desktop": {"min_width": 1024, "buttons": "row"},
        "mobile": {"max_width": 480, "buttons": "stack"},
        "min_touch_target_px": 44,
    },
    "accessibility": [
        "Every button shows its visible text label",
        "The status element uses role=status so changes are announced",
        "DOM order is pause, resume, cancel",
        "Minimum 44px touch targets on mobile",
    ],
}
UI_SEED = {"design/spec.json": json.dumps(UI_SPEC, indent=2) + "\n"}
UI_BUTTONS = ("pause", "resume", "cancel")
UI_DELIVERABLES = ("web/index.html", "web/test_static.py", "design/spec.json")


class _Html(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids: dict[str, dict] = {}
        self.order: list[str] = []
        self.external: list[str] = []
        self.viewport = False
        self.title_parts: list[str] = []
        self.texts: dict[str, list[str]] = {}
        self._open: list[str | None] = []
        self._in_title = False

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        element_id = attrs.get("id")
        if element_id:
            self.ids[element_id] = {"tag": tag, **attrs}
            self.order.append(element_id)
            self.texts.setdefault(element_id, [])
        if tag == "meta" and attrs.get("name") == "viewport":
            self.viewport = True
        for key in ("src", "href"):
            value = attrs.get(key) or ""
            if re.match(r"^(https?:)?//", value):
                self.external.append(value)
        if tag == "title":
            self._in_title = True
        if tag not in ("meta", "link", "br", "img", "input", "hr"):
            self._open.append(element_id)

    def handle_endtag(self, tag):
        if tag == "title":
            self._in_title = False
        if tag not in ("meta", "link", "br", "img", "input", "hr") and self._open:
            self._open.pop()

    def handle_data(self, data):
        if self._in_title:
            self.title_parts.append(data)
        for element_id in self._open:
            if element_id:
                self.texts[element_id].append(data)


def _chromium(playwright):
    """Launch Chromium from an explicit path when the runtime's own download is absent."""
    candidates = [os.environ.get("AUTOCODE_CHROMIUM"), os.environ.get("PLAYWRIGHT_CHROMIUM_EXECUTABLE"),
                  "/opt/pw-browsers/chromium", None]
    errors = []
    for path in candidates:
        if path and not Path(path).exists():
            continue
        try:
            return playwright.chromium.launch(executable_path=path) if path else playwright.chromium.launch()
        except Exception as error:  # noqa: BLE001 - report every launch failure
            errors.append(str(error).splitlines()[0][:120])
    raise RuntimeError("; ".join(errors) or "no chromium")


def ui01_static(project: Path, checks: Checks) -> bool:
    page = project / "web" / "index.html"
    if not page.is_file():
        checks.record("index.html.present", True, False)
        return False
    text = page.read_text(encoding="utf-8", errors="replace")
    parser = _Html()
    parser.feed(text)
    checks.record("title", UI_SPEC["title"], "".join(parser.title_parts).strip())
    checks.record("viewport_meta", True, parser.viewport)
    checks.record("no_external_resources", [], parser.external)
    for element in UI_SPEC["elements"]:
        checks.record(f"element[{element['id']}].present", True, element["id"] in parser.ids)
    status = parser.ids.get("status", {})
    checks.record("status.role", "status", status.get("role"))
    for name in UI_BUTTONS:
        node = parser.ids.get(name, {})
        checks.record(f"button[{name}].is_button", "button", node.get("tag"))
        checks.record(f"button[{name}].label", UI_SPEC["copy"][name],
                      "".join(parser.texts.get(name, [])).strip())
    positions = [parser.order.index(name) if name in parser.order else -1 for name in UI_BUTTONS]
    checks.record("buttons.dom_order", True, positions == sorted(positions) and -1 not in positions)
    for token in ("color.background", "color.accent", "color.danger"):
        checks.record(f"token[{token}]", True, UI_SPEC["tokens"][token].lower() in text.lower())
    return True


def ui01_dynamic(project: Path, checks: Checks) -> str:
    """Interaction and layout checks in a real browser. Returns a deferral reason or ''."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        return "playwright not importable; interaction and layout checks not run"
    url = (project / "web" / "index.html").resolve().as_uri()
    try:
        with sync_playwright() as playwright:
            try:
                browser = _chromium(playwright)
            except RuntimeError as error:
                return f"chromium unavailable ({error}); interaction and layout checks not run"
            try:
                page = browser.new_page(viewport={"width": 1280, "height": 800})
                page.goto(url)
                page.wait_for_load_state("load")

                def state_ok(name):
                    spec = UI_SPEC["states"][name]
                    observed = {button: page.locator(f"#{button}").is_disabled() for button in UI_BUTTONS}
                    expected = {button: button in spec["disabled"] for button in UI_BUTTONS}
                    checks.record(f"state[{name}].disabled_map", expected, observed)
                    checks.record(f"state[{name}].status_text", name, page.inner_text("#status").strip())
                    checks.record(f"state[{name}].note_visible", name == "CANCELLED", page.locator("#note").is_visible())

                def press(name):
                    # A disabled target is a wrong state, not a browser problem: record it and move on.
                    enabled = page.locator(f"#{name}").is_enabled()
                    checks.record(f"transition[{name}].target_enabled", True, enabled)
                    if enabled:
                        page.click(f"#{name}", timeout=5000)

                state_ok("RUNNING")
                press("pause")
                state_ok("PAUSED")
                press("resume")
                state_ok("RUNNING")
                press("cancel")
                state_ok("CANCELLED")
                checks.record("note.copy", UI_SPEC["copy"]["cancelled_note"], page.inner_text("#note").strip())
                boxes = {name: page.locator(f"#{name}").bounding_box() for name in UI_BUTTONS}
                rows = {round(box["y"]) for box in boxes.values() if box}
                checks.record("layout.desktop.buttons_in_one_row", 1, len(rows))
                page.set_viewport_size({"width": 375, "height": 700})
                page.reload()
                page.wait_for_load_state("load")
                boxes = {name: page.locator(f"#{name}").bounding_box() for name in UI_BUTTONS}
                rows = {round(box["y"]) for box in boxes.values() if box}
                checks.record("layout.mobile.buttons_stacked", len(UI_BUTTONS), len(rows))
                minimum = UI_SPEC["layout"]["min_touch_target_px"]
                small = [name for name, box in boxes.items()
                         if not box or box["height"] < minimum or box["width"] < minimum]
                checks.record("layout.mobile.touch_targets", [], small)
                checks.record("layout.mobile.no_horizontal_scroll", True,
                              page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"))
            finally:
                browser.close()
    except Exception as error:  # noqa: BLE001 - a browser crash is an oracle error, not a product fact
        return f"browser session failed ({str(error).splitlines()[0][:120]})"
    return ""


def ui01_oracle(project: Path) -> OracleResult:
    """Structure is checked from the file; behavior and layout in Chromium.

    Without a browser the result is DEFERRED: static structure alone cannot
    establish that the design was implemented.
    """
    checks = Checks("UI-01")
    if not ui01_static(project, checks):
        return checks.result()
    tests = project / "web" / "test_static.py"
    checks.record("test_static.present", True, tests.is_file())
    if tests.is_file():
        code, _, err = _run([sys.executable, "web/test_static.py"], cwd=project, timeout=60)
        checks.record("test_static.exit", 0, code if code == 0 else f"{code}: {err.strip()[-160:]}")
    deferred = ui01_dynamic(project, checks)
    return checks.result(deferred=deferred)


# --- briefs ---------------------------------------------------------------

BUGFIX_TASK = (
    "Bug report for the committed greeting CLI in this repository: `python3 greet.py \"\"` and "
    "`python3 greet.py \"   \"` print a greeting and exit 0. A blank or whitespace-only NAME must "
    "print the usage line to stderr and exit 2, exactly like the missing-argument case. Fix the "
    "defect in greet.py and add a regression test to test_greet.py that fails on the current code "
    "and passes after the fix. Keep every existing test, do not change valid-input behavior "
    "(`greet.py Ada` still prints `Hello, Ada` and exits 0), do not modify README.md, and do not "
    "add files. Python standard library only."
)

FEATURE_TASK = (
    "Add tag filtering to the committed notes CLI in this repository. `notes.py list --tag TAG` "
    "prints only the notes that carry TAG, compared case-insensitively (`--tag work` matches "
    "notes tagged `Work` or `WORK`), in the same `ID<TAB>TEXT<TAB>tag1,tag2` format and id order "
    "as plain `list`. No matching note prints nothing and exits 0. An empty tag (`--tag \"\"`) or a "
    "missing value (`list --tag`) prints the usage line to stderr and exits 2. Plain `list`, the "
    "`add` command, the stored file format, and existing stored data must not change; listing "
    "never rewrites notes.json. Keep every existing test and add tests for the new behavior in "
    "test_notes.py; update README.md's command summary. Do not add files. Python standard "
    "library only."
)

ARCH_TASK = (
    "Design the architecture for a small order-processing system with four components: catalog, "
    "cart, checkout, notifications. Deliver: (1) docs/adr/0001-service-decomposition.md with the "
    "sections `## Context`, `## Options considered`, `## Decision`, `## Consequences`; (2) "
    "architecture/components.json shaped {\"components\": [{\"id\", \"owns\": [repository-relative "
    "directories], \"depends_on\": [component ids], \"contract\": \"contracts/<id>.json\"}]}; the graph "
    "must be acyclic, checkout depends on catalog and cart, notifications depends on checkout, and "
    "catalog and cart depend on nothing; each component's `owns` must include its own package "
    "services/<id>/ and ownership must not overlap; (3) one contracts/<id>.json per component "
    "shaped {\"component\": id, \"operations\": [{\"name\", \"description\", \"input\": {...}, "
    "\"output\": {...}}]} with at least one operation each; (4) a Python package services/<id>/ per "
    "component whose services/<id>/api.py defines a plain function for every operation name in its "
    "contract (in-memory data, no servers, no persistence); (5) the dependency rule that a "
    "component package may import `services.<other>` only when <other> is listed in its "
    "depends_on; (6) architecture/check.py which verifies rules 2-5 against the repository and "
    "exits nonzero on any violation, plus a short docs/architecture.md explaining how to run it. "
    "Python standard library only."
)

PROGRAM_TASK = (
    "Build a locally runnable order system made of four independent HTTP services plus shared "
    "contracts and deployment descriptors. Python standard library only; every service is a "
    "separate process started as `python3 <path> --port N` and answers GET /health with 200 "
    "{\"status\": \"ok\"}. Shared contracts: contracts/README.md describing the JSON shapes Item "
    "{sku, name, price_cents}, CartItem {sku, quantity, price_cents}, Cart {cartId, items}, Order "
    "{orderId, cartId, total_cents}, plus one contracts/<shape>.json per shape. Services: "
    "services/catalog/server.py: GET /items -> 200 JSON list of at least three items; GET "
    "/items/{sku} -> 200 item or 404. services/cart/server.py: POST /carts/{cartId}/items with a "
    "CartItem body -> 200 Cart; GET /carts/{cartId} -> 200 Cart (empty items for an unknown id); "
    "DELETE /carts/{cartId} -> 204. services/checkout/server.py --catalog-url --cart-url: POST "
    "/checkout {cartId} -> 201 Order whose total_cents is the sum of quantity*price_cents over the "
    "cart's items fetched from the cart service, then clears that cart with DELETE; an empty cart "
    "-> 409 {\"error\": \"empty_cart\"}; GET /orders/{orderId} -> 200 Order or 404. "
    "gateway/server.py --catalog-url --cart-url --checkout-url proxies GET /catalog -> catalog "
    "/items, POST /cart/{cartId}/items and GET /cart/{cartId} -> the cart service, POST /checkout "
    "and GET /orders/{orderId} -> the checkout service, preserving status codes and bodies. "
    "Deployment descriptors: deploy/docker-compose.yml defining the services catalog, cart, "
    "checkout and gateway (never executed in this task) and deploy/README.md stating that no "
    "deployment was performed. scripts/run_local.py --catalog-port --cart-port --checkout-port "
    "--gateway-port starts all four processes wired together and stops them on SIGTERM/Ctrl-C. "
    "tests/test_e2e.py starts the services on free ports and checks: catalog lists items, adding "
    "two items to a cart, checkout total, the cart is empty after checkout, and an empty-cart "
    "checkout returns 409. Plan this as independent milestones with disjoint ownership: contracts "
    "first; catalog and cart in parallel; then checkout; then gateway; then tests and deploy."
)

UI_TASK = (
    "Implement the frozen design reference design/spec.json as a single self-contained "
    "web/index.html: inline CSS and JavaScript, no external resources, no build step, no "
    "framework. Every element id, visible copy, state (enabled/disabled buttons), transition "
    "(pause -> PAUSED, resume -> RUNNING, cancel -> CANCELLED with the cancelled note shown only "
    "then), color and font tokens, and layout rule (buttons in one row at or above 1024px, "
    "stacked full-width with at least 44px touch targets at or below 480px, never horizontal "
    "scrolling) must match the spec. The status element uses role=status. Add "
    "web/test_static.py, a Python standard-library check that parses index.html and verifies the "
    "ids, labels, role and viewport meta; it must exit nonzero on a mismatch. Do not modify "
    "design/spec.json or add other files."
)

PROGRAM_MANIFEST = {
    "version": 1,
    "name": "order system",
    "brief": PROGRAM_TASK,
    "shared": {
        "constraints": ["Python standard library only", "Every service is a separate process with GET /health",
                        "No deployment is executed"],
        "end_to_end_flow": ["List the catalog through the gateway", "Add two items to a cart",
                            "Check out and receive an order total", "The cart is empty afterwards",
                            "An empty-cart checkout is rejected with 409"],
        "interfaces": [{"id": "contracts", "summary": "JSON shapes Item, CartItem, Cart, Order", "paths": ["contracts/"]}],
    },
    "workstreams": [
        {"id": "contracts", "kind": "code", "owns": ["contracts/"], "depends_on": [],
         "brief": "Write contracts/README.md and one contracts/<shape>.json per shape: Item {sku, name, "
                  "price_cents}, CartItem {sku, quantity, price_cents}, Cart {cartId, items}, Order {orderId, "
                  "cartId, total_cents}. Documentation only; no code."},
        {"id": "catalog", "kind": "code", "owns": ["services/catalog/"], "depends_on": ["contracts"],
         "brief": "services/catalog/server.py --port N: GET /health -> 200 {status: ok}; GET /items -> 200 list of "
                  "at least three Item objects; GET /items/{sku} -> 200 Item or 404. Include unit tests in the package."},
        {"id": "cart", "kind": "code", "owns": ["services/cart/"], "depends_on": ["contracts"],
         "brief": "services/cart/server.py --port N: GET /health; POST /carts/{cartId}/items CartItem -> 200 Cart; "
                  "GET /carts/{cartId} -> 200 Cart (empty items for unknown ids); DELETE /carts/{cartId} -> 204. "
                  "In-memory storage. Include unit tests in the package."},
        {"id": "checkout", "kind": "code", "owns": ["services/checkout/"], "depends_on": ["catalog", "cart"],
         "brief": "services/checkout/server.py --port N --catalog-url --cart-url: GET /health; POST /checkout "
                  "{cartId} -> 201 Order with total_cents = sum(quantity*price_cents) over the cart fetched from "
                  "the cart service, then DELETE that cart; empty cart -> 409 {error: empty_cart}; GET "
                  "/orders/{orderId} -> 200 Order or 404. Include unit tests using a stub cart server."},
        {"id": "gateway", "kind": "code", "owns": ["gateway/"], "depends_on": ["checkout"],
         "brief": "gateway/server.py --port N --catalog-url --cart-url --checkout-url: GET /health; proxy GET "
                  "/catalog, POST /cart/{cartId}/items, GET /cart/{cartId}, POST /checkout, GET /orders/{orderId} "
                  "to the owning service, preserving status codes and JSON bodies."},
        {"id": "integration", "kind": "integration", "owns": ["tests/", "scripts/"], "depends_on": ["gateway"],
         "brief": "scripts/run_local.py --catalog-port --cart-port --checkout-port --gateway-port starts all four "
                  "services wired together and stops them on SIGTERM. tests/test_e2e.py starts the services on "
                  "free ports and verifies the full journey through the gateway: catalog listing, two cart "
                  "additions, checkout total, cart empty afterwards, empty-cart checkout 409. Fix integration "
                  "defects in any service only when the journey fails."},
        {"id": "deploy", "kind": "deployment", "owns": ["deploy/"], "depends_on": ["integration"],
         "brief": "deploy/docker-compose.yml defining services catalog, cart, checkout and gateway, and "
                  "deploy/README.md stating that no deployment was performed. Do not run docker or reach any "
                  "external system."},
    ],
}

SCENARIOS = {
    "BUGFIX-01": {
        "title": "Bug fix: blank-name validation in a committed CLI",
        "task_type": "bugfix",
        "task": BUGFIX_TASK,
        "seed": BUGFIX_SEED,
        "oracle": bugfix01_oracle,
        "oracle_name": "BUGFIX-01",
        "expected_class": "pass_or_honest",
        "deliverables": list(BUGFIX_DELIVERABLES),
        "baseline": {"status": "NOT_RUN", "note": "oracle proven against reference and broken variants only"},
    },
    "FEATURE-01": {
        "title": "Feature: case-insensitive tag filter in a seeded notes CLI",
        "task_type": "feature",
        "task": FEATURE_TASK,
        "seed": FEATURE_SEED,
        "oracle": feature01_oracle,
        "oracle_name": "FEATURE-01",
        "expected_class": "pass_or_honest",
        "deliverables": list(FEATURE_DELIVERABLES),
        "baseline": {"status": "NOT_RUN", "note": "oracle proven against reference and broken variants only"},
    },
    "ARCH-01": {
        "title": "Architecture: service decomposition with enforced boundaries",
        "task_type": "architecture",
        "task": ARCH_TASK,
        "seed": {},
        "oracle": arch01_oracle,
        "oracle_name": "ARCH-01",
        "expected_class": "pass_or_honest",
        "deliverables": [ARCH_ADR, "architecture/components.json", "architecture/check.py",
                         "docs/architecture.md"] + [f"contracts/{c}.json" for c in ARCH_COMPONENTS]
                        + [f"services/{c}/api.py" for c in ARCH_COMPONENTS],
        "baseline": {"status": "NOT_RUN", "note": "oracle proven against reference and broken variants only"},
    },
    "PROGRAM-01": {
        "title": "Program: four-service order system behind a gateway",
        "task_type": "program",
        "task": PROGRAM_TASK,
        "seed": {},
        "oracle": program01_oracle,
        "oracle_name": "PROGRAM-01",
        "expected_class": "pass_or_honest",
        "deliverables": list(PROGRAM_STATIC_FILES) + ["services/catalog/server.py", "services/cart/server.py",
                                                      "services/checkout/server.py", "gateway/server.py"],
        # The same brief can run as one run with parallel milestone Builders or
        # as `autocode program run` with this manifest; the oracle is identical.
        "program_manifest": PROGRAM_MANIFEST,
        "baseline": {"status": "NOT_RUN", "note": "oracle proven against reference and broken variants only"},
    },
    "UI-01": {
        "title": "UI: implement a frozen design reference (Figma stand-in)",
        "task_type": "ui",
        "task": UI_TASK,
        "seed": UI_SEED,
        "oracle": ui01_oracle,
        "oracle_name": "UI-01",
        "expected_class": "pass_or_honest",
        "deliverables": list(UI_DELIVERABLES),
        "baseline": {"status": "NOT_RUN", "note": "oracle proven against reference and broken variants only; "
                                                  "the live Figma route (autocode ui --build) has no offline oracle"},
    },
}
