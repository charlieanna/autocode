"""Review decisions require a separate, artifact-bound action and correction note."""

import contextlib
import io
import unittest
from unittest.mock import patch

import autocode_args as args
import autocode_configure as configure


class ReviewArgsTests(unittest.TestCase):
    def parse(self, *flags):
        with contextlib.redirect_stderr(io.StringIO()):
            return args.parse(None, ["--run-dir", "/saved/run", *flags], configure.DEFAULT_ROLE_MODELS)[0]

    def test_rejection_resolves_explicit_environment_token_and_note(self):
        with patch.dict("os.environ", {"AUTOCODE_REVIEW_TOKEN": "private-token"}):
            parsed = self.parse("--reject-review", "C1", "--review-token", "-", "--review-note", "Fix the message")
        self.assertEqual("C1", parsed.reject_review)
        self.assertEqual("private-token", parsed.review_token)
        self.assertEqual("Fix the message", parsed.review_note)

    def test_rejection_requires_its_own_nonempty_note_and_token(self):
        for flags in (
            ("--reject-review", "C1", "--review-token", "token"),
            ("--reject-review", "C1", "--review-note", "Fix it"),
            ("--reject-review", "C1", "--review-token", "token", "--review-note", "  "),
            ("--review-note", "Fix it"),
            ("--approve-review", "C1", "--review-token", "token", "--review-note", "Fix it"),
            ("--approve-review", "C1", "--reject-review", "C1", "--review-token", "token", "--review-note", "Fix it"),
        ):
            with self.subTest(flags=flags), self.assertRaises(SystemExit) as error:
                self.parse(*flags)
            self.assertEqual(2, error.exception.code)
