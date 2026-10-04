"""Black-box gates: run this file with the repository venv, no live providers.

Unlike catalog oracles, these checks observe the actual S1 boundary before
restarting the same CLI run. They import the black-box harness, never tools.
"""
import dataclasses
import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from harness import catalog
from harness.driver import Driver, DriveError, default_autocode, fake_setup
from harness.oracle import run as command
from harness.project import materialize


class ProgressiveCLI(unittest.TestCase):
    def driver(self, directory, fault="progressive_happy"):
        scenario = dataclasses.replace(catalog.load("progressive-learning-journey"), fake_fault=fault)
        root = Path(directory)
        project = materialize(scenario.seed, root / "project")
        flags, env = fake_setup(scenario, root, scenario.reference)
        driver = Driver(project, root, flags, env, autocode=default_autocode(), max_steps=60,
                        timeout_seconds=180)
        return scenario, driver

    def observed_stages(self, view):
        attempts = view["progressive"]["allowance_usage"]["attempts"]
        return [Path(identity).stem.rsplit("-", 1)[0] for identity, attempt in attempts.items()
                if (attempt.get("outcome") or {}).get("exit_code") == 0]

    def stage_statuses(self, scenario, driver):
        """Observe real CLI stage checkpoints, never private controller state."""
        driver.call("start", "--pause-after-stage", task=scenario.brief)
        driver.run_dir = next((driver.project / ".autocode" / "runs").iterdir())
        for _ in range(45):
            status = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
            view = status["view"]
            yield status
            if view["done"]:
                return
            need = view["needs"]
            if need["kind"] == "approve_plan":
                driver.serve(need)
            elif need["kind"] == "resume":
                self.assertEqual(view["status"], "PAUSED_REQUESTED", view)
                driver.call("operator-checkpoint-resume", "--resume-paused", "--pause-after-stage")
            elif need["kind"] == "continue":
                driver.call("resume", "--pause-after-stage")
            else:
                self.fail(f"Unexpected need at a stage checkpoint: {view}")
        self.fail("No completion within the bounded CLI observation steps")

    def goal_boundary(self, scenario, driver, *, permission=False):
        for status in self.stage_statuses(scenario, driver):
            view = status["view"]
            progressive = view.get("progressive") or {}
            if permission and view["needs"].get("request_kind") == "permission":
                return status
            if not permission and (progressive.get("active_slice") or {}).get("id") == "S2":
                return status
        self.fail("The original S1/independently reviewed S2 boundary never appeared")

    def scoped_permission(self, answer, *, restart=False):
        with tempfile.TemporaryDirectory(prefix="progressive-scoped-permission-") as directory:
            scenario, driver = self.driver(directory, "progressive_scoped_permission")
            pending = None
            for status in self.stage_statuses(scenario, driver):
                if status["view"]["needs"].get("request_kind") == "permission":
                    pending = status
                    break
            self.assertIsNotNone(pending, "The active Builder never requested scoped consent")
            captured = pending["human_escalation"]
            original_need = pending["view"]["needs"]
            original_token = pending["contract_token"]
            self.assertTrue(pending["human_request_authorized"])
            self.assertEqual(captured["scope"], "permission")
            self.assertEqual(captured["request_id"], original_need["resolver_request_id"])
            self.assertEqual(captured["request_token"], original_need["resolver_token"])
            self.assertEqual(captured["receipt_hash"], captured["request_id"])
            self.assertIn(answer, captured["request"]["options"])
            self.assertTrue(captured["request"]["proposed_delta"].startswith("No goal, scope, criterion"))
            progressive = pending["view"]["progressive"]
            self.assertTrue(progressive["delegation_approved"])
            self.assertEqual(progressive["active_slice"]["id"], "S2")
            self.assertIn("lessons.py", progressive["active_slice"]["paths"])
            self.assertEqual([row["slice_id"] for row in progressive["demonstrated_slices"]], ["S1"])
            goal_card = driver.call("show-goal", "--show-goal", action=True).stdout
            source = {path: (driver.project / path).read_text() for path in ("lessons.py", "progress.py")}
            self.assertNotIn("# Scoped consent: lesson note", source["lessons.py"])
            prior_steps = []
            if restart:
                # All CLI processes have exited. Drop the client and restore it
                # from only its public run directory and provider configuration.
                run_dir = driver.run_dir
                project, root, flags, env = driver.project, driver.root, driver.flags, driver.env
                prior_steps = list(driver.steps)
                del driver
                driver = Driver(project, root, flags, env, autocode=default_autocode(),
                                max_steps=60, timeout_seconds=180)
                driver.run_dir = run_dir
                restored = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
                self.assertEqual(restored["human_escalation"], captured)
                self.assertEqual(restored["contract_token"], original_token)
                self.assertEqual(restored["view"]["needs"], original_need)
                self.assertEqual(restored["view"]["progressive"], progressive)
            question = original_need["questions"][0]["id"]
            with self.assertRaises(DriveError):
                driver.call("permission-without-envelope", "--answer", f"{question}={answer}", action=True)
            with self.assertRaises(DriveError):
                driver.call("permission-stale-envelope", "--answer", f"{question}={answer}",
                            "--resolver-token", "stale-token", action=True)
            rejected = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
            self.assertEqual(rejected["human_escalation"], captured)
            self.assertEqual(rejected["view"]["progressive"], progressive)
            # Deliberately use the ORIGINAL captured envelope, never select a
            # replacement receipt after restart or an invalid answer attempt.
            driver.call("permission-current-envelope", "--answer", f"{question}={answer}",
                        "--resolver-token", original_need["resolver_token"], action=True)
            answered = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
            self.assertEqual(answered["contract_token"], original_token)
            self.assertIsNone(answered["human_escalation"])
            self.assertNotEqual(answered["view"]["needs"]["kind"], "approve_plan")
            self.assertEqual(answered["view"]["progressive"], progressive)
            shown = driver.call("unchanged-goal", "--show-goal", action=True).stdout
            def printed_body(text):
                body = text.split("\nIntended user:\n", 1)[1]
                for boundary in ("\n\nSaved user answers:", "\n\nDecision needed:", "\n\nJoint planning:"):
                    body = body.split(boundary, 1)[0]
                return body
            self.assertEqual(printed_body(shown), printed_body(goal_card))
            saved, _ = json.JSONDecoder().raw_decode(shown.split("Saved user answers:\n", 1)[1].lstrip())
            self.assertEqual(saved[question]["kind"], "permission_answer")
            self.assertEqual(saved[question]["text"], answer)
            self.assertEqual(saved[question]["contract_token"], original_token)
            self.assertEqual(saved[question]["request"], captured["request"])
            for path, contents in source.items():
                self.assertEqual((driver.project / path).read_text(), contents)
            final = driver.until_stopped()
            final_status = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
            self.assertTrue(final["done"], final.get("stop_reason"))
            self.assertTrue(final_status["completion_current"])
            self.assertEqual(final_status["contract_token"], original_token)
            self.assertTrue(final["progressive"]["delegation_approved"])
            self.assertEqual(final["progressive"]["required_checks"], progressive["required_checks"])
            self.assertEqual(final["progressive"]["retirements"], [])
            self.assertEqual(final["progressive"]["demonstrated_slices"][0], progressive["demonstrated_slices"][0])
            self.assertEqual([row["slice_id"] for row in final["progressive"]["demonstrated_slices"]], ["S1", "S2"])
            old_usage = progressive["allowance_usage"]
            usage = final["progressive"]["allowance_usage"]
            self.assertEqual(usage["pools"].keys(), old_usage["pools"].keys())
            for key, pool in old_usage["pools"].items():
                self.assertEqual(usage["pools"][key]["reviews_used"], pool["reviews_used"])
                self.assertEqual(usage["pools"][key]["review_limit"], 2)
                self.assertEqual(usage["pools"][key]["seconds_limit"], 5400)
                self.assertGreaterEqual(usage["pools"][key]["seconds_used"], pool["seconds_used"])
            for identity, attempt in old_usage["attempts"].items():
                self.assertEqual(usage["attempts"][identity], attempt)
            steps = [*prior_steps, *driver.steps]
            self.assertEqual(sum(step["kind"] == "approve-plan" for step in steps), 1)
            self.assertFalse(any("--edit-goal" in step["args"] or "--planning-review-call-limit" in step["args"] for step in steps))
            delivered = (driver.project / "lessons.py").read_text()
            self.assertEqual(delivered.count("# Scoped consent: lesson note"), 1 if answer.startswith("Yes,") else 0)
            self.assertEqual((driver.project / "progress.py").read_text(), source["progress.py"])
            self.assertNotIn("# Scoped consent: lesson note", (driver.project / "progress.py").read_text())
            self.assertFalse((driver.project / "optional-note.txt").exists())
            self.assertTrue(all(row.ok for row in scenario.oracle()(driver.project, scenario)))

    def test_scoped_permission_denial_and_condition_preserve_unchanged_goal(self):
        for answer in ("No. Do not add the optional note anywhere. Deliver the original goal using the offline fallback.",
                       "Yes, append only '# Scoped consent: lesson note' to lessons.py. Do not add this note to progress.py or any other file."):
            with self.subTest(answer=answer):
                self.scoped_permission(answer)

    def test_frozen_scoped_request_original_envelope_survives_client_restart(self):
        for answer in ("No. Do not add the optional note anywhere. Deliver the original goal using the offline fallback.",
                       "Yes, append only '# Scoped consent: lesson note' to lessons.py. Do not add this note to progress.py or any other file."):
            with self.subTest(answer=answer):
                self.scoped_permission(answer, restart=True)

    def edited_body(self, driver, original, *, marker="valid", permission=False):
        # The model's published plan is user-editable input, not mutable run state.
        report = sorted(driver.run_dir.glob("iterations/*/plan-finalize-[0-9][0-9].json"))[0]
        body = json.loads(report.read_text())["contract"]
        body.pop("initial_task", None)
        body["intended_outcome"] = "Retain durable lesson answers and expose a browsable lesson catalog"
        removes = body["required_behaviors"][1]
        body["required_behaviors"] = [body["required_behaviors"][0], "Learners can browse and open both catalog lessons"]
        body["end_to_end_flow"] = ["Browse lessons", "Open a selected lesson", "Answer", "Reopen saved progress"]
        body["acceptance_criteria"][0]["criterion"] = "Saved answers survive reopening and learners can browse and complete both lessons"
        body["milestones"][0]["objective"] = body["intended_outcome"]
        body["constraints"] = [line for line in body["constraints"] if not line.startswith("Progressive")]
        body["technical_approach"] = ["Keep lessons.py and progress.py; retain all earlier saved-answer checks"]
        body["scope_exclusions"] = []
        if permission:
            body["permission_boundaries"] = ["Read and edit source only in this scenario workspace; catalog export files may use learner-chosen paths outside this workspace; network access remains forbidden"]
            body["constraints"].append("User permission decision: catalog export files may use learner-chosen paths outside this workspace; network access remains forbidden")
        if marker != "missing" and not permission:
            old = next(row for row in original["view"]["progressive"]["required_checks"] if row["id"] == "B")
            self.assertEqual(old["method"], "python3 -m unittest test_journey.Recommendation")
            definition = {key: old[key] for key in ("id", "relation", "criterion_ids", "method")}
            definition["commands"] = [old["method"]]
            definition["criterion_ids"] = sorted(set(definition["criterion_ids"]))
            digest = hashlib.sha256(json.dumps(definition, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
            declaration = {"check_id": "B", "check_hash": digest, "removes": removes}
            if marker == "bad_hash":
                declaration["check_hash"] = "0" * 64
            if marker == "unknown_check":
                declaration["check_id"] = "fabricated-B"
            if marker == "unrelated_removal":
                declaration["removes"] = "An invented behavior that was never approved"
            line = "Progressive check retirement: " + json.dumps(declaration, sort_keys=True, separators=(",", ":"))
            body["scope_exclusions"] = [line]
            if marker == "bad_mirror":
                body["constraints"].append(line + " forged")
        if marker == "rename":
            body["acceptance_criteria"][0]["id"] = "C_RENAMED"
            body["milestones"][0]["acceptance_criteria"] = ["C_RENAMED"]
            body["scope_exclusions"] = []
            body["constraints"] = [line for line in body["constraints"] if not line.startswith("Progressive")]
        path = driver.root / "user-edited-goal.json"
        path.write_text(json.dumps(body))
        return path, body

    def replan_goal(self, driver, original, path):
        driver.call("edit-goal", "--edit-goal", str(path), "--expected-goal-token", original["contract_token"], action=True)
        suspended = driver.view()
        self.assertFalse(suspended["progressive"]["delegation_approved"])
        self.assertEqual(suspended["progressive"]["required_checks"], original["view"]["progressive"]["required_checks"])
        self.assertEqual(suspended["progressive"]["demonstrated_slices"], original["view"]["progressive"]["demonstrated_slices"])
        self.assertFalse(suspended["progressive"]["retirements"])
        # Continue the ordinary full planning cycle without answering budget needs.
        driver.call("renewal-planning", "--resume-paused")
        exhausted = driver.view()
        self.assertEqual(exhausted["status"], "PAUSED_PLANNING_BUDGET", exhausted.get("stop_reason"))
        pools = exhausted["progressive"]["allowance_usage"]["pools"]
        self.assertTrue(all(row["review_limit"] == 2 for row in pools.values()))
        driver.call("authorize-review-ceiling", "--planning-review-call-limit", "4", action=True)
        authorized = driver.view()
        self.assertEqual(authorized["progressive"]["allowance_usage"]["pools"].keys(), pools.keys())
        for key in pools:
            self.assertEqual(authorized["progressive"]["allowance_usage"]["pools"][key]["reviews_used"], pools[key]["reviews_used"])
        driver.call("renewal-final-review", "--resume-paused")
        before = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
        self.assertEqual(before["view"]["needs"]["kind"], "approve_plan", before["view"])
        self.assertFalse(before["view"]["progressive"]["retirements"])
        self.assertEqual(before["view"]["progressive"]["required_checks"], original["view"]["progressive"]["required_checks"])
        self.assertFalse(before["view"]["progressive"]["current_whole_product_proof"]["verified"])
        return before

    def test_goal_change_retirement_requires_review_ceiling_reapproval_and_fresh_restart(self):
        with tempfile.TemporaryDirectory(prefix="progressive-goal-change-") as directory:
            scenario, driver = self.driver(directory, "progressive_goal_change")
            original = self.goal_boundary(scenario, driver)
            path, body = self.edited_body(driver, original)
            before = self.replan_goal(driver, original, path)
            self.assertNotEqual(before["contract_token"], original["contract_token"])
            line = body["scope_exclusions"][0]
            shown = driver.call("show-goal", "--show-goal", action=True)
            self.assertIn(line, shown.stdout)
            reviewed_path = sorted(driver.run_dir.glob("iterations/*/plan-finalize-[0-9][0-9].json"))[-1]
            reviewed = json.loads(reviewed_path.read_text())["contract"]
            self.assertIn(line, reviewed["scope_exclusions"])
            self.assertIn(line, reviewed["constraints"])
            with self.assertRaises(DriveError):
                driver.call("stale-approval", "--approve-goal", original["contract_token"], action=True)
            self.assertFalse(driver.view()["progressive"]["retirements"])
            driver.call("approve-renewed", "--approve-goal", before["contract_token"], action=True)
            renewed = driver.view()
            self.assertTrue(renewed["progressive"]["delegation_approved"])
            self.assertEqual(renewed["progressive"]["demonstrated_slices"], original["view"]["progressive"]["demonstrated_slices"])
            checks = {row["id"] for row in renewed["progressive"]["required_checks"]}
            self.assertIn("A", checks)
            self.assertIn("N", checks)
            self.assertNotIn("B", checks)
            self.assertFalse(renewed["progressive"]["current_whole_product_proof"]["verified"])
            grants = renewed["progressive"]["retirements"]
            self.assertEqual(len(grants), 1)
            self.assertEqual(grants[0]["check_id"], "B")
            self.assertEqual(grants[0]["contract_token"], before["contract_token"])
            self.assertEqual(grants[0]["visible_removal"], line)
            declared = json.loads(line.removeprefix("Progressive check retirement: "))
            self.assertEqual({key: grants[0][key] for key in declared}, declared)
            self.assertTrue(grants[0]["artifact"]["sha256"])
            old_attempts = original["view"]["progressive"]["allowance_usage"]["attempts"]
            for identity, attempt in old_attempts.items():
                self.assertEqual(renewed["progressive"]["allowance_usage"]["attempts"][identity], attempt)
            for key, pool in original["view"]["progressive"]["allowance_usage"]["pools"].items():
                current = renewed["progressive"]["allowance_usage"]["pools"][key]
                self.assertGreaterEqual(current["reviews_used"], pool["reviews_used"])
                self.assertGreaterEqual(current["seconds_used"], pool["seconds_used"])
            restarted = Driver(driver.project, driver.root, driver.flags, driver.env,
                               autocode=default_autocode(), max_steps=60, timeout_seconds=180)
            restarted.run_dir = driver.run_dir
            final = restarted.until_stopped()
            self.assertTrue(final["done"], final)
            self.assertTrue(final["progressive"]["current_whole_product_proof"]["verified"])
            self.assertEqual([row["slice_id"] for row in final["progressive"]["demonstrated_slices"]], ["S1", "S3", "S4"])
            self.assertEqual(final["progressive"]["demonstrated_slices"][0], original["view"]["progressive"]["demonstrated_slices"][0])
            required = {row["id"] for row in final["progressive"]["required_checks"]}
            self.assertTrue({"A", "N", "F", "PRODUCT"} <= required)
            self.assertNotIn("B", required)
            replay = final["evidence"]["check_replay"]
            self.assertNotEqual(replay["source_revision"], original["view"]["evidence"]["check_replay"]["source_revision"])
            commands = {row["command"] for row in replay["checks"]}
            self.assertTrue({"python3 -m unittest test_journey.Skeleton", "python3 -m unittest test_change.NewJourney",
                             "python3 -m unittest test_change", "python3 -m unittest test_journey"} <= commands)
            pools = final["progressive"]["allowance_usage"]["pools"]
            self.assertEqual(len(pools), 2)
            self.assertEqual(sorted(pool["reviews_used"] for pool in pools.values()), [2, 4])

    def test_renewal_without_retirement_marker_keeps_original_checks(self):
        with tempfile.TemporaryDirectory(prefix="progressive-no-retirement-") as directory:
            scenario, driver = self.driver(directory, "progressive_goal_change")
            original = self.goal_boundary(scenario, driver)
            path, _ = self.edited_body(driver, original, marker="missing")
            before = self.replan_goal(driver, original, path)
            driver.call("approve-renewed", "--approve-goal", before["contract_token"], action=True)
            renewed = driver.view()["progressive"]
            self.assertTrue({"A", "B", "PRODUCT", "N"} <= {row["id"] for row in renewed["required_checks"]})
            self.assertFalse(renewed["retirements"])
            self.assertEqual(renewed["demonstrated_slices"], original["view"]["progressive"]["demonstrated_slices"])
            final = driver.until_stopped()
            self.assertTrue(final["done"], final.get("stop_reason"))
            self.assertIn("python3 -m unittest test_journey.Recommendation", [row["command"] for row in
                          final["evidence"]["check_replay"]["checks"]])

    def test_retirement_forgery_and_criterion_rename_cannot_publish_authority(self):
        for marker in ("bad_hash", "unknown_check", "unrelated_removal", "bad_mirror", "rename"):
            with self.subTest(marker=marker), tempfile.TemporaryDirectory(prefix="progressive-retirement-negative-") as directory:
                scenario, driver = self.driver(directory, "progressive_goal_change")
                original = self.goal_boundary(scenario, driver)
                path, _ = self.edited_body(driver, original, marker=marker)
                if marker == "bad_mirror":
                    # The user-authored conflicting visible mirror must fail at
                    # the editor or ordinary review, never become a grant.
                    try:
                        driver.call("edit-goal", "--edit-goal", str(path), action=True)
                        driver.call("renewal-planning", "--resume-paused")
                    except DriveError:
                        pass
                else:
                    before = self.replan_goal(driver, original, path)
                    with self.assertRaises(DriveError):
                        driver.call("reject-invalid-renewal", "--approve-goal", before["contract_token"], action=True)
                refused = driver.view()
                self.assertFalse(refused["done"])
                self.assertFalse(refused["progressive"]["retirements"])
                if marker != "bad_mirror":
                    self.assertFalse(refused["progressive"]["delegation_approved"])
                self.assertTrue({"A", "B", "PRODUCT"} <= {row["id"] for row in refused["progressive"]["required_checks"]})
                self.assertEqual(refused["progressive"]["demonstrated_slices"], original["view"]["progressive"]["demonstrated_slices"])
                self.assertFalse(refused["progressive"]["current_whole_product_proof"]["verified"])

    def test_unapproved_edit_and_stale_goal_envelope_cannot_dispatch(self):
        with tempfile.TemporaryDirectory(prefix="progressive-no-renewal-") as directory:
            scenario, driver = self.driver(directory, "progressive_goal_change")
            original = self.goal_boundary(scenario, driver)
            path, _ = self.edited_body(driver, original)
            with self.assertRaises(DriveError):
                driver.call("stale-edit", "--edit-goal", str(path), "--expected-goal-token", "stale-token", action=True)
            unchanged = driver.view()["progressive"]
            self.assertTrue(unchanged["delegation_approved"])
            self.assertEqual(unchanged["required_checks"], original["view"]["progressive"]["required_checks"])
            before = self.replan_goal(driver, original, path)
            source = (driver.project / "lessons.py").read_text()
            driver.call("continue-without-approval")
            blocked = driver.view()
            self.assertEqual(blocked["needs"]["kind"], "approve_plan")
            self.assertEqual((driver.project / "lessons.py").read_text(), source)
            self.assertEqual(blocked["progressive"]["required_checks"], before["view"]["progressive"]["required_checks"])
            self.assertFalse(blocked["progressive"]["retirements"])

    def test_permission_material_request_needs_current_answer_and_reapproval(self):
        with tempfile.TemporaryDirectory(prefix="progressive-permission-change-") as directory:
            scenario, driver = self.driver(directory, "progressive_permission_change")
            original = self.goal_boundary(scenario, driver, permission=True)
            need = original["view"]["needs"]
            self.assertEqual(need["request_kind"], "permission")
            self.assertFalse(original["view"]["done"])
            source = (driver.project / "lessons.py").read_text()
            question = need["questions"][0]["id"]
            answer = f"{question}=I authorize learner-chosen catalog export paths outside this workspace, but not network access; submit a revised plan for approval"
            with self.assertRaises(DriveError):
                driver.call("missing-answer-envelope", "--answer", answer, action=True)
            with self.assertRaises(DriveError):
                driver.call("stale-answer-envelope", "--answer", answer, "--resolver-token", "stale-token", action=True)
            self.assertEqual((driver.project / "lessons.py").read_text(), source)
            driver.call("answer-permission", "--answer", answer, "--resolver-token", need["resolver_token"], action=True)
            selected = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
            path, _ = self.edited_body(driver, original, marker="missing", permission=True)
            before = self.replan_goal(driver, selected, path)
            self.assertNotEqual(before["contract_token"], original["contract_token"])
            self.assertFalse(before["view"]["progressive"]["delegation_approved"])
            shown = driver.call("show-permission-plan", "--show-goal", action=True)
            self.assertIn("catalog export files may use learner-chosen paths outside this workspace", shown.stdout)
            driver.call("approve-permission-plan", "--approve-goal", before["contract_token"], action=True)
            renewed = driver.view()["progressive"]
            self.assertEqual(renewed["demonstrated_slices"], original["view"]["progressive"]["demonstrated_slices"])
            self.assertIn("A", {row["id"] for row in renewed["required_checks"]})
            final = driver.until_stopped()
            self.assertTrue(final["done"], final.get("stop_reason"))
            self.assertTrue(final["progressive"]["current_whole_product_proof"]["verified"])
            self.assertEqual([row["slice_id"] for row in final["progressive"]["demonstrated_slices"]], ["S1", "S3", "S4"])

    def test_split_preserves_contract_checks_and_shared_default_lineage(self):
        with tempfile.TemporaryDirectory(prefix="progressive-split-") as directory:
            scenario, driver = self.driver(directory, "progressive_split")
            statuses = list(self.stage_statuses(scenario, driver))
            snapshots = [status["view"] for status in statuses]
            final = snapshots[-1]
            self.assertTrue(final["done"], final)
            status = json.loads(driver.call("status", "--status", action=True, record=False).stdout)
            self.assertTrue(status["completion_current"])
            self.assertTrue(final["progressive"]["current_whole_product_proof"]["verified"])
            history = final["progressive"]["demonstrated_slices"]
            self.assertEqual([row["slice_id"] for row in history], ["S1", "S2a", "S2b"])
            approved = [step for step in driver.steps if step["kind"] == "approve-plan"]
            self.assertEqual(len(approved), 1)
            original_token = approved[0]["args"][1]
            approved_views = [view for view in snapshots if (view.get("progressive") or {}).get("delegation_approved")]
            for view in approved_views:
                self.assertEqual(view["evidence"]["outcome"], final["evidence"]["outcome"])
                self.assertEqual(view["progressive"]["disclosure"], final["progressive"]["disclosure"])
            for status in statuses:
                if (status["view"].get("progressive") or {}).get("delegation_approved"):
                    self.assertEqual(status["contract_token"], original_token)
            s1 = next(view for view in snapshots if len((view.get("progressive") or {}).get("demonstrated_slices", [])) == 1)
            s2a = next(view for view in snapshots if len((view.get("progressive") or {}).get("demonstrated_slices", [])) == 2)
            self.assertFalse(s2a["done"])
            self.assertFalse(s2a["progressive"]["current_whole_product_proof"]["verified"])
            self.assertNotEqual(s2a["evidence"]["acceptance"][0]["status"], "verified")
            self.assertIn("A", [row["id"] for row in s2a["progressive"]["required_checks"]])
            future = s2a["progressive"]["tentative_next_work"]
            self.assertEqual([row["id"] for row in future], ["S2b"])
            self.assertEqual(future[0]["checks"][0]["method"], "python3 -m unittest test_journey.Product")
            checks = {row["id"]: row for row in final["progressive"]["required_checks"]}
            self.assertEqual(checks["A"]["method"], "python3 -m unittest test_journey.Skeleton")
            self.assertEqual(checks["PRODUCT"]["method"], "python3 -m unittest test_journey.Product")
            self.assertEqual(checks["PRODUCT"]["relation"], "fully_verify")
            self.assertTrue(any(row["method"] == "python3 -m unittest test_journey" and
                                row.get("origin") == "product_contract" for row in checks.values()), checks)
            self.assertIn("python3 -m unittest test_journey", [row["command"] for row in
                          final["evidence"]["check_replay"]["checks"]])
            pools = final["progressive"]["allowance_usage"]["pools"]
            self.assertEqual(len(pools), 2, "Splitting NEW labels must not create extra allowance pools")
            s1_pools = s1["progressive"]["allowance_usage"]["pools"]
            child_pool = next(key for key, pool in s1_pools.items() if pool["reviews_used"] == 0)
            self.assertEqual(s2a["progressive"]["allowance_usage"]["pools"][child_pool]["reviews_used"], 1)
            self.assertEqual(pools[child_pool]["reviews_used"], 2)
            self.assertGreaterEqual(pools[child_pool]["seconds_used"],
                                    s2a["progressive"]["allowance_usage"]["pools"][child_pool]["seconds_used"])
            for pool in pools.values():
                self.assertEqual(pool["review_limit"], 2)
                self.assertEqual(pool["seconds_limit"], 5400)
            self.assertEqual(final["progressive"]["allowance_usage"]["run_limit"], 43200)
            self.assertTrue(all(row.ok for row in scenario.oracle()(driver.project, scenario)))

    def test_split_retry_does_not_replenish_lineage(self):
        with tempfile.TemporaryDirectory(prefix="progressive-split-retry-") as directory:
            scenario, driver = self.driver(directory, "progressive_split_retry")
            driver.call("start", task=scenario.brief)
            driver.run_dir = next((driver.project / ".autocode" / "runs").iterdir())
            driver.serve(driver.view()["needs"])
            driver.call("resume")
            # The catalog driver normally gives planning-budget feedback; that
            # is a new user action, not automatic retry credit. Leave it unmet.
            final = driver.view()
            self.assertFalse(final["done"], final)
            self.assertIn(final["status"], ("PAUSED_PLANNING_BUDGET", "PAUSED_PROGRESSIVE_BUDGET"), final)
            self.assertEqual([row["slice_id"] for row in final["progressive"]["demonstrated_slices"]], ["S1", "S2a"])
            pools = final["progressive"]["allowance_usage"]["pools"]
            self.assertEqual(len(pools), 2)
            self.assertTrue(all(pool["review_limit"] == 2 and pool["reviews_used"] == 2 for pool in pools.values()))
            self.assertTrue(all(pool["seconds_limit"] == 5400 for pool in pools.values()))
            self.assertFalse(final["progressive"]["current_whole_product_proof"]["verified"])

    def test_future_reorder_preserves_coverage_token_and_obligations(self):
        with tempfile.TemporaryDirectory(prefix="progressive-reorder-") as directory:
            scenario, driver = self.driver(directory, "progressive_reorder")
            statuses = list(self.stage_statuses(scenario, driver))
            approved = [status for status in statuses if (status["view"].get("progressive") or {}).get("delegation_approved")]
            initial = approved[0]
            self.assertEqual([row["id"] for row in initial["view"]["progressive"]["tentative_next_work"]], ["S2", "S3"])
            final = statuses[-1]
            self.assertTrue(final["completion_current"], final["view"])
            self.assertEqual([row["slice_id"] for row in final["view"]["progressive"]["demonstrated_slices"]],
                             ["S1", "S3", "S2"])
            self.assertTrue(all(status["contract_token"] == initial["contract_token"] for status in approved))
            self.assertEqual(sum(step["kind"] == "approve-plan" for step in driver.steps), 1)
            s3 = next(status["view"] for status in statuses if len(
                (status["view"].get("progressive") or {}).get("demonstrated_slices", [])) == 2)
            self.assertFalse(s3["done"])
            self.assertEqual([row["id"] for row in s3["progressive"]["tentative_next_work"]], ["S2"])
            self.assertFalse(s3["progressive"]["current_whole_product_proof"]["verified"])
            self.assertEqual({row["id"] for row in s3["progressive"]["required_checks"]}, {"A", "B"})
            retained = final["view"]["progressive"]["required_checks"]
            self.assertTrue({"A", "B", "PRODUCT"} <= {row["id"] for row in retained})
            self.assertIn("python3 -m unittest test_journey", [row["command"] for row in
                          final["view"]["evidence"]["check_replay"]["checks"]])
            usage = final["view"]["progressive"]["allowance_usage"]
            self.assertEqual(len(usage["pools"]), 2)
            self.assertTrue(all(pool["reviews_used"] == 2 and pool["review_limit"] == 2 and
                                pool["seconds_limit"] == 5400 for pool in usage["pools"].values()))
            self.assertTrue(all(row.ok for row in scenario.oracle()(driver.project, scenario)))

    def test_initial_task_and_future_map_cannot_bypass_slice_authority(self):
        for fault in ("progressive_initial_future_task", "progressive_initial_broad_task",
                      "progressive_undetailed_future", "progressive_malformed_future"):
            with self.subTest(fault=fault), tempfile.TemporaryDirectory(prefix="progressive-initial-") as directory:
                scenario, driver = self.driver(directory, fault)
                try:
                    final = driver.drive(scenario.brief)
                except DriveError as error:
                    self.assertIn(fault, ("progressive_initial_future_task", "progressive_initial_broad_task"))
                    expected = ("initial_task validation must belong to the concrete first slice" if
                                fault == "progressive_initial_future_task" else "exceeds active slice writable ownership")
                    self.assertIn(expected, str(error))
                    final = driver.view()
                self.assertFalse(final["done"], final)
                self.assertFalse((final.get("progressive") or {}).get("demonstrated_slices"), final)
                for path in ("lessons.py", "progress.py"):
                    self.assertEqual((driver.project / path).read_text(), (scenario.seed / path).read_text(),
                                     f"{fault} dispatched source-writing work")
                self.assertFalse(any("Builder started:" in step["stderr_tail"] for step in driver.steps), driver.steps)

    def test_s1_open_restart_s2_complete(self):
        with tempfile.TemporaryDirectory(prefix="progressive-cli-") as directory:
            scenario, driver = self.driver(directory)
            # An intentional operator stage checkpoint is not failure recovery.
            driver.call("start", "--pause-after-stage", task=scenario.brief)
            driver.run_dir = next((driver.project / ".autocode" / "runs").iterdir())
            run_dir = driver.run_dir
            boundary = None
            for _ in range(30):
                view = driver.view()
                progressive = view.get("progressive") or {}
                demonstrated = progressive.get("demonstrated_slices") or []
                if demonstrated:
                    boundary = view
                    break
                self.assertFalse(view["done"], "The public view never exposed the verified S1 boundary")
                need = view["needs"]
                if need["kind"] == "approve_plan":
                    driver.serve(need)
                elif need["kind"] == "resume":
                    self.assertEqual(view["status"], "PAUSED_REQUESTED", view)
                    driver.call("operator-checkpoint-resume", "--resume-paused", "--pause-after-stage")
                elif need["kind"] == "continue":
                    driver.call("resume", "--pause-after-stage")
                else:
                    self.fail(f"Unexpected need before S1 boundary: {view}")
            self.assertIsNotNone(boundary, "S1 never reached an independently verified boundary")
            self.assertFalse(boundary["done"])
            progressive = boundary["progressive"]
            self.assertIn("C1", [row["id"] for row in progressive["outstanding_product_criteria"]])
            self.assertFalse(progressive["current_whole_product_proof"]["verified"])
            usage = progressive["allowance_usage"]
            self.assertEqual(usage["run_limit"], 43200)
            self.assertEqual(usage["defaults"], {"reviews": 2, "local_seconds": 5400, "run_seconds": 43200})
            initial_pool = next(iter(usage["pools"].values()))
            self.assertEqual(initial_pool["reviews_used"], 2)
            self.assertEqual(initial_pool["review_limit"], 2)
            self.assertEqual(initial_pool["seconds_limit"], 5400)
            self.assertNotEqual(boundary["evidence"]["acceptance"][0]["status"], "verified")
            self.assertEqual(command([sys.executable, "-m", "unittest", "test_journey.Skeleton"],
                                     driver.project).returncode, 0)
            self.assertNotEqual(command([sys.executable, "-m", "unittest", "test_journey.Product"],
                                        driver.project).returncode, 0)
            # Construct a fresh driver, preserving only the public run directory.
            restarted = Driver(driver.project, driver.root, driver.flags, driver.env,
                               autocode=default_autocode(), max_steps=60, timeout_seconds=180)
            restarted.run_dir = run_dir
            if restarted.view()["status"] == "PAUSED_REQUESTED":
                restarted.call("operator-checkpoint-resume", "--resume-paused")
            final = restarted.until_stopped()
            self.assertEqual(restarted.run_dir, run_dir)
            self.assertTrue(final["done"], final)
            self.assertTrue(final["progressive"]["current_whole_product_proof"]["verified"])
            self.assertTrue(all(row.ok for row in scenario.oracle()(driver.project, scenario)))
            self.assertEqual(self.observed_stages(final).count("builder"), 2)

    def test_honest_regression_repaired_without_paused_resume(self):
        with tempfile.TemporaryDirectory(prefix="progressive-regression-") as directory:
            scenario, driver = self.driver(directory, "progressive_regression")
            final = driver.drive(scenario.brief)
            self.assertTrue(final["done"], final)
            names = self.observed_stages(final)
            self.assertIn("resolver", names)
            self.assertGreaterEqual(names.count("builder"), 3)
            self.assertGreaterEqual(names.count("validator"), 3)
            reports = [json.loads(path.read_text()) for path in
                       driver.run_dir.glob("iterations/*/validator-[0-9][0-9].json")]
            failures = [report for report in reports if report.get("verdict") == "FAIL"]
            self.assertTrue(failures, reports)
            checks = {row["command"]: row["exit_code"] for row in failures[0]["checks"]}
            self.assertNotEqual(checks["python3 -m unittest test_journey.Skeleton"], 0)
            self.assertEqual(checks["python3 -m unittest test_journey.Recommendation"], 0)
            self.assertNotEqual(checks["python3 -m unittest test_journey"], 0)
            self.assertFalse(any("--resume-paused" in step["args"] for step in driver.steps))
            self.assertTrue(all(row.ok for row in scenario.oracle()(driver.project, scenario)))

    def test_fabricated_pass_cannot_advance(self):
        with tempfile.TemporaryDirectory(prefix="progressive-fabricated-") as directory:
            scenario, driver = self.driver(directory, "progressive_fabricated_pass")
            final = driver.drive(scenario.brief)
            self.assertFalse(final["done"], final)
            self.assertEqual(self.observed_stages(final).count("builder"), 2)
            self.assertEqual(command([sys.executable, "-m", "unittest", "test_journey.Skeleton"],
                                     driver.project).returncode, 1)
            # Rejected PASS is not applied as an accepted Validator FAIL. Inspect
            # the runner's saved independent replay evidence after it stopped.
            receipts = [json.loads(path.read_text()) for path in
                        (driver.run_dir / "check-replay").glob("*/replay.json")]
            failed = [row for row in receipts if row.get("verdict") == "FAIL"]
            self.assertTrue(failed, receipts)
            self.assertTrue(any("test_journey.Skeleton" in row["command"] and row["exit_code"] != 0
                                for result in failed for row in result["checks"]), failed)
            self.assertNotEqual((final["evidence"].get("check_replay") or {}).get("source_revision"),
                                failed[-1]["source_revision"])
            self.assertFalse(final["progressive"]["current_whole_product_proof"]["verified"])

    def test_missing_required_check_cannot_advance(self):
        with tempfile.TemporaryDirectory(prefix="progressive-missing-") as directory:
            scenario, driver = self.driver(directory, "progressive_missing_check")
            final = driver.drive(scenario.brief)
            self.assertFalse(final["done"], final)
            self.assertEqual(self.observed_stages(final).count("builder"), 2)
            self.assertFalse(final["progressive"]["current_whole_product_proof"]["verified"])

    def test_source_changed_during_review_cannot_dispatch(self):
        with tempfile.TemporaryDirectory(prefix="progressive-stale-") as directory:
            scenario, driver = self.driver(directory, "progressive_stale_source")
            final = driver.drive(scenario.brief)
            self.assertFalse(final["done"], final)
            self.assertEqual(self.observed_stages(final).count("builder"), 1)
            self.assertIn("S2", str(final["progressive"]["tentative_next_work"]))


if __name__ == "__main__":
    unittest.main()
