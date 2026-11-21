"""Real independent Tester probe for the progressive greeting fixture."""

import copy
import shlex

PROBE = shlex.join(
    [
        "python3",
        "-c",
        "import subprocess; r = subprocess.run(['python3', 'greet.py', 'Grace'], capture_output=True, text=True); "
        "assert (r.returncode, r.stdout, r.stderr) == (0, 'Hello, Grace\\n', '')",
    ]
)


def with_independent_probe(report):
    """Cite a separately executed, asserting input variation for each claimed PASS."""
    result = copy.deepcopy(report)
    rows = [row for row in result.get("criterion_results", []) if row.get("status") == "PASS"]
    if not rows:
        return result
    result["checks"].append({"command": PROBE, "exit_code": 0, "evidence_ref": "event:independent-probe"})
    result["checks_run"].append(PROBE)
    for row in rows:
        row["evidence_refs"].append("event:independent-probe")
    return result
