"""The bug-fix Investigator in a run of one package, through TaskRun (#711).

A stand-in Codex plays the Investigator in a two-package repository; the run is started with
``--workflow bugfix --package packages/invoices/``. Its handoff names the package and the suite detected
there, and a reproduced diagnosis that names a file in the other package is refused as invalid output
instead of being planned from; one that names only the package's files is accepted, and the runner writes
its diagnosis note under ``docs/bugs/`` at the repository root.
"""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

ROOT = Path(__file__).resolve().parents[1]
PACKAGE = "packages/invoices"
NOTE = "docs/bugs/invoice-pages.json"
PAGER = "def page_count(total, size):\n    return total // size\n"
SEED = {
    "README.md": "Two packages: packages/invoices/ and packages/search/, each with its own pager.\n",
    f"{PACKAGE}/pager.py": PAGER,
    f"{PACKAGE}/test_pager.py": (
        "import unittest\n\nfrom pager import page_count\n\n\n"
        "class PagerTest(unittest.TestCase):\n"
        "    def test_exact_multiple(self):\n"
        "        self.assertEqual(2, page_count(10, 5))\n"
    ),
    "packages/search/pager.py": PAGER,
}
# Writes the report named by REPORT for the Investigator and for a repair of its report (a handoff with the
# rejected_report), and leaves a stage that stopped converging to the person; records each call in
# calls.jsonl. Any other stage is not scripted and fails.
PROVIDER = r"""
import json, os, sys, uuid
from pathlib import Path
if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT (offline package Investigator fixture)")
    raise SystemExit(0)
text = sys.stdin.read()
marker = "CURRENT HANDOFF DATA\n"
data = json.loads(text.split(marker, 1)[1]) if marker in text else {}
call = "report repair" if "rejected_report" in data else data.get("stage")
here = Path(__file__).parent
with (here / "calls.jsonl").open("a") as calls:
    calls.write(json.dumps({"call": call, "package_scope": data.get("package_scope")}) + "\n")
print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}), flush=True)
if call == "investigate_stuck":
    report = json.dumps({"diagnosis": "The scripted Investigator repeats its report.", "cause": "stage_output",
        "guidance": "", "recommendation": "pause", "user_question": "", "evidence_refs": [], "example": "",
        "probe": "", "untestable": ""})
elif call in ("investigate_bug", "report repair"):
    report = Path(os.environ["REPORT"]).read_text()
else:
    print("only the Investigator is scripted", file=sys.stderr)
    raise SystemExit(1)
Path(sys.argv[sys.argv.index("-o") + 1]).write_text(report)
print(json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}), flush=True)
"""
TASK = (
    "Bug report for packages/invoices/: page_count(11, 5) returns 2, but eleven invoices in pages of five is "
    "three pages. Fix it in packages/invoices/ only; packages/search/ belongs to another team."
)


def diagnosis(affected_paths, test_paths):
    return {
        "outcome": "reproduced",
        "note_path": NOTE,
        "observed": "page_count(11, 5) returns 2",
        "reproduction": "cd packages/invoices && python3 -c 'from pager import page_count; print(page_count(11, 5))'",
        "root_cause": "page_count floors the division and drops the last partial page",
        "affected_paths": affected_paths,
        "test_paths": test_paths,
        "invariant": "page_count(total, size) is the ceiling of total / size",
        "test_cases": [
            {
                "id": "T1",
                "given": "11 invoices",
                "when": "page_count(11, 5) runs",
                "then": "it returns 3",
                "kind": "restore",
            }
        ],
        "conclusion": "Round the page count up.",
        "fix_size": "large",
        "fix_plan": ["round up in page_count", "add test_t1_partial_page_counts"],
        "questions": [],
        "tests_run": ["python3 -m unittest"],
        "plan_approval_requested": False,
        "workspace_scope": {"spans_repositories": False, "external_parts": []},
        "counterexample": {"file": "", "find": "", "replace": ""},
        "probe": "",
        "untestable": "The fixture keeps the reproduction to the scripted report",
    }


class PackageInvestigatorCLI(unittest.TestCase):
    def setUp(self):
        output = ROOT / ".scenario-runs" / "package-investigator"
        output.mkdir(parents=True, exist_ok=True)
        self.root = Path(tempfile.mkdtemp(prefix="cli-", dir=output)).resolve()
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.project = self.root / "project"
        for name, content in SEED.items():
            path = self.project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        subprocess.run(["git", "init", "-q", str(self.project)], check=True)
        (self.project / ".gitignore").write_text(".autocode/\n")
        subprocess.run(["git", "-C", str(self.project), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(self.project), "-c", "user.name=Fixture", "-c", "user.email=fixture@example.test"]
            + ["commit", "-qm", "seed: two packages with the same pager bug"],
            check=True,
        )
        self.bindir = self.root / "bin"
        self.bindir.mkdir()
        provider = self.bindir / "provider.py"
        provider.write_text(PROVIDER)
        shim = self.bindir / "codex"
        shim.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " + shlex.quote(str(provider)) + ' "$@"\n')
        shim.chmod(0o755)

    def investigate(self, report):
        path = self.root / "report.json"
        path.write_text(json.dumps(report))
        env = {
            "PATH": str(self.bindir) + os.pathsep + os.environ["PATH"],
            "AUTOCODE_HOME": str(self.root / "registry"),
            "PYTHONDONTWRITEBYTECODE": "1",
            "REPORT": str(path),
        }
        run = taskrun.TaskRun.start(
            self.project,
            TASK,
            start_options=("--workflow", "bugfix", "--package", f"{PACKAGE}/"),
            options=(
                "--engine",
                "codex",
                "--pause-after-stage",
                "--max-stage-seconds",
                "30",
                "--max-seconds",
                "60",
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
            ),
            env=env,
            timeout=60,
        )
        calls = [json.loads(line) for line in (self.bindir / "calls.jsonl").read_text().splitlines()]
        return run, calls

    def test_the_handoff_names_the_package_and_its_suite(self):
        _, calls = self.investigate(diagnosis([f"{PACKAGE}/pager.py"], [f"{PACKAGE}/"]))
        scope = calls[0]["package_scope"]
        self.assertEqual("investigate_bug", calls[0]["call"])
        self.assertEqual(f"{PACKAGE}/", scope["package"])
        self.assertTrue(scope["suite"].startswith(f"cd {PACKAGE} && "), scope)
        self.assertTrue(scope["suite"].endswith(" -m unittest discover -v"), scope)
        self.assertIn(f"{PACKAGE}/: the reported bug and its fix belong there", scope["instruction"])
        self.assertIn("affected_paths and test_paths name only files or directories inside", scope["instruction"])

    def test_a_diagnosis_inside_the_package_is_accepted_and_its_note_is_written_at_the_root(self):
        run, _ = self.investigate(diagnosis([f"{PACKAGE}/pager.py"], [f"{PACKAGE}/"]))
        note = json.loads((self.project / NOTE).read_text())
        self.assertEqual([f"{PACKAGE}/pager.py"], note["affected_paths"])
        view = run.status()
        self.assertEqual(("bugfix", PACKAGE), (view["workflow"], view["package"]))
        self.assertNotIn(view["next_stage"], (None, "investigate_bug"), view)

    def test_a_diagnosis_that_names_the_other_package_is_refused_and_not_planned_from(self):
        run, _ = self.investigate(diagnosis([f"{PACKAGE}/pager.py", "packages/search/pager.py"], [PACKAGE]))
        view = run.status()
        details = {key: view.get(key) for key in ("status", "next_stage", "stop_reason", "needs")}
        self.assertTrue(view["status"].startswith("PAUSED_") or view["status"] == "WAITING_FOR_USER", details)
        self.assertFalse(view["done"])
        self.assertNotEqual("approve_plan", (view["needs"] or {}).get("kind"))
        self.assertEqual("investigate_bug", view["next_stage"])
        self.assertIn("packages/search/pager.py", view["stop_reason"])
        self.assertIn(f"This run works in the package {PACKAGE}/", view["stop_reason"])
        self.assertIn("A fix outside the package needs a run without --package", view["stop_reason"])
        self.assertFalse((self.project / NOTE).exists())
        # The Investigator, its report's repairs and the stuck-stage Investigator ran; nothing planned the fix.
        made = [json.loads(line)["call"] for line in (self.bindir / "calls.jsonl").read_text().splitlines()]
        self.assertEqual("investigate_bug", made[0], made)
        self.assertLessEqual(set(made), {"investigate_bug", "report repair", "investigate_stuck"}, made)


if __name__ == "__main__":
    unittest.main()
