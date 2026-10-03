"""Edited tests from the base commit are re-run in their original form (GitHub issue #229)."""
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import autocode_test_integrity as integrity
import autocode_util as util
import autocode_verify as verify

PRODUCT = "def shipping(subtotal):\n    return 0 if subtotal >= 5000 else 500\n"
BROKEN = "def shipping(subtotal):\n    return 0 if subtotal > 5000 else 500\n"
TESTS = textwrap.dedent("""\
    import unittest
    from shop import shipping


    class ShippingTests(unittest.TestCase):
        def test_free_at_threshold(self):
            self.assertEqual(0, shipping(5000))

        def test_flat_below_threshold(self):
            self.assertEqual(500, shipping(4999))
    """)


class TestIntegrity(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve() / "project"
        (self.root / "tests").mkdir(parents=True)
        (self.root / "shop.py").write_text(PRODUCT)
        (self.root / "tests" / "__init__.py").write_text("")
        (self.root / "tests" / "test_shipping.py").write_text(TESTS)
        self.git("init", "-q")
        self.git("add", "-A")
        self.git("-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD").strip()
        self.run_dir = self.root.parent / "run"
        self.state = {}

    def git(self, *args):
        return subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True, text=True).stdout

    def edit_tests(self, old, new):
        path = self.root / "tests" / "test_shipping.py"
        text = path.read_text()
        self.assertIn(old, text)
        path.write_text(text.replace(old, new))

    def check(self):
        framework = verify.detect_framework(self.root, python=sys.executable)
        return integrity.check(self.state, self.root, self.run_dir, base=self.base, framework=framework,
                               timeout=60)

    def revision(self):
        return util.snapshot(self.root)["revision"]

    def assert_weakened(self, result):
        self.assertEqual("FAIL", result["verdict"], result)
        self.assertEqual(["tests/test_shipping.py"], list(result["weakened"]))
        self.assertFalse(integrity.complete(self.state, self.revision()))
        self.assertIn("--approve-test-change", integrity.rejection(self.state))

    def test_changed_expected_value_is_weakening(self):
        (self.root / "shop.py").write_text(BROKEN)
        self.edit_tests("assertEqual(0, shipping(5000))", "assertEqual(500, shipping(5000))")
        result = self.check()
        self.assert_weakened(result)
        self.assertIn("test_free_at_threshold", " ".join(result["weakened"]["tests/test_shipping.py"]))

    def test_dropped_assertion_skip_and_unconditional_pass_are_weakening(self):
        variants = [
            ("self.assertEqual(0, shipping(5000))", "shipping(5000)"),
            ("    def test_free_at_threshold(self):",
             "    @unittest.skip('flaky')\n    def test_free_at_threshold(self):"),
            ("self.assertEqual(0, shipping(5000))", "self.assertTrue(True)"),
            ("def test_free_at_threshold(self):", "def free_at_threshold(self):"),
        ]
        for old, new in variants:
            with self.subTest(new=new):
                self.git("checkout", "-q", "--", ".")
                self.state = {}
                (self.root / "shop.py").write_text(BROKEN)
                self.edit_tests(old, new)
                self.assert_weakened(self.check())

    def test_deleted_protected_file_is_weakening(self):
        (self.root / "tests" / "test_shipping.py").unlink()
        result = self.check()
        self.assertEqual({"tests/test_shipping.py": ["the file was deleted"]}, result["weakened"])

    def test_fixing_the_code_and_adding_tests_passes(self):
        (self.root / "shop.py").write_text(PRODUCT + "\n\ndef express(subtotal):\n    return 1500\n")
        self.edit_tests("from shop import shipping", "from shop import express, shipping")
        with (self.root / "tests" / "test_shipping.py").open("a") as handle:
            handle.write("\n    def test_express_is_never_free(self):\n        self.assertEqual(1500, express(9999))\n")
        (self.root / "tests" / "test_express.py").write_text(
            "import unittest\nfrom shop import express\n\n\nclass E(unittest.TestCase):\n"
            "    def test_flat(self):\n        self.assertEqual(1500, express(1))\n")
        result = self.check()
        self.assertEqual("PASS", result["verdict"], result)
        self.assertEqual(["tests/test_shipping.py"], list(result["files"]), "Only files from base are protected")
        self.assertTrue(integrity.complete(self.state, self.revision()))

    def test_rewrite_that_keeps_meaning_passes(self):
        self.edit_tests("self.assertEqual(0, shipping(5000))", "expected = 0\n        self.assertEqual(expected, shipping(5000))")
        self.assertEqual("PASS", self.check()["verdict"])

    def test_broken_code_with_untouched_tests_is_not_this_guards_concern(self):
        (self.root / "shop.py").write_text(BROKEN)
        result = self.check()
        self.assertEqual("PASS", result["verdict"])
        self.assertEqual({}, result["files"])

    def test_approval_binds_the_checked_contents_and_a_later_edit_is_checked_again(self):
        (self.root / "shop.py").write_text(BROKEN)
        self.edit_tests("assertEqual(0, shipping(5000))", "assertEqual(500, shipping(5000))")
        self.check()
        integrity.approve(self.state, "tests/test_shipping.py", self.root, util.now())
        self.assertTrue(integrity.complete(self.state, self.revision()))
        self.assertEqual("test_change_approval", self.state["user_events"][-1]["kind"])
        self.edit_tests("assertEqual(500, shipping(4999))", "assertEqual(0, shipping(4999))")
        result = self.check()
        self.assertEqual(["tests/test_shipping.py"], integrity.outstanding(self.state, result))
        self.assertFalse(integrity.complete(self.state, self.revision()))

    def test_approval_refuses_unchecked_contents_and_unprotected_files(self):
        (self.root / "shop.py").write_text(BROKEN)
        self.edit_tests("assertEqual(0, shipping(5000))", "assertEqual(500, shipping(5000))")
        self.check()
        with self.assertRaisesRegex(ValueError, "not an edited protected test"):
            integrity.approve(self.state, "shop.py", self.root, util.now())
        self.edit_tests("assertEqual(500, shipping(4999))", "assertEqual(0, shipping(4999))")
        with self.assertRaisesRegex(ValueError, "changed after the runner checked it"):
            integrity.approve(self.state, "tests/test_shipping.py", self.root, util.now())
        self.assertNotIn("approvals", self.state["protected_tests"])

    def test_weakening_that_survives_a_rework_is_held_for_the_user(self):
        (self.root / "shop.py").write_text(BROKEN)
        self.edit_tests("assertEqual(0, shipping(5000))", "assertEqual(500, shipping(5000))")
        self.assertIsNone(integrity.hold(self.state, self.check()), "The first finding goes to the Validator")
        self.assertIsNone(integrity.hold(self.state, self.check()), "Re-checking one source is not a repeat")
        (self.root / "shop.py").write_text(BROKEN + "# reworked\n")
        held = integrity.hold(self.state, self.check())
        self.assertEqual(integrity.HOLD, held.status)
        self.assertIn("tests/test_shipping.py", str(held))
        integrity.approve(self.state, "tests/test_shipping.py", self.root, util.now())
        self.assertIsNone(integrity.hold(self.state, self.state["protected_tests"]["latest"]))

    def test_restoring_the_test_and_fixing_the_code_clears_the_gate(self):
        (self.root / "shop.py").write_text(BROKEN)
        self.edit_tests("assertEqual(0, shipping(5000))", "assertEqual(500, shipping(5000))")
        self.check()
        self.git("checkout", "-q", "--", ".")
        result = self.check()
        self.assertEqual("PASS", result["verdict"])
        self.assertIsNone(integrity.hold(self.state, result))
        self.assertTrue(integrity.complete(self.state, self.revision()))

    def test_unknown_base_is_recorded_without_crashing_the_dispatch(self):
        self.base = "0" * 40
        result = self.check()
        self.assertEqual("UNVERIFIED", result["verdict"])
        self.assertIn("could not be compared", result["notes"][0])
        self.assertTrue(integrity.complete(self.state, self.revision()), "Nothing is known to be edited")

    def test_framework_is_detected_only_when_an_edited_file_must_run(self):
        calls = []
        integrity.check(self.state, self.root, self.run_dir, base=self.base, timeout=60,
                        framework=lambda: calls.append(1))
        self.assertEqual([], calls)

    def test_no_check_for_the_current_source_does_not_complete(self):
        (self.root / "shop.py").write_text(BROKEN)
        self.edit_tests("assertEqual(0, shipping(5000))", "assertEqual(500, shipping(5000))")
        self.check()
        self.git("checkout", "-q", "--", ".")
        self.assertFalse(integrity.complete(self.state, self.revision()))
        self.assertTrue(integrity.complete({}, self.revision()), "A run that never checked is not blocked here")


if __name__ == "__main__":
    unittest.main()
