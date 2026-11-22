"""Stage the real first-Go scenario for approval in the extension host."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

from fixture_plan_review import require_reviewed_plan

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scenarios"))
from harness import catalog, driver, project  # noqa: E402, I001

REVIEWED_PLAN = "555b508c26dd940f96edb9e997aa2f79d377b3e25da165c79f0e6c3c61bc3421"


def main(root):
    root = Path(root).resolve(strict=True)
    scenario = catalog.load("feature-go-first-package")
    workspace = project.without_maintenance(project.materialize(scenario.seed, root / "project"))
    flags, env = driver.fake_setup(scenario, root, scenario.reference)
    env.pop("CODEX_HOME", None)
    tmp = root / "tmp"
    tmp.mkdir(exist_ok=True)
    env.update(
        PATH=os.pathsep.join((str(root / "bin"), str(Path(sys.executable).parent), os.environ.get("PATH", ""))),
        TMPDIR=str(tmp),
    )
    cli = [sys.executable, str(REPO / "tools/autocode.py")]
    run = driver.Driver(
        workspace, root, [*flags, "--max-seconds", "600"], env, autocode=cli, max_steps=50, timeout_seconds=240
    )
    assert all(run.env.get(key) == os.environ.get(key) for key in ("HOME", "CODEX_HOME"))
    started = run.call(
        "start",
        scenario.brief,
        "--workspace",
        str(workspace),
        "--no-chat",
        *run.flags,
        "--build-in-worktree",
        bare=True,
    )
    if started.returncode != 2:
        raise AssertionError(f"Unexpected launch result: {started.returncode} {started.stderr[-2000:]}")
    listed = subprocess.run(
        [*cli, "runs", "--workspace", str(workspace), "--json"],
        cwd=workspace,
        env=run.env,
        capture_output=True,
        text=True,
        check=True,
        timeout=40,
    )
    rows = json.loads(listed.stdout)["runs"]
    assert len(rows) == 1, rows
    task = Path(rows[0]["workspace"]).resolve(strict=True)
    assert task.is_relative_to(workspace / ".autocode/worktrees"), rows
    run.run_dir = Path(rows[0]["run_dir"]).resolve(strict=True)
    assert run.run_dir.is_relative_to(task / ".autocode/runs")
    run.project = task
    view = run.until_stopped(say_at="needs:approve_plan")
    assert view["worktree"]["kind"] == "worktree" and view["worktree"]["path"] == str(task)
    plan, need = view["displayed_plan"], view["needs"]
    (root / "public-before-approval.json").write_text(json.dumps(view, ensure_ascii=False, indent=2))
    require_reviewed_plan(plan, need, sys.executable, REVIEWED_PLAN)
    review = root / "agent-plan-review.json"
    review.write_text(
        json.dumps(
            {
                "token": need["token"],
                "plan_sha256": hashlib.sha256(
                    json.dumps(plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
                ).hexdigest(),
                "displayed_plan": plan,
                "review": "Operating agent review under the owner's delegation.",
            }
        )
    )
    public_env = (
        "AUTOCODE_HOME",
        "XDG_CONFIG_HOME",
        "PATH",
        "SCENARIO_FAKE_CONFIG",
        "TMPDIR",
        "PYTHONDONTWRITEBYTECODE",
    )
    print(
        json.dumps(
            {
                "workspace": str(workspace),
                "task_worktree": str(task),
                "run_dir": str(run.run_dir),
                "registry": str(root / "registry"),
                "plan_review": str(review),
                "env": {key: run.env[key] for key in public_env},
            }
        )
    )


if __name__ == "__main__":
    main(sys.argv[1])
