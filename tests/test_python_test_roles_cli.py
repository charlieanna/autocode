"""Real named proof keeps original suffix-named product bytes on the base side."""

import json
import shlex
import subprocess
import sys
import tempfile
import unittest
import venv
from pathlib import Path

import autocode_protected_oracles as protected
import autocode_verify as verify


@unittest.skipUnless(sys.platform == "linux", "supervised test commands require supported Linux")
class PythonRoleProofTests(unittest.TestCase):
    def test_named_proof_keeps_product_on_base_and_custom_tests_identical(self):
        with tempfile.TemporaryDirectory(prefix="python-role-proof-") as temporary:
            root = Path(temporary) / "project"
            root.mkdir()
            (root / "pkg").mkdir()
            (root / "pkg/__init__.py").write_text("")
            (root / "pkg/billing_tests.py").write_text("def charge():\n    return 1\n")
            (root / "pyproject.toml").write_text('[tool.setuptools]\npackages=["pkg"]\n')
            original = (
                "import unittest\nfrom pkg.billing_tests import charge\n"
                "class BillingChecks(unittest.TestCase):\n"
                "    def test_original_integer(self):\n        self.assertIsInstance(charge(), int)\n"
            )
            (root / "checks_tests.py").write_text(original)
            for command in (
                ["git", "init", "-q"],
                ["git", "config", "maintenance.auto", "false"],
                ["git", "config", "gc.auto", "0"],
                ["git", "add", "."],
                ["git", "-c", "user.name=Fixture", "-c", "user.email=f@example.test", "commit", "-qm", "original"],
            ):
                subprocess.run(command, cwd=root, check=True, capture_output=True)
            base = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=root, text=True).strip()
            runtime = Path(temporary) / "runtime"
            venv.EnvBuilder(with_pip=False).create(runtime)
            python = str(runtime / "bin/python")
            suite = f"{shlex.quote(python)} -m unittest -v checks_tests"
            framework = verify.command_framework(suite)
            evidence = Path(temporary) / "evidence"
            baseline = verify.baseline(root, base, evidence, framework=framework, suite_command=suite, timeout=60)
            (root / "pkg/billing_tests.py").write_text("def charge():\n    return 2\n")
            (root / "checks_tests.py").write_text(
                original + "    def test_corrected_charge(self):\n        self.assertEqual(2, charge())\n"
            )
            result = verify.verify(
                root, base, evidence, framework=framework, suite_command=suite, base_suite=baseline, timeout=60
            )
            self.assertEqual(verify.PASS, result["verdict"], result)
            self.assertEqual(["pkg/billing_tests.py"], result["source_files"])
            self.assertEqual(["checks_tests.py"], result["test_files"])
            self.assertIn("checks_tests.BillingChecks.test_corrected_charge", result["fail_to_pass"])
            self.assertIn("checks_tests.BillingChecks.test_original_integer", result["pass_to_pass"])
            self.assertIn("2 != 1", Path(result["checks"]["regression_on_base"]["output"]).read_text())
            self.assertNotIn("pkg/billing_tests.py", protected.inventory(root, verify.is_test_path))


@unittest.skipUnless(sys.platform == "linux", "supervised fake provider requires supported Linux")
class PythonRoleMigrationCliTests(unittest.TestCase):
    """The legacy assembly is a fixture; the observed transition uses TaskRun only."""

    def test_public_resume_corrects_legacy_oracles_and_retires_proof_at_intentional_cap(self):
        import hashlib
        import shutil

        import autocode_protected_paths as paths
        import autocode_protected_store as store
        import autocode_regression as regression
        import autocode_util as util
        from autocode_taskrun import TaskRun

        from . import test_bugfix_workflow as fixture

        # Reuse the existing hermetic provider fixture without running any of its tests.
        self.new_run_engine_args = ()
        self.launch = fixture.BugfixWorkflow.launch.__get__(self)
        self.saved = fixture.BugfixWorkflow.saved.__get__(self)
        self.prepare = fixture.BugfixWorkflow.prepare.__get__(self)
        self.draft = fixture.BugfixWorkflow.draft.__get__(self)
        self.builder_writes = fixture.BugfixWorkflow.builder_writes.__get__(self)
        fixture.BugfixWorkflow.setUp(self)
        (self.project / "pkg").mkdir()
        (self.project / "pkg/__init__.py").write_text("")
        (self.project / "pkg/billing_tests.py").write_text("def charge():\n    return 1\n")
        (self.project / "pyproject.toml").write_text('[tool.setuptools]\npackages=["pkg"]\n')
        subprocess.run(["git", "-C", str(self.project), "add", "."], check=True)
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
                "original runtime module",
            ],
            check=True,
            capture_output=True,
        )
        self.builder_writes(
            {
                "greet.py": fixture.references.BUGFIX_REFERENCE["greet.py"],
                "test_greet.py": fixture.references.BUGFIX_REFERENCE["test_greet.py"],
            }
        )
        run_dir, _ = self.draft()
        run = TaskRun(
            self.project,
            run_dir,
            tuple(self.entry),
            ("--pause-after-stage",),
            self.env,
            240,
        )
        view = run.status()
        self.assertIn("greet", run.show_goal().lower())
        run.approve_plan(view["needs"]["token"])
        for _ in range(3):
            view = run.resume_paused() if run.status()["status"].startswith("PAUSED_") else run.advance()
            if view["next_stage"] == "tester":
                break
        self.assertEqual("tester", view["next_stage"])
        self.assertTrue(view["status"].startswith("PAUSED_"))

        # Assemble a saved pre-upgrade fixture through core producers. Real
        # original oracle bytes come from their archive, never the candidate.
        state_path = run_dir / "state.json"
        state = util.read_object(state_path)
        original = state["settings"]["protected_tests"]
        original_root = self.root / "legacy-original"
        with store.opened(original, self.project) as retained:
            shutil.copytree(retained, original_root)
        product = original_root / "pkg/billing_tests.py"
        product.parent.mkdir(parents=True, exist_ok=True)
        product.write_bytes(
            subprocess.check_output(
                [
                    "git",
                    "-C",
                    str(self.project),
                    "show",
                    state["base_commit"] + ":pkg/billing_tests.py",
                ]
            )
        )
        product.chmod(0o644)
        legacy_files = {**original["files"], "pkg/billing_tests.py": paths.identity(product)}
        legacy = protected.retain(original_root, run_dir, legacy_files, original["command"])
        state["settings"]["protected_tests"] = legacy
        proof = regression.prove(state, self.project, run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], proof)
        # The fixture represents an old policy, whose observations must remain
        # historical even though the source has not changed.
        proof["execution_context"].pop("test_role_policy", None)
        proof_path = Path(proof["path"])
        proof_bytes = proof_path.read_bytes()
        state["settings"]["limits"]["max_seconds"] = 1
        state["settings"].setdefault("budget_origins", {})["max_seconds"] = "user_explicit"
        state["active_seconds"] = max(2, state.get("active_seconds", 0))
        util.atomic_json(state_path, state)

        # The public unit runner holds at this explicit cap without launching an Investigator.
        run.options = ("--unit", "autoreview")
        before_hash = hashlib.sha256(state_path.read_bytes()).hexdigest()
        before = run.status()
        self.assertEqual(before_hash, hashlib.sha256(state_path.read_bytes()).hexdigest())
        self.assertIn("pkg/billing_tests.py", before["evidence"]["protected_tests"]["files"])
        self.assertEqual("PASS", before["evidence"]["regression_proof"]["verdict"])
        self.assertIn("--resume-paused", before["next_command"])
        before_attempts = before["usage"]["accounting"]["attempts"]
        after = run.resume_paused()
        self.assertEqual("WAITING_FOR_USER", after["status"], after["needs"])
        self.assertEqual("answer", after["needs"]["kind"])
        self.assertEqual("tester", after["next_stage"])
        cap_questions = json.dumps(after["needs"].get("questions") or []).lower()
        self.assertIn("active time", cap_questions)
        for term in ("classification", "oracle", "baseline", "protected test", "regression proof"):
            self.assertNotIn(term, cap_questions)
        current = after["evidence"]["protected_tests"]
        self.assertEqual(original["files"], current["files"])
        self.assertEqual(legacy["command"], current["command"])
        self.assertNotIn("pkg/billing_tests.py", current["files"])
        self.assertIsNone(after["evidence"]["regression_proof"])
        after_attempts = after["usage"]["accounting"]["attempts"]
        self.assertEqual(
            [(row["identity"], row["stage"]) for row in before_attempts],
            [(row["identity"], row["stage"]) for row in after_attempts[: len(before_attempts)]],
        )
        new_attempts = after_attempts[len(before_attempts) :]
        self.assertTrue(
            all(
                row["stage"] not in ("builder", "tester", "review_checkpoint", "investigate_stuck")
                for row in new_attempts
            )
        )
        self.assertTrue(all(row.get("runner_owned") is True for row in new_attempts))
        self.assertTrue(any(row["kind"] == "protected_tests_roles_corrected" for row in after["recoveries"]))
        self.assertEqual(proof_bytes, proof_path.read_bytes())
        protected.verify_binding(legacy)
        protected.verify_binding(current)
