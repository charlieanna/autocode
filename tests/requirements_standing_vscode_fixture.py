"""Create an owned, offline run at the public Requirements review gate."""

from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(REPO / "tools"), str(REPO / "scenarios")]
from autocode_taskrun import TaskRun  # noqa: E402
from harness import catalog, driver  # noqa: E402

BRIEF = "Build a command that greets a person by name."


def main() -> None:
    root = Path(sys.argv[1]).resolve(strict=True)
    project, reference = root / "project", root / "reference"
    project.mkdir()
    reference.mkdir()
    (project / "convention.py").write_text('def reject():\n    raise ValueError("invalid input")\n')
    git = ["git", "-c", "credential.helper=", "-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false"]
    subprocess.run([*git, "init", "-q", str(project)], check=True)
    subprocess.run([*git, "-C", str(project), "add", "."], check=True)
    subprocess.run(
        [
            *git,
            "-C",
            str(project),
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "-qm",
            "base",
        ],
        check=True,
    )
    scenario = dataclasses.replace(catalog.load("greenfield-greeting-cli"), brief=BRIEF)
    flags, env = driver.fake_setup(scenario, root, reference)
    env.pop("CODEX_HOME", None)
    base = root / "bin/base_codex.py"
    (root / "bin/codex").rename(base)
    provider = REPO / "tests/requirements_standing_provider.py"
    (root / "bin/codex").write_text(
        f'#!{sys.executable}\nimport runpy\nrunpy.run_path({str(provider)!r}, run_name="__main__")\n'
    )
    (root / "bin/codex").chmod(0o755)
    env.update(
        {
            "STANDING_BASE_PROVIDER": str(base),
            "AUTOCODE_HOME": str(root / "registry"),
            "AUTOCODE_SOURCE_HOST": "0",
            "TMPDIR": str(root / "tmp"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
    )
    env["PATH"] = os.pathsep.join((str(root / "bin"), str(Path(sys.executable).parent), os.environ.get("PATH", "")))
    run = TaskRun.start(
        project,
        BRIEF,
        command=[sys.executable, str(REPO / "tools/autocode.py")],
        options=(*flags, "--spec-first", "--max-cost-usd", "0"),
        env=env,
        cwd=root,
        timeout=180,
    )
    view = run.advance_until_input()
    need = view.get("needs") or {}
    rows = (view.get("requirements") or {}).get("proposed_assumptions") or []
    if need.get("kind") != "answer" or not need.get("questions", [{}])[-1].get("id", "").startswith(
        "requirements-review-"
    ):
        raise AssertionError(f"Expected Requirements review, got {need}")
    if len(rows) != 1 or rows[0].get("standing") != "convention_not_approved":
        raise AssertionError(f"Expected the unapproved convention: {rows}")
    print(json.dumps({"workspace": str(project), "run_dir": str(run.run_dir), "env": env, "brief": BRIEF}))


if __name__ == "__main__":
    main()
