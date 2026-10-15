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
        """An attempt on the declared revision, runtime and profile, with every declared check passing."""
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
            spent_budget={"harness_minutes": 10},
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
            "declared oracle checks missing: maxfail_module": {
                "oracle_checks": [{"name": "stepwise_module", "ok": True}]
            },
            "no oracle checks recorded": {"oracle_checks": None},
            "no evidence references": {"evidence_refs": []},
            "runtime python '3.14.7' is not the declared '3.14.6'": {"runtime_identity": "python"},
            "frozen dependency record not recorded": {"runtime_identity": "dependencies"},
            "models differ from the declared profile for builder": {"actual_models_and_efforts": "builder"},
            "ran on ": {"source_revision": "c" * 40},
        }
        for note, change in variants.items():
            document = self.fresh()
            row = self.attempt(document, ARENA_ROW)
            if change.get("runtime_identity") == "python":
                row["runtime_identity"]["python"] = "3.14.7"
            elif change.get("runtime_identity") == "dependencies":
                del row["runtime_identity"]["dependencies"]
            elif change.get("actual_models_and_efforts") == "builder":
                row["actual_models_and_efforts"]["models"]["builder"] = "openai/gpt-6-sol"
            else:
                row.update(change)
            with self.subTest(note=note):
                result = self.check(document)
                checked = next(r for r in result["rows"] if r["schedule_id"] == ARENA_ROW)
                self.assertEqual("unverified_pass", checked["outcome"])
                self.assertIn(note, "; ".join(checked["evidence_gaps"] + checked["deviations"]))
                self.assertEqual(0, result["sections"]["live"]["outcomes"]["verified_delivery"])
                self.assertTrue(result["comparison"]["blocked"])

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
        ):
            campaign = copy.deepcopy(self.campaign)
            change(campaign["acceptance"])
            with tempfile.TemporaryDirectory() as tmp:
                path = Path(tmp) / "campaign.json"
                path.write_text(json.dumps(campaign))
                with self.subTest(name), self.assertRaisesRegex(ledger.LedgerError, pattern):
                    ledger.load_campaign(path)

    def test_render_lists_every_attempt_and_never_shows_unknown_as_zero(self):
        document = self.fresh()
        self.attempt(document, ARENA_ROW, estimated_usd=None, spent_budget=None)
        text = ledger.render(self.check(document))
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
            path.write_text(out.getvalue())
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
            path.write_text('{"schema_version": 1, "schema_version": 1}')
            with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                ledger.main(["report", str(CAMPAIGN), str(path)])


if __name__ == "__main__":
    unittest.main()
