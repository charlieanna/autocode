"""A Go workspace of several modules has a suite (autocode_toolchain_suite.go_workspace).

A repository whose root holds a ``go.work`` and no ``go.mod`` (``use ./app`` and ``use ./lib``, each with its
own ``go.mod``) had no detected suite: only a root ``go.mod`` named a Go project, and ``go test ./...`` at
such a root names no package ("directory prefix . does not contain modules listed in go.work"). A proof
there had no command, which ends a run by asking the person for ``--test-command`` (2026-11-14). The
detection cases are pure; the proof cases run ``go test`` in a Git project, with no provider.
"""

from __future__ import annotations

import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_toolchain_suite as toolchain_suite
import autocode_verify as verify

from .test_verify import Project

WORK = "go 1.21\n\nuse (\n\t./app\n\t./lib\n)\n"
LIB_MOD = "module example.com/shop/lib\n\ngo 1.21\n"
APP_MOD = "module example.com/shop/app\n\ngo 1.21\n\nrequire example.com/shop/lib v0.0.0\n"
PRICE = (
    "// Package lib holds the shop's price arithmetic.\npackage lib\n\n"
    "// Discounted is price in cents after percent off.\n"
    "func Discounted(price, percent int) int {\n\treturn price - percent\n}\n"
)
PRICE_FIXED = PRICE.replace("price - percent", "price - price*percent/100")
PRICE_TEST = (
    'package lib\n\nimport "testing"\n\n'
    "func TestNoDiscount(t *testing.T) {\n\tif got := Discounted(500, 0); got != 500 {\n"
    '\t\tt.Fatalf("Discounted(500, 0) = %d", got)\n\t}\n}\n'
)
PRICE_REGRESSION = PRICE_TEST + (
    "\nfunc TestTenPercent(t *testing.T) {\n\tif got := Discounted(500, 10); got != 450 {\n"
    '\t\tt.Fatalf("Discounted(500, 10) = %d", got)\n\t}\n}\n'
)
RECEIPT = (
    '// Package app prints receipts.\npackage app\n\nimport (\n\t"fmt"\n\n\t"example.com/shop/lib"\n)\n\n'
    "// Line is one receipt line.\nfunc Line(name string, price, percent int) string {\n"
    '\treturn fmt.Sprintf("%s: %d", name, lib.Discounted(price, percent))\n}\n'
)
RECEIPT_TEST = (
    'package app\n\nimport "testing"\n\n'
    'func TestLineWithoutDiscount(t *testing.T) {\n\tif got := Line("pen", 500, 0); got != "pen: 500" {\n'
    '\t\tt.Fatalf("Line = %q", got)\n\t}\n}\n'
)
RECEIPT_REGRESSION = RECEIPT_TEST + (
    '\nfunc TestLineWithDiscount(t *testing.T) {\n\tif got := Line("pen", 500, 10); got != "pen: 450" {\n'
    '\t\tt.Fatalf("Line = %q", got)\n\t}\n}\n'
)
SEED = {
    "go.work": WORK,
    "lib/go.mod": LIB_MOD,
    "lib/price.go": PRICE,
    "lib/price_test.go": PRICE_TEST,
    "app/go.mod": APP_MOD,
    "app/receipt.go": RECEIPT,
    "app/receipt_test.go": RECEIPT_TEST,
}


def detect(files):
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        for name, text in files.items():
            (root / name).parent.mkdir(parents=True, exist_ok=True)
            (root / name).write_text(text)
        return toolchain_suite.detect(root, sorted(files), verify.is_test_path)


class DetectionTests(unittest.TestCase):
    def test_each_module_the_workspace_uses_is_tested(self):
        framework = detect(SEED)
        self.assertEqual(("go", "go test ./app/... ./lib/..."), (framework.name, framework.suite))
        self.assertTrue(framework.per_test)
        self.assertEqual("go test ./lib", framework.targeted(["lib/price_test.go"]))

    def test_single_line_use_directives_and_comments_are_read(self):
        work = 'go 1.21\n\n// the modules\nuse ./lib // arithmetic\nuse "./app"\n\nreplace example.com/x => ./x\n'
        self.assertEqual("go test ./lib/... ./app/...", detect({**SEED, "go.work": work}).suite)

    def test_a_directory_with_no_module_or_outside_the_repository_is_left_out(self):
        work = "go 1.21\n\nuse (\n\t./lib\n\t./tools\n\t../shared\n\t/opt/modules/x\n\t./lib\n)\n"
        self.assertEqual("go test ./lib/...", detect({**SEED, "go.work": work}).suite)

    def test_a_root_module_keeps_its_own_suite(self):
        files = {
            **SEED,
            "go.mod": "module example.com/shop\n\ngo 1.21\n",
            "go.work": "go 1.21\n\nuse (\n\t.\n\t./lib\n)\n",
        }
        self.assertEqual("go test ./...", detect(files).suite)

    def test_a_workspace_that_uses_no_module_here_names_no_suite(self):
        self.assertIsNone(detect({"go.work": "go 1.21\n\nuse ../elsewhere\n", "README.md": "notes\n"}))
        self.assertIsNone(detect({"go.work": "go 1.21\n", "README.md": "notes\n"}))


@unittest.skipUnless(shutil.which("go"), "Go is required for native suite proof")
class ProofTests(unittest.TestCase):
    def project(self):
        project = Project(SEED)
        self.addCleanup(project.close)
        return project

    def test_a_fix_in_one_module_with_tests_in_both_is_proved(self):
        project = self.project()
        project.write(
            {
                "lib/price.go": PRICE_FIXED,
                "lib/price_test.go": PRICE_REGRESSION,
                "app/receipt_test.go": RECEIPT_REGRESSION,
            }
        )
        result = project.verify()
        self.assertEqual(verify.PASS, result["verdict"], result)
        self.assertEqual("go test ./app/... ./lib/...", result["commands"]["suite"])
        self.assertEqual(
            ["example.com/shop/app::TestLineWithDiscount", "example.com/shop/lib::TestTenPercent"],
            result["fail_to_pass"],
        )
        self.assertEqual(
            ["example.com/shop/app::TestLineWithoutDiscount", "example.com/shop/lib::TestNoDiscount"],
            result["pass_to_pass"],
        )

    def test_a_change_that_breaks_the_other_modules_test_is_refused(self):
        project = self.project()
        project.write(
            {
                "lib/price.go": PRICE.replace("price - percent", "price - price*percent/100 - 1"),
                "lib/price_test.go": PRICE_TEST.replace(
                    "Discounted(500, 0); got != 500", "Discounted(500, 0); got != 499"
                ),
            }
        )
        result = project.verify()
        self.assertEqual(verify.FAIL, result["verdict"], result)
        self.assertIn("example.com/shop/app::TestLineWithoutDiscount", " ".join(result["failures"]))


if __name__ == "__main__":
    unittest.main()
