"""Standalone external-change verification through the public CLI."""

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid
from contextlib import redirect_stderr, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

import autocode_verify_contract as criteria_policy
import autocode_verify_record as records
import autocode_verify_task as verifier

ROOT = Path(__file__).resolve().parents[1]
FAKE = """
import json, os, subprocess, sys, uuid
from pathlib import Path
if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT (offline fixture)")
    raise SystemExit(0)
if os.environ.get("AUTOCODE_TEST_REVIEW_CUSTODY"):
    assert not os.environ.get("GH_TOKEN"), "GitHub token reached review provider"
    assert not os.environ.get("GH_CONFIG_DIR"), "GitHub config reached review provider"
    subprocess.run([sys.executable, "-E", "-c", "import double; import tests.test_guard"], check=True)
if os.environ.get("AUTOCODE_TEST_REVIEW_INJECT"):
    Path("injected.dat").write_text("unexpected ignored provider payload")
data = json.loads(sys.stdin.read().split("CURRENT HANDOFF DATA\\n", 1)[1])
if data["stage"] != "review_change":
    raise SystemExit("Unexpected stage: " + data["stage"])
report = {"verdict": "approve", "summary": "Fixture approval; runner still must prove the change",
          "change_under_review": "Current workspace change", "change_patch": "",
          "findings": [], "tests_run": [], "delivered_tests": []}
print(json.dumps({"type":"thread.started", "thread_id":str(uuid.uuid4())}))
Path(sys.argv[sys.argv.index("-o")+1]).write_text(json.dumps(report))
print(json.dumps({"type":"turn.completed", "usage":{"input_tokens":20,"output_tokens":10}}))
"""


def clean_git_environment():
    # The suite injects maintenance overrides; pin them in each fixture's local
    # config instead so the real verifier still refuses ambient Git authority.
    return {
        name: value
        for name, value in os.environ.items()
        if name not in records.GIT_REDIRECTIONS and name != "GIT_CONFIG" and not name.startswith("GIT_CONFIG_")
    }


def contract(command, *, human=False):
    return {
        "intended_outcome": "Double every integer",
        "acceptance_criteria": [
            {
                "id": "C1",
                "criterion": "Doubling three returns six",
                "verification_method": command,
                "human_review": human,
            }
        ],
    }


class CriteriaTests(unittest.TestCase):
    def test_related_profile_pair_is_required_before_any_review_launch(self):
        for extra in (("--related-profile", "profile.json"), ("--related-profile-sha256", "0" * 64)):
            with self.subTest(extra=extra), mock.patch.object(verifier, "verify") as launch:
                error = StringIO()
                with mock.patch.dict(os.environ, clean_git_environment(), clear=True):
                    with redirect_stderr(error):
                        result = verifier.cli(["--json", *extra])
                self.assertEqual(4, result)
                refusal = json.loads(error.getvalue())
                self.assertEqual("UNVERIFIED", refusal["verdict"])
                self.assertIn("supplied together", refusal["error"])
                launch.assert_not_called()

    def test_related_profile_cannot_silently_discard_explicit_review_routes(self):
        for flag, value in (
            ("--engine", "codex"),
            ("--provider", "kilocode"),
            ("--model", "selected-model"),
            ("--reasoning-effort", "high"),
            ("--evidence-provenance", "fake"),
        ):
            with self.subTest(flag=flag), mock.patch.object(verifier, "verify") as launch:
                launch.return_value = {"verdict": "PASS", "record": "fixture"}
                error = StringIO()
                with mock.patch.dict(os.environ, clean_git_environment(), clear=True):
                    with redirect_stderr(error), redirect_stdout(StringIO()):
                        code = verifier.cli(
                            [
                                "--base",
                                "HEAD",
                                "--criteria",
                                json.dumps(contract("python3 -m unittest tests")),
                                "--related-profile",
                                "profile.json",
                                "--related-profile-sha256",
                                "0" * 64,
                                flag,
                                value,
                            ]
                        )
                self.assertEqual(4, code, error.getvalue())
                self.assertIn("--related-profile", error.getvalue())
                self.assertIn(flag, error.getvalue())
                launch.assert_not_called()

    def test_missing_empty_duplicate_or_prose_criteria_are_refused(self):
        for value in (
            "{}",
            '{"intended_outcome":"x","intended_outcome":"y","acceptance_criteria":[]}',
            json.dumps(contract("Run tests and inspect output")),
            json.dumps(contract("true")),
        ):
            with self.subTest(value=value), self.assertRaises(ValueError):
                criteria_policy.load(value)

    def test_a_criterion_may_set_its_check_environment_with_env(self):
        command = "env GOWORK=off GOFLAGS= go test -count=1 -run '^TestX$' ./pkg/"
        parsed = criteria_policy.load(json.dumps(contract(command)))
        self.assertEqual(command, parsed["acceptance_criteria"][0]["verification_method"])
        for refused in ("GOWORK=off go test ./pkg/", "env GOPATH=$HOME go test ./pkg/"):
            with self.subTest(refused=refused), self.assertRaises(ValueError):
                criteria_policy.load(json.dumps(contract(refused)))

    def test_caller_criteria_are_pinned_and_human_acceptance_is_retained(self):
        value = contract("python3 -m unittest discover -s tests -v", human=True)
        parsed = criteria_policy.load(json.dumps(value))
        self.assertTrue(parsed["acceptance_criteria"][0]["human_review"])
        value["acceptance_criteria"][0]["criterion"] = "Different behavior"
        self.assertNotEqual(parsed["sha256"], criteria_policy.load(json.dumps(value))["sha256"])


class VerifyCliTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temporary = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temporary.name).resolve()
        cls.base = cls.root / "base"
        cls.base.mkdir()
        (cls.base / "double.py").write_text("def double(number):\n    return number\n")
        (cls.base / "tests").mkdir()
        (cls.base / "tests/__init__.py").write_text("")
        (cls.base / "tests/test_guard.py").write_text(
            "import unittest\nfrom double import double\n"
            "class Guard(unittest.TestCase):\n    def test_zero(self): self.assertEqual(0, double(0))\n"
        )
        for args in (
            ("init", "-q"),
            ("config", "maintenance.auto", "false"),
            ("config", "gc.auto", "0"),
            ("add", "."),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base"),
        ):
            subprocess.run(["git", "-C", str(cls.base), *args], check=True, capture_output=True)
        cls.sha = subprocess.check_output(["git", "-C", str(cls.base), "rev-parse", "HEAD"], text=True).strip()
        cls.bin = cls.root / "bin"
        cls.bin.mkdir()
        fixture = cls.root / "fake_codex.py"
        fixture.write_text(FAKE)
        wrapper = cls.bin / "codex"
        wrapper.write_text(
            "#!/bin/sh\nexec " + shlex.quote(sys.executable) + " -B " + shlex.quote(str(fixture)) + ' "$@"\n'
        )
        wrapper.chmod(0o755)

    @classmethod
    def tearDownClass(cls):
        cls.temporary.cleanup()

    def project(self, name):
        project = self.root / name
        shutil.copytree(self.base, project)
        (project / "double.py").write_text("def double(number):\n    return number * 2\n")
        (project / "tests/test_new.py").write_text(
            "import unittest\nfrom double import double\n"
            "class New(unittest.TestCase):\n    def test_c1_double(self): self.assertEqual(6, double(3))\n"
        )
        return project

    def run_cli(
        self,
        project,
        out,
        criteria,
        *,
        nested_import=False,
        inject_ignored=False,
        selection=(),
        base=None,
        extra_environment=None,
    ):
        environment = dict(
            clean_git_environment(),
            PATH=os.pathsep.join((str(self.bin), str(Path(sys.executable).parent), os.environ["PATH"])),
            AUTOCODE_HOME=str(self.root / "registry"),
            AUTOCODE_ATTENTION_HOME=str(self.root / "attention"),
            CODEX_HOME=str(self.root / "codex-home"),
            **(extra_environment or {}),
        )
        if inject_ignored:
            environment["AUTOCODE_TEST_REVIEW_INJECT"] = "1"
        if nested_import:
            environment.update(
                AUTOCODE_TEST_REVIEW_CUSTODY="1",
                AUTOCODE_PASS_ENV="GH_TOKEN,GH_CONFIG_DIR",
                GH_TOKEN="fixture-token-must-not-reach-review",
                GH_CONFIG_DIR=str(self.root / "fixture-github-config"),
            )
        result = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "tools/autocode.py"),
                "verify",
                "--workspace",
                str(project),
                "--base",
                base or self.sha,
                *selection,
                "--criteria",
                json.dumps(criteria),
                "--out",
                str(out),
                "--engine",
                "codex",
                "--model",
                "gpt-6-sol",
                "--evidence-provenance",
                "fake",
                "--json",
            ],
            capture_output=True,
            text=True,
            env=environment,
            timeout=360,
        )
        return result, json.loads(result.stdout) if result.stdout else None

    def test_nested_pytest_proof_preserves_ids_and_empty_package_marker(self):
        project = self.project("nested-pytest-proof")
        (project / "tests/__init__.py").unlink()
        (project / "tests/test_new.py").write_text(
            "from double import double\ndef test_c1_double():\n    assert double(3) == 6\n"
        )
        parent_config = self.root / "pyproject.toml"
        self.assertFalse(parent_config.exists())
        parent_config.write_text('[project]\nname = "outer-host"\nversion = "0.0.0"\n')
        self.addCleanup(parent_config.unlink)
        preserved = (project / "tests/test_guard.py").read_bytes()
        command = shlex.join([sys.executable, "-m", "pytest", "-q", "tests"])
        result, value = self.run_cli(project, self.root / "nested-pytest-output", contract(command))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("PASS", value["verdict"])
        proof = value["evidence_report"]["document"]["regression_proof"]
        self.assertEqual(["tests.test_new::test_c1_double"], proof["fail_to_pass"])
        self.assertEqual(preserved, (project / "tests/test_guard.py").read_bytes())
        self.assertFalse((project / "tests/__init__.py").exists())

    def test_deleted_real_tests_and_nonempty_package_initializers_still_refuse(self):
        for label, path in (("real-test", "tests/test_guard.py"), ("package-import", "tests/__init__.py")):
            with self.subTest(label=label):
                project = self.project("deleted-" + label)
                base = self.sha
                if label == "package-import":
                    (project / path).write_text("import math\n")
                    git = ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(project)]
                    subprocess.run([*git, "add", path], check=True, capture_output=True, env=clean_git_environment())
                    subprocess.run(
                        [
                            *git,
                            "-c",
                            "user.name=Fixture",
                            "-c",
                            "user.email=fixture@example.invalid",
                            "commit",
                            "-qm",
                            "nonempty package initializer",
                        ],
                        check=True,
                        capture_output=True,
                        env=clean_git_environment(),
                    )
                    base = subprocess.check_output([*git, "rev-parse", "HEAD"], text=True).strip()
                (project / path).unlink()
                command = shlex.join([sys.executable, "-m", "pytest", "-q", "tests"])
                result, value = self.run_cli(
                    project, self.root / ("deleted-" + label + "-output"), contract(command), base=base
                )
                self.assertEqual("FAIL", value["verdict"], result.stdout + result.stderr)
                proof = value["evidence_report"]["document"]["regression_proof"]
                self.assertIn("Existing test files were deleted: " + path, proof["failures"])

    def test_added_empty_test_package_preserves_the_unmarked_baseline_suite(self):
        project = self.root / "added-test-package"
        shutil.copytree(self.base, project)
        for args in (
            ("rm", "tests/__init__.py"),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "plain tests"),
        ):
            subprocess.run(["git", "-C", str(project), *args], check=True, capture_output=True)
        base = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
        (project / "tests/__init__.py").write_text("")
        (project / "double.py").write_text("def double(number):\n    return number * 2\n")
        (project / "tests/test_new.py").write_text(
            "import unittest\nfrom double import double\n"
            "class New(unittest.TestCase):\n    def test_c1_double(self): self.assertEqual(6, double(3))\n"
        )
        command = shlex.quote(sys.executable) + " -m unittest discover -s tests -v"
        result, value = self.run_cli(project, self.root / "added-package-output", contract(command), base=base)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("PASS", value["verdict"])
        proof = value["evidence_report"]["document"]["regression_proof"]
        self.assertTrue(proof["fail_to_pass"])
        self.assertTrue(proof["commands"]["suite"].endswith("discover -v -s tests"), proof)

    def test_external_change_passes_without_plan_or_builder_and_later_drift_is_refused(self):
        project = self.project("pass")
        source = {str(p.relative_to(project)): p.read_bytes() for p in project.rglob("*") if p.is_file()}
        command = shlex.quote(sys.executable) + " -m unittest discover -s tests -v"
        result, value = self.run_cli(project, self.root / "pass-output", contract(command))
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("PASS", value["verdict"])
        report = value["evidence_report"]["document"]
        self.assertEqual("verification", report["kind"])
        self.assertEqual("PASS", report["regression_proof"]["verdict"])
        self.assertTrue(report["regression_proof"]["fail_to_pass"])
        self.assertEqual(["C1"], [row["id"] for row in report["criteria"]])
        self.assertTrue(report["checks"])
        status = json.loads((self.root / "pass-output/review-status.json").read_text())
        self.assertEqual("review", status["workflow"])
        self.assertEqual(["review_change"], [row["stage"] for row in report["attempts"]])
        self.assertEqual(
            source, {str(p.relative_to(project)): p.read_bytes() for p in project.rglob("*") if p.is_file()}
        )
        (project / "double.py").write_text("def double(number):\n    return 0\n")
        stale = subprocess.run(
            [
                sys.executable,
                "-B",
                str(ROOT / "tools/autocode.py"),
                "verify",
                "--record",
                str(self.root / "pass-output"),
                "--json",
            ],
            capture_output=True,
            text=True,
            env=clean_git_environment(),
            timeout=30,
        )
        self.assertEqual(2, stale.returncode, stale.stderr)
        self.assertEqual("UNVERIFIED", json.loads(stale.stdout)["verdict"])
        self.assertEqual("stale", json.loads(stale.stdout)["availability"])

    def test_an_editor_terminals_git_config_parameters_are_cleared_and_recorded(self):
        # Cursor and VS Code export these in their integrated terminals (#2504).
        secret = "editor-value-that-must-not-be-logged"
        editor = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": secret}
        project = self.project("editor-terminal")
        command = shlex.quote(sys.executable) + " -m unittest discover -s tests -v"
        out = self.root / "editor-output"
        result, value = self.run_cli(project, out, contract(command), extra_environment=editor)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("PASS", value["verdict"])
        receipt = json.loads((out / "verification.json").read_text())
        self.assertEqual(sorted(editor), receipt["original"]["cleared_git_config"])
        self.assertNotIn(secret, result.stdout + result.stderr)

    def test_a_test_skipped_by_the_change_is_caught_against_the_base_suite(self):
        project = self.project("skipped-guard")
        (project / "double.py").write_text("def double(number):\n    return number * 2 + (number == 0)\n")
        (project / "tests/test_guard.py").write_text(
            "import unittest\nfrom double import double\n"
            "class Guard(unittest.TestCase):\n    @unittest.skip('later')\n"
            "    def test_zero(self): self.assertEqual(0, double(0))\n"
        )
        command = shlex.quote(sys.executable) + " -m unittest discover -s tests -v"
        result, value = self.run_cli(project, self.root / "skipped-output", contract(command))
        self.assertNotEqual("PASS", value["verdict"], result.stdout + result.stderr)
        self.assertIn("test_zero", json.dumps(value))

    def test_finished_branch_is_verified_without_using_dirty_working_tree(self):
        project = self.project("finished-branch")
        for arguments in (
            ("checkout", "-qb", "finished-change"),
            ("add", "double.py", "tests/test_new.py"),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "external change"),
        ):
            subprocess.run(
                ["git", "-c", "core.hooksPath=" + os.devnull, "-C", str(project), *arguments],
                env=clean_git_environment(),
                capture_output=True,
                check=True,
            )
        (project / "double.py").write_text("def double(number):\n    return 0\n")
        with mock.patch.dict(os.environ, clean_git_environment(), clear=True):
            original = records.source_identity(project)
            original_index = records.index_identity(project)
        command = shlex.quote(sys.executable) + " -m unittest discover -s tests -v"
        result, value = self.run_cli(
            project, self.root / "branch-output", contract(command), selection=("--branch", "finished-change")
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("PASS", value["verdict"])
        self.assertEqual(["review_change"], [row["stage"] for row in value["evidence_report"]["document"]["attempts"]])
        with mock.patch.dict(os.environ, clean_git_environment(), clear=True):
            self.assertEqual(original, records.source_identity(project))
            self.assertEqual(original_index, records.index_identity(project))

    def test_external_patch_is_verified_without_using_dirty_working_tree(self):
        project = self.project("external-patch")
        subprocess.run(
            ["git", "-C", str(project), "add", "-N", "tests/test_new.py"],
            env=clean_git_environment(),
            capture_output=True,
            check=True,
        )
        patch = self.root / "finished-change.patch"
        patch.write_bytes(
            subprocess.check_output(
                ["git", "-C", str(project), "diff", "--no-ext-diff", "--no-textconv", "--binary", self.sha, "--"],
                env=clean_git_environment(),
            )
        )
        (project / "double.py").write_text("def double(number):\n    return 0\n")
        with mock.patch.dict(os.environ, clean_git_environment(), clear=True):
            original = records.source_identity(project)
            original_index = records.index_identity(project)
        command = shlex.quote(sys.executable) + " -m unittest discover -s tests -v"
        result, value = self.run_cli(
            project, self.root / "patch-output", contract(command), selection=("--patch", str(patch))
        )
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("PASS", value["verdict"])
        self.assertEqual(["review_change"], [row["stage"] for row in value["evidence_report"]["document"]["attempts"]])
        with mock.patch.dict(os.environ, clean_git_environment(), clear=True):
            self.assertEqual(original, records.source_identity(project))
            self.assertEqual(original_index, records.index_identity(project))

    @unittest.skipIf(os.geteuid() == 0, "POSIX cache-write prevention requires an unprivileged process")
    def test_nested_python_import_preserves_review_source_and_withholds_github_credentials(self):
        project = self.project("nested-import")
        out = self.root / "nested-output"
        command = shlex.quote(sys.executable) + " -m unittest discover -s tests -v"
        result, value = self.run_cli(project, out, contract(command), nested_import=True)
        self.assertEqual(0, result.returncode, result.stdout + result.stderr)
        self.assertEqual("PASS", value["verdict"])
        protected = json.loads((out / "python-cache-protection.json").read_text())
        self.assertEqual({"__pycache__", "tests/__pycache__"}, {row["path"] for row in protected["directories"]})
        for row in protected["directories"]:
            cache = out / "independent-review" / row["path"]
            self.assertEqual([], list(cache.iterdir()))
            self.assertEqual(0o555, cache.stat().st_mode & 0o777)
        with mock.patch.dict(os.environ, clean_git_environment(), clear=True):
            saved = records.read(out)
        self.assertEqual("PASS", saved["verdict"])
        self.assertEqual("current", saved["availability"])

    def test_ignored_review_payload_cannot_borrow_unchanged_source_and_approval(self):
        project = self.project("ignored-payload")
        (project / ".gitignore").write_text("injected.dat\n")
        out = self.root / "ignored-output"
        with mock.patch.dict(os.environ, clean_git_environment(), clear=True):
            original = records.source_identity(project)
            original_index = records.index_identity(project)
        command = shlex.quote(sys.executable) + " -m unittest discover -s tests -v"
        result, value = self.run_cli(project, out, contract(command), inject_ignored=True)
        self.assertEqual(4, result.returncode, result.stdout + result.stderr)
        self.assertIsNone(value)
        self.assertIn("unexpected source files: injected.dat", json.loads(result.stderr)["error"])
        self.assertFalse((out / "verification.json").exists())
        status = json.loads((out / "review-status.json").read_text())
        self.assertTrue(status["done"])
        self.assertEqual("review", status["workflow"])
        review_workspace = out / "independent-review"
        self.assertEqual(
            "approve",
            json.loads((review_workspace / "review/findings.json").read_text())["verdict"],
        )
        self.assertEqual(
            "unexpected ignored provider payload",
            (review_workspace / "injected.dat").read_text(),
        )
        subprocess.run(
            [
                "git",
                "-C",
                str(review_workspace),
                "check-ignore",
                "--quiet",
                "injected.dat",
            ],
            env=clean_git_environment(),
            check=True,
        )
        with mock.patch.dict(os.environ, clean_git_environment(), clear=True):
            self.assertNotIn("injected.dat", records.source_identity(review_workspace)["files"])
            self.assertEqual(original, records.source_identity(project))
            self.assertEqual(original_index, records.index_identity(project))

    def test_review_approval_cannot_replace_failed_fresh_check(self):
        project = self.project("failed-check")
        command = shlex.quote(sys.executable) + " -m unittest tests.test_guard.MissingTest -v"
        result, value = self.run_cli(project, self.root / "failed-output", contract(command))
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertNotEqual("PASS", value["verdict"])
        report = value["evidence_report"]["document"]
        self.assertEqual("PASS", report["regression_proof"]["verdict"])
        receipts = list((self.root / "failed-output/check-replay").glob("check-request-*/replay.json"))
        self.assertEqual(1, len(receipts))
        replay = json.loads(receipts[0].read_text())
        self.assertEqual("FAIL", replay["verdict"])
        self.assertEqual(report["revision"]["source_revision"], replay["source_revision"])
        self.assertEqual(command, replay["checks"][0]["command"])
        self.assertNotEqual(0, replay["checks"][0]["exit_code"])
        self.assertEqual(records.util.file_hash(replay["checks"][0]["output"]), replay["checks"][0]["output_sha256"])
        self.assertTrue(
            any(
                "Independent check replay refused:" in reason and str(receipts[0]) in reason
                for reason in value["reasons"]
            )
        )
        self.assertTrue(report["unverified"])

    def test_passing_checks_do_not_invent_human_acceptance(self):
        project = self.project("human")
        command = shlex.quote(sys.executable) + " -m unittest discover -s tests -v"
        result, value = self.run_cli(project, self.root / "human-output", contract(command, human=True))
        self.assertEqual(2, result.returncode, result.stdout + result.stderr)
        self.assertEqual("UNVERIFIED", value["verdict"])
        report = value["evidence_report"]["document"]
        self.assertEqual("PASS", report["regression_proof"]["verdict"])
        self.assertEqual(0, report["checks"][0]["exit_code"])
        self.assertFalse(report["criteria"][0]["human_reviewed"])


class VerificationBoundaryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.project = self.root / "project"
        self.project.mkdir()
        (self.project / "double.py").write_text("def double(number): return number\n")
        self.environment = clean_git_environment()
        for args in (
            ("init", "-q"),
            ("config", "maintenance.auto", "false"),
            ("config", "gc.auto", "0"),
            ("add", "."),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "base"),
        ):
            subprocess.run(
                ["git", "-C", str(self.project), *args], env=self.environment, capture_output=True, check=True
            )
        self.sha = subprocess.check_output(
            ["git", "-C", str(self.project), "rev-parse", "HEAD"], env=self.environment, text=True
        ).strip()
        (self.project / "double.py").write_text("def double(number): return number * 2\n")
        self.source = self.inventory()
        self.out = self.root / "output"
        self.marker = self.root / "provider-called"
        binary = self.root / "bin"
        binary.mkdir()
        for name in ("codex", "opencode"):
            sentinel = binary / name
            sentinel.write_text("#!/bin/sh\n: > " + shlex.quote(str(self.marker)) + "\nexit 99\n")
            sentinel.chmod(0o755)
        self.environment.update(
            PATH=str(binary) + os.pathsep + self.environment.get("PATH", "/usr/bin:/bin"),
            AUTOCODE_HOME=str(self.root / "registry"),
        )

    def inventory(self):
        return {
            str(path.relative_to(self.project)): path.read_bytes() for path in self.project.rglob("*") if path.is_file()
        }

    def new_arguments(self, out=None):
        return [
            "--workspace",
            str(self.project),
            "--base",
            self.sha,
            "--criteria",
            json.dumps(contract("python3 -m unittest discover -s tests -v")),
            "--out",
            str(self.out if out is None else out),
            "--json",
        ]

    def assert_untouched(self):
        self.assertEqual(self.source, self.inventory())
        self.assertFalse(self.out.exists())
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / "registry").exists())

    def test_review_launch_keeps_profile_routes_and_verification_bounds(self):
        for profile in (False, True):
            with self.subTest(profile=profile):
                extra = ["--related-profile", "profile.json", "--related-profile-sha256", "0" * 64] if profile else []
                args = verifier.parser().parse_args(self.new_arguments(self.root / str(profile)) + extra)
                with mock.patch.dict(os.environ, self.environment, clear=True):
                    with mock.patch.object(
                        verifier.TaskRun, "start", side_effect=verifier.TaskRunError("stop")
                    ) as launch:
                        result = verifier.verify(args, criteria_policy.load(args.criteria))
                self.assertEqual("UNVERIFIED", result["verdict"])
                launch.assert_called_once()
                options = launch.call_args.kwargs["options"]
                self.assertIn("--no-adaptive-planning", options)
                self.assertEqual("1", options[options.index("--max-iterations") + 1])
                if profile:
                    self.assertEqual("profile.json", options[options.index("--related-profile") + 1])
                    for flag in (
                        "--engine",
                        "--provider",
                        "--sol-model",
                        "--sol-reasoning-effort",
                        "--evidence-provenance",
                    ):
                        self.assertNotIn(flag, options)
                else:
                    for flag, expected in (
                        ("--engine", "opencode"),
                        ("--evidence-provenance", "unknown"),
                        ("--sol-reasoning-effort", "medium"),
                    ):
                        self.assertEqual(expected, options[options.index(flag) + 1])

    def test_git_redirects_and_config_overrides_refuse_new_and_saved_cli_without_writes(self):
        for name in (
            "GIT_DIR",
            "GIT_WORK_TREE",
            "GIT_INDEX_FILE",
            "GIT_OBJECT_DIRECTORY",
            "GIT_ALTERNATE_OBJECT_DIRECTORIES",
            "GIT_NAMESPACE",
            "GIT_CONFIG",
            "GIT_CONFIG_GLOBAL",
            "GIT_CONFIG_PARAMETERS",
        ):
            for arguments in (self.new_arguments(), ["--record", str(self.root / "unavailable-record"), "--json"]):
                with self.subTest(name=name, saved="--record" in arguments):
                    secret = "private-override-value-that-must-not-be-logged"
                    result = subprocess.run(
                        [sys.executable, "-B", str(ROOT / "tools/autocode.py"), "verify", *arguments],
                        env={**self.environment, name: secret},
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(4, result.returncode, result.stdout + result.stderr)
                    self.assertEqual("UNVERIFIED", json.loads(result.stderr)["verdict"])
                    self.assertIn(name, result.stderr)
                    self.assertIn("unset " + name, result.stderr)
                    self.assertNotIn(secret, result.stdout + result.stderr)
                    self.assert_untouched()

    def test_an_editor_terminals_git_config_parameters_do_not_stop_verification(self):
        editor = {"GIT_CONFIG_COUNT": "1", "GIT_CONFIG_KEY_0": "safe.directory", "GIT_CONFIG_VALUE_0": "x"}
        seen = {}

        def launch(*_args, **_kwargs):
            seen.update({name: os.environ.get(name) for name in editor})
            raise verifier.TaskRunError("stop")

        args = verifier.parser().parse_args(self.new_arguments())
        with mock.patch.dict(os.environ, {**self.environment, **editor}, clear=True):
            with mock.patch.object(verifier.TaskRun, "start", side_effect=launch) as started:
                result = verifier.verify(args, criteria_policy.load(args.criteria))
        self.assertEqual("UNVERIFIED", result["verdict"])
        started.assert_called_once()
        self.assertEqual(dict.fromkeys(editor), seen)

    def test_output_under_a_system_link_is_created_at_its_target(self):
        tmp = Path("/tmp")
        if not (tmp.is_symlink() and tmp.lstat().st_uid == 0):
            self.skipTest("/tmp is not a root-owned symbolic link on this host (macOS has one)")
        args = verifier.parser().parse_args(self.new_arguments(tmp / f"autocode-verify-{uuid.uuid4().hex}"))
        with mock.patch.dict(os.environ, self.environment, clear=True):
            with mock.patch.object(verifier.TaskRun, "start", side_effect=verifier.TaskRunError("stop")):
                result = verifier.verify(args, criteria_policy.load(args.criteria))
        created = Path(result["record"])
        self.addCleanup(shutil.rmtree, created, True)
        self.assertEqual(Path(os.path.realpath(tmp)) / args.out.name, created)
        self.assertTrue(created.is_dir())

    def test_git_visible_output_is_refused_before_creation_or_provider_dispatch(self):
        output = self.project / "verification-output"
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools/autocode.py"), "verify", *self.new_arguments(output)],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(4, result.returncode, result.stdout + result.stderr)
        self.assertIn("must be Git-ignored", json.loads(result.stderr)["error"])
        self.assertFalse(output.exists())
        self.assert_untouched()

    def redirect_workspace(self):
        foreign = self.root / "foreign-project"
        shutil.copytree(self.project, foreign)
        subprocess.run(
            ["git", "-C", str(self.project), "config", "core.worktree", str(foreign)],
            env=self.environment,
            capture_output=True,
            check=True,
        )
        self.source = self.inventory()
        return foreign

    def test_local_worktree_redirect_refuses_new_cli_without_writes_or_dispatch(self):
        foreign = self.redirect_workspace()
        foreign_before = {str(p.relative_to(foreign)): p.read_bytes() for p in foreign.rglob("*") if p.is_file()}
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools/autocode.py"), "verify", *self.new_arguments()],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=30,
        )
        self.assertEqual(4, result.returncode, result.stdout + result.stderr)
        self.assertIn("differs from the requested Git top-level", json.loads(result.stderr)["error"])
        self.assertEqual(
            foreign_before, {str(p.relative_to(foreign)): p.read_bytes() for p in foreign.rglob("*") if p.is_file()}
        )
        self.assert_untouched()

    def test_saved_record_declines_local_redirect_even_with_identical_copied_source(self):
        with mock.patch.dict(os.environ, self.environment, clear=True):
            original_snapshot = records.source_identity(self.project)
            original_index = records.index_identity(self.project)
        foreign = self.redirect_workspace()
        # The redirected tree deliberately has identical source/HEAD/index, so
        # content hashing alone cannot establish the requested original identity.
        with mock.patch.dict(os.environ, self.environment, clear=True):
            self.assertEqual(original_snapshot, records.source_snapshot.snapshot(self.project))
            self.assertEqual(original_index, records.index_identity(self.project))
            candidate_snapshot = records.source_identity(foreign)
        saved = self.root / "saved-record"
        saved.mkdir()
        receipt = {
            "contract": criteria_policy.load(json.dumps(contract("python3 -m unittest discover -s tests -v"))),
            "candidate": str(foreign),
            "candidate_snapshot": candidate_snapshot,
            "original": {"workspace": str(self.project), "snapshot": original_snapshot},
            "original_index": original_index,
            "pins": {},
            "verdict": "PASS",
            "anchor": {},
            "reasons": [],
        }
        receipt["binding"] = records.binding(receipt)
        (saved / "verification.json").write_text(json.dumps(receipt))
        canonical = {"document": {"kind": "verification"}, "availability": "recorded"}
        with (
            mock.patch.dict(os.environ, self.environment, clear=True),
            mock.patch.object(records.export, "read", return_value=canonical) as canonical_read,
        ):
            result = records.read(saved)
        self.assertEqual("UNVERIFIED", result["verdict"])
        self.assertEqual("stale", result["availability"])
        self.assertFalse(canonical_read.call_args.kwargs["current"])
        self.assertIn("Verification source or evidence is unavailable", result["reasons"])
        self.assert_untouched()

    def test_tracked_runtime_input_is_refused_before_task_dispatch(self):
        source = self.project / ".autocode" / "source-owned.txt"
        source.parent.mkdir()
        source.write_text("selected source is not a task output directory")
        for args in (
            ("add", "-f", ".autocode/source-owned.txt"),
            (
                "-c",
                "user.name=Fixture",
                "-c",
                "user.email=fixture@example.invalid",
                "commit",
                "-qm",
                "runtime-path fixture",
            ),
        ):
            subprocess.run(
                ["git", "-C", str(self.project), *args], env=self.environment, capture_output=True, check=True
            )
        before = self.inventory()
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools/autocode.py"), "verify", *self.new_arguments()],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(4, result.returncode, result.stdout + result.stderr)
        self.assertIn("reserved .autocode/", json.loads(result.stderr)["error"])
        self.assertEqual(before, self.inventory())
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / "registry").exists())

    def test_snapshot_omitted_inputs_are_refused_before_task_dispatch(self):
        for index, name in enumerate(
            (
                ".autocode-ui/source.txt",
                ".AUTOCODE/source.txt",
                "Review/source.txt",
                "compiled.pyc",
                "nested/__PYCACHE__/payload",
            )
        ):
            with self.subTest(name=name):
                source = self.project / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_text("selected source must not disappear from custody")
                for args in (
                    ("add", "-f", name),
                    (
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.invalid",
                        "commit",
                        "-qm",
                        "omitted input",
                    ),
                ):
                    subprocess.run(
                        ["git", "-C", str(self.project), *args],
                        env=self.environment,
                        capture_output=True,
                        check=True,
                    )
                before = self.inventory()
                result = subprocess.run(
                    [
                        sys.executable,
                        "-B",
                        str(ROOT / "tools/autocode.py"),
                        "verify",
                        *self.new_arguments(self.root / f"omitted-output-{index}"),
                    ],
                    env=self.environment,
                    capture_output=True,
                    text=True,
                    timeout=60,
                )
                self.assertEqual(4, result.returncode, result.stdout + result.stderr)
                self.assertEqual("UNVERIFIED", json.loads(result.stderr)["verdict"])
                self.assertIn("selected change", json.loads(result.stderr)["error"])
                self.assertEqual(before, self.inventory())
                self.assertFalse(self.marker.exists())
                self.assertFalse((self.root / "registry").exists())
                subprocess.run(
                    ["git", "-C", str(self.project), "rm", "-f", "--", name],
                    env=self.environment,
                    capture_output=True,
                    check=True,
                )
                subprocess.run(
                    [
                        "git",
                        "-c",
                        "user.name=Fixture",
                        "-c",
                        "user.email=fixture@example.invalid",
                        "-C",
                        str(self.project),
                        "commit",
                        "-qm",
                        "remove omitted input",
                    ],
                    env=self.environment,
                    capture_output=True,
                    check=True,
                )

    def test_source_controlled_cache_path_is_refused_before_task_dispatch_without_chmod(self):
        cache = self.project / "__pycache__"
        cache.write_text("source-controlled cache path must not be adopted")
        cache.chmod(0o644)
        for args in (
            ("add", "__pycache__"),
            ("-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid", "commit", "-qm", "cache fixture"),
        ):
            subprocess.run(
                ["git", "-C", str(self.project), *args], env=self.environment, capture_output=True, check=True
            )
        before = self.inventory()
        mode = cache.stat().st_mode
        result = subprocess.run(
            [sys.executable, "-B", str(ROOT / "tools/autocode.py"), "verify", *self.new_arguments()],
            env=self.environment,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual(4, result.returncode, result.stdout + result.stderr)
        self.assertIn("cache input omitted by source snapshots", json.loads(result.stderr)["error"])
        self.assertEqual(before, self.inventory())
        self.assertEqual(mode, cache.stat().st_mode)
        self.assertFalse(self.marker.exists())
        self.assertFalse((self.root / "registry").exists())

    def test_missing_review_dependency_refuses_public_cli_before_output_or_task_dispatch(self):
        original_import = __import__

        def unavailable(name, *args, **kwargs):
            if name in {"autocode_pr_checkout", "autocode_pr_review"}:
                raise ImportError("Unavailable authenticated review dependency")
            return original_import(name, *args, **kwargs)

        stderr = StringIO()
        with (
            mock.patch.dict(os.environ, self.environment, clear=True),
            mock.patch("builtins.__import__", side_effect=unavailable),
            mock.patch.object(verifier.TaskRun, "start", side_effect=AssertionError("Must not dispatch")) as dispatch,
            redirect_stderr(stderr),
        ):
            code = verifier.cli(self.new_arguments())
        self.assertEqual(4, code)
        self.assertIn(
            "Authenticated review artifact and checkout support is unavailable", json.loads(stderr.getvalue())["error"]
        )
        dispatch.assert_not_called()
        self.assert_untouched()
