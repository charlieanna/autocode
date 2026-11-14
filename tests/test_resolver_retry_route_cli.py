"""AutoCode's own scoped retry of a held incident runs on the saved routes, and a person is no longer
stopped at the hold to reroute it (#2485). When the provider refuses that retry, the model the stop
asks for is taken the way the stop names it (#3159)."""

import json
import shlex
import tempfile
import unittest
from pathlib import Path

from harness import catalog
from harness.driver import DriveError, default_autocode, fake_setup
from harness.project import materialize

from .test_rework_cli import SavedRoutesDriver

REFUSED, NAMED = "gpt-6-astra", "gpt-5.6-terra"  # the Resolver's saved model, and the one a person names


class ResolverRetryRouteCLI(unittest.TestCase):
    def driver(self, **env_extra):
        scratch = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.addCleanup(scratch.cleanup)
        root = Path(scratch.name).resolve()
        scenario = catalog.load("rework-same-failure")
        flags, env = fake_setup(scenario, root, scenario.reference)
        (root / "config").mkdir()
        env["XDG_CONFIG_HOME"] = str(root / "config")
        env.update(env_extra)
        # This scenario escalates the Builder before holding. Reuse the driver
        # that does not reapply starting model flags on every saved-run call.
        driver = SavedRoutesDriver(
            materialize(scenario.seed, root / "project"),
            root,
            flags,
            env,
            autocode=default_autocode(),
            max_steps=40,
            timeout_seconds=240,
        )
        return scenario, root, driver

    def test_autocodes_own_retry_of_the_held_incident_takes_no_route_change(self):
        scenario, root, driver = self.driver()
        before = driver.drive(scenario.brief)
        # The repeat held the Resolver, then the Builder; AutoCode took the one scoped retry of each itself
        # (the grant a person used to give with --resume-paused --retry-failed-stage), on the saved routes
        # and with no route change (#2485). The hold never stopped the run for a person.
        self.assertNotEqual("RESOLVER_PENDING", before["status"], before.get("stop_reason"))
        self.assertNotIn("No causal progress", before["stop_reason"])
        state = driver.state()
        grants = state["failure_retry_authorizations"]
        self.assertEqual([("autocode", "resolve"), ("autocode", "builder")], [(g["actor"], g["stage"]) for g in grants])
        self.assertFalse([g for g in grants if g.get("resolver_route_change")])
        receipts = [row["recovery_novelty"] for row in state["stages"] if row.get("recovery_novelty")]
        self.assertEqual(
            [(None, "first_incident"), ("automatic", "explicit_retry"), ("automatic", "explicit_retry")],
            [(row.get("grant_kind"), row["reason"]) for row in receipts],
        )
        self.assertEqual("derived", before["routes"]["resolver"]["origin"])
        for step in driver.steps:
            self.assertNotIn("--retry-failed-stage", step["stdout_tail"] + step["stderr_tail"], step)
        # At the stop the run reaches instead, the ladder's end is a recorded could-not-finish
        # outcome (#2597): the rerouted scoped retry is refused at the terminal stop and launches
        # nothing, and no settings change is saved — not even the model flag. Starting defaults
        # must not be replayed either.
        driver.flags = []
        before_trace = (root / "rework-trace.jsonl").read_text().splitlines()
        rejected = driver.call(
            "reroute-retry",
            "--resume-paused",
            "--retry-failed-stage",
            "--resolver-model",
            "gpt-6.1-sol",
            "--resolver-reasoning-effort",
            "medium",
        )
        self.assertEqual(2, rejected.returncode)
        self.assertEqual("", rejected.stderr, rejected.stdout)
        self.assertIn("PAUSED_INTERVENTION", rejected.stdout)
        self.assertIn("could not finish", rejected.stdout)
        after = driver.view()
        self.assertEqual(before["status"], after["status"])
        self.assertEqual(before["routes"], after["routes"])
        self.assertEqual(before_trace, (root / "rework-trace.jsonl").read_text().splitlines())

    def test_the_model_named_after_setting_a_refused_retry_aside_runs_it(self):
        # As in #3093's run: AutoCode's own scoped retry of the held Resolver is refused by the
        # provider's content filter, and the stop offers --abandon-stage, then --resume-paused
        # --resolver-model MODEL. Both steps must be taken, and the Resolver must run on MODEL.
        scenario, root, driver = self.driver(SCENARIO_FAKE_REFUSE_RESOLVER=REFUSED)
        # The refused attempt is now published as a model-choice gate. The driver leaves this
        # owner decision unanswered, rather than resuming the same refused call until it errors.
        before = driver.drive(scenario.brief)
        self.assertEqual("WAITING_FOR_USER", before["status"])
        need = before["needs"]
        self.assertEqual(
            ("answer", "operational_exhaustion", "PAUSED_CONTENT_FILTER"),
            tuple(need[key] for key in ("kind", "resolver_scope", "pause_origin")),
        )
        self.assertEqual(
            ("route-resolver", "resolver", "content_filter", REFUSED),
            tuple(need["route"][key] for key in ("question_id", "role", "cause", "stopped_model")),
        )
        self.assertEqual(["route-resolver"], [question["id"] for question in need["questions"]])
        self.assertEqual(
            ["--answer", "route-resolver=MODEL", "--resolver-token", "-"],
            shlex.split(before["next_command"])[-4:],
        )
        self.assertEqual((REFUSED, "derived"), tuple(before["routes"]["resolver"][key] for key in ("model", "origin")))
        self.assertEqual([], before["route_assignments"])
        stopped = driver.state()
        attempt = "002/resolver-01"
        request = stopped["resolver_human_request"]
        self.assertTrue(need["resolver_token"])
        self.assertEqual(
            (request["request_id"], request["request_token"]),
            (need["resolver_request_id"], need["resolver_token"]),
        )
        refused = [row for row in before["usage"]["accounting"]["attempts"] if row["stage"] == "resolve"]
        self.assertEqual(
            [(attempt, REFUSED, 3)], [(row["attempt_id"], row["model"], row["exit_code"]) for row in refused]
        )
        calls = [json.loads(line) for line in (root / "resolver-calls.jsonl").read_text().splitlines()]
        self.assertEqual([(REFUSED, True)], [(row["model"], row["refused"]) for row in calls])
        self.assertEqual("resolve", stopped["active_stage"]["stage"])
        self.assertIn(f"--abandon-stage {attempt}, then --resume-paused --resolver-model MODEL", stopped["stop_reason"])
        self.assertTrue(stopped["resolution_request"]["novelty_hold"], "the Resolver was held before its retry")
        self.assertEqual(
            [("autocode", "resolve")], [(g["actor"], g["stage"]) for g in stopped["failure_retry_authorizations"]]
        )
        driver.flags = []

        def call(name, *args):
            result = driver.call(name, *args)
            self.assertNotIn("nothing was saved", result.stdout + result.stderr, (name, result.stdout[-2000:]))
            return result

        self.assertEqual(0, call("abandon", "--abandon-stage", attempt).returncode)
        # A change the held packet cannot take as a route assignment names no retry the run would refuse.
        # The driver raises on argparse's usage error, carrying the CLI's refusal; a host whose CLI first
        # notes a capability drop on stderr hands the refusal back as the call's own stderr instead.
        before_refusal = (driver.run_dir / "state.json").read_bytes()
        try:
            refused = driver.call("effort-only", "--resume-paused", "--resolver-reasoning-effort", "medium")
        except DriveError as error:
            refusal = str(error)
        else:
            self.assertEqual(2, refused.returncode)
            refusal = refused.stderr
        self.assertIn(
            "A held Resolver's model changes only with its scoped retry, which cannot be given now "
            "(Scoped recovery retry requires its no-progress hold); nothing was saved",
            refusal,
        )
        self.assertNotIn("requires --resume-paused --retry-failed-stage", refusal)
        self.assertEqual(before_refusal, (driver.run_dir / "state.json").read_bytes())
        call("reroute", "--resume-paused", "--resolver-model", NAMED, "--resolver-reasoning-effort", "medium")
        calls = [json.loads(line) for line in (root / "resolver-calls.jsonl").read_text().splitlines()]
        self.assertEqual([(REFUSED, True), (NAMED, False)], [(row["model"], row["refused"]) for row in calls])
        state = driver.state()
        resolvers = [row for row in state["stages"] if row.get("stage") == "resolve"]
        self.assertEqual(NAMED, resolvers[-1]["command"][resolvers[-1]["command"].index("--model") + 1])
        # The refused call returned nothing, so AutoCode's one retry was still there for the named model,
        # and the packet's binding took the assignment, its effort and its origins as no change.
        self.assertEqual(
            [("automatic", "explicit_retry")] * 2,
            [(row["recovery_novelty"]["grant_kind"], row["recovery_novelty"]["reason"]) for row in resolvers],
        )
        self.assertEqual(
            [("resolver", REFUSED, NAMED, "resume_flag", attempt)],
            [
                (row["role"], row["from"], row["to"], row["via"], row["attempt_id"])
                for row in driver.view()["route_assignments"]
            ],
        )
        # The run goes on from the Resolver's answer; the set-aside attempt's question is never asked again.
        after = driver.until_stopped()
        self.assertNotIn(after["status"], ("PAUSED_CONTENT_FILTER", "PAUSED_STALE_HANDOFF", "WAITING_FOR_USER"))
        self.assertNotIn("content filter", json.dumps(after["needs"]))
        later = [row["stage"] for row in driver.state()["stages"][len(state["stages"]) - 1 :]]
        self.assertIn("investigate_stuck", later, "the run continued past the Resolver")


if __name__ == "__main__":
    unittest.main()
