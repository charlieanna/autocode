"""The Tester and Completion Reviewer probes: a scripted Builder plants a flaw (#3630).

The flaws are checked without a model: each flawed build passes its own tests and fails the plan's,
and the sound build passes both. Then whole probe requests run through the real `autocode` CLI, with
every stage but the measured one on the bundled scripted tool and the measured one on a fake model
that judges by whether the greeting is sound ("good"), or passes everything ("lenient").
"""

import contextlib
import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_check_probe as check_probe
import autocode_measured_routes as measured_routes
import autocode_model_probe as model_probe
import autocode_probe_cli as probe_cli
import probe_script_provider as probe_script

TOOLS = Path(__file__).resolve().parents[1] / "tools"
MODEL = "zai-coding-plan/glm-5.3"

# The measured model: the scripted tool's own stage playing, with a judgment of its own on top. "good"
# sends back a greeting that is not the sound one; "lenient" passes everything.
FAKE_MODEL = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
sys.path.insert(0, __TOOLS__)
import probe_script_provider as provider

MODELS = [__MODEL__]
if sys.argv[1:2] == ["models"]:
    print("\n".join(MODELS))
    raise SystemExit(0)
if sys.argv[1:] == ["--version"]:
    print("1.16.0")
    raise SystemExit(0)


def sound():
    builds = json.loads(Path(os.environ[provider.SCRIPT_ENV]).read_text())["builds"]
    return all(Path(path).read_text() == text for path, text in builds[-1]["files"].items())


FINDING = {"id": "", "severity": "high", "finding": "The greeting does not do what the plan asks",
           "evidence": "event:check", "blocking": True}


def judge(stage, report, data):
    own = (data.get("review_identity_policy") or {}).get("own_open_finding_ids") or []
    report["finding_dispositions"] = [{"id": i, "disposition": "resolved", "evidence": "event:check"} for i in own]
    if os.environ.get("FAKE_JUDGE") == "lenient" or sound():
        return report
    if stage == "tester":
        report.update(verdict="FAIL", findings=[{**FINDING, "reproduction_steps": ["python3 greet.py ''"],
                      "expected": "a usage error", "actual": "a greeting", "why_it_matters": "the plan asks for it",
                      "suggested_correction": "follow the plan"}])
        report["criterion_results"] = [{"id": "C1", "status": "FAIL", "evidence_refs": ["event:check"]}]
        report["end_to_end_result"]["status"] = "FAIL"
    if stage == "review_completion":
        report = provider.rework(stage, report, data, ["README.md", "greet.py", "test_greet.py"])
        report["findings"] = [FINDING]
    return report


argv = sys.argv[1:]
args = ["run", "--model", argv[argv.index("--model") + 1]]
if "--session" in argv:
    args += ["--session", argv[argv.index("--session") + 1]]
raise SystemExit(provider.main(args, models=MODELS, judge=judge))
"""


def run_tests(files, tests):
    with tempfile.TemporaryDirectory() as folder:
        for path, text in {**files, "test_greet.py": tests}.items():
            Path(folder, path).write_text(text, encoding="utf-8")
        result = subprocess.run(
            [sys.executable, "-m", "unittest", "test_greet"], cwd=folder, capture_output=True, text=True, timeout=120
        )
        return result.returncode == 0


class FlawTests(unittest.TestCase):
    def test_each_flaw_passes_its_own_tests_and_fails_the_plans(self):
        flaws = set()
        for request in check_probe.REQUESTS:
            builds = request.builds()
            with self.subTest(request=request.id):
                self.assertEqual(2 if request.flaw else 1, len(builds))
                self.assertEqual(check_probe.SOUND, builds[-1]["files"], "the last build is the sound greeting")
                for build in builds:
                    self.assertTrue(run_tests(build["files"], build["files"]["test_greet.py"]))
                if request.flaw:
                    self.assertFalse(run_tests(builds[0]["files"], check_probe.ORACLE), "the plan's tests catch it")
                    flaws.add(request.flaw)
                self.assertTrue(run_tests(builds[-1]["files"], check_probe.ORACLE))
        self.assertEqual(len(flaws), sum(1 for request in check_probe.REQUESTS if request.flaw))
        self.assertIn(None, {check_probe.BY_ID[name].flaw for name in check_probe.QUICK})

    def test_the_brief_quotes_what_the_scripted_requirements_cite(self):
        for quote in (
            "It prints 'Hello, NAME' for one nonempty name argument and exits 0.",
            "Any other argument count (no arguments, or two or more) prints a usage line to stderr and exits 2.",
            "Python standard library only.",
            "Deliver greet.py, test_greet.py with regression tests, and a short README.md.",
        ):
            self.assertIn(quote, check_probe.BRIEF)


def launches(*sent_back):
    rows = [{"stage": "builder", "build": 0, "sent_back_by": []}]
    for index, sources in enumerate(sent_back, start=1):
        rows.append({"stage": "tester"})
        rows.append({"stage": "builder", "build": min(index, 1), "sent_back_by": list(sources)})
    return rows


PASSED = {"ok": True, "ran": 6, "output": ""}
FAILED = {"ok": False, "ran": 6, "output": "FAILED (failures=1)"}


class GradeTests(unittest.TestCase):
    def grade(self, request, stage="tester", *, status="TASK_COMPLETE", log=None, result=PASSED, seconds=60.0):
        return check_probe.grade(
            request, stage, run_status=status, launches=log or launches(), result_tests=result, seconds=seconds
        )

    def test_a_caught_flaw_and_an_approved_sound_change_pass(self):
        self.assertTrue(self.grade("K2", log=launches(["tester"]))["passed"])
        self.assertTrue(self.grade("K1")["passed"])
        completion = self.grade("K2", "completion", log=launches(["task_planner", "runner"]))
        self.assertTrue(completion["passed"], completion)

    def test_a_missed_flaw_fails(self):
        graded = self.grade("K2", result=FAILED)
        self.assertEqual(["sent_back_flaw", "result_sound"], graded["failed"])

    def test_a_send_back_by_another_stage_is_not_the_measured_ones(self):
        graded = self.grade("K2", log=launches(["runner"]))
        self.assertIn("sent_back_flaw", graded["failed"])
        self.assertIn("sent_back_flaw", self.grade("K2", "completion", log=launches(["tester"]))["failed"])

    def test_sending_the_sound_build_back_fails(self):
        self.assertEqual(["approved_sound"], self.grade("K1", log=launches(["tester"]))["failed"])
        self.assertEqual(["approved_sound"], self.grade("K2", log=launches(["tester"], ["tester"]))["failed"])

    def test_an_unfinished_run_or_a_slow_check_fails(self):
        self.assertEqual(["completed"], self.grade("K1", status="PAUSED_BUDGET")["failed"])
        self.assertEqual(["time"], self.grade("K1", seconds=check_probe.TIME_LIMIT_SECONDS + 1)["failed"])
        self.assertEqual(["time"], self.grade("K1", seconds=None)["failed"])

    def test_the_quick_pass_needs_both_quick_requests(self):
        grades = [self.grade("K1"), self.grade("K2", log=launches(["tester"]))]
        self.assertTrue(check_probe.passes_quick(grades))
        self.assertFalse(check_probe.passes_quick(grades[:1]))


class EstimateAndCliTests(unittest.TestCase):
    def cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = probe_cli.cli(argv)
        return code, out.getvalue(), err.getvalue()

    def test_the_estimate_counts_every_request_and_says_the_rest_is_scripted(self):
        for stage in ("tester", "completion"):
            estimate = model_probe.estimate([(MODEL, "high")], stage)
            self.assertEqual(len(check_probe.REQUESTS), estimate["calls"])
            self.assertEqual(len(check_probe.QUICK), estimate["quick_calls"])
            self.assertIn("a script that calls no model", estimate["basis"])
            self.assertIsNotNone(estimate["max_usd"])
        self.assertEqual(("tester-v1", 5), measured_routes.PROBES["tester"])

    def test_a_check_probe_takes_no_planner_checker_or_requirements_and_no_codex(self):
        base = ["--stage", "tester", "--measure", MODEL, "high"]
        code, out, _err = self.cli([*base, "--json"])
        self.assertEqual(0, code)
        self.assertFalse(json.loads(out)["ran"])
        for extra, words in (
            (["--checker", MODEL, "high"], "--checker"),
            (["--planner", MODEL, "high"], "--planner"),
            (["--requirements", MODEL, "low"], "--requirements does not apply"),
            (["--engine", "codex"], "Codex"),
        ):
            with self.subTest(extra=extra):
                code, _out, err = self.cli([*base, *extra])
                self.assertEqual(2, code)
                self.assertIn(words, err)


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised provider launches require supported Linux")
class ScriptedRunTests(unittest.TestCase):
    """Probe requests through the real CLI: only the measured stage is not the script."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory(prefix="check-probe-")
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        binary = self.root / "bin"
        binary.mkdir()
        fake = binary / "opencode"
        fake.write_text(FAKE_MODEL.replace("__TOOLS__", repr(str(TOOLS))).replace("__MODEL__", repr(MODEL)))
        fake.chmod(0o755)
        self.env = {
            "PATH": f"{binary}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.root / "home"),
            "XDG_CONFIG_HOME": str(self.root / "config"),
            "XDG_DATA_HOME": str(self.root / "data"),
            "XDG_CACHE_HOME": str(self.root / "cache"),
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "OPENCODE_PURE": "1",
        }
        self.options = ("--engine", "opencode", "--provider", "opencode", "--allow-uncontained-tools")

    def request(self, request, stage, judge="good"):
        env = {**self.env, "FAKE_JUDGE": judge}
        return model_probe.run_check_request(MODEL, "high", request, stage=stage, options=self.options, env=env)

    def test_a_tester_that_sends_the_flaw_back_and_passes_the_fix_passes(self):
        graded = self.request("K2", "tester")
        self.assertEqual("TASK_COMPLETE", graded["run_status"], graded)
        self.assertTrue(graded["passed"], graded)
        self.assertEqual([0], graded["sent_back"])
        self.assertEqual(2, graded["builds"])

    def test_a_tester_that_passes_the_flaw_fails(self):
        graded = self.request("K2", "tester", judge="lenient")
        self.assertEqual("TASK_COMPLETE", graded["run_status"], graded)
        self.assertEqual(["sent_back_flaw", "result_sound"], graded["failed"])
        self.assertEqual(1, graded["builds"])

    def test_a_completion_reviewer_that_sends_the_flaw_back_passes(self):
        graded = self.request("K4", "completion")
        self.assertEqual("TASK_COMPLETE", graded["run_status"], graded)
        self.assertTrue(graded["passed"], graded)
        self.assertEqual([0], graded["sent_back"])


class ScriptToolTests(unittest.TestCase):
    def test_the_script_refuses_to_run_outside_a_probe_and_offers_one_name_per_job(self):
        env = {key: value for key, value in os.environ.items() if key != probe_script.SCRIPT_ENV}
        script = str(TOOLS / "probe_script_provider.py")
        refused = subprocess.run([sys.executable, script, "run"], env=env, capture_output=True, text=True)
        self.assertEqual(2, refused.returncode)
        self.assertIn("only inside `autocode probe`", refused.stderr)
        listed = subprocess.run([sys.executable, script, "models"], capture_output=True, text=True)
        self.assertEqual(sorted(set(probe_script.MODELS.values())), listed.stdout.split())
        self.assertNotEqual(probe_script.MODELS["builder"], probe_script.MODELS["tester"])
        self.assertNotEqual(probe_script.MODELS["planner"], probe_script.MODELS["plan_reviewer"])


if __name__ == "__main__":
    unittest.main()
