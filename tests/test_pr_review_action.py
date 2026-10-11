"""The companion Action invokes trusted installed tooling and never posts."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("review_action", ROOT / "examples/github-review/review_action.py")
ACTION = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(ACTION)


class ReviewActionTests(unittest.TestCase):
    def test_installed_cli_receives_url_as_one_argument_and_no_post(self):
        with tempfile.TemporaryDirectory() as tmp:
            installed = Path(tmp) / "autocode"
            installed.write_text("trusted fixture executable")
            calls = []
            env = {
                "AUTOCODE_REVIEW_CLI": str(installed),
                "AUTOCODE_REVIEW_OUT": str(Path(tmp) / "new-output"),
                "AUTOCODE_PR_URL": "https://github.com/a/b/pull/1#quoted;echo-injection",
                "AUTOCODE_REVIEW_OPTIONS": json.dumps(["--provider", "opencode"]),
                "GITHUB_TOKEN": "offline-token",
            }
            result = ACTION.main(
                env, run=lambda command, **kw: calls.append((command, kw)) or SimpleNamespace(returncode=7)
            )
            self.assertEqual(7, result)
            command, kwargs = calls[0]
            self.assertEqual(str(installed), command[0])
            self.assertEqual(env["AUTOCODE_PR_URL"], command[2])
            self.assertNotIn("--post", command)
            self.assertEqual(["--", "--provider", "opencode"], command[-3:])
            self.assertFalse(kwargs["check"])
            self.assertEqual(env, kwargs["env"])

    def test_relative_tooling_and_non_string_options_are_refused_before_launch(self):
        calls = []
        with self.assertRaises(ValueError):
            ACTION.main({"AUTOCODE_REVIEW_CLI": "./autocode"}, run=lambda *a, **k: calls.append(a))
        with tempfile.TemporaryDirectory() as tmp:
            installed = Path(tmp) / "autocode"
            installed.write_text("fixture")
            for options in ({"--post": True}, [False], [None]):
                with self.subTest(options=options), self.assertRaises(ValueError):
                    ACTION.main(
                        {"AUTOCODE_REVIEW_CLI": str(installed), "AUTOCODE_REVIEW_OPTIONS": json.dumps(options)},
                        run=lambda *a, **k: calls.append(a),
                    )
        self.assertEqual([], calls)
