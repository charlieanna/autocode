"""Issue #184 acceptance: a provider-quota pause asks the human, never switches.

Every test runs offline through the real CLI against fake providers
(fake_codex/fake_opencode/fake_parallel_builder plus a recording wrapper that
fails one chosen call with a structured quota error).
"""
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import test_subprocess

TOOLS = Path(__file__).resolve().parents[1] / "tools"
PRE_CHANGE_VIEW_KEYS = (
    "runner_check", "dependency", "schema", "status", "done", "needs", "recovery",
    "verification", "code_checkpoints", "phase", "next_stage", "iteration", "stop_reason",
    "current_task", "workflow", "workflow_source", "workflow_reason", "turn", "evidence",
    "usage", "request_context", "output_transport", "direct_rework_assignments")

# One offline provider wrapper: records every launched model, can fail the
# first call for one stage/milestone with a structured quota error event after
# optionally writing partial files (issue #184's dirty-source class), and
# otherwise delegates to a real fixture provider.
WRAPPER = '''#!/usr/bin/env python3
import json, os, subprocess, sys, time, uuid
from pathlib import Path
DELEGATE = os.environ["QUOTA_DELEGATE"]
if sys.argv[1:] == ["login", "status"] or sys.argv[1:2] == ["--version"]:
    os.execv(sys.executable, [sys.executable, DELEGATE, *sys.argv[1:]])
prompt = sys.stdin.read()
try:
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\\n", 1)[1])
except Exception:
    data = {}
stage = data.get("stage") or ""
milestone = (data.get("current_task") or {}).get("milestone_id") or ""
if os.environ.get("QUOTA_RECORD_MODELS") and "--model" in sys.argv:
    with open(os.environ["QUOTA_RECORD_MODELS"], "a") as stream:
        stream.write(json.dumps({"stage": stage, "milestone": milestone,
                                 "model": sys.argv[sys.argv.index("--model") + 1]}) + "\\n")
fail = os.environ.get("QUOTA_FAIL_ONCE")
if (fail and not Path(fail).exists() and stage == os.environ.get("QUOTA_FAIL_STAGE", "terra")
        and milestone == os.environ.get("QUOTA_FAIL_MILESTONE", milestone)):
    barrier = os.environ.get("AUTOCODE_BUILDER_BARRIER")
    if barrier:
        marker = Path(barrier)
        marker.mkdir(parents=True, exist_ok=True)
        (marker / ("quota-" + milestone)).write_text(str(os.getpid()))
        deadline = time.monotonic() + 10
        while len(list(marker.iterdir())) < 2:
            if time.monotonic() > deadline:
                raise RuntimeError("quota wrapper barrier timed out")
            time.sleep(0.02)
    Path(fail).parent.mkdir(parents=True, exist_ok=True)
    Path(fail).write_text("hit")
    for name, content in json.loads(os.environ.get("QUOTA_FAIL_WRITE", "{}")).items():
        Path(name).parent.mkdir(parents=True, exist_ok=True)
        Path(name).write_text(content)
    print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}), flush=True)
    print(json.dumps({"type": "error", "error": {"message": "subscription usage limit reached"}}), flush=True)
    raise SystemExit(3)
raise SystemExit(subprocess.run([sys.executable, DELEGATE, *sys.argv[1:]],
                                input=prompt, text=True).returncode)
'''


class QuotaPauseFlow(test_subprocess.SubprocessFlow):
    """The builtin OpenCode engine with per-role models and a recording wrapper.

    Only the harness is inherited. SubprocessFlow's scenario tests keep running
    in tests.test_subprocess, on the engine they were written for; re-running
    them under this quota fixture would drive a different configuration (the
    builtin engine, the recording wrapper) and so would not prove unchanged
    behavior. AC6 carries its own no-quota-failure control instead."""

    new_run_engine_args = ()

    for _inherited in (name for name, value in vars(test_subprocess.SubprocessFlow).items()
                       if name.startswith("test_") and callable(value)):
        locals()[_inherited] = None
    del _inherited

    def setUp(self):
        scratch = Path(os.environ.get("AUTOCODE_QUOTA_TEST_TMPDIR",
                                      TOOLS.parent / ".autocode" / "evidence")).resolve()
        scratch.mkdir(parents=True, exist_ok=True)
        with patch.object(tempfile, "tempdir", str(scratch)):
            super().setUp()
        self.env["TMPDIR"] = str(scratch)
        bin_dir = self.root / "fixture-bin"
        shutil.copy2(TOOLS / "fake_opencode.py", bin_dir / "opencode")
        (bin_dir / "opencode").chmod(0o755)
        for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL", "OPENCODE_CONFIG_CONTENT"):
            self.env.pop(key, None)
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        # fake_opencode compares an unset truncation hook with report-repair
        # handoffs lacking a stage; use a nonmatching sentinel for this fixture.
        self.env["AUTOCODE_FIXTURE_TRUNCATE_STAGE"] = "__never_truncate__"
        self.env["QUOTA_DELEGATE"] = str(TOOLS / "fake_codex.py")
        self.env["QUOTA_RECORD_MODELS"] = str(self.root / "models-used.jsonl")
        (bin_dir / "codex").write_text(WRAPPER)
        (bin_dir / "codex").chmod(0o755)
        self.models_file = self.root / "models-used.jsonl"

    def launch(self, args, expected, *, answers=None):
        # The fixture lives beneath the run's evidence directory, so its
        # nested Git/project path makes planning slower than the shared test
        # helper's 60-second subprocess bound. Keep this CLI call bounded.
        if "--run-dir" not in args and "--engine" not in args:
            args = [*self.new_run_engine_args, *args]
        if "--run-dir" not in args and "--in-place" not in args:
            args = [*args, "--in-place"]
        args = test_subprocess.with_resolver_token(args)
        result = subprocess.run([*self.entry, "--workspace", str(self.project), *args],
                                cwd=self.root, env=self.env, input=answers,
                                capture_output=True, text=True, timeout=120)
        self.assertEqual(expected, result.returncode, result.stdout + result.stderr)
        return result

    def launched_models(self):
        if not self.models_file.exists():
            return []
        return [json.loads(line) for line in self.models_file.read_text().splitlines()]

    def pause_at(self, stage, *, models=("--sol-model", "zai-coding-plan/glm-5.3",
                                         "--terra-model", "openai/gpt-6-astra")):
        """Launch to a quota pause at stage; returns (run, saved state)."""
        self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"] = stage
        self.launch(["Build greeting", "--chat", *models], 2, answers="CLI\nyes\n")
        return self.saved()

    def public_request(self, state):
        return state.get("resolver_human_request") or {}

    def quota_question(self, state):
        questions = [q for q in state.get("pending_questions") or []
                     if str(q.get("id", "")).startswith("Q-quota-")]
        self.assertEqual(1, len(questions), (state.get("pending_questions"),
                                             state.get("stop_reason"), state.get("next_stage")))
        return questions[0]

    def proposal_of(self, state):
        public = self.public_request(state)
        entry = ((state.get("resolver") or {}).get("human_escalations") or {}).get(public.get("request_id"))
        return ((entry or {}).get("identity") or {}).get("proposal") or {}

    def advertised_command(self, state):
        reason = state.get("stop_reason") or ""
        self.assertTrue(reason.startswith("PAUSED_BUDGET"), reason)
        tail = reason[reason.index("autocode "):]
        return tail, shlex.split(tail)

    def answer_via_advertised(self, state, replacement):
        """Execute the advertised answer command verbatim with the placeholder replaced."""
        tail, _ = self.advertised_command(state)
        question = self.quota_question(state)
        placeholder = ((state.get("user_request") or {}).get("quota") or {}).get("placeholder", "PROVIDER/MODEL")
        tail = tail.replace(shlex.quote(f"{question['id']}={placeholder}"),
                            shlex.quote(f"{question['id']}={replacement}"))
        parts = shlex.split(tail)
        self.assertEqual("autocode", parts[0])
        result = subprocess.run([sys.executable, str(TOOLS / "autocode.py"), *parts[1:]],
                                cwd=self.root, env=self.env, capture_output=True, text=True, timeout=120)
        return result

    def terra_records(self, state):
        return [row for row in state.get("stages", []) if row.get("stage") == "terra"]

    def sol_records(self, state):
        return [row for row in state.get("stages", []) if row.get("stage") == "sol"]

    def thread_of(self, record):
        for line in Path(record["events"]).read_text().splitlines():
            event = json.loads(line)
            thread = event.get("thread_id") or (event.get("part") or {}).get("sessionID")
            if thread:
                return thread
        return None


class BuiltinQuotaTests(QuotaPauseFlow):
    def test_ac1_quota_pause_asks_and_does_not_switch(self):
        run, state = self.pause_at("sol")
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertEqual("PAUSED_OR_BLOCKED", state["phase"])
        self.assertTrue((state.get("stop_reason") or "").startswith("PAUSED_BUDGET"))
        _, command = self.advertised_command(state)
        self.assertIn("--answer", command)
        self.assertEqual("PAUSED_BUDGET", self.proposal_of(state)["previous_status"])
        question = self.quota_question(state)
        self.assertEqual("Q-quota-sol", question["id"])
        self.assertIn("Tester", question["question"])
        self.assertIn("sol", question["question"])
        self.assertIn("replacement model", question["question"])
        self.assertEqual("zai-coding-plan/glm-5.3",
                         state["settings"]["roles"]["sol"]["model"])
        self.assertEqual(1, len([r for r in self.sol_records(state) if r.get("abandoned")]))

    def test_ac2_stop_reason_advertises_working_answer_command(self):
        run, state = self.pause_at("sol")
        tail, command = self.advertised_command(state)
        question = self.quota_question(state)
        self.assertIn(shlex.quote(str(run)), tail)
        self.assertIn(f"--answer Q-quota-sol=PROVIDER/MODEL", tail)
        self.assertIn(state["resolver_human_request"]["request_token"], tail)
        result = self.answer_via_advertised(state, "openai/gpt-5.6-terra")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        _, answered = self.saved()
        self.assertEqual([], answered["pending_questions"])
        self.assertIn("Q-quota-sol", answered.get("answers", {}))

    def test_ac3_answered_assignment_resumes_on_named_model(self):
        run, state = self.pause_at("sol")
        failed = self.sol_records(state)[-1]
        failed_snapshot = json.dumps(failed, sort_keys=True)
        failed_thread = self.thread_of(failed)
        self.assertEqual(0, self.answer_via_advertised(state, "openai/gpt-5.6-terra").returncode)
        del self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"]
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 0)
        _, resumed = self.saved()
        relaunched = [r for r in self.sol_records(resumed) if not r.get("rejected")]
        self.assertEqual(1, len(relaunched))
        self.assertEqual("openai/gpt-5.6-terra", relaunched[0]["launch_route"]["model"])
        self.assertNotEqual(failed_thread, self.thread_of(relaunched[0]))
        self.assertNotIn("resume", [part for part in json.loads(json.dumps(relaunched[0]))["command"]])
        archived = [r for r in self.sol_records(resumed) if r.get("rejected")]
        self.assertEqual(1, len(archived))
        self.assertEqual(failed_snapshot, json.dumps(archived[0], sort_keys=True))

    def test_ac4_cross_model_assignment_refused_with_guidance(self):
        run, state = self.pause_at("sol", models=("--sol-model", "openai/gpt-6-sol",
                                                  "--terra-model", "zai-coding-plan/glm-5.3"))
        result = self.answer_via_advertised(state, "zai-coding-plan/glm-5.3-flash")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Input rejected", result.stderr)
        for token in ("sol", "terra", "zai-coding-plan/glm-5.3-flash", "zai-coding-plan/glm-5.3"):
            self.assertIn(token, result.stderr)
        _, refused = self.saved()
        self.assertEqual(["Q-quota-sol"], [q["id"] for q in refused["pending_questions"]])
        self.assertEqual("openai/gpt-6-sol", refused["settings"]["roles"]["sol"]["model"])
        self.assertEqual("WAITING_FOR_USER", refused["status"])
        self.assertEqual("PAUSED_OR_BLOCKED", refused["phase"])
        self.assertTrue(refused["stop_reason"].startswith("PAUSED_BUDGET"))

    def test_ac5_assignment_recorded_and_routes_visible(self):
        import autocode_run_view as run_view
        run, state = self.pause_at("sol")
        self.assertEqual(0, self.answer_via_advertised(state, "openai/gpt-5.6-terra").returncode)
        _, answered = self.saved()
        events = [e for e in answered.get("user_events", []) if e.get("kind") == "model_assignment"]
        self.assertEqual(1, len(events))
        event = events[0]
        self.assertEqual("sol", event["role"])
        self.assertEqual("zai-coding-plan/glm-5.3", event["from_model"])
        self.assertEqual("openai/gpt-5.6-terra", event["to_model"])
        self.assertEqual("sol", event["stage"])
        self.assertTrue(event.get("at"))
        projection = run_view.view(answered)
        self.assertEqual("openai/gpt-5.6-terra", projection["role_routes"]["sol"])
        for key in PRE_CHANGE_VIEW_KEYS:
            self.assertIn(key, projection)

    def test_ac6_runs_without_quota_failures_unchanged(self):
        self.launch(["Build greeting", "--chat",
                     "--sol-model", "zai-coding-plan/glm-5.3",
                     "--terra-model", "openai/gpt-6-astra"], 0, answers="CLI\nyes\n")
        _, state = self.saved()
        self.assertEqual("TASK_COMPLETE", state["status"])
        self.assertEqual([], state.get("pending_questions") or [])
        self.assertNotIn("PAUSED_BUDGET", state.get("stop_reason") or "")
        self.assertEqual([], [e for e in state.get("user_events", []) if e.get("kind") == "model_assignment"])
        self.assertTrue(self.terra_records(state) and self.sol_records(state))

    def test_ac7_unanswered_run_stays_paused(self):
        run, state = self.pause_at("sol")
        stages_before = len(state["stages"])
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        _, still = self.saved()
        self.assertEqual("WAITING_FOR_USER", still["status"])
        self.assertEqual("PAUSED_OR_BLOCKED", still["phase"])
        self.assertTrue(still["stop_reason"].startswith("PAUSED_BUDGET"))
        self.assertEqual(["Q-quota-sol"], [q["id"] for q in still["pending_questions"]])
        self.assertEqual(stages_before, len(still["stages"]))

    def test_ac8_malformed_model_answer_refused(self):
        run, state = self.pause_at("sol")
        result = self.answer_via_advertised(state, "not a model")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Input rejected", result.stderr)
        _, refused = self.saved()
        self.assertEqual(["Q-quota-sol"], [q["id"] for q in refused["pending_questions"]])
        self.assertEqual("zai-coding-plan/glm-5.3", refused["settings"]["roles"]["sol"]["model"])
        self.assertEqual([], [e for e in refused.get("user_events", []) if e.get("kind") == "model_assignment"])
        self.assertEqual("WAITING_FOR_USER", refused["status"])
        self.assertEqual("PAUSED_OR_BLOCKED", refused["phase"])

    def test_ac9_second_quota_asks_again_and_keeps_first_event(self):
        run, state = self.pause_at("sol")
        self.assertEqual(0, self.answer_via_advertised(state, "openai/gpt-5.6-terra").returncode)
        # The quota hook stays on: the reassigned Tester fails quota again.
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        _, paused_again = self.saved()
        self.assertEqual("WAITING_FOR_USER", paused_again["status"])
        questions = [q for q in paused_again["pending_questions"] if q["id"] == "Q-quota-sol"]
        self.assertEqual(1, len(questions))
        self.assertIn("sol", questions[0]["question"])
        assignments = [e for e in paused_again.get("user_events", []) if e.get("kind") == "model_assignment"]
        self.assertEqual(1, len(assignments))
        self.assertEqual("openai/gpt-5.6-terra", assignments[0]["to_model"])

    def test_ac10_completion_quota_names_role_and_refuses_producer_model(self):
        run, state = self.pause_at("astra_review", models=("--completion-model", "openai/gpt-6-sol",
                                                           "--terra-model", "zai-coding-plan/glm-5.3",
                                                           "--sol-model", "openai/gpt-6-astra"))
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertTrue(state["stop_reason"].startswith("PAUSED_BUDGET"))
        _, command = self.advertised_command(state)
        self.assertIn("--answer", command)
        self.assertEqual("PAUSED_BUDGET", self.proposal_of(state)["previous_status"])
        question = self.quota_question(state)
        self.assertEqual("Q-quota-completion", question["id"])
        self.assertIn("Completion Reviewer", question["question"])
        result = self.answer_via_advertised(state, "zai-coding-plan/glm-5.3")
        self.assertNotEqual(0, result.returncode)
        for token in ("completion", "terra"):
            self.assertIn(token, result.stderr)
        _, refused = self.saved()
        self.assertEqual(["Q-quota-completion"], [q["id"] for q in refused["pending_questions"]])
        self.assertEqual("openai/gpt-6-sol", refused["settings"]["roles"]["completion"]["model"])

    def test_ac11_replayed_answer_command_refused(self):
        run, state = self.pause_at("sol")
        first = self.answer_via_advertised(state, "openai/gpt-5.6-terra")
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        _, answered = self.saved()
        routes = json.dumps(answered["settings"]["roles"], sort_keys=True)
        events = json.dumps(answered.get("user_events"), sort_keys=True)
        stages_before = len(answered["stages"])
        replay = self.answer_via_advertised(state, "openai/gpt-5.6-terra")
        self.assertNotEqual(0, replay.returncode)
        self.assertIn("Input rejected", replay.stderr)
        _, after = self.saved()
        self.assertEqual(routes, json.dumps(after["settings"]["roles"], sort_keys=True))
        self.assertEqual(events, json.dumps(after.get("user_events"), sort_keys=True))
        self.assertEqual(stages_before, len(after["stages"]))

    def test_ac14_answered_resume_skips_operational_escalation(self):
        run, state = self.pause_at("sol")
        self.assertEqual(0, self.answer_via_advertised(state, "openai/gpt-5.6-terra").returncode)
        _, answered = self.saved()
        self.assertEqual("PAUSED_BUDGET", answered["status"])
        self.assertEqual("PAUSED_OR_BLOCKED", answered["phase"])
        self.assertEqual([], answered["pending_questions"])
        self.assertEqual("model_assignment", answered["user_events"][-1]["kind"])
        ledger_before = set((answered.get("resolver") or {}).get("human_escalations", {}))
        del self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"]
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 0)
        _, resumed = self.saved()
        escalations = (resumed.get("resolver") or {}).get("human_escalations") or {}
        self.assertEqual(ledger_before, set(escalations))
        for entry in escalations.values():
            self.assertNotEqual("operational_exhaustion",
                                (entry.get("identity") or {}).get("proposal", {}).get("scope"))
        self.assertEqual([], resumed["pending_questions"])
        relaunched = [r for r in self.sol_records(resumed) if not r.get("rejected")]
        self.assertEqual(1, len(relaunched))
        self.assertEqual("openai/gpt-5.6-terra", relaunched[0]["launch_route"]["model"])

    def test_ac15_dirty_source_quota_still_asks_and_retains_partial_work(self):
        fixture = self.root / "builder-files.json"
        fixture.write_text(json.dumps({
            "greet.py": "import sys\nif len(sys.argv) != 2 or not sys.argv[1].strip():\n    raise SystemExit(2)\nprint('Hello, ' + sys.argv[1])\n",
            "builder-partial.txt": "partial builder work"}))
        self.env["AUTOCODE_FIXTURE_FILES"] = str(fixture)
        self.env["QUOTA_FAIL_ONCE"] = str(self.root / "quota-hit")
        self.env["QUOTA_FAIL_STAGE"] = "terra"
        self.env["QUOTA_FAIL_MILESTONE"] = "M1"
        self.env["QUOTA_FAIL_WRITE"] = json.dumps({"builder-partial.txt": "partial builder work"})
        self.launch(["Build greeting", "--chat", "--terra-model", "zai-coding-plan/glm-5.3",
                     "--sol-model", "openai/gpt-6-astra",
                     "--completion-model", "openai/gpt-6-astra"], 2, answers="CLI\nyes\n")
        run, state = self.saved()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertEqual("PAUSED_OR_BLOCKED", state["phase"])
        self.assertTrue(state["stop_reason"].startswith("PAUSED_BUDGET"))
        question = self.quota_question(state)
        self.assertEqual("Q-quota-terra", question["id"])
        self.assertIn("Builder", question["question"])
        self.assertEqual("partial builder work", (self.project / "builder-partial.txt").read_text())
        self.assertEqual(1, len(self.terra_records(state)))
        self.assertIn("builder-partial.txt", self.terra_records(state)[0]["changed_files"])
        self.assertEqual(0, self.answer_via_advertised(state, "openai/gpt-6-sol").returncode)
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 0)
        _, resumed = self.saved()
        builders = [r for r in self.terra_records(resumed) if not r.get("rejected")]
        self.assertEqual(1, len(builders))
        self.assertEqual("openai/gpt-6-sol", builders[0]["launch_route"]["model"])
        self.assertEqual("partial builder work", (self.project / "builder-partial.txt").read_text())

    def test_ac16_refusal_uses_recorded_producer_model(self):
        # Wave 1: terra (glm-5.3) fails quota; the assignment moves terra to gpt-6-sol.
        run, state = self.pause_at("terra", models=("--terra-model", "zai-coding-plan/glm-5.3",
                                                    "--sol-model", "openai/gpt-6-astra",
                                                    "--completion-model", "openai/gpt-6-astra"))
        self.assertEqual("zai-coding-plan/glm-5.3",
                         self.terra_records(state)[-1]["launch_route"]["model"])
        self.assertEqual(0, self.answer_via_advertised(state, "openai/gpt-6-sol").returncode)
        # Wave 2: terra fails quota on the assigned model; answer keeps gpt-6-sol.
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        run, second = self.saved()
        self.assertEqual("Q-quota-terra", self.quota_question(second)["id"])
        self.assertEqual(0, self.answer_via_advertised(second, "openai/gpt-6-sol").returncode)
        # Wave 3: terra succeeds, then the Tester pauses on quota.
        self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"] = "sol"
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        run, third = self.saved()
        self.assertEqual("Q-quota-sol", self.quota_question(third)["id"])
        result = self.answer_via_advertised(third, "zai-coding-plan/glm-5.3-flash")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Input rejected", result.stderr)
        self.assertIn("zai-coding-plan/glm-5.3", result.stderr)
        self.assertIn("sol", result.stderr)
        _, refused = self.saved()
        self.assertEqual(["Q-quota-sol"], [q["id"] for q in refused["pending_questions"]])
        self.assertEqual("openai/gpt-6-astra", refused["settings"]["roles"]["sol"]["model"])

    def test_ac17_tracked_file_change_on_quota_still_asks(self):
        (self.project / "builder-partial.txt").write_text("before builder work")
        # The fake Requirements provider cites greet.py as its source_ref when
        # the workspace has tracked source; keep that fixture reference real.
        (self.project / "greet.py").write_text("# initial greeting fixture\n")
        subprocess.run(["git", "-C", str(self.project), "add", "builder-partial.txt", "greet.py"], check=True)
        subprocess.run(["git", "-C", str(self.project), "-c", "user.name=F", "-c", "user.email=f@t",
                        "commit", "-qm", "tracked fixture"], check=True)
        self.env["QUOTA_FAIL_ONCE"] = str(self.root / "quota-hit")
        self.env["QUOTA_FAIL_STAGE"] = "terra"
        self.env["QUOTA_FAIL_MILESTONE"] = "M1"
        self.env["QUOTA_FAIL_WRITE"] = json.dumps({"builder-partial.txt": "partial builder work"})
        self.launch(["Build greeting", "--chat", "--terra-model", "zai-coding-plan/glm-5.3",
                     "--sol-model", "openai/gpt-6-astra"], 2, answers="CLI\nyes\n")
        run, state = self.saved()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        question = self.quota_question(state)
        self.assertEqual("Q-quota-terra", question["id"])
        self.assertIn("Builder", question["question"])
        self.assertEqual("partial builder work", (self.project / "builder-partial.txt").read_text())
        builders = self.terra_records(state)
        self.assertEqual(1, len(builders))
        self.assertIn("builder-partial.txt", builders[0]["changed_files"])
        self.assertEqual([], [r for r in builders if not r.get("rejected")])

    def test_ac20_opencode_bare_model_answer_refused(self):
        run, state = self.pause_at("sol")
        result = self.answer_via_advertised(state, "gpt-6-sol")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Input rejected", result.stderr)
        _, refused = self.saved()
        self.assertEqual(["Q-quota-sol"], [q["id"] for q in refused["pending_questions"]])
        self.assertEqual("zai-coding-plan/glm-5.3", refused["settings"]["roles"]["sol"]["model"])
        self.assertEqual([], [e for e in refused.get("user_events", []) if e.get("kind") == "model_assignment"])


class NativeCodexQuotaTests(QuotaPauseFlow):
    """Native Codex joint planning: bare model names, plan-reviewer quota."""

    joint_args = ("--engine", "codex", "--joint-planning",
                  "--astra-model", "gpt-5.6-terra", "--terra-model", "gpt-5.6-terra",
                  "--sol-model", "gpt-5.6-sol", "--completion-model", "gpt-5.6-sol",
                  "--glm-model", "gpt-5.6-terra", "--plan-reviewer-model", "gpt-5.6-sol")

    def pause_at_plan_reviewer(self):
        self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"] = "astra_challenge"
        self.launch(["Build greeting", "--chat", *self.joint_args], 2, answers="CLI\nyes\n")
        return self.saved()

    def test_ac13_codex_bare_name_answer_accepted(self):
        run, state = self.pause_at_plan_reviewer()
        question = self.quota_question(state)
        self.assertEqual("Q-quota-plan_reviewer", question["id"])
        before = self.launched_models()
        self.assertNotIn("gpt-6-sol", [row["model"] for row in before if row["stage"] == "astra_challenge"])
        result = self.answer_via_advertised(state, "gpt-6-sol")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        _, answered = self.saved()
        self.assertEqual("gpt-6-sol", answered["settings"]["roles"]["plan_reviewer"]["model"])
        self.assertEqual("codex", answered["settings"]["roles"]["plan_reviewer"].get("engine", "codex"))
        del self.env["AUTOCODE_FIXTURE_QUOTA_STAGE"]
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        relaunched = [row for row in self.launched_models()
                      if row["stage"] == "astra_challenge" and row not in before]
        self.assertTrue(relaunched)
        self.assertEqual(["gpt-6-sol"], [row["model"] for row in relaunched])

    def test_ac18_plan_reviewer_refuses_producer_model(self):
        run, state = self.pause_at_plan_reviewer()
        result = self.answer_via_advertised(state, "gpt-5.6-terra")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Input rejected", result.stderr)
        for token in ("plan_reviewer", "glm", "gpt-5.6-terra"):
            self.assertIn(token, result.stderr)
        _, refused = self.saved()
        self.assertEqual(["Q-quota-plan_reviewer"], [q["id"] for q in refused["pending_questions"]])
        self.assertEqual("gpt-5.6-sol", refused["settings"]["roles"]["plan_reviewer"]["model"])

    def test_ac19_codex_provider_model_answer_refused(self):
        run, state = self.pause_at_plan_reviewer()
        result = self.answer_via_advertised(state, "openai/gpt-6-sol")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("Input rejected", result.stderr)
        _, refused = self.saved()
        self.assertEqual(["Q-quota-plan_reviewer"], [q["id"] for q in refused["pending_questions"]])
        self.assertEqual("gpt-5.6-sol", refused["settings"]["roles"]["plan_reviewer"]["model"])
        self.assertEqual("codex", refused["settings"]["roles"]["plan_reviewer"].get("engine", "codex"))
        self.assertEqual([], [e for e in refused.get("user_events", []) if e.get("kind") == "model_assignment"])


class ParallelQuotaTests(QuotaPauseFlow):
    """AC12: a parallel Builder worker's quota pause becomes the parent's question.

    The quota injection (QUOTA_FAIL_ONCE / the worker barrier) lives in the
    codex-side wrapper, so this class drives the codex engine with bare model
    names, the way tests.test_dispatch drives fake_parallel_builder."""

    new_run_engine_args = ("--engine", "codex")

    def setUp(self):
        super().setUp()
        self.env["QUOTA_DELEGATE"] = str(TOOLS / "fake_parallel_builder.py")
        self.env["AUTOCODE_BUILDER_BARRIER"] = str(self.root / "barrier")
        self.env["QUOTA_FAIL_ONCE"] = str(self.root / "quota-hit")
        self.env["QUOTA_FAIL_STAGE"] = "terra"
        self.env["QUOTA_FAIL_MILESTONE"] = "M1"

    def test_ac12_parallel_builder_quota_asks_and_answer_retries_worker(self):
        self.launch(["Produce two outputs and combine", "--chat", "--max-parallel-builders", "2",
                     "--terra-model", "gpt-5.6-terra",
                     "--sol-model", "gpt-5.6-sol",
                     "--completion-model", "gpt-5.6-sol"], 2, answers="yes\n")
        run, state = self.saved()
        self.assertEqual("WAITING_FOR_USER", state["status"])
        self.assertEqual("PAUSED_OR_BLOCKED", state["phase"])
        self.assertTrue(state["stop_reason"].startswith("PAUSED_BUDGET"))
        self.assertEqual("PAUSED_BUDGET", self.proposal_of(state)["previous_status"])
        question = self.quota_question(state)
        self.assertEqual("Q-quota-terra", question["id"])
        self.assertIn("Builder", question["question"])
        self.assertIn("M1", question["question"])
        batch = state["orchestration_batch"]
        failed_row = next(row for row in batch["workers"] if row["milestone_id"] == "M1")
        self.assertNotIn("retry_requested", failed_row)
        child = json.loads((Path(failed_row["run_dir"]) / "state.json").read_text())
        attempts = [row for row in child.get("stages", []) if row.get("stage") == "terra"]
        self.assertEqual(1, len(attempts))
        self.assertTrue(attempts[0].get("abandoned") and attempts[0].get("rejected"))
        self.assertIsNone(child.get("active_stage"))
        # The answered assignment retries exactly that worker on the named model.
        result = self.answer_via_advertised(state, "gpt-6-sol")
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 0)
        _, done = self.saved()
        self.assertEqual("TASK_COMPLETE", done["status"])
        history = done["orchestration_history"][0]
        self.assertEqual("INTEGRATED", history["status"])
        child_done = json.loads((Path(failed_row["run_dir"]) / "state.json").read_text())
        child_attempts = [row for row in child_done.get("stages", []) if row.get("stage") == "terra"]
        self.assertEqual(2, len(child_attempts))
        self.assertEqual(["gpt-5.6-terra", "gpt-6-sol"],
                         [row["launch_route"]["model"] for row in child_attempts])
        self.assertTrue(child_attempts[1].get("accounted"))
        self.assertEqual({"M1", "M2", "M3"},
                         {row["id"] for row in done["milestone_progress"].values()
                          if row.get("accepted") and not str(row["id"]).startswith("batch:")})


if __name__ == "__main__":
    unittest.main()
