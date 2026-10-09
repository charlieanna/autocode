"""Wheel-smoke checks; run with installed Python -I outside the checkout.

This standalone entry point avoids tests/__init__.py's source-path imports.
The existing CI wheel-smoke job builds and installs the package before running it.
No engines, credentials, network requests or model calls are needed.
"""
from pathlib import Path
import sys
import unittest

from autocode_cli import autocode_doctor as doctor, provider_matrix


class InstalledProviderMatrixTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        prefix = Path(sys.prefix).resolve()
        for module in (doctor, provider_matrix):
            origin = Path(module.__file__).resolve()
            if not origin.is_relative_to(prefix):
                raise AssertionError(f"Expected an installed module under {prefix}, found {origin}")
        checkout = Path(__file__).resolve().parents[1]
        if Path.cwd().resolve().is_relative_to(checkout):
            raise AssertionError("Run the wheel smoke outside the source checkout")

    def test_shipped_known_row_is_available_to_doctor(self):
        row = provider_matrix.status_for("opencode", "1.18.33")
        self.assertIsNotNone(row)
        check = doctor.conformance_check("opencode", "1.18.33")
        self.assertEqual("ok", check.status, check.detail)
        self.assertIn("known-good", check.detail)
        self.assertIn("containment qualified", check.detail)
        self.assertIn("visual qualified", check.detail)

    def test_unknown_version_remains_an_untested_warning(self):
        self.assertIsNone(provider_matrix.status_for("opencode", "9.9.9"))
        check = doctor.conformance_check("opencode", "9.9.9")
        self.assertEqual("warn", check.status, check.detail)
        self.assertIn("9.9.9 is untested", check.detail)
        self.assertNotIn("cannot read", check.detail)

    def test_containment_refusal_still_warns(self):
        check = doctor.conformance_check("opencode", "2.0.20")
        self.assertEqual("warn", check.status, check.detail)
        self.assertIn("accepted-for-ordinary-runs", check.detail)
        self.assertIn("containment refused", check.detail)
        self.assertIn("visual refused", check.detail)


if __name__ == "__main__":
    unittest.main()
