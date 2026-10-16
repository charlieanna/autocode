"""Exercise installed native resources outside the checkout; never dispatch a model."""

import hashlib
import json
import shutil
import subprocess
import unittest
from pathlib import Path

import autocode_cli


class InstalledNativeResourceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = Path(autocode_cli.__file__).parent
        cls.node = shutil.which("node")
        if cls.node is None:
            raise RuntimeError("The native resource wheel smoke requires Node.js")

    def test_installed_kilo_decoder_accepts_high_source_and_refuses_a_different_model(self):
        body = {
            "model": "claude-opus-5-5",
            "stream": True,
            "max_tokens": 128,
            "output_config": {"effort": "high"},
            "thinking": {"type": "adaptive"},
            "messages": [{"role": "user", "content": [{"type": "text", "text": "Installed decoder control."}]}],
        }
        raw = json.dumps(body).encode()
        command = [self.node, str(self.root / "autocode_kilo_image_delivery.mjs"), "source-request"]
        result = subprocess.run(command, input=raw, capture_output=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr.decode())
        parsed = json.loads(result.stdout)
        self.assertTrue(parsed["payload_complete"])
        self.assertEqual(hashlib.sha256(raw).hexdigest(), parsed["body_sha256"])
        self.assertEqual([], parsed["images"])
        body["model"] = "unapproved-model"
        refused = subprocess.run(command, input=json.dumps(body).encode(), capture_output=True, timeout=15)
        self.assertNotEqual(0, refused.returncode)
        self.assertIn(b"wrong_messages_model_or_stream", refused.stderr)

    def test_installed_deriver_and_linux_capture_modules_load(self):
        deriver = (self.root / "autocode_kilo_request_derivation.mjs").as_uri()
        code = "const m=await import(process.argv[1]);if(m.MODEL!=='claude-opus-5-5'||m.SDK_VERSION!=='3.0.111')process.exit(2)"
        result = subprocess.run(
            [self.node, "--input-type=module", "-e", code, deriver], capture_output=True, timeout=15
        )
        self.assertEqual(0, result.returncode, result.stderr.decode())
        capture = self.root / "autocode_native_capture_linux.cjs"
        code = "const m=require(process.argv[1]);if(typeof m.command!=='function'||m.TRANSPORT!=='linux-bwrap-inherited-cdp-v1')process.exit(2)"
        result = subprocess.run([self.node, "-e", code, str(capture)], capture_output=True, timeout=15)
        self.assertEqual(0, result.returncode, result.stderr.decode())
        # The CLI entry imports its sibling deriver; a syntax check also proves
        # the installed executable resource can be opened by Node.
        result = subprocess.run(
            [self.node, "--check", str(self.root / "autocode_kilo_request_derivation_cli.mjs")],
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(0, result.returncode, result.stderr.decode())
        license_text = (self.root / "vendor/fflate-gzip-sync/LICENSE").read_text()
        self.assertIn("Permission is hereby granted", license_text)


if __name__ == "__main__":
    unittest.main()
