"""Criteria trace to the original request, mechanically (tools/autocode_source_trace.py, #595)."""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_brief_obligations as obligations
import autocode_control_replies as control
import autocode_source_log as source_log
import autocode_source_trace as trace

TASK = (
    "Build a batch CLI. `cli run FILE` processes up to 5000 rows and exits 0. "
    "The tool must never write outside the project. "
    "Do not build a web UI."
)


def make_state(task=TASK):
    state = {"task": task}
    source_log.seed(state)
    return state


def criterion(cid, text, quotes, *, source_id="task:0"):
    return {
        "id": cid,
        "criterion": text,
        "verification_method": "test: behavior",
        "human_review": False,
        "sources": [{"source_id": source_id, "quote": quote} for quote in quotes],
    }


def body(criteria, **overrides):
    result = {
        "acceptance_criteria": criteria,
        "required_behaviors": [],
        "constraints": [],
    }
    result.update(overrides)
    return result


def wrapper(**kwargs):
    return {"version": 1, "out_of_scope": [], "negation_waivers": [], "review": {}, **kwargs}


class CitationSyntaxTests(unittest.TestCase):
    def test_an_unknown_source_is_refused(self):
        state = make_state()
        draft = body([criterion("C1", TASK, [TASK])])
        draft["acceptance_criteria"][0]["sources"] = [{"source_id": "task:9", "quote": TASK}]
        with self.assertRaisesRegex(ValueError, "without quoting it verbatim"):
            trace.check_citations(draft, obligations.sources(state))

    def test_a_non_verbatim_quote_is_refused(self):
        state = make_state()
        draft = body([criterion("C1", TASK, ["up to 5001 rows"])])
        with self.assertRaisesRegex(ValueError, "without quoting it verbatim"):
            trace.check_citations(draft, obligations.sources(state))

    def test_a_quote_across_a_hard_wrapped_line_is_verbatim(self):
        # #1777: every live GLM Requirements report on a hard-wrapped brief quoted "False,\noffset" as "False, offset".
        wrapped = "Extend query(records, *, descending=False,\noffset=0, limit=None). Existing calls return\nall rows."
        state = make_state(wrapped)
        quote = "Extend query(records, *, descending=False, offset=0, limit=None)."
        trace.check_citations(body([criterion("C1", "keeps the signature", [quote])]), obligations.sources(state))
        for changed in (
            "Extend query(records, *, descending=True, offset=0, limit=None).",
            "descending=False,offset=0",
        ):
            with self.subTest(changed=changed), self.assertRaisesRegex(ValueError, "without quoting it verbatim"):
                trace.check_citations(body([criterion("C1", "x", [changed])]), obligations.sources(state))

    def test_an_empty_citation_array_is_a_draft_only_state(self):
        state = make_state()
        draft = body([criterion("C1", TASK, [])])
        draft["acceptance_criteria"][0]["sources"] = []
        # Strict output schemas force the key, so a draft may leave it empty; a
        # contract where nothing cites at all predates the trace and passes.
        trace.check_citations(draft, obligations.sources(state))
        trace.check_ready(draft, obligations.sources(state), wrapper())
        # Mixed with a cited criterion, the uncited one is an invented requirement.
        draft["acceptance_criteria"].append(criterion("C2", TASK, [TASK]))
        with self.assertRaisesRegex(ValueError, "must cite its source"):
            trace.check_ready(draft, obligations.sources(state), wrapper())

    def test_duplicate_citations_are_refused(self):
        state = make_state()
        draft = body([criterion("C1", TASK, [TASK])])
        draft["acceptance_criteria"][0]["sources"] *= 2
        with self.assertRaisesRegex(ValueError, "twice"):
            trace.check_citations(draft, obligations.sources(state))


class ReadyGateTests(unittest.TestCase):
    def setUp(self):
        self.records = obligations.sources(make_state())

    def ready(self, draft, wrap=None, *, records=None):
        trace.check_ready(draft, records if records is not None else self.records, wrap or wrapper())

    def test_acceptable_paraphrase_is_accepted(self):
        draft = body(
            [
                criterion(
                    "C1",
                    "`cli run FILE` processes at most 5000 rows, exits 0, never writes "
                    "outside the project, and no web UI is built",
                    [TASK],
                )
            ]
        )
        self.ready(draft)

    def test_an_invented_requirement_with_no_source_is_refused(self):
        draft = body([criterion("C1", TASK, [TASK]), criterion("C2", "The CLI has an JSON export", [])])
        draft["acceptance_criteria"][1].pop("sources")
        with self.assertRaisesRegex(ValueError, "must cite its source"):
            self.ready(draft)

    def test_an_omitted_constraint_is_refused(self):
        cited = "Build a batch CLI. `cli run FILE` processes up to 5000 rows and exits 0."
        draft = body(
            [criterion("C1", "Running a file processes at most 5000 rows and exits 0", [cited])],
            required_behaviors=["Never writes outside the project", "no web UI"],
        )
        # The other two sentences of the brief are neither cited nor out of scope.
        with self.assertRaisesRegex(ValueError, "neither cited nor out of scope"):
            self.ready(draft)

    def test_a_verbatim_span_inside_a_sentence_covers_that_sentence(self):
        # Live planners cite the command clause, not the "Commands:" lead-in.
        sentence = "Commands: `cli run FILE` processes up to 5000 rows and exits 0."
        span = "`cli run FILE` processes up to 5000 rows and exits 0."
        task = (
            "Build a batch CLI. " + sentence + " The tool must never write outside the project. Do not build a web UI."
        )
        state = make_state(task)
        draft = body(
            [
                criterion(
                    "C1",
                    "Running a file processes at most 5000 rows, exits 0, never writes "
                    "outside the project, and no web UI is built",
                    [
                        "Build a batch CLI.",
                        span,
                        "The tool must never write outside the project.",
                        "Do not build a web UI.",
                    ],
                )
            ]
        )
        self.ready(draft, records=obligations.sources(state))

    def test_a_changed_numeric_limit_is_refused(self):
        draft = body(
            [
                criterion(
                    "C1",
                    "Running a file processes at most 500 rows, exits 0, never writes "
                    "outside the project, and no web UI is built",
                    [TASK],
                )
            ]
        )
        with self.assertRaisesRegex(ValueError, "changed a number"):
            self.ready(draft)

    def test_a_standards_name_is_not_a_number_the_criteria_must_repeat(self):
        # Live bugfix-iso-weeks (#1689): "ISO 8601 weeks" was refused because no criterion said 8601.
        text = "The README promises ISO 8601 weeks, so the four days form one `2025-W01` row with 29.50h."
        records = [{"id": "answer:weeks", "kind": "answer", "text": text}]
        kept = "weekly_totals returns one `2025-W01` row with 29.50h, keyed by ISO week (date.isocalendar())"
        draft = body([criterion("C1", kept, [text], source_id="answer:weeks")])
        trace.check_citations(draft, records)
        self.ready(draft, records=records)
        dropped = "weekly_totals returns one `2025-W01` row, keyed by ISO 8601 week"
        draft = body([criterion("C1", dropped, [text], source_id="answer:weeks")])
        with self.assertRaisesRegex(ValueError, "changed a number the source states: 29.50"):
            self.ready(draft, records=records)

    def test_an_issue_reference_is_not_a_number_the_criteria_must_repeat(self):
        # Live OSS bugfix (#1874): "Fix issue #474 ..." was refused because no criterion said 474.
        text = "Fix issue #474 in this repo: from_html() raises ValueError on a ragged HTML table."
        records = [{"id": "task:0", "kind": "task", "text": text}]
        kept = "from_html() returns the table for a ragged HTML table instead of raising ValueError"
        self.ready(body([criterion("C1", kept, [text])]), records=records)
        # A stated value beside the reference still has to survive.
        valued = "Fix issue #474: process exactly 7 rows."
        changed = body([criterion("C1", "Fix issue #474: process rows.", [valued])])
        with self.assertRaisesRegex(ValueError, "changed a number the source states: 7"):
            self.ready(changed, records=[{"id": "task:0", "kind": "task", "text": valued}])

    def test_a_dropped_literal_is_refused(self):
        draft = body(
            [
                criterion(
                    "C1",
                    "Running a file processes at most 5000 rows, exits 0, never writes "
                    "outside the project, and no web UI is built",
                    [TASK],
                )
            ]
        )
        with self.assertRaisesRegex(ValueError, "dropped a literal"):
            self.ready(draft)

    def test_documentary_location_labels_do_not_become_product_values(self):
        for location in ("tools/parser.py:292–363", "src/parser.py:292-363", "parser.py:292"):
            with self.subTest(location=location):
                text = f"- `{location}`: reject unknown event references and preserve exit code 7."
                records = [{"id": "task:0", "kind": "task", "text": text}]
                kept = body([criterion("C1", "Reject unknown event references and preserve exit code 7.", [text])])
                trace.check_citations(kept, records)
                self.ready(kept, records=records)
                changed = body([criterion("C1", "Reject unknown event references and preserve exit codes.", [text])])
                with self.assertRaisesRegex(ValueError, "changed a number the source states: 7"):
                    self.ready(changed, records=records)

    def test_known_source_record_references_do_not_introduce_numeric_values(self):
        for text in ("The original task:0 requires exactly 7 rows.", "Copied from task:0, process exactly 7 rows."):
            with self.subTest(text=text):
                records = [{"id": "task:0", "kind": "task", "text": text}]
                self.ready(body([criterion("C1", "Process exactly 7 rows.", [text])]), records=records)
                with self.assertRaisesRegex(ValueError, "changed a number the source states: 7"):
                    self.ready(body([criterion("C1", "Process rows.", [text])]), records=records)

    def test_location_outputs_unknown_source_ids_and_real_examples_remain_protected(self):
        cases = (
            ("Print exactly:\n- `tools/parser.py:292–363`: location.", "Print a location.", "changed a number"),
            ("Print `tools/parser.py:292–363` exactly.", "Print a source location.", "changed a number"),
            ("Print original task:0 exactly.", "Print a source identifier.", "changed a number"),
            (
                "Emit `task:0` exactly, with exit code 7.",
                "Emit a source identifier with exit code 7.",
                "changed a number",
            ),
            ("The original task:9 requires exactly 7 rows.", "Process exactly 7 rows.", "changed a number"),
            (
                '- `tools/parser.py:292–363`: reject `{command: null, evidence_ref: "event:", exit_code: null}`.',
                "Reject a check with missing command metadata.",
                "dropped a literal",
            ),
        )
        for text, criterion_text, error in cases:
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, error):
                self.ready(
                    body([criterion("C1", criterion_text, [text])]),
                    records=[{"id": "task:0", "kind": "task", "text": text}],
                )

    def test_a_dropped_negation_is_refused(self):
        draft = body(
            [
                criterion(
                    "C1",
                    "`cli run FILE` processes at most 5000 rows and exits 0; the tool "
                    "writes outside the project; a web UI is built",
                    [TASK],
                )
            ]
        )
        with self.assertRaisesRegex(ValueError, "dropped the source's negation"):
            self.ready(draft)

    def test_a_dropped_negation_stands_only_with_the_pinned_waiver(self):
        draft = body(
            [
                criterion(
                    "C1",
                    "`cli run FILE` processes at most 5000 rows and exits 0",
                    [TASK],
                )
            ]
        )
        waived = wrapper(
            negation_waivers=[
                {
                    "source_id": "task:0",
                    "quote": "The tool must never write outside the project.",
                    "reason": "The runner enforces the sandbox; the criterion need not restate it",
                },
                {
                    "source_id": "task:0",
                    "quote": "Do not build a web UI.",
                    "reason": "Scope exclusion carried in the contract",
                },
            ]
        )
        self.ready(draft, waived)

    def test_a_dropped_non_goal_is_refused(self):
        cited = TASK.replace(" Do not build a web UI.", "")
        draft = body(
            [
                criterion(
                    "C1",
                    "`cli run FILE` processes at most 5000 rows, exits 0, never writes outside the project",
                    [cited],
                )
            ]
        )
        with self.assertRaisesRegex(ValueError, "neither cited nor out of scope"):
            self.ready(draft)

    def test_out_of_scope_covers_a_deliberate_exclusion(self):
        cited = TASK.replace(" Do not build a web UI.", "")
        draft = body(
            [
                criterion(
                    "C1",
                    "`cli run FILE` processes at most 5000 rows, exits 0, never writes outside the project",
                    [cited],
                )
            ]
        )
        out = wrapper(
            out_of_scope=[{"source_id": "task:0", "quote": "Do not build a web UI.", "reason": "User deferred the UI"}]
        )
        self.ready(draft, out)

    def test_an_exclusion_cannot_conflict_with_another_sources_citation(self):
        # Minimal shape of a saved final report: one answer is cited, while a
        # longer overlapping answer is deliberately excluded by its own ID.
        records = [
            {"id": "answer:first", "kind": "answer", "text": "plan"},
            {"id": "answer:second", "kind": "answer", "text": "unblock the plan"},
        ]
        draft = body([criterion("C1", "Produce the plan", ["plan"], source_id="answer:first")])
        out = wrapper(
            out_of_scope=[
                {"source_id": "answer:second", "quote": "unblock the plan", "reason": "Decision already applied"}
            ]
        )
        trace.check_citations(draft, records)
        self.ready(draft, out, records=records)

    def test_a_citation_cannot_cover_the_same_text_from_another_source(self):
        records = [
            {"id": "answer:first", "kind": "answer", "text": "Build the plan."},
            {"id": "answer:second", "kind": "answer", "text": "Build the plan."},
        ]
        draft = body([criterion("C1", "Build the plan.", ["Build the plan."], source_id="answer:first")])
        trace.check_citations(draft, records)
        with self.assertRaisesRegex(ValueError, "answer:second sentence is neither cited nor out of scope"):
            self.ready(draft, records=records)

    def test_an_exclusion_cannot_excuse_another_sources_missing_citation(self):
        records = [
            {"id": "answer:first", "kind": "answer", "text": "Read the code. Unblock the plan."},
            {"id": "answer:second", "kind": "answer", "text": "plan"},
        ]
        draft = body([criterion("C1", "Read the code.", ["Read the code."], source_id="answer:first")])
        out = wrapper(
            out_of_scope=[
                {"source_id": "answer:first", "quote": "Unblock the plan.", "reason": "Decision already applied"}
            ]
        )
        trace.check_citations(draft, records)
        with self.assertRaisesRegex(ValueError, "answer:second sentence is neither cited nor out of scope"):
            self.ready(draft, out, records=records)

    def test_another_sources_criterion_cannot_preserve_dropped_obligations(self):
        cases = (
            ("Process exactly 7 rows.", "Process rows.", "changed a number"),
            ("Use `cli run FILE`.", "Run the CLI.", "dropped a literal"),
            ("Never write outside the project.", "Write outside the project.", "dropped the source's negation"),
        )
        for text, changed, reason in cases:
            with self.subTest(reason=reason):
                records = [
                    {"id": "answer:first", "kind": "answer", "text": text},
                    {"id": "answer:second", "kind": "answer", "text": text},
                ]
                draft = body(
                    [
                        criterion("C1", changed, [text], source_id="answer:first"),
                        criterion("C2", text, [text], source_id="answer:second"),
                    ]
                )
                trace.check_citations(draft, records)
                with self.assertRaisesRegex(ValueError, "answer:first " + reason):
                    self.ready(draft, records=records)

    def test_a_sentence_cannot_be_both_covered_and_out_of_scope(self):
        draft = body([criterion("C1", TASK + " never no", [TASK])])
        out = wrapper(out_of_scope=[{"source_id": "task:0", "quote": "Do not build a web UI.", "reason": "deferred"}])
        with self.assertRaisesRegex(ValueError, "both covered and out of scope"):
            self.ready(draft, out)

    def test_an_out_of_scope_quote_that_covers_nothing_is_refused(self):
        draft = body([criterion("C1", TASK + " never no", [TASK])])
        out = wrapper(out_of_scope=[{"source_id": "task:0", "quote": "rows and exits", "reason": "fragment"}])
        with self.assertRaisesRegex(ValueError, "covers no source sentence"):
            self.ready(draft, out)


class SentenceIdTests(unittest.TestCase):
    """Criteria cite a whole sentence by id; the runner resolves it to the verbatim text first (#1630)."""

    FIRST = "Build a batch CLI."
    SECOND = "`cli run FILE` processes up to 5000 rows and exits 0."
    THIRD = "Do not build a web UI."
    TEXT = " ".join((FIRST, SECOND, THIRD))

    def setUp(self):
        self.state = make_state(self.TEXT)
        self.records = obligations.sources(self.state)

    def test_sentences_are_numbered_per_source_and_map_the_requirement_rows(self):
        rows = trace.sentence_rows(
            self.state,
            self.records,
            [
                {"requirement_id": "R1", "source_quote": "processes up to 5000 rows"},
                {"requirement_id": "R2", "source_quote": self.THIRD},
                {"requirement_id": "R3", "source_quote": ""},
                {"requirement_id": "R4", "source_quote": "text from no source"},
            ],
        )
        self.assertEqual(
            [("task:0#1", self.FIRST, []), ("task:0#2", self.SECOND, ["R1"]), ("task:0#3", self.THIRD, ["R2"])],
            [(row["id"], row["text"], row["requirement_ids"]) for row in rows],
        )
        self.assertEqual({"task:0"}, {row["source_id"] for row in rows})

    def test_a_control_reply_gets_no_sentence_and_numbering_does_not_shift(self):
        event = {"kind": "answer", "actor": "user_cli", "question_id": "requirements-review-0123abcd", "text": "plan"}
        source_log.save_answer(self.state, "requirements-review-0123abcd", event)
        self.state.setdefault("user_events", []).append(event)
        rows = trace.sentence_rows(self.state, obligations.sources(self.state))
        self.assertEqual(["task:0#1", "task:0#2", "task:0#3"], [row["id"] for row in rows])

    def resolved(self, *quotes, source_id="task:0"):
        draft = body([criterion("C1", "A criterion", list(quotes), source_id=source_id)])
        trace.resolve_sentence_ids(draft, self.records)
        return [citation["quote"] for citation in draft["acceptance_criteria"][0]["sources"]]

    def test_an_id_becomes_the_sentence_and_everything_else_is_left_alone(self):
        self.assertEqual([self.SECOND, self.FIRST], self.resolved("task:0#2", "task:0#1"))
        self.assertEqual([self.SECOND], self.resolved(self.SECOND))
        self.assertEqual(["processes up to 5000 rows"], self.resolved("processes up to 5000 rows"))
        self.assertEqual(["task:0#4"], self.resolved("task:0#4"))  # past the last sentence
        self.assertEqual(["task:0#0"], self.resolved("task:0#0"))
        self.assertEqual(["answer:other#1"], self.resolved("answer:other#1"))  # another source's id
        self.assertEqual(["task:0#1"], self.resolved("task:0#1", source_id="answer:other"))  # no such source

    def test_a_resolved_citation_is_checked_and_covers_like_its_quote(self):
        draft = body([criterion("C1", self.TEXT + " never no", ["task:0#1", "task:0#2", "task:0#3"])])
        trace.resolve_sentence_ids(draft, self.records)
        trace.check_citations(draft, self.records)
        self.assertEqual([], trace.uncited_sentences(self.state, draft, self.records))
        trace.check_ready(draft, self.records, wrapper())

    def test_an_id_that_names_no_sentence_is_refused_with_a_hint(self):
        draft = body([criterion("C1", "A criterion", ["task:0#9"])])
        trace.resolve_sentence_ids(draft, self.records)
        with self.assertRaisesRegex(ValueError, r"without quoting it verbatim \(no such sentence id\)"):
            trace.check_citations(draft, self.records)

    def test_two_ids_for_one_sentence_are_the_duplicate_citation_they_resolve_to(self):
        draft = body([criterion("C1", "A criterion", ["task:0#2", self.SECOND])])
        trace.resolve_sentence_ids(draft, self.records)
        with self.assertRaisesRegex(ValueError, "twice"):
            trace.check_citations(draft, self.records)


class ControlReplyTests(unittest.TestCase):
    """Replies that only steer a runner-written question are not requirements to quote (#1630)."""

    def row(self, question_id, text, kind="user_answer"):
        return {"id": "answer:" + question_id, "kind": kind, "text": text}

    def test_the_registered_replies_are_control(self):
        for question_id, text in (
            ("requirements-review-0123abcd", "plan"),
            ("requirements-review-0123abcd", "  PLAN\n"),
            ("rewording-0123abcd", "Keep the approved wording"),
            ("rewording-0123abcd", "accept the new wording"),
        ):
            with self.subTest(question_id=question_id, text=text):
                self.assertTrue(control.control_record(self.row(question_id, text)))

    def test_anything_else_is_still_a_source(self):
        for question_id, text, kind in (
            ("requirements-review-0123abcd", "Also reject empty files.", "user_answer"),
            ("rewording-0123abcd", "Use the wording from the ticket.", "user_answer"),
            ("rewording-0123abcd", "plan", "user_answer"),  # a reply belongs to its own question
            ("Q1", "plan", "user_answer"),  # a model-written question never joins the registry
            ("Q1", "Keep the approved wording", "user_answer"),
            ("requirements-review-0123abcd", "plan", "user_feedback"),
        ):
            with self.subTest(question_id=question_id, text=text, kind=kind):
                self.assertFalse(control.control_record(self.row(question_id, text, kind)))
        self.assertFalse(control.control_record({"id": "task:0", "kind": "task", "text": "plan"}))
        self.assertFalse(control.control_record(None))

    def test_quotable_and_the_source_rule_leave_them_out(self):
        records = [
            {"id": "task:0", "kind": "task", "text": "Build it."},
            self.row("rewording-0123abcd", "Keep the approved wording"),
            self.row("Q1", "Use SQLite."),
        ]
        self.assertEqual(["task:0", "answer:Q1"], [row["id"] for row in trace.quotable(records)])
        self.assertNotIn("rewording", trace.rule(records))

    def test_a_contract_sealed_while_a_reply_was_quoted_still_verifies(self):
        state = make_state("Build it.")
        event = {
            "kind": "answer",
            "actor": "user_cli",
            "question_id": "rewording-0123abcd",
            "text": "Keep the approved wording",
        }
        source_log.save_answer(state, "rewording-0123abcd", event)
        state.setdefault("user_events", []).append(event)
        records = obligations.sources(state)
        self.assertEqual(2, len(records))
        quotes = [(row["id"], row["text"]) for row in records]
        draft = body(
            [criterion("C1", "Build it. Keep the approved wording", [text], source_id=sid) for sid, text in quotes]
        )
        report = {"source_trace": {"out_of_scope": [], "negation_waivers": []}}
        output = Path(tempfile.mkdtemp()) / "review.json"
        output.write_text(json.dumps(report))
        events = output.with_suffix(".jsonl")
        events.write_text(json.dumps({"type": "item.completed"}) + "\n")
        record = {"stage": "astra_finalize", "output": str(output), "events": str(events)}
        state["task_id"] = "task"
        sealed = trace.reviewed(state, draft, report["source_trace"], record, records)
        trace.verify(state, sealed, records, ready=True)


class UncitedSentenceTests(unittest.TestCase):
    """The sentences a draft leaves uncited, which the runner names before any review (#1630)."""

    FIRST = "Build a batch CLI."
    SECOND = "`cli run FILE` processes up to 5000 rows and exits 0."
    THIRD = "Do not build a web UI."
    TEXT = " ".join((FIRST, SECOND, THIRD))

    def setUp(self):
        self.state = make_state(self.TEXT)
        self.records = obligations.sources(self.state)

    def uncited(self, *quotes):
        draft = body([criterion("C1", "A criterion", list(quotes))] if quotes else [])
        return [row["sentence"] for row in trace.uncited_sentences(self.state, draft, self.records)]

    def test_a_contract_that_cites_nothing_predates_the_trace(self):
        self.assertEqual([], self.uncited())
        self.assertEqual(
            [], [r for r in trace.uncited_sentences(self.state, body([criterion("C1", "A", [])]), self.records)]
        )

    def test_lists_exactly_the_sentences_no_criterion_cites(self):
        self.assertEqual([self.THIRD], self.uncited(self.FIRST, self.SECOND))
        self.assertEqual([], self.uncited(self.FIRST, self.SECOND, self.THIRD))

    def test_a_span_inside_a_sentence_counts_as_citing_it(self):
        self.assertEqual([self.FIRST, self.THIRD], self.uncited("`cli run FILE` processes up to 5000 rows"))

    def test_rows_name_their_source(self):
        draft = body([criterion("C1", "A criterion", [self.FIRST])])
        rows = trace.uncited_sentences(self.state, draft, self.records)
        self.assertEqual({"task:0"}, {row["source_id"] for row in rows})
        self.assertEqual(set(), {key for row in rows for key in row} - {"source_id", "sentence"})

    def test_a_requirements_review_go_ahead_is_never_listed(self):
        event = {"kind": "answer", "actor": "user_cli", "question_id": "requirements-review-0123abcd", "text": "plan"}
        source_log.save_answer(self.state, "requirements-review-0123abcd", event)
        self.state.setdefault("user_events", []).append(event)
        records = obligations.sources(self.state)
        self.assertIn("answer:requirements-review-0123abcd", [row["id"] for row in records])
        draft = body([criterion("C1", "A criterion", [self.TEXT])])
        self.assertEqual([], trace.uncited_sentences(self.state, draft, records))

    def test_a_correction_the_draft_ignores_is_listed(self):
        event = {"kind": "answer", "actor": "user_cli", "question_id": "Q1", "text": "Also reject empty files."}
        source_log.save_answer(self.state, "Q1", event)
        self.state.setdefault("user_events", []).append(event)
        records = obligations.sources(self.state)
        draft = body([criterion("C1", "A criterion", [self.TEXT])])
        rows = trace.uncited_sentences(self.state, draft, records)
        self.assertEqual([("answer:Q1", "Also reject empty files.")], [(r["source_id"], r["sentence"]) for r in rows])

    def test_only_unexcused_repeats_are_refused(self):
        previous = [{"source_id": "task:0", "sentence": self.SECOND}, {"source_id": "task:0", "sentence": self.THIRD}]
        draft = body([criterion("C1", "A criterion", [self.FIRST])])

        def repeats(out_of_scope):
            return [
                row["sentence"]
                for row in trace.unexcused_repeats(self.state, draft, self.records, previous, out_of_scope)
            ]

        self.assertEqual([self.SECOND, self.THIRD], repeats([]))
        self.assertEqual([self.SECOND, self.THIRD], repeats(None))
        excuse = {"source_id": "task:0", "quote": self.THIRD, "reason": "Deferred"}
        self.assertEqual([self.SECOND], repeats([excuse]))
        # An excuse names its own source: the same words from another source do not excuse this one.
        self.assertEqual([self.SECOND, self.THIRD], repeats([{**excuse, "source_id": "answer:other"}]))
        # A sentence the earlier draft did not leave uncited is not a repeat, however uncited it is now.
        self.assertEqual(
            [self.SECOND],
            [r["sentence"] for r in trace.unexcused_repeats(self.state, draft, self.records, previous[:1], [])],
        )


class SealTests(unittest.TestCase):
    def setUp(self):
        scratch = tempfile.TemporaryDirectory()
        self.addCleanup(scratch.cleanup)
        self.root = Path(scratch.name)
        self.state = make_state()
        self.state["task_id"] = "task"

    def record(self, report, *, stage="astra_finalize"):
        output = self.root / "review.json"
        output.write_text(json.dumps(report))
        events = self.root / "review.jsonl"
        events.write_text(json.dumps({"type": "item.completed"}) + "\n")
        return {"stage": stage, "output": str(output), "events": str(events)}

    def draft(self):
        return body([criterion("C1", TASK + " never no", [TASK])])

    def answer(self, text, question_id="requirements-review-0123abcd"):
        event = {"kind": "answer", "actor": "user_cli", "question_id": question_id, "text": text}
        source_log.save_answer(self.state, question_id, event)
        self.state.setdefault("user_events", []).append(event)

    def test_a_requirements_review_go_ahead_is_not_an_obligation_to_quote(self):
        # #1630: a live GLM-5.3 draft cited every brief sentence but not the review's "plan" reply,
        # and the Plan Reviewer's seal refused it as untraced.
        self.answer("plan")
        records = obligations.sources(self.state)
        self.assertIn("answer:requirements-review-0123abcd", [row["id"] for row in records])
        report = {"source_trace": {"out_of_scope": [], "negation_waivers": []}}
        sealed = trace.reviewed(self.state, self.draft(), report["source_trace"], self.record(report), records)
        trace.verify(self.state, sealed, records, ready=True)
        self.assertEqual(["task:0"], [row["id"] for row in trace.quotable(records)])
        self.assertNotIn("requirements-review", trace.rule(records))
        # A contract sealed while the reply was still quoted keeps verifying.
        cited = self.draft()
        cited["acceptance_criteria"][0]["sources"].append(
            {"source_id": "answer:requirements-review-0123abcd", "quote": "plan"}
        )
        sealed = trace.reviewed(self.state, cited, report["source_trace"], self.record(report), records)
        trace.verify(self.state, sealed, records, ready=True)

    def test_a_requirements_review_correction_must_still_be_traced(self):
        self.answer("Also reject negative row counts.")
        records = obligations.sources(self.state)
        self.assertEqual(records, trace.quotable(records))
        report = {"source_trace": {"out_of_scope": [], "negation_waivers": []}}
        with self.assertRaisesRegex(ValueError, "neither cited nor out of scope: 'Also reject negative row counts.'"):
            trace.reviewed(self.state, self.draft(), report["source_trace"], self.record(report), records)

    def test_the_seal_round_trips_through_verify(self):
        report = {"source_trace": {"out_of_scope": [], "negation_waivers": []}}
        sealed = trace.reviewed(
            self.state, self.draft(), report["source_trace"], self.record(report), obligations.sources(self.state)
        )
        trace.verify(self.state, sealed, obligations.sources(self.state), ready=True)

    def followup(self):
        # Native #1697: bugfix -> build keeps a valid historical exclusion in the review.
        self.state = make_state("Do not add peek yet.")
        event = {
            "id": "peek-followup",
            "kind": "brief_feedback",
            "actor": "user_cli",
            "text": "Add peek with at most 7 tasks.",
        }
        source_log.save_feedback(self.state, event)
        self.state["turns"] = [{"event_id": event["id"], "say": event["text"], "previous": {"workflow": "bugfix"}}]
        self.state["workflow"] = {"kind": "build"}
        draft = body([criterion("C1", event["text"], [event["text"]], source_id="feedback:peek-followup")])
        report = {
            "source_trace": {
                "out_of_scope": [
                    {"source_id": "task:0", "quote": "Do not add peek yet.", "reason": "Earlier turn finished"}
                ],
                "negation_waivers": [],
            }
        }
        return draft, report, obligations.sources(self.state)

    def test_followup_seals_with_a_valid_historical_exclusion(self):
        draft, report, records = self.followup()
        sealed = trace.reviewed(self.state, draft, report["source_trace"], self.record(report), records)
        trace.verify(self.state, sealed, records, ready=True)
        self.assertEqual(report["source_trace"]["out_of_scope"], sealed["source_trace"]["out_of_scope"])

    def test_followup_verify_keeps_full_inventory_for_historical_exclusions(self):
        draft, report, records = self.followup()
        self.state["workflow"] = {"kind": "bugfix"}
        sealed = trace.reviewed(self.state, draft, report["source_trace"], self.record(report), records)
        self.state["workflow"] = {"kind": "build"}
        trace.verify(self.state, sealed, records, ready=True)

    def test_followup_still_rejects_a_fragment_of_a_historical_sentence(self):
        draft, report, records = self.followup()
        report["source_trace"]["out_of_scope"][0]["quote"] = "add peek"
        with self.assertRaisesRegex(ValueError, "covers no source sentence"):
            trace.reviewed(self.state, draft, report["source_trace"], self.record(report), records)

    def test_followup_still_rejects_a_fabricated_historical_source(self):
        draft, report, records = self.followup()
        report["source_trace"]["out_of_scope"][0]["source_id"] = "answer:invented"
        with self.assertRaisesRegex(ValueError, "without quoting it verbatim"):
            trace.reviewed(self.state, draft, report["source_trace"], self.record(report), records)

    def test_followup_still_rejects_a_changed_current_turn_limit(self):
        draft, report, records = self.followup()
        draft["acceptance_criteria"][0]["criterion"] = "Add peek with at most 8 tasks."
        with self.assertRaisesRegex(ValueError, "changed a number the source states: 7"):
            trace.reviewed(self.state, draft, report["source_trace"], self.record(report), records)

    def test_a_model_cannot_invent_or_rewrite_the_seal(self):
        report = {"source_trace": {"out_of_scope": [], "negation_waivers": []}}
        record = self.record(report)
        sealed = trace.reviewed(
            self.state, self.draft(), report["source_trace"], record, obligations.sources(self.state)
        )
        forged = copy.deepcopy(sealed)
        forged["source_trace"]["out_of_scope"] = [
            {"source_id": "task:0", "quote": "Do not build a web UI.", "reason": "forged"}
        ]
        with self.assertRaisesRegex(ValueError, "missing or changed|Invalid source-trace"):
            trace.verify(self.state, forged, obligations.sources(self.state), ready=True)

    def test_a_planner_or_edited_contract_cannot_replace_the_seal(self):
        report = {"source_trace": {"out_of_scope": [], "negation_waivers": []}}
        sealed = trace.reviewed(
            self.state, self.draft(), report["source_trace"], self.record(report), obligations.sources(self.state)
        )
        self.state["goal_contract"] = {"body": sealed}
        changed = copy.deepcopy(sealed)
        changed["source_trace"] = dict(changed["source_trace"], version=2)
        with self.assertRaisesRegex(ValueError, "cannot replace"):
            trace.prepare(self.state, changed, "glm_draft")
        fresh = self.draft()
        carried = trace.prepare(self.state, fresh, "glm_draft")
        self.assertEqual(sealed["source_trace"], carried["source_trace"])

    def test_a_review_without_source_trace_seals_the_strict_default(self):
        sealed = trace.reviewed(self.state, self.draft(), None, self.record({}), obligations.sources(self.state))
        self.assertEqual(trace.DEFAULT_TRACE["out_of_scope"], sealed["source_trace"]["out_of_scope"])
        self.assertEqual(trace.DEFAULT_TRACE["negation_waivers"], sealed["source_trace"]["negation_waivers"])
        trace.verify(self.state, sealed, obligations.sources(self.state), ready=True)

    def test_a_body_without_the_seal_validates_as_before(self):
        # Contracts sealed before this change carry no source_trace key.
        trace.verify(self.state, self.draft(), obligations.sources(self.state), ready=True)

    def test_reviewed_body_seals_only_with_evidence_citations_or_an_explicit_trace(self):
        plain = body([{"id": "C1", "criterion": TASK, "verification_method": "test", "human_review": False}])
        # Synthetic apply_planning helpers pass only an output path.
        self.assertEqual(plain, obligations.reviewed_body(self.state, plain, [], {"output": "final.json"}))
        sealed = obligations.reviewed_body(
            self.state, plain, [], self.record({}), trace={"out_of_scope": [], "negation_waivers": []}
        )
        self.assertIn("source_trace", sealed)
        with self.assertRaisesRegex(ValueError, "execution evidence|report"):
            obligations.reviewed_body(self.state, self.draft(), [], {"stage": "astra_finalize", "output": "final.json"})


class CompletionVerdictSchemaTests(unittest.TestCase):
    """The Completion Reviewer's schema carries source_verdict only on a traced contract (#595)."""

    def request_schema(self, state, tag):
        from units import autoreview

        root = Path(state["workspace"])
        schema_dir = root / f"schemas-{tag}"
        shutil.copytree(Path(__file__).resolve().parents[1] / "tools" / "autocode-schemas", schema_dir)
        request = autoreview.prepare(state, "astra_review", root / "state.json", schema_dir)
        return request.schema

    def test_schema_follows_the_seal(self):
        import autocode_goals as goals
        from goal_fixtures import approve_fixture

        from tests import test_autocode

        fixture = test_autocode.RetrofitTest(methodName="run")
        fixture.setUp()
        self.addCleanup(fixture.temp.cleanup)
        state = fixture.state
        approve_fixture(state, goals)
        state["status"] = "RUNNING"
        self.assertNotIn("source_verdict", self.request_schema(state, "plain")["properties"])
        contract = state["goal_contract"]
        contract["body"]["source_trace"] = {
            "version": 1,
            "out_of_scope": [],
            "negation_waivers": [],
            "review": {},
        }
        # Re-seal the mutated contract the way install_draft computes its hash.
        import autocode_util as util

        contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
        contract["approval_event"]["token"] = goals.token(contract)
        state["displayed_goal"] = goals.token(contract)
        schema = self.request_schema(state, "traced")
        self.assertIn("source_verdict", schema["properties"])
        self.assertEqual(["PASS", "FAIL"], schema["properties"]["source_verdict"]["properties"]["verdict"]["enum"])


class CompletionVerdictConsistencyTests(unittest.TestCase):
    """A completion its own source verdict contradicts is an invalid report (#1909).

    The reviewer's prompt says a FAIL without a real gap only costs a repair; before this the
    field was never read, so a COMPLETE beside a FAIL verdict completed the run.
    """

    FAIL = {"verdict": "FAIL", "uncovered": ["Totals round half up."], "summary": "One sentence is not honored"}
    PASS = {"verdict": "PASS", "uncovered": [], "summary": "Every sentence is honored"}

    def check(self, status, verdict):
        import autocode_completion_result as completion_result

        value = {"status": status}
        if verdict is not None:
            value["source_verdict"] = verdict
        completion_result.check_source_verdict(value)

    def test_a_completion_beside_a_fail_or_an_uncovered_sentence_is_refused(self):
        for status in ("COMPLETE", "TASK_COMPLETE"):
            for verdict in (self.FAIL, {**self.PASS, "uncovered": ["Totals round half up."]}):
                with self.subTest(status=status, verdict=verdict["verdict"]):
                    with self.assertRaisesRegex(ValueError, "Totals round half up.*REWORK"):
                        self.check(status, verdict)
        with self.assertRaisesRegex(ValueError, "none named"):
            self.check("TASK_COMPLETE", {**self.FAIL, "uncovered": []})

    def test_a_consistent_completion_passes(self):
        # A REWORK beside a FAIL never reaches this check: only a completion being applied does.
        for verdict in (self.PASS, None):  # None: an untraced contract's report carries no verdict
            with self.subTest(verdict=verdict and verdict["verdict"]):
                self.check("TASK_COMPLETE", verdict)

    def approved_run(self):
        import autocode_goals as goals
        from goal_fixtures import approve_fixture

        from tests import test_autocode

        fixture = test_autocode.RetrofitTest(methodName="run")
        fixture.setUp()
        self.addCleanup(fixture.temp.cleanup)
        approve_fixture(fixture.state, goals)
        return fixture

    def review(self, fixture, verdict, **changes):
        from goal_fixtures import envelope

        value = {**fixture.decision("TASK_COMPLETE"), **envelope(fixture.state), **changes}
        value["source_verdict"] = verdict
        return value

    def test_the_review_step_refuses_the_contradiction_before_completing(self):
        from tests import test_autocode

        fixture = self.approved_run()
        before = fixture.state["status"]
        with self.assertRaisesRegex(ValueError, "source_verdict is FAIL"):
            test_autocode.runner.apply_result(
                fixture.state, "astra_review", self.review(fixture, self.FAIL), {}, fixture.root, fixture.run
            )
        self.assertEqual(before, fixture.state["status"])

    def test_a_consistent_review_goes_on_to_the_completion_gate(self):
        # The control: on this fixture (no validation yet) a PASS verdict passes the check and the
        # completion gate refuses it; on master the FAIL verdict above went the same way.
        import autocode_util as util

        from tests import test_autocode

        fixture = self.approved_run()
        with self.assertRaises(util.Paused) as paused:
            test_autocode.runner.apply_result(
                fixture.state, "astra_review", self.review(fixture, self.PASS), {}, fixture.root, fixture.run
            )
        self.assertIn("Completion rejected", str(paused.exception))

    def test_a_stale_contradictory_review_still_pauses_instead_of_going_back_for_repair(self):
        import autocode_util as util

        from tests import test_autocode

        fixture = self.approved_run()
        stale = self.review(fixture, self.FAIL, contract_revision=fixture.state["goal_contract"]["revision"] + 1)
        with self.assertRaises(util.Paused) as paused:
            test_autocode.runner.apply_result(fixture.state, "astra_review", stale, {}, fixture.root, fixture.run)
        self.assertEqual("PAUSED_STALE_GOAL", paused.exception.status)


class SourceCitationRuleTests(unittest.TestCase):
    def test_rule_lists_source_ids_without_repeating_verbatim_text(self):
        # #1337: the instruction named every source with its full text beside source_inventory.
        records = [{"id": "task:0", "kind": "task", "text": "A long program-child brief " + ("y" * 512)}]
        text = trace.rule(records)
        self.assertIn("[task:0] kind=task", text)
        self.assertNotIn(records[0]["text"], text)
        self.assertIn("source_inventory", text)


if __name__ == "__main__":
    unittest.main()
