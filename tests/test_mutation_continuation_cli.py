"""The public workflow finishes bounded local proof before its one independent Tester."""

import json
import sys
import textwrap
import unittest
from pathlib import Path

from tests import test_changed_code_evidence_cli as fixtures


@unittest.skipUnless(sys.platform.startswith("linux"), "supervised fake-provider workflow requires supported Linux")
class MutationContinuationCliTests(unittest.TestCase):
    # Share the public TaskRun fixture without inheriting its other scenarios.
    start = fixtures.ChangedCodeEvidenceCliTests.start
    attempts = fixtures.ChangedCodeEvidenceCliTests.attempts
    diagnostic = fixtures.ChangedCodeEvidenceCliTests.diagnostic
    export = fixtures.ChangedCodeEvidenceCliTests.export

    def configure_execution_fixture(self):
        self.local_trace = self.root / "local-proof.jsonl"
        wrapper = self.root / "bounded-proof-cli.py"
        wrapper.write_text(
            textwrap.dedent("""
            import json, runpy, sys, time
            from pathlib import Path
            tools = Path(TOOLS_LITERAL)
            trace = Path(TRACE_LITERAL)
            sys.path.insert(0, str(tools))
            import autocode_changed_code_continuation as continuation
            import autocode_completion_result as completion
            import autocode_mutation_check as mutation
            import autocode_runner_check as runner_check
            import autocode_status as status
            import autocode_util as util

            def note(**row):
                with trace.open("a") as stream:
                    stream.write(json.dumps(row) + "\\n")

            collect = mutation.collect_selected_tests
            def sliced(*args, **kwargs):
                args = list(args)
                run = args[7]
                now = [0]
                def execute(command, label, limit):
                    if label != "unmutated":
                        note(kind="site", label=label)
                    receipt = run(command, label, limit)
                    now[0] += 800
                    return receipt
                args[7] = execute
                kwargs["clock"] = lambda: now[0]
                return collect(*args, **kwargs)
            mutation.collect_selected_tests = sliced

            def one_window(state, workspace, run_dir, proof, framework, inputs, current_identity, *, fresh=False):
                if proof.get("verdict") != "PASS" or not continuation.pending(proof):
                    return proof
                original = util.read(proof["path"])
                with runner_check.track(state, run_dir, "regression_proof", "Bounded fixture window", status.persist):
                    return continuation._window(
                        state, workspace, run_dir, proof, original, framework, inputs,
                        proof["execution_context"], time.monotonic() + mutation.BUDGET_SECONDS, current_identity)
            continuation.continue_proof = one_window

            before_review = completion.before_review
            def observed(state, stage, workspace, run_dir):
                result = before_review(state, stage, workspace, run_dir)
                if stage in ("tester", "review_checkpoint", "review_completion"):
                    note(kind="review", stage=stage, deferred=result is True,
                         pending=continuation.pending(state.get("regression_proof") or {}))
                return result
            completion.before_review = observed
            runpy.run_path(str(tools / "autocode.py"), run_name="__main__")
        """)
            .replace("TOOLS_LITERAL", repr(str(Path(__file__).resolve().parents[1] / "tools")))
            .replace("TRACE_LITERAL", repr(str(self.local_trace)))
        )
        self.run.command = (sys.executable, str(wrapper))

    def test_multiple_local_steps_finish_without_duplicate_sites_or_an_early_paid_review(self):
        view = self.start(equivalent=True)
        self.assertEqual("TASK_COMPLETE", view["status"], self.diagnostic())
        self.assertEqual(self.approved_token, view["approved_contract"]["token"])
        self.assertEqual(self.routes, view["routes"])
        events = [json.loads(line) for line in self.local_trace.read_text().splitlines()]
        deferred = [row for row in events if row["kind"] == "review" and row["deferred"]]
        self.assertTrue(deferred, events)
        self.assertTrue(all(row["pending"] > 0 for row in deferred))
        testers = [row for row in self.attempts() if row["stage"] == "tester" and not row["repair"]]
        self.assertEqual(1, len(testers), self.diagnostic())
        report = testers[0]["handoff"]["regression_proof"]["mutation_check"]
        sites = [row for file in report["files"] for row in file["mutants"]]
        self.assertGreater(len(sites), 2)
        self.assertNotIn("not_run", {row["outcome"] for row in sites})
        labels = [row["label"] for row in events if row["kind"] == "site"]
        self.assertEqual(len(sites), len(labels))
        self.assertEqual(len(labels), len(set(labels)), "completed site commands are not replayed")
        self.assertFalse(any(row["pending"] for row in events if row["kind"] == "review" and not row["deferred"]))
