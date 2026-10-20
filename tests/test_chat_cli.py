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
        root = Path(tempfile.mkdtemp(prefix="chat-cli-", dir=os.environ.get("BUILD_AUDIT_ARTIFACTS"))).resolve()
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

    def test_requirements_answers_any_opener_first_and_the_job_is_recognized_after_approval(self):
        # An unrelated existing file must not force code inspection/citations
        # before the user has even named a goal.
        (self.workspace / "README.md").write_text("# Existing project\n")
        opener = "What is your name?"
        run = taskrun.TaskRun.start(
            self.workspace, opener, options=(*FIXTURE_OPTIONS, "--spec-first"), env=self.env, timeout=300
        )
        view = run.advance_until_input()

        def count(slug):
            return len(list(run.run_dir.glob(f"iterations/*/{slug}-*.jsonl")))

        self.assertEqual("answer", view["needs"]["kind"], view.get("needs"))
        self.assertEqual(1, count("requirements-gather"), "Requirements answers the first message")
        self.assertEqual(0, count("recognize-workflow"), "The job is recognized only after the goal is understood")
        self.assertFalse((run.run_dir / "model-preflight").exists(), "A chat turn must not probe models")
        messages = view["chat"]["messages"]
        self.assertEqual(("user", opener), (messages[0]["role"], messages[0]["text"]))
        self.assertEqual("assistant", messages[-1]["role"])
        self.assertIn("What would you like to build or change?", messages[-1]["text"])
        self.assertIsNone(view.get("displayed_plan"))
        self.assertEqual("Send reply", view["chat"]["input"]["label"])
        self.assertNotIn("--approve-goal", view["next_command"])
        # The chat's "Send reply" is brief feedback; a reply that names the work becomes the task.
        run.feedback(BRIEF)
        self.assertEqual(BRIEF, run.status()["task"])
        clarified = run.advance_until_input()
        self.assertEqual("answer", clarified["needs"]["kind"])
        self.assertTrue(clarified["needs"]["questions"][-1]["id"].startswith("requirements-review-"))
        self.assertEqual((2, 0, 0), (count("requirements-gather"), count("recognize-workflow"), count("discovery")))
        previous_question = clarified["needs"]["questions"][-1]
        run.feedback("Keep the same scope and standard-library-only constraint.")
        refreshed = run.advance_until_input()
        self.assertEqual("answer", refreshed["needs"]["kind"])
        self.assertNotEqual(previous_question["id"], refreshed["needs"]["questions"][-1]["id"])
        threads = []
        for path in sorted(run.run_dir.glob("iterations/*/requirements-gather-*.jsonl")):
            rows = [json.loads(line) for line in path.read_text().splitlines()]
            threads.append(next(row["thread_id"] for row in rows if row.get("type") == "thread.started"))
        self.assertEqual(3, len(threads))
        self.assertEqual(1, len(set(threads)), "Requirements must retain its session across replies")
        self.assertEqual(0, count("recognize-workflow"))
        self.assertFalse((self.workspace / "greet.py").exists())
        approved = refreshed["needs"]["questions"][-1]
        run.answer(approved["id"], "plan", resolver_token=refreshed["needs"]["resolver_token"])
        self.assertEqual("recognize_workflow", run.status()["next_stage"])
        reviewed = run.advance_until_input()
        self.assertEqual("approve_plan", reviewed["needs"]["kind"])
        self.assertEqual("build", reviewed["workflow"])
        self.assertEqual(1, count("recognize-workflow"))
        self.assertIsNotNone(reviewed["displayed_plan"])
        self.assertTrue(list(run.run_dir.glob("iterations/*/plan-challenge-*.jsonl")))
        texts = [row["text"] for row in reviewed["chat"]["messages"] if row["role"] == "user"]
        self.assertEqual((1, 1), (texts.count(opener), texts.count(BRIEF)), texts)
        self.assertFalse((self.workspace / "greet.py").exists(), "Requirements approval cannot start implementation")
        run.approve_plan(reviewed["needs"]["token"])
        self.assertEqual("TASK_COMPLETE", run.advance_until_input()["status"])
        # A later request in the same chat is gathered again (then confirmed at plan approval),
        # not planned straight from the previous turn's handoff.
        gathered = count("requirements-gather")
        run.follow_up("Keep the greeting CLI and also add a --shout option.")
        again = run.advance_until_input()
        self.assertEqual("approve_plan", again["needs"]["kind"], again.get("needs"))
        self.assertEqual(gathered + 1, count("requirements-gather"), "The follow-up must not skip Requirements")

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
