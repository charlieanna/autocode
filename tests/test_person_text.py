"""A person-only Resolver question in the status view carries no CLI machinery (#3145).

A live run (20261111-210549-vscode-10d2c3) paused on an AutoResolver request whose question, rendered
verbatim by the VS Code chat, told the person to type `--resolver-request ID --resolver-token -
(reads AUTOCODE_RESOLVER_TOKEN) --resolver-response provide_information --resolver-message TEXT, then
autocode resume`. The view names the reply as an argument array; the question shows the person's
sentences only, while the saved request and the stop reason keep the operator advice.
"""

import copy
import unittest

import autocode_pause_codes as pause_codes
import autocode_person_text as person_text
import autocode_progress_view as progress_view
import autocode_recovery_limits as recovery_limits
import autocode_resolver_human as human
import autocode_run_view as run_view
import autocode_status_summary as status_summary

from . import test_blocker_next_action, test_needs_contract, test_resolver_human
from .operational_fixtures import legacy_publication

PERSON = (
    "AutoResolver could not resolve the Builder's model limit. Provide corrective information or leave the run paused."
)
ATTEMPT = "001/builder-01"


def composed():
    """The request text resolver_runtime composes when no recovery grant is eligible."""
    return PERSON + " " + recovery_limits.advice(allow_grant=False, attempt=ATTEMPT)


class PlainTextTests(unittest.TestCase):
    def test_operator_sentences_are_dropped_and_person_sentences_kept(self):
        shown = person_text.plain(composed())
        self.assertTrue(shown.startswith(PERSON), shown)
        self.assertFalse(person_text.machinery(shown), shown)
        self.assertIn("Resuming without setting the uncertain attempt aside will hold", shown)

    def test_each_kind_of_machinery_is_recognized(self):
        for text in (
            "Run it with --resume-paused.",
            "The token is read from AUTOCODE_RESOLVER_TOKEN.",
            "Then autocode resume.",
            "Use autocode --run-dir RUN.",
        ):
            with self.subTest(text=text):
                self.assertTrue(person_text.machinery(text))
                self.assertEqual(person_text.FALLBACK, person_text.plain(text))
        for text in ("A hand-written -- aside.", "Use openai/gpt-6-sol at medium.", "The run is self-paced."):
            with self.subTest(text=text):
                self.assertFalse(person_text.machinery(text))
                self.assertEqual(text, person_text.plain(text))

    def test_lines_are_kept_and_a_machinery_line_is_dropped(self):
        text = (
            "Approve the correction?\nAC1 current: \"a\"\nRun --edit-goal body.json first.\n\nReply exactly 'accept'."
        )
        self.assertEqual(
            "Approve the correction?\nAC1 current: \"a\"\n\nReply exactly 'accept'.", person_text.plain(text)
        )

    def test_parenthetical_command_hints_are_removed_without_dropping_the_plain_sentence(self):
        reason = (
            "Last cause: No new provider activity within the inactivity limit "
            "(300 seconds, runner default; change it with autocode resume --max-idle-seconds N). "
            "The plan names two exports (CSV and JSON); pick one."
        )
        self.assertEqual(
            "Last cause: No new provider activity within the inactivity limit. "
            "The plan names two exports (CSV and JSON); pick one.",
            person_text.plain(reason),
        )

    def test_empty_text_passes_through(self):
        for text in (None, ""):
            self.assertEqual(text, person_text.plain(text))

    def test_an_option_loses_its_command_clause(self):
        cases = {
            "Abandon the uncertain attempt with --abandon-stage 001/builder-01": "Abandon the uncertain attempt",
            "Acknowledge the pause with autocode resume --no-progress-limit N": "Acknowledge the pause",
            "Allow more plan reviews with --planning-review-call-limit N": "Allow more plan reviews",
            "Leave paused": "Leave paused",
            "openai/gpt-6-sol": "openai/gpt-6-sol",
            "--resume-paused": None,
        }
        for option, shown in cases.items():
            with self.subTest(option=option):
                self.assertEqual(shown, person_text.plain_option(option))

    def test_a_question_row_keeps_its_other_fields(self):
        row = {"id": "R1", "question": composed(), "why": "Kept.", "options": ["--resume-paused", "Leave paused"]}
        shown = person_text.question(row)
        self.assertEqual(("R1", "Kept.", ["Leave paused"]), (shown["id"], shown["why"], shown["options"]))
        self.assertFalse(person_text.machinery(shown["question"]))
        self.assertEqual(composed(), row["question"])


class PersonOnlyQuestionTests(unittest.TestCase):
    setUp = test_resolver_human.ResolverHumanTests.setUp
    contract = test_resolver_human.ResolverHumanTests.contract
    request = test_resolver_human.ResolverHumanTests.request
    publish_operational = test_resolver_human.ResolverHumanTests.publish_operational

    def queue_blocker(self):
        request = test_blocker_next_action.blocker_request()
        request["decision_needed"] = composed()
        request["options"] = [
            "Provide corrective information",
            "Leave paused",
            f"Abandon the uncertain attempt with --abandon-stage {ATTEMPT}",
        ]
        human.queue(
            self.state,
            "blocker",
            {"stage": "resolve"},
            request=request,
            evidence={"diagnosis": "Model limit", "output": "iterations/001/resolver-01.json"},
        )
        self.state["stages"].append(
            {"stage": "resolve", "output": "iterations/001/resolver-01.json", "exit_code": 0, "changed_files": []}
        )

    def view(self):
        return run_view.view(self.state, run_dir=str(self.root / "run"), workspace=str(self.root))

    def assert_plain(self, need):
        question = need["questions"][0]
        for text in (question["question"], question["why"], *question["options"]):
            self.assertFalse(person_text.machinery(text), text)

    def test_a_published_blocker_question_and_its_chat_message_name_no_flags(self):
        self.state["status"] = "PAUSED_RESOLVER"
        self.queue_blocker()
        self.assertEqual("escalate", human.evaluate(self.state))
        view = self.view()
        need = view["needs"]
        self.assertEqual(("answer", "blocker"), (need["kind"], need["resolver_scope"]))
        self.assert_plain(need)
        self.assertTrue(need["questions"][0]["question"].startswith(PERSON))
        self.assertEqual(
            ["Provide corrective information", "Leave paused", "Abandon the uncertain attempt"],
            need["questions"][0]["options"],
        )
        message = next(row for row in view["chat"]["messages"] if row["id"].startswith("resolver-"))
        self.assertTrue(message["text"].startswith(PERSON), message["text"])
        self.assertFalse(person_text.machinery(message["text"]), message["text"])
        # The reply stays the named argument array, and the saved request keeps the operator advice.
        self.assertEqual("resolver_response", view["chat"]["input"]["kind"])
        self.assertIn("--resolver-response", view["chat"]["input"]["next_command"])
        self.assertEqual(composed(), human.current(self.state)["request"]["decision_needed"])

    def test_a_pending_blocker_proposal_question_names_no_flags(self):
        # The issue's run: RESOLVER_PENDING with an unpublished proposal the writer has not settled.
        self.queue_blocker()
        self.state["status"] = "RESOLVER_PENDING"
        need = self.view()["needs"]
        self.assertEqual(("answer", "blocker"), (need["kind"], need["resolver_scope"]))
        self.assert_plain(need)
        self.assertTrue(need["questions"][0]["question"].startswith(PERSON))

    def test_a_retained_machinery_question_is_not_shown_or_answerable(self):
        self.contract()
        self.publish_operational()
        self.state["pending_questions"][0]["question"] = composed()
        before = copy.deepcopy(self.state)
        view = self.view()
        self.assertIsNone(view["needs"])
        self.assertIsNone(view["next_command"])
        self.assertIsNone(view["chat"]["input"])
        self.assertIsNone(view["chat"]["gate"])
        self.assertEqual("blocked", view["recovery"]["category"])
        self.assertEqual(before, self.state)

    def test_a_requirements_question_is_shown_as_asked(self):
        text = "Should the CLI accept --verbose?"
        self.state["status"] = "AWAITING_USER"
        self.state["pending_questions"] = [{"id": "Q1", "question": text, "why": "", "options": []}]
        need = self.view()["needs"]
        self.assertNotIn("resolver_scope", need)
        self.assertEqual(text, need["questions"][0]["question"])

    def test_retained_recovery_exhaustion_never_offers_a_person_a_machinery_grant(self):
        self.contract()
        self.state.update(
            status="PAUSED_TIMEOUT_RECOVERY",
            next_stage="review_plan",
            automatic_recoveries_since_resume=3,
            consecutive_timeout_recoveries=3,
            planning={"plan_review_calls": 3},
        )
        legacy_publication(self.state, self.root / "run", request=self.request())
        retained = copy.deepcopy(self.state)
        for settle in (False, True):
            with self.subTest(settle=settle):
                if settle:
                    self.assertEqual("none", human.evaluate(self.state))
                view = self.view()
                self.assertIsNone(view["needs"])
                self.assertIsNone(view["next_command"])
                self.assertIsNone(view["chat"]["input"])
                self.assertIsNone(view["chat"]["gate"])
                shown = progress_view.progress(self.state, accepted=(), stage=None, needs=view["needs"])["needs_you"]
                self.assertNotIn("authorize additional recoveries", shown or "")
                for key in ("settings", "automatic_recoveries_since_resume", "consecutive_timeout_recoveries"):
                    self.assertEqual(retained.get(key), self.state.get(key), key)
                self.assertEqual(
                    retained["resolver"]["operational_receipts"], self.state["resolver"]["operational_receipts"]
                )


# A stop reason the runner saved for both audiences: the person's sentence, then operator advice.
SAVED_FOR_BOTH = (
    "The Builder stopped. "
    + recovery_limits.GRANT_ADVICE
    + " "
    + recovery_limits.INFORM_ADVICE
    + " Or correct it with --edit-goal FILE."
)


def person_fields(view):
    """The status-view fields a client shows a person as they are, named by their path."""
    progress = view.get("progress") or {}
    yield "progress.needs_you", progress.get("needs_you")
    yield "progress.line", progress.get("line")
    yield "summary.sentence", (view.get("summary") or {}).get("sentence")
    yield "explanation.what_it_means", (view.get("explanation") or {}).get("what_it_means")
    recovery = view.get("recovery") or {}
    for key in ("title", "what_happened", "retained"):
        yield f"recovery.{key}", recovery.get(key)
    for action in recovery.get("actions") or []:
        yield "recovery.actions[].label", action.get("label")
        yield "recovery.actions[].effect", action.get("effect")


def rendered(state):
    """The public view with the progress and summary the status command adds to it."""
    view = run_view.view(copy.deepcopy(state))
    view["progress"] = progress_view.progress(state, accepted=(), stage=None, needs=view["needs"])
    view["summary"] = status_summary.project(view)
    return view


class PersonFieldsTests(unittest.TestCase):
    """No view field written for a person names a flag, a token variable or a command, at any pause.

    The progress view wrote the action into `progress.needs_you` ("give plan feedback with --feedback
    TEXT or ... --planning-review-call-limit N"), which VS Code showed unfiltered in its wait
    notification, Details and status bar; each client stripped other fields for itself."""

    def states(self):
        rows = [{"status": code, "stop_reason": SAVED_FOR_BOTH} for code in pause_codes.CODES]
        rows.extend(test_needs_contract.KIND_STATES.values())
        for extra in test_needs_contract.KIND_SHAPES.values():
            rows.extend(extra)
        return rows

    def test_every_pause_code_and_needs_shape_shows_a_person_words_only(self):
        states = self.states()
        self.assertGreater(len(states), len(pause_codes.CODES))
        for state in states:
            view = rendered(state)
            for field, value in person_fields(view):
                with self.subTest(status=state["status"], field=field):
                    if isinstance(value, str):
                        self.assertFalse(person_text.machinery(value), value)
                        self.assertFalse(person_text.internal_machinery(value), value)
            with self.subTest(status=state["status"], field="progress.needs_you"):
                self.assertTrue(view["progress"]["needs_you"])

    def test_the_words_say_what_the_named_action_does(self):
        cases = {
            "PAUSED_PLANNING_BUDGET": "give plan feedback or raise the planning review limit",
            "PAUSED_PERMISSION_LOOP": "inspect the pause, then say what changed",
            "PAUSED_ACCEPTANCE_TESTS": "inspect the pause, then resume",
        }
        for status, asked in cases.items():
            with self.subTest(status=status):
                self.assertEqual(asked, rendered({"status": status, "stop_reason": "why"})["progress"]["needs_you"])
        edit = {
            "kind": "resume",
            "edit_required": True,
            "action": "--edit-goal FILE",
            "feedback_action": "--feedback TEXT",
        }
        asked = progress_view.progress({"status": "PAUSED_COMPONENT_PLAN"}, accepted=(), stage=None, needs=edit)
        self.assertEqual("correct the plan or give feedback on it; fresh approval needed", asked["needs_you"])
        edit.pop("feedback_action")
        asked = progress_view.progress({"status": "PAUSED_COMPONENT_PLAN"}, accepted=(), stage=None, needs=edit)
        self.assertEqual("correct the plan; fresh approval needed", asked["needs_you"])
        held = {"kind": "planning_budget", "action": "--resume-paused --resolver-request R"}
        asked = progress_view.progress({"status": "PAUSED_PLANNING_BUDGET"}, accepted=(), stage=None, needs=held)
        self.assertEqual("continue planning", asked["needs_you"])

    def test_a_stale_runs_next_command_is_never_the_persons_words(self):
        command = "--workspace /w --run-dir /r --reconcile-only"
        stale = progress_view.progress({"status": "RUNNING"}, accepted=(), stage="Builder", needs=None, stopped=command)
        self.assertEqual("inspect the stopped stage, then resume", stale["needs_you"])
        named = "AutoResolver must reconcile retained attempt 002/builder-01"
        stale = progress_view.progress({"status": "RUNNING"}, accepted=(), stage="Builder", needs=None, stopped=named)
        self.assertEqual(named, stale["needs_you"])

    def test_for_person_keeps_plain_text_and_drops_what_is_typed(self):
        self.assertEqual("", person_text.for_person(None))
        self.assertEqual("The Builder stopped.", person_text.for_person("The Builder stopped."))
        self.assertEqual("The Builder stopped.", person_text.for_person(SAVED_FOR_BOTH))
        self.assertEqual("", person_text.for_person(recovery_limits.GRANT_ADVICE))
        self.assertEqual(person_text.SCHEMA_SUMMARY, person_text.for_person("acceptance_test_repairs failed"))


if __name__ == "__main__":
    unittest.main()
