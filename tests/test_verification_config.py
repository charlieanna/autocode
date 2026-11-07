"""Verification command repairs through the CLI, using only offline providers."""

import copy
import json
import shlex
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import time
import unittest
import venv
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import autocode_resolver_human as human
import autocode_support as support
import autocode_verification_config as configuration

from . import test_bugfix_workflow as bugfix


class VerificationCommandRecovery(unittest.TestCase):
    new_run_engine_args = ()
    setUp = bugfix.BugfixWorkflow.setUp
    prepare = bugfix.BugfixWorkflow.prepare
    draft = bugfix.BugfixWorkflow.draft
    launch = bugfix.BugfixWorkflow.launch
    saved = bugfix.BugfixWorkflow.saved
    builder_writes = bugfix.BugfixWorkflow.builder_writes

    def status(self, run):
        return json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)

    def stopped_before_validation(self):
        # A project-owned gate chooses its authoritative test module and emits
        # verbose unittest results, just like the repository's serial suite gate.
        (self.project / "project_gate.py").write_text(
            "import unittest\n"
            'suite = unittest.defaultTestLoader.loadTestsFromName("test_greet")\n'
            "result = unittest.TextTestRunner(verbosity=2).run(suite)\n"
            "raise SystemExit(0 if result.wasSuccessful() else 1)\n"
        )
        subprocess.run(["git", "-C", str(self.project), "add", "project_gate.py"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.project),
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=f@example.test",
                "commit",
                "-qm",
                "project suite gate",
            ],
            check=True,
        )
        self.builder_writes(bugfix.references.BUGFIX_REFERENCE)
        run, approved = self.draft()
        self.launch(["--run-dir", str(run), "--approve-goal", approved["displayed_goal"]], 0)
        for _ in range(3):
            current = self.status(run)
            args = ["--run-dir", str(run), "--pause-after-stage", "--no-chat"]
            if current["status"].startswith("PAUSED_"):
                args.append("--resume-paused")
            self.launch(args, 2)
            if self.status(run)["next_stage"] == "sol":
                break
        self.assertEqual("sol", self.status(run)["next_stage"])
        return run

    @property
    def gate(self):
        return f"{shlex.quote(sys.executable)} project_gate.py"

    def test_stopped_run_uses_explicit_repository_gate_and_keeps_approved_settings(self):
        run = self.stopped_before_validation()
        before = self.status(run)
        regression = f"{shlex.quote(sys.executable)} -m unittest -v test_greet.py"
        self.launch(
            [
                "--run-dir",
                str(run),
                "--resume-paused",
                "--no-chat",
                "--test-command",
                self.gate,
                "--regression-command",
                regression,
            ],
            0,
        )
        after = self.status(run)
        self.assertEqual("TASK_COMPLETE", after["status"])
        self.assertEqual(before["contract_token"], after["contract_token"])
        for name in ("roles", "limits", "builder_retry"):
            self.assertEqual(before["settings"][name], after["settings"][name])
        final = self.saved()[1]
        proof = final["regression_proof"]
        self.assertEqual("PASS", proof["verdict"])
        self.assertEqual(self.gate, proof["commands"]["suite"])
        self.assertEqual(regression, proof["commands"]["regression"])
        receipt = json.loads(Path(proof["path"]).read_text())
        self.assertTrue(receipt["checks"]["suite_on_candidate"]["results"]["complete"])
        self.assertTrue(receipt["checks"]["suite_on_candidate"]["results"]["passed"])
        events = [row for row in final["user_events"] if row["kind"] == "verification_commands_changed"]
        self.assertEqual(1, len(events))
        self.assertEqual("user_cli", events[0]["actor"])

    def test_differing_command_without_resume_is_rejected_without_changing_settings(self):
        run = self.stopped_before_validation()
        before = self.status(run)
        self.launch(["--run-dir", str(run), "--no-chat", "--test-command", self.gate], 2)
        after = self.status(run)
        self.assertEqual(before["settings"], after["settings"])
        self.assertEqual(before["contract_token"], after["contract_token"])
        self.assertEqual(before["next_stage"], after["next_stage"])

    def test_command_change_after_validation_cannot_reuse_the_previous_pass(self):
        run = self.stopped_before_validation()
        self.launch(["--run-dir", str(run), "--resume-paused", "--pause-after-stage", "--no-chat"], 2)
        self.assertEqual("PASS", self.saved()[1]["regression_proof"]["verdict"])
        self.assertEqual("astra_review", self.status(run)["next_stage"])
        failing = f'{shlex.quote(sys.executable)} -c "raise SystemExit(7)"'
        self.launch(["--run-dir", str(run), "--resume-paused", "--no-chat", "--test-command", failing], 2)
        final = self.saved()[1]
        self.assertNotEqual("TASK_COMPLETE", self.status(run)["status"])
        # Reject the late override or execute a fresh proof. Retaining PASS
        # under the changed command would let Completion bypass verification.
        if final["settings"]["regression"].get("test_command") == failing:
            self.assertNotEqual("PASS", final["regression_proof"]["verdict"])
            self.assertEqual(failing, final["regression_proof"]["commands"]["suite"])
            self.assertGreaterEqual(len(final["regression_proofs"]), 2)

    def test_suite_override_does_not_approve_a_regression_command_missing_the_bug(self):
        run = self.stopped_before_validation()
        unchanged = f"{shlex.quote(sys.executable)} -m unittest -v test_greet.TestGreet.test_ada"
        self.launch(
            [
                "--run-dir",
                str(run),
                "--resume-paused",
                "--no-chat",
                "--test-command",
                self.gate,
                "--regression-command",
                unchanged,
            ],
            2,
        )
        final = self.saved()[1]
        self.assertNotEqual("TASK_COMPLETE", self.status(run)["status"])
        self.assertNotEqual("PASS", final["regression_proof"]["verdict"])
        self.assertEqual(unchanged, final["regression_proof"]["commands"]["regression"])


class VerificationCommandGuards(unittest.TestCase):
    def state(self):
        return {
            "status": "PAUSED_REQUESTED",
            "next_stage": "sol",
            "settings": {"regression": {"test_command": "old suite", "test_timeout": 7200}},
            "goal_contract": {"hash": "approved"},
            "regression_proof": {"verdict": "PASS", "source_revision": "current"},
            "regression_proofs": [{"verdict": "PASS", "path": "saved.json"}],
        }

    def test_active_uncertain_and_unreconciled_attempts_refuse_command_changes(self):
        for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts"):
            with self.subTest(key=key):
                state = self.state()
                state[key] = {"stage": "sol"}
                before = copy.deepcopy(state)
                args = SimpleNamespace(test_command="new suite", resume_paused=True)
                with self.assertRaises(ValueError):
                    configuration.configure_resume(state, state["settings"], args)
                self.assertEqual(before, state)

    def test_the_acceptance_proof_pause_accepts_a_suite_correction_before_its_proof(self):
        # #2609: no proof has run at this pause, so the saved command is the only correction it needs.
        state = self.state()
        state.update(status="PAUSED_ACCEPTANCE_TESTS", next_stage="write_acceptance_tests")
        args = SimpleNamespace(test_command="go test ./...", resume_paused=True)
        settings = configuration.configure_resume(state, state["settings"], args)
        self.assertEqual("go test ./...", settings["regression"]["test_command"])
        self.assertEqual("verification_commands_changed", state["user_events"][-1]["kind"])
        for status, stage in (("PAUSED_ACCEPTANCE_TESTS", "terra"), ("PAUSED_REQUESTED", "write_acceptance_tests")):
            with self.subTest(status=status, stage=stage):
                state = self.state()
                state.update(status=status, next_stage=stage)
                before = copy.deepcopy(state)
                with self.assertRaisesRegex(ValueError, "stopped checkpoint"):
                    configuration.configure_resume(state, state["settings"], args)
                self.assertEqual(before, state)

    def test_same_command_is_a_noop_even_when_run_has_not_reached_a_repair_checkpoint(self):
        state = self.state()
        state.update(status="RUNNING", active_runner_check={"stage": "regression_proof"})
        before = copy.deepcopy(state)
        args = SimpleNamespace(test_command="old suite", resume_paused=False)
        configuration.configure_resume(state, state["settings"], args)
        self.assertEqual(before, state)


class VerificationCommandBlockerHold(unittest.TestCase):
    """A Resolver's `--resume-paused --test-command` advice must work from the hold it was given at.

    The Completion Owner's blocker holds a run at RESOLVER_PENDING (queued) or WAITING_FOR_USER
    (published) with the Completion Reviewer next. The old command's proof must still not reach
    acceptance, so the change routes the run through the Tester and retires the blocker with a
    recorded reason.
    """

    QUESTION = {
        "id": "resolver-fixture",
        "question": "The saved suite does not exercise the change; which command proves it?",
        "why": "The Completion Owner cannot accept an unproved source",
        "options": [],
        "proposed_default": "",
    }

    def proposal(self, stage="astra_review", **changes):
        proposal = {
            "version": human.VERSION,
            "scope": "blocker",
            "origin": {"stage": stage, "output": "/run/review.json"},
            "request": {
                "kind": "blocker",
                "discovered": "The saved suite never runs the changed module",
                "impact": "The Completion Owner cannot accept an unproved source",
                "decision_needed": "Run a suite that covers the change",
                "options": [],
                "proposed_delta": "--resume-paused --test-command 'new suite'",
            },
            "questions": [],
            "evidence": {},
            "status": "WAITING_FOR_USER",
            "phase": "RESOLVING",
            "next_stage": "astra_review",
            "previous_status": "RUNNING",
        }
        proposal.update(changes)
        return proposal

    def base(self):
        return {
            "status": "RESOLVER_PENDING",
            "phase": "RESOLVING",
            "next_stage": "astra_review",
            "settings": {"regression": {"test_command": "old suite", "test_timeout": 7200}},
            "goal_contract": {"hash": "approved"},
            "regression_proof": {"verdict": "PASS", "source_revision": "current"},
            "validation": {"verdict": "FAIL", "source_revision": "current"},
            "pending_questions": [],
        }

    def queued(self, **changes):
        state = self.base()
        state[human.PRIVATE] = self.proposal(**changes)
        return state

    def published(self, identity_changes=None, **changes):
        state = self.base()
        proposal = self.proposal(questions=[copy.deepcopy(self.QUESTION)], **changes)
        identity = {
            "version": human.VERSION,
            "issuer": "resolver",
            "binding": {"task_id": "fixture"},
            "proposal": proposal,
            "action": "escalate",
            "reason": "The Completion Owner still does not accept this source",
            **(identity_changes or {}),
        }
        key = support.digest(identity)
        state.update(
            status="WAITING_FOR_USER",
            pending_questions=[copy.deepcopy(self.QUESTION)],
            user_request=copy.deepcopy(proposal["request"]),
            resolver={
                "human_escalations": {
                    key: {"identity": identity, "receipt_hash": key, "status": "pending", "issued_at": "2026-10-31"}
                }
            },
        )
        state[human.PUBLIC] = {"request_id": key, "scope": "blocker", "questions": [copy.deepcopy(self.QUESTION)]}
        return state

    def args(self, **changes):
        return SimpleNamespace(**{"test_command": "new suite", "resume_paused": True, **changes})

    def resume(self, state, **changes):
        return configuration.configure_resume(state, state["settings"], self.args(**changes))

    def assertRefused(self, state, **changes):
        before = copy.deepcopy(state)
        with self.assertRaises(ValueError):
            self.resume(state, **changes)
        self.assertEqual(before, state)

    def test_queued_blocker_hold_accepts_the_change_and_routes_to_the_tester(self):
        state = self.queued()
        proposal = copy.deepcopy(state[human.PRIVATE])
        settings = self.resume(state)
        self.assertEqual("new suite", settings["regression"]["test_command"])
        self.assertEqual(7200, settings["regression"]["test_timeout"])
        self.assertEqual("PAUSED_RESOLVER", state["status"])
        self.assertEqual("sol", state["next_stage"])
        self.assertNotIn(human.PRIVATE, state)
        self.assertEqual({"verdict": "PASS", "source_revision": "current"}, state["regression_proof"])
        (archived,) = state["validation_archive"]
        self.assertEqual(proposal, archived["resolver_proposal"])
        self.assertEqual(state["validation"], archived["validation"])
        self.assertEqual({"verdict": "PASS", "source_revision": "current"}, archived["regression_proof"])
        (event,) = state["user_events"]
        self.assertEqual("verification_commands_changed", event["kind"])
        self.assertEqual({"test_command": "old suite"}, event["previous"])
        self.assertEqual({"test_command": "new suite"}, event["current"])
        self.assertIn("blocker", archived["reason"])

    def test_published_blocker_hold_is_withdrawn_before_the_run_resumes(self):
        state = self.published()
        (key,) = state["resolver"]["human_escalations"]
        self.resume(state)
        self.assertEqual("PAUSED_RESOLVER", state["status"])
        self.assertEqual("sol", state["next_stage"])
        self.assertEqual([], state["pending_questions"])
        self.assertNotIn("user_request", state)
        self.assertNotIn(human.PUBLIC, state)
        entry = state["resolver"]["human_escalations"][key]
        self.assertEqual("superseded", entry["status"])
        self.assertIn("superseded_at", entry)
        self.assertIn("verification command", entry["superseded_reason"])
        (archived,) = state["validation_archive"]
        self.assertEqual(entry["identity"]["proposal"], archived["resolver_proposal"])

    def test_published_withdrawal_keeps_questions_the_blocker_did_not_issue(self):
        state = self.published()
        other = {"id": "q-other", "question": "Unrelated?", "options": []}
        state["pending_questions"].append(other)
        self.resume(state)
        self.assertEqual([other], state["pending_questions"])
        self.assertTrue(state["status"].startswith("PAUSED_"))

    def test_tester_stage_follows_the_workflow_mode(self):
        for mode, stage in (("glm_first_v1", "astra_checkpoint"), ("glm_final_audit_v2", "terra"), (None, "sol")):
            for build in (self.queued, self.published):
                with self.subTest(mode=mode, hold=build.__name__):
                    state = build()
                    if mode:
                        state["settings"]["workflow"] = {"mode": mode}
                    self.resume(state)
                    self.assertEqual(stage, state["next_stage"])

    def test_pause_status_comes_from_the_hold_when_it_names_one(self):
        state = self.queued(origin={"stage": "astra_review", "pause_status": "PAUSED_COMPLETION_GATE"})
        self.resume(state)
        self.assertEqual("PAUSED_COMPLETION_GATE", state["status"])
        state = self.queued(previous_status="PAUSED_REQUESTED")
        self.resume(state)
        self.assertEqual("PAUSED_REQUESTED", state["status"])

    def test_resolver_origin_blocker_is_accepted_too(self):
        state = self.queued(stage="astra_resolve")
        self.resume(state)
        self.assertEqual("sol", state["next_stage"])
        self.assertNotIn(human.PRIVATE, state)

    def test_an_unchanged_command_leaves_the_hold_alone(self):
        for build in (self.queued, self.published):
            with self.subTest(hold=build.__name__):
                state = build()
                before = copy.deepcopy(state)
                self.resume(state, test_command="old suite")
                self.assertEqual(before, state)

    def test_active_or_uncertain_attempts_still_refuse_from_a_hold(self):
        for build in (self.queued, self.published):
            for key in ("active_stage", "active_runner_check", "pending_report_repair", "uncertain_artifacts"):
                with self.subTest(hold=build.__name__, key=key):
                    state = build()
                    state[key] = {"stage": "astra_review"}
                    self.assertRefused(state)

    def test_a_hold_is_changed_only_under_resume_paused(self):
        for build in (self.queued, self.published):
            with self.subTest(hold=build.__name__):
                self.assertRefused(build(), resume_paused=False)

    def test_holds_that_are_not_a_completion_blocker_still_refuse(self):
        for scope in ("operational_exhaustion", "goal_approval", "requirements"):
            for build in (self.queued, self.published):
                with self.subTest(scope=scope, hold=build.__name__):
                    self.assertRefused(build(scope=scope))
        for build in (self.queued, self.published):
            with self.subTest(origin="terra", hold=build.__name__):
                self.assertRefused(build(stage="terra"))

    def test_a_hold_with_no_authentic_blocker_still_refuses(self):
        state = self.base()
        self.assertRefused(state)
        state = self.published()
        (entry,) = state["resolver"]["human_escalations"].values()
        entry["status"] = "consumed"
        self.assertRefused(state)
        state = self.published()
        (entry,) = state["resolver"]["human_escalations"].values()
        entry["identity"]["reason"] = "changed after sealing"
        self.assertRefused(state)
        state = self.published(identity_changes={"issuer": "builder"})
        self.assertRefused(state)
        state = self.published()
        state[human.PUBLIC]["request_id"] = "unknown"
        self.assertRefused(state)
        state = self.published()
        state["status"] = "RUNNING"
        self.assertRefused(state)

    def test_a_pause_that_is_not_a_checkpoint_or_a_hold_still_refuses(self):
        state = self.base()
        state.update(status="PAUSED_REQUESTED", next_stage="astra_review")
        self.assertRefused(state)


class VerificationProofCache(unittest.TestCase):
    def setUp(self):
        runtime = tempfile.TemporaryDirectory(prefix="verification-proof-runtime-")
        self.addCleanup(runtime.cleanup)
        root = Path(runtime.name).resolve()
        deadline = time.monotonic() + 120
        copied_files = copied_bytes = 0

        def copy_file(source, destination):
            nonlocal copied_files, copied_bytes
            copied_files += 1
            if copied_files > 20_000:
                raise RuntimeError("Private stdlib fixture exceeds its file limit")
            with open(source, "rb") as original, open(destination, "xb") as target:
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError("Private stdlib fixture preparation exceeded 120 seconds")
                    chunk = original.read(1024 * 1024)
                    if not chunk:
                        break
                    copied_bytes += len(chunk)
                    if copied_bytes > 1024 * 1024 * 1024:
                        raise RuntimeError("Private stdlib fixture exceeds its byte limit")
                    target.write(chunk)
            shutil.copystat(source, destination)
            return str(destination)

        stdlib = Path(sysconfig.get_path("stdlib")).resolve()
        private_stdlib = root / "stdlib"

        def ignore(directory, names):
            ignored = {name for name in names if name == "__pycache__" or name.endswith((".pyc", ".pyo"))}
            if Path(directory).resolve() == stdlib:
                # Global installed packages are not part of this no-pip fixture.
                ignored.update({"site-packages", "dist-packages"} & set(names))
            return ignored

        # A venv alone still imports the shared host stdlib. A parallel -I/-E
        # process can write host bytecode despite PYTHONDONTWRITEBYTECODE=1.
        shutil.copytree(stdlib, private_stdlib, ignore=ignore, copy_function=copy_file)
        extensions = Path(sysconfig.get_config_var("DESTSHARED")).resolve()
        if extensions.is_relative_to(stdlib):
            private_extensions = private_stdlib / extensions.relative_to(stdlib)
        else:
            private_extensions = root / "lib-dynload"
            shutil.copytree(extensions, private_extensions, ignore=ignore, copy_function=copy_file)
        if time.monotonic() >= deadline:
            raise TimeoutError("Private stdlib fixture preparation exceeded 120 seconds")
        venv.EnvBuilder(with_pip=False, symlinks=False).create(root / "venv")
        python = root / "venv" / "bin" / "python"
        self.assertTrue(python.resolve().is_relative_to(root))

        # ._pth selects these copies even for macOS framework interpreters,
        # whose linked framework can take precedence over pyvenv.cfg's home.
        paths = f"{private_stdlib}\n{private_extensions}\nimport site\n"
        for executable in {python, python.resolve()}:
            executable.with_name(executable.name + "._pth").write_text(paths)
        (private_stdlib / "sitecustomize.py").write_text(
            "import os, sys\n"
            "sys.dont_write_bytecode = True\n"
            # Keep normal fixture commands' source lookup. Identity's -I probe
            # must continue to omit cwd and inherited PYTHONPATH completely.
            "if not any(flag in sys.orig_argv[1:] for flag in ('-I', '-E')):\n"
            "    inherited = os.environ.get('PYTHONPATH', '').split(os.pathsep)\n"
            "    sys.path[:0] = [os.getcwd(), *[path for path in inherited if path]]\n"
        )
        # Warm the private encoding startup cache before any proof identity is
        # recorded, and verify that Python imports pure Python modules and any
        # dynamic math extension from the copy. A built-in math module remains
        # bound by the copied interpreter's byte identity. All later bytecode
        # remains fully identity-bound.
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("Private stdlib fixture preparation exceeded 120 seconds")
        witness = subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "import json,math,sys,unittest; print(json.dumps({"
                "'paths':sys.path,'unittest':unittest.__file__,"
                "'extension':getattr(math,'__file__',None),"
                "'extension_origin':math.__spec__.origin,"
                "'extension_builtin':math.__name__ in sys.builtin_module_names,"
                "'no_bytecode':sys.dont_write_bytecode}))",
            ],
            capture_output=True,
            text=True,
            timeout=min(15, remaining),
        )
        self.assertEqual(0, witness.returncode, witness.stderr)
        observed = json.loads(witness.stdout)
        self.assertTrue(observed["no_bytecode"])
        module_paths = [*observed["paths"], observed["unittest"]]
        if observed["extension"] is None:
            self.assertEqual("built-in", observed["extension_origin"])
            self.assertTrue(observed["extension_builtin"])
        else:
            module_paths.append(observed["extension"])
        for path in module_paths:
            self.assertTrue(Path(path).resolve().is_relative_to(root), path)
        self.assertLess(time.monotonic(), deadline, "Private stdlib preparation exceeded 120 seconds")
        self.python = str(python)
        self.private_stdlib = private_stdlib
        self.host_stdlib = stdlib

    def test_completion_review_reproves_a_deleted_path_and_reuses_the_original_baseline(self):
        import autocode_regression as regression

        from .test_verify import REFERENCE, Project

        project = Project()
        self.addCleanup(project.close)
        project.write({**REFERENCE, "docs/bugs/removed.json": '{"diagnosis": "runner note"}\n'})
        state = {
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "base_commit": project.base,
            "settings": {"regression": {"python": self.python, "test_timeout": 15}},
        }
        regression.before_review(state, "sol", project.root, project.evidence)
        first = copy.deepcopy(state["regression_proof"])
        self.assertEqual("PASS", first["verdict"], first)
        old_receipt = Path(first["path"]).read_bytes()
        self.assertIn("docs/bugs/removed.json", json.loads(old_receipt)["changes"])
        baseline = copy.deepcopy(state["regression_baseline"])

        (project.root / "docs/bugs/removed.json").unlink()
        regression.before_review(state, "astra_review", project.root, project.evidence)
        current = state["regression_proof"]
        self.assertEqual("PASS", current["verdict"], current)
        self.assertNotEqual(first["source_revision"], current["source_revision"])
        self.assertNotEqual(first["path"], current["path"])
        self.assertNotIn("docs/bugs/removed.json", json.loads(Path(current["path"]).read_text())["changes"])
        self.assertEqual(baseline, state["regression_baseline"])
        self.assertEqual(old_receipt, Path(first["path"]).read_bytes())
        self.assertEqual(current["path"], regression.handoff(state)["path"])
        regression.before_review(state, "astra_review", project.root, project.evidence)
        self.assertEqual(current["path"], state["regression_proof"]["path"])
        self.assertEqual(2, len(state["regression_proofs"]))

    def test_operator_patch_mutation_invalidates_a_cached_complete_proof(self):
        import difflib

        import autocode_base_patch as base_patch
        import autocode_regression as regression
        import autocode_verify as verify

        from .test_verify import REFERENCE, SEED, Project

        project = Project()
        self.addCleanup(project.close)
        marker = "# Operator instrumentation marker\n"
        patch = Path(project.temp.name) / "base.patch"
        patch.write_text(
            "".join(
                difflib.unified_diff(
                    SEED["greet.py"].splitlines(True),
                    (marker + SEED["greet.py"]).splitlines(True),
                    "a/greet.py",
                    "b/greet.py",
                )
            )
        )
        pinned = base_patch.pin(patch, project.root, project.base)
        project.write({**REFERENCE, "greet.py": marker + REFERENCE["greet.py"]})
        state = {
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "base_commit": project.base,
            "settings": {"regression": {"python": self.python, "test_timeout": 15, "base_patch": pinned}},
        }
        identity = {"source_revision": verify.util.snapshot(project.root)["revision"], "reuse_supported": True}
        with mock.patch.object(verify, "execution_identity", return_value=identity):
            first = regression.prove(state, project.root, project.evidence)
            self.assertEqual("PASS", first["verdict"], first)
            self.assertTrue(any("operator's base patch" in reason for reason in first["review_reasons"]))
            self.assertEqual(first["path"], regression.prove(state, project.root, project.evidence)["path"])
            patch.write_text(patch.read_text() + "\n")
            changed = regression.prove(state, project.root, project.evidence)
        self.assertEqual(first["source_revision"], changed["source_revision"])
        self.assertEqual("UNVERIFIED", changed["verdict"], changed)
        self.assertIn("changed after it was set", " ".join(changed["unverified"]))
        self.assertFalse(regression.complete(state, changed["source_revision"]))

    def test_current_complete_proof_reuses_but_tampered_output_and_environment_do_not(self):
        import os

        import autocode_regression as regression

        from .test_verify import REFERENCE, Project

        project = Project()
        self.addCleanup(project.close)
        project.write(REFERENCE)
        state = {
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "base_commit": project.base,
            "settings": {"regression": {"python": self.python, "test_timeout": 15}},
        }
        first = regression.prove(state, project.root, project.evidence)
        self.assertEqual("PASS", first["verdict"], first)
        self.assertTrue(regression.complete(state, first["source_revision"]))
        self.assertEqual(first["path"], regression.prove(state, project.root, project.evidence)["path"])
        output = Path(first["checks"]["regression_on_candidate"]["output"])
        output.write_text("tampered PASS summary\n")
        self.assertFalse(regression.complete(state, first["source_revision"]))
        second = regression.prove(state, project.root, project.evidence)
        self.assertEqual("PASS", second["verdict"], second)
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual("tampered PASS summary\n", output.read_text(), "old evidence must never be overwritten")
        with mock.patch.dict(os.environ, {"VERIFICATION_FIXTURE_SEED": "changed"}):
            third = regression.prove(state, project.root, project.evidence)
        self.assertEqual("PASS", third["verdict"], third)
        self.assertNotEqual(second["path"], third["path"])

    def baseline_evidence(self, state):
        import autocode_verify as verify

        saved = copy.deepcopy(state["regression_baseline"])
        path = Path(saved["path"])
        value = json.loads(path.read_text())
        receipt = value["receipt"]
        self.assertEqual("passing", value["health"], value)
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertTrue(verify.schedule.complete_results(receipt), receipt)
        self.assertTrue(verify.schedule.intact(receipt, root=path.parent), receipt)
        self.assertEqual(saved["sha256"], verify.util.file_hash(path))
        cleanup = verify.command_receipt.pins(receipt)
        self.assertTrue(cleanup, "the baseline must retain its real supervision receipt")
        paths = [path, Path(receipt["output"]), *(Path(name) for name in cleanup)]
        return saved, {name: name.read_bytes() for name in paths}

    def test_generated_dependency_changes_invalidate_real_candidate_and_base_proofs(self):
        import autocode_regression as regression
        import autocode_verification_schedule as schedule
        import autocode_verify as verify

        from .test_verify import Project, git

        seed = {
            ".gitignore": "pkg/_generated.py\n__pycache__/\n",
            "pkg/__init__.py": "",
            "app.py": "from pkg._generated import OFFSET\ndef value(n):\n    return n + 1 + OFFSET\n",
            "test_app.py": "import unittest\nfrom app import value\nclass Case(unittest.TestCase):\n"
            "    def test_one_is_preserved(self):\n        self.assertEqual(2, value(1))\n",
        }
        project = Project(seed)
        self.addCleanup(project.close)
        project.write({"pkg/_generated.py": "OFFSET = 0\n"})
        candidate = Path(project.temp.name) / "candidate"
        git(project.root, "worktree", "add", "--detach", str(candidate), project.base)
        self.addCleanup(verify.remove_tree, project.root, candidate)
        (candidate / "app.py").write_text(
            "from pkg._generated import OFFSET\ndef value(n):\n    return (4 if n == 2 else n + 1) + OFFSET\n"
        )
        (candidate / "test_app.py").write_text(
            seed["test_app.py"] + "    def test_two_is_fixed(self):\n        self.assertEqual(4, value(2))\n"
        )
        state = {
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "base_commit": project.base,
            "project_workspace": str(project.root),
            "settings": {"regression": {"python": self.python, "test_timeout": 15}},
        }
        run = project.evidence
        saved_evidence = {}

        def retain(path):
            for item in Path(path).parent.rglob("*"):
                if item.is_file() and not item.is_symlink():
                    saved_evidence.setdefault(item, item.read_bytes())

        def assert_evidence_held():
            for path, original in saved_evidence.items():
                self.assertEqual(original, path.read_bytes(), f"old evidence changed: {path}")

        def assert_dependency_drift(before, after, key):
            self.assertEqual(
                {k: v for k, v in before.items() if k != key}, {k: v for k, v in after.items() if k != key}
            )
            self.assertEqual(
                {k: v for k, v in before[key].items() if k != "dependencies"},
                {k: v for k, v in after[key].items() if k != "dependencies"},
            )
            self.assertNotEqual(before[key]["dependencies"], after[key]["dependencies"])

        def assert_negative(proof):
            context_refusal = "Execution context changed while proving the candidate: dependencies"
            reasons = proof.get("unverified") or []
            context_reasons = [reason for reason in reasons if reason.startswith("Execution context changed")]
            if context_reasons:
                self.assertEqual([context_refusal], context_reasons, proof)
                self.assertEqual("UNVERIFIED", proof["verdict"], proof)
                self.assertTrue(
                    set(reasons).issubset(
                        {
                            context_refusal,
                            "The base suite provided no complete passing-test evidence; "
                            "preservation of existing behavior is unproven",
                        }
                    ),
                    proof,
                )
            else:
                self.assertEqual("FAIL", proof["verdict"], proof)
            path = Path(proof["path"])
            self.assertEqual(proof["receipt_sha256"], verify.util.file_hash(path))
            receipt = json.loads(path.read_text())
            self.assertEqual(proof["verdict"], receipt["verdict"])
            candidate_receipt = receipt["checks"]["suite_on_candidate"]
            self.assertEqual(1, candidate_receipt["exit_code"])
            self.assertFalse(candidate_receipt["timed_out"])
            self.assertFalse(candidate_receipt.get("error"))
            self.assertTrue(schedule.complete_results(candidate_receipt), candidate_receipt)
            self.assertTrue(schedule.intact(candidate_receipt, root=path.parent), candidate_receipt)
            self.assertIn("test_app.Case.test_two_is_fixed", candidate_receipt["results"]["failed"])

        def assert_edited_positive(proof):
            context_refusal = "Execution context changed while proving the candidate: dependencies"
            if proof.get("unverified"):
                self.assertEqual([context_refusal], proof["unverified"], proof)
                self.assertEqual("UNVERIFIED", proof["verdict"], proof)
                self.assertFalse(regression.complete(state, proof["source_revision"]))
            else:
                self.assertEqual("PASS", proof["verdict"], proof)
            self.assertEqual([], proof["failures"], proof)
            path = Path(proof["path"])
            self.assertEqual(proof["receipt_sha256"], verify.util.file_hash(path))
            receipt = json.loads(path.read_text())
            self.assertEqual(proof["verdict"], receipt["verdict"])
            old_test = "test_app.Case.test_one_is_preserved"
            new_test = "test_app.Case.test_two_is_fixed"
            self.assertEqual([new_test], receipt["fail_to_pass"])
            self.assertEqual([old_test], receipt["pass_to_pass"])
            expected = {
                "regression_on_candidate": (0, {old_test, new_test}, set()),
                "regression_on_base": (1, {old_test}, {new_test}),
                "suite_on_candidate": (0, {old_test, new_test}, set()),
            }
            self.assertEqual(set(expected), set(receipt["checks"]))
            for label, (exit_code, passed, failed) in expected.items():
                command_receipt = receipt["checks"][label]
                self.assertEqual(exit_code, command_receipt["exit_code"])
                self.assertFalse(command_receipt["timed_out"])
                self.assertFalse(command_receipt.get("error"))
                self.assertTrue(schedule.complete_results(command_receipt), command_receipt)
                self.assertTrue(schedule.intact(command_receipt, root=path.parent), command_receipt)
                self.assertEqual(passed, set(command_receipt["results"]["passed"]))
                self.assertEqual(failed, set(command_receipt["results"]["failed"]))
                self.assertEqual([], command_receipt["results"]["skipped"])

        first = regression.prove(state, candidate, run)
        self.assertEqual("PASS", first["verdict"], first)
        self.assertTrue(first["execution_context"]["identity"]["reuse_supported"])
        dependency_paths = {row["path"] for row in first["execution_context"]["identity"]["dependencies"] if row}
        self.assertIn(str(self.private_stdlib), dependency_paths)
        self.assertNotIn(str(self.host_stdlib), dependency_paths)
        self.assertFalse((candidate / "pkg/_generated.py").exists(), "input comes from dependencies_from")
        baseline, old_evidence = self.baseline_evidence(state)
        retain(first["path"])
        retain(baseline["path"])
        repeated = regression.prove(state, candidate, run)
        self.assertEqual("PASS", repeated["verdict"], repeated)
        if first["execution_context"] == repeated["execution_context"]:
            self.assertEqual(first["path"], repeated["path"])
        else:
            assert_dependency_drift(first["execution_context"], repeated["execution_context"], "identity")
            self.assertNotEqual(first["path"], repeated["path"])
        assert_evidence_held()
        retain(repeated["path"])
        repeated_baseline, repeated_evidence = self.baseline_evidence(state)
        retain(repeated_baseline["path"])
        self.assertEqual(
            {key: value for key, value in baseline["binding"].items() if key != "runtime"},
            {key: value for key, value in repeated_baseline["binding"].items() if key != "runtime"},
        )
        for binding in (baseline["binding"], repeated_baseline["binding"]):
            for key in ("source_revision", "source_metadata", "generated_sources", "unbound_source_symlinks"):
                self.assertNotIn(key, binding["runtime"])
        if baseline["binding"] == repeated_baseline["binding"]:
            self.assertEqual(baseline["path"], repeated_baseline["path"])
        else:
            assert_dependency_drift(baseline["binding"], repeated_baseline["binding"], "runtime")
            self.assertNotEqual(baseline["path"], repeated_baseline["path"])
        for name, data in old_evidence.items():
            self.assertEqual(data, name.read_bytes(), f"original evidence changed: {name}")
        baseline, old_evidence = repeated_baseline, repeated_evidence

        # A Builder edit needs a new candidate proof, but leaves the original
        # suite unchanged. Reuse requires the whole base execution binding to
        # remain unchanged; real dependency drift must create fresh evidence.
        with (candidate / "app.py").open("a") as handle:
            handle.write("# unrelated candidate edit\n")
        edited = regression.prove(state, candidate, run)
        assert_edited_positive(edited)
        self.assertNotEqual(first["path"], edited["path"])
        edited_baseline = state["regression_baseline"]
        if baseline["binding"] == edited_baseline["binding"]:
            self.assertEqual(baseline["path"], edited_baseline["path"])
        else:
            assert_dependency_drift(baseline["binding"], edited_baseline["binding"], "runtime")
            self.assertNotEqual(baseline["path"], edited_baseline["path"])
        assert_evidence_held()
        retain(edited["path"])
        retain(edited_baseline["path"])
        current_baseline, current_evidence = self.baseline_evidence(state)
        self.assertEqual(
            {key: value for key, value in baseline["binding"].items() if key != "runtime"},
            {key: value for key, value in current_baseline["binding"].items() if key != "runtime"},
        )
        for binding in (baseline["binding"], current_baseline["binding"]):
            for key in ("source_revision", "source_metadata", "generated_sources", "unbound_source_symlinks"):
                self.assertNotIn(key, binding["runtime"])
        if baseline["binding"] == current_baseline["binding"]:
            self.assertEqual(baseline["path"], current_baseline["path"])
        else:
            self.assertNotEqual(baseline["path"], current_baseline["path"])
        for name, data in old_evidence.items():
            self.assertEqual(data, name.read_bytes(), f"original evidence changed: {name}")
        baseline, old_evidence = current_baseline, current_evidence

        project.write({"pkg/_generated.py": "OFFSET = 1\n"})
        changed = regression.prove(state, candidate, run)
        self.assertEqual(edited["source_revision"], changed["source_revision"])
        assert_negative(changed)
        self.assertNotEqual(edited["path"], changed["path"])
        self.assertNotEqual(edited_baseline["path"], state["regression_baseline"]["path"])
        assert_evidence_held()
        retain(changed["path"])
        retain(state["regression_baseline"]["path"])
        self.assertNotEqual(baseline["path"], state["regression_baseline"]["path"])
        self.assertNotEqual(
            baseline["binding"]["runtime"]["generated_dependency_sources"],
            state["regression_baseline"]["binding"]["runtime"]["generated_dependency_sources"],
        )
        for name, data in old_evidence.items():
            self.assertEqual(data, name.read_bytes(), f"previous evidence changed: {name}")
        receipt = json.loads(Path(changed["path"]).read_text())
        self.assertEqual(1, receipt["checks"]["suite_on_candidate"]["exit_code"])
        self.assertIn("test_app.Case.test_two_is_fixed", receipt["checks"]["suite_on_candidate"]["results"]["failed"])

        # A fresh proof of exactly the changed inputs is the independent negative
        # control: cache invalidation must agree with actual Python execution.
        fresh = regression.prove(
            copy.deepcopy(
                {key: state[key] for key in ("goal_contract", "base_commit", "project_workspace", "settings")}
            ),
            candidate,
            run / "fresh",
        )
        assert_negative(fresh)
        self.assertEqual(changed["source_revision"], fresh["source_revision"])
        assert_evidence_held()

    def test_owned_runtime_file_change_invalidates_a_real_baseline(self):
        import autocode_regression as regression

        from .test_verify import REFERENCE, Project

        sites = list(Path(self.python).parent.parent.glob("lib/python*/site-packages"))
        self.assertEqual(1, len(sites), sites)
        marker = sites[0] / "verification-runtime-input.txt"
        marker.write_text("first runtime input\n")
        project = Project()
        self.addCleanup(project.close)
        project.write(REFERENCE)
        state = {
            "goal_contract": {"body": {"task_kind": "bugfix"}},
            "base_commit": project.base,
            "settings": {"regression": {"python": self.python, "test_timeout": 15}},
        }
        first = regression.prove(state, project.root, project.evidence)
        self.assertEqual("PASS", first["verdict"], first)
        baseline, old_evidence = self.baseline_evidence(state)

        marker.write_text("changed runtime input\n")
        changed = regression.prove(state, project.root, project.evidence)
        self.assertEqual("PASS", changed["verdict"], changed)
        self.assertEqual(first["source_revision"], changed["source_revision"])
        self.assertNotEqual(first["path"], changed["path"])
        current_baseline, _ = self.baseline_evidence(state)
        self.assertNotEqual(baseline["path"], current_baseline["path"])
        self.assertNotEqual(
            baseline["binding"]["runtime"]["dependencies"], current_baseline["binding"]["runtime"]["dependencies"]
        )
        for name, data in old_evidence.items():
            self.assertEqual(data, name.read_bytes(), f"original evidence changed: {name}")

    def test_changed_detected_suite_reruns_proof_for_unchanged_source_and_options(self):
        import autocode_regression as regression
        import autocode_verify as verify

        with tempfile.TemporaryDirectory() as directory:
            project = Path(directory) / "project"
            project.mkdir()
            bugfix.references.write(bugfix.scenarios.BUGFIX_SEED, project)
            subprocess.run(["git", "init", "-q", str(project)], check=True)
            subprocess.run(["git", "-C", str(project), "add", "."], check=True)
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
                    "unfixed seed",
                ],
                check=True,
            )
            base = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
            bugfix.references.write(bugfix.references.BUGFIX_REFERENCE, project)
            state = {
                "goal_contract": {"body": {"task_kind": "bugfix"}},
                "base_commit": base,
                "settings": {"regression": {"python": self.python, "test_timeout": 15}},
            }
            original_options = copy.deepcopy(state["settings"])
            run = project / ".autocode" / "runs" / "fixture"
            python = shlex.quote(self.python)
            working = verify.Framework("unittest", f"{python} -m unittest discover -v", python=self.python)
            failing = verify.Framework("unittest", f'{python} -c "raise SystemExit(7)"', python=self.python)
            with mock.patch.object(verify, "detect_framework", return_value=working):
                first = regression.prove(state, project, run)
            self.assertEqual("PASS", first["verdict"], first)
            with mock.patch.object(verify, "detect_framework", return_value=failing):
                second = regression.prove(state, project, run)
            self.assertEqual(original_options, state["settings"])
            self.assertEqual(first["source_revision"], second["source_revision"])
            self.assertNotEqual("PASS", second["verdict"], second)
            self.assertEqual(failing.suite, second["commands"]["suite"])
            self.assertEqual(2, len(state["regression_proofs"]))
            self.assertNotEqual(first["path"], second["path"])
            self.assertTrue(Path(first["path"]).is_file())
            receipt = json.loads(Path(second["path"]).read_text())
            self.assertEqual(7, receipt["checks"]["suite_on_candidate"]["exit_code"])


if __name__ == "__main__":
    unittest.main()
