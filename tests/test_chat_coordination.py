"""A coordinated member owns the foreground while anchor polling continues."""

import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import autocode_chat_client as chat
import autocode_chat_coordination as coordination


class CoordinatedDisplayTests(unittest.TestCase):
    def test_anchor_reads_cannot_replace_the_active_member_and_callbacks_are_restored(self):
        for fail in (False, True):
            with self.subTest(child_fails=fail):
                events = []
                observed = lambda view: events.append(("view", view["run_dir"]))
                unavailable = lambda reason: events.append(("unavailable", reason))
                workspace = Path("/tmp/coordinated-display")
                anchor = {
                    "workspace": str(workspace),
                    "run_dir": str(workspace / "anchor"),
                    "status": "WAITING_FOR_USER",
                    "needs": {"kind": "answer"},
                }
                member = {
                    **anchor,
                    "workspace": str(workspace / "member"),
                    "run_dir": str(workspace / "member/run"),
                    "status": "RUNNING",
                    "needs": {"kind": "continue"},
                }
                parent = chat.ChatClient(
                    workspace, Path(anchor["run_dir"]), say=Mock(), observe=observed, unavailable=unavailable
                )
                self.addCleanup(parent.close)

                def respond(child, view):
                    # ChatTUI.wait still pumps the anchor's idle poll while this
                    # child advances. Its data stays fresh but must not steal focus.
                    parent.accept_status(anchor)
                    if parent._unavailable:
                        parent._unavailable("anchor-only outage")
                    child.accept_status(member)
                    if child._unavailable:
                        child._unavailable("member outage")
                    if fail:
                        raise RuntimeError("child refused")
                    return True

                summary = {"members": [], "done": False, "needs": {"kind": "input", "view": member}}
                with (
                    patch.object(coordination, "read", return_value=summary),
                    patch.object(chat.ChatClient, "respond", new=respond),
                ):
                    if fail:
                        with self.assertRaisesRegex(RuntimeError, "child refused"):
                            coordination.step(parent, anchor, Mock())
                    else:
                        self.assertIsNone(coordination.step(parent, anchor, Mock()))
                self.assertEqual(
                    [("view", member["run_dir"]), ("view", member["run_dir"]), ("unavailable", "member outage")],
                    events,
                )
                parent.accept_status(anchor)
                self.assertEqual(("view", anchor["run_dir"]), events[-1])
                self.assertIs(parent._observe, observed)
                self.assertIs(parent._unavailable, unavailable)


if __name__ == "__main__":
    unittest.main()
