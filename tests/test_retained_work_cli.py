"""A repair assignment must send unchanged retained work through fresh validation."""

import json
import unittest

from tests import test_subprocess as flow


class RetainedRepairFlow(unittest.TestCase):
    new_run_engine_args = ("--engine", "codex")
    setUp = flow.SubprocessFlow.setUp
    launch = flow.SubprocessFlow.launch

    def run_retained_candidate(self, *, broken=False):
        provider = self.root / "fixture-bin" / "codex"
        source = provider.read_text()
        # Inject one rejected review. The Completion Owner then creates a repair
        # assignment; its Builder preserves the candidate and submits a new report.
        # Later Validators execute the real CLI checks as usual.
        source = source.replace(
            "    goodbye_passed = False",
            """    marker = Path('.autocode/first-review-rejected')
    if not marker.exists():
        marker.write_text('injected review failure')
        passed = False
    goodbye_passed = False""",
        )
        source = source.replace(
            "    elif builder_files:\n        for name, content",
            "    elif Path('greet.py').exists():\n        pass\n    elif builder_files:\n        for name, content",
        )
        provider.write_text(source)
        self.env["AUTOCODE_FIXTURE_MODE"] = "no-human"
        if broken:
            files = self.root / "broken-source.json"
            files.write_text(json.dumps({"greet.py": "import sys\nprint('Hello, ' + sys.argv[1])\n"}))
            self.env["AUTOCODE_FIXTURE_FILES"] = str(files)
        probe = self.root / "launches.jsonl"
        self.env["AUTOCODE_REGISTRY_LAUNCH_PROBE"] = str(probe)
        self.launch(["Build greeting", "--chat"], 2 if broken else 0, answers="CLI\nyes\n")
        stages = [json.loads(line)["stage"] for line in probe.read_text().splitlines()]
        self.assertGreaterEqual(stages.count("sol"), 2)
        run = next((self.project / ".autocode/runs").iterdir())
        status = json.loads(self.launch(["--run-dir", str(run), "--status"], 0).stdout)["view"]
        return status, stages

    def test_new_assignment_reviews_retained_work_without_another_source_edit(self):
        status, stages = self.run_retained_candidate()
        self.assertEqual(2, stages.count("terra"))
        self.assertEqual(2, stages.count("sol"))
        self.assertEqual("TASK_COMPLETE", status["status"])
        self.assertTrue(status["done"])

    def test_retained_broken_source_still_fails_fresh_validation(self):
        status, _ = self.run_retained_candidate(broken=True)
        self.assertFalse(status["done"])
        self.assertNotEqual("TASK_COMPLETE", status["status"])


if __name__ == "__main__":
    unittest.main()
