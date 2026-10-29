"""Arena oracle controls, public CLI attempts and conservative comparison gates."""

import contextlib
import io
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode_arena as arena
import autocode_arena_policy as policy
import autocode_failure_attribution as attribution
from autocode_arena_store import ArenaError, Store, write_json

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "tools/autocode_arena.py"
ORACLE = """import json, subprocess, sys
from pathlib import Path
p = Path(sys.argv[1]) / "greet.py"
ok = False
if p.is_file():
    r = subprocess.run([sys.executable, "-B", str(p), "World"], capture_output=True, text=True)
    ok = r.returncode == 0 and r.stdout == "Hello, World\\n"
print(json.dumps({"checks": [{"name": "greeting", "ok": ok}]}))
"""


class StoreConnectionTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.connections = []
        self.connection_factory = sqlite3.Connection
        self.authorizer = None
        self.real_connect = sqlite3.connect

        def connect(*args, **kwargs):
            kwargs.setdefault("factory", self.connection_factory)
            db = self.real_connect(*args, **kwargs)
            self.connections.append(db)
            self.addCleanup(db.close)
            if self.authorizer is not None:
                db.set_authorizer(self.authorizer)
            return db

        patcher = patch("autocode_arena_store.sqlite3.connect", side_effect=connect)
        patcher.start()
        self.addCleanup(patcher.stop)

    def assert_connection_closed(self):
        with self.assertRaises(sqlite3.ProgrammingError):
            self.connections[-1].execute("SELECT 1")

    def test_each_store_operation_closes_its_connection_and_preserves_rows(self):
        store = Store(self.root / "arena")
        store.initialize()
        self.assert_connection_closed()
        running = {"id": "one", "cohort": "current", "verdict": "RUNNING"}
        store.insert(running)
        self.assert_connection_closed()
        finished = {**running, "verdict": "PASS"}
        store.finish(finished)
        self.assert_connection_closed()
        self.assertEqual([{**finished, "failure_attribution": attribution.unknown()}], store.rows("current"))
        self.assert_connection_closed()
        self.assertEqual([], store.rows("other"))
        self.assert_connection_closed()

    def test_failed_writes_close_and_roll_back_the_real_database(self):
        store = Store(self.root / "arena")
        store.initialize()
        running = {"id": "one", "cohort": "current", "verdict": "RUNNING"}
        store.insert(running)

        class FailAfterWrite(sqlite3.Connection):
            def execute(self, statement, parameters=()):
                cursor = super().execute(statement, parameters)
                if statement.startswith(("INSERT", "UPDATE")):
                    raise RuntimeError("injected failure after the SQLite write")
                return cursor

        self.connection_factory = FailAfterWrite
        for operation in (
            lambda: store.insert({"id": "two", "cohort": "current", "verdict": "RUNNING"}),
            lambda: store.finish({**running, "verdict": "PASS"}),
        ):
            with self.subTest(operation=operation):
                with self.assertRaisesRegex(RuntimeError, "after the SQLite write"):
                    operation()
                self.assert_connection_closed()
                self.assertEqual([{**running, "failure_attribution": attribution.unknown()}], store.rows())
                self.assert_connection_closed()

    def test_schema_and_payload_errors_close_their_connections(self):
        self.authorizer = lambda action, *args: (
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_CREATE_TABLE else sqlite3.SQLITE_OK
        )
        with self.assertRaises(sqlite3.DatabaseError):
            Store(self.root / "denied-schema").initialize()
        self.assert_connection_closed()
        self.authorizer = None
        store = Store(self.root / "invalid-payload")
        store.initialize()
        with contextlib.closing(self.real_connect(store.database)) as db, db:
            db.execute("INSERT INTO attempts VALUES (?, ?)", ("bad", "not-json"))
        with self.assertRaises(json.JSONDecodeError):
            store.rows()
        self.assert_connection_closed()


class ArenaTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.repo = self.root / "repo"
        self.repo.mkdir()
        arena.git(self.repo, "init", "-q")
        arena.git(
            self.repo,
            "-c",
            "user.name=T",
            "-c",
            "user.email=t@example.invalid",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "base",
        )
        self.base = arena.git(self.repo, "rev-parse", "HEAD")
        self.reference = self.root / "reference"
        self.reference.mkdir()
        (self.reference / "greet.py").write_text("import sys\nprint('Hello, ' + sys.argv[1])\n")
        self.oracle = self.root / "oracle.py"
        self.oracle.write_text(ORACLE)
        self.issue = self.root / "issue.json"
        write_json(
            self.issue,
            {
                "title": "Greeting CLI",
                "body": "Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
                "argument and exits 0. Any other argument count prints a usage line to stderr and exits 2. "
                "Deliver greet.py, test_greet.py with regression tests, and a short README.md. Python standard library only.",
            },
        )
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
        with (
            patch.object(arena, "run_oracle", side_effect=oracle),
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(err),
        ):
            try:
                code = arena.main(["--arena", str(self.store.root), *args])
            except SystemExit as error:
                code = error.code
        return SimpleNamespace(returncode=code, stdout=out.getvalue(), stderr=err.getvalue())

    def ingest(self, ident="greeting", split="development", oracle=None):
        return self.call(
            "ingest",
            ident,
            "--repository",
            str(self.repo),
            "--base",
            self.base,
            "--issue",
            "acme/demo#7",
            "--issue-file",
            str(self.issue),
            "--oracle",
            str(oracle or self.oracle),
            "--reference",
            str(self.reference),
            "--check",
            "greeting",
            "--split",
            split,
        )

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

    def test_checkout_allows_slow_bulk_operations_with_bounded_deadlines(self):
        run = subprocess.run
        operations = []

        def bounded_run(command, **kwargs):
            words = command[3:]
            bulk = words[0] in ("archive", "add") or "commit" in words
            operations.append((words, kwargs["timeout"]))
            # Simulate bulk work that exceeds metadata's limit without waiting.
            # The actual small Git transaction still proves the frozen checkout.
            if bulk and kwargs["timeout"] <= 60:
                raise subprocess.TimeoutExpired(command, kwargs["timeout"])
            self.assertEqual(600 if bulk else 60, kwargs["timeout"])
            return run(command, **kwargs)

        workspace = self.root / "slow-bulk-checkout"
        with patch.object(arena.subprocess, "run", side_effect=bounded_run):
            arena.checkout(self.repo, self.base, workspace)
        self.assertEqual(1, int(arena.git(workspace, "rev-list", "--count", "HEAD")))
        self.assertEqual(
            ["archive", "add", "commit"],
            [
                next(word for word in words if word in ("archive", "add", "commit"))
                for words, timeout in operations
                if timeout == 600
            ],
        )
        self.assertTrue(any(timeout == 60 for _words, timeout in operations))

    def test_vacuous_oracle_rejected(self):
        self.oracle.write_text('print(\'{"checks":[{"name":"greeting","ok":true}]}\')\n')
        result = self.ingest()
        self.assertNotEqual(0, result.returncode)
        self.assertIn("controls failed", result.stderr)
        self.assertEqual([], self.store.cases())

    def test_every_action_but_init_names_a_missing_store_and_the_command_that_creates_it(self):
        missing = Store(self.root / "no-arena")
        for args in (
            ("cases",),
            ("report",),
            ("propose", "--cohort", "c"),
            ("compare", "--baseline", "a", "--candidate", "b"),
        ):
            with self.subTest(args[0]):
                out, err = io.StringIO(), io.StringIO()
                with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
                    with self.assertRaises(SystemExit) as stopped:
                        arena.main(["--arena", str(missing.root), *args])
                self.assertEqual(2, stopped.exception.code)
                self.assertEqual(
                    f"arena error: No arena store at {missing.root}; run autocode arena init first\n", err.getvalue()
                )
                self.assertEqual("", out.getvalue())
        self.assertFalse(missing.root.exists(), "a refused action creates nothing")

    def test_cases_lists_prepared_workloads_without_running_them(self):
        self.assertEqual([], json.loads(self.call("cases").stdout)["cases"])
        self.assertEqual(0, self.ingest().returncode)
        listing = json.loads(self.call("cases").stdout)["cases"]
        self.assertEqual(
            [{"id": "greeting", "issue_ref": "acme/demo#7", "split": "development", "base_commit": self.base}], listing
        )
        self.assertEqual([], self.store.rows())

    def test_missing_checks_and_oracle_crash_are_errors(self):
        self.assertEqual(0, self.ingest().returncode)
        case = self.store.case("greeting")
        for code, output, error in (
            (0, '{"checks":[]}', ""),
            (0, '{"checks":[{"name":"other","ok":true}]}', ""),
            (1, "", "crash"),
            (-1, "", "TIMEOUT"),
        ):
            with (
                self.subTest(output=output, error=error),
                patch.object(arena, "run_oracle", return_value=(code, output, error)),
            ):
                with self.assertRaises(ArenaError):
                    arena.evaluate(case, self.reference, 1)

    def test_live_authorization_and_workspace_override_refused(self):
        self.assertEqual(0, self.ingest().returncode)
        denied = self.call("run", "greeting", "--cohort", "baseline")
        self.assertIn("i-authorize-live-model-spend", denied.stderr)
        override = self.call("run", "greeting", "--cohort", "fixture", "--fixture", "--option=--workspace=/tmp")
        self.assertIn("unsupported runner option", override.stderr)
        provider = self.call("run", "greeting", "--cohort", "fixture", "--fixture", "--option=--provider=opencode")
        self.assertIn("bundled Codex fixture", provider.stderr)
        self.assertEqual([], self.store.rows())

    def test_worker_options_reach_the_public_taskrun_child_unchanged(self):
        self.assertEqual(0, self.ingest().returncode)
        options = (
            "--requirements-model",
            "zai-coding-plan/glm-5.3-flash",
            "--glm-model=zai-coding-plan/glm-5.3",
            "--astra-model=xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "--terra-model",
            "zai-coding-plan/glm-5.3",
            "--plan-reviewer-model=xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "--resolver-model",
            "xiaomi-token-plan-sgp/mimo-v2.6-pro",
            "--sol-model=openai/gpt-6-sol",
            "--completion-model",
            "openai/gpt-6-sol",
            "--joint-planning",
            "--reasoning-effort=high",
            "--requirements-reasoning-effort",
            "high",
            "--glm-reasoning-effort=high",
            "--plan-reviewer-reasoning-effort",
            "high",
            "--astra-reasoning-effort=high",
            "--terra-reasoning-effort",
            "high",
            "--sol-reasoning-effort=high",
            "--completion-reasoning-effort",
            "high",
            "--resolver-reasoning-effort=high",
            "--pin-model-role",
            "astra",
            "--pin-model-role=terra",
            "--pin-model-role",
            "sol",
            "--pin-model-role=completion",
        )
        calls = []

        def child_cli(argv, **kwargs):
            # Keep the real TaskRun interface and capture its child command.
            # This process seam supplies a public plan stop, never a model PASS.
            calls.append((list(argv), kwargs))
            if "--status" in argv:
                view = {
                    "status": "AWAITING_GOAL_APPROVAL",
                    "done": False,
                    "needs": {"kind": "approve_plan", "token": "fixture-plan"},
                }
                return subprocess.CompletedProcess(argv, 0, json.dumps({"view": view}), "")
            workspace = Path(argv[argv.index("--workspace") + 1])
            marker = workspace / ".autocode/runs/fixture/state.json"
            marker.parent.mkdir(parents=True)
            marker.write_text("{}\n")  # Owned fixture marker, not private state inspection.
            return subprocess.CompletedProcess(argv, 2, "waiting for plan approval\n", "")

        with patch("autocode_taskrun.run_process", side_effect=child_cli):
            result = self.call(
                "run",
                "greeting",
                "--cohort",
                "worker-options",
                "--fixture",
                *("--option=" + token for token in options),
            )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        row = json.loads(result.stdout)
        self.assertEqual("STOPPED", row["verdict"])
        self.assertEqual("fixture", row["execution_kind"])
        forwarded = ("--engine", "codex", *options)
        self.assertEqual(list(forwarded), row["options"])
        self.assertEqual(2, len(calls))
        start_argv, start_kwargs = calls[0]
        self.assertEqual(
            [
                sys.executable,
                str(ROOT / "tools/autocode.py"),
                self.store.case("greeting")["task"],
                "--in-place",
                "--no-chat",
                *forwarded,
                "--workspace",
                row["workspace"],
            ],
            start_argv,
        )
        for _, kwargs in calls:
            self.assertEqual(ROOT, kwargs["cwd"])
            self.assertEqual(1200, kwargs["timeout"])
        self.assertTrue(start_kwargs["advancing"])
        self.assertFalse(calls[1][1]["advancing"])
        self.assertIn("--status", calls[1][0])
        report = self.call("report", "--cohort", "worker-options")
        self.assertEqual(0, report.returncode, report.stderr)
        self.assertEqual(1, json.loads(report.stdout)["outcomes"]["STOPPED"])

    def test_worker_option_refusals_precede_child_launch_and_attempt_rows(self):
        self.assertEqual(0, self.ingest().returncode)
        invalid = [
            (("--requirements-model",), "expected one argument"),
            (("--resolver-model",), "expected one argument"),
            (("--pin-model-role",), "expected one argument"),
            (("--pin-model-role=builder",), "invalid choice"),
            (("--pin-model-role=astra", "--pin-model-role=builder"), "invalid choice"),
            (("--reasoning-effort=unsupported", "--reasoning-effort=high"), "invalid choice"),
            (("--run-dir=/tmp/another-run",), "unsupported runner option"),
            (("--approve-plan=another-token",), "unsupported runner option"),
            (("--max-stage-seconds=900",), "unsupported runner option"),
        ]
        for name in (
            "reasoning-effort",
            "requirements-reasoning-effort",
            "glm-reasoning-effort",
            "plan-reviewer-reasoning-effort",
            "astra-reasoning-effort",
            "terra-reasoning-effort",
            "sol-reasoning-effort",
            "completion-reasoning-effort",
            "resolver-reasoning-effort",
        ):
            invalid.append((("--" + name + "=unsupported",), "invalid choice"))
        with patch("autocode_taskrun.run_process") as child:
            for tokens, error in invalid:
                with self.subTest(tokens=tokens):
                    result = self.call(
                        "run",
                        "greeting",
                        "--cohort",
                        "refused-options",
                        "--fixture",
                        *("--option=" + token for token in tokens),
                    )
                    self.assertEqual(2, result.returncode, result.stdout + result.stderr)
                    self.assertIn(error, result.stderr)
                    self.assertEqual([], self.store.rows())
                    child.assert_not_called()

    def test_route_reasoning_and_pin_options_reach_the_runner_exactly(self):
        # #953: the existing route, reasoning and pin controls are forwarded as typed.
        self.assertEqual(0, self.ingest().returncode)
        seen = []

        class RecordingRun:
            @classmethod
            def start(cls, workspace, brief, **kwargs):
                seen.append(kwargs["options"])
                result = cls()
                result.workspace, result.run_dir = workspace, workspace / ".autocode/runs/test"
                return result

            def advance_until_input(self):
                return {"status": "AWAITING_GOAL_APPROVAL", "needs": {"kind": "approve_plan", "token": "exact"}}

            def show_goal(self):
                return "Displayed benchmark plan"

        options = (
            "--resolver-model=xiaomi/mimo-v2.6-pro",
            "--requirements-model=zai/glm-5.3",
            "--investigator-model=anthropic/claude-sonnet-5-5",
            "--investigator-reasoning-effort=high",
            "--reasoning-effort=high",
            "--sol-reasoning-effort=high",
            "--pin-model-role=astra",
            "--pin-model-role=sol",
        )
        with patch.object(arena, "TaskRun", RecordingRun):
            stopped = self.call(
                "run", "greeting", "--cohort", "routes", "--fixture", *(f"--option={o}" for o in options)
            )
        self.assertEqual(2, stopped.returncode, stopped.stdout + stopped.stderr)
        self.assertEqual([("--engine", "codex", *options)], [tuple(row) for row in seen])
        self.assertEqual(list(options), json.loads(stopped.stdout)["options"][2:])
        refused = self.call(
            "run", "greeting", "--cohort", "routes", "--fixture", "--option=--sol-reasoning-effort=extreme"
        )
        self.assertIn("invalid choice", refused.stderr)

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
        with patch.object(arena, "TaskRun", ScriptedRun), patch.object(arena, "git", wraps=arena.git) as git_receipts:
            finished = self.call(
                "run", "greeting", "--cohort", "approved-fixture", "--fixture", "--approve-benchmark-plans"
            )
        self.assertEqual(0, finished.returncode, finished.stdout + finished.stderr)
        row = json.loads(finished.stdout)
        self.assertEqual("PASS", row["verdict"])
        self.assertEqual("fixture", row["execution_kind"])
        self.assertEqual(2, len(self.store.rows()))
        self.assertTrue((Path(row["workspace"]).parent / "approved-plan.txt").is_file())
        self.assertIn("greet.py", Path(row["patch_path"]).read_text())
        retained_patch_calls = [
            call
            for call in git_receipts.call_args_list
            if call.args[1:3] == ("add", "-N") or call.args[1:2] == ("diff",)
        ]
        self.assertEqual(2, len(retained_patch_calls))
        self.assertTrue(all(call.kwargs.get("timeout") == 600 for call in retained_patch_calls))
        self.assertEqual(
            "Hello, World\n",
            subprocess.check_output([sys.executable, str(Path(row["workspace"]) / "greet.py"), "World"], text=True),
        )

    def test_patch_capture_scores_a_run_in_a_workspace_that_ignores_autocode(self):
        """A completing attempt keeps its verdict when AutoCode ignores .autocode (#1185)."""
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
                    # Leave behind exactly what a real run leaves: bookkeeping that the
                    # workspace's own exclude file hides from Git.
                    self.run_dir.mkdir(parents=True, exist_ok=True)
                    (self.run_dir / "state.json").write_text("{}\n")
                    exclude = self.workspace / ".git/info/exclude"
                    exclude.write_text(exclude.read_text() + "/.autocode/\n")
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
            finished = self.call(
                "run", "greeting", "--cohort", "ignored-bookkeeping", "--fixture", "--approve-benchmark-plans"
            )
        row = json.loads(finished.stdout)
        self.assertEqual(0, finished.returncode, finished.stdout + finished.stderr)
        self.assertEqual("PASS", row["verdict"], row.get("error"))
        retained = Path(row["patch_path"]).read_text()
        self.assertIn("greet.py", retained)
        self.assertNotIn(".autocode", retained)

    def test_append_only_attempts_and_interruption_visible(self):
        row = {"id": "x", "cohort": "baseline", "verdict": "RUNNING"}
        self.store.insert(row)
        self.assertEqual(1, policy.summarize(self.store.rows())["outcomes"]["RUNNING"])
        row["verdict"] = "ERROR"
        self.store.finish(row)
        with self.assertRaises(ArenaError):
            self.store.finish(row)

    def test_failed_start_retains_the_created_run_for_inspection(self):
        self.assertEqual(0, self.ingest().returncode)

        def start(workspace, brief, **kwargs):
            run_dir = workspace / ".autocode/runs/saved-before-timeout"
            run_dir.mkdir(parents=True)
            raise arena.TaskRunError("start did not finish within 60 s", run_dir=run_dir)

        with patch.object(arena.TaskRun, "start", side_effect=start):
            result = self.call("run", "greeting", "--cohort", "timeout", "--fixture")
        self.assertEqual(1, result.returncode)
        row = json.loads(result.stdout)
        self.assertEqual("ERROR", row["verdict"])
        self.assertTrue(Path(row["run_dir"]).is_dir())
        self.assertTrue(Path(row["run_dir"]).is_relative_to(row["workspace"]))
        self.assertEqual(row["run_dir"], self.store.rows()[0]["run_dir"])

    def test_already_complete_attempt_does_not_require_a_plan_need(self):
        self.assertEqual(0, self.ingest().returncode)
        reference = self.reference

        class CompleteRun:
            @classmethod
            def start(cls, workspace, brief, **kwargs):
                import shutil

                result = cls()
                result.run_dir = workspace / ".autocode/runs/completed"
                shutil.copyfile(reference / "greet.py", workspace / "greet.py")
                return result

            def advance_until_input(self):
                return {"status": "TASK_COMPLETE", "done": True, "needs": None}

        with patch.object(arena, "TaskRun", CompleteRun):
            result = self.call("run", "greeting", "--cohort", "complete", "--fixture", "--approve-benchmark-plans")
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertEqual("PASS", json.loads(result.stdout)["verdict"])

    def test_proposal_does_not_expose_holdout_failures_or_invent_causes(self):
        self.assertEqual(0, self.ingest().returncode)
        for case in ("greeting", "secret-holdout"):
            self.store.insert(
                {
                    "id": case,
                    "case_id": case,
                    "cohort": "old",
                    "verdict": "FALSE_COMPLETE",
                    "checks": [{"name": "greeting", "ok": False}],
                    "error": None,
                }
            )
        result = arena.proposal(self.store, "old")
        self.assertEqual(["greeting"], [r["case"] for r in result["development_failures"]])
        self.assertEqual("UNDETERMINED", result["development_failures"][0]["root_cause"])
        self.assertFalse(result["promoted"])


class PolicyTests(unittest.TestCase):
    def test_false_completion_does_not_trust_runner_or_an_oracle_crash(self):
        self.assertEqual("FALSE_COMPLETE", policy.verdict("TASK_COMPLETE", [{"ok": False}]))
        self.assertEqual("ERROR", policy.verdict("TASK_COMPLETE", [], "oracle crash"))
        self.assertEqual("STOPPED", policy.verdict("AWAITING_GOAL_APPROVAL", [{"ok": False}]))
        self.assertEqual("STOPPED", policy.verdict("WAITING_FOR_USER", [{"ok": False}]))

    def population(self):
        cases = [{"id": "r", "sha256": "r", "split": "regression"}, {"id": "h", "sha256": "h", "split": "holdout"}]

        def row(case, version, outcome):
            return {
                "case_id": case,
                "case_sha256": case,
                "version_sha256": version,
                "execution_kind": "live",
                "options": [],
                "verdict": outcome,
            }

        return (
            cases,
            [row("r", "old", "PASS"), row("h", "old", "FAIL")],
            [row("r", "new", "PASS"), row("h", "new", "PASS")],
        )

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


class RunnerOptionTests(unittest.TestCase):
    """The runner options an Arena attempt forwards (#953)."""

    def test_route_reasoning_and_pin_controls_are_accepted_and_checked(self):
        selected = arena.runner_options(
            ("--resolver-model", "m1", "--requirements-model", "m2", "--terra-reasoning-effort", "xhigh")
            + ("--pin-model-role", "astra", "--pin-model-role", "terra")
        )
        self.assertEqual(
            ("m1", "m2", "xhigh", ["astra", "terra"]),
            (
                selected.resolver_model,
                selected.requirements_model,
                selected.terra_reasoning_effort,
                selected.pin_model_role,
            ),
        )
        for refused in (
            ("--sol-reasoning-effort", "extreme"),
            ("--investigator-reasoning-effort", "extreme"),
            ("--pin-model-role", "planner"),
            ("--workspace", "/tmp"),
            ("--resume-paused",),
            ("--run-dir=/tmp/run",),
        ):
            with self.subTest(refused=refused), self.assertRaises(ArenaError):
                arena.runner_options(refused)

    def test_the_checked_choices_are_the_main_parsers(self):
        import autocode_args
        import autocode_configure

        actions = autocode_args.build_parser(None, autocode_configure.DEFAULT_ROLE_MODELS)._option_string_actions
        for name in (*arena.RUNNER_VALUES, *arena.EFFORT_FLAGS, "pin-model-role", "joint-planning"):
            self.assertIn("--" + name, actions)
        for name in arena.EFFORT_FLAGS:
            self.assertEqual(list(arena.EFFORTS), list(actions["--" + name].choices), name)
        self.assertEqual(list(arena.PIN_ROLES), list(actions["--pin-model-role"].choices))


if __name__ == "__main__":
    unittest.main()
