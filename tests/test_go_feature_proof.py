"""A Go feature whose new tests do not build on base proves only those tests (#2505)."""

import shutil
import unittest

import autocode_go_tests as go_tests
import autocode_verify as verify

from tests.test_verify import Project

GO_MOD = "module shop\n\ngo 1.21\n"
CART = "package cart\n\nfunc Total(prices []int) int {\n\tsum := 0\n\tfor _, p := range prices {\n\t\tsum += p\n\t}\n\treturn sum\n}\n"
CART_TEST = (
    'package cart\n\nimport "testing"\n\nfunc TestTotal(t *testing.T) {\n'
    "\tif Total([]int{1, 2}) != 3 {\n\t\tt.Fatal(Total([]int{1, 2}))\n\t}\n}\n"
)
DISCOUNT = "package cart\n\ntype Discount struct{ Percent int }\n\nfunc (d Discount) Apply(total int) int {\n\treturn total - total*d.Percent/100\n}\n"
DISCOUNT_TEST = (
    "\nfunc TestDiscount(t *testing.T) {\n"
    "\tif (Discount{Percent: 10}).Apply(200) != 180 {\n\t\tt.Fatal(\"discount\")\n\t}\n}\n"
)


class DefinitionTests(unittest.TestCase):
    def test_only_added_or_edited_functions_are_changed(self):
        after = CART_TEST + DISCOUNT_TEST
        names = go_tests.changed_names(
            [("cart/cart_test.go", CART_TEST, after)], lambda directory: ("shop", "")
        )
        self.assertEqual({("shop/cart", "TestDiscount")}, names)
        self.assertTrue(go_tests.defined_in(names, "shop/cart::TestDiscount/sub"))
        self.assertFalse(go_tests.defined_in(names, "shop/cart::TestTotal"))
        edited = CART_TEST.replace("!= 3", "!= 4")
        self.assertEqual(
            {("shop/cart", "TestTotal")},
            go_tests.changed_names([("cart/cart_test.go", CART_TEST, edited)], lambda directory: ("shop", "")),
        )

    def test_a_nested_module_and_its_root_package_are_named_by_import_path(self):
        modules = lambda directory: ("example.com/svc", "svc")  # noqa: E731
        self.assertEqual(
            {("example.com/svc", "TestA"), ("example.com/svc/api", "TestB")},
            go_tests.changed_names(
                [
                    ("svc/a_test.go", None, "package svc\nfunc TestA(t *testing.T) {\n}\n"),
                    ("svc/api/b_test.go", None, "package api\nfunc TestB(t *testing.T) {\n}\n"),
                ],
                modules,
            ),
        )

    def test_an_unnamed_package_keeps_every_test(self):
        self.assertIsNone(go_tests.changed_names([("x_test.go", None, "")], lambda directory: None))


@unittest.skipUnless(shutil.which("go"), "Go is required for native suite proof")
class GoFeatureProofTests(unittest.TestCase):
    def prove(self, files):
        project = Project({"go.mod": GO_MOD, "cart/cart.go": CART, "cart/cart_test.go": CART_TEST})
        self.addCleanup(project.close)
        project.write(files)
        framework = verify.detect_framework(project.root)
        base = verify.baseline(
            project.root, project.base, project.evidence, framework=framework, suite_command=framework.suite, timeout=60
        )
        return verify.verify(
            project.root,
            project.base,
            project.evidence,
            framework=framework,
            suite_command=framework.suite,
            base_suite=base,
            new_behavior=True,
            timeout=60,
        )

    def test_a_new_test_file_proves_only_its_tests_and_keeps_the_existing_ones(self):
        result = self.prove(
            {"cart/discount.go": DISCOUNT, "cart/discount_test.go": 'package cart\n\nimport "testing"\n' + DISCOUNT_TEST}
        )
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["shop/cart::TestDiscount"], result["fail_to_pass"])
        self.assertIn("shop/cart::TestTotal", result["pass_to_pass"])
        self.assertNotIn("shop/cart::TestTotal", result["not_run_on_base"])

    def test_a_test_added_to_an_existing_file_is_the_only_proof(self):
        result = self.prove({"cart/discount.go": DISCOUNT, "cart/cart_test.go": CART_TEST + DISCOUNT_TEST})
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual(["shop/cart::TestDiscount"], result["fail_to_pass"])
        self.assertIn("shop/cart::TestTotal", result["pass_to_pass"])

    def test_a_change_that_only_reuses_existing_tests_shows_no_new_behavior(self):
        # The new test file adds a test that depends on the new type, but the proof must come from it, not
        # from the existing tests its build failure kept off the base.
        result = self.prove(
            {
                "cart/discount.go": DISCOUNT,
                "cart/discount_test.go": 'package cart\n\nvar _ = Discount{}\n',
            }
        )
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertEqual([], result["fail_to_pass"])


if __name__ == "__main__":
    unittest.main()
