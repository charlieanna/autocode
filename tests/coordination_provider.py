#!/usr/bin/env python3
"""Offline model boundary for the real three-repository TUI integration test.

Only provider answers are scripted. Builders write real product/test files;
Testers execute them, and the production proof runner checks the broken base.
"""

import json
import os
import shlex
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from blackbox_build_provider import SOURCE_VERDICT, cite_sources, source_trace_for
from live_fixture_provider import _contract

PRODUCT = {
    "checkout": (
        "client.py",
        "def display(payload):\n    return f\"${payload['amount']:.2f}\"\nHISTORY_REPOSITORY = 'history'\ndef checkout_total(history_client, subtotal):\n    return display(history_client.record(subtotal))\n",
    ),
    "history": (
        "history.py",
        "def serialize(total):\n    return {'amount': total, 'currency': 'USD'}\nTAX_REPOSITORY = 'tax'\nclass HistoryClient:\n    def __init__(self, tax_service):\n        self.tax_service = tax_service\n    def record(self, subtotal):\n        return serialize(self.tax_service.add_tax(subtotal))\n",
    ),
    "tax": ("tax.py", "def add_tax(value):\n    return value * 1.1\n"),
}
ASSERTIONS = {
    "checkout": "from client import display\n        self.assertEqual(display({'amount': 110}), '$110.00')",
    "history": "from history import serialize\n        self.assertEqual(serialize(110), {'amount': 110, 'currency': 'USD'})",
    "tax": "from tax import add_tax\n        self.assertAlmostEqual(add_tax(100), 110)",
}
JOINT_TEST = """import importlib.util
import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPOS = json.loads((ROOT / 'repositories.json').read_text())
def module(repo, name):
    path = ROOT / REPOS[repo]['path'] / (name + '.py')
    spec = importlib.util.spec_from_file_location(name, path)
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded

class JointRegression(unittest.TestCase):
    def test_checkout_total_through_real_tax_history_and_checkout(self):
        history = module('history', 'history').HistoryClient(module('tax', 'tax'))
        result = module('checkout', 'client').checkout_total(history, 100)
        self.assertEqual(result, '$110.00')
"""
JOINT_SETUP = "def pytest_configure(config):\n    config.option.importmode = 'importlib'\n"
SUITE = "python3 -m unittest discover -s tests -v"
TEST_PREAMBLE = "import unittest\nimport sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).resolve().parents[1]))\n\nclass Behavior(unittest.TestCase):\n    def test_T1_expected_behavior(self):\n        "


def default(schema):
    if "const" in schema:
        return schema["const"]
    kind = schema.get("type")
    if isinstance(kind, list):
        if "null" in kind:
            return None
        kind = kind[0]
    if "enum" in schema:
        return schema["enum"][0]
    if kind == "object":
        return {key: default(schema["properties"][key]) for key in schema.get("required", [])}
    return {"array": [], "string": "", "boolean": False, "integer": 0, "number": 0, "null": None}.get(kind)


def shaped(value, schema):
    """Recorded fixture answers use exactly the active protocol's field names."""
    if isinstance(value, dict) and schema.get("type") == "object":
        fields = schema.get("properties", {})
        return {
            key: shaped(value.get(key, default(fields[key])), fields[key])
            for key in fields
            if key in value or key in schema.get("required", [])
        }
    if isinstance(value, list) and schema.get("type") == "array":
        return [shaped(row, schema.get("items", {})) for row in value]
    return value


def main():
    global SUITE
    if sys.argv[1:3] == ["sandbox", "--help"]:
        print("--config -- macos linux")
        return
    if sys.argv[1:2] == ["sandbox"]:
        raise SystemExit(subprocess.call(sys.argv[sys.argv.index("--") + 1 :]))
    if sys.argv[1:] == ["login", "status"]:
        print("Logged in using ChatGPT (offline coordination fixture)")
        return
    if sys.argv[1:] == ["--version"]:
        print("codex-cli fixture")
        return
    prompt = sys.stdin.read()
    data = json.loads(prompt.split("CURRENT HANDOFF DATA\n", 1)[1])
    stage = data.get("stage", "")
    if data.get("report_repair"):
        stage = data["original"]["stage"]
    schema = json.loads(Path(sys.argv[sys.argv.index("--output-schema") + 1]).read_text())
    output = Path(sys.argv[sys.argv.index("-o") + 1])
    root = Path.cwd()
    joint = (root / "repositories.json").is_file()
    if joint:
        SUITE = "python3 -m pytest -q"
    name = "joint" if joint else json.loads((root / "repository.json").read_text())["name"]
    if (
        os.environ.get("COORDINATION_FIXTURE_MEMBER_CONTEXT")
        and root.parent.name == "worktrees"
        and stage == "requirements_gather"
    ):
        member = data.get("coordinated_member") or {}
        if member.get("member") != name or "SEPARATE isolated integration workspace" not in member.get(
            "instruction", ""
        ):
            raise RuntimeError("First member Requirements prompt omits the core-owned joint-proof responsibility")
        if "not exclusions or proxy acceptance" not in member["instruction"]:
            raise RuntimeError("Member context must retain the joint obligation instead of authorizing exclusion")
        if "REAL JOINT PROOF IS NOT A LOCAL PROXY" not in member["instruction"]:
            raise RuntimeError("Real coordinator proof must not ask for local stand-in acceptance")
        if "only this member's executable local path" not in member["instruction"]:
            raise RuntimeError("Local flow completion must precede, not wait for, coordinator joint proof")
        if name != "checkout":
            hint = member.get("reported_work")
            if not isinstance(hint, str) or not hint:
                raise RuntimeError("Discovered members must receive their bound investigation hint")
            sources = [str(data.get("task", "")), *[row.get("text", "") for row in data.get("source_inventory", [])]]
            if any(hint in text for text in sources):
                raise RuntimeError("A model-reported work hint became user requirement source text")
            if "Never copy reported_work into a requirement source_quote" not in member["instruction"]:
                raise RuntimeError("Member context must distinguish discovery hints from user authority")
    if (
        os.environ.get("COORDINATION_FIXTURE_MEMBER_CONTEXT")
        and root.parent.name == "worktrees"
        and stage in ("astra_challenge", "astra_finalize")
        and "REAL JOINT PROOF IS NOT A LOCAL PROXY" not in prompt
    ):
        raise RuntimeError("Member review must scope the ordinary out-of-checkout proxy rule")
    target = {"checkout": "history", "history": "tax"}.get(name)
    source = PRODUCT[name][0] if not joint else "repositories.json"
    test_path = "tests/test_joint.py" if joint else "tests/test_behavior.py"
    paths = [test_path]
    payloads = {}
    if joint:
        candidates = root / ".autocode/candidate-inputs.json"
        if candidates.exists():
            for row in json.loads(candidates.read_text()).values():
                directory = root / row["source"]
                for path in directory.rglob("*"):
                    if path.is_file():
                        payloads[row["destination"] + "/" + path.relative_to(directory).as_posix()] = path.read_text()
        payloads[test_path] = JOINT_TEST
        payloads["conftest.py"] = JOINT_SETUP
        payloads["pytest.ini"] = "[pytest]\naddopts = --import-mode=importlib -p no:cacheprovider\n"
        paths = [*payloads]
    else:
        payloads[source] = PRODUCT[name][1]
        payloads[test_path] = TEST_PREAMBLE + ASSERTIONS[name] + "\n"
        paths.insert(0, source)
    if os.environ.get("COORDINATION_FIXTURE_MISMATCH"):
        if joint and (root / ".autocode/candidate-inputs.json").exists():
            rows = json.loads((root / ".autocode/candidate-inputs.json").read_text())
            prefix = rows["history"]["destination"]
            payloads[prefix + "/history.py"] = PRODUCT["history"][1]
            payloads[prefix + "/tests/test_behavior.py"] = TEST_PREAMBLE + ASSERTIONS["history"] + "\n"
        elif name == "history" and not (root / test_path).exists():
            # Each repository can be locally green while its interfaces disagree.
            payloads[source] = PRODUCT["history"][1].replace("'amount': total", "'amount': total * 100")
            payloads[test_path] = payloads[test_path].replace("{'amount': 110,", "{'amount': 11000,")
    source_messages = [data["task"], *[row.get("text", "") for row in data.get("source_inventory") or []]]
    validate_only = any(
        text.startswith("A joint regression found a mismatch in another repository") for text in source_messages
    ) and all((root / path).is_file() and (root / path).read_text() == content for path, content in payloads.items())
    body = _contract()
    outcome = "Fix checkout showing the wrong final total."
    body.update(
        intended_outcome=outcome,
        end_to_end_flow=[
            "Compute tax, serialize history, display checkout total using the real member code"
            if joint
            else {
                "checkout": "Execute checkout formatting through its local receipt entry point",
                "history": "Execute history serialization and its local tax-service boundary",
                "tax": "Execute the local tax-service entry point and preserve zero",
            }[name]
        ],
        technical_approach=["Execute actual Python functions through their real interfaces"],
        deliverables=paths,
        required_behaviors=[outcome]
        + ([] if joint else ["The coordinator must separately prove the actual checkout, history and tax flow"]),
        constraints=["Do not mock production boundaries"],
        important_failure_cases=[],
        scope_exclusions=[],
        accepted_assumptions=[],
        acceptance_criteria=[{"id": "C1", "criterion": outcome, "verification_method": SUITE, "human_review": False}],
        milestones=[
            {"id": "M1", "objective": outcome, "acceptance_criteria": ["C1"], "depends_on": [], "affected_paths": paths}
        ],
    )
    if target:
        body["scope_exclusions"] = [f"Not covered: changes in {target}: Fix the downstream total contract"]
    if os.environ.get("COORDINATION_FIXTURE_SCOPE_METADATA") and name == "history":
        # Reproduce the live model's omission. The core owns these authenticated
        # peer boundaries; this provider must not manufacture them for it.
        body["scope_exclusions"] = []
    body = cite_sources(body, data)
    contract = data.get("goal_contract") or {}
    task = data.get("current_task") or {}
    report = default(schema)
    common = {
        "contract_revision": contract.get("revision", 0),
        "contract_hash": contract.get("hash", ""),
        "task_id": task.get("id", ""),
        "summary": "Executed deterministic three-repository fixture",
        "source_trace": source_trace_for(body, data),
    }
    report.update({key: value for key, value in common.items() if key in schema.get("properties", {})})
    session = sys.argv[sys.argv.index("resume") + 1] if "resume" in sys.argv else str(uuid.uuid4())
    print(json.dumps({"type": "thread.started", "thread_id": session}), flush=True)
    log = root / ".autocode/provider-calls.jsonl"
    log.parent.mkdir(exist_ok=True)
    with log.open("a") as stream:
        stream.write(
            json.dumps({"stage": stage, "name": name, "model": sys.argv[sys.argv.index("--model") + 1]}) + "\n"
        )
    if stage == "recognize_workflow":
        report.update(workflow="build", reason="Fixture follow-up or integration task", signals=[], design_document="")
        if "clarity" in report:
            report["clarity"] = "clear"
    elif stage == "investigate_bug":
        # The actual failing local behavior is corroborated by the runner's probe.
        if name == "checkout":
            expression = "from client import display; assert display({'amount': 110}) != '$110.00'"
            find, replace = "return str(payload['amount']) + ' USD'", "return f\"${payload['amount']:.2f}\""
        elif name == "history":
            expression = "from history import serialize; assert serialize(110) != {'amount':110,'currency':'USD'}"
            find, replace = "return {'amount': str(total)}", "return {'amount': total, 'currency': 'USD'}"
        else:
            expression = "from tax import add_tax; assert abs(add_tax(100) - 110) > 0.001"
            find, replace = "return value", "return value * 1.1"
        report.update(
            outcome="reproduced",
            note_path="docs/bugs/total.json",
            observed=outcome,
            reproduction="The local function produces the wrong total representation",
            root_cause="The local calculation or contract is stale",
            affected_paths=[source],
            test_paths=[test_path],
            invariant=outcome,
            test_cases=[
                {
                    "id": "T1",
                    "given": "a checkout total",
                    "when": "the real function is called",
                    "then": "the required total is returned",
                    "kind": "restore",
                }
            ],
            conclusion="Correct the local behavior and its downstream interface",
            fix_size="large",
            fix_plan=[outcome],
            questions=[],
            tests_run=[],
            plan_approval_requested=True,
            probe=shlex.join([sys.executable, "-c", expression]),
            untestable="",
            counterexample={"file": source, "find": find, "replace": replace},
            workspace_scope={
                "spans_repositories": bool(target),
                "external_parts": [
                    {
                        "target": target,
                        "work": "Fix the downstream total contract",
                        "source_quote": "",
                        "evidence": source + ":3",
                    }
                ]
                if target
                else [],
            },
        )
        if os.environ.get("COORDINATION_FIXTURE_SCOPE_METADATA") and name == "history":
            report["workspace_scope"]["external_parts"].extend(
                [
                    {
                        "target": "tax repository",
                        "work": "Verify actual tax in the joint run",
                        "source_quote": "",
                        "evidence": source + ":3",
                    },
                    {"target": "checkout", "work": "Format the receipt", "source_quote": data["task"], "evidence": ""},
                    {
                        "target": "checkout repository",
                        "work": "Verify actual composed checkout output",
                        "source_quote": data["task"],
                        "evidence": "",
                    },
                ]
            )
    elif "contract" in schema.get("properties", {}):
        body = {**(report.get("contract") or {}), **body}
        report["contract"] = body
        if "initial_task" in schema["properties"]["contract"].get("properties", {}):
            body["initial_task"] = {
                "kind": "validate" if validate_only else "implement",
                "milestone_id": "M1",
                "objective": outcome,
                "affected_paths": [] if validate_only else paths,
                "requirements": [outcome],
                "acceptance_criteria": ["C1"],
                "validation_plan": [SUITE],
                "findings": [],
            }
    elif stage == "requirements_gather":
        report.update(
            summary=outcome,
            intended_outcome=outcome,
            required_behaviors=[outcome],
            acceptance_tests=[SUITE],
            constraints=["Do not mock production boundaries"],
            source_refs=[source],
            requirements=[{"id": "R1", "text": data["task"], "source_quote": data["task"]}],
            open_questions=[],
            proposed_assumptions=[],
        )
        if (
            os.environ.get("COORDINATION_FIXTURE_EARLY_SCOPE") or os.environ.get("COORDINATION_FIXTURE_SCOPE_METADATA")
        ) and target:
            report["source_refs"] = [source]
            report["workspace_scope"] = {
                "spans_repositories": True,
                "external_parts": [
                    {
                        "target": target,
                        "work": "Fix the downstream total contract",
                        "source_quote": "",
                        "evidence": source + ":3",
                    }
                ],
            }
            if os.environ.get("COORDINATION_FIXTURE_CONSUMER_ALIAS") and name == "history":
                report["workspace_scope"]["external_parts"].append(
                    {
                        "target": "checkout repository",
                        "work": "Format the receipt in the consumer",
                        "source_quote": data["task"],
                        "evidence": "",
                    }
                )
            if os.environ.get("COORDINATION_FIXTURE_SCOPE_METADATA") and name == "history":
                report["workspace_scope"]["external_parts"].extend(
                    [
                        {
                            "target": "tax repository",
                            "work": "Verify actual tax in the joint run",
                            "source_quote": "",
                            "evidence": source + ":3",
                        },
                        {
                            "target": "checkout",
                            "work": "Format the receipt",
                            "source_quote": data["task"],
                            "evidence": "",
                        },
                        {
                            "target": "checkout repository",
                            "work": "Verify actual composed checkout output",
                            "source_quote": data["task"],
                            "evidence": "",
                        },
                    ]
                )
    elif stage == "astra_challenge":
        if os.environ.get("COORDINATION_FIXTURE_SCOPE_METADATA") and name == "history":
            exclusions = contract["body"]["scope_exclusions"]
            for target in ("tax", "tax repository", "checkout", "checkout repository"):
                if not any(row.startswith("Not covered: changes in " + target + ": ") for row in exclusions):
                    raise RuntimeError("Reviewer must see every core-assigned exclusion before approval: " + target)
        report.update(concerns=[], source_trace=source_trace_for((contract.get("body") or body), data))
    elif stage == "terra":
        changed = []
        for name, content in payloads.items():
            path = root / name
            path.parent.mkdir(parents=True, exist_ok=True)
            if not path.exists() or path.read_text() != content:
                changed.append(name)
            path.write_text(content)
        report.update(
            changed_files=changed,
            commands_run=[SUITE],
            results=["Executed the real behavior tests"],
            evidence_refs=["event:check"],
            remaining_risks=[],
            addressed_requirements=task.get("requirements", []),
        )
    elif stage == "review_change":
        report.update(
            verdict="approve",
            summary="Review actual joint code; the independent runner still executes both baselines",
            change_under_review="Joint regression and candidate repositories",
            change_patch="",
            findings=[],
            tests_run=[SUITE],
            delivered_tests=[],
        )
    elif stage in ("sol", "astra_checkpoint") and "verdict" in schema.get("properties", {}):
        report.update(
            verdict="PASS",
            checks_run=[SUITE],
            checks=[{"command": SUITE, "exit_code": 0, "evidence_ref": "event:check"}],
            criterion_results=[{"id": "C1", "status": "PASS", "evidence_refs": ["event:check"]}],
            end_to_end_result={
                "status": "PASS",
                "summary": "Executed the actual joint code" if joint else f"Executed the {name} local flow",
                "evidence_refs": ["event:check"],
                "technical_result": None,
                "pending_human_criteria": [],
            },
        )
    elif stage in ("astra_plan", "astra_review", "astra_checkpoint", "astra_resolve"):
        done = stage in ("astra_review", "astra_checkpoint") and (data.get("validation") or {}).get("verdict") == "PASS"
        report.update(
            status="COMPLETE" if done else "CONTINUE",
            acceptance_criteria=[
                {
                    "id": "C1",
                    "criterion": outcome,
                    "status": "verified" if done else "unverified",
                    "evidence": "event:check" if done else "",
                }
            ],
            next_objective="" if done else outcome,
            affected_paths=[] if done or validate_only else paths,
            evidence=["event:check"] if done else [],
            next_task={
                "kind": "none" if done else "validate" if validate_only else "implement",
                "milestone_id": "" if done else "M1",
                "requirements": [] if done else [outcome],
                "acceptance_criteria": [] if done else ["C1"],
                "validation_plan": [] if done else [SUITE],
                "findings": [],
            },
        )
    if stage in ("terra", "sol", "astra_review", "astra_checkpoint", "review_change"):
        checked = subprocess.run(shlex.split(SUITE), capture_output=True, text=True)
        print(
            json.dumps(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "check",
                        "type": "command_execution",
                        "command": SUITE,
                        "exit_code": checked.returncode,
                        "aggregated_output": checked.stdout + checked.stderr,
                    },
                }
            ),
            flush=True,
        )
        if checked.returncode:
            raise RuntimeError("The fixture's actual behavioral checks failed: " + checked.stderr)
    if "source_verdict" in report:
        report["source_verdict"] = dict(SOURCE_VERDICT)
    if "code_refs" in report:
        report["code_refs"] = [source + ":1"]
    if "requirement_trace" in report:
        report["requirement_trace"] = [
            {"requirement_id": row["requirement_id"], "disposition": "covered", "evidence": "C1"}
            for row in data.get("requirement_trace_rows", [])
        ]
    if "retain_contract" in schema.get("properties", {}).get("contract", {}).get("properties", {}):
        draft = report["contract"]
        retained = data.get("retained_contract") or (data.get("clarification_context") or {}).get("retained_contract")
        report["contract"] = {"retain_contract": retained, "open_blocking_questions": []}
        if "initial_task" in schema["properties"]["contract"]["properties"]:
            report["contract"]["initial_task"] = draft["initial_task"]
        if "acceptance_criteria" in schema["properties"]["contract"]["properties"]:
            report["contract"]["acceptance_criteria"] = {
                "retain_from": retained,
                "upsert": [],
                "remove": [],
                "proofs": [],
            }
        report["requirement_trace"] = []
    # Keep the fake output strictly at the active public schema, including optional fields it declared.
    output.write_text(json.dumps(shaped(report, schema)))
    print(json.dumps({"type": "turn.completed", "usage": {"input_tokens": 10, "output_tokens": 10}}), flush=True)


if __name__ == "__main__":
    main()
