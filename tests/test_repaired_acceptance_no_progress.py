"""Bound recovery and settlement when a repaired acceptance proof meets an old Builder no-progress hold."""

import copy
import json
import shlex
import subprocess
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import autocode as runner
import autocode_acceptance_tests as acceptance_tests
import autocode_acceptance_tests_job as acceptance_job
import autocode_acceptance_verification_retry as acceptance_retry
import autocode_contract_identity as identity
import autocode_no_progress_recovery as no_progress_recovery
import autocode_recovery_history as recovery_history
import autocode_resolver_human as human
import autocode_run_status as run_status
import autocode_run_view as run_view
import autocode_source_scope as source_scope
import autocode_stop as stop
import autocode_support as support
import autocode_util as util
from goal_fixtures import body as fixture_body

from . import test_no_progress_recovery, test_subprocess


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _runtime(run_dir: Path, *, stamp: str = "2026-11-14T11:00:00+00:00"):
    counter = {"n": 0}

    def now():
        counter["n"] += 1
        return stamp if counter["n"] == 1 else f"2026-11-14T11:00:{counter['n']:02d}+00:00"

    return SimpleNamespace(
        now=now,
        write_json=util.atomic_json,
    )


def _seed_approved_contract() -> tuple[dict, dict]:
    contract = {"task_id": "T1", "revision": 1, "body": fixture_body()}
    contract["hash"] = util.digest({key: contract[key] for key in ("task_id", "revision", "body")})
    approval = {"kind": "goal_approval", "actor": "user_cli", "token": identity.token(contract)}
    contract.update(approval_status="approved", approval_event=approval, origin="planner_draft")
    return contract, approval


def _builder_row(index: int, *, output_dir: Path, contract_hash: str, task_id: str = "T1") -> dict:
    return {
        "stage": "builder",
        "role": "builder",
        "accounted": True,
        "exit_code": 0 if index == 1 else 1,
        "interrupted": index > 1,
        "changed_files": [],
        "contract_hash": contract_hash,
        "task_id": task_id,
        "output": str(output_dir / f"builder-{index}.json"),
        "finished_at": f"2026-11-14T10:0{index}:00+00:00",
    }


def _seed_acceptance_proof(
    state: dict,
    workspace: Path,
    run_dir: Path,
    *,
    with_retry_record: bool = True,
    blind: bool = False,
    insert_writer_at: int | None = None,
    writer_task_id: str = "T1",
    writer_finished_at: str = "2026-11-14T10:15:00+00:00",
) -> dict:
    tests_dir = workspace / "tests"
    tests_dir.mkdir(exist_ok=True)
    test_file = tests_dir / "test_c1.py"
    content = b"def test_c1_greeting():\n    assert True\n"
    archive_dir = run_dir / "acceptance-archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    archive_file = archive_dir / "test_c1.py"
    archive_file.write_bytes(content)

    before_snap = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)
    before_ref = run_dir / "acceptance-writer.before.json"
    util.atomic_json(before_ref, before_snap)

    test_file.write_bytes(content)
    snap = source_scope.snapshot(workspace, state, base_snapshot=support.snapshot)
    after_ref = run_dir / "acceptance-writer.after.json"
    util.atomic_json(after_ref, snap)

    if blind:
        state["settings"][acceptance_tests.SETTING_BLIND] = True
        if test_file.exists():
            test_file.unlink()
        if not any(tests_dir.iterdir()):
            tests_dir.rmdir()

    contract_hash = state["goal_contract"]["hash"]
    value = {
        "summary": "Prepared independent C1 test",
        "tests": [{"case_id": "C1", "file": "tests/test_c1.py", "test_name": "test_c1_greeting"}],
        "already_passing": [],
        "cannot_write": [],
        "tests_run": [],
    }
    artifacts = {
        "output": run_dir / "acceptance-writer.output.json",
        "events": run_dir / "acceptance-writer.events.jsonl",
        "schema": run_dir / "acceptance-writer.schema.json",
        "before_ref": before_ref,
        "after_ref": after_ref,
    }
    util.atomic_json(artifacts["output"], value)
    artifacts["events"].write_text('{"type":"text"}\n', encoding="utf-8")
    util.atomic_json(artifacts["schema"], acceptance_job.SCHEMA)

    writer_row = {
        "stage": acceptance_tests.STAGE,
        "role": "tester",
        "accounted": True,
        "exit_code": 0,
        "changed_files": ["tests/test_c1.py"],
        "contract_hash": contract_hash,
        "task_id": writer_task_id,
        "source_revision": snap["revision"],
        "finished_at": writer_finished_at,
        **{key: str(path) for key, path in artifacts.items()},
    }
    if insert_writer_at is None:
        state["stages"].append(writer_row)
    else:
        state["stages"].insert(insert_writer_at, writer_row)

    cases = [{"id": "C1", "criterion": state["goal_contract"]["body"]["acceptance_criteria"][0]["criterion"]}]
    bkey = acceptance_tests.batch_key(state, cases)
    file_sha = util.file_hash(archive_file)
    receipt = {
        "contract_hash": contract_hash,
        "source_revision": snap["revision"],
        "output": str(artifacts["output"]),
        "case_ids": ["C1"],
        "files": {"tests/test_c1.py": {"sha256": file_sha, "archive": str(archive_file)}},
    }
    acc_state = state.setdefault(acceptance_tests.KEY, {"receipts": {}})
    acc_state.setdefault("receipts", {})[bkey] = receipt
    if with_retry_record:
        pins = {str(path): util.file_hash(path) for path in artifacts.values()}
        acc_state["verification_retries"] = [
            {
                "record": copy.deepcopy(writer_row),
                "value": copy.deepcopy(value),
                "pins": pins,
                "attempts": 1,
                "failures": [{"at": "2026-11-14T10:14:00+00:00", "reason": "Original reference launch failed"}],
            }
        ]
    return {"batch_key": bkey, "receipt": receipt, "writer_row": writer_row, "artifacts": artifacts}


class RepairedAcceptanceNoProgressUnitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="no-progress-repaired-")
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name).resolve()
        self.workspace = root / "project"
        self.run_dir = root / "run"
        self.workspace.mkdir()
        self.run_dir.mkdir()
        _git(self.workspace, "init", "-q")
        _git(
            self.workspace,
            "-c",
            "user.name=Fixture",
            "-c",
            "user.email=fixture@example.test",
            "commit",
            "--allow-empty",
            "-qm",
            "base",
        )
        self.contract, self.approval = _seed_approved_contract()
        self.state = {
            "task": "Build a greeting CLI",
            "workspace": str(self.workspace),
            "run_dir": str(self.run_dir),
            "status": "RUNNING",
            "phase": "EXECUTING",
            "next_stage": "builder",
            "iteration": 5,
            "no_progress_batches": 3,
            "goal_contract": self.contract,
            "user_events": [self.approval],
            "current_task": {"id": "T1", "kind": "implement", "objective": "Build greeting"},
            "settings": {
                "spec_first": True,
                "limits": {"no_progress_batches": 3, "max_cost_usd": 10.0},
                "budget_origins": {"no_progress_batches": "runner_default"},
            },
            "route_assignments": {"builder": {"model": "openai/gpt-5", "via": "configured"}},
            "stages": [
                _builder_row(i, output_dir=self.run_dir, contract_hash=self.contract["hash"]) for i in (1, 2, 3)
            ],
            "history": [],
        }

    def test_repaired_acceptance_retry_grants_one_bounded_attempt_without_machinery_question(self):
        seeded = _seed_acceptance_proof(self.state, self.workspace, self.run_dir, with_retry_record=True)
        runtime = _runtime(self.run_dir)
        prior_stages = copy.deepcopy(self.state["stages"])
        prior_routes = copy.deepcopy(self.state["route_assignments"])

        self.assertTrue(no_progress_recovery.recover(runtime, self.state, self.run_dir, self.workspace))
        self.assertEqual(4, self.state["settings"]["limits"]["no_progress_batches"])
        self.assertEqual("resolver_delegated", self.state["settings"]["budget_origins"]["no_progress_batches"])
        self.assertEqual(3, self.state["no_progress_batches"])
        self.assertEqual(self.contract, self.state["goal_contract"])
        self.assertEqual(prior_routes, self.state["route_assignments"])
        self.assertEqual(10.0, self.state["settings"]["limits"]["max_cost_usd"])
        self.assertEqual(prior_stages, self.state["stages"][:-1])
        self.assertIsNone(human.current(self.state))

        receipt_file = self.run_dir / "resolver" / "no-progress-proof-recovery-1.json"
        self.assertTrue(receipt_file.is_file())
        saved_receipt = json.loads(receipt_file.read_text(encoding="utf-8"))
        self.assertEqual(seeded["batch_key"], saved_receipt["receipt"]["evidence"]["batch_key"])
        self.assertEqual("repaired_acceptance_no_progress", saved_receipt["receipt"]["scope"])

        history_entries = recovery_history.project(self.state)
        self.assertTrue(any(row["headline"] == "Resolver: carry on" for row in history_entries))
        for entry in history_entries:
            self.assertNotIn("--no-progress-limit", entry.get("action") or "")
            self.assertNotIn("--resolver-token", entry.get("action") or "")

    def test_anti_replay_and_exhaustion_after_spent_grant(self):
        _seed_acceptance_proof(self.state, self.workspace, self.run_dir, with_retry_record=True)
        runtime = _runtime(self.run_dir)
        self.assertTrue(no_progress_recovery.recover(runtime, self.state, self.run_dir, self.workspace))

        # The spent proof must never authorize a second grant even if count climbs to the new limit
        # before another Builder runs.
        replay_probe = copy.deepcopy(self.state)
        replay_probe["no_progress_batches"] = 4
        proof = no_progress_recovery.authenticated_intervening_proof(replay_probe, self.workspace)
        self.assertIsNotNone(proof)
        self.assertTrue(no_progress_recovery.proof_already_spent(replay_probe, proof))
        self.assertFalse(no_progress_recovery.recover(runtime, replay_probe, self.run_dir, self.workspace))
        self.assertEqual(4, replay_probe["settings"]["limits"]["no_progress_batches"])

        # After the 4th Builder attempt also makes no progress, no safe recovery remains: settle to could_not_finish.
        self.state["stages"].append(_builder_row(4, output_dir=self.run_dir, contract_hash=self.contract["hash"]))
        self.state["no_progress_batches"] = 4
        self.assertIsNone(no_progress_recovery.authenticated_intervening_proof(self.state, self.workspace))
        self.assertFalse(no_progress_recovery.recover(runtime, self.state, self.run_dir, self.workspace))
        self.assertEqual(4, self.state["settings"]["limits"]["no_progress_batches"])

        err = support.Paused("PAUSED_NO_PROGRESS", "Repeated unchanged implementation batches require review")
        self.assertTrue(no_progress_recovery.conclude_exhausted(runtime, self.state, self.run_dir, self.workspace, err))
        self.assertEqual("PAUSED_INTERVENTION", self.state["status"])
        self.assertTrue(self.state["stop_reason"].startswith(run_status.ENDED_PREFIX))
        outcome = stop.outcome(self.state)
        self.assertIsNotNone(outcome)
        self.assertEqual("could_not_finish", outcome["result"])
        self.assertIsNone(outcome["evidence"]["spent_proof_digest"])
        self.assertEqual(["no-progress-proof-recovery-1"], outcome["evidence"]["prior_proof_recoveries"])

        # When conclude_exhausted runs while an already-spent intervening proof is present, it records its digest.
        err_replay = support.Paused("PAUSED_NO_PROGRESS", "Repeated unchanged implementation batches require review")
        self.assertTrue(
            no_progress_recovery.conclude_exhausted(runtime, replay_probe, self.run_dir, self.workspace, err_replay)
        )
        self.assertEqual(proof["proof_digest"], stop.outcome(replay_probe)["evidence"]["spent_proof_digest"])

        view = run_view.view(self.state, run_dir=self.run_dir, workspace=self.workspace)
        self.assertIsNone(view["next_command"])
        self.assertEqual("could_not_finish", view["recovery"]["outcome"]["result"])

    def test_stale_invalid_and_absent_evidence_refused_and_settled(self):
        runtime = _runtime(self.run_dir)

        def assert_refused_and_settled(mutated: dict, label: str):
            with self.subTest(case=label):
                self.assertIsNone(no_progress_recovery.authenticated_intervening_proof(mutated, self.workspace))
                self.assertFalse(no_progress_recovery.recover(runtime, mutated, self.run_dir, self.workspace))
                self.assertTrue(
                    no_progress_recovery.conclude_exhausted(
                        runtime,
                        mutated,
                        self.run_dir,
                        self.workspace,
                        support.Paused(
                            "PAUSED_NO_PROGRESS", "Repeated unchanged implementation batches require review"
                        ),
                    )
                )
                self.assertEqual("could_not_finish", stop.outcome(mutated)["result"])

        # 1. No acceptance proof at all.
        assert_refused_and_settled(copy.deepcopy(self.state), "missing_proof")

        # 2. Writer ran before the trailing Builder streak.
        before_builder = copy.deepcopy(self.state)
        _seed_acceptance_proof(before_builder, self.workspace, self.run_dir, insert_writer_at=0)
        assert_refused_and_settled(before_builder, "writer_before_builders")

        # Reset clean base with valid intervening proof to mutate one field per case.
        base_with_proof = copy.deepcopy(self.state)
        seeded = _seed_acceptance_proof(base_with_proof, self.workspace, self.run_dir, with_retry_record=True)

        # 3. Receipt contract_hash mismatch.
        wrong_contract = copy.deepcopy(base_with_proof)
        wrong_contract[acceptance_tests.KEY]["receipts"][seeded["batch_key"]]["contract_hash"] = "other-hash"
        assert_refused_and_settled(wrong_contract, "contract_hash_mismatch")

        # 4. Writer task_id mismatch.
        wrong_task = copy.deepcopy(base_with_proof)
        wrong_task["stages"][-1]["task_id"] = "OTHER-TASK"
        assert_refused_and_settled(wrong_task, "task_id_mismatch")

        # 5. Tampered #3517 verification_retries pin hash.
        bad_pin = copy.deepcopy(base_with_proof)
        bad_pin[acceptance_tests.KEY]["verification_retries"][0]["pins"][str(seeded["artifacts"]["output"])] = "bad"
        assert_refused_and_settled(bad_pin, "tampered_retry_pin")

        # 6. Pending verification_retry still open.
        pending_retry = copy.deepcopy(base_with_proof)
        pending_retry[acceptance_tests.KEY][acceptance_retry.KEY] = {"attempts": 0}
        assert_refused_and_settled(pending_retry, "open_verification_retry")

        # 7. Orchestration history finished after writer.
        newer_batch = copy.deepcopy(base_with_proof)
        newer_batch["orchestration_history"] = [{"finished_at": "2026-11-14T10:20:00+00:00"}]
        assert_refused_and_settled(newer_batch, "orchestration_after_writer")

        # 8. Workspace test file drift after writer finished.
        drifted = copy.deepcopy(base_with_proof)
        (self.workspace / "tests/test_c1.py").write_text("def test_c1_greeting():\n    assert False\n")
        assert_refused_and_settled(drifted, "workspace_source_drift")

    def test_blind_tests_intervening_proof_authentication_and_tamper_refusal(self):
        blind_state = copy.deepcopy(self.state)
        seeded = _seed_acceptance_proof(blind_state, self.workspace, self.run_dir, blind=True)
        runtime = _runtime(self.run_dir)

        # Tampered archive SHA-256 fails.
        tampered = copy.deepcopy(blind_state)
        tampered[acceptance_tests.KEY]["receipts"][seeded["batch_key"]]["files"]["tests/test_c1.py"]["sha256"] = (
            "0" * 64
        )
        self.assertIsNone(no_progress_recovery.authenticated_intervening_proof(tampered, self.workspace))

        # Drifted workspace relative to before_ref fails.
        (self.workspace / "drift.py").write_text("x = 1\n")
        self.assertIsNone(no_progress_recovery.authenticated_intervening_proof(blind_state, self.workspace))
        (self.workspace / "drift.py").unlink()

        # Exposed blind test file on workspace disk fails.
        (self.workspace / "tests").mkdir(exist_ok=True)
        (self.workspace / "tests/test_c1.py").write_text("leaked")
        self.assertIsNone(no_progress_recovery.authenticated_intervening_proof(blind_state, self.workspace))
        (self.workspace / "tests/test_c1.py").unlink()
        (self.workspace / "tests").rmdir()

        # Intact blind archive + matching before_ref authenticates and grants 1 bounded attempt.
        proof = no_progress_recovery.authenticated_intervening_proof(blind_state, self.workspace)
        self.assertIsNotNone(proof)
        self.assertTrue(no_progress_recovery.recover(runtime, blind_state, self.run_dir, self.workspace))
        self.assertEqual(4, blind_state["settings"]["limits"]["no_progress_batches"])

    def test_user_explicit_limit_and_unsettled_ownership_guards(self):
        runtime = _runtime(self.run_dir)
        explicit = copy.deepcopy(self.state)
        _seed_acceptance_proof(explicit, self.workspace, self.run_dir)
        explicit["settings"]["budget_origins"]["no_progress_batches"] = "user_explicit"
        self.assertIsNone(no_progress_recovery.default_builder_hold(explicit))
        self.assertFalse(no_progress_recovery.recover(runtime, explicit, self.run_dir, self.workspace))
        self.assertFalse(
            no_progress_recovery.conclude_exhausted(
                runtime,
                explicit,
                self.run_dir,
                self.workspace,
                support.Paused("PAUSED_NO_PROGRESS", "Repeated unchanged implementation batches require review"),
            )
        )
        explicit["status"] = "PAUSED_NO_PROGRESS"
        self.assertIsNone(no_progress_recovery.pending_legacy(explicit))

        with_proof = copy.deepcopy(self.state)
        _seed_acceptance_proof(with_proof, self.workspace, self.run_dir)
        for field, value in (
            ("active_stage", {"stage": "builder"}),
            ("uncertain_artifacts", [{"path": "x"}]),
            ("pending_report_repair", {"original": {"stage": "builder"}}),
            ("active_runner_check", {"stage": "verify"}),
        ):
            with self.subTest(unsettled=field):
                blocked = copy.deepcopy(with_proof)
                blocked[field] = value
                self.assertFalse(no_progress_recovery.recover(runtime, blocked, self.run_dir, self.workspace))
                self.assertEqual(3, blocked["settings"]["limits"]["no_progress_batches"])

        # Filesystem queue/pause gates block recovery and exhaustion.
        pause_file = self.run_dir / "pause-requested"
        pause_file.write_text("pause\n")
        self.assertFalse(no_progress_recovery.recover(runtime, copy.deepcopy(with_proof), self.run_dir, self.workspace))
        pause_file.unlink()

        inbox = self.run_dir / "interventions.json"
        inbox.write_text(
            json.dumps(
                {
                    "version": 1,
                    "requests": [
                        {
                            "id": "int-1",
                            "kind": "pause",
                            "text": "hold",
                            "order": 1,
                            "submitted_at": "2026-11-14T10:30:00+00:00",
                            "observed_goal_token": None,
                            "boundary_pause_requested": True,
                        }
                    ],
                }
            ),
            encoding="utf-8",
        )
        self.assertIn("pending_interruption:pending", no_progress_recovery.unsettled_reasons(with_proof, self.run_dir))
        self.assertFalse(no_progress_recovery.recover(runtime, copy.deepcopy(with_proof), self.run_dir, self.workspace))


class RepairedAcceptanceNoProgressCLITests(unittest.TestCase):
    setUp = test_subprocess.SubprocessFlow.setUp
    launch = test_subprocess.SubprocessFlow.launch
    saved = test_subprocess.SubprocessFlow.saved
    new_run_engine_args = ("--engine", "codex")
    stop_before_builder = test_no_progress_recovery.NoProgressBoundTests.stop_before_builder

    def _seed_legacy_waiting_hold(self, *, with_proof: bool) -> tuple[dict, dict]:
        state = self.stop_before_builder()
        state["settings"].setdefault("budget_origins", {})["no_progress_batches"] = "runner_default"
        state["settings"].setdefault("limits", {})["no_progress_batches"] = 3
        state["settings"]["spec_first"] = True
        state["no_progress_batches"] = 3
        contract_hash = state["goal_contract"]["hash"]
        task_id = state["current_task"]["id"]
        state["stages"] = [
            *state.get("stages", []),
            *(_builder_row(i, output_dir=self.run, contract_hash=contract_hash, task_id=task_id) for i in (1, 2, 3)),
        ]
        if with_proof:
            _seed_acceptance_proof(state, self.project, self.run, with_retry_record=True, writer_task_id=task_id)

        binding = {**human._binding(state), "runtime_sources": "previous-runtime"}
        with patch.object(human, "_binding", return_value=binding):
            self.assertTrue(
                runner.resolver_runtime.record_operational_exhaustion(
                    runner,
                    state,
                    self.run,
                    support.Paused("PAUSED_NO_PROGRESS", "Repeated unchanged implementation batches require review"),
                )
            )
            self.assertEqual("escalate", human.evaluate(state))
        (self.run / "state.json").write_text(json.dumps(state), encoding="utf-8")
        retained_req = human.retained(state)
        self.assertIsNotNone(retained_req)
        return state, retained_req

    def test_pre_fix_waiting_checkpoint_with_repaired_proof_resumes_past_hold(self):
        _, legacy_request = self._seed_legacy_waiting_hold(with_proof=True)

        before = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]
        self.assertEqual({"kind": "continue", "action": "--resume-paused"}, before["needs"])
        self.assertIsNone(before["chat"]["gate"])
        self.assertIsNone(before["chat"]["input"])
        self.assertIn("settle the retained no-progress hold", before["stop_reason"])
        self.assertNotIn("--no-progress-limit", before["stop_reason"])
        self.assertNotIn("--resolver-token", before["explanation"]["explanation"])
        self.assertIn("automatically", before["explanation"]["what_it_means"])
        self.assertIn("--resume-paused", before["next_command"])

        # Running the view's named --resume-paused command reconciles the old machinery question
        # and grants 3 -> 4 at the Builder boundary without asking the user.
        self.launch([*shlex.split(before["next_command"])[1:], "--pause-after-stage"], 2)
        _, after = self.saved()
        self.assertEqual(4, after["settings"]["limits"]["no_progress_batches"])
        self.assertEqual("resolver_delegated", after["settings"]["budget_origins"]["no_progress_batches"])
        escalation = after["resolver"]["human_escalations"][legacy_request["request_id"]]
        self.assertEqual("superseded", escalation["status"])
        self.assertTrue((self.project / "greet.py").is_file())
        self.assertTrue((self.run / "resolver" / "no-progress-proof-recovery-2.json").is_file())

    def test_pre_fix_waiting_checkpoint_without_proof_ends_could_not_finish_without_provider(self):
        self._seed_legacy_waiting_hold(with_proof=False)

        before = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]
        self.assertEqual({"kind": "continue", "action": "--resume-paused"}, before["needs"])
        resumed = self.launch(shlex.split(before["next_command"])[1:], 2)
        after = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]
        self.assertIn(after["stop_reason"], resumed.stdout)
        self.assertEqual("could_not_finish", after["recovery"]["outcome"]["result"])
        self.assertEqual("PAUSED_INTERVENTION", after["status"])
        self.assertIsNone(after["next_command"])
        non_runner = lambda view: [
            row for row in view["usage"]["accounting"]["attempts"] if not row.get("runner_owned")
        ]
        self.assertEqual(non_runner(before), non_runner(after))
        self.assertTrue(after["usage"]["accounting"]["attempts"][-1]["runner_owned"])

        # Re-running --resume-paused stays ended without launching any provider or writing another receipt.
        self.launch(["--run-dir", str(self.run), "--resume-paused", "--no-chat"], 2)
        repeated = json.loads(self.launch(["--run-dir", str(self.run), "--status"], 0).stdout)["view"]
        self.assertEqual(after["recovery"]["outcome"], repeated["recovery"]["outcome"])
        self.assertEqual(after["usage"]["accounting"]["attempts"], repeated["usage"]["accounting"]["attempts"])

    def test_build_loop_paused_no_progress_exhaustion_ends_via_conclude_exhausted(self):
        state = self.stop_before_builder()
        state["settings"].setdefault("budget_origins", {})["no_progress_batches"] = "runner_default"
        state["settings"].setdefault("limits", {})["no_progress_batches"] = 3
        state["no_progress_batches"] = 3
        task_id = state["current_task"]["id"]
        state["stages"] = [
            *state.get("stages", []),
            *(
                _builder_row(i, output_dir=self.run, contract_hash=state["goal_contract"]["hash"], task_id=task_id)
                for i in (1, 2, 3)
            ),
        ]
        support.atomic_json(self.run / "state.json", state)

        resumed = self.launch([*self.args, "--resume-paused"], 2)
        _, ended = self.saved()
        self.assertEqual("PAUSED_INTERVENTION", ended["status"])
        self.assertTrue(ended["stop_reason"].startswith(run_status.ENDED_PREFIX))
        self.assertIn(ended["stop_reason"], resumed.stderr)
        self.assertEqual("could_not_finish", stop.outcome(ended)["result"])
        self.assertIsNone(human.current(ended))


if __name__ == "__main__":
    unittest.main()
