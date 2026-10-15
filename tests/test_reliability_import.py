"""Fill a campaign ledger's rows from native scenario and Arena records (#634).

The records are small checked-in fixtures under tests/fixtures/reliability. The Arena store's SQLite
ledger is built from its attempts.json in a temporary directory, since SQLite files are binary; an
attempt whose runner identity reads DECLARED_REVISION_IDENTITY gets the declared revision's identity,
pinned below. No scenario, Arena attempt, oracle or model is run.
"""

from __future__ import annotations

import contextlib
import copy
import hashlib
import io
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import reliability_import as importer
import reliability_ledger as ledger
from harness import profiles

ROOT = Path(__file__).resolve().parents[1]
CAMPAIGN = ROOT / "arena" / "campaign.json"
FIXTURES = ROOT / "tests" / "fixtures" / "reliability"
LIVE_PASS = FIXTURES / "scenario-live-pass" / "result.json"
DIRTY = FIXTURES / "scenario-dirty-false-complete" / "result.json"
FAKE = FIXTURES / "scenario-fake-fixture" / "result.json"
RUNTIME = FIXTURES / "runtime-identity.json"
PYTEST_PASS, SYMPY_STOPPED, SYMPY_RUNNING, DJANGO_ELSEWHERE = (
    "5d0e8c1f6a2b4c3d9e7f1a2b3c4d5e6f",
    "8b7a6c5d4e3f2a1b0c9d8e7f6a5b4c3d",
    "0a1b2c3d4e5f60718293a4b5c6d7e8f9",
    "f0e1d2c3b4a5968778695a4b3c2d1e0f",
)
# The declared revision's runner identity: autocode_arena.version_identity of a `git worktree add --detach`
# checkout of that revision, computed apart from the import (a commit's tree never changes). The import must
# compute the same identity from local Git objects. Recompute it if the campaign is pinned to another revision.
DECLARED_REVISION, DECLARED_IDENTITY = (
    "8375b73cf965fd67c6c67dec7103125b56fab431",
    "0a4ad86f8011c4803a9dd20613076b203e98820a8fa48187127d6031e88e0353",
)


class ImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.campaign, cls.sha = ledger.load_campaign(CAMPAIGN)
        cls.definition = cls.campaign["profile"]["definition"]

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="reliability-import-test-"))
        self.addCleanup(shutil.rmtree, self.tmp)
        self.ledger = self.tmp / "ledger.json"
        self.new_ledger()

    def new_ledger(self):
        self.ledger.write_text(json.dumps(ledger.template(self.campaign, self.sha), indent=2) + "\n")

    def arena(self, change=None, cases=None) -> Path:
        """The fixture Arena store, as its attempts were recorded (see the module docstring)."""
        store = self.tmp / "arena"
        shutil.rmtree(store, ignore_errors=True)
        shutil.copytree(FIXTURES / "arena", store)
        if cases:
            catalog = json.loads((store / "catalog.json").read_text())
            cases({case["id"]: case for case in catalog["cases"]})
            (store / "catalog.json").write_text(json.dumps(catalog))
        attempts = json.loads((store / "attempts.json").read_text())
        for row in attempts:
            if row["version_sha256"] == "DECLARED_REVISION_IDENTITY":
                row["version_sha256"] = DECLARED_IDENTITY
        if change:
            change(attempts)
        with contextlib.closing(sqlite3.connect(store / "results.sqlite3")) as db, db:
            db.execute("CREATE TABLE attempts (id TEXT PRIMARY KEY, payload TEXT NOT NULL)")
            for row in attempts:
                db.execute("INSERT INTO attempts VALUES (?, ?)", (row["id"], json.dumps(row, sort_keys=True)))
        return store

    def run_import(self, *args: str, runtime=True) -> tuple[int, str, str]:
        out, err = io.StringIO(), io.StringIO()
        argv = [str(CAMPAIGN), str(self.ledger), *args, *(["--runtime-identity", str(RUNTIME)] if runtime else [])]
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            try:
                code = importer.main(argv)
            except SystemExit as stopped:
                code = stopped.code
        return code, out.getvalue(), err.getvalue()

    def refused(self, pattern: str, *args: str):
        before = self.ledger.read_bytes()
        code, _, err = self.run_import(*args)
        self.assertEqual(2, code, err)
        self.assertRegex(err, pattern)
        self.assertEqual(before, self.ledger.read_bytes(), "a refused import leaves the ledger as it was")

    def rows(self) -> dict:
        return {row["schedule_id"]: row for row in json.loads(self.ledger.read_text())["attempts"]}

    def report(self) -> tuple[dict, dict]:
        """The checked report's rows by schedule ID, and the whole report."""
        result = ledger.report(self.campaign, self.sha, json.loads(self.ledger.read_text()))
        return {row["schedule_id"]: row for row in result["rows"]}, result

    def test_a_live_scenario_pass_is_copied_into_its_row_without_rescoring(self):
        sid = "scenario/bugfix-iso-weeks/01"
        code, out, err = self.run_import("--scenario", sid, str(LIVE_PASS.parent))
        self.assertEqual(0, code, err)
        self.assertEqual(f"{sid}: PASS -> verified_delivery\n", out)
        row = self.rows()[sid]
        self.assertEqual(self.campaign["acceptance"]["required_attempt_fields"], list(row))
        result = json.loads(LIVE_PASS.read_text())
        self.assertEqual(("live", self.campaign["source_revision"]), (row["execution_kind"], row["source_revision"]))
        self.assertEqual(("TASK_COMPLETE", "PASS"), (row["native_status"], row["native_outcome"]))
        self.assertEqual([{"name": c["name"], "ok": c["ok"]} for c in result["checks"]], row["oracle_checks"])
        self.assertEqual(
            {**self.definition, "routes": result["metrics"]["model_routes"]}, row["actual_models_and_efforts"]
        )
        self.assertEqual(json.loads(RUNTIME.read_text()), row["runtime_identity"])
        digest = hashlib.sha256(LIVE_PASS.read_bytes()).hexdigest()
        self.assertEqual([f"scenario result {LIVE_PASS} sha256:{digest}"], row["evidence_refs"])
        self.assertEqual(
            [
                {"kind": "decision", "note": "the harness answered q1 (proposed_default)"},
                {"kind": "decision", "note": "the harness passed the approve_plan gate"},
            ],
            row["interventions_decision_or_repair"],
        )
        self.assertEqual(
            (1500.2, 2.41, result["usage_snapshot"]), (row["elapsed_seconds"], row["estimated_usd"], row["usage"])
        )
        self.assertEqual({"harness_minutes": 25.0033, "max_steps": 12}, row["spent_budget"])
        self.assertEqual(result["summary"], row["reason"])
        template = {r["schedule_id"]: r for r in ledger.template(self.campaign, self.sha)["attempts"]}
        self.assertEqual(
            {key: value for key, value in template.items() if key != sid},
            {key: value for key, value in self.rows().items() if key != sid},
        )

    def test_without_the_admission_record_a_pass_is_not_a_verified_delivery(self):
        code, out, _ = self.run_import("--scenario", "scenario/bugfix-iso-weeks/01", str(LIVE_PASS), runtime=False)
        self.assertEqual(0, code)
        self.assertEqual("scenario/bugfix-iso-weeks/01: PASS -> unverified_pass (runtime identity not recorded)\n", out)

    def test_a_dirty_checkout_an_incomplete_cost_and_an_overrun_stay_visible(self):
        sid = "scenario/ladder-14-transactional-csv-import/01"
        code, out, err = self.run_import("--scenario", sid, str(DIRTY))
        self.assertEqual(0, code, err)
        row = self.rows()[sid]
        self.assertIsNone(row["source_revision"], "uncommitted tools/ changes: the commit is not what ran")
        self.assertIsNone(row["estimated_usd"], "an incomplete cost is a floor, not the attempt's cost")
        self.assertEqual({"harness_minutes": 40.2083, "max_steps": 18}, row["spent_budget"])
        # The review acceptance is an answer and an approval; it is one decision.
        self.assertEqual(2, len(row["interventions_decision_or_repair"]))
        checked, result = self.report()
        self.assertEqual("false_completion", checked[sid]["outcome"])
        self.assertEqual(["spent harness_minutes 40.2083, over the declared 30"], checked[sid]["over_limits"])
        self.assertIn("source revision not recorded", checked[sid]["deviations"])
        live = result["sections"]["live"]
        self.assertEqual((None, 0.0, 1), (live["total_usd"], live["known_usd"], live["unpriced_attempts"]))
        self.assertIn("1 attempts spent more than a declared limit", result["comparison"]["reasons"])
        self.assertIn(f"{sid}: FALSE_COMPLETE -> false_completion (source revision not recorded; spent", out)

    def test_a_scripted_run_is_fixture_evidence_and_cannot_fill_a_live_row(self):
        sid = "scenario/verification-reuse/01"
        code, _, err = self.run_import("--scenario", sid, str(FAKE))
        self.assertEqual(0, code, err)
        row = self.rows()[sid]
        self.assertEqual("fixture", row["execution_kind"])
        self.assertEqual("fake", row["actual_models_and_efforts"]["mode"])
        checked, result = self.report()
        self.assertEqual("verified_delivery", checked[sid]["outcome"])
        self.assertEqual(0, result["sections"]["live"]["attempted"])
        scripted = json.loads(LIVE_PASS.read_text())
        del scripted["profile"]
        scripted["mode"] = "fake"
        path = self.tmp / "scripted.json"
        path.write_text(json.dumps(scripted))
        self.refused(
            "execution_kind 'fixture' is not the declared 'live'",
            "--scenario",
            "scenario/bugfix-iso-weeks/02",
            str(path),
        )
        hybrid = {**json.loads(LIVE_PASS.read_text()), "mode": "build-comparison-hybrid", "hybrid": {"live_tool": "x"}}
        path.write_text(json.dumps(hybrid))
        self.refused(
            "execution_kind 'fixture' is not the declared 'live'",
            "--scenario",
            "scenario/bugfix-iso-weeks/02",
            str(path),
        )

    def test_an_arena_attempt_keeps_its_verdict_and_proves_its_revision_by_runner_identity(self):
        self.assertEqual(DECLARED_REVISION, self.campaign["source_revision"], "recompute DECLARED_IDENTITY")
        self.assertEqual(DECLARED_IDENTITY, importer.revision_identity(ROOT, DECLARED_REVISION))
        store = self.arena()
        database = store / "results.sqlite3"
        before = database.read_bytes()
        code, out, err = self.run_import(
            *("--arena", "arena/pytest-stop-fixture-teardown/01", str(store), PYTEST_PASS),
            *("--arena", "arena/sympy-matrix-derivatives/01", str(store), SYMPY_STOPPED),
            *("--arena", "arena/sympy-matrix-derivatives/02", str(store), SYMPY_RUNNING),
        )
        self.assertEqual(0, code, err)
        self.assertEqual(before, database.read_bytes(), "the Arena ledger is only read")
        rows, (checked, _) = self.rows(), self.report()
        delivered = rows["arena/pytest-stop-fixture-teardown/01"]
        self.assertEqual(self.campaign["source_revision"], delivered["source_revision"])
        actual = delivered["actual_models_and_efforts"]
        self.assertEqual(
            {key: self.definition[key] for key in ("provider", "models", "effort")},
            {key: actual[key] for key in ("provider", "models", "effort")},
        )
        self.assertEqual([], actual["option_deviations"])
        self.assertEqual(
            [{"kind": "decision", "note": "Arena approved the displayed benchmark plan on the person's behalf"}],
            delivered["interventions_decision_or_repair"],
        )
        # Arena does not price an attempt, and measures no declared limit per CLI invocation.
        self.assertIsNone(delivered["estimated_usd"])
        self.assertEqual(
            dict.fromkeys(("cli_invocation_seconds", "oracle_seconds", "max_iterations")), delivered["spent_budget"]
        )
        self.assertEqual("verified_delivery", checked["arena/pytest-stop-fixture-teardown/01"]["outcome"])
        stopped = checked["arena/sympy-matrix-derivatives/01"]
        self.assertEqual(
            ("PAUSED_BUDGET", "STOPPED", "stopped"),
            (stopped["native_status"], stopped["native_outcome"], stopped["outcome"]),
        )
        self.assertEqual(["source revision not recorded"], stopped["deviations"], "another runner checkout ran it")
        self.assertEqual(
            "Arena STOPPED at PAUSED_BUDGET; oracle 6/8 checks, failing matrix_powers, mixed_explicit_array",
            stopped["reason"],
        )
        self.assertEqual([], rows["arena/sympy-matrix-derivatives/01"]["interventions_decision_or_repair"])
        self.assertEqual("ungraded", checked["arena/sympy-matrix-derivatives/02"]["outcome"])
        self.assertIn("arena/sympy-matrix-derivatives/02: RUNNING -> ungraded", out)

    def test_arena_runner_options_off_the_declared_profile_are_deviations(self):
        def value(flag, new):
            return lambda options: options.__setitem__(options.index(flag) + 1, new)

        def replace(item, new):
            return lambda options: options.__setitem__(options.index(item), new)

        def drop(item):
            return lambda options: options.remove(item)

        def add(*items):
            return lambda options: options.extend(items)

        sid = "arena/pytest-stop-fixture-teardown/01"
        differ = "runner options differ from the declared configuration: "
        glm = "zai-coding-plan/glm-5.3"
        # Changes to the fixture PASS's forwarded options, the role values its row then records, and the
        # deviations the import reports (none: the PASS verifies).
        variants = [
            (
                [value("--terra-model", "openai/gpt-6-sol")],
                {"models": {"builder": "openai/gpt-6-sol"}},
                [
                    "models differ from the declared profile for builder",
                    differ + f"--terra-model openai/gpt-6-sol instead of {glm}",
                ],
            ),
            # A role's own effort flag beats --reasoning-effort, which the harness writes as the builder's effort.
            (
                [add("--terra-reasoning-effort", "low")],
                {"effort": {"builder": "low"}},
                ["effort differ from the declared profile for builder", differ + "--terra-reasoning-effort low added"],
            ),
            # --resolver-* set the resolver over the --astra-* role, which is unchanged.
            (
                [value("--resolver-model", glm), value("--resolver-reasoning-effort", "low")],
                {"models": {"resolver": glm}, "effort": {"resolver": "low"}},
                [
                    "models differ from the declared profile for resolver",
                    "effort differ from the declared profile for resolver",
                    differ + f"--resolver-model {glm} instead of openai/gpt-6-astra, "
                    "--resolver-reasoning-effort low instead of high",
                ],
            ),
            # --single-model names every model but the resolver's, which --resolver-model sets.
            (
                [add("--single-model", "openai/gpt-6-luna")],
                {"models": dict.fromkeys(set(profiles.ROLES) - {"resolver"}, "openai/gpt-6-luna")},
                [
                    "models differ from the declared profile for builder, completion, planner, requirements, reviewer, "
                    "validator",
                    differ + "--single-model openai/gpt-6-luna added",
                ],
            ),
            # Options that set no role's model or effort still run another configuration.
            (
                [drop("--joint-planning"), replace("--max-iterations=3", "--max-iterations=50")],
                {},
                [differ + "--max-iterations 50 instead of 3, --joint-planning missing"],
            ),
            ([drop("--max-iterations=3")], {}, [differ + "--max-iterations 3 missing"]),
            (
                [add("--engine", "codex", "--investigator-model", glm, "--pin-model-role", "terra")],
                {},
                [differ + f"--engine codex added, --investigator-model {glm} added, --pin-model-role terra added"],
            ),
            # The declaration leaves each case's test command to the evaluator.
            ([lambda options: options.__setitem__(-1, "--test-command=pytest -q")], {}, []),
        ]
        for changes, recorded, deviations in variants:

            def attempt(attempts, changes=changes):
                for change in changes:
                    change(attempts[0]["options"])

            with self.subTest(deviations=deviations):
                self.new_ledger()
                code, out, err = self.run_import("--arena", sid, str(self.arena(attempt)), PYTEST_PASS)
                self.assertEqual(0, code, err)
                outcome = f"unverified_pass ({'; '.join(deviations)})" if deviations else "verified_delivery"
                self.assertEqual(f"{sid}: PASS -> {outcome}\n", out)
                actual = self.rows()[sid]["actual_models_and_efforts"]
                for key in ("models", "effort"):
                    self.assertEqual({**self.definition[key], **recorded.get(key, {})}, actual[key], key)

    def test_an_attempt_of_another_task_or_task_revision_is_refused(self):
        store = self.arena()
        self.refused(
            "ran case django-filteredrelation-join-lifecycle at base 111111111111 .*not the declared base 3fe0c609cf6d",
            *("--arena", "arena/django-filteredrelation-join-lifecycle/01", str(store), DJANGO_ELSEWHERE),
        )
        self.refused(
            "is case 'pytest-stop-fixture-teardown', not the declared 'sympy-matrix-derivatives'",
            *("--arena", "arena/sympy-matrix-derivatives/01", str(store), PYTEST_PASS),
        )
        self.refused(
            "is scenario 'bugfix-iso-weeks', not the declared 'greenfield-todo-cli'",
            *("--scenario", "scenario/greenfield-todo-cli/01", str(LIVE_PASS)),
        )
        self.refused(
            "arena/sympy-matrix-derivatives/01 is a task of kind arena; a scenario record cannot fill it",
            *("--scenario", "arena/sympy-matrix-derivatives/01", str(LIVE_PASS)),
        )

        # A store prepared before the pytest case gained its three same-fixture checks: same base, 9 checks.
        def earlier(cases):
            checks = cases["pytest-stop-fixture-teardown"]["required_checks"]
            checks[:] = [name for name in checks if not name.startswith("same_fixture_")]

        store = self.arena(cases=earlier)
        self.refused(
            "ran case pytest-stop-fixture-teardown at base f017df443a58 with checks .*'normal_fixture_reuse'\\], not "
            "the declared base f017df443a58",
            *("--arena", "arena/pytest-stop-fixture-teardown/01", str(store), PYTEST_PASS),
        )
        self.refused("holds no attempt 0123", "--arena", "arena/sympy-matrix-derivatives/01", str(store), "0123")
        self.refused("must be a simple name", "--arena", "arena/sympy-matrix-derivatives/01", str(store), "../x")

    def test_a_recorded_attempt_is_never_replaced_and_one_record_fills_one_row(self):
        store = self.arena()
        code, _, err = self.run_import("--scenario", "scenario/bugfix-iso-weeks/01", str(LIVE_PASS))
        self.assertEqual(0, code, err)
        self.refused("already records a PASS attempt", "--scenario", "scenario/bugfix-iso-weeks/01", str(DIRTY))
        copied = self.tmp / "copied-result.json"
        shutil.copyfile(LIVE_PASS, copied)
        self.refused("is already in the ledger", "--scenario", "scenario/bugfix-iso-weeks/02", str(copied))
        self.refused(
            "is already in the ledger",
            *("--arena", "arena/pytest-stop-fixture-teardown/01", str(store), PYTEST_PASS),
            *("--arena", "arena/pytest-stop-fixture-teardown/02", str(store), PYTEST_PASS),
        )
        self.refused(
            "named twice",
            *("--arena", "arena/sympy-matrix-derivatives/01", str(store), SYMPY_STOPPED),
            *("--arena", "arena/sympy-matrix-derivatives/01", str(store), SYMPY_RUNNING),
        )
        self.refused(
            "'scenario/bugfix-iso-weeks/03' is not a scheduled attempt",
            "--scenario",
            "scenario/bugfix-iso-weeks/03",
            str(DIRTY),
        )
        self.refused("name at least one record")

    def test_role_flags_are_the_scenario_harness_flags(self):
        self.assertEqual(set(profiles.ROLES), set(importer.ROLE_FLAGS))
        self.assertEqual(set(profiles.ROLES), set(importer.EFFECTIVE_FLAGS))
        for role, flags in importer.ROLE_FLAGS.items():
            self.assertEqual((profiles.MODEL_FLAGS[role], profiles.EFFORT_FLAGS[role]), flags, role)
            # The flag the harness writes is one AutoCode reads the role from.
            for written, read in zip(flags, importer.EFFECTIVE_FLAGS[role], strict=True):
                self.assertIn(written, read, role)
        # The declared configuration forwards the harness's flags for the profile, and they read back as it.
        options = profiles.flags(copy.deepcopy(self.definition))
        self.assertEqual(options, importer.declared_options(self.definition, {}))
        self.assertEqual(
            {**{key: self.definition[key] for key in ("provider", "models", "effort")}, "option_deviations": []},
            {key: value for key, value in importer.arena_profile(options, options).items() if key != "runner_options"},
        )


if __name__ == "__main__":
    unittest.main()
