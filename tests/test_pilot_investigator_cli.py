"""Read public inputs from the actual Investigator copy through TaskRun."""
import hashlib
import json
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

ROOT = Path(__file__).resolve().parents[1]
PROVIDER = r'''
import json, subprocess, sys, uuid
from pathlib import Path
if sys.argv[1:] == ["login", "status"]:
    print("Logged in using ChatGPT (offline copy fixture)")
    raise SystemExit(0)
text = sys.stdin.read()
data = json.loads(text.split("CURRENT HANDOFF DATA\n", 1)[1])
assert data["stage"] == "investigate_bug", data
scratch = Path(data["investigation_workspace"])
proc = subprocess.run([sys.executable, "-B", "check_inputs.py"], cwd=scratch,
                      capture_output=True, text=True, timeout=10)
print(json.dumps({"type": "thread.started", "thread_id": str(uuid.uuid4())}), flush=True)
print(json.dumps({"type": "item.completed", "item": {
    "id": "copied-input-read", "type": "command_execution",
    "command": "python3 -B check_inputs.py", "exit_code": proc.returncode,
    "aggregated_output": proc.stdout + proc.stderr}}), flush=True)
receipt = {"scratch": str(scratch), "exit_code": proc.returncode,
           "stdout": proc.stdout, "stderr": proc.stderr, "handoff": data}
Path(__file__).with_name("copy-read.json").write_text(json.dumps(receipt))
if proc.returncode:
    raise SystemExit(1)
report = {"outcome": "not_reproduced", "note_path": "docs/bugs/public-input-access.json",
    "observed": "Reported missing public proof inputs", "reproduction": proc.stdout.strip(),
    "root_cause": "", "affected_paths": [], "test_paths": [], "invariant": "",
    "test_cases": [], "conclusion": "The supplied inputs were read in the prepared copy.",
    "fix_size": "none", "fix_plan": [], "questions": [],
    "tests_run": ["python3 -B check_inputs.py"], "plan_approval_requested": True,
    "probe": "", "untestable": ""}
Path(sys.argv[sys.argv.index("-o") + 1]).write_text(json.dumps(report))
print(json.dumps({"type": "turn.completed", "usage": {
    "input_tokens": 1, "cached_input_tokens": 0, "output_tokens": 1}}), flush=True)
'''


class InvestigatorInputCLI(unittest.TestCase):
    def test_actual_investigator_reads_exact_inputs_without_ignored_content(self):
        output = ROOT / ".scenario-runs" / "pilot-input-copy"
        output.mkdir(parents=True, exist_ok=True)
        root = Path(tempfile.mkdtemp(prefix="cli-", dir=output)).resolve()
        project = root / "project"
        project.mkdir()
        def write(name, content):
            path = project / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
            path.chmod(0o644)
            return path
        write(".gitignore", ".autocode/\n.pilot-env/\nsecrets.env\nnode_modules/\ndist/\n")
        proof = write("pilot-public/PUBLIC-PROOF.md", "proof nonce: " + root.name + "\n")
        write("app.py", "value = 'base'\n")
        subprocess.run(["git", "init", "-q", str(project)], check=True)
        subprocess.run(["git", "-C", str(project), "add", "."], check=True)
        subprocess.run(["git", "-C", str(project), "-c", "user.name=Fixture",
                        "-c", "user.email=fixture@example.test", "commit", "-qm", "fixture"], check=True)
        patch = write("pilot-public/pr42.patch", "public patch nonce: " + root.name + "\n")
        write("app.py", "value = 'dirty'\n")
        for name in ("secrets.env", ".pilot-env/private/token.txt",
                     "node_modules/dep.js", "dist/output.js"):
            write(name, "excluded fixture; no real credential\n")
        expected = {name: {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                           "size": path.stat().st_size, "mode": 0o644}
                    for name, path in (("pilot-public/PUBLIC-PROOF.md", proof),
                                       ("pilot-public/pr42.patch", patch))}
        write("check_inputs.py", """import hashlib, json, stat
from pathlib import Path
expected = """ + repr(expected) + """
observed = {}
for name, identity in expected.items():
    path = Path(name)
    info = path.lstat()
    assert stat.S_ISREG(info.st_mode), name
    observed[name] = {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                      "size": info.st_size, "mode": stat.S_IMODE(info.st_mode)}
assert observed == expected, observed
assert Path("app.py").read_text() == "value = 'dirty'\\n"
for name in ("secrets.env", ".pilot-env", "node_modules", "dist", ".git", ".autocode"):
    assert not Path(name).exists(), "ignored or metadata content copied: " + name
print("PUBLIC_INPUT_READ_PASS " + json.dumps(observed, sort_keys=True))
""")
        before = {path.relative_to(project).as_posix(): path.read_bytes()
                  for path in project.rglob("*") if path.is_file() and ".git" not in path.parts}
        bindir = root / "bin"
        bindir.mkdir()
        provider = bindir / "provider.py"
        provider.write_text(PROVIDER)
        shim = bindir / "codex"
        shim.write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + " " +
                        shlex.quote(str(provider)) + ' "$@"\n')
        shim.chmod(0o755)
        env = {"PATH": str(bindir) + os.pathsep + os.environ["PATH"],
               "AUTOCODE_HOME": str(root / "registry"), "PYTHONDONTWRITEBYTECODE": "1"}
        run = taskrun.TaskRun.start(project,
            "Bug report: public proof inputs are missing. Diagnose by running python3 -B check_inputs.py "
            "in the prepared investigation copy. The exact supplied paths are "
            "pilot-public/PUBLIC-PROOF.md and pilot-public/pr42.patch. Do not change code.",
            options=("--engine", "codex", "--workflow", "bugfix", "--pause-after-stage",
                     "--max-stage-seconds", "30", "--max-seconds", "30",
                     "--joint-planning", "--astra-model", "gpt-6-astra",
                     "--terra-model", "gpt-5.6-terra", "--sol-model", "gpt-5.6-sol",
                     "--completion-model", "gpt-6-astra", "--glm-model", "gpt-5.6-sol",
                     "--plan-reviewer-model", "gpt-6-astra"), env=env, timeout=60)
        receipt = json.loads((bindir / "copy-read.json").read_text())
        self.assertEqual(0, receipt["exit_code"], receipt)
        self.assertIn("PUBLIC_INPUT_READ_PASS", receipt["stdout"])
        scratch = Path(receipt["scratch"])
        self.assertTrue(scratch.is_relative_to(project / ".autocode/investigation"))
        for name, content in before.items():
            self.assertEqual(content, (project / name).read_bytes(), name)
        view = run.status()
        self.assertEqual("bugfix", view["workflow"], view)
        note = json.loads((project / "docs/bugs/public-input-access.json").read_text())
        self.assertFalse(note["reproduced"], note)
        self.assertEqual([], note["changed"])
