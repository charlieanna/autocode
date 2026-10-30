"""Reporter questions through TaskRun, with a deterministic offline Investigator.

Runs are created through the CLI; no checkpoint is fabricated or changed. The
provider's retained handoffs and the public status/answer interface are the
oracles. The only tampering test changes a retained normal report artifact.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from autocode_taskrun import TaskRun, TaskRunError

from .test_changeset_cli import register_project

QUESTIONS = ["Which application version failed?", "Which input caused the failure?"]
PROVIDER = r"""#!/usr/bin/env python3
import json,os,sys,subprocess,uuid,shlex
from pathlib import Path
if sys.argv[1:3]==['sandbox','--help']:
 print('--config -- macos linux');raise SystemExit(0)
if sys.argv[1:2]==['sandbox']:
 raise SystemExit(subprocess.call(sys.argv[sys.argv.index('--')+1:]))
if sys.argv[1:]==['login','status']:
 print('Logged in (offline fixture)');raise SystemExit(0)
if sys.argv[1:]==['--version']:
 print('codex-cli fixture');raise SystemExit(0)
prompt=sys.stdin.read()
data=json.loads(prompt.split('CURRENT HANDOFF DATA\n',1)[1])
with Path(os.environ['INVESTIGATION_CALLS']).open('a') as stream:
 stream.write(json.dumps(data)+'\n')
stage=data.get('stage') or (data.get('original') or {}).get('stage')
if stage!='investigate_bug':
 print('Unexpected stage: '+str(stage),file=sys.stderr);raise SystemExit(41)
questions=['Which application version failed?','Which input caused the failure?']
if data.get('saved_answers'):
 questions=[]
report={'outcome':'not_reproduced','note_path':'docs/bugs/reporter-input.json',
 'observed':'The reporter saw a wrong calculation.',
 'reproduction':'The provided local example returns the expected result; the failing version and input are unknown.',
 'root_cause':'','affected_paths':[],'test_paths':[],'invariant':'','test_cases':[],
 'conclusion':'The failure is not reproduced in the provided example. Reporter details are needed before diagnosing it.',
 'fix_size':'none','fix_plan':[],'questions':questions,'tests_run':[],
 'plan_approval_requested':False,'probe':'','untestable':'',
 'counterexample':{'file':'','find':'','replace':''},
 'workspace_scope':{'spans_repositories':False,'external_parts':[]}}
if os.environ.get('INVESTIGATION_LATE_SCOPE'):
 report.update(outcome='reproduced', root_cause='The local calculation uses the old multiplier.',
  affected_paths=['calc.py'],test_paths=['tests/test_calc.py'],invariant='The new protocol triples the input.',
  test_cases=[{'id':'T1','given':'value 2','when':'double(2)','then':'returns 6','kind':'restore'}],
  fix_size='small',fix_plan=['Update the local calculation and test the changed contract'],questions=[],
  probe=shlex.quote(sys.executable)+" -c 'from calc import double; assert double(2) == 4'",
  counterexample={'file':'calc.py','find':'return value * 2','replace':'return value * 3'},
  workspace_scope={'spans_repositories':True,'external_parts':[
   {'target':'history-service','work':'Accept the new calculation contract','source_quote':'','evidence':'history_client.py:1'}]})
Path(sys.argv[sys.argv.index('-o')+1]).write_text(json.dumps(report))
session=sys.argv[sys.argv.index('resume')+1] if 'resume' in sys.argv else str(uuid.uuid4())
print(json.dumps({'type':'thread.started','thread_id':session}),flush=True)
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':10}}),flush=True)
"""


class InvestigatorQuestionTaskRunTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.seed_temp = tempfile.TemporaryDirectory(prefix="investigator-question-seed-")
        cls.seed = Path(cls.seed_temp.name) / "seed"
        cls.seed.mkdir()
        (cls.seed / ".gitignore").write_text(".autocode/\n__pycache__/\n")
        (cls.seed / "calc.py").write_text("def double(value):\n    return value * 2\n")
        (cls.seed / "history_client.py").write_text("HISTORY_SERVICE = 'https://history-service/results'\n")
        for command in (
            ["init", "-q"],
            ["config", "maintenance.auto", "false"],
            ["config", "gc.auto", "0"],
            ["add", "-A"],
            ["commit", "-qm", "seed"],
        ):
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(cls.seed),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.invalid",
                    "-c",
                    "commit.gpgsign=false",
                    *command,
                ],
                check=True,
            )

    @classmethod
    def tearDownClass(cls):
        cls.seed_temp.cleanup()

    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="investigator-question-")
        self.addCleanup(temp.cleanup)
        root = Path(temp.name)
        self.workspace = root / "project"
        shutil.copytree(self.seed, self.workspace)
        private = self.workspace / ".autocode"
        bindir = private / "bin"
        bindir.mkdir(parents=True)
        provider = bindir / "codex"
        provider.write_text(PROVIDER)
        provider.chmod(0o755)
        self.calls = private / "investigation-calls.jsonl"
        self.env = {
            "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
            "AUTOCODE_HOME": str(root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "INVESTIGATION_CALLS": str(self.calls),
        }
        self.options = ("--engine", "codex", "--astra-model", "gpt-6-sol", "--sol-model", "gpt-6-sol")

    def start(self):
        return TaskRun.start(
            self.workspace,
            "Investigate the reported incorrect result from calc.double.",
            options=self.options,
            start_options=("--workflow", "bugfix"),
            env=self.env,
            timeout=120,
        )

    def handoffs(self):
        return [json.loads(line) for line in self.calls.read_text().splitlines()]

    def diagnostics(self, run, view):
        """Compact retained failure evidence; state is never changed by a test."""
        saved = json.loads((run.run_dir / "state.json").read_text())
        return {
            **{key: view.get(key) for key in ("status", "phase", "next_stage", "stop_reason")},
            "failures": [
                {key: row.get(key) for key in ("last_error", "error_class", "reason")}
                for row in (saved.get("failure_history") or {}).values()
            ],
        }

    def test_reproduced_late_scope_absent_from_the_brief_stops_before_planning(self):
        self.env["INVESTIGATION_LATE_SCOPE"] = "1"
        run = self.start()
        view = run.advance_until_input()
        self.assertEqual("answer", view["needs"]["kind"], self.diagnostics(run, view))
        [question] = view["needs"]["questions"]
        self.assertTrue(question["id"].startswith("workspace-scope-"))
        self.assertIn("history-service", question["question"])
        self.assertEqual(["This repository only"], question["options"])
        self.assertEqual("", question["proposed_default"])
        self.assertEqual(
            [
                {
                    "target": "history-service",
                    "work": "Accept the new calculation contract",
                    "source_quote": "",
                    "evidence": "client.py:1",
                }
            ],
            view["scope"]["external_parts"],
        )
        self.assertIn("history-service", [row["repository"] for row in view["scope"]["briefs"]])
        self.assertEqual(1, len(self.handoffs()), "no Planner or Builder starts before the scope choice")
        member = self.workspace.parent / "history-service"
        shutil.copytree(self.seed, member)
        register_project(Path(self.env["AUTOCODE_HOME"]), member)
        linked_env = {**os.environ, **self.env, "INVESTIGATION_CALLS": str(member / ".autocode/calls.jsonl")}
        linked_env.pop("INVESTIGATION_LATE_SCOPE")
        command = [sys.executable, str(Path(__file__).resolve().parents[1] / "tools/autocode.py")]
        linked = subprocess.run(
            [
                *command,
                "changeset",
                "link",
                "--run-dir",
                str(run.run_dir),
                "--target",
                "history-service",
                "--workspace",
                str(member),
                "--json",
                "--",
                *self.options,
                "--workflow",
                "bugfix",
            ],
            env=linked_env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertEqual(0, linked.returncode, linked.stdout + linked.stderr)
        summary = json.loads(linked.stdout)
        self.assertEqual(["source", "member"], [row["role"] for row in summary["runs"]])
        self.assertIsNone(summary["approval"]["set_token"], "linking never approves or answers the scope choice")
        member_row = summary["runs"][1]
        self.assertEqual(str(member.resolve()), member_row["workspace"])
        member_run = TaskRun(member, Path(member_row["run_dir"]), env=linked_env)
        self.assertEqual(str(run.run_dir), member_run.status()["scope"]["linked"][0]["run_dir"])
        view = run.status()
        self.assertEqual(question["id"], view["needs"]["questions"][0]["id"])
        self.assertEqual("history-service", view["scope"]["linked"][0]["target"])
        self.assertIsNone(view["interaction_timing"]["first_builder_at"])
        self.assertIsNone(member_run.status()["interaction_timing"]["first_builder_at"])
        self.assertEqual(1, len(self.handoffs()), "linking cannot dispatch the source Investigator again")
        answered = run.answer(question["id"], "This repository only", resolver_token=view["needs"]["resolver_token"])
        self.assertEqual("this_repository", answered["scope"]["decision"])
        self.assertEqual(["history-service"], answered["scope"]["uncovered"])
        self.assertFalse(answered["done"])

    def waiting(self, run, count=2):
        view = run.status()
        self.assertEqual(
            ("WAITING_FOR_USER", "INVESTIGATING", "investigate_bug"),
            (view["status"], view["phase"], view["next_stage"]),
            self.diagnostics(run, view),
        )
        self.assertFalse(view["done"])
        self.assertEqual("bugfix", view["workflow"])
        self.assertEqual("answer", view["needs"]["kind"])
        self.assertEqual(count, len(view["needs"]["questions"]))
        self.assertTrue(view["needs"]["resolver_token"])
        self.assertNotIn("displayed_plan", view)
        return view

    def test_reporter_answers_are_authenticated_and_resume_the_investigator(self):
        run = self.start()
        view = self.waiting(run)
        self.assertEqual(QUESTIONS, [row["question"] for row in view["needs"]["questions"]])
        self.assertEqual(1, len(self.handoffs()))
        first = view["needs"]
        question = first["questions"][0]
        missing_token = subprocess.run(
            [
                *run.command,
                "--workspace",
                str(self.workspace),
                "--run-dir",
                str(run.run_dir),
                "--answer",
                question["id"] + "=Version 1.2",
            ],
            env={**os.environ, **self.env},
            capture_output=True,
            text=True,
            timeout=120,
        )
        self.assertNotEqual(0, missing_token.returncode)
        self.assertIn("token", missing_token.stdout + missing_token.stderr)
        with self.assertRaisesRegex(TaskRunError, "exact current AutoResolver request and token"):
            run.answer(question["id"], "Version 1.2", resolver_token="forged-token")
        view = run.answer(question["id"], "Version 1.2", resolver_token=first["resolver_token"])
        self.assertEqual(1, len(self.handoffs()), "An answer must not launch another model")
        remaining = self.waiting(run, count=1)["needs"]
        self.assertNotEqual(first["resolver_token"], remaining["resolver_token"])
        with self.assertRaisesRegex(TaskRunError, "exact current AutoResolver request and token|out of date"):
            run.answer(remaining["questions"][0]["id"], "double(4)", resolver_token=first["resolver_token"])
        view = run.answer(remaining["questions"][0]["id"], "double(4)", resolver_token=remaining["resolver_token"])
        self.assertEqual(
            ("RUNNING", "INVESTIGATING", "investigate_bug"), (view["status"], view["phase"], view["next_stage"])
        )
        self.assertEqual("continue", view["needs"]["kind"])
        self.assertFalse(view["done"])
        self.assertEqual(1, len(self.handoffs()))
        final = run.advance_until_input()
        self.assertTrue(
            final["done"], {key: final.get(key) for key in ("status", "phase", "next_stage", "stop_reason")}
        )
        self.assertIsNone(final["needs"])
        handoffs = self.handoffs()
        self.assertEqual(["investigate_bug", "investigate_bug"], [row["stage"] for row in handoffs])
        resumed = handoffs[-1]
        self.assertEqual({"Version 1.2", "double(4)"}, {answer["text"] for answer in resumed["saved_answers"].values()})
        self.assertTrue(all(answer["actor"] == "user_cli" for answer in resumed["saved_answers"].values()))
        self.assertEqual("not_reproduced", resumed["prior_investigation"]["outcome"])
        self.assertEqual(QUESTIONS, resumed["prior_investigation"]["questions"])
        self.assertIsNone(resumed["goal_contract"])
        self.assertIsNone(resumed["current_task"])
        self.assertEqual("def double(value):\n    return value * 2\n", (self.workspace / "calc.py").read_text())

    def test_changed_report_invalidates_the_published_answer_authority(self):
        run = self.start()
        need = self.waiting(run)["needs"]
        reports = list(run.run_dir.glob("iterations/*/bug-investigation-[0-9][0-9].json"))
        self.assertEqual(1, len(reports))
        original = reports[0].read_bytes()
        reports[0].write_bytes(original + b"\n ")
        question = need["questions"][0]
        with self.assertRaises(TaskRunError):
            run.answer(question["id"], "Version 1.2", resolver_token=need["resolver_token"])
        view = run.status()
        self.assertFalse(view["done"])
        self.assertEqual("investigate_bug", view["next_stage"])
        self.assertNotEqual("approve_plan", (view["needs"] or {}).get("kind"))
        self.assertEqual(1, len(self.handoffs()))
        reports[0].write_bytes(original)
        fresh = self.waiting(run)["needs"]
        accepted = run.answer(question["id"], "Version 1.2", resolver_token=fresh["resolver_token"])
        self.assertFalse(accepted["done"])
        self.assertEqual(1, len(accepted["needs"]["questions"]))


if __name__ == "__main__":
    unittest.main()
