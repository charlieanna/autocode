"""The gemini provider's translator and sign-in checks, from Gemini CLI's own output.

``fixtures/gemini/stream-0.71.1.jsonl`` is what Gemini CLI 0.71.1 printed for a scripted stage (its
``--fake-responses-non-strict`` option, under the read-only policy ``providers.gemini`` writes): a
shell command that exits 3, a ``git commit`` the policy denies, a ``write_file`` the read-only policy
removes, then the report in two pieces. A stand-in ``gemini`` replays such rows, so each test runs
the translator as a stage does, as its own process.
"""

import json
import os
import subprocess
import sys
import tempfile
import types
import unittest
from pathlib import Path

from providers import gemini, gemini_stream

FIXTURE = Path(__file__).parent / "fixtures" / "gemini" / "stream-0.71.1.jsonl"
REPLAY = "import sys; sys.stdout.write(open(sys.argv[1]).read()); sys.stderr.write(sys.argv[2]); raise SystemExit(int(sys.argv[3]))"


class TranslatorTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        self.report = self.root / "tester-01.json"

    def stage(self, rows, *, stderr="", code=0):
        """The translator's Codex rows, its exit code and the report file it left, for these Gemini rows."""
        stream = self.root / "gemini.jsonl"
        stream.write_text(rows if isinstance(rows, str) else "".join(json.dumps(row) + "\n" for row in rows))
        command = [sys.executable, "-c", REPLAY, str(stream), stderr, str(code), "--output-format", "stream-json"]
        done = subprocess.run(
            [
                sys.executable,
                "-I",
                gemini_stream.__file__,
                "--raw",
                str(self.root / "raw.jsonl"),
                "--report",
                str(self.report),
                "--",
                *command,
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.assertEqual("", done.stderr)
        events = [json.loads(line) for line in done.stdout.splitlines()]
        return events, done.returncode, (self.report.read_text() if self.report.is_file() else None)

    def items(self, events, kind):
        return [row["item"] for row in events if row["type"] == "item.completed" and row["item"]["type"] == kind]

    def test_a_real_gemini_stage_becomes_codex_rows_and_its_report(self):
        events, code, report = self.stage(FIXTURE.read_text())
        self.assertEqual(0, code)
        self.assertEqual({"type": "thread.started", "thread_id": "ee6bef88-43d6-4502-acbd-43bf1195cbac"}, events[0])
        probe, denied = self.items(events, "command_execution")
        # Gemini CLI reports no exit code: the command that exits 3 is not read as a pass.
        self.assertEqual(("echo hello && exit 3", None), (probe["command"], probe["exit_code"]))
        self.assertEqual(("git commit -m x", "failed"), (denied["command"], denied["status"]))
        self.assertIn("denied by policy", denied["aggregated_output"])
        self.assertEqual(["failed"], [item["status"] for item in self.items(events, "file_change")])
        self.assertEqual('{"verdict":"pass"}', report.strip())
        final = events[-1]
        self.assertEqual("turn.completed", final["type"])
        self.assertEqual(
            {"input_tokens": 490, "cached_input_tokens": 40, "output_tokens": 27},
            {key: final["usage"][key] for key in ("input_tokens", "cached_input_tokens", "output_tokens")},
        )
        path = self.root / "events.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in events))
        self.assertEqual({"verdict": "pass"}, gemini.final_report(path))
        self.assertTrue((self.root / "raw.jsonl").read_text().startswith('{"type":"init"'))

    def rows(self, answer):
        return [
            {"type": "init", "session_id": "s1", "model": "gemini-2.5-pro"},
            {"type": "message", "role": "assistant", "content": answer, "delta": True},
            {"type": "result", "status": "success", "stats": {"input_tokens": 5, "output_tokens": 2}},
        ]

    def test_a_fenced_report_is_the_report(self):
        events, _code, report = self.stage(self.rows('```json\n{"verdict": "pass"}\n```'))
        self.assertEqual(
            ['{"verdict":"pass"}'],
            [item["text"] for item in self.items(events, "agent_message") if item["id"] == gemini_stream.REPORT_ITEM],
        )
        self.assertEqual('{"verdict":"pass"}', report.strip())

    def test_prose_is_kept_for_format_correction_not_admitted(self):
        events, code, report = self.stage(self.rows("All checks pass."))
        self.assertEqual((0, "All checks pass."), (code, report.strip()))
        path = self.root / "events.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in events))
        with self.assertRaisesRegex(RuntimeError, "JSON"):
            gemini.final_report(path)

    def test_no_login_is_an_authentication_failure_with_no_report(self):
        said = "Please set an Auth method in your /home/x/.gemini/settings.json or specify one of the following"
        events, code, report = self.stage([], stderr=said, code=41)
        self.assertEqual((41, None), (code, report))
        self.assertEqual(401, events[-2]["error"]["data"]["statusCode"])
        self.assertEqual("authentication_failed", events[-1]["error"]["code"])
        self.assertIn("Sign in with Google", events[-1]["error"]["message"])

    def test_a_workspace_account_is_told_the_project_line(self):
        said = "Failed to login. Message: This account requires setting the GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_PROJECT_ID env var."
        events, _code, _report = self.stage([], stderr=said, code=1)
        self.assertEqual("authentication_failed", events[-1]["error"]["code"])
        self.assertIn('export GOOGLE_CLOUD_PROJECT="your-project-id"', events[-1]["error"]["message"])

    def test_quota_rate_limit_and_cut_off_streams_keep_their_meaning(self):
        for message, expected in (
            ("You have exhausted your daily quota (RESOURCE_EXHAUSTED)", "usage_limit_reached"),
            ("got status: 429 Too Many Requests", "rate_limit_exceeded"),
            ("Internal error", "provider_error"),
        ):
            with self.subTest(expected):
                rows = [
                    self.rows("")[0],
                    {"type": "result", "status": "error", "error": {"type": "Error", "message": message}},
                ]
                events, code, report = self.stage(rows, code=1)
                self.assertEqual((expected, None), (events[-1]["error"]["code"], report))
                self.assertNotEqual(0, code)
                self.assertNotIn("RESOURCE_EXHAUSTED", events[-1]["error"]["message"])
        events, code, report = self.stage(self.rows("partial")[:2], code=0)
        self.assertEqual(("incomplete_turn", None), (events[-1]["error"]["code"], report))


class SignInTests(unittest.TestCase):
    """``login_check`` with doctor's injected runner, and what Gemini CLI's own debug log says about a refresh."""

    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.home = Path(folder.name)
        (self.home / ".gemini").mkdir()

    def sign_in(self, kind="oauth-personal", creds=True):
        (self.home / ".gemini" / "settings.json").write_text(json.dumps({"security": {"auth": {"selectedType": kind}}}))
        if creds:
            (self.home / ".gemini" / "oauth_creds.json").write_text("{}")

    def run_gemini(self, command):
        if command[1:] == ["--version"]:
            return types.SimpleNamespace(returncode=0, stdout="0.71.1\n", stderr="")
        return types.SimpleNamespace(
            returncode=0, stdout=json.dumps([{"modelId": "auto"}, {"modelId": "gemini-2.5-pro"}]), stderr=""
        )

    def check(self):
        return gemini.login_check(env={"GEMINI_CLI_HOME": str(self.home), "PATH": os.defpath}, run=self.run_gemini)

    def test_a_google_sign_in_is_ready(self):
        self.sign_in()
        self.assertEqual(("0.71.1", "oauth-personal"), (self.check()["version"], self.check()["authType"]))

    def test_each_unusable_sign_in_says_what_to_do(self):
        cases = (
            (lambda: None, "not signed in"),
            (lambda: self.sign_in("gemini-api-key"), "bills per token"),
            (lambda: self.sign_in(creds=False), "no saved Google sign-in"),
        )
        for prepare, said in cases:
            with self.subTest(said):
                for name in ("settings.json", "oauth_creds.json"):
                    (self.home / ".gemini" / name).unlink(missing_ok=True)
                prepare()
                with self.assertRaisesRegex(RuntimeError, said):
                    self.check()

    def test_the_debug_log_names_an_expired_sign_in_and_a_workspace_account(self):
        self.assertIsNone(gemini.refresh_failure("[DEBUG] Loaded 4 models"))
        self.assertIn("sign in again", gemini.refresh_failure("Cached credentials are not valid: invalid_grant"))
        said = gemini.refresh_failure(
            "Failed to refresh auth for models list, falling back to default catalog: ProjectIdRequiredError: "
            "This account requires setting the GOOGLE_CLOUD_PROJECT or GOOGLE_CLOUD_PROJECT_ID env var."
        )
        self.assertIn('export GOOGLE_CLOUD_PROJECT="your-project-id"', said)


if __name__ == "__main__":
    unittest.main()
