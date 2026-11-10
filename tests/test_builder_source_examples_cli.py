"""An eligible bug-fix Builder reuses its attached tests through the public CLI.

The scripted provider checks its own prompt input and records only assertions,
never a copied prompt or private run state. The existing pager scenario supplies
the bug, solution and real verification commands.
"""

import json
import os
import shutil
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

import autocode_taskrun as taskrun

SCENARIOS = Path(__file__).resolve().parents[1] / "scenarios"
if str(SCENARIOS) not in sys.path:
    sys.path.insert(0, str(SCENARIOS))
from harness import catalog
from harness.driver import fake_setup
from harness.project import materialize, without_maintenance


@unittest.skipUnless(sys.platform == "linux", "supervised fake providers require supported Linux")
class BuilderSourceExamplesCLI(unittest.TestCase):
    def test_eligible_builder_references_exact_tests_and_completes_all_gates(self):
        temporary = tempfile.TemporaryDirectory(prefix="builder-source-examples-cli-")
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        scenario = catalog.load("bugfix-trivial")
        project = without_maintenance(materialize(scenario.seed, root / "project"))
        flags, provider_env = fake_setup(scenario, root, scenario.dir / "reference")
        environment = {**os.environ, **provider_env, "AUTOCODE_HOME": str(root / "registry")}
        paths = environment["PATH"].split(os.pathsep)
        environment["PATH"] = os.pathsep.join([paths[0], str(Path(sys.executable).parent), *paths[1:]])
        provider = root / "bin/codex"
        marker = "    capacity_exit = capacity_once(stage, session)"
        amendment = textwrap.dedent("""
            observed = {"stage": stage, "repair": bool(data.get("report_repair"))}
            if stage == "builder" and not observed["repair"]:
                prefix = prompt.split("\\nCURRENT HANDOFF DATA\\n", 1)[0]
                source = data.get("review_source") or {}
                attached = source.get("files") or []
                matched = [(i, row) for i, row in enumerate(attached) if row.get("path") == "tests/test_pager.py"]
                observed["complete_test_attached"] = len(matched) == 1
                if matched:
                    index, row = matched[0]
                    expected = Path("tests/test_pager.py").read_bytes()
                    observed["exact_test_bytes"] = row.get("content", "").encode() == expected
                    observed["exact_test_hash"] = row.get("sha256") == hashlib.sha256(expected).hexdigest()
                    observed["reference_present"] = f"review_source.files[{index}].content" in prefix
                observed["duplicate_excerpt_absent"] = "--- tests/test_pager.py (lines" not in prefix
                observed["independent_checks_required"] = "independently execute all required checks" in source.get("instruction", "")
                observed["style_instruction_retained"] = "Write every new test the same way" in prefix
            witness = Path(os.environ["SCENARIO_FAKE_CONFIG"]).with_name("builder-source-examples-witness.jsonl")
            with witness.open("a") as handle:
                handle.write(json.dumps(observed) + "\\n")
            if stage == "builder" and not observed["repair"]:
                assert all(value for name, value in observed.items() if name not in ("stage", "repair")), observed
        """)
        source = provider.read_text()
        self.assertEqual(1, source.count(marker))
        # This scenario's generic fake normally omits task_kind (defaulting to
        # build). Its requested pager correction is a bug fix; author that fact
        # in the fake's plan before the normal review and exact-plan approval.
        kind = '    if (DATA.get("bug_diagnosis") or {}).get("root_cause"):'
        self.assertEqual(1, source.count(kind))
        source = source.replace(kind, "    if CONFIG['title'] == " + repr(scenario.title) + ":", 1)
        provider.write_text(source.replace(marker, textwrap.indent(amendment, "    ") + "\n" + marker))
        run = taskrun.TaskRun.start(
            project,
            scenario.brief,
            options=(*flags, "--spec-first", "--max-cost-usd", "0", "--max-parallel-builders", "1"),
            env=environment,
            timeout=240,
            cwd=root,
        )
        approvals = []
        for _ in range(12):
            view = run.advance_until_input()
            need = view.get("needs") or {}
            if view["done"]:
                break
            if need.get("kind") == "approve_plan":
                self.assertIn("page_count", run.show_goal())
                approvals.append(need["token"])
                run.approve_plan(need["token"])
            elif need.get("kind") == "answer" and view.get("chat", {}).get("review"):
                question = need["questions"][0]
                run.answer(question["id"], view["chat"]["review"]["answer"], resolver_token=need["resolver_token"])
            else:
                break
        witness = root / "builder-source-examples-witness.jsonl"
        rows = [json.loads(line) for line in witness.read_text().splitlines()] if witness.exists() else []
        if evidence := os.environ.get("BUILDER_EXAMPLES_EVIDENCE_DIR"):
            destination = Path(evidence)
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "public-status.json").write_text(json.dumps(view, indent=2))
            if witness.exists():
                shutil.copy2(witness, destination / "provider-assertions.jsonl")
        self.assertEqual("TASK_COMPLETE", view["status"], view.get("stop_reason"))
        self.assertEqual([view["approved_contract"]["token"]], approvals)
        self.assertEqual("bugfix", view["approved_contract"]["body"]["task_kind"])
        self.assertEqual("clear", view["planning_depth"]["clarity"])
        self.assertEqual("PASS", view["evidence"]["regression_proof"]["verdict"])
        self.assertTrue(view["evidence"]["regression_proof"]["fail_to_pass"])
        self.assertEqual("PASS", view["evidence"]["regression_proof"]["changed_code_evidence"]["status"])
        self.assertEqual("PASS", view["evidence"]["check_replay"]["verdict"])
        self.assertTrue(view["evidence"]["acceptance"])
        for criterion in view["evidence"]["acceptance"]:
            self.assertEqual("verified", criterion["status"])
            self.assertEqual("PASS", criterion["validator_status"])
        self.assertFalse(any(row["repair"] for row in rows), rows)
        for stage in ("builder", "tester", "review_completion"):
            self.assertEqual(1, sum(row["stage"] == stage for row in rows), rows)
        builder = next(row for row in rows if row["stage"] == "builder")
        self.assertTrue(builder["complete_test_attached"])
        self.assertTrue(builder["reference_present"])
