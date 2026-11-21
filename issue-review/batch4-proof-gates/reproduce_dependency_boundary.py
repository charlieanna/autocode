"""Demonstrate that a source pin alone does not bind command dependency reads.

Run from the repository root with its test environment:
    PYTHONPATH=.:tools python issue-review/batch4-proof-gates/reproduce_dependency_boundary.py

This records the current behavior; it is not a requirement that ordinary scratch
commands be prevented from writing output or accessing dependencies.
"""

import json
import shlex
import sys
from pathlib import Path

import autocode_source_snapshot as source
import autocode_verify as verify

from tests.test_verify import Project


def observe(case):
    project = Project({"app.py": "value = 1\n", ".gitignore": ".venv/\n"})
    try:
        root = project.root
        environment = root / ".venv"
        environment.mkdir()
        (environment / "marker").write_text("authorized")
        foreign = Path(project.temp.name) / "foreign"
        foreign.mkdir()
        (foreign / "marker").write_text("foreign")
        if case == "retarget-existing":
            program = (
                "from pathlib import Path; p=Path('.venv'); old=p.readlink(); "
                f"p.unlink(); p.symlink_to({str(foreign)!r}, target_is_directory=True); "
                "print((p/'marker').read_text()); p.unlink(); p.symlink_to(old, target_is_directory=True)"
            )
        else:
            program = (
                "from pathlib import Path; p=Path('new-package'); p.mkdir(); "
                f"link=p/'node_modules'; link.symlink_to({str(foreign)!r}, target_is_directory=True); "
                "print((link/'marker').read_text()); link.unlink(); p.rmdir()"
            )
        revision = source.snapshot(root)["revision"]
        receipt = verify.scratch_run(
            root,
            project.evidence,
            command=shlex.join([sys.executable, "-c", program]),
            timeout=30,
            expected_source_revision=revision,
        )
        return {"exit_code": receipt.get("exit_code"), "output": Path(receipt["output"]).read_text()}
    finally:
        project.close()


if __name__ == "__main__":
    print(json.dumps({case: observe(case) for case in ("retarget-existing", "create-new")}, indent=2))
