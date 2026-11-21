"""Start an offline task to an actual unknown-classification outcome for VS Code."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))
from tests.test_failure_routing_cli import FailureRoutingCLI  # noqa: E402


class PublicFixture(FailureRoutingCLI):
    def call(self, unit, args, codes=(0,)):
        result = subprocess.run(
            self.command(unit, args), cwd=self.root, env=self.env, capture_output=True, text=True, timeout=300
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
                "--no-chat",
                *planner_extra,
            ],
            (2,),
        )
        self.run = next((self.project / ".autocode/runs").iterdir())
        initial = self.status()
        plan = initial["view"]["displayed_plan"]
        review = plan["review_markdown"]
        path = self.root / "agent-plan-review.json"
        path.write_text(
            json.dumps(
                {
                    "token": plan["token"],
                    "review_markdown": review,
                    "review": "Operating agent review under the owner's delegation; offline fixture only.",
                }
            )
        )
        digest = hashlib.sha256(review.encode()).hexdigest()
        if digest != "26d5c4fc0aad7571716514115dd1b06fdea095dbb1c05eb3fdf5181cd8cd6635":
            raise AssertionError(f"Review the fixture plan at {path}; sha256={digest}")
        self.approved_token = initial["contract_token"]
        if self.approved_token != plan["token"]:
            raise AssertionError("Displayed and approved tokens differ")


def main(argv: list[str]) -> int:
    if len(argv) != 1:
        raise SystemExit("usage: classification_vscode_fixture.py ROOT")
    root = Path(argv[0]).resolve()
    root.mkdir(parents=True, exist_ok=True)
    os.environ["BUILD_AUDIT_ARTIFACTS"] = str(root)
    fixture = PublicFixture(methodName="test_uncertain_no_source_change_investigates_before_spending_and_refuses")
    fixture.setUp()
    if "CODEX_HOME" in os.environ:
        fixture.env["CODEX_HOME"] = os.environ["CODEX_HOME"]
    else:
        fixture.env.pop("CODEX_HOME", None)
    if fixture.env.get("HOME") != os.environ.get("HOME"):
        raise AssertionError("Fixture changed inherited HOME")
    for name in (
        "OPENAI_API_KEY",
        "CODEX_API_KEY",
        "OPENAI_BASE_URL",
        "ANTHROPIC_API_KEY",
        "KILO_API_KEY",
        "ZAI_API_KEY",
    ):
        fixture.env.pop(name, None)
    fixture.env.update(AUTOCODE_SOURCE_HOST="0", BUILD_AUDIT_FAULT="no_change", FAILURE_ROUTING_DIAGNOSIS="unknown")
    fixture.seed()
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
                        "BUILD_AUDIT_FAULT",
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
