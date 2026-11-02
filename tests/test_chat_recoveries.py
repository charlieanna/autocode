"""What went wrong and what AutoCode did, as the chat says it: a notice, ``/why``, the side panel and ``/details``."""

import unittest

import autocode_chat_panel as panel
import autocode_chat_recoveries as recoveries
import autocode_chat_tui as tui


def row(
    headline,
    *,
    kind="rejected",
    at="2026-11-02T05:33:44+00:00",
    problem=None,
    action="AutoCode did not accept it.",
    by=None,
    detail=None,
):
    return {
        "id": headline,
        "kind": kind,
        "at": at,
        "job": headline.split()[0],
        "headline": headline,
        "problem": problem,
        "action": action,
        "by": by or headline.split()[0],
        "detail": detail,
    }


STUCK = row(
    "Builder stuck",
    kind="investigated",
    at="2026-11-02T05:41:01+00:00",
    problem="Builder attempts changed files outside the assigned paths.",
    action="AutoCode called the Investigator, which found the cause, and the Builder tried again with its advice.",
    by="Investigator",
    detail="The Builder ran capture from client/v3. Run capture from the workspace root.",
)
RESOLVED = row(
    "Resolver: try the stage again",
    kind="resolved",
    at="2026-11-02T05:45:55+00:00",
    action="The Resolver chose to try the stage again: Terminal report failure eligible for report-only repair",
    by="Resolver",
)
REJECTED = row(
    "Investigator answer rejected (2 times)", problem="A reproduced bug needs exactly one of probe or untestable"
)


def view(*rows, status="RUNNING"):
    return {"status": status, "recoveries": list(rows)}


class RecoveryTextTests(unittest.TestCase):
    def test_a_notice_says_which_stage_what_went_wrong_and_what_autocode_did(self):
        self.assertEqual(
            "Recovery · Builder stuck\n"
            "Builder attempts changed files outside the assigned paths.\n"
            "AutoCode called the Investigator, which found the cause, and the Builder tried again with its advice.",
            recoveries.notice(STUCK),
        )
        self.assertEqual("Recovery · Resolver: try the stage again\n" + RESOLVED["action"], recoveries.notice(RESOLVED))
        # The transcript draws it as a notice, not as plain output.
        self.assertEqual("notice", tui.role_of("\n" + recoveries.notice(STUCK)))

    def test_why_lists_each_one_with_the_investigators_findings_and_when_each_job_is_called(self):
        said = recoveries.why(view(REJECTED, STUCK, RESOLVED))
        blocks = said.split("\n\n")
        self.assertEqual(
            "05:41:01 Builder stuck\n"
            "  What went wrong: Builder attempts changed files outside the assigned paths.\n"
            f"  Then: {STUCK['action']}\n"
            "  What it found: The Builder ran capture from client/v3. Run capture from the workspace root.",
            blocks[1],
        )
        self.assertTrue(blocks[0].startswith("05:33:44 Investigator answer rejected (2 times)"))
        self.assertEqual([recoveries.WHY_CALLED["Investigator"], recoveries.WHY_CALLED["Resolver"]], blocks[-2:])
        # A run where only a rejected answer happened explains neither job, because neither ran.
        self.assertNotIn("Why the", recoveries.why(view(row("Tester answer rejected"))))
        self.assertIn("Nothing has gone wrong", recoveries.why(view()))
        self.assertIn("Nothing has gone wrong", recoveries.why({"recoveries": [{"id": 1}]}))

    def test_the_panel_lists_the_newest_first_and_details_points_at_why(self):
        self.assertIsNone(recoveries.compact(view()))
        self.assertEqual(
            (
                "3 so far · /why",
                ["05:45 Resolver: try the stage again", "05:41 Builder stuck", "05:33 " + REJECTED["headline"]],
            ),
            recoveries.compact(view(REJECTED, STUCK, RESOLVED)),
        )
        self.assertEqual(
            recoveries.PANEL_ROWS, len(recoveries.compact(view(*(row(f"Tester {n}") for n in range(9))))[1])
        )
        self.assertEqual(
            'What went wrong: 1 time so far, most recently "Builder stuck". /why shows each one and what AutoCode did.',
            recoveries.details(view(STUCK)),
        )
        self.assertEqual("", recoveries.details(view()))


class PanelTests(unittest.TestCase):
    def test_the_side_panel_has_a_what_went_wrong_section_newest_first(self):
        shown = {section.title: section for section in panel.sections(view(REJECTED, STUCK))}
        section = shown["What went wrong"]
        self.assertEqual("2 so far · /why", section.summary)
        # AutoCode handled each one, so none wears the panel's "needs you" mark.
        self.assertEqual(
            (panel.Row("↻", "05:41 Builder stuck", "plain"), panel.Row("↻", "05:33 " + REJECTED["headline"], "dim")),
            section.rows,
        )
        self.assertEqual("~", panel.ascii_mark(section.rows[0].mark))
        self.assertNotIn("What went wrong", [section.title for section in panel.sections(view())])

    def test_why_is_a_run_command_and_details_mentions_what_went_wrong(self):
        self.assertIn("/why", [command.name for command in panel.commands(asking=False)])
        self.assertIn("/why", panel.RUN_COMMANDS)
        log = panel.StageLog()
        self.assertEqual(recoveries.why(view(STUCK)), panel.text("/why", view(STUCK), log))
        self.assertIn('most recently "Builder stuck"', panel.text("/details", view(STUCK), log))


if __name__ == "__main__":
    unittest.main()
