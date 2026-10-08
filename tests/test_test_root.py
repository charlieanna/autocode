"""Caller-rooted regression proofs using real Git and Python test subprocesses."""
import shlex
import errno
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from copy import deepcopy
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace

import autocode_regression as regression
import autocode_test_root as test_root
import autocode_util as util
import autocode_verify as verify
from tests.test_verify import Project, git, isolated_python

ARCHITECTURE = {"README.md": "Two components.\n", "architecture/components.json": '[{"id":"gateway"},{"id":"store"}]\n'}
SERVER = "def health():\n    return {status!r}\n"
NEW_TEST = ("import os\nimport sys\nimport unittest\n"
            "sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))\n"
            "import server\nclass GatewayTests(unittest.TestCase):\n"
            "    def test_c1_health_answers_ok(self):\n        self.assertEqual('ok', server.health())\n")
OLD_TEST = NEW_TEST.replace("GatewayTests", "ExistingTests").replace("test_c1_health_answers_ok", "test_health_is_string").replace(
    "self.assertEqual('ok', server.health())", "self.assertIsInstance(server.health(), str)")
SMOKE_TEST = NEW_TEST.replace("self.assertEqual('ok', server.health())", "self.assertTrue(server.health())")
CRITERION = {"id": "C1", "criterion": "GET /health answers ok", "verification_method": "test: test_c1_health_answers_ok"}


def component_files(status="ok", *, tests_dir="tests", package=True):
    files = {"components/gateway/server.py": SERVER.format(status=status),
             f"components/gateway/{tests_dir}/test_gateway.py": NEW_TEST}
    if package:
        files[f"components/gateway/{tests_dir}/__init__.py"] = ""
    return files


def state(project, *, root="components/gateway", python=sys.executable, criterion=CRITERION):
    options = {key: value for key, value in (("test_root", root), ("python", python)) if value}
    return {"base_commit": project.base, "settings": {"regression": options}, "iteration": 1, "stages": [],
            "history": [], "goal_contract": {"body": {"task_kind": "build", "acceptance_criteria": [criterion],
                                                      "milestones": [{"id": "M1"}]}}}


def commit(project, files):
    project.write(files)
    git(project.root, "add", "-A")
    git(project.root, "-c", "user.name=t", "-c", "user.email=t@example.test", "commit", "-qm", "policy fixture")
    project.base = git(project.root, "rev-parse", "HEAD")


class ComponentProofTests(unittest.TestCase):
    def setUp(self):
        self.project = Project(ARCHITECTURE)
        self.addCleanup(self.project.close)
        temp = tempfile.TemporaryDirectory(prefix="test-root-proof-")
        self.addCleanup(temp.cleanup)
        self.run_dir = Path(temp.name)

    def prove(self, **options):
        return regression.prove(state(self.project, **options), self.project.root, self.run_dir)

    def reasons(self, proof):
        return " | ".join(proof["failures"] + proof["unverified"])

    def pytest_python(self):
        if not verify._python_can_import(sys.executable, "pytest"):
            self.skipTest("controller interpreter cannot import pytest")
        return sys.executable

    def test_rooted_suite_collects_and_proves_component_tests(self):
        self.project.write(component_files())
        proof = self.prove()
        self.assertEqual(verify.PASS, proof["verdict"], self.reasons(proof))
        self.assertEqual("detected:unittest", proof["commands"]["suite_source"])
        self.assertTrue(proof["commands"]["suite"].endswith("discover -v -s components/gateway"))
        self.assertEqual({"C1": ["components.gateway.tests.test_gateway.GatewayTests.test_c1_health_answers_ok"]},
                         proof["case_tests"])
        self.assertTrue(any("nothing under components/gateway/" in note for note in proof["notes"]))
        suite = util.read(proof["path"])["checks"]["suite_on_candidate"]
        self.assertEqual(["tests.test_gateway.GatewayTests.test_c1_health_answers_ok"], suite["results"]["passed"])

    def test_source_bound_component_proves_real_subprocess_http_health_post_get(self):
        server = '''import json
from http.server import BaseHTTPRequestHandler
NOTES = {}
class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass
    def reply(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_GET(self):
        if self.path == "/health":
            return self.reply(200, {"status": "ok"})
        self.reply(200, NOTES[int(self.path.rsplit("/", 1)[1])])
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        note = {"id": len(NOTES) + 1, "text": body["text"]}
        NOTES[note["id"]] = note
        self.reply(201, note)
'''
        test = '''import http.client
import json
import subprocess
import sys
import unittest
from pathlib import Path
class StoreTests(unittest.TestCase):
    def test_c1_real_http_flow(self):
        code = ('from http.server import ThreadingHTTPServer; import server; '
                'httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler); '
                'print(httpd.server_port, flush=True); httpd.serve_forever()')
        process = subprocess.Popen([sys.executable, "-u", "-c", code],
            cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        try:
            port = int(process.stdout.readline())
            client = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            client.request("GET", "/health")
            response = client.getresponse()
            self.assertEqual(200, response.status)
            self.assertEqual({"status": "ok"}, json.loads(response.read()))
            client.request("POST", "/notes", json.dumps({"text": "hello"}), {"Content-Type": "application/json"})
            response = client.getresponse()
            self.assertEqual(201, response.status)
            note = json.loads(response.read())
            self.assertEqual("hello", note["text"])
            self.assertIsInstance(note["id"], int)
            client.request("GET", "/notes/" + str(note["id"]))
            response = client.getresponse()
            self.assertEqual(200, response.status)
            self.assertEqual(note, json.loads(response.read()))
            client.close()
        finally:
            process.terminate()
            process.communicate(timeout=5)
'''
        self.project.write({"components/store/server.py": server, "components/store/tests/__init__.py": "",
                            "components/store/tests/test_store.py": test})
        criterion = {"id": "C1", "criterion": "Real local HTTP health, POST, GET flow",
                     "verification_method": "test: test_c1_real_http_flow"}
        run = state(self.project, root="components/store", criterion=criterion, python=isolated_python(self))
        proof = regression.prove(run, self.project.root, self.run_dir)
        self.assertEqual(verify.PASS, proof["verdict"], self.reasons(proof))
        self.assertEqual({"C1": ["components.store.tests.test_store.StoreTests.test_c1_real_http_flow"]}, proof["case_tests"])
        handoff = regression.handoff(run)
        self.assertEqual(proof["case_tests"], handoff["case_tests"])
        self.assertEqual(proof["source_revision"], handoff["source_revision"])
        self.assertTrue(handoff["checks"])
        revision = regression.source_scope.snapshot(self.project.root, run)["revision"]
        self.assertTrue(regression.complete(run, revision))
        self.project.write({"components/store/server.py": server.replace('self.reply(201, note)', 'self.reply(500, note)')})
        revision = regression.source_scope.snapshot(self.project.root, run)["revision"]
        self.assertFalse(regression.complete(run, revision))

    def test_unmarked_test_directory_and_empty_collection(self):
        for directory, verdict in (("tests", verify.PASS), ("checks", verify.UNVERIFIED)):
            with self.subTest(directory=directory):
                self.project = Project(ARCHITECTURE)
                self.addCleanup(self.project.close)
                self.project.write(component_files(tests_dir=directory, package=False))
                proof = self.prove()
                self.assertEqual(verdict, proof["verdict"], proof)
                if verdict == verify.UNVERIFIED:
                    self.assertIn("reported zero tests", self.reasons(proof))

    def test_old_failure_skip_and_hook_narrowing_cannot_be_hidden(self):
        for mutation in ("broken", "skip", "tests hook", "component hook"):
            with self.subTest(mutation=mutation):
                self.project = Project(ARCHITECTURE)
                self.addCleanup(self.project.close)
                commit(self.project, {**component_files(), "components/gateway/__init__.py": "",
                                     "components/gateway/tests/test_existing.py": OLD_TEST})
                self.project.write({"components/gateway/server.py": SERVER.format(status=200),
                    "components/gateway/tests/test_gateway.py": NEW_TEST.replace("'ok', server.health()", "200, server.health()")})
                if mutation == "skip":
                    self.project.write({"components/gateway/tests/test_existing.py": OLD_TEST.replace(
                        "    def test_health_is_string", "    @unittest.skip('hidden')\n    def test_health_is_string")})
                elif mutation.endswith("hook"):
                    package = "components/gateway/tests" if mutation == "tests hook" else "components/gateway"
                    self.project.write({package + "/__init__.py": 'def load_tests(loader, tests, pattern):\n'
                        '    return loader.loadTestsFromName("components.gateway.tests.test_gateway")\n'})
                proof = self.prove()
                self.assertEqual(verify.FAIL, proof["verdict"], proof)
                self.assertIn("test_health_is_string", self.reasons(proof))

    def test_component_package_hook_and_relative_imports_below_namespace_parent(self):
        commit(self.project, {**component_files(), "components/gateway/__init__.py":
            'def load_tests(loader, tests, pattern):\n    return loader.loadTestsFromName("components.gateway.hidden_checks")\n',
            "components/gateway/hidden_checks.py": OLD_TEST.replace("import server", "from components.gateway import server")})
        self.project.write({"components/gateway/server.py": SERVER.format(status=200),
            "components/gateway/tests/test_gateway.py": NEW_TEST.replace("'ok', server.health()", "200, server.health()")})
        proof = self.prove()
        self.assertEqual(verify.FAIL, proof["verdict"], proof)
        self.assertIn("hidden_checks.ExistingTests.test_health_is_string", self.reasons(proof))
        self.project = Project(ARCHITECTURE)
        self.addCleanup(self.project.close)
        self.project.write({**component_files(), "components/gateway/__init__.py": "",
                           "components/gateway/tests/test_gateway.py": NEW_TEST.replace("import server", "from .. import server")})
        proof = self.prove()
        self.assertEqual(verify.PASS, proof["verdict"], self.reasons(proof))
        self.assertTrue(proof["commands"]["suite"].endswith(" -s components/gateway -t ."))
        self.assertFalse((self.project.root / "components/__init__.py").exists())

    def test_preexisting_component_failure_does_not_become_new_regression(self):
        commit(self.project, {**component_files(status=200), "components/gateway/__init__.py": "",
                             "components/gateway/tests/test_existing.py": OLD_TEST,
                             "components/gateway/tests/test_passing.py": SMOKE_TEST})
        self.project.write({"components/gateway/server.py": SERVER.format(status="ok"),
                           "components/gateway/tests/test_gateway.py": NEW_TEST + "\n# repair coverage\n"})
        proof = self.prove()
        self.assertEqual(verify.PASS, proof["verdict"], self.reasons(proof))

    def test_outside_root_is_unverified_even_with_explicit_command(self):
        self.project.write({**component_files(), "components/__init__.py": ""})
        for explicit in (False, True):
            with self.subTest(explicit=explicit):
                run = state(self.project)
                if explicit:
                    run["settings"]["regression"]["test_command"] = f"{shlex.quote(sys.executable)} -m unittest discover -v -s components/gateway"
                proof = regression.prove(run, self.project.root, self.run_dir)
                self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
                self.assertIn("outside the test root components/gateway/ changed", self.reasons(proof))

    def test_explicit_command_gets_no_empty_root_exemption(self):
        self.project.write(component_files())
        for option in ("test_command", "regression_command"):
            with self.subTest(option=option):
                run = state(self.project)
                run["settings"]["regression"][option] = f"{shlex.quote(sys.executable)} -m unittest discover -v -s components/gateway"
                proof = regression.prove(run, self.project.root, self.run_dir)
                self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
                source = "suite_source" if option == "test_command" else "regression_source"
                self.assertEqual("explicit", proof["commands"][source])
                self.assertIn("preservation", self.reasons(proof))

    def test_a_caller_supplied_framework_gets_no_detected_root_exemption(self):
        suite = f"{shlex.quote(sys.executable)} -m unittest discover -v -s components/gateway -p test_gateway.py"
        for base in (ARCHITECTURE, {"README.md": "A new component.\n"}):
            self.project = Project(base)
            self.addCleanup(self.project.close)
            self.project.write(component_files())
            for marked in (False, True):
                with self.subTest(base=list(base), marked=marked):
                    framework = verify.Framework("unittest", suite, python=sys.executable,
                                                 test_root="components/gateway" if marked else None)
                    baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
                        framework=framework, suite_command=suite, timeout=120)
                    proof = verify.verify(self.project.root, self.project.base, self.run_dir,
                        framework=framework, base_suite=baseline, new_behavior=True, test_root="components/gateway", timeout=120)
                    self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
                    self.assertIn("preservation", self.reasons(proof))

    def test_independently_canonical_rooted_framework_needs_no_public_marker(self):
        for base in (ARCHITECTURE, {"README.md": "A new component.\n"}):
            self.project = Project(base)
            self.addCleanup(self.project.close)
            self.project.write(component_files())
            detected = verify.detect_framework(self.project.root, python=sys.executable,
                test_root="components/gateway", base=self.project.base)
            for framework in (detected, verify.Framework(detected.name, detected.suite, python=detected.python)):
                with self.subTest(base=list(base), marked=framework.test_root):
                    baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
                        framework=framework, suite_command=framework.suite, timeout=120)
                    proof = verify.verify(self.project.root, self.project.base, self.run_dir,
                        framework=framework, base_suite=baseline, new_behavior=True, test_root="components/gateway", timeout=120)
                    self.assertEqual(verify.PASS, proof["verdict"], self.reasons(proof))

    def test_canonical_root_command_with_different_python_metadata_is_not_authority(self):
        self.project.write(component_files())
        suite = f"{shlex.quote(sys.executable)} -m unittest discover -v -s components/gateway"
        framework = verify.Framework("unittest", suite, python="python3", test_root="components/gateway")
        baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
            framework=framework, suite_command=suite, timeout=120)
        proof = verify.verify(self.project.root, self.project.base, self.run_dir,
            framework=framework, base_suite=baseline, new_behavior=True, test_root="components/gateway", timeout=120)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
        self.assertIn("preservation", self.reasons(proof))

    @unittest.skipUnless(shutil.which("go"), "needs a Go toolchain")
    def test_scoped_explicit_go_command_cannot_use_unscoped_first_suite_allowance(self):
        self.project.write({"components/gateway/server.go": "package gateway\nfunc Health() int { return 200 }\n",
            "components/gateway/server_test.go": 'package gateway\nimport "testing"\n'
                'func TestHealth(t *testing.T) { if Health() != 200 { t.Fatal("health") } }\n'})
        suite = "go test ./..."
        framework = verify.Framework("go", suite, test_root="components/gateway")
        with patch.dict(os.environ, {"GO111MODULE": "off"}):
            baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
                framework=framework, suite_command=suite, timeout=120)
            for root, expected in ((None, verify.PASS), ("components/gateway", verify.UNVERIFIED)):
                with self.subTest(root=root):
                    proof = verify.verify(self.project.root, self.project.base, self.run_dir,
                        framework=framework, base_suite=baseline, suite_command=suite, new_behavior=True,
                        test_root=root, timeout=120)
                    self.assertEqual(expected, proof["verdict"], self.reasons(proof))

    def test_wrong_framework_kind_cannot_use_unparsed_baseline_exit_codes(self):
        python = self.pytest_python()
        root = "components/gateway"
        suite = f"{shlex.quote(python)} -m unittest discover -v -s {root}"
        for layout in ("architecture", "README", "existing"):
            base = {"README.md": "A new component.\n"} if layout == "README" else ARCHITECTURE
            if layout == "existing":
                base = {**base, **component_files(), f"{root}/tests/test_gateway.py": SMOKE_TEST,
                    f"{root}/tests/test_pytest_only.py": "from components.gateway import server\n"
                        "def test_old_health_is_string():\n    assert isinstance(server.health(), str)\n"}
            self.project = Project(base)
            self.addCleanup(self.project.close)
            self.project.write(component_files() if layout != "existing" else {
                f"{root}/server.py": SERVER.format(status=200),
                f"{root}/tests/test_new.py": NEW_TEST.replace("'ok', server.health()", "200, server.health()")})
            supplied = verify.Framework("pytest", suite, python=python, test_root=root)
            canonical = verify.detect_framework(self.project.root, python=python, test_root=root, base=self.project.base)
            self.assertEqual(("unittest", suite), (canonical.name, canonical.suite))
            baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
                framework=supplied, suite_command=suite, timeout=120)
            self.assertIsNone(baseline["receipt"]["results"])
            self.assertFalse(baseline["receipt"]["results_expected"])
            self.assertEqual("passing" if layout == "existing" else "failing", baseline["health"])
            self.assertEqual(0 if layout == "existing" else 1, baseline["receipt"]["exit_code"])
            with self.subTest(layout=layout, interface="verify"):
                proof = verify.verify(self.project.root, self.project.base, self.run_dir,
                    framework=supplied, base_suite=baseline, new_behavior=True, test_root=root, timeout=120)
                receipt = proof["checks"]["suite_on_candidate"]
                self.assertEqual(0, receipt["exit_code"])
                self.assertIsNone(receipt["results"])
                self.assertFalse(receipt["results_expected"])
                self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
                self.assertTrue(any("scoped suite" in reason for reason in proof["unverified"]), proof)
                self.assertFalse(any("nothing under" in note for note in proof["notes"]), proof)
            with self.subTest(layout=layout, interface="prove"):
                detect = verify.detect_framework
                initial = True
                def selected_framework(*args, **kwargs):
                    nonlocal initial
                    if initial:
                        initial = False
                        return supplied
                    return detect(*args, **kwargs)
                # Only initial selection is injected; detection and proof executions stay real.
                with patch.object(verify, "detect_framework", side_effect=selected_framework):
                    proof = self.prove(python=python)
                self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
                self.assertTrue(any("scoped suite" in reason for reason in proof["unverified"]), proof)

    def test_existing_scoped_explicit_suites_keep_real_preservation_evidence(self):
        collector = f"{shlex.quote(sys.executable)} -m unittest discover -v -s components/gateway -p test_gateway.py"
        # An opaque wrapper must still execute its base definition over candidate product code.
        for command in (collector, "sh components/gateway/check.sh"):
            with self.subTest(command=command):
                self.project = Project({**ARCHITECTURE, **component_files(),
                    "components/gateway/tests/test_gateway.py": SMOKE_TEST,
                    "components/gateway/check.sh": collector + " > /dev/null 2>&1\n"})
                self.addCleanup(self.project.close)
                self.project.write({"components/gateway/server.py": SERVER.format(status="fixed"),
                    "components/gateway/tests/test_gateway.py": NEW_TEST.replace("'ok', server.health()", "'fixed', server.health()")})
                run = state(self.project)
                run["settings"]["regression"]["test_command"] = command
                proof = regression.prove(run, self.project.root, self.run_dir)
                self.assertEqual(verify.PASS, proof["verdict"], self.reasons(proof))

    def test_wrong_kind_cannot_borrow_typed_baseline_then_hide_candidate_results(self):
        commit(self.project, {**component_files(), "components/gateway/tests/test_gateway.py": SMOKE_TEST})
        good = verify.detect_framework(self.project.root, python=sys.executable,
            test_root="components/gateway", base=self.project.base)
        baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
            framework=good, suite_command=good.suite, timeout=120)
        self.assertTrue(baseline["receipt"]["results"]["passed"])
        self.project.write({"components/gateway/server.py": SERVER.format(status=200),
            "components/gateway/tests/test_new.py": NEW_TEST.replace("'ok', server.health()", "200, server.health()")})
        wrong = verify.Framework("pytest", good.suite, python=good.python, test_root=True)
        proof = verify.verify(self.project.root, self.project.base, self.run_dir,
            framework=wrong, base_suite=baseline, new_behavior=True, test_root="components/gateway", timeout=120)
        self.assertIsNone(proof["checks"]["suite_on_candidate"]["results"])
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)

    def test_canonical_first_suite_does_not_depend_on_scope_descriptor_type(self):
        self.project.write(component_files())
        framework = verify.detect_framework(self.project.root, python=sys.executable,
            test_root="components/gateway", base=self.project.base)
        framework.test_root = True
        baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
            framework=framework, suite_command=framework.suite, timeout=120)
        proof = verify.verify(self.project.root, self.project.base, self.run_dir,
            framework=framework, base_suite=baseline, new_behavior=True, test_root="components/gateway", timeout=120)
        self.assertEqual(verify.PASS, proof["verdict"], self.reasons(proof))

    def test_scoped_preservation_requires_same_base_completed_passing_evidence(self):
        commit(self.project, component_files())
        framework = verify.detect_framework(self.project.root, python=sys.executable,
            test_root="components/gateway", base=self.project.base)
        baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
            framework=framework, suite_command=framework.suite, timeout=120)
        for condition in ("normal", "wrong base", "skipped", "zero", "incomplete", "timeout", "interrupted", "regression"):
            with self.subTest(condition=condition):
                supplied = deepcopy(baseline)
                candidate = deepcopy(baseline["receipt"])
                receipt = supplied["receipt"]
                results = receipt["results"]
                if condition == "wrong base":
                    supplied["base"] = "0" * 40
                elif condition == "skipped":
                    results["skipped"], results["passed"] = results["passed"], []
                elif condition == "zero":
                    results.update(passed=[], total=0)
                elif condition == "incomplete":
                    results["complete"] = False
                elif condition == "timeout":
                    receipt["timed_out"] = True
                elif condition == "interrupted":
                    receipt["supervision_errors"] = ["fault-injected interruption"]
                elif condition == "regression":
                    results["complete"] = False
                    candidate["results"]["failed"], candidate["results"]["passed"] = candidate["results"]["passed"], []
                    candidate["exit_code"] = 1
                with patch.object(verify, "run_suite", return_value=candidate):
                    proof = verify.verify(self.project.root, self.project.base, self.run_dir,
                        framework=framework, base_suite=supplied, preserve_only=True, test_root="components/gateway")
                expected = verify.PASS if condition == "normal" else verify.FAIL if condition == "regression" else verify.UNVERIFIED
                self.assertEqual(expected, proof["verdict"], proof)

    def test_canonical_first_root_requires_complete_executed_candidate_tests(self):
        self.project.write(component_files())
        framework = verify.detect_framework(self.project.root, python=sys.executable,
            test_root="components/gateway", base=self.project.base)
        baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
            framework=framework, suite_command=framework.suite, timeout=120)
        executed = verify.verify(self.project.root, self.project.base, self.run_dir,
            framework=framework, base_suite=baseline, new_behavior=True, test_root="components/gateway")
        self.assertEqual(verify.PASS, executed["verdict"], self.reasons(executed))
        for condition in ("skipped", "zero", "incomplete", "timeout", "interrupted"):
            with self.subTest(condition=condition):
                candidate = deepcopy(executed["checks"]["suite_on_candidate"])
                results = candidate["results"]
                if condition == "skipped":
                    results["skipped"], results["passed"] = results["passed"], []
                elif condition == "zero":
                    results.update(passed=[], total=0)
                elif condition == "incomplete":
                    results["complete"] = False
                elif condition == "timeout":
                    candidate["timed_out"] = True
                else:
                    candidate["supervision_errors"] = ["fault-injected interruption"]
                def run_suite(selected, command, tree, directory, stem, **kwargs):
                    if command == framework.suite:
                        return candidate
                    return executed["checks"][stem.replace("-", "_")]
                with patch.object(verify, "run_suite", side_effect=run_suite):
                    proof = verify.verify(self.project.root, self.project.base, self.run_dir,
                        framework=framework, base_suite=baseline, new_behavior=True, test_root="components/gateway")
                self.assertNotEqual(verify.PASS, proof["verdict"], proof)
                self.assertFalse(any("nothing under" in note for note in proof["notes"]), proof)

    def test_candidate_only_detection_cannot_override_pinned_ancestor_policy(self):
        commit(self.project, {"pytest.ini": "[pytest]\n"})
        (self.project.root / "pytest.ini").unlink()
        git(self.project.root, "add", "-u")
        self.project.write(component_files())
        supplied = verify.detect_framework(self.project.root, python=sys.executable, test_root="components/gateway")
        self.assertEqual("unittest", supplied.name)
        baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
            framework=supplied, suite_command=supplied.suite, timeout=120)
        proof = verify.verify(self.project.root, self.project.base, self.run_dir,
            framework=supplied, base_suite=baseline, new_behavior=True, test_root="components/gateway", timeout=120)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
        self.assertFalse(any("nothing under components/gateway/" in note for note in proof["notes"]), proof)

    def test_public_scope_marker_cannot_override_unsafe_pinned_policy(self):
        (self.project.root / "pytest.ini").symlink_to("pytest.ini")
        commit(self.project, {})
        (self.project.root / "pytest.ini").unlink()
        git(self.project.root, "add", "-u")
        self.project.write(component_files())
        supplied = verify.detect_framework(self.project.root, python=sys.executable, test_root="components/gateway")
        self.assertIsNone(verify.detect_framework(self.project.root, python=sys.executable,
            test_root="components/gateway", base=self.project.base))
        baseline = verify.baseline(self.project.root, self.project.base, self.run_dir,
            framework=supplied, suite_command=supplied.suite, timeout=120)
        proof = verify.verify(self.project.root, self.project.base, self.run_dir,
            framework=supplied, base_suite=baseline, new_behavior=True, test_root="components/gateway", timeout=120)
        self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)
        self.assertFalse(any("nothing under components/gateway/" in note for note in proof["notes"]), proof)

    def test_public_scope_marker_cannot_override_unknown_current_policy(self):
        for kind in ("loop", "oversized"):
            with self.subTest(kind=kind):
                project = Project(ARCHITECTURE)
                self.addCleanup(project.close)
                project.write(component_files())
                policy = project.root / "components/gateway/pyproject.toml"
                if kind == "loop":
                    policy.symlink_to("pyproject.toml")
                else:
                    policy.write_bytes(b"#" * (test_root.POLICY_MAX_BYTES + 1))
                self.assertIsNone(verify.detect_framework(project.root, python=sys.executable,
                    test_root="components/gateway", base=project.base))
                suite = f"{shlex.quote(sys.executable)} -m unittest discover -v -s components/gateway"
                framework = verify.Framework("unittest", suite, python=sys.executable, test_root="components/gateway")
                baseline = verify.baseline(project.root, project.base, self.run_dir,
                    framework=framework, suite_command=suite, timeout=120)
                proof = verify.verify(project.root, project.base, self.run_dir,
                    framework=framework, base_suite=baseline, new_behavior=True,
                    test_root="components/gateway", timeout=120)
                self.assertEqual(verify.UNVERIFIED, proof["verdict"], self.reasons(proof))
                self.assertFalse(any("nothing under components/gateway/" in note for note in proof["notes"]), proof)

    def test_native_pytest_policy_names_preserve_old_function_tests(self):
        python = self.pytest_python()
        for name in ("pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml"):
            for location in ("current", "ancestor", "pinned"):
                with self.subTest(name=name, location=location):
                    self.project = Project({**ARCHITECTURE, **component_files(),
                        "components/gateway/tests/test_gateway.py": SMOKE_TEST,
                        "components/gateway/tests/test_pytest_only.py": "from components.gateway import server\n"
                            "def test_old_health_is_string():\n    assert isinstance(server.health(), str)\n"})
                    self.addCleanup(self.project.close)
                    policy = name if location == "ancestor" else f"components/gateway/{name}"
                    self.project.write({policy: "[pytest]\n"})
                    if location != "current":
                        commit(self.project, {})
                    if location == "pinned":
                        (self.project.root / policy).unlink()
                        git(self.project.root, "add", "-u")
                    self.project.write({"components/gateway/server.py": SERVER.format(status=200),
                        "components/gateway/tests/test_new.py": NEW_TEST.replace("'ok', server.health()", "200, server.health()")})
                    proof = self.prove(python=python)
                    self.assertEqual(verify.FAIL, proof["verdict"], proof)
                    self.assertEqual("detected:pytest", proof["commands"]["suite_source"])
                    self.assertIn("test_old_health_is_string", self.reasons(proof))

    def test_native_pytest_pinned_links_validate_policy_not_host_or_candidate_bytes(self):
        python = self.pytest_python()
        for name in (".pytest.ini", "pytest.toml", ".pytest.toml"):
            for unsafe in (False, True):
                with self.subTest(name=name, unsafe=unsafe):
                    self.project = Project({**ARCHITECTURE, **component_files(),
                        "components/gateway/tests/test_gateway.py": SMOKE_TEST,
                        "components/gateway/tests/test_pytest_only.py": "from components.gateway import server\n"
                            "def test_old_health_is_string():\n    assert isinstance(server.health(), str)\n",
                        "components/gateway/policy.txt": "[pytest]\n"})
                    self.addCleanup(self.project.close)
                    link = self.project.root / "components/gateway" / name
                    link.symlink_to("missing.txt" if unsafe else "policy.txt")
                    commit(self.project, {})
                    link.unlink()
                    git(self.project.root, "add", "-u")
                    self.project.write({"components/gateway/server.py": SERVER.format(status=200),
                        "components/gateway/tests/test_new.py": NEW_TEST.replace("'ok', server.health()", "200, server.health()")})
                    proof = self.prove(python=python)
                    self.assertEqual(verify.UNVERIFIED if unsafe else verify.FAIL, proof["verdict"], proof)
                    if unsafe:
                        self.assertIsNone(verify.detect_framework(self.project.root, python=python,
                            test_root="components/gateway", base=self.project.base))
                    else:
                        self.assertIn("test_old_health_is_string", self.reasons(proof))
                        # Restore the policy as well: keep pytest rootdir and test identities unchanged.
                        link.symlink_to("policy.txt")
                        git(self.project.root, "add", link.relative_to(self.project.root).as_posix())
                        self.project.write({"components/gateway/server.py": SERVER.format(status="fixed"),
                            "components/gateway/tests/test_new.py": NEW_TEST.replace("'ok', server.health()", "'fixed', server.health()")})
                        control = self.prove(python=python)
                        self.assertEqual(verify.PASS, control["verdict"], self.reasons(control))

    def test_current_component_and_ancestor_pytest_policy(self):
        python = self.pytest_python()
        for policy in ("components/gateway/conftest.py", "conftest.py", "pytest.ini", "pyproject.toml"):
            with self.subTest(policy=policy):
                self.project = Project(ARCHITECTURE)
                self.addCleanup(self.project.close)
                content = "[tool.pytest.ini_options]\n" if policy.endswith("toml") else "[pytest]\n" if policy.endswith("ini") else ""
                if policy.startswith("components/"):
                    self.project.write({**component_files(), policy: content})
                else:
                    commit(self.project, {policy: content})
                    self.project.write(component_files())
                proof = self.prove(python=python)
                self.assertEqual(verify.PASS, proof["verdict"], self.reasons(proof))
                self.assertEqual("detected:pytest", proof["commands"]["suite_source"])
                self.assertTrue(proof["commands"]["suite"].endswith("components/gateway"))

    def test_pinned_and_dirty_launch_policy_cannot_switch_to_unittest(self):
        python = self.pytest_python()
        for location in ("components/gateway/pyproject.toml", "pyproject.toml", "dirty-launch"):
            with self.subTest(location=location):
                self.project = Project(ARCHITECTURE)
                self.addCleanup(self.project.close)
                policy = "components/gateway/pyproject.toml" if location == "dirty-launch" else location
                commit(self.project, {**component_files(), "components/gateway/tests/test_gateway.py": SMOKE_TEST,
                    "components/gateway/tests/test_pytest_only.py": "from components.gateway import server\n"
                        "def test_old_health_is_string():\n    assert isinstance(server.health(), str)\n"})
                self.project.write({policy: "[tool.pytest.ini_options]\n"})
                if location == "dirty-launch":
                    self.project.base = verify.commit_worktree(self.project.root)
                else:
                    commit(self.project, {})
                (self.project.root / policy).unlink()
                self.project.write({"components/gateway/server.py": SERVER.format(status=200),
                    "components/gateway/tests/test_new.py": NEW_TEST.replace("'ok', server.health()", "200, server.health()")})
                proof = self.prove(python=python)
                self.assertEqual(verify.FAIL, proof["verdict"], proof)
                self.assertEqual("detected:pytest", proof["commands"]["suite_source"])
                self.assertIn("test_old_health_is_string", self.reasons(proof))

    def test_root_conftest_imports_without_collecting_unrelated_root_tests(self):
        python = self.pytest_python()
        commit(self.project, {**component_files(), "conftest.py": "from components.gateway import server\nimport pytest\n"
            "@pytest.fixture\ndef existing_health():\n    return server.health()\n",
            "test_unrelated.py": "def test_unrelated():\n    assert False\n",
            "components/gateway/tests/test_pytest_only.py": "def test_old_health_is_string(existing_health):\n"
                "    assert isinstance(existing_health, str)\n"})
        self.project.write({"components/gateway/server.py": SERVER.format(status="fixed"),
            "components/gateway/tests/test_gateway.py": NEW_TEST.replace("'ok', server.health()", "'fixed', server.health()")})
        proof = self.prove(python=python)
        self.assertEqual(verify.PASS, proof["verdict"], self.reasons(proof))
        results = util.read(proof["path"])["checks"]["suite_on_candidate"]["results"]
        self.assertTrue(any("test_old_health_is_string" in name for name in results["passed"]))
        self.assertFalse(any("test_unrelated" in name for name in results["passed"] + results["failed"]))

    def test_unavailable_pytest_never_substitutes_passing_unittest(self):
        python = isolated_python(self)
        self.assertFalse(verify._python_can_import(python, "pytest"))
        self.project.write({**component_files(), "components/gateway/conftest.py": ""})
        proof = self.prove(python=python)
        self.assertNotEqual(verify.PASS, proof["verdict"], proof)
        self.assertEqual("detected:pytest", proof["commands"]["suite_source"])
        self.assertIn("reported no test results", self.reasons(proof))

    def test_pinned_symlink_policy_is_resolved_from_git_blobs(self):
        python = self.pytest_python()
        for kind in ("local", "root", "chain", "dirty-launch"):
            with self.subTest(kind=kind):
                self.project = Project({**ARCHITECTURE, **component_files(),
                    "components/gateway/tests/test_gateway.py": SMOKE_TEST,
                    "components/gateway/policy.toml": "[project]\nname='gateway'\n",
                    "components/gateway/tests/test_pytest_only.py": "from components.gateway import server\n"
                        "def test_old_health_is_string():\n    assert isinstance(server.health(), str)\n"})
                self.addCleanup(self.project.close)
                self.project.write({"components/gateway/policy.toml": "[tool.pytest.ini_options]\n"})
                link = self.project.root / ("pyproject.toml" if kind == "root" else "components/gateway/pyproject.toml")
                link.symlink_to("components/gateway/policy.toml" if kind == "root" else "second.toml" if kind == "chain" else "policy.toml")
                if kind == "chain":
                    (link.parent / "second.toml").symlink_to("./policy.toml")
                if kind == "dirty-launch":
                    self.project.base = verify.commit_worktree(self.project.root)
                else:
                    commit(self.project, {})
                self.project.write({"components/gateway/policy.toml": "[project]\nname='gateway'\n",
                    "components/gateway/server.py": SERVER.format(status=200),
                    "components/gateway/tests/test_new.py": NEW_TEST.replace("'ok', server.health()", "200, server.health()")})
                proof = self.prove(python=python)
                self.assertEqual(verify.FAIL, proof["verdict"], proof)
                self.assertEqual("detected:pytest", proof["commands"]["suite_source"])
                self.assertIn("test_old_health_is_string", self.reasons(proof))
                self.project.write({"components/gateway/policy.toml": "[tool.pytest.ini_options]\n",
                    "components/gateway/server.py": SERVER.format(status="fixed"),
                    "components/gateway/tests/test_new.py": NEW_TEST.replace("'ok', server.health()", "'fixed', server.health()")})
                control = self.prove(python=python)
                self.assertEqual(verify.PASS, control["verdict"], self.reasons(control))

    def test_unsafe_pinned_links_and_empty_root_ancestor_policy_stay_unverified(self):
        for kind in ("loop", "root-loop", "ini-loop", "conftest-loop", "escape", "absolute", "untracked", "depth", "empty-root"):
            with self.subTest(kind=kind):
                self.project = Project(ARCHITECTURE if kind == "empty-root" else {**ARCHITECTURE, **component_files(),
                                                                                 "components/gateway/tests/test_gateway.py": SMOKE_TEST})
                self.addCleanup(self.project.close)
                name = {"ini-loop": "pytest.ini", "conftest-loop": "conftest.py"}.get(kind, "pyproject.toml")
                link = self.project.root / (name if kind in ("root-loop", "empty-root") else f"components/gateway/{name}")
                target = {"escape": "../../../outside.toml", "absolute": str(self.run_dir / "external.toml"),
                          "untracked": "untracked.toml", "depth": "link0.toml"}.get(kind, name)
                link.symlink_to(target)
                if kind == "depth":
                    for index in range(40):
                        (link.parent / f"link{index}.toml").symlink_to(f"link{index + 1}.toml" if index < 39 else "policy.toml")
                    self.project.write({"components/gateway/policy.toml": "[project]\nname='gateway'\n"})
                commit(self.project, {})
                if kind == "untracked":
                    self.project.write({"components/gateway/untracked.toml": "[project]\nname='gateway'\n"})
                self.project.write(component_files(status=200))
                self.assertIsNone(verify.detect_framework(self.project.root, python=sys.executable,
                    test_root="components/gateway", base=self.project.base))
                proof = self.prove()
                self.assertEqual(verify.UNVERIFIED, proof["verdict"], proof)


class RootTests(unittest.TestCase):
    def test_current_policy_size_and_unreadable_are_unknown_not_absent(self):
        project = Project(ARCHITECTURE)
        self.addCleanup(project.close)
        project.write(component_files())
        policy = project.root / "components/gateway/pyproject.toml"
        def detect():
            return verify.detect_framework(project.root, python=sys.executable,
                test_root="components/gateway", base=project.base)
        self.assertEqual("unittest", detect().name)
        policy.write_text("[project]\nname='gateway'\n")
        self.assertEqual("unittest", detect().name)
        policy.write_text("[tool.pytest.ini_options]\n")
        self.assertEqual("pytest", detect().name)
        policy.write_bytes(b"#" * (1024 * 1024 + 1))
        self.assertIsNone(detect())
        original_fstat = os.fstat
        def growing_file(descriptor):
            current = original_fstat(descriptor)
            return SimpleNamespace(st_mode=current.st_mode, st_size=0)
        with patch.object(os, "fstat", side_effect=growing_file):
            self.assertIsNone(detect())
        policy.write_bytes(b"#" * test_root.POLICY_MAX_BYTES)
        self.assertEqual("unittest", detect().name)
        policy.write_text("[tool.pytest.ini_options]\n")
        if os.geteuid() != 0:
            policy.chmod(0)
            try:
                self.assertIsNone(detect())
            finally:
                policy.chmod(0o644)
        original_open = os.open
        for error in (errno.EACCES, errno.ELOOP):
            def unreadable(path, flags, *args, **kwargs):
                if path == "pyproject.toml" and kwargs.get("dir_fd") is not None:
                    raise OSError(error, "unknown current policy")
                return original_open(path, flags, *args, **kwargs)
            with self.subTest(errno=error), patch.object(os, "open", side_effect=unreadable):
                self.assertIsNone(detect())

    def test_current_special_policy_is_rejected_without_opening_or_blocking(self):
        project = Project(ARCHITECTURE)
        self.addCleanup(project.close)
        project.write(component_files())
        policy = project.root / "components/gateway/pyproject.toml"
        os.mkfifo(policy)
        with patch.object(os, "fdopen", wraps=os.fdopen) as reader:
            self.assertIsNone(verify.detect_framework(project.root, python=sys.executable,
                test_root="components/gateway", base=project.base))
            reader.assert_not_called()
        policy.unlink()
        policy.mkdir()
        self.assertIsNone(verify.detect_framework(project.root, python=sys.executable,
            test_root="components/gateway", base=project.base))

    def test_native_current_and_pinned_policy_detection_stays_pytest(self):
        for name in ("pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml"):
            for location in ("current", "ancestor", "pinned"):
                with self.subTest(name=name, location=location):
                    project = Project(ARCHITECTURE)
                    self.addCleanup(project.close)
                    project.write(component_files())
                    policy = name if location == "ancestor" else f"components/gateway/{name}"
                    project.write({policy: ""})
                    if location == "pinned":
                        commit(project, {})
                        (project.root / policy).unlink()
                        git(project.root, "add", "-u")
                    self.assertEqual("pytest", verify.detect_framework(project.root, python=sys.executable,
                        test_root="components/gateway", base=project.base).name)

    def test_current_policy_links_fail_closed_without_following_host_files(self):
        for name in ("pyproject.toml", "setup.cfg", "tox.ini", "pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml", "conftest.py"):
            for kind in ("loop", "missing", "outside"):
                with self.subTest(name=name, kind=kind):
                    project = Project(ARCHITECTURE)
                    self.addCleanup(project.close)
                    project.write(component_files())
                    outside = Path(project.temp.name) / "outside.toml"
                    outside.write_text("[tool.pytest.ini_options]\n")
                    policy = project.root / "components/gateway" / name
                    policy.symlink_to({"loop": name, "missing": "missing-policy", "outside": str(outside)}[kind])
                    with patch.object(os, "fdopen", wraps=os.fdopen) as reader:
                        self.assertIsNone(verify.detect_framework(project.root, python=sys.executable,
                            test_root="components/gateway", base=project.base))
                        reader.assert_not_called()

    def test_current_ancestor_link_and_safe_relative_chain(self):
        project = Project(ARCHITECTURE)
        self.addCleanup(project.close)
        project.write(component_files())
        policy = project.root / "pyproject.toml"
        policy.symlink_to("pyproject.toml")
        self.assertIsNone(verify.detect_framework(project.root, python=sys.executable,
            test_root="components/gateway", base=project.base))
        policy.unlink()
        project.write({"components/gateway/policy.toml": "[tool.pytest.ini_options]\n"})
        policy.symlink_to("components/gateway/second.toml")
        (policy.parent / "components/gateway/second.toml").symlink_to("./policy.toml")
        self.assertEqual("pytest", verify.detect_framework(project.root, python=sys.executable,
            test_root="components/gateway", base=project.base).name)

    def test_current_reader_bounds_directory_links_and_replacement(self):
        project = Project(ARCHITECTURE)
        self.addCleanup(project.close)
        project.write({**component_files(), "components/gateway/policies/policy.toml": "[tool.pytest.ini_options]\n"})
        policy = project.root / "components/gateway/pyproject.toml"
        alias = policy.parent / "policy-dir"
        alias.symlink_to("policies", target_is_directory=True)
        policy.symlink_to("policy-dir/policy.toml")
        self.assertEqual("pytest", verify.detect_framework(project.root, python=sys.executable,
            test_root="components/gateway", base=project.base).name)
        policy.unlink()
        policy.symlink_to("link0.toml")
        for index in range(40):
            (policy.parent / f"link{index}.toml").symlink_to(
                f"link{index + 1}.toml" if index < 39 else "policies/policy.toml")
        self.assertIsNone(verify.detect_framework(project.root, python=sys.executable,
            test_root="components/gateway", base=project.base))
        policy.unlink()
        policy.write_text("[tool.pytest.ini_options]\n")
        outside = Path(project.temp.name) / "host-policy.toml"
        outside.write_text("[tool.pytest.ini_options]\n")
        original_open = os.open
        def replace_before_open(path, flags, *args, **kwargs):
            if path == "pyproject.toml" and kwargs.get("dir_fd") is not None:
                policy.unlink()
                policy.symlink_to(outside)
            return original_open(path, flags, *args, **kwargs)
        with patch.object(os, "open", side_effect=replace_before_open):
            self.assertIsNone(verify.detect_framework(project.root, python=sys.executable,
                test_root="components/gateway", base=project.base))

    def test_pytest_config_known_names_and_conditional_sections(self):
        cases = [(name, "", True) for name in ("pytest.ini", ".pytest.ini", "pytest.toml", ".pytest.toml")]
        cases += [("setup.cfg", "[metadata]\nname=gateway\n", False),
                  ("setup.cfg", "[tool:pytest]\n", True), ("tox.ini", "[tox]\n", False),
                  ("tox.ini", "[pytest]\n", True), ("pyproject.toml", "[project]\nname='gateway'\n", False),
                  ("pyproject.toml", "[tool.pytest.ini_options]\n", True),
                  ("other.toml", "[pytest]\n", False), ("other.ini", "[pytest]\n", False)]
        for name, content, expected in cases:
            with self.subTest(name=name, content=content):
                self.assertEqual(expected, verify._pytest_configured(Path("."), [name],
                    read=lambda path: content if path.name == name else ""))

    def test_normalize_and_literal_boundary(self):
        self.assertEqual("components/gateway", test_root.normalize("components/gateway/"))
        for value in ("", "/abs/dir", "../x", "components/../x", "components//x", "./components", "-s", "components/*", ":(glob)x", "a\\b", "components/\n"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                test_root.normalize(value)
        self.assertEqual(["components/gateway", "components/gatewayx/a.py", "top.py"], test_root.outside(
            "components/gateway", ["components/gateway/a.py", "top.py", "components/gatewayx/a.py", "components/gateway"]))

    def test_empty_base_is_pinned_and_git_errors_are_not_empty(self):
        project = Project({**ARCHITECTURE, "components/gateway/server.py": SERVER.format(status="ok")})
        self.addCleanup(project.close)
        self.assertFalse(test_root.empty_on_base(project.root, project.base, "components/gateway"))
        self.assertTrue(test_root.empty_on_base(project.root, project.base, "components/store"))
        project.write({"components/store/server.py": "VALUE = 1\n"})
        self.assertTrue(test_root.empty_on_base(project.root, project.base, "components/store"))
        self.assertFalse(test_root.empty_on_base(project.root, "0" * 40, "components/store"))

    def test_first_suite_requires_no_old_tests_and_all_new_tests_passing(self):
        results = {"passed": ["tests.test_a.A.test_a"], "failed": [], "skipped": [], "collection_errors": [],
                   "uncollected": [], "total": 1, "complete": True}
        candidate = {"exit_code": 0, "timed_out": False, "results": results}
        absent = {"exit_code": 1, "timed_out": False, "results": None}
        self.assertTrue(test_root.first_suite(absent, candidate))
        self.assertFalse(test_root.first_suite({"timed_out": False, "results": results}, candidate))
        self.assertFalse(test_root.first_suite({**absent, "timed_out": True}, candidate))
        for change in ({"failed": ["bad"]}, {"skipped": ["bad"]}, {"passed": []}, {"complete": False}, {"collection_errors": ["bad"]}):
            with self.subTest(change=change):
                self.assertFalse(test_root.first_suite(absent, {**candidate, "results": {**results, **change}}))

    def test_new_run_cli_accepts_literal_component_root_and_rejects_escape(self):
        project = Project(ARCHITECTURE)
        self.addCleanup(project.close)
        for root, code in (("components/gateway/", 0), ("../elsewhere", 2)):
            proc = subprocess.run([sys.executable, str(Path(test_root.__file__).with_name("autocode.py")),
                "Build a thing", "--in-place", "--no-chat", "--dry-run", "--engine", "codex", "--workspace", str(project.root),
                "--test-root", root], cwd=project.root, capture_output=True, text=True, timeout=120)
            self.assertEqual(code, proc.returncode, proc.stdout + proc.stderr)
