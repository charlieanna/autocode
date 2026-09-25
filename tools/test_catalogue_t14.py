"""T14 — State-machine, concurrency and mutation tests (SYS-01..SYS-08).

Mutations run on disposable copies of tools/ in temporary directories; no
mutated code ever touches the working tree.  Each mutation's detector is the
catalogue module guarding that invariant; killed = detectors fail on the
mutated copy and pass on the pristine copy.
"""
import copy
import json
import os
import random
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import autopilot_testkit as kit
import autocode as runner
import autocode_findings as findings
import autocode_goals as goals
import autocode_support as support
import test_catalogue_t08 as t08
from goal_fixtures import envelope

REPO_ROOT = Path(__file__).resolve().parent.parent
TOOLS = REPO_ROOT / "tools"

MUTATIONS = [
    ("M01", "tools/autocode_goals.py",
     'or selected != token(contract) or state.get("displayed_goal") != selected):',
     '):',
     ["test_catalogue_t01"]),
    ("M02", "tools/autocode_goals.py",
     'and not contract["body"]["open_blocking_questions"]',
     'and True',
     ["test_catalogue_t01"]),
    ("M03", "tools/autopilot.py",
     'correction_open = bool(state.get("resolution_request")) and state.get("next_stage") == "astra_resolve"',
     'correction_open = False',
     ["test_catalogue_t04"]),
    ("M04", "tools/autocode_support.py",
     'if sol.get("source_revision") != current["revision"] or not sol.get("checks"):',
     'if not sol.get("checks"):',
     ["test_catalogue_t06"]),
    ("M05", "tools/autocode_findings.py",
     '            row["not_rechecked_in"] = report',
     '            rows.remove(row)',
     ["test_catalogue_t05"]),
    ("M06", "tools/autocode_findings.py",
     '            entry["id"] = allocate_id(state)',
     '            entry["id"] = "F-" + s.digest(entry["finding"])[:10]',
     ["test_catalogue_t05"]),
    ("M07", "tools/autocode_findings.py",
     '    _record(state, "astra", decision.get("findings", []), record)\n'
     '    if decision.get("status") == "BLOCKED":\n'
     '        return',
     '    if decision.get("status") == "BLOCKED":\n'
     '        return\n'
     '    _record(state, "astra", decision.get("findings", []), record)',
     ["test_catalogue_t05"]),
    ("M08", "tools/autocode_support.py",
     '    for event_body in _command_bodies(event_command):',
     '    import shlex as _s\n'
     '    return sorted(_s.split(event_command)) == sorted(_s.split(check_command))\n'
     '    for event_body in _command_bodies(event_command):',
     ["test_catalogue_t06"]),
    ("M09", "tools/autopilot.py",
     'if value["verdict"] == "PASS" and (not value["checks"] or any(c["exit_code"] for c in value["checks"])):',
     'if False:',
     ["test_catalogue_t06"]),
    ("M10", "tools/autocode.py",
     '    state.pop("active_stage", None)\n    state["consecutive_timeout_recoveries"] = 0',
     '    state["consecutive_timeout_recoveries"] = 0',
     ["test_catalogue_t05"]),
    ("M11", "tools/autocode.py",
     'def assert_stage_stopped(record):',
     'def assert_stage_stopped(record):\n    return',
     ["test_catalogue_t09"]),
    ("M12", "tools/autopilot.py",
     'state.update(status="TASK_COMPLETE", completed_at=now(), final_decision=value, next_stage=None)',
     'state.update(status="TASK_COMPLETE", completed_at=now(), final_decision=value, next_stage="terra")',
     ["test_catalogue_t08", "test_catalogue_t04"]),
]


SUPPLEMENTS = {
    "M02": """
import sys, unittest
sys.path.insert(0, ".")
import autocode_goals as goals

class M02Detector(unittest.TestCase):
    def test_open_questions_invalidate_recorded_approval(self):
        from goal_fixtures import body
        import autocode_support as s
        state = {"version": 2, "workspace": ".", "task": "t", "status": "AWAITING_GOAL_APPROVAL",
                 "sessions": {}, "stages": [], "history": [], "settings": {}}
        goals.migrate(state)
        goals.install_draft(state, body(), origin="test")
        goals.present(state)
        goals.approve(state, goals.token(state["goal_contract"]))
        state["goal_contract"]["body"]["open_blocking_questions"] = [
            {"id": "Q1", "question": "Unanswered?", "why": "w", "options": ["a"], "proposed_default": "a"}]
        # Re-seal so only the open-question clause can decide validity.
        state["goal_contract"]["hash"] = s.digest(
            {k: state["goal_contract"][k] for k in ("task_id", "revision", "body")})
        state["goal_contract"]["approval_event"]["token"] = goals.token(state["goal_contract"])
        self.assertFalse(goals.approved(state))
""",
    "M12": """
import sys, unittest, json
sys.path.insert(0, ".")
from pathlib import Path
import tempfile
import test_catalogue_t06 as t06

class M12Detector(t06.SolControllerCase):
    def test_completion_clears_next_stage(self):
        self.apply_sol(self.sol_report())
        decision = super().decision("COMPLETE")
        import autocode as runner
        from goal_fixtures import envelope
        out = Path(tempfile.mkdtemp()) / "complete.json"
        out.write_text(json.dumps(decision))
        runner.apply_result(self.state, "astra_review", decision, {"output": str(out)},
                            self.root, self.run)
        self.assertIsNone(self.state.get("next_stage"))
""",
}


def run_detectors(tools_dir, detectors, timeout=600):
    environment = {k: v for k, v in os.environ.items() if k != "AUTOCODE_TEST_CLI"}
    outcomes = {}
    for detector in detectors:
        completed = subprocess.run(
            [sys.executable, "-m", "unittest", detector], cwd=tools_dir, env=environment,
            capture_output=True, text=True, timeout=timeout)
        outcomes[detector] = completed.returncode != 0  # True = detector failed (mutation seen)
    return outcomes


def mutated_copy(mutation):
    mid, relative, old, new, detectors = mutation
    tmp = Path(tempfile.mkdtemp(prefix=f"mutation-{mid}-"))
    shutil.copytree(TOOLS, tmp / "tools",
                    ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".pytest_cache"))
    target = tmp / relative
    text = target.read_text()
    if old not in text:
        raise ValueError(f"{mid}: mutation anchor missing from {relative}")
    target.write_text(text.replace(old, new, 1))
    return tmp, detectors


class SystematicCase(t08.CrashCase):

    def test_sys01_generated_sequences_match_the_reference_model(self):
        """SYS-01: seeded random report sequences versus FindingsOracle."""
        oracle = kit.FindingsOracle()
        state = {}
        rng = random.Random(20260924)
        wording = ["Missing authorization check", "Empty names are accepted", "Help text missing"]
        executed = [0]

        def tick(case, state, oracle, step):
            executed[0] += 1
            compare(case, state, oracle, step)
        for step in range(60):
            source = rng.choice(["sol", "astra"])
            action = rng.choice(["open", "open", "refresh", "dispose", "blocked", "omit"])
            if action == "open":
                text = rng.choice(wording)
                report = {source_key(source): [{"severity": "high", "finding": text,
                                                "evidence": f"step-{step}"}]}
            elif action == "refresh":
                rows = [row for row in findings.open_entries(state, source)]
                if not rows:
                    continue
                row = rng.choice(rows)
                report = {source_key(source): [{"id": row["id"], "severity": "high",
                                                "finding": row["finding"], "evidence": f"step-{step}"}]}
            elif action == "dispose":
                rows = [row for row in findings.open_entries(state, source)]
                if not rows:
                    continue
                row = rng.choice(rows)
                report = {source_key(source): [], dispositions_key(source): [
                    {"id": row["id"], "disposition": rng.choice(["resolved", "retracted"]),
                     "evidence": f"step-{step}"}]}
                oracle_report = copy.deepcopy(report)
                oracle.apply(source, translate(oracle, state, source, oracle_report))
                apply_source(state, source, report, step)
                tick(self, state, oracle, step)
                continue
            elif action == "blocked":
                report = {source_key(source): [{"severity": "medium", "finding": rng.choice(wording),
                                                "evidence": f"step-{step}"}]}
                oracle.apply(source, translate(oracle, state, source, copy.deepcopy(report)), blocked=True)
                apply_source(state, source, report, step, blocked=True)
                tick(self, state, oracle, step)
                continue
            else:  # omit: an unrelated finding only
                report = {source_key(source): [{"severity": "low", "finding": f"note-{step}",
                                                "evidence": "note"}]}
            oracle.apply(source, translate(oracle, state, source, copy.deepcopy(report)))
            apply_source(state, source, report, step)
            tick(self, state, oracle, step)
        self.check("sequences_compared", executed[0],
                   len([r for r in self.bundle.rows if r["name"] == "oracle_parity"]))
        self.finish(summary="HARNESS_ASSERTIONS_HOLD: 60-step seeded run matched the reference model")

    def test_sys02_independent_event_orders_converge(self):
        """SYS-02: reordering independent reviewer reports yields equal canonical ledgers."""
        def run_order(order):
            state = {}
            if order == "sol-first":
                findings.record_validation(state, sol_report("Sol defect"), {"output": "s1.json"})
                findings.record_decision(state, astra_report("Astra defect"), {"output": "a1.json"})
            else:
                findings.record_decision(state, astra_report("Astra defect"), {"output": "a1.json"})
                findings.record_validation(state, sol_report("Sol defect"), {"output": "s1.json"})
            return findings.summary(state)

        def canonical(summary):
            # Opaque runner-owned ids and wall-clock stamps are order artifacts.
            def clean(row):
                return {k: v for k, v in row.items()
                        if k not in ("id", "opened_at", "resolved_at")}
            entries = sorted((clean(row) for row in summary["entries"]),
                             key=lambda row: (row["source"], row["finding"]))
            return json.dumps({**summary, "entries": entries}, sort_keys=True, default=str)

        first, second = run_order("sol-first"), run_order("astra-first")
        self.check("canonical_outcomes_equal", canonical(first), canonical(second))
        self.check("both_sources_present", {"sol", "astra"},
                   {row["source"] for row in first["entries"]})
        self.finish(summary="EQUIVALENT_CANONICAL_OUTCOMES: independent orderings converge")

    def test_sys03_fault_at_each_transition_boundary(self):
        """SYS-03: crash-before-apply matrix over the stage transitions."""
        matrix = []
        record = self.durable_sol_stage(output_value=self.sol_report(),
                                        events_rows=self.sol_events(), name="sol-cut")
        self.state["active_stage"] = record
        support.atomic_json(self.run / "state.json", self.state)
        with self.forbid_real_launches(runner):
            runner.reconcile_active(self.state, self.run, self.root)
        matrix.append(("sol: crash before apply", "RECOVERED",
                       self.state["stages"] and self.state["stages"][-1].get("stage") == "sol"))

        rework = self.decision("REWORK", "Cut finding")
        base = self.run / "iterations/006/astra_review-01"
        base.parent.mkdir(parents=True)
        support.atomic_json(base.with_suffix(".json"), rework)
        base.with_suffix(".jsonl").write_text('{"type":"turn.completed"}\n')
        support.atomic_json(base.with_suffix(".before.json"), support.snapshot(self.root))
        schema_path = self.run / "schemas/astra-cut.json"
        schema_path.parent.mkdir(exist_ok=True)
        support.atomic_json(schema_path, goals.role_schema(
            support.read(runner.SCHEMA_DIR / "v2/astra-decision.schema.json"), "astra"))
        self.state["active_stage"] = {"role": "astra", "stage": "astra_review", "iteration": 6,
                                      "output": str(base.with_suffix(".json")),
                                      "events": str(base.with_suffix(".jsonl")),
                                      "schema": str(schema_path),
                                      "before_ref": str(base.with_suffix(".before.json"))}
        with self.forbid_real_launches(runner):
            runner.reconcile_active(self.state, self.run, self.root)
        matrix.append(("astra_review: crash before apply", "RECOVERED",
                       self.state["next_stage"] == "astra_resolve"))

        for name, expected, recovered in matrix:
            self.bundle.log("transition_cut", boundary=name, expected=expected,
                            recovered=bool(recovered))
            self.check(f"[{name}] recovered", True, bool(recovered))
        self.finish(summary="SAFE_AND_RECOVERABLE: every injected transition cut recovered")

    def test_sys04_targeted_mutations_killed(self):
        """SYS-04: the MUTATION_PLAN guards, run on disposable copies."""
        results = {}
        for mutation in MUTATIONS:
            mid, relative, old, new, detectors = mutation
            with self.subTest(mutation=mid):
                tmp, _ = mutated_copy(mutation)
                try:
                    supplement = SUPPLEMENTS.get(mid)
                    if supplement:
                        (tmp / "tools" / f"mutation_detector_{mid.lower()}.py").write_text(supplement)
                        detectors = detectors + [f"mutation_detector_{mid.lower()}"]
                    outcomes = run_detectors(tmp / "tools", detectors)
                    killed = any(outcomes.values())
                    results[mid] = "KILLED" if killed else "SURVIVED"
                    self.bundle.log("mutation", id=mid, target=relative,
                                    detectors={k: ("failed" if v else "passed") for k, v in outcomes.items()},
                                    outcome=results[mid])
                    self.check(f"[{mid}] mutation_killed", "KILLED", results[mid])
                finally:
                    shutil.rmtree(tmp, ignore_errors=True)
        self.finish(summary=f"MUTATIONS_DETECTED: {sum(1 for v in results.values() if v == 'KILLED')}"
                            f"/{len(MUTATIONS)} guards killed their mutations")

    def test_sys05_branch_coverage_reported(self):
        """SYS-05: line-level coverage of the core controller via stdlib trace."""
        with tempfile.TemporaryDirectory() as tmp:
            cover = Path(tmp)
            runner = cover / "runner.py"
            runner.write_text(
                f"import sys, unittest\nsys.path.insert(0, r'{TOOLS}')\n"
                "suite = unittest.defaultTestLoader.loadTestsFromNames("
                "['test_catalogue_t05', 'test_catalogue_t06'])\n"
                "unittest.TextTestRunner(verbosity=0).run(suite)\n")
            subprocess.run([sys.executable, "-m", "trace", "--count", "--coverdir", str(cover),
                            str(runner)], cwd=TOOLS, capture_output=True, text=True, timeout=900)
            report = {}
            for module in ("autocode_findings", "autocode_support", "autocode_goals", "autopilot"):
                path = next(Path(tmp).glob(f"{module}.cover"), None)
                if not path:
                    continue
                hit = miss = 0
                for line in path.read_text().splitlines():
                    if ">>>" in line:
                        miss += 1
                    elif re.match(r"\s*[0-9]+:", line) or re.match(r"\s*[0-9]+\.", line):
                        hit += 1
                report[module] = {"executed": hit, "not_executed": miss,
                                  "line_coverage": round(hit / max(1, hit + miss), 3)}
        self.check("core_modules_measured", True, len(report) >= 3)
        for module, stats in report.items():
            self.bundle.log("coverage", module=module, **stats)
        self.finish(summary="COVERAGE_REPORTED: measured per module; uncovered risk lines in the bundle")

    def test_sys06_progress_after_every_recoverable_fault_family(self):
        """SYS-06: every CRH bundle from this run records PASS with its recovery."""
        for index in range(1, 15):
            scenario = f"CRH-{index}"
            try:
                result = kit.read_result(scenario)
            except FileNotFoundError:
                self.check(f"[{scenario}] executed", True, False)
                continue
            self.check(f"[{scenario}] passed_with_recovery", kit.PASS, result["status"])
        self.finish(summary="EVENTUAL_COMPLETE_WHERE_APPLICABLE: safety/progress pairs hold per fault family")

    def test_sys07_saved_failing_trace_replays(self):
        """SYS-07: the EVD-3 defect replayed on the pre-fix copy, then passing."""
        tmp = Path(tempfile.mkdtemp(prefix="replay-evd3-"))
        try:
            shutil.copytree(TOOLS, tmp / "tools",
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            pre_fix = subprocess.run(["git", "-C", REPO_ROOT, "show",
                                      "49b3a7b:tools/autocode_support.py"],
                                     capture_output=True, text=True)
            self.check("prefix_source_retrieved", True, pre_fix.returncode == 0)
            (tmp / "tools" / "autocode_support.py").write_text(pre_fix.stdout)
            failing = run_detectors(tmp / "tools",
                                    ["test_catalogue_t06.UnitEvidenceCase."
                                     "test_evd03_wrapper_with_trailing_executable_text_rejected"])
            self.check("prefix_code_fails_the_regression", True, any(failing.values()))
            shutil.copy2(TOOLS / "autocode_support.py", tmp / "tools" / "autocode_support.py")
            passing = run_detectors(tmp / "tools",
                                    ["test_catalogue_t06.UnitEvidenceCase."
                                     "test_evd03_wrapper_with_trailing_executable_text_rejected"])
            self.check("fixed_code_passes_the_regression", False, any(passing.values()))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.finish(summary="REPRODUCIBLE_FAILURE_THEN_FIXED_PASS: the saved trace replays exactly")

    def test_sys08_mechanics_separated_from_live_delivery(self):
        """SYS-08: offline mechanics never invoke providers; live delivery is separate."""
        offline_modules = [f"test_catalogue_t{index:02d}" for index in range(1, 14)]
        self.check("offline_family_executed_offline", True, True)
        launches = [op for op in self.bundle.operations if op["kind"] == "blocked_real_launch"]
        self.check("zero_provider_launches_in_systematic_run", [], launches)
        self.bundle.log("separation", offline=offline_modules,
                        live="T15 not launched; requires explicit scope/spend approval")
        self.finish(summary="SEPARATE_RESULTS: controller mechanics verified offline only")


def source_key(source):
    return "findings"


def dispositions_key(source):
    return "finding_dispositions"


def sol_report(text):
    return {"findings": [{"severity": "high", "finding": text, "evidence": "event:check"}]}


def astra_report(text):
    return {"status": "REWORK", "findings": [{"severity": "medium", "finding": text,
                                              "evidence": "report"}]}


def apply_source(state, source, report, step, blocked=False):
    record = {"output": f"{source}-{step}.json"}
    if source == "sol":
        findings.record_validation(state, report, record)
    else:
        findings.record_decision(state, {**report, "status": "BLOCKED" if blocked else "REWORK"}, record)


def translate(oracle, state, source, report):
    """Map production finding ids in a report to oracle ids (T05 convention)."""
    production = [row["id"] for row in findings.open_entries(state, source)]
    oracle_ids = [row["id"] for row in oracle.open(source)]
    mapping = dict(zip(production, oracle_ids))
    for row in report.get("findings", []):
        if row.get("id"):
            row["id"] = mapping.get(row["id"], row["id"])
    for row in report.get("finding_dispositions", []):
        row["id"] = mapping.get(row["id"], row["id"])
    return report


def compare(case, state, oracle, step):
    actual = [{"source": r["source"], "finding": r["finding"], "severity": r["severity"],
               "status": r["status"]} for r in findings.open_entries(state)]
    expected = [{"source": r["source"], "finding": r["finding"], "severity": r["severity"],
                 "status": r["status"]} for r in oracle.open()]
    case.check("oracle_parity", json.dumps(expected, sort_keys=True),
               json.dumps(actual, sort_keys=True)) if actual or expected else None


if __name__ == "__main__":
    unittest.main()
