"""Check a campaign attempt ledger against the pinned declaration and report it (#634).

The ledgers here are synthetic test rows built against the checked-in
declaration; no campaign, oracle or model is run.
"""

from __future__ import annotations

import contextlib
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path

import reliability_ledger as ledger

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "arena" / "campaign.json"
ARENA_ROW = "arena/pytest-stop-fixture-teardown/01"
SCENARIO_ROW = "scenario/bugfix-iso-weeks/02"
FIXTURE_ROW = "scenario/verification-reuse/01"


class LedgerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.campaign, cls.sha = ledger.load_campaign(CAMPAIGN)
        cls.tasks = {task["id"]: task for task in cls.campaign["tasks"]}
        cls.task_of = {row["id"]: cls.tasks[row["task"]] for row in cls.campaign["schedule"]}

    def fresh(self) -> dict:
        return ledger.template(self.campaign, self.sha)

    def row(self, document: dict, sid: str) -> dict:
        return next(row for row in document["attempts"] if row["schedule_id"] == sid)

    def attempt(self, document: dict, sid: str, outcome="PASS", status="TASK_COMPLETE", **changes) -> dict:
        """An attempt on the declared revision, runtime and profile, within its declared limits, with every
        declared check passing."""
        task = self.task_of[sid]
        names = (task.get("upstream") or {}).get("checks") or ["delivered_behaviour"]
        definition = self.campaign["profile"]["definition"]
        runtime = {key: self.campaign["runtime"][key] for key in ledger.RUNTIME_KEYS + ledger.LIVE_RUNTIME_KEYS}
        row = self.row(document, sid)
        row.update(
            runtime_identity={**runtime, "dependencies": "frozen-deps.txt sha256:" + "a" * 64},
            actual_models_and_efforts={key: copy.deepcopy(definition[key]) for key in ("provider", "models", "effort")},
            native_status=status,
            native_outcome=outcome,
            oracle_checks=[{"name": name, "ok": True} for name in names],
            evidence_refs=["autocode bundle sha256:" + "b" * 64],
            elapsed_seconds=600,
            usage={"input_tokens": 1000, "output_tokens": 200},
            estimated_usd=1.5,
            spent_budget={name: limit / 2 for name, limit in task["limits"].items()},
            reason="" if outcome == "PASS" else f"native {outcome}",
        )
        row.update(changes)
        return row

    def check(self, document: dict) -> dict:
        return ledger.report(self.campaign, self.sha, document)

    def refused(self, document: dict, pattern: str):
        with self.assertRaisesRegex(ledger.LedgerError, pattern):
            self.check(document)

    def test_template_accounts_for_every_scheduled_attempt_without_inventing_results(self):
        result = self.check(self.fresh())
        self.assertEqual(26, result["scheduled"])
        self.assertEqual([row["id"] for row in self.campaign["schedule"]], [r["schedule_id"] for r in result["rows"]])
        self.assertEqual(24, result["sections"]["live"]["outcomes"]["unattempted"])
        self.assertEqual(2, result["sections"]["fixture"]["outcomes"]["unattempted"])
        for section in result["sections"].values():
            self.assertEqual(0, section["attempted"])
            self.assertEqual(0, section["outcomes"]["verified_delivery"])
        self.assertTrue(result["comparison"]["blocked"])
        self.assertIn("26 of 26 scheduled attempts have no judgement", result["comparison"]["reasons"])

    def test_missing_duplicate_and_undeclared_attempts_are_refused(self):
        dropped = self.fresh()
        dropped["attempts"].pop()
        self.refused(dropped, "missing from the ledger")
        retried = self.fresh()
        retried["attempts"].append(copy.deepcopy(retried["attempts"][0]))
        self.refused(retried, "appears twice")
        extra = self.fresh()
        extra["attempts"].append({**copy.deepcopy(extra["attempts"][0]), "schedule_id": "arena/sympy/03"})
        self.refused(extra, "not a scheduled attempt")
        unnamed = self.fresh()
        unnamed["attempts"][0]["schedule_id"] = ["arena/sympy-matrix-derivatives/01"]
        self.refused(unnamed, "not a scheduled attempt")

    def test_a_ledger_is_bound_to_the_exact_declaration(self):
        for key, value in (("campaign_sha256", "0" * 64), ("campaign", "another-protocol"), ("schema_version", 2)):
            document = self.fresh()
            document[key] = value
            with self.subTest(key=key):
                self.refused(document, "declaration|schema_version")
        smuggled = self.fresh()
        smuggled["verified"] = True
        self.refused(smuggled, "exactly")

    def test_rows_hold_exactly_the_declared_attempt_fields(self):
        missing = self.fresh()
        del self.row(missing, ARENA_ROW)["spent_budget"]
        self.refused(missing, "missing attempt fields \\['spent_budget'\\]")
        extra = self.fresh()
        self.row(extra, ARENA_ROW)["verified_delivery"] = True
        self.refused(extra, "undeclared fields \\['verified_delivery'\\]")

    def test_fixture_evidence_cannot_be_relabelled_live(self):
        document = self.fresh()
        self.attempt(document, FIXTURE_ROW, execution_kind="live")
        self.refused(document, "execution_kind 'live' is not the declared 'fixture'")

    def test_native_outcomes_are_preserved_per_kind(self):
        for sid, outcome in ((ARENA_ROW, "HONEST_BLOCKER"), (SCENARIO_ROW, "STOPPED"), (SCENARIO_ROW, "VERIFIED")):
            document = self.fresh()
            self.attempt(document, sid, outcome=outcome, status="PAUSED_REQUESTED")
            with self.subTest(sid=sid, outcome=outcome):
                self.refused(document, "is not a (arena|scenario) outcome")

    def test_a_pass_that_contradicts_its_status_or_oracle_is_refused(self):
        stopped = self.fresh()
        self.attempt(stopped, SCENARIO_ROW, status="PAUSED_REQUESTED")
        self.refused(stopped, "contradicts the task's expected complete ending")
        failing = self.fresh()
        row = self.attempt(failing, ARENA_ROW)
        row["oracle_checks"][0]["ok"] = False
        self.refused(failing, "failing oracle check")
        silent = self.fresh()
        self.attempt(silent, ARENA_ROW, outcome="FALSE_COMPLETE", reason="")
        self.refused(silent, "must say why")
        unattempted = self.fresh()
        self.row(unattempted, ARENA_ROW)["oracle_checks"] = [{"name": "maxfail_module", "ok": True}]
        self.refused(unattempted, "DEFERRED row was never attempted")
        unjudged = self.fresh()
        self.attempt(unjudged, ARENA_ROW, outcome="FALSE_COMPLETE", status=None)
        self.refused(unjudged, "must preserve the native status")

    def test_an_attempt_that_spent_anything_cannot_be_recorded_as_deferred(self):
        # A run relabelled DEFERRED would leave the failure counts while its cost stayed in the total.
        for key, value in (("estimated_usd", 2.0), ("elapsed_seconds", 60), ("usage", {}), ("spent_budget", {})):
            document = self.fresh()
            self.row(document, ARENA_ROW)[key] = value
            with self.subTest(key=key):
                self.refused(document, f"DEFERRED row was never attempted, so it records no {key}")

    def test_cost_and_time_are_nonnegative_numbers_or_unknown(self):
        for key, value in (
            ("estimated_usd", -1),
            ("estimated_usd", True),
            ("estimated_usd", "1.5"),
            ("estimated_usd", 10**400),
            ("elapsed_seconds", -0.5),
            ("spent_budget", {"harness_minutes": -1}),
        ):
            document = self.fresh()
            self.attempt(document, ARENA_ROW, **{key: value})
            with self.subTest(key=key, value=str(value)[:20]):
                self.refused(document, "must be a finite nonnegative number or null when unknown")

    def test_each_kind_of_result_is_counted_apart(self):
        document = self.fresh()
        self.attempt(document, ARENA_ROW)
        self.attempt(
            document,
            "arena/pytest-stop-fixture-teardown/02",
            interventions_decision_or_repair=[{"kind": "repair", "note": "operator restored a deleted fixture"}],
        )
        self.attempt(document, SCENARIO_ROW, outcome="FALSE_COMPLETE", estimated_usd=2.0)
        self.attempt(document, "scenario/bugfix-iso-weeks/01", outcome="HONEST_BLOCKER", status="PAUSED_REQUESTED")
        self.attempt(document, "arena/sympy-matrix-derivatives/01", outcome="STOPPED", status="PAUSED_BUDGET")
        self.attempt(document, "arena/sympy-matrix-derivatives/02", outcome="ERROR", status=None, oracle_checks=None)
        self.attempt(
            document,
            "scenario/greenfield-todo-cli/01",
            outcome="INTERRUPTED_UNGRADED",
            status=None,
            oracle_checks=None,
            estimated_usd=None,
            elapsed_seconds=None,
            interventions_decision_or_repair=[{"kind": "decision", "note": "answered the scope question"}],
        )
        result = self.check(document)
        outcomes = {row["schedule_id"]: row["outcome"] for row in result["rows"]}
        self.assertEqual("verified_delivery", outcomes[ARENA_ROW])
        self.assertEqual("repaired_delivery", outcomes["arena/pytest-stop-fixture-teardown/02"])
        self.assertEqual("false_completion", outcomes[SCENARIO_ROW])
        self.assertEqual("stopped", outcomes["scenario/bugfix-iso-weeks/01"])
        self.assertEqual("stopped", outcomes["arena/sympy-matrix-derivatives/01"])
        self.assertEqual("error", outcomes["arena/sympy-matrix-derivatives/02"])
        self.assertEqual("ungraded", outcomes["scenario/greenfield-todo-cli/01"])
        live = result["sections"]["live"]
        self.assertEqual(7, live["attempted"])
        self.assertEqual(1, live["outcomes"]["verified_delivery"])
        self.assertEqual(0, live["outcomes"]["appropriate_stop"], "a stop on a task that expects completion")
        self.assertEqual((1, 1, 1), (live["decisions"], live["repairs"], live["attempts_with_repair"]))
        # Failed attempts' spend counts; the unpriced attempt keeps the total unknown, never zero.
        self.assertEqual(9.5, live["known_usd"])
        self.assertEqual(1, live["unpriced_attempts"])
        self.assertIsNone(live["total_usd"])
        self.assertIsNone(live["usd_per_verified_delivery"])
        self.assertEqual((3600, 600), (live["known_elapsed_seconds"], live["median_elapsed_seconds"]))
        self.assertEqual(1, live["untimed_attempts"])

    def test_cost_per_delivery_includes_every_failed_attempt(self):
        document = self.fresh()
        self.attempt(document, ARENA_ROW, estimated_usd=1.0)
        self.attempt(document, SCENARIO_ROW, outcome="FALSE_COMPLETE", estimated_usd=3.0)
        live = self.check(document)["sections"]["live"]
        self.assertEqual((4.0, 4.0, 0), (live["known_usd"], live["total_usd"], live["unpriced_attempts"]))
        self.assertEqual(4.0, live["usd_per_verified_delivery"])

    def test_a_pass_without_evidence_or_off_the_declared_configuration_is_not_a_delivery(self):
        variants = {
            "declared oracle checks missing: maxfail_module": lambda row: row.update(
                oracle_checks=[{"name": "stepwise_module", "ok": True}]
            ),
            "no oracle checks recorded": lambda row: row.update(oracle_checks=None),
            "no evidence references": lambda row: row.update(evidence_refs=[]),
            "runtime python '3.14.7' is not the declared '3.14.6'": lambda row: row["runtime_identity"].update(
                python="3.14.7"
            ),
            "runtime provider_version '1.18.34' is not the declared '1.18.33'": lambda row: row[
                "runtime_identity"
            ].update(provider_version="1.18.34"),
            "frozen dependency record not recorded": lambda row: row["runtime_identity"].pop("dependencies"),
            "runtime identity not recorded": lambda row: row.update(runtime_identity=None),
            "provider 'claude' is not the declared 'opencode'": lambda row: row["actual_models_and_efforts"].update(
                provider="claude"
            ),
            "models differ from the declared profile for builder": lambda row: row["actual_models_and_efforts"][
                "models"
            ].update(builder="openai/gpt-6-sol"),
            "effort differ from the declared profile for builder": lambda row: row["actual_models_and_efforts"][
                "effort"
            ].update(builder="high"),
            "actual models and efforts not recorded": lambda row: row.update(actual_models_and_efforts=None),
            "ran on ": lambda row: row.update(source_revision="c" * 40),
        }
        for note, change in variants.items():
            document = self.fresh()
            change(self.attempt(document, ARENA_ROW))
            with self.subTest(note=note):
                result = self.check(document)
                checked = next(r for r in result["rows"] if r["schedule_id"] == ARENA_ROW)
                self.assertEqual("unverified_pass", checked["outcome"])
                self.assertIn(note, "; ".join(checked["evidence_gaps"] + checked["deviations"]))
                self.assertEqual(0, result["sections"]["live"]["outcomes"]["verified_delivery"])
                self.assertTrue(result["comparison"]["blocked"])

    def test_spent_budget_is_compared_with_each_declared_limit(self):
        document = self.fresh()
        # bugfix-iso-weeks declares 90 harness minutes and 40 steps.
        self.attempt(document, SCENARIO_ROW, spent_budget={"harness_minutes": 95, "max_steps": 12})
        self.attempt(
            document,
            "scenario/bugfix-iso-weeks/01",
            outcome="FALSE_COMPLETE",
            spent_budget={"harness_minutes": 30, "max_steps": 41},
        )
        self.attempt(document, "scenario/greenfield-todo-cli/01", spent_budget={"harness_minutes": 60})
        self.attempt(document, ARENA_ROW, spent_budget=None)
        self.attempt(document, "arena/pytest-stop-fixture-teardown/02")
        result = self.check(document)
        rows = {row["schedule_id"]: row for row in result["rows"]}
        over = rows[SCENARIO_ROW]
        self.assertEqual(["spent harness_minutes 95, over the declared 90"], over["over_limits"])
        self.assertEqual(
            {
                "harness_minutes": {"declared": 90, "spent": 95, "status": "over"},
                "max_steps": {"declared": 40, "spent": 12, "status": "within"},
            },
            over["limits"],
        )
        self.assertEqual("unverified_pass", over["outcome"], "a PASS over its pinned budget is not a delivery")
        self.assertEqual("false_completion", rows["scenario/bugfix-iso-weeks/01"]["outcome"])
        # Exactly the declared limit is within it; a missing or null spend is unknown, never within.
        greenfield = rows["scenario/greenfield-todo-cli/01"]
        self.assertEqual(("within", "unknown"), tuple(item["status"] for item in greenfield["limits"].values()))
        self.assertEqual("verified_delivery", greenfield["outcome"])
        self.assertEqual({"unknown"}, {item["status"] for item in rows[ARENA_ROW]["limits"].values()})
        self.assertIsNone(rows["arena/sympy-matrix-derivatives/01"]["limits"], "a DEFERRED row spent nothing")
        live = result["sections"]["live"]
        self.assertEqual({"within": 1, "over": 2, "unknown": 2}, live["limits"])
        self.assertIn("2 attempts spent more than a declared limit", result["comparison"]["reasons"])
        text = ledger.render(result)
        line = next(line for line in text.splitlines() if line.startswith(f"| {SCENARIO_ROW} |"))
        self.assertIn("| harness_minutes 95 of 90 (over), max_steps 12 of 40 |", line)
        self.assertIn("spent harness_minutes 95, over the declared 90", line)
        self.assertIn("2 over at least one, 2 with a spend unknown", text)
        arena = next(line for line in text.splitlines() if line.startswith("| arena/pytest-stop-fixture-teardown/02 |"))
        self.assertIn("cli_invocation_seconds 1800 of 3600, oracle_seconds 60 of 120, max_iterations 1.5 of 3", arena)

    def test_a_record_that_does_not_establish_its_commit_keeps_a_null_revision(self):
        document = self.fresh()
        self.attempt(document, ARENA_ROW, source_revision=None)
        self.attempt(document, SCENARIO_ROW, outcome="FALSE_COMPLETE", source_revision=None)
        result = self.check(document)
        rows = {row["schedule_id"]: row for row in result["rows"]}
        self.assertEqual("unverified_pass", rows[ARENA_ROW]["outcome"])
        self.assertIn("source revision not recorded", rows[ARENA_ROW]["deviations"])
        self.assertEqual("false_completion", rows[SCENARIO_ROW]["outcome"])
        self.assertIn(
            "2 attempts differ from the declared revision, runtime or profile", result["comparison"]["reasons"]
        )
        for value in ("8375b73", "c" * 41, ""):
            broken = self.fresh()
            self.attempt(broken, ARENA_ROW, source_revision=value)
            with self.subTest(value=value):
                self.refused(broken, "full 40-character commit")

    def test_comparison_is_blocked_only_while_an_attempt_lacks_a_judgement_or_deviates(self):
        judged = self.fresh()
        for scheduled in self.campaign["schedule"]:
            self.attempt(judged, scheduled["id"])
        self.attempt(judged, "arena/sympy-matrix-derivatives/01", outcome="STOPPED", status="PAUSED_BUDGET")
        result = self.check(judged)
        self.assertEqual({"blocked": False, "reasons": []}, result["comparison"])
        self.assertIn("**Comparison not blocked:**", ledger.render(result))
        errored = copy.deepcopy(judged)
        self.attempt(errored, ARENA_ROW, outcome="ERROR", status=None, oracle_checks=None)
        self.assertEqual(["1 of 26 scheduled attempts have no judgement"], self.check(errored)["comparison"]["reasons"])
        moved = copy.deepcopy(judged)
        self.attempt(moved, ARENA_ROW, outcome="FALSE_COMPLETE", source_revision="c" * 40)
        self.assertEqual(
            ["1 attempts differ from the declared revision, runtime or profile"],
            self.check(moved)["comparison"]["reasons"],
        )

    def test_fixture_rows_are_reported_apart_and_never_pooled_with_live_rows(self):
        document = self.fresh()
        row = self.attempt(document, FIXTURE_ROW)
        row["actual_models_and_efforts"] = {"provider": "scripted-fault"}
        row["runtime_identity"].update(provider="fake", provider_version="0")
        result = self.check(document)
        self.assertEqual(1, result["sections"]["fixture"]["outcomes"]["verified_delivery"])
        self.assertEqual(0, result["sections"]["live"]["outcomes"]["verified_delivery"])
        self.assertEqual(0, result["sections"]["live"]["attempted"])
        text = ledger.render(result)
        self.assertIn("It never qualifies a live version.", text)
        self.assertLess(text.index("## Live attempts"), text.index("## Fixture attempts"))
        self.assertLess(text.index("## Fixture attempts"), text.index(f"| {FIXTURE_ROW} |"))

    def test_a_campaign_that_weakens_its_rules_is_not_reported(self):
        for name, change, pattern in (
            ("fixtures", lambda rules: rules.update(fixtures_qualify_live_version=True), "acceptance rules differ"),
            ("unknown cost", lambda rules: rules.update(missing_usage_and_cost=0), "acceptance rules differ"),
            ("spent budget", lambda rules: rules["required_attempt_fields"].remove("spent_budget"), "attempt field"),
            (
                "unclassified outcome",
                lambda rules: rules["native_outcomes"]["arena"].append("VERIFIED"),
                "no portfolio outcome for native outcomes \\['VERIFIED'\\]",
            ),
        ):
            campaign = copy.deepcopy(self.campaign)
            change(campaign["acceptance"])
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "campaign.json"
                path.write_text(json.dumps(campaign))
                with self.subTest(name), self.assertRaisesRegex(ledger.LedgerError, pattern):
                    ledger.load_campaign(path)

    def test_a_campaign_without_usable_limits_is_refused(self):
        for name, change, pattern in (
            ("missing", lambda task: task.pop("limits"), "must declare its limits"),
            ("negative", lambda task: task["limits"].update(max_steps=-1), "finite nonnegative number"),
            ("null", lambda task: task["limits"].update(max_steps=None), "limit max_steps must be a number"),
            ("text", lambda task: task["limits"].update(max_steps="40"), "finite nonnegative number"),
        ):
            campaign = copy.deepcopy(self.campaign)
            change(campaign["tasks"][3])
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "campaign.json"
                path.write_text(json.dumps(campaign))
                with self.subTest(name), self.assertRaisesRegex(ledger.LedgerError, pattern):
                    ledger.load_campaign(path)

    def test_render_lists_every_attempt_and_never_shows_unknown_as_zero(self):
        document = self.fresh()
        self.attempt(document, ARENA_ROW, estimated_usd=None, spent_budget=None)
        self.attempt(document, FIXTURE_ROW, outcome="ERROR", status=None, reason="oracle crashed\r| forged | row |")
        text = ledger.render(self.check(document))
        self.assertNotIn("\r", text, "a carriage return would end the table row")
        self.assertIn("oracle crashed \\| forged \\| row \\|", text)
        for scheduled in self.campaign["schedule"]:
            self.assertEqual(1, text.count(f"| {scheduled['id']} |"), scheduled["id"])
        self.assertIn("**Comparison blocked:**", text)
        self.assertIn("total unknown; known $0.000000; 1 attempts unpriced", text)
        arena_line = next(line for line in text.splitlines() if line.startswith(f"| {ARENA_ROW} |"))
        self.assertIn("| unknown | unknown |", arena_line)
        unrun = next(line for line in text.splitlines() if line.startswith(f"| {SCENARIO_ROW} |"))
        self.assertIn("| not run | not run | not run |", unrun)

    def test_cli_template_round_trips_and_refuses_a_broken_ledger(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ledger.json"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, ledger.main(["template", str(CAMPAIGN)]))
            template = out.getvalue()
            path.write_text(template)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                self.assertEqual(0, ledger.main(["report", str(CAMPAIGN), str(path), "--json"]))
            self.assertEqual(26, json.loads(out.getvalue())["scheduled"])
            document = json.loads(path.read_text())
            document["attempts"].pop()
            path.write_text(json.dumps(document))
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stopped:
                ledger.main(["report", str(CAMPAIGN), str(path)])
            self.assertEqual(2, stopped.exception.code)
            self.assertIn("ledger error: scheduled attempts missing", err.getvalue())
            # Parsers disagree on which of two equal keys wins, so a ledger must not hold both.
            path.write_text(
                template.replace(
                    '"native_outcome": "DEFERRED"', '"native_outcome": "FAIL", "native_outcome": "DEFERRED"', 1
                )
            )
            err = io.StringIO()
            with contextlib.redirect_stderr(err), self.assertRaises(SystemExit) as stopped:
                ledger.main(["report", str(CAMPAIGN), str(path)])
            self.assertEqual(2, stopped.exception.code)
            self.assertIn("duplicate JSON key 'native_outcome'", err.getvalue())


if __name__ == "__main__":
    unittest.main()
