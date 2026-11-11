"""The durable Stop-after-current-step operation: AC26-AC29.

Every credited case drives the real public CLI (tools/autocode.py) as a
subprocess with an offline config-tool provider, so no model is ever called.
The provider command wraps tools/fake_command_tool.py (the same offline
fixture tests/test_command_flow.py uses) with a hold: one chosen step blocks
until a release file appears, which keeps a real provider step in flight while
the test submits the durable stop through the supported intervention inbox.
"""

import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))


RUNNER = TOOLS / "autocode.py"
STOP_REASON_MARKER = "Durable stop"

# The provider command: the offline fixture tool, wrapped so one named stage
# blocks on a release file. Its stage log records when each provider step
# starts, when a held step is released, and when its report is delivered, which
# is how the tests prove the in-flight step finished and no later stage ran.
HOLDER = """\
#!/usr/bin/env python3
import json
import os
import sys
import time
from pathlib import Path

WRAP_FLAGS = {"--hold-stage", "--release", "--inflight", "--stage-log"}


def argument(name):
    return Path(sys.argv[sys.argv.index(name) + 1]) if name in sys.argv else None


def option(name):
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else None


def note(log, text):
    with log.open("a") as handle:
        handle.write(text + "\\n")


log = argument("--stage-log")
hold = option("--hold-stage")
release = argument("--release")
inflight = argument("--inflight")
prompt_file = argument("--prompt-file")
stage = ""
if prompt_file and prompt_file.is_file():
    text = prompt_file.read_text()
    if "CURRENT HANDOFF DATA" in text:
        try:
            stage = json.loads(text.split("CURRENT HANDOFF DATA\\n", 1)[1]).get("stage", "")
        except ValueError:
            stage = ""
if log:
    note(log, "start %s %s" % (stage or "?", time.time()))
if hold and stage == hold:
    if inflight:
        inflight.write_text(json.dumps({"stage": stage, "pid": os.getpid()}))
    deadline = time.time() + 300
    while release is None or not release.exists():
        if time.time() > deadline:
            raise SystemExit(3)
        time.sleep(0.05)
    if log:
        note(log, "released %s %s" % (stage, time.time()))
source = sys.argv[1:]
filtered = []
index = 0
while index < len(source):
    if source[index] in WRAP_FLAGS:
        index += 2
        continue
    filtered.append(source[index])
    index += 1
sys.argv = [sys.argv[0]] + filtered
here = Path(__file__).resolve().parent
sys.path.insert(0, str(here))
tool = here / "fake_command_tool.py"
code = compile(tool.read_text(), str(tool), "exec")
try:
    exec(code, {"__name__": "__main__", "__file__": str(tool)})
finally:
    if log:
        note(log, "done %s %s" % (stage or "?", time.time()))
"""

PROVIDER_TEMPLATE = """\
name = "stopholder"
command = ["stop-holder", "--report", "{report}", "--sandbox", "{sandbox}", "--model", "{model}",
           "--role", "{role}", "--prompt-file", "{prompt_file}", "--stage-log", "{run_dir}/stage-log.txt",
           "--hold-stage", "{hold_stage}", "--release", "{run_dir}/release.txt",
           "--inflight", "{run_dir}/inflight.json"]
prompt = "file"
models = ["fixture-reviewer", "fixture-builder", "fixture-validator", "fixture-completion",
          "fixture-planner", "fixture-plan-reviewer"]
version_command = ["stop-holder", "--version"]

[roles]
astra = { model = "fixture-reviewer", effort = "high" }
terra = { model = "fixture-builder", effort = "medium" }
sol = { model = "fixture-validator", effort = "high" }
completion = { model = "fixture-completion", effort = "medium" }
glm = { model = "fixture-planner", effort = "medium" }
plan_reviewer = { model = "fixture-plan-reviewer", effort = "high" }
"""


class StopFixture(unittest.TestCase):
    """A git project, the offline hold provider, and CLI launch helpers."""

    hold_stage = "requirements_gather"

    def setUp(self):
        temp = tempfile.TemporaryDirectory(dir=Path.cwd(), prefix=".tmp-")  # git-ignored (#668)
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.project = self.make_project("project")
        self.bin = self.root / "bin"
        self.bin.mkdir()
        holder = self.bin / "stop-holder"
        holder.write_text(HOLDER)
        holder.chmod(0o755)
        shutil.copy2(TOOLS / "fake_command_tool.py", self.bin / "fake_command_tool.py")
        shutil.copy2(TOOLS / "goal_fixtures.py", self.bin / "goal_fixtures.py")
        self.config = self.root / "config"
        (self.config / "autocode" / "providers").mkdir(parents=True)
        self.write_provider()
        self.env = {
            **os.environ,
            "PATH": str(self.bin) + os.pathsep + os.environ["PATH"],
            "PYTHONDONTWRITEBYTECODE": "1",
            "XDG_CONFIG_HOME": str(self.config),
            "AUTOCODE_HOME": str(self.root / "registry-home"),
        }
        self._outputs = []

    def make_project(self, name):
        project = self.root / name
        project.mkdir()
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=s@example.test",
                "commit",
                "--allow-empty",
                "-qm",
                "fixture",
            ],
            check=True,
        )
        return project

    def write_provider(self):
        (self.config / "autocode" / "providers" / "stopholder.toml").write_text(
            PROVIDER_TEMPLATE.replace("{hold_stage}", self.hold_stage)
        )

    # -- process helpers -------------------------------------------------
    def launch(self, goal="Build a greeting tool"):
        """Start one CLI run in the primary project; the hold stage blocks until release."""
        return self.launch_in(self.project, goal)

    def launch_in(self, project, goal="Build a greeting tool"):
        self.write_provider()
        stdin = self.root / f"chat-input-{time.monotonic()}.txt"
        stdin.write_text("CLI\nyes\n")
        stdout = self.root / f"run-{time.monotonic()}.out"
        stderr = self.root / f"run-{time.monotonic()}.err"
        self._outputs.append((stdout, stderr))
        stdin_handle = stdin.open("r")
        stdout_handle = stdout.open("w")
        stderr_handle = stderr.open("w")
        for handle in (stdin_handle, stdout_handle, stderr_handle):
            self.addCleanup(handle.close)
        process = subprocess.Popen(
            [
                sys.executable,
                str(RUNNER),
                "--provider",
                "stopholder",
                "--workspace",
                str(project),
                "--in-place",
                "--chat",
                goal,
            ],
            cwd=self.root,
            env=self.env,
            stdin=stdin_handle,
            stdout=stdout_handle,
            stderr=stderr_handle,
            start_new_session=True,
            text=True,
        )
        self.addCleanup(self.terminate, process)
        return process

    @staticmethod
    def terminate(process):
        if process.poll() is None:
            process.kill()
            process.wait(timeout=30)

    def output(self):
        return "\n".join(out.read_text() + err.read_text() for out, err in self._outputs)

    def wait_for(self, path, timeout=240, what="file"):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if Path(path).exists():
                return Path(path)
            time.sleep(0.05)
        self.fail(f"Timed out waiting for {what}: {path}")

    def run_dir(self, project=None):
        runs = (project or self.project) / ".autocode" / "runs"
        entries = [entry for entry in runs.iterdir()] if runs.is_dir() else []
        self.assertEqual(1, len(entries), f"expected exactly one run, found {entries}")
        return entries[0]

    def wait_run_dir(self, project=None):
        runs = (project or self.project) / ".autocode" / "runs"
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            if runs.is_dir():
                entries = [entry for entry in runs.iterdir() if (entry / "state.json").exists()]
                if len(entries) == 1:
                    return entries[0]
            time.sleep(0.05)
        self.fail(f"Timed out waiting for the run directory under {runs}")

    def cli(self, *args, **kwargs):
        return self.cli_in(self.project, self.run_dir(), *args, **kwargs)

    def cli_in(self, project, run, *args, **kwargs):
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "--provider",
                "stopholder",
                "--workspace",
                str(project),
                "--run-dir",
                str(run),
                *args,
            ],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=kwargs.get("timeout", 180),
        )

    def submit(self, request_id, kind="stop", text=""):
        return self.submit_in(self.project, self.run_dir(), request_id, kind, text)

    def submit_in(self, project, run, request_id, kind="stop", text=""):
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                "intervention",
                "submit",
                "--workspace",
                str(project),
                "--run-dir",
                str(run),
                "--request-id",
                request_id,
                "--kind",
                kind,
                "--text",
                text,
                "--json",
            ],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=60,
        )

    def state(self, project=None, run=None):
        return json.loads(((run or self.run_dir(project)) / "state.json").read_text())

    def stage_log(self, run=None):
        path = (run or self.run_dir()) / "stage-log.txt"
        return path.read_text().splitlines() if path.exists() else []

    def release(self, run=None):
        ((run or self.run_dir()) / "release.txt").write_text("go")

    def applied_stop_receipts(self, state):
        return [
            item
            for item in state.get("applied_interventions", [])
            if isinstance(item, dict) and item.get("kind") == "stop"
        ]

    def pending_inbox(self, run=None):
        path = (run or self.run_dir()) / "interventions.json"
        if not path.exists():
            return []
        return json.loads(path.read_text())["requests"]

    def started_stages(self, run=None):
        return [line.split()[1] for line in self.stage_log(run) if line.startswith("start ")]

    # -- shared scenarios ------------------------------------------------
    def stop_during_hold(self, kind="stop", request_id="s1", submitter=None):
        """Run to the held step, submit the durable request, then release it."""
        process = self.launch()
        run = self.wait_run_dir()
        self.wait_for(run / "inflight.json", what="in-flight held step")
        if submitter is not None:
            submitter(request_id)
        else:
            result = self.submit(request_id, kind=kind)
            self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.release(run)
        process.wait(timeout=300)
        return process, run

    def assert_stopped_boundary(self, state, request_id, statuses=("PAUSED_INTERVENTION",)):
        """The stop boundary: the existing PAUSED_INTERVENTION boundary status,
        never a new one, with the applied receipt, the stop-specific reason and
        no completion. The mid-flight boundary and the reconciled crash-restart
        boundary are both exactly PAUSED_INTERVENTION."""
        self.assertIn(state["status"], statuses, "the applied stop keeps a pre-existing stopped-family status")
        self.assertIn(STOP_REASON_MARKER, state.get("stop_reason", ""))
        receipts = self.applied_stop_receipts(state)
        self.assertEqual(
            [request_id], [item["id"] for item in receipts], f"exactly one applied stop receipt for {request_id}"
        )
        self.assertIsNone(receipts[0].get("resumed_at"), "a stop receipt is never stamped resumed")
        self.assertEqual([request_id], (state.get("stop_intent") or {}).get("request_ids"))
        self.assertNotIn("completed_at", state, "a stopped run is never marked complete")


class StopOperationTests(StopFixture):
    def test_ac26_stop_finishes_inflight_step_prevents_subsequent_stages(self):
        process, run = self.stop_during_hold()
        self.assertEqual(2, process.returncode, "the stopped run exits non-zero\n" + self.output())
        state = self.state()
        self.assert_stopped_boundary(state, "s1")
        log = self.stage_log(run)
        self.assertTrue(
            any(line.startswith("done requirements_gather") for line in log),
            f"the in-flight step finished and delivered its result after the stop: {log}",
        )
        self.assertFalse(state.get("active_stage"), "no step is left in flight")
        stages = [record.get("stage") for record in state.get("stages", [])]
        self.assertIn("requirements_gather", stages, "the finished step's result was saved")
        started = self.started_stages(run)
        self.assertIn("requirements_gather", started)
        self.assertEqual(
            [], started[started.index("requirements_gather") + 1 :], f"no stage after the held one was launched: {log}"
        )
        before = len(log)
        relaunched = self.cli("--no-chat")
        self.assertEqual(2, relaunched.returncode, "a stopped run relaunch is refused")
        self.assertIn(
            state["stop_reason"], relaunched.stdout + relaunched.stderr, "the refusal repeats the saved stop reason"
        )
        self.assertEqual(before, len(self.stage_log(run)), "no new provider stage was launched after the stop")

    def test_ac27_stop_idempotent_crash_reconciled_never_complete(self):
        process = self.launch()
        run = self.wait_run_dir()
        self.wait_for(run / "inflight.json", what="in-flight held step")
        first = self.submit("s1")
        self.assertEqual(0, first.returncode, first.stdout + first.stderr)
        original = json.loads(first.stdout)["receipt"]
        retry = self.submit("s1")
        self.assertEqual(0, retry.returncode, retry.stdout + retry.stderr)
        payload = json.loads(retry.stdout)
        self.assertTrue(payload["idempotent"], f"the identical retry is idempotent: {payload}")
        self.assertEqual(original, payload["receipt"], "the retry returns the original receipt")
        self.assertEqual(
            ["s1"], [item["id"] for item in self.pending_inbox(run)], "the retry recorded no second request"
        )
        # Crash the owner while the request is recorded and the step is in flight.
        # The provider runs in its own process group, so a host-style crash takes
        # both, exactly like a killed machine: no reaping, no graceful stop.
        fixture_pid = json.loads((run / "inflight.json").read_text())["pid"]
        os.killpg(process.pid, signal.SIGKILL)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(fixture_pid, signal.SIGKILL)
        process.wait(timeout=30)
        self.assertNotEqual(0, process.returncode, "the owner process crashed")
        crashed = self.state()
        self.assertTrue(crashed.get("active_stage"), "the crashed attempt is retained for reconciliation")
        self.assertEqual(
            ["s1"], [item["id"] for item in self.pending_inbox(run)], "the recorded stop survives the crash unconsumed"
        )
        self.assertEqual([], self.applied_stop_receipts(crashed), "the stop is not yet applied")
        crashed_stage_log = len(self.stage_log(run))
        # Restart once: the crashed attempt is reconciled without inventing its
        # completion, and the recorded stop is consumed and applied exactly once
        # at that saved boundary before the invocation exits. No manual
        # abandon/resume recovery may be needed to make the stop authoritative.
        restart = self.cli("--no-chat")
        self.assertNotEqual(0, restart.returncode, "the first restart does not resume work")
        self.assertIn(
            STOP_REASON_MARKER,
            restart.stdout + restart.stderr,
            "the first restart applies the recorded stop and reports its reason",
        )
        state = self.state()
        self.assert_stopped_boundary(state, "s1")
        self.assertEqual(
            [], self.pending_inbox(run), "the first restart consumed the recorded stop into the authoritative state"
        )
        self.assertTrue(
            state.get("active_stage"),
            "the crashed attempt's uncertain evidence is retained; completion is never invented",
        )
        self.assertEqual(
            crashed_stage_log, len(self.stage_log(run)), f"the first restart launched no stage: {self.stage_log(run)}"
        )
        self.assertNotIn(
            "draft_plan", self.started_stages(run), f"no stage launched after the crash: {self.stage_log(run)}"
        )
        resumed = self.cli("--no-chat", "--resume-paused", timeout=120)
        self.assertNotEqual(0, resumed.returncode, "a stopped run cannot resume")
        self.assertIn(
            state["stop_reason"], resumed.stdout + resumed.stderr, "the resume refusal repeats the saved stop reason"
        )
        final = self.submit("s1")
        self.assertEqual(0, final.returncode, final.stdout + final.stderr)
        payload = json.loads(final.stdout)
        self.assertTrue(payload["idempotent"])
        self.assertEqual(
            "already_applied",
            payload["consumer"],
            "the retry after reconciliation returns the original applied receipt",
        )
        self.assertEqual(1, len(self.applied_stop_receipts(self.state())), "the stop was reconciled exactly once")
        self.assertNotEqual("TASK_COMPLETE", self.state()["status"])

    def test_ac28_stop_distinct_from_pause_no_immediate_kill(self):
        self.hold_stage = "builder"
        self.write_provider()
        saved_history = {}

        def snapshot_history_then_submit(request_id):
            # Snapshot the persisted conversation-history records while the
            # worker is healthy and mid-step, immediately before the stop is
            # submitted, so preservation is proven against what was on disk.
            persisted = self.state()
            saved_history["progress_messages"] = list(persisted.get("progress_messages") or [])
            submitted = self.submit(request_id)
            self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)

        process, run = self.stop_during_hold(submitter=snapshot_history_then_submit)
        self.assertEqual(2, process.returncode, "the stopped run exits non-zero\n" + self.output())
        state = self.state()
        self.assert_stopped_boundary(state, "s1")
        history_before = saved_history["progress_messages"]
        self.assertTrue(history_before, "the conversation had saved transcript history before the stop")
        history_after = list(state.get("progress_messages") or [])
        self.assertEqual(
            history_before,
            history_after[: len(history_before)],
            "every saved conversation-history entry survives the applied stop",
        )
        log = self.stage_log(run)
        releases = [line for line in log if line.startswith("released builder")]
        dones = [line for line in log if line.startswith("done builder")]
        self.assertTrue(
            releases and dones and float(dones[0].split()[-1]) >= float(releases[0].split()[-1]),
            f"the in-flight worker was not killed: it delivered after the stop was submitted: {log}",
        )
        self.assertTrue(
            (self.project / "greet.py").exists(), "the finished step's partial work is preserved in the project"
        )
        stages = [record.get("stage") for record in state.get("stages", [])]
        self.assertIn("builder", stages, "the Builder result was saved")
        self.assertNotIn("tester", stages, "the stopped run never reached the next stage")
        self.assertNotIn("tester", self.started_stages(run), f"no worker for a later stage was launched: {log}")
        before = len(log)
        resumed = self.cli("--no-chat", "--resume-paused", timeout=180)
        self.assertEqual(2, resumed.returncode, "a stopped run does not resume the way a paused run does")
        self.assertIn(
            state["stop_reason"], resumed.stdout + resumed.stderr, "the resume refusal repeats the saved stop reason"
        )
        self.assertEqual(before, len(self.stage_log(run)), "the refused resume launched no stage")
        refused = self.state()
        self.assertEqual(
            history_before,
            list(refused.get("progress_messages") or [])[: len(history_before)],
            "the refused resume leaves the saved conversation history intact",
        )
        self.assertIsNone(
            self.applied_stop_receipts(refused)[0].get("resumed_at"), "the stop receipt never acknowledges a resume"
        )

        # Pause keeps its distinct resumable after-current-step semantics.
        paused_project = self.make_project("paused-twin")
        twin = self.launch_in(paused_project)
        twin_run = self.wait_run_dir(paused_project)
        self.wait_for(twin_run / "inflight.json", what="in-flight held step")
        submitted = self.submit_in(paused_project, twin_run, "p1", kind="pause")
        self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
        self.release(twin_run)
        twin.wait(timeout=300)
        self.assertEqual(2, twin.returncode, self.output())
        paused_state = self.state(paused_project, twin_run)
        self.assertEqual("PAUSED_INTERVENTION", paused_state["status"])
        self.assertIn("Queued pause was applied", paused_state["stop_reason"])
        pause_receipt = next(item for item in paused_state["applied_interventions"] if item["kind"] == "pause")
        self.assertIsNone(pause_receipt.get("resumed_at"), "the pause receipt is resumable, not terminal")
        self.assertEqual([], self.applied_stop_receipts(paused_state), "a pause is not a stop")
        finished_at = float(
            next(line for line in self.stage_log(twin_run) if line.startswith("done builder")).split()[-1]
        )
        resumed_twin = self.cli_in(paused_project, twin_run, "--no-chat", "--resume-paused", timeout=300)
        resumed_state = self.state(paused_project, twin_run)
        pause_receipt = next(item for item in resumed_state["applied_interventions"] if item["kind"] == "pause")
        self.assertTrue(pause_receipt.get("resumed_at"), "the pause receipt acknowledges its resume")
        later = [
            line
            for line in self.stage_log(twin_run)
            if line.startswith("start ") and float(line.split()[-1]) > finished_at
        ]
        self.assertTrue(later, f"the resumed pause launched its next stage: {self.stage_log(twin_run)}")
        self.assertEqual([], self.applied_stop_receipts(resumed_state), "no stop stands in the paused twin")
        self.assertEqual(
            "TASK_COMPLETE",
            resumed_state["status"],
            f"the paused twin resumed all the way to completion: {resumed_twin.stderr}",
        )

    def test_ac29_user_actions_refused_after_applied_stop(self):
        process, run = self.stop_during_hold()
        self.assertEqual(2, process.returncode, self.output())
        state = self.state()
        self.assert_stopped_boundary(state, "s1")
        questions = ((state.get("requirements_handoff") or {}).get("report") or {}).get("open_questions") or []
        self.assertTrue(
            any(item.get("id") == "Q1" for item in questions), "the stopped run retains its unanswered question Q1"
        )
        self.assertFalse((state.get("answers") or {}).get("Q1"), "Q1 remains unanswered")
        # This stop precedes any real plan. A separate public-CLI subcase below
        # obtains a genuinely displayed plan token; no checkpoint is synthesized.
        # No human-review request stands in this stopped run, so it displays no
        # review token (goals.review_token stays None until a contract is
        # approved); the review-token flags keep a placeholder because the
        # applied-Stop refusal fires before any review-token validation, and a
        # token complaint could never repeat the saved stop reason asserted below.
        self.assertFalse(state.get("displayed_review"), "the stopped run displays no review token")
        goal_file = self.root / "edited-goal.json"
        goal_file.write_text(json.dumps({"intended_outcome": "Edited"}))
        commands = [
            ["--answer", "Q1=ok"],
            ["--delegate", "Q1"],
            ["--feedback", "go on"],
            ["--follow-up", "later"],
            ["--approve-goal", "r1:not-displayed"],
            ["--approve-review", "C1"],
            ["--reconcile-review", "C1=A1", "--review-token", "r1:token"],
            ["--edit-goal", str(goal_file)],
            ["--accept-completion"],
            ["--resume-paused"],
            ["--no-chat"],
            ["--delegate-all", "--review-token", "r1:token"],
            ["--reject-assumption", "A1", "--review-token", "r1:token"],
            ["--resume-paused", "--retry-builder", "M1"],
            ["--resume-paused", "--retry-failed-stage"],
            ["--resume-paused", "--retry-report", "001/terra-01"],
            ["--resume-paused", "--grant-recovery", "1"],
            ["--resume-paused", "--diagnose-failed-stage"],
            ["--resume-paused", "--accept-transport-change"],
            ["--abandon-stage", "001/terra-01"],
        ]
        baseline_log = len(self.stage_log(run))
        for command in commands:
            result = self.cli(*command, timeout=120)
            self.assertNotEqual(0, result.returncode, f"the stopped run refuses {command[0]}")
            self.assertIn(
                state["stop_reason"],
                result.stdout + result.stderr,
                f"the {command[0]} refusal repeats the saved stop reason:\n{result.stdout}",
            )
            current = self.state()
            self.assertEqual("PAUSED_INTERVENTION", current["status"], f"{command[0]} retained the stopped status")
            self.assertNotIn("completed_at", current, f"{command[0]} did not complete the run")
            self.assertEqual(baseline_log, len(self.stage_log(run)), f"{command[0]} launched no stage")
            self.assertEqual(
                ["s1"],
                [item["id"] for item in self.applied_stop_receipts(current)],
                f"{command[0]} left exactly one applied stop",
            )
        # Later durable feedback and pause receipts never lift the applied stop.
        for kind, request_id, text in (("feedback", "later-feedback", "go on"), ("pause", "later-pause", "")):
            submitted = self.submit(request_id, kind=kind, text=text)
            self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
        stalled = self.state()
        self.assertEqual(
            ["s1"],
            [item["id"] for item in self.applied_stop_receipts(stalled)],
            "later requests cannot supersede the applied stop",
        )
        refused = self.cli("--no-chat", "--resume-paused", timeout=120)
        self.assertEqual(2, refused.returncode)
        self.assertIn(stalled["stop_reason"], refused.stdout + refused.stderr)
        after = self.state()
        self.assertEqual(["s1"], [item["id"] for item in self.applied_stop_receipts(after)])
        self.assertIsNone(self.applied_stop_receipts(after)[0].get("resumed_at"))
        # Controller re-entry: the admission funnel every stage launch passes
        # through refuses while the applied stop stands.
        import autocode as runner
        import autocode_stop as stop_policy

        probe = json.loads(json.dumps(after))
        with self.assertRaises(stop_policy.util.Paused) as raised:
            runner.consume_interventions(probe, run, self.project)
        self.assertEqual(stop_policy.STOP_STATUS, raised.exception.status)
        self.assertIn(STOP_REASON_MARKER, str(raised.exception))

        # Approval after Stop must also fail with a real, current, displayed token.
        from autocode_taskrun import TaskRun

        self.hold_stage = ""
        self.write_provider()
        project = self.make_project("stopped-at-plan")
        planned = TaskRun.start(
            project,
            "Build a greeting tool",
            options=("--provider", "stopholder"),
            command=(sys.executable, str(RUNNER)),
            env=self.env,
            timeout=180,
        )
        need = planned.status()["needs"]
        self.assertEqual("answer", need["kind"], need)
        planned.answer("Q1", "CLI", resolver_token=need["resolver_token"])
        view = planned.advance_until_input()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        token = view["needs"]["token"]
        submitted = self.submit_in(project, planned.run_dir, "stop-at-plan")
        self.assertEqual(0, submitted.returncode, submitted.stdout + submitted.stderr)
        stopped = planned.advance()
        self.assertIn(STOP_REASON_MARKER, stopped["stop_reason"])
        count = len(self.stage_log(planned.run_dir))
        refused = self.cli_in(project, planned.run_dir, "--approve-goal", token)
        self.assertEqual(2, refused.returncode)
        self.assertIn(stopped["stop_reason"], refused.stdout + refused.stderr)
        self.assertEqual(count, len(self.stage_log(planned.run_dir)), "Approval after Stop launches no stage")
        self.assertFalse(planned.status()["done"])
        self.assertFalse((project / "greet.py").exists(), "Approval after Stop cannot start implementation")


class StopPersistenceTests(unittest.TestCase):
    def test_applied_stop_uses_checkpoint_writer_without_proposal_normalization(self):
        from unittest.mock import Mock

        import autocode_stop as stop_policy

        ordinary, checkpoint = Mock(), Mock()
        writer = stop_policy.state_writer(ordinary, checkpoint)
        state = {
            "status": "RESOLVER_PENDING",
            "stop_reason": "Internal proposal",
            "applied_interventions": [{"kind": "stop", "id": "s"}],
            "human_request_proposal": {"question": "retained"},
            "completed_at": "incorrect",
        }
        writer(Path("fixture/state.json"), state)
        checkpoint.assert_called_once_with(Path("fixture/state.json"), state)
        ordinary.assert_not_called()
        self.assertEqual("PAUSED_INTERVENTION", state["status"])
        self.assertEqual(stop_policy.STOP_REASON, state["stop_reason"])
        self.assertEqual({"question": "retained"}, state["human_request_proposal"])
        self.assertNotIn("completed_at", state)
        writer(Path("fixture/report.json"), state)
        ordinary.assert_called_once_with(Path("fixture/report.json"), state)

    def test_a_run_autocode_ends_itself_records_the_outcome_and_stays_stopped(self):
        # #2485: when AutoCode cannot continue, it ends the run with a finished, failed outcome of its
        # own, as a stop receipt, so every rule that keeps a person's stop in force keeps this one.
        import autocode_stop as stop_policy

        state = {"status": "PAUSED_NO_PROGRESS", "stop_reason": "No causal progress", "completed_at": "incorrect"}
        evidence = {"incident_id": "abc", "packet": {"path": "recovery/1.json", "sha256": "0" * 64}}
        stop_policy.end(state, "the sol check kept failing: Missing summary.", evidence, at="2026-10-30T10:00:00Z")
        (receipt,) = state["applied_interventions"]
        self.assertEqual(
            ("stop", "autocode", "2026-10-30T10:00:00Z"), (receipt["kind"], receipt["actor"], receipt["applied_at"])
        )
        self.assertEqual(
            {
                "result": "could_not_finish",
                "reason": "the sol check kept failing: Missing summary.",
                "evidence": evidence,
            },
            receipt["outcome"],
        )
        self.assertEqual(receipt["outcome"], stop_policy.outcome(state))
        self.assertIs(receipt, stop_policy.applied_stop(state))
        self.assertEqual("PAUSED_INTERVENTION", state["status"])
        self.assertEqual(
            "AutoCode could not finish this task: the sol check kept failing: Missing summary. "
            "This run launches no further stages.",
            state["stop_reason"],
        )
        self.assertEqual(state["stop_reason"], stop_policy.stop_reason(state))
        self.assertNotIn("completed_at", state)
        self.assertEqual([receipt["id"]], state["stop_intent"]["request_ids"])
        # Ending again changes nothing, and re-asserting the stop keeps the outcome's reason.
        stop_policy.end(state, "another reason.", {}, at="2026-10-30T11:00:00Z")
        self.assertEqual([receipt], state["applied_interventions"])
        state["stop_reason"] = "drifted"
        self.assertTrue(stop_policy.assert_stopped(state))
        self.assertIn("Missing summary", state["stop_reason"])
        # A person's own stop keeps its generic reason and reports no outcome.
        person = {"status": "RUNNING", "applied_interventions": [{"kind": "stop", "id": "s", "text": ""}]}
        self.assertIsNone(stop_policy.outcome(person))
        self.assertEqual(stop_policy.STOP_REASON, stop_policy.stop_reason(person))

    def test_boundary_commit_carries_the_runner_outcome_a_person_stop_refuses(self):
        # #2597: the Builder classification apply that decides the pause applies the runner's
        # own end inside the saved-step candidate. That boundary commit is how the ended
        # state becomes durable, so consume must not refuse the receipt the candidate
        # itself applies. A person's stop still refuses every later boundary (ac26).
        import shutil
        import tempfile

        import autocode_stop as stop_policy
        from autocode_util import Paused

        base = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, base, True)
        (base / "ws").mkdir()
        (base / "run").mkdir()
        writes = []

        def record(path, value):
            writes.append(path)

        ended = {"status": "RUNNING"}
        stop_policy.end(
            ended,
            "Implementation remains blocked after the configured retries.",
            {"attempt": 6},
            at="2026-10-30T10:00:00Z",
        )
        self.assertFalse(
            stop_policy.consume(ended, base / "run", base / "ws", write_json=record, now=lambda: "2026-10-30T10:01:00Z")
        )
        self.assertEqual("PAUSED_INTERVENTION", ended["status"])
        self.assertIn("Implementation remains blocked", ended["stop_reason"])
        person = {"status": "RUNNING", "applied_interventions": [{"kind": "stop", "id": "s", "text": ""}]}
        with self.assertRaises(Paused):
            stop_policy.consume(
                person, base / "run", base / "ws", write_json=record, now=lambda: "2026-10-30T10:01:00Z"
            )
        self.assertEqual("PAUSED_INTERVENTION", person["status"])
        self.assertEqual(stop_policy.STOP_REASON, person["stop_reason"])


if __name__ == "__main__":
    unittest.main()
