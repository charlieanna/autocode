"""T06 — Command evidence and candidate identity catalogue scenarios (EVD-01..EVD-18).

Executed-command and receipt verification, source/evidence drift, criterion
coverage and completion-gate identity checks.  Expected equivalence decisions
come from the independent CommandOracle plus real subprocess ground truth,
never from the production matcher.  EVD-03 reproduces a wrapper-unwrapping
defect on the unfixed tree and guards its narrowly scoped fix.
"""
# path bootstrap: runtime in tools/, fakes in tests/fakes/
import sys as _sys
from pathlib import Path as _Path
_ROOT = _Path(__file__).resolve().parents[2] if 'fakes' in _Path(__file__).parts else _Path(__file__).resolve().parents[1]
_TOOLS = _ROOT / 'tools'
_FAKES = _ROOT / 'tests' / 'fakes'
for _p in (_ROOT, _TOOLS, _ROOT / 'tests', _FAKES):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import copy
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

_ROOT = _Path(__file__).resolve().parents[1] if _Path(__file__).name != 'live_trial.py' else _Path(__file__).resolve().parent.parent
for _p in (_ROOT, _ROOT / 'tools', _ROOT / 'tests', _ROOT / 'tests' / 'fakes'):
    _s = str(_p)
    if _s not in _sys.path:
        _sys.path.insert(0, _s)
import autopilot_testkit as kit
import autocode as runner
import autocode_findings as findings
import autocode_goals as goals
import autocode_support as support
import test_autocode as base
from goal_fixtures import approve_fixture, envelope

ZSH = "/bin/zsh"


def run_shell(command):
    return subprocess.run(command, shell=True, executable=ZSH, capture_output=True, text=True)


class UnitEvidenceCase(kit.CatalogueCase):
    """verify_checks / same_command / evidence-path cases on a temporary workspace."""

    def setUp(self):
        kit.CatalogueCase.setUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.run = self.root / ".autocode/runs/fixture"
        self.run.mkdir(parents=True)
        self.oracle = kit.CommandOracle()

    def events_file(self, rows):
        path = self.run / "events.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def command_event(self, event_id, command, exit_code, output="done"):
        return {"type": "item.completed", "item": {"id": event_id, "type": "command_execution",
                                                   "command": command, "exit_code": exit_code,
                                                   "aggregated_output": output}}

    def test_evd01_quoted_operators_are_not_executed_operators(self):
        """EVD-01. Existing: test_autocode.test_same_command_preserves_shell_quoting (4 variants);
        this adds command substitution and re-executes the matrix under the oracle."""
        variants = [
            ("printf '%s\\n' '&&' false", "printf '%s\\n' && false"),
            ("printf '%s\\n' ';' false", "printf '%s\\n' ; false"),
            ("printf '%s\\n' '|' false", "printf '%s\\n' | false"),
            ("printf '%s\\n' '>' /no/such/autocode-evidence", "printf '%s\\n' > /no/such/autocode-evidence"),
            ("printf '%s\\n' '$(printf executed-substitution)'", "printf '%s\\n' $(printf executed-substitution)"),
        ]
        for printed, executed in variants:
            with self.subTest(variant=printed):
                ran, claimed = run_shell(printed), run_shell(executed)
                self.check(f"[{printed[:26]}] printing_command_succeeds", 0, ran.returncode)
                self.check(f"[{printed[:26]}] executed_variant_behaves_differently", True,
                           claimed.returncode != 0 or claimed.stdout != ran.stdout)
                self.check(f"[{printed[:26]}] production_rejects_equivalence", False,
                           support.same_command(printed, executed))
                self.check(f"[{printed[:26]}] oracle_rejects_equivalence", False,
                           self.oracle.equivalent(printed, executed))
        # A success receipt from the printing command cannot prove the executed claim.
        events = self.events_file([self.command_event("e1", variants[0][0], 0)])
        self.expect_raises("verify_checks_rejects_printed_operator_claim", ValueError,
                           support.verify_checks,
                           [{"command": variants[0][1], "exit_code": 0, "evidence_ref": "event:e1"}],
                           self.root, events)
        self.finish(summary="EVIDENCE_REJECTED: quoting semantics preserved across the operator matrix")

    def test_evd02_legitimate_wrapper_accepted(self):
        """EVD-02. Existing: test_autocode.test_same_command_normalizes_wrapper_on_either_side."""
        body = "python3 -m unittest tests/test_hello.py"
        wrapped = "/bin/zsh -lc " + shlex.quote(body)
        for label, event, claim in (
            ("bare claim against wrapped event", wrapped, body),
            ("verbatim wrapped citation", wrapped, wrapped),
            ("-l -c split form", wrapped.replace("-lc", "-l -c"), body),
        ):
            with self.subTest(form=label):
                self.check(f"[{label}] oracle_accepts", True, self.oracle.equivalent(event, claim))
                self.check(f"[{label}] production_accepts", True, support.same_command(event, claim))
        events = self.events_file([self.command_event("e1", wrapped, 0)])
        check = {"command": body, "exit_code": 0, "evidence_ref": "event:e1"}
        support.verify_checks([check], self.root, events)
        self.check("wrapped_event_fills_exit", 0, check["exit_code"])
        self.check("event_binding_kept", "event:e1", check["evidence_ref"])
        self.finish(summary="EVIDENCE_ACCEPTED: legitimate wrapper normalized without semantic change")

    def test_evd03_wrapper_with_trailing_executable_text_rejected(self):
        """EVD-03. New: unwrapping must not drop executable content after the wrapper body.

        Reproduction of the defect: on the unfixed tree the trailing `&& ...`
        (and `-l -c ... extra`) forms were accepted as the bare body."""
        body = "printf hi"
        variants = {
            "trailing &&": f"/bin/zsh -lc {shlex.quote(body)} && rm -rf /tmp/autocode-fixture",
            "trailing -l -c extra": f"/bin/zsh -l -c {shlex.quote(body)} extra argument",
            "trailing pipe": f"/bin/zsh -lc {shlex.quote(body)} | tee /tmp/autocode-fixture",
            "unquoted trailing sequence": f"/bin/zsh -lc {body} ; echo pwned",
        }
        for label, event in variants.items():
            with self.subTest(variant=label):
                self.check(f"[{label}] oracle_rejects", False, self.oracle.equivalent(event, body))
                self.check(f"[{label}] production_rejects", False, support.same_command(event, body))
        events = self.events_file([self.command_event("e1", f"/bin/zsh -lc {shlex.quote(body)} && rm -rf /tmp/x", 0)])
        self.expect_raises("verify_checks_rejects_trailing_suffix", ValueError,
                           support.verify_checks,
                           [{"command": body, "exit_code": 0, "evidence_ref": "event:e1"}], self.root, events)
        # Identical trailing text on both sides is still one program: accepted.
        both = f"/bin/zsh -lc {shlex.quote(body)} && echo x"
        self.check("identical_full_lines_still_equal", True, support.same_command(both, both))
        self.finish(summary="EVIDENCE_REJECTED: wrapper with extra executable content no longer unwraps")

    def test_evd04_printed_pass_is_not_an_executed_check(self):
        """EVD-04. Existing: partial (command-mismatch in test_autocode.test_check_must_match...)."""
        printing = "echo 'PASS test_hello.py: 3 tests OK'"
        events = self.events_file([self.command_event("printer", printing, 0,
                                                      output="PASS test_hello.py: 3 tests OK")])
        self.check("printing_command_ran", 0, run_shell(printing).returncode)
        self.expect_raises("unittest_claim_on_print_pass_rejected", ValueError,
                           support.verify_checks,
                           [{"command": "python3 -m unittest tests/test_hello.py", "exit_code": 0,
                             "evidence_ref": "event:printer"}], self.root, events)
        # Citing the printer for what it actually ran is honest and accepted; the
        # acceptance never greps stdout for PASS to upgrade a different claim.
        honest = {"command": printing, "exit_code": 0, "evidence_ref": "event:printer"}
        support.verify_checks([honest], self.root, events)
        self.check("honest_printer_citation_accepted", 0, honest["exit_code"])
        self.finish(summary="EVIDENCE_REJECTED: text content alone never satisfies the executed-check rule")

    def test_evd05_wrong_kind_event_references_rejected(self):
        """EVD-05. Existing: test_runtime_reports.test_step_markers_missing_events_and_decorated_refs_are_rejected
        and test_incomplete_command_or_mixed_session_cannot_supply_evidence."""
        rows = [
            {"type": "item.started", "item": {"id": "c1", "type": "command_execution",
                                              "command": "python3 -m unittest", "exit_code": 0}},
            self.command_event("c2", "python3 -m unittest", 0),
            {"type": "item.completed", "item": {"id": "m1", "type": "message", "text": "PASS everything"}},
        ]
        events = self.events_file(rows)
        # The claimed check never executed in any form, so the unique-command
        # fallback (EVD-06) cannot rescue a wrong-kind reference either.
        for label, ref in (("missing id", "event:absent"), ("text message id", "event:m1"),
                           ("tool-start only", "event:c1")):
            with self.subTest(kind=label):
                self.expect_raises(f"[{label}] implementation_evidence_rejected", ValueError,
                                   support.implementation_evidence_paths, [ref], events)
                self.expect_raises(f"[{label}] verify_checks_rejected", ValueError, support.verify_checks,
                                   [{"command": "python3 -m pytest", "exit_code": 0, "evidence_ref": ref}],
                                   self.root, events)
        self.check("log_preserved", 3, len(events.read_text().strip().splitlines()))
        self.check("completed_command_still_admissible", [str(events)],
                   support.implementation_evidence_paths(["event:c2"], events))
        self.finish(summary="EVIDENCE_REJECTED: only completed command executions are admissible")

    def test_evd06_fallback_requires_a_unique_match(self):
        """EVD-06. New: unique executed-command fallback accepted, ambiguous rejected."""
        unique = self.events_file([self.command_event("a1", "ruby tests.rb", 0)])
        check = {"command": "ruby tests.rb", "exit_code": 0, "evidence_ref": "event:conv-guess"}
        support.verify_checks([check], self.root, unique)
        self.check("unique_match_normalized_to_real_event", "event:a1", check["evidence_ref"])
        self.check("unique_match_exit_filled", 0, check["exit_code"])
        ambiguous = self.events_file([self.command_event("a1", "ruby tests.rb", 0),
                                      self.command_event("a2", "ruby tests.rb", 0)])
        self.expect_raises("ambiguous_match_rejected", ValueError, support.verify_checks,
                           [{"command": "ruby tests.rb", "exit_code": 0, "evidence_ref": "event:conv-guess"}],
                           self.root, ambiguous)
        self.finish(summary="UNIQUE_ACCEPTED_AMBIGUOUS_REJECTED: fallback never picks a convenient success")

    def test_evd07_reported_exit_contradiction_rejected(self):
        """EVD-07. Existing: partial (contradiction in test_autocode.test_check_must_match...)."""
        events = self.events_file([self.command_event("f1", "ruby tests.rb", 1, output="1 failure")])
        self.expect_raises("claimed_zero_against_real_one", ValueError, support.verify_checks,
                           [{"command": "ruby tests.rb", "exit_code": 0, "evidence_ref": "event:f1"}],
                           self.root, events)
        missing = {"command": "ruby tests.rb", "evidence_ref": "event:f1"}
        support.verify_checks([missing], self.root, events)
        self.check("normalization_preserves_actual_failure", 1, missing["exit_code"])
        self.check("failed_check_blocks_completion", False,
                   support.completion_ready({"validation": {"checks": [{"exit_code": 1}]}},
                                            {"status": "TASK_COMPLETE", "acceptance_criteria": []},
                                            {"revision": "r"}) is True)
        self.finish(summary="CHECK_FAILED: contradictory or missing exits never become success")

    def test_evd08_receipt_from_another_attempt_rejected(self):
        """EVD-08. Existing: partial (report-file flow in test_command_flow); binding untested."""
        receipt = self.capture_receipt("attempt-1")
        check = {"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": str(receipt["path"])}
        self.expect_raises("cross_attempt_receipt_rejected", ValueError, support.verify_checks,
                           [dict(check)], self.root, self.run / "none.jsonl",
                           receipt_only=True, capture_context="attempt-2")
        support.verify_checks([check], self.root, self.run / "none.jsonl",
                              receipt_only=True, capture_context="attempt-1")
        self.check("owning_attempt_accepted", 0, check["exit_code"])
        self.finish(summary="EVIDENCE_REJECTED: receipts are bound to the attempt that captured them")

    def capture_receipt(self, capture_context, command=("python3", "-m", "unittest")):
        evidence_dir = self.root / ".autocode/evidence"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        raw = evidence_dir / "output.log"
        raw.write_text("3 tests passed\n")
        receipt = {"command": list(command), "exit_code": 0, "duration_seconds": 1,
                   "full_output": str(raw), "full_output_sha256": support.file_hash(raw),
                   "capture_context": capture_context,
                   "summary": {"format": "text", "content": "3 tests passed",
                               "omitted_progress_lines": 0, "repeated_lines": {}}}
        path = evidence_dir / f"receipt-{capture_context}.json"
        path.write_text(json.dumps(receipt))
        return {"receipt": receipt, "path": path, "raw": raw}

    def test_evd17_tampered_receipt_rejected(self):
        """EVD-17. New: command, output hash and path integrity of capture receipts."""
        captured = self.capture_receipt("attempt-1")
        original = captured["path"].read_text()

        def variant(name, mutate):
            path = self.root / f".autocode/evidence/{name}.json"
            receipt = copy.deepcopy(captured["receipt"])
            mutate(receipt)
            path.write_text(json.dumps(receipt))
            self.expect_raises(f"[{name}] tampered_receipt_rejected", ValueError, support.verify_checks,
                               [{"command": "python3 -m unittest", "exit_code": 0,
                                 "evidence_ref": str(path)}],
                               self.root, self.run / "none.jsonl", receipt_only=True,
                               capture_context="attempt-1")

        variant("changed_command", lambda r: r.update(command=["python3", "-m", "unittest", "extra"]))
        variant("changed_output_hash", lambda r: r.update(full_output_sha256="0" * 64))
        variant("wrong_context", lambda r: r.update(capture_context="other-attempt"))

        outside = self.root / "outside-receipt.json"
        receipt = copy.deepcopy(captured["receipt"])
        outside.write_text(json.dumps(receipt))
        self.expect_raises("outside_path_rejected", ValueError, support.verify_checks,
                           [{"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": str(outside)}],
                           self.root, self.run / "none.jsonl", receipt_only=True, capture_context="attempt-1")
        self.check("original_receipt_untouched", original, captured["path"].read_text())
        self.finish(summary="EVIDENCE_REJECTED: edited receipts never substitute for captured evidence")


class SolControllerCase(kit.CatalogueCase):
    """Shared controller harness: approved fixture, task assignment, sol applier."""

    def setUp(self):
        kit.CatalogueCase.setUp(self)
        base.RetrofitTest.setUp(self)
        self.oracle = kit.CommandOracle()
        approve_fixture(self.state, runner.goals)
        first = self.decision("CONTINUE")
        first["next_task"] = {"kind": "implement", "milestone_id": "M1", "requirements": ["Greet names"],
                              "acceptance_criteria": ["C1"], "validation_plan": ["Run both cases"], "findings": []}
        first["next_objective"] = "Implement greeting"
        runner.goals.assign_task(self.state, first, support.snapshot(self.root))

    def decision(self, status="CONTINUE"):
        criteria = [{**c, "status": "verified" if status == "COMPLETE" else "unverified",
                     "evidence": "event:check"} for c in self.state["acceptance_criteria"]]
        return {**envelope(self.state), "status": status, "acceptance_criteria": criteria,
                "next_objective": "", "next_task": {"kind": "none", "milestone_id": "",
                                                    "requirements": [], "acceptance_criteria": [],
                                                    "validation_plan": [], "findings": []},
                "findings": [], "finding_dispositions": [], "agreed_limitations": [],
                "evidence": ["event:check"], "blocker": "", "plan": [], "affected_paths": []}

    def sol_report(self, *, event_id="check", criterion_status="PASS", extra_criterion_results=None,
                   omit_criterion_results=False, verdict="PASS"):
        results = [{"id": c["id"], "status": criterion_status, "evidence_refs": [f"event:{event_id}"]}
                   for c in self.state["acceptance_criteria"]]
        if extra_criterion_results is not None:
            results = extra_criterion_results
        if omit_criterion_results:
            results = []
        return {"verdict": verdict, "checks_run": ["python3 -m unittest"], "unverified_criteria": [],
                "checks": [{"command": "python3 -m unittest", "exit_code": 0, "evidence_ref": f"event:{event_id}"}],
                "criterion_results": results,
                "end_to_end_result": {"status": "PASS", "summary": "Both CLI flows checked",
                                      "evidence_refs": [f"event:{event_id}"]},
                "findings": [], **envelope(self.state)}

    def apply_sol(self, report, *, event_id="check", command="python3 -m unittest", exit_code=0):
        events = self.run / f"sol-{event_id}.jsonl"
        events.write_text(json.dumps({"type": "item.completed", "item": {
            "id": event_id, "type": "command_execution", "command": command,
            "exit_code": exit_code, "aggregated_output": "3 tests passed"}}) + "\n")
        record = {"events": str(events), "source_revision": support.snapshot(self.root)["revision"],
                  "output": str(events), "role": "sol", "stage": "sol"}
        runner.apply_result(self.state, "sol", report, record, self.root, self.run)
        return record

    def pinned_validation(self, name="shot.png", content="fixture capture bytes"):
        shot = self.run / name
        shot.write_text(content)
        report = self.sol_report()
        self.apply_sol(report, event_id="check")
        # Pin the capture as required evidence on the stored validation.
        self.state["validation"]["evidence_hashes"][str(shot)] = support.file_hash(shot)
        return shot



class CompletionEvidenceCase(SolControllerCase):
    """Controller-level candidate identity and completion-gate cases."""

    def test_evd09_drifted_or_deleted_pinned_evidence_blocks_completion(self):
        """EVD-09. Existing: partial (changed bytes in test_autocode.test_completion_requires_current...)."""
        shot = self.pinned_validation()
        current = support.snapshot(self.root)
        complete = self.decision("COMPLETE")
        self.check_true("intact_pins_allow_completion",
                        support.completion_ready(self.state, complete, current))
        shot.write_text("tampered capture bytes")
        self.check_false("changed_bytes_rejected",
                         support.completion_ready(self.state, complete, current))
        shot.write_text("fixture capture bytes")
        self.check_true("restored_hash_accepted_again",
                        support.completion_ready(self.state, complete, current))
        shot.unlink()
        self.check_false("deleted_artifact_rejected",
                         support.completion_ready(self.state, complete, current))
        other = self.run / "decoy.png"
        other.write_text("different bytes entirely")
        shot.symlink_to(other)
        self.check_false("symlink_retarget_rejected",
                         support.completion_ready(self.state, complete, current))
        self.finish(summary="REVIEW_PENDING: pinned evidence is content-checked, not existence-checked")

    def test_evd10_source_drift_variants_change_candidate_identity(self):
        """EVD-10. Existing: untracked/exec-bit/submodule in test_autocode; these variants are new."""
        tracked = self.root / "app.py"
        tracked.write_text("value = 1\n")
        subprocess.run(["git", "-C", str(self.root), "add", "app.py"], check=True)
        subprocess.run(["git", "-C", str(self.root), "-c", "user.name=F", "-c", "user.email=f@t",
                        "commit", "-qm", "fixture app"], check=True)
        link = self.root / "link.py"
        target_a, target_b = self.root / "real_a.py", self.root / "real_b.py"
        target_a.write_text("a\n")
        target_b.write_text("b\n")
        link.symlink_to(target_a)
        self.pinned_validation()  # candidate identity pinned with the link present
        complete = self.decision("COMPLETE")
        self.check_true("symlinked_candidate_accepted",
                        support.completion_ready(self.state, complete, support.snapshot(self.root)))
        link.unlink()
        link.symlink_to(target_b)  # same link name, different target
        self.check_false("symlink_retarget_changes_identity",
                         support.completion_ready(self.state, complete, support.snapshot(self.root)))
        link.unlink()

        tracked.write_text("value = 2\n")  # dirty tracked edit, same HEAD
        self.check_false("tracked_dirty_edit_rejected",
                         support.completion_ready(self.state, complete, support.snapshot(self.root)))
        subprocess.run(["git", "-C", str(self.root), "checkout", "--", "app.py"], check=True)

        tracked.unlink()  # deletion
        self.check_false("deleted_tracked_file_rejected",
                         support.completion_ready(self.state, complete, support.snapshot(self.root)))
        tracked.write_text("value = 1\n")
        self.finish(summary="REVIEW_PENDING: dirty, deleted and retargeted source all change identity")

    def test_evd11_stale_capture_cannot_approve_new_candidate(self):
        """EVD-11. New: a capture pinned on C1 cannot approve changed C2 source."""
        shot = self.pinned_validation()
        complete = self.decision("COMPLETE")
        self.check_true("c1_capture_approves_c1",
                        support.completion_ready(self.state, complete, support.snapshot(self.root)))
        (self.root / "greet.py").write_text("print('v2 with different spacing')\n")
        self.check_false("c1_capture_approves_c2",
                         support.completion_ready(self.state, complete, support.snapshot(self.root)))
        with self.forbid_real_launches(runner):
            self.expect_raises("complete_over_stale_capture_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("no_completion_recorded", False, self.state["status"] == "TASK_COMPLETE")
        self.check("capture_requirement_retained", True,
                   str(shot) in self.state["validation"]["evidence_hashes"])
        self.finish(summary="VERIFICATION_PENDING: stale capture rejected; current screenshot required")

    def test_evd12_silent_reference_change_is_not_fidelity(self):
        """EVD-12. New: a swapped reference/baseline file cannot satisfy the pinned evidence."""
        shot = self.pinned_validation(name="reference.txt", content="approved frozen reference")
        complete = self.decision("COMPLETE")
        current = support.snapshot(self.root)
        self.check_true("frozen_reference_completes", support.completion_ready(self.state, complete, current))
        # The builder substitutes the reference instead of correcting the application.
        shot.write_text("builder-friendly reference")
        self.check_false("substituted_baseline_rejected",
                         support.completion_ready(self.state, complete, current))
        with self.forbid_real_launches(runner):
            self.expect_raises("complete_over_substituted_reference_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        # Recovery: restore the frozen reference and re-pin fresh evidence.
        shot.write_text("approved frozen reference")
        self.check_true("restored_reference_completes_again",
                        support.completion_ready(self.state, complete, current))
        self.finish(summary="REWORK_OR_SCOPE_PAUSE: reference swaps are detected, never credited")

    def test_evd13_criterion_coverage_is_exact(self):
        """EVD-13. Existing: partial (completion-level in test_goals); apply-level matrix new."""
        duplicate = self.sol_report(extra_criterion_results=[
            {"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]},
            {"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]}])
        before = copy.deepcopy(self.state)
        self.expect_raises("duplicate_criterion_ids_rejected", ValueError,
                           self.apply_sol, duplicate, event_id="dup")
        self.check("state_unchanged_after_duplicate", before, self.state)

        unknown = self.sol_report(extra_criterion_results=[
            {"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]},
            {"id": "C9", "status": "PASS", "evidence_refs": ["event:check"]}])
        self.expect_raises("unknown_criterion_ids_rejected", ValueError,
                           self.apply_sol, unknown, event_id="unk")

        missing = self.sol_report(omit_criterion_results=True)
        self.apply_sol(missing)
        complete = self.decision("COMPLETE")
        self.check_false("missing_criterion_cannot_complete",
                         support.completion_ready(self.state, complete, support.snapshot(self.root)))
        with self.forbid_real_launches(runner):
            self.expect_raises("complete_with_missing_criterion_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        named = json.dumps(self.state["acceptance_criteria"])
        self.check_true("diagnostic_names_the_criterion", "C1" in named)
        self.finish(summary="REVIEW_INCOMPLETE_OR_REJECTED: duplicates/unknowns/missing each handled exactly")

    def test_evd14_pass_with_empty_check_set_rejected(self):
        """EVD-14. New: vacuous success cannot complete."""
        report = self.sol_report()
        report["checks"] = []
        events = self.run / "sol-empty.jsonl"
        events.write_text(json.dumps({"type": "item.completed", "item": {
            "id": "check", "type": "command_execution", "command": "python3 -m unittest",
            "exit_code": 0, "aggregated_output": "3 tests passed"}}) + "\n")
        before = copy.deepcopy(self.state)
        with self.forbid_real_launches(runner):
            self.expect_raises("empty_checks_rejected", support.Paused,
                               runner.apply_result, self.state, "sol", report,
                               {"events": str(events), "output": str(events),
                                "source_revision": support.snapshot(self.root)["revision"]},
                               self.root, self.run)
        self.check("state_unchanged_after_empty_checks", before, self.state)
        self.finish(summary="EVIDENCE_REJECTED: PASS without executed checks is vacuous")

    def test_evd15_unavailable_verification_stays_unverified(self):
        """EVD-15. Existing: partial (NOT_VERIFIED statuses in test_goals)."""
        report = self.sol_report(criterion_status="UNVERIFIED")
        report["unverified_criteria"] = ["C1: forced-colors rendering check unavailable in this environment"]
        self.apply_sol(report)
        complete = self.decision("COMPLETE")
        self.check_false("unverified_criterion_blocks_completion",
                         support.completion_ready(self.state, complete, support.snapshot(self.root)))
        with self.forbid_real_launches(runner):
            self.expect_raises("complete_over_unverified_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("limitation_recorded", True,
                   "forced-colors" in json.dumps(self.state["validation"]["unverified_criteria"]))
        self.check("no_implementation_batch_started", "astra_review", self.state["next_stage"])
        self.finish(summary="VERIFICATION_BLOCKED: environment limits stay explicit and unverified")

    def test_evd16_source_drift_during_verdict_application_is_caught(self):
        """EVD-16. New: the completion gate re-snapshots identity at verdict application.

        A patched snapshot mutates the source at the linearization point between
        the initial validation and the completion verdict's identity check."""
        self.pinned_validation()
        complete = self.decision("COMPLETE")
        real_snapshot = support.snapshot
        calls = {"n": 0}

        def racing_snapshot(workspace):
            calls["n"] += 1
            # The completion verdict's identity recheck is the first snapshot
            # call inside this patched apply; mutate the protected source just
            # before it reads.
            if calls["n"] >= 1:
                self.bundle.operation("barrier_source_mutation", at_call=calls["n"],
                                      file="late-edit.py")
                (self.root / "late-edit.py").write_text("racing edit\n")
            return real_snapshot(workspace)

        with mock.patch.object(support, "snapshot", side_effect=racing_snapshot), \
                self.forbid_real_launches(runner):
            self.expect_raises("stale_verdict_cannot_win_the_race", support.Paused,
                               runner.apply_result, self.state, "astra_review", complete,
                               {"output": str(self.run / "complete.json")}, self.root, self.run)
        self.check("race_barrier_actually_crossed", True, calls["n"] >= 1)
        self.check("no_completion_after_race", False, self.state["status"] == "TASK_COMPLETE")
        self.check("racing_edit_preserved_for_review", True, (self.root / "late-edit.py").exists())
        self.finish(summary="REVIEW_PENDING: verdict application rechecks candidate identity and rejects drift")

    def test_evd18_fresh_verification_after_stale_rejection_completes(self):
        """EVD-18. New: prior stale evidence does not poison later genuine verification."""
        self.pinned_validation()
        (self.root / "greet.py").write_text("print('v1')\n")
        stale_complete = self.decision("COMPLETE")
        with self.forbid_real_launches(runner):
            self.expect_raises("stale_evidence_completion_rejected", support.Paused,
                               runner.apply_result, self.state, "astra_review", stale_complete,
                               {"output": str(self.run / "complete-stale.json")}, self.root, self.run)
        # Fresh verification on the new candidate.
        (self.root / "greet.py").write_text("print('v2')\n")
        fresh = self.sol_report(event_id="fresh-check")
        self.apply_sol(fresh, event_id="fresh-check")
        fresh_complete = self.decision("COMPLETE")
        support.atomic_json(self.run / "complete-fresh.json", fresh_complete)
        with self.forbid_real_launches(runner):
            runner.apply_result(self.state, "astra_review", fresh_complete,
                                {"output": str(self.run / "complete-fresh.json")}, self.root, self.run)
        self.check("fresh_verification_completes", "TASK_COMPLETE", self.state["status"])
        self.check("stale_history_retained", True,
                   any(entry.get("reason") == "Superseded by another independent validation"
                       for entry in self.state.get("validation_archive", [])))
        self.finish(summary="COMPLETE: fresh evidence accepted with retained C1 history")


if __name__ == "__main__":
    unittest.main()
