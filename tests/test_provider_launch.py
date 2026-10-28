"""Public provider launch and real capture behavior, without model dispatch."""

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_prompts as prompts
import autocode_provider_launch as launch
import autocode_source_snapshot as source
import autocode_util as util
import autocode_verification_copy as copies
from providers.command import CommandProvider


class ProviderLaunchTests(unittest.TestCase):
    def test_runner_selects_tool_free_policy_for_a_nonplanning_report_repair(self):
        import autocode as runner

        class Prepared(Exception):
            pass

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            schema = root / "schema.json"
            schema.write_text('{"type":"object"}')
            state = {
                "version": 2,
                "iteration": 1,
                "next_stage": "review_change",
                "stages": [],
                "settings": {
                    "engine": "opencode",
                    "provider": "kilocode",
                    "roles": {
                        "sol": {"model": "zai-coding-plan/glm-5.3", "reasoning_effort": "low"},
                    },
                },
            }
            for report_only in (False, True):
                with (
                    self.subTest(report_only=report_only),
                    mock.patch.object(runner.provider_launch, "prepare", side_effect=Prepared) as prepare,
                    self.assertRaises(Prepared),
                ):
                    runner.run_role(
                        role="sol",
                        prompt="Already-completed review report",
                        sandbox="read-only",
                        workspace=root,
                        run_dir=root / str(report_only),
                        state=state,
                        schema=schema,
                        model="zai-coding-plan/glm-5.3",
                        allow_write=False,
                        dry_run=True,
                        report_only=report_only,
                    )
                self.assertEqual(report_only, prepare.call_args.kwargs["planning_report_recovery"])
                self.assertEqual("zai-coding-plan/glm-5.3", prepare.call_args.kwargs["model"])

    def test_native_handoff_keeps_all_task_input_with_small_owned_manifest_locator(self):
        descriptor = {
            "manifest": "/owned/isolation.json",
            "manifest_sha256": "a" * 64,
            "files": {"/owned/sdk/" + "x" * 240 + str(i): "b" * 64 for i in range(3660)},
        }
        boundary = {
            "scratch": "/owned/scratch",
            "tools": ["bash"],
            "allow_write": False,
            "native_role_isolation": descriptor,
        }
        data = {
            "requirements": "Exact complete original approved brief",
            "cases": list(range(20)),
            "criteria": list(range(22)),
            "commands": ["python3 test_app.py", "literal second", "literal third"],
        }
        prompt = "Original instructions\nCURRENT HANDOFF DATA\n" + json.dumps(data)
        rendered = launch.containment_prompt(prompt, {"tool_containment": boundary})
        actual = json.loads(rendered.rsplit("\nCURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(data, {k: v for k, v in actual.items() if k != "tool_containment"})
        self.assertTrue(launch.native_locators.boundary_matches(actual["tool_containment"], boundary))
        self.assertLess(len(rendered), 4096)
        self.assertEqual(3660, len(descriptor["files"]))

    def test_stage_record_preserves_each_provider_launch_contract(self):
        cases = [
            ({"engine": "codex"}, {}),
            (
                {"engine": "opencode", "configured": True, "provider": "offline"},
                {"provider": "offline", "isolation": "Config-tool sandbox flag and workspace snapshot checks"},
            ),
            (
                {"engine": "opencode", "output_token_cap": 123},
                {
                    "isolation": "OpenCode tool permissions and workspace snapshot checks; no OS sandbox",
                    "tool_containment": None,
                    "output_token_cap": 123,
                },
            ),
            (
                {"engine": "opencode", "tool_containment": {"scratch": "/scratch"}, "output_token_cap": 123},
                {
                    "isolation": "Kernel-constrained native shell; other tools disabled",
                    "tool_containment": {"scratch": "/scratch"},
                    "output_token_cap": 123,
                },
            ),
        ]
        for worker, expected in cases:
            with self.subTest(engine=worker["engine"], configured=worker.get("configured")):
                self.assertEqual({"prompts_hash": prompts.HASH, **expected}, launch.stage_record(worker))

    def test_access_binding_is_saved_at_launch_and_does_not_follow_later_settings(self):
        from types import SimpleNamespace

        identity = {
            "engine": "fixture",
            "identity_version": 1,
            "executable": "/fixture",
            "version": "1",
            "config_path": "/config.toml",
            "config_sha256": "a" * 64,
        }
        settings = {
            "engine": "opencode",
            "provider": "fixture",
            "roles": {"requirements": {"model": "demo/reviewer"}},
            "transport_identities": {"opencode": identity},
        }
        adapter = SimpleNamespace(
            NAME="fixture", CONFIGURED=True, _config={"auth": {}}, launch=lambda *args, **kwargs: (["fixture"], {}, {})
        )
        _, _, _, worker = launch.prepare(
            engine="opencode",
            adapter=adapter,
            role="glm",
            route_role="requirements",
            workspace=Path("/workspace"),
            run_dir=Path("/run"),
            session=None,
            model="demo/reviewer",
            effort="low",
            allow_write=False,
            planning=True,
            report=Path("/report"),
            schema=Path("/schema"),
            prompt_file=Path("/prompt"),
            sandbox="read-only",
            transport_args=[],
            chatgpt=False,
            provider="fixture",
            settings=settings,
        )
        record = launch.stage_record(worker)
        binding = record["access_preflight_identity"]
        self.assertEqual(
            {
                "schema": 1,
                "identity": {
                    "provider": "fixture",
                    "transport_sha256": util.digest(identity),
                    "reasoning_effort": "low",
                },
                "model": "demo/reviewer",
            },
            binding,
        )
        identity["config_sha256"] = "b" * 64
        worker["access_preflight_identity"]["model"] = "changed"
        self.assertEqual("demo/reviewer", binding["model"])
        self.assertNotEqual(util.digest(identity), binding["identity"]["transport_sha256"])

    def test_a_provider_that_never_resumes_across_stages_starts_each_stage_fresh(self):
        """#717: the built-in claude provider resumes only a same-stage correction (a planning launch)."""

        class Stub:
            CONFIGURED = True
            NAME = "stub"
            RESUME_ACROSS_STAGES = False

            def __init__(self):
                self.sessions = []

            def launch(self, role, workspace, run_dir, session, *args, **kwargs):
                self.sessions.append(session)
                return ["stub"], {}, {}

        def prepare(adapter, planning):
            return launch.prepare(
                engine="opencode",
                adapter=adapter,
                role="terra",
                route_role="terra",
                workspace=Path("/workspace"),
                run_dir=Path("/run"),
                session="s1",
                model="m",
                effort="medium",
                allow_write=True,
                planning=planning,
                report=Path("/report"),
                schema=Path("/schema"),
                prompt_file=Path("/prompt"),
                sandbox="workspace-write",
                transport_args=[],
                chatgpt=False,
                provider=None,
            )

        stub = Stub()
        *_, worker = prepare(stub, planning=False)
        self.assertEqual([None], stub.sessions)
        self.assertIsNone(worker["provider_session"])
        self.assertIn("fresh session", launch.stage_record(worker)["fresh_session_reason"])
        *_, worker = prepare(stub, planning=True)
        self.assertEqual([None, "s1"], stub.sessions)
        self.assertEqual("s1", worker["provider_session"])
        self.assertNotIn("fresh_session_reason", launch.stage_record(worker))

        class Legacy:  # OpenCode and TOML-configured tools have no such attribute: unchanged
            CONFIGURED = True
            NAME = "legacy"
            launch = Stub.launch

            def __init__(self):
                self.sessions = []

        legacy = Legacy()
        *_, worker = prepare(legacy, planning=False)
        self.assertEqual((["s1"], "s1"), (legacy.sessions, worker["provider_session"]))

    def test_a_kilo_builder_starts_each_stage_in_a_fresh_session(self):
        """#1939: a resumed Kilo Builder session grew until Kilo compacted it; writable stages now start fresh too."""
        from providers.kilocode import KiloProvider

        kilo = object.__new__(KiloProvider)
        kilo.NAME = "kilocode"
        kilo.launch = mock.Mock(return_value=(["kilo", "run"], {}, {}))
        with tempfile.TemporaryDirectory() as root:
            *_, worker = launch.prepare(
                engine="opencode",
                adapter=kilo,
                role="terra",
                route_role="terra",
                workspace=Path(root),
                run_dir=Path(root) / ".autocode/runs/one",
                session="ses_saved",
                model="zai-coding-plan/glm-5.3",
                effort="medium",
                allow_write=True,
                planning=False,
                report=Path(root) / "output.json",
                schema=Path(root) / "schema.json",
                prompt_file=Path(root) / "prompt.txt",
                sandbox="workspace-write",
                transport_args=[],
                chatgpt=False,
                provider="kilocode",
            )
        self.assertIsNone(kilo.launch.call_args.args[3])
        self.assertIsNone(worker["provider_session"])
        self.assertIn("fresh session", launch.stage_record(worker)["fresh_session_reason"])

    def test_retired_or_unknown_engine_cannot_fall_back_to_codex(self):
        for engine in ("gocode", "qwen", "unknown"):
            with self.subTest(engine=engine), self.assertRaisesRegex(RuntimeError, "providers live in .*--provider"):
                launch.prepare(
                    engine=engine,
                    adapter=None,
                    role="sol",
                    route_role="sol",
                    workspace=Path("/workspace"),
                    run_dir=Path("/run"),
                    session=None,
                    model="example/model",
                    effort="high",
                    allow_write=False,
                    planning=True,
                    report=Path("/report"),
                    schema=Path("/schema"),
                    prompt_file=Path("/prompt"),
                    sandbox="read-only",
                    transport_args=[],
                    chatgpt=False,
                    provider=None,
                )


class NativeReplayBoundaryTests(unittest.TestCase):
    def options(self):
        return dict(
            engine="opencode",
            adapter=mock.Mock(),
            role="sol",
            route_role="sol",
            workspace=Path("/workspace"),
            run_dir=Path("/run"),
            session=None,
            model="anthropic/fixture-claude",
            effort="high",
            allow_write=False,
            planning=False,
            report=Path("/report"),
            schema=Path("/schema"),
            prompt_file=Path("/prompt"),
            sandbox="read-only",
            transport_args=[],
            chatgpt=False,
            provider=None,
        )

    def test_recorded_replay_cannot_replace_a_fresh_native_owner(self):
        for native in (
            {"native_role_isolation": object()},
            {"native_environment": {}},
            {"native_image_preparation": object()},
            {"native_role_isolation": object(), "native_environment": {}, "native_image_preparation": object()},
        ):
            options = self.options()
            with self.subTest(native=tuple(native)), mock.patch("autocode_replay_transport.prepare") as replay:
                with self.assertRaisesRegex(
                    util.Paused, "Recorded replay cannot replace the original fresh native owner"
                ) as error:
                    launch.prepare(**options, settings={"replay_recording": {"checks": "fresh"}}, **native)
                self.assertEqual("PAUSED_TOOL_CONTAINMENT", error.exception.status)
                replay.assert_not_called()
                options["adapter"].launch.assert_not_called()

    def test_ordinary_recorded_replay_keeps_its_original_context(self):
        options = self.options()
        settings = {"replay_recording": {"checks": "fresh"}}
        context = {"consumed": 0}
        with mock.patch("autocode_replay_transport.prepare", return_value=mock.sentinel.prepared) as replay:
            result = launch.prepare(**options, settings=settings, replay_context=context)
        self.assertIs(mock.sentinel.prepared, result)
        self.assertIs(context, replay.call_args.kwargs["context"])
        self.assertIs(settings, replay.call_args.kwargs["settings"])
        self.assertEqual("high", replay.call_args.kwargs["effort"])
        options["adapter"].launch.assert_not_called()


class CodexCaptureCopyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        (self.root / "source.py").write_text("value = 42\n")
        (self.root / ".gitignore").write_text(".autocode/\napproved/\n")
        for args in (
            ("init", "-q"),
            ("add", "."),
            ("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "source"),
        ):
            subprocess.run(["/usr/bin/git", *args], cwd=self.root, check=True, capture_output=True)
        self.run = self.root / ".autocode/runs/one"
        self.run.mkdir(parents=True)
        self.options = dict(
            engine="codex",
            adapter=None,
            role="sol",
            route_role="sol",
            workspace=self.root,
            run_dir=self.run,
            session=None,
            model="gpt-5.6-sol",
            effort="high",
            allow_write=False,
            planning=False,
            report=self.root / "report.json",
            schema=self.root / "schema.json",
            prompt_file=self.root / "prompt.txt",
            sandbox="workspace-write",
            transport_args=[],
            chatgpt=True,
            provider=None,
        )

    def capture(self, environment, name, command):
        entry = (
            "import sys; sys.path.insert(0, "
            + repr(str(Path(launch.__file__).parent))
            + "); import autocode_capture_command as c; sys.exit(c.cli())"
        )
        return subprocess.run(
            [
                sys.executable,
                "-c",
                entry,
                "--output",
                str(self.root / ".autocode/evidence" / (name + ".json")),
                "--",
                *command,
            ],
            cwd=self.root,
            env=environment,
            capture_output=True,
            text=True,
            timeout=30,
        )

    def test_real_capture_preserves_exact_command_and_keeps_generated_files_out_of_source(self):
        (self.root / "approved").mkdir()
        (self.root / "approved/value.txt").write_text("selected ignored input")
        before = source.snapshot(self.root, paths=["approved/value.txt"])
        command, environment, _, worker = launch.prepare(**self.options, source_paths=["approved/value.txt"])
        self.assertEqual(
            [
                "codex",
                "exec",
                "-C",
                str(self.root),
                "--sandbox",
                "workspace-write",
                "-c",
                'forced_login_method="chatgpt"',
                "-c",
                'model_reasoning_effort="high"',
                "-",
                "--json",
                "--output-schema",
                str(self.options["schema"]),
                "-o",
                str(self.options["report"]),
                "--model",
                "gpt-5.6-sol",
            ],
            command,
        )
        code = (
            "from pathlib import Path; from source import value; assert value == 42; "
            "assert Path('approved/value.txt').read_text() == 'selected ignored input'; "
            "Path('other_1.sqlite3').touch(); print('real native check')"
        )
        native = [sys.executable, "-B", "-c", code]
        result = self.capture(environment, "first", native)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        # Baseline must fail on the real generated original-source file,
        # before this test depends on new metadata.
        self.assertFalse((self.root / "other_1.sqlite3").exists())
        self.assertEqual(before, source.snapshot(self.root, paths=["approved/value.txt"]))
        copy = worker["verification_copy"]
        self.assertEqual(self.run, Path(copy["manifest"]).parent.parent)
        self.assertEqual(
            ["evidence", "output", "runs"], sorted(path.name for path in (self.root / ".autocode").iterdir())
        )
        self.assertEqual(copy["manifest"], environment["AUTOCODE_VERIFICATION_COPY"])
        self.assertEqual(copy["sha256"], environment["AUTOCODE_VERIFICATION_COPY_SHA256"])
        self.assertNotIn("tool_containment", worker)
        launch.verify_containment(worker)
        record = launch.stage_record(worker)
        self.assertEqual(copy, record["verification_copy"])
        self.assertNotIn("Kernel", record["isolation"])
        receipt = json.loads(result.stdout)
        self.assertEqual(native, receipt["command"])
        self.assertEqual(
            {"source_revision": copy["source_revision"], "manifest_sha256": copy["sha256"]},
            receipt["verification_copy"],
        )
        self.assertEqual("real native check\n", Path(receipt["full_output"]).read_text())
        self.assertTrue((Path(copy["tree"]) / "other_1.sqlite3").is_file())
        # New outputs survive a later capture; retained inputs are still checked.
        next_check = self.capture(
            environment,
            "second",
            [sys.executable, "-B", "-c", "from pathlib import Path; assert Path('other_1.sqlite3').is_file()"],
        )
        self.assertEqual(0, next_check.returncode, next_check.stderr)
        self.assertEqual("value = 42\n", (self.root / "source.py").read_text())

    def test_each_native_stage_allocates_fresh_authority_even_with_opencode_opt_out(self):
        copies_created = []
        for _ in range(2):
            _, environment, _, worker = launch.prepare(**self.options, settings={"allow_uncontained_tools": True})
            copies_created.append(worker["verification_copy"]["manifest"])
            self.assertIn("AUTOCODE_VERIFICATION_COPY", environment)
        self.assertNotEqual(*copies_created)

    def test_writer_planning_and_preview_allocate_nothing_and_drop_ambient_authority(self):
        for changes in (
            {"allow_write": True},
            {"planning": True},
            {"enforce_tool_boundary": False},
            {"sandbox": "read-only", "role": "astra", "route_role": "investigator"},
        ):
            with (
                self.subTest(changes=changes),
                mock.patch.dict(
                    os.environ,
                    {"AUTOCODE_VERIFICATION_COPY": "/old/stage/manifest", "AUTOCODE_VERIFICATION_COPY_SHA256": "old"},
                ),
            ):
                _, environment, _, worker = launch.prepare(**{**self.options, **changes})
                self.assertNotIn("AUTOCODE_VERIFICATION_COPY", environment)
                self.assertNotIn("AUTOCODE_VERIFICATION_COPY_SHA256", environment)
                self.assertNotIn("verification_copy", worker)
                self.assertEqual([], list(self.run.iterdir()))
                self.assertEqual(["runs"], sorted(path.name for path in (self.root / ".autocode").iterdir()))

    def test_prelaunch_rejects_changed_manifest_copy_source_and_authority(self):
        for mutation, expected in (
            ("manifest", "authority changed"),
            ("copy", "input changed"),
            ("source", "source changed"),
            ("environment", "launch authority changed"),
        ):
            with self.subTest(mutation=mutation):
                _, _, _, worker = launch.prepare(**self.options)
                copy = worker["verification_copy"]
                if mutation == "manifest":
                    Path(copy["manifest"]).write_text("{}")
                elif mutation == "copy":
                    (Path(copy["tree"]) / "source.py").write_text("changed copy\n")
                elif mutation == "source":
                    (self.root / "source.py").write_text("concurrent original change\n")
                else:
                    worker["environment"]["AUTOCODE_VERIFICATION_COPY"] = "/other/manifest"
                with self.assertRaisesRegex(util.Paused, expected) as caught:
                    launch.verify_containment(worker)
                self.assertEqual("PAUSED_STALE_VALIDATION", caught.exception.status)
                if mutation == "source":
                    self.assertEqual("concurrent original change\n", (self.root / "source.py").read_text())
                    (self.root / "source.py").write_text("value = 42\n")

    def test_capture_rejects_current_source_drift_before_running_command(self):
        _, environment, _, _ = launch.prepare(**self.options)
        (self.root / "source.py").write_text("concurrent original change\n")
        result = self.capture(
            environment, "stale", [sys.executable, "-c", "from pathlib import Path; Path('should-not-run').touch()"]
        )
        self.assertEqual(2, result.returncode, result.stderr)
        self.assertIn("Verification source changed", result.stderr)
        self.assertFalse((self.root / "should-not-run").exists())
        self.assertFalse((self.root / ".autocode/evidence/stale.log").exists())
        self.assertEqual("concurrent original change\n", (self.root / "source.py").read_text())

    def test_failed_preparation_removes_only_its_new_control_directory(self):
        prior = self.run / "tool-containment-prior"
        prior.mkdir(parents=True)
        (prior / "owned-by-other-stage").write_text("keep")
        other = self.root / ".autocode/runs/other"
        other.mkdir()
        (other / "owned-by-other-run").write_text("keep too")

        def fail(workspace, scratch, **kwargs):
            (scratch / "partial").write_text("disposable")
            raise ValueError("copy failed")

        with mock.patch.object(copies, "create", side_effect=fail):
            with self.assertRaisesRegex(util.Paused, "copy failed"):
                launch.prepare(**self.options)
        self.assertEqual([prior], list(self.run.iterdir()))
        self.assertEqual("keep", (prior / "owned-by-other-stage").read_text())
        self.assertEqual("keep too", (other / "owned-by-other-run").read_text())
        self.assertEqual(["runs"], sorted(path.name for path in (self.root / ".autocode").iterdir()))

    def test_collected_preparation_timeout_has_its_own_pause_without_copy_authority(self):
        prior = self.run / "tool-containment-prior"
        prior.mkdir()
        sentinel = prior / "existing-evidence"
        sentinel.write_text("keep")
        before = source.snapshot(self.root)

        def timeout(workspace, scratch, **kwargs):
            (scratch / "partial").write_text("disposable")
            raise subprocess.TimeoutExpired(["/usr/bin/git", "fast-import"], 900)

        with (
            mock.patch.object(copies, "create", side_effect=timeout),
            mock.patch.dict(
                os.environ,
                {"AUTOCODE_VERIFICATION_COPY": "/old/copy", "AUTOCODE_VERIFICATION_COPY_SHA256": "old"},
            ),
            self.assertRaises(util.Paused) as caught,
        ):
            launch.prepare(**self.options)
        self.assertEqual("PAUSED_VERIFICATION_PREPARATION", caught.exception.status)
        self.assertIn("before provider launch", str(caught.exception))
        self.assertEqual([prior], list(self.run.iterdir()))
        self.assertEqual("keep", sentinel.read_text())
        self.assertEqual(before, source.snapshot(self.root))

    def test_source_drift_during_preparation_keeps_the_stale_validation_pause(self):
        with mock.patch.object(copies, "allocate", side_effect=ValueError("Source changed while preparing copy")):
            with self.assertRaises(util.Paused) as caught:
                launch.prepare(**self.options)
        self.assertEqual("PAUSED_STALE_VALIDATION", caught.exception.status)
        self.assertIn("Source changed", str(caught.exception))

    def test_symlinked_storage_is_rejected_without_touching_external_contents(self):
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory)
            self.run.rmdir()
            self.run.parent.rmdir()
            self.run.parent.parent.rmdir()
            self.root.joinpath(".autocode").symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(util.Paused, "must not be a symlink"):
                launch.prepare(**self.options)
            self.assertEqual([], list(external.iterdir()))

    def test_copy_requires_the_existing_direct_run_directory(self):
        outside, nested = self.root / "outside-run", self.run / "nested"
        outside.mkdir()
        nested.mkdir()
        invalid = (
            self.root / "outside-run",
            self.run / "nested",
            self.run.parent / "missing",
            self.run.parent,
            Path(".autocode/runs/one"),
        )
        for directory in invalid:
            with (
                self.subTest(directory=directory),
                self.assertRaisesRegex(util.Paused, "existing physical task run directory"),
            ):
                launch.prepare(**{**self.options, "run_dir": directory})
        self.assertEqual([], list(outside.iterdir()))
        self.assertEqual([], list(nested.iterdir()))
        nested.rmdir()
        self.assertEqual([], list(self.run.iterdir()))
        self.assertEqual(["runs"], sorted(path.name for path in (self.root / ".autocode").iterdir()))

    def test_symlinked_run_directory_cannot_allocate_external_authority(self):
        with tempfile.TemporaryDirectory() as directory:
            external = Path(directory).resolve()
            self.run.rmdir()
            self.run.symlink_to(external, target_is_directory=True)
            with self.assertRaisesRegex(util.Paused, "existing physical task run directory"):
                launch.prepare(**self.options)
            self.assertEqual([], list(external.iterdir()))

    def test_run_copy_authority_cannot_move_to_another_run(self):
        _, environment, _, worker = launch.prepare(**self.options)
        original = Path(worker["verification_copy"]["manifest"])
        other_run = self.run.parent / "other"
        other_run.mkdir()
        moved = other_run / original.parent.name
        original.parent.rename(moved)
        manifest = moved / original.name
        with self.assertRaisesRegex(ValueError, "Unexpected verification copy layout"):
            copies.execution(manifest, environment["AUTOCODE_VERIFICATION_COPY_SHA256"], self.root)

    def test_run_copy_rejects_lookalike_control_names(self):
        _, environment, _, worker = launch.prepare(**self.options)
        original = Path(worker["verification_copy"]["manifest"])
        moved = self.run / "tool-containment-lookalike"
        original.parent.rename(moved)
        with self.assertRaisesRegex(ValueError, "Verification copy authority changed"):
            copies.execution(moved / original.name, environment["AUTOCODE_VERIFICATION_COPY_SHA256"], self.root)

    def test_existing_kernel_contained_copy_layout_remains_executable(self):
        scratch = self.root / ".autocode" / ("tool-containment-" + "f" * 32) / "scratch"
        scratch.mkdir(parents=True)
        legacy = copies.create(self.root, scratch)
        tree, provenance = copies.execution(legacy["manifest"], legacy["sha256"], self.root)
        self.assertEqual(scratch / "verification", tree)
        self.assertEqual("value = 42\n", (tree / "source.py").read_text())
        self.assertEqual(legacy["sha256"], provenance["manifest_sha256"])

    def configured(self):
        return CommandProvider(
            {
                "name": "offline",
                "command": ["fixture-provider", "{model}", "{workspace}", "{sandbox}", "{report}"],
                "roles": {"sol": {"model": "verifier", "effort": "high"}},
            },
            self.root / "provider.toml",
        )

    def test_configured_provider_real_capture_creates_its_fixture_only_in_the_copy(self):
        before = source.snapshot(self.root)
        ambient = {"AUTOCODE_FIXTURE_MARKER": "preserved"}
        with mock.patch.dict(os.environ, ambient):
            command, environment, _, worker = launch.prepare(
                **{**self.options, "engine": "opencode", "adapter": self.configured(), "model": "verifier"}
            )
        self.assertEqual(
            ["fixture-provider", "verifier", str(self.root), "workspace-write", str(self.options["report"])], command
        )
        native = [
            sys.executable,
            "-B",
            "-c",
            "from pathlib import Path; import runpy,os; "
            "assert os.environ['AUTOCODE_FIXTURE_MARKER']=='preserved'; "
            "Path('probe.py').write_text('from source import value; assert value == 42\\n'); "
            "runpy.run_path('probe.py'); Path('other_1.sqlite3').touch(); print('configured native capture')",
        ]
        result = self.capture(environment, "configured", native)
        self.assertEqual(0, result.returncode, result.stderr + result.stdout)
        self.assertFalse((self.root / "probe.py").exists())
        self.assertFalse((self.root / "other_1.sqlite3").exists())
        self.assertEqual(before, source.snapshot(self.root))
        copy = worker["verification_copy"]
        self.assertEqual(self.run, Path(copy["manifest"]).parent.parent)
        self.assertEqual(
            ["evidence", "output", "runs"], sorted(path.name for path in (self.root / ".autocode").iterdir())
        )
        self.assertEqual("offline", worker["provider"])
        self.assertNotIn("tool_containment", worker)
        record = launch.stage_record(worker)
        self.assertEqual("offline", record["provider"])
        self.assertIn("Configured-provider permission checks", record["isolation"])
        self.assertNotIn("Codex", record["isolation"])
        self.assertNotIn("Kernel", record["isolation"])
        receipt = json.loads(result.stdout)
        self.assertEqual(native, receipt["command"])
        self.assertEqual(copy["sha256"], receipt["verification_copy"]["manifest_sha256"])
        self.assertEqual("configured native capture\n", Path(receipt["full_output"]).read_text())
        self.assertTrue((Path(copy["tree"]) / "probe.py").is_file())
        launch.verify_containment(worker)

    def test_configured_judging_mints_new_authority_instead_of_inheriting_an_old_copy(self):
        ambient = {
            "AUTOCODE_VERIFICATION_COPY": "/old/verification-copy.json",
            "AUTOCODE_VERIFICATION_COPY_SHA256": "old",
        }
        with mock.patch.dict(os.environ, ambient):
            _, environment, _, worker = launch.prepare(
                **{**self.options, "engine": "opencode", "adapter": self.configured()}
            )
        copy = worker["verification_copy"]
        self.assertNotEqual(ambient["AUTOCODE_VERIFICATION_COPY"], copy["manifest"])
        self.assertEqual(copy["manifest"], environment["AUTOCODE_VERIFICATION_COPY"])
        self.assertEqual(copy["sha256"], environment["AUTOCODE_VERIFICATION_COPY_SHA256"])
        launch.verify_containment(worker)

    def test_real_configured_adapter_writer_planning_preview_and_investigator_keep_no_copy(self):
        for changes in (
            {"allow_write": True},
            {"planning": True},
            {"enforce_tool_boundary": False},
            {"sandbox": "read-only", "role": "astra", "route_role": "investigator"},
        ):
            with (
                self.subTest(changes=changes),
                mock.patch.dict(
                    os.environ,
                    {
                        "AUTOCODE_VERIFICATION_COPY": "/old/verification-copy.json",
                        "AUTOCODE_VERIFICATION_COPY_SHA256": "old",
                    },
                ),
            ):
                _, environment, _, worker = launch.prepare(
                    **{**self.options, "engine": "opencode", "adapter": self.configured(), **changes}
                )
                self.assertNotIn("AUTOCODE_VERIFICATION_COPY", environment)
                self.assertNotIn("AUTOCODE_VERIFICATION_COPY_SHA256", environment)
                self.assertNotIn("verification_copy", worker)
                self.assertEqual([], list(self.run.iterdir()))
                self.assertEqual(["runs"], sorted(path.name for path in (self.root / ".autocode").iterdir()))
