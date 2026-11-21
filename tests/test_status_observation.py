"""One bounded budget covers all nested, read-only status observations."""

import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import autocode_failure_retry_admission as failure_retry_admission
import autocode_interventions as interventions
import autocode_job_retry_admission as job_retry_admission
import autocode_status_observation as observation
import autocode_status_observation_worker as worker
import autocode_util as util
import autocode_verification_inspection as verification


class StatusObservationTests(unittest.TestCase):
    def test_effort_offer_uses_saved_kilo_provider_without_listing_models(self):
        import autocode_interventions as interventions
        import autocode_job_retry_admission as admission
        import autocode_providers as providers
        import autocode_role_transport as role_transport

        state = {
            "settings": {
                "engine": "opencode",
                "provider": "kilocode",
                "roles": {"architect": {"reasoning_effort": "high"}},
            },
            "job_failure": {"configuration": {"roles": {"architect": {"reasoning_effort": "medium"}}}},
        }
        facade = Mock(CONFIGURED=True)
        with (
            patch.object(admission, "validate_retry", side_effect=admission.RetryConfigurationChanged("drift")),
            patch.object(admission, "route_is_repairable", return_value=True) as repairable,
            patch.object(admission.quota_route, "job_question", return_value={"route_role": "architect"}),
            patch.object(interventions, "pending", return_value=[]),
            patch.object(providers, "resolve", return_value=facade) as resolve,
            patch.object(role_transport, "adapter", return_value=facade) as adapter,
        ):
            result = worker._worker(
                {
                    "operation": "job_retry",
                    "payload": {"state": state, "workspace": "/tmp", "run_dir": "/tmp/run", "token": "jr:test"},
                }
            )
        resolve.assert_called_once_with("kilocode")
        adapter.assert_called_once_with(state["settings"], "architect", facade, "/tmp")
        self.assertTrue(repairable.call_args.kwargs["configured_tool"])
        self.assertEqual({"allowed": False, "route_allowed": True, "effort_drift": True, "reason": "drift"}, result)
        facade.check_models.assert_not_called()
        facade.available_models.assert_not_called()

    def test_effort_offer_holds_when_role_transport_has_changed(self):
        import autocode_providers as providers
        import autocode_role_transport as role_transport

        state = {"settings": {"engine": "opencode", "provider": "kilocode"}}
        request = {
            "operation": "job_retry",
            "payload": {"state": state, "workspace": "/tmp", "run_dir": "/tmp/run", "token": "jr:test"},
        }
        for error in (ValueError("missing saved transport"), util.Paused("PAUSED_TRANSPORT_CHANGED", "drift")):
            with (
                self.subTest(error=error),
                patch.object(
                    job_retry_admission,
                    "validate_retry",
                    side_effect=job_retry_admission.RetryConfigurationChanged("drift"),
                ),
                patch.object(job_retry_admission.quota_route, "job_question", return_value={"route_role": "architect"}),
                patch.object(interventions, "pending", return_value=[]),
                patch.object(providers, "resolve", return_value=Mock(CONFIGURED=True)),
                patch.object(role_transport, "adapter", side_effect=error),
                patch.object(job_retry_admission, "route_is_repairable") as repairable,
            ):
                self.assertEqual(
                    {"allowed": False, "reason": "The saved provider could not be inspected"}, worker._worker(request)
                )
            repairable.assert_not_called()

    def test_effort_offer_holds_when_saved_provider_cannot_be_inspected(self):
        import autocode_providers as providers

        state = {"settings": {"engine": "opencode", "provider": "kilocode"}}
        request = {
            "operation": "job_retry",
            "payload": {"state": state, "workspace": "/tmp", "run_dir": "/tmp/run", "token": "jr:test"},
        }
        with (
            patch.object(
                job_retry_admission,
                "validate_retry",
                side_effect=job_retry_admission.RetryConfigurationChanged("drift"),
            ),
            patch.object(job_retry_admission.quota_route, "job_question", return_value={"route_role": "architect"}),
            patch.object(interventions, "pending", return_value=[]),
            patch.object(providers, "resolve", side_effect=ValueError("unavailable")),
            patch.object(job_retry_admission, "route_is_repairable") as repairable,
        ):
            self.assertEqual(
                {"allowed": False, "reason": "The saved provider could not be inspected"}, worker._worker(request)
            )
        repairable.assert_not_called()

    def test_effort_offer_checks_real_missing_role_transport_identity(self):
        import autocode_providers as providers

        settings = {
            "engine": "opencode",
            "provider": "kilocode",
            "roles": {"architect": {"engine": "opencode", "provider": "other"}},
        }
        request = {
            "operation": "job_retry",
            "payload": {"state": {"settings": settings}, "workspace": "/tmp", "run_dir": "/tmp/run", "token": "t"},
        }
        with (
            patch.object(
                job_retry_admission,
                "validate_retry",
                side_effect=job_retry_admission.RetryConfigurationChanged("drift"),
            ),
            patch.object(job_retry_admission.quota_route, "job_question", return_value={"route_role": "architect"}),
            patch.object(interventions, "pending", return_value=[]),
            patch.object(providers, "resolve", return_value=Mock(CONFIGURED=True)),
            patch.object(job_retry_admission, "route_is_repairable") as repairable,
        ):
            self.assertEqual(
                {"allowed": False, "reason": "The saved provider could not be inspected"}, worker._worker(request)
            )
        repairable.assert_not_called()

    def test_effort_offer_does_not_load_provider_for_invalid_role(self):
        import autocode_providers as providers

        request = {
            "operation": "job_retry",
            "payload": {"state": {}, "workspace": "/tmp", "run_dir": "/tmp/run", "token": "t"},
        }
        with (
            patch.object(
                job_retry_admission,
                "validate_retry",
                side_effect=job_retry_admission.RetryConfigurationChanged("drift"),
            ),
            patch.object(job_retry_admission.quota_route, "job_question", return_value={"route_role": "unknown"}),
            patch.object(interventions, "pending", return_value=[]),
            patch.object(providers, "resolve") as resolve,
        ):
            self.assertEqual({"allowed": False, "reason": "drift"}, worker._worker(request))
        resolve.assert_not_called()

    def test_suite_reports_fail_closed_on_timeout_or_malformed_worker_output(self):
        with patch.object(observation, "_observe", side_effect=subprocess.TimeoutExpired("worker", 1)):
            self.assertEqual([], observation.suite_reports({}, "/tmp/project", "/tmp/run"))
        with patch.object(
            observation, "_observe", return_value=[{"kind": "collection", "independently_verified": True}]
        ):
            self.assertEqual([], observation.suite_reports({}, "/tmp/project", "/tmp/run"))

    def test_nested_observations_share_one_deadline_and_do_not_launch_after_it(self):
        result = SimpleNamespace(stdout='{"result": {"ok": true}}')
        run = Mock(return_value=result)

        @observation.budgeted
        def project():
            observation._observe("first", {})
            observation._observe("second", {})
            with self.assertRaises(subprocess.TimeoutExpired):
                observation._observe("third", {})

        with (
            patch.object(observation.time, "monotonic", side_effect=[100, 101, 110, 116]),
            patch.object(observation.captured, "run", run),
        ):
            project()
        self.assertEqual([14.0, 5.0], [call.kwargs["timeout"] for call in run.call_args_list])

    def test_failed_worker_is_not_an_observation(self):
        with patch.object(observation.captured, "run", side_effect=subprocess.TimeoutExpired("worker", 1)):
            self.assertIsNone(observation.report_repair({}, "/tmp/project"))
        with patch.object(observation, "_observe", return_value={"revision": "fresh", "stale": True}):
            self.assertEqual({"revision": "fresh", "stale": True}, observation.report_repair({}, "/tmp/project"))

    def test_uncertain_cleanup_blocks_later_observations_in_the_same_projection(self):
        run = Mock(side_effect=observation.captured.ProcessError("cleanup unconfirmed"))

        @observation.budgeted
        def project():
            self.assertIsNone(observation.source_snapshot({}, "/tmp/project"))
            self.assertIsNone(observation.report_repair({}, "/tmp/project"))

        with patch.object(observation.captured, "run", run):
            project()
        self.assertEqual(1, run.call_count)

    def test_job_retry_worker_preserves_refusal_and_unavailable_source(self):
        request = {"operation": "job_retry", "payload": {"state": {}, "workspace": "/project", "token": "token"}}
        with patch.object(job_retry_admission, "validate_retry", return_value={}):
            self.assertEqual({"allowed": True}, worker._worker(request))
        for error in (ValueError("Source changed"), util.Paused("PAUSED_SOURCE_UNAVAILABLE", "Git unavailable")):
            with self.subTest(error=error), patch.object(job_retry_admission, "validate_retry", side_effect=error):
                self.assertEqual({"allowed": False, "reason": str(error)}, worker._worker(request))

    def test_model_reroute_observation_refuses_queued_intervention(self):
        state = {"run_dir": "/run"}
        request = {"operation": "job_retry", "payload": {"state": state, "workspace": "/project", "token": "t"}}
        error = job_retry_admission.RetryConfigurationChanged("Configuration changed")
        with (
            patch.object(job_retry_admission, "validate_retry", side_effect=error),
            patch.object(interventions, "pending", return_value=[{"kind": "pause"}]),
            patch.object(job_retry_admission, "route_is_repairable") as preflight,
        ):
            result = worker._worker(request)
        self.assertFalse(result["allowed"])
        self.assertNotIn("route_allowed", result)
        preflight.assert_not_called()

    def test_model_reroute_observes_the_explicit_run_directory(self):
        request = {
            "operation": "job_retry",
            "payload": {"state": {}, "workspace": "/project", "run_dir": "/actual-run", "token": "t"},
        }
        error = job_retry_admission.RetryConfigurationChanged("Configuration changed")
        with (
            patch.object(job_retry_admission, "validate_retry", side_effect=error),
            patch.object(interventions, "pending", return_value=[]) as pending,
            patch.object(job_retry_admission.quota_route, "job_question", return_value={"route_role": "architect"}),
            patch.object(job_retry_admission, "route_is_repairable", return_value=True),
        ):
            result = worker._worker(request)
        self.assertTrue(result["route_allowed"])
        pending.assert_called_once_with(Path("/actual-run"))

    def test_repeated_failure_worker_distinguishes_source_error_from_stale_frontier(self):
        request = {"operation": "failure_retry", "payload": {"state": {}, "workspace": "/project"}}
        refused = failure_retry_admission.FailureRetryFrontierRefused("Source changed")
        with patch.object(failure_retry_admission, "failure_retry_frontier", side_effect=refused):
            self.assertEqual(
                {"allowed": False, "reason": "Source changed", "unavailable": False}, worker._worker(request)
            )
        for error in (
            ValueError("Git failed"),
            ValueError("Unsafe source path"),
            util.Paused("PAUSED_SOURCE_UNAVAILABLE", "unavailable"),
        ):
            with (
                self.subTest(error=error),
                patch.object(failure_retry_admission, "failure_retry_frontier", side_effect=error),
            ):
                self.assertEqual({"allowed": False, "reason": str(error), "unavailable": True}, worker._worker(request))

    def test_malformed_repeated_failure_observation_is_unavailable(self):
        for value in (
            {"allowed": False, "reason": "stale"},
            {"allowed": False, "reason": "stale", "unavailable": "no"},
        ):
            with self.subTest(value=value), patch.object(observation, "_observe", return_value=value):
                self.assertTrue(observation.failure_retry({}, "/project")["unavailable"])
        valid = {"allowed": False, "reason": "Source changed", "unavailable": False}
        with patch.object(observation, "_observe", return_value=valid):
            self.assertEqual(valid, observation.failure_retry({}, "/project"))

    def test_job_retry_timeout_or_unconfirmed_cleanup_fails_closed(self):
        for error in (subprocess.TimeoutExpired("worker", 1), observation.captured.ProcessError("uncertain")):
            with self.subTest(error=error), patch.object(observation.captured, "run", side_effect=error):
                result = observation.job_retry({}, "/project", "token")
                self.assertFalse(result["allowed"])
                self.assertIn("could not be checked", result["reason"])
        with patch.object(observation, "_observe", return_value={"allowed": True}):
            self.assertTrue(observation.job_retry({}, "/project", "token")["allowed"])
        with patch.object(observation, "_observe", return_value={"allowed": "yes"}):
            self.assertFalse(observation.job_retry({}, "/project", "token")["allowed"])

    def test_evidence_worker_rereads_old_checkpoint_with_saved_name_migration(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "state.json").write_text('{"next_stage": "terra", "settings": {"roles": {"terra": {}}}}')
            request = {
                "operation": "evidence_inspection",
                "payload": {
                    "state": {},
                    "workspace": str(root),
                    "run_dir": str(root),
                    "accepted_human_ids": [],
                    "initial_snapshot": None,
                },
            }
            with patch.object(verification, "inspect", side_effect=lambda *args, **kwargs: kwargs["read_state"]()):
                result = worker._worker(request)
        self.assertEqual("builder", result["next_stage"])


if __name__ == "__main__":
    unittest.main()
