"""JUnit identities, conservative outcomes and captured report authentication."""

import copy
import hashlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import autocode_junit as junit


class JUnitTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.tree = self.root / "project"
        self.evidence = self.root / "evidence"
        self.tree.mkdir()
        self.evidence.mkdir()
        self.source = self.tree / "report.xml"
        self.destination = self.evidence / "captured.xml"

    def parse(self, xml):
        self.source.write_bytes(xml.encode() if isinstance(xml, str) else xml)
        return junit.results(self.source)

    def captured(self, xml=None):
        junit.prepare(self.tree, "junit:report.xml")
        self.source.write_text(xml or '<testsuite name="Example"><testcase name="works" time="0.25"/></testsuite>')
        metadata, parsed = junit.capture(self.tree, "junit:report.xml", self.destination)
        return {"test_report": metadata, "results": parsed}

    def test_normalizes_only_contained_explicit_junit_files(self):
        self.assertEqual(junit.normalize("junit:./target//test-results.xml"), "junit:target/test-results.xml")
        for spec in (
            None,
            "",
            "xml:report.xml",
            "JUnit:report.xml",
            "junit:",
            "junit: ",
            "junit:.",
            "junit:/report.xml",
            "junit:../report.xml",
            "junit:target/../report.xml",
            "junit:C:/report.xml",
            "junit:target\\report.xml",
            "junit:report\x00.xml",
            "junit:report\n.xml",
        ):
            with self.subTest(spec=spec), self.assertRaises(ValueError):
                junit.normalize(spec)

    def test_preserves_class_names_parameter_names_and_only_reported_durations(self):
        parsed = self.parse(
            '<testsuite name="Fallback" tests="3" failures="1" skipped="1" time="2.5">'
            '<testcase classname="pkg.Example" name="works[unicode Ω]" time="1.25"/>'
            '<testcase name="assertion"><failure message="expected 2"/></testcase>'
            '<testcase classname="pkg.Example" name="disabled" time="0"><skipped/></testcase>'
            '<system-out><![CDATA[<testcase name="invented"/>]]></system-out></testsuite>'
        )
        self.assertEqual(parsed["passed"], ["pkg.Example::works[unicode Ω]"])
        self.assertEqual(parsed["failed"], ["Fallback::assertion"])
        self.assertEqual(parsed["skipped"], ["pkg.Example::disabled"])
        self.assertEqual(parsed["collection_errors"], [])
        self.assertEqual(parsed["uncollected"], [])
        self.assertEqual(parsed["durations"], {"pkg.Example::disabled": 0.0, "pkg.Example::works[unicode Ω]": 1.25})
        self.assertEqual(parsed["total"], 3)
        self.assertIs(parsed["complete"], True)

    def test_missing_durations_stay_unknown(self):
        parsed = self.parse('<testsuite name="Example" time="4"><testcase name="works"/></testsuite>')
        self.assertNotIn("durations", parsed)

    def test_runtime_skip_is_not_necessarily_a_disabled_test(self):
        parsed = self.parse(
            '<testsuite name="Example" tests="1" skipped="1" disabled="0">'
            '<testcase name="runtimeSkip"><skipped/></testcase></testsuite>'
        )
        self.assertEqual(parsed["skipped"], ["Example::runtimeSkip"])
        self.assertEqual(parsed["passed"], [])
        with self.assertRaises(ValueError):
            self.parse(
                '<testsuite name="Example" tests="1" skipped="1" disabled="2">'
                '<testcase name="runtimeSkip"><skipped/></testcase></testsuite>'
            )

    def test_error_cannot_establish_an_ordinary_fail_to_pass_flip(self):
        parsed = self.parse(
            '<testsuite name="Example" tests="1" failures="0" errors="1">'
            '<testcase name="setupOrBody"><error type="RuntimeException"/></testcase></testsuite>'
        )
        self.assertEqual(parsed["passed"], [])
        self.assertEqual(parsed["failed"], ["Example::setupOrBody"])
        self.assertEqual(parsed["collection_errors"], ["Example::setupOrBody"])
        self.assertEqual(parsed["uncollected"], ["Example::setupOrBody"])

    def test_nonexecution_attributes_cannot_silently_become_passes(self):
        for attributes in ('status="notrun"', 'result="suppressed"', 'result="skipped"', 'status="unknown"'):
            with self.subTest(attributes=attributes), self.assertRaises(ValueError):
                self.parse(f'<testsuite name="Example"><testcase name="disabled" {attributes}/></testsuite>')
        parsed = self.parse(
            '<testsuite name="Example"><testcase name="works" status="run" result="completed"/></testsuite>'
        )
        self.assertEqual(parsed["passed"], ["Example::works"])

    def test_nested_aggregate_counts_are_not_counted_twice(self):
        parsed = self.parse(
            '<testsuites tests="3" failures="1" errors="1" skipped="1">'
            '<testsuite name="outer" tests="2" failures="1" skipped="1">'
            '<testsuite name="inner" tests="1" failures="1">'
            '<testcase name="failed"><failure/></testcase></testsuite>'
            '<testcase name="skip"><skipped/></testcase></testsuite>'
            '<testsuite name="other" tests="1" errors="1">'
            '<testcase name="error"><error/></testcase></testsuite></testsuites>'
        )
        self.assertEqual(parsed["total"], 3)
        self.assertEqual(parsed["failed"], ["inner::failed", "other::error"])
        self.assertEqual(parsed["skipped"], ["outer::skip"])

    def test_accepts_namespaced_junit_and_optional_counts(self):
        parsed = self.parse(
            '<j:testsuites xmlns:j="urn:junit"><j:testsuite name="Suite">'
            '<j:properties><j:property name="version" value="1"/></j:properties>'
            '<j:testcase name="works"/></j:testsuite></j:testsuites>'
        )
        self.assertEqual(parsed["passed"], ["Suite::works"])

    def test_nested_outcomes_in_metadata_are_invalid(self):
        for scope in ("suite", "case"):
            for metadata in (
                '<system-out><failure message="failed"/></system-out>',
                "<system-err><error/></system-err>",
                "<properties><skipped/></properties>",
                '<properties><property name="x"><failure/></property></properties>',
            ):
                case = '<testcase name="t">' + (metadata if scope == "case" else "") + "</testcase>"
                xml = '<testsuite name="S">' + (metadata if scope == "suite" else "") + case + "</testsuite>"
                with self.subTest(scope=scope, metadata=metadata), self.assertRaises(ValueError):
                    self.parse(xml)

    def test_outcome_elements_with_children_are_invalid(self):
        for outcome in ("failure", "error", "skipped"):
            with self.subTest(outcome=outcome), self.assertRaises(ValueError):
                self.parse(
                    '<testsuite name="S"><testcase name="t">'
                    f"<{outcome}><system-out/></{outcome}></testcase></testsuite>"
                )

    def test_escaped_and_cdata_auxiliary_text_preserves_outcomes(self):
        parsed = self.parse(
            '<testsuite name="S" tests="4" failures="1" errors="1" skipped="1">'
            '<properties><property name="suite" value="&lt;failure/&gt;"/>'
            '<property name="literal"><![CDATA[<error/>]]></property></properties>'
            "<system-out>&lt;skipped/&gt;</system-out>"
            '<testcase name="pass"><properties><property name="case" value="literal"/>'
            '<property name="literal"><![CDATA[<failure/>]]></property></properties>'
            "<system-out>&lt;failure/&gt;</system-out>"
            "<system-err><![CDATA[<error/>]]></system-err></testcase>"
            '<testcase name="failure"><failure><![CDATA[<skipped/> trace]]></failure></testcase>'
            '<testcase name="error"><error>trace &lt;failure/&gt;</error></testcase>'
            '<testcase name="skip"><skipped><![CDATA[<failure/> reason]]></skipped></testcase>'
            "</testsuite>"
        )
        self.assertEqual(parsed["passed"], ["S::pass"])
        self.assertEqual(parsed["failed"], ["S::error", "S::failure"])
        self.assertEqual(parsed["skipped"], ["S::skip"])
        self.assertEqual(parsed["collection_errors"], ["S::error"])
        self.assertEqual(parsed["uncollected"], ["S::error"])
        self.assertEqual(parsed["total"], 4)
        self.assertIs(parsed["complete"], True)

    def test_rejects_empty_truncated_or_non_junit_xml(self):
        for xml in (
            "",
            "<testsuite/>",
            '<testsuite name="Example" tests="0"/>',
            '<testsuite name="Example"><testcase name="missingEnd"/>',
            '<testsuite name="Example"><testcase name="works"/></testsuite>trailing',
            '<results><testcase classname="Example" name="works"/></results>',
            '<testsuites><testcase classname="Example" name="works"/></testsuites>',
            '<testsuite name="Example"><error message="setup failed"/></testsuite>',
            '<testsuite name="Example"><testcase name="works"><rerunFailure/></testcase></testsuite>',
            '<?xml version="1.0" encoding="unknown-encoding"?><testsuite name="Example"><testcase name="works"/></testsuite>',
        ):
            with self.subTest(xml=xml), self.assertRaises(ValueError):
                self.parse(xml)

    def test_rejects_doctype_and_entities_including_utf16(self):
        xml = (
            '<!DOCTYPE testsuite [<!ENTITY injected "works">]>'
            '<testsuite name="Example"><testcase name="&injected;"/></testsuite>'
        )
        for data in (xml.encode(), xml.encode("utf-16")):
            with self.subTest(encoding=data[:8]), self.assertRaisesRegex(ValueError, "DOCTYPE"):
                self.parse(data)
        with self.assertRaises(ValueError):
            self.parse('<testsuite name="Example"><testcase name="&undefined;"/></testsuite>')

    def test_rejects_missing_duplicate_or_conflicting_identities(self):
        for body in (
            '<testcase classname="Example"/>',
            '<testcase name=""/>',
            '<testcase name="works"/><testcase name="works"/>',
            '<testcase name="works"><failure/><skipped/></testcase>',
            '<testcase name="works"><failure/><failure/></testcase>',
        ):
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.parse('<testsuite name="Example">' + body + "</testsuite>")
        with self.assertRaises(ValueError):
            self.parse('<testsuite><testcase name="works"/></testsuite>')
        with self.assertRaises(ValueError):
            self.parse(
                '<testsuites><testsuite name="One"><testcase classname="Same" name="works"/></testsuite>'
                '<testsuite name="Two"><testcase classname="Same" name="works"/></testsuite></testsuites>'
            )

    def test_declared_counts_must_match_leaf_outcomes(self):
        for declaration in (
            'tests="2"',
            'tests="-1"',
            'tests="1.0"',
            'failures="1"',
            'errors="1"',
            'skipped="1"',
            'disabled="1"',
        ):
            with self.subTest(declaration=declaration), self.assertRaises(ValueError):
                self.parse(f'<testsuite name="Example" {declaration}><testcase name="works"/></testsuite>')
        with self.assertRaises(ValueError):
            self.parse(
                '<testsuites tests="2"><testsuite name="Example" tests="1">'
                '<testcase name="works"/></testsuite></testsuites>'
            )

    def test_rejects_nonfinite_negative_or_invalid_reported_times(self):
        for time in ("NaN", "Infinity", "-0.1", "1e999", "bad", "", "1_000"):
            for location in ("suite", "case"):
                suite_time = f' time="{time}"' if location == "suite" else ""
                case_time = f' time="{time}"' if location == "case" else ""
                with self.subTest(time=time, location=location), self.assertRaises(ValueError):
                    self.parse(f'<testsuite name="Example"{suite_time}><testcase name="works"{case_time}/></testsuite>')

    def test_report_size_identity_count_and_nesting_are_bounded(self):
        xml = '<testsuite name="Example"><testcase name="works"/></testsuite>'
        with mock.patch.object(junit, "MAX_REPORT_BYTES", len(xml) - 1), self.assertRaises(ValueError):
            self.parse(xml)
        with mock.patch.object(junit, "MAX_TESTS", 1), self.assertRaises(ValueError):
            self.parse('<testsuite name="Example"><testcase name="one"/><testcase name="two"/></testsuite>')
        with mock.patch.object(junit, "MAX_DEPTH", 1), self.assertRaises(ValueError):
            self.parse(
                '<testsuites><testsuite name="outer"><testsuite name="inner">'
                '<testcase name="works"/></testsuite></testsuite></testsuites>'
            )

    def test_prepare_clears_old_report_and_missing_replacement_cannot_count(self):
        self.source.write_text('<testsuite name="Stale"><testcase name="oldPass"/></testsuite>')
        self.assertEqual(junit.prepare(self.tree, "junit:report.xml"), self.source)
        self.assertFalse(self.source.exists())
        with self.assertRaises(ValueError):
            junit.capture(self.tree, "junit:report.xml", self.destination)
        self.assertFalse(self.destination.exists())
        self.assertEqual(junit.prepare(self.tree, "junit:new/nested/report.xml"), self.tree / "new/nested/report.xml")
        self.assertFalse((self.tree / "new").exists())

    def test_tracked_output_is_refused_even_when_missing_and_never_deleted(self):
        with self.assertRaises(ValueError):
            junit.prepare(self.tree, "junit:report.xml", tracked=True)
        self.source.write_text("tracked content")
        with self.assertRaises(ValueError):
            junit.prepare(self.tree, "junit:report.xml", tracked=True)
        self.assertEqual(self.source.read_text(), "tracked content")

    def test_report_symlink_and_symlinked_parent_never_read_or_clear_target(self):
        outside = self.root / "outside.xml"
        outside.write_text('<testsuite name="Outside"><testcase name="works"/></testsuite>')
        self.source.symlink_to(outside)
        for action in (
            lambda: junit.prepare(self.tree, "junit:report.xml"),
            lambda: junit.capture(self.tree, "junit:report.xml", self.destination),
            lambda: junit.results(self.source),
        ):
            with self.assertRaises(ValueError):
                action()
        self.assertTrue(outside.exists())
        (self.tree / "linked").symlink_to(self.root, target_is_directory=True)
        for action in (
            lambda: junit.prepare(self.tree, "junit:linked/outside.xml"),
            lambda: junit.capture(self.tree, "junit:linked/outside.xml", self.destination),
        ):
            with self.assertRaises(ValueError):
                action()
        self.assertIn("Outside", outside.read_text())

    def test_fifo_and_directory_are_refused_without_opening_as_reports(self):
        for kind in ("fifo", "directory"):
            if kind == "fifo":
                os.mkfifo(self.source)
            else:
                self.source.mkdir()
            for action in (
                lambda: junit.prepare(self.tree, "junit:report.xml"),
                lambda: junit.capture(self.tree, "junit:report.xml", self.destination),
                lambda: junit.results(self.source),
            ):
                with self.subTest(kind=kind), self.assertRaises(ValueError):
                    action()
            if kind == "fifo":
                self.source.unlink()
            else:
                self.source.rmdir()

    def test_capture_preserves_exact_bytes_and_survives_scratch_report_removal(self):
        receipt = self.captured()
        metadata = receipt["test_report"]
        self.assertEqual(metadata["format"], "junit")
        self.assertEqual(metadata["spec"], "junit:report.xml")
        self.assertEqual(metadata["path"], str(self.destination))
        self.assertEqual(self.destination.read_bytes(), self.source.read_bytes())
        self.assertEqual(metadata["sha256"], hashlib.sha256(self.source.read_bytes()).hexdigest())
        self.source.unlink()
        self.assertTrue(junit.intact(receipt, root=self.evidence))
        self.assertTrue(junit.intact(receipt))

    def test_capture_refuses_symlink_evidence_and_source_as_destination(self):
        self.source.write_text('<testsuite name="Example"><testcase name="works"/></testsuite>')
        target = self.evidence / "existing.xml"
        target.write_text("preserve")
        self.destination.symlink_to(target)
        with self.assertRaises(ValueError):
            junit.capture(self.tree, "junit:report.xml", self.destination)
        self.assertEqual(target.read_text(), "preserve")
        with self.assertRaises(ValueError):
            junit.capture(self.tree, "junit:report.xml", self.source)

    def test_capture_failure_publishes_no_report_and_removes_temporary_bytes(self):
        self.source.write_text('<testsuite name="Example"><testcase name="works"/></testsuite>')
        with mock.patch.object(junit.os, "replace", side_effect=OSError("write failed")):
            with self.assertRaisesRegex(ValueError, "Could not capture"):
                junit.capture(self.tree, "junit:report.xml", self.destination)
        self.assertEqual(list(self.evidence.iterdir()), [])

    def test_changed_xml_or_results_never_remain_intact(self):
        receipt = self.captured()
        for mutate in (
            lambda row: row["results"].update(complete=1),
            lambda row: row["results"].update(total=True),
            lambda row: row["results"].update(durations={"Example::works": 0}),
            lambda row: row["results"].update(passed=["Example::invented"]),
            lambda row: row["test_report"].update(sha256="0" * 64),
            lambda row: row["test_report"].update(spec="junit:../escape.xml"),
            lambda row: row["test_report"].update(format="other"),
        ):
            changed = copy.deepcopy(receipt)
            mutate(changed)
            with self.subTest(changed=changed):
                self.assertFalse(junit.intact(changed, root=self.evidence))
        self.destination.write_text('<testsuite name="Example"><testcase name="invented"/></testsuite>')
        self.assertFalse(junit.intact(receipt, root=self.evidence))

    def test_artifact_missing_outside_root_or_symlinked_fails_closed(self):
        receipt = self.captured()
        self.assertFalse(junit.intact(receipt, root=self.tree))
        alias = self.evidence / "alias"
        alias.symlink_to(self.evidence, target_is_directory=True)
        changed = copy.deepcopy(receipt)
        changed["test_report"]["path"] = str(alias / self.destination.name)
        self.assertFalse(junit.intact(changed, root=self.evidence))
        self.destination.unlink()
        self.assertFalse(junit.intact(receipt, root=self.evidence))
        self.destination.symlink_to(self.source)
        self.assertFalse(junit.intact(receipt, root=self.evidence))
        for invalid in ({}, {"test_report": None}, {"test_report": {}}, None):
            with self.subTest(invalid=invalid):
                self.assertFalse(junit.intact(invalid, root=self.evidence))


if __name__ == "__main__":
    unittest.main()
