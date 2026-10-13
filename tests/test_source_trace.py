"""Criteria trace to the original request, mechanically (tools/autocode_source_trace.py, #595)."""

import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import autocode_brief_obligations as obligations
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


def criterion(cid, text, quotes):
    return {
        "id": cid,
        "criterion": text,
        "verification_method": "test: behavior",
        "human_review": False,
        "sources": [{"source_id": "task:0", "quote": quote} for quote in quotes],
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

    def ready(self, draft, wrap=None):
        trace.check_ready(draft, self.records, wrap or wrapper())

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
            out_of_scope=[
                {"source_id": "task:0", "quote": "Do not build a web UI.", "reason": "User deferred the UI"}
            ]
        )
        self.ready(draft, out)

    def test_a_sentence_cannot_be_both_covered_and_out_of_scope(self):
        draft = body([criterion("C1", TASK + " never no", [TASK])])
        out = wrapper(
            out_of_scope=[{"source_id": "task:0", "quote": "Do not build a web UI.", "reason": "deferred"}]
        )
        with self.assertRaisesRegex(ValueError, "both covered and out of scope"):
            self.ready(draft, out)

    def test_an_out_of_scope_quote_that_covers_nothing_is_refused(self):
        draft = body([criterion("C1", TASK + " never no", [TASK])])
        out = wrapper(
            out_of_scope=[{"source_id": "task:0", "quote": "rows and exits", "reason": "fragment"}]
        )
        with self.assertRaisesRegex(ValueError, "covers no source sentence"):
            self.ready(draft, out)


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

    def test_the_seal_round_trips_through_verify(self):
        report = {"source_trace": {"out_of_scope": [], "negation_waivers": []}}
        sealed = trace.reviewed(
            self.state, self.draft(), report["source_trace"], self.record(report), obligations.sources(self.state)
        )
        trace.verify(self.state, sealed, obligations.sources(self.state), ready=True)

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
        sealed = trace.reviewed(
            self.state, self.draft(), None, self.record({}), obligations.sources(self.state)
        )
        self.assertEqual(trace.DEFAULT_TRACE["out_of_scope"], sealed["source_trace"]["out_of_scope"])
        self.assertEqual(trace.DEFAULT_TRACE["negation_waivers"], sealed["source_trace"]["negation_waivers"])
        trace.verify(self.state, sealed, obligations.sources(self.state), ready=True)

    def test_a_body_without_the_seal_validates_as_before(self):
        # Contracts sealed before this change carry no source_trace key.
        trace.verify(self.state, self.draft(), obligations.sources(self.state), ready=True)

    def test_reviewed_body_seals_only_with_evidence_citations_or_an_explicit_trace(self):
        plain = body(
            [{"id": "C1", "criterion": TASK, "verification_method": "test", "human_review": False}]
        )
        # Synthetic apply_planning helpers pass only an output path.
        self.assertEqual(plain, obligations.reviewed_body(self.state, plain, [], {"output": "final.json"}))
        sealed = obligations.reviewed_body(
            self.state, plain, [], self.record({}), trace={"out_of_scope": [], "negation_waivers": []}
        )
        self.assertIn("source_trace", sealed)
        with self.assertRaisesRegex(ValueError, "execution evidence|report"):
            obligations.reviewed_body(
                self.state, self.draft(), [], {"stage": "astra_finalize", "output": "final.json"}
            )


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
        from tests import test_autocode
        from goal_fixtures import approve_fixture
        import autocode_goals as goals

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

        contract["hash"] = util.digest(
            {key: contract[key] for key in ("task_id", "revision", "body")}
        )
        contract["approval_event"]["token"] = goals.token(contract)
        state["displayed_goal"] = goals.token(contract)
        schema = self.request_schema(state, "traced")
        self.assertIn("source_verdict", schema["properties"])
        self.assertEqual(["PASS", "FAIL"], schema["properties"]["source_verdict"]["properties"]["verdict"]["enum"])


if __name__ == "__main__":
    unittest.main()
