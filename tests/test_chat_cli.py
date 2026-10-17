"""Drive the chat projection through the public CLI with the offline provider."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun
from harness.conformance import ConformanceDriver

from tests.test_taskrun import BRIEF, FIXTURE_OPTIONS, HERE


class ChatCliTests(unittest.TestCase):
    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="chat-cli-", dir=os.environ.get("BUILD_AUDIT_ARTIFACTS")))
        if not os.environ.get("BUILD_AUDIT_ARTIFACTS"):
            self.addCleanup(shutil.rmtree, root)
        self.workspace = root / "project"
        self.workspace.mkdir()
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=T",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-q",
                "--allow-empty",
                "-m",
                "base",
            ],
            check=True,
        )
        bindir = root / "bin"
        bindir.mkdir()
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "codex")
        (bindir / "codex").chmod(0o755)
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def test_source_inventory_refusal_retains_the_report_and_resumes_without_another_provider_call(self):
        root = self.workspace.parent
        marker = root / "inventory-refusal"
        bindir = Path(self.env["PATH"].split(os.pathsep)[0])
        real_git = shutil.which("git")
        wrapper = bindir / "git"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import json, os, sys\nfrom pathlib import Path\n"
            f"workspace = Path({str(self.workspace)!r})\nmarker = Path({str(marker)!r})\n"
            "if sys.argv[1:2] == ['ls-files'] and (not marker.exists() or marker.read_text() != 'resolved'):\n"
            " for path in workspace.glob('.autocode/runs/*/iterations/*/plan-challenge-*.jsonl'):\n"
            "  for line in path.read_text().splitlines():\n"
            "   try: event = json.loads(line)\n"
            "   except ValueError: continue\n"
            "   if event.get('type') == 'turn.completed':\n"
            "    marker.write_text('one refusal after a completed model report')\n"
            "    print('fatal: detected dubious ownership (injected)', file=sys.stderr)\n"
            "    sys.exit(128)\n"
            f"os.execv({real_git!r}, [{real_git!r}, *sys.argv[1:]])\n"
        )
        wrapper.chmod(0o755)
        options = (*FIXTURE_OPTIONS, "--no-adaptive-planning")
        run_dir = self.workspace / ".autocode/runs/source-refusal"
        result = subprocess.run(
            [
                *taskrun.AUTOCODE,
                BRIEF,
                *options,
                "--in-place",
                "--no-chat",
                "--workspace",
                str(self.workspace),
                "--new-run-dir",
                str(run_dir),
            ],
            env={**os.environ, **self.env},
            text=True,
            capture_output=True,
            timeout=300,
        )
        self.assertTrue(marker.exists(), result.stdout + result.stderr)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertIn("dubious ownership", result.stderr)
        run = taskrun.TaskRun(self.workspace, run_dir, options=options, env=self.env, timeout=300)
        held = run.status()
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", held["status"])
        self.assertEqual("your_environment", held["pause_category"])
        self.assertEqual("resume", held["needs"]["kind"])
        self.assertIn("--resume-paused", held["next_command"])
        self.assertFalse(held["done"])
        self.assertIsNone(held.get("displayed_plan"))
        reports = {path.relative_to(run_dir): path.read_bytes() for path in run_dir.glob("iterations/*/*.jsonl")}
        self.assertTrue(reports)
        still_held = run.resume_paused()
        self.assertEqual("PAUSED_SOURCE_UNAVAILABLE", still_held["status"])
        self.assertEqual(
            reports,
            {path.relative_to(run_dir): path.read_bytes() for path in run_dir.glob("iterations/*/*.jsonl")},
            "An unresolved source refusal must not launch any provider",
        )
        marker.write_text("resolved")
        resumed = run.resume_paused()
        self.assertEqual("approve_plan", resumed["needs"]["kind"])
        for path, content in reports.items():
            self.assertEqual(content, (run_dir / path).read_bytes(), "Recovery must preserve the retained report")
        self.assertEqual(
            sorted(path for path in reports if "plan-challenge-" in path.name),
            sorted(path.relative_to(run_dir) for path in run_dir.glob("iterations/*/plan-challenge-*.jsonl")),
            "Recovery must not replay the completed reviewer; the following finalize stage may run",
        )
        self.assertFalse((self.workspace / "greet.py").exists())

    def test_greeting_asks_for_the_outcome_and_a_reply_returns_to_requirements_before_any_plan(self):
        # An unrelated existing file must not force code inspection/citations
        # before the user has even named a goal.
        (self.workspace / "README.md").write_text("# Existing project\n")
        run = taskrun.TaskRun.start(
            self.workspace, "hi", options=(*FIXTURE_OPTIONS, "--spec-first"), env=self.env, timeout=300
        )
        view = run.status()
        self.assertEqual("answer", view["needs"]["kind"])
        questions = view["needs"]["questions"]
        self.assertEqual(1, len(questions), questions)
        self.assertEqual("Hi! What would you like to work on today?", questions[0]["question"])
        self.assertFalse(list(run.run_dir.glob("iterations/*/*.jsonl")), "A greeting must dispatch no provider")
        self.assertFalse((run.run_dir / "model-preflight").exists(), "A greeting must not probe models")
        self.assertEqual(questions[0]["question"], view["chat"]["messages"][-1]["text"])
        self.assertIsNone(view.get("displayed_plan"))
        self.assertEqual("Send reply", view["chat"]["input"]["label"])
        self.assertNotIn("--approve-goal", view["next_command"])
        self.assertFalse(list(run.run_dir.glob("iterations/*/discovery-*.jsonl")))
        # The chat's "Send reply" is brief feedback; the first reply names the work.
        run.feedback(BRIEF)
        self.assertEqual(BRIEF, run.status()["task"])
        clarified = run.advance_until_input()
        self.assertEqual("answer", clarified["needs"]["kind"])
        self.assertTrue(clarified["needs"]["questions"][-1]["id"].startswith("requirements-review-"))
        self.assertEqual(1, len(list(run.run_dir.glob("iterations/*/recognize-workflow-*.jsonl"))))
        self.assertEqual(1, len(list(run.run_dir.glob("iterations/*/requirements-gather-*.jsonl"))))
        self.assertFalse(list(run.run_dir.glob("iterations/*/discovery-*.jsonl")))
        self.assertIn(BRIEF, [row["text"] for row in clarified["chat"]["messages"]])
        previous_question = clarified["needs"]["questions"][-1]
        run.feedback("Keep the same scope and standard-library-only constraint.")
        refreshed = run.advance_until_input()
        self.assertEqual("answer", refreshed["needs"]["kind"])
        self.assertNotEqual(previous_question["id"], refreshed["needs"]["questions"][-1]["id"])
        threads = []
        for path in sorted(run.run_dir.glob("iterations/*/requirements-gather-*.jsonl")):
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            threads.append(next(row["thread_id"] for row in rows if row.get("type") == "thread.started"))
        self.assertEqual(2, len(threads))
        self.assertEqual(threads[0], threads[1], "Requirements must retain its session across replies")
        self.assertEqual(1, len(list(run.run_dir.glob("iterations/*/recognize-workflow-*.jsonl"))))
        self.assertFalse((self.workspace / "greet.py").exists())
        approved = refreshed["needs"]["questions"][-1]
        run.answer(approved["id"], "plan", resolver_token=refreshed["needs"]["resolver_token"])
        self.assertEqual("astra_discovery", run.status()["next_stage"])
        reviewed = run.advance_until_input()
        self.assertEqual("approve_plan", reviewed["needs"]["kind"])
        self.assertIsNotNone(reviewed["displayed_plan"])
        self.assertTrue(list(run.run_dir.glob("iterations/*/plan-challenge-*.jsonl")))
        self.assertFalse((self.workspace / "greet.py").exists(), "Requirements approval cannot start implementation")

    def test_chat_named_feedback_and_followup_keep_one_run_and_require_fresh_approval(self):
        run = taskrun.TaskRun.start(
            self.workspace, BRIEF, options=(*FIXTURE_OPTIONS, "--no-adaptive-planning"), env=self.env, timeout=300
        )
        first = run.status()

        def send(view, text):
            client = object.__new__(ConformanceDriver)
            client.project, client.run_dir = self.workspace, run.run_dir
            words, env, source = client.message_command(view, text)
            self.assertEqual("chat.input.next_command", source)
            proc = subprocess.run(
                [*taskrun.AUTOCODE, *words, "--workspace", str(self.workspace), "--run-dir", str(run.run_dir)],
                env={**os.environ, **self.env, **env},
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, proc.returncode, proc.stdout + proc.stderr)
            return run.status()

        self.assertEqual("feedback", first["chat"]["input"]["kind"])
        # The offline provider has one fixed requirements corpus. Reiterate a
        # supported clause to exercise invalidation without inventing model output.
        feedback = "Python standard library only."
        saved = send(first, feedback)
        self.assertEqual(first["usage"], saved["usage"], "Saving feedback must not launch a provider")
        self.assertIn(feedback, [row["text"] for row in saved["chat"]["messages"]])
        self.assertFalse((self.workspace / "greet.py").exists())
        refreshed = run.advance_until_input()
        self.assertEqual("approve_plan", refreshed["needs"]["kind"])
        with self.assertRaises(taskrun.TaskRunError):
            run.approve_plan(first["needs"]["token"])
        run.approve_plan(refreshed["needs"]["token"])
        finished = run.advance_until_input()
        self.assertEqual("TASK_COMPLETE", finished["status"])
        reply = finished["chat"]["messages"][-1]
        self.assertEqual("assistant", reply["role"])
        self.assertEqual("follow_up", finished["chat"]["input"]["kind"])
        followup = "Review the greeting implementation."
        saved = send(finished, followup)
        self.assertEqual(2, saved["turn"])
        self.assertFalse(saved["done"])
        self.assertIn(followup, [row["text"] for row in saved["chat"]["messages"]])
        self.assertIn(reply["text"], [row["text"] for row in saved["chat"]["messages"] if row["role"] == "assistant"])
        reattached = taskrun.TaskRun(self.workspace, run.run_dir, env=self.env)
        self.assertEqual(saved["chat"], reattached.status()["chat"])
