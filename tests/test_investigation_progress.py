"""The Investigator's reproduction-first budget (#1902): classification, monitor and supervisor."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_investigation_progress as progress
import autocode_process as processes
from autocode_activity import ActivityMonitor
from autocode_taskrun import TaskRunError

from .test_job_failure_recovery import PROVIDER, WRAPPER, JobHarness


def kilo(identifier, tool, status="completed", **given):
    return {"type": "tool_use", "part": {"id": identifier, "tool": tool, "state": {"status": status, "input": given}}}


def codex(identifier, command, phase="completed"):
    return {"type": "item." + phase, "item": {"id": identifier, "type": "command_execution", "command": command}}


class Classification(unittest.TestCase):
    def test_read_only_pipelines_are_exploration(self):
        for command in (
            "cat tools/a.py | head -50",
            "cd /repo && grep -rn foo tools/ | wc -l",
            "git log --oneline -5 -- tools/a.py",
            "sed -n 1,40p tools/a.py",
            "find . -name '*.py' | sort",
        ):
            self.assertEqual(progress.EXPLORATION, progress.classify_command(command), command)

    def test_running_code_is_a_reproduction_attempt(self):
        for command in (
            "python3 -c 'import pager; assert pager.page_count(5, 2) == 2'",
            ".venv/bin/python -m unittest tests.test_pager",
            "cd .autocode/investigation/bug-1 && PYTHONDONTWRITEBYTECODE=1 pytest -x tests/test_pager.py",
            "cat tools/a.py | python3 -",
            "sed -i s/a/b/ .autocode/investigation/bug-1/a.py",
        ):
            self.assertEqual(progress.REPRODUCTION, progress.classify_command(command), command)

    def test_an_unrecognized_command_counts_as_nothing(self):
        self.assertIsNone(progress.classify_command("curl -s https://example.test"))
        self.assertIsNone(progress.classify_command("git checkout main"))
        self.assertIsNone(progress.classify_command("sed -i s/a/b/ a.py"))
        self.assertIsNone(progress.classify_command(""))

    def test_both_provider_shapes_classify_only_completed_calls(self):
        self.assertEqual(("p1", progress.EXPLORATION), progress.classify_row(kilo("p1", "read", filePath="a.py")))
        self.assertEqual(("p2", progress.EXPLORATION), progress.classify_row(kilo("p2", "bash", command="ls")))
        self.assertEqual(("p3", progress.REPRODUCTION), progress.classify_row(kilo("p3", "write", filePath="t.py")))
        self.assertIsNone(progress.classify_row(kilo("p4", "read", status="running")))
        self.assertEqual(("c1", progress.EXPLORATION), progress.classify_row(codex("c1", "rg foo")))
        self.assertIsNone(progress.classify_row(codex("c2", "rg foo", phase="started")))
        for junk in ({}, {"type": "tool_use"}, {"type": "tool_use", "part": {"id": 3}}, {"item": "x"}):
            self.assertIsNone(progress.classify_row(junk))


class BudgetTests(unittest.TestCase):
    def test_exploration_alone_exhausts_the_budget_once_per_call(self):
        budget = progress.Budget(3)
        for index in range(2):
            budget.observe(kilo(f"r{index}", "read"))
            budget.observe(kilo(f"r{index}", "read"))  # a repeated row is the same call
        self.assertIsNone(budget.reason())
        budget.observe(kilo("r2", "grep", pattern="x"))
        self.assertIn("3 source-exploration calls without attempting a reproduction", budget.reason())

    def test_a_probe_before_the_limit_ends_the_budget_for_good(self):
        budget = progress.Budget(3)
        budget.observe(kilo("r0", "read"))
        budget.observe(codex("c0", "python3 -c 'assert 1'"))
        for index in range(10):
            budget.observe(kilo(f"r{index + 1}", "read"))
        self.assertIsNone(budget.reason())

    def test_zero_or_invalid_limits_disable_it(self):
        for limit in (0, None, -1, "3", True):
            budget = progress.Budget(limit)
            for index in range(50):
                budget.observe(kilo(f"r{index}", "read"))
            self.assertIsNone(budget.reason(), limit)


class MonitorAndSupervisor(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "events.jsonl"
        self.path.touch()

    def write(self, rows):
        with self.path.open("a") as stream:
            for row in rows:
                stream.write(json.dumps(row) + "\n")

    def test_the_monitor_reports_a_progress_stop_and_no_stop_without_a_budget(self):
        monitor = ActivityMonitor(self.path, progress=progress.Budget(2), clock=lambda: 0)
        self.write([kilo("r0", "read")])
        self.assertIsNone(monitor.poll()["progress_stop"])
        self.assertIsNone(monitor.expired())
        self.write([kilo("r1", "read")])
        snapshot = monitor.poll()
        self.assertEqual("progress", monitor.expired()["kind"])
        self.assertEqual("stalled", snapshot["activity"])
        self.assertTrue(snapshot["progress_stop"])
        plain = ActivityMonitor(self.path, clock=lambda: 0)
        self.assertNotIn("progress_stop", plain.poll())
        self.assertIsNone(plain.expired())

    def run_provider(self, rows, budget):
        self.write(rows)
        monitor = ActivityMonitor(self.path, idle_seconds=0, tool_seconds=0, progress=progress.Budget(budget))
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True)
        self.addCleanup(child.kill)
        code, expired = processes.wait_for_stage(child, 20, lambda _rows: None, activity=monitor)
        return code, expired, monitor

    def test_the_supervisor_stops_a_stage_that_only_explores(self):
        code, expired, monitor = self.run_provider([kilo(f"r{i}", "read") for i in range(3)], 3)
        self.assertTrue(expired)
        self.assertNotEqual(0, code)
        self.assertEqual("progress", monitor.timeout["kind"])
        self.assertIn("without attempting a reproduction", monitor.timeout["reason"])

    def test_a_stage_that_probes_within_budget_is_left_alone(self):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1)"], start_new_session=True)
        self.addCleanup(child.kill)
        self.write([kilo("r0", "read"), codex("c0", "python3 -m pytest -x"), kilo("r1", "read"), kilo("r2", "read")])
        monitor = ActivityMonitor(self.path, idle_seconds=0, tool_seconds=0, progress=progress.Budget(3))
        code, expired = processes.wait_for_stage(child, 20, lambda _rows: None, activity=monitor)
        self.assertEqual((0, False), (code, expired))


class RetryHandoff(unittest.TestCase):
    def test_summary_lists_reads_once_and_counts_probes(self):
        rows = [
            kilo("a", "read", filePath="/repo/tools/a.py"),
            kilo("a", "read", filePath="/repo/tools/a.py"),
            kilo("b", "grep", path="tools", pattern="x"),
            kilo("c", "bash", command="ls tools"),
            codex("d", "python3 -c 'assert 1'"),
        ]
        self.assertEqual(
            {"explored_calls": 3, "reproduction_attempts": 1, "explored_paths": ["tools/a.py", "tools"]},
            progress.summarize(rows, "/repo"),
        )

    def test_exact_retry_packet_carries_what_the_stopped_attempt_explored(self):
        import autocode_bug_job as bug_job

        with tempfile.TemporaryDirectory() as temp:
            archive = Path(temp) / "archived"
            archive.mkdir()
            events = archive / "bug-investigation-01.jsonl"
            events.write_text("\n".join(json.dumps(kilo(f"r{i}", "read", filePath=f"/repo/m{i}.py")) for i in range(3)))
            state = {
                "task": "bug",
                "workspace": "/repo",
                "settings": {"limits": {"investigation_exploration_budget": 3}},
                "job_failure": {
                    "stage": "investigate_bug",
                    "attempt_id": "001/bug-investigation-01",
                    "job_retry_token": "jr:x",
                    "source_identity": "src",
                    "archive": str(archive),
                },
                "recovery_context": {
                    "kind": "job_failure",
                    "stage": "investigate_bug",
                    "attempt_id": "001/bug-investigation-01",
                    "events": str(events),
                    "timeout_kind": "progress",
                },
                "job_retry_authorization": {"stage": "investigate_bug", "token": "jr:x", "consumed": False},
            }
            packet = bug_job.packet(state)
        prior = packet["prior_failed_attempt"]
        self.assertEqual(3, packet["exploration_budget"])
        self.assertEqual(
            ("progress", 3, 0), (prior["stop_kind"], prior["explored_calls"], prior["reproduction_attempts"])
        )
        self.assertEqual(["m0.py", "m1.py", "m2.py"], prior["explored_paths"])
        self.assertEqual("unverified_leads_only", prior["evidence_status"])


EXPLORER = PROVIDER.replace(
    "mode=os.environ['JOB_MODE']\n",
    "mode=os.environ['JOB_MODE']\n"
    "if mode=='explore':\n"
    " print(json.dumps({'type':'thread.started','thread_id':str(uuid.uuid4())}),flush=True)\n"
    " for i in range(40):print(json.dumps({'type':'item.completed','item':{'id':'c'+str(i),'type':'command_execution','command':'cat calc.py | head -5','status':'completed','exit_code':0}}),flush=True)\n"
    " __import__('time').sleep(120)\n",
    1,
)


class InvestigatorThatOnlyExplores(JobHarness):
    def test_the_stage_stops_early_and_pauses_for_the_existing_retry_question(self):
        self.assertIn("mode=='explore'", EXPLORER)
        self.wrapper.write_text(WRAPPER.replace("'filtered'):", "'filtered','explore'):", 1))
        (self.workspace / ".autocode/bin/codex").write_text(EXPLORER)
        run = self.start("explore", workflow="bugfix")
        view = run.status()
        self.assertEqual("PAUSED_JOB_FAILURE", view["status"], view)
        need = view["needs"]
        self.assertEqual(("retry_job", "investigate_bug"), (need["kind"], need["stage"]))
        self.assertTrue(need["job_retry_token"])
        self.assertRegex(
            need["reason"], r"Stopped after \d+ source-exploration calls without attempting a reproduction \(budget 30"
        )
        self.assertEqual(1, self.count())
        self.assertNotIn("120", need["reason"])  # stopped by the budget, not the 900-second window


# The explorer, also logging each launch's argv and prompt; and the wrapper, also saving the tool's
# variant listing for the configured model at the normal settings boundary (as a Kilo/OpenCode run
# records its listing), never by editing a checkpoint.
LOGGING_EXPLORER = EXPLORER.replace(
    "sys.stdin.read()\n",
    "prompt=sys.stdin.read()\n"
    "with Path(os.environ['JOB_LAUNCHES']).open('a') as f:f.write(json.dumps({'argv':sys.argv,'prompt':prompt})+'\\n')\n",
    1,
)
DECLARING_WRAPPER = WRAPPER.replace("'filtered'):", "'filtered','explore'):", 1).replace(
    "raise SystemExit(autocode.main())",
    "configure_declared=autocode.configure\n"
    "def declared(*args,**kwargs):\n"
    " selected=configure_declared(*args,**kwargs)\n"
    " selected['declared_variants']=json.loads(os.environ['JOB_DECLARED'])\n"
    " return selected\n"
    "autocode.configure=declared\n"
    "raise SystemExit(autocode.main())",
    1,
)


class InvestigatorRetriesAtALowerEffort(JobHarness):
    """#1902 step 2: the stop offers the same model at a lighter declared variant; the answer binds the retry to it."""

    def launches(self):
        return [json.loads(line) for line in Path(self.env["JOB_LAUNCHES"]).read_text().splitlines()]

    def test_an_exploration_stop_offers_and_binds_the_next_lower_declared_effort(self):
        self.assertIn("JOB_LAUNCHES", LOGGING_EXPLORER)
        self.assertIn("JOB_DECLARED", DECLARING_WRAPPER)
        self.wrapper.write_text(DECLARING_WRAPPER)
        (self.workspace / ".autocode/bin/codex").write_text(LOGGING_EXPLORER)
        self.env["JOB_LAUNCHES"] = str(self.workspace / ".autocode" / "launches")
        self.env["JOB_DECLARED"] = json.dumps({"gpt-6-sol": ["low", "high"]})
        self.options = (*self.options, "--reasoning-effort", "high")
        run = self.start("explore", workflow="bugfix")
        held = run.status()
        need = held["needs"]
        self.assertEqual(("PAUSED_JOB_FAILURE", "investigate_bug"), (held["status"], need.get("stage")), need)
        route = need["route"]
        self.assertEqual(("timeout", "investigator"), (route["cause"], route["role"]))
        self.assertEqual(
            {"model": "gpt-6-sol", "from": "high", "to": "low", "answer": "gpt-6-sol@low"}, route["lower_effort"]
        )
        self.assertEqual(["gpt-6-sol@low"], route["candidates"])
        self.assertIn("--answer route-investigator=gpt-6-sol@low --job-retry-token", held["next_command"])
        self.assertIn("lower effort", need["reason"])
        self.assertIn('model_reasoning_effort="high"', " ".join(self.launches()[0]["argv"]))

        # A level the model does not declare is refused and changes nothing.
        with self.assertRaisesRegex(TaskRunError, "does not declare reasoning variant 'medium'"):
            run.assign_model("investigator", "gpt-6-sol@medium")
        self.assertEqual(held["needs"], run.status()["needs"])

        assigned = run.assign_model("investigator", "gpt-6-sol@low")
        self.assertEqual(1, self.count(), "naming the lighter variant launches nothing")
        self.assertEqual(
            ("gpt-6-sol", "low"), tuple(assigned["routes"]["investigator"][k] for k in ("model", "reasoning_effort"))
        )
        for role in set(held["routes"]) - {"investigator"}:
            self.assertEqual(held["routes"][role], assigned["routes"][role], role)
        self.assertEqual(held["usage"], assigned["usage"])
        token = assigned["needs"]["job_retry_token"]
        self.assertNotEqual(need["job_retry_token"], token)
        self.assertNotIn("lower_effort", assigned["needs"]["route"])
        self.assertIn("at low effort (was high)", assigned["needs"]["reason"])
        with self.assertRaises(TaskRunError):
            run.retry_job(need["job_retry_token"])
        self.assertEqual(1, self.count())

        run.retry_job(token)  # the retry explores again and stops again; its launch is the oracle
        self.assertEqual(2, self.count())
        retry = self.launches()[1]
        self.assertIn('model_reasoning_effort="low"', " ".join(retry["argv"]))
        # The handoff names which limit stopped the attempt it retries.
        self.assertIn('"stop_kind": "progress"', retry["prompt"])


if __name__ == "__main__":
    unittest.main()
