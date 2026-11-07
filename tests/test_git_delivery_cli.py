"""Real CLI delivery through the public task-run interface; scripted provider only."""

import subprocess
import unittest
from pathlib import Path

import autocode_taskrun as taskrun
import autocode_util as util

from . import test_chat_cli as chat_cli
from .test_taskrun import BRIEF, FIXTURE_OPTIONS, HERE


class GitDeliveryCLI(unittest.TestCase):
    setUp = chat_cli.ChatCliTests.setUp

    def test_approved_atomic_delivery_finishes_only_after_verifying_the_commit(self):
        self.check_delivery(builder_commits=False)

    def test_builder_commit_finishes_without_an_operator_git_reset_or_resume_loop(self):
        self.check_delivery(builder_commits=True)

    def check_delivery(self, *, builder_commits):
        for key, value in (("user.name", "Fixture"), ("user.email", "fixture@example.test")):
            subprocess.run(["git", "-C", str(self.workspace), "config", key, value], check=True)
        (self.workspace / ".gitignore").write_text(".autocode/\n")
        subprocess.run(["git", "-C", str(self.workspace), "add", ".gitignore"], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "commit", "-qm", "Ignore runner metadata"], check=True)
        base = subprocess.check_output(["git", "-C", str(self.workspace), "rev-parse", "HEAD"], text=True).strip()
        provider = Path(self.env["PATH"].split(":")[0]) / "codex"
        script = (
            (HERE / "live_fixture_provider.py")
            .read_text()
            .replace("def _contract() -> dict:", "def _original_contract() -> dict:")
        )
        script = script.replace(
            "def _criteria(",
            """def _contract():
    plan = _original_contract()
    plan["git_delivery"] = {"kind": "atomic_commit", "message": "Deliver greeting CLI"}
    plan["acceptance_criteria"][0]["verification_method"] = "test: test_greet"
    return plan


def _criteria(""",
        )
        script = script.replace(
            '    elif stage in ("sol", "astra_checkpoint"):',
            '    elif stage in ("sol", "astra_checkpoint"):\n        if subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip() == '
            + repr(base)
            + ':\n            raise RuntimeError("Delivery criterion cannot pass before the controller commit")',
        )
        self.assertIn("Delivery criterion cannot pass", script)
        if builder_commits:
            script = script.replace(
                "        changed = _write_files()",
                "        changed = _write_files()\n"
                '        subprocess.run(["git", "add", "greet.py", "test_greet.py", "README.md"], check=True)\n'
                '        subprocess.run(["git", "commit", "-qm", "Builder committed its own change"], check=True)',
            )
        provider.write_text(script)
        run = taskrun.TaskRun.start(
            self.workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--no-adaptive-planning"), env=self.env, timeout=300
        )
        plan = run.status()
        self.assertTrue("displayed_plan" in plan, {k: plan.get(k) for k in ("status", "stop_reason", "needs")})
        self.assertEqual("atomic_commit", plan["displayed_plan"]["git_delivery"]["kind"])
        run.approve_plan(plan["needs"]["token"])
        finished = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", finished["status"], finished.get("stop_reason"))
        head = subprocess.check_output(["git", "-C", str(self.workspace), "rev-parse", "HEAD"], text=True).strip()
        self.assertNotEqual(base, head)
        self.assertEqual(
            "Deliver greeting CLI",
            subprocess.check_output(["git", "-C", str(self.workspace), "log", "-1", "--format=%s"], text=True).strip(),
        )
        self.assertEqual(
            "1",
            subprocess.check_output(
                ["git", "-C", str(self.workspace), "rev-list", "--count", base + "..HEAD"], text=True
            ).strip(),
        )
        self.assertEqual(
            "", subprocess.check_output(["git", "-C", str(self.workspace), "status", "--porcelain"], text=True).strip()
        )
        self.assertTrue(finished["done"])
        self.assertEqual("follow_up", finished["chat"]["input"]["kind"])
        # Evidence must be current on the actual committed HEAD, rather than the
        # earlier pre-commit source revision that originally passed review.
        report = run.evidence_report()
        self.assertEqual("current", report["availability"], report)
        self.assertEqual(util.snapshot(self.workspace)["revision"], report["document"]["revision"]["source_revision"])
