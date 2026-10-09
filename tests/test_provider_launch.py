"""Unsupported engines must not silently dispatch through Codex."""
import unittest
from pathlib import Path

import autocode_provider_launch as launch


class ProviderLaunchTests(unittest.TestCase):
    def test_retired_or_unknown_engine_cannot_fall_back_to_codex(self):
        for engine in ("gocode", "unknown"):
            with self.subTest(engine=engine), self.assertRaisesRegex(
                    RuntimeError, "providers live in .*--provider"):
                launch.prepare(engine=engine, adapter=None, role="sol", route_role="sol",
                    workspace=Path("/workspace"), run_dir=Path("/run"), session=None,
                    model="example/model", effort="high", allow_write=False, planning=True,
                    report=Path("/report"), schema=Path("/schema"), prompt_file=Path("/prompt"),
                    sandbox="read-only", transport_args=[], chatgpt=False, provider=None)
