"""The design workflow's Architect: review a design without building anything, or hand a
request for a new design on to the build pipeline."""
import copy
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import autocode_design_job as design_job
import autocode_follow_up as follow_up
import autocode_jobs as jobs
import autocode_run_view as run_view
import autocode_workflows as workflows
import autopilot
from units import autoreview


def state_for(workspace="/nowhere", task="Review the design in docs/design/kafka-events.md."):
    return {"version": 3, "task": task, "workspace": workspace, "status": "RUNNING", "stages": [],
            "workflow": {"kind": "design", "then": "requirements_gather"},
            "settings": {"joint_planning": True, "roles": {
                "requirements": {"model": "r"}, "glm": {"model": "g"}, "plan_reviewer": {"model": "p", "engine": "codex"},
                "astra": {"model": "a", "engine": "codex"}, "terra": {"model": "t"}, "sol": {"model": "s"}}}}


def report(mode="review", **overrides):
    value = {"mode": mode, "design_under_review": "docs/design/kafka-events.md", "verdict": "request_changes",
             "summary": "Ordering is lost", "satisfied": ["Throughput: 24 partitions cover 5,000/s"],
             "concerns": [{"id": "F1", "area": "ordering", "severity": "blocking",
                           "summary": "Keyed by kind; per-domain order is lost", "evidence": "Producer paragraph",
                           "example": "Given create and renew for one domain; when they land on two partitions; "
                                      "then renew can be processed first", "probe": ""}],
             "questions": [{"id": "Q1", "question": "Is ordering per domain required?", "options": ["yes", "no"]}]}
    if mode == "propose":
        value.update(design_under_review="", verdict="not_applicable", satisfied=[], concerns=[], questions=[])
    value.update(overrides)
    return value


class RoutingTests(unittest.TestCase):
    def test_a_recognized_design_job_goes_to_the_architect(self):
        state = state_for()
        workflows.begin(state, "requirements_gather")
        workflows.apply(state, {"workflow": "design", "reason": "", "signals": []}, {})
        self.assertEqual(design_job.STAGE, state["next_stage"])
        self.assertEqual("autoreview", autopilot.unit_for(design_job.STAGE))
        self.assertIn(design_job.STAGE, jobs.STAGES)


class PrepareTests(unittest.TestCase):
    def test_architect_inherits_the_plan_reviewer_model_on_its_own_route(self):
        with tempfile.TemporaryDirectory() as workspace:
            state = state_for(workspace)
            request = autoreview.prepare(state, design_job.STAGE, "/run/state.json", None)
        self.assertEqual(("astra", "architect", True), (request.role, request.route_role, request.allow_write))
        roles = state["settings"]["roles"]
        self.assertEqual((roles["plan_reviewer"]["model"], roles["plan_reviewer"]["engine"]),
                         (roles["architect"]["model"], roles["architect"]["engine"]))
        self.assertEqual(design_job.SCHEMA, request.schema)
        self.assertIn("kafka-events.md", request.prompt)
        # A first review is asked as before revisions existed.
        self.assertNotIn("REVISING YOUR REVIEW", request.prompt)
        self.assertNotIn("previous_review", design_job.packet(state))

    def test_prompt_puts_an_open_requirement_choice_to_the_requester(self):
        """A blocking concern hinging on an unspecified guarantee must become a question, not an assumption."""
        text, _ = design_job.prompt(state_for("/nowhere"), {})
        self.assertIn("requirement choice open", text)
        self.assertIn("whether strict ordering is required", text)
        self.assertIn("ask it here rather than assuming one interpretation", text)
        self.assertIn("already settles", text)

    def test_architect_effort_is_capped_at_medium_but_never_raised(self):
        for given, expected in (("max", "medium"), ("xhigh", "medium"), ("high", "medium"),
                                ("medium", "medium"), ("low", "low"), (None, "medium"), ("weird", "medium")):
            roles = {"plan_reviewer": {"model": "p", "reasoning_effort": given}, "astra": {"model": "a"}}
            with self.subTest(given=given):
                self.assertEqual(expected, autoreview.architect_route(roles)["reasoning_effort"])
                self.assertEqual(given, roles["plan_reviewer"]["reasoning_effort"], "the Plan Reviewer is untouched")


class ApplyTests(unittest.TestCase):
    def apply(self, value, changed=()):
        workspace = tempfile.mkdtemp()
        state = state_for(workspace)
        autoreview.apply_job(design_job.STAGE, state, value, {"changed_files": list(changed), "output": "o"}, workspace)
        return state, Path(workspace)

    def test_a_review_writes_the_report_and_completes_without_building(self):
        state, workspace = self.apply(report())
        written = json.loads((workspace / "review" / "design-review.json").read_text())
        self.assertEqual(("request_changes", ["F1"], ["Q1"]),
                         (written["verdict"], [c["id"] for c in written["concerns"]], [q["id"] for q in written["questions"]]))
        view = run_view.view(state)
        self.assertTrue(view["done"])
        self.assertEqual("design", view["workflow"])
        self.assertIs(design_job, jobs.ended_in(state))
        self.assertIn("1 blocking, 0 advisory, 1 question(s) for you", jobs.render(state, lambda _: "build"))

    def test_the_reply_hint_appears_only_when_the_review_asks_something(self):
        # A design review never waits: its questions are answered with --follow-up in the next turn.
        hint = "Reply with --follow-up TEXT to answer them in this run."
        asked, _ = self.apply(report())
        self.assertIn(hint, design_job.render(asked))
        settled, _ = self.apply(report(questions=[]))
        self.assertNotIn("--follow-up", design_job.render(settled))

    def test_a_request_for_a_new_design_is_handed_to_the_build_pipeline(self):
        state, workspace = self.apply(report("propose"))
        self.assertFalse((workspace / "review").exists())
        self.assertEqual(("RUNNING", "requirements_gather"), (state["status"], state["next_stage"]))
        self.assertIsNone(jobs.ended_in(state))

    def test_a_design_review_that_changed_the_repository_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "must not change the repository.*docs/design/kafka-events.md"):
            self.apply(report(), changed=["docs/design/kafka-events.md"])

    def test_verdict_must_match_the_blocking_concerns(self):
        with self.assertRaisesRegex(ValueError, "verdict"):
            self.apply(report(verdict="approve"))
        with self.assertRaisesRegex(ValueError, "verdict"):
            self.apply(report(verdict="request_changes", concerns=[]))

    def test_a_sound_design_is_approved_with_no_blocking_concerns(self):
        advisory = [{"id": "S1", "area": "ops", "severity": "advisory", "summary": "retention", "evidence": "x"}]
        state, workspace = self.apply(report(verdict="approve", concerns=advisory))
        self.assertEqual("approve", json.loads((workspace / "review" / "design-review.json").read_text())["verdict"])
        self.assertEqual(0, state["design_review"]["blocking"])

    def test_propose_mode_may_not_smuggle_a_review(self):
        with self.assertRaisesRegex(ValueError, "handed on, not reviewed"):
            self.apply(report("propose", concerns=report()["concerns"]))


def concern(id, severity="blocking", status="open", resolution="", **fields):
    return {"id": id, "area": fields.pop("area", "ordering"), "severity": severity, "status": status,
            "resolution": resolution, "summary": fields.pop("summary", f"concern {id}"), "evidence": "design text",
            "example": "Given a transfer; when events split; then order is lost" if severity == "blocking" else "",
            "probe": "", **fields}


class RevisionTests(unittest.TestCase):
    """A reply to a finished design review makes the Architect revise it (issue #185): same ids, resolved
    concerns kept, and the whole trail in the report."""

    def setUp(self):
        self.workspace = Path(tempfile.mkdtemp(prefix="design-revision-"))
        self.state = state_for(str(self.workspace))
        first = report(concerns=[concern("F1", area="migration"), concern("F2", "advisory", area="dlq")],
                       questions=[{"id": "Q1", "question": "Which ordering do consumers need?", "options": ["a"]}])
        autoreview.apply_job(design_job.STAGE, self.state, first, {"changed_files": [], "output": "o1"}, self.workspace)

    def reply(self, say, kind="design"):
        follow_up.accept(self.state, say, self.workspace, "t")
        workflows.apply(self.state, {"workflow": kind, "reason": "", "signals": []}, {})

    def revise(self, **overrides):
        value = report(concerns=[concern("F1", area="migration"), concern("F2", "advisory", area="dlq"),
                                 concern("O1", summary="a transfer splits a domain's events")],
                       questions=[{"id": "Q2", "question": "Is per-registry order acceptable?", "options": ["yes"]}])
        value.update(overrides)
        return value

    def saved(self):
        return json.loads((self.workspace / design_job.REPORT_PATH).read_text())

    def test_a_first_review_is_revision_1_with_every_concern_open(self):
        written = self.saved()
        self.assertEqual((1, ["open", "open"], ["", ""]),
                         (written["revision"], [c["status"] for c in written["concerns"]],
                          [c["resolution"] for c in written["concerns"]]))
        self.assertEqual([{"revision": 1, "said": None, "event_id": None, "verdict": "request_changes",
                           "blocking": ["F1"], "advisory": ["F2"], "resolved": [],
                           "questions": [{"id": "Q1", "question": "Which ordering do consumers need?"}]}],
                         written["revisions"])
        text = (self.workspace / design_job.REPORT_PATH).read_bytes()
        self.assertEqual(hashlib.sha256(text).hexdigest(), self.state["design_review"]["report_sha256"])
        self.assertIsNone(design_job.revising(self.state))

    def test_a_reply_asks_for_a_revision_with_the_previous_review_and_the_message(self):
        self.reply("Ordering is per-domain.")
        request = autoreview.prepare(self.state, design_job.STAGE, "/run/state.json", None)
        self.assertEqual(design_job.REVISION_SCHEMA, request.schema)
        self.assertIn("REVISING YOUR REVIEW", request.prompt)
        packet = design_job.packet(self.state)
        self.assertEqual("Ordering is per-domain.", packet["user_message"])
        self.assertEqual((1, ["F1", "F2"], ["Q1"]),
                         (packet["previous_review"]["revision"], [c["id"] for c in packet["previous_review"]["concerns"]],
                          [q["id"] for q in packet["previous_review"]["questions"]]))
        # A reply recognized as another job revises nothing.
        other = copy.deepcopy(self.state)
        other["workflow"]["kind"] = "build"
        self.assertEqual(design_job.SCHEMA, design_job.schema_for(other))

    def test_a_revision_that_breaks_the_rules_is_refused(self):
        self.reply("Ordering is per-domain.")
        before = copy.deepcopy(self.state)
        cases = {
            "keeps every earlier concern.*F2 \\(concern F2\\)":
                self.revise(concerns=[concern("F1"), concern("O1")]),
            "own id; used more than once: \\['F1'\\]":
                self.revise(concerns=[concern("F1"), concern("F1"), concern("F2", "advisory")]),
            "says what settled it in resolution: \\['F2'\\]":
                self.revise(concerns=[concern("F1"), concern("F2", "advisory", status="resolved")]),
            "Only an earlier concern can be resolved.*\\['O1'\\]":
                self.revise(concerns=[concern("F1"), concern("F2", "advisory"),
                                      concern("O1", status="resolved", resolution="fine")]),
            "open blocking concern": self.revise(verdict="approve"),
            "open blocking concern ": self.revise(
                verdict="request_changes", concerns=[concern("F1", status="resolved", resolution="done"),
                                                     concern("F2", "advisory")]),
        }
        for pattern, value in cases.items():
            state = copy.deepcopy(before)
            with self.subTest(pattern), self.assertRaisesRegex(ValueError, pattern.strip()):
                autoreview.apply_job(design_job.STAGE, state, value, {"changed_files": [], "output": "o2"},
                                     self.workspace)
        # A first review resolves nothing.
        with self.assertRaisesRegex(ValueError, "first review of a design resolves nothing.*: F2"):
            design_job.check(self.revise(concerns=[concern("F1"), concern("F2", "advisory", status="resolved",
                                                                          resolution="no")]), [])

    def test_a_reply_about_another_design_gets_a_fresh_review(self):
        self.reply("Now review docs/design/other.md instead.")
        fresh = self.revise(design_under_review="docs/design/other.md",
                            concerns=[concern("C1")], questions=[])
        resolving = copy.deepcopy(fresh)
        resolving["concerns"].append(concern("C2", status="resolved", resolution="n/a"))
        with self.assertRaisesRegex(ValueError, "resolves nothing"):
            autoreview.apply_job(design_job.STAGE, copy.deepcopy(self.state), resolving,
                                 {"changed_files": [], "output": "o2"}, self.workspace)
        autoreview.apply_job(design_job.STAGE, self.state, fresh, {"changed_files": [], "output": "o2"}, self.workspace)
        written = self.saved()
        self.assertEqual((1, ["Now review docs/design/other.md instead."]),
                         (written["revision"], [row["said"] for row in written["revisions"]]))

    def test_two_replies_leave_three_revisions_in_the_report(self):
        self.reply("Ordering is per-domain.")
        first_event = self.state["turns"][-1]["event_id"]
        autoreview.apply_job(design_job.STAGE, self.state, self.revise(), {"changed_files": [], "output": "o2"},
                             self.workspace)
        self.assertEqual(("TASK_COMPLETE", 2), (self.state["status"], self.state["design_review"]["revision"]))
        self.reply("Per-registry is fine.")
        settled = self.revise(questions=[], concerns=[
            concern("F1", area="migration"), concern("F2", "advisory", area="dlq"),
            concern("O1", status="resolved", resolution="per-registry accepted; the registry_id key keeps order")])
        autoreview.apply_job(design_job.STAGE, self.state, settled, {"changed_files": [], "output": "o3"},
                             self.workspace)
        written = self.saved()
        self.assertEqual(3, written["revision"])
        self.assertEqual([(1, None, ["F1"], ["F2"], [], ["Q1"]),
                          (2, "Ordering is per-domain.", ["F1", "O1"], ["F2"], [], ["Q2"]),
                          (3, "Per-registry is fine.", ["F1"], ["F2"], ["O1"], [])],
                         [(row["revision"], row["said"], row["blocking"], row["advisory"], row["resolved"],
                           [q["id"] for q in row["questions"]]) for row in written["revisions"]])
        self.assertEqual(first_event, written["revisions"][1]["event_id"])
        self.assertEqual("resolved", written["concerns"][2]["status"])
        found = self.state["design_review"]
        self.assertEqual((1, 1, 1, 0, 3), (found["blocking"], found["advisory"], found["resolved"],
                                           found["questions"], found["revision"]))
        self.assertEqual(hashlib.sha256((self.workspace / design_job.REPORT_PATH).read_bytes()).hexdigest(),
                         found["report_sha256"])
        self.assertIn("Revision 3: 1 concern(s) resolved", design_job.render(self.state))

    def test_same_design_reads_the_path_first_then_the_words(self):
        for before, after, same in (("docs/design/a.md", "docs/design/a.md, revision 2", True),
                                    ("docs/design/a.md", "docs/design/b.md", False),
                                    ("docs/design/a.md", "a.md (the revised review)", True),
                                    ("./docs/design/a.md", "docs/design/a.md", True),
                                    ("docs/design/a.md", "docs/design/xa.md", False),
                                    ("the design named in the request", "The design  named in the request", True),
                                    ("docs/design/a.md", "the design in the request", False),
                                    ("", "", False)):
            with self.subTest(before=before, after=after):
                self.assertEqual(same, design_job.same_design(before, after))

    def test_a_repair_of_a_revision_gets_the_revision_rules(self):
        self.assertIn("Keep every concern id from previous_review", jobs.repair_rules(design_job.STAGE))


if __name__ == "__main__":
    unittest.main()


class ProbeTests(unittest.TestCase):
    """A concern about today's code carries a probe the runner runs in a scratch copy; no model."""

    def apply(self, concerns):
        import subprocess
        root = Path(tempfile.mkdtemp(prefix="design-probe-"))
        (root / "events").mkdir()
        (root / "events" / "processor.py").write_text("STRICT_SEQ = True\n")
        for args in (["init", "-q"], ["add", "-A"], ["-c", "user.name=t", "-c", "user.email=t@example.test",
                                                      "commit", "-qm", "seed"]):
            subprocess.run(["git", *args], cwd=root, check=True)
        state = state_for(str(root))
        autoreview.apply_job(design_job.STAGE, state, report(concerns=concerns),
                             {"changed_files": [], "output": str(root / "o.json")}, root)
        return state, root

    def concern(self, probe, **overrides):
        return {"id": "F1", "area": "ordering", "severity": "blocking", "summary": "the processor requires strict seq",
                "evidence": "events/processor.py", "example": "Given seq 3 was processed; when seq 5 arrives; "
                "then the processor rejects it", "probe": probe, **overrides}

    def test_a_concern_whose_probe_exits_0_is_recorded_as_shown(self):
        probe = "python3 -c 'from events.processor import STRICT_SEQ; assert STRICT_SEQ'"
        state, root = self.apply([self.concern(probe)])
        self.assertEqual([("F1", 0)], [(row["id"], row["exit_code"]) for row in state["design_review"]["probes"]])
        self.assertEqual("TASK_COMPLETE", state["status"])

    def test_a_concern_whose_probe_fails_rejects_the_review(self):
        probe = "python3 -c 'from events.processor import STRICT_SEQ; assert not STRICT_SEQ'"
        with self.assertRaisesRegex(ValueError, "concerns' probes did not exit 0.*'F1'"):
            self.apply([self.concern(probe)])

    def test_every_blocking_concern_needs_an_example(self):
        with self.assertRaisesRegex(ValueError, "example of the problem"):
            self.apply([self.concern("", example=" ")])

    def test_an_advisory_concern_needs_no_example_and_a_text_only_concern_no_probe(self):
        state, _ = self.apply([self.concern("", example="", severity="advisory"),
                               self.concern("", id="F2", example="Given a switch back; when events were consumed "
                                                                 "from Kafka; then nothing reconciles them")])
        self.assertEqual([], state["design_review"]["probes"])
