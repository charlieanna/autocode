"""Pure source inventory and pinned synthetic command evidence; no commands launch."""

import copy
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import autocode_changed_code_evidence as policy
import autocode_changed_code_measurement as measurement
import autocode_util as util

from .test_command_receipt import guarded_command_receipt

SOURCE = "current-source"
CONTEXT = "current-context"
APP = b"def value(n):\n    return n + 1\n"
TESTS = ["tests.test_value.Value.test_boundary"]


class PythonInventoryTests(unittest.TestCase):
    def test_declarations_comments_and_docstrings_are_not_executable_obligations(self):
        data = b'''# comment
"""module docs"""
def plain():
    """function docs"""
    return 1

@decorate(5)
def configured(
    value=make_default(),
):
    return value

class Plain:
    """class docs"""
    pass

class Derived(make_base(), metaclass=Meta):
    value = 1
'''
        lines = measurement.python_lines(data)
        self.assertFalse(lines & {1, 2, 3, 4, 6, 8, 10, 12, 13, 14, 16})
        self.assertTrue({5, 7, 9, 11, 17, 18} <= lines, lines)

    def test_inline_body_and_evaluated_default_survive_header_classification(self):
        self.assertEqual({1}, measurement.python_lines(b"def value(): return 1\n"))
        self.assertEqual(set(), measurement.python_lines(b'def value(): "docs"\n'))
        self.assertEqual({1}, measurement.python_lines(b'def value(n=default()): "docs"\n'))

    def test_source_inventory_binds_exact_bytes_and_preserves_unsupported_uncertainty(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "app.py").write_bytes(APP)
            (root / "code.rs").write_text("// comment\nfn value() -> i32 { 1 }\n")
            diff = "diff --git a/app.py b/app.py\n+++ b/app.py\n@@ -0,0 +1,2 @@\n"
            diff += "diff --git a/code.rs b/code.rs\n+++ b/code.rs\n@@ -0,0 +1,2 @@\n"
            rows = measurement.inventory(root, [("app.py", "added"), ("code.rs", "added")], diff)
            self.assertEqual([2], rows[0]["lines"])
            self.assertEqual(hashlib.sha256(APP).hexdigest(), rows[0]["sha256"])
            self.assertEqual([2], rows[1]["lines"])
            self.assertIn("No language statement parser", rows[1]["note"])

    @unittest.skipUnless(os.name == "posix", "POSIX symlink fixture")
    def test_source_reader_refuses_aliases_and_parent_traversal(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "app.py").write_bytes(APP)
            (root / "alias.py").symlink_to(root / "app.py")
            with self.assertRaises(ValueError):
                measurement.source(root, "alias.py")
            with self.assertRaises(ValueError):
                measurement.source(root, "../app.py")


class InventoryRefinementTests(unittest.TestCase):
    def setUp(self):
        self.rows = [{"path": "app.js", "sha256": "a" * 64, "lines": [1, 2, 3, 4]}]
        self.coverage = {
            "source_revision": SOURCE,
            "proof_context": CONTEXT,
            "tool": "c8",
            "files": [{"path": "app.js", "sha256": "a" * 64, "executed_lines": [2], "missing_lines": [3]}],
        }

    def refine(self, rows=None, coverage=None):
        return measurement.refine_inventory(
            self.rows if rows is None else rows,
            self.coverage if coverage is None else coverage,
            source_revision=SOURCE,
            proof_context=CONTEXT,
        )

    def test_measured_go_and_javascript_declarations_and_braces_are_not_obligations(self):
        for name, tool in (("app.go", "go coverprofile"), ("app.js", "c8"), ("app.ts", "vitest")):
            with self.subTest(name=name):
                self.rows[0]["path"] = self.coverage["files"][0]["path"] = name
                self.coverage["tool"] = tool
                before = copy.deepcopy((self.rows, self.coverage))
                result = self.refine()
                self.assertEqual([2, 3], result[0]["lines"])
                self.assertEqual(before, (self.rows, self.coverage))
                self.assertIn("authenticated", result[0]["note"])

    def test_unbound_or_malformed_statement_maps_cannot_erase_obligations(self):
        for alter in (
            lambda value: value.pop("source_revision"),
            lambda value: value.update(source_revision="old"),
            lambda value: value.update(proof_context="old"),
            lambda value: value.update(tool=None),
            lambda value: value.update(files=[]),
            lambda value: value["files"][0].update(sha256="b" * 64),
            lambda value: value["files"][0].pop("missing_lines"),
            lambda value: value["files"][0].update(executed_lines=[True]),
            lambda value: value["files"].append(copy.deepcopy(value["files"][0])),
        ):
            with self.subTest(alter=alter):
                coverage = copy.deepcopy(self.coverage)
                alter(coverage)
                self.assertEqual(self.rows, self.refine(coverage=coverage))
        self.assertEqual(self.rows, self.refine(coverage={"measured_lines": {"app.js": {"executed": []}}}))

    def test_python_and_unmeasured_files_retain_their_inventory(self):
        for name in ("app.py", "app.pyi", "unmeasured.rs"):
            with self.subTest(name=name):
                rows = [{**self.rows[0], "path": name}]
                coverage = copy.deepcopy(self.coverage)
                if name.endswith((".py", ".pyi")):
                    coverage["files"][0]["path"] = name
                self.assertEqual(rows, self.refine(rows, coverage))

    def test_whole_line_block_comments_excluded_but_mixed_code_retained(self):
        data = b'/* heading\n * text\n */\n/* label */ value();\n/* one */\nconst text = "/* string */";\n'
        self.assertEqual({1, 2, 3, 5}, measurement._block_comment_lines(data, "app.js"))
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve()
            (root / "app.js").write_bytes(data)
            diff = "diff --git a/app.js b/app.js\n+++ b/app.js\n@@ -0,0 +1,6 @@\n"
            rows = measurement.inventory(root, [("app.js", "added")], diff)
            self.assertEqual([4, 6], rows[0]["lines"])

    def test_ambiguous_multiline_literals_and_nested_comment_languages_stay_conservative(self):
        for data, name in (
            (b"const text = `\n/* literal */\n`;\n", "app.js"),
            (b"const text = '\\\n/* literal */';\n", "app.js"),
            (b'const auto text = R"(\n/* literal */\n)";\n', "app.cpp"),
            (b"/* outer /* nested */\n still comment */\n", "app.rs"),
        ):
            with self.subTest(name=name, data=data):
                self.assertEqual(set(), measurement._block_comment_lines(data, name))


@unittest.skipUnless(
    hasattr(os, "O_NOFOLLOW") and hasattr(os, "O_DIRECTORY"), "POSIX descriptor-anchored capture custody"
)
class MeasurementTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.tree = self.root / "candidate"
        self.tree.mkdir()
        (self.tree / "app.py").write_bytes(APP)
        self.sha = hashlib.sha256(APP).hexdigest()
        self.inventory = [{"path": "app.py", "sha256": self.sha, "lines": [2]}]

    def receipt(self, name, *, failed=(), passed=TESTS):
        output = self.root / (name + ".log")
        output.write_text("synthetic owned capture " + name)
        return guarded_command_receipt(
            {
                "command": "python -m unittest tests.test_value",
                "output": str(output),
                "output_sha256": util.file_hash(output),
                "exit_code": 1 if failed else 0,
                "timed_out": False,
                "error": "",
                "results": {
                    "complete": True,
                    "passed": list(passed),
                    "failed": list(failed),
                    "skipped": [],
                    "total": len(passed) + len(failed),
                    "collection_errors": [],
                    "uncollected": [],
                },
            }
        )

    def coverage(self, raw=None, *, tool="coverage.py", inventory=None):
        raw = raw if raw is not None else {"files": {"app.py": {"executed_lines": [1, 2], "missing_lines": []}}}
        path = self.root / "coverage.json"
        path.write_text(raw if isinstance(raw, str) else json.dumps(raw))
        receipt = self.receipt("coverage")
        report = {
            "collected": True,
            "tool": tool,
            "report_path": str(path),
            "report_sha256": util.file_hash(path),
            "report_fresh": True,
            "source_root": str(self.tree),
            "command_outputs": [receipt["output"]],
            "command_receipts": [receipt],
            "measured_lines": {"app.py": {"executed": [999], "missing": []}},
        }
        return report, [receipt]

    def normalize_coverage(self, report, receipts, inventory=None):
        return measurement.coverage_envelope(
            report,
            inventory if inventory is not None else self.inventory,
            source_revision=SOURCE,
            proof_context=CONTEXT,
            receipts=receipts,
            root=self.root,
        )

    def mutation(self, *outcomes):
        reference = self.receipt("reference")
        receipts, rows = [reference], []
        for index, outcome in enumerate(outcomes or ("killed",)):
            record = self.receipt(
                str(index), failed=TESTS if outcome == "killed" else (), passed=[] if outcome == "killed" else TESTS
            )
            receipts.append(record)
            rows.append(
                {
                    "line": 2,
                    "end_line": 2,
                    "operator": "constant_return",
                    "source_sha256": self.sha,
                    "outcome": outcome,
                    "evidence": [record["output"]],
                    "failed_tests": TESTS if outcome == "killed" else [],
                }
            )
        return {
            "collected": True,
            "selected_tests": TESTS,
            "reference_output": reference["output"],
            "files": [{"path": "app.py", "sha256": self.sha, "mutants": rows}],
        }, receipts

    def normalize_mutation(self, report, receipts):
        return measurement.mutation_envelope(
            report, source_revision=SOURCE, proof_context=CONTEXT, receipts=receipts, root=self.root
        )

    def test_coverage_reparses_pinned_raw_bytes_instead_of_advisory_summary(self):
        report, receipts = self.coverage()
        before = copy.deepcopy((report, receipts, self.inventory))
        result = self.normalize_coverage(report, receipts)
        self.assertEqual([1, 2], result["files"][0]["executed_lines"])
        self.assertEqual(before, (report, receipts, self.inventory))
        Path(report["report_path"]).write_text("{}")
        self.assertEqual([], self.normalize_coverage(report, receipts)["files"])

    def test_capture_reader_refuses_redirects_nonregular_oversized_and_wrong_pin(self):
        record = self.receipt("capture")
        output = Path(record["output"])
        self.assertEqual(output.read_bytes(), measurement.pinned(str(output), record["output_sha256"], self.root))
        alias = self.root / "capture-alias.log"
        alias.symlink_to(output)
        directory_alias = self.root / "directory-alias"
        directory_alias.symlink_to(self.tree, target_is_directory=True)
        copied = self.tree / "capture.log"
        copied.write_bytes(output.read_bytes())
        fifo = self.root / "pipe"
        os.mkfifo(fifo)
        for path in (alias, directory_alias / "capture.log", fifo, self.tree):
            with self.subTest(path=path), self.assertRaises((OSError, ValueError)):
                measurement.pinned(str(path), record["output_sha256"], self.root)
        with self.assertRaises(ValueError):
            measurement.pinned(str(output), "0" * 64, self.root)
        with patch.object(measurement, "MAX_CAPTURE_BYTES", 1):
            self.assertFalse(measurement.known_capture(record, self.root))

    def test_missing_stale_redirected_failed_or_unowned_coverage_never_counts(self):
        for mutation in (
            lambda report, receipts: report.pop("report_sha256"),
            lambda report, receipts: report.update(report_fresh=False),
            lambda report, receipts: report.update(command_outputs=["unrelated.log"]),
            lambda report, receipts: receipts[0].update(exit_code=1),
            lambda report, receipts: receipts[0].pop("supervision"),
            lambda report, receipts: Path(receipts[0]["output"]).write_text("changed"),
            lambda report, receipts: (self.tree / "app.py").write_bytes(APP + b"# changed\n"),
        ):
            with self.subTest(mutation=mutation):
                (self.tree / "app.py").write_bytes(APP)
                report, receipts = self.coverage()
                mutation(report, receipts)
                self.assertEqual([], self.normalize_coverage(report, receipts)["files"])
        (self.tree / "app.py").write_bytes(APP)
        report, receipts = self.coverage()
        alias = self.root / "alias.json"
        alias.symlink_to(report["report_path"])
        report["report_path"] = str(alias)
        self.assertEqual([], self.normalize_coverage(report, receipts)["files"])

    def test_coverage_formats_keep_zero_statement_go_blocks_out(self):
        (self.tree / "go.mod").write_text("module example.test/app\n")
        (self.tree / "value.go").write_text("package app\nfunc value() int { return 1 }\n")
        digest = util.file_hash(self.tree / "value.go")
        rows = [{"path": "value.go", "sha256": digest, "lines": [2]}]
        report, receipts = self.coverage(
            "mode: set\nexample.test/app/value.go:1.1,1.12 0 0\nexample.test/app/value.go:2.20,2.28 1 1\n",
            tool="go coverprofile",
        )
        result = self.normalize_coverage(report, receipts, rows)
        self.assertEqual([2], result["files"][0]["executed_lines"])
        self.assertEqual([], result["files"][0]["missing_lines"])
        (self.tree / "value.js").write_text("const value = () => 1;\n")
        rows = [{"path": "value.js", "sha256": util.file_hash(self.tree / "value.js"), "lines": [1]}]
        raw = {
            str(self.tree / "value.js"): {
                "statementMap": {"0": {"start": {"line": 1}, "end": {"line": 1}}},
                "s": {"0": 1},
            }
        }
        for tool in ("c8", "vitest"):
            with self.subTest(tool=tool):
                report, receipts = self.coverage(raw, tool=tool)
                self.assertEqual([1], self.normalize_coverage(report, receipts, rows)["files"][0]["executed_lines"])

    def test_authentic_kills_survivors_and_unrun_rows_all_survive_normalization(self):
        report, receipts = self.mutation("killed", "survived")
        report["files"][0]["mutants"].append({"line": 2, "outcome": "not_run", "source_sha256": self.sha})
        before = copy.deepcopy((report, receipts))
        result = self.normalize_mutation(report, receipts)
        self.assertEqual(["killed", "survived", "not_run"], [row["outcome"] for row in result["files"][0]["mutants"]])
        assessed = policy.assess(self.inventory, None, result, source_revision=SOURCE, proof_context=CONTEXT)
        self.assertEqual("UNVERIFIED", assessed["status"])
        self.assertEqual(before, (report, receipts))

    def test_invalid_mutant_provenance_is_retained_beside_a_valid_kill(self):
        for mutation in (
            lambda row, receipt: row.update(source_sha256="b" * 64),
            lambda row, receipt: row.update(evidence=["missing.log"]),
            lambda row, receipt: row.update(failed_tests=["different.test"]),
            lambda row, receipt: receipt["results"].update(failed=[], passed=TESTS),
            lambda row, receipt: receipt["results"].update(total=0),
            lambda row, receipt: receipt["results"].update(setup_errors=TESTS),
            lambda row, receipt: receipt["results"].update(collection_errors=TESTS),
            lambda row, receipt: Path(receipt["output"]).write_text("tampered"),
        ):
            with self.subTest(mutation=mutation):
                report, receipts = self.mutation("killed", "killed")
                mutation(report["files"][0]["mutants"][1], receipts[2])
                rows = self.normalize_mutation(report, receipts)["files"][0]["mutants"]
                self.assertEqual(["killed", "not_run"], [row["outcome"] for row in rows])
                self.assertEqual([], rows[1]["failed_tests"])

    def test_unmutated_missing_tests_unknown_custody_or_missing_baseline_refuses_kills(self):
        for mutation in (
            lambda report, receipts: report.update(reference_output="absent"),
            lambda report, receipts: report.update(reference_output=[]),
            lambda report, receipts: report.update(selected_tests=TESTS + ["missing.test"]),
            lambda report, receipts: receipts[0]["results"].update(passed=[], total=0),
            lambda report, receipts: receipts[0].pop("supervision"),
        ):
            with self.subTest(mutation=mutation):
                report, receipts = self.mutation()
                mutation(report, receipts)
                rows = self.normalize_mutation(report, receipts)["files"][0]["mutants"]
                self.assertEqual(["not_run"], [row["outcome"] for row in rows])

    def test_closed_deadline_is_known_nonpositive_but_never_a_kill(self):
        report, receipts = self.mutation("killed", "timeout")
        receipt = receipts[2]
        terminal_path = Path(receipt["supervision"]["receipt"])
        terminal = util.read(terminal_path)
        terminal["cause"] = "stage_deadline"
        util.atomic_json(terminal_path, terminal)
        receipt.update(exit_code=None, timed_out=True, supervision_sha256=util.file_hash(terminal_path))
        self.assertTrue(measurement.known_capture(receipt, self.root))
        rows = self.normalize_mutation(report, receipts)["files"][0]["mutants"]
        self.assertEqual(["killed", "timeout"], [row["outcome"] for row in rows])
        for change in (
            {"interrupted": True},
            {"supervision_errors": ["uncertain"]},
            {"exit_code": 0},
            {"supervision_sha256": None},
        ):
            self.assertFalse(measurement.known_capture({**receipt, **change}, self.root))
        terminal["cleanup_error"] = "unknown child"
        util.atomic_json(terminal_path, terminal)
        receipt["supervision_sha256"] = util.file_hash(terminal_path)
        self.assertFalse(measurement.known_capture(receipt, self.root))


if __name__ == "__main__":
    unittest.main()
