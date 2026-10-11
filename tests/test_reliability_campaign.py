"""Validate a planned, pinned measurement protocol; never execute its campaign."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
import os
import re
import subprocess
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = "8375b73cf965fd67c6c67dec7103125b56fab431"
ARENA = ("sympy-matrix-derivatives", "django-filteredrelation-join-lifecycle", "pytest-stop-fixture-teardown")
SCENARIOS = (
    "bugfix-iso-weeks",
    "feature-timesheet-by-project",
    "greenfield-todo-cli",
    "ladder-14-transactional-csv-import",
    "ladder-17-transaction-ledger",
    "ladder-18-durable-lease-queue",
    "ladder-19-transactional-outbox",
    "ladder-21-event-replay-snapshots",
    "verification-reuse",
    "review-then-fix",
)


def digest(payload):
    return hashlib.sha256(payload).hexdigest()


def pinned_files(document):
    """Read immutable local Git objects, including modes, without checkout or imports.

    CI fetches full history. Later profile/catalogue edits do not rewrite this
    planned protocol: its original revision is the authority, not current files.
    """
    env = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    env.update(GIT_CONFIG_NOSYSTEM="1", GIT_CONFIG_GLOBAL=os.devnull)
    command = ["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0"]
    prefixes = [*document["inputs"], *[f"arena/cases/{name}" for name in ARENA]]
    prefixes += [f"scenarios/catalog/{name}" for name in SCENARIOS]
    tree = subprocess.run(
        [*command, "ls-tree", "-rz", "--full-tree", BASE, "--", *prefixes],
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    entries = []
    for entry in tree.rstrip(b"\0").split(b"\0"):
        metadata, path = entry.split(b"\t", 1)
        mode, kind, oid = metadata.decode().split()
        if kind != "blob" or mode not in ("100644", "100755", "120000"):
            raise ValueError("unsupported pinned input")
        entries.append((path.decode(), mode, oid))
    packed = subprocess.run(
        [*command, "cat-file", "--batch"],
        input="".join(oid + "\n" for _, _, oid in entries).encode(),
        cwd=ROOT,
        env=env,
        check=True,
        capture_output=True,
        timeout=30,
    ).stdout
    cursor, result = 0, {}
    for name, mode, oid in entries:
        end = packed.index(b"\n", cursor)
        actual, kind, size = packed[cursor:end].decode().split()
        if actual != oid or kind != "blob":
            raise ValueError("pinned object mismatch")
        cursor = end + 1
        payload = packed[cursor : cursor + int(size)]
        cursor += int(size)
        if len(payload) != int(size) or packed[cursor : cursor + 1] != b"\n":
            raise ValueError("incomplete pinned object")
        cursor += 1
        result[name] = {"mode": mode, "sha256": digest(payload), "bytes": payload}
    if cursor != len(packed):
        raise ValueError("unexpected pinned data")
    return result


def definition(files, prefix):
    rows = sorted([name, row["mode"], row["sha256"]] for name, row in files.items() if name.startswith(prefix + "/"))
    return {
        "path": prefix,
        "files": len(rows),
        "sha256": digest(json.dumps(rows, separators=(",", ":"), ensure_ascii=True).encode()),
    }


def require(condition, explanation):
    if not condition:
        raise ValueError(explanation)


def validate(document, files):
    """The checked-in declaration's invariants, including adversarial mutations."""
    require(
        set(document)
        == {
            "schema_version",
            "id",
            "status",
            "source_revision",
            "inputs",
            "runtime",
            "profile",
            "repetitions",
            "tasks",
            "schedule",
            "acceptance",
        },
        "unexpected campaign fields or results",
    )
    require(
        type(document["schema_version"]) is int
        and document["schema_version"] == 1
        and document["id"] == "reliability-protocol-v1",
        "unknown protocol",
    )
    require(document["status"] == "PLANNED" and document["source_revision"] == BASE, "not the pinned planned protocol")
    require(
        set(document["inputs"])
        == {
            "arena/catalog.json",
            "docs/arena.md",
            "tools/autocode_arena_policy.py",
            "scenarios/harness/catalog.py",
            "scenarios/harness/profiles.py",
            "scenarios/harness/verdict.py",
            "scenarios/harness/driver.py",
            "scenarios/run.py",
            "tools/autocode_args.py",
            "tools/autocode_run_setup.py",
            "pyproject.toml",
        },
        "evaluation input pin missing",
    )
    for path, pin in document["inputs"].items():
        require(path in files and pin == files[path]["sha256"], "evaluation input changed")
    profile_ast = ast.parse(files["scenarios/harness/profiles.py"]["bytes"])
    profiles = next(
        node.value
        for node in profile_ast.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "PROFILES" for target in node.targets)
    )
    profile = next(
        ast.literal_eval(value)
        for key, value in zip(profiles.keys, profiles.values, strict=True)
        if ast.literal_eval(key) == "build-comparison"
    )
    require(
        document["profile"] == {"name": "build-comparison", "definition": profile},
        "profile or role configuration changed",
    )
    runtime = document["runtime"]
    require(runtime["provider"] == profile["provider"], "provider disagrees with profile")
    project = tomllib.loads(files["pyproject.toml"]["bytes"].decode())
    require(runtime["autocode_version"] == project["project"]["version"], "package version mismatch")
    for field in ("python", "psutil", "provider_version"):
        require(
            isinstance(runtime[field], str) and re.fullmatch(r"\d+\.\d+\.\d+", runtime[field]),
            "runtime version must be pinned",
        )
    require(bool(runtime["dependencies"]), "dependency freeze prerequisite missing")
    repetitions = document["repetitions"]
    require(type(repetitions) is int and repetitions == 2, "repetitions must preserve the declared schedule")
    tasks = document["tasks"]
    expected_ids = [*(f"arena/{name}" for name in ARENA), *(f"scenario/{name}" for name in SCENARIOS)]
    require([row["id"] for row in tasks] == expected_ids, "missing, duplicate or undeclared task")
    catalogue = json.loads(files["arena/catalog.json"]["bytes"])["cases"]
    catalog_ast = ast.parse(files["scenarios/harness/catalog.py"]["bytes"])
    constructor = next(
        node
        for node in ast.walk(catalog_ast)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "Scenario"
    )
    defaults = {
        keyword.arg: ast.literal_eval(keyword.value.args[1])
        for keyword in constructor.keywords
        if keyword.arg in ("max_steps", "timeout_minutes", "expected")
    }
    for task in tasks:
        name, kind = task["case_id"], task["kind"]
        require(task["id"] == f"{kind}/{name}", "task identity does not match scheduled case")
        prefix = f"arena/cases/{name}" if kind == "arena" else f"scenarios/catalog/{name}"
        require(task["definition"] == definition(files, prefix), "task definition changed")
        if kind == "arena":
            case = next(row for row in catalogue if row["id"] == name)
            require(
                task["upstream"]
                == {
                    key: case[key]
                    for key in ("repository", "base_commit", "reference_commit", "issue_ref", "split", "checks")
                },
                "upstream task or checks changed",
            )
            require(
                task["limits"] == {"cli_invocation_seconds": 3600, "oracle_seconds": 120, "max_iterations": 3},
                "Arena invocation limits changed",
            )
            require(
                "--timeout 3600 --oracle-timeout 120" in files["docs/arena.md"]["bytes"].decode(),
                "Arena limits no longer match their pinned source",
            )
            require(task["execution_kind"] == "live", "Arena execution mode changed")
        else:
            metadata = tomllib.loads(files[prefix + "/scenario.toml"]["bytes"].decode())
            run = metadata.get("run", {})
            require(
                task["limits"]
                == {
                    "harness_minutes": run.get("timeout_minutes", defaults["timeout_minutes"]),
                    "max_steps": run.get("max_steps", defaults["max_steps"]),
                },
                "original scenario limits changed",
            )
            require(
                task["category"] == metadata["category"]
                and task["expected"] == run.get("expected", defaults["expected"])
                and task["requires_stages"] == run.get("requires_stages", []),
                "scenario acceptance changed",
            )
            require(
                task["execution_kind"] == ("fixture" if name == "verification-reuse" else "live"),
                "scripted interruption must stay fixture evidence",
            )
    schedule = [
        {"id": f"{task}/{repeat:02}", "task": task, "repetition": repeat}
        for task in expected_ids
        for repeat in range(1, repetitions + 1)
    ]
    require(document["schedule"] == schedule, "every task times repetition must be accountable exactly once")
    policy = document["acceptance"]
    for flag in (
        "record_every_scheduled_attempt",
        "publish_nonpass_and_spent_budget",
        "preserve_native_outcome",
        "incomplete_blocks_comparison",
        "unknown_usage_or_cost_is_not_zero",
        "public_historical_cases_are_not_private_holdouts",
    ):
        require(policy[flag] is True, "lost failure, cost or provenance accounting")
    for flag in (
        "arena_stopped_proves_appropriate_stop",
        "fixtures_qualify_live_version",
        "automatic_promotion",
        "statistical_reliability_claim",
    ):
        require(policy[flag] is False, "unsupported qualification claim")
    require(
        policy["missing_usage_and_cost"] is None and policy["unattempted_outcome"] == "DEFERRED",
        "unattempted or unknown data misreported",
    )
    arena_ast = ast.parse(files["tools/autocode_arena_policy.py"]["bytes"])
    arena_outcomes = next(
        ast.literal_eval(node)
        for node in ast.walk(arena_ast)
        if isinstance(node, ast.Tuple)
        and all(isinstance(item, ast.Constant) and isinstance(item.value, str) for item in node.elts)
        and ast.literal_eval(node) == ("PASS", "FALSE_COMPLETE", "STOPPED", "FAIL", "ERROR", "RUNNING")
    )
    scenario_ast = ast.parse(files["scenarios/harness/verdict.py"]["bytes"])
    constants = {
        node.targets[0].id: node.value.value
        for node in scenario_ast.body
        if isinstance(node, ast.Assign)
        and isinstance(node.targets[0], ast.Name)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    }
    require(policy["native_outcomes"]["arena"] == list(arena_outcomes), "Arena outcomes dropped or renamed")
    require(
        policy["native_outcomes"]["scenario"]
        == [
            constants[name]
            for name in (
                "PASS",
                "FALSE_COMPLETE",
                "HONEST_BLOCKER",
                "ERROR",
                "SKIPPED",
                "NOT_EXERCISED",
                "INTERRUPTED_UNGRADED",
                "PENDING_UNGRADED",
            )
        ],
        "scenario outcomes dropped or renamed",
    )
    require(
        policy["delivery_requires"]
        == [
            "public_current_completion",
            "independent_oracle_all_pass",
            "required_flow_reached",
            "current_source_evidence",
            "required_human_reviews",
        ],
        "delivery evidence weakened",
    )
    require(
        set(policy["required_attempt_fields"])
        == {
            "schedule_id",
            "execution_kind",
            "source_revision",
            "runtime_identity",
            "actual_models_and_efforts",
            "native_status",
            "native_outcome",
            "oracle_checks",
            "evidence_refs",
            "interventions_decision_or_repair",
            "elapsed_seconds",
            "usage",
            "estimated_usd",
            "spent_budget",
            "reason",
        },
        "attempt evidence or spent cost omitted",
    )


class ReliabilityCampaignTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.document = json.loads((ROOT / "arena/campaign.json").read_text())
        cls.files = pinned_files(cls.document)

    def test_planned_manifest_pins_real_tasks_profile_and_original_limits(self):
        validate(self.document, self.files)
        self.assertEqual(13, len(self.document["tasks"]))
        self.assertEqual(26, len(self.document["schedule"]))

    def test_task_body_cannot_be_substituted_under_another_schedule_identity(self):
        for destination, substitute in ((0, 3), (3, 0), (3, 4)):
            document = copy.deepcopy(self.document)
            scheduled_id = document["tasks"][destination]["id"]
            document["tasks"][destination] = copy.deepcopy(document["tasks"][substitute])
            document["tasks"][destination]["id"] = scheduled_id
            with (
                self.subTest(destination=destination, substitute=substitute),
                self.assertRaisesRegex(ValueError, "task identity"),
            ):
                validate(document, self.files)

    def test_missing_duplicate_and_undeclared_scheduled_rows_are_refused(self):
        for change in (
            lambda rows: rows.pop(),
            lambda rows: rows.append(rows[0]),
            lambda rows: rows[0].update(task="missing"),
        ):
            document = copy.deepcopy(self.document)
            change(document["schedule"])
            with self.subTest(schedule=document["schedule"][-1]), self.assertRaisesRegex(ValueError, "accountable"):
                validate(document, self.files)

    def test_repetition_counts_cannot_hide_or_multiply_attempts(self):
        for repetition in (True, 0, -1, 1, 3):
            document = copy.deepcopy(self.document)
            document["repetitions"] = repetition
            with self.subTest(repetition=repetition), self.assertRaisesRegex(ValueError, "repetitions"):
                validate(document, self.files)

    def test_altered_budget_or_total_campaign_reinterpretation_is_refused(self):
        for index, limits in (
            (0, {"campaign_seconds": 3600, "oracle_seconds": 120, "max_iterations": 3}),
            (3, {"harness_minutes": 45, "max_steps": 40}),
        ):
            document = copy.deepcopy(self.document)
            document["tasks"][index]["limits"] = limits
            with self.subTest(index=index), self.assertRaisesRegex(ValueError, "limits"):
                validate(document, self.files)

    def test_definition_or_upstream_revision_drift_is_refused(self):
        for field in ("definition", "upstream"):
            document = copy.deepcopy(self.document)
            document["tasks"][0][field]["sha256" if field == "definition" else "base_commit"] = "0" * (
                64 if field == "definition" else 40
            )
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, "changed"):
                validate(document, self.files)

    def test_pinned_input_or_role_model_drift_is_refused(self):
        for field in ("input", "missing", "model"):
            document = copy.deepcopy(self.document)
            if field == "input":
                document["inputs"]["scenarios/harness/verdict.py"] = "0" * 64
            elif field == "missing":
                document["inputs"].pop("scenarios/harness/driver.py")
            else:
                document["profile"]["definition"]["models"]["validator"] = "invented/model"
            with self.subTest(field=field), self.assertRaises(ValueError):
                validate(document, self.files)

    def test_nonpass_outcomes_and_unknown_costs_cannot_disappear(self):
        for change in (
            lambda policy: policy.update(publish_nonpass_and_spent_budget=False),
            lambda policy: policy.update(missing_usage_and_cost=0),
            lambda policy: policy["native_outcomes"]["scenario"].remove("SKIPPED"),
            lambda policy: policy["native_outcomes"]["arena"].remove("RUNNING"),
            lambda policy: policy["required_attempt_fields"].remove("spent_budget"),
            lambda policy: policy.update(unattempted_outcome="PASS"),
        ):
            document = copy.deepcopy(self.document)
            change(document["acceptance"])
            with self.subTest(policy=document["acceptance"]), self.assertRaises(ValueError):
                validate(document, self.files)

    def test_fixture_interruption_cannot_be_relabeled_as_live(self):
        document = copy.deepcopy(self.document)
        next(task for task in document["tasks"] if task["case_id"] == "verification-reuse")["execution_kind"] = "live"
        with self.assertRaisesRegex(ValueError, "fixture"):
            validate(document, self.files)

    def test_public_holdouts_stops_and_fixture_passes_cannot_make_stronger_claims(self):
        for flag in (
            "public_historical_cases_are_not_private_holdouts",
            "arena_stopped_proves_appropriate_stop",
            "fixtures_qualify_live_version",
            "automatic_promotion",
            "statistical_reliability_claim",
        ):
            document = copy.deepcopy(self.document)
            document["acceptance"][flag] = not document["acceptance"][flag]
            with self.subTest(flag=flag), self.assertRaises(ValueError):
                validate(document, self.files)

    def test_no_results_or_current_completion_are_invented(self):
        for change in (
            lambda document: document.update(results=[{"verdict": "PASS"}]),
            lambda document: document.update(status="COMPLETE"),
        ):
            document = copy.deepcopy(self.document)
            change(document)
            with self.assertRaises(ValueError):
                validate(document, self.files)


if __name__ == "__main__":
    unittest.main()
