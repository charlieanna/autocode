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

    def test_requirements_answers_any_opener_first_and_the_job_it_names_starts_after_approval(self):
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
        self.assertEqual(0, count("recognize-workflow"), "Requirements names the job; no recognizer runs (#1846)")
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
        self.assertEqual(4, len(threads), "One reply interpretation precedes the updated Requirements handoff")
        self.assertEqual(1, len(set(threads)), "Requirements must retain its session across replies")
        self.assertEqual(0, count("recognize-workflow"))
        self.assertFalse((self.workspace / "greet.py").exists())
        approved = refreshed["needs"]["questions"][-1]
        run.answer(approved["id"], "plan", resolver_token=refreshed["needs"]["resolver_token"])
        # The build Requirements named and the person approved is planned at once (#1846).
        self.assertEqual("draft_plan", run.status()["next_stage"])
        reviewed = run.advance_until_input()
        self.assertEqual("approve_plan", reviewed["needs"]["kind"])
        self.assertEqual("build", reviewed["workflow"])
        self.assertEqual(0, count("recognize-workflow"))
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


class SmallTurnChatTests(unittest.TestCase):
    """The small-turn acceptance examples (greeting skip, single pass, goal-turn guards).

    A wrapper provider delegates every dispatch to the copied offline fixture and then
    overrides the Requirements answer with the exact shape the approved criterion gives,
    so the turn is driven through the public task-run interface with the offline provider.
    """

    REPLY = "Hi! I'm AutoCode's requirements assistant."
    GOAL = "Add a --shout option to the greeting CLI"
    OUTCOME = "The greeting CLI gains a --shout option"
    REQUIREMENT = {
        "id": "R1",
        "text": "With --shout the CLI prints the greeting in upper case",
        "source_quote": "Add a --shout option to the greeting CLI",
    }

    def setUp(self):
        root = Path(tempfile.mkdtemp(prefix="small-turn-", dir=os.environ.get("BUILD_AUDIT_ARTIFACTS")))
        if not os.environ.get("BUILD_AUDIT_ARTIFACTS"):
            self.addCleanup(shutil.rmtree, root)
        self.root = root
        self.workspace = root / "project"
        self.workspace.mkdir()
        # A goal turn's repaired report cites inspected source, so the folder names one.
        (self.workspace / "README.md").write_text("# Greeting project\n")
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
        shutil.copy2(HERE / "live_fixture_provider.py", bindir / "fixture-provider.py")
        wrapper = bindir / "codex"
        wrapper.write_text(
            f"#!{sys.executable}\n"
            "import json, os, subprocess, sys\n"
            "fixture = os.path.join(os.path.dirname(os.path.realpath(__file__)), 'fixture-provider.py')\n"
            "prompt = sys.stdin.read()\n"
            "try:\n"
            "    data = json.loads(prompt.split('CURRENT HANDOFF DATA\\n', 1)[1])\n"
            "except (IndexError, ValueError):\n"
            "    data = {}\n"
            "stage = data.get('stage') or (data.get('original') or {}).get('stage')\n"
            "overrides = None\n"
            "if stage == 'requirements_gather':\n"
            "    key = 'AUTOCODE_TEST_REPAIR_ANSWER' if data.get('report_repair') else 'AUTOCODE_TEST_FIRST_ANSWER'\n"
            "    path = os.environ.get(key)\n"
            "    if path and os.path.isfile(path):\n"
            "        with open(path) as source:\n"
            "            overrides = json.load(source)\n"
            "proc = subprocess.run(\n"
            "    [sys.executable, fixture, *sys.argv[1:]], input=prompt, text=True, capture_output=True\n"
            ")\n"
            "if overrides is not None:\n"
            "    output = sys.argv[sys.argv.index('-o') + 1]\n"
            "    with open(output) as source:\n"
            "        report = json.load(source)\n"
            "    report.update(overrides)\n"
            "    with open(output, 'w') as target:\n"
            "        target.write(json.dumps(report))\n"
            "sys.stdout.write(proc.stdout)\n"
            "sys.stderr.write(proc.stderr)\n"
            "sys.exit(proc.returncode)\n"
        )
        wrapper.chmod(0o755)
        self.env = {
            "PATH": f"{bindir}{os.pathsep}{os.environ['PATH']}",
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }

    def drive(self, message, first_answer, repair_answer):
        """Run one chat turn to its person-facing stop; return (run, view, controller invocations).

        Controller invocations are the task-run actions that advance the turn, one CLI
        invocation each: the start invocation plus every advance until the run stops.
        """
        scripts = self.root / "answers"
        scripts.mkdir(exist_ok=True)
        first, repair = scripts / "first.json", scripts / "repair.json"
        first.write_text(json.dumps(first_answer))
        repair.write_text(json.dumps(repair_answer))
        env = {
            **self.env,
            "AUTOCODE_TEST_FIRST_ANSWER": str(first),
            "AUTOCODE_TEST_REPAIR_ANSWER": str(repair),
        }
        run = taskrun.TaskRun.start(
            self.workspace, message, options=(*FIXTURE_OPTIONS, "--spec-first"), env=env, timeout=300
        )
        invocations = 1  # TaskRun.start's invocation already advanced the turn
        view = run.status()
        for _ in range(10):
            if view["done"] or view["needs"]["kind"] != "continue":
                return run, view, invocations
            view = run.advance()
            invocations += 1
        raise AssertionError(f"the turn never stopped for the person: {view.get('needs')}")

    @staticmethod
    def dispatch_counts(run):
        # A rejected dispatch is archived under iterations/NNN/archived-*, an accepted one
        # stays live, so count the recorded dispatch artifacts recursively.
        gather = [
            path
            for path in run.run_dir.glob("iterations/**/requirements-gather-*.jsonl")
            if "report-repair" not in path.name
        ]
        repair = list(run.run_dir.glob("iterations/**/requirements-gather-report-repair-*.jsonl"))
        return gather, repair

    def greeting_answers(self):
        first = {
            "assistant_reply": self.REPLY,
            "intended_outcome": "A friendly greeting, with no software named to build or change",
            "requirements": [],
            "required_behaviors": [],
            "acceptance_tests": [],
            "open_questions": [],
            "job": "none",
        }
        # The repair keeps the fixture's requested-outcome question so today's code can
        # accept the turn after one repair; after the fix this answer is never requested.
        repair = {
            "summary": "The user has not described a task yet.",
            "assistant_reply": self.REPLY,
            "intended_outcome": "A friendly greeting, with no software named to build or change",
            "requirements": [],
            "required_behaviors": [],
            "acceptance_tests": [],
            "constraints": [],
            "proposed_assumptions": [],
            "ignored_statements": [],
            "source_refs": [],
            "open_questions": [
                {
                    "id": "requested-outcome",
                    "question": "What would you like to build or change?",
                    "why": "No engineering outcome has been requested yet.",
                    "options": [],
                    "proposed_default": "",
                    "kind": "decision",
                    "category": "requested_outcome",
                    "delegable": False,
                }
            ],
            "job": "none",
            "design_document": "",
            "clarity": "clear",
        }
        return first, repair

    def test_ac1_greeting_first_usable_answer_skips_report_repair(self):
        first, repair = self.greeting_answers()
        run, view, _ = self.drive("hi", first, repair)
        self.assertEqual("answer", view["needs"]["kind"], view.get("needs"))
        # The public view trims question categories, so recognize the runner's
        # requested-outcome stop by what it asks the person for.
        self.assertTrue(
            any("build" in question.get("question", "").lower() for question in view["needs"]["questions"]),
            view["needs"]["questions"],
        )
        self.assertIn(
            self.REPLY,
            [row["text"] for row in view["chat"]["messages"] if row["role"] == "assistant"],
            view["chat"]["messages"],
        )
        gather, repaired = self.dispatch_counts(run)
        self.assertEqual(1, len(gather), [str(path) for path in gather])
        self.assertEqual(0, len(repaired), "a usable no-goal first answer must not pay a report repair")

    def test_ac4_small_turn_single_pass_single_launch(self):
        first, repair = self.greeting_answers()
        run, view, invocations = self.drive("hi", first, repair)
        self.assertEqual("answer", view["needs"]["kind"], view.get("needs"))
        self.assertEqual(1, invocations, "one reply must cost one controller invocation")
        gather, repaired = self.dispatch_counts(run)
        self.assertEqual(1, len(gather), [str(path) for path in gather])
        self.assertEqual(0, len(repaired), "one reply must cost one provider dispatch")
        self.assertFalse((run.run_dir / "model-preflight").exists(), "a chat turn must not probe models")
        state = json.loads((run.run_dir / "state.json").read_text())
        intervals = [
            row
            for row in state.get("efficiency_observations") or []
            if (row.get("provenance") or {}).get("phase") == "source_launcher_preparation"
        ]
        self.assertEqual(1, len(intervals), "launch preparation must run once for the reply's single launch")

    def goal_answers(self, first):
        repair = {
            **first,
            "summary": "The greeting CLI gains a --shout option",
            "requirements": [self.REQUIREMENT],
            "required_behaviors": ["With --shout the CLI prints the greeting in upper case"],
            "acceptance_tests": ["Run the greeting CLI with --shout and one name and read the upper-case greeting"],
            "constraints": [],
            "proposed_assumptions": [],
            "ignored_statements": [],
            "source_refs": ["README.md"],
            "open_questions": [],
            "design_document": "",
            "clarity": "clear",
        }
        return first, repair

    def assert_goal_turn_still_repaired(self, first):
        run, view, _ = self.drive(self.GOAL, *self.goal_answers(first))
        self.assertEqual("answer", view["needs"]["kind"], view.get("needs"))
        self.assertTrue(
            view["needs"]["questions"][-1]["id"].startswith("requirements-review-"),
            view["needs"]["questions"],
        )
        self.assertIsNone(view.get("displayed_plan"))
        gather, repaired = self.dispatch_counts(run)
        self.assertEqual(1, len(gather), [str(path) for path in gather])
        self.assertEqual(1, len(repaired), "a goal turn omitting a required field must still be repaired")

    def test_ac2_goal_turn_missing_field_still_repaired(self):
        self.assert_goal_turn_still_repaired(
            {
                "job": "build",
                "intended_outcome": self.OUTCOME,
                "requirements": [self.REQUIREMENT],
                "required_behaviors": [],
                "acceptance_tests": [],
                "open_questions": [],
                "assistant_reply": "I understand you want a --shout option.",
            }
        )

    def test_ac6_goal_turn_no_job_empty_lists_still_repaired(self):
        self.assert_goal_turn_still_repaired(
            {
                "job": "none",
                "intended_outcome": self.OUTCOME,
                "requirements": [],
                "required_behaviors": [],
                "acceptance_tests": [],
                "open_questions": [],
                "assistant_reply": "I understand you want a --shout option.",
            }
        )
