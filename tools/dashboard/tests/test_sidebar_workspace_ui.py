"""M1 acceptance cases: the project-grouped sidebar workspace (AC1-AC9).

Frontend behavior is driven through the Node VM harness
(sidebar_workspace_harness.js, same lightweight pattern as
test_archive_suggestions_ui.js); creation-time project scoping is additionally
exercised against the real conversation stores through the Console glue, and
the provider-scope boundary runs against ContinuousConversationStore with
fake providers, matching the style of test_continuous_recovery.py.
"""

import json
import os
import re
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

TOOLS = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(TOOLS / "dashboard"))

from agent_console import Console  # noqa: E402
from dashboard_continuous import ContinuousConversationStore  # noqa: E402
from dashboard_setup import conversation_model_fields  # noqa: E402

HARNESS = Path(__file__).resolve().parent / "sidebar_workspace_harness.js"
DASHBOARD_CSS = TOOLS / "dashboard" / "dashboard.css"


def harness_case(name):
    env = {**os.environ, "AUTOCODE_TEST_PYTHON": sys.executable}
    return subprocess.run(["node", str(HARNESS), name], capture_output=True, text=True, timeout=180, env=env)


def css_variable_values(css, token):
    """Resolve a custom property for the light and dark themes.

    Several :root blocks layer the legacy palette and the M1 shell tokens; the
    value that reaches the element is the block that defines the token.
    """
    values = {}
    for theme, pattern in (("light", r":root\{[^}]*\}"), ("dark", r":root\[data-theme=dark\]\{[^}]*\}")):
        for block in re.findall(pattern, css):
            match = re.search(re.escape(token) + r"\s*:\s*(#[0-9a-fA-F]{6}\b)", block)
            if match:
                values[theme] = match.group(1)
                break
    if set(values) != {"light", "dark"}:
        raise AssertionError(f"{token} is not defined for both themes")
    return values


def rgb_channels(hex_color):
    value = hex_color.lstrip("#")
    return int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)


class ConversationScopingFixture(unittest.TestCase):
    """A disposable Console with a fake planner provider and one Git project."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "AutoCode"
        (self.project / ".git").mkdir(parents=True)
        self.runner = self.root / "runner.py"
        self.runner.write_text("raise SystemExit(0)\n")
        self.console = self.make_console()

    def provider(self, messages, model, workdir):
        return "Planner: what should the first milestone deliver?"

    def make_console(self):
        console = Console(
            [self.project],
            self.runner,
            lambda: None,
            conversation_root=self.root / "dashboard/conversations",
            conversation_provider=self.provider,
        )
        # Fake only the external catalogue and transport; real admission still
        # checks every required route before dispatching the fake provider.
        models = sorted({route["model"] for route in conversation_model_fields()["conversation_routes"].values()})
        console.catalogue.fetch = lambda **kwargs: {"usable": True, "models": models}
        console._probe_conversation_transport = lambda *_: {"status": "ok", "data": {"version": "1.18.33"}}

        def close():
            console.pool.shutdown(wait=True)
            if console._conversation_store is not None:
                console._conversation_store.close(wait=True)

        self.addCleanup(close)
        return console

    def wait_ready(self, store, doc):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = store.get(doc["id"])
            if current["status"] != "thinking":
                return current
            time.sleep(0.01)
        self.fail("The fake provider did not finish")


class SidebarWorkspaceUITests(ConversationScopingFixture):
    def assert_harness(self, name):
        result = harness_case(name)
        self.assertEqual(0, result.returncode, f"case {name} failed:\n{result.stdout}\n{result.stderr}")

    def test_ac1_sidebar_groups_conversations_by_project_with_no_project_group(self):
        self.assert_harness("ac1")

    def test_ac2_sidebar_search_filters_conversation_rows(self):
        self.assert_harness("ac2")

    def test_ac3_per_project_new_conversation_scoped_to_project(self):
        self.assert_harness("ac3")
        # The server side of the same flow: an empty scoped conversation is
        # saved before any message exists, and stays attached to the project.
        doc = self.console.conversation_create(
            {"empty": True, "request_id": "scope-create-empty", "workspace": str(self.project)}
        )
        self.assertEqual(str(self.project), doc.get("project_workspace"))
        self.assertEqual([], doc.get("messages"))
        self.assertIsNone(doc.get("attachment"))
        again = self.console.conversation_create(
            {"empty": True, "request_id": "scope-create-empty", "workspace": str(self.project)}
        )
        self.assertEqual(doc["id"], again["id"], "replaying the same request returns the saved record")
        reopened = self.console.conversation_get(doc["id"])
        self.assertEqual(str(self.project), reopened.get("project_workspace"))
        self.assertEqual([], reopened.get("messages"))
        self.assertIsNone(reopened.get("attachment"))
        # The text path keeps its delivered behavior.
        text_doc = self.wait_ready(
            self.console.conversations,
            self.console.conversation_create(
                {"text": "Build the grouped sidebar", "request_id": "scope-create-1", "workspace": str(self.project)}
            ),
        )
        self.assertEqual(str(self.project), text_doc.get("project_workspace"))
        self.assertIsNone(text_doc.get("attachment"))
        self.assertTrue(text_doc.get("messages"), "the text path records its first message")
        with self.assertRaises(ValueError):
            self.console.conversation_create(
                {"text": "Not a project", "request_id": "scope-create-2", "workspace": str(self.root / "missing")}
            )
        with self.assertRaises(ValueError):
            self.console.conversation_create(
                {"empty": True, "request_id": "scope-create-3", "workspace": str(self.root / "missing")}
            )

    def test_ac4_top_new_conversation_shows_active_project_scope(self):
        self.assert_harness("ac4")

    def test_ac5_unscoped_conversation_openable_and_not_attached(self):
        self.assert_harness("ac5")
        doc = self.wait_ready(
            self.console.conversations,
            self.console.conversation_create({"text": "Free-form idea", "request_id": "free-create-1"}),
        )
        self.assertIsNone(doc.get("project_workspace"))
        reopened = self.console.conversation_get(doc["id"])
        self.assertIsNone(reopened.get("attachment"))
        self.assertIsNone(reopened.get("project_workspace"))
        self.assertTrue(reopened.get("messages"), "the saved history stays intact")

    def test_ac6_attention_marker_only_for_pending_human_input(self):
        self.assert_harness("ac6")

    def test_ac6_intake_marker_projects_verified_unanswered_question(self):
        # The doc side of the pending-reply state: an intake conversation with
        # a verified, unresolved human question shows the amber sidebar marker
        # (delivery errors, forged projections and answered questions do not).
        self.assert_harness("ac6_intake")

    def test_ac7_marker_accessible_and_survives_opening(self):
        self.assert_harness("ac7")

    def test_ac8_marker_clears_after_chat_resolution(self):
        self.assert_harness("ac8")

    def test_ac9_complete_tick_and_no_global_needs_you_nav(self):
        self.assert_harness("ac9")
        # The tick's glyph color comes from the .complete-tick rule; resolve the
        # computed value of its color token for both themes and require green.
        css = DASHBOARD_CSS.read_text()
        rule = re.search(r"\.complete-tick\{[^}]*\}", css)
        self.assertTrue(rule, "the completed tick has a style rule")
        color = re.search(r"(?<!-)color\s*:\s*var\(\s*(--[\w-]+)\s*\)", rule.group(0))
        self.assertTrue(color, "the tick color is driven by a design token")
        for theme, value in css_variable_values(css, color.group(1)).items():
            red, green, blue = rgb_channels(value)
            self.assertTrue(
                green > red and green > blue, f"the {theme} tick color {value} is green, not {red}/{green}/{blue}"
            )


class ProjectScopeProviderContextTests(unittest.TestCase):
    """A saved project scope must reach both planning provider contexts."""

    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.project = self.root / "AutoCode"
        (self.project / ".git").mkdir(parents=True)
        (self.project / "AGENTS.md").write_text("Project-only instruction: keep tests honest.\n")
        self.calls = []

        def gatherer(messages, model, workdir):
            self.calls.append(("gatherer", Path(workdir), [row.get("text", "") for row in messages]))
            return "Requirements gathered for the scoped project."

        def planner(messages, route, workdir):
            self.calls.append(("planner", Path(workdir), [row.get("text", "") for row in messages]))
            user = next(row for row in reversed(messages) if row["role"] == "user")
            return json.dumps(
                {
                    "contract_version": 1,
                    "kind": "autocode.planner-structured-draft",
                    "goal": "Plan inside the selected repository",
                    "requirements": ["Scope planning to the project"],
                    "milestones": ["Implement and verify"],
                    "parallelism": [],
                    "unresolved_questions": [],
                    "source_revision": {
                        "requirements_revision": sum(row["role"] == "user" for row in messages),
                        "logical_turn_id": user["logical_turn_id"],
                    },
                    "attribution": {
                        "role": "planner",
                        "model": route["model"],
                        "reasoning_effort": route["reasoning_effort"],
                    },
                    "freshness": {"state": "fresh", "updated_at": "2026-09-30T12:00:00+00:00"},
                }
            )

        self.store = ContinuousConversationStore(root=self.root / "conversations", provider=gatherer, planner=planner)
        self.addCleanup(self.store.close)

    def wait_ready(self, doc):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            current = self.store.get(doc["id"])
            if current["status"] != "thinking":
                return current
            time.sleep(0.01)
        self.fail("The fake providers did not finish")

    def test_ac3_project_scope_reaches_gatherer_and_planner_providers(self):
        doc = self.wait_ready(
            self.store.create("Plan the scoped sidebar", request_id="scope-1", workspace=str(self.project))
        )
        self.assertEqual("ready", doc["status"])
        self.assertEqual(str(self.project), doc.get("project_workspace"))
        kinds = {kind for kind, _, _ in self.calls}
        self.assertEqual(
            {"gatherer", "planner"}, kinds, "both the requirements gatherer and the structured planner dispatched"
        )
        for kind, workdir, texts in self.calls:
            self.assertEqual(self.project, workdir.resolve(), f"the {kind} ran inside the selected repository")
            scope = [text for text in texts if "Project-only instruction" in text]
            self.assertEqual(1, len(scope), f"the {kind} received the repository instructions exactly once")
            self.assertIn(str(self.project), scope[0], "the instruction names the repository path")

    def test_unscoped_conversations_keep_scratch_provider_context(self):
        doc = self.wait_ready(self.store.create("Free-form idea", request_id="free-1"))
        self.assertEqual("ready", doc["status"])
        self.assertIsNone(doc.get("project_workspace"))
        scratch = self.root / "conversations" / "scratch" / doc["id"]
        for kind, workdir, texts in self.calls:
            self.assertEqual(scratch, workdir, f"the {kind} ran in the conversation scratch directory")
            self.assertTrue(
                all("Project-only instruction" not in text for text in texts),
                f"the {kind} received no project instructions",
            )

    def test_ac3_empty_scoped_conversation_first_message_uses_project_scope(self):
        opened = self.store.create_empty(str(self.project), request_id="empty-1")
        self.assertEqual("ready", opened["status"])
        self.assertEqual([], opened["messages"])
        self.assertEqual("New conversation", opened["title"])
        self.assertEqual(str(self.project), opened.get("project_workspace"))
        self.calls.clear()
        doc = self.wait_ready(self.store.send(opened["id"], "Plan the scoped sidebar", request_id="first-send"))
        self.assertEqual("ready", doc["status"])
        self.assertEqual("Plan the scoped sidebar", doc["title"], "the first message titles the pre-send conversation")
        self.assertEqual(str(self.project), doc.get("project_workspace"))
        kinds = {kind for kind, _, _ in self.calls}
        self.assertEqual({"gatherer", "planner"}, kinds, "the first message dispatched both providers")
        for kind, workdir, texts in self.calls:
            self.assertEqual(self.project, workdir.resolve(), f"the {kind} ran inside the selected repository")
            self.assertTrue(
                any("Project-only instruction" in text for text in texts),
                f"the {kind} received the repository instructions",
            )

    def test_replaced_project_never_dispatches_either_provider_and_keeps_saved_turn(self):
        opened = self.store.create_empty(str(self.project), request_id="before-replacement")
        original = self.root / "preserved-project"
        self.project.rename(original)
        self.project.mkdir()
        (self.project / "keep.txt").write_text("replacement work")
        self.store.send(opened["id"], "Preserve this exact turn", request_id="after-replacement")
        self.store.close()
        current = self.store.get(opened["id"])
        self.assertEqual([], self.calls, "neither provider may enter a replacement folder")
        self.assertEqual("error", current["status"])
        self.assertIn("replaced", current["error"])
        self.assertEqual(
            ["Preserve this exact turn"], [row["text"] for row in current["messages"] if row["role"] == "user"]
        )
        self.assertEqual("replacement work", (self.project / "keep.txt").read_text())
        self.assertTrue((original / "AGENTS.md").exists())

    def test_legacy_failed_handoff_requires_confirmation_and_rejects_non_git_folder(self):
        opened = self.store.create_empty(str(self.project), request_id="legacy-handoff")
        path = self.store.root / (opened["id"] + ".json")
        old = json.loads(path.read_text())
        old.pop("_project_identity")
        old["attachment"] = {"status": "failed", "workspace": str(self.project), "launch_rejected": True}
        path.write_text(json.dumps(old))  # controlled pre-upgrade fixture
        current = self.store.get(opened["id"])
        self.assertIn("project_scope_confirmation", current)
        token = current["project_scope_confirmation"]["token"]
        (self.project / ".git").rename(self.project / "saved-git")
        invalid = self.store.get(opened["id"])
        self.assertTrue(invalid["project_scope_error"])
        self.assertNotIn("project_scope_confirmation", invalid)
        with self.assertRaises(ValueError):
            self.store.confirm_project_scope(opened["id"], token)
        (self.project / "saved-git").rename(self.project / ".git")
        confirmed = self.store.confirm_project_scope(opened["id"], token)
        self.assertFalse(confirmed.get("project_scope_error"))
        self.assertEqual(old["attachment"], confirmed["attachment"])
        self.assertEqual([], self.calls)


if __name__ == "__main__":
    unittest.main()
