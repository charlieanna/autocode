"""A public boundary pause must not discard the Investigator's unconsumed correction."""

import json
import socket
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from . import test_stage_repair_cli as fixtures


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised fake-provider regression requires supported Linux")
class InvestigatorPauseCLI(unittest.TestCase):
    fixture_for = fixtures.StageRepairCLI.fixture_for

    def test_pause_after_stage_keeps_the_investigator_correction_on_resume(self):
        fixture = self.fixture_for("investigator")
        executable = fixture.root / "fixture-bin/codex"
        source = executable.read_text()
        anchor = "    output.write_text(text)\n    append_trace(row)"
        self.assertEqual(1, source.count(anchor))
        executable.write_text(
            source.replace(
                anchor,
                '    row["active_guidance"] = "INVESTIGATOR GUIDANCE" in prompt.split("CURRENT HANDOFF DATA\\n", 1)[0]\n'
                + anchor,
            )
        )
        fixture.launch(["Build a greeting tool", "--no-chat"], 2)
        run = next((fixture.project / ".autocode/runs").iterdir())
        fixture.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)

        def status():
            return json.loads(fixture.launch(["--run-dir", str(run), "--status"], 0).stdout)

        def trace():
            return [json.loads(line) for line in (fixture.root / "stage-repair-trace.jsonl").read_text().splitlines()]

        for _ in range(20):
            fixture.launch(["--run-dir", str(run), "--resume-paused", "--pause-after-stage", "--no-chat"], 2)
            paused = status()
            self.assertEqual("PAUSED_REQUESTED", paused["status"], paused["view"].get("stop_reason"))
            before = trace()
            if paused["next_stage"] == "finalize_plan" and before[-1]["stage"] == "investigate_stuck":
                break
        else:
            self.fail("The scripted Investigator never reached its saved checkpoint")

        # The pending retry first reaches its separate plan-review allowance. Preserve
        # the advice through that hold too; raising a limit must not grant another diagnosis.
        fixture.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        self.assertEqual("PAUSED_PLANNING_BUDGET", status()["view"]["recovery"]["cause"])
        fixture.launch(["--run-dir", str(run), "--planning-review-call-limit", "4", "--no-chat"], 0)
        resumed = fixture.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        after = trace()
        finalizers = [row for row in after[len(before) :] if row["stage"] == "finalize_plan" and not row["repair"]]
        self.assertTrue(finalizers, resumed.stdout[-3000:] + resumed.stderr[-1000:])
        self.assertTrue(finalizers[0]["active_guidance"], "The resumed stage lost its Investigator correction")
        self.assertEqual("approve_plan", status()["view"]["needs"]["kind"])
        self.assertFalse(any(row["stage"] == "builder" for row in after), "No Builder before exact plan approval")
        self.assertEqual(
            [row for row in before if row["stage"] == "investigate_stuck"],
            [row for row in after if row["stage"] == "investigate_stuck"],
            "A checkpoint must not grant another investigation",
        )

    def test_queued_pause_after_investigation_preserves_guidance_for_the_resumed_stage(self):
        fixture = self.fixture_for("investigator")
        executable = fixture.root / "fixture-bin/codex"
        source = executable.read_text()
        anchor = "    output.write_text(text)\n    append_trace(row)"
        self.assertEqual(1, source.count(anchor))
        hook = """    row["active_guidance"] = "INVESTIGATOR GUIDANCE" in prompt.split("CURRENT HANDOFF DATA\\n", 1)[0]
    marker = Path(os.environ["PAUSE_GUIDANCE_MARKER"])
    if stage == "investigate_stuck" and repair and row.get("stuck_status") == "PAUSED_PLANNING_BUDGET" and not marker.exists():
        import socket
        marker.write_text("ready")
        with socket.socket(socket.AF_UNIX) as gate:
            gate.settimeout(90)
            gate.connect(os.environ["PAUSE_GUIDANCE_SOCKET"])
            gate.sendall(b"ready")
            assert gate.recv(1) == b"x"
"""
        executable.write_text(source.replace(anchor, hook + anchor))
        fixture.env["PAUSE_GUIDANCE_MARKER"] = str(fixture.root / "pause-once")
        fixture.launch(["Build a greeting tool", "--no-chat"], 2)
        run = next((fixture.project / ".autocode/runs").iterdir())
        fixture.launch(["--run-dir", str(run), "--answer", "Q1=CLI"], 0)

        def status():
            return json.loads(fixture.launch(["--run-dir", str(run), "--status"], 0).stdout)

        def trace():
            return [json.loads(line) for line in (fixture.root / "stage-repair-trace.jsonl").read_text().splitlines()]

        with tempfile.TemporaryDirectory(prefix="guidance-socket-") as tmp, socket.socket(socket.AF_UNIX) as server:
            server.bind(str(Path(tmp) / "gate"))
            server.listen(1)
            server.settimeout(90)
            fixture.env["PAUSE_GUIDANCE_SOCKET"] = str(Path(tmp) / "gate")
            process = subprocess.Popen(
                [*fixture.entry, "--workspace", str(fixture.project), "--run-dir", str(run), "--no-chat"],
                cwd=fixture.root,
                env=fixture.env,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            try:
                connection, _ = server.accept()
                with connection:
                    self.assertEqual(b"ready", connection.recv(5))
                    self.assertIsNotNone(status()["view"]["pause"]["next_command"])
                    result = subprocess.run(
                        [
                            *fixture.entry,
                            "intervention",
                            "submit",
                            "--workspace",
                            str(fixture.project),
                            "--run-dir",
                            str(run),
                            "--request-id",
                            "pause-after-investigator",
                            "--kind",
                            "pause",
                            "--json",
                        ],
                        cwd=fixture.root,
                        env=fixture.env,
                        capture_output=True,
                        text=True,
                        timeout=30,
                    )
                    self.assertEqual(0, result.returncode, result.stdout + result.stderr)
                    connection.sendall(b"x")
                stdout, stderr = process.communicate(timeout=90)
                self.assertEqual(2, process.returncode, stdout + stderr)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate(timeout=10)

        paused = status()
        self.assertEqual("PAUSED_INTERVENTION", paused["status"], paused["view"].get("stop_reason"))
        before = trace()
        self.assertFalse(any(row["stage"] == "builder" for row in before))
        fixture.launch(["--run-dir", str(run), "--resume-paused", "--no-chat"], 2)
        resumed = trace()[len(before) :]
        finalizers = [row for row in resumed if row["stage"] == "finalize_plan" and not row["repair"]]
        self.assertTrue(finalizers, {"calls": resumed, "view": status()["view"]})
        self.assertTrue(finalizers[0]["active_guidance"], "A pause must retain the unconsumed correction")
        view = status()["view"]
        self.assertEqual("approve_plan", view["needs"]["kind"], view.get("stop_reason"))
        self.assertFalse(any(row["stage"] == "builder" for row in trace()))
        identity = "finalize_plan:PAUSED_REPEATED_FAILURE"
        self.assertEqual(
            sum(row.get("stuck_identity") == identity for row in before),
            sum(row.get("stuck_identity") == identity for row in trace()),
            "Resuming must not renew the spent investigation identity",
        )


if __name__ == "__main__":
    unittest.main()
