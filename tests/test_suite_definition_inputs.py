"""Pure controls for the retained source inputs of a suite-definition proof."""

import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_suite_definition_inputs as inputs


class DefinitionInputTests(unittest.TestCase):
    def retained(self, files, links, pinned):
        entries = {name: ("100644", "object") for name in files}
        entries.update({name: ("120000", "object") for name in links})
        return inputs.linked_inputs(entries, links.__getitem__, set(pinned))

    def test_chained_and_directory_links_retain_their_targets(self):
        retained = self.retained(
            {"config/npmrc", "config/nested/settings", "src/app.js"},
            {".npmrc": "alias/npmrc", "alias": "config"},
            {".npmrc"},
        )
        self.assertEqual({".npmrc", "alias/npmrc", "alias", "config/npmrc"}, retained)
        self.assertEqual(
            {".yarn", "config", "config/npmrc", "config/nested/settings"},
            self.retained({"config/npmrc", "config/nested/settings"}, {".yarn": "config"}, {".yarn"}),
        )

    def test_missing_target_remains_absent_in_the_definition(self):
        self.assertEqual({".npmrc", "config/new"}, self.retained(set(), {".npmrc": "config/new"}, {".npmrc"}))

    def test_external_and_cyclic_links_cannot_prove_a_bound_definition(self):
        for links in ({".npmrc": "../outside"}, {".npmrc": "/tmp/outside"}, {".npmrc": "config", "config": ".npmrc"}):
            with self.subTest(links=links), self.assertRaises(ValueError):
                self.retained(set(), links, {".npmrc"})

    def test_a_parent_replacement_cannot_remove_a_retained_target(self):
        with self.assertRaisesRegex(ValueError, "replaces a directory"):
            inputs.check_parents({"config": "added"}, {"config/npmrc"})
        inputs.check_parents({"config/other": "modified"}, {"config/npmrc"})

    def test_reviewed_paths_include_reversions_and_deleted_patch_additions(self):
        with tempfile.TemporaryDirectory() as temporary:
            Path(temporary, "reverted.js").write_text("base version\n")
            measured = inputs.reviewed_changes(temporary, {"new.js": "added"}, {"reverted.js", "removed.js"})
        self.assertEqual({"new.js": "added", "reverted.js": "modified", "removed.js": "deleted"}, measured)

    def test_candidate_controlled_temp_directory_is_refused_and_cleaned_up(self):
        with tempfile.TemporaryDirectory() as temporary:
            with mock.patch.object(inputs.tempfile, "tempdir", temporary):
                with self.assertRaisesRegex(ValueError, "inside the candidate"):
                    with inputs.isolated_folder(temporary):
                        self.fail("an in-project proof folder must never be yielded")
            self.assertEqual([], list(Path(temporary).iterdir()))

    def test_isolated_folder_is_outside_the_project_and_removed_after_use(self):
        with tempfile.TemporaryDirectory() as temporary:
            with inputs.isolated_folder(temporary) as folder:
                self.assertTrue(folder.is_dir())
                self.assertFalse(folder.is_relative_to(Path(temporary).resolve()))
                (folder / "owned-output").write_text("proof scratch\n")
            self.assertFalse(folder.exists())
