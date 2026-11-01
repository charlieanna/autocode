"""Documentary title identifiers never excuse product values or literal output."""

import unittest

import autocode_source_metadata as metadata


class TicketTitleTests(unittest.TestCase):
    def test_joint_report_wrapper_keeps_the_original_ticket_title_documentary(self):
        prefix = "Implement a joint regression for this original bug report:\n"
        title = "DTI-12398: Missing history for domain 232027272 and result 1000."
        text = prefix + title
        self.assertEqual("DTI-12398", metadata.ticket_title_key(text))
        self.assertEqual(
            prefix + ": Missing history for domain 232027272 and result 1000.",
            metadata.value_text(text, [], ticket_title_key="DTI-12398"),
        )
        self.assertEqual(text, metadata.value_text(text, [], ticket_title_key="OTHER-12398"))

    def test_joint_wrapper_cannot_hide_literal_or_ambiguous_ticket_values(self):
        prefix = "Implement a joint regression for this original bug report:\n"
        for title in (
            "ERR-12398: Error code for invalid requests.",
            "DTI-12398: Print this identifier exactly.",
            "DTI-12398: Missing history\n\nEmit DTI-12398.",
            "`DTI-12398`: Missing history",
        ):
            with self.subTest(title=title):
                self.assertEqual("", metadata.ticket_title_key(prefix + title))
        for prefix in (
            "Print exactly:\nImplement a joint regression for this original bug report:\n",
            "Implement a joint regression for this original bug report: ",
            "An arbitrary heading:\n",
        ):
            with self.subTest(prefix=prefix):
                self.assertEqual("", metadata.ticket_title_key(prefix + "DTI-12398: Missing history"))

    def test_only_a_leading_documentary_ticket_title_identifies_metadata(self):
        for title in (
            "DTI-12398: Incomplete history",
            "\n## APP2-47: Missing history\n\nKeep exit code 7.",
        ):
            with self.subTest(title=title):
                expected = "APP2-47" if "APP2" in title else "DTI-12398"
                self.assertEqual(expected, metadata.ticket_title_key(title))

    def test_ambiguous_codes_literals_and_output_titles_are_not_metadata(self):
        for text in (
            "Handle ERR-12398 when the request fails.",
            "Background\n\nDTI-12398: Incomplete history",
            "`DTI-12398`: Incomplete history",
            "DTI-12398: Print this identifier exactly.",
            "ERR-12398: Error code for invalid requests.",
            "SKU-12398: Product identifier for the replacement item.",
            "DTI-12398: Incomplete history\n\nEmit DTI-12398.",
            "DTI-12398: Incomplete history\n\nUse `DTI-12398` as the key.",
            "Print exactly:\nDTI-12398: Incomplete history",
            "DTI-12398: ",
        ):
            with self.subTest(text=text):
                self.assertEqual("", metadata.ticket_title_key(text))

    def test_value_scan_removes_only_the_identified_title_key(self):
        text = "DTI-12398: Missing history for domain 232027272 and result 1000."
        self.assertEqual(
            ": Missing history for domain 232027272 and result 1000.",
            metadata.value_text(text, [], ticket_title_key="DTI-12398").strip(),
        )
        self.assertEqual(text, metadata.value_text(text, []))
        self.assertEqual(text, metadata.value_text(text, [], ticket_title_key="OTHER-12398"))
        inline = "Handle DTI-12398 as the product error code."
        self.assertEqual(inline, metadata.value_text(inline, [], ticket_title_key="DTI-12398"))

