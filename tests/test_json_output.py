"""--json on user actions: one JSON object on stdout, everything else on stderr (#714)."""

import contextlib
import copy
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode as runner
import autocode_containment_policy as containment_policy
import autocode_goal_lifecycle as lifecycle
import autocode_goals as goals
import autocode_json_output as json_output
import autocode_support as support
import autocode_util as util
from goal_fixtures import body, seed_greeting_workspace

SCHEMA = json.loads((Path(__file__).resolve().parents[1] / "docs/schemas/status-view.schema.json").read_text())


class JsonOutputTests(unittest.TestCase):
    """The stream routing, with a stand-in status payload."""

    def setUp(self):
        self.addCleanup(json_output.restore)

    def args(self, **flags):
        return SimpleNamespace(json=True, status=False, explain=False, dry_run=False, **flags)

    def test_under_json_everything_printed_goes_to_stderr_and_the_streams_come_back(self):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            json_output.begin(self.args())
            print("Saved; no agent launched by this action.")
            print("Input rejected: stale token", file=sys.stderr)
            self.assertEqual(2, json_output.finish(2))
            json_output.restore()
            self.assertIs(out, sys.stdout)
            self.assertIs(err, sys.stderr)
        result = json.loads(out.getvalue())["result"]
        self.assertEqual(
            {
                "exit_code": 2,
                "saved": False,
                "message": "Saved; no agent launched by this action.",
                "error": {"code": "input_rejected", "message": "stale token"},
            },
            result,
        )
        lines = err.getvalue().strip().splitlines()
        self.assertEqual("Saved; no agent launched by this action.", lines[0])
        self.assertEqual({"error": result["error"]}, json.loads(lines[-1]))

    def test_one_object_only_and_the_read_commands_are_left_alone(self):
        out = io.StringIO()
        with (
            contextlib.redirect_stdout(out),
            contextlib.redirect_stderr(io.StringIO()),
            patch.object(json_output.status_command, "render", lambda *_: print('{"status": "TASK_COMPLETE"}')),
        ):
            json_output.begin(self.args())
            json_output.status(runner, {}, None, None, None)
            json_output.finish(0)  # the status payload was the one object
            json_output.restore()
        self.assertEqual({"status": "TASK_COMPLETE"}, json.loads(out.getvalue()))
        for read in ("status", "explain", "dry_run"):
            with self.subTest(read=read):
                json_output.begin(SimpleNamespace(**{**vars(self.args()), read: True}))
                self.assertNotIsInstance(sys.stdout, json_output._Copy)
        json_output.begin(SimpleNamespace(json=False))
        self.assertNotIsInstance(sys.stdout, json_output._Copy)


class JsonActionCliTests(unittest.TestCase):
    """The CLI on a saved run whose plan waits for approval, in process (autocode.main)."""

    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        # An empty config home, so a new run never takes the developer's default_provider (#1933).
        config_home = tempfile.TemporaryDirectory()
        self.addCleanup(config_home.cleanup)
        environment = patch.dict(
            os.environ,
            {
                "AUTOCODE_HOME": str(self.root / "registry-home"),
                "XDG_CONFIG_HOME": config_home.name,
                "AUTOCODE_PROVIDER": "",
            },
        )
        environment.start()
        self.addCleanup(environment.stop)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        seed_greeting_workspace(self.root)
        subprocess.run(["git", "-C", str(self.root), "add", "greet.py", "test_greeting.py"], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "-c", "user.name=F", "-c", "user.email=f@example.test"]
            + ["commit", "-qm", "fixture"],
            check=True,
        )
        with (self.root / ".git/info/exclude").open("a") as exclude:
            exclude.write("/registry-home/\n")
        self.run_dir = self.root / ".autocode/runs/fixture"
        self.run_dir.mkdir(parents=True)
        self.local = {"auth_mode": "fixture"}
        self.state = {
            "version": 2,
            "workspace": str(self.root),
            "task": "Make a greeting tool",
            "status": "RUNNING",
            "iteration": 1,
            "sessions": {},
            "stages": [],
            "history": [],
            "next_stage": "terra",
            "acceptance_criteria": [],
            "settings": {
                "roles": {r: {"model": r, "reasoning_effort": "high"} for r in ("astra", "terra", "sol")},
                "transport_identity": self.local,
                "headroom": {"enabled": False},
                "context_soft_tokens": 10000,
                "limits": {"iteration_ceiling": 5, "max_seconds": None, "no_progress_batches": 3},
            },
        }
        lifecycle.migrate(self.state)
        lifecycle.install_draft(self.state, body(), origin="test")
        support.atomic_json(self.run_dir / "state.json", self.state)
        self.invoke("--show-goal")  # publishes the plan request a person approves
        self.token = goals.token(self.state["goal_contract"])

    @contextlib.contextmanager
    def cli(self, *args):
        """autocode --workspace FIXTURE ARGS, in process and launching no agent; yields (stdout, stderr)."""
        with (
            patch.object(sys, "argv", ["autocode", "--workspace", str(self.root), *args]),
            patch.object(support, "assert_no_legacy_process"),
            patch.object(support, "local_settings", return_value=self.local),
            patch.object(runner, "run_role", side_effect=AssertionError("No agent may launch")),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            yield stdout, stderr

    def invoke(self, *args):
        """autocode ARGS on the fixture run; (exit code, stdout, stderr)."""
        with self.cli("--run-dir", str(self.run_dir), *args) as (stdout, stderr):
            code = runner.main()
        self.state = support.read(self.run_dir / "state.json")
        return code, stdout.getvalue(), stderr.getvalue()

    def raised(self, *args):
        """autocode ARGS, which main ends by raising the error cli() prints as ``autocode: ...`` (exit 2);
        (that error's text, stdout, stderr)."""
        with self.cli(*args) as (stdout, stderr), self.assertRaises(ValueError) as caught:
            runner.main()
        return str(caught.exception), stdout.getvalue(), stderr.getvalue()

    def test_an_action_prints_the_status_payload_with_its_result(self):
        code, stdout, stderr = self.invoke("--approve-goal", self.token, "--json")
        self.assertEqual(0, code, stderr)
        payload = json.loads(stdout)  # exactly one object: anything else would not parse
        util.validate_schema(payload, SCHEMA)
        self.assertEqual(self.state["status"], payload["status"])
        self.assertIn("needs", payload["view"])
        result = payload["result"]
        self.assertEqual((0, True, None), (result["exit_code"], result["saved"], result["error"]))
        self.assertIn("Saved; no agent launched by this action.", result["message"])
        self.assertIn("Saved; no agent launched by this action.", stderr)  # the prose a person reads

    def test_a_refused_action_reports_its_error_as_json_on_both_streams(self):
        code, stdout, stderr = self.invoke("--approve-goal", "r9:stale", "--json")
        self.assertEqual(2, code)
        util.validate_schema(json.loads(stdout), SCHEMA)
        error = json.loads(stdout)["result"]["error"]
        self.assertEqual("input_rejected", error["code"])
        self.assertTrue(error["message"])
        # The status payload's cost line still reaches a person on stderr, never the refusal (#721).
        self.assertNotIn("Cost:", error["message"])
        self.assertIn("Cost:", stderr)
        self.assertEqual({"error": error}, json.loads(stderr.strip().splitlines()[-1]))
        self.assertFalse(goals.approved(self.state))

    def test_a_refusal_before_the_run_loads_still_prints_one_result(self):
        code, stdout, _ = self.invoke("--feedback", "  ", "--json")
        self.assertEqual(2, code)
        self.assertEqual(
            {
                "result": {
                    "exit_code": 2,
                    "saved": False,
                    "message": "",
                    "error": {"code": "input_rejected", "message": "Feedback and follow-ups must be nonempty"},
                }
            },
            json.loads(stdout),
        )

    def test_a_setting_the_saved_run_cannot_take_prints_its_payload_with_the_refusal(self):
        """configure's refusal still ends the invocation as ``autocode: ...`` on stderr, exit 2; under --json
        stdout carries the saved run's payload with that refusal as ``result``; the refused setting is not saved."""
        saved = (self.run_dir / "state.json").read_bytes()
        on_run = ("--run-dir", str(self.run_dir))
        for setting in (("--single-model", "gpt-x"), ("--spec-first",)):
            with self.subTest(setting=setting[0]):
                refusal, stdout, stderr = self.raised(*on_run, *setting, "--json")
                payload = json.loads(stdout)  # exactly one object
                util.validate_schema(payload, SCHEMA)
                self.assertEqual(
                    (str(self.run_dir), "AWAITING_GOAL_APPROVAL"), (payload["run_dir"], payload["view"]["status"])
                )
                result = payload["result"]
                self.assertEqual((2, False), (result["exit_code"], result["saved"]))
                self.assertEqual({"code": "input_rejected", "message": refusal}, result["error"])
                self.assertTrue(refusal.startswith(f"{setting[0]} "), refusal)
                # main's last line, after the status hints; cli() then prints `autocode: ...`.
                self.assertEqual({"error": result["error"]}, json.loads(stderr.strip().splitlines()[-1]))
                self.assertEqual(saved, (self.run_dir / "state.json").read_bytes())
                # Without --json nothing changes: the same error, and nothing on stdout.
                self.assertEqual((refusal, ""), self.raised(*on_run, *setting)[:2])
        # A new run refused before its first checkpoint is saved prints the result alone.
        new_run = ("Make another tool", "--in-place", "--single-model", "gpt-x", "--sol-model", "y", "--json")
        refusal, stdout, _ = self.raised(*new_run)
        error = {"code": "input_rejected", "message": refusal}
        self.assertEqual(
            {"result": {"exit_code": 2, "saved": False, "message": "", "error": error}}, json.loads(stdout)
        )
        # Without --in-place the task worktree and branch are made before configure refuses: result.message
        # carries what the invocation printed about them; pre-save cleanup then removes both.
        refusal, stdout, stderr = self.raised(*(arg for arg in new_run if arg != "--in-place"))
        result = json.loads(stdout)["result"]
        self.assertEqual((2, False), (result["exit_code"], result["saved"]))
        self.assertEqual({"code": "input_rejected", "message": refusal}, result["error"])
        created, branch, starting = result["message"].splitlines()
        self.assertTrue(created.startswith("Task worktree: "), created)
        worktree = Path(created.removeprefix("Task worktree: "))
        self.assertEqual(self.root / ".autocode/worktrees", worktree.parent)
        self.assertFalse(worktree.exists(), worktree)
        remaining = subprocess.run(
            ["git", "-C", str(self.root), "branch", "--list", f"autocode/{worktree.name}"],
            check=True,
            capture_output=True,
            text=True,
        )
        self.assertEqual("", remaining.stdout)
        self.assertIn(
            f"Removed the unused task worktree and branch autocode/{worktree.name}; no run was saved.", stderr
        )
        self.assertEqual(f"Branch: autocode/{worktree.name}", branch)
        self.assertEqual("Starting from committed HEAD; the original checkout is unchanged.", starting)

    def test_a_tool_containment_refusal_prints_its_payload_with_the_refusal(self):
        """containment_policy refuses as the run loads: --allow-uncontained-tools on a run with no built-in OpenCode
        stage, or a built-in OpenCode run without it where the kernel tool boundary is unavailable. Under --json
        that prints like any refused setting; the refusal's text, cli()'s ``autocode: ...`` and the saved run
        are unchanged."""
        on_run = ("--run-dir", str(self.run_dir))
        opencode = copy.deepcopy(self.state)  # the same run on built-in OpenCode
        opencode["settings"].update(engine="opencode", provider="opencode")
        for route in opencode["settings"]["roles"].values():
            route["model"] = "openai/" + route["model"]
        problem = "fixture host has no kernel sandbox"
        for name, state, flags, expected in (
            (
                "flag on a run it does not apply to",
                self.state,
                (containment_policy.FLAG,),
                f"{containment_policy.FLAG} applies only to built-in OpenCode runs; "
                "this run launches no kernel-contained OpenCode stage",
            ),
            ("OpenCode run without the flag", opencode, (), containment_policy.refusal(problem)),
        ):
            with (
                self.subTest(name),
                patch.object(containment_policy.tool_containment, "unavailable", return_value=problem),
            ):
                support.atomic_json(self.run_dir / "state.json", state)
                saved = (self.run_dir / "state.json").read_bytes()
                refusal, stdout, stderr = self.raised(*on_run, *flags, "--json")
                self.assertEqual(expected, refusal)
                payload = json.loads(stdout)  # exactly one object; master printed nothing
                util.validate_schema(payload, SCHEMA)
                self.assertEqual(str(self.run_dir), payload["run_dir"])
                error = {"code": "input_rejected", "message": refusal}
                self.assertEqual({"exit_code": 2, "saved": False, "message": "", "error": error}, payload["result"])
                self.assertEqual({"error": error}, json.loads(stderr.strip().splitlines()[-1]))
                self.assertEqual(saved, (self.run_dir / "state.json").read_bytes())
                # Without --json nothing changes: the same error, and nothing on stdout.
                self.assertEqual((refusal, ""), self.raised(*on_run, *flags)[:2])
        # A new run refused before its first checkpoint prints the result alone.
        refusal, stdout, _ = self.raised(
            "Make another tool", "--in-place", "--engine", "codex", containment_policy.FLAG, "--json"
        )
        self.assertTrue(refusal.startswith(f"{containment_policy.FLAG} applies only"), refusal)
        error = {"code": "input_rejected", "message": refusal}
        self.assertEqual(
            {"result": {"exit_code": 2, "saved": False, "message": "", "error": error}}, json.loads(stdout)
        )

    def test_any_other_error_after_the_run_loads_still_prints_nothing_on_stdout(self):
        """main reports only a refused setting (run_setup.SettingRefused). Any other error cli() prints as
        ``autocode: ...`` still leaves stdout empty under --json, though the run has loaded: protected_oracles'
        refusal of --revise-protected-tests outside a saved validation pause (a ValueError), or a failed registry
        update, which saves PAUSED_REGISTRY and raises (a RuntimeError)."""
        registry = runner.run_setup.registry
        for flags, failure, expected in (
            (
                ("--revise-protected-tests", str(self.root / "revision.json")),
                contextlib.nullcontext(),
                (ValueError, "--revise-protected-tests requires a saved pause and --resume-paused"),
            ),
            (
                (),
                patch.object(registry, "register_run", side_effect=registry.RegistryError("fixture", "unreachable")),
                (support.Paused, f"Registry registration failed for {self.run_dir}: unreachable"),
            ),
        ):
            with self.subTest(expected[0].__name__):
                with (
                    failure,
                    self.cli("--run-dir", str(self.run_dir), *flags, "--json") as (stdout, stderr),
                    self.assertRaises((ValueError, RuntimeError)) as caught,
                ):
                    runner.main()
                self.assertEqual(expected, (type(caught.exception), str(caught.exception)))
                self.assertEqual("", stdout.getvalue())
                self.assertNotIn('{"error"', stderr.getvalue())

    def test_a_run_stopped_inside_the_build_loop_prints_its_saved_payload_with_the_result(self):
        self.assertEqual(0, self.invoke("--approve-goal", self.token)[0])
        (self.run_dir / "pause-requested").write_text("")  # what --pause-after-stage and a person's pause leave
        code, stdout, stderr = self.invoke("--json")
        self.assertEqual(2, code, stderr)
        payload = json.loads(stdout)
        util.validate_schema(payload, SCHEMA)
        self.assertEqual(("PAUSED_REQUESTED", "PAUSED_REQUESTED"), (self.state["status"], payload["view"]["status"]))
        self.assertEqual("resume", payload["view"]["needs"]["kind"])
        result = payload["result"]
        self.assertEqual((2, False, None), (result["exit_code"], result["saved"], result["error"]))

    def test_a_run_stopped_by_its_iteration_or_time_limit_prints_its_saved_payload_with_the_result(self):
        waiting = self.state  # the plan waits for approval
        for pause, limit in (
            ("PAUSED_ITERATION_LIMIT", {"iteration_ceiling": 0}),
            ("PAUSED_TIME_LIMIT", {"max_seconds": 60}),
        ):
            with self.subTest(pause):
                state = copy.deepcopy(waiting)
                state["settings"]["limits"].update(limit)
                state["active_seconds"] = 60
                support.atomic_json(self.run_dir / "state.json", state)
                self.assertEqual(0, self.invoke("--approve-goal", self.token)[0])
                code, stdout, stderr = self.invoke("--json")
                self.assertEqual(2, code, stderr)
                payload = json.loads(stdout)
                util.validate_schema(payload, SCHEMA)
                # AutoResolver cannot raise a saved limit, so it asks the person (an operational stop).
                view = payload["view"]
                self.assertEqual(("WAITING_FOR_USER", "autocode"), (view["status"], view["pause_category"]))
                self.assertEqual(
                    (pause, "operational_exhaustion"), (view["needs"]["pause_origin"], view["needs"]["resolver_scope"])
                )
                result = payload["result"]
                self.assertEqual((2, False, None), (result["exit_code"], result["saved"], result["error"]))

    def test_a_run_stopped_for_its_environment_or_provider_prints_its_saved_payload_with_the_result(self):
        """Environment and provider pauses keep their category under --status and --json (#714)."""
        self.assertEqual(0, self.invoke("--approve-goal", self.token)[0])
        approved = self.state
        for pause, category, after in (
            # Auth stays on resume. A rate limit asks for corrective information, like a saved limit.
            ("PAUSED_AUTH", "your_environment", ("PAUSED_AUTH", "resume", None)),
            ("PAUSED_RATE_LIMIT", "model_or_provider", ("WAITING_FOR_USER", "answer", "operational_exhaustion")),
        ):
            with self.subTest(pause):
                state = copy.deepcopy(approved)
                state["status"] = pause
                state["stop_reason"] = f"fixture {pause}"
                support.atomic_json(self.run_dir / "state.json", state)
                code, stdout, stderr = self.invoke("--status")
                self.assertEqual(0, code, stderr)
                status = json.loads(stdout)
                util.validate_schema(status, SCHEMA)
                self.assertNotIn("result", status)
                self.assertEqual(
                    (pause, category, "resume"),
                    (status["view"]["status"], status["view"]["pause_category"], status["view"]["needs"]["kind"]),
                )
                self.assertIn("--resume-paused", status["view"]["next_command"])
                code, stdout, stderr = self.invoke("--json")
                self.assertEqual(2, code, stderr)
                payload = json.loads(stdout)
                util.validate_schema(payload, SCHEMA)
                view = payload["view"]
                status_after, need_kind, scope = after
                self.assertEqual(
                    (status_after, category, need_kind),
                    (view["status"], view["pause_category"], view["needs"]["kind"]),
                )
                if scope is not None:
                    self.assertEqual((pause, scope), (view["needs"]["pause_origin"], view["needs"]["resolver_scope"]))
                else:
                    self.assertEqual(
                        (view["status"], view["needs"]),
                        (status["view"]["status"], status["view"]["needs"]),
                    )
                result = payload["result"]
                self.assertEqual((2, False, None), (result["exit_code"], result["saved"], result["error"]))

    def test_without_json_an_action_prints_its_prose(self):
        code, stdout, _ = self.invoke("--approve-goal", self.token)
        self.assertEqual(0, code)
        self.assertIn("Saved; no agent launched by this action.", stdout)
        with self.assertRaises(ValueError):
            json.loads(stdout)


if __name__ == "__main__":
    unittest.main()
