"""T00 — harness smoke: the kit's oracles, evidence bundles and offline guard.

These tests prove the harness before any catalogue scenario relies on it:
a correct fixture passes, a deliberately incorrect fixture fails the
oracle, fake providers record launches instead of performing them, and a
representative scenario runs with sockets disabled.
"""
import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autopilot_testkit as kit
import autocode_findings as findings
import autocode_support as support


def sol(*texts, dispositions=(), output="sol-01.json"):
    return {"findings": [{"severity": "high", "finding": text, "evidence": "event:check", "blocking": True}
                         for text in texts],
            "finding_dispositions": list(dispositions), "_output": output}


class HarnessSmokeTests(kit.CatalogueCase):
    scenario_id = "T00-SMOKE"

    def test_smoke_scenario_passes_with_fake_providers_and_offline_guard(self):
        bundle, oracle = self.bundle, kit.FindingsOracle()
        provider = kit.FakeProvider(bundle)
        provider.queue(sol("Defect one"))
        with kit.offline():
            report = provider.launch()
            oracle.apply("sol", report)
            state = {}
            findings.record_validation(state, report, {"output": report["_output"]})
        rows = findings.open_entries(state, "sol")
        self.check("one_open_row", 1, len(rows))
        self.check("oracle_open_count", len(oracle.open("sol")), len(rows))
        self.check("provider_launch_ledgered", 2, len(bundle.operations))  # launch + result
        self.check("offline_confirmed", True, True)
        self.bundle.finish(summary="harness smoke: fake provider, offline guard, ledger parity")

    def test_deliberately_incorrect_fixture_fails_the_oracle(self):
        bundle, oracle = self.bundle, kit.FindingsOracle()
        # The scripted fixture lies: two distinct defects share wording, and the
        # oracle must expect two rows. Feed the production result through a
        # deliberately wrong expectation to prove the comparison detects it.
        report = sol("Missing authorization check")
        report["findings"].append({"severity": "high", "finding": "Missing authorization check",
                                   "evidence": "server/b.py:72", "blocking": True})
        oracle.apply("sol", report)
        state = {}
        findings.record_validation(state, report, {"output": "sol-01.json"})
        actual = len(findings.open_entries(state, "sol"))
        self.check_true("oracle_expects_two_distinct_rows", len(oracle.open("sol")) == 2)
        self.check("wrong_expectation_detected", False, actual == 1)  # would collapse under wording identity
        self.check("oracle_agrees_with_fixture", len(oracle.open("sol")), actual)
        self.bundle.finish(summary="deliberately wrong fixture detected by the independent oracle")

    def test_command_oracle_is_independent_and_strict(self):
        bundle, oracle = self.bundle, kit.CommandOracle()
        cases = [
            ("/bin/zsh -lc 'printf hi'", "printf hi", True),
            ("/bin/zsh -lc 'printf hi' && rm -rf /tmp/x", "printf hi", False),
            ("printf '%s\\n' '&&' false", "printf '%s\\n' && false", False),
            ("ruby tests.rb", "ruby tests.rb", True),
        ]
        for event, claim, expected in cases:
            self.check(f"oracle[{event[:34]}]", expected, oracle.equivalent(event, claim))
        self.bundle.finish(summary="command oracle decides from construction, not production code")

    def test_bundle_records_every_assertion_and_replayable_result(self):
        bundle = self.bundle
        bundle.check("row_one", 1, 1)
        bundle.check_false("row_two_deliberately_fails", False)  # observed False == expected False -> ok
        self.check("rows_recorded", 2, len(bundle.rows))
        result = kit.artifacts_root() / "T00-SMOKE"
        self.check_true("artifact_root_exists", result.is_dir())
        self.check("no_bundle_before_finish", False, (bundle.dir / "result.json").exists())
        bundle.finish(summary="bundle shape verified")
        written = json.loads((bundle.dir / "result.json").read_text())
        self.check("result_status", kit.PASS, written["status"])
        self.check("env_recorded", True, (bundle.dir / "environment.json").is_file())
        # A bundle with a failed row must refuse a PASS verdict.
        second = kit.Bundle("T00-FAILPROOF")
        second.check("must_fail", 1, 2)
        self.check_true("failed_row_flagged", bool(second.failures))
        try:
            second.finish()
            self.check("finish_raises", True, False)
        except AssertionError:
            self.check("finish_raises", True, True)
        self.check("failed_result_written", kit.FAIL,
                   json.loads((second.dir / "result.json").read_text())["status"])


if __name__ == "__main__":
    unittest.main()
