"""Start a real offline task for a named-test proof control in VS Code."""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tests import fixture_plan_review  # noqa: E402
from tests import test_build_blackbox as build_fixture  # noqa: E402
from tests.test_failure_routing_cli import FailureRoutingCLI  # noqa: E402


class PublicFixture(FailureRoutingCLI):
    def call(self, unit, args, codes=(0,)):
        result = subprocess.run(
            self.command(unit, args), cwd=self.root, env=self.env, capture_output=True, text=True, timeout=300
        )
        (self.root / f"{unit}-last-call.json").write_text(
            json.dumps(
                {"args": args, "returncode": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
                indent=2,
            )
        )
        if result.returncode not in codes:
            raise AssertionError(f"{unit} exited {result.returncode}: {result.stderr[-2000:]}")
        return result

    def approve(self, spec, *, max_parallel=1, checker="gpt-5.6-sol", planner_extra=()):
        (self.root / "plan.json").write_text(json.dumps(spec))
        goal = spec["contract"]["intended_outcome"]
        self.call(
            "autoplanner",
            [
                goal,
                "--engine",
                "codex",
                "--in-place",
                "--builder-model",
                "gpt-6-luna",
                "--tester-model",
                checker,
                "--completion-model",
                checker,
                "--builder-strong-model",
                "gpt-5.4",
                "--max-parallel-builders",
                str(max_parallel),
                "--max-seconds",
                "600",
                "--no-chat",
                *planner_extra,
            ],
            (2,),
        )
        inventory = subprocess.run(
            [sys.executable, str(self.source / "autocode.py"), "runs", "--json", "--workspace", str(self.project)],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=40,
            check=True,
        )
        rows = [
            row
            for row in json.loads(inventory.stdout)["runs"]
            if row.get("workspace") == str(self.project) and row.get("needs") == "approve_plan"
        ]
        if len(rows) != 1:
            raise AssertionError("Public run inventory did not identify exactly one owned plan")
        self.run = Path(rows[0]["run_dir"]).resolve()
        if not self.run.is_relative_to((self.project / ".autocode/runs").resolve()):
            raise AssertionError("Public run inventory named a run outside the owned workspace")
        initial = self.status()
        plan = initial["view"]["displayed_plan"]
        need = initial["view"]["needs"]
        path = self.root / "agent-plan-review.json"
        path.write_text(
            json.dumps(
                {
                    "token": plan["token"],
                    "plan": plan,
                    "review": "Operating agent review under the owner's delegation; offline fixture only.",
                }
            )
        )
        fixture_plan_review.require_reviewed_plan(
            plan, need, sys.executable, "bb31ffeae4ec5b6d368a29f4db82a84ac2d4bfc41087ce4c1bccae5f0465dfcc"
        )
        self.approved_token = initial["contract_token"]
        if self.approved_token != plan["token"]:
            raise AssertionError("Displayed and approved tokens differ")


def main(argv: list[str]) -> int:
    if len(argv) != 2 or argv[1] not in ("skip", "missing"):
        raise SystemExit("usage: named_proof_vscode_fixture.py ROOT {skip|missing}")
    root = Path(argv[0]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["BUILD_AUDIT_ARTIFACTS"] = str(root)
    # The imported setup creates its first commit before it constructs a child
    # environment. Never run an operator's configured Git hooks or signing.
    os.environ.update(
        GIT_CONFIG_COUNT="2",
        GIT_CONFIG_KEY_0="core.hooksPath",
        GIT_CONFIG_VALUE_0=os.devnull,
        GIT_CONFIG_KEY_1="commit.gpgsign",
        GIT_CONFIG_VALUE_1="false",
    )
    fixture = PublicFixture(methodName="test_uncertain_no_source_change_investigates_before_spending_and_refuses")
    fixture.setUp()
    for key, value in (("core.hooksPath", os.devnull), ("commit.gpgsign", "false")):
        subprocess.run(["git", "config", "--local", key, value], cwd=fixture.project, check=True)
    if "CODEX_HOME" in os.environ:
        fixture.env["CODEX_HOME"] = os.environ["CODEX_HOME"]
    else:
        fixture.env.pop("CODEX_HOME", None)
    if fixture.env.get("HOME") != os.environ.get("HOME"):
        raise AssertionError("Fixture changed inherited HOME")
    bindir, old_path = fixture.env["PATH"].split(os.pathsep, 1)
    fixture.env["PATH"] = os.pathsep.join((bindir, str(Path(sys.executable).parent), old_path))
    for name in tuple(fixture.env):
        if (
            name == "GIT_CONFIG_COUNT"
            or re.match(
                r"^(GIT_CONFIG_KEY_|GIT_CONFIG_VALUE_|OPENAI_|ANTHROPIC_|KILO_API_|CODEX_API_|ZAI_API_|AWS_|AZURE_|GOOGLE_)",
                name,
            )
            or re.search(r"(_API_KEY|_TOKEN|_SECRET|_PASSWORD)$", name)
        ):
            fixture.env.pop(name, None)
    fixture.env.update(AUTOCODE_SOURCE_HOST="0", FAILURE_ROUTING_DIAGNOSIS="unknown")
    (fixture.project / "app.py").write_text("def old():\n    return 1\n")
    (fixture.project / "test_existing.py").write_text("import app\ndef test_old():\n    assert app.old() == 1\n")
    subprocess.run(["git", "add", "app.py", "test_existing.py"], cwd=fixture.project, check=True)
    subprocess.run(
        ["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "Original project"],
        cwd=fixture.project,
        check=True,
    )
    source = "def old():\n    return 1\ndef new():\n    return 42\n"
    test = (
        "import app, pytest\ndef test_new():\n    if not hasattr(app, 'new'): pytest.skip('feature absent')\n    assert app.new() == 42\n"
        if argv[1] == "skip"
        else "import app\ndef test_present():\n    assert app.old() == 1\nif hasattr(app, 'new'):\n    def test_new():\n        assert app.new() == 42\n"
    )
    command = shlex.join([sys.executable, "-m", "pytest", "-q"])
    spec = build_fixture.plan(
        [([], "The new feature returns 42", ["app.py", "test_feature.py"])],
        {"M1": {"app.py": source, "test_feature.py": test}},
        {"M1": "import app; assert app.new() == 42"},
        "Add the new feature returning 42",
    )
    spec["contract"]["constraints"] = ["Project code uses only the Python standard library; tests use pytest"]
    fixture.approve(spec, planner_extra=("--test-command", command))
    view = fixture.status()["view"]
    if (view.get("needs") or {}).get("kind") != "approve_plan":
        raise AssertionError("The offline run did not stop at the reviewed plan")
    print(
        json.dumps(
            {
                "workspace": str(fixture.project),
                "runDir": str(fixture.run),
                "env": {
                    name: fixture.env[name]
                    for name in (
                        "AUTOCODE_HOME",
                        "XDG_CONFIG_HOME",
                        "PATH",
                        "AUTOCODE_SOURCE_HOST",
                        "PYTHONDONTWRITEBYTECODE",
                        "BUILD_AUDIT_SPEC",
                        "BUILD_AUDIT_LOG",
                        "FAILURE_ROUTING_PROVIDER",
                        "FAILURE_ROUTING_TRACE",
                        "FAILURE_ROUTING_DIAGNOSIS",
                    )
                },
                "trace": fixture.env["FAILURE_ROUTING_TRACE"],
                "planReview": str(fixture.root / "agent-plan-review.json"),
                "planToken": fixture.approved_token,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
