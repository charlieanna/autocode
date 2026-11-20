"""The status view's ``recoveries``: what went wrong in a run and what AutoCode did about it."""

import copy
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
                "identity": {"stage": "builder", "error_class": "PAUSED_ASSIGNMENT_SCOPE"},
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
                "stage": "builder",
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
            {"stage": "builder", "finished_at": "2026-11-02T05:40:00+00:00"},
            {
                "stage": "resolver",
                "engine": "runner",
                "finished_at": "2026-11-02T05:45:55+00:00",
                "decision": {"action": "retry", "rationale": "Terminal report failure eligible for report-only repair"},
            },
        ],
    }
    return {**base, **keys}


class RecoveryHistoryTests(unittest.TestCase):
    def test_source_recovery_is_visible_without_claiming_tests_or_approval(self):
        saved = {
            "reconciliation_notes": [
                {
                    "kind": "source_access_recovered",
                    "actor": "runner",
                    "at": "2026-11-18T10:53:17Z",
                    "source_revision": "a" * 64,
                    "reason": "Git inventory could not be read",
                    "allowances_renewed": False,
                }
            ]
        }
        before = copy.deepcopy(saved)
        rows = history.project(saved)
        self.assertEqual(1, len(rows))
        self.assertEqual(
            ("source_access_recovered", "AutoCode", True), (rows[0]["kind"], rows[0]["by"], rows[0]["handled"])
        )
        self.assertIn("limits", rows[0]["action"])
        self.assertIn("not", rows[0]["action"])
        import autocode_util as util

        schema = json.loads((ROOT / "docs/schemas/status-view.schema.json").read_text())
        util.validate_schema(rows, schema["properties"]["view"]["properties"]["recoveries"])
        self.assertEqual(before, saved)

    def test_source_notes_without_a_confirmed_read_are_not_successful_recoveries(self):
        for changes in ({"source_revision": None}, {"allowances_renewed": True}, {"actor": "unknown"}):
            note = {
                "kind": "source_access_recovered",
                "actor": "runner",
                "source_revision": "a" * 64,
                "allowances_renewed": False,
                **changes,
            }
            self.assertEqual([], history.project({"reconciliation_notes": [note]}))

    def test_a_run_that_never_recovered_has_none(self):
        self.assertEqual([], history.project({}))
        self.assertEqual([], history.project({"history": [{"stage": "builder"}], "regression_proofs": []}))

    def test_each_recovery_says_which_stage_what_went_wrong_and_what_autocode_did_oldest_first(self):
        # The Builder ran again after the Investigator finished, so the investigation says it tried again (#3927).
        rows = history.project(state(stages=[{"stage": "builder", "started_at": "2026-11-02T05:42:10+00:00"}]))
        self.assertEqual(
            [
                ("rejected", "Investigator", "Investigator answer rejected (2 times)"),
                ("report_repaired", "Investigator", "Investigator rewrote its report"),
                ("sent_back", "Builder", "Builder sent back for untested lines"),
                ("investigated", "Builder", "Builder stuck"),
                ("resolver_decision", "Resolver", "Resolver: try the stage again"),
            ],
            [(row["kind"], row["job"], row["headline"]) for row in rows],
        )
        rejected, repaired, sent_back, stuck, resolved = rows
        self.assertEqual("A reproduced bug needs exactly one of probe or untestable", rejected["problem"])
        self.assertTrue(repaired["handled"])
        self.assertFalse(resolved["handled"])
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
            "The Resolver's rules, with no model call, chose to try the stage again: "
            "Terminal report failure eligible for report-only repair",
            resolved["action"],
        )
        self.assertEqual(len({row["id"] for row in rows}), len(rows))
        self.assertEqual(set(history.project(state())[0]), set(rows[0]))  # every row has the same keys

    def test_a_resolver_hold_is_a_stop_with_its_cause_and_command_hints_removed(self):
        run = state(
            history=[
                {
                    "stage": "resolver",
                    "engine": "runner",
                    "finished_at": "2026-11-02T05:46:00+00:00",
                    "decision": {
                        "action": "hold",
                        "rationale": (
                            "AutoResolver cannot safely resolve this blocker under the current authority. "
                            "Automatic recovery budget exhausted; no further provider will launch. "
                            "Last cause: No new provider activity within the inactivity limit "
                            "(300 seconds, runner default; change it with "
                            "autocode resume --max-idle-seconds N). AutoResolver retained the diagnosis "
                            "and failure history; this is an operational stop, not a request for approval. "
                            "After fixing the cause, authorize more recoveries explicitly with "
                            "autocode resume --grant-recovery N."
                        ),
                    },
                }
            ]
        )
        held = next(row for row in history.project(run) if row["by"] == "Resolver")

        self.assertEqual(("resolver_decision", "Resolver: stop here"), (held["kind"], held["headline"]))
        self.assertEqual("No new provider activity within the inactivity limit", held["problem"])
        self.assertIn("chose to stop here", held["action"])
        self.assertNotIn("autocode resume", json.dumps(held))
        self.assertNotIn("--max-idle-seconds", json.dumps(held))
        self.assertFalse(held["handled"])

    def test_a_retry_reports_the_failed_attempt_cause_without_claiming_success(self):
        run = state(
            history=[
                {
                    "stage": "resolver",
                    "engine": "runner",
                    "finished_at": "2026-11-02T05:46:00+00:00",
                    "decision": {
                        "action": "retry",
                        "rationale": "AutoResolver retained the stopped attempt and its diagnosis.",
                    },
                    "receipt": {
                        "evidence": {
                            "timeout_reason": (
                                "No new provider activity within the inactivity limit "
                                "(300 seconds; see autocode resume --max-idle-seconds N)."
                            )
                        }
                    },
                }
            ]
        )
        retried = next(row for row in history.project(run) if row["by"] == "Resolver")

        self.assertEqual("No new provider activity within the inactivity limit.", retried["problem"])
        self.assertIn("chose to try the stage again", retried["action"])
        self.assertFalse(retried["handled"])

    def test_a_command_hint_in_parentheses_does_not_hide_the_cause(self):
        reason = (
            "Last cause: No new provider activity within the inactivity limit "
            "(300 seconds, runner default; change it with autocode resume --max-idle-seconds N)."
        )

        shown = history.explain(reason)

        self.assertIn("Last cause: No new provider activity within the inactivity limit", shown)
        self.assertNotIn("autocode resume", shown)
        self.assertNotIn("--max-idle-seconds", shown)

    def test_a_model_resolver_decision_is_not_called_a_rule(self):
        model_run = state(
            history=[
                {
                    "stage": "resolve",
                    "engine": "opencode",
                    "finished_at": "2026-11-02T05:46:00+00:00",
                    "decision": {"action": "replan", "rationale": "The milestone order blocks M2"},
                }
            ]
        )
        resolved = next(row for row in history.project(model_run) if row["kind"] == "resolver_decision")

        self.assertEqual("The Resolver chose to revise the plan: The milestone order blocks M2", resolved["action"])
        self.assertEqual("The milestone order blocks M2", resolved["problem"])
        self.assertFalse(resolved["handled"])

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

    def test_ac4_schema_rejections_are_summarized_for_a_person(self):
        run = {
            "failure_history": {
                "a": {
                    "identity": {"stage": "investigate_bug", "error_class": "ValueError"},
                    "last_error": "$: missing counterexample",
                    "last_seen": "2026-11-02T05:33:44+00:00",
                    "count": 3,
                }
            },
            "stuck_investigations": [
                {
                    "stage": "requirements_gather",
                    "requested_at": "2026-11-02T05:34:00+00:00",
                    "finished_at": "2026-11-02T05:35:00+00:00",
                    "outcome": "retried",
                    "reason": "Completed requirements_gather_report_repair output was rejected ($: missing message_id; "
                    "$: missing contract_token; $: unexpected fields: acceptance_tests, access_blockers, conflicts); "
                    "attempt archived. Resume explicitly with autocode resume.",
                }
            ],
        }
        before = copy.deepcopy(run)
        rows = history.project(run)
        rejected = next(row for row in rows if row["kind"] == "rejected")
        investigated = next(row for row in rows if row["kind"] == "investigated")
        self.assertEqual(
            "The answer was not in the format AutoCode requires: 1 required part missing.", rejected["problem"]
        )
        self.assertEqual("Investigator answer rejected (3 times)", rejected["headline"])
        self.assertEqual(
            "The answer was not in the format AutoCode requires: 2 required parts missing, 3 parts it does not take.",
            investigated["problem"],
        )
        for row in rows:
            for value in row.values():
                for raw in ("$:", "missing counterexample", "unexpected fields"):
                    self.assertNotIn(raw, str(value))
        self.assertEqual(before["failure_history"], run["failure_history"])
        self.assertEqual(before["stuck_investigations"], run["stuck_investigations"])

    def test_a_requirement_quoting_words_the_person_never_wrote_is_said_plainly(self):
        # The 2026-11-11 run: Requirements picked an issue and quoted its body as the person's words.
        reason = (
            "Requirements R3, R4, R9: source_quote is not in the task or a saved user event. "
            'R3 quotes "A valid plan\'s completion order is lost in the VS Code render model before display.". '
            "For each, copy the source text character for character if it says this requirement; otherwise "
            "remove that requirement (and every reference to its id), or record it as a proposed_assumption"
        )
        self.assertEqual(
            "it listed R3, R4, R9 as things you asked for, but you never wrote the words it quoted for them.",
            history.explain(reason),
        )
        self.assertEqual(
            "it listed R2 as things you asked for, but you never wrote the words it quoted for them.",
            history.explain("Requirement R2 source_quote is not in the task or a saved user event. R2 quotes x"),
        )
        self.assertEqual(
            "its correction to the earlier answer was not in a form AutoCode can apply.",
            history.explain("Report edit value_json must be a bounded JSON string"),
        )
        self.assertIsNone(history.explain(None))

    def test_ac5_plain_rejection_reasons_stay_as_they_are(self):
        rows = history.project(state())
        investigated = next(row for row in rows if row["kind"] == "investigated")
        self.assertEqual(SCOPE, investigated["problem"])
        self.assertNotIn("autocode resume", json.dumps(rows))

    def test_ac14_every_schema_error_class_is_summarized_and_rendered_without_raw_text(self):
        import autocode_chat_recoveries as recoveries

        def rejected(last_error):
            return {
                "failure_history": {
                    "s": {
                        "identity": {"stage": "tester"},
                        "count": 1,
                        "last_seen": "2026-11-02T05:50:00+00:00",
                        "last_error": last_error,
                    }
                }
            }

        mixed = rejected(
            "$: unexpected fields: extra; $.verdict: invalid enum; $.findings[0]: missing id; "
            "$.findings[0].severity: invalid enum; $.summary: expected string"
        )
        many = rejected("; ".join(f"$: missing k{index:02d}" for index in range(20)) + "; and 5 more")
        stuck = {
            "stuck_investigations": [
                {
                    "stage": "plan_task",
                    "requested_at": "2026-11-02T05:51:00+00:00",
                    "finished_at": "2026-11-02T05:52:00+00:00",
                    "outcome": "retried",
                    "reason": "Completed astra_plan_report_repair output was rejected ($.milestones: too few items; "
                    "$: unexpected fields: notes); attempt archived. Resume explicitly with autocode resume.",
                }
            ]
        }
        cases = (
            (
                mixed,
                "rejected",
                "The answer was not in the format AutoCode requires: "
                "1 required part missing, 1 part it does not take, 3 other mismatches.",
                "Tester answer rejected",
            ),
            (
                many,
                "rejected",
                "The answer was not in the format AutoCode requires: 20 required parts missing, 5 other mismatches.",
                None,
            ),
            (
                stuck,
                "investigated",
                "The answer was not in the format AutoCode requires: 1 part it does not take, 1 other mismatch.",
                "Planner stuck",
            ),
        )
        for run, kind, sentence, headline in cases:
            with self.subTest(kind=kind, sentence=sentence):
                before = {key: copy.deepcopy(run.get(key)) for key in ("failure_history", "stuck_investigations")}
                rows = history.project(run)
                row = next(row for row in rows if row["kind"] == kind)
                self.assertEqual(sentence, row["problem"])
                if headline:
                    self.assertEqual(headline, row["headline"])
                raws = ("$", "invalid enum", "unexpected fields", "too few items", "expected string", "missing k")
                for text in (recoveries.notice(row), recoveries.why({"recoveries": rows})):
                    self.assertIn(sentence, text)
                    for raw in raws:
                        self.assertNotIn(raw, text)
                self.assertEqual(before["failure_history"], run.get("failure_history"))
                self.assertEqual(before["stuck_investigations"], run.get("stuck_investigations"))

    def test_a_send_back_names_its_round_out_of_the_rounds_the_gate_allows(self):
        # The gate records the round it took (autocode_changed_code_rework); an older record has none,
        # and the Investigator's record is not a Builder round.
        rework = state()["regression_proofs"][1]
        first = {**rework, "round": 1, "round_limit": 2}
        second = {**rework, "at": "2026-11-02T05:45:00+00:00", "round": 2, "round_limit": 2}
        third = {**rework, "at": "2026-11-02T05:50:00+00:00", "action": "investigator", "round": 3, "round_limit": 2}
        rows = [
            row
            for row in history.project(state(regression_proofs=[first, second, third]))
            if row["kind"] == "sent_back"
        ]
        self.assertEqual(
            [
                "Builder sent back for untested lines (round 1 of 2)",
                "Builder sent back for untested lines (round 2 of 2)",
                "Builder sent back for untested lines",
            ],
            [row["headline"] for row in rows],
        )
        legacy = history.project(state())
        self.assertIn("Builder sent back for untested lines", [row["headline"] for row in legacy])

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
                        "identity": {"stage": "tester"},
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

        current = state(
            status="RUNNING",
            phase="EXECUTING",
            reconciliation_notes=[
                {
                    "kind": "source_access_recovered",
                    "actor": "runner",
                    "source_revision": "a" * 64,
                    "at": "2026-11-18T10:53:17Z",
                    "allowances_renewed": False,
                }
            ],
            user_events=[{"kind": "automatic_interruption_recovery", "stage": "tester", "at": "2026-11-16T10:00:00Z"}],
            retained_candidate_handoffs=[
                {
                    "at": "2026-11-02T05:46:00+00:00",
                    "accepted_test_writer": "writer.json",
                    "reconsidered": True,
                }
            ],
        )
        shown = run_view.view(current)
        self.assertEqual(history.project(current), shown["recoveries"])
        schema = json.loads((ROOT / "docs/schemas/status-view.schema.json").read_text())
        view = schema["properties"]["view"]
        self.assertIn("recoveries", view["required"])
        item = view["properties"]["recoveries"]["items"]
        self.assertEqual(sorted(item["required"]), sorted(shown["recoveries"][0]))
        self.assertEqual(
            sorted(item["properties"]["kind"]["enum"]), sorted({row["kind"] for row in shown["recoveries"]})
        )
        self.assertEqual("boolean", item["properties"]["handled"]["type"])


# The #3927 incident: the test writer finished at 02:26:03, the Investigator ran from 02:26:26 to 02:32:35.
TESTER_IDENTITY = "write_acceptance_tests:PAUSED_INVALID_OUTPUT"
RECHECKED = (
    "AutoCode called the Investigator, which found the cause, and AutoCode re-checked the saved tests "
    "without running the Tester again."
)
TRIED_AGAIN = "AutoCode called the Investigator, which found the cause, and the Tester tried again with its advice."
NOT_RUN_YET = "AutoCode called the Investigator, which found the cause; the Tester has not run again yet."


def tester_investigation(requested_at, finished_at, **keys):
    return {
        "identity": TESTER_IDENTITY,
        "stage": "write_acceptance_tests",
        "status": "PAUSED_INVALID_OUTPUT",
        "reason": "Acceptance verification retry could not finish: original-reference execution missing",
        "requested_at": requested_at,
        "finished_at": finished_at,
        "outcome": "retried",
        "cause": "environment",
        "diagnosis": "The saved test imports the public module at collection time.",
        "guidance": "Keep the public import and move it into the test body",
        **keys,
    }


def recheck_note(at):
    return {
        "at": at,
        "stage": "write_acceptance_tests",
        "reason": "Retrying local acceptance verification without another model call",
        "output": "/run/iterations/001/acceptance-tests-01.json",
        "attempt": 2,
    }


def incident(*, rechecked=True, archived=False, **keys):
    """The saved run behind #3927, in its own records' shapes: no Tester attempt after the Investigator."""
    retry = {"attempts": 2 if rechecked else 1, "investigations": [TESTER_IDENTITY] if rechecked else []}
    run = {
        "stuck_investigations": [tester_investigation("2026-11-20T02:26:26+00:00", "2026-11-20T02:32:35+00:00")],
        "stages": [
            {
                "stage": "write_acceptance_tests",
                "started_at": "2026-11-20T02:10:00+00:00",
                "finished_at": "2026-11-20T02:26:03+00:00",
            },
            {
                "stage": "investigate_stuck",
                "started_at": "2026-11-20T02:26:26+00:00",
                "finished_at": "2026-11-20T02:32:35+00:00",
            },
        ],
        "acceptance_tests": {"verification_retries": [retry]} if archived else {"verification_retry": retry},
    }
    if rechecked:
        run["reconciliation_notes"] = [recheck_note("2026-11-20T02:32:40+00:00")]
    run.update(keys)
    return run


def investigated(run):
    return [row for row in history.project(run) if row["kind"] == "investigated"]


class InvestigationOutcomeTests(unittest.TestCase):
    """An investigation's entry says what actually ran after the Investigator (#3927)."""

    def test_ac10_investigation_history_says_saved_tests_rechecked(self):
        for archived in (False, True):
            with self.subTest(archived=archived):
                (row,) = investigated(incident(archived=archived))
                self.assertEqual(RECHECKED, row["action"])
                self.assertNotIn("tried again with its advice", row["action"])

    def test_ac11_investigation_history_says_stage_not_run_yet(self):
        run = incident(rechecked=False)
        self.assertNotIn("reconciliation_notes", run)
        (row,) = investigated(run)
        self.assertEqual(NOT_RUN_YET, row["action"])
        # A re-check recorded before the Investigator finished is not one that ran after it.
        (row,) = investigated(
            incident(rechecked=False, reconciliation_notes=[recheck_note("2026-11-20T02:26:10+00:00")])
        )
        self.assertEqual(NOT_RUN_YET, row["action"])

    def test_ac12_investigation_history_says_tried_again_after_later_attempt(self):
        later = {"stage": "write_acceptance_tests", "started_at": "2026-11-20T02:33:00+00:00"}
        base = incident(rechecked=False)
        for run in (
            {**base, "stages": [*base["stages"], later]},
            {**base, "active_stage": {**later, "iteration": 2}},
        ):
            with self.subTest(active="active_stage" in run):
                (row,) = investigated(run)
                self.assertEqual(TRIED_AGAIN, row["action"])

    def test_ac25_investigation_history_reads_only_its_own_window(self):
        run = incident(
            stuck_investigations=[
                tester_investigation("2026-11-20T02:26:26+00:00", "2026-11-20T02:32:35+00:00"),
                tester_investigation("2026-11-20T02:40:00+00:00", "2026-11-20T02:45:00+00:00", cause="stage_output"),
            ],
        )
        run["stages"].append(
            {
                "stage": "write_acceptance_tests_report_repair",
                "original_stage": "write_acceptance_tests",
                "started_at": "2026-11-20T02:46:00+00:00",
            }
        )
        first, second = investigated(run)
        self.assertEqual(RECHECKED, first["action"])
        self.assertEqual(TRIED_AGAIN, second["action"])


if __name__ == "__main__":
    unittest.main()
