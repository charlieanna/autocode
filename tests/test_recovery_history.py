"""The status view's ``recoveries``: what went wrong in a run and what AutoCode did about it."""

import json
import unittest
from pathlib import Path

import autocode_recovery_history as history

ROOT = Path(__file__).resolve().parents[1]

SCOPE = (
    "Builder attempts for this task changed files outside the assigned paths; the runner removed the files "
    "they created, so a retry starts without them: client/v3/.autocode/evidence/resolver-tests.json"
)


def state(**keys):
    """A run that recovered the way the etcd run did, in its own records' shapes."""
    base = {
        "failure_history": {
            "a": {
                "identity": {"stage": "investigate_bug", "error_class": "ValueError"},
                "last_error": "A reproduced bug needs exactly one of probe or untestable",
                "last_seen": "2026-11-02T05:33:44+00:00",
                "count": 2,
            },
            "b": {
                "identity": {"stage": "terra", "error_class": "PAUSED_ASSIGNMENT_SCOPE"},
                "last_error": SCOPE,
                "last_seen": "2026-11-02T05:41:01+00:00",
                "count": 1,
            },
        },
        "report_repair_history": [
            {
                "at": "2026-11-02T05:34:12+00:00",
                "attempts": 2,
                "result": "accepted",
                "repair": {"stage": "investigate_bug_report_repair", "original_stage": "investigate_bug"},
            }
        ],
        "regression_proofs": [
            {"kind": "proof", "at": "2026-11-02T05:39:00+00:00"},
            {
                "kind": "changed_code_rework",
                "at": "2026-11-02T05:39:53+00:00",
                "action": "builder",
                "assessment": {
                    "reason": "Changed product lines still need behavioral evidence: resolver.go:48, resolver.go:56",
                    "obligations": [
                        {"path": "resolver.go", "line": line, "status": "reason_required"}
                        for line in (48, 56, 57, 58, 77)
                    ],
                },
            },
        ],
        "stuck_investigations": [
            {
                "stage": "terra",
                "requested_at": "2026-11-02T05:41:01.5+00:00",
                "finished_at": "2026-11-02T05:41:55+00:00",
                "outcome": "retried",
                "reason": f"Completed terra output was rejected ({SCOPE}); attempt archived. "
                "Resume explicitly with autocode resume to retry with a fresh request.",
                "diagnosis": "The Builder ran capture from client/v3, so its receipt landed outside its paths.",
                "guidance": "Run capture from the workspace root.",
            }
        ],
        "history": [
            {"stage": "terra", "finished_at": "2026-11-02T05:40:00+00:00"},
            {
                "stage": "resolver",
                "finished_at": "2026-11-02T05:45:55+00:00",
                "decision": {"action": "retry", "rationale": "Terminal report failure eligible for report-only repair"},
            },
        ],
    }
    return {**base, **keys}


class RecoveryHistoryTests(unittest.TestCase):
    def test_a_run_that_never_recovered_has_none(self):
        self.assertEqual([], history.project({}))
        self.assertEqual([], history.project({"history": [{"stage": "terra"}], "regression_proofs": []}))

    def test_each_recovery_says_which_stage_what_went_wrong_and_what_autocode_did_oldest_first(self):
        rows = history.project(state())
        self.assertEqual(
            [
                ("rejected", "Investigator", "Investigator answer rejected (2 times)"),
                ("report_repaired", "Investigator", "Investigator rewrote its report"),
                ("sent_back", "Builder", "Builder sent back for untested lines"),
                ("investigated", "Builder", "Builder stuck"),
                ("resolved", "Resolver", "Resolver: try the stage again"),
            ],
            [(row["kind"], row["job"], row["headline"]) for row in rows],
        )
        rejected, repaired, sent_back, stuck, resolved = rows
        self.assertEqual("A reproduced bug needs exactly one of probe or untestable", rejected["problem"])
        self.assertIn("It took 2 tries. AutoCode accepted the rewritten report.", repaired["action"])
        self.assertEqual(
            "Changed lines have no test that fails when they break, and no checked reason: resolver.go lines 48, 56-58, 77.",
            sent_back["problem"],
        )
        self.assertEqual("AutoCode sent the Builder back to test these lines or give a reason.", sent_back["action"])
        self.assertEqual("Investigator", stuck["by"])
        self.assertIn("tried again with its advice", stuck["action"])
        self.assertEqual(
            "The Builder ran capture from client/v3, so its receipt landed outside its paths. "
            "Run capture from the workspace root.",
            stuck["detail"],
        )
        self.assertEqual(
            "The Resolver chose to try the stage again: Terminal report failure eligible for report-only repair",
            resolved["action"],
        )
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        self.assertEqual(set(history.project(state())[0]), set(rows[0]))  # every row has the same keys

    def test_an_investigation_explains_the_rejection_it_was_called_for_and_drops_internal_text(self):
        stuck = next(row for row in history.project(state()) if row["kind"] == "investigated")
        # The rejection the Investigator was called for is said once, with it, in the stage's own words:
        # no code name ("Completed terra output") and no command for a person to type.
        self.assertEqual(SCOPE, stuck["problem"])
        self.assertNotIn("autocode resume", json.dumps(history.project(state())))
        self.assertEqual(1, sum(row["problem"] == SCOPE for row in history.project(state())))
        # Before the Investigator's record exists, the rejection itself is the news.
        waiting = history.project(state(stuck_investigations=[]))
        self.assertEqual(
            ["Builder answer rejected"],
            [row["headline"] for row in waiting if row["job"] == "Builder" and row["problem"] == SCOPE],
        )

    def test_a_third_send_back_names_the_investigator_and_carries_its_guidance(self):
        rework = state()["regression_proofs"][1]
        third = {
            **rework,
            "at": "2026-11-02T05:50:00+00:00",
            "action": "investigator",
            "investigation": {"guidance": "Bind TestT4 to line 48."},
        }
        row = history.project(state(regression_proofs=[rework, third]))[-1]
        self.assertEqual("Investigator", row["by"])
        self.assertIn("called the Investigator", row["action"])
        self.assertEqual("Bind TestT4 to line 48.", row["detail"])

    def test_text_is_bounded_redacted_and_the_list_keeps_only_the_newest(self):
        long = "x" * 2000
        secret = "sk-ant-api03-" + "A" * 40
        rows = history.project(
            {
                "failure_history": {
                    str(index): {
                        "identity": {"stage": "sol"},
                        "last_error": f"{long} {secret}" if index == 29 else f"problem {index}",
                        "last_seen": f"2026-11-02T06:{index:02d}:00+00:00",
                    }
                    for index in range(30)
                }
            }
        )
        self.assertEqual(history.LIMIT, len(rows))
        self.assertEqual("problem 10", rows[0]["problem"])
        self.assertLessEqual(len(rows[-1]["problem"]), history.TEXT)
        self.assertNotIn(secret, json.dumps(rows))

    def test_the_status_view_carries_it_and_the_schema_declares_it(self):
        import autocode_run_view as run_view

        shown = run_view.view(state(status="RUNNING", phase="EXECUTING"))
        self.assertEqual(history.project(state()), shown["recoveries"])
        schema = json.loads((ROOT / "docs/schemas/status-view.schema.json").read_text())
        view = schema["properties"]["view"]
        self.assertIn("recoveries", view["required"])
        item = view["properties"]["recoveries"]["items"]
        self.assertEqual(sorted(item["required"]), sorted(shown["recoveries"][0]))
        self.assertEqual(
            sorted(item["properties"]["kind"]["enum"]), sorted({row["kind"] for row in shown["recoveries"]})
        )


if __name__ == "__main__":
    unittest.main()
