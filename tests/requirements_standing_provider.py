"""Offline provider for the Requirements standing editor regression.

The convention is model-reported. Its cited source is actually read in the
provider's workspace and emitted as a command event; it is not an approval.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


def main() -> int:
    prompt = sys.stdin.read()
    result = subprocess.run(
        [sys.executable, os.environ["STANDING_BASE_PROVIDER"], *sys.argv[1:]],
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        sys.stderr.write(result.stderr)
        return result.returncode
    if "CURRENT HANDOFF DATA\n" not in prompt or "--output-schema" not in sys.argv:
        sys.stdout.write(result.stdout)
        return 0
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    schema = json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
    if (
        data.get("stage") != "requirements_gather"
        or data.get("report_repair")
        or "ready" in schema.get("properties", {})
    ):
        sys.stdout.write(result.stdout)
        return 0
    target = Path(sys.argv[sys.argv.index("-o") + 1])
    report = json.loads(target.read_text())
    report["proposed_assumptions"] = [
        {
            "id": "A1",
            "text": "Keep the project's existing exception convention.",
            "kind": "inferable",
            "category": "technical",
            "convention_ref": "convention.py:1",
            "rationale": "The existing module uses ValueError.",
            "supports": [],
        }
    ]
    target.write_text(json.dumps(report))
    read = subprocess.run(["cat", "convention.py"], capture_output=True, text=True, check=True)
    event = {
        "type": "item.completed",
        "item": {
            "id": "standing-source-read",
            "type": "command_execution",
            "command": "cat convention.py",
            "exit_code": read.returncode,
            "aggregated_output": read.stdout,
        },
    }
    lines = result.stdout.splitlines(keepends=True)
    index = next(i for i, line in enumerate(lines) if json.loads(line).get("type") == "turn.completed")
    lines.insert(index, json.dumps(event) + "\n")
    sys.stdout.write("".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
