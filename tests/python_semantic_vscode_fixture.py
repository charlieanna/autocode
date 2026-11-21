"""Prepare a real offline task at its reviewed plan gate for the VS Code host."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scenarios"))
from harness import catalog, driver  # noqa: E402, I001

BRIEF = "Extend labels() to append the exact string added after the 25 existing labels, in order, and retain existing behavior."
CHECK = "python3 -m unittest -v test_added test_existing"
SOURCE_HASH = "1950da2d0077d5b3eca5ac7dd0d359a88e390a261fd74b5c6f1aaa04cf7a13c5"
TEST_HASH = "5b8b59f7ba3165761c2959a5c74d57a963aa9592d77d7255625f8af589937c04"
EXISTING_HASH = "f0b3a4112f2dc4fdc7a0dc9e84cd99d2985d4961f8224b5271f6e37dffe7249b"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def main(argv):
    if len(argv) != 1:
        raise SystemExit("usage: python tests/python_semantic_vscode_fixture.py ROOT")
    root = Path(argv[0]).resolve(strict=True)
    project, reference = root / "project", root / "reference"
    project.mkdir()
    reference.mkdir()
    (root / "tmp").mkdir(exist_ok=True)
    items = [f"item-{index:02}" for index in range(25)]

    def source(values):
        return "def labels():\n    return [\n" + "".join(f"        {value!r},\n" for value in values) + "    ]\n"

    original = source(items)
    candidate = source([*items, "added"])
    existing = (
        "import unittest\nfrom labels import labels\n\nclass Existing(unittest.TestCase):\n"
        f"    def test_original_prefix(self):\n        self.assertEqual(labels()[:25], {items!r})\n"
    )
    added = (
        "import unittest\nfrom labels import labels\n\nclass Added(unittest.TestCase):\n"
        f"    def test_c1_added_label(self):\n        self.assertEqual(labels(), {[*items, 'added']!r})\n"
    )
    for text, digest in ((candidate, SOURCE_HASH), (added, TEST_HASH), (existing, EXISTING_HASH)):
        if hashlib.sha256(text.encode()).hexdigest() != digest:
            raise AssertionError("The offline task fixture differs from the reviewed source or tests")
    (project / "labels.py").write_text(original)
    (project / "test_existing.py").write_text(existing)
    (reference / "labels.py").write_text(candidate)
    (reference / "test_existing.py").write_text(existing)
    (reference / "test_added.py").write_text(added)
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
    scenario = dataclasses.replace(
        catalog.load("greenfield-greeting-cli"),
        title="Multiline label evidence",
        brief=BRIEF,
        fake_check=CHECK,
        fake_criteria={"C1": "test: test_c1_added_label verifies the exact ordered labels"},
    )
    flags, env = driver.fake_setup(scenario, root, reference)
    env.pop("CODEX_HOME", None)
    env["PATH"] = os.pathsep.join((str(root / "bin"), str(Path(sys.executable).parent), os.environ.get("PATH", "")))
    env["TMPDIR"] = str(root / "tmp")
    run = driver.Driver(
        project,
        root,
        [*flags, "--max-seconds", "1200"],
        env,
        autocode=[sys.executable, str(REPO / "tools/autocode.py")],
        max_steps=50,
        timeout_seconds=300,
    )
    if any(run.env.get(name) != os.environ.get(name) for name in ("HOME", "CODEX_HOME")):
        raise AssertionError("The offline fixture changed the operator's HOME or CODEX_HOME")
    run.call("start", task=BRIEF)
    listed = subprocess.run(
        [sys.executable, str(REPO / "tools/autocode.py"), "runs", "--workspace", str(project), "--json"],
        cwd=project,
        env=run.env,
        text=True,
        capture_output=True,
        check=True,
        timeout=40,
    )
    runs = json.loads(listed.stdout).get("runs") or []
    if len(runs) != 1 or runs[0].get("workspace") != str(project) or runs[0].get("availability") != "available":
        raise AssertionError("The offline fixture did not create exactly one run")
    run_dir = Path(runs[0]["run_dir"]).resolve(strict=True)
    if not run_dir.is_relative_to(project / ".autocode/runs"):
        raise AssertionError("The discovered run is outside the owned workspace")
    run.run_dir = run_dir
    view = run.until_stopped(say_at="needs:approve_plan")
    plan = view.get("displayed_plan") or {}
    if (view.get("needs") or {}).get("kind") != "approve_plan":
        raise AssertionError(f"The fixture did not reach plan review: {view.get('status')}")
    token, digest, revision = plan.get("token"), plan.get("hash"), plan.get("revision")
    if (
        not isinstance(digest, str)
        or len(digest) != 64
        or token != f"r{revision}:{digest}"
        or view["needs"]["token"] != token
    ):
        raise AssertionError("The visible plan and approval action have different identities")
    expected = json.loads((REPO / "tests/fixtures/python-semantic-host/displayed-plan.json").read_text())
    if expected.get("hash") != "<run-plan-hash>" or expected.get("token") != "<run-plan-token>":
        raise AssertionError("The reviewed plan template has unexpected identity placeholders")
    # The template's only per-run values are populated; the actual displayed
    # object is never normalized or modified. Every field must match exactly.
    expected["hash"], expected["token"] = digest, token
    if plan != expected:
        raise AssertionError("The complete displayed plan differs from the agent-reviewed offline task")
    review = {
        "token": token,
        "plan_sha256": hashlib.sha256(canonical(plan)).hexdigest(),
        "displayed_plan": plan,
        "review": "Operating agent review under the owner's delegation; offline fixture only.",
    }
    review_path = root / "agent-plan-review.json"
    review_path.write_text(json.dumps(review, ensure_ascii=False, indent=2))
    settings = {
        key: run.env[key]
        for key in (
            "AUTOCODE_HOME",
            "XDG_CONFIG_HOME",
            "PATH",
            "SCENARIO_FAKE_CONFIG",
            "TMPDIR",
            "PYTHONDONTWRITEBYTECODE",
        )
    }
    print(
        json.dumps(
            {
                "workspace": str(project),
                "run_dir": str(run.run_dir),
                "registry": str(root / "registry"),
                "plan_review": str(review_path),
                "env": settings,
                "source_sha256": SOURCE_HASH,
                "test_sha256": TEST_HASH,
                "existing_sha256": EXISTING_HASH,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
