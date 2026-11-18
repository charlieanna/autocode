"""Failed Git observations never grant authority to initialize a workspace."""

from __future__ import annotations

import subprocess
import unittest
from pathlib import Path
from unittest import mock

import autocode_git_location as location


class LocationTests(unittest.TestCase):
    def test_empty_success_is_not_a_checkout_or_permission_to_initialize(self):
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(["git"], 0, "", "")):
            with self.assertRaisesRegex(ValueError, "Cannot inspect Git"):
                location.root("/project")

    def test_an_unreadable_metadata_entry_cannot_be_treated_as_absent(self):
        result = subprocess.CompletedProcess(
            ["git"], 128, "", "fatal: not a git repository (or any of the parent directories): .git\n"
        )
        with mock.patch.object(subprocess, "run", return_value=result):
            with mock.patch.object(Path, "lstat", side_effect=PermissionError("metadata is unreadable")):
                with self.assertRaisesRegex(ValueError, "metadata is unreadable"):
                    location.root("/project")

    def test_missing_refs_are_distinct_from_failed_queries(self):
        for code, expected in ((0, True), (1, False)):
            with self.subTest(code=code):
                with mock.patch.object(
                    subprocess, "run", return_value=subprocess.CompletedProcess(["git"], code, "", "")
                ):
                    self.assertIs(expected, location.has_ref("/project", "HEAD"))
        with mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess(["git"], 128, "", "")):
            with self.assertRaisesRegex(ValueError, "Git exited with status 128"):
                location.has_ref("/project", "HEAD")
