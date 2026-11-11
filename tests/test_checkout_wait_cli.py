"""Requirements leaves the folder free for another chat, and the Planner takes it (2026-11-11).

Two VS Code chats in one folder: one chat's Requirements held the checkout while it talked with the
person, so the other chat's Planner found the folder busy and stopped at a bare Continue. Requirements
only reads the code; the offline fixture provider tries the folder's writer lock from inside each stage
to show who holds it while that stage works.
"""

import os
import sys
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


class CheckoutWaitCliTests(unittest.TestCase):
    def test_requirements_leaves_the_folder_free_and_the_planner_holds_it(self):
        workspace, env = fixture_workspace(self)
        probes = workspace.parent / "probes.txt"
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        source = provider.read_text()
        self.assertIn(marker, source)
        probe = textwrap.dedent(f"""
            if stage in ("requirements_gather", "draft_plan"):
                import fcntl
                with open({str(workspace / ".autocode" / "writer.lock")!r}, "a+") as writer:
                    try:
                        fcntl.flock(writer, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        fcntl.flock(writer, fcntl.LOCK_UN)
                        held = "free"
                    except BlockingIOError:
                        held = "held"
                with open({str(probes)!r}, "a") as record:
                    record.write(f"{{stage}} {{held}}\\n")
        """)
        provider.write_text(source.replace(marker, textwrap.indent(probe, "    ") + "\n" + marker))
        env["PATH"] = str(Path(sys.executable).parent) + os.pathsep + env["PATH"]
        run = taskrun.TaskRun.start(
            workspace,
            BRIEF,
            options=(*FIXTURE_OPTIONS, "--spec-first", "--no-adaptive-planning"),
            env=env,
            timeout=180,
        )
        review = run.advance_until_input()
        self.assertEqual("answer", review["needs"]["kind"], review["stop_reason"])
        self.assertIsNone(review["checkout_wait"], "a run waiting for its person waits for no folder")
        question = review["needs"]["questions"][0]
        run.answer(question["id"], review["chat"]["review"]["answer"])
        plan = run.advance_until_input()
        self.assertEqual("approve_plan", plan["needs"]["kind"], plan["stop_reason"])
        probed = probes.read_text().splitlines()
        self.assertIn("requirements_gather free", probed, "Requirements left the folder to another chat")
        self.assertNotIn("requirements_gather held", probed)
        self.assertIn("draft_plan held", probed, "the Planner holds the folder while it works")


if __name__ == "__main__":
    unittest.main()
