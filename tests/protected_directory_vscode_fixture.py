"""Start a real offline task for protected-directory replay in the Extension Host."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

repo = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(repo / "scenarios"))
from fixture_plan_review import require_reviewed_plan
from harness import catalog, driver, project

root = Path(sys.argv[1]).resolve(strict=True)
seed = repo / "scenarios/catalog/bugfix-trivial"
reference = root / "reference"
shutil.copytree(seed / "reference", reference)
scenario = catalog.load("bugfix-trivial")
workspace = project.without_maintenance(project.materialize(seed / "seed", root / "project"))
fixture = workspace / "tests/fixtures/v2"
fixture.mkdir(parents=True)
(fixture / "example.txt").write_text("approved fixture data\n")
(fixture.parent / "current").symlink_to("v2/")
(fixture.parent / "example").symlink_to("current/example.txt")
with (workspace / "tests/__init__.py").open("a") as out:
    out.write(
        "\nfrom pathlib import Path\n"
        "fixture = Path(__file__).with_name('fixtures') / 'example'\n"
        "assert fixture.read_text() == 'approved fixture data\\n'\n"
        "print('Original directory fixture verified')\n"
    )
project.git(workspace, "add", "tests")
project.git(workspace, *project.NO_MAINTENANCE, "commit", "-qm", "Add protected directory fixture")
flags, env = driver.fake_setup(scenario, root, reference)
env.pop("CODEX_HOME", None)
env["PATH"] = os.pathsep.join((str(root / "bin"), str(Path(sys.executable).parent), os.environ["PATH"]))
env["TMPDIR"] = str(root / "tmp")
env["AUTOCODE_SOURCE_HOST"] = "0"
cli = [sys.executable, str(repo / "tools/autocode.py")]
run = driver.Driver(
    workspace, root, [*flags, "--max-seconds", "900"], env, autocode=cli, max_steps=45, timeout_seconds=180
)
run.call("start", task=scenario.brief)
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
assert len(rows) == 1 and rows[0]["workspace"] == str(workspace)
run.run_dir = Path(rows[0]["run_dir"]).resolve(strict=True)
assert run.run_dir.is_relative_to(workspace / ".autocode/runs")
view = run.until_stopped(say_at="needs:approve_plan")
assert view["needs"]["kind"] == "approve_plan", (view["status"], view.get("stop_reason"))
plan = view["displayed_plan"]
assert plan["token"] == view["needs"]["token"] == f"r{plan['revision']}:{plan['hash']}"
require_reviewed_plan(
    plan, view["needs"], sys.executable, "17c2933f3c5eef9491a35e280a539b8c1ff0bdc81e306bb6dc41a9b1b66daff3"
)
review = root / "agent-plan-review.json"
review.write_text(
    json.dumps(
        {
            "token": plan["token"],
            "plan_sha256": hashlib.sha256(
                json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest(),
            "displayed_plan": plan,
            "review": "Operating agent review under the owner's delegation.",
        }
    )
)
keys = (
    "AUTOCODE_HOME",
    "XDG_CONFIG_HOME",
    "PATH",
    "SCENARIO_FAKE_CONFIG",
    "TMPDIR",
    "PYTHONDONTWRITEBYTECODE",
    "AUTOCODE_SOURCE_HOST",
)
print(
    json.dumps(
        {
            "workspace": str(workspace),
            "run_dir": str(run.run_dir),
            "registry": str(root / "registry"),
            "plan_review": str(review),
            "env": {key: run.env[key] for key in keys},
        }
    )
)
