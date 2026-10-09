"""Native event hook conformance: fake clock, real JS hook and report parser."""
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from providers import opencode

HOOK = Path(opencode.__file__).with_name("opencode_activity.mjs")


class NativeActivityHook(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node is needed to execute the OpenCode hook")
    def test_native_deltas_are_bounded_metadata_without_heartbeats_or_cross_session_credit(self):
        script = r"""
import plugin from HOOK;
let now = 0;
Object.defineProperty(globalThis.performance, "now", {value: () => now});
Date.now = () => now;
const hook = await plugin();
const update = (id, kind = "text", sessionID = "main", end) => hook.event({event: {
  type: "message.part.updated", properties: {part: {id, type: kind, sessionID, time: end ? {end} : {}}}}});
const delta = (id, text, eventID, sessionID = "main") => hook.event({event: {
  id: eventID, type: "message.part.delta", properties: {sessionID, partID: id, field: "text", delta: text}}});
await hook["chat.message"]({sessionID: "main"});
await update("part");
await delta("part", "private report fragment", "e1");
now = 100;
await delta("part", " + second", "e2");
now = 300;
await delta("part", " + third", "e3");
now = 700;
await delta("part", "private report fragment", "e1"); // replay
await delta("part", "outside", "other", "foreign");
await delta("unknown", "unbound", "missing");
await update("tool", "tool");
await delta("tool", "not text", "tool-event");
now = 800;
await delta("part", " \t\n", "whitespace");
now = 1000;
await delta("part", " + final", "e4");
now = 1010;
await delta("part", " remainder", "e5");
await update("part", "text", "main", 1010); // flush without fabricating completion
await hook["chat.message"]({sessionID: "next"});
now = 1300;
await delta("part", "old session", "old");
await update("next-part", "reasoning", "next");
await delta("next-part", "new reasoning fragment", "next1", "next");
""".replace("HOOK", json.dumps(HOOK.resolve().as_uri()))
        result = subprocess.run([shutil.which("node"), "--input-type=module", "-e", script],
                                capture_output=True, text=True, check=True, timeout=15)
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual([0, 300, 1000, 1010, 1300], [r["timestamp"] for r in rows])
        self.assertEqual(["main"] * 4 + ["next"], [r["sessionID"] for r in rows])
        self.assertTrue(all(r["type"] == "autocode_progress" for r in rows))
        positions = [r["progress"]["position"] for r in rows[:4]]
        self.assertEqual(sorted(set(positions)), positions)
        self.assertNotIn("private report fragment", result.stdout)
        self.assertNotIn("reasoning fragment", result.stdout)
        self.assertNotIn("part", rows[0])  # no native part ID usable as command/report proof
        for row in rows:
            self.assertRegex(row["progress"]["content_hash"], r"^[0-9a-f]{64}$")
            self.assertRegex(row["progress"]["delta_hash"], r"^[0-9a-f]{64}$")

    def test_launch_preserves_plugins_and_registers_one_packaged_hook(self):
        uri = HOOK.resolve().as_uri()
        self.assertTrue(HOOK.is_file())
        with patch.dict("os.environ", {"OPENCODE_CONFIG_CONTENT": json.dumps({
                "plugin": ["file:///existing.mjs", uri]})}):
            _, env, saved = opencode.launch("glm", Path("/workspace"), Path("/run"), None,
                                             "openai/gpt-6-sol", "medium", False, planning=True)
        self.assertEqual(["file:///existing.mjs", uri], json.loads(env["OPENCODE_CONFIG_CONTENT"])["plugin"])
        self.assertEqual([uri], saved["plugin"])
        self.assertEqual("deny", next(iter(saved["agent"].values()))["permission"]["bash"])

    def test_required_hook_is_part_of_transport_configuration_identity(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / ".git").mkdir()
            self.assertIn(HOOK.resolve(), opencode.configuration_inputs(root))

    def test_progress_alone_is_not_a_report_or_completed_turn(self):
        row = {"type": "autocode_progress", "version": 1, "sessionID": "session",
               "progress": {"id": "part", "kind": "text", "position": 100, "nonwhite": True,
                            "content_hash": "a" * 64, "delta_hash": "b" * 64},
               "text": '{"verdict":"PASS"}'}
        normalized = opencode.normalized_events([row])
        self.assertFalse(any(r["type"] == "turn.completed" for r in normalized))
        self.assertFalse(any(r.get("item", {}).get("type") == "command_execution" for r in normalized))
        with tempfile.TemporaryDirectory() as temp:
            events = Path(temp) / "events.jsonl"
            events.write_text(json.dumps(row) + "\n")
            with self.assertRaisesRegex(RuntimeError, "no successful terminal step"):
                opencode.final_report(events)
            terminal = {"type": "step_finish", "sessionID": "session", "part": {
                "id": "finish", "messageID": "message", "reason": "stop"}}
            events.write_text(json.dumps(row) + "\n" + json.dumps(terminal) + "\n")
            with self.assertRaisesRegex(RuntimeError, "not a JSON report"):
                opencode.final_report(events)
