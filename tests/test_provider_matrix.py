"""The published provider conformance matrix is generated and honest (#706)."""

from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))
import provider_matrix


class ProviderMatrixTests(unittest.TestCase):
    def test_the_matrix_lists_the_sweeps_versions(self):
        rows = provider_matrix.load_rows()
        versions = {(r["engine"], r["version"]) for r in rows}
        self.assertIn(("opencode", "1.18.33"), versions)
        self.assertIn(("opencode", "2.0.20"), versions)
        self.assertIn(("codex", "native"), versions)

    def test_a_row_names_its_commit_and_date(self):
        for row in provider_matrix.load_rows():
            with self.subTest(row=row["version"]):
                self.assertRegex(row["commit"], r"^[0-9a-f]{7,40}$")
                self.assertRegex(row["date"], r"^\d{4}-\d{2}-\d{2}$")
                self.assertTrue(row["note"].strip())

    def test_the_generated_table_is_in_providers_md(self):
        text = (TOOLS.parent / "docs" / "providers.md").read_text()
        self.assertIn(provider_matrix.BEGIN, text)
        self.assertIn(provider_matrix.END, text)
        self.assertIn("1.18.33", text)
        self.assertIn("do not edit by hand", text.lower())
        self.assertIn(provider_matrix.markdown(provider_matrix.load_rows()), text)

    def test_status_for_reports_untested_honestly(self):
        self.assertIsNotNone(provider_matrix.status_for("opencode", "1.18.33"))
        self.assertIsNone(
            provider_matrix.status_for("opencode", "9.9.9"), "an untested version must not be reported as known-good"
        )

    def test_regenerating_the_table_is_stable(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "providers.md"
            path.write_text((TOOLS.parent / "docs" / "providers.md").read_text())
            block = provider_matrix.write_table(path)
            generated = path.read_bytes()
            self.assertEqual(block, provider_matrix.write_table(path))
            self.assertEqual(generated, path.read_bytes())

    def test_explicit_matrix_path_controls_the_generated_rows(self):
        row = dict(provider_matrix.load_rows()[0], engine="fixture", version="custom")
        with tempfile.TemporaryDirectory() as temporary:
            matrix = Path(temporary) / "custom.json"
            matrix.write_text(json.dumps({"schema_version": 1, "rows": [row]}))
            self.assertEqual([row], provider_matrix.load_rows(matrix))
            document = Path(temporary) / "providers.md"
            document.write_text("# Fixture providers\n")
            block = provider_matrix.write_table(document, matrix)
            self.assertIn("| fixture | custom |", block)
            self.assertNotIn("| opencode | 1.18.33 |", block)
            self.assertTrue(document.read_text().startswith("# Fixture providers\n"))
            self.assertIn(block, document.read_text())


if __name__ == "__main__":
    unittest.main()
