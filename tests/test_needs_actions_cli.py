"""Execute a needs action against a genuine fake-provider repair pause (#1010)."""

import json
import unittest

import autocode_taskrun as taskrun

from . import test_subprocess


class NeedsActionsCliTests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp

    def progress(self, run, view):
        need = view["needs"] or {}
        if need.get("kind") == "answer":
            for question in need["questions"]:
                answer = question.get("proposed_default") or question["options"][0]
                run.answer(question["id"], answer, resolver_token=need.get("resolver_token"))
            return run.advance()
        if need.get("kind") == "approve_plan":
            run.approve_plan(need["token"])
            return run.advance()
        if need.get("kind") == "resume":
            self.assertEqual("PAUSED_REQUESTED", view["status"], self.details(view))
            return run.resume_paused()
        self.assertEqual("continue", need.get("kind"), self.details(view))
        return run.advance()

    @staticmethod
    def details(view):
        return {key: view.get(key) for key in ("status", "next_stage", "needs", "stop_reason")}

    def launches(self):
        return [json.loads(line) for line in self.probe.read_text().splitlines()]

    def test_advertised_source_acceptance_executes_and_preserves_approval(self):
        self.probe = self.root / "provider-launches.jsonl"
        self.env.update(
            AUTOCODE_FIXTURE_MODE="rework",
            AUTOCODE_FIXTURE_INCOMPLETE_REWORK="1",
            AUTOCODE_REGISTRY_LAUNCH_PROBE=str(self.probe),
        )
        run = taskrun.TaskRun.start(
            self.project,
            "Build greeting",
            options=("--engine", "codex", "--no-adaptive-planning", "--pause-after-stage"),
            env=self.env,
            timeout=120,
            cwd=self.root,
        )
        view = run.status()
        for _ in range(16):
            if view["status"] == "PAUSED_REQUESTED" and view["next_stage"] == "astra_resolve":
                break
            view = self.progress(run, view)
        else:
            self.fail("The genuine rejected-review handoff did not reach the Resolver boundary")
        self.assertFalse(view["done"])
        approved = view["approved_contract"]
        source = self.project / "greet.py"
        edited = source.read_bytes() + b"\n# Operator edit while repair is paused\n"
        source.write_bytes(edited)

        stale = run.resume_paused()
        self.assertEqual("PAUSED_STALE_HANDOFF", stale["status"], self.details(stale))
        self.assertEqual("resume", stale["needs"]["kind"])
        self.assertEqual("--resume-paused --accept-source-edit", stale["needs"]["action"])
        before = self.launches()
        attempts = stale["usage"]["accounting"]
        held = run.resume_paused()
        self.assertEqual(stale["needs"], held["needs"])
        self.assertEqual(before, self.launches(), "A plain resume must not dispatch the stale repair")
        self.assertEqual(attempts, held["usage"]["accounting"])
        self.assertEqual(approved, held["approved_contract"])
        self.assertEqual(edited, source.read_bytes())

        accepted = run.accept_source_edit()
        self.assertEqual("PAUSED_REQUESTED", accepted["status"], self.details(accepted))
        self.assertEqual("terra", accepted["next_stage"])
        self.assertEqual(["astra_resolve"], [row["stage"] for row in self.launches()[len(before) :]])
        self.assertEqual(approved, accepted["approved_contract"])
        self.assertEqual(edited, source.read_bytes(), "Accepting source may launch only the read-only Resolver")
        self.assertNotIn("accept-source-edit", accepted["needs"].get("action", ""))

        completed = accepted
        for _ in range(8):
            if completed["done"]:
                break
            completed = self.progress(run, completed)
        self.assertTrue(completed["done"], self.details(completed))
        self.assertEqual("TASK_COMPLETE", completed["status"])
        self.assertEqual(approved, completed["approved_contract"])
        self.assertEqual("PASS", completed["evidence"]["check_replay"]["verdict"])
        self.assertEqual(1, completed["efficiency"]["delivery"]["verified_deliveries"])


if __name__ == "__main__":
    unittest.main()
