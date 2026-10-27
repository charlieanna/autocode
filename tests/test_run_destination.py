"""Exact fresh destinations cannot overwrite or redirect an existing run."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode as runner
import autocode_model_preflight as preflight
import autocode_run_destination as destination
import autocode_support as support
from providers.command import CommandProvider


class FreshDestinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.target = self.workspace / ".autocode" / "runs" / "recording-replay"

    def test_allocates_the_exact_new_directory(self):
        self.assertEqual(self.target, destination.reserve(self.workspace, self.target))
        self.assertTrue(self.target.is_dir())
        self.assertEqual([], list(self.target.iterdir()))

    def test_a_reserved_run_the_launch_never_saved_is_discarded_and_a_saved_one_kept(self):
        """A launch refused after the lock and before state.json left a directory every listing showed as
        "cannot be read"."""
        self.assertFalse(destination.discard_unsaved(None))
        self.assertFalse(destination.discard_unsaved(self.target), "nothing reserved yet")
        destination.reserve(self.workspace, self.target)
        (self.target / "writer.lock").touch()
        self.assertTrue(destination.discard_unsaved(self.target))
        self.assertFalse(self.target.exists())
        for kept in ("state.json", "evidence.jsonl"):
            with self.subTest(kept=kept):
                self.target.mkdir(parents=True)
                (self.target / "writer.lock").touch()
                (self.target / kept).write_text("{}")
                self.assertFalse(destination.discard_unsaved(self.target))
                self.assertTrue((self.target / kept).is_file())
                (self.target / kept).unlink()
                (self.target / "writer.lock").unlink()
                self.target.rmdir()
        link = self.workspace / ".autocode" / "runs" / "link"
        link.symlink_to(self.root)
        self.assertFalse(destination.discard_unsaved(link), "a link is never followed")
        self.assertTrue(self.root.is_dir())

    def test_an_existing_empty_or_saved_run_is_never_reused(self):
        self.target.mkdir(parents=True)
        for saved in (False, True):
            with self.subTest(saved=saved):
                if saved:
                    (self.target / "evidence.jsonl").write_bytes(b"retained evidence\n")
                before = {p.name: p.read_bytes() for p in self.target.iterdir()}
                with self.assertRaises(FileExistsError):
                    destination.reserve(self.workspace, self.target)
                self.assertEqual(before, {p.name: p.read_bytes() for p in self.target.iterdir()})

    def test_external_and_nested_destinations_are_refused_before_writes(self):
        for path in (self.root / "foreign", self.target / "nested", self.workspace / ".autocode" / "runs"):
            with self.subTest(path=path):
                with self.assertRaises(ValueError):
                    destination.reserve(self.workspace, path)
                self.assertFalse((self.workspace / ".autocode").exists())
        self.assertFalse((self.root / "foreign").exists())

    def test_traversal_cannot_choose_another_directory(self):
        path = self.target.parent / ".." / "runs" / "other"
        with self.assertRaises(ValueError):
            destination.reserve(self.workspace, path)
        self.assertFalse((self.workspace / ".autocode").exists())

    def test_symlinked_store_cannot_write_into_a_foreign_directory(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (self.workspace / ".autocode").symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(ValueError):
            destination.reserve(self.workspace, self.target)
        self.assertEqual([], list(foreign.iterdir()))

    def test_symlinked_destination_cannot_replace_earlier_evidence(self):
        foreign = self.root / "foreign"
        foreign.mkdir()
        (foreign / "evidence.jsonl").write_bytes(b"original\n")
        self.target.parent.mkdir(parents=True)
        self.target.symlink_to(foreign, target_is_directory=True)
        with self.assertRaises(ValueError):
            destination.reserve(self.workspace, self.target)
        self.assertEqual(b"original\n", (foreign / "evidence.jsonl").read_bytes())

    def test_a_regular_file_cannot_be_used_as_the_run_store(self):
        (self.workspace / ".autocode").write_bytes(b"keep\n")
        with self.assertRaises(OSError):
            destination.reserve(self.workspace, self.target)
        self.assertEqual(b"keep\n", (self.workspace / ".autocode").read_bytes())

    def bootstrapped(self, name="build-calculator-1a2b3c4d", run="recording-replay"):
        project = self.workspace / "autocode-projects" / name
        project.mkdir(parents=True)
        (project / ".git").mkdir()
        run_dir = project / ".autocode" / "runs" / run
        run_dir.mkdir(parents=True)
        (run_dir / "state.json").write_text(json.dumps({"workspace": str(project), "status": "WAITING_FOR_USER"}))
        return project, run_dir

    def test_a_destination_named_before_bootstrap_is_reserved_under_the_project(self):
        project, _ = self.bootstrapped()
        requested = self.workspace / ".autocode" / "runs" / "20261018-120000-vscode-a1b2c3"
        reserved = destination.reserve(project, requested, named_from=self.workspace)
        self.assertEqual(project / ".autocode" / "runs" / requested.name, reserved)
        self.assertTrue(reserved.is_dir())
        self.assertFalse((self.workspace / ".autocode").exists(), "the selected folder stays untouched")

    def test_a_symlinked_ancestor_of_the_store_does_not_change_the_destination(self):
        real = self.root / "real"
        real.mkdir()
        link = self.root / "link"
        link.symlink_to(real, target_is_directory=True)
        reserved = destination.reserve(link, link / ".autocode" / "runs" / "run-1")
        self.assertEqual(real / ".autocode" / "runs" / "run-1", reserved)
        self.assertTrue(reserved.is_dir())

    def test_named_from_does_not_authorize_an_unrelated_destination(self):
        project, _ = self.bootstrapped()
        for path in (self.root / "foreign" / "run", self.workspace / "elsewhere" / "run"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                destination.reserve(project, path, named_from=self.workspace)

    def test_a_pre_relocation_handle_resolves_to_the_bootstrapped_run(self):
        project, run_dir = self.bootstrapped()
        requested = self.workspace / ".autocode" / "runs" / run_dir.name
        self.assertEqual((project, run_dir), destination.resolve_relocated(self.workspace, requested))

    def test_an_ambiguous_run_name_is_not_resolved(self):
        _, run_dir = self.bootstrapped()
        self.bootstrapped(name="build-calculator-ffffffff", run=run_dir.name)
        requested = self.workspace / ".autocode" / "runs" / run_dir.name
        self.assertIsNone(destination.resolve_relocated(self.workspace, requested))

    def test_a_run_whose_state_names_another_workspace_is_not_resolved(self):
        _, run_dir = self.bootstrapped()
        (run_dir / "state.json").write_text(json.dumps({"workspace": str(self.root / "moved")}))
        requested = self.workspace / ".autocode" / "runs" / run_dir.name
        self.assertIsNone(destination.resolve_relocated(self.workspace, requested))


class RelocatedDestinationCliTests(unittest.TestCase):
    """A client's fresh destination survives the bootstrapped-project relocation."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="relocated-destination-")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.plain = self.root / "plain"
        self.plain.mkdir()
        (self.plain / "keep.txt").write_text("user file\n")
        self.requested = self.plain / ".autocode" / "runs" / "20261018-120000-vscode-a1b2c3"
        self.models = {
            role: "fixture/reviewer" for role in ("astra", "terra", "sol", "completion", "glm", "plan_reviewer")
        }
        self.models["terra"] = self.models["glm"] = "fixture/builder"
        config = {
            "name": "budgetfixture",
            "command": [sys.executable, "-c", "raise AssertionError('no provider may spawn')"],
            "roles": {role: {"model": model, "effort": "medium"} for role, model in self.models.items()},
            "auth": {"command": ["fixture", "auth", "list"]},
        }
        adapter = CommandProvider(config, self.root / "provider.toml")
        stack = contextlib.ExitStack()
        self.addCleanup(stack.close)
        stack.enter_context(
            patch.dict(
                os.environ,
                {
                    "AUTOCODE_HOME": str(self.root / "registry"),
                    "XDG_CONFIG_HOME": str(self.root / "config"),
                    "CODEX_HOME": str(self.root / "codex-home"),
                    "AUTOCODE_PROVIDER": "budgetfixture",
                },
            )
        )
        stack.enter_context(patch.object(runner.autocode_providers, "resolve", return_value=adapter))
        stack.enter_context(patch.object(adapter, "local_settings", return_value={"engine": "budgetfixture"}))
        stack.enter_context(patch.object(adapter, "available_models", return_value=set(self.models.values())))
        stack.enter_context(patch.object(adapter, "check_models"))
        stack.enter_context(patch.object(adapter, "check_subscription_routes"))
        stack.enter_context(patch.object(preflight.transport, "execute", side_effect=self.probe))
        stack.enter_context(patch.object(runner, "run_role", side_effect=self.stage))

    def probe(self, **kwargs):
        directory = kwargs["directory"]
        directory.mkdir(parents=True, exist_ok=True)
        events = [
            {"type": "step_start", "sessionID": "probe", "part": {"id": "start", "sessionID": "probe"}},
            {
                "type": "step_finish",
                "sessionID": "probe",
                "part": {
                    "id": "finish",
                    "sessionID": "probe",
                    "reason": "stop",
                    "cost": 0,
                    "tokens": {"input": 1, "output": 1, "reasoning": 0, "cache": {"read": 0, "write": 0}},
                },
            },
        ]
        events_path = directory / "events.jsonl"
        events_path.write_text("".join(json.dumps(row) + "\n" for row in events))
        return {
            "exit_code": 0,
            "timed_out": False,
            "report": {"ok": True},
            "rows": events,
            "report_error": None,
            "metrics": support.event_metrics(events_path),
        }

    def stage(self, *args, **kwargs):
        raise support.Paused("PAUSED_FIXTURE_STAGE", "Ordinary stage was reached")

    def invoke(self, *arguments):
        with (
            patch.object(sys, "argv", ["autocode", *arguments]),
            contextlib.redirect_stdout(io.StringIO()) as stdout,
            contextlib.redirect_stderr(io.StringIO()) as stderr,
        ):
            code = runner.main()
        return code, stdout.getvalue(), stderr.getvalue()

    def test_a_named_destination_survives_bootstrap_and_the_handle_still_reads(self):
        code, stdout, stderr = self.invoke(
            "Build a greeting CLI",
            "--in-place",
            "--no-chat",
            "--joint-planning",
            "--spec-first",
            "--provider",
            "budgetfixture",
            "--workspace",
            str(self.plain),
            "--new-run-dir",
            str(self.requested),
        )
        self.assertEqual(2, code, stdout + stderr)
        projects = [path for path in (self.plain / "autocode-projects").iterdir() if path.is_dir()]
        self.assertEqual(1, len(projects), stdout + stderr)
        project = projects[0]
        run_dir = project / ".autocode" / "runs" / self.requested.name
        self.assertTrue((run_dir / "state.json").is_file(), stdout + stderr)
        self.assertFalse((self.plain / ".autocode").exists(), "the selected folder stays untouched")

        code, stdout, stderr = self.invoke("--status", "--workspace", str(self.plain), "--run-dir", str(self.requested))
        self.assertEqual(0, code, stderr)
        view = json.loads(stdout)["view"]
        self.assertEqual(str(project), view["workspace"])
        self.assertEqual(str(run_dir), view["run_dir"])
        self.assertEqual("PAUSED_FIXTURE_STAGE", view["status"])
