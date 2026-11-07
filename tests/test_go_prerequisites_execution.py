"""Offline real-Go proof: test dependencies cannot substitute for a product fix."""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

import autocode_verify as verify


@unittest.skipUnless(sys.platform == "linux" and shutil.which("go"), "owned Go checks require supported Linux")
class GoPrerequisitesExecutionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="go-prerequisites-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.workspace = self.root / "project"
        self.workspace.mkdir()
        proxy = self.root / "proxy"
        for module, version, files in [
            (
                "example.test/direct",
                "v1.0.0",
                {
                    "go.mod": "module example.test/direct\ngo 1.23.0\nrequire example.test/indirect v1.2.0\n",
                    "direct.go": "package direct\nfunc Two() int { return 2 }\n",
                },
            ),
            (
                "example.test/indirect",
                "v1.2.0",
                {
                    "go.mod": "module example.test/indirect\ngo 1.23.0\n",
                    "indirect.go": "package indirect\nfunc One() int { return 1 }\n",
                },
            ),
        ]:
            folder = proxy / module / "@v"
            folder.mkdir(parents=True)
            (folder / (version + ".mod")).write_text(files["go.mod"])
            (folder / (version + ".info")).write_text(json.dumps({"Version": version, "Time": "2024-01-01T00:00:00Z"}))
            with zipfile.ZipFile(folder / (version + ".zip"), "w") as archive:
                for name, content in files.items():
                    archive.writestr(module + "@" + version + "/" + name, content)
        self.environment = {
            **os.environ,
            "GOPROXY": proxy.as_uri(),
            "GOSUMDB": "off",
            "GOTOOLCHAIN": "local",
            "GOENV": "off",
            "GOWORK": "off",
            "GOMODCACHE": str(self.root / "cache"),
            "GOFLAGS": "-p=2",
            "GOMAXPROCS": "2",
        }
        self.enterContext(mock.patch.dict(os.environ, self.environment))
        (self.workspace / "go.mod").write_text(
            "module example.test/app\ngo 1.23.0\nrequire example.test/direct v1.0.0\n"
        )
        (self.workspace / "app.go").write_text(
            "package app\nfunc Value() int { return 0 }\nfunc Existing() int { return 2 }\n"
        )
        (self.workspace / "app_test.go").write_text(
            'package app\nimport ("testing"; "example.test/direct")\n'
            'func TestExisting(t *testing.T) { if Existing() != direct.Two() { t.Fatal("existing") } }\n'
        )
        subprocess.run(
            ["go", "mod", "download", "all"],
            cwd=self.workspace,
            env=self.environment,
            capture_output=True,
            check=True,
            timeout=60,
        )
        os.environ["GOPROXY"] = "off"
        self.environment["GOPROXY"] = "off"
        self.git("init", "-q")
        self.git("config", "maintenance.auto", "false")
        self.git("add", ".")
        self.git("-c", "user.name=test", "-c", "user.email=test@example.test", "commit", "-qm", "base")
        self.base = self.git("rev-parse", "HEAD")
        self.framework = verify.detect_framework(self.workspace)
        self.baseline = verify.baseline(
            self.workspace,
            self.base,
            self.root / "baseline",
            framework=self.framework,
            suite_command="go test ./...",
            timeout=60,
        )
        self.assertEqual(0, self.baseline["receipt"]["exit_code"])
        self.original_mod = (self.workspace / "go.mod").read_text()
        self.candidate_mod = self.original_mod + "require example.test/indirect v1.2.0 // indirect\n"
        self.new_test = (
            'package app\nimport ("testing"; "example.test/indirect")\n'
            'func TestAC1(t *testing.T) { if Value() != indirect.One() { t.Fatal("wrong value") } }\n'
        )

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.workspace), *args], text=True).strip()

    def candidate(self, fixed):
        (self.workspace / "go.mod").write_text(self.candidate_mod)
        (self.workspace / "regression_test.go").write_text(self.new_test)
        if fixed:
            (self.workspace / "app.go").write_text(
                "package app\nfunc Value() int { return 1 }\nfunc Existing() int { return 2 }\n"
            )

    def proof(self, name):
        return verify.verify(
            self.workspace,
            self.base,
            self.root / name,
            framework=self.framework,
            suite_command="go test ./...",
            regression_command="go test -run '^TestAC1$' .",
            base_suite=self.baseline,
            timeout=60,
        )

    def test_identical_prerequisites_reach_behavioral_failure_on_original_source(self):
        self.candidate(True)
        result = self.proof("corrected")
        self.assertEqual("PASS", result["verdict"], verify.feedback(result))
        self.assertIn("example.test/app::TestAC1", result["fail_to_pass"])
        self.assertEqual([], result["checks"]["regression_on_base"]["results"]["collection_errors"])
        self.assertEqual(self.candidate_mod, (self.workspace / "go.mod").read_text())
        self.assertIn("go.mod", result["test_prerequisites"]["files"])

    def test_metadata_without_product_fix_cannot_pass(self):
        self.candidate(False)
        result = self.proof("unfixed")
        self.assertEqual("FAIL", result["verdict"])
        self.assertIn("example.test/app::TestAC1", result["checks"]["regression_on_candidate"]["results"]["failed"])

    def test_public_fake_provider_acceptance_and_saved_reopen(self):
        import dataclasses

        import autocode_taskrun as taskrun

        scenarios = Path(__file__).resolve().parents[1] / "scenarios"
        if str(scenarios) not in sys.path:
            sys.path.insert(0, str(scenarios))
        from harness import catalog
        from harness.driver import fake_setup

        reference = self.root / "reference"
        reference.mkdir()
        for name in ("app.go", "app_test.go", "go.mod", "go.sum"):
            shutil.copy2(self.workspace / name, reference / name)
        (reference / "app.go").write_text(
            "package app\nfunc Value() int { return 1 }\nfunc Existing() int { return 2 }\n"
        )
        (reference / "go.mod").write_text(self.candidate_mod)
        (reference / "regression_test.go").write_text(self.new_test)
        scenario = dataclasses.replace(
            catalog.load("feature-stock-refusals"),
            dir=self.root,
            brief="Make Value return the existing indirect module One value, 1. Preserve Existing returning 2. "
            "Add a real TestAC1 regression and only the required existing-version indirect Go metadata. "
            "Change app.go, regression_test.go, go.mod and go.sum only.",
            fake_fault="",
            fake_criteria={"AC1": "test: TestAC1"},
            fake_milestones=(),
            fake_turn_paths=(),
            turns=(),
            fake_check="go test ./...",
            fake_flags=(),
            fake_plan_review_call_limit=None,
        )
        flags, fake_environment = fake_setup(scenario, self.root, reference)
        environment = {**fake_environment, **self.environment, "PATH": fake_environment["PATH"]}
        environment.update(
            AUTOCODE_HOME=str(self.root / "registry"), PYTHONDONTWRITEBYTECODE="1", SCENARIO_FAKE_EXISTING_SOURCE="1"
        )
        provider = self.root / "bin/codex"
        script = provider.read_text()
        marker = 'if __name__ == "__main__":'
        amendment = """
def acceptance_tests(data):
    for name in ("regression_test.go", "go.mod"):
        shutil.copy2(Path(CONFIG["reference"]) / name, Path.cwd() / name)
    return {"summary": "Real Go test with existing-version loading metadata", "tests": [
        {"case_id": row["id"], "test_name": row["test_name"], "file": "regression_test.go"}
        for row in data["cases"]], "already_passing": [], "cannot_write": [], "tests_run": []}
"""
        amendment += "\nEXPECTED_GO_SOURCE = " + repr((reference / "app.go").read_text()) + "\n"
        amendment += "EXPECTED_GO_TEST = " + repr(self.new_test) + "\n"
        amendment += r"""
_original_lazy_citations = lazy_citations
def lazy_citations(report):
    import hashlib
    report = _original_lazy_citations(report)
    if "changed_code_exceptions" not in OUTPUT_SCHEMA.get("properties", {}):
        return report
    source = Path("app.go").read_text()
    if source != EXPECTED_GO_SOURCE or Path("regression_test.go").read_text() != EXPECTED_GO_TEST:
        return report
    proof = DATA.get("regression_proof") or {}
    digest = hashlib.sha256(source.encode()).hexdigest()
    for row in (proof.get("changed_code_evidence") or {}).get("obligations") or []:
        if (row.get("path") == "app.go" and row.get("sha256") == digest and row.get("line") == 2
                and row.get("status") == "reason_required" and not row.get("mutation_outcomes") and proof.get("path")):
            report.setdefault("changed_code_exceptions", []).append({
                "gap_id": row["gap_id"], "decision": "accept", "reason_kind": "instrumentation_unavailable",
                "reason": "The runner has no Go mutation support. The exact TestAC1 calls Value and compares its result with the pinned indirect.One value 1; returning the original 0 fails that native test. This reason covers only this exact scripted return line.",
                "evidence_refs": [proof["path"], "app.go"],
            })
    return report
"""
        self.assertEqual(1, script.count(marker))
        provider.write_text(script.replace(marker, amendment + "\n" + marker))
        run = taskrun.TaskRun.start(
            self.workspace,
            scenario.brief,
            options=(*flags, "--spec-first"),
            env=environment,
            timeout=240,
            cwd=self.root,
        )
        approved = []
        for _ in range(10):
            view = run.advance_until_input()
            if view["done"]:
                break
            need = view.get("needs") or {}
            if need.get("kind") == "approve_plan":
                self.assertIn("TestAC1", run.show_goal())
                approved.append(need["token"])
                run.approve_plan(need["token"])
            elif need.get("kind") == "answer" and need.get("resolver_scope") == "clarification":
                question = need["questions"][0]
                self.assertEqual("plan", question.get("proposed_default"))
                run.answer(question["id"], "plan", resolver_token=need["resolver_token"])
            elif need.get("kind") == "review":
                for criterion in need["criteria"]:
                    run.approve_review(criterion, need["token"])
            else:
                self.fail(str({k: view.get(k) for k in ("status", "next_stage", "needs", "stop_reason")}))
        self.assertEqual("TASK_COMPLETE", view["status"])
        self.assertEqual(1, len(approved))
        proof = view["evidence"]["regression_proof"]
        self.assertEqual("PASS", proof["verdict"])
        attempts = len(view["usage"]["accounting"]["attempts"])
        reopened = taskrun.TaskRun(run.workspace, run.run_dir, env=environment, timeout=60).status()
        self.assertTrue(reopened["done"])
        self.assertEqual(attempts, len(reopened["usage"]["accounting"]["attempts"]))
        self.assertEqual(self.candidate_mod, (Path(run.workspace) / "go.mod").read_text())
