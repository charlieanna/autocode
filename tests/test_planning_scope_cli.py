"""Late Planner scope is observable through the public CLI before review or building."""

import os
import sys
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

from .test_agent_step import fixture_workspace
from .test_taskrun import BRIEF, FIXTURE_OPTIONS


@unittest.skipUnless(sys.platform == "linux", "fake providers require supported Linux")
class PlanningScopeCliTests(unittest.TestCase):
    def start(self, *, grounded, invalid_citation=False):
        workspace, env = fixture_workspace(self)
        provider = Path(env["PATH"].split(os.pathsep)[0]) / "codex"
        env["PATH"] = f"{provider.parent}{os.pathsep}{Path(sys.executable).parent}{os.pathsep}{env['PATH']}"
        elsewhere = "Also record greetings in greet-history."
        quote = elsewhere if grounded else "Charge credit cards in billing."
        marker = '    output = Path(sys.argv[sys.argv.index("-o") + 1])'
        addition = textwrap.dedent(f"""
            if stage == 'draft_plan':
                report['workspace_scope'] = {{'spans_repositories': True, 'external_parts': [{{
                    'target': 'greet-history', 'work': 'Record greetings',
                    'source_quote': {quote!r}, 'evidence': ''}}]}}
                if {invalid_citation!r} and not repairing:
                    report['contract']['acceptance_criteria'][0]['sources'] = [
                        {{'source_id': 'task:0', 'quote': 'Invented requirement absent from the request.'}}]
        """)
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        provider.write_text(source.replace(marker, textwrap.indent(addition, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(
            workspace,
            f"{BRIEF} {elsewhere}",
            options=(*FIXTURE_OPTIONS, "--joint-planning", "--no-adaptive-planning"),
            env=env,
            timeout=180,
        )
        return run, env, run.advance_until_input()

    def test_malformed_scope_report_is_repaired_before_offering_assignment(self):
        run, env, view = self.start(grounded=True, invalid_citation=True)
        self.assertEqual("answer", view["needs"]["kind"], view["needs"])
        self.assertTrue(view["needs"]["questions"][0]["id"].startswith("workspace-scope-"))
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertEqual(
            ["requirements_gather", "draft_plan", "draft_plan_report_repair"],
            [row["stage"] for row in attempts],
        )
        self.assertTrue(attempts[-1]["report_only"])
        reopened = taskrun.TaskRun(run.workspace, run.run_dir, env=env).status()
        self.assertEqual(view["usage"], reopened["usage"])
        self.assertEqual(view["cost"], reopened["cost"])
        self.assertFalse((run.workspace / "greet.py").exists())

    def test_planner_discovery_reopens_at_scope_before_review_and_preserves_approval(self):
        run, env, view = self.start(grounded=True)
        self.assertEqual("answer", view["needs"]["kind"], view["needs"])
        [question] = view["needs"]["questions"]
        self.assertTrue(question["id"].startswith("workspace-scope-"))
        self.assertEqual(["greet-history"], [part["target"] for part in view["scope"]["external_parts"]])
        attempts = view["usage"]["accounting"]["attempts"]
        self.assertEqual(["requirements_gather", "draft_plan"], [row["stage"] for row in attempts])
        self.assertFalse(any(row["report_only"] for row in attempts))
        reopened = taskrun.TaskRun(run.workspace, run.run_dir, env=env).status()
        self.assertEqual(view["needs"], reopened["needs"])
        self.assertEqual(attempts, reopened["usage"]["accounting"]["attempts"])
        self.assertFalse((run.workspace / "greet.py").exists())

        run.answer(question["id"], "This repository only", resolver_token=view["needs"]["resolver_token"])
        planned = run.advance_until_input()
        self.assertEqual("approve_plan", planned["needs"]["kind"], planned["needs"])
        self.assertEqual("this_repository", planned["scope"]["decision"])
        self.assertIn(
            "Not covered: changes in greet-history: Record greetings", planned["displayed_plan"]["scope_exclusions"]
        )
        self.assertNotIn("builder", [row["stage"] for row in planned["usage"]["accounting"]["attempts"]])

    def test_ungrounded_planner_discovery_does_not_expand_scope(self):
        run, _, view = self.start(grounded=False)
        self.assertEqual("approve_plan", view["needs"]["kind"], view["needs"])
        self.assertEqual([], view["scope"]["external_parts"])
        self.assertEqual(["greet-history"], view["scope"]["dropped"])
        self.assertFalse(any(row["report_only"] for row in view["usage"]["accounting"]["attempts"]))
        self.assertFalse((run.workspace / "greet.py").exists())


@unittest.skipUnless(sys.platform == "linux", "fake providers require supported Linux")
class AssignedPlannerReportCliTests(unittest.TestCase):
    def test_assignment_reuses_completed_planner_report_instead_of_regenerating_scope(self):
        self.assignment_reuses_report(pause_on_scope=False)

    def test_paused_assignment_reuses_completed_planner_report_instead_of_regenerating_scope(self):
        self.assignment_reuses_report(pause_on_scope=True)

    def assignment_reuses_report(self, *, pause_on_scope):
        from unittest.mock import patch

        import autocode_chat_client as chat
        import autocode_chat_coordination as coordination

        from . import test_coordination_tui_e2e as fixture

        fixture.CoordinationTuiEndToEnd.setUp(self)
        source = (fixture.ROOT / "tests/coordination_provider.py").read_text()
        source = source.replace("ROOT = Path(__file__).resolve().parents[1]", f"ROOT = Path({str(fixture.ROOT)!r})")
        marker = "    # Keep the fake output strictly at the active public schema"
        self.assertEqual(1, source.count(marker))
        at = source.index(marker)
        source = (
            source[:at]
            + textwrap.dedent("""
            if stage == 'draft_plan' and name == 'checkout':
                counter = root / '.autocode/scope-draft-count'
                number = int(counter.read_text()) + 1 if counter.exists() else 1
                counter.write_text(str(number))
                report['workspace_scope'] = {'spans_repositories': True, 'external_parts': [{
                    'target': 'history', 'work': f'Preserve the downstream total; wording {number}',
                    'source_quote': '', 'evidence': 'client.py:3'}]}
        """)
            .replace("\n", "\n    ")
            .lstrip("\n")
            + "\n"
            + source[at:]
        )
        if pause_on_scope:
            pause = textwrap.indent(
                textwrap.dedent(f"""
            if stage == 'draft_plan' and name == 'checkout':
                subprocess.run({list(taskrun.AUTOCODE)!r} + [
                    'intervention', 'submit', '--workspace', str(root),
                    '--run-dir', str(output.parents[2]), '--request-id', 'pause-completed-scope',
                    '--kind', 'pause', '--json'], check=True, capture_output=True, text=True)
            """),
                "    ",
            )
            source = source.replace(marker, pause + "\n" + marker)
        scope_stage = "revise_plan" if pause_on_scope else "draft_plan"
        if pause_on_scope:
            source = source.replace(
                "stage == 'draft_plan' and name == 'checkout'", "stage == 'revise_plan' and name == 'checkout'"
            )
        provider = self.root / "bin/late-scope-provider.py"
        provider.write_text(source)
        launcher = self.root / "bin/codex"
        launcher.write_text(f'#!/bin/sh\nexec {sys.executable} {provider} "$@"\n')
        with patch.dict(os.environ, self.env, clear=True):
            client = chat.start(
                self.intake,
                "Fix checkout showing the wrong final total.",
                ["--workflow", "bugfix"],
                [*FIXTURE_OPTIONS, "--no-adaptive-planning", "--spec-first", "--joint-planning"],
                env=self.env,
                ask=lambda prompt: fixture.CoordinationTuiEndToEnd.answer(self, prompt),
                say=self.lines.append,
            )
            self.addCleanup(client.close)
            client.history = chat.History(self.intake, env=self.env)
            pause_restored = False
            for _ in range(45):
                current = client.status()
                if current.get("coordination"):
                    summary = coordination.read(client, current)
                    child = next((m for m in summary["members"] if m["target"] == "checkout"), None)
                    if child and child["status"] != "STARTING":
                        run = taskrun.TaskRun(Path(child["workspace"]), Path(child["run_dir"]), env=self.env)
                        view = run.status()
                        if pause_on_scope and view["status"] == "PAUSED_INTERVENTION":
                            before = view["usage"]
                            view = run.resume_paused()
                            self.assertEqual("answer", view["needs"]["kind"], view["needs"])
                            self.assertEqual(before, view["usage"])
                            self.assertTrue(view["needs"]["questions"][0]["id"].startswith("workspace-scope-"))
                            pause_restored = True
                        calls = [a for a in view["usage"]["accounting"]["attempts"] if a["stage"] == scope_stage]
                        if calls and view["scope"]["decision"] == "linked_repositories":
                            break
                client.step()
            else:
                self.fail("The coordinator did not assign the late Planner scope")
            self.assertEqual(pause_on_scope, pause_restored)
            self.assertEqual("finalize_plan" if pause_on_scope else "review_plan", view["next_stage"], view["needs"])
            self.assertEqual([], self.questions, "Only source Requirements confirmation precedes scope assignment")
            self.assertEqual(1, len(calls))
            self.assertEqual("1", (run.workspace / ".autocode/scope-draft-count").read_text())
            reopened = run.status()
            self.assertEqual(view["usage"], reopened["usage"])
            self.assertEqual(view["scope"], reopened["scope"])
            self.assertNotIn("approved_contract", view)
            self.assertFalse(any(a["stage"] == "builder" for a in view["usage"]["accounting"]["attempts"]))
            self.assertEqual([], self.github.posts)
            self.assertEqual("feedback", view["chat"]["input"]["kind"])
            message = "Preserve the existing checkout reply while correcting its displayed total."
            corrected = run.feedback(message)
            self.assertIn(message, [row["text"] for row in corrected["request_sources"]])
            self.assertEqual(view["usage"], corrected["usage"])
            self.assertEqual(view["cost"], corrected["cost"])
            self.assertIsNone(corrected.get("approved_contract"))
            self.assertEqual([], self.github.posts)
