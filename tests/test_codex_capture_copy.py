"""TaskRun flow with real captured checks; only Codex model answers are scripted."""

import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_provider_launch  # noqa: F401 - changed-suite dependency
from autocode_taskrun import TaskRun, TaskRunError

HERE = Path(__file__).resolve().parents[1] / "tools"
BRIEF = (
    "Build a deterministic greeting CLI named greet.py. It prints 'Hello, NAME' for one nonempty name "
    "argument and exits 0. Any other argument count (no arguments, or two or more) prints a usage line to "
    "stderr and exits 2. Deliver greet.py, test_greet.py with regression tests, and a short README.md. "
    "Python standard library only."
)
OPTIONS = (
    "--engine",
    "codex",
    "--joint-planning",
    "--astra-model",
    "gpt-6-astra",
    "--terra-model",
    "gpt-5.6-terra",
    "--sol-model",
    "gpt-5.6-sol",
    "--completion-model",
    "gpt-6-astra",
    "--glm-model",
    "gpt-5.6-sol",
    "--plan-reviewer-model",
    "gpt-6-astra",
    "--max-idle-seconds",
    "0",
    "--max-tool-seconds",
    "0",
    "--max-stage-seconds",
    "45",
    "--max-seconds",
    "180",
)


class CodexCaptureWorkflowTests(unittest.TestCase):
    def setUp(self):
        if artifacts := os.environ.get("BUILD_AUDIT_ARTIFACTS"):
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=artifacts)).resolve()
        else:
            temporary = tempfile.TemporaryDirectory()
            self.addCleanup(temporary.cleanup)
            self.root = Path(temporary.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        for args in (
            ("init", "-q"),
            ("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "--allow-empty", "-qm", "base"),
        ):
            subprocess.run(["/usr/bin/git", *args], cwd=self.workspace, check=True, capture_output=True)
        bindir = self.root / "bin"
        bindir.mkdir()
        self.receipt = self.workspace / ".autocode/evidence/planned-check.json"
        self.probe = self.root / "provider-launches.jsonl"
        check = (
            "from pathlib import Path; import subprocess,sys; "
            "result=subprocess.run([sys.executable,'-m','unittest','test_greet.py']); "
            "assert result.returncode == 0; Path('other_1.sqlite3').touch()"
        )
        self.command = shlex.join([sys.executable, "-B", "-c", check])
        script = (HERE / "live_fixture_provider.py").read_text()
        script = script.replace("import json\n", "import json\nimport shlex\n", 1)
        script = script.replace(
            "def _contract() -> dict:", "PLANNED_CHECK = " + repr(self.command) + "\n\ndef _contract() -> dict:", 1
        )
        script = script.replace('"Execute greeting and invalid-input regression checks"', "PLANNED_CHECK")
        anchor = '    repairing = bool(data.get("report_repair"))'
        self.assertIn(anchor, script)
        script = script.replace(
            anchor,
            """    with Path(os.environ['AUTOCODE_FIXTURE_PROBE']).open('a') as probe:
        probe.write(json.dumps({'stage': stage, 'copy': os.environ.get('AUTOCODE_VERIFICATION_COPY'),
                                'copy_sha256': os.environ.get('AUTOCODE_VERIFICATION_COPY_SHA256')}) + '\\n')
"""
            + anchor,
            1,
        )
        begin = script.index('    elif stage in ("sol", "astra_checkpoint"):')
        end = script.index('    elif stage in ("astra_review", "astra_plan", "astra_resolve"):', begin)
        script = (
            script[:begin]
            + """    elif stage in ("sol", "astra_checkpoint"):
        receipt_path = Path('.autocode/evidence/planned-check.json').resolve()
        invocation = shlex.split(data['capture_command']) + ['--output', str(receipt_path), '--', *shlex.split(PLANNED_CHECK)]
        checked = subprocess.run(invocation, capture_output=True, text=True)
        print(json.dumps({'type':'item.completed', 'item':{'id':'captured-check',
            'type':'command_execution', 'command':shlex.join(invocation),
            'exit_code':checked.returncode, 'aggregated_output':checked.stdout + checked.stderr}}), flush=True)
        if checked.returncode:
            print(checked.stderr, file=sys.stderr)
            return 3
        mode = os.environ.get('AUTOCODE_FIXTURE_MUTATION', '')
        if mode == 'direct':
            Path('greet.py').write_text('raise SystemExit(99)\\n')
        elif mode == 'concurrent':
            # A separate real process writes an original source file after the
            # capture. It must remain visible to the ordinary source guard.
            subprocess.run([sys.executable, '-c', "from pathlib import Path; Path('external-note.txt').write_text('concurrent writer\\\\n')"], check=True)
        reference = str(receipt_path)
        report = {**common, 'verdict':'PASS', 'checks_run':[PLANNED_CHECK],
            'findings':[], 'finding_dispositions':[], 'unverified_criteria':[],
            'checks':[{'command':PLANNED_CHECK, 'exit_code':0, 'evidence_ref':reference}],
            'criterion_results':[{'id':'C1', 'status':'PASS', 'evidence_refs':[reference]}],
            'end_to_end_result':{'status':'PASS', 'summary':'Real greeting regression captured',
                'evidence_refs':[reference], 'technical_result':None, 'pending_human_criteria':[]}}
"""
            + script[end:]
        )
        provider = bindir / "codex"
        provider.write_text(script)
        provider.chmod(0o755)
        config, codex_home = self.root / "config", self.root / "codex-home"
        config.mkdir()
        codex_home.mkdir()
        self.env = {
            **os.environ,
            "PATH": str(bindir) + os.pathsep + os.environ["PATH"],
            "AUTOCODE_HOME": str(self.root / "registry"),
            "XDG_CONFIG_HOME": str(config),
            "CODEX_HOME": str(codex_home),
            "PYTHONDONTWRITEBYTECODE": "1",
            "AUTOCODE_FIXTURE_PROBE": str(self.probe),
        }
        self.env.pop("AUTOCODE_PROVIDER", None)
        self.options = OPTIONS

    def run_to_validation(self, mutation=""):
        env = {**self.env, "AUTOCODE_FIXTURE_MUTATION": mutation}
        run = TaskRun.start(self.workspace, BRIEF, options=self.options, env=env, timeout=180)
        view = run.status()
        self.assertEqual("approve_plan", view["needs"]["kind"], view)
        run.approve_plan(view["needs"]["token"])
        try:
            view = run.advance_until_input()
        except TaskRunError as error:
            # Stale validation is an authentic CLI stop, not a successful run.
            self.assertEqual(run.run_dir, error.run_dir)
            view = run.status()
        return run, view

    def test_planned_capture_generates_disposable_output_and_passes_real_clean_replay(self):
        run, view = self.run_to_validation()
        self.assertEqual("TASK_COMPLETE", view["status"], view)
        self.assertTrue(view["done"])
        receipt = json.loads(self.receipt.read_text())
        self.assertEqual(shlex.split(self.command), receipt["command"])
        self.assertEqual(0, receipt["exit_code"])
        self.assertIn("Ran 4 tests", Path(receipt["full_output"]).read_text())
        self.assertIn("verification_copy", receipt)
        self.assertFalse((self.workspace / "other_1.sqlite3").exists())
        launches = [json.loads(line) for line in self.probe.read_text().splitlines()]
        tester = next(row for row in launches if row["stage"] == "sol")
        self.assertTrue(tester["copy"])
        self.assertEqual(run.run_dir, Path(tester["copy"]).parent.parent)
        self.assertFalse(list((self.workspace / ".autocode").glob("tool-containment-*")))
        self.assertEqual(tester["copy_sha256"], receipt["verification_copy"]["manifest_sha256"])
        manifest = json.loads(Path(tester["copy"]).read_text())
        self.assertTrue((Path(manifest["tree"]) / "other_1.sqlite3").exists())
        self.assertTrue(all(not row["copy"] for row in launches if row["stage"] == "terra"))
        # Completion requires independently replayed captured and planned
        # commands. The public evidence proves the report was accepted.
        self.assertIsNotNone(view["evidence"]["check_replay"], view["evidence"])
        self.assertEqual(
            "TASK_COMPLETE", TaskRun(self.workspace, run.run_dir, options=self.options, env=self.env).status()["status"]
        )

    def test_direct_original_source_write_still_pauses_stale_validation(self):
        _, view = self.run_to_validation("direct")
        self.assertEqual("PAUSED_STALE_VALIDATION", view["status"], view)
        self.assertFalse(view["done"])
        self.assertEqual("raise SystemExit(99)\n", (self.workspace / "greet.py").read_text())
        self.assertFalse((self.workspace / "other_1.sqlite3").exists())

    def test_concurrent_original_source_write_is_not_deleted_or_restored(self):
        _, view = self.run_to_validation("concurrent")
        self.assertEqual("PAUSED_STALE_VALIDATION", view["status"], view)
        self.assertFalse(view["done"])
        self.assertEqual("concurrent writer\n", (self.workspace / "external-note.txt").read_text())
        self.assertFalse((self.workspace / "other_1.sqlite3").exists())


class ConfiguredCaptureWorkflowTests(CodexCaptureWorkflowTests):
    """The same public behavior through the real report-file command adapter."""

    def setUp(self):
        super().setUp()
        config = Path(self.env["XDG_CONFIG_HOME"]) / "autocode/providers/offline.toml"
        config.parent.mkdir(parents=True)
        models = ["gpt-6-astra", "gpt-5.6-terra", "gpt-5.6-sol"]
        command = [sys.executable, str(self.root / "bin/codex"), "-o", "{report}"]
        config.write_text(
            'name="offline"\nprompt="stdin"\noutput="report_file"\ncommand='
            + json.dumps(command)
            + "\nmodels="
            + json.dumps(models)
            + "\n[roles]\n"
            + "\n".join(
                f'{role}={{ model="{model}", effort="medium" }}'
                for role, model in (
                    ("astra", "gpt-6-astra"),
                    ("terra", "gpt-5.6-terra"),
                    ("sol", "gpt-5.6-sol"),
                    ("completion", "gpt-6-astra"),
                    ("glm", "gpt-5.6-sol"),
                    ("plan_reviewer", "gpt-6-astra"),
                )
            )
            + "\n"
        )
        self.options = ("--provider", "offline", *OPTIONS[2:])
