"""Accepted component designs: validation, stable identity, and real CLI builds.

Only provider answers are scripted; no Figma access or live model calls.
"""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_multicomponent as mc
import autocode_taskrun as taskrun
from . import test_multicomponent as fixture

URL = "https://www.figma.com/design/Alpha123/Alpha?node-id=1-2"
BRIEF = "Accepted alpha layout: blue navigation and a compact message card."


def accepted_ui_run(directory, *, brief=BRIEF):
    """A reviewed version-two UI handoff, including the accepted artifact hashes."""
    directory.mkdir(parents=True, exist_ok=True)
    reports = {
        "requirements_draft": "Alpha navigation and message-card layout.\n",
        "plan_reviewer": {"status": "PASS", "evidence": ["Reviewed alpha layout"]},
        "brief": brief,
        "plan_finalizer": {"status": "ACCEPT", "evidence": ["Accepted alpha requirements"],
                           "required_changes": []},
        "builder": {"status": "COMPLETE", "figma_file": URL, "evidence": ["Node 1:2"]},
        "validator": {"status": "PASS", "figma_file": URL, "evidence": ["Compared Node 1:2"]},
        "decision_owner": {"status": "ACCEPT", "figma_file": URL, "evidence": ["Accepted Node 1:2"]},
    }
    refs = {}
    for role, value in reports.items():
        path = directory / (role + (".md" if isinstance(value, str) else ".json"))
        path.write_text(value if isinstance(value, str) else json.dumps(value))
        refs[role] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    (directory / "state.json").write_text(json.dumps({"status": "COMPLETE"}))
    (directory / "handoff.json").write_text(json.dumps(
        {"version": 2, "task": "Design alpha", "figma_file": URL, "artifacts": refs}))
    return directory


def replace_accepted_brief(directory, text):
    """A newly accepted handoff can be valid while differing from the build's pin."""
    document = json.loads((directory / "handoff.json").read_text())
    ref = document["artifacts"]["brief"]
    path = directory / ref["path"]
    path.write_text(text)
    ref["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    (directory / "handoff.json").write_text(json.dumps(document))


class ComponentDesignTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="component-design-")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.directory = self.root / "architecture"
        self.directory.mkdir()
        self.ui_run = accepted_ui_run(self.directory / "accepted-alpha")

    def load(self, **design):
        (self.directory / "components.json").write_text(json.dumps(
            [{**fixture.component("alpha"), **design}]))
        return mc.Architecture.load(self.directory)

    def test_direct_reference_and_null_fields(self):
        component = self.load(figma_file=URL, ui_run=None).components["alpha"]
        self.assertEqual(("--figma-file", URL), component.design.start_options())
        self.assertIsNone(self.load(figma_file=None, ui_run=None).components["alpha"].design)

    def test_relative_accepted_handoff_resolves_against_architecture(self):
        design = self.load(ui_run="accepted-alpha").components["alpha"].design
        self.assertEqual(("--ui-run", str(self.ui_run.resolve())), design.start_options())
        self.assertTrue(design.fingerprint())

    def test_invalid_inputs_are_refused_at_architecture_load(self):
        for row in ({"figma_file": "https://example.com/design/Alpha123"},
                    {"figma_file": "https://www.figma.com/file/Alpha123/Alpha"},
                    {"figma_file": URL, "ui_run": "accepted-alpha"},
                    {"figma_file": ""}, {"figma_file": " "}, {"figma_file": 1},
                    {"ui_run": ""}, {"ui_run": " "}, {"ui_run": False}):
            with self.subTest(row=row), self.assertRaises(mc.ArchitectureError):
                self.load(**row)

    def test_incomplete_and_tampered_handoffs_are_refused(self):
        (self.ui_run / "state.json").write_text(json.dumps({"status": "BUILDING"}))
        with self.assertRaisesRegex(mc.ArchitectureError, "completed, accepted"):
            self.load(ui_run="accepted-alpha")
        (self.ui_run / "state.json").write_text(json.dumps({"status": "COMPLETE"}))
        (self.ui_run / "brief.md").write_text("An unreviewed design change")
        with self.assertRaisesRegex(mc.ArchitectureError, "changed after acceptance"):
            self.load(ui_run="accepted-alpha")

    def test_identity_ignores_handoff_json_formatting_but_pins_accepted_artifacts(self):
        architecture = self.load(ui_run="accepted-alpha")
        before = architecture.fingerprint()
        path = self.ui_run / "handoff.json"
        path.write_text(json.dumps(json.loads(path.read_text()), indent=4, sort_keys=True))
        self.assertEqual(before, architecture.fingerprint())
        replace_accepted_brief(self.ui_run, BRIEF + " Accepted revised spacing.")
        self.assertNotEqual(before, architecture.fingerprint())

    def test_design_changed_after_build_construction_is_refused_before_launch(self):
        architecture = self.load(ui_run="accepted-alpha")
        repo = self.root / "repo"
        repo.mkdir()
        fixture.git(repo, "init", "-q")
        fixture.git(repo, "commit", "--allow-empty", "-q", "-m", "base")
        build = mc.MultiComponentBuild(repo, architecture)
        replace_accepted_brief(self.ui_run, "Accepted replacement layout.")
        with self.assertRaisesRegex(mc.ArchitectureError, "changed"):
            build.build()
        self.assertFalse((repo / ".autocode-components").exists())

    def test_in_memory_architecture_still_pins_accepted_design(self):
        loaded = self.load(ui_run="accepted-alpha")
        architecture = mc.Architecture(components=loaded.components)
        original = architecture.fingerprint()
        self.assertIsNotNone(original)
        replace_accepted_brief(self.ui_run, "Accepted replacement layout.")
        self.assertNotEqual(original, architecture.fingerprint())

    def test_design_cannot_silently_override_a_different_engine(self):
        architecture = self.load(ui_run="accepted-alpha")
        for options in (("--engine", "opencode"), ("--engine=gocode",),
                        ("--engine", "codex", "--engine", "opencode")):
            with self.subTest(options=options), self.assertRaisesRegex(mc.ArchitectureError, "engine codex"):
                mc.MultiComponentBuild(self.root, architecture, options=options)
        self.assertFalse((self.root / ".autocode-components").exists())


class ComponentDesignCliTests(unittest.TestCase):
    """Composition reuses fixture setup without inheriting its unrelated tests."""
    write_manifest = fixture.BuildAndIntegrateTests.write_manifest
    run_cli = fixture.CliTests.run_cli
    component_branches = fixture.CliTests.component_branches

    def setUp(self):
        fixture.BuildAndIntegrateTests.setUp(self)
        self.env["CODEX_HOME"] = str(self.root / "codex-config")
        self.ui_run = accepted_ui_run(self.root / "accepted-alpha")
        self.observations = self.root / "observations"
        self.directory = self.repo / "architecture"
        self.directory.mkdir()
        (self.directory / "contracts").mkdir()
        (self.directory / "components.json").write_text(json.dumps(
            [{**fixture.component("alpha"), "ui_run": os.path.relpath(self.ui_run, self.directory)},
             fixture.component("beta")]))
        fixture.git(self.repo, "add", "-A")
        fixture.git(self.repo, "commit", "-q", "-m", "architecture")
        self.write_manifest()
        manifest = json.loads(self.manifest.read_text())
        for spec in manifest.values():
            spec["observations"] = str(self.observations)
        self.manifest.write_text(json.dumps(manifest))

    def cli(self, *args):
        return self.run_cli("architecture", "--workspace", str(self.repo),
                            "--options", " ".join(fixture.FIXTURE_OPTIONS), *args)

    def test_accepted_design_is_scoped_and_survives_cli_resume_and_integration(self):
        first = self.cli()
        self.assertEqual(2, first.returncode, first.stdout + first.stderr)
        stopped = json.loads(first.stdout)
        for cid in ("alpha", "beta"):
            self.assertEqual("approve_plan", stopped["components"][cid]["view"]["needs"]["kind"])
        branches = self.component_branches()

        second = self.cli("--auto-approve", "--integrate", "integration")
        self.assertEqual(0, second.returncode, second.stdout + second.stderr)
        completed = json.loads(second.stdout)
        for cid in ("alpha", "beta"):
            info = completed["components"][cid]
            self.assertEqual("done", info["status"])
            self.assertEqual("TASK_COMPLETE", info["view"]["status"])
            self.assertTrue(info["view"]["done"])
            self.assertTrue(info["resumed"])
            self.assertEqual(stopped["components"][cid]["run_dir"], info["run_dir"])
        self.assertEqual(branches, self.component_branches())
        self.assertEqual(["alpha", "beta"], completed["integration"]["integrated"])
        self.assertIsNone(completed['components']['alpha']['view']['efficiency']['visual']['accepted_frames'])
        self.assertIsNone(completed['components']['alpha']['view']['efficiency']['visual']['accepted_states'])
        for cid in ("alpha", "beta"):
            path = self.repo / "integration" / "components" / cid / "message.txt"
            self.assertEqual(f"from {cid}\n", path.read_text())
        self.assertFalse((self.repo / "components").exists())
        observations = [json.loads(path.read_text()) for path in self.observations.glob("*.json")]
        alpha = [item for item in observations if item["component_id"] == "alpha"]
        self.assertTrue({"requirements_gather", "astra_discovery", "terra", "sol"}
                        <= {item["stage"] for item in alpha})
        for item in observations:
            if item["component_id"] == "alpha":
                self.assertIn(URL, item["prompt"], item["stage"])
                self.assertIn(BRIEF, item["prompt"], item["stage"])
            else:
                self.assertNotIn(URL, item["prompt"], item["stage"])
                self.assertNotIn(BRIEF, item["prompt"], item["stage"])

    def test_new_accepted_design_cannot_resume_runs_for_previous_design(self):
        first = self.cli()
        self.assertEqual(2, first.returncode, first.stdout + first.stderr)
        branches = self.component_branches()
        prompts = set(self.observations.glob("*.json"))
        replace_accepted_brief(self.ui_run, "Accepted alpha replacement design.")
        second = self.cli("--auto-approve")
        self.assertNotEqual(0, second.returncode)
        self.assertIn("architecture changed", second.stderr)
        self.assertEqual(prompts, set(self.observations.glob("*.json")))
        self.assertEqual(branches, self.component_branches())

    def test_design_changed_by_external_acceptance_during_build_is_not_blessed(self):
        original = mc.Architecture.load(self.directory).fingerprint()
        manifest = json.loads(self.manifest.read_text())
        manifest["alpha"]["reaccept_ui_run"] = str(self.ui_run)
        self.manifest.write_text(json.dumps(manifest))
        result = self.cli("--auto-approve")
        self.assertNotEqual(0, result.returncode)
        self.assertIn("changed during this build", result.stderr)
        saved = json.loads((self.repo / ".autocode-components" / "manifest.json").read_text())
        self.assertEqual(original, saved["architecture_fingerprint"])
        self.assertNotEqual(original, mc.Architecture.load(self.directory).fingerprint())
        prompts = set(self.observations.glob("*.json"))
        again = self.cli("--auto-approve")
        self.assertNotEqual(0, again.returncode)
        self.assertIn("architecture changed", again.stderr)
        self.assertEqual(prompts, set(self.observations.glob("*.json")))


class TaskRunStartOptionsTests(unittest.TestCase):
    def test_component_design_options_are_sent_only_when_starting(self):
        with tempfile.TemporaryDirectory(prefix="taskrun-options-") as temp:
            root = Path(temp)
            command = root / "client.py"
            log = root / "invocations.jsonl"
            command.write_text("""import json, sys
from pathlib import Path
args = sys.argv[1:]
workspace = Path(args[args.index('--workspace') + 1])
with Path(sys.argv[0]).with_name('invocations.jsonl').open('a') as out:
    out.write(json.dumps(args) + '\\n')
if '--status' in args:
    print(json.dumps({'view': {'status': 'TASK_COMPLETE', 'done': True, 'needs': None}}))
elif '--run-dir' not in args:
    run = workspace / '.autocode' / 'runs' / 'fixture'
    run.mkdir(parents=True)
    (run / 'state.json').write_text('{}')
""")
            options = ("--engine", "codex")
            run = taskrun.TaskRun.start(root, "Implement alpha", options=options,
                start_options=("--ui-run", "accepted-alpha"), command=(sys.executable, str(command)))
            self.assertEqual(options, run.options)
            self.assertTrue(run.advance()["done"])
            calls = [json.loads(line) for line in log.read_text().splitlines()]
            self.assertIn("--ui-run", calls[0])
            self.assertEqual("accepted-alpha", calls[0][calls[0].index("--ui-run") + 1])
            self.assertFalse(any("--ui-run" in args for args in calls[1:]))


if __name__ == "__main__":
    unittest.main()
