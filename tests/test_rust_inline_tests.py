"""Tests for Rust inline ``#[cfg(test)]`` scanning, base splicing, and diff-coverage exclusion (#3467)."""

from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_cargo_tests as cargo_tests
import autocode_changed_code_measurement as measurement
import autocode_changed_tests as changed_tests
import autocode_plan_test_suite as plan_test_suite
import autocode_regression as regression
import autocode_rust_inline_tests as rust_inline
import autocode_verify as verify

from .test_verify import Project

BASE_LIB = """\
pub fn page_count(total: u32, size: u32) -> u32 {
    total / size
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_multiple() {
        assert_eq!(page_count(10, 5), 2);
    }
}
"""

FIXED_LIB = """\
pub fn page_count(total: u32, size: u32) -> u32 {
    if total == 0 { 0 } else { (total + size - 1) / size }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn exact_multiple() {
        assert_eq!(page_count(10, 5), 2);
    }

    #[test]
    fn test_partial_last_page() {
        assert_eq!(page_count(11, 5), 3);
    }
}
"""

TEST_ONLY_LIB = BASE_LIB.replace(
    "    #[test]\n    fn exact_multiple() {\n        assert_eq!(page_count(10, 5), 2);\n    }\n",
    "    #[test]\n    fn exact_multiple() {\n        assert_eq!(page_count(10, 5), 2);\n    }\n\n"
    "    #[test]\n    fn test_partial_last_page() {\n        assert_eq!(page_count(11, 5), 3);\n    }\n",
)


def _fake_cargo_script() -> str:
    body = r"""import re
import sys
from pathlib import Path

args = sys.argv[1:]
if "test" not in args:
    sys.exit(0)
src = Path("src/lib.rs").read_text(encoding="utf-8")
if "new_seam()" in src and "fn new_seam" not in src:
    print("error[E0425]: cannot find function `new_seam` in this scope", file=sys.stderr)
    print("   Compiling pager v0.1.0 (/work)")
    print("error: could not compile `pager` (lib test) due to 1 previous error", file=sys.stderr)
    sys.exit(101)

# Simulate page_count(10, 5) == 2 and page_count(11, 5) == 3 on fixed vs 2 on broken.
is_fixed = "(total + size - 1) / size" in src
mod_match = re.search(r"#\[cfg\(test\)\]\s*mod\s+tests\s*\{(.*)\}", src, re.S)
tests = []
if mod_match:
    for fn_name in re.findall(r"fn\s+([A-Za-z_]\w*)\s*\(\)", mod_match.group(1)):
        ok = True if fn_name == "exact_multiple" else is_fixed
        tests.append((f"tests::{fn_name}", ok))
passed = sum(1 for _, ok in tests if ok)
failed = len(tests) - passed
print("     Running unittests src/lib.rs (target/debug/deps/pager-1234)")
print()
print(f"running {len(tests)} tests")
for name, ok in tests:
    print(f"test {name} ... {'ok' if ok else 'FAILED'}")
print()
status = "ok" if failed == 0 else "FAILED"
print(f"test result: {status}. {passed} passed; {failed} failed; 0 ignored; 0 measured; 0 filtered out; finished in 0.00s")
sys.exit(0 if failed == 0 else 101)
"""
    return f"#!{sys.executable}\n" + body


class ScannerTests(unittest.TestCase):
    def test_ignores_strings_comments_char_literals_lifetimes_and_non_test_cfg(self):
        src = b"""\
pub fn lifetime_user<'a>(s: &'a str) -> char {
    let fake1 = "#[cfg(test)] mod in_str { fn nope() {} }";
    let fake2 = r#"#[cfg(test)] mod in_raw { fn nope() {} }"#;
    let fake3 = b"#[cfg(test)] mod in_bytes {}";
    // #[cfg(test)] mod in_line_comment {}
    /* nested /* #[cfg(test)] */ #[cfg(test)] mod in_block_comment {} */
    let _ = fake1.len() + fake2.len() + fake3.len();
    '{'
}

#[cfg(not(test))]
mod non_test {
    fn keep_me() {}
}

#[cfg(all(test, feature = "extra"))]
mod gated_tests {}

#[cfg(/* keep */ test, )]
#[allow(dead_code)]
pub mod inline_suite {
    #[test]
    fn t_one() {
        let brace = '}';
        let _ = brace;
    }
}
"""
        spans = rust_inline.test_modules(src)
        self.assertEqual(["inline_suite"], [name for name, _, _ in spans])
        lines = rust_inline.test_lines(src)
        text_lines = src.decode().splitlines()
        self.assertEqual("#[cfg(/* keep */ test, )]", text_lines[min(lines) - 1])
        self.assertEqual("}", text_lines[max(lines) - 1])
        prod = rust_inline.product_bytes(src).decode()
        self.assertIn("mod non_test", prod)
        self.assertIn("mod gated_tests", prod)
        self.assertNotIn("t_one", prod)

    def test_splice_replaces_existing_appends_new_and_supports_nested_scope(self):
        spliced, details = rust_inline.splice_bytes(BASE_LIB.encode(), FIXED_LIB.encode())
        self.assertEqual("tests", details[0]["name"])
        self.assertEqual(rust_inline.product_bytes(BASE_LIB.encode()), rust_inline.product_bytes(spliced))
        self.assertIn(b"total / size", spliced)
        self.assertIn(b"test_partial_last_page", spliced)

        base_without_tests = b"pub fn add(a: i32, b: i32) -> i32 {\n    a + b\n}"
        cand_with_tests = b"pub fn add(a: i32, b: i32) -> i32 {\n    a + b + 0\n}\n\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn it_works() {}\n}\n"
        # Product bytes differ between base and candidate, splice_bytes replaces nothing and checks base invariance!
        spliced_new, _ = rust_inline.splice_bytes(base_without_tests, cand_with_tests)
        self.assertEqual(
            rust_inline.product_bytes(base_without_tests).rstrip(b"\n"),
            rust_inline.product_bytes(spliced_new).rstrip(b"\n"),
        )
        self.assertIn(b"it_works", spliced_new)

        nested_base = b"mod inner {\n    pub fn f() -> i32 { 1 }\n}\n"
        nested_cand = b"mod inner {\n    pub fn f() -> i32 { 1 }\n\n    #[cfg(test)]\n    mod tests {\n        #[test]\n        fn t() {}\n    }\n}\n"
        nested_spliced, nested_details = rust_inline.splice_bytes(nested_base, nested_cand)
        self.assertEqual(1, nested_details[0]["depth"])
        self.assertEqual(rust_inline.product_bytes(nested_base), rust_inline.product_bytes(nested_spliced))
        self.assertIn(b"fn t()", nested_spliced)

        with self.assertRaises(ValueError):
            rust_inline.splice_bytes(BASE_LIB.encode(), b"pub fn no_tests() {}\n")
        with self.assertRaises(ValueError):
            rust_inline.splice_bytes(b"mod other {\n    pub fn f() {}\n}\n", nested_cand)


class KeptTests(unittest.TestCase):
    """rust_inline.tests_kept and kept: a Rust file keeps the tests written beside its code."""

    CODE = b"pub fn double(n: i32) -> i32 { n * 2 }\n"
    TESTS = b"\n#[cfg(test)]\nmod tests {\n    #[test]\n    fn doubles() { assert_eq!(super::double(2), 4); }\n}\n"
    MORE = b"\n#[cfg(test)]\nmod more {\n    #[test]\n    fn again() { assert_eq!(super::double(1), 2); }\n}\n"

    def test_the_same_modules_are_kept_whatever_the_code_around_them_does(self):
        self.assertTrue(rust_inline.tests_kept(self.CODE, self.CODE.replace(b"n * 2", b"n + n")))
        self.assertTrue(rust_inline.tests_kept(b"<<<<<<< HEAD\n" + self.CODE + self.TESTS, self.CODE + self.TESTS))
        self.assertTrue(rust_inline.tests_kept(self.CODE + self.TESTS, self.CODE + self.TESTS + self.MORE))

    def test_a_module_that_is_removed_or_edited_is_not_kept(self):
        both = self.CODE + self.TESTS + self.MORE
        self.assertFalse(rust_inline.tests_kept(both, self.CODE + self.TESTS))
        self.assertFalse(rust_inline.tests_kept(both, self.CODE))
        self.assertFalse(rust_inline.tests_kept(both, self.CODE + self.TESTS.replace(b", 4)", b", 5)") + self.MORE))
        self.assertFalse(rust_inline.tests_kept(self.CODE + self.TESTS, b""))

    def test_a_test_item_outside_a_module_is_not_judged(self):
        for item in (
            b"#[test]\nfn top() {}\n",
            b"#[tokio::test]\nasync fn top() {}\n",
            b"# [ test ]\nfn top() {}\n",
            b"#[cfg(test)]\nuse std::fmt;\n",
            b"#[cfg(test)]\nfn helper() {}\n",
            b"#[cfg(all(test, unix))]\nfn helper() {}\n",
            b"#[cfg_attr(test, derive(Debug))]\nstruct Kept;\n",
        ):
            with self.subTest(item=item):
                self.assertFalse(rust_inline.tests_kept(self.CODE + item, self.CODE + item))
                self.assertFalse(rust_inline.tests_kept(self.CODE, self.CODE + item))
                self.assertFalse(rust_inline.tests_kept(self.CODE + item, self.CODE))
        self.assertTrue(rust_inline.tests_kept(self.CODE + b"#[inline]\nfn fast() {}\n", self.CODE))

    def test_a_file_the_scanner_cannot_read_is_not_shown_to_keep_its_tests(self):
        # One side of the conflict opens a block the other closes: the module is no longer at the top.
        unbalanced = (
            b"<<<<<<< HEAD\npub fn double(n: i32) -> i32 {\n=======\npub fn twice(n: i32) -> i32 {\n>>>>>>> feature\n"
            b"    n * 2\n}\n" + self.TESTS
        )
        self.assertFalse(rust_inline.tests_kept(unbalanced, self.CODE))

    def test_every_rust_file_the_change_edits_or_deletes_must_keep_them(self):
        tested = (self.CODE + self.TESTS).decode()
        project = Project({"src/lib.rs": tested, "src/other.rs": tested, "notes.txt": "#[test]\n"})
        self.addCleanup(project.close)
        kept = lambda changes: rust_inline.kept(project.root, project.base, changes)  # noqa: E731
        self.assertTrue(kept({}))
        self.assertTrue(kept({"src/lib.rs": "modified", "notes.txt": "deleted"}))
        project.write({"src/new.rs": "#[test]\nfn top() {}\n"})
        self.assertTrue(kept({"src/new.rs": "added"}))
        self.assertFalse(kept({"src/new.rs": "modified"}))  # the original code has no such file
        project.write({"src/lib.rs": self.CODE.decode()})
        self.assertFalse(kept({"src/lib.rs": "modified"}))
        self.assertFalse(kept({"src/other.rs": "deleted"}))
        self.assertTrue(changed_tests.inline_tests_kept(project.root, project.base, {"src/other.rs": "modified"}))
        self.assertFalse(changed_tests.inline_tests_kept(project.root, project.base, {"src/lib.rs": "modified"}))


class ProofIntegrationTests(unittest.TestCase):
    def test_inventory_excludes_inline_test_lines_on_rs_product_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "src").mkdir()
            (root / "src/lib.rs").write_text(FIXED_LIB)
            diff = (
                "diff --git a/src/lib.rs b/src/lib.rs\n"
                "--- a/src/lib.rs\n+++ b/src/lib.rs\n"
                "@@ -2,1 +2,1 @@\n+    if total == 0 { 0 } else { (total + size - 1) / size }\n"
                "@@ -13,0 +13,4 @@\n+    #[test]\n+    fn test_partial_last_page() {\n+        assert_eq!(page_count(11, 5), 3);\n+    }\n"
            )
            rows = measurement.inventory(root, [("src/lib.rs", "modified")], diff)
            self.assertEqual([2], rows[0]["lines"])

    def test_cargo_changed_names_ignores_product_edits_in_mixed_source_files(self):
        # Product changed, only test_partial_last_page was added inside #[cfg(test)].
        names = cargo_tests.changed_names([("src/lib.rs", BASE_LIB, FIXED_LIB)])
        self.assertEqual({("src/lib.rs", "test_partial_last_page")}, names)
        self.assertTrue(cargo_tests.defined_in(names, "src/lib.rs::tests::test_partial_last_page"))
        self.assertFalse(cargo_tests.defined_in(names, "src/lib.rs::tests::exact_multiple"))
        # Product-only edit outside #[cfg(test)] reports zero changed test functions.
        prod_only = BASE_LIB.replace("total / size", "(total + size - 1) / size")
        self.assertEqual(set(), cargo_tests.changed_names([("src/lib.rs", BASE_LIB, prod_only)]))

    def test_plan_test_suite_detects_inline_rust_tests_as_existing_tests(self):
        project = Project({"Cargo.toml": '[package]\nname = "pager"\nversion = "0.1.0"\n', "src/lib.rs": BASE_LIB})
        self.addCleanup(project.close)
        self.assertTrue(plan_test_suite._existing_tests(project.root))
        body = {
            "milestones": [{"id": "M1", "affected_paths": ["src/lib.rs"]}],
            "acceptance_criteria": [
                {"id": "C1", "criterion": "pages", "verification_method": "test: test_partial_last_page"}
            ],
        }
        self.assertIsNone(plan_test_suite.untested_code(project.root, body))

    def test_verify_bugfix_and_test_only_rejection_with_inline_cargo_tests(self):
        with tempfile.TemporaryDirectory() as bindir:
            cargo_bin = Path(bindir) / "cargo"
            cargo_bin.write_text(_fake_cargo_script())
            cargo_bin.chmod(0o755)
            env = {**os.environ, "PATH": f"{bindir}:{os.environ.get('PATH', '')}"}
            with mock.patch.dict(os.environ, env):
                project = Project(
                    {"Cargo.toml": '[package]\nname = "pager"\nversion = "0.1.0"\n', "src/lib.rs": BASE_LIB}
                )
                self.addCleanup(project.close)
                cases = [
                    {
                        "id": "AC1",
                        "text": "counts partial last page",
                        "kind": "restore",
                        "test_name": "test_partial_last_page",
                    }
                ]

                # 1. Valid bugfix: product fix + new inline test -> fails on base, passes on candidate!
                project.write({"src/lib.rs": FIXED_LIB})
                proof = project.verify(cases=cases)
                self.assertEqual(verify.PASS, proof["verdict"], proof)
                regression.check_cases(proof, cases)
                self.assertEqual(verify.PASS, proof["verdict"], proof)
                self.assertEqual(["src/lib.rs::tests::test_partial_last_page"], proof["fail_to_pass"])
                self.assertIn("test_parts", proof["test_prerequisites"])

                # 2. Inline-test-only edit in bugfix mode: caught by product-change gate!
                project.write({"src/lib.rs": TEST_ONLY_LIB})
                test_only = project.verify(cases=cases)
                self.assertEqual(verify.FAIL, test_only["verdict"], test_only)
                self.assertIn("Only test files changed", " ".join(test_only["failures"]))

                # 3. Seam check on inline #[cfg(test)] using function added only by candidate!
                seam_lib = FIXED_LIB.replace("page_count(11, 5), 3", "new_seam()").replace(
                    "pub fn page_count", "pub fn new_seam() -> u32 { 3 }\n\npub fn page_count"
                )
                project.write({"src/lib.rs": seam_lib})
                src_added, inline_words = changed_tests.seam_words(
                    "src/lib.rs", BASE_LIB, seam_lib, __import__("autocode_proof_seam")
                )
                self.assertIn("new_seam", src_added)
                self.assertIn("new_seam", inline_words)
                seam_proof = project.verify(cases=cases)
                self.assertEqual(verify.FAIL, seam_proof["verdict"], seam_proof)
                self.assertTrue(seam_proof["failures"])

                # Sanity check that a subprocess call worked cleanly.
                proc = subprocess.run(["cargo", "--version"], env=env, capture_output=True)
                self.assertEqual(0, proc.returncode)


if __name__ == "__main__":
    unittest.main()
