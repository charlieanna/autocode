"""The advertised report-check input form runs where a read-only stage's shell can write no file (#3875)."""

from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_report_check as report_check
import autocode_tool_containment as containment
import autocode_tool_handoff as tool_handoff
import autocode_toolchain as toolchain

ROOT = Path(__file__).resolve().parents[1]
PIPE = "printf '%s' '<candidate JSON>' | <report_check_command>"
CANDIDATE = json.dumps({"summary": "it's checked"})
SPEC = ".autocode/runs/own/planning/report-check/builder-001-01.json"
NATIVE_PROBLEM = containment.sandbox_problem()


def no_file_data():
    """Run in the child before exec: neither the shell nor its children can write file data anywhere."""
    resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))


def snapshot(root: Path, skip=lambda path: False) -> dict:
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob("*") if p.is_file() and not skip(p)}


def advertised(workspace: Path) -> tuple[str, str]:
    """Save a builder spec in ``workspace``; return the handoff's instructions and the filled pipe form."""
    spec_path = workspace / SPEC
    spec_path.parent.mkdir(parents=True)
    schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["summary"],
        "properties": {"summary": {"type": "string"}},
    }
    spec_path.write_text(json.dumps(report_check.build_spec(stage="builder", workspace=workspace, schema=schema)))
    prompt = tool_handoff.with_report_check_command('Instr\nCURRENT HANDOFF DATA\n{"stage": "builder"}', spec_path)
    instructions, _, packet = prompt.partition(tool_handoff.MARKER)
    quoted = "'" + CANDIDATE.replace("'", "'\\''") + "'"
    command = json.loads(packet)["report_check_command"]
    return instructions, PIPE.replace("'<candidate JSON>'", quoted).replace("<report_check_command>", command)


class ReadOnlyShellInputTests(unittest.TestCase):
    def test_ac1_handoff_names_file_free_pipe_form(self):
        prompt = 'Instr\nCURRENT HANDOFF DATA\n{"stage": "builder"}'
        once = tool_handoff.with_report_check_command(prompt, "/tmp/spec.json")
        twice = tool_handoff.with_report_check_command(once, "/tmp/spec.json")
        self.assertEqual(once, twice)
        instructions, marker, packet = twice.partition(tool_handoff.MARKER)
        self.assertTrue(marker)
        self.assertEqual(1, instructions.count(PIPE))
        self.assertIn("'\\''", instructions)
        self.assertIn("here-document (<<)", instructions)
        self.assertIn("here-string (<<<)", instructions)
        self.assertIn("the shell backs them with a temporary file", instructions)
        self.assertTrue(json.loads(packet)["report_check_command"].endswith("report-check --spec /tmp/spec.json -"))

    def test_ac2_advertised_pipe_runs_when_no_file_can_be_written(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            workspace, scratch = root / "workspace", root / "tmp"
            workspace.mkdir()
            scratch.mkdir()
            (workspace / "tracked.txt").write_text("clean\n")
            instructions, command = advertised(workspace)
            self.assertIn(PIPE, instructions)
            env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1", "TMPDIR": str(scratch)}
            before, outside = snapshot(workspace), set(snapshot(root))

            result = subprocess.run(
                ["bash", "-c", command],
                cwd=workspace,
                env=env,
                preexec_fn=no_file_data,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertEqual((0, "OK (builder)\n"), (result.returncode, result.stdout), result.stderr)
            self.assertEqual(before, snapshot(workspace))

            # A file-backed input form cannot run under the same limit: Bash puts a here-document
            # larger than the pipe buffer in a temporary file under TMPDIR, and writing it fails.
            heredoc = "wc -c <<'AUTOCODE_INPUT'\n" + "x" * 70000 + "\nAUTOCODE_INPUT\n"
            control = subprocess.run(
                ["bash", "-c", heredoc],
                cwd=scratch,
                env=env,
                preexec_fn=no_file_data,
                capture_output=True,
                text=True,
                timeout=60,
            )
            self.assertNotEqual(0, control.returncode, control.stdout)
            self.assertEqual(before, snapshot(workspace))
            created = set(snapshot(root)) - outside
            self.assertTrue(all((root / name).is_relative_to(scratch) for name in created), created)

    @unittest.skipIf(NATIVE_PROBLEM, f"requires a real kernel sandbox: {NATIVE_PROBLEM}")
    def test_ac3_advertised_pipe_runs_in_read_only_tool_containment(self):
        parent = ROOT / ".autocode"
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="read-only-shell-input-", dir=parent) as directory:
            home = Path(directory).resolve()
            # Metadata custody belongs to Core's ambient configuration; this fixture owns its own store.
            self.enterContext(patch.dict(os.environ, {"XDG_STATE_HOME": str(home / "state")}))
            workspace = home / "project"
            workspace.mkdir()
            (workspace / "tracked.txt").write_text("clean\n")
            instructions, command = advertised(workspace)
            self.assertIn(PIPE, instructions)
            env = {**os.environ, "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")}
            roots = [Path(sys.prefix).resolve(), Path(sys.base_prefix).resolve(), (ROOT / "tools").resolve()]
            selected = toolchain.discover(workspace, (), env)
            before = snapshot(workspace)
            boundary = containment.prepare(
                workspace,
                allow_write=False,
                private_run=workspace / ".autocode/runs/own",
                read_roots=[*roots, *selected["read_roots"]],
                links=selected["links"],
                environment=env,
            )

            result = subprocess.run(
                [boundary["shell"], "-c", command], cwd=workspace, capture_output=True, text=True, timeout=60
            )
            self.assertEqual((0, "OK (builder)\n"), (result.returncode, result.stdout), result.stderr)

            def control(path: Path) -> bool:
                return any(part.startswith("tool-containment-") for part in path.relative_to(workspace).parts)

            self.assertEqual(before, snapshot(workspace, control))


if __name__ == "__main__":
    unittest.main()
