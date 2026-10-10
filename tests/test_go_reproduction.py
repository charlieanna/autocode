"""Real Go collection and public investigation replay, using scripted answers."""

import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_verify as verify
from autocode_taskrun import TaskRun, TaskRunError

GO = shutil.which("go")
SOURCE = "package pager\n\nfunc PageCount(total, size int) int { return total / size }\n"
TEST = """package pager
import (
    "flag"
    "testing"
)
var jsonArgument = flag.String("json", "", "literal test program argument")
func Test_t1_truncationProbe(t *testing.T) {
    if PageCount(5, 2) != 2 { t.Fatal("expected the reported truncation bug") }
}
func Test_skipProbe(t *testing.T) {
    t.Skip("reproduction prerequisite is unavailable")
}
func Test_jsonArgument(t *testing.T) {
    if *jsonArgument != "false" { t.Fatalf("json argument changed: %q", *jsonArgument) }
}
func Test_argumentAfterDelimiter(t *testing.T) {
    if args := flag.Args(); len(args) != 1 || args[0] != "-json=false" {
        t.Fatalf("literal test arguments changed: %q", args)
    }
}
"""


@unittest.skipUnless(GO, "needs a Go toolchain")
class GoReproductionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if "GOCACHE" in os.environ:
            cache = os.environ["GOCACHE"]
            if not Path(cache).is_absolute():
                raise RuntimeError("An explicitly supplied GOCACHE must be an absolute directory")
            cls.cache_path = cache
        else:
            cls.cache_directory = tempfile.TemporaryDirectory(prefix="autocode-go-probe-cache-")
            cls.addClassCleanup(cls.cache_directory.cleanup)
            cls.cache_path = cls.cache_directory.name

    def setUp(self):
        if artifacts := os.environ.get("BUILD_AUDIT_ARTIFACTS"):
            Path(artifacts).mkdir(parents=True, exist_ok=True)
            self.root = Path(tempfile.mkdtemp(prefix=self._testMethodName + "-", dir=artifacts)).resolve()
        else:
            directory = tempfile.TemporaryDirectory()
            self.addCleanup(directory.cleanup)
            self.root = Path(directory.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        for name, text in {
            "go.mod": "module example.test/pager\n\ngo 1.20\n",
            "pager.go": SOURCE,
            "pager_test.go": TEST,
            ".gitignore": ".autocode/\n",
        }.items():
            (self.workspace / name).write_text(text)
        for args in (
            ("init", "-q"),
            ("add", "."),
            ("-c", "user.name=T", "-c", "user.email=t@example.test", "commit", "-qm", "buggy pager"),
        ):
            subprocess.run(["/usr/bin/git", *args], cwd=self.workspace, check=True, capture_output=True)
        self.environment = {
            **os.environ,
            "GOCACHE": self.cache_path,
            "GOPROXY": "off",
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "local",
            "GOWORK": "off",
            "GOFLAGS": "",
            "AUTOCODE_HOME": str(self.root / "registry"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        self.patch_environment = mock.patch.dict(os.environ, self.environment)
        self.patch_environment.start()
        self.addCleanup(self.patch_environment.stop)

    def command(self, selector, *flags, executable=GO):
        # The executable is absolute, matching the live command that bypassed
        # zero-collection rejection. Quoted selectors retain their exact argv.
        return shlex.join([executable, "test", ".", "-run", selector, "-count=1", "-timeout=20s", *flags])

    def scratch(self, command):
        run_dir = self.workspace / ".autocode/probe"
        run_dir.mkdir(parents=True)
        receipt = verify.scratch_run(self.workspace, run_dir, command=command, timeout=30)
        (self.root / "scratch-receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        return receipt

    def test_absolute_go_empty_selector_is_rejected_despite_native_zero(self):
        receipt = self.scratch(self.command("^TestMissing$"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertFalse(receipt["timed_out"], receipt)
        self.assertIsInstance(receipt["results"], dict, receipt)
        self.assertEqual(0, receipt["results"]["total"], receipt)
        self.assertIn("zero tests or incomplete per-test results", receipt["error"])
        self.assertIn("-json", shlex.split(receipt["command"]))

    def test_absolute_go_named_probe_has_complete_native_identity(self):
        receipt = self.scratch(self.command("^Test_t1_truncationProbe$"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertFalse(receipt["timed_out"], receipt)
        self.assertEqual("", receipt["error"], receipt)
        self.assertEqual(["example.test/pager::Test_t1_truncationProbe"], receipt["results"]["passed"])
        self.assertEqual(1, receipt["results"]["total"])
        self.assertTrue(receipt["results"]["complete"])

    def test_plain_go_all_skipped_probe_is_rejected_with_complete_named_results(self):
        # The untouched runner already recognizes this literal command. The
        # regression isolates skipped-only acceptance from absolute Go parsing.
        receipt = self.scratch(self.command("Test_skipProbe", executable="go"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertFalse(receipt["timed_out"], receipt)
        self.assertEqual(["example.test/pager::Test_skipProbe"], receipt["results"]["skipped"])
        self.assertEqual([], receipt["results"]["passed"])
        self.assertEqual([], receipt["results"]["failed"])
        self.assertEqual(1, receipt["results"]["total"])
        self.assertTrue(receipt["results"]["complete"])
        self.assertIn("only skipped tests", receipt["error"])

    def test_mixed_real_pass_and_skip_probe_keeps_complete_native_collection(self):
        receipt = self.scratch(self.command("^(Test_t1_truncationProbe|Test_skipProbe)$"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertFalse(receipt["timed_out"], receipt)
        self.assertEqual("", receipt["error"], receipt)
        self.assertEqual(["example.test/pager::Test_t1_truncationProbe"], receipt["results"]["passed"])
        self.assertEqual(["example.test/pager::Test_skipProbe"], receipt["results"]["skipped"])
        self.assertEqual(2, receipt["results"]["total"])
        self.assertTrue(receipt["results"]["complete"])

    def test_disabled_json_does_not_become_collection_proof(self):
        receipt = self.scratch(self.command("^Test_t1_truncationProbe$", "-json=false"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertIsNone(receipt["results"], receipt)
        self.assertIn("zero tests or incomplete per-test results", receipt["error"])
        self.assertIn("-json=false", shlex.split(receipt["command"]))

    def test_leading_and_later_enabled_reporters_keep_real_collection(self):
        for position in ("leading", "later"):
            with self.subTest(position=position):
                words = shlex.split(self.command("^Test_t1_truncationProbe$"))
                if position == "leading":
                    words.insert(2, "-json=true")
                else:
                    words.append("-json=true")
                # Each receipt has its own output root; never parse an earlier log.
                run_dir = self.workspace / ".autocode" / position
                run_dir.mkdir(parents=True)
                receipt = verify.scratch_run(self.workspace, run_dir, command=shlex.join(words), timeout=30)
                self.assertEqual(0, receipt["exit_code"], receipt)
                self.assertEqual("", receipt["error"], receipt)
                self.assertEqual(["example.test/pager::Test_t1_truncationProbe"], receipt["results"]["passed"])
                self.assertEqual(
                    words if position == "leading" else [*words[:2], "-json", *words[2:]],
                    shlex.split(receipt["command"]),
                )

    def test_json_named_test_program_argument_does_not_disable_outer_reporter(self):
        receipt = self.scratch(self.command("^Test_jsonArgument$", "-args", "-json=false"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertEqual("", receipt["error"], receipt)
        self.assertEqual(["example.test/pager::Test_jsonArgument"], receipt["results"]["passed"])

    def test_test_program_delimiter_keeps_literal_arguments_and_real_collection(self):
        receipt = self.scratch(self.command("^Test_argumentAfterDelimiter$", "-args", "--", "-json=false"))
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertEqual("", receipt["error"], receipt)
        self.assertEqual(["example.test/pager::Test_argumentAfterDelimiter"], receipt["results"]["passed"])

    def test_malformed_json_flag_remains_an_authentic_native_failure(self):
        receipt = self.scratch(self.command("^Test_t1_truncationProbe$", "-json=invalid"))
        self.assertNotEqual(0, receipt["exit_code"], receipt)
        self.assertIsNone(receipt["results"], receipt)

    def test_plain_assertion_probe_remains_valid_without_test_collection(self):
        command = shlex.join(
            [
                sys.executable,
                "-B",
                "-c",
                "from pathlib import Path; assert 'return total / size' in Path('pager.go').read_text()",
            ]
        )
        receipt = self.scratch(command)
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertEqual("", receipt["error"], receipt)
        self.assertIsNone(receipt["results"])

    def investigate(self, selector, *flags, executable=GO):
        command = self.command(selector, *flags, executable=executable)
        report = self.root / "report.json"
        report.write_text(
            json.dumps(
                {
                    "outcome": "reproduced",
                    "note_path": "docs/bugs/pager.json",
                    "observed": "PageCount(5, 2) returns 2",
                    "reproduction": "PageCount(5, 2) == 2",
                    "root_cause": "integer division truncates partial pages",
                    "affected_paths": ["pager.go"],
                    "test_paths": ["pager_test.go"],
                    "invariant": "round partial pages up",
                    "test_cases": [
                        {
                            "id": "T1",
                            "given": "total=5 and size=2",
                            "when": "PageCount(5, 2)",
                            "then": "returns 3",
                            "kind": "restore",
                        }
                    ],
                    "probe": command,
                    "untestable": "",
                    "conclusion": "Round up partial pages.",
                    "fix_size": "large",
                    "fix_plan": ["round partial pages up"],
                    "questions": [],
                    "tests_run": [],
                    "plan_approval_requested": False,
                }
            )
        )
        provider = self.root / "provider.py"
        provider.write_text("""import json,subprocess,sys,shlex,hashlib
from pathlib import Path
schema=json.loads(Path(sys.argv[2]).read_text())
if 'outcome' not in schema.get('properties',{}):
    raise SystemExit(2)  # This fixture exercises investigation only.
data=json.loads(sys.stdin.read().split('CURRENT HANDOFF DATA\\n',1)[1])
value=json.loads(Path(sys.argv[3]).read_text())
native=Path(sys.argv[3]).with_suffix('.probe.json')
if data.get('report_repair'):
    previous=json.loads(native.read_text())
    if previous['command'] != shlex.split(value['probe']) or previous['exit_code'] != 0:
        raise RuntimeError('Repair requires the matching prior native probe receipt')
    Path(sys.argv[3]).with_suffix('.repair.json').write_text(json.dumps({
        'original_stage':data['original']['stage'],'validation_error':data['error'],
        'native_probe':str(native),'native_probe_sha256':hashlib.sha256(native.read_bytes()).hexdigest(),
        'probe_reran':False}))
else:
    result=subprocess.run(shlex.split(value['probe']),cwd=data['investigation_workspace'],capture_output=True,text=True,timeout=30)
    native.write_text(json.dumps({'command':shlex.split(value['probe']),'exit_code':result.returncode,'stdout':result.stdout,'stderr':result.stderr}))
    if result.returncode:
        print(result.stderr,file=sys.stderr)
        raise SystemExit(3)
value['tests_run']=[value['probe']+' exited 0 in the prepared source copy']
Path(sys.argv[1]).write_text(json.dumps(value))
print(json.dumps({'type':'turn.completed','usage':{'input_tokens':1,'output_tokens':1}}))
""")
        config = Path(self.environment["XDG_CONFIG_HOME"]) / "autocode/providers/offline.toml"
        config.parent.mkdir(parents=True)
        config.write_text(
            'name="offline"\nprompt="stdin"\noutput="report_file"\ncommand='
            + json.dumps([sys.executable, str(provider), "{report}", "{schema}", str(report)])
            + '\nmodels=["producer","verifier","planner","reviewer"]\n[roles]\n'
            + "\n".join(
                f'{role}={{ model="{model}", effort="medium" }}'
                for role, model in (
                    ("astra", "reviewer"),
                    ("terra", "producer"),
                    ("sol", "verifier"),
                    ("completion", "verifier"),
                    ("glm", "planner"),
                    ("plan_reviewer", "reviewer"),
                )
            )
            + "\n"
        )
        options = (
            "--provider",
            "offline",
            "--workflow",
            "bugfix",
            "--joint-planning",
            "--test-command",
            command,
            "--max-stage-seconds",
            "30",
            "--max-seconds",
            "60",
            "--max-idle-seconds",
            "0",
            "--max-tool-seconds",
            "0",
        )
        try:
            run = TaskRun.start(
                self.workspace,
                "Fix PageCount(5, 2) returning 2 instead of 3.",
                options=options,
                env=self.environment,
                timeout=60,
            )
        except TaskRunError as error:
            self.assertIsNotNone(error.run_dir, str(error))
            run = TaskRun(self.workspace, error.run_dir, options=options, env=self.environment, timeout=30)
        view = run.status()
        (self.root / "public-status.json").write_text(json.dumps(view, indent=2) + "\n")
        return view, report.with_suffix(".probe.json")

    def assert_original_public_rejection(self, native):
        repair = json.loads((self.root / "report.repair.json").read_text())
        self.assertEqual("investigate_bug", repair["original_stage"], repair)
        self.assertIn("reproduction claims' probes", repair["validation_error"])
        self.assertEqual(str(native), repair["native_probe"], repair)
        self.assertEqual(hashlib.sha256(native.read_bytes()).hexdigest(), repair["native_probe_sha256"])
        self.assertIs(False, repair["probe_reran"], repair)

    def test_public_investigation_rejects_native_zero_with_empty_absolute_go_selector(self):
        view, native = self.investigate("^TestMissing$")
        self.assertEqual(0, json.loads(native.read_text())["exit_code"])
        self.assertIn("[no tests to run]", json.loads(native.read_text())["stdout"])
        self.assertFalse((self.workspace / "docs/bugs/pager.json").exists(), view)
        self.assertEqual([], view["evidence"]["test_cases"], view)
        self.assertFalse(view["done"], view)
        self.assert_original_public_rejection(native)

    def test_public_investigation_accepts_self_contained_actual_named_go_probe(self):
        view, native = self.investigate("^Test_t1_truncationProbe$")
        self.assertEqual(0, json.loads(native.read_text())["exit_code"])
        note = self.workspace / "docs/bugs/pager.json"
        self.assertTrue(note.is_file(), view)
        accepted = json.loads(note.read_text())
        self.assertIn(shlex.quote(GO), accepted["proven_by"])
        self.assertIn("Test_t1_truncationProbe", accepted["proven_by"])
        self.assertEqual(["T1"], [case["id"] for case in view["evidence"]["test_cases"]], view)
        self.assertEqual(SOURCE, (self.workspace / "pager.go").read_text())
        self.assertEqual(TEST, (self.workspace / "pager_test.go").read_text())

    def test_public_investigation_rejects_all_skipped_probe_with_native_named_skip(self):
        view, native = self.investigate("Test_skipProbe", "-json", executable="go")
        observed = json.loads(native.read_text())
        self.assertEqual(0, observed["exit_code"])
        events = [json.loads(line) for line in observed["stdout"].splitlines()]
        self.assertTrue(
            any(event.get("Action") == "skip" and event.get("Test") == "Test_skipProbe" for event in events), observed
        )
        self.assertFalse(
            any(event.get("Action") in ("pass", "fail") and event.get("Test") for event in events), observed
        )
        self.assertFalse((self.workspace / "docs/bugs/pager.json").exists(), view)
        self.assertEqual([], view["evidence"]["test_cases"], view)
        self.assertFalse(view["done"], view)
        self.assert_original_public_rejection(native)
