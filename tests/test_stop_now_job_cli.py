"""An intentional Writer Stop now keeps its partial work and resumes the same job.

The stock scenario uses the real public Requirements/plan/approval flow. Only the
copied fake provider is instrumented: its first acceptance Writer writes known
partial test bytes and waits on a FIFO. Stop now must collect that actual child,
restore its pinned original source, retain the unverified bytes and consume the
pause once. A public resume runs a fresh Writer; --pause-after-stage bounds the
fixture before Builder without changing routes or weakening any evidence gate.
"""

import dataclasses
import hashlib
import json
import os
import select
import signal
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import autocode_client_actions as actions
import autocode_taskrun as taskrun
import psutil

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance

CASES = {
    "C1": "test: test_c1_move_transfers_units",
    "C2": "test: test_c2_remove_takes_units_and_drops_empty_holdings",
    "C3": "test: test_c3_move_more_than_on_hand_is_refused",
}
STAGE = "write_acceptance_tests"
PARTIAL = b"# Unfinished acceptance Writer output: not accepted test evidence.\n"


@unittest.skipUnless(sys.platform == "linux", "supervised fake providers require supported Linux")
class StopNowJobCliTests(unittest.TestCase):
    def setUp(self):
        artifacts = os.environ.get("BUILD_AUDIT_ARTIFACTS")
        if artifacts:
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix="writer-stop-now-", dir=artifacts)).resolve()
        else:
            temporary = tempfile.TemporaryDirectory(prefix="writer-stop-now-")
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name).resolve()
        loaded = catalog.load("feature-stock-refusals")
        scenario = dataclasses.replace(loaded, fake_fault="", fake_criteria=dict(CASES))
        self.project = without_maintenance(materialize(scenario.seed, self.root / "project"))
        for key, value in (("user.name", "Codex agent review"), ("user.email", "agent@autocode.invalid")):
            subprocess.run(["git", "-C", str(self.project), "config", key, value], check=True)
        self.flags, environment = fake_setup(scenario, self.root, scenario.dir / "reference")
        paths = environment["PATH"].split(os.pathsep)
        self.env = {
            **os.environ,
            **environment,
            "PATH": os.pathsep.join((paths[0], str(Path(sys.executable).parent), *paths[1:])),
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.brief = scenario.brief
        self.entry = (sys.executable, str(Path(__file__).resolve().parents[1] / "tools/autocode.py"))
        self.read_number = 0
        self.ready, self.gate = self.root / "writer-ready", self.root / "writer-gate"
        self.released, self.completed = self.root / "writer-released", self.root / "held-writer-completed"
        self.trace = self.root / "provider-stages.jsonl"
        for path in (self.ready, self.gate):
            os.mkfifo(path)
        self.ready_fd = os.open(self.ready, os.O_RDWR | os.O_NONBLOCK)
        self.gate_fd = os.open(self.gate, os.O_RDWR | os.O_NONBLOCK)
        self.addCleanup(os.close, self.ready_fd)
        self.addCleanup(os.close, self.gate_fd)
        provider = self.root / "bin/codex"
        marker = "def report_for(stage: str, data: dict) -> dict:\n"
        wrapper = textwrap.dedent(f"""
            original_acceptance_tests = acceptance_tests
            def acceptance_tests(data):
                if not Path({str(self.released)!r}).exists():
                    partial = Path('tests/test_stock.py')
                    partial.write_bytes({PARTIAL!r})
                    with Path({str(self.ready)!r}).open('w') as stream:
                        stream.write(json.dumps({{'pid': os.getpid(), 'cwd': str(Path.cwd()),
                                                  'path': str(partial.resolve())}}) + '\\n')
                    with Path({str(self.gate)!r}).open('rb') as stream:
                        stream.read(1)
                    Path({str(self.completed)!r}).write_text('the held Writer completed normally')
                return original_acceptance_tests(data)

        """)
        trace = (
            f"    with Path({str(self.trace)!r}).open('a') as stream:\n"
            "        stream.write(json.dumps({'stage': stage, 'pid': os.getpid()}) + '\\n')\n"
        )
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, wrapper + marker + trace))

    def public(self, *words, codes=(0,), env=None):
        result = subprocess.run(
            [*self.entry, *words],
            cwd=self.root,
            env={**self.env, **(env or {})},
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.read_number += 1
        (self.root / f"public-cli-{self.read_number:02d}.json").write_text(
            json.dumps(
                {"argv": list(words), "exit_code": result.returncode, "stdout": result.stdout, "stderr": result.stderr},
                indent=2,
            )
            + "\n"
        )
        self.assertIn(result.returncode, codes, result.stdout + result.stderr)
        return result

    def status(self, run):
        return json.loads(
            self.public("--workspace", str(run.workspace), "--run-dir", str(run.run_dir), "--status").stdout
        )

    def planned_run(self):
        run = taskrun.TaskRun.start(
            self.project,
            self.brief,
            command=self.entry,
            options=(
                *self.flags,
                "--no-adaptive-planning",
                "--spec-first",
                "--pause-after-stage",
                "--max-seconds",
                "900",
                "--max-stage-seconds",
                "180",
                "--max-cost-usd",
                "0",
                "--max-planning-calls",
                "20",
            ),
            env=self.env,
            timeout=240,
            cwd=self.root,
        )
        for number in range(20):
            view = run.status()
            need = view.get("needs") or {}
            if view.get("approved_contract"):
                self.assertIn(view["next_stage"], ("builder", "orchestrator", STAGE), view)
                self.assertFalse(any(row["stage"] == STAGE for row in view["usage"]["accounting"]["attempts"]))
                return run
            if need.get("kind") == "approve_plan":
                plan = actions.plan_for_approval(view)
                self.assertIsNotNone(plan)
                self.assertEqual(set(CASES), {row["id"] for row in plan["acceptance_criteria"]})
                exported = self.root / f"reviewed-plan-{number:02d}.md"
                run.export_plan(exported)
                checked = json.loads(self.public("plan", "--check", str(exported), "--json").stdout)
                self.assertTrue(checked["matches"])
                displayed = run.show_goal()
                token = need["token"]
                self.assertEqual(token, run.status()["needs"]["token"])
                (self.root / f"agent-plan-review-{number:02d}.json").write_text(
                    json.dumps(
                        {
                            "actor": "Codex agent review under owner delegation in AGENTS.md",
                            "token": token,
                            "whole_displayed_plan": plan,
                            "show_goal": displayed,
                            "whole_exported_plan": exported.read_text(),
                            "check": checked,
                        },
                        indent=2,
                    )
                    + "\n"
                )
                run.approve_plan(token)
            elif need.get("kind") == "answer":
                reports = [row for row in view["replay_source"]["records"] if row["stage"] == "requirements_gather"]
                report = json.loads(Path(reports[-1]["output"]).read_text())
                question = need["questions"][0]
                answer = question.get("proposed_default") or "plan"
                (self.root / f"agent-requirements-review-{number:02d}.json").write_text(
                    json.dumps({"whole_report": report, "questions": need["questions"], "answer": answer}, indent=2)
                    + "\n"
                )
                run.answer(question["id"], answer, resolver_token=need.get("resolver_token"))
            elif need.get("kind") == "resume":
                run.resume_paused()
            elif need.get("kind") == "continue":
                run.advance()
            else:
                self.fail(f"Unexpected stock planning gate: {view}")
        self.fail("The public stock planning flow did not reach its approved Writer boundary")

    @staticmethod
    def attempts(document):
        return document["view"]["usage"]["accounting"]["attempts"]

    def test_stop_now_keeps_writer_partial_source_and_plain_resume_repeats_only_that_job(self):
        run = self.planned_run()
        initial = self.status(run)
        original_test = (self.project / "tests/test_stock.py").read_bytes()
        named = actions.named(initial["view"], run.workspace, run.run_dir)
        streams = [(self.root / f"worker.{name}").open("w") for name in ("stdout", "stderr")]
        self.addCleanup(streams[0].close)
        self.addCleanup(streams[1].close)
        worker = subprocess.Popen(
            [
                *self.entry,
                "--workspace",
                str(run.workspace),
                "--run-dir",
                str(run.run_dir),
                *named.argv,
                "--pause-after-stage",
            ],
            cwd=self.root,
            env={**self.env, **named.env},
            stdout=streams[0],
            stderr=streams[1],
        )
        try:
            readable, _, _ = select.select([self.ready_fd], [], [], 150)
            self.assertTrue(readable, "The real acceptance Writer never entered its FIFO handshake")
            entered = json.loads(os.read(self.ready_fd, 4096))
            self.assertEqual(str(self.project), entered["cwd"])
            self.assertEqual(self.project / "tests/test_stock.py", Path(entered["path"]))
            self.assertEqual(PARTIAL, Path(entered["path"]).read_bytes())
            provider = psutil.Process(entered["pid"])
            before = self.status(run)
            [active] = [row for row in self.attempts(before) if row["active"]]
            self.assertEqual(STAGE, active["stage"])
            self.assertEqual("acceptance_tester", active["route_role"])
            offer = before["view"]["pause"]["stop_now"]
            self.assertFalse(offer["requested"])
            argv = [word.replace("REQUEST_ID", "stop-acceptance-writer-1") for word in offer["next_command"]]
            receipt = json.loads(self.public(*argv).stdout)
            self.assertTrue(receipt["accepted"])
            self.assertTrue(receipt["receipt"]["now"])
            self.assertEqual(2, worker.wait(timeout=45), (self.root / "worker.stdout").read_text())
            after = self.status(run)
            view = after["view"]
            # This is the reported defect, not an assertion inferred from report text.
            self.assertEqual("PAUSED_INTERVENTION", view["status"])
            self.assertEqual(STAGE, view["next_stage"])
            self.assertFalse(view["done"])
            self.assertEqual("resume", view["needs"]["kind"])
            self.assertIn("Stopped by you", view["stop_reason"])
            self.assertEqual(0, view["usage"]["accounting"]["active_attempts"])
            self.assertFalse(after.get("runner_check_workers"))
            self.assertTrue(not provider.is_running() or provider.status() == psutil.STATUS_ZOMBIE)
            self.assertFalse(self.completed.exists(), "The stopped Writer must not complete its FIFO on its own")
            self.assertEqual(original_test, (self.project / "tests/test_stock.py").read_bytes())
            self.assertEqual(before["settings"], after["settings"])
            self.assertEqual(before["view"]["routes"], view["routes"])
            self.assertEqual(initial["view"]["approved_contract"], view["approved_contract"])
            self.assertEqual(len(self.attempts(before)), len(self.attempts(after)))
            self.assertEqual([row for row in self.attempts(before) if not row["active"]], self.attempts(after)[:-1])
            stopped = self.attempts(after)[-1]
            self.assertEqual(active["attempt_id"], stopped["attempt_id"])
            self.assertEqual(active["launch_route"], stopped["launch_route"])
            self.assertIsNotNone(stopped["finished_at"])
            self.assertIsNotNone(stopped["exit_code"])
            self.assertTrue(stopped["timed_out"])
            intervention = after["interventions"]
            self.assertEqual(0, intervention["pending_count"])
            [applied] = [row for row in intervention["applied_receipts"] if row["id"] == receipt["receipt"]["id"]]
            self.assertEqual("pause", applied["kind"])
            self.assertTrue(applied["now"])
            self.assertTrue(applied["applied_at"])
            [record] = [row for row in view["replay_source"]["records"] if row["stage"] == STAGE]
            capture = record["job_source"]
            kept = capture["stopped_capture"]
            self.assertEqual(kept["sha256"], hashlib.sha256(Path(kept["path"]).read_bytes()).hexdigest())
            partial = json.loads(Path(kept["path"]).read_text())
            self.assertEqual("unverified", partial["verification"])
            self.assertEqual(capture["before_identity"], partial["original_identity"])
            self.assertEqual(capture["witness_hash"], partial["witness_sha256"])
            self.assertEqual(PARTIAL, Path(partial["blobs"]["tests/test_stock.py"]).read_bytes())
            self.assertEqual(hashlib.sha256(PARTIAL).hexdigest(), partial["files"]["tests/test_stock.py"]["sha256"])
            self.assertIn("archived-", str(Path(kept["path"]).parent))
            original = json.loads(Path(capture["capture"]).read_text())["original"]
            for name, entry in original["files"].items():
                if entry["kind"] == "file":
                    path = self.project / name
                    self.assertEqual(entry["sha256"], hashlib.sha256(path.read_bytes()).hexdigest(), name)
                    self.assertEqual(entry["mode"], stat.S_IMODE(path.stat().st_mode), name)
            duplicate = json.loads(self.public(*argv).stdout)
            self.assertEqual("already_applied", duplicate["consumer"])
            repeated = self.status(run)
            self.assertEqual(intervention, repeated["interventions"])
            self.assertEqual(view["usage"], repeated["view"]["usage"])
            resume = actions.named(repeated["view"], run.workspace, run.run_dir)
            self.assertIn("--resume-paused", resume.argv)
            self.assertFalse(any("model" in word or "effort" in word or "job-retry" in word for word in resume.argv))
            stages_before = [json.loads(line) for line in self.trace.read_text().splitlines()]
            self.released.touch()
            resumed = subprocess.run(
                [
                    *self.entry,
                    "--workspace",
                    str(run.workspace),
                    "--run-dir",
                    str(run.run_dir),
                    *resume.argv,
                    "--pause-after-stage",
                ],
                cwd=self.root,
                env={**self.env, **resume.env},
                capture_output=True,
                text=True,
                timeout=240,
            )
            (self.root / "resume.stdout").write_text(resumed.stdout)
            (self.root / "resume.stderr").write_text(resumed.stderr)
            self.assertEqual(2, resumed.returncode, resumed.stdout + resumed.stderr)
            resumed_status = self.status(run)
            self.assertEqual("PAUSED_REQUESTED", resumed_status["status"])
            self.assertEqual(initial["view"]["next_stage"], resumed_status["view"]["next_stage"])
            self.assertEqual(after["settings"], resumed_status["settings"])
            self.assertEqual(view["approved_contract"], resumed_status["view"]["approved_contract"])
            rows = self.attempts(resumed_status)
            self.assertEqual(self.attempts(after), rows[:-1])
            self.assertEqual(STAGE, rows[-1]["stage"])
            self.assertNotEqual(stopped["attempt_id"], rows[-1]["attempt_id"])
            self.assertEqual(stopped["launch_route"], rows[-1]["launch_route"])
            self.assertFalse(rows[-1]["active"])
            stages_after = [json.loads(line) for line in self.trace.read_text().splitlines()]
            self.assertEqual(stages_before, stages_after[:-1])
            self.assertEqual(STAGE, stages_after[-1]["stage"])
            self.assertEqual(
                1, sum(row["id"] == applied["id"] for row in resumed_status["interventions"]["applied_receipts"])
            )
            self.assertEqual(0, resumed_status["interventions"]["pending_count"])
        finally:
            # Release only this fixture's known FIFO if an assertion failed before
            # Stop now. The stage boundary still prevents a later Builder launch.
            self.released.touch()
            os.write(self.gate_fd, b"release")
            if worker.poll() is None:
                try:
                    worker.wait(timeout=60)
                except subprocess.TimeoutExpired:
                    worker.send_signal(signal.SIGINT)
                    worker.wait(timeout=30)
