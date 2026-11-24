"""Controller isolation through the public CLI and TaskRun interface."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_build_loop as build_loop
import autocode_containment_policy as containment_policy
import autocode_controller_isolation as isolation
import autocode_run_setup as run_setup
import autocode_run_view as run_view
import autocode_taskrun as taskrun
import autocode_tool_containment as containment
from providers import kilocode, opencode

from . import opencode_fixture_cli as fixture_cli
from . import test_subprocess as fixtures
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


class RequirementsAdmissionTests(unittest.TestCase):
    def state(self, **changes):
        state = {
            "next_stage": "requirements_gather",
            "workflow": {"kind": "discuss"},
            "settings": {"engine": "opencode"},
        }
        state.update(changes)
        return state

    def test_admits_only_requirements_without_build_authority(self):
        for stage in isolation.REQUIREMENTS_STAGES:
            for kind in (None, "discuss"):
                with self.subTest(stage=stage, kind=kind):
                    self.assertTrue(
                        isolation.saved_requirements_only(self.state(next_stage=stage, workflow={"kind": kind}))
                    )

    def test_refuses_a_requirements_attempt_that_would_dispatch_a_build_stage(self):
        active = {"stage": "requirements_gather", "role": "requirements_gather", "route_role": "requirements"}
        for stage in ("orchestrator", "builder", "draft_plan", None):
            with self.subTest(stage=stage):
                self.assertFalse(isolation.saved_requirements_only(self.state(active_stage=active, next_stage=stage)))

    def test_refuses_retained_build_and_repair_authority(self):
        for key in isolation.BUILD_AUTHORITY_KEYS:
            with self.subTest(key=key):
                value = ["AC1"] if key == "acceptance_criteria" else {"id": "retained-build-authority"}
                self.assertFalse(isolation.saved_requirements_only(self.state(**{key: value})))

    def test_fresh_start_exception_is_only_for_run_action_initialization(self):
        fresh = {
            "status": "RUNNING",
            "next_stage": "plan_task",
            "stages": [],
            "settings": {"engine": "opencode"},
        }
        self.assertFalse(isolation.saved_requirements_only(fresh))
        self.assertTrue(isolation.saved_requirements_only(fresh, allow_fresh_start=True))
        self.assertFalse(
            isolation.saved_requirements_only({**fresh, "current_task": {"id": "build"}}, allow_fresh_start=True)
        )

    def test_admits_same_stage_read_only_report_repair(self):
        cases = (
            (
                {"stage": "requirements_gather", "role": "requirements_gather", "route_role": "requirements"},
                {
                    "stage": "requirements_gather_report_repair",
                    "role": "requirements_gather",
                    "route_role": "requirements",
                    "report_only": True,
                },
                {"engine": "opencode"},
            ),
            (
                {"stage": "requirements_gather", "role": "requirements"},
                {
                    "stage": "requirements_gather_report_repair",
                    "role": "requirements",
                    "original_stage": "requirements_gather",
                    "report_only": True,
                },
                {"engine": "opencode", "joint_planning": True},
            ),
            (
                {"stage": "requirements", "role": "requirements"},
                None,
                {"engine": "opencode", "planning_flow": "v2"},
            ),
        )
        for original, latest, settings in cases:
            for rejected in (None, latest):
                with self.subTest(original=original, rejected=rejected):
                    repair = {"original": original}
                    if rejected is not None:
                        repair["latest_rejected"] = {**rejected, "original_stage": original["stage"]}
                    state = self.state(
                        next_stage=original["stage"],
                        settings=settings,
                        stages=[original],
                        pending_report_repair=repair,
                    )
                    self.assertTrue(isolation.saved_requirements_only(state))

    def test_refuses_a_report_repair_with_another_route_or_stage(self):
        original = {"stage": "requirements_gather", "role": "requirements_gather", "route_role": "requirements"}
        rejected = {
            "stage": "requirements_gather_report_repair",
            "role": "requirements_gather",
            "route_role": "requirements",
            "original_stage": "requirements_gather",
            "report_only": True,
        }
        cases = (
            {"original": {**original, "stage": "draft_plan", "role": "planner", "route_role": "planner"}},
            {"original": {**original, "role": "builder"}},
            {"original": {**original, "route_role": "builder"}},
            {"original": original, "latest_rejected": {**rejected, "report_only": False}},
            {"original": original, "latest_rejected": {**rejected, "role": "builder"}},
            {"original": original, "latest_rejected": {**rejected, "route_role": "builder"}},
            {"original": original, "latest_rejected": {**rejected, "original_stage": "draft_plan"}},
            {"original": original, "latest_rejected": {**rejected, "stage": "draft_plan_report_repair"}},
        )
        for repair in cases:
            with self.subTest(repair=repair):
                self.assertFalse(isolation.saved_requirements_only(self.state(pending_report_repair=repair)))
        self.assertFalse(
            isolation.saved_requirements_only(
                self.state(next_stage="draft_plan", pending_report_repair={"original": original})
            )
        )

    def test_admits_real_interrupted_requirements_records_and_their_report_repairs(self):
        cases = (
            {"stage": "requirements_gather", "role": "requirements_gather"},
            {"stage": "requirements_gather", "role": "requirements_gather", "route_role": "requirements"},
            {"stage": "requirements_gather", "role": "requirements", "route_role": "requirements"},
            {
                "stage": "requirements_gather_report_repair",
                "role": "requirements_gather",
                "route_role": "requirements",
                "original_stage": "requirements_gather",
            },
            {"stage": "requirements", "role": "requirements", "route_role": "requirements"},
            {
                "stage": "requirements_report_repair",
                "role": "requirements",
                "route_role": "requirements",
                "original_stage": "requirements",
            },
        )
        for record in cases:
            with self.subTest(record=record):
                settings = {"engine": "opencode"}
                if record["stage"] == "requirements" or record["stage"].startswith("requirements_report_repair"):
                    settings["planning_flow"] = "v2"
                elif record["role"] == "requirements":
                    settings["joint_planning"] = True
                state = self.state(active_stage=record, settings=settings)
                self.assertTrue(isolation.saved_requirements_only(state))

    def test_admits_requirements_review_wait_and_completed_discussion(self):
        waiting = self.state(
            next_stage="draft_plan",
            status="WAITING_FOR_USER",
            pending_questions=[{"id": "requirements-review-123"}],
            requirements_handoff={"report": {"job": "discuss"}},
            stages=[{"stage": "requirements_gather"}],
        )
        self.assertTrue(isolation.saved_requirements_only(waiting))
        complete = self.state(next_stage="draft_plan", status="TASK_COMPLETE", stages=[{"stage": "requirements"}])
        self.assertTrue(isolation.saved_requirements_only(complete))

    def test_refuses_plan_review_wait_and_any_completed_build_stage(self):
        plan_wait = self.state(
            next_stage="draft_plan",
            status="WAITING_FOR_USER",
            pending_questions=[{"id": "plan-review-123"}],
            requirements_handoff={"report": {"job": "build"}},
            stages=[{"stage": "requirements_gather"}],
        )
        self.assertFalse(isolation.saved_requirements_only(plan_wait))
        built = self.state(status="TASK_COMPLETE", stages=[{"stage": "requirements_gather"}, {"stage": "builder"}])
        self.assertFalse(isolation.saved_requirements_only(built))

    def test_refuses_active_build_and_uncontained_states(self):
        cases = (
            {"next_stage": "draft_plan"},
            {"workflow": {"kind": "build"}},
            {"active_stage": {"stage": "draft_plan", "role": "planner"}},
            {"active_stage": {"stage": "requirements_gather", "role": "builder"}},
            {"active_stage": {"stage": "requirements_gather", "role": "requirements_gather", "route_role": "builder"}},
            {
                "active_stage": {
                    "stage": "requirements_gather",
                    "role": "requirements_gather",
                    "original_stage": "draft_plan",
                }
            },
            {"active_stage": {"stage": None}},
            {"build_worktree": {"workspace": "/task"}},
            {"base_commit": None},
            {"project_workspace": "/project"},
            {"task_branch": "task/build"},
            {"workspace_bootstrap": {"project": "/project"}},
            {"launch_sources": {"base_commit": None}},
            {"settings": {"engine": "opencode", "allow_uncontained_tools": True}},
            {"settings": {"engine": "opencode", "task_preflight": {"body": {"checks": []}}}},
            {"task_preflight": {"receipt": "/run/preflight.json"}},
            {"pending_report_repair": {"original": {"stage": "draft_plan", "report_only": True}}},
        )
        for changes in cases:
            with self.subTest(changes=changes):
                self.assertFalse(isolation.saved_requirements_only(self.state(**changes)))


class RequirementsContainmentTests(unittest.TestCase):
    def test_unavailable_boundary_is_refused_without_saving_acceptance(self):
        state = {}
        settings = {"engine": "opencode"}
        with mock.patch.object(containment_policy.tool_containment, "unavailable", return_value="sandbox unavailable"):
            with self.assertRaisesRegex(ValueError, "Requirements-only.*sandbox unavailable"):
                containment_policy.configure_requirements(
                    state, settings, allow=False, adapter=opencode, workspace=Path("/project")
                )
        self.assertEqual({}, state)
        self.assertEqual({"engine": "opencode"}, settings)

    def test_saved_uncontained_acceptance_is_refused(self):
        state = {}
        settings = {"engine": "opencode", "allow_uncontained_tools": True}
        with self.assertRaisesRegex(ValueError, "Requirements-only.*saved run accepted uncontained tools"):
            containment_policy.configure_requirements(
                state, settings, allow=False, adapter=opencode, workspace=Path("/project")
            )
        self.assertEqual({}, state)

    def test_kilo_requirements_uses_its_exact_configured_executable(self):
        provider = kilocode.KiloProvider(
            {"name": "kilocode", "command": ["/opt/kilo", "run"], "roles": {}}, Path("/unused-kilo.toml")
        )
        settings = {
            "engine": "opencode",
            "provider": "kilocode",
            "roles": {"requirements": {"engine": "opencode", "provider": "kilocode"}},
        }
        environment = {"PATH": "/bin"}
        with (
            mock.patch.object(provider, "_preflight_environment", return_value=environment),
            mock.patch.object(containment, "unavailable", return_value=None) as unavailable,
        ):
            containment_policy.configure_requirements(
                {}, settings, allow=False, adapter=provider, workspace=Path("/project")
            )
        unavailable.assert_called_once_with(
            Path("/project"),
            command="/opt/kilo",
            environment=environment,
            supported=containment.kilo_versions(),
            label="Kilo",
        )

    def test_requirements_admission_checks_its_route_not_another_opencode_role(self):
        settings = {
            "engine": "opencode",
            "roles": {
                "requirements": {"engine": "codex"},
                "builder": {"engine": "opencode"},
            },
        }
        with (
            mock.patch.object(opencode, "containment_problem") as check,
            self.assertRaisesRegex(ValueError, "this route is not covered"),
        ):
            containment_policy.configure_requirements(
                {}, settings, allow=False, adapter=opencode, workspace=Path("/project")
            )
        check.assert_not_called()

    def test_requirements_admission_refuses_a_configured_provider_without_the_boundary(self):
        with self.assertRaisesRegex(ValueError, "this route is not covered"):
            containment_policy.configure_requirements(
                {},
                {"engine": "opencode", "provider": "custom"},
                allow=False,
                adapter=SimpleNamespace(CONFIGURED=True),
                workspace=Path("/project"),
            )

    def test_requirements_adapter_keeps_the_base_kilo_facade_without_a_role_binding(self):
        provider = kilocode.KiloProvider(
            {"name": "kilocode", "command": ["/opt/kilo", "run"], "roles": {}}, Path("/unused-kilo.toml")
        )
        settings = {
            "engine": "opencode",
            "provider": "kilocode",
            "roles": {"requirements": {"engine": "opencode", "provider": "kilocode"}},
        }
        self.assertIs(provider, run_setup.requirements_adapter(settings, provider, Path("/project")))

    def test_requirements_adapter_selects_the_role_binding_instead_of_the_base_provider(self):
        identity = {
            "engine": "kilo_role",
            "identity_version": 1,
            "executable": "/opt/kilo",
            "version": "7.8.27",
            "config_path": "/providers/kilo-role.toml",
            "config_sha256": "a" * 64,
        }
        settings = {
            "engine": "opencode",
            "provider": "base",
            "roles": {"requirements": {"engine": "opencode", "provider": "kilo_role"}},
            "transport_identities": {"role:requirements": {"provider": "kilo_role", "identity": identity}},
        }
        selected = SimpleNamespace(
            CONFIGURED=True,
            local_settings=mock.Mock(return_value=identity),
            transport_drift=mock.Mock(return_value=False),
        )
        with mock.patch.object(run_setup.role_transport.providers, "resolve", return_value=selected):
            self.assertIs(selected, run_setup.requirements_adapter(settings, opencode, Path("/project")))
        selected.local_settings.assert_called_once_with(Path("/project"))
        selected.transport_drift.assert_called_once_with(identity, identity)

    def test_prepared_boundary_must_name_the_exact_workspace_run_and_store(self):
        workspace = Path("/project")
        run_dir = workspace / ".autocode" / "runs" / "one"
        policy = {
            "backend": "seatbelt",
            "allow_write": False,
            "workspace": str(workspace),
            "private_reads": {
                "run_dir": str(run_dir),
                "denied": [str(workspace / ".autocode" / "runs")],
            },
        }
        self.assertIsNone(
            containment_policy.requirements_boundary_problem(policy, workspace=workspace, run_dir=run_dir)
        )
        cases = (
            ({**policy, "allow_write": True}, "not read-only"),
            ({**policy, "workspace": "/other"}, "another workspace"),
            (
                {**policy, "private_reads": {**policy["private_reads"], "run_dir": "/project/.autocode/runs/two"}},
                "another run",
            ),
            (
                {**policy, "private_reads": {**policy["private_reads"], "denied": ["/other/.autocode/runs"]}},
                "run store",
            ),
            ({"backend": "seatbelt", "allow_write": False, "workspace": str(workspace)}, "another run"),
        )
        for changed, message in cases:
            with self.subTest(message=message):
                self.assertIn(
                    message,
                    containment_policy.requirements_boundary_problem(changed, workspace=workspace, run_dir=run_dir),
                )


class LocalRequirementsLimitTests(unittest.TestCase):
    def test_planning_cap_stops_local_requirements_before_dispatch(self):
        class LoopExit(Exception):
            pass

        dispatched = []

        def drive(current, dispatch, *, before, **kwargs):
            before(current)
            dispatched.append(current["next_stage"])

        state = {
            "iteration": 1,
            "next_stage": "requirements_gather",
            "settings": {"limits": {"iteration_ceiling": None, "max_seconds": 0, "planning_calls": 1}},
            "stages": [{"stage": "requirements_gather", "started_at": "2026-11-23T00:00:00+00:00"}],
        }
        runner = SimpleNamespace(
            orchestrator=SimpleNamespace(drive=drive, LoopExit=LoopExit),
            native_calls=None,
            consume_interventions=mock.Mock(return_value=False),
            timeout_recovery_guard=mock.Mock(),
            iteration_limit_reached=build_loop.iteration_limit_reached,
            recover_default_budget=mock.Mock(return_value=False),
            now=lambda: "2026-11-23T00:01:00+00:00",
            write_json=mock.Mock(),
        )
        args = SimpleNamespace(unit=None, _local_requirements_only=True)
        with (
            mock.patch.object(build_loop.controller_isolation, "require_local_requirements"),
            mock.patch.object(build_loop.model_preflight, "check") as preflight,
            mock.patch.object(build_loop.cost, "exceeded", return_value=None),
            mock.patch.object(build_loop.early_baseline, "reconcile_at_boundary"),
            mock.patch.object(build_loop.no_progress_recovery, "conclude_exhausted", return_value=False),
            mock.patch.object(build_loop.classification_recovery, "conclude", return_value=False),
            self.assertRaises(build_loop.support.Paused) as paused,
        ):
            build_loop.run(runner, args, state, Path("/run/state.json"), Path("/run"), Path("/project"))
        self.assertEqual("PAUSED_PLANNING_CALL_LIMIT", paused.exception.status)
        preflight.assert_not_called()
        self.assertEqual([], dispatched)

    def test_local_requirements_cannot_dispatch_the_orchestrator(self):
        class LoopExit(Exception):
            pass

        def drive(current, dispatch, *, before, **kwargs):
            before(current)
            dispatch(current, "orchestrator")

        state = {
            "iteration": 1,
            "next_stage": "requirements_gather",
            "workflow": {"kind": "discuss"},
            "settings": {"limits": {"iteration_ceiling": None, "max_seconds": 0, "planning_calls": 10}},
            "stages": [],
        }
        runner = SimpleNamespace(
            orchestrator=SimpleNamespace(drive=drive, LoopExit=LoopExit, SKIP=None),
            native_calls=None,
            consume_interventions=mock.Mock(return_value=False),
            timeout_recovery_guard=mock.Mock(),
            iteration_limit_reached=build_loop.iteration_limit_reached,
            recover_default_budget=mock.Mock(return_value=False),
            now=lambda: "2026-11-23T00:01:00+00:00",
            write_json=mock.Mock(),
        )
        args = SimpleNamespace(unit=None, _local_requirements_only=True)
        with (
            tempfile.TemporaryDirectory() as directory,
            mock.patch.object(
                build_loop.controller_isolation,
                "setup_view",
                return_value={"ready": False, "requirements_ready": True},
            ),
            mock.patch.object(build_loop.model_preflight, "check") as preflight,
            mock.patch.object(build_loop.cost, "exceeded", return_value=None),
            mock.patch.object(build_loop.early_baseline, "reconcile_at_boundary"),
            mock.patch.object(build_loop.no_progress_recovery, "conclude_exhausted", return_value=False),
            mock.patch.object(build_loop.classification_recovery, "conclude", return_value=False),
            mock.patch.object(build_loop.progressive, "guard_dispatch"),
            mock.patch.object(build_loop.autopilot.builder_failure, "dispatch_guard"),
            mock.patch.object(build_loop.milestones, "dispatch_guard"),
            mock.patch.object(build_loop.workflow, "dispatch_guard"),
            mock.patch.object(build_loop.autopilot, "admit_validation"),
            mock.patch.object(build_loop.planning, "is_planning", return_value=False),
            mock.patch.object(build_loop.completion_result, "before_review", return_value=False),
            mock.patch.object(build_loop.launch_inputs, "guard"),
            mock.patch.object(build_loop.autopilot, "revalidate_resolver", return_value=False),
            mock.patch.object(build_loop.recovery_dispatch, "dispatch_orchestrator") as dispatch_orchestrator,
            self.assertRaises(build_loop.support.Paused) as paused,
        ):
            build_loop.run(runner, args, state, Path(directory) / "state.json", Path(directory), Path("/project"))
        self.assertEqual("PAUSED_LOCAL_REQUIREMENTS_BUILD", paused.exception.status)
        self.assertIn("cannot dispatch orchestrator", str(paused.exception))
        preflight.assert_not_called()
        dispatch_orchestrator.assert_not_called()

    def test_queued_pause_precedes_exhausted_limits_and_report_repair(self):
        for at_boundary in (False, True):
            with self.subTest(at_boundary=at_boundary), tempfile.TemporaryDirectory() as directory:

                class LoopExit(Exception):
                    pass

                dispatched = []
                run_dir = Path(directory)
                pause = run_dir / "pause-requested"
                if not at_boundary:
                    pause.touch()

                def drive(current, dispatch, *, before, **kwargs):
                    if at_boundary:
                        current["iteration"] = 2
                        pause.touch()
                        runner.timeout_recovery_guard.reset_mock()
                    before(current)
                    dispatched.append(current["next_stage"])

                state = {
                    "iteration": 2 if not at_boundary else 1,
                    "next_stage": "requirements_gather",
                    "settings": {"limits": {"iteration_ceiling": 1, "max_seconds": 0, "planning_calls": 10}},
                    "pending_report_repair": {"original": {"stage": "requirements_gather"}},
                }
                runner = SimpleNamespace(
                    orchestrator=SimpleNamespace(drive=drive, LoopExit=LoopExit),
                    native_calls=None,
                    consume_interventions=mock.Mock(return_value=False),
                    timeout_recovery_guard=mock.Mock(),
                    iteration_limit_reached=build_loop.iteration_limit_reached,
                    recover_default_budget=mock.Mock(return_value=False),
                    execute_report_repair=mock.Mock(),
                    now=lambda: "2026-11-23T00:01:00+00:00",
                    write_json=mock.Mock(),
                )
                args = SimpleNamespace(unit=None, _local_requirements_only=True)
                with (
                    mock.patch.object(build_loop.controller_isolation, "require_local_requirements"),
                    mock.patch.object(build_loop.model_preflight, "check") as preflight,
                    mock.patch.object(build_loop.cost, "exceeded", return_value=None),
                    mock.patch.object(build_loop.early_baseline, "reconcile_at_boundary"),
                    mock.patch.object(build_loop.no_progress_recovery, "conclude_exhausted", return_value=False),
                    mock.patch.object(build_loop.classification_recovery, "conclude", return_value=False),
                    self.assertRaises(build_loop.support.Paused) as paused,
                ):
                    build_loop.run(runner, args, state, run_dir / "state.json", run_dir, Path("/project"))
                self.assertEqual("PAUSED_REQUESTED", paused.exception.status)
                preflight.assert_not_called()
                runner.timeout_recovery_guard.assert_not_called()
                runner.recover_default_budget.assert_not_called()
                runner.execute_report_repair.assert_not_called()
                self.assertEqual([], dispatched)


class ControllerIsolationCliTests(unittest.TestCase):
    def setUp(self):
        fixtures.SubprocessFlow.setUp(self)
        source = Path(__file__).resolve().parents[1] / "tools"
        shutil.copy2(source / "live_fixture_provider.py", self.root / "fixture-bin" / "codex")
        (self.root / "fixture-bin" / "codex").chmod(0o755)
        (self.project / "tools").mkdir()
        (self.project / "tools" / "autocode.py").write_text("# AutoCode checkout marker\n")
        (self.project / "tools" / "autocode_controller_isolation.py").write_text("# AutoCode checkout marker\n")
        (self.project / "AGENTS.md").write_text("AutoCode fixture instructions\n")
        self.git("add", ".")
        self.git("commit", "-qm", "AutoCode markers")

    def git(self, *args):
        return subprocess.run(
            ["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c", "user.email=f@example.test", *args],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    def invoke(self, workspace, *args, command=None):
        return subprocess.run(
            [*(command or self.entry), "--workspace", str(workspace), *args],
            cwd=self.root,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=240,
        )

    def linked(self, name="self-build"):
        tree = self.root / name
        self.git("worktree", "add", "--detach", str(tree), "HEAD")
        return tree

    def copy_controller(self):
        source = Path(__file__).resolve().parents[1] / "tools"
        shutil.copytree(
            source, self.project / "tools", dirs_exist_ok=True, ignore=shutil.ignore_patterns("__pycache__")
        )
        # The AutoCode repository ignores bytecode, so the controller may cache it without changing the checkout.
        (self.project / ".gitignore").write_text("__pycache__/\n")
        self.git("add", "tools", ".gitignore")
        self.git("commit", "-qm", "Controller fixture")

    def test_external_controller_works_in_place_in_the_main_checkout(self):
        run = taskrun.TaskRun.start(
            self.project, BRIEF, command=self.entry, options=FIXTURE_OPTIONS, env=self.env, timeout=240
        )
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertFalse(Path(view["runner"]["path"]).is_relative_to(self.project))
        self.assertEqual([run.run_dir], taskrun.TaskRun.runs_in(self.project))

    def test_main_checkout_cannot_execute_its_own_controller(self):
        self.copy_controller()
        before = set(self.project.iterdir())
        local = [sys.executable, str(self.project / "tools/autocode.py")]
        result = self.invoke(
            self.project, BRIEF, "--in-place", "--no-chat", "--provider", "missing-provider", command=local
        )
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("Unsafe AutoCode self-build", result.stderr)
        self.assertIn("executing controller is inside", result.stderr)
        self.assertIn("Select an AutoCode CLI", result.stderr)
        self.assertNotIn("worktree add --detach", result.stderr)
        self.assertEqual(before, set(self.project.iterdir()))

    def footprint(self, project):
        def git(*args):
            return subprocess.run(
                ["git", "-C", str(project), "-c", "user.name=Fixture", "-c", "user.email=f@example.test", *args],
                check=True,
                capture_output=True,
                text=True,
                env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"},
            ).stdout

        index = Path(git("rev-parse", "--git-path", "index").strip())
        if not index.is_absolute():
            index = project / index
        if index.exists():
            stat = index.stat()
            observed_index = {"bytes": index.read_bytes(), "mtime_ns": stat.st_mtime_ns, "ctime_ns": stat.st_ctime_ns}
        else:
            observed_index = None
        return {
            "head": git("rev-parse", "HEAD"),
            "refs": git("for-each-ref", "--format=%(refname) %(objectname)"),
            "worktrees": git("worktree", "list", "--porcelain"),
            "index": observed_index,
            "status": git("status", "--porcelain"),
            # Tracked files and untracked files Git does not ignore: what "no Git or source change" covers.
            "sources": {
                name: (project / name).read_bytes()
                for name in sorted(git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split("\0"))
                if name and (project / name).is_file() and not {".git", ".autocode"} & set(Path(name).parts)
            },
        }

    def local_discussion_fixture(self, brief=None):
        scenario_root = Path(__file__).resolve().parents[1] / "scenarios"
        if str(scenario_root) not in sys.path:
            sys.path.insert(0, str(scenario_root))
        from harness import catalog
        from harness.driver import fake_setup
        from harness.project import materialize

        scenario = catalog.load("discuss-cache-choice")
        project = self.root / "discussion-project"
        materialize(scenario.seed, project)
        source = Path(__file__).resolve().parents[1] / "tools"
        shutil.copytree(source, project / "tools", ignore=shutil.ignore_patterns("__pycache__"))
        (project / "AGENTS.md").write_text("AutoCode fixture instructions\n")
        with (project / ".gitignore").open("a") as ignore:
            ignore.write("__pycache__/\n")
        subprocess.run(["git", "-C", str(project), "add", "tools", "AGENTS.md", ".gitignore"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(project),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "-qm",
                "Local controller fixture",
            ],
            check=True,
        )
        provider_root = self.root / "discussion-provider"
        _, environment = fake_setup(scenario, provider_root, scenario.dir / "reference")
        config = json.loads((provider_root / "fake-config.json").read_text())
        config["brief"] = brief or (
            "How does app/metadata.py cache values, and how do in-process and shared file-backed caches compare?"
        )
        (provider_root / "fake-config.json").write_text(json.dumps(config))
        bundle = provider_root / "bin"
        shutil.copy2(source / "fake_opencode.py", bundle / "opencode")
        (bundle / "opencode").chmod(0o755)
        shutil.copy2(source / "goal_fixtures.py", bundle / "goal_fixtures.py")
        self.env.update(environment)
        self.env["PATH"] = str(bundle) + os.pathsep + self.env["PATH"]
        self.env["AUTOCODE_FIXTURE_QUALIFIED_OPENCODE"] = "1"
        self.env["AUTOCODE_FIXTURE_SCENARIO_DELEGATE"] = str(bundle / "codex")
        local = [sys.executable, str(project / "tools/autocode.py")]
        self.assertEqual(fixture_cli.checked_fixture(env=self.env).name, "opencode")
        return project, config["brief"], local

    @unittest.skipUnless(containment.sandbox_problem() is None, "requires a working kernel sandbox")
    def test_local_discuss_finishes_in_verified_read_only_containment_without_git_or_source_changes(self):
        project, brief, command = self.local_discussion_fixture()
        before = self.footprint(project)
        options = (
            "--in-place",
            "--workflow",
            "discuss",
            "--no-chat",
            "--engine",
            "opencode",
            "--evidence-provenance",
            "fake",
            "--joint-planning",
            "--spec-first",
            "--requirements-model",
            "openai/gpt-6-sol",
            "--max-cost-usd",
            "0",
        )
        run = taskrun.TaskRun.start(project, brief, command=command, options=options, env=self.env, timeout=240)
        for _ in range(12):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if view["done"] or need.get("kind") != "answer":
                break
            question = need["questions"][0]
            answer = (
                view["chat"]["review"]["answer"]
                if question["id"].startswith("requirements-review-")
                else question.get("proposed_default") or "No preference"
            )
            run.answer(question["id"], answer, resolver_token=need["resolver_token"])
        self.assertTrue(view["done"], view)
        self.assertEqual("discuss", view["workflow"], view)
        state = json.loads((run.run_dir / "state.json").read_text())
        requirements = [row for row in state["stages"] if row.get("stage") == "requirements_gather"]
        self.assertEqual(1, len(requirements), state["stages"])
        record = requirements[0]
        self.assertEqual("requirements", record["role"])
        self.assertEqual(containment.backend(), record["tool_containment"]["backend"])
        self.assertTrue(record["tool_containment"]["private_reads"]["denied"])
        self.assertNotIn("uncontained_tools", record)
        self.assertNotIn("launch_sources", state)
        self.assertNotIn("base_commit", state, "Requirements-only setup must not invent a launch baseline")
        self.assertNotIn("build_worktree", state)
        self.assertFalse((project / ".autocode/worktrees").exists())
        self.assertEqual(before, self.footprint(project))

    @unittest.skipUnless(containment.sandbox_problem() is None, "requires a working kernel sandbox")
    def test_local_discussion_inside_task_worktree_never_delivers_ambient_changes(self):
        import autocode_workspaces as workspaces

        project, brief, _ = self.local_discussion_fixture()
        metadata = workspaces.create(project, "ambient-task")
        tree = Path(metadata["workspace"])
        (tree / "dirty.txt").write_text("unrelated tracked change\n")
        (tree / "untracked.txt").write_text("unrelated untracked file\n")
        before = self.footprint(tree)
        command = [sys.executable, str(tree / "tools/autocode.py")]
        options = (
            "--in-place",
            "--workflow",
            "discuss",
            "--no-chat",
            "--engine",
            "opencode",
            "--evidence-provenance",
            "fake",
            "--joint-planning",
            "--spec-first",
            "--requirements-model",
            "openai/gpt-6-sol",
            "--max-cost-usd",
            "0",
        )
        run = taskrun.TaskRun.start(tree, brief, command=command, options=options, env=self.env, timeout=240)
        for _ in range(12):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if view["done"] or need.get("kind") != "answer":
                break
            question = need["questions"][0]
            answer = (
                view["chat"]["review"]["answer"]
                if question["id"].startswith("requirements-review-")
                else question.get("proposed_default") or "No preference"
            )
            run.answer(question["id"], answer, resolver_token=need["resolver_token"])
        self.assertTrue(view["done"], view)
        self.assertEqual(before, self.footprint(tree), "completion must not deliver ambient task-worktree changes")
        resumed = self.invoke(tree, "--run-dir", str(run.run_dir), "--no-chat", command=command)
        self.assertEqual(0, resumed.returncode, resumed.stdout + resumed.stderr)
        self.assertTrue(run.status()["done"])
        self.assertEqual(before, self.footprint(tree), "completed-run resume must not deliver ambient changes")
        state = json.loads((run.run_dir / "state.json").read_text())
        self.assertNotIn("project_workspace", state)
        self.assertNotIn("task_branch", state)

    @unittest.skipUnless(containment.sandbox_problem() is None, "requires a working kernel sandbox")
    def test_local_requirements_report_repair_uses_the_same_read_only_boundary(self):
        project, brief, command = self.local_discussion_fixture()
        self.env["SCENARIO_FAKE_REQUIREMENTS_REPAIR_ONCE"] = "1"
        before = self.footprint(project)
        options = (
            "--in-place",
            "--workflow",
            "discuss",
            "--no-chat",
            "--engine",
            "opencode",
            "--evidence-provenance",
            "fake",
            "--joint-planning",
            "--spec-first",
            "--requirements-model",
            "openai/gpt-6-sol",
            "--max-cost-usd",
            "0",
        )
        run = taskrun.TaskRun.start(project, brief, command=command, options=options, env=self.env, timeout=240)
        for _ in range(12):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if view["done"] or need.get("kind") != "answer":
                break
            question = need["questions"][0]
            answer = (
                view["chat"]["review"]["answer"]
                if question["id"].startswith("requirements-review-")
                else question.get("proposed_default") or "No preference"
            )
            run.answer(question["id"], answer, resolver_token=need["resolver_token"])
        state = json.loads((run.run_dir / "state.json").read_text())
        self.assertTrue(
            view["done"],
            {
                "status": view.get("status"),
                "stop_reason": view.get("stop_reason"),
                "report_repair": view.get("report_repair"),
                "stages": [row.get("stage") for row in view.get("records", [])],
                "repair_archive": state.get("report_repair_archive"),
                "recovery_events": [row for row in state.get("user_events", []) if "repair" in row.get("kind", "")],
            },
        )
        stages = [row for row in state["stages"] if row.get("stage") != "model_preflight"]
        self.assertEqual(
            ["requirements_gather", "requirements_gather_report_repair"],
            [row["stage"] for row in stages],
            {
                "stages": [
                    {key: row.get(key) for key in ("stage", "report_only", "rejected", "repaired_by", "events")}
                    for row in stages
                ],
                "repair_history": state.get("report_repair_history"),
                "repair_archive": state.get("report_repair_archive"),
                "recovery_events": [row for row in state.get("user_events", []) if "repair" in row.get("kind", "")],
            },
        )
        repair = stages[1]
        self.assertTrue(repair["report_only"])
        self.assertEqual("requirements_gather", repair["original_stage"])
        self.assertEqual("requirements", repair.get("route_role", repair["role"]))
        self.assertEqual(containment.backend(), repair["tool_containment"]["backend"])
        self.assertFalse(repair["tool_containment"]["allow_write"])
        self.assertEqual([], repair["tool_containment"]["tools"])
        self.assertNotIn("verification_copy", repair["tool_containment"])
        self.assertTrue(repair["tool_containment"]["private_reads"]["denied"])
        self.assertNotIn("uncontained_tools", repair)
        self.assertNotIn("launch_sources", state)
        self.assertNotIn("base_commit", state)
        self.assertNotIn("build_worktree", state)
        self.assertEqual(before, self.footprint(project))

    @unittest.skipUnless(containment.sandbox_problem() is None, "requires a working kernel sandbox")
    def test_local_build_request_stops_before_planner_without_git_or_source_changes(self):
        project, brief, command = self.local_discussion_fixture(
            "Write docs/decisions/metadata-cache.json with the recommended shared-file cache and its tradeoffs."
        )
        before = self.footprint(project)
        options = (
            "--in-place",
            "--workflow",
            "discuss",
            "--no-chat",
            "--engine",
            "opencode",
            "--evidence-provenance",
            "fake",
            "--joint-planning",
            "--requirements-model",
            "openai/gpt-6-sol",
            "--max-cost-usd",
            "0",
        )
        run = taskrun.TaskRun.start(project, brief, command=command, options=options, env=self.env, timeout=240)
        view = run.advance_until_input()
        self.assertEqual("PAUSED_LOCAL_REQUIREMENTS_BUILD", view["status"], view)
        state = json.loads((run.run_dir / "state.json").read_text())
        self.assertEqual(
            ["requirements_gather"],
            [row["stage"] for row in state["stages"] if row["stage"] != "model_preflight"],
        )
        self.assertEqual("draft_plan", state["next_stage"])
        self.assertNotIn("launch_sources", state)
        self.assertNotIn("base_commit", state)
        self.assertNotIn("build_worktree", state)
        self.assertFalse((project / ".autocode/worktrees").exists())
        self.assertEqual(before, self.footprint(project))

    def test_local_discuss_without_supported_containment_refuses_before_a_run_is_saved(self):
        self.copy_controller()
        before = self.footprint(self.project)
        local = [sys.executable, str(self.project / "tools/autocode.py")]
        result = self.invoke(
            self.project,
            BRIEF,
            "--in-place",
            "--workflow",
            "discuss",
            "--no-chat",
            "--engine",
            "codex",
            "--evidence-provenance",
            "fake",
            "--max-cost-usd",
            "0",
            command=local,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("Requirements-only", result.stdout + result.stderr)
        self.assertIn("not covered", result.stdout + result.stderr)
        self.assertNotIn("--allow-uncontained-tools", result.stdout + result.stderr)
        self.assertFalse(list((self.project / ".autocode/runs").glob("*/state.json")))
        self.assertEqual(before, self.footprint(self.project))

    def test_local_discuss_refuses_the_uncontained_tools_opt_out_before_a_run_is_saved(self):
        self.copy_controller()
        source = Path(__file__).resolve().parents[1] / "tools"
        bundle = self.root / "fixture-bin"
        shutil.copy2(source / "fake_opencode.py", bundle / "opencode")
        (bundle / "opencode").chmod(0o755)
        shutil.copy2(source / "fake_codex.py", bundle / "codex")
        (bundle / "codex").chmod(0o755)
        shutil.copy2(source / "goal_fixtures.py", bundle / "goal_fixtures.py")
        self.env["AUTOCODE_FIXTURE_QUALIFIED_OPENCODE"] = "1"
        local = [sys.executable, str(self.project / "tools/autocode.py")]
        command = local
        before = self.footprint(self.project)
        result = self.invoke(
            self.project,
            BRIEF,
            "--in-place",
            "--workflow",
            "discuss",
            "--no-chat",
            "--engine",
            "opencode",
            "--evidence-provenance",
            "fake",
            "--allow-uncontained-tools",
            "--max-cost-usd",
            "0",
            command=command,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("Requirements-only", result.stdout + result.stderr)
        self.assertIn("cannot use uncontained tools", result.stdout + result.stderr)
        self.assertFalse(list((self.project / ".autocode/runs").glob("*/state.json")))
        self.assertEqual(before, self.footprint(self.project))

    def test_local_discuss_refuses_task_preflight_before_any_command_or_run(self):
        self.copy_controller()
        marker = self.root / "preflight-executed"
        manifest = self.root / "preflight.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": 1,
                    "inputs": [],
                    "runtime_files": [],
                    "checks": [
                        {
                            "id": "unsafe-setup",
                            "phase": "planning",
                            "argv": [sys.executable, "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
                            "recovery": "Do not run setup for a read-only discussion",
                        }
                    ],
                }
            )
        )
        before = self.footprint(self.project)
        local = [sys.executable, str(self.project / "tools/autocode.py")]
        result = self.invoke(
            self.project,
            BRIEF,
            "--in-place",
            "--workflow",
            "discuss",
            "--task-preflight",
            str(manifest),
            "--no-chat",
            "--engine",
            "codex",
            "--max-cost-usd",
            "0",
            command=local,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("Requirements-only discussion cannot use task preflight", result.stdout + result.stderr)
        self.assertFalse(marker.exists())
        self.assertFalse(list((self.project / ".autocode/runs").glob("*/state.json")))
        self.assertEqual(before, self.footprint(self.project))

    def test_unsafe_saved_builder_is_refused_before_reconciliation_or_mutation(self):
        run = taskrun.TaskRun.start(
            self.project, BRIEF, command=self.entry, options=FIXTURE_OPTIONS, env=self.env, timeout=240
        )
        self.assertEqual("approve_plan", run.status()["needs"]["kind"])
        checkpoint = run.run_dir / "state.json"
        state = json.loads(checkpoint.read_text())
        state["next_stage"] = "builder"
        checkpoint.write_text(json.dumps(state))
        self.copy_controller()
        before = self.footprint(self.project)
        saved = checkpoint.read_bytes()
        local = [sys.executable, str(self.project / "tools/autocode.py")]
        result = self.invoke(self.project, "--run-dir", str(run.run_dir), "--no-chat", command=local)
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("Unsafe AutoCode self-build", result.stderr)
        self.assertEqual(saved, checkpoint.read_bytes())
        self.assertEqual(before, self.footprint(self.project))
        self.assertFalse([row for row in json.loads(saved)["stages"] if row.get("stage") == "builder"])

    @unittest.skipUnless(containment.sandbox_problem() is None, "requires a working kernel sandbox")
    def test_saved_local_requirements_refuses_repair_before_copy_or_verification(self):
        import autocode_repair_handoff as handoff
        import autocode_source_scope as source_scope

        project, brief, controller = self.local_discussion_fixture()
        options = (
            "--in-place",
            "--workflow",
            "discuss",
            "--no-chat",
            "--engine",
            "opencode",
            "--evidence-provenance",
            "fake",
            "--joint-planning",
            "--spec-first",
            "--requirements-model",
            "openai/gpt-6-sol",
            "--max-cost-usd",
            "0",
        )
        run = taskrun.TaskRun.start(project, brief, command=controller, options=options, env=self.env, timeout=240)
        for _ in range(12):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if view["done"] or need.get("kind") != "answer":
                break
            question = need["questions"][0]
            answer = (
                view["chat"]["review"]["answer"]
                if question["id"].startswith("requirements-review-")
                else question.get("proposed_default") or "No preference"
            )
            run.answer(question["id"], answer, resolver_token=need["resolver_token"])
        self.assertTrue(view["done"], view)

        checkpoint = run.run_dir / "state.json"
        state = json.loads(checkpoint.read_text())
        revision = source_scope.snapshot(project, state)["revision"]
        marker = self.root / "repair-verification-ran"
        check = f'{sys.executable} -c "from pathlib import Path; Path({str(marker)!r}).touch(); raise SystemExit(3)"'
        method = f"Run `{check}`"
        state.update(
            status="PAUSED_BUILDER_CLASSIFICATION",
            phase="PAUSED_OR_BLOCKED",
            stop_reason="A crafted checkpoint retained a dispatched repair",
            next_stage="requirements_gather",
            workflow={"kind": "discuss"},
            active_stage={
                "stage": "requirements_gather",
                "role": "requirements",
                "route_role": "requirements",
            },
            resolution_request={
                "contract_hash": "repair-contract",
                "task_id": "repair-1",
                "source_stage": "review_completion",
                "provenance": "completion_review_decision",
                "source_revision": revision,
            },
            current_task={
                "id": "repair-1",
                "kind": "implement",
                "milestone_id": "M1",
                "milestone_ids": ["M1"],
                "decision": "CONTINUE",
                "objective": "Replace the fixture instructions",
                "requirements": ["Deliver the replacement externally"],
                "acceptance_criteria": ["AC1"],
                "affected_paths": ["AGENTS.md"],
                "validation_plan": [method],
                "contract_hash": "repair-contract",
                "contract_revision": 1,
                "source_revision": revision,
            },
            goal_contract={
                "hash": "repair-contract",
                "revision": 1,
                "body": {
                    "task_kind": "build",
                    "acceptance_criteria": [
                        {"id": "AC1", "criterion": "The instructions changed", "verification_method": method}
                    ],
                    "milestones": [
                        {
                            "id": "M1",
                            "objective": "Replace the fixture instructions",
                            "acceptance_criteria": ["AC1"],
                            "affected_paths": ["AGENTS.md"],
                        }
                    ],
                },
            },
        )
        state.setdefault("settings", {})["regression"] = {}
        handoff.export(state, run.run_dir)
        checkpoint.write_text(json.dumps(state))
        delivery = self.root / "repair-delivery"
        subprocess.run(["git", "clone", "-q", str(project), str(delivery)], check=True)
        (delivery / "AGENTS.md").write_text("externally repaired instructions\n")
        before = self.footprint(project)
        saved = checkpoint.read_bytes()

        result = self.invoke(
            project,
            "--run-dir",
            str(run.run_dir),
            "--submit-repair",
            str(delivery),
            "--no-chat",
            command=controller,
        )
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertIn("Unsafe AutoCode self-build", result.stderr, result.stdout + result.stderr)
        self.assertFalse(marker.exists(), "repair verification ran before the read-only admission guard")
        self.assertFalse((run.run_dir / "handoff" / "verification").exists())
        self.assertEqual(saved, checkpoint.read_bytes())
        self.assertEqual(before, self.footprint(project))

    def test_external_controller_can_start_in_a_caller_owned_linked_worktree(self):
        tree = self.linked("self build\nwith trailing space ")
        run = taskrun.TaskRun.start(tree, BRIEF, command=self.entry, options=FIXTURE_OPTIONS, env=self.env, timeout=240)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        self.assertFalse(Path(view["runner"]["path"]).is_relative_to(tree))
        self.assertEqual(view["runner"], view["evidence"]["runner"])
        self.assertTrue(view["evidence"]["base_commit"])
        self.assertEqual([], taskrun.TaskRun.runs_in(self.project))

    def test_default_cli_launch_isolates_the_self_build(self):
        result = self.invoke(self.project, BRIEF, "--no-chat", *FIXTURE_OPTIONS)
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("Task worktree:", result.stdout)
        trees = list((self.project / ".autocode" / "worktrees").iterdir())
        self.assertEqual(1, len(trees))
        run = taskrun.TaskRun.attach(trees[0], command=self.entry, options=FIXTURE_OPTIONS, env=self.env, timeout=240)
        self.assertIsNotNone(run)
        self.assertEqual("approve_plan", run.status()["needs"]["kind"])

    def test_linked_worktree_cannot_execute_its_own_controller(self):
        self.copy_controller()
        tree = self.linked()
        result = self.invoke(
            tree, BRIEF, "--in-place", "--no-chat", command=[sys.executable, str(tree / "tools/autocode.py")]
        )
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("executing controller is inside", result.stderr)
        self.assertEqual([], taskrun.TaskRun.runs_in(tree))

    def test_status_of_a_saved_run_remains_read_only_under_an_unsafe_controller(self):
        self.copy_controller()
        tree = self.linked()
        run = taskrun.TaskRun.start(tree, BRIEF, command=self.entry, options=FIXTURE_OPTIONS, env=self.env, timeout=240)
        checkpoint = run.run_dir / "state.json"
        before = checkpoint.read_bytes()
        local = [sys.executable, str(tree / "tools/autocode.py")]
        shown = self.invoke(tree, "--run-dir", str(run.run_dir), "--status", command=local)
        self.assertEqual(0, shown.returncode, shown.stderr)
        self.assertEqual(run.status()["runner"], json.loads(shown.stdout)["view"]["runner"])
        refused = self.invoke(tree, "--run-dir", str(run.run_dir), "--no-chat", command=local)
        self.assertEqual(2, refused.returncode, refused.stderr)
        self.assertIn("Unsafe AutoCode self-build", refused.stderr)
        self.assertEqual(before, checkpoint.read_bytes())


class VendoredCopyTests(unittest.TestCase):
    """A project carrying an old copy of tools/autocode.py is not an AutoCode self-build."""

    def test_a_vendored_autocode_copy_is_an_ordinary_project(self):
        with tempfile.TemporaryDirectory() as temp:
            project = Path(temp).resolve()
            (project / "tools").mkdir()
            (project / "tools" / "autocode.py").write_text("# vendored copy\n")
            (project / "AGENTS.md").write_text("the project's own instructions\n")
            controller = Path(isolation.__file__)
            isolation.require_isolated(project, controller)  # not refused
            (project / "tools" / "autocode_controller_isolation.py").write_text("# AutoCode itself\n")
            isolation.require_isolated(project, controller)  # an outside controller may edit it in place
            with self.assertRaisesRegex(ValueError, "Unsafe AutoCode self-build"):
                isolation.require_isolated(project, project / "tools" / "autocode_controller_isolation.py")


class ControllerIdentityViewTests(unittest.TestCase):
    def test_saved_identity_is_copied_and_never_inferred_from_builder_commit(self):
        state = {
            "status": "RUNNING",
            "base_commit": "builder",
            "runner": {"path": "/controller/tools", "commit": "old+modified"},
        }
        view = run_view.view(state)
        self.assertEqual(state["runner"], view["runner"])
        self.assertEqual("builder", view["evidence"]["base_commit"])
        view["runner"]["commit"] = "mutated"
        view["evidence"]["runner"]["path"] = "/mutated"
        self.assertEqual({"path": "/controller/tools", "commit": "old+modified"}, state["runner"])

    def test_legacy_missing_and_installed_unknown_commit_remain_unknown(self):
        self.assertIsNone(run_view.view({"status": "RUNNING"})["runner"])
        installed = {"path": "/venv/site-packages/autocode_cli", "commit": None}
        self.assertEqual(installed, run_view.view({"status": "RUNNING", "runner": installed})["runner"])
