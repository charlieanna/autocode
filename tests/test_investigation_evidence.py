"""Bound investigation handoff and negative controls; no model calls."""

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_bug_job as bug_job
import autocode_investigation_evidence as evidence
import autocode_util as util
from units import autoplanner, autoresolver

from .test_bug_job import diagnosis, state_for


class InvestigationEvidenceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.workspace = Path(temporary.name).resolve() / "project"
        self.workspace.mkdir()
        (self.workspace / "calc.py").write_text("value = 2\n")
        (self.workspace / "test_calc.py").write_text("assert True\n")
        (self.workspace / "settings.json").write_text('{"factor":2}\n')
        (self.workspace / ".gitignore").write_text(".autocode/\n.venv/\n__pycache__/\n")
        subprocess.run(["git", "init", "-q", str(self.workspace)], check=True)
        subprocess.run(["git", "-C", str(self.workspace), "add", "-A"], check=True)
        subprocess.run(
            [
                "git",
                "-C",
                str(self.workspace),
                "-c",
                "user.name=t",
                "-c",
                "user.email=t@example.test",
                "commit",
                "-qm",
                "seed",
            ],
            check=True,
        )
        self.run_dir = self.workspace / ".autocode/runs/current"
        self.attempt = self.run_dir / "iterations/001"
        self.attempt.mkdir(parents=True)
        self.output = self.attempt / "bug-investigation-01.json"
        self.before = self.attempt / "bug-investigation-01.before.json"
        self.report = diagnosis(
            fix_size="large",
            note_path="docs/bugs/calc.json",
            affected_paths=["calc.py"],
            test_paths=["test_calc.py"],
            probe="python3 -c 'import calc; assert calc.value == 2'",
            untestable="",
            counterexample={"file": "calc.py", "find": "2", "replace": "3"},
        )
        self.output.write_text(json.dumps(self.report))
        snapshot = util.snapshot(self.workspace)
        self.before.write_text(json.dumps(snapshot))
        self.record = {
            "stage": "investigate_bug",
            "iteration": 1,
            "output": str(self.output),
            "before_ref": str(self.before),
            "source_revision": snapshot["revision"],
            "changed_files": [],
        }
        self.state = state_for(str(self.workspace))
        self.runtime = mock.patch.object(evidence, "_runtime", return_value="bound-runtime")
        self.runtime_reader = self.runtime.start()
        self.addCleanup(self.runtime.stop)

    def accept(self):
        captured = evidence.begin(self.state, self.report, self.record, self.workspace, self.run_dir)
        bug_job.apply(
            self.state,
            self.report,
            self.record,
            self.workspace,
            run_probe=lambda command: {"exit_code": 0, "tail": "value=2"},
            run_counterexample=lambda command, edit: {"exit_code": 1, "tail": "assertion failed"},
        )
        evidence.finish(self.state, self.report, self.record, self.workspace, captured)
        self.state["stages"].append(self.record)
        return evidence.context(self.state, self.run_dir)

    def test_current_diagnosis_delivers_original_identity_hashes_and_runner_receipt(self):
        result = self.accept()
        self.assertEqual("reusable_planning_context", result["eligibility"], result)
        self.assertEqual(str(self.run_dir), result["origin"]["run_dir"])
        self.assertEqual("investigate_bug", result["origin"]["stage"])
        self.assertEqual(1, result["origin"]["iteration"])
        self.assertEqual(util.file_hash(self.output), result["origin"]["output_hash"])
        self.assertEqual(util.file_hash(self.workspace / "calc.py"), result["cited_sources"]["calc.py"])
        self.assertEqual(0, result["reproduction_receipt"]["exit_code"])
        self.assertEqual(1, result["reproduction_receipt"]["counterexample"]["exit_code"])
        self.assertIn("test_calc.py", result["cited_sources"])
        for stage in ("astra_discovery", "requirements_gather", "plan", "requirements"):
            with self.subTest(stage=stage):
                # v2 artifacts are unrelated to this packet test; no saved v2 predecessor exists.
                with mock.patch.object(autoplanner.artifacts, "verify_predecessor", return_value=None):
                    text, _ = autoplanner.context(self.state, stage, self.run_dir / "state.json")
                packet = json.loads(text.split("CURRENT HANDOFF DATA\n", 1)[1])
                self.assertEqual(result, packet["bug_diagnosis_evidence"])
                self.assertEqual(self.report["root_cause"], packet["bug_diagnosis"]["root_cause"])
                self.assertIn("never changes a protected criterion", text)
        self.assertIn("instead of re-reading those same files", autoplanner.PROMPTS["astra_discovery"])
        self.assertFalse(self.state.get("goal_contract"))
        self.assertEqual("astra_discovery", self.state["next_stage"])

    def assert_fresh(self, reason):
        result = evidence.context(self.state, self.run_dir)
        self.assertEqual("fresh_discovery_required", result["eligibility"], result)
        self.assertIn(reason, result["reason"])
        self.assertNotIn("reproduction_receipt", result)

    def test_source_test_config_creation_and_deletion_each_invalidate(self):
        self.accept()
        for name in ("calc.py", "test_calc.py", "settings.json", "new-config.toml"):
            with self.subTest(name=name):
                path = self.workspace / name
                original = path.read_bytes() if path.exists() else None
                path.write_text("changed\n")
                self.assert_fresh("source, tests or configuration")
                if original is None:
                    path.unlink()
                else:
                    path.write_bytes(original)
                self.assertEqual("reusable_planning_context", evidence.context(self.state, self.run_dir)["eligibility"])
        (self.workspace / "test_calc.py").unlink()
        self.assert_fresh("source, tests or configuration")

    def test_all_authority_and_evidence_pins_invalidate_without_rewriting_history(self):
        self.accept()
        original = copy.deepcopy(self.state)
        changes = (
            ("answers", {"Q1": {"text": "New requirement"}}, "requirements"),
            ("requirements_handoff", {"revision": 2}, "requirements"),
            ("turns", [{"stage_index": 1}], "legacy or unbound"),
        )
        for key, value, reason in changes:
            with self.subTest(key=key):
                self.state = copy.deepcopy(original)
                self.state[key] = value
                self.assert_fresh(reason)
        for key, value in (
            ("root_cause", "changed cause"),
            ("probe", "python3 -c 'pass'"),
            ("output_hash", "different"),
            ("probe_result", {"exit_code": 0}),
        ):
            with self.subTest(key=key):
                self.state = copy.deepcopy(original)
                self.state["investigation"][key] = value
                self.assert_fresh("changed")
        self.state = copy.deepcopy(original)
        self.runtime_reader.return_value = "changed-environment-or-dependency"
        self.assert_fresh("runtime/dependencies/environment changed")
        self.assertEqual(original, self.state)

    def test_legacy_foreign_and_tampered_artifacts_are_leads_only(self):
        self.accept()
        retained = copy.deepcopy(self.state)
        self.state["investigation"].pop("discovery_evidence")
        self.assert_fresh("legacy")
        self.state = retained
        result = evidence.context(self.state, self.run_dir.parent / "foreign")
        self.assertEqual("fresh_discovery_required", result["eligibility"])
        for path in (self.output, self.before):
            with self.subTest(path=path.name):
                original = path.read_bytes()
                path.write_text("{}")
                self.assert_fresh("artifact changed")
                path.write_bytes(original)
        self.output.unlink()
        self.output.symlink_to(self.workspace / "settings.json")
        self.assert_fresh("artifact changed")

    def test_unbound_runtime_or_no_reproduction_does_not_grant_reuse(self):
        self.runtime_reader.side_effect = ValueError("incomplete runtime")
        self.accept()
        self.assert_fresh("incomplete runtime")
        self.runtime_reader.side_effect = None
        self.report.update(probe="", untestable="needs a remote service")
        self.output.write_text(json.dumps(self.report))
        self.accept()
        self.assert_fresh("no runner-executable reproduction")

    def test_mutation_during_probe_or_runtime_change_cannot_seal_receipt(self):
        captured = evidence.begin(self.state, self.report, self.record, self.workspace, self.run_dir)
        bug_job.apply(
            self.state,
            self.report,
            self.record,
            self.workspace,
            run_probe=lambda command: {"exit_code": 0},
            run_counterexample=lambda command, edit: {"exit_code": 1},
        )
        self.runtime_reader.return_value = "different-runtime"
        evidence.finish(self.state, self.report, self.record, self.workspace, captured)
        self.assert_fresh("runtime changed")
        self.runtime_reader.return_value = "bound-runtime"
        (self.workspace / "settings.json").write_text("modified during execution")
        evidence.finish(self.state, self.report, self.record, self.workspace, captured)
        self.assert_fresh("source changed")

    def test_runtime_keeps_ignored_inputs_and_complete_dependency_binding(self):
        # Check the adapter, not a synthetic equality check: all runtime dimensions
        # except the separately checked source revision/metadata must remain bound.
        self.runtime.stop()
        with (
            mock.patch.object(evidence.sys, "platform", "win32"),
            self.assertRaisesRegex(ValueError, "unavailable on this platform"),
        ):
            evidence._runtime(self.state, self.workspace, self.run_dir, self.report["probe"])
        pins = {
            "cache_binding_complete": True,
            "reuse_supported": True,
            "source_revision": "s",
            "source_metadata": "m",
            "dependencies": ["dep1"],
            "environment_hash": "env",
            "generated_sources": {"ignored.py": "hash"},
            "unbound_source_symlinks": [],
        }
        with mock.patch.object(evidence.launch_inputs, "runners", return_value=(None, lambda *a, **k: pins)):
            current = evidence._runtime(self.state, self.workspace, self.run_dir, self.report["probe"])
            with self.assertRaisesRegex(ValueError, "source changed during runtime identity collection"):
                evidence._runtime(
                    self.state,
                    self.workspace,
                    self.run_dir,
                    self.report["probe"],
                    source_revision="changed-during-hash",
                )
            for key, value in (
                ("dependencies", ["dep2"]),
                ("environment_hash", "env2"),
                ("generated_sources", {"ignored.py": "changed"}),
            ):
                with self.subTest(key=key):
                    original = pins[key]
                    pins[key] = value
                    self.assertNotEqual(
                        current, evidence._runtime(self.state, self.workspace, self.run_dir, self.report["probe"])
                    )
                    pins[key] = original
            pins["cache_binding_complete"] = False
            with self.assertRaisesRegex(ValueError, "not completely bound"):
                evidence._runtime(self.state, self.workspace, self.run_dir, self.report["probe"])

    def test_owning_unit_brackets_real_probe_application_and_publishes_handoff(self):
        def clean(workspace, directory, *, command, timeout):
            return {"exit_code": 0, "tail": "observed"}

        with (
            mock.patch.object(autoresolver.launch_inputs, "runners", return_value=(clean, None)),
            mock.patch.object(autoresolver.counterexample, "runner", return_value=lambda *args: {"exit_code": 1}),
            mock.patch.object(autoresolver.compact_planning, "start"),
        ):
            autoresolver.apply_job(
                "investigate_bug", self.state, self.report, self.record, self.workspace, run_dir=self.run_dir
            )
        self.state["stages"].append(self.record)
        self.assertEqual("reusable_planning_context", evidence.context(self.state, self.run_dir)["eligibility"])

    def test_runtime_identity_never_executes_or_reinterprets_shell_programs(self):
        self.assertEqual(
            "python3 -c pass",
            evidence._identity_command(self.workspace, "python3 -B -c 'import calc; assert calc.value == 2'"),
        )
        for command in (
            "python3 -c pass; curl example.test",
            "python3 -c pass && true",
            "python3 -c 'print(\"$(env)\")'",
            "env X=1 python3 -c pass",
            "node -e pass",
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                evidence._identity_command(self.workspace, command)

    def test_live_redundant_environment_prefix_preserves_complete_runtime_identity(self):
        command = "PYTHONDONTWRITEBYTECODE=1 python3 -c 'import calc; assert calc.value == 2'"
        self.assertEqual("python3 -c pass", evidence._identity_command(self.workspace, command))
        self.assertEqual(
            "python3 -c pass",
            evidence._identity_command(
                self.workspace, "CI=1 PYTHONDONTWRITEBYTECODE=1 python3 -c 'print(1); print(2)'"
            ),
        )

    def test_changed_missing_or_tree_dependent_environment_prefix_requires_fresh_discovery(self):
        environment = evidence.verify.test_environment(self.workspace)
        for prefix in ("PYTHONDONTWRITEBYTECODE=0", "AUTOCODE_MISSING_2194=somevalue", "PATH=" + environment["PATH"]):
            with self.subTest(name=prefix.split("=", 1)[0]), self.assertRaises(ValueError):
                evidence._identity_command(self.workspace, prefix + " python3 -c pass")

    def test_shell_tilde_expansion_does_not_count_as_a_redundant_literal(self):
        with mock.patch.dict("os.environ", {"AUTOCODE_2194_TEST": "~/unexpanded"}):
            with self.assertRaisesRegex(ValueError, "not provably redundant"):
                evidence._identity_command(self.workspace, "AUTOCODE_2194_TEST=~/unexpanded python3 -c pass")

    def test_prefix_environment_is_rechecked_during_runtime_identity_collection(self):
        self.runtime.stop()
        initial = evidence.verify.test_environment(self.workspace)
        changed = {**initial, "AUTOCODE_2194_CHANGED": "yes"}
        pins = {
            "cache_binding_complete": True,
            "reuse_supported": True,
            "environment_hash": util.digest(initial),
            "source_revision": "s",
            "unbound_source_symlinks": [],
        }
        with (
            mock.patch.object(evidence.verify, "test_environment", side_effect=[initial, changed]),
            mock.patch.object(evidence.launch_inputs, "runners", return_value=(None, lambda *a, **k: pins)),
        ):
            with self.assertRaisesRegex(ValueError, "environment changed during identity collection"):
                evidence._runtime(self.state, self.workspace, self.run_dir, "PYTHONDONTWRITEBYTECODE=1 python3 -c pass")

    def test_adjacent_shell_controls_are_never_direct_python_identity(self):
        for suffix in (";printf", "&&true", "|cat", ">out", "<in", "&true", "\ntrue"):
            with self.subTest(suffix=suffix), self.assertRaises(ValueError):
                evidence._identity_command(self.workspace, "python3 -c pass" + suffix)
        self.assertEqual(
            "python3 -c pass", evidence._identity_command(self.workspace, "python3 -c 'print(1); print(2)'")
        )

    def test_changed_original_stage_identity_never_reuses_a_receipt(self):
        self.accept()
        retained = copy.deepcopy(self.state)
        for key, value in (("iteration", 9), ("source_revision", "foreign"), ("rejected", True), ("stage", "sol")):
            with self.subTest(key=key):
                self.state = copy.deepcopy(retained)
                self.state["stages"][0][key] = value
                self.assert_fresh("identity is missing or changed")

    @unittest.skipUnless(sys.platform.startswith("linux"), "complete runtime identity is qualified on Linux")
    def test_real_ignored_dependency_bytes_and_environment_invalidate(self):
        self.runtime.stop()
        subprocess.run([sys.executable, "-m", "venv", "--without-pip", str(self.workspace / ".venv")], check=True)
        first = evidence._runtime(self.state, self.workspace, self.run_dir, self.report["probe"])
        sites = list((self.workspace / ".venv/lib").glob("python*/site-packages"))
        self.assertEqual(1, len(sites))
        dependency = sites[0] / "local_dependency.py"
        dependency.write_text("setting = 1\n")
        second = evidence._runtime(self.state, self.workspace, self.run_dir, self.report["probe"])
        self.assertNotEqual(first, second, "ignored installed dependency bytes must be bound")
        with mock.patch.dict("os.environ", {"AUTOCODE_LOCAL_REPRO_INPUT": "changed"}):
            self.assertNotEqual(
                second, evidence._runtime(self.state, self.workspace, self.run_dir, self.report["probe"])
            )
