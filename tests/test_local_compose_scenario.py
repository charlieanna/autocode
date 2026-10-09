"""Public Compose pipeline, independent HTTP controls and bounded owned cleanup."""
import argparse
import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import psutil
from harness import catalog, component_services, processes, verdict
from harness.project import materialize, overlay_paths

from scenarios import run


class SampleRun:
    def run_sample(self, directory, *, real=False, solution="reference"):
        args = argparse.Namespace(fake=True, fake_solution=solution, profile=None, provider=None, hybrid=False,
                                  out=directory, autocode=None, max_steps=None, timeout_minutes=None,
                                  local_docker=real, i_authorize_live_model_spend=False)
        return run.run_one(catalog.load("local-compose-two-services"), args)


def alive(pid):
    try:
        child = psutil.Process(pid)
        return child.is_running() and child.status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


class LocalComposeScenarioTests(SampleRun, unittest.TestCase):
    def test_oracle_command_stops_same_group_and_detached_helpers_and_preserves_sentinel(self):
        for detached in (False, True):
            with self.subTest(detached=detached), tempfile.TemporaryDirectory() as folder:
                pidfile = Path(folder) / "child.pid"
                script = ("import subprocess,sys,time; from pathlib import Path; "
                          "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],"
                          f"start_new_session={detached}); Path({str(pidfile)!r}).write_text(str(p.pid)); "
                          + ("time.sleep(60)" if detached else "print('retained output',flush=True); sys.exit(7)"))
                sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], start_new_session=True)
                try:
                    if detached:
                        with self.assertRaises(subprocess.TimeoutExpired):
                            component_services.run_command([sys.executable, "-c", script], 2)
                    else:
                        result = component_services.run_command([sys.executable, "-c", script], 5)
                        self.assertEqual(7, result.returncode)
                        self.assertIn("retained output", result.stdout)
                    self.assertTrue(pidfile.is_file())
                    self.assertFalse(alive(int(pidfile.read_text())))
                    self.assertIsNone(sentinel.poll())
                finally:
                    sentinel.kill()
                    sentinel.wait(timeout=5)
                    if pidfile.exists() and alive(int(pidfile.read_text())):
                        child = psutil.Process(int(pidfile.read_text()))
                        child.kill()
                        child.wait(timeout=5)

    def test_oracle_cli_timeout_stops_detached_child_before_down(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pidfile = root / "child.pid"
            script = ("import subprocess,sys,time; from pathlib import Path; "
                      "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
                      f"Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(60)")
            actual = component_services.run_command
            stopped, receipt = [], {}
            def command(argv, timeout):
                if "up" in argv:
                    return actual([sys.executable, "-c", script], 2)
                if "down" in argv:
                    stopped.append(not alive(int(pidfile.read_text())))
                return actual([sys.executable, "-c", "pass"], 2)
            with mock.patch.object(component_services.shutil, "which", return_value="/fake/docker"), \
                    mock.patch.dict(os.environ, {"DOCKER_HOST": "unix:///fake-local.sock", "DOCKER_CONTEXT": ""}), \
                    mock.patch.object(component_services, "run_command", command):
                with self.assertRaises(subprocess.TimeoutExpired):
                    with component_services.running_compose(root / "compose.json", receipt):
                        self.fail("timed-out up must not yield")
            self.assertEqual([True], stopped)
            self.assertTrue(receipt["torn_down"])

    def test_real_cli_timeout_is_durable_ungraded_and_never_calls_oracle(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pidfile = root / "child.pid"
            script = ("import subprocess,sys,time; from pathlib import Path; "
                      "p=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'],start_new_session=True); "
                      f"Path({str(pidfile)!r}).write_text(str(p.pid)); time.sleep(60)")
            real_call = processes.run_cli
            def bounded_call(command, **kwargs):
                kwargs.update(timeout=2, lifeline={**kwargs["lifeline"], "deadline": time.monotonic() + 2})
                return real_call(command, **kwargs)
            def architecture(driver, brief):
                reference = catalog.load("architecture-two-services").reference
                for name in overlay_paths(reference):
                    target = driver.project / name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes((reference / name).read_bytes())
            args = argparse.Namespace(fake=True, fake_solution="reference", profile=None, provider=None, hybrid=False,
                                      out=root / "out", autocode=[sys.executable, "-c", script], max_steps=None,
                                      timeout_minutes=10, local_docker=False, i_authorize_live_model_spend=False)
            with mock.patch.object(run.components_driver.Driver, "drive", architecture), \
                    mock.patch.object(run.components_driver.Driver, "view", return_value={"complete": True}), \
                    mock.patch.object(run.components_driver, "run_cli", bounded_call), \
                    mock.patch.object(run.verdict, "evaluate", side_effect=AssertionError("no grading")):
                result = run.run_one(catalog.load("local-compose-two-services"), args)
            self.assertEqual(verdict.INTERRUPTED_UNGRADED, result["verdict"])
            self.assertIsNone(result["oracle_passed"])
            self.assertEqual("unknown", result["usage_status"])
            evidence = Path(result["evidence"])
            self.assertEqual(verdict.INTERRUPTED_UNGRADED, json.loads((evidence / "result.json").read_text())["verdict"])
            step = json.loads((evidence / "components-steps.json").read_text())[-1]
            self.assertEqual("CallTimeout", step["interruption"])
            self.assertEqual([], step["cleanup_errors"])
            self.assertTrue(pidfile.is_file())
            self.assertFalse(alive(int(pidfile.read_text())))

    def test_bodyless_redirect_and_204_health_pass_but_endpoint_json_is_required(self):
        scenario = catalog.load("local-compose-two-services")
        architecture = catalog.load(scenario.components_architecture).reference
        for missing in (None, "create", "events", "analytics"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as folder:
                project = materialize(scenario.seed, Path(folder) / "project", architecture, scenario.reference)
                for cid in ("link-service", "analytics-service"):
                    path = project / "components" / cid / "server.py"
                    condition = "status in (302, 404) or self.path == '/health'"
                    if missing == "create" and cid == "link-service":
                        condition += " or status == 201"
                    if missing == "events" and cid == "link-service":
                        condition += " or self.path == '/events'"
                    if missing == "analytics" and cid == "analytics-service":
                        condition += " or self.path.startswith('/stats')"
                    source = path.read_text().replace("body = json.dumps(document).encode()",
                                                      f"body = b'' if {condition} else json.dumps(document).encode()")
                    path.write_text(source.replace("self.send_response(status)",
                                                   "self.send_response(204 if self.path == '/health' else status)"))
                result = verdict.evaluate(scenario, project)
                self.assertEqual(missing is None, result.passed, result.summary)
                self.assertFalse(result.error, result.error)

    def test_public_component_metrics_read_no_private_state(self):
        attempts = [{"stage": "terra", "model": "builder", "engine": "codex", "duration_seconds": 2,
                     "tokens": {"input_tokens": 10, "output_tokens": 3}}]
        summary = {"components": {"api": {"view": {"usage": {"accounting": {"attempts": attempts}}}}}}
        with mock.patch.object(Path, "read_text", side_effect=AssertionError("no private files")):
            metric = run.components_driver.component_metrics(summary)
        self.assertEqual(["terra"], metric["model_stage_names"])
        self.assertEqual(10, metric["tokens"]["input"])

    def test_independent_oracle_controls(self):
        rows = run.self_test(catalog.load("local-compose-two-services"))
        self.assertEqual({"seed", "reference", "broken/wrong-wiring", "broken/wrong-redirect"}, {name for name, _, _ in rows})
        self.assertTrue(all(ok for _, ok, _ in rows), rows)

    def test_native_signal_exit_is_ungraded_before_json_or_oracle(self):
        for exit_code in (-15, 129, 130, 143, 7):
            with self.subTest(exit=exit_code), tempfile.TemporaryDirectory() as folder:
                root = Path(folder)
                def architecture(driver, brief):
                    reference = catalog.load("architecture-two-services").reference
                    for name in overlay_paths(reference):
                        target = driver.project / name
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes((reference / name).read_bytes())
                with mock.patch.object(run.components_driver.Driver, "drive", architecture), \
                        mock.patch.object(run.components_driver.Driver, "view", return_value={"complete": True}), \
                        mock.patch.object(run.components_driver, "run_cli", return_value=subprocess.CompletedProcess([], exit_code, "", "")), \
                        mock.patch.object(run.verdict, "evaluate", side_effect=AssertionError("no grading")):
                    result = self.run_sample(root)
                self.assertEqual(verdict.ERROR if exit_code == 7 else verdict.INTERRUPTED_UNGRADED, result["verdict"])
                if exit_code != 7:
                    self.assertIsNone(result["oracle_passed"])

    def test_full_fake_pipeline_executes_actual_generated_services(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_sample(Path(folder))
            self.assertEqual(verdict.PASS, result["verdict"], (result.get("summary"), result["evidence"]))
            local = result["components"]["local_run"]
            self.assertEqual(["link-service", "analytics-service"], local["ready"])
            self.assertEqual(4, len(local["steps"]))
            self.assertTrue(local["torn_down"])
            evidence = Path(result["evidence"])
            commands = [json.loads(line) for line in (evidence / "docker.jsonl").read_text().splitlines()]
            self.assertTrue(any(command[-5:] == ["down", "-v", "--remove-orphans", "--rmi", "local"] for command in commands))
            prompts = list((evidence / "component-prompts").glob("*-terra-*.json"))
            self.assertEqual(2, len(prompts))
            self.assertTrue(any("LINK_SERVICE_URL" in path.read_text() for path in prompts))

    def test_wrong_redirect_fails_independent_oracle_even_when_product_smoke_passes(self):
        with tempfile.TemporaryDirectory() as folder:
            result = self.run_sample(Path(folder), solution="broken/wrong-redirect")
            self.assertEqual(verdict.FALSE_COMPLETE, result["verdict"], (result.get("summary"), result["evidence"]))
            self.assertEqual("passed", result["components"]["local_run"]["status"])
            self.assertTrue(any(not row["ok"] and row["name"].startswith("real_redirect") for row in result["checks"]))


@unittest.skipUnless(os.environ.get("AUTOCODE_TEST_REAL_DOCKER") == "1", "real Docker requires explicit opt-in")
class RealDockerTests(SampleRun, unittest.TestCase):
    def test_final_runtime_reference_and_independent_engine_cleanup(self):
        from dataclasses import replace

        import autocode_local_run as local
        import autocode_multicomponent as components
        root = Path(run.REPO) / ".scenario-runs"
        root.mkdir(exist_ok=True)
        work = Path(tempfile.mkdtemp(prefix="local-compose-engine-", dir=root))
        scenario = catalog.load("local-compose-two-services")
        project = materialize(scenario.seed, work / "project", catalog.load(scenario.components_architecture).reference,
                              scenario.reference)
        architecture = components.Architecture.load(project / "architecture")
        plan = replace(local.prepare(architecture.directory, {cid: row.runtime for cid, row in architecture.components.items()}),
                       endpoint=local.check_docker())
        summary = local.LocalRun(plan, project, work / "product-run", health_timeout=30).run()
        record = {"local_docker": True, "components": {"local_run": summary}}
        oracle = verdict.evaluate(scenario, project, record)
        receipt = {"local_run": summary, "oracle_passed": oracle.passed,
                   "checks": [vars(row) for row in oracle.checks], "oracle_error": oracle.error}
        (work / "result.json").write_text(json.dumps(receipt, indent=2))
        print(json.dumps({"evidence": str(work), "product_project": summary["project"],
                          "oracle_passed": oracle.passed, "checks": receipt["checks"]}, indent=2))
        self.assertEqual("passed", summary["status"], summary)
        self.assertTrue(summary["torn_down"])
        self.assertTrue(oracle.passed, oracle.summary)
        for args in (("ps", "-aq"), ("network", "ls", "-q"), ("image", "ls", "-q")):
            proc = component_services.run_command(["docker", "--host", plan.endpoint, *args, "--filter",
                                                  f"label=com.docker.compose.project={summary['project']}"], 20)
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual("", proc.stdout.strip())

    def test_real_local_pipeline_and_independent_oracle_cleanup(self):
        root = Path(run.REPO) / ".scenario-runs"
        root.mkdir(exist_ok=True)
        result = self.run_sample(root, real=True)
        print(json.dumps({"evidence": result["evidence"], "verdict": result["verdict"],
                          "wall_seconds": result.get("wall_seconds"), "local": result.get("components", {}).get("local_run")}, indent=2))
        self.assertEqual(verdict.PASS, result["verdict"], (result.get("summary"), result["evidence"]))
        local = result["components"]["local_run"]
        for args in (("ps", "-aq"), ("network", "ls", "-q"), ("image", "ls", "-q")):
            proc = component_services.run_command(["docker", *args, "--filter",
                                                  f"label=com.docker.compose.project={local['project']}"], 20)
            self.assertEqual(0, proc.returncode, proc.stderr)
            self.assertEqual("", proc.stdout.strip())
        self.assertTrue(any(row["name"] == "independent_real_engine_cleanup" and row["ok"] for row in result["checks"]))
