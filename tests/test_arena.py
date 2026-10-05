"""Arena oracle controls, public CLI attempts and conservative comparison gates."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
import contextlib
import io
from types import SimpleNamespace
from unittest.mock import patch

import autocode_arena as arena
import autocode_arena_policy as policy
from autocode_arena_store import Store, ArenaError, write_json

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools/autocode_arena.py"
ORACLE = '''import json, subprocess, sys
from pathlib import Path
p = Path(sys.argv[1]) / "greet.py"
ok = False
if p.is_file():
    r = subprocess.run([sys.executable, "-B", str(p), "World"], capture_output=True, text=True)
    ok = r.returncode == 0 and r.stdout == "Hello, World\\n"
print(json.dumps({"checks": [{"name": "greeting", "ok": ok}]}))
'''


class ArenaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        arena.git(self.repo, "init", "-q")
        arena.git(self.repo, "-c", "user.name=T", "-c", "user.email=t@example.invalid",
                  "commit", "-q", "--allow-empty", "-m", "base")
        self.base = arena.git(self.repo, "rev-parse", "HEAD")
        self.reference = self.root / "reference"
        self.reference.mkdir()
        (self.reference / "greet.py").write_text("import sys\nprint('Hello, ' + sys.argv[1])\n")
        self.oracle = self.root / "oracle.py"
        self.oracle.write_text(ORACLE)
        self.issue = self.root / "issue.json"
        write_json(self.issue, {"title": "Greeting CLI", "body":
            "Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
            "argument and exits 0. Any other argument count prints a usage line to stderr and exits 2. "
            "Deliver greet.py, test_greet.py with regression tests, and a short README.md. Python standard library only."})
        self.store = Store(self.root / "arena")
        self.store.initialize()

    def call(self, *args):
        # Exercise parsing, controls and persistence with a deterministic oracle
        # transport. The shared birth-identity supervisor has its own tests;
        # it needs a procfs view matching child PIDs, unavailable in some hosts.
        def oracle(command, cwd, timeout):
            p = subprocess.run(command, cwd=cwd, timeout=timeout, capture_output=True, text=True)
            return p.returncode, p.stdout, p.stderr
        out, err = io.StringIO(), io.StringIO()
        with patch.object(arena, "run_oracle", side_effect=oracle), contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = arena.main(["--arena", str(self.store.root), *args])
            except SystemExit as error:
                code = error.code
        return SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue())

    def ingest(self, ident="greeting", split="development", oracle=None):
        return self.call("ingest", ident, "--repository", str(self.repo), "--base", self.base,
                         "--issue", "acme/demo#7", "--issue-file", str(self.issue),
                         "--oracle", str(oracle or self.oracle), "--reference", str(self.reference),
                         "--check", "greeting", "--split", split)

    def test_controls_pins_and_no_future_history(self):
        result = self.ingest()
        self.assertEqual(0, result.returncode, result.stderr)
        case = self.store.case("greeting")
        (self.repo / "future-fix.txt").write_text("answer")
        arena.git(self.repo, "add", ".")
        arena.git(self.repo, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "future")
        workspace = self.root / "isolated"
        arena.checkout(self.repo, case["base_commit"], workspace)
        self.assertFalse((workspace / "future-fix.txt").exists())
        self.assertEqual("1", arena.git(workspace, "rev-list", "--count", "HEAD"))
        Path(case["oracle_path"]).write_text("tampered")
        with self.assertRaisesRegex(ArenaError, "oracle changed"):
            self.store.case("greeting")

    def test_vacuous_oracle_rejected(self):
        self.oracle.write_text('print(\'{"checks":[{"name":"greeting","ok":true}]}\')\n')
        result = self.ingest()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("controls failed", result.stderr)
        self.assertEqual([], self.store.cases())

    def test_missing_checks_and_oracle_crash_are_errors(self):
        self.assertEqual(0, self.ingest().returncode)
        case = self.store.case("greeting")
        for code, output, error in ((0, '{"checks":[]}', ''),
                                    (0, '{"checks":[{"name":"other","ok":true}]}', ''),
                                    (1, '', 'crash'), (-1, '', 'TIMEOUT')):
            with self.subTest(output=output, error=error), patch.object(arena, "run_oracle", return_value=(code, output, error)):
                with self.assertRaises(ArenaError):
                    arena.evaluate(case, self.reference, 1)

    def test_live_authorization_and_workspace_override_refused(self):
        self.assertEqual(0, self.ingest().returncode)
        denied = self.call("run", "greeting", "--cohort", "baseline")
        self.assertIn("i-authorize-live-model-spend", denied.stderr)
        override = self.call("run", "greeting", "--cohort", "fixture", "--fixture", "--option=--workspace=/tmp")
        self.assertIn("unsupported runner option", override.stderr)
        provider = self.call("run", "greeting", "--cohort", "fixture", "--fixture",
                             "--option=--provider=opencode")
        self.assertIn("bundled Codex fixture", provider.stderr)
        self.assertEqual([], self.store.rows())

    def test_taskrun_boundary_respects_plan_gate_then_scores_delivery(self):
        self.assertEqual(0, self.ingest().returncode)
        reference = self.reference
        class ScriptedRun:
            @classmethod
            def start(cls, workspace, brief, **kwargs):
                result = cls()
                result.workspace, result.run_dir = workspace, workspace / ".autocode/runs/test"
                result.approved = False
                return result
            def advance_until_input(self):
                if self.approved:
                    shutil.copyfile(reference / "greet.py", self.workspace / "greet.py")
                    return {"status": "TASK_COMPLETE", "done": True, "needs": {"kind": "none"}}
                return {"status": "AWAITING_GOAL_APPROVAL", "needs": {"kind": "approve_plan", "token": "exact"}}
            def show_goal(self):
                return "Displayed benchmark plan"
            def approve_plan(self, token):
                if token != "exact":
                    raise AssertionError("stale token")
                self.approved = True
        import shutil
        with patch.object(arena, "TaskRun", ScriptedRun):
            stopped = self.call("run", "greeting", "--cohort", "without-approval", "--fixture")
        self.assertEqual(2, stopped.returncode, stopped.stdout + stopped.stderr)
        stop = json.loads(stopped.stdout)
        self.assertEqual("STOPPED", stop["verdict"])
        self.assertEqual("approve_plan", stop["needs"]["kind"])
        with patch.object(arena, "TaskRun", ScriptedRun):
            finished = self.call("run", "greeting", "--cohort", "approved-fixture", "--fixture",
                                 "--approve-benchmark-plans")
        self.assertEqual(0, finished.returncode, finished.stdout + finished.stderr)
        row = json.loads(finished.stdout)
        self.assertEqual("PASS", row["verdict"])
        self.assertEqual("fixture", row["execution_kind"])
        self.assertEqual(2, len(self.store.rows()))
        self.assertTrue((Path(row["workspace"]).parent / "approved-plan.txt").is_file())
        self.assertIn("greet.py", Path(row["patch_path"]).read_text())
        self.assertEqual("Hello, World\n", subprocess.check_output(
            [sys.executable, str(Path(row["workspace"]) / "greet.py"), "World"], text=True))

    def test_append_only_attempts_and_interruption_visible(self):
        row = {"id": "x", "cohort": "baseline", "verdict": "RUNNING"}
        self.store.insert(row)
        self.assertEqual(1, policy.summarize(self.store.rows())["outcomes"]["RUNNING"])
        row["verdict"] = "ERROR"
        self.store.finish(row)
        with self.assertRaises(ArenaError):
            self.store.finish(row)

    def test_proposal_does_not_expose_holdout_failures_or_invent_causes(self):
        self.assertEqual(0, self.ingest().returncode)
        for case in ("greeting", "secret-holdout"):
            self.store.insert({"id": case, "case_id": case, "cohort": "old", "verdict": "FALSE_COMPLETE",
                               "checks": [{"name": "greeting", "ok": False}], "error": None})
        result = arena.proposal(self.store, "old")
        self.assertEqual(["greeting"], [r["case"] for r in result["development_failures"]])
        self.assertEqual("UNDETERMINED", result["development_failures"][0]["root_cause"])
        self.assertFalse(result["promoted"])


class PolicyTests(unittest.TestCase):
    def test_false_completion_does_not_trust_runner_or_an_oracle_crash(self):
        self.assertEqual("FALSE_COMPLETE", policy.verdict("TASK_COMPLETE", [{"ok": False}]))
        self.assertEqual("ERROR", policy.verdict("TASK_COMPLETE", [], "oracle crash"))
        self.assertEqual("STOPPED", policy.verdict("AWAITING_GOAL_APPROVAL", [{"ok": False}]))

    def population(self):
        cases = [{"id": "r", "sha256": "r", "split": "regression"},
                 {"id": "h", "sha256": "h", "split": "holdout"}]
        def row(case, version, outcome):
            return {"case_id": case, "case_sha256": case, "version_sha256": version,
                    "execution_kind": "live", "options": [], "verdict": outcome}
        return cases, [row("r", "old", "PASS"), row("h", "old", "FAIL")], [row("r", "new", "PASS"), row("h", "new", "PASS")]

    def test_gate_requires_heldout_gain_and_no_regression(self):
        cases, baseline, candidate = self.population()
        self.assertEqual("CANDIDATE_FOR_HUMAN_REVIEW", policy.compare(cases, baseline, candidate)["decision"])
        candidate[0]["verdict"] = "FAIL"
        self.assertEqual("REJECT", policy.compare(cases, baseline, candidate)["decision"])
        candidate[0]["verdict"] = "FALSE_COMPLETE"
        self.assertIn("candidate falsely claimed completion", str(policy.compare(cases, baseline, candidate)))

    def test_gate_rejects_missing_repeated_fixture_tampered_and_identical_evidence(self):
        for mutation in ("missing", "repeated", "fixture", "tampered", "identical", "unfinished"):
            cases, baseline, candidate = self.population()
            if mutation == "missing":
                candidate.pop()
            elif mutation == "repeated":
                candidate.append(candidate[0].copy())
            elif mutation == "fixture":
                candidate[0]["execution_kind"] = "fixture"
            elif mutation == "tampered":
                candidate[0]["case_sha256"] = "changed"
            elif mutation == "identical":
                candidate[0]["version_sha256"] = "old"
            else:
                candidate[0]["verdict"] = "RUNNING"
            with self.subTest(mutation=mutation):
                self.assertEqual("REJECT", policy.compare(cases, baseline, candidate)["decision"])


if __name__ == "__main__":
    unittest.main()
