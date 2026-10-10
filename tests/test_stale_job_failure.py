"""A later non-job abandon is not blocked by an older workflow-job failure (#567)."""

import json
import tempfile
import unittest
from pathlib import Path

import autocode_job_failure as job_failure
import autocode_quota_route as quota_route
import autocode_run_view as run_view
import autocode_stage_recovery as stage_recovery
import autocode_util as util

from tests.test_verify import Project

FAILURE = {
    "stage": "investigate_stuck",
    "attempt_id": "001/stuck-investigation-03",
    "job_retry_token": "jr:investigator",
    "reason": "Investigator interrupted",
    "kind": "exit",
    "source_identity": "saved",
    "source_paths": [],
    "configuration": {},
    "archive": "/run/old",
    "write_diagnosis": {},
    "unrestored": [],
}


def observed():
    """The saved pause from the report: Requirements abandoned, the Investigator failure still set."""
    return {
        "status": "PAUSED_STAGE_ABANDONED",
        "next_stage": "astra_discovery",
        "stop_reason": "Partial work retained. Resume explicitly for Requirements to inspect it and choose the next step.",
        "job_failure": dict(FAILURE),
        "job_retry_authorization": {"token": "jr:investigator"},
        "recovery_context": {"attempt_id": "001/discovery-05", "role": "astra"},
    }


class StaleJobFailureTests(unittest.TestCase):
    def test_a_later_abandon_is_not_the_jobs_pause(self):
        self.assertFalse(quota_route.job_pause_current(observed()))
        current = observed()
        current["recovery_context"] = {
            "kind": "job_failure",
            "stage": "investigate_stuck",
            "attempt_id": "001/stuck-investigation-03",
        }
        current["status"] = "PAUSED_JOB_FAILURE"
        self.assertTrue(quota_route.job_pause_current(current))
        self.assertTrue(
            quota_route.job_pause_current({"status": "PAUSED_STAGE_ABANDONED", "job_failure": dict(FAILURE)})
        )

    def test_plain_resume_continues_the_later_stage_and_drops_the_old_token(self):
        state = observed()
        self.assertIsNone(job_failure.resume_gate(state, resume=True, retry=False, token=None))
        self.assertNotIn("job_failure", state)
        self.assertNotIn("job_retry_authorization", state)

    def test_the_old_retry_token_does_not_relaunch_the_earlier_job(self):
        state = observed()
        blocked = job_failure.resume_gate(state, resume=True, retry=True, token="jr:investigator")
        self.assertIn("earlier attempt", blocked)
        self.assertEqual("jr:investigator", state["job_failure"]["job_retry_token"])
        self.assertEqual("astra_discovery", state["next_stage"])

    def test_a_current_job_pause_still_requires_its_explicit_retry(self):
        state = {
            "status": "PAUSED_JOB_FAILURE",
            "stop_reason": "Investigator interrupted",
            "job_failure": dict(FAILURE),
            "recovery_context": {
                "kind": "job_failure",
                "stage": "investigate_stuck",
                "attempt_id": "001/stuck-investigation-03",
            },
        }
        self.assertEqual(
            "Investigator interrupted", job_failure.resume_gate(state, resume=True, retry=False, token=None)
        )
        self.assertEqual("authorize", job_failure.resume_gate(state, resume=True, retry=True, token="jr:investigator"))
        self.assertIn("job_failure", state)

    def test_status_offers_resume_for_the_later_pause_and_retry_for_the_job(self):
        need = run_view.needs(observed())
        self.assertEqual("resume", need["kind"])
        self.assertNotIn("jr:investigator", json.dumps(need))
        current = observed()
        current["status"] = "PAUSED_JOB_FAILURE"
        current["recovery_context"] = {
            "kind": "job_failure",
            "stage": "investigate_stuck",
            "attempt_id": "001/stuck-investigation-03",
        }
        self.assertEqual("retry_job", run_view.needs(current)["kind"])

    def test_abandoning_a_later_stage_clears_the_earlier_job_failure(self):
        project = Project({"README.md": "Notes\n"})
        self.addCleanup(project.close)
        run = Path(tempfile.mkdtemp(prefix="stale-job-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(run, True))
        iteration = run / "iterations" / "001"
        iteration.mkdir(parents=True)
        output = iteration / "discovery-05.json"
        output.write_text("{}\n")
        (iteration / "discovery-05.jsonl").write_text('{"type":"turn.completed"}\n')
        before = iteration / "discovery-05.before.json"
        before.write_text(json.dumps(util.snapshot(project.root)))
        state = {
            "status": "PAUSED_JOB_FAILURE",
            "sessions": {},
            "settings": {},
            "stages": [],
            "job_failure": dict(FAILURE),
            "job_retry_authorization": {"token": "jr:investigator"},
            "recovery_context": {
                "kind": "job_failure",
                "stage": "investigate_stuck",
                "attempt_id": "001/stuck-investigation-03",
            },
            "active_stage": {
                "role": "astra",
                "stage": "astra_discovery",
                "planning": True,
                "iteration": 1,
                "duration_seconds": 1,
                "exit_code": -15,
                "processes": [],
                "output": str(output),
                "events": str(iteration / "discovery-05.jsonl"),
                "before_ref": str(before),
            },
        }
        stage_recovery.abandon_stage(state, run, project.root, "001/discovery-05")
        self.assertEqual("PAUSED_STAGE_ABANDONED", state["status"])
        self.assertEqual("astra_discovery", state["next_stage"])
        self.assertNotIn("job_failure", state)
        self.assertNotIn("job_retry_authorization", state)
        self.assertEqual("001/discovery-05", state["recovery_context"]["attempt_id"])
        self.assertIsNone(job_failure.resume_gate(state, resume=True, retry=False, token=None))


if __name__ == "__main__":
    unittest.main()
