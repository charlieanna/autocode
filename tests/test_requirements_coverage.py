"""Exact requirement quotes, formatting-aware coverage, and guarded retries."""

import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

import autocode as runner
import autocode_goals as goals
import autocode_support as support
from units import autoplanner


class CoverageTests(unittest.TestCase):
    def report(self, *quotes):
        return {
            "requirements": [
                {"id": "R" + str(i), "text": quote, "source_quote": quote} for i, quote in enumerate(quotes)
            ],
            "ignored_statements": [],
        }

    def test_multisentence_quote_covers_bulleted_requirement(self):
        quote = "The Validator must inspect the actual images. No human review gate is requested."
        for marker in ("- ", "* ", "+ ", "1. ", "2) "):
            with self.subTest(marker=marker):
                state = {"task": "Review policy.\n" + marker + quote}
                goals.check_requirement_handoff(state, self.report(quote))

    def test_formatting_normalization_does_not_authorize_invented_quotes(self):
        with self.assertRaisesRegex(ValueError, "source_quote is not in"):
            goals.check_requirement_handoff(
                {"task": "- The Validator must inspect images."}, self.report("The Validator must approve images.")
            )

    def test_builder_task_cannot_be_cited_as_new_user_requirement(self):
        inherited = "Fix the scheduling-dependent race in the conversation test."
        state = {
            "task": "Build a planner.",
            "current_task": {"requirements": [inherited]},
            "brief_feedback": [{"text": "Declare the test file in M2."}],
        }
        report = {
            "requirements": [
                {"id": "R1", "text": "Declare the test file", "source_quote": "Declare the test file in M2."},
                {"id": "R2", "text": inherited, "source_quote": inherited},
            ],
            "ignored_statements": [],
        }
        with self.assertRaisesRegex(ValueError, "keep that existing obligation"):
            goals.check_requirement_handoff(state, report)
        report["requirements"].pop()
        goals.check_requirement_handoff(state, report)

    def test_feedback_archives_stale_report_repair_before_new_handoff(self):
        state = {
            "status": "WAITING_FOR_USER",
            "settings": {"roles": {"requirements": {}}},
            "pending_report_repair": {"attempts": 2, "error": "stale citation"},
        }
        goals.feedback(state, "Declare the consumer test in M2.")
        self.assertEqual("requirements_gather", state["next_stage"])
        self.assertNotIn("pending_report_repair", state)
        self.assertEqual("stale citation", state["report_repair_archive"][-1]["repair"]["error"])

    def test_all_missing_sentences_are_reported_without_truncation(self):
        sentences = ["You must preserve the reference.", "You must inspect " + "every image pair " * 12 + "."]
        with self.assertRaises(ValueError) as caught:
            goals.check_requirement_handoff({"task": " ".join(sentences)}, self.report())
        for sentence in sentences:
            self.assertIn(sentence, str(caught.exception))

    def test_ignored_statement_still_requires_explicit_coverage(self):
        state = {"task": "You must retain drafts. You must use fixtures."}
        report = self.report("You must retain drafts.")
        with self.assertRaisesRegex(ValueError, "You must use fixtures"):
            goals.check_requirement_handoff(state, report)
        report["ignored_statements"] = ["You must use fixtures. This is a validation procedure."]
        goals.check_requirement_handoff(state, report)

    def test_requirements_prompt_exposes_the_exact_coverage_checklist(self):
        state = {
            "task": "Make a dashboard.\n- You must retain drafts.",
            "workspace": "/fixture",
            "settings": {"engine": "codex", "joint_planning": True, "roles": {"requirements": {}}},
        }
        prompt, _ = autoplanner.context(state, "requirements_gather", Path("/fixture/state.json"))
        packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(["- You must retain drafts."], packet["requirement_coverage_checklist"])
        self.assertIsNone(packet["goal_contract"])

    def follow_up(self, workflow="design"):
        earlier = "Write only `docs/decisions/cache.json`; do not write code."
        old = {
            "kind": "brief_feedback",
            "id": "old",
            "actor": "user_cli",
            "text": "The decision note must keep its comparison.",
        }
        event = {"kind": "brief_feedback", "id": "new", "actor": "user_cli", "text": "Shared it is; design it."}
        answer = {
            "kind": "answer",
            "question_id": "Q1",
            "actor": "user_cli",
            "text": "The discussion must list both alternatives.",
        }
        return {
            "task": event["text"] + "\n\nThis follows up an earlier request (discuss): " + earlier,
            "workflow": {"kind": workflow},
            "workspace": "/fixture",
            "settings": {"engine": "codex", "joint_planning": True, "roles": {"requirements": {}}},
            "turns": [
                {
                    "say": event["text"],
                    "event_id": event["id"],
                    "previous": {"workflow": "discuss", "task": earlier, "wrote": ["docs/decisions/cache.json"]},
                }
            ],
            "brief_feedback": [old, event],
            "answers": {"Q1": answer},
            "user_events": [old, answer, event],
        }

    def test_prior_job_requirements_remain_quotable_but_do_not_reopen_its_outputs(self):
        state = self.follow_up()
        before = copy.deepcopy(state)
        goals.check_requirement_handoff(state, self.report("Shared it is; design it."))
        self.assertIn("The decision note must keep its comparison.", goals.source_texts(state))
        goals.check_requirement_handoff(state, self.report("The discussion must list both alternatives."))
        self.assertEqual(before, state)

    def test_current_literals_and_answers_remain_required(self):
        state = self.follow_up()
        event = {
            "kind": "answer",
            "question_id": "Q2",
            "actor": "user_cli",
            "text": "The design must use `docs/design/cache.md`.",
        }
        state["answers"]["Q2"] = event
        state["user_events"].append(event)
        with self.assertRaisesRegex(ValueError, "The design must use"):
            goals.check_requirement_handoff(state, self.report("Shared it is; design it."))
        goals.check_requirement_handoff(state, self.report(event["text"]))
        with self.assertRaisesRegex(ValueError, "docs/design/cache.md"):
            goals.check_requirement_trace(state, {}, {})
        goals.check_requirement_trace(state, {}, {"deliverables": ["docs/design/cache.md"]})

    def test_same_kind_and_unreceipted_turns_keep_coverage_guards(self):
        for state in (self.follow_up("discuss"), self.follow_up()):
            if state["workflow"]["kind"] == "design":
                state["user_events"].pop()
            with self.assertRaisesRegex(ValueError, "cache.json"):
                goals.check_requirement_handoff(state, self.report("Shared it is; design it."))

    def test_planner_packet_keeps_binding_design_and_contract(self):
        state = self.follow_up("build")
        state["turns"][-1]["previous"]["workflow"] = "design"
        state["turns"][-1]["previous"]["wrote"] = ["docs/design/cache.md"]
        state["design_constraint"] = {
            "design_document": "docs/design/cache.md",
            "summary": "Shared cache",
            "constraints": ["Never return stale data."],
        }
        state["goal_contract"] = {
            "revision": 4,
            "hash": "binding",
            "body": {"required_behaviors": ["Retain the shared cache decision."]},
        }
        prompt, _ = autoplanner.context(state, "requirements_gather", Path("/fixture/state.json"))
        packet = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(state["turns"][-1]["say"], packet["task"])
        self.assertEqual(["docs/design/cache.md"], packet["previous_turn"]["wrote"])
        self.assertEqual([], packet["requirement_coverage_checklist"])
        planned, _ = autoplanner.context(state, "astra_discovery", Path("/fixture/state.json"))
        planning_packet = json.loads(planned.split("CURRENT HANDOFF DATA\n", 1)[1])
        self.assertEqual(state["goal_contract"], planning_packet["goal_contract"])
        self.assertIn("Never return stale data.", prompt)
        self.assertIn("current contract and approved_design, when present, remain binding", prompt)
        self.assertIn("Do not redesign it", prompt)


class HeadingCueTests(unittest.TestCase):
    """A Markdown heading label is formatting, not a user requirement (#216)."""

    def report(self, *quotes):
        return {
            "requirements": [
                {"id": "R" + str(i), "text": quote, "source_quote": quote} for i, quote in enumerate(quotes)
            ],
            "ignored_statements": [],
        }

    def test_label_heading_is_not_an_obligation(self):
        # The live-run shape: the heading fused with an orphaned list marker into
        # one un-quotable "requirement-like sentence".
        task = "Add refunds.\n\n## Required behavior\n\n1. Refunds must credit the original payment method."
        self.assertEqual(["Refunds must credit the original payment method."], goals.cue_sentences(task))
        goals.check_requirement_handoff({"task": task}, self.report("Refunds must credit the original payment method."))

    def test_substantive_heading_stays_a_clean_obligation(self):
        task = "Harden storage.\n\n## Files must be encrypted\n"
        self.assertEqual(["Files must be encrypted."], goals.cue_sentences(task))
        with self.assertRaisesRegex(ValueError, "Files must be encrypted"):
            goals.check_requirement_handoff({"task": task}, self.report())
        goals.check_requirement_handoff({"task": task}, self.report("Files must be encrypted"))

    def test_plain_requirement_sentences_are_still_enforced(self):
        task = "Add refunds. Refunds must credit the original payment method."
        self.assertEqual(["Refunds must credit the original payment method."], goals.cue_sentences(task))
        with self.assertRaisesRegex(ValueError, "Refunds must credit"):
            goals.check_requirement_handoff({"task": task}, self.report())

    def test_a_fenced_block_is_context_not_an_obligation(self):
        task = (
            'Build notes. Notes must be numbered from 1.\n\n```json\n{"note": "M4 must only test the journey."}\n```\n'
            "Search must ignore case."
        )
        self.assertEqual(["Notes must be numbered from 1.", "Search must ignore case."], goals.cue_sentences(task))
        goals.check_requirement_handoff(
            {"task": task}, self.report("Notes must be numbered from 1.", "Search must ignore case.")
        )
        with self.assertRaisesRegex(ValueError, "Search must ignore case"):
            goals.check_requirement_handoff({"task": task}, self.report("Notes must be numbered from 1."))
        # A sentence inside the block stays quotable.
        self.assertIn(task, goals.source_texts({"task": task}))

    def test_a_fenced_block_ends_the_sentence_before_it(self):
        # A program brief's parent plan follows a label with no stop and is followed by an instruction; joined,
        # they made a sentence found nowhere in the brief, which no verbatim quote could cover (2026-10-06).
        task = (
            "Paths you own: only notes/store.py\n\nApproved parent contract (read only):\n```json\n"
            '{"note": "M4 must only test the journey."}\n```\nKeep its exclusions; never merge branches.'
        )
        sentences = goals.cue_sentences(task)
        self.assertEqual(
            [
                "Paths you own: only notes/store.py",
                "Approved parent contract (read only):",
                "Keep its exclusions; never merge branches.",
            ],
            sentences,
        )
        self.assertTrue(all(sentence in task for sentence in sentences), sentences)
        goals.check_requirement_handoff({"task": task}, self.report(*sentences))
        # Quotes that run into the block, as a report splitting the brief only on its stops writes them, still cover.
        into, out_of = task[: task.index(" the journey")], task[task.index('{"note"') :]
        self.assertTrue(into.endswith('```json\n{"note": "M4 must only test') and out_of.startswith('{"note"'), into)
        goals.check_requirement_handoff({"task": task}, self.report(into, out_of))

    def test_only_a_closed_fence_on_lines_of_its_own_hides_obligations(self):
        for task, owed in (
            ("Wrap code in ``` fences when you paste it. You must never log tokens.", ["You must never log tokens."]),
            (
                "Example:\n```python\nprint(1)\n\nYou must keep the exit code 0 on success.",
                ["You must keep the exit code 0 on success."],
            ),
            (
                "Search must ignore case.\n\n```\nexport must never print a header\n",
                ["Search must ignore case.", "```\nexport must never print a header"],
            ),
        ):
            with self.subTest(task=task):
                self.assertEqual(owed, goals.cue_sentences(task))
                self.assertTrue(all(sentence in task for sentence in owed))
                with self.assertRaisesRegex(ValueError, "neither quoted nor explicitly ignored"):
                    goals.check_requirement_handoff({"task": task}, self.report())

    def test_delegated_answer_is_quotable_but_never_owed(self):
        state = {
            "task": "Add receipts.",
            "answers": {"q1": {"kind": "delegated", "text": "Use plain-text receipts only."}},
        }
        self.assertIn("Use plain-text receipts only.", goals.source_texts(state))
        self.assertNotIn("Use plain-text receipts only.", goals.scan_texts(state))
        # Not covering the model's own default passes...
        goals.check_requirement_handoff(state, self.report())
        # ...and a report may still quote it as a source.
        goals.check_requirement_handoff(state, self.report("Use plain-text receipts only."))

    def test_real_answer_still_must_be_covered(self):
        state = {"task": "Add receipts.", "answers": {"q1": {"kind": "answer", "text": "Receipts must be plain text."}}}
        with self.assertRaisesRegex(ValueError, "Receipts must be plain text"):
            goals.check_requirement_handoff(state, self.report())

    def test_changed_cause_allows_fresh_planning_retry_without_erasing_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            run = root / ".autocode/run"
            run.mkdir(parents=True)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(
                [
                    "git",
                    "-C",
                    str(root),
                    "-c",
                    "user.name=Fixture",
                    "-c",
                    "user.email=fixture@example.test",
                    "commit",
                    "--allow-empty",
                    "-qm",
                    "fixture",
                ],
                check=True,
            )
            (root / "checker.py").write_text("old checker")
            record = {
                "stage": "requirements_gather",
                "source_revision": support.snapshot(root)["revision"],
                "output": str(run / "rejected.json"),
                "failure_key": "failure",
            }
            Path(record["output"]).write_text('{"rejected":true}')
            state = {
                "status": "PAUSED_REPEATED_FAILURE",
                "next_stage": "requirements_gather",
                "settings": {"joint_planning": True},
                "stages": [record],
                "pending_report_repair": {"original": record, "attempts": 2},
                "failure_history": {"failure": {"count": 3, "identity": {"error_class": "ValueError"}}},
            }
            before = copy.deepcopy(state)
            with self.assertRaises(support.Paused):
                runner.repeated_failure_resume_guard(state, root)
            self.assertEqual(before, state)
            (root / "checker.py").write_text("fixed checker")
            runner.repeated_failure_resume_guard(state, root)
            self.assertTrue(runner.prepare_planning_retry(state, run))
            self.assertNotIn("pending_report_repair", state)
            self.assertEqual(before["pending_report_repair"], state["report_repair_archive"][-1]["repair"])
            self.assertEqual(before["stages"], state["stages"])
            self.assertEqual('{"rejected":true}', Path(record["output"]).read_text())
            self.assertEqual("PAUSED_REPEATED_FAILURE", state["status"])


if __name__ == "__main__":
    unittest.main()
