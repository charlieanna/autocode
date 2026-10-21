"""Real Kilo display hook, without provider traffic or timing waits."""

import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from providers import kilocode


class RequirementsStreamTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("node"), "Node executes the Kilo hook")
    def test_native_deltas_display_only_assistant_reply_and_ignore_foreign_or_reasoning_parts(self):
        hook = Path(kilocode.__file__).with_name("requirements_stream.mjs").as_uri()
        script = r"""
import {requirementsStream} from HOOK;
const h = await requirementsStream();
await h['chat.message']({sessionID:'current'});
const update = (id, type, sessionID='current', messageID='answer') => h.event({event:{type:'message.part.updated',properties:{part:{id,type,sessionID,messageID}}}});
const delta = (partID, delta, sessionID='current') => h.event({event:{type:'message.part.delta',properties:{partID,delta,sessionID,field:'text'}}});
await update('reason','reasoning'); await delta('reason','private reasoning');
await update('tool','tool'); await delta('tool','private tool output');
await update('outside','text','foreign'); await delta('outside','foreign','foreign');
await update('user','text','current','user'); await delta('user','{"assistant_reply":"private prompt example"}');
await h.event({event:{type:'message.updated',properties:{info:{id:'answer',sessionID:'current',role:'assistant'}}}});
await update('text','text');
await delta('text','{"summary":"private structured field","assistant_reply":"What');
await delta('text',' would you like?","constraints":["private constraint"]}');
""".replace("HOOK", json.dumps(hook))
        result = subprocess.run(
            [shutil.which("node"), "--input-type=module", "-e", script],
            capture_output=True,
            text=True,
            check=True,
            timeout=15,
        )
        rows = [json.loads(line) for line in result.stdout.splitlines()]
        self.assertEqual(["What", "What would you like?"], [row["reply"] for row in rows])
        self.assertTrue(all(row["type"] == "autocode_chat_reply" for row in rows))
        self.assertNotIn("private", result.stdout)

    def test_only_conversational_requirements_launch_adds_the_hook_and_preserves_configuration(self):
        provider = kilocode.load("kilocode")
        with tempfile.TemporaryDirectory() as directory:
            schema = Path(directory) / "schema.json"
            schema.write_text(json.dumps({"properties": {"assistant_reply": {"type": "string"}}}))
            inherited = {"KILO_CONFIG_CONTENT": json.dumps({"plugin": ["file:///existing.mjs"], "model": "retained"})}
            with (
                patch.object(provider, "_preflight_environment", return_value=inherited.copy()),
                patch.object(
                    kilocode.kilocode_policy, "restrict", side_effect=lambda argv, child, *a, **kw: (argv, child)
                ),
            ):
                _, env, _ = provider.launch(
                    "requirements", Path(directory), Path(directory), None, "plan/glm", "medium", False, schema=schema
                )
            config = json.loads(env["KILO_CONFIG_CONTENT"])
            self.assertEqual("retained", config["model"])
            self.assertEqual("file:///existing.mjs", config["plugin"][0])
            self.assertTrue(config["plugin"][1].endswith("opencode_activity.mjs"))
            self.assertTrue(config["plugin"][2].endswith("requirements_stream.mjs"))
            with (
                patch.object(provider, "_preflight_environment", return_value=inherited.copy()),
                patch.object(
                    kilocode.kilocode_policy, "restrict", side_effect=lambda argv, child, *a, **kw: (argv, child)
                ),
            ):
                _, env, _ = provider.launch(
                    "sol", Path(directory), Path(directory), None, "plan/glm", "medium", False, schema=schema
                )
            expected = json.loads(inherited["KILO_CONFIG_CONTENT"])
            expected["plugin"].append(Path(kilocode.__file__).with_name("opencode_activity.mjs").resolve().as_uri())
            self.assertEqual(expected, json.loads(env["KILO_CONFIG_CONTENT"]))
