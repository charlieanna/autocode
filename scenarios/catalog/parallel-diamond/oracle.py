import json
import sys

from harness.oracle import Check, non_stdlib_imports, run

EDGES = {("A", "B"), ("A", "C"), ("B", "D"), ("C", "D")}
FILES = ("contract/schema.json", "dependency_trace.json", "server/handler.py", "client/fetch.py",
         "integration/check.py")
# Wrap handle() and fetch() before integration.check imports them, then call check().
PROBE = """
import json, server.handler as h, client.fetch as f
calls = []
real_handle, real_fetch = h.handle, f.fetch
h.handle = lambda node: (calls.append("handle"), real_handle(node))[1]
f.fetch = lambda node: (calls.append("fetch"), real_fetch(node))[1]
from integration.check import check
result = check()
print(json.dumps({"type": type(result).__name__, "calls": sorted(set(calls)),
                  "handle": type(real_handle("x")).__name__, "fetch": type(real_fetch("x")).__name__}))
"""


def load_json(path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def check(project, scenario):
    checks = [Check(f"file[{name}]", (project / name).is_file()) for name in FILES]
    trace = load_json(project / "dependency_trace.json") or {}
    edges = {tuple(edge) for edge in trace.get("edges", []) if isinstance(edge, list) and len(edge) == 2}
    checks.append(Check("trace_edges", edges == EDGES, f"got {sorted(edges)}"))
    schema = load_json(project / "contract" / "schema.json")
    checks.append(Check("schema_keys", isinstance(schema, dict) and {"node", "edges"} <= set(schema)))
    probe = run([sys.executable, "-c", PROBE], project, timeout=30)
    try:
        seen = json.loads(probe.stdout.strip().splitlines()[-1])
    except (ValueError, IndexError):
        seen = {}
    checks.append(Check("handle_and_fetch_return_strings", seen.get("handle") == seen.get("fetch") == "str",
                        probe.stderr[-300:]))
    checks.append(Check("check_combines_both", seen.get("type") == "str" and seen.get("calls") == ["fetch", "handle"],
                        str(seen) or probe.stderr[-300:]))
    foreign = non_stdlib_imports(project)
    checks.append(Check("stdlib_only", not foreign, "; ".join(foreign)))
    return checks
